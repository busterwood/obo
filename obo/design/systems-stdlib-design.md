# Systems standard library

Status: **design for independent review; no implementation authorized.**
Baseline: released `main` `51ce9e7680a90df9e3d5e38a9b469a460a2ca968`,
obo 0.12, symbol format 6, ABI 1. Date: 2026-10-31. Decimal `0.12` and the
experimental Postgres/SQLite adapters are present at this baseline.
No compiler, ABI, symbol-format or package version is reserved by this note.

## 1. Decision and scope

Build a general-purpose Linux systems layer around **resources, bounded byte
operations, deadlines and explicit lifecycle**, rather than exposing a syscall
catalog. Start with files, directory-relative operations, clocks and randomness;
then add nonblocking networking and a single-threaded readiness reactor; then
process supervision. Recommend **one reactor per worker process**, initially
launched by an external supervisor, subsequently by the process module. Native
obo threads and synchronization are separate runtime/compiler work in
`systems-threads-design.md`; they are not part of this core library.

This is foundation work for command-line programs, services and native-library
adapters. The HTTP readiness assessment in §10 describes dependencies only.
Request parsing, framing, routing, HTTP/1.1, HTTP/2 and HTTP/3 are outside this
design. TLS and compression are separately maintained transport/codec adapters,
not implementations of those protocols here.

Review posture: §3–4 and **P0/P1a are the approvable slice** after these fixes;
P2/P3 and the optional TLS/codec adapters remain directional until P0 evidence
lands. Threads and synchronization are not part of this approval.

The first target is **Linux x86-64**. Public contracts prefer Unix concepts which
can later have BSD/macOS implementations. That is a portability boundary, not a
claim that obo's code generator, startup or library already runs there. Linux-only
capabilities are queried explicitly and can return `Unsupported`; a fallback must
preserve the promised semantics or fail. Do not silently weaken path confinement,
close-on-exec or timeout behavior to approximate POSIX compatibility.

### Reading guide

- §2: baseline inventory and evidence.
- §3–4: information hiding, dependency graph, errors and resource invariants.
- §5–8: implementable module contracts and 0.12-shaped API headings.
- §9: process/reactor concurrency verdict and pointer to the thread note.
- §10–13: readiness, tests, packaging and core delivery phases.
- §14–15: rejected alternatives and real product decisions.

API blocks are **proposed declarations**, using ordinary 0.12 types, not complete
modules available for import. Type declarations, record/variant construction,
`match`, `defer`, `try` and ownership patterns in the examples follow 0.12
syntax. Bodyless `pub fn` headings are signature notation: 0.12 has no
declaration-only function form, so each heading becomes a definition in an
implementation; an API block as written is not a compilable module. There are no
invented generics, destructors, async keywords, borrowed returns, `Send`/`Sync`
markers or thread syntax. `Result[T]` is the existing builtin. Thread headings
are not in this core document; see `systems-threads-design.md`.

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
| Incremental files | `openFile("r"/"w"/"a")`, `readFd`, `writeFd`, `closeFile`; runtime.c:767–833; [LANGUAGE.md](../LANGUAGE.md):2009–2030 | Raw copied fd integers; open lacks CLOEXEC; fd-state table covers only numbers below 1024; number reuse is not ownership. Reads/writes retry EINTR internally; writes may succeed partially. |
| Buffered I/O | [io.obx](../lib/io.obx):13–57,75–241: Reader/Writer, fill, readLine, readN, readByte, takeChunk, flush, pump, count, write/writeBytes/writeByte | Reader/Writer borrow an int fd and own buffers only. Chunk size clamps to 1..1 MiB; line/readN totals are unbounded; readN is not exact-read. Flush retains unwritten bytes, but writeBytes can consume a prefix before failure without returning its count. No readiness/deadline/vectored interface. |
| Directories | [fs.obx](../lib/fs.obx):109–219: open/next/close, copied DirEntry names; runtime.c:847–871 | getdents64 layout decoded in obo; skips `.`/`..`; unsorted; regular-file open only fails on next. DT_UNKNOWN fallback lstats a reconstructed path and can race. No directory-relative mutation. |
| Metadata | fs.obx:56–106,221–269; runtime.c:872–930: stat/lstat, 14-number stat array, readlink, kind predicates | Linux values are exposed; atime/ctime have seconds only; no fstat, stat-at, chmod, mkdir, rename, hard link, unlink, filesystem sync, mapping interface. mmap exists only inside the allocator. |
| Environment / arguments | [env.obx](../lib/env.obx):12–50: get/has/value/getOr; runtime.c:1002–1042; [cli.obx](../lib/cli.obx):1–65,195–411: argv and option parser | getenv searches initial envp, copies bytes, conflates invalid name and absent. No snapshot enumeration or mutation. cli preserves eza-specific grammar/messages; it is not a process launcher. |
| Terminal | io.obx:59–73; runtime.c:936–981: isatty/terminalWidth | No termios mode ownership, restoration, resize events or checked terminal errors. |
| Sleep / time | `sleepMs`; runtime.c:987–1000. [time.obx](../lib/time.obx):1–24,69–128: UTC civil conversion, minute-precision long-ISO/ISO display | nanosleep retries EINTR with remainder, accepts up to 2,147,483,647 ms. No clock read, monotonic deadline, timer queue, RFC3339 parser or timezone support. Existing `formatIso` is not RFC3339. |
| Mapping | private `mmap` only inside `rt_init` (runtime.c:28–41,212–300) | No public anonymous/file mapping, protection, flush or copy interface; no mapping lifetime separate from the allocator. |
| Network / readiness | absent | No socket, connect/accept, bind, send/receive, poll/epoll/eventfd, nonblocking mode or DNS. There is no way to wait for readiness without blocking in `readFd`/`sleepMs`. |
| Process / signals | absent | No spawn/exec, wait/reap, pipe creation, process group, pidfd, signal set, signalfd or SIGCHLD policy. `exit` skips defers and is a raw process exit. |
| Concurrency | absent | No threads, atomics, mutex, condvar, channels, worker entry ABI or reactor. The runtime and language are single-threaded by construction; threads and synchronization are analyzed in `systems-threads-design.md`. |
| Strings / paths | [strings.obx](../lib/strings.obx):32–74,163–315,831–1195; [paths.obx](../lib/paths.obx):1–18,26–133 | Byte comparisons, split/join/trim, UTF-8 validation/lossy conversion, partial simple Unicode folding, display width, natural order and eza quoting. Paths are lexical join/parent/component/extension, not filesystem containment, normalization against symlinks or a sandbox. |
| Sort / format / errors | [sort.obx](../lib/sort.obx):1–26,124–198; [fmt.obx](../lib/fmt.obx):13–34,36–139; [errors.obx](../lib/errors.obx):11–59,120–123 | Stable index sorting and integer sort; digits/padding and eza modes; small English errno table plus eza exit constants. No generic containers, error categories, structured logging or duration formatting. These are useful existing modules, not replacements for OS resources. |
| HTML | [html.obx](../lib/html.obx):82–225 defines `Html`, `escapeText`, `escapeAttr`, `validateUrl`, `raw` and `join`; the exact eleven-module set is pinned in [imports.py](../tests/imports.py):169–172 | Present at this baseline, but it is an escaping/serialization helper with public fields and convention-level trust. It supplies no sockets, timers, request parser, framing or concurrency. |
| Native interop | [cinterop README](../experimental/cinterop/README.md):13–21,57–84,277–298; [design](native-c-interop-design.md) §8; [skill](../experimental/cinterop/skill/SKILL.md):22–61 | Trusted scalar i64 input, i32/i64 result, checked C tokens, bounded byte loops, explicit close. Internal/experimental, no callbacks into obo, no pointers or structs by value, no production TLS/IFUNC guarantee, no automatic foreign finalizer. zlib is a proof fixture, not a stable streaming codec. |
| DB adapters | `experimental/db/{postgres,sqlite}` at this baseline use `ref record { tok: int }` (e.g. postgres.obx:34–35, sqlite.obx:49–50) | **Documented temporary exception**, not the new core model. Their kind-checked C registry rejects stale/wrong-kind tokens, but the public field is a token, not a `res: platform::Resource` identity. Migrate after the core registry lands; do not copy this shape into new systems modules. |

### 2.1 Actual builtin and syscall boundary

The builtin name universe is in [obo.obo](../src/obo.obo):2393 and the dispatch
in `fn builtin` at 7110 (`nativeCall` at 7043); ownership/Result constructs have
additional special parsing.
System-facing builtins are the file, directory, metadata, terminal, environment,
args, sleep, output and exit operations above. Float and string helpers do not
add OS capabilities. There is no arbitrary public `syscall`, socket, poll,
clock_gettime, getrandom, fork, exec, wait, signal, thread or atomic builtin.

Actual syscall sites in runtime.c:14–52,646–1035 use Linux x86-64 read (0),
write (1), open (2), close (3), lseek (8, private to readFile), mmap (9, private
heap reservation), ioctl (16), nanosleep (35), exit (60), readlink (89),
getdents64 (217) and newfstatat (262). getenv/args read the saved startup stack;
they are not syscalls. The existence of private lseek/mmap is not a public API.

[entry.S](../runtime/entry.S):19–101 defines slots 0–72 in **both** tables.
Slot 59 is `rt_dec_reserved` in the baseline runtime and `rt_set_host_exit` in
the hosted variant; decimal occupies slots 60–72. Compiler constants are
obo.obo:2000–2082. Do not reuse 59, shift existing slots, treat 60–72 as free,
or copy obsolete 40/59-slot counts from historical interop prose. Runtime stubs
also write the global `rt_entry_sp` (entry.S:110).

### 2.2 Language and runtime constraints

- Owned records/arrays move; strings share non-atomic reference counts.
  References cannot be stored or returned, and several mutable borrows may
  alias: LANGUAGE.md:133–151 and 214–431. Group borrowing prevents use-after-free;
  it does not establish cross-thread exclusion.
- `^T` is a checked **non-owning** link, not a resource owner or a thread-safe
  weak pointer. runtime.c:470–493 computes heap-relative generation tokens;
  validation is not atomic with a subsequent access.
- `try` propagates the same error cell and runs defers; main cannot use try.
  Deferred cleanup cannot propagate its error. `exit` runs no defers:
  LANGUAGE.md:1736–1950 and 2009–2030. Trap statuses and Result error codes are
  different namespaces (LANGUAGE.md:2240–2260).
- Exported record fields are visible/writable; private types cannot occur in
  exported layouts (LANGUAGE.md:1371–1395; native interop design §8.5). Merely
  naming a record `File` with a public `tok` does not hide a token.
- Native calls carry `freesAll` and `writesHandles` (obo.obo:7043 (nativeCall) and 7101 (effects)).
  They cannot implement the current local/pure interface effect tiers, and
  cannot be invoked through pure lambda bodies. Interior references into a
  connection array cannot be passed across such a call. Store/move a connection
  into a whole local first; reborrow state after the call. No proposal here
  quietly marks C calls pure to make a Reader interface compile.
- Function values are second-class; Stage B captures borrow a creator's stack
  for the duration of a call (LANGUAGE.md:1214–1300). Neither a lambda
  cell nor a captured reference can be an asynchronous thread entry argument.
- The allocator, free lists, free traversal stack, counters, temporary roots,
  fd tracker and output buffer are global (runtime.c:83–159,330–468).
  `rt_str_inc/dec` use ordinary loads/stores (:417–428). `rt_collect` returns
  immediately (:309–321); collector comments at the file head are historical.
- [README.md](../../README.md):163–211: 4 GiB default arena, 16 GiB ceiling,
  one-object payload at most 4 GiB minus 32 bytes, separate metadata and native
  memory. mmap reserves virtual address space, not RAM. Free lists retain
  memory; `OBO_STATS` is not an RSS meter.
- Managed allocation failure currently terminates with exit 2 (runtime.c:330–350).
  Even `err` and `ok` allocate cells. We can return Result for OS/native ENOMEM,
  validation and configured capacity exhaustion, **not guarantee recoverable
  managed-heap exhaustion without separate runtime allocation work**. No normal
  I/O error or remote input should intentionally use a runtime panic.
- The compiler live-memory budget is **2,500,000 bytes**, not the application
  heap limit; the released 0.12 line peaks at 2,194,160 bytes in the recorded
  README gate. All initial APIs are source plus trusted C shims using the
  existing experimental seam. Compiler features are separately gated.

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
| os/stat | metadata layout, unavailable fields, identity representation | Metadata snapshot and identity equality | statx versus fstatat, timestamp precision, interned device/inode identity |
| os/mapping | mapped address/protection/page alignment | Mapping, bounded copy, protect, flush, close | mmap layout, page size, mprotect restrictions |
| time/clock | clock source and unit conversion | Instant, Duration, Deadline, Timestamp, realtime/monotonic | clock_gettime versus vDSO; backend fake clock |
| time/calendar | civil calendar and accepted text syntax | parse/format RFC3339, UTC conversion | parser implementation or formatting optimization |
| random | entropy provider and partial-fill mechanics | fillSome, fillUntil, bytes with explicit limit | getrandom availability and chunk size |
| io/stream | endpoint kind and partial-transfer normalization | Stream, readSome/writeSome, half-close, explicit close | socket/file/pipe dispatch, native buffering |
| io/buffer | capacity, compaction, byte cursor and pending progress | feed/find/copy/consume, Reader/Writer steps | ring versus linear storage; readv/writev batching |
| io/wait | readiness/deadline/cancellation composition and retained progress | readUntil/writeAllUntil/flushUntil steps | helper loop policy; reactor integration |
| poll/reactor | interest registration, wakeup and timer queue | watch/modify/remove, wait batch, timer, stop | poll versus epoll, timer heap versus wheel |
| net/address | sockaddr packing and presentation | IPv4/IPv6/Unix addresses, parse/format | kernel layouts, byte order, scope handling |
| net/socket | connection state and socket policy | Listener, Connection, Datagram, connect/accept/receive/send | accept4 fallback, options implementation |
| net/resolve | NSS/resolver selection and result ownership | resolveBlocking, bounded job/poll result | libc getaddrinfo versus explicit resolver service |
| process | spawn transaction, fd inheritance and child reaping | Command, Child, wait/terminate, exit status | posix_spawn versus fork/exec, pidfd availability |
| signals | signal mask, coalescing and safe delivery machinery | SignalSet, event source, next | signalfd versus self-pipe handler |
| environment | snapshot and startup mutation policy | Environment snapshot, set/unset, child environment | initial envp versus host libc environment |
| terminal (later) | mode snapshot/restoration and ioctl layouts | Terminal, size, enterRaw, restore | termios differences, resize delivery |
| logging | record escaping, buffering and loss/backpressure policy | bounded emit, drain, flush, counters | text versus structured sink, queue strategy |

No generic config module is proposed. Configuration format and precedence are
application decisions until multiple real consumers share a specified grammar.
Existing cli/env/strings plus bounded file reads are enough to start. Logging and
terminal earn modules because they own lifecycle/encoding decisions, not because
their helper functions need somewhere to live.

### 3.2 Dependency DAG

`A -> B` means A imports B. This is the permitted dependency graph for the core
systems library; within a row there are no implicit reverse edges. Platform C
adapters may call common registry support, never application obo. Threads and
synchronization are a separate design (`systems-threads-design.md`) and do not
appear in this core DAG.

```text
syserrors                                      -> (builtins only)
sysplatform                                    -> syserrors
sysclock, sysaddress                            -> syserrors
  sysclock                                     -> sysplatform
  syscalendar                                  -> sysclock, syserrors
sysenv, sysrandom                                -> sysplatform, syserrors
sysstat                                        -> sysclock, sysplatform, syserrors
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
syswaitio                                      -> sysstream, sysbuffer, sysreactor, sysclock, sysplatform
systerminal                                    -> sysstream, syssignals, sysreactor, sysplatform
syslog                                         -> sysstream, sysclock, sysplatform, syserrors
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

**Implementing hiding in 0.12:** choose one concrete shell representation.
Every native typed shell owns a **concrete** field `res: platform::Resource`
(for example `pub type File = ref record { res: platform::Resource }`);
there is no type-erasing alias and no `^File -> ^Resource` conversion. The
support record itself is `pub type Resource = ref record { reserved: int }`, which
carries no authority. `platform::source(r: &Resource): Result[^Resource]` checks
that the record is a live table member and returns its non-owning identity.
`platform::requireKind(r: &Resource, kind: int): Result[^Resource]` performs the
same check plus the expected kind. Every typed module operation calls
`requireKind(&x.res, platform::KIND_*)` before any native access; typed `source`/
`watch` helpers do the same. A module-private table associates that identity with
a C token, kind, state and quota charge. Validate membership, generation and kind
before every native access. A user-created `Resource` has no
entry and returns `InvalidResource`. Moving a shell preserves identity; copying
or reconstructing a record does not duplicate a resource.

**Public-field limitation, stated honestly:** `res` is public and writable.
Replacing it cannot forge a new capability: the registry still checks membership,
generation and kind, and a user-created `Resource` fails. But a caller *can*
deliberately point a shell at another live resource of the same kind, which
aliases or steals that resource and can leave the original open (a leak). Typed
modules therefore revalidate the field on every entry point and reject a
cross-kind alias with `InvalidResource`; they do not claim that public fields are
opaque. A compiler-negative test must reject `^File`/`^Stream` conversion, and a
runtime test must cover a replaced same-kind and cross-kind field. At this
baseline fields are public; if the approved private-by-default field design is
implemented, `res` must be explicitly `pub` to preserve this contract.

`Resource.reserved` is the **checked table hint** from P0 onward, not merely a
linear-scan placeholder: it holds a bounded slot index plus an epoch. A lookup
indexes a fixed table with it, then verifies the full owner identity (generation,
kind, live state) and never treats the hint as authority. An out-of-range,
forged, stale or cross-context hint returns `InvalidResource` or falls back to a
bounded scan; it can never select a different live resource by itself. P0's
table is a fixed-capacity array (default 4096 live resources per context) with
free slots and tombstone epochs, so the common path is O(1) and does not rely on
a whole-table scan. If a later index needs a hash, it is still an untrusted hint
checked against full identity; changing the hint layout is not a client API.
Exported-field privacy cannot be assumed. Private tables must not retain an
owning reference to the shell: omitted close is a detectable native leak, not an
immortal managed object disguised as cleanup.

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
short transfer is success; it is never silently repeated. `readInto`/`writeFrom`
are the allocation-free forms: `readInto` writes at `offset` within the existing
`dst` length, up to `max`, and returns 0 only at EOF; `writeFrom` writes at most
`count` bytes from `src[offset..]` and returns the short count. Neither allocates
a result array or copies the payload again. Empty writes return 0 after resource
validation. A zero read request is InvalidArgument; EOF is `ok(nil)` for
`readSome` and `ok(0)` for `readInto`, distinct from WouldBlock. For UDP, empty
datagrams are data, not EOF.
No normal network/filesystem failure traps. Checked sizes are validated before
array indexing, multiplication, narrowing, allocation or native calls.

### 4.2 Ownership and resource state

Every successful creator returns one owned obo shell with one logical close
obligation. The shell's record destructor does **not** release native resources.
Register `defer close(owner)` immediately. A borrowed operation never retains a
borrowed obo reference in C; queued bytes are copied to C-owned memory before
return, or moved into an obo-owned pending operation on the same thread.

**Uniform public-field rule.** Every public value record is untrusted at every
entry point, not only at its constructor. Revalidate all fields, lengths, ranges,
cross-field combinations and the `res` hint before indexing, arithmetic, copying
or a native call. This explicitly covers `platform::Options`, `syssignals::
SignalSet`, `sysaddress::Address`, `sysenv::Environment`, `sysbuffer::Buffer`,
`sysfile::OpenOptions`, `sysnet::ListenOptions`, `sysprocess::Command` and
reactor event requests. `sysstat::Identity` is a value record, but
`sameIdentity` is pure equality of opaque bits and is explicitly not an authority
check. Reject a malformed record with `InvalidArgument`, `Range` or
`InvalidResource`; never trust a field because a module constructor usually set
it. Negative tests construct each record directly.

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
pub type Transfer = ref record { res: Resource }
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
pub const KIND_ATOMIC_INT = 19
pub const KIND_ATOMIC_BOOL = 20
pub const KIND_SENDER = 21
pub const KIND_RECEIVER = 22
pub fn initialize(options: &Options): Result[bool]
pub fn hasCapability(name: string): bool
pub fn source(r: &Resource): Result[^Resource]
pub fn requireKind(r: &Resource, kind: int): Result[^Resource]
pub fn counts(): Result[Counts]
pub fn releaseTransfer(t: &Transfer): Result[bool]
pub fn shutdown(): Result[Counts]
```

`source` is the supported bridge from a shell's `res` field to the checked
identity used by `watchResource`; a caller could still write `handle(f.res)` and
the table would validate it, but that is not the documented route. `requireKind`
is the typed-module entry check. Both reject an unregistered, closed or
wrong-generation record.
The `KIND_*` values are library constants; changing them is a library ABI
change, not a kernel ABI exposure. `KIND_WORKER`, `KIND_ATOMIC_INT`,
`KIND_ATOMIC_BOOL`, `KIND_SENDER`, `KIND_RECEIVER`, `KIND_TRANSFER` and
`Transfer`/`releaseTransfer` are **reserved for the separate thread note**: P0–P3
does not create or consume them, and no core module imports `systhreads`/`syssync`.
`initialize` validates every `Options` field at entry: positive limits, checked
arithmetic and an explicit `ignorePipeSignal` policy.

Initialization is explicit, before resource creation; imports have no OS side
effects. Defaults supplied by a constructor: 4096 native resources, 8192 live
shells, 64 MiB native-buffer bytes and `ignorePipeSignal = true`, independently
adjustable with checked limits. `ignorePipeSignal = false` preserves the legacy
SIGPIPE disposition and makes new pipe-write APIs return Unsupported before
writing (see §8.2). Unknown capability names return false. Reinitializing with the
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
prerequisite hidden inside this plan. Slots 0–72 are occupied in both tables (59 is the
reserved/hosted-exit slot; 60–72 are decimal builtins). No new jump-table
allocation is prescribed here.

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

pub type Directory = ref record { res: platform::Resource }
pub type Iterator = ref record { res: platform::Resource }
pub type Entry = ref record { name: string; kind: int }
pub fn open(path: string): Result[Directory]
pub fn child(base: &Directory, name: string): Result[Directory]
pub fn iterate(base: &Directory): Result[Iterator]
pub fn next(it: &Iterator): Result[?Entry]
pub fn metadata(base: &Directory, name: string, follow: bool): Result[stat::Metadata]
pub fn readLink(base: &Directory, name: string): Result[[]byte]
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
`readLink` returns the raw target bytes, not a NUL-terminated string, and inherits
the existing contract: a dangling target succeeds, a non-symlink is InvalidArgument,
and a target of 4095 bytes or more is NameTooLong (never a silent truncation).
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

pub type File = ref record { res: platform::Resource }
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
pub fn intoStream(mut f: &File): Result[stream::Stream]
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
`File::intoStream` and `Connection::intoStream` **logically consume** the typed
shell and return a `Stream` owning the same native resource. The signature takes
`mut &` so a `Connecting` failure can leave the shell usable; on success the
shell's `res` is replaced with an unregistered value and every later typed
operation fails. The shell record itself is still an ordinary owned record. It
adds no fd and is the supported way to hand a file/connection to the buffered/
step APIs. It atomically **re-tags the live table entry** from `KIND_FILE`/
`KIND_CONNECTION` to `KIND_STREAM`; the old shell's handle is unusable, and the
returned Stream passes `requireKind(..., KIND_STREAM)`.

Only a live established resource is convertible: a File is open; a Connection
must be Open, ReadClosed or WriteClosed, and the Stream preserves its half-close
state. A `Connecting` Connection returns `InProgress` and is left unchanged (not
consumed), so `finishConnect`/`SO_ERROR` remain available; a `Failed`/closed
resource returns the state error and retires any live resource. On a non-`InProgress` failure the source is closed and its native
resource retired exactly once, never leaked (an already-closed shell has no live
resource to retire). `intoStream` loses File `metadata`/`seek`/`sync`/`truncate`/
`chmod` and Connection `finishConnect`/addresses/socket options; use
`duplicateStream` when both interfaces are needed. `duplicateStream` is `dup`
semantics: the duplicate shares the open file
description, so it **shares the file offset and status flags**. Use `readAt`/
`writeAt` for independent positions; do not mix buffered readers on duplicates.
The same sharing rule applies to `Connection::duplicateStream` (shared socket
state and half-close).

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
import "sysclock.obx" as clock
pub type Identity = ref record { bits: int }
pub type Metadata = ref record {
    kind: int; permissions: int; size: int; links: int
    uid: int; gid: int; modified: clock::Timestamp; accessed: clock::Timestamp
    changed: clock::Timestamp; created: ?clock::Timestamp; identity: Identity
}
pub const KIND_UNKNOWN = 0
pub const KIND_FIFO = 1
pub const KIND_CHR = 2
pub const KIND_DIR = 4
pub const KIND_BLK = 6
pub const KIND_REG = 8
pub const KIND_LNK = 10
pub const KIND_SOCK = 12
pub fn sameIdentity(a: &Identity, b: &Identity): bool
```

Metadata is a detached owned snapshot, not a borrowed kernel struct. `kind` is a
library constant from the list above; permissions are low Unix permission bits,
not type bits. `Identity.bits` is an **opaque, context-local interned identity**
for the exact `(device,inode)` pair, stored as one int bit pattern and compared
only for equality. It is not a hash and not a raw inode: the adapter interns the
exact pair in a fixed table, so equal files compare equal and collisions are
impossible; table exhaustion is `ResourceLimit`, not a silent collision. Entries live for
the systems context (bounded, cleared at shutdown); there is no per-`Metadata`
release hook, so equality remains valid for every snapshot in that context. It
is not stable across processes/restarts and must never be persisted, used in
arithmetic, formatted as a number, or interpreted as a negative error. This avoids
two `[]byte` allocations per stat while preserving exact equality. uid/gid fit
positive int; links outside the signed range return Range. size is meaningful for
regular files; device/FIFO sizes carry no portable content-length promise.
`clock::Timestamp` normalizes nanos to 0..999,999,999 and keeps seconds separate,
permitting dates outside signed-nanosecond epoch range. Birth time is nil when
unavailable. `sameIdentity` is **pure equality of opaque interned bits**: it does
not validate table membership or grant authority, and a copied `Identity` is just
a value. It is not a resource check; callers still need `Metadata`/`requireKind`
for any native operation. Statx/fstatat conversion stays native; no new indexed
stat array.

### 5.4 Mapping

```obo
module sysmapping
import "sysfile.obx" as file
import "sysplatform.obx" as platform
pub type Mapping = ref record { res: platform::Resource }
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
pub type Timestamp = ref record { seconds: int; nanos: int }
pub fn monotonic(): Result[Instant]
pub fn realtime(): Result[Timestamp]
pub fn realtimeNs(): Result[int]
pub fn durationNs(n: int): Result[Duration]
pub fn durationMs(n: int): Result[Duration]
pub fn after(now: &Instant, duration: &Duration): Result[Deadline]
pub fn forever(): Deadline
pub fn elapsed(start: &Instant, now: &Instant): Result[Duration]
pub fn remaining(now: &Instant, deadline: &Deadline): Result[?Duration]
pub fn sleep(duration: &Duration): Result[bool]
pub fn sleepUntil(deadline: &Deadline): Result[bool]
```

Instant is CLOCK_MONOTONIC, valid only within this boot/backend clock domain;
never persist it or compare to realtime. Duration is nonnegative signed i64 ns;
negative inputs are InvalidArgument. Multiplication, timespec conversion,
deadline addition and elapsed subtraction are checked before arithmetic;
overflow is Range, not wrap/saturation. A finite deadline at/before now is
expired; remaining returns zero. Infinity is a distinct flag, not INT64_MAX;
`remaining` returns nil for an infinite deadline, so no duration overflow is
invented. Reject malformed public records. `realtime()` returns seconds+nanos
and is the single timestamp type used by metadata and calendar; `realtimeNs`
is a convenience that returns signed ns since the Unix epoch and Range if the
seconds cannot be represented (~1677–2262), so metadata/calendar avoid that
narrow range. No float time arithmetic.

`elapsed` subtracts two monotonic instants with checked ordering (a later start
is `InvalidArgument`). `sleep(duration)` is `sleepUntil(now + duration)` with the
same interruptible, no-hidden-retry contract; it is a convenience, not a second
timer implementation. `sleepUntil` is deliberately interruptible and returns
Interrupted; calling it again uses the same absolute deadline. Reactor waits and syswaitio helpers may
handle EINTR internally only by checking stop/cancellation and recomputing
remaining time against the original deadline. Rounding for a millisecond backend
is upward with checked clamping per wait, never making an early timeout. Realtime
clock adjustments cannot extend a monotonic timeout. Suspend time is not included
by CLOCK_MONOTONIC; a future boottime clock is a separately named capability.

```obo
module syscalendar
import "sysclock.obx" as clock
pub fn parseRfc3339(text: string): Result[clock::Timestamp]
pub fn formatRfc3339(t: &clock::Timestamp): Result[string]
```

`parseRfc3339` accepts four-digit years 0001..9999, valid Gregorian dates,
uppercase `T`/`Z` and lowercase `t`/`z` (RFC 3339 §5.6), seconds, optional 1..9
fractional digits, and `Z` or signed HH:MM offset; normalize to UTC. `-00:00`
means UTC with an unknown local offset (RFC 3339 §4.3), so it is accepted and
normalized, not rejected. Reject leap seconds, excess fractional digits,
out-of-range offsets and trailing text. Output UTC `Z`, omitting a zero fraction
and trimming trailing zeros.
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
pub type Stream = ref record { res: platform::Resource }
pub type Standard = closed { Input {} Output {} Error {} }
pub fn source(s: &Stream): Result[^platform::Resource]
pub fn standard(which: &Standard): Result[Stream]
pub fn standardInput(): Result[Stream]
pub fn standardOutput(): Result[Stream]
pub fn standardError(): Result[Stream]
pub fn readSome(s: &Stream, max: int): Result[?[]byte]
pub fn readInto(s: &Stream, mut dst: &[]byte, offset: int,
                max: int): Result[int]
pub fn writeSome(s: &Stream, bytes: &[]byte, offset: int, count: int): Result[int]
pub fn writeFrom(s: &Stream, src: &[]byte, offset: int,
                 count: int): Result[int]
pub fn writeVector(s: &Stream, parts: &[][]byte,
                   first: int, offset: int): Result[int]
pub fn readVector(s: &Stream, sizes: &[]int): Result[?[][]byte]
pub fn shutdownRead(s: &Stream): Result[bool]
pub fn shutdownWrite(s: &Stream): Result[bool]
pub fn close(s: &Stream): Result[bool]
```

Stream is a concrete tagged native endpoint abstraction, not a current obo
interface with unsound native effect claims. `Stream.source(s)` is exactly
`platform::source(&s.res)` (a convenience, not a second identity model);
typed endpoints reach it by a logical `intoStream` transfer or by an explicit duplicate.
File/pipe/socket APIs produce a Stream by that explicit transfer or duplicate. Native side keeps iovecs and C buffers alive
through the syscall; obo arrays are borrowed only for the call. Vector total bytes
and count (default <=64, further bounded by backend IOV_MAX) are checked; the
return count advances a cursor across vectors, not a count per vector. ReadVector
returns owned arrays, final one possibly short; nil is EOF before any data.
Socket send uses MSG_NOSIGNAL. Pipe writes use the default-ignore SIGPIPE policy
in §8.2 (or Unsupported if the caller keeps the legacy disposition).
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
pub type Cancellation = ref record { res: platform::Resource }
pub fn cancellation(): Result[Cancellation]
pub fn request(c: &Cancellation): Result[bool]
pub fn closeCancellation(c: &Cancellation): Result[bool]
pub fn readExactUntil(s: &stream::Stream, r: &reactor::Reactor, count: int,
                       mut out: &buffer::Buffer, until: &clock::Deadline,
                       cancel: &Cancellation): Result[bool]
pub fn writeAllUntil(s: &stream::Stream, r: &reactor::Reactor, bytes: &[]byte,
                      mut cursor: &buffer::Cursor, until: &clock::Deadline,
                      cancel: &Cancellation): Result[bool]
pub fn flushUntil(mut w: &buffer::Writer, s: &stream::Stream,
                  r: &reactor::Reactor, until: &clock::Deadline,
                  cancel: &Cancellation): Result[bool]
```

`readExactUntil.count` is the number of **additional** bytes to append after
`out`'s existing `[start,end)` prefix; success means exactly that many new bytes,
`EndOfInput` means EOF before they arrive, and the prefix is never discarded.
`writeAllUntil` succeeds when `cursor.done == len(bytes)`; a short write advances
the cursor and never replays bytes. `flushUntil` succeeds when the writer's
pending bytes are drained, retaining any remainder on error/timeout. All three
use the same absolute deadline and never restart it.

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
        ignorePipeSignal: true
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

Every consumer (`listen`, `connect`, `bindDatagram`, `send`, `format`) revalidates
a caller-constructed `Address`: IPv4/IPv6 octet lengths are exactly 4/16; port
0..65535; scope an unsigned 32-bit interface index represented as nonnegative
int; Unix path has no NUL and fits `sockaddr_un`; abstract name is bounded.
Host/network byte order is private. Numeric parsing does no DNS, accepts IPv6 bracket presentation only
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
`cancelResolve`, `closeResolve` over a bounded ResolverJob identity. The
implementation is an **open question**: an explicitly configured helper process
(P3) or a C-only worker in the separate thread note (P4). Do not silently rely on
either. The 2026-10-28 database-adapter decision rejected a C helper process for
database work; DNS has different latency/cancellation constraints and needs its
own evidence. A helper process adds lifecycle/framing; a C worker needs hosted
TLS/pthread qualification. Deadline/cancel abandons the result, frees eventual
output and bounds outstanding work; it does not pretend to interrupt an arbitrary
libc resolver safely. Callbacks from an
alternative C resolver queue records on the C side. A simple listening server
can use numeric addresses and requires no DNS.

### 7.2 Listener, connection and datagram contracts

```obo
module sysnet
import "sysaddress.obx" as address
import "sysplatform.obx" as platform
import "sysstream.obx" as stream
import "sysreactor.obx" as reactor

pub type Listener = ref record { res: platform::Resource }
pub type Connection = ref record { res: platform::Resource }
pub type Datagram = ref record { res: platform::Resource }
pub type ListenOptions = ref record {
    backlog: int; reuseAddress: bool; reusePort: bool; ipv6Only: bool
}
pub type Packet = ref record {
    bytes: []byte; peer: ?address::Address; truncated: bool; originalSize: int
}
pub fn listen(a: &address::Address, options: &ListenOptions): Result[Listener]
pub fn accept(l: &Listener): Result[Connection]
pub fn connect(a: &address::Address): Result[Connection]
pub fn finishConnect(c: &Connection): Result[bool]
pub fn readSome(c: &Connection, max: int): Result[?[]byte]
pub fn writeSome(c: &Connection, bytes: &[]byte, offset: int, count: int): Result[int]
pub fn shutdownWrite(c: &Connection): Result[bool]
pub fn intoStream(mut c: &Connection): Result[stream::Stream]
pub fn duplicateStream(c: &Connection): Result[stream::Stream]
pub fn localAddress(c: &Connection): Result[address::Address]
pub fn peerAddress(c: &Connection): Result[address::Address]
pub fn listenerLocalAddress(l: &Listener): Result[address::Address]
pub fn datagramLocalAddress(d: &Datagram): Result[address::Address]
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
connection, since dup shares file-status flags. `listenerLocalAddress` and
`datagramLocalAddress` return the bound address, so a port-0 bind can discover
the kernel's chosen port before accepting or sending. accept returns WouldBlock
on an empty queue. On EMFILE/ENFILE pause acceptance, report resource pressure and wait
for capacity/backoff; any reserved-fd recovery technique is an explicit listener
policy, not a busy accept loop. EINTR is observable. `ListenOptions` is revalidated
at `listen`: backlog is positive (kernel capping documented), booleans are
independent, and the three option flags are copied, never retained. REUSEADDR and
REUSEPORT are independent explicit options; default reusePort=false,
ipv6Only=true. No accidental dual-stack bind.

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

`Connection::intoStream` re-tags `KIND_CONNECTION` to `KIND_STREAM` and is valid
only in Open/ReadClosed/WriteClosed. A Connecting connection returns `InProgress`
and is left unchanged (not consumed), so `finishConnect`/`SO_ERROR` remain
available; Failed/Closed returns the state error and retires any live resource.
On a non-`InProgress` failure the source is closed and its native resource
retired, never leaked. The Stream loses
`finishConnect`, addresses and socket options; use `duplicateStream` if both are
needed (the duplicate shares the socket and half-close state).

Directional state is two independent bits after connection establishment.
shutdownWrite is idempotent and never implies that the peer has consumed bytes.
Close is abortive with respect to application-buffered data: flush first when
needed. Never set linger to a blocking close by default. A duplicated Stream has
an independent fd lifetime but shares the socket and half-close state; mixing
readers on duplicates splits incoming bytes and is explicitly unsupported by
buffered-reader helpers. Native state tracks endpoint sharing.

Datagram receive preserves message boundaries, sender and truncation. An empty
datagram succeeds with empty bytes. `peer` is nil for an unnamed Unix datagram
sender; a caller must not treat nil as an address. `originalSize` is exact when
the backend can provide it, otherwise -1 with truncated=true; no silent
truncation. A datagram send is all-or-error; an unexpected short send is
IoFailure, never continuation as a second datagram. v1 datagrams are unconnected:
repeated `send(d, samePeer, ...)` is the connected-peer pattern; a
`connectDatagram`/socket-option escape hatch is not exposed. Provide checked
receive/send buffer size options and broadcast enablement in P2; multicast
membership, ancillary data and batch sendmmsg/recvmmsg are later independently
justified additions.

Server-specific needs are accept fairness, descriptor pressure, bounded per-peer
buffers, idle deadlines and shutdown. Client-specific needs are address resolution,
connect completion and fallback policy. Neither needs a public fd or socket-option
integer escape hatch.

### 7.3 Reactor

```obo
module sysreactor
import "sysclock.obx" as clock
import "sysplatform.obx" as platform
pub type Reactor = ref record { res: platform::Resource }
pub type Watch = ref record { res: platform::Resource }
pub type Timer = ref record { res: platform::Resource }
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

`timer` requires a finite `at`; `forever()` as `at` is InvalidArgument (a timer
that never fires is useless and would complicate cancellation). A zero `period`
is one-shot; a positive period is periodic. `wait` may take `forever()`.
Valid `Event` combinations: `timer`/`wake`/`failed` are mutually exclusive;
`readable`/`writable`/`hangup` may combine; `timer` has `ticks >= 1`; `wake` and
`failed` have no I/O bits; a `failed` event still requires explicit close.

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
import "sysplatform.obx" as platform
import "sysstream.obx" as stream
import "sysreactor.obx" as reactor
pub type Child = ref record { res: platform::Resource }
pub type Redirect = closed {
    Inherit {}
    Null {}
    Pipe {}
    FromStream { source: ?stream::Stream }
    MergeError {}                 // stderr -> stdout (2>&1); valid for error only
}
pub type EnvPolicy = closed {
    Inherit {}                    // snapshot inherited process environment at spawn
    Explicit { value: env::Environment }
}
pub type Command = ref record {
    executable: string; arguments: []string; environment: EnvPolicy
    directory: ?dir::Directory; input: Redirect; output: Redirect; error: Redirect
    newGroup: bool
}
pub type ExitStatus = closed {
    Exited { code: int }
    Signalled { signal: int; coreDumped: bool } // library id, not a Linux number
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
pub fn reapChildren(): Result[int]
pub fn cwd(): Result[string]
pub fn chdir(directory: &dir::Directory): Result[bool]
```

Command owns its arrays/environment policy/redirection resources; `spawn`
revalidates the whole public record (nonempty executable, argv shape, no NULs,
valid `EnvPolicy`, each `Redirect` kind, open directory) and copies validated
argv/env/native launch state before launching. `environment: Inherit` snapshots
the process environment at spawn; `Explicit` uses the caller's snapshot.
`MergeError` is valid only for `error` and duplicates the stdout descriptor as
stderr (`2>&1`); `input`/`output` reject it. No shell parsing:
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
alone. The terminal `Signalled.signal` is a library signal id mapped by the C
adapter, never a raw Linux number. Stopped/continued notifications, if later
exposed, are distinct nonterminal events. pollExit returns nil while running, stores the terminal status and reaps
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
import "sysplatform.obx" as platform
import "sysreactor.obx" as reactor
pub type SignalSet = ref record { bits: int }
pub type SignalSource = ref record { res: platform::Resource }
pub type Event = ref record { signal: int; count: int }
pub const INTERRUPT = 1
pub const TERMINATE = 2
pub const HANGUP = 3
pub const CHILD = 4
pub const WINDOW_CHANGED = 5
pub const OTHER = 6                 // status-only; not subscribable
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
an obo signal-handler function. SignalSet uses library-assigned ids mapped by the
C adapter, not Linux signal numbers; `add` and `subscribe` both revalidate the
public `bits` (supported ids, range, at least one signal) and reject
SIGKILL/SIGSTOP and synchronous fault signals, so a directly constructed
`SignalSet` cannot block them. `OTHER` is a status-only id for an exit signal
outside the subscribable set; `add` rejects it.
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

**SIGPIPE default:** systems-context initialization installs `SIGPIPE` ignored
by default (`Options.ignorePipeSignal = true`). New pipe-write APIs then return
`BrokenPipe` after a failed write. This deliberately changes legacy
`print`/`println`, which do not check write errors: instead of being killed by
SIGPIPE on a closed pipe, they now silently drop output. That compatibility
consequence must be documented in the release note and tested; it is the price
of a Result-returning pipe API. Socket sends use `MSG_NOSIGNAL` without changing
global state. Do not change process dispositions on module import. A caller that
needs the old disposition sets `ignorePipeSignal = false`; in that mode new
pipe-write APIs return `Unsupported` **before** writing, rather than allowing
default termination. Spawn restores intended child defaults. When hosted
libraries also own signals, incompatible subscriptions return `Busy`; no handler
replacement behind their backs. There is no separate per-call suppression
primitive in v1.

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

Validate names (nonempty, no `=`/NUL), values (no NUL) and total size at
construction **and again in `get`/`set`/`unset`/`entries`**: a caller can construct
`Environment` directly, so malformed entries are `InvalidArgument`, never a
native call. Missing is nil, present-empty is an empty byte array. A snapshot
owns all content; local mutation has no process effect. `entries` returns a
**copy** (new `Pair` records and copied strings), never a borrow of the internal
array, so the caller cannot mutate the snapshot through the result. Entries
preserve startup order and duplicate-key policy is explicit at implementation;
lookup is first match, like `getenv`. This hides environment serialization and
duplicate-key handling, not arbitrary config. There is no native resource and no
close obligation; dropping the snapshot frees its strings normally.

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
import "sysplatform.obx" as platform
import "sysstream.obx" as stream
import "sysreactor.obx" as reactor
import "syssignals.obx" as signals
pub type Terminal = ref record { res: platform::Resource }
pub type Size = ref record { rows: int; columns: int }
pub fn openStandard(which: &stream::Standard): Result[Terminal]
pub fn size(t: &Terminal): Result[Size]
pub fn enterRaw(t: &Terminal): Result[bool]
pub fn restore(t: &Terminal): Result[bool]
pub fn watchResize(r: &reactor::Reactor, t: &Terminal,
                   tag: int): Result[reactor::Watch]
pub fn duplicate(t: &Terminal): Result[stream::Stream]
pub fn close(t: &Terminal): Result[bool]
```

`systerminal` owns an independent standard-stream duplicate (`openStandard`
selects Input/Output/Error through `stream::standard` with CLOEXEC; it does not
take ownership of 0/1/2 and accepts no arbitrary fd) and a saved termios
snapshot. `enterRaw` is an explicit restoration obligation with
`defer restore`; `close` restores once and is idempotent. Window size events use
syssignals; `watchResize` is a typed integration, not a second signal handler.
Arbitrary ioctl is not exposed. A terminal UI framework is separate.

```obo
module syslog
import "sysplatform.obx" as platform
import "sysstream.obx" as stream
import "sysclock.obx" as clock
pub type Logger = ref record { res: platform::Resource }
pub type Level = closed { Debug {} Info {} Warn {} Error {} }
pub type Policy = closed { ReturnWouldBlock {} DropWithCounter {} }
pub type EmitResult = ref record { accepted: bool; dropped: int }
pub fn openStandard(which: &stream::Standard, capacity: int,
                    policy: &Policy): Result[Logger]
pub fn emit(l: &Logger, level: &Level, message: &[]byte): Result[EmitResult]
pub fn drain(l: &Logger, until: &clock::Deadline): Result[bool]
pub fn flush(l: &Logger): Result[bool]
pub fn close(l: &Logger): Result[bool]
```

`syslog` exposes a Logger with an owned bounded output buffer, level, per-record
limit, and policy (`ReturnWouldBlock` or explicit `DropWithCounter`).
`openStandard` selects Input/Output/Error through `stream::standard`, duplicates
it with CLOEXEC, and accepts no arbitrary fd. `emit` copies a record atomically
into the queue or rejects it;
`drain`/`flush` report sink errors. Escape newline/control bytes in fields; timestamp by realtime and
record latency by monotonic time. No implicit global logger, background thread,
unbounded queue or hidden application secrets. An initial line format is
sufficient; structured JSON needs a separately tested encoder. Reactor must never
log by calling a potentially blocking sink on every error. Multi-process logging
uses a supervisor collector/pipe with defined framing and size caps; writes to a
shared regular file are not magically record-atomic.

## 9. Process and reactor concurrency verdict

| Choice | Verdict |
|---|---|
| Prefork/process workers first | Deferred until a focused hosted/fork runtime audit; general obo `fork` is not a v1 API. |
| One reactor per process | **Recommended first.** Many idle connections, one heap, deterministic deadlines, no allocator change. |
| Full threads plus arbitrary shared state | Rejected for the core library. It requires the separate thread design and still excludes shared obo values. |
| Reactor per process plus confined workers later | **Target architecture.** Supervised processes first; confined compute workers only after the thread note's T2 prerequisites. |

Run one reactor per supervised worker process. Use `spawn/exec` supervision in
P3; a controlled prefork launcher needs its own runtime audit. CPU-heavy or
blocking work moves to another process until threads are separately approved.
No core module creates a hidden thread or a callback into obo.

Threads, synchronization, worker entry, T2 context-relative state and the
T1/T2 decision are **not part of this core design**. They live in
[systems-threads-design.md](systems-threads-design.md). That note records that
T2 (context-relative globals and the identity table) is a prerequisite for P4b/P5;
P4a is an infrastructure spike with no public worker API; T1-only workers are
compute/native jobs with no `sys*` resource use. The core phases below stop at P3
plus optional adapters. P4/P5 do not authorize implementation here.

## 10. HTTP readiness assessment (no protocol design)

Phases refer to §13 for this core; thread/sync phases are in
`systems-threads-design.md`. “Foundation” means a facility exists for a future
HTTP implementation, not that an HTTP server or protocol is supplied.

| Need | Baseline | Supplying module / phase | Remaining assessment |
|---|---|---|---|
| Listen/accept, local/peer address | absent | sysaddress/sysnet, P2 | numeric TCP/Unix listener, CLOEXEC and accept pressure policy |
| Nonblocking reads/writes, backpressure, half-close | raw fd partial writes only | sysstream/sysbuffer/sysnet, P2 | bounded queues and retained progress; throughput of scalar bridge must be measured |
| Timers, idle/read/write/connect timeouts | sleep only | sysclock P1; sysreactor/syswaitio P2 | absolute monotonic deadlines, cancellation and fairness |
| TLS | no stable adapter | optional transport adapter P2T after P2 + hosted interop hardening | certificate/key loading, hostname verification for clients, handshake/read/write/shutdown state and want-read/want-write; C callbacks queue events only; no pointers/SSL structs escape |
| Concurrent connections | single blocking program | reactor P2 | one-core event loop, bounded state machines |
| CPU utilization / isolation | single process/thread | externally supervised P2 workers; sysprocess P3 | multi-process scale first; confined workers are in the thread note, blocked on T2 |
| Shared application state/cache | single-thread-local only | per-process owner + IPC P3; byte channels are in the thread note | no arbitrary shared obo object graph; no present Mutex workaround |
| Compression | experimental in-memory zlib example | optional bounded streaming codec P2C | incremental input/output, output quota, expansion-ratio/CPU limits; protocol negotiation separate |
| DNS | absent | resolveBlocking P2; reactive helper is an open question (P3 process helper vs P4 C worker) | not needed for numeric server bind; NSS blocking cannot hide inside event loop; the DB helper-process rejection of 2026-10-28 is not silently ignored |
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

Core boundary tests required before the relevant phase:

- **Identity bridge:** `platform::source`/`requireKind` accept the shell's own
  `res`; a compiler-negative test rejects a `^File` passed where `^Stream` or
  `^Resource` is required. At runtime, replace `file.res` with another same-kind
  resource (documented alias/steal) and with a cross-kind resource (must return
  `InvalidResource`); a user-constructed `Resource` must fail.
- **Hint table:** forge `Resource.reserved` out of range, stale and pointing at
  another live slot; every case must fail full identity/kind checks or fall back
  safely, never select the wrong resource.
- **Public-field bypass:** construct `Options`, `SignalSet`, `Address`,
  `Environment`, `Buffer`, `OpenOptions`, `ListenOptions` and `Command` directly
  with invalid fields and assert `InvalidArgument`/`Range`, never a native call.
- **Standard streams:** `openStandard(Input/Output/Error)` each duplicate the
  right descriptor with CLOEXEC; the closed `Standard` type makes an invalid
  `which` unrepresentable, so the test covers all three variants; no arbitrary
  fd exists in the API; closing a
  returned Stream/Terminal/Logger cannot close process 0/1/2. Keep a legacy
  `openFile` descriptor open across a new-registry operation and prove neither
  owner closes the other.
- **Readlink:** dangling target, non-symlink, empty target and a 4095-byte target;
  4096 bytes is `NameTooLong`, never truncated.
- **Port 0:** bind TCP and UDP to port 0, then `listenerLocalAddress`/
  `datagramLocalAddress` returns a nonzero port that accepts/sends. An unnamed
  Unix datagram sender yields `Packet.peer == nil`, not a fabricated address.
- **intoStream:** convert an Open File and an Open Connection; the old shell is
  invalidated, the table entry changes to `KIND_STREAM`, and Stream operations
  pass `requireKind`. A Connecting Connection returns `InProgress` and remains usable
  for `finishConnect`; a Failed/closed source fails and retires the native
  resource exactly once. Assert the documented method loss. The offset test below
  covers `duplicateStream`, not `intoStream`.
- **Offset sharing:** two `duplicateStream` descriptors observe one file offset;
  `readAt`/`writeAt` remain independent. Do not claim independent buffer reads.
- **Identity equality:** `sameIdentity` is pure equality of opaque bits; copying
  an `Identity` or forging `bits` grants no authority and performs no native
  operation. A metadata operation still requires the owning shell and kind check.
- **Environment copy:** mutate the array returned by `entries`; the original
  snapshot and lookup results do not change.
- **Signals:** a child killed by a known signal reports the library id
  (`TERMINATE` etc.), an unmapped one reports `OTHER`, never the raw Linux
  number; `ExitStatus.Exited.code` is not shifted.
- **Kinds:** every `sysstat::KIND_*` maps to the correct `fs::typeOf`/metadata
  class, including FIFO, socket, block, char and symlink.

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
- TSAN when any native worker/registry is concurrent; the thread note covers
  runtime C plus appropriate generated-code memory accesses. TSAN on C alone
  cannot prove obo code race-free. The thread note owns forced scheduling/litmus
  tests for publication, join, close/use and channel shutdown; stress tests
  supplement, not replace, deterministic schedules.
- Descriptor/socket/mapping/child/thread/registry/buffer live counts, bytes and
  high-water marks per context. Deliberately omit a close to prove the oracle
  detects it. Native counters are separate from OBO_CHECK managed/fd counters;
  current fdstate cannot see C-owned fds. Fail test teardown on either leak.

### 11.3 Conformance and operational observations

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
  systerminal.obx                                              (later)
obo/experimental/systems/        reviewed native adapters, .cbind, fake backend
obo/tests/systems/               obo roots and native/model fixtures
obo/examples/systems/            bounded copy, atomic replace, echo, supervisor
```

Application: `import "sys/sysfile.obx" as file` with `-I obo/lib`; sibling module:
`import "sysdir.obx" as dir`. Unique declared names `module sysfile`, etc. satisfy
the basename rule and the **flat .sym namespace**; do not create another
`module time`, `io` or `env` under a subdirectory (LANGUAGE.md:850–880 and 1371–1395).
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
  eleven-file `WANT_LIB` check at 169–172; it currently lists **only the top level**. Add a
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
  Current `--check` **rejects native calls** (obo.obo:7044); retain that expected
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
| P0: boundary proof | chosen `res: platform::Resource` shell representation, `source`/`requireKind`, P0 checked slot-hint identity table, status mapping, bounded byte bridge, fake backend, leak oracle, compiler-negative `^File`/`^Stream` tests; benchmark bridge | existing experimental cinterop; hosted TLS/errno qualification for libc calls; no compiler additions | 3–4 |
| P1a: smallest useful slice | owned File/open/read/write/close, anchored Directory, Metadata, seek/truncate, monotonic/realtime clock, bounded buffers, getrandom; bounded file-copy example | P0; explicit close and managed OOM limitations; pure calendar/parser tests can proceed independently | 3–5 |
| P1b: practical filesystem | rename/link/mkdir/chmod/sync and atomic replace reports, RFC3339, env snapshots, basic bounded logging; configuration-update example | P1a; openBeneath capability tested separately; mapping is optional P1b extension with SIGBUS contract | 2–4 (+1–2 mapping) |
| P2: network/reactor | numeric addresses, TCP/UDP/Unix sockets, partial/vector I/O, level readiness, timers/wake/fairness, bounded echo and timeout demos; blocking DNS clearly named | P1a; native bridge performance gate, no thread requirement | 4–7 |
| P2T/P2C: optional adapters | TLS transport and streaming compression readiness, not HTTP | separate adapter designs; production hosted boundary/TLS library validation | 3–6 TLS; 1–3 codec, independently scoped |
| P3: processes | spawn/exec/wait, pipes/redirection, process groups, reaper, signals, cwd policy, resolver policy and supervisor example | P2 for nonblocking supervision; process env mutation blocked on coherent env bridge; controlled prefork optional separate audit | 4–6 |

P4 (threads) and P5 (synchronization) are **not estimated or authorized in this
core note**. They are specified in `systems-threads-design.md`; P4a is an
infrastructure spike, while P4b/P5 are blocked on T2 context-relative globals
and the identity table. They keep their own phase gates. Core P0/P1a are the
approvable slice; P2/P3 are directional until their P0 evidence lands.

Implement P1a before networking: it improves ordinary programs immediately and
proves resource/error/copy contracts. P2 may use externally supervised worker
processes without waiting for P3. P3 gives libraries explicit subprocess control.
Terminal raw-mode work is a demand-driven 1–2 week extension after signals, not
a prerequisite for networking. No HTTP module is hidden in any phase. Threads and
synchronization are a separate design and do not gate P0–P3.

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
5. **Hidden retries / timeout restart:** EINTR and EAGAIN are control flow with
   explicit policy; one absolute deadline survives every retry and partial transfer.
6. **GC-dependent resource cleanup or finalizers:** obo has no collector, record
   drop does not close C resources, and resource exhaustion cannot await GC anyway.
7. **Zero-copy pointers across scalar interop / borrowed mapping slices:** violates
   the trusted scalar boundary and 0.12 reference restrictions. Copy first;
   reviewed optimization later.
8. **Make legacy builtins transparently safe/nonblocking:** silently changes retry,
   fd ownership, error and buffering contracts used by tools/compiler. Migrate
   explicitly rather than changing their meaning in a library design.

Thread-specific rejected alternatives (`pthread_create` plus `Mutex`, a
goroutine-style port, generic `Mutex[T]`/`Send`/`Sync`) are in
`systems-threads-design.md`; they are not part of the core library.

## 15. Open product choices and recommendations

Only choices that change product scope remain open; implementation obligations
above are not optional votes about correctness.

| Choice | Recommendation | Consequence of choosing otherwise |
|---|---|---|
| Default service concurrency | reactor per supervised process; spawn/exec supervisor before controlled prefork. Thread/sync choices are in `systems-threads-design.md`. | prefork default needs a runtime/native-library fork audit before advertising it; thread-per-connection is a separate, later design |
| DNS reactive helper | open question: an explicit helper process (P3) or C-only worker (P4); do not silently assume either. The 2026-10-28 database-adapter decision rejected a C helper process for DB work. | a helper process adds lifecycle/framing; a C worker needs thread/TLS qualification; blocking NSS cannot sit in the reactor |
| POSIX abstraction level | Unix resource semantics, Linux implementation and explicit Linux capabilities | exact Linux mirrors weaken portability; lowest-common-denominator POSIX loses path/notification guarantees |
| Stable systems library dependent on hosted C | accept an explicitly native-linked experimental package first; stabilize only after interop qualification | requiring freestanding-only immediately needs another generic native-link/backend design; do not fund dozens of compiler builtins implicitly |
| DB adapter token exception | documented temporary exception: `experimental/db/{postgres,sqlite}` use `ref record { tok: int }` until migrated to the new `res: platform::Resource` registry; do not copy the model into new core modules | leaving the exception unstated creates two native-resource models; a forced migration now expands the DB adapter scope |
| Throughput versus strict scalar copy boundary | measure byte/packed-scalar bridge in P0/P2; keep API representation-independent | if insufficient, request a separate checked bulk-transfer proposal and compiler-budget evidence before changing the seam |

No decision about HTTP version, protocol features, routing, TLS provider or
compression negotiation is made here. Those belong to their separate designs.
