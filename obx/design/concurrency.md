# Concurrency for obx

Status: research note, 2026-11-02. Not a decision. Future work: after the
self-hosted compiler (stage 4), alongside or after the native backend (stage
5). Nothing here changes SPEC.md draft 1.

Related: obo's [systems-threads-design.md](../../obo/design/systems-threads-design.md)
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
   one. There is no `async`/`await` and no function colouring. Tasks share
   nothing. They exchange owned values over channels, and a send is a pointer
   move, not a copy.
2. **Across cores: shared-nothing worker processes,** the nginx model. A
   supervisor forks N workers and each worker runs layer 1. The language
   needs nothing for this, and a crash stays inside one worker.
3. **Later, and only if a real program needs it:** worker threads in place of
   processes, and then fork-join data parallelism. Both need runtime work
   (atomic string reference counts, thread-local runtime state) and a little
   language work (thread-local copy globals, an opt-out for C types that are
   tied to one thread).

The language changes for layers 1 and 2 are two generic built-ins and no new
syntax: `spawn` and a channel type. Everything else is library and `rt.h`.
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

- **Owned values have one owner.** Moving one into another task's message
  transfers it whole. No alias survives in the sender.
- **Borrows cannot be stored** in a field, an element, a global or a type
  argument (rule 1). So no borrow can be put into a message or a task. This
  is the property Rust needs `Send` and `'static` bounds to get.
- **Globals cannot be borrowed** (5.2). A copy global is read and written by
  value. Within one thread, two tasks touching it interleave only at
  suspension points. That is a logical race, not a memory race.
- **Const tables** are read-only static data.
- **A `once` global** is never freed before exit, so its `get()` borrow is
  valid in every task.
- **Non-escaping closures (`&fn`)** cannot be stored, so they cannot leak into
  a spawned task.

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
   object. A handle must not be in a message. Messages could be limited to
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
task's frame, a message in transit, or the scheduler. Every borrow is rooted
in its own task's frame (rule 2) and cannot leave it (rule 1). So the only
data a parked task can have a borrow into is data that only that task can
reach. Copy globals cannot be borrowed. A `once` global is never freed. That
leaves nothing B can free or mutate under A. The proof does not need to know
which calls suspend, so no function needs to be marked.

It follows that tasks cannot share a mutable context through a borrow.
Handing `mut &ctx` to two tasks would be unsound, and the existing rules
already reject it, because `spawn` takes a plain `fn` and an owned argument.
Anything shared, such as a database pool or a cache, is owned by a server
task and reached through messages (section 6).

## 5. Staged plan

### Stage C1: coroutines and an event loop in one process

Runtime (`rt.h`, about 300 lines of C):

- A context switch: save and restore the callee-saved registers, about 20
  lines of assembly each for x86-64 and aarch64. Do not use `swapcontext`: it
  makes a signal-mask system call on every switch.
- Coroutine stacks from `mmap`, with a guard page below each. The default is
  256 KiB virtual, settable per spawn. Stacks are reused from a free list.
- Stack overflow (exit 4) per coroutine. The SIGSEGV handler checks the
  faulting address against the current task's guard page, not against the
  main stack's `rt_stack_top`.
- An epoll loop, timers (a heap of deadlines), and park/wake.
- A helper thread pool for blocking C calls (see below). It runs C code
  only, never obx code.

Language (compiler built-ins, no new syntax):

- `spawn[T](f: fn(T), arg: T)`, through a scope (below). It takes a plain
  function value and an owned argument, never a `&fn`. Code generation boxes
  the argument per instance and calls `f` on the new stack.
- `Chan[T]`: a runtime-managed queue whose endpoint is a copy type,
  reference counted like `str`. This has to be built in: two tasks reaching
  one queue is shared ownership, which obx code cannot express. The compiler
  generates drop glue for messages still queued when the last endpoint goes.
  `send(v: T)` moves `v` in, and `recv(): Result[T, Closed]` parks until a
  message arrives.

Library (`task`, `net`, `time`):

- `task.scope(fn(s: mut &Scope) { ... s.spawn(f, x) ... })` is structured, as
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

Blocking C calls fall into three kinds:

| C library behaviour | Approach |
|---|---|
| Has a non-blocking API with a socket (libpq: `PQsendQueryParams`, `PQsocket`, `PQconsumeInput`, `PQisBusy`, `PQconnectPoll`; OpenSSL; curl multi) | the wrapper parks on the socket. No threads. Best case. |
| Short blocking calls (SQLite on local disk, `stat`) | call inline. The worker stalls briefly, and the other workers keep serving. |
| Long blocking calls (large SQLite queries, `getaddrinfo`, password hashing) | run on the helper thread pool: the task parks and a C thread runs the call. nginx and libuv use thread pools this way. Arguments are ints, `ptr`s, or string bytes pinned for the call. The task is parked, so its memory is untouched. errno is captured on the helper thread. |

For the third kind, the extern needs a marker, for example
`extern "sqlite3" fn sqlite3_step(st: ptr): i32 blocking`. Externs are
already private to their module, so the wrapper module makes the choice.

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

type Job = record { sock: net.Socket; pool: db.Pool }   // db.Pool is a Chan endpoint, a copy type

fn main() {
    let ln = net.listen("0.0.0.0:8080").expect("listen")
    task.workers(8, fn() {
        let pool = db.startPool(10, "dbname=ledger")      // a task that owns 10 PG connections
        task.scope(fn(s) {
            while true {
                match ln.accept() {                         // parks
                    Ok(sock) { s.spawn(serve, Job{sock: sock, pool: pool}) }
                    Err(e) { eprintln(e) }
                }
            }
        })
    })
}

fn serve(j: Job) {
    var conn = j.pool.acquire()                  // parks until a connection is free; moved to us
    let rows = conn.query("select ...")          // libpq async API; parks on its socket
    ...
}   // conn is dropped: its drop sends it back to the pool task
```

The pool task owns the connections. `acquire` sends a request that carries a
reply channel and receives a `PooledConn` by move. `PooledConn.drop` sends
the connection back, and that send never blocks, because the pool's queue
has room for every connection. No locks are used, and nothing is shared
except through the queue.

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
  Channel deadlocks (A waits for B, B waits for A) remain, and the runtime
  can detect the all-parked case.
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

1. Are processes acceptable as the only way to use several cores for the
   foreseeable future (C2), giving up a shared in-process cache?
2. Shared queues `Chan[T]` (Go) or typed per-task mailboxes `Task[M]`
   (Erlang)? Queues make pools and reply channels simple. Mailboxes give
   each task a single address and make "the task is gone" a checked error.
3. Should every spawn go through a scope (Trio-style, recommended), or should
   detached tasks be allowed?
4. Purely cooperative scheduling, or compiler-inserted yield checks at loop
   back-edges?
5. Should a panic stop the worker (recommended) or only the task?
6. For C3: thread-local copy globals (D) or no writes from workers? Atomic
   `str` counts or a transfer step?
7. Mark blocking C calls with a `blocking` marker on `extern`, or with a
   library call that runs a function on the helper pool?
8. When should this happen relative to stage 4 and the native backend? The
   context switch and stacks could be added to `rt.h` as early as the
   library work starts.
