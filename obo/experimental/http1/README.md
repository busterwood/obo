# Experimental HTTP/1.1 protocol core (H1a0)

Status: **experimental H1a0 slice. Unstable. Not a merged or versioned API, and
not a stability, protocol-version or release claim.** This directory implements
only the pure grammar/parser/serializer/date part of the approved design in
`obo/design/http1-server-design.md`. It makes no compiler, runtime, ABI,
symbol-format, seed or package-version change.

H1a0 runs entirely in memory. It opens no socket, imports no systems module,
reads no clock and needs no reactor, TLS session or native C code. The caller
feeds bounded byte arrays and drains bounded output bytes. The network
`Connection`, deadlines, `Transport`, server loop, plain adapter and TLS adapter
belong to H1a/H1b/H1c and are **not** here.

## Layout

```text
obo/experimental/http1/
  http1core.obx      Limits, Header, RequestInfo, status/error tables, predicates
  http1parse.obx     incremental request parser (feed/next/readBody/eof)
  http1write.obx     response serializer and protocol error responses
  http1date.obx      IMF-fixdate formatter, RFC 850/asctime parser
  tests/
    test_http1.py    one-command H1a0 suite (no sockets, TLS or clock)
    apps/            small drivers used only by the tests
```

Modules are ordinary source modules and are imported by basename:

```obo
import "http1core.obx" as core
import "http1parse.obx" as http1parse
import "http1write.obx" as http1write
import "http1date.obx" as http1date
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

## Build and test

From the repository root:

```sh
make -C obo obo runtime_text.bin runtime_data.bin   # or: make -C obo http1
make -C obo http1
```

`make http1` builds the seed compiler image, then builds three small drivers and
runs the in-memory suite. The suite covers exact bytes/statuses, one-byte and
fragmented feeds, the smuggling corpus, CL/TE conflicts, duplicate fields, chunk
boundaries/extensions/trailers, exact limits, HTTP/1.0, Host/absolute-form,
100-continue, HEAD/204/205/304, response framing/injection, backpressure, error
responses and Date formats. Every driver runs with `OBO_CHECK=1`, so leaks and
double frees fail the suite.

## Explicit non-goals

No `Connection` state machine, transport interface, deadlines/timeouts, socket,
TLS, ALPN, server loop, routing, middleware, HTTP/2, WebSocket, upgrade,
`CONNECT` tunneling, compression, caching, redirects or a client. H1a0 does not
buffer a whole request or response, and it never parses a second request after a
terminal error.
