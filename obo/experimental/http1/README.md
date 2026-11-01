# Experimental HTTP/1.1 protocol core (H1a0 + H1b)

Status: **experimental H1a0/H1b slice. Unstable. Not a merged or versioned API,
and not a stability, protocol-version or release claim.** This directory
implements the pure grammar/parser/serializer/date core (H1a0) and the plain
transport, connection state machine and server loop (H1b) from the approved
design in `obo/design/http1-server-design.md`. It makes no compiler, runtime,
ABI, symbol-format, seed or package-version change.

H1a0 runs entirely in memory and has no systems dependency. H1b adds a
deterministic in-memory transport and a plain nonblocking `sysstream` transport,
explicit deadlines, keep-alive/pipelining, graceful drain, and a
`sysnet`/`sysreactor` loopback server loop. There is still **no TLS**: the
neutral TLS adapter and ALPN dispatch are H1c. A connection with parser-buffered
body bytes or a fairness-cap exit requests an immediate server turn, so an Echo
response cannot stall waiting for a socket-read event that will never arrive.

## Layout

```text
obo/experimental/http1/
  http1core.obx      Limits, Header, RequestInfo, status/error tables, predicates
  http1parse.obx     incremental request parser (feed/next/readBody/eof)
  http1write.obx     response serializer and protocol error responses
  http1date.obx      IMF-fixdate formatter, RFC 850/asctime parser
  http1transport.obx Transport operation seam, plain and memory adapters
  http1conn.obx      Connection phases, deadlines, framing, body/response API
  http1server.obx    listener accept, reactor watches, admission, drain, metrics
  tests/
    test_http1.py    one-command H1a0 suite (no sockets, TLS or clock)
    test_http1b.py   H1b memory + loopback suite (fake and real systems adapters)
    apps/            small drivers used only by the tests
```

Modules are ordinary source modules and are imported by basename:

```obo
import "http1core.obx" as core
import "http1parse.obx" as http1parse
import "http1write.obx" as http1write
import "http1date.obx" as http1date
import "http1transport.obx" as http1transport
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

- `http1transport::plain(stream)` wraps an owned `sysstream::Stream`;
  `http1transport::memory(bytes)` is the deterministic test transport. Both
  expose `handshakeStep`, `readSome`, `writeSome`, `flushPending`,
  `shutdownStep`, `pending`, `want`, `alpn`, `isSecure`, `maxTransfer`, `watch`
  and `close`. The plain adapter maps `WouldBlock` to `ReadWantRead`/
  `WriteWantWrite`, reports `AlpnNone`/`isSecure=false`/`pending=0` and never
  exposes a descriptor or native token.
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
- `http1transport` uses an opaque record with module functions rather than an
  interface for the same reason: 0.14 has no impure interface-member tier, so
  an interface method could not call `sysstream`/`sysnet`. The operation
  contract is unchanged.
- H1c seam note: the opaque `Transport` cannot be extended by another module.
  A feasible option is a dedicated `http1io` module that imports both
  `sysstream` and `systls` and defines a closed `Plain`/`Tls` transport; the
  connection then dispatches over that closed value and never imports `systls`
  itself. The alternative is a future compiler impure interface/function-value
  tier, which would also restore the design's mutable-`&Connection` handler.
  H1b implements neither TLS nor that compiler change; the pure action router
  and opaque transport above are the 0.14-compatible seam.

## Build and test

From the repository root:

```sh
make -C obo obo runtime_text.bin runtime_data.bin   # or: make -C obo http1
make -C obo http1        # H1a0 + H1b
make -C obo http1b       # H1b alone
```

`make http1` builds the seed compiler image, then runs the H1a0 in-memory suite
and the H1b suite. H1a0 covers exact bytes/statuses, fragmentation, smuggling,
limits, HTTP/1.0, 100-continue, HEAD/204/205/304, injection, backpressure and
dates. H1b covers the connection state machine and deadlines over a memory
transport, then loopback TCP IPv4/IPv6 and Unix sockets, curl and raw clients,
keep-alive/pipelining, 100-continue, slowloris/accept/idle timeouts, request
limits, 256 idle connections and graceful shutdown. Every driver runs with
`OBO_CHECK=1`; cinterop builds also verify their manifests.

## Explicit non-goals

No TLS implementation, `http1tls` adapter, ALPN negotiation over TLS,
certificate/key handling, HTTP/2, WebSocket, upgrade, `CONNECT` tunneling,
routing, middleware, compression, caching, redirects or a client. H1c (TLS) is
explicitly later. H1b does not buffer a whole request or response, never parses
a second request after a terminal error, and has no request queue or concurrent
handler. The `http1server` handler is a pure router/action selector, not the
full mutable-connection handler described in the design; see the language note
above.
