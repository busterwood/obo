# Experimental TLS adapter (P2T-b)

Status: **experimental server-side TLS 1.2/1.3 adapter implemented; pending
independent review.** This is not a stable API, not an ABI, and not approved
for merge/promotion. It does not claim that an obo server is secure by itself.

The authoritative design is
[`../../design/tls-adapter-design.md`](../../design/tls-adapter-design.md).
This directory contains the ordinary obo wrapper (`systls.obx`), the trusted C
adapter under `native/`, the cinterop contract (`tls_adapter.cbind`) and
test-only fixtures/builders under `tests/`.

## Scope (P2T-b)

- server-only TLS over an owned `sysstream::Stream`; OpenSSL 3.0 memory BIOs,
  never `SSL_set_fd`;
- TLS 1.2/1.3 minimum/maximum selection, explicit cipher/group policy,
  ALPN (Disabled/Prefer/Require), SNI presence policy, session tickets off,
  no 0-RTT, no renegotiation, security level 2;
- bounded PEM certificate chain/private-key/trust-bundle loading from bytes
  (the module never reads a path);
- explicit step API: `handshakeStep`, `readSome`, `writeSome`, `flush`,
  `shutdownStep`, `pending`, `want`, `watch`; output-first after every
  OpenSSL call; one transport read and one transport write at most per step;
- copied handshake information and peer identity; explicit `close`,
  `closeContext` (`Busy` while sessions live), leak counters;
- single `systls` error family, OpenSSL error-queue drain and bounded
  diagnostics.

Out of scope: client mode (`connect` returns `Unsupported`), HTTP/1.1 or HTTP/2,
QUIC/DTLS, session tickets/resumption/0-RTT, asynchronous engines, OCSP/CRL,
multi-certificate SNI selection, threads/workers, and automatic finalizers.

## Layout

```text
obo/experimental/tls/
  systls.obx                 ordinary obo API and state machine
  tls_adapter.cbind          scalar-only cinterop contract
  native/tls_adapter.h       exported C symbols and packet/status constants
  native/tls_adapter.c       trusted OpenSSL adapter, C-only callbacks
  tests/apps/tls_echo_app.obx  public-API echo/listener fixture
  tests/c/tls_harness.c      C memory-BIO protocol harness (normal + ASAN/UBSAN)
  tests/test_tls.py          one-command hosted suite
```

The C adapter uses the experimental systems registry (`../sys/native/`) for
its private context/session tokens. `TlsContext.res`/`TlsSession.res` are
`platform::Resource` values; the C token and slot table stay module-private.
`TlsSession.transport` is private (0.13 field rule), so external code cannot
construct a session; `TlsContext` has only the public `res` field and an
unregistered hand-built context fails `InvalidResource`.

## Build

The OpenSSL development headers are a build dependency. They are not committed.
`tests/test_tls.py` locates matching installed headers, or fetches the exact
`libssl-dev` package without root into a private cache (same policy as the
P2T-a cinterop qualification), and generates a private `.cbind` with absolute
`include_dirs`. The committed `.cbind` deliberately has no machine-specific
paths.

The build links `libssl.so.3` and `libcrypto.so.3` with `-pthread`; hosted
cinterop always adds full RELRO, immediate binding and non-executable stack.
The cbind includes the systems adapter sources because the experimental
systems library has no separate multi-spec link step. This is an experimental
build arrangement, not a packaging claim.

## What this is not

- not a stable native ABI, not a stable obo API, no version/format/seed change;
- not a replacement for deployment certificate lifecycle, HTTP policy,
  authentication or rate limiting;
- not a sandbox against trusted C or OpenSSL;
- not proof that OpenSSL's internal heap is fully charged to the systems
  budget (see `LIMITS.md`).

## Differences from the design sketch that are forced by the 0.14 language

The design API sketch names several variants `None`, `Done`, `WantRead`,
`WantWrite` in different closed types. obo closed-variant constructors share
module scope, and optional values are allowed only for reference types. The
implementation therefore uses unique names (`WantNone`, `StepDone`,
`ReadData`, `ShutdownSent`, `VerifyNone`, ...) and represents absent ALPN/SNI
strings as `""` rather than `?string`. This is a source-level naming change,
not a protocol/security change; see `API.md`.
