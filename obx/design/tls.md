# TLS for obx

Status: built (2026-11-05) as `lib/tls.obx`, with https in `lib/http.obx`
(design/http-client.md, H2). No language or compiler change was needed.
It is the prerequisite for a TDS (SQL Server) client written in obx, whose
encryption is TLS carried inside TDS's own packets during login.

## 1. What it is

TLS 1.2 and 1.3 through the system's OpenSSL 3 (3.0.13 here), called by
externs on `libssl.so.3` and `libcrypto.so.3`. No headers and no C shim:
every function is declared by hand, and OpenSSL's macros (SNI, the
version limits, `BIO_ctrl_pending`) are written as the `SSL_ctrl`,
`SSL_CTX_ctrl` and `BIO_ctrl` calls they stand for, each with the macro's
name in a comment. Both backends link the two libraries by their path,
so the -dev package is not needed (as for libpq and ODBC).

Written in obx on top of OpenSSL rather than in obx itself: cryptography
needs audited code, and the system's copy gets security fixes with the OS.

## 2. One engine, two modes

The core is an `Engine`: an OpenSSL `SSL` over two **memory BIOs**. It
never touches a socket. The caller

- feeds it the ciphertext that arrived (`feed`),
- calls `handshake()`, `read(buf)` or `write(data)`,
- and sends whatever the engine produced (`output(buf)`, `takeOutput()`):
  handshake flights, alerts, close_notify and records.

`read` returns `WANT_INPUT` when the engine needs more bytes. Memory BIOs
grow, so the engine never asks to wait for writing.

**Memory mode** is the Engine itself. A protocol that wraps TLS records in
its own framing drives it by hand:

```obx
var e = try ctx.engine("db.example.com")
while !(try e.handshake()) {
    sendInMyPackets(e.takeOutput())        // e.g. TDS PRELOGIN packets
    e.feed(receiveFromMyPackets())
}
sendInMyPackets(e.takeOutput())            // the last flight (TLS 1.3: the client's Finished)
```

**Socket mode** is a `Conn`: an Engine plus a `net.Conn`. It runs the same
loop, moving ciphertext between the two:

```obx
let ctx = try tls.client(tls.defaults())
var c = try ctx.connect(try net.dial("example.com:443"), "example.com")
try c.writeStr("GET / HTTP/1.1\r\nHost: example.com\r\nConnection: close\r\n\r\n")
var buf = array(16384, byte(0))
while true {
    let n = try c.read(mut &buf)               // 0: the peer closed
    if n == 0 { break }
    ...
}
try c.close()                                  // close_notify
```

Because socket mode is built on the engine, the two can be mixed:
- `tls.over(engine, sock)` puts an engine whose handshake was done (or
  started) in memory mode over a socket. TDS needs this: the handshake
  goes through PRELOGIN packets, then the records go straight on the
  socket.
- `conn.detach()` ends TLS without close_notify and gives back the socket
  and the bytes that arrived after the last record (`Detached {sock,
  rest}`). TDS needs this too, when only the login is encrypted.

## 3. Task integration

`Conn`'s calls park their task, never the thread:
- When the engine wants input, `Conn` flushes what the engine made, then
  `read`s the socket. That read is `net`'s, which parks the task on epoll.
- Writes go a record (16 KiB) at a time, through `net`'s parking write.

So a slow handshake costs other tasks nothing. `tls_tasks.obx` shows this:
a ticker task keeps ticking during a server's 300 ms delay, and 200 tasks
each run a handshake and an 8 KiB echo at once. Deadlines and cancellation
come from `net` (`task.within`, a dropped future). They surface as
`Kind.Timeout` and `Kind.Cancelled`, with the socket's `IoError` as
`cause`.

Every OpenSSL call runs on the program's thread. None of the externs is
`blocking`: they are CPU work on memory buffers. This matters for two
reasons:
- **The error queue.** OpenSSL keeps errors per thread. tls clears the
  queue before each call and reads it right after, on the same thread, so
  a message never belongs to another call.
- **Thread safety.** One `SSL` is never used by two threads. An `SSL_CTX`
  is reference-counted by OpenSSL. `share()` takes a reference, for
  handing a context to tasks or a pool.

Worker processes (`task.workers`) each make their own contexts, like
their own http clients.

A handshake's CPU (a few hundred µs, section 7) runs on the program's
thread, as the http client design accepted.

**Drops never park.** An open `Conn` that is dropped puts close_notify
into its output and writes it with `net.Conn.writeNow`, a new write that
never parks: if the socket's buffer is full, the alert is skipped. A drop
may run inside `Shared.with` (the http pool closes idle connections there),
where parking stops the program. `close()` parks until close_notify is
written.

## 4. API

```obx
pub type Options = value record {
    pub caFile: str; pub caDir: str          // trust these instead of the system's store
    pub insecureSkipVerify: bool             // client: accept any certificate and name
    pub pins: str                            // SHA-256 fingerprints, hex, comma-separated
    pub certFile: str; pub keyFile: str      // own certificate (server; client optional)
    pub clientCaFile: str                    // server: require and verify client certificates
    pub minVersion: Version; pub maxVersion: Version   // Tls12, Tls13
    pub alpn: str                            // client: "h2,http/1.1"
}
pub fn defaults(): Options
pub fn client(o: Options): Result[Context, Error]
pub fn server(o: Options): Result[Context, Error]

Context: share, isServer, engine(serverName), connect(sock, serverName), accept(sock)
Engine:  feed, pending, output, takeOutput, unread, handshake, read, write, shutdown,
         isOpen, closeNotified, version, cipher, alpn, peer, verifyResult, verifyText,
         resumed, serverName
Conn:    over(e, sock), handshake, read, readNow, write, writeStr, shutdown, close,
         detach, engine, version, cipher, alpn, peer, closeNotified, peerAddr,
         localAddr, setNoDelay
Cert:    {subject, issuer, notBefore, notAfter (Unix s), sha256}
Error:   {kind, msg, code, peer, cause}; toStr() "tls: verify: certificate has expired (localhost)"
Kind:    Config, Handshake, Verify, Protocol, Io, Timeout, Cancelled
openHandles(), libVersion(), WANT_INPUT
```

**Errors.** Each error names its reason:

| Kind | When | Details |
|---|---|---|
| `Config` | options and files | "the CA file x.pem: No such file or directory", "a server name is needed to verify the server's certificate", "minVersion is above maxVersion", a malformed pin |
| `Verify` | the peer's certificate is refused | `code` is X509's verify result and `msg` its text (10 "certificate has expired", 20 "unable to get local issuer certificate", 62 "hostname mismatch", 64 "IP address mismatch"), or a pin did not match (the fingerprint is printed) |
| `Handshake` | anything else that stops the handshake | OpenSSL's reasons, e.g. "unsupported protocol" from a TLS 1.1 server, "tlsv1 alert protocol version", "the peer closed the connection during the handshake" |
| `Protocol` | after the handshake | a damaged record ("decryption failed or bad record mac"), a client certificate refused late (TLS 1.3 sends that alert after the handshake: "tlsv13 alert certificate required"), the socket ending inside a record |
| `Io`, `Timeout`, `Cancelled` | the socket | |

In http, TLS's own kinds become `Kind.Tls` with "verify: ..." or
"handshake: ..." in msg.

## 5. Defaults and security choices (to review)

1. **Verification on.** The chain is checked against the system's trust
   store (`SSL_CTX_set_default_verify_paths`). The name given to `connect`
   is checked with `SSL_set1_host`, which matches SAN DNS names with
   wildcards; an IP literal is matched against SAN IP addresses. SNI is
   sent for names, not addresses (as RFC 6066 says).
   - A client must give a name unless verification is off: `engine("")`
     is a `Config` error, so "forgot the host name" cannot mean "skip the
     name check".
2. **TLS 1.2 minimum, 1.3 maximum.** The `Version` type has no value for
   older versions, so they cannot be chosen. A TLS 1.1-only server fails
   the handshake.
3. **OpenSSL's default cipher suites and groups** at its default security
   level (1 in 3.0: RSA keys of 2048 bits or more, no MD5 or SHA-1
   signatures in the chain). No obx cipher list to keep up to date.
4. **Renegotiation refused** (`SSL_OP_NO_RENEGOTIATION`).
5. **insecureSkipVerify is the only way to turn checks off**, and its name
   says what it does. It skips the chain and the name, but:
   - `verifyResult()` still reports what verification would have said;
   - pins still apply. Pins with skipVerify are how to trust one
     self-signed certificate: SQL Server's default, or
     TrustServerCertificate done safely.
6. **Pins add to verification.** With verification on, the chain and the
   name must verify *and* the certificate must match a pin. A pin is the
   SHA-256 of the whole certificate (DER), as `openssl x509 -fingerprint
   -sha256` prints it. It is not the SPKI hash (HPKP style), so a renewed
   certificate needs a new pin.
7. **close_notify is sent on close**, and on drop when it does not have to
   wait. A peer that closes the socket at a record boundary without
   close_notify reads as end of stream, with `closeNotified()` false. That
   is a possible truncation, and the protocol above must detect it (HTTP's
   framing does). Ending inside a record is a `Protocol` error. This is the
   usual choice (Go, curl) for HTTP over the internet, where many servers
   skip close_notify.
8. **Client certificates** come from files (PEM). A key in the cert file
   works too. The key must match (`SSL_CTX_check_private_key`).
9. **Files are read once**, when the Context is made. A server that
   rotates its certificate makes a new Context.
10. **No session resumption yet** (section 8): every connection does a full
    handshake.
11. **The server side** works with a certificate, optional client
    certificates against `clientCaFile`, and its versions, but without
    ALPN selection, SNI-based certificate choice or session tickets
    tuned. It is enough for tests and simple servers. httpserver does not
    use it yet.

## 6. Resources

- A `Context` owns an `SSL_CTX` (each handle one reference).
- An `Engine` owns an `SSL`, which owns its two BIOs.
- A `Conn` owns its Engine and socket.

Every one of them is freed by its drop. `openHandles()` counts contexts and
engines alive, and the tests check it returns to 0. Every TLS test runs
under `OBX_CHECK=1`, which checks the runtime's allocations. OpenSSL's own
allocations are outside it, but every `SSL_new` and `SSL_CTX_new` is
balanced, as `openHandles` shows.

## 7. Measured

`python3 obx/cmd/tls_bench.py` (PERFORMANCE.md, "TLS") compares
`cmd/tlsbench.obx` with `openssl s_time` and curl against the same local
servers:
- handshakes per second against `openssl s_server`;
- one large GET (MB/s and the client's CPU);
- small requests over kept-alive https connections.

The numbers are in PERFORMANCE.md. In short, obx's handshake and bulk costs
are OpenSSL's: the engine's copies through memory BIOs are a small part of
a record's encryption.

## 8. What is left

- **Session resumption.** TLS 1.3 tickets and 1.2 session IDs on the
  client: keep `SSL_SESSION`s per host in the Context (`SSL_get1_session`
  after the handshake, `SSL_set_session` before the next) and expire them.
  This would make reconnecting http pools and TDS reconnects cheaper.
- **Revocation.** No OCSP, OCSP stapling or CRLs; OpenSSL does not check
  revocation by default. Stapling is the cheap one (`SSL_set_tlsext_status_type`
  and a check of the stapled response).
- **The server side.** SNI-based certificate choice
  (`SSL_CTX_set_tlsext_servername_callback` needs a C callback, which obx
  externs cannot provide yet: a gap in C interop); ALPN selection (also a
  callback); https in `httpserver`.
- **Callbacks in general.** Custom verification (`SSL_CTX_set_verify` with
  a callback), key logging (`SSLKEYLOGFILE`) and the PSK and ticket hooks
  all need C function pointers into obx. The library avoids them today.
- **Keys and certificates from memory** (`PEM_read_bio_X509` over a memory
  BIO) instead of files: needed for secrets that never touch the disk.
- **Certificate chains in `peer()`**: only the leaf is shown now.
- **TLS 1.3 0-RTT**: not planned (replay risk).
- **HTTP/2 over ALPN "h2"**: not planned (design/http-client.md, section 7).

## 9. The TDS plan

SQL Server's TDS encrypts in two stages (MS-TDS 2.2.6.5):

1. **PRELOGIN.** The client and server exchange PRELOGIN packets and agree
   on encryption (`ENCRYPT_OFF`, `ON`, `REQ`, or `NOT_SUP`).
2. **The handshake inside PRELOGIN packets.** The TLS handshake records
   travel as the payload of TDS packets of type 0x12. This is exactly
   memory mode:

   ```obx
   var e = try ctx.engine(serverName)             // pins or caFile for the server's cert
   while !(try e.handshake()) {
       try writeTdsPacket(sock, 0x12, e.takeOutput())
       e.feed(try readTdsPacketPayload(sock))
   }
   let last = e.takeOutput()                      // TLS 1.3: the client's Finished
   if len(last) > 0 { try writeTdsPacket(sock, 0x12, last) }
   ```

3. **After the handshake, TLS records go directly on the socket** (TDS
   packets inside TLS records): `var c = tls.over(e, sock)`, then TDS
   reads and writes its packets through `c`.
   - With `ENCRYPT_OFF`, only the LOGIN7 packet is encrypted. After it,
     `c.detach()` returns the socket (and any bytes already read past the
     last record) for plain TDS.
4. **TDS 8.0 (strict encryption, SQL Server 2022)** starts TLS before
   PRELOGIN, with ALPN "tds/8.0". That is plain socket mode:
   `ctx.connect(sock, host)` with `alpn: "tds/8.0"`.

Options a TDS client will want:
- `TrustServerCertificate=yes` maps to `insecureSkipVerify`, ideally with a
  pin;
- `HostNameInCertificate` is the `serverName` passed to `engine`;
- a CA file for a private CA.

Login packets for the ENCRYPT_OFF case are small. Nothing in tls limits
record sizes, and a TDS packet larger than a TLS record is split by the
engine.

What tls already proves for this:
- `tls_openssl.obx` drives a handshake by hand over a socket
  ("memory mode by hand");
- `tls_memory.obx` runs two engines in memory, flight by flight, with TLS
  1.2 and 1.3 and a client certificate;
- `tls_tasks.obx` tests `detach` with the bytes that follow.

## 10. Tests

- `lib/tests/support/tlstest.obx` makes the test PKI with the `openssl`
  command when a test starts:
  - a CA and a second CA nobody trusts;
  - certificates for localhost and 127.0.0.1, an expired one, one for
    another name, one signed by the second CA, and a client certificate;
  - all on P-256 keys.

  It also starts `openssl s_server` and waits for its port, and has a small
  https server for the http tests. A missing openssl command exits 77
  (skipped); `// needs: ssl` skips the tests when libssl is not installed.
- `tls_openssl.obx`, against s_server:
  - the handshake, version, cipher, subject, issuer, expiry and
    fingerprint;
  - data both ways, 1 MiB both ways, CLOSE;
  - an IP address;
  - a TLS 1.2-only server;
  - a client certificate accepted, and refused when missing;
  - memory mode by hand.
- `tls_verify.obx`:
  - refused: a CA nobody trusts, the system's store, another name, another
    address, an expired certificate, TLS 1.1;
  - `insecureSkipVerify`, pins with and without it, a wrong pin;
  - bad options.
- `tls_memory.obx`: two engines in one process:
  - flights, client certificate, TLS 1.2;
  - 3 MiB, a record fed a byte at a time;
  - close_notify, unread bytes;
  - garbage instead of a handshake, a changed record, a missing client
    certificate.
- `tls_tasks.obx`: a server in the program:
  - 200 concurrent connections;
  - a slow handshake while another task runs;
  - close_notify both ways;
  - a peer dying inside a record, an end without close_notify;
  - a deadline, `detach`.
- `http_tls.obx`: the http client over https (see http-client.md);
  `http_live.obx` adds https://example.com with `OBX_HTTP_LIVE=1`.

All of them run under `OBX_CHECK=1` on both backends (`run_tests.py`,
`run_native.py --lib`).
