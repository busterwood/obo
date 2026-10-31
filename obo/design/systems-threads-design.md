# Systems threads and synchronization

Status: **design for independent review; no implementation authorized.**
Baseline: released `main` `51ce9e7680a90df9e3d5e38a9b469a460a2ca968`,
obo 0.12, symbol format 6, ABI 1. Date: 2026-10-31.
No compiler, ABI, symbol-format or package version is reserved by this note.

This is the separate thread/synchronization half of the systems library. The
core resource, file, network, reactor and process contracts are in
[systems-stdlib-design.md](systems-stdlib-design.md). This note owns P4/P5 only;
it does not authorize changes to the core phases and does not make arbitrary
shared obo values safe.

## 1. Decision

- **T2 is a prerequisite for P4b/P5.** Context-relative mutable globals and a
  context-relative `platform::Resource` table are required before a public worker
  may touch any `sys*` module. P4a may build infrastructure with no public worker
  API. T1-only workers are compute/native jobs with copied bytes and no `sys*`
  resource use, not a smaller version of general workers.
- **Public P4/P5 do not ship while T2 is missing.** P4a may build the entry,
  arena and TLS machinery behind no public API; P4b and P5 remain blocked.
- **P5 ships byte channels and concrete `AtomicInt`/`AtomicBool` only.**
  `SharedBytes`, `Mutex`, `Condvar`, `RwLock`, `Guard` and `Once` are out of P5;
  they have no cross-worker transfer design and no checked current-worker
  language. They stay internal/deferred.
- **No `Send`/`Sync` markers.** Cross-worker application state is copied bytes and
  explicit scalar metadata. Native shared cells, if ever added, are concrete
  C-owned types with a separate transfer design.
- **Hosted build only.** Threads require host TLS and pthread startup. The
  freestanding static image stays single-threaded and uses processes. Hosted
  TLS/pthread startup is a P0/P4 qualification dependency, not an existing
  capability.

The core verdict still stands: one reactor per supervised process first; a
controlled prefork launcher only after its own fork audit; threads only after
this note's gates.

## 2. Why threads are impossible today

Concurrent execution of two arbitrary obo functions is unsafe for concrete,
file-level reasons:

- `rt_init` maps one arena and one starts bitmap, and `heap_start`, `heap_top`,
  `heap_end`, `free_small[]`, `free_large`, `mstack`, `msp`/`mcap`,
  `tmp_root[]`, `rt_entry_sp`, `env_base`, `stack_base`, `glob_start/glob_end`,
  `obuf`/`olen` and the counters are file-static or global (runtime.c:83–159,
  212–300). Two threads allocating or freeing race on the free lists, the
  traversal worklist and the counters; a second `rt_init` also remaps the
  process-wide globals.
- `rt_str_inc` and `rt_str_dec` are ordinary load/add/store on the high 32 bits
  of a string's size word (runtime.c:417–428). They are correct only for one
  mutator.
- `rt_handle` encodes a heap-relative granule offset and a generation
  (runtime.c:474–493). A `^T` from one arena cannot be validated against
  another, and `handle_target` is not atomic with the subsequent access.
- The compiler emits mutable/owned globals at fixed `0x0C000000` addresses.
  Every thread would share one instance, including its owned strings/records
  and initialization state.
- `rt_entry_sp` is written by every runtime stub (entry.S:83–90); output is a
  single static buffer; `fdstate[1024]` is static (runtime.c:152). A worker
  calling legacy `openFile`/`readFd`/`closeFile`, or `fs`/`io` wrappers over
  them, writes that shared table.
- The core `platform::Resource` identity table is itself a mutable module global.
  T1 forbids a worker closure from touching mutable globals, so a T1 worker
  cannot use the table, and therefore cannot use any `sys*` resource—including
  channels. A claim that T1 workers get per-context TLS and sys* I/O is
  contradictory.
- `OBO_EXIT` and the trap paths use raw `exit(60)`, which in a multithreaded
  process terminates only the caller and can leave the process alive with
  corrupt accounting. Hosted `exit()` is process-wide but runs `atexit`
  handlers from a worker.
- The C seam accepts only scalar i64 arguments and returns i32/i64; it has no
  function-pointer parameter, no callback, no C-created obo entry and no TLS
  guarantee. A stored/captured lambda is call-scoped and cannot be an
  asynchronous entry argument.
- `fork()` in a process with obo state duplicates one owner graph and one set of
  locks with no owner in the child. A prefork launcher must fork before native
  libraries/threads exist and enter a compiler-known process entry.

A Mutex record does not repair any of these. C-only helper threads that never
touch obo memory can eventually service native work, but the native registry,
host TLS, completion queue and wakeup still need a thread-safety review. That is
not full obo threading and is not a P4 deliverable by itself.

## 3. Options and verdict

| Choice | CPU / isolation / memory | Handler ergonomics and HTTP relevance | Testing and migration |
|---|---|---|---|
| (a) Prefork/process workers first | multiple cores, process fault isolation, independent non-atomic heaps; duplicated/COW pages and per-process native resources | blocking handler is simple but consumes a worker; good deployment boundary, shared cache needs IPC | kill/restart and pipe tests are straightforward; general obo fork is unavailable; spawn/exec avoids inherited lock problems |
| (b) One reactor now | many idle connections on one core, one heap, no allocator change | explicit state machines; blocking DNS/file/CPU work stalls all peers; enough to prove transport/timeouts | deterministic fake time/readiness; endpoint API remains useful inside future workers |
| (c) Full threads + arbitrary shared state now | multicore and shared cache, weakest isolation; allocator/RC/handle/compiler memory model all change | synchronous thread-per-connection is familiar but stack/memory limits and cancellation matter | largest race/test surface and migration risk; wrapping pthread_create/mutex is unsound |
| (d) Hybrid: reactor per process, confined workers later | cores via processes first; later bounded compute/native worker pools; ownership stays local | explicit reactor for I/O, messages for CPU work; no protocol dependency | staged gates; stream/deadline/byte-message contracts survive; application chooses a worker deployment |

**Verdict: (d), implemented first as (b) plus externally supervised processes.**
Add spawn/exec supervision in the core P3. Use prefork only after a focused
runtime/hosted fork audit. Full threads (c) are not a first release: they require
every item in §7 and arbitrary shared obo state remains excluded even then.

## 4. T1, T2 and the staged worker boundary

### 4.1 T1 (restricted; no public sys* worker)

T1 means a worker closure with:

- no mutable/owned globals (including module initializer state);
- no access to the `platform::Resource` identity table or any `sys*` shell;
- copied `[]byte` input and an `int` result;
- native C libraries only if they never call obo and are themselves thread-safe.

T1 is useful for compute kernels and native batch jobs. It is **not** a general
worker model: it cannot open a file, watch a socket, use a channel, or call
`platform::initialize`. The compiler does not currently perform the transitive
whole-program closure analysis that T1 needs; a build-time checker that scans
reachable functions and rejects mutable/owned global access, direct or transitive,
is new work. Do not describe T1 as "threads with a few restrictions."

### 4.2 T2 (prerequisite for P4/P5)

T2 makes the runtime context real:

- mutable/owned globals and their initialization are context-relative;
- the identity table, its hint array, free list and generations are
  context-relative (or otherwise synchronized with full identity checks);
- `fdstate`, output buffers, counters, `rt_entry_sp` and the `sysstat` identity
  intern table are context-local;
- the native registry and `Resource.reserved` hint resolve in the current
  context;
- thread-local `RtCtx` is established before any obo code runs.

Only T2 allows a worker to call `platform::source`/`requireKind`, `openFile`/
`readFd` (legacy), `sysfile`, `sysnet`, channels, or any other resource API.
T2 is compiler and runtime work, not a library flag. It must be reviewed with
the exact `.sym`/ABI changes it implies.

### 4.3 Phase split

- **P4a (infrastructure):** hosted pthread/TLS qualification, entry ABI,
  trampoline, per-worker arena/stack/context, signal masks, output sink,
  `exit_group`, join/detach reclamation. No public worker API; tests run native
  fixtures and a private entry.
- **P4b (public confined workers):** T2 context-relative globals/table/fdstate,
  T1 whole-program closure analysis (if retained for compute-only entries), the
  public `systhreads` API, and worker-safe `sys*` use.
- **P5 (synchronization):** byte channels and atomic scalars after P4b, because
  both depend on the context-relative identity table and transfer service.

P4a may proceed as a spike. P4b/P5 are blocked until T2 passes. The core note
does not estimate or authorize any of these phases.

## 5. Worker model and API

Each native typed shell owns a concrete `res: platform::Resource` field. There
is no type-erasing alias and no `^Worker -> ^Resource` conversion. `source` and
`requireKind` from the core note are the only bridge to reactor/watch identities.

```obo
module systhreads
import "sysclock.obx" as clock
import "sysplatform.obx" as platform
pub type Worker = ref record { res: platform::Resource }
pub type WorkerEntry = ref record { slot: int }
pub type StartOptions = ref record {
    stackBytes: int; heapBytes: int; name: string
    tickets: []?platform::Transfer
}
pub type JoinStatus = ref record { code: int; cancelled: bool }
pub fn entry(name: string): Result[WorkerEntry]
pub fn start(entry: &WorkerEntry, input: []byte,
             mut options: &StartOptions): Result[Worker]
pub fn takeTicket(index: int): Result[platform::Transfer]
pub fn cancel(w: &Worker): Result[bool]
pub fn join(w: &Worker, until: &clock::Deadline): Result[JoinStatus]
pub fn detach(w: &Worker): Result[bool]
pub fn close(w: &Worker): Result[bool]
pub fn reapWorkers(): Result[int]
```

`entry(name)` resolves a **build-registered static entry**, not a code address
or lambda. The link manifest lists each entry name, its fixed obo signature
`fn worker(input: []byte): int`, and a generated table slot. `start` revalidates
the slot against that table; an unknown name or forged slot is NotFound/
InvalidArgument, and there is no way to construct an entry from an int.
`WorkerEntry` is a build-table descriptor, not a native owner; dropping it is
harmless and it has no close obligation. The entry function runs with its own arena, stack, globals
and native context only when T2 holds; otherwise `start` rejects any entry whose
closure would touch a resource or a mutable/owned global.

`start` **consumes** `input` and every non-nil `options.tickets` entry on every
path (it takes them, leaving nil). It copies bytes into a bounded native staging
buffer; the worker allocates its own `[]byte` in its own arena and frees the
staging buffer. Failure frees the input, releases unconsumed tickets and all
partially created native state. Tickets are single-use and typed; a caller cannot
forge a native handle from an int. The worker obtains a launch ticket with
`takeTicket(index)` (valid only inside a worker, index checked against the launch
packet); the ticket is consumed by the typed importer. `takeTicket` is the only
way a ticket enters the worker; it is never encoded in `input` bytes or returned
as a raw int.

Worker lifecycle: Starting -> Running -> Finished -> Joined, or Running/Finished
-> Detached -> Reaped. Failed startup returns no worker and releases stack, arena
and mailbox. Join timeout leaves the Worker joinable; successful join/detach is
exactly once; repeated join returns a cached JoinStatus or Closed as fixed at
implementation. `close` on a Finished/Joined worker releases the shell; on a
Running worker it transfers the join obligation to the supervisor and does not
kill or block. `detach` does the same explicitly. `reapWorkers` is the
nonblocking supervisor turn that joins finished workers and releases their
native state; applications without a reactor must call it or `join` directly.
Process shutdown joins or reaps every worker before the main arena is destroyed.

`cancel` sets a C atomic flag and wakes waits; the worker checks it at library
blocking points or explicitly during computation. A blocked native call (DNS, a
blocking file read, a third-party library) may not observe cancellation; `join`
reports TimedOut, not a lie. No `pthread_cancel`.

**`exit()` inside a worker:** the obo `exit(code)` builtin is **process-exit**
via `exit_group` (or hosted process termination), exactly as on the main thread.
It runs no defers, terminates all workers and does not fabricate a join. A worker
that wants a normal per-worker result returns from its entry; `exit` is not a
worker-finish primitive. Both normal return and worker `exit` need tests.

## 6. Worker entry ABI

The only C-to-obo transition is a runtime-owned bootstrap calling one
build-validated entry label. `pthread_create` receives the address of a runtime
C function, `rt_worker_bootstrap`, never an obo function pointer, a lambda, a
captured environment or an application-provided C callback. The bootstrap is
part of the trusted runtime and is reviewed with the ABI.

The entry calling convention is the existing obo stack convention, not a new
FFI. The worker has a separate 16-byte-aligned obo stack. With one owned `[]byte`
argument, the C bootstrap has already published the worker's `RtCtx` in TLS and
then calls an assembly trampoline equivalent to:

```asm
; rdi = WorkerStart* (runtime-owned); returns the entry's int in rax
rt_worker_trampoline:
    push %rbp
    mov  %rsp, %rbp                 ; save the C/pthread stack in the frame word
    mov  WorkerStart.obo_stack_top(%rdi), %rsp
    sub  $8, %rsp                   ; alignment pad above the argument
    push WorkerStart.input_array(%rdi) ; one obo argument, in the worker arena
    call *WorkerStart.code(%rdi)    ; entry sees rsp % 16 == 8
    mov  %rbp, %rsp                 ; back to the C trampoline frame
    pop  %rbp
    ret                             ; C bootstrap calls rt_worker_finish(rax)
```

`WorkerStart.code` is the table slot for a statically named `fn worker(input:
[]byte): int`. The entry prologue expects the argument at `[rbp+16]`, exactly as
`fnBody` lays out one parameter. The trampoline does **not** keep the C stack
pointer or the context in `r12`/`r13`: obo uses those registers as local homes
and does not promise a callee-saved contract for them. It relies only on `%rbp`,
which the obo prologue/epilogue preserves as its frame pointer. The runtime
allocates the input array header and bytes in the worker arena before the call;
the callee owns and frees the array on return. The entry has no receiver, no
borrowed arguments, no captured values and no `Result` return. A `Result` would
have to be resolved into an int status and copied bytes before return.

## 7. Runtime and compiler work

| Work | Exact obligation and risk |
|---|---|
| Entry ABI and validation | A runtime-owned C/assembly bootstrap calls only a build-registered entry label. `pthread_create` receives `rt_worker_bootstrap`, not an obo pointer. The build table records name, signature and code slot; unknown/duplicate names fail the link. No general C-to-obo callback, no lambda/capture, no app C callback. Raw `clone` is rejected for hosted builds: it does not set up glibc TLS, loader state or pthread bookkeeping. |
| Hosted TLS qualification | **Not an existing capability.** P0/P4a must build a hosted C fixture that creates a pthread, proves distinct per-thread `errno`/TLS, and returns scalar results through the cinterop seam; it must run and be recorded before P4a is accepted. `pthread_create` linking alone is not qualification. P4 remains blocked until this passes. |
| Entry execution context | Split `rt_init` into process initialization, worker initialization and worker teardown. A worker must not rerun process initializers, `atexit`, argv parsing or module side effects. It gets an immutable argv/env snapshot, its own context and its own arena; it must not call process `rt_finish`. Register active workers in a native list for diagnostics/shutdown. |
| Allocation / arena | Each `RtCtx` owns `heap_start/top/end`, starts bitmap, free lists, `mstack/msp/mcap`, temporary roots and counters. Worker arenas are separate `mmap` reservations of `options.heapBytes` (default 64 MiB, checked minimum 64 KiB, aggregate cap), not 4 GiB each. The starts bitmap and worklist scale with the worker arena. The joiner destroys the arena only after the thread has terminated and no native operation can refer to it. A shared allocator would require locks/atomics on every allocate/free path, reentrancy review and scalable worklists; one malloc lock is insufficient. |
| T2 globals and identity table | Current mutable/owned globals and the `platform::Resource` table are fixed-address or module-global. T2 makes both context-relative: a fixed TLS offset holds the context's globals and table base, and per-context initialization/destruction follows dependency order without rerunning external side effects. The `Resource.reserved` hint resolves against the current context's table and is still fully rechecked. T1-only code may not touch either. This is major ABI/codegen work, not a library flag. |
| Refcounts | Confinement keeps the existing non-atomic string RC correct only if no heap string/array/record is shared, including globals and library caches. Copy bytes on transfer; never transfer an RC pointer. If shared immutable strings are ever required, use atomic inc/dec with release/acquire destruction and an overflow policy, plus arena lifetime protection; atomic RC still does not make mutable records safe. |
| Handles | The current `^T` is a 32-bit heap-granule offset plus a 32-bit generation (runtime.c:474–493); it has no context tag and assumes one heap. No `^T` crosses a worker boundary. Each context decodes against its own arena. Adding an arena tag for defense in depth needs a reviewed handle-ABI change; do not silently steal generation bits. Check/use must not race a free even within one context. |
| Compiler escape/effect boundary | Validate the static entry signature; reject borrowed values, `^T`, interfaces, function values, optionals of owned values and any thread-foreign type at the entry and channel boundaries. Introduce backend-owned worker-entry descriptors without changing the scalar C interop contract. Native effects remain conservative. The T1 transitive global/table closure analysis does not exist today and is new whole-program work. `.sym`/ABI consequences and the 2,500,000-byte compiler budget need independent review. |
| TLS and errno | Hosted libc requires real per-thread TLS. Use pthread-created threads after the qualification fixture passes. Store `RtCtx *` in a `__thread` variable (or a reviewed fixed TLS slot) and load it at every runtime entry. C adapters capture `errno` immediately after each libc call into a scalar before any other call; raw syscalls already return negative errno. Never expose TLS addresses. `getaddrinfo`, `strerror`, `locale` and third-party libraries must be audited for thread safety; an unsafe library is confined to one worker or protected by a C lock. |
| `fdstate` and legacy fd builtins | `fdstate[1024]` is static. T2 moves it into `RtCtx` (or replaces it with the context-local registry). Until that lands, the worker-safe closure excludes `openFile`/`readFd`/`writeFd`/`closeFile`/`readDirFd` and every `fs`/`io` wrapper over them. A two-worker legacy-fd race test must pass or the build must reject the entry. |
| Signals | Signals and dispositions are process-wide. Block service signals before creating workers; every worker inherits the mask; one designated signalfd/pidfd consumer drains them. No obo code runs in an asynchronous handler. `SIGPIPE` follows the core default (ignored at systems initialization) or the explicit keep-old option; socket sends use `MSG_NOSIGNAL`. `SIGSEGV`/`SIGBUS`/`SIGFPE`/`SIGILL` remain fatal. Do not install a `SIGCHLD` handler that calls obo. |
| Output, registry and fds | Output buffers and `rt_entry_sp` become context-local. Each worker flushes its own buffer through a C mutex around `write(2)`; no obo code runs while holding it. File descriptors are process-wide: each context owns its own descriptors; never close a shared duplicate. The native registry is per-context; a resource transfer ticket invalidates the source shell and installs a new shell in the destination, with kind/generation rechecked. Standard streams are duplicated per worker (stdin sharing is explicitly unsupported unless the application coordinates it). |
| Traps and termination | An obo trap or managed OOM is **process-fatal** via `exit_group` (or hosted process abort) with a minimal diagnostic. It must not use thread-local `exit(60)` and must not fabricate a successful join or run partial defers. Normal worker return runs its own defers and frees its locals, then tears down only that worker. Split `OBO_EXIT` into worker-finish versus process-exit; after joining/reaping workers, main's normal finish also uses `exit_group` so no worker outlives the process image. Catchable thread traps are a later design. |
| Reclamation | Every worker pthread stays **joinable**; `pthread_detach` is not used. `detach`/`close` on Running transfers the join obligation to the supervisor; `reapWorkers` calls `pthread_join` after the completion event and only then frees arena, stack and mailbox. A completion event alone is not enough: the C bootstrap may still be using the stack, so the supervisor must join the thread before release. A stress test covers detach, close-on-Running, shutdown and repeated reap. |
| Memory model | Obo accesses are worker-confined by construction. Publication of native byte messages and completion flags uses C11 atomics with release/acquire; join observes prior worker completion. All shared native state uses C atomics/locks. Do not advertise a data-race-free shared-obo memory model until the language can enforce one. |

StackBytes: validate a page multiple, guard pages, a minimum sufficient for the
bootstrap and known runtime stack use, and a configured process total; default
1 MiB is a starting measurement, not a proven bound. No growable stacks. Overflow
is fatal with guard detection. HeapBytes is carved from an aggregate managed
quota; native messages, stacks and metadata have separate caps/counters. Thread
creation `EAGAIN`/`ENOMEM` is a Result; managed OOM/traps follow the fatal model.
The compiler may not exceed the 2,500,000-byte budget to land this phase; if it
cannot fit, stop and revise scope.

## 8. What may be shared

| Value / facility | Across confined workers | Reason |
|---|---|---|
| `int`, `byte`, `bool`, `float` by value; serialized bytes | copy | No ownership or hidden header. |
| Immutable rodata string literals and scalar `const`s | read-only share | `rt_str_inc/dec` ignores non-heap addresses; no mutable header. |
| Heap `string`, `[]T`, record/closed value, `Result`, `?T`, interface, function value | **no** | Non-atomic RC, owned graph, call-scoped captures or vtable/box ownership. |
| `^T` handle | **no** | Heap-relative token; wrong arena and check/use race. |
| `File`, `Directory`, `Stream`, `Connection`, `Mapping`, `Worker` shell | **no** | Registry identity and native lifetime belong to one context; use an explicit transfer ticket only. |
| Mutable/owned global, module initializer state | **no** under T1; context-relative under T2 | Fixed address and one initialization without T2. |
| Native `AtomicInt`/`AtomicBool`, channel endpoints | yes, as concrete native types after P4b | C-owned state with explicit synchronization; not an obo object. |
| `SharedBytes`/`Mutex`/`Condvar`/`RwLock`/`Once` | **not in P5** | No cross-worker cell/lock transfer design; deferred. |

The safe rule is: **copy scalars and bytes; never copy an obo ownership token,
a `^T`, or a borrowed reference.** A native resource handoff needs its own
old-owner-invalidation protocol; sending a shell or token is not a handoff.

## 9. Synchronization: P5 scope

P5 ships **bounded byte channels** and **concrete `AtomicInt`/`AtomicBool`**
only. Both are C-owned native objects in the context registry. They are created
by the supervisor, exported as `platform::Transfer` tickets before `start`, and
imported by the worker with `takeTicket` plus a typed importer. A ticket is
single-use; the source endpoint remains usable after export.

```obo
module syssync
import "sysclock.obx" as clock
import "sysplatform.obx" as platform
pub type Sender = ref record { res: platform::Resource }
pub type Receiver = ref record { res: platform::Resource }
pub type Channel = ref record { sender: ?Sender; receiver: ?Receiver }
pub type AtomicInt = ref record { res: platform::Resource }
pub type AtomicBool = ref record { res: platform::Resource }
pub type Exchange = ref record { changed: bool; previous: int }
pub fn bytesChannel(maxMessages: int, maxBytes: int): Result[Channel]
pub fn send(s: &Sender, data: &[]byte, until: &clock::Deadline): Result[bool]
pub fn receive(r: &Receiver, until: &clock::Deadline): Result[?[]byte]
pub fn exportSender(s: &Sender): Result[platform::Transfer]
pub fn exportReceiver(r: &Receiver): Result[platform::Transfer]
pub fn importSender(t: platform::Transfer): Result[Sender]
pub fn importReceiver(t: platform::Transfer): Result<Receiver>
pub fn closeSender(s: &Sender): Result[bool]
pub fn closeReceiver(r: &Receiver): Result[bool]
pub fn atomicInt(value: int): Result[AtomicInt]
pub fn atomicBool(value: bool): Result<AtomicBool>
pub fn loadInt(a: &AtomicInt): Result[int]
pub fn storeInt(a: &AtomicInt, value: int): Result[bool]
pub fn compareExchangeInt(a: &AtomicInt, expected: int,
                          value: int): Result[Exchange]
pub fn loadBool(a: &AtomicBool): Result[bool]
pub fn storeBool(a: &AtomicBool, value: bool): Result[bool]
pub fn exportAtomic(a: &AtomicInt): Result[platform::Transfer]
pub fn importAtomic(t: platform::Transfer): Result[AtomicInt]
pub fn exportAtomicBool(a: &AtomicBool): Result[platform::Transfer]
pub fn importAtomicBool(t: platform::Transfer): Result<AtomicBool>
```

`send` copies all bytes before publication; a full queue is WouldBlock for an
expired/zero wait and TimedOut at a finite later deadline. Failure publishes
nothing and the caller retains its array. `receive` copies into the receiving
arena, returning nil only after all senders close and queued messages drain.
Receiver close rejects new sends and frees queued native bytes; blocked calls
wake with Closed/Cancelled. Bound both message count and aggregate byte count.
No arbitrary `[]T` channel. Channels intended across the boundary are created
before `start`; a worker-created endpoint cannot be returned through
`JoinStatus` (only an int status) in v1.

Atomic operations are sequentially consistent by default. `compareExchangeInt`
returns whether it changed and the previous value; no address casts, no
unchecked pointer arithmetic. `AtomicBool` has no add/exchange variants in v1.

### 9.1 Explicitly deferred: shared byte cells and locks

`SharedBytes`, `Mutex`, `Condvar`, `RwLock`, `Guard` and `Once` are **not public
P5 APIs**. There is no `exportSharedBytes`/`exportMutex`/`exportOnce`, so two
workers calling `sharedBytes` would create two independent cells, not one shared
cell. A checked "current worker" guard language would imply cross-worker
ownership that does not exist. These types remain internal C mechanisms (for
example a runtime queue lock) and are not exposed to applications.

If a real consumer later requires them, a separate design must add:

- a single-use transfer ticket for the shared cell and for each lock endpoint;
- a cell kind/lock-kind registry check and a current-context check for every
  guard operation;
- explicit close/Busy rules for holders and waiters;
- a predicate-loop contract without callbacks into obo;
- a proof that replacing a public `res` field cannot alias a cell of the wrong
  kind or bypass the generation check.

Until that design exists, no public `Mutex[T]`, `Send` or `Sync` is proposed.

## 10. Testing and gates

- **Hosted TLS/pthread fixture:** a C-only hosted fixture creates two pthreads,
  forces distinct `errno` values, reads them back independently and returns scalar
  results through the cinterop seam. Record the build/link/run command and output;
  P4a cannot be accepted without it. Do not claim pthread-capable linking alone
  proves TLS.
- **Entry ABI:** a link-time table test rejects unknown/duplicate entry names and
  a non-`fn worker(input: []byte): int` signature. Disassembly or a marker test
  proves `pthread_create` receives `rt_worker_bootstrap`, not a user pointer. The
  worker sees `rsp % 16 == 8` at entry and the input array at `[rbp+16]`; after
  normal return the joiner observes the int status, the entry's defers ran, and
  the arena was freed once.
- **T2 context isolation:** two workers allocate/free thousands of objects and
  use `Resource.reserved` hints while the main context does the same; no
  free-list, counter, starts-bitmap, identity-table or hint corruption. A
  cross-context `^T`/shell must be rejected by the compiler or fail the full
  identity check.
- **T1 closure:** a negative test proves a T1 entry that transitively touches a
  mutable global, the identity table, `openFile`, `sysfile`, `sysnet` or a channel
  is rejected. A positive test proves a pure compute entry runs.
- **Legacy fd race:** two workers call legacy `openFile`/`readFd`/`closeFile`
  (and `fs`/`io` wrappers) concurrently. Either `fdstate` is context-local and
  the test passes, or the build rejects the worker before it runs. No shared-table
  race is accepted.
- **Reclamation:** detach, close-on-Running, supervisor `reapWorkers`, shutdown
  and repeated reap; assert the arena/stack is freed exactly once, only after
  `pthread_join`, and no completion event is observed after release.
- **`exit` semantics:** normal worker return finishes only the worker and joins
  normally; `exit(code)` in a worker terminates the whole process via
  `exit_group`, runs no defers and does not report a successful join.
- **Channels/atomics:** copy-on-send, full/empty boundaries, sender close,
  receiver close, cancellation/deadline, ticket single-use, double import,
  wrong-kind import, and two-worker producer/consumer stress.
- **Sanitizers:** TSAN on native adapters, registry, queues and C bootstrap;
  generated obo code is not TSAN-instrumented, so combine TSAN with confinement
  and forced-schedule tests. ASAN/UBSAN run in a separate C harness if the
  fixed-address image conflicts with the sanitizer mapping.
- **Signals:** every worker inherits the blocked service mask; one signalfd
  consumer receives a coalesced event; `SIGPIPE` follows the core default and the
  keep-old option is tested.

## 11. Packaging and compatibility

Proposed modules are `obo/lib/sys/systhreads.obx` and `obo/lib/sys/syssync.obx`,
with adapters/tests under `obo/experimental/systems/` and `obo/tests/systems/`.
They follow the core note's flat unique-basename and `.sym` rules. Thread support
is an experimental hosted-only package until the TLS/pthread fixture and T2
review pass; freestanding programs never import these modules. No compiler, ABI,
symbol-format or package version is reserved by this note. New runtime/ABI work
uses the existing exact-match version policy when implemented.

## 12. Phases and effort

Estimates are engineer-weeks including tests/docs and one review cycle, not a
schedule commitment.

| Phase | Scope / exit | Dependencies | Rough effort |
|---|---|---|---|
| P4a: thread infrastructure | hosted TLS/pthread qualification, entry ABI/trampoline, per-worker arena/stack/context, signal masks, output sink, `exit_group`, join/reap; no public worker API | P0 cinterop boundary; hosted qualification fixture; runtime/compiler review | 6–9 |
| P4b: public confined workers | T2 context-relative globals/table/fdstate, T1 whole-program closure analysis, public `systhreads`, worker-safe `sys*` | P4a + T2 proof + compiler budget gate | 6–11 |
| P5: synchronization | byte channels, `AtomicInt`/`AtomicBool`, transfer tickets; no SharedBytes/Mutex/Once | P4b + context-local identity/registry | 3–6 |

**P4 total 12–20+ engineer-weeks**, with T2 as the largest hidden cost; this
supersedes the earlier 8–14+ estimate. A smaller T1-only compute experiment may
be run as a spike, but it is not counted as P4 and does not authorize public
`sys*` workers. P5 is blocked on P4b. The compiler's 2,500,000-byte live budget
is a hard gate; if T2 or T1 cannot fit, stop and revise scope.

## 13. Rejected alternatives

1. **“Just pthread_create plus Mutex”:** allocator, RC, globals, handles, TLS,
   entry ABI and process-exit semantics remain unsafe.
2. **T1 as general workers:** T1 cannot touch the identity table or any `sys*`
   resource, including channels; claiming otherwise contradicts T2.
3. **`pthread_detach` for workers:** there is no join point after the C bootstrap
   may still use the stack, so arena/stack reclamation is unproven. Keep every
   worker joinable and let the supervisor join it.
4. **Goroutine-style port / implicit thread spawning:** requires scheduler,
   growable stacks, blocking-call interception and different cancellation/lifetime
   rules. A resolver or buffered writer must not create hidden execution contexts.
5. **Generic `Mutex[T]`/`SharedBytes` in P5 without transfer:** invents shared
   ownership and two independent cells. Defer until a ticket/guard design exists.
6. **`Send`/`Sync` markers without semantics:** structural classification,
   global/effect tracking, exclusive access and atomic lifetime machinery are a
   language design project, not a library wrapper.

## 14. Open choices

| Choice | Recommendation | Consequence of choosing otherwise |
|---|---|---|
| T2 priority | fund T2 before any public P4/P5; keep T1 only as a compute spike | T1-only "threads" cannot use files, sockets or channels and would force an API rewrite later |
| Hosted-only threads | yes; freestanding stays single-threaded and uses processes | freestanding pthread/TLS support needs loader/TLS work outside this design |
| First sync scope | byte channels + `AtomicInt`/`AtomicBool` only | `SharedBytes`/`Mutex` needs a separate transfer and guard design |
| Reclamation | joinable worker + supervisor join after completion event; no `pthread_detach` | a detached thread has no safe point to free its arena/stack |
| Public worker API timing | P4a spike first; P4b after T2; P5 after P4b | exposing workers before T2 creates a false sys* story and a likely rewrite |
| Shared application state | copied bytes/messages only | arbitrary shared obo values require a language memory model, not a C lock |

No decision about HTTP/1.1/2/3, TLS provider or compression is made here.
