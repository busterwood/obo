# An HTTP client for obx

Status: H1 is built (2026-11-03): `lib/url.obx`, `net.resolve` and
`net.dial`, `lib/http.obx`, and `httpd` on the shared parser. Section 8
says what was built, how it differs from this plan, and what it measured.
H2 (https) is built (2026-11-05) on `lib/tls.obx` (design/tls.md); section
9. No language change was needed: everything is library code on `task`,
`net` and externs.

## 0. Goal

`import "http"` gives a program a client that fetches a URL in one line and
still holds up in a server that makes thousands of outgoing requests:
keep-alive connections, deadlines, cancellation, limits on everything the
other side controls, and `https` with certificate checks. It is written in
the blocking style of the rest of the library (a call parks its task, other
tasks run), like `postgres` since C1c.

Out of scope: HTTP/2 and HTTP/3 (section 7), cookies, a browser-like cache.

## 1. What exists and what is missing

| Piece | State |
|---|---|
| TCP over IPv4/IPv6, parking reads and writes | `lib/net.obx` (C1b) |
| deadlines, cancellation, `Where` | `lib/task.obx` (C1a/b) |
| a pool of connections handed out as leases | `postgres.Pool` (C1c), the model to copy |
| parsing an HTTP/1.1 request head | `cmd/httpd.obx` (server side, minimal) |
| a load client | `cmd/httpload.obx` (fixed request, no parsing beyond lengths) |
| **host names** | missing: `net.connect` takes IP literals and `localhost` only |
| **URLs** | missing |
| **TLS** | missing at H1 (built in H2 as `lib/tls.obx`). `libssl.so.3` is installed (OpenSSL 3.0.13); an `extern "ssl"` links it by path without the -dev package |
| **gzip** | missing; `libz` is installed |
| **JSON** | missing (most HTTP APIs need it; section 6) |

## 2. Design

### API

```obx
import "http"

// one line
let r = try http.get("https://api.example.com/v1/rates?ccy=EUR")
if r.status != 200 { return Err(...) }        // a status is not an error
let body = try r.text()                        // the whole body, within a limit

// a client: its connection pool and defaults, shared by copies (as postgres.Pool)
let c = http.newClient(http.Options{maxPerHost: 8, timeoutMs: 10000, maxBody: 16 << 20})
var rq = http.request("POST", "https://api.example.com/v1/orders")
rq.header("Content-Type", "application/json")
rq.body(strBytes(json))
let r = try c.send(&rq)

// streaming a large body
var buf = array(65536, byte(0))
while true {
    let n = try r.read(mut &buf)
    if n == 0 { break }
    ...
}
```

- `Response` holds its connection, as `postgres.Lease` does. When the
  response is dropped and its body was read to the end, the connection goes
  back to the pool; otherwise it is closed. So no connection leaks, and none
  is reused in an unknown state.
- Errors are `Result[_, http.Error]` with a kind: `Dns`, `Connect`, `Tls`,
  `Timeout`, `Cancelled`, `Protocol` (a malformed or ambiguous reply),
  `TooLarge`, `Redirects`, plus the message and the URL. A 404 or a 500 is
  an `Ok(Response)`.
- Every call that can park takes a last `w: Where`, so traps name the
  caller's line.
- Deadlines come from `task.within`, as everywhere; `Options.timeoutMs` is
  a default `within` around each request. Cancelling the task closes the
  connection.

### Host names

`getaddrinfo` as a `blocking` extern (SPEC 12), so a lookup runs on the
helper pool while the task parks. That gives `/etc/hosts`, nsswitch and the
system's resolver for free. It cannot be cancelled while it runs; the task
sees a cancellation when it returns. `net.dial("example.com:443")` resolves,
then tries the addresses in order (IPv6 and IPv4 interleaved), each attempt
bounded by the remaining deadline. A resolver written in obx (UDP to the
servers in `/etc/resolv.conf`) would be cancellable and need no thread. It is
worth it only if lookups show up in measurements.

### URLs: `lib/url.obx`

RFC 3986 for `http` and `https`: scheme, host (IPv6 in brackets), port,
path, query, fragment; percent-encoding and decoding; a query builder;
resolving a relative reference (for redirects). Hosts are ASCII; a
non-ASCII host (IDNA) is an error for now. User information in a URL
(`user:pass@`) is refused, not sent.

### HTTP/1.1: `lib/http.obx`

- The request is written in one buffer with the head and a small body; a
  large body follows it. `Host`, `Content-Length` and `User-Agent: obx` are
  added unless set.
- The reply: status line, headers (names compared without case, repeated
  headers kept in order), then the body framed by, in RFC 9112's order:
  - no body for HEAD, 1xx, 204 and 304;
  - `Transfer-Encoding: chunked`, trailers read and dropped;
  - `Content-Length`;
  - otherwise until the server closes, and that connection is not reused.
  - A `100 Continue` before the real reply is skipped.
- Strict where ambiguity is dangerous (request smuggling, RFC 9112 section
  6.3): both `Content-Length` and `Transfer-Encoding`, two different
  `Content-Length`s, a header line folded onto the next, or a bad chunk size
  are `Protocol` errors.
- Limits against a hostile server: a head of at most 64 KiB and 100 headers,
  a body of at most `maxBody` for `text()` and `bytes()` (`read` streams
  without a limit), and at most 10 redirects.
- The header parser is shared with the server: `httpd`'s request parsing
  moves into `lib/http.obx`, so client and server read headers the same way.

### Connection pool

- Idle connections are kept per (scheme, host, port) in a `task.Shared`, as
  in `postgres.Pool`, with at most `maxPerHost` open; a task over the limit
  waits first come, first served.
- An idle connection is dropped after 90 s, or when the server's
  `Keep-Alive: timeout` says so.
- A request on a reused connection that fails before any byte of the reply
  is sent once more on a new connection, but only for GET, HEAD, PUT and
  DELETE. This covers the race with a server closing an idle connection.
  POST is never resent.

### Redirects

They are followed for 301, 302, 303, 307 and 308, up to the limit.
- 303, and 301 or 302 after a POST, continue as a GET without a body; 307
  and 308 keep the method and the body.
- `Authorization` and `Cookie` are dropped when the host changes.
- From `https` to `http` is refused unless the options allow it.
- `Options.redirects: false` returns the 3xx response as it is.

### TLS: OpenSSL through externs

OpenSSL 3, the system's library, with no C shim: externs on `libssl.so.3`
and `libcrypto.so.3`, as `mssql` uses ODBC.

- **Non-blocking on our socket.** `SSL_set_fd`, then `SSL_connect`,
  `SSL_read` and `SSL_write` return `SSL_ERROR_WANT_READ` or
  `SSL_ERROR_WANT_WRITE`, and the task parks on the socket in that direction
  and calls again. This is the same loop as libpq's async API, so no helper
  thread is involved.
- **Checks on by default.**
  - The system's CA store: `SSL_CTX_set_default_verify_paths`.
  - The host name: `SSL_set1_host`.
  - SNI: `SSL_set_tlsext_host_name`, which is a macro and so is called as
    `SSL_ctrl(ssl, 55, 0, name)`.
  - TLS 1.2 or newer, and ALPN `http/1.1`.
  - Turning verification off takes an option whose name says so
    (`insecureSkipVerify`), and is never the default.
- **The handshake's CPU** (about 0.1 to 1 ms) runs on the program's thread.
  That is fine for a client, and sessions are resumed through tickets on a
  reused client.
- **Ownership.** The `SSL*` and `SSL_CTX*` are owned obx values freed by
  their drop, as ODBC handles are in `mssql`.
- **Macros.** OpenSSL's macros are a recurring trap for externs: each one
  used is written out as its `SSL_ctrl` call, with the constant and a
  comment.

The same binding gives a TLS server later (`httpd` over https), using
`SSL_accept` and a certificate file.

## 3. Phases

**H1. HTTP/1.1 over TCP.** `url.obx`, `net.dial` with `getaddrinfo`,
`http.obx` (request, reply, body framing, limits, pool, retries, redirects,
deadlines), and `httpd`'s parser moved into the library.

Tests:
- run against our own `httpd`, with new routes `/echo`, `/chunked`,
  `/redirect`, `/slow` and `/close`;
- run against scripted servers inside the tests (tasks on a listener) that
  send malformed, truncated, ambiguous or huge replies;
- a generator in the style of `vrgen.py` that frames bodies every legal way
  and compares the result.

Gate:
- the client in 1,000 tasks against `httpd` on one core reaches at least
  the throughput of `httpload`;
- every malformed case is an error, not a crash or a hang;
- no leaks under `OBX_CHECK=1`, ASan and UBSan, on both backends.

**H2. https.** The OpenSSL binding, certificate and host-name checks, SNI
and ALPN.

Tests:
- a local TLS server with a certificate made at test time by the `openssl`
  command;
- the failing cases (expired, wrong host, self-signed, TLS 1.0) are errors;
- opt-in live tests against public sites, which exit 77 without network.

Gate:
- 1,000 https requests over kept-alive connections;
- certificate failures are `Tls` errors that name the reason;
- no leaks.

**H3. Later, as programs need them.**
- `Accept-Encoding: gzip` through zlib (opt-in);
- streaming request bodies;
- `HTTP_PROXY` and `HTTPS_PROXY` (CONNECT);
- an https server for `httpd`;
- a resolver written in obx.

Sizes, estimated from the existing adapters: `url.obx` about 300 lines,
`http.obx` about 1,200, the TLS binding about 400.

## 4. Decisions to make

1. **TLS library.** Recommended: OpenSSL 3 through externs, the system's
   copy. Its security updates arrive with the OS, it needs no C shim, and
   the non-blocking API fits the event loop.
   Alternatives:
   - rustls-ffi: memory-safe, but not installed and not packaged by Ubuntu
     24.04;
   - TLS written in obx: no, because cryptography needs audited code.
2. **Module layout.** Recommended: `url` and `http` for the client and the
   shared parsing, the TLS binding as `tls` (also usable by `net` users),
   and the server's parts moved into `http` when `httpd` adopts them.
3. **Statuses are not errors.** Recommended: yes, as in Go and Rust's
   reqwest; a `r.ok()` helper is cheap.
4. **JSON next** (section 6), for the APIs the client will talk to.

## 5. Risks

- **Blocking lookups.** `getaddrinfo` blocks and cannot be cancelled. A
  name server that does not answer holds a helper thread for its timeout
  (5 s by default, with retries). With 4 helpers, four stuck lookups stall
  every other blocking call in the program. Mitigation: a lookup cache with
  the answer's lifetime, and the obx resolver (H3) if it happens in
  practice.
- **OpenSSL errors are a queue per thread.** Read them with
  `ERR_get_error` right after the failing call, on the same thread. The TLS
  calls run on the program's thread, so this holds.
- **Fork.** A `Client` made before `task.workers` forks (C2) is copied into
  every worker with the parent's idle connections, and two processes would
  then share one socket. Rule: make clients inside the worker. The client
  can refuse to run in another process than the one that made it, by
  storing the pid.

## 6. JSON (a separate plan, needed soon after H1)

`lib/json.obx`:
- a parser to a `Json` value (an enum: null, bool, number, string, array,
  object with its keys in order) and a writer;
- numbers kept as their text until they are read, so `j.decimal()` is exact
  (money, rates) and `j.float()` and `j.int()` are checked conversions. No
  amount becomes a binary float on the way in.

Decoding into records without writing the code by hand would need derived
code from the compiler, which is a language question for later.

## 7. HTTP/2 and HTTP/3

Not planned. HTTP/1.1 with a pool of kept-alive connections is enough for a
client calling APIs.
- HTTP/2 needs HPACK, streams and flow control: about 3,000 lines.
- HTTP/3 needs QUIC.

Revisit when a server we must call speaks only HTTP/2 (gRPC, for example).

## 8. H1 as built

**Files.** `lib/url.obx` (550 lines), `lib/http.obx` (1,550),
`net.resolve`/`net.dial`/`Conn.readNow` in `lib/net.obx`, `io.LOOKUP`
in `lib/io.obx`. The API is in `lib/README.md` (url, http, net).

**Name lookup** lives in `net`: `resolve(host, port)` calls `getaddrinfo`
as a `blocking` extern (helper pool, task parked), and `dial(addr)` tries
the addresses in turn, IPv6 and IPv4 interleaved, each attempt with an
equal share of the time left and at least 2 s. A failed lookup is an
`IoError` with errno `io.LOOKUP + n`, which http turns into `Kind.Dns`.
Linking: the native backend links a program dynamically only when it
reaches `dial` or `resolve` (glibc's `getaddrinfo` needs nsswitch and
cannot be used from a static binary); everything else stays static. `httpd`
was already dynamic (libpq); `httpload` is dynamic since `--client`.

**The TLS seam.** A connection's transport is
`Stream = value closed { Tcp(net.Conn); Gone }` with `read`, `write`,
`writeStr` and `quiet`; everything above it (framing, pool, retries) only
sees a `Stream`. H2 added a `Tls` case (section 9).

**Pool and retries.** A `Client` is a copy handle on a
`task.Shared[PoolState]` (as `postgres.Pool`): per `scheme://authority`
key, a count of open connections, a LIFO list of idle ones and a FIFO list
of waiters (each a promise). `acquire` closes idle connections whose time
is up (`idleTimeoutMs`, or Keep-Alive's timeout less a second), hands out
the most recent idle one, opens a new place under `maxPerHost`, or queues
the caller. A connection idle for more than 100 ms is checked with a read
that does not park before it is reused (EAGAIN: still open). The
`Response` owns the connection; its drop returns it to the pool only when
the body was read to the end and the server keeps the connection, else it
is closed and the place given to the first waiter. A request is sent once
more, on a new connection, only when it failed on a reused connection
before any byte of the reply (EPIPE, ECONNRESET, or an end of stream),
and only for GET, HEAD, PUT, DELETE, OPTIONS and TRACE. The client stores
its process's pid and refuses (`Kind.Process`) in another process.

**Deviations from the plan.**
- The test routes (`/echo`, `/chunked`, `/redirect`, `/slow`, `/close`,
  and `/hello`, `/status`, `/big`, `/slowhead`, `/drop`) are in a test
  support module, `lib/tests/support/httptest.obx`, with a scripted server
  for byte-exact replies, not in `cmd/httpd.obx`: the server stays the
  benchmark it is, and the tests need no external process.
- `Options` has `redirects`, `maxRedirects`, `allowHttpsToHttp`,
  `idleTimeoutMs` and `userAgent` as well; defaults are in `defaults()`.
  `Request.body` is a `str` field, not a method.
- `Kind` also has `Url`, `Invalid` (a request refused before sending),
  `Io` and `Process`. An error's `url` is the request's URL as given (or
  the redirect's target).
- Retries also cover OPTIONS and TRACE (idempotent too).
- `httpd` became stricter with the shared parser (400 and close for
  CL+TE, differing lengths, folding, a bare LF, a target not starting with
  `/`).
- The gate is measured with `httpload --client` (the same program, its
  load sent through one `http.Client`) rather than a separate program.

**Tests.** `lib/tests/run/url_parse`, `url_resolve`, `net_dial_names`,
`http_basic`, `http_framing`, `http_malformed`, `http_redirect`,
`http_pool`, `http_process`, `http_heads`, and `http_live` (only with
`OBX_HTTP_LIVE=1`; else exit 77), on both backends under `OBX_CHECK=1`,
and under ASan and UBSan in `gate.sh full`. `tests/httpgen.py` generates
programs whose scripted servers frame random bodies every legal way
(lengths, chunked with extensions and trailers, until close, no body,
interim replies, byte-at-a-time writes) and checks every body and the
number of connections on both backends; `gate.sh full` runs 40 programs
(240 cases).

**Gate.** Measured with `httpload` against the native `httpd` on CPU 0, the
loader on CPU 1, 5 s per row, the median of three alternating rounds:
"raw" is httpload's own loop (a fixed request, only Content-Length
parsed), "client" is `httpload --client`, the same number of tasks through
one `http.Client` (`get` and `text()` per request).

| conns | native raw | native client | via C raw | via C client |
|---:|---:|---:|---:|---:|
| 1: requests/s | 4,320 | 4,530 | 4,760 | 4,410 |
| 1: p50 / p99 | 227 / 325 µs | 217 / 320 µs | 205 / 332 µs | 222 / 362 µs |
| 100: requests/s | 69,700 | 59,900 | 69,700 | 65,400 |
| 100: p50 / p99 | 1.43 / 1.90 ms | 1.70 / 2.20 ms | 1.43 / 1.85 ms | 1.53 / 2.10 ms |
| 1,000: requests/s | 62,000 | 53,600 | 62,300 | 51,300 |
| 1,000: p50 / p99 | 16.2 / 21.2 ms | 18.4 / 25.2 ms | 16.0 / 20.2 ms | 19.6 / 23.0 ms |

The throughput part of the gate is **not met** at 100 and 1,000
connections: the client reaches 86% (native) and 82% (via C) of the raw
loop at 1,000, and 86% and 94% at 100; at one connection, where the
loopback round trip is the limit, they are level. With the loader's core
saturated, the difference is the client's own work: about 7 µs of user
time per request against the raw loop's 3.3 µs (system time is the same,
about 12 µs). That work is what a client has to do and the raw loop skips:
building the request with Host and User-Agent, parsing every reply header
into `Headers`, working out the framing, and taking and returning the
connection through the pool's `Shared` state. Profiles after the
optimizations (one array for the request, single-pass framing, the reply
built once, a cache of the last parsed URL, no allocation in Content-Length
and URL checks; 46k to 54k requests/s at 1,000) show no single cost left
above 5%. The other two parts of the gate hold: every malformed reply in
`http_malformed` (about 50 cases) and the generated framings end in a
value or an error, never a crash or a hang, and the tests and
`httpload --client` under `OBX_CHECK=1` (10 and 1,000 connections), and
the tests under ASan and UBSan, report no leaks.

**Left for H2.** The OpenSSL binding (the `Tls` stream case),
certificate and host-name checks, SNI and ALPN, and the https tests. The
https-to-http redirect rule and the `Tls` kind are already in place.

## 9. H2 as built

**The binding is its own module**, `lib/tls.obx` (design/tls.md), not part
of http. It does not use `SSL_set_fd` as section 2 planned: an engine over
memory BIOs, driven over a `net.Conn` by `tls.Conn`, so the same engine
also serves protocols that wrap TLS in their own packets (TDS). The task
parks in `net`'s read and write; no OpenSSL call waits.

**The stream.** `Stream = value closed { Tcp(net.Conn); Tls(tls.Conn);
Gone }`. A match on a place binds its payload read-only, and `tls.Conn`'s
calls change it, so each `Tls` call takes the connection out
(`replace(s, Stream.Gone)`), calls it and puts it back: the connection
belongs to the request's task alone meanwhile. Stream calls return `SErr
{errno, io, tls}`: a socket's error as before (so the retry rule on
`ECONNRESET` and `EPIPE` still sees errnos), or TLS's own reason, which
becomes `Kind.Tls` ("verify: certificate has expired"). `quiet()` on a
TLS stream is `tls.Conn.readNow`: nothing there, or only a TLS 1.3 session
ticket, is quiet; close_notify or data is not.

**Options.** `Options.tls: tls.Options`, `tls.defaults()` with ALPN
`http/1.1`: verification against the system's store, the URL's host
checked (names and IP literals), SNI. `o.tls.caFile`, `caDir`, `pins`,
`certFile`/`keyFile` (client certificates) and `insecureSkipVerify` are
there. A client makes one `tls.Context` at its first https request,
before connecting, so bad options are a `Tls` error with no connection
made; the context is shared by all its connections (`share()`).

**Errors.** A refused certificate or failed handshake is `Kind.Tls`; a
socket error during the handshake is `Connect` (it happens while the
connection is being made), and a deadline `Timeout`.

**Not done.** Session resumption (each connection does a full handshake;
kept-alive connections make this matter less), OCSP, HTTP proxies
(CONNECT), and an https server in `httpserver`.

**Tests.** `http_tls.obx`, against an https server in the test
(`tests/support/tlstest.obx`, certificates made by the `openssl` command
at test time):
- requests on one kept-alive connection, a body both ways, 1 MiB,
  `Connection: close`;
- refused: the system's store (the test CA is not in it), an expired
  certificate, another name;
- the address form of the URL;
- `insecureSkipVerify`, a CA file that does not exist (no connection made);
- redirects from http to https (followed) and back (refused, then allowed);
- pools per scheme;
- 50 requests in tasks over at most 8 connections.

`http_basic.obx` now expects a handshake error from an https URL to a
plain server. `http_live.obx` (with `OBX_HTTP_LIVE=1`) fetches
https://example.com verified against the system's store. Measurements are
in PERFORMANCE.md ("TLS").

