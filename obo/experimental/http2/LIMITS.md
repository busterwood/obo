# `http2` limits, memory and safety envelope (H2a)

Status: experimental. The defaults below are starting points, not protocol
constants. Every value is revalidated with checked arithmetic before a native
call or allocation.

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
| `maxRequestsPerConnection` | 1000 | completed requests (admission policy) |
| `maxConnections` | 14 | server admission |
| `maxTotalConnectionBytes` | 67108864 | 64 MiB aggregate reservation |
| `maxResetBurst` / `maxResetRate` | 100 / 20 | nghttp2 reset token bucket |
| `maxPingAcks` | 100 | outstanding ACK retention |
| `maxSettingsEntries` | 32 | SETTINGS entries per peer frame |
| `frameBurst` / `frameRate` | 4000 / 2000 | inbound frame token bucket |
| `zeroDataBurst` / `zeroDataRate` | 200 / 100 | zero-length DATA bucket |
| `prefaceTimeoutMs` | 10000 | h2c preface / first frame |
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
- `maxWire >= 65536` and `maxWire >= maxReadChunk + 9`;
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
  never resolves to freed memory.
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
  reported only by a sent GOAWAY (for example DATA on stream 0) is surfaced from
  the frame-send callback.
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

## H2a gaps (deliberate)

- h2spec/h2load/Go differential and the full security/fuzz matrix are H2c.
- The test server is one-in-flight-request; multiplexed streams are covered by
  the C harness and the 32-stream cap, not by the obo test server.
- `interim(1xx)` is `Unsupported`; `101` is invalid.
- `Date` is not added automatically.
- `setTrailerNames` validates names but does not reserve a trailer block; use
  `addTrailer` before `endResponse`.
- The pure action executor owns fixed bounded bodies (Hello, Chunked, Big,
  Slow). Arbitrary streaming bodies use the concrete stream functions.
