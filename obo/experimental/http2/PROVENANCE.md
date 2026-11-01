# nghttp2 provenance (H2a)

The HTTP/2 shim is built against the system nghttp2, not a vendored copy. The
runtime SONAME is the ABI gate; the development headers are a build input.
The cinterop manifest records the resolved library path, its SHA-256, the
resolved header paths and hashes, the compiler and toolchain, the cflags,
include dirs and the final manifest hash. That manifest is the per-build
provenance record (`cinterop.py verify` checks it).

Baseline used by the H2a tests:

| Artifact | Observed value |
|---|---|
| runtime package | `libnghttp2-14 1.59.0-1ubuntu0.4` |
| runtime library | `/lib/x86_64-linux-gnu/libnghttp2.so.14.26.0` |
| SONAME | `libnghttp2.so.14` |
| library SHA-256 | `46764ab5b6ca7e353a322054f0bee45d9158a27f3e6a82a2fa4447f2ba9ba268` |
| ELF build ID | `df11ae18f11cdd49163738ef2eac9fa534f9e49b` |
| development package | `libnghttp2-dev 1.59.0-1ubuntu0.4` |
| development `.deb` SHA-256 | `6f39f3e4fdeeeff06d4b9ef8101e8e76450d56b9f682a9a57890cb49528fae3d` |
| `nghttp2/nghttp2.h` SHA-256 | `4eb610b672f7f6520250619330d16ec613c51e4f31c1418db93bfbbce58e50e9` |
| `nghttp2/nghttp2ver.h` SHA-256 | `b72d511ddaf0059ad532ec83a9a5b3ec8ccc34968332a9beb016ef301b8d1187` |
| `NGHTTP2_VERSION` / `_NUM` | `"1.59.0"` / `0x013b00` |
| `nghttp2_version(0)` | age 1, `version_num 0x013b00`, `proto_str "h2"` |
| link | `-l:libnghttp2.so.14` plus the systems adapter sources |
| hardening | full RELRO, immediate binding, non-executable stack |

`NGHTTP2_VERSION_NUM >= 0x013b00` is a compile-time gate. Compile/link
references to `nghttp2_option_set_max_continuations` and
`nghttp2_option_set_stream_reset_rate_limit` make a missing security option a
build failure. The runtime check requires age 1, a compatible version number and
`proto_str == "h2"`. The Ubuntu `1.59.0-1ubuntu0.4` package is required for
`nghttp2_option_set_max_continuations`; an unpatched upstream 1.59.0 header
does not contain it.

Headers are never committed. If matching headers are installed they are used;
otherwise the test script runs `apt-get download libnghttp2-dev=<installed
version>` and extracts them into a private cache under `/tmp/opencode`. The
committed `.cbind` contains relative header names and an empty `include_dirs`;
the private build spec supplies the absolute cache path.

The test suite additionally rejects a mismatched `libnghttp2-dev` version and,
for the pinned Ubuntu package, checks the two header hashes above. This is a
corruption/provenance check, not a signature or memory-safety proof.
