# Experimental HTTP/2 server core (H2a)

Status: **experimental H2a slice. Unstable. Not a merged or versioned API, and
not a stability, protocol-version or release claim.** The authoritative design
is [`obo/design/http2-server-design.md`](../../design/http2-server-design.md).

H2a implements the trusted nghttp2 C shim and the obo connection/stream core:
ALPN/mode checks, the client preface, SETTINGS/ACK, PING, GOAWAY, HEADERS,
CONTINUATION, DATA, WINDOW_UPDATE, RST_STREAM, bounded header/body buffers,
per-stream shells with adopt/orphan/close lifetime, flow-control window
restoration, deadlines, frame budgets, the fixed event ring, the complete-frame
wire ring, the deferred data provider, response headers/body/trailers, the
pure action executor and deterministic/interop tests. It adds no compiler,
runtime, symbol-format, ABI, seed or package-version change.

H2b adds the wider h2spec stream/flow/trailer matrix and H2c adds the reactor
multiplexed server loop, h2c/TLS dispatch integration, full h2spec/h2load/Go
differential and the security/fuzz matrix. See `LIMITS.md` for the exact
boundary of this slice.

## Layout

```text
obo/experimental/http2/
  http2.obx              H2Connection/H2Stream, limits/options/admission,
                         events, flow control, response API, runAction
  http2_adapter.cbind    scalar/token binding (headers required)
  native/h2_adapter.h    fixed scalar surface and packet/event constants
  native/h2_adapter.c    trusted nghttp2 server shim
  tests/
    test_http2.py        one-command C + obo + h2c/TLS + curl suite
    c/h2_harness.c       nghttp2 client/server harness, error and ASAN gates
    apps/h2_mem_driver.obx   deterministic memory-transport driver
    apps/h2_serve.obx        shared bounded test server loop
    apps/h2c_server.obx      cleartext prior-knowledge curl server
    apps/h2tls_server.obx    systls ALPN h2 curl server
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
harness and its ASAN/UBSAN/LSAN build, builds the obo memory/h2c/TLS drivers
through cinterop, verifies manifests, checks limits/admission/mode policy and
the adoption-failure path, verifies memory response bytes and trailers with a
real nghttp2 client, checks orphan access, idle timeout, reset, fatal-error
GOAWAY flush and DATA-on-stream-0 handling, and runs curl
`--http2-prior-knowledge` and `--http2` over `systls` ALPN h2 on loopback.
h2spec and h2load are explicit integration dependencies. They are skipped when
not installed, with the missing-tool reason printed; no default test downloads a
protocol tool or contacts a protocol peer outside loopback. The only optional
network action is `apt-get download libnghttp2-dev=<installed version>` when the
matching headers are not installed; it is recorded and the archive is extracted
privately. h2spec v2.6.0 is the pinned manual integration target
(`h2spec_linux_amd64.tar.gz`, GitHub release v2.6.0); it is not fetched
automatically.

## Non-goals in H2a

- No client mode, server push, HTTP/3, CONNECT tunneling, proxying or caches.
- No reactor-backed multiplexed server; the test server intentionally keeps one
  in-flight request per connection. The shim and C harness cover multiplexed
  streams and the 32-stream cap.
- No h2spec/h2load/Go differential or fuzz corpus in this slice.
- Interim `1xx` responses (`interim`) are `Unsupported`; `101` is invalid.
- The `Date` response header is not added automatically yet; applications may
  add it with `setHeader`. Protocol-owned `content-length` is injected from
  `setLength` and cannot be set through `setHeader`.
- A general impure application handler is a language gap in 0.14. The pure
  `Handler = fn(&RequestMeta, &Now): Action` plus concrete stream functions is
  the supported path.
