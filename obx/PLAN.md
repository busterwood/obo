# obx plan

The stages come from section 7 of
[generic-core-rethink.md](../obo/design/generic-core-rethink.md). The user set
this order on 2026-11-02: finish the language (decimal, float and the rest)
and the self-hosted compiler first. Only then port the existing programs
(eza, the SQLite and Postgres bindings) to obx as targets.

| Stage | Content | Gate | State |
|---|---|---|---|
| 1 | Core spec, [SPEC.md](SPEC.md) | about the size of obo's core guide | draft 1 written (about 580 lines) |
| 2 | Reference compiler, `ref/` (Python to C) | under 5,000 lines; conformance suite passes with no leaks | done: 3,800 lines, 387 conformance + 77 own tests pass; soundness review next |
| 3 | Library: `lib/` | no compiler change except primitives | next |
| 4 | Self-hosted compiler, `compiler/` (obx to C) | compiles itself to a fixed point; passes the suite | |
| 5 | Native x86-64 ELF backend; then eza, SQLite, Postgres | eza shorter than obo's and as fast | |
| 6 | Retire obo 0.19 | | |

## Stage 3: the library

All of it is ordinary obx code over a small set of built-in intrinsics.

- **Decimal.** obo's decimal semantics carried over as library code: 128-bit
  coefficient (38 significant digits), exponent, display scale, finite values
  only. Exact `+ - *`, a `div` with an explicit scale and rounding mode, the
  eight rounding modes, `cmp`/`eq`/`hash` by value (`1.0 == 1.00`),
  `parse`/`format`/`canonical`. Errors are `Result` values for the operations
  that can fail by value (`div`, `quantize`, `parse`) and a stop for the
  operators (overflow). It needs the 128 × 128 to 256-bit multiply. The
  library can build that from 64-bit halves if the language gets a `u128` or a
  `mulHigh` intrinsic. Deciding which belongs to this stage.
- **Money[C: Currency]** over Decimal, with ISO 4217 currencies as types.
- **Float**: formatting (shortest text that reads back as the same value),
  parsing, and the math functions as externs to libm.
- **Containers**: `Map[K, V]` (hash, open addressing), `Set[T]`, `Vec`
  helpers (insert, remove, slice copies), `sort`/`sortBy` (merge sort,
  stable), binary search.
- **Strings**: `StrBuf`, split, join, find, trim, prefix and suffix tests,
  parsing integers, UTF-8 decode, formatting helpers (`fmt`).
- **OS**: `io` (stdout/stderr, buffered writer, read lines), `fs` (read,
  write, list a directory, stat), `os` (args, env, exit), `time`.

## Stage 4: the self-hosted compiler

The compiler is written in obx, structured like the reference compiler:
lexer, parser to an AST (boxed closed types), declaration collection, the
checker (definition-time for generics), and a per-instance C emitter. The
runtime C text is embedded. It is first compiled by `ref/`, then by itself;
the generated C of stage 2 and stage 3 must be identical (fixed point). The
generated C of the compiler is checked in as the seed, so a build needs only a
C compiler. Then `ref/` is retired.

Draft 1 of the language has no closures and no interface values. A compiler
does not need either: AST walks are `match`, and symbol tables are maps from
names to indices.
