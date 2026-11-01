# Skill note: worked database adapters

This note supplements `SKILL.md` for database-shaped C APIs. The worked
packages are `obo/experimental/db/postgres`, `obo/experimental/db/sqlite` and
`obo/experimental/db/mssql`; all use cinterop v2 foreign handles and explicit
close. `db/postgres` wraps `libpq::Connection`/`libpq::Res`; `db/sqlite` owns
`sqlite3*`/`sqlite3_stmt*` directly; `db/mssql` owns ODBC `SQLHDBC`/`SQLHSTMT`
through project shim types.

## What transfers from libpq

- Keep the real C pointer in a `foreign` record's private word. `own`/`own_opt`
  return it, `borrow`/`borrow_mut` pass it, `is_open` reads the word, and a
  direct borrow after close exits 7. The older `cres` token registry is only
  for the zlib adapter and its test, the buffer example and the registry test,
  not these packages.
- List shared ordinary `.obx` helpers in the spec's `wrapper_sources` (sqlite
  and postgres list `sqldialect.obx`, zlib lists `buffer.obx`, and postgres
  also lists `libpq.obx`) so the manifest hashes them. Every spec function
  carries `"effects": ["impure"]`; the compiler adds the conservative
  `freesall,writeshandles` summary and the tool checks it.
- Release every connection/result/statement exactly once from an explicit
  close. A second close is a safe `false`/`ok(false)`.
- Copy borrowed values and error strings with an explicit length. Use the
  library's reported length (`PQgetlength`, `sqlite3_column_bytes`), never
  `strlen`, for a value that may contain NUL.
- Keep error state at the level that owns it: connection-level open/exec errors
  on the connection, and a separate copied message/code per prepared statement.
  A successful bind on statement B must not erase A's last error.
- Carry NULL as a distinct kind/flag. Empty bytes are not NULL.
- Pass the value/binary/OID arrays directly to `PQexecParams`; there is no
  length-delimited packet and no local C parser. The typed OID array is checked
  against the value count and `0..0xffffffff`.
- Set explicit parameter OIDs/types when the API allows it. Preserve exact
  numeric text plus metadata; do not convert database numerics through a float.
- Use a fixed SQL statement with placeholders. Do not interpolate strings.
- Under `OBO_CHECK=1` the foreign-handle census reports a dropped open handle
  (exit 8); test that instead of adding `live_*` counters.
- Test with a deterministic fake API first, then the real library; an installed
  library must not produce a silent skip after a build failure.

## Out parameters and error paths

SQLite's open and prepare calls report errors through a status code while still
writing (or leaving NULL) an out handle. The v2 out record exists for this:

```obo
native fn open_v2(path: &[]byte, flags: int, out db: Connection): open_v2_out
native fn prepare_v2(db: &Connection, sql: &[]byte,
                     out st: Stmt, out tail: int from sql): prepare_v2_out
native fn prepare_at(db: &Connection, sql: &[]byte, off: int,
                     out st: Stmt, out tail: int from sql): prepare_at_out
```

- An out call is stored in a `var`; a bare statement is refused because it
  would drop a live `out_own`.
- The record is `rc: int` first, then one field per out parameter in
  declaration order (`?Stmt` for `out_own`, `int` for `out_offset`).
- `take(out.st)` moves the handle out. Close it exactly once on the error path
  too: `sqlite3_open_v2` writes a non-NULL `sqlite3*` on most failures, so read
  the message before closing it.
- `out_offset` yields `-1` for a NULL or out-of-range tail pointer; the wrapper
  turns that into an error. `sq_prepare_at` prepares at an absolute offset
  (linear in the statement stream, not a copy per statement).
- The out record is an ordinary owned value. Dropping it frees the obo record,
  not the C object; a missed close is reported by the census.

## `release_result: discard`

`db/sqlite/sqlite.cbind` uses `"release_result": "discard"` for
`sqlite3_close_v2` and `sqlite3_finalize`. Both return an int, but their result
is not treated as a close failure here (D14/D15): close succeeds while
statements are live, the `Connection` census drops, and the statements keep
working until finalized. `discard` calls the int-returning release and ignores
the value. Use `"int"` only when a non-zero release really is an error, and
`"void"` for a void release such as `PQfinish`/`PQclear`.

## Text NUL, binary length and parameter formats

- A text-format parameter is a `len+1` copy truncated back to `len`. libpq
  reads text `paramValues[]` with `strlen` and ignores `paramLengths`, so the
  terminator sits one byte past the data. SQL, text and C-string inputs reject
  embedded NUL before the call.
- A binary parameter (OID 17 `bytea`) keeps its exact length and may contain
  NUL. `Rows.fmt` is 0 for a text result and 1 for
  `exec_params_binary`/`exec_params_typed_binary`.
- Result values and error messages are copied by length. `PQgetlength`/
  `sqlite3_column_bytes` are authoritative; never call `strlen`.
- Preserve exact numeric text, OID and typmod. No numeric value is routed
  through a float.

## Database-specific choices

- **PostgreSQL.** `db/postgres` wraps the v2 `examples/libpq.obx`. The wrapper
  records hold the libpq handle in a field, and the field reaches a **native
  borrow** directly; passing that field to another obo function is refused by
  the deep-reference rule. OIDs used here: text 25, bytea 17, int8 20, bool 16,
  numeric 1700, untyped NULL 0. A text-format parameter is a `len+1` copy
  truncated back to `len`; binary values keep their exact length. A `PGresult`
  outlives its `PGconn`; test that explicitly.
- **SQLite.** A missing development header is not permission to pretend a
  header check happened. Use a small, independently written declarations-only
  subset matching the official C API, link `-l:libsqlite3.so.0`, and record
  `signature_check: "declarations-only"`. List the declaration file under
  `declaration_files` so the manifest hashes it without pretending it was a
  compiled header check. If a real header is available in a private evidence
  location, compile the declarations against it as a read-only cross-check, but
  keep the committed build independent of that header. The libpq binding is
  the `signature_check: "header"` example (against `toolkit/libpq_decls.h`).
- SQLite uses `sqlite3_close_v2` (D14): close succeeds while statements are
  live, the `Connection` census drops, and the statements keep working until
  finalized. Register `defer conn.close()` before `defer stmt.close()` as the
  tidy order; the statement census catches a forgotten finalize.
- **SQL Server / ODBC.** The narrow (ANSI) ODBC entry points are not UTF-8 for
  SQL text, column names or diagnostics: the driver converts them through the
  process locale. The `db/mssql` binding converts those itself and calls the W
  entry points; result values and `SQL_C_CHAR` parameter values are UTF-8 and
  were measured correct under the C locale. `SQLGetData` reports
  `SQL_NO_TOTAL` for a truncated `SQL_C_CHAR` chunk (the chunk length is
  recovered with a `0xFF` sentinel) but the remaining length for
  `SQL_C_BINARY`. ODBC has no one-argument statement free, so the release is a
  shim (`ms_close_stmt`) and the foreign type is a project opaque type whose
  value is the driver handle. See `db/mssql/README.md` for the probe table.
- SQLite's `sqlite3_exec` and `sqlite3_prepare_v2` have different statement
  counts: exec accepts multiple statements, prepare accepts exactly one. State
  that in the wrapper rather than silently truncating SQL.
- `sqlite3_reset` returns the previous step error after resetting. Report reset
  as success and clear that diagnostic; otherwise a reusable statement looks
  permanently failed.
- `sqlite3_column_int64` is exact only for INTEGER; REAL truncates. Expose
  exact text separately and say which accessor preserves which representation.

## Obo-language constraint

Obo optional values are reference types only, so `Result[?string]` is not
expressible in this language version. The database packages use
`Result[?[]byte]` for exact text with `nil` meaning SQL NULL and add a
`text`/`column_text` convenience that returns `Result[string]` and treats NULL
as an error. Say this plainly; do not fake a nullable string with an empty
string.

## Not approved here

No SQL/HTML template engine, decimal wire format, callbacks, automatic
finalizers, connection pool, ORM or schema migration layer. Those need separate
design and review.
