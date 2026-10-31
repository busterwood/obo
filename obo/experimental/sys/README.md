# Experimental systems library P0 (`obo/experimental/sys`)

Status: **experimental internal P0 boundary proof. Not a stable API, not P1a,
and not authorized for merge/promotion by itself.** The package deliberately
does **not** add `File`, `Directory`, a clock, networking, processes, threads or
synchronization. Those are later phases. P1a is the first useful public slice.

This package proves the boundary that P1a will build on:

1. a managed resource identity shell (`platform::Resource`) whose public field
   is only a checked slot/epoch hint;
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
  native/
    sysadapter.h                scalar-only exported adapter contract
    sysregistry.{h,c}           separate trusted C registry (does not use cres.*)
    sysadapter_real.c           Linux eventfd + C buffers
    sysadapter_fake.c           scripted fake counters + shared C buffers
    sysadapter_real.cbind       real link manifest
    sysadapter_fake.cbind       fake link manifest
    sysadapter_fake_ctl.cbind   fake + test-only control symbols
  tests/
    sysfakectl.obx              test-only fake controls (fake_ctl build only)
    apps/*.obx                  obo roots for real, fake and adversarial runs
    c/{native_test,fake_test,bench_copy}.c
    test_sys_p0.py              one-command P0 suite
```

`sysregistry.*` is intentionally separate from
`experimental/cinterop/toolkit/cres.*`, so this work does not collide with the
database-adapter registry work in another worktree.

## Resource identity in one page

```obo
pub type Resource = ref record { reserved: int }
```

`reserved` is a **checked hint**: a bounded slot index plus an epoch. It is not
a C token, not a capability and not authority.

A literal hash keyed by `handle(res)` is not expressible in 0.12: handles are
checked non-owning links but have no equality or integer conversion, and passing
one to C is forbidden. The design's identity table is therefore realized as a
fixed-capacity slot table selected by the checked hint; the registered shell's
handle is stored only for liveness/tombstone sweeping. Full identity still
requires the epoch, kind, state, shell liveness and private C token checks on
every operation, so a stale or forged hint cannot select a different live
entry by itself. The module-private table stores, per slot:

- epoch/generation;
- library kind (`KIND_*`);
- state (free, open, closed tombstone);
- the private C registry token;
- a non-owning `^Resource` handle used only to notice that a tombstone's shell
  has died and can be swept.

Every entry point:

1. decodes the hint and checks the slot is in range and the epoch matches;
2. checks the stored shell handle is alive;
3. checks state and kind;
4. copies the token/kind into scalar locals;
5. asks the C registry to validate the private token for that kind;
6. only then performs a native call.

A user-created `Resource{reserved: 0}`, an out-of-range hint, a wrong epoch, a
stale hint after slot reuse and a cross-kind shell all return
`InvalidResource`. A closed shell returns `Closed` from typed operations; a
second `close` is `ok(false)`. The C token is never stored in the shell and
never crosses into obo.

`source`/`requireKind` are the supported bridge from a shell's `res` field to a
checked `^Resource` identity. Typed modules use the handle forms
`_sourceHandle`/`_requireKindHandle` because a 0.12 conservative native effect
summary does not allow an interior reference (`&shell.res` through another
reference) to cross a function containing a native call. The public
`source(&r)`/`requireKind(&r, kind)` forms remain for whole-local resources.

**Honest public-field limitation:** record fields are public in 0.12. Copying a
live hint into a new shell aliases the same entry (it cannot create a token or
a second native object). Replacing a shell's `res` with another live resource
is therefore an alias/steal, exactly as the approved design says. Typed
operations revalidate kind on every entry. The identity layer protects against
accidental forged/stale/wrong-kind use, not against a deliberately aliasing
caller or trusted C.

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

## Error namespace

`syserrors` assigns library codes starting at 10001, separate from positive
legacy Linux errnos and from runtime trap codes 2..9. Callers use
`kind(code)`, `code(kind)`, `isInterrupted`, `isWouldBlock`, `isRetryable`; they
never branch on a raw number. `fromErrno` is the adapter-facing translator for
one captured Linux errno. Unknown native failures become `SystemFailure`.

`EINTR` (Interrupted) and `EAGAIN` (WouldBlock) remain distinct. One-shot public
operations make exactly one adapter attempt; the fake fault test checks the
call counter after each operation. `isRetryable` is advisory and never causes a
hidden retry. Close never retries after EINTR; the token is retired regardless.

## Bounded byte bridge

`sysbytes::Bytes` is a `Resource` shell of kind `KIND_BYTE_BUFFER`. The bytes
live in a C-owned buffer. No pointer, struct or registry token crosses.

- default / maximum per-buffer size: **16 MiB** (`MAX_BYTE_BUFFER`);
- every source/destination offset and count is checked before indexing;
- `setByte`/`getByte` are the scalar reference path;
- `writePacked`/`readPacked` move at most seven bytes per scalar (positive
  56-bit packed value);
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
```

The suite builds and runs:

- C registry tests against the real and fake adapters, including forged/stale
  tokens, double close, wrong kind, table exhaustion, byte bounds, quotas and
  init/shutdown ordering;
- the same C harnesses under ASAN/UBSAN with leak detection;
- obo roots for identity/adversarial behavior, capacity, init ordering and the
  leak oracle;
- a real-vs-fake conformance trace that must match line for line;
- scripted fake creation failure, EINTR, EAGAIN, progress and close-EINTR
  consumption, with per-class call counts proving no hidden retry;
- a packed-vs-scalar bridge benchmark (C exact path and obo wrapper path).

Sample result on the P0 development host (Linux x86-64, gcc `-O2`; only a
record of this run, not a portable promise):

```text
C bridge 1 MiB: scalar_in 197.3 MiB/s, packed_in 769.2 MiB/s,
                scalar_out 221.0 MiB/s, packed_out 308.5 MiB/s
obo whole process 2 MiB moved: packed 56.3 MiB/s, scalar 8.8 MiB/s
```

## Provenance

This package is additive at base commit
`9441f2d0c4a92aa817ef8e29be0582ab1c820e48` (obo 0.12, symbol format 6, ABI 1).
It reuses the existing `experimental/cinterop` build/verify tool and the
existing tracked compiler/runtime **without modifying them**. It does not touch
`experimental/cinterop/toolkit/cres.*`; the P0 registry is separate. Each
`.cbind` build records hashes for the spec, wrapper sources, adapter sources,
header, compiler and link inputs in `manifest.json`; those hashes are
provenance/corruption checks, not signatures. No version, ABI, symbol-format or
seed value is reserved or changed.

## What P0 is not

See [`LIMITS.md`](LIMITS.md) for the complete list. In short: one process
context, single-threaded, no finalizers, no raw fd/token exposure, no File/
Directory/clock/network/process API, no production stability, and no version or
ABI reservation. P1a adds the first owned File/Directory/clock/getrandom slice
and bounded stream buffers on this seam.
