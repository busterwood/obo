---
name: obo-c-interop
description: Use when adapting an existing C library to obo through the experimental cinterop toolkit, especially when writing a C adapter, an ordinary obo wrapper package, a .cbind contract, explicit connection/result ownership, byte/string or parameterized-query bridging, or the matching fake-API and live-library tests. Trigger on requests to bind libpq, another system C library, opaque handles, resource registries, or C byte/error APIs from obo, even if the user only says "use this library from obo" or "write a wrapper".
---

# Adapting a C library with the obo cinterop toolkit

This skill is a portable project skill under
`obo/experimental/cinterop/skill/`. It is not installed globally. The experiment
is still **experimental**: no stable native ABI, no automatic foreign finalizer,
and no universal configurable binding generator.

Read these before changing code:

- `obo/design/native-c-interop-design.md`, especially §1.1 (toolkit + agent
  skill, not a generator), §8 (ownership/effects) and §14 (what is built).
- `obo/experimental/cinterop/README.md` and `toolkit/*.h`.
- The real examples: `examples/libpq.cbind`, `examples/libpq.obx`,
  `toolkit/libpq_adapter.c`, `tests/fake_libpq.c`,
  `tests/fake_adapter_test.c`.

## The boundary you must preserve

1. **C stays trusted.** The toolkit checks artifacts, signatures, arity, effects
   and ABI shape. It does not sandbox C, prove memory safety, or turn a header
   annotation into a security proof.
2. **No raw C pointer reaches obo.** A C adapter exports only `long`-shaped
   scalar entry points. It keeps `PGconn*`, `PGresult*`, `void*`, `char*` and
   all pointer arithmetic on the C side.
3. **Resources are tokens, not pointers.** Use the toolkit registry
   (`toolkit/cres.h`): a token combines a table slot, a kind and a generation.
   Validate kind and generation on every call; poison the generation on close;
   free the underlying object exactly once. A token protects against accidental
   stale/wrong-kind use, not against trusted C or an adversarial caller.
4. **Explicit close is the contract, normally through `defer`.** Ordinary obo
   record drop frees only the record; it does **not** call `PQfinish`,
   `PQclear`, `free` or any foreign destructor. Make `close` borrow the wrapper
   (`&Connection`, `&Rows`, `&Buffer`) so `defer c.close()` is legal, poisons the
   token, and is a safe false on a second close. Register the defer promptly
   after creation; block exit (normal, early return, error arm, loop
   break/continue) runs it. Obo `exit(...)` runs no defers, so return from the
   block when cleanup must run. If a user asks for automatic cleanup, explain
   that it needs a new, reviewed finalizer design; do not smuggle it into a
   record drop.
5. **Bytes cross by length, never by `strlen`.** A borrowed C byte range is
   copied into a bounded C buffer (or through a narrow trusted copy helper),
   then read by the wrapper with the reported length. Embedded NUL is preserved
   in binary values. Text/SQL/C-string inputs reject embedded NUL explicitly.
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

## Workflow

### 1. Inspect the C API and write down the contract

From the installed header or the official documentation, record for every
function you will expose:

- C symbol and exact prototype; whether a header was actually available;
- argument widths and return width (this toolkit currently supports `i64`
  arguments and `i32`/`i64` results only);
- ownership: who allocates, who frees, and when a borrowed value becomes invalid;
- error convention: return status, out-parameter, or `PQerrorMessage`;
- lengths: where the length comes from, whether embedded NUL is meaningful;
- lifetime: which object must outlive which other object.

Never infer ownership from a pointer type or a header field. Write the
assumption down in the wrapper and tests.

### 2. Write the C adapter first

Start from `toolkit/libpq_adapter.c` / `toolkit/libpq_adapter.h` and
`toolkit/cres.h`. Keep the exported ABI scalar:

```c
long my_lib_open(long config_buf);
long my_lib_close(long handle);
long my_lib_read(long handle, long index);
```

- Register every foreign allocation with `cres_register` and a release function.
- `cres_peek(token, KIND)` before every dereference; return a negative adapter
  error, never a raw pointer.
- `cres_close_kind(token, KIND)` for every close path; double close returns 0.
- Bound every length, index, row, column and count before use.
- Copy borrowed bytes with `cres_buf_from_bytes` / `cres_buf_data`.
- On allocation failure, free everything allocated so far and return a
  distinguishable error; do not leave a live token.
- Use `PQgetlength`, not `strlen`, for `PGresult` values.
- Keep `PGresult` independent of `PGconn`; do not assume closing the connection
  invalidates an existing result.

### 3. Write the ordinary obo package

Write a maintained `module` source (not a generated one) that imports nothing
special and calls the internal `__cinterop` builtin only from its own private
helpers. `examples/libpq.obx` is the reference.

```obo
pub type Connection = ref record { tok: int }

pub fn (c: &Connection) close(): bool {
    return __cinterop("my_lib_close", c.tok) == 1
}

fn use(c: &Connection) {
    defer c.close()          // borrow, not a move
    // ...
}
```

- The record field is an integer token. Obo record fields are **not private**;
  say so honestly. Forged or stale integers are rejected by the C registry
  before dereference.
- Accept `[]byte`, `[]?[]byte` and `[]bool` at the public edge; build the small
  fixed packet in obo, then copy it to a C buffer once.
- Provide `value`/`text` accessors that use the C length and reject embedded NUL
  only when converting to `string`.
- Provide `live_resources()`/`live_connections()`/`live_results()` counters and
  a test that fails when a close is omitted.
- Use `defer` in the application wrapper for success, error/early-return,
  nested and loop paths. A C buffer helper with the same borrowed `close` is a
  good demonstration. Do not claim automatic ownership.

### 4. Write the `.cbind`

The `.cbind` is the trusted reviewable contract for the **adapter** API, not a
wide C header parser.

- `format: cinterop-proto/1`, exact platform/ABI;
- `wrapper: "your_wrapper.obx"` when the package is maintained;
- `sources` for the adapter and any toolkit C files;
- `libs` only for the real system library; a fake build uses none and links the
  fake source instead;
- `headers` when a real header is present, otherwise an explicit `declarations`
  list; never pretend a declarations-only check used the installed header;
- one function entry per exported C symbol, with `i64` args and `i32`/`i64` ret.

### 5. Build and test in a private directory

```sh
python3 obo/experimental/cinterop/cinterop.py build \
  path/to/library.cbind path/to/app.obx \
  --out /tmp/opencode/<private-name> --obo obo/obo
python3 obo/experimental/cinterop/cinterop.py verify \
  /tmp/opencode/<private-name>/manifest.json
```

Test at three levels:

1. **Fake C API** (deterministic, no network): success, connection/result
   errors, NULL vs empty, embedded NUL binary, parameter injection-as-data,
   allocation failure, double close, stale/wrong-kind token, result outliving
   connection, and every cleanup path. Check `PQfinish`/`PQclear` counters.
2. **Obo wrapper** against the fake: `defer` success, error/early-return,
   nested lifetimes, loop paths and a C buffer; leak counter with and without a
   missing close; exact numeric text and metadata; binary length/NUL; manifest
   verification; no real library dependency; result-outlives-connection.
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

### 6. Review what the manifest actually proves

The manifest hashes inputs and artifacts and checks platform/ABI/pointer width,
thunk set, signature mode, effects and resolved libraries. Hashes are
provenance/corruption checks, not signatures. A same-signature sidecar or
library substitution remains trusted input. Say that plainly.

## Templates

- `templates/adapter.c` — minimal real C adapter skeleton using `cres`.
- `templates/adapter.h` — scalar-only exported declarations.
- `templates/library.cbind` — a copyable contract skeleton.
- `templates/wrapper.obx` — maintained obo wrapper skeleton with borrowed
  explicit close, `defer` usage and a leak counter.
- `templates/README.md` — how the files fit together.
- `examples/libpq_defer_app.obx` and `examples/zlib_app.obx` — real worked
  examples of `defer` cleanup, binary buffers and a second library.
- `db-adapters.md` — database-specific notes from the experimental
  `obo/experimental/db/{postgres,sqlite}` packages: typed parameter packets,
  statement/connection close ordering, and the declarations-only SQLite ABI
  cross-check policy.

Start from `examples/libpq.*` and `toolkit/libpq_adapter.c` for a complete
working example, then remove the parts your library does not need. Do not grow
`cinterop.py` into a per-library option matrix or a universal IDL generator.

## Stop conditions

Stop and report a concrete blocker if the library requires any of:

- callbacks, C variadic calls, struct-by-value, TLS/IFUNC, floats/decimals;
- a pointer or managed object crossing into obo;
- automatic foreign finalization on ordinary record drop;
- a new compiler-wide language feature or a version/format reservation;
- unbounded or non-length-delimited borrowed memory;
- a test that would modify server configuration or persistent data without
  separate explicit authorization.
