# `systls` public API (experimental P2T-b)

Status: experimental, hosted-only. No version or ABI is claimed. All functions
return `Result[...]`; every `err` code is a `systls` code, not a systems code
and not a raw errno. Decode it with `systls::failure(code)` and `systls::text`.

## Shells and construction

```obo
pub type TlsContext = ref record { pub res: platform::Resource }
pub type TlsSession = ref record { pub res: platform::Resource; transport: stream::Stream }
```

`transport` is private. A session is created only by `systls::accept` (or, in a
future P2T-c, `connect`). A hand-built `TlsSession` cannot cross the module
boundary; a hand-built `TlsContext` has an unregistered `res` and fails
`InvalidResource`. `TlsContext` owns one `SSL_CTX`; `TlsSession` owns one
`SSL`, its memory BIOs and exactly one `Stream`. `close`/`closeContext` are
explicit; record drop never calls OpenSSL or closes the stream.

## Options

`serverOptions()` / `clientOptions()` return `ContextOptions` with defaults:
TLS 1.2–1.3, server verify `VerifyNone`, ALPN `Prefer(["http/1.1"])`, SNI not
required, depth 8, 256 sessions (server) / 64 (client), 1 MiB handshake,
64 MiB context budget. Client mode is represented but `connect` is
`Unsupported` in P2T-b.

Variant names are unique in this module because obo closed-variant constructors
share module scope:

- `Role`: `Server`, `Client`
- `Version`: `Tls12`, `Tls13`
- `VerifyMode`: `VerifyNone`, `VerifyOptional`, `VerifyRequired`
- `AlpnPolicy`: `Disabled`, `Prefer{protocols: []string}`,
  `Require{protocols: []string}`
- `Want`: `WantNone`, `WantRead`, `WantWrite`, `WantReadWrite`
- `Step`: `StepDone`, `StepWantRead`, `StepWantWrite`
- `ReadOutcome`: `ReadData{bytes, want}`, `ReadEof{want}`,
  `ReadWantRead`, `ReadWantWrite`
- `WriteOutcome`: `WriteWrote{count, want}`, `WriteWantRead`,
  `WriteWantWrite`
- `FlushOutcome`: `FlushDone`, `FlushWantWrite`
- `ShutdownOutcome`: `ShutdownDone`, `ShutdownSent{want}`,
  `ShutdownPeerClosed{want}`, `ShutdownWantRead`, `ShutdownWantWrite`

`HandshakeInfo.alpn` and `HandshakeInfo.serverName` are strings; `""` means no
ALPN/SNI (obo has no optional string type).

## Functions

```obo
pub fn createContext(options: &ContextOptions): Result[TlsContext]
pub fn loadCertificateChain(ctx: &TlsContext, pem: &[]byte): Result[bool]
pub fn loadPrivateKey(ctx: &TlsContext, pem: &[]byte): Result[bool]
pub fn setTrustedCertificates(ctx: &TlsContext, pem: &[]byte): Result[bool]
pub fn useSystemTrustStore(ctx: &TlsContext): Result[bool]  // client-only; Unsupported server
pub fn setAlpn(ctx: &TlsContext, policy: &AlpnPolicy): Result[bool]
pub fn setMinVersion(ctx: &TlsContext, version: &Version): Result[bool]
pub fn setMaxVersion(ctx: &TlsContext, version: &Version): Result[bool]
pub fn setCipherSuites(ctx: &TlsContext, suites: []string): Result[bool] // TLS 1.3
pub fn setCipherList(ctx: &TlsContext, ciphers: []string): Result[bool]  // TLS 1.2
pub fn setGroups(ctx: &TlsContext, groups: []string): Result[bool]
pub fn accept(ctx: &TlsContext, transport: stream::Stream): Result[TlsSession]
pub fn connect(ctx: &TlsContext, transport: stream::Stream,
               identity: &ServerIdentity): Result[TlsSession] // Unsupported
pub fn handshakeStep(s: &TlsSession): Result[Step]
pub fn readSome(s: &TlsSession, max: int): Result[ReadOutcome]
pub fn writeSome(s: &TlsSession, bytes: &[]byte, offset: int,
                 count: int): Result[WriteOutcome]
pub fn flush(s: &TlsSession): Result[FlushOutcome]
pub fn shutdownStep(s: &TlsSession): Result[ShutdownOutcome]
pub fn pending(s: &TlsSession): Result[Pending]
pub fn want(s: &TlsSession): Result[Want]
pub fn info(s: &TlsSession): Result[HandshakeInfo]
pub fn peerIdentity(s: &TlsSession): Result[PeerIdentity]
pub fn alpn(s: &TlsSession): Result[string]
pub fn watch(s: &TlsSession, r: &reactor::Reactor, readable: bool,
             writable: bool, tag: int): Result[reactor::Watch]
pub fn maxTransfer(s: &TlsSession): int // 65536
pub fn isSecure(s: &TlsSession): bool
pub fn close(s: &TlsSession): Result[bool]
pub fn closeContext(ctx: &TlsContext): Result[bool]
pub fn failure(code: int): Failure
pub fn text(code: int): string
pub fn verifyText(code: int): string
pub fn counts(): Result[Counts]
pub fn contextCounts(ctx: &TlsContext): Result[ContextCounts]
```

## Step contract

- Every call is nonblocking and bounded. At most one transport read and one
  transport write happen per step; at most one OpenSSL I/O call.
- Output-first: after `SSL_do_handshake`, `SSL_read_ex`, `SSL_write_ex` or
  `SSL_shutdown`, pending write-BIO/staging bytes turn `WantRead` into
  `WantWrite`. A `Done`/`ReadData`/`ReadEof`/`WriteWrote`/`ShutdownSent`
  outcome carries `want=Write` when output remains.
- `handshakeStep` may read one transport chunk before the SSL call. It returns
  `StepDone` only when handshake output is empty. Use `want(s)`/the outcome to
  set reactor interest; the caller owns the absolute deadline.
- `readSome` flushes prior output before reading; it returns `ReadEof` only
  after a received `close_notify`. Transport EOF without close_notify is
  `Failure.Truncated`.
- `writeSome` accepts at most 65,536 bytes, copies the exact
  `bytes[offset..offset+count]` range (the native call then uses offset 0),
  reports the accepted prefix exactly once, and never replays it. It does not
  flush newly produced ciphertext; call `flush`/handle `want=Write`.
- `flush` performs at most one transport write and never returns
  `WantRead`. `Done` requires both staging and the write BIO to be empty.
- `shutdownStep` drives `SSL_shutdown`; `Done`/`Sent`/`PeerClosed` carry
  `want` when close_notify output remains. On a fatal error the wrapper makes
  one bounded best-effort flush of queued alert output before returning the
  error.
- `close` is abortive; it may discard unsent plaintext. Use `shutdownStep` for
  a clean close_notify exchange. `close` retires the session token even if the
  transport close reports an error; a second close returns `ok(false)`.
- `closeContext` returns `Busy` while any session of that context is live.
  If a public `res` replacement bypasses it, context release detaches live
  sessions: handshaking sessions become `Failed` and their later
  handshake/feed calls return `Closed`; established sessions remain closable
  and retain their copied verification classification.
- `want(s)` returns `WantNone` when plaintext is already buffered or the
  receive side is closed; call `readSome` rather than waiting for readability.
- `isSecure(s)` is true only for an established, non-NULL session that is not
  both-closed.

`setCipherSuites`/`setCipherList`/`setGroups` accept only the exact approved
AEAD suites/groups listed in `LIMITS.md`. OpenSSL expressions/directives
(`@SECLEVEL=0`, `ALL`), broad keywords (`CBC`, `SHA`, `AES`), `eNULL`/`NULL`,
export/anon/RC4/3DES/DES/MD5 and CBC suite names are `InvalidArgument`.
Security level 2 is re-applied and checked after each setter.

## Errors

`Failure` variants: `InvalidArgument`, `Range`, `InvalidResource`, `Closed`,
`HandshakeFailed`, `ProtocolAlert{description}`, `ProtocolViolation`,
`VerifyFailed{verifyCode}`, `NoPeerCertificate`, `CertificateRejected`,
`KeyRejected`, `Configuration`, `AlpnRequired`, `NoApplicationProtocol`,
`AlpnMismatch`, `Truncated`, `NotReady`, `Transport{sysCode}`, `ResourceLimit`,
`Busy`, `NotConfigured`, `Unsupported`, `Internal`.

`WantRead`/`WantWrite` are control flow, never `Failure` values. `Transport`
carries the original systems library code (or, for an OpenSSL syscall error,
the captured errno) in its payload. Text is diagnostic only; branch on
`failure(code)`.

## Example

```obo
let opts = systls::serverOptions()
let ctx = try systls::createContext(&opts)
defer systls::closeContext(&ctx)
try systls::loadCertificateChain(&ctx, &certPem)
try systls::loadPrivateKey(&ctx, &keyPem)
let session = try systls::accept(&ctx, transport)
defer systls::close(&session)
// call handshakeStep/readSome/writeSome/flush/shutdownStep with an
// application-owned reactor watch and absolute deadline.
```
