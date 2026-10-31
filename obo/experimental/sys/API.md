# Experimental sys P0/P1a/P2 API

Status: **experimental, internal, unstable.** No numeric value below is a
stable ABI. P2 adds addresses, sockets, the reactor, waitio and resolution;
P1b/P3 and later phases may change or replace any of these modules before
promotion.

Common rules:

- `Result[T]` errors carry a **library code** from `syserrors`, never a raw
  errno or a runtime trap status.
- A successful creator returns one owned shell with one explicit close
  obligation. Record drop runs no finalizer.
- Every public record field is untrusted at entry and revalidated.
- Native calls are one-shot. Interrupted and WouldBlock are distinct; no public
  one-shot operation retries.
- A `Resource` contains only `reserved: int`, a checked slot/random-nonce hint.

### Field visibility (0.13)

The P1a/P2 public records deliberately mark their documented fields `pub` under
0.13 private-by-default fields. Callers construct `OpenOptions`, `ListenOptions`
and `Address` values, inspect `Metadata`/`Entry`/`Packet`/`Event`, and read a
shell's `res` for the documented public-field limitation. This does not make the record opaque or turn fields
into authority: the managed slot table, nonces, native tokens, registry state
and free lists remain private, and every typed entry point revalidates kind,
generation and liveness. The slot/table internals are not exposed.

## `syserrors`

```obo
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

pub fn code(k: &Kind): int
pub fn kind(code: int): Kind
pub fn fromErrno(errno: int): Kind
pub fn isInterrupted(code: int): bool
pub fn isWouldBlock(code: int): bool
pub fn isRetryable(code: int): bool
pub fn text(k: &Kind): string
```

- Assigned library codes start at 10001. Use `kind`/predicates, not literals.
- `fromErrno` is only for an adapter/wrapper that captured one Linux errno
  before another call. `EDQUOT` maps to `NoSpace` and generation `EOVERFLOW`
  to `ResourceLimit`, matching the design table. Unknown or zero values map to
  `SystemFailure`.
- `isRetryable` is advisory (`Interrupted`/`WouldBlock` only) and does not
  authorize a retry.
- `text` is diagnostic only and is never parsed for control flow.

## `sysplatform`

```obo
// Documented P0 public-field limitation: tests and typed shells construct/read
// these records directly. Later phases may add constructors/accessors.
pub type Resource = ref record { pub reserved: int }

pub type Options = ref record {
    pub maxResources: int
    pub maxShells: int
    pub maxNativeBytes: int
    pub ignorePipeSignal: bool
}

pub type Counts = ref record {
    pub resources: int
    pub shells: int
    pub nativeBytes: int
    pub cleanupFailures: int
}

pub const KIND_FILE = 1          // planned kinds 1..22 are not implemented in P0
pub const ...                    // KIND_DIRECTORY .. KIND_RECEIVER
pub const KIND_TEST_RESOURCE = 90
pub const KIND_TEST_RESOURCE_ALT = 91
pub const KIND_BYTE_BUFFER = 92

pub fn defaultOptions(): Options
pub fn initialize(options: &Options): Result[bool]
pub fn hasCapability(name: string): bool
pub fn source(r: &Resource): Result[^Resource]
pub fn requireKind(r: &Resource, kind: int): Result[^Resource]
pub fn counts(): Result[Counts]
pub fn shutdown(): Result[Counts]
```

### `initialize`

Validates every field before any native call: `maxResources` 1..65536,
`maxShells` 1..1048575, `maxNativeBytes` 1..2147483647; `ignorePipeSignal` is a
boolean policy. Defaults: 4096 resources, 8192 shells, 64 MiB native bytes,
`ignorePipeSignal = true`. First success returns `ok(true)`; reinitializing with
the same policy returns `ok(false)`; a different policy returns `Busy`. Invalid
fields return `InvalidArgument`. Importing the module performs no native action.

`ignorePipeSignal` is stored for P1a pipe APIs. P0 installs no signal handler
and exposes no pipe write, so the flag changes no P0 behavior.

### `source` / `requireKind`

Both require an initialized context and a live registered shell. They check the
slot, random nonce, shell liveness, state and kind, then ask the C registry to
validate its private token. `source` returns `InvalidResource` for unregistered,
closed, stale, future-forged or wrong-nonce shells. `requireKind` returns
`Closed` for a closed shell and `InvalidResource` for a stale or wrong-kind
shell. `source` is the documented bridge for future `watchResource`-style calls;
a typed module never passes a registry token.

The handle forms `_sourceHandle(h: ^Resource)` and
`_requireKindHandle(h: ^Resource, kind: int)` exist for typed modules because
0.12's conservative native effect summary refuses an interior reference across
a function containing a native call. They are internal.

### `counts`

Requires an initialized context. Reports:

- `resources`: live native resources in the C registry;
- `shells`: open slots plus closed tombstones whose shell is still alive
  (open leak records count as open slots until shutdown);
- `nativeBytes`: **logical** C-owned byte-buffer payload bytes currently
  charged; it excludes the +1 guard byte, buffer struct and registry table
  overhead;
- `cleanupFailures`: native release/close failures plus shutdown anomalies.

### `shutdown`

Explicit, idempotent and returns a cached report on later calls. On the first
call it snapshots the **pre-cleanup** census, closes every open slot, asks the
adapter to drain anything left, records failures, clears the managed table and
retires the context. The returned `Counts` keeps pre-cleanup `resources`,
`shells` and `nativeBytes` so leaks are not hidden by post-cleanup zeros.
No native action is legal after shutdown until a new `initialize`. Old shells
cannot address the new context because every slot creation draws a fresh random
nonce. Explicit `close` on a shell from a retired context returns `ok(false)`
without resolving against a new registry; a truly unknown forged shell returns
`NotInitialized`/`InvalidResource`.

### Internal seam

```obo
pub fn _create(kind: int, a: int, b: int): Result[Resource]
pub fn _close(r: &Resource, kind: int): Result[bool]
pub fn _closeHandle(h: ^Resource, kind: int): Result[bool]
pub fn _op(r: &Resource, kind: int, op: int, a: int, b: int, c: int): Result[int]
pub fn _opHandle(h: ^Resource, kind: int, op: int, a: int, b: int, c: int): Result[int]
pub fn _hint(index: int, nonce: int): int      // adversarial tests only
pub fn _bounds(): int                          // slot-hint bound, tests only
```

Exported only because 0.12 has no friend-module visibility. Applications must
not call these. `_close` is the explicit close/release operation: it tombstones
on every outcome and invokes the private C release exactly once; a native close
error still retires the token (Linux close consumes the descriptor). `_op`
validates kind and token before one native operation and never retries.
`shutdown` performs the same release for orphaned open entries after recording
the pre-cleanup census.

## `sysbytes`

```obo
pub type Bytes = ref record { pub res: platform::Resource }

pub fn create(count: int): Result[Bytes]                       // 0..16 MiB
pub fn source(b: &Bytes): Result[^platform::Resource]
pub fn length(b: &Bytes): Result[int]
pub fn setByte(b: &Bytes, index: int, value: int): Result[bool]
pub fn getByte(b: &Bytes, index: int): Result[int]
pub fn writePacked(b: &Bytes, offset: int, packed: int, count: int): Result[int]
pub fn readPacked(b: &Bytes, offset: int, count: int): Result[int]
pub fn readInt64(b: &Bytes, offset: int): Result[int]
pub fn writeInt64(b: &Bytes, offset: int, value: int): Result[int]
pub fn fill(b: &Bytes, value: int): Result[int]
pub fn copyIn(b: &Bytes, src: &[]byte, srcOffset: int, dstOffset: int,
              count: int): Result[int]
pub fn copyInScalar(b: &Bytes, src: &[]byte, srcOffset: int, dstOffset: int,
                    count: int): Result[int]
pub fn copyOut(b: &Bytes, mut dst: &[]byte, srcOffset: int, dstOffset: int,
               max: int): Result[int]
pub fn copyOutScalar(b: &Bytes, mut dst: &[]byte, srcOffset: int, dstOffset: int,
                     max: int): Result[int]
pub fn close(b: &Bytes): Result[bool]
```

- `create` accepts `0..MAX_BYTE_BUFFER` (16 MiB); larger values are `Range`,
  negative values `Range`, before a native call. Exact 16 MiB is accepted and
  one past it is rejected. The C adapter independently enforces the same
  ceiling and the configured `maxNativeBytes` quota (`NoSpace`; the registry
  raw adapter code remains `EDQUOT`).
- `writePacked`/`readPacked` use `count` 1..7 and a nonnegative packed value.
  `writePacked` rejects a value that does not fit the requested bytes
  (`packed >= 1 << (8*count)`) with `Range`; `readPacked` returns a positive
  56-bit value.
- `copyIn` copies exactly `count` bytes from `src[srcOffset..]` to
  `buffer[dstOffset..]`; `copyOut` returns the short count at the buffer end.
- Both ranges are validated before the first native call. Embedded NUL bytes
  are ordinary data. No C `strlen` or NUL terminator is used for length.
- `copyInScalar`/`copyOutScalar` are the one-byte reference path.

## `sysstat`

```obo
pub type Identity = ref record { pub bits: int }
pub type Metadata = ref record {
    pub kind: int; pub permissions: int; pub size: int; pub links: int
    pub uid: int; pub gid: int
    pub modified: clock::Timestamp; pub accessed: clock::Timestamp
    pub changed: clock::Timestamp; pub created: ?clock::Timestamp
    pub identity: Identity
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

Metadata is a detached snapshot decoded from a fixed 16-field C packet.
Permissions are the low permission bits. `created` is nil when the backend has
no birth time. `Identity.bits` is one opaque, context-local interned
`(device,inode)` bit pattern; equality only, never persisted or interpreted as
an error/hash. `sameIdentity` is pure bit equality and grants no authority.

## `sysclock`

```obo
pub type Instant = ref record { pub nanos: int }
pub type Duration = ref record { pub nanos: int }
pub type Deadline = ref record { pub finite: bool; pub nanos: int }
pub type Timestamp = ref record { pub seconds: int; pub nanos: int }

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

Durations are nonnegative. Arithmetic is checked; overflow is `Range`, not
wrap/saturation. `remaining` returns nil only for `forever()` and zero at/before
the deadline. `sleepUntil` is interruptible (`Interrupted`) and never restarts a
deadline; an infinite deadline is `InvalidArgument` for a finite sleep call.

## `sysrandom`

```obo
pub const MAX_RANDOM_BYTES = 1048576
pub fn fillSome(mut bytes: &[]byte, offset: int, count: int,
                blocking: bool): Result[int]
pub fn bytes(count: int, blocking: bool): Result[[]byte]
```

`fillSome` writes only within the existing destination and returns short kernel
progress. The error publishes no partial destination bytes. `bytes` is a bounded
compound helper (1 MiB cap) that keeps trying after short fills but stops on the
first error. `blocking = false` can return `WouldBlock`; EINTR is `Interrupted`.
No userspace PRNG or fallback source exists.

## `sysfile`

```obo
pub type File = ref record { pub res: platform::Resource }
pub type OpenOptions = ref record {
    pub read: bool; pub write: bool; pub create: bool; pub exclusive: bool
    pub truncate: bool; pub append: bool; pub noFollow: bool
    pub nonblocking: bool; pub permissions: int
}
pub const SEEK_START = 0
pub const SEEK_CURRENT = 1
pub const SEEK_END = 2
pub const MAX_CHUNK = 1048576

pub fn readOptions(): OpenOptions
pub fn open(path: string, options: &OpenOptions): Result[File]
pub fn openAt(base: &dir::Directory, name: string, options: &OpenOptions): Result[File]
pub fn readSome(f: &File, max: int): Result[?[]byte]
pub fn writeSome(f: &File, bytes: &[]byte, offset: int, count: int): Result[int]
pub fn readAt(f: &File, position: int, max: int): Result[?[]byte]
pub fn writeAt(f: &File, position: int, bytes: &[]byte): Result[int]
pub fn seek(f: &File, offset: int, origin: int): Result[int]
pub fn truncateTo(f: &File, length: int): Result[bool]
pub fn metadata(f: &File): Result[stat::Metadata]
pub fn duplicateStream(f: &File): Result[stream::Stream]
pub fn intoStream(mut f: &File): Result[stream::Stream]
pub fn close(f: &File): Result[bool]
```

Options are copied at open and validated before native work: at least one of
read/write; create/exclusive/truncate/append require write; exclusive requires
create; truncate and append are mutually exclusive; permissions are 0..0xFFF.
Every open is CLOEXEC. `readSome`/`writeSome` make one attempt; a short transfer
is success, EOF is nil, large requests are capped at 1 MiB. `readAt`/`writeAt`
never move the shared offset, and positional writes on an append-open File,
including a zero-length write, are `InvalidArgument`. `close` never retries after EINTR. `intoStream` re-tags the
live resource and replaces the old shell's `res`; `duplicateStream` shares the
open file description (offset and flags), matching `dup(2)`.

## `sysdir`

```obo
pub type Directory = ref record { pub res: platform::Resource }
pub type Iterator = ref record { pub res: platform::Resource }
pub type Entry = ref record { pub name: string; pub kind: int }

pub fn open(path: string): Result[Directory]
pub fn source(base: &Directory): Result[^platform::Resource]
pub fn child(base: &Directory, name: string): Result[Directory]
pub fn iterate(base: &Directory): Result[Iterator]
pub fn next(it: &Iterator): Result[?Entry]
pub fn metadata(base: &Directory, name: string, follow: bool): Result[stat::Metadata]
pub fn close(base: &Directory): Result[bool]
pub fn closeIterator(it: &Iterator): Result[bool]
```

`open` follows host symlinks. `child`, `metadata` and `openAt` accept one
component only (nonempty, no `/`, NUL, `.` or `..`); `child` opens NOFOLLOW.
`next` skips `.`/`..`, returns owned names, reports Unknown kind rather than
hiding a failed stat, and fails permanently on corrupt dirents. Order is not
promised and this is not a path-containment API. `iterate` duplicates the
anchor descriptor, so closing the Directory does not invalidate the Iterator;
because `dup(2)` shares the open file description, the Directory and all
iterators made from it share one directory offset. Use one active iterator per
anchor and do not assume independent snapshots.

## `sysstream`

```obo
pub type Stream = ref record { pub res: platform::Resource }
pub type Standard = closed { Input {} Output {} Error {} }
pub const MAX_CHUNK = 1048576

pub fn source(s: &Stream): Result[^platform::Resource]
pub fn standard(which: &Standard): Result[Stream]
pub fn standardInput(): Result[Stream]
pub fn standardOutput(): Result[Stream]
pub fn standardError(): Result[Stream]
pub fn readSome(s: &Stream, max: int): Result[?[]byte]
pub fn readInto(s: &Stream, mut dst: &[]byte, offset: int, max: int): Result[int]
pub fn writeSome(s: &Stream, bytes: &[]byte, offset: int, count: int): Result[int]
pub fn writeFrom(s: &Stream, src: &[]byte, offset: int, count: int): Result[int]
pub fn close(s: &Stream): Result[bool]
pub fn closeStream(s: ^Stream): Result[bool]
```

Standard streams are owned CLOEXEC duplicates; process fds 0/1/2 are never
closed by this module. Read/write are one attempt with short success, nil/0 EOF
and distinct WouldBlock.

P2 also provides:

```obo
pub const MAX_VECTOR_PARTS = 64
pub const MAX_VECTOR_BYTES = 1048576
pub fn shutdownRead(s: &Stream): Result[bool]
pub fn shutdownWrite(s: &Stream): Result[bool]
pub fn writeVector(s: &Stream, parts: &[][]byte, first: int,
                   offset: int): Result[int]
pub fn readVector(s: &Stream, sizes: &[]int): Result[?[][]byte]
```

Half-close applies to socket-backed streams; a file/pipe stream returns
`Unsupported`. `syswaitio` helpers therefore require a socket-backed Stream. `writeVector` composes one contiguous bounded transfer (it does
not promise one writev syscall), so a short return advances a caller cursor
across the vectors. `readVector` returns owned arrays, the final one possibly
short; nil is EOF before any data.

## `sysbuffer`

```obo
pub type Buffer = ref record { pub bytes: []byte; pub start: int; pub end: int; pub limit: int }
pub type Reader = ref record { pub buffer: Buffer; pub eof: bool }
pub type Writer = ref record { pub buffer: Buffer }
pub type Cursor = ref record { pub done: int }
pub const DEFAULT_LIMIT = 65536
pub const MAX_BUFFER_BYTES = 16777216
pub const MAX_STEP_BYTES = 1048576

pub fn make(limit: int): Result[Buffer]
pub fn available(b: &Buffer): Result[int]
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

Storage is allocated once; `len(bytes) == limit` and
`0 <= start <= end <= limit` are revalidated at entry. `feed` is all-or-nothing
with respect to capacity (it compacts but never appends past the allocation).
`find` is binary-safe and returns an absolute buffer index or -1. `copy` returns
an independent prefix. `readStep` appends one transfer and retains data on
error; `writeStep` removes only the written prefix. Exact steps attempt one
transfer, keep progress on every exit, and report `EndOfInput` when EOF arrives
before the requested count.

## `sysaddress`

```obo
pub type Address = closed {
    IPv4 { octets: []byte; port: int }
    IPv6 { octets: []byte; port: int; scope: int }
    UnixPath { path: string }
    UnixAbstract { name: []byte }
}
pub const FAMILY_NONE = 0
pub const FAMILY_IPV4 = 1
pub const FAMILY_IPV6 = 2
pub const FAMILY_UNIX_PATH = 3
pub const FAMILY_UNIX_ABSTRACT = 4
pub const MAX_PORT = 65535
pub const MAX_UNIX_PATH = 107
pub const MAX_UNIX_ABSTRACT = 107
pub fn validate(a: &Address): Result[bool]
pub fn parseNumeric(text: string, port: int): Result[Address]
pub fn format(a: &Address): Result[string]
```

IPv4 has exactly 4 octets, IPv6 exactly 16, ports are 0..65535 and IPv6 scope
is 0..4294967295. Unix paths are nonempty, NUL-free and at most 107 bytes;
abstract names are nonempty and at most 107 bytes. `parseNumeric` does no DNS,
rejects brackets, does not guess octal IPv4 and does not parse embedded IPv4 in
IPv6 text. `format` is deterministic lowercase and returns the host/path text
without the port (the record carries the port separately); abstract names use
`unix-abstract:<hex>` because they have no portable text form. Host/network byte
order is a private adapter detail. Every `listen`/`connect`/`bindDatagram`/`send`
revalidates and re-encodes the caller's record before a native call.

## `sysnet`

```obo
pub type Listener = ref record { pub res: platform::Resource }
pub type Connection = ref record { pub res: platform::Resource }
pub type Datagram = ref record { pub res: platform::Resource }
pub type ListenOptions = ref record {
    pub backlog: int; pub reuseAddress: bool
    pub reusePort: bool; pub ipv6Only: bool
}
pub type Packet = ref record {
    pub bytes: []byte; pub peer: ?Address
    pub truncated: bool; pub originalSize: int
}
pub const MAX_CHUNK = 1048576
pub const MAX_BACKLOG = 65535
pub const MAX_DATAGRAM = 65507

pub fn listenOptions(): ListenOptions
pub fn listen(a: &Address, options: &ListenOptions): Result[Listener]
pub fn accept(l: &Listener): Result[Connection]
pub fn connect(a: &Address): Result[Connection]
pub fn finishConnect(c: &Connection): Result[bool]
pub fn readSome(c: &Connection, max: int): Result[?[]byte]
pub fn readInto(c: &Connection, mut dst: &[]byte, offset: int,
                max: int): Result[int]
pub fn writeSome(c: &Connection, bytes: &[]byte, offset: int,
                 count: int): Result[int]
pub fn writeFrom(c: &Connection, src: &[]byte, offset: int,
                 count: int): Result[int]
pub fn shutdownRead(c: &Connection): Result[bool]
pub fn shutdownWrite(c: &Connection): Result[bool]
pub fn duplicateStream(c: &Connection): Result[stream::Stream]
pub fn intoStream(mut c: &Connection): Result[stream::Stream]
pub fn localAddress(c: &Connection): Result[Address]
pub fn peerAddress(c: &Connection): Result[Address]
pub fn listenerLocalAddress(l: &Listener): Result[Address]
pub fn datagramLocalAddress(d: &Datagram): Result[Address]
pub fn setNoDelay(c: &Connection, enabled: bool): Result[bool]
pub fn setKeepAlive(c: &Connection, idleSeconds: int,
                    intervalSeconds: int, probes: int): Result[bool]
pub fn clearKeepAlive(c: &Connection): Result[bool]
pub fn setReceiveBuffer(d: &Datagram, bytes: int): Result[bool]
pub fn setSendBuffer(d: &Datagram, bytes: int): Result[bool]
pub fn setBroadcast(d: &Datagram, enabled: bool): Result[bool]
pub fn watchConnection(r: &reactor::Reactor, c: &Connection, readable: bool,
                       writable: bool, tag: int): Result[reactor::Watch]
pub fn watchListener(r: &reactor::Reactor, l: &Listener,
                     tag: int): Result[reactor::Watch]
pub fn bindDatagram(a: &Address): Result[Datagram]
pub fn receive(d: &Datagram, max: int): Result[Packet]
pub fn send(d: &Datagram, to: &Address, bytes: &[]byte): Result[int]
pub fn closeConnection(c: &Connection): Result[bool]
pub fn closeListener(l: &Listener): Result[bool]
pub fn closeDatagram(d: &Datagram): Result[bool]
pub fn sourceConnection(c: &Connection): Result[^platform::Resource]
pub fn sourceDatagram(d: &Datagram): Result[^platform::Resource]
```

Sockets are nonblocking and CLOEXEC atomically at creation. `listenOptions`
defaults to backlog 128, reuseAddress true, reusePort false, ipv6Only true; an
IPv6 socket is never accidentally dual-stack. The kernel may cap a larger
requested backlog to `somaxconn`. Datagram binds apply the same IPv6-only
default, so `::` does not reserve the matching IPv4 port. Keepalive idle,
interval and probe values are positive and capped at 2147483. `accept` returns WouldBlock on an
empty queue and ResourceLimit on EMFILE/ENFILE; it makes one attempt. `connect`
returns an owned Connection while establishment is pending. `finishConnect`
returns false while pending, true when connected, or the captured SO_ERROR
failure; writability alone is not success. `intoStream` returns InProgress and
leaves a Connecting connection unchanged; a failed connection is retired once
and its state error is preserved. `duplicateStream` shares the socket and
half-close state but owns a separate descriptor. A datagram send is all-or-error;
receive preserves empty datagrams, peer and truncation (`originalSize` exact on
Linux). Port 0 is discoverable before accepting/sending. `peer` is nil only for a
kernel-unnamed sender; P2 has no unbound datagram constructor, so applications
normally observe the sender's bound address.

## `sysreactor`

```obo
pub type Reactor = ref record { pub res: platform::Resource }
pub type Watch = ref record { pub res: platform::Resource }
pub type Timer = ref record { pub res: platform::Resource }
pub type Event = ref record {
    pub tag: int; pub readable: bool; pub writable: bool; pub hangup: bool
    pub failed: bool; pub timer: bool; pub ticks: int; pub wake: bool
}
pub const EVENT_BYTES = 64
pub const MAX_EVENTS = 4096
pub const MAX_BATCH = 64
pub fn create(maxWatches: int, maxTimers: int): Result[Reactor]
pub fn watchResource(r: &Reactor, source: ^platform::Resource,
                     readable: bool, writable: bool, tag: int): Result[Watch]
pub fn modify(w: &Watch, readable: bool, writable: bool): Result[bool]
pub fn remove(w: &Watch): Result[bool]
pub fn timer(r: &Reactor, at: &clock::Deadline,
             period: &clock::Duration, tag: int): Result[Timer]
pub fn cancelTimer(t: &Timer): Result[bool]
pub fn wait(r: &Reactor, until: &clock::Deadline,
            maxEvents: int): Result[[]Event]
pub fn wake(r: &Reactor): Result[bool]
pub fn stop(r: &Reactor): Result[bool]
pub fn close(r: &Reactor): Result[bool]
```

Linux v1 is level-triggered epoll only. `timer` requires a finite `at`; a zero
period is one-shot. `wait` accepts `forever()` and returns an empty batch on
deadline, at most 64 events per call. Events carry application tags only; native
registry tokens/generations never reach the caller, and stale registrations are
filtered. `failed`/`timer`/`wake` are mutually exclusive and `failed` carries no
I/O bits. Timers are kept in an insertion-order linked list and scanned each
turn (bounded by `maxTimers`); due timers fill the batch before socket
readiness. A fresh eventfd wake is drained and emitted before due timers so an
always-due timer cannot hide it; a wake coalesced from a full batch alternates
with due timers, and an infinite wait never blocks while one is pending. Ready
subscriptions rotate between batches. Endpoint close deregisters its watches; reactor close invalidates all
children and `remove`/`cancelTimer` remain idempotent. `stop` prevents new
registrations, wakes waiters and makes subsequent `wait` return `Cancelled`.
Watching a regular file returns `Unsupported` (it is not generally
epoll-watchable).

## `syswaitio`

```obo
pub type Cancellation = ref record { pub res: platform::Resource }
pub fn cancellation(): Result[Cancellation]
pub fn request(c: &Cancellation): Result[bool]
pub fn cancelled(c: &Cancellation): Result[bool]
pub fn closeCancellation(c: &Cancellation): Result[bool]
pub fn deadlineReached(until: &clock::Deadline): Result[bool]
pub fn readExactUntil(s: &stream::Stream, r: &reactor::Reactor, count: int,
                      mut out: &buffer::Buffer, until: &clock::Deadline,
                      cancel: &Cancellation): Result[bool]
pub fn writeAllUntil(s: &stream::Stream, r: &reactor::Reactor,
                     bytes: &[]byte, mut cursor: &buffer::Cursor,
                     until: &clock::Deadline,
                     cancel: &Cancellation): Result[bool]
pub fn flushUntil(mut w: &buffer::Writer, s: &stream::Stream,
                  r: &reactor::Reactor, until: &clock::Deadline,
                  cancel: &Cancellation): Result[bool]
```

Cancellation is a sticky native flag plus eventfd wake. These compound helpers
use one absolute deadline, never restart it on EINTR/WouldBlock/progress, and
retain buffer/cursor progress on every exit. EAGAIN/EINTR with no progress is
ordinary backpressure: the helper waits for readiness and retries; only a
non-retryable zero-progress transfer is terminal. `readExactUntil.count` is additional
bytes appended after the existing buffer prefix. They consume readiness batches,
so they require a **dedicated reactor**; event-driven callers use the buffer
step APIs with `cancelled`/`deadlineReached` instead.

## `sysresolve`

```obo
pub const FAMILY_ANY = 0
pub const FAMILY_IPV4 = 4
pub const FAMILY_IPV6 = 6
pub const SOCKET_STREAM = 1
pub const SOCKET_DATAGRAM = 2
pub const MAX_RESULTS = 64
pub fn resolveBlocking(host: string, service: string, family: int,
                       kind: int): Result[[]address::Address]
pub fn resolveNumeric(host: string, service: string, family: int,
                      kind: int): Result[[]address::Address]
pub fn resolveTcp(host: string, service: string): Result[[]address::Address]
pub fn resolveUdp(host: string, service: string): Result[[]address::Address]
```

Host is 1..255 bytes and service 0..31 bytes, both NUL-free. Results preserve
getaddrinfo/NSS order; the C adapter copies them and always calls
`freeaddrinfo`. EAI_AGAIN is ResolveTemporary, EAI_NONAME/NODATA is
ResolveNotFound and other EAI failures are ResolveFailure; EAI_SYSTEM preserves
the captured errno category. `resolveNumeric` uses AI_NUMERICHOST and
AI_NUMERICSERV and needs no NSS/DNS. `resolveBlocking` is **blocking with no finite deadline** and is not for reactor
loops. No cache, TTL, helper process or reactive resolver is part of P2. The
fake resolver is a deterministic table (`localhost`, `example.test`,
`resolve-again.test`, `notfound.test`, `system.test`) plus numeric parsing; it
performs no NSS/DNS.

## `systestres` (P0 fixture, not a future File)

```obo
pub type TestResource = ref record { pub res: platform::Resource }

pub fn open(): Result[TestResource]
pub fn openAlt(): Result[TestResource]
pub fn source(t: &TestResource): Result[^platform::Resource]
pub fn sourceAsAlt(t: &TestResource): Result[^platform::Resource]
pub fn write(t: &TestResource, value: int): Result[bool]
pub fn read(t: &TestResource): Result[int]
pub fn progress(t: &TestResource): Result[int]     // fake script hook; real = Unsupported
pub fn close(t: &TestResource): Result[bool]
pub fn closeHandle(t: ^TestResource): Result[bool]
pub fn closeAlt(t: &TestResource): Result[bool]
pub fn hint(t: &TestResource): int                 // tests only
```

The real backend is a CLOEXEC, nonblocking `eventfd`; an empty read is
`WouldBlock`, a zero write is `InvalidArgument`, and a written value is returned
in the range `1..2^63-1`. The fake backend is a C-owned counter with the same
public behavior plus scriptable failures/progress/time. This module is a
boundary fixture, not a promise of a future Event/File API.

## Adapter convention (C side)

Exported symbols take only `long` and return `long`. `>= 0` is success or
progress; a negative result is `-errno` captured before any intervening call.
The obo wrapper maps it immediately with `syserrors::fromErrno`. No pointer,
struct-by-value, callback, variadic or float crosses to obo. The registry token
is held only in the module-private slot table and never appears in a public
record or application call.

## `sysshells` (P0 type-identity fixture)

`sysshells` defines P0-only `File` and `Stream` shells and `acceptStream`/
`acceptResource` functions. They have no I/O behavior. They exist so the test
suite can prove with `obo --check` that a `^File` cannot be passed where a
`^Stream` or `^platform::Resource` is required. P1a replaces their role with the
real owned File/Directory/Stream modules.
