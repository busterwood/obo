# Experimental decimal foundations (`decimal`)

Status: **experimental D1 private core, ready for independent review.** This is
not compiler integration, not a public API, not a stable ABI, not a language
literal grammar and not a database adapter. No version, `SYM_FORMAT`, `SYM_ABI`,
seed, release or default rounding/division policy was changed. Do not merge,
push or promote without the independent review that this milestone stops for.

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
limb multiply, `divq`-based integer division primitives, exact/rounded add, sub
and multiply within the documented guard, explicit-mode quantize, numeric
comparison ignoring display scale, and raw-struct validation. The add/sub guard
is honest: each alignment shift and each aligned operand/sum must fit the
256-bit accumulator. Some mathematically representable extreme-gap results are
returned as `DEC_UNSUPPORTED_EXACT` rather than guessed. Arithmetic is **not**
claimed complete; decimal division and remainder are not implemented.

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
`/tmp/opencode/decimal-D1-<UTC>/` evidence directory, builds a freestanding
`ld` binary with no libc/libm/libgcc, runs the C and Python suites, then builds a
separate host ASAN/UBSAN diagnostic with the same test bodies. It records
source hashes, generated-fixture hashes, command logs, `nm` output, sizes,
disassembly and results.

At this milestone the freestanding harness reports **22,821 checks, 0 failures**:

```text
PASS checks=5804         retained reviewed core vectors/tests
PASS text checks=14103   parse/format fixed, random, boundary, upper-exponent
                         and buffer cases
PASS bind checks=2914    exact fit, explicit rounding, carry and limits
```

The Python oracles run **34 tests** and include the retained 5,804-check core
corpus (including 1,053 comparison vectors and property checks), 571 text cases
(500 seeded random plus fixed boundaries), and 500 binding cases. The upper-edge
property additionally checks raw coefficient values `10^k * 10^32767` and
`10^k * 10^32766`, both signs, canonical and display round-trips against exact
`Fraction` values. Python
`decimal` is used only as a secondary exact cross-check; no oracle copies the C
limb code and no oracle uses binary float. `nm -u` on the final freestanding
binary is empty; no `__udivti3`, `__udivmodti4`, `__umodti3`, `__multi3`, libc,
libm or libgcc symbol is present. The host sanitizer run is not the freestanding
artifact.

The source/provenance and reviewed-prototype hashes are in
[`PROVENANCE.md`](PROVENANCE.md).

An optional host-only seeded probe retains the larger property checks:

```sh
python3 obo/experimental/decimal/tests/run_property_probes.py          # quick
python3 obo/experimental/decimal/tests/run_property_probes.py --full   # review scale
```

The `--full` run used in this evidence checked 100,000 ordering pairs, 50,000
binding cases and 1,150 upper-exponent canonical/display round-trips with zero
failures. These counts are separate from the 22,821 freestanding checks above.

## Not implemented / still unsupported

- Full decimal division with a result-scale policy, remainder, and any claim
  that arithmetic is complete.
- A sticky/one-ulp wide-gap path; `12e76 + 1` remains honestly
  `DEC_UNSUPPORTED_EXACT` (`9e76 + 1` and `109e75 + 1` are accepted).
- Nonzero strict quantize above target 32768; zero accepts every `uint16`.
- The `10^38` carry normalization rule (prototype uses one divide-by-ten).
- Compiler/runtime integration, the 24-byte ABI, multiword locals/params/
  returns/fields/arrays/globals/constants, `Result[decimal]`, `.sym` constants,
  literal policy, comparisons/constant folding and standard-library APIs.
- Public display policy, default rounding, default division scale, language
  overflow/error grammar, and canonical text as a language guarantee.
- Database drivers, TDS/OCI/libpq conversion, vendor metadata rules, nullable
  binding, PostgreSQL special values and any helper process.
- NaN, infinity, signed zero, or a `decimal(p,s)` language type.

The core is Linux x86-64 C using `unsigned __int128` multiply and inline
`divq`; it is not a frozen calling convention. A later compiler integration
must choose the ABI and error/overflow/division policies from the approved
research first, with explicit review. That integration, Stage B/lambda work,
the C interop toolkit and allocator work are separate and were not touched.

## Next compiler integration plan (not started)

1. Independent review of this D1 core/text/binding contract and its honest
   limits.
2. Freeze the 24-byte calling convention and constant representation only from
   the approved design, including overflow, error/`Result`, division scale and
   rounding defaults still unresolved.
3. Add compiler multiword value paths and literal parsing after the literal
   policy review; do not add `decimal(p,s)` syntax or a suffix silently.
4. Add database adapters separately, with explicit driver/connection permission
   and vendor-specific p/s metadata; no native-pointer/`libpq` decimal shortcut.
