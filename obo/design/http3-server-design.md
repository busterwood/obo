# HTTP/3 server over QUIC

Status: **design for independent review; no implementation authorized.**
Baseline: current `main` `544dff836eb9f3f815d71f29875e2db9c9c175ae`
(HTTP/2 H2a/H2b/H2c, HTTP/1.1 H1c and TLS P2T-b merged). Date: 2026-11-01.
Current obo is `0.14`, symbol format 7, ABI 1. This note reserves no compiler,
ABI, symbol-format, package, protocol or release version. It designs a
server-side HTTP/3 origin server above the existing HTTP/1.1/2 server, systems
and TLS layers; it does not redesign HTTP/1.1 or HTTP/2, does not add a new
crypto library to `systls`, and does not authorize implementation.

Related notes and code:
[http2-server-design.md](http2-server-design.md),
[http1-server-design.md](http1-server-design.md),
[tls-adapter-design.md](tls-adapter-design.md),
[systems-stdlib-design.md](systems-stdlib-design.md),
[native-c-interop-design.md](native-c-interop-design.md),
[template-literals-design.md](template-literals-design.md),
[`../experimental/http2/`](../experimental/http2/),
[`../experimental/httptransport/`](../experimental/httptransport/),
[`../experimental/httpapi/`](../experimental/httpapi/).

Reading guide: §1 scope and decision; §2 environment evidence; §3 strategy;
§4 QUIC transport and the C boundary; §5 HTTP/3 mapping; §6 API and the shared
handler seam; §7 state machines, deadlines and flow control; §8 security and
DoS; §9 tests; §10 phasing and effort; §11 rejected alternatives; §12 open
choices; §13 invariants and review questions; §14 conclusion.

## 1. Decision and scope

Build a **native server-side HTTP/3 origin server** on UDP, using the system
**ngtcp2 0.12.1** QUIC stack, **nghttp3 0.8.0** for HTTP/3/QPACK, and the
**GnuTLS 3.8.3** QUIC-TLS integration supplied by
`libngtcp2_crypto_gnutls`. obo owns the UDP socket policy, admission, limits,
deadlines, the connection/stream shells and the shared handler seam. The
trusted C shim owns ngtcp2, nghttp3, GnuTLS and all callbacks; it exposes only
scalars, tokens and bounded buffers, and never calls into obo.

Summary of decisions:

| Decision | Choice |
|---|---|
| QUIC stack | system ngtcp2 0.12.1 (`libngtcp2.so.9`) |
| HTTP/3/QPACK | system nghttp3 0.8.0 (`libnghttp3.so.3`) |
| QUIC-TLS | GnuTLS 3.8.3 through `libngtcp2_crypto_gnutls.so.2`, a separate internal `quictls` C unit; **not** `systls` and not OpenSSL |
| UDP I/O | extend `sysnet::Datagram` with bounded receive batches, GSO send, GRO segment reporting and a datagram watch; the C shim never owns a socket |
| Native boundary | scalar `i64` arguments/results and platform buffer tokens; no pointer, struct, function pointer or callback into obo |
| Native ownership | C owns `ngtcp2_conn*`, `nghttp3_conn*`, GnuTLS session, CID routing table, event ring, wire batch; obo owns `Server`, `Connection` and `Stream` shells |
| ALPN | `h3` required; no `h3-29`, no HTTP/1.1 or HTTP/2 over QUIC |
| HTTP/3 scope | request/response, DATA, trailers, control/QPACK streams, SETTINGS, GOAWAY, bounded multiplexing |
| Push, WebTransport, HTTP/3 datagrams | none in v1 |
| 0-RTT | disabled in v1; no tickets, no early data |
| Migration | `disable_active_migration=1`; tolerate validated NAT rebinding; no preferred address |
| Coalescing | one authority per QUIC connection in v1; a different `:authority` gets `421` |
| Client mode | none |
| Retry | address validation by Retry on a public listener; configurable for loopback tests |
| Phase | H3a/H3b/H3c after systems P2q (UDP batch/GSO/GRO), cinterop P2T-a and TLS P2T-b |

### 1.1 Scope

- Server-side HTTP/3 (RFC 9114) over QUIC v1 (RFC 9000) with TLS 1.3
  (RFC 9001) and ALPN `h3` (RFC 7301).
- UDP datagram receive/send with bounded batching, Linux UDP GSO/GRO where
  available, and a correct one-datagram fallback.
- QUIC handshake, transport parameters, packet protection, connection IDs,
  Retry tokens, version negotiation (v1 only), loss recovery, congestion
  control, anti-amplification, stateless reset, key updates, path validation
  and idle/handshake timers.
- HTTP/3 control stream, QPACK encoder/decoder streams, SETTINGS, request and
  response headers, DATA, trailers, GOAWAY, stream limits and bounded QPACK.
- A concrete `quic::Server`/`Connection`/`Stream` shell set over platform
  resources; per-connection event pull; absolute deadlines; explicit
  backpressure; graceful drain.
- A shared `httpapi::RequestMeta`/`Action`/`Handler` seam with HTTP/1.1 and
  HTTP/2, plus concrete HTTP/3 free functions for general streaming handlers.
- Deterministic tests, optional h3spec/aioquic/quic-interop-runner/curl
  integration, ASAN/UBSAN/LSAN and leak gates.

### 1.2 Non-goals

- HTTP/3 client mode, server push, PUSH_PROMISE, CANCEL_PUSH, MAX_PUSH_ID.
- WebTransport (RFC 9297 is HTTP datagrams; WebTransport is a separate
  protocol), HTTP/3 DATAGRAM frames, Extended CONNECT (`:protocol`),
  `CONNECT` tunneling, proxying, gateways or caches.
- QUIC v2 (RFC 9369). ngtcp2 0.12.1 exposes `NGTCP2_PROTO_VER_MAX ==
  NGTCP2_PROTO_VER_V1`; a v2 client gets Version Negotiation listing v1.
- 0-RTT/early data, session resumption, PSK, session tickets.
- Active connection migration and server preferred addresses.
- A new TLS implementation, certificate policy, cipher policy, SNI
  authorization or client-certificate authentication.
- A new QUIC framer, packet protection, loss recovery, congestion controller,
  QPACK codec or HTTP/3 state machine. ngtcp2/nghttp3 supply them; this note
  constrains them.
- Automatic compression, routing, static files, authentication, cookies or
  form parsing.
- Buffering a whole request or response body.
- Per-connection UDP sockets, threads, or a kernel-bypass path.
- Hidden retries, hidden timeout extension, or an automatic finalizer.
- A claim that using ngtcp2/nghttp3 makes the server secure. It removes a
  large protocol attack surface; policy and application behavior remain
  separate.
- A version reservation. All module names, kinds and function names are
  proposals.

## 2. Baseline and environment evidence

### 2.1 Repository contracts used

| Module / artifact | Contract used here | Status at baseline |
|---|---|---|
| `sysplatform` | `Resource`, `requireKind`, `_tokenHandle`, `_adoptToken`, `_closeHandle`, kinds, counts, bounded byte buffers | experimental P2, present |
| `sysnet` | `Datagram`, `bindDatagram`, `receive`, `send`, `closeDatagram`, `datagramLocalAddress` | experimental P2, present; **batch/GSO/GRO/watch are missing** |
| `sysaddress` | checked IPv4/IPv6 `Address`, numeric parse/format | experimental P2, present |
| `sysclock` | `Instant`, `Duration`, `Deadline`, `after`, `remaining`, `forever` | experimental P1, present |
| `sysreactor` | level-triggered `watchResource`, `modify`, `remove`, `timer`, `wait` | experimental P2, present |
| `sysbytes` | `Bytes` shell over a C-owned bounded buffer; `copyIn`/`copyOut` | experimental P0, present |
| `systls` | **TCP-only** TLS 1.2/1.3 adapter over OpenSSL 3.0 | experimental P2T-b, present; explicitly non-QUIC |
| `httpapi` | `Header`, `RequestMeta`, `ProtocolKind`, `Action`, `Handler`, `Failure` | experimental H2a, present; needs `ProtocolHttp3` |
| `httptransport` | concrete plain/TLS/memory byte `Transport` | experimental H2a, present; not used by QUIC |
| `http2` | `Connection`/`Stream`, events, flow control, `runAction` | experimental H2a/H2b/H2c, present |
| `http1` | H1.1 parser/serializer/transport | H1a0/H1b/H1c, present |
| `ngtcp2` | QUIC v1 transport, loss recovery, congestion, CID, packet writer, Retry/VN/stateless-reset helpers | system package, **not installed**; available in apt |
| `nghttp3` | HTTP/3 framing, QPACK, control/QPACK stream state | system package, **not installed**; available in apt |
| `GnuTLS` | TLS 1.3 handshake and QUIC secret callbacks | installed runtime; development headers available |

The systems note's constraints apply unchanged: owned records move, references
are not stored in fields or returned, public fields are untrusted at every
entry, managed allocation failure is fatal, configured capacity exhaustion is
a recoverable `Result`, and no C callback enters obo.

### 2.2 OpenSSL 3.0.13 has no QUIC API

Checked on the actual host:

```text
$ openssl version
OpenSSL 3.0.13 30 Jan 2024 (Library: OpenSSL 3.0.13 30 Jan 2024)

$ nm -D /lib/x86_64-linux-gnu/libssl.so.3 | grep -E 'SSL_QUIC|OSSL_QUIC|QUIC_'
(no output)

$ ls /usr/include/openssl/ssl.h
(absent)
```

The conclusion is not "OpenSSL cannot do TLS 1.3"; it is that **this OpenSSL
3.0.13 build exposes no QUIC integration API**. QUIC needs:

- a TLS handshake whose handshake bytes are carried in QUIC CRYPTO frames, not
  TLS records;
- export of handshake and application traffic secrets at the QUIC encryption
  levels (Initial, Handshake, 0-RTT, 1-RTT);
- QUIC transport parameters in the TLS extension;
- alert and key-update callbacks that ngtcp2 can drive.

OpenSSL 3.0's `SSL_do_handshake` over memory BIOs produces TLS records. Feeding
those records to ngtcp2 is not QUIC. `SSL_set_quic_method` is a BoringSSL/quictls
API, not present in this `libssl.so.3`. `systls` is therefore TCP-only and must
not be extended with a "quic" mode.

### 2.3 ngtcp2, nghttp3 and GnuTLS packages

The packages are **available in the Ubuntu 24.04 archive but not installed** on
the design host. The design downloaded and extracted the exact `.deb` files
without installing them; the C implementation must do the same or install the
matching development packages in a hermetic build image.

| Artifact | Observed value |
|---|---|
| `libngtcp2-9` | `0.12.1+dfsg-1+deb12u1build0.24.04.1` |
| `libngtcp2-crypto-gnutls2` | `0.12.1+dfsg-1+deb12u1build0.24.04.1` |
| `libnghttp3-3` | `0.8.0-2` |
| `libgnutls30t64` | `3.8.3-1.1ubuntu3.6` |
| `libngtcp2-dev` | `0.12.1+dfsg-1+deb12u1build0.24.04.1` (headers required) |
| `libngtcp2-crypto-gnutls-dev` | `0.12.1+dfsg-1+deb12u1build0.24.04.1` |
| `libnghttp3-dev` | `0.8.0-2` |
| `libgnutls28-dev` | `3.8.3-1.1ubuntu3.6` |
| `ngtcp2-client` / `ngtcp2-server` | `0.12.1+dfsg-1+deb12u1build0.24.04.1`; optional `gtlsclient`/`gtlsserver` integration |

Exact library artifacts (extracted from those packages):

| Artifact | SONAME | SHA-256 | ELF build ID |
|---|---|---|---|
| `libngtcp2.so.9.0.1` | `libngtcp2.so.9` | `d43407241de1b62eaefa5ef8d4ff3a448a0e3dbcebfb15881728ccc6ee05564f` | `591bb8439d4607db721c95ad58d0ad7e412b69e1` |
| `libngtcp2_crypto_gnutls.so.2.1.0` | `libngtcp2_crypto_gnutls.so.2` | `b94e341626fd633a6410a08c258074c05d305388d259d2d03bf9269dd19e9b73` | `22e67cffad4f417a36fd43f59d686147353911bc` |
| `libnghttp3.so.3.0.2` | `libnghttp3.so.3` | `2ba2aaa5bcae22b81e9a9af845bd899e538b59879b45fd323b811c3d99c88711` | `1a6497988eb559f16f17d4e931ba54598bc5d424` |
| `/lib/x86_64-linux-gnu/libgnutls.so.30` | `libgnutls.so.30` | `ddbae2995750875c07bc218d12a062d73f8250678f54dedda7e9be5068781c98` | `fafaae89d2fbbdf5bd3837aea5160e38d8991432` |

`libngtcp2.so.9` has `NEEDED libc.so.6` only. `libnghttp3.so.3` has
`NEEDED libc.so.6` only. `libngtcp2_crypto_gnutls.so.2` has
`NEEDED libngtcp2.so.9`, `libgnutls.so.30`, `libc.so.6`.

Exact header hashes (SHA-256), for provenance only:

```text
ngtcp2/ngtcp2.h                 7eeeb66878785c16bd7ab816aca9c0795d78a5742f7b23bdca49449d0888cde7
ngtcp2/ngtcp2_crypto.h          b0406acffec30d47df3582613f378a9681d3040aee0a92f251048ec5edebe034
ngtcp2/ngtcp2_crypto_gnutls.h   d6cf8b0a3f6b3f274f6317e138a4b6ae8d922f06763e0dca3ec8f31fe4beb6db
nghttp3/nghttp3.h               27601c8a4121b5d5abcd691311fbe0378d4625b2901c96dd760dfa6196fb841a
gnutls/gnutls.h                 8cc0c3459760d41598df7a666a5c24a4a13f01f684f0add149d594e2395ca633
```

Package `.deb` hashes, for a reproducible build sysroot:

```text
libngtcp2-dev_0.12.1+dfsg-1+deb12u1build0.24.04.1_amd64.deb
  998bba82cca9342565d3dd497df16de190f40ec91b69f2656018a777b937ba57
libngtcp2-crypto-gnutls-dev_0.12.1+dfsg-1+deb12u1build0.24.04.1_amd64.deb
  4cfcb9ba1fa2d193a615f8b30311290471920d8121a73968165541cf1f83ae95
libnghttp3-dev_0.8.0-2_amd64.deb
  38a88f452e32cd290083e2f245798ecb0c476e9997ea8b0ddaddf6cda5d772c9
libgnutls28-dev_3.8.3-1.1ubuntu3.6_amd64.deb
  b13e52a43f18e8fdb49e37150cd20c14ec79b42aead1facc5006cb217122a777
libngtcp2-9_0.12.1+dfsg-1+deb12u1build0.24.04.1_amd64.deb
  29eaa0757a5873d06b952ab41776fa9a2633fdd66cbad00ba5c6abbaf3de630f
libngtcp2-crypto-gnutls2_0.12.1+dfsg-1+deb12u1build0.24.04.1_amd64.deb
  689bb7a96ee21739b0d9cc8ffa05b293defcb123f8630771d908ae7d47bff0d6
libnghttp3-3_0.8.0-2_amd64.deb
  d1aa1d91c16d30fcc19209453aa1dbe4976afd16be3aa75e637e9d6ccee83910
```

Version and license facts:

- `NGTCP2_VERSION "0.12.1"`, `NGTCP2_VERSION_NUM 0x000c01`,
  `NGTCP2_VERSION_AGE 1`. `NGTCP2_PROTO_VER_MAX == NGTCP2_PROTO_VER_V1`
  (`0x00000001`). QUIC v2 is not supported by this version.
- `NGHTTP3_VERSION "0.8.0"`, `NGHTTP3_VERSION_NUM 0x000800`,
  `NGHTTP3_VERSION_AGE 1`.
- `libngtcp2-crypto-gnutls2` depends on `libgnutls30t64 (>= 3.8.2)`; the
  installed runtime is 3.8.3.
- ngtcp2 and nghttp3 are MIT. GnuTLS is LGPL-2.1-or-later. Record the exact
  package copyright files in the provenance manifest.

**Security/provenance pin.** The Ubuntu ngtcp2 package revision
`+deb12u1build0.24.04.1` contains a backport for **CVE-2026-40170** (Debian
`bookworm-security` entry, 2026-04-19). A pristine upstream 0.12.1 tarball does
not contain that patch. The build must pin the patched Ubuntu package (or a
later package with the same `NGTCP2_VERSION_NUM` and a reviewed patch set),
not an arbitrary upstream 0.12.1 build. `libnghttp3-3 0.8.0-2` has no Ubuntu
security update in this archive; that is a recorded risk, not a claim of
safety. Upgrading nghttp3 or ngtcp2 beyond these exact API versions is a
reviewed port, because 0.x APIs are not ABI-stable.

### 2.4 Tooling

| Tool | Status | Use |
|---|---|---|
| `curl 8.5.0` | present; `libcurl/8.5.0` with `nghttp2/1.59.0` and OpenSSL 3.0.13; **features line has no HTTP3** | system curl cannot test HTTP/3 |
| `openssl 3.0.13` | present | TCP TLS fixtures only; no QUIC client/server |
| `gtlsclient` / `gtlsserver` | packages available (`ngtcp2-client`/`ngtcp2-server` 0.12.1), not installed | optional HTTP/3 interop |
| `h3spec` | **absent** | pin exact release/hash in the integration job |
| `quic-interop-runner` | **absent** | optional Docker-based interop job |
| `aioquic` | **absent**; Python 3.12.3 present | optional scripted HTTP/3 client |
| `go 1.26.4` | present | differential HTTP/3 client/server if a Go QUIC stack is pinned |
| ASAN/UBSAN/LSAN | available through gcc/clang | C shim and hosted server |
| Linux UDP GSO/GRO | kernel feature; not assumed | `UDP_SEGMENT`/`UDP_GRO`; fallback required |

No default test downloads a tool or touches the network. h3spec, aioquic,
quic-interop-runner, gtlsclient and a curl HTTP/3 build are explicit
integration dependencies with recorded versions and hashes; a missing tool is
a skip only where the phase says so. The only optional network action is
`apt-get download` of the exact matching development packages when they are
not installed, as the H2 suite already does.

## 3. Implementation strategy

### 3.1 Native ngtcp2/nghttp3 versus the alternatives

**Recommendation: bind the system ngtcp2 0.12.1 and nghttp3 0.8.0 through one
trusted C shim, with GnuTLS 3.8.3 for QUIC-TLS. Do not write QUIC or HTTP/3 in
obo; do not vendor BoringSSL/quictls/quiche/msquic; do not make a sidecar the
primary design.**

| Option | Verdict | Reason |
|---|---|---|
| **system ngtcp2 + nghttp3 + GnuTLS via a C shim** | **Recommended** | Complete QUIC v1 transport, loss recovery, congestion, CID/Retry/VN/stateless-reset helpers, HTTP/3/QPACK; MIT; available as pinned Ubuntu packages; GnuTLS 3.8 already installed; OpenSSL is not usable for QUIC here |
| BoringSSL | Reject for v1 | No system package or stable ABI; vendoring a second TLS/crypto stack; API churn; provenance and audit burden |
| quictls (OpenSSL fork) | Reject | No system package; a second OpenSSL in the process; symbol/provider/interposition ambiguity with the existing `systls` and libpq; no stable ABI |
| quiche (Rust) | Reject for v1 | No system package; second TLS stack (BoringSSL); FFI plus a new Cargo build and audit; duplicates ngtcp2/nghttp3 |
| msquic | Reject for v1 | No Ubuntu package in this archive; Windows-centric build; another TLS integration and ABI |
| From-scratch QUIC/HTTP/3/QPACK | Reject | 20k+ lines of crypto-adjacent, loss-recovery and state-machine code; CVE exposure; no review capacity |
| Sidecar/edge terminator as primary | Reject as primary | See §3.4 and §11; it does not give obo HTTP/3 semantics or the shared handler seam |
| Sidecar as a temporary deployment bridge | Allowed fallback | Pinned nginx/Caddy/Envoy over loopback h1.1/h2; explicitly not the native design |

ngtcp2/nghttp3 do not remove the need for this design. They supply packet
protection, ACK/loss/PTO, congestion, stream flow control, QPACK and HTTP/3
frame grammar. obo and the shim must still enforce admission, UDP batching,
CID routing, Retry policy, header/body caps, deadlines, per-stream fairness,
ALPN and certificate policy, drain, error mapping and the handler seam.

### 3.2 The TLS-QUIC gap and the separate `quictls` unit

`quictls` is a **separate internal trusted-C module** compiled into the `q3`
shim at `obo/experimental/http3/native/quictls.{h,c}`. It is not a public obo
module and it is not `systls`. It has its own GnuTLS context/session, its own
certificate loading and its own error mapping. No `gnutls_session_t`,
`gnutls_certificate_credentials_t`, `ngtcp2_conn*` or `nghttp3_conn*` crosses
the cbind.

| QUIC-TLS need | OpenSSL 3.0.13 | GnuTLS 3.8.3 + `ngtcp2_crypto_gnutls` |
|---|---|---|
| TLS 1.3 handshake over QUIC CRYPTO frames | no QUIC API | `gnutls_handshake_set_read_function` and `ngtcp2_crypto_read_write_crypto_data` |
| Export handshake/application secrets | no | `gnutls_handshake_set_secret_function`; `ngtcp2_crypto_derive_and_install_{rx,tx}_key` |
| QUIC transport parameters extension | no | registered by `ngtcp2_crypto_gnutls_configure_server_session` |
| Alert delivery | not usable for QUIC | `gnutls_alert_set_read_function` |
| ALPN `h3` mandatory | possible for TCP only | `gnutls_alpn_set_protocols(..., GNUTLS_ALPN_MANDATORY)` |
| 0-RTT | TLS API exists, not QUIC-integrated | GnuTLS early-data API; **disabled in v1** |
| Key update | no QUIC callback | `ngtcp2_crypto_update_key_cb` |
| Certificates/keys | OpenSSL PEM | GnuTLS `gnutls_certificate_set_x509_key_mem` |
| Process-global init | `OPENSSL_init_ssl` | `gnutls_global_init` |

`ngtcp2_crypto_gnutls_configure_server_session(session)` installs the GnuTLS
secret/read/alert callbacks and the transport-parameters extension. The shim
must set `gnutls_session_set_ptr(session, &conn_ref)` with a C-owned
`ngtcp2_crypto_conn_ref` whose `get_conn` returns the `ngtcp2_conn*`, and call
`ngtcp2_conn_set_tls_native_handle(conn, session)`. Those pointers stay in C.

`quictls` policy:

- TLS 1.3 only. A GnuTLS priority string equivalent to
  `NORMAL:-VERS-ALL:+VERS-TLS1.3`; no TLS 1.2, no fallback.
- Cipher suites: `TLS_AES_128_GCM_SHA256`, `TLS_AES_256_GCM_SHA384`,
  `TLS_CHACHA20_POLY1305_SHA256`. Groups: `X25519`, `SECP256R1`, `SECP384R1`.
  No export/legacy suites.
- ALPN mandatory `h3`. A missing or non-`h3` ALPN fails the handshake with
  `no_application_protocol`; no fallback to HTTP/1.1 or h2.
- One certificate chain and matching key per context, loaded from bounded PEM
  bytes. No path access in the C module. Certificate chain <=1 MiB and <=16
  certs; key <=64 KiB.
- SNI is captured for diagnostics only. It does not select a certificate and
  is never authorization in v1.
- No client-certificate request in v1. If client auth is added, it needs a
  separate trust and authorization design.
- No session tickets, no resumption, no 0-RTT. No ticket key is installed;
  `gnutls_record_set_max_early_data_size(session, 0)`. The integration test
  asserts no NewSessionTicket and no accepted early data; if the pinned GnuTLS
  still issues a ticket without an explicit key, the adapter disables server
  ticket issuance before accepting and the test records the mechanism.
- No OCSP fetch, no CRL fetch, no blocking verification, no hidden thread.
- `gnutls_global_init()` is process-global and called once before the first
  context. The design is hosted single-threaded; worker TLS is a later
  qualification.

### 3.3 C shim boundary

The shim is trusted C, reviewed like the TLS and H2 adapters, at
`obo/experimental/http3/native/`. It owns:

- `ngtcp2_conn*`, its callbacks, CID routing table and Retry/stateless-reset
  secrets;
- `nghttp3_conn*` and its callbacks, per-stream request/response buffers;
- the GnuTLS session and `ngtcp2_crypto_conn_ref`;
- the fixed server and per-connection event rings;
- the bounded wire batch and per-stream receive/send rings;
- the connection/stream tables, generations and deferred-free sweeps.

obo owns the `Server`, `Connection` and `Stream` shells, admission, policy,
deadlines and the handler seam. The cbind exposes only `i64` arguments/results
and platform byte-buffer tokens. No `ngtcp2_*`, `nghttp3_*`, `gnutls_*` or
`void*` pointer, C struct by value, function pointer or variadic argument
appears in the cbind. The library callbacks are C-internal function pointers;
they never appear in the cbind and never call obo. This is the systems design's
allowed pattern: "native callbacks, if needed by a library, remain in C and
queue bounded events for an obo polling call."

Representative C surface (names are implementation details, not a public ABI):

| C entry | Arguments | Result |
|---|---|---|
| `q3_init` | none | `1` compatible, negative failure |
| `q3_context_new` | options packet token, length | context token, negative status |
| `q3_context_free` | context token | `1`/`0` |
| `q3_server_new` | context token, limits packet token, length | server token, negative status |
| `q3_server_free` | server token | `1`/`0` |
| `q3_server_feed` | server token, buffer token, offset, count, segment size, peer packet token, length, now ns | segments consumed, negative status |
| `q3_server_next` | server token, now ns | event type, `0` none, negative status |
| `q3_server_event_conn` | server token | connection id |
| `q3_server_event_stream` | server token | stream id |
| `q3_server_event_flags` | server token | flags (new, fin, local, path) |
| `q3_server_event_error` | server token | QUIC/H3 error code |
| `q3_server_event_aux` | server token | last-stream-id, byte count, deadline |
| `q3_server_event_peer` | server token, out packet token | peer address packet length |
| `q3_server_event_clear` | server token | `1`/`0` |
| `q3_server_send` | server token, out buffer token, max bytes, max datagrams, info packet token | bytes queued, `0`, negative status |
| `q3_server_deadline` | server token | earliest absolute ns, or `-1` |
| `q3_server_stats` | server token, out packet token | packet length |
| `q3_conn_adopt` | server token, connection id | connection token, negative status |
| `q3_conn_abandon` | connection token | `1`/`0` (idempotent) |
| `q3_conn_markAdopted` | connection token | `1`/`0` |
| `q3_conn_next` | connection token, now ns | event type, `0` none, negative status |
| `q3_conn_deadline` | connection token | earliest absolute ns, or `-1` |
| `q3_conn_drain` | connection token | submits first GOAWAY, `1`/negative |
| `q3_conn_drain_poll` | connection token, now ns | `1`/`0`/negative |
| `q3_conn_goaway` | connection token, last stream id, error code | submits final GOAWAY |
| `q3_conn_close` | connection token, error code | `1`/negative |
| `q3_conn_stats` | connection token, out packet token | packet length |
| `q3_conn_info` | connection token, out packet token | SNI/ALPN/path/stats packet |
| `q3_h3_enable` | connection token, H3 settings packet token, length | `1`/negative |
| `q3_h3_stream_adopt` | connection token, stream id | stream token, negative status |
| `q3_h3_stream_markAdopted` | stream token | `1`/`0` |
| `q3_h3_stream_abandon` | stream token | `1`/`0` (idempotent) |
| `q3_h3_stream_close` | stream token | `1`/`0` |
| `q3_h3_stream_state` | stream token | state enum |
| `q3_h3_request_info` | stream token, out packet token | packet length |
| `q3_h3_header_at` | stream token, index, out token, which | length |
| `q3_h3_trailer_at` | stream token, index, out token, which | length |
| `q3_h3_read_body` | stream token, out buffer token, max, now ns | bytes copied, `0`, negative status |
| `q3_h3_discard_body` | stream token, max, now ns | discarded bytes, negative status |
| `q3_h3_begin_response` | stream token, status | `1`/negative |
| `q3_h3_set_header` | stream token, name token, value token | `1`/negative |
| `q3_h3_set_length` | stream token, length | `1`/negative |
| `q3_h3_write_body` | stream token, buffer token, offset, count | accepted bytes, negative status |
| `q3_h3_trailer` | stream token, name token, value token | `1`/negative |
| `q3_h3_end_response` | stream token | `1`/negative |
| `q3_h3_reset` | stream token, H3 error code | `1`/negative |
| `q3_h3_stats` | stream token, out packet token | packet length |

Fixed event records (one shared type for the server and per-connection rings):

```c
typedef struct {
  uint32_t type;        /* Q3_EV_* */
  int32_t  conn_id;     /* 0 for server-level */
  int64_t  stream_id;   /* 0 for connection-level */
  uint32_t flags;       /* bidi, fin, local, path, trailers */
  uint32_t error;       /* QUIC/H3/library error code */
  int64_t  aux;         /* deadline, last-stream-id, byte count */
} q3_event;
```

| C event | obo event | Meaning |
|---|---|---|
| `Q3_EV_NEED_READ` | `EventNeedRead` | socket level |
| `Q3_EV_NEED_WRITE` | `EventNeedWrite` | pending output |
| `Q3_EV_CONNECTION_NEW` | `EventConnectionNew` | first Initial accepted |
| `Q3_EV_HANDSHAKE_DONE` | `EventHandshakeDone` | TLS handshake complete |
| `Q3_EV_STREAM_OPENED` | `EventStreamOpened` | remote stream opened |
| `Q3_EV_STREAM_DATA` | `EventStreamData` | receive ring has bytes |
| `Q3_EV_STREAM_END` | `EventStreamEnd` | peer FIN |
| `Q3_EV_TRAILERS` | `EventTrailers` | trailer block complete |
| `Q3_EV_STREAM_CLOSED` | `EventStreamClosed` | reset/close |
| `Q3_EV_PATH_CHANGED` | `EventPathChanged` | validated path update |
| `Q3_EV_GOAWAY` | `EventGoaway` | peer GOAWAY |
| `Q3_EV_DRAIN_COMPLETE` | `EventDrainComplete` | local drain finished |
| `Q3_EV_TIMED_OUT` | `EventTimedOut` | deadline |
| `Q3_EV_PROTOCOL_ERROR` | `EventProtocolError` | fatal connection error |
| `Q3_EV_CONNECTION_CLOSED` | `EventConnectionClosed` | terminal |

Events carry no header/body payload. Header names/values and body bytes are
pulled from bounded per-stream C buffers via the `q3_h3_*_at`/`read_body`
entries. Event-ring overflow is a connection failure, never silent loss; the
server ring overflow stops accepting new connections and closes the server.

The exact set is finalized with implementation. The boundary rules are fixed:
scalar/token only, bounded buffers, no pointer, no callback into obo.

### 3.4 Why not a sidecar as the design

A sidecar/edge terminator (nginx 1.25+, Caddy 2.7+, Envoy, HAProxy 2.6+ with
QUIC, or a cloud load balancer) can terminate HTTP/3 and forward HTTP/1.1 or
HTTP/2 to obo over loopback or a Unix socket. It is operationally real and can
be a temporary deployment bridge. It is **not** the primary design because:

- obo would not implement HTTP/3; it would implement h1/h2 behind a QUIC
  proxy. The shared `httpapi::Handler` would still be an h1/h2 handler, and
  `RequestMeta.protocol` would be `ProtocolHttp11`/`ProtocolHttp2`, not H3.
- HTTP/3-specific semantics are terminator-owned: GOAWAY, QPACK limits,
  0-RTT policy, migration, connection coalescing, stream resets, trailers and
  full-duplex request/response behavior. obo cannot enforce or test them.
- The terminator needs the certificate and private key, or a re-encryption
  hop; that changes the TLS/provenance boundary and the deployment's key
  custody.
- It adds a process/container, a second HTTP stack, health/reload/logging
  surface and a loopback hop. It does not reduce total attack surface; it
  moves it.
- Request metadata is lossy: client address, SNI, ALPN, QUIC transport
  parameters, 0-RTT status and connection identity are not faithfully
  representable in h1/h2 headers. `X-Forwarded-For` is not an identity.
- A sidecar cannot share obo's `Admission`, connection counts, deadlines or
  per-stream backpressure. Two independent admission domains can oversubscribe
  the process.

The fallback, if native H3 slips, is a pinned terminator with an explicit
loopback profile: obo `ProtocolHttp11`, one request per connection or h2
multiplexing, `X-Forwarded-For` trusted only from a configured loopback peer,
and a documented statement that HTTP/3 is terminated outside obo. It is not a
substitute for H3a/H3b/H3c. §11 records the rejected sidecar-as-primary option.

### 3.5 Headers versus declarations-only

**Decision: require development headers for ngtcp2, nghttp3 and GnuTLS.
Reject declarations-only.**

Reasons:

- The shim uses `ngtcp2_callbacks`, `ngtcp2_settings`,
  `ngtcp2_transport_params`, `ngtcp2_path`, `ngtcp2_pkt_hd`,
  `ngtcp2_cid`, `nghttp3_callbacks`, `nghttp3_settings`, `nghttp3_nv`,
  `nghttp3_vec`, GnuTLS types and many enum constants. These are not a small
  stable scalar surface. A stale struct layout or enum silently changes
  protocol behavior; the linker cannot catch it.
- The callback typedefs are ABI. Hand-copying them is a second unreviewed ABI.
- The packages are exact, cheap to pin and available without root. The H2 and
  TLS designs already require the matching development headers for the same
  reason.
- A declarations-only **adapter** header (`q3_adapter.h`) is still correct for
  the functions the shim exports: `long q3_*(long, ...)`.
- Do not copy ngtcp2/nghttp3/GnuTLS headers into the repository. They are
  build dependencies, not vendored source.

Provenance and test strategy:

- Generate a private `.cbind` with absolute `include_dirs` for the extracted
  development package roots. The committed `.cbind` has no machine-specific
  paths.
- Link `-l:libngtcp2.so.9`, `-l:libngtcp2_crypto_gnutls.so.2`,
  `-l:libnghttp3.so.3` and `-l:libgnutls.so.30`. Do not use unversioned
  `-lngtcp2`/`-lnghttp3`, whose development symlinks may resolve differently.
- Keep full RELRO, immediate binding and a non-executable stack from the
  cinterop hosted link.
- Record in the manifest: package versions, absolute library paths, SHA-256,
  build IDs, header hashes, `NGTCP2_VERSION_NUM`, `NGHTTP3_VERSION_NUM`,
  GnuTLS version, `signature_check: header`, include paths, cflags, compiler
  and manifest hash.
- Compile-time checks:
  `#if NGTCP2_VERSION_NUM != 0x000c01 #error ...` and
  `#if NGHTTP3_VERSION_NUM != 0x000800 #error ...` for the pinned ABI, plus
  compile references to the exact callback fields and enum constants used.
  A later version is a reviewed port, not a silent upgrade.
- Runtime checks before the first context: `ngtcp2_version(0)` has age 1 and
  `version_num == 0x000c01`; `nghttp3_version(0)` has age 1 and
  `version_num == 0x000800`; `gnutls_check_version("3.8.2")` succeeds. The
  loader's SONAME checks are the real ABI checks; do not hash the loaded
  library at runtime.
- Header-mode negative test: a deliberately wrong declaration (wrong arity or
  struct field) must fail the generated signature check. A modified header
  must fail `verify`.

## 4. QUIC transport requirements

QUIC is not "TLS over UDP". The transport owns packet numbers, ACK/loss
recovery, congestion control, connection IDs, path validation, anti-amplification
and packet protection. The design puts all of that in ngtcp2 and keeps obo
responsible for I/O, policy, buffers and deadlines.

### 4.1 What ngtcp2 supplies, what the shim supplies, what obo supplies

| Requirement | ngtcp2 / crypto library | C shim | obo / sysnet |
|---|---|---|---|
| UDP socket, bind, options, close | — | — | `sysnet::Datagram`; `bindDatagramOptions`, `closeDatagram` |
| Receive batching / GRO | — | splits segments for `ngtcp2_conn_read_pkt` | `receiveBatch`, `Packet.segmentSize`, `watchDatagram` |
| Send batching / GSO | — | packs complete datagrams; returns segment size | `sendBatch` with `UDP_SEGMENT`; fallback one-by-one |
| Packet parse/protection, ACK, loss, PTO, congestion | yes | drives `read_pkt`/`writev_stream` | supplies `now`, send budget |
| Connection IDs, routing, retirement | CID generation callback and transport state | CID table, stateless-reset tokens, tombstones | no CID visibility |
| Retry token, address validation | token crypto + `ngtcp2_accept`/`write_retry` | policy, secret rotation, token lifetime | `RetryMode` option |
| Version negotiation | `ngtcp2_accept`, `ngtcp2_pkt_write_version_negotiation` | lists v1, drops bad packets | no |
| Anti-amplification (3x) | yes | must not bypass; Retry first | no |
| Stateless reset | `ngtcp2_pkt_write_stateless_reset` | token derivation, rate limit, size rule | no |
| 0-RTT/early data | GnuTLS/ngtcp2 flags | policy; v1 rejects | no |
| Migration/path validation | ngtcp2 path validation | accept NAT rebinding; `disable_active_migration` | no |
| Key update / AEAD limit | `ngtcp2_crypto_update_key_cb` | maps failure; no key material | no |
| Transport parameters | validation | maps limits to params | `Limits` validated |
| Idle/handshake timers | `get_expiry`/`handle_expiry` | converts to absolute ns | reactor timer + deadlines |
| Stream flow control | QUIC windows | consume after copy; extend offsets | per-stream buffers |
| HTTP/3 framing/QPACK | nghttp3 | callbacks, bounded copies | H3 limits/policy |
| Admission and fairness | — | fixed tables, event/wire bounds | `Admission`, per-turn caps |
| Handler semantics | — | — | `httpapi::Handler` + concrete functions |

### 4.2 UDP socket, GSO and GRO

The v1 `sysnet::Datagram` contract (systems design §7.2) has single-datagram
`receive`/`send`, no watch helper and explicitly defers `sendmmsg`/`recvmmsg`.
HTTP/3 needs a bounded batch extension. Add it to `sysnet` as **P2q**, not as a
private socket in the QUIC shim:

```obo
pub type DatagramOptions = ref record {
    pub receiveBuffer: int       // SO_RCVBUF, default 4 MiB
    pub sendBuffer: int          // SO_SNDBUF, default 4 MiB
    pub reusePort: bool          // explicit, default false
    pub gro: bool                // try UDP_GRO, default true
    pub gso: bool                // try UDP_SEGMENT, default true
    pub dontFragment: bool       // IP_MTU_DISCOVER, default true
}

pub type Packet = ref record {
    pub bytes: []byte
    pub peer: ?address::Address
    pub truncated: bool
    pub originalSize: int
    pub segmentSize: int         // 0 = one datagram; >0 = GRO-coalesced
}

pub fn bindDatagramOptions(a: &address::Address,
                           o: &DatagramOptions): Result[Datagram]
pub fn receiveBatch(d: &Datagram, maxPackets: int,
                    maxBytes: int): Result[[]Packet]
pub fn sendBatch(d: &Datagram, to: &address::Address, bytes: &[]byte,
                 offset: int, count: int, segmentSize: int): Result[int]
pub fn watchDatagram(r: &reactor::Reactor, d: &Datagram,
                     tag: int): Result[reactor::Watch]
```

Rules:

- `bindDatagram` keeps its current signature and uses defaults. The new
  `bindDatagramOptions` is the QUIC path; it revalidates every field, sets
  `CLOEXEC` and nonblocking, and closes the socket on any post-bind failure.
- `receiveBatch` requires `1 <= maxPackets <= 64` and
  `1 <= maxBytes <= 65536`; it returns up to that many packets and never
  silently truncates. `truncated` and `originalSize` are preserved. A single
  `recvmmsg` call fills the batch; extra datagrams remain in the socket.
- If `UDP_GRO` is enabled and the kernel returns a coalesced buffer,
  `segmentSize` is the GRO segment size and `bytes` contains all segments; the
  final segment may be shorter. If GRO is unavailable or disabled,
  `segmentSize == 0`. The consumer must be able to handle both.
- `sendBatch` with `segmentSize == 0` sends exactly one datagram, all-or-error,
  like `send`. With `segmentSize > 0` it uses one `sendmsg` with a
  `UDP_SEGMENT` control message; all segments but the last must be exactly
  `segmentSize`, and the call is all-or-error. If GSO is unavailable, return
  `Unsupported`; the caller falls back to repeated one-datagram sends. Do not
  silently loop a partial batch inside `sendBatch`.
- `watchDatagram` is the only readiness route. The typed helper validates the
  datagram's platform resource and calls `watchResource`; it exposes no fd.
  Level-triggered only.
- Linux is the v1 target. `UDP_SEGMENT` requires Linux 4.18+;
  `UDP_GRO` requires Linux 4.18+. `ENOPROTOOPT`/`EINVAL` disables the feature,
  not correctness. IPv4 and IPv6 are supported; Unix datagrams are rejected
  for QUIC.
- `dontFragment` sets `IP_MTU_DISCOVER=IP_PMTUDISC_DO` (IPv4) or
  `IPV6_MTU_DISCOVER=IPV6_PMTUDISC_DO` (IPv6). With `maxUdpPayload=1200` and
  PMTUD disabled in v1, no datagram exceeds the IPv6 minimum MTU.
- Socket buffers are explicit. A full receive buffer drops datagrams; QUIC
  retransmits. No unbounded kernel queue is assumed.

`maxUdpPayload=1200`, `maxBatchDatagrams=16`, `maxWire=65536`. At most 16
datagrams of 1200 bytes (19,200 bytes) are built per send batch. Larger
responses are split across turns; there is no hidden send loop.

### 4.3 Server demux and connection IDs

One UDP socket serves every QUIC connection on a listener. A QUIC connection
is identified by a Destination Connection ID, not by a peer address or a file
descriptor.

- On the first client Initial, the shim calls `ngtcp2_accept`. It validates
  version, DCID length (>= 8 for the initial DCID), packet size (>= 1200 for
  Initial), and the token policy. Invalid packets are dropped before any
  connection state is allocated.
- The server generates an 8-byte SCID with `getrandom`. It does not encode the
  peer address, port, time or a sequence number. The CID is a random routing
  key, not a capability.
- The shim keeps a C-only hash table from CID bytes to connection record and
  generation. It supports the initial SCID plus up to
  `active_connection_id_limit=4` issued CIDs. `get_new_connection_id` fills a
  random CID and a stateless-reset token derived with
  `ngtcp2_crypto_generate_stateless_reset_token(token, secret, cid)`. Old CIDs
  are retired on `remove_connection_id`.
- `original_dcid` and `retry_scid` are set in `ngtcp2_transport_params`
  exactly as RFC 9000 requires, including after Retry; `initial_scid` is
  library-owned and must not be set by the application. The Retry token
  contains the original DCID and is authenticated. The
  initial SCID's stateless-reset token is derived the same way and published
  with `stateless_reset_token_present=1`; later CIDs get their tokens in the
  `get_new_connection_id` callback.
- On connection close, CIDs are removed from the routing table. A bounded
  tombstone keeps the stateless-reset token for 3 PTO so late packets get a
  reset instead of a new handshake. Tombstones are capped
  (`maxStatelessResetsOutstanding=256`) and evicted oldest-first.
- The peer address is not an identity. A NAT rebinding changes the path; it
  does not change the connection. The shim reports `PathChanged` and updates
  its stored `ngtcp2_path`.

### 4.4 Retry, tokens and amplification

- Default `RetryMode` is `Always` on a public UDP listener. Loopback test
  profiles may set `Never` for deterministic packet traces.
- On `NGTCP2_ERR_RETRY` from `ngtcp2_accept`/`read_pkt`, the shim generates a
  Retry token with `ngtcp2_crypto_generate_retry_token` and writes the packet
  with `ngtcp2_crypto_write_retry`. The token is AEAD-protected with a
  32-byte context secret, binds the client address, version, Retry SCID and
  original DCID, and is valid for 10 seconds.
- On the retried Initial, `ngtcp2_crypto_verify_retry_token` must succeed
  before `ngtcp2_conn_server_new`. The extracted original DCID becomes
  `params.original_dcid`; the Retry SCID becomes `params.retry_scid` with
  `retry_scid_present=1`; and `settings.token` points at the verified token
  bytes (ngtcp2 makes a copy).
- Token failure is `INVALID_TOKEN` (or a silent drop where the RFC permits);
  never a connection with unvalidated state.
- The secret is generated at context creation with `getrandom`, kept only in
  C, and rotated after `retrySecretRotateMs=86400000`. Old and new secrets are
  accepted during a 2x lifetime overlap. A restart invalidates outstanding
  tokens; clients retry.
- Even with Retry, ngtcp2 enforces the 3x anti-amplification limit before
  address validation. The shim never writes more bytes than
  `ngtcp2_conn_writev_stream` returns, and never sends application data before
  the handshake is confirmed. A Retry packet itself is bounded and not
  amplified.
- No `NEW_TOKEN` is issued in v1. Retry tokens are enough for address
  validation; a future NEW_TOKEN policy needs its own replay/rotation review.

### 4.5 Packet protection, loss recovery and congestion

- Packet protection, header protection, AEAD, packet number spaces, ACK
  generation, PTO, loss detection, congestion control and path MTU are
  ngtcp2's. The shim never implements crypto or a retransmission timer.
- `ngtcp2_settings.cc_algo = NGTCP2_CC_ALGO_CUBIC`. Reno/BBR are available but
  are not exposed in v1. Congestion is not application-scheduled.
- `max_tx_udp_payload_size=1200`, `no_pmtud=1`. PMTUD is deferred; a later
  phase may raise the payload after GSO/GRO and black-hole tests.
- `initial_rtt=100ms`, `ack_thresh=2`, default `max_ack_delay`; these are
  ngtcp2 defaults unless the limits packet overrides them for tests.
- The shim calls `ngtcp2_conn_get_expiry(conn)`; the minimum over all
  connections is the server deadline. When `now >= expiry`, it calls
  `ngtcp2_conn_handle_expiry(conn, now)` and then writes packets. C never
  reads a clock; obo passes monotonic nanoseconds.
- A `NGTCP2_ERR_*` from `read_pkt`/`writev_stream` is mapped to the shared
  failure family. `NGTCP2_ERR_DRAINING` is not a new error; it means the
  connection is closing.
- The shim must not call `read_pkt` from inside a callback, and must not free a
  connection inside a callback. Callbacks only mark state and enqueue fixed
  events; the post-`read`/post-`write` sweep retires closed objects.

### 4.6 Timers and clock

All deadlines are absolute monotonic nanoseconds from `sysclock::Deadline`.
C stores `int64` nanoseconds plus a finite flag. C never calls `clock_gettime`.

| Timer | Owner | Rule |
|---|---|---|
| QUIC handshake | ngtcp2 | `handshake_timeout=10s` from `initial_ts`; expiry -> close |
| QUIC loss/PTO/idle | ngtcp2 | `get_expiry`/`handle_expiry`; obo arms a reactor timer |
| HTTP/3 header | obo | 10s from first request byte |
| HTTP/3 body idle | obo | 30s, renewed only by decoded body progress |
| HTTP/3 response idle | obo | 60s, renewed only by a positive datagram write |
| HTTP/3 stream total | obo | 300s from request stream open |
| Connection idle | ngtcp2 + obo | `max_idle_timeout=30s`; no live requests |
| Drain barrier | obo | one PTO or 1s after GOAWAY notice |
| Drain total | obo | 30s |
| Retry token | shim | 10s validity |
| Stateless-reset tombstone | shim | 3 PTO, capped table |

`deadline(server)` is the minimum finite deadline across all connections and
server-level timers. `deadline(conn)` is the minimum for one connection. Only
finite deadlines create `reactor::timer`s; `forever()` is valid for
`reactor::wait` but not for `reactor::timer`. `next` checks deadlines before
and after I/O so a timeout is not delayed behind a busy batch.

### 4.7 0-RTT and early data

**v1 decision: disabled.** There is no session ticket, no PSK, no early data,
and no `Early-Data` header. The server does not call
`gnutls_record_set_max_early_data_size` with a nonzero value and does not
install a ticket key. If a client sends 0-RTT data without an accepted ticket,
the TLS handshake rejects it; ngtcp2 discards the early packets. The shim still
recognizes `NGTCP2_STREAM_DATA_FLAG_EARLY` and treats any early application
bytes as a protocol error/drop, never as a request.

If 0-RTT is enabled later, the minimum policy is: single-use ticket, <=10s
ticket age, <=16 KiB early data, `GET`/`HEAD` only, no authentication, no
state-changing methods, no request body, replay cache keyed by ticket nonce,
and `425 Too Early` for anything else. That is a separate review; it is not a
hidden H3b deliverable.

### 4.8 Migration and path validation

- v1 sets `disable_active_migration=1` in the server transport parameters. A
  conforming client does not actively migrate; NAT rebinding can still change
  the observed path.
- On a path change, ngtcp2 performs path validation (PATH_CHALLENGE/
  PATH_RESPONSE). The shim accepts a validated path only if the peer address is
  a numeric IPv4/IPv6 address, the CID is live, and the change rate is below
  `maxPathChangesPerMinute=4`.
- No preferred address is advertised. No server-initiated migration. No
  connection ID is bound to a path.
- A failed validation closes with `NO_VIABLE_PATH` (0x10). The old path remains
  usable until the new path is validated, per RFC 9000.
- A path change does not reset HTTP/3 state, request deadlines or admission.
  It only updates the stored peer address and emits `EventPathChanged`.

### 4.9 Stateless reset and version negotiation

- Stateless reset is sent only for a packet with an unknown DCID whose length
  is >= 21 bytes and for which a reset token is known. The response is a
  short-header packet with a random length in `[21, 40]`, no larger than the
  triggering packet, written by `ngtcp2_pkt_write_stateless_reset`.
- Rate limits: global `maxStatelessResetsPerSecond=100`, per-peer
  `maxStatelessResetsPerPeerPerSecond=4`, token table capped at 256. A reset
  is never a new connection and never allocates a `Connection` shell.
- Version negotiation: `ngtcp2_accept` returns
  `NGTCP2_ERR_VERSION_NEGOTIATION` for a long-header Initial with an
  unsupported version. The shim writes a Version Negotiation packet with
  `ngtcp2_pkt_write_version_negotiation` listing only
  `NGTCP2_PROTO_VER_V1` and drops the packet. It does not create state and does
  not echo the client's version as supported. A v2-only client fails and may
  retry v1; there is no v2 support in this pin.
- A Version Negotiation packet is not sent in response to a packet smaller than
  the minimum Initial size or to a non-Initial long-header packet.

### 4.10 Connection close and draining

- `quic::close(conn)` is abortive: send CONNECTION_CLOSE with the mapped
  transport or application error, flush the datagram once, remove CIDs, keep a
  stateless-reset tombstone for 3 PTO, then free the C object. It does not
  close the UDP socket.
- `http3::drain(conn)` is graceful: HTTP/3 GOAWAY notice, one PTO, final
  GOAWAY, finish live requests, then CONNECTION_CLOSE with `H3_NO_ERROR`
  (0x0100) and the same tombstone rule.
- `quic::drainServer(server)` stops accepting and starts graceful drain on
  every established connection. The application continues `next` until
  `serverDrainComplete` or `drainTimeoutMs`, then calls
  `quic::closeServer(server)`.
- `quic::closeServer(server)` is abortive: it closes remaining connections,
  then closes the `sysnet::Datagram` exactly once. It does not block for a
  graceful handshake.
- A close on a connection shell does not free a stream shell owned by the
  caller; live streams are orphaned and become `Closed`. A stream shell close
  retires its own token. This mirrors H2's orphan rule.

## 5. HTTP/3 mapping

### 5.1 Streams and roles

HTTP/3 uses QUIC streams:

| Stream | Direction | ID (server view) | Owner |
|---|---|---|---|
| Control | client -> server | client uni 2 | client; server reads |
| QPACK encoder | client -> server | client uni 6 | client; server reads |
| QPACK decoder | client -> server | client uni 10 | client; server reads |
| Control | server -> client | server uni 3 | server; `nghttp3` writes |
| QPACK encoder | server -> client | server uni 7 | server; `nghttp3` writes |
| QPACK decoder | server -> client | server uni 11 | server; `nghttp3` writes |
| Request | client bidi | 0, 4, 8, ... | one request/response per stream |
| Push | server uni | not used | disabled |

After the TLS handshake completes and the client's transport parameters are
known, the shim opens exactly three server unidirectional streams with
`ngtcp2_conn_open_uni_stream` (expected IDs 3, 7, 11), then calls
`nghttp3_conn_bind_control_stream(h3, 3)` and
`nghttp3_conn_bind_qpack_streams(h3, 7, 11)`. It does not open server bidi
streams. Opening them earlier is rejected because the client's
`initial_max_streams_uni` is not known until its transport parameters arrive.
If the client allows fewer than three server uni streams, or
`ngtcp2_conn_open_uni_stream` returns `NGTCP2_ERR_STREAM_ID_BLOCKED`, the
connection fails with `H3_STREAM_CREATION_ERROR` (0x0103).
A client uni stream is classified by its stream type byte: control
(0x00), push (0x01, rejected), QPACK encoder (0x02), QPACK decoder (0x03).
Unknown/GREASE types are drained and ignored up to `maxUniStreamBytes`.

### 5.2 SETTINGS

The server's first control-stream frame is SETTINGS. nghttp3 emits it; obo
selects the values.

| Setting | Value | Reason |
|---|---|---|
| `SETTINGS_MAX_FIELD_SECTION_SIZE` | 16384 | decoded header cap |
| `SETTINGS_QPACK_MAX_TABLE_CAPACITY` | 4096 | bounded decoder dynamic table |
| `SETTINGS_QPACK_BLOCKED_STREAMS` | 16 | bounded blocked-header state |
| `SETTINGS_ENABLE_CONNECT_PROTOCOL` | absent/0 | no Extended CONNECT |
| `SETTINGS_H3_DATAGRAM` | absent/0 | no HTTP/3 datagrams |

`nghttp3_settings`:

```c
nghttp3_settings_default(&s);
s.max_field_section_size = 16384;
s.qpack_max_dtable_capacity = 4096;
s.qpack_encoder_max_dtable_capacity = 4096;
s.qpack_blocked_streams = 16;
s.enable_connect_protocol = 0;
```

The peer's SETTINGS are validated by nghttp3. A missing SETTINGS frame on the
control stream before the first request is `H3_MISSING_SETTINGS` (0x010a).
A second SETTINGS frame, a setting with a bad length, or a forbidden value is a
connection error (`H3_SETTINGS_ERROR`, 0x0109). The shim also caps the raw
settings frame at 4096 bytes and 32 entries.

### 5.3 Request and response headers

Request pseudo-headers:

| Pseudo-header | Rule |
|---|---|
| `:method` | required; token; no control bytes |
| `:scheme` | required; must be `https` in v1 |
| `:authority` | required for origin-form; no userinfo; bounded 1024 bytes |
| `:path` | required except `CONNECT` (rejected); raw target, no decoding/normalization |
| `:protocol` | forbidden; Extended CONNECT disabled |
| `:status` | forbidden in requests |

Rules:

- Pseudo-headers precede regular fields; duplicates are rejected. `:authority`
  and `Host` must not disagree; if both exist and differ, `400`/stream reset.
- `CONNECT` is `501`/reset; `101` is invalid. `PRI * HTTP/2.0` and other h1/h2
  artifacts are not HTTP/3.
- `TE` may appear only with value `trailers`. `Connection`, `Keep-Alive`,
  `Proxy-Connection`, `Transfer-Encoding` and `Upgrade` are forbidden.
- `content-length` must equal the sum of DATA. A mismatch is
  `H3_MESSAGE_ERROR` (0x010e) for the stream. `content-length` over
  `maxBody` is rejected with `413` after headers, or reset if the header block
  is incomplete.
- Multiple `Cookie` fields are preserved; a convenience join uses `"; "`.
- Header names/values are copied from nghttp3 `rcbuf`s after length checks, then
  the refcounts are released. No `strlen`, no NUL truncation.
- Response headers are validated in obo/C before submit: no CR/LF/NUL, no
  pseudo-header injection, no `content-length` through `setHeader`. `Date` and
  optional `Server` are protocol-owned. No request echo.
- `HEAD`, `204`, `205`, `304` and `1xx` never carry response DATA. `304` may
  carry the selected representation length; `HEAD` may carry metadata length.
  `101` is invalid.

`httpapi::RequestMeta` additions for H3:

```obo
pub type ProtocolKind = closed {
    ProtocolHttp11 {}
    ProtocolHttp2 {}
    ProtocolHttp3 {}
}

pub type Failure = closed {
    /* existing variants */
    Qpack {}
    TransportParameter {}
    VersionNegotiation {}
    ConnectionID {}
    EarlyData {}
    Migration {}
    StatelessReset {}
}
```

`RequestMeta.versionMajor=3`, `versionMinor=0`; `streamID` is the QUIC stream
ID; `secure=true`; `scheme="https"`; `target` is the raw `:path`; `authority`
is `:authority`; `headers` excludes pseudo-headers. No `Host` is synthesized.

### 5.4 DATA, trailers and body flow

- Request DATA is delivered by nghttp3 `recv_data`; the shim copies it into a
  bounded per-stream receive ring. The ring capacity equals the advertised
  QUIC stream window, so the peer cannot overflow it.
- `readBody` copies bytes out and then calls
  `ngtcp2_conn_extend_max_stream_offset(stream_id, n)` and
  `ngtcp2_conn_extend_max_offset(n)`. nghttp3's `read_stream` return value
  covers control/QPACK/header bytes, not DATA; the shim must account for DATA
  explicitly.
- `discardBody` consumes exactly the discarded bytes at both levels. A reset or
  close with unread DATA connection-consumes the buffered bytes before the
  record is reaped. No path frees a receive ring with unconsumed bytes.
- Request trailers are HEADERS after DATA with no pseudo-headers. They are
  exposed only after the request body ends. `maxTrailerBytes=4096`,
  `maxTrailerCount=32`.
- Response trailers are submitted with `nghttp3_conn_submit_trailers` after the
  last DATA. A trailer after END_STREAM is `InvalidState`. `endResponse` with a
  declared `content-length` checks the DATA sum except for
  `HEAD`/`204`/`205`/`304`/`1xx`.
- `endResponse` may need multiple calls; it returns false when output does not
  fit. The application yields and retries under the same deadline. No hidden
  loop.
- Send path: `nghttp3_conn_writev_stream` returns `nghttp3_vec`s, a stream ID
  and a FIN flag. The shim passes them to `ngtcp2_conn_writev_stream`. After
  ngtcp2 accepts `n` bytes (including `n == 0` for a FIN-only send), it calls
  `nghttp3_conn_add_write_offset(h3, stream_id, n)`. It never acknowledges
  bytes ngtcp2 did not accept, and it never advances the vector cursor before
  the offset call.

### 5.5 Control stream, GOAWAY and stream limits

- The server control stream carries SETTINGS first. A client closing the server
  control stream, or a server closing the client control stream, is
  `H3_CLOSED_CRITICAL_STREAM` (0x0104). The same applies to either QPACK stream.
- `nghttp3_conn_submit_shutdown_notice(h3)` sends a GOAWAY with the maximum
  stream ID; after one PTO/`drainBarrierMs=1000`, `nghttp3_conn_shutdown(h3)`
  sends the final GOAWAY and refuses new requests. The shim records the last
  accepted request stream ID and uses `min(lastAccepted, nghttp3's value)`.
- New request streams above the GOAWAY cutoff get `H3_REQUEST_REJECTED`
  (0x010b) or `H3_REQUEST_CANCELLED` (0x010c) as appropriate. Existing
  requests finish until `drainTimeoutMs`.
- Concurrent request limit: `maxConcurrentRequests=32`
  (`initial_max_streams_bidi=32`, `nghttp3_conn_set_max_concurrent_streams`).
  Total requests per connection: `maxRequestsPerConnection=1000`
  (`nghttp3_conn_set_max_client_streams_bidi(1000)`). A 33rd concurrent stream
  is refused without allocating a new stream record.
- Client unidirectional streams: `initial_max_streams_uni=8`; the three
  required HTTP/3 streams plus bounded GREASE/unknown. More than 8 is
  `H3_STREAM_CREATION_ERROR` (0x0103) or `STREAM_LIMIT_ERROR` (0x04) at the
  QUIC layer. Unknown uni streams are drained up to `maxUniStreamBytes=65536`
  and then `STOP_SENDING`.
- On a stream close, the shim calls `nghttp3_conn_close_stream` and extends
  the QUIC stream limit with `ngtcp2_conn_extend_max_streams_bidi(1)`. Stream
  IDs are never reused.

### 5.6 QPACK

QPACK is RFC 9204. Dynamic table: decoder max capacity 4096, encoder max 4096,
blocked streams
  16. A dynamic-table capacity update above the advertised value is
  `QPACK_DECOMPRESSION_FAILED` (0x0200).
- Header field section: `max_field_section_size=16384` decoded; header count
  100; field 8192; raw compressed block budget 65536. Exceeding the decoded
  list is a stream error (`H3_EXCESSIVE_LOAD`, 0x0107) after one reset;
  exceeding the compressed budget is also `H3_EXCESSIVE_LOAD`.
- QPACK encoder/decoder stream bytes are bounded by `maxQpackStreamBytes=262144`
  per connection. A decoder-stream error is `QPACK_DECODER_STREAM_ERROR`
  (0x0202); an encoder-stream error is `QPACK_ENCODER_STREAM_ERROR` (0x0201).
  QPACK fatal is a connection error.
- `nghttp3_conn_add_ack_offset` is called from `acked_stream_data` so the
  encoder can release referenced buffers. Do not acknowledge bytes ngtcp2 did
  not accept.
- No dynamic table sharing across connections. No QPACK offload. No unbounded
  blocked-header queue.

### 5.7 Priority, push and coalescing

- Priority: RFC 9218 `priority` request headers and `PRIORITY_UPDATE` frames
  are parsed/ignored. There is no priority scheduler in v1. Fairness is
  application round-robin with a per-stream work cap (`maxReadChunk=16384` or
  one event per turn).
- Push: disabled. The server never sends PUSH_PROMISE, never opens push
  streams, and does not send MAX_PUSH_ID. A client PUSH_PROMISE is a
  connection error. `CANCEL_PUSH`/`MAX_PUSH_ID` from a client are ignored or
  rejected per nghttp3; no push state is allocated.
- Extended CONNECT / WebTransport: disabled (`enable_connect_protocol=0`,
  `:protocol` forbidden).
- Coalescing: HTTP/3 clients may coalesce origins on one connection. v1 pins
  the first request's `:authority` to the connection and answers a different
  authority with `421 Misdirected Request` (or a stream reset). This avoids
  cross-origin request smuggling and certificate-scope ambiguity. It is a
  server policy, not a protocol violation. Multi-authority coalescing requires
  certificate SAN introspection and a separate review.
- `Alt-Svc` advertisement is an optional application/TCP-listener helper:
  `Alt-Svc: h3=":443"; ma=86400`. It is not part of the H3 core and must not
  be emitted for a port/protocol the server cannot serve.

### 5.8 HTTP/3 error codes

| Code | Name | Use |
|---|---|---|
| 0x0100 | `H3_NO_ERROR` | graceful close |
| 0x0101 | `H3_GENERAL_PROTOCOL_ERROR` | generic connection error |
| 0x0102 | `H3_INTERNAL_ERROR` | shim/handler invariant |
| 0x0103 | `H3_STREAM_CREATION_ERROR` | bad/too many streams |
| 0x0104 | `H3_CLOSED_CRITICAL_STREAM` | control/QPACK closed |
| 0x0105 | `H3_FRAME_UNEXPECTED` | frame in wrong place |
| 0x0106 | `H3_FRAME_ERROR` | malformed frame |
| 0x0107 | `H3_EXCESSIVE_LOAD` | caps/abuse |
| 0x0108 | `H3_ID_ERROR` | bad stream/push ID |
| 0x0109 | `H3_SETTINGS_ERROR` | bad SETTINGS |
| 0x010a | `H3_MISSING_SETTINGS` | no SETTINGS before request |
| 0x010b | `H3_REQUEST_REJECTED` | not processed |
| 0x010c | `H3_REQUEST_CANCELLED` | cancelled |
| 0x010d | `H3_REQUEST_INCOMPLETE` | incomplete request |
| 0x010e | `H3_MESSAGE_ERROR` | malformed HTTP message |
| 0x010f | `H3_CONNECT_ERROR` | CONNECT failure (not used) |
| 0x0110 | `H3_VERSION_FALLBACK` | version fallback (not used) |
| 0x0200 | `QPACK_DECOMPRESSION_FAILED` | QPACK decode failure |
| 0x0201 | `QPACK_ENCODER_STREAM_ERROR` | encoder stream error |
| 0x0202 | `QPACK_DECODER_STREAM_ERROR` | decoder stream error |

Transport close codes use RFC 9000: `NO_ERROR` 0x00, `INTERNAL_ERROR` 0x01,
`CONNECTION_REFUSED` 0x02, `FLOW_CONTROL_ERROR` 0x03, `STREAM_LIMIT_ERROR`
0x04, `STREAM_STATE_ERROR` 0x05, `FINAL_SIZE_ERROR` 0x06,
`FRAME_ENCODING_ERROR` 0x07, `TRANSPORT_PARAMETER_ERROR` 0x08,
`CONNECTION_ID_LIMIT_ERROR` 0x09, `PROTOCOL_VIOLATION` 0x0a,
`INVALID_TOKEN` 0x0b, `APPLICATION_ERROR` 0x0c, `CRYPTO_BUFFER_EXCEEDED`
0x0d, `KEY_UPDATE_ERROR` 0x0e, `AEAD_LIMIT_REACHED` 0x0f,
`NO_VIABLE_PATH` 0x10.

## 6. API, ownership and the shared handler seam

### 6.1 Module layout and dependency DAG

```text
httpapi       -> sysclock
quic          -> httpapi, sysnet, sysaddress, sysplatform, sysbuffer,
                 sysbytes, sysclock, sysreactor
http3         -> quic, httpapi, sysclock
http1         -> httpapi, httptransport, http1core, http1parse, http1date,
                 sysbuffer, sysclock
http2         -> httpapi, httptransport, sysplatform, sysbuffer, sysbytes,
                 sysclock
quic          -/-> http1, http2
http3         -/-> http1, http2
systls        -/-> quic, http3
```

`quic` is the transport shell layer; `http3` is the HTTP/3 mapping over it.
`http3` does not redefine the transport. `quic` and `http3` do not import
`systls`; they use the internal `quictls` C unit.

Proposed source layout:

```text
obo/experimental/http3/quic.obx          Server/Connection/Stream shells,
                                         limits, admission, events, deadlines
obo/experimental/http3/http3.obx         HTTP/3 request/response/trailer API,
                                         runAction, drain, stats
obo/experimental/http3/native/q3_adapter.h
obo/experimental/http3/native/q3_adapter.c
obo/experimental/http3/native/quictls.h
obo/experimental/http3/native/quictls.c
obo/experimental/http3/http3_adapter.cbind
obo/experimental/http3/tests/...         C harness, obo drivers, integration
obo/experimental/sys/sysnet.obx          P2q datagram batch/GSO/GRO extension
```

Unique module/basename names: `quic`, `http3`. C prefixes: `q3_` for the
transport/H3 shim, `q3_tls_` for `quictls`. The C registry kinds are library
constants, not kernel ABI.

### 6.2 Kinds, shells and ownership

Candidate kinds (assign the next free values at implementation; 25/26 are H2,
27–30 are candidates):

```obo
pub const KIND_QUIC_CONTEXT    = 27
pub const KIND_QUIC_SERVER     = 28
pub const KIND_QUIC_CONNECTION = 29
pub const KIND_QUIC_STREAM     = 30
```

```obo
pub type Context = ref record { pub res: platform::Resource }

pub type Server = ref record {
    pub res: platform::Resource
    datagram: sysnet::Datagram       // owned after makeServer
    context: Context                 // owned after makeServer
    limits: Limits                   // deep-copied at makeServer
}

pub type Connection = ref record {
    pub res: platform::Resource
    reserved: int                    // stored per-connection reservation
}
pub type Stream = ref record { pub res: platform::Resource }
```

- `Context` owns the GnuTLS certificate/key/policy. It has no sessions. It is
  consumed by `makeServer`; a second server needs a new context (or a future
  shared-context design with reference counting, which is not in v1).
- `Server` owns exactly one `sysnet::Datagram` and one `Context` after
  `makeServer`. It owns the C server routing table and all live connections.
  It does not own obo `Connection` shells.
- `Connection` is a shell over a C connection record. It does not own the UDP
  socket and cannot outlive the server. Dropping the record frees only the
  record.
- `Stream` is a shell over a C HTTP/3 request stream. It does not own the
  connection. Dropping it frees only the record; `close`/`reset` retires the
  token.
- Public fields are not authority. `streamID(s)` and `connectionID(c)` ask C;
  there is no public `id` field. A forged/stale token fails
  `platform::requireKind` before any C dereference.

Lifecycle:

- `makeContext(options)` loads cert/key into C and registers
  `KIND_QUIC_CONTEXT`. On failure no token remains.
- `makeServer(ctx, d, limits, options)` moves `ctx` and `d` into the server,
  registers `KIND_QUIC_SERVER`, and adopts the token. On any validation
  failure it closes `d` and `ctx` exactly once and returns no server.
- `drainServer(server)` stops accepting, starts GOAWAY on every established
  connection, and keeps the server open until `serverDrainComplete` or the
  application deadline. `closeServer(server)` is abortive: it closes remaining
  connections, the datagram and the context exactly once, and retires the
  server token. A second close returns `ok(false)`. Graceful shutdown is
  `drainServer` -> pump `next` -> `closeServer`.
- `openConnection(server, id)` adopts a C connection exactly once; a second
  call returns `AlreadyExists`; an unknown/closed id returns `StreamClosed`.
  If `_adoptToken` fails, `q3_conn_abandon` is idempotent and performs exactly
  one native release. `openConnection` copies `reservation(s.limits)` into the
  shell's private `reserved` scalar. The caller calls `Admission.admit()`
  before `openConnection`; `reservedBytes(c)` returns the stored
  per-connection reservation after `closeConnection`, so the application can
  release the admission without reading a stale token.
- `close(conn)` is abortive and idempotent; live streams are orphaned.
- `http3::enable(conn, limits)` is called once after `EventHandshakeDone`; it
  opens the server control/QPACK streams and creates the nghttp3 connection.
  Calling it before the handshake returns `NotReady`; calling it twice returns
  `ok(false)`. It is not a hidden side effect of `openConnection`. `H3Limits`
  must not exceed the server's `Limits` header/QPACK/body/stream caps; an
  inconsistent profile is `InvalidArgument`, not a second hidden budget.
- `openStream(conn, id)` adopts a C request stream exactly once; a second call
  returns `AlreadyExists`; a closed id returns `StreamClosed`.
- `closeStream(s)`/`reset(s, code)` mark closing, submit RESET_STREAM/
  STOP_SENDING or the response end, and retire the token. The C record is
  freed only by the post-feed/post-send sweep after nghttp3/ngtcp2 reports the
  stream closed. A data-read callback cannot reach freed memory.
- `defer` is the normal cleanup:
  ```obo
  let ctx = try quic::makeContext(opts)
  let srv = try quic::makeServer(ctx, dgram, limits, options)
  defer quic::closeServer(srv)
  ```
  Register the server defer last so it runs first.

### 6.3 Public API sketch

The block below is **proposed declarations**, not a compilable module. It uses
current 0.14 field style and only 0.11/Stage B features. Bodyless `pub fn`
headings become definitions.

```obo
module quic
import "httpapi.obx" as http
import "sys/sysclock.obx" as clock
import "sys/sysnet.obx" as net
import "sys/sysplatform.obx" as platform
import "sys/sysreactor.obx" as reactor

pub const KIND_QUIC_CONTEXT = 27
pub const KIND_QUIC_SERVER = 28
pub const KIND_QUIC_CONNECTION = 29
pub const KIND_QUIC_STREAM = 30

pub type RetryMode = closed {
    RetryNever {}
    RetryAlways {}
    RetryUnderLoad {}
}

pub type ContextOptions = ref record {
    pub certificateChainPem: []byte    // <= 1 MiB
    pub privateKeyPem: []byte          // <= 64 KiB
    pub requireSni: bool               // diagnostics only
    pub retryMode: RetryMode
}

pub type Context = ref record { pub res: platform::Resource }

pub type Options = ref record {
    pub serverName: string             // empty omits Server
    pub requireAuthority: bool         // default true
    pub h3: bool                       // false = transport-only test profile
}

pub type Limits = ref record {
    pub maxConnections: int
    pub maxPendingHandshakes: int
    pub maxTotalBytes: int
    pub maxConcurrentRequests: int
    pub maxRequestsPerConnection: int
    pub maxUniStreams: int
    pub maxStreamRecvWindow: int
    pub maxConnectionWindow: int
    pub maxStreamSendBuffer: int
    pub maxHeaderListBytes: int
    pub maxHeaderCount: int
    pub maxHeaderFieldBytes: int
    pub maxHeaderBlockBytes: int
    pub maxHeaderArena: int
    pub maxQpackTable: int
    pub maxQpackBlocked: int
    pub maxQpackStreamBytes: int
    pub maxTrailerBytes: int
    pub maxTrailerCount: int
    pub maxResponseHeaderBytes: int
    pub maxResponseTrailerBytes: int
    pub maxResponseTrailerCount: int
    pub maxBody: int
    pub maxReadChunk: int
    pub maxUdpPayload: int
    pub maxBatchDatagrams: int
    pub maxWire: int
    pub maxEvents: int
    pub maxStreamsPerConnection: int
    pub maxResetBurst: int
    pub maxResetRate: int
    pub maxPathChangesPerMinute: int
    pub maxStatelessResetsOutstanding: int
    pub maxStatelessResetsPerSecond: int
    pub handshakeTimeoutMs: int
    pub idleTimeoutMs: int
    pub headerTimeoutMs: int
    pub bodyIdleTimeoutMs: int
    pub responseTimeoutMs: int
    pub responseTotalTimeoutMs: int
    pub streamTotalTimeoutMs: int
    pub drainBarrierMs: int
    pub drainTimeoutMs: int
    pub retryTokenMs: int
}

pub fn defaultLimits(): Limits
pub fn validateLimits(l: &Limits): Result[bool]
pub fn defaultOptions(): Options
pub fn reservation(l: &Limits): Result[int]

pub type Admission = ref record { /* private counters */ }
pub fn makeAdmission(): Admission
pub fn admit(a: &Admission, l: &Limits): Result[bool]
pub fn release(a: &Admission, reserved: int): bool
pub fn activeCount(a: &Admission): int
pub fn admissionReservedBytes(a: &Admission): int

pub type Server = ref record { /* private fields as above */ }
pub type Connection = ref record {
    pub res: platform::Resource
    reserved: int                    // stored per-connection reservation
}
pub type Stream = ref record { pub res: platform::Resource }

pub type Event = closed {
    EventNeedRead {}
    EventNeedWrite {}
    EventConnectionNew { conn: int }
    EventHandshakeDone { conn: int }
    EventStreamOpened { conn: int; stream: int; bidi: bool }
    EventStreamData { conn: int; stream: int }
    EventStreamEnd { conn: int; stream: int }
    EventTrailers { conn: int; stream: int }
    EventStreamClosed { conn: int; stream: int; code: int }
    EventPathChanged { conn: int }
    EventGoaway { conn: int; lastStreamID: int }
    EventDrainComplete { conn: int }
    EventTimedOut { conn: int; stream: int }
    EventProtocolError { conn: int; code: int }
    EventConnectionClosed { conn: int; code: int }
}

pub fn makeContext(o: &ContextOptions): Result[Context]
pub fn makeServer(ctx: Context, d: net::Datagram, l: &Limits,
                  o: &Options): Result[Server]
pub fn serverID(s: &Server): Result[int]
pub fn next(mut s: &Server, now: &http::Now): Result[Event]
pub fn deadline(s: &Server): Result[clock::Deadline>
pub fn wantsRead(s: &Server): bool
pub fn wantsWrite(s: &Server): bool
pub fn watch(mut s: &Server, r: &reactor::Reactor,
             tag: int): Result[reactor::Watch]
pub fn openConnection(s: &Server, id: int): Result[Connection]
pub fn connectionID(c: &Connection): Result[int]
pub fn reservedBytes(c: &Connection): int
pub fn closeConnection(c: &Connection): Result[bool]
pub fn drainConnection(c: &Connection, now: &http::Now): Result[bool]
pub fn drainComplete(c: &Connection): bool
pub fn drainServer(s: &Server, now: &http::Now): Result[bool]
pub fn serverDrainComplete(s: &Server): bool
pub fn connectionStats(c: &Connection): Result<Stats>
pub fn closeServer(mut s: &Server): Result[bool]

pub type StreamState = closed {
    StreamOpen {}
    StreamHalfClosedRemote {}
    StreamHalfClosedLocal {}
    StreamClosed {}
}

pub fn openStream(c: &Connection, id: int): Result[Stream]
pub fn closeStream(s: &Stream): Result[bool]
pub fn streamID(s: &Stream): Result[int]
pub fn streamState(s: &Stream): Result[StreamState]
pub fn reset(s: &Stream, code: int): Result[bool]

pub type Counts = ref record {
    pub liveServers: int
    pub liveConnections: int
    pub liveStreams: int
    pub connecting: int
    pub handshaking: int
    pub established: int
    pub draining: int
    pub streamsOpened: int
    pub streamsClosed: int
    pub requestsCompleted: int
    pub resetsSent: int
    pub resetsReceived: int
    pub retriesSent: int
    pub versionNegotiations: int
    pub statelessResets: int
    pub pathChanges: int
    pub datagramsIn: int
    pub datagramsOut: int
    pub bytesIn: int
    pub bytesOut: int
    pub eventsHighWater: int
    pub wireHighWater: int
    pub timeouts: int
    pub protocolErrors: int
    pub nativeBytes: int
    pub maxNativeBytes: int
}

pub type Stats = ref record {
    pub liveStreams: int
    pub maxConcurrentRequests: int
    pub streamsOpened: int
    pub streamsClosed: int
    pub resetsSent: int
    pub resetsReceived: int
    pub retriesSent: int
    pub versionNegotiations: int
    pub statelessResets: int
    pub pathChanges: int
    pub datagramsIn: int
    pub datagramsOut: int
    pub bytesIn: int
    pub bytesOut: int
    pub eventsHighWater: int
    pub wireHighWater: int
    pub recvWindow: int
    pub connWindow: int
}

pub fn stats(s: &Server): Result[Stats>
pub fn counts(): Result[Counts>
```

The HTTP/3 facade adds request/response functions:

```obo
module http3
import "quic.obx" as quic
import "httpapi.obx" as http
import "sys/sysclock.obx" as clock

pub type H3Limits = ref record {
    pub maxConcurrentRequests: int
    pub maxRequestsPerConnection: int
    pub maxHeaderListBytes: int
    pub maxHeaderCount: int
    pub maxHeaderFieldBytes: int
    pub maxHeaderBlockBytes: int
    pub maxQpackTable: int
    pub maxQpackBlocked: int
    pub maxTrailerBytes: int
    pub maxTrailerCount: int
    pub maxBody: int
    pub maxReadChunk: int
    pub maxResponseHeaderBytes: int
    pub maxResponseTrailerBytes: int
    pub maxResponseTrailerCount: int
}

pub fn defaultLimits(): H3Limits
pub fn validateLimits(l: &H3Limits): Result[bool]

pub type Event = closed {
    EventNeedRead {}
    EventNeedWrite {}
    EventConnectionNew { conn: int }
    EventHandshakeDone { conn: int }
    EventRequest { conn: int; stream: int }
    EventRequestData { conn: int; stream: int }
    EventRequestEnd { conn: int; stream: int }
    EventTrailers { conn: int; stream: int }
    EventStreamClosed { conn: int; stream: int; code: int }
    EventPathChanged { conn: int }
    EventGoaway { conn: int; lastStreamID: int }
    EventDrainComplete { conn: int }
    EventTimedOut { conn: int; stream: int }
    EventProtocolError { conn: int; code: int }
    EventConnectionClosed { conn: int; code: int }
}

pub fn next(mut s: &quic::Server, now: &http::Now): Result[Event>
pub fn openConnection(s: &quic::Server, id: int): Result[quic::Connection]
pub fn enable(c: &quic::Connection, l: &H3Limits): Result[bool]
pub fn openStream(c: &quic::Connection, id: int): Result<quic::Stream>

pub fn requestInfo(s: &quic::Stream): Result[http::RequestMeta]
pub fn headerCount(s: &quic::Stream): Result[int]
pub fn headerAt(s: &quic::Stream, index: int): Result[http::Header]
pub fn trailerCount(s: &quic::Stream): Result[int]
pub fn trailerAt(s: &quic::Stream, index: int): Result[http::Header]

pub fn readBody(s: &quic::Stream, max: int,
                now: &http::Now): Result[http::BodyRead]
pub fn discardBody(s: &quic::Stream, max: int,
                   now: &http::Now): Result[bool]
pub fn beginResponse(s: &quic::Stream, status: int): Result[bool]
pub fn setHeader(s: &quic::Stream, name: string,
                 value: string): Result[bool]
pub fn setLength(s: &quic::Stream, length: int): Result[bool]
pub fn writeBody(s: &quic::Stream, bytes: &[]byte, offset: int,
                 count: int): Result[int]
pub fn interim(s: &quic::Stream, status: int): Result[bool]
pub fn setTrailerNames(s: &quic::Stream, names: &[]string): Result[bool]
pub fn addTrailer(s: &quic::Stream, name: string,
                  value: string): Result[bool]
pub fn endResponse(s: &quic::Stream): Result[bool]
pub fn resetStream(s: &quic::Stream, code: int): Result[bool]

pub type ActionState = ref record { /* private progress state */ }
pub fn makeActionState(): ActionState
pub fn runAction(mut st: &ActionState, h: http::Handler,
                 req: &http::RequestMeta, mut s: &quic::Stream,
                 now: &http::Now): Result[bool]
pub fn actionStopRequested(st: &ActionState): bool
pub fn actionCloseRequested(st: &ActionState): bool

pub fn drain(c: &quic::Connection, now: &http::Now): Result[bool]
pub fn drainComplete(c: &quic::Connection): bool
pub fn stats(c: &quic::Connection): Result[quic::Stats>
```

### 6.4 Event pull, deadlines and backpressure

- `next(server, now)` performs at most one bounded `receiveBatch`, one bounded
  `sendBatch` (or one fallback datagram loop), one timer check and returns at
  most one event. It never loops on socket readiness.
- `EventNeedRead` means the application should watch datagram readability.
  `EventNeedWrite` is rare for UDP; it means a pending output batch could not be
  sent and should be retried. The application never spins.
- `deadline(server)` returns the minimum finite deadline. The application arms
  one reactor timer and one datagram watch; it does not create per-connection
  OS resources.
- `writeBody` accepts only free space in the per-stream send ring. `0` means
  full; the handler yields and retries under the same deadline. Accepted bytes
  are sent exactly once or the stream is reset.
- `readBody` returns `BodyNeedRead` when no decoded body bytes are available;
  it never blocks. The application watches the datagram; a readable datagram
  can still produce no event for this stream.
- Fairness: at most `maxDatagramsPerTurn=16` received and
  `maxSendDatagrams=16` sent per turn, and `maxStreamWorkPerTurn=16384` bytes
  per stream. One connection or stream cannot monopolize a turn.

`http3::next` maps the transport event ring to HTTP/3 events:

| `quic::Event` | `http3::Event` | Rule |
|---|---|---|
| `EventNeedRead`/`EventNeedWrite` | same | pass through |
| `EventConnectionNew` | `EventConnectionNew` | caller adopts and enables H3 |
| `EventHandshakeDone` | `EventHandshakeDone` | caller calls `http3::enable` |
| `EventStreamOpened { bidi=true }` | `EventRequest` | client bidi stream is a request |
| `EventStreamOpened { bidi=false }` | ignored | control/QPACK/unknown uni |
| `EventStreamData` | `EventRequestData` | only for an adopted request |
| `EventStreamEnd` | `EventRequestEnd` | request END_STREAM |
| `EventTrailers` | `EventTrailers` | request trailer block complete |
| `EventStreamClosed` | `EventStreamClosed` | response/reset close |
| `EventPathChanged` | same | peer path updated |
| `EventGoaway` | same | peer GOAWAY |
| `EventDrainComplete` | same | local drain finished |
| `EventTimedOut` | same | `stream=0` means connection timer |
| `EventProtocolError` | same | map code |
| `EventConnectionClosed` | same | terminal |

Ordering invariant: `EventHandshakeDone` precedes the first `EventRequest` on a
connection, so the application can call `http3::enable` in between. No request
event is surfaced while H3 is not enabled.

### 6.5 Shared handler semantics without pretending transports are identical

Shared in `httpapi`:

- `Header`, `RequestMeta`, `ProtocolKind`, `CommonLimits`, `BodyRead`,
  `Failure`, `Action`, `Handler`, `Now`.
- The pure handler contract `Handler = fn(&RequestMeta, &Now): Action`.
- The standard action vocabulary and executor semantics.

Concrete per protocol:

| Concern | HTTP/1.1 | HTTP/2 | HTTP/3 |
|---|---|---|---|
| Byte transport | `httptransport::Transport` | `httptransport::Transport` | QUIC datagrams/streams |
| Connection shell | `http1conn::Connection` | `http2::Connection` | `quic::Server` + `quic::Connection` |
| Per-request shell | connection | `http2::Stream` | `quic::Stream` |
| Body operations | concrete free functions | concrete free functions | concrete free functions |
| Reset | close/abort | RST_STREAM | RESET_STREAM/STOP_SENDING |
| Trailers | chunked only | HEADERS after DATA | HEADERS after DATA |
| Full duplex | no | yes | yes |
| Version | 1.1 | 2 | 3 |
| ALPN | `http/1.1`/none | `h2` | `h3` only |

They share `RequestMeta` and `Action`; they do **not** share a transport
interface, an exchange interface or a callback. The pure `Handler` is the
standard/demo seam. A general impure application handler remains a documented
language gap: obo 0.14 has no impure interface-member or function-value tier.
The concrete `quic::Stream` functions are the working path for streaming
applications and do not require that tier.

`runAction` is the same executor shape as H2:

| Action | HTTP/3 execution |
|---|---|
| `Hello` | `begin(200)`, `setLength(6)`, write `hello\n`, `end` |
| `Echo` | `begin(200)` no length; `readBody` -> `writeBody` until `BodyEnd`; `end` |
| `Chunked` | `begin(200)` no length; fixed three-part body; `end` |
| `Head` | `begin(200)`, `setLength(6)`, no DATA, `end` |
| `NoContent` | `begin(204)`, `end` |
| `NotModified` | `begin(304)`, `end` |
| `Big` | `begin(200)`, `setLength(20480)`, bounded 20 KiB body, `end` |
| `Slow` | `begin(200)`; 10 bytes per call; `end` |
| `Stop` | `Hello`, then set stop flag |
| `Close` | `Hello`, then set close flag |

`ActionState` latches the first action, per-stream bytes read/written, the body
cursor and the response flags. A pure handler that reads `&Now` cannot change
its decision on a retry. One `ActionState` belongs to one stream and is not
reused across requests. `runAction` never spins; `writeBody` returning 0,
`readBody` returning `BodyNeedRead`, or `endResponse` returning false yields.
For actions other than `Echo`, `runAction` discards the remaining body with
bounded `discardBody`; over `maxBody` or the stream deadline resets with
`H3_REQUEST_CANCELLED`.

### 6.6 Representative application loop

```obo
type Slot = ref record {
    conn: quic::Connection
    stream: ?quic::Stream
    req: ?httpapi::RequestMeta
    state: ?http3::ActionState
    connID: int
    streamID: int
}

fn serveOne(srv: &quic::Server, slots: &[]Slot,
            h: httpapi::Handler, now: &httpapi::Now): Result[bool] {
    let ev = try http3::next(srv, now)
    match ev {
        EventNeedRead { return ok(false) }
        EventNeedWrite { return ok(false) }
        EventConnectionNew { conn } {
            let c = try http3::openConnection(srv, conn)
            // store a slot keyed by conn
            return ok(true)
        }
        EventHandshakeDone { conn } {
            let c = try findConnection(slots, conn)
            try http3::enable(&c, &h3limits)
            return ok(true)
        }
        EventRequest { conn, stream } {
            let c = try findConnection(slots, conn)
            let s = try http3::openStream(&c, stream)
            let req = try http3::requestInfo(&s)
            let st = http3::makeActionState()
            // store a slot keyed by (conn, stream)
            return http3::runAction(&st, h, &req, &s, now)
        }
        EventRequestData { conn, stream } {
            // resume the slot's runAction
            return ok(true)
        }
        EventRequestEnd { conn, stream } { return ok(true) }
        EventTrailers { conn, stream } { return ok(true) }
        EventStreamClosed { conn, stream, code } {
            // close/drop the slot; the token may already be stale
            return ok(true)
        }
        EventConnectionClosed { conn, code } {
            // close/drop all slots for the connection
            return ok(true)
        }
        EventPathChanged { conn } { return ok(true) }
        EventGoaway { conn, lastStreamID } { return ok(true) }
        EventDrainComplete { conn } { return ok(true) }
        EventTimedOut { conn, stream } { return ok(true) }
        EventProtocolError { conn, code } { return ok(false) }
    }
}
```

The sketch omits the slot-table lookup and error policy. It never stores a
borrow to the server or connection inside `ActionState`. The application owns
the handler function value, the slots and the `Admission`.

## 7. State machines, limits and flow control

### 7.1 Server state

| State | `next` | `openConnection` | `closeServer` |
|---|---|---|---|
| `Open` | receive/send/events | allowed for a C-announced id | -> `Closing` |
| `Draining` | receive/send existing; no new conns | `Refused` for new ids | -> `Closing` |
| `Closing` | flush only | `Closed` | idempotent |
| `Closed` | `Closed` | `Closed` | `ok(false)` |
| `Failed` | `Closed` | `Closed` | `ok(true)` once |

`Draining` is entered by `drainServer` or an application policy (stop
accepting). It is not automatically entered on one connection's GOAWAY.
`closeServer` moves directly to `Closing`.

### 7.2 Connection state

```text
Handshaking -> Established -> Draining -> Closing -> Closed
       |              |
       +-> Failed <---+
```

| State | Entry | Allowed | Exit |
|---|---|---|---|
| `Handshaking` | first Initial accepted | read/write handshake; no HTTP/3 request accepted | handshake complete -> `Established`; timeout/error -> `Failed` |
| `Established` | TLS handshake complete | HTTP/3 requests/responses; path validation | `drain` -> `Draining`; error/idle -> `Closing` |
| `Draining` | GOAWAY notice | finish existing requests; refuse new | no live requests -> `Closing` |
| `Closing` | CONNECTION_CLOSE queued | flush; drop new packets | tombstone -> `Closed` |
| `Closed` | free | none | terminal |
| `Failed` | fatal protocol/TLS error | flush best-effort | `close` -> `Closed` |

Invariants:

1. No HTTP/3 request is accepted before the TLS handshake completes and
   `http3::enable` has opened the control/QPACK streams. No earlier request
   stream is surfaced; 0-RTT bytes are dropped.
2. `drain` is idempotent; the first GOAWAY precedes the barrier PING/timer.
3. `close` is abortive; `drain` is graceful; they are not conflated.
4. A connection close orphans stream shells; it does not free caller-owned
   shells.
5. `Connection` operations after `close` return `Closed`; a stale token fails
   its kind check before C dereference.

### 7.3 Stream state

QUIC stream states (RFC 9000 §3) and the HTTP/3 request overlay:

| State | Entry | Receive | Send |
|---|---|---|---|
| `Idle` | stream ID allocated | none | none |
| `Open` | request HEADERS begin | HEADERS/DATA | response headers/DATA |
| `HalfClosedRemote` | request END_STREAM | none | response until end |
| `HalfClosedLocal` | response END_STREAM | request DATA/trailers until end | none |
| `Closed` | RESET/STOP or both ends | none | none |

Internal response state:

```text
RespNone -> RespHeadersStaged -> RespHeadersSent -> RespBody
         -> RespTrailersPending -> RespDone
         -> RespReset
```

Invariants:

1. Request HEADERS are once; response HEADERS are once.
2. `beginResponse` is valid only after request headers and before response
   headers commit; it is once.
3. Response headers commit on the first `writeBody` or `endResponse`; after
   commit, `setHeader`/`setLength`/`setTrailerNames` are `InvalidState`.
4. `writeBody` is invalid before `beginResponse` or after `endResponse`/
   `reset`.
5. `endResponse` is once; false means retry after the send path drains.
6. Request trailers are exposed only after `BodyEnd`; response trailers only
   after the last DATA and before END_STREAM.
7. `HEAD`/`1xx`/`204`/`205`/`304` never emit DATA; `writeBody` accepts and
   discards without accumulating `responseDataBytes`.
8. `101` is invalid. `CONNECT`/`:protocol` are rejected.
9. `Date` is captured once at `beginResponse`; `Server` is optional; neither
   echoes request data.

### 7.4 Limits

`Limits` is deep-copied and validated at `makeServer`. `validateLimits` uses
checked arithmetic; a reservation below the minimum is
`ResourceLimit`/`InvalidArgument`, never a wrapped smaller budget.

| Limit | Default | Rule |
|---|---|---|
| `maxConnections` | 14 | established/draining connections |
| `maxPendingHandshakes` | 32 | unadopted handshakes without a validated address |
| `maxTotalBytes` | 67108864 | 64 MiB aggregate reservation |
| `maxConcurrentRequests` | 32 | 1..256; `initial_max_streams_bidi` |
| `maxRequestsPerConnection` | 1000 | cumulative client bidi streams |
| `maxUniStreams` | 8 | client unidirectional streams |
| `maxStreamRecvWindow` | 32768 | QUIC stream receive window |
| `maxConnectionWindow` | 1048576 | QUIC connection receive window |
| `maxStreamSendBuffer` | 32768 | per-stream response body |
| `maxHeaderListBytes` | 16384 | decoded name+value+32 per field |
| `maxHeaderCount` | 100 | per header block |
| `maxHeaderFieldBytes` | 8192 | one name or value |
| `maxHeaderBlockBytes` | 65536 | compressed HEADERS block |
| `maxHeaderArena` | 1572864 | per-connection header staging |
| `maxQpackTable` | 4096 | decoder and encoder dynamic table |
| `maxQpackBlocked` | 16 | blocked request streams |
| `maxQpackStreamBytes` | 262144 | QPACK encoder/decoder stream budget |
| `maxTrailerBytes` | 4096 | request trailers |
| `maxTrailerCount` | 32 | request trailer fields |
| `maxResponseHeaderBytes` | 16384 | staged response headers |
| `maxResponseTrailerBytes` | 4096 | staged response trailers |
| `maxResponseTrailerCount` | 32 | staged response trailers |
| `maxBody` | 8388608 | decoded request body |
| `maxReadChunk` | 16384 | per read/write call |
| `maxUdpPayload` | 1200 | QUIC datagram payload; no PMTUD |
| `maxBatchDatagrams` | 16 | GSO batch |
| `maxWire` | 65536 | C wire batch and obo send buffer |
| `maxEvents` | 256 | per-connection event ring |
| `maxStreamsPerConnection` | 1000 | total opened; then GOAWAY |
| `maxResetBurst` | 100 | reset token bucket |
| `maxResetRate` | 20 | resets/second |
| `maxPathChangesPerMinute` | 4 | validated path changes |
| `maxStatelessResetsOutstanding` | 256 | tombstone cap |
| `maxStatelessResetsPerSecond` | 100 | global reset rate |
| `handshakeTimeoutMs` | 10000 | ngtcp2 handshake timeout |
| `idleTimeoutMs` | 30000 | QUIC max_idle_timeout |
| `headerTimeoutMs` | 10000 | request headers |
| `bodyIdleTimeoutMs` | 30000 | after first body read |
| `responseTimeoutMs` | 60000 | response progress |
| `responseTotalTimeoutMs` | 300000 | absolute response |
| `streamTotalTimeoutMs` | 300000 | from request stream open |
| `drainBarrierMs` | 1000 | GOAWAY notice barrier |
| `drainTimeoutMs` | 30000 | graceful drain |
| `retryTokenMs` | 10000 | Retry token validity |

Validation checks (exact):

- Every count/byte limit positive; `maxConcurrentRequests <= 256`;
  `maxStreamsPerConnection >= maxConcurrentRequests`;
  `maxUniStreams >= 3`.
- `maxStreamRecvWindow >= maxReadChunk`;
  `maxConnectionWindow >= maxConcurrentRequests * maxStreamRecvWindow` (checked).
- `maxHeaderBlockBytes >= maxHeaderListBytes`;
  `maxHeaderListBytes >= maxHeaderFieldBytes`;
  `maxTrailerBytes <= maxHeaderListBytes`;
  `maxTrailerCount <= maxHeaderCount`;
  `maxQpackBlocked <= maxConcurrentRequests`.
- `maxHeaderArena >= maxConcurrentRequests *
  (maxHeaderListBytes + maxTrailerBytes + maxResponseHeaderBytes +
  maxResponseTrailerBytes)`.
- `maxWire >= maxUdpPayload * maxBatchDatagrams` and
  `maxWire >= 65536`; `maxBatchDatagrams <= 64`.
- `maxEvents >= 5 * maxConcurrentRequests + 32`.
- Timeouts fit checked millisecond-to-nanosecond conversion.
- `maxPendingHandshakes <= 64`;
  `maxTotalBytes >= maxConnections * perConnectionReservation +
  maxPendingHandshakes * pendingReservation`; otherwise `ResourceLimit`, not a
  silent lower budget.

Per-connection reservation:

```text
perConnectionReservation =
    maxConcurrentRequests * (maxStreamRecvWindow + maxStreamSendBuffer)
  + maxHeaderArena
  + maxEvents * 32
  + maxWire
  + fixedOverhead(65536)
```

Defaults: `32*(32768+32768) = 2,097,152`; `+ 1,572,864 + 8,192 + 65,536 +
65,536 = 3,809,280` bytes (~3.63 MiB). `14 * 3,809,280 = 53,329,920` bytes
(~50.9 MiB), plus `32 * 65,536 = 2,097,152` pending bytes (~2.0 MiB) =
55,427,072 bytes (~52.9 MiB), under the 64 MiB platform default. TLS/GnuTLS,
ngtcp2's internal
loss/crypto heap and nghttp3's internal heap are **outside** this charge;
choose `maxConnections` with headroom or raise the platform budget. The
reservation is charged at `openConnection` adoption; pending handshakes get a
small fixed pending charge (`pendingReservation=65536`) and are capped by
`maxPendingHandshakes`.

Fixed constants that are not per-profile `Limits` fields:

| Constant | Value | Use |
|---|---|---|
| `pendingReservation` | 65536 | charge per unadopted handshake |
| `maxDatagramsPerTurn` | 16 | receive fairness |
| `maxSendDatagrams` | 16 | send fairness, one batch |
| `maxStreamWorkPerTurn` | 16384 | per-stream body work |
| `maxStatelessResetsPerPeerPerSecond` | 4 | reset rate per peer |
| `retrySecretRotateMs` | 86400000 | Retry secret rotation |
| `maxRetryTokenLen` | 78 | `NGTCP2_CRYPTO_MAX_RETRY_TOKENLEN` |
| `maxCidLen` | 8 | server CID length |
| `maxActiveCids` | 4 | `active_connection_id_limit` |
| `maxCidTableEntries` | 4096 | routing table hard cap |

`RetryUnderLoad` retries when the pending-handshake count is at least half of
`maxPendingHandshakes`; `RetryAlways` retries every new Initial; `RetryNever`
is loopback-test only.

### 7.5 Flow control and backpressure

- Advertised QUIC transport parameters:
  `initial_max_data = maxConnectionWindow`,
  `initial_max_stream_data_bidi_remote = maxStreamRecvWindow`,
  `initial_max_stream_data_bidi_local = 0` (no server bidi),
  `initial_max_stream_data_uni = maxReadChunk`,
  `initial_max_streams_bidi = maxConcurrentRequests`,
  `initial_max_streams_uni = maxUniStreams`,
  `max_idle_timeout = idleTimeoutMs`,
  `max_udp_payload_size = 1200`,
  `active_connection_id_limit = 4`,
  `max_datagram_frame_size = 0`,
  `disable_active_migration = 1`.
- Receive ring capacity equals the advertised stream window. nghttp3's
  `recv_data` copies application bytes into the ring; the peer cannot exceed
  the window. `readBody` copies out, then extends stream and connection
  offsets by exactly those bytes.
- For control/QPACK/header bytes, the shim extends both offsets by the positive
  value returned from `nghttp3_conn_read_stream` (that return value excludes
  DATA payload). For DATA, the `recv_data` callback supplies the byte count and
  the shim extends only after those bytes leave the ring. There is no
  double-extend path.
- A stalled stream can hold at most its stream window. The connection window
  is `maxConcurrentRequests * maxStreamRecvWindow`; all streams stalled is the
  fair worst case, not one stream starving the rest.
- `discardBody` extends offsets for discarded bytes. A reset/close with unread
  DATA extends at the connection level before the record is reaped. No path
  loses connection credit.
- Send ring capacity is `maxStreamSendBuffer`. `writeBody` accepts only free
  space; `0` means full. `nghttp3`'s data reader pulls from the ring; an empty
  ring returns `NGHTTP3_ERR_WOULDBLOCK` and `nghttp3_conn_resume_stream` is
  called after append.
- When ngtcp2 reports `NGTCP2_ERR_STREAM_DATA_BLOCKED`/`STREAM_SHUT_WR`, the
  shim calls `nghttp3_conn_block_stream`; on `extend_max_stream_data` it calls
  `nghttp3_conn_unblock_stream`. There is no busy retry.
- No auto window update in obo. ngtcp2 may auto-tune its window only if
  `max_window`/`max_stream_window` are set; v1 sets them to 0 (fixed), so the
  reservation is predictable.

### 7.6 Deadlines and fairness

Deadlines are absolute; no partial progress restarts one. `next` checks before
and after I/O. A timeout that can still send a response resets the request
with `H3_REQUEST_CANCELLED`; a connection-level timeout sends
CONNECTION_CLOSE when possible; a handshake timeout closes silently.

Fairness rules:

- At most one `receiveBatch` and one send batch per `next`.
- At most `maxDatagramsPerTurn=16` received and `maxSendDatagrams=32` sent.
- At most `maxStreamWorkPerTurn=16384` body bytes per stream per turn.
- At most 32 events drained per application turn.
- Timers are checked every turn even under continuous traffic.
- A connection with no progress does not block other connections; the server
  loops round-robin over ready connections for output.

## 8. Security and denial of service

### 8.1 Attack and control table

| Attack | Control | Result |
|---|---|---|
| UDP reflection/amplification | 3x limit in ngtcp2, Retry address validation, no application data before handshake confirmed | bounded; spoofed source gets no large response |
| Retry token forgery/replay | AEAD token, address/version/ODCID binding, 10s lifetime, secret rotation | `INVALID_TOKEN`/drop |
| 0-RTT replay | disabled in v1; no tickets; early-data bytes dropped | no replay state |
| Active migration abuse | `disable_active_migration=1`; path validation; 4/min cap | failed validation `NO_VIABLE_PATH` |
| NAT rebinding spoof | path validation; CID live; address parsed/validated | only validated path accepted |
| QPACK bomb | 4 KiB table, 16 blocked, 16 KiB field section, 100 fields, 8 KiB field, 64 KiB block | stream `H3_EXCESSIVE_LOAD`/`QPACK_DECOMPRESSION_FAILED` |
| Header list flood | header cap, arena cap, header deadline | one stream reset; connection survives |
| Rapid reset | reset token bucket 100/20 per s, 1000 streams/conn, 32 concurrent | GOAWAY(`H3_EXCESSIVE_LOAD`) |
| Stream limit abuse | 32 concurrent, 8 uni, 1000 total | `STREAM_LIMIT_ERROR`/`H3_REQUEST_REJECTED` |
| Zero/slow data | handshake/header/body/response/stream deadlines; idle timeout | reset/close; no timer reset by zero bytes |
| Ping/ACK/frame flood | ngtcp2 ACK/flow control; per-turn datagram/stream work caps | bounded CPU; close on persistent abuse |
| Connection ID flood | `active_connection_id_limit=4`; CID table cap; retire | `CONNECTION_ID_LIMIT_ERROR` |
| NEW_CONNECTION_ID flood | limit 4 active; routing table cap | protocol error/close |
| Path challenge flood | path validation cap; 4/min; bounded challenge state | close/ignore |
| Stateless reset abuse | token table 256; 100/s global, 4/s peer; >=21 bytes; response <= input | bounded; no new state |
| Slowloris handshake | 10s handshake timeout; pending cap | close; no state leak |
| QPACK/control stream closure | critical-stream rule | `H3_CLOSED_CRITICAL_STREAM` |
| ALPN confusion | `GNUTLS_ALPN_MANDATORY` `h3`; no h1/h2 over UDP | handshake failure/close |
| Certificate mismatch/SNI | GnuTLS chain/key; SNI diagnostics only; no tenant routing | handshake/421 policy |
| Cross-origin coalescing | first authority pinned; different authority -> 421 | no cross-origin request |
| Response splitting | header token validation in obo/C; no CR/LF/NUL; nghttp3 validation | reject header |
| Error reflection | minimal plain-text body; no request echo/HTML/version | safe error |
| Memory exhaustion | aggregate reservation, per-stream caps, event/wire bounds, QPACK caps | ResourceLimit/Busy |
| Key update abuse | ngtcp2 AEAD/packet-number limits; `KEY_UPDATE_ERROR` | close |
| Datagram/WebTransport smuggling | `max_datagram_frame_size=0`, no `:protocol` | reject |

### 8.2 Amplification and Retry

The server never sends more than 3x the bytes received from an unvalidated
address. ngtcp2 enforces the limit; the shim must not bypass it by writing
application data early or by sending a large Retry packet. Retry validates the
address before connection state is allocated. A public listener defaults to
`RetryAlways`; a loopback test profile may disable it. The Retry token is the
only state carried across the first round trip; it is authenticated, not
encrypted for confidentiality (it contains a CID and timestamp, not secrets).

### 8.3 0-RTT replay policy

v1 rejects 0-RTT. There is no ticket key, no resumption, no early-data accept
path and no `Early-Data` header. If a future phase enables it, the minimum
policy in §4.7 applies: single-use ticket, short lifetime, idempotent methods,
no body/auth, replay cache, explicit `425`. It must not be enabled by a
configuration boolean without those controls.

### 8.4 Migration and connection ID privacy

- `disable_active_migration=1` is the v1 signal. NAT rebinding is still
  handled by path validation; it is not an active migration.
- CIDs are random 8-byte values from `getrandom`; no IP, port, timestamp,
  tenant or sequence is encoded. CIDs rotate on `get_new_connection_id` and are
  retired. Logs use a truncated hash, not the raw CID.
- Stateless-reset tokens are derived with a 32-byte secret and the CID; they
  are not stored in the clear outside C. The tombstone table is bounded.
- No preferred address, no server-initiated migration, no connection ID
  transfer across processes.

### 8.5 QPACK and header controls

- Decoder dynamic table 4 KiB, encoder table 4 KiB, blocked streams 16.
- Decoded field section 16 KiB, 100 fields, 8 KiB per field, raw block 64 KiB.
- The header arena is per-connection and bounded; exceeding it resets the
  stream, not the process.
- QPACK encoder/decoder streams are capped at 256 KiB per connection. A fatal
  QPACK error is a connection error; a single bad field section resets the
  request stream.
- Header copies use explicit lengths; no `strlen`, no NUL termination, no
  duplicate pseudo-header, no connection-specific field.

### 8.6 Stream limits and rapid reset

QUIC has `RESET_STREAM` and `STOP_SENDING`, not HTTP/2 `RST_STREAM`. The attack
shape is the same: open a stream, send minimal data, reset it, repeat. Controls:

- 32 concurrent request streams, 8 client uni streams, 1000 total streams per
  connection.
- Reset token bucket: burst 100, rate 20/s. Exceeding it closes with
  `H3_EXCESSIVE_LOAD`/`ENHANCE_YOUR_CALM`-equivalent policy; there is no
  HTTP/2 `ENHANCE_YOUR_CALM` code in HTTP/3, so use `H3_EXCESSIVE_LOAD`.
- Every reset/close with unread DATA extends connection credit before the
  record is reaped.
- A reset storm must not allocate a new stream record after the cap; the C
  stream table is fixed and indexed by stream ID.
- Counters `resetsSent`, `resetsReceived`, `streamsOpened`, `streamsClosed` and
  `bytesIn` are exposed for detection.

### 8.7 Slowloris and timers

- Handshake timeout 10s; pending handshake cap 32.
- Header timeout 10s from the first request byte.
- Body idle 30s, renewed only by decoded body progress.
- Response idle 60s, renewed only by a positive datagram write.
- Stream total 300s; response total 300s.
- QUIC idle timeout 30s; no live requests.
- No per-byte timer reset. A client that sends one byte per interval still
  hits the absolute deadline.

### 8.8 Key updates, ALPN and certificates

- ngtcp2 handles key updates with `ngtcp2_crypto_update_key_cb`; obo never sees
  key material. A `KEY_UPDATE_ERROR` or `AEAD_LIMIT_REACHED` closes the
  connection. Key update is not a request event.
- ALPN is mandatory `h3`; no `h3-29`, no `h2`, no `http/1.1` on the UDP
  listener. A combined deployment uses a TCP listener for h1/h2 and a UDP
  listener for h3; they are separate `systls`/`quictls` contexts.
- Certificate chain/key are loaded from bounded PEM; TLS 1.3 only; no client
  auth; no OCSP/CRL fetch; SNI is diagnostics only; no wildcard/tenant routing
  in v1. Certificate lifecycle and renewal are deployment concerns, not this
  layer.
- No secrets, tokens, CIDs or packet payloads in logs. Error diagnostics are
  bounded codes/strings, not raw TLS records.

### 8.9 Error mapping

| Native condition | `httpapi::Failure` | Close/reset |
|---|---|---|
| `NGTCP2_ERR_NOMEM` | `ResourceLimit` | CONNECTION_CLOSE `INTERNAL_ERROR` |
| `NGTCP2_ERR_RETRY` | `Refused` (internal control) | send Retry, no close |
| `NGTCP2_ERR_DROP_CONN` | `Closed` | drop silently |
| `NGTCP2_ERR_DRAINING` | `Closed` | no new work |
| `NGTCP2_ERR_VERSION_NEGOTIATION` | `VersionNegotiation` | send VN, drop |
| `NGTCP2_ERR_HANDSHAKE_TIMEOUT` | `Timeout` | CONNECTION_CLOSE `NO_ERROR` |
| `NGTCP2_ERR_CRYPTO` | `TlsFailure{gnutlsCode}` | CONNECTION_CLOSE `CRYPTO_ERROR` |
| `NGTCP2_ERR_PROTO` | `ProtocolFailure` | `PROTOCOL_VIOLATION` |
| `NGTCP2_ERR_TRANSPORT_PARAM` | `TransportParameter` | `TRANSPORT_PARAMETER_ERROR` |
| `NGTCP2_ERR_CONNECTION_ID_LIMIT` | `ConnectionID` | `CONNECTION_ID_LIMIT_ERROR` |
| `NGTCP2_ERR_FLOW_CONTROL` | `FlowControl` | `FLOW_CONTROL_ERROR` |
| `NGTCP2_ERR_STREAM_LIMIT` | `Flood`/`Refused` | `STREAM_LIMIT_ERROR` |
| `NGTCP2_ERR_STREAM_STATE` | `ProtocolFailure` | `STREAM_STATE_ERROR` |
| `NGTCP2_ERR_FRAME_ENCODING` | `BadFrame` | `FRAME_ENCODING_ERROR` |
| `NGTCP2_ERR_FINAL_SIZE` | `ProtocolFailure` | `FINAL_SIZE_ERROR` |
| `NGTCP2_ERR_CRYPTO_BUFFER_EXCEEDED` | `ResourceLimit` | `CRYPTO_BUFFER_EXCEEDED` |
| `NGTCP2_ERR_AEAD_LIMIT_REACHED` | `Closed` | `AEAD_LIMIT_REACHED` |
| `NGTCP2_ERR_NO_VIABLE_PATH` | `Migration` | `NO_VIABLE_PATH` |
| `NGTCP2_ERR_IDLE_CLOSE` | `Timeout` | close silently |
| `NGTCP2_ERR_STREAM_DATA_BLOCKED`/`STREAM_SHUT_WR` | none | block, then resume |
| `NGTCP2_ERR_INVALID_ARGUMENT` | `Internal` | close and report |
| `NGHTTP3_ERR_QPACK_*` | `Qpack` | `QPACK_DECOMPRESSION_FAILED` |
| `NGHTTP3_ERR_H3_FRAME_ERROR` | `BadFrame` | `H3_FRAME_ERROR` |
| `NGHTTP3_ERR_H3_FRAME_UNEXPECTED` | `ProtocolFailure` | `H3_FRAME_UNEXPECTED` |
| `NGHTTP3_ERR_H3_MISSING_SETTINGS` | `ProtocolFailure` | `H3_MISSING_SETTINGS` |
| `NGHTTP3_ERR_H3_CLOSED_CRITICAL_STREAM` | `ProtocolFailure` | `H3_CLOSED_CRITICAL_STREAM` |
| `NGHTTP3_ERR_H3_SETTINGS_ERROR` | `ProtocolFailure` | `H3_SETTINGS_ERROR` |
| `NGHTTP3_ERR_H3_ID_ERROR` | `ProtocolFailure` | `H3_ID_ERROR` |
| `NGHTTP3_ERR_H3_MESSAGE_ERROR` | `HeaderList` | stream `H3_MESSAGE_ERROR` |
| `NGHTTP3_ERR_WOULDBLOCK` | none | data reader waits; no error |
| GnuTLS fatal alert | `TlsFailure{alert}` | map alert; close |
| transport/`sendmsg` error | `TransportFailure{sysCode}` | close server/connection |

`httpapi::text` is diagnostic only. HTTP status mapping is separate: `400`
malformed request, `408` timeout, `413` body too large, `431` header too large
when the block completed, `421` authority mismatch, `500` handler/internal,
`503` admission. Protocol errors never get an HTML page.

## 9. Tests

### 9.1 Deterministic C shim tests (no network)

- `q3_adapter_test.c` links the shim, ngtcp2, nghttp3 and GnuTLS and feeds
  synthetic datagrams through `q3_server_feed`; asserts event/wire sequences.
- Retry: token generate/verify, wrong address, wrong version, expired token,
  replay, malformed token, secret rotation.
- CID: allocation, routing by DCID, retirement, unknown CID stateless reset,
  tombstone cap/eviction, `active_connection_id_limit`.
- Version negotiation: unsupported version -> VN listing v1; too-small packet
  -> drop; no state allocation.
- Stateless reset: minimum size, response <= input, rate limit, no reset for
  small packets.
- Event ring: coalescing, high-water, deliberate overflow -> connection
  failure; server ring overflow -> stop accepting.
- Wire batch: max datagrams, exact `segmentSize`, last shorter segment,
  `WOULDBLOCK` when the buffer is full, no partial datagram.
- H3: control/QPACK stream binding, SETTINGS first, missing/duplicate
  SETTINGS, unknown uni stream, stream type errors.
- Header caps: exactly at and one over count/field/list/block/arena; one reset,
  not a second.
- QPACK: dynamic table cap, blocked stream cap, encoder/decoder stream errors,
  decompression failure.
- Flow control: receive ring capacity, consume-after-copy, discard, reset with
  unread DATA, connection credit restoration, send ring full/blocked/resume.
- Response path: staged headers, commit on write/end, empty body END_STREAM,
  trailers, deferred read callback, partial ngtcp2 acceptance and
  `nghttp3_conn_add_write_offset`.
- Error mapping: every ngtcp2/nghttp3/GnuTLS code used maps to a stable
  `httpapi::Failure` and the correct close code.
- Version checks: `ngtcp2_version`, `nghttp3_version`, GnuTLS version.

### 9.2 Deterministic obo tests (fake datagram)

A `sysnet` memory datagram variant or a test-only `Datagram` shim provides
scripted receive batches, writes, wants, peer addresses and segment sizes. No
sockets, TLS or reactor.

- Server/connection/stream adoption, orphan, close, stale token, double close.
- `next` event order: NewConnection, HandshakeDone, Request, Data, End,
  Trailers, Closed.
- Multiplexed requests: two request streams interleaved; per-stream isolation;
  no response reordering requirement.
- Body: known/unknown/zero length, body over `maxBody`, DATA after END_STREAM
  (reject), trailers after DATA, trailer after END_STREAM (reject).
- Response: known/unknown length, HEAD/204/205/304/1xx, trailers, `end` retry,
  zero-write backpressure, content-length mismatch, over-write.
- Reset: peer RESET/STOP, local reset, close stream, stale shell, unread DATA
  credit restoration.
- Deadlines: fake clock, one byte per interval, handshake/header/body/response/
  stream/idle/drain; no restart on partial progress.
- Admission: max connections, max pending handshakes, max total bytes; rollback
  on failed `openConnection`; reservation survives close.
- Action latch: a pure handler that returns different actions on retries is
  latched to the first action.
- ALPN/mode: missing/wrong ALPN, h3-only, transport-only profile, h2/h1 bytes
  on UDP, 0-RTT bytes rejected.
- Coalescing: second authority -> 421; same authority continues.

### 9.3 h3spec and interop

Primary conformance oracle: **h3spec** (pin the exact release/commit and hash
in the integration job; it is absent now). Run its full HTTP/3 suite against a
loopback TLS listener with a self-signed certificate and ALPN h3; record the
exact CLI and any skip with a written reason. h3spec tests frame ordering,
control streams, SETTINGS, GOAWAY, malformed frames, stream IDs and QPACK
behavior.

Additional interop:

- `gtlsclient` from `ngtcp2-client 0.12.1` (and optionally `gtlsserver` as the
  differential peer). The package is available; the integration job records
  the exact binary hash and CLI.
- `aioquic` Python client (pin the wheel/version and hash) with a scripted
  client: request/response, trailers, multiplexing, flow control, GOAWAY,
  Retry, 0-RTT rejection, migration and loss via a UDP relay.
- curl built from a pinned source with `--with-ngtcp2 --with-nghttp3
  --with-gnutls`; the system curl 8.5.0 has no HTTP3 and cannot be used.
  Run `curl --http3-only -k https://127.0.0.1:<port>/`.
- Go `net/http` HTTP/3 client/server if a pinned Go QUIC stack is added;
  otherwise skip with a reason.
- ALPN matrix: client offers h3, h3-29, h2, http/1.1, none; verify h3-only
  selection/rejection and no h1/h2 parse on the UDP listener.
- `quic-interop-runner` (Docker, pinned commit) is optional; record whether
  the environment can run network namespaces. It is not a default gate.

### 9.4 Handshake, loss, 0-RTT, migration, Retry

- Handshake: full, fragmented Initial/Handshake, missing ALPN, wrong SNI,
  bad cert, TLS 1.2-only client, v2-only client.
- Retry: forced Retry, token expiry, address change, spoofed source, Retry
  disabled loopback.
- Loss: deterministic UDP relay drops/reorders/duplicates 1%, 5%, 20% of
  packets; PTO/loss recovery progresses; no unbounded memory; responses
  eventually complete or time out.
- 0-RTT: client replays a ticket; server rejects early data, completes 1-RTT,
  and does not process the early request; no duplicate side effect.
- Migration: NAT rebinding accepted after validation; active migration refused
  by `disable_active_migration`; failed path validation closes
  `NO_VIABLE_PATH`; no request state loss.
- Idle/handshake timeout: no leak; connection removed from routing table;
  tombstone bounded.

### 9.5 Multiplexing, flow, trailers, GOAWAY, malformed frames

- 32 concurrent requests; 33rd refused; 1000 total then GOAWAY; one stream
  stalled does not starve others.
- 160 KiB response with withheld stream/connection credit; low CPU; grants
  resume.
- Request trailers and response trailers; trailer count/byte caps; trailer
  after END_STREAM rejected.
- GOAWAY notice, barrier, final GOAWAY; requests above cutoff rejected;
  existing requests finish; drain deadline closes.
- Malformed: bad frame type/length, DATA before HEADERS, HEADERS twice,
  SETTINGS on request stream, GOAWAY with bad ID, QPACK instruction errors,
  control stream closed, unknown critical stream, reserved frame types.
- Frame/datagram budget: zero-length DATA/PING-like traffic, unknown frames,
  ACK-only flood; per-turn caps and reset bucket trip; server stays alive.

### 9.6 Sanitizers, leaks and accounting

- C shim and obo server under ASAN/UBSAN; ngtcp2/nghttp3/GnuTLS under LSAN.
- `platform::counts()` returns zero `KIND_QUIC_*` after all shells close;
  `quic::counts().liveConnections == 0`, `liveStreams == 0`; `nativeBytes`
  returns to baseline.
- Deliberately omitted `close` fails the leak gate (positive control).
- Allocation failure at context/server/connection/stream/header/wire/event
  creation returns `ResourceLimit`/`Busy`, closes cleanly, leaks nothing.
- One native release per connection/stream; double close returns false; stale
  shells fail at their own kind check without dereference.
- Adoption fault injection: `_adoptToken` failure after C registration ->
  exactly one release, intended `H3_REQUEST_REJECTED`/close, no double free,
  C table cleanup.
- Fuzz: libFuzzer feeds random and mutated datagrams to `q3_server_feed`;
  no crash, no leak, bounded memory. A second harness fuzzes the response
  header/trailer packet parser. A structured H3 corpus uses aioquic/h3spec
  malformed frames.

### 9.7 Sidecar differential (only if deployed)

If the optional sidecar profile is used, run the same handler matrix through
native H3 and through the terminator + obo h1/h2. Compare status, headers,
body, trailers, reset behavior, timeouts and metrics. Differences are recorded
as sidecar limitations, not used to weaken native H3 gates. The sidecar is not
a conformance oracle for HTTP/3.

### 9.8 Acceptance gates

H3a:

- C harness: Retry/token, CID routing, VN, stateless reset, event/wire batch,
  error mapping, no pointer in cbind, no callback into obo.
- Real handshake with `gtlsclient` or aioquic over loopback with ALPN h3.
- ASAN/LSAN clean; no fd leak; `sysnet` batch fallback works with GSO/GRO
  disabled.

H3b:

- h3spec HTTP/3 sections for control streams, SETTINGS, request/response,
  DATA, trailers, GOAWAY, stream limits, QPACK.
- Multiplexing, flow-control/backpressure, max-streams, deadline, trailer and
  reset-with-unread-DATA tests.
- ASAN/LSAN close with queued response body and pending nghttp3 data reader; no
  UAF.
- Leak counters zero; aggregate memory measured against the reservation.

H3c:

- Full h3spec over TLS h3; aioquic/gtlsclient/curl (if built) differential.
- Retry/token, 0-RTT rejection, migration/NAT rebinding, loss/PTO, rapid reset,
  QPACK bomb, header flood, slowloris, malformed frames, ALPN matrix.
- ASAN/UBSAN/LSAN; fuzz corpus; graceful drain; no request smuggling.

## 10. Phasing, dependencies and effort

| Phase | Scope | Depends on | Exit | Rough effort |
|---|---|---|---|---|
| **P2q: sysnet UDP batch** | `DatagramOptions`, `receiveBatch`, `sendBatch`, `watchDatagram`, `Packet.segmentSize`, GSO/GRO fallback | systems P2 | C/obo tests with GSO on/off; no protocol | 1 engineer-week |
| **H3a: QUIC transport** | `quictls`, C shim, ngtcp2, context/server/connection shells, Retry/CID/VN/stateless reset, event/wire batch, timers, close/drain skeleton | P2q, cinterop P2T-a, pinned headers, GnuTLS | handshake, Retry, CID, timers; no HTTP/3 | 3-4 engineer-weeks |
| **H3b: HTTP/3 mapping** | nghttp3, control/QPACK streams, SETTINGS, request/response, body/trailers, flow control, stream limits, GOAWAY, `runAction`, shared `httpapi` | H3a | h3spec H3 sections, multiplex/flow/trailer tests | 4-5 engineer-weeks |
| **H3c: security/interop** | 0-RTT off policy, migration, QPACK/header caps, reset budget, stateless reset, malformed/fuzz, aioquic/gtlsclient/curl/h3spec, combined TCP+UDP listener and Alt-Svc helper | H3b, H1c/H2c for combined listener | full h3spec, interop, ASAN/leaks, security matrix | 3-4 engineer-weeks |
| **Sidecar option** | pinned terminator, loopback h1/h2 profile, certificate/key deployment, differential harness | existing H1/H2, ops | documented bridge only | 1-2 engineer-weeks |

Total native effort: **10-13 engineer-weeks** for one experienced engineer,
including tests, not review/fuzzing/ops. Review, h3spec triage, fuzzing time and
interop debugging are additional.

Prerequisites:

- `sysnet` P2q implemented and approved; without batch/GSO/GRO, H3 can still
  work one datagram at a time, but the shim must not own a raw socket and the
  design's performance and batching claims are not met.
- cinterop hosted P2T-a and the systems registry (already used by H2/TLS).
- Development headers for `libngtcp2-dev`,
  `libngtcp2-crypto-gnutls-dev`, `libnghttp3-dev`, `libgnutls28-dev`; exact
  package pins in the manifest.
- GnuTLS 3.8.2+ runtime; installed 3.8.3.
- H1c/H2c for a combined TCP listener and Alt-Svc; H3 can be tested on a
  dedicated UDP port before that.
- No compiler, runtime, symbol-format or ABI change is required. The design
  uses the existing pure-handler and concrete-function model; a general impure
  handler remains a language gap.

## 11. Rejected alternatives

1. **OpenSSL 3.0 TCP TLS adapter for QUIC.** No QUIC API; memory BIOs produce
   TLS records; `SSL_set_quic_method` is absent. Reject. `systls` stays TCP.
2. **From-scratch QUIC.** Packet protection, ACK/loss/PTO, congestion, CID,
   path validation, Retry, stateless reset, key update, HTTP/3 and QPACK are
   20k+ lines of security-sensitive code. Reject.
3. **BoringSSL/quictls as a second TLS stack.** No system package/stable ABI;
   vendoring/provenance/audit burden; symbol/interposition ambiguity with
   `systls` and libpq. Reject for v1.
4. **quiche/msquic.** No system package in this archive; second TLS/build/ABI;
   duplicates ngtcp2/nghttp3. Reject for v1.
5. **Sidecar as the primary design.** It does not give obo HTTP/3 semantics,
   the shared handler seam, or control over QPACK/0-RTT/migration/GOAWAY;
   it moves the attack surface and key custody. Reject as primary; allow only
   as a documented deployment bridge.
6. **Plain UDP proxy without TLS.** No encryption, authentication or QUIC
   handshake; not HTTP/3. Reject.
7. **Ignoring congestion, Retry or anti-amplification.** Reflection/amplification
   and congestion collapse. Reject.
8. **One stream at a time.** Defeats QUIC multiplexing and creates head-of-line
   blocking. Reject.
9. **Exposing `ngtcp2_conn*`/`nghttp3_conn*`/GnuTLS pointers to obo.**
   Lifetime/UAF/ABI hazard. Tokens and copied buffers only. Reject.
10. **Letting ngtcp2/nghttp3/GnuTLS callbacks call obo.** Unsupported by
    cinterop; reentrancy/lifetime hazard. C queues bounded events; obo polls.
11. **`dlopen` ngtcp2/nghttp3.** No link-time prototype/provenance; failure
    moves to runtime. Reject.
12. **Per-connection UDP socket.** QUIC CID routing and port sharing need one
    listener; per-connection sockets exhaust fds and break migration. Reject.
13. **HTTP/3 datagrams, WebTransport, push, client mode in v1.** Extra state,
    authorization and abuse surface; not needed. Reject.
14. **0-RTT in v1.** Replay policy and ticket lifecycle need a separate
    review. Reject/defer.
15. **Active migration / preferred address in v1.** Path validation and address
    policy add state; `disable_active_migration=1` is the safe default.
16. **Cross-authority coalescing in v1.** Certificate-scope and request
    smuggling ambiguity; pin the first authority and return 421. Reject.
17. **Ignoring QPACK dynamic-table bounds.** Unbounded blocked streams/table
    memory. Reject.
18. **Using `SSL_CTX`/`systls` certificate loading for QUIC.** Different
    library, different callbacks; a shared PEM convenience is fine, a shared
    context is not.
19. **Auto-PMTUD in v1.** Black-hole and GSO size interactions need their own
    tests; start at 1200 with DF. Defer.
20. **Treating HTTP/1.1/2/3 as one transport interface.** obo 0.14 has no
    impure interface-member tier; transports differ materially. Share
    `RequestMeta`/`Action`, not a fake interface.

## 12. Open choices and recommendations

| Choice | Recommendation | Consequence otherwise |
|---|---|---|
| Native vs sidecar | Native ngtcp2+nghttp3+GnuTLS; sidecar only as a bridge | Sidecar cannot enforce H3/QUIC policy or share the handler seam |
| QUIC library | ngtcp2 0.12.1 exact Ubuntu package | A different 0.x version is an API port; a pristine 0.12.1 lacks the CVE patch |
| HTTP/3 library | nghttp3 0.8.0 exact package | No security update in this archive; monitor and plan a reviewed port |
| TLS | GnuTLS 3.8.3 `ngtcp2_crypto_gnutls`; separate `quictls` | OpenSSL 3.0 cannot do QUIC; sharing `systls` is impossible |
| Retry | `Always` on public UDP; `Never` on loopback tests | Without Retry, spoofed sources can allocate state up to the 3x/limits budget |
| 0-RTT | Off in v1; re-review later | Enabling without replay controls creates duplicate side effects |
| Migration | `disable_active_migration=1`; validated NAT rebinding only | Active migration needs path/address policy and more state |
| Coalescing | One authority per connection; 421 otherwise | Cross-origin coalescing needs SAN/cert introspection and smuggling review |
| Max streams | 32 concurrent bidi, 8 uni, 1000 total | Higher caps raise per-connection memory and reset/QPACK exposure |
| Flow-control windows | 32 KiB stream, 1 MiB connection | Larger windows raise the 64 MiB reservation per connection |
| QPACK | 4 KiB table, 16 blocked, 16 KiB field section | Larger tables improve compression but increase bomb/blocked-state risk |
| UDP batching | `receiveBatch`/`sendBatch`, GSO/GRO best-effort | One datagram at a time is correct but slower; shim must not own the socket |
| PMTUD | Off; 1200-byte payload | Enabling PMTUD needs black-hole/GSO tests and a higher path budget |
| Test oracle | h3spec + aioquic + gtlsclient; curl built if possible | System curl 8.5 has no HTTP3; h3spec/aioquic are absent now |
| Versioning | No obo version; package pins; QUIC v1 only | QUIC v2/HTTP/3 extensions need a new pin and review |
| Alt-Svc | Optional helper on TCP listeners, not core | Advertising h3 without a serving UDP port misleads clients |
| Sidecar fallback | Pinned terminator + loopback h1/h2, explicitly labeled | Do not claim obo speaks HTTP/3 through a proxy |

## 13. Invariants and review questions

### 13.1 Invariants

1. One UDP socket per server; many QUIC connections are logical and bounded by
   `maxConnections` plus `maxPendingHandshakes`.
2. No HTTP/3 byte is parsed before ALPN `h3` and a completed TLS handshake.
3. No 0-RTT request is accepted in v1; no session ticket is issued.
4. `ngtcp2_conn*`, `nghttp3_conn*`, GnuTLS handles and all C structs stay in C;
   only scalars, tokens and bounded buffers cross the cbind.
5. C callbacks never call obo; they enqueue fixed events and copy bounded
   payloads. Event-ring overflow is a connection failure, never silent loss.
6. Header/body payloads live in bounded per-stream C buffers; events carry no
   payload; no whole-body allocation.
7. `recvBuffered[s] <= maxStreamRecvWindow`; consume only after bytes leave the
   ring; every received application byte is delivered+extended or
   discarded+extended. No retirement path loses connection credit.
8. `sendBuffered[s] <= maxStreamSendBuffer`; accepted bytes are sent exactly
   once or the stream is reset.
9. Wire bytes live in the C batch or the obo send buffer, never both; the
   batch never contains a partial datagram.
10. Every deadline is absolute; no partial progress restarts it.
11. GOAWAY precedes graceful close; `drain` is graceful, `close` is abortive.
12. A connection close orphans stream shells; a stream shell close retires its
    own token exactly once. Dropping a record frees only the record.
13. `_adoptToken` failure after C registration performs exactly one native
    release and no double free.
14. CIDs are random, not address/time encoded; stateless-reset tokens are
    secret-derived and rate-limited.
15. Retry validates the address and binds the token; anti-amplification is
    never bypassed.
16. QPACK/header/stream/event/wire caps are validated with checked arithmetic
    before allocation or native call.
17. All request/response header fields are validated before use; no CR/LF/NUL
    injection, no request echo, no HTML error page.
18. A handler error after headers commit resets the stream; before commit it
    sends a minimal 5xx; no partial header block is emitted.
19. One stream cannot monopolize a turn; per-stream work and per-turn datagram
    caps are enforced.
20. The shared seam is `RequestMeta` + `Action` + concrete functions; there is
    no fake transport/exchange interface and no callback into obo.
21. HTTP/1.1, HTTP/2 and HTTP/3 share handler semantics but not transport
    identity; `RequestMeta.protocol` and framing differences are explicit.
22. The system curl/tooling gaps are recorded, not hidden: no HTTP3 in curl
    8.5, no h3spec/aioquic/quic-interop-runner installed, no OpenSSL QUIC.
23. A sidecar is never described as native HTTP/3; if used, `RequestMeta`
    remains h1/h2 and the limitation is documented.

### 13.2 Review questions

1. Is the native ngtcp2+nghttp3+GnuTLS decision defensible against the exact
   package versions, the CVE-2026-40170 pin, and the absence of QUIC v2?
2. Is the `quictls` separation from `systls` complete, especially certificate
   loading, ALPN, early data, key update and error mapping?
3. Is the `sysnet` P2q batch/GSO/GRO extension the right seam, or should the
   QUIC shim own the UDP socket? Does the fallback preserve all-or-error
   datagram semantics and reactor ownership?
4. Is the one-socket/many-connections demux, CID table, tombstone and Retry
   design correct and bounded?
5. Is the C shim boundary (fixed events, pull-based headers/body, per-stream
   rings, `nghttp3` write-offset accounting, no pointers) implementable and
   sufficient?
6. Are the HTTP/3 SETTINGS/QPACK/header/stream limits safe, and are the
   stream-vs-connection error choices correct?
7. Are the QUIC and HTTP/3 deadlines coherent, especially handshake, PTO,
   header, body, response and drain?
8. Is `disable_active_migration=1` plus validated NAT rebinding the right v1
   migration policy?
9. Is the 0-RTT-off decision acceptable for v1, and is the future policy
   sufficiently constrained?
10. Is one-authority-per-connection coalescing policy defensible, and is 421
    the right response?
11. Is the shared `httpapi`/`Handler`/`Action` seam honest about the missing
    impure handler tier and the transport differences?
12. Are the test oracles and missing-tool policy sufficient, especially
    h3spec/aioquic/curl and the sidecar differential?
13. Are the reservation formula and 64 MiB/14-connection default safe once
    GnuTLS/ngtcp2/nghttp3 internal heaps are considered?
14. Is the phasing right: P2q and H3a before H3b, and H3c after a working
    h3spec/aioquic path?

## 14. Conclusion

The recommended implementation is a native HTTP/3 origin server built from a
thin, trusted C shim around ngtcp2 0.12.1, nghttp3 0.8.0 and GnuTLS 3.8.3,
with a separate `quictls` unit and a `sysnet` UDP batch/GSO/GRO extension. obo
owns the server/connection/stream shells, admission, limits, deadlines,
backpressure, graceful drain and the shared `httpapi` handler seam. The C shim
owns QUIC, HTTP/3/QPACK and TLS state and never calls into obo.

This keeps the security-critical packet protection, loss recovery, congestion,
QPACK and HTTP/3 framing in maintained system libraries, while making obo
responsible for the parts a library cannot decide: which connections to admit,
which streams to fund, how long to wait, what to log, how to drain, and how the
application handler sees a request. It does not pretend HTTP/1.1, HTTP/2 and
HTTP/3 are the same transport, and it does not pretend a sidecar is native
HTTP/3.

The design is ready for independent review, not implementation. The first
implementation gate is P2q plus a compiled shim against the pinned headers, a
real GnuTLS/ngtcp2 handshake with `gtlsclient` or aioquic, and the C harness
proving Retry/CID/event/wire/flow-control invariants.
