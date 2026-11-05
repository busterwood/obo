# A native TDS client: SQL Server and Sybase in obx

Status: plan, 2026-11-04. T0 (TLS) is in main; T1 is built on branch
`tds-t1` (section 8, 2026-11-05). The open decisions in section 7 are the
user's; T1 took D1 and D3 provisionally.

## 1. Why

`lib/mssql.obx` reaches SQL Server through ODBC: unixODBC, Microsoft's
ODBC Driver 18 and a dynamic link. Every call that can wait is a `blocking`
extern, which runs on the helper pool of C threads (design/concurrency.md,
C1c) while the task parks.

That works, but it costs on several fronts:

- **Concurrency:** a waiting query occupies a helper thread. The helper
  pool's size caps how many queries a process has in flight.
- **Deployment:** a program needs the driver installed. It cannot be a
  single static binary.
- **Types:** decimals and money are converted through text or C buffers.
- **Reach:** Sybase ASE is not covered at all.

A client written in obx that speaks TDS (Tabular Data Stream) over
`net.Conn` fits the concurrency model the way the HTTP server does:

- **One OS thread** runs an epoll loop with stackful tasks.
- **Code is written in blocking style.** A read that would block parks the
  task.
- **Many queries can be in flight**, one connection each, with no helper
  threads.
- **Cores are used through worker processes** (`task.workers`), each with
  its own pool. There is no shared state.

Sources:

- [MS-TDS], Microsoft's Open Specifications, covered by the Open
  Specification Promise. The client implements TDS 7.4, plus TDS 8.0
  (strict encryption).
- Sybase's *TDS 5.0 Functional Specification*, for ASE.
- FreeTDS is LGPL. Its behaviour (`tsql`, `freebcp`) is useful to compare
  against, but its code is not to be translated into this Apache-2.0
  project.

## 2. How it fits the async model

```
 HTTP handler task ──acquire──▶ Pool (Shared[PoolState]) ──▶ Lease (owns a Conn)
        │                           waiters: Promise[Lease], parked
        ▼
 lease.query(sql, args) ──▶ RPC sp_executesql ──▶ packets ──▶ Transport
        │                                                     ├─ Plain(net.Conn)
        ▼                                                     └─ Tls(tls.Conn)
 Rows: token stream decoded as rows arrive (park on the socket between reads)
```

- **Tasks and parking.** Every network call parks only the calling task.
  This is the `net` rule, so `connect`, `query` and `step` take the caller's
  `Where`, as `postgres.obx` does. No call holds a `Shared.with`.
- **Futures.** Running queries side by side is ordinary task code:
  `s.start(fn(id) { pool.acquire()?.query(...) }, id)` per query, then
  `task.all`. Each future takes its own connection from the pool.
- **Pool.** The same shape as `postgres.Pool`:
  - a counted handle around `task.Shared[PoolState]`;
  - `acquire` parks on a `Promise[Lease]` when the pool is full;
  - a `Lease` returns its connection when dropped.
  - Connections that are not idle (a transaction left open, a result not
    drained, an attention pending) are reset or closed rather than reused.
  - A worker process has its own pool, as with Postgres.
- **The HTTP server.** One process with an epoll loop serves the requests,
  and a handler takes a lease, queries and answers. `http_gate.py --db`
  gets a SQL Server variant: requests per second and latency at 4 and 16
  connections, against the ODBC module (helper pool) and against Postgres.
- **One request per connection.** Multiple Active Result Sets (MARS)
  multiplexes several requests on one connection; it is not planned. Tasks
  plus a pool give the same concurrency with less protocol. A connection
  is busy until its response is read to the final DONE.

### Cancellation and deadlines

Cancellation reaches the client through `task.within(ms, ...)` and a
cancelled task. TDS cancels a running request with an ATTENTION packet
(type 6), and the server then answers with a DONE carrying the attention
bit. Two problems follow:

1. **A cancelled task's network calls return at once with ECANCELED**
   (net.obx), so the cancelled task itself cannot send the ATTENTION and
   read up to the acknowledgement.
2. **`drop` never parks** (the library rule "closing never parks"), so
   dropping a `Rows` before its end cannot drain the stream either.

Proposed answer, with no runtime change:

- The connection records that it is dirty: "attention owed" or "stream not
  drained".
- The next user does the cleanup before its own request: the next query on
  the same `Conn`, or the pool when the `Lease` comes back. It sends the
  ATTENTION if still needed, drains to the acknowledging DONE under its own
  deadline, and closes the connection if that fails.
- A pool can also hand dirty connections to a cleanup task of its own, so
  the next `acquire` does not wait for them.

The alternative is a runtime "shield": a region in which a cancelled task
may still do I/O, with its own short deadline (Python's
`asyncio.shield`, Trio's shielded cancel scopes). That would be a
`task.obx` and runtime change, and it is open decision D2.

## 3. Layers

Each layer is a module, tested on its own.

| layer | module (proposed) | contents |
|---|---|---|
| transport | `tds` | `Plain(net.Conn)` or `Tls(tls.Conn)` behind a closed type (obx has no interface values); buffered read and write |
| packets | `tds` | the 8-byte header (type, status with EOM, length, SPID, packet id); messages split and joined at the negotiated packet size (ENVCHANGE) |
| pre-login and TLS | `tds` | the PRELOGIN options (version, encryption, instance, thread id, MARS off); the TLS handshake carried in PRELOGIN packets through tls.obx's memory mode; then TLS on the socket (Encrypt=on/mandatory), or login only (Encrypt=off as the server allows); TDS 8.0 strict runs TLS first, with ALPN `tds/8.0` |
| login | `tds` | LOGIN7 (SQL authentication; the password scrambled as the spec says, inside TLS); LOGINACK, ENVCHANGE (database, language, collation, packet size), FEATUREEXTACK (UTF-8 support, session recovery off). Later: NTLM (SSPI token), Azure AD tokens (FEDAUTH) |
| requests | `tds` | SQLBatch (the text in UTF-16LE with the ALL_HEADERS transaction descriptor); RPC `sp_executesql` with typed parameters from `sqldialect.Value`; ATTENTION |
| tokens | `tds` | an incremental decoder over the packet stream, returning one token at a time: COLMETADATA, ROW, NBCROW (null bitmap), DONE / DONEPROC / DONEINPROC (row counts, more results, errors, attention), ERROR, INFO, ENVCHANGE, RETURNSTATUS, RETURNVALUE, ORDER, LOGINACK, FEATUREEXTACK |
| types | `tds` | TYPE_INFO and values, below |
| API | `sqlserver` (D1) | `connect`, `Conn`, `exec`, `query`, `run`, `transaction`, `Rows`, `Pool`, `Lease`, as postgres.obx and mssql.obx |
| Sybase | `sybase` (T4) | TDS 5.0: its login record, LANGUAGE and DBRPC requests, ROWFMT/ROWFMT2, PARAMFMT, CAPABILITY, its types. Shares the transport, the packets and the pool |

**The parser must never trap on server input.** The decoder checks every
length and count against the bytes it has and returns an error otherwise.
A malformed or hostile packet gives `Err`, never an exit 10 or a huge
allocation. The token decoder is fuzzed (section 5).

### Types

| SQL Server | obx | notes |
|---|---|---|
| bit, tinyint, smallint, int, bigint (and INTN) | `int` / `bool` | |
| real, float (FLTN) | `float` | exact: the bits are read as they are |
| decimal(p,s), numeric | `Decimal` | sign byte and a 4, 8, 12 or 16-byte little-endian magnitude: exactly obx's 38-digit Decimal, with no text step |
| money, smallmoney | `Money` / `Decimal` | an int64 or int32 count of 1/10,000 |
| date, time(n), datetime2(n), datetimeoffset(n), datetime, smalldatetime | `time.Date`, and new time-of-day and date-time types (D5) | 100 ns precision; datetime's 1/300 s ticks rounded as the server does |
| nchar, nvarchar, ntext | `str` | UTF-16LE to UTF-8, strict, as mssql.obx's `fromUtf16` |
| char, varchar, text | `str` | by the column's collation: UTF-8 collations (2019+) as they are; code page 1252 and Latin-1 through tables; other code pages an error at first (D4) |
| binary, varbinary, image | bytes (`str`) | |
| (max) types, xml | `str` / bytes | PLP: partially length-prefixed chunks, decoded as they arrive; a size limit as in mssql.obx's `setMaxValue` |
| uniqueidentifier | `str` | the canonical text (mixed-endian layout) |
| sql_variant, table-valued parameters, UDTs | later | |

Parameters go out typed. `Value.Dec` is sent as decimal(p, s) from the
value. Text is sent as nvarchar, with max beyond 4,000 characters. NULL
goes out with the type that is declared. SQL text is never interpolated.

### Rows: buffered and streamed

Two shapes:

- `query` returns all rows in client memory, as `postgres.Rows` does. A
  `Rows` value then does not hold the connection.
- `stream` steps through the rows as they arrive, as `mssql.Stmt.step`
  does. This suits large results.

A streamed `Rows` needs the connection while it is alive, which can be
done in two ways:

- **Today:** the `Rows` takes the `Conn` (a move) and gives it back on
  `finish()` or when it is dropped. In the drop case the connection is
  marked dirty, as in section 2.
- **With the borrow-class types proposal** (branch `borrow-types`):
  `Rows` holds a `mut &Conn` as a second-class value. The checker then
  ensures the connection is not used while rows are pending, with no move
  and no runtime state. This is a direct use of that proposal (D3).

## 4. Connection strings

Two forms are accepted:

- the ADO.NET form: `Server=host,1433;Database=...;User Id=...;
  Password=...;Encrypt=...;TrustServerCertificate=...;
  Connect Timeout=...;Application Name=...`
- the ODBC keywords that mssql.obx is given: `Server`, `UID`, `PWD`,
  `Database`, `Encrypt`, `TrustServerCertificate`. Anything specific to
  the `Driver=` part is ignored.

Because of the second form, one DSN (`MSSQL_TEST_DSN`) drives both the ODBC
module and the TDS client, which is what the differential tests need.
`Encrypt` follows ODBC Driver 18:

- `yes`/`mandatory` (the default): TLS throughout, certificate verified.
- `no`/`optional`: login only, if the server allows it.
- `strict`: TDS 8.0.
- `TrustServerCertificate=yes` maps to tls.obx's explicitly named option
  that skips verification.

The password is never logged or kept in an error message, and errors
show the string with the password masked.

## 5. Testing

- **Hermetic, on every gate run:**
  - **Encoder and decoder tests** on byte strings from the spec's examples.
  - **A recorded-exchange server:** a small Python or obx test server
    replays captured TDS conversations (login, a batch, every type, errors,
    multiple results, attention), and the client must produce the expected
    values. The captures are made once against a real server, with any
    secrets removed.
  - **A fuzz run** on the token decoder with mutated captures. Malformed
    input must give an error: no trap, no hang, no leak.
- **Live, when `MSSQL_TEST_DSN` (or `~/.obo-mssql-dsn`) exists.** The tests
  check only that the DSN exists and pass it through the environment;
  they never print it.
  - **A differential suite:** the same queries through `mssql` (ODBC) and
    through the TDS client must give identical values, column types and
    errors.
  - **Coverage:** every type with its edge values (decimal(38,0) at its
    limits, money's range, datetime2(7), max-length nvarchar, empty and
    NULL, surrogate pairs), multiple result sets, RAISERROR at several
    severities, PRINT, row counts, transactions (commit, rollback, a
    deadlock victim), deadlines and cancellation mid-result, and a
    connection dropped by the server.
- **Load:** `http_gate.py --db` with SQL Server, the ODBC module against
  TDS at 4 and 16 connections and 100 and 1,000 clients. TDS should need
  no helper threads, and its latency should be no worse.
- **Sybase:** the same differential suite against a real ASE, if one can
  be had (D6). FreeTDS's `tsql` serves as a second client to compare with.

## 6. Stages

Each stage is one branch, merged after the full gate.

| stage | contents | done when |
|---|---|---|
| T0 | `lib/tls.obx` (OpenSSL; socket and memory modes), https in http.obx | branch `tls` (running) |
| T1 | transport, packets, PRELOGIN with TLS inside, LOGIN7 (SQL authentication), SQLBatch, the core tokens, int/float/nvarchar/varchar (UTF-8 collations), buffered `query`, `exec`; the connection string | logs in to the live server under Encrypt=yes, runs batches, the first differential cases pass |
| T2 | every type of the table, collations 1252 and Latin-1, RPC `sp_executesql` with parameters, multiple results, ERROR/INFO with their state and severity, row counts, `transaction[T]`, ENVCHANGE | the full type matrix and the error cases match ODBC (built: section 9) |
| T3 | streamed `Rows`, ATTENTION with deadlines and cancellation (section 2), `Pool`/`Lease`, the http_gate `--db` SQL Server load, PERFORMANCE.md | load numbers against ODBC; cancellation tests; no leak under OBX_CHECK=1 |
| T4 | Sybase ASE over TDS 5.0 (`sybase` module) | the differential suite against ASE (needs a server) |
| T5 | bulk copy (BULK LOAD, packet type 7) for fast inserts; NTLM; TDS 8.0 strict; Azure AD tokens; table-valued parameters | each as needed |

T1 is about one agent's run, and T2 and T3 one each. T4 depends on having a
server.

## 7. Open decisions (the user's)

- **D1. Module names, and the future of `mssql`.** Decided (the user,
  2026-11-05): `tds` and `sqlserver`, as built in T1. Recommendation was: a new
  `tds` module (the protocol) plus `sqlserver` (the API). Keep `mssql`
  (ODBC) until the TDS client matches it on the differential suite and
  the load test, then decide whether `mssql` stays as an ODBC fallback or
  goes.
- **D2. Cleanup after cancellation.** Recommendation: a dirty connection
  is cleaned by its next user or by the pool (section 2), with no runtime
  change. A runtime "shield" would be a task.obx change.
- **D3. Streamed rows.** Recommendation: buffered `query` plus a `stream`
  that takes the `Conn` and gives it back, now. Move to a borrow-class
  `Rows` if that proposal is adopted.
- **D4. Narrow code pages.** Recommendation: UTF-8 collations, 1252 and
  Latin-1 first; others an error naming the code page, added from
  Microsoft's published tables when someone needs them.
  Decided and built (2026-11-05): `codepage.obx` (decode, encode, supported)
  with tables generated from Python's codecs by `gen_codepages.py` (a new
  single-byte code page is one line there); `tds` decodes varchar in code
  pages 1252 and 28591 (Sybase's iso_1), any other non-UTF-8 code page is
  `E_TYPE` for that cell, naming it. The five bytes cp1252 leaves undefined
  (0x81, 0x8D, 0x8F, 0x90, 0x9D) decode to U+0081, U+008D, U+008F, U+0090
  and U+009D: the live server's own `UNICODE()` and its conversion to
  nvarchar give exactly these. (The ODBC driver gives `?` for them, so the
  differential test leaves them out and `sqlserver_live_basic` checks them
  against `UNICODE()`.) `encode` is there for T2's varchar parameters.
- **D5. Dates and times.** time.obx has `Date` (year, month, day) and
  `Time`, an instant in seconds and nanoseconds. It has no time of day, no
  local date-time without a zone (`datetime2`, Postgres `timestamp`) and no
  offset (`datetimeoffset`, `timestamptz`). Recommendation: add
  `time.TimeOfDay` (nanoseconds since midnight), `time.LocalDateTime` (a
  `Date` and a `TimeOfDay`) and `time.OffsetDateTime` (a `LocalDateTime`
  and an offset in minutes) in T2. Postgres would then use them too.
  Decided (the user, 2026-11-05) and built in time.obx: offsets are
  minutes within ±23:59; `eq` on OffsetDateTime is the same local
  date-time and offset, `sameInstant` the same instant. T2 maps the SQL
  types onto them.
- **D6. A Sybase ASE server for tests.** There is none on this machine (no
  Docker either). Without one, T4 is written from the specification and
  tested only by recorded exchanges.

## 8. Stage T1, as built (2026-11-05)

`lib/tds.obx` (the protocol) and `lib/sqlserver.obx` (the API), with D1's
names and D3's buffered `query`; `mssql` is unchanged. Written from MS-TDS.

- **Transport:** packets of the negotiated size (ENVCHANGE 4; the server
  caps 32767 at 16384), messages read whole into memory up to `maxReply`
  (256 MiB). Under TLS each packet is written by itself: SQL Server resets
  the connection when one TLS record holds the end of a packet and the
  start of the next, which is what one write of a multi-packet message
  gives. This was found live; every batch over one packet failed before.
- **PRELOGIN and TLS:** the handshake runs in tls.obx's memory mode, its
  records carried in PRELOGIN packets, then `tls.over` takes the socket.
  `Encrypt=yes` needs the server to answer ON or REQ; `Encrypt=no` with
  OFF encrypts the login only (`detach` after LOGIN7), with NOT_SUP
  nothing. `strict` (TDS 8.0) is an `E_INPUT` "not supported yet".
- **LOGIN7:** SQL authentication, TDS 7.4, the password scrambled per the
  spec, OptionFlags1 0xE0 and OptionFlags2 0x03 (the live `@@OPTIONS` matches
  ODBC's), the UTF-8 feature extension.
- **Tokens:** COLMETADATA, ROW, NBCROW, DONE/DONEPROC/DONEINPROC (counts),
  ERROR, INFO, LOGINACK, ENVCHANGE (database, packet size, collation,
  transactions, the rest kept), FEATUREEXTACK, ORDER, RETURNSTATUS,
  TABNAME/COLINFO skipped. RETURNVALUE, ALTMETADATA/ALTROW and
  DATACLASSIFICATION are `E_PROTOCOL` "not supported yet". Every type's
  metadata is parsed, so any result set can be read; a cell of a type T1
  does not decode is `E_TYPE` naming the type, only when read.
- **Errors:** the server's first ERROR is the call's `Err` (its number as
  `code`); `E_IO` 1023 and `E_PROTOCOL` 1022 are new in sqldialect. A wire
  or protocol failure marks the session broken.
- **Tests:** hermetic `tds_codec`, `tds_malformed` (truncations, 4000
  mutations, random bytes: two traps found and fixed), `sqlserver_connstr`,
  `sqlserver_fake` (a scripted server task, plain TDS); live
  `sqlserver_live_basic` and `sqlserver_live_diff`. The differential cases
  (ints at their limits, real and float, bit, NULL of every type,
  nvarchar with surrogate pairs, UTF-8 varchar, (max) values, binary,
  `@@OPTIONS`, 3000 generated rows, a system catalog) give no difference
  against ODBC. All pass under OBX_CHECK=1 on the C and native backends.
- **D4, built after T1:** varchar in code pages 1252 and ISO-8859-1 (see D4
  in section 7); the live differential adds 1252 cases (the 250 defined
  bytes 0x01 to 0xFF, text, (max)) with no difference against ODBC.
- **Left for T2:** RPC with parameters, every result set of a batch, the
  other types (D5), RETURNVALUE, `transaction[T]`. For T3: ATTENTION,
  streamed rows, pools. The recorded-exchange server of section 5 is the
  scripted `sqlserver_fake` for now; TLS inside PRELOGIN is tested live
  only.

## 9. Stage T2, as built (2026-11-05)

Branch `tds-t2`. No change to the compiler, time.obx, decimal or sqldialect;
`Cell` gained variants (`Dec`, `Date`, `Time`, `DateTime`, `Offset`,
`Guid`), so a program matching on `tds.Cell` needs the new arms or `else`.

- **Types:** decimal and numeric become `Decimal` straight from the sign
  byte and the 4-, 8-, 12- or 16-byte magnitude (`fromFraction`, no text in
  between); money (high int32, then low uint32) and smallmoney are
  `Decimal`s of scale 4. date is days since 0001-01-01; time(n) 3 to 5
  bytes of 10^-n s; datetime2 that time, then the date; datetimeoffset the
  UTC time and date, then the offset in minutes, turned into the local
  date-time at that offset (`time.OffsetDateTime`, D5). datetime is days
  since 1900-01-01 and 1/300 s ticks; **its rounding:** milliseconds =
  (ticks * 10 + 1) / 3, integer division, which gives .000, .003, .007,
  .010, ...: what `CONVERT(..., 121)`, `CONVERT(..., 126)` and the ODBC
  driver give. (CAST to datetime2(7) rounds to 100 ns instead, .0033333;
  the differential test checks every tick of a second and the last 300 of
  a day against CONVERT style 126.) smalldatetime is days since 1900 and
  minutes. uniqueidentifier is canonical upper-case text from the
  mixed-endian bytes (the first three groups little-endian). xml is the
  PLP UTF-16 text, a byte order mark dropped. Bytes framed right but out of
  range (a time past midnight, a day past 9999-12-31, 39 digits, an offset
  of 24 hours) are a `Cell.Failed` naming why; a decimal TYPE_INFO of a bad
  length or precision is `E_PROTOCOL`. sql_variant, UDTs (CLR types:
  geography, geometry, hierarchyid) stay `E_TYPE` naming the type.
  `tds.cellText` writes every cell as SQL Server and ODBC do.
- **RPC:** `sp_executesql` by its ProcID (10), `@stmt` and `@params`
  unnamed and positional, then `@p1`, `@p2`, ... Int bigint, Float float
  (the exact bits: obx has no float-to-bits builtin, so `tds.bitsOfFloat`
  computes them; NaN, the infinities and subnormals are refused), Bool
  bit, Text nvarchar(4000) or nvarchar(max) as PLP beyond 4,000 UTF-16
  units (always nvarchar: `codepage.encode` is not needed for parameters),
  Bytes varbinary(8000) or varbinary(max), Dec decimal(p, s) with p and s
  from the value (decimal(4,2) for 12.50; 39 digits or more than 38
  fraction digits are an error). NULL is `NULL_TYPE`, nvarchar(4000): it
  converts to every type but binary ones (257 from the server, live: ODBC
  fails alike); sql_variant was tried and converts to nothing implicitly,
  and `sp_describe_undeclared_parameters` (ODBC's SQLDescribeParam) fails
  for temporary tables and for a parameter used twice, and would doom an
  open transaction under XACT_ABORT. `call` declares OUTPUT parameters
  from SQL type names (`typeInfoOf`: the integer types, bit, real, float,
  money, smallmoney, datetime, smalldatetime, uniqueidentifier, date, xml,
  decimal/numeric(p, s), time/datetime2/datetimeoffset(n),
  (n)char/(n)varchar/binary/varbinary(n or max)).
- **Results:** `queryAll` keeps every result set and every DONE,
  DONEPROC and DONEINPROC with a count, in order, each tied to the result
  set it ends (`StatementCount{token, rows, resultSet}`); sp_executesql's
  own DONEPROC is left out. A reply may end with DONEPROC.
- **ERROR and INFO:** all kept with number, state, class, text, server,
  procedure and line (`errors()`, `diagnostics()`, reset by each call);
  the first ERROR is the Err. The server sends RAISERROR at severity 10 as
  class 0.
- **RETURNVALUE and RETURNSTATUS:** RETURNVALUE is parsed with its
  TYPE_INFO (an encrypted one is `E_PROTOCOL`); status 1 values are
  `Results.outputs`, one row. Every RETURNSTATUS is kept; sp_executesql's
  own (the last) is dropped. Through sp_executesql the server sends no
  RETURNSTATUS for an EXEC inside (found live), so `returnStatus` is for
  batches; with parameters write `EXEC @r = p ...` and an Out `@r`.
- **transaction[T]:** a Transaction Manager request (packet type 14)
  begins (BEGIN_XACT, the descriptor arrives in ENVCHANGE 8 and goes in
  every ALL_HEADERS after it), commits (7) on Ok and rolls back (8) on Err,
  keeping the body's errors and infos over the rollback's. If the server
  ended the transaction itself (XACT_ABORT, a COMMIT in the SQL, an error
  that dooms it), there is no rollback, and an Ok is `E_INPUT`, as nothing
  was committed. The body is `fn(mut &Conn)`, not a closure over the
  connection as in postgres: obx lets one place change the connection at a
  time, so the body is lent it.
- **ENVCHANGE:** database, language, charset, sort locale and flags,
  collation, packet size, the transaction descriptors (begin, commit,
  rollback, enlist, defect, ended), mirror partner, user instance and reset
  acknowledgements are kept or applied; routing (20) fails `open` with a
  message naming it.
- **Tests:** `tds_codec` decodes every type from bytes (ordinary values,
  NULLs, the limits, bad content) and checks the RPC, parameter, TM and
  float-bit bytes; `tds_malformed` adds a T2 reply's truncations and 4000
  mutations (every decoded cell formatted), RETURNVALUE and decimal cases,
  fuzzed type names and GUID text: no trap. `sqlserver_fake` checks two
  RPCs and two transactions byte by byte against the scripted server.
  Live, `sqlserver_live_diff` gives no difference against ODBC on the full
  matrix (section 3's types at their limits and NULL, every scale of time,
  datetime2 and datetimeoffset, decimal(38) at both ends and scale 38, every
  datetime tick), on parameters of every Value variant (selected and
  stored), on result sets and counts, and on errors (syntax, unknown
  procedure, unique index, NOT NULL, conversions, overflow, RAISERROR at
  severities 0 to 18 and states 0 to 255, in a parameter, mid-batch); the
  typed values match the server's CONVERT, and the errors its
  ERROR_*() in a CATCH. One disagreement was ours (a subnormal float
  parameter, fixed); ODBC's Err keeps only the first of several errors.
  All pass under OBX_CHECK=1 on both backends.
- **Proposals:** `Value` variants for dates and times (`Date`, `Time`,
  `DateTime`, `Offset`) and for GUIDs, so that parameters carry their
  type (they go in as text now, which the server converts); and a typed
  NULL (`Value.NullOf(type)` or a `Param` type) for binary columns.
- **Left for T3:** streamed `Rows` (D3), ATTENTION with deadlines and
  cancellation, `Pool`/`Lease`, the load test. Later: sql_variant, table
  parameters, UDTs, routing, ALTMETADATA/ALTROW (COMPUTE), DATACLASSIFICATION,
  an RPC to a procedure by name (its RETURNSTATUS and outputs without
  sp_executesql), Encrypt=strict.
