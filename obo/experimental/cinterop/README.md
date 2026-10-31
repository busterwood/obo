# Experimental C interop (`cinterop`)

Status: **experimental prototype, not a public compiler API and not a stable
format.** The design proposal is
[`../../design/native-c-interop-design.md`](../../design/native-c-interop-design.md).
Do not merge, push, promote or seed-copy this as a release feature without the
separate architecture/security review.

This directory contains a working scalar prototype of the design:

- a small JSON spec (`*.cbind`) names C symbols, scalar argument types and a
  result type;
- `cinterop.py` generates an ordinary obo wrapper module, an assembly thunk for
  every function, and a SHA-256 manifest;
- the obo compiler is invoked with the experimental `--native-relocs` sidecar;
- the fixed obo image is linked with the system `cc`/`ld`, the generated shim,
  the supplied C sources and the requested shared library.

The application sees ordinary obo:

```obo
module app
import "hello.obx"

fn main() {
    println(hello::add(20, 22))
}
```

## Build

```sh
# from the repository root
cd obo
make obo
python3 experimental/cinterop/cinterop.py build \
    experimental/cinterop/examples/scalar.cbind \
    experimental/cinterop/examples/scalar_app.obx \
    --out /tmp/opencode/c-interop-scalar --obo ./obo
/tmp/opencode/c-interop-scalar/app
python3 experimental/cinterop/cinterop.py verify \
    /tmp/opencode/c-interop-scalar/manifest.json
```

The libpq availability probe uses only `PQlibVersion`, with a manual
declaration because the development header is not installed on the probe host:

```sh
python3 experimental/cinterop/cinterop.py build \
    experimental/cinterop/examples/pq.cbind \
    experimental/cinterop/examples/pq_app.obx \
    --out /tmp/opencode/c-interop-pq --obo ./obo
/tmp/opencode/c-interop-pq/app
ldd /tmp/opencode/c-interop-pq/app
```

It does not connect to a database, send SQL, or start a server.

## Tests

```sh
python3 obo/experimental/cinterop/tests/test_cinterop.py
```

The targeted test builds the scalar module (0..8 arguments, signed 64-bit,
alignment checks), runs a freestanding ABI probe that checks
`rbx`/`rbp`/`r12..r15` preservation, verifies the manifest, and exercises
wrong-arity, platform-mismatch, modified-object and stale-manifest failures.
It then builds the `PQlibVersion` probe when `libpq.so.5` is available.

## What this prototype actually supports

- `i64` and `i32` integer arguments, 0..8 arguments, and `i64`/`i32` results.
- Generated obo wrappers with ordinary `pub fn` interfaces and ordinary
  `.sym` format 5.
- A real non-PIE system-linked `ET_EXEC` with the native object linked in.
- Explicit function specs, a C signature check when a header or declaration is
  supplied, and manifest hash verification.

## Boundaries and known limits

- The `cinterop("symbol", args...)` builtin is experimental. It is scalar-only
  and direct use without the generated manifest is unsupported; the compiler
  refuses to link native calls without `--native-relocs`.
- The tool extracts the compiler's fixed `ET_EXEC` load regions into a
  relocatable object before the final link. That is a generic prototype bridge,
  not a production object emitter.
- The host startup is a prototype: it uses the host `crt1.o`/`crti.o`/`crtn.o`
  (and therefore the normal `__libc_start_main` path) plus a synthetic
  initial-stack block; `rt_finish` still exits by raw syscall, so libc
  `atexit`/stdio teardown is bypassed.
- No ownership/pointer mapping is implemented. No `PGconn`, `PGresult`, C
  `malloc` pointer or borrowed C byte range crosses the prototype boundary.
  Automatic foreign-resource cleanup is future work.
- No floats, decimals, callbacks, variadics, structs by value, TLS, C++ or
  Windows/macOS support.
- Hash checks are provenance/corruption checks, not a proof that C is memory
  safe. C is trusted and unchecked.
- The final layout is non-PIE and depends on the fixed obo addresses. PIE/ASLR
  is not supported.
- The libpq branch is a version call only; it is not a database driver.
