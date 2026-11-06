# A native PostgreSQL client

Status: design (2026-11-06), branch `pgwire-design`. Nothing is built yet.
The stage briefs are in [pgwire-stages.md](pgwire-stages.md). Items marked
**for the user** are the user's to decide; everything else is decided here,
each with a one-line reason.

## 1. Why

obx talks to PostgreSQL through libpq (`lib/postgres.obx`). SQL Server
(`tds`, `sqlserver`) and Oracle (`tns`, `oracle`) have clients that speak
their wire protocol in obx. PostgreSQL should too:

| reason | evidence |
|---|---|
| libpq's single-row mode makes a PGresult per row | streamed, client cycles a row: PostgreSQL 2,172 (native) and 2,561 (via C); Oracle 1,169, SQL Server 1,290 (design/db-streaming.md, section 7) |
| a stream via libpq is no faster than the buffered query via C | 621 ms streamed against 824 ms buffered (via C); libpq's per-row cost eats the gain |
| early close needs PQcancel and a drain | Execute with a row limit gives chunked fetches; closing the portal ends a stream cleanly |
| programs need libpq.so.5 | the native client needs only what `tls` and `crypto` already link (section 21, note 1) |
| pipelining | the protocol allows it; libpq's async API hides it |
| one client model | the same Transport, Session, raw rows and RowView pattern as `tds` and `tns` |

## 2. Scope

In scope:

| area | what |
|---|---|
| protocol | Frontend/Backend protocol 3.0 (196608) |
| transport | TCP (IPv4, IPv6, names through `net.dial`), Unix-domain sockets (path and Linux abstract `@name`) |
| encryption | SSLRequest then TLS 1.2/1.3 through `tls`, every libpq `sslmode` |
| authentication | trust, peer (nothing to send), cleartext password, MD5, SCRAM-SHA-256, SCRAM-SHA-256-PLUS (`tls-server-end-point`) |
| connection strings | libpq's keyword=value and URI forms, the PG* environment variables, `~/.pgpass`, multiple hosts tried in order |
| queries | simple Query (scripts), extended protocol (Parse, Bind, Describe, Execute, Sync, Flush, Close) |
| results | text format, every type; typed decoding of the types `postgres` maps today |
| streaming | Execute with a row limit, PortalSuspended, fetch-ahead, early close by Close portal |
| cancellation | CancelRequest on a second connection, for task cancellation and deadlines |
| errors | ErrorResponse and NoticeResponse fields, SQLSTATE |
| later stages | COPY, LISTEN/NOTIFY, a pipelined batch API, the service file, the remaining libpq options (section 7.6) |

Non-goals:

| not done | why |
|---|---|
| GSSAPI encryption (GSSENCRequest) and GSSAPI, SSPI, Kerberos authentication | needs a GSSAPI library; a server that asks for them gets a clear error |
| replication connections (`replication=`) | another protocol on top |
| protocol 3.2 (PostgreSQL 18: cancel keys up to 256 bytes) | the local server is 16; libpq 18 still asks for 3.0 by default. The reader accepts BackendKeyData keys of 4 to 256 bytes anyway |
| direct TLS (`sslnegotiation=direct`, PostgreSQL 17, ALPN `postgresql`) | saves one round trip at connect; later (P5) |
| binary result format | section 9.3: text is exact and needs no extra round trip |
| a statement cache (named prepared statements) | section 9.1; a later option, measured first |
| fully static executables | `tls` and `crypto` still link OpenSSL (section 21, note 1) |

Servers: written against PostgreSQL 16 (the local server is 16.15, Ubuntu,
`password_encryption` scram-sha-256, `ssl` on). Expected to work with 12 and
later (SCRAM needs 10; numeric `Infinity` text needs 14). Nothing from 17 or
18 is used. What 17 and 18 change, for reference:

| version | change | effect here |
|---|---|---|
| 17 | `sslnegotiation=direct`: TLS at once, ALPN `postgresql` | refused with E_INPUT until P5 |
| 17 | libpq `PQcancelCreate`: cancel requests encrypted like the connection | we send cancel requests in clear, as libpq 16's `PQcancel` (section 13) |
| 17 | libpq `PQsetChunkedRowsMode` | not relevant: Execute's row limit does the same on the wire |
| 18 | protocol 3.2: `BackendKeyData` and `CancelRequest` keys of 4 to 256 bytes; `min_protocol_version`, `max_protocol_version` | we ask for 3.0; the keys are read with their length anyway |
| 18 | MD5 password authentication deprecated (warnings) | still supported |

## 3. Layers

| module | what | like |
|---|---|---|
| `net` (P0 additions) | Unix-domain sockets (`connectUnix`, `listenUnix`); cleanup I/O that ignores the task's cancellation, bounded in time (`readFor`, `writeFor`, `connectFor`, `connectUnixFor`); `setKeepAlive` | |
| `tls` (P0 additions) | `Options.verifyName` (verify-ca), `Options.sni` (sslsni=0), `Engine.endPoint()` (channel binding data), `Conn.readFor`, `Conn.writeFor` | |
| `os` (P0 additions) | `userName()` (the default user), `homeDir()` (`~/.pgpass`, `~/.postgresql/`) | |
| `crypto` (P0 additions) | `sha256`, `hmacSha256`, `pbkdf2Sha256` (on the helper pool), `base64`, `unbase64` | |
| `pgwire` (new) | connection strings, env, `.pgpass`; the Transport (socket or TLS, inbox, message framing); startup, SSLRequest, authentication (SCRAM, MD5, cleartext); the Session state machine; messages; simple and extended queries; raw rows; the value codecs (text decoding, parameter encoding); cancel requests | `tds`, `tns` |
| `postgres` (rewritten on pgwire) | the program API, unchanged: `connect`, `Conn`, `Rows`, `Stream`, `Pool`, `Lease`, transactions, `openHandles` | `sqlserver`, `oracle` |
| `postgres_libpq` (the old `postgres.obx`, renamed) | the libpq client, kept as the differential test's oracle and a fallback | |
| `sqlrows` | unchanged: `Columns`, `Column`, `RowView`, `Codec`, `Kind` | |
| `sqldialect` | unchanged: `Value`, `DbError`, `Sql`, the decimal helpers | |

What moves from today's `postgres.obx`:

| piece | goes to |
|---|---|
| libpq externs, `finish`, `flush`, `abort`, `settle`, `cancel`, `nextResult`, `Res` | `postgres_libpq` only |
| the type OIDs, `describe`, `typeText`, `kindsOf`, the text codecs `pInt` ... `pOffset`, `pgCodec`, `param`, `floatText` | `pgwire` (public: the codecs are shared by `postgres` and the tests) |
| `Conn`, `Rows`, their accessors, `DecimalType`, `decimalParam`, `transaction`, `Stream`, `Pool`, `Lease`, `PoolStats`, `handOn` | stay in `postgres`, over `pgwire.Session` instead of libpq handles |
| the connection string check (`PQconninfoParse`) | `pgwire.parseConfig` |

**Module names, for the user.** Recommended: `pgwire` (protocol), `postgres`
(API, now native), `postgres_libpq` (the old client). Programs and the pool
do not change. Alternatives: (a) keep `postgres` on libpq and add the
native client as `pg` (programs opt in; two APIs to keep alike); (b)
`pgproto` or `pq` instead of `pgwire`. The recommendation follows
`tds`/`sqlserver` and `tns`/`oracle`.

**How long `postgres_libpq` stays, for the user.** Recommended: until P4 is
merged and the differential test has run clean in every gate for two weeks;
then move it to `archive/`. It is the oracle for every type and error the
native client decodes.

## 4. The public API of `postgres`

Unchanged (names, behaviour; the tests' expected output stays, apart from
the lines in "Changes"; the receivers are `mut &Conn` and `mut &Lease` and
`transaction`'s body takes the connection, `fn(mut &Conn)`, see "As built
(P2)"):

| kind | members |
|---|---|
| types | `Value`, `Error`, `Conn`, `Rows`, `DecimalType`, `Pool`, `Lease`, `PoolStats`, `Columns`, `RowView`, `Kind`, `Stream[B, T]` |
| constants | `CONNECTION_OK` 0, `CONNECTION_BAD` 1, `PGRES_EMPTY_QUERY` 0, `PGRES_COMMAND_OK` 1, `PGRES_TUPLES_OK` 2, `PGRES_FATAL_ERROR` 7, `PGRES_SINGLE_TUPLE` 9, `OID_BOOL` ... `OID_TIMESTAMPTZ` (the 17 OIDs) |
| functions | `openHandles()`, `connect(conninfo, w)`, `decimalParam(d, p, s)`, `newPool(conninfo, size)` |
| `Conn` | `close()`, `serverVersion()`, `idle()`, `exec(script, w)`, `query(sql, args, w)`, `querySql(q, w)`, `transaction[T](body, w)`, `stream[B, T](sql, args, bind, read, w)` |
| `Rows` | `close()`, `rowCount()`, `columnCount()`, `affected()`, `columnName(c)`, `columnType(c)`, `columnTypmod(c)`, `decimalType(c)`, `isNull(r, c)`, `text(r, c)`, `int(r, c)`, `bool(r, c)`, `float(r, c)`, `bytes(r, c)`, `decimal(r, c)`, `decimalOrNull(r, c)`, `value(r, c)` |
| `Pool`, `Lease` | `stats()`, `acquire(w)`; `exec`, `query`, `querySql`, `stream`, `transaction` |
| `Stream` | `columns()`, `rowsRead()`, `fill(out, w)`, `next(w)`, `close(w)` |

Additions (new names only; nothing existing changes for them):

| addition | why | stage |
|---|---|---|
| `Conn.inTransaction(): bool` | `oracle` has it; the pool and tests read the transaction status | P2 |
| `Conn.parameter(name): Option[str]` | the server's ParameterStatus values (`TimeZone`, `server_version`, ...) | P2 |
| `Conn.backendPid(): int` | from BackendKeyData; tests check cancels against `pg_stat_activity` | P2 |
| `Conn.lastServerError(): Option[pgwire.ServerError]` | every ErrorResponse field (detail, hint, position, constraint, ...), not only code and message | P2 |
| `Conn.roundTrips(): int`, `Conn.setFetchRows(n)`, `Conn.setFetchAhead(on)` | as `oracle`: measurement and tuning of streams | P3 |
| `Conn.batch(stmts, w)` | pipelining (section 14) | P4 |

Changes (the only ones):

| what | before (libpq) | after (native) | why |
|---|---|---|---|
| `libVersion()` | libpq's version | removed from `postgres`; stays in `postgres_libpq` | there is no libpq; a made-up number would mislead |
| the connection lost after connecting (socket error, server gone, TLS error) | code 7 `PGRES_FATAL_ERROR`, libpq's text | `E_IO` (1023), the connection broken (`idle()` false, later calls `E_IO`) | as `sqlserver` and `oracle`; a program can tell a dead connection from an SQL error |
| a malformed reply | libpq's text, code 7 | `E_PROTOCOL` (1022), broken | as `tds`, `tns` |
| a failed connect | code 1, libpq's text, state "" | code 1, libpq-style text, state the server's SQLSTATE when it sent one (28P01 for a wrong password) | the SQLSTATE is useful and free |
| COPY in `exec` or `query` | code 2 or 4 status, "unsupported result status 4 (COPY?)", the connection stuck in COPY | `E_INPUT` "COPY is not supported yet"; COPY FROM STDIN refused with CopyFail, COPY TO STDOUT read and dropped; the connection stays usable | P5 adds a COPY API |
| server notices (`RAISE NOTICE`) | printed on stderr by libpq's default notice processor | kept (the last 100) in the connection, never printed | a library does not write to stderr; `notices()` comes in P5 |
| a stream closed early | PQcancel, then the rest drained | Close portal and Sync (no cancel unless the server is slow to answer, section 12.5) | the point of Execute's row limit |
| a data-modifying statement with RETURNING closed early as a stream | cancelled, so rolled back if the cancel arrived in time | its portal closed and the implicit transaction committed (the statement had already run in full) | closing a stream means "no more rows", not "undo" |

## 5. Connection

### 5.1 Sequence

| step | client | server |
|---|---|---|
| 1 | connect: Unix socket `dir/.s.PGSQL.port`, or TCP to `hostaddr` or the host's addresses (`net.dial` order) | |
| 2 | TCP only, unless `sslmode=disable`: SSLRequest (Int32 8, Int32 80877103) | one byte: `S` or `N` (or an ErrorResponse: a pre-7.0 server; refuse) |
| 3 | after `S`: read exactly one byte, then hand the socket to `tls` (a byte already buffered after `S` is E_PROTOCOL: CVE-2021-23222) and run the handshake | |
| 4 | StartupMessage: Int32 length, Int32 196608, then name/value strings, then a 0 byte | |
| 5 | authentication exchange (5.4) | `R` messages, ending in AuthenticationOk (`R` 0) or ErrorResponse |
| 6 | | ParameterStatus `S` (any number), BackendKeyData `K`, NoticeResponse `N`, NegotiateProtocolVersion `v`, then ReadyForQuery `Z` |
| 7 | ready: transaction status from `Z` (`I`, `T`, `E`) | |

StartupMessage parameters, in this order:

| name | value | when |
|---|---|---|
| `user` | the user | always |
| `database` | dbname | always (default: the user) |
| `application_name` | `application_name`, else `fallback_application_name` | when set |
| `client_encoding` | `client_encoding` if set (`auto` counts as UTF8), else `UTF8` | always; libpq sends it only when set: obx strings are UTF-8 |
| `options` | `options` | when set |
| `datestyle`, `timezone`, `geqo` | `PGDATESTYLE`, `PGTZ`, `PGGEQO` | when set in the environment (libpq does the same) |

Each name and value is checked for NUL (E_INPUT).

`connect_timeout` (seconds; 1 counts as 2; 0, negative or unset: none)
bounds each host's attempt from step 1 to step 7, as libpq. The task's
deadline (`task.within`) and cancellation bound the whole connect.

### 5.2 sslmode

libpq's meanings exactly. "Plain" is a connection without TLS. Unix
sockets ignore `sslmode` (no SSLRequest).

| sslmode | first try | if the server says `N` | if the handshake or verification fails | certificate checked |
|---|---|---|---|---|
| `disable` | plain | - | - | - |
| `allow` | plain | - | if the server refuses the plain connection (any startup error), one new connection with TLS | as `require` on the retry |
| `prefer` (libpq's default) | TLS | continue plain on the same socket | one new plain connection | only when a root CA file exists (then as `verify-ca`) |
| `require` | TLS | fail | fail | only when a root CA file exists (then as `verify-ca`) |
| `verify-ca` | TLS | fail | fail | chain against the root CA file; the name is not checked |
| `verify-full` | TLS | fail | fail | chain and the host name (`tls`'s check: SAN dNSName, the CN when there is no dNSName SAN; an IP address against iPAddress SANs) |

| key | meaning |
|---|---|
| `sslrootcert` | the CA file; default `~/.postgresql/root.crt` when it exists. `system`: the system trust store, and `sslmode` must then be `verify-full` (the default becomes `verify-full`; weaker is E_INPUT) |
| missing CA file with `verify-ca` or `verify-full` | connect fails (code 1): `root certificate file "<path>" does not exist` and libpq's hint |
| `sslcert`, `sslkey` | client certificate and key; defaults `~/.postgresql/postgresql.crt` and `.key` when the certificate file exists. The key must be mode 0600 or less, or owned by root with 0640 (as libpq); otherwise E_INPUT |
| `sslpassword` (an encrypted key) | E_INPUT "encrypted private keys are not supported" |
| `sslcrl`, `sslcrldir`, a default `~/.postgresql/root.crl` that exists | E_INPUT "certificate revocation lists are not supported yet" when verification is on (never skip a CRL silently); P5 |
| `sslsni` | 1 (default): SNI for a host name; 0: no SNI (`tls.Options.sni`) |
| `ssl_min_protocol_version`, `ssl_max_protocol_version` | `TLSv1.2` (default min), `TLSv1.3`; `TLSv1` and `TLSv1.1` are E_INPUT (`tls` speaks 1.2 and 1.3) |
| `sslcertmode` | `allow` (default) and `disable`; `require` is E_INPUT (P5) |
| `sslnegotiation` | `postgres` (default); `direct` is E_INPUT (P5) |
| `sslcompression` | accepted and ignored (libpq ignores it too) |

**sslmode default, for the user.** Recommended: `prefer`, as libpq, so a
connection string means the same to both clients and the differential test
compares like with like; the default local Unix socket is not affected.
Alternative: `verify-full` (secure by default, as `sqlserver`'s
`Encrypt=mandatory`), which breaks connection strings written for libpq that
reach a server with a self-signed certificate.

### 5.3 GSSAPI

No GSSENCRequest is ever sent (`gssencmode` `disable` and `prefer` behave
alike; libpq's `prefer` also sends nothing without Kerberos credentials).
`gssencmode=require` is E_INPUT "GSSAPI encryption is not supported".

### 5.4 Authentication

| request (`R`, Int32 code) | answer | notes |
|---|---|---|
| 0 Ok | - | done; with `channel_binding=require` and no SCRAM-PLUS exchange before it: fail ("channel binding required, but server authenticated client without channel binding") |
| 3 CleartextPassword | PasswordMessage `p`: the password, NUL-terminated | sent over any transport, as libpq; `require_auth` can forbid it |
| 5 MD5Password (4-byte salt) | `p`: `"md5" + hex(md5(hex(md5(password + user)) + salt))` | |
| 10 SASL (mechanism list) | SASLInitialResponse `p`: the mechanism, Int32 length, client-first-message | 5.5 |
| 11 SASLContinue | SASLResponse `p`: client-final-message | |
| 12 SASLFinal | check the server signature | AuthenticationOk follows |
| 2 KerberosV5, 7 GSS, 8 GSSContinue, 9 SSPI | refuse: close; code 1 "GSSAPI authentication is not supported" | |
| any other | refuse: code 1 "unsupported authentication request N" | |

No password from the string, `PGPASSWORD` or `.pgpass` when one is asked
for: code 1, `fe_sendauth: no password supplied` (libpq's text).

`require_auth` (libpq 16): a comma list of `password`, `md5`, `scram-sha-256`,
`none`, or all negated with `!`; `gss` and `sspi` accepted in the list and
never met. Checked at each request, and at AuthenticationOk for `none`.

### 5.5 SCRAM-SHA-256

RFC 5802 and RFC 7677, as PostgreSQL's SASL chapter describes.

| choice | rule (libpq's) |
|---|---|
| mechanism | `SCRAM-SHA-256-PLUS` when the connection is TLS, the server lists it and `channel_binding` is not `disable`; else `SCRAM-SHA-256` |
| gs2 header | `p=tls-server-end-point,,` with PLUS; `y,,` on TLS when the server does not offer PLUS (and `channel_binding=prefer`); `n,,` without TLS or with `channel_binding=disable` |
| `channel_binding=require` | TLS and PLUS required, else fail before sending anything |
| user name in the message | empty (`n=,`): the server uses the startup user |
| client nonce | 18 random bytes, base64 (24 characters) |

Messages:

| message | content |
|---|---|
| client-first | gs2 header + `n=,r=` + client nonce |
| server-first | `r=` combined nonce (must start with ours and be longer), `s=` base64 salt (not empty), `i=` iterations (at least 1); anything else: fail |
| client-final | `c=` + base64(gs2 header + binding data) + `,r=` + combined nonce + `,p=` + base64(proof) |
| server-final | `v=` + base64(server signature), compared in constant time; `e=` text: fail with it |

Arithmetic: SaltedPassword = PBKDF2-HMAC-SHA-256(SASLprep(password), salt,
i, 32 bytes); ClientKey = HMAC(SaltedPassword, "Client Key"); StoredKey =
SHA-256(ClientKey); AuthMessage = client-first-bare + "," + server-first +
"," + client-final-without-proof; proof = ClientKey XOR HMAC(StoredKey,
AuthMessage); ServerSignature = HMAC(HMAC(SaltedPassword, "Server Key"),
AuthMessage).

Binding data (`tls-server-end-point`, RFC 5929): the hash of the server
certificate's DER with the certificate's signature hash; SHA-256 when that
is MD5 or SHA-1; no hash (Ed25519): fail, as libpq ("could not determine
server certificate signature algorithm"). `tls.Engine.endPoint()` gives it.

PBKDF2 runs on the helper pool (a `blocking` extern): `scram_iterations` can
be large (4096 by default, about 2 ms) and must not stall other tasks.

SASLprep: printable ASCII passwords are used as they are (SASLprep leaves
them so). A password that is not valid UTF-8, or has a character SASLprep
prohibits, is used raw, as PostgreSQL and libpq do. Otherwise the RFC 4013
mapping is applied (non-ASCII spaces to space, "mapped to nothing"
characters removed) but not NFKC normalization: a password whose NFKC form
differs from itself (compatibility characters, decomposed accents) fails to
authenticate. NFKC needs Unicode tables obx does not have; P5. Listed under
risks.

### 5.6 After authentication

| message | kept |
|---|---|
| ParameterStatus `S` | a map name to value, updated whenever one arrives (also later, after `SET`) |
| BackendKeyData `K` | Int32 pid, the key (the rest of the message: 4 bytes in 3.0, 4 to 256 accepted) |
| NegotiateProtocolVersion `v` | the newest minor version must be 0 and no option unrecognized (we send none); else fail |
| NoticeResponse `N` | kept (5.8) |
| ReadyForQuery `Z` | the transaction status: `I` idle, `T` in a transaction block, `E` in a failed block |

`serverVersion()` is libpq's number from `server_version`: major * 10000 +
minor for 10 and later ("16.15 (Ubuntu ...)" is 160015).

Termination: dropping or closing a connection sends Terminate `X` with
`writeNow` (a drop never parks; if the socket will not take the 5 bytes
they are skipped) and closes the socket; a TLS connection's drop sends
close_notify the same way.

## 6. Connection strings

### 6.1 Forms

libpq's two forms, parsed by `pgwire.parseConfig`:

| form | rules |
|---|---|
| keyword/value | `key = value` separated by white space; spaces around `=` optional; a value in single quotes may hold spaces; `\'` and `\\` escape inside or outside quotes; an empty value is `''` |
| URI | `postgresql://` or `postgres://`, then `[user[:password]@][host[:port][,host[:port]...]][/dbname][?key=value[&...]]`; percent-decoding in every part; IPv6 in brackets; a host starting with `%2F` is a socket directory; `ssl=true` means `sslmode=require` (JDBC) |

Error texts are libpq's, so `postgres_basic`'s expected lines stay:

| input | E_INPUT message |
|---|---|
| `bogus=1` | `invalid connection option "bogus"` |
| `nonsense` | `missing "=" after "nonsense" in connection info string` |
| `host='x` | `unterminated quoted string in connection info string` |
| URI `?x` | `missing key/value separator "=" in URI query parameter: "x"` |
| URI `%zz` | `invalid percent-encoded token: "%zz"` |
| URI `?bogus=1` | `invalid URI query parameter: "bogus"` |
| a NUL anywhere | `connection string contains a NUL byte` (today's text) |

No error text contains a password value. `Config.masked()` gives the string
with `password` replaced by `***`, for logs.

### 6.2 Sources and precedence

For each keyword: the connection string, else its environment variable,
else the default. `password` alone has a fourth source: `.pgpass`, read
when the server asks for a password and none came from the first two.

| keyword | env | default |
|---|---|---|
| `host` | `PGHOST` | the Unix socket directory (6.3) |
| `hostaddr` | `PGHOSTADDR` | none |
| `port` | `PGPORT` | 5432 |
| `dbname` | `PGDATABASE` | the user |
| `user` | `PGUSER` | the OS user (`getpwuid(geteuid())`, `os.userName()`) |
| `password` | `PGPASSWORD` | `.pgpass` |
| `passfile` | `PGPASSFILE` | `~/.pgpass` (`$HOME`, else the passwd entry's home) |
| `connect_timeout` | `PGCONNECT_TIMEOUT` | none |
| `application_name` | `PGAPPNAME` | none |
| `fallback_application_name` | - | none |
| `client_encoding` | `PGCLIENTENCODING` | UTF8 |
| `options` | `PGOPTIONS` | none |
| `sslmode` | `PGSSLMODE`; `PGREQUIRESSL=1` means require when `PGSSLMODE` is unset | `prefer` (**for the user**, 5.2) |
| `sslrootcert`, `sslcert`, `sslkey`, `sslcrl`, `sslcrldir`, `sslsni`, `sslcertmode` | `PGSSLROOTCERT`, `PGSSLCERT`, `PGSSLKEY`, `PGSSLCRL`, `PGSSLCRLDIR`, `PGSSLSNI`, `PGSSLCERTMODE` | 5.2 |
| `ssl_min_protocol_version`, `ssl_max_protocol_version` | `PGSSLMINPROTOCOLVERSION`, `PGSSLMAXPROTOCOLVERSION` | TLSv1.2, none |
| `channel_binding` | `PGCHANNELBINDING` | `prefer` |
| `require_auth` | `PGREQUIREAUTH` | any |
| `gssencmode` | `PGGSSENCMODE` | `prefer` (no effect) |
| `target_session_attrs` | `PGTARGETSESSIONATTRS` | `any` |
| `load_balance_hosts` | `PGLOADBALANCEHOSTS` | `disable` |
| `keepalives`, `keepalives_idle`, `keepalives_interval`, `keepalives_count` | - | 1, system defaults (`net.setKeepAlive`) |
| `service` | `PGSERVICE` | none (6.5) |

### 6.3 Hosts

| rule | as libpq |
|---|---|
| a host starting with `/` | a socket directory: connect to `<dir>/.s.PGSQL.<port>` |
| a host starting with `@` | a Linux abstract socket `@<name>/.s.PGSQL.<port>` |
| no host | the socket directory `/var/run/postgresql` (Debian and Ubuntu's libpq), else `/tmp` (upstream's) when the first has no socket file. libpq has one compiled-in directory: this tries both |
| `hostaddr` | TCP to that address, no name lookup; `host` is then the name for TLS verification and `.pgpass` |
| several hosts (`host=a,b port=5432,5433`, or a URI list) | tried in order; one port applies to all; a counts mismatch is E_INPUT (libpq's text: `could not match 2 port numbers to 3 hosts`). Each host's addresses are tried as `net.dial` does. Authentication failing ends the attempt (the other hosts are not tried, as libpq) |
| `target_session_attrs` | `any` only; other values E_INPUT until P5 |
| `load_balance_hosts` | `disable` only; `random` E_INPUT until P5 |

### 6.4 The password file

| rule | |
|---|---|
| file | `passfile`, else `PGPASSFILE`, else `~/.pgpass` |
| used | only when the server asks for a password and none was given |
| ignored | when it is not a regular file, or when it gives group or world any access (libpq warns on stderr; we stay silent) |
| lines | `hostname:port:database:username:password`; `#` lines are comments; `\:` and `\\` escape; the first four fields may be `*` |
| host matched | `host`, else `hostaddr`, else `localhost`; for a Unix socket in the default directory, `localhost` |
| match | the first matching line wins |

### 6.5 The service file

Out of P1 to P4, in P5 (small: an INI file). Until then `service=` or
`PGSERVICE` is E_INPUT "service files are not supported yet". Rule for P5:
`PGSERVICEFILE` or `~/.pg_service.conf`, then `PGSYSCONFDIR/pg_service.conf`
(default `/etc/postgresql-common/pg_service.conf` on Debian); the service's
keys apply where the string gives none, before the environment.

### 6.6 Accepted and ignored, refused

| keywords | treatment |
|---|---|
| `sslcompression`, `krbsrvname`, `gsslib`, `gssdelegation`, `tcp_user_timeout` | accepted, ignored |
| `replication`, `requirepeer` (P5), `sslpassword`, `sslcertmode=require`, `sslnegotiation=direct`, `min_protocol_version`/`max_protocol_version` other than `3.0`, `gssencmode=require` | E_INPUT naming the keyword (never its value) |
| any other unknown keyword | E_INPUT `invalid connection option "<key>"` |

### 6.7 Differences from libpq, all in one place

| difference | why |
|---|---|
| no GSSAPI, SSPI, Kerberos; `gssencmode=require` refused | no GSSAPI library |
| the service file only from P5 | scope |
| two default socket directories tried | libpq's is compiled in; this machine's libpq uses `/var/run/postgresql` |
| `client_encoding=UTF8` always sent | obx strings are UTF-8 |
| cancel requests in clear, even on a TLS connection | libpq 16's `PQcancel` does the same; libpq 17's `PQcancelCreate` encrypts (P5) |
| notices kept, not printed | a library does not write to stderr |
| SASLprep without NFKC | no Unicode tables (5.5) |
| CRLs, encrypted keys, `sslcertmode=require`, `requirepeer`, `target_session_attrs` other than `any`, `load_balance_hosts=random` refused | P5; refusing beats silently weakening |
| verify-full: OpenSSL's host check | libpq 16 also matches an IP address against dNSName SANs and has its own wildcard rule; a certificate relying on that fails here |
| `.pgpass` with loose permissions ignored silently | no stderr |

## 7. Queries

### 7.1 Which protocol for which call

| call | protocol | messages sent in one write | as libpq today |
|---|---|---|---|
| `exec(script)` | simple Query | `Q` | `PQsendQuery` |
| `query(sql, args)`, `querySql` | extended, unnamed statement and portal | Parse, Bind, Describe portal, Execute 0, Sync | `PQsendQueryParams` (the same messages) |
| `stream` | extended, unnamed, row limit | Parse, Bind, Describe portal, Execute n, Flush (no Sync, section 12) | single-row mode |
| `transaction` | `exec("BEGIN")`, the body, `exec("COMMIT")` or `exec("ROLLBACK")` | | the same |

`query` always uses the extended protocol, also without arguments, as
libpq's `PQsendQueryParams` does: one statement only (several are 42601
"cannot insert multiple commands into a prepared statement" from both
clients).

### 7.2 Statements

The unnamed statement and the unnamed portal, every time: each lasts until
the next Parse or Bind, so nothing is ever closed or leaked. A named
statement cache (pgx's default) would skip Parse on repeats and allow
per-column binary results, at the cost of server memory per connection,
`cached plan must not change result type` (0A000) after DDL, and
`DISCARD ALL` interplay with poolers. Not now; P5 measures it on
`http_gate --db` (the case it would help) before anyone builds it.

### 7.3 Parameters

Bind: one format code per parameter; result format codes: none (all text).

| `sqldialect.Value` | Parse OID | format | bytes | as today |
|---|---|---|---|---|
| `Null` | 0 (the server infers; `$1::numeric` where it cannot) | text | length -1 | yes |
| `Int(n)` | 20 int8 | text | `n.toStr()` | yes |
| `Float(f)` | 701 float8 | text | `NaN`, `Infinity`, `-Infinity`, else the shortest round-trip text | yes |
| `Bool(b)` | 16 bool | text | `t` or `f` | yes |
| `Text(s)` | 25 text | text | the bytes; a NUL is E_INPUT `text value contains a NUL byte` (the server would refuse it too) | yes |
| `Bytes(b)` | 17 bytea | **binary** (1) | the bytes themselves | text `\x` + hex today: the server stores the same value; half the bytes, no hex step |
| `Dec(d)` | 1700 numeric | text | `sqldialect.decimalText(d, 16383)`: E_DEC_PG_RANGE below 1e-16383 | yes |

At most 65,535 parameters (Int16), E_INPUT beyond. The SQL text may not
hold NUL (the protocol's String), E_INPUT `SQL text contains a NUL byte`.

### 7.4 Results: text format, decoded in place

**Decision: every result column in text format (format code 0).** Reasons:

| reason | detail |
|---|---|
| exact | numeric text is the exact decimal with its display scale; floats are sent as the shortest round-trip text (`extra_float_digits` 1, the default since 12); nothing is lost against binary |
| no extra round trip | per-column formats need the column types before Bind: a Describe round trip, or a statement cache (7.2). Text needs neither |
| the API is text-shaped | `Rows.text(r, c)` returns the server's text for every type, and `value()` gives `Text` for every type without a case; with binary results that text would have to be rebuilt (impossible for `timestamptz` in the session's zone, `interval` in the session's style, arrays, ranges, user types) |
| the oracle compares like with like | both clients get the same bytes from the server; any difference is a decoding bug |
| speed | the decoders read digits straight from the received bytes; binary saves little (section 18 sets the target; P3 measures and only then considers binary) |

The binary encodings are listed below anyway: P3's fallback if text decoding
misses its target, and the reference for a later statement cache.

### 7.5 Types

What `postgres` maps today, kept exactly. "Kinds" are `sqlrows` kinds for
streams; the `Rows` accessor is the buffered one.

| type (OID) | text form (what we receive) | decoded as | `Rows` accessors, kinds | binary form (reference) |
|---|---|---|---|---|
| int2 (21), int4 (23), int8 (20) | `-?[0-9]+` | `int` | `int`, `value` = `Int`; kinds Int, Decimal | big-endian two's complement, 2/4/8 bytes |
| numeric (1700) | `-?[0-9]+(\.[0-9]+)?`, exactly dscale fraction digits; `NaN`, `Infinity`, `-Infinity` (14+) | `Decimal` (7.6) | `decimal`, `decimalOrNull`, `value` = `Dec`; kinds Decimal, and Int for numeric(p, 0) p <= 18 and unconstrained | Int16 ndigits, Int16 weight, UInt16 sign (0x0000 +, 0x4000 -, 0xC000 NaN, 0xD000 +Inf, 0xF000 -Inf), Int16 dscale, ndigits Int16 base-10000 digits; value = sum of d[i] * 10000^(weight - i) |
| float4 (700), float8 (701) | shortest round-trip, `1e+300` style, `-0`, `Infinity`, `-Infinity`, `NaN` | `float` via `math.parse` (`Infinity` handled first) | `float`, `value` = `Float`; kind Float | IEEE 754 big-endian, 4/8 bytes |
| bool (16) | `t`, `f` | `bool` | `bool`, `value` = `Bool`; kind Bool | 1 byte, 0 or 1 |
| text (25), varchar (1043), bpchar (1042, blank-padded), name (19), char (18) | the bytes in the client encoding (UTF-8) | `str`, unchanged | `text`, `value` = `Text`; kind Text | the same bytes (char: 1 byte) |
| bytea (17) | `\x` + lower-case hex (`bytea_output=hex`, the default) | `str` of the bytes (`sqldialect.unhex`); escape format is E_TYPE `bytea in hex format`, as today | `bytes`, `value` = `Bytes`; kind Bytes | the bytes |
| date (1082) | `YYYY-MM-DD` with `DateStyle` ISO; `infinity`, `-infinity`, `... BC` | `time.Date` (`time.parseDate`; years 1 to 9999, else E_TYPE as today) | `text`; kind Date | Int32 days since 2000-01-01; INT32_MAX/MIN for ±infinity |
| time (1083) | `HH:MM:SS[.ffffff]` | `time.TimeOfDay` | `text`; kind Time | Int64 microseconds since midnight |
| timestamp (1114) | `YYYY-MM-DD HH:MM:SS[.ffffff]` | `time.LocalDateTime` | `text`; kind DateTime | Int64 microseconds since 2000-01-01 00:00; INT64_MAX/MIN for ±infinity |
| timestamptz (1184) | as timestamp, then the session zone's offset `+HH[:MM[:SS]]` | `time.OffsetDateTime` with the session's offset | `text`; kind OffsetDateTime | Int64 microseconds since 2000-01-01 00:00 UTC (the offset is not sent: one reason for text) |
| interval (1186) | per `IntervalStyle` (`1 year 2 mons 3 days 04:05:06`) | not decoded | `text`, `value` = `Text`; kind Text | Int64 microseconds, Int32 days, Int32 months |
| uuid (2950) | `8-4-4-4-12` lower-case hex | not decoded | `text`; kind Text | 16 bytes |
| json (114), jsonb (3802) | the JSON text (jsonb normalized by the server) | not decoded | `text`; kind Text | json: the text; jsonb: a version byte 1, then the text |
| oid (26) | unsigned decimal | not decoded | `text`; kind Text | UInt32 big-endian |
| every other type (arrays, ranges, enums, inet, money, ...) | its output function's text | not decoded | `text`, `value` = `Text`; kind Text | its `typsend` |
| NULL, any type | DataRow length -1 | - | `isNull` true; `value` = `Null`; `text` and the typed accessors E_NULL | the same |

`value()`'s rule stays: `Bool`, `Int` (int2/4/8), `Float` (float4/8), `Dec`
(numeric), `Bytes` (bytea), `Text` for everything else, `Null` for NULL.

The accessors keep today's type checks and error texts (`column 3 is not
an integer`, E_TYPE), so the differential test compares them too.

### 7.6 numeric and Decimal, exactly

| case | result (the same for `Rows.decimal`, the stream's `decimal`, `value`) | code |
|---|---|---|
| up to 38 significant digits (leading zeros not counted; trailing zeros stripped by `Decimal`) | that `Decimal`, display scale = the text's fraction digits (`12.50` has scale 2) | |
| a numeric(p, s) column (typmod >= 4, s >= 0) | `sqldialect.fitDecimal(d, p, s)`: scale s | |
| more than 38 significant digits | error | E_DEC_PRECISION 1007 |
| an exponent outside `Decimal`'s range (beyond about 1e32805) | error | E_DEC_EXPONENT 1008 |
| `NaN`, `Infinity`, `-Infinity` | error | E_NONFINITE 1004 |
| read as int (`int` kind on unconstrained or numeric(p, 0)) | the value when it is integral and fits `int`; else `"<d> is not an int"` | E_TYPE 1002 |
| a parameter below 1e-16383 | refused before sending | E_DEC_PG_RANGE 1013 |

The fast path (P3): `[-]digits[.digits]` with at most 38 digits after the
leading zeros is read into an `i128` in one pass and made with
`fromFraction(units, scale)` (std's literal constructor, as `tns` does);
anything else goes through `sqldialect.parseDecimal`, so codes and edge
cases are exactly today's. The differential test checks the two paths agree
over generated values.

### 7.7 Results per call

| reply | `query` gives | `exec` gives |
|---|---|---|
| RowDescription, DataRows, CommandComplete | `Rows` with the columns and rows | Ok |
| NoData, CommandComplete (`INSERT 0 3`) | `Rows` with no columns; `affected()` 3 | Ok |
| EmptyQueryResponse (`query("")`) | `Rows` with nothing (as libpq's PGRES_EMPTY_QUERY) | Ok |
| ErrorResponse | Err (8.1) | Err: the first error of the script |
| several statements (exec only) | - | Ok, or the first error (the script stops there, as libpq) |
| CopyInResponse | Err E_INPUT "COPY is not supported yet", after CopyFail `f` and the server's 57014 error | the same |
| CopyOutResponse, CopyBothResponse | Err E_INPUT, after the CopyData are read and dropped up to CommandComplete | the same |

`affected()`: the CommandComplete tag's last number for `INSERT`, `UPDATE`,
`DELETE`, `MERGE`, `SELECT`, `MOVE`, `FETCH`, `COPY`; 0 for any other tag
(libpq's `PQcmdTuples`).

## 8. Errors and notices

### 8.1 ErrorResponse to DbError

ErrorResponse `E` is a list of (Byte1 field code, String) ending in a 0 byte.

| DbError | from |
|---|---|
| `code` | 7 (`PGRES_FATAL_ERROR`), as today |
| `state` | field `C` (SQLSTATE) |
| `msg` | field `M` (the primary message) |

`pgwire.ServerError` keeps every field (`Conn.lastServerError()`):
`severity` (`V`, else `S`), `code` (`C`), `message` (`M`), `detail` (`D`),
`hint` (`H`), `position` (`P`), `internalPosition` (`p`), `internalQuery`
(`q`), `where` (`W`), `schema` (`s`), `table` (`t`), `column` (`c`),
`dataType` (`d`), `constraint` (`n`), `file` (`F`), `line` (`L`), `routine`
(`R`). Unknown field codes are skipped.

The library's own errors keep the `sqldialect` codes: E_INPUT, E_NULL,
E_TYPE, E_NONFINITE, E_DEC_*, E_CANCELLED, E_TIMEOUT, E_PROTOCOL, E_IO,
E_COLUMN; connect failures code 1 (`CONNECTION_BAD`).

Connect failure text (libpq's shape): `connection to server on socket
"<path>" failed: <reason>` or `connection to server at "<host>" (<addr>),
port <port> failed: <reason>`; a server error as `<severity>:  <message>`
(two spaces, as libpq). Several hosts: each attempt's text, one line each.

### 8.2 The connection after an error

| where | server | connection after |
|---|---|---|
| extended query, outside a transaction block | ErrorResponse, skips to Sync, ReadyForQuery `I` | idle; the implicit transaction rolled back |
| extended query, in a block | ReadyForQuery `E` | in a failed block: every statement is 25P02 until ROLLBACK (`transaction` sends it) |
| simple Query script | ErrorResponse, the rest of the script skipped, ReadyForQuery | `I`, or `E` inside an explicit block |
| a stream (no Sync sent yet) | ErrorResponse, then it waits for Sync | we send Sync and read ReadyForQuery; idle (or `E` in a block) |
| ErrorResponse with severity FATAL or PANIC | then the server closes | broken: `idle()` false, later calls E_IO |
| socket or TLS failure, EOF | | broken |
| malformed message (bad length, unknown type, a field past the message's end) | | broken, E_PROTOCOL |
| cancellation or deadline while waiting | | cancel and drain (section 13); broken if the drain fails |

A broken connection is never used again: every call is E_IO `the connection
is broken: <first cause>`, `idle()` is false, so a pool drops it.

### 8.3 Messages that come at any time

| message | handling |
|---|---|
| NoticeResponse `N` | parsed like an error; the last 100 kept in the connection (`notices()` in P5); never printed |
| ParameterStatus `S` | the map updated |
| NotificationResponse `A` | queued (at most 10,000, then the oldest dropped and counted); API in P5 |

## 9. The Session state machine

| state | meaning | allowed next |
|---|---|---|
| Ready | after ReadyForQuery | any request |
| Busy | a request sent; its ReadyForQuery not read yet | only reading its reply |
| Portal | a stream's portal open, no Sync sent; `pending` 0 or 1 Execute whose reply is not read | the stream's Execute, Close; any other call first settles (12.6) |
| Broken | wire failure, FATAL, malformed reply, drain failed | nothing: E_IO |

`idle()` is Ready, transaction status `I`, and not broken: what a pool keeps.

## 10. Reading replies

| piece | rule |
|---|---|
| inbox | a `[]byte` of 64 KB per connection; each socket read appends what has arrived |
| framing | Byte1 type, Int32 length (counting itself, at least 4); bodies are read with bounds checks against that length |
| chunks | after each read, the whole messages in the inbox are copied once into a `str` (a chunk); the partial message at the end stays in the inbox. Every decoder reads the chunk in place, as `tds` reads a packet |
| a large message | the inbox grows by doubling as bytes arrive (never to a declared length before the bytes come: a malformed length cannot allocate a gigabyte); after it is consumed an inbox over 1 MB is replaced by a new 64 KB one |
| limit | a declared length over 1 GiB + 64 KB is E_PROTOCOL (PostgreSQL's largest field is 1 GiB) |
| writes | outgoing messages are built into one reused `[]byte` and sent with one write per request |

A DataRow is Int16 column count (must equal RowDescription's), then per
column Int32 length (-1 NULL, other negatives E_PROTOCOL) and the bytes.
`scanRow(chunk, at, ncols, starts, lens)` fills the value spans, in the
pattern of `tds.scanRow` and `tns.scanRaw`.

Raw rows, the batch a stream holds and the buffered `Rows`:

| holder | kept | per row | per value |
|---|---|---|---|
| a stream's batch | the chunks of one Execute's reply | (chunk index, offset of the DataRow) | nothing: scanned into the stream's `starts`/`lens` (ncols ints) when `fill` reaches the row |
| buffered `Rows` | the chunks of the whole reply | the chunk index (`[]i32`) | start and length in its chunk (`[]i32` each; -1 length for NULL) |

So `Rows` keeps about the bytes received plus 8 bytes a value and 4 a row
(1,000,000 rows of 4 columns: about 36 MB besides the data), and a text read
from it shares the chunk's bytes, as `oracle`'s `Rows` do. Values are
decoded when an accessor reads them.

## 11. Buffered query

`query` sends Parse, Bind, Describe portal, Execute 0, Sync in one write and
reads to ReadyForQuery, keeping RowDescription, the DataRows raw (section
10) and the tag. One round trip, as libpq. Rows do not borrow the `Conn`:
they stay readable after it closes (the test checks it).

## 12. Streaming

### 12.1 The transaction rule

Portals die at the end of their transaction. Outside a transaction block a
Sync ends the implicit transaction, so the portal must live across Executes
without a Sync.

| option | verdict |
|---|---|
| Flush instead of Sync between Executes; Sync at the end or at close | **chosen**: the protocol allows it ("A Flush must be sent after any extended-query command except Sync, if the frontend wishes to examine the results"); the implicit transaction stays open while the stream is open, exactly as a running query holds its snapshot today |
| BEGIN around every stream | an extra round trip, and changes the semantics inside an explicit block |
| simple protocol, single-row style | no row limit, no clean early close: what we leave libpq to get away from |

Inside an explicit block (`T`) the same messages work: the final Sync does
not end the block.

### 12.2 Messages

| moment | client sends | server answers |
|---|---|---|
| `stream()` | Parse, Bind, Describe portal, Execute `FIRST_ROWS`, Flush | ParseComplete, BindComplete, RowDescription or NoData, then the first batch |
| `stream()` returns | after RowDescription: `bind` runs on the columns; the batch stays on the wire | |
| a batch | | DataRows, then PortalSuspended `s` (more rows) or CommandComplete (the end) or ErrorResponse |
| more rows | Execute n, Flush (12.4: sent as soon as the previous batch is read) | the next batch |
| the end | Sync | ReadyForQuery |
| early close | Close portal, Sync | CloseComplete, ReadyForQuery |
| an error | Sync | ReadyForQuery |

### 12.3 Rows per Execute

| Execute | rows asked |
|---|---|
| the first (sent before the columns are known) | `FIRST_ROWS` = 256: a short result is one round trip |
| the next ones | the rows of `FETCH_TARGET` (256 KB) at the average DataRow size of the last batch, at least the stream buffer's free space, at least 64, at most what `FETCH_LIMIT` (4 MB) holds at the largest DataRow seen, at most 100,000 |
| `setFetchRows(n)` | n rows every time (0: the byte rule) |

The constants follow `oracle` (design/oracle.md, "round trips sized by
bytes"); P3 may tune them by measurement and records why.

### 12.4 Fetch-ahead

As `oracle` does: when a batch's reply has been read to its PortalSuspended,
the next Execute and Flush are sent at once, and only then are the batch's
rows handed out. The server makes batch k+1 while the program decodes batch
k. At most one Execute is outstanding; the stream holds one batch of raw
rows. `setFetchAhead(false)` sends the Execute when the rows run out (for
measurement). Buffered `query` has nothing to fetch ahead (one Execute 0).

### 12.5 fill, errors, close

| event | behaviour |
|---|---|
| `fill(out)` | scans the batch's next DataRows in place into the stream's `starts`/`lens`, calls `read(bound, RowView)`, until `out` is full or the batch ends; then reads the outstanding batch (sending the next Execute at once) and goes on |
| an ErrorResponse after rows | the rows first, the error from the next `fill` (as today: `later error: 4999 rows then error 7 22012`) |
| `read` or a decoder fails | the stream ends with that error; the portal is closed (Close, Sync) |
| `close()` with no Execute outstanding | Close portal, Sync; read CloseComplete and ReadyForQuery |
| `close()` with an Execute outstanding | read its reply and drop it; if it has not ended within `CLOSE_WAIT_MS` (1,000 ms: a slow query), send a CancelRequest and keep reading, at most `ABORT_MS` (5,000 ms) in all; then Close, Sync (after an error: Sync). Past `ABORT_MS`: broken |
| the end (CommandComplete) | Sync, read ReadyForQuery; `fill` returns 0 |

### 12.6 A dropped stream

`Stream` holds `mut &Conn` and has no `drop` (a value record of borrows).
The next call on the connection settles it first, with `close()`'s steps.
`idle()` is false until then (as today: `dropped: 3 rows | idle false |
after: 2 | idle true`).

## 13. Cancellation

| piece | rule |
|---|---|
| when | a task cancelled, or past its deadline, while it waits for a reply; `close()` of a slow stream (12.5) |
| how | a new connection to the same endpoint (the socket path, or the address that worked), no SSLRequest, CancelRequest (Int32 16, Int32 80877102, Int32 pid, the key), then wait for the server to close it (at most 1 s), as libpq 16's `PQcancel` |
| then | read the original connection's reply to its ReadyForQuery (sending Sync first if a stream's portal is open), at most `ABORT_MS` (5,000 ms), dropping what comes (57014 "canceling statement due to user request" expected) |
| I/O in a cancelled task | through `net`'s and `tls`'s `...For(ms)` calls (P0), which ignore the task's cancellation and deadline: the same thing today's `postgres` does with `rtFdWait(..., cancellable false)` |
| result | E_CANCELLED `query cancelled` or E_TIMEOUT `query: deadline passed`, as today; the connection idle again, or broken when the drain did not finish |
| race | a cancel that arrives after the query ended may hit the next one, as with libpq; the drain to ReadyForQuery before any next request makes it rare |

TLS-encrypted cancel requests (libpq 17) come in P5.

## 14. Pipelining

The protocol lets a client send many requests before reading. Internally
every call already sends its messages in one write (one round trip a
query, as libpq).

**Design (built in P4):** `conn.batch(stmts: &[Stmt], w): Result[[]Rows,
Error]`, where `pub type Stmt = record { pub sql: str; pub args: []Value }`.
All statements as Parse, Bind, Describe, Execute 0, in one write, then one
Sync: one implicit transaction, all or nothing (the first error is the Err;
the server skips the rest; nothing is committed). Inside an explicit block
the Sync does not commit. To avoid the pipeline deadlock (both sides blocked
writing), a batch whose messages exceed 64 KB is written in pieces with the
replies read (`readNow`) between pieces.

**Not used for the pool and `http_gate --db` (P4).** Each request there is
one query from one task; pipelining would need several tasks' queries to
share one connection (tokio-postgres's model: a dispatcher per connection
that matches replies to waiters). That cuts round trips only when queries
queue on few connections, and needs a design for transactions on a shared
connection. P4 measures the plain pool first; the profile of the libpq
version says the cost per request is system calls (about 40 us of the
server's thread), which the native client reduces by itself (one write, one
read per query, no libpq poll loop). Multiplexing is listed as a later
option with that measurement as its trigger.

## 15. The pool

`Pool`, `Lease`, `acquire`, `handOn` and the waiter queue stay as they are,
over the native `Conn`.

| point | rule |
|---|---|
| giving back | `Lease.drop`: `idle()` true (Ready, status `I`, not broken) keeps it; anything else (in a block, a stream dropped, broken) closes it and passes its place on, as today |
| reset | none: no `DISCARD ALL`, as today. Session settings and temporary tables persist across leases; a program uses `SET LOCAL` inside a transaction. Reason: one more round trip per lease for every program to protect a few |
| taking an idle one (P4) | a private `alive()` check: a `readNow` on the socket; EOF, an error, or bytes (an unsolicited FATAL such as 57P01 at server shutdown) close it and open a new one. libpq's version checked nothing |
| a waiter's deadline, a failed connect | as today |

## 16. Tasks

Every read and write goes through `net.Conn` or `tls.Conn`, which park the
task on the socket and take the caller's `Where` (cancellation, deadline).
Nothing blocks the program's thread: name lookup is `net.dial`'s helper
pool call, PBKDF2 a `blocking` extern, the rest is computation on bytes in
hand. One task uses a `Conn` at a time (the `Pool` hands them out);
`Stream` holds `mut &Conn`, so the checker refuses other uses while it is
open.

## 17. Testing

| test | backends | server | what |
|---|---|---|---|
| `pgwire_config` | C, native | none | keyword/value and URI parsing, escapes, percent-decoding, IPv6, socket paths, multiple hosts, every env variable (set with `os.setEnv` in the test), defaults, `.pgpass` matching and permissions (files in a temporary directory), libpq's error texts, masking |
| `pgwire_codec` | C, native | none | message encoding against byte vectors; DataRow scanning; text decoders on edge values; numeric fast path against `parseDecimal` on 100,000 generated strings; SCRAM against RFC 7677's vector (user `user`, password `pencil`); MD5 against a vector computed by PostgreSQL's formula; `tls-server-end-point` hash choice |
| `pgwire_fake` | C, native; ASan and UBSan (gate "redteam tasks") | a scripted server in the test (`lib/tests/support/pgtest.obx`) on 127.0.0.1 and on a Unix socket | trust, cleartext, MD5 and SCRAM (the fake computes the server side) including a wrong server signature, a nonce without our prefix, iteration count 0; SSLRequest `N`, and `S` followed by a stray byte (CVE-2021-23222: refused); NegotiateProtocolVersion; streams with PortalSuspended, rows across reads, messages split at every byte; an early close (Close and Sync seen by the server); 300 mutated or cut replies (bit flips, wrong lengths, negative lengths, counts that disagree, unknown types, EOF mid-message, a 2 GB declared length), each ending in rows or an Err, never a trap or hang; a cancelled wait (the CancelRequest seen with the right pid and key) |
| `postgres_live`, `postgres_live_stream`, `task_postgres_async`, `task_postgres_pool`, `http_server_pg_cancel`, `postgres_basic` | C, native | live | the existing tests, unchanged but for the lines of section 4's "Changes" (and libpq names in their comments) |
| `postgres_live_diff` | C, native | live | the oracle, below |
| `pgwire_live` | C, native | live, the local socket | connect, scripts, queries, parameters, errors and the state after, a portal in batches, closePortal, a cancelled and a timed-out `pg_sleep`, NOTICE, COPY refused |
| `pgwire_live_tls` | C, native | live, TCP with TLS | each sslmode against the local server (`ssl` is on), verify-ca and verify-full with a CA given (the test makes none: skipped without `OBX_PG_TLS_CA`), channel binding prefer and require, SCRAM-PLUS used (`pg_stat_ssl`; the server's log is not needed) |
| `postgres_libpq_basic` | C, native | none | today's `postgres_basic` on `postgres_libpq` |

Every test runs with `OBX_CHECK=1` (no leak, `openHandles()` 0, `tls` and
`postgres` handles both). Live tests read `OBX_PG_DSN`, default
`host=/var/run/postgresql dbname=postgres connect_timeout=3`, exit 77 when
they cannot connect, and never print the DSN. The TCP and TLS tests build
their own string from `OBX_PG_TLS_DSN` (exit 77 without it).

**The differential oracle** (`postgres_live_diff`, the pattern of
`sqlserver_live_diff`): the same SQL through `postgres` and
`postgres_libpq`, on two connections to the same server, and for each:

| compared | how |
|---|---|
| columns | count, names, type OIDs, typmods, `decimalType` |
| rows | `rowCount`, `affected` |
| each cell | `isNull`; `text`; `value().toStr()`; and every typed accessor (`int`, `float`, `bool`, `bytes`, `decimal`, `decimalOrNull`) as Ok value or Err code, state and message |
| streams | the same query streamed by both, a RowView reader that calls every accessor of every column, the results compared |
| errors | code, state, message, and the connection's `idle()` after |

The queries:

| set | content |
|---|---|
| edge values | for every type of 7.5: int2/4/8 min, max, 0, -1; numeric 0, `-0.00`, 38 nines, 39 digits, `1e-16383`, `NaN`, `Infinity`, `-Infinity`, numeric(10,2), numeric(2,-3), numeric(38,0); float4/float8 0.1, 1e300, 5e-324, -0, ±Infinity, NaN; bool; text empty, multi-byte, 1 MB; varchar; char(5) padding; name; `"char"`; bytea empty, all 256 byte values, 1 MB; date 0001-01-01, 9999-12-31, ±infinity, BC; time `24:00:00`; timestamp ±infinity, microseconds; timestamptz under three session zones (`SET TimeZone`); interval in each IntervalStyle; uuid; json, jsonb; oid; an int array; NULL of each |
| generated | 2,000 rows from a fixed-seed generator: each row a random choice of columns, each column a random type and a random edge or random value of it, so values meet in every combination of columns and positions |
| parameters | every `Value` variant as `SELECT $1` and into a column of each mapped type, including NULL untyped and cast |
| errors | syntax (42601), undefined table, unique and not-null violations, division by zero mid-result, 22003, two statements in `query`, `RAISE EXCEPTION USING ERRCODE` with a custom SQLSTATE, `RAISE NOTICE` (no error), an error in a block then 25P02, `exec` scripts failing in the middle |

Differences are printed with the query's own values only; the expected
output is `differences: 0`.

Gate: `obx/tools/gate.sh full` must PASS, its whole output saved to a
scratch file. Add `obx/lib/tests/run/pgwire_*.obx` to the "redteam tasks"
step's file list (ASan and UBSan); `task_postgres_*` already run under TSan.

## 18. Performance

Benches:

| bench | what |
|---|---|
| `python3 obx/bench/dbstream/run.py` | 1,000,000 rows of (bigint, int, text, numeric(12,2)), streamed and buffered, both backends; P3 adds a `pglibpq` client to `dbstream.obx` so the old client is measured in the same run |
| `perf stat -e cycles:u,instructions:u` on the client | cycles and instructions a row, user and system time, as design/db-streaming.md section 7 did |
| `python3 obx/cmd/http_gate.py --servers native,c --db 4,16` and `--workers 1,2,4,8 --db 4` | requests/s and latency through the pool |

Targets (native backend; measured pinned to one CPU with its SMT sibling
idle, after `uptime`, least of 5 interleaved runs):

| measure | libpq today | target |
|---|---:|---:|
| streamed, cycles a row | 2,172 | at most 1,550 (SQL Server's 1,290 + 20 %; Oracle's is 1,169) |
| buffered, cycles a row | 1,456 | at most 1,450 |
| streamed, peak RSS | 8.3 MB | at most 16 MB |
| buffered, peak RSS | 109.3 MB | at most 110 MB |
| streamed, wall, 1,000,000 rows | 540 ms | below libpq's |
| `http_gate --db`, requests/s | 22,300 (10 clients, pool 4) | at least libpq's, each configuration |
| `http_gate --db`, p99 | 656 us | at most libpq's + 10 % |

If text decoding keeps the stream above its target after the in-place work,
P3 profiles it and writes up binary results (a Describe round trip or a
statement cache) as a proposal; it does not build them.

P3 results (branch `pgwire-p3`, 2026-11-06, CPU 10 with its sibling idle,
`perf stat` for cycles and instructions, least of 5 interleaved runs; the
times from `run.py`, least of 5; design/db-streaming.md section 7 has the
full tables):

| measure | libpq today | P3 native | target | met |
|---|---:|---:|---:|---|
| streamed, cycles a row | 2,223 (pglibpq native, same run) | 1,410 | at most 1,550 | yes |
| buffered, cycles a row | 1,461 (pglibpq native, same run) | 1,526 | at most 1,450 | no |
| streamed, peak RSS | 8.8 MB | 9.8 MB | at most 16 MB | yes |
| buffered, peak RSS | 109.8 MB | 90.8 MB | at most 110 MB | yes |
| streamed, wall, 1,000,000 rows | 547 ms | 497 ms | below libpq's | yes |
| `http_gate --db` | not measured in P3 | | | |

The streamed target is met, so no binary-results proposal is written. The
buffered target is missed by 5 %: the buffered path is not the stream's
path, and the profile (section 26) puts the cost in the same text decoding.

## 19. Language changes

None needed. Two known frictions, worked around as `oracle` does: a stream
cannot empty an array and keep its capacity (new arrays per batch), and the
native runtime keeps one freed large block (page faults with large
batches). Neither blocks this design.

## 20. Decisions

Decided by the user on 2026-11-06:

- **Module names:** as recommended: `pgwire` (protocol), `postgres` (native, API unchanged), `postgres_libpq` (the old client).
- **`sslmode` default:** `prefer`, as libpq.
- **`postgres_libpq`:** stays until P4 is merged and the differential test has run clean for two weeks, then moves to `archive/`.
- **Password role for the live TCP/TLS tests:** not yet; the user will add it after the work is complete. Until then those tests exit 77 and the fake server covers the paths.

The original options, for the record:

| decision | recommendation | alternative |
|---|---|---|
| module names | `pgwire`, `postgres` (native), `postgres_libpq` | keep `postgres` on libpq and add `pg`; or `pgproto`/`pq` for the protocol |
| `sslmode` default | `prefer` (libpq's) | `verify-full` (secure by default, like `sqlserver`) |
| how long `postgres_libpq` stays | until P4 is merged and the oracle has run clean for two weeks, then `archive/` | keep it indefinitely as a fallback |
| a password role for the TCP tests | the local server allows only peer login over the Unix socket for `chris` (no `.pgpass`, no CREATEROLE). SCRAM, MD5, TLS and channel binding are covered by the fake server, but a live check needs a role with a SCRAM password reachable on `localhost:5432` and its DSN in `OBX_PG_TLS_DSN` (never printed); the server's certificate is Ubuntu's snakeoil, so `OBX_PG_TLS_CA=/etc/ssl/certs/ssl-cert-snakeoil.pem` enables verify-ca | skip the live TLS test (exit 77) and rely on the fake server |

Decided here:

| decision | reason |
|---|---|
| protocol 3.0 | the local server is 16; 3.2 is 18's and libpq 18 still asks for 3.0 |
| text results for every column | exact, no extra round trip, `text()` stays the server's text, the oracle compares like with like (7.4) |
| unnamed statements, no cache | nothing to leak or invalidate; Parse costs the server little (7.2) |
| bytea parameters in binary | the same value, half the bytes |
| streams: Flush without Sync, Sync at the end | the only way to keep a portal outside a block without a BEGIN (12.1) |
| fetch-ahead with one Execute outstanding | what `oracle` measured: up to 20 % at 1 ms round trips; bounded memory |
| early close by Close portal, cancel only for a slow reply | clean and cheap; no PQcancel race in the common case |
| cancel requests in clear | as libpq 16; servers accept them; TLS cancel in P5 |
| lost connection is E_IO, broken | as `sqlserver` and `oracle` |
| notices kept, never printed | a library does not write to stderr |
| `client_encoding=UTF8` always | obx strings are UTF-8 |
| no reset on lease return | as today; one round trip saved per lease |
| an `alive()` check when the pool takes an idle connection | a server restart otherwise fails the next request |
| `libVersion()` removed from `postgres` | there is no libpq |
| SASLprep without NFKC | no tables; ASCII and NFKC-stable passwords work |
| the service file in P5 | small, rarely used, keeps P1 smaller |
| default socket directory `/var/run/postgresql`, then `/tmp` | covers Debian's and upstream's libpq |
| stages P0 to P5 instead of the four suggested | the `net`/`tls`/`crypto` additions touch the runtime and the seed (their own review); the switch of `postgres` needs streams working (the existing tests use them), so streams' first form comes with the switch and their speed work after |

## 21. Notes and risks

1. **Static executables.** A program importing `postgres` still links
   `libssl` and `libcrypto` dynamically: `pgwire` imports `tls` (TLS) and
   `crypto` (SCRAM), and the compiler links the library of every extern of
   every imported module. Dropping libpq is a real gain (one library less;
   no libpq version skew), but not a static binary. A static build would
   need SHA-256, HMAC and PBKDF2 in obx and TLS left out of programs that
   only use Unix sockets: **later, for the user**, if static binaries
   matter.
2. **SASLprep.** Passwords that are not NFKC-stable fail with SCRAM (5.5).
   Rare; the error says authentication failed. P5 adds NFKC if needed.
3. **The cancel race** (13): as with libpq.
4. **Flush without Sync** keeps the implicit transaction (its snapshot and
   locks) open while a stream is open, as a running query does today; a
   program that leaves a stream open for long holds back vacuum.
5. **Text decoding speed** is the main performance unknown; P3 measures
   before anything else is tried (section 18).
6. **TLS host check differences** from libpq (6.7) may refuse certificates
   libpq accepts (IP addresses in dNSName SANs).
7. **The P0 runtime change** (Unix sockets, keepalive) touches both
   runtimes and needs a reseed; the gate's seed fixed point checks it.
8. **Latency** (P3). At a 2.5 ms one-way delay the native stream takes
   1,376 ms, against 625 ms for libpq: a stream of 1,000,000 rows is 164
   batches of about 256 KB, each a round trip (6.4 ms here), and only one
   batch is ahead. libpq's single-row mode sends one query and reads the
   rows as they arrive. The batch size (the 256 KB target of 12.3) and the
   number of batches in flight trade round trips against memory; the choice
   is open and for the user. At 0 delay the native stream is faster (430 ms
   against 600 ms).

## 22. Sources

- PostgreSQL 16 documentation, chapter 55 "Frontend/Backend Protocol":
  55.2 Message Flow (start-up, SSL and GSSAPI encryption requests, extended
  query, pipelining, cancelling requests, termination), 55.3 SASL
  Authentication, 55.7 Message Formats; chapter 34 libpq: 34.1 Database
  Connection Control Functions (connection strings, parameter keywords),
  34.15 Environment Variables, 34.16 The Password File, 34.17 The
  Connection Service File, 34.19 SSL Support. The 17 and 18 editions of the
  same pages for the differences in section 2 (protocol 3.2 and
  `min_protocol_version`/`max_protocol_version` appear first in 18,
  `sslnegotiation` in 17).
- RFC 5802 (SCRAM), RFC 7677 (SCRAM-SHA-256 and its test vector), RFC 5929
  (`tls-server-end-point`), RFC 4013 (SASLprep).
- For comparison only, nothing ported: pgx (Go, MIT) for its statement
  cache and per-column formats (`pgconn`, `pgtype`), tokio-postgres (MIT
  or Apache-2.0) for pipelined connections shared by tasks; libpq's source
  for `PQcmdTuples`'s tags and the SCRAM gs2 header choice
  (`fe-auth-scram.c`).

## 23. As built (P0)

Branch `pgwire-p0`. Everything in the P0 brief is in; no PostgreSQL code.
What differs from the brief or the design, and what was learned:

**Runtime** (`rt.h`, `native/rt.c`, in a section of their own at the end;
`rt.obx`, `rtx.obx` regenerated; reseeded).

- `obxlib_net_connect_unix`, `obxlib_net_listen_unix`,
  `obxlib_net_keepalive`, `obxlib_net_wait_for` as in the brief. A path of
  0 bytes is `-ENOENT`; 108 bytes or more is `-ENAMETOOLONG`, abstract names
  included (107 including the `@`).
- `obxlib_net_wait_for` with `fd < 0` is a pause that only `ms` ends (or,
  when cancellable, cancellation or the deadline). Not in the brief: `net`
  needs it to retry a Unix connect while the listener's backlog is full.
  `fd >= 0` must be a registered socket (`-EINVAL` otherwise; a listener's
  lazy registration is not done here).
- `obxlib_net_addr` reports an `AF_UNIX` socket as family 1 (the port and
  address bytes stay 0); before, it would have reported IPv4 garbage.
- A Unix `connect` that answers EAGAIN (full backlog) leaves the socket
  unconnected, and an epoll wait on it does not help (it reports writable at
  once, and no event follows). So `*pending = 1` means "try again" for a
  Unix socket, and `connectUnix` closes the socket, pauses (2 ms, doubling
  to 50) and connects again, until the task is cancelled, its deadline
  passes, or (`connectUnixFor`) `ms` is up.
- The compiler imports `os`, so the `os` commit changes the seed too: the
  seed was regenerated twice (runtime commit, `os` commit); the fixed point
  holds at the end (`seed_check.py`).

**net**: as the brief. `listenUnix` does not unlink the path on close or
before bind (a stale file gives EADDRINUSE; removing it is the caller's job:
`pgwire` never listens). `Listener.localAddr()` of a Unix listener is
`"unix:" + path` (the brief only says `localAddr()` of a Conn is `""`).
`setKeepAlive` on a Unix socket fails with the errno of the first TCP option
(ENOPROTOOPT) after setting `SO_KEEPALIVE`. A `...For` call's `ms` is the
limit for the whole call (a `writeFor` of many chunks shares it); `ms` 0
tries the operation once and times out if it would wait.

**tls**: as the brief, including `Conn.endPoint()`; `Context` keeps the two
new options. `Conn.readFor`/`writeFor` take one time limit across all the
socket calls of the call (a record needing several reads), by a deadline;
the handshake in `writeFor` shares it. OpenSSL 3's `EVP_get_digestbynid` is
a macro, so `endPoint` calls `EVP_get_digestbyname(OBJ_nid2sn(nid))`. The
tests used RSA (SHA-1, 256, 384), ECDSA (the test CA's certificate) and
Ed25519 certificates made at test time with the openssl command
(self-signed, trusted as their own CA): `endPoint()` equals the openssl
fingerprint with the same hash, SHA-256 for the SHA-1 one, and Ed25519
gives the Verify error. SNI is checked on the wire: a raw listener reads the
ClientHello and looks for the name in it.

**os**: `userName`, `homeDir` as the brief. `os` now imports `ffi`.

**crypto**: `pbkdf2Sha256` runs on the helper pool (a second, `blocking`
declaration of `PKCS5_PBKDF2_HMAC`; a test shows another task running
meanwhile). `unbase64` requires whole groups of four, `=` only as the last
one or two characters, and does not check the unused bits of the last
character (libpq's decoder does not either). HMAC with an empty key works
(tested).

**Tests**: `net_unix`, `tls_endpoint`, `os_user` are new, `crypto_basic` is
extended; they pass on the C and the native backend under `OBX_CHECK=1`.
`net_unix` is picked up by the gate's `net_*` globs (redteam tasks, TSan).
No measurements were needed in P0.

## 24. As built (P1)

Branch `pgwire-p1`. `obx/lib/pgwire.obx` (about 2,800 lines with comments)
and its tests; `postgres.obx` is untouched; no compiler or runtime change.
Everything in the P1 brief's API list is in, with the names and signatures
given there. What differs, and what was learned:

**Added to the public API** (small helpers the tests need; none changes a
brief signature): `Out`, `newOut` and the `put*` encoders for every frontend
message (`putStartup`, `putSslRequest`, `putCancelRequest`, `putQuery`,
`putParse`, `putBind`, `putDescribe`, `putExecute`, `putClose`, `putSync`,
`putFlush`, `putTerminate`, `putPassword`, `putSaslInitial`,
`putSaslResponse`, `putCopyFail`), `parseServerError` (an ErrorResponse
body), `newRawRows`, `socketDirFor(port, dirs)` (the default socket
directory choice, testable with temporary directories),
`Session.noticeCount()` and `notificationCount()` (the stored notices and
notifications have no accessors until P5). `Param` is a public type, as the
brief's `param` and `query` signatures need. `Out` is a buffer that keeps its
capacity between messages (an array cannot be emptied without freeing it).

**Configuration** (6.1 to 6.6).

- Error texts were checked against `psql` (libpq 16). One row of 6.1's table
  is wrong: a URI query parameter without `=` (`?x`) is `missing key/value
  separator "=" in URI query parameter: "x"`; `invalid URI query parameter:
  "x"` is for an unknown name (`?x=1`). pgwire says what libpq says. The
  URI errors that quote the string (`unexpected character`, brackets) quote
  it with the password masked.
- `connect_timeout=abc` is an error here (`invalid integer value`); libpq
  accepts it silently until it connects. `keepalives_*` likewise.
- The key file mode check (0600, or root 0640) happens when the TLS context
  is made, at connect, not in `parseConfig`.
- `sslmode=prefer` with a root certificate present verifies the chain (as
  `verify-ca`), as libpq; `sslrootcert=system` makes `verify-full` the default.
- Several hosts: a failure at the connection level (refused, closed, no SSL
  when required) tries the next host; a server's ErrorResponse, a failed
  login, a cancel or a deadline ends the attempt. One endpoint returns the
  error itself (its code kept); several return code 1 with one line per host.

**Transport and login.**

- `connect_timeout` bounds each endpoint by `task.within`; a timeout that
  was the task's own deadline reads `connect: deadline passed`, the other
  `connect: connect_timeout passed` (both E_TIMEOUT).
- Terminate and the TLS close_notify never park: a write with a time limit
  of 0 (`writeFor`), then the socket drops.
- `prefer`: after a failed handshake, a TLS error or a server refusal on a
  TLS connection, one new plain connection. `allow`: a server refusal of the
  plain startup, one TLS connection (as `require`). An `E` answer to the
  SSLRequest counts as a failed handshake (a new connection), not as `N`.
- The CVE-2021-23222 check reads the one answer byte alone and then looks for
  more with `readNow` before the handshake.
- A FATAL or PANIC ErrorResponse returns that error to the call that was
  waiting (code 7 with the SQLSTATE) and breaks the session; the next call is
  `E_IO "the connection is broken: <that error>"`.
- A write that fails, also one refused because the task was already
  cancelled, breaks the session (part of a message may be out). A cancelled
  task that calls `query` therefore loses its connection; a pool closes it.
- SCRAM-PLUS was proved against the real server: with `channel_binding=require`
  over TLS the login succeeds, without TLS it fails before anything is sent
  (the API has no accessor for the mechanism used).
- SASLprep: ASCII, plus the non-ASCII space mapping and the characters mapped
  to nothing; no NFKC, as designed.

**Queries.**

- `script` returns the last statement's reply; `query` the single reply.
  COPY: `G` is answered with CopyFail, `H` and `W` are read and dropped; the
  error is `E_INPUT "COPY is not supported yet"` and the session stays usable
  (live test and fake test).
- Portals: a limit that divides the row count exactly ends with one more,
  empty batch (10,000 rows by 1,000 is 11 batches: the server sends
  `PortalSuspended` after the last full batch). `receiveBatch` at
  CommandComplete or an ErrorResponse sends Sync and reads ReadyForQuery;
  the batch then has `more` false and, after an error, `err`.
- `closePortal` with an Execute outstanding waits `CLOSE_WAIT_MS`, then sends
  a CancelRequest and keeps reading, `ABORT_MS` in all; past that the session
  is broken (E_TIMEOUT). `cancelRequest` takes the address that worked
  (socket path, or the peer address of the TCP socket), as designed.
- The cancel drain and `closePortal` use the `...For` calls; a cancelled
  wait returns `E_CANCELLED "query cancelled"` or `E_TIMEOUT "query:
  deadline passed"` and the session is ready (live test: cancelled after
  100 ms from a task, `pg_sleep(30)`).
- The reply loop accepts any order of the messages it knows; only a DataRow
  before a RowDescription, a DataRow whose end is not its message's end, a
  malformed length, and unknown types are E_PROTOCOL.

**Language frictions met** (no change needed; worth knowing for P2): a match
on a `let`-bound `Result` cannot move its payload (use `match move(r)` or
match the call itself); a closure cannot move a captured variable (put it in
an `Option` and `replace` it); `closed` is a keyword; `try` needs a function
whose error type is the same (there is no `?`); constant tables are `&[T]`,
not `&[]T`; a `value record` with `str` fields is a copy type, so
`Option[ServerError]` copies.

**Tests** (C and native, `OBX_CHECK=1`; the `pgwire_*` files are in the gate's
redteam tasks step, ASan and UBSan):

| test | result |
|---|---|
| `pgwire_config` | 8 groups, 198 checks |
| `pgwire_codec` | 7 groups, 95 checks: byte vectors, RFC 7677's proof and signature, MD5, tags, parameters |
| `pgwire_fake` | 39 scripted scenarios, about 3 s with the two timeout cases: see the file's expect lines |
| `pgwire_live` | the local socket, peer authentication |
| `pgwire_live_tls` | ran live: SCRAM-SHA-256 over TCP with TLS 1.3 (the DSN from `~/.obo-pg-tls-dsn`) |

No measurements were taken in P1.

**For P2**: `Batch.rowAt` points at the DataRow's column count, which is what
`scanRow` takes; `Reply.rows` keeps the chunks of the whole reply, so a
`Rows` can hold the `RawRows` as they are.

## 25. As built (P2)

Branch `pgwire-p2`. `obx/lib/postgres.obx` is rewritten over `pgwire.Session`
(664 lines); the libpq client is `obx/lib/postgres_libpq.obx` (`git mv`, only
its header comment changed). No compiler, runtime or `pgwire` change. 6.1's
table is corrected: a URI query parameter without `=` is `missing key/value
separator "=" in URI query parameter: "x"` (as P1 built it).

**Differences from the design and the brief.**

- **`mut &Conn`.** A native session is mutated by every call, so `exec`,
  `query`, `querySql`, `stream` and `transaction` take `mut &Conn`, and the
  `Lease` ones `mut &Lease`, as `oracle` and `sqlserver` do (a `&Conn`
  receiver cannot reach a `mut &Session`). `transaction`'s body therefore gets
  the connection back: `body: &fn(mut &Conn): Result[T, Error]` (a closure
  that captured the `Conn` would borrow it twice). Section 4 and the brief
  said "unchanged signatures"; this cannot be, so the callers changed their
  borrows only (`&postgres.Conn` to `mut &postgres.Conn`, `let` to `var` for
  leases and connections, the body's parameter): `postgres_live`,
  `task_postgres_async`, `task_postgres_pool`, `http_server_pg_cancel`,
  `postgres_ledger`, `httpd`. No expected line changed. `serverVersion`,
  `idle`, `inTransaction`, `parameter`, `backendPid` and `lastServerError`
  keep `&Conn`.
- **`Lease` holds `cs: []Conn`** (one element, none once given back) instead
  of a `Conn` replaced by a null handle: a native `Conn` cannot be made empty,
  and an `Option[Conn]` cannot be borrowed `mut` out of a match.
- **`libVersion()` is gone from `postgres`**, as section 4 says; the one use
  (the ledger example's first line) now prints the server version only.
- **Status constants** are `pgwire`'s: `CONNECTION_BAD`, `PGRES_FATAL_ERROR`
  and the 17 `OID_*` are `pub const` aliases of `pgwire.CONNECTION_BAD`,
  `FATAL_ERROR`, `OID_*`; `CONNECTION_OK`, `PGRES_EMPTY_QUERY`,
  `PGRES_COMMAND_OK`, `PGRES_TUPLES_OK`, `PGRES_SINGLE_TUPLE` stay as literals
  (pgwire has no equivalent; nothing returns them).
- **A statement that fails before its first row, streamed**, reports it from
  the first `fill` or `next`, not from `stream()` (12.2: the first batch stays
  on the wire). `idle()` is true afterwards as before. The old client said it
  from `stream()` (single-row mode waited for the first result). A statement
  without rows (INSERT, UPDATE) streamed is run by the Execute that
  `stream()` sends; its error shows at the first `fill`.
- **Stream sizes**: Execute 256 for the first batch and 1,024 for each later
  one, no fetch-ahead and no byte sizing (P3).

**The oracle.** `postgres_live_diff` compared 5,264 results (each a whole
buffered or streamed result with every column, cell, accessor and error) and
found no difference in the clients' values. What it did find, and who was
right:

- the empty query, streamed: libpq's `stream` answers `unsupported result
  status 0 (COPY?)` (it treats PGRES_EMPTY_QUERY as an error); native gives a
  stream with no columns and no rows. Native is right; compared buffered only.
- COPY FROM STDIN through `postgres_libpq` leaves the call waiting forever
  (the connection is stuck in COPY, the old known limit); native refuses it
  with `E_INPUT "COPY is not supported yet"` and stays usable
  (`pgwire_live`). Not compared.
- NOTICE and WARNING: libpq prints them on stderr (`there is already a
  transaction in progress`, `RAISE NOTICE`); native keeps them in the session.
- the error timing above (first `fill` against `stream()`): the comparison
  treats an error before any row alike.

Everything else matched: the edge values of every mapped type (numeric with
38 and 39 digits, `1e-16383`, NaN, numeric(p, s) with a negative scale, 1 MB
text and bytea, all 256 byte values, date limits and infinities,
`timestamptz` under five zones, interval under four styles), 2,000 generated
rows, 24 parameter values alone and into 13 column types (bytea goes as
binary now: the stored values are equal), 65,536 parameters (E_INPUT on
both), and the errors (22012 mid-result and late, 22003, 42601, 23505,
23502, a custom SQLSTATE, 25P02 after an error in a block, a script failing
in the middle and rolled back, with `idle()` after each).

**Tests** (C and native, `OBX_CHECK=1`): `postgres_basic` (its `libpq >= 10`
line moved to `postgres_libpq_basic`), `postgres_libpq_basic`, `postgres_live`,
`postgres_live_stream`, `task_postgres_async`, `task_postgres_pool`,
`http_server_pg_cancel` (all ran live against the local socket, none
skipped, expected lines unchanged), `postgres_live_diff` (about 3 s). `httpd`
serves `/db`; `dbstream pg` stream and buffered agree (checksum 212159094,
20,000 rows).

## 26. As built (P3)

Branch `pgwire-p3` (from ef18639e). The streaming speed of P2's client, the
decimal fast path (7.6), the `pglibpq` bench client and the latency mode.

**Differences from the design and the brief.**

- **`Conn` gained fields** (`fetchRows`, `ahead`), and the three methods of
  12.3 and 12.4 take the receiver the way the other connection methods do:
  `roundTrips(): int` (`&Conn`), `setFetchRows(n)` and `setFetchAhead(on)`
  (`mut &Conn`; `n <= 0` restores the byte rule, otherwise `min(n, 100000)`).
- **The byte rule is in `Stream`**, not in `pgwire`: `rowsNext()` sizes each
  later Execute from the last batch's average DataRow body and the widest
  one seen, both kept per stream (`Batch.widest` is added in `pgwire`, the
  body length `m.end - m.at`). The first Execute asks for 256 rows.
- **Fetch-ahead** is `sendAhead`: when a batch arrives and more rows follow,
  the next Execute goes out before the batch's rows are read; at most one is
  outstanding. A failure to send is kept as the batch's error, so it shows
  after the rows already read (the `error ahead` line of the live test).
- **Round trips** count a portal's open, each Execute and its close; the
  final Sync does not count (it is not a round trip of the query's work).
- **Decimal fast path**: `pgwire.decimalOfText` reads `[-]digits[.digits]`
  with at most 38 significant digits and 1000 fraction digits straight into
  an `i128`, and makes the decimal with `fromFraction`. Any other text (sign
  of exponent, NaN, a value out of range) goes to `sqldialect.parseDecimal`.
  The codec test checks 100,000 generated numeric texts against it (50,778
  decimals, 49,222 errors, 0 differences).
- **`pglibpq` in `dbstream`**: `dbstream.obx` gained a client on
  `postgres_libpq` (the old client, kept), so the two clients run in one
  `run.py` table. `run.py` gained `--latency` for `pg` and `pglibpq` (over a
  delay proxy, with the TLS DSN read for its host and port only).
- **Not measured**: `http_gate --db` (section 18's last row). Not run in
  this stage.

**Measured** (cycles and instructions a row, user space; full table in
db-streaming section 7): native streamed 1,410 cycles a row (before P3:
1,401; the change is in time, 816 to 497 ms, not in cycles); buffered 1,526
(before P3: 1,561). With the decimal fast path switched off in an A/B build,
streamed 1,377 and buffered 1,536: no measurable gain on this box, kept
because 7.6 says so. A profile of the native stream (`OBX_NATIVE_MAP`):
`parseInt` 11.5 %, the fast path and its `i128` multiplies 13 %, the
numeric check and `quantize` about 10 %. Peak RSS of the stream is 9.8 MB at
1,000,000 rows, flat in the row count.

**Tests** (C and native, `OBX_CHECK=1`; 14 of 14 on both): `postgres_live_fetch`
(new: the round trips of narrow and wide rows through a buffer of 7, one row
and short results, `setFetchRows(100)` with fetching ahead off, early close,
drop and an error in the batch fetched ahead, all with the connection used
after), `pgwire_codec` (the numeric differential above), and the existing
`postgres_*`, `pgwire_*`, `http_server_pg_cancel` and `task_postgres_*`
tests. `pgwire_live_tls` ran against the DSN file's server (the default CA,
not skipped). Expected lines unchanged in every test.

**Open** (for the user): the batch size at high latency (note 8 of section
21); the buffered native target (1,526 against 1,450), which is text
decoding (db-streaming section 7).
