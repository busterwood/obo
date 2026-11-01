# `http2` limits, memory and safety envelope (H2a/H2b/H2c)

Status: experimental H2a/H2b/H2c. The defaults below are starting points, not
protocol constants. Every value is revalidated with checked arithmetic before a
native call or allocation. The reactor server uses the same `Limits` record and
adds no protocol constants of its own.

## Default limits

| Limit | Default | Rule |
|---|---:|---|
| `maxConcurrentStreams` | 32 | 1..256; advertised in SETTINGS |
| `maxStreamsPerConnection` | 1000 | total opened; then a connection error |
| `maxHeaderListBytes` | 16384 | decoded name+value+32 per field, cumulative; SETTINGS advertises 4x this as a soft ceiling so the bounded callback, not nghttp2, owns the stream-level rejection |
| `maxHeaderCount` | 100 | per request header block |
| `maxHeaderFieldBytes` | 8192 | one name or value |
| `maxHeaderBlockBytes` | 81920 | compressed HEADERS+CONTINUATION budget |
| `maxContinuations` | 4 | following one HEADERS |
| `maxHeaderArena` | 1572864 | per-connection header staging |
| `maxStreamRecvWindow` | 32768 | advertised INITIAL_WINDOW_SIZE |
| `maxStreamRecvBuffer` | 32768 | must equal the window in v1 |
| `maxConnectionWindow` | 1048576 | `maxConcurrentStreams * maxStreamRecvWindow` |
| `maxStreamSendBuffer` | 32768 | per-stream response body |
| `maxWire` | 65536 | C wire ring and obo out-buffer; 64 KiB..16 MiB |
| `maxEvents` | 256 | fixed event-ring records |
| `maxBody` | 8388608 | declared/cumulative decoded request body; enforced in `requestInfo`/`readBody`/`discardBody` |
| `maxTrailerBytes` | 4096 | decoded request trailer bytes |
| `maxTrailerCount` | 32 | request trailer fields |
| `maxResponseHeaderBytes` | 16384 | staged response header block |
| `maxResponseTrailerBytes` | 4096 | staged response trailers |
| `maxResponseTrailerCount` | 32 | staged response trailer fields |
| `maxReadChunk` | 16384 | per `read`/`write` call |
| `maxRequestsPerConnection` | 1000 | later streams are reset with `REFUSED_STREAM` |
| `maxConnections` | 14 | server admission |
| `maxTotalConnectionBytes` | 67108864 | 64 MiB aggregate reservation |
| `maxResetBurst` / `maxResetRate` | 100 / 20 | nghttp2 reset token bucket |
| `maxPingAcks` | 100 | outstanding ACK retention |
| `maxSettingsEntries` | 32 | SETTINGS entries per peer frame |
| `frameBurst` / `frameRate` | 4000 / 2000 | inbound frame token bucket |
| `zeroDataBurst` / `zeroDataRate` | 200 / 100 | zero-length DATA bucket |
| `prefaceTimeoutMs` | 10000 | h2c preface; armed at accept before any frame |
| `settingsTimeoutMs` | 10000 | our SETTINGS ACK |
| `headerTimeoutMs` | 10000 | HEADERS..END_HEADERS |
| `bodyIdleTimeoutMs` | 30000 | after the first body read |
| `responseTimeoutMs` | 60000 | response progress |
| `responseTotalTimeoutMs` | 300000 | absolute response |
| `streamTotalTimeoutMs` | 300000 | from HEADERS begin |
| `idleTimeoutMs` | 60000 | no live streams |
| `drainBarrierMs` | 1000 | PING-ACK barrier after the first GOAWAY |
| `drainTimeoutMs` | 30000 | graceful drain |

## Validation identities

- every count/byte limit is positive;
- `maxStreamRecvBuffer == maxStreamRecvWindow` in v1;
- `maxConnectionWindow == maxConcurrentStreams * maxStreamRecvWindow`
  (checked multiplication);
- `maxHeaderBlockBytes >= maxHeaderListBytes >= maxHeaderFieldBytes`;
- `maxTrailerBytes <= maxHeaderListBytes`,
  `maxTrailerCount <= maxHeaderCount`;
- `maxResponseTrailerBytes <= maxResponseHeaderBytes`,
  `maxResponseTrailerCount <= maxHeaderCount`;
- `maxWire >= 65536`, `maxReadChunk` positive, and the
  `maxWire >= maxReadChunk + 9` comparison is performed with checked addition;
- `maxEvents >= 5 * maxConcurrentStreams + 16`;
- `maxHeaderArena >= maxConcurrentStreams * (maxHeaderListBytes +
  maxTrailerBytes + maxResponseHeaderBytes + maxResponseTrailerBytes)`;
- `maxTotalConnectionBytes >= maxConnections * reservation(limits)`;
- timeouts are positive and fit checked millisecond-to-nanosecond conversion.

## Reservation and memory

```
perConnectionReservation =
    maxConcurrentStreams * (maxStreamRecvBuffer + maxStreamSendBuffer)
  + 2 * maxWire
  + maxHeaderArena
  + maxEvents * 32
  + maxConcurrentStreams * (2 * maxHeaderCount + maxTrailerCount +
    maxResponseTrailerCount) * 16   (header-name/value reference records)
  + 65536 fixed overhead
```

Defaults: `32*(32768+32768) + 2*65536 + 1572864 + 8192 + 135168 + 65536` =
**4,009,984 bytes** (about 3.82 MiB). Fourteen connections reserve
**56,139,776 bytes** (about 53.5 MiB), under the platform's 64 MiB default.

The connection's `sys_reg_new_object(KIND_H2_CONNECTION)` charge is the whole
per-connection reservation. Stream entries charge zero extra bytes: their
receive ring, send ring and four staging arenas are already covered by the
connection reservation, and the C table is capped at `maxConcurrentStreams`.
nghttp2's internal heap is outside the charge and documented like OpenSSL's
heap in the TLS adapter. `Admission` counts the H2 reservation only; TLS
sessions, transport staging and other native users are outside it, so choose
`maxConnections`/`maxTotalConnectionBytes` with headroom or lower the count.

An unregistered stream (before `openStream`, or refused at `maxConcurrent`)
cannot undercount memory: the connection reservation already covers the worst
case. `platform::maxNativeBytes` and its shell/table limits are enforced by
`_adoptToken`; a failure there is `ResourceLimit`/`Busy`, never a smaller hidden
budget.

## Buffer and lifetime invariants

- `recvBuffered[s] <= maxStreamRecvBuffer`; the peer cannot exceed the
  advertised stream window.
- `sum(recvBuffered) <= maxConnectionWindow`; only application bytes are
  consumed by the shim, padding is consumed by nghttp2.
- Every received application byte is either delivered and consumed
  (`readBody`) or discarded and connection-consumed (reset/close/retire/
  adoption failure). A retirement path never loses connection window.
- `sendBuffered[s] <= maxStreamSendBuffer`; accepted bytes are sent exactly
  once or the stream is reset.
- Wire bytes live in the C ring or the obo out-buffer, never both. The C ring
  holds complete frames only; a frame that does not fit returns
  `WOULDBLOCK` without a partial copy.
- Header names/values live in bounded per-stream arenas. Request headers are
  released by `requestInfo`; trailers and response staging use separate arenas.
- The event ring holds at most `maxEvents` fixed records. Overflow is a
  connection failure, not silent loss.
- A closed stream record is freed only by the post-feed/post-send sweep after
  nghttp2 reports the close or the RST is sent. A live `KIND_H2_STREAM` token
  never resolves to freed memory. nghttp2 retains up to
  `maxConcurrentStreams` closed peer streams so RFC 9113 5.1 STREAM_CLOSED
  detection still works; that internal state is outside the H2 reservation,
  like the rest of nghttp2's heap. The shim does not advertise
  `SETTINGS_NO_RFC7540_PRIORITIES`, because doing so makes nghttp2 discard the
  closed-stream state. RFC 7540 priority frames are validated but never used
  to schedule.
- Connection close frees the session, rings and arenas once, and orphans live
  registered stream entries (their shells still close and free them).
  Unregistered streams are freed with the connection. A never-closed stream
  shell is a deliberate leak reported by the platform leak oracle.
- Orphaned stream header/trailer counts and payload accessors return `Closed`
  before touching an arena. `conn_release` clears the counts and frees the
  arenas; no accessor resolves an orphan to freed memory.
- The idle deadline is armed after a valid preface when no live streams remain,
  and again after the last stream retires. Expiry queues GOAWAY(NO_ERROR),
  emits `EventTimedOut`, and enters CLOSING; the GOAWAY remains sendable.
- A fatal shim or nghttp2 connection error sets FAILED and keeps a bounded
  send path open for the queued GOAWAY. `http2::next` flushes it before
  returning `EventProtocolError`; a connection-level nghttp2 error that is
  reported only by a sent GOAWAY (for example DATA on stream 0 or a zero
  WINDOW_UPDATE increment) is surfaced from the frame-send callback.
- Settings timeout, frame/zero-DATA flood, event-ring overflow and callback
  failures all queue GOAWAY with a flushable failure state. A preface timeout
  intentionally has no GOAWAY; `next` ignores the no-output send failure and
  returns `EventTimedOut`. A clean `terminate(NO_ERROR)` enters CLOSING and is
  not classified as a protocol error.
- `requestInfo` rejects a declared `content-length` above `maxBody`, and
  `readBody`/`discardBody` enforce the cumulative decoded body bound. A
  `Host`/`:authority` mismatch is a protocol error.

## Provenance

| Artifact | Value |
|---|---|
| runtime package | `libnghttp2-14 1.59.0-1ubuntu0.4` |
| SONAME | `libnghttp2.so.14` |
| headers | `libnghttp2-dev 1.59.0-1ubuntu0.4`, privately fetched |
| `NGHTTP2_VERSION_NUM` | `0x013b00` |
| required symbols | `nghttp2_session_server_new3`, `_mem_recv`, `_session_send`, `_submit_settings`, `_submit_response`, `_submit_trailer`, `_submit_rst_stream`, `_submit_goaway`, `_submit_shutdown_notice`, `_session_terminate_session2`, `_session_consume`, `_session_consume_connection`, `_session_resume_data`, `_session_get_last_proc_stream_id`, `_session_set_local_window_size`, `nghttp2_option_set_max_continuations`, `nghttp2_option_set_stream_reset_rate_limit` |
| link | `-l:libnghttp2.so.14` plus the existing systems adapter sources |
| hardening | full RELRO, immediate binding, non-executable stack |

No nghttp2 header is committed and no absolute header path is committed. The
test script records the extracted header version and rejects a mismatched
`libnghttp2-dev` package.

## Reactor server envelope (H2b/H2c)

| Item | Bound |
|---|---|
| connection slots | `min(maxConnections, 65536)`; the fixed table never grows past the limit |
| per-connection streams | `maxConcurrentStreams` live shells; the shim also enforces the same advertised cap |
| accept batch | 32 sockets per turn; `ResourceLimit`/EMFILE pauses 100 ms |
| per-turn connection work | 32 `http2::next` steps; exhaust marks the slot resumable for an immediate turn |
| per-turn stream resumption | 32 action steps across live streams, rotating a fair cursor; blocked writes wait for readiness/deadline |
| handshake deadline | `prefaceTimeoutMs` from accept, before any H2 session exists |
| server drain | `drainTimeoutMs` from `beginShutdown`; each connection uses the H2 GOAWAY/PING barrier and `drainBarrierMs` |
| admission | `http2::reservation(limits)` x live connections, bounded by `maxConnections` and `maxTotalConnectionBytes`; full admission removes the listener watch |
| reactor batch | 64 events per wait (systems `MAX_BATCH`) |

TLS session memory, transport staging and the platforms' connection objects are
outside the H2 reservation. The server admits on the H2 reservation only, as
the API documents; choose `maxConnections` and `maxTotalConnectionBytes` with
headroom for TLS. An accepted TLS connection that fails handshake or ALPN is
closed and its reservation released; it never becomes an H2 slot.

A connection slot is tagged by `(generation, index)`. Stale events are ignored
without dereferencing a connection. `runOnce` checks every live slot's H2
deadline after the reactor wait even when socket events dominate, and a
resumable slot forces a zero-timeout wait rather than blocking.

When the client preface is already buffered inside TLS at handshake completion,
the loop drives the new H2 connection once immediately. This avoids waiting for
a socket-readability edge that will never arrive. A wrong/no ALPN closes the
session; there is no h2 fallback. h2c has no Upgrade path.

For h2c, `h2_conn_arm_preface` initializes the C preface deadline at accept
time. Without it, a zero-byte client would not reach `check_deadlines` because
the shim only arms the deadline on the first `next_event`; all
`maxConnections` slots could be held forever. `prefaceTimeoutMs` is therefore a
strict bound from accept, not from the first byte.

When admission is full, the listener watch is removed instead of leaving a
level-triggered readable backlog. `rejected` increments once per transition.
The watch is restored when a slot closes and capacity exists. Reservation
rollback for a pre-slot failure releases the H2 admission charge without
decrementing the live connection/reserved counters.

`ResourceLimit`/EMFILE accept pressure uses the same watch pause plus a 100 ms
`acceptPausedUntil` backoff; the listener is not left readable during the
backoff. `acceptPressure` is reported in `http2server::Metrics` and H2METRICS.

A zero-count `writeBody` is a blocked write, not ready work. Such a stream
keeps `workPending` for a later retry but does not force a zero-timeout reactor
turn: `computeUntil` returns immediately only for event-backlog work or a
resumable action that can progress without new readiness. A client withholding
WINDOW_UPDATE therefore costs no CPU until a window update or deadline.

A response action that accepted zero bytes from `writeBody` sets
`actionWriteBlocked`; the server keeps the stream resumable and retries after
flushing. This is required for non-default `maxStreamSendBuffer` values.
`maxRequestsPerConnection` refusal submits `RST_STREAM(REFUSED_STREAM)` for the
unadopted stream instead of leaving it open.

Graceful drain now requires the final GOAWAY frame to be handed to the shim
wire (`drain_final_sent`) and the server flushes shim wire, obo output and TLS
ciphertext before closing. The final GOAWAY is `min(last_proc, cutoff)` and
follows the barrier PING.

`make`/`makeTls` consume the listener, reactor and optional TLS context. On a
validation failure they close what they own. `shutdown` closes every stream
shell before the connection so C never orphans a live `KIND_H2_STREAM`; it
releases admission after `http2::close`. Post-shutdown `h2::counts()` has zero
live/closing streams and `platform::counts().cleanupFailures` is zero.

## H2b/H2c gaps (deliberate)

- h2spec/h2load/Go are manual/optional integration tools; the default suite
  detects their absence and prints the pinned commands. No protocol tool is
  downloaded automatically and no peer outside loopback is contacted.
- There is no committed libFuzzer corpus. The C harness remains the native
  ASAN/UBSAN/LSAN gate; the obo server suite checks the abuse matrix and
  post-shutdown counters.
- The server is h2-only per listener. A combined H1/H2 ALPN dispatch is future
  integration work; the caller can run separate listeners now.
- `interim(1xx)` is `Unsupported`; `101` is invalid.
- `Date` is not added automatically.
- `setTrailerNames` validates names but does not reserve a trailer block; use
  `addTrailer` before `endResponse`.
- The pure action executor owns fixed bounded bodies (Hello, Chunked, Big,
  Slow). Arbitrary streaming bodies use the concrete stream functions.
