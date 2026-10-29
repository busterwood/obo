# Decimal arithmetic implementation research

**Date:** 2026-10-29
**Status:** Research spike. No compiler, runtime, test, Makefile or other design document was changed. Benchmark sources and binaries live under `/tmp/obo-decimal-arith/`, not in the repository.
**Companions:** `decimal-research-2026-10-28.md` (approved 24-byte design and database survey) and `numeric-literals-decision-2026-10-29.md` (literal policy).

**Markers:** [V] measured in this spike, or directly documented by a cited primary source; [O] observed implementation or environment detail, not a general contract; [I] inference, design judgment or recommendation. All performance numbers are [V] for the stated host and method only. No unmeasured performance claim is presented as fact.

## Executive verdict

- **Keep the approved representation unchanged.** A 24-byte value with a 128-bit binary coefficient, sign, `int16` decimal exponent and `uint16` display scale is the only fixed layout considered here that holds 38 significant digits in 24 bytes. The coefficient bound is `10^38 - 1 < 2^127`; the exact product of two such coefficients is below `10^76 < 2^253`, so a 256-bit intermediate is sufficient. [V arithmetic bounds; I layout judgment]
- **The core arithmetic is practical.** Measured on a Zen 4 host: 128x128 -> 256 multiply 2.0-2.6 ns; multiply plus one rounding to 38 digits 29.2 ns; 256/128 division 26.9 ns; decimal divide plus round 33.4 ns; exponent alignment plus add 1.6 ns for typical small exponent differences (3.2 ns at a difference of 38); parse of 38 digits 9.3 ns; format 23.8 ns. [V]
- **Use the 64-bit fast path inside the 24-byte type.** Coefficients below `10^19` fit in a `u64`: 64x64 -> 128 multiply costs 0.43-0.49 ns versus 2.5-2.7 ns for 128x128 -> 256, and 64/64 division costs 1.57 ns versus 26.9 ns for 256/128. An invoice loop costs 1.6 ns per line at 64-bit precision, 4.4-4.6 ns with 128-bit coefficients, and 55-59 ns through the full 38-digit multiply/divide/round path. [V]
- **Scalar baseline C is the default; do not hand-write MULX/ADX.** GCC's ordinary `unsigned __int128` code is as fast as a hand-written BMI2+ADX sequence within run-to-run noise (default build: baseline 2.0-2.1 ns, hand-written ADX 2.1-2.3 ns; `-mbmi2 -madx` build: baseline 2.2-2.3 ns, ADX 2.0-2.1 ns). Compiling the project with `-march`/`-mbmi2 -madx` would raise the minimum CPU for no reliable gain. [V]
- **Do not add CPU dispatch, `target_clones`, or ifunc.** There is no useful scalar feature to dispatch to. `target_clones` does not work in obo's freestanding static link: without libgcc it does not link, and with libgcc the `R_X86_64_IRELATIVE` relocation is never applied, so the first call jumps through a self-referential PLT slot and hangs (observed under `timeout`). `__builtin_cpu_supports` silently reports every feature false unless libgcc's `__cpu_indicator_init` constructor runs, which a custom `_start` does not do. Manual CPUID is the only viable feature test, and a CPUID costs 463 ns per operation on this WSL2 host versus 0.44 ns for a cached flag. [V/O]
- **Do not use SIMD for scalar decimal.** AVX2 has no 64x64 -> 128 vector multiply. AVX-512 IFMA helps only a batch of independent products whose results stay in vector registers: 0.78-0.89 ns per product for a batch of eight, but 2.9-3.1 ns once each 256-bit product is packed out, and 29-30 ns for a single value. Intel's own libbid has no SIMD paths, and no AVX-512 decimal128 library was found. [V]
- **The largest risks are ABI and exact-rounding semantics, not raw arithmetic speed.** The 24-byte value touches parameters, returns, fields, arrays, globals, constants, `Result[decimal]` and `.sym`; division scale policy and one-rounding/add-sticky behavior need explicit decisions and test vectors. The core C arithmetic is comparatively well understood. [I]
- **Recommended default:** freestanding C runtime helpers using `unsigned __int128` and explicit limb code, called through a hidden destination pointer; 64-bit fast paths; no `-march`, no dispatch, no SIMD, no libgcc. Add a 256/64 division specialization and an oracle-driven test suite. [I]

## 1. Representation comparison

The value is `(-1)^sign * coefficient * 10^exponent`, with `coefficient` in `0..10^38-1`, a separate `dscale` for display, finite values only, and zero canonical and positive. [V design]

| Option | Size and precision | Add/sub | Multiply | Divide | Parse/format | Fit for obo |
|---|---|---|---|---|---|---|
| **128-bit binary coefficient + exponent + dscale (approved)** | 24 B; 38 digits; exponent `int16`; dscale `uint16` | 128/256-bit integer adds; align by `10^d` | 4x 64x64 -> 128 partial products; 256-bit exact | 256/128 Knuth D with a 192-bit quotient | SWAR parse; base-`10^19` format | **Recommended** [I] |
| Base-`10^9` limbs | 5 x 32-bit = 20 B coefficient plus metadata (~24-32 B); 45 digits | 5-limb carry loops | 25 partial products | base-`10^9` long division | Decimal digits are natural | Good for the later arbitrary `numeric` tier; not the fixed 24-byte type [V design; I] |
| Base-`10^19` limbs | 2 x 64-bit = 16 B coefficient; exactly 38 digits | Binary multiply by `10^d`, then renormalize | 2x2 limb products; rounding can drop low limbs | 2-limb base-`10^19` division; normalization and quotient estimation are harder | Natural 19-digit chunks | Interesting alternative, but no measured advantage and more complex division; not recommended without a prototype [I] |
| DPD / IEEE decimal128 | 16 B; **34 digits**; BID or DPD; NaN/Inf/signed zero | software on x86 | software on x86 | software on x86 | software | Wrong precision and semantics; measured GCC `_Decimal128` is much slower. [V] |
| .NET-style 96-bit | 16 B; 28-29 digits | simple | 96x96 -> 192 | 192/96 | software | Cannot hold 38 digits. [V design] |
| 256-bit coefficient storage | ~40 B; up to 77 digits | larger | 256x256 -> 512 for full generality | much larger | larger | Conditional Oracle 39-40-digit upgrade only; doubles the ABI. [V design] |

Why the approved layout wins:

- The coefficient is a binary integer, so add/sub/compare are ordinary multi-word integer operations and the fast path is a single 64-bit word.
- 256 bits hold the exact 76-digit product; no precision is lost before the single final rounding.
- SQL Server and ASE send a little-endian binary magnitude, so the coefficient maps directly to the wire format. Oracle's base-100 and PostgreSQL's base-10000 need division, but neither is the storage format of this type.
- DPD and decimal128 are storage/interchange formats with a 34-digit limit and special values; they are the wrong primitive for a 38-digit, finite-only, 24-byte type.

A base-`10^19` coefficient deserves one caution. It can make multiplication rounding cheap by dropping low limbs, but addition still needs alignment by an arbitrary power of ten, division needs a base-`10^19` normalization and quotient estimate, and there is no measured implementation here. It is not recommended on current evidence; it would be a prototype only if division later dominates. [I]

## 2. Algorithms

### 2.1 Add and subtract

1. Compare signs. Same sign: add magnitudes. Different signs: subtract the smaller magnitude from the larger and use the larger's sign.
2. Align exponents to `e = min(ea, eb)`: multiply the coefficient with the larger exponent by `10^(|ea-eb|)`.
3. Compute into a 256-bit accumulator. If the aligned coefficient exceeds 38 significant digits, round once to 38 digits using the requested mode; record inexact if required. If the exponent difference is so large that the smaller operand cannot affect the top 38 digits, only its sticky contribution needs to be retained, not a full 256-bit alignment.
4. Set `dscale = max(dscale_a, dscale_b)`; preserve the display scale. Check the `int16` exponent range and overflow after rounding. Zero is canonical positive.

Measured alignment-plus-add cost (including a 256-bit result and checksum):

| Case | Median ns/op | Min ns/op | Repeat range (median) |
|---|---:|---:|---:|
| exponent difference 0 (no multiply) | 0.727 | 0.629 | 0.635-0.727 |
| exponent difference 4 (one 128x64) | 1.672 | 1.502 | 1.672-1.688 |
| exponent difference 19 (one 128x64) | 1.620 | 1.514 | 1.597-1.620 |
| exponent difference 38 (128x64 then 192x64) | 3.229 | 2.920 | 3.115-3.254 |
| variable difference 0..38 | 2.836 | 2.584 | 2.685-2.929 |

A pure 128-bit coefficient add with equal exponents costs 0.44 ns; the 256-bit result materialization and checksum account for most of the 0.64-0.73 ns at difference 0. The table is the cost of producing a 256-bit aligned sum, not of a complete decimal add with metadata and traps. [V; I for the complete-operation caveat]

A complete add that must round because the aligned sum exceeds 38 digits adds a division step. The full-width rounding case costs the 256/128 division path measured in §5.3 (26.9 ns), so the common no-round add is roughly 1.6-3.2 ns and the exceptional round is roughly 28 ns. Implement the no-round path first and make the rounding path correct before optimizing it. [I]

### 2.2 Multiply

1. Compute the exact 256-bit product of the two 128-bit coefficients with four 64x64 -> 128 partial products. Measured 2.0-2.6 ns.
2. Let `digits` be the decimal digit count of the product (at most 76) and `k = digits - 38`. If `k <= 0`, the product fits and no rounding is needed.
3. If `k > 0`, divide the 256-bit product by `10^k` and round once, half away from zero by default. The divisor is a power of ten: if `k <= 19` the divisor fits a `u64` and a 256/64 path applies; if `k > 19` the divisor needs the 256/128 path. For two full 38-digit operands, `k = 38`, so the 256/128 path is mandatory in the worst case.
4. Set `dscale = min(dscale_a + dscale_b, maximum)` and `exponent = ea + eb + k`. After rounding, if the coefficient reached `10^38`, divide by ten and increment the exponent; `10^38` normalizes to coefficient 1 with exponent 38 and is not an overflow of the type. [V design]
5. Overflow if the normalized coefficient still exceeds 38 digits or the exponent leaves `int16`; trap by default, with `Result` variants later.

Measured cost of the full-width rounded multiply (128x128 -> 256, then divide by `10^37` or `10^38`, then half-away increment): 29.2 ns median, 26.5 ns min. The multiply alone is 2.0-2.6 ns; the 256/128 division dominates. [V]

Intel libbid avoids the division in rounding: it precomputes reciprocal constants `Kx` that approximate `10^-x` rounded up, multiplies the coefficient by `Kx` (after adding a half-unit term) and corrects near the boundary, with rounding helpers partitioned by digit count (`round64_2_18`, `round128_19_38`, `round192_39_57`, `round256_58_76`). That is a proven alternative to the 256/128 division used here, but it was not implemented or measured in this spike. Start with the division path for correctness and consider reciprocal constants only if rounding is hot in profiles. [V: https://github.com/gcc-mirror/gcc/tree/master/libgcc/config/libbid; I]

The 256/64 specialization is not measured in this spike. It should be implemented and added to the benchmark suite; do not assume its cost from the 64/64 and 256/128 endpoints. [I]

### 2.3 Divide

1. Division requires an explicit result scale/mode (or a documented checked default). There is no hidden low-precision default.
2. Scale the numerator by `10^g` so the quotient has 38 digits plus at least one guard digit, then divide the 256-bit numerator by the 128-bit denominator with Knuth Algorithm D. The quotient can exceed 128 bits during the process; the core must return a 192-bit quotient and round to 38 digits afterwards, not assume a 128-bit quotient.
3. Round once. The benchmark's decimal path performs the 256/128 division and then divides the 192-bit quotient by 10 to obtain the 38-digit result and the rounding digit; measured 33.4 ns median, 32.2 ns min.
4. Division by zero traps; checked variants return `Result`. The result's `dscale` and exponent follow the chosen scale policy.

Three proven division strategies exist in the surveyed implementations. Intel libbid estimates the quotient with binary floating point, subtracts `estimate * divisor`, corrects by comparison and subtraction, and returns a quotient plus a residual, rounding by comparing `2 * residual` with the divisor. .NET divides with trial quotient limbs (`Div96By32`, `Div128By64`, `Div128By96`) and scales in `10^9` chunks. libmpdec keeps one guard digit and applies a sticky-LSD adjustment. [V: https://github.com/gcc-mirror/gcc/tree/master/libgcc/config/libbid, https://github.com/dotnet/runtime/blob/main/src/libraries/System.Private.CoreLib/src/System/Decimal.DecCalc.cs, https://www.bytereef.org/mpdecimal/] Knuth D is chosen here because it needs no floating-point estimate and is straightforward to verify against an oracle; the alternatives are worth revisiting only if division dominates a profile. [I]

Measured division endpoints:

| Operation | Median ns/op | Min ns/op |
|---|---:|---:|
| `divq` 64/64 | 1.570 | 1.446 |
| `divq` 128/64 | 3.425 | 3.315 |
| Knuth D 256/128 (192-bit quotient) | 26.928 | 25.329 |
| decimal divide + round to 38 digits | 33.398 | 32.193 |

`divq` divides a 128-bit `RDX:RAX` by a 64-bit divisor and produces a 64-bit quotient; 256/128 is not a single instruction. Knuth D uses normalized divq estimates and correction steps. [V ISA semantics; V measurements]

A 256/64 path for divisors that fit a `u64` (for example dividing by `10^4` or `10^19`) is likely cheaper than 256/128 but was not measured; implement and measure it rather than assuming. The same applies to a reciprocal-based full division. Division is the largest single arithmetic cost and the best later optimization target. [I]

### 2.4 Compare, equality and hash

Numeric comparison must ignore display scale. A practical sequence:

1. Compare signs; different signs decide immediately (zero is positive).
2. Compare adjusted exponents `exponent + decimal_digits(coefficient)`. A different adjusted exponent decides the magnitude without alignment.
3. If equal, align the coefficients by the (bounded) exponent difference and compare the 128-bit integers.

Equality and hashing use the same normalized numeric value, not the stored `dscale`. This avoids the Java `BigDecimal.equals` trap where `1.0` and `1.00` are unequal despite `compareTo == 0`. [V Java semantics; I algorithm]

The compare path was not separately benchmarked; it is a handful of integer operations relative to the arithmetic measured here. It must still be tested for the equal-value/different-scale case and for cancellation. [I]

### 2.5 Parse and format

Parsing:

- Recognize sign, digits, decimal point and exponent; count significant digits; reject or explicitly round above 38 digits according to the literal policy.
- Accumulate the coefficient with SWAR eight-digit chunks. The measured SWAR parse of 38 digits is 9.3 ns; a 19+19 chunk parse is 17.0 ns; a naive `u128` multiply-by-ten loop is 53.3 ns. For 18 digits, the SWAR path is 14.2 ns and the naive `u64` loop is 24.4 ns.
- Preserve the literal's display scale (`1.50` has dscale 2); apply the exponent and check the `int16` range. Parsing above 38 significant digits is an explicit error/rounded conversion, never silent truncation. [V design; V measurements]

Formatting:

- Produce decimal digits with base-`10^19` chunks: two 128/64 divisions to split the coefficient, then two 19-digit chunks formatted with a two-digit table and divisions by 100. Measured 23.8 ns for 38 digits; base-`10^9` chunking is 33.7 ns; an 18-digit `u64` path is 10.3 ns.
- Insert the decimal point from `dscale`, preserving trailing zeros; formatting must not normalize unless asked.

| Operation | Median ns/op | Min ns/op | Repeat range (median) |
|---|---:|---:|---:|
| parse 38 digits, naive `u128` loop | 53.254 | 51.857 | 53.254-56.137 |
| parse 38 digits, 19+19 chunks | 16.970 | 16.289 | 16.970-17.840 |
| parse 38 digits, SWAR 8-digit chunks | 9.252 | 8.488 | 9.252-9.686 |
| parse 18 digits, naive `u64` loop | 24.374 | 23.288 | 24.374-25.739 |
| parse 18 digits, SWAR | 14.164 | 12.982 | 14.164-14.691 |
| format 38 digits, base `10^19` | 23.785 | 22.247 | 23.785-24.410 |
| format 38 digits, base `10^9` | 33.725 | 33.032 | 33.725-36.003 |
| format 18 digits, `/100` pairs | 10.312 | 9.560 | 10.053-10.312 |

### 2.6 64-bit fast path

Any coefficient below `2^64` fits a `u64`; that covers all 19-digit values (below `10^19`) and 20-digit values up to 18,446,744,073,709,551,615. The fast path is an internal branch on the high 64-bit coefficient word being zero; the value remains 24 bytes and the ABI does not change. Stages that rely specifically on a 19-digit bound (a `10^19` parse chunk, or a power-of-ten multiplier that must stay in 64 bits) must test the value against `10^19` rather than only the high word.

| Operation | Measured cost |
|---|---|
| 64-bit add | 0.43 ns |
| 128-bit add | 0.44-0.50 ns |
| 64x64 -> 64 low multiply | 0.25-0.29 ns |
| 64x64 -> 128 multiply | 0.43-0.49 ns |
| 128x128 -> 256 multiply | 2.52-2.66 ns |
| 64/64 divide | 1.57 ns |
| 256/128 divide | 26.9 ns |

Add is not much cheaper at 64 bits because the loop is dominated by loads and stores, but multiply and divide are dramatically cheaper. The 64-bit fast path is therefore most valuable in multiplication, division, parse/format and mixed workloads. The ledger measurements (§5.6) show the aggregate effect. [V; I]

### 2.7 Overflow, underflow, division by zero, zero

- Coefficient above `10^38-1` after normalization is overflow; exponent outside `int16` is overflow/error. `10^38` normalizes to coefficient 1 and exponent 38 and is not an overflow of the type, though a database column may still reject it. [V design]
- There is no separate underflow value; an exponent below `int16` is an error, and rounding to zero is recorded as inexact when the API supports it. [V design]
- Zero is canonical positive, with no signed zero. [V design]
- Division by zero traps by default; checked arithmetic returns `Result`. [V design]

### 2.8 Rounding

Half away from zero is the default for database scale conversion and `round`/`quantize`; half-even and the other modes are explicit. One rounding only: intermediate products and aligned sums keep guard digits and a sticky indicator, and the final step rounds. Do not round the multiply product and then round again during a scale change. [V design; I implementation rule]

The rounding decision itself is a remainder comparison against half the divisor (with an increment for half away from zero); no double rounding and no pre-rounding of the 256-bit intermediate. [I]

## 3. Instruction-set findings

### 3.1 There is no x86-64 hardware decimal floating point

- No x86-64 instruction performs IEEE 754 decimal32/64/128 arithmetic. Intel's decimal128 support is a software library: the Intel Decimal Floating-Point Math Library describes itself as a software implementation of IEEE 754 decimal arithmetic. [V: https://www.intel.com/content/www/us/en/developer/articles/tool/intel-decimal-floating-point-math-library.html]
- GCC on x86 uses libbid/BID for `_Decimal32/64/128`; POWER and S/390 use DPD. [V: https://gnu.googlesource.com/gcc/+/refs/tags/release-12.2.mpacbti-rel1/config/dfp.m4]
- The x86 BCD adjust instructions (`AAA`, `AAS`, `AAM`, `AAD`, `DAA`, `DAS`) are not valid in 64-bit mode; x87 `FBLD`/`FBSTP` only convert packed BCD to/from 80-bit floating point. There is no packed-BCD multiply or divide. [V: https://www.felixcloutier.com/x86/daa, https://www.felixcloutier.com/x86/fbld, https://learn.microsoft.com/en-us/windows-hardware/drivers/debugger/x64-instructions]
- CPUID leaf 7 has no decimal floating-point feature bit. [V: https://www.felixcloutier.com/x86/cpuid]

### 3.2 POWER and IBM Z have hardware decimal, but it is 34-digit DPD

- Power ISA Book I Chapter 5 specifies decimal floating point in short/long/extended formats, DPD-encoded, with `dadd`/`dmul`/`ddiv` and related instructions. [V: Power ISA v3.1C, http://wiki.raptorcs.com/w/images/5/5f/OPF_PowerISA_v3.1C.pdf]
- IBM Z has a decimal floating-point facility with DPD encoding and `ADTR`/`MDTR`/`DDTR`-style instructions. [V: https://public.dhe.ibm.com/software/dw/linux390/perf/dfp.pdf, https://www.ibm.com/support/pages/sites/default/files/2022-09/SA22-7871-11.pdf]
- Hardware decimal128 is a **34-digit** format, not 38 digits; a true 38-digit coefficient still needs a 128-bit binary integer and software 128x128 -> 256 multiply. Hardware DFP is not a shortcut for this type. [V; I]

### 3.3 Scalar instructions and measured usefulness

| Facility | What it does | Single 38-digit value? | Batch? | Measured / documented |
|---|---|---|---|---|
| `MUL` + `ADC` | 64x64 -> 128 and carry chains | Yes; compiler-generated baseline | Yes, scalar loop | 128x128 -> 256: 2.0-2.1 ns (default build), 2.2-2.3 ns with `-mbmi2 -madx`. Zen 4 `MUL r64` latency 3/4/3, throughput 1.00. [V: https://www.uops.info/html-instr/MUL_R64.html] |
| BMI2 `MULX` | 64x64 -> 128 without flags | Yes, but no measured gain here | Slightly easier scheduling | 2.1-2.35 ns; Zen 4 latency 4/3, throughput 1.00. [V: https://www.uops.info/html-instr/MULX_R64_R64_R64.html] |
| ADX `ADCX`/`ADOX` | Two independent carry chains | Marginal; best 1.96-2.08 ns with `-mbmi2 -madx`, versus 2.17-2.31 ns baseline in the same build; default-build ADX was 2.1-2.3 ns versus 2.0-2.1 ns baseline | Helps larger multi-limb loops | Not worth a higher minimum CPU for one product. Zen 4 `ADCX`/`ADOX` 1-cycle latency, 0.38 throughput. [V: https://www.uops.info/html-instr/ADCX_R64_R64.html, https://www.uops.info/html-instr/ADOX_R64_R64.html] |
| `DIVQ` | 128/64 -> 64 quotient and remainder | Yes, but 256/128 needs several steps | No | 64/64 1.57 ns, 128/64 3.43 ns, 256/128 26.9 ns. Zen 4 latency 9-17, throughput 7.00. [V: https://www.uops.info/html-instr/DIV_R64.html, https://www.felixcloutier.com/x86/div] |
| AVX2 | 32x32 -> 64 vector multiply; no 64x64 -> 128 | No | Only with 32-bit limb decomposition | Not useful for this coefficient; no 64-bit full multiply. [I] |
| AVX-512 IFMA `VPMADD52LUQ/HUQ` | 52-bit lane multiply-add into 64-bit accumulators | No; single value 29-30 ns | Yes, if results stay vector-resident | Batch of 8: 0.78-0.89 ns/product; batch plus pack: 2.9-3.1 ns/product; single value: 29-30 ns. [V: https://www.felixcloutier.com/x86/vpmadd52luq] |
| Legacy BCD adjust | `DAA`/`DAS`/`AAA`/`AAS` | Unusable in 64-bit mode | No | #UD in 64-bit mode; `FBLD`/`FBSTP` only load/store BCD. [V] |

Host flags include `bmi1 bmi2 adx avx2 avx512f avx512ifma avx512dq avx512vl avx512bw avx512vbmi avx512_vbmi2`; the host is a Zen 4 AMD Ryzen 7 8745HS. [V: `/proc/cpuinfo`]

The IFMA result deserves emphasis. A batch of eight independent products kept in vector registers is 2.4-2.7x faster than scalar. Packing each product into the ordinary 256-bit result destroys the gain. A single value is an order of magnitude slower. IFMA is therefore a possible bulk/array optimization later, never a scalar arithmetic path. [V; I]

Intel libbid V2.5 contains no SSE or AVX intrinsics: a source search of its decimal arithmetic files for `_mm_`, `__m128` and AVX found nothing, and no AVX-512 IFMA decimal128 library was found in this research. IFMA appears in the literature for big-integer multiplication and binary-to-decimal conversion, not for fixed decimal coefficients. [V: https://www.netlib.org/misc/intel/, https://github.com/gcc-mirror/gcc/tree/master/libgcc/config/libbid]

### 3.4 AVX-512 feature checks, if a batch path is ever added

- CPUID leaf 7, subleaf 0, EBX: BMI2 bit 8, AVX512F bit 16, ADX bit 19, AVX512_IFMA bit 21. [V: https://www.felixcloutier.com/x86/cpuid]
- AVX-512 also requires OSXSAVE (leaf 1 ECX bit 27) and XCR0 bits 7:5 and 2:1 set. GCC's own detection checks exactly those conditions. [V: https://github.com/gcc-mirror/gcc/blob/master/gcc/common/config/i386/cpuinfo.h]
- Detect once at startup with manual CPUID, cache the result, and branch or use a function pointer. Never execute CPUID per operation. [V measurement; I rule]

## 4. Dispatch versus baseline

### 4.1 CPUID cost

On this WSL2 host, a CPUID leaf-7 read costs 463-472 ns per operation; a cached feature-flag test costs 0.44-0.50 ns. The absolute CPUID cost is inflated by virtualization (this is an [O] environment result, not a bare-metal number), but the conclusion is robust: CPUID is hundreds of times more expensive than a cached branch and must never be in the per-value path. [V/O]

### 4.2 `target_clones` and ifunc do not work in the freestanding runtime

The obo runtime is linked with `ld -T runtime/rt.ld`, not with the GCC driver, and has a custom `_start`; no libc startup code runs. [V: `obo/Makefile`, `obo/runtime/entry.S`]

Observed:

- With `-nostdlib`, a `target_clones("default","avx512f")` function fails to link: undefined `__cpu_indicator_init` and `__cpu_model` from the generated resolver. [V]
- With `-nostdlib -lgcc`, it links, but the binary hangs on the first call (`timeout 5` returns 124). The object contains an `R_X86_64_IRELATIVE` relocation at GOT address `0x405000` with addend `0x402110` (`work.resolver`), and the GOT slot contains `0x401000`, the address of the PLT stub itself. Without glibc's startup applying IRELATIVE relocations, the PLT jumps to itself forever. [V: `readelf -r`, `objdump -d`, observed run]
- `__builtin_cpu_supports("avx512f")` without libgcc fails to link (`__cpu_model` undefined). With `-lgcc` it links but returns false if `__cpu_indicator_init` has not run; a test program exited 7 with the feature present. Calling `__cpu_indicator_init()` manually before the test returned true (exit 0). Libgcc normally runs this from a constructor of priority 101, which a custom `_start` does not run. [V: https://github.com/gcc-mirror/gcc/blob/master/libgcc/config/i386/cpuinfo.c]

GCC documents that function multiversioning relies on the ELF indirect-function extension; static glibc's startup applies the IRELATIVE relocations before `main`, which is exactly what the freestanding runtime lacks. [V: https://gcc.gnu.org/onlinedocs/gcc/Function-Multiversioning.html, https://sourceware.org/pipermail/libc-alpha/2012-August/033716.html]

Consequences:

- Do not use `target_clones` or ifunc in the runtime.
- Do not depend on libgcc's `__cpu_model`; the runtime does not link libgcc, and the constructors do not run.
- If a future batch path needs a feature check, use manual CPUID once at startup and a cached flag/function pointer. [I]

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
- Timing: `CLOCK_MONOTONIC_RAW` for ns and `rdtsc` for TSC; five warmup passes, 21 timed samples per benchmark, median and minimum reported, samples sorted with `qsort`. Counters use N=512 values and REPS=256 (131,072 operations per sample) unless noted; the slow 38-digit ledger uses REPS=64. All runs pinned with `taskset -c 3`. [V]
- The host was not idle: other work was running during the runs (load average about 5), so medians vary by roughly 5-10% between repeats and occasionally more. Key multiply, fast-path, alignment, ledger and ABI measurements were repeated 2-3 times; tables show a representative median (min) and, where useful, the repeat range. Differences below about 10% are not treated as real. The numbers are Zen 4/WSL2 results, not a cross-CPU survey. [O]
- Build: static, non-PIE, `-O2 -fno-pic -fno-pie -no-pie -mcmodel=small -fno-tree-vectorize -std=gnu11`, no `-march`. The IFMA kernels carry their own `target("avx512f,avx512ifma")` attribute. `-fno-tree-vectorize` keeps the scalar loops scalar.
- Arithmetic kernels were checked against Python big-integer vectors: 1,000 128x128 -> 256 products, 1,000 IFMA products, 512 256/128 divisions, 256 multiply-and-round cases, 512 parse/format cases, 512 alignment cases and 512 ledger cases. All reported 0 bad. [V]

Exact build and run commands (all sources under `/tmp/obo-decimal-arith/`):

```text
gcc -O2 -fno-pic -fno-pie -no-pie -mcmodel=small -fno-tree-vectorize -std=gnu11 \
    -DFLAGS_TAG='"default -O2 (no -march)"' bench_mul.c -o bench_mul
gcc -O2 -mbmi2 -madx -fno-pic -fno-pie -no-pie -mcmodel=small -fno-tree-vectorize \
    -std=gnu11 -DFLAGS_TAG='"-O2 -mbmi2 -madx (global)"' bench_mul.c -o bench_mul_bmi2adx
gcc -O2 -fno-pic -fno-pie -no-pie -mcmodel=small -fno-tree-vectorize -std=gnu11 bench_div.c -o bench_div
gcc -O2 -fno-pic -fno-pie -no-pie -mcmodel=small -fno-tree-vectorize -std=gnu11 bench_conv.c -o bench_conv
gcc -O2 -fno-pic -fno-pie -no-pie -mcmodel=small -fno-tree-vectorize -std=gnu11 bench_align.c -o bench_align
gcc -O2 -fno-pic -fno-pie -no-pie -mcmodel=small -fno-tree-vectorize -std=gnu11 bench_ledger.c -o bench_ledger
gcc -O2 -fno-pic -fno-pie -no-pie -mcmodel=small -fno-tree-vectorize -std=gnu11 bench_abi.c -o bench_abi
gcc -O2 -fno-pic -fno-pie -no-pie -mcmodel=small -fno-tree-vectorize -std=gnu11 bench_fast.c -o bench_fast
gcc -O2 -fno-pic -fno-pie -no-pie -mcmodel=small -fno-tree-vectorize -std=gnu11 bench_dispatch.c -o bench_dispatch
gcc -O2 -fno-pic -fno-pie -no-pie -mcmodel=small -fno-tree-vectorize -std=gnu11 bench_dbconv.c -o bench_dbconv
taskset -c 3 ./bench_mul
taskset -c 3 ./bench_div
...
```

The benchmark sources are `bench_common.h`, `bench_mul.c`, `bench_div.c`, `bench_conv.c`, `bench_align.c`, `bench_ledger.c`, `bench_abi.c`, `bench_fast.c`, `bench_dispatch.c` and `bench_dbconv.c`; vector files are `vectors_*.bin/.txt`; the saved runs are in `/tmp/obo-decimal-arith/results/`. None of this is in the repository. [V]

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

Repeated default-build runs: baseline 1.98-2.12 ns, MULX 2.14-2.35 ns, ADX 2.11-2.35 ns, IFMA batch 0.78-0.89 ns. [V]

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

Repeated `-mbmi2 -madx` runs: baseline 2.17-2.31 ns, MULX 2.20-2.32 ns, ADX 1.96-2.08 ns, IFMA batch 0.81-0.89 ns. The ADX advantage in this build (about 5-10% over baseline) is the only place ADX looked better, and the best default-build baseline (1.98 ns) already matches the best ADX result (1.96 ns). The serial-chain outlier in the first global-flags run (3.961 ns) did not reproduce; repeats were about 2.0 ns. [V/O]

### 5.3 Division and decimal divide/round

| Operation | Median ns/op | Min ns/op | TSC/op | Mops/s |
|---|---:|---:|---:|---:|
| `divq` 64/64 | 1.570 | 1.446 | 5.953 | 637.07 |
| `divq` 128/64 | 3.425 | 3.315 | 12.990 | 291.99 |
| Knuth D 256/128, 192-bit quotient | 26.928 | 25.329 | 102.138 | 37.14 |
| Decimal divide + round to 38 digits | 33.398 | 32.193 | 126.676 | 29.94 |
| 128x128 multiply + round to 38 digits | 29.238 | 26.532 | 110.897 | 34.20 |
| GCC `_Decimal128` divide (34-digit, libgcc) | 184.414 | 175.989 | 699.473 | 5.42 |
| GCC `_Decimal128` multiply (34-digit, libgcc) | 46.102 | 44.129 | 174.862 | 21.69 |

Repeat run: 1.675 / 3.693 / 27.598 / 34.504 / 30.441 / 180.941 / 49.607 ns. [V]

The GCC `_Decimal128` loop includes operand setup (`a + i`, `b + (i&31)`) and full IEEE decimal semantics; the obo kernels are the inner coefficient arithmetic without IEEE flags, NaN/Inf or exponent metadata. The comparison is therefore directional, not an exact like-for-like benchmark, but the 5.5x divide and 1.6x multiply gaps are large enough to reject libbid-style general decimal arithmetic as the implementation strategy. [V measurements; I comparison]

### 5.4 Parse and format

See §2.5 for the table. The SWAR parser is 5.8x faster than the naive `u128` loop and 1.8x faster than 19+19 chunks. Base-`10^19` formatting is 1.4x faster than base-`10^9`; the two-digit table and division by 100 matter more than the chunk base. [V; I]

### 5.5 Exponent alignment plus add

See §2.1. The alignment kernel multiplies a 128-bit coefficient by a table-selected power of ten, forms a 256-bit result and adds the other 128-bit coefficient; it was checked against 512 Python-generated 256-bit sums with 0 bad. The measured cost is 1.6-1.7 ns for a small exponent difference, 3.1-3.3 ns at a difference of 38, and 2.7-2.9 ns for a variable difference. [V]

### 5.6 64-bit fast paths and ledger loop

Fast-path microbenchmarks, observed median range across the main run and three repeats:

| Operation | Median ns/op (main + repeats) |
|---|---:|
| 64-bit add | 0.426-0.492 |
| 128-bit add | 0.438-0.503 |
| 64x64 -> 64 low multiply | 0.246-0.289 |
| 64x64 -> 128 multiply | 0.433-0.488 |
| 128x128 -> 256 multiply | 2.520-2.656 |

One first run reported 0.877 ns for 64x64 -> 128 and 3.711 ns for 128x128 -> 256; those were not reproducible and are excluded as noise. [V/O]

Ledger-style loop, per line item (price times quantity, tax division by a runtime divisor, accumulation):

| Tier | Median ns/op | Min ns/op | Repeat range |
|---|---:|---:|---:|
| 64-bit exact invoice (mul, divide by 10000) | 1.693 | 1.579 | 1.623-1.693 |
| 128-bit exact invoice (mul, 128/64 divide) | 4.597 | 4.204 | 4.394-4.597 |
| 38-digit path (mul, 256/128 divide, round twice) | 55.341 | 51.730 | 55.341-58.606 |

The 64-bit tier is about 2.7x faster than the 128-bit exact tier and about 33x faster than the full 38-digit path. The full path's two 256/128 divisions dominate, as expected from §5.3. [V; I]

### 5.7 24-byte ABI costs

The value is a 24-byte struct `{ uint64_t w0, w1, meta; }`; the helper takes a hidden destination pointer and two operand pointers and is marked `noinline`. The indirect path calls through a `volatile` function pointer. The first columns are a representative run; the last column is the range of medians across the two repeat runs.

| Variant | Median ns/op | Min ns/op | Repeat median range |
|---|---:|---:|---:|
| Inline 128-bit add + metadata copy | 0.361 | 0.360 | 0.385-0.511 |
| No-inline helper, direct call | 1.348 | 1.283 | 1.384-1.547 |
| No-inline helper, indirect call | 1.064 | 1.051 | 1.078-1.156 |
| 24-byte struct copy | 0.170 | 0.169 | 0.202-0.214 |
| 8-byte word copy | 0.064 | 0.064 | 0.065-0.080 |

The direct/indirect call overhead over inline is about 0.7-1.0 ns for this trivial add. The indirect call was not slower than the direct call on this CPU, so a cached dispatch pointer would not add a measurable penalty; the reason not to dispatch is that there is no useful scalar variant. A 24-byte copy costs about 2.7x an 8-byte copy. [V; I]

For multiply and divide, the helper call overhead is negligible relative to 29-33 ns. For add, the call is most of the cost when the exponents are already equal (1.35 ns helper versus 0.36 ns inline); this is worth revisiting only after the language feature exists and profiles show add as hot. [V; I]

### 5.8 Database magnitude conversion

The formats and measured per-value conversion costs (one value per operation, 512 values, 256 reps):

| Target | Encoding | Work | Median ns/op | Min ns/op |
|---|---|---|---:|---:|
| SQL Server TDS | little-endian 128-bit magnitude + sign | 16-byte copy | 0.115 | 0.114 |
| Sybase ASE | base-256 magnitude | 16 shifts | 7.921 | 7.442 |
| PostgreSQL `numeric` | base-10000 digits | 10 divmod128/64 | 38.051 | 35.999 |
| Oracle `NUMBER` | base-100 digits | 19 divmod128/64 | 69.150 | 67.782 |

The conversion cost is dominated by the number of division steps. SQL Server is effectively free and Sybase is under 10 ns; PostgreSQL and Oracle cost tens of nanoseconds per value. Batch/SIMD conversion is not worth initial effort: IFMA does not implement the base-100/base-10000 divisions, and scalar ILP across independent values is the realistic later optimization if bulk loads become hot. Database adapters are deferred per the approved design. [V measurements; I]

### 5.9 CPUID and dispatch

| Operation | Median ns/op | Min ns/op |
|---|---:|---:|
| CPUID leaf 7 | 463.424 | 459.245 |
| Cached feature-flag test | 0.444 | 0.438 |

The CPUID absolute cost is WSL2-specific; the ratio is the actionable result. [V/O]

### 5.10 Not measured or unavailable

- The 256/64 division and 256/64 rounding paths were not benchmarked; only the 64/64 and 256/128 endpoints were measured.
- Reciprocal-constant rounding and estimate-and-correct division were not implemented or timed.
- No bare-metal or non-Zen-4 CPU was available; all timings are WSL2 on one Zen 4 host under load.
- AVX-512 IFMA was measured only in the batch kernels shown. No AVX2 kernel was written because AVX2 has no 64x64 -> 128 multiply.
- `target_clones`/ifunc could not be made to work in the freestanding link; that is a functional result, not a performance result.
- Database formats were measured only as magnitude-conversion kernels; no database adapter or protocol code was written or run.
- General IEEE decimal semantics (NaN/Inf, all five libbid rounding modes) were not tested; only obo's finite coefficient paths were checked.

## 6. Existing implementations and lessons

This section builds on the primary sources already collected in `decimal-research-2026-10-28.md` §8; the URLs below were re-checked for this note. No implementation code was copied; the licenses differ and the project is Apache-2.0. [V citations; I license rule]

| Implementation | Representation and size | Arithmetic strategy | Semantics | Lesson for obo |
|---|---|---|---|---|
| **Intel Decimal Floating-Point Math Library (libbid, decimal128 BID)** | Sign + 14-bit biased exponent (bias 6176) + 113-bit binary coefficient; 34 digits; 16 B; software | 128x128 -> 256 from two 64x128 partial products; kernel chosen by digit count `q1+q2` (<=19 fits `u64`, 20 uses 64x64 -> 128, <=38 128-bit, 39 uses 128/192, <=57 256-bit); rounding by precomputed reciprocal constants rather than division; divide by binary-float estimate plus residual correction; multiply shares the FMA kernel; no SIMD in V2.5 | IEEE 754 decimal, flags, NaN/Inf, five rounding modes | Closest fixed binary-coefficient reference; the digit-count kernel split directly supports obo's 64-bit fast path, but 34 digits and full IEEE semantics are more than this type needs. [V: https://www.netlib.org/misc/intel/, https://github.com/gcc-mirror/gcc/tree/master/libgcc/config/libbid, https://www.scitepress.org/Papers/2006/13209/13209.pdf] |
| **GCC `_Decimal128` on x86-64** | libbid/BID software on x86; libdecnumber/DPD on PowerPC and other targets (base-1000 units, `DECDPUN=3`) | Same libbid kernels on x86; DPD arithmetic in libdecnumber elsewhere | IEEE decimal via fenv; 34 digits | Measured 46.1 ns multiply and 184.4 ns divide in this harness, versus 29.2/33.4 ns for the specialized 38-digit kernels. GCC is GPLv3 with the Runtime Library Exception; the bundled libbid files carry Intel's BSD-style notice. General IEEE software is not a performance baseline to copy. [V: https://gnu.googlesource.com/gcc/+/refs/tags/release-12.2.mpacbti-rel1/config/dfp.m4, https://gcc.gnu.org/onlinedocs/gcc/Decimal-Float.html, measured] |
| **.NET `System.Decimal` / DecCalc** | 96-bit coefficient (three 32-bit limbs), sign, scale 0-28 in a flags word; 16 B; 28-29 digits | `Math.BigMul` 64-bit fast path; nine partial products into a 192-bit `Buf24`; `ScaleResult` reduces 192 -> 96 with half-even and a sticky bit; divide by `Div96By32`/`Div128By64`/`Div128By96` with trial limbs and scaling in `10^9` chunks | No NaN/Inf; scale preserved; numeric equality; half-even only; overflow and divide-by-zero throw | Limb arithmetic, `ScaleResult`-style rounding and the 64-bit fast path are useful references, but 96 bits cannot hold 38 digits. [V: https://learn.microsoft.com/en-us/dotnet/api/system.decimal?view=net-10.0, https://github.com/dotnet/runtime/blob/main/src/libraries/System.Private.CoreLib/src/System/Decimal.DecCalc.cs] |
| **Rust `rust_decimal`** | 96-bit coefficient + `u32` flags (scale bits 16-23, sign bit 31); 16 B; 28-29 digits; `Copy` | `u128` product when both operands fit `u64`; `Buf24` partial products otherwise; `Dec64` add path when the scale difference is <=19; `div32`/`div64`/`div96` with normalized divisors; seven public rounding strategies | Trailing zeros preserved; numeric equality/hash; no NaN/Inf; operators panic, `checked_*` methods return `Option` | Existence proof that an allocation-free fixed decimal is practical, and it uses the same 64-bit/128-bit fast-path split; still 28-29 digits. [V: https://docs.rs/rust_decimal/latest/rust_decimal/, https://github.com/paupino/rust-decimal] |
| **Go `cockroachdb/apd`** | `big.Int` coefficient (two inline 64-bit words before heap) + `int32` exponent + sign; arbitrary precision | Upscale to a common exponent, `big.Int` add/mul; divide scales the dividend to the context precision, `QuoRem`, compares `2*R` with the divisor; eight rounding modes | NaN/Inf conditions, context traps, `Quantize`, `Reduce`; default half-up | The context machinery and special values are more than a fixed primitive needs; `Quantize` and the `2*R` comparison are good semantic references. [V: https://pkg.go.dev/github.com/cockroachdb/apd/v3, https://github.com/cockroachdb/apd] |
| **Python `decimal` / libmpdec** | Heap coefficient, **base-`10^19` limbs on 64-bit** (`10^9` on 32-bit), plus exponent; arbitrary precision | Base-`10^19` add/sub; base multiply, Karatsuba and NTT tiers by operand size; divide with one guard digit, a sticky-LSD adjustment and a Newton reciprocal for large divisors | NaN/Inf/signed zero; context; half-even default; trailing zeros | Best test oracle and semantic reference; base-`10^19` is an arbitrary-precision design, not evidence for a fixed 38-digit type; the guard-digit and sticky-LSD rules are directly useful for correct one-rounding. [V: https://docs.python.org/3/library/decimal.html, https://www.bytereef.org/mpdecimal/] |
| **Java `BigDecimal`** | `BigInteger` unscaled + `int` scale; `intCompact` long holds values up to 18 digits; arbitrary | Scale alignment by `10^diff`; `BigInteger` Karatsuba/Toom-Cook/Burnikel-Ziegler; divide computes a preferred scale, uses long division and applies the rounding mode | Eight rounding modes; `equals` is scale-sensitive, `compareTo` numeric; divide by zero and inexact `UNNECESSARY` throw | Strong warning about scale-sensitive equality; obo's numeric equality must ignore `dscale`. [V: https://docs.oracle.com/en/java/javase/21/docs/api/java.base/java/math/BigDecimal.html, https://github.com/openjdk/jdk/blob/master/src/java.base/share/classes/java/math/BigDecimal.java] |
| **IBM decNumber / General Decimal Arithmetic** | Arbitrary precision; context | Reference semantics for decimal arithmetic | IEEE-style context | Use as a semantic and test reference, not as a performance model; general context handling is too heavy for a fixed value. [V: https://speleotrove.com/decimal/] |

Published cross-implementation data is sparse and mostly third-party. The Intel ICSOFT 2006 paper reports decimal128 add/multiply/divide medians of 170/300/250 cycles for libbid versus 2,300/3,000/2,000 for decNumber on a 3.4 GHz Xeon, and claims one to two orders of magnitude overall. The libmpdec benchmark page reports 34-digit Mandelbrot times of 6.78 s (libmpdec) versus 8.07 s (libbid) and 13.10 s (decNumber) on a 3.16 GHz Core 2 Duo, with different rankings on a 32-bit Athlon. The Boost.Decimal page compares 20M-element vectors for GCC `_Decimal128`, Intel BID_UINT128 and Boost decimal128_t on an i9-11900K. These are 2006-2025 maintainer or third-party numbers on different CPUs; they are cited as context, not re-measured here. [V citations; O third-party]

Common lessons:

1. Fixed-width implementations converge on 96-bit/28-digit (.NET, rust_decimal) or 113-bit/34-digit (Intel/BID) coefficients. Neither reaches 38 digits; obo's 128-bit/38-digit choice is a genuine extension of the state of the art, not a reimplementation of an existing type. [V; I]
2. Binary-coefficient arithmetic dominates fixed-width implementations; base-`10^n` limbs appear only in arbitrary-precision libraries (base-`10^19` in libmpdec on 64-bit, base-1000 in libdecnumber, base-`10^9` in obo's planned arbitrary tier) where variable length matters. This supports the approved binary coefficient for the fixed type. [V; I]
3. General IEEE decimal software (libbid/libdecnumber) is slower than a specialized 38-digit core in these measurements: GCC `_Decimal128` multiply/divide are 46.1/184.4 ns versus 29.2/33.4 ns here. A small, fixed, finite-only core is the right performance strategy. [V; I]
4. Scale-sensitive equality is a known trap (Java, .NET); the design's numeric equality and separate `dscale` are correct. [V design; I]
5. Licenses: .NET (MIT), rust_decimal (MIT), apd (Apache-2.0), libmpdec (BSD-2-Clause) and the Intel libbid files (BSD-3-Clause-style) permit idea reuse; GCC's libdecnumber is GPLv3 with the Runtime Library Exception and Java is GPLv2+CE, so neither code nor tests should be copied into the Apache-2.0 runtime. Even where a license permits reuse, write independent C and use the libraries only as semantic oracles. [V licenses as stated by their projects; I rule]
6. Every fixed-width implementation has a 64-bit fast path: libbid switches on `q1+q2` digit counts, .NET uses `Math.BigMul`, rust_decimal uses `u128`/`Dec64`, Java uses `intCompact` and libmpdec uses `shortdiv` for one-limb divisors. This corroborates the measured 64-bit fast path and its priority. [V; I]
7. Division is the main algorithmic lever. The three proven approaches (libbid estimate-and-correct, .NET trial limbs, libmpdec guard digit plus Newton) all avoid a naive full-width loop, and libbid avoids division even in rounding by reciprocal constants. Those alternatives should be considered after the Knuth D baseline is correct and measured. [V; I]

## 7. Recommendation

### 7.1 Default implementation

- **Representation:** the approved 24-byte value: `word0` coefficient bits 0-63, `word1` bits 64-127, `word2` `int16` exponent, `uint16` dscale, sign/flags. Coefficient range `0..10^38-1`; zero canonical positive; finite only.
- **Language:** freestanding C in `runtime/` using `unsigned __int128` and explicit 64-bit limbs, compiled with the existing CFLAGS. No `-march`, no `-mbmi2`, no `-madx`, no libgcc.
- **Add/sub:** align to the smaller exponent with a table-driven `10^d` multiply into 256 bits; add/sub; round once to 38 digits only if needed; `dscale = max`; traps for overflow; guard+sticky for exact ties.
- **Multiply:** four 64x64 -> 128 products into a 256-bit product; divide by `10^k` and round once (half away by default); `dscale = sum`; normalize a `10^38` carry into exponent + 1. Implement a 256/64 path when `k <= 19`, and the Knuth 256/128 path for larger `k`. Reciprocal-constant rounding (libbid style) is a later optimization if multiply-and-round profiles hot.
- **Divide:** explicit result scale/mode; scale the numerator for 38 digits plus guard; Knuth D 256/128 with a 192-bit quotient; round once; division by zero traps. Add a 256/64 specialization for small divisors.
- **Compare/equality/hash:** numeric, ignoring `dscale`; adjusted exponent, then coefficient compare. [I]
- **Parse/format:** SWAR parse; base-`10^19` format with a two-digit table; preserve `dscale`; reject or explicitly round above 38 digits.
- **64-bit fast path:** branch on the high coefficient word being zero; use `u64` operations where they fit, especially multiply, divide, parse and format. Keep the 24-byte ABI.
- **Runtime interface:** hidden destination pointer plus operand pointers. For example `void rt_dec_add(dec24 *d, const dec24 *a, const dec24 *b)` and `void rt_dec_div(dec24 *d, const dec24 *a, const dec24 *b, uint64_t scale_mode)`. Pack scale and mode into one word to stay within the four-argument runtime limit. The compiler allocates the 24-byte destination temporary and reads it after the call; the helper returns `void`. [I]
- **Inline versus helper:** use runtime helpers for v1, including add and compare. The measured helper overhead is about 0.7-1.0 ns over inline; multiply/divide are dominated by arithmetic, and a single helper implementation reduces code size and trap-handling duplication. Inline add/sub/compare later only if profiles show it matters. [V; I]
- **Dispatch and SIMD:** none for scalar values. No CPUID, no `target_clones`, no ifunc, no libgcc, no AVX-512 requirement. If a bulk/array API later justifies IFMA, gate it with one manual CPUID check and a cached function pointer, and keep the scalar path as the baseline. [I]
- **Errors:** traps for overflow and division by zero by default; `Result` variants later, per the approved design. [V design]

### 7.2 Effort and risk

The design already sizes the compiler/runtime work at roughly 700-1,600 lines for layout/ABI, 800-1,600 lines for codegen, 1,000-2,000 lines for the runtime C arithmetic and 2,000-4,000 lines of tests, and rates the ABI/codegen/runtime work as high risk. [V design §6.1] This spike does not revise those estimates downward; the measurements mainly remove performance uncertainty from the core arithmetic.

Relative risks:

- **High:** 24-byte ABI plumbing (parameters, returns, fields, arrays, globals, constants, `Result[decimal]`, `.sym`, hidden-pointer aliasing), and division/rounding semantics.
- **Medium:** exact one-rounding add/sub with cancellation and sticky bits; parse/format edge cases; exponent/dscale normalization.
- **Low-medium:** raw arithmetic throughput; the measured core is comfortably fast for ledgers and CLI tools, and the 64-bit fast path covers common small values.
- **Low:** CPU feature availability, because the recommended path requires only baseline x86-64.

### 7.3 Implementation order

1. **M0: oracle and goldens.** Python `decimal`/big-integer oracle for 38-digit arithmetic, exponent/dscale, rounding modes, overflow and boundary values; golden vectors for the formats in the design. This is the gate for every later step. [V design M0; I]
2. **M1a: value and basic operations.** 24-byte ABI, literals/constants, add/sub/compare, parse/format, traps, 64-bit fast path. Verify against oracle vectors and the benchmark suite.
3. **M1b: multiply and rounding.** 128x128 -> 256, 256/64 and 256/128 rounding paths, half-away and explicit modes, overflow normalization. Add the rounded-multiply benchmark and vectors.
4. **M1c: divide and quantize.** Explicit scale/mode policy, 256/128 core, 256/64 specialization, remainder, trap/Result behavior.
5. **M2: containers and symbols.** Fields, arrays, globals, constants, `Result[decimal]`, `.sym` form, ABI/format bumps.
6. **M3: database adapters.** SQL Server TDS first, then Oracle, ASE and PostgreSQL, with byte-format goldens; the measured conversion costs are advisory only. [V design staging; I order]
7. **Later:** arbitrary `numeric` tier, float interop after floats, Money/currency library, optional IFMA batch work only if a bulk API appears, and reciprocal-constant rounding if profiles justify it.

## 8. Risks and open questions

1. **Division result scale and mode are not finalized.** The design requires an explicit scale/mode or a documented checked default; this must be decided before implementing `/`. [V design; open]
2. **Add/sub rounding at the 38-digit boundary.** Exponent alignment can produce 39+ digits. The implementation needs guard+sticky and correct tie behavior, especially when the smaller operand is far below the larger (sticky only) and when cancellation exposes low digits. Test all midpoint cases against the oracle. [I]
3. **24-byte ABI details.** Hidden-pointer aliasing (callee assignment to a parameter), parameter/return copies, register allocation with three-word values, array stride 24, globals, 24-byte constants, `.sym` 48 hex digits, and `Result[decimal]` payload layout. The design flags these as high risk. [V design; open]
4. **No `?decimal`.** Zero is a valid decimal, so a sentinel word cannot represent nil; use `Result[decimal]` or a flag record. [V design]
5. **Canonicalization and dscale.** When to strip trailing zeros, how literal exponent normalization works, and how `dscale` interacts with numeric equality, hashing and database binding. The design fixes the broad rules but not every literal edge case. [V design; open]
6. **Division performance.** 26.9-33.4 ns is the largest arithmetic cost. The 256/64 specialization, reciprocal-based 256/128 and small-divisor paths should be implemented and measured before any claim about production throughput. [I]
7. **Rounding-mode coverage.** Half-away is the database default; half-even, truncate, floor, ceiling and `UNNECESSARY` still need explicit APIs and tests. Do not add a global context. [V design]
8. **Overflow/underflow boundary.** `int16` exponent limits, the `10^38` normalization rule and the trap-versus-Result split need exhaustive tests. [V design; I]
9. **Measurement environment.** The numbers are from one Zen 4 WSL2 host under load; CPUID is virtualization-inflated and some medians varied by 5-10% (occasionally more). Re-measure on bare metal and at least one older x86-64 CPU before publishing performance claims. [O]
10. **No AVX-512 assumption.** IFMA is batch-only and not portable; a future batch path needs a manual CPUID gate and a scalar fallback. `target_clones`/ifunc must not be used in this runtime. [V observations; I]
11. **Existing licenses.** GCC's libdecnumber is GPLv3 with the Runtime Library Exception and Java is GPLv2+CE; neither may be copied into the Apache-2.0 runtime. The Intel libbid sources carry a BSD-3-Clause-style notice, .NET and rust_decimal are MIT, apd is Apache-2.0 and libmpdec is BSD-2-Clause. Even where a license permits reuse, write independent C and use the libraries only as semantic references; keep the Python oracle as the test authority. [V licenses as stated by the projects; I rule]
12. **Database limits.** Oracle raw 39-40-digit values, PostgreSQL unconstrained `numeric`, NaN/Infinity and negative scale remain the later `numeric` tier; the fixed type is 38 significant digits and finite only. [V design]

## 9. Sources

### Project documents and code

- `/home/chris/obx/obo/design/decimal-research-2026-10-28.md` — approved 24-byte design, §A.5.7 arithmetic policy, §A.5.9 ABI, §A.6.4 scale/exponent semantics, §A.6.5 staging, §6.1 effort/risk.
- `/home/chris/obx/obo/design/numeric-literals-decision-2026-10-29.md` — decimal-by-default literal policy.
- `/home/chris/obx/obo/Makefile` — freestanding CFLAGS and `ld` link of the runtime.
- `/home/chris/obx/obo/runtime/runtime.c`, `/home/chris/obx/obo/runtime/entry.S` — runtime ABI, jump table and stack-alignment contract.
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

### Database formats

- SQL Server `decimal`/`numeric`: https://learn.microsoft.com/en-us/sql/t-sql/data-types/decimal-and-numeric-transact-sql
- [MS-TDS] decimal/numeric encoding: https://learn.microsoft.com/en-us/openspecs/windows_protocols/ms-tds/b46a581a-39de-4745-b076-ec4dbb7d13ec
- Oracle `NUMBER`/OCI data types: https://docs.oracle.com/en/database/oracle/oracle-database/18/lnoci/data-types.html
- PostgreSQL `numeric`: https://www.postgresql.org/docs/current/datatype-numeric.html
- PostgreSQL `numeric.c`: https://github.com/postgres/postgres/blob/3d6a828938a5fa0444275d3d2f67b64ec3199eb7/src/backend/utils/adt/numeric.c
- SAP ASE decimal types: https://help.sap.com/docs/SAP_ASE/2df920cd5b1644e5a065d55b637d5e92/aab84005bc2b1014a9e4de77b205c641.html?version=16.0.3.0&locale=en-US
- FreeTDS numeric conversion: https://github.com/FreeTDS/freetds/blob/dda3cd532574bfd0cfd349a946736bfc5d264738/src/tds/numeric.c

### Benchmark artifacts (outside the repository)

- `/tmp/obo-decimal-arith/bench_common.h` — timing harness, RNG, types.
- `/tmp/obo-decimal-arith/bench_mul.c` — scalar, MULX, ADX and IFMA multiply kernels; verified vectors.
- `/tmp/obo-decimal-arith/bench_div.c` — divq, Knuth D, decimal divide/round, GCC `_Decimal128` reference.
- `/tmp/obo-decimal-arith/bench_conv.c` — parse and format kernels.
- `/tmp/obo-decimal-arith/bench_align.c` — exponent alignment plus add.
- `/tmp/obo-decimal-arith/bench_ledger.c` — 64-bit, 128-bit and 38-digit ledger loops.
- `/tmp/obo-decimal-arith/bench_abi.c` — inline/helper/indirect 24-byte ABI costs and copies.
- `/tmp/obo-decimal-arith/bench_fast.c` — 64-bit versus 128-bit add/multiply.
- `/tmp/obo-decimal-arith/bench_dispatch.c` — CPUID and cached-flag costs.
- `/tmp/obo-decimal-arith/bench_dbconv.c` — base-100, base-10000, base-256 and TDS-copy costs.
- `/tmp/obo-decimal-arith/results/` — saved run logs and host context.

No benchmark source, binary, vector or log was added to the repository. [V]
