# cinterop adapter templates

These are real, buildable templates for the experimental toolkit. They are not
a universal generator. Copy the directory into a maintained obo package and
replace the demo operations.

Files:

- `adapter.c`, `adapter.h` — a C adapter using the toolkit registry. The
  exported ABI is scalar (`long` in/out); the raw pointer stays in C.
- `wrapper.obx` — the maintained ordinary obo wrapper with borrowed explicit
  close and `defer` usage.
- `library.cbind` — the reviewable contract for the adapter API and tools.
- `app.obx` — a small consumer used as a dry-run smoke test.

Build the template from this directory:

```sh
cd <repository>
python3 obo/experimental/cinterop/cinterop.py build \
  obo/experimental/cinterop/skill/templates/library.cbind \
  obo/experimental/cinterop/skill/templates/app.obx \
  --out /tmp/opencode/cinterop-template --obo obo/obo
/tmp/opencode/cinterop-template/app
```

The relative `../../toolkit/cres.c` path in `library.cbind` and
`../../toolkit/cres.h` include in `adapter.c` assume this directory layout. When
you copy the files into a package, update those paths and add the package's own
C sources and libraries.

For a complete real library example, read:

- `../../examples/libpq.cbind` and `../../examples/libpq.obx` (the maintained wrapper);
- `../../toolkit/libpq_adapter.c` (packet parsing, PQexecParams, result copies);
- `../../tests/fake_libpq.c` and `../../tests/fake_adapter_test.c`
  (deterministic ownership/error tests);
- `../../examples/libpq_defer_app.obx` and `../../examples/zlib_app.obx`
  (explicit `defer` cleanup and a second real library).

Keep the explicit-close contract. Do not add a destructor to an ordinary obo
record and do not return a C pointer as an integer.
