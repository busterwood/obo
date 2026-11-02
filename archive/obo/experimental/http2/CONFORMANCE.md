# HTTP/2 conformance and load notes

Status: manual integration record for the experimental H2a/H2b/H2c server. This
is not a stability or release claim. The deterministic suite stays offline; the
tools below are fetched into `/tmp` and are **not** committed.

## Pinned tools

| Tool | Version / provenance | SHA-256 |
|---|---|---|
| h2spec | v2.6.0, release binary `h2spec_linux_amd64.tar.gz`, upstream commit `70ac2294010887f48b18e2d64f5cccd48421fad1` | `157ee0de702e01ad40e752dbf074b366027e550c8e7504f9450da2809e279318` |
| h2spec source | v2.6.0 source tarball, used only to read test expectations | `c438130c70c6cec2c20276726a454a9249b64ba48ca89ce37f39aa721867b4f0` |
| h2load / nghttp | Ubuntu `nghttp2-client 1.59.0-1ubuntu0.4`, reports `nghttp2/1.59.0` | `381c5da569ebd2ec7f0418e76e3b65066496ab58b243067b543dc0df565cc03d` |
| h2load runtime | `libjemalloc2 5.3.0-2build1` (extracted privately) | `0cccf7701765b5a633b964ed551c2bd1c074cad17daea8fa20a4e9cad0adb5a8` |
| h2load runtime | `libev4t64 1:4.33-2.1build1` (extracted privately) | `efc04482efadbc5b95a9386131190015c206fdae324caffdd129e602196b0757` |
| nghttp2 | system `libnghttp2-14 1.59.0-1ubuntu0.4`, headers `libnghttp2-dev 1.59.0-1ubuntu0.4` fetched privately by the test script | design baseline in `LIMITS.md` |
| reference server | Ubuntu `nghttp2-server 1.59.0-1ubuntu0.4`, `nghttpd --no-tls` | `0d919e4f516d054e32b11ad87164e80bab25b103d19883c6e7f8d03943e743ec` |

`curl 8.5.0` (nghttp2/1.59.0, OpenSSL 3.0.13) and `openssl 3.0.13` are the
other integration clients.

## Commands

The server driver is built by the normal suite, which also proves the cert and
pinned nghttp2 headers. In this run the driver was built privately under
`/tmp/opencode/h2-conformance-build`:

```sh
make -C obo http2
./out-h2server/app h2c 1744
./out-h2server/app tls 1549 certs/cert.pem certs/key.pem
```

h2spec, cleartext prior-knowledge (`h2c`) on port 1744:

```sh
h2spec -h 127.0.0.1 -p 1744 -P / -o 3
h2spec -S -h 127.0.0.1 -p 1744 -P / -o 3
```

h2spec over TLS with ALPN `h2` on port 1549 (`-t` makes h2spec negotiate h2):

```sh
h2spec -h 127.0.0.1 -p 1549 -P / -t -k -o 3
h2spec -S -h 127.0.0.1 -p 1549 -P / -t -k -o 3
```

Load, all requests to `/hello` (the fixed `Hello` action, 6-byte body):

```sh
h2load -n 5000 -c 10 -m 10 http://127.0.0.1:1744/hello
h2load -n 5000 -c 10 -m 10 https://127.0.0.1:1549/hello
```

## Results

h2spec, final code:

| Target | Mode | Tests | Passed | Failed |
|---|---|---:|---:|---:|
| h2c, port 1744 | default | 146 | 145 | 1 |
| h2c, port 1744 | strict (`-S`) | 147 | 146 | 1 |
| TLS ALPN h2, port 1549 | default | 146 | 145 | 1 |
| TLS ALPN h2, port 1549 | strict (`-S`) | 147 | 146 | 1 |

The single remaining failure is HTTP/2 `5.1.1/2`, "Sends stream identifier that
is numerically smaller than previous". h2spec opens stream 5 and then sends
HEADERS for stream 3; nghttp2 1.59.0 deliberately ignores an unknown lower
stream id instead of issuing PROTOCOL_ERROR (its source explains the
asynchronous close/race ambiguity). The reference Ubuntu `nghttpd 1.59.0`
fails exactly the same one test under the same h2spec binary (145/146), so this
is an upstream conservative choice, not a defect introduced by this server.
It is recorded as a known library limitation rather than patched with a second,
shim-level stream-id tracker.

h2load, final code:

| Target | `-n` | `-c` | `-m` | Succeeded | Failed | Time | Request rate |
|---|---:|---:|---:|---:|---:|---:|---:|
| h2c | 5000 | 10 | 10 | 5000 | 0 | 0.98 s | 5118.74 req/s |
| TLS h2 | 5000 | 10 | 10 | 5000 | 0 | 1.05 s | 4759.24 req/s |

TLS negotiated TLS 1.3 (`TLS_AES_256_GCM_SHA384`, X25519) and ALPN `h2`.
The h2c run used h2c prior knowledge.

`maxRequestsPerConnection` is 1000. A run that requests more than 1000 total
per connection (for example `-n 10000 -c 10`) intentionally gets
`RST_STREAM(REFUSED_STREAM)` for the excess and reports h2load errors; that is
the configured policy, not a protocol failure. The default platform
`maxShells` (8192) also bounds total resource shells created in one server
process; long-running production profiles should raise
`platform::Options.maxShells` (and `maxResources` if needed) deliberately.

## Triage of the initial failures, and fixes

Initial baseline before the fixes below was observed approximately in single
runs: h2c about 137/146 (9 failures), TLS about 103/146 (43 failures). Repeated
baseline runs varied (for example h2c 136/137 and TLS 100/98) because the
stalls and timeouts interact with the tool's per-connection sequencing; treat
those baseline counts as approximate observations. The final 145/146 default
and 146/147 strict results are stable. The extra TLS failures were control
frames that stalled with no application work pending.

Fixed real server/protocol defects:

1. **Closed-stream frames were silently ignored.** The shim set
   `nghttp2_option_set_no_closed_streams` and advertised
   `SETTINGS_NO_RFC7540_PRIORITIES=1`. Either choice makes nghttp2 destroy
   closed streams, so frames after RST_STREAM/END_STREAM were not rejected.
   RFC 9113 5.1 requires STREAM_CLOSED handling. Both are removed; nghttp2 now
   retains at most `maxConcurrentStreams` closed streams, which is bounded.
   This fixes h2spec 5.1/5, 5.1.8, 5.1.9, 5.1.11, 5.1.12 and the PRIORITY
   tests 5.3.1/2 and 6.3/1. The application/shim implements no priority policy;
   nghttp2 1.59 may use the retained RFC 7540 dependency tree for internal
   scheduling.
2. **Peer GOAWAY stalled the drain.** `nghttp2_submit_shutdown_notice` is a
   no-op once the peer has sent GOAWAY, so waiting for its send callback never
   advanced. `h2_conn_drain` now submits the final GOAWAY directly when a peer
   GOAWAY was already received. This fixes h2spec generic 3.8/1 and 7/1.
3. **TLS ciphertext was not flushed or counted as pending output.** A PING ACK,
   SETTINGS ACK or GOAWAY could sit in the TLS session with no H2 wire bytes,
   no write watch, and no later turn. `http2::next` flushes transport output,
   `outputPending`/`canWrite` report it, and the server watches the read side
   while output is pending so a peer FIN is not missed. This fixed the
   TLS-only PING/SETTINGS/GOAWAY/half-closed-stream failures.
4. **Deferred response data could stop after a WINDOW_UPDATE.** nghttp2 can
   still want write after its wire ring drained (priority-tree scheduling).
   `h2_conn_want_write`/`http2::canWrite` now keeps the server driving send, and
   the drain-complete check is repeated after a final GOAWAY flush. The
   response-flow-control regression test now also exercises this path.

Not server defects / unsupported optional features:

- 5.1.1/2 above: upstream nghttp2 conservative stream-id handling; also fails
  on nghttpd 1.59.0.
- The application/shim implements no RFC 7540 priority policy. Priority state
  is retained only for validation, and nghttp2 1.59 may use the retained
  dependency tree for internal scheduling. RFC 9218 priority signals are not
  advertised.
- Server push, `CONNECT`, extended CONNECT, 1xx interim responses, combined
  H1/H2 ALPN dispatch and automatic `Date` remain non-goals as documented in
  `README.md`.

## Regression coverage added

`tests/test_http2.py` now asserts, in addition to the existing suite:

- DATA after END_STREAM and DATA after RST_STREAM produce STREAM_CLOSED;
- PRIORITY on stream 0 and a self-dependent PRIORITY produce PROTOCOL_ERROR;
- a peer GOAWAY followed by PING is acknowledged or the connection closes
  promptly instead of stalling;
- a TLS PING gets an ACK both on an idle connection and after a response, and a
  subsequent request still succeeds.

These are deterministic loopback tests and do not require h2spec or h2load.
