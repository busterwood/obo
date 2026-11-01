---
name: obo-c-interop
description: Use when adapting an existing C library to obo through the experimental cinterop toolkit, especially when writing a spec/types/release table, an ordinary obo wrapper package, a .cbind contract, foreign handles with explicit close, generated out (`_out`) records, copy-out bytes/strings, or the matching fake-API, census and live-library tests. Trigger on requests to bind libpq, another system C library, opaque handles, resource registries, or C byte/error APIs from obo, even if the user only says "use this library from obo" or "write a wrapper".
---

# Adapting a C library with the obo cinterop toolkit

This skill is a portable project skill under
`obo/experimental/cinterop/skill/`. It is not installed globally. The experiment
is still **experimental**: no stable native ABI, no automatic foreign finalizer,
and no universal configurable binding generator.

Read these before changing code:

- `obo/design/cinterop-boundary-design.md` — the approved v2 design. §3 is the
  kind table and foreign handles, §5 the libpq surface, §6 SQLite, §7 the
  decisions, §8 the stages and §9 the test plan.
- `obo/design/cinterop-v2-handoff.md` — where the work stands, the gates, the
  decisions that are final, and how the work is run.
- `obo/experimental/cinterop/README.md` and `toolkit/*.h`.
- The real examples: `examples/libpq.cbind`, `examples/libpq.obx`,
  `tests/fake_libpq.c`, `tests/fake_libpq_probe.h`, and the worked packages in
  `obo/experimental/db/{postgres,sqlite}`.
- Historical only: `obo/design/native-c-interop-design.md` describes the pre-v2
  scalar/token adapter. It is superseded for libpq and SQLite; do not copy its
  `cres` adapter shape into a new binding.

## The boundary you must preserve

1. **C stays trusted.** The toolkit checks artifacts, signatures, arity, effects
   and ABI shape. It does not sandbox C, prove memory safety, or turn a header
   annotation into a security proof.
2. **No raw C pointer reaches obo as an integer.** In v2 the real pointer lives
   in a `foreign` record's private word; obo cannot construct, copy, convert or
   do arithmetic on it. A legacy token adapter keeps the pointer in C and
   exposes only scalar entry points.
3. **Foreign handles are not tokens.** `own`/`own_opt` return a `foreign` value,
   `borrow<C>`/`borrow_mut<C>` pass it, and `is_open` reads the word. A direct
   borrow after close exits 7. The retained `toolkit/cres.h` token registry is
   only for the zlib adapter and the buffer example.
4. **Explicit close is the contract, normally through `defer`.** Ordinary obo
   record drop frees only the record; it does **not** call `PQfinish`,
   `PQclear`, `sqlite3_finalize` or any foreign destructor. A v2 wrapper's
   `close` zeroes the handle word and releases once; a second close is a safe
   `false`/`ok(false)`. Register the defer promptly after creation; block exit
   (normal, early return, error arm, loop `break`/`continue`) runs it. Obo
   `exit(...)` runs no defers and no census, so return from the block when
   cleanup must run. If a user asks for automatic cleanup, explain that it
   needs a new, reviewed finalizer design; do not smuggle it into a record
   drop.
5. **Bytes cross by length, never by `strlen`.** A borrowed C byte range is
   copied into a bounded C buffer (or passed through a narrow trusted copy
   helper), then read by the wrapper with the reported length. Embedded NUL is
   preserved in binary values. Text/SQL/C-string inputs reject embedded NUL
   explicitly.
6. **NULL is not empty.** Carry an explicit null flag/kind; do not infer SQL
   NULL from a zero length or a pointer value.
7. **No SQL interpolation.** Build a fixed SQL statement with `$1..$n`
   placeholders. Send text/binary parameter values with their lengths and
   formats. String concatenation into SQL is a bug even when it "looks safe".
8. **No float conversion for numeric database text.** Preserve exact numeric
   text plus OID/typmod metadata. Do not route `numeric`, `money` or decimal
   text through a binary float. Source-level floats are not supported by the
   native contract in this checkpoint.
9. **No unsupported C surface.** Do not add callbacks, C variadics,
   struct-by-value, TLS, IFUNC, `long double`/float arguments, or decimal ABI
   to this seam. Stop and report a concrete blocker rather than inventing an
   unsafe ABI.
10. **The census replaces live counters.** Under `OBO_CHECK=1` a leaked foreign
    handle is reported at normal end with exit 8; a census decrement at zero
    exits 4. There is no `live_resources`/`live_connections` counter in a v2
    package.

## Kinds and the spec table

A v2 spec names each C parameter by a kind. `@k` is the obo argument index
(counted from 0); a kind with no `@k` (`null`, `const:<n>`, `out_own`) consumes
no call argument.

| Direction | Kind | Obo value | C parameter |
| --- | --- | --- | --- |
| arg | `i64@k` | `int` | `long` |
| arg | `i32@k` | `int`, range-checked (exit 11) | `int` |
| arg | `u32@k` | `int`, range-checked | `unsigned int` |
| arg | `borrow_bytes@k` | `[]byte`/`string` | `const void *`, `int` |
| arg | `borrow_cstr@k` | `[]byte`/`string`, NUL-checked | `const char *` (scratch copy) |
| arg | `ints@k:i32`, `ints@k:u32` | `[]int`, elements range-checked | `const int *` / `const unsigned int *` |
| arg | `ints@k:len` | the count of list `k` | `int` |
| arg | `nil_bytes_list@k` | `[]?[]byte` | `const char *const *`, `const int *` |
| arg | `null` | none | the spec `ctypes` type, passed NULL |
| arg | `const:<n>` | none | the spec `ctypes` type, value `n` |
| arg | `borrow<C>@k` | `&X` | `const C *` |
| arg | `borrow_mut<C>@k` | `&X` | `C *` |
| arg | `out_own<C>` | `out name: X` | `C **` |
| arg | `out_offset@k` | `out name: int from input` | `const char **` (relative to input `k`) |
| return | `void` | none | `void` |
| return | `i32`, `i64` | `int` | `int` / `long` |
| return | `u32` | `int` | `unsigned int` |
| return | `nonzero` | `int` | pointer turned into 1/0 |
| return | `cbytes` | `?[]byte` | `ret_ctype`, `length` function, `cap` |
| return | `cstr_out` | `[]byte` | `ret_ctype`, up to the first NUL |
| return | `own<C>` | `X` | `C *` (NULL exits 11) |
| return | `own_opt<C>` | `?X` | `C *` (NULL is nil) |

Rules that are easy to get wrong:

- A `null` or `const:<n>` slot has no obo argument, so its C type comes from
  `ctypes`, one entry per argument. A function-pointer `const` slot accepts only
  `const:-1` (the no-callback destructor, e.g. `SQLITE_TRANSIENT`).
- `cbytes`/`cstr_out`/`nonzero` need `ret_ctype`. `cbytes` also needs
  `length` (same C parameters, `int` return) and a positive `cap`.
- `out_own` and `out_offset` consume no call argument and appear **in C order**,
  interleaved with inputs when the C prototype does. For example,
  `sqlite3_open_v2(path, &db, flags, NULL)` is `borrow_cstr@0`,
  `out_own<sqlite3>`, `i32@1`, `null`. At most one `out_own` and one
  `out_offset` are allowed, and the native return type is the literal
  `<native>_out`.
- A `types` entry gives the C type and the release:
  `{"name": "PGconn", "obo": "Connection", "release": "PQfinish",
  "release_result": "void"}`.
- `own` and `own_opt` differ on NULL. `own<C>` treats NULL as a native contract
  violation (exit 11); `own_opt<C>` gives nil so the wrapper can return `err`.
  Use `own` only where the C API's NULL is truly unreachable, and `own_opt`
  where an allocation failure is a normal error (the template uses `own_opt`).

## Workflow

### 1. Inspect the C API and write the spec/types/release table first

From the installed header or the official documentation, record for every
function you will expose:

- C symbol and exact prototype; whether a real header was actually available;
- ownership: who allocates, who releases, and which object must outlive which;
- release function and its result (`void`, `int` or `discard`);
- error convention: return status, out parameter, or `PQerrorMessage`/
  `sqlite3_errmsg`;
- lengths: where the length comes from, whether embedded NUL is meaningful;
- whether a value is an out parameter (`out_own`) or a pointer offset
  (`out_offset`);
- lifetime: which object must outlive which other object.

Never infer ownership from a pointer type or a header field. Write the
assumption down in the spec, wrapper and tests.

### 2. Write the `.cbind` — the trusted contract

The `.cbind` is the reviewable contract for the **binding** API, not a wide C
header parser. Start with the spec, then the type/release table, then the
functions; do not start by writing a token adapter.

```json
{
  "format": "cinterop-proto/2",
  "module": "demo",
  "platform": "x86_64-linux-gnu",
  "abi": "sysv-amd64",
  "signature_check": "header",
  "wrapper": "wrapper.obx",
  "sources": ["adapter.c"],
  "headers": ["adapter.h"],
  "declarations": [],
  "libs": [],
  "cflags": [],
  "types": [
    {"name": "demo_item", "obo": "Demo", "release": "demo_close",
     "release_result": "void"}
  ],
  "functions": [
    {"name": "demo_open", "symbol": "demo_open",
     "args": ["i64@0"], "ret": "own_opt<demo_item>", "effects": ["impure"]},
    {"name": "demo_read_byte", "symbol": "demo_read_byte",
     "args": ["borrow<demo_item>@0", "i64@1"], "ret": "i64",
     "effects": ["impure"]},
    {"name": "demo_make", "symbol": "demo_make",
     "args": ["i64@0", "out_own<demo_item>"], "ret": "i32",
     "effects": ["impure"]}
  ]
}
```

- `format: cinterop-proto/2` and an explicit `signature_check`. `header`
  requires `headers` and checks every declaration plus the release prototypes
  against them; `declarations-only` requires explicit `declarations` and makes
  no installed-header claim. Never pretend a declarations-only check used the
  installed header.
- `sources` for the adapter and any trusted helper C file; `libs` only for the
  real system library; a fake build links the fake source instead and lists no
  library.
- `wrapper_sources` lists the ordinary `.obx` modules the maintained wrapper
  imports when they are not `wrapper` itself (for example zlib's
  `buffer.obx`, sqlite's `sqldialect.obx`, and postgres's `sqldialect.obx`
  plus `libpq.obx`). The manifest hashes them, so a shared helper change is a
  provenance input.
- `effects` is `["impure"]` on every exported function. The compiler adds the
  conservative `freesall,writeshandles` summary and the tool checks it; a spec
  `pure` claim is not accepted, because a native call can touch anything C can.
- `include_dirs` for a private sysroot and `cflags` for real compile options.
  The signature check, every binding source compile **and** each dependency
  scan use the same `-fno-pie -O2`/include/cflag state. A relative include root
  must stay inside the spec directory or obo tree; write an absolute root only
  when it is an explicit build input, and do not commit machine-specific
  absolute paths.
- `wrapper` names the maintained obo wrapper. Every spec function must be
  reached by a native call; an unused symbol fails the build.
- `ret_ctype` must be the exact object-pointer spelling in the header
  (`const char *`, `void *`, `struct foo *`). `own`/`own_opt` use
  `<C> *`, where `<C>` is the `types` name.

### 3. Write the ordinary obo package

Write a maintained `module` source (not a generated one). `examples/libpq.obx`
is the reference.

```obo
pub type Demo = foreign "demo_item"

native fn demo_open(size: int): ?Demo               // spec ret own_opt<demo_item>
native fn demo_read_byte(item: &Demo, index: int): int
native fn demo_make(size: int, out item: Demo): demo_make_out

pub fn (mut d: &Demo) close(): Result[bool] { return __foreignClose(d) }
pub fn (d: &Demo) is_open(): bool { return __foreignIsOpen(d) }

pub fn open(size: int): Result[Demo] {
    if size < 0 || size > 1048576 { return err("size is out of range", 2) }
    let c = demo_open(size)            // own_opt: NULL is nil, not exit 11
    if c == nil { return err("demo_item allocation failed", 3) }
    var d = c
    return ok(d)
}

pub fn open_out(size: int): Result[Demo] {
    var out = demo_make(size)          // an out-call must be stored in a var
    var d = take(out.item)             // out_own field is ?Demo
    if d == nil { return err("no handle", 3) }
    if out.rc != 0 {
        d.close()                      // close on the error path too
        return err("open failed", out.rc)
    }
    return ok(d)
}
```

- `foreign` is reserved. `pub type X = foreign "C"` has one private word;
  `open`/`new X` and conversions are refused. A value exists only from an
  `own`/`own_opt` result or an out record.
- For a field handle, pass the field (`&r.h`) to a **native borrow** directly;
  passing it to another obo function is refused by the deep-reference rule.
- The out record is an ordinary owned value: `rc: int` first, then one field
  per out parameter (`?Demo` for `out_own`). A bare out-call statement is
  refused, because it would silently drop a live handle. `take` moves the
  handle out; close it once on the error path (the record's drop frees the obo
  record, not the C object, and the census reports a missed close). A failing
  open can write a non-NULL handle (as `sqlite3_open_v2` does), so `rc != 0`
  does not imply nil.
- Accept `[]byte`, `[]?[]byte` and `[]bool` at the public edge and pass the
  arrays to the native call (`ints`/`nil_bytes_list`) directly; do not build a
  length-delimited packet. Send a typed OID array with the values when the API
  allows it.
- Provide `value`/`text` accessors that use the C length and reject embedded
  NUL only when converting to `string`.
- Under `OBO_CHECK=1` the foreign-handle census reports a dropped open handle
  (exit 8); test that instead of adding `live_*` counters.
- Use `defer` in the application wrapper for success, error/early-return,
  nested and loop paths. Do not claim automatic ownership.

#### Typed OIDs and text NUL

`examples/libpq.obx` (`exec_params_typed`/`exec_params_typed_binary`) and
`db/postgres/postgres.obx` (`encode_params`) show the parameter rules:

- Pass the value, binary-format and OID arrays directly to `PQexecParams`; there
  is no packet builder and no local C parser. The typed entry points check that
  the OID and value counts match and that each OID is in `0..0xffffffff`.
- A text-format parameter is a `len+1` copy truncated back to `len`: libpq
  reads text `paramValues[]` with `strlen` and ignores `paramLengths`, so the
  terminator must sit one byte past the data.
- A binary parameter keeps its exact length and may contain NUL. Document which
  values are text and which are binary (`Rows.fmt` is 0 for text, 1 for binary).
- Result values and error messages are copied by length (`PQgetlength`,
  `sqlite3_column_bytes`), never `strlen`. A SQL NULL is distinct from empty
  bytes.

#### `release_result`

`types.release_result` is `"void"`, `"int"` or `"discard"`:

- `void` calls a `void C*(C *)` release and gives `ok(true)`.
- `int` calls an `int C*(C *)` release; a non-zero result gives
  `err("foreign release failed", code)` and the word stays zero.
- `discard` calls an int-returning release and ignores the result. This is for
  APIs whose release returns a status that is not an error here:
  `db/sqlite/sqlite.cbind` uses it for `sqlite3_close_v2` and
  `sqlite3_finalize` (D14/D15).

The release is signature-checked against its `CType *` prototype: `int` for
`int`/`discard`, `void` for `void`.

### 4. Build and test in a private directory

```sh
python3 obo/experimental/cinterop/cinterop.py build \
  path/to/library.cbind path/to/app.obx \
  --out /tmp/opencode/<private-name> --obo obo/obo
python3 obo/experimental/cinterop/cinterop.py verify \
  /tmp/opencode/<private-name>/manifest.json
```

Test at these levels:

1. **Fake C API** (deterministic, no network): success, connection/result
   errors, NULL vs empty, embedded NUL binary, parameter injection-as-data,
   allocation failure, double close, stale token (legacy only), result
   outliving connection, and every cleanup path. Count real releases.
2. **Obo wrapper** against the fake: `defer` success, error/early-return,
   nested lifetimes, loop paths and a C buffer; exact numeric text and
   metadata; binary length/NUL; manifest verification; no real library
   dependency; result-outlives-connection.
3. **Real library, no server** where possible: `PQlibVersion` and
   `PQmakeEmptyPGresult(NULL, status)` + `PQclear` prove a real link and real
   result ownership without a connection. For a second library use an installed
   real header and system library, in memory where possible; zlib is the worked
   example.
4. **Hosted startup/shutdown** if the package uses the hosted entry: exercise
   even/odd argv/env, near-ARG_MAX, invalid guarded-stack size, low/high guard
   pages, deep-recursion guard fault, constructor/atexit/stdio flush, normal
   return, `exit(code)` and `OBO_CHECK` status preservation.
5. **Live local database** only with explicit user authorization and an explicit
   DSN. Prefer a least-privileged local role, Unix-socket peer auth, a bounded
   `connect_timeout`, read-only `SELECT`s, and session-scoped state. Never
   create roles/databases/schema, edit `pg_hba`, start services, expose ports,
   or read credential files as part of a test run. Use `timeout` around the
   whole app and close every resource.
6. **Private header package / hosted qualification**, if the library needs one.
   Do not install system packages or use root. Download the exact matching
   development package (for example `apt-get download
   libssl-dev=<installed libssl3t64 version>`), extract it with `dpkg-deb -x`
   into a private path, and pass that include root through `include_dirs`.
   Never commit the extracted headers. Accept already-installed headers only
   when their package version matches the installed runtime library; otherwise
   fetch the matching development package. Keep a content hash manifest for a
   private cache and re-check it on a cache hit. A build failure with the
   library present is a failure; skip only when the runtime library is
   genuinely absent. The hosted OpenSSL 3.0 fixture in
   `tests/openssl_qualify.c` is the worked example for constructors, atexit,
   C stdio, thread-local state, provider loading and `OPENSSL_cleanup`.

**The v2 checklist.** Every foreign handle package needs tests for:

- use after close: exit **7**, with no C call;
- double close: `ok(false)` (or `false` on a `bool` wrapper), release exactly
  once;
- a dropped open handle under `OBO_CHECK=1`: exit **8**, the release not
  called;
- census underflow: exit **4**, forced with `OBO_CENSUS_FAULT=underflow`
  (inert unless `OBO_CHECK=1`);
- no census on `exit(n)` or a trap;
- an out record on success, and on error with a non-NULL out handle that is
  closed exactly once (and a NULL out handle);
- `release_result: "int"` returning an error and leaving the word zero;
- `is_open` before and after close, and `defer` on success/error/loop paths;
- a field handle closed through a `mut` receiver, and a handle through `^T`.

### 5. Review what the manifest actually proves

The manifest hashes inputs and artifacts and checks platform/ABI/pointer width,
thunk set, signature mode, effects, resolved libraries, transitive header
dependencies and the RELRO/NOW/noexecstack link policy. Hashes are
provenance/corruption checks, not signatures. A same-signature sidecar, header
or library substitution remains trusted input. Say that plainly.

## Legacy: the `cres` token adapter (zlib/buffer only)

`toolkit/cres.c/.h` is the older integer-token registry. It remains only for
the zlib adapter and its C test, `examples/buffer.obx`, and the
`tests/cres_test.c` registry test. zlib is outside the v2 gates. Do **not**
start a new binding on `cres`; the database packages, the skill templates and
the sys/tls/http2 adapters do not use it.

If you are maintaining that legacy shape, keep the exported ABI scalar:

```c
long my_lib_open(long config_buf);
long my_lib_close(long handle);
long my_lib_read(long handle, long index);
```

- Register every foreign allocation with `cres_register` and a release
  function; `cres_peek(token, KIND)` before every dereference; return a
  negative adapter error, never a raw pointer.
- `cres_close_kind(token, KIND)` on every close path; a double close returns 0.
- Bound every length, index, row, column and count before use, and copy
  borrowed bytes with `cres_buf_from_bytes`/`cres_buf_data`.
- The token field is private to the wrapper module, but that is not a
  capability boundary: the registry, not field visibility, rejects a copied or
  stale token.

## Templates

- `templates/adapter.c`, `templates/adapter.h` — a v2 adapter: a real
  `demo_item *`, an out-record entry point and a release that frees once. No
  `cres`, no tokens.
- `templates/library.cbind` — a `cinterop-proto/2` contract with
  `signature_check: header`, the `types` release table and
  `own`/`borrow`/`out_own`.
- `templates/wrapper.obx` — a `foreign` type, body-less `native fn`
  declarations, exported `close`/`is_open`, `defer` and the out-record
  `take`/error-path close.
- `templates/app.obx`, `templates/README.md` — a buildable consumer and how the
  files fit together.
- `examples/libpq_defer_app.obx` and `examples/zlib_app.obx` — real worked
  examples of `defer` cleanup, binary buffers and a second library.
- `db-adapters.md` — database notes from `obo/experimental/db/{postgres,sqlite}`:
  typed OIDs, text NUL, `release_result: discard`, statement/connection close
  ordering, and the declarations-only SQLite policy.

Start from `templates/`, then `examples/libpq.*` and
`db/postgres/postgres.obx` for a complete working example, and remove the parts
your library does not need. Do not grow `cinterop.py` into a per-library option
matrix or a universal IDL generator.

## Stop conditions

Stop and report a concrete blocker if the library requires any of:

- callbacks, C variadic calls, struct-by-value, TLS/IFUNC, floats/decimals;
- a pointer or managed object crossing into obo as an integer;
- automatic foreign finalization on ordinary record drop;
- a new compiler-wide language feature or a version/format reservation;
- unbounded or non-length-delimited borrowed memory;
- a test that would modify server configuration or persistent data without
  separate explicit authorization.
