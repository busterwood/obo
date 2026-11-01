# Decimal storage tier: plan

**Date:** 2026-11-01
**Status:** plan, for an independent review before implementation.
**Baseline:** `main` `6f83880` (obo `0.15`, `SYM_FORMAT=8`, `SYM_ABI=1`, after cinterop v2 stage d).
**Branch/worktree:** `decimal-storage` at `.claude/worktrees/decimal-storage`.
**Target:** one release, `0.16` / `SYM_FORMAT=9` / `SYM_ABI=1`, merged once with one seed promotion.

This is the remainder of milestone **M2 ("usable tier")** in
[decimal-language-design.md](decimal-language-design.md) §6. That design already
decided the layout (§1.2: 24-byte inline field, stride-24 pointer-free array,
24-byte global/constant, `.sym` constant `hex "<48 hex>"`). This note does not
reopen those decisions. It turns them into stages, names the code to change, and
sets the defaults for questions that design left open.

## 1. Why

`decimal` works only as a local, parameter, return value or `Result[decimal]`
payload. A business program can't hold an `Invoice { total: decimal }`, a
`[]decimal` of line amounts, a `const VAT_RATE = 0.20`, or a `SqlArg` decimal
variant. Today money has to travel as text. This tier makes decimal a
storable value.

## 2. Current refusals (all in `obo/src/obo.obo` at the baseline)

| Line | Refusal | Stage |
|---:|---|---|
| 5757 | `decimal record fields are not supported in v1` (shared by `ref record` and closed-variant fields) | D1 |
| 5685, 14975 | `arrays of decimal (24-byte stride) are not supported in v1` (source parser and `.sym` parser) | D2 |
| 10995, 11054 | `decimal globals are not supported in v1` | D3 |
| 13504, 13508 | `decimal constants are not supported in v1` | D3 |
| 13264-13265 | decimal method parameters/returns | D4 |
| 13033, 13123 | decimal interface method parameters/returns | D4 |
| 5654, 5667, 7022 | decimal function-value parameters/returns | D4 |
| 9841, 9893 | decimal lambda parameters/returns | D4 |
| 9941 | decimal captures | D4 |
| 7371 | `string(decimal)` | not changed (use `decimal::format`) |

Existing helpers to reuse: `decimalTemp`, `decPtr`, `copyDecimal`,
`copyDecimalToBase`, `copyDecimalToPtr`, `pushDecimalValue` (`obo.obo:3488-3577`).
A decimal value is already an addressable `Item` (`mode "mem"`: base, index,
disp), so a field read is the existing field path with a 24-byte type, and a
store is `copyDecimal`.

Layout facts the plan relies on:

- `fieldList` (`obo.obo:5740-5776`) assigns `offset += 8` per field and builds
  the descriptor from **byte** offsets of owned and string fields
  (`descriptor(offset, ptrs, strs)`). Decimal is pointer-free and not a string,
  so a 24-byte field only needs `offset += sizeOf(t)` and no pointer-map entry.
- The runtime `Array` already carries `esize` (`runtime.c:315`).
  `rt_alloc_array` accepts kinds 1/2/4/6 with esize 1 or 8 (`runtime.c:501-509`).
  `rt_append` copies a one-word `v` (`runtime.c:513-532`). `rt_truncate` memsets
  `esize` bytes (`runtime.c:537-544`).
- `Result[decimal]` already stores a 24-byte payload in a heap cell, which
  proves the allocator and descriptors handle 24-byte pointer-free data.

## 3. Stages

Every stage lands in this one worktree as one or more commits. Each goes through
an implementation agent, then an independent review agent that tries to break
it, then fixes, before the next stage starts. Only one compiler stage is in
flight at a time. The seed is **not** committed on the branch.

### D1 — record and closed-variant fields

- Add `fn sizeOf(t: ^Type): int` (24 for decimal, 8 otherwise) and use it in
  `fieldList` and in the `.sym` field-list reader, so source and `.sym` agree on
  offsets. Alignment stays 8, fields stay in declaration order, and there's no
  reordering or padding.
- Field read: the existing selector path (`obo.obo:~9535`) already yields a
  `mem` item. Check that every consumer of a decimal item (arithmetic, compare,
  `println`, argument push, `ok(...)`, return) accepts a `mem` item whose base
  is a register and not RBP.
- Field write: `r.f = d`, compound `r.f += d` / `-=` / `*=` / `%=`, and
  record literals `new T{f: d}` all copy 24 bytes. The store paths currently
  emit one-word moves, so add a decimal branch that uses `copyDecimal` /
  `copyDecimalToBase`.
- Assignment-across-call rule (`LANGUAGE.md` §12): a decimal field target whose
  right-hand side calls a function that might free the record must be refused
  exactly as for an int field. The decimal operators call runtime helpers, so
  keep the target address valid across the helper call. Compute into a
  temporary, then copy.
- Closed variants: a variant field of type decimal (for example `Decimal { v: decimal }`)
  is laid out with the same `sizeOf`. Consuming and non-consuming `match` copy
  the 24 bytes into the arm's binding.
- Through handles (`h.f` where `h: ^T`) and through `&T` / `mut &T` borrows.
- `.sym` field lists: write and read `decimal` as a field type. A **private**
  decimal field can't be a one-word opaque `priv` slot. Add a 24-byte opaque
  marker (`priv24`) so the layout survives without leaking the type. This is
  part of the reason for the format bump.
- Tests: read/write/compound/literal, nested records, variant payloads with
  `match`, handles, borrows, a cross-module record with public and private
  decimal fields (the importer builds from `.sym` only), heap leak/peak checks
  under `OBO_CHECK=1`, and the assign-across-call refusal.

### D2 — `[]decimal`

- Runtime: a new array kind for 24-byte pointer-free elements (pick an unused
  kind number, documented beside the `kind:` comment at `runtime.c:500`).
  `rt_alloc_array` sets `esize = 24`. Add an `rt_append_dec(Array*, const u64 *src)`
  that copies 24 bytes. `rt_truncate` already works by `esize`. Check the
  array copy path at `runtime.c:~1534`, the empty-data path and `free_block`
  for the new kind.
- `new []decimal(n)` zero-fills; all-zero bits are decimal `0` with dscale 0
  (design §1.1), so no initialiser loop is needed.
- Index read/write: address = data + 8 + i*24. x86 SIB scale stops at 8, so
  compute `i*3` and use scale 8 (`lea r, [i+i*2]`). Bounds checks are unchanged.
- `append(xs, d)`, `truncate`, `len`, `for d in xs` (24-byte copy per pass) and
  `for i in 0..len(xs)`.
- `take`/`replace` on a decimal element behave as they do for an `int` element:
  where `int` is refused, decimal is refused with the same diagnostic shape.
- `[]decimal` as a record field, a parameter (`&[]decimal`, `mut &[]decimal`),
  a return and in `Result[[]decimal]`.
- `.sym`: remove the `14975` refusal and accept `[]decimal` in signatures and
  field lists.
- Tests: sizes 0/1/8/9 (growth boundaries), append after truncate,
  out-of-bounds trap, sum loop, nested `[][]decimal`, cross-module, and
  heap/leak/peak checks.

### D3 — globals and constants

- Globals: `var g: decimal` (and `var g = 1.50`) in 24-byte zero-initialised
  data. Module initialisation runs the initialiser, then all reads and writes
  go through `copyDecimal`. Public globals across modules use `.sym` global
  lines with type `decimal`.
- Constants: `const RATE = 0.0825` and `const NEG = -1.5`. The value is a
  **literal or unary-minus literal only**. Arithmetic in a decimal `const` is a
  compile error (`decimal constants must be a literal; arithmetic is not
  folded`), because decimal folding is not in v1. A use materialises the value
  with the same three immediate word stores as a literal (`emitDecWords`), so
  no rodata is needed. `.sym` writes `const NAME decimal hex "<48 lowercase hex>"`
  (w0, w1, w2 little-endian words, each as 16 hex digits). The reader validates
  that length, that the case is lowercase, and that w2 has zero high bits and a
  sign of 0 or 1. Malformed input is a symbol-file error.
- Tests: global read/write/compound, global in a record literal, pub const
  across modules from `.sym` only, a malformed `.sym` constant (`sym_fuzz`
  corpus), and a negative zero constant (prints `0`, positive).

### D4 — methods, interfaces, function values, lambdas, captures

- Methods: decimal parameters and returns, using the existing hidden destination
  pointer for returns. A decimal is never a receiver.
- Interface members: decimal parameters and returns through the vtable call,
  with the hidden destination pointer pushed in the same place as for direct
  calls. This also removes the internal error at `obo.obo:7022`.
- Function types and non-capturing lambdas: `fn(decimal): decimal` with
  call-through-pointer. Keep the destination-pointer convention identical to
  direct calls, so one function can be both called and used as a value.
- Stage B captures: a decimal is captured by a **24-byte value snapshot**, like
  the float one-word snapshot (`LANGUAGE.md` §5d). `mut`/`move` captures stay
  unsupported.
- `.sym`: function, method and interface signatures with decimal parameters and
  returns.
- Tests: each form direct and cross-module, a lambda passed to a sort or
  accumulator, a capture snapshot that doesn't see later changes, and
  interface dispatch over two implementations.

### D5 — `lib/decimals.obx` (no compiler change)

`decimal` is a reserved type name, so the module is `decimals`. It is ordinary
obo built on the builtins:

- `floor(d, scale)`, `ceil`, `trunc`, `round(d, scale)` (HALF_EVEN) and
  `roundAway(d, scale)` (HALF_AWAY), each a thin `Result[decimal]` over
  `decimal::quantize`.
- `abs`, `neg`, `sign`, `isZero`, `min`, `max`, `cmp`.
- `sum(&[]decimal): decimal` (traps on overflow like `+`).
- `allocate(total, parts: int, scale): Result[[]decimal]` splits evenly with
  the largest-remainder method, so the parts always sum exactly to `total`.
  `allocateWeighted(total, &[]decimal, scale)` does the same by weights.
- `formatGrouped(d, sep: byte): Result[string]` uses fixed display text with a
  thousands separator (ASCII only, no locale).
- Tests under `tests/stdlib/`, plus `stdlib.py` registration.

Money/currency (a `Money { amount, currency }` record with ISO 4217 minor
units) is milestone M4 and is **not** in this branch.

### D6 — database bridge (experimental packages, no compiler change)

- `SqlArg` gains `Decimal { v: decimal }`. PostgreSQL binds it as numeric
  OID 1700 with exact `decimal::format` text (falling back to `canonical` on
  `INEXACT`). SQLite binds it as TEXT with the same text. `DecimalText` stays
  for callers who already have text.
- A `${d}` hole of type `decimal` in a `sql` template produces
  `SqlArg.Decimal` instead of a compile error. Check where the template
  compiler maps hole types (`LANGUAGE.md` §8 template literals). If that mapping
  is compiler code, this item moves to D4's compiler work.
- Reading: a closed type `DecimalOrNull { Null {}; Value { v: decimal } }`
  plus `getDecimal(rows, r, c): Result[DecimalOrNull]` in both adapters,
  parsing with `decimal::parse` and setting dscale from the column (PostgreSQL
  typmod scale when present). NULL is never zero.
- Tests in `experimental/db/tests`, including the decimal oracles already there.

## 4. Versioning and compatibility

- `OBO_VERSION = "0.16"`, `SYM_FORMAT = 9`, `SYM_ABI = 1`. The format bumps
  because `.sym` gains decimal field types with 24-byte offsets, `priv24`,
  decimal constants and decimal signatures. A format-8 reader must reject
  these, and a format-9 reader accepts format-9 files only (the existing
  version rule). The ABI is unchanged: no existing layout or calling
  convention changes.
- Existing programs don't change behaviour; every change turns a former
  compile error into a feature.
- Update the hand-written `.sym` fixtures and goldens mechanically
  (`sym_test.py --update` only for the version line, with every other golden
  diff reviewed). Never weaken a test.

## 5. Gates (per stage on the branch; full set before merge)

Run from `obo/` and record the results in `FORK.md` under a new
"Decimal storage tier (0.16, format 9)" section, in the style of the 0.12 and
0.14 sections:

`make`, `run_fork.py`, `module_test.py`, `sym_test.py`, `sym_fuzz.py`,
`doc_check.py`, `homes_check.py`, `homes_edge.py`, `elf_test.py`, `io_test.py`,
`meta_test.py`, `heap_test.py`, `port_test.py`, `port_compare.py` (peak),
`imports.py`, `stdlib.py`, `tools.py --require-reference`, eza checks if the
reference is available, `mutate_fork.py`, `mutate_port.py`, `mutate_io.py`,
`mutate_decimal.py`, `own_fuzz.py`, `rt_dec_text_test`, and the fixed point
(stage2 == stage3).

New mutants (added to `mutate_decimal.py` or `mutate_fork.py` with catchers):
a field size of 8 instead of 24, stride 16 instead of 24, a two-word field copy,
a dropped zero-fill, a dropped `rt_append_dec` copy word, a wrong constant hex
word order, `priv24` read as `priv`, a capture snapshot taken by address, and
a dropped interface destination pointer.

## 6. Risks and stop conditions

- **Memory budget.** The 0.15 self-compile peak is 2,383,264 bytes against
  `PEAK_BUDGET = 2,500,000` (about 4.7% headroom, before cinterop v2). This
  tier adds compiler source. Measure after every stage. **If the peak would
  exceed 2,500,000, stop and report. Don't raise the budget, and don't trim
  comments to fit, without the user's decision.**
- **Single-pass codegen.** The 24-byte copies need two registers and must not
  clobber live registers. Reuse the existing helpers and don't open-code moves.
- **Assignment-across-call soundness.** Decimal helpers are runtime calls. A
  stage that lets a decimal store target outlive a freeing call is a soundness
  hole, which is a stop condition.
- **Review rounds.** A gate that can't be made green after two review rounds
  stops the branch, recorded in `FORK.md`.
- **Model capacity.** This is deep compiler work done by Haiku agents in small
  stages. If a stage fails review twice for capacity reasons rather than design
  reasons, report to the user instead of silently changing model.

## 7. Defaults chosen here (record in FORK.md)

1. Layout as in decimal-language-design §1.2: inline 24 bytes, 8-aligned,
   declaration order, stride 24.
2. All-zero bits are the zero value for new arrays and globals.
3. Decimal constants are literal or negated literal only, with no folding.
4. `priv24` is the opaque marker for a private decimal field in `.sym`.
5. `take`/`replace`/`append` behaviour on decimal elements mirrors `int`.
6. Captures snapshot 24 bytes by value. There are no decimal `mut`/`move`
   captures.
7. `string(decimal)` stays a compile error; use `decimal::format`.
8. The stdlib module is `decimals`, and Money stays M4.
9. One release, `0.16`/format 9/ABI 1, with one promotion after merge.
