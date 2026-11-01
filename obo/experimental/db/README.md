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
typmod, and numeric/text values are never routed through a float. Scalar
`decimal` values bind and read back as exact decimal text (PostgreSQL numeric
OID 1700, SQLite TEXT); there is no binary numeric wire format. The
dialect-neutral `Sql` template value now has a PostgreSQL bridge and a SQLite
bridge (below); SQL Server and Oracle have placeholder rendering only. Neither
package implements HTML templates, callbacks, automatic finalizers, or a
schema/ORM layer.

## SQL templates

`sql` templates produce a `Sql` value: alternating literal text and ordered
`SqlArg` parameters, with no placeholders in the text. Rendering is a separate,
small step in `obo/lib/sqldialect.obx`; the adapter then binds the same ordered
parameters. Literal chunks are trusted SQL structure, values are never
interpolated or escaped into the text, and no identifier/`ORDER BY` allow-list
is provided.

```obo
import "postgres.obx"
import "sqldialect.obx" as sqldialect

let q = sql`SELECT * FROM t WHERE id = ${id} AND name = ${name}`
let text = sqldialect::renderPostgres(&q)   // SELECT ... id = $1 ... name = $2
let rows = postgres::execute_sql(&conn, &q) // render + map + execute
```

The bridge functions borrow the fragment, so the same `Sql` may be executed or
prepared more than once. `bind_sql` takes the same fragment that supplied the
prepared SQL text and binds indices `1..n` in traversal order. A direct build
from this tree passes `-I obo/lib`; the `cinterop` specs copy
`lib/sqldialect.obx` beside the wrapper with `wrapper_sources`.

| dialect | id | placeholder | this pass |
|---|---:|---|---|
| PostgreSQL | `POSTGRES` | `$1`, `$2`, ... | renderer + `postgres::execute_sql` |
| SQLite | `SQLITE` | `?`, `?`, ... | renderer + `sqlite::prepare_sql` / `bind_sql` |
| SQL Server TDS/`sp_executesql` | `SQLSERVER` | `@p1`, `@p2`, ... | renderer only |
| SQL Server ODBC | `SQLSERVER_ODBC` | `?`, `?`, ... | renderer only |
| Oracle | `ORACLE` | `:1`, `:2`, ... | renderer only |

`render(&s, dialect)` validates that the text and parameter arrays alternate
(`len(text) == len(params) + 1`), rejects an empty fragment and an unknown
dialect, and copies literal chunks verbatim. `$`, `?`, `@` and `:` in literal
text are preserved; they are not rewritten. The explicit helpers are
`renderPostgres`, `renderSqlite`, `renderSqlServer`, `renderSqlServerOdbc` and
`renderOracle`.

`SqlArg` mapping in the two implemented bridges:

| `SqlArg` | PostgreSQL | SQLite |
|---|---|---|
| `Null` | untyped NULL, OID 0 | SQL NULL |
| `Int` | int8, OID 20 | INTEGER (i64) |
| `Bool` | bool, OID 16, `t`/`f` | INTEGER 0/1 |
| `Str` | text, OID 25 | TEXT |
| `Bytes` | bytea, OID 17, exact length | BLOB, exact length |
| `DecimalText` | numeric, OID 1700, exact text | TEXT, exact bytes |
| `Float` | explicit `1014` error | explicit `1014` error |

`SqlArg` byte payloads are borrowed from the `Sql`; the PostgreSQL and SQLite
bridges deep-copy `Bytes`, and `DecimalText` keeps its exact bytes without
reformatting (the packet path parses it only to validate grammar/precision).
`postgres::param_decimal_text(b)` copies bytes and
keeps `PARAM_DECIMAL`; the existing packet validation still rejects empty, NUL,
malformed or over-precision decimal text. The bridge does not silently truncate,
round or interpolate any value.

`Float` has no adapter parameter kind in this pass. Both bridges return
`SQL_ARG_FLOAT_UNSUPPORTED` (`1014`) before a packet or binding is sent;
`decimal` text is the exact transport, and binary64 remains available in the
language but is not routed through these adapters. PostgreSQL OID 701 and a
SQLite `sqlite3_bind_double` path would be separate reviewed additions.

Known type/placeholder gaps for the renderer-only dialects:

- **SQL Server:** `bit`, `nvarchar`, `varbinary`, `decimal` and temporal types
  need an ODBC/TDS type mapping. `@pN` is the TDS/`sp_executesql` form;
  `SQLSERVER_ODBC` uses positional `?` because the ODBC driver numbers
  parameters, not the server. No ODBC adapter is built here.
- **Oracle:** `NUMBER`, `NVARCHAR2`, `RAW`, `DATE`/`TIMESTAMP` and large objects
  need an OCI type mapping; `:n` is the placeholder form. Oracle treats an empty
  string as NULL, so a `Str { v: "" }` binding is not an empty-string value.
  No OCI adapter is built here.
- **PostgreSQL:** `$n` is the extended-query form. An untyped NULL or a bare
  `SELECT $1` may need a cast for server type inference, as with the direct
  `Param` API.
- **SQLite:** `?` is positional. SQLite's own one-based indices are used by
  `bind_sql`; text rejects embedded NUL, while BLOB preserves it.

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

### PostgreSQL decimal boundary

`Param` has two decimal kinds: `PARAM_DECIMAL` (5) carries exact decimal text
and binds as OID 1700 in text format, and `PARAM_DECIMAL_NULL` (6) is a typed
SQL NULL with OID 1700 (needed for `SELECT $1` with no target type). Use:

```obo
postgres::param_decimal(value, p, s)      // checked default
postgres::param_decimal_unchecked(value)  // unconstrained/deliberate coercion
postgres::param_decimal_null()
let meta = rows.decimal_type(col)         // precision, scale, constrained
let d = rows.decimal(row, col)            // Result[decimal]
```

- The default binder is checked: it calls `decimal::check` first and returns
  `1010` (would round), `1011` (exceeds precision) or `1012` (invalid p/s)
  before sending. `param_decimal_unchecked` is the explicit opt-out.
- Emitted text is the exact display form. If only the display scale exceeds
  PostgreSQL's 16,383 limit, the exact canonical form is sent instead; a value
  whose normalized exponent is below -16,383 returns `1013` before the call.
- `Rows.fmt` is 0 for `execute()` and 1 for `execute_binary()`.
  `Rows.decimal` checks, in order: NULL (`1001`), binary format (`1003`), OID
  1700 (`1002`), typmod (`1012`), exact text (`1006`/`1007`/`1008`/`1009`),
  non-finite (`1004`), then `decimal::check` for a constrained column
  (`1010`/`1011`). NULL wins over every other failure.
- `decimal_type` accepts only OID 1700; a domain over numeric or any other OID
  is `1002`. It decodes the upstream typmod formula, including negative scale,
  and rejects out-of-range metadata rather than returning an unconstrained
  record. The text keeps its own dscale; a negative column scale is metadata
  only.
- The C packet reader re-validates the strict public grammar and caps decimal
  text at 98,342 bytes. Empty text, `.5`, `1.`, `1..2`, `1e`, underscores,
  `0x`, whitespace, `NaN`/`Infinity`, NUL and non-ASCII are rejected. There is
  no float path and no interpolation.

Runnable apps: `postgres/examples/postgres_probe_app.obx` (real libpq link and
`PQmakeEmptyPGresult` ownership without a connection), `postgres_live_app.obx`
(opt-in SELECT-only smoke test), `postgres_outlives_app.obx`,
`postgres_sql_live_app.obx` (opt-in `execute_sql` vectors),
`postgres_fake_app.obx` with `postgres_fake.cbind` (obo-level typed packet
encoder over the deterministic fake libpq), and `postgres_sql_app.obx` with
`postgres_fake_sql.cbind` plus the test-only `postgres_fake_probe.obx`
(rendered text, ordered OIDs and exact values of an `execute_sql` packet).

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

### SQLite decimal boundary

```obo
sqlite::bind_decimal(st, index, value, p, s)  // checked default
sqlite::bind_decimal_unchecked(st, index, value)
let d = st.decimal(col)                       // Result[decimal]
```

- Decimal storage is `TEXT` containing exact decimal text. The default binder
  calls `decimal::check` and returns `1010`/`1011`/`1012`; SQLite has no `(p,s)`
  enforcement, so this is the only schema gate. `_unchecked` is the explicit
  opt-out. Neither function binds REAL.
- `Stmt.decimal` rejects SQL NULL (`1001`), REAL (`1005`) and BLOB (`1002`),
  then parses exact TEXT bytes. INTEGER is accepted as SQLite's exact i64-to-text
  rendering with dscale 0. Empty/NUL/non-ASCII text is `1006`; `NaN`/`Infinity`
  is `1004`; precision/exponent/display-scale parse failures map to
  `1007`/`1008`/`1009`.
- Declare decimal columns `TEXT`. NUMERIC/INTEGER affinity converts integral
  text such as `1.00`, `1.0` and `0.0000` to INTEGER and loses display scale
  (accepted, dscale 0); non-integral text becomes REAL and is rejected as
  `1005`. The tests demonstrate both traps. REAL affinity always becomes REAL
  and is rejected.
- `bind_text` still rejects embedded NUL before SQLite. A TEXT cell written by
  SQL can contain NUL and reads as `1006`; a BLOB is `1002` first. A copy failure
  above the 16 MiB registry bound is the size/plumbing code `2`, never empty
  text. Read-side text is not capped at 98,342 bytes: a 100,001-byte
  leading-zero cell parses as `1`.
- SQLite TEXT has no numeric ordering; sort after parsing in obo.

Runnable apps: `sqlite/examples/sqlite_memory_app.obx`,
`sqlite_decimal_app.obx`, `sqlite_file_app.obx` and `sqlite_sql_app.obx`
(`prepare_sql`/`bind_sql` round-trip in memory).

## Tests

```sh
python3 obo/experimental/db/tests/test_db_adapters.py
```

The suite covers:

- deterministic fake-libpq C tests: typed packet/OIDs, empty text/bytea, NULL,
  binary NUL, decimal kind 5/6 and its strict grammar/invalid/oversize vectors,
  33-parameter rejection, malformed packets, SQL/text NUL, bounds, allocation
  failure, invalid conninfo diagnostic, double close, result-outlives-connection
  and live counters;
- an obo-level fake-libpq packet encoder test: typed text/int/blob/NULL params,
  binary NUL, exact numeric text and OID/typmod, no real libpq dependency;
- the `Sql` renderer across PostgreSQL, SQLite, both SQL Server forms and
  Oracle, including parameter order, preserved literal `$`/`?`, empty chunks,
  malformed fragments and unknown dialects;
- `postgres::execute_sql` over the fake libpq: rendered `$1..$n` text, ordered
  text/int8/bool/bytea/NULL/numeric parameters with OIDs, exact decimal bytes,
  a borrowed fragment reused twice, and the `Float`/malformed errors;
- `sqlite::prepare_sql`/`bind_sql` in memory: `?` rendering, ordered
  text/int/bool/BLOB/NULL/decimal binding, an SQL-looking string kept as data,
  exact decimal TEXT and the `Float`/malformed errors;
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
- PostgreSQL decimal binding/read-back over the fake libpq: exact display and
  canonical text, checked error codes, typed NULL, typmod vectors and invalid
  metadata, NULL-vs-binary order, non-finite and parse-status mapping;
- SQLite decimal TEXT/INTEGER/REAL/BLOB storage, NUMERIC and INTEGER affinity
  traps, NULL/empty/zero, NUL policy, dscale 32768/65535 and 38-digit boundaries,
  large read-side text, copy-failure code `2`, and checked binder errors;
- the public decimal text oracle's typmod decode/validation and PostgreSQL
  display-scale boundary vectors; a no-float source scan and `nm -u` check;
- manifest checks that the declarations-only header is hashed as a provenance
  input (without claiming a compiled header check);
- ASAN/UBSAN builds of both C adapter suites.

The PostgreSQL live section is opt-in and SELECT-only, including the
`execute_sql` bridge vectors:

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
- SQL templates are bridged for PostgreSQL and SQLite only; SQL Server and
  Oracle have placeholder rendering, not adapters. No HTML templates, no binary
  numeric wire format and no `Float`/binary64 binding path (code `1014`); these
  are separate future work. Scalar exact decimal text is implemented.
- No decimal fields, `?decimal`, decimal arrays, `decimal(p,s)` language type or
  `DecimalOrNull` wrapper. NULL is error-only (`is_null` first); `DEC_DB_NULL`
  1001 is a defensive fallback.
- Domains over numeric, COPY, arrays, intervals, prepared-statement caches and
  catalog/typmod discovery beyond the result metadata are out of scope.
- No source float arguments/results in the native contract and no `SqlArg.Float`
  binding (`SQL_ARG_FLOAT_UNSUPPORTED`, 1014). SQLite REAL columns are exposed
  through SQLite's text conversion only; do not use this adapter for a
  floating-point protocol.
- Obo token fields are private to their wrapper modules (0.13+). The token
  protects against accidental stale/wrong-kind/forged integer use, not against
  trusted C or an adversarial caller. The generation is finite (31 bits).
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
