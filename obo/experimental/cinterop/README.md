# Experimental C interop (`cinterop`)

Status: **hardening checkpoint pending independent rereview.** The prototype
was repaired after the architecture/security review of `a29f35d`, merged with
released `0.10` main, and now has an explicit-defer cleanup story, a guarded
hosted entry stack with proper C exit/atexit/stdio shutdown, and a second real
library (zlib). This is still an experimental, trusted-build probe, not a
production API, not a stable format, and not approved for merge, push, promotion
or seed copy.
Design:
[`../../design/native-c-interop-design.md`](../../design/native-c-interop-design.md).

The compiler boundary still supports **scalar `i64` arguments and `i32`/`i64`
results only**. The toolkit now also has a small trusted **C resource registry**
and an explicit-close libpq adapter: obo receives generation/kind-checked
integer tokens, never a raw `PGconn*`/`PGresult*`. Connection/Rows/Buffer close
is explicit and normally written with obo `defer`; dropping an ordinary record
does **not** call `PQfinish`/`PQclear`/`free`. There are still no callbacks, C
variadics, structs by value, source floats/decimals in the native contract, TLS
or IFUNC guarantees, automatic foreign finalizers or a full database driver. C is trusted and unchecked; hashes are provenance/corruption
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

- Guarded hosted startup: the synthetic pointer block sits on a separate
  `mmap` stack with `PROT_NONE` guard pages below and above, a validated size,
  checked arithmetic and a 128 KiB growth headroom. The base is aligned before
  writing `argc`; the pad is above the block. Invalid sizes and near-ARG_MAX
  blocks fail with a clean message before any write outside the usable stack.
- Proper hosted shutdown: only the cinterop build compiles a separate
  `-DOBO_HOSTED_EXIT` runtime variant (the baseline freestanding runtime bytes
  are unchanged). Normal return, `exit`, `OBO_CHECK` leak failure and runtime
  panics enter a host C callback and call libc `exit`, so constructors run,
  C stdio flushes and atexit handlers run while the obo exit status is kept.
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
  wrapper, shim source/object, `native.relocs`, host/start/host-stack/link
  sources, runtime source/entry/linker script, hosted runtime blobs, tool,
  compiler and C compiler; `host_object`/`start_object` are required build
  roles. Missing files fail rather than being silently omitted. The
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
- A cbind may list optional `declaration_files`: C declaration headers are
  resolved and hashed into the manifest as provenance inputs without being
  compiled or promoted to `signature_check: header`. Use this for honest
  declarations-only bindings.
- `pq_connect` distinguishes invalid conninfo syntax (return `-4`, with a
  copied diagnostic from `pq_conninfo_error_buf()`) from buffer, allocation and
  connection-status failures. A syntactically valid conninfo that libpq cannot
  connect still returns a connection token with `CONNECTION_BAD`.

## Explicit-close libpq adapter

`examples/libpq.cbind` + `examples/libpq.obx` + `toolkit/libpq_adapter.c` are a
real second package using the same generic toolkit. The public surface is
ordinary obo:

```obo
let made = libpq::connect(dsn_bytes)
match made {
    ok(conn) {
        defer conn.close()
        let rows = libpq::exec_params(&conn, sql_bytes, values, binary_flags)
        match rows {
            ok(r) {
                defer r.close()
                // read values/errors
            }
            err(m, c) { ... }
        }
    }
    err(m, c) { ... }
}
```

- `Connection` and `Rows` contain only an integer token. Obo record fields are
  not private; the C registry rejects stale/wrong-kind/unknown tokens before
  dereference. The token is not an unforgeable capability.
- `close` is explicit and borrows the wrapper, so `defer conn.close()` and
  `defer rows.close()` are the canonical cleanup. A `PGresult` remains usable
  after its connection closes. `PQfinish`/`PQclear` happen exactly once; the
  record drop never frees the foreign object.
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

## Explicit defer cleanup

`defer` is the idiomatic explicit cleanup. Register it promptly after creating
the wrapper, and do not use `exit(...)` when cleanup must run: obo `exit` runs no
defers (libc atexit handlers still run through the hosted exit callback).

```obo
fn use(): Result[bool] {
    let made = libpq::connect(dsn)
    match made {
        ok(conn) {
            defer conn.close()
            let q = libpq::query(&conn, sql)
            match q {
                ok(rows) {
                    defer rows.close()
                    return ok(rows.status() == libpq::PGRES_TUPLES_OK)
                }
                err(m, c) { return err(m, c) }
            }
        }
        err(m, c) { return err(m, c) }
    }
}
```

The same pattern works for `buffer::Buffer`, including a `defer` inside a loop
or nested block. Each block exit (normal, early return, error arm, `break`/
`continue`) runs its defers once; double close is a safe false. The C registry
live counters remain diagnostics independent of record drop.

`examples/libpq_defer_app.obx` exercises success, early-return, nested
connection/result lifetimes, a loop and a C buffer. `examples/libpq_outlives_app.obx`
closes a connection before its result and reads the result afterwards.

## Build

The cinterop build compiles its own `-DOBO_HOSTED_EXIT` runtime under the private
output directory; the normal compiler/freestanding runtime and its default
output are unchanged. The hosted stack defaults to 1 MiB; override it with
`OBO_CINTEROP_STACK_BYTES` between 256 KiB and 16 MiB. Invalid sizes fail before
launch.

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
- explicit-defer success/early-return/nested/loop/buffer paths and a
  result-outlives-connection app;
- real libpq's conninfo parser for timeout detection (quoted values are not
  mistaken for keywords), with `PQconnectdb` wrapped and never called;
- guarded-stack fixtures: invalid size, low/high guard SIGSEGV, and a real obo
  deep-recursion guard fault;
- hosted shutdown fixtures: constructor, atexit, C stdio flush, normal exit,
  `exit(7)` and `OBO_CHECK` exit 8;
- a second real library, zlib: real system header, in-memory binary
  compress/uncompress roundtrip, API errors and explicit close;
- ASAN/UBSAN runs of the fake adapter, zlib adapter and guarded-stack fixture;
- the opt-in live local PostgreSQL smoke test when `CINTEROP_TEST_DSN` is set.

The project skill and buildable templates are in [`skill/`](skill/SKILL.md).
The experimental database packages that build on this toolkit are in
[`../db/`](../db/README.md): a PostgreSQL typed-parameter adapter and a
declarations-only SQLite adapter, both explicit-close and tested.

## Known limits

- The compiler still emits a fixed `ET_EXEC`; the tool extracts its load regions
  into a relocatable object. Production should emit a real object with
  sections/relocations directly.
- Hosted startup uses host `crt1.o`/`crti.o`/`crtbegin.o`/`crtend.o`/`crtn.o`,
  `.init_array`/`.fini_array` placement, and a guarded `mmap` stack. Constructors,
  atexit and C stdio flush are tested in this hosted subset. TLS and IFUNC are
  **not** claimed; the normal CRT/loader support for arbitrary library TLS/IFUNC
  needs its own fixtures and review. The baseline freestanding runtime still
  exits by raw syscall; only the separate cinterop hosted runtime uses the exit
  callback.
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

## Review findings resolved in this hardening checkpoint

- `connect_timeout` detection now asks libpq's own `PQconninfoParse` whether the
  `connect_timeout` keyword has a value. Quoted values such as
  `application_name='connect_timeout='` are covered by a real-parser test.
- `host_object` and `start_object` are required manifest build roles, and a
  negative test deletes `host_object` and expects verification to fail.
- The real-libpq version/result probes and the timeout-parser test share one
  skip policy: skip only when `libpq.so.5` is actually absent; a build failure
  with the library present is a test failure.
- The static 1 MiB startup buffer is replaced by a guarded `mmap` stack with
  invalid-size, guard-fault and deep-recursion tests.

Remaining production limits are listed above; these findings are not silently
carried as unresolved.
