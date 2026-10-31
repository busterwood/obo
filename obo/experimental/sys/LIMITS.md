# Experimental sys P0 limits

These limits are part of the P0 boundary proof. They are deliberately explicit
rather than hidden behind a stable-looking API.

## Scope

- **P0 only.** There is no `File`, `Directory`, metadata, mapping, clock,
  calendar, randomness, stream, reactor, socket, DNS, process, signal, terminal
  or logging API here. P1a starts the first useful slice.
- **One process context, single thread.** The managed table and C registry are
  process globals. There is no worker context selection, lock, atomic or TSAN
  claim.
- **Linux x86-64 real backend.** The real fixture uses `eventfd`; other
  platforms would need a different adapter and are not claimed.
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
- Public record fields are writable in 0.12. An exact copied live hint aliases
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
- `ignorePipeSignal` is validated and stored for P1a. P0 installs no signal
  disposition and exposes no pipe write, so it changes no P0 behavior.

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

## Backends and tests

- Real and fake are selected at **link time** by different `.cbind` manifests.
  There is no production environment variable or runtime flag that injects
  faults. `sysfake_*` symbols exist only in the fake link units; the test-only
  control module is not part of the production wrapper surface.
- ASAN/UBSAN run on separate C harnesses, not on the fixed-address hosted obo
  image. This is stated rather than claiming instrumentation that did not run.
- The fake owns fake counters and shared C buffers, not file descriptors. A
  scripted close error still consumes the fake resource, modeling Linux close.
- `sysshells` contains P0-only `File`/`Stream` type-identity fixtures, not I/O
  APIs. Compiler-negative fixtures assert that `^File` cannot be passed where
  `^Stream` or `^platform::Resource` is required.
- Tests assume a local Linux host with a C compiler. No network service, DNS,
  database, host limit change or persistent data is used.

## Compatibility and provenance

- This package makes **no version, ABI, symbol-format or seed reservation**.
  It is source plus trusted C on the existing 0.12/cinterop seam. It does not
  modify `obo/src/obo.obo`, the runtime, `seed/`, symbol format 6 or ABI 1.
- The `.cbind` format is the existing experimental `cinterop-proto/1`. Hashes in
  the manifest are provenance/corruption checks, not signatures.
- Experimental APIs may change with a migration note. Nothing here is stable;
  P1a is where owned File/Directory/clock/getrandom and bounded stream buffers
  are added and reviewed.
