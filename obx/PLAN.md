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
| 3 | Library: `lib/` | no compiler change except primitives | done: 9 modules, 3,300 lines, 25 library tests (OBX_CHECK=1); compiler changes: `lib/` import root, `obx_*` C helpers in rt.h; see [lib/README.md](lib/README.md) |
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
  operators (overflow). The 128 × 128 to 256-bit multiply is built from
  64-bit halves of `u128` in the library; no intrinsic was needed.
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

## Language backlog

Pain points found while writing the library (stage 3 report, 2026-11-02).

Decided by the user on 2026-11-02 and written into SPEC.md (compiler work
pending, the round after the red-team fixes):

- Records can be taken apart: `let P{a, b: x, c: _} = e` (9.1).
- A `never` result type (5.3).
- Fractional literals carry an `i128` coefficient and may have an exponent;
  the lang item is `fromFraction(units: i128, scale: int)` (2, 7.3).
- Externs are always private to their module (corrected 2026-11-02: the user
  wants C access hidden inside the module that wraps it), may be renamed with
  `as "c_name"`, and may return `str`; two externs with the same C name and
  different signatures are an error (12).
- Variants are always qualified by their type (`Expr.Num(1)`,
  `Rounding.HALF_EVEN`), in expressions and match arms, except the prelude's
  `Some`, `None`, `Ok` and `Err` (4.4).
- Generic function values inside generic code are a compiler gap to fix; and
  non-escaping closures, `&fn(A): R`, second class like borrows (4.6).
  Escaping closures wait for interface values.
- Globals: copy globals as before, plus constant tables (`const T: []u128 =
  [...]`, read-only static data seen as `&[T]`) and set-once globals
  (`var C: once Config`, `set`/`get`/`isSet`). Other mutable owned state is
  owned by `main` or a context record and passed down (5.2).
- Imports: `./` and `../` paths are relative to the importing file; bare
  paths are library modules only (5.1).

Deferred (user, 2026-11-02):

- Marking unchecked code. Any module can still declare its own extern (for
  example C's `close`) or call the raw intrinsics (`writeFd` takes a bare
  descriptor). A rule that only modules marked unchecked may do either, as Go's
  `unsafe` import or Rust's `unsafe` do, is left for later.
- Library resource types: a streaming file reader, a directory handle and seek,
  each an owned record with a private descriptor and a `drop` method, like
  `io.Writer`. Added when eza and fd need them.
