# Decimal arithmetic implementation research

**Date:** 2026-10-29
**Revision:** 2 (post-review; revision 1 was commit `ec2344c`)
**Status:** Research spike. No compiler, runtime, test, Makefile or other design document was changed. Benchmark sources, binaries and logs live under `/tmp/obo-decimal-arith/`, not in the repository.
**Companions:** `decimal-research-2026-10-28.md` (approved 24-byte design and database survey) and `numeric-literals-decision-2026-10-29.md` (literal policy).

**D1 implementation status (2026-10-31, non-normative).** A private, experimental
core now lives in `obo/experimental/decimal/` for independent review. It imports
the repaired 24-byte/38-digit prototype and adds exact parse/format and
mathematical `decimal(p,s)` binding validation. It is not compiler/runtime
integration, not an ABI, not a public API and not approved `decimal(p,s)` syntax;
division, remainder, default rounding and database adapters remain open.
The 16-byte/96-bit `.NET` sketches and the `m` literal suffix remain superseded
for the target design. See `experimental/decimal/README.md` for the implemented
contract, honest unsupported cases and evidence.

**Markers:** **[V]** verified locally in this spike: measured, rebuilt, or read in the local source tree. **[C]** cited externally: stated by the cited primary source but not independently reproduced here. **[O]** observed environment or implementation detail, not a general contract. **[I]** inference, design judgment or recommendation. Design-document statements are marked [V design]; vendor facts they cite are [C] unless reproduced here. All performance numbers are [V] for the stated host and method only. No unmeasured performance claim is presented as fact.

**Review response.** An independent Sonnet review reproduced the benchmark and vector suites but returned REVISE. This revision applies the blocking and should-fix items:

- **libgcc contradiction resolved.** GCC lowers 128-bit `/` and `%` to `__udivti3`, `__udivmodti4` and `__umodti3`; the freestanding `ld -T runtime/rt.ld` link cannot resolve those symbols. The revision 1 `128/64` row was therefore **libgcc-assisted, not a raw `divq`**, and so were the 256/128 Knuth estimate, old format, database and 128-bit ledger rows. The recommendation is now explicit inline-asm `divq`/limb code that never calls libgcc, with measured no-libgcc numbers and a link-level proof. §5.11 records the evidence.
- **`_Decimal128` comparison corrected.** The original multiply accumulator becomes decimal128 Infinity after about five of 256 reps, and the divide loop builds operands inside the timed loop (about 62-67 ns/op of setup). Fair finite, precomputed-operand measurements replace the invalid 5.5x/1.6x ratios: the direction (libbid slower) stands, the fair gaps are about 3.1x for divide and about 3x for multiply.
- **Claims qualified.** "Only fixed layout" now means only binary-coefficient layout; SQL Server's and ASE's existing 128-bit/38-digit binary coefficients are acknowledged as precedent; ASE byte order is not asserted; the ABI inline benchmark checksums all 24 bytes; the add/sub exactness bound and d>38 sticky case are tested; boundary vectors and gaps are listed; database conversion is scoped as a magnitude-copy kernel. Minor corrections (effort lines, `_entry`, `-static`, saved logs, normalization wording, CPUID ratio and units, fixed-width parse/format scope, rounding-mode list, `decimal_digits(0)`, §2.6 kernel labels, serial-control shape, the bad 58-bit comment, and [V]/[C] marking) are applied throughout.

## Executive verdict

- **Keep the approved representation unchanged.** A 24-byte value with a 128-bit binary coefficient, sign, `int16` decimal exponent and `uint16` display scale is the only **binary-coefficient** layout considered here that holds 38 significant digits in 24 bytes. Base-`10^19` (2 x 64-bit coefficient plus 8 bytes of metadata) and base-`10^9` (5 x 32-bit plus metadata, about 24-32 bytes) also fit 24 bytes, so uniqueness is not the argument; the binary coefficient wins on add/compare/divide structure and the 64-bit fast path. The coefficient bound is `10^38 - 1 < 2^127`; the exact product of two such coefficients is below `10^76 < 2^253`, so a 256-bit intermediate is sufficient. [V arithmetic bounds; I layout judgment]
- **The 128-bit/38-digit coefficient is not new to databases.** SQL Server `decimal(38)`/`SqlDecimal` and ASE `numeric(38)` already use fixed 128-bit/38-digit binary coefficients. What this design adds is the 24-byte language value with a separate signed decimal exponent and display scale and a finite-only arithmetic core; that is an engineering combination, not a new arithmetic technique. [C vendor documentation; I scope]
- **The core arithmetic is practical, with no libgcc calls.** Measured on a Zen 4 host with the no-libgcc `divq` kernels: 128x128 -> 256 multiply 2.0-2.6 ns; multiply plus one rounding to 38 digits 25.8-30.0 ns (9.5-10.0 ns when `k <= 19` through 256/64); Knuth 256/128 division 22.0-26.7 ns; decimal divide plus round 24.3-27.9 ns; 256/64 division 10.2-10.9 ns; exponent alignment plus add 1.6-1.9 ns for typical small differences (3.1-3.5 ns at a difference of 38); parse of 38 digits 8.8-11.4 ns; format 23.0-27.8 ns. [V]
- **Use the 64-bit fast path inside the 24-byte type.** Coefficients below `10^19` fit in a `u64`: 64x64 -> 128 multiply costs 0.43-0.49 ns versus 2.5-2.7 ns for 128x128 -> 256, and 64/64 division costs 1.5-1.8 ns versus 22.0-26.7 ns for 256/128. An invoice loop costs 1.5-1.9 ns per line at 64-bit precision, 4.6-4.9 ns with 128-bit coefficients, and 49-54 ns through the full 38-digit multiply/divide/round path (one 66 ns high-load outlier). [V]
- **Scalar baseline C is the default; do not hand-write MULX/ADX.** GCC's ordinary `unsigned __int128` code is as fast as a hand-written BMI2+ADX sequence within run-to-run noise (default build: baseline 2.0-2.1 ns, hand-written ADX 2.1-2.3 ns; `-mbmi2 -madx` build: baseline 2.2-2.3 ns, ADX 2.0-2.1 ns). Compiling the project with `-march`/`-mbmi2 -madx` would raise the minimum CPU for no reliable gain. [V]
- **Do not add CPU dispatch, `target_clones`, or ifunc.** There is no useful scalar feature to dispatch to. `target_clones` does not work in obo's freestanding static link: without libgcc it fails to link, and with libgcc the `R_X86_64_IRELATIVE` relocation is never applied, so the first call jumps through a self-referential PLT slot and hangs (observed under `timeout` in the freestanding `-static` link). `__builtin_cpu_supports` silently reports every feature false unless libgcc's `__cpu_indicator_init` constructor runs, which a custom `_entry` does not do. Manual CPUID is the only feature test that works without libgcc, and a CPUID costs about 464 ns per operation on this WSL2 host versus 0.44 ns for a cached flag, roughly 1050x. [V/O]
- **Do not use SIMD for scalar decimal.** AVX2 has no 64x64 -> 128 vector multiply. AVX-512 IFMA helps only a batch of independent products whose results stay in vector registers: 0.77-0.88 ns per product for a batch of eight in the default build (up to 0.89 with `-mbmi2 -madx`), but 2.9-3.1 ns once each 256-bit product is packed out, and 29-30 ns for a single value. Intel's own libbid has no SIMD paths, and no AVX-512 decimal128 library was found. [V measurements; C libbid source search]
- **The largest risks are ABI and exact-rounding semantics, not raw arithmetic speed.** The 24-byte value touches parameters, returns, fields, arrays, globals, constants, `Result[decimal]` and `.sym`; division scale policy and one-rounding/add-sticky behavior need explicit decisions and test vectors. The core C arithmetic is comparatively well understood. [I]
- **Recommended default:** freestanding C runtime helpers that use `unsigned __int128` for add and multiply only, explicit 64-bit limbs for division, inline-asm `divq` routines that never call libgcc, hidden destination pointer, 64-bit fast paths, no `-march`, no dispatch, no SIMD, no libgcc. Add a build-time check that the runtime objects reference no `__udivti3`, `__udivmodti4` or `__umodti3`, a 256/64 specialization, and an oracle-driven test suite. [I]

## 1. Representation comparison

The value is `(-1)^sign * coefficient * 10^exponent`, with `coefficient` in `0..10^38-1`, a separate `dscale` for display, finite values only, and zero canonical and positive. [V design]

| Option | Size and precision | Add/sub | Multiply | Divide | Parse/format | Fit for obo |
|---|---|---|---|---|---|---|
| **128-bit binary coefficient + exponent + dscale (approved)** | 24 B; 38 digits; exponent `int16`; dscale `uint16` | 128/256-bit integer adds; align by `10^d` | 4x 64x64 -> 128 partial products; 256-bit exact | 256/128 Knuth D with a 192-bit quotient; `divq` limbs | SWAR parse; base-`10^19` format | **Recommended** [I] |
| Base-`10^9` limbs | 5 x 32-bit = 20 B coefficient plus metadata (~24-32 B); 45 digits | 5-limb carry loops | 25 partial products | base-`10^9` long division | Decimal digits are natural | Fits 24 B with tight metadata; good for the later arbitrary `numeric` tier; not the fixed type [V design; I] |
| Base-`10^19` limbs | 2 x 64-bit = 16 B coefficient plus 8 B metadata = 24 B; exactly 38 digits | Binary multiply by `10^d`, then renormalize | 2x2 limb products; rounding can drop low limbs | 2-limb base-`10^19` division; normalization and quotient estimation are harder | Natural 19-digit chunks | Also fits 24 B; no measured advantage and more complex division; not recommended without a prototype [I] |
| DPD / IEEE decimal128 | 16 B; **34 digits**; BID or DPD; NaN/Inf/signed zero | software on x86 | software on x86 | software on x86 | software | Wrong precision and semantics; measured GCC `_Decimal128` is much slower. [V] |
| .NET-style 96-bit | 16 B; 28-29 digits | simple | 96x96 -> 192 | 192/96 | software | Cannot hold 38 digits. [V design] |
| 256-bit coefficient storage | ~40 B; up to 77 digits | larger | 256x256 -> 512 for full generality | much larger | larger | Conditional Oracle 39-40-digit upgrade only; doubles the ABI. [V design] |

Why the approved layout wins:

- The coefficient is a binary integer, so add/sub/compare are ordinary multi-word integer operations and the fast path is a single 64-bit word.
- 256 bits hold the exact 76-digit product; no precision is lost before the single final rounding.
- SQL Server's TDS/on-page format is documented or observed as a sign byte plus a little-endian binary magnitude, so the coefficient maps directly after scale alignment. Sybase ASE also uses a sign byte plus a base-256 binary magnitude, but the approved design deliberately marks ASE's exact byte order as a decision gate; this note does not assert ASE little-endian. Oracle's base-100 and PostgreSQL's base-10000 need division, but neither is the storage format of this type. [C SQL Server; C ASE; I consequence]
- DPD and decimal128 are storage/interchange formats with a 34-digit limit and special values; they are the wrong primitive for a 38-digit, finite-only, 24-byte type.

A base-`10^19` coefficient deserves one caution. It can make multiplication rounding cheap by dropping low limbs, but addition still needs alignment by an arbitrary power of ten, division needs a base-`10^19` normalization and quotient estimate, and there is no measured implementation here. It is not recommended on current evidence; it would be a prototype only if division later dominates. [I]

## 2. Algorithms

### 2.1 Add and subtract

1. Compare signs. Same sign: add magnitudes. Different signs: subtract the smaller magnitude from the larger and use the larger's sign.
2. Align exponents to `e = min(ea, eb)`: multiply the coefficient with the larger exponent by `10^(|ea-eb|)`.
3. **Exactness bound.** A full 256-bit alignment fits whenever `a * 10^d + b < 2^256`, which is the sharp condition. A simple sufficient decimal bound is `digits(a) + d <= 77`: with `a, b < 10^38`, then `a * 10^d + b < 10^77 + 10^38 < 2^256`. The case `a = 10^38-1`, `d = 39`, `b = 10^38-1` gives `10^77 - 9*10^38 - 1`, bit length 256, and fits. The implementation uses the stricter guard `digits(a) + d <= 76` (then `a * 10^d + b < 10^76 + 10^38 < 2^253`), so the 77-digit case is deliberately routed to the sticky path as a conservative choice, not because it overflows. When `digits(a) + d > 76` and `digits(b) <= 38`, the most significant digit of `b` lies below the 38-digit rounding window (`digits(b) - 1 - d < digits(a) - 38`), so for same-sign addition only its nonzero/sticky contribution matters. Use that sticky path instead of a full alignment. A test with `digits(a) = 1` and `d = 75` (exact under the 76 guard) and with `digits(a) = 38` and `d = 39` (sticky) exercises the guard; 45 nonzero d>38 sticky vectors plus one `b = 0` control pass, and a separate check confirms the 77-digit boundary value fits in 256 bits.
4. **Subtraction caveat.** For subtraction where the exact result falls just below `a`, a tiny `b` below the window is not just sticky: if `a` has trailing zeros, `a - b` lies within one ulp of the 38-digit grid below `a`. The correctly rounded result is sign- and mode-dependent. For positive `a`, toward-zero/truncate and floor produce the next lower representable (one ulp below `a`), while ceiling and away-from-zero produce `a`. For negative `a`, the exact result is more negative: floor still produces the more-negative neighbour and ceiling produces `a`, while truncation toward zero produces `a` and away-from-zero produces the more-negative neighbour (truncation and away-from-zero swap relative to the positive case). Nearest/half-up may round back to `a`, but the inexact flag still depends on `b != 0`. Implement the sign- and mode-dependent one-ulp adjustment explicitly; do not drop `b` blindly.
5. Compute into a 256-bit accumulator. If the aligned coefficient exceeds 38 significant digits, round once to 38 digits using the requested mode; record inexact if required.
6. Set `dscale = max(dscale_a, dscale_b)`; preserve the display scale. Check the `int16` exponent range and overflow after rounding. Zero is canonical positive.

Measured alignment-plus-add cost (including a 256-bit result and checksum; coefficients below `10^38`, exact vectors):

| Case | Median ns/op | Min ns/op | Repeat range (median) |
|---|---:|---:|---:|
| exponent difference 0 (no multiply) | 0.710 | 0.641 | 0.666-0.811 |
| exponent difference 4 (one 128x64) | 1.718 | 1.499 | 1.718-1.886 |
| exponent difference 19 (one 128x64) | 1.627 | 1.503 | 1.627-1.894 |
| exponent difference 38 (128x64 then 192x64) | 3.135 | 2.842 | 3.135-3.467 |
| variable difference 0..38 (exact) | 3.115 | 2.632 | 3.048-4.549 |
| guarded variable 0..150 (full or sticky) | 1.673 | 1.476 | 1.663-2.834 |
| sticky-only difference >38 (no alignment) | 0.665 | 0.539 | 0.553-1.093 |

A pure 128-bit coefficient add with equal exponents costs 0.44-0.50 ns; the 256-bit result materialization and checksum account for most of the 0.67-0.81 ns at difference 0. The variable 0..38 median had one high-load 4.55 ns sample set; its other medians were 3.05-3.14 ns. The table is the cost of producing a 256-bit aligned sum, not of a complete decimal add with metadata and traps. [V; I for the complete-operation caveat]

A complete add that must round because the aligned sum exceeds 38 digits adds a division step. The full-width rounding case costs the 256/128 division path measured in §5.3 (22.0-26.7 ns no-libgcc), so the common no-round add is roughly 1.6-3.5 ns and the exceptional round is roughly 25-30 ns. Implement the no-round and sticky paths first and make the rounding path correct before optimizing it. [I]

### 2.2 Multiply

1. Compute the exact 256-bit product of the two 128-bit coefficients with four 64x64 -> 128 partial products. Measured 2.0-2.6 ns.
2. Let `digits` be the decimal digit count of the product (at most 76) and `k = digits - 38`. If `k <= 0`, the product fits and no rounding is needed.
3. If `k > 0`, divide the 256-bit product by `10^k` and round once, half away from zero by default. The divisor is a power of ten: if `k <= 19` the divisor fits a `u64` and a 256/64 path applies (measured 9.5-10.0 ns including the multiply and rounding); if `k > 19` the divisor needs the 256/128 path. For two full 38-digit operands, `k = 38`, so the 256/128 path is mandatory in the worst case (measured 25.8-30.0 ns).
4. Set `dscale = min(dscale_a + dscale_b, 65535)` (the `uint16` field maximum; the approved design allows display scale 0..65535) and `exponent = ea + eb + k`. After rounding, the coefficient can be exactly `10^38`; that is not an overflow of the type. In the canonical zero-stripping form it normalizes to coefficient 1 with exponent increased by 38 (for `e = 0`, coefficient 1, exponent 38), by stripping all 38 zeros or special-casing the value. A single divide-by-ten is not that normalization: it yields coefficient `10^37` with exponent incremented by one, still a valid 38-digit coefficient. Choose one rule (strip all zeros, or one divide-by-ten) and check the final `int16` exponent. [V design]
5. Overflow if the normalized coefficient still exceeds 38 digits or the exponent leaves `int16`; trap by default, with `Result` variants later.

Measured cost of the full-width rounded multiply (128x128 -> 256, then divide by `10^37` or `10^38`, then half-away increment): 25.8 ns median, 23.4 ns min in the representative run; 27.0-30.0 ns across repeats. The multiply alone is 2.0-2.6 ns; the 256/128 division dominates. [V]

Intel libbid avoids the division in rounding: it precomputes reciprocal constants `Kx` that approximate `10^-x` rounded up, multiplies the coefficient by `Kx` (after adding a half-unit term) and corrects near the boundary, with rounding helpers partitioned by digit count (`round64_2_18`, `round128_19_38`, `round192_39_57`, `round256_58_76`). That is a proven alternative to the 256/128 division used here, but it was not implemented or measured in this spike. Start with the division path for correctness and consider reciprocal constants only if rounding is hot in profiles. [C: https://github.com/gcc-mirror/gcc/tree/master/libgcc/config/libbid; I]

The 256/64 specialization is now measured: 9.5-10.0 ns for multiply, divide by `10^k` (`k <= 19`) and round, using four `divq` limb steps. The recommended multiply path should use it whenever `k <= 19`. [V]

### 2.3 Divide

1. Division requires an explicit result scale/mode (or a documented checked default). There is no hidden low-precision default.
2. Scale the numerator by `10^g` so the quotient has 38 digits plus at least one guard digit, then divide the 256-bit numerator by the 128-bit denominator with Knuth Algorithm D. The quotient can exceed 128 bits during the process; the core must return a 192-bit quotient and round to 38 digits afterwards, not assume a 128-bit quotient.
3. The quotient estimate must not use C `u128` division. GCC lowers `num / vn1` to `__udivti3`, which the freestanding runtime link cannot resolve. Use a raw `divq` for the estimate, with the `hi >= divisor` case clamped to `UINT64_MAX` for Knuth's correction path. The 256/128 core is then a limb routine of `divq` estimates plus multiply/subtract corrections. [V link and disassembly; I implementation]
4. Round once. The benchmark's decimal path performs the 256/128 division and then divides the 192-bit quotient by 10 (two `divq` steps) to obtain the 38-digit result and the rounding digit; measured 24.3 ns median, 21.5 ns min, 25.8-27.9 ns across repeats.
5. Division by zero traps; checked variants return `Result`. The result's `dscale` and exponent follow the chosen scale policy.

Three proven division strategies exist in the surveyed implementations. Intel libbid estimates the quotient with binary floating point, subtracts `estimate * divisor`, corrects by comparison and subtraction, and returns a quotient plus a residual, rounding by comparing `2 * residual` with the divisor. .NET divides with trial quotient limbs (`Div96By32`, `Div128By64`, `Div128By96`) and scales in `10^9` chunks. libmpdec keeps one guard digit and applies a sticky-LSD adjustment. [C: https://github.com/gcc-mirror/gcc/tree/master/libgcc/config/libbid, https://github.com/dotnet/runtime/blob/main/src/libraries/System.Private.CoreLib/src/System/Decimal.DecCalc.cs, https://www.bytereef.org/mpdecimal/] Knuth D is chosen here because it needs no floating-point estimate and is straightforward to verify against an oracle; the alternatives are worth revisiting only if division dominates a profile. [I]

Measured division endpoints. Rows marked "libgcc" were built from revision-1 C that used `u128` `/` or `%`; their disassembly calls `__udivti3`/`__udivmodti4`. Rows marked "divq" are the revision-2 no-libgcc kernels, verified to reference no libgcc division symbol:

| Operation | Median ns/op | Min ns/op | Repeat range / notes | Implementation |
|---|---:|---:|---:|---|
| 64/64 | 1.775 | 1.536 | 1.51-1.78 | raw `div` |
| 128/64 (revision 1) | 3.425 | 3.315 | one saved run | **libgcc `__udivti3`**, not a raw `divq` |
| 128/64, fixed two steps | 4.695 | 4.259 | 4.50-4.74 | two `divq` |
| 128/64, `hi < d` one-step branch | 3.251 | 2.907 | 3.04-3.23 | one or two `divq` |
| 256/64 | 10.205 | 9.395 | 10.29-10.89 | four `divq` |
| Knuth D 256/128 (revision 1) | 26.928 | 25.329 | one saved run | **libgcc estimate** |
| Knuth D 256/128 (revision 2) | 22.033 | 20.269 | 24.32-26.73 | `divq` estimate |
| Decimal divide + round, revision 1 | 33.398 | 32.193 | one saved run | **libgcc-assisted** |
| Decimal divide + round, revision 2 | 24.268 | 21.535 | 25.77-27.91 | `divq` throughout |
| 128x128 mul + round k=37/38, revision 1 | 29.238 | 26.532 | one saved run | **libgcc-assisted** |
| 128x128 mul + round k=37/38, revision 2 | 25.815 | 23.380 | 26.98-30.02 | `divq` throughout |
| 128x128 mul + round k<=19 | 9.895 | 8.856 | 9.49-9.98 | 256/64, four `divq` |

The revision-1 rows have one saved run each; earlier unsaved revision-1 repeat parentheticals are removed. Only the 64/64 row is a raw `div` in one instruction. `divq` divides a 128-bit `RDX:RAX` by a 64-bit divisor and produces a 64-bit quotient; a 128/64 C division is a helper call in revision 1, and 256/128 is never a single instruction. The branchy 128/64 routine takes one `divq` when the high word is below the divisor and two otherwise; it matches or beats the libgcc helper while remaining linkable. Knuth D uses normalized `divq` estimates and correction steps. [V ISA semantics; V measurements; V disassembly]

A 256/64 path is now measured (four `divq` steps, 10.2-10.9 ns) and should be used for divisors below `2^64`, especially powers of ten up to `10^19`. The 256/128 kernel requires the divisor's high word to be nonzero; a divisor below `2^64` must be routed to 256/64 (or 128/64), never fed to the 256/128 core. A reciprocal-based full division was not implemented. Division is the largest single arithmetic cost and the best later optimization target, but the no-libgcc path is 18.2% faster on the 256/128 core, 27.3% on divide+round and 11.7% on multiply+round in the saved runs. [I]

### 2.4 Compare, equality and hash

Numeric comparison must ignore display scale. A practical sequence:

1. **Special-case zero.** Zero is canonical positive and has no meaningful digit count. Do not call `decimal_digits(0)`; its value is deliberately unspecified (the test implementation returns 0). Two zeros compare equal regardless of exponent or `dscale`.
2. Compare signs; different signs decide immediately (zero is positive).
3. Compare adjusted exponents `exponent + decimal_digits(coefficient)` for nonzero coefficients. A different adjusted exponent decides the magnitude without alignment.
4. If equal, align the coefficients by the (bounded) exponent difference and compare the 128-bit integers. Equal adjusted exponents imply the digit-count difference bounds the scaling exponent, so the scaled coefficient stays within 128 bits for two 38-digit coefficients.

Equality and hashing use the same normalized numeric value, not the stored `dscale`. This avoids the Java `BigDecimal.equals` trap where `1.0` and `1.00` are unequal despite `compareTo == 0`. [C Java semantics; I algorithm]

The compare path was not separately benchmarked; it is a handful of integer operations relative to the arithmetic measured here. A 22-case verification covers zero vs zero, zero vs positive and negative, equal values with different `dscale` (`1.0` vs `1.00`), equal adjusted exponents with alignment (`123.45` vs `123.450`), large exponent gaps, 38-digit boundaries, negative ordering and cancellation to canonical zero; all pass. [V verification; I performance]

### 2.5 Parse and format

The timed parse and format kernels are **fixed-width digit kernels**: 38 ASCII digits to a 128-bit coefficient and back, and 18 digits to a `u64`. They are not full literal parsers. Sign, decimal point, exponent, significant-digit counting and the 38-digit rejection/rounding decision are a separate layer; an untimed 22-case literal scanner test covers `-0`, `0.00`, `1.50`, `-1.50e2`, `1e38`, `.5`, `.5e-2`, `1.`, `+.5`, leading zeros, 38 nines, and rejection of a 39-digit literal. [V scope and tests]

Parsing:

- Recognize sign, digits, decimal point and exponent; count significant digits; reject or explicitly round above 38 digits according to the literal policy.
- Accumulate the coefficient with SWAR eight-digit chunks. In the revision runs the measured SWAR parse of 38 digits is 8.8-11.4 ns; a 19+19 chunk parse is 16.5-20.6 ns; a naive `u128` multiply-by-ten loop is 51.8-63.8 ns. For 18 digits, the SWAR path is 13.6-15.4 ns and the naive `u64` loop is 23.8-29.0 ns.
- Preserve the literal's display scale (`1.50` has dscale 2); apply the exponent and check the `int16` range. Parsing above 38 significant digits is an explicit error/rounded conversion, never silent truncation. [V design; V measurements]

Formatting:

- Produce decimal digits with base-`10^19` chunks: one raw `divq` split of the coefficient by `10^19` (the high word of a valid 38-digit coefficient is always below `10^19`), then two 19-digit chunks formatted with a two-digit table and divisions by 100. Measured 23.0-27.8 ns for 38 digits across the representative and repeat runs; base-`10^9` chunking is 30.8-37.5 ns; an 18-digit `u64` path is 9.2-11.0 ns.
- The revision-1 format row was **libgcc-assisted** (`u128` `%`/`/` by `10^19` lowered to `__udivmodti4`). The revision-2 row uses `divq` and references no libgcc division symbol. The cost is similar (the digit loops dominate), but the link works.
- Insert the decimal point from `dscale`, preserving trailing zeros; formatting must not normalize unless asked.

| Operation | Median ns/op | Min ns/op | Repeat range (median) |
|---|---:|---:|---:|
| parse 38 digits, naive `u128` loop | 58.888 | 55.197 | 58.9-63.8 |
| parse 38 digits, 19+19 chunks | 17.922 | 16.290 | 17.9-20.6 |
| parse 38 digits, SWAR 8-digit chunks | 10.369 | 8.834 | 10.3-11.4 |
| parse 18 digits, naive `u64` loop | 26.074 | 23.532 | 26.1-29.0 |
| parse 18 digits, SWAR | 15.437 | 13.251 | 15.1-15.4 |
| format 38 digits, base `10^19` (`divq`) | 26.878 | 23.208 | 26.6-27.8 |
| format 38 digits, base `10^9` | 35.989 | 34.094 | 36.0-37.5 |
| format 18 digits, `/100` pairs | 10.711 | 9.483 | 10.7-11.0 |

The parse table uses the last of three repeat runs as the representative; the main run under lighter load gave 8.8 ns for SWAR and 23.0 ns for base-`10^19` format. The within-run ratios are the stable result: SWAR parse is 5.5-5.8x faster than the naive `u128` loop and 1.7-1.8x faster than 19+19 chunks; base-`10^19` formatting is about 1.35x faster than base-`10^9`. [V]

The auxiliary "scale alignment (coefficients <= 58 bits)" rows in the revision-1 `bench_conv` output are **excluded**. The source comment was wrong: a 58-bit coefficient times `10^38` needs about 185 bits, so the `u128` products wrapped; the rows were never verified and are not used anywhere in this note. The exact alignment measurements are the revised 256-bit kernel in §2.1. [V source inspection; I exclusion]

### 2.6 64-bit fast path

Any coefficient below `2^64` fits a `u64`; that covers all 19-digit values (below `10^19`) and 20-digit values up to 18,446,744,073,709,551,615. The fast path is an internal branch on the high 64-bit coefficient word being zero; the value remains 24 bytes and the ABI does not change. Stages that rely specifically on a 19-digit bound (a `10^19` parse chunk, or a power-of-ten multiplier that must stay in 64 bits) must test the value against `10^19` rather than only the high word.

The table mixes kernel shapes; each row is labelled. All rows are from `bench_fast` unless noted.

| Operation | Measured cost | Kernel |
|---|---|---|
| 64-bit add | 0.43 ns | `bench_fast` store kernel |
| 128-bit add | 0.44-0.50 ns | `bench_fast` store kernel |
| 64x64 -> 64 low multiply | 0.25-0.29 ns | `bench_fast` store kernel |
| 64x64 -> 128 multiply | 0.43-0.49 ns | `bench_fast` store kernel |
| 128x128 -> 256 multiply | 2.52-2.66 ns | `bench_fast` store kernel; the `bench_mul` checksum-accumulate kernels report 2.0-2.6 ns for the same operation, a different loop and sum shape |
| 64/64 divide | 1.5-1.8 ns | `bench_div`, raw `div` |
| 128/64 divide | 3.0-4.7 ns | `bench_div`, one-step branch or fixed two `divq` |
| 256/64 divide | 10.2-10.9 ns | `bench_div`, four `divq` |
| 256/128 divide | 22.0-26.7 ns | `bench_div`, `divq` Knuth |

Add is not much cheaper at 64 bits because the loop is dominated by loads and stores, but multiply and divide are dramatically cheaper. The 64-bit fast path is therefore most valuable in multiplication, division, parse/format and mixed workloads. The ledger measurements (§5.6) show the aggregate effect. [V; I]

### 2.7 Overflow, underflow, division by zero, zero

- Coefficient above `10^38-1` after normalization is overflow; exponent outside `int16` is overflow/error. A rounded coefficient of exactly `10^38` normalizes (canonical zero-stripping) to coefficient 1 with exponent + 38; a single divide-by-ten instead leaves coefficient `10^37` with exponent + 1. Neither is an overflow of the type, though a database column may still reject it. [V design]
- There is no separate underflow value; an exponent below `int16` is an error, and rounding to zero is recorded as inexact when the API supports it. [V design]
- Zero is canonical positive, with no signed zero. [V design]
- Division by zero traps by default; checked arithmetic returns `Result`. [V design]

### 2.8 Rounding

The modes are half-away-from-zero (the default tie rule; often called half-up), half-even, half-toward-zero, away-from-zero (directed, always up in magnitude), toward-zero/truncate, floor, ceiling and `UNNECESSARY`. One rounding only: intermediate products and aligned sums keep guard digits and a sticky indicator, and the final step rounds. Do not round the multiply product and then round again during a scale change. [V design; I implementation rule]

The rounding decision itself is a remainder comparison against half the divisor (with an increment for half away from zero); no double rounding and no pre-rounding of the 256-bit intermediate. The 256/64 path compares a 64-bit remainder with `ceil(d/2)` after four `divq` steps; the 256/128 path compares the full remainder. Boundary tests cover zero and one through the rounding path, `10^38-1` unchanged, a half-way case that rounds up to exactly `10^38`, and both normalization readings. [V verification; I]

## 3. Instruction-set findings

### 3.1 There is no x86-64 hardware decimal floating point

- No x86-64 instruction performs IEEE 754 decimal32/64/128 arithmetic. Intel's decimal128 support is a software library: the Intel Decimal Floating-Point Math Library describes itself as a software implementation of IEEE 754 decimal arithmetic. [C: https://www.intel.com/content/www/us/en/developer/articles/tool/intel-decimal-floating-point-math-library.html]
- GCC on x86 uses libbid/BID for `_Decimal32/64/128`; POWER and S/390 use DPD. Locally, the revision-1 `_Decimal128` benchmark links `__bid_*` routines from libgcc. [C: https://gnu.googlesource.com/gcc/+/refs/tags/release-12.2.mpacbti-rel1/config/dfp.m4; V local link]
- The x86 BCD adjust instructions (`AAA`, `AAS`, `AAM`, `AAD`, `DAA`, `DAS`) are not valid in 64-bit mode; x87 `FBLD`/`FBSTP` only convert packed BCD to/from 80-bit floating point. There is no packed-BCD multiply or divide. [C: https://www.felixcloutier.com/x86/daa, https://www.felixcloutier.com/x86/fbld, https://learn.microsoft.com/en-us/windows-hardware/drivers/debugger/x64-instructions]
- CPUID leaf 7 has no decimal floating-point feature bit. [C: https://www.felixcloutier.com/x86/cpuid]

### 3.2 POWER and IBM Z have hardware decimal, but it is 34-digit DPD

- Power ISA Book I Chapter 5 specifies decimal floating point in short/long/extended formats, DPD-encoded, with `dadd`/`dmul`/`ddiv` and related instructions. [C: Power ISA v3.1C, http://wiki.raptorcs.com/w/images/5/5f/OPF_PowerISA_v3.1C.pdf]
- IBM Z has a decimal floating-point facility with DPD encoding and `ADTR`/`MDTR`/`DDTR`-style instructions. [C: https://public.dhe.ibm.com/software/dw/linux390/perf/dfp.pdf, https://www.ibm.com/support/pages/sites/default/files/2022-09/SA22-7871-11.pdf]
- Hardware decimal128 is a **34-digit** format, not 38 digits; a true 38-digit coefficient still needs a 128-bit binary integer and software 128x128 -> 256 multiply. Hardware DFP is not a shortcut for this type. [C formats; I consequence]

### 3.3 Scalar instructions and measured usefulness

| Facility | What it does | Single 38-digit value? | Batch? | Measured / documented |
|---|---|---|---|---|
| `MUL` + `ADC` | 64x64 -> 128 and carry chains | Yes; compiler-generated baseline | Yes, scalar loop | 128x128 -> 256: 2.0-2.1 ns (default build), 2.2-2.3 ns with `-mbmi2 -madx`. Zen 4 `MUL r64` latency 3/4/3, throughput 1.00 cycles per instruction. [V measurements; C: https://www.uops.info/html-instr/MUL_R64.html] |
| BMI2 `MULX` | 64x64 -> 128 without flags | Yes, but no measured gain here | Slightly easier scheduling | 2.1-2.35 ns; Zen 4 latency 4/3, throughput 1.00. [V measurements; C: https://www.uops.info/html-instr/MULX_R64_R64_R64.html] |
| ADX `ADCX`/`ADOX` | Two independent carry chains | Marginal; best 1.96-2.08 ns with `-mbmi2 -madx`, versus 2.17-2.31 ns baseline in the same build; default-build ADX was 2.1-2.3 ns versus 2.0-2.1 ns baseline | Helps larger multi-limb loops | Not worth a higher minimum CPU for one product. Zen 4 `ADCX`/`ADOX` 1-cycle latency, 0.38 cycles per instruction throughput. [V measurements; C: https://www.uops.info/html-instr/ADCX_R64_R64.html, https://www.uops.info/html-instr/ADOX_R64_R64.html] |
| `DIVQ` (`div`) | 128/64 -> 64 quotient and remainder | Yes, but 256/128 needs several steps | No | 64/64 1.5-1.8 ns, 128/64 3.0-4.7 ns, 256/64 10.2-10.9 ns, 256/128 22.0-26.7 ns. **Only 64/64 is one raw `div`; C `u128/u64` is a libgcc call, not `divq`.** Zen 4 latency 9-17, throughput 7.00 cycles per instruction. [V measurements and disassembly; C: https://www.uops.info/html-instr/DIV_R64.html, https://www.felixcloutier.com/x86/div] |
| AVX2 | 32x32 -> 64 vector multiply; no 64x64 -> 128 | No | Only with 32-bit limb decomposition | Not useful for this coefficient; no 64-bit full multiply. [I] |
| AVX-512 IFMA `VPMADD52LUQ/HUQ` | 52-bit lane multiply-add into 64-bit accumulators | No; single value 29-30 ns | Yes, if results stay vector-resident | Batch of 8: 0.77-0.88 ns/product in the default build (0.89 with `-mbmi2 -madx`); batch plus pack: 2.9-3.1 ns/product; single value: 29-30 ns. [V measurements; C: https://www.felixcloutier.com/x86/vpmadd52luq] |
| Legacy BCD adjust | `DAA`/`DAS`/`AAA`/`AAS` | Unusable in 64-bit mode | No | #UD in 64-bit mode; `FBLD`/`FBSTP` only load/store BCD. [C] |

Host flags include `bmi1 bmi2 adx avx2 avx512f avx512ifma avx512dq avx512vl avx512bw avx512vbmi avx512_vbmi2`; the host is a Zen 4 AMD Ryzen 7 8745HS. [V: `/proc/cpuinfo`]

The IFMA result deserves emphasis. A batch of eight independent products kept in vector registers is 2.4-2.7x faster than scalar. Packing each product into the ordinary 256-bit result destroys the gain. A single value is an order of magnitude slower. IFMA is therefore a possible bulk/array optimization later, never a scalar arithmetic path. [V; I]

Intel libbid V2.5 contains no SSE or AVX intrinsics: a source search of its decimal arithmetic files for `_mm_`, `__m128` and AVX found nothing, and no AVX-512 IFMA decimal128 library was found in this research. IFMA appears in the literature for big-integer multiplication and binary-to-decimal conversion, not for fixed decimal coefficients. [C: https://www.netlib.org/misc/intel/, https://github.com/gcc-mirror/gcc/tree/master/libgcc/config/libbid]

### 3.4 AVX-512 feature checks, if a batch path is ever added

- CPUID leaf 7, subleaf 0, EBX: BMI2 bit 8, AVX512F bit 16, ADX bit 19, AVX512_IFMA bit 21. [C: https://www.felixcloutier.com/x86/cpuid]
- AVX-512 also requires OSXSAVE (leaf 1 ECX bit 27) and XCR0 bits 7:5 and 2:1 set. GCC's own detection checks exactly those conditions. [C: https://github.com/gcc-mirror/gcc/blob/master/gcc/common/config/i386/cpuinfo.h]
- Detect once at startup with manual CPUID, cache the result, and branch or use a function pointer. Never execute CPUID per operation. [V measurement; I rule]

## 4. Dispatch versus baseline

### 4.1 CPUID cost

On this WSL2 host, a CPUID leaf-7 read costs 463.4 ns per operation (459.2 ns minimum) in the saved run; a cached feature-flag test costs 0.444 ns (0.438 ns minimum). The ratio is 463.424/0.444 = 1044x on the medians and 459.245/0.438 = 1049x on the minima, so roughly 1050x. The absolute CPUID cost is inflated by virtualization (this is an [O] environment result, not a bare-metal number), but the conclusion is robust: CPUID is three orders of magnitude more expensive than a cached branch and must never be in the per-value path. [V/O]

### 4.2 `target_clones` and ifunc do not work in the freestanding runtime

The obo runtime is linked with `ld -T runtime/rt.ld`, not with the GCC driver, and has a custom `_entry`; no libc startup code runs. [V: `obo/Makefile`, `obo/runtime/entry.S`, `obo/runtime/rt.ld`]

Observed:

- With `-nostdlib`, a `target_clones("default","avx512f")` function fails to link: undefined `__cpu_indicator_init` and `__cpu_model` from the generated resolver. [V]
- With the freestanding `-nostdlib -static -lgcc` link, it links, but the binary hangs on the first call (`timeout 5` returns 124). The object contains an `R_X86_64_IRELATIVE` relocation at GOT address `0x405000` with addend `0x402110` (`work.resolver`), and the GOT slot contains `0x401000`, the address of the PLT stub itself. Without glibc's startup applying IRELATIVE relocations, the PLT jumps to itself forever. The hang requires the freestanding static link, not a normal static glibc target; a dynamically linked control would have the loader apply the relocation. [V: `readelf -r`, `objdump -d`, observed run]
- `__builtin_cpu_supports("avx512f")` without libgcc fails to link (`__cpu_model` undefined). With `-lgcc` it links but returns false if `__cpu_indicator_init` has not run; a test program exited 7 with the feature present. Calling `__cpu_indicator_init()` manually before the test returned true (exit 0). Libgcc normally runs this from a constructor of priority 101, which a custom `_entry` does not run. [V local test; C: https://github.com/gcc-mirror/gcc/blob/master/libgcc/config/i386/cpuinfo.c]
- Independently of dispatch, the same freestanding link cannot resolve libgcc's 128-bit division helpers. A runtime helper written as `u128 coefficient / u64 divisor` references `__udivti3` (and `%` references `__umodti3`), and `ld -T runtime/rt.ld` fails with undefined references. This is why the division recommendation is explicit `divq`/limb code. §5.11 has the probe output. [V]

GCC documents that function multiversioning relies on the ELF indirect-function extension; static glibc's startup applies the IRELATIVE relocations before `main`, which is exactly what the freestanding runtime lacks. [C: https://gcc.gnu.org/onlinedocs/gcc/Function-Multiversioning.html, https://sourceware.org/pipermail/libc-alpha/2012-August/033716.html]

Consequences:

- Do not use `target_clones` or ifunc in the runtime.
- Do not depend on libgcc's `__cpu_model`; the runtime does not link libgcc, and the constructors do not run.
- If a future batch path needs a feature check, use manual CPUID once at startup and a cached flag/function pointer. Manual CPUID is the only feature test that works without libgcc. [I]

### 4.3 Minimum CPU and code size

The project compiles the runtime with `-O2 -ffreestanding -fno-builtin -nostdlib -fno-pic -fno-pie -fno-stack-protector -fno-asynchronous-unwind-tables -mcmodel=small -fno-tree-loop-distribute-patterns -Wall` and no `-march`. [V: `obo/Makefile`]

That baseline produces portable x86-64 (SSE2) code, which is the right minimum for a compiler runtime. The measured differences among baseline, MULX and ADX do not justify raising it. [V; I]

Representative code sizes for the benchmark kernels (not the final runtime):

| Kernel | Baseline | `-mbmi2 -madx` |
|---|---:|---:|
| 128x128 -> 256 multiply kernel | 232 B | 234 B |
| Hand-written ADX multiply kernel | 255 B | 255 B |
| IFMA batch-of-8 kernel | 643 B | 643 B |
| 38-digit ledger path (mul + 256/128 + round twice) | 627 B | 654 B |

`target_clones` duplicates the function for each clone and adds a resolver and an ifunc PLT/GOT entry, so it would multiply any duplicated arithmetic code and add an unresolved-relocation hazard in this link. [V sizes; I consequence]

## 5. Measurements

### 5.1 Methodology and exact commands

- Host: AMD Ryzen 7 8745HS (Zen 4), 16 logical CPUs, WSL2 kernel `6.18.54.1-microsoft-standard-WSL2`, GCC `13.3.0` (Ubuntu 13.3.0-6ubuntu2~24.04.1). BogoMIPS 7585.86; TSC approximately 3.79 GHz, consistent with the ratio of TSC/op to ns/op in every table. [V/O]
- Timing: `CLOCK_MONOTONIC_RAW` for ns and `rdtsc` for TSC; five warmup passes, 21 timed samples per benchmark, median and minimum reported, samples sorted with `qsort`. Counters use N=512 values and REPS=256 (131,072 operations per sample) unless noted; the slow 38-digit ledger uses REPS=64. All timed runs are pinned with `taskset -c 3`. [V]
- The host was not idle: load average was roughly 5-15 during the original and revision runs, so medians vary by roughly 10% between repeats and occasionally by much more. The revision repeat logs are saved (see §5.11); tables show a representative median (minimum) and, where useful, the range of medians across runs. Differences below about 10% are not treated as real. The numbers are Zen 4/WSL2 results, not a cross-CPU survey. [O]
- Build: non-PIE harness binaries, dynamically linked against libc (the freestanding runtime link is separate), `-O2 -fno-pic -fno-pie -no-pie -mcmodel=small -fno-tree-vectorize -std=gnu11`, no `-march`. The IFMA kernels carry their own `target("avx512f,avx512ifma")` attribute. `-fno-tree-vectorize` keeps the scalar loops scalar. The revised conversion benchmark uses `-Wno-stringop-overflow` to silence a known GCC fixed-width-formatter range false positive. [V]
- Arithmetic kernels were checked against Python big-integer vectors: 1,000 128x128 -> 256 products; the **same** 1,000 products fed to IFMA in batches of eight (not a second independent 1,000); 512 256/128 divisions; 256 k=37/38 multiply-and-round cases; 544 128/64 division cases (32 boundary/power-of-ten plus 512 random, generated by Python); 522 256/64 cases; 520 small-k multiply-and-round cases; 519 exact alignment cases; 45 nonzero d>38 sticky cases plus one `b = 0` control; 512 38-digit plus 512 18-digit parse/format random cases; and 512 ledger cases **per tier** (three files). Fixed-width boundary strings (13), full literals (22), compare cases (22) and the boundary arithmetic cases (zero/one, `10^38-1`, `10^38` normalization, divide 0/1) are C self-tests with hard-coded expectations; the 77-digit alignment boundary is checked by `revision/verify_align_bound.c`. All reported 0 bad. [V]
- Revision-1 and revision-2 binaries are kept side by side. The revision-1 division, conversion and ledger binaries contain and call the libgcc division helpers (the database benchmark calls `__udivti3`; the division and ledger benchmarks call `__udivti3`/`__udivmodti4`); the revision-2 no-libgcc binaries contain none. [V nm/objdump logs]

Exact build and run commands for the revision-2 kernels (sources under `/tmp/obo-decimal-arith/revision/`; the original sources are one directory up):

```text
gcc -O2 -fno-pic -fno-pie -no-pie -mcmodel=small -fno-tree-vectorize -std=gnu11 \
    -I. bench_div2.c -o bench_div_nolibgcc
gcc -O2 -fno-pic -fno-pie -no-pie -mcmodel=small -fno-tree-vectorize -std=gnu11 \
    -Wno-stringop-overflow -I. bench_conv2.c -o bench_conv_nolibgcc
gcc -O2 -fno-pic -fno-pie -no-pie -mcmodel=small -fno-tree-vectorize -std=gnu11 \
    -I. bench_align2.c -o bench_align2
gcc -O2 -fno-pic -fno-pie -no-pie -mcmodel=small -fno-tree-vectorize -std=gnu11 \
    -I. bench_ledger2.c -o bench_ledger2
gcc -O2 -fno-pic -fno-pie -no-pie -mcmodel=small -fno-tree-vectorize -std=gnu11 \
    -I. bench_dbconv2.c -o bench_dbconv2
gcc -O2 -fno-pic -fno-pie -no-pie -mcmodel=small -fno-tree-vectorize -std=gnu11 \
    -I. bench_abi2.c -o bench_abi2
gcc -O2 -fno-pic -fno-pie -no-pie -mcmodel=small -fno-tree-vectorize -std=gnu11 \
    -I. bench_dec128_fair.c -o bench_dec128_fair
gcc -O2 -std=gnu11 verify_cmp.c -o verify_cmp
gcc -O2 -fno-pic -fno-pie -no-pie -mcmodel=small -fno-tree-vectorize -std=gnu11 \
    -I. verify_align_bound.c -o verify_align_bound
taskset -c 3 ./bench_div_nolibgcc
...
```

The revision-2 sources are `revision/bench_div2.c` (with `revision/dec_div.h`), `revision/bench_conv2.c`, `revision/bench_align2.c`, `revision/bench_ledger2.c`, `revision/bench_dbconv2.c`, `revision/bench_abi2.c`, `revision/bench_dec128_fair.c`, `revision/verify_cmp.c`, `revision/probe_runtime_nolibgcc.c`, `revision/probe_runtime_libgcc.c`, `revision/probe_all_primitives.c` and `revision/verify_align_bound.c`; the vector generators are `revision/gen_vectors2.py` and `revision/gen_align2.py`; the freestanding probes and link evidence are in `revision/`. Revision-1 sources remain at the top level. None of this is in the repository. [V]

### 5.2 128x128 -> 256 multiply

Default build (`-O2`, no `-march`), representative run:

| Variant | Median ns/op | Min ns/op | TSC/op | Mops/s |
|---|---:|---:|---:|---:|
| Baseline C (`unsigned __int128` partial products) | 2.049 | 1.869 | 7.774 | 487.93 |
| BMI2 `mulx` | 2.145 | 2.050 | 8.138 | 466.10 |
| BMI2 `mulx` + ADX, hand-written | 2.238 | 2.083 | 8.487 | 446.93 |
| AVX-512 IFMA batch of 8, vector accumulator | 0.769 | 0.766 | 2.915 | 1301.13 |
| AVX-512 IFMA batch of 8, then pack to 256-bit results | 3.052 | 2.776 | 11.577 | 327.61 |
| AVX-512 IFMA broadcast, one value across 8 lanes | 6.786 | 6.254 | 25.738 | 147.37 |
| AVX-512 IFMA single value | 29.011 | 27.996 | 110.040 | 34.47 |
| Baseline C with a serial dependency chain | 1.941 | 1.857 | 7.363 | 515.15 |

Repeated default-build runs: baseline 1.98-2.12 ns, MULX 2.14-2.35 ns, ADX 2.11-2.35 ns, IFMA batch 0.769-0.884 ns (0.77-0.88 rounded). The global `-mbmi2 -madx` runs were 0.807-0.893 ns (0.81-0.89). [V]

The "serial dependency chain" control is not the same loop as the baseline: it feeds only `p.w0` from the previous product into the next iteration's first operand, so it creates a single low-word dependency chain rather than a full 256-bit product dependency. It is a shape control, not a like-for-like latency baseline. [V source inspection]

Build with global `-mbmi2 -madx`:

| Variant | Median ns/op | Min ns/op | TSC/op | Mops/s |
|---|---:|---:|---:|---:|
| Baseline C | 2.314 | 2.098 | 8.778 | 432.12 |
| BMI2 `mulx` | 2.303 | 2.156 | 8.734 | 434.27 |
| BMI2 `mulx` + ADX, hand-written | 2.083 | 1.927 | 7.901 | 480.03 |
| AVX-512 IFMA batch of 8, vector accumulator | 0.874 | 0.813 | 3.314 | 1144.50 |
| AVX-512 IFMA batch of 8, then pack | 3.882 | 2.770 | 14.724 | 257.61 |
| AVX-512 IFMA broadcast, one value across 8 lanes | 8.111 | 6.522 | 30.766 | 123.29 |
| AVX-512 IFMA single value | 29.180 | 28.071 | 110.678 | 34.27 |
| Baseline C with a serial dependency chain | 3.961 | 2.015 | 15.023 | 252.48 |

Repeated `-mbmi2 -madx` runs: baseline 2.17-2.31 ns, MULX 2.20-2.32 ns, ADX 1.96-2.08 ns, IFMA batch 0.81-0.89 ns. The ADX advantage in this build (9.7-11.6% over baseline across the saved runs) is the only place ADX looked better, and the best default-build baseline (1.98 ns) already matches the best ADX result (1.96 ns). The serial-chain outlier in the first global-flags run (3.961 ns) did not reproduce; repeats were about 2.0 ns. [V/O]

### 5.3 Division, decimal divide/round, and the finite `_Decimal128` comparison

The no-libgcc rows are in the division table in §2.3. Only two things need emphasis here:

- **Revision 1's 128/64 row was mislabelled.** It was a C `unsigned __int128 / uint64_t` expression, which GCC lowers to `__udivti3`; the binary contains `__udivti3` and calls it. It was never a raw `divq`. Likewise the 256/128 Knuth estimate used `num / vn[1]` -> libgcc, and the decimal divide/round row inherited it. The no-libgcc equivalents are 3.0-4.7 ns (128/64), 22.0-26.7 ns (256/128), and 24.3-27.9 ns (divide + round). The 64/64 row is the only raw single `div`. [V disassembly, §5.11]
- **The `_Decimal128` reference must use finite operands.** In the original multiply loop, `acc *= a + i` becomes decimal128 Infinity after about five of the 256 reps (the growth probe is finite through rep index 4 and Infinity from index 5 onward). The original divide loop builds `a + i` and `b + (i & 31)` inside the timed loop; a setup-only control costs 66.6 ns/op median (60.1 ns min). The original 46.1 ns multiply and 184.4 ns divide are therefore not finite-operand costs.

Fair finite measurements with precomputed 34-significant-digit operands and a volatile store per result (store-only control 0.45-1.05 ns/op):

| Loop shape | Median ns/op | Range across runs |
|---|---:|---:|
| multiply, store each product | 90.352 | 76.9-119.3 (three runs 76.9-90.4; one 119.3 under load) |
| multiply, four independent chains | 93.437 | 89.6-103.2 |
| divide, store each quotient | 77.343 | 75.0-77.3 (min 68.5) |
| divide, four independent add chains | 108.315 | 108.3-128.7 |
| divide, serial dependency chain | 84.063 | 83.5-97.6 |
| setup-only control (no divide) | 66.618 | 60.1 (minimum; one run) |
| original multiply loop (Infinity after ~5 reps) | 51.584 | 46.1-72.2 |
| original divide loop (setup in loop) | 189.922 | 184.4-228.1 |

Corrected gaps against the revision-2 no-libgcc kernels: divide is about (75-77) / 24.3 = **3.1-3.2x**; multiply is about (77-90) / 25.8 = **3.0-3.5x** (2.6-3.1x if one compares against the older 29.2 ns rounded-multiply figure). The revision-1 claim of 5.5x divide and 1.6x multiply was an artifact of setup and Infinity; the direction (libbid slower) stands. [V; I comparison]

The GCC `_Decimal128` loop includes full IEEE decimal semantics, and the obo kernels are the inner coefficient arithmetic without IEEE flags, NaN/Inf or exponent metadata, so the comparison is directional rather than exact like-for-like. [V; I]

### 5.4 Parse and format

See §2.5 for the table and the fixed-width scope. The SWAR parser is 5.5-5.8x faster than the naive `u128` loop and 1.7-1.8x faster than 19+19 chunks. Base-`10^19` formatting is about 1.35x faster than base-`10^9`; the two-digit table and division by 100 matter more than the chunk base. The revision-2 base-`10^19` formatter uses a raw `divq` split and no libgcc; the revision-1 formatter called `__udivmodti4`. [V; I]

### 5.5 Exponent alignment plus add

See §2.1. The exact kernel multiplies a coefficient below `10^38` by a table-selected power of ten, forms a 256-bit result and adds the other coefficient; the 519 exact vectors satisfy the implementation guard `digits(a) + d <= 76` and report 0 bad. The 45 nonzero sticky vectors plus one `b = 0` control satisfy `digits(a) + d > 76`, verify that the smaller operand lies below the 38-digit window (`digits(b) - 1 - d < digits(a) - 38`), and report 0 bad. The guard test confirms `d=75` with a one-digit `a` is exact while `d=39` with a 38-digit `a` is sticky. The sharp 256-bit fit extends to `digits(a) + d <= 77`; `verify_align_bound.c` computes `a = 10^38-1, d = 39, b = 10^38-1`, confirms bit length 256 and matching limbs, and notes that the conservative guard still routes it to the sticky path. The measured cost is 1.6-1.9 ns for a small exponent difference, 3.1-3.5 ns at a difference of 38, 3.05-4.55 ns for a variable exact difference, and 0.53-1.09 ns for the sticky-only path. [V]

### 5.6 64-bit fast paths and ledger loop

Fast-path microbenchmarks, observed median range across the main run and repeats (`bench_fast`, per §2.6):

| Operation | Median ns/op (main + repeats) |
|---|---:|
| 64-bit add | 0.426-0.492 |
| 128-bit add | 0.438-0.503 |
| 64x64 -> 64 low multiply | 0.246-0.289 |
| 64x64 -> 128 multiply | 0.433-0.488 |
| 128x128 -> 256 multiply | 2.520-2.656 |

One first run reported 0.877 ns for 64x64 -> 128 and 3.711 ns for 128x128 -> 256; those were not reproducible and are excluded as noise. [V/O]

Ledger-style loop, per line item (price times quantity, tax division by a runtime divisor, accumulation). Revision 1 used libgcc division in the 128-bit and 38-digit tiers; revision 2 uses the `divq` kernels:

| Tier | Revision 1 median (libgcc in the 128/38 tiers) | Revision 2 median (no libgcc) | Revision 2 repeat range |
|---|---:|---:|---:|
| 64-bit exact invoice (mul, divide by 10000) | 1.693 | 1.756 | 1.54-1.88 |
| 128-bit exact invoice (mul, 128/64) | 4.597 | 4.565 | 4.57-4.91 |
| 38-digit path (mul, 256/128, round twice) | 55.341 | 49.914 | 49.1-53.9 (one 66.3 high-load run; min 41.7) |

The 64-bit tier is about 2.55-3.07x faster than the 128-bit exact tier and about 28-35x faster than the full 38-digit path. The full path's two 256/128 divisions dominate, as expected from §5.3. The 38-digit median improved by about 10% with the no-libgcc kernels. [V; I]

### 5.7 24-byte ABI costs

The value is a 24-byte struct `{ uint64_t w0, w1, meta; }`; the helper takes a hidden destination pointer and two operand pointers and is marked `noinline`. The indirect path calls through a `volatile` function pointer. **Every path checksums all 24 bytes (`w0 ^ w1 ^ meta`), and the copy kernels checksum every destination element**, so every half of the add and every copy destination is observably read; this is what removes the revision-1 dead-code elimination in these builds. The revision-1 inline row checksummed only `w0 ^ meta`; its 0.361 ns was dead-code eliminated and is not comparable. [V source and disassembly]

| Variant | Median ns/op | Min ns/op | Repeat median range |
|---|---:|---:|---:|
| Inline 128-bit add + metadata copy (all 24 bytes read) | 0.442 | 0.441 | 0.425-0.503 |
| No-inline helper, direct call | 1.159 | 1.148 | 1.268-1.689 |
| No-inline helper, indirect call | 1.323 | 1.321 | 1.310-1.777 |
| 24-byte struct array copy (bulk) | 0.178 | 0.178 | 0.178-0.229 |
| 8-byte word array copy (bulk) | 0.067 | 0.067 | 0.067-0.085 |

The direct/indirect call overhead over inline is about 0.7-1.2 ns for this trivial add. The indirect call is sometimes slower than the direct call and sometimes equal; the difference is within host noise. A cached dispatch pointer would therefore not be free, but the decisive reason not to dispatch is that there is no useful scalar variant. The copy rows measure bulk array-copy throughput: each rep copies 512 elements and the checksum reads every destination element, so they are not a single per-value struct assignment. A bulk 24-byte-element copy costs about 2.7x a bulk 8-byte-element copy. [V; I]

For multiply and divide, the helper call overhead is negligible relative to 25-30 ns. For add, the call is most of the cost when the exponents are already equal (about 1.2 ns helper versus 0.44 ns inline); this is worth revisiting only after the language feature exists and profiles show add as hot. [V; I]

### 5.8 Database magnitude conversion

These are **magnitude-conversion kernels**, not complete adapters. The revision-1 Oracle and PostgreSQL rows used libgcc 128/64 division; ASE is shift-only and the TDS row is a copy. Revision 2 uses the `divq` routine for the digit-extraction rows. The input is a value below `2^98` (a column-scale magnitude), extracted with a fixed 19-step (base-100) or 10-step (base-10000) loop.

| Target | Encoding | Work | Revision 1 median (libgcc only for Oracle/PostgreSQL) | Revision 2 median (divq) | Revision 2 range |
|---|---|---|---:|---:|---:|
| SQL Server TDS | little-endian 128-bit magnitude + sign | 16-byte magnitude copy | 0.115 | 0.130 | 0.117-0.154 |
| Sybase ASE | base-256 magnitude | 16 shifts | 7.921 | 8.637 | 8.35-10.74 |
| PostgreSQL `numeric` | base-10000 digits | 10 divmod128/64 | 38.051 | 24.153 | 23.9-24.6 |
| Oracle `NUMBER` | base-100 digits | 19 divmod128/64 | 69.150 | 50.439 | 48.8-50.7 |

Scope qualifications that must not be dropped:

- The 0.12-0.15 ns SQL Server row is a **16-byte magnitude copy at column scale**. It excludes scale alignment, sign-byte conversion beyond the copy, tier-width selection/truncation, validation and TDS row framing. It is not a full TDS decimal conversion cost.
- The Oracle and ASE rows exclude exponent, sign, bias, complement/terminator and protocol framing work; they measure digit or byte extraction only. ASE's exact byte order remains unverified.
- The conversion cost is dominated by the number of division steps. SQL Server's magnitude copy is effectively free and Sybase is under 11 ns; PostgreSQL and Oracle cost tens of nanoseconds per value at this column scale. Batch/SIMD conversion is not worth initial effort: IFMA does not implement the base-100/base-10000 divisions, and scalar ILP across independent values is the realistic later optimization if bulk loads become hot. Database adapters are deferred per the approved design. [V measurements; I]

### 5.9 CPUID and dispatch

| Operation | Median ns/op | Min ns/op |
|---|---:|---:|
| CPUID leaf 7 | 463.424 | 459.245 |
| Cached feature-flag test | 0.444 | 0.438 |

The CPUID absolute cost is WSL2-specific; the ratio, about 1050x, is the actionable result. [V/O]

### 5.10 Not measured or unavailable

- Reciprocal-constant rounding and estimate-and-correct division were not implemented or timed.
- The 128/64 fixed two-step path and the branchy one-step path are measured; a broader 256/64 with a leading-zero-limb skip was not separately optimized or timed.
- The compare path was verified for semantics but not benchmarked.
- The full runtime literal parser (sign/point/exponent and significant-digit counting) was tested for correctness but not timed; only fixed-width digit kernels are measured.
- No bare-metal or non-Zen-4 CPU was available; all timings are WSL2 on one Zen 4 host under variable load.
- AVX-512 IFMA was measured only in the batch kernels shown. No AVX2 kernel was written because AVX2 has no 64x64 -> 128 multiply.
- `target_clones`/ifunc could not be made to work in the freestanding link; that is a functional result, not a performance result.
- Database formats were measured only as magnitude-conversion kernels; no database adapter or protocol code was written or run.
- General IEEE decimal semantics (NaN/Inf, all five libbid rounding modes) were not tested; only obo's finite coefficient paths were checked.
- **Coverage gaps in revision 1, and where revision 2 stands.** Revision 1 had no zero/one/`10^38-1`/`10^38`-normalization cases in the arithmetic vectors, no d>38 sticky cases, no small-k (`k <= 19`) rounding vectors, no short/zero/38-nines/leading-zero strings, and no sign/point/exponent literal cases. Revision 2 adds all of these as correctness checks (Python-generated vectors for the big-integer arithmetic; C self-tests for the fixed literal, string and compare expectations); the full literal parser remains untimed, and the compare path remains unbenchmarked.

### 5.11 Review evidence and reproducibility

All paths are under `/tmp/obo-decimal-arith/`.

**Freestanding link evidence.** `revision/probe_runtime_libgcc.c` defines `rt_dec_div_pow10` as `u128 / u64`; its object has undefined `__udivti3` and `__umodti3`. Linking `entry.o runtime.o probe_runtime_libgcc.o` with `ld -T obo/runtime/rt.ld` fails with those undefined references. `revision/probe_runtime_nolibgcc.c` uses `revision/dec_div.h`; its object has no undefined division symbol, the same real link succeeds, the resulting ELF contains no `__udivti3`/`__udivmodti4`/`__umodti3`, and the disassembly shows raw `div` instructions (a one-step branch when the high word is below the divisor, two steps otherwise). A standalone `_entry` probe linked through `rt.ld` runs and exits 0 with the expected 128/64 quotient and remainder. An all-primitives probe references `div128_64`, `div128_64_fast`, `div256_64`, `div192_10`, `divq64_checked` and `div256_128_nolibgcc` and links through the same runtime objects with no libgcc division symbol, so the link proof covers every recommended primitive rather than a representative. The standalone exit, all-primitives link and `verify_align_bound` output are saved in `results/nolibgcc_link_evidence.txt`. [V]

**Symbol checks.** `results/nolibgcc_symbols.txt` records the revision-1 binaries defining and calling the libgcc division helpers (division, conversion, ledger and database benchmarks), and the revision-2 objects and binaries with none of those symbols and no calls to them. The saved file contains the `nm -u` outputs and targeted `objdump -d` call checks (not full disassemblies) for the division, conversion, alignment, ledger and database objects. [V]

**Saved runs.** Revision-2 logs: `results/bench_div_nolibgcc.txt` and `_repeat.txt`; `bench_conv_nolibgcc.txt` and `_repeat.txt`; `bench_align2.txt` and `_repeat.txt`; `bench_ledger2.txt` and `_repeat.txt`; `bench_dbconv2.txt` and `_repeat.txt`; `bench_abi2.txt` and `_repeat.txt`; `bench_dec128_fair.txt` and `_repeat.txt`; `dec128_setup.txt`; `verify_cmp.txt`; `verify_align_bound.txt`; `host_revision.txt`. Revision-1 logs remain in `results/` as well. The repeat logs are saved, not merely summarized. [V]

**Vector generators.** `revision/gen_vectors2.py` generates the 128/64, 256/64 and small-k vectors; `revision/gen_align2.py` generates the exact and sticky alignment vectors. Both are deterministic (fixed seed) and assert the arithmetic bounds in Python before writing. [V]

## 6. Existing implementations and lessons

This section builds on the primary sources already collected in `decimal-research-2026-10-28.md` §8; the URLs below were re-checked for this note. No implementation code was copied; the licenses differ and the project is Apache-2.0. [C citations; I license rule]

| Implementation | Representation and size | Arithmetic strategy | Semantics | Lesson for obo |
|---|---|---|---|---|
| **Intel Decimal Floating-Point Math Library (libbid, decimal128 BID)** | Sign + 14-bit biased exponent (bias 6176) + 113-bit binary coefficient; 34 digits; 16 B; software | 128x128 -> 256 from two 64x128 partial products; kernel chosen by digit count `q1+q2` (<=19 fits `u64`, 20 uses 64x64 -> 128, <=38 128-bit, 39 uses 128/192, <=57 256-bit); rounding by precomputed reciprocal constants rather than division; divide by binary-float estimate plus residual correction; multiply shares the FMA kernel; no SIMD in V2.5 | IEEE 754 decimal, flags, NaN/Inf, five rounding modes | Closest fixed binary-coefficient reference; the digit-count kernel split directly supports obo's 64-bit fast path, but 34 digits and full IEEE semantics are more than this type needs. [C: https://www.netlib.org/misc/intel/, https://github.com/gcc-mirror/gcc/tree/master/libgcc/config/libbid, https://www.scitepress.org/Papers/2006/13209/13209.pdf] |
| **GCC `_Decimal128` on x86-64** | libbid/BID software on x86; libdecnumber/DPD on PowerPC and other targets (base-1000 units, `DECDPUN=3`) | Same libbid kernels on x86; DPD arithmetic in libdecnumber elsewhere | IEEE decimal via fenv; 34 digits | Fair finite measurements: about 75-77 ns per divide and 77-90 ns per multiply (store shapes; 108-129 ns when an add is included), versus 24.3/25.8 ns for the specialized 38-digit kernels. The original 46.1/184.4 ns figures were Infinity- and setup-distorted. GCC is GPLv3 with the Runtime Library Exception; the bundled libbid files carry Intel's BSD-style notice. General IEEE software is not a performance baseline to copy. [V measurements; C: https://gnu.googlesource.com/gcc/+/refs/tags/release-12.2.mpacbti-rel1/config/dfp.m4, https://gcc.gnu.org/onlinedocs/gcc/Decimal-Float.html] |
| **.NET `System.Decimal` / DecCalc** | 96-bit coefficient (three 32-bit limbs), sign, scale 0-28 in a flags word; 16 B; 28-29 digits | `Math.BigMul` 64-bit fast path; nine partial products into a 192-bit `Buf24`; `ScaleResult` reduces 192 -> 96 with half-even and a sticky bit; divide by `Div96By32`/`Div128By64`/`Div128By96` with trial limbs and scaling in `10^9` chunks | No NaN/Inf; scale preserved; numeric equality; half-even only; overflow and divide-by-zero throw | Limb arithmetic, `ScaleResult`-style rounding and the 64-bit fast path are useful references, but 96 bits cannot hold 38 digits. [C: https://learn.microsoft.com/en-us/dotnet/api/system.decimal?view=net-10.0, https://github.com/dotnet/runtime/blob/main/src/libraries/System.Private.CoreLib/src/System/Decimal.DecCalc.cs] |
| **Rust `rust_decimal`** | 96-bit coefficient + `u32` flags (scale bits 16-23, sign bit 31); 16 B; 28-29 digits; `Copy` | `u128` product when both operands fit `u64`; `Buf24` partial products otherwise; `Dec64` add path when the scale difference is <=19; `div32`/`div64`/`div96` with normalized divisors; seven public rounding strategies | Trailing zeros preserved; numeric equality/hash; no NaN/Inf; operators panic, `checked_*` methods return `Option` | Existence proof that an allocation-free fixed decimal is practical, and it uses the same 64-bit/128-bit fast-path split; still 28-29 digits. [C: https://docs.rs/rust_decimal/latest/rust_decimal/, https://github.com/paupino/rust-decimal] |
| **Go `cockroachdb/apd`** | `big.Int` coefficient (two inline 64-bit words before heap) + `int32` exponent + sign; arbitrary precision | Upscale to a common exponent, `big.Int` add/mul; divide scales the dividend to the context precision, `QuoRem`, compares `2*R` with the divisor; eight rounding modes | NaN/Inf conditions, context traps, `Quantize`, `Reduce`; default half-up | The context machinery and special values are more than a fixed primitive needs; `Quantize` and the `2*R` comparison are good semantic references. [C: https://pkg.go.dev/github.com/cockroachdb/apd/v3, https://github.com/cockroachdb/apd] |
| **Python `decimal` / libmpdec** | Heap coefficient, **base-`10^19` limbs on 64-bit** (`10^9` on 32-bit), plus exponent; arbitrary precision | Base-`10^19` add/sub; base multiply, Karatsuba and NTT tiers by operand size; divide with one guard digit, a sticky-LSD adjustment and a Newton reciprocal for large divisors | NaN/Inf/signed zero; context; half-even default; trailing zeros | Best test oracle and semantic reference; base-`10^19` is an arbitrary-precision design, not evidence for a fixed 38-digit type; the guard-digit and sticky-LSD rules are directly useful for correct one-rounding. [C: https://docs.python.org/3/library/decimal.html, https://www.bytereef.org/mpdecimal/] |
| **Java `BigDecimal`** | `BigInteger` unscaled + `int` scale; `intCompact` long holds values up to 18 digits; arbitrary | Scale alignment by `10^diff`; `BigInteger` Karatsuba/Toom-Cook/Burnikel-Ziegler; divide computes a preferred scale, uses long division and applies the rounding mode | Eight rounding modes; `equals` is scale-sensitive, `compareTo` numeric; divide by zero and inexact `UNNECESSARY` throw | Strong warning about scale-sensitive equality; obo's numeric equality must ignore `dscale`. [C: https://docs.oracle.com/en/java/javase/21/docs/api/java.base/java/math/BigDecimal.html, https://github.com/openjdk/jdk/blob/master/src/java.base/share/classes/java/math/BigDecimal.java] |
| **IBM decNumber / General Decimal Arithmetic** | Arbitrary precision; context | Reference semantics for decimal arithmetic | IEEE-style context | Use as a semantic and test reference, not as a performance model; general context handling is too heavy for a fixed value. [C: https://speleotrove.com/decimal/] |

Published cross-implementation data is sparse and mostly third-party. The Intel ICSOFT 2006 paper reports decimal128 add/multiply/divide medians of 170/300/250 cycles for libbid versus 2,300/3,000/2,000 for decNumber on a 3.4 GHz Xeon, and claims one to two orders of magnitude overall. The libmpdec benchmark page reports 34-digit Mandelbrot times of 6.78 s (libmpdec) versus 8.07 s (libbid) and 13.10 s (decNumber) on a 3.16 GHz Core 2 Duo, with different rankings on a 32-bit Athlon. The Boost.Decimal page compares 20M-element vectors for GCC `_Decimal128`, Intel BID_UINT128 and Boost decimal128_t on an i9-11900K. These are 2006-2025 maintainer or third-party numbers on different CPUs; they are cited as context, not re-measured here. [C citations; O third-party]

Common lessons:

1. Fixed-width implementations converge on 96-bit/28-digit (.NET, rust_decimal) or 113-bit/34-digit (Intel/BID) coefficients. The 128-bit/38-digit coefficient is not new: SQL Server `decimal(38)`/`SqlDecimal` and ASE `numeric(38)` already use it. obo's contribution is the 24-byte language value with a separate `int16` exponent and `uint16` display scale, the finite-only arithmetic core, and the no-libgcc runtime path; call that an engineering combination, not a new arithmetic technique. [C vendor docs; I scope]
2. Binary-coefficient arithmetic dominates fixed-width implementations; base-`10^n` limbs appear only in arbitrary-precision libraries (base-`10^19` in libmpdec on 64-bit, base-1000 in libdecnumber, base-`10^9` in obo's planned arbitrary tier) where variable length matters. This supports the approved binary coefficient for the fixed type. [C; I]
3. General IEEE decimal software (libbid/libdecnumber) is slower than a specialized 38-digit core in these measurements: fair finite GCC `_Decimal128` divide/multiply are about 75/77-90 ns versus 24.3/25.8 ns here (the original 184.4/46.1 ns figures were not fair finite comparisons). A small, fixed, finite-only core is the right performance strategy. [V; I]
4. Scale-sensitive equality is a known trap (Java, .NET); the design's numeric equality and separate `dscale` are correct. [C design; I]
5. Licenses: .NET (MIT), rust_decimal (MIT), apd (Apache-2.0), libmpdec (BSD-2-Clause) and the Intel libbid files (BSD-3-Clause-style) permit idea reuse; GCC's libdecnumber is GPLv3 with the Runtime Library Exception and Java is GPLv2+CE, so neither code nor tests should be copied into the Apache-2.0 runtime. Even where a license permits reuse, write independent C and use the libraries only as semantic oracles. [C licenses as stated by their projects; I rule]
6. Every fixed-width implementation has a 64-bit fast path: libbid switches on `q1+q2` digit counts, .NET uses `Math.BigMul`, rust_decimal uses `u128`/`Dec64`, Java uses `intCompact` and libmpdec uses `shortdiv` for one-limb divisors. This corroborates the measured 64-bit fast path and its priority. [C; I]
7. Division is the main algorithmic lever. The three proven approaches (libbid estimate-and-correct, .NET trial limbs, libmpdec guard digit plus Newton) all avoid a naive full-width loop, and libbid avoids division even in rounding by reciprocal constants. Those alternatives should be considered after the `divq` Knuth baseline is correct and measured. [C; I]

## 7. Recommendation

### 7.1 Default implementation

- **Representation:** the approved 24-byte value: `word0` coefficient bits 0-63, `word1` bits 64-127, `word2` `int16` exponent, `uint16` dscale, sign/flags. Coefficient range `0..10^38-1`; zero canonical positive; finite only.
- **Language and link constraint:** freestanding C in `runtime/` using `unsigned __int128` for add and multiply only, and explicit 64-bit limbs for division. **Never use `u128` `/` or `%` in the runtime.** GCC lowers them to `__udivti3`/`__udivmodti4`/`__umodti3`, and `ld -T runtime/rt.ld` cannot resolve those symbols. Compile with the existing CFLAGS; no `-march`, no `-mbmi2`, no `-madx`, no libgcc.
- **Division primitives:** a `divq64(hi, lo, divisor, *rem)` inline-asm primitive; `div128_64` (two steps, or one when `hi < divisor`); `div256_64` (four steps); a Knuth D 256/128 with a `divq` quotient estimate and `hi >= divisor` clamp; `div192_10` for the final decimal digit. Add a build-time `nm` check that the runtime objects contain no undefined reference to `__udivti3`, `__udivmodti4` or `__umodti3`. The 128/64 branchy routine and the 256/64 specialization are measured and recommended.
- **Add/sub:** align to the smaller exponent with a table-driven `10^d` multiply into 256 bits when `digits(a) + d <= 76` (the conservative implementation guard; the sharp 256-bit fit is `<= 77`); beyond the guard use the sticky-only path (`b` below the 38-digit window), with the sign- and mode-dependent one-ulp adjustment for subtraction handled explicitly. Add/sub; round once to 38 digits only if needed; `dscale = max`; traps for overflow; guard+sticky for exact ties.
- **Multiply:** four 64x64 -> 128 products into a 256-bit product; divide by `10^k` and round once (half away by default); `dscale = sum`, capped at 65535. Implement the 256/64 path when `k <= 19` (measured 9.5-10.0 ns) and the Knuth 256/128 path for larger `k` (measured 25.8-30.0 ns including the rounding). `10^38` normalizes either by one divide-by-ten to `10^37` with exponent + 1 or by stripping all 38 zeros to coefficient 1 with exponent + 38 (for `e = 0`, coefficient 1, exponent 38); pick one rule and check the exponent. Reciprocal-constant rounding (libbid style) is a later optimization if multiply-and-round profiles hot.
- **Divide:** explicit result scale/mode; scale the numerator for 38 digits plus guard; Knuth D 256/128 with a 192-bit quotient and `divq` estimates; round once; division by zero traps. Use the 256/64 specialization for divisors that fit `u64`; use the branchy 128/64 routine where the high word is usually below the divisor.
- **Compare/equality/hash:** numeric, ignoring `dscale`; zero special-cased before any digit count; adjusted exponent, then coefficient compare. [I]
- **Parse/format:** SWAR parse for fixed-width digit runs; base-`10^19` format with a `divq` split and a two-digit table; preserve `dscale`; reject or explicitly round above 38 digits in the literal layer. [I]
- **64-bit fast path:** branch on the high coefficient word being zero; use `u64` operations where they fit, especially multiply, divide, parse and format. Keep the 24-byte ABI.
- **Runtime interface:** hidden destination pointer plus operand pointers. For example `void rt_dec_add(dec24 *d, const dec24 *a, const dec24 *b)` and `void rt_dec_div(dec24 *d, const dec24 *a, const dec24 *b, uint64_t scale_mode)`. Pack scale and mode into one word to stay within the four-argument runtime limit. The compiler allocates the 24-byte destination temporary and reads it after the call; the helper returns `void`. [I]
- **Inline versus helper:** use runtime helpers for v1, including add and compare. The measured helper overhead is about 0.7-1.2 ns over inline with all 24 bytes consumed; multiply/divide are dominated by arithmetic, and a single helper implementation reduces code size and trap-handling duplication. Inline add/sub/compare later only if profiles show it matters. [V; I]
- **Dispatch and SIMD:** none for scalar values. No CPUID, no `target_clones`, no ifunc, no libgcc, no AVX-512 requirement. If a bulk/array API later justifies IFMA, gate it with one manual CPUID check and a cached function pointer, and keep the scalar path as the baseline. [I]
- **Errors:** traps for overflow and division by zero by default; `Result` variants later, per the approved design. [V design]

### 7.2 Effort and risk

The design already sizes the compiler/runtime work at roughly 700-1,400 lines for layout/ABI, 800-1,600 lines for codegen, 1,000-2,000 lines for the runtime C arithmetic and 2,000-4,000 lines of tests, and rates the ABI/codegen/runtime work as high risk. [V design §6.1] This spike does not revise those estimates downward; the measurements mainly remove performance uncertainty from the core arithmetic. The no-libgcc division work adds a small, contained amount of inline asm and its test vectors.

Relative risks:

- **High:** 24-byte ABI plumbing (parameters, returns, fields, arrays, globals, constants, `Result[decimal]`, `.sym`, hidden-pointer aliasing), and division/rounding semantics.
- **High:** reintroducing a libgcc division reference by writing `u128` `/` or `%` in the runtime. The link fails closed, which is good, but a build-time symbol gate is needed to catch it early.
- **Medium:** exact one-rounding add/sub with cancellation, the d>38 sticky path and the subtraction borrow case; parse/format edge cases; exponent/dscale normalization; the `10^38` normalization rule.
- **Low-medium:** raw arithmetic throughput; the measured core is comfortably fast for ledgers and CLI tools, and the 64-bit fast path covers common small values.
- **Low:** CPU feature availability, because the recommended path requires only baseline x86-64.

### 7.3 Implementation order

1. **M0: oracle and goldens.** Python `decimal`/big-integer oracle for 38-digit arithmetic, exponent/dscale, rounding modes, overflow and boundary values; golden vectors for the formats in the design, including zero, one, `10^38-1`, `10^38` normalization, half-way ties, small-k rounding and the d>38 sticky/borrow cases. This is the gate for every later step. [V design M0; I]
2. **M1a: value and basic operations.** 24-byte ABI, literals/constants, add/sub/compare, parse/format, traps, 64-bit fast path. Verify against oracle vectors and the benchmark suite.
3. **M1b: multiply and rounding.** 128x128 -> 256, 256/64 and 256/128 rounding paths, half-away and explicit modes, overflow normalization. Add the rounded-multiply benchmark and vectors.
4. **M1c: divide and quantize.** Explicit scale/mode policy, 256/128 `divq` core, 256/64 specialization, remainder, trap/Result behavior.
5. **M2: containers and symbols.** Fields, arrays, globals, constants, `Result[decimal]`, `.sym` form, ABI/format bumps.
6. **M3: database adapters.** SQL Server TDS first, then Oracle, ASE and PostgreSQL, with byte-format goldens; the measured conversion costs are magnitude-kernel estimates only and exclude adapter work. [V design staging; I order]
7. **Later:** arbitrary `numeric` tier, float interop after floats, Money/currency library, optional IFMA batch work only if a bulk API appears, and reciprocal-constant rounding if profiles justify it.

## 8. Risks and open questions

1. **Division result scale and mode are not finalized.** The design requires an explicit scale/mode or a documented checked default; this must be decided before implementing `/`. [V design; open]
2. **Add/sub rounding at the 38-digit boundary.** Exponent alignment can produce 39+ digits. The implementation needs guard+sticky and correct tie behavior, especially when the smaller operand is far below the larger (sticky only), when cancellation exposes low digits, and when subtraction borrows through trailing zeros. Test all midpoint cases against the oracle, including d>38 and directed rounding. [I]
3. **24-byte ABI details.** Hidden-pointer aliasing (callee assignment to a parameter), parameter/return copies, register allocation with three-word values, array stride 24, globals, 24-byte constants, `.sym` 48 hex digits, and `Result[decimal]` payload layout. The design flags these as high risk. [V design; open]
4. **No `?decimal`.** Zero is a valid decimal, so a sentinel word cannot represent nil; use `Result[decimal]` or a flag record. [V design]
5. **Canonicalization and dscale.** When to strip trailing zeros, how literal exponent normalization works, and how `dscale` interacts with numeric equality, hashing and database binding. The design fixes the broad rules but not every literal edge case. [V design; open]
6. **Division performance and link safety.** The no-libgcc 256/128 path is 22.0-26.7 ns and the full divide+round is 24.3-27.9 ns, the largest arithmetic cost. Add the 256/64 specialization and the 128/64 branch, keep a build-time `nm` gate against libgcc division symbols, and measure reciprocal-based division before any production-throughput claim. [V; I]
7. **Rounding-mode coverage.** The default tie rule is half-away-from-zero (often called half-up); half-even, half-toward-zero, the directed away-from-zero mode, toward-zero/truncate, floor, ceiling and `UNNECESSARY` still need explicit APIs and tests. Do not add a global context. [V design]
8. **Overflow/underflow boundary.** `int16` exponent limits, the `10^38` normalization rule (one divide-by-ten versus stripping all 38 zeros) and the trap-versus-Result split need exhaustive tests. [V design; I]
9. **Measurement environment.** The numbers are from one Zen 4 WSL2 host under load (load average roughly 5-15); CPUID is virtualization-inflated and medians varied by 10% or more, with occasional larger outliers. Re-measure on bare metal and at least one older x86-64 CPU before publishing performance claims. [O]
10. **No AVX-512 assumption.** IFMA is batch-only and not portable; a future batch path needs a manual CPUID gate and a scalar fallback. `target_clones`/ifunc must not be used in this runtime. [V observations; I]
11. **Existing licenses.** GCC's libdecnumber is GPLv3 with the Runtime Library Exception and Java is GPLv2+CE; neither may be copied into the Apache-2.0 runtime. The Intel libbid sources carry a BSD-3-Clause-style notice, .NET and rust_decimal are MIT, apd is Apache-2.0 and libmpdec is BSD-2-Clause. Even where a license permits reuse, write independent C and use the libraries only as semantic references; keep the Python oracle as the test authority. [C licenses as stated by the projects; I rule]
12. **Database limits.** Oracle raw 39-40-digit values, PostgreSQL unconstrained `numeric`, NaN/Infinity and negative scale remain the later `numeric` tier; the fixed type is 38 significant digits and finite only. [V design]
13. **ASE byte order.** The approved design marks ASE's exact byte order as a decision gate. Do not share a codec with SQL Server or assume little-endian without testing the target product and version. [V design; open]

## 9. Sources

### Project documents and code

- `/home/chris/obx/obo/design/decimal-research-2026-10-28.md` — approved 24-byte design, §A.5.7 arithmetic policy, §A.5.9 ABI, §A.6.4 scale/exponent semantics, §A.6.5 staging, §6.1 effort/risk.
- `/home/chris/obx/obo/design/numeric-literals-decision-2026-10-29.md` — decimal-by-default literal policy.
- `/home/chris/obx/obo/Makefile` — freestanding CFLAGS and `ld -T runtime/rt.ld` link of the runtime (read locally).
- `/home/chris/obx/obo/runtime/runtime.c`, `/home/chris/obx/obo/runtime/entry.S`, `/home/chris/obx/obo/runtime/rt.ld` — runtime ABI, `_entry`, jump table, stack-alignment contract, link script (read locally).
- `/home/chris/obx/obo/src/obo.obo` — `rtCall` four-argument limit and one-word return path.

### Instruction set and hardware

- Intel Decimal Floating-Point Math Library (software decimal32/64/128): https://www.intel.com/content/www/us/en/developer/articles/tool/intel-decimal-floating-point-math-library.html
- Intel BID design slides: https://www.lirmm.fr/arith18/slides/CorneaM_Decimal_ARITH18.pdf
- GCC decimal format selection (BID on x86, DPD on POWER/S/390): https://gnu.googlesource.com/gcc/+/refs/tags/release-12.2.mpacbti-rel1/config/dfp.m4
- CPUID feature bits: https://www.felixcloutier.com/x86/cpuid
- `DAA` invalid in 64-bit mode: https://www.felixcloutier.com/x86/daa
- x64 instruction list: https://learn.microsoft.com/en-us/windows-hardware/drivers/debugger/x64-instructions
- `FBLD`: https://www.felixcloutier.com/x86/fbld
- `DIV`: https://www.felixcloutier.com/x86/div
- `MUL r64` timings: https://www.uops.info/html-instr/MUL_R64.html
- `MULX` timings: https://www.uops.info/html-instr/MULX_R64_R64_R64.html
- `ADCX` timings: https://www.uops.info/html-instr/ADCX_R64_R64.html
- `ADOX` timings: https://www.uops.info/html-instr/ADOX_R64_R64.html
- `DIV r64` timings: https://www.uops.info/html-instr/DIV_R64.html
- Power ISA v3.1C (decimal floating point, DPD): http://wiki.raptorcs.com/w/images/5/5f/OPF_PowerISA_v3.1C.pdf
- IBM z DFP overview: https://public.dhe.ibm.com/software/dw/linux390/perf/dfp.pdf
- IBM z/Architecture reference summary: https://www.ibm.com/support/pages/sites/default/files/2022-09/SA22-7871-11.pdf
- `VPMADD52LUQ`: https://www.felixcloutier.com/x86/vpmadd52luq
- `VPMADD52HUQ`: https://www.felixcloutier.com/x86/vpmadd52huq
- Gueron and Krasnov, accelerating big integer arithmetic with IFMA: http://acsel-lab.com/arithmetic/arith23/data/1616a032.pdf

All instruction timings and ISA descriptions above are [C]: cited externally from the listed sources, not independently measured on this host. The local measurements are the `div`/`MUL`/`ADC`/ADX/IFMA results in §5.

### Dispatch and toolchain

- GCC function multiversioning: https://gcc.gnu.org/onlinedocs/gcc/Function-Multiversioning.html
- GCC common function attributes: https://gcc.gnu.org/onlinedocs/gcc/Common-Function-Attributes.html
- libgcc x86 CPU detection: https://github.com/gcc-mirror/gcc/blob/master/libgcc/config/i386/cpuinfo.c
- GCC x86 feature detection: https://github.com/gcc-mirror/gcc/blob/master/gcc/common/config/i386/cpuinfo.h
- Static ifunc/IRELATIVE startup discussion: https://sourceware.org/pipermail/libc-alpha/2012-August/033716.html
- mold documentation of ifunc/static-link failure modes: https://github.com/rui314/mold/blob/main/docs/bugs.md

### Implementations and semantics

- .NET `System.Decimal`: https://learn.microsoft.com/en-us/dotnet/api/system.decimal?view=net-10.0
- .NET `Decimal.DecCalc.cs`: https://github.com/dotnet/runtime/blob/a4f8db31af247193ddb8bcdb6313b5ee8f626487/src/libraries/System.Private.CoreLib/src/System/Decimal.DecCalc.cs
- Rust `rust_decimal`: https://docs.rs/rust_decimal/latest/rust_decimal/ and https://github.com/paupino/rust-decimal
- Go `cockroachdb/apd`: https://pkg.go.dev/github.com/cockroachdb/apd/v3
- Python `decimal`: https://docs.python.org/3/library/decimal.html
- libmpdec: https://www.bytereef.org/mpdecimal/
- Java `BigDecimal`: https://docs.oracle.com/en/java/javase/21/docs/api/java.base/java/math/BigDecimal.html
- IBM decNumber / General Decimal Arithmetic: https://speleotrove.com/decimal/
- Intel DFP source distribution on Netlib: https://www.netlib.org/misc/intel/ (README: https://www.netlib.org/misc/intel/README.txt)
- GCC libbid sources: https://github.com/gcc-mirror/gcc/tree/master/libgcc/config/libbid
- Cornea et al., ICSOFT 2006, libbid versus decNumber cycle counts: https://www.scitepress.org/Papers/2006/13209/13209.pdf
- GCC decimal floating-point: https://gcc.gnu.org/onlinedocs/gcc/Decimal-Float.html
- GCC Runtime Library Exception: https://github.com/gcc-mirror/gcc/blob/master/COPYING.RUNTIME
- libmpdec benchmarks: https://www.bytereef.org/mpdecimal/benchmarks.html
- Boost.Decimal third-party decimal benchmarks: https://www.boost.org/doc/libs/master/libs/decimal/doc/html/benchmarks.html
- .NET decimal benchmark discussions: https://github.com/dotnet/runtime/issues/7778, https://github.com/dotnet/runtime/pull/99212
- rust_decimal changelog: https://github.com/paupino/rust-decimal/blob/master/CHANGELOG.md
- Third-party apd benchmark table: https://pkg.go.dev/github.com/ericlagergren/decimal_benchmarks
- OpenJDK BigInteger thresholds: https://github.com/openjdk/jdk/blob/master/src/java.base/share/classes/java/math/BigInteger.java
- IEEE decimal128 format: https://en.wikipedia.org/wiki/Decimal128_floating-point_format

Implementation descriptions, license statements and third-party benchmark figures above are [C], not locally reproduced except where §5/§6 marks a local measurement.

### Database formats

- SQL Server `decimal`/`numeric`: https://learn.microsoft.com/en-us/sql/t-sql/data-types/decimal-and-numeric-transact-sql
- [MS-TDS] decimal/numeric encoding: https://learn.microsoft.com/en-us/openspecs/windows_protocols/ms-tds/b46a581a-39de-4745-b076-ec4dbb7d13ec
- Oracle `NUMBER`/OCI data types: https://docs.oracle.com/en/database/oracle/oracle-database/18/lnoci/data-types.html
- PostgreSQL `numeric`: https://www.postgresql.org/docs/current/datatype-numeric.html
- PostgreSQL `numeric.c`: https://github.com/postgres/postgres/blob/3d6a828938a5fa0444275d3d2f67b64ec3199eb7/src/backend/utils/adt/numeric.c
- SAP ASE decimal types: https://help.sap.com/docs/SAP_ASE/2df920cd5b1644e5a065d55b637d5e92/aab84005bc2b1014a9e4de77b205c641.html?version=16.0.3.0&locale=en-US
- FreeTDS numeric conversion: https://github.com/FreeTDS/freetds/blob/dda3cd532574bfd0cfd349a946736bfc5d264738/src/tds/numeric.c

SQL Server TDS/on-page little-endian magnitude is documented or observed; ASE's exact byte order is unverified and remains a decision gate.

### Benchmark artifacts (outside the repository)

Revision-1 sources and vectors:

- `/tmp/obo-decimal-arith/bench_common.h` — timing harness, RNG, types.
- `/tmp/obo-decimal-arith/bench_mul.c` — scalar, MULX, ADX and IFMA multiply kernels; verified vectors.
- `/tmp/obo-decimal-arith/bench_div.c` — revision-1 division kernels (64/64 raw `div`; 128/64 and 256/128 libgcc-assisted), decimal divide/round, GCC `_Decimal128` reference.
- `/tmp/obo-decimal-arith/bench_conv.c` — revision-1 parse/format kernels (libgcc-assisted format).
- `/tmp/obo-decimal-arith/bench_align.c` — revision-1 exponent alignment plus add (d=0..38).
- `/tmp/obo-decimal-arith/bench_ledger.c` — revision-1 64-bit, 128-bit and 38-digit ledger loops (libgcc-assisted).
- `/tmp/obo-decimal-arith/bench_abi.c` — revision-1 ABI costs (inline row dead-code eliminated).
- `/tmp/obo-decimal-arith/bench_fast.c` — 64-bit versus 128-bit add/multiply.
- `/tmp/obo-decimal-arith/bench_dispatch.c` — CPUID and cached-flag costs.
- `/tmp/obo-decimal-arith/bench_dbconv.c` — revision-1 base-100, base-10000, base-256 and TDS-copy costs (libgcc-assisted).
- `/tmp/obo-decimal-arith/results/` — revision-1 logs and host context.

Revision-2 sources, generators and evidence:

- `/tmp/obo-decimal-arith/revision/dec_div.h` — no-libgcc `divq` primitives (`divq64`, `div128_64`, `div128_64_fast`, `div256_64`, `div192_10`, `divq64_checked`, `div256_128_nolibgcc`).
- `/tmp/obo-decimal-arith/revision/bench_div2.c` — no-libgcc division, boundary tests, finite `_Decimal128`-free division rows.
- `/tmp/obo-decimal-arith/revision/bench_conv2.c` — no-libgcc base-`10^19` format, boundary strings, full literal scanner.
- `/tmp/obo-decimal-arith/revision/bench_align2.c` — exact, guarded and sticky alignment; d>38 tests.
- `/tmp/obo-decimal-arith/revision/bench_ledger2.c` — no-libgcc ledger tiers.
- `/tmp/obo-decimal-arith/revision/bench_dbconv2.c` — no-libgcc magnitude conversion.
- `/tmp/obo-decimal-arith/revision/bench_abi2.c` — all-24-byte ABI checksums.
- `/tmp/obo-decimal-arith/revision/bench_dec128_fair.c` — finite precomputed `_Decimal128` comparison.
- `/tmp/obo-decimal-arith/revision/verify_cmp.c` — compare/zero/cancellation semantics.
- `/tmp/obo-decimal-arith/revision/probe_runtime_libgcc.c`, `probe_runtime_nolibgcc.c`, `probe_all_primitives.c` — real-link division probes (single helper and all recommended primitives).
- `/tmp/obo-decimal-arith/revision/verify_align_bound.c` — 77-digit alignment-bound check.
- `/tmp/obo-decimal-arith/revision/gen_vectors2.py`, `gen_align2.py` — deterministic vector generators.
- `/tmp/obo-decimal-arith/results/` — revision-2 logs: `bench_div_nolibgcc*`, `bench_conv_nolibgcc*`, `bench_align2*`, `bench_ledger2*`, `bench_dbconv2*`, `bench_abi2*`, `bench_dec128_fair*`, `dec128_setup.txt`, `verify_cmp.txt`, `nolibgcc_link_evidence.txt`, `nolibgcc_symbols.txt`, `verify_align_bound.txt`, `host_revision.txt`.

No benchmark source, binary, vector or log was added to the repository. [V]
