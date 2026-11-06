# Native PostgreSQL client: stage briefs

The design is [pgwire.md](pgwire.md); section numbers below are its
sections. Each brief is meant to be enough to start from alone, together
with that note. The stages build on each other in order; each ends with a
reviewed merge by the lead before the next starts.

| stage | what | size (new or changed lines, tests included) |
|---|---|---|
| P0 | `net` Unix sockets, cleanup I/O and keepalive (both runtimes, reseed); `tls` verify-ca, SNI switch, channel binding data, cleanup I/O; `crypto` SHA-256, HMAC, PBKDF2-SHA-256, base64 | small to medium: about 700 |
| P1 | `pgwire`: connection strings, env, `.pgpass`; transport; startup, TLS, authentication; simple and extended queries; raw rows; errors; portals; cancel; codecs; offline and fake-server tests | large: about 3,500 |
| P2 | `postgres` rewritten on `pgwire`, old one renamed `postgres_libpq`; the existing tests pass natively; the differential test | medium to large: about 2,000 |
| P3 | streaming speed: batches sized by bytes, fetch-ahead, in-place scanning, the numeric fast path, the bench | medium: about 700 |
| P4 | the pool's alive check, `httpd`/`http_gate --db` measured, the `batch` (pipeline) API | small to medium: about 600 |
| P5 | later items, each its own small brief when picked: COPY, LISTEN/NOTIFY, notices, the service file, TLS cancel requests, CRLs, `target_session_attrs`, `load_balance_hosts`, `requirepeer`, direct TLS, NFKC SASLprep, statement cache study | small each |

## Rules every stage carries

Copy these into the agent's prompt with the brief.

- Work on your own branch from current `main` in your worktree. Commit
  there; do not merge into main and do not push.
- Commit with a message file and `git commit -F <file>` (`-F -` fails
  here). End the message with:

      Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>
      Claude-Session: https://claude.ai/code/session_018PJaBBJf9SYL2T9iR5WgrY

- Never print, log, copy or commit a DSN or a password: `OBX_PG_DSN`,
  `OBX_PG_TLS_DSN`, and do not touch the MSSQL or Oracle DSNs. Tests read
  them from the environment and never echo them; error texts never contain
  a password value.
- Never `cp` onto `obx/compiler/out/obxc`: build to `out/obxc.new`, then
  `mv` it (cached builds are hard links).
- Run `obx/tools/gate.sh full`, save its whole output to a scratch file,
  and it must say PASS. Report the file's path.
- No `while pgrep -f X` wait loops (they match themselves): use the tool's
  background run, or wait on a PID.
- Never touch the untracked root files (`app.obx`, `build.log`, the
  libssl-dev `.deb`, `out/`, `parse_actual.txt`, `parse_run.err`,
  `probe.c`, `probe.cbind`, `deepseek.sh`) or `archive/`.
- Measurements: run `uptime` first; pin to a named CPU whose SMT sibling is
  idle (pairs 0-1, 2-3, ...); do not measure while other agents measure;
  least of 5 interleaved runs; record CPU, load and time with the numbers.
- Language changes are the user's decision. If the work seems to need one,
  stop and report it; do not work around it with a hack.
- Fractional literals in obx need a suffix: `1.5m` (Decimal), `1.5f`
  (float).
- `python3 obx/tests/markdown.py` must still pass after any `.md` change.
- Design questions not settled by pgwire.md: stop and ask the lead; do not
  decide them. Where pgwire.md and this brief disagree, this brief wins for
  names and signatures, pgwire.md for behaviour; report the disagreement.
- At the end, add an "As built (Pn)" section to `obx/design/pgwire.md`:
  what differs from the design, what was learned, the measurements.

## P0: net, tls and crypto additions

Branch `pgwire-p0`.

### Scope

What `pgwire` needs from the libraries below it (pgwire.md sections 3,
5.2, 5.5, 13). No PostgreSQL code.

### Runtime (both runtimes, then regenerate and reseed)

Files: `obx/compiler/rt.h`, `obx/compiler/native/rt.c`; then
`python3 obx/compiler/gen_rt.py` (rt.obx), `python3 obx/compiler/gen_rtx.py`
(rtx.obx, native/obxrt.h), `obx/compiler/reseed.sh`. Follow commit
e36ba6e9 (the HTTP server's runtime pieces) as the pattern: new functions
in their own section at the end of each runtime.

| function | behaviour |
|---|---|
| `int64_t obxlib_net_connect_unix(const uint8_t* path, int64_t n, int64_t* pending)` | `socket(AF_UNIX, SOCK_STREAM \| NONBLOCK \| CLOEXEC)`, `connect` to `path[0..n)`; a path starting with `@` is the Linux abstract namespace (`sun_path[0] = 0`, the rest after it, length `offsetof(sun_path) + n`); `n` over 107 is `-ENAMETOOLONG`; `*pending` as `obxlib_net_connect` (EINPROGRESS, EAGAIN for a full backlog counts as pending too); registers it as `rt_net_new` does |
| `int64_t obxlib_net_listen_unix(const uint8_t* path, int64_t n, int64_t backlog)` | bind and listen on a Unix path (abstract with `@`); a lazy listener as `obxlib_net_listen` |
| `int64_t obxlib_net_keepalive(int64_t fd, int64_t on, int64_t idle, int64_t intvl, int64_t cnt)` | `SO_KEEPALIVE`, then `TCP_KEEPIDLE`, `TCP_KEEPINTVL`, `TCP_KEEPCNT` for each value above 0; 0 or `-errno` |
| `int64_t obxlib_net_wait_for(int64_t fd, int64_t dir, int64_t ms, bool cancellable, const char* w)` | `obxlib_net_wait` with a time limit and optional cancellation: parks until ready, ms passed (`ms` >= 0) or, when `cancellable`, the task's cancellation or deadline; returns 0, 1 cancelled, 3 deadline or ms passed. It must keep net's edge-triggered registration (do not use `obxlib_fd_wait`, whose one-shot `EPOLL_CTL_MOD` would replace it) |

Add each to the extern lists `gen_rtx.py` writes (rtx.obx lists them
twice, as for `obxlib_net_connect`).

### net (obx/lib/net.obx)

```obx
pub fn connectUnix(path: str, w: Where): Result[Conn, io.IoError]
pub fn connectUnixFor(path: str, ms: int, w: Where): Result[Conn, io.IoError]
pub fn listenUnix(path: str): Result[Listener, io.IoError]
pub fn connectFor(addr: str, ms: int, w: Where): Result[Conn, io.IoError]
pub fn (c: &Conn) readFor(buf: mut &[byte], ms: int, w: Where): Result[int, io.IoError]
pub fn (c: &Conn) writeFor(data: &[byte], ms: int, w: Where): Result[Unit, io.IoError]
pub fn (c: &Conn) setKeepAlive(on: bool, idleS: int, intervalS: int, count: int): Result[Unit, io.IoError]
pub fn (c: &Conn) isUnix(): bool
```

- `...For` calls ignore the task's cancellation and deadline and give up
  after `ms` milliseconds (ETIMEDOUT): for cleanup after a cancellation
  (pgwire.md section 13). They still stop with exit 13 inside
  `Shared.with`, like every call that can park.
- A Unix socket's `peerAddr()` and the address in its errors is
  `"unix:" + path`; `localAddr()` is `""`. `Listener.accept` on a Unix
  listener gives Conns whose `peerAddr()` is `"unix:"`.
- `connectFor` takes only what `connect` takes (an IP address and port; no
  names).

### tls (obx/lib/tls.obx)

| addition | behaviour |
|---|---|
| `Options.verifyName: bool` (`defaults()`: true) | false: the chain is verified but no name is set (`SSL_set1_host` skipped); SNI is still sent for a name. `engine("")` is then allowed with verification on |
| `Options.sni: bool` (`defaults()`: true) | false: no SNI |
| `pub fn (e: &Engine) endPoint(): Result[str, Error]` | RFC 5929 `tls-server-end-point`: the DER hash of the peer certificate with its signature's hash (`X509_get_signature_nid`, `OBJ_find_sigid_algs`; MD5 or SHA-1 become SHA-256; `EVP_get_digestbynid`; `X509_digest`); `Kind.Verify` error "could not determine server certificate signature algorithm" when there is none (Ed25519) or no certificate; raw bytes, not hex |
| `pub fn (c: &Conn) endPoint(): Result[str, Error]` | the same through the Conn |
| `pub fn (c: mut &Conn) readFor(buf: mut &[byte], ms: int, w: Where): Result[int, Error]` and `writeFor(data: &[byte], ms: int, w: Where): Result[Unit, Error]` | `read`/`write` over the socket's `readFor`/`writeFor` |

### os (obx/lib/os.obx)

```obx
pub fn userName(): str     // getpwuid(geteuid())->pw_name; "" when there is no entry
pub fn homeDir(): str      // $HOME when set and not empty, else the passwd entry's pw_dir, else ""
```

`extern "c" fn geteuid(): u32` and `extern "c" fn getpwuid(uid: u32): ptr`;
the fields read with `ffi.peekPtr` (pw_name at word 0, pw_dir at word 4 on
x86-64 glibc). libpq calls `getpwuid` inline too.

### crypto (obx/lib/crypto.obx)

```obx
pub fn sha256(data: str): Result[str, Error]                       // 32 bytes
pub fn hmacSha256(key: str, data: str): Result[str, Error]         // 32 bytes (OpenSSL HMAC())
pub fn pbkdf2Sha256(password: str, salt: str, iterations: int, keyLen: int): Result[str, Error]
pub fn base64(data: str): str                                      // standard alphabet, padded
pub fn unbase64(text: str): Option[str]                            // strict: padding required, no white space
```

`pbkdf2Sha256` calls `PKCS5_PBKDF2_HMAC` through a second declaration
marked `blocking` (`extern "crypto" fn pbkdf2Blocking(...): i32 as
"PKCS5_PBKDF2_HMAC" blocking`, SPEC 12), so a large iteration count runs on
the helper pool. Its failure text is "PBKDF2 failed" (OpenSSL's error queue
belongs to the helper thread). Limits as `pbkdf2Sha512`.

### Tests (new files in obx/lib/tests/run/)

| test | checks |
|---|---|
| `net_unix.obx` | listenUnix and connectUnix on a path in a temporary directory and on an abstract `@name`; echo both ways; peerAddr; a missing path (ENOENT); a path of 108 bytes (ENAMETOOLONG); readFor with nothing to read gives ETIMEDOUT after about ms; in a cancelled task readFor still reads data that arrives, while read gives ECANCELED; within a passed deadline the same; setKeepAlive on a TCP socket |
| `tls_endpoint.obx` | `endPoint()` on a test server with an RSA-SHA256 certificate (support/tlstest.obx makes one) equals the certificate's SHA-256 fingerprint (`Cert.sha256`, hex of the same DER hash); verifyName false accepts a certificate with a wrong name and still refuses an unknown CA; sni false |
| `os_user.obx` | `userName()` equals `$USER` when set (skip the check otherwise); `homeDir()` with `HOME` set, empty and unset |
| `crypto_basic.obx` (extend) | SHA-256 of "" and "abc" (FIPS vectors); HMAC-SHA-256 RFC 4231 cases 1 and 2; PBKDF2-HMAC-SHA-256: RFC 7914 section 11's first vector (P "passwd", S "salt", c 1, 64 bytes) and the widely published ones for password "password", salt "salt", 32 bytes (c 1: `120fb6cf...`, c 4096: `c5e478d5...`); base64 of RFC 4648's vectors; unbase64 rejects bad padding and characters |

Put `net_unix` in the gate's TSan step's list next to `net_*` (it already
matches `net_*`: check).

### Done

All of the above; `net_unix`, `tls_endpoint`, `os_user`, `crypto_basic` pass on C and
native under `OBX_CHECK=1`; the seed fixed point holds after the reseed;
`gate.sh full` PASS (output saved).

## P1: pgwire

Branch `pgwire-p1`. Depends on P0.

### Scope

New module `obx/lib/pgwire.obx` (pgwire.md sections 5 to 13): everything
on the wire, with no program API. Do not touch `postgres.obx` yet.
NOTICE needs no change (nothing is ported).

### API (exact)

```obx
// imports: net, tls, crypto, sqldialect, sqlrows, time, math, os, io, fs, strings

pub type Error = sqldialect.DbError
pub const PROTOCOL = 196608
pub const CONNECTION_BAD = 1          // connect failures
pub const FATAL_ERROR = 7             // server errors (PGRES_FATAL_ERROR)
// OID_BOOL 16 ... OID_TIMESTAMPTZ 1184: the 17 OID constants of postgres.obx, same names and values
pub const OID_OID = 26
pub const OID_JSON = 114
pub const OID_JSONB = 3802
pub const OID_UUID = 2950
pub const OID_INTERVAL = 1186
pub const TX_IDLE = 73                // 'I'
pub const TX_BLOCK = 84               // 'T'
pub const TX_FAILED = 69              // 'E'
pub const ABORT_MS = 5000
pub const CLOSE_WAIT_MS = 1000

// ---- configuration (pgwire.md section 6)
pub type SslMode = value closed { Disable; Allow; Prefer; Require; VerifyCa; VerifyFull }
pub type ChannelBinding = value closed { Disable; Prefer; Require }
pub type Endpoint = value record {
    pub host: str          // as given: a name, an address, "/dir" or "@name" (Unix)
    pub hostaddr: str      // "" when not given
    pub port: int
}
pub fn (e: &Endpoint) isUnix(): bool
pub fn (e: &Endpoint) socketPath(): str        // "<dir>/.s.PGSQL.<port>" for a Unix endpoint
pub type Setting = value record { pub name: str; pub value: str }
pub type KeepAlive = value record { pub on: bool; pub idleS: int; pub intervalS: int; pub count: int }
pub type Config = record {
    pub endpoints: []Endpoint
    pub user: str
    pub password: Option[str]       // from the string or PGPASSWORD; .pgpass is read at connect
    pub passfile: str
    pub dbname: str
    pub sslmode: SslMode
    pub sslrootcert: str            // a path, "system", or "" (no file: none exists at the default path)
    pub sslcert: str                // "" none
    pub sslkey: str
    pub sslsni: bool
    pub sslMin: tls.Version
    pub sslMax: tls.Version
    pub channelBinding: ChannelBinding
    pub requireAuth: str            // as given; "" any
    pub connectTimeoutMs: int       // -1: none; 1 s counts as 2 s
    pub keepAlive: KeepAlive
    pub startup: []Setting          // StartupMessage pairs after user and database, in pgwire.md 5.1's order
}
pub fn parseConfig(conninfo: str): Result[Config, Error]   // string, then environment, then defaults
pub fn masked(conninfo: str): str
pub fn passwordFor(cfg: &Config, ep: &Endpoint): Option[str]  // the .pgpass lookup (6.4)

// ---- results
pub type Column = value record {
    pub name: str; pub tableOid: int; pub attnum: int; pub typeOid: int; pub typeSize: int; pub typmod: int; pub format: int
}
pub type RawRows = record {        // pgwire.md section 10
    pub chunks: []str
    pub rowChunk: []i32            // per row
    pub starts: []i32              // per value, row-major: offset in the row's chunk
    pub lens: []i32                // per value: -1 NULL
    pub ncols: int
}
pub fn (r: &RawRows) count(): int
pub fn (r: &RawRows) isNull(row: int, col: int): bool          // caller checks ranges
pub fn (r: &RawRows) bytes(row: int, col: int): &str           // the value's text (not NULL)
pub type Reply = record { pub columns: []Column; pub rows: RawRows; pub tag: str; pub empty: bool }
pub fn affectedOf(tag: str): int                               // PQcmdTuples' rule (7.7)

pub type ServerError = value record {
    pub severity: str; pub code: str; pub message: str; pub detail: str; pub hint: str; pub position: str
    pub internalPosition: str; pub internalQuery: str; pub where: str; pub schema: str; pub table: str
    pub column: str; pub dataType: str; pub constraint: str; pub file: str; pub line: str; pub routine: str
}
pub fn (e: &ServerError) dbError(): Error                      // {code: 7, state: code, msg: message}

// ---- parameters (7.3)
pub type Param = value record { pub oid: int; pub binary: bool; pub null: bool; pub data: str }
pub fn param(v: &sqldialect.Value): Result[Param, Error]

// ---- the session
pub type Session = record { /* private */ }
pub fn connect(cfg: &Config, w: Where): Result[Session, Error]
pub fn (s: &Session) broken(): bool
pub fn (s: &Session) ready(): bool                 // state Ready and not broken
pub fn (s: &Session) txStatus(): int               // TX_IDLE, TX_BLOCK, TX_FAILED
pub fn (s: &Session) parameter(name: str): Option[str]
pub fn (s: &Session) serverVersion(): int
pub fn (s: &Session) backendPid(): int
pub fn (s: &Session) lastServerError(): Option[ServerError]
pub fn (s: &Session) roundTrips(): int             // requests that waited for a reply, login included
pub fn (s: mut &Session) close()                   // Terminate by writeNow, socket closed; never parks
pub fn (s: mut &Session) script(sql: str, w: Where): Result[Reply, Error]                    // simple Query
pub fn (s: mut &Session) query(sql: str, params: &[Param], w: Where): Result[Reply, Error]   // extended, Execute 0, Sync
pub fn (s: mut &Session) settle(w: Where): Result[Unit, Error]   // finishes an abandoned portal (12.6); every request calls it first

// ---- portals (12.2, 12.5): P1 has no fetch-ahead and fixed row counts; P3 adds them
pub type Batch = record {
    pub chunks: []str
    pub rowChunk: []i32            // per row: chunk index
    pub rowAt: []i32               // per row: offset of the DataRow's column count in its chunk
    pub more: bool                 // PortalSuspended: more rows to Execute for
    pub err: Option[Error]         // an ErrorResponse after these rows (the portal is gone, Sync done)
    pub bytes: int                 // the DataRows' bytes, for sizing the next Execute
}
pub fn (s: mut &Session) openPortal(sql: str, params: &[Param], rows: int, w: Where): Result[[]Column, Error]
                                   // Parse, Bind, Describe portal, Execute rows, Flush; reads to RowDescription or NoData
pub fn (s: mut &Session) execute(rows: int, w: Where): Result[Unit, Error]       // Execute, Flush (no read)
pub fn (s: mut &Session) receiveBatch(w: Where): Result[Batch, Error]           // reads the outstanding Execute's reply; at CommandComplete or error: Sync and ReadyForQuery
pub fn (s: mut &Session) closePortal(w: Where): Result[Unit, Error]             // 12.5's close steps; no-op when no portal
pub fn (s: &Session) portalOpen(): bool
pub fn scanRow(d: &str, at: int, ncols: int, starts: mut &[]int, lens: mut &[]int): Result[int, Error]  // the position after the row; E_PROTOCOL

// ---- cancellation (13)
pub fn (s: &Session) cancelRequest(w: Where): Result[Unit, Error]

// ---- value codecs (moved from postgres.obx, unchanged behaviour)
pub fn describe(cols: &[]Column): []sqlrows.Column     // postgres.obx's describe over Column
pub fn typeText(oid: int, typmod: int, p: int, s: int): str
pub fn kindsOf(oid: int, typmod: int, p: int, s: int): int
pub fn codec(): sqlrows.Codec                          // pgCodec(): pInt ... pOffset
pub fn decimalOfText(t: &str): Result[Decimal, Error]   // sqldialect.parseDecimal (P3 adds the fast path here)

// ---- SCRAM pieces, public for the tests
pub fn scramClientProof(password: str, salt: str, iterations: int, authMessage: str): Result[str, Error]
pub fn scramServerSignature(password: str, salt: str, iterations: int, authMessage: str): Result[str, Error]
pub fn md5Password(user: str, password: str, salt: str): Result[str, Error]   // "md5..." text
```

Private structure (names free, shape fixed): a `Stream` enum over
`net.Conn` and `tls.Conn` as in `tns.obx` (Plain, Tls, Gone); an `Inbox`
and the chunking of pgwire.md section 10 (64 KB, whole messages copied once
into a `str`, growth by doubling as bytes arrive, the 1 GiB + 64 KB limit,
shrink after a message over 1 MB); one reused output `[]byte`; the state
machine of section 9; the ParameterStatus map; the last 100 notices; the
notification queue (10,000).

### Behaviour

Everything in pgwire.md sections 5 to 13, with these limits for P1:

- `openPortal`'s `rows` and `execute`'s `rows` are what the caller says
  (P3 adds byte sizing and fetch-ahead in `postgres`; pgwire only needs
  `execute` to be callable right after `receiveBatch`).
- Every wait goes through the Stream's read with `w`. A read that returns
  cancelled or past the deadline while a reply is outstanding: `cancelRequest`,
  then drain with the `...For` calls to ReadyForQuery (Sync first when a
  portal is open), at most `ABORT_MS`; then the Err E_CANCELLED `query
  cancelled` or E_TIMEOUT `query: deadline passed`; broken if the drain
  failed. During connect: `connect cancelled`, `connect: deadline passed`,
  `connect: connect_timeout passed` (today's texts).
- `closePortal` with an Execute outstanding waits `CLOSE_WAIT_MS`, then
  cancels (12.5).
- COPY responses as in 7.7.

### Tests (obx/lib/tests/run/; support in obx/lib/tests/support/pgtest.obx)

| test | needs | checks |
|---|---|---|
| `pgwire_config.obx` | none | every row of pgwire.md 6.1 to 6.6: keyword/value quoting and escapes; URI parts, percent-decoding, IPv6, `%2F` socket hosts, `?host=`, `ssl=true`; multiple hosts and port lists (and the mismatch error text); each environment variable of 6.2 (set and unset with `os.setEnv`/`os.unsetEnv`; restore after); defaults (user from `os.userName()` or the passwd entry, dbname = user, port 5432, socket directory choice with a temporary directory standing in); `.pgpass`: matching order, `*`, escapes, `localhost` for the default socket, permissions 0644 ignored, a directory ignored; the libpq error texts of 6.1; the refused and ignored keywords of 6.6; `masked()`; no error text contains the password |
| `pgwire_codec.obx` | none | messages built by the client against byte vectors (StartupMessage, SSLRequest, CancelRequest, Parse, Bind with mixed formats, Describe, Execute, Close, Flush, Sync, Terminate, PasswordMessage, SASL messages); `scanRow` on DataRows (NULLs, empty values, counts that disagree, a negative length other than -1, a length past the end: E_PROTOCOL); `affectedOf` for each tag; `param` for every Value; SCRAM: RFC 7677's exchange (user "user", password "pencil", client nonce "rOprNGfwEbeRWgbNEkqO", salt "W22ZaJ0SNY7soEsUEjb6gQ==", 4096 iterations: the proof and the server signature of the RFC); `md5Password` against `md5` + md5(md5("pencil" + "user") hex + salt) computed in the test from `crypto.md5`; ServerError fields from an ErrorResponse body |
| `pgwire_fake.obx` | none (`needs: ssl`) | a scripted server task (pgtest.obx) on 127.0.0.1 and on a Unix socket in a temporary directory: trust; cleartext; MD5; SCRAM (the fake computes the server side with `scramServerSignature`) with a wrong server signature, a server nonce not starting with ours, `i=0`, an `e=` error; SCRAM-PLUS over TLS (the fake's certificate from tlstest.obx; the fake checks the `c=` binding data against its own certificate's `endPoint`); `channel_binding=require` against trust, against SCRAM without PLUS, over plain TCP; `require_auth`; SSLRequest answered `N` under prefer and require; `S` followed by a stray byte (refused, E_PROTOCOL); an ErrorResponse to SSLRequest; NegotiateProtocolVersion with minor 0 (accepted) and with an option listed (refused); BackendKeyData with 4 and 32-byte keys; ParameterStatus later in the session; a simple query script with two results and an error; an extended query; a portal of 300 rows read in batches of 7 with PortalSuspended, the client's Executes seen by the fake; rows split across socket reads at every byte position of a 3-row reply; closePortal (Close and Sync seen); a cancelled wait (the fake sees a CancelRequest on a new connection with the pid and key it sent, then answers 57014 and ReadyForQuery; the session is ready after); a FATAL mid-session (broken, E_IO after); 300 mutated or cut replies (bit flips, lengths changed, negative lengths, a 2 GB declared length followed by EOF, unknown message types, EOF at every message boundary), each ending in Ok or Err, never a trap or a hang; `openHandles()` of tls 0 |
| `pgwire_live.obx` | `OBX_PG_DSN` (default the local socket; exit 77 without a server) | connect (peer over the Unix socket); `parameter("server_version")`, `serverVersion()`, `backendPid()` equals `pg_backend_pid()`; script and query; parameters of every Value; errors with SQLSTATE and the state after (8.2) inside and outside a block; a portal of 10,000 rows in batches of 1,000; closePortal mid-way then a query; `pg_sleep(30)` cancelled from a task (E_CANCELLED within 1 s, the session ready after) and under `task.within(200)` (E_TIMEOUT); a NOTICE does not disturb a query; COPY TO STDOUT and FROM STDIN refused with the session usable after |
| `pgwire_live_tls.obx` | `OBX_PG_TLS_DSN` (a TCP DSN with a password; exit 77 without); `OBX_PG_TLS_CA` optional | sslmode disable, prefer, require (TLS used: `SELECT ssl FROM pg_stat_ssl WHERE pid = pg_backend_pid()`); verify-ca with `OBX_PG_TLS_CA` (skipped without it); channel binding prefer (PLUS chosen when TLS) and require; a wrong password (code 1, state 28P01) |

Add `obx/lib/tests/run/pgwire_*.obx` to the "redteam tasks" step of
`obx/tools/gate.sh` (ASan and UBSan).

### Done

All tests pass on C and native under `OBX_CHECK=1` (the live ones on this
machine with `OBX_PG_DSN` unset, i.e. the local socket; report whether
`pgwire_live_tls` ran or was skipped); `gate.sh full` PASS (output saved);
"As built (P1)" written.

## P2: postgres on pgwire

Branch `pgwire-p2`. Depends on P1.

### Scope

pgwire.md sections 3, 4, 7, 11, 12 (streams without fetch-ahead), 15
(unchanged pool), 17.

### Files

| file | change |
|---|---|
| `obx/lib/postgres.obx` to `obx/lib/postgres_libpq.obx` | `git mv`; only its header comment changes (module name, "the libpq client, kept as the oracle"); its code stays as it is |
| `obx/lib/postgres.obx` | new: the API of pgwire.md section 4 over `pgwire.Session` |
| `obx/lib/tests/run/postgres_basic.obx` | the `libpq >= 10` line moves to `postgres_libpq_basic`; header text; `needs: pq` removed |
| `obx/lib/tests/run/postgres_libpq_basic.obx` | new: today's `postgres_basic` on `postgres_libpq` |
| `postgres_live`, `postgres_live_stream`, `task_postgres_async`, `task_postgres_pool`, `http_server_pg_cancel` | comments: libpq, PQcancel, single-row mode replaced by what happens now; `needs: pq` removed; expected lines unchanged (if one must change, stop and report it) |
| `obx/lib/tests/run/postgres_live_diff.obx` | new: the differential test |
| `obx/lib/examples/postgres_ledger.obx`, `obx/cmd/httpd.obx`, `obx/bench/dbstream/dbstream.obx` | unchanged; they must build and run |
| `obx/lib/README.md`, `obx/design/db-streaming.md` section 5 | the PostgreSQL column: what it is now (rebase first: another branch edited `obx/lib/README.md`) |
| `HISTORY.md` | one entry |

### API (exact)

Everything in pgwire.md section 4's "Unchanged" table with the same
signatures as today's `postgres.obx`, plus:

```obx
pub fn (c: &Conn) inTransaction(): bool             // txStatus() != TX_IDLE
pub fn (c: &Conn) parameter(name: str): Option[str]
pub fn (c: &Conn) backendPid(): int
pub fn (c: &Conn) lastServerError(): Option[pgwire.ServerError]
```

Shapes: `pub type Conn = record { s: pgwire.Session }` (its drop:
`s.close()`, `OPEN -= 1`); `pub type Rows = record { cols: []pgwire.Column;
raw: pgwire.RawRows; tag: str; empty: bool }` (its drop: `OPEN -= 1`; Rows
keep counting in `openHandles()` as today; `columnTypmod` and `columnType`
read `cols`). `connect(conninfo, w)` =
`pgwire.parseConfig` then `pgwire.connect`. `exec` = `settle`, `script`;
`query` = `settle`, `param` for each arg, `pgwire.query`. `idle()` =
`ready() && txStatus() == TX_IDLE && !portalOpen()`. The accessors keep
today's checks and error texts, reading `raw.bytes(r, c)` instead of
`PQgetvalue`. `transaction` unchanged. Pool, Lease, handOn unchanged.

Streams (`Stream[B, T]`, same public shape): `stream` = `settle`,
`openPortal(sql, params, 256, w)`, `bind`; fields hold the current
`pgwire.Batch`, the row index in it, `starts`/`lens` of ncols and the state
of `sqlrows`. `fill` scans each DataRow with `pgwire.scanRow` into
`starts`/`lens` and calls `read` with `sqlrows.rowView(cols, codec,
&batch.chunks[rowChunk[i]], &starts, &lens, row)`; when the batch is used
up and `more`, `execute(1024)` then `receiveBatch`. `close` =
`closePortal`. No fetch-ahead yet (P3).

### The differential test (postgres_live_diff.obx)

pgwire.md section 17, "The differential oracle": the edge-value queries,
the 2,000 generated rows (a fixed seed; a small linear congruential
generator in the test), the parameters, the errors; buffered and streamed;
both clients on their own connections to `OBX_PG_DSN`; prints each
difference with the query's values only and ends with `differences: 0`
(the expect line). `needs: pq`. On a failed connect only the code is
printed.

### Tests

The six existing postgres tests, `postgres_libpq_basic`,
`postgres_live_diff`, P1's tests, on C and native, `OBX_CHECK=1`.

### Done

Every existing postgres test passes with unchanged expected lines (but
`postgres_basic`'s libpq line); the differential test reports 0
differences; `httpd` serves `/db`; `dbstream.obx` runs its `pg` client;
`gate.sh full` PASS (output saved); "As built (P2)" lists any difference
the oracle found and who was right (as `sqlserver_live_diff`'s header
does).

## P3: streaming speed

Branch `pgwire-p3`. Depends on P2.

### Scope

pgwire.md sections 7.6 (fast path), 10, 12.3, 12.4, 18.

### Changes

| where | what |
|---|---|
| `postgres.Stream` | rows per Execute by 12.3 (`FIRST_ROWS` 256, `FETCH_TARGET` 262,144 bytes, `FETCH_LIMIT` 4,194,304, at least 64 and the buffer's free space, at most 100,000; `setFetchRows(n)` overrides); fetch-ahead by 12.4 (`setFetchAhead`, on by default) |
| `postgres.Conn` | `pub fn (c: &Conn) roundTrips(): int`, `pub fn (c: mut &Conn) setFetchRows(n: int)`, `pub fn (c: mut &Conn) setFetchAhead(on: bool)` |
| `pgwire.decimalOfText` | the fast path of 7.6 (`fromFraction`); everything else to `sqldialect.parseDecimal` |
| `pgwire` codecs, `postgres.Rows` | integers read from the bytes without building a `str` (`parseInt` on the `&str` view is acceptable if it does not copy: check); no per-value allocation on the stream's path except what `read` keeps |
| `obx/bench/dbstream/dbstream.obx`, `run.py` | a `pglibpq` client (the same query through `postgres_libpq`), so both clients are measured in one run; `--latency` works for `pg` and `pglibpq` (delayproxy in front of a TCP DSN, `OBX_PG_TLS_DSN`; skipped without it) |

### Tests

`postgres_live_stream` expected lines unchanged (the fill counts are the
caller's buffer, not the Execute sizes); a new `postgres_live_fetch.obx`
(as `oracle_live_fetch`): rows per round trip for narrow and wide rows,
buffer 7 with the byte rule, `setFetchRows(100)` and fetch-ahead off giving
the same rows, an early close and a drop with an Execute outstanding (the
connection used after), an error in the batch fetched ahead (rows, then
the error), `roundTrips()`. The differential test's streamed half again.
`pgwire_codec`: the fast path against `parseDecimal` on 100,000 generated
strings (scales 0 to 40, 1 to 45 digits, signs, leading zeros) giving equal
values and scales or the same error code.

### Measurements

`python3 obx/bench/dbstream/run.py` (pinned, rules above) and `perf stat
-e cycles:u,instructions:u` per configuration; the table of pgwire.md
section 18 filled in for `pg` (native and C backends) next to `pglibpq`,
with a profile (`perf record`) of the native stream. If the streamed
cycles a row stay above 1,550 after the work above, write the binary
results proposal (section 18) into "As built (P3)"; do not build it.

### Done

Tests pass on C and native under `OBX_CHECK=1`; the measurements recorded
in "As built (P3)" and design/db-streaming.md section 7 (a new
subsection); `gate.sh full` PASS (output saved).

## P4: pool, httpd, pipelining

Branch `pgwire-p4`. Depends on P3.

### Scope

pgwire.md sections 14 and 15.

### Changes

| where | what |
|---|---|
| `pgwire.Session` | `pub fn (s: &Session) alive(): bool`: `readNow` on the socket; true only for EAGAIN (nothing waiting); EOF, an error or any bytes: false (the bytes are not consumed by anyone after: the session is then closed) |
| `postgres.Pool.acquire` | an idle connection that is not `alive()` is closed and its place reused (open a new one), as one that went bad while idle is today |
| `postgres` | `pub type Stmt = record { pub sql: str; pub args: []Value }`; `pub fn (c: &Conn) batch(stmts: &[Stmt], w: Where): Result[[]Rows, Error]` and `Lease.batch`: section 14 (one Sync, all or nothing, writes in pieces with `readNow` between when over 64 KB) |
| `pgwire` | `pub fn (s: mut &Session) queryBatch(stmts: &[BatchItem], w: Where): Result[[]Reply, Error]` with `pub type BatchItem = record { pub sql: str; pub params: []Param }` |

### Tests

`task_postgres_pool` unchanged; a new `task_postgres_pool_alive.obx`: a
pooled idle connection killed with `pg_terminate_backend` from another
connection, then `acquire` gives a working connection (the dead one
closed); `postgres_live_batch.obx`: 1,000 statements in one batch (over 64
KB of messages, no deadlock), an error in statement 500 (nothing committed,
the Err is that error, the connection idle after), a batch inside
`transaction`, an empty batch.

### Measurements

`python3 obx/cmd/http_gate.py --servers native,c --db 4,16` and
`--workers 1,2,4,8 --db 4`, pinned as the script does, with the old client
(build `httpd` against `postgres_libpq` by a one-line import change in a
scratch copy, not committed) and the new: the table of PERFORMANCE.md's
"With a Postgres pool" section for both. Targets in pgwire.md section 18.
Write whether shared-connection multiplexing (section 14) looks worth a
design, from the profile of `/db` (system calls and the server's thread per
request).

### Done

Tests pass on C and native under `OBX_CHECK=1`; measurements in "As built
(P4)"; PERFORMANCE.md's Postgres sections updated only after asking the
lead (another branch may be editing it); `gate.sh full` PASS (output
saved).

## P5: later items

Each is a small brief of its own when the lead picks it; the behaviour is
in pgwire.md where noted.

| item | outline |
|---|---|
| COPY | `conn.copyIn(sql, source: fn(mut &[byte]): Result[int, Error], w)`, `copyOut(sql, sink, w)`: CopyData in 64 KB messages, CopyDone, CopyFail on the source's error; replaces the E_INPUT of 7.7 |
| LISTEN/NOTIFY | `conn.notifications(): []Notification` (taken from the queue), `conn.waitNotification(w)` parking on the socket |
| notices | `conn.notices(): []pgwire.ServerError` (taken) |
| service file | 6.5 |
| TLS cancel requests | the CancelRequest after SSLRequest and a handshake when the main connection used TLS (libpq 17) |
| CRLs | `tls.Options.crlFile`/`crlDir` (X509_STORE flags), then `sslcrl`, `sslcrldir`, the default `root.crl` |
| `target_session_attrs`, `load_balance_hosts=random` | libpq's rules (`SHOW transaction_read_only`, `pg_is_in_recovery()`), random order |
| `requirepeer` | `SO_PEERCRED` on the Unix socket (a runtime function) |
| direct TLS | `sslnegotiation=direct`: TLS at once with ALPN `postgresql`, only with `sslmode` require or stricter |
| NFKC SASLprep | NFKC tables generated like `gen_regex_unicode.py` |
| statement cache study | measure Parse's share on `http_gate --db`; write a proposal (named statements, invalidation on 0A000, `DISCARD`), with binary results per column; build only if the user agrees |
| shared-connection multiplexing | only if P4's profile says so; a design note first |
