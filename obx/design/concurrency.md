# Concurrency for obx

Status: research note, 2026-11-02; first decisions 2026-11-03 (section 0).
Stage C1a is done (2026-11-03): `lib/task.obx`, the runtime in `rt.h` and
`native/rt.c`, SPEC 16; the measured costs replace the estimates in
section 5. Stage C1b is done (2026-11-03): the epoll loop, timers and
deadlines, `lib/net.obx`, and the HTTP server gate without Postgres
(`cmd/httpd.obx`); section 5 records how it was built. C1c (the helper pool,
libpq's non-blocking path) is next.

## 0. Decisions

2026-11-03:

- **Futures, not channels.** Inside a process, tasks are started from a scope
  and return a `Future[T]`; a `Promise[T]` is a future completed by hand; state
  that several tasks need is held in a `Shared[T]`. These three are the
  built-ins. Channels are dropped for now. If a streaming program needs one, a
  channel is library code on `Shared` and `Promise` (section 5).
- **Parking inside `Shared.with` traps now** (exit 13) **and becomes a compile
  error later**, when the compiler has effect summaries (SPEC 13). Not a lock:
  a lock held across a park brings back deadlocks and latency.
- **No threads: obx is shared-nothing, for safety and for performance.** A
  process runs one OS thread with coroutines; several cores means several
  processes that share nothing but the database. This is a performance
  decision as much as a safety one: the fastest servers share nothing
  between cores (nginx's process per core, Redis's single-threaded core,
  Seastar's thread per core with no sharing, LMAX's single-threaded matching
  engine at about 6 million orders a second), because locks and cache lines
  moving between cores cost more than they gain. One core with an epoll loop
  serves roughly 100,000–500,000 simple HTTP requests a second, and a real
  application meets Postgres's limits first. Threads would tax every
  program (atomic `str` counts at 5–20 ns instead of about 1 ns, allocator
  and runtime-state changes, `fork` made unsafe), add lock-order deadlocks
  and nondeterministic failures, and need a cross-thread constraint and
  thread-tied C markers in the language. What is given up: an in-process
  cache that every core reads and writes; for financial data that state
  belongs in the database. Alternatives for the rest are in section 5,
  "Without threads". Stages C3 and C4 are not planned. Revisit only when a
  real program shows a measured bottleneck those alternatives cannot fix.
  The one exception is the helper pool for blocking C calls (C1c): threads
  that run only C code, never obx code, invisible to the language.

- **Q1, Q3 to Q8 as recommended** (section 8): processes are the only way to
  use several cores for now; every task is started from a scope; scheduling
  is cooperative with `task.yield()`, yield checks later as an IR pass if
  needed; a panic stops the worker; no threads (above); blocking C
  calls are marked `blocking` on the extern; C1a starts after the `ir` and
  `perf2` branches merge and the docs pass.

Related: obo's [systems-threads-design.md](../../archive/obo/design/systems-threads-design.md)
reached a similar verdict for obo 0.12: one reactor per process first, then
supervised processes, and threads only behind strict gates. This note keeps
that verdict and changes one thing. Handlers are written in blocking style on
stackful coroutines, not as explicit state machines.

## 1. Recommendation

Build concurrency in two layers, and keep it shared-nothing. Each layer is
useful without the next one.

1. **Inside a process: one OS thread, many stackful coroutines.** An epoll
   event loop runs underneath, in the library and runtime. Handler code is
   written in plain blocking style (`let n = try sock.read(mut &buf)`). A
   socket call that would block parks the coroutine and the loop runs another
   one. There is no `async`/`await` and no function colouring. A task starts
   child tasks and waits for one or all of their results (futures); a result
   is moved to the waiter, not copied. State that several tasks need lives in
   a `Shared[T]`, reached only in `with` blocks that cannot park.
2. **Across cores: shared-nothing worker processes,** the nginx model. A
   supervisor forks N workers and each worker runs layer 1. The language
   needs nothing for this, and a crash stays inside one worker.
3. **No threads** (decided 2026-11-03, section 0). Worker threads and
   fork-join threads (stages C3 and C4) are not planned. Batch work across
   cores uses processes (section 9 records process futures as an idea).

The language changes are three generic built-ins and no
new syntax: `Future[T]` (from a scope's `start`), `Promise[T]` and
`Shared[T]`. Everything else is library and the two runtimes (`rt.h`,
`native/rt.c`).
The design rules out data races without any annotation. That follows from
rules obx already has: single ownership, borrows that cannot be stored, and
globals that cannot be borrowed.

## 2. Survey

| Model | Language cost | Runtime cost | Correctness risk | Fit for obx |
|---|---|---|---|---|
| OS threads + locks (C, Java) | mutex types, a memory model, ideally Send/Sync | thread-safe allocator, atomic refcounts, TLS | highest: data races, deadlocks, lock-order bugs | poor |
| Go: goroutines + channels, M:N | `go`, `chan`, `select` | big: work-stealing scheduler, growable stacks (these need precise stack maps), GC | data races still possible; deadlocks; a race detector is needed | blocking style yes; M:N and growable stacks no |
| Node/libuv: one thread, event loop, promises, async/await | `async`/`await` colours every function; escaping closures | small loop plus a thread pool for files and DNS | no memory races; logical races at each `await`; one blocking call stalls everything | event loop yes, colouring no |
| Erlang/BEAM: processes, private heaps, mailboxes | spawn, send, receive | large: per-process GC, preemptive scheduler | very low; let it crash plus supervisors | the model fits well, and moves make it cheaper than Erlang's copying |
| Rust: Send/Sync threads, async | auto traits, lifetimes, Arc/Mutex, `Pin`, coloured async, no async drop | async runtime (Tokio) is a large library | no data races (proven); deadlocks possible; complexity is the cost | take the Send idea, leave the rest |
| Structured concurrency (Trio, Swift TaskGroup and actors, Java Loom, Kotlin) | scopes or nurseries; Swift and Kotlin add colouring, Loom does not | Loom: virtual threads, stackful, no colouring | no orphan tasks; clean cancellation; races depend on the memory model | scopes match second-class borrows very well |
| Pony reference capabilities | six capabilities (iso, trn, ref, val, box, tag) | actors, per-actor GC | no data races, no locks | obx owned values already behave like `iso` and `str` like `val`, without the annotations |
| Lua/Wren coroutines | a library: create, resume, yield | tiny: a stack switch | none extra; one thread | the right building block |
| Shared-nothing processes (nginx, PostgreSQL, Apache prefork, gunicorn) | none | fork plus a supervisor | lowest; crashes are isolated | best first step for using several cores |
| epoll / io_uring loops | none; a mechanism, not a model | epoll is small; io_uring is bigger and blocked in some sandboxes | the loop is correct once, in the library | epoll first; io_uring later if file I/O needs it |
| Cilk / rayon fork-join | spawn/sync, or closures plus Send/Sync | work-stealing pool | rayon: no races; Cilk: races possible | obx rule 3 already checks disjoint closure captures; last step |
| Seastar, Glommio (thread per core, shared nothing) | none | per-core loops, cross-core queues | low | the same shape as layer 2, with processes in place of threads |
| Vale / Hylo / Austral | Vale: region borrowing for parallel loops over frozen data (proposed); Hylo: value semantics plus structured concurrency, aiming to avoid colouring; Austral: linear types, no concurrency design yet | — | — | obx is closest to Hylo and Vale, and their direction agrees with this note |

Two observations from the survey:

- **Colouring and stacks are the same trade.** A stackless design
  (async/await, Kotlin `suspend`, Rust futures) saves memory per task but
  marks every function that can wait. A stackful design (Go, Loom, Lua)
  leaves code unchanged but gives each task a stack. obx compiles through C,
  so it cannot grow and move stacks the way Go does: that needs precise stack
  maps. It can still have fixed-size stacks with guard pages, the way C
  coroutine libraries and Loom's carrier model work.
- **Data races come from shared mutable memory, not from threads.** Erlang,
  Pony and Rust prevent them in three ways: copying, capabilities and
  Send/Sync. obx gets the same result from single ownership. An owned value
  has one owner, so moving it to another task leaves the sender nothing to
  race with.

## 3. How close obx already is to "no shared mutable state"

What already holds:

- **Owned values have one owner.** Moving one into a child task's argument, or
  out of it as its result, transfers it whole. No alias survives in the
  sender.
- **Borrows cannot be stored** in a field, an element, a global or a type
  argument (rule 1). So no borrow can be put into a task's argument or result,
  or into a `Shared` value. This
  is the property Rust needs `Send` and `'static` bounds to get.
- **Globals cannot be borrowed** (5.2). A copy global is read and written by
  value. Within one thread, two tasks touching it interleave only at
  suspension points. That is a logical race, not a memory race.
- **Const tables** are read-only static data.
- **A `once` global** is never freed before exit, so its `get()` borrow is
  valid in every task.
- **Non-escaping closures (`&fn`)** cannot be stored, so they cannot leak into
  a started task.

What breaks once there is more than one OS thread:

1. **`str` reference counts** (`rt_str_dup`/`rt_str_drop` in `rt.h`) use plain
   `++`/`--`. Copies and substrings share blocks. A record moved to another
   thread may hold a `str` whose block the sender also holds, and both
   threads then update the same count. Fixes:
   - atomic counts. Simple; costs a `lock xadd` per copy.
   - a transfer step: messages that cross threads copy any string block whose
     count is above 1. The compiler would generate this per type, like drop
     glue.
   - immortal strings (count below 0, as literals are now) for data that is
     frozen and shared, such as a `once` global after `set`.

   Fork-join over shared borrowed data needs atomic counts, because the
   workers copy strings out of data they all read.
2. **Mutable copy globals** are shared memory. One option is thread-local
   globals, as D does by default (D makes `shared` explicit). Another is to
   forbid writes to them from code a worker thread can reach, which needs the
   effect summaries of SPEC 13.
3. **`once` globals:** `set` needs an atomic compare-and-swap. Its value's
   strings must become immortal, or the counts must be atomic. A simpler
   rule: `set` after the first thread starts stops the program.
4. **Runtime statics** in `rt.h`: `rt_live`, the stdout buffer `rt_obuf`, the
   extern string temporaries `rt_ctmp`, the static stat buffer `obx_st`, and
   the stack bounds used for overflow detection. Each becomes thread-local or
   atomic. `obx_stat` followed by `obx_stat_field` is a two-call protocol that
   only works with one thread.
5. **C resources** (`ptr` inside a wrapper record). Moving a `PGconn*`,
   `sqlite3*` or `SSL*` to another thread is usually fine, because a move
   does not share it. Some C libraries are tied to one thread. A wrapper
   module would need a way to mark its type as staying on its thread
   (opt-out, not opt-in).
6. **Handles (`^T`, planned).** A handle is an alias. Two tasks holding
   handles to the same object reintroduce shared mutable state, even inside
   one thread: one task dereferences, then parks, and the other frees the
   object. A handle must not be in a task's argument or result, or in a
   `Shared`. These could be limited to
   handle-free types, or a borrow obtained through a handle could be
   forbidden from staying live across a call that can suspend.

**With one thread per process (layers 1 and 2), only item 6 applies, and
handles do not exist yet.**

## 4. Why stackful coroutines are sound in obx without colouring

The usual danger of coroutines on one thread is this: task A holds a pointer
into shared data, it parks, task B changes or frees the data, and A resumes
with a dangling pointer. Rust's async needs `RefCell` or locks for that
reason, and colouring is what tells you where the parks are.

In obx, every heap object is owned by exactly one place. That place is one
task's frame, a task's argument or result in transit, a `Shared` value
(section 0), or the scheduler. Every borrow is rooted
in its own task's frame (rule 2) and cannot leave it (rule 1). So the only
data a parked task can have a borrow into is data that only that task can
reach. Copy globals cannot be borrowed. A `once` global is never freed. That
leaves nothing B can free or mutate under A. The proof does not need to know
which calls suspend, so no function needs to be marked.

It follows that tasks cannot share a mutable context through a borrow.
Handing `mut &ctx` to two tasks would be unsound, and the existing rules
already reject it, because `start` takes a plain `fn` and an owned argument.

**`Shared[T]`** is the one deliberate exception: several tasks hold a handle
to one value. It stays sound because its only access is `with`, which lends
the value to a closure, and **no task may park while any `with` is open**.
While a borrow into shared data is live, no other task runs, so nothing can
free or move the data under it. Two `with` blocks on the same value at once
(a nested call) would be two mutable borrows of it, so the value carries a
busy flag. Both are checked at run time for now (section 5); a compile-time
check needs the "can park" effect of every function (section 8, Q9). This is
the one place where the soundness argument needs to know which calls park.

## 5. Staged plan

### Stage C1: coroutines and an event loop in one process

Runtime, in both `rt.h` (C backend) and `native/rt.c` (native backend; it
already makes raw system calls and has its own SIGSEGV handler):

- A context switch: save and restore the callee-saved registers, the stack
  pointer and the resume address, about 15 instructions and 20 lines of
  assembly each for x86-64 and aarch64. Do not use `swapcontext`: it makes a
  signal-mask system call on every switch.
- Coroutine stacks from `mmap`, with a guard page below each. The default is
  256 KiB virtual, settable per `start`. Stacks are reused from a free list.
- Stack overflow (exit 4) per coroutine. The SIGSEGV handler checks the
  faulting address against the current task's guard page, not against the
  main stack's `rt_stack_top`.
- An epoll loop, timers (a heap of deadlines), and park/wake. Every
  suspension goes through one function, `rt_park`.
- A helper thread pool for blocking C calls (see below). It runs C code
  only, never obx code. Only dynamically linked programs need it (a static
  native program calls no C library). The native runtime's own `malloc` is
  not thread safe: before the pool exists, check which `malloc` the C
  libraries bind to, and lock the allocator or keep them on libc's.

Language (compiler built-ins, no new syntax). Decided 2026-11-03: futures,
not channels (section 0).

- `s.start[A, T](f: fn(A): T, arg: A): Future[T]`, on a scope (below). It
  takes a plain function value and an owned argument, never a `&fn`. Code
  generation boxes the argument per instance and calls `f` on the new stack;
  the task's result is moved into a slot in the future.
- `Future[T]` is owned and not copy: one place waits on it. `f.wait():
  Result[T, Cancelled]` consumes it, parks until the task has finished, and
  moves the result out (2–5 ns if it has already finished). A task's `Result`
  error comes back through `wait`, so `try` passes it on. Dropping a future
  without waiting cancels its task (and waits for it to unwind, at the scope's
  end at the latest).
- `task.all(fs: [Future[T]]): Result[[T], Cancelled]` waits for every one
  (C#'s `WhenAll`); `task.any(fs)` returns the first result and cancels the
  rest, waiting until they have unwound (C#'s `WhenAny`, with Trio's rule
  that nothing is left running).
- `task.promise[T](): (Promise[T], Future[T])`, a future completed by hand
  (C#'s `TaskCompletionSource`). `p.complete(v: T)` consumes the promise and
  never parks. Dropping a promise without completing it makes `wait` return
  `Err(Abandoned)`, so a lost reply is never a silent hang.
- `Shared[T]`: one value that several tasks reach. The handle is a copy type,
  counted like `str`; the value is dropped with the last handle. `task.shared(v:
  T): Shared[T]`, and `sh.with(f: &fn(mut &T))` is the only access. The
  runtime keeps the open `with` blocks; an operation that can park, called
  while one is open, stops the program with exit 13 (`obx: a task parked
  inside Shared.with (the with at file:line) at file:line`). `with` on a value whose `with` is already open
  (a nested call) also stops with exit 13. Nesting `with` on different values
  is allowed. Each check is a load and a branch, about 1 ns. A `with` block
  is atomic: no other task runs inside it, so it is a critical section
  without a lock. Later the compiler rejects a `with` whose closure can reach
  a park (section 8, Q9).

What parks: waiting on a future, a socket read or write that would block,
sleeping and timers, a call to a `blocking` extern on the helper pool, and
`task.yield()`. File I/O and `println` stay plain blocking system calls and
do not park, so they are fine inside `with`. A drop inside `with` (replacing
a value whose drop flushes to a socket) can park and then traps.

Channels are not built in. If a streaming program needs one (a producer and
a consumer with backpressure), `Chan[T]` is library code: a bounded queue in
a `Shared`, with promises for the tasks waiting on it.

Measured costs (`obx/bench/tasks.obx`, best of 5, WSL2, gcc 13 -O2 for the C
backend, 2026-11-03), in ns per operation, each including its obx wrapper
(the `Result` it returns, the boxing of argument and result):

| operation | C backend | native |
|---|---:|---:|
| task switch (`task.yield()` between two tasks) | 6.4 | 17.7 |
| `start` with a stack from the free list | 35 | 26 |
| `start`, then a `wait` that parks: the task runs and ends, the waiter resumes | 86 | 71 |
| `wait` on a finished task (no park) | 21 | 21 |
| promise `complete` | 11 | 16 |
| `Shared.with` (a closure adding 1) | 2.6 | 5.2 |

The switch itself (`rt_ctx_swap`) is 22 instructions; the native backend's
switch figure is mostly its unoptimized `yield().unwrap()` wrapper. A fresh
stack is an `mmap` and an `mprotect` (first use only; 64 stacks are kept
for reuse).

C1a as built (`lib/task.obx`; SPEC 16). Deviations from the plan above:
`task.promise` returns a `task.Handoff[T]` record with the fields
`promise` and `future` (obx has no tuples, by design: a record names its
parts);
`task.scoped[R]` is `scope` for a function with a result (no "no result"
type argument); the error type is `task.Cancelled`, a closed type with the
variants `Cancelled` and `Abandoned` (written `Cancelled.Abandoned`); `start`
has no stack-size argument; `both` and `forEach` are not built (they need
borrows into tasks). A deadlock is exit 14.

The exit 13 and 14 messages name the user's lines (SPEC 14), through an
implicit call-site parameter like Rust's `#[track_caller]`: `std.Where` (a
lang item, SPEC 15) is the text `"file:line"`, and a call of a library
function whose last parameter is a `Where` that leaves it out gets the
call's own location. Every task function that can park (`scope`, `scoped`,
`wait`, `all`, `any`, `yield`) and `with` takes one and hands it to the
runtime (`all` and `any` pass theirs on to `wait`). The runtime keeps the
open `with` blocks as a stack (each `Shared` block records where its `with`
was called; no task can park while one is open, so the stack is global) and
each task's last park location:

- `obx: a task parked inside Shared.with (the with at a.obx:37) at a.obx:41`:
  41 is the call that would park, 37 the innermost open `with` (the one whose
  closure made the call, directly or through functions).
- `obx: Shared.with on a value whose with is already open (the with at
  a.obx:30) at a.obx:35`: the open `with` on the same value, and the new one.
- `obx: deadlock: every task is parked at a.obx:41`: the call whose park found
  nothing runnable; when a task finishes and leaves nothing runnable, the
  place where its scope's owner is parked.

Dropping a `Future` does not park (its task is cancelled, and the scope's
end waits for it), so it needs no location; `any` joins the losers at its
own call's location.

C1b as built (`lib/net.obx`, `task.sleep`, `task.within`; SPEC 16), in both
runtimes (the native one with raw system calls, so a static program stays
static):

- **The event loop.** The scheduler owns one epoll instance, made when the
  first socket is. Each socket is registered once, edge-triggered for
  reading and writing, when it is made; an operation tries the system call
  first and parks only on `EAGAIN`, in a table indexed by fd (one reader and
  one writer per socket). Timers are a binary heap of parked tasks ordered by
  (time, sequence), so equal times fire in the order they were set. When no
  task is runnable, the scheduler expires the due timers and calls
  `epoll_wait` with the nearest timer as its timeout (`nanosleep` when no
  socket is waited on); while tasks are runnable it polls without waiting
  every 64 switches, so a busy program still sees its sockets. The
  deadlock report (exit 14) now needs every task parked, no socket waited
  on and no timer pending; its location rules are unchanged. A spurious or
  stale wake is harmless because every operation retries.
- **Deadlines, not timeouts on each call.** `task.within(ms, f)` sets the
  running task's deadline (the earlier wins; restored after `f`), and the
  tasks started meanwhile inherit it. Every park that can be cancelled sets
  a timer at the task's deadline, and every call that can park checks, in
  this order: inside `with` (exit 13), cancelled, past the deadline. The
  waits give `Err(Cancelled.Timeout)`, the `net` calls `ETIMEDOUT`. Why a
  deadline on the task rather than a timeout argument on each call (Go's
  `SetDeadline` on a connection, or a `timeout` parameter): one deadline
  bounds a whole exchange (connect, write, read) and the work it starts, as
  Trio's `move_on_after` and Go's `context.WithTimeout` do, and no call
  needs a second signature. A read with a timeout is
  `task.within(500, fn(): Result[int, io.IoError] { return c.read(mut &buf) })`.
- **A cancelled or expired task's I/O does nothing.** `sleep` and the `net`
  calls return the error at once, even when they would not park (Trio's
  checkpoints). Otherwise a loop that always finds data would never see its
  cancellation under load. `wait` still gives a result that is already
  there, which would otherwise be lost.
- **`net`.** TCP over IPv4 and IPv6: `listen` (`SO_REUSEADDR`, backlog
  4096), `accept`, `connect` (non-blocking; it parks until writable, then
  reads `SO_ERROR`), `read`, `write` (all of it, with `MSG_NOSIGNAL`, so a
  closed peer is `EPIPE`, never `SIGPIPE`), `closeWrite`, `setNoDelay`,
  `localAddr`, `peerAddr`; `Listener` and `Conn` are owned and closed by
  their drop, which never parks. Errors are `io.IoError` with the address as
  the path, and `isCancelled()`/`isTimeout()` on `IoError`. Addresses are
  numeric (RFC 5952 text for IPv6) plus `localhost`: `getaddrinfo` blocks,
  so names wait for C1c's helper pool.
- **`Scope.spawn(f, arg)`**, not in the plan: a task with no future. An
  accept loop cannot keep a future per connection (they pile up), and
  dropping one cancels its task; a spawned task's result is dropped, it is
  cancelled with its scope's task, and the scope still waits for it, so
  every task is still in a scope.
- `main` parks like any task: a plain program uses sockets and `sleep` with
  no special mode. `time.sleepMs` still blocks the thread.

Deviations: `within` has only the form with a result (a closure with no
result cannot give the type argument; return a value, or use `scoped`
inside); a `Conn` is used by one task at a time (no split into halves yet);
an idle connection keeps its task and stack (the stack-less idle
connection of section 7 is not built); no `io.Reader`/`Writer` interfaces
(the language has no interface values yet).

Measured (`bench/tasks.obx`, ns per operation, C / native): a wait that
parks with a deadline (timer set and removed) 108 / 90, against 95 / 75
without; a `sleep(1)` that fires, beyond a yield, about 20–260 / 40–290
(noisy: the 1 ms sleep's overshoot is spread over 10,000 tasks); a socket
read that finds data 650 / 710; a one-byte round trip between two tasks
over loopback TCP 180,000–210,000 / 205,000, which is this machine's
loopback (WSL2: a Python ping-pong, threads or epoll, takes 190,000–205,000
too). Both runtimes read the clock through the vDSO (libc's
`clock_gettime`; the native runtime finds `__vdso_clock_gettime` itself,
design/native.md section 2), about 20 ns a read, once per timer set; until
2026-11-03 the native runtime made the system call (about 250 ns) and its
timed wait cost 316–339 ns. The gate (`cmd/http_gate.py`, one core for
the server, one for the load client): 10,000 idle keep-alive connections
at 13.0 KB of RSS each
(native; 12.7 KB through C): two touched stack pages, the 4 KiB read buffer
and the task; 4,900 / 38,900 / 69,400 requests per second with 1 / 10 / 100
clients while they stay open (p99 0.33 / 0.37 / 1.9 ms), the same through C
within 5%, against Go's net/http (GOMAXPROCS=1: 19.8 KB per connection,
46,000 req/s at 100 clients) and Node 24 (9.3 KB, 42,900 req/s); `/shutdown`
with 10,000 connections open ends in 0.19 s with exit 0 under `OBX_CHECK=1`.
PERFORMANCE.md has the table.

Library (`task`, `net`, `time`):

- `task.scope(fn(s: mut &Scope) { ... s.start(f, x) ... })` is structured, as
  in Trio. The scope returns only when all its tasks have finished. `main`
  runs in the root scope, so no task outlives `main`, the globals are still
  dropped last, and the `OBX_CHECK=1` leak check stays meaningful.
- `task.both(a: &fn(), b: &fn())` and `task.forEach(xs: mut &[T], f:
  &fn(mut &T))` run concurrent children that may borrow the parent's locals.
  This is sound because the parent cannot run until they finish. The current
  checker already rejects `both(fn() { x = 2 }, fn() { println(x) })` ('x' is
  borrowed mutably twice in one call). `forEach` runs one closure several
  times at once, so it must reject mutable captures: one new check.
- Cancellation and deadlines: every call that can park returns
  `Err(Cancelled)` or `Err(Timeout)`. The task unwinds with `try`, and its
  drops run. No task is ever killed from outside, so `drop` always runs.
- `net`: listen, accept, read and write on non-blocking sockets. They park on
  `EAGAIN`. The library needs `obx_*` helpers for `sockaddr`, like the stat
  helpers.
- Deadlock report: if every task in the process is parked with no I/O and no
  timer pending, the program stops with an error, as Go reports "all
  goroutines are asleep".

Order of work: C1a is `start`, `Future`, `Promise`, `Shared`, scopes,
`all`/`any`, cancellation and the deadlock report, with no I/O, in both
runtimes and checked by the native-vs-C differential. C1b is epoll, `net`
and timers. C1c is the helper pool, the `blocking` marker and libpq's
non-blocking path.

Blocking C calls fall into three kinds:

| C library behaviour | Approach |
|---|---|
| Has a non-blocking API with a socket (libpq: `PQsendQueryParams`, `PQsocket`, `PQconsumeInput`, `PQisBusy`, `PQconnectPoll`; OpenSSL; curl multi) | the wrapper parks on the socket. No threads. Best case. |
| Short blocking calls (SQLite on local disk, `stat`) | call inline. The worker stalls briefly, and the other workers keep serving. |
| Long blocking calls (large SQLite queries, `getaddrinfo`, password hashing) | run on the helper thread pool: the task parks and a C thread runs the call. nginx and libuv use thread pools this way. Arguments are ints, `ptr`s, or string bytes pinned for the call. The task is parked, so its memory is untouched. errno is captured on the helper thread. |

For the third kind, the extern needs a marker, for example
`extern "sqlite3" fn sqlite3_step(st: ptr): i32 blocking`. Externs are
already private to their module, so the wrapper module makes the choice. A
`blocking` extern may not take a function argument: since the language batch
an `extern fn` accepts any obx function of the matching type, and a callback
would run obx code on the helper thread.

Gate: an HTTP/1.1 server on one core that holds 10,000 idle keep-alive
connections and serves a Postgres query per request through a pool, with no
leaks under `OBX_CHECK=1` after a graceful shutdown.

### Stage C2: workers as processes

Section 9 records an idea under consideration, process futures, that would
subsume this stage if adopted.

- `task.workers(n, f: &fn())` runs `f` in n forked children. It flushes
  stdout before forking, and it forks before any connection or helper thread
  exists. A supervisor in the parent restarts a crashed worker and forwards
  SIGTERM for a graceful drain.
- Sockets: listen with `SO_REUSEPORT` in each worker. The other option is to
  share the parent's listener with `EPOLLEXCLUSIVE` to avoid waking every
  worker.
- Each worker has its own database pool. The total is workers × pool size,
  so the Postgres `max_connections` budget matters, or use pgbouncer.
- No language change. Workers share nothing except the database, which is
  correct for financial data anyway.

Gate: throughput scales with cores; killing a worker loses only its
in-flight requests.

### Without threads

How a server gets what threads would give it, with no language change:

| What threads give | Without threads |
|---|---|
| one in-memory cache for every core | a cache per worker (fine unless large); data loaded before the fork, shared copy-on-write; Redis or the database; read-only data in a memory-mapped file |
| less memory | code pages are shared; data loaded before the fork is shared until written; 8 workers × 50 MB of heap is 400 MB |
| rebalancing around a slow connection | more workers than cores; `EPOLLEXCLUSIVE` on accept; heavy work to another process |
| global counters (rate limits) | a small fixed-size shared-memory counter area (nginx's zones), or Redis |
| a shared TLS session cache | TLS 1.3 session tickets need no shared state |
| fewer database connections | pgbouncer |
| several cores on one large dataset (batch) | processes sharing the data copy-on-write after a fork (section 9) |

Keeping the door open costs almost nothing, so keep these rules:

- `str` counts are changed in one place: `rt_str_dup`/`rt_str_drop` and the
  one routine in each backend that inlines them.
- New runtime state is marked as per-process; no hidden statics.
- No global lock, ever: parallelism is real or absent.
- `Shared` belongs to one thread; anything shared across threads would be a
  separate type.

### Stages C3 and C4: not planned

Worker threads (C3) and fork-join threads (C4) were sketched in the first
version of this note. Both are dropped by the shared-nothing decision
(section 0); the sketch is in git history. They would need atomic or biased
`str` counts, thread-local runtime state and copy globals, a per-thread
allocator in the native runtime, a `send` constraint, and markers for C
types tied to one thread.

## 6. Sketch

```obx
import "task"
import "net"
import "./db"

type Job = record { sock: net.Socket; pool: db.Pool }   // db.Pool holds a Shared[PoolState], a copy type

fn main() {
    let ln = net.listen("0.0.0.0:8080").expect("listen")
    task.workers(8, fn() {
        let pool = try db.openPool(10, "dbname=ledger")   // 10 PG connections in a Shared
        task.scope(fn(s) {
            while true {
                match ln.accept() {                         // parks
                    Ok(sock) { s.start(serve, Job{sock: sock, pool: pool}) }
                    Err(e) { eprintln(e) }
                }
            }
        })
    })
}

fn serve(j: Job): Result[(), db.Error] {
    var conn = try j.pool.acquire()              // parks until a connection is free; moved to us
    return task.scope(fn(s) {
        let q = s.start(fetchQuote, "EURUSD")    // fan out: runs while we query
        let rows = try conn.query("select ...")  // libpq async API; parks on its socket
        let quote = try q.wait()
        ...
    })
}   // conn is dropped: its drop puts it back in the pool
```

The pool is a `Shared[PoolState]` with a list of free connections and a list
of waiting promises. `acquire` takes a free connection inside `with`, or, if
there is none, pushes a promise and waits on its future after the `with` has
closed. Dropping a `PooledConn` opens `with` and either completes the first
waiting promise with the connection or puts it back on the free list;
neither parks. No locks and no server task.

## 7. Costs

- **Lost compared with threads and shared memory:** no in-process cache
  shared across cores (use the database, Redis, or one cache task per
  worker); no parallel speedup inside one request (processes for batch work, section 9); heap memory and
  database connections multiply by the worker count (code pages are shared).
- **Cooperative scheduling:** a CPU-heavy handler, such as parsing a 50 MB
  JSON body or a big decimal batch, stalls its worker's other connections.
  Mitigations: an explicit `task.yield()`; sending the work to the helper
  pool or another worker; or, later, a budget counter the compiler places at
  loop back-edges (Go added asynchronous preemption in 1.14 for this reason).
- **Stacks:** fixed size, with no growth, because C output has no stack maps.
  At 256 KiB virtual and 8 to 32 KiB resident, 10,000 active tasks take
  about 2.5 GB of address space and 80 to 320 MB of memory (C1b measured an
  idle HTTP connection's task at two pages, 8 KiB). Deep recursion
  in a handler overflows sooner (exit 4). C libraries set the floor:
  OpenSSL, `getaddrinfo` and printf-style code use several KB to tens of KB.
  Mitigation: keep idle keep-alive connections in epoll with no task, and
  take a stack only when bytes arrive.
- **Drop and ownership:** a drop may park (a Writer flushing to a slow
  socket). That is fine for stackful tasks and is an advantage over async
  designs (Rust still has no async drop). Cancellation only works if every
  parking call returns an error, and library code must not swallow
  `Cancelled`.
- **Panic** stops the whole worker, because obx does not unwind. The
  supervisor restarts it, and in-flight requests on that worker fail. Making
  a panic stop only its task would need unwinding or leaked task memory.
  Stopping the worker is also the cautious choice for financial state.
- **Remaining bugs:** no memory races and no lock deadlocks, but logical
  races remain (copy globals between suspensions; two requests updating one
  row, for which databases still need transactions and isolation levels).
  Waits that can never finish remain (a promise kept but never completed);
  the runtime detects the all-parked case, and a dropped promise is
  `Err(Abandoned)`. A `with` that parks traps (exit 13) until the compiler
  checks it.
- **Native backend:** needs a context-switch routine, per-task stack bounds
  for its overflow check, and stack probes that respect guard pages. The
  calling convention does not change. The compiler does not have to turn
  functions into state machines, as async would require. With no threads it
  needs no TLS and no atomic instructions. Known frame sizes could later allow smaller
  stacks for call graphs without recursion.
- **io_uring:** not needed for thousands of sockets; epoll handles that.
  io_uring helps file I/O and batching, and it can be added behind the same
  park/wake interface.

## 8. Open questions

All decided 2026-11-03 (section 0): Q2 and Q9 as written below, the others
as recommended.

1. Are processes acceptable as the only way to use several cores for the
   foreseeable future (C2), giving up a shared in-process cache?
   Recommended: yes.
2. ~~Shared queues `Chan[T]` (Go) or typed per-task mailboxes `Task[M]`
   (Erlang)?~~ Decided: neither as a built-in. Futures, promises and
   `Shared`; channels in the library if needed.
3. Should every task be started from a scope (Trio-style), or should detached
   tasks be allowed? Recommended: scopes only, so no task outlives `main` and
   the leak check stays meaningful.
4. Purely cooperative scheduling, or compiler-inserted yield checks at loop
   back-edges? Recommended: cooperative with `task.yield()` first; the checks
   later as an IR pass (it serves both backends) if CPU-heavy handlers need
   them.
5. Should a panic stop the worker or only the task? Recommended: the worker;
   obx does not unwind.
6. ~~For C3: thread-local copy globals or no writes from workers? Atomic
   `str` counts or a transfer step?~~ Superseded: no threads (section 0).
7. Mark blocking C calls with a `blocking` marker on `extern`, or with a
   library call that runs a function on the helper pool? Recommended: the
   marker, which also lets the compiler reject function arguments.
8. When? Stage 4 and the native backend are done. Recommended: C1a after the
   `ir` and `perf2` branches merge and the docs pass.
9. ~~Parking inside `Shared.with`: trap, lock, or compile error?~~ Decided:
   trap at run time now (exit 13), compile error later from a whole-program
   "can park" effect (direct calls exactly; calls through function values
   and interfaces conservatively, over every matching function). The later
   check may reject a program whose parking path is never taken, so moving
   to it can break programs that run today; that cost is accepted. A lock
   was rejected: held across a park, it brings back deadlocks and latency.

## 9. Ideas under consideration

Not decisions. Recorded so they are not lost; each needs its own discussion
before it is planned.

### Process futures (2026-11-03)

A future promises "a `T` later, or an error"; where the work runs does not
matter to the waiter. So a process could be one more way to start a future,
with the same `Future[T]`, `wait`, `task.all` and `task.any`, mixing tasks and
processes.

**Our own program's work in a process** (the main idea):

```obx
task.scope(fn(s) {
    var fs: [Future[Result[Valuation, ProcessError]]] = []
    for chunk in split(portfolio, 8) {
        fs.push(s.startProcess(valueChunk, chunk))   // 8 cores, no threads
    }
    let parts = try task.all(fs)
    report(merge(parts))
})
```

- The parent parks on a pipe in its epoll loop; other tasks keep running.
- A child that panics, runs out of memory or crashes comes back as
  `Err(ProcessFailed{exit})`: failure stays inside that piece of work.
- Cancellation sends SIGTERM, then SIGKILL after a deadline.
- What crosses the pipe is encoded by compiler-generated code (like drop
  glue), behind a new derived constraint (say `wire`, like `copy`) that a type
  fails if it holds a `ptr`, a `Shared` or a function value. That is a
  language change.

Three ways to start the child:

| Method | Argument | Main risk | Start (ns, estimate) |
|---|---|---|---:|
| `fork` directly | free (copy-on-write) | unsafe once helper threads exist (a lock held by another thread stays held); the child inherits open C resources such as a Postgres socket | 50,000–5,000,000 (grows with the page tables) |
| fork server: a small process forked at program start, forking each child on request | encoded | the child starts from the program's early state | 50,000–150,000 |
| re-run our own executable with a hidden flag naming the function | encoded | slowest start; cleanest child | 1,000,000–3,000,000 |

The fork server is the likely choice. Python's `multiprocessing` used plain
`fork` on Linux for years, hit the threads-and-locks problem, and moved its
Linux default to a fork server in 3.14. Sending a result back costs
2,000–5,000 ns plus about 1 ns per byte, so process futures suit coarse work
(milliseconds or more), not one per request.

If adopted, this would subsume stage C2 (`task.workers` becomes n process
futures and a supervisor loop that restarts any that return `Err`) and is
the shared-nothing way to put several cores on one batch job.

**Other programs** (a side benefit): `s.run("gzip", ["-9", path])` returning
`Future[Result[Exit, IoError]]`, through `posix_spawn` and pipes. The library
has no process spawning today (the compiler driver calls gcc with
`system()`). No language change.

Open points: the `wire` constraint (the user's decision); a marker for C
wrapper types that must not be used across a fork; a child reaching a
`Shared` through a global changes only its own copy.
