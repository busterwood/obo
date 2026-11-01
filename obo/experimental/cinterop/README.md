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
nil-able byte-array lists; see *Argument kinds* below), with `i32`/`i64` results,
the stage (b)/(c) additions: copy-out `cbytes`, `cstr_out`, `void` and
`nonzero` results, and body-less `native fn` declarations (see *Return kinds and
typed native declarations* below), and the stage (d) foreign handles with
`own`/`own_opt`/`borrow<C>`, explicit `close`/`is_open`, use-after-close exit 7
and the leak census (see *Foreign handles* below). The libpq package is now on
these foreign handles (`Connection`/`Res` hold the real `PGconn*`/`PGresult*` in
a private word; there is no integer token or `cres` registry). The old trusted
**C resource registry** and `libpq_adapter.c/.h` are retained only because
`db/postgres` still uses them until stage (g). Connection/Rows close is explicit
and normally written with obo `defer`; dropping an ordinary record does **not**
call `PQfinish`/`PQclear`/`free`. There are still no callbacks, C variadics,
structs by value, source floats/decimals in the native contract, TLS or IFUNC
guarantees, automatic foreign finalizers or a full database driver. C is trusted
and unchecked; hashes are provenance/corruption checks, not a signature
authority or a memory-safety proof.

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
  still experimental. The spec format is `cinterop-proto/1` (unchanged) or
  `cinterop-proto/2` for the return kinds and typed native declarations; the
  relocation sidecar is `cinterop-relocs 2`, and a v1 sidecar is still read.
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

## libpq on cinterop v2 (stage (e))

`examples/libpq.cbind` + `examples/libpq.obx` are the libpq binding on the v2
boundary. `Connection` and `Res` are `pub type ... = foreign` handles (the real
`PGconn*`/`PGresult*` live in the private word); `Rows` owns one `Res` and
records the result format. The public surface is ordinary obo:

```obo
let made = libpq::connect(dsn_bytes)
match made {
    ok(conn) {
        defer conn.close()                 // Result[bool], discarded by defer
        let rows = libpq::exec_params(&conn, sql_bytes, values, binary_flags)
        match rows {
            ok(r) {
                defer r.close()            // Rows.close is unit
                // read values/errors
            }
            err(m, c) { ... }
        }
    }
    err(m, c) { ... }
}
```

- `close` is the foreign-handle close: `Connection.close` returns
  `Result[bool]` (`ok(false)` on a second close), `Rows.close` is unit and closes
  its `Res`. A `PGresult` remains usable after its `PGconn` closes. There is no
  integer token and no `cres` registry in this binding.
- The live counters (`live_resources`, `live_connections`, `live_results`,
  `live_buffers`) are removed. Under `OBO_CHECK=1` the foreign-handle census
  reports a dropped open `Connection`/`Res` (exit 8); the tests use that.
- Native kinds: `PQconnectdb` uses `borrow_cstr@0`/`own_opt<PGconn>`;
  read-only calls use `borrow<...>`, `PQexecParams` uses `borrow_mut<PGconn>@0`;
  `PQgetvalue` is `cbytes` with length `PQgetlength` and a 16 MiB cap;
  `PQerrorMessage`/`PQresultErrorMessage`/`PQfname` are `cstr_out`;
  `PQftype` is `u32`, `PQfmod` is `i32`; `PQmakeEmptyPGresult` takes
  `null`/`i32@0` and returns `own<PGresult>`. A NULL empty result is a contract
  violation and the `own` result exits 11 (visible change from the old adapter,
  which returned an error); the fake test forces this with `FAKE_EMPTY_NULL=1`.
- `Rows` accessors check `is_open(&r.h)` and then the row/column against
  `ntuples`/`nfields` before any `is_null`/`length`/`value`/`ftype` call.
  `value` returns `err("SQL NULL", 1)` for SQL NULL and
  `err("value is larger than the 16 MiB limit", 2)` for a copy-out over the cap,
  so the two nil cases stay distinct. `status`/`ntuples`/`nfields` return -1,
  `is_null` false, `length` -1, `ftype` 0 and `fmod` -1 on a closed/invalid
  result; the `Result`-returning accessors return an explicit error.
- `exec_params`/`exec_params_binary`/`query` send the obo arrays directly to
  `PQexecParams`. SQL/text values reject embedded NUL; binary values preserve
  their length including NUL; `nil` is SQL NULL and an empty array is an empty
  value. There is no SQL interpolation. Limits are enforced in the wrapper:
  more than 32 parameters (`PQ_MAX_PARAMS`), more than 1 MiB of SQL
  (`PQ_MAX_SQL`), a DSN over 16 MiB (`PQ_MAX_DSN`) or a value over 16 MiB
  (`PQ_MAX_VALUE`) returns a specific `err` before any C call.
- `connect_timeout`: `toolkit/libpq_conninfo.c` asks libpq's own
  `PQconninfoParse` whether the keyword is present, and the wrapper appends
  `connect_timeout=5` when it is absent (URI and keyword forms). An explicit
  `connect_timeout=0` counts as present, so it disables libpq's timeout and gets
  no default; negative values are passed unchanged. An invalid conninfo returns
  the helper's copied diagnostic as `err`.
- Result values and error messages are copied by length; `PQgetlength` is used,
  never `strlen`. Parameter values are different: libpq reads a text-format
  `paramValues[]` entry as a C string, so the wrapper passes a `len+1` copy
  truncated back to `len` (the terminator stays one byte past the data) for
  text, while binary values keep their explicit length and may contain NUL.
  Exact numeric text, OID and typmod are preserved; no numeric value is routed
  through a float. `Rows.fmt` is 0 for a text result and 1 for
  `exec_params_binary`.
- A dropped `Rows` leaves its owned `Res` in the census (exit 8 under
  `OBO_CHECK=1`, with no `PQclear`), even after the `Connection` closes.
- `examples/libpq_defer_app.obx` no longer imports `buffer.obx`: the buffer
  package has its own adapter and is not part of the libpq spec.
- **Temporary retention:** `toolkit/libpq_adapter.c` and `.h` (and the `cres`
  toolkit) are still in the tree because `db/postgres` uses them until stage
  (g). The libpq binding no longer lists them in its spec or wrapper sources;
  stage (g) deletes the adapter after porting postgres.

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
with status 2 and `empty_closed=true`. It does not connect to a database, send
SQL, start a server, open a socket or use a service.

### Signature checks for the libpq binding

`libpq.cbind` and `libpq_fake.cbind` list `toolkit/libpq_decls.h` and
`toolkit/libpq_conninfo.h` as headers and use `signature_check: header`. Every
binding declaration and the release prototypes are checked against those
headers (`PQfinish`/`PQclear` as `void (*)(CType *)`), and the generated foreign
table compiles with `-Werror`. libpq's own prototypes come from the hand-written
`toolkit/libpq_decls.h`, because the libpq development header is not installed on
the probe host; the check is only as strong as that trusted declarations file.

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
| `null` | none | the spec's `ctypes` type, passed as NULL |
| `const:<n>` | none | the spec's `ctypes` type, passed as the value `n` (e.g. `const:-1`) |

Rules:

- `null` and `const:<n>` have no obo argument, so their C type is not implied by
  the kind. The spec gives it in `ctypes`, one entry per argument (a C type for each
  `null`/`const`, `null` elsewhere), for example `"ctypes": ["const char *"]` for a
  NULL `const char *`, `"void (*)(void *)"` for `const:-1` as a destructor, and
  `"int"` for `const:5`. The signature check then compares that type with the
  header exactly, like every other slot.
- A `ctypes` entry is exactly one C type: an integer type from the fixed list, a
  pointer `[const] T *...`, or a function pointer `R (*)(P, ...)` whose parameters
  are pointer or integer types. A comma is allowed only inside the parameter list, so
  `"void *, void *"` is refused, as are unbalanced parentheses, a declarator name
  (`"void *p"`), two types, and a struct by value (the thunk passes every slot in a
  register). A `ctypes` key on an int-form binding (all `i64`) is refused.
- A `const:<n>` value must fit its integer type by width and sign: `unsigned int`
  takes 0 to 4294967295, `int` takes -2147483648 to 2147483647, and a negative value
  is refused for an unsigned type. A pointer or function-pointer type takes any 64-bit
  value, such as `const:-1` for `SQLITE_TRANSIENT`. The magnitude is at most 17 digits.
- Other slots keep their fixed types. A mismatched pointer slot is refused by the C
  compiler's signature check, not by the obx compiler: the generated sigcheck
  assignment is an incompatible-pointer error, and the build fails before link.
- `ints@k:len` must name a list kind (`ints` or `nil_bytes_list`).
- Element counts are capped only at `INT_MAX`, which is what the C length parameter
  can hold. A per-library cap, such as libpq's 32 parameters, belongs to the wrapper
  (design R10): the wrapper checks it and returns an error before the call.
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
  On an exit-11 path the process ends without freeing scratch: the OS reclaims it.
- The sidecar records the descriptor in its argtypes field, and the tool checks it
  against the spec (arity and each kind) before the link. A kinds binding needs a
  maintained `wrapper`; generated wrappers stay int-only.
- Limits: at most 12 kinds per binding and 8 for the int form; an argument index
  `@k` is below 16 (the compiler and the tool both refuse more); a kind takes one
  `@k` argument (`null` and `const:<n>` take none); results are still `i32`/`i64`.

The helpers the thunks call are in `host.c` (`obo_ci_*`), hosted builds only. They
have hidden visibility, so they stay out of the dynamic symbol table and a shared
library cannot bind to them. That prevents dynamic-symbol clashes only: a second
static object in the same executable that defines an `obo_ci_*` name is a
duplicate-symbol link error.

## Return kinds and typed native declarations (stages (b) and (c))

### Spec format v2

`cinterop-proto/2` adds the return kinds below and makes the signature claim
explicit. A `/2` spec must carry `"signature_check": "header"` or
`"signature_check": "declarations-only"`:

- `header` requires `headers`, and the generated C function-pointer assignment
  is checked against those headers;
- `declarations-only` makes no header claim and requires explicit
  `declarations`.

`cinterop-proto/1` keeps today's behaviour exactly: its results are `i32`/`i64`
and its mode is derived from whether `headers` is present. A `/1` spec cannot
use the new result kinds.

| Return kind | Obo result | Meaning |
| --- | --- | --- |
| `void` | none | unit |
| `i32`, `i64` | `int` | sign-extended |
| `u32` | `int` | zero-extended (`Oid`, status masks) |
| `nonzero` | `int` | 1 when the pointer is non-NULL, 0 otherwise; the pointer is discarded |
| `cbytes` | `?[]byte` | copy of `(value, length)`, value first; `nil` if over the cap |
| `cstr_out` | `[]byte` | copy up to the first NUL, bounded at 16 MiB; NULL is empty |

Pointer-returning kinds (`cbytes`, `cstr_out`, `nonzero`) require
`"ret_ctype"`, and the signature check compares that exact C type with the
header. `ret_ctype` must spell an **object pointer type with `*`**, using the
`[const] T *...` grammar (`const char *`, `void *`, `struct foo *`). A typedef
that hides the pointer (for example a typedef whose type is a function
pointer), a pointer to a function pointer (`void (**)(void *)`), a pointer to
an array (`int (*)[4]`) and a function-pointer return type are refused.
`void`/`i32`/`u32`/`i64` use their fixed C types. A kind that is not
implemented yet (`own`, `own_opt`, `borrow`) is refused as a stage (d) kind.

### `cbytes`

A `cbytes` function names its value function as `symbol` and its length function
as `"length"`, and gives a positive `"cap"`:

```json
{"name": "getvalue", "symbol": "PQgetvalue", "ret": "cbytes",
 "length": "PQgetlength", "cap": 16777216, "ret_ctype": "const char *",
 "args": ["borrow_bytes@0", "i32@1", "i32@2"]}
```

The thunk calls the value function first and keeps the pointer in its frame,
then calls the length function with the same expanded C argument words, then
copies. No obo code runs between the two native calls. Both calls reset `rsp`
to the same fixed frame, so 16-byte alignment and stack-passed arguments (more
than six C parameters) are the same for both. The copy allocates a normal obo
`[]byte` through the runtime jump table, so `OBO_CHECK=1` leak checking applies:

- a negative length, or NULL with a positive length, exits 11;
- a length above `cap` returns `nil`, and a wrapper returns `err`;
- length 0 returns an empty array, even for NULL;
- the length is authoritative: the helper never calls `strlen`, and NULs are
  preserved.

### `cstr_out`

The value function returns a C string. NULL gives an empty array. Otherwise the
helper copies up to the first NUL; a value with no NUL within 16 MiB exits 11
rather than reading unbounded memory. No trailing bytes are copied.

### `void` and `nonzero`

`void` calls the function and returns no value (the thunk returns 0, which the
call site ignores). `nonzero` turns the pointer into 1/0 for probes such as
`sqlite3_next_stmt(db, NULL) != NULL`.

### Body-less `native fn`

```obo
native fn sum3(v: &[]int, bias: int): int
native fn error_message(code: int): []byte
native fn version(): int
native fn finish(rc: int)          // no return annotation: a unit result
```

A foreign-handle parameter such as `fn status(c: &Conn): int` is **stage (d)**;
stage (c) accepts only the types below.

`native` is contextual: only a top-level `native fn` is a declaration, so
programs may still use `native` as an identifier. The declaration name is the
spec `name`; the tool maps it to the spec `symbol` and rewrites the relocation
before linking. Supported parameter types are `int`, `string`, `&[]byte`,
`&[]int` and `&[]?[]byte`; supported returns are no return annotation (unit),
`int`, `[]byte` and `?[]byte`. Anything else is a compile error. A typed
binding needs a maintained `"wrapper"`; generated wrappers remain int-only.

Rejected with a clear error: a body on a native declaration, a receiver
(`native fn (r: &T) ...`), `pub native fn` (export a normal wrapper instead),
`native` `main`, a duplicate native declaration, and a native declaration later
defined as a normal function or vice versa.

At a call the compiler emits the same machinery as `__cinterop`, but records an
obo-side **shape** per argument rather than an exact kind:

| Declared parameter | Shape | Words pushed |
| --- | --- | --- |
| `int` | `int` | 1 |
| `string`, `&[]byte` | `bytes` | value, string flag |
| `&[]int` | `ints` | 1 |
| `&[]?[]byte` | `byteslist` | 1 |

The spec decides the exact kind: `int` accepts `i64`/`i32`/`u32`; `bytes`
accepts `borrow_bytes`/`borrow_cstr`; `ints` accepts `ints:i32`/`ints:u32`;
`byteslist` accepts `nil_bytes_list`. C-only spec slots (`null`, `const:<n>`,
and `ints@k:len` expanding a list argument) are added by the thunk; the compiler
never sees them. The return shape is `i64` (for `i32`/`u32`/`i64`/`nonzero`),
`void`, `bytes` (`cstr_out`) or `optbytes` (`cbytes`).

`--check` parses and type-checks native declarations, but refuses a native call:
`--check` has no typed sidecar and no link step, the same policy as
`__cinterop`. Programs that contain no native call are byte-identical to the
previous compiler.

### Sidecar v2

The compiler writes `cinterop-relocs 2`. Each line is:

```
<off> <name> <nargs> <mode> <shapes> <ret-shape> <effects>
```

- `name` is the C symbol for a `call`, or the obo declaration name for `fn`;
- `nargs` is the descriptor-entry count for `call` (the int form counts its
  arguments) and the number of obo arguments for `fn`;
- `mode` is `call` (`__cinterop`) or `fn` (a typed native fn call);
- `shapes` is `-` for the int form, an exact kind list (`i32@0`, `null`, ...)
  for a kinds `call`, or a shape list (`int`, `bytes`, ...) for `fn`;
- `ret-shape` is `i64`, `void`, `bytes` or `optbytes`;
- effects stay `freesall,writeshandles`.

The tool reads v1 sidecars too. It rejects a v2 line with an unknown mode,
shape, or result shape, and checks shapes against the spec before the link.
A spec function that no call reaches is still refused (`no native call for spec
symbol(s)`), as before.

## Foreign handles (stage (d))

```obo
pub type Conn = foreign "PGconn"

native fn connectdb(conninfo: &[]byte): ?Conn     // spec ret own_opt<PGconn>
native fn status(c: &Conn): int                    // spec arg borrow<PGconn>@0

pub fn (mut c: &Conn) close(): Result[bool] { return __foreignClose(c) }
pub fn (c: &Conn) is_open(): bool { return __foreignIsOpen(c) }
```

`foreign` is a reserved word. `type X = foreign "C"` declares an owned reference
type: a normal record with one private word at offset 0, in neither the owned nor
the string offset list of its descriptor, so `rt_free` frees the record and never
reads the C pointer. `open`/`new X` cannot construct it, `x.word` is always
refused (even in the declaring module), and `int(x)`/`string(x)` conversions are
refused. A value exists only from a native `own`/`own_opt` result (or an own
result of a typed native fn). `pub type X = foreign` exports as an ordinary
opaque record with one `priv` placeholder field in the `.sym`; no format bump and
no C name is needed by importers. The wrapper's exported `close`/`is_open`
methods are ordinary methods. Limitation: an importer sees an opaque record, so
it can only close/open it through the wrapper's exported methods; without them
the type has no usable lifetime operation. `--check` can consume a wrapper's
`.sym` alone; a full build still needs the wrapper source (the compiler resolves
imports from source unless checking).

**Native kinds.** A `&X` parameter is the shape `foreign:X` and one of two spec
kinds: `borrow<C>@k` is read-only and has the C parameter `const C *`;
`borrow_mut<C>@k` has `C *`. Both push the same record pointer and the thunk
calls `obo_ci_foreign_word`, which exits 7 with
`obo: foreign handle used after close` for a nil record or a zero word; constness
is only the C type check. A return `X` is `own:X` / spec `own<C>`; `?X` is
`own_opt:X` / `own_opt<C>`. The thunk returns the raw C word, then the compiler
calls `obo_ci_foreign_own(id, raw, desc, optional)`: `own` with NULL exits 11,
`own_opt` with NULL gives nil, otherwise it allocates a normal record of `desc`,
stores the word and increments the per-type census. The spec `types` entry gives
the C type name and the release:

```json
"types": [{"name": "PGconn", "obo": "Conn", "release": "PQfinish", "release_result": "void"}]
```

`release_result` is `"void"` or `"int"`. The release is signature-checked against
its `CType *` prototype. `out_own` and `out_offset` are **deferred to stage (f)**
(SQLite); stage (d) needs only own/own_opt/borrow/close for libpq.

**close and is_open.** `close` is `close(mut &X): Result[bool]`:

- word 0 gives `ok(false)` and makes no C call (double close);
- otherwise it copies the word, zeroes it, decrements the census, calls the
  release, and returns `ok(true)`;
- a `release_result: "int"` release with a non-zero result gives
  `err("foreign release failed", code)`, and the word stays zero.

`is_open(&X): bool` reads the word. In this implementation wrappers declare both
methods over two internal builtins, `__foreignClose(x)` and
`__foreignIsOpen(x)`, instead of the compiler generating method bodies (a
documented pragmatic deviation; the public API and semantics are the same).
`__foreignClose` needs a writable place (a `var`, a `mut &X`, a field through a
mut receiver, an array element, or a `^TX` handle) and is not given the
conservative C summary, so closing a field receiver is allowed; the release is
trusted not to free obo memory or call back. `__foreignIsOpen` is an inline word
load, so it does not invalidate references. `x.close()` and `x.is_open()` on a
foreign receiver with no explicit method are sugar for the same builtins.

**Census.** The generated `foreign_table.c` supplies the release wrappers, the
obo type names and `release_result`, in sidecar order. `host.c` keeps one live
count per type. Under `OBO_CHECK=1`, `rt_finish` calls the hosted-only foreign
census between the fd census and the object census: every non-zero type is
reported as `obo: foreign handles leaked: T n` and the program exits 8. Dropping
an open handle, overwriting it, or dropping an enclosing record releases nothing
and leaves the count up. `exit(n)` and traps run no census. A decrement at zero
exits 4 (`obo: internal error: foreign handle census underflow`);
`OBO_CENSUS_FAULT=underflow` forces one underflow and is inert unless
`OBO_CHECK=1`.

**Sidecar.** Sidecar v2 starts with `foreign <id> <obo-name> <c-name>` lines in
first-declaration order, then the relocations. Stage (d) adds these lines under
the existing `cinterop-relocs 2` header without a version bump; a reader that
expects only the 7-field relocation lines rejects them as malformed (the v2
reader here is the updated one). A typed foreign call records
shapes `foreign:X`, `own:X` or `own_opt:X`; the tool maps the shape's obo name
through `types` to the C name. The tool accepts mode `host` records for the
internal helpers with fixed arity (`obo_ci_foreign_own` 4,
`obo_ci_foreign_close` 5, `obo_ci_foreign_census_adjust` 2) and generates their
thunks, but the compiler only emits `own` and `close`; `is_open` is an inline
word load and the census adjust is internal to the own/close helpers. A sidecar
`foreign` line must match a spec `types` entry.

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
the scratch counter, and refused spec and descriptor mismatches before link), the
`null`/`const:<n>` slots typed by `ctypes` (a NULL `const char *`, a NULL struct
pointer, a `const:-1` function-pointer destructor, `const:5` for an `int`, and
refusals for a struct swap, a pointer mismatch, a missing or by-value type), and
seven to eleven C parameters with odd and even stack-argument counts, each checked
for a 16-byte aligned callee frame and for its values). It also covers the
stage (b)/(c) surface (`returns()`): `cbytes` empty/1-byte/NUL-containing/exact
length/over-cap/`nil`-then-`err`, NULL-with-positive-length and negative length
exit 11, both `cbytes` calls with even and odd stack-argument counts (8 and 9 C
parameters), `cstr_out` normal/empty/NULL/no trailing text and the 16 MiB bound,
an all-`i64` spec with each typed return, `void`, `nonzero`, typed declarations
with C-only `null`/`const`/`ints:len` slots (including C-only slots before an
obo argument and `ints:len` before its list), a v1 sidecar reader, sidecar
shape/mode/return/nargs tampering, a v2 fn against a v1 spec, a v2 call against
a typed return, spec v2 `signature_check` (header and declarations-only), a
function-pointer `ret_ctype`, and `cbytes` key/length-function refusals,
declaration/spec shape mismatches, the native declaration negatives, the
`native` identifier programs, and the `--check` refusal. The stage (d) surface
(`foreigns()`) covers own/own_opt/borrow/borrow_mut (including both constness
directions), close through a local, field, `mut` receiver, `^T` handle and array
element, deferred close, double close, exactly one
release, `is_open` and `alive`, a `release_result: "int"` error, use-after-close
exit 7 with no C call, the leak census (scope end, overwrite, enclosing record;
exit 8 and no release; inert without `OBO_CHECK=1`), no census on `exit`/trap,
census underflow exit 4 and `OBO_CENSUS_FAULT`, the compile negatives
(`new`/field/conversion/owned parameter), a mismatched foreign borrow, sidecar
foreign/host tampering, an app importing a wrapper with `pub type X = foreign`,
`--check` over that wrapper's `.sym` alone, a wrapper with no explicit
close/is_open (the sugar path), the temporary and `let` receiver rules, and an
ASAN/UBSAN run over the C fixture. A libpq build failure is a test failure
when `libpq.so.5` is present; the test skips only when the library is absent.

It also builds and runs:

- the real `libpq` `PQlibVersion` + `PQmakeEmptyPGresult`/`PQclear` probe;
- the deterministic fake-libpq C suite (`fake_adapter_test.c`) for the retained
  adapter: token poisoning, double close, `PQfinish`/`PQclear` counts, NULL vs
  empty, binary NUL, allocation failure, malformed packets,
  result-outlives-connection, and injection-as-data;
- the v2 libpq wrapper through the fake ABI: foreign release counts via
  `FAKE_REPORT=1`, NULL vs empty, binary NUL, double close `ok(false)`,
  use-after-close exit 7, result-outlives-connection, and a deliberate
  missing-close app whose `Connection` census exits 8;
- invalid conninfo (`host='unterminated`) returns libpq's copied diagnostic with
  code 2 and registers no connection; the fake parser reports the same text, and
  the real libpq parser runs too when `libpq.so.5` is present. No server is used;
- limits with the fake ABI: 33 parameters, 1 MiB + 1 bytes of SQL, a 16 MiB + 1
  value and a 16 MiB + 1 DSN are rejected with specific messages, and 32
  parameters, exactly 1 MiB of SQL and a 16 MiB value are accepted;
- the v2 wrapper's own messages: `PQconnectdb` returning NULL and an empty
  `PQexecParams` failure each return a specific `err`, and exec on a closed
  connection is the stage (d) exit 7 (not the old adapter's code -1);
- the wrapper limit constants (`PQ_MAX_PARAMS`, `PQ_MAX_SQL`, `PQ_MAX_DSN`,
  `PQ_MAX_VALUE`) are read from `libpq.obx` and checked against the reviewed
  values; there are no adapter C mirrors any more;
- a libpq spec declaration that disagrees with `libpq_decls.h` (an internally
  consistent `long` for `PQlibVersion`, whose header return is `int`) fails the
  signature check before link;
- explicit-defer success/early-return/nested/loop paths and a
  result-outlives-connection app;
- a dropped `Rows` leaves its owned `Res` in the census (exit 8, `clear=0`);
- NULL versus empty, a SQL-NULL `value` error, an over-cap `cbytes` result,
  `Rows.fmt` 0/1, bounds sentinels/errors on every accessor, and closed-Rows
  accessors;
- NUL rejection for the DSN, SQL and text values (binary NUL is preserved), an
  explicit `connect_timeout=3` is not duplicated, and the default
  `connect_timeout=5` is appended;
- the conninfo helper against real libpq (`PQconninfoParse`/`PQconninfoFree`,
  quoted values are not keywords) and the compile-time behaviour;
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
- ASAN/UBSAN runs of the fake adapter, zlib adapter, guarded-stack fixture and
  the `libpq_conninfo.c` helper;
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
- There is no automatic foreign finalizer. `PGconn`/`PGresult` are owned foreign
  handles; dropping a `Connection`/`Rows` never calls `PQfinish`/`PQclear` and
  is reported by the `OBO_CHECK=1` census. The binding is not a full database
  driver or a stable public API.
- The retained `cres` token registry (zlib, buffer, templates, and db/postgres
  until stage (g)) protects against accidental stale/wrong-kind/forged integer
  use, not against trusted C or an adversarial caller. Its generation is 31
  bits. Obo token fields are private to their wrapper modules; that reduces
  accidental construction, not deliberate forgery.
- Source floats/decimals are not part of the native contract. The libpq
  binding preserves exact numeric text/OID/typmod and never converts a database
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
