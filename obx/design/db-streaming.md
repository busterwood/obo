# Streaming query results into the program's records

Status: merged into main (2026-11-06); Oracle's round trips sized by bytes and its fetching ahead are on branch `oracle-fetch` (section 7, "Oracle over a slow network"). The design is
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
| per fill | as many packets as the buffer needs | as many rows as the buffer holds, parking on the socket | one FETCH of the rows still wanted, at least the rows of about 256 KB at the widest row (`Conn.rowsFor`; `Fetch Size` when set); rows beyond the buffer wait for the next fill. The next FETCH is sent as soon as a reply's rows arrive (fetching ahead), so it is usually on the wire before the fill that needs it |
| state while open | in the `Session` (`RowReader`) | in the stream (the pending PGresult) and libpq | in the stream (the last fetch) and the `Session` (`streamCursor`; `pending`: a FETCH sent ahead whose reply is not read) |
| early `close()` | ATTENTION, then the reply read up to the DONE with the attention flag | `PQcancel`, then the results read to the end (at most 5 s, as for a cancelled query) | the next request first reads past a FETCH reply still on the wire (`settle`), then closes the server cursor (piggybacked, as every cursor close) |
| stream dropped without close | the next call on the connection does the ATTENTION first; `idle()` is false until then | the next call cancels first (libpq says a command is active); `idle()` is false until then | the same as close: the next request reads past the outstanding FETCH reply, then closes the cursor; `idle()` stays true |
| more result sets | the first is streamed; at its end the rest of the reply is read and skipped; their errors come from `fill` | one statement only (as `query`) | one statement |
| error after some rows | rows then `Err` (8134 at row 5,000: 4,999 rows, then the error) | rows then `Err` (22012: 4,999 rows) | the FETCH that fails brings no rows: with 3,449-row fetches (76-byte rows) 3,449 rows, then ORA-01476, also when that FETCH was sent ahead |
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

Measured 2026-11-06 13:53 on CPU 10 (11 idle), branch `streaming` at
deb05bea. The load average was 4.1 at the start and 12.6 at the end (other
agents' builds on other CPUs, and the servers on this machine): take the
times as indicative; the memory numbers do not depend on it.

| client | backend | streamed 100,000 | streamed 1,000,000 | buffered 1,000,000 |
|---|---|---:|---:|---:|
| SQL Server | C | 137 ms, 11.2 MB | 668 ms, 11.2 MB | 1,590 ms, 364.5 MB |
| SQL Server | native | 139 ms, 11.7 MB | 612 ms, 11.6 MB | 1,678 ms, 365.3 MB |
| PostgreSQL | C | 114 ms, 8.0 MB | 990 ms, 8.0 MB | 1,012 ms, 109.0 MB |
| PostgreSQL | native | 85 ms, 8.4 MB | 813 ms, 8.4 MB | 1,475 ms, 109.4 MB |
| Oracle | C | 120 ms, 11.0 MB | 873 ms, 11.0 MB | 1,923 ms, 377.9 MB |
| Oracle | native | 110 ms, 11.2 MB | 810 ms, 11.2 MB | 1,328 ms, 410.0 MB |

- Peak RSS stays flat while streaming: the same at 100,000 and 1,000,000
  rows (8 to 12 MB, the program and the buffer). Buffered grows with the
  result: 109 MB (libpq's PGresult) to 365 to 410 MB (Cells per value).
- Streaming is faster everywhere: 2.4x to 2.7x for SQL Server, 1.0x to
  1.8x for PostgreSQL (libpq's per-row PGresult costs about as much as
  the whole buffered result's parse via C), 1.6x to 2.2x for Oracle (the Oracle rows: measured again on 2026-11-06 at 14:32, load 0.5, with round trips sized by bytes and fetching ahead; with 1,024-row fetches and no fetching ahead they were 1,460 and 1,346 ms streamed, 2,114 and 2,323 ms buffered).
- The checksums are equal in every row (rows, ids, name lengths, amounts).

### Oracle over a slow network

Oracle reads a result in FETCH round trips, so latency multiplies by
their number. Two changes (branch `oracle-fetch`, design/oracle.md "As
built: round trips sized by bytes"): a FETCH asks for the rows of about
256 KB at the widest row the describe allows (2,202 rows for this
query, whose widest row is 119 bytes), instead of Fetch Size 100 (the
buffered query) or the buffer's 1,024 (the stream); and a stream sends its
next FETCH as soon as a reply's rows arrive, so the server makes batch
k+1 while the program decodes batch k.

`python3 obx/bench/dbstream/run.py --latency 0,0.12,1.75`: no root for
tc/netem, so `delayproxy.py` sits in front of the server's port and
delays every chunk in each direction (one thread per direction, select
and a short spin; its own cost is about 0.3 ms a round trip, so the
one-way delays 0.12 and 1.75 ms give the measured 1 and 5 ms round trips:
the mean of 500 `SELECT 1`). Query time without the login, least of 3
interleaved runs on CPU 10 (11 idle; the proxy on CPUs 4 to 8, the server
unpinned), load average 0.5 to 0.7; 2026-11-06 14:20. Old: Fetch Size 100
and no fetching ahead (a stream still asks for its buffer's 1,024 rows);
new: the defaults.

| round trip | backend | mode | rows | old: trips, ms | new: trips, ms | new / old |
|---|---|---|---:|---:|---:|---:|
| 0.38 ms (direct) | C | buffered | 100,000 | 1,001, 540 | 47, 177 | 0.33 |
| | C | buffered | 1,000,000 | 10,001, 5,192 | 456, 1,820 | 0.35 |
| | C | stream | 100,000 | 99, 124 | 47, 84 | 0.68 |
| | C | stream | 1,000,000 | 978, 1,218 | 456, 818 | 0.67 |
| | native | buffered | 100,000 | 1,001, 469 | 47, 133 | 0.28 |
| | native | buffered | 1,000,000 | 10,001, 4,820 | 456, 1,302 | 0.27 |
| | native | stream | 100,000 | 99, 108 | 47, 77 | 0.71 |
| | native | stream | 1,000,000 | 978, 1,072 | 456, 745 | 0.69 |
| 0.98 ms | C | buffered | 100,000 | 1,001, 1,086 | 47, 215 | 0.20 |
| | C | buffered | 1,000,000 | 10,001, 10,569 | 456, 2,202 | 0.21 |
| | C | stream | 100,000 | 99, 180 | 47, 117 | 0.65 |
| | C | stream | 1,000,000 | 978, 1,784 | 456, 1,152 | 0.65 |
| | native | buffered | 100,000 | 1,001, 988 | 47, 175 | 0.18 |
| | native | buffered | 1,000,000 | 10,001, 10,089 | 456, 1,779 | 0.18 |
| | native | stream | 100,000 | 99, 168 | 47, 112 | 0.67 |
| | native | stream | 1,000,000 | 978, 1,667 | 456, 1,103 | 0.66 |
| 4.93 ms | C | buffered | 100,000 | 1,001, 4,766 | 47, 394 | 0.08 |
| | C | buffered | 1,000,000 | 10,001, 48,920 | 456, 3,855 | 0.08 |
| | C | stream | 100,000 | 99, 568 | 47, 295 | 0.52 |
| | C | stream | 1,000,000 | 978, 5,633 | 456, 2,879 | 0.51 |
| | native | buffered | 100,000 | 1,001, 4,595 | 47, 347 | 0.08 |
| | native | buffered | 1,000,000 | 10,001, 46,725 | 456, 3,368 | 0.07 |
| | native | stream | 100,000 | 99, 558 | 47, 290 | 0.52 |
| | native | stream | 1,000,000 | 978, 5,462 | 456, 2,847 | 0.52 |

Peak RSS does not change: 10.5 to 11.2 MB streamed, 46.5 to 50.4 MB
buffered at 100,000 rows, 378 to 411 MB at 1,000,000 (the cells, not the
fetches). Rows per round trip: old 100 (buffered) and 1,010 to 1,022
(stream), new 2,128 to 2,193. The checksums are equal everywhere.

What fetching ahead alone gives (the stream, 1,000,000 rows, rows by
bytes with and without it, the same run conditions at 14:30):

| round trip | backend | without | with | saved |
|---|---|---:|---:|---:|
| 0.40 ms | C | 970 ms | 844 ms | 13 % |
| 0.40 ms | native | 903 ms | 783 ms | 13 % |
| 0.99 ms | C | 1,414 ms | 1,136 ms | 20 % |
| 0.99 ms | native | 1,297 ms | 1,112 ms | 14 % |
| 4.87 ms | C | 3,106 ms | 2,877 ms | 7 % |
| 4.87 ms | native | 3,066 ms | 2,880 ms | 6 % |

- The round trips dominate the buffered query: 10,001 at 100 rows. The
  byte target makes it 12 to 14 times faster at a 5 ms round trip and 5
  to 6 times at 1 ms.
- Fetching ahead hides the decoding of a batch behind the next round
  trip: up to the time the program spends on a batch (about 1.7 ms for
  2,193 rows). At 5 ms the round trip is the longer of the two, so what
  is left is about 456 round trips of 4.9 ms (2.2 s of the 2.9 s).
- The widest row is a bound: the bench's rows are about 30 bytes on the
  wire against 119 at most, so a 256 KB FETCH carries about 68 KB. A
  target measured from the rows actually received (after the first
  FETCH) would cut the round trips by about 4 here; left for later.

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
| Oracle's smallest FETCH | the rows of 256 KB at the widest row (2,202 for the bench's row, 21 for three full VARCHAR2(4000)), or Fetch Size when set | the buffer's size exactly (28,572 round trips for 200,000 rows with a buffer of 7); a byte key in the connection string |
| Oracle fetching ahead | on (`setFetchAhead`): the next FETCH goes when a reply's rows arrive | only when the buffer is larger than one FETCH; never |
| nullable columns | never refused at bind | refuse a non-Option read where the server says nullable (SQL Server, Oracle) |
| `idle()` after a dropped stream | false (SQL Server, PostgreSQL) until the next call cleans up | clean up in `idle()` |

## 10. Tests

| test | backends | what |
|---|---|---|
| `sqlserver_live_stream` | C, native; OBX_CHECK=1 | 20 checks: names, missing, ambiguous, type, wide int, NULL, every type (incl. nvarchar(max) longer than a packet), 200,000 rows with buffers 7 and 1,024 against the buffered query, rows kept across fills, next, early close, dropped, later error, no result set, by position, parameters, no handle left |
| `postgres_live_stream` | C, native; OBX_CHECK=1 | the same, and a pool lease |
| `oracle_live_stream` | C, native; OBX_CHECK=1 | the same, and no cursor left open (V$MYSTAT over repeated early closes, drops, errors and bind failures) |
| `oracle_live_fetch` | C, native; OBX_CHECK=1 | row widths and rows per round trip (narrow, three full VARCHAR2(4000)), a buffer of 7 with the byte target, Fetch Size 100 and fetching ahead off for the same rows, buffered round trips, an early close and a drop with a FETCH on the wire (the connection used after), an error in the FETCH sent ahead, no cursor left open over repeated early closes |
| `sqlserver_fake_stream` | C, native; OBX_CHECK=1; no server needed | a scripted server in 512-byte packets: rows spanning packets, NBCROW NULLs, nvarchar(max) chunks longer than a packet, an early close (ATTENTION seen and acknowledged, the connection reused), 300 mutated or cut replies through a buffer of 7: each ends in rows or an Err |
| `stream_conn_in_use` (error) | checker | the connection used while a stream is open |
| `stream_keep_row_borrow` (error) | checker | `&buf[0]` kept across the next fill |

Each live test exits 77 (skipped) without its DSN.
