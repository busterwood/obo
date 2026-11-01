# Skill note: worked database adapters

This note supplements `SKILL.md` for database-shaped C APIs. The worked
packages are `obo/experimental/db/postgres` and `obo/experimental/db/sqlite`;
both use cinterop v2 foreign handles and explicit close. `db/postgres` wraps
`libpq::Connection`/`libpq::Res`; `db/sqlite` owns `sqlite3*`/`sqlite3_stmt*`
directly.

## What transfers from libpq

- Keep the real C pointer in a `foreign` record's private word. `own`/`own_opt`
  return it, `borrow`/`borrow_mut` pass it, `is_open` reads the word, and a
  direct borrow after close exits 7. The older `cres` token registry is for
  sys/tls/http2/zlib/buffer, not these packages.
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
  keep the committed build independent of that header.
- SQLite uses `sqlite3_close_v2` (D14): close succeeds while statements are
  live, the `Connection` census drops, and the statements keep working until
  finalized. Register `defer conn.close()` before `defer stmt.close()` as the
  tidy order; the statement census catches a forgotten finalize.
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
