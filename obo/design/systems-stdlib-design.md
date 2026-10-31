# Systems standard library

Status: **design for independent review; no implementation authorized.**
Baseline: released `main` `34b798bc4c76cf41362d3b374c13b80e7b43f73b`,
obo 0.11, symbol format 6, ABI 1. Date: 2026-10-31.
No compiler, ABI, symbol-format or package version is reserved by this note.

## 1. Decision and scope

Build a general-purpose Linux systems layer around **resources, bounded byte
operations, deadlines and explicit lifecycle**, rather than exposing a syscall
catalog. Start with files, directory-relative operations, clocks and randomness;
then add nonblocking networking and a single-threaded readiness reactor; then
process supervision. Recommend **one reactor per worker process**, initially
launched by an external supervisor, subsequently by the process module. Native
obo threads are later runtime/compiler work. Public synchronization of arbitrary
obo objects is not sound today and is not part of the first thread release.

This is foundation work for command-line programs, services and native-library
adapters. The HTTP readiness assessment in §10 describes dependencies only.
Request parsing, framing, routing, HTTP/1.1, HTTP/2 and HTTP/3 are outside this
design. TLS and compression are separately maintained transport/codec adapters,
not implementations of those protocols here.

The first target is **Linux x86-64**. Public contracts prefer Unix concepts which
can later have BSD/macOS implementations. That is a portability boundary, not a
claim that obo's code generator, startup or library already runs there. Linux-only
capabilities are queried explicitly and can return `Unsupported`; a fallback must
preserve the promised semantics or fail. Do not silently weaken path confinement,
close-on-exec or timeout behavior to approximate POSIX compatibility.

### Reading guide

- §2: baseline inventory and evidence.
- §3–4: information hiding, dependency graph, errors and resource invariants.
- §5–8: implementable module contracts and 0.11-shaped API headings.
- §9: thread/runtime work and concurrency decision.
- §10–13: readiness, tests, packaging and delivery phases.
- §14–15: rejected alternatives and real product decisions.

API blocks are **proposed declarations**, using ordinary 0.11 types, not complete
modules available for import. Type declarations, record/variant construction,
`match`, `defer`, `try` and ownership patterns in the examples follow 0.11
syntax. Bodyless `pub fn` headings are signature notation: 0.11 has no
declaration-only function form, so each heading becomes a definition in an
implementation; an API block as written is not a compilable module. There are no
invented generics, destructors, async keywords, borrowed returns, `Send`/`Sync`
markers or thread syntax. `Result[T]` is the existing builtin. The thread
headings are explicitly blocked proposals, not evidence of thread support.

## 2. Current state: what actually exists

All source/line references below are relative to the repository root and pinned
to the baseline above. In-tree links aid navigation; line ranges identify the
evidence even when subsequent implementation moves it. No repository
`AGENTS.md` exists at this baseline (checked the worktree and its ancestor
directories); this note follows the checked-in `LANGUAGE.md`, the design-note
conventions, and the cinterop skill instead. The HTML escaping module is present
at this baseline; it is unrelated to the systems layer.

| Area | Existing surface and evidence | Important limits / missing foundation |
|---|---|---|
| Whole files | `readFile`, `writeFile`; [runtime.c](../runtime/runtime.c):646–679 | Optional/bool errors lose errno; readFile seeks to size and is unsuitable for pipes; no public seek, truncate, durable replace or permission API. |
| Incremental files | `openFile("r"/"w"/"a")`, `readFd`, `writeFd`, `closeFile`; runtime.c:767–833; [LANGUAGE.md](../LANGUAGE.md):1943–1965 | Raw copied fd integers; open lacks CLOEXEC; fd-state table covers only numbers below 1024; number reuse is not ownership. Reads/writes retry EINTR internally; writes may succeed partially. |
| Buffered I/O | [io.obx](../lib/io.obx):13–57,75–241: Reader/Writer, fill, readLine, readN, readByte, takeChunk, flush, pump, count, write/writeBytes/writeByte | Reader/Writer borrow an int fd and own buffers only. Chunk size clamps to 1..1 MiB; line/readN totals are unbounded; readN is not exact-read. Flush retains unwritten bytes, but writeBytes can consume a prefix before failure without returning its count. No readiness/deadline/vectored interface. |
| Directories | [fs.obx](../lib/fs.obx):109–219: open/next/close, copied DirEntry names; runtime.c:847–871 | getdents64 layout decoded in obo; skips `.`/`..`; unsorted; regular-file open only fails on next. DT_UNKNOWN fallback lstats a reconstructed path and can race. No directory-relative mutation. |
| Metadata | fs.obx:56–106,221–269; runtime.c:872–930: stat/lstat, 14-number stat array, readlink, kind predicates | Linux values are exposed; atime/ctime have seconds only; no fstat, stat-at, chmod, mkdir, rename, hard link, unlink, filesystem sync, mapping interface. mmap exists only inside the allocator. |
| Environment / arguments | [env.obx](../lib/env.obx):12–50: get/has/value/getOr; runtime.c:1002–1042; [cli.obx](../lib/cli.obx):1–65,195–411: argv and option parser | getenv searches initial envp, copies bytes, conflates invalid name and absent. No snapshot enumeration or mutation. cli preserves eza-specific grammar/messages; it is not a process launcher. |
| Terminal | io.obx:59–73; runtime.c:936–981: isatty/terminalWidth | No termios mode ownership, restoration, resize events or checked terminal errors. |
| Sleep / time | `sleepMs`; runtime.c:987–1000. [time.obx](../lib/time.obx):1–24,69–128: UTC civil conversion, minute-precision long-ISO/ISO display | nanosleep retries EINTR with remainder, accepts up to 2,147,483,647 ms. No clock read, monotonic deadline, timer queue, RFC3339 parser or timezone support. Existing `formatIso` is not RFC3339. |
| Mapping | private `mmap` only inside `rt_init` (runtime.c:28–41,212–300) | No public anonymous/file mapping, protection, flush or copy interface; no mapping lifetime separate from the allocator. |
| Network / readiness | absent | No socket, connect/accept, bind, send/receive, poll/epoll/eventfd, nonblocking mode or DNS. There is no way to wait for readiness without blocking in `readFd`/`sleepMs`. |
| Process / signals | absent | No spawn/exec, wait/reap, pipe creation, process group, pidfd, signal set, signalfd or SIGCHLD policy. `exit` skips defers and is a raw process exit. |
| Concurrency | absent | No threads, atomics, mutex, condvar, channels, worker entry ABI or reactor. The runtime and language are single-threaded by construction (see §9.2). |
| Strings / paths | [strings.obx](../lib/strings.obx):32–74,163–315,831–1195; [paths.obx](../lib/paths.obx):1–18,26–133 | Byte comparisons, split/join/trim, UTF-8 validation/lossy conversion, partial simple Unicode folding, display width, natural order and eza quoting. Paths are lexical join/parent/component/extension, not filesystem containment, normalization against symlinks or a sandbox. |
| Sort / format / errors | [sort.obx](../lib/sort.obx):1–26,124–198; [fmt.obx](../lib/fmt.obx):13–34,36–139; [errors.obx](../lib/errors.obx):11–59,120–123 | Stable index sorting and integer sort; digits/padding and eza modes; small English errno table plus eza exit constants. No generic containers, error categories, structured logging or duration formatting. These are useful existing modules, not replacements for OS resources. |
| HTML | [html.obx](../lib/html.obx):1–80: `Html`, `escapeText`, `escapeAttr`, `validateUrl`, `raw`, `join`; the exact eleven-module set is pinned in [imports.py](../tests/imports.py):169–185 | Present at this baseline, but it is an escaping/serialization helper with public fields and convention-level trust. It supplies no sockets, timers, request parser, framing or concurrency. |
| Native interop | [cinterop README](../experimental/cinterop/README.md):13–21,57–84,277–298; [design](native-c-interop-design.md) §8; [skill](../experimental/cinterop/skill/SKILL.md):22–61 | Trusted scalar i64 input, i32/i64 result, checked C tokens, bounded byte loops, explicit close. Internal/experimental, no callbacks into obo, no pointers or structs by value, no production TLS/IFUNC guarantee, no automatic foreign finalizer. zlib is a proof fixture, not a stable streaming codec. |

### 2.1 Actual builtin and syscall boundary

The builtin name universe is in [obo.obo](../src/obo.obo):2358 and the dispatch
in :6559 onward; ownership/Result constructs have additional special parsing.
System-facing builtins are the file, directory, metadata, terminal, environment,
args, sleep, output and exit operations above. Float and string helpers do not
add OS capabilities. There is no arbitrary public `syscall`, socket, poll,
clock_gettime, getrandom, fork, exec, wait, signal, thread or atomic builtin.

Actual syscall sites in runtime.c:14–52,646–1035 use Linux x86-64 read (0),
write (1), open (2), close (3), lseek (8, private to readFile), mmap (9, private
heap reservation), ioctl (16), nanosleep (35), exit (60), readlink (89),
getdents64 (217) and newfstatat (262). getenv/args read the saved startup stack;
they are not syscalls. The existence of private lseek/mmap is not a public API.

[entry.S](../runtime/entry.S):19–90 defines slots 0..58 in the normal runtime;
**slot 59 is `rt_set_host_exit` only in the hosted variant** (the normal table
has 59 slots, the hosted table 60). Compiler constants are obo.obo:1987–2055.
Do not reuse 59, shift existing slots, or copy obsolete 40-slot counts from
historical interop prose. Runtime stubs also write the global `rt_entry_sp`
(entry.S:83–90).

### 2.2 Language and runtime constraints

- Owned records/arrays move; strings share non-atomic reference counts.
  References cannot be stored or returned, and several mutable borrows may
  alias: LANGUAGE.md:67–155,419–431. Group borrowing prevents use-after-free;
  it does not establish cross-thread exclusion.
- `^T` is a checked **non-owning** link, not a resource owner or a thread-safe
  weak pointer. runtime.c:470–493 computes heap-relative generation tokens;
  validation is not atomic with a subsequent access.
- `try` propagates the same error cell and runs defers; main cannot use try.
  Deferred cleanup cannot propagate its error. `exit` runs no defers:
  LANGUAGE.md:1670–1847,1962–1965. Trap statuses and Result error codes are
  different namespaces (:2159–2178).
- Exported record fields are visible/writable; private types cannot occur in
  exported layouts (LANGUAGE.md:1305–1327; native interop design §8.5). Merely
  naming a record `File` with a public `tok` does not hide a token.
- Native calls carry `freesAll` and `writesHandles` (obo.obo:6486–6556).
  They cannot implement the current local/pure interface effect tiers, and
  cannot be invoked through pure lambda bodies. Interior references into a
  connection array cannot be passed across such a call. Store/move a connection
  into a whole local first; reborrow state after the call. No proposal here
  quietly marks C calls pure to make a Reader interface compile.
- Function values are second-class; Stage B captures borrow a creator's stack
  for the duration of a call (LANGUAGE.md:1034–1115,1148–1225). Neither a lambda
  cell nor a captured reference can be an asynchronous thread entry argument.
- The allocator, free lists, free traversal stack, counters, temporary roots,
  fd tracker and output buffer are global (runtime.c:83–159,330–468).
  `rt_str_inc/dec` use ordinary loads/stores (:417–428). `rt_collect` returns
  immediately (:309–321); collector comments at the file head are historical.
- [README.md](../../README.md):133–179: 4 GiB default arena, 16 GiB ceiling,
  one-object payload at most 4 GiB minus 32 bytes, separate metadata and native
  memory. mmap reserves virtual address space, not RAM. Free lists retain
  memory; `OBO_STATS` is not an RSS meter.
- Managed allocation failure currently terminates with exit 2 (runtime.c:330–350).
  Even `err` and `ok` allocate cells. We can return Result for OS/native ENOMEM,
  validation and configured capacity exhaustion, **not guarantee recoverable
  managed-heap exhaustion without separate runtime allocation work**. No normal
  I/O error or remote input should intentionally use a runtime panic.
- The compiler live-memory budget is **2,500,000 bytes**, not the application
  heap limit (README.md:73–94). All initial APIs are source plus trusted C shims
  using the existing experimental seam. Compiler features are separately gated.

## 3. Information hiding and module boundaries

Following Parnas, boundaries follow decisions likely to change, not the order
of Linux manual pages. A file is an owned open resource with useful operations;
it is not an exported `open(2)` number. A reactor manages subscriptions and
deadlines; it is not an epoll_event struct. A `util` grab-bag and a giant `os`
module obscure these decisions and are rejected.

### 3.1 Secrets, stable interfaces, local changes

Names in this table are logical names; unique physical module names are in §12.

| Module | Secret (design decision hidden) | Narrow surviving interface | Change kept local |
|---|---|---|---|
| sys/errors | OS error-number mapping and diagnostic wording | named categories/predicates over Result.code | Linux errno versus BSD errno, new kernel errors |
| sys/platform (support) | syscall/libc choice, native registries, allocation quotas, ABI conversion | resource operations on managed identities, capability queries, diagnostics | libc versus raw syscall implementation; registry growth; fake backend |
| os/file | open policy, offsets, durability and replace transaction | File, open/openAt, read/write, seek, sync, replace | openat2 capability check, fsync ordering, temporary-file strategy |
| os/dir | directory cursor and anchored-name traversal | Directory, Iterator, next, child operations | getdents versus readdir, buffer sizing, unknown d_type |
| os/stat | metadata layout, unavailable fields, identity representation | Metadata snapshot and identity equality | statx versus fstatat, timestamp precision, unsigned IDs |
| os/mapping | mapped address/protection/page alignment | Mapping, bounded copy, protect, flush, close | mmap layout, page size, mprotect restrictions |
| time/clock | clock source and unit conversion | Instant, Duration, Deadline, realtimeNs/monotonic | clock_gettime versus vDSO; backend fake clock |
| time/calendar | civil calendar and accepted text syntax | parse/format RFC3339, UTC conversion | parser implementation or formatting optimization |
| random | entropy provider and partial-fill mechanics | fillSome, fillUntil, bytes with explicit limit | getrandom availability and chunk size |
| io/stream | endpoint kind and partial-transfer normalization | Stream, readSome/writeSome, half-close, explicit close | socket/file/pipe dispatch, native buffering |
| io/buffer | capacity, compaction, byte cursor and pending progress | feed/find/copy/consume, Reader/Writer steps | ring versus linear storage; readv/writev batching |
| poll/reactor | interest registration, wakeup and timer queue | watch/modify/remove, wait batch, timer, stop | poll versus epoll, timer heap versus wheel |
| net/address | sockaddr packing and presentation | IPv4/IPv6/Unix addresses, parse/format | kernel layouts, byte order, scope handling |
| net/socket | connection state and socket policy | Listener, Connection, Datagram, connect/accept/receive/send | accept4 fallback, options implementation |
| net/resolve | NSS/resolver selection and result ownership | resolveBlocking, bounded job/poll result | libc getaddrinfo versus explicit resolver service |
| process | spawn transaction, fd inheritance and child reaping | Command, Child, wait/terminate, exit status | posix_spawn versus fork/exec, pidfd availability |
| signals | signal mask, coalescing and safe delivery machinery | SignalSet, event source, next | signalfd versus self-pipe handler |
| environment | snapshot and startup mutation policy | Environment snapshot, set/unset, child environment | initial envp versus host libc environment |
| threads (later) | worker start ABI, stacks, runtime contexts and join ownership | start registered worker, cancel, join/detach | runtime thread context/allocator implementation |
| sync (later) | native lock/queue implementation and memory ordering | bounded bytes channels and concrete shared scalar cells | futex versus pthread primitives; queue layout |
| terminal (later) | mode snapshot/restoration and ioctl layouts | Terminal, size, enterRaw, restore | termios differences, resize delivery |
| logging | record escaping, buffering and loss/backpressure policy | bounded emit, drain, flush, counters | text versus structured sink, queue strategy |

No generic config module is proposed. Configuration format and precedence are
application decisions until multiple real consumers share a specified grammar.
Existing cli/env/strings plus bounded file reads are enough to start. Logging and
terminal earn modules because they own lifecycle/encoding decisions, not because
their helper functions need somewhere to live.

### 3.2 Dependency DAG

`A -> B` means A imports B. This is the permitted dependency graph; within a row
there are no implicit reverse edges. Platform C adapters may call common registry
support, never application obo. Thread bootstrap is a runtime boundary below it.

```text
syserrors                                      -> (builtins only)
sysplatform                                    -> syserrors
sysclock, syscalendar, sysaddress                -> syserrors
  sysclock                                     -> sysplatform
sysenv, sysrandom, sysstat                       -> sysplatform, syserrors
sysstream                                      -> sysplatform, syserrors
sysdir                                         -> sysplatform, sysstat, syserrors
sysfile                                        -> sysdir, sysstat, sysstream, sysplatform
sysmapping                                     -> sysfile, sysplatform
sysbuffer                                      -> sysstream, syserrors
sysreactor                                     -> sysplatform, sysclock, syserrors
sysnet                                         -> sysaddress, sysstream, sysreactor, sysplatform
sysresolve                                     -> sysaddress, sysreactor, sysplatform
syssignals                                     -> sysreactor, sysplatform
sysprocess                                     -> sysenv, sysdir, sysstream, sysreactor, sysplatform
syswaitio                                      -> sysstream, sysbuffer, sysreactor, sysclock
systhreads                                     -> sysplatform, sysclock
syssync                                        -> systhreads, sysreactor, sysclock, sysplatform
systerminal                                    -> sysstream, syssignals, sysreactor, sysplatform
syslog                                         -> sysclock, sysplatform, syserrors
```

Clock has no timer-queue dependency: reactor owns timers. Reactor does not import
net/process/signals; those modules provide typed `watch` functions which call the
reactor's support entry using platform resource identities. The stream module
can dispatch a native endpoint kind without importing net. Waiting helpers live
above reactor and stream, avoiding a stream/reactor cycle. Resolve does not
depend on process just to use a C helper process internally. Existing `sort ->
strings` and `fmt -> io` remain a separate acyclic graph. No systems module
imports a tool or depends on eza exit-code policy.

### 3.3 Never leaks across the supported application boundary

- OS fd numbers, syscall numbers, sockaddr/stat/termios/epoll/pthread layouts.
- Native errno encoding, negative return conventions, libc errno addresses.
- Native buffer addresses, aliasing/retention rules, implicit ownership transfer.
- C registry tokens, token bit packing, release-function pointers.
- Transfer-ticket payloads; a ticket may move only as a whole `Transfer` into a
  typed importer, never as an int or byte array.
- Thread IDs used as kernel handles, worker stacks, allocator arenas, TLS slots,
  runtime handle internals or bootstrap code addresses.

Public byte ownership and explicit close are documented, not hidden. Private
representations are hidden. Diagnostic strings may contain an OS error name or
number, but clients never branch by parsing them. Existing raw-fd builtins and
legacy fs/io remain a documented legacy boundary; this rule applies to the new
systems API, not a false claim about all existing obo programs.

**Implementing hiding in 0.11:** choose one concrete shell representation.
Every native typed shell owns a **concrete** field `resource: platform::Resource`
(for example `pub type File = ref record { resource: platform::Resource }`);
there is no type-erasing alias and no `^File -> ^Resource` conversion. The
support record itself is `pub type Resource = ref record { reserved: int }`, which
carries no authority. `platform::source(r: &Resource): Result[^Resource]` checks
that the record is a live table member and returns its non-owning identity.
`platform::requireKind(r: &Resource, kind: int): Result[^Resource]` performs the
same check plus the expected kind. A module-private table associates that
identity with a C token, kind, state and quota charge. Validate membership,
generation and kind before every native access. A user-created `Resource` has no
entry and returns `InvalidResource`. Moving a shell preserves identity; copying
or reconstructing a record does not duplicate a resource.

**Public-field limitation, stated honestly:** `resource` is public and writable.
Replacing it cannot forge a new capability: the registry still checks membership,
generation and kind, and a user-created `Resource` fails. But a caller *can*
deliberately point a shell at another live resource of the same kind, which
aliases or steals that resource and can leave the original open (a leak). Typed
modules therefore revalidate the field on every entry point and reject a
cross-kind alias with `InvalidResource`; they do not claim that public fields are
opaque. A compiler-negative test must reject `^File`/`^Stream` conversion, and a
runtime test must cover a replaced same-kind and cross-kind field.

Start with a bounded linear identity table (default 4096 live resources per
context) and measure it. This is a correctness prototype, not an O(1) scalability
claim. A later index cache may use an **untrusted managed-table hint**, checked
against full owner identity on every lookup; it must not encode a C token or fd.
Changing that implementation field is not supported client usage. If sufficient
performance requires actual opaque types or a new identity-hash primitive,
propose and budget that separately. Exported-field privacy cannot be assumed.
Private tables must not retain an owning reference to the shell: omitted close
is a detectable native leak, not an immortal managed object disguised as cleanup.

Keep a closed tombstone only while its shell is alive, so repeated close can
return false without treating a forged shell as previously closed. Sweep dead
tombstones on table insertion and explicit context maintenance; cap live shells
(open plus closed) separately from open native resources. Dead **open** entries
are leak records, never silently swept as if close occurred. Report them and
release their native resources only during explicit context teardown. Before a
native call, copy table token/state into scalar locals and release all interior
table borrows; after it, re-find the entry before updating. This is required by
the actual conservative native effect summary, not just a coding preference.
Cross-module support calls may take a copied `^Resource` identity and resolve it
without passing an interior reference; that managed handle is never passed to C
or cast to int. It is not the C registry token. For operations that really need
an owned child resource, take it from an optional field into a whole local,
perform the operation, then restore it before returning. The implementation must
exercise these paths with the real native effects, not only signature stubs.

## 4. Common contracts

### 4.1 Errors and partial progress

Use `Result[T]` with named, stable **library** codes. The new family uses an
assigned-at-implementation range distinct from positive legacy Linux errnos and
from runtime trap statuses; this note reserves no numeric values. Callers branch
on `syserrors::kind(code)` and named predicates, never on a raw number. Unknown
native errors map to `SystemFailure` and preserve operation plus native detail in
the message. No process-global `lastError`: native status is captured before
another C call can overwrite errno, then copied into the operation's result
record/status packet.

```obo
module syserrors
pub type Kind = closed {
    InvalidArgument {} Range {} InvalidResource {} Closed {} NotInitialized {}
    NotFound {} AlreadyExists {} Permission {} NotDirectory {} IsDirectory {}
    PathLoop {} NameTooLong {} CrossDevice {}
    Interrupted {} WouldBlock {} InProgress {} TimedOut {} Cancelled {}
    EndOfInput {} ResourceLimit {} NoMemory {} NoSpace {} LimitExceeded {}
    BrokenPipe {} Reset {} Refused {} AddressInUse {} Unreachable {}
    NotSeekable {} Unsupported {} Busy {}
    ResolveTemporary {} ResolveNotFound {} ResolveFailure {}
    MessageTooLarge {} Poisoned {} IoFailure {} SystemFailure {} DurabilityUnknown {}
}
pub fn kind(code: int): Kind
pub fn code(k: &Kind): int
pub fn fromErrno(errno: int): Kind
pub fn isInterrupted(code: int): bool
pub fn isWouldBlock(code: int): bool
pub fn isRetryable(code: int): bool
pub fn text(k: &Kind): string
```

`code`/`fromErrno` are the only constructors of numeric library codes; the
numbers are assigned once in the implementation and are not a stable ABI for
clients. `fromErrno` is for adapters translating a captured native errno;
`kind` interprets a library code. `isRetryable` is advisory and returns true only
for `Interrupted`/`WouldBlock`; it does not authorize an automatic retry.
`text` is diagnostic only, never parsed by a caller. Hot paths should use the
predicates; `kind` allocates a variant and is for diagnostics or structured
branching, not a per-byte check.

| Category | Meaning / representative Linux source | Required caller behavior |
|---|---|---|
| InvalidArgument, Range, InvalidResource, Closed | validation, stale/wrong kind, closed owner | fix input or lifecycle; no syscall on invalid input |
| NotInitialized | explicit systems-context startup has not succeeded | initialize once before opening resources |
| NotFound, AlreadyExists, Permission, NotDirectory, IsDirectory, PathLoop, NameTooLong, CrossDevice | ENOENT/EEXIST/EACCES/EPERM/ENOTDIR/EISDIR/ELOOP/ENAMETOOLONG/EXDEV | handle operation failure; no check-then-open workaround |
| Interrupted | EINTR with **no reported progress** | one-shot caller chooses retry; deadline helper checks remaining time first |
| WouldBlock | EAGAIN/EWOULDBLOCK, including full nonblocking queues | wait for interest, retry; never busy-loop |
| InProgress | nonblocking connect EINPROGRESS/EALREADY | wait then check connect status; not connected yet |
| TimedOut, Cancelled | deadline reached / explicit cancellation | inspect retained progress/state; no automatic restart |
| EndOfInput | exact-read ended early | accumulated bytes stay in caller-owned state |
| ResourceLimit, NoMemory, NoSpace, LimitExceeded | EMFILE/ENFILE, native ENOMEM, ENOSPC/EDQUOT, configured byte/entry cap | apply backpressure or release resources |
| BrokenPipe, Reset, Refused, AddressInUse, Unreachable | EPIPE/ECONNRESET/ECONNREFUSED/EADDRINUSE/ENETUNREACH/EHOSTUNREACH | connection/endpoint policy |
| NotSeekable, Unsupported, Busy | ESPIPE, ENOSYS/EOPNOTSUPP, active borrow/registration or job | choose a supported operation or finish lifecycle |
| ResolveTemporary, ResolveNotFound, ResolveFailure | EAI_AGAIN/EAI_NONAME/other EAI_* | DNS retry policy is explicit; EAI_SYSTEM captures errno |
| MessageTooLarge, Poisoned | datagram exceeds the configured cap / a shared owner failed | reject before truncation; do not reuse poisoned state |
| IoFailure, SystemFailure, DurabilityUnknown | EIO, unmapped failure, failure after replace commit | do not confuse failure with rollback |

Add `MessageTooLarge` for datagram size and `Poisoned` for later shared native
state whose owner failed. No arbitrary errno integer is cast to a category.
`err.message` is for humans, not a structured transport. Rich outcomes that must
carry both progress and status use a success record or a mutable operation state;
do not expect a second generic error parameter in `Result`.

**No hidden retries.** One-shot operations (`open`, `readSome`, `writeSome`,
`accept`, `connect`, `receive`, `send`, `stat`, `rename`, `close`) make one
syscall attempt and return the observed result. `Interrupted` is not `WouldBlock`:
the former means the call was interrupted with no reported progress; the latter
means the endpoint is not ready. A caller chooses whether to retry, waits for
readiness, or gives up. The only routines with an internal retry loop are named
compound helpers (`replaceAtomic`, `syswaitio` and reactor waits); their finite
policy, absolute deadline and stop/cancel checks are part of the contract, and
they never restart a timeout from zero. `close` is never retried after `EINTR` on
Linux because the descriptor is already consumed.

`readSome` / `writeSome` perform at most one kernel transfer attempt. A positive
short transfer is success; it is never silently repeated. Empty writes return
0 after resource validation. A zero read request is InvalidArgument; EOF is
`ok(nil)`, distinct from WouldBlock. For UDP, empty datagrams are data, not EOF.
No normal network/filesystem failure traps. Checked sizes are validated before
array indexing, multiplication, narrowing, allocation or native calls.

### 4.2 Ownership and resource state

Every successful creator returns one owned obo shell with one logical close
obligation. The shell's record destructor does **not** release native resources.
Register `defer close(owner)` immediately. A borrowed operation never retains a
borrowed obo reference in C; queued bytes are copied to C-owned memory before
return, or moved into an obo-owned pending operation on the same thread.

| State | Operation | Transition / result |
|---|---|---|
| Unregistered shell | any I/O or close | InvalidResource; never dereference C |
| Open | read/write/stat/etc. | Open, except explicit endpoint transitions |
| Open | close | invalidate native token before release; Closed even if close reports failure |
| Closed | close | `ok(false)`; no kernel close |
| Closed | I/O | Closed |
| Open | explicit duplicate | new independent owner; both must close |
| Open owner dropped without close | leak census | native-resource leak; no hidden finalizer |
| Open with registration | owner close | invalidate/remove native subscriptions; already-copied events are stale and safely ignored |

On Linux close EINTR consumes the fd; never retry close, even after EIO. Report
the close diagnostic but retire the token. On other Unixes the backend must
implement its own close semantics without exposing reusable numbers. Successful
sync before close is necessary when the application needs to report writeback
errors. Deferred close discards its Result, so explicitly close/flush/sync on the
normal success path when their errors matter; the defer then sees Closed.

Every newly created/duplicated fd is CLOEXEC **atomically** (openat flags,
accept4, pipe2, socket flags, F_DUPFD_CLOEXEC). If a platform cannot do this,
serialize all descriptor creation and spawning inside the backend or reject the
capability; a late fcntl with an exec race is not equivalent. Standard streams
are obtained as owned CLOEXEC duplicates; new APIs never close process fd 0/1/2.
Do not import raw integers from legacy builtins into the new registry.

Generation exhaustion retires a C registry slot instead of wrapping. The
managed identity table also inherits the runtime's finite-generation limitation;
thread work must audit wrap/ABA policy, not claim permanent capability security.
Native resource checks defend accidental stale/wrong-kind use, not malicious C.

### 4.3 Platform support, testability and compiler cost

`sysplatform` is a support boundary, not a public syscall escape hatch. It holds
capability queries (`openBeneath`, `fileMap`, `epoll`, `signalQueue`, etc.), resource
quotas and a diagnostic snapshot. Thin calls are acceptable **inside** this
boundary, where Linux struct layouts and errno translation belong.

```obo
module sysplatform
pub type Resource = ref record { reserved: int }
pub type Transfer = ref record { resource: Resource }
pub type Options = ref record {
    maxResources: int; maxShells: int; maxNativeBytes: int
    ignorePipeSignal: bool
}
pub type Counts = ref record {
    resources: int; shells: int; nativeBytes: int; cleanupFailures: int
}
pub const KIND_FILE = 1
pub const KIND_DIRECTORY = 2
pub const KIND_ITERATOR = 3
pub const KIND_MAPPING = 4
pub const KIND_STREAM = 5
pub const KIND_REACTOR = 6
pub const KIND_WATCH = 7
pub const KIND_TIMER = 8
pub const KIND_LISTENER = 9
pub const KIND_CONNECTION = 10
pub const KIND_DATAGRAM = 11
pub const KIND_CHILD = 12
pub const KIND_SIGNAL_SOURCE = 13
pub const KIND_TERMINAL = 14
pub const KIND_LOGGER = 15
pub const KIND_WORKER = 16
pub const KIND_CANCELLATION = 17
pub const KIND_TRANSFER = 18
pub const KIND_ATOMIC = 19
pub const KIND_SENDER = 20
pub const KIND_RECEIVER = 21
pub fn initialize(options: &Options): Result[bool]
pub fn hasCapability(name: string): bool
pub fn source(r: &Resource): Result[^Resource]
pub fn requireKind(r: &Resource, kind: int): Result[^Resource]
pub fn counts(): Result[Counts]
pub fn releaseTransfer(t: &Transfer): Result[bool]
pub fn shutdown(): Result[Counts]
```

`source` is the only supported bridge from a shell's `resource` field to the
checked identity used by `watchResource`; `requireKind` is the typed-module
entry check. Both reject an unregistered, closed or wrong-generation record.
The `KIND_*` values are library constants; changing them is a library ABI
change, not a kernel ABI exposure. `initialize` validates every `Options` field
at entry: positive limits, checked arithmetic and an explicit `ignorePipeSignal`
policy.

Initialization is explicit, before resource creation; imports have no OS side
effects. Defaults supplied by a constructor: 4096 native resources, 8192 live
shells and 64 MiB native-buffer bytes, independently adjustable with checked
positive limits. Unknown capability names return false. Reinitializing with the
same policy returns false; incompatible policy is Busy. shutdown closes orphaned
resources after an application has closed its owners, reports pre-cleanup leaks
and cleanup failures, and rejects running children/workers until their shutdown
policy is resolved. It does not hide the evidence by returning only post-cleanup
zeros. Register deferred shutdown as a fallback and inspect its Result explicitly
on the normal path. One current context per thread is a backend decision; v1 has
one process context, later worker contexts are selected by TLS without changing
these function signatures. No native action is legal after shutdown until a new
context is explicitly initialized; old shells cannot address the new context.
shutdown itself is idempotent and returns a copy of its cached final report;
hasCapability returns false outside an initialized context. Cleanup calls on
retired shells still return false, without resolving against a new registry.

Use a maintained `.cbind`, header, C adapter and ordinary obo wrapper, following
the cinterop skill. At the C ABI only scalar i64 arguments and i32/i64 results
cross. Long inputs/outputs are bounded C buffers indexed by validated tokens;
copy via scalar byte access initially, optionally packed groups of up to seven
bytes (fits a positive signed int) after benchmarks. Address, path, stat and
process packets have versioned lengths and explicit integer fields, not copied
C structs. No managed pointer crosses, including a pointer disguised as an int.
No compiler switch per syscall. Native callbacks, if needed by a library, remain
in C and queue bounded events for an obo polling call.

The existing byte bridge is slow; network throughput is an acceptance measurement,
not an assumption. A future compiler-assisted bulk copy would require its own
checked ownership/effects boundary, benchmark against packing, self-host fixed
point and measured <=2,500,000-byte compiler peak. It is **optional**, not a
prerequisite hidden inside this plan. Existing slot 59 remains reserved to hosted
exit. No new jump-table allocation is prescribed here.

Select the native fake versus Linux backend at build time in the link manifest.
They implement the same adapter symbols. There is no global runtime toggle or
stored obo callback. A fake operation script supplies time, readiness, exact
progress and failures. Public operations do not depend on the test harness.
Conservative native effect summaries remain intact. Until interop is stabilized,
the systems modules using it remain experimental even if their source APIs look
ordinary.

## 5. Files, directories, metadata and mappings

Logical os/* lives under `obo/lib/sys/`, with unique basename/module names.
The following headings show the intended division, not a giant OS namespace.

### 5.1 Directories and path safety

```obo
module sysdir
import "sysplatform.obx" as platform
import "sysstat.obx" as stat

pub type Directory = ref record { resource: platform::Resource }
pub type Iterator = ref record { resource: platform::Resource }
pub type Entry = ref record { name: string; kind: int }
pub fn open(path: string): Result[Directory]
pub fn child(base: &Directory, name: string): Result[Directory]
pub fn iterate(base: &Directory): Result[Iterator]
pub fn next(it: &Iterator): Result[?Entry]
pub fn metadata(base: &Directory, name: string, follow: bool): Result[stat::Metadata]
pub fn mkdir(base: &Directory, name: string, permissions: int): Result[bool]
pub fn unlink(base: &Directory, name: string): Result[bool]
pub fn removeEmpty(base: &Directory, name: string): Result[bool]
pub fn close(base: &Directory): Result[bool]
pub fn closeIterator(it: &Iterator): Result[bool]
```

`name` in these anchored operations is a **single component**: nonempty, no `/`,
NUL, `.` or `..`. Path strings preserve non-UTF-8 Unix bytes. `open(path)` accepts
an ordinary path with explicit host semantics and follows symlinks; an application
chooses the initial anchor. `child` opens a directory with NOFOLLOW and CLOEXEC.
Do not convert names through lossy UTF-8 or use lexical paths::join for confinement.

The iterator duplicates its anchor; closing Directory does not invalidate it.
It yields owned names independent of refill/close, skips `.`/`..`, promises no
order or directory snapshot, and returns Unknown kind rather than hiding a failed
stat. Metadata is a separate checked operation; names can disappear between calls.
Kernel dirent corruption fails the iterator permanently with IoFailure, not a
repeatable infinite loop. Enumeration buffer and maximum name length are bounded.

For multi-component hostile paths, add `file::openBeneath(base, path, options)`
using openat2 RESOLVE_BENEATH|RESOLVE_NO_MAGICLINKS and an explicit symlink policy.
Absence of that capability is Unsupported, not string-prefix validation. The
single-component/NOFOLLOW API is the portable first slice; it must not claim to
solve every concurrent rename/mount namespace containment problem.

### 5.2 File operations and atomic replacement

```obo
module sysfile
import "sysdir.obx" as dir
import "sysplatform.obx" as platform
import "sysstat.obx" as stat
import "sysstream.obx" as stream

pub type File = ref record { resource: platform::Resource }
pub type OpenOptions = ref record {
    read: bool; write: bool; create: bool; exclusive: bool
    truncate: bool; append: bool; noFollow: bool; nonblocking: bool
    permissions: int
}
pub type ReplaceReport = ref record {
    committed: bool; durable: bool; code: int; message: string
}
pub fn readOptions(): OpenOptions
pub fn open(path: string, options: &OpenOptions): Result[File]
pub fn openAt(base: &dir::Directory, name: string, options: &OpenOptions): Result[File]
pub fn openBeneath(base: &dir::Directory, path: string, options: &OpenOptions): Result[File]
pub fn readSome(f: &File, max: int): Result[?[]byte]
pub fn writeSome(f: &File, bytes: &[]byte, offset: int, count: int): Result[int]
pub fn readAt(f: &File, position: int, max: int): Result[?[]byte]
pub fn writeAt(f: &File, position: int, bytes: &[]byte): Result[int]
pub fn seek(f: &File, offset: int, origin: int): Result[int]
pub fn truncateTo(f: &File, length: int): Result[bool]
pub fn metadata(f: &File): Result[stat::Metadata]
pub fn chmod(f: &File, permissions: int): Result[bool]
pub fn sync(f: &File, dataOnly: bool): Result[bool]
pub fn duplicateStream(f: &File): Result[stream::Stream]
pub fn close(f: &File): Result[bool]
pub fn rename(from: &dir::Directory, old: string,
              to: &dir::Directory, name: string, replace: bool): Result[bool]
pub fn link(from: &dir::Directory, old: string,
            to: &dir::Directory, name: string): Result[bool]
pub fn symlink(target: string, to: &dir::Directory, name: string): Result[bool]
pub fn replaceAtomic(to: &dir::Directory, name: string, data: &[]byte,
                     permissions: int, durable: bool): Result[ReplaceReport]
```

Option validation: at least one of read/write; create/exclusive/truncate/append
require write; exclusive requires create; truncate and append together are
InvalidArgument. Permissions are 0..0xFFF and subject to umask on creation;
chmod explicitly sets those bits. Options are copied at open, never retained.
No integer Linux flag word. `SEEK_START/CURRENT/END` are library constants;
resulting positions and lengths must be nonnegative signed 64-bit bytes. Check
addition before seeking. ESPIPE maps to NotSeekable. Positional writes on
append-open files are rejected rather than inheriting Linux's pwrite/O_APPEND
surprise. Nonblocking regular-file operations may still block for storage I/O.

Open-at names use §5.1 validation. Metadata and chmod act on the open file,
avoiding a second path lookup. Hard link does not follow the source symlink;
symlink target accepts relative/absolute bytes without NUL and does not resolve
them. Rename is atomic for name visibility within the filesystem. `replace=false`
requires an atomic no-replace primitive or returns Unsupported; no existence
check followed by overwrite. Cross-filesystem rename is CrossDevice, never an
implicit copy/delete. Directory sync is a backend operation used by replace.

Atomic replacement is a transaction, not writeFile with a new name:

| Stage | Invariant on failure |
|---|---|
| Validate/cap input; create unique O_EXCL temporary in target directory | old target untouched; native temp owned by transaction |
| Write with explicit internal progress; set requested permissions | temp removed on error; no hidden infinite EINTR retry |
| If durable, fsync temporary | failure is precommit; old name untouched |
| Rename temporary over target | commit point; target now refers to new inode |
| If durable, fsync directory | failure returns report `{committed:true,durable:false,code:DurabilityUnknown}` |
| Close/remove leftovers | never roll back a committed rename; report cleanup failure |

Precommit failures return err; postcommit failures return ok(ReplaceReport) with
nonzero code so callers can distinguish commitment. Successful nondurable replace
has committed=true/durable=false/code=0. This helper owns its finite retry policy:
Interrupted stops before commit, and short writes advance the cursor; a future
deadline variant uses one absolute deadline. No promise to preserve old inode,
hard links, ACLs, ownership, xattrs or watchers; replacement permissions are
explicit. Resource leaks/cleanup errors remain observable even if the primary
failure is returned.

### 5.3 Metadata

```obo
module sysstat
pub type Timestamp = ref record { seconds: int; nanos: int }
pub type Identity = ref record { device: []byte; inode: []byte }
pub type Metadata = ref record {
    kind: int; permissions: int; size: int; links: int
    uid: int; gid: int; modified: Timestamp; accessed: Timestamp
    changed: Timestamp; created: ?Timestamp; identity: Identity
}
pub fn sameIdentity(a: &Identity, b: &Identity): bool
```

Metadata is a detached owned snapshot, not a borrowed kernel struct. kind is a
library constant; permissions are low Unix permission bits, not type bits.
Identity components are canonical 8-byte unsigned values, with equality only;
this avoids silently interpreting a uint64 inode as a negative obo int. uid/gid
fit positive int; links outside the signed range return Range. size is meaningful
for regular files; device/FIFO sizes carry no portable content-length promise.
Timestamps normalize nanos to 0..999,999,999 and keep seconds separate, permitting
dates outside signed-nanosecond epoch range. Birth time is nil when unavailable.
Statx/fstatat conversion stays native; no new indexed stat array.

### 5.4 Mapping

```obo
module sysmapping
import "sysfile.obx" as file
import "sysplatform.obx" as platform
pub type Mapping = ref record { resource: platform::Resource }
pub fn anonymous(length: int, writable: bool): Result[Mapping]
pub fn fromFile(f: &file::File, offset: int, length: int,
                writable: bool, shared: bool): Result[Mapping]
pub fn copyOut(m: &Mapping, offset: int, count: int): Result[[]byte]
pub fn copyIn(m: &Mapping, offset: int, data: &[]byte): Result[bool]
pub fn protect(m: &Mapping, readable: bool, writable: bool): Result[bool]
pub fn flush(m: &Mapping): Result[bool]
pub fn close(m: &Mapping): Result[bool]
```

Addresses, munmap lengths and page-rounding live in C. Length >0; offset >=0;
file offset must be page-aligned; queried page size, checked end arithmetic and
configured mapping cap precede mmap. Whole-mapping protect only in v1; no execute
permission. Bounds/protection violations become Result before a copy. No returned
borrow into a mapping or fake []byte header. Close unmaps once. File mapping
outlives File close because the kernel mapping retains its backing object.
MAP_PRIVATE changes are not persisted; shared flush uses msync and does not
promise directory durability. Anonymous mappings are suitable for large native
scratch buffers, not for allocating obo records outside its heap.

External truncation of a mapped file can still cause SIGBUS; a prior stat cannot
prevent it. Hence file-backed mapping is an explicit specialized facility with
a stable-backing-file precondition, **not** the default file reader or an HTTP
input path. Returnable normal I/O failures use pread/copy instead; this design
does not attempt signal-based recovery from arbitrary memory faults.

## 6. Time, randomness and byte I/O

### 6.1 Clocks, durations, calendar text and timers

```obo
module sysclock
pub type Instant = ref record { nanos: int }
pub type Duration = ref record { nanos: int }
pub type Deadline = ref record { finite: bool; nanos: int }
pub fn monotonic(): Result[Instant]
pub fn realtimeNs(): Result[int]
pub fn durationNs(n: int): Result[Duration]
pub fn durationMs(n: int): Result[Duration]
pub fn after(now: &Instant, duration: &Duration): Result[Deadline]
pub fn forever(): Deadline
pub fn remaining(now: &Instant, deadline: &Deadline): Result[?Duration]
pub fn sleepUntil(deadline: &Deadline): Result[bool]
```

Instant is CLOCK_MONOTONIC, valid only within this boot/backend clock domain;
never persist it or compare to realtime. Duration is nonnegative signed i64 ns;
negative inputs are InvalidArgument. Multiplication, timespec conversion,
deadline addition and elapsed subtraction are checked before arithmetic;
overflow is Range, not wrap/saturation. A finite deadline at/before now is
expired; remaining returns zero. Infinity is a distinct flag, not INT64_MAX;
`remaining` returns nil for an infinite deadline, so no duration overflow is
invented. Reject malformed public records. realtimeNs is signed ns since Unix epoch and
returns Range if seconds cannot be represented (~1677–2262 range); calendar
and metadata use seconds+nanos for wider dates. No float time arithmetic.

`sleepUntil` is deliberately interruptible and returns Interrupted; calling it
again uses the same absolute deadline. Reactor waits and syswaitio helpers may
handle EINTR internally only by checking stop/cancellation and recomputing
remaining time against the original deadline. Rounding for a millisecond backend
is upward with checked clamping per wait, never making an early timeout. Realtime
clock adjustments cannot extend a monotonic timeout. Suspend time is not included
by CLOCK_MONOTONIC; a future boottime clock is a separately named capability.

```obo
module syscalendar
pub type Timestamp = ref record { seconds: int; nanos: int }
pub fn parseRfc3339(text: string): Result[Timestamp]
pub fn formatRfc3339(t: &Timestamp): Result[string]
```

`parseRfc3339` accepts four-digit years 0001..9999,
valid Gregorian dates, `T`, seconds, optional 1..9 fractional digits, and `Z` or
signed HH:MM offset; normalize to UTC. Reject leap seconds, excess fractional
digits, trailing text and `-00:00` (unknown offset) rather than silently assigning
an instant. Output UTC `Z`, omitting a zero fraction and trimming trailing zeros.
This is a specified ISO-8601/RFC3339 profile, not all ISO-8601 forms, locale or TZ
database support. Existing time::civil/formatIso keep their contracts.

Reactor owns `Timer` registrations (§7), one-shot or periodic. A zero
`period.nanos` is one-shot; a positive period is periodic. Periodic timers
advance from the previous scheduled deadline, not callback finish; coalesce
missed ticks into a checked count and bound each delivery batch. Timer arithmetic
overflow disables that timer and reports Range. There is no signal handler timer
or implicit helper thread.

### 6.2 Cryptographic randomness

`sysrandom::fillSome(mut bytes:&[]byte, offset:int, count:int,
blocking:bool):Result[int]` writes only within pre-existing array length and
returns exact progress. `bytes(count:int, blocking:bool):Result[[]byte]` is a
bounded convenience which fails on interruption and publishes no partial output.

```obo
module sysrandom
pub fn fillSome(mut bytes: &[]byte, offset: int, count: int,
                blocking: bool): Result[int]
pub fn bytes(count: int, blocking: bool): Result[[]byte]
```

A later `fillUntil` uses a retained cursor and deadline: only nonblocking
getrandom attempts, with bounded timer backoff while entropy is unavailable.
getrandom has no pollable fd in this API. Never put a blocking entropy syscall
inside a helper claiming a finite timeout.

Use getrandom without GRND_RANDOM. blocking=true may wait for kernel CSPRNG
initialization; blocking=false uses GRND_NONBLOCK and returns WouldBlock. EINTR
is Interrupted; short fill is success. ENOSYS is Unsupported initially; no
silent insecure PRNG or time/PID seed. Cap one allocation (default 1 MiB), allow
larger explicit chunk loops. No userspace CSPRNG in v1: it adds seed, reseed,
fork-safety and state-erasure obligations with no demonstrated need. Random bytes
are not promised secure-memory zeroization; use a dedicated C-owned secret-buffer
adapter if that is a later requirement. Fake deterministic entropy never ships
as an application-selected production mode.

### 6.3 Streams and bounded buffers

```obo
module sysstream
import "sysplatform.obx" as platform
pub type Stream = ref record { resource: platform::Resource }
pub fn source(s: &Stream): Result[^platform::Resource]
pub fn standardInput(): Result[Stream]
pub fn standardOutput(): Result[Stream]
pub fn standardError(): Result[Stream]
pub fn readSome(s: &Stream, max: int): Result[?[]byte]
pub fn writeSome(s: &Stream, bytes: &[]byte, offset: int, count: int): Result[int]
pub fn writeVector(s: &Stream, parts: &[][]byte,
                   first: int, offset: int): Result[int]
pub fn readVector(s: &Stream, sizes: &[]int): Result[?[][]byte]
pub fn shutdownRead(s: &Stream): Result[bool]
pub fn shutdownWrite(s: &Stream): Result[bool]
pub fn close(s: &Stream): Result[bool]
```

Stream is a concrete tagged native endpoint abstraction, not a current obo
interface with unsound native effect claims. File/pipe/socket APIs produce it by
explicit duplicate or transfer. Native side keeps iovecs and C buffers alive
through the syscall; obo arrays are borrowed only for the call. Vector total bytes
and count (default <=64, further bounded by backend IOV_MAX) are checked; the
return count advances a cursor across vectors, not a count per vector. ReadVector
returns owned arrays, final one possibly short; nil is EOF before any data.
Socket send uses MSG_NOSIGNAL. Pipe writes need the explicit SIGPIPE policy in §8.
Half-close on unsupported endpoint kinds returns Unsupported.

```obo
module sysbuffer
import "sysstream.obx" as stream
pub type Buffer = ref record { bytes: []byte; start: int; end: int; limit: int }
pub type Reader = ref record { buffer: Buffer; eof: bool }
pub type Writer = ref record { buffer: Buffer }
pub type Cursor = ref record { done: int }
pub fn make(limit: int): Result[Buffer]
pub fn feed(mut b: &Buffer, data: &[]byte): Result[bool]
pub fn find(b: &Buffer, separator: &[]byte, from: int): Result[int]
pub fn copy(b: &Buffer, count: int): Result[[]byte]
pub fn consume(mut b: &Buffer, count: int): Result[bool]
pub fn readStep(mut r: &Reader, s: &stream::Stream, max: int): Result[int]
pub fn writeStep(mut w: &Writer, s: &stream::Stream): Result[int]
pub fn writeExactStep(s: &stream::Stream, bytes: &[]byte,
                       mut cursor: &Cursor): Result[bool]
pub fn readExactStep(s: &stream::Stream, count: int,
                      mut out: &Buffer): Result[bool]
```

Buffer is owned mutable byte storage; its public fields are checked at entry.
Invariants `0 <= start <= end <= len(bytes)` and `len(bytes) == limit`; unread/pending bytes are
`[start,end)`. Allocate fixed storage with `new []byte(limit)` at construction,
use indexed writes and compaction, and never append past that allocation. This
avoids obo's geometric array growth silently exceeding a capacity promise.
Default limit is 64 KiB, configured maximum 16 MiB for these buffers, native
transfer chunk <=1 MiB. Record/array headers and temporary copied chunks are
additional bounded overhead; caller admission limits must count them as well.
Reject invalid limits rather than legacy io::clampChunk behavior. feed is all-or-nothing with
respect to capacity; it never writes to the endpoint. find is binary-safe and
incremental; returns -1 when a delimiter has not arrived, with empty separator
InvalidArgument. copy returns an independent prefix, consume releases/compacts
it; no borrowed slices escape. Parsers keep integer offsets, not references
across fill/compaction. Protocol-specific parsing is the caller's responsibility.

readStep appends one transfer and preserves partial data on error; writeStep
removes only the successfully written prefix. ExactStep attempts at most one
transfer and returns true if complete, false after partial success, or err;
cursor/buffer retains progress on every exit. EOF before count is EndOfInput.
Validate 0<=cursor.done<=len(bytes), no replay of already-written bytes, no
zero-progress busy loop. Data must be identical across repeated writeExactStep
calls; an owning `PendingWrite` variant can enforce that by moving an array.

`syswaitio::readExactUntil` / `writeAllUntil` / `flushUntil` combine those same
states with explicit reactor, deadline and cancellation arguments. They never
reset a timeout after EINTR, partial progress or readiness. New buffering does
not store `&Stream`: endpoints are parameters of each step, or an owned endpoint
is explicitly moved into a higher-level owner. Existing io.Reader/Writer stay
source-compatible; no inheritance/interface redesign is required.

Representative waiting signatures (syswaitio also imports sysbuffer as buffer,
sysstream as stream, sysreactor as reactor and sysclock as clock):

```obo
module syswaitio
import "sysbuffer.obx" as buffer
import "sysstream.obx" as stream
import "sysreactor.obx" as reactor
import "sysclock.obx" as clock
import "sysplatform.obx" as platform
pub type Cancellation = ref record { resource: platform::Resource }
pub fn cancellation(): Result[Cancellation]
pub fn request(c: &Cancellation): Result[bool]
pub fn closeCancellation(c: &Cancellation): Result[bool]
pub fn readExactUntil(s: &stream::Stream, r: &reactor::Reactor, count: int,
                       mut out: &buffer::Buffer, until: &clock::Deadline,
                       cancel: &Cancellation): Result[bool]
pub fn writeAllUntil(s: &stream::Stream, r: &reactor::Reactor, bytes: &[]byte,
                      mut cursor: &buffer::Cursor, until: &clock::Deadline,
                      cancel: &Cancellation): Result[bool]
```

Cancellation is a native flag plus wake registration, not a shared obo bool.
request is idempotent and cancellation is sticky. V1 can request it before a
helper or have a configured native signal/event producer request it; it does
not enable unsupported concurrent obo execution. The helper checks before any
transfer and after each wait, removes temporary watches on every exit, and keeps
cursor/output progress. A helper may consume readiness batches from its reactor;
therefore these blocking conveniences require a **dedicated reactor**. They do
not recursively drive an application's existing reactor or discard unrelated
events. Event-driven applications use the Step APIs directly.

### 6.4 Example of the cleanup/error style

This is proposed usage once sysfile exists; it demonstrates current syntax.

```obo
module sample
import "sys/sysfile.obx" as file
import "sys/sysplatform.obx" as platform

fn inspect(path: string): Result[int] {
    let options = file::readOptions()
    let f = try file::open(path, options)
    defer file::close(f)
    let bytes = try file::readSome(f, 4096)
    if bytes == nil { return ok(0) }
    let n = len(bytes)
    try file::close(f)
    return ok(n)
}

fn main() {
    let options = new platform::Options{
        maxResources: 4096, maxShells: 8192, maxNativeBytes: 67108864,
        ignorePipeSignal: false
    }
    let started = platform::initialize(options)
    match started {
        ok(active) { }
        err(message, code) { eprintln(message); return }
    }
    defer platform::shutdown()
    let result = inspect("input.dat")
    match result {
        ok(n) { println(n) }
        err(message, code) { eprintln(message) }
    }
    let stopped = platform::shutdown()
    match stopped {
        ok(counts) { }
        err(message, code) { eprintln(message) }
    }
}
```

The example returns normally to run checks; an application choosing a nonzero
exit status must do so only after its cleanup helper returns. It must not use
the Result code as an exit status. Defer reads the whole local f, and no later
operation moves it; an explicit close plus defer is intentionally idempotent.

## 7. Networking and readiness

### 7.1 Addresses and resolution

```obo
module sysaddress
pub type Address = closed {
    IPv4 { octets: []byte; port: int }
    IPv6 { octets: []byte; port: int; scope: int }
    UnixPath { path: string }
    UnixAbstract { name: []byte }
}
pub fn parseNumeric(text: string, port: int): Result[Address]
pub fn format(a: &Address): Result[string]
```

IPv4/IPv6 octet lengths are exactly 4/16; port 0..65535, scope an unsigned
32-bit interface index represented as nonnegative int. Host/network byte order
is private. Numeric parsing does no DNS, accepts IPv6 bracket presentation only
where explicitly documented, and never guesses octal IPv4. UnixPath rejects
NUL and checks sockaddr_un capacity including terminator; UnixAbstract preserves
arbitrary bytes and is a Linux capability, not a portable pathname. Bind never
unlinks an existing path automatically. v1 Unix streams and datagrams use the
same lifecycle as IP sockets; no ancillary fd passing or peer-credential API is
implied.

`sysresolve::resolveBlocking(host:string, service:string,
family:int, socketKind:int):Result[[]address::Address]` copies getaddrinfo results
and always calls freeaddrinfo. Bound host/service input, list length and total
bytes; preserve NSS result order. Distinguish EAI errors from errno. No hidden
cache or TTL promise: getaddrinfo does not supply DNS TTLs. Numeric-only queries
are available separately and do not need NSS. Blocking resolver calls are
startup/client conveniences; **no finite deadline is promised for blocking NSS**.

For reactive resolution, add `beginResolve`, `watchResolve`, `takeResolved`,
`cancelResolve`, `closeResolve` over a bounded ResolverJob identity. P3 uses an
explicitly configured helper process (not automatic process spawning by import).
P4 may use C-only workers that never call obo or touch its heap. Deadline/cancel
abandons the result, frees eventual output and bounds outstanding work; it does
not pretend to interrupt an arbitrary libc resolver safely. Callbacks from an
alternative C resolver queue records on the C side. A simple listening server
can use numeric addresses and requires no DNS.

### 7.2 Listener, connection and datagram contracts

```obo
module sysnet
import "sysaddress.obx" as address
import "sysstream.obx" as stream
import "sysreactor.obx" as reactor

pub type Listener = ref record { reserved: int }
pub type Connection = ref record { reserved: int }
pub type Datagram = ref record { reserved: int }
pub type ListenOptions = ref record {
    backlog: int; reuseAddress: bool; reusePort: bool; ipv6Only: bool
}
pub type Packet = ref record {
    bytes: []byte; peer: address::Address; truncated: bool; originalSize: int
}
pub fn listen(a: &address::Address, options: &ListenOptions): Result[Listener]
pub fn accept(l: &Listener): Result[Connection]
pub fn connect(a: &address::Address): Result[Connection]
pub fn finishConnect(c: &Connection): Result[bool]
pub fn readSome(c: &Connection, max: int): Result[?[]byte]
pub fn writeSome(c: &Connection, bytes: &[]byte, offset: int, count: int): Result[int]
pub fn shutdownWrite(c: &Connection): Result[bool]
pub fn duplicateStream(c: &Connection): Result[stream::Stream]
pub fn localAddress(c: &Connection): Result[address::Address]
pub fn peerAddress(c: &Connection): Result[address::Address]
pub fn setNoDelay(c: &Connection, enabled: bool): Result[bool]
pub fn setKeepAlive(c: &Connection, idleSeconds: int,
                    intervalSeconds: int, probes: int): Result[bool]
pub fn watchConnection(r: &reactor::Reactor, c: &Connection,
                       readable: bool, writable: bool, tag: int): Result[reactor::Watch]
pub fn watchListener(r: &reactor::Reactor, l: &Listener,
                     tag: int): Result[reactor::Watch]
pub fn bindDatagram(a: &address::Address): Result[Datagram]
pub fn receive(d: &Datagram, max: int): Result[Packet]
pub fn send(d: &Datagram, to: &address::Address, bytes: &[]byte): Result[int]
pub fn closeConnection(c: &Connection): Result[bool]
pub fn closeListener(l: &Listener): Result[bool]
pub fn closeDatagram(d: &Datagram): Result[bool]
```

All sockets are nonblocking and CLOEXEC from creation; no mode toggle on an open
connection, since dup shares file-status flags. accept returns WouldBlock on an
empty queue. On EMFILE/ENFILE pause acceptance, report resource pressure and wait
for capacity/backoff; any reserved-fd recovery technique is an explicit listener
policy, not a busy accept loop. EINTR is observable. Positive backlog is validated
but kernel capping is documented. REUSEADDR and REUSEPORT are independent explicit
options; default reusePort=false, ipv6Only=true. No accidental dual-stack bind.

connect creates an owned Connection even if connection establishment is pending;
the pending status is stored internally, not returned as err that loses the
owner. Immediate establishment failure returns err and closes the socket.
finishConnect returns false while pending, true when connected, or the captured
SO_ERROR failure; writability alone is not success. A wait helper watches then
checks SO_ERROR under one deadline. Client address fallback / racing attempts
must be explicit policies, never an unbounded sequential DNS/connect loop.

| Connection state | Allowed transition |
|---|---|
| Connecting | finishConnect -> Open, pending, or Failed; read/write -> InProgress |
| Open | short read/write -> Open; read EOF -> ReadClosed; shutdownWrite -> WriteClosed |
| ReadClosed | writes still allowed; reads return EOF |
| WriteClosed | reads still allowed; writes return BrokenPipe |
| Both halves closed / Failed | explicit close still required |
| Closed | close false; all transfers Closed |

Directional state is two independent bits after connection establishment.
shutdownWrite is idempotent and never implies that the peer has consumed bytes.
Close is abortive with respect to application-buffered data: flush first when
needed. Never set linger to a blocking close by default. A duplicated Stream has
an independent fd lifetime but shares the socket and half-close state; mixing
readers on duplicates splits incoming bytes and is explicitly unsupported by
buffered-reader helpers. Native state tracks endpoint sharing.

Datagram receive preserves message boundaries, sender and truncation. An empty
datagram succeeds with empty bytes. originalSize is exact when the backend can
provide it, otherwise -1 with truncated=true; no silent truncation. A datagram
send is all-or-error; an unexpected short send is IoFailure, never continuation
as a second datagram. Provide checked receive/send buffer size options and
broadcast enablement in P2; multicast membership, ancillary data and batch
sendmmsg/recvmmsg are later independently justified additions.

Server-specific needs are accept fairness, descriptor pressure, bounded per-peer
buffers, idle deadlines and shutdown. Client-specific needs are address resolution,
connect completion and fallback policy. Neither needs a public fd or socket-option
integer escape hatch.

### 7.3 Reactor

```obo
module sysreactor
import "sysclock.obx" as clock
import "sysplatform.obx" as platform
pub type Reactor = ref record { reserved: int }
pub type Watch = ref record { reserved: int }
pub type Timer = ref record { reserved: int }
pub type Event = ref record {
    tag: int; readable: bool; writable: bool; hangup: bool
    failed: bool; timer: bool; ticks: int; wake: bool
}
pub fn create(maxWatches: int, maxTimers: int): Result[Reactor]
pub fn watchResource(r: &Reactor, source: ^platform::Resource,
                      readable: bool, writable: bool, tag: int): Result[Watch]
pub fn modify(w: &Watch, readable: bool, writable: bool): Result[bool]
pub fn remove(w: &Watch): Result[bool]
pub fn timer(r: &Reactor, at: &clock::Deadline,
             period: &clock::Duration, tag: int): Result[Timer]
pub fn cancelTimer(t: &Timer): Result[bool]
pub fn wait(r: &Reactor, until: &clock::Deadline, maxEvents: int): Result[[]Event]
pub fn wake(r: &Reactor): Result[bool]
pub fn stop(r: &Reactor): Result[bool]
pub fn close(r: &Reactor): Result[bool]
```

This is **readiness**, not completion: readable can produce WouldBlock; hangup
does not mean buffered input has been drained. Always attempt transfers and
inspect their Results. Linux implementation uses epoll, level-triggered only in
v1, with a poll reference backend for conformance. Regular files are not generally
epoll-watchable; return Unsupported rather than promise nonblocking disk I/O.
No io_uring promise. Edge-triggered mode is deferred until callers and tests can
prove drain-to-EAGAIN plus rearm behavior; it is not a public optimization switch
in the first API.

stream::source returns an ordinary checked, non-owning managed identity, which
can be passed to watchResource without an interior borrow or a module cycle.
It grants observation, not I/O or close. It exposes no native token or handle
bits. watchResource looks up the identity and verifies liveness, kind and
watchability before a native call; it never dereferences a stale handle and
traps. Typed net/process/signal watch helpers perform this conversion internally.
syswaitio uses the same stream source bridge. The source cannot outlive its
endpoint as a usable subscription authority.

Watch owns a subscription, not the endpoint. Endpoint close deregisters it and
invalidates its generation. Reactor close invalidates all watches/timers; remove
and cancel remain idempotent. Returned Events contain **application tags only**,
never native identities. Internally queued events carry both registration and
resource generations, and stale events are filtered before return. A returned
event may still refer to application state removed after wait; applications use
their own generation-qualified tags, never reuse a live tag, and recheck state.
Do not confuse that application tag with a registry token.

wait returns an empty batch on deadline, Interrupted only if it cannot continue
under the explicit retry policy, and Cancelled after stop. It checks timers,
wakeup/stop and remaining monotonic time on every interrupted wait. Infinite
wait remains interruptible by wake/stop. Internally eventfd (or nonblocking pipe)
is drained and coalesced; saturation means a wake is already pending, not a lost
shutdown. In v1 wake is useful to C event producers or another process via an
explicit shared wake facility; it does not justify passing a Reactor obo record
to an unsupported thread.

Fairness: cap each returned batch (default 64, maximum 4096), rotate ready
subscriptions, and bound application work per endpoint (recommended 64 KiB or
32 transfers, and 32 accepts per turn). Check due timers each turn, even under
continuous traffic. Disable write interest when there is no queued output;
otherwise writable sockets cause a hot loop. Resource and total pending-byte
caps are independent. Native buffers for returned batches are owned by the wait
operation and copied before reuse.

Reactor state is Open -> Stopping -> Closed. stop prevents new registrations and
wakes waiters; it does not close connections. Graceful application shutdown:
stop accepting, retain existing watches, drain bounded work until a fixed deadline,
then close endpoints and finally reactor. Call `stop` after draining (or use the
application's draining flag); stopping early cancels wait and cannot itself run
a drain loop. No registered obo callbacks: caller iterates Event records.

## 8. Process, signals, environment and optional service helpers

### 8.1 Spawn, pipes and reaping

```obo
module sysprocess
import "sysenv.obx" as env
import "sysdir.obx" as dir
import "sysstream.obx" as stream
import "sysreactor.obx" as reactor
pub type Child = ref record { reserved: int }
pub type Redirect = closed {
    Inherit {}
    Null {}
    Pipe {}
    FromStream { source: ?stream::Stream }
}
pub type Command = ref record {
    executable: string; arguments: []string; environment: env::Environment
    directory: ?dir::Directory; input: Redirect; output: Redirect; error: Redirect
    newGroup: bool
}
pub type ExitStatus = closed {
    Exited { code: int }
    Signalled { signal: int; coreDumped: bool }
}
pub fn spawn(command: &Command): Result[Child]
pub fn closeCommand(mut command: &Command): Result[bool]
pub fn takeInput(child: &Child): Result[stream::Stream]
pub fn takeOutput(child: &Child): Result[stream::Stream]
pub fn takeError(child: &Child): Result[stream::Stream]
pub fn pollExit(child: &Child): Result[?ExitStatus]
pub fn wait(child: &Child): Result[ExitStatus]
pub fn terminate(child: &Child, force: bool, group: bool): Result[bool]
pub fn watchChild(r: &reactor::Reactor, child: &Child,
                  tag: int): Result[reactor::Watch]
pub fn detach(child: &Child): Result[bool]
pub fn close(child: &Child): Result[bool]
```

Command owns its arrays/environment/redirection resources; spawn borrows it and
copies validated argv/env/native launch state before launching. No shell parsing:
executable is an explicit pathname, arguments includes argv[0], NUL is rejected,
and ARG_MAX overflow is a Result. PATH search is a separately named optional
policy; never interpret metacharacters. FromStream sources are duplicated for
the child, not consumed on success or failure. Parent pipe ends are nonblocking;
child standard ends are blocking by default. Take methods transfer each pipe end
at most once; Child close closes untaken parent ends.

Command's Directory and FromStream fields are owned resource shells, so ordinary
record drop is insufficient for them too: `defer closeCommand(command)` closes
those resources, and is idempotent. It does not close previously spawned Child
objects. Applications construct these fields from independent duplicates when
they also need to keep using an original endpoint. The same explicit graph-close
rule applies to any future aggregate containing resource owners.
FromStream.source must be non-nil at spawn; closeCommand takes it into a whole
local and leaves nil. Taking the optional directory works the same way. It tries
every cleanup even if one fails and returns the first failure; repeated cleanup
with no remaining resources returns false. This also avoids borrowing an interior
resource across an impure native call.

Use posix_spawn when it can provide requested actions; otherwise a trusted C
fork/execve sequence with preallocated state, async-signal-safe child actions,
and a CLOEXEC error pipe to distinguish exec failure from exit 127. Handle dup2
collision ordering and close all unintended descriptors, using a close-range
capability or explicit inventory fallback. Failed spawn closes both pipe ends and
reaps any failed child. Child cwd is set using an anchored directory in the child,
not temporary chdir in the parent. Reset signal masks/dispositions to the explicit
spawn defaults. fork must not run arbitrary obo code on this general path.

Public `fork` returning twice into obo is **not** a v1 API. It would duplicate
managed ownership/accounting/output/native state and, after threads, locks with
missing owners. The initial multiworker recommendation uses external spawning
or spawn/exec. A controlled Linux freestanding **prefork launcher** may later
fork before any native library/worker threads, flush output, repair counters,
close disallowed resources, and enter a compiler-known process entry. It needs
its own runtime audit; calling libc fork in a scalar wrapper is not that audit.
exec-replace can be a separate checked API: failure returns err; success never
returns and cannot run defers. Prefer spawn for normal applications.

wait decodes WIFEXITED/WEXITSTATUS/WIFSIGNALED/WTERMSIG/WCOREDUMP, not `status >> 8`
alone. Stopped/continued notifications, if later exposed, are distinct nonterminal
events. pollExit returns nil while running, stores the terminal status and reaps
exactly once. Repeated waits return copied cached status. EINTR is Interrupted
for blocking wait; use reactor + pollExit for bounded waiting. pidfd is preferred
on Linux; fallback tracks only owned children and cannot safely signal an arbitrary
reused PID. Process-group termination applies only to groups created/owned by this
module, never an unchecked negative PID. No public kernel PID authority.

| Child state | Meaning |
|---|---|
| Running | obligation to wait or transfer to reaper |
| ExitedUnreaped (native observation) | reap before publishing final status |
| Reaped | cached ExitStatus; wait is repeatable |
| Detached | module reaper owns wait obligation; user wait is Closed |
| Closed | untaken pipes released; no user operations except idempotent close |

close on Running transfers to the process-context reaper, not kill and not a
blocking wait in defer. detach does the same explicitly. The single-threaded
reaper runs at reactor turns and explicit `reapChildren`; applications without a
reactor must call it or wait directly. At context shutdown, report outstanding
children and require an explicit wait/terminate/detach-to-OS policy. Dropping a
shell without close is a lifecycle leak. No invisible thread or SIGCHLD callback
calling into obo. A detached child may briefly be a zombie until the next reap
turn; tests and documentation must state this scheduling requirement.

### 8.2 Signal delivery and process-global policy

```obo
module syssignals
import "sysreactor.obx" as reactor
pub type SignalSet = ref record { bits: int }
pub type SignalSource = ref record { reserved: int }
pub type Event = ref record { signal: int; count: int }
pub const INTERRUPT = 2
pub const TERMINATE = 15
pub const HANGUP = 1
pub const CHILD = 17
pub const WINDOW_CHANGED = 28
pub fn empty(): SignalSet
pub fn add(mut s: &SignalSet, signal: int): Result[bool]
pub fn remove(mut s: &SignalSet, signal: int): Result[bool]
pub fn subscribe(set: &SignalSet): Result[SignalSource]
pub fn watch(r: &reactor::Reactor, source: &SignalSource,
             tag: int): Result[reactor::Watch]
pub fn next(source: &SignalSource): Result[?Event]
pub fn close(source: &SignalSource): Result[bool]
```

`subscribe`, `watch`, `next` and `close` expose **queued/coalesced events**, never
an obo signal-handler function. SignalSet uses named library constants, not a raw
sigaction layout; `add` rejects SIGKILL/SIGSTOP and synchronous fault signals.
Linux prefers signalfd: block the selected signals before starting workers and
keep them blocked in every thread; one designated consumer drains the queue.
Restore a saved mask on close only under context ownership; overlapping
subscriptions are rejected or reference counted centrally. Synchronous fault
signals SIGSEGV/SIGBUS/SIGFPE/SIGILL are excluded.

A portable self-pipe backend installs a trusted C handler that preserves errno,
sets `sig_atomic_t` pending bits and attempts a nonblocking async-signal-safe write.
No allocation, mutex, print, managed object or C-to-obo callback in a handler.
Pipe-full coalesces pending signals; it must not lose the pending stop bit.
Ordinary signals already coalesce, so this is a notification service, not an
exact event counter. SIGKILL/SIGSTOP cannot be intercepted. Reap all exited
children when handling Child, rather than one per signal.

SIGPIPE policy must be installed explicitly at systems-context startup for pipe
I/O (normally ignore it process-wide and return BrokenPipe). Socket sends use
MSG_NOSIGNAL without changing global state. Do not change process dispositions on
module import. Spawn restores intended child defaults. When hosted libraries
also own signals, incompatible subscriptions return Busy; no handler replacement
behind their backs.
Until a separately reviewed per-call SIGPIPE suppression implementation exists,
new pipe-write APIs return Unsupported before issuing a write when the context
has not enabled that policy. Default signal termination must not masquerade as
a Result-returning BrokenPipe contract.

### 8.3 Environment and cwd

```obo
module sysenv
pub type Pair = ref record { name: string; value: string }
pub type Environment = ref record { entries: []Pair }
pub fn snapshot(): Result[Environment]
pub fn get(e: &Environment, name: string): Result[?[]byte]
pub fn set(mut e: &Environment, name: string, value: string): Result[bool]
pub fn unset(mut e: &Environment, name: string): Result[bool]
pub fn entries(e: &Environment): Result[[]Pair]
```

Validate names (nonempty, no `=`/NUL), values (no NUL) and total size. Missing is
nil, present-empty is an empty byte array. A snapshot owns all content; local
mutation has no process effect. Entries preserve startup order and duplicate-key
policy is explicit at implementation; lookup is first match, like `getenv`. This
hides environment serialization and duplicate-key handling, not arbitrary config.
There is no native resource and no close obligation; dropping the snapshot frees
its strings normally.

Optional `setProcess`/`unsetProcess` are **startup-only**, before any native worker,
resolver or thread; then freeze process environment. libc setenv alone cannot
update runtime::env_base, which still names initial envp (runtime.c:1002–1021).
Until a reviewed bridge makes legacy getenv and hosted libc coherent, process
mutation is Unsupported; child snapshots work without it. This is an explicit
blocker, not two contradictory environments called one API.

`sysprocess::cwd():Result[string]` snapshots current cwd;
`chdir(directory:&dir::Directory):Result[bool]` is startup-only and freezes with
the systems context. Thread-local cwd is not a Unix feature. Prefer anchored
directory operations and Command.directory. Libraries must not chdir as a hidden
side effect. Resource-limit query/update is a separate process policy API with
named limits and checked integers, not arbitrary prlimit numbers; v1 needs at
least open-files and address-space queries for diagnostics.

### 8.4 Terminal and logging

```obo
module systerminal
import "sysstream.obx" as stream
import "sysreactor.obx" as reactor
import "syssignals.obx" as signals
pub type Terminal = ref record { reserved: int }
pub type Size = ref record { rows: int; columns: int }
pub fn open(fd: int): Result[Terminal]
pub fn size(t: &Terminal): Result[Size]
pub fn enterRaw(t: &Terminal): Result[bool]
pub fn restore(t: &Terminal): Result[bool]
pub fn watchResize(r: &reactor::Reactor, t: &Terminal,
                   tag: int): Result[reactor::Watch]
pub fn duplicate(t: &Terminal): Result[stream::Stream]
pub fn close(t: &Terminal): Result[bool]
```

`systerminal` owns an independent standard-stream duplicate (`open` duplicates
the given descriptor with CLOEXEC; it does not take ownership of 0/1/2) and a
saved termios snapshot. `enterRaw` is an explicit restoration obligation with
`defer restore`; `close` restores once and is idempotent. Window size events use
syssignals; `watchResize` is a typed integration, not a second signal handler.
Arbitrary ioctl is not exposed. A terminal UI framework is separate.

```obo
module syslog
import "sysclock.obx" as clock
pub type Logger = ref record { reserved: int }
pub type Level = closed { Debug {} Info {} Warn {} Error {} }
pub type Policy = closed { ReturnWouldBlock {} DropWithCounter {} }
pub type EmitResult = ref record { accepted: bool; dropped: int }
pub fn open(fd: int, capacity: int, policy: &Policy): Result[Logger]
pub fn emit(l: &Logger, level: &Level, message: &[]byte): Result[EmitResult]
pub fn drain(l: &Logger, until: &clock::Deadline): Result[bool]
pub fn flush(l: &Logger): Result[bool]
pub fn close(l: &Logger): Result[bool]
```

`syslog` exposes a Logger with an owned bounded output buffer, level, per-record
limit, and policy (`ReturnWouldBlock` or explicit `DropWithCounter`). `open`
duplicates the descriptor with CLOEXEC and does not take ownership of the
caller's fd. `emit` copies a record atomically into the queue or rejects it;
`drain`/`flush` report sink errors. Escape newline/control bytes in fields; timestamp by realtime and
record latency by monotonic time. No implicit global logger, background thread,
unbounded queue or hidden application secrets. An initial line format is
sufficient; structured JSON needs a separately tested encoder. Reactor must never
log by calling a potentially blocking sink on every error. Multi-process logging
uses a supervisor collector/pipe with defined framing and size caps; writes to a
shared regular file are not magically record-atomic.

## 9. Concurrency decision and the actual thread boundary

### 9.1 Options

| Choice | CPU / isolation / memory | Handler ergonomics and HTTP relevance | Testing and migration |
|---|---|---|---|
| (a) Prefork/process workers first | multiple cores, process fault isolation, independent non-atomic heaps; duplicated/COW pages and per-process native resources | blocking handler is simple but consumes a worker; good deployment boundary, shared cache needs IPC | kill/restart and pipe tests are straightforward; general obo fork is unavailable; spawn/exec avoids inherited lock problems |
| (b) One reactor now | many idle connections on one core, one heap, no allocator change | explicit state machines; blocking DNS/file/CPU work stalls all peers; enough to prove transport/timeouts | deterministic fake time/readiness; endpoint API remains useful inside future workers |
| (c) Full threads + arbitrary shared state now | multicore and shared cache, weakest isolation; allocator/RC/handle/compiler memory model all change | synchronous thread-per-connection is familiar but stack/memory limits and cancellation matter | largest race/test surface and migration risk; wrapping pthread_create/mutex is unsound |
| (d) Hybrid: reactor per process, confined threads later | cores via processes first; later bounded compute/native worker pools; ownership stays local | explicit reactor for I/O, messages for CPU work; no protocol dependency | staged gates; stream/deadline/byte-message contracts survive; application chooses a worker deployment |

**Verdict: choose (d), implemented first as (b) plus externally supervised
processes.** Add spawn/exec supervision in P3. Use prefork only after a focused
runtime/hosted fork audit, never claim it already works. This uses multiple cores
without requiring shared-memory obo values. It preserves failure isolation and
makes deadline/fault tests deterministic. CPU-heavy handlers must go to another
process until the thread stage; starting an implicit thread to hide blocking is
rejected. Full threads (c) are not a first release: they require every item in
§9.4, and arbitrary shared obo state remains excluded even then.

### 9.2 What is impossible today, with the exact reason

Concurrent execution of two arbitrary obo functions is unsafe. The blocking
facts, not general caution, are:

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
- The compiler emits mutable/owned globals at fixed addresses in the
  `0x0C000000` region. Every thread would share one instance, including its
  owned strings/records and initialization state.
- `rt_entry_sp` is written by every runtime stub (entry.S:83–90); output is a
  single static buffer; the legacy `fdstate[1024]` table is static.
- `OBO_EXIT` and the trap paths use raw `exit(60)`, which in a multithreaded
  process terminates only the caller and can leave the process alive with
  corrupt accounting. Hosted `exit()` is process-wide but runs `atexit`
  handlers from a worker.
- The C seam accepts only scalar i64 arguments and returns i32/i64; it has no
  function-pointer parameter, no callback, no C-created obo entry and no TLS
  guarantee. A Stored/captured lambda is call-scoped and cannot be an
  asynchronous entry argument (LANGUAGE.md:1034–1225).
- `fork()` in a process with obo state duplicates one owner graph and one set of
  locks with no owner in the child; it is not an obo thread API. A prefork
  launcher must fork before native libraries/threads exist and then enter a
  compiler-known process entry, as §8.1 requires.

A Mutex record does not repair any of these. C-only helper threads that never
touch obo memory can eventually service native work, but the native registry,
host TLS, completion queue and wakeup still need a thread-safety review. This is
not full obo threading. P1–P3 use helper processes instead and make no new
runtime threading claim.

#### What may be shared if threads are added

| Value / facility | Across confined workers | Reason |
|---|---|---|
| `int`, `byte`, `bool`, `float` by value; serialized bytes | copy | No ownership or hidden header. |
| Immutable rodata string literals and scalar `const`s | read-only share | `rt_str_inc/dec` ignores non-heap addresses; no mutable header. |
| Heap `string`, `[]T`, record/closed value, `Result`, `?T`, interface, function value | **no** | Non-atomic RC, owned graph, call-scoped captures or vtable/box ownership. |
| `^T` handle | **no** | Heap-relative token; wrong arena and check/use race. |
| `File`, `Directory`, `Stream`, `Connection`, `Mapping`, `Worker` shell | **no** | Registry identity and native lifetime belong to one context; use an explicit transfer ticket only. |
| Mutable/owned global, module initializer state | **no** by default | Fixed address and one initialization; per-context globals are compiler work. |
| Native `AtomicInt`/`AtomicBool`, channel endpoints, `SharedBytes` guard | yes, as concrete native types | C-owned state with explicit synchronization; not an obo object. |

The safe rule is: **copy scalars and bytes; never copy an obo ownership token,
a `^T`, or a borrowed reference.** A native resource handoff needs its own
old-owner-invalidation protocol; sending a shell or token is not a handoff.

### 9.3 Recommended first obo thread model: confined workers and copied messages

No generic `Send`/`Sync` markers in the first release. Each obo worker has a
runtime context; all managed values and mutable globals are confined to that
context. Cross-worker messages contain copied bytes and explicit scalar metadata.
The compiler does not pretend arbitrary records are shareable. Native shared
resources are concrete reviewed types, accessed only by validated operations.

Threading is a **hosted-build feature**. The current freestanding static image
has one process context and no host TLS; adding worker threads there would also
require loader/TLS setup that this design does not propose. The hosted cinterop
build already provides libc startup and pthread-capable linking; use that for
threads. Freestanding programs stay single-threaded and use processes.

```obo
module systhreads
import "sysclock.obx" as clock
import "sysplatform.obx" as platform
pub type Worker = ref record { reserved: int }
pub type WorkerEntry = ref record { reserved: int }
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
```

`entry(name)` resolves a **build-registered static entry**, not a code address
or lambda. The link manifest lists each entry name, its fixed obo signature
`fn worker(input: []byte): int`, and a generated table slot. An unknown name is
NotFound; there is no way to construct an entry from an int. The returned
`WorkerEntry` is a build-table descriptor, not a native owner: dropping it is
harmless and it has no close obligation. The entry function
runs with its own arena, stack, globals and native context. It may call ordinary
library code only if that code is in the worker-safe closure (no forbidden
globals, no thread-foreign values); the compiler/build checker must prove that
closure rather than trusting a comment.

`start` **consumes** `input` and every non-nil `options.tickets` entry on every
path (it takes them, leaving nil). It copies the bytes into a bounded native
staging buffer before launching; the worker then allocates its own `[]byte` in
its own arena and frees the staging buffer. This is copying, not moving a pointer
between arenas. Failure frees the input, releases unconsumed tickets and all
partially created native state. A later borrow-and-copy variant can preserve the
caller's array on failure without changing the worker model. A ticket is
single-use and typed; a caller cannot forge a native handle from an int. The
worker obtains a launch ticket with `takeTicket(index)` (valid only inside a
worker, index checked against the launch packet); the ticket is then consumed by
the typed importer, for example `syssync::importSender`. `takeTicket` is the only
way a ticket enters the worker; it is never encoded in `input` bytes or returned
as a raw int.

Worker lifecycle: Starting -> Running -> Finished -> Joined, or Running/Finished
-> Detached -> Reaped. Failed startup returns no worker and releases stack, arena
and mailbox. Join timeout leaves the Worker joinable; successful join/detach is
exactly once; repeated join returns a cached JoinStatus (recommended, like Child)
or Closed as fixed at implementation. `close` on a Finished/Joined worker
releases the shell; on a Running worker it transfers to the supervisor (like
`Child.close`) and does not kill or block. Detach transfers lifetime/accounting
to the runtime supervisor, never discards a still-running context. Process shutdown
cancels and joins/detaches all workers before the main arena is destroyed. No
`pthread_cancel`/asynchronous cancellation: `cancel` sets a C atomic flag, wakes
waits, and the worker checks it at library blocking points or explicitly during
computation. A blocked native call (DNS, a blocking file read, a third-party
library) may not observe cancellation; `join` reports TimedOut, not a lie.

#### Worker entry ABI (no general C-to-obo callback)

The only C-to-obo transition is a runtime-owned bootstrap calling one
build-validated entry label. `pthread_create` receives the address of a **runtime
C function**, `rt_worker_bootstrap`, never an obo function pointer, a lambda, a
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
borrowed arguments, no captured values and no `Result` return, so a bad entry
cannot smuggle a pointer or a second ownership graph across the boundary. A
`Result` would have to be resolved into an int status and copied bytes before
return.

`rt_worker_finish` does not free the worker arena or the obo stack while the
bootstrap is still using them. It records status, marks the context Finished,
signals the joiner and returns; `pthread` releases the C stack, and the joiner (or
detached supervisor) releases the obo stack and arena only after the thread has
terminated. A normal worker return runs the entry function's own defers and frees
its locals before `rt_worker_finish`. A trap/OOM is process-fatal (below), so
there is no half-unwound context to join.

This is deliberately narrower than "call any obo function on a new thread". It
is enough for a worker pool or a compute job; it is not a general callback
facility, a coroutine runtime or thread-per-connection with arbitrary shared
state.

### 9.4 Required runtime/compiler work, in implementation order

| Work | Exact obligation and risk |
|---|---|
| Entry ABI and validation | A runtime-owned C/assembly bootstrap calls only a build-registered entry label (9.3). `pthread_create` receives `rt_worker_bootstrap`, not an obo pointer. The build table records name, signature and code slot; unknown/duplicate names fail the link. No general C-to-obo callback, no lambda/capture, no app C callback. Raw `clone` is rejected for hosted builds: it does not set up glibc TLS, loader state or pthread bookkeeping. |
| Entry execution context | Split `rt_init` into process initialization, worker initialization and worker teardown. A worker must not rerun process initializers, `atexit`, argv parsing or module side effects. It gets an immutable argv/env snapshot, its own context and its own arena; it must not call process `rt_finish`. Register active workers in a native list for diagnostics/shutdown. |
| Allocation / arena | Confinement is the recommendation: each `RtCtx` owns `heap_start/top/end`, starts bitmap, free lists, `mstack/msp/mcap`, temporary roots and counters. Worker arenas are separate `mmap` reservations of `options.heapBytes` (default 64 MiB, checked minimum 64 KiB, aggregate cap), not 4 GiB each. The starts bitmap and worklist scale with the worker arena. The joiner destroys the arena only after the thread has terminated and no native operation can refer to it. A shared allocator would require locks/atomics on every allocate/free path, reentrancy review and scalable worklists; one malloc lock is insufficient. |
| Refcounts | Confinement keeps the existing non-atomic string RC correct only if no heap string/array/record is shared, including through globals or library caches. Copy bytes on transfer; never transfer an RC pointer. If shared immutable strings are ever required, use atomic inc/dec with release/acquire destruction and an overflow policy, plus arena lifetime protection; atomic RC still does not make mutable records safe. |
| Globals and generated addressing | Current mutable/owned globals live at fixed `0x0C000000` addresses and are shared. Two staged options: (T1) reject a worker entry whose transitive call closure reads or writes mutable/owned globals; immutable scalar/rodata constants may be shared. (T2) generate context-relative global access (recommended implementation: a fixed TLS offset holding the context's globals base, emitted at each global access) and initialize/destroy per-context globals in dependency order. T1 is the smaller sound first step; T2 is an ABI/codegen change, not a library flag. Do not rerun module initializers with external side effects in every worker. |
| Handles | The current `^T` is a 32-bit heap-granule offset plus a 32-bit generation (runtime.c:474–493); it has no context tag and assumes one heap. No `^T` crosses a worker boundary. Each context decodes against its own arena. If defense-in-depth against a forged cross-context handle is required, adding an arena tag needs a reviewed handle-ABI change; do not silently steal generation bits. Check/use must not race a free even within one context. |
| Compiler escape/effect boundary | Validate the static entry signature; reject borrowed values, `^T`, interfaces, function values, optionals of owned values and any thread-foreign type at the entry and channel boundaries. Introduce backend-owned worker-entry descriptors without changing the scalar C interop contract. Native effects remain conservative. `.sym`/ABI consequences and the 2,500,000-byte compiler budget need independent review. |
| TLS and errno | Hosted libc requires real per-thread TLS. Use pthread-created threads so libc initializes it. Store `RtCtx *` in a `__thread` variable (or a reviewed fixed TLS slot) and load it at every runtime entry. C adapters capture `errno` immediately after each libc call into a scalar before any other call; raw syscalls already return negative errno and need no libc errno. Never expose TLS addresses. `getaddrinfo`, `strerror`, `locale` and third-party libraries must be audited for thread safety; an unsafe library is confined to one worker or protected by a C lock, not assumed safe. |
| Signals | Signals and dispositions are process-wide. Block service signals before creating workers; every worker inherits the mask; one designated signalfd/pidfd consumer drains them. No obo code runs in an asynchronous handler. `SIGPIPE` is ignored process-wide only by explicit systems-context startup; socket sends use `MSG_NOSIGNAL`. `SIGSEGV`/`SIGBUS`/`SIGFPE`/`SIGILL` remain fatal. Do not install a `SIGCHLD` handler that calls obo. |
| Output, registry and fds | Output buffers and `rt_entry_sp` become context-local. Each worker flushes its own buffer through a C mutex around `write(2)`; no obo code runs while holding it. File descriptors are process-wide: each context owns its own descriptors; never close a shared duplicate. The new native registry is per-context; a resource transfer ticket atomically invalidates the source shell and installs a new shell in the destination, with kind/generation rechecked. Standard streams are duplicated per worker (stdin sharing is explicitly unsupported unless the application coordinates it). |
| Traps and termination | First thread release: an obo trap or managed OOM is **process-fatal** via `exit_group` (or hosted process abort) with a minimal diagnostic. It must not use thread-local `exit(60)` and must not fabricate a successful join or run partial defers. Normal worker return runs its own defers and frees its locals, then tears down only that worker. Split `OBO_EXIT` into worker-finish versus process-exit; after joining/detaching workers, main's normal finish also uses `exit_group` so a detached worker cannot outlive the process image. Catchable thread traps are a later design. |
| Memory model | Obo accesses are worker-confined by construction. Publication of native byte messages and completion flags uses C11 atomics with release/acquire; join observes prior worker completion. All shared native state uses C atomics/locks. Do not advertise a data-race-free shared-obo memory model until the language can enforce one. |

StackBytes: validate a page multiple, guard pages, a minimum sufficient for the
bootstrap and known runtime stack use, and a configured process total; default
1 MiB is a starting measurement, not a proven bound. No growable stacks. Overflow
is fatal with guard detection. HeapBytes is carved from an aggregate managed
quota; native messages, stacks and metadata have separate caps/counters. Thread
creation `EAGAIN`/`ENOMEM` is a Result; managed OOM/traps follow the fatal model.
The compiler may not exceed the 2,500,000-byte budget to land this phase; if it
cannot fit, stop and revise scope.

### 9.5 Synchronization: what can a Mutex protect?

A Mutex protects **a shareable storage location**, not an arbitrary object simply
because an application promises to lock. With 0.11's aliasable borrows and exposed
fields, `Mutex[T]`, returning `&T` from lock, or putting `^T` in a shared record
would be invented/unsafe APIs. There are no generic user types or Send/Sync
markers today. Recommend v1 **message passing only for obo application state**,
plus concrete native shared scalar cells. A C mutex may protect C registry/queue
memory internally before any public sync module exists.

| Primitive | Proposed contract / permitted storage | Availability |
|---|---|---|
| Mutex | nonrecursive native ownership lock for reviewed C-owned cells; lockUntil, tryLock, unlock; owner-checked, close Busy while held/waited on | internal first; public raw lock only after a shareable-cell API exists |
| Condvar | wait atomically releases/reacquires its associated native mutex; predicate loop mandatory; spurious wakes allowed; monotonic deadlines | internal channel implementation first; no obo predicate callbacks |
| RwLock | C-owned snapshot cell; writer-preference/no starvation objective; no lock upgrade, no references escaping read lock | later concrete SharedBytes store, not arbitrary records |
| Once | C-owned initialization state Unset/Running/Ready/Failed; recursion is error; failure cached or explicitly reset before use | native package initialization only; no retained obo lambda |
| Atomics | concrete AtomicInt/AtomicBool in C registry; load/store/exchange/fetchAdd/compareExchange, checked/wrapping policy named | after thread-safe registry; default sequential consistency, no address casts |
| Channels | bounded byte-message queue with explicit sender/receiver endpoint ownership, close, deadline and wake integration | recommended first public synchronization |

For a later **concrete C-owned SharedBytes** extension, these are possible
0.11-shaped headings in syssync (not arbitrary obo-value guards):

```obo
module syssync
import "sysclock.obx" as clock
pub type SharedBytes = ref record { reserved: int }
pub type Mutex = ref record { reserved: int }
pub type Condvar = ref record { reserved: int }
pub type RwLock = ref record { reserved: int }
pub type Guard = ref record { reserved: int }
pub type Once = ref record { reserved: int }
pub fn sharedBytes(initial: &[]byte, limit: int, readers: bool): Result[SharedBytes]
pub fn mutex(cell: &SharedBytes): Result[Mutex]
pub fn rwlock(cell: &SharedBytes): Result[RwLock]
pub fn lock(m: &Mutex, until: &clock::Deadline): Result[Guard]
pub fn readLock(m: &RwLock, until: &clock::Deadline): Result[Guard]
pub fn writeLock(m: &RwLock, until: &clock::Deadline): Result[Guard]
pub fn snapshot(g: &Guard): Result[[]byte]
pub fn replaceBytes(g: &Guard, bytes: &[]byte): Result[bool]
pub fn unlock(g: &Guard): Result[bool]
pub fn condvar(m: &Mutex): Result[Condvar]
pub fn waitCondition(c: &Condvar, g: &Guard,
                      until: &clock::Deadline): Result[bool]
pub fn notify(c: &Condvar, all: bool): Result[bool]
pub fn once(limit: int): Result[Once]
pub fn beginOnce(o: &Once): Result[bool]
pub fn publishOnce(o: &Once, bytes: &[]byte): Result[bool]
pub fn failOnce(o: &Once): Result[bool]
pub fn readOnce(o: &Once): Result[[]byte]
```

A SharedBytes cell owns **one** lock, selected as mutex or rwlock at construction;
asking for the other kind is InvalidArgument. Returned lock endpoints refer to
that same native lock, never attach a second independent lock to the cell.
Guard is checked for current worker, cell, generation and read/write mode on
every operation. snapshot copies; replaceBytes requires a write guard and is
all-or-nothing within the cap. Register `defer unlock(guard)`; no guard crosses
workers. Closing a cell/lock with guards or waiters is Busy, not destruction of
live pthread/futex storage. Lock endpoints and Once also require explicit close
functions with the common state contract; these headings omit those repetitive
declarations, not their ownership obligations.

waitCondition requires the associated mutex guard, releases/reacquires it
atomically with waiting, and returns with the guard held, including timeout or
interruption. `ok(true)` means notified, `ok(false)` means the deadline expired,
and `Interrupted`/`Cancelled` are errors with the guard still held. **Reacquisition
may finish after the condition deadline**; the deadline bounds the condition
wait, not arbitrary lock-holder execution. Predicates must be rechecked from a
copied snapshot in a loop. notify does not assert a predicate is true. Once.begin grants initialization to exactly one worker;
Running returns WouldBlock, Ready returns false, Failed returns its cached error.
The initializer calls publishOnce with copied bytes or failOnce on every exit;
no stored lambda. A missing publish is a lifecycle error visible in diagnostics.
This extension stays deferred until a real consumer needs shared native bytes.

Concrete future channel sketch (after P4):

```obo
module syssync
import "sysclock.obx" as clock
import "sysplatform.obx" as platform
pub type Sender = ref record { reserved: int }
pub type Receiver = ref record { reserved: int }
pub type Channel = ref record { sender: ?Sender; receiver: ?Receiver }
pub type AtomicInt = ref record { reserved: int }
pub type Exchange = ref record { changed: bool; previous: int }
pub fn bytesChannel(maxMessages: int, maxBytes: int): Result[Channel]
pub fn send(s: &Sender, data: &[]byte, until: &clock::Deadline): Result[bool]
pub fn receive(r: &Receiver, until: &clock::Deadline): Result[?[]byte]
pub fn exportSender(s: &Sender): Result[platform::Transfer]
pub fn exportReceiver(r: &Receiver): Result[platform::Transfer]
pub fn importSender(t: platform::Transfer): Result[Sender]
pub fn importReceiver(t: platform::Transfer): Result[Receiver]
pub fn closeSender(s: &Sender): Result[bool]
pub fn closeReceiver(r: &Receiver): Result[bool]
pub fn exportAtomic(a: &AtomicInt): Result[platform::Transfer]
pub fn importAtomic(t: platform::Transfer): Result[AtomicInt]
pub fn atomicInt(value: int): Result[AtomicInt]
pub fn load(a: &AtomicInt): Result[int]
pub fn store(a: &AtomicInt, value: int): Result[bool]
pub fn compareExchange(a: &AtomicInt, expected: int, value: int): Result[Exchange]
```

send copies all bytes before publication; full queue is WouldBlock for expired/
zero wait, TimedOut at a finite later deadline. Failure publishes nothing and
caller retains its array. receive copies into the receiving arena, returning nil
only after all senders close and queued messages drain. Receiver close rejects
new sends and frees queued native bytes; blocked calls wake with Closed/Cancelled.
Bound both message count and aggregate byte count. No arbitrary []T channel.

Cross-thread Sender/Receiver/AtomicInt use **explicit export/import tickets** in
the runtime launch packet (`StartOptions.tickets`; retrieved in the worker with
`systhreads::takeTicket(index)`), with checked native reference ownership; the
obo shell itself never crosses. Each side gets a new local shell. `exportSender`
and `exportReceiver` add a native endpoint reference and return a single-use
ticket; the source endpoint remains usable. `importSender`/`importReceiver`/
`importAtomic` consume a ticket and return a new local shell; a ticket cannot be
imported twice, and an unused ticket is released with
`sysplatform::releaseTransfer`. Endpoint duplication increments native ownership,
not an obo reference count. Tickets are typed runtime transfer records, not public
token integers or bytes a user can forge into native handles. This transfer
service must land before public channels; the headings alone do not implement it.
Worker input remains ordinary copied bytes for business data. Channels intended
across the boundary are created before `start` and their endpoints exported into
`StartOptions.tickets`; a worker-created endpoint cannot be returned through
`JoinStatus` (which is only an int status) in v1. Same-process reactor owners can
use corresponding nonblocking channel step/watch methods without rewriting
byte-message semantics.

SharedBytes, if added, stores bytes in C and returns copies under locks; it never
returns `&[]byte`. Threads may **copy** int/byte/bool/float values and serialized
bytes; they may not share managed strings, records, arrays, Result cells, borrowed
references, function cells, interfaces, handles, mutable globals, File/Connection
shells or mapping views. Sending a stream requires a separately specified native
resource handoff with old-owner invalidation; it is not enabled by sending a token.

Send/Sync-like markers become necessary only if the product later chooses general
shared/transferable obo values. They need structural type classification,
global/effect tracking, exclusive access or guard enforcement, atomic/shared
lifetime machinery and ABI rules. The marker names alone are not the design.

**Synchronization verdict.** Do not ship `Mutex`/`Condvar`/`RwLock`/`Once` as
guards over arbitrary obo values in 0.11. Ship bounded byte channels and concrete
native `AtomicInt`/`AtomicBool` first, because their shared storage is C-owned and
their operations can be checked. A later `SharedBytes` gives lock-protected
native bytes with copy-in/copy-out, not `Mutex[T]` and not a returned `&T`.
`Send`/`Sync`-like markers are a language design project, not a library
convenience; until then, message passing is the only supported way to move
application state between workers. A `Mutex` record can protect a C registry cell
internally, but it cannot make an obo record safe to share.

## 10. HTTP readiness assessment (no protocol design)

Phases refer to §13. “Foundation” means a facility exists for a future HTTP
implementation, not that an HTTP server or protocol is supplied.

| Need | Baseline | Supplying module / phase | Remaining assessment |
|---|---|---|---|
| Listen/accept, local/peer address | absent | sysaddress/sysnet, P2 | numeric TCP/Unix listener, CLOEXEC and accept pressure policy |
| Nonblocking reads/writes, backpressure, half-close | raw fd partial writes only | sysstream/sysbuffer/sysnet, P2 | bounded queues and retained progress; throughput of scalar bridge must be measured |
| Timers, idle/read/write/connect timeouts | sleep only | sysclock P1; sysreactor/syswaitio P2 | absolute monotonic deadlines, cancellation and fairness |
| TLS | no stable adapter | optional transport adapter P2T after P2 + hosted interop hardening | certificate/key loading, hostname verification for clients, handshake/read/write/shutdown state and want-read/want-write; C callbacks queue events only; no pointers/SSL structs escape |
| Concurrent connections | single blocking program | reactor P2 | one-core event loop, bounded state machines |
| CPU utilization / isolation | single process/thread | externally supervised P2 workers; sysprocess P3 | multi-process scale first; P4 confined workers only after runtime gates |
| Shared application state/cache | single-thread-local only | per-process owner + IPC P3; byte channels P5 | no arbitrary shared obo object graph; no present Mutex workaround |
| Compression | experimental in-memory zlib example | optional bounded streaming codec P2C | incremental input/output, output quota, expansion-ratio/CPU limits; protocol negotiation separate |
| DNS | absent | resolveBlocking P2; reactive helper P3/P4 | not needed for numeric server bind; NSS blocking cannot hide inside event loop |
| Graceful shutdown | exit skips defers | reactor drain P2; signals/reaping P3 | stop acceptance, fixed drain deadline, flush/close, join/reap, normal return |
| Logging / telemetry | print/eprint and heap stats | bounded syslog P1/P2, process collector P3 | error-returning sink, queue/drop counters; no blocking print dependency in hot reactor |
| Static/config files | limited read/stat | sysfile/sysdir/sysstat P1 | anchored paths and bounded reads; storage may still block |
| Secure random bytes | absent | sysrandom P1 | OS CSPRNG with explicit blocking policy |
| HTML serialization | `html.obx` escaping helpers | existing module, no systems dependency | escapes text/attributes/URLs; does not parse requests, frame responses or enforce a template policy |
| HTTP request parsing, response framing, routing | absent | **not in this design** | separate protocol design; needs bounded buffers and byte streams, not new OS primitives |
| HTTP/1.1, HTTP/2, HTTP/3, TLS negotiation | absent | **not in this design** | separate protocol/transport designs above sysnet/sysstream; no claim of readiness from this layer |

TLS/codec adapters require their own detailed designs and conformance reviews;
they can be placed above stream/readiness without importing HTTP. A TLS engine
may own the native connection or use C-side memory BIO buffers with explicit
feed/drain; it must not request an obo callback for transport I/O. Its result must
distinguish “need read/write” from fatal TLS error. Do not market P2 as a secure
Internet server while this dependency is absent. Native helper threads used by
third-party libraries still require host TLS/signal/fork review even if obo stays
single-threaded.

## 11. Testing, fault injection and observability

### 11.1 Deterministic model versus actual Linux

Two implementations of the same native adapter contract:

1. **Scripted fake backend**: operation ordinal/resource kind, errno/category,
   bytes transferred, fake monotonic/realtime values, readiness events and close
   outcomes. It owns fake registry entries, not real fds. Build selects it; no
   production environment variable can inject faults. Advance time explicitly
   on waits; readiness can be spurious or continuously ready. No timing sleeps.
2. **Linux integration backend**: private temporary directories, loopback sockets,
   Unix sockets, pipes/pty and child fixtures. Use watchdogs and randomized port
   allocation, no external DNS/network service in the default suite. Verify
   both epoll and poll contracts where supported; unsupported features are
   explicit capability outcomes, not silent test skips.

Fault injection cases: EMFILE/ENFILE before and during accept/spawn; EINTR storms
before progress and after earlier partial progress; EAGAIN after a readiness
event; short reads/writes including vector boundaries; ENOMEM at every native
allocation/registry grow; ENOSPC/fsync/close errors; stale/double/wrong-kind close;
delayed completions after cancellation/owner close; generation retirement;
malformed/oversized packets; child exec failure versus genuine exit 127.
For fake close EINTR, close the fake underlying descriptor too, so the test models
Linux consumption rather than manufacturing a leak (legacy fault_close's behavior
must not be copied blindly).

State-table tests assert invariants, not only returned strings: one native release,
no fd reuse confusion, bytes neither duplicated nor lost, unchanged absolute
deadline, bounded memory, no callback into obo, cached reaping result, no event
dispatch to a removed generation. Every failure of a compound operation checks
all partially acquired resources. Error-path retained buffers and ownership moves
need compiler-negative tests as well as runtime tests.

### 11.2 Resource pressure and native diagnostics

- RLIMIT_NOFILE tests with a very low soft limit, inherited descriptors above
  1024, standard descriptors initially closed, and exec inheritance checks.
- RLIMIT_AS and low OBO_HEAP_LIMIT distinguish native ENOMEM, arena reservation
  failure and managed fatal OOM. Test the 4/16 GiB virtual reservations and
  per-process/native quotas separately. cgroup memory.max/pids.max tests run in
  an available isolated harness; record unavailable facilities, do not modify
  host-wide limits. Record OOM-kill as external termination, not a Result.
- ASAN/UBSAN for C adapters, registry and byte codecs; malformed packet fuzzing,
  size/offset overflow fuzzing, path/address/time parsers, stateful close/reopen
  and event-cancellation sequences. Sanitizer binaries may need a separate C
  harness because the hosted fixed-address layout can conflict with sanitizer
  mappings; never claim instrumentation that did not run.
- TSAN when any native worker/registry is concurrent; after P4 also instrument
  runtime C plus appropriate generated-code memory accesses. TSAN on C alone
  cannot prove obo code race-free. Add forced scheduling/litmus tests for
  publication, join, close/use, Once failure, condvar spurious wakeups and channel
  shutdown. Stress tests supplement, not replace, deterministic schedules.
- Descriptor/socket/mapping/child/thread/registry/buffer live counts, bytes and
  high-water marks per context. Deliberately omit a close to prove the oracle
  detects it. Native counters are separate from OBO_CHECK managed/fd counters;
  current fdstate cannot see C-owned fds. Fail test teardown on either leak.

### 11.3 Thread and synchronization gates

These are gates for the later thread phase, not claims about the current build.

- **Entry ABI:** a link-time table test rejects unknown/duplicate entry names and
  a non-`fn worker(input: []byte): int` signature. Disassembly or a marker test
  proves `pthread_create` receives `rt_worker_bootstrap`, not a user pointer.
  The worker sees `rsp % 16 == 8` at entry and the input array at `[rbp+16]`;
  after normal return the joiner observes the int status, the entry's defers ran,
  and the arena was freed once.
- **Arena isolation:** a worker allocates/frees thousands of objects while the
  main context does the same; no free-list, counter or starts-bitmap corruption.
  A test that tries to pass a `^T`, `string`, record, array or `Result` across the
  boundary must be rejected by the compiler; a native ticket test must invalidate
  the source shell and recheck kind/generation in the destination.
- **Refcounts:** with confinement, deliberately share no heap string. If a later
  shared-string mode is added, a litmus test must cover concurrent inc/dec,
  final release, overflow and arena destruction; TSAN alone cannot prove the
  generated obo accesses.
- **TLS and errno:** two workers force different `errno` values through a C
  adapter and read them back independently; `rt_ctx` resolves per thread. A
  hosted library that is not thread-safe is either confined or protected.
- **Signals and traps:** every worker inherits the blocked service mask; one
  signalfd consumer receives a coalesced event; `SIGPIPE` policy is explicit.
  A worker trap exits the whole process via `exit_group`; the test asserts no
  fabricated successful join and no partial worker teardown.
- **Output:** concurrent `print`/`eprint` records do not tear within a record;
  the C mutex is never held while obo code runs. Detached worker completion
  releases its arena; shutdown cancels/joins or explicitly detaches every worker.
- **Sanitizers:** TSAN on native adapters, registry, queues and C bootstrap;
  generated obo code is not TSAN-instrumented, so combine TSAN with confinement
  tests and litmus/forced-schedule tests. ASAN/UBSAN run in a separate C harness
  if the fixed-address image conflicts with the sanitizer mapping.

### 11.4 Conformance and operational observations

Compare file contents/permissions/rename visibility with Python os/coreutils;
metadata and symlink behavior with Python stat; RFC3339/profile dates with an
independent Python/Go model (explicitly exclude differing leap/offset policies);
TCP/UDP/half-close/timeout behavior with Python or Go socket peers. Compare
protocol-independent bytes, statuses and state, not incidental errno prose.
Use fake clocks for clock jumps and overflow, real clock tests for monotonicity
and lower-bound sleep only with broad scheduler tolerances. Fuzz directory/metadata
conversion independently of the live kernel's well-formed records.

Observability snapshot: live resources by kind, native bytes, queued bytes,
rejected-by-limit counts, interrupted/would-block counts, partial transfers,
reactor wake/batch/timer lag, open children/workers, cleanup failures, dropped log
records. Never dump payloads/tokens/keys by default. Correlate using application
tags. Include RSS/virtual size externally; OBO_STATS alone excludes native memory.
Benchmark small-file tools, byte-copy bridge, thousands of idle/active sockets,
fairness under one hot connection, and per-process memory. Report compiler peak
separately, with the existing budget and empty-array oracle unchanged.

## 12. Packaging, compatibility and repository gates

### 12.1 Layout and names

Proposed source layout after staged implementation:

```text
obo/lib/                         existing eleven modules stay at their paths
obo/lib/sys/
  syserrors.obx  sysplatform.obx  sysclock.obx   syscalendar.obx
  sysrandom.obx  sysstat.obx      sysdir.obx     sysfile.obx
  sysmapping.obx sysstream.obx    sysbuffer.obx  syswaitio.obx
  sysaddress.obx sysnet.obx       sysresolve.obx sysreactor.obx
  sysenv.obx     syssignals.obx   sysprocess.obx syslog.obx
  systhreads.obx syssync.obx      systerminal.obx              (later)
obo/experimental/systems/        reviewed native adapters, .cbind, fake backend
obo/tests/systems/               obo roots and native/model fixtures
obo/examples/systems/            bounded copy, atomic replace, echo, supervisor
```

Application: `import "sys/sysfile.obx" as file` with `-I obo/lib`; sibling module:
`import "sysdir.obx" as dir`. Unique declared names `module sysfile`, etc. satisfy
the basename rule and the **flat .sym namespace**; do not create another
`module time`, `io` or `env` under a subdirectory (LANGUAGE.md:1257–1288,1383–1392).
No re-export/facade pretending `module os` can aggregate imports. CamelCase
functions, PascalCase types and uppercase policy constants follow existing style.

Experimental source may initially live alongside adapters under
`obo/experimental/systems/lib/` with the **same unique module basenames**; move to
`obo/lib/sys/` only when the subset is approved. Applications use the staged
include root, never import both copies. Document stable versus experimental
per module and per capability. This note adds no empty API files merely to reserve
names. Native dependencies are explicit build-manifest inputs; a plain `obo`
compile is not falsely advertised as a full native systems link.

### 12.2 Compatibility policy

Legacy builtins/fs/io/env/time continue to mean what they mean now. Additive
modules do not reinterpret legacy positive errno codes, eza exit constants or
borrowed-fd ownership. Migration is explicit at a resource-creation boundary;
do not let legacy closeFile and new registry close both own one descriptor.

Stable contracts include partial-I/O behavior, cleanup obligation, deadlines,
capacity bounds, error categories and field-independent resource identity.
Changing any of these is a breaking library change even if function spelling
does not change. Experimental APIs may change with a migration note; stable
ones require deprecation/major-compatible release policy agreed at release time.
During pre-1.0, do not use “minor version” as permission for silent breaks.
Pure library additions need no compiler version reservation; .sym hashes track
source interfaces. New compiler grammar/effects or runtime layouts require the
existing exact-match version/ABI policy, reviewed when implemented, not numbers
allocated here. Native manifest formats stay independently versioned.

### 12.3 How modules are actually tested here

- [imports.py](../tests/imports.py):131–185 has tiny importer roots and an exact
  eleven-file `WANT_LIB` check; it currently lists **only the top level**. Add a
  separate explicit recursive systems inventory/import matrix, dependency-cycle
  and duplicate-name tests; retain the original top-level checks and tool-decoy
  tests. Add native-link roots rather than pretending ordinary compile tests
  execute `__cinterop`.
- [stdlib.py](../tests/stdlib.py):6–27,45–56,98–177 discovers headerless roots,
  checks `// expect:` / `// expect-exit:` / compile-error markers and pinned
  EXPECTED_ROOTS/MODULES, runs OBO_CHECK=1 plus poison, `-I obo/lib`, private
  `--sym-dir`. Add pure systems tests here or an equivalent explicit systems
  runner; update pinned inventory deliberately so headers cannot hide tests.
- [doc_check.py](../tests/doc_check.py):1–26 checks **LANGUAGE.md only**. This
  design's proposed headings are not runnable documentation tests. Once a module
  exists, put complete supported examples into the checked reference or a named
  systems example harness. Do not claim doc_check validated this design's APIs.
- Run `tests/run_fork.py`, `tests/module_test.py`, `tests/imports.py`,
  `tests/stdlib.py`, `tests/doc_check.py` for integration as appropriate. Existing
  `tests/io_test.py`, `tests/meta_test.py`, `tests/heap_test.py`, `tests/elf_test.py`
  and tool differential gates protect runtime/legacy behavior. Native systems
  tests also use the cinterop build/verify flow and negative sidecar/ABI cases.
  Current `--check` **rejects native calls** (obo.obo:6493); retain that expected
  diagnostic until a reviewed metadata seam exists. Fake public-signature modules
  can check client syntax, but cannot prove native effect/link correctness.
- Any optional compiler/runtime change adds relevant ownership, symbol, ABI,
  homes/register-mode, mutation and port_compare/self-host budget gates. Measure
  the fixed point and <=2,500,000-byte compiler live peak; no cap relaxation.
  Docs-only review uses link/anchor, syntax-contract and diff checks, not a claim
  that unimplemented systems tests have passed.

## 13. Phases, dependencies and rough effort

Estimates are **engineer-weeks**, including tests/docs and one review cycle,
assuming a maintainer familiar with obo/native ABI. They are planning ranges,
not a schedule commitment; hosted interop stabilization is a separate dependency.
Each phase has a usable exit point and can stop without exposing later APIs.

| Phase | Scope / useful exit | Dependencies / blocked work | Rough effort |
|---|---|---|---|
| P0: boundary proof | native identity registry with no public tokens, status mapping, bounded byte bridge, fake backend, leak oracle; benchmark bridge | existing experimental cinterop; hosted TLS/errno qualification for libc calls; no compiler additions | 2–3 |
| P1a: smallest useful slice | owned File/open/read/write/close, anchored Directory, Metadata, seek/truncate, monotonic/realtime clock, bounded buffers, getrandom; bounded file-copy example | P0; explicit close and managed OOM limitations; pure calendar/parser tests can proceed independently | 3–5 |
| P1b: practical filesystem | rename/link/mkdir/chmod/sync and atomic replace reports, RFC3339, env snapshots, basic bounded logging; configuration-update example | P1a; openBeneath capability tested separately; mapping is optional P1b extension with SIGBUS contract | 2–4 (+1–2 mapping) |
| P2: network/reactor | numeric addresses, TCP/UDP/Unix sockets, partial/vector I/O, level readiness, timers/wake/fairness, bounded echo and timeout demos; blocking DNS clearly named | P1a; native bridge performance gate, no thread requirement | 4–7 |
| P2T/P2C: optional adapters | TLS transport and streaming compression readiness, not HTTP | separate adapter designs; production hosted boundary/TLS library validation | 3–6 TLS; 1–3 codec, independently scoped |
| P3: processes | spawn/exec/wait, pipes/redirection, process groups, reaper, signals, cwd policy, resolver helper and supervisor example | P2 for nonblocking supervision; process env mutation blocked on coherent env bridge; controlled prefork optional separate audit | 4–6 |
| P4: confined threads | worker-entry ABI, per-worker contexts/arenas, TLS/masks/termination, startup/join/detach/cancel, copied worker input; **T1** worker-safe closure with no mutable/owned globals; **T2** context-relative globals only if T1 blocks a real consumer | **blocked on all runtime/compiler obligations in §9.4**, review and compiler memory gate; not implementable as a C shim alone; hosted build only | 8–14+ (T2 adds 2–4), design risk high |
| P5: synchronization | native endpoint tickets, bounded byte channels, atomic scalar cells, wake integration; Mutex/Condvar internals; later concrete shared byte store | P4 + thread-safe native registry; arbitrary shared obo values still excluded | 3–6; general Send/Sync excluded from estimate |

Implement P1a before networking: it improves ordinary programs immediately and
proves resource/error/copy contracts. P2 may use externally supervised worker
processes without waiting for P3. P3 gives libraries explicit subprocess control.
Threads then synchronization are separate deliverables; native locks needed to
implement P4 are internal mechanisms, not early public shared-value promises.
Terminal raw-mode work is a demand-driven 1–2 week extension after signals, not
a prerequisite for networking. No HTTP module is hidden in any phase.

Phase acceptance requires normal success, bounded failure, omitted-close oracle,
documentation, current-language syntax and an independently understandable
manifest. Do not stabilize a module whose only working path exceeds its stated
capacity or requires an unreviewed compiler primitive. After P2, benchmark whether
scalar copying is good enough; optimize only on evidence. Preserve the public
stream/byte/deadline contracts if the backend later gains threads or bulk copy.

## 14. Rejected alternatives

1. **One giant os module / util bucket:** unrelated secrets, unavoidable coupling
   between clocks, file transactions, process policy and network state.
2. **Raw fd/u64/token exposure:** obo has int, not a public u64 pointer type;
   integer reuse loses ownership, checked C tokens are implementation details,
   and public record fields are not opaque. Use identity-backed resource shells.
3. **One-to-one syscall mirrors:** export Linux ABI quirks, retry rules and
   lifetime composition to every caller. Keep thin wrappers only inside adapters.
4. **Callbacks into obo from C, including signal/SSL callbacks:** unsupported ABI
   and stack/ownership context. C queues events; explicit polling enters obo
   normally. Dedicated future runtime worker bootstrap is not general FFI callbacks.
5. **“Just pthread_create plus Mutex”:** allocator, RC, globals, handles, TLS,
   entry ABI and process-exit semantics remain unsafe.
6. **Goroutine-style port / implicit thread spawning:** requires scheduler,
   growable stacks, blocking-call interception and different cancellation/lifetime
   rules. A resolver or buffered writer must not create hidden execution contexts.
7. **Hidden retries / timeout restart:** EINTR and EAGAIN are control flow with
   explicit policy; one absolute deadline survives every retry and partial transfer.
8. **GC-dependent resource cleanup or finalizers:** obo has no collector, record
   drop does not close C resources, and resource exhaustion cannot await GC anyway.
9. **General generic Mutex[T]/Channel[T] or Send/Sync labels without semantics:**
   invents unsupported language and fails to define shareable storage/alias rules.
10. **Zero-copy pointers across scalar interop / borrowed mapping slices:** violates
    the trusted scalar boundary and 0.11 reference restrictions. Copy first;
    reviewed optimization later.
11. **Make legacy builtins transparently safe/nonblocking:** silently changes retry,
    fd ownership, error and buffering contracts used by tools/compiler. Migrate
    explicitly rather than changing their meaning in a library design.

## 15. Open product choices and recommendations

Only choices that change product scope remain open; implementation obligations
above are not optional votes about correctness.

| Choice | Recommendation | Consequence of choosing otherwise |
|---|---|---|
| Default service concurrency | reactor per supervised process; spawn/exec supervisor before controlled prefork | prefork default needs a runtime/native-library fork audit before advertising it; thread-per-connection needs P4 first |
| Threads now versus later | ship P1–P3 first; validate workload demand before P4 | prioritizing threads brings the 8–14+ week ABI/runtime effort and budget risk forward |
| First thread scope | T1: confined workers, copied byte messages, no mutable/owned globals in the worker closure | T2 context-relative globals is more general but adds compiler/ABI work; arbitrary shared obo values are still excluded |
| First sync scope | bounded byte channels plus concrete `AtomicInt`/`AtomicBool`; no `Mutex[T]`/`Send`/`Sync` | a general guard API would require a language ownership/memory-model design, not a C mutex wrapper |
| Hosted-only threads | yes; freestanding stays single-threaded and uses processes | claiming freestanding pthread/TLS support would require loader/TLS work outside this design |
| Shareable language values | byte-message confinement first, no Send/Sync markers yet | general shared state requires a new language memory/aliasing/type design, not just P5 |
| POSIX abstraction level | Unix resource semantics, Linux implementation and explicit Linux capabilities | exact Linux mirrors weaken portability; lowest-common-denominator POSIX loses path/notification guarantees |
| Stable systems library dependent on hosted C | accept an explicitly native-linked experimental package first; stabilize only after interop qualification | requiring freestanding-only immediately needs another generic native-link/backend design; do not fund dozens of compiler builtins implicitly |
| Throughput versus strict scalar copy boundary | measure byte/packed-scalar bridge in P0/P2; keep API representation-independent | if insufficient, request a separate checked bulk-transfer proposal and compiler-budget evidence before changing the seam |

No decision about HTTP version, protocol features, routing, TLS provider or
compression negotiation is made here. Those belong to their separate designs.
