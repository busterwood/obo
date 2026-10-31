# Decimal language integration design

**Date:** 2026-10-31
**Status:** APPROVED 2026-10-31, RELEASED as `0.12`/format 6/ABI 1, merged and promoted on main after rebasing on released `0.11` HTML main `34b798b`; the independent review returned REVISE, the fixes were approved and the review is closed. The promoted `seed/obo` is the same 0.12 image (643,073 bytes, sha256 `d98d6b5f…`), built once by the 0.11 seed `01fa9ed3…`. Text/format stdlib, database adapters, containers, globals/constants and captures are still later work.
**Baseline:** approved on `main` at `4a072cf5f5db8fe661ac65ff6db94ea59006e6ec`; integration rebased on the released `0.11` HTML-escaping main `34b798bc4c76cf41362d3b374c13b80e7b43f73b` (and earlier on heap-limit main `316963c`). Branch/worktree: `decimal-language`.
**Target:** released as `0.12`/`SYM_FORMAT=6`/`SYM_ABI=1`, merged and promoted on main after rebasing on the released 0.11 HTML main `34b798b`; the promoted seed is the same image.

## 0. Provenance

Based on approved research: `decimal-research-2026-10-28.md` (24-byte, 128-bit coefficient, 38 significant digits, `int16` exponent, `uint16` dscale, finite only), `decimal-arithmetic-research-2026-10-29.md` (exact/rounded separation, 256-bit intermediates, `divq`, hidden destination pointer), and `numeric-literals-decision-2026-10-29.md` (bare fraction/exponent is exact decimal; `f`/`F` is float; no `m`). The D1/D2 package in `obo/experimental/decimal/` is reviewed evidence, not a language ABI/API/grammar/default; D2: 32,719 checks, 0 failures.

The tested core statuses are exact: `DEC_OK=0`, `DEC_OVERFLOW=1`, `DEC_UNSUPPORTED_EXACT=2` (reserved), `DEC_INEXACT=3`, `DEC_INVALID=4`, `DEC_DIV_ZERO=5`, `DEC_UNSUPPORTED_RANGE=6`, `DEC_PRECISION_OVERFLOW=7`, `DEC_EXPONENT_RANGE=8`, `DEC_DISPLAY_RANGE=9`, `DEC_BUFFER_SMALL=10`; modes are `HALF_EVEN, HALF_AWAY, HALF_TOWARD_ZERO, TOWARD_ZERO, AWAY, FLOOR, CEILING, UNNECESSARY`.

Baseline: `0.10`, `SYM_FORMAT=6`, `SYM_ABI=1`, one-word values, raw binary64 floats in one word, `Result[T]` as an owned cell, Stage B captures design-only.

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
| Array | pointer-free kind with alignment 8 and stride 24; append/truncate/for copy 24 bytes |
| Global / constant / `.sym` | later tier: 24-byte zero-initialised storage or rodata; `Sym.value` would address a constant; `.sym` is `hex "<48 lowercase hex digits>"` |
| `Result[decimal]` | owned cell; `ok` payload at offset 0, descriptor size 24 |
| Interface / lambda | parameter/return allowed, not receiver; Stage B capture copies 24 bytes |

`decimal` is one built-in singleton: `isOwned`, `isRef` and `nonNull` are false; only decimal assigns to decimal (no implicit int→decimal); `?decimal` is rejected. Addressability is internal: expressions materialise in 24-byte stack temporaries and helpers read/write that address; there is no `&decimal`, decimal reference or handle.

### 1.3 Calls, locals and returns

**ABI: by-value slots for user functions, hidden destination pointer for decimal returns, pointer arguments only at the runtime boundary.**

- A decimal argument is passed by value as three words in a 24-byte stack slot at `16 + 24*(decimal params after it) + 8*(one-word params after it)`, 8-byte aligned. Strings, records, arrays and handles stay one word; the callee's slot is its own copy. Do **not** pass user decimal parameters by pointer: it aliases or forces a hidden copy; pointers are for runtime temporaries.
- A decimal-returning function uses a hidden destination pointer (internal out pointer): the caller allocates a 24-byte temporary and pushes its address **before** visible arguments, at `16 + total visible bytes`, so offsets do not change. `return expr` copies 24 bytes; with defers, a local holds it until after them.
- Runtime helpers use the C ABI and explicit pointers, with no 24-byte return: `void rt_dec_add(dec24 *dst, const dec24 *a, const dec24 *b)`. Scale/mode pack into one word.
- Rejected: `RAX:RDX:RCX` return, 32-byte pad, pointer parameters.

`Item` gains a decimal mode holding a stack/field/element address, not a register value. `load`, `store`, `materialise`, assignment and argument passing do 24-byte copies (three word moves or `memcpy`). Decimal locals are never register-homed. Comparisons call a helper and produce a one-word bool. No float ABI is reused: no XMM, binary64 reinterpretation or IEEE decimal128/DPD.

### 1.4 Comparison, equality, `Result`

Comparison and equality compare value only, so `1.0 == 1.00` is true. `dec_cmp(a,b)` returns -1/0/1, special-cases zero, and emits all six relational operators. `Result` cells compare by identity. `ok(d)`, `try` and `match` copy 24 bytes; `Result[decimal]` costs one heap cell, a direct return none.

### 1.5 Compiler budget

The compiler budget is `PEAK_BUDGET = 2,500,000` bytes. The released 0.12 scalar tier peaks at 2,194,160 bytes direct and 2,194,560 bytes in the port comparison. Later decimal tiers stay under the same ceiling; measure and record any change with a `FORK.md` reason before raising it.

## 2. Literals

**No decimal suffix.** A numeric token with a fraction or exponent and no `f`/`F` is `decimal`; `1` remains `int`; `f`/`F` remains `float`. `m`/`M` and `d`/`D` are not accepted.

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

Exact literals; never round. More than 38 significant digits, a nonzero value outside `int16`, or dscale outside `uint16` is a compile error. There is no underflow-to-zero: `1e-32769` errors. Zero keeps exponent 0, so a huge written exponent on zero is accepted if its dscale fits. Upper-exponent rescue: `1e32804` becomes coefficient `10^37`, exponent 32767; `1e32805` needs 39 digits and errors.

The compiler parses a literal into two 64-bit coefficient words with multiply-by-ten/add and carries, validating precision, exponent and dscale; it uses no runtime. Decimal **arithmetic** folding is not required in v1; a literal materialises directly as a 24-byte frame temporary with three immediate word stores (v1 does not use rodata for decimal literals), and later folding uses the same rules. `decimal(p,s)` is not syntax; precision/scale is boundary metadata (section 5).

## 3. Operations and operators

| Operation | Result | Policy |
|---|---|---|
| `a + b`, `a - b`, `a * b` | `decimal` | exact; trap on overflow (precision or exponent) |
| `a % b` | `decimal` | exact truncating remainder; sign of `a`; trap `DIV_ZERO` |
| `-a` | `decimal` | exact; preserves dscale; zero stays positive |
| `a / b`, `a /= b` | — | **compile error**: "decimal division needs an explicit scale and mode; use decimal::div(a,b,scale,mode)" |
| `== != < <= > >=` | `bool` | numeric, compares value only (ignores dscale) |
| `+= -= *= %=` | `decimal` | same as binary form |

`+ - *` are exact. A result that needs more than 38 significant digits or an exponent outside `int16` is a **fatal trap**, like integer divide-by-zero: no hidden rounding, no `Result`. Diagnostics are `obo: decimal result is not exactly representable` and `obo: decimal overflow`; exit code 10, not `SIGFPE`. `%` and `%=` are exact truncating remainder: `q=trunc(a/b)`, `r=a-q*b` with the sign of `a`; a zero divisor traps `obo: decimal division by zero`. A folded constant that would trap is a **compile error**.

Division exists as an explicit Result-returning function, with no default scale or mode:

```obo
decimal::div(a, b, scale, mode) -> Result[decimal]
decimal::quantize(a, scale, mode) -> Result[decimal]
```

`scale` is the target fractional display scale (`0..32768`) and arithmetic target for both `div` and `quantize`; there is no negative-scale API in v1. The core's strict quantize limit above 32768 is documented and retained. `mode` is one of the eight tested modes. `UNNECESSARY` returns `INEXACT` if a nonzero digit would be discarded. Division by zero is `DIV_ZERO`; there is no infinity or NaN. `decimal::div(1.0, 3.0, 6, HALF_AWAY)` gives `0.333333`.

Display propagation is presentation, not arithmetic: add/sub `max`; multiply `sum` capped by the 38-digit/integer-part rule in §4.1 (value unchanged); remainder `max`; division/quantize the explicit target; unary minus unchanged; comparisons value only. `1.20 + 1.2` is `2.40`; `1.20 * 1.2` is `1.440`; `decimal::div(1.00, 2.0, 2, HALF_AWAY)` is `0.50`.

Decimal exact arithmetic traps use exit 10 with diagnostic ids only: 40 not exactly representable (`DEC_INEXACT`), 41 overflow (`DEC_OVERFLOW`), 42 division by zero (`DEC_DIV_ZERO`), 43 invalid (`DEC_INVALID`), 44 unsupported range, 45 precision overflow, 46 exponent range, 47 display range, 48 buffer too small and 49 other. `Result`-returning APIs (`decimal::div`, `quantize`, `toInt`) carry the tested **core status** in the err code: 1 `DEC_OVERFLOW`, 3 `DEC_INEXACT`, 4 `DEC_INVALID`, 5 `DEC_DIV_ZERO`, 6 `DEC_UNSUPPORTED_RANGE`; 7/8/9 are text/binding statuses not produced by these three APIs (the text APIs `parse`/`format`/`canonical`/`check` produce 7/8/9 and 10), 2 is reserved, and 10 is text-allocation only. Scale/mode are validated before the core: constant invalid values are compile errors, runtime invalid scale returns 6 and invalid mode 4. `ok`/`err` follow `Result[T]`.

## 4. Conversions and formatting

- Conversions are **explicit in both directions**. `decimal(i)` for `int`/`byte` is exact, dscale 0, but there is no implicit int/byte→decimal in any context; write `decimal(1)` or `1.0`. Mixed decimal/int arithmetic is a compile error unless converted.
- `int(d)` is a compile error pointing to `decimal::toInt(d, mode) -> Result[int]`, which rounds once with the named mode, returns `INEXACT` for `UNNECESSARY`, and `OVERFLOW` outside `int`; it never silently truncates or wraps. `floor`, `ceil`, `trunc`, `round` are named stdlib operations.
- **float→decimal is forbidden**: no literal, implicit or explicit conversion, because binary64 is inexact and no hidden rounding is allowed. decimal→float is omitted in v1; if later offered it must be explicit, documented lossy `decimal::toFloat(d)` and never a money path. No implicit decimal/float mixing.
- `decimal::parse(s) -> Result[decimal]` accepts the strict public text grammar:
  optional sign, mandatory integer digits, optional `.` with at least one
  fraction digit, optional exponent with at least one digit; integer-only and
  signed database cells are accepted with dscale 0. The whole input is ASCII;
  whitespace, grouping, underscores, NUL, `NaN`/`Infinity`, `.5` and `1.` are
  rejected. It preserves dscale and returns `PRECISION`, `DISPLAY_RANGE` or
  `EXPONENT` on the tested precedence (precision, then display range, then
  exponent), and `INVALID` on syntax. The source literal grammar remains
  section 2; this is its database-cell extension.
- `decimal::format(d) -> Result[string]` is fixed notation with exactly dscale fractional digits, invariant C locale, no grouping and no exponent; it returns `INEXACT` if capped dscale would drop nonzero fractional digits. `decimal::canonical(d)` returns exact minimal text for keys/serialisation. `print`/`println`/`eprint`/`eprintln` use `format`: `1.2300` prints `1.2300`, `0.00` keeps its dscale, dscale 0 has no point; on `INEXACT` they print canonical exact text, never round. `formatScientific` is separate and float `e±NN` is not reused.
- No binary float for money; database paths never use `double`.

### 4.1 Display-scale cap and database practice

`dscale` is metadata, not a numeric quantum. A multiply can produce more fractional places than a 38-digit value can display with its integer part. The value is unchanged; dscale is capped:

```text
intd = max(0, digits(result_coefficient) + result_exponent)   // digits left of the point
dscale = min(s1 + s2, max(0, 38 - intd))
```

This uses the exact result value, never an operand, so multiplication display scale is order-independent. It preserves the integer part and 38 displayed digits without rounding. `format` returns `INEXACT` if significant fractional digits exceed the capped scale; only numeric overflow (>38 digits or exponent outside `int16`) traps.

SQL Server `decimal(p,s)` (p<=38) reduces `s` when multiplication `p=p1+p2+1` exceeds 38, preserving integer digits. PostgreSQL `numeric` is arbitrary precision and stores dscale; Oracle `NUMBER` has 38 digits and column scale -84..127; MySQL `DECIMAL` caps 65/30. obo has no p/s in the type, so it derives this value-local cap within 38 digits.

## 5. Database integration (future; not implemented)

No adapter, helper process, DB connection or native pointer ABI is in this work.

- Metadata is separate (`DecimalColumn { precision, scale, nullable, dbtype, oid, typmod }`); SQL Server/ASE p/s, Oracle scale and PostgreSQL OID 1700/typmod are schema metadata, not dscale.
- Boundaries use `decimal::check(value,p,s) -> Result[decimal]`: exact fit means `value*10^s` integer and `abs(value*10^s)<10^p`, never rounded. Rounding is explicit `quantize`, then `check`; no silent default. This is metadata validation, not a language `decimal(p,s)`.
- Fetch sets dscale from PostgreSQL dscale or column metadata; `dec_bind_*` is the model, not public API.
- **NULL is distinct from zero and error.** Since `?decimal` is not representable, adapters return a tagged `DecimalOrNull` wrapper or a `Result` null variant; never encode NULL as zero. `decimal(p,s)` constraints and nullable-column APIs are later.
- C interop is explicit pointer/length, little-endian, ABI-versioned; not frozen and not a `libpq` shortcut. No float, no helper process.

## 6. Phased implementation plan

**M1 — scalar slice:** type kind/`tDecimal`; literal scanner/parser; 24-byte locals, parameters and hidden-pointer returns; exact `+ - * %`, unary minus, comparisons; trap diagnostics; parse/format; explicit int conversion.

**M2 — usable tier:** record fields, arrays, globals, constants, `Result[decimal]`, `.sym` constants, format/ABI bump, interface methods, lambdas/Stage B integration, `div`/`quantize`, boundary `check` and `lib/decimal.obx`. M1+M2 is the minimum viable decimal.

**M3 — database adapters, separate approval:** SQL Server TDS first, then Oracle, ASE and PostgreSQL, with byte-format goldens, exact text, metadata and NULL wrappers. No database process is started here.

**M4 — later:** arbitrary `numeric`, `decimal(p,s)` constraints, Money/currency, batch/SIMD only if measured.

Gates: `doc_check`, `run_fork`, `port_compare`, self-host/fixed point, `mutate_fork`/`mutate_port` (decimal mutants: tie, overflow, sign, stride 24, const hex, dscale), `sym_fuzz`, `own_fuzz`, oracle vectors, ABI/`.sym` goldens, an `nm` check for libgcc division, and the memory budget. Risks: hidden-pointer/defers, layout, `.sym`, scale policy, division, interface/lambda, concurrent Stage B.

## 7. Final decisions and rejected alternatives

Implementation landed after Stage B `0.11` as `0.12`/format 6/ABI 1; later decimal tiers reserve no version here. Rejected: 16-byte/96-bit `.NET`; scale-only 128-bit; 256-bit storage; heap/string decimal; IEEE decimal128/DPD; float money; `m`/`d`; all-`Result` operators; default division; pointer parameters; `?decimal` sentinel; immediate `decimal(p,s)`; helper process; frozen C wire.

| Decision | Final choice | Remaining alternative |
|---|---|---|
| Literal marker | none: bare fraction/exponent is decimal, `f/F` float | `m`, `d` (rejected) |
| Trap codes | exit 10; ids 40–49 are trap ids only; Result errors carry core statuses 1/3/4/5/6 (7/8/9 reserved, 2/10 internal) | shared errno/trap codes |
| Arithmetic failure | `+ - * %` trap; `div` returns `Result` | all operators `Result` |
| Division/scale | no default; `div(a,b,scale,mode)`; `/` compile error; 0..32768 | fixed/negative scale |
| Remainder | `%`/`%=` truncating, sign of dividend, `DIV_ZERO` trap | floor/Euclidean default |
| Display propagation | add/sub `max`; multiply capped sum; div target; rem `max` | fatal display trap; unbounded sum |
| `decimal(p,s)` | deferred; boundary `check(value,p,s)` | add syntax now |
| Operators vs stdlib | core ops; div/round/conversions in `lib/decimal.obx`; `parse`/`format`/`canonical`/`check`/`exponent`/`dscale` are compiler-known text builtins in the P0 tier (a stdlib wrapper may follow) | all stdlib/methods |
| Conversions | explicit both ways; `toInt(d,mode) -> Result`; no truncation/wrap; float→decimal forbidden | implicit int→decimal |
| ABI / width | 24-byte three-word, align 8, stride 24, hidden out pointer; 32-byte rejected | `RAX:RDX:RCX`; 32-byte (rejected) |
| `?decimal` / NULL | no `?decimal`; tagged DB wrapper | sentinel; `Result` as NULL |

Open engineering choices: adapter order and final diagnostic wording; the 40–49 assignment above is implemented and reviewed on this branch.

## 8. Migration and compatibility

Today `0.10`/format 6 rejects bare `1.5`; `1.5f` is float. Decimal changes that error into a value, so no valid float program changes. `f/F` and one-word raw binary64 `.sym` stay; decimal never reuses float's slot/XMM/format-6 grammar.

The released integration is on branch `decimal-language`, rebased on the released 0.11 HTML main `34b798b`: `OBO_VERSION="0.12"`, `SYM_FORMAT=6`, `SYM_ABI=1`. No format 7 or ABI 2 is introduced. Old `.sym` files must be rebuilt because the compiler version line changed. It is merged and promoted on main; the promoted seed is the same image. No helper process/DB/native pointer ABI is added.
