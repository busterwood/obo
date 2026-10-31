# Experimental sys P0 API

Status: **experimental, internal, unstable.** No numeric value below is a
stable ABI. P1a may change or replace these modules before any promotion.

Common rules:

- `Result[T]` errors carry a **library code** from `syserrors`, never a raw
  errno or a runtime trap status.
- A successful creator returns one owned shell with one explicit close
  obligation. Record drop runs no finalizer.
- Every public record field is untrusted at entry and revalidated.
- Native calls are one-shot. Interrupted and WouldBlock are distinct; no public
  one-shot operation retries.
- A `Resource` contains only `reserved: int`, a checked slot/random-nonce hint.

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
pub type Resource = ref record { reserved: int }

pub type Options = ref record {
    maxResources: int
    maxShells: int
    maxNativeBytes: int
    ignorePipeSignal: bool
}

pub type Counts = ref record {
    resources: int
    shells: int
    nativeBytes: int
    cleanupFailures: int
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
pub type Bytes = ref record { res: platform::Resource }

pub fn create(count: int): Result[Bytes]                       // 0..16 MiB
pub fn source(b: &Bytes): Result[^platform::Resource]
pub fn length(b: &Bytes): Result[int]
pub fn setByte(b: &Bytes, index: int, value: int): Result[bool]
pub fn getByte(b: &Bytes, index: int): Result[int]
pub fn writePacked(b: &Bytes, offset: int, packed: int, count: int): Result[int]
pub fn readPacked(b: &Bytes, offset: int, count: int): Result[int]
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

## `systestres` (P0 fixture, not a future File)

```obo
pub type TestResource = ref record { res: platform::Resource }

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
