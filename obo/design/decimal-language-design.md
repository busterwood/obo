# Decimal language integration design

**Date:** 2026-10-31
**Status:** Proposal for independent review. Not approved, not implemented. No compiler, runtime, seed, `.sym` format or database code is changed.
**Baseline:** `main` at `4a072cf5f5db8fe661ac65ff6db94ea59006e6ec` ("Merge independently reviewed experimental decimal D2 arithmetic"). Branch/worktree: `decimal-language-design`.
**Target:** after Stage B (`0.11` is next planned), version-neutral. This document does not reserve `0.12`, a format number or an ABI number; those are chosen at implementation/promotion.

## 0. Provenance

Based on the approved research: `decimal-research-2026-10-28.md` (24-byte, 128-bit coefficient, 38 significant digits, `int16` exponent, `uint16` dscale, finite only), `decimal-arithmetic-research-2026-10-29.md` (exact/rounded separation, 256-bit intermediates, `divq`, hidden destination pointer), and `numeric-literals-decision-2026-10-29.md` (bare fraction/exponent is exact decimal; `f`/`F` is float; no `m`). The D1/D2 package in `obo/experimental/decimal/` is reviewed evidence, not a language ABI, public API, literal grammar or hidden default; D2 reports 32,719 freestanding checks, 0 failures.

The tested core statuses are exact: `DEC_OK=0`, `DEC_OVERFLOW=1`, `DEC_UNSUPPORTED_EXACT=2` (reserved), `DEC_INEXACT=3`, `DEC_INVALID=4`, `DEC_DIV_ZERO=5`, `DEC_UNSUPPORTED_RANGE=6`, `DEC_PRECISION_OVERFLOW=7`, `DEC_EXPONENT_RANGE=8`, `DEC_DISPLAY_RANGE=9`, `DEC_BUFFER_SMALL=10`; modes are `HALF_EVEN, HALF_AWAY, HALF_TOWARD_ZERO, TOWARD_ZERO, AWAY, FLOOR, CEILING, UNNECESSARY`.

The compiler baseline is `0.10`, `SYM_FORMAT=6`, `SYM_ABI=1`, one-word values, raw binary64 floats in one word, `Result[T]` as an owned cell, and Stage B captures design-only.

## 1. Value representation and ABI

### 1.1 Layout

**24 bytes is three 8-byte words, not four.** The canonical value is three words; a fourth zero word is not part of it.

| Word | Meaning |
|---|---|
| `w0` | coefficient bits 0..63 (little-endian limb) |
| `w1` | coefficient bits 64..127 |
| `w2` | bits 0..15 `int16` exponent; 16..31 `uint16` dscale; 32..39 sign; 40..63 zero |

`value = (-1)^sign * coefficient * 10^exponent`, coefficient `<= 10^38 - 1`; zero is canonical positive (0, sign 0, exponent 0) with any dscale. All-zero bits are valid zero. `sizeof=24`, align 8, no padding; words are little-endian on x86-64. Example: `123.4500` is coefficient `12345`, exponent `-2`, dscale `4`, words `0x3039`, `0`, `0x0004fffe`.

### 1.2 Placement

| Place | Representation |
|---|---|
| Local / parameter | 24-byte 8-aligned stack slot; parameter is a by-value caller slot |
| Return | hidden caller destination pointer; callee copies 24 bytes |
| Record field | 24-byte inline field; offsets use a size/alignment function |
| Array | pointer-free kind with `esize=24`; append/truncate/for copy 24 bytes |
| Global / constant | 24-byte zero-initialised storage or rodata; `Sym.value` addresses a constant |
| `.sym` | `const X: decimal = hex "<48 lowercase hex digits>"` |
| `Result[decimal]` | owned cell; `ok` payload at offset 0, descriptor size 24 |
| `?decimal` | **not supported**; zero is valid, so no sentinel means nil |
| Interface / lambda | parameter/return allowed, not receiver; Stage B capture copies 24 bytes |

`decimal` is one built-in singleton: `isOwned`, `isRef` and `nonNull` are false; only decimal assigns to decimal (no implicit int→decimal); `?decimal` is rejected. Addressability is internal: decimal expressions materialise in 24-byte stack temporaries and helpers read/write that address. There is no `&decimal`, decimal reference parameter or decimal handle.

### 1.3 Calls, locals and returns

**Recommendation: by-value slots for user functions, hidden destination pointer for decimal returns, pointer arguments only at the runtime boundary.**

- A decimal parameter is 24 bytes at `16 + 24*(decimal params after it) + 8*(one-word params after it)`, 8-byte aligned. Strings, records, arrays and handles stay one word; the callee's slot is its own copy.
- Do **not** pass user decimal parameters by pointer: it aliases or forces a hidden copy. Pointers are for runtime temporaries.
- A decimal-returning function receives a hidden destination pointer. The caller allocates a 24-byte temporary and pushes its address **before** the visible arguments; it is at `16 + total visible bytes` in the callee, so visible offsets do not change. `return expr` copies 24 bytes there; with defers, a 24-byte local holds it until after them.
- Runtime helpers use the C ABI and explicit pointers, with no 24-byte return: `void rt_dec_add(dec24 *dst, const dec24 *a, const dec24 *b)`. Scale and mode pack into one word to stay within four arguments.
- Rejected: `RAX:RDX:RCX` return; 32-byte pad; pointer parameters with copy-on-entry.

`Item` gains a decimal mode holding a stack/field/element address, not a register value. `load`, `store`, `materialise`, assignment and argument passing do 24-byte copies (three word moves or `memcpy`). Decimal locals are never register-homed. Comparisons call a helper and produce a one-word bool. No float ABI is reused: no XMM, binary64 reinterpretation or IEEE decimal128/DPD.

### 1.4 Comparison, equality, `Result`

Comparison and equality compare value only, so `1.0 == 1.00` is true. `dec_cmp(a,b)` returns -1/0/1, special-cases zero, and emits all six relational operators. `Result` cells still compare by identity. `ok(d)`, `try` and `match` copy 24 bytes; a `Result[decimal]` success costs one heap cell, a direct decimal return none.

### 1.5 Compiler budget

The Stage B budget is `PEAK_BUDGET = 1,351,000` bytes for the port compiling itself; the recent peak is around 1.2 MB. The compiler need not use decimal; its parser and metadata use existing constructs, so incremental peak should be modest. A `2,500,000`-byte ceiling is a plausible planning upper bound if the compiler grows, not a target or reservation; measure in M1 and raise the budget only with a recorded `FORK.md` reason.

## 2. Literals

**Recommendation: no decimal suffix.** A numeric token with a fraction or exponent and no `f`/`F` is `decimal`; `1` remains `int`; `f`/`F` remains `float`. `m`/`M` and `d`/`D` are not accepted.

```text
decimal-literal := digits [ '.' digits ] [ ('e'|'E') ['+'|'-'] digits ]
```

At least one of fraction or exponent is required. A leading/trailing dot (`.5`, `1.`) is rejected in v1; write `0.5`/`1.0`, avoiding `1..3` and field-access ambiguity. A leading sign is unary minus; underscores are between digits.

| Literal | Coefficient | Exponent | dscale | Display |
|---|---:|---:|---:|---|
| `1.50` | 15 | -1 | 2 | `1.50` |
| `1.5e3` | 15 | 2 | 0 | `1500` |
| `1.2300e-2` | 123 | -4 | 6 | `0.012300` |
| `0.00` | 0 | 0 | 2 | `0.00` |

The coefficient is normalised by removing trailing zeros; `dscale = max(0, fractional_digits - explicit_exponent)`. Written precision is preserved for display (`1.50` is not silently `1.5`); numeric equality ignores dscale.

Exact literals; never round. More than 38 significant digits is a compile error (`decimal literal has more than 38 significant digits`); a nonzero value outside `int16` is `decimal literal exponent is out of range`; dscale outside `uint16` is `decimal literal display scale is out of range`. There is no underflow-to-zero: `1e-32769` errors. Zero keeps exponent 0, so a huge written exponent on zero is accepted if its dscale fits. Upper-exponent rescue follows the tested parser: `1e32804` becomes coefficient `10^37`, exponent 32767; `1e32805` needs 39 digits and errors.

The compiler parses a literal into two 64-bit coefficient words with multiply-by-ten/add and carries, validating precision, exponent and dscale. It uses no runtime or decimal arithmetic. Decimal **arithmetic** folding is not required in v1; literals and unary minus materialise as rodata, and later folding must use the same exactness rules. `decimal(p,s)` is not syntax; precision/scale is boundary metadata (section 5).

## 3. Operations and operators

| Operation | Result | Policy |
|---|---|---|
| `a + b`, `a - b`, `a * b` | `decimal` | exact; trap on overflow (precision or exponent) |
| `a % b` | `decimal` | exact truncating remainder; sign of `a`; trap `DIV_ZERO` |
| `-a` | `decimal` | exact; preserves dscale; zero stays positive |
| `a / b`, `a /= b` | — | **compile error**: "decimal division needs an explicit scale and mode; use decimal::div(a,b,scale,mode)" |
| `== != < <= > >=` | `bool` | numeric, compares value only (ignores dscale) |
| `+= -= *= %=` | `decimal` | same as binary form |

`+ - *` are exact. A result that needs more than 38 significant digits or an exponent outside `int16` is a **fatal trap**, like integer divide-by-zero: no hidden rounding, no `Result`. Diagnostics are `obo: decimal result is not exactly representable` and `obo: decimal overflow`; exit code 10, not `SIGFPE`. `%` traps `obo: decimal division by zero`. A folded constant that would trap is a **compile error**.

Division exists as an explicit Result-returning function, with no default scale or mode:

```obo
decimal::div(a, b, scale, mode) -> Result[decimal]
decimal::quantize(a, scale, mode) -> Result[decimal]
```

`scale` is the target fractional display scale (`0..32768`) and arithmetic target; negative-scale rounding is not exposed. `mode` is one of the eight tested modes. `UNNECESSARY` returns `INEXACT` if a nonzero digit would be discarded. Division by zero is `DIV_ZERO`; there is no infinity or NaN. `decimal::div(1.0, 3.0, 6, HALF_AWAY)` gives `0.333333`.

Display propagation is presentation, not arithmetic: add/sub `max`; multiply the exact **sum** of scales (over `65535` traps with `DEC_DISPLAY`, never a silent cap); remainder `max`; division/quantize the explicit target; unary minus unchanged; comparisons value only. `1.20 + 1.2` is `2.40`; `1.20 * 1.2` is `1.440`; `decimal::div(1.00, 2.0, 2, HALF_AWAY)` is `0.50`.

The language surface uses named constants in `lib/decimal.obx` (proposal: values 40–49, distinct from I/O errno and trap codes). Mapping: `DEC_INEXACT=3` → `DEC_INEXACT` (needs >38 digits or `UNNECESSARY` discarded digits); `DEC_OVERFLOW=1` → `DEC_OVERFLOW`; `DEC_DIV_ZERO=5` → `DEC_DIV_ZERO`; `DEC_INVALID=4` → `DEC_INVALID`; `DEC_UNSUPPORTED_RANGE=6` → `DEC_RANGE`; `DEC_PRECISION_OVERFLOW=7` → `DEC_PRECISION`; `DEC_EXPONENT_RANGE=8` → `DEC_EXPONENT`; `DEC_DISPLAY_RANGE=9` → `DEC_DISPLAY`. `DEC_UNSUPPORTED_EXACT=2` and `DEC_BUFFER_SMALL=10` stay internal. `ok` carries the decimal, `err` the message/code, consistent with `Result[T]`.

## 4. Conversions and formatting

- Conversions are **explicit in both directions**. `decimal(i)` for `int`/`byte` is exact, dscale 0, but there is no implicit int/byte→decimal in assignment, argument, return or mixed arithmetic; write `decimal(1)` or `1.0`. Mixed decimal/int arithmetic is a compile error unless converted.
- `int(d)` is a compile error pointing to `decimal::toInt(d, mode) -> Result[int]`, which rounds once with the named mode, returns `INEXACT` for `UNNECESSARY`, and `OVERFLOW` outside `int`. `floor`, `ceil`, `trunc`, `round` are named stdlib operations with their own result types.
- **float→decimal is forbidden**: no literal, implicit or explicit conversion, because binary64 cannot represent decimal values exactly and no hidden rounding is allowed. decimal→float is omitted in v1; if later offered it must be an explicit, documented lossy `decimal::toFloat(d)` and never a money path. No implicit decimal/float mixing.
- `decimal::parse(s) -> Result[decimal]` accepts the section 2 grammar with full consumption and rejects whitespace, grouping, underscores, `NaN`, `Infinity` and a bare sign. It preserves dscale and returns `PRECISION`, `EXPONENT` or `DISPLAY` on the tested precedence, and `INVALID` on syntax.
- `decimal::format(d) -> string` is fixed notation with exactly dscale fractional digits, invariant C locale, no grouping and no exponent; `decimal::canonical(d)` strips trailing zeros for keys/serialisation. `print`/`println`/`eprint`/`eprintln` use `format`: `1.2300` prints `1.2300`, `0.00` keeps its dscale, dscale 0 has no point; `formatScientific` is separate and float `e±NN` is not reused.
- No binary float for money; database paths never use `double`.

## 5. Database integration (future; not implemented)

No adapter, helper process, database connection or native pointer ABI is part of this work.

- Metadata is separate: `DecimalColumn { precision, scale, nullable, dbtype, oid, typmod }`; SQL Server/ASE p/s, Oracle `NUMBER` scale (including negative) and PostgreSQL OID 1700/typmod are schema metadata, not the value's dscale.
- Boundaries validate with `decimal::check(value, p, s) -> Result[decimal]`: exact fit means `value * 10^s` is an integer and `abs(value * 10^s) < 10^p`; it never rounds. Rounding to a column is `quantize` with an explicit mode, then `check`; there is no silent default. This is metadata validation, not a language `decimal(p,s)` type.
- Fetch sets dscale from PostgreSQL's stored dscale or column metadata. The private `dec_bind_*` contract is the model, not a public API.
- **NULL is distinct from zero and error.** Since `?decimal` is not representable, adapters return a tagged `DecimalOrNull` wrapper or a `Result` null variant; never encode NULL as zero. `decimal(p,s)` constraints and a nullable-column API are later.
- C interop is explicit pointer/length, little-endian, ABI-versioned. It is **not frozen now** and is not a `libpq` shortcut; conversion is at the adapter boundary. No float, no helper process.

## 6. Phased implementation plan

**M1 — scalar slice:** type kind/`tDecimal`; literal scanner/parser; 24-byte locals, parameters and hidden-pointer returns; exact `+ - * %`, unary minus, comparisons; trap diagnostics; parse/format; explicit int conversion.

**M2 — usable tier:** record fields, arrays, globals, constants, `Result[decimal]`, `.sym` constants, format/ABI bump, interface methods, lambdas/Stage B integration, `div`/`quantize`, boundary `check` and `lib/decimal.obx`. M1+M2 is the minimum viable decimal.

**M3 — database adapters, separate approval:** SQL Server TDS first, then Oracle, ASE and PostgreSQL, with byte-format goldens, exact text, metadata and NULL wrappers. No database process is started here.

**M4 — later:** arbitrary `numeric`, `decimal(p,s)` constraints, Money/currency, batch/SIMD only if measured.

Gates: `doc_check`, `run_fork`, `port_compare`, self-hosting/fixed point, `mutate_fork`/`mutate_port` with decimal mutants (tie direction, overflow, sign, array stride 24, constant hex, dscale propagation), `sym_fuzz`, `own_fuzz`, oracle vectors, ABI/`.sym` goldens (`Result[decimal]`, field offsets, array stride, constants), an `nm` check for `__udivti3`/`__udivmodti4`/`__umodti3`, and the memory budget. Risks: hidden-pointer returns through defers, parameter/field/array layout, `.sym` bootstrap, display/scale policy, division rounding, interface/lambda integration and concurrent Stage B.

## 7. Final decisions and rejected alternatives

Implementation lands after Stage B `0.11`; no version is reserved. Rejected: 16-byte/96-bit `.NET` decimal; scale-only 128-bit; 256-bit storage; heap/string-only decimal; IEEE decimal128/DPD; float as money; `m`/`d` suffixes; all operators `Result`; default division/rounding; pointer parameters; `?decimal` sentinel; `decimal(p,s)` now; helper process; freezing the C wire.

| Decision | Final choice | Remaining alternative |
|---|---|---|
| Literal marker | none: bare fraction/exponent is decimal, `f/F` float | `m`, `d` (rejected) |
| Arithmetic failure | `+ - * %` trap; `div` returns `Result` | all operators `Result` (rejected) |
| Division default | none; `div(a,b,scale,mode)`; `/` compile error | fixed scale; exact-only `/` (rejected) |
| Display propagation | add/sub `max`; multiply **sum**; div target; rem `max` | normalise always; silent cap at 65535 (not chosen) |
| `decimal(p,s)` | deferred; `check(value,p,s)` at boundaries | add syntax now (rejected) |
| Operators vs stdlib | `+ - * %`, unary and comparisons in the language; div/round/parse/format/conversions in `lib/decimal.obx` | all in stdlib; all as methods |
| Conversions | explicit both ways; int→decimal `decimal(i)`; decimal→int `decimal::toInt(d,mode) -> Result`; float→decimal forbidden | implicit int→decimal (rejected) |
| Return ABI / width | hidden destination pointer; 24-byte three-word value | `RAX:RDX:RCX`; 32-byte pad |
| `?decimal` / NULL | no `?decimal`; tagged DB wrapper | sentinel; `Result` as NULL |

Open engineering choices: error-code values, 24/32-byte ABI, adapter order, diagnostic wording.

## 8. Migration and compatibility

Today `0.10`/format 6 rejects a bare `1.5`; `1.5f` is float. Adding decimal changes that error into a value, so no valid float program changes meaning. The `f/F` spelling and one-word raw binary64 `.sym` constant stay. Decimal never reuses float's slot, XMM class or format-6 constant grammar.

The implementation lands after Stage B (`0.11`), not concurrently. It must not reserve `0.12`, format 7 or ABI 2 in advance; version, `SYM_FORMAT` and `SYM_ABI` are chosen at promotion after Stage B's calling convention and capture ABI are known. Old `.sym` files must be rebuilt once layout or constant grammar changes. This design is version-neutral, assumes no Stage B commit, seed promotion, merge or push, and adds no helper process, database connection or native pointer ABI.
