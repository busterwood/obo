# Decimal performance: research and ranked options

Status: written 2026-11-05 as research; items 1 to 3 and 5 to 7 then committed on branch
`decperf`, item 4 measured and not committed: see section 8, Results.
Decided 2026-11-05: items 1 to 7 to be worked through on a branch, each committed only if measured
faster; no cached digit count (the Decimal record keeps its shape); no instruction set beyond the
baseline for now; item 8 not requested.

Every cost below is an estimate from operation counts and the code as read. None is measured.
Confirm each with the micro-benchmarks in section 4 before changing anything.

## 0. Summary

- The main costs are not the arithmetic. They are: (a) `digits()`, a linear scan that runs several
  times per operation; (b) every u128 `/` and `%` (including `/ 10` and `% 10`), which the native
  backend compiles to a libgcc call (`__udivti3`, `__umodti3`) through `rt_n_udiv128`; (c) binary
  long division for 256-bit numerators with divisors above 2^64, one bit per iteration.
- The techniques that apply most directly are Granlund and Montgomery (1994), Möller and Granlund
  (2011), and exact division and divisibility tests (the same family as Lemire's fastmod, 2019).
  Digit conversion work (Lemire 2026, jeaiii, Muła, Lemire's SWAR parsing) applies to toStr and
  parse. Cox (2026) applies only to Decimal-to-float.
- Krapivensky's 2020 decimal multiplication paper (NTT) does not apply: it is for numbers of
  thousands of digits, and the coefficient is at most 38 digits.

## 1. How obx Decimal works now

### 1.1 Representation

- `pub type Decimal = value record { coef: u128; exp: int; dscale: int; neg: bool }` (std.obx:794).
  Value = coef * 10^exp; dscale is the display scale only. The form is canonical: no trailing zero
  digit, zero is positive with exp 0 (std.obx:781-783). So `eq` and `hash` compare fields.
- Coefficient limit: 38 digits (`COEF_MAX` std.obx:848, `TEN38` std.obx:849).
- `POW10: []u128` (std.obx:1481-1493), read through `pow10(k)` (std.obx:1495).
- Money (lib/money.obx) inherits every cost: `times` is `(a.amt * rate).quantize(...)`
  (money.obx:103-104); `allocate` and `unitCount` use `div` (money.obx:126, 138).

### 1.2 How u128 arithmetic is compiled

Native backend (x64gen.obx, the default path):
- `+ - & | ^` on 128-bit values are inline (add/adc, sub/sbb, logic on register pairs;
  x64gen.obx:4062-4072). Operands go through `pair128` (x64gen.obx:3547-3558), which spills.
- `*` is inline: two `imul` for the cross products and one 64-bit `mul` for the low halves
  (x64gen.obx:4073-4080). It is a full 128×128 low product even when both operands fit in 64 bits.
  I found no 64×64→128 widening special case in x64gen.obx or cgen.obx, so `mul128` (std.obx:1383)
  pays for four generic 128-bit products, about 12 multiplies, instead of four `mul`s.
- `/` and `%` on 128-bit values always call the runtime: `callRt("rt_n_udiv128")` and
  `callRt("rt_n_umod128")` (x64gen.obx:4084-4090). No constant-divisor case exists.
- The wrappers are `return a / b` and `return a % b` in C (obx/compiler/native/rt.c:1403-1404).
  gcc lowers them to `__udivti3` and `__umodti3`; gen_rtx.py loads those libgcc members
  (gen_rtx.py:296-298; rtx.obx:398-399, 1532-1533). rt.c is freestanding, `-O2 -ffreestanding -nostdlib`.
- 64-bit `/` and `%` also get no constant-divisor treatment: native code emits `DIV`/`IDIV`
  directly (x64gen.obx:4121-4131).

C backend (cgen.obx): the 128-bit types are `__int128` and `unsigned __int128` (cgen.obx:115-116);
C `/` and `%` go to libgcc the same way. Whether gcc expands 128-bit division by a constant here is
unverified; check with `objdump` on a small file (no timing run needed).

Decimal's digit output: `u128ToStr` is an intrinsic (check.obx:5110-5113) mapped to
`rt_u128_to_str` (rt.c:912; rt.h:501-505 for the C backend). `rt_digits` divides by 10 while the
value is above 2^64 (rt.c:906-911): two u128 libgcc calls per digit above 19 digits.

A libgcc `__udivti3`/`__umodti3` call with a one-word divisor is probably tens of cycles on current
x86. That is an estimate; M0 in section 4 measures it.

### 1.3 Hot paths (lines are obx/lib/std.obx unless stated)

| Path | Lines | What it does | Cost drivers (estimate) |
|---|---|---|---|
| `makeDec` (every result) | 907-925 | strips trailing zeros; checks 38 digits and exponent range | `while x % 10 == 0 { x = x / 10 }` (917-921): one u128 `%` call per check, one `/` per stripped digit. Line 923 calls `digits(x)`. |
| `digits` | 928-932 | counts decimal digits | Linear: `while n < 39 && x >= pow10(n)`. About digits+1 iterations, each a bounds-checked load and 128-bit compare. Called from makeDec (923), addExact (985), mulExact (1011), cmp (1166-1167), quantize (1095), toInt (1127), parse (1339). |
| `addExact` | 975-1001 | exact sum | Fast path (985-993): `digits` twice in the condition, `pow10`, then `makeDec`. Slow path (994-1000): `mulPow10` and U256 add/sub. Typical add: 3 `digits` calls and 1 libgcc call. |
| `mulExact` | 1005-1014 | exact product | `mul128` (1008); `digits` (1011); `makeDec`. Typical mul: 2 `digits` calls, 1 libgcc call. |
| `divModU` | 1432-1455 | 256÷128 | Native `/` if both fit 128 bits (1434). `divSmall` if the divisor is below 2^64 (1437-1440): three libgcc calls. Otherwise (numerator at least 2^128, divisor at least 2^64) the bit loop (1441-1453): up to 256 iterations of U256 shift, compare, subtract. |
| `div` | 1048-1086 | quotient at a scale, rounded | `mulPow10` (1061 or 1067), `divModU` (1062, 1069), `makeDec` (1085). Scaled numerators at or above 2^128 with a divisor of at least 2^64 take the bit loop. |
| `quantize` | 1090-1112 | rounding to a scale | `a.coef / p` and `a.coef % p` (1104-1105): two libgcc calls; then `makeDec`. |
| `rem` | 1027-1043 | truncating remainder | `a.coef % b.coef` (1036) and `powMod10`, which calls `mulMod` about 2·log2(d) times (1468-1478). `mulMod` (1463-1465) is `divModU(mul128(x,y), m)`: for m above 2^64 that is the bit loop every time. The slowest operation in the file. |
| `cmp` | 1162-1182 | comparison | Two `digits` calls (1166-1167) and a `pow10` rescale. |
| `toStr` / `format` | 1199-1241 | fixed-notation text | `u128ToStr` (1211), a copy into a byte array (1219-1240), `strFromBytes`. About 37 libgcc calls at 38 digits. |
| `canonical` | 1245-1262 | shortest exact text | `u128ToStr`, then slices and concatenations (several allocations). |
| `parse` | 1271-1341 | text to Decimal | Byte loop (1286-1311) with a u128 multiply-add per digit (1302). Strip loop (1334-1337): one `%` per check, one `/` per stripped zero. Then `digits` (1339). |
| `toFloat` | 1135-1139 | Decimal to float | `canonical()` then `parseFloat`: a string round trip. |
| `toInt` | 1124-1132 | to int | `quantize`, `digits`, i128 multiply. |

Per-operation libgcc calls, common case (6 to 20 digit coefficients, result below 2^128):
add 1, mul 1, quantize to 2 places 2 plus the `makeDec` strip, toStr 0 at 19 digits or fewer and
about 37 at 38 digits, parse of 10 characters 1 to 3.

## 2. Papers and techniques

"Applies" means I checked the technique against the obx code, not just the title.

### 2.1 Lemire, Kaser, Kurz: fastmod (arXiv:1902.01961; SPE 49(6), 2019; github.com/lemire/fastmod)

- For a 32-bit divisor d, M = ⌈2^64/d⌉; n mod d = ⌊d·((M·n) mod 2^64) / 2^64⌋; d divides n iff
  (M·n) mod 2^64 < M. One 64-bit multiply and one 128-bit high product each. Reported: up to ~30%
  faster than compilers for constant divisors; 15 to 80% faster for divisibility tests against
  Granlund-Montgomery (read from the arXiv HTML).
- For 64-bit numerators the constant is 128 bits. I did not check that variant in the paper.
- Applies to: divisibility tests and remainders by 10^k. The 64-bit form does not fit u128
  coefficients directly; item 2 in section 3 uses the exact-division form instead.
- Estimated gain: strip loops from two libgcc calls per digit to a few multiplies: 1.2 to 1.5 times
  on add, mul and parse results with trailing zeros.

### 2.2 Krapivensky: "Speeding up decimal multiplication" (arXiv:2011.11524, 2020)

- Not about BID or DPD. NTT-based multiplication of very large numbers in base 10^N, cache-efficient
  transpose, portable C; 3 to 5 times mpdecimal on large operands.
- Applies to: nothing in obx. The coefficient is at most 38 digits. Does not apply.

### 2.3 Champagne Gareau and Lemire: integer to decimal string in under 2 ns (arXiv:2604.26019; SPE 56(8), 2026)

- Technique: table-free SIMD conversion. Constants c_k = ⌊2^52/10^k⌋ and AVX-512 IFMA
  (vpmadd52luq/huq), one digit per lane. Two variants (branch-light for mixed lengths,
  branch-heavy for uniform). Inputs below 10^16 use the vector path; larger ones split by 10^8 or
  10^4 with a scalar tail.
- Reported (Zen 5, clang 21): 0.72 ns per 8-digit integer; 4.56 ns for uniform 1 to 20 digits;
  1.4 to 2 times the closest competitor; 2 to 4 times std::to_chars.
- Applies to: toStr (rt_digits) in principle. It needs AVX-512 IFMA (not approved), works on
  64-bit inputs, and obx's coefficient is 128 bits, so the split in item 3 comes first. A scalar
  port using 64-bit multiplies would be the realistic version (not verified).
- Estimated gain for obx: small to moderate, after item 3.

### 2.4 Russ Cox: "Floating-Point Printing and Parsing Can Be Simple and Fast" (research.swtch.com/fp, January 2026)

- Read the first 100,000 of about 105,000 characters.
- Technique: "fast unrounded scaling". A 685-entry table of 128-bit mantissas of 10^p; one table
  lookup and one 64×128 multiply, keeping the top word and a sticky bit from the middle word.
  Covers fixed printing (up to 18 digits), shortest printing and parsing (up to 19 digits) in about
  400 lines of Go. The author's claims about speed (against Ryu, Dragonbox, Eisel-Lemire) are his own.
- Applies to: Decimal to float only (toFloat, std.obx:1135-1139). Not to Decimal arithmetic.
- Estimated gain: toFloat 3 to 10 times. Low priority for money.

### 2.5 Granlund and Montgomery: "Division by Invariant Integers using Multiplication" (PLDI 1994)

- Technique: for a fixed divisor d, precompute m and a shift so that n/d = mulhi(n, m) >> s, with a
  correction step. For constant divisors such as 10^k the reciprocal is computed once (offline).
  Exact division: if d divides n, then n·inv(d) mod 2^N is n/d, where inv(d) is the inverse of d's
  odd part mod 2^N. Divisibility is then one multiply and a compare. The paper covers the exact
  division case (the section number is not confirmed here).
- Applies to: quantize (1104-1105), toStr chunking, and the strip loops in makeDec and parse. Exact
  division by 10 needs no reciprocal at all (item 2).
- Estimated gain: quantize and round 2 to 4 times; strip loops 1.2 to 1.5 times on add, mul, parse.

### 2.6 Möller and Granlund: "Improved Division by Invariant Integers" (IEEE TC 60(2), 2011; DOI 10.1109/TC.2010.143)

- Technique: a normalized single-word reciprocal v = ⌊(B²−1)/d⌋ − B (B = 2^64). A 2-by-1 division
  (128-bit numerator, normalized 64-bit divisor) costs two 64-bit multiplies, a few adds and at most
  one correction. The 3-by-2 version does the same for a 192-bit numerator and 128-bit divisor. GMP
  uses it (`udiv_qr_3by2`). Read from search snippets and the draft's outline; the PDF did not decode.
- Applies to: divModU (1432-1455), mulMod and powMod10 (1463-1478) for rem, and the bit loop in div.
- Estimated gain: numerators above 2^128: 10 to 30 times (microseconds to about 50-100 ns). Common
  case unchanged; rem gains most.
- Needs a 128-by-64 divide with RDX:RAX as the dividend (baseline `DIV`). obx has no intrinsic for
  it; a C helper with inline `divq` in rt.c is the simplest route.

### 2.7 Intel BID, IEEE 754-2008 decimal, Cowlishaw's decimal arithmetic

- BID's coefficient is a binary integer, the same idea as obx's u128 coef. Intel's libbid has entry
  points such as `__bid128_mul` (Debian manpage). I did not read its source, so how it divides by
  powers of ten is unknown to me.
- Boost.Decimal mailing-list posts (2024, 2025; authors' numbers, unverified): an Intel run was about
  an order of magnitude faster than Boost.Decimal (decimal64, per the thread); Boost.Decimal ran about
  4.8 times GCC's `_Decimal128` on the geometric mean for decimal128.
- Applies as a design comparison only. The gains come from the algorithms in 2.5 and 2.6, not from
  a format change (the format is public: canonical text and key hashing).
- Cowlishaw's General Decimal Arithmetic spec (speleotrove.com) is the reference for the rounding
  modes. Not read in this pass.

### 2.8 Lemire and Gall: "Number Parsing at a Gigabyte per Second" (arXiv:2101.11408; SPE 51(8), 2021); fast_float

- Technique (search snippets): a 128-bit table of powers of five; one or two 64-bit multiplies to get
  the correctly rounded float; a 192-bit fallback for hard cases.
- Applies to: toFloat only (and to any float side of parse). Not to Decimal's own parse.

### 2.9 Ryu, Dragonbox, Schubfach, and the 2026 review (arXiv:2603.06581)

- Ryu (Adams, PLDI 2018; https://dl.acm.org/citation.cfm?id=3192369; github.com/ulfjack/ryu): shortest
  float output with a 128-bit table. Not read directly.
- Dragonbox (Jeon; github.com/jk-jeon/dragonbox) and Schubfach (Giulietti, 2017-2018): shortest
  output with fewer 128-bit multiplies. Not read in full.
- The 2026 review (Champagne Gareau and Lemire): Schubfach and Dragonbox up to 10 times faster than
  Dragon4 (about 210 instructions against 1500 to 5000); Ryu close behind.
- Applies to: float formatting only. Not on the list.

### 2.10 Anhalt's jeaiii itoa

- Technique (Jeon's write-up, read): find y, k, D with n = ⌊10^k·y / 2^D⌋; each two-digit pair costs
  one multiply and a mask, with a 200-byte table of "00" to "99". Magic constants chosen per length;
  wider integers split into 32-bit segments. The write-up calls its lead-digit trick "not very
  impressive" against a naive variant; a C port was reported slower in one issue (github jeaiii/itoa #17).
- Applies to: digit generation for the 64-bit pieces of the coefficient, after item 3.
- Estimated gain: 1.3 to 2 times on the digit loop, over 64-bit `rt_digits`. Measure before adopting.

### 2.11 Muła (SIMD itoa) and Lemire (SWAR parsing of eight digits)

- Muła's SSE2 routines convert two 8-digit groups at once with reciprocal multiplies (32-bit inputs).
  Read only through secondary copies.
- Lemire's "Quickly parsing eight digits" (2018) and "SWAR explained" (2022), read through a mirror:
  load 8 bytes into a u64, check they are digits, combine with three multiplies and shifts.
- Applies to: parse (1286-1311). The digit loop does one u128 multiply-add per digit today. Keep the
  first 19 digits in u64 (10^19 < 2^64), then combine; an 8-digit SWAR step on top. Needs an 8-byte
  load from a str (a C helper or intrinsic; today obx reads bytes with `strByte`).
- Estimated gain: parse of 20 to 38 digits 1.5 to 2 times overall; the digit loop 2 to 3 times.

### 2.12 128-bit division references

- Knuth, TAOCP vol. 2 §4.3.1 (Algorithm D). compiler-rt's `udivmodti4` and libdivide's
  128-by-64 helper both cite it. libgcc's `__udivti3` (used by obx today) has no precomputed divisor.
- libdivide has invariant-divisor support for 16 to 64 bits; I found no u128 type in the 5.0 header.
  Its 128-by-64 helper is the piece that matters here.

### 2.13 Other decimal libraries

- .NET System.Decimal (DecCalc.cs, source.dot.net): 96-bit mantissa, Div96By32 / Div96By64 /
  Div128By96 on 32-bit limbs, and a newer version using `Math.BigMul`. I found no .NET 2018 to 2023
  Decimal performance write-up (Toub's name turned up on a BigInteger PR only). Unverified beyond the code.
- rust_decimal: 96-bit mantissa in three 32-bit words, scale 0 to 28. decimax (a competitor, its own
  benchmarks) claims 2 to 6 times faster on + - * by splitting 32/64/96-bit fast paths. Lesson: obx
  has no "coefficient fits in 64 bits" fast path.
- Java BigDecimal: `intCompact` (a long) when the value fits, `INFLATED` as the sentinel for the
  BigInteger form. The same two-tier idea. Read from OpenJDK snippets.

## 3. Ranked top 8 changes

Gains are estimates per operation, not measurements. "Library" = std.obx. "Compiler" = x64gen.obx.
"Runtime" = rt.c / rt.h (regenerate rtx.obx with `python3 obx/compiler/gen_rtx.py`).

| # | Change | Where | Expected gain (estimate) | Difficulty |
|---|---|---|---|---|
| 1 | Replace linear `digits()` with a bit-length estimate and one table compare; pure obx first (binary search over 39 entries). A BSR intrinsic later makes it one instruction. | Library | add, cmp, mul 1.3 to 2 times; parse and toInt small | Low |
| 2 | Exact division by 10 without libcalls: x is divisible by 10 iff x is even and (x·inv5 mod 2^128) ≤ ⌊(2^128−1)/5⌋; then x/10 = ((x>>1)·inv5) mod 2^128 (use `*%`). Replaces the strip loops at makeDec 917-921 and parse 1334-1337. | Library | add, mul, parse 1.2 to 1.5 times; more with trailing zeros | Low |
| 3 | Chunk toStr by 10^19: one u128 divide, then 64-bit digit loops on both halves. Removes the two-libcalls-per-digit loop. Also write digits straight into the output (format 1219-1240). | Runtime (rt.c, rt.h) | 38-digit toStr 3 to 10 times; no change at 19 digits or fewer | Low |
| 4 | Per-k reciprocals for division by 10^k: shift out 2^k, then divide by odd 5^k with a precomputed Granlund-Montgomery reciprocal (3-by-2 step above 2^64). Used in quantize (1104-1105), toStr chunking and rem. | Library (generated table) and runtime | quantize and round 2 to 4 times; rem more | Medium |
| 5 | Compiler: a 64×64→128 widening multiply when both operands are zero-extended u64 (one `mul`). Then rewrite `mul128` (1383-1394) to use it. | Compiler (x64gen.obx:4073-4080) | mul core 1.3 to 2 times; mulExact 1.2 to 1.5 times | Medium |
| 6 | Möller-Granlund 2-by-1 and 3-by-2 division for 256÷128 (divModU 1432-1455) and mulMod (1463-1465). A C helper with inline `divq` is simplest. | Library plus runtime helper | numerators above 2^128: 10 to 30 times; rem with large divisors much more; common case unchanged | Medium-high |
| 7 | parse: accumulate the first 19 digits in u64 (no u128 multiply per digit), then an 8-digit SWAR step for longer runs via a runtime load helper. Keep the DecError order documented at 1267-1268. | Library, optional runtime helper | parse 1.5 to 2 times; digit loop 2 to 3 times | Medium |
| 8 | toFloat from (coef, exp) through a 128-bit power-of-ten table (Cox 2026 or Eisel-Lemire), with the string path as fallback for hard cases. | Library (table) | toFloat 3 to 10 times | Medium |

Also worth doing, outside Decimal: constant-divisor multiplication for 64-bit `/` and `%` in
x64gen.obx (4121-4131). It helps every obx program that divides by a constant. A larger change than
item 5.

Order: 1, 2 and 3 first (low difficulty; they remove most libgcc calls and repeated digit scans).
Then 5 (compiler), then 4 and 6, then 7. Item 8 only on request.

## 4. Micro-benchmarks to write first (run only when the machine is free)

Each benchmark runs old and new code on the same inputs with a result checksum so the work is not
dropped. Report ns per operation (median of several runs) and instruction counts
(`perf stat -e instructions`). Inputs: fixed-seed random coefficients at 6, 19, 20 and 38 digits, and
money-like values with 2 decimal places.

- M0: libgcc call cost. C `__udivti3` and `__umodti3` with one-word divisors against a constant
  multiply, dividends below and above 2^64. Sets the unit cost for every other estimate.
- M1: `digits()` on 1, 10, 19, 20 and 38 digit coefficients.
- M2: add on the fast path (6, 15, 19 digits; results with trailing zeros).
- M3: mul with products below 2^128 and above it (19 × 19 digits; covers `mul128` and the hi != 0 loop).
- M4: makeDec strip loop with 0, 1 and 5 trailing zeros.
- M5: div at scale 2 (numerator below 2^128) and at a scale that pushes the numerator above 2^128
  (covers the native branch, divSmall and the bit loop).
- M6: quantize to 2 places from 6, 20 and 38 digit values; a shift above 38 (the branch at 1102).
- M7: parse of 10, 20 and 38 characters, with and without trailing zeros and a fraction.
- M8: toStr at 6, 19 and 38 digits (fixed and canonical).
- M9: rem with exponent differences 1, 10 and 40 (covers powMod10).
- M10: cmp on same-length and different-length coefficients.
- Before any change: a differential test of old and new results on at least 10^6 random pairs per
  operation (canonical text, error codes, display scale), plus the existing Decimal tests in
  obx/tests. The new code must match the old on every case.

## 5. Instruction-set decisions the user needs to make

Required since 2026-11-05 (the start-up check, exit 16): x86-64-v3, that is AVX2, BMI1, BMI2,
FMA3, F16C, LZCNT and MOVBE with SSE4.1 and AVX. So BSR, LZCNT, MULX, SHLX/SHRX and AVX2 may be
used without further approval. ADX and AVX-512 are not in v3 and still need the user's approval.

1. BSR (bit scan reverse). Baseline x86-64; no approval needed. Recommended for item 1.
2. LZCNT. Needs ABM, which is not on the approved list. Recommend BSR instead.
3. MULX (BMI2): flagless 64×64→128 multiply with free destination registers. Perhaps 5 to 15 percent
   on the multiply core after item 5. Needs approval. Decide after item 5 is measured.
4. SHLX / SHRX (BMI2): variable shifts without CL; small gain, since item 6 replaces the bit loop.
   Same approval as MULX.
5. ADCX / ADOX (ADX): two carry chains for 256-bit products. Gain limited after item 5. Recommend not now.
6. 128-by-64 `DIV` with RDX:RAX. Baseline; no approval. Needed for item 6 (or a C helper in rt.c).
7. SSE4.1 or AVX SIMD digit conversion (Muła style). Within the baseline (no approval needed).
   Helps bulk toStr only. Recommend defer.
8. AVX-512 IFMA (Lemire 2026): not in x86-64-v3, not approved; IFMA runs only on Zen 4+ and Ice
   Lake server+. Recommend not proposing now. (AVX2 is in v3 and allowed.)
9. FMA3: no integer use in Decimal. Not needed.

Representation decision (not an instruction set): caching the digit count in the Decimal record
would make `digits()` free. It changes the shape of a `pub` type, so it needs the user's approval.
eq and hash can ignore a cached field (hash uses coef and exp only).

## 6. What does not apply, and risks

- Krapivensky 2020 (section 2.2) and BID-style encodings (a format change; not recommended).
- Lemire 2026 and Muła's routines need the item 3 split first.
- Ryu, Dragonbox and Schubfach are float-output algorithms; not relevant to Decimal.
- Checked integer `*` stops on overflow (SPEC.md:525), so the u128 multiplies in addExact and parse
  may carry guard code. `*%` avoids that where the bound is proven. Check in M3.
- Items 4, 6 and 8 add static tables or new helpers; check binary size if that matters.
- Correctness is the main risk in items 4, 6 and 8. Each needs the differential test in section 4.

## 7. References

- Lemire, Kaser, Kurz (fastmod): https://arxiv.org/abs/1902.01961 ; https://github.com/lemire/fastmod
- Krapivensky, decimal multiplication: https://arxiv.org/abs/2011.11524
- Champagne Gareau, Lemire (integer to string): https://arxiv.org/abs/2604.26019
- Champagne Gareau, Lemire (float to string review): https://arxiv.org/abs/2603.06581 ;
  https://github.com/fastfloat/float_serialization_benchmark
- Cox (floating-point printing and parsing, 2026): https://research.swtch.com/fp
- Granlund, Montgomery (PLDI 1994): https://gmplib.org/~tege/divcnst-pldi94.pdf
- Möller, Granlund (IEEE TC 2011; DOI 10.1109/TC.2010.143): draft https://www.lysator.liu.se/~nisse/archive/draft-division-paper.pdf
- Lemire, Gall (number parsing, 2021): https://arxiv.org/abs/2101.11408 ; https://github.com/fastfloat/fast_float
- Adams, Ryu (PLDI 2018): https://dl.acm.org/citation.cfm?id=3192369 ; https://github.com/ulfjack/ryu
- Jeon, Dragonbox: https://github.com/jk-jeon/dragonbox ; jeaiii write-up https://jk-jeon.github.io/posts/2022/02/jeaiii-algorithm ; https://github.com/jeaiii/itoa
- Lemire, eight-digit parsing (2018, 2022), via mirror: https://eklausmeier.neocities.org/lemire/blog/2018/10-03-quickly-parsing-eight-digits
- Muła, SIMD integer to string: http://0x80.pl/articles/ (secondary copies only)
- libdivide: https://github.com/ridiculousfish/libdivide
- compiler-rt udivmodti4: https://cgit.freebsd.org/src/tree/contrib/llvm-project/compiler-rt/lib/builtins/udivmodti4.c
- Knuth, TAOCP vol. 2, section 4.3.1 (book)
- Intel libbid manpage: https://dyn.manpages.debian.org/testing/libintelrdfpmath-dev/intelrdfpmath.3
- Boost.Decimal benchmarks: https://develop.decimal.cpp.al/benchmarks.html ; mailing list https://lists.boost.org/archives/list/boost@lists.boost.org/message/LXP6LNTUBDPEXP7IZHLPQEKCSOB6EFT2
- Cowlishaw, General Decimal Arithmetic: https://speleotrove.com/decimal/
- .NET DecCalc: https://source.dot.net/System.Private.CoreLib/src/libraries/System.Private.CoreLib/src/System/Decimal.DecCalc.cs.html
- rust_decimal: https://github.com/paupino/rust-decimal ; https://wubingzheng.github.io/en/Decimal-Crates-Comparison.html
- Java BigDecimal: https://github.com/openjdk/jdk/blob/master/src/java.base/share/classes/java/math/BigDecimal.java
- Intel SDM (MULX, ADCX/ADOX, DIV, BSR): https://www.intel.com/sdm

Not accessed or not read in full:
- Granlund and Montgomery PDF: citation only, from search.
- Möller and Granlund: draft PDF did not decode; IEEE paper not reached. Method from snippets and my own reading.
- Ryu (ACM), Eisel-Lemire paper, Dragonbox paper, Schubfach: not read; summaries only.
- Muła's articles (0x80.pl): not reached; secondary copies only.
- Lemire's 8-digit posts: through a mirror; lemire.me not fetched.
- .NET DecCalc and Java BigDecimal: code snippets only; no performance write-up found.
- Intel libbid: manpage and third-party benchmark snippets only.
- libdivide: no u128 support found in the 5.0 header excerpt.
- Cox: first 100,000 of about 105,000 characters read; Go benchmark numbers are in plots only.
- Krapivensky: abstract only.
- Cowlishaw's spec: not read.

## 8. Results (2026-11-05, branch `decperf`)

Measured with `obx/bench/decimal` (dbench M0-M10, `run.py`): user cycles from `perf stat -e
instructions:u,cycles:u`, least of 5 (7 to 11 on the second run), the benchmark pinned to one CPU
with `taskset -c 2` after other agents started benchmarking on the same machine. Each item was
measured against the commit before it; ratios are new/old cycles, native then C. "Committed" needs
5% on both backends, or 5% on one and no more than 1% worse on the other, on the item's own
benchmarks, repeated on a second run. Every commit passed the differential test (`diff.py`:
11 operations x 10^6 inputs, old and new, native and C, all four identical), the lib and native
tests and the quick gate. The branch was rebased onto main at 641945a (x86-64-v3) after item 2;
items 3 to 7 were measured on that base. No item uses an instruction beyond plain x86-64 (the
runtime's `divq` in item 3); the x86-64-v3 approval came after the design and nothing here needed it.

| Item | Commit | Benchmarks (native / C cycles, new/old) |
|---|---|---|
| (extra) 128-bit constants as two immediates (native) | ae8518f | M2-M7, M9 0.81-0.92 / C unchanged |
| 1. digits() as a compare tree | f11d6f3 | M1:1/10/19/38 0.75/0.37/0.23/0.13 and 0.84/0.59/0.36/0.21; M2 0.46-0.78 / 0.49-0.88; M3:6/19 0.75/0.50 and 0.91/0.64; M10 0.28 / 0.30 |
| (extra) u128 +% -% *%, checked + -, constant shifts, u64() inline (native) | 7c362b9 | M3 0.88-0.92, M5 0.86-0.88, M6 0.87-0.94, M7 0.85-0.90, M9 0.85-0.86 / C 1.00 |
| 2. Exact division by 10 in the strip loops | 082e083 | M4:1/5 0.91/0.77 and 0.91/0.68; M2:tz 0.87 / 0.78; M7:20z 0.84 / 0.61; M4:0 1.015 / 0.996 |
| 3. u128 text in 10^19 chunks (runtime) | 6ba1ce9 | M8 38 digits 0.44-0.50 / 0.44-0.46; 19 digits 0.91-0.92 / 0.90-0.91; 6 digits 0.96-0.98 / 0.97-0.99 |
| 4. Per-k reciprocals for / 10^k (quantize) | not committed | see below |
| 5. Widening 64x64 multiply (native) and mul128 written for it | fba6a68 | M3 0.92-0.93 / 1.00; M9:1/10 0.92/0.90 / 1.00 |
| 6. 256-by-128 division by 64-bit digits (Knuth D) | bd0bc02 | M5:big 0.073 / 0.157; M9:40 0.093 / 0.175 |
| 7. parse: digit runs 8 bytes at a time, 19 digits per u64 | fd8bb7b | M7:10 0.79-0.83 / 0.98-0.99; M7:20 0.64 / 0.76; M7:38 0.50 / 0.52; M7:20z 0.67 / 0.74 |

Notes per item:

- Item 1. The planned form (bit length times 1233 >> 12, then one table compare) was slower than
  the old scan for one-digit values; a balanced compare tree over the powers of ten (one- to
  four-digit values first) won everywhere.
- Item 2. Three forms were measured. The rotate form (`x * INV5` rotated right by one, compared
  with (2^128 - 1) / 10) lost in C on coefficients without trailing zeros (M2:6 1.10, M4:0 1.13).
  Branching on parity first lost natively (M2:6 1.02) until the u128 inlining commit; the
  committed form tests parity and divisibility in one compare (`q | (x << 127)`).
  A first try that emitted u128 wrapping ops as C operators (instead of `rt_w*_u128` calls) made
  C 4-5% slower on M5:big and M9:40 and was dropped; the native inlining replaced it.
- Item 3. Without the two-digit table (19 single-digit divisions per chunk) 38-digit text was
  0.73 / 0.73 (format) and 0.67 / 0.68 (canonical), and 19 digits 1.00-1.04; the table made both
  better. Benchmarks that make no text keep their instruction counts but some move with the
  layout: M0:lo C 1.05, M10:diff 1.03.
- Item 4, not committed. quantize spends little of its time in the division. The reciprocal
  version (k <= 19 and coefficient below 2^64: `(x >> k) * RECIP5[k]`, high word, shift; one
  division and a multiply-subtract for the rest) gave M6:6/20/38 1.02/0.99/0.97 native and
  0.99/1.03/1.05 C; native instructions went up 9% at 6 digits. A plain 64-bit `/` and `%` when
  the coefficient fits gave 0.96/1.00/1.01 native and 0.98/1.23/1.25 C (the branch on the
  coefficient's size mispredicts at 20 digits). Neither met the bar.
- Item 5. The backend emits one `mul` for a u128 `*` or `*%` whose factors are both below 2^64 by
  their form (a widened unsigned, a constant, an AND with one, a shift right by 64 or more);
  mul128 names its halves as expressions so that the backend sees them. MULX would not add to it.
- Item 6. Knuth's algorithm D in base 2^64 needs no runtime helper: each quotient digit's
  estimate is the existing u128 `/` with a divisor below 2^64 (libgcc's one-`divq` path), so the
  Möller-Granlund reciprocal was not needed for this gain. Checked on 131308 structured divisions
  against Python, including 486 add-backs. In the C build gcc inlines std differently afterwards:
  M8:f38 1.02 (4.8% more instructions) and M9:10 1.035-1.04.
- Item 7. ParseAcc.take skips the second (fraction) call when there is no fraction; without that
  M7:10 was 1.06-1.08 in C. Checked on 400000 generated texts (results and errors identical).

End to end, main at 641945a against `decperf` at fd8bb7b (least of 9, pinned; native / C):

| Benchmark | native | C |
|---|---|---|
| M1 digits() 1 / 19 / 38 digits | 0.61 / 0.22 / 0.12 | 0.85 / 0.36 / 0.21 |
| M2 add 6 / 19 digits / trailing zeros | 0.61 / 0.45 / 0.43 | 0.90 / 0.63 / 0.66 |
| M3 mul 6 / 19 digits / product above 2^128 | 0.49 / 0.31 / 0.62 | 0.83 / 0.54 / 0.99 |
| M4 strip 0 / 5 zeros | 0.40 / 0.44 | 0.65 / 0.65 |
| M5 div below 2^128 / divisor below 2^64 / big | 0.64 / 0.62 / 0.072 | 0.91 / 0.87 / 0.16 |
| M6 quantize 6 / 20 / 38 digits | 0.58 / 0.54 / 0.51 | 0.93 / 0.76 / 0.70 |
| M7 parse 10 / 20 / 38 digits | 0.49 / 0.41 / 0.30 | 0.87 / 0.64 / 0.47 |
| M8 format 6 / 19 / 38 digits | 0.93 / 0.91 / 0.49 | 0.98 / 0.92 / 0.47 |
| M9 rem exponent gap 1 / 10 / 40 | 0.64 / 0.61 / 0.068 | 0.92 / 1.00 / 0.18 |
| M10 cmp | 0.27-0.28 | 0.28-0.30 |

Not done: item 8 (toFloat; not requested); a BSR or LZCNT intrinsic for digits() (the compare
tree made it cheap enough); constant-divisor multiplication for 64-bit `/` and `%` in the native
backend (outside Decimal).

Seen on the way, outside this work: main's switch to gcc -march=x86-64-v3 (641945a) made the C
build of Decimal code slower, with the same instructions: against faf112b, M2:6 1.36, M6:6 1.37,
M4:0 1.13, M2:19 1.10 in cycles; M5:big and M9:40 1.49 (also 1.4 times the instructions, in
libgcc's division). The native build did not move.
