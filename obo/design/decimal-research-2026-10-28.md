# A lossless decimal type for obo: design research

**Date:** 2026-10-28  
**Scope:** design research only. No repository, branch, worktree, seed or configuration was changed. This is a future feature for after floats, not a proposal to implement it during the current stdlib/`eza` phase.  
**obo version examined:** branch `tools-modules`, HEAD `4e733a6` ("Merge branch 'tools-modules': build tools from source modules").  
**.NET reference:** .NET 10/11 documentation and `dotnet/runtime` `main` at commit `a4f8db31af247193ddb8bcdb6313b5ee8f626487` (2026-10-28). The runtime source is ahead of released .NET; version-specific behavior is noted where it matters.

**Evidence convention.** **[V]** means verified from the cited primary source (Microsoft docs, dotnet/runtime source, IEEE/standards material, a language's own docs/source, or obo's source at the commit above). **[I]** means inference, estimate or recommendation. Facts in sections 2 and 3 are [V] unless marked; the obo effort/risk estimates and all recommendations are [I].

---

## 1. Executive verdict

**A .NET-`System.Decimal`-style type is the best fit for obo among the designs examined, but it should be a compiler primitive, not a library type, and it should stay after floats.** [I]

The decisive facts:

- .NET `decimal` is a **16-byte value type** with a **96-bit integer coefficient**, a sign bit and a **scale of 0-28**. It represents `(-1)^sign * coefficient / 10^scale`, with a range of about **±7.9228e28** and **28-29 significant digits**. It has **no NaN or infinity**, and arithmetic uses **round-half-to-even ("banker's rounding")**. It is **not IEEE 754-2008 decimal128**: decimal128 has a 113-bit coefficient, 34 digits, a much larger exponent range, and NaN/infinity. [V]
- The design is proven in finance, is allocation-free for ordinary values, and matches obo's ownership model: decimal values would be unowned and copied by value, like `int`. [I]
- obo currently has **no value records**; every `record` is `ref record` and every field, parameter, global and array element is one 8-byte word. A library decimal built from two `int`s would therefore be a heap-allocated reference type, one allocation per arithmetic result, moved and freed under single ownership, with no operator overloading and no methods yet. That is a poor core numeric type. A true value decimal forces a compiler primitive. [V for layout/no value records; I for consequence]
- The main cost is not the decimal algorithm; it is **16-byte layout and ABI**: locals, parameters, returns, record fields, array elements, globals, constants and `Result[decimal]` payloads all assume one 8-byte word today, and the code generator's `Item` and runtime-call path assume 8-byte values and a single `RAX` return. This is a medium-large, high-risk language feature, larger than `Result` or I/O and comparable to the module work, with substantial arithmetic-correctness testing. [V for assumptions; I for effort]
- A 64-bit fixed-point integer (`cents`/`mills`) is simple and fast, but it is not a general finance decimal: ISO 4217 minor units vary from 0 to 4, it carries no scale, and obo's `int` arithmetic wraps silently on overflow. It is a reasonable **temporary library** for a narrow tool, not the language's decimal type. [V for ranges/minor units/wrap; I for recommendation]

**Recommended minimum viable feature, when scheduled after floats:** a built-in 16-byte `decimal` with a 96-bit coefficient, scale 0-28, sign, literals such as `1.23m`, `+ - * /` and unary `-`, comparisons, `int`/`string` conversions, `round`/`quantize` with explicit rounding modes, scale preservation, no NaN/Infinity/signed-zero semantics, arithmetic traps on overflow and division by zero, and checked `Result`-returning variants for recovery. No currency is baked into the type. Float interop waits until floats exist. [I]

---

## 2. .NET `System.Decimal` precisely

### 2.1 Internal representation, range, precision, ABI

**[V]** `System.Decimal` is a 128-bit value type implemented as four 32-bit words:

- a **96-bit unsigned integer coefficient** in `lo` (32 bits), `mid` (32 bits) and `hi` (32 bits);
- a **flags** word whose **bit 31** is the sign, **bits 16-23** are the scale (0-28), and bits 0-15 and 24-30 must be zero;
- value = `(-1)^sign * coefficient / 10^scale`.

The maximum coefficient is `2^96 - 1 = 79,228,162,514,264,337,593,543,950,335`, so `Decimal.MaxValue` is about **7.9228e28**, `Decimal.MinValue` is its negative, and the smallest positive non-zero value is **1e-28**. The precision is commonly stated as **28-29 significant digits**, not "28 digits before and after the point": the 96-bit coefficient caps *total* significant digits, so a value with scale 18 has only about 10-11 integer digits. [V: Microsoft C# docs table; dotnet/runtime `Decimal.cs` constants and field comments; `Decimal.DecCalc.cs` `DEC_SCALE_MAX = 28`]

Relevant source:

- `src/libraries/System.Private.CoreLib/src/System/Decimal.cs`, lines 86-127 (`SignMask`, `ScaleMask`, `ScaleShift`, `MaxValue`, `MinValue`, field comments) at the pinned commit.
- `Decimal.DecCalc.cs`, line 103 (`DEC_SCALE_MAX = 28`).

**Size and ABI.** C# documents `decimal` as **16 bytes**; it is a value type. In managed code it is inline in fields and arrays and copied on assignment/passing; boxing allocates. The struct is `[StructLayout(LayoutKind.Sequential)]` and the field order is deliberately the Win32 `DECIMAL` layout. The managed JIT's exact register/stack classification is an implementation detail; the language-visible facts are 16-byte size and value semantics. [V: C# floating-point types table; `Decimal.cs` attributes/fields; I: JIT classification]

| Property | .NET `decimal` |
|---|---|
| Size | 16 bytes |
| Coefficient | 96-bit unsigned integer |
| Scale | 0-28 (power of 10 divisor) |
| Sign | 1 bit |
| Value form | `(-1)^s * c / 10^e`, `0 <= e <= 28` |
| Max coefficient | `2^96 - 1` = 79,228,162,514,264,337,593,543,950,335 |
| Approx. range | ±7.9228e28 |
| Smallest non-zero | 1e-28 |
| Precision | 28-29 significant digits |
| NaN / Infinity | none |
| Signed zero | bit pattern can encode it; treated as equal to positive zero in operations |

### 2.2 Arithmetic, scale handling, rounding, overflow and division by zero

**[V]** The C# specification states the governing rule: the result of a decimal operation is the exact result, preserving the scale defined for that operator, then **rounded to the nearest representable value; ties go to the value with an even least-significant digit ("banker's rounding")**. If the magnitude is too large, an overflow exception is thrown; rounding may also turn a non-zero exact result into zero. [V: C# specification §8.3.8 "The decimal type"; `Decimal.DecCalc.cs` `ScaleResult`/`InternalRound`]

Scale behavior in the implementation:

| Operation | Scale rule | Rounding / failure |
|---|---|---|
| `a + b`, `a - b` | Align to the larger scale; result keeps that scale unless a carry beyond 96 bits forces a reduction | Exact 96-bit add/sub; if more than 96 bits, divide by 10, reduce scale and round half-even; overflow if scale would go below 0 |
| `a * b` | Scale is `scale(a) + scale(b)` | 96x96 -> 192-bit product; if >96 bits or scale >28, divide by 10 and round half-even; overflow if too large; tiny results may round to zero |
| `a / b` | Preferred scale is `scale(a) - scale(b)`; an exact quotient may need a larger scale; a remainder extends the scale up to 28 | Long division at 96/128 bits, round half-even when scale 28 or 96 bits is reached; `DivideByZeroException` if `b == 0`; overflow if quotient too large |
| `a % b` | Remainder of truncating division; sign follows the dividend | `DivideByZeroException` if `b == 0` |
| `decimal.Round(d, n, mode)` | Result has `n` decimal places | Default `n`-digit overload uses `MidpointRounding.ToEven`; explicit modes include AwayFromZero, ToZero, ToNegativeInfinity, ToPositiveInfinity |

Representative consequences (the scale rules follow directly from the source; exact strings are [I] examples, not separately executed here because no .NET runtime is installed in this environment):

- `1.0m + 1.00m` -> `2.00` (larger scale 2).
- `1.20m - 1.20m` -> `0.00` (scale 2).
- `1.20m * 1.2m` -> `1.440` (scale 2+1).
- `1.00m / 2m` -> `0.50` (preferred scale 2, exact).
- `1m / 2m` -> `0.5` (exact division extends scale to 1).
- `1m / 3m` -> a 28-decimal-place value rounded half-even (`0.333...3` with 28 digits).
- `1.0m == 1.00m` is **true**; `1.0m.ToString()` is `"1.0"` and `1.00m.ToString()` is `"1.00"`; `GetBits` differs. Equality and comparison ignore scale; the stored scale is presentation/quantum metadata.

**Overflow/underflow.** Too large a result throws `OverflowException`; too small a result is rounded to zero (no separate underflow exception). There is no saturation or wrapping. [V: C# spec §8.3.8; `Decimal.DecCalc.cs` `Number.ThrowDecimalOverflowException` call sites]

**Division by zero.** `decimal.Divide`, `%`, and the `/` operator throw `DivideByZeroException`. [V: `Decimal.DecCalc.cs` `VarDecDiv` and `VarDecMod`; `Decimal.cs` `Divide`]

**Special values.** `System.Decimal` has **no NaN, no positive/negative infinity, and no signaling NaN**. The docs are explicit: "The `decimal` type is not an IEEE 754 floating-point format (such as `decimal128`); it does not support NaN or infinity values, and represents numbers using a 96-bit integer mantissa scaled by a power of 10." [V: Microsoft C# floating-point types page]

**Signed zero.** The encoding has a sign bit, and `GetBits` notes that the bit representation distinguishes negative and positive zero, but "These values are treated as being equal in all operations." There is no distinct arithmetic negative-zero value, no reciprocal-infinity behavior, and no NaN propagation. [V: `Decimal.GetBits` remarks; `Decimal.DecCalc.Equals` zero special case]

### 2.3 Equality, comparison, hashing, formatting and parsing with different scales

**[V]** Equality and ordering are **numeric, not representation-based**:

- `Decimal.Equals` and `==` compare the numeric values. `1.0m == 1.00m` is true, even though the bits and `Scale` differ.
- `CompareTo` uses numeric ordering and also ignores scale.
- `GetHashCode` normalizes trailing zeros (it "unscales" the coefficient when it is even), so equal values with different scales have the same hash. This is required for correct dictionary/set behavior.
- `GetBits` and `Scale` expose the stored scale; `1.0m` has scale 1 and `1.00m` has scale 2. The four words differ.
- The scaling factor **preserves trailing zeros**. The `Decimal` remarks say: "The scaling factor also preserves any trailing zeros in a `Decimal` number. Trailing zeros do not affect the value of a `Decimal` number in arithmetic or comparison operations. However, trailing zeros might be revealed by the `ToString` method if an appropriate format string is applied." Default general formatting uses the stored scale, so `1.00m` prints `"1.00"`.
- Parsing preserves the written scale: `decimal.Parse("1.00")` yields coefficient 100, scale 2; `TryNumberToDecimal` sets the scale from the parsed exponent, and values with more than 28 fractional digits are rounded/clamped at scale 28.

Relevant source/docs:

- `Decimal.cs` lines 431-457 (`CompareTo`, `Equals`, `GetHashCode`).
- `Decimal.DecCalc.cs` lines 1323-1425 (`Equals`, `VarDecCmp`, `VarDecCmpSub`) and 1954-1976 (`GetHashCode`).
- `Number.Parsing.cs` lines 786-935 (`ParseDecimal`, `TryParseDecimal`, `TryNumberToDecimal`).
- `Number.Formatting.cs` lines 480-587 (`FormatDecimal`, `DecimalToNumber`; `number.Scale = i - scale`).
- Microsoft `Decimal` remarks and `Decimal.GetBits` remarks.

**Culture.** `ToString()` and `Parse()` are culture-sensitive by default; `1,234.56` versus `1.234,56` depends on `NumberFormatInfo`. For audit/JSON use the invariant culture (`.` decimal separator, no grouping unless explicitly requested). [V: `Decimal.ToString` docs; `Decimal.Parse` docs]

### 2.4 Literals and conversions

**Literals.** C# real literals use a suffix: no suffix or `d`/`D` is `double`, `f`/`F` is `float`, and **`m`/`M` is `decimal`**. Scientific notation is allowed (`1.5E6m`). The specification says a decimal literal is converted by taking its exact value and, if necessary, rounding half-even; **"Any scale apparent in the literal is preserved unless the value is rounded."** Example: `2.900m` becomes sign 0, coefficient 2900, scale 3. [V: C# specification §6.4.5.4 real literals]

**Conversions:**

- From `int`, `uint`, `long`, `ulong`, `byte`, etc. to `decimal`: implicit and exact. [V]
- `decimal` to `int`/`long`: explicit; **truncates toward zero**; overflow throws. [V: `Decimal.ToInt32`/`ToInt64` source and docs]
- `double`/`float` to `decimal`: explicit; NaN/Infinity/out-of-range throws `OverflowException`. Current .NET 11 docs state the constructor "rounds the exact value of `value` to the nearest representable `Decimal`"; the pinned source contains a correctly rounded exact conversion (`VarDecFromR8`, including a round-to-nearest-even path and a comment that the exact value is rounded once). Older runtime versions may differ; pin the target runtime if exact double-to-decimal behavior matters. [V: `Decimal(double)` docs; `Decimal.DecCalc.cs` lines 1601-1693; I: version caveat]
- `decimal` to `double`/`float`: explicit; may lose precision but does not throw for finite values in the normal path; current implementation correctly rounds to binary64/binary32. [V: `Decimal.cs` `ToDouble`/`ToSingle` and `DecCalc.VarR8FromDec` comments]
- `decimal` and `double`/`float` cannot be mixed implicitly in C# expressions; a cast is required. [V: C# floating-point types docs]
- `string` to `decimal`: `Parse`/`TryParse` with `NumberStyles` and culture; malformed input throws `FormatException` or returns false. `decimal` to `string`: `ToString` with format/culture; default `G` preserves scale. [V]

### 2.5 Performance characteristics

**[V]** `System.Decimal` arithmetic is **software arithmetic**. x86-64 has no decimal floating-point instruction set; Intel's decimal library is a software implementation of IEEE decimal32/64/128 for x86, and .NET implements its own 96-bit `DecCalc` in managed code using 32-bit limbs, 192-bit intermediate buffers and 128-bit division loops. .NET's JIT can inline and optimize it, but it cannot turn it into a hardware decimal operation. [V: `Decimal.DecCalc.cs` structure; Intel Decimal Floating-Point Math Library; I: no x86 hardware decimal]

Published microbenchmarks are old and hardware-specific, but they consistently show an order-of-magnitude gap:

- A classic Stack Overflow benchmark (100 million additions) reported long 231 ms, double 286 ms, decimal 2010 ms.
- A per-operation benchmark reported addition ~11x, subtraction ~18x, multiplication ~15x, division ~32x slower than double/long.
- A 2018 dotnet/runtime issue on 32-bit x86 reported native decimal ADD 28.43 ns, MUL 19.23 ns (with managed variants slower at that time).
- Microsoft's own guidance says: "You can use `double` instead of `decimal` when optimizing performance is more important than ensuring accuracy. However, any difference in performance goes unnoticed by all but the most calculation-intensive applications." [V: Microsoft C# floating-point types page]

Treat these as order-of-magnitude: a modern x64 machine and a current .NET JIT will differ. The structural facts are stable: 16 bytes per value, no allocation for ordinary locals/fields/arrays, software multiply/divide, and division much more expensive than add/sub. [V/I]

### 2.6 Is it IEEE 754-2008 decimal128? No

**No. `System.Decimal` is not IEEE 754-2008 decimal128, decimal64, or decimal32, and its bits are not interchangeable with them.** Microsoft says so directly. [V: Microsoft C# floating-point types page]

| Property | .NET `System.Decimal` | IEEE 754-2008 decimal128 |
|---|---|---|
| Size | 16 bytes | 16 bytes |
| Coefficient | 96-bit binary integer | 113-bit binary integer (BID), or 34 decimal digits in DPD |
| Precision | 28-29 significant digits | 34 significant digits |
| Scale/exponent | scale 0-28, value `c/10^e` | exponent range: `emax=6144`, `emin=-6143`, `Etiny=-6176`; quantum exponents from about -6176 to +6111 |
| Approx. finite range | ±7.9228e28; min non-zero 1e-28 | roughly ±9.999e6144; min non-zero 1e-6176 |
| Special values | none | ±Infinity, quiet NaN, signaling NaN |
| Encoding | 96-bit coefficient + flags word (Win32 `DECIMAL` layout) | combination field + exponent continuation + coefficient continuation; BID or DPD |
| Cohorts/quantum | scale is retained (1.0 vs 1.00), but no full IEEE cohort/quantum arithmetic | full cohort/quantum model; preferred exponents |
| Default rounding | half-even | roundTiesToEven is the recommended default; decimal also supports ties-to-away |
| Hardware | software on x86-64 | software on x86-64; hardware on some POWER/z systems |

The practical differences for obo are: .NET-style is simpler (no NaN/Inf/sNaN, no BID/DPD, no huge exponent range, no special-value rules), has fewer digits (28-29 vs 34), and is easier to implement in a 16-byte value. If interoperability with IEEE decimal128 or a database that stores decimal128 is a goal, that is a different type/encoding decision, not a minor variation. [I]

---

## 3. Alternatives

### 3.1 Comparison table

| Design | Layout / size | Precision and range | Rounding control | Special values | Allocation / performance | Money fit |
|---|---|---|---|---|---|---|
| **.NET `System.Decimal`** | 16 B value; 96-bit coeff + sign + scale 0-28 | 28-29 digits; ±7.9228e28; 1e-28 min | Half-even; `Decimal.Round` modes | None | Value type, no per-value heap; software; ~10-30x slower than double/long | Excellent for fiat and general business amounts |
| **IEEE decimal128** | 16 B; 113-bit coeff; 14-bit exponent; BID/DPD | 34 digits; ~1e-6176 to ~1e6144 | Half-even default; all IEEE modes | ±Inf, qNaN, sNaN, signed zero | Software on x86-64; complex | Excellent when 34 digits/range or IEEE interop needed; more complex |
| **IEEE decimal64** | 8 B; 50-bit continuation + 10-bit exponent | 16 digits; ~1e-398 to ~1e384 | Same | ±Inf, qNaN, sNaN | Software; compact | Good for compact storage but only 16 digits |
| **Java `BigDecimal`** | Heap object: `BigInteger` unscaled + 32-bit scale | Arbitrary precision; scale int32 (can be negative) | 8 `RoundingMode`s; divide without rounding throws on non-terminating | None | Heap; immutable; every operation allocates | Excellent precision; heavy; pitfalls (`equals` vs `compareTo`, divide, double ctor) |
| **Python `decimal`** | Heap object: sign, coefficient digits, exponent; `libmpdec` C core | Context precision default 28; arbitrary; Emin/Emax ±999999 default | Context rounding (default half-even), traps/flags | NaN, sNaN, ±Inf, ±0 | Heap; C library; arbitrary precision | Excellent semantics for accounting; dynamic context/traps complexity |
| **Rust `rust_decimal`** | 16 B; 96-bit coeff + scale 0-28 + sign; `Copy` | 28-29 digits; ±7.9228e28 | `RoundingStrategy` (7 modes); `round_dp` nearest-even by default | None | No heap; software; fast for a decimal | Excellent; closest public analogue to .NET |
| **Rust `bigdecimal`** | Heap `BigInt` + i64 scale | Arbitrary; default precision 100 for infinite operations; multiplication keeps all digits | `Context`, `RoundingMode` (HalfEven default) | Finite only; no NaN/Inf documented | Heap; arbitrary precision | Good when >28 digits; heavy |
| **Go `shopspring/decimal`** | Heap `*big.Int` + int32 exponent | Arbitrary; max 2^31 fractional digits; decode default exponents up to 10,000 | `DivRound` precision; ties away from zero; `DivisionPrecision=16` default | No NaN/Inf representation | Heap; immutable; docs say "less performant" due to allocations | Fair; arbitrary but easy to misuse; division precision default is low |
| **Go `cockroachdb/apd`** | Heap coefficient + int32 exponent; `Context` | Arbitrary; context `MaxExponent=100000`, `MinExponent=-100000` | Context precision/rounding/traps; `RoundHalfUp` default | `Finite`, `Infinite`, `NaN`, `NaNSignaling` | Heap; optimized big.Int wrapper; good but context-heavy | Excellent for database-style decimal; more machinery |
| **SQL Server `decimal(38)`** | 5, 9, 13 or 17 B by precision; fixed precision/scale | Up to 38 digits; fixed p/s | Rounds on conversion; DB engine policy | None | Database storage/compute | Good fixed-schema money; not a language value type |
| **PostgreSQL `numeric`** | Variable-length heap; groups of four decimal digits | Up to 131072 digits before / 16383 after decimal | Ties away from zero in numeric; DB engine | `NaN`, `Infinity`, `-Infinity` | Heap; docs: "very slow compared to integer or floating-point" | Excellent precision; database, not an in-process value |
| **int64 fixed point** | 8 B integer; scale implicit in code | 18-19 digits total; cents: ±9.22e16 units; mills: ±9.22e15; micros: ±9.22e12; nanos: ±9.22e9 | Manual; caller chooses | None | Fastest; no allocation | Good for one currency/scale; poor general finance |
| **int128 fixed point** | 16 B integer; scale implicit | 38-39 digits total; at scale 2 ±1.7e36; at scale 18 ±1.7e20 | Manual | None | Fast; needs compiler/runtime support if no native type | Good if range fits; still fixed scale |
| **Rational** | Heap numerator + denominator (arbitrary integers) | Exact; unbounded size; denominator can grow | No rounding until explicit conversion | None | Heap; denominator growth; expensive | Poor for money/reporting; useful for exact rates internally |

### 3.2 Notes on each alternative

**IEEE 754-2008 decimal128 / decimal64 / decimal32 [V].** decimal128 has `p=34`, `emax=6144`, `emin=-6143`, `Etiny=-6176`; decimal64 has `p=16`, `emax=384`, `emin=-383`, `Etiny=-398`; decimal32 has `p=7`, `emax=96`, `emin=-95`, `Etiny=-101`. All have ±Infinity and quiet/signaling NaN, and both BID (binary integer decimal) and DPD (densely packed decimal) encodings exist. The standard's recommended default rounding is roundTiesToEven; decimal implementations also support ties-to-away. On x86-64 these formats are software (Intel's decimal library; GCC's `_Decimal128` support is software based on it); POWER and z have hardware decimal. The "34-digit precision, not range, is the notable characteristic" of decimal128. Compared with .NET, decimal128 is more precise but much more complex; it is the right choice only if IEEE/decimal128 interoperability is a requirement. [V: Wikipedia decimal128/decimal64/decimal floating point/IEEE 754; Intel library; I: suitability]

**Java `BigDecimal` [V].** "Immutable, arbitrary-precision signed decimal numbers. A `BigDecimal` consists of an arbitrary precision integer *unscaled value* and a 32-bit integer *scale*." Scale can be negative. Add/sub/mul are exact; `divide` without a scale/rounding throws `ArithmeticException` if the exact quotient has a non-terminating decimal expansion (for example `1/3`), and division by zero also throws. There are eight rounding modes: `UP`, `DOWN`, `CEILING`, `FLOOR`, `HALF_UP`, `HALF_DOWN`, `HALF_EVEN`, `UNNECESSARY`. It has no NaN/Infinity and no signed zero. Its classic pitfalls are: `new BigDecimal(0.1)` captures the binary double artifact instead of 1/10; `equals` is scale-sensitive while `compareTo` is numeric (`1.0` and `1.00` are `compareTo == 0` but `equals == false`), and natural ordering is inconsistent with equals; `toString` may use scientific notation, so `toPlainString` is used for display. It is heap-heavy and every operation tends to allocate. For a fixed-width value language it is the wrong memory model; for arbitrary precision it is excellent.

**Python `decimal` [V].** The module implements the General Decimal Arithmetic specification (libmpdec in C). A `Decimal` is immutable and has a sign, coefficient digits and an exponent; trailing zeros are preserved. The default context is `prec=28`, `rounding=ROUND_HALF_EVEN`, `Emin=-999999`, `Emax=999999`, with `Overflow`, `DivisionByZero` and `InvalidOperation` trapped by default. It supports `NaN`, `sNaN`, `Infinity`, `-Infinity`, `+0` and `-0`. Construction is not limited by context precision; arithmetic results are. `Decimal(0.1)` is the exact binary value `0.1000000000000000055511151231257827...`, not `Decimal("0.1")`. Contexts can be local/thread-local, and `quantize` is the money-friendly rounding operation. It is the best semantic reference for exact decimal behavior, but it is a heap object with dynamic context state, not a fixed value type.

**Rust `rust_decimal` [V].** This is the closest public analogue to .NET: 96-bit integer coefficient, scale 0-28, sign bit, 16-byte `Copy` value. The README states: "The binary representation consists of a 96 bit integer number, a scaling factor used to specify the decimal fraction and a 1 bit sign. Because of this representation, trailing zeros are preserved..." It has no NaN/Infinity; operators panic on overflow and division by zero, while `checked_*` methods return `Option`. `RoundingStrategy` has MidpointNearestEven, MidpointAwayFromZero, MidpointTowardZero, ToZero, AwayFromZero, ToNegativeInfinity and ToPositiveInfinity; `round_dp` defaults to nearest-even. Equality/ordering are numeric and `Hash` normalizes, so `1.0 == 1.00`; `normalize()` strips trailing zeros. It is an existence proof that a 16-byte, allocation-free .NET-style decimal is practical in a systems language.

**Rust `bigdecimal` [V].** `BigDecimal` uses a `BigInt` and a 64-bit scale; it is arbitrary precision. The default context precision is 100 for operations that can produce infinite digits (inverse, sqrt); multiplication preserves all digits, so 70-digit operands produce a 140-digit result unless the user trims. The default rounding is HalfEven. It allocates and is not a fixed ABI value. It is appropriate when 28-34 digits are not enough.

**Go `shopspring/decimal` [V].** "Arbitrary-precision fixed-point decimal numbers in go"; internal `*big.Int` coefficient and `int32` exponent. `DivisionPrecision` defaults to 16 decimal places; `DivRound` rounds ties away from zero; division by zero panics. Trailing zeros are preserved and `Equal`/`Cmp` are numeric, while `Normalize` canonically strips zeros. The docs explicitly warn about "extra allocations, so Decimal is less performant". It is convenient but easy to get a low-precision division by default.

**Go `cockroachdb/apd` [V].** Implements much of the General Decimal Arithmetic specification (the same specification as Python's decimal and GCC's decimal extension). Values are coefficient × 10^exponent; arithmetic is through a `Context` with precision, rounding, traps and exponent limits (`MaxExponent=100000`, `MinExponent=-100000`); `NaN`, `NaNSignaling` and `Infinite` forms exist; the default rounder is `RoundHalfUp`; `Quantize` and `Reduce` control scale/trailing zeros. It is powerful and database-grade, but the context machinery and special values are more than a finance language's core value type needs.

**SQL Server `decimal(38)` and PostgreSQL `numeric` [V].** SQL Server fixes precision and scale at declaration, supports up to 38 digits, stores 5/9/13/17 bytes depending on precision, and rounds on conversion to a lower scale. PostgreSQL `numeric` is arbitrary precision (up to 131072 digits before and 16383 after the decimal point), exact where possible, variable-length, supports NaN/Infinity in unconstrained columns, and is documented as "very slow compared to the integer types, or to the floating-point types." They are useful references for schema-level fixed vs arbitrary precision, not for an in-process 16-byte value.

**Fixed-point `int64`/`int128` [V for ranges; I for fit].** A scaled integer is exact for the chosen scale and extremely fast. `int64` covers 18-19 total digits: at scale 2 (cents) the maximum magnitude is about 9.22e16 units; at scale 3 (mills) about 9.22e15; at scale 6 (micros) about 9.22e12; at scale 9 (nanos) about 9.22e9. `int128` covers 38-39 digits, but obo has no `int128` today. The scale is not carried in the value, rounding is manual, and overflow must be checked: obo's `int` `+`, `-` and `*` compile to plain x86 instructions with no overflow check, so a fixed-point library would wrap silently unless every operation is checked. ISO 4217 minor units are 0, 2, 3 or 4 depending on currency, so a single "cents" scale is wrong for JPY (0), KWD (3), CLF/UYW (4), etc. Fixed point is excellent for a known domain (one currency, one scale, tight loops) and inadequate as the language's general decimal.

**Rational arithmetic [V/I].** Exact ratio arithmetic (for example Python `fractions.Fraction`) stores normalized numerator/denominator integers. It is exact, but denominators grow with every distinct operation, comparison/equality is exact but expensive, and a rational has a finite decimal expansion only if the denominator has no prime factors other than 2 and 5; `1/3` has no finite decimal representation. Formatting is necessarily rounded. It is useful for exact internal rates or allocations, not for a money type or audit reports.

---

## 4. Obo fit

### 4.1 Current constraints that matter

**[V]** At `4e733a6`, obo has:

- `int` (64-bit), `byte`, `bool`, `string`, `void`, `nil`, `record`, `closed`, `array`, `opt`, `result`, `resultctor`, `errval` as the type kinds. `Type.kind` is a string; primitives are singletons (`tInt`, `tByte`, `tBool`, `tString`, `tVoid`, `tNil`); `same` compares primitives by kind and derived types recursively. `/home/chris/obx/obo/src/obo.obo` lines 882-975.
- **No value records.** Records are declared `type X = ref record { ... }` and are heap references. Fields are one 8-byte word each; `fieldList` assigns `offset = 8 * len(rec.fields)` (line 9448). A library decimal made of two `int`s would therefore be a heap object, not an inline value.
- **One-word layout everywhere.** Record fields 8 bytes; function parameters at `16 + 8*(n-1-i)` (line 8069); globals `GLOBAL_BASE + gsize`, `gsize += 8` (lines 6299-6300); arrays element size 8 except `[]byte` at 1 (`runtime/runtime.c` `rt_alloc_array`, line 356); `Result` ok payload at offset 0 with descriptor size 8 (`okVariantOf`, lines 1995-2002).
- **8-byte codegen items.** `Item` has `val`, `r`, `base/index/disp` and `width/scale` all assuming one word (`src/obo.obo` lines 1153-1175); `makeConst` stores an `int`; `binop` requires `isInt` and emits x86 add/sub/imul/idiv directly (lines 5980-6075). `int` overflow is unchecked; constant `fold` also wraps.
- **Runtime calls** use the C ABI, at most four arguments, return in `RAX` (`rtCall`, lines 2334-2392). A 16-byte return or a fifth argument needs a new convention.
- **Constants** in source are primitive: `constDecl` requires a compile-time `const` item and stores `s.value = x.val` (`src/obo.obo` 8233-8243). In `.sym`, constants are only `int`/`byte` (16 hex digits), `bool`, or `string` (`hex "..."`) (`appConst` 9020-9045; `symConstDecl` 9748-9785).
- **`.sym` type expressions** print a built-in by `t.name` (`appTypeExpr`, 8924-8952) and read built-ins by name (`symParseType`, 9384-9431). A built-in `decimal` is easy to add syntactically, but the ABI version (`SYM_ABI = 1`) must change if layout/calling convention changes.
- **Optionals are reference-only**: `parseType` rejects `?T` unless `nonNull(T)` (record/closed/array/handle/result); `?decimal` would be rejected as "only reference types can be optional" unless that rule changes.
- **No methods, interfaces or lambdas yet** (roadmap phases 7-9 in `README.md`). A library decimal cannot have `a + b` or `a.round(2)` syntax; it would be `dec::add(a, b)` and allocation-returning functions.
- **The compiler is self-hosting and single-pass, no AST/IR.** Adding a type means changing the scanner, the combined parser/typechecker, the direct x86-64 emitter, the `.sym` reader/writer and the runtime; there is no IR in which to lower a 16-byte value. `make promote` requires stage 2 == stage 3 (`Makefile`).

### 4.2 Design options

| Option | What it is | Pros | Cons | Verdict |
|---|---|---|---|---|
| **A. Built-in 128-bit value type (`.NET`-style)** | New primitive `decimal`, 16-byte value, 96-bit coefficient + scale/sign; runtime C helpers; operators | True value semantics; no heap; matches finance; works in fields/arrays/params/returns/constants; operators possible | Pervasive layout/ABI/codegen change; software arithmetic; high test/seed risk | **Recommended after floats** |
| **B. Library type from two 64-bit words / record** | `ref record { lo: int; hi: int; scale: int }` or `{ coeff: int; scale: int }`; functions | No compiler/runtime/ABI/seed change; can start now; semantics prototype | Heap allocation per value/result; single-ownership moves; no operators; no `.sym` constants; not a value type; 64-bit coefficient only unless manual 128-bit | Prototype only; not final |
| **B'. Library fixed-point over `int64`** | One `int`, implicit scale (cents/mills/micros), checked functions | Simplest; fastest; no compiler change; exact for chosen scale | 18-19 digits; fixed scale; no scale metadata; overflow wraps unless checked; not general finance | Interim for a narrow tool |
| **C. Built-in 64-bit fixed point** | New 8-byte primitive with an implicit/encoded scale | Simpler ABI than 16 bytes; fast | Range/precision too small; currency scales vary; still a compiler primitive | Not recommended as the general decimal; maybe a separate `money` later |
| **D. Arbitrary precision** | Library `[]int` limbs + scale; big integer algorithms | Unlimited precision | Allocation/ownership per op; no value semantics; large implementation; no operators; `.sym`/const complexity | Much later, if ever |
| **E. IEEE decimal128 built-in** | 16-byte BID/DPD, 34 digits, NaN/Inf | 34 digits; IEEE/DB interop | 113-bit coefficient, special values, cohorts/quantum, more complex arithmetic; still software | Only if decimal128 interop is a requirement |

**Why a compiler primitive rather than a library type.** The language has no value records. A record is a heap-allocated reference. A library decimal would make every `amount` a pointer, every arithmetic result a fresh object, every assignment a move, and every temporary subject to the ownership checker's rules. It would also be impossible to use as a `const` (records are not constants) or in a `.sym` constant. A built-in decimal makes the value unowned and copyable, exactly like `int`; `isOwned(decimal)` can return false, so the ownership analysis does not need to track it. This is the single strongest reason to make decimal a primitive. [V for no value records/records-ref; I for design conclusion]

**Why not 64-bit fixed point as the language type.** It is tempting because obo already has 64-bit `int` and fast x86 arithmetic. But it has no scale identity, only 18-19 total digits, and ISO currency scales vary. A fixed-point library is a good interim for a specific finance tool; it is not a general `decimal` that the language can offer. [V for ranges/minor units; I for conclusion]

### 4.3 How a built-in decimal would fit (Option A sketch)

**Representation.** A 16-byte value, for example two 64-bit words:

- word 0: low 64 bits of the 96-bit coefficient;
- word 1: high 32 bits of the coefficient (bits 0-31) + scale (bits 32-39) + sign (bit 63); the remaining bits must be zero, or the layout can mirror .NET's four 32-bit words exactly (flags, hi, lo64) for easier reference.

A 96-bit coefficient + 8-bit scale + sign fits in 16 bytes with room to spare. The exact bit layout is a design choice; matching .NET's `GetBits` order makes cross-checking and serialization easier. [I]

**Type identity and `.sym`.** Add a singleton `tDecimal` with `kind = "decimal"`, `name = "decimal"`, `owner = ""`, `uid = 0`. `same` returns true only for that singleton; `assignable` allows only `decimal` to `decimal`; `isOwned` false; `isRef` false; `nonNull` false (no `?decimal` initially). `appTypeExpr` prints `decimal`; `symParseType` recognizes `decimal` alongside `int`/`byte`/`bool`/`string`. Because it is a built-in primitive, its identity is stable across modules without a `uid` or `.sym` type definition. The `.sym` ABI version must be bumped when the layout changes; `obosym` format may also need a bump if decimal constants add a grammar form. [V for mechanism; I for the new kind]

**Layout and ABI.** This is the largest change. Every place that assumes 8 bytes needs a size/alignment function:

- **Locals/parameters/returns.** Locals currently get 8-byte stack slots; parameters are at `16 + 8*(n-1-i)`; returns come back in `RAX`. A 16-byte decimal needs two slots (or a 16-byte aligned slot), a 16-byte parameter area, and a return convention: `RAX:RDX`, or a hidden pointer to a caller-provided destination. Passing by value on the stack and returning in two registers preserves value semantics; a hidden pointer is also viable but must copy on entry if the callee can assign to the parameter. [V for current ABI; I for options]
- **Record fields.** `fieldList` must compute offsets from a size/alignment function, not `8 * index`. Fields after a decimal shift; descriptors list pointer/string offsets only, and a decimal has neither, so descriptor contents do not need new marking, but record size and field offsets do. [V for field code; I for impact]
- **Arrays.** `rt_alloc_array` currently sets `esize = 1` for `[]byte` and 8 otherwise. A new array kind (for example kind 7) with `esize = 16` is needed; `rt_append` must copy 16 bytes, `rt_truncate` must zero 16-byte elements, element load/store must read two words, and `for x in xs` must copy a 16-byte element. The array block remains pointer-free, so marking/freeing is unchanged. [V for runtime; I for changes]
- **Globals.** `gsize` is advanced by 8; a decimal global needs 16 and zero-initialization is valid (all-zero bits = coefficient 0, scale 0, positive). [V/I]
- **Constants.** A decimal constant needs 16 bytes in rodata and a way for `Sym.value` (an `int`) to refer to them. Options: store an offset/index, add a field to `Sym`, or materialize the constant as a rodata address. Source `const` can use a decimal literal; `.sym` can encode 32 hex digits (the raw 16 bytes) under `const X: decimal = hex "..."`, which reuses the existing hex-string style and preserves scale exactly. No decimal arithmetic folding is required initially; literals and constants can be materialized from rodata. [V for const/Sym; I for design]

  A subtle bootstrap issue: the compiler is itself an obo program with only 64-bit `int`s, and it cannot call the compiled program's runtime C helpers at compile time. Parsing a 28-digit decimal literal into a 96-bit coefficient therefore needs either (a) a small 96-bit decimal parser in `src/obo.obo` using two or three 32/64-bit limbs, (b) a restriction that literals fit a 64-bit coefficient (about 18-19 digits) until the compiler-side parser is written, or (c) storing the literal text in rodata and parsing it at program startup, which makes it a runtime operation that can fail and is unsuitable for `const`/`.sym`. Option (a) is the clean long-term route and is a contained but non-trivial part of the effort; option (b) is a reasonable first vertical slice. Constant folding of decimal arithmetic can be skipped entirely: a decimal `const` can be a rodata value, and the `.sym` form can carry its raw bits. [I]
- **`Result[decimal]`.** The ok variant's descriptor size is currently 8 and `storeCellField` stores one word. A 16-byte payload needs descriptor size 16 and two stores; `try`/`match` must copy 16 bytes. The err variant stays 16 bytes. This makes `Result[decimal]` possible for `parse`/checked arithmetic, at the cost of one heap cell per success. [V for Result layout; I for changes]
- **Optionals.** `?decimal` is not representable as a zero word (0 is a valid decimal). Do not support it initially; if an optional decimal is needed, use `Result[decimal]` or a record with a flag. [I]

**Codegen.** The current `Item` is an 8-byte value in a register, constant, memory location, element or condition. A decimal item needs either:

1. **two registers / two words** in `Item` (lo and hi), with add/sub inline (`add`/`adc`) and multiply/divide/round via helpers; or
2. **a stack-slot address** in `Item`, with all operations calling runtime helpers that take destination/source pointers. This is simpler to get correct, slower, and matches the "wide value lives in a stack temp" model.

A pragmatic first implementation is (2): materialize decimal expressions into 16-byte stack temporaries, call C helpers for add/sub/mul/div/compare/round, and load/store fields/arrays with two-word moves. Later, inline add/sub and comparisons can be optimized. This avoids writing a 96x96 -> 192-bit multiply and a 192/96 divide in the raw x86 encoder. [I]

**Runtime helpers.** Add C functions in `runtime/runtime.c` for `add`, `sub`, `mul`, `div`, `rem`, `compare`, `round/quantize`, `parse`, `format`, `int->decimal`, `decimal->int`, and scale normalization. GCC on x86-64 supports `unsigned __int128` even in the freestanding build, so 64x64 -> 128 products and 128/64 divisions can use it; 96x96 -> 192 and 192/96 still need 32- or 64-bit limb code (the .NET `DecCalc` `Buf24`/`Buf12` algorithms are a good reference). Formatting must build strings without libc; parsing must be hand-written. Helpers can be called with pointers (`rt_dec_add(dst, a, b)`) to avoid the 4-argument/`RAX` limit, or the compiler's `rtCall` can be extended to more arguments and a two-register return. [V for runtime/GCC; I for helper design]

**Rounding.** Operators use half-even (matching .NET and the C# spec). A separate built-in or library function `round(x, scale, mode)` exposes modes: half-even (default), half-up/away, half-down/toward-zero, down/truncate, up/away, floor, ceiling, and `UNNECESSARY` (error if any non-zero digit is discarded). There is no global context or thread-local state; the mode is an explicit argument. This avoids Python/Java context complexity and is single-pass friendly. [I]

**Errors.** obo has no exceptions. There are two realistic models:

1. **Operators trap** on overflow and division by zero (like an `int` division-by-zero SIGFPE, but with a clear runtime message), and explicit checked functions (`addChecked`, `divChecked`, `parse`) return `Result[decimal]` with error codes. This keeps `a + b` ergonomic and allocation-free.
2. **All operators return `Result[decimal]`**, which is safe but allocates a cell for every operation and makes arithmetic unreadable.

Recommendation: model 1. Error codes: overflow, division by zero, invalid literal, inexact/rounding required, scale out of range. `parse` always returns `Result[decimal]`; `format` returns `string` (or `Result[string]` only if formatting can fail). [I]

**Scale preservation.** Preserve the scale by default, as .NET does: `1.20m` stays `1.20`, `1.20 + 1.20` is `2.40`, and `1.00 / 2` is `0.50`. Equality/ordering/hash are numeric and ignore scale. Provide an explicit `normalize(x)` that strips trailing zeros and canonicalizes zero for keys/serialization. This gives audit-friendly display without making arithmetic surprising. [I]

**Conversions.**

- `int`/`byte` -> decimal: exact (all 64-bit integers fit in 96 bits).
- decimal -> `int`/`byte`: truncate toward zero? .NET truncates toward zero. Alternatively provide explicit floor/ceil/round. Overflow is an error (`Result` or trap).
- decimal -> `string`: invariant plain decimal with stored scale; optional `format(x, scale, mode)` quantizes first.
- `string` -> decimal: `parse`/`tryParse` returning `Result`; accept optional sign, digits, `.`, optional exponent initially; reject grouping unless a style is requested.
- decimal <-> future float/double: explicit only; decimal -> float may lose precision; float -> decimal rounds/overflows and rejects NaN/Inf. Do this after floats exist.
- `int(decimal)` and `decimal(int)` syntax can reuse the existing conversion-call syntax (`int(x)`, `string(x)`); add `decimal(int)` and `decimal(string)` but make the string form `Result`-returning or a separate `parse` to avoid a hidden failure. [I]

**JSON/`jq` interop.** JSON numbers are arbitrary-precision textually, but RFC 8259's interoperability note assumes IEEE 754 binary64; standard `jq` stores numbers as doubles and only preserves untouched literals (via its decnum literal backend); arithmetic converts to double and loses precision. A lossless finance tool should serialize money as a JSON **string** by default (or a number only when the consumer is known to preserve decimals), and should not expect `jq` arithmetic to preserve 28 digits. [V: RFC 8259 §6; jq manual; I: mapping recommendation]

**Bootstrap and seed.** The compiler is built by the seed compiler (`seed/obo`) and must reach a fixed point. New decimal syntax cannot appear in `src/obo.obo` until the seed understands it. The compiler-side support (scanner token, type kind, layout, `.sym` reader/writer) can be written using only existing `int`/`string`/record constructs, so the old seed can compile it; the new compiler then supports decimal for user programs and tests. The runtime helpers are C and are built by GCC, not by the obo compiler. `make promote` runs stage 2 and stage 3 and compares bytes; a decimal feature should be promoted once, after the fixed point and suite pass, exactly like `Result` and I/O. [V: `Makefile`; I: bootstrap strategy]

### 4.4 Recommendation and minimum viable feature

**Recommendation: implement a built-in, .NET-style 16-byte decimal after floats; do not make a library record the final design.** [I]

A staged path that respects the current focus:

- **M0 (design/semantics prototype, no compiler change):** Write a Python `decimal`-based oracle (context precision 28, `ROUND_HALF_EVEN`) and a small `fixed` or record-based prototype to test scale/rounding/parse/format semantics. This is a library-only first step; it can be deleted or kept as tests.
- **M1 (scalar vertical slice):** new `decimal` kind; literals `1.23m`; locals, parameters and returns; `+ - * /`, unary `-`, comparisons; `int`/`string` conversions; `round`; trap on overflow/div0; invariant format. No fields/arrays/globals yet. This derisks the 16-byte codegen/ABI and the arithmetic helpers.
- **M2 (containers and symbols):** record fields, arrays, globals, constants, `Result[decimal]`, `.sym` type/const forms, ABI/format bumps, module cross-checks, checked `Result` APIs, `parse`. This is the usable finance feature.
- **M3 (later):** float interop after floats; decimal128/arbitrary precision only if a concrete requirement appears; currency/Money library on top.

Minimum viable feature (M1+M2): 96-bit coefficient, scale 0-28, sign; `m` literals; operators and comparisons; scale preservation; half-even default plus explicit rounding modes; no NaN/Inf/signed-zero/currency; trap errors plus checked `Result` variants; `int`/`string` conversions; fields/arrays/globals/params/returns/constants/`Result[decimal]`; `?decimal` not supported; float interop deferred. [I]

A **limited library-only first step** is worthwhile only as an interim: a `fixed` module over `int64` with a scale constant, checked add/sub/mul/div/round/parse/format. It can serve a narrow tool or prototype the finance API, but it is not the language decimal and should not block or replace the built-in feature. [I]

---

## 5. Finance semantics

### 5.1 Exact decimal versus binary float

Binary floating point cannot represent most decimal fractions exactly. The canonical example is `0.1`: it has no exact `double`/`float` representation, while decimal can represent it exactly. Microsoft's own guidance says decimal is appropriate for currency amounts, interest rates and similar numbers, and that `0.1` is exactly representable in decimal but not in `double`/`float`. Python's docs make the same point: `Decimal.from_float(0.1)` is `0.100000000000000005551115123125782702158...`, not `0.1`. For ledgers, sums, reconciliations and audit reports, use decimal, not binary float. [V: Microsoft C# floating-point types page; Python decimal docs]

### 5.2 Division, rounding and scale

Decimal addition, subtraction and multiplication are exact within the coefficient/scale limits; division is where policy enters. `1/3` has no finite decimal expansion. A finance system must decide:

- the **result scale** (for example 2 for USD amounts, 3 for KWD, 4 for CLF, 6-12 for FX rates, 0-9 for quantities);
- the **rounding mode** (half-even default; half-up/away-from-zero is common for invoices and tax; floor/ceiling for some regulations);
- **when** to round (usually once at the final monetary amount, not at every intermediate step, to avoid accumulated bias);
- whether a result must be exact (`UNNECESSARY`/inexact error), which is valuable for validation.

.NET's default is half-even for both arithmetic and `Decimal.Round`; Python's default context is half-even; Java requires an explicit `RoundingMode` for inexact division; `shopspring` defaults to 16 decimal places and ties away from zero; `apd` defaults to half-up. There is no universal finance default, but half-even is the least biased default and the best match for .NET/Python. Provide explicit modes and record the mode used in reports. [V for defaults; I for policy recommendation]

### 5.3 Scale and trailing zeros

Scale is both numeric metadata and presentation metadata. `1.20` and `1.2` are numerically equal, but `1.20` communicates two decimal places and is the customary money presentation. .NET preserves trailing zeros, Python preserves them and even documents `Decimal('1.30') + Decimal('1.20') == Decimal('2.50')` as "the customary presentation for monetary applications." Preserve scale by default; make normalization explicit for keys, canonical JSON, and deduplication. Equality/ordering/hash should be numeric so `1.0` and `1.00` do not create two different keys. [V: .NET Decimal remarks; Python decimal docs; I for obo policy]

### 5.4 Overflow and precision limits

A 96-bit coefficient is about 7.9e28 total significant digits. That is enormous for fiat amounts: at scale 2 the maximum magnitude is about 7.9e26 units (hundreds of septillions), far beyond any real ledger. But it is not unlimited:

- At scale 6, the maximum is about 7.9e22.
- At scale 9, about 7.9e19.
- At scale 18, about 7.9e10 (tens of billions). A $100 billion balance with 18 fractional digits would exceed the 96-bit coefficient.
- A 34-digit decimal128 raises the scale-18 integer range to about 1e16, but is still finite.
- Crypto assets, high-precision unit prices, scientific quantities or aggregated products may need arbitrary precision.

The important design rule is to treat "28-29 digits" as **total significant digits**, not "28 integer digits plus 28 fractional digits." Define the scale per domain and check overflow explicitly. For fixed-point int64, overflow is worse: obo's `int` arithmetic wraps silently, so every fixed-point operation must use checked helpers. [V for coefficient/range and obo wrap; I for domain guidance]

### 5.5 Currency versus quantity

Do not bake currency into the decimal type. A decimal is a number; a currency is a policy and a unit. ISO 4217 assigns minor units of 0 (JPY, KRW, etc.), 2 (USD, EUR, most), 3 (BHD, KWD, OMR, etc.) and 4 (CLF, UYW); payment providers sometimes deviate. A `Money` value should pair an amount with a currency code, and the scale/rounding policy should come from the currency/domain, not from the number of digits the user happened to type. Keep quantities (shares, units, hours) and rates (FX, interest) as decimals with their own scale policies, not as money. The Money pattern's core warning is exactly this: do not add dollars to yen without accounting for currency, and do not lose pennies through rounding. [V: ISO 4217; Fowler Money; I for obo modeling]

### 5.6 Audit and reporting

For audit/reporting:

- use exact decimal arithmetic and explicit rounding;
- preserve scale/trailing zeros for presentation;
- use invariant formatting (`.` decimal separator, no locale surprises);
- record the rounding mode and scale in report metadata;
- serialize money as a decimal string unless the consumer is known to preserve decimals;
- keep sums exact and reconcile at the same scale;
- test edge cases (midpoints, negative values, overflow, zero, very small values);
- avoid NaN/Infinity, which complicate serialization and database round-trips.

[I, grounded in the decimal docs above]

### 5.7 Performance and allocation

A built-in 16-byte decimal is allocation-free for locals, fields and array elements, but arithmetic is software and roughly an order of magnitude slower than `int64`/`double`, with division the worst. That is acceptable for ledgers, reports and CLI tools, not for tight numeric loops. A library record decimal would allocate per operation and add ownership moves, which is much worse. A fixed-point `int64` is the fastest option when its scale/range fit. [V for .NET performance; I for obo extrapolation]

### 5.8 Verdict on .NET-style decimal for finance

**.NET-style decimal is a good fit.** It has the right semantics (base-10 exactness, 28-29 digits, scale preservation, no NaN/Inf, half-even rounding), a proven implementation, and a compact 16-byte value that suits obo's no-GC/ownership model. Its limits (28-29 total digits, no huge exponent range, no arbitrary precision) are acceptable for fiat and business amounts; they are not acceptable for every possible financial quantity, so the type should not pretend to be arbitrary precision. Do not add currency or locale to the type. [I]

---

## 6. Compiler/runtime impact and sequencing

### 6.1 Phases touched

| Phase | Change | Risk | Rough size [I] |
|---|---|---|---|
| Scanner / literals | New decimal token, `m`/`M` suffix, exponent, lookahead so `1..3` remains a range and `1.` is not a field access; store literal text/value | Low-medium | 100-200 lines |
| Type model / checker | `decimal` kind, `tDecimal` singleton, `same`/`assignable`/`isOwned`/`isRef`/`nonNull`, conversions, reject `?decimal` | Low-medium | 200-400 lines |
| Layout / ABI | 16-byte size/alignment for locals, parameters, returns, fields, arrays, globals, `Result` payload; new array kind/esize; return convention; bump `SYM_ABI` | High | 400-800 lines |
| Constants / `.sym` | 16-byte rodata constants, `Sym.value` representation, `const` parser, `.sym` decimal const form (32 hex digits), reader/writer, interface hash, format/ABI bump | Medium-high | 200-400 lines |
| Codegen | Wide `Item`, literal materialization, `+ - * / %`, unary minus, comparisons, compound assignment, conversions, calls/returns, load/store, arrays/fields | High | 500-1,000 lines |
| Runtime C | 96-bit add/sub/mul/div/rem/compare, scale/round/quantize, parse, format, int conversions, error traps; 192-bit intermediates; no libc | High | 500-1,000 lines |
| Tests / mutants / docs | Arithmetic oracle, scale/rounding edge cases, parse/format fuzz, ABI/`.sym` goldens, arrays/fields/Result tests, docs examples, mutant coverage | High | 1,000-2,000 lines/test data |
| Seed promotion | Bootstrap with old seed, fixed point, memory budget, review, merge, `make promote` once | Medium | gates |

The **x86-64 128-bit multiply/divide** does not have to be written in the raw encoder. C helpers using `unsigned __int128` plus 64-bit limbs are the pragmatic route; the compiler only needs to emit a call and move 16-byte values. Inline `add`/`sub` (`add`/`adc`) and comparison can come later as an optimization. [I]

### 6.2 Effort and risk relative to Result, I/O and modules

The repository's own history gives a scale:

- `Result[T]`/`try`: **+480 compiler lines** in `src/obo.obo` (`d7fe98a`).
- Incremental I/O: **+107 compiler lines**, **+189 runtime C lines**, plus test programs and a runtime harness (`21f1cee`, `59ab1bd`).
- Modules Stage 1: **+305** and **+575** compiler lines across stages; Stage 2 symbol files: **+1,784 compiler lines** (`60d0888`, `dcf5f3f`, `d104bb6`).

A decimal primitive is likely **larger than `Result` or I/O** and **comparable to the module work in compiler surface**, plus a substantial C arithmetic/format/parse layer and more edge-case testing. The risk is higher than `Result` because layout/ABI changes touch every value path and because arithmetic correctness (scale, rounding, overflow, parse/format) has a large state space. It is not a routine feature. [I, based on the cited diff sizes]

### 6.3 Why it should remain after floats

- Floats are 8-byte, hardware-supported (SSE), and simpler to add; they establish the real-literal, conversion and formatting patterns that decimal can reuse.
- The current roadmap puts floats at position 11; decimal is explicitly after them. The current focus is stdlib/`eza`, and no current tool needs decimal: `sort` already compares decimal-looking numbers as exact digit strings without floating point (`FORK.md` around line 3255), and `jq` numbers are double-based anyway.
- Decimal is finance-specific and 16-byte/software; doing it before floats would spend the harder feature first and delay the tool roadmap. [V for roadmap/status/FORK; I for sequencing]

### 6.4 Limited library-only first step versus built-in

A library-only first step can be useful but is not a substitute:

- **`fixed` module over `int64`:** no compiler/runtime/seed change; checked functions; scale constant; exact for one currency/scale; can be used by a narrow finance tool now. It proves the API and tests, but cannot be a general `decimal`.
- **Record-based decimal:** two `int`s or a 128-bit manual implementation in a `ref record`; no compiler change, but one allocation per value/result, ownership moves, no operators, no constants. Useful as a semantics prototype only.
- **Built-in decimal:** the real feature; requires the compiler/runtime work above. This is the recommended destination.

Do not ship a record-based decimal as the language's decimal: it would freeze a heap-allocating, move-heavy representation into user code and make a later built-in type a breaking change. [I]

### 6.5 Gates and tests

The existing gates that a decimal feature must join:

- `obo/tests/doc_check.py` for `LANGUAGE.md` examples.
- `obo/tests/run_fork.py` and the stage/self-hosting tests for behavior and the fixed point.
- `obo/tests/port_compare.py` for the port/compiler comparison.
- `obo/tests/mutate_fork.py`, `mutate_io.py`, `mutate_port.py` and the other mutant drivers; add decimal mutants (scale off-by-one, wrong tie direction, missing overflow, wrong sign, wrong array element size, wrong `.sym` const encoding).
- `obo/tests/sym_fuzz.py` and module tests for `.sym` reader/writer and ABI/format versions.
- `obo/tests/own_fuzz.py` for ownership interactions (decimal itself is unowned, but `Result[decimal]` and record/array containers are owned).
- `obo/tests/tools.py` must remain green; decimal should not disturb the tool gate.
- A Python `decimal` oracle with context precision 28 and `ROUND_HALF_EVEN` for arithmetic/scale/rounding cases; expected values for .NET-specific scale rules (division, trailing zeros) should be recorded as goldens rather than assumed identical to Python (Python contexts/scale rules differ in division and formatting).
- ABI/`.sym` goldens: record/array/Result layouts with decimal fields, 16-byte constants, cross-module constants.
- Memory budget: a 16-byte type will increase compiler/test memory only if the compiler uses it; the compiler itself need not, so the budget impact should be small. The seed promotion must still meet the existing cap.

`make promote` remains the final gate: stage 2 == stage 3, suite green, then copy stage 2 to `seed/obo` once. [V: Makefile/tests paths; I: test plan]

---

## 7. Open questions and recommended defaults

| Question | Recommended default | Rationale |
|---|---|---|
| Type name | `decimal` (lowercase, like `int`/`byte`/`bool`/`string`); no `Decimal` alias | obo primitives are lowercase; one spelling avoids type-identity confusion |
| Literal suffix | `m`/`M` required for decimal literals (`1m`, `1.23m`, `1.5e6m`) | C#-compatible; leaves bare real literals for future floats/doubles; `d`/`D` should mean double later |
| Precision / scale | .NET-style 96-bit coefficient, scale 0-28, 28-29 digits | Fits 16 bytes, covers fiat/business amounts, simpler than decimal128; 34-digit/arbitrary later only on concrete need |
| Rounding default | Half-even for arithmetic and `round` default | Matches .NET and Python; least biased; explicit modes for policy |
| Rounding modes | Half-even, half-up/away, half-down/toward-zero, down/truncate, up/away, floor, ceiling, unnecessary | Covers invoice/tax/regulatory policies without global context |
| Scale preservation | Preserve by default; explicit `normalize`; numeric equality/hash | Audit/display need trailing zeros; keys/serialization need canonical form |
| Error model | Operators trap on overflow/div0; checked functions and `parse` return `Result[decimal]` | Keeps `a+b` ergonomic and allocation-free; recovery available where needed |
| Signed zero / NaN / Infinity | None; zero canonical positive; division by zero is an error | Avoids special-value rules and serialization ambiguity; matches .NET behavior in practice |
| Formatting / culture | Invariant C locale, plain decimal, no grouping by default; formatting/currency helpers separate | Deterministic audit output; no locale state in a single-pass language |
| Currency | Not in `decimal`; separate `Money`/currency code and scale policy | ISO minor units vary 0-4; one type cannot encode currency policy |
| JSON / `jq` | Serialize money as a JSON string by default; number only when the consumer is known to preserve decimals; do not rely on `jq` arithmetic | RFC 8259 binary64 interoperability; jq arithmetic converts to double |
| Fixed-point 64-bit first? | Not as the language type; optional `int64` library interim for a narrow tool | 18-19 digits, fixed scale, silent overflow; not general finance |
| Optionals | No `?decimal` initially; use `Result[decimal]` or a record with a flag | Zero is a valid decimal; a sentinel word cannot represent nil |
| Containers / ABI | Support fields, arrays, globals, parameters, returns, constants and `Result[decimal]`; 16-byte value; bump `SYM_ABI` | A decimal that cannot live in records/arrays is not usable; ABI must change exactly once |
| Float interop | After floats; explicit conversions only; decimal->float lossy, float->decimal rounded/overflow-checked | Floats are position 11; no implicit mixing (C# rule) |
| IEEE decimal128 compatibility | Not initially | 34 digits/NaN/Inf/BID-DPD are a different, larger design; revisit only for interop |

---

## 8. Sources

### Microsoft / .NET

- C# floating-point numeric types (range, precision, 16 bytes, not IEEE decimal128, no NaN/Inf, literal suffixes, performance guidance): https://learn.microsoft.com/en-us/dotnet/csharp/language-reference/builtin-types/floating-point-numeric-types
- `System.Decimal` struct (remarks, 96-bit integer, scale, trailing zeros): https://learn.microsoft.com/en-us/dotnet/api/system.decimal?view=net-10.0
- `Decimal.GetBits` (bits 16-23 scale, bit 31 sign, negative zero note, examples with 1 vs 1.00): https://learn.microsoft.com/en-us/dotnet/api/system.decimal.getbits?view=net-10.0
- `Decimal.Round` (default ToEven, banker's rounding, 0-28 decimals): https://learn.microsoft.com/en-us/dotnet/api/system.decimal.round?view=net-10.0
- `Decimal.Equals` and `Decimal.GetHashCode`: https://learn.microsoft.com/en-us/dotnet/api/system.decimal.equals?view=net-10.0
- `Decimal(double)` constructor (current .NET 11 exact rounding): https://learn.microsoft.com/en-us/dotnet/api/system.decimal.-ctor?view=net-10.0
- `Decimal.ToString` (culture, general format): https://learn.microsoft.com/en-us/dotnet/api/system.decimal.tostring?view=net-10.0
- C# specification §8.3.8 "The decimal type" (exact result, half-even, overflow, 128-bit): https://learn.microsoft.com/en-us/dotnet/csharp/language-reference/language-specification/types
- C# specification §6.4.5.4 "Real literals" (`m` suffix, exponent, `2.900m` scale preservation): https://learn.microsoft.com/en-us/dotnet/csharp/language-reference/language-specification/lexical-structure
- SQL Server decimal/numeric (precision 38, storage 5/9/13/17 bytes, rounding/overflow): https://learn.microsoft.com/en-us/sql/t-sql/data-types/decimal-and-numeric-transact-sql

### dotnet/runtime source (pinned commit `a4f8db31af247193ddb8bcdb6313b5ee8f626487`)

- `src/libraries/System.Private.CoreLib/src/System/Decimal.cs` (fields, masks, MaxValue/MinValue, operators, equality/hash, conversions, Scale): https://github.com/dotnet/runtime/blob/a4f8db31af247193ddb8bcdb6313b5ee8f626487/src/libraries/System.Private.CoreLib/src/System/Decimal.cs
- `.../System/Decimal.DecCalc.cs` (`DEC_SCALE_MAX`, `ScaleResult`, `SearchScale`, `DecAddSub`, `VarDecMul`, `VarDecDiv`, `InternalRound`, `GetHashCode`, `VarDecFromR8`): https://github.com/dotnet/runtime/blob/a4f8db31af247193ddb8bcdb6313b5ee8f626487/src/libraries/System.Private.CoreLib/src/System/Decimal.DecCalc.cs
- `.../System/Number.Parsing.cs` (`ParseDecimal`, `TryParseDecimal`, `TryNumberToDecimal`, scale preservation): https://github.com/dotnet/runtime/blob/a4f8db31af247193ddb8bcdb6313b5ee8f626487/src/libraries/System.Private.CoreLib/src/System/Number.Parsing.cs
- `.../System/Number.Formatting.cs` (`FormatDecimal`, `DecimalToNumber`, scale-based formatting): https://github.com/dotnet/runtime/blob/a4f8db31af247193ddb8bcdb6313b5ee8f626487/src/libraries/System.Private.CoreLib/src/System/Number.Formatting.cs
- dotnet/runtime decimal performance issue (x86 native/managed ADD/MUL ns): https://github.com/dotnet/runtime/issues/10437
- Stack Overflow, "C# Decimal datatype performance" (old 100M/1M-operation benchmarks, decimal ~7-30x slower): https://stackoverflow.com/questions/366852/c-sharp-decimal-datatype-performance

### IEEE 754 / decimal formats / implementations

- Decimal128 format (34 digits, 113-bit coefficient, exponent bias/range, NaN/Inf, BID/DPD): https://en.wikipedia.org/wiki/Decimal128_floating-point_format
- Decimal64 format (16 digits, 50-bit continuation, exponent bias 398): https://en.wikipedia.org/wiki/Decimal64_floating-point_format
- Decimal floating point formats (p/emax/emin/Etiny table for decimal32/64/128): https://en.wikipedia.org/wiki/Decimal_floating_point
- IEEE 754 (default roundTiesToEven, NaN/Inf, BID/DPD): https://en.wikipedia.org/wiki/IEEE_754
- Intel Decimal Floating-Point Math Library (software decimal32/64/128 on x86): https://www.intel.com/content/www/us/en/developer/articles/tool/intel-decimal-floating-point-math-library.html
- IBM decNumber / General Decimal Arithmetic (arbitrary precision, context, IEEE decimal formats): https://speleotrove.com/decimal/

### Other languages / libraries

- Java `BigDecimal` (unscaled BigInteger + int scale, divide exception, rounding modes, equals vs compareTo, no NaN/Inf): https://docs.oracle.com/en/java/javase/21/docs/api/java.base/java/math/BigDecimal.html
- Python `decimal` (default context, traps, special values, trailing zeros, float exactness, libmpdec): https://docs.python.org/3/library/decimal.html
- Python `fractions` (exact rational, normalization, float conversion, formatting limits): https://docs.python.org/3/library/fractions.html
- Rust `rust_decimal` (96-bit + scale, 16 bytes, trailing zeros, rounding strategies, checked ops): https://docs.rs/rust_decimal/latest/rust_decimal/ and https://github.com/paupino/rust-decimal
- Rust `bigdecimal` (BigInt + i64 scale, default precision 100, HalfEven): https://docs.rs/bigdecimal/latest/bigdecimal/ and https://github.com/akubera/bigdecimal-rs
- Go `shopspring/decimal` (big.Int + int32 exp, DivisionPrecision 16, DivRound ties away, allocation warning): https://pkg.go.dev/github.com/shopspring/decimal and https://github.com/shopspring/decimal
- Go `cockroachdb/apd` (General Decimal Arithmetic, Context, NaN/Inf, exponent limits, RoundHalfUp default): https://pkg.go.dev/github.com/cockroachdb/apd/v3
- PostgreSQL `numeric` (arbitrary precision, exact, NaN/Inf, variable storage, slow): https://www.postgresql.org/docs/current/datatype-numeric.html
- ISO 4217 (minor units 0/2/3/4, examples JPY/USD/KWD/CLF): https://en.wikipedia.org/wiki/ISO_4217
- jq manual (numbers are IEEE754 double; literals preserved; arithmetic converts): https://jqlang.org/manual/
- RFC 8259 JSON (number grammar; binary64 interoperability; precision caveats): https://www.rfc-editor.org/rfc/rfc8259.txt
- Martin Fowler, Money pattern (currency risk, rounding, Money class): https://martinfowler.com/eaaCatalog/money.html

### obo repository (HEAD `4e733a6`, branch `tools-modules`)

- `/home/chris/obx/obo/src/obo.obo`: type kinds and `Type`/`Sym`/`Item` records (around 882-1175), layout/`fieldList` (9448), parameters (8069), globals (6299), `rtCall` (2334), `binop` (5980), `constDecl` (8233), `appConst` (9020), `appTypeExpr` (8924), `symConstDecl` (9748), `symParseType` (9384), `okVariantOf` (1995), `Result` construction (4950-4990).
- `/home/chris/obx/obo/runtime/runtime.c`: object/array layout and `rt_alloc_array` element size (around 53-68, 356), record allocation/descriptors (217, 278), `rt_append`/`rt_truncate`, freestanding C environment.
- `/home/chris/obx/obo/LANGUAGE.md`: types table (section 1), `.sym` format/constants/versions (section 6).
- `/home/chris/obx/README.md`: current status and roadmap (Result, I/O, modules, tools, floats later).
- `/home/chris/obx/obo/Makefile`: `runtime.elf`/`runtime_text.bin` build, `obo` build by seed, `tools`, `make promote` fixed point.
- `/home/chris/obx/obo/FORK.md`: design record, including exact-digit `sort` comparison around line 3255 and the tools/modules gates.
- Tests and gates: `/home/chris/obx/obo/tests/doc_check.py`, `run_fork.py`, `port_compare.py`, `mutate_fork.py`, `mutate_io.py`, `mutate_port.py`, `own_fuzz.py`, `sym_fuzz.py`, `tools.py`.
