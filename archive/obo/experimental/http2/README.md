# Experimental HTTP/2 server (H2a/H2b/H2c)

Status: **experimental H2a/H2b/H2c slice. Unstable. Not a merged or versioned
API, and not a stability, protocol-version or release claim.** The
authoritative design is
[`obo/design/http2-server-design.md`](../../design/http2-server-design.md).

H2a implements the trusted nghttp2 C shim and the obo connection/stream core:
ALPN/mode checks, the client preface, SETTINGS/ACK, PING, GOAWAY, HEADERS,
CONTINUATION, DATA, WINDOW_UPDATE, RST_STREAM, bounded header/body buffers,
per-stream shells with adopt/orphan/close lifetime, flow-control window
restoration, deadlines, frame budgets, the fixed event ring, the complete-frame
wire ring, the deferred data provider, response headers/body/trailers, the
pure action executor and deterministic/interop tests. It adds no compiler,
runtime, symbol-format, ABI, seed or package-version change.

H2b adds the multiplexed stream/flow-control/trailer server path. H2c adds the
reactor-backed server loop, TLS ALPN and h2c listeners, rapid-reset and
CONTINUATION/HPACK/zero-DATA caps, graceful GOAWAY drain, and the integration
matrix. The nghttp2 shim and every native lifetime rule remain unchanged from
H2a. See `LIMITS.md` for the exact boundary of this slice.

## Layout

```text
obo/experimental/http2/
  http2.obx              H2Connection/H2Stream, limits/options/admission,
                         events, flow control, response API, runAction
  http2server.obx        H2b/H2c reactor server loop: h2c/TLS listeners,
                         multiplexed slots, admission, drain, metrics
  http2_adapter.cbind    scalar/token binding (headers required)
  native/h2_adapter.h    fixed scalar surface and packet/event constants
  native/h2_adapter.c    trusted nghttp2 server shim
  CONFORMANCE.md         pinned h2spec/h2load versions, commands and triage
  tests/
    test_http2.py        one-command C + obo + h2c/TLS + curl suite
    c/h2_harness.c       nghttp2 client/server harness, error and ASAN gates
    apps/h2_mem_driver.obx   deterministic memory-transport driver
    apps/h2_serve.obx        shared bounded test server loop
    apps/h2c_server.obx      cleartext prior-knowledge curl server
    apps/h2tls_server.obx    systls ALPN h2 curl server
    apps/h2server.obx        H2b/H2c multiplexed h2c/TLS server driver
    h2client.py             raw multiplex/flow/abuse test client
obo/experimental/httpapi/httpapi.obx
                         shared Header/RequestMeta/Action/Handler/Failure
obo/experimental/httptransport/httptransport.obx
                         concrete plain/TLS/memory byte transport
```

The modules are ordinary source modules:

```obo
import "http2.obx" as http2
import "httpapi.obx" as http
import "httptransport.obx" as transport
```

## Native boundary

`native/h2_adapter.c` owns `nghttp2_session`, callbacks, the per-connection
wire ring, the fixed event ring, per-stream header arenas, receive/send rings
and the deferred data provider. The cbind exposes only `i64` arguments/results
and platform byte-buffer tokens. No nghttp2 pointer, C struct, `void*`, function
pointer or variadic argument crosses the boundary. nghttp2 callbacks queue fixed
events and copy bounded payloads; C never calls into obo.

Build inputs:

- `libnghttp2-14` runtime, SONAME `libnghttp2.so.14`. Link with
  `-l:libnghttp2.so.14`, never `-lnghttp2`.
- `libnghttp2-dev` headers are **required**; none are committed. The test
  script uses installed headers when they are the pinned version, otherwise
  `apt-get download libnghttp2-dev=<installed libnghttp2-14 version>` and
  extracts them privately under `/tmp/opencode`. The committed `.cbind` has no
  machine-specific include path.
- The shim requires `NGHTTP2_VERSION_NUM >= 0x013b00` (1.59.0), age 1,
  `proto_str == "h2"`, and compile/link references to
  `nghttp2_option_set_max_continuations` and
  `nghttp2_option_set_stream_reset_rate_limit`. The design's Ubuntu
  `1.59.0-1ubuntu0.4` package is the provenance baseline; an unpatched upstream
  1.59.0 header lacks the CONTINUATION option and fails the build.
- The hosted link keeps full RELRO, immediate binding and a non-executable
  stack from the cinterop tool.

## One-command tests

```sh
make -C obo http2
```

or directly:

```sh
python3 obo/experimental/http2/tests/test_http2.py
```

The suite builds the shim and harness against the pinned headers, runs the C
harness and its ASAN/UBSAN/LSAN build, builds the obo memory/h2c/TLS/server
drivers through cinterop, verifies manifests, checks limits/admission/mode
policy and the adoption-failure path, verifies memory response bytes and
trailers with a real nghttp2 client, checks orphan access, idle timeout, reset,
fatal-error GOAWAY flush and DATA-on-stream-0 handling, and runs curl
`--http2-prior-knowledge` and `--http2` over `systls` ALPN h2 on loopback.

The H2b/H2c section additionally drives the reactor server with a raw HTTP/2
client: eight concurrent streams on one connection, 160 KiB of flow-controlled
`/big` responses, a 30 KiB echoed request, the 32-stream cap, rapid reset,
CONTINUATION flood, header-list flood, zero-DATA flood, bad h2c preface and an
HTTP/1.1 `Upgrade` attempt. Each abuse case must leave the server serving a
fresh connection. A fast-timeout limits profile checks 14 zero-byte h2c
prefaces, idle, stalled-header and body-idle deadlines without stopping the
loop. A saturation test fills `maxConnections`, leaves a 15th connection
pending, and asserts the paused listener does not spin. A response-flow-control
test requests 204800 bytes across ten `/big` streams and a 100 KiB Echo
response, withholds connection/stream WINDOW_UPDATE, asserts low CPU, then
grants credit and asserts every response resumes. An EMFILE test runs
under `ulimit -n 12`, opens 30 pending connections, asserts the accept-pressure
pause does not spin and that service recovers. A small-buffer profile checks
8 KiB `maxStreamSendBuffer` progress, 4 KiB `maxReadChunk` echo and
`maxRequestsPerConnection` refusal with `RST_STREAM(REFUSED_STREAM)`. The drain
test keeps an active stream open and asserts the notice GOAWAY, barrier PING,
and final `min(last_proc, cutoff)` GOAWAY reach the wire before close. TLS tests
enforce the wrong-ALPN failure, multiplex over TLS, graceful GOAWAY drain and
balanced connection/stream/platform counts. Conformance regressions cover
frames after END_STREAM/RST_STREAM (`STREAM_CLOSED`), PRIORITY stream 0 and
self-dependency (`PROTOCOL_ERROR`), peer-GOAWAY-plus-PING progress, and TLS
control-frame flush (PING ACK on an idle connection and after a response).
h2spec and h2load are explicit integration dependencies. They are skipped when
not installed, with the missing-tool reason printed; no default test downloads a
protocol tool or contacts a protocol peer outside loopback. The only optional
network action is `apt-get download libnghttp2-dev=<installed version>` when the
matching headers are not installed; it is recorded and the archive is extracted
privately. h2spec v2.6.0 is the pinned manual integration target
(`h2spec_linux_amd64.tar.gz`, GitHub release v2.6.0); it is not fetched
automatically. `CONFORMANCE.md` records the exact pinned tools, commands and
results: final h2spec is 145/146 default (146/147 strict) on both h2c and TLS
ALPN h2, with the one residual upstream nghttp2 stream-id case; h2load completes
5000/5000 requests at `-n 5000 -c 10 -m 10` on both listeners.

## Non-goals

- No client mode, server push, HTTP/3, CONNECT tunneling, proxying or caches.
- No combined HTTP/1.1 + HTTP/2 ALPN dispatch: `http2server` owns an h2-only or
  h2c listener. A caller that wants both protocols composes two listeners or a
  future H1c dispatch layer.
- h2spec/h2load/Go are optional integration tools; the default suite reports
  their absence and the pinned manual targets instead of downloading them.
  There is no committed fuzz corpus in this slice.
- No `interim(1xx)` yet; `101` is invalid.
- The loop's handler is the pure `httpapi::Handler`. Arbitrary body inspection
  and streaming use the concrete `http2` stream functions.
- Interim `1xx` responses (`interim`) are `Unsupported`; `101` is invalid.
- The `Date` response header is not added automatically yet; applications may
  add it with `setHeader`. Protocol-owned `content-length` is injected from
  `setLength` and cannot be set through `setHeader`.
- A general impure application handler is a language gap in 0.14. The pure
  `Handler = fn(&RequestMeta, &Now): Action` plus concrete stream functions is
  the supported path.

## Server loop

```obo
import "http2server.obx" as server

let srv = try server::make(listener, reactor, &limits, &options)       // h2c
// or: server::makeTls(listener, reactor, &limits, &options, ctx)      // ALPN h2
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

Admission is the H2 per-connection reservation plus `maxConnections` and
`maxTotalConnectionBytes`. Connection slots use `(generation, index)` tags, so a
stale reactor event cannot reach a reused slot. When admission is full the
listener watch is removed and restored when capacity returns; a permanently
readable backlog does not spin. One connection failure never stops the loop.
A `Stop` action triggers `beginShutdown`; `Close` drains one connection. `drain`
uses the H2 GOAWAY/PING barrier, requires the final GOAWAY to reach the wire,
flushes pending output, and is bounded by `drainTimeoutMs`. The shim preface
deadline is armed at accept, so h2c zero-byte clients are bounded by
`prefaceTimeoutMs`.
