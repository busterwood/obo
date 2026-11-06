# Streaming query results into the program's records

Status: built on branch `streaming` (2026-11-06), not merged. The design is
the user's "option A": hand-written mappers, library only, no language
change. Names and signatures marked **provisional** are for the user to
confirm (section 9).

## 1. What it is

A query result is decoded straight into the program's own record type, a
bounded batch at a time, into a buffer the caller owns. The client never
holds the whole result.

```obx
type Order = value record { id: int; customer: int; name: str; amount: Decimal }
type OrderCols = value record { id: int; customer: int; name: int; amount: int }

fn bindOrder(c: &sqlrows.Columns): Result[OrderCols, sqlrows.Error] {
    return Ok(OrderCols{id: try c.index("id", Kind.Int), customer: try c.index("customer_id", Kind.Int),
                        name: try c.index("customer_name", Kind.Text), amount: try c.index("amount", Kind.Decimal)})
}

fn readOrder(b: &OrderCols, r: &sqlrows.RowView): Result[Order, sqlrows.Error] {
    return Ok(Order{id: try r.int(b.id), customer: try r.int(b.customer),
                    name: try r.text(b.name), amount: try r.decimal(b.amount)})
}

var src = try conn.stream(sql, [Value.Int(7)], bindOrder, readOrder)   // holds conn
var buf = array(1024, Order{id: 0, customer: 0, name: "", amount: 0.00m})
while true {
    let n = try src.fill(mut &buf)
    if n == 0 { break }
    for o in buf[0..n] { ... }
}
```

The same `bindOrder` and `readOrder` work with `sqlserver`, `postgres` and
`oracle`: `Columns` and `RowView` are one type for all three.

## 2. Two phases

| phase | when | what the program writes | what it costs |
|---|---|---|---|
| bind | once, before the first row | `fn(&Columns): Result[B, Error]`: each field to a column index, `index(name, kind)` | name matching and type checks, once |
| read | per row | `fn(&B, &RowView): Result[T, Error]`: the record from accessors by index | the accessors the record uses; nothing else |

`B` is the program's own binding (a record of ints). No name is looked up
per row. There is no `Value` or `Cell` per value: each accessor decodes the
value's bytes as received. Columns the mapper never reads are never
decoded (their bytes are only skipped).

## 3. Columns and RowView (module `sqlrows`)

`sqlrows` is a new module (not in `sqldialect`): `sqldialect` is values,
errors and SQL text for every adapter, sqlite and mssql included;
`sqlrows` is result reading for the streaming clients and imports `time`.
Each client re-exports the types as aliases (`sqlserver.Columns`,
`postgres.RowView`, `oracle.Kind`, ...), the same types.

| type | members |
|---|---|
| `Kind` | `Int Decimal Float Text Bytes Bool Date Time DateTime OffsetDateTime` |
| `Column` | `name`, `typeName` ("decimal(12,2)", "numeric(12,2)", "NUMBER(10,0)"), `kinds` (bits), `nullable` (`NULLABLE_YES`, `_NO`, `_UNKNOWN`); `wire size precision scale aux` for the client |
| `Columns` | `index(name, kind): Result[int, Error]`, `at(i, kind): Result[int, Error]`, `count()`, `get(i)`, `list()`, `names()`, `clone()` |
| `RowView` | `int decimal float text bytes bool date timeOfDay localDateTime offsetDateTime` by index, each with an `...OrNull` form giving an `Option`; `isNull(i)`, `rowNumber()`, `columnCount()` |
| `Codec` | ten function values, one per kind: how a client decodes a value's bytes |

`RowView` is a value record of borrows: the client's received bytes, the
value spans of the current row (start, length, -1 for NULL), the column
descriptions and the client's `Codec`. An accessor checks the index and
NULL, then calls the codec on the value's bytes. A codec error gets the
column name and row number added.

## 4. Matching rules (all clients)

| rule | as built |
|---|---|
| names | ASCII case ignored, underscores ignored: `customer_id` = `CUSTOMER_ID` = `customerId`. Other bytes compared as they are |
| renaming | SQL aliases only; no annotations |
| missing column | `E_COLUMN` (1024) at bind: `no column "email" (case and underscores ignored); the columns are: ID, Customer_Id, ...` |
| two columns match | `E_COLUMN` at bind, naming both (only when that name is asked for) |
| extra columns | allowed, never decoded |
| by position | `at(i, kind)`: the same type check, no name |
| type | checked once at bind against the description: `E_TYPE`, `column CUSTOMERNAME (nvarchar) cannot be read as Decimal; it reads as text` |
| NULL | a non-Option accessor on NULL: `E_NULL` at that row, `column note is NULL at row 2: read it with textOrNull into an Option` |
| nullable columns | never refused at bind, on any client (below) |

Why nullable is not checked at bind: SQL Server and Oracle describe nearly
every computed column as nullable (`a + 1`, `CAST`, aggregates, `CASE`), and
Postgres says nothing. Refusing a plain field for a nullable column would
force `Option` on most expressions. `Columns.get(i).nullable` has what the
server said, for a program that wants to check.

Kinds per server type:

| kind | SQL Server | PostgreSQL | Oracle |
|---|---|---|---|
| Int | tinyint, smallint, int, bigint, bit; decimal/numeric(p, 0) with p <= 18 | int2, int4, int8; numeric(p, 0) with p <= 18; numeric without typmod (each value checked) | NUMBER(p, 0) with p <= 18; NUMBER without precision (each value checked); BINARY_INTEGER |
| Decimal | decimal, numeric, money, smallmoney, the integers | numeric, the integers | NUMBER, FLOAT(b), BINARY_INTEGER |
| Float | real, float | float4, float8 | BINARY_FLOAT, BINARY_DOUBLE |
| Text | nchar, nvarchar, ntext, char, varchar, text (by collation), xml, uniqueidentifier | text, varchar, bpchar, name, and every type without a case here (its text form) | VARCHAR2, CHAR, NVARCHAR2, NCHAR, LONG, ROWID |
| Bytes | binary, varbinary, image | bytea | RAW, LONG RAW |
| Bool | bit | bool | BOOLEAN (23 and up) |
| Date | date | date | DATE (each value checked: midnight) |
| Time | time(n) | time | - |
| DateTime | datetime2, datetime, smalldatetime | timestamp | DATE, TIMESTAMP, TIMESTAMP WITH LOCAL TIME ZONE |
| OffsetDateTime | datetimeoffset | timestamptz | TIMESTAMP WITH TIME ZONE |

Oracle NUMBER without precision: Oracle describes `COUNT(*)`, `SUM`,
`ROWNUM`, `level`, literals and most arithmetic this way, so refusing `Int`
would leave them no integer field short of a `CAST`. It reads as `Int` with
each value checked (`E_TYPE`, `12.5 is not an int`, with column and row).
`NUMBER(p, 0)` with p of 19 and up (`INTEGER` is `NUMBER(38)`) is refused,
as the rule says: `CAST(x AS NUMBER(18))` maps it. PostgreSQL's
unconstrained numeric (`sum(int8)`) follows the same rule.

## 5. Per client

| | SQL Server (`sqlserver` over `tds`) | PostgreSQL (`postgres` over libpq) | Oracle (`oracle` over `tns`) |
|---|---|---|---|
| mechanism | the reply read a packet at a time; ROW/NBCROW tokens scanned into value spans; a row may span packets (the rest of a packet is joined to the next) | libpq single-row mode (`PQsetSingleRowMode`): one PGresult per row, its values copied into one row string; text format | FETCH round trips; ROW_DATA kept as the values' bytes (BIT_VECTOR repeats copied from the row before, across fetches too) |
| per fill | as many packets as the buffer needs | as many rows as the buffer holds, parking on the socket | one FETCH of the rows still wanted, at least the connection's fetch size (`setFetchSize`, 100); rows beyond the buffer wait for the next fill |
| state while open | in the `Session` (`RowReader`) | in the stream (the pending PGresult) and libpq | in the stream (the last fetch) and the `Session` (`streamCursor`) |
| early `close()` | ATTENTION, then the reply read up to the DONE with the attention flag | `PQcancel`, then the results read to the end (at most 5 s, as for a cancelled query) | the server cursor closed with the next request (piggybacked, as every cursor close) |
| stream dropped without close | the next call on the connection does the ATTENTION first; `idle()` is false until then | the next call cancels first (libpq says a command is active); `idle()` is false until then | the next request closes the cursor; `idle()` stays true (nothing is pending on the wire) |
| more result sets | the first is streamed; at its end the rest of the reply is read and skipped; their errors come from `fill` | one statement only (as `query`) | one statement |
| error after some rows | rows then `Err` (8134 at row 5,000: 4,999 rows, then the error) | rows then `Err` (22012: 4,999 rows) | the FETCH that fails brings no rows: with 100-row fetches 4,900 rows, then ORA-01476 |
| using the connection while open | refused by the checker (the stream holds `mut &Conn`) | the same (`mut &Conn`; `&Conn` methods are refused too) | the same |
| chunked rows | - | libpq 17's `PQsetChunkedRowsMode` is not used: this machine has libpq 16 and the extern would not link; obx cannot call a symbol looked up at run time | - |

Rows before an error: when an error comes after rows in the same `fill`,
the rows are returned first and the error comes from the next `fill`
(after which `fill` returns 0). Every error ends the stream and cleans the
connection at once (ATTENTION, cancel, cursor close).

## 6. Keeping rows

| what | kept past the next fill? |
|---|---|
| a record copied out of the buffer: `append(mut &list, buf[i])`, `let o = buf[i]` | yes: every accessor returns an owned value (`text` is a `str` of its own) |
| a borrow of the buffer: `let first = &buf[0]` | no: the next `fill(mut &buf)` is refused by the checker while it is live (`stream_keep_row_borrow`) |
| the `RowView` | no: it exists only while `read` runs |

There is no zero-copy `&str` accessor. One would make every record that
holds it borrow the client's buffer, so it could not outlive the batch; it
can be added later as a separate, opt-in accessor.

## 7. Memory and time

`obx/bench/dbstream/run.py`: 1,000,000 rows of (bigint, int, text,
decimal(12,2)), every value decoded into an `Order` and folded into a
checksum (equal across modes), buffer 1,024 rows, peak RSS of the process
(VmHWM), least of 3 interleaved runs pinned to one CPU. The servers run on
the same machine.

RESULTS_TABLE

## 8. API as built

All three clients have the same shape (`B`: the binding, `T`: the record):

```obx
pub fn (c: mut &Conn) stream[B, T](sql: str, args: []Value, bind: fn(&Columns): Result[B, Error],
                                   read: fn(&B, &RowView): Result[T, Error], w: Where): Result[Stream[B, T], Error]
pub fn (s: mut &Stream[B, T]) fill(out: mut &[T], w: Where): Result[int, Error]    // 0: the end
pub fn (s: mut &Stream[B, T]) next(w: Where): Result[Option[T], Error]
pub fn (s: Stream[B, T]) close(w: Where): Result[Unit, Error]
pub fn (s: &Stream[B, T]) columns(): Columns      // a copy
pub fn (s: &Stream[B, T]) rowsRead(): int
```

Also `postgres.Lease.stream` and `oracle.Conn.streamWith` (typed `Param`
binds); `sqlserver.Conn.streaming()`.

`Stream` is a value record holding `mut &Conn`, a borrow-class type
(design/borrow-types.md). Notes from building it:

- `args` is taken (`[]Value`), not borrowed: a borrowed argument would be a
  second root of the stream, and `&[...]` written in the call is a
  temporary, which a `let` of the stream cannot hold.
- `columns()` returns a copy: a method may not lend from a receiver that
  holds borrows (friction 3 of batches.md).
- A value record holding borrows has no `drop`, so the work of a dropped
  stream is done by the connection's next call, as in section 5.
- The producer shape is the one batches.md found: `fill(out: mut &[T])`
  into the caller's buffer, a count back, here with a `Result` because a
  database fill can fail. The clients import no batch library.

## 9. Provisional, for the user

| item | as built | alternatives |
|---|---|---|
| names | `stream`, `Stream`, `fill`, `next`, `close`, `Columns.index`, `Columns.at`, `RowView`, `Kind`, module `sqlrows` | `query` with a mapper; `rows`; `Source` |
| `fill` signature | `fill(out: mut &[T], w): Result[int, Error]` | `Result[Option[int], Error]` |
| errors after rows | rows first, the error from the next fill | the error at once, the partial batch lost |
| `args` | taken: `[]Value` | `&[Value]` with a separate `let` for the arguments |
| Oracle unconstrained NUMBER | reads as `Int`, each value checked | refused, `CAST` needed |
| Oracle's smallest FETCH | the connection's fetch size (100) | the buffer's size exactly (28,572 round trips for 200,000 rows with a buffer of 7) |
| nullable columns | never refused at bind | refuse a non-Option read where the server says nullable (SQL Server, Oracle) |
| `idle()` after a dropped stream | false (SQL Server, PostgreSQL) until the next call cleans up | clean up in `idle()` |

## 10. Tests

| test | backends | what |
|---|---|---|
| `sqlserver_live_stream` | C, native; OBX_CHECK=1 | 20 checks: names, missing, ambiguous, type, wide int, NULL, every type (incl. nvarchar(max) longer than a packet), 200,000 rows with buffers 7 and 1,024 against the buffered query, rows kept across fills, next, early close, dropped, later error, no result set, by position, parameters, no handle left |
| `postgres_live_stream` | C, native; OBX_CHECK=1 | the same, and a pool lease |
| `oracle_live_stream` | C, native; OBX_CHECK=1 | the same, and no cursor left open (V$MYSTAT over repeated early closes, drops, errors and bind failures) |
| `sqlserver_fake_stream` | C, native; OBX_CHECK=1; no server needed | a scripted server in 512-byte packets: rows spanning packets, NBCROW NULLs, nvarchar(max) chunks longer than a packet, an early close (ATTENTION seen and acknowledged, the connection reused), 300 mutated or cut replies through a buffer of 7: each ends in rows or an Err |
| `stream_conn_in_use` (error) | checker | the connection used while a stream is open |
| `stream_keep_row_borrow` (error) | checker | `&buf[0]` kept across the next fill |

Each live test exits 77 (skipped) without its DSN.
