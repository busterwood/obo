# H1a0 API

Status: experimental, unstable. The declarations below are the H1a0 surface; H1a
may wrap or rename them when it adds `Connection`, `Transport` and deadlines.
No function here performs I/O.

## `http1core`

```obo
pub type Limits = ref record { /* pub fields; see LIMITS.md */ }
pub type Header = ref record { pub name: string; pub value: string }
pub type RequestInfo = ref record {
    pub method, target, path, query, scheme, authority: string
    pub versionMajor, versionMinor: int
    pub http10: bool
    pub headers: []Header
    pub hasContentLength: bool
    pub contentLength: int
    pub chunked: bool
    pub hasBody: bool
    pub expectContinue: bool
    pub keepAlive: bool
    pub close: bool
}

pub fn defaultLimits(): Limits
pub fn copyLimits(l: &Limits): Limits
pub fn validateLimits(l: &Limits): Result[bool]

pub fn isToken(s: string): bool
pub fn equalFold(a: string, b: string): bool
pub fn isForbiddenTrailer(name: string): bool
pub fn isWriterOwned(name: string): bool
pub fn statusText(status: int): string
pub fn errorBody(status: int): string
```

`RequestInfo` is an owned snapshot. `target`, `path`, `query` and `authority`
are raw byte strings (no decoding or normalization). `headers` is ordered and
keeps duplicates. `contentLength` is meaningful only when `hasContentLength` is
true. `keepAlive` is the policy for this request; a last allowed request has
`keepAlive == false`.

Error codes used by non-protocol failures are `ERR_INVALID_STATE`,
`ERR_INVALID_ARGUMENT`, `ERR_LIMIT_EXCEEDED`, `ERR_INCOMPLETE_BODY` and
`ERR_INTERNAL`. Parser protocol errors use their HTTP status as both the `Error`
event's `status` and `code`.

## `http1parse`

```obo
pub type Event = closed {
    NeedRead {}
    HeadReady {}
    BodyData { bytes: []byte }
    BodyEnd {}
    Error { status: int; code: int; message: string }
    Failed {}
    Closed {}
}

pub type ContinueState = closed {
    ContinueNone {}
    ContinueDue {}
    ContinueSent {}
    ContinueSkipped {}
}

pub type Parser = ref record { /* private fields */ }

pub fn makeParser(l: &Limits): Result[Parser]
pub fn feed(mut p: &Parser, data: &[]byte): Result[bool]
pub fn next(mut p: &Parser): Result[Event]
pub fn nextHead(mut p: &Parser): Result[Event]
pub fn readBody(mut p: &Parser, max: int): Result[Event]
pub fn eof(mut p: &Parser): Result[bool]

pub fn requestInfo(p: &Parser): Result[RequestInfo]
pub fn headerCount(p: &Parser): int
pub fn headerAt(p: &Parser, index: int): Result[Header]
pub fn headerValue(p: &Parser, name: string): Result[?Header]
pub fn trailerCount(p: &Parser): int
pub fn trailerAt(p: &Parser, index: int): Result[Header]
pub fn trailerValue(p: &Parser, name: string): Result[?Header]

pub fn continueState(p: &Parser): ContinueState
pub fn continueDue(p: &Parser): bool
pub fn markContinueSent(mut p: &Parser): Result[bool]
pub fn buffered(p: &Parser): int
pub fn isFailed(p: &Parser): bool
pub fn isComplete(p: &Parser): bool
pub fn requestNumber(p: &Parser): int
```

### Driving the parser

1. `makeParser` validates and deep-copies `Limits`. The input buffer is
   `maxHeaderBytes + maxHeaderLine` bytes; it never grows.
2. `feed` copies caller bytes into the bounded input buffer all-or-nothing.
   `false` means that call copied nothing and the parser is terminal: the buffer
   is full, a clean EOF was already signalled, or a `Closed` was already emitted.
   Callers must feed bounded pieces and treat `false` as terminal.
3. `next` is the H1a0 streaming convenience: it returns `HeadReady` once, then
   body events, then `BodyEnd` once. When no complete line or body byte is
   buffered it returns `NeedRead` without losing state. After `BodyEnd`, a later
   `next` either parses the next buffered pipelined request, returns `NeedRead`,
   returns `Closed` (close policy/EOF) or returns a terminal error.
4. `nextHead` is the H1a gate: it parses a head, errors, `NeedRead` or `Closed`
   and never streams a body. While a head is ready and its body is unread it
   returns `InvalidState`; the caller must use `readBody` to reach `BodyEnd`.
   The future `Connection` must use this pair, not body-streaming `next`.
5. `readBody(max)` is the body reader with a caller bound `1..max`. It returns
   `BodyData` with 1..max owned bytes, `BodyEnd`, `NeedRead` or a terminal error.
   `max <= 0` is an argument error; a second call after `BodyEnd`, or a call
   before `HeadReady`, is `InvalidState`.
6. `eof` records a clean end of input exactly once. A partial start line/header/
   body/chunk/trailer becomes `400`; an idle parser becomes `Closed`; an exact
   complete message is unaffected. Once EOF is signalled, `feed` returns `false`,
   but bytes already buffered before the EOF may still parse. `Closed` is
   terminal: later `next`/`nextHead` stay `Closed` and `feed` stays `false`.

`BodyData.bytes` is a fresh owned array, never a slice of the input buffer.
Bytes after a completed body are next-request bytes and are never consumed as
body. On `Error`, the next `next` returns `Failed` and no new request is parsed.
On `Closed`, the parser is terminal, not merely idle.

### Continue state

`ContinueDue` means an interim `100 Continue` may be sent. `markContinueSent`
moves it to `ContinueSent` and is idempotent. `ContinueSkipped` means body bytes
were already buffered when the head completed, or body progress began before the
interim response. `ContinueNone` means no body, no valid `Expect`, or HTTP/1.0.
The parser never writes bytes; use `http1write::continueBytes`.

### Keep-alive and pipelining

A parser is one in-flight request at a time. It parses the next request only
after `BodyEnd`. HTTP/1.1 is persistent by default; `Connection: close`,
HTTP/1.0 compatibility, `maxRequests` or `pipelining == false` produce `Closed`
after the current body/response boundary. With `pipelining == false` a
sequential second request fed later is also refused: `Closed` is terminal and
`feed` returns `false`. Already-buffered pipelined bytes are a `400`. Buffered bytes beyond the current message are bounded
by `maxPipelineBytes`. There is no request queue.

## `http1write`

```obo
pub type Response = ref record { /* private fields */ }

pub fn makeResponse(l: &Limits, method: string, http10: bool, close: bool,
                    dateSeconds: int, dateNanos: int): Result[Response]
pub fn beginResponse(mut r: &Response, status: int): Result[bool]
pub fn beginResponseWithReason(mut r: &Response, status: int,
                               reason: string): Result[bool]
pub fn setHeader(mut r: &Response, name: string, value: string): Result[bool]
pub fn setLength(mut r: &Response, length: int): Result[bool]
pub fn setTrailerNames(mut r: &Response, names: &[]string): Result[bool]
pub fn addTrailer(mut r: &Response, name: string, value: string): Result[bool]
pub fn writeBody(mut r: &Response, bytes: &[]byte, offset: int,
                 count: int): Result[int]
pub fn writeText(mut r: &Response, text: string, offset: int,
                 count: int): Result[int]
pub fn endResponse(mut r: &Response): Result[bool]
pub fn takeOutput(mut r: &Response): Result[[]byte]
pub fn pending(r: &Response): int
pub fn isCommitted(r: &Response): bool
pub fn isDone(r: &Response): bool
pub fn hasFailed(r: &Response): bool
pub fn errorResponse(l: &Limits, status: int, isHead: bool, http10: bool,
                     dateSeconds: int, dateNanos: int): Result[[]byte]
pub fn continueBytes(): []byte
```

Rules:

- `makeResponse` validates `Limits`, validates the method token and formats the
  supplied Date once. It owns a fixed-capacity output budget
  (`maxResponseBuffer`). `takeOutput` returns and clears the pending bytes.
- `beginResponse` accepts `200..599`; `1xx` is internal only. It fixes status and
  reason but does not commit headers.
- `setHeader` accepts application fields before commit. It rejects writer-owned
  `Date`, `Server`, `Content-Length`, `Transfer-Encoding`, `Connection`,
  `Trailer`, `Keep-Alive` and `Upgrade` names and all CR/LF/NUL/control bytes.
  Values are trimmed of leading/trailing SP/HTAB. Duplicates are preserved in
  insertion order.
- `setLength` sets fixed framing. `writeBody` then requires exactly that many
  bytes for a normal response; a short body fails `endResponse` and an overrun
  fails `writeBody`. HEAD/304 treat the length as metadata.
- Without a length, the first `writeBody` commits chunked framing (HTTP/1.1) or
  close-delimited framing (HTTP/1.0). `endResponse` with no body writes and no
  length commits `Content-Length: 0`, not an empty chunked message.
- HEAD discards body writes but still accepts them. 1xx/204/205/304 never put
  body bytes on the wire. 204 and 1xx reject `Content-Length`; 205 accepts only
  length 0 and emits `Content-Length: 0`; 304 may carry length metadata.
- Response trailers are opt-in, chunked-only and must be declared before a body
  write. `addTrailer` requires a committed chunked response after a body write.
  The `Trailer` field counts toward the header limit; trailer count/bytes are
  bounded separately.
- `endResponse` returns `false` when the final chunk/trailer block does not fit
  the current output budget. The caller must `takeOutput` and call it again;
  `false` is not completion. A second successful `endResponse` is an error.
- `errorResponse` produces the static protocol error body from `http1core` with
  `Content-Type: text/plain; charset=utf-8`, exact `Content-Length`, `Date`,
  optional `Server`, and `Connection: close`. It never echoes request data.
  For HEAD it keeps the length and omits the body.
- `continueBytes` is exactly `HTTP/1.1 100 Continue\r\n\r\n`.

Header order on the wire is status line, `Date`, `Server` (when configured),
application fields in insertion order, framing field, `Connection: close` when
closing, then the blank line.

## `http1date`

```obo
pub type DateValue = ref record { pub seconds: int; pub nanos: int }

pub fn formatHttpDate(seconds: int, nanos: int): Result[string]
pub fn parseHttpDate(text: string, nowSeconds: int): Result[DateValue]
```

`formatHttpDate` emits IMF-fixdate in GMT and validates `nanos` 0..999999999 and
years 0001..9999. `parseHttpDate` accepts IMF-fixdate, RFC 850 and asctime
case-insensitively for names, rejects trailing text, and uses `nowSeconds` only
for the RFC 850 50-year two-digit-year window. That window is applied at
timestamp granularity: a candidate more than the same instant 50 calendar years
later rolls back one century; exactly 50 years does not. RFC 850 requires a full
weekday name. Weekday names are checked as known names but are not required to
match the calendar date. The later clock wrapper adapts `clock::Timestamp` to
these explicit seconds/nanos functions.
