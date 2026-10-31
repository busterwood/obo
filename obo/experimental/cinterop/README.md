# Experimental C interop (`cinterop`)

Status: **REVISE response pending independent rereview.** The prototype was
repaired after the architecture/security review of `a29f35d`; this is still an
experimental, trusted-build probe, not a production API, not a stable format,
and not approved for merge, push, promotion or seed copy. Design:
[`../../design/native-c-interop-design.md`](../../design/native-c-interop-design.md).

The current prototype covers **scalar `i64` arguments and `i32`/`i64` results
only**. It has no pointers, ownership resources, callbacks, floats, decimals,
structs by value, TLS or database driver. C is trusted and unchecked; hashes are
provenance/corruption checks, not a signature authority or a memory-safety
proof.

## Approved steering decision (2026-10-31)

The approved direction is a **small interop toolkit plus an agent skill**, not a
universal configurable C-binding generator. Generic native compiler/link/ABI
infrastructure remains essential: mandatory compiler-visible native contracts,
the system-linker bridge and explicit foreign ownership are never replaced by
trusting an LLM. The deterministic toolkit enforces signatures, effects, link
inputs, artifact checks and ABI safety; the planned agent skill guides inspection
of C headers/ownership, idiomatic obo module design, C/assembly adapters from
templates, binding/effect/link declarations, and ABI/lifetime/error/integration
tests. Each library becomes a maintained obo package with ordinary imports, not
a generator run on every application build.

The skill is **proposed/planned, not implemented or installed**; its packaging
and template catalog need separate approval. `cinterop.py` remains an
experimental proof fixture/convenience, not a wide header parser or option-rich
universal generator. It is retained to harden the generic boundary and reusable
build/validation templates; a concrete future generator is considered only if
repeated real bindings show genuine automation benefit. This decision preserves
the one-time generic seam and does not require compiler changes per library.

## What changed in the repair

- Synthetic startup stack: the base is aligned **before** writing `argc`; the
  alignment pad is above the block, never between the base and `argc`. The
  pointer block has checked arithmetic, a 128 KiB stack-growth headroom and a
  clean `obo: cinterop startup stack overflow` failure before any write outside
  the 1 MiB buffer.
- Native calls are typed at the call site: the compiler's `__cinterop` sidecar
  records arity, `i64` argument types, result width and the conservative
  `freesall,writeshandles` effects. The tool checks **every** record against the
  `.cbind` spec before the link. A direct call with missing/extra arguments or
  an incompatible symbol is rejected; a same-signature sidecar edit remains a
  trusted-input substitution, not a cryptographic authorization.
- `i32` arguments are rejected in this prototype. `i32` returns remain
  supported and sign-extended; boundary tests cover `INT32_MIN`, `INT32_MAX`
  and `INT64_MIN/MAX`.
- Native calls get a conservative compiler summary: `freesall` and
  `writeshandles`, with deep/handle invalidation. Wrapper `.sym` files assert
  that contract. No spec `pure` claim is accepted.
- Provenance is checked before compiling and before link, and the build runs
  `verify` automatically. Required inputs include the header (where supplied),
  wrapper, shim source/object, `native.relocs`, host/start/link/tool/compiler
  and C compiler; missing files fail rather than being silently omitted. The
  final manifest records resolved shared-library paths and hashes, with the
  limitation that dynamic dependencies can still resolve differently at run
  time.
- The linker script has region capacity `ASSERT`s; the final ELF is checked for
  expected fixed addresses, segment flags, no W+X segment, non-executable
  `GNU_STACK`, file bounds and overlapping segments.
- `--check` rejects native modules because it cannot carry the typed sidecar;
  it does not pretend to have checked them. `--deps` remains a header scan.
- The builtin is named `__cinterop`, is internal to generated wrappers, and is
  still experimental. The spec format is `cinterop-proto/1`.

## Build

```sh
cd obo
make obo
python3 experimental/cinterop/cinterop.py build \
    experimental/cinterop/examples/scalar.cbind \
    experimental/cinterop/examples/scalar_app.obx \
    --out /tmp/opencode/c-interop-scalar --obo ./obo
/tmp/opencode/c-interop-scalar/app
python3 experimental/cinterop/cinterop.py verify \
    /tmp/opencode/c-interop-scalar/manifest.json
```

The ordinary application import remains:

```obo
module app
import "hello.obx"

fn main() {
    println(hello::add(20, 22))
}
```

The libpq availability probe uses only `PQlibVersion`, with a manual
declaration because the development header is absent on the probe host:

```sh
python3 experimental/cinterop/cinterop.py build \
    experimental/cinterop/examples/pq.cbind \
    experimental/cinterop/examples/pq_app.obx \
    --out /tmp/opencode/c-interop-pq --obo ./obo
/tmp/opencode/c-interop-pq/app
ldd /tmp/opencode/c-interop-pq/app
```

It does not connect to a database, send SQL, start a server, open a socket or
use a service.

## Tests

```sh
python3 obo/experimental/cinterop/tests/test_cinterop.py
```

The targeted test covers 0..8 scalar arguments, `INT64_MIN/MAX`, `i32` return
bounds, odd/even stack alignment, live-register/nested/non-zero-depth native
calls, the freestanding ABI probe, argv/environment parity, `OBO_CHECK`/
`OBO_STATS` visibility, near-limit environment pointers, direct wrong-arity
calls, incompatible substitutions, `i32` input rejection, header/declaration
requirements, variadic rejection, missing definitions, `--check` policy,
manifest metadata/file tampering, header changes, deleted/tampered relocations,
missing wrapper `.sym`, and the libpq version probe. A libpq build failure is a
test failure when `libpq.so.5` is present; the test skips only when the library
is absent.

## Known limits

- The compiler still emits a fixed `ET_EXEC`; the tool extracts its load regions
  into a relocatable object. Production should emit a real object with
  sections/relocations directly.
- Startup uses host `crt1.o`/`crti.o`/`crtn.o` and a synthetic initial-stack
  block. It does **not** link `crtbegin.o`/`crtend.o` and does **not** claim
  support for C constructors, TLS, IFUNC or other hosted-runtime behaviour
  beyond what the minimal probe exercised. `rt_finish` still exits by raw
  syscall, so libc `atexit`/stdio teardown is bypassed.
- The final layout is non-PIE and fixed-address; ASLR/PIE is unsupported.
- C is trusted and unchecked. A malicious `.cbind`, header/declaration or
  same-signature sidecar is within the trusted build input, not sandboxed.
- There is no foreign-resource finalizer and no `PGconn`/`PGresult`, `malloc`,
  borrowed-byte or automatic `PQfinish`/`PQclear` support. `PQlibVersion` is
  not a database driver.
