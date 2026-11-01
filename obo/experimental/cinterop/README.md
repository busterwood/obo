# Experimental C interop (`cinterop`)

Status: **P2T-a cinterop prerequisites implemented; hardening checkpoint
pending independent rereview.** The prototype was repaired after the
architecture/security review of `a29f35d`, merged with released `0.10` main, and
now has an explicit-defer cleanup story, a guarded hosted entry stack with
proper C exit/atexit/stdio shutdown, a second real library (zlib), validated
private include roots/cflags and full RELRO/immediate binding on hosted links.
A hosted OpenSSL 3.0 memory-BIO/TLS 1.3 qualification also passes. This is still
an experimental, trusted-build probe, not a production API, not a stable format,
and not approved for merge, push, promotion or seed copy. It does **not** add a
TLS adapter API to the toolkit or change the compiler, symbol format, ABI or
package version; the separate experimental P2T-b server adapter under
`../tls/` consumes this seam.
Design:
[`../../design/native-c-interop-design.md`](../../design/native-c-interop-design.md).

The compiler boundary supports **`i64` arguments, and argument kinds** (borrowed
bytes and strings, NUL-checked C strings, `i32`/`u32` scalars, int arrays and
nil-able byte-array lists; see *Argument kinds* below), with `i32`/`i64` results. The toolkit now also has a small trusted **C resource registry**
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
- A bare `i32` argument is rejected in this prototype; `i32@k` (a kinds binding)
  range-checks an obo int at the call instead. `i32` returns remain supported and
  sign-extended; boundary tests cover `INT32_MIN`, `INT32_MAX` and `INT64_MIN/MAX`.
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
  connect still returns a connection token with `CONNECTION_BAD`. The wrapper
  returns the diagnostic as the `err` message (for example
  `unterminated quoted string in connection info string`) and gives each other
  negative code its own message.

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

- `Connection` and `Rows` contain only an integer token, private to the wrapper
  module (0.13+). Importers use `connect`/`query`; the C registry rejects
  stale/wrong-kind/unknown tokens before dereference. The token is not an
  unforgeable capability, and a deliberate same-module raw-token escape (such as
  `buffer::wrap`) remains possible.
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
- Packet limits are enforced in the wrapper before any C call: more than 32
  parameters (`PQ_MAX_PARAMS`) or more than 1 MiB of SQL (`PQ_MAX_SQL`) returns
  a specific `err`. The constants in `libpq.obx` must match the C defines in
  `toolkit/libpq_adapter.c`. An adapter-side rejection is reported by its code,
  never as a possibly stale connection error.
- `connect_timeout`: when the conninfo has no `connect_timeout` keyword, the
  adapter appends `connect_timeout=5`. Any explicit `connect_timeout` is used as
  given, so an explicit `connect_timeout=0` disables libpq's timeout and gets no
  default; negative values are passed to libpq unchanged.
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

### Signature checks for the libpq binding

`libpq.cbind` and `libpq_fake.cbind` list `toolkit/cres.h` and
`toolkit/libpq_adapter.h` as headers, so their declarations are checked against
the real adapter prototypes (`signature_check: header`). A declaration that
disagrees with a header fails the build before link. libpq's own prototypes
still come from the hand-written `toolkit/libpq_decls.h`, because the libpq
development header is not installed on the probe host. The header check
therefore covers the obo-facing adapter API, not libpq's API itself.

### Private include roots, cflags and link hardening

A `.cbind` may list `include_dirs` and `cflags`. The same preprocessor state is
used by:

- the generated C signature check (`signature_check: header`),
- every binding source compile, and
- the dependency scan for each of those compiles.

All three use `-fno-pie -O2 -I <spec>`, the declared `include_dirs` and the
declared `cflags`. A private header selected by `__OPTIMIZE__` or a `-D` flag is
therefore hashed, and a conditional prototype cannot validate one declaration
while the source object is compiled against another. A negative test covers
both cases.

That matters for a private sysroot: the explicit `headers` list is not enough
when `openssl/ssl.h` includes other headers from the include root. A relative
include directory must stay inside the spec directory or the obo tree; an
absolute include directory is allowed only when written explicitly. Symlink
resolution is checked too. The manifest records `include_dirs`, `include_paths`,
`cflags`, and `header_deps` (the transitive preprocessor inputs, with SHA-256),
so a changed private header is detected by `verify` instead of being ambient
state. The generated wrapper/spec does not need absolute build paths; committed
bindings should use repository-relative paths (for example the zlib binding's
`zlib_system.h` wrapper around `<zlib.h>`), and a cbind for a private root can
be generated inside the private build directory. Source, header and include
roots are passed to `-ffile-prefix-map`, so absolute build roots do not appear
in emitted objects.

Every hosted cinterop link now gets `-Wl,-z,relro`, `-Wl,-z,now` and
`-Wl,-z,noexecstack` by default. The final ELF must contain a non-empty
`PT_GNU_RELRO`, `BIND_NOW`/`DF_1_NOW`, and `.preinit_array`/`.init_array`/
`.fini_array` must lie inside the RELRO range; `validate_final` and `verify`
both check this. The separate freestanding runtime/seed build is unchanged. A
missing or incompatible shared-library symbol therefore fails at process load,
not on a later request.

### Hosted OpenSSL 3.0 qualification (P2T-a prerequisite)

The test suite qualifies the hosted runtime against the system OpenSSL 3.0
libraries without installing development packages or using root:

```sh
# The test locates libssl.so.3/libcrypto.so.3 with ldconfig -p (falling back to
# the standard multiarch paths). If the installed libssl-dev package version
# does not match libssl3t64, it uses the matching package in a private cache:
#   apt-get download libssl-dev=<installed version>
#   dpkg-deb -x libssl-dev_<version>_amd64.deb <private-root>
# It then adds <private-root>/usr/include and
# <private-root>/usr/include/x86_64-linux-gnu as include_dirs. A cache hit is
# content-checked against a stored SHA-256 manifest of the extracted headers.
# The generated spec and C fixture copy live in the private build directory, so
# the uncommitted spec has no absolute repository source paths.
python3 experimental/cinterop/tests/test_cinterop.py
```

The C fixture (`tests/openssl_qualify.c`) is linked through `cinterop.py` with
`-l:libssl.so.3 -l:libcrypto.so.3` and checks:

- `OPENSSL_init_ssl(OPENSSL_INIT_NO_LOAD_CONFIG)`, `OpenSSL_version_num` and the
  default provider;
- `RAND_bytes`;
- a generated self-signed cert/key, parsed back from PEM memory BIOs;
- `SSL_CTX_new(TLS_server_method)`, minimum TLS 1.2, maximum TLS 1.3;
- a TLS 1.3 handshake over memory BIOs with ALPN `http/1.1` and an application
  data roundtrip;
- `pthread_once`, `clock_gettime`, `mmap` and thread-local `errno`/OpenSSL error
  queue isolation;
- linked constructor, libc atexit, unflushed C stdio, `OPENSSL_atexit` ordering,
  explicit `OPENSSL_cleanup`, and a post-cleanup no-crash check;
- full RELRO/NOW and the expected `libssl.so.3`/`libcrypto.so.3` dependencies.

The same C fixture also runs under ASAN/UBSAN as a separate harness. The test
skips when `libssl.so.3`/`libcrypto.so.3` are absent (or
`CINTEROP_TEST_NO_OPENSSL=1` is explicitly set). The header download needs the
network. If `apt-get download` cannot run or fails, the qualification is
**skipped** with the printed line `skipped: openssl (download failed)` and the rest of
the suite continues. A skip is never a silent pass. With the library present and
headers available, a build or qualification failure is still a test failure.
Extracted headers are never committed.

This qualifies the hosted CRT/loader/OpenSSL interaction for the approved TLS
adapter work. It is **not** the TLS adapter itself: the separate experimental
P2T-b adapter under `../tls/` provides `TlsContext`/`TlsSession`,
certificate-policy and ALPN; this qualification makes no general TLS/IFUNC
guarantee and does not change the compiler, symbol format, ABI or package
version.

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

## Argument kinds (stage (a))

A binding whose `args` are all `i64` is the int form, as before. Any other binding
is a **kinds binding**: its spec `args` names each C parameter as a kind, and its
wrapper passes the same list as the `__cinterop` descriptor, with the obo arguments
after it:

```
pub fn hash(b: &[]byte): int {
    return __cinterop("bytes_hash", "borrow_bytes@0", b)
}
```

`@k` names the obo argument, counted from 0. Each obo argument has exactly one data
kind:

| kind | obo argument | C parameters |
| --- | --- | --- |
| `i64@k` | int | `long` |
| `i32@k` | int, range-checked (exit 11) | `int` |
| `u32@k` | int, range-checked (exit 11); values above 2^31 are fine | `unsigned int` |
| `borrow_bytes@k` | `[]byte` or `string` | `const void *, int` (data, length) |
| `borrow_cstr@k` | `[]byte` or `string`, NUL-checked (exit 11) | `const char *`, a copy freed after the call |
| `ints@k:i32`, `ints@k:u32` | `[]int`, each element range-checked (exit 11) | `const int *`, `const unsigned int *` |
| `ints@k:len` | the element count of list `k`; reads no argument | `int` |
| `nil_bytes_list@k` | `[]?[]byte` | `const char *const *` (NULL for nil), `const int *` (lengths) |
| `null` | none | `void *`, NULL |
| `const:<n>` | none | `void *`, the value `n` (e.g. `const:-1`) |

Rules:

- `ints@k:len` must name a list kind (`ints` or `nil_bytes_list`).
- A `borrow_*`, `ints` or `nil_bytes_list` argument is an array or string. Arrays are
  passed as `&a`, under the existing `&T` argument rules (a temporary array is
  refused by `&`). A string is passed by value. A string temporary, such as a call
  result or a concatenation, lives until the end of its statement, so it is accepted.
- The nil string and an empty array pass a non-NULL pointer with length 0.
- Codegen (design 3.2): every argument expression runs first. The data pointers and
  lengths are then read immediately before the call, so `f(&a, grow(&a))` passes the
  grown array where the checker accepts the call. The checker adds no new rule.
- Scratch copies (`borrow_cstr`, `ints`, `nil_bytes_list`) are freed after the call on
  the normal return path. A contract violation exits 11 before the C call. The
  hosted counter `obo_ci_scratch_live()` is 0 after each call; the test checks it.
- The sidecar records the descriptor in its argtypes field, and the tool checks it
  against the spec (arity and each kind) before the link. A kinds binding needs a
  maintained `wrapper`; generated wrappers stay int-only.
- Limits: at most 12 kinds per binding and 8 for the int form; a kind takes one
  `@k` argument (`null` and `const:<n>` take none); results are still `i32`/`i64`.

The helpers the thunks call are in `host.c` (`obo_ci_*`), hosted builds only.

## Tests

```sh
python3 obo/experimental/cinterop/tests/test_cinterop.py
```

Scratch build trees and the OpenSSL header cache live under `TMPROOT` (default:
Python's `tempfile.gettempdir()`, which honours `TMPDIR`). Set `TMPROOT` to choose
another location; the committed build does not depend on `/tmp`.

The targeted test covers 0..8 scalar arguments, `INT64_MIN/MAX`, `i32` return
bounds, odd/even stack alignment, live-register/nested/non-zero-depth native
calls, the freestanding ABI probe, argv/environment parity, `OBO_CHECK`/
`OBO_STATS` visibility, near-limit environment pointers, direct wrong-arity
calls, incompatible substitutions, `i32` input rejection, header/declaration
requirements, variadic rejection, missing definitions, `--check` policy,
manifest metadata/file tampering, header changes, deleted/tampered relocations,
missing wrapper `.sym`, the libpq version probe, and the argument kinds (`kinds()`:
a `bytes` fixture with empty, 1 MiB, nil-string and embedded-NUL inputs, the NUL
rejected by `borrow_cstr`, the `const:-1` recorder, the `f(&a, grow(&a))` timing,
`u32` above `2^31`, `i32`/`u32`/element range exits 11, nil and empty list elements,
the scratch counter, and refused spec and descriptor mismatches before link). A libpq build failure is a
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
- invalid conninfo (`host='unterminated`) returns libpq's copied diagnostic with
  code 2 and registers no connection; the fake parser reports the same text, and
  the real libpq parser runs too when `libpq.so.5` is present. No server is used;
- packet limits with the fake ABI: 33 parameters and 1 MiB + 1 bytes of SQL are
  rejected with specific messages, and 32 parameters is accepted; exactly 1 MiB
  of SQL is not rejected by the limit check, and a packet of exactly 16 MiB is
  accepted while one byte more is rejected by the buffer bound;
- adapter error messages with the fake ABI: `PQconnectdb` returning NULL (code 0),
  an empty `PQexecParams` failure, and exec on a closed connection (code -1) each
  give their own message;
- the wrapper limit constants (`PQ_MAX_PARAMS`, `PQ_MAX_SQL`, `PQ_MAX_BUFFER`)
  equal the C defines (`PQ_MAX_PARAMS`, `PQ_MAX_SQL` in `toolkit/libpq_adapter.c`,
  `CRES_MAX_BUFFER` in `toolkit/cres.h`), checked by reading the sources;
- a libpq spec declaration that disagrees with `libpq_adapter.h` (an
  internally consistent `int` against the header's `long`) fails the signature
  check before link;
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
- private `include_dirs`/`cflags`: a header reachable only through the include
  root must work in the signature check and source compile, and its absence or
  a missing cflag must fail (relative escapes are rejected);
- RELRO/NOW: `readelf` sees `GNU_RELRO` and `BIND_NOW`/`FLAGS_1 NOW`, and
  `.preinit_array`/`.init_array`/`.fini_array` lie inside the RELRO range;
- hosted OpenSSL 3.0: private `libssl-dev` extraction, real `libssl.so.3`/
  `libcrypto.so.3` link, TLS 1.3 memory-BIO/ALPN handshake, provider/RAND,
  thread-local state, constructor/atexit/stdio/`OPENSSL_cleanup`, and a separate
  ASAN/UBSAN run;
- ASAN/UBSAN runs of the fake adapter, zlib adapter and guarded-stack fixture;
- the opt-in live local PostgreSQL smoke test when `CINTEROP_TEST_DSN` is set.

The project skill and buildable templates are in [`skill/`](skill/SKILL.md).
The experimental database packages that build on this toolkit are in
[`../db/`](../db/README.md): a PostgreSQL typed-parameter adapter and a
declarations-only SQLite adapter, both explicit-close and tested.

## Known limits

- Argument kinds: no ASan run over the obo image itself. The scratch check is the
  counter above, not a sanitizer. Contract exits (11) end the process without
  freeing scratch, which the OS reclaims.

- The compiler still emits a fixed `ET_EXEC`; the tool extracts its load regions
  into a relocatable object. Production should emit a real object with
  sections/relocations directly.
- Hosted startup uses host `crt1.o`/`crti.o`/`crtbegin.o`/`crtend.o`/`crtn.o`,
  `.init_array`/`.fini_array` placement, and a guarded `mmap` stack. Constructors,
  atexit and C stdio flush are tested in this hosted subset. Hosted OpenSSL 3.0
  passes the qualification above, including its use of libc TLS and
  `pthread_once`. General TLS/IFUNC support for arbitrary libraries is still
  **not** claimed. The experimental P2T-b TLS adapter now exists under
  `../tls/`; the cinterop qualification itself still makes no such claim. The
  baseline
  freestanding runtime still exits by raw syscall; only the separate cinterop
  hosted runtime uses the exit callback.
- The final layout is non-PIE and fixed-address; ASLR/PIE is unsupported.
- C is trusted and unchecked. A malicious `.cbind`, header/declaration or
  same-signature sidecar is within the trusted build input, not sandboxed.
- There is no automatic foreign finalizer. `PGconn`/`PGresult` and bounded
  borrowed bytes are supported only through the explicit-close adapter; dropping
  a `Connection`/`Rows` never calls `PQfinish`/`PQclear`. The adapter is not a
  full database driver or a stable public API.
- The token registry protects against accidental stale/wrong-kind/forged integer
  use, not against trusted C or an adversarial caller. Its generation is 31
  bits. Obo token fields are private to their wrapper modules; that reduces
  accidental construction, not deliberate forgery.
- Source floats/decimals are not part of the native contract. The libpq
  adapter preserves exact numeric text/OID/typmod and never converts a database
  numeric through a float.
- The hosted OpenSSL qualification is distro-aware only to the extent of its
  package probe: it accepts `/usr/include` headers only when the installed
  `libssl-dev` package version matches `libssl3t64`, otherwise it fetches the
  matching package. Libraries are discovered with `ldconfig -p`; the test
  skips when `libssl.so.3`/`libcrypto.so.3` are genuinely absent (or the
  explicit opt-out is set), and skips with `skipped: openssl (download failed)` when
  the matching headers cannot be downloaded.
- The private header cache is content-checked against a stored hash manifest.
  That catches accidental cache corruption; same-user tampering with both the
  cache and its manifest is outside the trusted build boundary.
- A private-root cbind generated at test/build time may contain an explicit
  absolute `include_dirs` entry. Source, header and include roots are
  path-mapped in emitted objects, and extracted headers are never committed.
- The project skill is a repository/portable artifact, not a global install.
- Modules found through `-I` paths and the obo library are not yet hashed in the
  build manifest. Imported-module provenance needs a compiler change and is
  deferred.

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
