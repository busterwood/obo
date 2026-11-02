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

## Status after the fixes (2026-11-02)

All 12 are fixed; `rt.py` gives 498 OK of 498 (the 486 cases, now in the
current syntax, and the 12 g-files). Each finding has a regression test,
`rt2_gNN_*` (in `../../tests/{run,error}` for spec behaviour, in
`../../ref/tests` for compiler choices and runtime limits).

| | Fix | Tests |
|---|---|---|
| g01 | A missing upper slice bound is the slice's length, not -1. | `run/rt2_g01_slice_hi_minus_one` |
| g02, g03, g04 | An indexed slice result keeps its array's root and path (`holdtmp` takes the source value), so it is held, frozen and can root a `let` or a returned borrow. | `error/rt2_g02_*`, `error/rt2_g02b_*`, `error/rt2_g03_*`, `error/rt2_g03b_*`, `run/rt2_g04_*` |
| g05, g06 | An array literal (also nested) or `array(n, v)` whose element type would be a borrow, slice or closure is an error. `append` cannot get there: its array's element type comes from a literal, a written type or a type argument, and all three reject borrows. | `error/rt2_g05_*`, `error/rt2_g05b_*`, `error/rt2_g06_*` |
| g07 | `rt_str_concat` passes a 64-bit length; every str with new bytes comes from `rt_str_new`, which checks it against `RT_STR_MAX` (4 GiB − 1) with exit 2. `StrBuf` is a `[]byte` (64-bit lengths) and `toStr`, `join` and `repeat` go through `strFromBytes`, which was already checked. g07 itself now stops with exit 2 after about 3 GiB; the unit tests build with `-DRT_STR_MAX=100` (a `// cflags:` directive of `ref/tests/unit`, run by `ref/run_tests.py`). | `ref/tests/unit/rt2_g07_*` (4) |
| g08 | In a drop method, the drops on leaving it (locals, at the end or at `return`, and the last statement's temporaries) are copied to the heap and pushed on the drop worklist (`rt_ddefer`); the glue of a type with a drop method pushes its field drops before calling the method, so the deferred values are dropped right after the method and before the fields, in the order of SPEC 9.2. | `run/rt2_g08_*` (300,000 nodes, also with a discarded `replace`), `run/rt2_g08b_*` (order) |
| g09 | A compound assignment to a place made of variables, fields and indexes no longer holds it while the right side is evaluated: the index runs once, before the right side, and the store finds the place again (as `a = a + b` does). Through a slice or a call's borrow result the place is still held. | `run/rt2_g09_*`, `error/rt2_g09b_*` |
| g10 | `MIN / -1` and `MIN % -1` in a typed constant are overflow errors. | `error/rt2_g10_*` |
| g11 | `+% -% *%` fold in a typed constant, wrapping at its width (untyped operands only: an error, there is no width). | `run/rt2_g11_*` |
| g12 | With a fractional literal on the left, the right operand's type is found by checking it and discarding the code (`peek_type`); then the literal and the right operand are evaluated in order. | `ref/tests/run/rt2_g12_*` |

**g08 and the spec.** A full guarantee is not possible: a drop method that
drops its child *before its last statement* (in an inner block, or by passing
it to a function that drops it) must run the rest of its body after the whole
rest of the chain is dropped, so each node needs a live frame (the order of
9.2 cannot be kept with constant stack). SPEC 9.4 now says that the guarantee
covers what a drop method still owns when it returns, and that the other kind
recurses and overflows on a long enough chain (exit 4).

**g09 and older tests.** Two tests expected `xs[0] += f(mut &xs)` to be
rejected (`ref/tests/error/compound_nested_mut`, round-1 case
`hb_compound_index_mut`); both now expect the result, as `xs[0] = xs[0] +
f(mut &xs)` gives. SPEC 7.4 says that the index runs once and the store goes
to the place after the right side.

**Corpus changes.** Syntax only (the meaning of each case is unchanged):
qualified variants in `dr_drop_method_builds_array_deep`,
`dr_mixed_closed_record`, `dr_tree_left_deep`, `dr_tree_right_deep`,
`dr_value_closed_boxed`, `ev_variant_named_order`, `lr_global_in_variant`,
`m2_match_consumed_drop_method`, `m2_match_global_closed_copy`,
`m2_match_global_closed_str`; `fromFraction(units: i128, ...)` in
`ev_frac_call_args_order`, `ev_frac_left_order` and `g12`. `rt.py` now
matches `// error:` words against the message without the file name
(`m2_match_consumed_drop_method` passed only because its file name contains
"drop"). One expectation changed: `dr_drop_self_recursive` (a drop method
that makes a value of its own type) loops forever without using stack now
that the value is a deferred tail local, so the case drops it in an inner
block, which still recurses to exit 4.
