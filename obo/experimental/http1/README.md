# Experimental HTTP/1.1 protocol core (H1a0 + H1b + H1c)

Status: **experimental H1a0/H1b/H1c slice. Unstable. Not a merged or versioned
API, and not a stability, protocol-version or release claim.** This directory
implements the pure grammar/parser/serializer/date core (H1a0), the plain
transport, connection state machine and server loop (H1b), and HTTPS/TLS
integration (H1c) from the approved design in
`obo/design/http1-server-design.md`. It makes no compiler, runtime, ABI,
symbol-format, seed or package-version change.

H1a0 runs entirely in memory and has no systems dependency. H1b adds a
deterministic in-memory transport and a plain nonblocking `sysstream` transport,
explicit deadlines, keep-alive/pipelining, graceful drain, and a
`sysnet`/`sysreactor` loopback server loop. H1c replaces the H1b opaque seam
with the concrete `http1io` closed dispatch, adds an owned
`systls::TlsSession` backend, drives the TLS handshake through the existing
connection state machine, enforces ALPN `http/1.1`/h2/nil policy before any HTTP
parse, and gives the server an optional owned `TlsContext`. A connection with
parser-buffered body bytes or a fairness-cap exit requests an immediate server
turn, so an Echo response cannot stall waiting for a socket-read event that will
never arrive.

## Layout

```text
obo/experimental/http1/
  http1core.obx      Limits, Header, RequestInfo, status/error tables, predicates
  http1parse.obx     incremental request parser (feed/next/readBody/eof)
  http1write.obx     response serializer and protocol error responses
  http1date.obx      IMF-fixdate formatter, RFC 850/asctime parser
  http1io.obx        closed concrete transport dispatch: plain, TLS, memory
  http1conn.obx      Connection phases, deadlines, framing, body/response API
  http1server.obx    listener accept, reactor watches, TLS context, drain, metrics
  tests/
    test_http1.py    one-command H1a0 suite (no sockets, TLS or clock)
    test_http1b.py   H1b memory + loopback suite
    test_http1c.py   H1c HTTPS/TLS suite (self-signed certs, ALPN, curl -k)
    apps/            small drivers used only by the tests
```

Modules are ordinary source modules and are imported by basename:

```obo
import "http1core.obx" as core
import "http1parse.obx" as http1parse
import "http1write.obx" as http1write
import "http1date.obx" as http1date
import "http1io.obx" as http1io
import "http1conn.obx" as http1conn
import "http1server.obx" as http1server
```

Records used by callers (`Limits`, `Header`, `RequestInfo`, `DateValue`) expose
their fields with `pub`. Implementation records (`Parser`, `Response`) have
private fields under the 0.13 field-visibility model and are constructed only by
`makeParser`/`makeResponse`. A public function is the authority: every entry
point revalidates caller-visible fields.

## What H1a0 covers

- Strict request line: token method, raw target, exactly one SP between fields,
  CRLF only, `HTTP/1.1`; limited `HTTP/1.0` compatibility.
- Origin-form, absolute-form and `OPTIONS *` targets. Raw path/query bytes; no
  percent-decoding, dot-segment removal or normalization. `CONNECT`/`TRACE` are
  rejected with `501`.
- Ordered, case-insensitive header lookup with original spelling and duplicates
  preserved; token names, OWS trimming, no obs-fold, no whitespace before the
  colon, CTL/NUL/DEL rejection.
- Limits for start line, target, header block/line/count, body, chunks, chunk
  metadata/count, trailers and buffered pipeline bytes.
- Host rules, absolute-form authority comparison with default-port
  canonicalization, bracketed IPv6-literal syntax (nonempty hex/colon/dot form
  with at least one colon; this is not a full IPv6 semantic validator), and
  missing/duplicate Host rejection.
- CL/TE smuggling defenses: CL+TE rejected, duplicate/conflicting CL rejected,
  duplicate TE rejected, chunked only as the final coding, empty list elements
  rejected, unsupported codings `501`, no close-delimited request bodies.
- Fixed-length and chunked request bodies, chunk extensions, strict data CRLF,
  bounded trailers, forbidden trailer fields, decoded-body limit.
- `Expect: 100-continue` state (due/sent/skipped/none) without writing a
  response; the connection layer or a test driver writes `100 Continue`.
- Keep-alive, `Connection: close`, HTTP/1.0 always-close, sequential bounded
  pipelining and per-connection `maxRequests`.
- Response serialization with writer-owned framing headers, bounded output,
  exact header order, chunked-only-when-unknown, no body for HEAD/1xx/204/205/
  304, 205 length 0, 304 metadata, header-injection rejection, response trailers
  and HTTP/1.0 close-delimited output.
- IMF-fixdate formatting from explicit `(seconds, nanos)` and parsing of all
  three RFC 9110 date forms with the RFC 850 50-year window at timestamp
  granularity and a required full weekday name. No clock dependency.

## H1b surface

- `http1io::plain(stream)` wraps an owned `sysstream::Stream`;
  `http1io::memory(bytes)` is the deterministic test transport. Both expose
  `handshakeStep`, `readSome`, `writeSome`, `flushPending`, `shutdownStep`,
  `pending`, `want`, `alpn`, `isSecure`, `maxTransfer`, `watch` and `close`.
  The plain adapter maps `WouldBlock` to `ReadWantRead`/`WriteWantWrite`,
  reports `AlpnNone`/`isSecure=false`/`pending=0` and never exposes a
  descriptor or native token.
- `http1conn::make(transport, limits)` deep-copies `Limits` and binds the
  transport; `makeAt(transport, limits, now)` also arms the accept/handshake
  deadlines at creation and is what the server uses at accept. `next`, `readBody`, `discardBody`, `beginResponse`,
  `beginResponseWithReason`, `setHeader`, `setLength`, `setTrailerNames`,
  `addTrailer`, `writeBody`, `writeText`, `endResponse`, `flush`,
  `requestClose`, `shutdownStep`, `close`, `acceptAlpn`, `sendContinue`,
  `wantsRead` and `wantsWrite` form the explicit step API. Every entry point
  takes a `Now` where a deadline can be observed; no hidden clock read, retry or
  callback exists.
- `http1server::make(listener, reactor, limits)` owns the listener, reactor and
  a fixed slot table. `runOnce` accepts in batches of 32, drives ready slots and
  checks deadlines every turn. `beginShutdown`, `drain` and `shutdown` implement
  graceful stop. `metrics` reports accepted/rejected/closed/requests, protocol
  errors, timeouts, accept pressure, handler/watch errors and peak active.
- Admission uses both `maxConnections` and `maxTotalConnectionBytes`; the
  per-connection reservation is `2*maxHeaderBytes + maxHeaderLine +
  maxResponseBuffer + 2*maxTarget`. `ResourceLimit` from `accept` (EMFILE/ENFILE)
  pauses acceptance for 100 ms instead of spinning.
- The server handler is a **pure** function value
  `fn(core::RequestInfo, &Now): Action`. `Action` is `Hello`, `Echo`, `Chunked`,
  `Head`, `NoContent`, `NotModified`, `Big`, `Slow`, `Stop` or `Close`. The
  server performs the bounded response/body steps. This is the 0.14
  language-limited adaptation: function values cannot be impure, and interface
  methods cannot be impure either, so application code cannot itself drive a
  mutable connection through a function value. There is still no C callback and
  no hidden thread.
- `http1io` uses an opaque record with a closed concrete backend rather than an
  interface for the same reason: 0.14 has no impure interface-member tier, so an
  interface method could not call `sysstream`/`systls`. The operation contract
  is unchanged.
- H1c seam note: `http1io` is the dedicated module that imports both
  `sysstream` and `systls` and dispatches over `Plain`/`Tls`/`Memory`; the
  connection and server never import `systls` for byte I/O. The server imports
  `systls` only for the owned `TlsContext` passed to `makeTls`. The alternative
  remains a future compiler impure interface/function-value tier, which would
  also restore the design's mutable-`&Connection` handler.

## H1c surface

- `http1io::fromTls(session)` takes ownership of an established or handshaking
  `systls::TlsSession`. `handshakeStep`, `readSome`, `writeSome`,
  `flushPending`, `shutdownStep`, `pending`, `want`, `alpn`, `isSecure`,
  `maxTransfer`, `watch` and `close` map the TLS adapter outcomes without
  inventing retries; `flushPending` maps `tls::flush` and cannot accept
  plaintext. A TLS `close` is abortive and exactly once.
- `http1server::makeTls(listener, reactor, limits, ctx)` owns the
  `TlsContext`. Each accepted TCP stream becomes a `TlsSession`; the existing
  per-connection handshake state and absolute handshake deadline drive the TLS
  handshake. After `HandshakeDone`, `http1conn::acceptAlpn` accepts only
  `http/1.1`; a *negotiated* `h2` or other protocol closes before parsing, and
  no ALPN is accepted only when `Limits.allowNoAlpn` is true. The default HTTPS
  listener policy is TLS `Require(["http/1.1"])`; `Prefer` is only for an
  application that wants the HTTP-layer h2/other dispatch. The server closes the
  context after the last session during `shutdown`.
- Normal close after a response (or an idle TLS connection during graceful
  server shutdown) queues the local close_notify, drains remaining ciphertext,
  and then closes without waiting indefinitely for the peer close_notify; fatal
  errors and expiry take the abortive path. The per-connection close deadline is
  `responseTimeoutMs`.
- TLS session/context accounting stays in `systls`; HTTP admission continues to
  use `maxConnections` and `maxTotalConnectionBytes` for the fixed HTTP
  reservation. OpenSSL/BIO memory is outside that budget and is reported by
  `systls::counts()` in the H1c test driver.

## Build and test

From the repository root:

```sh
make -C obo obo runtime_text.bin runtime_data.bin   # or: make -C obo http1
make -C obo http1        # H1a0 + H1b + H1c
make -C obo http1b       # H1b alone
make -C obo http1c       # H1c HTTPS alone
```

`make http1` builds the seed compiler image, then runs the H1a0 in-memory suite,
the H1b suite and the H1c HTTPS suite. H1a0 covers exact bytes/statuses,
fragmentation, smuggling, limits, HTTP/1.0, 100-continue, HEAD/204/205/304,
injection, backpressure and dates. H1b covers the connection state machine and
deadlines over a memory transport, then loopback TCP IPv4/IPv6 and Unix sockets,
curl and raw clients, keep-alive/pipelining, 100-continue,
slowloris/accept/idle timeouts, request limits, 256 idle connections and
graceful shutdown. H1c covers TLS 1.2/1.3, ALPN `http/1.1`, h2/other/no-ALPN
policy, silent and partial handshake timeout, byte-at-a-time ClientHello
fragmentation, keep-alive/pipelining over TLS, slowloris, server-initiated
close_notify (client does not send one), large/slow-reader backpressure,
optional and required client certificates, plaintext-on-TLS-port rejection,
graceful shutdown and `curl -k`. Every driver runs with `OBO_CHECK=1`; cinterop builds
also verify their manifests. Because `http1io` links the TLS backend, even the
H1b memory/plain drivers use the combined systems+TLS cbind; they still open no
TLS listener and need no certificate. OpenSSL headers are located or fetched
privately by the TLS suite, exactly as in P2T-b.

## Explicit non-goals

No HTTP/2, HTTP/3, WebSocket, upgrade, `CONNECT` tunneling, routing,
middleware, compression, caching, redirects or a client. TLS client mode,
session tickets/resumption/0-RTT, SNI multi-certificate selection, revocation,
hidden threads and callbacks remain P2T-b non-goals. H1c does not buffer a whole
request or response, never parses a second request after a terminal error, and
has no request queue or concurrent handler. The `http1server` handler is a pure
router/action selector, not the full mutable-connection handler described in
the design; see the language note above.
