# TLS transport adapter

Status: **revision 3 design for focused independent re-review; no
implementation authorized.** Revision 3 resolves R1/R2/R3 from the re-review
of `2c8a824`; revision 2 resolved the earlier `ea02b29` blockers/major issues.
See §15.1 for the resolution map.
Baseline: current `main` `2364add3a7ad0a02ec9412b6834a36a510d45f32`,
released obo `0.12`, symbol format 6, ABI 1. Date: 2026-10-31.
No compiler, ABI, symbol-format or package version is reserved by this note.

The API sketches use only the `0.11` source feature set: records, closed
variants, `Result[T]`, `defer`, single ownership, borrowed `&T` and checked
`^T`. They do not require `0.12` decimal, private-by-default fields, interface
handles or any new compiler feature. They are written against the current
`0.12` baseline because that is what `main` contains; an implementation may be
built with the `0.11` compiler if the systems and cinterop prerequisites are
present.

Related notes and code: [systems-stdlib-design.md](systems-stdlib-design.md),
[systems-threads-design.md](systems-threads-design.md),
[native-c-interop-design.md](native-c-interop-design.md),
[cinterop README](../experimental/cinterop/README.md),
[cinterop skill](../experimental/cinterop/skill/SKILL.md),
[DB adapters README](../experimental/db/README.md).

Reading guide: §1 scope; §2 environment evidence; §3 library/build decision;
§4 ownership and the C boundary; §5 API; §6 nonblocking state machine; §7 C
callbacks; §8 security policy; §9 errors; §10 tests; §11 phases and HTTP
integration; §12 rejected alternatives; §13 open choices; §14 invariants and
review questions.

## 1. Decision and scope

Build a **server-side TLS 1.2/1.3 transport adapter over TCP** on top of the
approved `sysstream`/`sysreactor` layer, using the system
**OpenSSL 3.0** `libssl.so.3` and `libcrypto.so.3` through **memory BIOs**.
The adapter exposes an ordinary obo byte stream and a small handshake/close
state machine. It does not parse HTTP, does not own application protocol
framing, and never calls back into obo.

Summary of decisions:

| Decision | Choice |
|---|---|
| Provider | OpenSSL 3.0 from the operating system; no vendored crypto implementation |
| Build inputs | OpenSSL development headers required; declarations-only rejected for TLS |
| Native boundary | scalar `long` C ABI; memory BIOs; no `SSL*`/`BIO*`/`X509*` to obo |
| Ownership | owned `TlsContext` and `TlsSession` shells; **non-optional** owned `Stream`; target private `transport`; explicit close |
| Version policy | TLS 1.2 minimum, TLS 1.3 maximum, no fallback |
| ALPN | default `http/1.1`; `h2` only after HTTP/2; `Require` enforced by C ClientHello-presence + select callbacks |
| Client mode | separate design; server first |
| Session resumption | **disabled entirely in v1; no option, no 0-RTT** |
| Output | every progress outcome carries `want`; explicit `flush`; no stranded Finished/close_notify |
| Handshake helper | step API only in P2T-b; `handshakeUntil` deferred |
| Hosting | hosted build only; no freestanding TLS, no hidden threads |
| Phase | P2T after syslib P2 **approval and implementation** plus cinterop hosted qualification |

### 1.1 Scope

- Server-side TLS 1.2 and 1.3 over an established nonblocking TCP
  `sysstream::Stream`.
- ALPN negotiation for `h2` and `http/1.1`.
- Certificate chain and private key loading from bounded PEM bytes, with
  explicit trust anchors for client-certificate verification.
- Nonblocking handshake, read, write, **flush** and multi-step shutdown;
  explicit `WantRead`/`WantWrite`/`want`; absolute caller deadlines; no hidden
  retries and no stranded output.
- Peer certificate identity and verification result as copied value records.
- Per-context and per-session resource identity, generation checks, explicit
  close and leak counters.
- A local conformance path using `openssl s_client` and, after HTTP
  integration, `curl` with HTTP/1.1 and HTTP/2.

### 1.2 Non-goals

- No HTTP/1.1, HTTP/2, HTTP/3 request parsing, framing, routing, header
  handling, compression negotiation or WebSocket upgrade. Those live above
  this adapter.
- No HTTP/3 or QUIC. OpenSSL 3.0.13 has no QUIC API in this environment; the
  installed `libssl.so.3` exports no QUIC symbol and the extracted 3.0.13
  headers contain no QUIC API. QUIC needs a separate design and a newer
  library.
- No DTLS.
- No client mode in the first deliverable. The API reserves `connect` and
  hostname verification for P2T-c.
- No 0-RTT early data, no PSK, no custom certificate selection by SNI, no
  OCSP stapling, no dynamic private keys, no asynchronous engine.
- No freestanding/static image, no thread-per-connection, no hidden worker
  thread, no TLS context shared across processes or workers.
- No automatic finalizer. Record drop frees the obo record only.
- No claim that this design makes an obo server "secure" by itself. It is one
  transport component; deployment, certificate lifecycle, HTTP policy,
  authentication and rate limiting are separate.

## 2. Baseline and environment evidence

### 2.1 Repository evidence

| Area | Evidence | Consequence for this design |
|---|---|---|
| Stream contract | `obo/design/systems-stdlib-design.md`:888–925 defines `sysstream::Stream`, `readSome`/`writeSome`, `readInto`/`writeFrom`, half-close and explicit close | The TLS session must consume an owned `Stream`; it must not expose a raw fd or bypass partial-transfer rules |
| Bounded buffers | systems design:927–968 defines `sysbuffer::Buffer`, limits and step APIs | TLS uses one bounded staging buffer per direction; no unbounded record reassembly in obo |
| Reactor | systems design:1227–1310 defines `sysreactor`, level-triggered readiness, timers, `watchResource`, `wait` | TLS steps return `WantRead`/`WantWrite`; the application owns readiness and deadlines |
| Wait helpers | systems design:970–1018 defines `syswaitio` deadline/cancellation helpers | P2T-b does **not** use them: no TLS helper blocks in `reactor::wait`, so no cancellation-wake claim |
| TLS readiness | systems design:1614–1646 (§10) says a TLS engine may use C-side memory BIOs with explicit feed/drain and no obo callback; result must distinguish need-read/write from fatal | This note adopts memory BIOs and explicit outcomes |
| Shell identity | systems design:251–307 defines `res: platform::Resource`, `source`/`requireKind`, generations and public-field limits | `TlsContext`/`TlsSession` are concrete shells with `res`; no `tok` field |
| Error model | systems design:311–398 defines named library codes, no global `lastError`, no hidden retries | TLS has its own code family and outcome types |
| States | systems design:399–447 defines open/closed/leak rules | Context/session follow the same lifecycle; no finalizer |
| Hosted-only threads | `obo/design/systems-threads-design.md`:30–33, 260–278 | TLS is hosted-only; one context per process/worker; no cross-worker shell |
| C boundary | `obo/design/native-c-interop-design.md` §6, §10, §14; `obo/experimental/cinterop/README.md` | Scalar `i64` args and `i32`/`i64` results; no pointers, structs by value, callbacks into obo, TLS or IFUNC guarantee |
| Registry pattern | `obo/experimental/cinterop/toolkit/cres.h`; `obo/experimental/db/sqlite/sqlite_adapter.c` | Tokens with kind/generation; explicit close; buffers by length. The TLS adapter should use the systems platform registry, not a second public `tok` model |
| Declarations-only precedent | `obo/experimental/db/sqlite/sqlite_decls.h`, `obo/experimental/db/README.md` | Declarations-only is acceptable for a stable scalar library ABI; it is not sufficient for OpenSSL macro policy |

### 2.2 Installed OpenSSL

The design was checked on the actual host:

```text
$ openssl version -a
OpenSSL 3.0.13 30 Jan 2024 (Library: OpenSSL 3.0.13 30 Jan 2024)
built on: Wed Sep 16 18:47:02 2026 UTC
platform: debian-amd64
options:  bn(64,64)
compiler: gcc ... -DOPENSSL_TLS_SECURITY_LEVEL=2 ...
OPENSSLDIR: "/usr/lib/ssl"
MODULESDIR: "/usr/lib/x86_64-linux-gnu/ossl-modules"
```

| Artifact | Observed value |
|---|---|
| `libssl.so.3` | `/usr/lib/x86_64-linux-gnu/libssl.so.3`, 696,512 bytes, SONAME `libssl.so.3`, NEEDED `libcrypto.so.3` and `libc.so.6` |
| `libcrypto.so.3` | `/usr/lib/x86_64-linux-gnu/libcrypto.so.3`, 5,309,400 bytes, SONAME `libcrypto.so.3` |
| `libssl.so.3` SHA-256 | `a40e7b67489bbd257e432c700ff8f6a1ad796425b5eccecdc2930127fdd86624` |
| `libcrypto.so.3` SHA-256 | `6866ed711dab8927e1bf82bf1cd30e828bdfc34f3cc1e83b1c9eb01ce6c37d9a` |
| `libssl.so.3` build ID | `e33015d57b8e510a25d82399804f4bdd8d18453e` |
| `libcrypto.so.3` build ID | `2eb7babe130d27bd183d57702b02626012169094` |
| Package | `libssl3t64 3.0.13-0ubuntu3.16 amd64`; `openssl 3.0.13-0ubuntu3.16` |
| CLI | `openssl` present at 3.0.13 |
| Development headers | **absent** at `/usr/include/openssl` |
| `libssl-dev` | available as `3.0.13-0ubuntu3.16`; fetched privately with `apt-get download` and extracted without installing |
| Header evidence | `opensslv.h`: `OPENSSL_VERSION_STR "3.0.13"`, `OPENSSL_VERSION_NUMBER 0x300000d0`; exact header hashes below |
| QUIC | targeted `grep -E 'SSL_QUIC|OSSL_QUIC|QUIC_'` and `nm -D libssl.so.3 | grep -E 'SSL_QUIC|OSSL_QUIC|QUIC_'` found no QUIC API. A bare `grep -i quic` is not used: it matches `..._QUICK` identifiers. The conclusion (no QUIC API in 3.0.13) is unchanged |
| IFUNC | this build's `libssl.so.3` and `libcrypto.so.3` expose no dynamic `IFUNC` symbols; `libssl.so.3` carries IBT/SHSTK GNU property notes. This is an observation about this build, not a general OpenSSL guarantee |
| Symbol versions | `OPENSSL_3.0.0` and `OPENSSL_3.0.3` version nodes exist; the core SSL functions checked for this design resolve to `OPENSSL_3.0.0`; the linker records the exact node for every referenced symbol |
| Security level | compiled default `OPENSSL_TLS_SECURITY_LEVEL=2`; the adapter also sets level 2 explicitly |

Exact extracted-header hashes (SHA-256), for the provenance record only:

```text
ssl.h             710a4527f2d6871dcdda2bb9e0f157b98bd8bde88f09664de6f89950a2a8606d
opensslv.h        724c362124239abc4b77decaf5d5f70af61476ea0fa84b1cb7a97c15957c1415
x509v3.h          045ccc247078cdce3285e4a932fbda6bc4d62e31a67c9940d8bfafa1a4015914
x509.h            6f6c2476067bf3f2320ee59fa051ea9836acba4a82fd0f034b214ce5b2f5c995
x509_vfy.h        8f4b78478d697614edad17c08ca34317ccfefd594ec053d444e842aaecebb14d
err.h             3cc1e1dbda3781fec4f515b1d61e31c39c6e76b802b3150e7c977b0b0a213608
bio.h             9dde507c18007338ee90517748b2b468cf416b4738f56a68baab0b4bb362cbc0
opensslconf.h     890184233890bacd52fd420fef07befad411b9a318b97efbf36f46673d3e7841
configuration.h   eee44bb7885f6e6b9a2a139100a7751c2564f1b83090ed237f178c1470737046
```

The headers are not installed, so a normal `#include <openssl/ssl.h>` fails on
the probe host. `libssl-dev` can be fetched without root and extracted into a
private include root. That is sufficient for a design-time probe and for a
hermetic CI sysroot. It is **not** sufficient for a supported build unless the
build manifest records the header package and the cinterop signature check can
see it (see §3.2).

### 2.3 Design-time probe (not committed code)

A private C probe against the extracted headers and the installed libraries
established the intended mechanics:

| Probe | Result |
|---|---|
| `OPENSSL_init_ssl(OPENSSL_INIT_NO_LOAD_CONFIG, NULL)` | succeeds; `OpenSSL_version_num()` = `0x300000d0` |
| TLS 1.3 handshake with two memory BIO pairs, self-signed `localhost` certificate | succeeds; `TLS_AES_256_GCM_SHA384`; ALPN selects `h2` |
| TLS 1.2 with ALPN server list `h2` and client list `http/1.1`, server policy fatal | handshake fails with OpenSSL reason `no application protocol` (`0x0A0000EB`); no fallback |
| Server `Require` with a client that sends **no ALPN**, using only the ALPN select callback | handshake **succeeds**; the select callback is not called (`alpn_calls=0`). This is why the design adds a C ClientHello-presence callback |
| Same no-ALPN client with `SSL_CTX_set_client_hello_cb` checking `SSL_client_hello_get0_ext(...ALPN...)` | callback is invoked, reports the extension absent, and aborts with `SSL_AD_MISSING_EXTENSION`; the handshake fails |
| Required client cert, client sends none | `SSL_ERROR_SSL`, verify result `0`, reason `peer did not return a certificate`; maps to `NoPeerCertificate` |
| Client `SSL_set1_host("localhost")` with the test CA | verify result `0` |
| Client `SSL_set1_host("wrong.example")` | verify result `62` (`X509_V_ERR_HOSTNAME_MISMATCH`); `SSL_get_error` = `SSL_ERROR_SSL` |
| `SSL_shutdown` on both peers, then drain | first call returns `0` on each; second returns `1`; `SSL_SENT_SHUTDOWN`/`SSL_RECEIVED_SHUTDOWN` become set |
| Application data through memory BIOs | 21 plaintext bytes written, 21 read, no loss; `ERR_get_error()` remains `0` |

The private/review probes did not test the obo cinterop hosted startup,
certificate expiry/revocation, fragmentation at every byte boundary, or the
full test matrix in §10. Those remain implementation gates. The no-ALPN and
required-client-certificate probes are recorded because they changed the
design (ClientHello callback; `NoPeerCertificate` mapping).

## 3. Library and build decision

### 3.1 OpenSSL versus alternatives

| Option | Verdict | Reason |
|---|---|---|
| **OpenSSL 3.0 system library** | **Recommended** | Installed, TLS 1.2/1.3, ALPN, SNI, client certificates, memory BIOs, stable SONAME and symbol versioning, Apache-2.0, system security updates |
| BoringSSL | Reject for v1 | No system package or stable ABI; API churn; vendoring a second crypto stack into the process; no package provenance; build and audit cost |
| GnuTLS | Reject for v1 | Could work, but no advantage on this host; different callback/session model; another large dependency; libpq already brings OpenSSL into many deployments |
| Static OpenSSL archive | Reject | Embeds a second crypto stack, bypasses system security updates, bloats the fixed image, complicates symbol/version provenance |
| Hand-written TLS | Reject | Protocol and side-channel risk; not a library exercise |
| `dlopen`/`dlsym` | Reject | No link-time prototype/version check; failure moves to run time; hides provenance |
| mbedTLS/wolfSSL/Rustls | Reject for v1 | No installed system library, extra toolchain/ABI, no demonstrated need |

Use the **system OpenSSL 3.0** already present. Do not bundle a second OpenSSL
inside an obo process: symbol interposition, provider state, error queues and
atexit behavior become ambiguous, especially when libpq or another dependency
also uses OpenSSL.

### 3.2 Headers required versus declarations-only

**Recommendation: require OpenSSL development headers for the TLS adapter
build. Reject declarations-only for OpenSSL.**

The SQLite/libpq declarations-only path works because those adapters call a
small set of exported functions with stable scalar prototypes, and the adapter
header itself is the reviewed ABI contract. OpenSSL is different:

- Much of the policy API is **function-like macros**, not exported symbols.
  In the extracted 3.0.13 `ssl.h`:
  - `SSL_CTX_set_min_proto_version` is a macro over
    `SSL_CTX_ctrl(ctx, SSL_CTRL_SET_MIN_PROTO_VERSION, version, NULL)`
    (ssl.h:1535–1538); the control number is `123` (ssl.h:1371–1372).
  - `SSL_CTX_set_max_proto_version` uses control `124`.
  - `SSL_CTX_set_session_cache_mode` is a macro over
    `SSL_CTX_ctrl(ctx, SSL_CTRL_SET_SESS_CACHE_MODE, m, NULL)`
    (ssl.h:2185–2186; control `44`).
  - `SSL_CTX_set_read_ahead` is a macro over control `41`
    (ssl.h:2194–2195).
  - `SSL_CTX_set1_curves_list` is an alias of `SSL_CTX_set1_groups_list`
    (ssl.h:1574), which itself is a macro over control `92`
    (ssl.h:1490–1491, 1347).
- The linker cannot catch a wrong `SSL_CTRL_*` number, a wrong `SSL_OP_*`
  bit, a wrong `SSL_VERIFY_*` value or a wrong `SSL_MODE_*` bit. A
  declarations-only adapter would hand-copy those numbers; a single stale
  value can silently change protocol policy. That is unacceptable for a
  security boundary.
- `STACK_OF`, `X509_NAME`, `GENERAL_NAMES`, `ASN1_TIME` and provider APIs are
  macro/opaque-type heavy. Reimplementing the subset is a second, unreviewed
  ABI description.
- The header package is available and cheap to pin. The exact package and
  header hashes are recorded in §2.2 and in the build manifest.

A declarations-only **adapter** header is still correct for the functions the
TLS adapter exports: `tls_*.h` declares only `long tls_x(long, ...)`. The
`.cbind` should use `headers` for `openssl/ssl.h`, the OpenSSL headers it
includes (`x509v3.h`, `x509_vfy.h`, `bio.h`, `err.h`, ...) and the adapter
header, with `signature_check: header`. The OpenSSL headers are trusted build
input, not vendored source.

**cinterop prerequisite found during this design:** `generate_sigcheck`
(`obo/experimental/cinterop/cinterop.py`:206–220) includes the header names
from the spec, but the signature-check compile at line 790 is:

```python
run([cc, '-c', '-fno-pie', '-Werror', '-I', spec_dir, sig_path, '-o', sig_o])
```

It does **not** receive `cflags`. If the headers live in a private sysroot
(as they must on this host), `#include "openssl/ssl.h"` may be found via
`spec_dir`, but that header's `<openssl/...>` includes will not see the
private include root. P2T-a must add an `include_dirs` field (or apply the
compile `cflags`) to the signature-check compile and record it in the
manifest. Without that small toolkit change, header mode cannot be used with a
private `libssl-dev` extraction.

Second build prerequisite: the cinterop link line
(`cinterop.py`:821–825) does not pass `-Wl,-z,relro,-z,now`. TLS should use
full RELRO and immediate binding so a missing/incompatible OpenSSL symbol
fails at process start rather than during a request. Add a validated
`link_flags` field (preferred) or an explicit TLS cbind link flag after
toolkit review.

### 3.3 Pin, provenance and runtime checks

- Link **directly** against the SONAME files:
  `libs: ["-l:libssl.so.3", "-l:libcrypto.so.3"]`.
  Do not use `-lssl`/`-lcrypto` (the development symlink can resolve to a
  different library) and do not link `libssl.a`.
- Record in the build manifest or a TLS provenance sidecar:
  - exact package versions (`libssl3t64`, `openssl`, `libssl-dev`);
  - absolute resolved paths and SHA-256 of `libssl.so.3` and
    `libcrypto.so.3` (cinterop already records `resolved_libraries` with
    path/hash at `cinterop.py`:537–563);
  - ELF build IDs;
  - `OPENSSL_VERSION_STR`, `OPENSSL_VERSION_NUMBER`,
    `OPENSSL_version_num()` at build and first run;
  - symbol version node used (`OPENSSL_3.0.0`);
  - header package version and hashes of at least `ssl.h`, `opensslv.h`,
    `x509v3.h`, `x509_vfy.h`, `err.h`, `bio.h`, `x509.h`, `provider.h`,
    `opensslconf.h`, `configuration.h`;
  - compiler/toolchain string and cinterop manifest hash.
- Use `-Wl,-z,relro,-z,now` and non-executable stack. The existing cinterop
  link already sets `-Wl,-z,noexecstack`; add the RELRO/NOW flags.
- Runtime check before the first context:
  - `OPENSSL_version_num()` must have major version `3` and be at least
    `0x30000000`. For a deployment pinned to the probe host, optionally
    require exactly `0x300000d0`; the default should accept a compatible
    `3.0.x` patch.
  - `OpenSSL_version(OPENSSL_VERSION)` is copied into diagnostics only.
  - The loader's symbol-version check is the real ABI check. Do not hash the
    loaded library at runtime; the manifest hash is provenance, and the
    dynamic loader may resolve a different file.
- Do **not** claim that the manifest hash is a signature or an authorization.
  A same-signature library substitution is trusted input. C and OpenSSL are
  trusted; the manifest catches accidents and stale artifacts.
- Do not rely on `LD_PRELOAD`, `LD_LIBRARY_PATH` or `OPENSSL_CONF` in
  production. The adapter calls
  `OPENSSL_init_ssl(OPENSSL_INIT_NO_LOAD_CONFIG, NULL)` so the process does not
  read `OPENSSL_CONF`. If another library in the same process already
  initialized OpenSSL with a config, that decision cannot be undone; document
  the process-wide interaction. The TLS module should initialize before any
  optional database adapter in a process that uses both.

### 3.4 What the cinterop trust boundary does and does not guarantee

Guarantees, given the existing toolkit:

- The compiler emits a typed native-call sidecar with arity, `i64` argument
  types, result width and conservative `freesall,writeshandles` effects.
- The tool checks every call against the `.cbind` before link; wrong arity,
  missing/extra arguments, unknown symbols and duplicate offsets fail.
- A header-mode signature check compiles a C function-pointer assignment for
  every declared function, so the adapter prototype and the installed header
  must agree at compile time.
- The shim export set is checked; a missing `obo_native_*` definition is a
  link failure.
- Input and final manifests hash the spec, wrapper, shim, sidecar, native
  object, headers/declarations, host/start/runtime sources, toolchain and
  resolved shared libraries. Missing files fail.
- The final ELF is checked for `ET_EXEC`, expected fixed addresses, segment
  bounds/overlap, no W+X segment and non-executable `GNU_STACK`.
- Ordinary obo never receives a raw C pointer; the scalar seam carries only
  `long` tokens and buffer indices.

It does **not** guarantee:

- that the C adapter or OpenSSL is memory-safe, correct or free of side
  channels;
- that the security policy in this note is implemented correctly merely
  because it compiles;
- that the runtime library is the one hashed at build time;
- that the OpenSSL headers and the runtime library have the same patch level;
- that IFUNC, TLS, constructors, `atexit`, `pthread_once` or provider loading
  work in the hosted obo image;
- that a same-signature native object or library substitution is safe;
- that private keys or plaintext are zeroized;
- that the obo layer can defend against malicious trusted C.

The TLS adapter must be reviewed as trusted C plus a trusted cryptographic
library, not as a sandbox.

### 3.5 IFUNC/ABI and hosted caveats

- This OpenSSL build has no dynamic `IFUNC` symbols in `libssl.so.3` or
  `libcrypto.so.3`; `libssl.so.3` does carry IBT/SHSTK property notes. Do not
  generalize that to every OpenSSL build. If a future build uses IFUNC, the
  loader resolves it before `main`; the cinterop non-PIE hosted image must be
  re-qualified.
- The cinterop note explicitly does not claim TLS or IFUNC support. OpenSSL's
  own error queue is thread-local; the hosted process has glibc TLS, but the
  adapter must not use `__thread` and must not claim general thread support.
- OpenSSL may use `pthread_once`, `getrandom`, `clock_gettime`, `mmap`,
  `atexit` and constructors. The cinterop hosted subset tests normal CRT
  constructors/atexit/stdio; it does **not** claim arbitrary-library
  constructors, OpenSSL TLS or IFUNC. P2T-a must build and run a C fixture
  **through the cinterop hosted startup**, not a normal `gcc` main, that:
  1. calls `OPENSSL_init_ssl(OPENSSL_INIT_NO_LOAD_CONFIG, NULL)`;
  2. explicitly loads the default provider and calls `RAND_bytes`;
  3. creates a server and client context with memory BIOs;
  4. performs a self-signed TLS 1.3 handshake and an application-data
     roundtrip;
  5. exercises `pthread_once`, `clock_gettime`/`mmap` and the thread-local
     error queue;
  6. asserts `atexit`/`OPENSSL_cleanup` ordering and a post-cleanup
     no-crash check;
  7. returns scalar status through the cinterop seam;
  8. runs under ASAN/UBSAN in the separate C harness.
  Until this passes, the TLS adapter is not accepted, and no HTTPS claim is
  made.
- The cinterop fixed image is non-PIE `ET_EXEC` with fixed addresses. OpenSSL
  is a normal shared library; its relocations and constructors are handled by
  the host loader. Do not attempt to link a static OpenSSL into the fixed
  image.

### 3.6 Reproducible build story

- Build inputs are source, adapter C, `.cbind`, OpenSSL header package and the
  system linker inputs. The build must run in a clean private root with the
  extracted `libssl-dev` headers and no network.
- Use the exact commands and `cflags` recorded in the manifest. For the probe
  host the include roots are:
  `.../libssl-dev/usr/include` and
  `.../libssl-dev/usr/include/x86_64-linux-gnu`.
- Link `-l:libssl.so.3 -l:libcrypto.so.3`, `-Wl,-z,relro,-z,now`,
  `-Wl,-z,noexecstack`, `--no-as-needed`, `-lc`.
- Record the compiler, linker, libc, package versions and resolved library
  hashes. Use `-ffile-prefix-map` for adapter objects so temporary build paths
  do not appear in diagnostics, and a fixed `SOURCE_DATE_EPOCH` for generated
  files.
- Bit-for-bit reproducibility of the final ELF is **not** claimed until the
  cinterop link records and pins the linker build ID policy and path maps.
  Provenance hashes and the final-ELF checks are the gate for this phase.
- Do not copy OpenSSL headers into the repository. `libssl-dev` is a build
  dependency; the manifest records its package version and hashes.

## 4. Module boundary and ownership

### 4.1 Dependency DAG

```text
systls -> sysplatform, sysstream, sysclock, sysreactor
```

`systls` does **not** import `sysnet`, `sysfile`, `sysbuffer`, `syswaitio`,
HTTP/1.1 or HTTP/2. TLS uses C-owned staging buffers, not `sysbuffer`; it has no
public cancellation helper in P2T-b. An
application obtains a connected `sysstream::Stream` from `sysnet` (or a test
fixture), then hands it to `systls::accept`. Certificate and key bytes are read
by the application with `sysfile` (bounded reads) or supplied from a test
fixture; the TLS module does not read paths.

Unique module name: `module systls`, source `obo/lib/sys/systls.obx` after
approval. Adapter C, `.cbind`, fake transport and tests live under
`obo/experimental/systems/` and `obo/tests/systems/`, following the core
layout at systems design §12.1.

### 4.2 Secrets and stable interfaces

| Secret | Hidden by `systls` | Surviving interface | Change kept local |
|---|---|---|---|
| OpenSSL object graph | `SSL_CTX*`, `SSL*`, `BIO*`, `X509*`, `EVP_PKEY*`, `X509_STORE*` | `TlsContext`, `TlsSession`, copied `PeerIdentity` | memory BIO versus future custom BIO; OpenSSL 3.x internals |
| Cipher/version policy | macro names and control numbers | `ContextOptions`, ALPN/version enums | OpenSSL defaults, security level, provider |
| Certificate parsing | PEM/DER, chain order, X509 names/SAN/time | bounded PEM bytes, copied identity | parser changes, SAN edge cases |
| Error queue | lib/reason/alert codes, queue lifetime | `Failure`, `text`, `verifyText` | OpenSSL error text and reason codes |
| Transport ownership | `Stream` shell and byte movement | non-optional `TlsSession.transport` (target private) | buffer sizes, pump order |
| Callback policy | C function pointers and C-side copied lists | ALPN/SNI/verify enums | multi-cert selection, ticket keys |
| Counters | C atomics/plain counters in v1 | `Counts`, `contextCounts` | future per-worker context |

### 4.3 Kinds, identities and generations

Add two library kinds at implementation time:

```obo
pub const KIND_TLS_CONTEXT = 23
pub const KIND_TLS_SESSION = 24
```

The core note reserves 1–22 (including the thread-note reservations). 23/24 are
the next free values; if another adapter claims them first, assign new values
at implementation. These are library constants, not kernel ABI; changing them
is a library ABI change.

Every public entry point that takes a `TlsContext` or `TlsSession`:

1. validates the public fields and ranges of its arguments;
2. calls `platform::requireKind(&ctx.res, KIND_TLS_CONTEXT)` or
   `platform::requireKind(&s.res, KIND_TLS_SESSION)`;
3. copies the table token/state into scalar locals, releases interior table
   borrows;
4. calls the C adapter with only scalar values and buffer tokens;
5. re-finds the entry after the call and updates state.

A user-constructed `Resource` has no entry and fails `InvalidResource`. A
same-kind replaced `res` can alias/steal another context or session (the
documented public-field limit); a cross-kind replacement is rejected. The TLS
module does not claim that public fields are opaque.

The C adapter does not use the DB adapters' `cres` table as a second public
resource model. It registers context/session state in the systems platform
registry so there is one identity, kind, generation and leak census. If the
platform registry is not yet available, P2T is blocked; a temporary `cres`
adapter would create a second ownership model and is rejected for the stable
API.

Generation exhaustion retires the slot; it never wraps. Close poisons the
generation before releasing the `SSL*`/`SSL_CTX*`, so a stale/double close
cannot free twice. A closed tombstone is kept only while its shell is alive;
dead open entries are leak records, not silently swept.

### 4.4 Ownership and lifecycle

- `TlsContext` owns one `SSL_CTX*` and its certificate/key/trust policy. It
  does not own sessions.
- `TlsSession` owns one `SSL*`, its two memory BIOs and exactly one
  `stream::Stream` in the non-optional `transport` field. The `Stream` is
  moved into the session at `accept`/`connect`; the session closes it on
  `close`. There is no `none` session state.
- **Public-field hard limitation, stated without a security claim.** In the
  current `0.11`/`0.12` public-field language shape, a caller can replace
  `transport` with another live `Stream`; TLS ciphertext/plaintext then goes
  to that stream and `close` closes it. `transport` has no kind/generation
  binding check equivalent to `res`. This is a trusted-obo hazard, not a
  capability boundary. The intended field-visibility shape is `pub res` on
  both shells and private `transport`, with `accept`/`connect` as the only
  constructors. Until
  that lands, this design does not claim protection against a caller who
  mutates the session record. Every step still revalidates `KIND_STREAM`;
  a closed/invalid transport returns `InvalidResource`/`Closed`.
- Record drop frees only the obo record. It never calls `SSL_free`,
  `SSL_CTX_free` or `stream::close`.
- `defer` is the normal cleanup. Register the context defer first and the
  session defer second, so LIFO closes sessions before the context:
  ```obo
  let ctx = try systls::createContext(opts)
  defer systls::closeContext(ctx)
  let session = try systls::accept(ctx, transport)
  defer systls::close(session)
  ```
- `closeContext` returns `Busy` while live sessions exist. Close sessions
  first. This keeps policy lifetime and counters unambiguous.
- `close` retires the session token even if the transport close reports an
  error. It returns the close error but does not leave a live token. Repeated
  close returns `ok(false)`.
- A session created by `accept` consumes the transport on every path. If
  session creation fails, the transport is closed exactly once and no session
  token remains.

### 4.5 Context state table

| State | `accept`/`connect` | load/set policy | `closeContext` |
|---|---|---|---|
| `Open/Unconfigured` (server: no cert+key; client: not ready) | `NotConfigured` | allowed | `ok(true)`, retire |
| `Open/Ready` | allowed; increments live sessions | `Busy` if live sessions; otherwise allowed | `ok(true)` if no sessions; `Busy` otherwise |
| `Poisoned` | `Configuration` | `Configuration` | `ok(true)`, retire |
| `Closing` | `Closed` | `Closed` | `ok(false)` |
| `Closed` | `Closed` | `Closed` | `ok(false)` |

A server context becomes `Ready` only after a certificate chain and a matching
private key are loaded. A client context becomes `Ready` after its verify
policy and trust store are established. A context loads its certificate and
key once; to change identity, create a new context. Policy setters are allowed
only before the first session; after that they return `Busy`. If a setter or
certificate/key load fails after partial native mutation, the context becomes
`Poisoned`: all further configuration and `accept`/`connect` return
`Configuration`; only `closeContext` is allowed. A failed trust-bundle
replacement is atomic and leaves the previous store intact, so it does not
poison the context.

### 4.6 Session state table

| State | `handshakeStep` | `readSome` | `writeSome` | `shutdownStep` | `close` |
|---|---|---|---|---|---|
| `New` | drives handshake | `NotReady` | `NotReady` | `NotReady` | `ok(true)` |
| `Handshaking` | drives | `NotReady` | `NotReady` | `NotReady` | `ok(true)` |
| `Established` | `Done` | data / `Eof` / want | wrote / want | drives | `ok(true)` |
| `SendClosed` (local close_notify sent) | `Done` | data / `Eof` / want | `Closed` | drives | `ok(true)` |
| `RecvClosed` (peer close_notify received) | `Done` | `Eof` | wrote / want | drives | `ok(true)` |
| `Failed` | `Closed` | `Closed` | `Closed` | `Closed` | `ok(true)` once |
| `Closed` | `Closed` | `Closed` | `Closed` | `Closed` | `ok(false)` |

`SendClosed` and `RecvClosed` are independent flags internally; the table
shows the common combinations. TLS 1.3 close_notify is unidirectional, so a
peer may half-close and continue reading. `readSome` after `RecvClosed`
returns `Eof`; `writeSome` may still succeed until local close_notify.
`flush` is valid in `Established`, `SendClosed` and `RecvClosed`; every
progress outcome carries `want` when output remains.

### 4.7 Leak counters

`Counts` and `ContextCounts` are defined once in the API block (§5). Counts are
per systems context (one process context in v1). `liveSessions` includes
handshaking and established sessions.

- `ciphertextPending` is `rbio pending + wbio pending + C staging bytes` over
  live sessions. C staging is included; a short transport write must not hide
  pending output from the counter or from `want`.
- `plaintextPending` is `SSL_pending` over live sessions. There is no obo-side
  plaintext queue; a future obo queue must be added to this counter.
- `contextBytes` is the adapter-owned charge for contexts and live sessions
  (BIO/staging/handshake/certificate buffers). `maxContextBytes` is the
  configured aggregate cap. OpenSSL's internal heap is **not** fully charged.

`platform::counts()` also reports `KIND_TLS_CONTEXT`/`KIND_TLS_SESSION`, so the
leak oracle has both views.

OpenSSL's internal heap is **not** fully charged to `platform::maxNativeBytes`.
The adapter enforces per-session BIO/certificate caps; ASAN/LSAN and the
counters cover leaks. Do not install a process-global
`CRYPTO_set_mem_functions` just to count TLS allocations: it would affect
libpq and every other OpenSSL user in the process. Record this limitation
honestly.

### 4.8 C adapter scalar surface

The C adapter exports only `long f(long, ...)` functions. No pointer, C
struct, union, array-by-value, variadic argument or function pointer appears in
the `.cbind`. Complex options and copied results use a versioned, bounded
packet in a platform buffer token; the obo wrapper builds and parses it with
checked offsets.

Representative entry points (names are implementation details, not a public
ABI):

| C entry | Arguments | Result |
|---|---|---|
| context create | options packet token | context hint or negative status |
| load chain/key/CA | context hint, PEM buffer token | 1/0 or negative status |
| set ALPN/version/cipher/group | context hint, packet token | 1/0 or negative status |
| session create | context hint | session hint or negative status |
| handshake step | session hint | `1` done, `0` want-read, `-1` want-write, negative fatal |
| feed ciphertext | session hint, buffer token, offset, count | bytes consumed or negative status |
| drain ciphertext | session hint, max | buffer token or 0/negative status |
| read plaintext | session hint, max | buffer token or status |
| write plaintext | session hint, buffer token, offset, count | bytes accepted or negative status |
| shutdown step | session hint | 1 done, 0 sent, negative want/fatal |
| info/peer | session hint, field selector | scalar or buffer token |
| close context/session | hint | 1/0 or negative status |
| diagnostics | hint | buffer token; obo copies then releases |

The obo wrapper maps the negative status to `Failure`/outcomes and copies all
buffer tokens before returning. The C adapter never writes an obo array, never
retains an obo pointer and never returns an OpenSSL pointer. The platform
registry owns the buffer tokens and the context/session hints; a forged hint
fails kind/generation validation before any `SSL*` dereference.

## 5. API in 0.11 obo

The block below is **proposed declarations**, not a compilable module. It uses
the existing style: bodyless `pub fn` headings are signature notation; each
becomes a definition in an implementation. No generics, destructors, async
keywords or borrowed returns are invented.

```obo
module systls
import "sysplatform.obx" as platform
import "sysstream.obx" as stream
import "sysclock.obx" as clock
import "sysreactor.obx" as reactor

pub const KIND_TLS_CONTEXT = 23
pub const KIND_TLS_SESSION = 24

pub type TlsContext = ref record { res: platform::Resource }
pub type TlsSession = ref record {
    res: platform::Resource
    transport: stream::Stream
}

pub type Role = closed { Server {} Client {} }
pub type Version = closed { Tls12 {} Tls13 {} }
pub type VerifyMode = closed { None {} Optional {} Required {} }
pub type AlpnPolicy = closed {
    Disabled {}
    Prefer { protocols: []string }
    Require { protocols: []string }
}
pub type ContextOptions = ref record {
    role: Role
    minVersion: Version
    maxVersion: Version
    verify: VerifyMode
    alpn: AlpnPolicy
    requireSni: bool
    verifyDepth: int
    maxSessions: int
    maxHandshakeBytes: int
    maxContextBytes: int
}
pub type ServerIdentity = closed {
    Dns { name: string }
    Ip { text: string }
}

pub type Want = closed { None {} Read {} Write {} ReadWrite {} }
pub type Step = closed { Done {} WantRead {} WantWrite {} }
pub type ReadOutcome = closed {
    Data { bytes: []byte; want: Want }
    Eof { want: Want }
    WantRead {}
    WantWrite {}
}
pub type WriteOutcome = closed {
    Wrote { count: int; want: Want }
    WantRead {}
    WantWrite {}
}
pub type FlushOutcome = closed { Done {} WantWrite {} }
pub type ShutdownOutcome = closed {
    Done {}
    Sent { want: Want }
    PeerClosed { want: Want }
    WantRead {}
    WantWrite {}
}
pub type Pending = ref record {
    plaintext: int
    ciphertext: int
}

pub type PeerVerification = closed {
    NotRequested {}
    Verified {}
    Failed { code: int }
}
pub type PeerIdentity = ref record {
    present: bool
    verification: PeerVerification
    subject: string
    issuer: string
    dnsNames: []string
    ipAddresses: []string
    notBefore: clock::Timestamp
    notAfter: clock::Timestamp
    sha256: []byte
}
pub type HandshakeInfo = ref record {
    version: string
    cipher: string
    alpn: ?string
    serverName: ?string
    peer: PeerIdentity
}

pub type Counts = ref record {
    liveContexts: int
    liveSessions: int
    handshakesStarted: int
    handshakesCompleted: int
    handshakeFailures: int
    verifyFailures: int
    alpnRejections: int
    truncations: int
    ciphertextPending: int
    plaintextPending: int
    contextBytes: int
    maxContextBytes: int
}
pub type ContextCounts = ref record {
    liveSessions: int
    maxSessions: int
    handshakesStarted: int
    handshakesCompleted: int
    handshakeFailures: int
    bytesCharged: int
    maxBytes: int
}

pub type Failure = closed {
    InvalidArgument {}
    Range {}
    InvalidResource {}
    Closed {}
    HandshakeFailed {}
    ProtocolAlert { description: int }
    ProtocolViolation {}
    VerifyFailed { verifyCode: int }
    NoPeerCertificate {}
    CertificateRejected {}
    KeyRejected {}
    Configuration {}
    AlpnRequired {}
    NoApplicationProtocol {}
    AlpnMismatch {}
    Truncated {}
    NotReady {}
    Transport { sysCode: int }
    ResourceLimit {}
    Busy {}
    NotConfigured {}
    Unsupported {}
    Internal {}
}

pub fn serverOptions(): ContextOptions
pub fn clientOptions(): ContextOptions
pub fn createContext(options: &ContextOptions): Result[TlsContext]

pub fn loadCertificateChain(ctx: &TlsContext, pem: &[]byte): Result[bool]
pub fn loadPrivateKey(ctx: &TlsContext, pem: &[]byte): Result[bool]
pub fn setTrustedCertificates(ctx: &TlsContext,
                              pem: &[]byte): Result[bool]
pub fn useSystemTrustStore(ctx: &TlsContext): Result[bool]
pub fn setAlpn(ctx: &TlsContext, policy: &AlpnPolicy): Result[bool]
pub fn setMinVersion(ctx: &TlsContext, version: &Version): Result[bool]
pub fn setMaxVersion(ctx: &TlsContext, version: &Version): Result[bool]
pub fn setCipherSuites(ctx: &TlsContext, suites: []string): Result[bool]
pub fn setCipherList(ctx: &TlsContext, ciphers: []string): Result[bool]
pub fn setGroups(ctx: &TlsContext, groups: []string): Result[bool]

pub fn accept(ctx: &TlsContext,
              transport: stream::Stream): Result[TlsSession]
pub fn connect(ctx: &TlsContext, transport: stream::Stream,
               identity: &ServerIdentity): Result[TlsSession]
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
pub fn alpn(s: &TlsSession): Result[?string]
pub fn watch(s: &TlsSession, r: &reactor::Reactor,
             readable: bool, writable: bool,
             tag: int): Result[reactor::Watch]
pub fn maxTransfer(s: &TlsSession): int
pub fn isSecure(s: &TlsSession): bool
pub fn close(s: &TlsSession): Result[bool]
pub fn closeContext(ctx: &TlsContext): Result[bool]
pub fn failure(code: int): Failure
pub fn text(code: int): string
pub fn verifyText(code: int): string
pub fn counts(): Result[Counts]
pub fn contextCounts(ctx: &TlsContext): Result[ContextCounts]
```

### 5.1 Types and validation

- `Role.Server` is the only role implemented in P2T-b. `Role.Client` is
  accepted by `createContext` but `connect` returns `Unsupported` until
  P2T-c. `accept` on a client context and `connect` on a server context are
  `Configuration`.
- `Version` has only `Tls12`/`Tls13`. `minVersion <= maxVersion`; anything
  else is `Configuration`. TLS 1.0/1.1/SSLv3 are unrepresentable.
- `VerifyMode`:
  - server: `None`, `Optional`, `Required`;
  - client: `None` or `Required`; `Optional` is `Configuration` for a client.
- `AlpnPolicy`:
  - `Disabled`: no ALPN extension;
  - `Prefer`: select the first server-preference protocol present in the
    client list; if none, no ALPN (NOACK);
  - `Require`: the ClientHello must contain the ALPN extension **and** the
    select callback must find an overlap. Absence is rejected by a C-only
    `client_hello_cb` with `SSL_AD_MISSING_EXTENSION`; no overlap is rejected
    by the ALPN select callback with alert 120. Both fail the handshake.
  Protocols: 1..16 entries, each 1..255 bytes, no NUL, no duplicates, total
  <=1024 bytes. Empty `Prefer`/`Require` is `InvalidArgument`.
- **Session tickets are disabled in v1 and are not an option.** There is no
  ticket configuration field; no ticket key, no resumption, no 0-RTT. A future
  shared-key design needs its own review.
- `requireSni`: server-only; requires a non-empty SNI, but does **not** select
  a certificate and does **not** authorize a name in v1. It is a presence
  policy. On a client context, `true` is `Configuration`. Default `false`.
- `verifyDepth`: 0..16, default 8.
- `maxSessions`: 1..4096, default 256. Exceeding it makes `accept`/`connect`
  return `ResourceLimit` after closing the transport; there is no hidden
  accept queue.
- `maxHandshakeBytes`: 16 KiB..8 MiB, default 1 MiB. It bounds ciphertext
  fed during the handshake; exceeding it fails the session with
  `ResourceLimit`.
- `maxContextBytes`: 1 MiB..1 GiB, default 64 MiB. It bounds the sum of
  adapter-owned context/session buffers (BIO pending, C staging, handshake
  accumulation, certificate/key/trust copies). Exceeding it fails the
  operation with `ResourceLimit`; OpenSSL's internal heap is outside this
  budget and is documented separately.
- `serverOptions()` defaults: server, TLS 1.2–1.3, verify `None`, ALPN
  `Prefer(["http/1.1"])`, SNI not required, depth 8, 256 sessions, 1 MiB
  handshake, 64 MiB context budget. `h2` is **not** advertised until the
  HTTP/2 layer exists; an application enabling it sets
  `Prefer(["h2","http/1.1"])` or `Require(["h2"])` explicitly.
- `clientOptions()` defaults: client, TLS 1.2–1.3, verify `Required`, ALPN
  `Prefer(["http/1.1"])`, depth 8, 64 sessions, 1 MiB handshake, 64 MiB
  context budget.
- `TlsSession.transport` is non-optional; there is no nil/none session
  transport. In v0.11 public-field source a caller can replace it; the target
  field-visibility shape is `pub res` plus private `transport` and
  construction only through `accept`/`connect` (see §4.4).

### 5.2 Context functions

`createContext` validates every `ContextOptions` field, then creates the
`SSL_CTX`, applies policy, registers a `KIND_TLS_CONTEXT` entry and returns the
shell. It calls `OPENSSL_init_ssl(OPENSSL_INIT_NO_LOAD_CONFIG, NULL)` once
(process-global, idempotent) and checks `OPENSSL_version_num() >= 0x30000000`,
major 3. If initialization or version check fails, it returns
`Configuration`/`Unsupported`; no context token is created. Fixed context
overhead and later certificate/key/trust copies are charged to
`maxContextBytes`; exceeding it returns `ResourceLimit` before any session.
`Require` installs both the C ClientHello presence callback and the ALPN
select callback;
`Prefer` installs only the select callback; `Disabled` installs neither.

`loadCertificateChain`:

- input is bounded PEM bytes (<=1 MiB, <=16 certificates, each DER
  <=256 KiB); leaf first, then intermediates; root optional;
- parses with `PEM_read_bio_X509`; rejects trailing garbage that is not a
  certificate (bounded scan), and fails if the first object is not a
  certificate;
- installs the leaf with `SSL_CTX_use_certificate` (which increments the
  `X509` reference count) and each intermediate with
  `SSL_CTX_add_extra_chain_cert` (which takes ownership); the adapter frees
  its parsed leaf with `X509_free` after install and does not free the
  intermediates it transferred;
- supports RSA, ECDSA P-256/P-384 and Ed25519 keys through OpenSSL; rejects
  weak keys through security level 2 and the configured cipher/group policy;
- does not read a path, does not follow a symlink, does not block on a file.
  The application reads bytes with bounded `sysfile` operations.
- Parses the whole chain before installing anything. An install failure after
  partial native mutation poisons the context; the caller creates a new one.
  Every parsed `X509` is freed on every failure path.

`loadPrivateKey`:

- input is bounded PEM (<=64 KiB), unencrypted PKCS#8/PKCS#1/SEC1;
- parses with `PEM_read_bio_PrivateKey` and a C-only no-password callback, so
  an encrypted key fails cleanly instead of prompting on stdin;
- parses the key completely before installing; `SSL_CTX_use_PrivateKey`
  increments the `EVP_PKEY` reference count, so the adapter frees its parsed
  key with `EVP_PKEY_free` after install. `SSL_CTX_check_private_key` must
  pass; a mismatch is `KeyRejected` and poisons the context.
- zeroizes the C-side copy with `OPENSSL_cleanse` and frees it. The caller's
  obo array is not zeroized by the adapter; callers should load keys from a
  file with restrictive permissions and drop the array promptly. Encrypted
  keys need a separate secret-buffer design (see §13).
- Load the chain before the key; `loadPrivateKey` checks against the installed
  leaf. Loading a key first is `NotConfigured`. A context loads its
  certificate and key once; a second load is `Configuration` (or `Busy` after
  a session). To change identity, create a new context. A server `accept` with
  no certificate or key is `NotConfigured`.

`setTrustedCertificates` **replaces** the context's trust store atomically.
It parses a bounded PEM bundle (<=4 MiB, <=256 certificates) into a temporary
list, creates a new `X509_STORE`, adds every certificate (a duplicate
`X509_R_CERT_ALREADY_IN_HASH_TABLE` is accepted idempotently; the store takes
its own reference), frees the parsed certificates, and only then swaps the
store into the `SSL_CTX` with `SSL_CTX_set_cert_store` (which takes ownership
of the new store and frees the old one). On any parse/add failure the
temporary store and parsed certificates are freed and the previous store
remains intact. It does not use `SSL_CTX_load_verify_locations` with an
application path; paths are the caller's responsibility. One call configures
the whole bundle; there is no partial-add API.

`useSystemTrustStore` is client-only. It builds a new `X509_STORE`, calls
`X509_STORE_set_default_paths` on that store, and swaps it into the `SSL_CTX`
atomically; a failure frees the new store and leaves the previous one intact.
OpenSSL's default paths honor `SSL_CERT_FILE` and `SSL_CERT_DIR`; the adapter
documents that client trust-store mode inherits those environment variables
unless the application clears them. For deterministic trust, use
`setTrustedCertificates`. Server client-certificate verification must use an
explicit bundle.

`setAlpn` replaces the context's copied ALPN policy, validates it exactly as
`createContext` does, and updates or clears the ALPN select and ClientHello
presence callbacks. `setMinVersion`/`setMaxVersion` validate
`minVersion <= maxVersion` and call `SSL_CTX_set_min_proto_version`/
`SSL_CTX_set_max_proto_version`.

All policy setters validate and stage their inputs before mutating OpenSSL.
If a setter fails after a native mutation, the context becomes `Poisoned`;
the caller must destroy it, not continue with a mixed policy. A trust-store
replacement is atomic and does not poison on failure. All policy setters are
`Busy` after the first session.

`setCipherSuites` applies **TLS 1.3** cipher suites; `setCipherList` applies
**TLS 1.2** cipher suites. They are separate on purpose: setting one does not
set the other. `setGroups` applies both protocol versions. Each validates and
joins its tokens (1..64 printable ASCII, no `:`, total <=1024) and calls
`SSL_CTX_set_ciphersuites`/`SSL_CTX_set_cipher_list`/
`SSL_CTX_set1_groups_list`; a zero return is `Configuration` and poisons the
context. The defaults are in §8.

`closeContext` returns `Busy` if any live session belongs to the context.
Otherwise it poisons the generation, calls `SSL_CTX_free`, retires the entry
and returns `true`. A second close is `false`.

### 5.3 Session creation

`accept(ctx, transport)`:

- requires `Role.Server`, a `Ready` context (not `Poisoned`), and a live
  `Stream`; `Configuration`/`InvalidResource` otherwise;
- checks `maxSessions` and the `maxContextBytes` admission charge; on overflow
  closes `transport` and returns `ResourceLimit`;
- calls `SSL_new`, creates two memory BIOs, `SSL_set_bio` (which transfers BIO
  ownership), sets accept state and registers a `KIND_TLS_SESSION` entry;
- moves the non-optional `transport` into the session record. On any failure,
  frees the SSL/BIO state exactly once, closes `transport` exactly once, and
  returns the error.
- It does not perform the handshake and does not read/write the transport.
  The session record is constructed only by this function (or `connect`); in
  v0.11 a caller can still `new` the public record, which yields an
  unregistered `res` and fails `InvalidResource`.

`connect(ctx, transport, identity)` is the symmetric client path for P2T-c:

- requires `Role.Client`;
- `ServerIdentity.Dns` sets SNI and `SSL_set1_host`; `ServerIdentity.Ip` sets
  the IP verification parameter with `X509_VERIFY_PARAM_set1_ip_asc`;
- `SSL_set_hostflags` includes `X509_CHECK_FLAG_NO_PARTIAL_WILDCARDS`;
- it does not disable verification unless the context explicitly uses
  `VerifyMode.None`;
- until implemented, it returns `Unsupported` and closes the transport.

### 5.4 Handshake

`handshakeStep` advances at most one bounded pump step:

- if output (C staging or write BIO) is pending, it flushes one bounded chunk
  first; if output remains, it returns `WantWrite` and does not call
  `SSL_do_handshake`;
- if the session is already `Established`/`SendClosed`/`RecvClosed` and output
  is empty, it returns `Done`;
- if closed/failed, it returns `Closed`;
- otherwise it calls `SSL_do_handshake` **once**. On success it sets
  `Established`; it returns `Done` only when the write BIO and C staging are
  empty. If new output is pending, it returns `WantWrite` without a second
  write.
- After the `SSL_do_handshake` call, inspect output **first**. If the write
  BIO or C staging is non-empty, return `WantWrite` even when OpenSSL returned
  `SSL_ERROR_WANT_READ`; a bare `WantRead` with pending handshake output is a
  defect. If output is empty, `WANT_READ` returns `WantRead` (the wrapper may
  perform one transport read and feed the C rbio, without a second SSL call);
  `WANT_WRITE` returns `WantWrite`.
- It never waits for readiness, never sleeps, never loops on `WouldBlock`, and
  never restarts a deadline. The caller watches write when the step says
  `WantWrite`, including the “OpenSSL wanted read but produced output” case.

`handshakeUntil` is **not part of P2T-b**. It is deferred until `sysreactor`/
`syswaitio` has a reviewed cancellation-wake contract (or a caller-provided
watch source that does not consume unrelated events). P2T-b uses the step API
with the application's existing reactor: call `handshakeStep`, watch the
transport according to `want(s)`, call `reactor::wait` with one absolute
deadline, and repeat. There is no `forever()` example and no helper that
pretends to wake a blocked `wait` from outside.

`info` is valid after the handshake (`Established`, `SendClosed` or
`RecvClosed`); before that it is `NotReady`. It copies:
- negotiated version (`SSL_get_version`, bounded);
- cipher name (`SSL_CIPHER_get_name`, bounded);
- ALPN selection (`SSL_get0_alpn_selected`, bounded, nil if none);
- SNI (`SSL_get_servername`, bounded, nil if none);
- peer identity (see §5.6).

`watch` observes the underlying transport, not TLS completion. A readable
socket may still yield `WantRead` for a partial record; a writable socket may
have no ciphertext to flush. Use `want(s)` to set interest and always call the
step and inspect its outcome.

`maxTransfer(s)` returns 65,536. It is the same cap that `readSome` and
`writeSome` enforce; HTTP uses it to split larger buffers. It is a constant for
v1, not a negotiated TLS value.

### 5.5 Read, write and shutdown

`readSome(s, max)`:

- `max` must be 1..65536; otherwise `Range`;
- before handshake: `NotReady`; after close: `Closed`;
- if output (C staging or write BIO) is pending, it flushes one bounded chunk
  first. If output remains, it returns `WantWrite` and performs no read; this
  prevents a response/KeyUpdate from being stranded behind a read wait.
- if `SSL_pending(s) > 0`, it may return data without touching the transport;
  otherwise it may perform at most one transport read and feed the C rbio.
- it calls `SSL_read_ex` **once**. After the call, inspect output first:
  - data/Eof plus pending output -> `Data{bytes, want=Write}` /
    `Eof{want=Write}`;
  - no data/Eof but pending output -> `WantWrite`, even if OpenSSL returned
    `SSL_ERROR_WANT_READ`; a bare `WantRead` with pending output is a defect;
  - data/Eof with no output -> `Data{bytes, want=None}` / `Eof{want=None}`;
  - no data/Eof and no output -> `WantRead`/`WantWrite` according to OpenSSL.
- `Data{bytes,want}` is an owned array of 1..max bytes; `Eof` is a clean peer
  close_notify. A fatal `Result.err` is separate.
- a transport EOF without a received close_notify is `Truncated`, never
  `Eof`. This is a security rule, not a convenience;
- a zero-length plaintext record is not application data; OpenSSL handles it;
  the adapter does not expose it as `Data` with zero bytes.

`writeSome(s, bytes, offset, count)`:

- validates `0 <= offset <= len(bytes)`, `0 <= count`, and
  `offset + count <= len(bytes)` with checked arithmetic; `count` <=65536;
- `count == 0` returns `Wrote{0, want}` after validation and performs no
  flush. Use `flush(s)` when no new plaintext is available.
- before handshake: `NotReady`; after local close_notify/close: `Closed`;
- if output is already pending, it flushes at most one bounded chunk first and
  returns `WantWrite` if not fully drained; it does not accept new plaintext
  while output remains.
- otherwise it calls `SSL_write_ex` **once**, with partial-write mode enabled.
  After the call, inspect output first:
  - accepted `n > 0` plus pending output -> `Wrote{n, want=Write}`;
  - accepted `n > 0` with no output -> `Wrote{n, want=None}`;
  - no accepted plaintext and pending output -> `WantWrite`, even if OpenSSL
    returned `SSL_ERROR_WANT_READ`; a bare `WantRead` with pending output is a
    defect;
  - no accepted plaintext and no output -> `WantRead`/`WantWrite` according to
    OpenSSL.
- It does **not** flush newly produced ciphertext in the same step;
  `want=Write` tells the caller to call `flush`/`handshakeStep` next. It never
  replays bytes; the caller advances its own cursor by `n`.
- if a transport write fails while flushing prior output, the session is
  failed and the error is returned; do not report `Wrote{n}` as if the bytes
  reached the peer.

`flush(s)`:

- drains one bounded chunk of output per call: C staging first, then the write
  BIO into C staging. It never refills staging while it is non-empty.
- if staging is empty and wbio is non-empty, it refills staging from wbio
  **without a transport write**, then performs one transport write.
- if staging drains and wbio still has bytes, it returns `WantWrite` and does
  not write wbio in the same step.
- returns `Done` only when both C staging and the write BIO are empty;
  `WantWrite` if the transport accepted a short prefix, would block, or wbio
  remains after the write.
- `flush` performs at most one transport write and never calls
  `SSL_write_ex`/`SSL_read_ex`/`SSL_shutdown`; it cannot return `WantRead`.

`shutdownStep(s)` drives `SSL_shutdown`:

- if output is pending, it flushes one bounded chunk first; if output remains,
  returns `WantWrite`;
- calls `SSL_shutdown` **once**. After the call, inspect output first:
  - output pending -> `WantWrite` if no clean state is complete, or
    `Sent{want=Write}`/`PeerClosed{want=Write}` when the close_notify state is
    already known; never return a bare `WantRead` with pending output;
  - returning `1` with output empty: both directions are shut down, `Done`;
  - returning `0`: local close_notify is queued; return `Sent{want}` with
    `want=Write` if output remains, otherwise `None`;
  - `SSL_ERROR_ZERO_RETURN`: peer close_notify arrived before ours,
    `PeerClosed{want}`; calling again sends our close_notify and returns
    `Sent`/`Done`;
  - `WantRead` with output empty -> `WantRead`; `WantWrite` -> `WantWrite`;
  - any fatal error: fail the session.

`shutdownStep` is the only close_notify path exposed to HTTP; the HTTP
`Transport.shutdownStep` maps directly to it. `close` is abortive and is not a
clean-shutdown promise.

`close(s)`:

- uses the session's native `transportClosed` flag to ensure exactly one
  `stream::close`; the public `transport` field is left as a closed `Stream`
  shell and is freed with the session record;
- calls `SSL_free` (which frees the SSL and its BIOs);
- retires the session token even if the transport close fails; returns the
  close error if there was one, otherwise `true`;
- repeated close returns `ok(false)` and does not touch native state.

`close` is **not** a clean-shutdown promise. It may discard unsent plaintext
and may close without a close_notify exchange. Applications that need a clean
close call `shutdownStep` until `Done`/`PeerClosed` or a deadline, then close.

### 5.6 Peer identity

`peerIdentity` copies a bounded snapshot of the peer certificate, if any:

- `present`: a peer certificate was received;
- `verification`:
  - `NotRequested` when the context's verify mode is `None`;
  - `Verified` when chain and (in client mode) hostname verification passed;
  - `Failed{code}` with the `X509_V_ERR_*` code otherwise;
- `subject`, `issuer`: RFC 2253 strings from `X509_NAME_print_ex`, each
  <=1024 bytes, with control characters escaped;
- `dnsNames`: SAN DNS entries, <=64 entries, each <=255 bytes;
- `ipAddresses`: SAN IP entries, <=64 entries, canonical text;
- `notBefore`, `notAfter`: parsed `ASN1_TIME` converted to
  `clock::Timestamp`; a parse failure is `Internal` (the handshake already
  succeeded, so this is a library bug, not a peer error);
- `sha256`: 32-byte SHA-256 of the peer certificate DER.

No `X509*`, `STACK_OF`, `GENERAL_NAMES` or `ASN1_TIME` pointer is returned.
If there is no peer certificate, `present=false`, `verification` is
`NotRequested`, strings/arrays are empty and timestamps are zero. A server
with `VerifyMode.Required` never reaches `info` without a peer certificate;
the handshake fails.

`verifyText(code)` copies `X509_verify_cert_error_string(code)` into a bounded
diagnostic. It is for humans, not branching. Branch on
`PeerVerification.Failed.code` or `Failure.VerifyFailed.verifyCode`.

### 5.7 Representative server loop

This is proposed usage, not a compilable module:

```obo
fn driveHandshake(s: &systls::TlsSession, r: &sysreactor::Reactor,
                  until: &clock::Deadline): Result[bool] {
    var w = try systls::watch(s, r, true, false, 1)
    defer sysreactor::remove(w)
    while true {
        match systls::handshakeStep(s) {
            ok(step) {
                match step {
                    Done {} { return ok(true) }
                    WantRead {} {
                        try sysreactor::modify(w, true, false)
                        try sysreactor::wait(r, until, 1)
                    }
                    WantWrite {} {
                        try sysreactor::modify(w, false, true)
                        try sysreactor::wait(r, until, 1)
                    }
                }
            }
            err(message, code) { return err(message, code) }
        }
    }
    return ok(false)
}
```

A multi-connection server uses the same `handshakeStep`/`want`/`watch` calls
inside its existing event loop and application tags; it does not hide a
per-connection blocking helper. The deadline is absolute and caller-owned.
`handshakeUntil` is deferred (see §11.4), because the public reactor/waitio
API has no cancellation-wake registration for a new helper. A step that
OpenSSL reports as `WANT_READ` but that produced handshake output returns
`WantWrite`, so the loop watches write first and flushes before reading.

## 6. Nonblocking state machine

### 6.1 Memory BIOs, not `SSL_set_fd`

`SSL_set_fd` (or `BIO_new_socket`) would make OpenSSL perform `read`/`write`
on a socket directly. That is rejected because:

- it bypasses `sysstream` partial-transfer, half-close, close and CLOEXEC
  rules;
- it gives OpenSSL an fd and a close obligation that the systems registry
  cannot see;
- it makes readiness/deadline ownership ambiguous and can block inside
  `SSL_do_handshake`/`SSL_read`;
- it cannot be driven by a reactor step API without hidden blocking or
  `SSL_MODE_AUTO_RETRY`.

Instead each session owns two memory BIOs:

```text
transport (sysstream::Stream)
        |
        | obo reads ciphertext (bounded chunk)
        v
   C feed buffer  --->  rbio (BIO_s_mem)  --->  SSL_do_handshake / SSL_read_ex
                                                         |
                                                         v
   obo writes ciphertext  <---  C drain buffer  <---  wbio (BIO_s_mem)
        |
        v
transport (sysstream::Stream)
```

The C adapter never reads or writes the transport. The obo wrapper owns the
`Stream` and performs one bounded `readSome`/`writeSome` per pump step. The
C adapter owns `SSL`, `rbio`, `wbio`, the per-session C staging buffers and
the copied policy. No pointer to obo memory is retained by C.

There is no second protocol buffer in obo: OpenSSL's memory BIO is the record
buffer, and obo keeps only one bounded transfer chunk per direction. The copy
across the scalar C ABI is unavoidable and bounded by 64 KiB; obo does not
reassemble TLS records, retain a second ciphertext queue, or grow a buffer with
the connection. A short transport write leaves the remainder in the C staging
buffer, not in an unbounded obo array.

`SSL_set_bio(ssl, rbio, wbio)` transfers BIO ownership; `SSL_free` frees both.
On a path before `SSL_set_bio`, both BIOs are freed explicitly. The rbio is
configured with `BIO_set_mem_eof_return(rbio, -1)` so an empty read BIO yields
retry/WANT_READ rather than a false EOF. The wbio is read with `BIO_read`.

### 6.2 Pump invariants

Every public step obeys these invariants:

1. **Bounded work.** At most one `sysstream::readSome`, one
   `sysstream::writeSome`, and one OpenSSL I/O call (`SSL_do_handshake`,
   `SSL_read_ex`, `SSL_write_ex` or `SSL_shutdown`) per step. No unbounded
   loop, no sleep, no hidden thread. A step that flushes prior output may
   perform the write and skip the OpenSSL call; it never performs two writes.
2. **No hidden wait.** `WouldBlock` from the transport becomes `WantRead` or
   `WantWrite`; it is never retried inside the same call.
3. **No hidden retry.** `Interrupted`, `EINTR`, `EAGAIN`, a partial write or a
   `WANT_*` result is returned as control flow. The caller retries with the
   same absolute deadline.
4. **No byte loss.** A plaintext prefix accepted by `SSL_write_ex` is
   reported exactly once in `Wrote{n,want}`; a ciphertext prefix written to
   the transport is removed from the C staging buffer exactly once. A short
   transport write leaves the remainder pending in C and is reported by
   `want=Write`.
5. **Output-first after every SSL call.** After `SSL_do_handshake`,
   `SSL_read_ex`, `SSL_write_ex` or `SSL_shutdown`, inspect wbio/C staging
   before returning. If output is non-empty, return `WantWrite` or a progress
   outcome carrying `want=Write` (never a bare `WantRead`), even when OpenSSL
   returned `SSL_ERROR_WANT_READ`. `flush` is the explicit drain operation;
   `Done`/`Data`/`Eof`/`Wrote`/`Sent`/`PeerClosed` carry `want` when output
   remains.
6. **No unbounded growth.** rbio/wbio pending bytes, C staging, handshake
   bytes, certificate size, ALPN list, session count and the per-context
   aggregate budget are capped. Exceeding a cap fails the session; bytes are
   not silently dropped or truncated.
7. **No TLS parsing in obo.** OpenSSL reassembles records, handles KeyUpdate
   and alerts. obo moves opaque ciphertext and reads plaintext.

### 6.3 Handshake states

| Condition | Result | Next state |
|---|---|---|
| prior output pending, flush not done | `Step.WantWrite` | `Handshaking` |
| `SSL_do_handshake` returns 1 and output empty | `Step.Done` | `Established` |
| `SSL_do_handshake` returns 1 and output pending | `Step.WantWrite` | `Established` |
| `SSL_ERROR_WANT_READ` and output pending | `Step.WantWrite` | `Handshaking` |
| `SSL_ERROR_WANT_READ` and output empty | `Step.WantRead` | `Handshaking` |
| `SSL_ERROR_WANT_WRITE` | `Step.WantWrite` | `Handshaking` |
| `SSL_ERROR_ZERO_RETURN` | fatal `HandshakeFailed` | `Failed` |
| `SSL_ERROR_SYSCALL`, ret 0, empty error queue | `Truncated` | `Failed` |
| `SSL_ERROR_SYSCALL`, ret < 0, errno | `Transport{sysCode}` | `Failed` |
| `SSL_ERROR_SSL` + `SSL_R_UNEXPECTED_EOF_WHILE_READING` | `Truncated` | `Failed` |
| `SSL_ERROR_SSL` + `alpn_required_missing` flag | `AlpnRequired` | `Failed` |
| `SSL_ERROR_SSL` + `SSL_R_NO_APPLICATION_PROTOCOL` | `NoApplicationProtocol` | `Failed` |
| `SSL_ERROR_SSL` + `SSL_R_PEER_DID_NOT_RETURN_A_CERTIFICATE` or `SSL_R_TLSV13_ALERT_CERTIFICATE_REQUIRED` | `NoPeerCertificate` | `Failed` |
| `SSL_ERROR_SSL` + verify result non-OK | `VerifyFailed{code}` | `Failed` |
| `SSL_ERROR_SSL` + received alert only | `ProtocolAlert{description}` | `Failed` |
| other `SSL_ERROR_SSL` | `HandshakeFailed`/`ProtocolViolation` | `Failed` |
| `BIO`/handshake/context byte cap exceeded | `ResourceLimit` | `Failed` |
| transport `Closed`/EOF before close_notify | `Truncated`/`Transport{Closed}` | `Failed` |

`SSL_get_error` is called immediately after the SSL call, before any other
OpenSSL call that could change the error queue. `errno` is copied immediately
after a `SSL_ERROR_SYSCALL` result. The per-session `alpn_required_missing`
flag is set only by the C `client_hello_cb`; it takes precedence over a sent
alert so the server's own missing-ALPN rejection is never mislabeled
`ProtocolAlert`.

### 6.4 Read states

| Condition | Result |
|---|---|
| output pending before read | flush one chunk; `WantWrite` if incomplete |
| `SSL_read_ex` returns 1 with `n > 0` | `Data{bytes, want}`; `want=Write` if output pending |
| `SSL_pending > 0`, no transport read needed | `Data{bytes, want}`; `want=Write` if output pending |
| `SSL_ERROR_ZERO_RETURN` | `Eof{want}`; `want=Write` if output pending |
| `SSL_ERROR_WANT_READ` and output pending | `WantWrite` (never bare `WantRead`) |
| `SSL_ERROR_WANT_READ` and output empty | `WantRead` |
| `SSL_ERROR_WANT_WRITE` | `WantWrite` |
| transport `WouldBlock` | `WantRead` |
| transport `Interrupted` | `err(Transport{Interrupted})` |
| transport EOF and `SSL_RECEIVED_SHUTDOWN` set | `Eof{want}` |
| transport EOF and no close_notify | `err(Truncated)` |
| `SSL_ERROR_SYSCALL`, ret 0, empty queue | `err(Truncated)` |
| `SSL_ERROR_SSL` + `SSL_R_UNEXPECTED_EOF_WHILE_READING` | `err(Truncated)` |
| `SSL_ERROR_SYSCALL`, errno | `err(Transport{sysCode})` |
| `SSL_ERROR_SSL` verify/protocol | `err(VerifyFailed/ProtocolAlert/ProtocolViolation)` |

A partial TLS record is not application data. OpenSSL keeps it in its internal
buffer; the adapter returns `WantRead` until a complete record is available.

### 6.5 Write states

| Condition | Result |
|---|---|
| wbio/staging above cap | `err(ResourceLimit)`; fail session |
| output pending before new plaintext | flush one chunk; `WantWrite` if incomplete |
| `SSL_write_ex` returns 1 with `n > 0` | `Wrote{n, want}`; no flush of new output |
| `SSL_write_ex` returns 1 with `n == 0` and count > 0 | `err(Internal)`; invariant violation |
| `SSL_ERROR_WANT_READ` and output pending | `WantWrite` (no plaintext accepted) |
| `SSL_ERROR_WANT_READ` and output empty | `WantRead` (no plaintext accepted) |
| `SSL_ERROR_WANT_WRITE` | `WantWrite` (no plaintext accepted) |
| `SSL_ERROR_ZERO_RETURN` | `err(Transport{Closed})` |
| transport failure during prior-output flush | `err(Transport{sysCode})`; fail session |
| `count == 0` | `Wrote{0, want}`; no flush |

`SSL_MODE_ENABLE_PARTIAL_WRITE | SSL_MODE_ACCEPT_MOVING_WRITE_BUFFER |
SSL_MODE_RELEASE_BUFFERS` is set. `SSL_MODE_ACCEPT_MOVING_WRITE_BUFFER` allows
a retry after `WANT_READ`/`WANT_WRITE` even if the obo wrapper's C staging
address changed; the caller must still retry the same plaintext bytes and
length. The adapter copies the caller's slice into a stable per-session C
buffer before `SSL_write_ex`, so the address is normally stable anyway.

`flush` states:

| Condition | Result |
|---|---|
| C staging empty and wbio empty | `Done` |
| C staging empty, wbio non-empty | refill staging from wbio **without a transport write**, then perform one bounded transport write; `WantWrite` if wbio still has bytes, `Done` only if both are empty |
| C staging non-empty, transport accepts all | `Done` if wbio is also empty; otherwise `WantWrite` (do **not** write wbio in the same step) |
| transport accepts a short prefix | `WantWrite`; staging retains remainder |
| transport `WouldBlock` | `WantWrite` |
| transport `Interrupted` | `err(Transport{Interrupted})` |
| transport `Closed` | `err(Transport{Closed})` |

`Done` requires both C staging and the write BIO to be empty. A refill from
wbio is not a transport write; the step performs at most one transport write.
If staging drains and wbio still has bytes, return `WantWrite` and do not write
wbio in the same step. If wbio remains after a write, the result is
`WantWrite`.

### 6.6 Shutdown and close_notify

| Condition | Result |
|---|---|
| output pending before close_notify | flush one chunk; `WantWrite` if incomplete |
| `SSL_shutdown` returns 1 and output empty | `Done` |
| `SSL_shutdown` returns 1 and output pending | `WantWrite` (do not return `Done`) |
| `SSL_shutdown` returns 0 | `Sent{want}`; `want=Write` if output remains |
| `SSL_ERROR_ZERO_RETURN` | `PeerClosed{want}`; call again to send ours |
| `SSL_ERROR_WANT_READ` and output pending | `WantWrite` |
| `SSL_ERROR_WANT_READ` and output empty | `WantRead` |
| `SSL_ERROR_WANT_WRITE` | `WantWrite` |
| `SSL_ERROR_SYSCALL`/`SSL_ERROR_SSL` | fatal; close |

`shutdownStep` never performs a second transport write after `SSL_shutdown`;
the new close_notify is reported by `want=Write` and the next `flush`/
`shutdownStep` sends it. The HTTP `Transport.shutdownStep` maps directly to
this method; there is no separate `shutdownWrite` in the TLS API.

The adapter does **not** call `stream::shutdownWrite` automatically after
sending close_notify. The application may do so after `Sent` if it wants the
TCP FIN to follow promptly, but it must still read until peer close_notify or
a deadline. `close` closes the transport regardless.

Truncation rule: once the handshake is established, a transport EOF without
`SSL_RECEIVED_SHUTDOWN` is `Truncated`. A clean peer close_notify sets
`SSL_RECEIVED_SHUTDOWN`; `readSome` then returns `Eof`. `SSL_OP_IGNORE_UNEXPECTED_EOF`
is **not** set. `SSL_ERROR_SSL` + `SSL_R_UNEXPECTED_EOF_WHILE_READING` is also
mapped to `Truncated`, as a defense-in-depth path. This prevents a network
attacker from turning a truncated response into a shorter valid one.

### 6.7 Renegotiation, KeyUpdate and tickets

- `SSL_OP_NO_RENEGOTIATION` is set and
  `SSL_CTX_set_post_handshake_auth(ctx, 0)` is called. TLS 1.2
  client-initiated renegotiation and TLS 1.3 post-handshake authentication are
  disabled. A peer that requires renegotiation fails the connection; there is
  no fallback.
- TLS 1.3 KeyUpdate is handled by OpenSSL. It is not application data; a
  `SSL_read_ex`/`SSL_write_ex` may return `WANT_READ`/`WANT_WRITE` while
  OpenSSL processes it. The adapter's pump handles that without exposing the
  control message. If KeyUpdate produces output, the outcome carries
  `want=Write`; `flush`/`handshakeStep` drains it. `readSome` flushes prior
  output before reading, so a KeyUpdate cannot livelock behind a read wait.
- **Session tickets are disabled and there is no public option.** Set
  `SSL_OP_NO_TICKET` and `SSL_CTX_set_num_tickets(ctx, 0)` unconditionally.
  No ticket key, no resumption, no 0-RTT in v1. A future shared-key design
  needs its own review and a C-only ticket-key callback.
- 0-RTT is out of scope: `SSL_CTX_set_max_early_data(ctx, 0)`, no
  `SSL_read_early_data`/`SSL_write_early_data`, no early-data callback.
- Client-side session cache/resumption is later; the v1 client does not cache
  sessions.

### 6.8 Buffer and resource limits

| Resource | Limit | Enforcement |
|---|---|---|
| Plaintext read per `readSome` | 1..65,536 bytes | `Range` before C |
| Plaintext write per `writeSome` | 0..65,536 bytes | `Range` before C |
| Ciphertext feed chunk | <=65,536 bytes | step argument |
| rbio pending | <=131,072 bytes | `BIO_ctrl_pending` before/after feed; `ResourceLimit` |
| wbio pending | <=131,072 bytes | flush before more writes; `ResourceLimit` |
| C staging per direction | <=131,072 bytes | bounded chunk; included in counters |
| Aggregate context bytes | `maxContextBytes`, default 64 MiB | charge/release on accept/feed/close; `ResourceLimit` |
| Handshake ciphertext total | `maxHandshakeBytes`, default 1 MiB | per-session counter; `ResourceLimit` |
| Certificate chain | <=1 MiB PEM, <=16 certs, each DER <=256 KiB | loader validation |
| Private key | <=64 KiB PEM | loader validation |
| Trust bundle | <=4 MiB PEM, <=256 certs | loader validation |
| ALPN protocols | <=16, total <=1024 bytes, each <=255 | options validation |
| Sessions per context | `maxSessions`, default 256, max 4096 | `accept`/`connect`; `ResourceLimit` |
| Verify depth | 0..16, default 8 | `SSL_CTX_set_verify_depth` |
| OpenSSL max cert list | 256 KiB | `SSL_CTX_set_max_cert_list` |

These are protocol-independent caps. HTTP may impose smaller header/body
limits above TLS; TLS must not be configured above these without a reviewed
change. `SSL_CTX_set_max_cert_list` is a macro that returns the previous
limit, not a success flag; the adapter does not treat its return value as an
error.

## 7. C callback containment

All OpenSSL callbacks are **C-only**. None receives an obo pointer, calls an
obo function, allocates an obo object or runs an obo effect. The callback
argument is a C-owned `tls_context`/`tls_session` that outlives the
`SSL_CTX`/`SSL` and is freed only after the corresponding `SSL_CTX_free`/
`SSL_free`. No callback is registered after the context is closed.

| Callback | Where registered | C-only state | Allowed work | Forbidden |
|---|---|---|---|---|
| ALPN select | `SSL_CTX_set_alpn_select_cb` | copied server protocol list + policy enum | `SSL_select_next_proto`; return `OK`, `NOACK` or `ALERT_FATAL` | obo call, per-handshake unbounded allocation, logging |
| ClientHello ALPN presence | `SSL_CTX_set_client_hello_cb` | `Require` flag | `SSL_client_hello_get0_ext(...ALPN...)`; absent -> `*al = SSL_AD_MISSING_EXTENSION`, return `SSL_CLIENT_HELLO_ERROR` | any other ClientHello parsing, obo call, per-request allocation |
| SNI | `SSL_CTX_set_tlsext_servername_callback` | `requireSni` flag; later a C cert table | `SSL_get_servername`, length/emptiness check, later `SSL_set_SSL_CTX` | obo call, dynamic cert from application state |
| Verify | `SSL_CTX_set_verify(ctx, mode, NULL)` | none in v1 | OpenSSL default chain verification | custom callback in v1; hostname checks belong to `SSL_set1_host` |
| Ticket key | not installed (tickets always off) | none | none | obo call, 0-RTT, ticket keys |
| Info | `SSL_set_info_callback` | separate received/sent alert codes, state | record received vs sent alert codes/state for diagnostics | logging secrets/plaintext |
| Keylog | not installed | none | none | exposing traffic secrets |
| Msg | not installed | none | debug-only C harness if needed | production use |
| Certificate selection (`cert_cb`) | not installed | none | none in v1 | application-driven certificate selection |
| PSK/early data/OCSP | not installed | none | none in v1 | replay/stapling policy in obo |

How a C callback selects policy without obo state:

- At `createContext`, the obo wrapper validates `AlpnPolicy`, `requireSni`,
  verify mode, cipher lists and limits, copies them into a C `tls_context`,
  and registers static C functions. The policy is immutable once the first
  session exists; setters return `Busy` after that point.
- The ALPN select callback reads only the copied protocol array and policy
  enum. It writes the selected protocol through OpenSSL's `out`/`outlen`, not
  through obo memory.
- The ClientHello callback runs only for `Require`, checks that the ALPN
  extension is present with `SSL_client_hello_get0_ext`, and sets a
  **per-session** C `alpn_required_missing` flag (SSL ex-data), never a global
  or context flag, on absence. It does not select a protocol, parse other
  extensions or call obo.
- The SNI callback reads only the C `requireSni` flag and the OpenSSL-provided
  server name. It returns an alert code, not an obo error.
- The info callback records **received** and **sent** alert descriptions
  separately in the C session. Error mapping uses the received alert for
  `ProtocolAlert`; a local sent alert (for example our own ALPN rejection) is
  classified by the local reason/flag, never as a peer alert. It never logs.
- If multi-certificate selection is added later, the C context holds an array
  of pre-created `SSL_CTX*` and exact/pattern names; the SNI callback selects
  one with `SSL_set_SSL_CTX`. The array is built from obo bytes at
  configuration time, not per handshake. This is deferred (see §13).

Unsupported in v1:

- any callback that needs per-request obo state (routing, tenant lookup,
  database-backed certificate selection);
- a ClientHello callback beyond ALPN presence; certificate selection via
  `cert_cb` or ClientHello parsing is not installed;
- custom verify policy expressed in obo;
- asynchronous private-key operations or an external crypto provider;
- OCSP/CRL fetching callbacks, stapling;
- client-certificate selection callbacks;
- PSK, 0-RTT, DTLS cookies;
- QUIC transport parameters (no API in OpenSSL 3.0).

## 8. Security policy

### 8.1 Protocol versions

- Minimum TLS 1.2, maximum TLS 1.3. Default min/max = 1.2/1.3. TLS 1.2-only
  and TLS 1.3-only are representable; SSLv3/TLS 1.0/TLS 1.1 are not.
- `SSL_CTX_set_min_proto_version(ctx, TLS1_2_VERSION)` and
  `SSL_CTX_set_max_proto_version(ctx, TLS1_3_VERSION)`.
- Belt-and-braces: `SSL_OP_NO_SSLv3 | SSL_OP_NO_TLSv1 | SSL_OP_NO_TLSv1_1`.
- `SSL_OP_IGNORE_UNEXPECTED_EOF` is **not** set; unexpected EOF remains a
  truncation error, not a clean close.
- No downgrade retry. If a peer offers only TLS 1.1, the handshake fails. The
  application must not retry with a lower minimum.

### 8.2 Cipher suites and groups

`setCipherSuites` applies only the TLS 1.3 list; `setCipherList` applies only
the TLS 1.2 list. Setting one does not change the other.

TLS 1.3 (`SSL_CTX_set_ciphersuites`), default order:

```text
TLS_AES_256_GCM_SHA384:
TLS_CHACHA20_POLY1305_SHA256:
TLS_AES_128_GCM_SHA256
```

TLS 1.2 (`SSL_CTX_set_cipher_list`), default order:

```text
ECDHE-ECDSA-AES256-GCM-SHA384:
ECDHE-RSA-AES256-GCM-SHA384:
ECDHE-ECDSA-CHACHA20-POLY1305:
ECDHE-RSA-CHACHA20-POLY1305:
ECDHE-ECDSA-AES128-GCM-SHA256:
ECDHE-RSA-AES128-GCM-SHA256
```

- No RSA key exchange, static ECDH, CBC, RC4, 3DES, NULL, export or anonymous
  suites. The list excludes the RFC 7540 §9.2.2 HTTP/2 cipher blacklist
  (no CBC, no RC4, no 3DES, no static RSA).
- No DHE by default; if DHE is added later, it needs explicit DH parameters
  and a separate policy review.
- Groups: `X25519:P-256:P-384`. P-521 and X448 may be added after a policy
  review; no arbitrary curve list from untrusted input.
- `SSL_CTX_set_security_level(ctx, 2)` explicitly. This enforces roughly
  112-bit security, accepts RSA >=2048 and ECDSA P-256, and rejects SHA-1
  signatures in normal chains. Do not lower it to accommodate an old
  certificate; replace the certificate.
- `SSL_OP_CIPHER_SERVER_PREFERENCE` so the server order is used.
- `SSL_CTX_set1_groups_list` is used for both TLS 1.3 key shares and TLS 1.2
  ECDHE groups.
- The default lists are checked against the installed OpenSSL with
  `openssl ciphers`; the design-time probe confirms they are accepted.

### 8.3 Certificates, keys and chains

- PEM only in v1. DER can be added later; it is not needed for the first
  server.
- Chain: leaf first, then intermediates; root optional. The loader
  (`SSL_CTX_use_certificate` + `SSL_CTX_add_extra_chain_cert`) preserves order.
  It does not silently reorder or fetch missing intermediates.
- Private key: unencrypted PKCS#8/PKCS#1/SEC1 PEM, <=64 KiB. RSA >=2048,
  ECDSA P-256/P-384 or Ed25519. `SSL_CTX_check_private_key` must pass.
- The server's own certificate validity is not checked by OpenSSL during a
  handshake. Deployment must monitor expiry; a future `contextInfo` may expose
  `notAfter` for the loaded leaf.
- Encrypted private keys are rejected cleanly (C-only no-password callback).
  A secret-buffer/passphrase design is future work; do not prompt on stdin and
  do not log the key.
- Do not load keys with `readFile` (unbounded); use bounded `sysfile` reads or
  a test fixture. The TLS module receives bytes, not paths.
- Do not use the legacy provider, an ENGINE, a custom `CRYPTO_set_mem_functions`
  or a second OpenSSL. The default provider only.
- FIPS mode is not claimed. If FIPS is required, it is a separate deployment
  and provider design.

### 8.4 Client certificate verification

- Server `VerifyMode.None`: no client certificate requested.
- `Optional`: `SSL_VERIFY_PEER`; a client may omit a certificate. If one is
  sent, the chain is verified; an invalid chain fails the handshake. On a
  successful handshake, `peer.present` distinguishes absent from present.
- `Required`: `SSL_VERIFY_PEER | SSL_VERIFY_FAIL_IF_NO_PEER_CERT`; no
  certificate is a handshake failure.
- Trust anchors come from `setTrustedCertificates` (explicit bundle) for
  server client-auth. `useSystemTrustStore` is client-only.
- `SSL_CTX_set_verify_depth(ctx, depth)` with default 8.
- No custom verify callback in v1. The default OpenSSL verification is used;
  a non-`X509_V_OK` result is `VerifyFailed{code}` or
  `PeerVerification.Failed{code}`.
- A client certificate proves possession of a key and a chain; it does not
  authorize an application identity. The HTTP/application layer maps the
  verified subject/SAN/fingerprint to an authorization decision. TLS does not
  invent roles.
- **No revocation checking in v1.** `VerifyMode.Required` verifies chain,
  validity time and (client mode) hostname; it does **not** fetch or check
  CRLs/OCSP. A revoked-but-otherwise-valid certificate is accepted unless the
  deployment supplies its own revocation policy outside this adapter. CRL/OCSP
  support is a separate design. The test matrix includes a revoked fixture to
  make this limitation visible.

### 8.5 Hostname verification (client mode)

- `ServerIdentity.Dns`: `SSL_set_tlsext_host_name` for SNI,
  `SSL_set1_host` for verification, and
  `SSL_set_hostflags(ssl, X509_CHECK_FLAG_NO_PARTIAL_WILDCARDS)`.
- `ServerIdentity.Ip`: parse the text as an IP literal and use
  `X509_VERIFY_PARAM_set1_ip_asc`; do not send it as SNI.
- Verification is mandatory by default in `clientOptions()`; disabling it
  requires an explicit `VerifyMode.None` and yields
  `PeerVerification.NotRequested`, not `Verified`.
- A hostname mismatch is `VerifyFailed{62}` (or the corresponding
  `X509_V_ERR_*`) and the session fails. No fallback to a different name.
- `useSystemTrustStore` uses OpenSSL's default paths, which honor
  `SSL_CERT_FILE` and `SSL_CERT_DIR`. Client trust-store mode therefore
  inherits those environment variables unless the application clears them; for
  deterministic trust use `setTrustedCertificates`. A rogue environment must
  not silently replace an explicit bundle.

### 8.6 SNI, ALPN and protocol selection

- Server SNI is captured for `info.serverName`; in v1 it does not select a
  certificate. `requireSni` only requires a non-empty SNI and is off by
  default. It is **not** hostname authorization.
- **SNI must not be used for authorization or tenant routing in v1.** With one
  certificate, the server cannot verify through this API that its own
  certificate covers the SNI; `PeerIdentity` describes the peer's certificate,
  not the server's. SNI is for logging only until a `contextInfo`/
  `serverIdentity` (own certificate SANs) or multi-certificate selector is
  designed.
- Multi-certificate SNI selection is deferred. If added, it is C-side policy
  with pre-registered contexts; no obo callback.
- Server ALPN:
  - `Prefer`: select the first server-preference protocol present in the
    client list; if none, `SSL_TLSEXT_ERR_NOACK` and the connection proceeds
    without ALPN. The HTTP layer may then choose HTTP/1.1 or reject.
  - `Require`: the C ClientHello callback rejects a missing ALPN extension
    with `SSL_AD_MISSING_EXTENSION`; the handshake fails with `AlpnRequired`.
    If the extension is present but there is no overlap, the ALPN select
    callback returns `SSL_TLSEXT_ERR_ALERT_FATAL`; OpenSSL sends
    `no_application_protocol` (alert 120) and the handshake fails with
    `NoApplicationProtocol`.
  - The default `serverOptions()` offers only `http/1.1`. `h2` is advertised
    only after the HTTP/2 layer exists; an application enabling it sets the
    policy explicitly.
- Client ALPN:
  - `Disabled`: no ALPN extension;
  - `Prefer`: offer the list; accept whatever the server selects;
  - `Require`: offer the list; after handshake, if no protocol is selected or
    the selection is not in the offered list, the client fails the session
    with `AlpnMismatch` and closes. This is a local post-handshake check, not a
    protocol alert; the server may already consider the connection open.
- HTTP/2 requires ALPN `h2`. HTTP/1.1 is used with `http/1.1` or no ALPN.
  There is no `Upgrade: h2c` fallback on a TLS connection. `h2c` prior
  knowledge is a separate plaintext path.
- ALPN mismatch never falls back to a lower protocol silently. The HTTP layer
  may return an HTTP error or close, but TLS does not reinterpret it.

### 8.7 Renegotiation, compression and tickets

- `SSL_OP_NO_RENEGOTIATION`; no TLS 1.2 renegotiation, no TLS 1.3
  post-handshake auth.
- `SSL_OP_NO_COMPRESSION`; TLS compression is off. HTTP content compression is
  a separate codec/HTTP decision.
- `SSL_OP_NO_TICKET` and `SSL_CTX_set_num_tickets(ctx, 0)` unconditionally.
- `SSL_CTX_set_session_cache_mode(ctx, SSL_SESS_CACHE_OFF)`. There is no
  ticket key, no resumption and no 0-RTT in v1. Multi-process ticket keys
  would need a C-only callback and a key-provisioning design, which is a
  separate review.
- Secure renegotiation is OpenSSL's default; unsafe legacy renegotiation is
  not enabled (`SSL_OP_LEGACY_SERVER_CONNECT` is not set,
  `SSL_OP_ALLOW_UNSAFE_LEGACY_RENEGOTIATION` is not set).
- `SSL_CTX_set_post_handshake_auth(ctx, 0)`.
- `SSL_CTX_set_max_early_data(ctx, 0)`; no early-data callbacks.

### 8.8 Error queue, secrets and logging

- Before every public C operation, call `ERR_clear_error()`.
- The error queue is thread-local but shared with every OpenSSL user on the
  same thread, including libpq. The adapter owns the queue only around its own
  call and can erase another component's pending errors; other components must
  read errors immediately. This is a documented process-wide interaction, not
  a TLS-only guarantee.
- After a failure, call `SSL_get_error` first, capture `errno` immediately for
  `SSL_ERROR_SYSCALL`, then drain `ERR_get_error()` in a bounded loop (at most
  16 entries). Format each entry with `ERR_error_string_n` into a fixed C
  buffer; include library/reason numbers only if useful. Clear the remaining
  queue before returning.
- Never return `ERR_error_string`'s static pointer, `SSL*`, `BIO*`, `X509*` or
  any pointer. The diagnostic is copied into a C buffer token and then into an
  obo string; the token is freed.
- Do not install `SSL_CTX_set_keylog_callback`; it would expose traffic
  secrets. Do not log plaintext, private keys, session keys, tickets, session
  IDs or master secrets. Peer subject/issuer/SAN, cipher, version and ALPN are
  public metadata and may be logged with escaping.
- Cap diagnostic messages (for example 1024 bytes), replace control
  characters, and never parse a message to make a security decision. Branch
  on `Failure`/`PeerVerification` variants.
- Private key bytes are copied into C, parsed, then zeroized with
  `OPENSSL_cleanse` and freed. The original obo byte array is caller-owned and
  is not zeroized by the adapter. Document this; a future `SecretBytes` type
  is the right fix.
- Constant-time comparisons are OpenSSL's responsibility. The adapter must not
  compare tags, keys or secrets itself. Do not add a custom MAC/verification
  path.

### 8.9 Handshake DoS and resource limits

- Per-context `maxSessions`; `accept` fails closed with `ResourceLimit`.
- Per-session `maxHandshakeBytes` (default 1 MiB); `SSL_CTX_set_max_cert_list`
  (256 KiB); verify depth <=16.
- Per-context `maxContextBytes` (default 64 MiB) charges rbio/wbio/C staging,
  handshake accumulation, certificate/key/trust copies and fixed session
  overhead. A feed/accept that would exceed it fails with `ResourceLimit`
  before allocating; closing a session releases its charge. OpenSSL's
  internal heap is outside this budget, so the default is not a total RSS
  bound; it is the adapter-owned DoS bound.
- No renegotiation, no 0-RTT, no client-controlled unbounded certificate
  fetching, no OCSP callback.
- The application sets a finite absolute handshake deadline (for example
  10 seconds) and checks its own shutdown flag between steps. TLS has no
  internal timer, no hidden worker and no cancellation-wake claim in P2T-b.
- The reactor should watch the transport only when `want(s)` says read/write;
  otherwise a writable socket causes a hot loop. Disable write interest when
  no ciphertext is pending.
- Rate limiting, connection acceptance limits and HTTP header/body limits are
  above TLS. TLS can only bound its own record/handshake memory.

### 8.10 Fork, threads and process-wide state

- No `fork` with live TLS sessions or a live `SSL_CTX`. OpenSSL's state after
  fork is not defined for this design. Start TLS after fork/exec in the child,
  or reinitialize.
- v1 is single-threaded. Do not share `TlsContext`/`TlsSession` across
  workers; there is no `Send`/`Sync` marker and no cross-context transfer
  ticket for TLS in this note.
- `OPENSSL_init_ssl` and provider state are process-global. The adapter calls
  it once, early, with `OPENSSL_INIT_NO_LOAD_CONFIG`. If another library in
  the process already initialized OpenSSL differently, document the
  interaction; do not assume the TLS adapter controls it.
- OpenSSL may use libc `pthread_once`/locks internally; that is not permission
  to create obo threads or share obo resources. Hosted qualification covers
  this process model.

## 9. Error model

### 9.1 Result taxonomy

**One `systls` code family.** Every public `systls` function returns a TLS code
for every `Result.err`; callers must not pass a `systls` code to
`syserrors::kind` or a systems code to `systls::failure`. `sysplatform`/
`sysstream` validation and I/O errors are translated at the module boundary:
`InvalidResource`, `InvalidArgument`, `Range`, `Closed` and
`Transport{sysCode}` are TLS variants, not bare systems codes. The nested
`Transport{sysCode}` preserves the original systems code as payload.

`failure(code)` reconstructs the variant and its payload. The encoding is
library-private and not an ABI: the high byte is a TLS failure kind; the low
24 bits are the payload (0 when absent). A systems `sysCode`, `X509_V_ERR_*`
code or alert description that exceeds 24 bits is clamped to `0xFFFFFF` in the
payload and preserved in the text; current systems/X509/alert codes fit. The
kind values are assigned at implementation; they do not reuse positive
`syserrors` numbers. A code outside the TLS kind range returns `Failure.Internal`
from `failure(code)`; callers should not pass systems codes. `text(code)`
formats the kind and payload; callers branch on `failure(code)`.

Control flow is **not** a fatal error:

- `Step.WantRead`, `Step.WantWrite`;
- `ReadOutcome.WantRead`, `ReadOutcome.WantWrite`;
- `WriteOutcome.WantRead`, `WriteOutcome.WantWrite`;
- `FlushOutcome.WantWrite`;
- `ShutdownOutcome.WantRead`, `ShutdownOutcome.WantWrite`.

A `Result.err` fails that operation; it may be recoverable (`Busy`,
`NotConfigured`, `InvalidArgument`) or connection-fatal (`ProtocolViolation`,
`Truncated`). `WantRead`/`WantWrite` never appear as `Failure` codes.

| Failure | Meaning | Typical cause |
|---|---|---|
| `InvalidArgument` | malformed input/options | empty ALPN list, bad offset |
| `Range` | length/limit out of range | `readSome(max)` > 65536 |
| `InvalidResource` | stale/wrong-kind shell or transport | forged `res`, closed stream |
| `Closed` | session/context already closed | use after close |
| `HandshakeFailed` | handshake ended without a usable session | peer alert, version/cipher mismatch |
| `ProtocolAlert{description}` | **peer** sent a fatal alert | received alert captured by the info callback |
| `ProtocolViolation` | malformed/illegal protocol data | OpenSSL `SSL_ERROR_SSL` without a more specific class |
| `VerifyFailed{code}` | chain/hostname verification failed | `X509_V_ERR_*` code |
| `NoPeerCertificate` | required client certificate absent | server `VerifyMode.Required` |
| `CertificateRejected` | certificate parse/load/policy failure | malformed PEM, weak key, bad chain |
| `KeyRejected` | private key parse/mismatch/encrypted | wrong key, encrypted key in v1 |
| `Configuration` | invalid/poisoned policy | setter failure, invalid option, poisoned context |
| `AlpnRequired` | server `Require` and client sent no ALPN | ClientHello callback rejected absence |
| `NoApplicationProtocol` | server `Require` and no overlap | fatal alert 120 |
| `AlpnMismatch` | client post-handshake ALPN check failed | no/other protocol |
| `Truncated` | transport EOF without close_notify | network cut, peer crash |
| `NotReady` | handshake not complete | read/write before `Done` |
| `Transport{sysCode}` | sysstream failure | `WouldBlock` is mapped to Want; other codes are fatal |
| `ResourceLimit` | session/handshake/BIO/cert/context cap exceeded | DoS defense |
| `Busy` | context has live sessions, or setter after session | lifecycle |
| `NotConfigured` | server certificate/key missing | `accept` too early |
| `Unsupported` | client mode/QUIC/0-RTT/feature absent | phase boundary |
| `Internal` | invariant violation | adapter bug; fail closed |

A wrong-kind/forged `res` is `InvalidResource` before any C TLS call. A closed
`Stream` encountered during a TLS step is `Transport{Closed}`; a closed
`TlsSession`/`TlsContext` shell is `Closed`.

### 9.2 OpenSSL-to-TLS mapping

| OpenSSL condition (checked in this order) | Mapping |
|---|---|
| C `alpn_required_missing` flag set | `AlpnRequired`; takes precedence over sent alert 109 |
| `SSL_ERROR_SSL` + reason `SSL_R_NO_APPLICATION_PROTOCOL` | `NoApplicationProtocol`; takes precedence over received alert 120 |
| `SSL_ERROR_SSL` + reason `SSL_R_PEER_DID_NOT_RETURN_A_CERTIFICATE` or `SSL_R_TLSV13_ALERT_CERTIFICATE_REQUIRED` | `NoPeerCertificate` |
| `SSL_ERROR_SSL` + reason `SSL_R_UNEXPECTED_EOF_WHILE_READING` | `Truncated` |
| `SSL_ERROR_SSL` + `SSL_get_verify_result != X509_V_OK` | `VerifyFailed{verifyCode}` |
| `SSL_ERROR_SSL` + received alert code | `ProtocolAlert{description}` |
| `SSL_ERROR_SSL` + only a sent alert | local reason policy: `AlpnRequired`/`NoApplicationProtocol`/`Configuration`; never `ProtocolAlert` |
| `SSL_ERROR_SSL`, other | `ProtocolViolation`/`HandshakeFailed` |
| `SSL_ERROR_SYSCALL`, ret < 0, `errno` | `Transport{sysCode}`; capture `errno` first |
| `SSL_ERROR_SYSCALL`, ret == 0, error queue empty | `Truncated` |
| `SSL_ERROR_WANT_READ` | `WantRead` if output empty; `WantWrite` if wbio/C staging is non-empty; not fatal |
| `SSL_ERROR_WANT_WRITE` | `WantWrite` outcome; not fatal |
| `SSL_ERROR_ZERO_RETURN` | `Eof{want}`/`PeerClosed{want}`; clean close_notify; output pending -> want=Write |
| `SSL_ERROR_WANT_X509_LOOKUP` | `Unsupported`; no lookup callback |
| `SSL_ERROR_WANT_ASYNC`/`WANT_ASYNC_JOB` | `Unsupported`; no async engine |
| `SSL_ERROR_WANT_CLIENT_HELLO_CB` | `Internal`; our ClientHello callback never returns RETRY |
| `SSL_ERROR_WANT_RETRY_VERIFY` | `Internal`; no custom verify |
| `SSL_new`/`SSL_CTX_new` returns NULL | `ResourceLimit` (allocation) or `Internal`; drain queue |
| `BIO_new`/cert parse allocation failure | `ResourceLimit` |
| cert/key parse | `CertificateRejected`/`KeyRejected` |
| `SSL_CTX_check_private_key` != 1 | `KeyRejected` |
| no cert/key at `accept` | `NotConfigured` |
| live sessions at `closeContext` | `Busy` |
| cap exceeded | `ResourceLimit` |
| closed session/context shell | `Closed` |

`SSL_get_error` must be called with the exact return value of the immediately
preceding SSL I/O call. Do not call `SSL_get_error` after a diagnostic helper
that may call OpenSSL. Do not call `ERR_get_error` before `SSL_get_error`. The
info callback keeps received and sent alert codes in separate fields; only a
received alert can become `ProtocolAlert`.

### 9.3 No hidden retries

- `SSL_MODE_AUTO_RETRY` is not set.
- No loop on `EINTR`, `EAGAIN`, `WantRead`, `WantWrite` or partial output
  inside a public step. A step returns its outcome; the caller loops.
- There is no public `handshakeUntil` in P2T-b. The caller's loop uses the
  step API with one absolute deadline; it must not restart the deadline after
  a partial handshake, an interrupt or a short write.
- `close` is not retried after `EINTR`; the systems stream close rule applies.
- A failed handshake is not retried with a lower version, a different cipher,
  or verification disabled.

### 9.4 Deadlines, cancellation and close

- Every step is nonblocking. The caller owns the reactor, the watch and the
  absolute deadline. `watch`/`reactor::wait` are used directly; there is no
  hidden per-connection helper in P2T-b.
- Cancellation: P2T-b has no TLS helper that blocks in `reactor::wait`, so it
  does not claim cancellation wake. A future `handshakeUntil`/cancel helper
  requires a `sysreactor`/`syswaitio` cancellation-wake contract or a
  caller-provided watch source; that is a separate systems design.
- A session must not be closed concurrently from another thread in v1. The
  owner closes it after a step returns. If a future worker model allows
  cross-thread cancellation, it needs T2 and a separate review.
- Closing the underlying `Stream` behind a live session is a caller error.
  Subsequent TLS operations map the stream's `Closed` to
  `Transport{Closed}`; the session's own close still frees the SSL state and
  reports the transport close failure.

### 9.5 Safe diagnostics

- C copies OpenSSL error text into a bounded C buffer; obo copies the buffer
  into a string. No pointer escapes.
- The message may contain a library/reason number and a human string. It
  never contains a private key, plaintext, session key or ticket.
- `failure(code)` and `verifyText(code)` are diagnostic classifiers; callers
  must not parse messages.
- Peer subject/issuer/SAN strings are escaped and bounded; do not embed them
  in HTTP headers without the HTTP layer's own validation.

## 10. Tests

Tests are part of P2T acceptance. They use local fixtures only; no external
network service or DNS.

### 10.1 Certificate fixtures

Generate and commit deterministic test fixtures (or generate them in a
hermetic script with recorded hashes):

- a local test CA (RSA 2048 and ECDSA P-256);
- server certificate for `localhost` with SAN `DNS:localhost` and
  `IP:127.0.0.1`, signed by the test CA;
- a second server certificate for `wrong.example` (hostname mismatch);
- an expired certificate and a not-yet-valid certificate (fixed `notBefore`/
  `notAfter`, generated with `openssl ca` or a fixed fixture);
- a self-signed certificate not in the trust store;
- a chain with an intermediate CA, plus a missing-intermediate case;
- a client certificate signed by the test CA, an untrusted client cert, and a
  revoked-but-otherwise-valid client cert for the documented no-revocation
  limitation;
- a weak RSA 1024 key (rejected by security level 2);
- an encrypted private key (must fail cleanly, no stdin prompt);
- malformed PEM, a chain over the size/count limit, and a certificate/key
  mismatch.

The fixtures live under `obo/tests/systems/tls/` (implementation phase) with a
README stating they are test-only and not trust anchors for production.

### 10.2 Handshake and fragmentation

- TLS 1.3 and TLS 1.2 handshakes over memory BIOs, with the self-signed
  `localhost` fixture.
- Feed every handshake message one byte at a time, at every split point;
  feed multiple records in one chunk; feed an empty chunk; feed after the
  handshake is complete.
- Split the ClientHello, ServerHello, certificate, CertificateVerify and
  Finished messages; verify no data loss and no busy loop.
- Force a TLS 1.3 HelloRetryRequest (for example a client key share group the
  server does not accept) and assert the step loop handles the second
  ClientHello within the handshake byte cap without a stall.
- Drive `WantRead`/`WantWrite` at every step with a scripted transport that
  returns `WouldBlock` for N calls, then data. Assert the absolute deadline is
  unchanged and no byte is duplicated.
- Verify `handshakeStep` performs bounded work and never blocks.
- Complete a handshake with a transport that accepts one byte at a time;
  assert the peer receives Finished and `handshakeStep` does not return
  `Done` while C staging/write BIO output remains. Assert `want=Write` and a
  following `flush` drains it.
- **WANT_READ with pending output probe**: drive `SSL_do_handshake` until it
  returns `SSL_ERROR_WANT_READ` with 1,359 bytes in wbio; assert the step
  returns `Step.WantWrite` (not `Step.WantRead`), the loop watches write, and
  the following `flush` drains the 1,359 bytes before the next read.
- Repeat the output-first assertion for `SSL_read_ex` (KeyUpdate/alert),
  `SSL_write_ex` and `SSL_shutdown` (close_notify): no bare `WantRead` with
  pending output.
- Verify a step loop using `watch`/`reactor::wait` honors the same absolute
  deadline and removes its watch on error; no `handshakeUntil` helper exists.

### 10.3 ALPN and SNI

- Server offers `h2` and `http/1.1`, client offers both: `h2` selected.
- Client offers only `http/1.1`: `http/1.1` selected.
- Client offers only `h2`, server `Prefer`: `h2` selected.
- No overlap, server `Prefer`: no ALPN; handshake succeeds; `info.alpn == nil`.
- No overlap, server `Require` with ALPN present: handshake fails with
  `NoApplicationProtocol` (not `ProtocolAlert{120}`); `openssl s_client`
  observes alert 120.
- **Client `Disabled` (no ALPN extension), server `Require`: handshake must
  fail with `AlpnRequired`.** The C ClientHello callback must be invoked and
  reject absence; `alpn_calls` being zero is a test failure, not a pass.
- Client `Require` with no server ALPN: client returns `AlpnMismatch` and
  closes; no fallback.
- Default `serverOptions()` does not advertise `h2`; it offers only
  `http/1.1`. A P2T-d HTTP/1.1 server with `curl --http2` either negotiates
  `http/1.1` or cleanly refuses; it never negotiates `h2` and then closes.
- SNI present/absent; `requireSni=true` rejects absent SNI with
  `unrecognized_name`; `info.serverName` is copied correctly. SNI is not used
  for authorization in v1.
- ALPN protocol list limits: empty, 17 entries, 256-byte entry, duplicate,
  NUL, over 1024 bytes; all rejected before C.

### 10.4 Certificates and verification

- Valid chain with the test CA: `PeerVerification.Verified`.
- Untrusted/self-signed: `VerifyFailed` with the expected `X509_V_ERR_*`.
- Expired and not-yet-valid: `VerifyFailed` with the expected code.
- Hostname mismatch (client): `VerifyFailed{62}`.
- **Old protocol versions**: `openssl s_client -tls1_1`/`-tls1` fail; no
  downgrade retry. Cipher/group/security-level mismatch fails; a 1024-bit
  certificate is rejected at load.
- IP SAN verification with `ServerIdentity.Ip`.
- Wildcard certificate: exact and partial-wildcard behavior; partial
  wildcards rejected by the host flags.
- Missing intermediate: verification fails; no automatic fetch.
- Chain over 16 certs / 1 MiB: `CertificateRejected`/`ResourceLimit`.
- Key mismatch: `KeyRejected`.
- Weak RSA 1024: `CertificateRejected`/`KeyRejected` under security level 2.
- Encrypted key: clean `KeyRejected`, no prompt, no hang.
- Server client-auth `None`/`Optional`/`Required`: no cert, valid cert,
  invalid cert, untrusted cert, expired cert.
- **Required client cert, no cert**: server returns `NoPeerCertificate`, not
  `HandshakeFailed`/`ProtocolViolation`; the mapping covers
  `SSL_R_PEER_DID_NOT_RETURN_A_CERTIFICATE` and
  `SSL_R_TLSV13_ALERT_CERTIFICATE_REQUIRED`.
- **Revocation limitation**: a revoked-but-otherwise-valid client certificate
  is accepted in v1; the test asserts the documented behavior (no CRL/OCSP).
- **System trust environment**: with a rogue `SSL_CERT_FILE`, the documented
  behavior is either the environment-supplied trust store (if the application
  uses `useSystemTrustStore`) or no effect (if it uses an explicit
  `setTrustedCertificates` bundle); the test does not claim silent isolation.
- `PeerIdentity` fields for a known certificate: subject, issuer, SANs,
  timestamps, SHA-256 fingerprint match an independent `openssl x509` output.

### 10.5 I/O, close_notify and truncation

- 1-byte, 16 KiB and 64 KiB application writes; reads across record
  boundaries; `SSL_pending` path; zero-length write; max read.
- TLS 1.3 KeyUpdate during a read/write sequence; no application data lost.
  A KeyUpdate that produces output returns `WantWrite`/`Data{want=Write}`,
  never a bare `WantRead`; the caller flushes before waiting.
- TLS 1.2 renegotiation attempt rejected by `SSL_OP_NO_RENEGOTIATION`; the
  connection fails or remains established without renegotiation, as OpenSSL
  defines; no silent fallback.
- Clean shutdown: both peers call `shutdownStep`; `Sent` then `Done`; peer
  reads `Eof`; no truncation.
- Peer close_notify first: `PeerClosed`; local `shutdownStep` sends ours;
  `Done`.
- Truncation: close the transport after a partial response without
  close_notify; peer `readSome` returns `Truncated`, not `Eof`. Also truncate
  mid-record and assert `SSL_R_UNEXPECTED_EOF_WHILE_READING` maps to
  `Truncated`; `SSL_OP_IGNORE_UNEXPECTED_EOF` is not set.
- Close during handshake; close after `Failed`; repeated close false.
- Partial transport writes: scripted transport accepts 1 byte at a time;
  ciphertext is retained in C, no duplicate/loss; `writeSome` returns
  `Wrote{n, want=Write}` for the accepted prefix; the caller advances by `n`
  and `flush` drains the rest. Assert no replay and no stall.
- **Accepted prefix + blocked flush**: force a short write after
  `SSL_write_ex` accepts `n`; assert the outcome carries both `n` and
  `want=Write`. A subsequent `flush` drains without reaccepting plaintext.
- **Response then next request**: write a response with a 1-byte transport,
  call `readSome` for the next request, and assert no deadlock: `readSome`
  returns `WantWrite`/flushes pending output before waiting for input.
- **Moved write buffer retry**: retry `SSL_write_ex` after `WANT_WRITE` with a
  moved/reallocated C buffer and assert no `SSL_R_BAD_WRITE_RETRY`.
- `writeSome(count=0)` returns `Wrote{0,want}` and does not flush; `flush`
  is the explicit drain path.
- **Flush refill**: with staging empty and wbio non-empty, assert `flush`
  refills staging without a transport write, performs at most one transport
  write, and returns `Done` only when both are empty; otherwise `WantWrite`.
  Count transport writes per step and assert never more than one.
- wbio/rbio/staging cap: force over-cap; `ResourceLimit`, session failed, no
  unbounded allocation.
- **Aggregate cap**: open sessions until `maxContextBytes` would be exceeded;
  assert `ResourceLimit`, no partial session, and charges released on close.
- Large certificate chain near the limit; handshake byte counter triggers.

### 10.6 Session tickets, resumption and 0-RTT

- Tickets are unconditionally off (`SSL_OP_NO_TICKET`, num_tickets 0); no
  ticket appears in the handshake. Use `-sess_out`/`-sess_in` for any future
  TLS 1.3 resumption test; `-reconnect` is not used for TLS 1.3.
- There is no public ticket option; a test that tries to enable it must not
  compile (or the option is absent from `ContextOptions`).
- A client early-data attempt is not accepted; no early-data callback.
- Ticket key material never appears in obo, logs or `Counts`.

### 10.7 Reactor and concurrency

- Loopback TCP server with one reactor and multiple simultaneous TLS sessions;
  handshake under fragmented readiness; `want(s)` toggles read/write interest.
- No hot loop when no ciphertext is pending and the socket is writable.
- Fairness: a busy session does not starve handshakes; the reactor's batch
  cap is respected.
- A handshake deadline closes only the slow session; other sessions continue.
- There is no TLS cancellation-wake helper in P2T-b; a caller-side shutdown
  flag is checked between steps, and the session remains closable.
- `maxSessions` overflow closes the offered transport exactly once and leaves
  no session token; a forced `SSL_new`/`BIO_new` failure frees/closes exactly
  once; a failed handshake removes its watch.
- No hidden thread is created; check process thread count before/after.

### 10.8 Leak counters and lifecycle

- Omit `close(session)`: `systls::counts().liveSessions` and
  `platform::counts()` show the leak; test teardown fails.
- Omit `closeContext`: live context count is visible; `platform::shutdown`
  reports it; no silent sweep.
- `closeContext` with live sessions returns `Busy`; close sessions then
  context.
- Double close session/context returns false; no double `SSL_free`.
- Forged/wrong-kind `res`: `InvalidResource` before C; same-kind alias is
  documented and does not crash.
- **Transport replacement**: in the v0.11 public-field shape, replacing
  `transport` with another live `Stream` redirects TLS I/O and `close`; the
  test documents this trusted-obo hazard. It is not a pass for a security
  claim. Under private-by-default fields, a negative compile test must reject
  external construction/field access.
- **Partial trust bundle**: a bundle whose Nth certificate is malformed leaves
  the previous store intact; a setter that fails after native mutation leaves
  the context `Poisoned` and every later `accept` returns `Configuration`.
- **Error family**: `Busy`, `ResourceLimit`, `NotConfigured` and
  `InvalidResource` are decoded by `systls::failure(code)`, not by
  `syserrors::kind(code)`; no bare systems code crosses the public `systls`
  boundary. `Transport{code}`, `VerifyFailed{code}` and
  `ProtocolAlert{description}` round-trip their payloads.
- `exit()` runs no defers; the hosted exit path runs libc atexit; the leak
  oracle is not fooled by process exit in a test harness.
- ASAN/LSAN: no leaks from the adapter; OpenSSL global allocations may be
  reported as reachable; the C harness calls `OPENSSL_cleanup` after all
  sessions to distinguish.

### 10.9 Sanitizers and fuzzing

- ASAN/UBSAN on the C adapter and a C test harness. The hosted fixed-address
  obo image may conflict with sanitizer mappings; run the C harness separately
  and record that the obo image itself was not instrumented.
- Fuzz `tls_feed`/handshake/read with arbitrary ciphertext under
  `-fsanitize=fuzzer,address,undefined`; assert bounded memory, no crash, no
  out-of-bounds, and a fatal TLS error for invalid input.
- Fuzz PEM parsing with malformed/oversized chains and keys.
- Fuzz ALPN protocol lists, ClientHello ALPN extension presence/length and
  options validation.
- Run `valgrind`/LSAN on normal success, error and truncation paths.

### 10.10 No plaintext on the wire; no pointer/callback escape

- Put a unique sentinel in application plaintext; capture every byte written
  to the transport; assert the sentinel never appears (TLS 1.2 and 1.3,
  after KeyUpdate, partial writes).
- Assert the private key, session ticket and master secret never appear in
  diagnostics or `Counts`.
- `.cbind`/adapter check: every exported function takes/returns only `long`;
  no pointer, struct-by-value, function pointer or variadic parameter.
- `nm`/`readelf` check: the adapter object may have undefined `SSL_*`/`BIO_*`
  references (expected), but the obo wrapper has no `__cinterop` call with a
  non-scalar argument and no exported pointer-returning function.
- Compiler-negative test: `^TlsSession` cannot be passed where `^Stream` or
  `^Resource` is required; a runtime forged `res` is rejected by
  `requireKind`.
- Callback audit: ClientHello/ALPN/SNI/info callbacks contain no
  `__cinterop`, no obo symbol, no allocation of obo objects; they only touch C
  state and OpenSSL. The ClientHello callback only checks ALPN presence.
- A C fixture that tries to store an obo callback pointer in `SSL_CTX` must
  not compile/link through the approved ABI.

### 10.11 `openssl s_client` and `curl`

Against a local TLS echo/HTTP fixture configured for the listed ALPN policy,
with the test CA:

```sh
openssl s_client -connect 127.0.0.1:PORT -servername localhost \
  -CAfile ca.pem -verify_return_error -alpn h2 -tls1_3
openssl s_client -connect 127.0.0.1:PORT -servername localhost \
  -CAfile ca.pem -verify_return_error -alpn http/1.1 -tls1_2
openssl s_client -connect 127.0.0.1:PORT -servername localhost \
  -CAfile ca.pem -verify_return_error
```

The third form intentionally omits `-alpn`, which means the client sends no
ALPN extension.

Expected: TLS 1.3/1.2, expected cipher, expected ALPN, verify result 0, clean
close_notify. A mismatch name or missing CA fails with a nonzero verify
result. Tickets are off in v1; no resumption is expected. Any future TLS 1.3
resumption check uses `-sess_out`/`-sess_in`, not `-reconnect`.

After HTTP/1.1 integration:

```sh
curl --http1.1 --cacert ca.pem https://localhost:PORT/
```

After HTTP/2 integration, if the local curl has HTTP/2 and the listener
advertises `h2`:

```sh
curl --http2 --cacert ca.pem https://localhost:PORT/
```

`curl` must observe the negotiated protocol; a server that requires `h2` and
receives an HTTP/1.1-only client must fail the handshake, not silently speak
HTTP/1.1. No external network is used.

### 10.12 Acceptance gates

P2T is accepted only when:

1. the hosted OpenSSL/cinterop qualification fixture passes;
2. **ALPN `Require` rejects a client with no ALPN extension (`AlpnRequired`)**
   and no-overlap (`NoApplicationProtocol`), with the exact variant asserted;
3. **no `Done`/`Data`/`Wrote` outcome strands pending Finished, close_notify,
   KeyUpdate, alert or application ciphertext, and no step returns a bare
   `WantRead` while output is pending**; the 1,359-byte probe,
   1-byte-transport, response-then-read, accepted-prefix, flush-refill and
   one-transport-write-per-step tests pass;
4. the full local handshake/ALPN/certificate/truncation matrix passes;
5. `openssl s_client` (TLS 1.2 and 1.3, ALPN h2/http1.1/none) passes;
6. leak counters return to zero after every path and the omitted-close oracle
   fails deliberately;
7. ASAN/UBSAN and the ciphertext fuzzer pass;
8. no plaintext sentinel appears on the wire and no pointer/callback escape is
   found;
9. the design's limits, aggregate context cap, atomic/poisoned setters and
   single error family are exercised, not only the happy path.

## 11. Phases and dependencies

### 11.1 P2T phases

| Phase | Scope / exit | Dependencies | Rough effort |
|---|---|---|---|
| **P2T-a: build and hosted qualification** | OpenSSL header package/sysroot, manifest provenance, `-z now`, cinterop sigcheck include fix, hosted OpenSSL memory-BIO handshake fixture, ASAN/UBSAN harness; no public API | syslib P2 approval/implementation; cinterop hosted CRT/exit; libssl-dev; local cert fixtures | 1–2 weeks |
| **P2T-b: server TLS** | `TlsContext`/`TlsSession`, cert/key/CA loading, server handshake, ALPN (ClientHello presence + select), read/write/**flush**/multi-step shutdown, peer identity, single error family, counts, core tests, `openssl s_client` TLS echo | P2T-a; `sysstream`/`sysreactor`/`sysplatform`/`sysclock`; no `sysbuffer`/`syswaitio` TLS dependency | 2–3 weeks |
| **P2T-c: client mode (separate design)** | `connect`, SNI/hostname verification, system trust, client certs, curl/s_client client tests; no resumption in v1 | P2T-b | 1–2 weeks |
| **P2T-d: HTTP/1.1 integration** | Dispatch on ALPN; HTTP/1.1 over `TlsSession`; default ALPN `http/1.1`; graceful shutdown (HTTP response then close_notify); curl --http1.1 | P2T-b; HTTP/1.1 design | 1–2 weeks (HTTP side) |
| **P2T-e: HTTP/2 integration** | ALPN `h2` only after the HTTP/2 layer; GOAWAY then close_notify; curl --http2 | P2T-b; HTTP/2 design | HTTP/2 design effort, not TLS |
| **Later: QUIC/HTTP/3** | Separate transport; no OpenSSL 3.0 QUIC API | New library/design | Not estimated here |

The systems design's P2T estimate is 3–6 engineer-weeks (systems design:1867).
The split here is 1–2 (a) + 2–3 (b) + 1–2 (c) = **4–7**, so it supersedes the
earlier 3–6 estimate for this scope. P2T-c is a separate design; it is not a
prerequisite for P2T-b/d. P2T-d must not advertise `h2`; a `curl --http2`
client offering `h2` and `http/1.1` negotiates `http/1.1` or is cleanly
refused, never negotiated-then-closed.

### 11.2 What must exist first

- **syslib P2 approval and implementation**: P2T is blocked on P2 being
  approved and implemented, not merely on a build command. TLS requires
  `sysstream` (partial read/write, half-close, close), `sysreactor` (level
  readiness, timers, `watchResource`, `wait`), `sysplatform` (resource
  registry, `requireKind`, counts, shutdown) and `sysclock` (peer certificate
  timestamps). TLS does **not** require `sysbuffer` or `syswaitio`; HTTP may
  use those independently.
- **cinterop hosted qualification**: normal CRT startup, constructors,
  atexit, stdio, `exit_group` semantics; scalar seam. The existing cinterop
  checkpoint covers much of this for scalar/libpq/zlib, but not OpenSSL/TLS.
  P2T-a must explicitly assert provider load, `RAND_bytes`, `pthread_once`,
  `clock_gettime`/`mmap`, the thread-local error queue, `atexit`/
  `OPENSSL_cleanup` ordering, and a post-cleanup no-crash check.
- **cinterop header-mode include fix**: `include_dirs`/`cflags` applied to the
  signature check (`cinterop.py:790`); otherwise a private `libssl-dev`
  sysroot cannot be used.
- **cinterop link flags**: RELRO/NOW (`cinterop.py:821–824`); otherwise
  missing symbols can fail mid-request.
- **OpenSSL build dependency**: `libssl-dev` headers, `libssl.so.3` and
  `libcrypto.so.3` at runtime.
- **Test fixtures**: local CA, server/client certs, expired/mismatch/weak
  fixtures, `openssl s_client`, optional `curl`.
- **HTTP designs**: HTTP/1.1 and HTTP/2 boundaries, ALPN dispatch, graceful
  shutdown, header/body limits. TLS can ship its echo server before HTTP;
  HTTP integration starts only when the HTTP layer can consume a byte stream.

### 11.3 HTTP/1.1 and HTTP/2 integration contract

- The HTTP layer receives an established `TlsSession`; it drives the handshake
  with `handshakeStep` + `want` + `watch` and one absolute deadline. There is
  no `handshakeUntil`.
- It selects the protocol from `Transport.alpn()`:
  - `AlpnH2` -> HTTP/2, only if the HTTP/2 layer is present;
  - `AlpnHttp11` -> HTTP/1.1;
  - `AlpnNone` -> HTTP/1.1 if the listener policy allows it, otherwise close;
  - `AlpnOther` -> close or a protocol-specific listener policy.
- It uses only the `http1::Transport` interface; it never touches
  `session.transport` directly, never calls OpenSSL and never uses an fd or
  transport-bypass escape.
- The required methods for the neutral `http1tls` adapter, aligned with
  `obo/design/http1-server-design.md` at `326cdad`, are:
  - `handshakeStep(): Result[http1::Step]` mapping `systls::Step` to
    `StepDone`/`StepWantRead`/`StepWantWrite`;
  - `readSome(max: int): Result[http1::ReadOutcome]` mapping
    `Data`/`Eof`/`Want*` to `ReadData{bytes}`/`ReadEof`/
    `ReadWantRead`/`ReadWantWrite`; pending output forces `ReadWantWrite` or
    `ReadData` with the adapter's `want()` reporting write;
  - `writeSome(bytes, offset, count): Result[http1::WriteOutcome]` mapping
    `Wrote{n,want}` to `WriteWrote{n}` and preserving pending output through
    `want()`; `Want*` maps to `WriteWantRead`/`WriteWantWrite`, never to a
    systems error;
  - `flushPending(): Result[http1::WriteOutcome]` mapping `systls::flush`:
    drained -> `WriteWrote{0}`, pending -> `WriteWantWrite`; it never accepts
    plaintext and cannot return `WriteWantRead` in v1;
  - `shutdownStep(): Result[http1::ShutdownOutcome]` mapping
    `Done`/`Sent`/`PeerClosed`/`Want*` to
    `ShutdownDone`/`ShutdownSent`/`ShutdownPeerClosed`/`ShutdownWant*`;
  - `pending(): Result[int]` returning `systls::pending(s).ciphertext`;
  - `want(): Result[http1::Want]` mapping `None/Read/Write/ReadWrite` to
    `WantNone/WantRead/WantWrite/WantReadWrite`;
  - `alpn(): Result[http1::Alpn]` mapping nil -> `AlpnNone`,
    `http/1.1` -> `AlpnHttp11`, `h2` -> `AlpnH2`, anything else ->
    `AlpnOther{name}`;
  - `isSecure(): bool` -> `true`;
  - `maxTransfer(): int` -> `systls::maxTransfer(s)` (65,536);
  - `watch(r, readable, writable, tag): Result[reactor::Watch]` mapping
    `systls::watch`; this is the only readiness registration route;
  - `close(): Result[bool]` mapping abortive `systls::close`.
  - `systls::info`/`peerIdentity` are not required by `http1::Transport`; the
    HTTP layer only needs `alpn()` for dispatch.
  - `ReadData`/`WriteWrote`/`ShutdownSent` progress is preserved together with
    `want()` reporting `WantWrite` when output remains; no accepted prefix is
    lost.
  - The adapter never maps a `Want*` outcome to a systems error and never
    exposes a callback; every method is a synchronous bounded step.
- HTTP/1.1 request parsing, header limits, chunked framing and keep-alive are
  above TLS. HTTP/2 connection preface, SETTINGS, HPACK, stream state and
  GOAWAY are above TLS.
- Graceful shutdown: stop accepting, finish in-flight responses, send HTTP/2
  GOAWAY if applicable, call `shutdownStep` until `Done`/`PeerClosed` or a
  fixed deadline, then `close`. A truncation is an error; do not report a
  clean close.
- Client certificate identity is not part of `http1::Transport`. `http1tls`
  may expose `info.peer` as an application-level extension outside the core
  interface; the HTTP core only uses `isSecure()` and `alpn()`. TLS verifies
  the chain; it does not assign roles.
- SNI is not part of `http1::Transport`; if an application-level extension
  exposes it, it is for logging only in v1. It must not be used for
  authorization or tenant routing: the API does not expose the server
  certificate's own SANs. Multi-cert selection/`contextInfo` is later.
- No HTTP parsing, ALPN-specific framing, header injection or response
  generation in TLS.

### 11.4 Hosted-only and later work

- TLS is hosted-only. Freestanding static images stay single-threaded and
  have no TLS; use a supervised process or a plaintext local path.
- **`handshakeUntil` is deferred.** It needs a `sysreactor`/`syswaitio`
  cancellation-wake contract or a caller-provided watch source that does not
  consume unrelated events. P2T-b uses the step API only.
- Client mode (P2T-c) is a separate design; it reuses the same context/session
  types and memory BIO pump, but adds hostname verification, system trust and
  its own review. There is no client session cache/resumption in v1.
- QUIC/HTTP/3 is separate. OpenSSL 3.0.13 lacks the QUIC API; do not emulate
  it with TLS-over-UDP or a hand-written QUIC stack.
- DTLS is not planned.
- Threads/T2: if workers later use TLS, each worker needs its own
  `TlsContext`/`TlsSession`; no shell crosses a worker. `OPENSSL_init_ssl` and
  provider state are process-global and must be qualified under the thread
  note. This design does not authorize P4/P5.

## 12. Rejected alternatives

1. **`SSL_set_fd` / `BIO_new_socket` blocking or nonblocking.** OpenSSL would
   perform socket I/O directly, bypassing `sysstream`, the reactor, half-close
   and fd ownership; hidden blocking/retry and close semantics are
   unacceptable. Memory BIOs are required.
2. **Callbacks into obo (ALPN, SNI, verify, ticket, keylog).** No ABI, stack,
   ownership or effect support; the C seam has no function-pointer parameter;
   a callback could run after the obo stack frame is gone. Callbacks stay in C
   and read copied policy.
3. **Exposing `SSL*`, `BIO*`, `X509*` or `SSL_SESSION*` as integers.** No
   public pointer type; integer casts lose lifetime and type; public fields
   are not opaque; stale pointers are a memory-safety bug.
4. **Hand-written TLS.** Cryptographic protocol implementation is out of
   scope and unsafe without a dedicated review.
5. **Global library initialization per import.** Imports must have no OS side
   effects; initialization order and failure would be invisible. Use an
   explicit first `createContext` that calls `OPENSSL_init_ssl` once.
6. **Hidden retries / `SSL_MODE_AUTO_RETRY` / timeout restart.** EINTR,
   EAGAIN, WANT_READ and WANT_WRITE are control flow; one absolute deadline
   survives every retry. Hidden retries make cancellation and DoS bounds
   meaningless.
7. **Automatic finalizers on record drop.** obo has no collector; record drop
   frees only obo fields; resource exhaustion cannot wait for GC. Explicit
   close/defer is the contract.
8. **Binary-only undocumented OpenSSL ABI.** No source-less import; no
   `.sym` smuggling; no raw symbol address. Headers + link-time prototypes +
   symbol-version checks are required.
9. **Declarations-only OpenSSL.** Macro/control-number and opaque-type errors
   are invisible to the linker; one wrong `SSL_CTRL_*` or `SSL_OP_*` value can
   disable a security policy. Headers are required.
10. **Static/vendored OpenSSL or a second crypto stack.** Duplicate symbols,
    provider/error-queue/atexit conflicts, no system security updates.
11. **One global `SSL_CTX` singleton.** No per-application policy, trust
    store, ALPN or certificate isolation; tests and multi-tenant use become
    unsafe. One explicit context per policy.
12. **One `SSL_CTX` per connection.** Expensive certificate/key parsing and
    policy setup; no reason when a context is immutable and refcounted.
13. **TLS 1.0/1.1 fallback or downgrade retry.** Violates the minimum version
    policy and invites downgrade attacks; fail closed.
14. **Session tickets on by default / 0-RTT.** Ticket-key distribution and
    replay surface; 0-RTT has different anti-replay semantics. Disabled in
    v1.
15. **OCSP stapling, dynamic certificate selection, PSK, async keys in v1.**
    Each needs a C callback and a provisioning design; not part of the first
    server transport.
16. **QUIC/HTTP/3 on OpenSSL 3.0.** The API is absent; a TLS-over-UDP
    emulation is not QUIC.
17. **`fork` with live TLS.** OpenSSL state after fork is not defined for this
    design; use spawn/exec or initialize after fork.
18. **Using `readFile` for private keys/certificates.** Unbounded read and a
    path API; use bounded `sysfile` reads and explicit limits.
19. **Public optional `transport` with no binding.** A `?Stream` creates a
    `none` state and a live replacement redirects ciphertext/plaintext and
    close. Use non-optional `transport`; target private field/explicit
    constructor. The v0.11 public-field hazard is documented, not claimed
    safe.
20. **`handshakeUntil` in P2T-b without a cancellation-wake contract.** It
    cannot be woken by `syswaitio::request` through the public reactor, and a
    dedicated-reactor helper conflicts with a shared multi-connection loop.
    Ship the step API; defer the helper.
21. **A write outcome that cannot express accepted bytes plus pending
    output.** `WantWrite` after accepting `n` bytes loses the prefix;
    `Wrote{n}` without `want` hides pending ciphertext. Every progress
    outcome carries `want`; `flush` is explicit.

## 13. Open choices and recommendations

| Choice | Recommendation | Consequence of choosing otherwise |
|---|---|---|
| TLS provider | OpenSSL 3.0 system libraries | BoringSSL/GnuTLS need a new dependency, ABI and build story; static OpenSSL loses system updates |
| Headers vs declarations-only | Headers required; `signature_check: header` | Declarations-only can silently mis-set security macros/control numbers |
| Server vs client first | Server first (P2T-b); client is a separate design (P2T-c) | Client-first delays the HTTPS server goal and adds hostname/trust work |
| Session resumption | **Disabled entirely in v1; no option, no 0-RTT** | Ticket keys, rotation and replay surface are unnecessary for the first server |
| SNI multi-cert | One certificate per context in v1; SNI logging only, never authorization; C-side multi-cert later | Multi-cert needs a C SNI selector, cert table, own-cert SAN API and a new review |
| ALPN default | `Prefer(["http/1.1"])`; `h2` only when HTTP/2 exists; `Require` after the ClientHello-presence fix | Advertising h2 before h2 exists negotiates a protocol the server cannot speak |
| Client certificate default | Server `None`; client `Required` | Server `Required` by default breaks public clients; client `None` by default silently disables authentication |
| Private key encryption | Unencrypted PEM in v1; encrypted keys need a `SecretBytes` design | Passphrase in an obo string is not zeroized; prompting on stdin is unacceptable |
| Cipher policy | Explicit AEAD/ECDHE lists + security level 2 | OpenSSL defaults change across versions; explicit policy is reviewable |
| Error API | One `systls` code family; outcomes carry `want`; fatal `Failure` codes with packed payloads | Mixed systems/TLS codes and payload-less errors are ambiguous |
| Transport field | Non-optional; target `pub res` + private `transport`/explicit constructor; v0.11 public field documented as trusted-obo hazard | Optional/public transport creates a `none` state and silent redirect/close |
| Handshake helper | Step API only in P2T-b; `handshakeUntil` deferred | A helper without cancellation wake and a shared-reactor rule is unimplementable |
| Context close with sessions | `Busy`; close sessions first | Allowing context close with live sessions complicates policy lifetime and counters |
| Ticket key sharing | Not enabled in v1 | Shared keys need a C callback and a provisioning/rotation design |
| QUIC/HTTP/3 | Separate later design; OpenSSL 3.5+ or a QUIC library | Emulating QUIC on 3.0 is unsafe and non-conformant |
| Manifest extension | Add `openssl` provenance/sidecar and `link_flags`; the build must verify the sidecar | Cramming metadata into `resolved_libraries` loses version/header detail |

## 14. Invariants and review gates

### 14.1 Invariants

1. No `SSL*`, `BIO*`, `X509*`, `EVP_PKEY*`, C struct or function pointer
   crosses into obo. The C ABI is scalar `long` plus bounded buffer tokens.
2. No C callback calls obo, allocates an obo object or retains an obo pointer.
3. A `TlsSession` owns exactly one non-optional `Stream`; `close` closes it
   exactly once; record drop never closes it. The v0.11 public `transport`
   field is a documented trusted-obo hazard, not a security boundary.
4. A `TlsContext` cannot close while sessions live; `closeContext` returns
   `Busy`; a failed setter/load puts it in `Poisoned`.
5. `WantRead`/`WantWrite` are outcomes, never fatal errors; a `Result.err`
   fails that operation (which may be recoverable, for example `Busy`, or
   connection-fatal, for example `ProtocolViolation`).
6. No hidden retry, no `SSL_MODE_AUTO_RETRY`, no deadline restart, no
   renegotiation, no fallback to a lower TLS version.
7. No output stranding: after every SSL call, inspect output first. No step
   returns a bare `WantRead` while wbio/C staging is non-empty; `Done`/`Data`/
   `Eof`/`Wrote`/`Sent`/`PeerClosed` carry `want` when output remains. `flush`
   is the explicit drain; `handshakeStep` never returns `Done` with pending
   Finished/close_notify output.
8. A transport EOF without close_notify is `Truncated`, never `Eof`;
   `SSL_OP_IGNORE_UNEXPECTED_EOF` is not set.
9. Every `systls` public error is in the single TLS code family; platform/
   stream errors are translated, with `Transport{sysCode}` payload. Every C
   error is copied into a bounded diagnostic; the queue is drained; no pointer
   escapes.
10. ALPN `Require` rejects a missing extension in a C ClientHello callback and
    a no-overlap in the select callback; neither can silently succeed.
11. All buffers are bounded: plaintext, ciphertext, handshake, certificate,
    key, ALPN, sessions and the aggregate `maxContextBytes`. Over-cap fails
    closed.
12. Private key bytes are never logged and are zeroized in C; the obo input
    array is caller-owned and not zeroized.
13. TLS is hosted-only and single-threaded in v1; no shell crosses a worker;
    no `fork` with live TLS.
14. The build records OpenSSL package, header hashes, library paths/hashes,
    build IDs, version/symbol nodes and toolchain; runtime checks the major
    version and required symbols.

### 14.2 Review questions

- Is the systems platform registry (not a second `cres` table) the right
  identity owner for TLS contexts/sessions? (Recommendation: yes.)
- When does private-by-default field visibility land, and can `pub res` +
  private `transport` be adopted at that point? Until then the public field is
  a documented trusted-obo hazard.
- What exact `sysreactor`/`syswaitio` contract is needed before a future
  `handshakeUntil`/cancellation helper can be reviewed? P2T-b ships steps only.
- Is `Prefer(["http/1.1"])` the right default until HTTP/2 exists, and what
  listener policy should a `nil` ALPN result have?
- Is the proposed `PeerIdentity` field set sufficient for HTTP authentication,
  or does the HTTP layer need raw certificate DER/chain access? (Raw DER would
  be a bounded byte copy, not an `X509*`.)
- Should the build require an exact OpenSSL patch (`0x300000d0`) or accept any
  compatible `3.0.x`? Recommendation: major-3 minimum, exact only for a
  pinned deployment.
- Is a `tls-provenance.json` sidecar preferable to extending the cinterop
  manifest schema? The sidecar must be verified by the build/link gate.
- Does the cinterop signature-check include-dir fix belong in P2T-a or in a
  separate cinterop patch? It is a prerequisite for a private `libssl-dev`
  sysroot.
- Should the cinterop link always use RELRO/NOW, or only for TLS? Always is
  safer but changes every native package; a TLS-only option is smaller.
- Does the HTTP/2 design require any TLS extension beyond ALPN? (No tickets
  are available in v1; do not grow HTTP-specific policy in TLS.)

## 15. Conclusion

The implementable path is narrow and reviewable: system OpenSSL 3.0, required
headers, memory BIOs, a non-optional owned `TlsContext`/`TlsSession` pair over
`sysstream`, a bounded step pump with explicit `WantRead`/`WantWrite` and
`flush`, output-first handling after every SSL call, ALPN presence enforced by
a C ClientHello callback plus a select callback, C-only SNI/info callbacks,
explicit close and a strict TLS 1.2/1.3 policy. It does not parse HTTP, expose
OpenSSL pointers, call back into obo, hide retries or return `Done` with
stranded Finished/close_notify output. The
first deliverable is a server-side TLS echo that `openssl s_client` can drive;
HTTP/1.1 and HTTP/2 consume the same byte stream after their own designs land.
QUIC/HTTP/3 and client mode are separate follow-ons.

No implementation, compiler change, ABI change, version reservation, merge,
push or seed promotion is authorized by this note. Stop for focused
independent re-review.

### 15.1 Resolution map for the `ea02b29` review

| Review item | Resolution in revision 2 |
|---|---|
| R1/R2 output-first after SSL calls | Handshake/read/write/shutdown now inspect wbio/C staging **after** every SSL call; non-empty output forces `WantWrite` or a progress outcome with `want=Write`, never a bare `WantRead`. Added the 1,359-byte `SSL_do_handshake` WANT_READ probe and KeyUpdate/close_notify output tests. |
| R2 flush refill row | Added “staging empty, wbio non-empty -> refill staging without a transport write, then one write; `Done` only both empty, else `WantWrite`”; at most one transport write per step. |
| R3 HTTP1 contract alignment | §11.3 now matches `http1-server-design.md` at `326cdad`: `handshakeStep` `StepDone/StepWantRead/StepWantWrite`, `readSome`, `writeSome`, `flushPending`, multi-step `shutdownStep`, `pending()`, `want()`, `alpn/isSecure/maxTransfer/watch/close`; no `source()`, no `shutdownWrite`, no `WouldBlock` mapping, no callbacks. |
| B1 ALPN `Require` absent extension | C-only `SSL_CTX_set_client_hello_cb` checks `SSL_client_hello_get0_ext(...ALPN...)`; absent -> `SSL_AD_MISSING_EXTENSION` + `SSL_CLIENT_HELLO_ERROR`; `AlpnRequired`. No-overlap remains select-callback alert 120/`NoApplicationProtocol`. Tests assert both variants. |
| B2 stranded output / no flush | Added `flush`/`FlushOutcome`; every step flushes prior output first; `handshakeStep` returns `Done` only when C staging and write BIO are empty; `readSome` flushes before waiting; `writeSome(0)` is explicitly not a flush. |
| B3 accepted bytes + want | `Wrote{count,want}`, `Data{bytes,want}`, `Eof{want}`, `Sent{want}`, `PeerClosed{want}`; `want` is `None/Read/Write/ReadWrite`; no replay. |
| B4 `handshakeUntil` / cancellation / shared reactor | Removed from P2T-b. Step API + `watch` + `reactor::wait` with one absolute deadline; `forever()` removed. Helper deferred until reactor/waitio has a cancellation-wake contract. |
| B5 optional public transport | `transport` is non-optional `stream::Stream`; target `pub res` + private `transport`; v0.11 public-field replacement is documented as a trusted-obo hazard, not a security boundary; no `none` state. |
| B6 partial policy/trust | `setTrustedCertificates` replaces the store atomically; other setters stage/validate and poison the context on failure; `Poisoned` state; tests for Nth-cert failure. |
| M1 moving write buffer | `SSL_MODE_ACCEPT_MOVING_WRITE_BUFFER` + stable C staging; test retry after `WANT_*` with moved buffer. |
| M2 required client cert | Map `SSL_R_PEER_DID_NOT_RETURN_A_CERTIFICATE` and `SSL_R_TLSV13_ALERT_CERTIFICATE_REQUIRED` to `NoPeerCertificate`; exact test. |
| M3 alert precedence | Info callback separates received/sent alerts; only received -> `ProtocolAlert`; `AlpnRequired`/`NoApplicationProtocol`/`NoPeerCertificate` checked first. |
| M4 pending counters | `ciphertextPending = rbio + wbio + C staging`; `plaintextPending = SSL_pending`; `pending(s): Pending`. |
| M5 aggregate cap | `maxContextBytes`, default 64 MiB; charge/release; `contextBytes`/`maxContextBytes`; test. |
| M6 revocation | Explicit v1 limitation: chain/time/hostname only, no CRL/OCSP; revoked cert accepted test. |
| M7 error namespace/payload | One `systls` code family; platform/stream translated; `Transport{sysCode}`/`VerifyFailed{code}`/`ProtocolAlert{description}` packed and decoded by `failure(code)`; exact-family tests. |
| M8 two writes per step | One transport write per step; `Wrote{n,want}` reports new output without a second write; `shutdownStep` reports `Sent{want}`. |
| M9 unexpected EOF | `SSL_OP_IGNORE_UNEXPECTED_EOF` not set; `SSL_R_UNEXPECTED_EOF_WHILE_READING` -> `Truncated`; mid-record test. |
| M10 default ALPN h2 | `serverOptions()`/`clientOptions()` default `Prefer(["http/1.1"])`; `h2` only after HTTP/2; P2T-d test. |
| M11 `SSL_CERT_FILE`/`SSL_CERT_DIR` | Documented; `useSystemTrustStore` inherits them; explicit bundle recommended; test. |
| M12 shared error queue | Documented: adapter clears/drains only around its calls; other components must read errors immediately. |
| M13 hosted qualification | Fixture now asserts provider load, `RAND_bytes`, `pthread_once`, clock/mmap, TLS error queue, atexit/`OPENSSL_cleanup`, post-cleanup no-crash. |
| M14 cert/key ownership | `SSL_CTX_use_certificate`/`use_PrivateKey` refcount; free parsed `X509`/`EVP_PKEY`; `add_extra_chain_cert`/store take ownership; failure-path test. |
| M15 SNI authorization | SNI is logging only in v1; no own-cert SAN API; `PeerIdentity` is peer cert, not server cert. |

Minor corrections: signature-check line is `cinterop.py:790`; QUIC check uses
targeted `SSL_QUIC|OSSL_QUIC|QUIC_` patterns and notes `..._QUICK` false
positives; hosted-startup wording is qualified; the example uses `clock::` and
references/`watch` (no `forever`/`handshakeUntil`); the DAG omits
`sysbuffer`/`syswaitio`; the phase estimate is 4–7 and explicitly supersedes
3–6; P2T is blocked on P2 approval; `s_client` resumption guidance uses
`-sess_out`/`-sess_in`; `Counts` is declared once; TLS 1.3 vs 1.2 cipher
setters are documented separately; system trust paths are additive but replaced
atomically; `ProtocolAlert` is received-only; `max_cert_list` return convention
is noted; `handshakeStep` makes one SSL call.
