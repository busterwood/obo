# HTTP/2 server protocol layer

Status: **design for independent review; no implementation authorized.**
Baseline: current `main` `db2146cdd7fd5fe30a89bfb2a033ddba3ae9ca8e`
(TLS P2T-b, H1a0 and systems P2 merged). Date: 2026-11-01. Current obo is
`0.14`, symbol format 7, ABI 1. This note reserves no compiler, ABI,
symbol-format, package or protocol version. It designs protocol, framing and
HTTP/2 state above the approved TLS adapter and the systems stream, buffer and
reactor contracts; it does not design a new OS layer or redesign HTTP/1.1, and
does not design HTTP/3.

Related notes and code:
[tls-adapter-design.md](tls-adapter-design.md),
[http1-server-design.md](http1-server-design.md),
[systems-stdlib-design.md](systems-stdlib-design.md),
[native-c-interop-design.md](native-c-interop-design.md),
[cinterop README](../experimental/cinterop/README.md),
[cinterop skill](../experimental/cinterop/skill/SKILL.md),
[TLS P2T-b API](../experimental/tls/API.md),
[H1a0 modules](../experimental/http1/).

Reading guide: §1 scope; §2 environment evidence; §3 implementation strategy;
§4 ownership and the C boundary; §5 shared handler seam; §6 public API;
§7 state machine and flow control; §8 security and DoS; §9 tests; §10 phasing
and dependencies; §11 rejected alternatives; §12 open choices; §13 invariants
and review questions; §14 conclusion.

## 1. Decision and scope

Build a **server-side HTTP/2 origin-server protocol layer** (RFC 9113) over the
approved `systls` byte transport and `sysstream`/`sysbuffer`/`sysreactor`
systems contracts, with a **trusted C shim around the system
`libnghttp2.so.14`**. The shim owns the `nghttp2_session` and all nghttp2
callbacks, and exposes a scalar, bounded, pointer-free API. obo owns the
connection shell, per-stream shells, deadlines, admission, policy and the
handler seam. C never calls into obo.

Summary of decisions:

| Decision | Choice |
|---|---|
| Framer/HPACK provider | system nghttp2 1.59.0 (`libnghttp2.so.14`), not a new implementation |
| Build inputs | nghttp2 development headers **required**; declarations-only rejected |
| Native boundary | scalar `i64` args/results, platform byte-buffer tokens, no pointers |
| Native ownership | C owns `nghttp2_session*`; obo owns `H2Connection` and `H2Stream` shells |
| Event delivery | bounded C event ring of fixed records; obo polls; no callback into obo |
| Header/body payload | pulled from bounded per-stream C buffers, not queued in events |
| Transport | `httpapi::Transport` (owned bytes, explicit `Want*`, flush, shutdown), shared with H1 |
| ALPN | `h2` required over TLS; `http/1.1` is a separate dispatch; no downgrade |
| h2c | opt-in prior knowledge on a dedicated cleartext profile; no `Upgrade` in v1 |
| Push | none in v1; advertised disabled |
| Priority | RFC 7540 priority ignored; RFC 9218 signalled and not scheduled |
| Client mode | none; server sessions only |
| Phase | H2a/H2b/H2c after systems P2, cinterop P2T-a and TLS P2T-b |

### 1.1 Scope

- Server-side HTTP/2 over TLS with ALPN `h2` (RFC 9113, RFC 7301).
- Optional cleartext HTTP/2 with prior knowledge on a separately configured
  listener; the client connection preface is validated before any frame.
- Connection preface, SETTINGS exchange and ACK, stream lifecycle, HEADERS,
  CONTINUATION, DATA, WINDOW_UPDATE, RST_STREAM, PING, GOAWAY, PRIORITY,
  PUSH_PROMISE rejection, padding and frame-size validation.
- Request/response header lists, pseudo-header validation, request and
  response trailers, content-length checks, `HEAD`, `204`, `304`, `1xx`.
- Stream and connection flow control with explicit backpressure, bounded
  buffers and no auto window update.
- Multiplexed streams with bounded concurrency, per-stream deadlines and
  fairness.
- Graceful drain: GOAWAY, stop accepting new streams, finish existing streams,
  deadline, close.
- A handler seam shared with HTTP/1.1: `RequestMeta`, a single per-exchange
  `Exchange` interface (body plus response), trailers/reset capabilities, and
  an `Handler` interface. No function values in fields and no C callback.
- Deterministic tests, h2spec/h2load/curl integration, fuzz/ASAN and leak
  gates.

### 1.2 Non-goals

- HTTP/2 client mode, server push, HTTP/3, QUIC, DTLS, WebSocket/Extended
  CONNECT (RFC 8441), `CONNECT` tunneling, proxying, gateways or caches.
- HTTP/1.1-over-h2 `Upgrade`, `HTTP2-Settings`, or `101 Switching Protocols`.
  Version negotiation by Upgrade is rejected (see §11).
- A new TLS implementation, certificate policy, ALPN policy or cipher policy.
  Those remain `systls`.
- A new framer, HPACK codec, HPACK Huffman table, stream scheduler or priority
  tree. nghttp2 supplies them; this note constrains them.
- Automatic compression (`Content-Encoding`), routing, static files,
  authentication, cookies, sessions, multipart or form parsing.
- Buffering a whole request or response body in obo.
- Per-stream file descriptors, threads or reactor watches. One TCP/TLS
  connection has one reactor watch and many logical streams.
- Hidden retries, hidden timeout extension, or an automatic finalizer.
- A claim that using nghttp2 makes the server secure. It removes a large
  framing/HPACK attack surface; admission, policy and application behavior
  remain separate.

## 2. Baseline and environment evidence

### 2.1 Existing contracts used

| Module / artifact | Contract used here | Status at baseline |
|---|---|---|
| `sysplatform` | `Resource`, `requireKind`, `_tokenHandle`, `_adoptToken`, `_closeHandle`, kinds, counts, byte buffers | experimental P2, present |
| `sysstream` | `Stream`, `readSome`/`writeSome`, `shutdownWrite`, `close`, `source` | experimental P2, present |
| `sysbuffer` | fixed-capacity `Buffer`, `feed`, `consume`, `copy`, cursor steps | experimental P1a/P2, present |
| `sysclock` | `Instant`, `Duration`, `Deadline`, `after`, `remaining`, `forever` | experimental P1, present |
| `sysreactor` | level-triggered `watchResource`, `modify`, `remove`, `timer`, `wait` | experimental P2, present |
| `sysbytes` | `Bytes` shell over a C-owned bounded byte buffer; `copyIn`/`copyOut` | experimental P0, present |
| `systls` | server TLS 1.2/1.3, ALPN, `handshakeStep`, `readSome`, `writeSome`, `flush`, `shutdownStep`, `pending`, `want`, `watch`, `close` | experimental P2T-b, present; pending independent review |
| `http1core`/`http1parse` | shared HTTP/1.1 limits/header/request vocabulary and pure grammar | H1a0, present |
| `httpapi` (new) | shared `Transport`, `Header`, `RequestMeta`, `Exchange`, `Handler`, `Failure` | design, this note |
| `http2` (new) | connection/stream protocol state and the `Exchange` implementation | design, this note |
| `nghttp2` | RFC 9113 framer, HPACK, flow control, settings, stream state, h2spec-tested | system library; headers required |

The systems note's constraints apply unchanged: owned records move, references
are not stored in fields or returned, public fields are untrusted at every
entry, managed allocation failure is fatal, only configured capacity
exhaustion is a recoverable `Result`, and no C callback enters obo.

### 2.2 Installed nghttp2

Checked on the actual host:

```text
$ dpkg-query -W libnghttp2-14
libnghttp2-14:amd64  1.59.0-1ubuntu0.4

$ ls -l /lib/x86_64-linux-gnu/libnghttp2.so.14.26.0
-rw-r--r-- 1 root root 170352 Jun 30 17:44 ...
```

| Artifact | Observed value |
|---|---|
| Library path | `/lib/x86_64-linux-gnu/libnghttp2.so.14.26.0` (symlinked from `libnghttp2.so.14`) |
| SONAME | `libnghttp2.so.14`; `NEEDED` only `libc.so.6` |
| SHA-256 | `46764ab5b6ca7e353a322054f0bee45d9158a27f3e6a82a2fa4447f2ba9ba268` |
| ELF build ID | `df11ae18f11cdd49163738ef2eac9fa534f9e49b` |
| Package | `libnghttp2-14 1.59.0-1ubuntu0.4` |
| `nghttp2_version(0)` | age `1`, `version_num` `80640` (`0x013b00`), `version_str` `"1.59.0"`, `proto_str` `"h2"` |
| Symbol versioning | no `NGHTTP2_*` version nodes; all exports are global. The `libnghttp2.so.14` SONAME is the ABI gate |
| Exports used | `nghttp2_session_server_new3`, `_mem_recv`, `_session_send`, `_submit_settings`, `_submit_response`, `_submit_trailer`, `_submit_rst_stream`, `_submit_goaway`, `_submit_shutdown_notice`, `_session_terminate_session2`, `_session_consume`, `_session_resume_data`, `_session_get_last_proc_stream_id`, `_session_get_remote_settings`, `_option_*` |
| License | nghttp2 library is Expat/MIT. Other-licensed test/doc files in the upstream source tree are not linked or vendored |

The headers are **absent** at `/usr/include/nghttp2`. The matching development
package is available without root and was extracted privately for this design:

| Artifact | Observed value |
|---|---|
| Package | `libnghttp2-dev 1.59.0-1ubuntu0.4` (candidate matches the runtime) |
| `.deb` SHA-256 | `6f39f3e4fdeeeff06d4b9ef8101e8e76450d56b9f682a9a57890cb49528fae3d` |
| `nghttp2.h` | 209,554 bytes, SHA-256 `4eb610b672f7f6520250619330d16ec613c51e4f31c1418db93bfbbce58e50e9` |
| `nghttp2ver.h` | 1,605 bytes, SHA-256 `b72d511ddaf0059ad532ec83a9a5b3ec8ccc34968332a9beb016ef301b8d1187` |
| `NGHTTP2_VERSION` | `"1.59.0"` |
| `NGHTTP2_VERSION_NUM` | `0x013b00` |
| Security options present | `nghttp2_option_set_stream_reset_rate_limit` and `nghttp2_option_set_max_continuations` |
| API generation | `nghttp2_submit_response` with `nghttp2_data_provider` and `nghttp2_data_source_read_callback`; the newer `*2` symbols are **not** in this package |

**Important provenance detail.** Upstream `v1.59.0` `nghttp2.h` does not contain
`nghttp2_option_set_max_continuations`; the Ubuntu security update
`1.59.0-1ubuntu0.4` does, and the installed library exports it. A build against
an unpatched upstream 1.59.0 header will fail to compile the CONTINUATION cap.
The build manifest must therefore pin the Ubuntu package (or a later upstream
release that contains the option). This is a build gate, not a runtime feature
probe.

A bounded C probe against the extracted headers and the installed library was
not run for this note; the symbol/header/version evidence above is the design
input. The implementation gates include a compiled shim, a real TLS h2
handshake, and the test matrix in §9.

### 2.3 Tooling

| Tool | Status | Use |
|---|---|---|
| `curl 8.5.0` | present; `libcurl/8.5.0` built with `nghttp2/1.59.0`, OpenSSL 3.0.13 | `--http2`, `--http2-prior-knowledge` interop |
| `openssl 3.0.13` | present | TLS fixtures, `s_client` ALPN probes |
| `go 1.26.4` | present | differential client/server |
| `h2spec` | **absent** | pin and fetch a specific release in the integration job |
| `h2load`, `nghttpd`, `nghttp` | **absent** | optional; install `nghttp2-client`/`nghttp2-server` in the integration job |
| ASAN/UBSAN | available through the C toolchain | C shim and hosted server |

No default test downloads a tool or touches the network. h2spec and
`nghttp2-client` are explicit integration dependencies with recorded versions
and hashes; a missing tool is a skip only where the phase says so.

## 3. Implementation strategy

### 3.1 nghttp2 versus a from-scratch framer/HPACK

**Recommendation: bind the system nghttp2 server through a trusted C shim.
Do not write a framer or HPACK in obo or in a new C library.**

| Option | Verdict | Reason |
|---|---|---|
| **system nghttp2 1.59.0 via a C shim** | **Recommended** | Complete RFC 9113 framing, HPACK, flow control, settings, stream state, h2spec-tested; Expat/MIT; installed; security-patched; small trusted adapter |
| From-scratch framer + HPACK in obo | Reject | HPACK Huffman/static/dynamic tables, CONTINUATION, flow control, settings and stream state are 8k-15k lines of security-sensitive code; h2spec/CVE exposure; no schedule or review capacity |
| From-scratch framer + HPACK in a new C library | Reject | Same protocol work plus a new C ABI, fuzzing and maintenance burden; duplicates a maintained library |
| Use nghttp2's fd/`recv_callback` mode directly | Reject | The transport is already `systls` plaintext, not a socket; callbacks would need TLS I/O and would invert control |
| Let nghttp2 callbacks call obo | Reject | Unsupported by cinterop; reentrancy and lifetime hazard; no pointer may cross |
| `dlopen` nghttp2 | Reject | No link-time prototype/provenance; failure moves to run time |
| Vendor nghttp2 source | Reject for v1 | Second copy, security-update burden, symbol/interposition ambiguity; link the system library |

nghttp2 does not remove the need for this design. It supplies frame grammar,
HPACK, flow-control bookkeeping and stream state; the adapter must still
enforce admission, header/body caps, event-queue bounds, deadlines, ALPN/h2c
policy, error mapping, graceful drain and the shared handler seam.

### 3.2 Shim boundary

The shim is trusted C, reviewed like the TLS adapter, under
`obo/experimental/http2/native/`. It owns:

- `nghttp2_session*`, `nghttp2_option*`, `nghttp2_session_callbacks*`;
- the per-connection wire ring, event ring and header arena;
- the per-stream C records: request header block, response staging, receive
  ring, send ring, trailers, deadlines and counters;
- the C-side callbacks and the data provider.

obo owns the `H2Connection` and `H2Stream` shells, admission,
policy and deadlines. The cbind exposes only `i64` arguments/results and
platform byte-buffer tokens. No `nghttp2_*` pointer, `void*`, C struct by
value, function pointer or variadic argument appears in the cbind.
nghttp2's callbacks are C-internal function pointers passed to nghttp2; they
never appear in the cbind and never call obo. This is the systems design's
allowed pattern: "native callbacks, if needed by a library, remain in C and
queue bounded events for an obo polling call."

Representative C surface (names are implementation details, not a public ABI):

| C entry | Arguments | Result |
|---|---|---|
| `h2_init` | none | `1` compatible, negative failure |
| `h2_conn_new` | options packet token, packet length | connection token, negative status |
| `h2_conn_feed` | conn token, byte-buffer token, offset, count | bytes consumed, negative status |
| `h2_conn_send` | conn token, max bytes | bytes queued in the wire ring, negative status |
| `h2_conn_wire_pending` | conn token | wire bytes |
| `h2_conn_wire_read` | conn token, out buffer token, max | bytes copied, `0`, negative status |
| `h2_conn_next_event` | conn token, now nanos | event type, `0` none, negative status |
| `h2_conn_event_stream_id` | conn token | stream id of the current event |
| `h2_conn_event_flags` | conn token | flags (end stream, trailers, local) |
| `h2_conn_event_error` | conn token | HTTP/2 or library error code |
| `h2_conn_deadline` | conn token | earliest absolute monotonic deadline, or `0` |
| `h2_conn_settings_sent/acked` | conn token | `1`/`0` |
| `h2_conn_drain` | conn token | submits the first GOAWAY, `1`/negative |
| `h2_conn_goaway` | conn token, last stream id, error code | submits the final GOAWAY |
| `h2_conn_terminate` | conn token, last stream id, error code | terminates the session |
| `h2_conn_stats` | conn token, out buffer token | stats packet length |
| `h2_stream_adopt` | conn token, stream id | stream token, negative status |
| `h2_stream_begin` | stream token, status | `1`/negative |
| `h2_stream_header` | stream token, name token, value token | `1`/negative |
| `h2_stream_commit` | stream token, end stream | `1`/negative |
| `h2_stream_write` | stream token, byte-buffer token, offset, count | accepted bytes, negative status |
| `h2_stream_end` | stream token | `1`/negative |
| `h2_stream_trailer` | stream token, name token, value token | `1`/negative |
| `h2_stream_read` | stream token, out buffer token, max | bytes copied, `0` none, negative status |
| `h2_stream_info` | stream token, out packet token | packet length, negative status |
| `h2_stream_header_at` | stream token, index, out token, which | length, negative status |
| `h2_stream_trailer_at` | stream token, index, out token, which | length, negative status |
| `h2_stream_reset` | stream token, error code | `1`/negative |
| `h2_stream_close` | stream token | `1`/`0` |
| `h2_stream_state` | stream token | state enum |
| `h2_stream_windows` | stream token, out packet token | stream/connection windows |

The exact set is finalized with the implementation; the boundary rules are
fixed: scalar/token only, bounded buffers, no pointer, no callback into obo.

### 3.3 Event queue

Events are **fixed-size records**, not payload carriers:

```c
typedef struct {
  uint32_t type;        /* stream/data/end/close/goaway/settings/error/... */
  int32_t  stream_id;   /* 0 for connection events */
  uint32_t flags;       /* end-stream, trailers, local-close, ... */
  uint32_t error;       /* HTTP/2 error code or mapped library code */
  int64_t  aux;         /* deadline, last-stream-id, byte count, ... */
} h2_event;
```

A per-connection ring holds at most `maxEvents` records (default 256). Header
names/values and body bytes are **not** copied into events; the callback
stores them in bounded per-stream C buffers and the obo wrapper pulls them
with `h2_stream_header_at`, `h2_stream_trailer_at` and `h2_stream_read`. This
keeps the event ring small, makes overflow cheap to reason about, and avoids
a second copy of a potentially large header block.

Event types and obo mapping:

| C event | obo `Event` | Meaning |
|---|---|---|
| `H2_EV_STREAM_STARTED` | `EventStreamStarted { id }` | request HEADERS completed; stream open |
| `H2_EV_STREAM_DATA` | `EventStreamData { id }` | receive buffer has data (coalesced) |
| `H2_EV_STREAM_END` | `EventStreamEnd { id }` | peer END_STREAM seen |
| `H2_EV_STREAM_CLOSED` | `EventStreamClosed { id; code }` | stream closed or reset |
| `H2_EV_TRAILERS` | `EventTrailers { id }` | request trailers completed |
| `H2_EV_SETTINGS_ACKED` | `EventSettingsAcked {}` | peer ACKed our SETTINGS |
| `H2_EV_GOAWAY` | `EventGoaway { lastStreamID; code }` | peer GOAWAY |
| `H2_EV_TIMEOUT` | `EventTimedOut { id }` | id 0 = connection deadline |
| `H2_EV_PROTOCOL_ERROR` | `EventProtocolError { code }` | connection-level failure |
| `H2_EV_DRAIN_COMPLETE` | `EventDrainComplete {}` | no live streams after GOAWAY |
| `H2_EV_CLOSED` | `EventConnectionClosed {}` | terminal |

Event-queue overflow is a **connection failure**, never silent loss. A
callback that cannot enqueue a required event marks the connection failed and
returns `NGHTTP2_ERR_CALLBACK_FAILURE`; obo sees `EventProtocolError` and
closes with `ENHANCE_YOUR_CALM`. Non-essential events (PING statistics,
window-update notifications) are coalesced or counted, not queued.

### 3.4 Send path

Use `nghttp2_session_send` with a `send_callback`, **not**
`nghttp2_session_mem_send`. The callback copies a complete frame into the
per-connection wire ring only if the full frame fits; otherwise it returns
`NGHTTP2_ERR_WOULDBLOCK` without copying. The ring is at least
`maxFrameSize + 9` bytes (default 64 KiB), so a maximum frame always fits.

- `h2_conn_send` loops until `NGHTTP2_ERR_WOULDBLOCK` or no frames remain.
- `h2_conn_wire_read` copies up to `max` bytes into a `sysbytes::Bytes`; obo
  copies them into an obo `[]byte` and releases the buffer.
- obo writes that array to the transport. A partial TLS write leaves the
  unwritten suffix in an obo out-buffer, capped at `maxWire`.
- Bytes live in exactly one queue: the C wire ring or the obo out-buffer,
  never both. `h2_conn_wire_read` consumes from C; the obo copy is
  authoritative until the transport accepts it.
- The wire ring is never a frame queue and never exposes an nghttp2 internal
  pointer. `nghttp2_session_mem_send`'s pointer lifetime is not used.

### 3.5 Data provider and response body

Response headers are staged in C until the first body write or `end`, then
submitted exactly once with `nghttp2_submit_response` and a
`nghttp2_data_provider` whose read callback drains the per-stream send ring.
The callback never calls obo.

- If the send ring has bytes, copy up to the requested length and return that
  count.
- If the ring is empty and the response is not finished, return
  `NGHTTP2_ERR_DEFERRED`. obo calls `nghttp2_session_resume_data` after it
  appends more bytes.
- If the response is finished and the ring is empty, set
  `NGHTTP2_DATA_FLAG_EOF`. If response trailers are pending, also set
  `NGHTTP2_DATA_FLAG_NO_END_STREAM` and call `nghttp2_submit_trailer` from
  the callback (allowed by the 1.59 API); otherwise the DATA frame carries
  `END_STREAM`.
- `nghttp2_submit_response` copies the `nghttp2_nv` array; the shim frees the
  staging packet after the call. Do not set `NO_COPY_*` flags.
- Response header names are validated and lowercased in obo/C before submit;
  nghttp2 lowercases names again and preserves order.

`H2Stream.write` accepts only what fits in the per-stream send ring
(`maxStreamSend`, default 32 KiB). It returns `0` when full; the handler
yields and retries under the same deadline. Accepted bytes are eventually
sent, or the stream is reset; they are never duplicated or silently dropped.

### 3.6 Token and registry lifetime

- `h2_conn_new` creates a C object and registers it with the shared systems
  registry:
  `sys_reg_new_object(KIND_H2_CONNECTION, ptr, release, reservedBytes)`.
  The returned integer is adopted by obo with
  `platform::_adoptToken(KIND_H2_CONNECTION, raw)`. The token never appears in
  a public record.
- At the first `HEADERS` for a stream, C creates a stream object in the
  connection table but does **not** register it with the systems registry.
  `openStream` calls `h2_stream_adopt(connToken, id)`, which registers the
  object with
  `sys_reg_new_object(KIND_H2_STREAM, ptr, release, streamMetaBytes)` if it is
  unclaimed and returns the token; obo then calls
  `platform::_adoptToken(KIND_H2_STREAM, token)`.
- `h2_stream_adopt` is atomic in C: the first call marks the entry claimed,
  registers it and returns the token; a second call returns `AlreadyExists`;
  an unknown or closed id returns `StreamClosed`. If `_adoptToken` fails after
  a successful registration, obo calls the private `h2_stream_abandon`, which
  submits `RST_STREAM(REFUSED_STREAM)`, retires the new entry and frees the
  stream. There is no half-adopted retry. An unclaimed stream has no registry
  entry and no obo shell; it is freed by the connection close path.
- The registry entry stores kind and generation. `sys_reg_peek(token, kind)`
  validates liveness and kind before any dereference. Close poisons the
  generation before calling the release function, so a double close cannot
  free twice.
- `H2Stream` is a real shell over its own `KIND_H2_STREAM` entry:
  `H2Stream = { pub res: platform::Resource, pub id: int }`. It does not hold a
  `Resource` for the connection, because obo `ref record` assignment moves and
  a `Resource` cannot be copied into a second shell. Every stream operation validates
  its own token; C checks the stream id, state and direction.
- The C stream object's `conn` pointer is C-internal. On connection close, the
  connection release function walks its stream list, detaches each stream
  (`conn = NULL`), frees the buffers that depend on nghttp2, and leaves the
  `KIND_H2_STREAM` registry entry alive. The stream shell can still be closed;
  its release function frees the orphaned record. A stream operation on an
  orphaned entry returns `Closed`. The connection close path does not retire a
  registered stream token; it orphans it. The nghttp2 stream-close path retires
  it and invalidates the shell.
- No C object is freed inside an nghttp2 callback. `on_stream_close` marks the
  entry closed and enqueues `EventStreamClosed`; `h2_conn_feed` performs a
  post-`mem_recv` sweep that retires/frees closed entries. This avoids
  use-after-free while nghttp2 may still hold the stream's user data.
- Stream ids are never reused on a connection. After `closeStream`, the token
  is retired; a forged/stale shell fails `KIND_H2_STREAM` before any C
  dereference.
- The connection release function frees the nghttp2 session, wire ring, event
  ring and header arena. Record drop in obo frees only the obo record; it never
  calls `nghttp2_session_del`.

The reserved byte charge is explicit and conservative: the connection's
`sys_reg_new_object` charge is the whole `perConnectionReservation` (wire,
event ring, header arena, the worst-case `maxConcurrentStreams` receive/send
rings, and fixed overhead). Stream entries are identity only and charge their
small fixed metadata (or zero); their buffers are already covered by the
connection reservation. Therefore an unclaimed stream cannot undercount
memory, `nativeBytes` is honest at admission, and no per-allocation callback
is needed. `server()` checks H2's own `maxConnections`/
`maxTotalConnectionBytes` before creating the connection; exceeding it returns
`ResourceLimit`. The platform's `maxNativeBytes` is enforced by `_adoptToken`,
which also returns `ResourceLimit`/`Busy`. nghttp2's own internal heap
is outside the charge and is documented like OpenSSL's heap in the TLS note.
The server's `maxResources` must be at least `maxConnections * (1 +
maxConcurrentStreams)` (14 * 33 = 462 with the defaults), well under the 4096
default.

### 3.7 Headers versus declarations-only

**Decision: require nghttp2 development headers for the HTTP/2 build. Reject
declarations-only.**

Reasons:

- The shim uses `nghttp2_frame`, `nghttp2_nv`, `nghttp2_settings_entry`,
  `nghttp2_data_provider`, `nghttp2_data_source`, callback typedefs and the
  `nghttp2_session_callbacks_*` setter prototypes. `nghttp2_frame` is a union
  whose layout and member offsets are ABI; hand-copying it is a second,
  unreviewed ABI description.
- The callback typedefs and enum constants (`NGHTTP2_ERR_*`,
  `NGHTTP2_DATA_FLAG_*`, `NGHTTP2_SETTINGS_*`, `NGHTTP2_HCAT_*`,
  `NGHTTP2_NV_FLAG_*`) are not a small stable scalar surface. A stale value
  silently changes protocol behavior; the linker cannot catch it.
- The header package is cheap to pin and the exact package matches the
  runtime. The TLS adapter made the same decision for OpenSSL macros; nghttp2
  is less macro-heavy but more struct/typedef-heavy, so the same rule applies.
- A declarations-only **adapter** header (`h2_adapter.h`) is still correct for
  the functions the shim exports: `long h2_*(long, ...)`. The `.cbind` uses
  `headers` for `nghttp2/nghttp2.h`, the adapter header and the systems
  adapter headers, with `signature_check: header` and private `include_dirs`.
  The nghttp2 headers are trusted build input, not vendored source.

Provenance and test strategy:

- Generate a private `.cbind` with absolute `include_dirs` for the extracted
  `libnghttp2-dev` root, as the TLS tests do for `libssl-dev`. The committed
  `.cbind` has no machine-specific paths.
- Link `-l:libnghttp2.so.14` (not `-lnghttp2`, whose development symlink may
  resolve differently), plus the existing systems adapter sources. Keep full
  RELRO, immediate binding and non-executable stack from the cinterop hosted
  link. The `.cbind` `sources` include `../sys/native/sysregistry.c`,
  `sysadapter_real.c` and `sysnet_real.c` exactly as the TLS adapter does;
  there is no separate multi-spec link step in the experimental package.
- Record in the manifest: `libnghttp2-14` and `libnghttp2-dev` package
  versions, absolute library path, SHA-256, build ID, `nghttp2.h` and
  `nghttp2ver.h` hashes, `NGHTTP2_VERSION_NUM`, `signature_check: header`,
  include paths, cflags, compiler and manifest hash.
- Compile-time check: `#if NGHTTP2_VERSION_NUM < 0x013b00 #error ...` and a
  compile reference to `nghttp2_option_set_max_continuations` and
  `nghttp2_option_set_stream_reset_rate_limit`. A missing header/symbol is a
  build failure, not a runtime surprise.
- Runtime check before the first connection: `nghttp2_version(0)` has age 1,
  `version_num >= 0x013b00`, `proto_str == "h2"`. The loader's SONAME check is
  the real ABI check; do not hash the loaded library at runtime.
- Header-mode negative test: a deliberately wrong declaration (wrong arity or
  enum width) must fail the generated signature check. A modified header must
  fail `verify`.
- Do not copy nghttp2 headers into the repository. `libnghttp2-dev` is a build
  dependency.

## 4. Module layout and ownership

### 4.1 Dependency DAG

```text
httpapi  -> sysclock
http2    -> httpapi, sysplatform, sysbuffer, sysbytes, sysclock, sysreactor
httptls  -> httpapi, systls, sysreactor, sysstream
http1    -> httpapi, http1core, http1parse, http1date, sysbuffer, sysclock
http2    -/-> http1, systls
systls   -/-> httpapi, http1, http2
```

Proposed source layout, following the experimental package style:

```text
obo/experimental/httpapi/httpapi.obx     shared transport, header, request,
                                         exchange, handler, failure
obo/experimental/http2/http2.obx         H2Connection/H2Stream, limits, events,
                                         state machine, error mapping
obo/experimental/http2/native/h2_adapter.h
obo/experimental/http2/native/h2_adapter.c
obo/experimental/http2/http2_adapter.cbind
obo/experimental/http2/tests/...         C harness, obo drivers, integration
obo/experimental/httptls/httptls.obx     neutral TLS Transport adapter
```

`httpapi` is a new shared vocabulary, not a new protocol. `http1` keeps its
existing H1a0 modules; H1b/H1c aliases `http1::Transport` to
`httpapi::Transport` (or keeps a source-compatible wrapper if the compiler
does not allow the alias) and implements `httpapi::Exchange` for
`Connection`. `http1tls` becomes `httptls` or a compatibility alias. This note
does not change H1; it states the adoption path.

Unique module/basename names: `httpapi`, `http2`, `httptls`. The C shim prefix
is `h2_`; the C registry kinds are library constants.

### 4.2 Kinds and resources

Add two library kinds at implementation time:

```obo
pub const KIND_H2_CONNECTION = 25
pub const KIND_H2_STREAM = 26
```

23/24 are `KIND_TLS_CONTEXT`/`KIND_TLS_SESSION`; 25/26 are the next free
values. If another adapter claims them first, assign the next free values at
implementation. These are library constants, not kernel ABI; changing them is
a library ABI change.

`H2Connection` and `H2Stream` are shells over managed identities:

```obo
pub type Connection = ref record { pub res: platform::Resource }
pub type Stream = ref record {
    pub res: platform::Resource
    pub id: int
}
```

The public fields are not opaque. A forged `Stream` with a live token but a
wrong `id` is ignored: C uses the token's authoritative stream id, and
`streamID` is diagnostic. A forged/stale token fails `KIND_H2_STREAM` before
any C dereference. The connection shell is not reachable from the stream
shell; C keeps the connection pointer internally and detaches it on connection
close. No field is an authority.

### 4.3 Ownership and lifecycle

- `server()` creates one C session, registers `KIND_H2_CONNECTION`, adopts the
  token, and returns the shell. The server preface SETTINGS is queued but not
  necessarily written.
- `close(c)` is abortive: it retires the connection token even if the native
  close reports an error, does not close the transport (the application owns
  it, as in H1), and returns `ok(false)` on a second close. Live streams are
  orphaned, not freed: their shells remain closable and their operations return
  `Closed`.
- `drain(c)` is graceful: submit the first GOAWAY, stop accepting new
  streams, keep feeding/sending existing streams, and finish when the drain
  deadline or `EventDrainComplete` arrives. `close` is still explicit.
- `openStream(c, id)` asks C to adopt/register the stream entry exactly once and
  then adopts `KIND_H2_STREAM`. A second call for the same id returns
  `AlreadyExists`; an unknown/closed id returns `StreamClosed`; a
  draining/closed connection returns `Refused`/`Closed`. If `_adoptToken`
  fails, obo calls the private `h2_stream_abandon`, which resets and frees the
  stream; there is no half-adopted retry.
- `closeStream(s)` removes the stream record and, if the stream is still open,
  submits `RST_STREAM(CANCEL)` first. It is idempotent (`ok(false)` on a
  second close or after a peer-initiated close). It never closes the
  connection.
- Dropping a `Connection` or `Stream` record frees only the obo record. It
  never calls `nghttp2_session_del`, closes TLS or resets a stream.
- `defer` is the normal cleanup:
  ```obo
  let c = try http2::server(limits, options)
  defer http2::close(c)
  ```
  Register the connection defer first so it runs last; close streams before
  the connection on the normal path. `exit` runs no defers.
- A stream still live when the connection closes and whose shell is never
  closed is a live `KIND_H2_STREAM` leak; the connection orphans it and the
  leak oracle reports it. This is deliberate: the connection cannot free a
  shell owned by the caller. A stream already closed by nghttp2 was retired by
  the post-feed sweep, so it does not leak.

### 4.4 Connection state table

| State | `next` | `openStream` | `drain` | `close` |
|---|---|---|---|---|
| `AwaitPreface` | validates preface; no frame parse | `NotReady` | allowed, queues GOAWAY | `ok(true)` |
| `AwaitSettings` | sends SETTINGS, parses frames | allowed after first HEADERS | allowed | `ok(true)` |
| `Open` | feed/send/events | allowed | -> `Draining` | `ok(true)` |
| `Draining` | feed/send existing streams; new streams refused | allowed at/below cutoff; `Refused` above | idempotent | `ok(true)` |
| `Closing` | flush only | `Closed` | `Closed` | `ok(true)` |
| `Closed` | `Closed` | `Closed` | `Closed` | `ok(false)` |
| `Failed` | `Closed` | `Closed` | `Closed` | `ok(true)` once |

For TLS, `AwaitPreface` is still entered: the client connection preface is
required over TLS too. The server may send SETTINGS immediately because ALPN
already selected h2; for h2c it waits for the preface before sending SETTINGS.
`next` performs at most one transport read, one transport write and one
`flushPending` per call, and returns at most one event. The application loop
drains at most 32 events per turn; it never loops on TLS readiness or hides a
retry.

### 4.5 Limits

`H2Limits` is deep-copied and validated at `server()`. The shared seam defines
`httpapi::CommonLimits`; H2 repeats the shared cap fields deliberately so H1
can keep its existing `Limits` until H1b adopts the common record. H2 adds
protocol limits.

| Limit | Default | Rule |
|---|---|---|
| `maxConcurrentStreams` | 32 | 1..256; advertised in SETTINGS |
| `maxStreamsPerConnection` | 1000 | total opened; then GOAWAY(ENHANCE_YOUR_CALM) |
| `maxHeaderListBytes` | 16384 | decoded name+value+32 per field, cumulative |
| `maxHeaderCount` | 100 | per header block |
| `maxHeaderFieldBytes` | 8192 | one name or value |
| `maxHeaderBlockBytes` | 81920 | compressed HEADERS+CONTINUATION budget (5 x 16384) |
| `maxContinuations` | 4 | following one HEADERS |
| `maxHeaderArena` | 1572864 | per-connection request+response header bytes |
| `maxStreamRecvWindow` | 32768 | advertised INITIAL_WINDOW_SIZE |
| `maxStreamRecvBuffer` | 32768 | must equal the window |
| `maxConnectionWindow` | 1048576 | `maxConcurrent * streamRecvWindow` |
| `maxStreamSendBuffer` | 32768 | per-stream response body |
| `maxWire` | 65536 | C wire ring and obo out-buffer |
| `maxEvents` | 256 | event ring records |
| `maxBody` | 8388608 | decoded request body bytes |
| `maxTrailerBytes` | 4096 | decoded request trailer bytes |
| `maxTrailerCount` | 32 | request trailer fields |
| `maxResponseHeaderBytes` | 16384 | staged response header block |
| `maxResponseTrailerBytes` | 4096 | staged response trailers |
| `maxResponseTrailerCount` | 32 | staged response trailers |
| `maxReadChunk` | 16384 | per `read`/`write` call |
| `maxRequestsPerConnection` | 1000 | completed requests |
| `maxConnections` | 14 | server admission |
| `maxTotalConnectionBytes` | 67108864 | 64 MiB aggregate reservation (platform default) |
| `maxResetBurst` | 100 | rapid-reset token bucket |
| `maxResetRate` | 20 | tokens/second |
| `maxPingAcks` | 100 | outstanding ACKs |
| `maxSettingsEntries` | 32 | per SETTINGS frame |
| `prefaceTimeoutMs` | 10000 | h2c preface / first frame |
| `settingsTimeoutMs` | 10000 | our SETTINGS ACK |
| `headerTimeoutMs` | 10000 | HEADERS..END_HEADERS |
| `bodyIdleTimeoutMs` | 30000 | per stream, after first body read |
| `responseTimeoutMs` | 60000 | progress |
| `responseTotalTimeoutMs` | 300000 | absolute |
| `streamTotalTimeoutMs` | 300000 | from HEADERS begin |
| `idleTimeoutMs` | 60000 | no live streams |
| `drainTimeoutMs` | 30000 | GOAWAY drain |

`validateLimits` checks: every count/byte limit positive; `streamRecvBuffer >=
streamRecvWindow` and `streamRecvBuffer == streamRecvWindow` for v1;
`connectionWindow == maxConcurrentStreams * streamRecvWindow` (checked
multiplication, no overflow); `maxHeaderBlockBytes >= maxHeaderListBytes`;
`maxHeaderListBytes >= maxHeaderFieldBytes`; `maxWire >= 65536`;
`maxTrailerBytes <= maxHeaderListBytes`; `maxTrailerCount <= maxHeaderCount`;
`maxHeaderArena >= maxHeaderListBytes + maxTrailerBytes +
maxResponseHeaderBytes + maxResponseTrailerBytes`;
`maxTotalConnectionBytes >= maxConnections * perConnectionReservation`;
`maxEvents >= 3 * maxConcurrentStreams + 16`; timeout values fit checked
millisecond-to-nanosecond conversion. A reservation below the minimum is
`ResourceLimit`/`InvalidArgument`, never a wrapped smaller budget.
`perConnectionReservation = maxConcurrent*(streamRecvBuffer +
streamSendBuffer) + 2*maxWire + maxHeaderArena + maxEvents*32 +
fixedOverhead`. Header staging is drawn from `maxHeaderArena`, not counted a
second time per stream. Defaults:
`32*(32768+32768) + 2*65536 + 1572864 + 8192 + 65536` =
`2,097,152 + 1,777,664` = `3,874,816` bytes, about 3.70 MiB.
`14 * 3,874,816 = 54,247,424` bytes, about 51.7 MiB, under the 64 MiB
aggregate. A 20-connection configuration would be 73.9 MiB and would
require raising `platform::Options.maxNativeBytes` to at least 96 MiB
(plus other native users); the default therefore stays within the 64 MiB
platform default. An implementation may lower per-stream buffers and raise
the connection count only when the platform budget is raised; the checked
product is the gate.

These are starting points, not protocol constants. They must be validated by
the memory tests in §9; raising `maxConcurrent` or `maxConnections` without
raising the aggregate budget must fail validation.

## 5. Shared handler seam with HTTP/1.1

### 5.1 What is shared and what is not

Shared:

- `Header` (ordered, duplicates preserved, case-insensitive lookup).
- `RequestMeta` (method, target, path, query, scheme, authority, version,
  headers, secure, body length).
- `Exchange`: one per-request exchange object with body read, response write, trailers,
  interim and reset operations.
- `Handler`: an interface with `handle(req, ex)`.
- `Failure` categories and `BodyRead`.
- `Transport`: byte I/O, `Want*`, flush, shutdown, ALPN.

Not shared, and not pretended identical:

| Aspect | HTTP/1.1 | HTTP/2 |
|---|---|---|
| Concurrency | one request in flight | up to `maxConcurrentStreams` |
| Framing | Content-Length/chunked | DATA + END_STREAM |
| Header block | text, repeated | HPACK, pseudo-headers |
| Flow control | TCP backpressure | stream and connection windows |
| Request end | CL/chunk terminator | END_STREAM |
| Trailers | chunked only | HEADERS after DATA |
| Reset | close the connection | RST_STREAM per stream |
| Shutdown | `Connection: close` | GOAWAY |
| Reason phrase | yes | no |
| Priority | n/a | ignored |
| 1xx | 100 only | 100-199 HEADERS |
| Full duplex | response after body read/drain (or close) | request body may still arrive after response start; `read` remains valid until close |
| Error response | status line + body | `:status` + DATA, or RST/GOAWAY |

The seam abstracts the request/response vocabulary, not the connection model.
A handler that needs `streamID`, multiplexing or per-stream reset checks
`RequestMeta.streamID`/`Exchange.capabilities()`; it must not assume H1
keep-alive semantics. In particular, H2 may deliver request body bytes after
the response has started (full duplex), while H1 forbids `readBody` after a
final response. A shared handler should read/drain the body before responding
unless it explicitly branches on `RequestMeta.protocol`.

### 5.2 Interfaces

```obo
module httpapi
import "sys/sysclock.obx" as clock
import "sys/sysreactor.obx" as reactor

pub type Now = ref record {
    pub mono: clock::Instant
    pub wall: clock::Timestamp
}

pub type CommonLimits = ref record {
    pub maxBody: int
    pub maxResponseBuffer: int
    pub maxReadChunk: int
    pub maxHeaderListBytes: int
    pub maxHeaderCount: int
}

pub type Protocol = closed {
    ProtocolHttp11 {}
    ProtocolHttp2 {}
}

pub type Header = ref record { pub name: string; pub value: string }

pub type RequestMeta = ref record {
    pub protocol: Protocol
    pub streamID: int              // 0 for HTTP/1.1
    pub method: string
    pub target: string             // raw target / :path, no decoding
    pub path: string               // origin-form path, no normalization
    pub query: string              // without '?'; empty if absent
    pub scheme: string             // "" if absent
    pub authority: string          // "" if absent
    pub versionMajor: int          // 1/1 or 2/0
    pub versionMinor: int
    pub headers: []Header          // ordered; pseudo-headers excluded
    pub secure: bool
    pub hasBody: bool
    pub hasContentLength: bool
    pub contentLength: int
}

pub type Capabilities = record {
    pub multiplexed: bool
    pub trailers: bool
    pub reset: bool
    pub interim: bool
    pub push: bool
    pub reasonPhrase: bool
}

pub type BodyRead = closed {
    BodyData { bytes: []byte }
    BodyEnd {}
    BodyNeedRead {}
}

pub type Failure = closed {
    InvalidArgument {}
    Range {}
    InvalidResource {}
    Closed {}
    NotReady {}
    InvalidState {}
    BadPreface {}
    BadFrame {}
    FrameSize {}
    Protocol {}
    Compression {}
    HeaderList {}
    ContinuationFlood {}
    Flood {}
    Refused {}
    StreamClosed {}
    Reset {}
    Goaway {}
    FlowControl {}
    SettingsTimeout {}
    Timeout {}
    ResourceLimit {}
    Busy {}
    Unsupported {}
    Internal {}
    Transport { sysCode: int }
    Tls { tlsCode: int }
}

pub fn failure(code: int): Failure
pub fn text(code: int): string

pub type Exchange = interface {
    fn capabilities(): Capabilities

    mut fn read(max: int): Result[BodyRead]
    mut fn discard(max: int): Result[bool]

    fn trailerCount(): int                  // request trailers; valid after BodyEnd
    fn trailerAt(index: int): Result[Header] // request trailers

    mut fn begin(status: int): Result[bool]
    mut fn header(name: string, value: string): Result[bool]
    mut fn setLength(length: int): Result[bool]
    mut fn write(bytes: &[]byte, offset: int, count: int): Result[int]
    mut fn interim(status: int): Result[bool]
    mut fn declareTrailers(names: []string): Result[bool]
    mut fn trailer(name: string, value: string): Result[bool]
    mut fn end(): Result[bool]
    mut fn reset(code: int): Result[bool]
}

pub type Handler = interface {
    mut fn handle(req: &RequestMeta, mut ex: &Exchange): Result[bool]
}
```

The handler is an interface, not a function value. obo forbids function types
whose parameters contain interfaces and forbids storing function values in
fields; an interface value is allowed in a field and can be implemented by an
application router. The protocol layer never calls the handler; the
application loop does.

`httpapi::Transport` is the H1 transport contract moved to a neutral module.
The outcome types are moved with it; variant names stay unique:

```obo
pub type Want = closed {
    WantNone {}
    WantRead {}
    WantWrite {}
    WantReadWrite {}
}

pub type Step = closed {
    StepDone {}
    StepWantRead {}
    StepWantWrite {}
}

pub type ReadOutcome = closed {
    ReadData { bytes: []byte }
    ReadEof {}
    ReadWantRead {}
    ReadWantWrite {}
}

pub type WriteOutcome = closed {
    WriteWrote { count: int }
    WriteWantRead {}
    WriteWantWrite {}
}

pub type ShutdownOutcome = closed {
    ShutdownDone {}
    ShutdownSent {}
    ShutdownPeerClosed {}
    ShutdownWantRead {}
    ShutdownWantWrite {}
}

pub type FlushOutcome = closed {
    Flushed {}
    FlushWantRead {}
    FlushWantWrite {}
}

pub type Alpn = closed {
    AlpnNone {}
    AlpnHttp11 {}
    AlpnH2 {}
    AlpnOther { name: string }
}

pub type Transport = interface {
    mut fn handshakeStep(): Result[Step]
    mut fn readSome(max: int): Result[ReadOutcome]
    mut fn writeSome(bytes: &[]byte, offset: int,
                     count: int): Result[WriteOutcome]
    mut fn flushPending(): Result[WriteOutcome]
    mut fn shutdownStep(): Result[ShutdownOutcome]
    fn pending(): Result[int]
    fn want(): Result[Want]
    fn alpn(): Result[Alpn]
    fn isSecure(): bool
    fn maxTransfer(): int
    fn watch(r: &reactor::Reactor, readable: bool, writable: bool,
             tag: int): Result[reactor::Watch]
    mut fn close(): Result[bool]
}
```

Unique closed-variant names (`StepDone`, `ReadData`, `WriteWrote`,
`ShutdownSent`, `WantNone`, `AlpnH2`, ...) follow the TLS/H1 precedent because
closed constructors share module scope. `httpapi` imports `sysreactor` and
`sysclock`; it does not import `http1`, `http2` or `systls`.

### 5.3 Handler obligations

1. Inspect `RequestMeta.protocol`, `streamID` and `capabilities()`.
2. Read at most `max` bytes per `read`; never assume the whole body is
   available.
3. If responding before reading the body, either `discard` within bounds or
   `reset` (H2) / close (H1). Do not leave a body unread and then reuse
   connection state.
4. Use `write` until the body is complete; `0` means backpressure. Yield and
   retry with the same cursor. Never replay accepted bytes.
5. Set `Content-Length` when known via `setLength`; do not set framing or
   connection-specific headers through `header`.
6. Declare trailers before the body; add trailers only after body writes and
   before `end`, and only when `capabilities().trailers` is true.
7. Use `end` once; `false` means retry after the loop pumps output.
8. Do not store `&RequestMeta`, `&Exchange` or `&Handler` borrows. Store owned
   copies or per-stream state keyed by `streamID`.
9. Never close the connection or stream; the loop owns lifecycle. `reset` is
   the abort operation, not close.
10. Treat `Result` errors as per-exchange failures; do not panic or exit.

### 5.4 Handler ownership and the loop

The application owns the `Handler` object (an owning interface), the
connection, the transport and per-stream state. `impl httpapi::Exchange for
http2::Stream` stays in `http2`; `impl httpapi::Exchange for
http1::Connection` stays in `http1` (H1b). A loop calls `http2::next`, then,
for each stream event, obtains a stream shell, builds `RequestMeta`, and calls
`handler.handle(&req, &stream)`. The `&Stream` coerces to `mut &Exchange` for
the duration of the call. The handler must not retain the borrow.

A per-stream handler object is also valid: the loop creates one, stores it in
its slot, and calls it on each event. The protocol layer does not own or store
handlers.

### 5.5 Why not a callback or a function value

- C callbacks into obo are unsupported and reentrancy-prone.
- Obo function types cannot take or return interface values in v1, so a
  `fn(req, body, response)` handler cannot compile with this seam.
- Function values cannot be stored in fields, so a per-connection handler
  field would be illegal.
- The `Handler` interface avoids both limits, keeps control in the
  application loop, and matches H1's "handler is application code called by
  the loop" rule.

## 6. Public API

The block below is **proposed declarations**, not a compilable module. It uses
current 0.14 field style (`pub res`) and only 0.11/Stage B features. Bodyless
`pub fn` headings become definitions.

```obo
module http2
import "httpapi.obx" as http
import "sys/sysbuffer.obx" as buffer
import "sys/sysclock.obx" as clock
import "sys/sysplatform.obx" as platform

pub const KIND_H2_CONNECTION = 25
pub const KIND_H2_STREAM = 26

pub type Mode = closed {
    ModeTls {}
    ModePriorKnowledge {}
}

pub type Options = ref record {
    pub mode: Mode
    pub serverName: string         // empty omits the Server field
}

pub type Limits = ref record {
    pub maxConcurrentStreams: int
    pub maxStreamsPerConnection: int
    pub maxHeaderListBytes: int
    pub maxHeaderCount: int
    pub maxHeaderFieldBytes: int
    pub maxHeaderBlockBytes: int
    pub maxContinuations: int
    pub maxHeaderArena: int
    pub maxStreamRecvWindow: int
    pub maxStreamRecvBuffer: int
    pub maxConnectionWindow: int
    pub maxStreamSendBuffer: int
    pub maxWire: int
    pub maxEvents: int
    pub maxBody: int
    pub maxTrailerBytes: int
    pub maxTrailerCount: int
    pub maxResponseHeaderBytes: int
    pub maxResponseTrailerBytes: int
    pub maxResponseTrailerCount: int
    pub maxReadChunk: int
    pub maxRequestsPerConnection: int
    pub maxConnections: int
    pub maxTotalConnectionBytes: int
    pub maxResetBurst: int
    pub maxResetRate: int
    pub maxPingAcks: int
    pub maxSettingsEntries: int
    pub prefaceTimeoutMs: int
    pub settingsTimeoutMs: int
    pub headerTimeoutMs: int
    pub bodyIdleTimeoutMs: int
    pub responseTimeoutMs: int
    pub responseTotalTimeoutMs: int
    pub streamTotalTimeoutMs: int
    pub idleTimeoutMs: int
    pub drainTimeoutMs: int
}

pub fn defaultLimits(): Limits
pub fn validateLimits(l: &Limits): Result[bool]
pub fn defaultOptions(): Options

pub type Connection = ref record { pub res: platform::Resource }
pub type Stream = ref record {
    pub res: platform::Resource
    pub id: int
}

pub type StreamState = closed {
    StreamOpen {}
    StreamHalfClosedRemote {}
    StreamHalfClosedLocal {}
    StreamClosed {}
}

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

pub type Counts = ref record {
    pub liveConnections: int
    pub liveStreams: int
    pub streamsOpened: int
    pub streamsClosed: int
    pub requestsCompleted: int
    pub resetsSent: int
    pub resetsReceived: int
    pub goawaysSent: int
    pub goawaysReceived: int
    pub headerBytes: int
    pub bodyBytesIn: int
    pub bodyBytesOut: int
    pub wireBytes: int
    pub flowControlBlocked: int
    pub eventQueueHighWater: int
    pub wireHighWater: int
    pub timeouts: int
    pub protocolErrors: int
    pub nativeBytes: int
    pub maxNativeBytes: int
}

pub type Stats = ref record {
    pub liveStreams: int
    pub maxConcurrentStreams: int
    pub streamsOpened: int
    pub streamsClosed: int
    pub resetsSent: int
    pub resetsReceived: int
    pub goawaysSent: int
    pub goawaysReceived: int
    pub headerBytesIn: int
    pub bodyBytesIn: int
    pub bodyBytesOut: int
    pub wireBytes: int
    pub recvWindow: int
    pub connWindow: int
    pub eventQueueHighWater: int
    pub wireHighWater: int
}

// HTTP/2 maps nghttp2 and shim failures onto the shared httpapi::Failure
// family; h2::failure/text are convenience decoders.
pub fn server(limits: &Limits, options: &Options): Result[Connection]
pub fn next(mut c: &Connection, mut t: &http::Transport,
            now: &http::Now): Result[Event]
pub fn deadline(c: &Connection): Result[clock::Deadline]
pub fn wantsRead(c: &Connection, t: &http::Transport): Result[bool]
pub fn wantsWrite(c: &Connection, t: &http::Transport): Result[bool]

pub fn openStream(c: &Connection, id: int): Result[Stream]
pub fn closeStream(s: &Stream): Result[bool]
pub fn requestInfo(s: &Stream): Result[http::RequestMeta]
pub fn headerCount(s: &Stream): Result[int]
pub fn headerAt(s: &Stream, index: int): Result[http::Header]
pub fn trailerCount(s: &Stream): Result[int]
pub fn trailerAt(s: &Stream, index: int): Result[http::Header]
pub fn streamState(s: &Stream): Result[StreamState]
pub fn streamID(s: &Stream): int
pub fn reset(s: &Stream, code: int): Result[bool]

pub fn readBody(s: &Stream, max: int,
                now: &http::Now): Result[http::BodyRead]
pub fn discardBody(s: &Stream, max: int,
                   now: &http::Now): Result[bool]
pub fn beginResponse(s: &Stream, status: int): Result[bool]
pub fn setHeader(s: &Stream, name: string, value: string): Result[bool]
pub fn setLength(s: &Stream, length: int): Result[bool]
pub fn writeBody(s: &Stream, bytes: &[]byte, offset: int,
                 count: int): Result[int]
pub fn interim(s: &Stream, status: int): Result[bool]
pub fn setTrailerNames(s: &Stream, names: &[]string): Result[bool]
pub fn addTrailer(s: &Stream, name: string, value: string): Result[bool]
pub fn endResponse(s: &Stream): Result[bool]

pub fn drain(c: &Connection, now: &http::Now): Result[bool]
pub fn drainComplete(c: &Connection): bool
pub fn close(c: &Connection): Result[bool]

pub fn stats(c: &Connection): Result[Stats]
pub fn counts(): Result[Counts]
pub fn failure(code: int): http::Failure
pub fn text(code: int): string
```

### 6.1 Types and validation

- `Limits` is deep-copied; the caller's later mutation does not change the
  connection. `validateLimits` checks all fields, including the aggregate
  reservation and checked multiplication.
- `Options.mode` selects TLS or prior-knowledge cleartext. `ModeTls` requires
  `t.alpn() == "h2"` before any frame; there is no no-ALPN fallback for h2.
  `ModePriorKnowledge` is only valid for a cleartext transport; a secure
  transport with this mode returns `Unsupported`.
- `serverName` is a bounded token (<=64 bytes, no CR/LF/NUL); empty omits the
  `Server` field. It is not a version string.
- `openStream` requires `id > 0` and odd (server receives client-initiated
  streams). It asks C to adopt/register the stream entry exactly once and then
  adopts `KIND_H2_STREAM`; a second call returns `AlreadyExists`, an unknown or
  already-closed id returns `StreamClosed`. The C entry is created at the
  first HEADERS, so the application calls `openStream` on
  `EventStreamStarted` and keeps the shell. It allocates one managed identity
  and one C stream record, not an OS resource. If the platform registry is
  exhausted, `openStream` returns `ResourceLimit`; C submits
  `RST_STREAM(REFUSED_STREAM)` and removes the stream, so the application can
  ignore it. If `_adoptToken` fails for another reason, obo calls the private
  `h2_stream_abandon`, which resets and frees the stream; there is no
  half-adopted retry.
- `requestInfo` copies the whole request header block into an owned
  `RequestMeta` and releases the C request-header copy; call it once. Use
  `headerAt` for lazy access before `requestInfo`; after `requestInfo`, use
  the `RequestMeta` copy. Request trailers remain in C until `BodyEnd` and are
  copied by `trailerAt`. After `requestInfo`, `headerCount`/`headerAt` return
  `InvalidState`; they are for the lazy path before the snapshot.
- `streamState` is the RFC 9113 state, not the internal response state.
- `EventStreamStarted` is returned exactly once per stream. `EventStreamData`
  is coalesced: at most one queued per stream until the application reads.
  `EventStreamEnd` is returned once. `EventStreamClosed` is returned once.
- `next` returns at most one event per call. The application calls it again
  to drain queued events. A `NeedRead`/`NeedWrite` event is not terminal.
- `closeStream` is idempotent; after it, every stream operation returns
  `Closed`. A peer-initiated close retires the token first, so the app's
  `closeStream` returns `ok(false)` and every stream operation returns
  `Closed`/`InvalidResource`. `close` retires the connection token; live
  stream shells are orphaned and still closable.
- `drain` is idempotent. After it, `openStream` returns `Refused` for new ids;
  existing streams continue. `drainComplete` is true only when no live streams
  remain and the final GOAWAY is queued.
- `stats`/`counts` are diagnostic, not authority. Counters never control
  admission; they are sampled after the operation.

### 6.2 Representative handler

```obo
type Router = ref record {
    bodyBytes: []byte
    bodyPos: int
    bodyDone: bool
    responseStarted: bool
    finished: bool
}

fn (mut r: &Router) handle(req: &httpapi::RequestMeta,
                           mut ex: &httpapi::Exchange): Result[bool] {
    if !r.responseStarted {
        let ct = "Content-Type"
        let cv = "text/plain; charset=utf-8"
        try ex.header(ct, cv)
        try ex.begin(200)
        r.responseStarted = true
    }
    while !r.finished {
        // Flush any accepted-but-unwritten bytes before reading more.
        if r.bodyPos < len(r.bodyBytes) {
            let n = try ex.write(&r.bodyBytes, r.bodyPos,
                                 len(r.bodyBytes) - r.bodyPos)
            if n == 0 { return ok(false) }
            r.bodyPos += n
            if r.bodyPos < len(r.bodyBytes) { return ok(true) }
        }
        if !r.bodyDone {
            let read = try ex.read(16384)
            match read {
                BodyData {
                    r.bodyBytes = read.bytes
                    r.bodyPos = 0
                }
                BodyNeedRead { return ok(false) }
                BodyEnd { r.bodyDone = true }
            }
        } else {
            let done = try ex.end()
            if done { r.finished = true } else { return ok(false) }
        }
    }
    return ok(true)
}

impl httpapi::Handler for Router
```

The sketch is resumable: it keeps per-stream state in the handler object,
never replays a status/header/body byte, and yields on `BodyNeedRead`, a zero
write, or an `end` that did not fit. A real multiplexed server keeps one
`Router`-like object per stream (or keys state by `req.streamID`) and repeats
`handle` on each event. The protocol layer does not call this function.

## 7. State machine

### 7.1 Connection

```text
AwaitPreface -> AwaitSettings -> Open -> Draining -> Closing -> Closed
                     |             |
                     +-> Failed <--+
```

| State | Entry | Exit |
|---|---|---|
| `AwaitPreface` | `server()` before the client preface (h2c, or TLS h2) | 24-byte preface valid -> `AwaitSettings`; mismatch/timeout -> `Closing`/`Failed` |
| `AwaitSettings` | preface valid | our SETTINGS sent; first HEADERS -> `Open`; SETTINGS ACK deadline armed |
| `Open` | first HEADERS or SETTINGS exchange | `drain` -> `Draining`; fatal frame/session error -> `Failed`; idle timeout -> `Closing` |
| `Draining` | `drain()`/GOAWAY | no live streams -> `EventDrainComplete` -> `Closing`; deadline -> `Closing` |
| `Closing` | close policy/error | transport close -> `Closed` |
| `Closed` | `close` | terminal |
| `Failed` | fatal protocol/library error | `close` -> `Closed` |

For TLS, the dispatcher has already completed `systls::handshakeStep` and
verified `alpn == "h2"`. `http2::next` does not drive TLS handshakes. For h2c,
`http2::next` validates the preface before feeding any byte to nghttp2.

### 7.2 Stream

RFC 9113 §5.1 states (server side, peer-initiated odd streams):

| State | Entry | Allowed receive | Local action |
|---|---|---|---|
| `Open` | HEADERS without END_STREAM | HEADERS (trailers), DATA, RST, WINDOW_UPDATE, PRIORITY | send HEADERS/DATA/trailers/RST |
| `HalfClosedRemote` | END_STREAM on HEADERS or DATA | RST, WINDOW_UPDATE, PRIORITY | send response until END_STREAM |
| `HalfClosedLocal` | local END_STREAM sent | DATA/HEADERS from peer until END_STREAM | no more local DATA |
| `Closed` | RST either side, or both END_STREAM | none (stream error) | none; remove after close |

Internal response state:

```text
RespNone -> RespHeadersStaged -> RespHeadersSent -> RespBody
         -> RespTrailersPending -> RespDone
         -> RespReset
```

Invariants:

1. `RespNone` is the only state where `begin` is valid; `begin` is once.
2. Headers commit on the first `write` or `end`; after commit, `header`,
   `setLength` and `declareTrailers` are `InvalidState`.
3. `write` is invalid before `begin` or after `end`/`reset`.
4. `end` is once; `false` means output did not fit, retry after pumping.
5. Trailers are invalid after END_STREAM; response trailers are submitted
   only after the last DATA with `NO_END_STREAM`.
6. Request trailers are exposed only after `BodyEnd`; they never precede DATA.
7. `HEAD`, `1xx`, `204`, `205`, `304` never put DATA on the wire. `write`
   accepts and discards the bytes for `HEAD`; for `204`/`205`/`304` it is
   `InvalidState` or accepted-and-discarded per the chosen H1-compatible rule
   (recommend accept-and-discard, and validate `content-length`).
8. `101` is invalid in h2 (`InvalidArgument`); no protocol switch.
9. `begin` captures `Date` once with `sysclock::realtime`; a non-empty
   `Options.serverName` adds `Server`. Both are protocol-owned and never echo
   request data.

### 7.3 Frame handling

| Frame | Action |
|---|---|
| `HEADERS` | `on_begin_headers` allocates/gets stream, starts header block; `on_header` copies and enforces caps; `on_frame_recv` finalises and enqueues `Started`/`Trailers`; END_STREAM -> `StreamEnd` |
| `CONTINUATION` | internal to nghttp2; `maxContinuations`, header block/time cap; any other frame interleaved is a connection error |
| `DATA` | `on_data_chunk_recv` copies app bytes into the stream receive ring; END_STREAM on `on_frame_recv` -> `StreamEnd`; padding is consumed by nghttp2 |
| `WINDOW_UPDATE` | nghttp2 validates increment/overflow and updates windows; may unblock the send ring; count, no app event |
| `RST_STREAM` | nghttp2 validates stream state; `on_stream_close` enqueues `StreamClosed(code)`; no more data |
| `SETTINGS` | nghttp2 validates/applies; ACK is automatic; peer ACK surfaces `SettingsAcked`; invalid size/id -> connection error |
| `PING` | nghttp2 auto-ACKs (unless disabled); `maxPingAcks`/`maxOutboundAck` bounds; count, no app event |
| `GOAWAY` | peer drain; surface `Goaway(lastStreamID, code)`; no new local streams; existing streams may finish |
| `PRIORITY` | parsed and ignored; no scheduling tree; `SETTINGS_NO_RFC7540_PRIORITIES=1` |
| `PUSH_PROMISE` | server receives one -> connection error PROTOCOL_ERROR; server never sends one |
| unknown/ext | rejected by nghttp2 with the RFC error; surface `BadFrame`/`Protocol` |

### 7.4 HEADERS and CONTINUATION

- `on_begin_headers` distinguishes `NGHTTP2_HCAT_REQUEST` (request headers)
  from `NGHTTP2_HCAT_HEADERS` (trailers) on the server.
- Header names/values are copied immediately; nghttp2's pointers are valid
  only during the callback. Copy lengths are bounded; no `strlen`.
- Header list size uses `nameLen + valueLen + 32` per field. Exceeding
  `maxHeaderFieldBytes`, `maxHeaderCount` or `maxHeaderListBytes` submits
  `RST_STREAM(ENHANCE_YOUR_CALM)` and returns
  `NGHTTP2_ERR_TEMPORAL_CALLBACK_FAILURE`. The stream is reset, not the
  connection, for a header-list overflow.
- Exceeding `maxContinuations` or `maxHeaderBlockBytes` is a connection
  error; let nghttp2's queued GOAWAY stand; map to `ContinuationFlood`/
  `Compression`. Do not submit a second GOAWAY.
- Empty header names, uppercase names, invalid characters, pseudo-header
  order/duplicates and connection-specific fields are rejected by nghttp2's
  HTTP messaging layer before the callback; the shim also rejects them as
  defense in depth.
- Required pseudo-headers: `:method`, `:scheme`, `:path` (except CONNECT,
  which is rejected in v1); `:authority` is optional but recommended.
  `:authority` and `Host` mismatch -> `400`/stream reset. Derive authority
  from `:authority`, else `Host`, else empty; enforce a configured
  `requireAuthority` for origin-form requests.
- `:path` is the raw target. Do not percent-decode, normalize or reject `..`
  here; that is routing policy. `*` is allowed for OPTIONS.
- `TE` may be present only with value `trailers`; `Connection`, `Keep-Alive`,
  `Proxy-Connection`, `Transfer-Encoding` and `Upgrade` are forbidden.
- `content-length` must equal the sum of DATA; a mismatch is a stream error
  (`PROTOCOL_ERROR`). nghttp2's HTTP messaging checks this; the H2 layer also
  checks at `BodyEnd` and before committing a response.
- Request `content-length` greater than `maxBody` is rejected before reading
  with `413` if headers are complete; otherwise reset/close.
- Multiple `Cookie` fields are preserved; a convenience join uses `"; "`.
  Duplicate `Host`/`:authority` is rejected.

### 7.5 SETTINGS

Server's initial SETTINGS:

| Setting | Value | Reason |
|---|---|---|
| `HEADER_TABLE_SIZE` | 4096 | bounded HPACK decoder table |
| `ENABLE_PUSH` | 0 | no push |
| `MAX_CONCURRENT_STREAMS` | 32 | admission |
| `INITIAL_WINDOW_SIZE` | 32768 | per-stream receive window |
| `MAX_FRAME_SIZE` | 16384 | default; do not raise |
| `MAX_HEADER_LIST_SIZE` | 16384 | advisory + enforced |
| `NO_RFC7540_PRIORITIES` | 1 | priority ignored (RFC 9218 signal) |

- The server sends SETTINGS as its first frame. It may send before receiving
  the client preface over TLS; for h2c, after the preface is validated.
- `nghttp2_option_set_no_recv_client_magic(option, 1)` is set because obo
  validates the 24-byte client preface itself and then feeds only frames.
  nghttp2 never parses the magic, and a non-preface byte sequence is rejected
  with `BadPreface` before any frame state exists.
- The client's SETTINGS is ACKed by nghttp2 automatically. The client's ACK
  of our SETTINGS must arrive before `settingsTimeoutMs`; otherwise
  `GOAWAY(SETTINGS_TIMEOUT)` and close.
- Peer `INITIAL_WINDOW_SIZE` and `MAX_FRAME_SIZE` govern outbound DATA;
  nghttp2 enforces. A peer `MAX_FRAME_SIZE` above 16384 is accepted up to
  16777215, but the shim's wire ring is sized for the maximum.
- `nghttp2_option_set_max_settings(option, maxSettingsEntries)` bounds the
  number of entries per peer SETTINGS frame; `max_outbound_ack` bounds
  ACK/PING ACK retention; `ERR_FLOODED` maps to `Flood`.
- `nghttp2_option_set_no_closed_streams(option, 1)` lets nghttp2 discard its
  closed-stream state; our C stream entry is the shell's authority and is
  retired by the post-feed sweep.

### 7.6 Flow control and backpressure

- Disable automatic window update:
  `nghttp2_option_set_no_auto_window_update(option, 1)`.
- Advertise `INITIAL_WINDOW_SIZE = maxStreamRecvWindow`; set the connection
  window with
  `nghttp2_session_set_local_window_size(session, 0, 0, maxConnectionWindow)`.
- Set `maxConnectionWindow = maxConcurrentStreams * maxStreamRecvWindow`
  (checked). A single stalled stream can hold at most its stream window, so
  it cannot exhaust the connection window and starve the others. All streams
  stalled means the connection window is exactly the sum of their buffers,
  which is the fair worst case.
- `on_data_chunk_recv` copies into the stream receive ring. The ring capacity
  equals the advertised stream window, so it cannot overflow: the peer cannot
  send more than the window, and the shim consumes only after bytes leave the
  ring.
- `h2_stream_read` copies bytes out and then calls
  `nghttp2_session_consume(session, stream_id, n)`. The wrapper copies from
  the platform buffer into an obo `[]byte`; if that copy fails, reset the
  stream (no silent data loss and no stuck window).
- Padding is consumed by nghttp2 immediately (verified in the 1.59 inbound
  DATA path: it calls `nghttp2_session_consume` for `readlen - data_readlen`).
  The shim consumes only application bytes; padding never accumulates.
- `h2_stream_write` accepts only free space in the send ring. `0` means full;
  the handler yields. A full ring caused by a zero peer window is cleared by
  inbound WINDOW_UPDATE, which wakes the read watch; no separate timer is
  required.
- `maxConnectionWindow` is an absolute cap; a peer that never reads is
  eventually blocked by its own receive window, and the stream/response
  deadlines close it.
- No auto window update in the shim; no hidden unread buffering; no whole-body
  allocation.

### 7.7 RST, GOAWAY, PING, WINDOW_UPDATE

- `reset(s, code)` submits `RST_STREAM(code)` and marks the local side
  closed. The peer's `on_stream_close` is ignored after the C entry is
  removed. `CANCEL` is the application abort; `REFUSED_STREAM` for admission;
  `ENHANCE_YOUR_CALM` for abuse; `INTERNAL_ERROR` for handler failure;
  `PROTOCOL_ERROR` for malformed stream state.
- Peer RST -> `EventStreamClosed(code)`; all subsequent reads return
  `StreamClosed`; the C entry is removed.
- `drain(c)` calls `nghttp2_submit_shutdown_notice` (GOAWAY with
  `last_stream_id = (1<<31)-1`), enters `Draining`, records
  `drainCutoff = nghttp2_session_get_last_proc_stream_id`, and refuses new
  stream ids above it (RST_STREAM(REFUSED_STREAM)) until the final GOAWAY. A
  racing stream at or below the cutoff may still be processed. After one round
  trip or when the live stream count reaches zero, it calls
  `nghttp2_submit_goaway(nghttp2_session_get_last_proc_stream_id, NO_ERROR)`
  (never above the cutoff). Existing streams continue until
  `EventDrainComplete` or `drainTimeoutMs`, then `close`.
- A fatal local error uses `nghttp2_session_terminate_session2(last_proc,
  code)`: it queues GOAWAY and stops read/write. obo flushes the wire, then
  closes. H2 does not own TLS shutdown: after `drain`/`close`, the application
  drives `t.shutdownStep` best-effort to `ShutdownDone`/`ShutdownPeerClosed`
  or a deadline, then `t.close`. `close(c)` is not a clean TLS close promise.
- PING is auto-ACKed. A PING/SETTINGS ACK flood is bounded by
  `maxOutboundAck`/`maxSettingsEntries`; `ERR_FLOODED` -> `GOAWAY(
  ENHANCE_YOUR_CALM)`.
- WINDOW_UPDATE increment 0 -> `PROTOCOL_ERROR`; overflow ->
  `FLOW_CONTROL_ERROR`; nghttp2 validates. The shim counts window updates and
  exposes current windows for diagnostics.

### 7.8 Padded frames, frame size and priority

- Padded HEADERS/DATA are accepted. nghttp2 strips padding, includes it in
  flow-control accounting, and consumes it immediately; the shim never
  exposes padding bytes. A padding-only DATA frame delivers zero application
  bytes and does not stall the window.
- `MAX_FRAME_SIZE` is 16384. Inbound frames larger than our advertised size
  are `FRAME_SIZE_ERROR` (nghttp2). Outbound frames respect the peer's
  advertised size, but nghttp2's data-source read length defaults to 16 KiB
  and this shim does not install a callback that raises it. Our DATA frames
  therefore stay at or below 16 KiB; a large HEADERS block is split into
  HEADERS+CONTINUATION frames by nghttp2. `maxWire=65536` is at least
  `maxReadChunk + 9`; if a future configuration raises `maxReadChunk` above
  16384, `validateLimits` must also require `maxWire >= maxReadChunk + 9`.
  A peer raising its own `MAX_FRAME_SIZE` does not force us to fill it.
- PRIORITY frames are parsed and ignored. `SETTINGS_NO_RFC7540_PRIORITIES=1`
  tells the peer not to rely on the RFC 7540 tree. There is no priority
  scheduler; fairness is application round-robin with a per-stream work cap
  (default 16 KiB or 8 events per turn).
- Server push is disabled. `SETTINGS_ENABLE_PUSH=0`; no `PUSH_PROMISE` is
  sent; a client `PUSH_PROMISE` is a connection error. `push()` is not in
  the API.

### 7.9 Deadlines

All deadlines are absolute monotonic nanoseconds (`sysclock::Deadline`),
stored in C as `int64` nanos plus a finite flag. C never reads a clock; obo
passes `now.mono.nanos` into `next`/checks. No deadline restarts on partial
progress, `WouldBlock`, `Interrupted` or a handler call that produced no
transport progress.

| Deadline | Active | Starts | Renewed/cleared | Default | Expiry |
|---|---|---|---|---|---|
| preface | h2c `AwaitPreface` | `server()`/accept | cleared on valid preface | 10 s | close, no GOAWAY |
| settings ACK | `AwaitSettings` | our SETTINGS sent | cleared on `SettingsAcked` | 10 s | GOAWAY(SETTINGS_TIMEOUT), close |
| header | stream `Open` before END_HEADERS | HEADERS begin | never | 10 s | RST(CANCEL) |
| body idle | after first `read` | first `read` | positive body bytes | 30 s | RST(CANCEL) |
| response idle | after headers commit | first response byte | positive wire write | 60 s | RST(CANCEL) |
| response total | after headers commit | commit | cleared on response complete | 300 s | RST(CANCEL) |
| stream total | HEADERS begin | HEADERS begin | cleared on stream close | 300 s | RST(CANCEL) |
| idle | no live streams | last stream closes | cleared on new HEADERS | 60 s | GOAWAY(NO_ERROR), close |
| drain | `Draining` | `drain()` | never | 30 s | close |

Rules:

- `deadline(c)` returns the earliest **active finite** deadline across the
  connection and live streams, or `sysclock::forever()` if none. Only finite
  deadlines create `reactor::timer`s; `forever()` is valid for
  `reactor::wait` but not for `reactor::timer`.
- `next` checks deadlines before and after I/O so a timeout is not delayed
  behind a busy batch.
- A timeout that can still send a response (header/body/response) resets the
  stream with `CANCEL`; a connection-level timeout sends GOAWAY when
  possible; a preface/handshake timeout closes silently.
- Handler computation is not billed as client slowness before the first
  `read`; after that, body idle renews only on decoded body progress.
- Response idle renews on a positive transport write, not on a handler call
  that produced no wire progress.
- Timers are checked every reactor turn even under continuous traffic.

## 8. Security and denial of service

### 8.1 Attack and control table

| Attack | Control | Result |
|---|---|---|
| Rapid reset (CVE-2023-44487) | nghttp2 server reset token bucket (`maxResetBurst=100`, `maxResetRate=20`), `maxStreamsPerConnection=1000`, reset counters | GOAWAY(ENHANCE_YOUR_CALM), close |
| CONTINUATION flood | `maxContinuations=4`, `maxHeaderBlockBytes=81920`, header deadline | nghttp2 connection error/GOAWAY, close |
| HPACK bomb | decode table 4096, encoder table cap 4096, header list 16 KiB, count 100, field 8 KiB | RST(ENHANCE_YOUR_CALM) |
| Oversized header list | same, plus header arena cap | RST/431; no unbounded allocation |
| Too many streams | advertised 32, C table cap 32 | RST(REFUSED_STREAM); no new stream record |
| Flow-control starvation | connection window = maxConcurrent x stream window, per-stream buffer = window | a stalled stream holds only its share |
| Slowloris | preface/header/body/response/stream deadlines, absolute | reset/GOAWAY/close |
| PING/SETTINGS ACK flood | `maxOutboundAck=100`, `maxSettingsEntries=32`, `ERR_FLOODED` | GOAWAY |
| Zero-length DATA / frame flood | frame accounting, stream total, max streams per connection | reset/GOAWAY |
| Priority flood | ignored; no tree; `NO_RFC7540_PRIORITIES` | no scheduling effect |
| Padded DATA abuse | nghttp2 consumes padding; frame size cap; stream window | only the sender's window shrinks |
| GOAWAY ignored | drain deadline, then close | bounded shutdown |
| ALPN confusion | TLS `Require(["h2"])` or dispatcher; no h1 parse on h2 | handshake/close |
| h2c downgrade | prior-knowledge only; no Upgrade; dedicated profile | close on non-preface |
| Request smuggling | no CL/TE framing; nghttp2 content-length/DATA check; forbidden connection headers | stream/connection error |
| Response splitting | header token/value validation in obo+C; no CR/LF/NUL; nghttp2 check | reject header |
| Error reflection | minimal plain-text body; no request echo; no HTML | safe error |
| Memory exhaustion | aggregate reservation, per-stream/connection caps, event/wire bounds | ResourceLimit/Busy |

### 8.2 Rapid reset and stream accounting

The nghttp2 reset limiter is server-side, token-bucket based: an incoming
RST_STREAM consumes a token; when empty, nghttp2 sends GOAWAY and tears down
the connection. Defaults are burst 1000, rate 33/s; this design sets
`burst=100`, `rate=20` (configurable) and additionally caps total streams per
connection at 1000. Counters `resetsReceived`, `streamsOpened` and
`streamsClosed` are exposed. A reset storm must not allocate a new stream
record, event or header block after the cap; the C table is fixed.

### 8.3 CONTINUATION and HPACK

- `maxContinuations=4` is the primary CONTINUATION-flood control. The
  Ubuntu 1.59.0 header exposes the option; an upstream 1.59.0 header does
  not, so the package pin is part of the security gate.
- `maxHeaderBlockBytes=81920` bounds compressed HEADERS+CONTINUATION bytes;
  `maxHeaderListBytes=16384`, `maxHeaderCount=100` and
  `maxHeaderFieldBytes=8192` bound decoded output. A single header field is
  copied only after its length is checked; no `strlen`.
- `SETTINGS_HEADER_TABLE_SIZE=4096` bounds the decoder's dynamic table. The
  encoder's table is capped at 4096 even if the peer advertises more
  (`nghttp2_option_set_max_deflate_dynamic_table_size`). A dynamic-table
  size update above our advertised value is a COMPRESSION_ERROR.
- Header overflow resets only the stream; a compression/continuation error
  closes the connection. This distinction is in the error mapping table.

### 8.4 ALPN and transport policy

- A dedicated h2 TLS listener configures `systls` with
  `Require(["h2"])`. A combined listener configures
  `Require(["h2","http/1.1"])` (or `Prefer` with an explicit policy) and
  dispatches on `alpn()` after `StepDone`:
  `h2` -> http2; `http/1.1` -> http1; empty -> reject for an h2-only
  listener (the combined H1 policy may treat no-ALPN as HTTP/1.1, but never
  as HTTP/2).
- No h2 byte is parsed before ALPN is checked. `http2::server(ModeTls)`
  returns `Unsupported` if `alpn()` is not `h2`; the dispatcher closes.
- Prior-knowledge h2c is a separate cleartext profile. The first 24 bytes
  must be `PRI * HTTP/2.0\r\n\r\nSM\r\n\r\n`; a mismatch closes without an
  HTTP/1.1 response. No `Upgrade`, no `HTTP2-Settings`, no `101`.
- A mixed cleartext h1/h2 listener that sniffs the preface is rejected for
  v1: version sniffing creates downgrade and smuggling ambiguity. Use
  separate ports or a TLS ALPN combined listener.
- TLS errors, certificate policy, SNI and client authentication remain
  `systls`; this layer sees only `Result` categories and `alpn`.

### 8.5 Error and status policy

- Connection-level protocol errors: GOAWAY with the RFC code
  (`PROTOCOL_ERROR`, `FRAME_SIZE_ERROR`, `COMPRESSION_ERROR`,
  `ENHANCE_YOUR_CALM`, `SETTINGS_TIMEOUT`, `INTERNAL_ERROR`), then close.
- Stream-level request errors: if the request headers are complete and the
  stream is open, send a minimal response (`400`, `408`, `413`, `431`, `500`,
  `503`) with `content-length` and plain text; then END_STREAM. If headers
  are not complete or the stream is reset, send RST_STREAM with
  `PROTOCOL_ERROR`/`CANCEL`.
- A handler error after headers are committed: RST_STREAM(INTERNAL_ERROR).
- No error body echoes the request target, headers or handler message. No
  HTML. No version or stack trace. `Server` is omitted by default.
- `101` is invalid; `CONNECT` is `501`/RST; unknown extension methods are
  `501` unless policy allows them.
- `100 Continue` is optional: `interim(100)` sends a 1xx HEADERS; it is not
  automatic in v1. H1 maps `interim(100)` to its existing `100 Continue`;
  other 1xx are `Unsupported` on H1.

### 8.6 Error mapping

| nghttp2 / shim condition | `httpapi::Failure` | Action |
|---|---|---|
| `NGHTTP2_ERR_NOMEM` | `ResourceLimit` | GOAWAY(INTERNAL_ERROR), close |
| `NGHTTP2_ERR_BAD_CLIENT_MAGIC` | `BadPreface` | close, no GOAWAY |
| `NGHTTP2_ERR_FLOODED` | `Flood` | GOAWAY(ENHANCE_YOUR_CALM) |
| `NGHTTP2_ERR_TOO_MANY_CONTINUATIONS` | `ContinuationFlood` | GOAWAY(ENHANCE_YOUR_CALM) |
| `NGHTTP2_ERR_HEADER_COMP` / invalid header block | `Compression` | GOAWAY(COMPRESSION_ERROR) |
| `NGHTTP2_ERR_FRAME_SIZE_ERROR` | `FrameSize` | GOAWAY(FRAME_SIZE_ERROR) |
| `NGHTTP2_ERR_PROTO` / invalid frame / settings expected | `Protocol`/`BadFrame` | GOAWAY(PROTOCOL_ERROR) |
| `NGHTTP2_ERR_INVALID_STREAM_ID` / state / closed | `StreamClosed`/`Protocol` | RST(PROTOCOL_ERROR) or ignore |
| `NGHTTP2_ERR_REFUSED_STREAM` | `Refused` | RST(REFUSED_STREAM) |
| `NGHTTP2_ERR_PUSH_DISABLED` | `Unsupported` | GOAWAY(PROTOCOL_ERROR) |
| `NGHTTP2_ERR_WOULDBLOCK` | none | wire ring full; output pending |
| `NGHTTP2_ERR_DEFERRED` | none | data provider has no bytes; resume later |
| `NGHTTP2_ERR_TEMPORAL_CALLBACK_FAILURE` | `Reset` | RST(INTERNAL_ERROR or submitted code) |
| `NGHTTP2_ERR_CALLBACK_FAILURE` | `Internal` | GOAWAY(INTERNAL_ERROR), close |
| `NGHTTP2_ERR_EOF` | `Closed` | close |
| `NGHTTP2_ERR_GOAWAY_ALREADY_SENT` | `Goaway` | close after drain |
| `NGHTTP2_ERR_SESSION_CLOSING` | `Closed` | no further work |
| `NGHTTP2_ERR_STREAM_ID_NOT_AVAILABLE` | `Refused` | GOAWAY(ENHANCE_YOUR_CALM) |
| `NGHTTP2_ERR_INVALID_ARGUMENT` | `InvalidArgument` | internal bug; close and report |
| `NGHTTP2_ERR_HTTP_HEADER` / `HTTP_MESSAGING` | `Protocol`/`HeaderList` | RST(PROTOCOL_ERROR) |
| unknown negative code | `Internal` | close; copy `nghttp2_strerror` to diagnostics |

Connection-level mapping uses GOAWAY with the RFC code; stream-level mapping
uses RST_STREAM. `nghttp2_strerror`/`nghttp2_http2_strerror` are copied into
bounded diagnostics, never parsed. `text(code)` is diagnostic only. HTTP
status mapping is separate: `400` malformed request, `408` timeout, `413`
body too large, `431` header too large (only if the header block completed),
`500` handler/internal, `503` admission; protocol-level failures do not get an
HTML page.

## 9. Tests

### 9.1 Deterministic C shim tests (no network)

- `h2_adapter_test.c` links the shim and nghttp2, feeds byte vectors through
  a fake `feed`/`send`, and asserts exact wire bytes and event sequences.
- Preface: correct magic, truncated magic, wrong magic, h1-looking bytes.
- SETTINGS: our initial frame, peer SETTINGS, ACK, missing ACK timeout,
  duplicate/invalid settings, too many entries.
- Frames: HEADERS, CONTINUATION, DATA, END_STREAM, END_HEADERS, PADDED,
  PRIORITY, PING, WINDOW_UPDATE, RST_STREAM, GOAWAY, unknown type.
- Header caps: count, field, list, arena; exactly at and one over each.
- Flow control: stream/connection window accounting; padding-only DATA;
  window update 0/overflow; consume-after-copy; no auto update.
- Response path: staged headers, commit on write/end, empty body END_STREAM,
  body with EOF, trailers with `NO_END_STREAM`, data provider deferred.
- Event ring: coalescing, high-water, deliberate overflow -> failure.
- Wire ring: full -> WOULDBLOCK, drain, retry, no partial frame.
- Error mapping: every nghttp2 code used by the shim maps to a stable
  `Failure`; unknown codes map to `Internal` with a copied diagnostic.
- `nghttp2_version`/`NGHTTP2_VERSION_NUM` compatibility check.

### 9.2 Deterministic obo tests (MemoryTransport)

A `MemoryTransport` implements `httpapi::Transport` with scripted reads,
writes, wants, ALPN and pending bytes. No sockets, TLS or reactor.

- Preface -> SETTINGS -> HEADERS -> response; exact bytes.
- Multiplexed streams: two HEADERS, interleaved DATA, two responses; no
  response reordering requirement, per-stream isolation.
- Request body: CL known/unknown, zero-length, DATA after END_STREAM
  (reject), body over `maxBody` (413/reset).
- Response: known length, unknown length, HEAD, 204, 304, 1xx, trailers,
  `end` retry, zero-write backpressure.
- Reset: peer RST mid-body, local reset, close stream, stale shell.
- Trailers: request trailers after DATA, response trailers after body,
  trailer after END_STREAM (reject), count/byte caps.
- Deadlines: fake clock, one byte per interval, header/body/response/idle/
  drain; no restart on partial progress.
- Error/status: malformed request headers, oversized, missing pseudo-header,
  Host mismatch, forbidden connection headers.
- Counters: streams opened/closed, resets, bytes, high-water; zero after
  close.

### 9.3 h2spec and interoperability

Primary conformance oracle: **h2spec** (pin the exact release and hash in the
integration job). Run all sections; a skip needs a written reason.

- TLS h2: a test server on loopback with a self-signed certificate and
  `systls Require(["h2"])`:
  `h2spec -h 127.0.0.1 -p <port> -t -k` (flags per the pinned h2spec
  release).
- h2c prior knowledge: a separate cleartext profile:
  `h2spec -h 127.0.0.1 -p <port>`.
- `curl --http2 -k https://127.0.0.1:<port>/` and
  `curl --http2-prior-knowledge http://127.0.0.1:<port>/`.
- `h2load -n 10000 -c 10 -m 20 https://...` (install `nghttp2-client` in the
  job) checks multiplexing, flow control and throughput; record, do not gate
  on, throughput.
- ALPN matrix: client offers h2, http/1.1, both, none; verify selection,
  rejection, and no h1 parse on h2.
- Differential: Go `net/http`/`net/http2` client and server against the same
  handler matrix; compare status, headers, body, trailers and reset
  behavior. `nghttpd` is optional if installed; it is absent now, so the
  default suite does not depend on it.

### 9.4 Security and abuse tests

- Rapid reset: `HEADERS` + immediate `RST_STREAM` in a tight loop; reset
  limiter fires; GOAWAY; memory/CPU bounded; no stream record leak.
- CONTINUATION flood: HEADERS without END_HEADERS plus many CONTINUATION
  frames; `maxContinuations` closes; no arena growth.
- HPACK bomb: one indexed field repeated to exceed the list cap; reset; no
  large allocation.
- Max streams: open 33 with `maxConcurrent=32`; 33rd refused; table count
  stays 32.
- Flow-control starvation: one stream fills its window and stops reading;
  other streams continue; connection window not exhausted by the stalled
  one.
- Slowloris: one byte per interval on preface/headers/body; deadlines fire.
- GOAWAY drain: client keeps opening streams after GOAWAY; new streams
  refused; existing finish; close by deadline.
- Malformed frames: bad length, stream 0 DATA, even stream id, HEADERS after
  END_STREAM, DATA on idle, PUSH_PROMISE, invalid padding, frame size.
- ALPN/h2c: wrong ALPN, no ALPN, h1 bytes on h2, h2 preface on h1, h2c
  Upgrade attempt.
- Header policy: oversized, duplicate authority, Host mismatch, forbidden
  connection headers, invalid value bytes, response header injection.
- Fuzz: libFuzzer harness feeds random bytes and mutated valid frames to
  `h2_conn_feed`; no crash, no leak, bounded memory. A second harness fuzzes
  the header/response packet parsers.

### 9.5 Sanitizers, leaks and accounting

- C harness and obo server under ASAN/UBSAN; nghttp2 and the shim under
  LSAN.
- `platform::counts()` returns zero `KIND_H2_CONNECTION` after close;
  `h2::counts().liveStreams == 0`; `nativeBytes` returns to baseline.
- Deliberately omitted `close` fails the leak gate (positive control).
- Allocation failure at connection/stream/header/wire/event creation returns
  `ResourceLimit`/`Busy`, closes cleanly, and leaks nothing. nghttp2 NOMEM is
  tested with a custom `nghttp2_mem` allocator in the C harness (optional,
  but recommended).
- One native release per connection; double close returns false; stale
  stream shells fail at their own kind check without dereference.

### 9.6 Acceptance gates

H2a:

- C harness: preface, SETTINGS/ACK, PING, GOAWAY, wire/event bounds, error
  mapping.
- h2spec connection-level sections that do not require streams.
- ASAN/LSAN clean; no pointer in the cbind; no callback into obo.

H2b:

- h2spec HTTP/2 sections for streams, headers, data, flow control, reset,
  trailers, settings, ping, goaway, priority, padding.
- Multiplexing, flow-control/backpressure, max-streams, timeout and trailer
  tests.
- Leak counters zero; aggregate memory measured against the reservation.

H2c:

- Full h2spec over TLS h2 and h2c; curl/h2load/Go differential.
- Rapid reset, CONTINUATION flood, HPACK bomb, slowloris, GOAWAY drain,
  malformed frames, ALPN/h2c matrix.
- ASAN/UBSAN/LSAN; fuzz corpus; graceful shutdown; no request smuggling.

## 10. Phasing, dependencies and effort

| Phase | Scope | Depends on | Exit | Rough effort |
|---|---|---|---|---|
| **H2a: shim/session** | C shim, cbind, `H2Connection`, preface, SETTINGS/ACK, PING, GOAWAY, event/wire rings, stats, error mapping; no streams | systems P2, cinterop P2T-a, pinned nghttp2 headers | C harness, connection-level h2spec, ASAN/LSAN, no pointers | 2-3 engineer-weeks |
| **H2b: streams/flow** | stream table, HEADERS/CONTINUATION, request body, response headers/body/trailers, flow control, reset, max streams, deadlines | H2a | h2spec stream sections, multiplexing/flow/trailer/max-stream tests | 3-4 engineer-weeks |
| **H2c: integration/security** | TLS ALPN dispatch, h2c prior knowledge, shared `httpapi` handler seam, rapid reset, CONTINUATION/HPACK caps, drain, differential, fuzz | H2b, TLS P2T-b (present), H1b/H1c for a combined listener (H1a0 only now) | full h2spec, curl/h2load/Go, security matrix, ASAN/leaks | 2-3 engineer-weeks |

H2 can be tested standalone over a dedicated TLS or h2c port before H1b/H1c;
the combined listener and H1 handler adoption are H2c/H1c integration work.
No phase requires a new compiler feature or OS primitive.

Effort is for one experienced engineer including tests, not a marketing
estimate. Review, fuzzing time and h2spec triage are additional.

## 11. Rejected alternatives

1. **From-scratch framer/HPACK.** 8k-15k lines of security-sensitive code,
   HPACK Huffman/static/dynamic tables, CONTINUATION, flow control, settings
   and stream state; h2spec/CVE exposure. nghttp2 is maintained,
   security-patched and already installed. Reject.
2. **Callbacks from C into obo.** Unsupported by cinterop, reentrancy and
   lifetime hazard. C queues bounded events; obo polls. Reject.
3. **One stream at a time.** Defeats multiplexing and creates head-of-line
   blocking the protocol exists to avoid. Reject.
4. **Ignoring flow control or using auto window update.** Unbounded receive
   buffering and memory DoS; defeats backpressure. Reject.
5. **Per-stream OS resources (fd, thread, reactor watch).** One TCP/TLS
   connection has one watch; streams are logical. Reject.
6. **Exposing `nghttp2_session*`/`nghttp2_frame*` to obo.** Pointer lifetime,
   UAF and ABI hazard. Tokens and copied packets only. Reject.
7. **`nghttp2_session_mem_send` with its internal pointer.** The pointer is
   invalidated by the next call. Copy complete frames into a wire ring. Reject.
8. **h2 over HTTP/1.1 `Upgrade` in v1.** Adds `101`, `HTTP2-Settings`,
   version-sniffing and smuggling ambiguity; no demonstrated need. Reject.
9. **Mixed cleartext h1/h2 by preface sniffing.** Same ambiguity; use
   separate profiles or TLS ALPN. Reject for v1.
10. **Buffering whole request/response bodies.** Memory DoS, no streaming, no
    trailers. Reject.
11. **Declarations-only nghttp2 binding.** Hand-copied structs, unions,
    callback typedefs and enums are a second unreviewed ABI. Require headers.
12. **Server push in v1.** Extra state, cache/authorization policy and abuse
    surface; not needed. Reject.
13. **RFC 7540 priority scheduling.** Deprecated by RFC 9113/9218; a tree adds
    state and starvation bugs. Ignore it.
14. **A single global connection budget without per-stream caps.** One stream
    can exhaust it. Per-stream and aggregate caps both required. Reject.
15. **Treating `close` as a clean GOAWAY drain.** `close` is abortive; use
    `drain` for graceful shutdown. Reject conflating them.
16. **A function-value handler or a C handler callback.** Obo forbids function
    types with interface parameters and function values in fields; C cannot
    call obo. Use the `Handler` interface called by the application loop.
17. **A borrowed `H2Stream` view without its own registry entry.** Obo
    `ref record` assignment moves and a `platform::Resource` cannot be copied
    into a second shell; the `Exchange` implementation would have no valid
    connection handle. Use a per-stream `KIND_H2_STREAM` shell with explicit
    adopt/orphan and close.

## 12. Open choices and recommendations

| Choice | Recommendation | Consequence otherwise |
|---|---|---|
| nghttp2 headers | Require `libnghttp2-dev`; pin `1.59.0-1ubuntu0.4` or later with `max_continuations`; header-mode signature check | Declarations-only is an unreviewed ABI and can silently change framing/HPACK policy |
| Push | Disabled; `ENABLE_PUSH=0`; `push()` absent | Push adds cache/authorization policy and abuse surface |
| h2c | Off by default; prior knowledge only on a dedicated cleartext profile; no Upgrade | Upgrade/sniffing creates downgrade and smuggling ambiguity |
| Max concurrent/header caps | 32 streams, 16 KiB header list, 100 fields, 4 continuations | Higher caps increase per-connection memory and HPACK/DoS exposure |
| Priority | Ignore RFC 7540; advertise `NO_RFC7540_PRIORITIES`; app-level fairness | A priority tree adds state and starvation bugs for little v1 benefit |
| Handler model | `Handler` interface called by the app loop; one `Exchange` per stream; no stored borrows | Function values/callbacks are unsupported and invert control |
| Error page | Minimal plain text, no echo, `content-length`, no `Server` version; RST/GOAWAY for protocol errors | HTML/echo invites XSS, log injection and fingerprinting |
| Stream shell | Per-stream `KIND_H2_STREAM` shell, adopted exactly once; connection close orphans it and the shell close retires it | A borrowed view cannot hold a `Resource`; copying one is illegal and a raw id is not a checked identity |
| 100-continue | Optional `interim(100)`, not automatic | Always-send is unsolicited; never-send can deadlock a waiting client |
| `maxConnections` | 14 with 32 streams and the checked 64 MiB aggregate; 20 requires raising `maxNativeBytes` to 96 MiB | A count alone lets raised buffers exceed the memory budget |
| h2c preface timing | Validate the preface before sending SETTINGS | Sending to scanners wastes bytes and complicates failure policy |
| Response header commit | Commit on first body write or `end` | Early commit adds empty DATA frames or a separate flush API |
| Trailers on H1 | Chunked only; H2 always (subject to caps) | Pretending H1 always supports trailers changes framing |

## 13. Invariants and review questions

### 13.1 Invariants

1. One TCP/TLS connection has one reactor watch and one `H2Connection`; streams
   are logical and bounded by `maxConcurrentStreams`.
2. No h2 byte is parsed before ALPN `h2` (TLS) or a valid preface (h2c).
3. The server SETTINGS is the first frame; the client preface is validated
   before any frame; a bad preface closes without an HTTP response.
4. At most `maxEvents` fixed event records exist; overflow is a connection
   failure, never silent loss.
5. Header/body payloads live in bounded per-stream C buffers; events carry no
   payload; no whole-body allocation.
6. `recvBuffered[s] <= maxStreamRecvBuffer`; `sum(recvBuffered) <=
   maxConnectionWindow`; consume only after bytes leave the ring.
7. Padding is consumed by nghttp2; only application bytes are consumed by the
   shim; no padding-induced window stall.
8. `sendBuffered[s] <= maxStreamSendBuffer`; accepted bytes are sent exactly
   once or the stream is reset.
9. `wire` bytes live in the C ring or the obo out-buffer, never both; the wire
   ring never holds a partial frame.
10. One `nghttp2_session` and one C stream record per live stream; no
    per-stream fd/thread/watch.
11. `openStream` adopts/registers a stream entry exactly once; `closeStream`/
    `close` are idempotent; dropping a record frees only the record.
12. A connection close retires the connection token, frees the session, wire,
    events and arena once, and orphans registered stream entries; each stream
    shell close retires its own token exactly once. An unclaimed stream has no
    registry entry and is freed with the connection.
13. Every deadline is absolute; no partial progress restarts it.
14. GOAWAY is sent before graceful close; `close` is abortive; drain has a
    deadline.
15. No pointer, C struct, function pointer or callback crosses the cbind; C
    never calls obo.
16. Remote bytes produce `Result`/events, never a panic, trap or exit.
17. Header names/values, pseudo-headers, connection-specific fields and
    response framing are validated before use; no request echo.
18. A handler error after headers commit resets the stream; before commit it
    sends a minimal 5xx; no partial header block is ever emitted.
19. Application fairness: one stream cannot monopolize a turn; per-stream
    work is capped.
20. All limits are validated with checked arithmetic before any allocation or
    native call.

### 13.2 Review questions

1. Is the nghttp2 shim boundary (fixed events, pull-based headers/body,
   all-or-nothing send callback, deferred data provider, no pointers) correct
   and sufficient?
2. Is the per-stream `KIND_H2_STREAM` shell with adopt/orphan/close correct,
   including `openStream` adopt failure, connection close with live streams,
   and the leak oracle?
3. Are the flow-control invariants correct, especially consume-after-copy,
   padding, connection window = maxConcurrent x stream window, and the
   zero-window send-ring behavior?
4. Can H1 implement `httpapi::Exchange` without changing its parser/serializer
   semantics, and does the seam avoid pretending h1/h2 are identical?
5. Are the limit defaults and the aggregate reservation formula safe and
   testable? Is 64 MiB / 14 connections the right starting point?
6. Is the GOAWAY drain sequence (`shutdown_notice`, then `submit_goaway
   (last_proc_stream_id)`, then close) correct for RFC 9113 and nghttp2
   1.59?
7. Is requiring nghttp2 headers with the exact Ubuntu pin defensible, and is
   the provenance/test strategy sufficient?
8. Are the rapid-reset, CONTINUATION, HPACK and header-list controls
   sufficient, and are the reset-vs-connection-error choices right?
9. Is the error/status policy (minimal responses, RST/GOAWAY, no echo)
   implementable and safe?
10. Is the phasing/dependency order right, especially H2 standalone before
    the H1 combined listener?

## 14. Conclusion

The recommended implementation is a thin, trusted nghttp2 C shim under a
strict scalar/token boundary, with obo owning connection policy, per-stream shells,
deadlines, admission and the shared `httpapi` handler seam. This keeps the
security-critical framing, HPACK and flow-control work in a maintained,
h2spec-tested system library, while making obo responsible for the parts a
library cannot decide: limits, fairness, ALPN/h2c policy, error policy,
graceful drain and application ownership.

The design is ready for independent review, not implementation. The first
implementation gate is a compiled shim against the pinned headers, a C
harness proving the event/wire/flow-control invariants, and a TLS h2
connection that passes the connection-level h2spec sections. No compiler,
ABI, symbol-format, package or protocol version is reserved by this note.
