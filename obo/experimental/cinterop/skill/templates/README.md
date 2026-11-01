# cinterop v2 adapter templates

These are real, buildable templates for the experimental toolkit. They are not
a universal generator. Copy the directory into a maintained obo package and
replace the demo operations.

Files:

- `adapter.c`, `adapter.h` — a C adapter that keeps the real `demo_item *` on
  the C side. `demo_open` returns it for an `own` result, `demo_make`
  reports failure through an out record, and `demo_close` frees once. There is
  no token registry, no `cres` and no pointer-as-integer.
- `wrapper.obx` — the maintained obo wrapper: `pub type Demo = foreign
  "demo_item"`, body-less `native fn` declarations, and exported
  `close`/`is_open` over `__foreignClose`/`__foreignIsOpen`.
- `library.cbind` — the reviewable `cinterop-proto/2` contract:
  `signature_check: header` against `adapter.h`, the `types` release table, and
  `own`/`borrow`/`out_own` kinds.
- `app.obx` — a small consumer used as a dry-run smoke test. It closes with
  `defer`, shows that a second close is safe, and reads a handle through the
  out record.

Build the template from this directory:

```sh
cd <repository>
python3 obo/experimental/cinterop/cinterop.py build \
  obo/experimental/cinterop/skill/templates/library.cbind \
  obo/experimental/cinterop/skill/templates/app.obx \
  --out /tmp/opencode/cinterop-template --obo obo/obo
/tmp/opencode/cinterop-template/app
```

Expected output:

```text
byte0=0
open=true
closed=true
out_byte0=0
```

There are no `../../toolkit` paths: the only C source is `adapter.c` beside the
spec. When you copy the files into a package, update `sources`, `headers`,
`wrapper` and `libs` for your library.

For a complete real library example, read:

- `../../examples/libpq.cbind` and `../../examples/libpq.obx` — the maintained
  v2 wrapper: `foreign` `Connection`/`Res`, typed OID arrays, `cbytes` reads
  and the census;
- `../../db/postgres/postgres.obx` — a second wrapper over libpq:
  `Connection`/`Rows` records, typed parameters and the explicit close
  contract;
- `../../db/sqlite/sqlite.cbind` and `sqlite.obx` — a declarations-only v2
  binding with out records (`open_v2`/`prepare_v2`/`prepare_at`),
  `release_result: discard`, `nonzero` and `cbytes`;
- `../../tests/fake_libpq.c` and `../../tests/fake_libpq_probe.h` —
  deterministic ownership, error and census tests;
- `../../examples/libpq_defer_app.obx` and `../../examples/zlib_app.obx` —
  explicit `defer` cleanup and a second real library.

Keep the explicit-close contract. Do not add a destructor to an ordinary obo
record and do not return a C pointer as an integer.

## Legacy `cres` policy

`toolkit/cres.c` and `cres.h` stay only for the zlib adapter and the
`examples/buffer.obx` example (and the registry test under `tests/`). zlib
is outside the v2 gates and still uses the older integer-token registry. Do not
start a new binding on `cres`: a new package uses a `foreign` handle with an
explicit `close`, as this template does. The database packages no longer include
`cres`, and the sys/tls/http2 adapters use their own `sysregistry`.
