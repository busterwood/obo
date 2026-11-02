# Red-team findings, round 2 (2026-11-02)

Reviewed: the reference compiler at `5ba02a5` (slices, shared substrings, the
16-byte str layout, and the round-1 fixes). 12 findings: 5 high, 1 medium,
6 low; no compiler crash and no C that gcc rejects. Each has a minimal program
`gNN_*.obx` here whose header gives the behaviour the spec requires.

455 of the 486 cases in `cases/` pass under ASan, UBSan and `OBX_CHECK=1`;
every failure belongs to a finding below or to `cf_float_tiny` (exponent
literals, implemented in compiler round 2). The mutation fuzzer ran 21,000
mutants (5,000 also built with gcc) with no traceback and no gcc failure.

## High: accepted, memory corruption

- **g02** Indexing a slice result loses its root: `xs[0..2][clear(mut &xs)]`
  reads freed memory. In `check.x_index`, `if v.cat == 'rv': v =
  self.holdtmp(...)` gives a slice value with no root, so the later `hold` is
  skipped. Same for `mid(&xs)[...]`, `&xs[0..1][0]` as an argument, a method
  receiver `bs[0..1][0].get(clear(...))`, and `g[..][0][0..1]`.
- **g03** `match xs[0..1][0] { Some(b) { xs = []; ... } }` and
  `for x in g[..][0] { g = [] }` do not freeze the array: same cause; `s_match`
  and the `for` code get `root = None`.
- **g05** Array literals can hold slices: `var a = [xs[0..1]]` outlives `xs`.
  `x_arrlit` unwraps refs but does not reject slice element types; also nested
  literals, `append`, literals passed or returned.
- **g06** `array(2, xs[0..2])` stores the slice: `b_array` asks only
  `is_copy(v)`, which is true for slices; borrow types must be rejected.
- **g07** str concatenation wraps at 4 GiB: `rt_str_concat` adds 32-bit
  lengths, the sum wraps to 0, `rt_str_new(0)` returns the static empty string
  and the copy overwrites static data (dies as "stack overflow", exit 4; the
  spec requires exit 2). Check StrBuf growth, `join` and `repeat` too.

## Medium: missing trap

- **g01** `xs[1..b]` with `b = -1` is read as "to the end": `x_sub` passes -1
  for "no upper bound" and `rt_sub` treats `hi == -1` as the length. Substrings
  and `strSlice` trap correctly.

## Low

- **g04** Wrong reject (same cause as g02): `let r = &xs[1..][0]`,
  `let r = &mid(&xs)[0]`, `return &xs[1..][0]`.
- **g08** A drop method that detaches its child
  (`let c = replace(mut &n.next, None)`) recurses once per node through nested
  `rt_drun`; a 300,000-node list overflows the stack. Spec 9.4 says chain drops
  do not need deeper stacks for longer chains.
- **g09** Wrong reject: `n += f(mut &n)` (while `n = n + f(mut &n)` is
  accepted): compound assignment calls `binop(..., held=p)`.
- **g10** The constant `MIN % -1` folds to 0; at run time it traps:
  `sema.fold` has no overflow check for `%`.
- **g11** Wrong reject: `+% -% *%` in constants: no cases in `sema.fold`.
- **g12** `0.2 + f()` with a user `fromFraction` runs `f()` first: the
  `is_lit(e.l) and not is_lit(e.r)` branch of `x_bin` evaluates the right
  operand first, and `is_lit` includes `'frac'`.

## What held up

Slice freezes (append, pop, assignment, move, field assignment, grid rows,
through nested scopes, break, continue, return and match arms); rule 5 slice
results through methods, generics and function values; held arguments; two
mut slices; the round-1 late-read fixes in new positions (including `try` in
index and slice bounds); iterative drops of trees and chains, drop order,
globals dropped at exit (with substrings); stack overflow exit 4; shared
substrings in arrays, records, Option, globals and Map keys, under hash, eq
and cmp, as extern arguments, with embedded NUL, in `getenv`/`readFile` when
the output aliases the input; `strByte` bounds; every slice-taking library
function; constant-fold widths, conversions, shifts and traps.

## Harness

- `rt.py [--probe] [files/dirs]`: build with ASan+UBSan, run with
  `OBX_CHECK=1`, compare with the header. No arguments: `cases/` and `g*.obx`.
  `--probe` also builds programs expected to be rejected.
- `split.py batch.txt [dir]`: batch file to case programs (`batches/`;
  `batches/findings.txt` is the source of the g-files).
- `run1.sh`: one file with ASan. `fuzz.py N seed`: mutation fuzzer (`FZGCC=1`
  also runs gcc).
