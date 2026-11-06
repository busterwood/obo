# A native Oracle Database client

Status: design for stages O1 to O3; items marked **provisional, for the user**
are choices made to get going that the user should confirm or change.

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
