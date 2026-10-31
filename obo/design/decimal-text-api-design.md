# Decimal text API design (P0)

**Date:** 2026-10-31
**Status:** DESIGN ONLY, revision 2 for focused re-review. This pass changes no
source, runtime, seed, grammar, version, `.sym` format or ABI. No version is
reserved. It is the P0 prerequisite for the approved decimal database boundary
and for later SQL templates. Revision 2 resolves the review of `0b9d98b`:
private/public oracle split, corrected `1000e-16384` raw-word note, `DecimalText`
bind-size gate, `check` code-10 allocation wording, heap/free-list failure
tests, unnormalised and zero low-exponent vectors, exact-message assertions,
extra check boundaries, `alloc_raw` diagnostic preservation, DB error-mapping
alignment, module/mutation coverage, and trap-id wording.
**Baseline:** `main` at `2364add3a7ad0a02ec9412b6834a36a510d45f32` (0.12 decimal
scalar tier, merged approved decimal database boundary). Worktree
`/home/chris/.local/share/opencode/worktree/fa36f3/decimal-text-design`.
**Target:** implemented after field visibility (`0.13`/`SYM_FORMAT=7`), as the
next free version, likely `0.14`/`SYM_FORMAT=7`/`SYM_ABI=1`. If another feature
takes 0.14 first, this design takes the next free version. The text API itself
does not change `.sym` grammar; it inherits format 7 and keeps ABI 1.

## 0. Summary of decisions

1. Six compiler-known builtins, all in the existing `decimal::` namespace:
   `parse`, `format`, `canonical`, `check`, `exponent`, `dscale`.
2. `parse` is a **strict public grammar**: optional sign, mandatory integer
   digits, optional fraction with at least one digit, optional exponent with at
   least one digit. It accepts integer-only and signed database cells, rejects
   `.5`, `1.`, empty text, whitespace, grouping, underscores, NUL, non-ASCII,
   `NaN` and `Infinity`, and normalises a written `-0` to positive zero. It
   preserves written display scale.
3. `format` is fixed display text with exactly `dscale` fractional digits. It
   never rounds; it returns `DEC_INEXACT` (3) instead of dropping a nonzero
   digit.
4. `canonical` is exact minimal text ignoring `dscale`. It uses fixed notation
   while the fixed magnitude text is at most 128 bytes and exact scientific
   notation above that. It is value-unique and round-trips through `parse`.
5. `check(value,p,s)` is the exact-fit metadata gate: `value*10^s` must be an
   integer and `abs(value*10^s) < 10^p`, with `p 1..1000` and
   `s -1000..1000`. It never rounds and returns the value unchanged.
6. `exponent` returns the **normalised** exponent (stored `int16` plus the
   coefficient's trailing-zero count); `dscale` returns the stored `uint16`
   display scale. Both are total for a valid language decimal.
7. The text APIs return core statuses as `Result` codes; they never use the
   arithmetic diagnostic ids 40–49 (which are labels, not process exit codes).
   Exact messages are part of the test contract.
8. Strings are owned by the `Result[string]` cell. `string(decimal)` stays a
   compile error; there is no float path and no pointer ABI.
9. Runtime slots append at 73–78 after the existing decimal block ending at 72.
   No existing slot, `Result` layout or 24-byte decimal layout changes.
10. The database boundary can call these functions directly; `DecimalText { v:
    []byte }` remains the v1 transport for `sql`. Decimal record/variant fields
    and `?decimal` remain blocked.

## 1. Scope, non-goals and prerequisites

**In scope**

- the public in-memory text surface: `decimal::parse`, `format`, `canonical`,
  `check`, `exponent`, `dscale`;
- exact display/canonical byte rules, error codes, messages and limits;
- runtime helpers and compiler builtin registration;
- tests, gates, mutants and the interaction contract with the approved database
  design.

**Out of scope**

- database adapters, libpq/libsqlite changes, SQL text, `DecimalText` lowering;
- decimal record fields, arrays, globals, constants, captures, methods, closures
  (`obo/LANGUAGE.md:2431-2436`; `obo/src/obo.obo:5448`, `:5382`,
  `:11483-11484`);
- `?decimal` and any nullable decimal representation
  (`decimal-language-design.md:42`);
- a language `decimal(p,s)` type or implicit coercion
  (`decimal-language-design.md:84`);
- binary numeric wire formats, float interop, `decimal::toFloat`, COPY, arrays,
  intervals, money/currency;
- starting a database, creating schema/data or changing server configuration.

**Prerequisite:** field visibility lands first as `0.13`/format 7. This is a
packaging order because the text feature ships on top of the field-visibility
`.sym` format; the text implementation does not otherwise depend on private
fields. Decimal fields and variants are **not** unblocked by field visibility;
they remain `M2` container work.

The approved decimal design already promises `parse`/`format`/`canonical` and
`check` (`decimal-language-design.md:114-132`, `:134-142`), but only
`div`/`quantize`/`toInt`/`fromInt` are exposed today
(`obo/LANGUAGE.md:97-100`; `obo/src/obo.obo:3522-3559`). This design supplies the
missing text surface.

## 2. Value and text model

### 2.1 Representation recap

A decimal is 24 bytes, three words (`obo/runtime/decimal24_core.inc:73-81`):

| Word | Meaning |
|---|---|
| `w0` | coefficient bits 0..63 |
| `w1` | coefficient bits 64..127 |
| `w2` | bits 0..15 `int16` exponent; 16..31 `uint16` dscale; 32..39 sign; 40..63 zero |

`value = (-1)^sign * coefficient * 10^exponent`, coefficient `<= 10^38 - 1`.
Zero is canonical positive (`coefficient 0`, sign 0, exponent 0) with any
`dscale`. There is no NaN, infinity, negative zero or hidden rounding
(`decimal-language-design.md:26-28`). `dscale` is presentation metadata, not a
numeric quantum.

### 2.2 Public text grammar

```text
decimal-text := sign? int-digits ( '.' frac-digits )? exponent?
sign         := '+' | '-'
int-digits   := digit+
frac-digits  := digit+
exponent     := ('e'|'E') sign? digit+
digit        := '0' | '1' | ... | '9'
```

Rules, all normative:

- The **whole input** is consumed; there is no leading or trailing whitespace.
- The integer part is mandatory and has at least one digit. A leading point
  (`.5`) and a trailing point (`1.`) are `DEC_INVALID` (4), matching the literal
  grammar (`decimal-language-design.md:67-71`) and the database design
  (`decimal-db-boundary-design.md:93-120`).
- An optional leading `+` or `-` is part of this **text** grammar. A source
  literal `1` is still `int`, and a source sign is still unary; a database cell
  may be `1`, `+1` or `-1` with `dscale = 0`.
- Integer-only text is accepted with `dscale = 0`. This is the database-cell
  extension, not a change to the literal token grammar.
- `e`/`E` is the only letter accepted, and only as the exponent marker. The
  exponent may have `+`/`-` and may have leading zeros (`1e+003` = `1000`).
- ASCII only. Any byte `0x00` or `> 0x7f` is `DEC_INVALID` (4). No embedded NUL,
  no UTF-8 digits, no non-breaking space.
- No grouping separators, underscores, `0x`, `NaN`, `Inf`, `Infinity`, bare
  sign or empty string.
- Leading zeros in the integer part are accepted: `0001.2300` is `1.2300`.
  Fractional trailing zeros are significant for `dscale` and are preserved.
- The coefficient is normalised by stripping trailing zeros. `dscale` is
  computed from the **written** fraction and explicit exponent:
  `dscale = max(0, frac_digits - explicit_exponent)`; an absent exponent is 0.
  Examples: `1.2300` dscale 4; `1.2300e3` dscale 1; `1.2300e-2` dscale 6;
  `1e3` dscale 0; `0.00` dscale 2.
- Zero has canonical exponent 0 regardless of written exponent, and keeps the
  computed `dscale`. `-0`, `+0`, `0`, `-0.00` all become positive zero; only
  the display scale differs (0, 0, 0, 2). No `-0` text is ever emitted.
- Upper-exponent rescue follows the approved core: if the normalised exponent
  exceeds 32767, append zeros to the coefficient (up to 38 significant digits)
  and store exponent 32767. `1e32768` is coefficient `10`, exponent 32767;
  `1e32804` is coefficient `10^37`, exponent 32767; `1e32805` needs 39 digits
  and is `DEC_EXPONENT_RANGE` (8).
- Lower exponent has no rescue: after normalisation, exponent `< -32768` is
  `DEC_EXPONENT_RANGE` (8).
- More than 38 significant digits is `DEC_PRECISION_OVERFLOW` (7). Trailing
  zeros after the 38th significant digit are not significant and do not
  overflow; a nonzero 39th significant digit does.
- `dscale > 65535` is `DEC_DISPLAY_RANGE` (9).
- Semantic precedence after the whole text is syntactically valid is
  **PRECISION (7)**, then **DISPLAY_RANGE (9)**, then **EXPONENT (8)**. This is
  the core's tested order (`decimal24_core.inc:1884-1891`) and is visible in
  `1e-70000` (9, not 8) and `1e40000` (8, not 9).

### 2.3 Limits

| Quantity | Limit |
|---|---|
| coefficient | `<= 10^38 - 1`, at most 38 significant digits |
| stored exponent | `int16`, `-32768..32767` |
| normalised exponent (`exponent`) | `-32768..32804` |
| display scale (`dscale`) | `uint16`, `0..65535` |
| `check` precision `p` | `1..1000` |
| `check` scale `s` | `-1000..1000` |
| maximum exact text from a formatter | `98,342` bytes (`DEC_TEXT_MAX_OUTPUT`, `decimal24_core.inc:1916`) |
| fixed canonical threshold | 128 bytes of magnitude text excluding sign (`DEC_TEXT_CANON_FIXED_MAX`, `:1915`) |

The 98,342-byte formatter bound is exactly the worst case: 32,805 integer
positions (exponent 32767 plus 38 coefficient digits) + one point + 65,535
fractional digits + one sign. It is the same bound the database adapters use on
the bind side (`decimal-db-boundary-design.md:159-178`).

`parse` has no input-length cap of its own. A database cell longer than 98,342
bytes (for example 100,000 leading zeros followed by `1`) parses if the value
fits the 38-digit/exponent/dscale rules. The database adapter's 16 MiB buffer is
the external bound (`decimal-db-boundary-design.md:573-577`).

## 3. Public API contracts

### 3.1 Signatures

```obo
decimal::parse(s: string) -> Result[decimal]
decimal::format(d: decimal) -> Result[string]
decimal::canonical(d: decimal) -> Result[string]
decimal::check(value: decimal, p: int, s: int) -> Result[decimal]
decimal::exponent(d: decimal) -> int
decimal::dscale(d: decimal) -> int
```

All are free functions, not methods. `decimal` method parameters and returns are
rejected in v1 (`obo/src/obo.obo:11483-11484`). The names are compiler-known;
there is no `lib/decimal.obx` requirement for P0, although a later stdlib wrapper
may add helpers. `parse` borrows its string; the returned decimal is a 24-byte
value. `format`/`canonical` return a fresh owned string in the `ok` arm.
`check` returns a copy of the input value. `exponent`/`dscale` return one-word
`int` values.

### 3.2 `decimal::parse`

**Contract**

1. Validate the section 2.2 grammar over the whole string, byte for byte.
2. Reject any byte `0x00` or `> 0x7f` as `DEC_INVALID` (4).
3. On syntax failure return `err(message, 4)`; no value is produced.
4. Parse the same bytes with the exact core (`dec_parse`,
   `decimal24_core.inc:1970`) after the strict grammar check. The core's private
   grammar accepts `.5` and `1.`; the public wrapper must reject them before
   calling it. Do not change `dec_parse`'s private behaviour.
5. On success return `ok(decimal)` with the normalised coefficient, stored
   exponent and computed display scale.
6. Preserve the written display scale; do not canonicalise the value's
   `dscale`.
7. Full consumption; no trimming, no locale, no float, no rounding.

**Result codes**

| Code | Meaning | When |
|---:|---|---|
| 0 | success | valid text |
| 4 | invalid text | syntax, empty, NUL, non-ASCII, `.5`, `1.` |
| 7 | precision | more than 38 significant digits |
| 9 | display range | `dscale > 65535` |
| 8 | exponent range | nonzero exponent outside `int16` after upper rescue |
| 1 | overflow | defensive only; invalid internal coefficient/exponent |
| 6 | unsupported range | defensive only; not produced by valid text |
| 10 | buffer/allocation | the `Result[decimal]` ok cell cannot be allocated |

`5` (`DIV_ZERO`) never occurs. `2` (`UNSUPPORTED_EXACT`) is reserved and never
occurs. The database boundary maps parse `4/7/8/9` to `1006/1007/1008/1009`
and parse `10` to adapter allocation code `3`; see section 9.

**Precedence examples**

| Input | Code | Why |
|---|---:|---|
| `1e-70000` | 9 | `dscale` 70,000 > 65,535; display range wins over exponent |
| `1e-65536` | 9 | `dscale` 65,536 is the first display-range failure |
| `1e-65535` | 8 | display scale fits, exponent -65,535 is below `int16` |
| `1e-32768` | 0 | minimum stored exponent |
| `1e-32769` | 8 | below `int16` |
| `1e40000` | 8 | upper rescue cannot fit 38 digits |
| `1e32804` | 0 | upper-rescue boundary |
| `1e32805` | 8 | needs 39 digits |
| 39 significant digits | 7 | precision wins over any exponent/display issue |
| `123...39digits.` | 4 | syntax wins over precision |
| `0e-70000` | 9 | zero keeps `dscale`, so display range still applies |
| `0e70000` | 0 | zero exponent 0, `dscale` 0 |

### 3.3 `decimal::format`

**Contract**

- Fixed notation only: optional `-` for a nonzero negative value, at least one
  integer digit, no leading zeros except a single `0`, no exponent, no grouping,
  no `+`, ASCII `.`.
- Exactly `dscale` fractional digits. If `dscale == 0`, there is no point. If
  `dscale > 0`, the point is always written, even for zero: `0.00`, `0.0000`.
- Zero never prints `-0`: `format(-0.00)` is `0.00`.
- No rounding and no truncation. Let `c` be the coefficient, `e` the stored
  exponent, `t` the coefficient's trailing-zero count. For `c != 0`, the
  display form is exact iff `e + dscale + t >= 0`. Otherwise the display would
  drop a nonzero digit and the result is `DEC_INEXACT` (3), with no partial
  string.
- Maximum output length: `int_digits + (dscale ? 1 + dscale : 0) + sign`,
  where `int_digits = max(1, digits(c) + e)`. If this exceeds 98,342, return
  `DEC_DISPLAY_RANGE` (9). For a valid 24-byte value the bound is exact, so this
  is a defensive path.
- Locale invariant: the bytes are always `0`–`9`, `.` and `-`; no `LC_NUMERIC`,
  no thousands separator, no localized minus, no UTF-8.
- Success value is a fresh owned string. The caller may move it through `ok`,
  `match`, `try` or assignment; the `Result[string]` descriptor owns it
  (`obo/src/obo.obo:2937-2947`).

**Result codes**

| Code | Meaning | When |
|---:|---|---|
| 0 | success | exact display text |
| 3 | inexact | capped/insufficient `dscale` would drop a nonzero digit |
| 9 | display range | required text exceeds 98,342 bytes (defensive for valid values) |
| 10 | buffer/allocation | text payload or ok cell cannot be allocated |
| 4 | invalid value | defensive; malformed 24-byte backing bits |

The database adapter uses `format` first and calls `canonical` only on code 3
(`decimal-db-boundary-design.md:600-655`). It must not treat code 3 as a value
error and must not round.

### 3.4 `decimal::canonical`

**Contract**

- Exact minimal text ignoring `dscale`. It is value-unique: every decimal has
  exactly one canonical string.
- Zero is `0`, never `0.0`, never `-0`.
- Nonzero: let the coefficient have `len` digits (most significant first,
  including trailing zeros), `sd` significant digits after stripping trailing
  zeros, stored exponent `e`, and `adj = e + len - 1` (the power of ten of the
  leading significant digit).
- Fixed notation is used when the **fixed magnitude text excluding sign** is at
  most 128 bytes. With `pos = len + e`:

  ```text
  pos <= 0        plain_len = 2 + (-pos) + sd
  pos >= sd       plain_len = pos
  otherwise       plain_len = sd + 1
  ```

  If `plain_len <= 128`, render fixed: optional `-`, then the digits with the
  point inserted, no trailing fraction zeros and no trailing point. A negative
  fixed value can therefore be 129 bytes including its sign; this is the
  documented core threshold (`decimal24_core.inc:1893-1899`).
- If `plain_len > 128`, render exact scientific:

  ```text
  [-] d0 [ '.' d1 ... d(sd-1) ] 'e' [-] adj
  ```

  Lowercase `e`; no `+`; no leading zeros in `adj`; no trailing fraction zeros;
  no point when `sd == 1`. Examples: `1.23e-200`, `1e-32768`, `1e32804`,
  `1.8e-16382`. `adj` is in `-32768..32804`.
- Threshold boundary examples: `1e-126` is fixed (128 bytes), `1e-127` is
  scientific; `1e127` is fixed (128 bytes), `1e128` is scientific.
  `1e-40` is fixed (`0.` + 39 zeros + `1`, 42 bytes), not scientific.
- `parse(canonical(v)) == v` for every valid `v`; canonical loses `dscale` by
  design. The text has no exponent ambiguity for database round-trip: the
  canonical string parses back to exactly the same value, and PostgreSQL
  `numeric_in` accepts lowercase `e`.
- Maximum canonical length is 129 bytes including sign for fixed, or at most
  47 bytes including sign for scientific (`sd <= 38`, `adj` at most 5 digits
  plus sign). It never approaches 98,342.

**Result codes**

| Code | Meaning | When |
|---:|---|---|
| 0 | success | exact canonical text |
| 9 | range | required text exceeds 98,342 bytes (defensive; not reachable for a valid value) |
| 10 | buffer/allocation | text payload or ok cell cannot be allocated |
| 4 | invalid value | defensive; malformed backing bits |

Canonical never returns `INEXACT`: it has no display cap and never rounds.

### 3.5 `decimal::check`

**Contract**

- `p` is the declared precision, `s` the declared scale (which may be
  negative). Valid ranges: `p 1..1000`, `s -1000..1000`. These are PostgreSQL's
  `NUMERIC_MAX_PRECISION` and `NUMERIC_MIN/MAX_SCALE`
  (`decimal-db-boundary-design.md:166-170`).
- `check` is a metadata gate, not a conversion. It returns the value unchanged
  (24-byte copy, same `dscale`, sign, coefficient and exponent) on success.
- It ignores `dscale` entirely: `1.2300` and `1.23` fit the same `(p,s)`.
- It never rounds and never materialises `10^p`; the **predicate** is
  allocation-free, integer-only. The API is not allocation-free: a successful
  `Result[decimal]` needs an ok cell, so code 10 is possible on allocation
  failure (section 6.4).
- Exact-fit predicate. For a valid value `v = (-1)^sign * c * 10^e`, let
  `n = digits(c)` (0 for zero), `t = trailing_zeros(c)`, and `k = e + s`.
  Then:

  ```text
  c == 0                                  -> fit
  k < 0 and t < -k                        -> DEC_INEXACT (3)
  otherwise, n + k > p                    -> DEC_PRECISION_OVERFLOW (7)
  otherwise                               -> fit
  ```

  `n + k` is the number of digits of `abs(value * 10^s)` when that scaled value
  is an integer. `n + k <= p` is exactly `abs(value * 10^s) < 10^p`, because a
  `p`-digit integer is below `10^p` and a `p+1`-digit integer is not.
- The predicate is invariant under coefficient normalisation. If
  `c = c' * 10^t` and `e = e' - t`, then `digits(c) + k = digits(c') + (e' + s)`
  and the trailing-zero test is the same. An arithmetic value stored as
  `1800 * 10^-3` and a parsed `1.8` therefore get the same result.
- Negative scale works: `s = -3` requires the value to be a multiple of 1000
  and the scaled integer to have at most `p` digits. `99000` fits `(2,-3)`;
  `1500` does not (would require rounding).
- Error precedence: invalid value `4` first (defensive), then invalid `p`/`s`
  range `6`, then `3`, then `7`.
- Unlike `div`/`quantize`, constant `p`/`s` are **not** compile-time validated.
  Metadata is data: a constant out-of-range pair returns `err(...,6)` at
  runtime, so the database adapter can map it to `1012` and tests can exercise
  the error path in language. Argument types are still checked at compile time.

**Result codes**

| Code | Meaning | When |
|---:|---|---|
| 0 | success | exact fit |
| 3 | inexact | `value*10^s` is not an integer; rounding would be required |
| 7 | precision | scaled integer needs more than `p` digits |
| 6 | unsupported range | `p` outside `1..1000` or `s` outside `-1000..1000` |
| 4 | invalid value | defensive; malformed backing bits |
| 1 | overflow | defensive only; not produced by this predicate |
| 10 | buffer/allocation | the `Result[decimal]` ok cell cannot be allocated |

The database boundary maps `3/7/6` to `1010/1011/1012`
(`decimal-db-boundary-design.md:325-371`, `:418-455`).

### 3.6 `decimal::exponent` and `decimal::dscale`

```obo
decimal::exponent(d: decimal) -> int
decimal::dscale(d: decimal) -> int
```

**`exponent`** returns the normalised exponent:

- zero returns `0` (the canonical zero exponent); `dscale` is irrelevant;
- otherwise `stored_int16_exponent + trailing_zero_count(coefficient)`, in
  `-32768..32804`;
- it is not the raw `w2` exponent. Parse already normalises its coefficient:
  `1000e-16384` is stored as coefficient `1`, exponent `-16381`, so a raw-word
  read passes that case and it is **not** a raw-word guard. Arithmetic can keep
  an unnormalised coefficient: `(1.20 * 1.5) * 1e-16382` may store
  `1800 * 10^-16385`, whose raw word is `-16385` while the normalised exponent
  is `-16383`. A raw-word accessor would falsely reject that value at
  PostgreSQL's `-16383` boundary (`decimal-db-boundary-design.md:373-416`).

**`dscale`** returns the stored display scale, a direct `uint16` field read
from `w2`, `0..65535`. It is not normalised and not derived from the exponent.

Both are total for a valid language decimal: there is no `Result`, no `err` arm
and no value-dependent failure. A malformed 24-byte backing value is an
internal invariant violation, not a language value; the runtime helper traps
with `DEC_INVALID` (diagnostic id 43; the runtime exits with status 10) like
`rt_dec_cmp`
(`decimal24_core.inc:2456-2459`). This keeps the PostgreSQL bind pre-check a
simple integer comparison. A `Result[int]` would force an unwrap on every bind
and add a cell allocation for no recoverable state.

Why the database boundary needs both:

```obo
// PostgreSQL pre-check, before encoding text (conceptual)
if value != 0.0 && decimal::exponent(value) < -16383 {
    return err("value is not representable in PostgreSQL numeric", 1013)
}
if decimal::dscale(value) > 16383 {
    // display scale cannot be sent; canonical fallback preserves the value
}
```

`dscale > 16383` is not an error by itself: the value may still be exact in
canonical form. `exponent < -16383` is a hard PostgreSQL range error. The
distinction is only possible with a normalised exponent.

## 4. Status and error mapping

### 4.1 Core statuses to public Result codes

The reviewed core statuses are (`decimal24_core.inc:87-101`):

| Core | Name | Public text Result | Notes |
|---:|---|---:|---|
| 0 | `DEC_OK` | success | not an err arm |
| 1 | `DEC_OVERFLOW` | 1 | parse/check defensive only |
| 2 | `DEC_UNSUPPORTED_EXACT` | never | reserved; do not expose |
| 3 | `DEC_INEXACT` | 3 | `format`, `check` |
| 4 | `DEC_INVALID` | 4 | `parse`; defensive in formatters/`check` |
| 5 | `DEC_DIV_ZERO` | never | `div`/`%` only |
| 6 | `DEC_UNSUPPORTED_RANGE` | 6 for `check`; 9 for formatter output bound | metadata range vs text range |
| 7 | `DEC_PRECISION_OVERFLOW` | 7 | `parse`, `check` |
| 8 | `DEC_EXPONENT_RANGE` | 8 | `parse` |
| 9 | `DEC_DISPLAY_RANGE` | 9 | `parse`, formatter output bound |
| 10 | `DEC_BUFFER_SMALL` | 10 | formatter text allocation, or any `Result` ok-cell allocation |

The one deliberate remap is formatter output bound: the core length query
returns `DEC_UNSUPPORTED_RANGE` (6) when `required > 98,342`; the public
`format`/`canonical` return `9`, because the cause is a display/text bound and
the database adapter's approved mapping expects `9` (or `10`) for that path
(`decimal-db-boundary-design.md:600-655`). `check` keeps `6` for bad metadata.

### 4.2 Per-API codes

| API | Success | Err codes that can be produced |
|---|---|---|
| `parse` | `ok(decimal)` | 4, 7, 8, 9, 10; 1/6 defensive |
| `format` | `ok(string)` | 3, 9, 10; 4 defensive |
| `canonical` | `ok(string)` | 4, 9, 10 |
| `check` | `ok(decimal)` | 3, 6, 7, 10; 1/4 defensive |
| `exponent` | `int` | none; diagnostic id 43 (process exit status 10) on malformed backing bits |
| `dscale` | `int` | none; diagnostic id 43 (process exit status 10) on malformed backing bits |

`parse` cannot produce 3/6; `format` cannot produce 7/8; `canonical` cannot
produce 3/6/7/8; `check` cannot produce 8/9, and produces 10 only when the
`Result[decimal]` ok cell cannot be allocated. Tests assert the produced set,
not merely the message.

### 4.3 Exact messages

Result messages have no trailing newline and start with `obo: `, matching the
existing decimal `Result` cells (`decimal24_core.inc:2369-2382`,
`:2528-2535`). The message text is part of the test contract; do not rename a
code or message independently.

| API | Code | Exact message |
|---|---:|---|
| `parse` | 4 | `obo: invalid decimal text` |
| `parse` | 7 | `obo: decimal text has more than 38 significant digits` |
| `parse` | 8 | `obo: decimal exponent is outside int16` |
| `parse` | 9 | `obo: decimal display scale is outside uint16` |
| `parse` | 10 | `obo: decimal result could not be allocated` |
| `parse` | 1 | `obo: decimal overflow` |
| `parse` | 6 | `obo: decimal value is outside the supported range` |
| `format` | 3 | `obo: decimal display scale would drop nonzero digits` |
| `format` | 9 | `obo: decimal text is outside the supported range` |
| `format` | 10 | `obo: decimal result could not be allocated` |
| `format` | 4 | `obo: invalid decimal value` |
| `canonical` | 4 | `obo: invalid decimal value` |
| `canonical` | 9 | `obo: decimal text is outside the supported range` |
| `canonical` | 10 | `obo: decimal result could not be allocated` |
| `check` | 3 | `obo: value does not fit the declared scale without rounding` |
| `check` | 6 | `obo: decimal precision/scale is outside the supported range` |
| `check` | 7 | `obo: value exceeds the declared precision` |
| `check` | 10 | `obo: decimal result could not be allocated` |
| `check` | 1 | `obo: decimal overflow` |
| `check` | 4 | `obo: invalid decimal value` |

The database adapter may replace these with its own `1006`–`1012` messages; the
codes are the interface, the exact text is the standalone-API contract.

### 4.4 Traps versus Result

- The 40–49 ids are **diagnostic identifiers**, not process exit statuses
  (`obo/LANGUAGE.md:2240-2245`). `die_code` always exits with status 10; the id
  names the failure. They are never returned in a `Result` err arm and never
  used by the text APIs. The text APIs return an err cell with a code from the
  table above.
- `+ - * %` keep their existing exit-status-10 traps
  (`decimal-language-design.md:97`, `:110`). `print`/`println` keep their
  existing display-then-canonical behaviour and do not expose `INEXACT` as an
  error.
- `exponent`/`dscale` have no `Result`; their only failure is the internal
  malformed-value diagnostic 43 (process exit status 10), which a valid language
  value cannot reach.
- `parse`/`format`/`canonical`/`check` never call `rt_dec_trap` for a value
  error. They build a `Result` err cell. A runtime heap exhaustion that prevents
  even the small err cell is the existing global `obx: out of memory` fatal
  path; no API can return a `Result` without a cell. The text payload allocation
  itself is recoverable and returns 10.

## 5. Display and scale rules

### 5.1 Rules

- `dscale` is written fraction metadata. It is set by parsing/literals and
  propagated by arithmetic; it is not the numeric quantum.
- `format` preserves `dscale` exactly and never adds/removes a significant
  digit. It may pad zeros; it may drop only zeros.
- `canonical` ignores `dscale` and strips all coefficient trailing zeros. It is
  the value key, not the display form.
- `check` ignores `dscale`; precision/scale are numeric metadata.
- Comparisons (`==`, `!=`, `<`, ...) compare value only; `1.0 == 1.00` is true
  (`decimal-language-design.md:57`). Text equality is not value equality.
- `print`/`println` use display text; on display `INEXACT` they print canonical
  exact text, never a rounded value (`decimal-language-design.md:118`).

### 5.2 Value examples

| Text | Coefficient / exp | `dscale` | `format` | `canonical` |
|---|---|---:|---|---|
| `1.2300` | `123 * 10^-2` | 4 | `1.2300` | `1.23` |
| `0.001` | `1 * 10^-3` | 3 | `0.001` | `0.001` |
| `1e3` | `1 * 10^3` | 0 | `1000` | `1000` |
| `1.2300e3` | `123 * 10^1` | 1 | `1230.0` | `1230` |
| `0.00` | zero | 2 | `0.00` | `0` |
| `-0.00` | zero | 2 | `0.00` | `0` |
| `1e-40` | `1 * 10^-40` | 40 | `0.` + 39 zeros + `1` | same fixed (42 bytes) |
| `1e-126` | `1 * 10^-126` | 126 | `0.` + 125 zeros + `1` | same fixed (128 bytes) |
| `1e-127` | `1 * 10^-127` | 127 | `0.` + 126 zeros + `1` | `1e-127` |
| `1e127` | `1 * 10^127` | 0 | `1` + 127 zeros | same fixed (128 bytes) |
| `1e128` | `1 * 10^128` | 0 | `1` + 128 zeros | `1e128` |
| 38 nines | `10^38 - 1` | 0 | 38 nines | 38 nines |
| `1e-16383` | `1 * 10^-16383` | 16383 | `0.` + 16382 zeros + `1` | `1e-16383` |
| `1.0e-16383` | `1 * 10^-16383` | 16384 | `0.` + 16382 zeros + `10` | `1e-16383` |
| `1000e-16384` | `1 * 10^-16381` | 16384 | `0.` + 16380 zeros + `1000` | `1e-16381` |
| `1e32804` | `10^37 * 10^32767` | 0 | `1` + 32804 zeros | `1e32804` |
| `1e-32768` | `1 * 10^-32768` | 32768 | `0.` + 32767 zeros + `1` | `1e-32768` |
| `1e-20 * 1e-20` | `1 * 10^-40` | 38 (cap) | **err 3** | `0.` + 39 zeros + `1` |

The last row distinguishes a parsed `1e-40` (dscale 40, display exact) from an
arithmetic result (dscale capped to 38 by the multiply rule,
`decimal-language-design.md:123-130`; `decimal24_core.inc:2406-2416`). The value
is identical; only presentation differs. `format` returns `INEXACT` for the
arithmetic result and the adapter falls back to canonical. This is the approved
database behaviour (`decimal-db-boundary-design.md:1007-1013`).

`1000e-16384` is parse-normalised to coefficient `1`, exponent `-16381`; it is
**not** a raw-word guard. The raw-word guard is the arithmetic fixture
`(1.20 * 1.5) * 1e-16382`, which may store `1800 * 10^-16385` (raw `-16385`,
normalised `-16383`).

### 5.3 Check examples

| Value | `p` | `s` | Result | Why |
|---|---:|---:|---|---|
| `1.2345` | 10 | 4 | ok | scaled 12345, 5 digits |
| `1.23456` | 10 | 4 | 3 | `1.23456*10^4` is not an integer |
| `123456.7890` | 10 | 4 | ok | scaled 1234567890, 10 digits |
| `1234567.890` | 10 | 4 | 7 | scaled 12345678900, 11 digits |
| 38 nines | 38 | 0 | ok | maximum 38-digit coefficient |
| 38 nines | 37 | 0 | 7 | 38 digits > 37 |
| `0` | 1 | 0 | ok | zero always fits valid metadata |
| `0.001` | 1 | 3 | ok | scaled 1 |
| `0.01` | 1 | 3 | 7 | scaled 10, 2 digits > 1 |
| `99000` | 2 | -3 | ok | scaled 99 |
| `15000` | 2 | -3 | ok | scaled 15 |
| `1500` | 2 | -3 | 3 | 1500 is not a multiple of 1000 |
| `99999` | 2 | -3 | 3 | would round to 100000 |
| `1.5e999` | 1000 | -1000 | 3 | would round to 0 at scale -1000 |
| `-1.2300` | 10 | 4 | ok | sign ignored by `abs` |
| `1.2300` | 10 | 2 | ok | dscale ignored; value 1.23 |
| `1.2300` | 10 | 1 | 3 | would round 1.23 to 1.2 |
| `1000` | 2 | -3 | ok | scaled 1 |
| `1000` | 1 | -3 | ok | scaled 1 |
| `0` | 0 | 0 | 6 | invalid metadata is checked even for zero |
| `0` | 1001 | 0 | 6 | precision above range |
| `0` | 1 | 1001 | 6 | scale above range |

Unnormalised coefficient fixtures:
`1800 * 10^-3` (value 1.8) fits `(2,1)` just like parsed `1.8`; `1800 * 10^-3`
does not fit `(2,0)` (not an integer) and `1800 * 10^-2` (value 18) fits
`(2,0)` because its trailing zeros make `value*10^0` integral. The tests create
these values through arithmetic where possible and through the C helper for
guaranteed stored forms.

## 6. Runtime implementation plan

### 6.1 Slots

Append after the existing decimal block `60..72` (`obo/src/obo.obo:2070-2082`;
`obo/runtime/entry.S:87-101`):

```obo
const RT_DEC_PARSE = 73
const RT_DEC_FORMAT = 74
const RT_DEC_CANONICAL = 75
const RT_DEC_CHECK = 76
const RT_DEC_EXPONENT = 77
const RT_DEC_DSCALE = 78
```

`entry.S` adds six `slot` lines after `rt_dec_int_result` and six `check` lines
after the existing `check rt_dec_int_result` (`entry.S:101`, `:189`). Slot 59
stays the hosted-exit/reserved slot; slots 0–72 keep their meaning. The comment
at `decimal24_core.inc:2346-2354` changes from `60..71` to `60..78`.

Runtime C signatures (all C ABI, explicit pointers; no 24-byte return):

```c
u64 rt_dec_parse_text(u64 s, u64 descErr, u64 descOk);
u64 rt_dec_format_text(const dec24 *a, u64 descErr, u64 descOk);
u64 rt_dec_canonical_text(const dec24 *a, u64 descErr, u64 descOk);
u64 rt_dec_check(const dec24 *a, const i64 *req, u64 descErr, u64 descOk);
i64 rt_dec_exponent(const dec24 *a);
i64 rt_dec_dscale(const dec24 *a);
```

`req` points to two words `{p, s}` in the compiler's frame, following the
existing `decimalRequest` pattern (`obo/src/obo.obo:3416-3444`). This keeps the
`rtCall` four-argument limit (`obo/src/obo.obo:3576-3579`) without packing
values or aliasing an integer.

### 6.2 Strict public parser

Add a wrapper next to the core parser, without changing `dec_parse`:

```c
static int dec_public_grammar(const char *t, size_t n) {
    size_t i = 0;
    if (i < n && (t[i] == '+' || t[i] == '-')) i++;
    size_t int_start = i;
    while (i < n && ascii_digit((unsigned char)t[i])) i++;
    if (i == int_start) return 0;              /* mandatory integer digit */
    if (i < n && t[i] == '.') {
        i++;
        size_t frac_start = i;
        while (i < n && ascii_digit((unsigned char)t[i])) i++;
        if (i == frac_start) return 0;         /* '1.' is invalid */
    }
    if (i < n && (t[i] == 'e' || t[i] == 'E')) {
        i++;
        if (i < n && (t[i] == '+' || t[i] == '-')) i++;
        size_t exp_start = i;
        while (i < n && ascii_digit((unsigned char)t[i])) i++;
        if (i == exp_start) return 0;          /* '1e' / '1e+' invalid */
    }
    return i == n;                             /* full consumption */
}

static int dec_parse_public(dec24 *out, const char *text, size_t len) {
    if (!dec_public_grammar(text, len)) return DEC_INVALID;
    return dec_parse(out, text, len);           /* same semantic precedence */
}
```

`dec_public_grammar` rejects `.5`, `1.`, empty, bare signs, `1..2`, `1e`, and
stops at whitespace/underscore/letters/NUL; `dec_parse` then rejects NUL and
non-ASCII and performs the precision/display/exponent checks in the approved
order. This is a pre-validation, not a second numeric parser: the coefficient
and exponent arithmetic remain exactly the tested core. The database read path
and `DecimalText` validation call the same wrapper through `decimal::parse`.

`rt_dec_parse_text`:

```c
u64 rt_dec_parse_text(u64 s, u64 descErr, u64 descOk) {
    dec24 v;
    int st = dec_parse_public(&v, sdata(s), slen(s));
    if (st != DEC_OK) return rt_dec_text_err(descErr, st, RT_OP_PARSE);
    return rt_dec_decimal_ok(RT_OP_PARSE, descErr, descOk, &v);
}
```

`sdata`/`slen` are the runtime's length-delimited string accessors
(`runtime.c:585-587`); no NUL terminator is assumed. The input string is
borrowed for the call. A parse-created value is copied into the ok cell, not
aliased.

### 6.3 Formatter Result builder

Both formatters share one builder:

```c
enum { RT_OP_PARSE = 1, RT_OP_FORMAT, RT_OP_CANONICAL, RT_OP_CHECK };

static int rt_dec_text_code(int st) {
    /* The core length query uses UNSUPPORTED_RANGE for a text bound. */
    return st == DEC_UNSUPPORTED_RANGE ? DEC_DISPLAY_RANGE : st;
}

static u64 rt_dec_decimal_ok(u64 op, u64 descErr, u64 descOk,
                             const dec24 *v) {
    u64 sz = ((u64 *)descOk)[0] ? ((u64 *)descOk)[0] : 8;
    if (sz < 24) return rt_dec_text_err(descErr, DEC_INVALID, (int)op);
    u64 c = alloc_raw_try(descOk, sz);
    if (!c) return rt_dec_text_err(descErr, DEC_BUFFER_SMALL, (int)op);
    memcpy(c, v, 24);                          /* Result[decimal] payload */
    return c;
}

static u64 rt_dec_text_result(const dec24 *a, int op,
                              u64 descErr, u64 descOk) {
    int canonical = op == RT_OP_CANONICAL;
    if (!dec_valid(a)) return rt_dec_text_err(descErr, DEC_INVALID, op);
    size_t n = 0;
    int st = canonical ? dec_format_canonical_len(a, &n)
                       : dec_format_display_len(a, &n);
    if (st != DEC_OK)
        return rt_dec_text_err(descErr, rt_dec_text_code(st), op);
    if (n == 0 || n > (size_t)DEC_TEXT_MAX_OUTPUT)
        return rt_dec_text_err(descErr, DEC_DISPLAY_RANGE, op);

    /* [len][bytes][NUL]; the logical string length is n, the NUL is spare. */
    u64 sp = alloc_raw_try(3, payload_add(8, n + 1));
    if (!sp) return rt_dec_text_err(descErr, DEC_BUFFER_SMALL, op);
    *(u64 *)sp = (u64)n;
    size_t got = 0;
    st = canonical ? dec_format_canonical(a, (char *)sp + 8, n + 1, &got)
                   : dec_format_display(a, (char *)sp + 8, n + 1, &got);
    if (st != DEC_OK) {
        rt_str_dec(sp);                        /* strings are refcounted */
        return rt_dec_text_err(descErr, rt_dec_text_code(st), op);
    }
    if (got != n) {
        rt_str_dec(sp);
        return rt_dec_text_err(descErr, DEC_BUFFER_SMALL, op);
    }

    u64 c = alloc_raw_try(descOk, ((u64 *)descOk)[0] ? ((u64 *)descOk)[0] : 8);
    if (!c) {
        rt_str_dec(sp);
        return rt_dec_text_err(descErr, DEC_BUFFER_SMALL, op);
    }
    ((u64 *)c)[0] = sp;                        /* Result[string] owns the string */
    return c;
}
```

`rt_dec_parse_text` calls `rt_dec_decimal_ok(RT_OP_PARSE, descErr, descOk, &v)`;
`rt_dec_check` calls it with `RT_OP_CHECK`. The `n == 0` check is defensive: both
formatters produce at least `0`.

Points that must not be lost in implementation:

- `dec_format_*` requires `cap >= required + 1` and writes a NUL
  (`decimal24_core.inc:2191-2203`). The string block therefore allocates one
  spare byte after the logical `n` bytes; the length prefix remains `n`.
- A rejected string is freed with `rt_str_dec`, not `rt_free`; `rt_free` traps
  on a string object (`runtime.c:440-449`).
- The ok cell is allocated with the exact descriptor the compiler passed, so
  `match` compares tags correctly (`obo/src/obo.obo:3669-3680`).
- `format` code 3 is returned as 3; the builder never falls back by itself.
  Print has its own fallback; the database adapter chooses the fallback.
- The `n > DEC_TEXT_MAX_OUTPUT` branch maps to 9; the allocation/buffer branch
  maps to 10. Neither truncates.

Add `alloc_raw_try` next to `alloc_raw` (`runtime.c:330-362`): the same free-list
and bump allocation, but return `0` instead of dying when `payload >
MAX_PAYLOAD` or when no block fits. On success it performs the same zeroing,
header, generation, `live_owned` and byte counters. Keep `alloc_raw`'s two
diagnostics exact:

- `payload > MAX_PAYLOAD` must still die with `obx: object too large\n`;
- a full heap must still die with `obx: out of memory\n`.

The simplest faithful refactor is to leave the `MAX_PAYLOAD` pre-check and
`die("obx: object too large\n")` in `alloc_raw`, then call `alloc_raw_try` and
`die("obx: out of memory\n")` on 0. Do not collapse both reasons into one
message; `heap_test.py` asserts both. `alloc_raw_try` itself may report a
reason through an out-parameter, but `alloc_raw` must preserve the existing
text. This is the only new allocator primitive; there is no general
exceptions/panic mechanism.

### 6.4 `check` runtime

```c
u64 rt_dec_check(const dec24 *a, const i64 *req, u64 descErr, u64 descOk) {
    if (!dec_valid(a)) return rt_dec_text_err(descErr, DEC_INVALID, RT_OP_CHECK);
    if (req == 0) return rt_dec_text_err(descErr, DEC_INVALID, RT_OP_CHECK);
    i64 p = req[0], s = req[1];
    if (p < 1 || p > 1000 || s < -1000 || s > 1000)
        return rt_dec_text_err(descErr, DEC_UNSUPPORTED_RANGE, RT_OP_CHECK);
    if (!dec_is_zero(a)) {
        char d[38];
        int len = 0, trailing = 0;
        int st = coeff_digits(a, d, &len, &trailing);
        if (st != DEC_OK) return rt_dec_text_err(descErr, st, RT_OP_CHECK);
        i64 k = (i64)(int16_t)a->exp + s;
        if (k < 0 && (i64)trailing < -k)
            return rt_dec_text_err(descErr, DEC_INEXACT, RT_OP_CHECK);
        if ((i64)len + k > p)
            return rt_dec_text_err(descErr, DEC_PRECISION_OVERFLOW, RT_OP_CHECK);
    }
    return rt_dec_decimal_ok(RT_OP_CHECK, descErr, descOk, a);   /* unchanged */
}
```

`coeff_digits` is the existing 38-byte helper (`decimal24_core.inc:2088-2119`);
zero is handled before it. No `10^p` is built, no allocation happens in the
predicate, and the input is not modified. `rt_dec_decimal_ok` allocates the
24-byte ok payload with `alloc_raw_try`; failure returns code 10.

### 6.5 Accessors

```c
i64 rt_dec_exponent(const dec24 *a) {
    if (!dec_valid(a)) rt_dec_trap(DEC_INVALID);
    if (dec_is_zero(a)) return 0;
    char d[38];
    int len = 0, trailing = 0;
    int st = coeff_digits(a, d, &len, &trailing);
    if (st != DEC_OK) rt_dec_trap(st);
    return (i64)(int16_t)a->exp + (i64)trailing;
}

i64 rt_dec_dscale(const dec24 *a) {
    if (!dec_valid(a)) rt_dec_trap(DEC_INVALID);
    return (i64)a->dscale;
}
```

`coeff_digits` already computes the trailing count used by canonical; do not
re-divide the 128-bit coefficient by ten in a second loop. `exp` is the
two's-complement `int16` bit pattern at `w2` bits 0..15 (`decimal24_core.inc:73-81`).

### 6.6 Error cells and exact messages

Add one static length-prefixed message table, so a `Result` err message needs no
heap allocation and an allocation failure cannot change the message:

```c
struct dec_msg { u64 len; char text[64]; };
static const struct dec_msg MSG_PARSE_INVALID = { 25, "obo: invalid decimal text" };
/* ... one per (operation, code); no trailing newline ... */
```

`rt_dec_text_err(descErr, st, op)` selects the table entry, then allocates the
16-byte err cell with `alloc_raw_try(descErr, 16)`. Place the table and
`rt_dec_text_err` before the formatter builder, or add a static forward
declaration; the builder uses it. The cell is the shared err
variant: message word at offset 0, code word at offset 8
(`obo/src/obo.obo:2954-2966`). The static message lives in rodata; `rt_str_dec`
ignores non-heap strings (`runtime.c:415-427`), so freeing the err cell does not
free rodata. If the err cell itself cannot be allocated, the existing global
`obx: out of memory` fatal path applies; there is no representable `Result`
without a cell. A defensive unknown `(op,code)` uses
`obo: decimal operation failed`.

The exact strings are the section 4.3 table. Keep the existing
`rt_dec_status_message` for arithmetic/print (`decimal24_core.inc:2369-2382`);
do not change its wording or add a newline to Result messages.

### 6.7 No libc, no libgcc, no pointer ABI

The new runtime code uses only existing freestanding primitives: `memcpy`,
`alloc_raw_try`, `rt_str_dec`, `coeff_digits`, `dec_div128_64` (inside
`coeff_digits`) and the decimal core. No `malloc`, `strtod`, `snprintf`,
`pow`, `ldexp`, `localeconv`, `strlen` or libgcc 128-bit division is added. The
Makefile already builds `runtime.c` with `-ffreestanding -fno-builtin -nostdlib`
(`obo/Makefile:3-6`). `nm -u`/`elf_test` must show no new imports.

The public ABI is unchanged: decimal arguments/results use the existing 24-byte
by-value slots and hidden destination pointer (`decimal-language-design.md:44-53`).
The runtime signatures use pointers only at the runtime boundary, as all
existing decimal helpers do (`decimal24_core.inc:2422-2469`). Strings remain
one-word owned pointers with a length prefix; there is no new raw pointer type.

## 7. Compiler implementation plan

### 7.1 Constants and registration

Add after `RT_DEC_INT_RESULT` (`obo/src/obo.obo:2082`) the six slots from 6.1.
`decimalBuiltin` (`obo/src/obo.obo:3522-3561`) gains branches. Keep the existing
argument-parsing style (`expect(T_LPAREN)`, `nestedExpr`, `expect(T_RPAREN)`).

```obo
fn decimalParse(s: Item): Item {
    if s.typ.kind != "string" { error("decimal::parse needs a string, not " + tname(s.typ)) }
    return resultCall3(RT_DEC_PARSE, s, tDecimal)
}

fn decimalFormat(a: Item): Item {
    if a.typ.kind != "decimal" { error("decimal::format needs a decimal value, not " + tname(a.typ)) }
    return resultCall3(RT_DEC_FORMAT, decPtr(a), tString)
}

fn decimalCanonical(a: Item): Item {
    if a.typ.kind != "decimal" { error("decimal::canonical needs a decimal value, not " + tname(a.typ)) }
    return resultCall3(RT_DEC_CANONICAL, decPtr(a), tString)
}

fn decimalExponent(a: Item): Item {
    if a.typ.kind != "decimal" { error("decimal::exponent needs a decimal value, not " + tname(a.typ)) }
    return rtCall1(RT_DEC_EXPONENT, decPtr(a), tInt)
}

fn decimalDscale(a: Item): Item {
    if a.typ.kind != "decimal" { error("decimal::dscale needs a decimal value, not " + tname(a.typ)) }
    return rtCall1(RT_DEC_DSCALE, decPtr(a), tInt)
}
```

`resultCall3` (`obo/src/obo.obo:3673-3680`) passes the shared err descriptor and
the cached ok variant of the exact payload. `tDecimal` is the built-in singleton
at `obo/src/obo.obo:1295`; `Result[decimal]` uses a 24-byte ok payload
(`obo/src/obo.obo:2945`). `format`/`canonical` use `Result[string]`; the ok
variant owns the string at offset 0 (`obo/src/obo.obo:2937-2947`).

### 7.2 `check` request block

```obo
fn decimalCheckRequest(p: Item, s: Item): Item {
    if !isInt(p.typ) { error("decimal::check precision must be an int") }
    if !isInt(s.typ) { error("decimal::check scale must be an int") }
    let pv = load(p)
    let sv = load(s)
    let at = newFrameBytes(16)
    let pd = newItem("mem", tInt)
    pd.base = RBP
    pd.disp = at
    store(pd, pv)
    let sd = newItem("mem", tInt)
    sd.base = RBP
    sd.disp = at + 8
    store(sd, sv)
    let req = newItem("mem", tInt)
    req.base = RBP
    req.disp = at
    return decPtr(req)
}

fn decimalCheck(a: Item, p: Item, s: Item): Item {
    if a.typ.kind != "decimal" { error("decimal::check needs a decimal value, not " + tname(a.typ)) }
    let req = decimalCheckRequest(p, s)
    return resultCall4(RT_DEC_CHECK, decPtr(a), req, tDecimal)
}
```

This mirrors `decimalRequest` but deliberately does **not** reject constant
out-of-range `p`/`s` (`obo/src/obo.obo:3420-3428`); the runtime returns code 6.
Evaluate `a`, then `p`, then `s`, as the other builtins do; the decimal item stays
in its frame slot while the request block is built.

### 7.3 `decimalBuiltin` branches and diagnostics

Add branches before the final unknown-operation error:

```obo
if member == "parse" {
    expect(T_LPAREN, "")
    let s = nestedExpr()
    expect(T_RPAREN, "")
    return decimalParse(s)
}
if member == "format" {
    expect(T_LPAREN, "")
    let a = nestedExpr()
    expect(T_RPAREN, "")
    return decimalFormat(a)
}
if member == "canonical" {
    expect(T_LPAREN, "")
    let a = nestedExpr()
    expect(T_RPAREN, "")
    return decimalCanonical(a)
}
if member == "check" {
    expect(T_LPAREN, "")
    let a = nestedExpr()
    expect(T_COMMA, "")
    let p = nestedExpr()
    expect(T_COMMA, "")
    let s = nestedExpr()
    expect(T_RPAREN, "")
    return decimalCheck(a, p, s)
}
if member == "exponent" {
    expect(T_LPAREN, "")
    let a = nestedExpr()
    expect(T_RPAREN, "")
    return decimalExponent(a)
}
if member == "dscale" {
    expect(T_LPAREN, "")
    let a = nestedExpr()
    expect(T_RPAREN, "")
    return decimalDscale(a)
}
```

Update the fallback text to list all operations:
`unknown decimal operation '<member>'; v1 has parse, format, canonical, check, exponent, dscale, div, quantize, toInt and fromInt`.

Arity errors stay ordinary `expect`/parse errors; no new diagnostic machinery.
`designatorT` already routes `decimal::member` to `decimalBuiltin`
(`obo/src/obo.obo:7769-7777`); no lexer or grammar token is added.

### 7.4 Compiler budget, fixed point, hello

- `PEAK_BUDGET = 2,500,000` (`obo/tests/peak_budget.py`; `obo/FORK.md:5771`).
  The released 0.12 peak is 2,194,160 direct / 2,194,560
  port, leaving ~305,000 bytes. Six builtins, one request helper and the runtime
  tables should be a few KB; measure before/after with `peak_budget.py` and do
  not raise the budget for this feature. If it unexpectedly grows, record the
  reason in `FORK.md` before raising it.
- The empty-array self-compile oracle floor stays 30,000 bytes over baseline
  (`obo/FORK.md:5775-5780`); do not weaken it.
- `make promote` must reach a byte fixed point (stage 2 == stage 3,
  `obo/Makefile:78-86`). The source change changes the compiler, so the seed
  used for the build must be the 0.13/format 7 seed; stage 2 and stage 3 are
  compared after the first build.
- The `hello` gate builds a normal `println("hello")` with the old seed and the
  new compiler against the same runtime and requires byte-identical output
  (the 0.12 release gate is 69,633 bytes; `obo/FORK.md:6155-6158`). Adding
  slots/builtins must not change code generation for a program that does not
  use them.
- `SYM_FORMAT` is 7 because field visibility landed; this text API does not
  touch `.sym` grammar. `SYM_ABI` stays 1. `OBO_VERSION` becomes the next free
  version, likely `"0.14"`. Every `.sym` built by 0.13 has a different compiler
  line and must be rebuilt (`obo/src/obo.obo:14233-14235`); no field-list or
  type-signature change comes from the text API itself.

### 7.5 Source anchors to change

| File | Anchor | Change |
|---|---|---|
| `obo/src/obo.obo` | `2070-2082` | add `RT_DEC_PARSE`..`RT_DEC_DSCALE` |
| `obo/src/obo.obo` | `3298-3317` | reuse `decimalTemp`/`decPtr`; no change |
| `obo/src/obo.obo` | `3522-3561` | six builtin branches, unknown-op text |
| `obo/src/obo.obo` | `3673-3690` | reuse `resultCall3`/`resultCall4` |
| `obo/src/obo.obo` | `3420-3444` | new `decimalCheckRequest` beside `decimalRequest` |
| `obo/runtime/entry.S` | `87-101`, `177-189` | six slots/checks |
| `obo/runtime/decimal24_core.inc` | `1970-2082` | `dec_public_grammar` / `dec_parse_public` wrapper |
| `obo/runtime/decimal24_core.inc` | `2346-2554` | text Result/check/accessor helpers, comment update |
| `obo/runtime/runtime.c` | `330-362` | `alloc_raw_try`, refactor `alloc_raw` |
| `obo/runtime/runtime.c` | `585-587`, `712-724` | reuse string/err/ok primitives |
| `obo/LANGUAGE.md` | `68-131`, `2247-2255` | document the six APIs after implementation |
| `obo/tests/port_compare.py` | `257-272` | add text cases at the target version |

## 8. Tests

### 8.1 Test files

Add, under `obo/tests/run/`:

- `decimal_text_parse.obx` — valid/invalid/boundary parse and codes;
- `decimal_text_format.obx` — format/canonical exact bytes, thresholds,
  `INEXACT`, print-vs-format, round-trip;
- `decimal_text_check.obx` — check vectors, negative scale, unnormalised
  coefficients, metadata range;
- `decimal_text_access.obx` — exponent/dscale including the PG fixtures;
- `decimal_text_limits.obx` — 38-digit, exponent/dscale boundaries, long
  leading-zero inputs, NUL/ASCII, 98,342-byte formatter bound, buffer cases.

Add `obo/tests/errors/decimal_text.obx` for argument-type errors. Extend
`obo/experimental/decimal/tests/dec24_test.c` (or add
`dec24_text_public_test.c`) for C-level exact-byte/short-buffer cases. Add
`obo/tests/rt_dec_text_test.c` for the public 98,342-byte output, the
`OBO_HEAP_LIMIT` code-10/free-list cases, and malformed accessor traps;
`heap_test.py` builds and runs it under the same heap controls as
`rt_heap_test.c`.

**Do not change the private oracle.** `decimal_text_oracle.py::parse_text`
(`:53`) and `build_text_cases` (`:177`) model the private `dec24_text` protocol
and deliberately accept `.5`/`1.` (`_GRAMMAR`, `:41-44`);
`test_decimal_text_oracle.py:60-61` asserts that, and
`generate_text_vectors.py:88` plus `dec24_text_test.c` consume the generated
fixtures. Keep those functions and fixtures byte-identical. Add, alongside
them, a strict public model:

- `parse_public_text(text)` with a `_PUBLIC_GRAMMAR` that requires at least one
  integer digit, at least one digit after a point, and at least one exponent
  digit; it shares only the value/`canonical_text`/`display_text` helpers;
- `build_public_text_cases()` with the public valid/invalid/boundary vectors;
- `check_public(value,p,s)` / `build_public_check_cases()` for the exact-fit
  predicate;
- a separate `test_decimal_text_public_oracle.py` registered in
  `run_decimal_tests.py`.

Add the new names to the oracle module's `__all__`; keep the private names and
their behavior unchanged.

Add a concrete module test at
`obo/tests/modules/run/decimal_text/lib.obx` exporting free functions
`parseText(s: string): Result[decimal]`, `formatText(d: decimal):
Result[string]`, `canonicalText(d: decimal): Result[string]`, `checked(d:
decimal, p: int, s: int): Result[decimal]`, `exponentOf(d: decimal): int` and
`dscaleOf(d: decimal): int`, plus a root that calls them. This exercises
`Result[decimal]`/`Result[string]` in `.sym` and module signatures; no decimal
field or method is used.

### 8.2 Parse vectors

Valid:

| Input | Code | `dscale` | Note |
|---|---:|---:|---|
| `0` | 0 | 0 | zero |
| `0.00` | 0 | 2 | zero keeps display scale |
| `-0` | 0 | 0 | canonical positive zero |
| `+0` | 0 | 0 | canonical positive zero |
| `-0.00` | 0 | 2 | positive zero, dscale 2 |
| `1` | 0 | 0 | integer-only DB cell |
| `+1` | 0 | 0 | signed DB cell |
| `-1` | 0 | 0 | signed DB cell |
| `0001.2300` | 0 | 4 | leading zeros accepted |
| `+1.2300` | 0 | 4 | signed fraction, DB cell |
| `-1.2300` | 0 | 4 | signed fraction, DB cell |
| `1.2300` | 0 | 4 | display scale preserved |
| `0.001` | 0 | 3 | |
| `1e3` | 0 | 0 | |
| `1E+3` | 0 | 0 | |
| `1e+003` | 0 | 0 | exponent leading zeros |
| `1e0003` | 0 | 0 | exponent leading zeros |
| `1e-0003` | 0 | 3 | exponent leading zeros |
| `1.2300e3` | 0 | 1 | |
| `1.2300e-2` | 0 | 6 | |
| `1e32767` | 0 | 0 | exponent boundary |
| `1e32768` | 0 | 0 | upper rescue, coefficient 10 |
| `1e32804` | 0 | 0 | last 38-digit rescue |
| `1e-32768` | 0 | 32768 | minimum exponent |
| `1e-16383` | 0 | 16383 | PG boundary |
| `1e-16384` | 0 | 16384 | obo ok, PG not |
| `1.0e-16383` | 0 | 16384 | normalised exp -16383 |
| `1000e-16384` | 0 | 16384 | normalised exp -16381 |
| `0e999999` | 0 | 0 | zero exponent 0 |
| `0e-32769` | 0 | 32769 | zero keeps low display scale |
| `0e-65535` | 0 | 65535 | zero display-scale boundary |
| `-0e-32769` | 0 | 32769 | sign dropped, scale kept |
| 38 nines | 0 | 0 | maximum coefficient |
| `0.` + 65535 zeros | 0 | 65535 | dscale boundary |
| `0`*100000 + `1` | 0 | 0 | read-side longer than 98,342 |

Invalid (code 4 unless stated):

`""`, `"+"`, `"-"`, `"."`, `".5"`, `"1."`, `"1..2"`, `"1e"`, `"1e+"`,
`"1e-"`, `"1e3."`, `"1e3e4"`, `"++1"`, `"--1"`, `"+-1"`, `" 1"`, `"1 "`,
`"1\t2"`, `"1_000"`, `"0x10"`, `"NaN"`, `"Inf"`, `"Infinity"`,
`"-Infinity"`, `"1\0"`, `"\0"`, `"1é"`, `"１２３"`, `"1\u00a0"`.

Semantic:

| Input | Code | Note |
|---|---:|---|
| 39 significant digits | 7 | precision |
| `1e32805` | 8 | needs 39 digits |
| `1e-32769` | 8 | below int16 |
| `1e-65535` | 8 | display scale fits, exponent fails |
| `1e-65536` | 9 | first display-range failure |
| `1e-70000` | 9 | display range wins |
| `1.0e-70000` | 9 | |
| `0e-70000` | 9 | zero keeps display scale |
| `0e-65536` | 9 | zero display-range failure |
| `0e-65535` | 0 | zero scale boundary, exponent stays 0 |
| `1e40000` | 8 | upper rescue fails |
| `0e70000` | 0 | zero exponent 0 |

Add the precedence cases `123...39digits.` → 4 (syntax first), `1e-70000` → 9
(not 8), and `1e40000` → 8 (not 9).

### 8.3 Format and canonical exact bytes

Use `==` against a constructed expected string, not only printed prefixes. A
test helper doubles a zero string to build long expected values:

```obo
fn zeros(n: int): string {
    var z = "0"
    while len(z) < n { z = z + z }
    return substr(z, 0, n)
}
fn formatIs(d: decimal, want: string) {
    match decimal::format(d) {
        ok(s) { if s == want { println("ok") } else { println("format differs") } }
        err(m, c) { println("err ", c, " ", m) }
    }
}
fn canonicalIs(d: decimal, want: string) {
    match decimal::canonical(d) {
        ok(s) { if s == want { println("ok") } else { println("canonical differs") } }
        err(m, c) { println("err ", c, " ", m) }
    }
}
fn errIsS(r: Result[string], code: int, msg: string) {
    match r {
        ok(s) { println("unexpected ok") }
        err(m, c) {
            if c == code && m == msg { println("ok") }
            else { println("err ", c, " ", m) }
        }
    }
}
fn errIsD(r: Result[decimal], code: int, msg: string) {
    match r {
        ok(v) { println("unexpected ok") }
        err(m, c) {
            if c == code && m == msg { println("ok") }
            else { println("err ", c, " ", m) }
        }
    }
}
```

Reachable errors assert both the code and the exact message, not only the
code: parse 4/7/8/9, format 3, check 3/6/7, and code 10 in the heap test. The C
harness asserts the defensive 4 path for malformed backing bits. Example:

```obo
errIsS(decimal::format(1e-20 * 1e-20), 3,
       "obo: decimal display scale would drop nonzero digits")
```

Exact cases:

| Value | `format` | `canonical` |
|---|---|---|
| `1.2300` | `1.2300` | `1.23` |
| `0.001` | `0.001` | `0.001` |
| `1e3` | `1000` | `1000` |
| `1.2300e3` | `1230.0` | `1230` |
| `0.00` | `0.00` | `0` |
| `-0.00` | `0.00` | `0` |
| 38 nines | 38 nines | 38 nines |
| `1e-40` | `0.` + 39 zeros + `1` | same |
| `1e-126` | `0.` + 125 zeros + `1` | same (128 bytes) |
| `1e-127` | `0.` + 126 zeros + `1` | `1e-127` |
| `1e127` | `1` + 127 zeros | same (128 bytes) |
| `1e128` | `1` + 128 zeros | `1e128` |
| `-1e-126` | `-0.` + 125 zeros + `1` | same (129 bytes with sign) |
| `-1e-127` | `-0.` + 126 zeros + `1` | `-1e-127` |
| `1e-16383` | `0.` + 16382 zeros + `1` | `1e-16383` |
| `1.0e-16383` | `0.` + 16382 zeros + `10` | `1e-16383` |
| `1000e-16384` | `0.` + 16380 zeros + `1000` | `1e-16381` |
| `1e32804` | `1` + 32804 zeros | `1e32804` |
| `1e-32768` | `0.` + 32767 zeros + `1` | `1e-32768` |
| `1.23e-200` | `0.` + 199 zeros + `123` | `1.23e-200` |
| `1e-20 * 1e-20` | err 3 | `0.` + 39 zeros + `1` |
| `1.20 * 1.2` | `1.440` | `1.44` |
| `1.20 + 1.2` | `2.40` | `2.4` |
| `1.20 * 1.0` | `1.200` | `1.2` |
| `0e-65535` | `0.` + 65535 zeros | `0` |

Also assert `len(format(...))` and `len(canonical(...))` for the 16K/32K cases;
`==` already proves every byte, but the length assertions localize a failure.

Message assertions (both code and exact message, using `errIsS`/`errIsD`):

| Call | Code | Exact message |
|---|---:|---|
| `decimal::parse("")` | 4 | `obo: invalid decimal text` |
| `decimal::parse(39 significant digits)` | 7 | `obo: decimal text has more than 38 significant digits` |
| `decimal::parse("1e32805")` | 8 | `obo: decimal exponent is outside int16` |
| `decimal::parse("1e-70000")` | 9 | `obo: decimal display scale is outside uint16` |
| `decimal::format(1e-20 * 1e-20)` | 3 | `obo: decimal display scale would drop nonzero digits` |
| `decimal::check(1.23456, 10, 4)` | 3 | `obo: value does not fit the declared scale without rounding` |
| `decimal::check(1.0, 0, 4)` | 6 | `obo: decimal precision/scale is outside the supported range` |
| `decimal::check(1e38, 38, 0)` | 7 | `obo: value exceeds the declared precision` |

The C harness asserts the defensive message `obo: invalid decimal value` (code
4) for a malformed dec24 passed to `format`/`canonical` and the diagnostic
`obo: decimal invalid value` for malformed accessor input. Code 10 is asserted
with `obo: decimal result could not be allocated` in the heap test.

### 8.4 Check, typmod and accessor vectors

Run the section 5.3 table through a `showCheck` helper that prints the code or
`ok` plus the value; assert the value is byte-identical to the input by
`v == input` (value equality) and `dscale(v) == dscale(input)`.

Typmod vectors (database integration; P0 does not decode them, but the same
suite records them for P1/P2) from
`decimal-db-boundary-design.md:201-215`:

| Declaration | typmod |
|---|---:|
| `numeric(1,0)` | 65540 |
| `numeric(10,2)` | 655366 |
| `numeric(10,4)` | 655368 |
| `numeric(10,-2)` | 657410 |
| `numeric(2,-3)` | 133121 |
| `numeric(3,5)` | 196617 |
| `numeric(38,0)` | 2490372 |
| `numeric(1000,0)` | 65536004 |
| `numeric(1000,1000)` | 65537004 |
| `numeric(1000,-1000)` | 65537052 |
| unconstrained | -1 |

Accessor vectors:

| Value | `dscale` | `exponent` |
|---|---:|---:|
| `1.2300` | 4 | -2 |
| `0.001` | 3 | -3 |
| `1e3` | 0 | 3 |
| `1.2300e3` | 1 | 1 |
| `0.00` | 2 | 0 |
| `0e-32769` | 32769 | 0 |
| `0e-65535` | 65535 | 0 |
| `1e-16383` | 16383 | -16383 |
| `1.0e-16383` | 16384 | -16383 |
| `1000e-16384` | 16384 | -16381 |
| `1e32804` | 0 | 32804 |
| `1e-32768` | 32768 | -32768 |
| `(1.20 * 1.5) * 1e-16382` | 38 | -16383 |
| `1e-20 * 1e-20` | 38 | -40 |

Check boundary vectors (in addition to section 5.3):

| Value | `p` | `s` | Result |
|---|---:|---:|---|
| `1e999` | 1000 | 0 | ok |
| `1e1000` | 1000 | 0 | 7 |
| `1e1000` | 1 | -1000 | ok |
| `1e1001` | 1 | -1000 | 7 |
| `1e38` (value `10^38`) | 38 | 0 | 7 |
| `1.23456` | 0 | 4 | 6 (metadata wins over 3) |
| `9`*38 + `0` (39-digit integer ending in zero) | 39 | 0 | ok |
| `9`*38 + `0` | 38 | 0 | 7 |
| `1.0` | 1 | -1001 | 6 |

The `9`*38 + `0` fixture is a 39-digit integer with 38 significant digits:
parse accepts it as coefficient `10^38 - 1`, exponent 1. `check` accepts it at
`(39,0)` and rejects it at `(38,0)`, proving `digits(c)+k` uses the full scaled
width, not the coefficient's significant digits alone.

### 8.5 Database design vectors

These are the P0 primitives the database design depends on; the adapter tests
repeat them end-to-end in P1/P2/P3.

| Case | Expected |
|---|---|
| `1e-16383` parse | ok, dscale 16383, exponent -16383, canonical `1e-16383`; PG display form exact |
| `1e-16384` parse | ok, dscale 16384, exponent -16384, canonical `1e-16384`; PG unchecked bind `1013` |
| `1.0e-16383` | ok, dscale 16384, exponent -16383, canonical `1e-16383`; PG display scale > 16383, canonical fallback |
| `1000e-16384` | ok, parse normalises to coefficient 1, exponent -16381, canonical `1e-16381`; **not** `1013`; not a raw-word guard |
| `(1.20 * 1.5) * 1e-16382` | value 1.8e-16382, exponent -16383, format err 3, canonical `1.8e-16382`; PG accepts with dscale 16383 |
| `1e-20 * 1e-20` | exponent -40, dscale 38 (cap), format err 3, canonical fixed 42 bytes |
| 38-digit `12345678901234567890123456789012345678e-32768` | parse ok; SQLite TEXT round-trip; PG unchecked bind `1013` |
| `1e40000` | parse 8; PG can store but obo cannot |
| `1e-70000` | parse 9; PG would reject display range |
| `"0"*100000 + "1"` | parse ok; value 1; read-side length is not capped at 98,342; `DecimalText` bind rejects the 100,001-byte payload with size code 2 |

The canonical fallback rule is: try `format`; on code 3 use `canonical`; if the
normalised exponent is below -16383 on PostgreSQL, return `1013` before
sending; if the display `dscale` exceeds 16383 but the exponent is in range,
canonical preserves the value. A raw-word exponent would fail the
`(1.20*1.5)*1e-16382` case; `1000e-16384` is normalised at parse and is **not**
a raw-word guard. The accessor tests use the arithmetic fixture.

`DecimalText` length is a separate bind-side gate: its bytes must be
`1..98,342`; empty is `1006`, oversize is the size/plumbing code `2`. The
read-side database cell has no such cap and may exceed 98,342 bytes (for
example a 100,001-byte leading-zero cell). Do not use `string(bytes)` as the
size check: it copies through fatal `alloc_raw`, so a heap failure there is the
global `obx: out of memory`, not code 10/3. Check `len(bytes)` first, and treat
the subsequent bounded copy as input transport, not as a text-API allocation
result.

### 8.6 Print versus format and round-trip

| Test | Expected |
|---|---|
| `println(1.2300)` | `1.2300`; `format` same |
| `format(0.00)` | `0.00`; `println(0.00)` `0.00`; canonical `0` |
| `println(1e-20 * 1e-20)` | canonical fixed text; `format` err 3 |
| `println(1e-16383)` | 16,385-byte display line; `format` same, not canonical |
| `println(1.0e-16383)` | 16,386-byte display line; `format` same; database canonical fallback is adapter policy, not print policy |
| `println(1000e-16384)` | 16,386-byte display line; `format` same |
| `parse(format(v)) == v` | true for every value whose format is ok |
| `dscale(parse(format(v))) == dscale(v)` | true for every format-ok value |
| `parse(canonical(v)) == v` | true for every value; dscale may differ |
| `parse("-0.00") == 0.00` | true; canonical `0`; format `0.00` |

Do not assert that canonical preserves `dscale`; it is value-exact only
(`decimal-db-boundary-design.md:668-691`).

### 8.7 Limits, NUL/ASCII, display cap, buffers

- 38 nines parse/format/canonical; 38 nines plus a nonzero 39th digit → 7;
  39 digits ending in zero is accepted (the trailing zero is not significant)
  and canonical has 39 integer digits.
- `0.` + 65,535 zeros → dscale 65,535, format length 65,537, canonical `0`.
  `0.` + 65,536 zeros → 9. `0e-32769` formats to length 32,771 and canonical
  `0`; `0e-65535` formats to length 65,537 and canonical `0`; `-0e-32769` is
  positive zero with dscale 32,769.
- `1.` + 65,535 zeros → dscale 65,535, format length 65,537, canonical `1`.
- `1e-32768` format length 32,770; `1e32804` format length 32,805.
- Maximum formatter output through the new public helper: a C harness builds a
  negative 38-digit coefficient, exponent 32,767 and dscale 65,535, calls
  `rt_dec_format_text` with the real `Result[string]`/err descriptors, and
  asserts the ok cell's string length is 98,342, the spare NUL is at
  `data+len`, and the whole block frees cleanly. A positive value gives 98,341.
  `cap == req` on the core formatter returns `DEC_BUFFER_SMALL` (10), writes
  nothing and reports `req`; `cap == req+1` succeeds and NUL-terminates.
- `alloc_raw_try` with an impossible payload returns 0; the text builder maps
  it to 10. Test the free-list path too: allocate, free, then confirm
  `alloc_raw_try` reuses the block. The public 9 path is defensive for valid
  values because the 98,342 bound is exact; 10 is a real allocation failure
  tested under `OBO_HEAP_LIMIT`. The C test asserts no truncation and no empty
  string.
- Heap-exhaustion Result test: under a small `OBO_HEAP_LIMIT` that still leaves
  room for a 16-byte err cell, allocate the input string/value and descriptors
  first, then fill the heap so the ok/text payload cannot be allocated; call
  the runtime helpers for parse, format, canonical and check; assert each
  returns an err cell with code 10 and the exact
  `obo: decimal result could not be allocated` message, with no fatal
  `obx: out of memory`. This exercises the real free-list/exhaustion path, not
  only an impossible payload.
- Malformed accessor trap: a C harness builds dec24 values with sign 2,
  coefficient above `10^38-1`, and nonzero `flags`/padding; `rt_dec_exponent`
  and `rt_dec_dscale` must exit with status 10 and
  `obo: decimal invalid value` (diagnostic id 43). No language value can reach
  this path.
- `DecimalText` bind size: 1..98,342 bytes accepted; empty → 1006; 98,343 bytes
  → size code 2. A 100,001-byte leading-zero cell remains parseable on the
  read side (no bind cap). `len(bytes)` is checked before `string(bytes)`; the
  latter's fatal `alloc_raw` is not a code 10/3 path.
- NUL/non-ASCII: `"1\0"`, `"\0"`, `"1é"`, full-width digits, NBSP → 4. The
  string length is used, never `strlen`; an embedded NUL is not a terminator.
- Empty text is never zero and never NULL; `parse("")` is 4.

### 8.8 Gates

| Gate | What must pass |
|---|---|
| `python3 obo/tests/doc_check.py` | LANGUAGE examples (after the implementation adds API examples) |
| `python3 obo/tests/run_fork.py` | new run/error/limits tests with `OBO_CHECK=1`; no leaks/double frees |
| `python3 obo/tests/port_compare.py` | new cases compile/run identically on the port; version-gated at the target |
| `make promote` | stage 2 == stage 3 byte fixed point; suite passes |
| `python3 obo/tests/peak_budget.py` | peak under 2,500,000; empty-array delta ≥ 30,000 |
| `mutate_fork.py` / `mutate_port.py` | new compiler text mutants 100% caught; no survivors |
| `mutate_io.py` | new runtime/`decimal24_core.inc` text and allocator mutants; entry-slot mutants |
| `heap_test.py` | `alloc_raw` diagnostics and the code-10 heap-exhaustion Result tests |
| `test_decimal_text_oracle.py` (private) and `test_decimal_text_public_oracle.py` (public) | private oracle/fixtures unchanged; public model agrees with `decimal::*` |
| `module_test.py` / `sym_test.py` | module signatures with `Result[decimal]`/`Result[string]`; `.sym` rebuilt for format 7/0.14 |
| `elf_test.py` / `nm -u` | no libc/libgcc/float imports; runtime links |
| hello gate | normal `println("hello")` byte-identical between old seed and new compiler |

`port_compare.py:257-272` currently marks decimal tests as introduced at
`(0,12)`. The new text tests must be added at the target version (likely
`(0,14)`) so the seed comparison allows the expected unsupported-feature
outcome rather than reporting a spurious port difference.

### 8.9 Mutants

Add a `mutate_text.py` catalog (or extend `mutate_decimal.py`) with at least:

| Mutant | Catcher |
|---|---|
| public grammar allows a missing integer part | `.5` → 4 |
| public grammar allows a trailing point | `1.` → 4 |
| public grammar accepts whitespace/underscore | `" 1"`, `1_000` |
| precision limit 38→39 | 39 significant digits → 7 |
| dscale formula ignores explicit exponent | `1.2300e3` dscale 1, not 0 |
| dscale clamp 65,535→65,534 | `0.` + 65,535 zeros |
| exponent lower bound -32,768→-32,767 | `1e-32768` |
| upper rescue 38-digit cap 37→36 | `1e32804` |
| negative zero sign retained | `-0.00` format/canonical |
| canonical threshold 128→127 or 129 | `1e-126`/`1e-127`, `1e127`/`1e128` |
| canonical keeps trailing zeros | `1.2300` canonical `1.23` |
| canonical adjusted exponent off by one | `1.23e-200` |
| format INEXACT `<`→`<=` | `1.2300` at dscale 2 vs 1 |
| format pads dscale-1 | `1.2300` |
| format rounds a dropped digit | `1.23456` at dscale 4 |
| check trailing-zero test `>=`→`>` | `1000` at `(2,-3)` |
| check ignores coefficient trailing zeros | `1500` at `(2,-3)` |
| check precision `<=p`→`<p` | 38 nines at `(38,0)`; `9`*38+`0` at `(39,0)` |
| check p range 1..1000→0..1000 | `(0,0)` |
| exponent returns the raw stored exponent | `(1.20*1.5)*1e-16382` (raw -16385, normalised -16383); not `1000e-16384`, which parse normalises |
| dscale reads word 1/offset 16 | `1.2300` |
| parse status 7↔8 swap | 39 digits or `1e40000` |
| formatter length/NUL off by one | exact-byte C test; max 98,342 output |
| text allocation failure not mapped to 10 | heap-exhaustion test, free-list path |
| `alloc_raw` loses its too-large reason | `heap_test.py` `obx: object too large` |
| `alloc_raw` loses its OOM reason | `heap_test.py` `obx: out of memory` |
| entry slot 73..78 points at the wrong runtime routine | each new builtin test; slot-table mutation |
| `.sym`/compiler rejects the new builtin names | module/error tests |

The compiler-side mutants are registered in `mutate_fork.py` and
`mutate_port.py` (the `MUTANTS`/`PORT_PATTERNS` catalogs; `mutant_catchers.tsv`
for port first-failure paths). Runtime and `decimal24_core.inc` mutants belong
in `mutate_io.py`'s `RUNTIME_MUTANTS`; because `build_runtime` currently copies
`runtime.c` and `entry.S` but not a mutant `.inc`, extend it with an
`RUNTIME_INC`/include-text path (or a small `mutate_text.py` that builds the
runtime from both sources). `entry.S` slot mutations need a new
`ENTRY_MUTANTS` path in the same harness, or a dedicated slot-table test that
the existing `mutate_fork` compiler cannot express. Do not omit the new slots:
a wrong slot target that no test calls is a surviving mutant. No mutant may be
accepted by changing a test expectation. The existing decimal mutants
(`obo/tests/mutate_decimal.py:39-40`) remain.

## 9. Interaction with the database design

### 9.1 Signatures the adapters can call

```obo
// PostgreSQL and SQLite, in lib/*.obx or the adapter modules
decimal::parse(s: string) -> Result[decimal]
decimal::format(d: decimal) -> Result[string]
decimal::canonical(d: decimal) -> Result[string]
decimal::check(value: decimal, p: int, s: int) -> Result[decimal]
decimal::exponent(d: decimal) -> int
decimal::dscale(d: decimal) -> int
```

The existing approved adapter signatures remain unchanged:

```obo
pub fn param_decimal(value: decimal, p: int, s: int): Result[Param]
pub fn param_decimal_unchecked(value: decimal): Result[Param]
pub fn param_decimal_null(): Param
pub fn (r: &Rows) decimal_type(col: int): Result[DecimalType]
pub fn (r: &Rows) decimal(row: int, col: int): Result[decimal]
pub fn bind_decimal(st: &Stmt, index: int, value: decimal,
                    p: int, s: int): Result[bool]
pub fn bind_decimal_unchecked(st: &Stmt, index: int, value: decimal): Result[bool]
pub fn (s: &Stmt) decimal(col: int): Result[decimal]
```

`param_decimal` calls `decimal::check` first, then `encode_decimal`. `Rows.decimal`
calls `decimal::parse`, then `decimal::check` when constrained. `bind_decimal`
calls `check`, then `encode_decimal`, then `bind_text`.

### 9.2 Error-code mapping

The approved database error namespace is `decimal-db-boundary-design.md:418-455`.
The text API mapping is:

| Source | Adapter code |
|---|---:|
| `parse` 4 | 1006 `DEC_DB_TEXT_INVALID` |
| `parse` 7 | 1007 `DEC_DB_PRECISION` |
| `parse` 8 | 1008 `DEC_DB_EXPONENT` |
| `parse` 9 | 1009 `DEC_DB_DISPLAY_RANGE` |
| `parse` 10 | 3 allocation |
| `parse` 1/6 (defensive) | 2 plumbing |
| `format` 3 | not an error: call `canonical` |
| `format` 9 | 1009 |
| `format` 10 | 3 allocation |
| `format` 6 (defensive) | 1009 |
| `format` 4 (defensive) | 2 plumbing |
| `canonical` 9 | 1009 |
| `canonical` 10 | 3 allocation |
| `canonical` 6 (defensive) | 1009 |
| `canonical` 4 (defensive) | 2 plumbing |
| `check` 3 | 1010 `DEC_DB_SCALE` |
| `check` 7 | 1011 `DEC_DB_RANGE` |
| `check` 6 | 1012 `DEC_DB_METADATA` |
| `check` 10 | 3 allocation |
| `check` 1/4 (defensive) | 2 plumbing |
| `exponent`/`dscale` | no error |

**Correction to the approved sketch.** The earlier `canonical_db` sketch mapped
both code 9 and code 10 to `1009`, while its prose said a canonical range
failure maps to `1009` and an allocation failure maps to `3`. Code 10 is the
allocation/buffer code in this design; the sketch maps 9 (and defensive 6) to
`1009`, 10 to `3`, and invalid 4 to plumbing `2`. The earlier `format` sketch
mapped every non-3 error to `1009`; it now maps 6/9 to `1009`, 10 to `3`, and 4
to plumbing `2`. The DB design's §5.1 sketch and prose are updated accordingly.
If the adapter deliberately keeps 10→1009, no value is lost or truncated, but
an allocation failure is mislabelled as a display-range error.

### 9.3 `DecimalText`

`SqlArg.DecimalText { v: []byte }` remains the v1 SQL-template transport
(`decimal-db-boundary-design.md:711-759`). The template builds the bytes with
`encode_decimal` (display, canonical fallback), not at render time. The renderer
first checks `len(bytes)` is `1..98,342` (empty → 1006, oversize → size code
2), then validates the bytes with `decimal::parse(string(bytes))` and maps
4/7/8/9 to 1006/1007/1008/1009 and 10 to 3. The size check is the bind gate:
read-side database cells keep the no-cap rule and may be longer than 98,342.
`string(bytes)` is a fatal-`alloc_raw` copy, not a code 10/3 source; do not use
it as the size check. `DecimalText` is a value payload, not SQL syntax; it is
never interpolated. The text API makes this work without decimal record/variant
fields.

### 9.4 What remains blocked

- `DecimalOrNull` and any decimal record/closed-variant field:
  `obo/src/obo.obo:5448` rejects `decimal` fields; field visibility does not
  change that. NULL stays `is_null` plus defensive code 1001 for v1.
- `?decimal`: not representable (`decimal-language-design.md:42`).
- `SqlArg.Decimal { v: decimal }`: blocked by the same variant-field rule;
  `DecimalText` is the workaround.
- SQL `Decimal` variant and nullable-column metadata: later.
- Binary numeric wire, COPY, arrays, intervals, domains, catalog introspection:
  out of scope.
- `decimal(p,s)` language syntax: deferred; `check` is boundary metadata.

P0 does **not** unblock these. It unblocks `param_decimal`/`bind_decimal`,
`Rows.decimal`/`Stmt.decimal`, the PostgreSQL `-16383` pre-check, and
`DecimalText` validation.

## 10. Rejected alternatives

- **Reuse private `dec_parse` unchanged.** It accepts `.5` and `1.`
  (`dec24_text.h:11-16`; `dec24_text.c:44-156`), contradicts the approved public
  grammar and would disagree with the Python oracle and the database read path.
  The strict wrapper is mandatory; the private parser stays private.
- **`string(decimal)`.** It would have to choose display or canonical, has no
  error channel for `INEXACT`/allocation, and the language deliberately rejects
  it (`obo/src/obo.obo:7004`). Use explicit `format`/`canonical`.
- **Implicit rounding in `format` or `check`.** Drops digits or accepts values
  the declared `(p,s)` cannot represent; violates the no-silent-rounding rule
  and lets a server round money. Return `INEXACT`/`PRECISION` instead.
- **Print as the text API.** `print` writes to stdout, falls back to canonical
  on `INEXACT`, and has no `Result`; it cannot be a bind/key primitive. Keep
  print as presentation and add value-returning builtins.
- **Canonical that loses digits or rounds.** A truncated fixed form breaks
  database round-trip and value keys. Canonical is exact by construction.
- **Exponentless-only canonical.** `1e-32768` and `1e32804` need 32K+ bytes and
  still would not cover future wider values; exact scientific is bounded and
  PostgreSQL/SQLite both accept it. Fixed is still preferred under 128 bytes.
- **`check` that rounds.** It would hide schema/scale changes and make the
  default binder unsafe. `check` is exact; callers who want rounding call
  `quantize` first, then `check`.
- **Buffers without errors.** Truncation, empty text or a sentinel string would
  silently corrupt values and conflate allocation with empty text. Return 9/10.
- **`Result[string]` with a static/cached string.** Aliasing and ownership
  hazards; each call returns a fresh owned string.
- **A pointer/handle decimal text API.** The approved value model has no
  `&decimal`; runtime pointers stay internal (`decimal-language-design.md:42`).

## 11. Open choices and recommendations

| # | Choice | Recommendation |
|---|---|---|
| O1 | Canonical scientific threshold | **128 bytes of fixed magnitude text excluding sign**, matching `DEC_TEXT_CANON_FIXED_MAX` and the approved DB design O10. It is tested, keeps canonical ≤129 bytes with sign, and makes `1e-126`/`1e127` fixed, `1e-127`/`1e128` scientific. Alternatives: 64 (more scientific), 1000 (larger fixed text), unbounded (would need 32K+ byte keys). |
| O2 | Parse `dscale` for exponent literals | **`max(0, frac_digits - explicit_exponent)`**, exactly the literal rule (`decimal-language-design.md:80`). `1.2300e3` has dscale 1, `1.2300e-2` has dscale 6. Alternative: keep `frac_digits` regardless of exponent; that would display `1230.0000` for `1.2300e3` and disagree with literals. |
| O3 | Signed zero | **Canonical positive zero, preserve written `dscale`**. `-0`, `+0`, `-0.00` all have sign 0; `format(-0.00)` is `0.00`; canonical is `0`. Alternative: keep a sign bit for `-0`; PostgreSQL has no negative zero and the core already canonicalises it. |
| O4 | `exponent`/`dscale` error policy | **Total `int` functions for valid values**, with diagnostic id 43 (process exit status 10) only for malformed backing bits. `dscale` is `0..65535`; `exponent` is `-32768..32804` normalised. A `Result[int]` would force unwrap/allocation on every bind and obscure the PG pre-check. Revisit if a raw-bits ABI ever exposes malformed values. |
| O5 | Buffer/allocation code | **10 `DEC_BUFFER_SMALL`** for text payload/ok-cell allocation failure; **9** for a required output above 98,342 bytes. The 98,342 bound is exact for a valid 24-byte value, so the public 9 path is defensive; the C tests still assert short-buffer behaviour. A global OOM that prevents even the err cell remains the existing fatal path. |
| O6 | `check` constant `p`/`s` validation | **Runtime code 6**, not a compile error. Metadata is data; the database adapter must map invalid typmod metadata to 1012 and tests must exercise it in language. Type errors remain compile errors. |
| O7 | `canonical` code 10 in the DB adapter | **Map 10 to allocation code 3**, correcting the earlier sketch's `9/10 -> 1009`; map 9 (and defensive 6) to 1009, 4 to plumbing 2. No value loss either way, but 10 is allocation, not display range. |
| O8 | Location of the text surface | **Compiler-known `decimal::` builtins for P0**; a `lib/decimal.obx` wrapper can add convenience later. This matches `div`/`quantize`/`toInt` and avoids module/ABI work in the prerequisite tier. |
| O9 | `DecimalText` bind size | **Check `len(bytes)` 1..98,342 before parse**: empty is `1006`, oversize is size/plumbing `2`; read-side cells keep the no-cap rule. Do not use `string(bytes)` (`alloc_raw`, fatal on OOM) as the size check. |

## 12. References

Repository (line numbers at baseline `2364add`):

- `obo/design/decimal-language-design.md:26-28` layout/zero, `:42` no
  `?decimal`, `:57` value equality, `:67-84` literals and display scale,
  `:97-110` arithmetic/traps/statuses, `:114-132` conversions/format/cap,
  `:134-142` database `check`, `:156-172` decisions.
- `obo/design/decimal-db-boundary-design.md:81-120` public DB grammar,
  `:122-149` display/canonical, `:159-178` limits, `:201-215` typmod vectors,
  `:325-371` check predicate/vectors, `:373-416` PG `-16383`/normalised
  exponent, `:418-455` error codes/messages, `:600-655` encode/fallback,
  `:668-691` round-trip limits, `:711-759` `DecimalText`, `:955-1013` text
  vectors, `:1145-1171` P0 list, `:1273-1288` open choices.
- `obo/LANGUAGE.md:68-131` released decimal scalar tier, `:2240-2255`
  diagnostic ids and Result codes, `:2431-2436` known holes.
- `obo/src/obo.obo:110-118` version/format/ABI, `:1295` `tDecimal`, `:2069-2082`
  decimal slots, `:2937-2966` `Result` variants, `:3298-3317`
  decimal temp/pointer, `:3420-3444` request block, `:3453-3475` decimal
  Result, `:3522-3561` `decimalBuiltin`, `:3669-3690` result calls,
  `:5448` no decimal fields, `:7004` no `string(decimal)`, `:7154-7186` print,
  `:7769-7777` `decimal::` routing, `:11483-11484` no decimal method
  parameters/returns, `:14233-14235` `.sym` compiler-version check.
- `obo/runtime/decimal24_core.inc:73-101` layout/statuses, `:87-101` status
  values, `:472-507` `dec_valid`/`dec_set`, `:1884-1916` private grammar and
  text bounds, `:1970-2082` `dec_parse`, `:2088-2119` coefficient digits,
  `:2150-2262` canonical, `:2272-2344` display, `:2346-2554` runtime helpers,
  `:2369-2382` trap messages, `:2406-2416` multiply dscale cap, `:2528-2554`
  Result cells.
- `obo/runtime/runtime.c:330-362` `alloc_raw`, `:364` record alloc, `:415-449`
  string refcount/free, `:585-624` string layout/copy, `:712-724`
  err/ok cells, `:1534` core include.
- `obo/runtime/entry.S:87-101` decimal slot table, `:177-189` slot checks.
- `obo/tests/run/decimal_display.obx`, `decimal_literals.obx`,
  `decimal_result.obx`, `decimal_result_codes.obx` current print/Result
  behaviour.
- `obo/experimental/decimal/tests/decimal_text_oracle.py:41-44` private
  `_GRAMMAR`, `:53` `parse_text`, `:177` `build_text_cases` (keep unchanged);
  `test_decimal_text_oracle.py:60-61` private `.5`/`1.` assertions;
  `generate_text_vectors.py:88` private fixture consumer. Add
  `parse_public_text`/`build_public_text_cases`/`check_public` alongside.
- `obo/tests/mutate_decimal.py:39-40` existing literal mutants;
  `obo/tests/mutate_io.py:47` `RUNTIME_MUTANTS`, `:310-326` runtime build
  (currently copies `entry.S`/`decimal24_core.inc` unchanged; extend for
  `.inc` and entry-slot mutants);
  `obo/tests/port_compare.py:257-272` decimal-version gating;
  `obo/tests/peak_budget.py` budget; `obo/FORK.md:5771-5780` 2,500,000/30,000;
  `obo/FORK.md:6145-6158` 0.12 identity/hello gate.

External:

- PostgreSQL numeric text input/output and limits:
  <https://www.postgresql.org/docs/current/datatype-numeric.html>
- PostgreSQL `numeric.c` (`numeric_in`/`numeric_out`, `NUMERIC_DSCALE_MAX`,
  `NUMERIC_MAX_PRECISION`, negative scale):
  <https://raw.githubusercontent.com/postgres/postgres/master/src/backend/utils/adt/numeric.c>
- SQLite storage classes/affinity:
  <https://www.sqlite.org/datatype3.html>
