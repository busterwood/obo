# Experimental Microsoft SQL Server adapter (`db/mssql`)

Status: **experimental first slice, not a production driver and not a public
API.** It is a cinterop v2 binding over unixODBC and the Microsoft ODBC Driver
18 for SQL Server, with the same explicit-close, foreign-handle and census
rules as `db/sqlite` and `db/postgres`. The package adds no compiler or runtime
changes.

- `Connection` and `Stmt` are `foreign` handles; obo never sees a `SQLHDBC` or
  `SQLHSTMT`.
- `Rows` is an ordinary record that owns one `Stmt` and is the streaming cursor:
  `fetch()` advances one row and the column accessors read that row.
- Close explicitly, normally with `defer`. A second close is `ok(false)`. A use
  after close exits 7 in the native thunk. Under `OBO_CHECK=1` a dropped open
  handle is reported at normal end with exit 8; a census underflow exits 4.
- SQL text, parameter values, result values and diagnostics are UTF-8. Text is
  never formatted through a float. SQL is a fixed statement with `?`
  placeholders; there is no string interpolation.

## Measured ODBC surface (Phase 1)

A throwaway C probe ran against the live driver before this design was written.
The measurements are the reason for the W/narrow split below.

### Sizes

`sizeof(SQLWCHAR)=2`, `sizeof(SQLSMALLINT)=2`, `sizeof(SQLINTEGER)=4`,
`sizeof(SQLLEN)=8`, `sizeof(SQLULEN)=8`, `sizeof(SQLHANDLE)=8` on
x86_64-linux-gnu. `mssql_decls.h` states these directly instead of including
the system headers.

### ANSI (narrow) API is not UTF-8-safe for text metadata

The probe ran under the `C` locale and compared the resulting server-side
UTF-16 bytes:

| Surface | Narrow result |
|---|---|
| SQL text `SELECT N'café'` via `SQLExecDirect` | **wrong**: the server received `cafÃ©` (the driver converted UTF-8 through the locale) |
| SQL text `SELECT 'café'` | also locale-converted |
| `SQLBindParameter` with `SQL_C_CHAR` + `SQL_WVARCHAR` | **correct**: `caf e9 00` in UTF-16; the parameter bytes are honoured as UTF-8 |
| `SQLGetData(SQL_C_CHAR)` of an `nvarchar` | **correct** UTF-8 (`caf c3 a9`) |
| `SQLDescribeCol` column name `[café]` | **wrong**: Latin-1 `0xE9`, not UTF-8 |
| `SQLGetDiagRec` message `no_such_café` | **wrong**: Latin-1 `0xE9` |

So the binding converts UTF-8 to UTF-16 itself and calls the **W** entry points
for the connection string, SQL text, column names and diagnostics. Result and
parameter **values** stay narrow `SQL_C_CHAR`, which the driver handles as
UTF-8. `ms_utf16.c` implements strict conversion (overlong forms, lone
surrogates, values above U+10FFFF and truncated sequences are rejected).

### Connecting

`SQLDriverConnectW` returned `SQL_SUCCESS_WITH_INFO` with two `01000` records
("Changed database context", "Changed language setting"). The connection is
usable; these are informational, not errors. Reading a diagnostic does not
clear it, so `Connection.error_message()` immediately after `open` can show
those records. Statement errors put their records on the statement handle; the
connection handle has none.

### `SQLGetData`

- `nvarchar`, `decimal`, `datetime2` and `uniqueidentifier` with `SQL_C_CHAR`
  return UTF-8 text. `decimal(18,4)` keeps the column scale (`12.3400`,
  `-.5000`, `.0000`) and omits the leading zero below one. `datetime2(3)`
  returns `YYYY-MM-DD HH:MM:SS.fff`; `datetimeoffset` returns its offset suffix
  and is readable as text. `uniqueidentifier` returns the canonical 36-character
  hyphenated text.
- `varbinary` with `SQL_C_BINARY` returns raw bytes. The indicator is the
  remaining total length, so a chunk is `min(indicator, buffer)`.
- NULL is `indicator = -1` (`SQL_NULL_DATA`); an empty value is `indicator = 0`.
  They are distinct.
- A truncated `SQL_C_CHAR` chunk returns `SQL_SUCCESS_WITH_INFO` with
  `indicator = -4` (`SQL_NO_TOTAL`), not the remaining length. The chunk length
  is recovered by pre-filling the buffer with `0xFF` (never valid UTF-8) and
  finding the driver's NUL terminator. This keeps embedded NULs exact; the
  final `SQL_SUCCESS` call reports the exact remaining byte count.
- A one-byte `SQL_C_CHAR` buffer makes no progress (the driver needs room for
  the terminator). The binding always passes at least two bytes and grows by
  doubling to the 16 MiB value cap.
- `SQLRowCount` is `-1` before or during a `SELECT` and reports the row count
  after fetching to `SQL_NO_DATA`; for DML it is the affected-row count.

### Declaration and link

`mssql.cbind` uses `signature_check: "declarations-only"` and lists
`mssql_decls.h` and `ms_internal.h` under `declaration_files`. The declarations
are independently written and ABI-identical to the installed driver's types;
the live tests are the ABI gate. The binding links `-l:libodbc.so.2` and does
not include a system ODBC header.

## API

```obo
module app
import "mssql.obx"

fn main() {
    let made = mssql::open(dsn)               // Result[Connection]
    match made {
        ok(conn) {
            defer conn.close()

            // No parameters: one fixed statement. Rows owns the statement.
            let q = conn.exec("SELECT id, name FROM dbo.items ORDER BY id")
            match q {
                ok(rows) {
                    defer rows.close()
                    while true {
                        let more = rows.fetch()
                        match more {
                            ok(r) {
                                if !r { break }
                                let id = rows.text(0)
                                let isn = rows.is_null(1)
                                // text(), bytes(), value(), decimal(), decimal_text()
                            }
                            err(m, c) { break }
                        }
                    }
                }
                err(m, c) { }
            }

            // Typed parameters. Values are copied into C slots before execute.
            var ps = new []mssql::Param(0)
            append(ps, mssql::param_text("café"))
            append(ps, mssql::param_int(42))
            append(ps, mssql::param_bytes(bytes))
            append(ps, mssql::param_null())
            let dp = try mssql::param_decimal_text(decimal_bytes)
            append(ps, dp)
            let r = conn.exec_params("SELECT ?, ?, ?, ?, ?", ps)
        }
        err(m, c) { }
    }
}
```

### Connection

| Function | Notes |
|---|---|
| `mssql::open(dsn: string): Result[Connection]` | DSN is a UTF-8 ODBC connection string. Empty, over 16 MiB, or containing NUL is code 2. A failed connect copies the diagnostic, SQLSTATE and native code before freeing the handle. |
| `Connection.close(): Result[bool]` | Marks every child statement dead, frees its HSTMT, then disconnects and frees the DBC. A disconnect/free failure is an `err`; the handle is gone either way. Second close is `ok(false)`. A child `Rows` remains usable only as a clean "statement's connection is closed" error after this call. |
| `Connection.is_open(): bool` | |
| `Connection.status(): int` | `SQL_SUCCESS` while open, `SQL_ERROR` after close. |
| `Connection.error_message(): []byte` | All connection diagnostic records as UTF-8 `SQLSTATE/native/message` lines. |
| `Connection.sqlstate(): []byte` | First record's SQLSTATE. |
| `Connection.error_code(): int` | First record's native (SQL Server) error number. |
| `Connection.exec(sql: string): Result[Rows]` | Execute one fixed statement. A SQL failure is `err(message, native_code)` and the statement is closed first. |
| `Connection.prepare(sql: string): Result[Rows]` | Prepare for typed `?` parameters. The returned Rows is unexecuted. |
| `Connection.exec_params(sql, params: []Param): Result[Rows]` | Prepare, bind and execute in one call. |

### Rows (the streaming cursor)

| Function | Notes |
|---|---|
| `fetch(): Result[bool]` | `true` row, `false` `SQL_NO_DATA`, `err` on a driver failure. Resets the per-row cell cache. |
| `execute(): Result[bool]` | Execute a prepared statement. |
| `bind_all(params: &[]Param): Result[bool]` | Bind every parameter in order. Count mismatch is code 2 before any bind. |
| `clear_params(): Result[bool]` | Free the C-side bind slots and reset the parameter state. |
| `row_count(): int` | `SQLRowCount`; -1 when unknown (before/while fetching a SELECT). |
| `column_count(): int`, `column_name(col)`, `column_type(col)`, `column_size(col)`, `column_digits(col)`, `column_nullable(col)` | Metadata; column indexes are 0-based and bounds-checked. |
| `is_binary(col): Result[bool]` | True for `binary`/`varbinary`/`longvarbinary`. |
| `is_null(col): Result[bool]` | Reads and caches the cell. NULL is distinct from empty. |
| `value(col): Result[?[]byte]` | nil is SQL NULL; an empty array is an empty value. Binary columns return raw bytes; other columns return the driver's UTF-8 bytes. |
| `bytes(col): Result[[]byte]` | Non-NULL exact bytes; embedded NUL is preserved. |
| `text(col): Result[string]` | Non-NULL text; rejects NULL, binary columns and embedded NUL. |
| `decimal_text(col): Result[?[]byte]` | SQL Server's exact decimal text; nil is NULL. |
| `decimal(col): Result[decimal]` | Parses the exact text. NULL is `DEC_DB_NULL`; binary is `DEC_DB_NOT_DECIMAL`; `float`/`real` is `DEC_DB_INEXACT_STORAGE`. Inserts the leading zero SQL Server omits (`.5000` → `0.5000`) without changing value or display scale. |
| `status(): int`, `error_message()`, `sqlstate()`, `error_code()` | Last `SQLRETURN` and the statement diagnostic. |
| `close(): Result[bool]`, `is_open()` | Second close is `ok(false)`; accessors after the Rows closes are `err("rows are closed", 2)`. After the **Connection** closes, `is_open()` is false and accessors are `err("statement's connection is closed", 2)`; `close()` still frees the C state once. |

The cell cache is per statement and per column/representation: `is_null(0)`
then `text(0)` then `bytes(0)` reuse one `SQLGetData` read for the current row.
`SQLGetData` consumes columns in ascending order, so reading `text(1)` first
makes the shim read and cache column 0 as well; a later `text(0)` is served
from that cache. The cache is cleared when a row is fetched or when the cursor
reaches `SQL_NO_DATA`/a failed fetch, so `text(0)` after exhaustion is
`err("no current row", 2)`, not the last row.

### Parameters

| Constructor | Wire form |
|---|---|
| `param_text(string)` | `SQL_C_CHAR` + `SQL_WVARCHAR`; UTF-8 (invalid UTF-8 is rejected before the driver can substitute U+FFFD); embedded NUL rejected at bind. Values over 4000 bytes use an unspecified `ColumnSize`, which the driver requires. |
| `param_int(int)` | `SQL_C_SBIGINT` + `SQL_BIGINT`; the full i64 range. |
| `param_bytes([]byte)` / `param_blob` | `SQL_C_BINARY` + `SQL_VARBINARY`; exact length including NUL. Values over 8000 bytes use an unspecified `ColumnSize`. |
| `param_null()` | `SQL_NULL_DATA`; typed by the target column. |
| `param_decimal_text([]byte)` | Exact decimal text; validated by `decimal::parse`. SQL Server decimal has at most 38 significant digits, which is the real bound. |
| `param_decimal(value, p, s)` | `decimal::check` first; a value that would round is `DEC_DB_SCALE`. |

Every value is copied into a C-side slot at bind time, so the caller's array
need not outlive `execute`. The slots are freed on `clear_params` or close.
Decimal values travel as text and SQL Server converts to the target column's
type; there is no binary decimal format.

## Limits and error model

Kept from the other adapters: SQL text 1 MiB, connection string 16 MiB, value
16 MiB (text and binary parameters included), 32 parameters. This package also
caps result columns at 4096 and rejects an out-of-range column or a closed
handle before any C call. SQL Server's `decimal(p,s)` has `p` and `s` in
`1..38`; `decimal::parse` enforces the 38-significant-digit bound, so the
generic 98,342-byte text cap used by the other adapters does not apply here.

A SQL failure is `err(message, code)`. `message` is the formatted diagnostic, so
a string search for the SQLSTATE and the native number both work; the same
values are available structurally through `sqlstate()` and `error_code()`.
Plumbing/input errors use code 2 and allocation failures code 3. A native SQL
Server error code (for example 208 for an invalid object) is used unchanged.

## Tests

The live suite is opt-in:

```sh
python3 obo/experimental/db/mssql/tests/test_mssql.py
```

It reads the DSN from `MSSQL_TEST_DSN`, or from `~/.obo-mssql-dsn` (mode 600)
when the environment is unset, and skips cleanly when neither exists. The DSN
is never printed or written into the manifest. Against the local
`obo_test`/`dbo.items` fixture it covers:

- `SELECT 1` and non-ASCII SQL text (the W path);
- `dbo.items` ids 1–3 with `nvarchar` (`café`), `decimal(18,4)`
  (`12.3400`/`-.5000`/`.0000`), `datetime2(3)`, `uniqueidentifier`,
  `varbinary` (`00 FF 10`), SQL NULL and an empty binary value;
- typed text/int/binary/NULL/decimal parameters, including `café`, a surrogate
  pair, empty text/binary (distinct from NULL), and the full i64 range, a
  bind-count mismatch, a declared-scale rejection and a `float` decimal
  rejection;
- SQLSTATE `42S02`/native 208 for a missing table, UTF-8 in a non-ASCII
  diagnostic, empty/NUL/oversize SQL input errors, a bad DSN (`IM002`);
- non-ASCII column metadata (`SELECT 1 AS [café]`);
- 1 MiB text and binary parameters (the >4000/8000 `ColumnSize` path);
- large values that cross the 4096-byte read chunks, embedded NUL across
  chunks (6,001 UTF-8 bytes), a 10,000-byte binary value, an exactly 16 MiB
  value ending in a two-byte character, and a value one byte over the 16 MiB
  cap that must report `value exceeds the 16 MiB cap`;
- ascending column reads (`text(1)` before `text(0)`), a stale-row check after
  `SQL_NO_DATA`, and the full i64 min/max parameter and result range;
- `datetimeoffset` readable as exact text;
- close/double-close and closed-`Rows` accessors;
- the B1 parent-close fixture: `conn.close()` first, then `rows.is_open()` is
  false and `rows.text(0)` is a clean `statement's connection is closed` error
  (no signal), followed by a clean `rows.close()`;
- a standalone ASan harness that links the shim sources directly and repeats
  the parent-close sequence, so a use-after-free is detected outside the obo
  image;
- `OBO_CHECK=1` census: a clean run exits 0; a dropped Connection reports
  `Connection 1` (exit 8), a dropped Stmt reports `Stmt 1` (exit 8), `exit(0)`
  runs no census, use after close exits 7, and
  `OBO_CENSUS_FAULT=underflow` exits 4.

The runner asserts all 21 section markers, so a section that is accidentally
left out of `run_all` fails the suite instead of passing silently. It also
checks `signature_check: declarations-only`, the two declaration-file hashes,
the manifest verify, `libodbc.so.2` in `ldd`, and builds/runs the ASan harness.
`obo/experimental/db/tests/test_db_adapters.py` invokes this suite too, or
prints a visible `mssql live: skipped` line when no DSN is configured.

## Unsupported / follow-ups

These are deliberately outside the first slice and need separate review:

- **Transactions.** Autocommit is on; there is no `begin`/`commit`/`rollback`
  helper, no savepoints and no isolation-level control.
- **Multiple result sets.** `exec` uses the first result set. `SQLMoreResults`
  is not exposed, and a batch with several statements is not drained.
- **`datetimeoffset`/`date`/`time` typed accessors.** They read as exact text;
  there is no typed value type or timezone arithmetic.
- **`money`/`smallmoney` typed accessor.** Exact text and `decimal()` work.
- **Floats.** No `float`/`real` parameter kind and no `SqlArg.Float` bridge;
  `SQL_ARG_FLOAT_UNSUPPORTED` is reserved for a future template bridge.
- **Output parameters, streaming LOBs, scrollable cursors, connection
  pooling, prepared-statement caching, bulk copy, SQL Server TDS and the
  `SQLSERVER_ODBC` template bridge.** The `sqldialect` renderer exists, but
  this package does not bind `Sql` fragments yet.
- **A deterministic fake driver.** The live server is the first gate; a fake
  `libodbc` cbind is future work.
- **Threads.** The binding is single-threaded; ODBC handles are thread-confined
  and the shim side table is process-wide.
- **Automatic close on drop.** Dropping a record leaks the handle until the
  census reports it, exactly as for files.
