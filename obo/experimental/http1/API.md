# H1a0/H1b API

Status: experimental, unstable. The first part is the H1a0 pure grammar,
serializer and date surface. The H1b section at the end adds the transport,
connection state machine and server loop; it performs I/O, deadlines and socket
accept. H1c (TLS/`http1tls`) is explicitly later. Nothing here is a stability,
protocol-version or release claim.

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

---

# H1b API (experimental)

Status: experimental, unstable. H1b adds the transport seam, connection state
machine and server loop. It does **not** add TLS, `http1tls`, routing or a full
mutable-connection handler; H1c is later.

## `http1transport`

`Transport` is an opaque record, not an interface: 0.14 has no impure
interface-member tier, so an interface method could not call `sysstream`.

```obo
pub type Step = closed { StepDone {} StepWantRead {} StepWantWrite {} }
pub type ReadOutcome = closed {
    ReadData { bytes: []byte } ReadEof {} ReadWantRead {} ReadWantWrite {}
}
pub type WriteOutcome = closed {
    WriteWrote { count: int } WriteWantRead {} WriteWantWrite {}
}
pub type ShutdownOutcome = closed {
    ShutdownDone {} ShutdownSent {} ShutdownPeerClosed {}
    ShutdownWantRead {} ShutdownWantWrite {}
}
pub type Want = closed { WantNone {} WantRead {} WantWrite {} WantReadWrite {} }
pub type Alpn = closed { AlpnNone {} AlpnHttp11 {} AlpnH2 {} AlpnOther { name: string } }

pub fn plain(s: sysstream::Stream): Transport
pub fn memory(input: &[]byte): Transport
pub fn empty(): Transport

pub fn handshakeStep(mut t: &Transport): Result[Step]
pub fn readSome(mut t: &Transport, max: int): Result[ReadOutcome]
pub fn writeSome(mut t: &Transport, bytes: &[]byte, offset: int,
                 count: int): Result[WriteOutcome]
pub fn flushPending(mut t: &Transport): Result[WriteOutcome]
pub fn shutdownStep(mut t: &Transport): Result[ShutdownOutcome]
pub fn pending(t: &Transport): Result[int]
pub fn want(t: &Transport): Result[Want]
pub fn alpn(t: &Transport): Result[Alpn]
pub fn isSecure(t: &Transport): bool
pub fn maxTransfer(t: &Transport): int
pub fn watch(t: &Transport, r: &sysreactor::Reactor, readable: bool,
             writable: bool, tag: int): Result[sysreactor::Watch]
pub fn close(mut t: &Transport): Result[bool]
```

Rules:

- `readSome(max)` requires `1 <= max <= maxTransfer` (65536) and returns owned
  bytes, a clean `ReadEof`, or a `Want*`. A short read is normal.
- `writeSome` returns the accepted prefix. `WriteWrote{0}` for a positive count
  is an adapter invariant violation; the connection closes instead of spinning.
  `WouldBlock` maps to `WriteWantWrite`; `readSome` maps it to `ReadWantRead`.
- `flushPending` drains ciphertext only (plain has none). `pending` reports
  adapter ciphertext; `Flushed` requires the HTTP buffer empty and `pending == 0`.
- `shutdownStep` is multi-step. `close` is abortive and exactly once.
- `watch` passes a checked handle internally; no fd, native token or stream
  identity is exposed. The memory transport has no watch and returns
  `Unsupported`.
- `memory*` helpers configure the deterministic test transport (read/write
  limits, pending ciphertext, want flags, ALPN, secure flag, captured output).

## `http1conn`

```obo
pub type Now = ref record { pub mono: sysclock::Instant; pub wall: sysclock::Timestamp }
pub type Phase = closed {
    Handshaking {} Idle {} ReadingHead {} ReadingBody {}
    Responding {} Draining {} Closing {} Closed {}
}
pub type Event = closed {
    HandshakeDone {} NeedRead {} NeedWrite {} HeadReady {} ResponseComplete {}
    TimedOut {} ProtocolError { status: int } ConnectionClosed {}
}
pub type BodyRead = closed { BodyData { bytes: []byte } BodyEnd {} BodyNeedRead {} }
pub type FlushOutcome = closed { Flushed {} FlushWantRead {} FlushWantWrite {} }

pub fn make(t: http1transport::Transport, l: &Limits): Result[Connection]
pub fn phase(c: &Connection): Phase
pub fn deadline(c: &Connection): sysclock::Deadline
pub fn next(mut c: &Connection, now: &Now): Result[Event]
pub fn readBody(mut c: &Connection, max: int, now: &Now): Result[BodyRead]
pub fn discardBody(mut c: &Connection, maxBytes: int, now: &Now): Result[bool]
pub fn requestInfo(c: &Connection): Result<RequestInfo>
pub fn headerCount(c: &Connection): int
pub fn headerAt(c: &Connection, index: int): Result<Header>
pub fn headerValue(c: &Connection, name: string): Result[?Header]
pub fn trailerValue(c: &Connection, name: string): Result[?Header]
pub fn beginResponse(mut c: &Connection, status: int, now: &Now): Result[bool]
pub fn beginResponseWithReason(mut c: &Connection, status: int, reason: string,
                               now: &Now): Result[bool]
pub fn setHeader(mut c: &Connection, name: string, value: string): Result[bool]
pub fn setLength(mut c: &Connection, length: int): Result[bool]
pub fn setTrailerNames(mut c: &Connection, names: &[]string): Result[bool]
pub fn addTrailer(mut c: &Connection, name: string, value: string): Result[bool]
pub fn writeBody(mut c: &Connection, bytes: &[]byte, offset: int,
                 count: int): Result[int]
pub fn writeText(mut c: &Connection, text: string, offset: int,
                 count: int): Result[int]
pub fn endResponse(mut c: &Connection, now: &Now): Result[bool]
pub fn flush(mut c: &Connection, now: &Now): Result[FlushOutcome]
pub fn sendContinue(mut c: &Connection): Result[bool]
pub fn requestClose(mut c: &Connection): Result[bool]
pub fn closeAfterResponse(c: &Connection): bool
pub fn acceptAlpn(mut c: &Connection): Result[bool]
pub fn shutdownStep(mut c: &Connection, now: &Now): Result[ShutdownOutcome]
pub fn close(mut c: &Connection): Result[bool]
pub fn wantsRead(mut c: &Connection): Result[bool]
pub fn wantsWrite(mut c: &Connection): Result[bool]
```

Rules:

- `make` validates and deep-copies `Limits`, creates the parser and response
  buffers and arms the accept/handshake deadlines on the first `next`. A plain
  transport completes `handshakeStep` immediately; the caller must call
  `acceptAlpn` after `HandshakeDone` before any HTTP parse.
- `next` checks active deadlines, drives the handshake, flushes bounded output,
  then parses a head or advances a completed response. It never reads a body on
  the handler's behalf. While a body is unread it returns `InvalidState` instead
  of parsing a second request.
- `readBody` returns 1..max owned bytes, `BodyEnd` once, or `BodyNeedRead`. It
  starts the body-progress deadline on the first call and renews it only on
  positive decoded data. `Expect: 100-continue` is sent on the first read when
  `autoContinue` is true and no body was already buffered. `readBody` is
  `InvalidState` after `endResponse` or after an early final response that set
  the close policy. The strict design reading ("after any beginResponse") was
  relaxed so the server can stream a request body into a chunked response; the
  early-final Expect case remains closed.
- `writeBody`/`writeText` accept only what fits the bounded encoded buffer and
  return the accepted prefix (0 means flush and retry). `endResponse` returns
  `false` while the final chunk/trailer block does not fit; callers must flush
  and retry. The connection does not spin.
- `flush` performs at most one `writeSome` and then drains adapter ciphertext;
  `Flushed` requires both buffers empty. `next` maps `FlushWantRead/Write` to
  `NeedRead/NeedWrite`.
- `requestClose` sets `Connection: close` before header commit; after commit the
  connection closes after the flushed response. `shutdownStep` flushes, then
  drives the transport's multi-step shutdown. `close` is abortive and
  idempotent.
- Deadline policy: accept/handshake are disarmed on first request byte/
  `StepDone`; header is absolute from the first byte; body idle starts at first
  read; idle starts when a response completes; response idle renews on positive
  writes; response total starts at header commit. `deadline` returns the earliest
  active finite deadline or `forever()`. Timeouts queue `408` when a response is
  still possible, otherwise close silently.
- `discardBody` is a bounded early-response drain; it refuses after the final
  response and closes when its byte cap is exceeded.
- `memoryOutput`, `memoryClearOutput`, `memoryAppendInput`,
  `memorySetReadLimit`, `memorySetWriteLimit`, `memorySetWant`,
  `memorySetPending`, `memorySetAlpn`, `memorySetSecure`, `memoryPending` and
  `memoryClosed` are deterministic test accessors for the memory transport.

## `http1server`

```obo
pub type Action = closed {
    Hello {} Echo {} Chunked {} Head {} NoContent {}
    NotModified {} Big {} Slow {} Stop {} Close {}
}
pub type Handler = fn(http1core::RequestInfo, &http1conn::Now): Action
pub type Metrics = ref record { /* accepted, rejected, closedConnections, ... */ }

pub fn make(l: sysnet::Listener, r: sysreactor::Reactor,
            limits: &Limits): Result[Server]
pub fn runOnce(mut s: &Server, handler: Handler, maxEvents: int): Result[bool]
pub fn beginShutdown(mut s: &Server, now: &http1conn::Now): Result[bool]
pub fn drain(mut s: &Server, handler: Handler, maxTurns: int): Result[bool]
pub fn shutdown(mut s: &Server, now: &http1conn::Now): Result[bool]
pub fn metrics(s: &Server): Metrics
pub fn active(s: &Server): int
pub fn reservedBytes(s: &Server): int
pub fn isStopping(s: &Server): bool
pub fn stopRequested(s: &Server): bool
pub fn now(): Result<http1conn::Now>
pub fn isAcceptPressure(code: int): bool
pub fn admissionFits(active: int, reserved: int, perConnection: int,
                     limits: &Limits): bool
```

Rules:

- `make` owns the listener and reactor, validates `Limits`, registers the
  listener watch and starts an empty fixed slot table. `maxConnections` is
  capped at 65536 (the tag table width).
- `runOnce` blocks in `reactor::wait` until an event or the earliest connection/
  accept-pause/drain deadline. It accepts at most 32 per batch, drives each ready
  slot at most 32 events, checks all deadlines again after I/O, and refreshes
  watch interest. It never registers writable interest without queued output or
  transport `WantWrite`.
- Admission enforces both the count and the aggregate reservation. When full it
  leaves sockets in the accept queue; a `ResourceLimit` accept error (EMFILE/
  ENFILE) pauses acceptance for 100 ms and increments `acceptPressure`.
- `beginShutdown` removes the listener watch, stops accepting, closes idle/
  handshaking connections immediately and calls `requestClose` on in-flight
  connections. `drain` runs turns until active reaches zero or the drain
  deadline. `shutdown` closes remaining slots, the listener and the reactor.
- `Handler` is pure. It cannot drive the connection because 0.14 function values
  and interface methods cannot be impure. The server itself performs the bounded
  actions: `Hello` (`hello\n`), `Echo` (stream the request body into a chunked
  response, sending `100 Continue` when needed), `Chunked`, `Head`, `NoContent`,
  `NotModified`, `Big` (20 KB fixed), `Slow` (10 bytes per turn),
  `Stop` (respond, then request shutdown) and `Close` (`Connection: close` then
  `Hello`). A general application handler needs a future impure function-value
  tier or a different dispatch API; this is documented as an H1c/follow-up
  language gap, not a hidden callback.
- Tags are `generation * 65536 + slot`; stale events from a retired generation
  are ignored. Transport close happens exactly once per slot.
