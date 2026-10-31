# Experimental C interop (`cinterop`)

Status: **REVISE response plus explicit-close resource-adapter checkpoint,
pending independent rereview.** The prototype was repaired after the
architecture/security review of `a29f35d`, then merged with released `0.10`
main. This is still an experimental, trusted-build probe, not a production API,
not a stable format, and not approved for merge, push, promotion or seed copy.
Design:
[`../../design/native-c-interop-design.md`](../../design/native-c-interop-design.md).

The compiler boundary still supports **scalar `i64` arguments and `i32`/`i64`
results only**. The toolkit now also has a small trusted **C resource registry**
and an explicit-close libpq adapter: obo receives generation/kind-checked
integer tokens, never a raw `PGconn*`/`PGresult*`. Connection/Rows close is
explicit; dropping an ordinary record does **not** call `PQfinish`/`PQclear`.
There are still no callbacks, C variadics, structs by value, source floats/
decimals in the native contract, TLS, automatic foreign finalizers or a full
database driver. C is trusted and unchecked; hashes are provenance/corruption
checks, not a signature authority or a memory-safety proof.

## Approved steering decision (2026-10-31)

The approved direction is a **small interop toolkit plus an agent skill**, not a
universal configurable C-binding generator. Generic native compiler/link/ABI
infrastructure remains essential: mandatory compiler-visible native contracts,
the system-linker bridge and explicit foreign ownership are never replaced by
trusting an LLM. The deterministic toolkit enforces signatures, effects, link
inputs, artifact checks and ABI safety; the project agent skill guides inspection
of C headers/ownership, idiomatic obo module design, C adapters from templates,
binding/effect/link declarations, and ABI/lifetime/error/integration tests. Each
library becomes a maintained obo package with ordinary imports, not a generator
run on every application build.

The portable project skill and real templates are **implemented** under
[`skill/`](skill/SKILL.md). They are not installed globally; packaging and a
stable template catalog still need separate approval. `cinterop.py` remains an
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

## Explicit-close libpq adapter

`examples/libpq.cbind` + `examples/libpq.obx` + `toolkit/libpq_adapter.c` are a
real second package using the same generic toolkit. The public surface is
ordinary obo:

```obo
let made = libpq::connect(dsn_bytes)
match made {
    ok(conn) {
        let rows = libpq::exec_params(&conn, sql_bytes, values, binary_flags)
        // read values/errors, then rows.close() and conn.close()
    }
    err(m, c) { ... }
}
```

- `Connection` and `Rows` contain only an integer token. Obo record fields are
  not private; the C registry rejects stale/wrong-kind/unknown tokens before
  dereference. The token is not an unforgeable capability.
- `close` is explicit: `conn.close()`, `rows.close()`. A `PGresult` remains
  usable after its connection closes. `PQfinish`/`PQclear` happen exactly once.
- `live_resources()`/`live_connections()`/`live_results()`/`live_buffers()`
  expose the registry counters; the tests fail if a close is omitted.
- `exec_params` sends a fixed SQL statement plus `$n` parameters through
  `PQexecParams`. SQL/text values reject embedded NUL; binary values preserve
  their length including NUL; `nil` is SQL NULL and an empty array is an empty
  value. There is no SQL interpolation.
- Values and error messages are copied by length; `PQgetlength` is used, never
  `strlen`. Exact numeric text, OID and typmod are preserved; no numeric value
  is routed through a float.
- The byte-copy bridge is a bounded one-byte-at-a-time loop for this first
  proof. It is deliberately slow and honest, not a general string ABI.

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

The scalar build still uses the generated wrapper. The libpq availability and
result-ownership probe uses `PQlibVersion` and
`PQmakeEmptyPGresult(NULL, status)`/`PQclear` with manual declarations because
the development header is absent on the probe host:

```sh
python3 experimental/cinterop/cinterop.py build \
    experimental/cinterop/examples/pq.cbind \
    experimental/cinterop/examples/pq_app.obx \
    --out /tmp/opencode/c-interop-pq --obo ./obo
/tmp/opencode/c-interop-pq/app
ldd /tmp/opencode/c-interop-pq/app
```

Build the full explicit-close adapter without connecting:

```sh
python3 experimental/cinterop/cinterop.py build \
    experimental/cinterop/examples/libpq.cbind \
    experimental/cinterop/examples/libpq_probe_app.obx \
    --out /tmp/opencode/c-interop-libpq --obo ./obo
/tmp/opencode/c-interop-libpq/app
```

The probe output is `PQlibVersion=160015`, an empty `PGRES_TUPLES_OK` result
with status 2, successful close, and `live=0`. It does not connect to a
database, send SQL, start a server, open a socket or use a service.

The live PostgreSQL smoke test is **opt-in**. Pass an explicitly authorized
conninfo; the test app uses only `SELECT`s and explicit closes:

```sh
CINTEROP_TEST_DSN='host=/var/run/postgresql port=5432 dbname=postgres user=chris connect_timeout=3' \
  python3 experimental/cinterop/tests/test_cinterop.py
```

That conninfo is a private local-development example, not a default. The test
does not create roles/databases/schema, edit server configuration, start
services, expose ports, install packages or read credential files. Without
`CINTEROP_TEST_DSN`, the live section is skipped.

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

It also builds and runs:

- the real `libpq` `PQlibVersion` + `PQmakeEmptyPGresult`/`PQclear` probe;
- the deterministic fake-libpq C suite (`fake_adapter_test.c`): token poisoning,
  double close, `PQfinish`/`PQclear` counts, NULL vs empty, binary NUL,
  allocation failure, malformed packets, result-outlives-connection, and
  injection-as-data;
- the fake-libpq obo wrapper app and a deliberate missing-close app whose
  `live=1` proves the leak counter detects the omission;
- the opt-in live local PostgreSQL smoke test when `CINTEROP_TEST_DSN` is set.

The project skill and buildable templates are in [`skill/`](skill/SKILL.md).

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
- There is no automatic foreign finalizer. `PGconn`/`PGresult` and bounded
  borrowed bytes are supported only through the explicit-close adapter; dropping
  a `Connection`/`Rows` never calls `PQfinish`/`PQclear`. The adapter is not a
  full database driver or a stable public API.
- The token registry protects against accidental stale/wrong-kind/forged integer
  use, not against trusted C or an adversarial caller. Its generation is 31
  bits. Obo record fields are not private.
- Source floats/decimals are not part of the native contract. The libpq
  adapter preserves exact numeric text/OID/typmod and never converts a database
  numeric through a float.
- The project skill is a repository/portable artifact, not a global install.

## Known follow-ups (non-blocking, not fixed here)

These were noted by the independent execution review and are recorded rather
than silently claimed as resolved:

- `with_connect_timeout` detects an existing timeout with a `strstr` substring
  check. A quoted conninfo value that happens to contain `connect_timeout=`
  could be misdetected. A stricter conninfo/keyword parse is future work; the
  approved live test passes an explicit bounded timeout.
- The manifest verifier hashes `host_object` and `start_object` when present but
  does not list them in the required role sets, so a manifest that omits those
  roles is not rejected on that ground alone. Add them to the required sets and
  negative tests in a follow-up.
- The `libpq_result` probe builds unconditionally, while the older `pq` probe
  skips only when `libpq.so.5` is absent. On a host without libpq the former
  fails rather than skipping; the skip policy should be made consistent.
- The synthetic startup stack is a static 1 MiB buffer with runtime length and
  headroom checks, but no guard page or compile-time capacity assertion. It is
  prototype infrastructure, not a memory-safety guarantee.
