# Skill note: worked database adapters

This note supplements `SKILL.md` for database-shaped C APIs. The worked
packages are `obo/experimental/db/postgres` and `obo/experimental/db/sqlite`;
both use the same `cres` registry and byte-buffer pattern as the libpq toolkit
adapter.

## What transfers from libpq

- Keep foreign pointers in C and put only generation/kind-checked integer
  tokens in obo records.
- Register every connection/result/statement allocation; release it exactly
  once from an explicit close. A second close is a safe false.
- Copy borrowed values and error strings with an explicit length. Use the
  library's reported length (`PQgetlength`, `sqlite3_column_bytes`), never
  `strlen`, for a value that may contain NUL.
- Carry NULL as a distinct kind/flag. Empty bytes are not NULL.
- Build a small length-delimited packet in obo for parameter batches, then copy
  it once into a registry buffer. The C adapter parses and bounds the packet.
- Set explicit parameter OIDs/types when the API allows it. Preserve exact
  numeric text plus metadata; do not convert database numerics through a float.
- Use a fixed SQL statement with placeholders. Do not interpolate strings.
- Provide live counters and a test that fails when a close is omitted.
- Test with a deterministic fake API first, then the real library; an installed
  library must not produce a silent skip after a build failure.

## Database-specific choices

- **PostgreSQL.** Reuse the reviewed `toolkit/libpq_adapter.c` for connection
  status, result metadata and close; add only a typed `PQexecParams` packet.
  Keep the eight-argument call signature. OIDs used here: text 25, bytea 17,
  int8 20, bool 16, untyped NULL 0. A `PGresult` outlives its `PGconn`; test
  that explicitly.
- **SQLite.** A missing development header is not permission to pretend a
  header check happened. Use a small, independently written declarations-only
  subset matching the official C API, link `-l:libsqlite3.so.0`, and record
  `signature_check: "declarations-only"`. If a real header is available in a
  private evidence location, compile the declarations against it as a read-only
  cross-check, but keep the committed build independent of that header.
- SQLite connections must not close while prepared statements are live. Track a
  per-connection statement count, return `SQLITE_BUSY` from close, and document
  the LIFO `defer stmt.close()` / `defer conn.close()` order.
- SQLite's `sqlite3_exec` and `sqlite3_prepare_v2` have different statement
  counts: exec accepts multiple statements, prepare accepts exactly one. State
  that in the wrapper rather than silently truncating SQL.
- A prepare error code can be a small positive SQLite result code, so the
  adapter's "no token" return must be non-positive (0/negative) or the obo
  wrapper will mistake an error for a token.

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
