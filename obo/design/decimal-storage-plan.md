# Decimal storage tier: plan

**Date:** 2026-11-01
**Status:** revision 2, after the independent review (REVISE, 9 blocking findings, folded in below). Implementation is **blocked on the memory budget** (§6).
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

### D1 — record and closed-variant fields (with the format bump)

The `SYM_FORMAT` 8 → 9 and `OBO_VERSION` 0.16 bump lands here, with the first
`.sym` change (`obo.obo:121-123`; test mirrors `tests/sym_test.py:29-31`,
`tests/mutate_fork.py:796-798, 1602-1603`).

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
  is laid out with the same `sizeOf`. Closed types never consume (the consuming
  path at `obo.obo:12137` is for `Result` only), and a non-consuming closed
  `match` narrows without binding a name (`LANGUAGE.md:1931-1936`). So the
  closed-variant work is the offset fix plus narrowed field reads (`v.f` inside
  the arm). The 24-byte consuming copy already exists for `Result[decimal]`
  (`consumeArm`, 12092).
- Through handles (`h.f` where `h: ^T`) and through `&T` / `mut &T` borrows.
- `.sym` field lists: write and read `decimal` as a field type. A **private**
  decimal field can't be a one-word opaque `priv` slot. Add a 24-byte opaque
  marker (`priv24`) so the layout survives without leaking the type
  (writer `appFields` `obo.obo:14314-14340`). The reader `symFieldList`
  (`obo.obo:14996-15039`) uses `8*len(rec.fields)` as the offset at 15018 and
  15034. Change it to a running byte offset via `sizeOf`, with placeholder type
  `tDecimal` for `priv24`. A full build compares the `.sym` against the live
  type (`symCompareFields` 15042-15058, offsets included), so both sides must
  agree. Importers build from `.sym` alone only under `--check`.
- Tests: read/write/compound/literal, nested records, variant payloads with
  `match`, handles, borrows, a cross-module record with public and private
  decimal fields (the importer builds from `.sym` only), heap leak/peak checks
  under `OBO_CHECK=1`, and the assign-across-call refusal.

### D2 — `[]decimal`

- Runtime: a new array kind for 24-byte pointer-free elements (pick an unused
  kind number, documented beside the `kind:` comment at `runtime.c:500`).
  Every site that switches on kind must learn it:
  - `rt_free` (`runtime.c:396-406`) treats any kind other than 1-6 as a
    descriptor pointer. A new kind 7 would be dereferenced. Pick the number
    and the dispatch so it can't be mistaken for a descriptor.
  - `empty_data` / `is_empty_data` (344-357) need an empty block for the new
    kind. Otherwise `new []decimal(0)` dies, and the first append frees rodata.
  - `rt_alloc_array` (502): accept the kind with `esize = 24`.
  - `tpl_clone` (1520-1523) maps unknown kinds to 1 with esize 8. That's a heap
    overflow for esize 24, so map the new kind explicitly.
  - `rt_append_dec(Array*, const u64 *src)` copies 24 bytes. It needs a new
    fixed slot in `runtime/entry.S` with a `check_` stub, and an `RT_` constant
    after `obo.obo:2100`.
  - `rt_truncate` already works by `esize`.
- Compiler: `newExpr` (`obo.obo:9726-9741`) has no decimal branch and yields
  kind 1. Add one.
- `new []decimal(n)` zero-fills; all-zero bits are decimal `0` with dscale 0
  (design §1.1), so no initialiser loop is needed.
- Index read/write: address = data + 8 + i*24. `resolve()` (`obo.obo:3307-3313`)
  sets scale = width = 8 after bounds-checking the raw index, and every decimal
  helper goes through `resolve` (3497-3550). Scale **inside `resolve`, after
  the bounds check**, into a **fresh register** (`lea r, [i+i*2]`, then scale 8).
  Never scale in place: `asReg` (3369) can return a local's home register.
- `append(xs, d)`, `truncate`, `len`, and `for i in 0..len(xs)`. `for d in xs`
  (`obo.obo:11886-11893`) copies one word with `mem(ra,ri,8,8)`, so it needs a
  scaled three-word copy into the 24-byte loop local.
- `take`/`replace` on a decimal element behave as they do for an `int` element:
  where `int` is refused, decimal is refused with the same diagnostic shape.
- `[]decimal` as a record field, a parameter (`&[]decimal`, `mut &[]decimal`),
  a return and in `Result[[]decimal]`.
- `.sym`: remove the `14975` refusal and accept `[]decimal` in signatures and
  field lists. The reader's dummy globals use an 8-byte stride (check mode
  only). Fix that in D3.
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
  folded`), because decimal folding is not in v1. `constDecl`
  (`obo.obo:13499-13508`) calls `expression()`, which emits frame stores for a
  decimal literal (`decimalLiteral` 5598-5603). Parse the `T_DECIMAL` token
  text and an optional unary minus directly instead, with no code emitted.
  `Sym.value` is one int (`obo.obo:1599-1624`), so store the three words in new
  `Sym` fields, or in one side table. A use materialises the value with the same
  three immediate word stores as a literal (`emitDecWords`) into a frame
  temporary. A decimal constant item is **never** mode `"const"`, because store
  and `copyDecimal` assume `mem`.
- `.sym` constant: `const NAME decimal hex "<48 lowercase hex>"`. The 48 digits
  are the **24 value bytes in memory order** (w0 bytes 0-7 little-endian, then
  w1, then w2), the same convention `symHexString` (`obo.obo:14798`) decodes.
  Don't use `appHex16`, which is numeric MSB-first: write the bytes yourself in
  `appConst` (14442-14463). The reader (`symConstDecl` 15555-15600) gains a
  decimal branch. It rejects uppercase (`symHexString` accepts it, so check
  first), a length other than 48, a coefficient above `10^38-1`, a zero with
  sign 1, a sign other than 0/1, and nonzero w2 bits 40-63. Each is a
  symbol-file error.
- Tests: global read/write/compound, global in a record literal, pub const
  across modules from `.sym` only, a malformed `.sym` constant (`sym_fuzz`
  corpus), and a negative zero constant (prints `0`, positive).

### D4a — methods and interface members

- Interface calls load the vtable from `[RSP+8*n]` (`obo.obo:7278`) after the
  receiver pair is pushed (`pushIfaceReceiver` 6944). With decimal arguments,
  the offset must be the **explicit-argument byte count** + 8, not `8*n`.
- `recordParamFree` (`obo.obo:5003`) maps a parameter offset to an index with
  `/8`. A 24-byte parameter shifts that and corrupts the `pfree` mask, which is
  a free-summary soundness bug. Map by parameter index, not by offset.

### D4b — function values, lambdas and captures (write the layout first)

Before any code, the D4b agent writes the frame layout into this note for
review. Function values push the return pointer, then the cell, then the
arguments (`obo.obo:7029-7050`), so the cell sits where a decimal-returning
callee reads its destination pointer (12300). "Identical to direct calls" can't
hold. Define where the destination pointer and cell live for a call through a
function value. Change the lambda environment read `16 + 8*(n-1)` (12932) to
a byte offset. Captures are 8 bytes each (10236-10250, with `load()` refusing
decimal at 10245) and are loaded per word (12940-12955). A decimal capture
needs a 24-byte slot.

The original D4 bullets below apply across D4a and D4b.

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

### D6 — database bridge

**Review correction:** a `${d}` decimal hole already works. `sqlTemplateHole`
(`obo.obo:8436-8464`, decimal branch 8453-8456) maps it to tag 6, `DecimalText`,
and the runtime formats it (`runtime.c:1617-1636`). So the write path is
lossless today. A typed `SqlArg.Decimal { v: decimal }` variant would need
compiler work in `buildTemplateTypes` (3095-3106), `ensureTemplateDescs`
(3143, descriptor size 24), the hole branch, and a runtime tag. That only buys
adapters a binary path, and no adapter uses binary numerics. **Default:** keep
`DecimalText` for writes. D6 is the read side only, which needs no compiler
change:
- Reading: a closed type `DecimalOrNull { Null {}; Value { v: decimal } }`
  plus `getDecimal(rows, r, c): Result[DecimalOrNull]` in both adapters,
  parsing with `decimal::parse` and setting dscale from the column (PostgreSQL
  typmod scale when present). NULL is never zero.
- Tests in `experimental/db/tests`, including the decimal oracles already there.

### Ordering

D1 (with the format bump), D2, D3, D4a, D4b. Then D5 (needs D2's `[]decimal`)
and D6 (needs D1's variant fields). D5 and D6 are not compiler work and may run
in parallel with each other after D2.

### Files every compiler stage also updates

`tests/errors/decimal.obx` (it pins the old refusals, so turn each refusal into
a positive test as it lifts), `tests/port_compare.py:286`, `LANGUAGE.md`
105-223, 1702, 1747-1751 and 2692-2701, `design/decimal-language-design.md`
status lines, and `FORK.md`. `rt_dec_text_test` is built by
`tests/heap_test.py:78-80`, not the Makefile.

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

- **Memory budget: already exceeded at the baseline.** The 0.15 self-compile
  peak was 2,383,264 bytes. Cinterop v2 stages a-d added about 134 KB, which
  `FORK.md` doesn't record. Main `6f83880` now measures **2,519,264 bytes**
  (`OBO_STATS=1 ./obo $PWD/src/obo.obo -o … --rt $PWD`, as in
  `tests/heap_test.py:238-250`), 19,264 over `PEAK_BUDGET = 2,500,000`. So
  `heap_test.py` already fails on main. **No compiler stage starts until the
  user decides** whether to raise the budget, reduce memory first, or both.
  After that, measure after every stage and stop again at the new cap.
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
   captures. The D4b layout gets its own review first.
10. Decimal SQL writes stay `DecimalText`. There's no `SqlArg.Decimal` variant
    in this tier.
7. `string(decimal)` stays a compile error; use `decimal::format`.
8. The stdlib module is `decimals`, and Money stays M4.
9. One release, `0.16`/format 9/ABI 1, with one promotion after merge. The
   ABI number is unchanged, but list the additions: the runtime slot for
   `rt_append_dec` and the new array kind.
