# Red-team findings, round 4 (2026-11-02)

Reviewed: borrows taken at the call (d72da54), `if`/`match` expressions and
constant match arms (060f40b). 330 programs (`cases/` 314, `mcases/` 7
multi-file), findings `k01`..`k06`; generated A/B pairs (`gen.py` 3,680 pairs,
`gen3.py` 1,500 pairs, compared by `diff.py`), 12,000 fuzz mutants, and the
self-hosted front end under ASan+UBSan on every test (4,418 runs). No memory
error, leak or dangling pointer in any accepted program.

Counts: 2 medium (trap order), 4 low (2 wrong accepts, 2 wrong rejects).

## Medium

- **F1** `k01_late_index_not_checked_in_position`: in `f(mut &xs[5], g())`,
  `g()` runs before the index trap; SPEC 7.5 says the index is checked in
  position and again at the call. `x_index` builds `rt_idx` only inside the
  place's C expression; `call()`/`late()` emit no check in position. Also for
  ordinary borrow arguments, receivers, `append`, `replace` (predates d72da54).
- **F2** `k02_trap_order_unsequenced_c_args`: `f(10 / z, xs[5])` exits 3, not
  10, under gcc; the same for binary operands, ternary `if` expressions, a late
  receiver against a trapping value argument. Arguments that are not hoisted
  are emitted into one C call or operator, whose evaluation order C leaves
  unsequenced (predates the merge).

## Low

- **F3** `k03_match_else_not_last`: `else` before other arms, or two `else`
  arms, parse; the C fails ("internal error"). `matchstmt` does not require
  `else` last and once.
- **F4** `k04_char_literal_arm_on_int`: `match n { 'a' {..} }` with `n: int`
  is accepted (`match_lits` does not check a char arm against a non-byte).
- **F5** `k05_matchx_frac_literal_arm`: `match k { 1 { 1.5 } else { f } }`
  with `f: float` is rejected; `x_matchx` re-types only integer literal arms
  (`x_ifx` handles fractional ones).
- **F6** `k06_literal_branch_too_big_for_int`: `if c { 18446744073709551615 }
  else { u }` with `u: u64` is rejected ("does not fit int"): the literal
  branch is checked without an expected type before it is re-checked.

Borderline, not filed: `if c { if d { 1 } else { 2 } } else { u }` is
rejected ("int and u64"); the inner `if` is not a literal, so this is the rule
as written.

## Spec decisions for the fixes (orchestrator, 2026-11-02)

- F1/F2: every check (index, arithmetic, conversion) happens in the
  left-to-right evaluation order of SPEC 7: an operand's trap comes before
  anything a later operand does. The C must sequence them (hoist into
  temporaries where needed), never rely on C argument order.
- F3: `else` is the last arm and appears at most once (syntax error).
- F4: a char literal arm needs a byte scrutinee, as `==` does.
- F5/F6: a literal branch or arm takes the type of the other branches; it is
  never first fixed to its default type. The borderline nested case stays
  rejected.

## What held up

Late borrows re-find their place correctly when a later argument grows,
shrinks, clears, reassigns, replaces, swaps or pops the array (nested arrays,
fields, value records, generic and constraint receivers, borrow parameters and
variables); rule 3 and moves against late borrows; `xs[0] += f(mut &xs)`;
closures; if/match expressions (only the chosen branch runs, drop order of
temporaries, `try`, consuming matches, conditions, globals, once, externs);
constant arms (u128/i128 limits, aliases, imports, private constants,
shadowing, duplicates).

## Harness

`rt.py` (ASan+UBSan, `OBX_CHECK=1`; `CC=clang`), `split.py`, `gen.py OUT [2]`
and `gen3.py OUT` then `J=12 diff.py OUT`, `fuzz.py`, `run1.sh`, `selfhost.sh`
(self-hosted front end at -O0; -O1 gives false LSan reports on exit(1) paths).
