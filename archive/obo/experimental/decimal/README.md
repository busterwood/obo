# Experimental decimal foundations (`decimal`)

Status: **experimental D2 arithmetic contract, reviewed and copied unchanged
into `runtime/decimal24_core.inc`; the minimum scalar decimal tier is released
as obo `0.12`.** D2 adds the exact/rounded arithmetic separation, general
division, truncating remainder and the wide-gap sticky add/sub path on top of
the reviewed D1 core. This package remains the reviewed core and provenance
record; the language integration lives in the compiler/runtime and is a released
public contract for the scalar tier. Later tiers (fields, arrays, globals,
captures, methods/interfaces, text APIs, database wire) remain unimplemented.

The approved direction remains:

- [`../../design/decimal-research-2026-10-28.md`](../../design/decimal-research-2026-10-28.md)
  — the 24-byte, 128-bit coefficient, 38-significant-digit representation
  with `int16` exponent and independent `uint16` display scale.
- [`../../design/decimal-arithmetic-research-2026-10-29.md`](../../design/decimal-arithmetic-research-2026-10-29.md)
  — arithmetic bounds, no-libgcc division strategy and the exactness guard.
- [`../../design/numeric-literals-decision-2026-10-29.md`](../../design/numeric-literals-decision-2026-10-29.md)
  — bare fractional/exponent literals will be exact `decimal`; `f`/`F` marks
  `float`.

Superseded and deliberately absent here: the older 16-byte / 96-bit /
`System.Decimal` model, the `m` literal suffix, and any claim that the earlier
private prototype was a language ABI. `decimal(p,s)` remains a **proposal**, not
approved syntax; this package only contains a mathematical binding-validation
helper for later discussion.

## Layout and core contract

```text
word 0: coefficient bits 0..63
word 1: coefficient bits 64..127
word 2: int16 exponent | uint16 display scale | sign | flags | pad
value = (-1)^sign * coefficient * 10^exponent
```

The coefficient is at most `10^38 - 1`. Zero is canonical positive
(coefficient 0, sign 0, exponent 0) and may carry any `uint16` display scale.
Display scale is presentation metadata, not the arithmetic quantum exponent.
Finite values only: no NaN, infinity or signed zero. The representation is
static-asserted at 24 bytes and 8-byte alignment.

`dec24.c` is the reviewed private core imported from the repaired prototype:
limb multiply, `divq`-based integer division primitives, rounded add, sub and
multiply, explicit-mode quantize, numeric comparison ignoring display scale,
and raw-struct validation. D1's exact 256-bit alignment remains the first path.
D2 adds a bounded sticky fallback when the exact accumulator cannot hold the
aligned result; it computes the correctly rounded 38-digit neighbour, including
the subtraction borrow, directed-mode asymmetry and the lower-decade
power-of-ten boundary. `DEC_UNSUPPORTED_EXACT` is retained as a status value but
is no longer returned by add/sub. Arithmetic is **not** claimed complete for
compiler integration; see the explicit limits below.

`dec24_arith.h`/`dec24_arith.c` add the separate arithmetic contract. They use
only the public limb primitives and fixed-width integer state. They do not call
libc, libm, libgcc or a big-integer/decimal package, do not allocate, and do not
use binary floating point.

## Exact and rounded arithmetic (D2)

D2 deliberately separates two contracts instead of pretending that every
operation is exact:

```text
exact op     no rounding. DEC_OK only when the exact mathematical result is
             representable with <= 38 significant digits and an int16 exponent.
             DEC_INEXACT means the exact value is finite but needs more than 38
             significant digits, or the quotient is nonterminating. DEC_OVERFLOW
             means the normalized exponent is outside int16. The output is not
             written on any non-OK status.
rounded op   an explicit mode (and, for division, an explicit scale or
             precision) is mandatory. There is no hidden language default.
```

The existing `dec_add`, `dec_sub`, `dec_mul` and `dec_quantize` keep their D1
rounded-mode signatures and display-scale rules (`max` for add/sub, sum capped
at 65535 for multiply, target for quantize). They are rounded operations:
`DEC_OK` does not claim that discarded digits were zero. Use the new exact
entry points when that distinction matters.

New private entry points (all take an explicit independent result display
scale; no public display-propagation policy is chosen here):

```c
int dec_add_exact(dec24 *, const dec24 *, const dec24 *, uint16_t dscale);
int dec_sub_exact(dec24 *, const dec24 *, const dec24 *, uint16_t dscale);
int dec_mul_exact(dec24 *, const dec24 *, const dec24 *, uint16_t dscale);
int dec_div_exact(dec24 *, const dec24 *, const dec24 *, uint16_t dscale);
int dec_rem_trunc(dec24 *, const dec24 *, const dec24 *, uint16_t dscale);
int dec_div(dec24 *, const dec24 *, const dec24 *,
            const dec_div_request *);
int dec_div_scale(dec24 *, const dec24 *, const dec24 *,
                  int32_t scale, unsigned mode, uint16_t dscale);
int dec_div_precision(dec24 *, const dec24 *, const dec24 *,
                      unsigned precision, unsigned mode, uint16_t dscale);
```

`dec_div_request` has an explicit `kind`:

- `DEC_DIV_ROUND_SCALE`: round the exact quotient once to `scale` fractional
  decimal places. Nonzero results use exponent `-scale`, except the documented
  core carry normalization (`10^38` becomes `10^37` with exponent +1). A scale
  outside `-32767..32768` is `DEC_UNSUPPORTED_RANGE`. If the coefficient before
  the carry check exceeds `10^38 - 1`, or the exponent leaves `int16`, the
  result is `DEC_OVERFLOW`.
- `DEC_DIV_ROUND_PRECISION`: round the exact quotient once to
  `1..38` significant digits. The exponent is derived from the quotient's
  adjusted exponent, and a representable exact value with fewer significant
  digits is padded to the requested precision. Exponent range follows the
  parser rule: below `int16`, coefficient trailing zeros are removed only as
  far as needed; above `int16`, trailing zeros are appended (upper-exponent
  rescue) until the exponent fits. The stored coefficient can therefore be
  longer than the requested precision: `1e32800` at precision 1 is coefficient
  `10^33` with exponent 32767 (34 coefficient digits), while `1e32805` still
  overflows because 39 digits would be required. The value is unchanged and
  display scale stays independent. `DEC_OVERFLOW` means the exponent cannot be
  brought into `int16` within 38 coefficient digits, not merely that the
  initially derived exponent was outside the range.

Nonterminating quotients such as `1/3`, `1/7` or `1/(10^38-1)` therefore need
a mode and a scale/precision; `UNNECESSARY` returns `DEC_INEXACT`. Division by
zero is `DEC_DIV_ZERO` for every division/remainder entry point, not infinity or
NaN. Zero divided by a nonzero value is canonical positive zero with the
caller's display scale. Overflow and underflow are explicit statuses; a tiny
quotient is never silently flushed to zero.

`dec_rem_trunc` is the explicit truncating remainder, not Euclidean or
floor-mod:

```text
q = trunc(a / b)          (toward zero)
r = a - q * b              exactly
-|b| < r < |b|, and r has the sign of a (zero is canonical positive)
```

The quotient is not rounded before the multiplication. The remainder is exact
and fits the core for valid finite inputs. The modular power-of-ten path keeps
large exponent gaps (`d <= 65535`) bounded: it uses `O(log d)` 128-bit modular
multiplications rather than building a 65,536-digit integer or a 65,536-byte
stack array. SQL-style sign-of-dividend remainder can be built from this named
truncating operation; no default SQL/vendor semantics are encoded in it.

Wide-gap add/sub is completed without pretending the exact result is
representable. For example:

```text
12e76 + 1   exact: DEC_INEXACT (the exact value needs more than 38 digits)
            HALF_EVEN/TOWARD_ZERO: 12e76
            AWAY/CEILING: one 38-digit ulp above 12e76
            UNNECESSARY: DEC_INEXACT
9e76 + 1    exact alignment still fits the 256-bit path and rounds normally
```

The sticky path covers both signs, all eight modes and the lower-decade
power-of-ten cancellation boundary: the predecessor of `10^k` is
`10^k - 10^(k-38)` (38 nines), not `10^k - 10^(k-37)`.

### Proposed public policy (not approved)

The following are **PROPOSED** for later language review, not implemented
language rules and not defaults in this package:

- exact `+ - * /` with `Result`-style `INEXACT`/`OVERFLOW` for exact contexts,
  versus explicitly rounded operations in a checked/context form;
- a decimal precision/scale context or caller-supplied display scale; no
  default `decimal(p,s)` syntax and no default rounding mode;
- truncating remainder as the primitive, with SQL/mod dialect semantics layered
  explicitly by a library or adapter;
- display-scale propagation `max` for add/sub, sum for multiply, and an
  explicit target for divide/remainder. These are plausible defaults but remain
  caller/context decisions outside this private contract.

## Exact text (D1)

`dec24_text.h` adds a private exact parser and two formatters. There is no
binary floating point anywhere, no `strtod`, no allocation and no libc.

Private grammar (not the obo language grammar):

```text
sign     := '+' | '-' | empty
mantissa := digits [ '.' [digits] ] | '.' digits
exponent := ('e'|'E') ['+'|'-'] digits
text     := sign mantissa [exponent]
```

The caller supplies an explicit length. The whole length must be consumed;
embedded NUL, non-ASCII bytes, whitespace, underscores, `NaN`, infinity and a
bare sign are rejected. The private API accepts an optional leading `+`; that is
a convenience here, not a decision about obo literal syntax.

The coefficient is normalized by removing trailing zeros. Display scale is
`max(0, fractional_digits - explicit_exponent)`. For example:

```text
"1.2300"   -> coefficient 123, exponent -2, display scale 4
"1.5e3"    -> coefficient 15,  exponent  2, display scale 0
"1.2300e-2"-> coefficient 123, exponent -4, display scale 6
"-0.00"    -> canonical zero, display scale 2 (no negative zero)
```

Semantic parse statuses are distinct and checked only after the full text is
syntactically valid: `DEC_PRECISION_OVERFLOW` for more than 38 significant
digits, `DEC_DISPLAY_RANGE` for display scale outside `uint16`, and
`DEC_EXPONENT_RANGE` for a nonzero value that remains outside `int16`. Zero
keeps canonical exponent 0, so a huge positive explicit exponent does not make
zero unrepresentable; its display scale still has to fit `uint16`. Leading
zeros and trailing zeros are handled without building huge powers of ten, so
long inputs are linear and never silently rounded.

Upper-exponent normalization is round-trip complete. If stripping trailing
zeros pushes the exponent above 32767, the parser puts back only the minimal
number of coefficient zeros needed to store exponent 32767, provided the
coefficient stays within 38 digits. Thus `1e32804` is accepted as coefficient
`10^37` with exponent 32767, while `1e32805` would need 39 digits and remains
`DEC_EXPONENT_RANGE`. Negative exponents are not falsely rescued: stripping has
already made the exponent as large as possible, so a still-too-negative value
is genuinely unrepresentable.

Two separate formatters avoid inventing a public display policy:

- `dec_format_canonical*` produces exact numeric text and ignores display
  scale. Fixed notation is used when its magnitude text (excluding the optional
  `-` sign) is at most 128 bytes; otherwise it uses the exact scientific form.
  A negative fixed value can therefore be one byte longer than the threshold.
  This 128-byte threshold is a documented private protocol, not a final
  language rule.
- `dec_format_display*` produces fixed notation with exactly the value's
  `dscale` fractional digits, preserving trailing zeros (`1.2300`). It never
  rounds: if the value needs more fractional digits than its display scale, it
  returns `DEC_INEXACT`.

Both use a caller buffer and checked capacity. `*_len` returns the required
length excluding NUL. A short buffer returns `DEC_BUFFER_SMALL`, reports the
required length and writes nothing. Output is bounded (`DEC_TEXT_MAX_OUTPUT`);
huge display scales are size-queried rather than allocated. On success the text
is NUL-terminated; on other errors the buffer is untouched.

```c
dec24 d;
size_t n;
char buf[16];
dec_parse(&d, "1.2300", 6);              /* 123 * 10^-2, dscale 4 */
dec_format_canonical(&d, buf, sizeof buf, &n); /* "1.23" */
dec_format_display(&d, buf, sizeof buf, &n);   /* "1.2300" */
```

Parsing a successful display string recovers the same value and display scale;
parsing the canonical string recovers the same numeric value.

## Binding validation (D1, proposal only)

`dec24_bind.h` implements the mathematical exact-fit contract

```text
v fits decimal(p,s) exactly iff v * 10^s is an integer and abs(v * 10^s) < 10^p
```

with `p >= 1` and `s` any `int64`. Negative scale and `s > p` are accepted when
the mathematics allows them. No SQL Server `0 <= s <= p` rule, Oracle range,
PostgreSQL typmod range or vendor uniform range is hardcoded. Exact checks
ignore display scale. SQL `NULL` is not modelled and must not be encoded as
zero.

`dec_bind_quantize` is the separate explicit-rounding helper. It requires a
mode, applies one rounding at the requested scale, checks precision and writes
the result only if it fits. It does not provide a default. The required carry
case is covered:

```text
999.995, decimal(5,2), HALF_AWAY -> 1000.00 -> six scaled digits -> reject
```

Nonzero quantize uses exponent `-s`, so this helper supports `-32767 <= s <=
32768`; zero can keep a larger `uint16` display scale. A result that would need
more than the core's 38 coefficient digits returns `DEC_OVERFLOW` even if a
wider database column could hold it. No database connection, driver, wire
format, PostgreSQL/local integration or native pointer ABI was used or added.

## Tests and evidence

Run from the repository root:

```sh
python3 obo/experimental/decimal/tests/run_decimal_tests.py
```

The runner generates deterministic fixtures into a fresh
`/tmp/opencode/decimal-D2-<UTC>/` evidence directory, builds a freestanding
`ld` binary with no libc/libm/libgcc, runs the C and Python suites, then builds a
separate host ASAN/UBSAN diagnostic with the same test bodies. It records
source hashes, generated-fixture hashes, command logs, `nm` output, sizes,
disassembly and results.

At this milestone the freestanding harness reports **32,719 checks, 0 failures**:

```text
PASS checks=5820         retained reviewed core vectors/tests (D1 plus the
                         explicit 12e76 sticky replacement)
PASS text checks=14103   parse/format fixed, random, boundary, upper-exponent
                         and buffer cases
PASS bind checks=2914    exact fit, explicit rounding, carry and limits
PASS arith checks=9882   exact add/sub/mul/div/rem, rounded division scale and
                         precision, all modes, wide gaps and power-of-ten edges
```

The Python oracles run **48 tests**. The arithmetic corpus contains 388 add,
388 sub, 120 mul, 527 exact-operation, 1,004 division (scale and precision) and
189 remainder vectors, generated by `arithmetic_oracle.py` from exact
`Fraction` values and integer rounding decisions. The retained D1 corpus still
includes the 5,820-check core vectors, 571 text cases and 500 binding cases.
The upper-edge property additionally checks raw coefficient values
`10^k * 10^32767` and `10^k * 10^32766`, both signs, canonical and display
round-trips against exact `Fraction` values. Python `decimal` is used only as a
secondary exact cross-check; no oracle copies the C limb code and no oracle
uses binary float. `nm -u` on the final freestanding binary is empty; no
`__udivti3`, `__udivmodti4`, `__umodti3`, `__multi3`, libc, libm or libgcc
symbol is present. The host sanitizer run is not the freestanding artifact.

The source/provenance and reviewed-prototype hashes are in
[`PROVENANCE.md`](PROVENANCE.md).

An optional host-only seeded probe retains the larger property checks:

```sh
python3 obo/experimental/decimal/tests/run_property_probes.py          # quick
python3 obo/experimental/decimal/tests/run_property_probes.py --full   # review scale
```

The `--full` run used in this evidence checked 100,000 ordering pairs, 50,000
binding cases, 1,150 upper-exponent canonical/display round-trips, 20,000
division cases, 10,000 truncating-remainder cases and 20,000 wide-gap add/sub
cases with zero failures. The division/remainder/gap probes compare against
exact `Fraction` and the independent arithmetic model, not only against the C
implementation. These counts are separate from the 32,719 freestanding checks
above.

## D2 performance evidence

A fresh microbenchmark reused the decimal24 Astra fixtures (`bench.c`, 256
fixtures, 1,000 reps/trial, seven alternating trials, CPU 3, identical
checksums across frozen D1, step-1 and D2). It is a microbenchmark, not an
application or compiler-budget claim. The fixtures mix up to 19-digit
invoice-style inputs with 38-digit wide inputs and 38-digit rounded products,
not small cash values. Median ns/op (D2 / frozen D1):

```text
add_money      25.02 / 372.37     mul_money       15.96 / 715.11
add_mixed      25.65 / 428.45     mul_38          47.80 / 3872.15
cmp_money       9.24 / 62.71      cmp_38           7.43 / 211.04
format_money   24.51 / 46.01      format_38       70.23 / 173.35
parse_mix      73.25 / 73.05      bind_money      27.13 / 36.52
quantize_38    15.04 / 1744.91    overflow_mul    46.95 / 3851.10
cancel         10.85 / 10.69      wide_kernel     11.18 / 11.30
```

The step-1 to D2 comparison is within trial/layout noise on most common paths;
`add_money` was 28.59 -> 25.02 ns and `format_38` 75.42 -> 70.23 ns, while
`mul_money` was 15.38 -> 15.96 ns and `overflow_mul` 43.80 -> 46.95 ns. Ranges
overlap and no correctness or checksum changed. New D2-only operations, medians
over seven trials: `div_scale_money` 56.8 ns, `div_prec_money` 60.5 ns,
`rem_money` 32.5 ns, `add_sticky_near` 28.0 ns, `exact_div_money` 78.3 ns,
`div_scale_38` 417 ns, `rem_38` 384 ns. The 38-digit division/remainder
numbers are the bitwise correctness-first path, not an optimized
quotient-estimate path.

## Not implemented / still unsupported

- Compiler/runtime integration beyond the released 0.12 minimum scalar tier:
  fields, arrays, globals/constants, captures, methods/interfaces, `.sym`
  constants, constant folding and standard-library APIs are still not
  implemented. The scalar ABI/literals/operators/`Result[decimal]` integration
  is released as `0.12`; the remaining tiers are not.
- Public display policy, **public** default rounding, **public** default
  division scale, language overflow/error grammar, and canonical text as a
  language guarantee. The D2 division/remainder APIs are private and explicit.
- Database drivers, TDS/OCI/libpq conversion, vendor metadata rules, nullable
  binding, PostgreSQL special values and any helper process.
- NaN, infinity, signed zero, or a `decimal(p,s)` language type.
- Nonzero strict `dec_quantize` above target 32768 (a pre-existing core/helper
  limit); zero accepts every `uint16`.
- A general arbitrary-precision `numeric` tier. This is the fixed 38-digit
  finite core.
- Division/remainder performance is correctness-first: the 256/128 path is a
  bitwise `divq` loop, not the later Knuth-D optimization. Measured costs are
  reported in the D2 evidence; they are not an application-throughput claim.
- The `10^38` carry rule remains the documented prototype choice: one
  divide-by-ten for add/sub/mul/divide rounding, not full trailing-zero
  canonicalization. Exact operations remove trailing zeros only when needed to
  fit 38 digits or the int16 exponent range.

The core is Linux x86-64 C using `unsigned __int128` multiply and inline
`divq`; it is not a frozen calling convention. A later compiler integration
must choose the ABI and error/overflow/division policies from the approved
research first, with explicit review. That integration, Stage B/lambda work,
the C interop toolkit and allocator work are separate and were not touched.

## Compiler integration plan (minimum tier released; later tiers not started)

1. Independent review of this D1/D2 core, text, binding and arithmetic contract
   and its honest limits.
2. Decide the **public** arithmetic policy: exact-error versus rounded context,
   default rounding/division scale (if any), display propagation and
   overflow/`Result` grammar. The private D2 APIs are evidence, not approval.
3. Freeze the 24-byte calling convention and constant representation only from
   the approved design, including the unresolved public policies above.
4. Add compiler multiword value paths and literal parsing after the literal
   policy review; do not add `decimal(p,s)` syntax or a suffix silently.
5. Add database adapters separately, with explicit driver/connection permission
   and vendor-specific p/s metadata; no native-pointer/`libpq` decimal shortcut.
