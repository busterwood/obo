# Concurrency for obx

Status: research note, 2026-11-02; first decisions 2026-11-03 (section 0).
Future work: after the self-hosted compiler (stage 4) and the native backend
(stage 5), both done. Nothing here changes SPEC.md draft 1 yet.

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

- **Q1, Q3 to Q8 as recommended** (section 8): processes are the only way to
  use several cores for now; every task is started from a scope; scheduling
  is cooperative with `task.yield()`, yield checks later as an IR pass if
  needed; a panic stops the worker; threads (C3) are deferred; blocking C
  calls are marked `blocking` on the extern; C1a starts after the `ir` and
  `perf2` branches merge and the docs pass.

Related: obo's [systems-threads-design.md](../../archive/obo/design/systems-threads-design.md)
reached a similar verdict for obo 0.12: one reactor per process first, then
supervised processes, and threads only behind strict gates. This note keeps
that verdict and changes one thing. Handlers are written in blocking style on
stackful coroutines, not as explicit state machines.

## 1. Recommendation

Build concurrency in three layers. Each layer is useful without the next one.

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
3. **Later, and only if a real program needs it:** worker threads in place of
   processes, and then fork-join data parallelism. Both need runtime work
   (atomic string reference counts, thread-local runtime state) and a little
   language work (thread-local copy globals, an opt-out for C types that are
   tied to one thread).

The language changes for layers 1 and 2 are three generic built-ins and no
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
| Seastar, Glommio (thread per core, shared nothing) | none | per-core loops, cross-core queues | low | this is layer 3 |
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
  runtime keeps a count of open `with` blocks; `rt_park` with that count above
  zero stops the program with exit 13 (`obx: a task parked inside
  Shared.with at file:line`). `with` on a value whose `with` is already open
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

Estimated costs, to be replaced by benchmarks: `start` with a reused stack
50–150 ns (a fresh `mmap` 2,000–5,000 ns, first use only); `wait` that parks
and resumes 10–30 ns; a task switch 3–10 ns; `complete` 3–8 ns; `with` 2–5
ns; `task.all` about 10 ns per future.

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

### Stage C3 (optional): workers as threads

Do this only if per-worker memory or a shared in-process cache becomes a
real need. Requirements:

- Thread-local runtime statics, as listed in section 3.
- `str` counts: either atomic, or kept single-threaded with a transfer step
  for messages between workers. Decide by benchmark.
- Copy globals become thread-local, as in D. `once.set` becomes atomic and
  makes the value's strings immortal.
- Tasks stay pinned to their worker: no work stealing between threads. Two
  tasks in one worker may share string blocks, so moving a task to another
  thread would race their counts.
- A `send` constraint, derived like `copy`, for values that cross workers. A
  type fails it when it contains a handle or a C type its module marked as
  tied to one thread.
- The native backend needs TLS access to runtime state, `lock`-prefixed
  instructions for the counts, and a per-thread allocator if it replaces
  malloc.

### Stage C4 (optional): fork-join parallelism

`par.both`, `par.forEach(mut &[T], &fn(mut &T))` and `par.map` run on a
work-stealing pool, with the same borrow rules as `task.both` and
`task.forEach`. This needs C3, atomic `str` counts (the workers copy strings
out of data they share), and the copy-global decision. It suits batch
computation, such as risk or valuation runs over large arrays of decimals,
not the web server itself.

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
  worker); no parallel speedup inside one request until C4; heap memory and
  database connections multiply by the worker count (code pages are shared).
- **Cooperative scheduling:** a CPU-heavy handler, such as parsing a 50 MB
  JSON body or a big decimal batch, stalls its worker's other connections.
  Mitigations: an explicit `task.yield()`; sending the work to the helper
  pool or another worker; or, later, a budget counter the compiler places at
  loop back-edges (Go added asynchronous preemption in 1.14 for this reason).
- **Stacks:** fixed size, with no growth, because C output has no stack maps.
  At 256 KiB virtual and 8 to 32 KiB resident, 10,000 active tasks take
  about 2.5 GB of address space and 80 to 320 MB of memory. Deep recursion
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
  functions into state machines, as async would require. Threads (C3) add
  TLS and atomic instructions. Known frame sizes could later allow smaller
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
6. For C3: thread-local copy globals (D) or no writes from workers? Atomic
   `str` counts or a transfer step? Recommended: defer; lean to thread-local
   globals, counts by benchmark. `Shared` stays worker-local.
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
