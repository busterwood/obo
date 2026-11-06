# A native Oracle Database client

Status: stages O1 to O3 and O5 built (see the "As built" sections at the end);
items marked **provisional, for the user** are choices made to get going that
the user should confirm or change.

## Why and what

obx programs talk to SQL Server through `sqlserver` and `tds`, a client that
speaks the wire protocol itself (design/tds.md). This is the same for Oracle
Database: no Oracle Instant Client, no OCI, no C library except OpenSSL's
libcrypto (already a dependency through `tls`). Oracle publishes the protocol
only as code: the "thin" mode of python-oracledb (`src/oracledb/impl/thin`,
Apache-2.0 OR UPL-1.0) and node-oracledb 6 (`lib/thin`, the same design in
JavaScript). The port follows python-oracledb message by message; node-oracledb
is the second reading where Python is unclear; go-ora (MIT) only for behaviour
neither Oracle client has. NOTICE carries the attribution; each ported file
names its source in its header.

Scope: Oracle Database 12.1 and later (the 12c "TTC field version" floor of the
thin clients); plain TCP or TLS (`tcps`); password authentication with the 12c
verifier (PBKDF2-HMAC-SHA512) and the 11g one (SHA-1). Not Native Network
Encryption (NNE, Oracle's own encryption and checksumming instead of TLS): a
server that requires it is refused with a clear error.

## Layers

| module | what | like |
|---|---|---|
| `crypto` (new, small) | libcrypto bindings: SHA-1/SHA-512/MD5 digests, PBKDF2-HMAC-SHA512, AES-CBC encrypt/decrypt, random bytes | the extern pattern of `tls.obx` |
| `tns` | TNS packets (CONNECT, ACCEPT, REFUSE, REDIRECT, RESEND, DATA, MARKER, CONTROL); the TTC message codecs (universal integers, length-prefixed bytes, chunked long values); the value codecs (NUMBER, DATE, TIMESTAMP[ WITH TIME ZONE], BINARY_FLOAT/DOUBLE, BOOLEAN); the connect, protocol, data-types and auth exchanges; execute, fetch, commit, rollback, logoff; the reply decoder | `tds` |
| `oracle` | the program API: connection strings, connect, exec, query (buffered rows), run, transactions, close, errors; `sqldialect` values | `sqlserver` |

**provisional, for the user:** `crypto` as a public library module (other
protocols, such as a future PostgreSQL SCRAM client, need the same digests and
PBKDF2). The alternative is private externs inside `tns`.

Async model: the same as `tds`. The socket is a `net.Conn` (or a `tls.Conn`
over one); every read and write parks the calling task; the `Where` parameter
carries cancellation and the deadline; a wire error, a cancellation or a
timeout during a request marks the session broken, and the next call fails
with `E_CLOSED`-style errors instead of reading a half-finished reply.

## Message flow

Packets: an 8-byte header (length, packet checksum 0, type, flags, header
checksum 0). Until the ACCEPT the length is 16 bits followed by 16 zero bits;
after an ACCEPT with protocol version 315 or later the length is 32 bits.
DATA packets start with 16 bits of data flags (0x40 EOF: the server ends the
session).

Connect:

1. TCP connect (`net.dial`), TLS handshake when `tcps`.
2. CONNECT packet: version 319 wanted, 300 lowest; SDU and TDU 8192 (16-bit
   fields) and again as 32-bit; NSI flags "security renegotiation, NA
   disabled"; no out-of-band breaks; the connect descriptor
   `(DESCRIPTION=(ADDRESS=(PROTOCOL=tcp)(HOST=h)(PORT=p))(CONNECT_DATA=(SERVICE_NAME=s)(CID=(PROGRAM=p)(HOST=m)(USER=u))))`;
   a descriptor longer than 230 bytes follows in a DATA packet.
3. Reply: ACCEPT (protocol version, SDU, flags: "NA required" means NNE is
   required, refused), REFUSE (the listener's text, for example
   `(ERR=12514)` for an unknown service: an error with that ORA- code),
   REDIRECT (a new address and descriptor: reconnect there once), RESEND
   (send the CONNECT again).
4. TTC Protocol message (1): driver name; reply: server banner, the
   database character set id, the "FDO" block (national character set id),
   the server's compile-time and run-time capabilities. The TTC field version
   is the smaller of ours and the server's.
5. TTC DataTypes message (2): the client's capabilities and the 323-entry
   type conversion table copied from python-oracledb; the reply is skipped.
6. Auth phase one (function 118): user name and session data (terminal,
   program, machine, pid, OS user); reply: key/value pairs with the verifier
   type, `AUTH_SESSKEY`, `AUTH_VFR_DATA`, the PBKDF2 parameters.
7. Auth phase two (function 115): the encrypted session key, the encrypted
   password (and the 12c "speedy key"), the client character set 873
   (AL32UTF8), driver name and version, and `ALTER SESSION SET
   TIME_ZONE='+00:00'`; reply: the session data and `AUTH_SVR_RESPONSE`,
   which must decrypt to contain `SERVER_TO_CLIENT` (the server knows the
   password too).

Verifier arithmetic (from python-oracledb `crypto.pyx`): 12c: key =
PBKDF2-HMAC-SHA512(password, vfr_data + "AUTH_PBKDF2_SPEEDY_KEY", 64 bytes,
VGEN_COUNT iterations); hash = SHA-512(key + vfr_data)[0:32]. 11g: hash =
SHA-1(password + vfr_data) + 4 zero bytes. Session key part A = AES-CBC
decrypt(hash, AUTH_SESSKEY); part B random; combined key from both (MD5 for
48-byte keys, PBKDF2 with CSK_SALT and SDER_COUNT otherwise); the password is
sent AES-CBC encrypted under the combined key after 16 random bytes.

Requests after login are TTC function messages: message type 3, function
code, a sequence number (1..255, never 0), and from field version 18 a token
number. A request is one or more DATA packets; the reply is read message by
message until a STATUS (9), ERROR (4) or END_OF_RESPONSE (29) message ends
it.

- Query: EXECUTE (94) with PARSE|EXECUTE|FETCH, the SQL text, the bind
  metadata and values, and a prefetch of `fetchSize` rows. Reply:
  DESCRIBE_INFO (column metadata), ROW_HEADER / BIT_VECTOR / ROW_DATA per
  row, then ERROR with code 0 (more rows) or 1403 (no more rows), carrying the
  cursor id and the row count. More rows: FETCH (5) with the cursor id and
  the array size, until 1403. A BIT_VECTOR marks columns whose value repeats
  the previous row and is not sent again.
- DML and DDL: EXECUTE without FETCH; the ERROR message carries the row count.
- Cursors: each query leaves a server cursor; its id is queued and closed by a
  CLOSE_CURSORS piggyback (function 105, message type 17) in front of the
  next request, as python-oracledb does.
- Commit (14), rollback (15), logoff (9): a bare function message.
- Breaks: the server can answer with a MARKER packet (after an error in a
  multi-packet request); the client sends a RESET marker, reads until the
  server's RESET marker, and then reads the error as usual.

Reading a reply: python-oracledb decodes from a buffer that refills from the
socket in the middle of a value. `tns` instead decodes one whole message from
the bytes it has; when the message is cut off it reads another packet and
decodes that message again from its start. Row data is cut on row boundaries
by the server in practice, so the cost stays linear in the reply size.

## Types

| Oracle | obx (`sqldialect.Value` / `Rows` accessor) | notes |
|---|---|---|
| NUMBER, FLOAT, INTEGER | `Dec` (Decimal), `int` when integral and in range | base-100 digits, exact both ways; 1 to 40 digits on the wire, Decimal holds 38 (E_INEXACT beyond); exponent range 1e-130 to 1e125 |
| BINARY_FLOAT, BINARY_DOUBLE | `Float` | sign-flipped IEEE bytes, exact |
| VARCHAR2, CHAR, LONG | `Text` | the database character set must be AL32UTF8 (873); other sets are refused at connect |
| NVARCHAR2, NCHAR | `Text` | UTF-16BE (AL16UTF16, 2000) on the wire; strict decode |
| CLOB, NCLOB | `Text` | O4; fetched as LONG through a define, like python-oracledb's `fetch_lobs=False` |
| RAW, LONG RAW, BLOB (O4) | `Bytes` | |
| BOOLEAN (23ai) | `Bool` | |
| DATE | `time.LocalDateTime` (`localDateTime`), `time.Date` (`date`) | 7 bytes, no fraction |
| TIMESTAMP | `time.LocalDateTime` | 11 bytes, nanoseconds |
| TIMESTAMP WITH TIME ZONE | `time.OffsetDateTime` | 13 bytes: the UTC value plus hour and minute offset; a region id (named zone) is an E_TYPE error |
| TIMESTAMP WITH LOCAL TIME ZONE | `time.LocalDateTime` | in the session time zone, which login sets to UTC |
| ROWID, UROWID | `Text` | O2 |
| INTERVAL, objects, collections, JSON, VECTOR, XMLTYPE, REF CURSOR | E_TYPE error naming the type | later stages |
| NULL | `Null` | length 0 or 255 |

Binds: the SQL text uses Oracle's `:1`, `:2` (or any `:name`), and values
bind by position in the order given. `Int` and `Dec` bind as NUMBER, `Float`
as BINARY_DOUBLE, `Bool` as BOOLEAN on 23ai and as NUMBER 1/0 before, `Text`
as VARCHAR2 (LONG over 32767 bytes), `Bytes` as RAW (LONG RAW over 32767),
`Null` as a NULL VARCHAR2. **provisional, for the user:** time values bind
through `sqldialect.Value` as text today (Value has no time variants, as for
SQL Server); `oracle` adds typed bind helpers like `tds`'s `Param` only if
the user wants them.

## API (the `sqlserver` shape)

`oracle.connect(connStr, w)` → `Conn`; `exec`, `query` → buffered `Rows`,
`run` → affected rows, `transaction[T](body, w)`, `commit`/`rollback` inside
it, `idle()`, `inTransaction()`, `close()`, `openHandles()`. Errors are
`sqldialect.DbError` with the ORA- number as code and the server's message
(`ORA-00942: table or view does not exist`); the library's own failures use
the `E_*` codes. Oracle has no autocommit on the wire: every statement runs
in the session transaction; `exec`/`query`/`run` outside `transaction` commit
by sending the COMMIT option with the execute (python-oracledb's
`autocommit=True`), so a program sees the SQL Server behaviour.
**provisional, for the user.**

Connection strings, **provisional, for the user**: ODP.NET's keywords, parsed
like `sqlserver`'s (`keyword=value;`, quotes and braces):
`User Id`, `Password`, `Data Source` (`host[:port]/service` in EZConnect form,
or a full `(DESCRIPTION=...)`), `Connect Timeout` (seconds, default 15),
`Encrypt` / `Protocol=tcps`, `TrustServerCertificate`, `Fetch Size` (rows per
round trip, default 100). `masked()` hides the password.

## Testing

- Hermetic tests (no server): the codecs against byte vectors from
  python-oracledb's tests and its encoders (NUMBER edges: 0, -1, 38 and 40
  digits, 1e125, 1e-130, negative sentinel byte 102; dates and timestamps;
  floats), packet framing, the verifier arithmetic against vectors computed
  by the reference implementation, connection string parsing.
- Live tests (`obx/lib/tests/run/oracle_live_*.obx`): read the connection
  string from env `ORACLE_TEST_DSN` or `~/.obo-oracle-dsn`; exit 77 (skipped)
  when neither exists; never print it. The server is the Oracle Database Free
  container that `obx/tools/oracle_container.sh` creates (gvenzl/oracle-free,
  podman, 127.0.0.1:1521, PDB FREEPDB1, user APP with a random password).
- Every test runs under `OBX_CHECK=1` (no leaks, no open handles) on the C and
  the native backend.

## Stages

- **O1** connect, login (12c and 11g verifiers), a query of simple columns, close.
- **O2** binds of every `Value`, every mapped type both ways, errors with
  ORA- codes, transactions, multi-round-trip fetches, closing mid-result,
  server gone, wrong password.
- **O3** TLS (`tcps`), with the same certificate options as `sqlserver`.
- **O4** LOBs: CLOB/NCLOB/BLOB through LONG defines, then LOB locators and
  streaming reads and writes.
- **later** REF CURSORs and implicit results, PL/SQL OUT binds, intervals,
  JSON and VECTOR, array DML, statement cache, DRCP and pooling, Advanced
  Queuing, NNE, Kerberos/RADIUS/token auth, out-of-band breaks (cancel of a
  running statement), the 23ai "fast auth" and pipelining.

## Out of scope

Native Network Encryption and checksumming; character sets other than
AL32UTF8 for the database character set; Oracle client wallets (`cwallet.sso`;
PEM files work through `tls`); `tnsnames.ora` lookup.

## Language gaps

None so far; workarounds will be listed here if any are needed.

## As built (O1 to O3)

Live against Oracle AI Database 26ai Free (23.26.3, the gvenzl/oracle-free
slim image) on the C and the native backend, under `OBX_CHECK=1`:
`oracle_live_basic` (connect, errors with ORA- codes, DML, transactions,
5000-row fetches in rounds of Fetch Size, wrong password, a killed session,
closing mid-result), `oracle_live_types` (every mapped type both ways,
NUMBER edges 1e125 and 1e-130, 38 digits, NUMBER(10,2) rounding, 4000-byte
VARCHAR2, NVARCHAR2, CHAR padding, RAW, DATE, TIMESTAMP(9), TIMESTAMP WITH
TIME ZONE, BOOLEAN, nulls) and `oracle_live_tls`; offline `tns_codec` and
`oracle_connstr`.

What differs from the design above, and what was learned:

- **Fast auth and END_OF_RESPONSE.** A 23ai ACCEPT carries flags2
  FAST_AUTH (0x10000000) and HAS_END_OF_RESPONSE (0x02000000). With fast
  auth the client sends one message (type 34) holding the Protocol request,
  the DataTypes request and auth phase one; the reply runs PROTOCOL,
  DATA_TYPES and the auth reply up to an END_OF_RESPONSE (29). With
  END_OF_RESPONSE on (protocol version 319 and the capability bits set in
  compile caps 40), ERROR and STATUS no longer end a reply: only
  END_OF_RESPONSE does. Older servers get the classic three exchanges.
- **The DataTypes table must be exact.** python-oracledb's table stores the
  extended types as N + 256; a copy without the +256 was accepted by the
  listener but made the server drop the connection on the first request
  after login ("Network protocol error on first data after new connect",
  ORA-28546 in the server trace). `tns`'s table is generated from
  `data_types.pyx` (322 entries).
- **Two length-prefixed byte forms.** Some fields are a ub4 length followed,
  when non-zero, by u8-length chunks (python's `read_bytes_with_length`:
  OIDs, the describe's current date, dcbqcky, error text, the row header's
  rowid, piggybacks 7 and 8); `tns` reads those with `str2`, the rest with
  the plain u8-length form. Mixing them up showed as "message type 0".
- **TLS renegotiation.** Over tcps the listener hands the connection to the
  database with a RESEND whose packet flags have 0x08: the TLS session ends
  there without close_notify and a fresh handshake runs on the same socket
  before CONNECT goes again (python's `renegotiate_tls`; `tls.Conn.detach`).
- **Typed binds** (the provisional point under Types, decided for now):
  `oracle.Param` is `Of(Value)`, `Date(time.Date)`, `DateTime(time.LocalDateTime)`,
  `Offset(time.OffsetDateTime)` and `Number(tns.Num)` (NUMBER beyond
  Decimal's range); `query` and `run` take `&[Value]`, `queryWith` and
  `runWith` take `&[Param]`.
- **Connection strings:** a provisional `CA File` key (a PEM of the CAs to
  trust, for a self-signed server certificate) next to the design's keys.
- **close() and logoff():** `close()` (and dropping a Conn) closes the
  socket without a LOGOFF round trip, which cannot park in a destructor;
  `logoff(w)` sends LOGOFF first. **provisional, for the user.**
- LOB columns: their cells are E_TYPE errors until O4; the query itself
  succeeds.
- `crypto` is a public module (provisional, as proposed above).

The test container: `obx/tools/oracle_container.sh create` (rootless podman,
127.0.0.1:1521 TCP and 127.0.0.1:2484 TCPS, random passwords, the DSN in
`~/.obo-oracle-dsn` mode 600), `tls` (run by create: orapki from a throwaway
container of the full image makes a self-signed CN=localhost wallet; the CA
goes to `~/.obo-oracle-ca.pem` and the TLS Data Source to `~/.obo-oracle-tls`),
`start` after a reboot, `stop`, `status`, `logs`, `rm`, and `grants` (run by
create: SELECT on V$MYSTAT, V$STATNAME and V$OPEN_CURSOR for APP, which the
cursor leak check of `oracle_live_out` reads).

## As built: OUT binds, REF CURSORs, implicit results (O5)

Branch `oracle-out`. Live on 23.26 Free on the C and the native backend
under `OBX_CHECK=1` (`oracle_live_out`; the placeholder parser also offline
in `tns_codec`).

API, the shape of `sqlserver.call`:

    conn.call(sql, args: &[Value], outs: &[Out], w): Result[Results, Error]
    conn.callWith(sql, params: &[Param], outs: &[Out], w)
    oracle.out(name, sqlType): Out              // OUT
    oracle.inOut(name, sqlType, value: Param): Out  // IN OUT
    Results{sets: []Rows, outputs: Rows, affected: int}

- **Binding** (provisional, for the user): `tns.placeholders` finds the
  statement's placeholders the way python-oracledb's StatementParser does
  (strings, q-strings, quoted names and comments skipped; each occurrence in
  SQL, each name once in PL/SQL; the ones after RETURNING ... INTO in DML
  marked). A placeholder whose name is an Out's (without ':', upper case
  unless written in double quotes) takes that Out; the others take args in
  order. An Out without a placeholder, two Outs of one name, an unknown
  type, too many or too few args are E_INPUT before anything is sent.
  sqlserver's outs come after the args by position; Oracle binds by
  position too, so naming the Outs keeps the args positional however the
  two are interleaved in the SQL.
- **Types and sizes** (provisional, for the user): `Out.sqlType` is the
  Oracle type: NUMBER[(p[,s])] (p and s only label the output column, so
  `decimal()` shows NUMBER(10,2) as 12.50), INTEGER, BINARY_DOUBLE,
  BINARY_FLOAT (bound as BINARY_DOUBLE: the widening is exact),
  VARCHAR2(n) / CHAR(n) / RAW(n) in bytes (`n CHAR`: 4n bytes),
  NVARCHAR2(n) / NCHAR(n) in UTF-16 units, DATE, TIMESTAMP, TIMESTAMP WITH
  TIME ZONE, BOOLEAN (23ai), SYS_REFCURSOR / REF CURSOR. The size is the bind
  buffer: a longer value is the server's ORA-06502 ("character string buffer
  too small"), and an IN OUT value longer than its size is E_INPUT. An OUT
  value the server still cut (its actual length is not 0) fails the call,
  never a shortened value.
- **Wire** (python-oracledb's messages/base.pyx): binds carry no direction;
  the server answers a PL/SQL execute with an IO_VECTOR giving each bind's
  (IN 32, OUT 16, IN OUT 48), then one ROW_DATA with the OUT and IN OUT
  values in bind order, each followed by its actual length. DML RETURNING
  binds are declared with the others but get no value in the request; each
  comes back as a count and that many values (one row of `outputs` per row
  changed). FLUSH_OUT_BINDS is answered with a FLUSH_OUT_BINDS request.
- **REF CURSORs**: an OUT cursor is sent as NULL (1, 0) and comes back as a
  describe and a cursor id. Its rows are fetched with an EXECUTE carrying
  FETCH alone on that cursor (python does this for a cursor without SQL),
  Fetch Size rows a round trip, and the cursor is closed by the
  CLOSE_CURSORS piggyback of the next request, also when a fetch fails (the
  query of an opened cursor can fail only then: ORA-01476 for 1/0) and when
  an earlier cursor of the call failed. `Results.sets` holds the cursors in
  the order of the Outs, then the implicit results (IMPLICIT_RESULTSET: a
  describe and a cursor id each), after a query's own rows when the
  statement is a query. A cursor the PL/SQL never opened comes back with id
  0: E_INPUT "REF CURSOR :c was not opened by the PL/SQL". Cursor Outs are
  not columns of `outputs` (provisional). Strong REF CURSOR types work the
  same way (the describe gives the columns).
- **Leaks**: `oracle_live_out` runs twenty rounds of cursors, failing
  cursors, unopened cursors and implicit results and compares the session's
  "opened cursors current" before and after: equal (the count is taken after
  a first round, as PL/SQL keeps its own static SQL's cursors open; with the
  close left out the check sees 100 more).
- `query`/`run` with a PL/SQL block that has OUT binds now run (the values
  are dropped) instead of failing on the IO_VECTOR; any cursor such a reply
  returns is closed.

## As built: streams (2026-11-06)

Branch `streaming`; the shared design is design/db-streaming.md.

- `Conn.stream(sql, args, bind, read)` and `streamWith` (typed `Param`
  binds) give a `Stream[B, T]` holding `mut &Conn`. The execute is sent
  with no rows asked for (prefetch 0): it describes the columns for bind.
- Each `fill` whose rows are used up sends one FETCH for the rows still
  wanted, at least the connection's fetch size (`setFetchSize`, 100 by
  default); rows beyond the buffer wait in the stream for the next fill.
  So memory is bounded by the larger of the buffer and the fetch size.
- Rows are kept raw (`Reply.raw`): each value's bytes appended to one
  buffer with its start and length, a BIT_VECTOR repeat copied from the
  row before (the last row of the previous fetch seeds the next). A row
  cut off at a packet's end is taken back and decoded again. NUMBER is
  decoded straight from its bytes to `int` or `Decimal` (`numberFixed`;
  `decodeNumber` beyond 38 digits).
- Early close, an error, or a dropped stream: the cursor is closed with
  the next request (piggybacked). `Session.streamCursor` holds the cursor of
  an open stream, so a dropped stream's cursor is closed by whatever request
  comes next. `oracle_live_stream` checks "opened cursors current" before
  and after rounds of early closes, drops, errors and bind failures: equal.
- An error during a FETCH (ORA-01476 at row 5,000) comes with none of that
  FETCH's rows: with 100-row fetches the stream gives 4,900 rows, then the
  error, though the error's row count says 4,999.
- LOB columns are skipped (their columns read as nothing, so bind refuses
  them).

Next stages: O4 LOBs (LONG defines, then locators; LOB OUT binds),
PL/SQL collections and records as binds, array DML (executemany), REF
CURSOR IN binds, nested cursors (CURSOR(...) columns), intervals, JSON and
VECTOR, REDIRECT packets (RAC and shared servers), out-of-band breaks,
statement cache, pooling and DRCP, Advanced Queuing, NNE.
