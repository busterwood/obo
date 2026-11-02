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
| 4 | Self-hosted compiler, `compiler/` (obx to C) | compiles itself to a fixed point; passes the suite | done: identical AST, types and C to ref on all 2,427 test files; stage 2 == stage 3; C seed checked in (`compiler/seed/obxc.c`, `build.sh`, `reseed.sh`); full gate passes with the seed-built compiler; see [compiler/README.md](compiler/README.md) |
| 5 | Native x86-64 ELF backend; then eza, SQLite, Postgres | eza shorter than obo's and as fast | native backend done; eza done: [cmd/eza.obx](cmd/eza.obx), 1,609 lines (1,335 without comments and blank lines) against obo's 3,482 (2,842) plus its library helpers; obo's golden suite passes with the native and the C build (482 cases, 72 live-only skipped, [cmd/README.md](cmd/README.md)); native time equal to obo's on `/usr/share` trees; SQLite and Postgres next |
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
lexer, parser to an AST (an arena of nodes named by index; compiler/README.md
says why), declaration collection, the checker (definition-time for
generics), and a per-instance C emitter. The
runtime C text is embedded. It is first compiled by `ref/`, then by itself;
the generated C of stage 2 and stage 3 must be identical (fixed point). The
generated C of the compiler is checked in as the seed, so a build needs only a
C compiler. Then `ref/` is retired.

The compiler needs no interface values: AST walks are `match`, and symbol
tables are maps from names to indices. Closures (`&fn`) help with sorting and
table walks.

### How the port is checked

The self-hosted compiler is a port of `ref/`, module by module, with the same
algorithms. Its gate is mechanical: for every program in the test suites
(conformance, `ref/tests`, `lib/tests`, the red-team corpora) both compilers
must produce **byte-identical C**, and identical error messages for rejected
programs. That makes every step checkable without judgement, and catches a
porting slip on the first program that reaches it.

Before the port starts, `ref/` must be deterministic (no iteration over Python
sets or hash-ordered structures in anything that reaches the output; runs with
different `PYTHONHASHSEED` values must give the same C) and gains
`--dump-ast` and `--dump-types` outputs, so the front end can be compared
before the back end exists.

### Order

1. `compiler/syntax.obx` (lexer, AST arena, parser). Gate:
   identical `--dump-ast` on every test file.
2. `compiler/sema.obx` (modules, imports, declarations, types, constraints,
   constant folding). Gate: identical `--dump-types` and identical errors for
   the rejection tests that fail in sema.
3. `compiler/check.obx` and `compiler/emit.obx` (bodies to C templates,
   instances, glue). Gate: identical C for every test program; identical
   errors for every rejection test.
4. `compiler/main.obx` (driver; the runtime `rt.h` embedded as a constant;
   gcc invoked as `ref/` does). Bootstrap: `ref/` compiles the compiler (stage
   1), stage 1 compiles it (stage 2), stage 2 compiles it (stage 3); the C of
   stages 2 and 3 must be identical. The generated C of the compiler is checked
   in as the seed, so a build needs only gcc.
Notes for the port: `ref/` checks the right operand of an operator twice when
the left operand is a fractional literal (to learn its type, red-team fix
g12), which is exponential in deeply right-nested chains. The port should get
the type from a cheaper type-only query and keep the C identical.

5. Retire `ref/` once the seed build passes every suite. The conformance suite
   and red-team corpora remain the acceptance tests.

## Language backlog

Pain points found while writing the library (stage 3 report, 2026-11-02).

Decided by the user on 2026-11-02 and written into SPEC.md; all implemented
in the reference compiler (compiler round 2, 2026-11-02), with the library
moved over (qualified variants, `never` stop helpers, renamed externs, the
`POW10` constant table, `&fn` predicates, `Map.each`):

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

Pain points from writing the self-hosted front end (stage 4 step 1,
2026-11-02), for the user to decide; the most pressing is the first, since
`check` is full of `self.f(..., self.g())`:

1. Rule 3 rejects nested mutating calls on one receiver, `p.node(K,
   p.expr())`, `p.toks[p.next()]`; about 45 calls became `let`s. A two-step
   receiver borrow (the receiver is only reserved while the arguments run,
   like Rust's two-phase borrows) would accept them.
2. `type`, `mut`, `pub`, `once` are keywords, so they cannot be names.
3. `match` arms cannot be named constants; kinds are compared with if-chains.
4. No conditional expression; no tuples or multiple results (out-parameters).
5. Imported names are always qualified (`sx.` about 130 times in one file).
6. No exceptions: the first error stops the program (fine for a compiler).

Decided by the user on 2026-11-02 and implemented in ref (SPEC 7.5, 7.8, 8,
10): borrow arguments and receivers whose place is variables, fields and array
indexes are taken when the call is made (point 1; `p.toks[p.next()]`, an
index, is unchanged); `if` and `match` expressions (point 4, the first half);
constants as match arms (point 3).

More from porting sema (stage 4 step 2, 2026-11-02):

7. An element of an array of records cannot be read into a local without a
   copy type (`let d = pg.types[i]` moves out of a borrow), and a `let d =
   &pg.types[i]` borrow freezes all of `pg`, so no `pg.mk...()` call may
   follow while it lives. sema therefore writes `pg.types[d].tparams[i]`
   paths in full (about 250 of them), and splits a dump function into a
   part that mutates `pg` and a read-only part that borrows.
8. Rule 4 (a `for` over `pg.x` freezes `pg`) makes every loop that interns
   types while walking a declaration's list copy the list first (`copyInts`,
   about 15 times) or use an index loop.
9. No big integers: constant folding needs Python's unbounded `int` (and
   `repr(float)` for its dump and for C), so `num.obx` adds 450 lines.
10. A record literal names every field, so creating a FnDecl or TypeDecl
    lists 15 to 18 fields, most of them defaults; field defaults (or a
    `..base` update form) would remove that.
11. A record as a map key needs hand-written `eq` and `hash` methods;
    interning types wants a structural key, so sema builds a string key per
    type instead.

Performance (`bench/README.md`). Done in `ref/` 2026-11-02, for the port to
copy: `gcc -O2`, direct drop glue for types that cannot form a chain, `malloc`
and live-object counting only under `OBX_CHECK`, 64-bit `toStr` digits; obx is
now 0.25x to 1.33x obo's time. Left:

- Language note: a borrow cannot be moved along a chain (`cur = &cur.next`),
  so walking a list by borrow recurses. Worth a decision before eza.
- Deferred to the native backend (the user, 2026-11-02: matching obo is not
  needed while the compiler emits C): one block for a `str` concatenation
  chain `a + b + c` (now n-1 blocks, n-2 of them temporaries), and a
  size-class free list in `rt_alloc` (half of `lists` and `trees` is
  `malloc`/`free`).

Small gaps noted by the round-3 fixes (2026-11-02), not yet decided:

- A record literal through a generic alias needs written type arguments
  (`P[int]{...}`); variants through one infer them.
- At exit, copy globals (`str`) are dropped after every set-once global
  instead of reverting to their initializer; equivalent for soundness.

Deferred (user, 2026-11-02):

- Marking unchecked code: not planned. A module that declares its own extern
  (for example C's `close`) or calls the raw intrinsics takes responsibility
  for what happens (the user, 2026-11-02). The safety promise covers obx code
  that uses no externs of its own.
- Library resource types: a streaming file reader, a directory handle and seek,
  each an owned record with a private descriptor and a `drop` method, like
  `io.Writer`. Added when eza and fd need them.
