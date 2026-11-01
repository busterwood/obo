# Experimental systems library P0/P1a/P1b/P2q (`obo/experimental/sys`)

Status: **experimental internal P1b/P2q slice. Not a stable API and not
authorized for merge/promotion by itself.** P0 proved the resource/error/byte
boundary; P1a adds owned `File`, anchored `Directory`, metadata snapshots,
clocks, `getrandom`, concrete streams, bounded buffers and a single-path copy
example. P1b adds anchored directory mutation, `openBeneath`, chmod/sync,
rename/link/symlink, the `replaceAtomic` transaction, RFC3339 text, environment
snapshots, bounded logging and a configuration-update example. P2 adds numeric
addresses, TCP/UDP/Unix sockets, an epoll readiness reactor with timers/wake/stop,
cancellation/deadline helpers and blocking numeric/name resolution on the same
seam. P2q adds the bounded UDP batch extension HTTP/3 needs: `receiveBatch`,
`sendBatch` with Linux `UDP_SEGMENT` GSO, `UDP_GRO` segment reporting, an
explicit one-datagram fallback, `watchDatagram`, socket option profiles and the
QUIC concrete-bind policy. Processes, signals, mappings, terminal raw mode and
all thread/synchronization kinds are later phases.

P0 proved the boundary that P1a builds on:

1. a managed resource identity shell (`platform::Resource`) whose public field
   is only a checked slot/nonce hint;
2. a library error namespace distinct from positive errno and runtime traps;
3. a bounded, NUL-safe, C-owned byte bridge with validated tokens/offsets;
4. a link-time scripted fake backend implementing the same scalar adapter
   symbols as the real Linux backend;
5. explicit `initialize`/`counts`/`shutdown`, a leak census, and no OS action
   at import time.

It uses the existing experimental cinterop seam (scalar `i64` arguments and
`i32`/`i64` results only). No compiler, runtime, ABI, symbol-format, version or
seed change is made or reserved. The package's `.cbind` manifests are explicit
build inputs; a plain `obo` compile is not a native systems link.

## Layout

```text
obo/experimental/sys/
  syserrors.obx                 library error categories and errno mapping
  sysplatform.obx               Resource shell, identity table, init/counts/shutdown
  systestres.obx                P0 native eventfd/counter fixture (not a File)
  sysbytes.obx                  16 MiB bounded C-owned byte bridge
  sysshells.obx                 P0-only File/Stream type-identity fixtures
  sysstat.obx                   P1a metadata snapshot and identity equality
  sysclock.obx                  P1a Instant/Duration/Deadline/Timestamp
  sysrandom.obx                 P1a getrandom fillSome/bytes
  sysstream.obx                 P1a concrete Stream and standard streams; additive
                                handle forms for record-owned fields
  sysdir.obx                    P1a anchored Directory and bounded Iterator
  sysfile.obx                   P1a owned File and explicit OpenOptions
  sysbuffer.obx                 P1a fixed-capacity Buffer/Reader/Writer/Cursor
  syscalendar.obx               P1b RFC3339 parse/format profile
  sysenv.obx                    P1b owned environment snapshot (no mutation)
  syslog.obx                    P1b bounded line Logger over a standard duplicate
  sysaddress.obx                P2 numeric IPv4/IPv6/Unix addresses
  sysnet.obx                    P2/P2q Listener/Connection/Datagram, UDP batch
  sysreactor.obx                P2 epoll, watches, timers, wake/stop
  syswaitio.obx                 P2 cancellation + absolute-deadline helpers
  sysresolve.obx                P2 blocking numeric/name resolution
  native/
    sysadapter.h                scalar-only exported adapter contract
    sysregistry.{h,c}           separate trusted C registry (does not use cres.*)
    sysadapter_real.c           Linux files/dirs/stat/clock/getrandom + P0 fixtures
    sysnet_real.c               Linux sockets/epoll/timers/eventfd/getaddrinfo
    sysnet.h                    shared socket object and refcounted half-close state
    sysadapter_fake.c           scripted fake resources + file/dir/clock fixtures
    sysnet_fake.c               in-memory fake sockets/reactor/timers/resolver
    sysnet_fake.h               fake P2 helper surface
    sysfake_internal.h          shared fake handle shape
    sysadapter_real.cbind       real link manifest
    sysadapter_fake.cbind       fake link manifest
    sysadapter_fake_ctl.cbind   fake + test-only control symbols
  tests/
    sysfakectl.obx              test-only fake controls (fake_ctl build only)
    apps/*.obx                  obo roots for real, fake, buffer and fault runs
    c/{native_test,fake_test,bench_copy,p1a_test,p1b_test,p2_test,p2q_test}.c
    apps/p1b_*.obx              P1b filesystem/atomic/calendar/env/log roots
    test_sys_p0.py              one-command P0 suite
    test_sys_p1a.py             one-command P1a suite
    test_sys_p1b.py             one-command P1b suite
    test_sys_p2.py              one-command P2 suite
    test_sys_p2q.py             one-command P2q UDP batch suite
```

`sysregistry.*` is intentionally separate from
`experimental/cinterop/toolkit/cres.*`, so this work does not collide with the
database-adapter registry work in another worktree.

## Resource identity in one page

```obo
pub type Resource = ref record { pub reserved: int }
```

`reserved` is public in P0 because the tests and typed shell fixtures construct
and read the shell directly. This is the documented P0 public-field limitation;
the module-private slot table and native registry token remain private.

`reserved` is a **checked hint**: a bounded slot index plus an unpredictable
40-bit per-slot nonce. It is not a C token, not a capability and not authority.
The nonce is generated by the C adapter from `getrandom`/`/dev/urandom`, is
never 0 or 1, and is never exposed as authority.

Handles are comparable, but the identity contract deliberately keeps the
approved §11.1 copied-live-hint alias: a copied live hint names the same entry
while its registered owner shell is alive. The table is therefore selected by
the checked slot+nonce, not by owner-handle equality; the registered shell's
handle is stored for liveness/tombstone sweeping. The module-private table
stores, per slot:

- the random nonce;
- library kind (`KIND_*`);
- state (free, open, closed tombstone);
- the private C registry token;
- a non-owning `^Resource` handle used only to notice that a tombstone's shell
  has died and can be swept.

Every entry point:

1. decodes the hint and checks the slot is in range and the random nonce matches;
2. checks the stored shell handle is alive;
3. checks state and kind;
4. copies the token/kind into scalar locals;
5. asks the C registry to validate the private token for that kind;
6. only then performs a native call.

A user-created `Resource{reserved: 0}`, an out-of-range hint, a wrong nonce, a
future hint forged before its slot was populated, a stale hint after slot reuse
and a cross-kind shell all return `InvalidResource`. A closed shell returns
`Closed` from typed operations; a second `close` is `ok(false)`, including after
shutdown/reinitialization for a shell from a retired context. The C token is
kept only in the module-private slot table; it never appears in `Resource` or a
public record.

`source`/`requireKind` are the supported bridge from a shell's `res` field to a
checked `^Resource` identity. Typed modules use the handle forms
`_sourceHandle`/`_requireKindHandle` because the conservative native effect
summary does not allow an interior reference (`&shell.res` through another
reference) to cross a function containing a native call. The public
`source(&r)`/`requireKind(&r, kind)` forms remain for whole-local resources.

**Honest public-field limitation:** documented value/shell fields are explicitly
`pub` under 0.13 private-by-default records. Copying a
live hint into a new shell aliases the same entry while its registered owner is
alive (it cannot create a token or a second native object); once the owner shell
dies, the same copied hint fails the liveness check. Replacing a shell's `res`
with another live resource is therefore an alias/steal, exactly as the approved
design says. Typed operations revalidate kind on every entry. The identity layer
protects against accidental forged/stale/wrong-kind use, not against a
deliberately aliasing caller or trusted C.

Close and release are one explicit operation: `_close` retires the token and
calls the private C release exactly once; shutdown performs the same for
orphaned open entries after snapshotting the leak census. There is no separate
automatic release path.

Close is explicit and normally written with `defer`:

```obo
let made = sysbytes::create(4096)
match made {
    ok(b) {
        defer sysbytes::close(&b)
        // ...
    }
    err(m, c) { ... }
}
```

Dropping a record does **not** close the native resource; there is no finalizer.
An open entry whose shell died remains a leak record and is reported by
`counts`/`shutdown`; only explicit shutdown releases it. Closed tombstones live
only while their shell is alive and are swept on later insertion.

## P1a surface (experimental)

P1a keeps every public shell concrete and every native resource behind the
checked `platform::Resource` identity. No raw fd, registry token, pointer or C
struct crosses the application boundary. Each creator returns one owned shell
with one explicit close obligation; record drop still runs no finalizer.

Under 0.13 private-by-default fields, the documented P1a record fields are
explicitly `pub`: callers build `OpenOptions`, read metadata/entry/timestamp
values and exercise the public-field limitation. This is not a privacy
relaxation for the registry: the slot table, nonces, native tokens, free lists
and per-kind state remain module-private, and every typed entry revalidates.

- `sysfile::File` with explicit `OpenOptions` (`read`, `write`, `create`,
  `exclusive`, `truncate`, `append`, `noFollow`, `nonblocking`, `permissions`).
  A positional write on an append-open File is rejected even when it is
  zero-length; `writeSome` with a zero count returns 0 after validation.
  `open`/`openAt` always add CLOEXEC atomically; option validation happens before
  a native call. `readSome`/`writeSome` make one attempt (short success, EOF nil,
  WouldBlock separate), `readInto`/`writeFrom` use existing arrays, and `seek`,
  `truncateTo`, `readAt`, `writeAt`, `metadata`, `duplicateStream`,
  `intoStream` and `close` cover the P1a lifecycle. `duplicateStream` is `dup`
  semantics; `intoStream` re-tags the live table entry and logically consumes
  the File.
- `sysdir::Directory` and `Iterator`: `open(path)` accepts an ordinary host
  path and follows symlinks; `child`, `metadata` and anchored names are single
  components and `child` opens relative to the base descriptor with NOFOLLOW
  (never the process cwd). Iteration owns names, skips `.`/`..`, reports
  Unknown kind and is bounded. The iterator duplicates its anchor, so closing
  the Directory does not invalidate it; `dup(2)` also means the anchor and all
  its iterators share one directory offset. There is no path-containment claim
  for hostile multi-component paths; `openBeneath` is deferred.
- `sysstat::Metadata` is a detached snapshot: kind, permissions, size, links,
  uid/gid, seconds+nanos times, optional birth time and an equality-only interned
  identity. `sameIdentity` is pure bit equality, not authority.
- `sysclock`: monotonic `Instant`, nonnegative `Duration`, absolute `Deadline`
  with a distinct infinity flag, realtime `Timestamp` seconds+nanos. Checked
  arithmetic; `sleepUntil` is interruptible with no hidden restart.
- `sysrandom`: `fillSome` and bounded `bytes` use `getrandom`; blocking/
  nonblocking and EINTR/EAGAIN are distinct. No userspace PRNG or insecure
  fallback.
- `sysstream::Stream` is a concrete tagged endpoint over platform resources.
  Standard streams are owned CLOEXEC duplicates. P1a covers read/write/close;
  P2 adds `shutdownRead`/`shutdownWrite`, bounded `readVector`/`writeVector` and
  socket-backed streams produced by `sysnet::intoStream`/`duplicateStream`.
  A file/pipe stream returns `Unsupported` for half-close.
- `sysbuffer`: fixed allocation (`make(limit)`, default 64 KiB, 16 MiB cap),
  `feed`/`find`/`copy`/`consume`, `readStep`/`writeStep`, and one-attempt
  `readExactStep`/`writeExactStep`. A single `writeStep` moves at most 1 MiB,
  so a larger pending range advances across several calls. No append growth;
  progress is retained.

## P1b surface (experimental)

P1b completes the practical-filesystem slice on the same resource identity and
single-attempt error rules. Anchored names remain one component (nonempty, no
`/`, NUL, `.` or `..`) and make no path-containment claim.

- `sysdir` adds `mkdir`, `unlink`, `removeEmpty`, `readLink` and `sync`.
  `readLink` returns raw target bytes: a dangling target succeeds, a non-symlink
  is `InvalidArgument`, and a 4095-byte target is `NameTooLong` (never silent
  truncation). `removeEmpty` is `rmdir`; a nonempty directory maps the kernel
  `ENOTEMPTY` to `Busy` because the error namespace has no `NotEmpty` kind.
- `sysfile` adds `openBeneath`, `chmod`, `sync(dataOnly)`, anchored `rename`,
  `link`, `symlink` and `replaceAtomic`. `openBeneath` uses
  `openat2(RESOLVE_BENEATH|RESOLVE_NO_MAGICLINKS)`; a kernel without openat2
  returns `Unsupported`, never a string-prefix check. `rename` is atomic for
  name visibility, `replace=false` uses `RENAME_NOREPLACE` (or `Unsupported`),
  and cross-device is `CrossDevice`, never copy/delete. `link` does not follow
  the source symlink. `symlink` stores relative/absolute target bytes verbatim.
- `replaceAtomic` is an O_EXCL temporary in the target directory, explicit
  short-write progress, explicit chmod, optional file fsync, atomic rename
  commit, then optional directory fsync. Precommit failures return `err` and
  remove the temporary; a directory-fsync failure after commit returns
  `ok(ReplaceReport{committed:true, durable:false, code:DurabilityUnknown})`.
  A committed rename is never rolled back.
- `syscalendar::parseRfc3339`/`formatRfc3339` implement the §6.1 profile: years
  0001..9999, valid Gregorian dates, `T`/`t`, `Z`/`z`, 1..9 fractional digits,
  signed `HH:MM` offsets normalized to UTC, `-00:00` accepted, leap seconds and
  trailing text rejected. Output is UTC `Z`, with a zero fraction omitted and
  trailing zeros trimmed. Checked integer arithmetic; no floats or TZ database.
- `sysenv::Environment` owns a startup-order snapshot with first-match lookup,
  explicit missing (nil) versus present-empty (empty array), and a deep-copy
  `entries` result. Every operation revalidates names, values and total size.
  Process mutation (`setProcess`/`unsetProcess`) is intentionally absent and
  remains `Unsupported`: there is no second environment.
- `syslog::Logger` owns a CLOEXEC standard-stream duplicate and a fixed-capacity
  C queue. `emit` builds one bounded line (realtime RFC3339 timestamp, level,
  escaped `\n`/`\r`/`\t`/control/backslash bytes, newline) and copies it
  atomically. `ReturnWouldBlock` rejects a full-queue record; `DropWithCounter`
  counts whole drops. `drain`/`flush` report sink errors and retain progress on
  short writes. There is no global logger, background thread or JSON encoder.

The `p1b_config_example.obx` root reads a config file, replaces it atomically,
syncs and verifies it, then demonstrates RFC3339, an environment lookup and a
bounded log line. It writes only inside its argument directory.

Deferred beyond P2: mappings, processes, signals, environment mutation,
terminal raw mode and all thread/synchronization kinds. `sysshells` remains a P0
compiler-negative fixture, not the P1a/P1b/P2 API.

## P2 surface (experimental)

All P2 shells are concrete records containing only `pub res: platform::Resource`
(and documented value records). Public fields are untrusted and revalidated at
every entry. No raw descriptor, socket-option integer, pointer or registry token
crosses the application boundary.

- `sysaddress::Address` is a closed IPv4/IPv6/UnixPath/UnixAbstract value.
  `parseNumeric` does no DNS and rejects octal-looking IPv4, brackets and
  invalid lengths; `format` is deterministic lowercase and never fabricates an
  unnamed address. Host/network byte order is private to C.
- `sysnet::Listener`/`Connection`/`Datagram`: `listen` validates
  `ListenOptions` (backlog 1..65535, kernel-capped to `somaxconn`; independent
  reuseAddress/reusePort, ipv6Only default **true**), `accept` returns WouldBlock on an empty queue,
  and every socket is nonblocking+CLOEXEC at creation. `connect` returns an
  owned Connection even while pending; `finishConnect` returns false until a
  nonblocking completion check succeeds and then the captured `SO_ERROR`
  failure. `readSome`/`writeSome`/`readInto`/`writeFrom` are one-attempt forms.
  `shutdownWrite` is idempotent, EOF is nil, WouldBlock is distinct, and
  `intoStream`/`duplicateStream` implement consuming transfer and `dup`
  semantics. IPv6 datagram binds default to v6-only. Datagram receive preserves boundaries,
  peer and truncation;
  empty datagrams are data. Port 0 is discoverable through the listener/
  datagram local address.
- `sysnet` P2q UDP batch: `bindDatagramOptions` validates the explicit
  receive/send buffer, reusePort, GRO, GSO and DF profile; `bindDatagram` keeps
  its old signature and the legacy P2 profile (`gro=false`,
  `dontFragment=false`, default buffers) so existing one-datagram `receive`
  and fragmenting `send` behavior is unchanged. `receiveBatch` returns a
  bounded `[]Packet` with a **total** `maxBytes` budget, at most 64 datagrams
  and a 65507-byte cap;
  `Packet.segmentSize` is a GRO segment size (0 for an ordinary datagram) and a
  GRO segment is one UDP datagram, not one QUIC packet. `sendBatch` with a
  non-zero segment size asks for one `UDP_SEGMENT` send and returns
  `Unsupported` rather than looping; `sendBatchFallback` is the explicit
  one-datagram fallback, capped at 65507 total bytes and 64 pieces.
  `watchDatagram` is the only readiness helper and exposes no descriptor. `datagramCapabilities` reports the features the
  socket actually enabled. `bindQuicDatagram` rejects SO_REUSEPORT, a wildcard
  local address and Unix datagrams for the one-socket QUIC profile.
- `sysreactor`: level-triggered epoll only in v1. `watchResource` accepts a
  checked source, `modify`/`remove` update the subscription, timers use the
  fake/real monotonic clock, and `wait` returns at most 64 `Event` records per
  batch with application tags only. Timers are checked every turn; ready
  subscriptions rotate. `wake` is an eventfd/coalesced wake and `stop` makes
  `wait` return `Cancelled`. Endpoint/reactor close invalidates subscriptions;
  stale native events are filtered. There are no registered callbacks.
- `syswaitio::Cancellation` is a sticky native flag plus eventfd wake.
  `readExactUntil`/`writeAllUntil`/`flushUntil` use one caller-owned absolute
  deadline, retain progress and never reset the deadline on EINTR, WouldBlock
  or partial progress; retryable no-progress failures wait and retry. They
  require a socket-backed Stream. They require a **dedicated reactor**; event-driven
  callers use the buffer step APIs plus `cancelled`/`deadlineReached`.
- `sysresolve::resolveBlocking` copies getaddrinfo results and always calls
  `freeaddrinfo`; `resolveNumeric` is an explicit no-NSS path. Blocking NSS is
  documented as unbounded: it is a startup/client convenience, not a reactor
  operation. EAI categories and EAI_SYSTEM errno stay distinct.
- `sysnet` also exposes checked datagram receive/send buffer sizes and
  broadcast enablement. Multicast membership, ancillary data beyond the
  internal UDP GSO/GRO control messages, `IP_PKTINFO`/per-datagram local
  addresses, connected UDP, `sendmmsg`/`recvmmsg`, edge-triggered readiness and
  a poll reference backend remain deferred.

## Error namespace

`syserrors` assigns library codes starting at 10001, separate from positive
legacy Linux errnos and from runtime trap codes 2..9. Callers use
`kind(code)`, `code(kind)`, `isInterrupted`, `isWouldBlock`, `isRetryable`; they
never branch on a raw number. `fromErrno` is the adapter-facing translator for
one captured Linux errno. Unknown native failures become `SystemFailure`.

`EINTR` (Interrupted) and `EAGAIN` (WouldBlock) remain distinct. A one-shot
public operation performs one read-only validation call and then one data-path
adapter attempt; the fake fault test checks the data-path call counter after
each operation. `isRetryable` is advisory and never causes a hidden retry.
Close never retries after EINTR; the token is retired regardless. Adapter
mapping follows the design table: `EDQUOT` is `NoSpace`, and generation
`EOVERFLOW` is `ResourceLimit`, not `Range`.

## Bounded byte bridge

`sysbytes::Bytes` is a `Resource` shell of kind `KIND_BYTE_BUFFER`. The bytes
live in a C-owned buffer. No pointer or struct crosses; the registry token is
kept only in the module-private slot table and never appears in `Bytes` or
another public record.

- default / maximum per-buffer size: **16 MiB** (`MAX_BYTE_BUFFER`);
- every source/destination offset and count is checked before indexing;
- `setByte`/`getByte` are the scalar reference path;
- `writePacked`/`readPacked` move at most seven bytes per scalar (positive
  56-bit packed value); a packed value that does not fit the requested byte
  count is rejected with `Range`;
- `copyIn`/`copyOut` validate the whole range first, then use 7-byte groups;
- `copyInScalar`/`copyOutScalar` are the one-byte benchmark path;
- all copies are length-delimited and preserve embedded NUL bytes;
- oversized, negative and out-of-range requests fail with `Range` or
  `InvalidArgument` before a native call.

The bridge is a correctness seam, not a promise of network throughput. P2 may
request a separately reviewed bulk-copy design if measurements demand it.

## Backends and tests

The real and fake adapters export the same scalar symbols. Selection is at
**link time** through a different `.cbind`; there is no production environment
toggle and no stored obo callback. The fake owns fake registry entries, not real
file descriptors. `sysfake_*` controls exist only in the `fake_ctl` link unit
and are imported only by the fault test root.

Run:

```sh
make -C obo obo
python3 obo/experimental/sys/tests/test_sys_p0.py
python3 obo/experimental/sys/tests/test_sys_p1a.py
python3 obo/experimental/sys/tests/test_sys_p1b.py
# or
make -C obo sys-p0
make -C obo sys-p1a
make -C obo sys-p1b
make -C obo sys-p2
```

The suite builds and runs:

- C registry tests against the real and fake adapters, including nonce
  generation, forged/stale tokens, double close, wrong kind, table exhaustion,
  byte bounds, packed-width boundaries, exact-16-MiB edges, quotas and
  init/shutdown ordering;
- the same C harnesses under ASAN/UBSAN with leak detection;
- obo roots for identity/adversarial behavior (including future-forged hints
  before population and copied-hint owner liveness), capacity, init ordering,
  retired-shell close and the leak oracle;
- compiler-negative fixtures proving `^File` cannot be passed where `^Stream`
  or `^platform::Resource` is required, with the exact diagnostic asserted;
- a real-vs-fake conformance trace that must match line for line;
- scripted fake creation failure, EINTR, EAGAIN, progress and close-EINTR
  consumption, with per-class call counts proving no hidden retry;
- a packed-vs-scalar bridge benchmark (C exact path and obo wrapper path).

The P1a suite additionally runs:

- a C harness on both adapters for open/write/read/seek/truncate/fstat/identity,
  dup/re-tag, getdents64 iteration, clocks, sleep and getrandom, under
  ASAN/UBSAN with leak detection;
- real-vs-fake roots for file/stream lifecycle, open-option validation,
  EOF-vs-WouldBlock status, anchored metadata/identity, bounded buffers and a
  5-byte-at-a-time file copy that must match line for line;
- scripted EINTR/EAGAIN/short-read/short-write/ENOENT/EMFILE/ENOMEM faults with
  per-class call counts, close-EINTR consumption, absolute-deadline retry and
  errno-category checks.

The P1b suite additionally runs:

- a C harness on both adapters for mkdir/unlink/removeEmpty/readLink, links,
  rename replace/no-replace, chmod/fdatasync/fsync, openat2 containment, the
  logger queue and environment snapshots, under ASAN/UBSAN with leak detection;
- identical real/fake roots for the full filesystem contract, validation,
  closed-handle use and census; fake_ctl roots inject `replaceAtomic` failure at
  temporary create, write, chmod, file sync, rename and directory sync, proving
  precommit cleanup (no temporary, target untouched) versus the postcommit
  `DurabilityUnknown` report;
- an openat2 fixture covering `..`, absolute paths, inside/outside symlinks and
  `/proc` magic links, plus a fake capability-off root that must return
  `Unsupported`;
- RFC3339 table/round-trip cases and trimming, fake/real environment behavior,
  bounded logger queue/drop/escaping/short-drain/sink-error/leak cases, a real
  cross-device rename when `/dev/shm` is on another filesystem, and the
  configuration-update example.

The P2 suite additionally runs:

- a C harness on both adapters for IPv4/IPv6/Unix TCP, Unix datagrams, port-0
  discovery, reactor timers/wake/stop, resolver status encoding and real
  RLIMIT_NOFILE accept pressure, under ASAN/UBSAN/LSan;
- identical real/fake roots for address parsing/formatting/validation
  (including abstract-name hex separation), TCP/UDP/Unix/abstract loopback,
  readInto/writeFrom, duplicateStream, socket options, short/empty/truncated
  datagrams, reactor EOF/timers/fairness/wake/stop/stale filtering, resolver
  numeric/blocking errors and waitio cancellation/deadline/no-reset behavior;
- C checks for IPv6-only datagram binds, an unnamed Unix datagram peer and
  coalesced wake/timer priority;
- scripted fake EMFILE/EAGAIN/short I/O/pending-or-failed connect/EAI faults,
  accepted-connection pressure, watch/timer quota pressure and retained
  deadline/progress assertions.

The P2q suite additionally runs:

- a C batch harness on both adapters (normal and ASAN/UBSAN/LSan) covering
  `UDP_SEGMENT` send, GRO/ordinary receive acceptance, segment accounting,
  the total `maxBytes` budget and Range, single-datagram truncation metadata,
  peer addresses, capability bits and SO_REUSEPORT;
- identical real/fake roots for batch send/receive, explicit GSO fallback,
  GRO-off segment splitting, `watchDatagram`, socket option profiles,
  reusePort, the QUIC concrete-bind policy and argument validation;
- scripted fake batch listen/write/read faults and the explicit one-datagram
  fallback path.

Sample result on the P0 development host (Linux x86-64, gcc `-O2`; only a
record of this run, not a portable promise):

```text
C bridge 1 MiB, median of 3 after warmup:
    scalar_in 213.3 MiB/s, packed_in 757.8 MiB/s,
    scalar_out 195.5 MiB/s, packed_out 228.7 MiB/s
obo whole process 2 MiB moved: packed 49.0 MiB/s, scalar 9.5 MiB/s
```

## Provenance

This package is additive at P2 base commit
`8cd46f1270454868ef68a2748d4eac28afba2461` (obo 0.13, symbol format 7, ABI 1).
P0/P1a were separately reviewed; P2 builds on their package without modifying
the compiler, runtime, ABI or seed. It reuses the existing `experimental/cinterop` build/verify tool and the
existing tracked compiler/runtime **without modifying them**. It does not touch
`experimental/cinterop/toolkit/cres.*`; the P0 registry is separate. Each
`.cbind` build records hashes for the spec, wrapper sources, adapter sources,
header, compiler and link inputs in `manifest.json`; those hashes are
provenance/corruption checks, not signatures. No version, ABI, symbol-format or
seed value is reserved or changed.

## What P2 is not

See [`LIMITS.md`](LIMITS.md) for the complete list. In short: one process
context, single-threaded, no finalizers, no raw fd/token exposure, no process/
signal/mapping API, no production stability, and no version or ABI reservation.
Anchored names still make no containment claim against concurrent rename/mount
changes; `openBeneath` covers hostile multi-component paths only where openat2 is
available. P3 owns process supervision, signals, cwd/resolver policy and
environment mutation; P4 owns threads/synchronization. Reactive DNS
(`beginResolve`/`watchResolve`), multicast, general ancillary data beyond UDP
GSO/GRO, per-datagram `IP_PKTINFO`, edge-triggered readiness and a poll
reference backend remain deferred.
