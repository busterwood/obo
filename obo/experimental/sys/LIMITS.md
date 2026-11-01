# Experimental sys P0/P1a/P1b/P2q/P3 limits

These limits are part of the P0/P1a/P1b/P2/P3 boundary proof. They are deliberately
explicit rather than hidden behind a stable-looking API.

## Scope

- **P0, P1a, P1b, P2, P2q and P3.** P0 provided the identity/error/byte seam;
  P1a adds owned `File`, anchored `Directory`/`Iterator`, metadata snapshots,
  clocks, `getrandom`, concrete streams and bounded buffers; P1b adds anchored
  directory mutation, chmod/sync, rename/link/symlink, the atomic replacement
  transaction, openat2 containment, RFC3339 text, environment snapshots and
  bounded logging. P2 adds numeric addresses, TCP/UDP/Unix sockets, an epoll
  reactor, cancellation/deadline helpers and blocking resolution. P2q adds the
  bounded UDP batch, GSO/GRO reporting, an explicit one-datagram fallback,
  datagram watch and the concrete QUIC bind profile. P3 adds `spawn`/exec,
  wait/reap, pipes/redirection, process groups, queued signal delivery, cwd
  policy and terminal raw-mode ownership. There is still no mapping,
  environment mutation, reactive DNS API, thread or synchronization kind.
- **One process context, single thread.** The managed table and C registry are
  process globals. There is no worker context selection, lock, atomic or TSAN
  claim.
- **Linux x86-64 real backend.** The real adapter uses openat/read/write/lseek/
  ftruncate/fstat/statx/getdents64/clock_gettime/getrandom, socket/bind/listen/
  accept4/connect/recv/send/sendto/recvmsg/sendmsg/shutdown, setsockopt and
  `UDP_SEGMENT`/`UDP_GRO` ancillary control, epoll/eventfd, poll for
  nonblocking connect completion, and getaddrinfo. Other platforms would need a
  different adapter and are not claimed. P2q targets Linux 4.18+ for GSO/GRO;
  an older kernel clears those features rather than failing the bind. The v1
  reactor is level-triggered only; there is no poll-reference backend or
  edge-triggered mode.
- **Hosted cinterop only.** The package uses the experimental hosted
  cinterop link (non-PIE fixed layout). The freestanding compiler/runtime is
  unchanged and does not link this package.
- **Trusted C.** The registry token protects against accidental stale, forged,
  wrong-kind or double use by ordinary obo code. It is not a security boundary
  against malicious trusted C, a tampered `.cbind`/header/sidecar, or a
  deliberate caller.

## Identity and lifetime

- `Resource.reserved` is a slot+nonce **hint**, not a capability. It uses 20
  slot bits plus an unpredictable 40-bit per-slot nonce. Slot indices are
  1-based in the hint. The nonce is generated in C from `getrandom` with a
  `/dev/urandom` fallback; 0 and 1 are never issued. It is not a registry token
  and is never treated as authority by itself.
- A new slot creation gets a fresh nonce, checked against all current and
  retired nonces (up to four retries). A guessed hint forged before a slot is
  populated therefore stays invalid after population. A stale hint after slot
  reuse fails the nonce check. The managed nonce is separate from the C token
  generation, which still retires instead of wrapping at its 31-bit ceiling.
- The managed table has a fixed configured slot ceiling. A dead tombstone is
  swept on insertion; an **open** entry whose shell died is a leak record and is
  never silently swept. It keeps its slot until explicit shutdown, so repeated
  omission of close can exhaust the slot/resource cap.
- Documented value/shell fields are explicitly `pub` under 0.13 private-by-default
  records and remain writable by callers. An exact copied live hint aliases
  the same entry **only while the registered owner shell is alive**; after the
  owner dies the same hint fails the liveness check. A replaced same-kind `res`
  is the documented alias/steal limitation. A copied hint cannot create a C
  token or a second native object, and cross-kind use is rejected on every
  entry. Do not present this layer as field-opaque or as protection against a
  hostile caller.
- Closed tombstones answer `ok(false)` to a second close only while their shell
  is alive. After the shell dies the slot may be reused; an old hint then fails
  on the nonce check. A shell from a retired context still answers explicit
  close with `ok(false)` after shutdown/reinitialization, without resolving
  against the new registry.
- Retired-context hint/kind records are retained for the process lifetime so
  retired-shell `close` can answer `ok(false)` without a registry lookup. The
  list is small per retired shell but grows with repeated shutdowns; P0 does not
  garbage-collect it.
- There are **no finalizers**. Dropping a record closes nothing. There is no
  background sweeper, GC integration or destructor callback.
- `close` is Linux-semantics: a close that reports EINTR/EIO still consumes the
  descriptor and the token is retired; it is never retried.

## Capacity

| Limit | Default | P0 hard ceiling |
|---|---:|---:|
| Live native resources | 4096 | 65536 |
| Live shells/slots (open + closed tombstones) | 8192 | 1048575 |
| C-owned logical byte payload charged | 64 MiB | 2 GiB (`maxNativeBytes`) |
| One `sysbytes` buffer | 16 MiB | 16 MiB (`MAX_BYTE_BUFFER`) |
| Packed bridge group | 7 bytes | 7 bytes |
| One file/stream transfer chunk | 1 MiB | 1 MiB (`MAX_CHUNK`) |
| One `sysrandom` request | 1 MiB | 1 MiB (`MAX_RANDOM_BYTES`) |
| One bounded buffer | 64 KiB default | 16 MiB (`MAX_BUFFER_BYTES`) |
| Path string | 4095 bytes | 4095 bytes |
| One directory component name | 255 bytes | 255 bytes |
| Symlink target | 4095 bytes | 4095 bytes (`NameTooLong` above) |
| `replaceAtomic` payload | 16 MiB | 16 MiB |
| One log record | 4096 bytes | 4096 bytes (`LimitExceeded`) |
| One log queue | 1 byte..1 MiB at open | 1 MiB |
| Environment snapshot | captured size | 1 MiB / 65536 entries |
| Metadata packet | 16 fields | 128 bytes |
| Address packet / resolver record | 160 bytes | 160 bytes |
| IPv4 / IPv6 address | 4 / 16 octets | 4 / 16 octets |
| Port / IPv6 scope | 0..65535 / 0..2^32-1 | same |
| Unix path / abstract name | 107 bytes | 107 bytes |
| One TCP/Unix stream transfer | 1 MiB | 1 MiB (`MAX_CHUNK`) |
| One datagram message | 65507 bytes | 65507 bytes (`MAX_DATAGRAM`) |
| One datagram batch | 16 (QUIC plan) | 64 datagrams / 65507 total bytes |
| One GSO send | 16 (QUIC plan) | 64 segments / 65507 total bytes |
| Datagram socket buffer | 4 MiB | 1 GiB per direction |
| One reactor batch | 64 events | 4096 requested, 64 returned (`MAX_BATCH`) |
| Reactor watches / timers | configured at create | 65536 each |
| Resolver results | 64 | 64 (`MAX_RESULTS`) |
| Resolver host / service text | 255 / 31 bytes | 255 / 31 bytes |
| Vector parts / total | 64 / 1 MiB | 64 / 1 MiB |

`Options.maxResources`, `maxShells` and `maxNativeBytes` must be positive and
within the ceilings. The native byte quota charges the **logical payload
length** per buffer; it excludes the +1 guard byte, buffer struct and registry
table overhead. It is released on explicit close or shutdown. Exhaustion returns
`ResourceLimit`, `NoSpace` (EDQUOT quota) or `Range` as appropriate; it does not
allocate past the stated capacity.

## Errors

- Library codes 10001.. are implementation-assigned and **not stable**. Use
  `syserrors::kind`, `code` and predicates.
- `fromErrno` only translates a captured Linux errno. There is no process-global
  `lastError`; the C adapter captures errno before any other call. `EDQUOT`
  maps to `NoSpace`; generation `EOVERFLOW` maps to `ResourceLimit`.
- Unknown native errors are `SystemFailure`; diagnostic text is not parsed.
- `Interrupted` (EINTR) and `WouldBlock` (EAGAIN) remain separate. No public
  one-shot operation retries. Close never retries. `isRetryable` is advisory.
- `ignorePipeSignal` is validated and stored for later pipe APIs. P1a exposes no
  pipe write and installs no signal disposition, so it changes no P1a behavior.

## Byte bridge

- All sizes and offsets are validated before indexing or a native call. A
  negative size, overflow, or `offset + count > length` is rejected; arithmetic
  is done with subtraction/unsigned checks in C to avoid wraparound.
- Copies are length-delimited and NUL-safe. There is no implicit string
  conversion, no `strlen`, and no pointer/borrow retained after the call.
- `copyIn`/`copyOut` validate both the obo array range and the C buffer range
  before the first chunk. A mid-copy adapter failure can leave a prefix
  written; the P0 wrapper returns the error and does not claim rollback.
- The 7-byte packing fits a positive signed 56-bit value. It is not an 8-byte
  `u64` ABI. A packed value that does not fit the requested byte count is
  rejected by both the obo wrapper and C (`Range`); exact-16-MiB and
  one-past-16-MiB probes are in the C and obo tests.
- Throughput is measured, not assumed. P0 records scalar and packed results;
  P2 may request a separately reviewed bulk-copy design if the bridge is the
  limiting factor.

## P1a file and buffer limits

- Open options are a library record, not a Linux flag word. Validation is
  repeated before native work; C independently rejects inconsistent flags.
  CLOEXEC is added atomically by the adapter. `permissions` is 0..0xFFF and
  `umask` still applies on creation. Under 0.13, the documented record fields
  (options, metadata, entries, timestamps, buffer fields and each shell's `res`)
  are explicitly `pub`; the slot table, nonce, token and registry state are not.
- `readSome`/`writeSome`/`readAt`/`writeAt` move at most 1 MiB per call. A
  positive short result is success and is never silently repeated. `readInto`
  and `writeFrom` use temporary C-owned scratch buffers through the P0 byte
  bridge, so they are bounded allocation-free **forms** for the caller's result
  array, not allocation-free native paths.
- `readAt`/`writeAt` do not move the file offset. A positional write on an
  append-open File is rejected (`InvalidArgument`) even with a zero byte count,
  instead of inheriting the kernel's pwrite/O_APPEND ambiguity. A zero-length
  current-position `writeSome` returns 0 after resource validation. Negative or
  nonrepresentable seek results map to `Range`. Nonblocking regular-file I/O may
  still block in storage.
- `duplicateStream` shares the open file description, so offset and status
  flags are shared. `intoStream` re-tags the live resource to `KIND_STREAM` and
  replaces the old shell's `res`; the old File shell then fails typed operations.
- Anchored names are single components. This is not proof against concurrent
  rename/mount namespace changes. `openBeneath` with RESOLVE_BENEATH is deferred.
- Directory iteration uses a bounded 8 KiB getdents64 buffer, at most 64
  refills per `next`, maximum name length 255 and no ordering/snapshot promise.
  The iterator duplicates its anchor with CLOEXEC; `dup(2)` shares the open
  file description, so the Directory and its iterators share one directory
  offset. Use one active iterator per anchor. The fake backend yields a scripted
  `.`/`..`/`alpha`/`beta`/`gamma(Unknown)` root and a single anchored child
  named `sub`; it is not a general directory tree.
- Buffer storage is `new []byte(limit)` with `make`; it never grows by append.
  `feed` compacts and is all-or-nothing by capacity. `copy` allocates an owned
  prefix. Exact steps attempt one transfer and retain progress; `readExactStep`
  requires enough fixed free capacity before reading. `writeStep` copies only a
  1 MiB prefix of a larger pending range and returns that progress; the next
  call continues from the compacted remainder.
- Metadata identity interning is bounded at 65536 pairs per context. Exhaustion
  is `ResourceLimit`; entries live until shutdown. Identity bits are not stable
  across processes or restarts and must never be persisted.
- `statx` supplies birth time when available; a `stat`/`fstat` fallback reports
  birth as nil. Timestamps are integer seconds+nanos, never floats.
- Clock arithmetic is checked signed 64-bit nanoseconds. `realtimeNs` has the
  narrower 1677–2262 range and returns `Range` outside it. Infinite deadlines are
  a flag; `sleepUntil` rejects them rather than inventing a duration.
- Randomness comes only from `getrandom`; there is no insecure fallback. One
  call is capped at 1 MiB. Fake deterministic entropy exists only in the
  test-only fake link and is never an application-selected production mode.

## P1b practical-filesystem limits

- Anchored `mkdir`, `unlink`, `removeEmpty`, `readLink`, `rename`, `link` and
  `symlink` names are single components. They are anchored to the opened
  directory and are not a path-containment claim against concurrent rename or
  mount-namespace changes. `removeEmpty` maps kernel `ENOTEMPTY` to `Busy`; the
  error namespace has no `NotEmpty` kind.
- `openBeneath` needs Linux openat2 (5.6+). It requests
  `RESOLVE_BENEATH|RESOLVE_NO_MAGICLINKS` and an explicit `noFollow` option. A
  kernel without openat2 returns `Unsupported`, never a lexical path check.
  Containment escape is the kernel's `EXDEV` → `CrossDevice`; an absolute path
  is also `EXDEV` under `RESOLVE_BENEATH`; a magic link is `PathLoop`. This does
  not promise protection against a privileged mount race or a caller that
  deliberately aliases live resources.
- `rename` is atomic for name visibility within one filesystem. `replace=false`
  requires `RENAME_NOREPLACE`; absence is `Unsupported`, never an existence
  check followed by overwrite. Because Linux filesystems are inconsistent about
  reporting “flags unsupported”, an `EINVAL` from `renameat2` on the no-replace
  path (with validated component names) is treated as that absence; every other
  captured error is preserved. Cross-filesystem rename is `CrossDevice`; there
  is no copy/delete fallback. `link` does not follow a source symlink and a
  directory source is `Permission`. `symlink` stores the target bytes verbatim
  and does not validate the target's existence.
- `replaceAtomic` caps the payload at 16 MiB (the byte-bridge cap) and creates
  the temporary in the target directory. It owns a finite retry-free transaction:
  a short write advances the cursor, EINTR is not retried, and no partial target
  is published. Precommit failures return `err` after closing and removing the
  temporary; a cleanup unlink failure is not hidden but cannot roll back the
  primary error. The rename is the commit point. Directory-fsync failure after
  commit is `ok` with `committed=true, durable=false,
  code=DurabilityUnknown`; it never reports rollback. It preserves no old inode,
  hard links, ACLs, ownership, xattrs or watchers, and the replacement
  permissions are explicit.
- RFC3339 is one specified profile: no leap seconds, no timezone database, no
  locale, no floats and no ISO forms outside the documented grammar. Formatting
  a timestamp outside years 0001..9999 or with unnormalized nanos is `Range` or
  `InvalidArgument`.
- Environment snapshots cover startup environment order and are Linux/getenv
  semantics. Duplicates are retained, lookup is first-match and `unset` removes
  all matches. A malformed name/value is `InvalidArgument`; an entry-count or
  total-size cap is `Range`, whether the record was constructed directly or
  captured from an over-cap process environment. `set` validates the
  prospective total before assigning, so a rejected update leaves the old value
  and the snapshot usable. Process mutation is intentionally absent because
  libc `setenv` and the runtime's initial `envp` cannot be kept coherent yet;
  child snapshots do not require it.
- Logging is line-oriented and single-threaded. `openStandard` only selects
  Input/Output/Error and never closes or changes the status flags of process fd
  0/1/2. Because the duplicate shares the open file description, drain/flush
  toggle `O_NONBLOCK` only around their own write/poll attempt and restore the
  exact prior flags before returning. A large process `print` is therefore still
  subject to ordinary blocking backpressure, not logger-induced truncation.
  The temporary toggle is safe only in the v1 single-threaded model; a future
  worker context needs a separately opened nonblocking description. The
  queue is fixed at open; `DropWithCounter` drops whole records, and
  `ReturnWouldBlock` rejects a full queue. `close` without a prior drain/flush
  discards queued records. P1b emits a realtime timestamp but not the design's
  monotonic record-latency field; adding it would need a caller-supplied start
  instant and is deferred. Escaping is `\n`, `\r`, `\t`, `\\` and `\xHH` for
  other control bytes. There is no JSON encoder, global logger, background drain
  thread, cross-process record atomicity for shared files, or payload redaction.
- Mappings remain out of P1b: the optional mmap extension was not needed for
  the practical-filesystem phase.

## P2q UDP batch limits

- `receiveBatch` accepts `1..64` datagrams and a **total** byte budget of
  `1..65507` bytes, the kernel's UDP datagram cap. It allocates one data buffer
  of `maxBytes`, one metadata packet of `maxPackets * 40` bytes and one peer
  packet of `maxPackets * 160` bytes; it never allocates
  `maxPackets * maxBytes`. The real adapter uses a bounded `recvmsg` loop
  against that one buffer rather than `recvmmsg`'s per-message iovecs, so the
  total budget is exact and a 64-packet batch cannot reserve 4 MiB.
  A datagram that does not fit the remaining budget is consumed and the call
  returns `Range`; earlier datagrams in that partial batch are not returned.
  A coalesced GRO buffer is one message for this budget: if it exceeds the
  remaining `maxBytes`, all of its segments are consumed and lost, and the call
  returns `Range`. Batch results always set `truncated=false`; over-budget is
  `Range`, not silent truncation. Size the budget for the largest expected
  datagram or coalesced buffer (QUIC uses 1200-byte payloads) and retry after a
  `Range`.
- `bindDatagram` keeps the legacy P2 profile: `gro=false`,
  `dontFragment=false` and default buffers. Legacy `receive` therefore returns
  exactly one UDP datagram and legacy `send` may fragment. Use
  `bindDatagramOptions`/`bindQuicDatagram` for GRO, GSO, DF and explicit
  buffers.
- `sendBatch` accepts at most 64 segments, `65507` bytes per segment and
  `65507` total bytes. `segmentSize == 0` is one ordinary datagram (at most
  `65507` bytes). Every segment but the last must be exactly `segmentSize`;
  a call is all-or-error. GSO unavailability is `Unsupported`, and
  `sendBatchFallback` is the explicit one-datagram loop: it is capped at
  `65507` total bytes and 64 pieces, clamps each piece to `MAX_DATAGRAM`,
  stops at the first error and does not retry.
- A GRO segment is one UDP datagram, not one QUIC packet. `Packet.bytes` may
  contain several coalesced QUIC packets; the shim/consumer must not infer
  packet boundaries from `segmentSize`. `segmentSize == 0` means one ordinary
  datagram. The final GRO segment may be shorter.
- Socket buffers default to 4 MiB each and are capped at 1 GiB by validation;
  the kernel may clamp the effective size. A full receive buffer drops
  datagrams; there is no unbounded kernel queue or hidden retry.
- `UDP_GRO` and `UDP_SEGMENT` are best-effort. `datagramCapabilities` reports
  what the socket enabled; a failed `sendBatch` clears GSO for that socket.
  `dontFragment` requests `IP_PMTUDISC_DO`/`IPV6_PMTUDISC_DO` and is likewise
  best-effort on kernels that reject the option.
- `bindQuicDatagram` rejects `reusePort`, wildcard local addresses and Unix
  datagrams. `receiveBatch` does not return `IP_PKTINFO`, so one concrete
  local address is reported once through `datagramLocalAddress`; multi-homed
  wildcard listeners are deferred.
- `watchDatagram` is level-triggered through the existing reactor. GRO may
  delay readiness/flush by a short kernel timer; callers use the reactor
  deadline and a bounded WouldBlock retry rather than a busy loop.

## P3/P4 and P2 deferrals

P3 owns process spawn/reaping, pipes/redirection, process groups, signals, cwd
policy, process environment mutation, supervisor examples and the resolver
policy/helper decision. P4 owns threads and synchronization; no worker, atomic,
channel, sender or receiver kind is created here. Mappings and terminal raw mode
remain post-P1b extensions.

P2 deliberately defers mappings (despite the older phase note), multicast
membership, general ancillary data/fd passing beyond the internal UDP GSO/GRO
control messages, `IP_PKTINFO`/per-datagram local addresses, peer credentials,
connected UDP, `sendmmsg`/`recvmmsg`, scatter/gather native syscalls (the obo
`writeVector` composes one bounded contiguous transfer), edge-triggered
readiness, a poll reference backend, reactive DNS
(`beginResolve`/`watchResolve`), a resolver cache/TTL and any hidden helper
process. `resolveBlocking` has **no finite
deadline**; it can block indefinitely in NSS/DNS and must not run inside a
reactor loop. `syswaitio` helpers require a dedicated reactor because they
consume readiness batches.

## Backends and tests

- Real and fake are selected at **link time** by different `.cbind` manifests.
  There is no production environment variable or runtime flag that injects
  faults. `sysfake_*` symbols exist only in the fake link units; the test-only
  control module is not part of the production wrapper surface.
- ASAN/UBSAN run on separate C harnesses, not on the fixed-address hosted obo
  image. This is stated rather than claiming instrumentation that did not run.
- The fake owns fake counters, in-memory inodes/descriptions, a fixed directory
  script and shared C buffers, not file descriptors. Separate opens have
  independent offsets, `dup` shares one description, O_APPEND writes at inode
  end, and path/fstat identity comes from the same inode. A scripted close error
  still consumes the fake resource, modeling Linux close. P1b adds flat
  in-memory names for mkdir/unlink/rename/link/symlink/readLink, a bounded fake
  logger sink and the same replaceAtomic stage classes. It is a deterministic
  contract/fault fixture, not a filesystem implementation: its directory child
  namespace (`sub`), hierarchy and metadata policy are intentionally shallow,
  and the fake environment is a fixed five-entry table.
- P1b fault injection uses the fake operation classes for temporary create,
  write, chmod, file sync, rename and directory sync. `sysfake_store_count()`
  (ctl link only) proves a failed transaction left no temporary. The real
  cross-device case is skipped visibly when no second filesystem is available
  under `/dev/shm`; the openat2 suite skips visibly only if the kernel lacks the
  syscall, and the fake capability-off root covers the `Unsupported` path.
- The fake P2 backend uses in-memory paired connections, queued datagrams,
  scripted readiness/errors and a fake monotonic clock. Its P2q path models
  GSO splitting and GRO coalescing deterministically and honours the same
  batch/segment limits as the real adapter. It has no kernel socket, epoll or
  DNS; its resolver is the fixed table documented in `API.md`, not a name
  service: blocked readiness is represented by explicit scripts/queues,
  and an infinite `wait` with nothing ready returns an empty batch instead of
  hanging the single-threaded test process. Timers are a bounded insertion-order
  list scanned per turn; due timers and fresh/coalesced wakes are prioritized
  over socket readiness, and wake/timer priority alternates where both are
  ready so a hot watch or an always-due timer cannot starve the other. Scripted close/shutdown errors still
  consume the fake object. It proves contract/state/error behavior, not kernel
  TCP/UDP semantics.
- `sysshells` contains P0-only `File`/`Stream` type-identity fixtures, not I/O
  APIs. Compiler-negative fixtures assert that `^File` cannot be passed where
  `^Stream` or `^platform::Resource` is required. The P1a `sysfile::File` and
  `sysstream::Stream` are separate concrete modules.
- Tests assume a local Linux host with a C compiler. Loopback IPv4/IPv6 and Unix
  sockets are used; no external network service or DNS is required. The only
  process-limit change is a short RLIMIT_NOFILE adjustment inside the C harness
  to observe EMFILE, restored immediately. No database or persistent user data
  is touched.

## P3 process, signal and terminal limits

- **POSIX process authority is deliberately absent.** `Child` hides its pid and
  every native token. `terminate` accepts a pid only through the registered
  child and a group only when `newGroup` created it; there is no public pid,
  kill, waitpid or negative-pid surface. A child is observed through
  `pollExit`/`wait` or transferred to the module reaper.
- **Reaping is single-threaded and turn-based.** `pollExit` is `WNOHANG` and
  `wait` is one blocking waitpid. `close` on a Running child transfers the
  obligation, never kills and never blocks in a defer. The reaper runs at
  reactor turns and in `reapChildren`; a detached child can remain a zombie
  until one of those turns. Applications without a reactor must call
  `reapChildren` or `wait` themselves. `pidfd` is preferred when the kernel
  provides it; without it, terminate tracks only owned children and cannot
  promise pid-reuse immunity for an externally reused number.
- **Fake process scheduling is scripted.** The fake backend has no real child,
  pid, pipe fd or kernel signal. Pipe data is supplied by
  `sysfake_proc_write`/`sysfake_proc_read`, exits by `sysfake_proc_exit`, and a
  blocking `wait` on a still-Running scripted child returns `WouldBlock`
  instead of sleeping. This proves state/error/cleanup behavior, not kernel
  scheduling. Real pty tests use only the local test process and a child
  fixture; no network is involved.
- **Signals are queued notifications.** Linux blocks the selected signals and
  uses one signalfd; there is no obo handler and no C-to-obo callback. Signals
  coalesce, `Event.count` is an observation and never an exact delivery count,
  and `OTHER` is status-only. Overlapping subscriptions are rejected with
  `Busy`; one source owns the saved mask at a time. The mask is per-thread, so
  this is a single-threaded contract. SIGKILL/SIGSTOP and synchronous faults
  cannot be represented. Closing a source restores the saved mask; shutdown
  restores it through the same release path.
- **SIGPIPE policy is explicit.** Systems-context initialization ignores SIGPIPE
  if it was not already ignored. This deliberately changes legacy
  `print`/`println` on a closed pipe from termination to silently dropped
  output; `Options.ignorePipeSignal = false` keeps the legacy disposition. New
  pipe writes are not exposed in P3, but spawn restores the child default
  disposition.
- **cwd is process-global.** `cwd` snapshots it; `chdir` is startup-only and
  returns `Busy` once spawn/signal/terminal state has frozen the context. No
  library operation chdirs behind the caller, and there is no thread-local cwd.
  Process environment mutation remains **Unsupported** because libc setenv
  cannot update the runtime's initial-envp view coherently.
- **Terminal ownership is narrow.** Only a CLOEXEC duplicate of 0/1/2 is owned;
  the standard descriptors and their shared description flags are untouched.
  `size`/`enterRaw` return `Unsupported` on a non-tty. Raw mode is an explicit
  obligation; `restore` and idempotent `close` put the saved termios back. A
  module-private `WINDOW_CHANGED` source backs `watchResize`; only one terminal
  owns it at a time and closing that terminal retires resize watches. No
  arbitrary ioctl or UI framework exists.
- **Command variants share one name scope.** `EnvPolicy` uses
  `EnvInherit`/`EnvExplicit` because closed variants cannot repeat
  `Redirect::Inherit` in the same module. The semantics are the design's
  Inherit/Explicit policies.
- **Spawn limits.** Executable/argument text is NUL-free and bounded by
  `ARG_MAX`, the 4 MiB spawn cap and a 128 KiB per-argument cap. `FromStream` is
  duplicated into the child and never consumes the command's source. A failed
  spawn closes both pipe ends and reaps any failed child; the error pipe
  distinguishes exec failure from a genuine exit 127. `posix_spawn` is used when
  the request has no anchored directory; the fork/execve path is trusted C only.
- **No hidden process work.** P3 creates no thread, signal handler returning to
  obo, background reaper or implicit kill. `shutdown` reports outstanding
  children as cleanup failures rather than silently blocking, killing or
  hiding them.

## Compatibility and provenance

- This package makes **no version, ABI, symbol-format or seed reservation**.
  It is source plus trusted C on the existing 0.13/cinterop seam. It does not
  modify `obo/src/obo.obo`, the runtime, `seed/`, symbol format 7 or ABI 1.
- The `.cbind` format is the existing experimental `cinterop-proto/1`. Hashes in
  the manifest are provenance/corruption checks, not signatures.
- Experimental APIs may change with a migration note. Nothing here is stable;
  P1b adds the practical filesystem/calendar/environment/log surface, and P2
  adds addresses, sockets, the reactor, waitio and blocking resolution on the
  reviewed P0/P1a seam, and P3 adds processes, signals and terminal ownership.
  Threads and synchronization remain a separate design (P4/P5); process
  environment mutation and mapping remain deferred.
