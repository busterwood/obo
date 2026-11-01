# `http2` public API (experimental H2a/H2b/H2c)

Status: experimental and unstable. Every function returns `Result[...]` or a
plain value as shown. Errors carry an `httpapi` failure code; decode with
`http2::failure(code)` and `http2::text(code)`. `stats`/`counts` are diagnostic,
not authority.

```obo
module http2
import "httpapi.obx" as http
import "httptransport.obx" as transport
```

## Kinds and modes

```obo
pub const KIND_H2_CONNECTION = 25
pub const KIND_H2_STREAM = 26

pub type Mode = closed { ModeTls {} ModePriorKnowledge {} }
pub type Options = ref record {
    pub mode: Mode
    pub serverName: string
    pub requireAuthority: bool
}
```

`ModeTls` requires a secure transport with ALPN `h2`. `ModePriorKnowledge`
requires a cleartext transport and validates the 24-byte client preface before
any frame. There is no no-ALPN fallback and no HTTP/1.1 `Upgrade`.

## Limits, options and admission

```obo
pub type Limits = ref record { /* see LIMITS.md */ }
pub fn defaultLimits(): Limits
pub fn copyLimits(l: &Limits): Limits
pub fn validateLimits(l: &Limits): Result[bool]
pub fn reservation(l: &Limits): Result[int]
pub fn defaultOptions(): Options

pub type Admission = ref record { /* private counters */ }
pub fn makeAdmission(): Admission
pub fn admit(mut a: &Admission, l: &Limits): Result[bool]
pub fn release(mut a: &Admission, reserved: int): bool
pub fn activeCount(a: &Admission): int
pub fn admissionReservedBytes(a: &Admission): int
```

`validateLimits` deep-checks positive ranges, the checked
`maxConnectionWindow == maxConcurrentStreams * maxStreamRecvWindow` identity,
the per-stream staging/arena and event formulas, the wire bound and the
aggregate reservation. Invalid limits return an error; callers should treat any
error or `ok(false)` as rejection. `reservation` returns the per-connection
charge used for admission. `Admission` is application-owned policy: call
`admit` before `make`, and `release(a, http2::reservedBytes(c))` after `close`.
`make` enforces only per-connection limits.

## Shells

```obo
pub type Connection = ref record {
    pub res: platform::Resource
    /* private transport, limits, output buffer and state */
}
pub type Stream = ref record {
    pub res: platform::Resource
    /* private read/write limits and request flags */
}
```

`Connection` owns the transport after `make`. `Stream` is a real shell over a
`KIND_H2_STREAM` entry; it does not hold a connection handle. A stale or forged
resource fails its own kind check before any C dereference. `_emptyStream()` is a
private-shell placeholder for test/driver `replace` moves; it is not a usable
stream.

## Connection operations

```obo
pub fn make(t: transport::Transport, limits: &Limits,
            options: &Options): Result[Connection]
pub fn next(mut c: &Connection, now: &http::Now): Result[Event]
pub fn deadline(c: &Connection): Result[clock::Deadline]
pub fn pending(c: &Connection): Result[int]
pub fn want(c: &Connection): Result[transport::Want]
pub fn wantsRead(c: &Connection): Result[bool]
pub fn wantsWrite(c: &Connection): Result[bool]
pub fn reservedBytes(c: &Connection): int
pub fn state(c: &Connection): Result[int]
pub fn settingsAcked(c: &Connection): Result[bool]
pub fn shutdownStep(mut c: &Connection,
                    now: &http::Now): Result[transport::ShutdownOutcome]
pub fn watch(mut c: &Connection, r: &reactor::Reactor, readable: bool,
             writable: bool, tag: int): Result[reactor::Watch]
pub fn memoryOutput(mut c: &Connection): Result[[]byte]
pub fn memoryAppendInput(mut c: &Connection, bytes: &[]byte): Result[bool]
```

`make` moves the transport. On any validation/mode/ALPN failure it closes the
transport exactly once and returns no connection token. `next` performs one
transport write/flush and at most one read per call, returns at most one event,
and never loops on readiness. `deadline` returns the earliest active finite
monotonic deadline (nanoseconds) or `clock::forever()`. `memoryOutput` and
`memoryAppendInput` are deterministic test helpers; they are `Unsupported` on
non-memory transports. `close` is abortive and idempotent (`ok(false)` on the
second close); `drain` is the graceful path.

## Events

```obo
pub type Event = closed {
    EventNeedRead {}
    EventNeedWrite {}
    EventStreamStarted { id: int }
    EventStreamData { id: int }
    EventStreamEnd { id: int }
    EventTrailers { id: int }
    EventStreamClosed { id: int; code: int }
    EventSettingsAcked {}
    EventGoaway { lastStreamID: int; code: int }
    EventTimedOut { id: int }
    EventProtocolError { code: int }
    EventDrainComplete {}
    EventConnectionClosed {}
}
```

Events are fixed records; header names/values and body bytes are pulled from
bounded per-stream C buffers. `EventStreamData` is coalesced. The event ring is
fixed at `maxEvents`; overflow is a connection failure. `code` on
`EventStreamClosed`/`EventGoaway` is the HTTP/2 error code. `EventProtocolError`
carries an `httpapi` failure code (see `failure`/`text`).

## Streams

```obo
pub type StreamState = closed {
    StreamOpen {}
    StreamHalfClosedRemote {}
    StreamHalfClosedLocal {}
    StreamClosed {}
}

pub fn openStream(mut c: &Connection, id: int): Result[Stream]
pub fn closeStream(s: &Stream): Result[bool]
pub fn reset(s: &Stream, code: int): Result[bool]
pub fn streamID(s: &Stream): Result[int]
pub fn streamState(s: &Stream): Result[StreamState]
pub fn windows(s: &Stream): Result[Windows]
pub fn commitResponse(s: &Stream, endStream: bool): Result[bool]

pub type Windows = ref record { pub stream: int; pub connection: int }
```

`openStream` requires a positive odd server-side stream id and adopts/registers
the C stream entry exactly once. A second call returns `InvalidState`; an
unknown/closed id is `StreamClosed`. If the platform shell/slot adoption fails,
obo calls the idempotent `h2_stream_abandon` path: no double free, one
`REFUSED_STREAM`, and buffered connection window restored. `closeStream` marks
the record closing, submits `RST_STREAM(CANCEL)` if still open, and retires the
token; the C record is freed only by the post-feed/post-send sweep. A second
close or a peer/stale close returns `ok(false)`. `reset` submits the caller's
raw HTTP/2 error code. `commitResponse` submits staged headers early; normal
streams commit on the first body write or `endResponse`.

## Request headers and body

```obo
pub fn requestInfo(s: &Stream): Result[http::RequestMeta]
pub fn headerCount(s: &Stream): Result[int]
pub fn headerAt(s: &Stream, index: int): Result[http::Header]
pub fn trailerCount(s: &Stream): Result[int]
pub fn trailerAt(s: &Stream, index: int): Result[http::Header]
pub fn readBody(mut s: &Stream, max: int, now: &http::Now): Result[http::BodyRead]
pub fn discardBody(mut s: &Stream, max: int, now: &http::Now): Result[int]
```

`requestInfo` copies the whole lazy request header list into an owned
`RequestMeta` and releases the C request copy; call it once. `headerAt` works
before it and returns `InvalidState` afterwards. Pseudo-headers are excluded
from `RequestMeta.headers`; a regular `Host` field is retained there and also
used as the authority fallback. `RequestMeta.target`/`path`/`query` are raw bytes;
no percent-decoding or normalization is performed. `requireAuthority` rejects
an empty `:authority`/`Host`; a non-empty `:authority` that disagrees with
`Host` is a protocol error, and a declared `content-length` above `maxBody` is
rejected. `readBody` returns owned `BodyData`, `BodyEnd` or `BodyNeedRead`;
`max` is capped at `maxReadChunk` and the cumulative delivered/discarded body is
capped at `maxBody`. `discardBody` drops and consumes bytes with the same bound,
so an unread body does not hold the window.

Request header/trailer accessors return `Closed` for an orphaned stream (after
connection close) or a stream already closed by nghttp2; the C arenas are freed
before an orphaned shell is retired, so no accessor may touch them. `streamID`
and `streamState` on an orphan return `Closed`.

## Response

```obo
pub fn beginResponse(mut s: &Stream, status: int): Result[bool]
pub fn setHeader(s: &Stream, name: string, value: string): Result[bool]
pub fn setLength(s: &Stream, length: int): Result[bool]
pub fn writeBody(mut s: &Stream, bytes: &[]byte, offset: int,
                 count: int): Result[int]
pub fn addTrailer(s: &Stream, name: string, value: string): Result[bool]
pub fn setTrailerNames(s: &Stream, names: &[]string): Result[bool]
pub fn endResponse(s: &Stream): Result[bool]
pub fn interim(s: &Stream, status: int): Result[bool]
```

`begin` is once. Headers commit on the first `writeBody`/`endResponse`.
`setLength` declares `content-length`; C injects the protocol-owned header and
`setHeader("content-length", ...)` is rejected. Normal `2xx` responses must
deliver exactly the declared DATA sum; `HEAD`, `1xx`/`204`/`205`/`304` are
exempt and `writeBody` accepts-and-discards their bytes without emitting DATA.
`writeBody` accepts only free send-buffer space and returns the accepted count
(`0` means full; yield and retry). Trailers are declared before body
writes and added after the body; `endResponse` uses `NO_END_STREAM` and
`nghttp2_submit_trailer`. A trailer after `END_STREAM` is `InvalidState`.
`interim` is `Unsupported` in H2a; `101` is invalid.

## Drain, close and counters

```obo
pub fn drain(mut c: &Connection, now: &http::Now): Result[bool]
pub fn drainPoll(c: &Connection, now: &http::Now): Result[bool]
pub fn drainComplete(c: &Connection): bool
pub fn goaway(c: &Connection, lastStreamID: int, code: int): Result[bool]
pub fn close(mut c: &Connection): Result[bool]

pub type Stats = ref record { /* pub fields; see design */ }
pub type Counts = ref record { /* pub fields; see design */ }
pub fn stats(c: &Connection): Result[Stats]
pub fn counts(): Result[Counts]
```

`drain` submits the first GOAWAY notice, records the stream cutoff and refuses
new ids above it (a CLOSING/CLOSED connection also refuses new streams). `drainPoll` submits the barrier PING only after the notice is
sent, waits for the matching ACK or `drainBarrierMs`, then submits the final
GOAWAY with `min(last_proc, cutoff)`. `drainComplete` is true only after the
final GOAWAY is queued and no live streams remain. Idle timeout is armed whenever
the preface is valid and no live streams remain; it queues GOAWAY(NO_ERROR),
emits `EventTimedOut`, enters CLOSING and remains flushable. A fatal shim/nghttp2
error enters FAILED but leaves a bounded send path open so `next` can flush the
queued GOAWAY before the application closes. Settings-timeout, callback/flood, and
event-ring-overflow failures queue GOAWAY and use the same flush path; a preface
timeout has no GOAWAY and `next` ignores the resulting no-output send error while
still returning `EventTimedOut`. A clean `terminate(NO_ERROR)` (for example EOF)
enters CLOSING and is not reported as a protocol error. `close` retires the connection
token and closes the transport exactly once; live stream shells are orphaned and
remain closable, returning `Closed` for operations.

## Pure action executor

```obo
pub type ActionState = ref record { /* private progress */ }
pub fn makeActionState(): ActionState
pub fn runAction(mut st: &ActionState, h: http::Handler,
                 req: &http::RequestMeta, mut s: &Stream,
                 now: &http::Now): Result[bool]
pub fn actionStopRequested(st: &ActionState): bool
pub fn actionCloseRequested(st: &ActionState): bool
pub fn actionDone(st: &ActionState): bool
```

`runAction` latches the first action returned by the pure handler, executes the
closed action set (`Hello`, `Echo`, `Chunked`, `Head`, `NoContent`,
`NotModified`, `Big`, `Slow`, `Stop`, `Close`), and returns `false` on yield
(write buffer full, body not ready, or end not yet accepted). It never spins and
never calls a handler twice to change a decision. `actionDone` is true only
after the response is ended and the request body is consumed; the server loop
uses it to distinguish a completed action from a yielded one. Non-echo actions discard any
remaining request body in bounded pieces; an oversized body or timeout resets
with `CANCEL`. The function is ordinary impure module code, not a callback or
interface.

## Diagnostics

```obo
pub fn failure(code: int): http::Failure
pub fn text(code: int): string
pub fn nativeVersion(): Result[int]
pub fn diagnostic(code: int): Result[string]
pub fn consumeConnection(c: &Connection, bytes: int): Result[bool]
pub fn resetCounts(mut c: &Connection): Result[bool]
```

`failure`/`text` map numeric failure codes. `nativeVersion` reports
`NGHTTP2_VERSION_NUM`. `diagnostic` copies the shim's bounded text for a code.
`consumeConnection` restores connection-level window for an externally
discarded range; normal reads/discards/retirement already do this.
`resetCounts` zeroes diagnostic counters only.

## Reactor server (`http2server`)

`http2server` is the H2b/H2c concrete server loop. It owns a nonblocking
listener, a level-triggered reactor, the optional `systls::TlsContext`, and a
fixed table of connection slots. Each connection owns a bounded table of
`http2::Stream` shells. The handler is the same pure `httpapi::Handler`; no C
callback, hidden thread or interface dispatch is involved.

```obo
import "http2server.obx" as server

pub type Handler = httpapi::Handler

pub fn make(listener: net::Listener, r: reactor::Reactor,
            limits: &http2::Limits,
            options: &http2::Options): Result[Server]
pub fn makeTls(listener: net::Listener, r: reactor::Reactor,
               limits: &http2::Limits, options: &http2::Options,
               ctx: tls::TlsContext): Result[Server]

pub fn runOnce(mut s: &Server, handler: Handler,
               maxEvents: int): Result[bool]
pub fn run(mut s: &Server, handler: Handler,
           maxTurns: int): Result[bool]
pub fn beginShutdown(mut s: &Server, now: &httpapi::Now): Result[bool]
pub fn drain(mut s: &Server, handler: Handler,
             maxTurns: int): Result[bool]
pub fn shutdown(mut s: &Server, now: &httpapi::Now): Result[bool]
```

`make` is the cleartext prior-knowledge (h2c) listener. Every accepted
transport is passed to `http2::make` with `ModePriorKnowledge`; the 24-byte
preface is validated before any frame and an HTTP/1.1 `Upgrade` is never
accepted. `makeTls` takes ownership of a `systls` context. The caller must
configure `Require(["h2"])`; the loop additionally requires the negotiated
ALPN to be exactly `h2` after the handshake and closes the session otherwise.
The context is closed by `shutdown` after the last session.

Admission uses `http2::reservation(limits)` plus both `maxConnections` and
`maxTotalConnectionBytes`. TLS sessions, transport staging and other native
users are outside that H2 reservation. Accept batches are capped at 32, and
`ResourceLimit`/`EMFILE` pauses acceptance with a short backoff instead of
spinning.

Connection slots are tagged by `(generation, index)` so a stale reactor event
cannot drive a reused slot. A connection failure, handler failure, protocol
error or timeout closes only that connection; the loop and other connections
keep running. Per-turn work is bounded (32 connection steps and 32 stream
resumptions). A batch that exhausts the connection-step budget marks the slot
resumable, so queued events are driven without waiting for new socket
readiness.

The loop enforces the H2 core deadlines from `http2::deadline`, the TLS
handshake deadline (`prefaceTimeoutMs`), and the server drain deadline. A TLS
handshake completion drives the new H2 connection once so a preface already
buffered inside the TLS layer is not stranded.

`runOnce` performs one accept batch, one reactor wait, the resulting slot
steps, deadline checks and bounded resumable work. It automatically calls
`beginShutdown` after an action reports `Stop`. `run` loops `runOnce` up to
`maxTurns`; `drain` drives live connections after `beginShutdown` until they
finish or `drainTimeoutMs` elapses. `shutdown` is the final close: it removes
watches, closes every stream shell, runs a bounded close-notify/shutdown step,
closes each H2 connection once, releases admission, and closes the TLS context,
listener and reactor.

```obo
pub type Metrics = ref record {
    pub accepted, rejected, closedConnections, requests: int
    pub streamsOpened, streamsClosed, protocolErrors, timeouts: int
    pub acceptPressure, acceptErrors, handlerErrors, watchErrors: int
    pub handshakeFailures, drains, peakActive, active: int
    pub reservedBytes: int
}
pub fn metrics(s: &Server): Metrics
pub fn active(s: &Server): int
pub fn reservedBytes(s: &Server): int
pub fn isStopping(s: &Server): bool
pub fn stopRequested(s: &Server): bool
pub fn now(): Result[httpapi::Now]
pub fn isAcceptPressure(code: int): bool
```

`metrics` is a snapshot. `active` counts live connection slots and
`reservedBytes` is the aggregate H2 reservation held by admission. Diagnostic
counters never control admission. `now()` is the same monotonic/realtime pair
the loop uses for deadlines. A normal application loop is:

```obo
let srv = try server::makeTls(listener, reactor, &limits, &options, ctx)
let h = fn (req: &httpapi::RequestMeta, at: &httpapi::Now): httpapi::Action {
    return route(req, at)
}
while !server::stopRequested(&srv) {
    let progressed = try server::runOnce(&srv, h, 64)
}
let at = try server::now()
let _ = try server::beginShutdown(&srv, &at)
let _ = try server::drain(&srv, h, 2000)
let _ = try server::shutdown(&srv, &at)
```
