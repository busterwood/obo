# Experimental database adapters

Status: **experimental, stable-ish first adapters, not a production driver and
not a public API.** These packages are built with the reviewed
[`cinterop`](../cinterop/README.md) toolkit plus the project
[`obo-c-interop` skill](../cinterop/skill/SKILL.md). They add no compiler or
runtime changes and no per-library compiler branch.

The packages deliberately stay small:

- `postgres/` — a PostgreSQL adapter over libpq. It reuses the reviewed
  `toolkit/libpq_adapter.c` connection/result functions and adds only the typed
  `PQexecParams` packet for `execute`.
- `sqlite/` — a parallel adapter over `libsqlite3.so.0`, with the same
  `cres` registry, length-delimited byte buffers, explicit close and live
  counters. The host has no SQLite development header, so the C ABI is a
  read-only-verified **declarations-only** subset; see below.

Both packages accept scalars and return exact bytes/text, with explicit NULL
flags rather than length inference. PostgreSQL columns expose type OID and
typmod, and numeric/text values are never routed through a float. Neither
package implements SQL templates, HTML templates, a decimal wire format,
callbacks, automatic finalizers, or a schema/ORM layer.

## Ownership and cleanup

`Connection`, `Rows` and `Stmt` are ordinary obo records holding only an integer
registry token. No raw `PGconn*`, `PGresult*`, `sqlite3*`, `sqlite3_stmt*` or
`char*` reaches obo. The trusted C registry checks kind and generation before
every dereference.

Dropping a record **does not** free the foreign object. Cleanup is explicit,
normally with `defer`:

```obo
defer conn.close()
defer rows.close()
defer stmt.close()
```

- `close` borrows the wrapper, poisons the token, and returns `true` once.
- A second close is a safe `false`, not a second `PQfinish`/`PQclear`/
  `sqlite3_finalize`.
- A stale or wrong-kind token is rejected before C uses a pointer.
- Obo `exit(...)` runs no defers; return from the block when cleanup must run.
- Live counters (`live_resources`, `live_connections`, `live_results`,
  `live_statements`, `live_buffers`) make an omitted close visible in tests.
- There is no automatic foreign finalizer. Adding one needs a separate,
  reviewed language/runtime design.

## PostgreSQL

```obo
module app
import "postgres.obx"

fn main() {
    let made = postgres::open("host=/var/run/postgresql dbname=postgres user=chris connect_timeout=3")
    match made {
        ok(conn) {
            defer conn.close()
            var ps = new []postgres::Param(0)
            append(ps, postgres::param_text("hello"))
            append(ps, postgres::param_int(42))
            append(ps, postgres::param_bool(true))
            append(ps, postgres::param_blob(bytes))
            append(ps, postgres::param_null())
            let q = postgres::execute(&conn,
                "SELECT $1::text AS t, $2::int8 AS n, $3::bool AS b, encode($4::bytea,'hex') AS p, $5::text AS z",
                ps)
            match q {
                ok(rows) {
                    defer rows.close()
                    // row_count(), column_count(), column_name(),
                    // column_type_oid(), column_type_modifier()
                    // value() -> Result[?[]byte]: nil is SQL NULL
                    // text()  -> Result[string] (SQL NULL is an error)
                    // bytes() -> exact bytes including embedded NUL
                }
                err(m, c) { }
            }
        }
        err(m, c) { }
    }
}
```

- `open(conninfo)` calls `PQconnectdb`; the reviewed toolkit's bounded
  `connect_timeout` check is reused. A syntactically invalid conninfo returns
  `err("invalid connection string: <libpq parser message>", 2)` before any
  connection attempt. A syntactically valid conninfo that libpq cannot connect
  (bad socket path, invalid `connect_timeout` value, refused authentication)
  returns `err(<libpq message>, 1)` / `CONNECTION_BAD`, not the input-error code
  and not "could not be allocated". A failed connection copies the error text
  before `PQfinish`.
- `execute(sql, params)` and `query(sql)` use `PQexecParams` with the fixed
  eight-argument descriptor and explicit parameter OIDs: text 25, bytea 17,
  int8 20, bool 16, and 0 for an untyped NULL. `execute_binary` asks for binary
  result columns.
- SQL and text parameters reject embedded NUL. Bytea parameters preserve their
  exact length, including an empty bytea (non-NULL, zero length). SQL is never
  interpolated; `$1..$n` are placeholders.
- A SQL execution failure is still a successful adapter call: `execute()`
  returns `ok(Rows)` with `rows.status() == PGRES_FATAL_ERROR` and the copied
  `error_message()`. This matches the reviewed libpq toolkit; only packet,
  transport, allocation and input errors are `err(...)`.
- `value(row,col)` is `Result[?[]byte]` because obo optional values must be
  reference types; `text(row,col)` is the string convenience and rejects NULL
  and embedded NUL. `bytes(row,col)` returns exact value bytes, with NULL as an
  error. (The requested `Result[?string]` shape is not expressible in this obo
  language version; the NULL distinction is preserved through `?[]byte` and
  `is_null`.)
- A `PGresult` remains usable after its `PGconn` closes; the tests exercise
  that lifetime. Rows/columns are bounds checked; typmod `-1` is a valid value,
  so column index checks happen before reading it.
- The package links `-l:libpq.so.5` and therefore inherits libpq's system
  dependencies (OpenSSL, GSSAPI, etc. as installed). The header is absent on the
  probe host, so the cbind is explicitly `declarations-only`; hashes are
  provenance checks, not header proof.

Runnable apps: `postgres/examples/postgres_probe_app.obx` (real libpq link and
`PQmakeEmptyPGresult` ownership without a connection), `postgres_live_app.obx`
(opt-in SELECT-only smoke test), `postgres_outlives_app.obx`, and
`postgres_fake_app.obx` with `postgres_fake.cbind` (obo-level typed packet
encoder over the deterministic fake libpq).

## SQLite

```obo
module app
import "sqlite.obx"

fn main() {
    let made = sqlite::open(":memory:")   // or a fresh path under /tmp
    match made {
        ok(conn) {
            defer conn.close()
            let created = conn.exec("CREATE TABLE t(id INTEGER PRIMARY KEY, v TEXT)")
            let st = conn.prepare("INSERT INTO t(id,v) VALUES(?1,?2)")
            match st {
                ok(s) {
                    defer s.close()
                    s.bind_int(1, 1)
                    s.bind_text(2, "hello")
                    s.step()                  // Result[bool]: true row, false done
                    s.reset()
                }
                err(m, c) { }
            }
        }
        err(m, c) { }
    }
}
```

- `open(path)` uses `sqlite3_open_v2` with READWRITE|CREATE|URI. `:memory:` and
  files under `/tmp` are supported; the tests use both and remove their files.
- `exec(sql)` uses `sqlite3_exec` and accepts multiple statements. `prepare(sql)`
  accepts exactly one statement and conservatively rejects trailing text after
  it, including a trailing SQL comment; this is deliberate, not a full SQL
  lexer.
- Bind indices are 1-based; out-of-range is `SQLITE_RANGE`. Bindings are
  `bind_null`, `bind_int` (i64), `bind_text` and `bind_blob`; text rejects
  embedded NUL, blob preserves it.
- `step()` is `Result[bool]`: `true` for `SQLITE_ROW`, `false` for
  `SQLITE_DONE`, and an error Result for any SQLite error. `reset()` is
  explicit and returns success after resetting even when the previous step
  failed, so the statement is reusable; `close()` finalizes exactly once.
- `column_value` is `Result[?[]byte]` (nil is SQL NULL); `column_text` is the
  string convenience; `column_bytes` preserves TEXT/BLOB bytes; `column_type`
  returns the SQLite datatype. A non-NULL value whose copy exceeds the 16 MiB
  registry/buffer bound (or fails allocation/registry registration) is an
  error, never an empty success; zero-length values remain valid. `column_int`
  is an exact i64 only for an INTEGER column; TEXT is converted by SQLite.
  REAL is rejected by `column_value`, `column_text` and `column_int` because it
  is not exact decimal/integer data. SQLite's documented integer-to-text
  conversion is used, never an obo float in the adapter.
- A connection refuses to close while a prepared statement is live
  (`SQLITE_BUSY`); close statements first, then the connection. In one block,
  register `defer conn.close()` **before** `defer stmt.close()`: defers run
  last-in, first-out, so the statement finalizes before the connection closes.
  Registering the connection defer later would run `conn.close()` first, get
  `SQLITE_BUSY`, and leak the connection.
- Errors are copied immediately: `sqlite3_exec`'s allocated `errmsg` is copied
  and freed, `open` copies the message before closing a failed handle, and
  `error_message`/`error_code` are snapshots. Connection-level errors belong to
  the connection; each statement has its own message/code, so statement B
  neither sees nor clears statement A's last error. Returned SQLite codes are
  exposed as `SQLITE_ERROR`, `SQLITE_CONSTRAINT`, `SQLITE_BUSY`, etc.

### SQLite declarations-only provenance

The probe host has `/lib/x86_64-linux-gnu/libsqlite3.so.0` and the `sqlite3`
3.45.1 shell, but no `/usr/include/sqlite3.h` and no `pkg-config`. The committed
`sqlite_decls.h` is an independently written minimal ABI declaration subset.
`sqlite.cbind` records `signature_check: "declarations-only"`; it does not claim
a header check. Linking is `-l:libsqlite3.so.0`. The declaration file and the
adapter/shared headers are listed under `declaration_files` and hashed into the
manifest as provenance inputs, without being promoted to a compiled header
check. The PostgreSQL cbind does the same for `libpq_decls.h`,
`postgres_adapter.h` and `cres.h`.

A private evidence-only cross-check compiled the real 3.45.1 header first and
these declarations together with static assertions for every used constant,
prototype and integer width. That check is not part of the committed build and
does not require the header: tests still run declarations-only on a host with
only the runtime library. The cross-check changed no declaration (all
prototypes, constness, enum values and `SQLITE_TRANSIENT` matched).

Runnable apps: `sqlite/examples/sqlite_memory_app.obx` and
`sqlite_file_app.obx`.

## Tests

```sh
python3 obo/experimental/db/tests/test_db_adapters.py
```

The suite covers:

- deterministic fake-libpq C tests: typed packet/OIDs, empty text/bytea, NULL,
  binary NUL, 33-parameter rejection, malformed packets, SQL/text NUL, bounds,
  allocation failure, invalid conninfo diagnostic, double close,
  result-outlives-connection and live counters;
- an obo-level fake-libpq packet encoder test: typed text/int/blob/NULL params,
  binary NUL, exact numeric text and OID/typmod, no real libpq dependency;
- real libpq link/PGresult ownership with no connection, plus invalid-conninfo
  input-error reporting;
- real SQLite in-memory create/insert/select/param roundtrip/NULL/blob-NUL/
  constraint error/reset-after-error/cross-statement error isolation/bounds/
  close-order/double-close/stale-token/live counters; >16 MiB BLOB/TEXT copy
  failures are errors (zero-length values remain valid), REAL is rejected, and
  a same-block defer-order regression checks statement-before-connection close;
- real SQLite tempfile under `/tmp`, compared with Python's `sqlite3` module as
  an independent oracle for `typeof`, NULL, empty text and BLOB bytes, then
  removed;
- manifest checks that the declarations-only header is hashed as a provenance
  input (without claiming a compiled header check);
- ASAN/UBSAN builds of both C adapter suites.

The PostgreSQL live section is opt-in and SELECT-only:

```sh
CINTEROP_TEST_DSN='host=/var/run/postgresql port=5432 dbname=postgres user=chris connect_timeout=3' \
  python3 obo/experimental/db/tests/test_db_adapters.py
```

The conninfo above is a private local-development example, not a default. The
tests do not create roles/databases/schema/data, edit server configuration,
start services, expose ports, install packages, or read credential files. A
missing `libpq.so.5`/`libsqlite3.so.0` is the only skip; once a library is
present, a build or assertion failure is a test failure. SQLite header absence
is not a skip because the committed adapter is declarations-only.

## Known limits

- No production driver, prepared-statement cache, notifications, COPY, TLS
  policy, connection pool, migrations, transactions API or ORM.
- No SQL/HTML templates and no decimal wire format yet; these are separate
  future work. Numeric text is preserved exactly, but there is no decimal
  arithmetic in obo here.
- No source float arguments/results in the native contract. SQLite REAL columns
  are exposed through SQLite's text conversion only; do not use this adapter for
  a floating-point protocol.
- Obo record fields are not private. The token protects against accidental
  stale/wrong-kind/forged integer use, not against trusted C or an adversarial
  caller. The generation is finite (31 bits).
- PostgreSQL's cbind is declarations-only on this host; libpq dynamic
  dependencies can still resolve differently at run time.
- SQLite's declarations-only ABI is verified against the 3.45.1 header in
  private evidence, not by a committed header check. A future SQLite ABI change
  needs a new review.
- The resource registry has 64 slots total and bounds buffers to 16 MiB.
- The C registry kinds (`CONN`, `RESULT`, `STMT`) are shared by the two
  packages. A compiler build links one cbind/package, so a token from the other
  adapter is not reachable in a normal application; do not link both native
  packages into one custom image. A cross-adapter token would still be
  same-kind and is not rejected by kind alone.
- SQLite `prepare()` rejects trailing text/comments after the first statement
  rather than implementing a SQL lexer.
- No automatic foreign finalizer; a dropped record leaks until explicit close.
  The live counters are diagnostics, not a safety proof.
- The manifest hashes the adapter/shared declaration headers through
  `declaration_files`; host-provided CRT startup objects (`crt1.o`, `crti.o`,
  `crtbegin.o`, `crtend.o`, `crtn.o`) are resolved from the host toolchain by
  cinterop and are not individually hashed. The manifest records the compiler
  path and other build inputs.
