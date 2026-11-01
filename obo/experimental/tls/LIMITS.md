# Experimental TLS adapter: limits and known gaps

Status: P2T-b implementation, pending independent review. No security,
version, ABI or production-readiness claim.

## Protocol and policy

- Server mode only. `connect` is `Unsupported`; there is no client cert
  selection, no SNI-based certificate selection, no multi-certificate context.
- TLS 1.2/1.3 only. TLS 1.0/1.1/SSLv3 are unrepresentable; no downgrade retry.
- Session tickets, resumption and 0-RTT are disabled unconditionally. There is
  no public option to enable them.
- No renegotiation, no TLS 1.3 post-handshake auth, no TLS compression.
- No CRL/OCSP or revocation checking; a revoked-but-valid certificate is
  accepted unless deployment policy rejects it outside this adapter.
- SNI is captured/logged and can be required for presence only. It is not
  authorization and does not select a certificate.
- Default ALPN advertises only `http/1.1`; `h2` is available only when the
  application explicitly sets it. HTTP integration is P2T-d.

## Memory and resource bounds

- `readSome`/`writeSome` are 1..65,536 bytes; `flush` moves at most 65,536
  bytes per call.
- Per-session read/write BIO pending is capped at 131,072 bytes; C staging is
  131,072 bytes. Exceeding a cap fails the session with `ResourceLimit`.
- Handshake ciphertext is capped by `ContextOptions.maxHandshakeBytes`
  (16 KiB..8 MiB, default 1 MiB).
- Certificate chain: <=1 MiB PEM, <=16 certs, each DER <=256 KiB.
- Private key: <=64 KiB PEM, unencrypted only. Encrypted keys fail cleanly;
  there is no stdin prompt and no passphrase API.
- Trust bundle: <=4 MiB PEM, <=256 certs. Replacement is atomic; a failed
  parse leaves the previous store intact.
- ALPN: <=16 protocols, each 1..255 bytes, total plus one length byte per
  protocol <=1024, no NUL/duplicates. Cipher/group lists are <=64 items, each
  1..255 bytes of `[A-Za-z0-9_-]`, joined length <=1024 including colons.
- `maxSessions` 1..4096 (default 256 server); `maxContextBytes` 1 MiB..1 GiB
  (default 64 MiB). `maxContextBytes` charges the context structure, the
  bounded PEM input lengths retained for certificate/key/trust policy,
  per-session structures, staging and BIO pending, **not** OpenSSL's internal
  heap or allocator overhead. It is a DoS bound for adapter-owned memory, not
  an RSS limit. Committing drained ciphertext lowers the charge.
- `tls_context_set_alpn`'s C-side duplicate validation is defense in depth;
  the obo wrapper validates the public list first.
- Cipher/group setters accept only the exact approved names:
  `TLS_AES_256_GCM_SHA384`, `TLS_CHACHA20_POLY1305_SHA256`,
  `TLS_AES_128_GCM_SHA256`; `ECDHE-ECDSA/RSA-AES256-GCM-SHA384`,
  `ECDHE-ECDSA/RSA-CHACHA20-POLY1305`,
  `ECDHE-ECDSA/RSA-AES128-GCM-SHA256`; and `X25519`, `P-256`, `P-384`.
  Everything else, including OpenSSL expressions/directives (`@SECLEVEL=0`,
  `ALL`, `CBC`, `SHA`, `AES`, `eNULL`/`NULL`, export/anon/RC4/3DES/DES/MD5,
  CBC suite names) is `InvalidArgument`. Security level 2 is re-applied and
  verified after every cipher/group setter; a native failure poisons the
  context.
- A fatal handshake/read/write/shutdown error makes one bounded best-effort
  flush of queued alert output before the error is returned. A caller that
  still has pending ciphertext should call `flush`/`handshakeStep` until the
  transport closes. On this OpenSSL 3.0.13 build the ClientHello-presence
  callback rejection is visible on the wire as `handshake_failure` (40),
  while the obo result remains `AlpnRequired`; the no-overlap path sends
  `no_application_protocol` (120).
- `want` returns `WantNone` when plaintext is buffered or the receive side is
  closed; the caller should call `readSome` rather than wait for readability.
  `isSecure` is true only for an established, non-NULL session in
  `Established`/`SendClosed`/`RecvClosed`, not after both directions close.
- If a public `res` replacement bypasses `closeContext`, context release
  detaches live sessions instead of leaking the context. Handshaking sessions
  are marked `Failed` and subsequent handshake/feed calls return `Closed`;
  callbacks carrying the context pointer are cleared. Established detached
  sessions remain closable, preserve the stored verification mode/code, and
  no longer call context callbacks.
- OpenSSL's per-process global state, provider and error queue are shared with
  every other OpenSSL user (for example libpq). The adapter clears the queue
  around its own calls; other components must read errors promptly.
- No `CRYPTO_set_mem_functions` is installed. OpenSSL's internal heap is not
  fully charged to the systems budget.

## Process, fork and threads

- Hosted build only; no freestanding TLS.
- No hidden threads, worker threads or automatic finalizers. v1 is
  single-threaded; do not share a context/session across workers.
- No `fork` with a live `SSL_CTX` or session. Start TLS after fork/exec in the
  child. `OPENSSL_init_ssl(OPENSSL_INIT_NO_LOAD_CONFIG)` is process-global and
  idempotent; another library may already have initialized OpenSSL.
- If another OpenSSL user is present, the adapter cannot undo an earlier
  configuration decision.

## Public-field and trusted-C limits

- `TlsSession.transport` is private, so external construction is rejected.
- `TlsContext` intentionally has only `pub res`. A caller can hand-build a
  `TlsContext` with a live `Resource` from another context; that is the
  documented public-field hazard, not a capability boundary.
- C is trusted. The registry rejects stale/forged scalar tokens, but a
  same-signature C/header/library substitution is trusted build input. Hashes
  are provenance/corruption checks, not signatures.
- The cinterop manifest is not a runtime proof that the loaded `libssl.so.3`
  is the one hashed at build time.

## Experimental build arrangement

- OpenSSL headers are a build dependency, fetched into a private cache by the
  test script when the matching `libssl-dev` package is not installed. No
  extracted header is committed.
- The committed cbind has no absolute include paths. It includes the systems C
  adapter sources because the experimental cinterop build links one spec per
  executable.
- The adapter uses the systems registry and kinds 23/24; this is not a
  compiler/runtime/ABI change and not a stable platform promise.
- POSIX absolute deadlines and reactor watches belong to the caller. P2T-b has
  no `handshakeUntil` helper and no cancellation-wake claim.
