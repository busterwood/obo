# A lossless decimal type for obo: design research

**Date:** 2026-10-28

**Addendum date:** 2026-10-28 — database-first revision. No code, branch, worktree, seed or configuration was changed. This remains a future feature for after floats, not a proposal to implement it during the current stdlib/`eza` phase.

**obo version examined:** branch `tools-modules`, HEAD `4e733a6` ("Merge branch 'tools-modules': build tools from source modules"). The obo source assumptions were not re-examined for this addendum; no obo code was changed.

**.NET reference:** .NET 10/11 documentation and `dotnet/runtime` `main` at commit `a4f8db31af247193ddb8bcdb6313b5ee8f626487` (2026-10-28). The runtime source is ahead of released .NET; version-specific behavior is noted where it matters.

**Database references:** vendor documentation/specifications and pinned PostgreSQL/.NET source in §8. No database product was run. Values marked **[O]** are observed or implementation-derived, not vendor contracts.

**Evidence convention.** **[V]** means documented or verified from the cited primary source (vendor documentation, protocol specification, source code, IEEE/standards material, a language's own docs/source, or obo's source at the commit above). **[O]** means observed or implementation-derived — for example a `DUMP` output, `DBCC PAGE` bytes, or a driver/source behavior — and is not a vendor guarantee. **[I]** means inference, estimate or recommendation. Facts in sections 2 and 3 are [V] unless marked; the database byte layouts distinguish [V] from [O]; the obo effort/risk estimates and all recommendations are [I].

> **2026-10-28 addendum — database compatibility and storage formats.** The authoritative new context is: the user works in finance; the main use case is storing and retrieving exact numbers in databases, not general arithmetic; priority databases are SQL Server, Oracle and possibly Sybase (PostgreSQL later); and database access will be **in-process obo/C interop**, with no helper process. The decimal type must therefore map losslessly to database values and wire formats. This changes the target from .NET `System.Decimal` (96-bit coefficient, scale 0–28, 28–29 digits) to a **38-significant-digit, exponent-and-display-scale** type. **§A is authoritative for the revised recommendation.** §1, §3, §4, §5, §6, §7 and §8 are updated to match; sections 2 and the original 16-byte sketches are retained as .NET background and are marked superseded where they conflict.

---

> **2026-10-29 literal-policy addendum.** The user approved the numeric literal policy in [numeric-literals-decision-2026-10-29.md](numeric-literals-decision-2026-10-29.md): bare fractional and exponent literals are exact `decimal`; the `f`/`F` suffix marks binary `float`; there is no `m` suffix and no implicit float literal. The `1.23m` examples in section 1 and the original background section are superseded for literal syntax only. The 24-byte/38-digit decimal design, the database analysis and all other recommendations below are unchanged.

## 1. Executive verdict

**Revised 2026-10-28 (database-first): the right target is a database-compatible fixed decimal, not a copy of `System.Decimal`.** [I]

The decisive facts:

- **SQL Server** `decimal`/`numeric` has precision **1–38**, scale **0–p**, storage **5/9/13/17 bytes**, and a sign byte plus a little-endian binary magnitude in TDS; `System.Decimal` (96-bit coefficient, scale 0–28, 28–29 digits) cannot hold `decimal(38,0)` or `decimal(38,10)` maximum values, while `System.Data.SqlTypes.SqlDecimal` (128-bit coefficient, 38 digits) can. [V; see §A.1]
- **Oracle** `NUMBER` has **38 digits**, scale **-84..127**, a documented range of about **1e-130 to 9.99e125**, and a base-100 internal format with an exponent byte. It needs an exponent and display-scale representation, not just a 0–38 scale. [V; see §A.2]
- **Sybase ASE** `numeric`/`decimal` has precision **1–38**, scale **0–38**, and a sign byte plus a base-256 magnitude in SAP's documented client/TDS representation; it is not the packed-BCD format sometimes assumed. **SQL Anywhere** is a separate product with precision **1–127** and its own storage rules. [V; see §A.3]
- **PostgreSQL** `numeric` is variable-length base-10000 with arbitrary implementation limits (**131072** digits before and **16383** after the decimal point), a stored display scale, `NaN`, and `Infinity`/`-Infinity` since PostgreSQL 14. [V; see §A.4]
- All four use **sign + decimal coefficient + exponent/scale metadata**, never binary floating point. Exact decimal is the right model. [V]
- The minimum precision for the priority databases is **38 significant digits**; Oracle adds a large exponent range and negative scale; PostgreSQL is arbitrary. A 16-byte/96-bit `System.Decimal`-style type is therefore insufficient. The relevant .NET starting point is `SqlDecimal` (128-bit/38), but even `SqlDecimal` has no exponent or display scale. [V for limits; I for consequence]
- **Recommended primary design:** a **24-byte value type** with a **128-bit unsigned coefficient**, sign, **int16 decimal exponent**, and **uint16 display scale**; arithmetic uses **256-bit intermediates** (38×38 multiplication is up to 76 digits). It is a compiler primitive with value semantics, no heap allocation, and finite values only. Column precision/scale metadata lives beside the value, not inside every decimal. This covers SQL Server and ASE `decimal(38,s)` and documented Oracle `NUMBER`; it covers PostgreSQL `numeric(p,s)` when p ≤ 38 and the value is finite. [I; see §A.6]
- **Recommended second tier:** a later arbitrary-precision `numeric` (heap value, base-10^9 limbs, PostgreSQL-style display scale and special values) for PostgreSQL unconstrained columns, values above 38 significant digits, Oracle raw 39–40-digit internal values if they must round-trip, and NaN/Infinity. [I; see §A.6]
- **A 256-bit coefficient storage** (about 40 bytes per value) is a conditional upgrade, not the primary design: it covers 76-digit products and Oracle's 20-mantissa-byte raw form, but roughly doubles the ABI cost. The decision gate is real Oracle data with >38 significant digits. [I]
- The main cost is now **24-byte layout/ABI plus database conversion**: locals, parameters, returns, record fields, arrays, globals, constants, `Result[decimal]` payloads, `.sym` constants, and in-process TDS/OCI/libpq conversion. This is larger than the original 16-byte estimate: larger than `Result` or I/O and comparable to or larger than the module work, with substantial arithmetic and byte-format testing. It should still remain after floats. [V for current one-word layout; I for effort]

**Recommended minimum viable feature, when scheduled after floats:** a built-in **24-byte `decimal`** with a 128-bit coefficient, 38 significant digits, sign, int16 decimal exponent and uint16 display scale; `m` literals; `+ - * /`, unary `-`, comparisons; `int`/`string` conversions; parse/format preserving display scale; `round`/`quantize` with explicit rounding modes; finite-only semantics (no NaN/Infinity/signed zero); traps on overflow and division by zero plus checked `Result`-returning variants; **database metadata-aware binding** (precision, scale, nullable) that never silently rounds; fields, arrays, globals, parameters, returns, constants and `Result[decimal]`; no `?decimal`; float interop after floats; arbitrary `numeric` and DB adapters later. No currency is baked into the type. [I]

### 1.1 What changed from the original .NET-based verdict, and why

| Original (2026-10-28, before database context) | Revised (database-first) | Why |
|---|---|---|
| 96-bit coefficient, scale 0–28, 28–29 digits | 128-bit coefficient, 38 digits, int16 exponent, uint16 display scale | SQL Server/ASE need 38 digits; Oracle needs exponent range and scale -84..127 |
| 16-byte value | 24-byte value (24-byte alignment/slot) | 16-byte coefficient + exponent + display scale + sign/flags |
| `System.Decimal` as the model | `SqlDecimal` (128-bit/38) plus database wire formats | `System.Decimal` cannot hold `decimal(38)`; `SqlDecimal` is the lossless SQL Server client model |
| Scale-only (`c/10^s`) | Coefficient × 10^exponent plus display scale | Oracle values such as 1e125 and 1e-130 cannot be represented by scale 0–38 alone |
| Half-even default | Half-away from zero for database scale conversion and `round`; half-even available | SQL Server, Oracle, PostgreSQL `numeric` and ASE use commercial/half-away rounding; .NET is the outlier |
| No arbitrary tier | Later arbitrary `numeric` second tier | PostgreSQL unconstrained, >38 digits, Oracle 39–40-digit raw values, NaN/Infinity |
| One ABI change at 16 bytes | Larger 24-byte ABI change plus DB metadata/conversion layer | More value paths and database adapters |

**Background: the original .NET-based verdict (retained; superseded for the target semantics).**

**A .NET-`System.Decimal`-style type is the best fit for obo among the designs examined, but it should be a compiler primitive, not a library type, and it should stay after floats.** [I]

- .NET `decimal` is a **16-byte value type** with a **96-bit integer coefficient**, a sign bit and a **scale of 0-28**. It represents `(-1)^sign * coefficient / 10^scale`, with a range of about **±7.9228e28** and **28-29 significant digits**. It has **no NaN or infinity**, and arithmetic uses **round-half-to-even ("banker's rounding")**. It is **not IEEE 754-2008 decimal128**. [V]
- The design is proven in finance, is allocation-free for ordinary values, and matches obo's ownership model: decimal values would be unowned and copied by value, like `int`. [I]
- obo currently has **no value records**; every `record` is `ref record` and every field, parameter, global and array element is one 8-byte word. A library decimal built from two `int`s would therefore be a heap-allocated reference type, one allocation per arithmetic result, moved and freed under single ownership, with no operator overloading and no methods yet. That is a poor core numeric type. A true value decimal forces a compiler primitive. [V for layout/no value records; I for consequence]
- The main cost is not the decimal algorithm; it is **layout and ABI**: locals, parameters, returns, record fields, array elements, globals, constants and `Result[decimal]` payloads all assume one 8-byte word today, and the code generator's `Item` and runtime-call path assume 8-byte values and a single `RAX` return. This is a medium-large, high-risk language feature, larger than `Result` or I/O and comparable to the module work, with substantial arithmetic-correctness testing. [V for assumptions; I for effort]
- A 64-bit fixed-point integer (`cents`/`mills`) is simple and fast, but it is not a general finance decimal: ISO 4217 minor units vary from 0 to 4, it carries no scale, and obo's `int` arithmetic wraps silently on overflow. It is a reasonable **temporary library** for a narrow tool, not the language's decimal type. [V for ranges/minor units/wrap; I for recommendation]

**Original recommended minimum viable feature (background):** a built-in 16-byte `decimal` with a 96-bit coefficient, scale 0-28, sign, literals such as `1.23m`, `+ - * /` and unary `-`, comparisons, `int`/`string` conversions, `round`/`quantize` with explicit rounding modes, scale preservation, no NaN/Infinity/signed-zero semantics, arithmetic traps on overflow and division by zero, and checked `Result`-returning variants for recovery. No currency is baked into the type. Float interop waits until floats exist. [I]

---

## A. 2026-10-28 addendum: database compatibility and storage formats

### A.0 Scope, method and terminology

This addendum answers a different question from the original report: not "what is the best general-purpose finance decimal?" but "what value type can store and retrieve every decimal value of the priority databases exactly, through in-process obo/C interop, without a helper process?" No database product was run for this research. Vendor documentation and protocol/source code are cited; where a byte layout is observed rather than documented, it is marked **[O]** and should be verified against the target product/version before it is relied on. [I for method]

Terminology used below:

- **Coefficient**: the unsigned decimal integer whose digits are the significant digits of the value.
- **Exponent**: the power of ten applied to the coefficient: `value = (-1)^sign * coefficient * 10^exponent`.
- **Display scale (`dscale`)**: the number of digits to show after the decimal point. It is presentation/quantum metadata; it is not necessarily the same as the exponent, and it is what preserves `1.20` versus `1.2`.
- **Column precision/scale**: the database column's declared maximum significant digits `p` and fractional digits `s`. These are schema metadata, not invariants of every value. SQL Server treats every `(p,s)` combination as a distinct type; obo should not.
- **Wire format**: bytes on the client/server protocol (TDS, OCI, libpq, Sybase TDS). **On-page format**: bytes in the database's row storage. **Host format**: the client library's struct (for example `SqlDecimal`, `OracleDecimal`, `CS_NUMERIC`).

The central result is simple: SQL Server, Oracle, Sybase ASE and PostgreSQL all represent exact decimals as **sign + decimal coefficient + exponent/scale metadata**. None uses binary floating point for its decimal/numeric type. That validates the original report's "exact decimal, never binary float" conclusion, but it invalidates the 16-byte/28-29-digit target: the priority databases need at least 38 significant digits, and Oracle needs an exponent range and display scale that a scale-only 96-bit type cannot provide. [V for formats; I for consequence]

### A.1 SQL Server `decimal` / `numeric`

#### A.1.1 Type, precision, scale and storage

**Documented facts [V].** `decimal` and `numeric` are synonyms. Precision `p` is the maximum total number of decimal digits on both sides of the point; `p` is 1–38, default 18. Scale `s` is the number of digits to the right of the point; `s` is 0–p, default 0. With maximum precision, values range from `-10^38 + 1` through `10^38 - 1`. SQL Server considers each `(p,s)` combination a different data type. Storage depends only on precision:

| Precision `p` | Storage bytes | Sign + magnitude |
|---:|---:|---|
| 1–9 | 5 | 1 sign + 4-byte magnitude |
| 10–19 | 9 | 1 sign + 8-byte magnitude |
| 20–28 | 13 | 1 sign + 12-byte magnitude |
| 29–38 | 17 | 1 sign + 16-byte magnitude |

Source: Microsoft Learn, "decimal and numeric (Transact-SQL)", SQL Server 2017–2025 (`view=sql-server-ver17`, page date 2026-10-13), Arguments and storage table. [V]

Conversion to a lower precision/scale **rounds**; if the value is too large for the target precision, an arithmetic overflow error 8115 occurs when `SET ARITHABORT` or `SET ANSI_WARNINGS` is `ON`; with both `OFF` the result is `NULL` and a warning is returned unless `SET ARITHIGNORE` is `ON`. Loss of only precision/scale does not raise an error. [V: same page, "Convert decimal and numeric data"]

Consequences for obo: a SQL Server decimal value needs a **38-digit coefficient and a scale 0–38**; the column's `(p,s)` must be carried separately so a bind can validate/round exactly as the server would. A scale-only 0–28 type is not enough. [I]

#### A.1.2 TDS wire format

**Documented facts [V].** In TDS, `decimal` and `numeric` are `DECIMALNTYPE = 0x6A` and `NUMERICNTYPE = 0x6C`. The legacy fixed tokens `DECIMALTYPE = 0x37` and `NUMERICTYPE = 0x3F` are not supported for `INSERT BULK`; use the N types. For the N types, valid non-NULL lengths are `0x05`, `0x09`, `0x0D`, `0x11`; NULL is length `0x00`. The type metadata (`TYPE_INFO`) carries `PRECISION` then `SCALE`; precision must be ≤ 38 and scale ≤ precision. [V: [MS-TDS] v20260617, §2.2.5.4.2, §2.2.5.4.3, §2.2.5.6]

The value encoding is:

1. one 1-byte unsigned sign: **0 = negative, 1 = nonnegative**;
2. one 4-, 8-, 12- or 16-byte integer representing `value * 10^scale`, width fixed by precision: 4 bytes for `1 <= p <= 9`, 8 for `10 <= p <= 19`, 12 for `20 <= p <= 28`, 16 for `29 <= p <= 38`.

The specification's exact wording is "One 4-, 8-, 12-, or 16-byte signed integer that represents the decimal value multiplied by 10s" (the PDF renders `10^s` as `10s`). Read literally, a signed magnitude plus a separate sign byte is redundant. Implementations and the observed on-page format treat the bytes as an **unsigned little-endian magnitude** with a separate sign byte; TDS general rules say all integer types are little-endian unless otherwise specified. We flag this as a specification ambiguity, not as a second sign bit. [V for quote/widths; O for unsigned-magnitude reading]

A `decimal(38,10)` TDS/value payload therefore has length `0x11` (17 = 1 sign + 16 magnitude), precision `0x26` (38), scale `0x0A` (10), sign `0x01` for nonnegative, and 16 magnitude bytes. [V for layout; I for the example framing]

#### A.1.3 On-page representation

**Documented facts [V].** Microsoft documents the storage sizes (5/9/13/17 bytes) but not the on-page byte layout. The column's precision and scale are catalog metadata (`sys.columns.precision`, `sys.columns.scale`, `sys.columns.max_length`), not bytes in the value. [V: Microsoft decimal docs; O for catalog behavior]

**Observed [O].** A SQL Server 2019 `DBCC PAGE` investigation reports the on-page form as **1 sign byte + an unsigned little-endian magnitude**, where the magnitude is `value * 10^scale`, zero-padded to the tier width. `01` = positive, `00` = negative; negatives use the same magnitude with only the sign byte changed, not two's complement. The decimal point is not stored. Examples from that investigation for `decimal(9,2)`:

| Value | On-page bytes | Meaning |
|---|---|---|
| `123.45` | `01 39 30 00 00` | positive; magnitude `12345` = `0x3039` |
| `-123.45` | `00 39 30 00 00` | negative; same magnitude |
| `1.00` | `01 64 00 00 00` | positive; magnitude `100` |
| `0.00` | `01 00 00 00 00` | positive zero |

The catalog reports `max_length` 5/9/13/17 for `decimal(9,2)`, `decimal(19,4)`, `decimal(28,6)`, `decimal(38,0)`. Scale does not change the byte count. Source: SQL Server Science, "How SQL Server Stores DECIMAL and NUMERIC, Down to the Bytes" (experimental, SQL Server 2019), which explicitly labels the layout observed rather than documented. [O]

For interoperability, the safest statement is: **TDS is the documented wire contract; the on-page layout is an implementation detail observed to match sign + little-endian magnitude.** obo should convert via TDS/driver APIs, not by reading SQL Server pages. [I]

#### A.1.4 .NET client models: `System.Decimal` versus `SqlDecimal`

| Property | `System.Decimal` | `System.Data.SqlTypes.SqlDecimal` |
|---|---|---|
| Managed layout | 16 bytes; 96-bit coefficient in three 32-bit words + flags word (sign, scale 0–28) | Four 32-bit unsigned data words = 128-bit coefficient; separate status/length/precision/scale bytes; managed struct is larger than 16 bytes |
| Precision | 28–29 significant digits | 38 significant digits |
| Scale | 0–28 | 0–38 |
| Exponent | None | None |
| Maximum | About ±7.9228e28 (`2^96 - 1` coefficient) | About ±(10^38 - 1) |
| SQL Server `decimal(38)` lossless? | No | Yes (for SQL Server's value range) |
| SQL Server wire helper | No | `WriteTdsValue` writes the four coefficient words; `ConvertToPrecScale` adjusts precision/scale |

Sources: Microsoft `SqlDecimal` docs (remarks: `SqlDecimal` uses four unsigned 4-byte integers, maximum precision and scale 38, range -10^38+1..10^38-1); `SQLDecimal.cs` at `dotnet/runtime` commit `ee61e035dc191ec606d1fd83dfa171a8914feb04` (fields `_bStatus`, `_bLen`, `_bPrec`, `_bScale`, `_data1.._data4`; `s_NUMERIC_MAX_PRECISION = 38`); ADO.NET "SQL Server Data Type Mappings" (decimal/numeric map to .NET `Decimal` and `SqlDbType.Decimal`, with `GetSqlDecimal` as the SqlTypes accessor; the page explicitly notes .NET `Decimal` max scale 28 versus SQL Server max scale 38). [V]

The practical conclusion: **`SqlDecimal` is the relevant .NET model for SQL Server compatibility, not `System.Decimal`.** But `SqlDecimal` still has no exponent and no display-scale metadata beyond the SQL Server precision/scale pair; it is a better coefficient container, not a complete database-first decimal. [V for `SqlDecimal`; I for the conclusion]

#### A.1.5 Exact `decimal(38,10)` example

`decimal(38,10)` has 28 integer digits and 10 fractional digits. Its maximum and minimum are:

```
max =  9999999999999999999999999999.9999999999
min = -9999999999999999999999999999.9999999999
coefficient = 10^38 - 1 = 99999999999999999999999999999999999999
            = 0x4b3b4ca85a86c47a098a223fffffffff
```

The coefficient does not fit a 96-bit/`System.Decimal` coefficient; it fits a 128-bit coefficient. The 17-byte on-page/TDS value for the maximum is one sign byte plus the 16-byte little-endian encoding of `10^38 - 1`:

```
positive: 01 ff ff ff ff 3f 22 8a 09 7a c4 86 5a a8 4c 3b 4b
negative: 00 ff ff ff ff 3f 22 8a 09 7a c4 86 5a a8 4c 3b 4b
```

A non-boundary example, `1234567890123456789012345678.1234567890`, has coefficient `12345678901234567890123456781234567890` and bytes:

```
01 d2 7a a1 0e 4f 90 49 c4 13 33 02 f0 f6 b0 49 09
```

For TDS, the TYPE_INFO/row framing carries length `0x11`, precision `0x26` and scale `0x0A`; the 17 bytes above are the value. All values here are derived from the documented sign + little-endian magnitude layout; the on-page sign polarity is the observed [O] result. [V for arithmetic/widths; O for sign polarity]

The same example shows why `System.Decimal` fails: `decimal(38,10)` maximum has a 38-digit coefficient, while `System.Decimal` has only 96 coefficient bits (28–29 digits). `SqlDecimal` holds it; a database-first obo type must too. [V]

#### A.1.6 What SQL Server requires of obo

- **Precision:** 38 significant digits.
- **Scale:** 0–38, carried from the column; display scale is `s`.
- **Magnitude:** binary coefficient; little-endian wire/on-page; sign byte with SQL Server's polarity.
- **Metadata:** precision, scale, nullable, and `max_length` tier must be preserved and used when binding/fetching.
- **No float:** never convert through `double`/`float`; `float` conversion is explicitly lossy in the SQL Server docs. [V]

### A.2 Oracle `NUMBER`

#### A.2.1 Type, precision, scale, range and metadata

**Documented facts [V].** Oracle `NUMBER(p,s)`: precision `p` is 1–38; scale `s` is -84..127; both are in decimal digits. A `NUMBER` value requires from 1 to 22 bytes. If a value exceeds the precision, Oracle returns an error; if it exceeds the scale, Oracle rounds. Negative scale rounds to the left of the decimal point: `NUMBER(6,-2)` stores `123.89` as `100`; `NUMBER(10,-2)` means "round to hundreds." Examples from the SQL Language Reference:

| Actual data | Specified as | Stored as |
|---|---|---|
| `123.89` | `NUMBER(3)` | `124` |
| `123.89` | `NUMBER(3,2)` | exceeds precision |
| `123.89` | `NUMBER(5,2)` | `123.89` |
| `123.89` | `NUMBER(6,1)` | `123.9` |
| `123.89` | `NUMBER(6,-2)` | `100` |
| `.000127` | `NUMBER(4,5)` | `.00013` |
| `1.2e-5` | `NUMBER(2,5)` | `0.00001` |

Oracle's OCCI `Number` class documents 38 decimal digits of guaranteed precision and the range `1e-130` through `9.99999999999999999999999999999999999999e125` (38 nines at the top), symmetric for negatives; zero is exact; `NUMBER` has representations for positive and negative infinity used to indicate overflow, but no NaN. The SQL Reference also says Oracle guarantees portability of numbers with precision up to **20 base-100 digits**, equivalent to 39 or 40 decimal digits depending on the decimal point position — see the 38-versus-40 caveat in §A.2.5. [V: Oracle 19c SQL Reference, "Data Types"; OCCI `Number` class docs]

Oracle column precision and scale are catalog metadata (`ALL_TAB_COLUMNS.DATA_PRECISION`, `DATA_SCALE`, `DATA_LENGTH`). The value's internal representation does not carry a SQL display scale. [V for metadata; I for consequence]

Oracle `BINARY_FLOAT` and `BINARY_DOUBLE` are 4-byte and 8-byte IEEE binary floating point, approximate; `NUMBER` is exact decimal. Do not use `BINARY_FLOAT`/`BINARY_DOUBLE` for money or database decimal round-trips. [V: Oracle SQL Reference, BINARY_FLOAT/BINARY_DOUBLE; "Values are stored using decimal precision for NUMBER ... binary floating-point numbers ... cannot represent all values using decimal precision exactly"]

#### A.2.2 Internal `NUMBER` / `VARNUM` byte layout

Oracle documents the internal format in the OCI programmer's guide; this is not merely folklore. [V]

- Maximum internal length: **21 bytes** (`NUMBER`); the `VARNUM` external type prefixes a length byte, so reserve **22 bytes**. The SQL Reference says a `NUMBER` value requires **1 to 22 bytes**, while OCI documents a 21-byte internal maximum and a 22-byte `VARNUM` buffer; treat 22 bytes as the safe client buffer and note that the two documentation pages count differently. [V for both counts; I for the safe-buffer rule]
- First byte: **sign and exponent**. The high-order bit is the sign bit, set for positive and clear for negative. The lower 7 bits are the exponent, a base-100 digit with an offset of 65. To calculate the decimal exponent, add 65 to the base-100 exponent and add another 128 if the number is positive; for negatives, do the same and then invert the bits. Note: OCI documents the sign in the high bit of the **exponent** byte; the first mantissa byte is a biased base-100 digit, not the sign carrier. Informal descriptions that say "sign in the leading digit" should be read against OCI's byte layout.
- Following bytes: **1 to 20 mantissa bytes**, each a base-100 digit in the range 1..100. For positive numbers, add 1 to the digit (value 5 is stored as 6). For negative numbers, subtract the digit from 101 (value -5 is stored as 96). Negative numbers append a byte containing **102** to the data bytes, except when there are 20 mantissa bytes.
- The mantissa is normalized; leading zeros are not stored. Up to 20 data bytes can represent the mantissa, but only **19 are guaranteed accurate**, yielding the documented maximum precision of **38 digits**.

Official OCI `VARNUM` examples:

| Decimal value | Length byte | Exponent byte | Mantissa bytes | Terminator |
|---:|---:|---:|---|---:|
| 2767 | 3 | 194 | 28, 68 | — |
| -2767 | 4 | 61 | 74, 34 | 102 |
| 100000 | 2 | 195 | 11 | — |

Source: Oracle 18c/19c OCI Programmer's Guide, "Data Types", `NUMBER` and `VARNUM`, Table 5-3. [V]

`DUMP` output follows the same `Typ=2` bytes; the following are observed or derived from the documented format, and the exact strings are [O] unless noted:

| Expression | `DUMP` bytes | Decode |
|---|---|---|
| `1` | `193, 2` | exponent 0; digit 1 |
| `123.456` | `194, 2, 24, 46, 61` | exponent 1; digits 1, 23, 45, 60 |
| `-123.456` | `61, 100, 78, 56, 41, 102` | exponent 1; complemented digits 1, 23, 45, 60; terminator |
| `123456.789` | `195, 13, 35, 57, 79, 91` | exponent 2; digits 12, 34, 56, 78, 90 |
| `-123456.789` | `60, 89, 67, 45, 23, 11, 102` | exponent 2; complemented digits 12, 34, 56, 78, 90; terminator |
| `0` | `128` | Oracle zero special byte |

The `1`, `123456.789` and `-123456.789` strings are commonly reported `DUMP` outputs; `123.456` and `-123.456` are derived from the documented encoding. The zero encoding is observed. [O]

#### A.2.3 Trailing zeros and display scale

Oracle `NUMBER` is a **value**, not a fixed-scale quantum. The OCI documentation says the mantissa is normalized and leading zeros are not stored; the OCCI `Number` documentation says "Scale is not preserved when `Number` instances are created." Observed `DUMP` output shows the nuance:

- `DUMP(500.00)` as a literal is `194, 6` — the trailing `.00` is not represented.
- A `NUMBER(30,2)` column containing `500.00` can dump as `194, 6, 1` — an extra base-100 zero digit is retained in that observed case.

Source for the observed distinction: Stack Overflow question/revision 26279538 ("TO_CHAR(number) ..."), plus Oracle forum/AskTom `DUMP` examples; these are [O], not a vendor specification of when a trailing zero digit is retained. [O]

The safe design rule is: **do not try to recover Oracle display scale from the value bytes.** Read the column's `DATA_SCALE` metadata, apply it when formatting/binding, and treat the internal trailing-zero behavior as an implementation detail. This differs from PostgreSQL, which stores `dscale` in the value, and from SQL Server/ASE, which also rely on column metadata. [I]

#### A.2.4 ODP.NET, OCI and the client mapping

- **OCI:** `SQLT_NUM` / data type code 2 returns the 21-byte internal format; `SQLT_VNU` / `VARNUM` returns a length byte plus the same bytes and reserves 22 bytes. `OCINumber` functions operate on this opaque type. [V: Oracle OCI Programmer's Guide, external data types and `NUMBER`/`VARNUM` sections]
- **ODP.NET:** `OracleDecimal` "represents an Oracle `NUMBER` in the database or any Oracle numeric value"; it "can store up to 38 precision, while the .NET `Decimal` data type can only hold up to 28 precision"; accessing `.Value` on an `OracleDecimal` with more than 28 precision throws; to retrieve the actual value use `OracleDecimal.ToString()` or the `BinData` property (the internal Oracle `NUMBER` byte array). [V: Oracle ODP.NET Developer's Guide, `OracleDecimal` structure]
- **Column scale:** ODP.NET/OCI expose the column's scale through the parameter/column metadata; `OracleDecimal` itself is a value container. Do not assume `OracleDecimal` preserves display scale. [V for OracleDecimal capabilities; I for the metadata rule]

#### A.2.5 The 38-versus-40-digit caveat

Oracle's SQL Reference says precision is 1–38, but also says portability is guaranteed for **20 base-100 digits**, equivalent to **39 or 40 decimal digits** depending on the decimal point position. The OCI page says 20 mantissa bytes are possible but only **19** are guaranteed accurate, which yields **38** decimal digits. These statements are from Oracle but use precision/portability differently. [V]

For obo, the documented target should be **38 significant digits** (matching SQL Server/ASE and guaranteed Oracle accuracy). A 128-bit coefficient can hold 38 nines (`10^38 - 1`) but not 39–40 nines. If the user's Oracle data contains raw 20-mantissa-byte values with 39–40 significant digits, a 128-bit coefficient is not lossless; the options are a 256-bit coefficient storage (about 40 bytes per value) or the arbitrary `numeric` tier. This is a decision gate, not a settled fact; test real `DUMP`/`BinData` data before freezing the layout. [I]

#### A.2.6 What Oracle requires of obo

- **Precision:** 38 guaranteed significant digits; 39–40 raw caveat.
- **Exponent:** at least the Oracle range `1e-130 .. 9.99e125`; a signed 16-bit decimal exponent is sufficient for these values and for SQL Server/ASE.
- **Scale:** column scale can be negative to -84 and positive to 127; carry it as signed column metadata, and carry a display scale (`uint16` is enough for Oracle's 0–127 and PostgreSQL's 16383).
- **Sign:** separate.
- **Wire/host:** base-100 exponent+mantissa, sign/complement/terminator; `OracleDecimal.BinData` or OCI `SQLT_VNU`; no float.
- **Display scale:** from column metadata, not value bytes.

### A.3 Sybase ASE and SQL Anywhere

"Sybase" is ambiguous. **SAP Adaptive Server Enterprise (ASE)** and **SAP SQL Anywhere** are different database products with different limits and storage engines; SQL Anywhere is not a variant of ASE. The task's priority list should name the product and version. [V for product distinction; I for the naming recommendation]

#### A.3.1 SAP ASE `numeric` / `decimal`

**Documented facts [V].** ASE's `numeric` and `decimal` are identical except that only `numeric` with scale 0 can be used for an IDENTITY column. Precision is 1–38, default 18; scale is 0–38, default 0, and must be ≤ precision. Values range from `-(10^38 - 1)` to `10^38 - 1`. Storage depends on precision, minimum 2 bytes and maximum 17 bytes; the documented formula is:

```
storage_bytes = ceiling(precision / log10(256)) + 1
```

Examples: `numeric(18,4)` = 9 bytes; `numeric(38,s)` = 17 bytes. Scale does not change the byte count. Source: SAP ASE 16.1 Reference Manual: Building Blocks (document version 1.0, 2025-02-28), §2.1.1.2 "Decimal Datatypes", pages 14–15; SAP Help "Decimal Datatypes" (ASE 16.0.3). [V]

#### A.3.2 ASE client/TDS representation: base-256, not BCD

**Documented facts [V].** SAP's Open Client `CS_NUMERIC` / `CS_DECIMAL` structure is:

```c
typedef struct _cs_numeric {
    CS_BYTE precision;
    CS_BYTE scale;
    CS_BYTE array[CS_MAX_NUMLEN];
} CS_NUMERIC;
typedef CS_NUMERIC CS_DECIMAL;
```

- `precision` legal 1–77, default 18 (client-library limits; note this is wider than ASE server's 1–38).
- `scale` legal 0–77, default 0, must be ≤ precision.
- `array` is a **base-256 representation** of the numeric value. The byte at index 0 is the sign: **0 = positive, 1 = negative**. The remaining bytes 1–n hold the base-256 magnitude. SAP's page says "in little-endian order, with the byte at index 1 being the most significant byte" — a wording that is internally contradictory (index 1 most-significant implies big-endian significance order). Treat the exact byte order as an implementation detail to verify, not as a settled rule. [V for struct/sign; O/I for byte-order interpretation]

Source: SAP ASE SDK 16.1, "Real, Float, Numeric, and Decimal Types"; Sybase Client-Library 15.5 Reference, "Real, float, numeric, and decimal types" (`ctref362.htm`). [V]

**This is the key correction to the task's premise:** SAP's documented `CS_NUMERIC` client representation is **base-256 binary**, not packed BCD, and FreeTDS implements the Sybase TDS numeric form as base-256 as well. Do not model the ASE client/TDS numeric representation as BCD; the on-page format is not documented in the reviewed material and may differ. [V for `CS_NUMERIC` base-256; O for the TDS form; I for the correction]

**TDS 5.0 [V/O].** The Sybase TDS 5.0 Functional Specification (v3.4) defines `TDS_NUMN = 0x6C` (numeric) and `TDS_DECN = 0x6A` (decimal); both have the same format. The token is followed by a data length byte (data only, 1–33), a precision byte (1–77), a scale byte (0–77), then the data (sign + base-256 magnitude). The original vendor PDF link (`sybase.com/content/1013412/tds34.pdf`) now redirects to a marketing page; the token/limit details here are corroborated by FreeTDS source (`src/tds/numeric.c`, `src/tds/data.c` at commit `dda3cd532574bfd0cfd349a946736bfc5d264738`). [V for FreeTDS source; O for the vendor-spec details because the original PDF was not retrievable on 2026-10-28]

**Sign polarity and byte order differ from SQL Server.** SAP `CS_NUMERIC` uses **0 = positive, 1 = negative**; MS-TDS uses **1 = nonnegative, 0 = negative**. FreeTDS's `tds_swap_numeric()` swaps the sign byte and the magnitude bytes when converting Microsoft TDS 7+ values into its Sybase-style internal representation, confirming that the two wire formats differ in both sign polarity and byte order. Do not share one TDS codec between SQL Server and ASE without an explicit conversion/version branch. [V for SAP sign and FreeTDS behavior; O for the exact ASE wire byte order]

The exact ASE **on-page** byte layout is not publicly documented in the material reviewed; SAP documents the storage size formula and the client/TDS representation, not a page format. Do not infer on-page bytes from the client struct without testing. [I]

#### A.3.3 SAP SQL Anywhere `DECIMAL` / `NUMERIC`

**Documented facts [V].** SQL Anywhere's `DECIMAL` (synonym `NUMERIC`, abbreviation `DEC`) has precision **1–127**, default **30**; scale **0–127**, default **6** if both precision and scale are omitted, and **0** if precision is specified but scale is omitted; scale must be ≤ precision. It is exact and preserves accuracy to the least significant digit after arithmetic. The documented storage estimate is:

```
2 + INT(((precision - scale) + 1) / 2) + INT((scale + 1) / 2) bytes
```

where `INT` takes the integer part; storage is based on the value stored, not the column's maximum precision/scale. Source: SAP SQL Anywhere 17 SQL Reference, "DECIMAL Data Type", page 146. [V]

SQL Anywhere's internal/wire byte format is not publicly documented in the reviewed material. Its precision limit (127) is much larger than ASE's (38), so SQL Anywhere values above 38 significant digits cannot fit the fixed decimal and need the arbitrary `numeric` tier. Use a SQL Anywhere-specific adapter; do not assume ASE's TDS format. [V for limits; I for adapter conclusion]

#### A.3.4 What ASE/SQL Anywhere require of obo

- **ASE:** 38 digits, scale 0–38; sign + base-256; sign polarity and byte order differ from SQL Server; precision/scale metadata; 2–17 bytes on storage. Fits the recommended 128-bit fixed decimal.
- **SQL Anywhere:** up to 127 digits; only values with ≤38 significant digits fit the fixed decimal; larger values need arbitrary `numeric`. Separate adapter/format.
- **No packed BCD** for the documented ASE client/TDS numeric representation; do not build a BCD codec without evidence for the target storage path.
- **Byte-order decision gate:** verify ASE sign/byte order against a real ASE 16.x instance before implementation; the SAP wording is contradictory and the FreeTDS behavior is implementation evidence, not a vendor byte-order guarantee.

### A.4 PostgreSQL `NUMERIC` / `DECIMAL`

#### A.4.1 Type, precision, scale and storage

**Documented facts [V].** `numeric` and `decimal` are equivalent. The maximum precision that can be explicitly specified in a type declaration is **1000**. The declared scale may be any value from **-1000 to 1000**; negative scale is allowed beginning in PostgreSQL 15. An unconstrained `numeric` is subject to implementation limits of **up to 131072 digits before the decimal point** and **up to 16383 digits after the decimal point**. Storage is variable; the docs say "two bytes for each group of four decimal digits, plus three to eight bytes overhead" and "numeric values are physically stored without any extra leading or trailing zeroes." Numeric calculations are exact where possible but "very slow compared to the integer types, or to the floating-point types." [V: PostgreSQL current docs, "Numeric Types", Table 8.2 and following text]

`numeric` supports `NaN`, `Infinity` and `-Infinity`; these must be quoted as SQL constants; infinity can only be stored in an unconstrained `numeric` column because it exceeds any finite precision limit. PostgreSQL treats `NaN` values as equal and greater than all non-NaN values. `NaN` is long-standing; `Infinity`/`-Infinity` support was added in PostgreSQL 14. [V: PostgreSQL numeric docs; PostgreSQL 14 release notes, "Add support for Infinity and -Infinity values in the numeric data type (Tom Lane)"]

Rounding: if a value's scale exceeds the column scale, PostgreSQL rounds to the declared scale; if the integer digits exceed `precision - scale`, it errors. "When rounding values, the `numeric` type rounds ties away from zero, while ... `real` and `double precision` ... round ties to the nearest even number." Example: `0.5::numeric` rounds to 1; `-2.5::numeric` rounds to -3. [V: PostgreSQL numeric docs]

Negative-scale examples: `NUMERIC(3,1)` stores -99.9..99.9; `NUMERIC(2,-3)` rounds to the nearest thousand and stores -99000..99000; `NUMERIC(3,5)` rounds to 5 decimals and stores -0.00999..0.00999. [V: PostgreSQL numeric docs]

#### A.4.2 Internal representation

**Documented source facts [V].** PostgreSQL's `numeric` is a base-10000 (`NBASE = 10000`, `DEC_DIGITS = 4`) variable-length value. Each digit is a signed 16-bit `NumericDigit` in 0..9999. The on-disk `NumericData` has a varlena header and a `NumericChoice` union:

- `NumericShort`: a 16-bit header carrying sign, 6-bit display scale, and 7-bit weight. Used for common small values.
- `NumericLong`: a 16-bit `n_sign_dscale` (sign + 14-bit display scale), a 16-bit `n_weight`, then the base-10000 digits.
- `NumericData`: `int32 vl_len_` + the union.

Header flag bits: `NUMERIC_POS = 0x0000`, `NUMERIC_NEG = 0x4000`, `NUMERIC_SHORT = 0x8000`, `NUMERIC_SPECIAL = 0xC000`; special values are `NUMERIC_NAN = 0xC000`, `NUMERIC_PINF = 0xD000`, `NUMERIC_NINF = 0xF000`. The display-scale mask in the long form is `0x3FFF` = **16383**. The maximum stored weight is `PG_INT16_MAX` = 32767, i.e. 32768 base-10000 groups = **131072** decimal digits before the point. [V: `src/backend/utils/adt/numeric.c` at `REL_18_0`, commit `3d6a828938a5fa0444275d3d2f67b64ec3199eb7`, lines 96, 136–202, 236–260; `src/include/utils/numeric.h` lines 32–43]

For arithmetic, `NumericVar` carries `ndigits`, `weight`, `sign`, `dscale` and a `NumericDigit *digits` array. `dscale` is the nominal number of decimal digits after the point and may exceed the number of physically stored fractional digits, "implying that we have suppressed storage of significant trailing zeroes." The packed form is stripped of leading and trailing zero digits; zero has no digits at all. [V: `numeric.c` lines 281–325]

Examples (derived from the base-10000 format; [V] for the format, [I] for the worked digits):

| Value | Sign | Weight | `dscale` | Base-10000 digits |
|---|---:|---:|---:|---|
| `1.2300` | + | 0 | 4 | `[1, 2300]` |
| `12345678901234567890.12345` | + | 4 | 5 | `[1234, 5678, 9012, 3456, 7890, 1234, 5000]` |
| `0.0001` | + | -1 | 4 | `[1]` |
| `-123.456` | - | 0 | 3 | `[123, 4560]` |

The binary send format (`numeric_send`) is: number of digits (int16), weight (int16), sign (int16), display scale (int16), then each base-10000 digit as int16. The receive side reads the same fields. Text format is exact but does not carry the binary header; drivers may expose either. [V: `numeric.c` `numeric_send`/`numeric_recv`, lines 1072–1180]

#### A.4.3 What PostgreSQL requires of obo

- **Typmod values:** `numeric(p,s)` with `p <= 38` and finite values fit the recommended fixed decimal; values outside that need arbitrary `numeric`. `numeric(p,s)` with `p` up to 1000 and unconstrained `numeric` are arbitrary.
- **Display scale:** PostgreSQL stores `dscale` in the value; preserve it. This is the one priority database where the value itself carries display scale, unlike SQL Server/ASE/Oracle column metadata.
- **Special values:** `NaN`, `Infinity`, `-Infinity` do not fit a finite fixed decimal. Map them to the arbitrary `numeric` tier or return an explicit error; do not silently map to zero or a float.
- **Rounding:** PostgreSQL numeric ties away from zero; match that in DB conversion unless the user requests another mode.
- **Wire:** use the binary send/receive format for exact `dscale`/digit round-trip, or text with the same value; never use `double`.

### A.5 What the storage formats imply for obo's language design

#### A.5.1 The common model is exact decimal, not binary float

All four systems use **sign + decimal coefficient + exponent/scale**. SQL Server and ASE use binary coefficients but decimal semantics and a decimal scale; Oracle uses base-100 decimal digits; PostgreSQL uses base-10000 decimal digits. None stores money as binary floating point in its decimal/numeric type. The original report's exact-decimal conclusion stands. [V]

#### A.5.2 Minimum precision and range

| Database | Documented precision | Scale/exponent | Value range | 128-bit coefficient enough? |
|---|---:|---|---|---|
| SQL Server `decimal(38,s)` | 38 | s 0..38 | up to 10^38-1 at s=0; at s=10, 28 integer digits | Yes |
| Oracle `NUMBER` | 38 guaranteed (39–40 raw caveat) | s -84..127; exponent ~1e-130..9.99e125 | ±1e-130 .. 9.99e125 | Yes for 38 digits; 39–40 raw needs 256-bit/numeric |
| Sybase ASE `numeric(38,s)` | 38 | s 0..38 | up to 10^38-1 at s=0 | Yes |
| PostgreSQL `numeric` | 1000 typmod; 131072/16383 unconstrained | s -1000..1000 typmod; dscale in value | arbitrary finite; NaN/Inf | Only for p≤38 finite; otherwise arbitrary tier |

Sources: §A.1–A.4. [V for limits; I for "enough"]

The minimum precision for the priority databases is therefore **38 significant digits**, not 28–29. A 16-byte/96-bit `System.Decimal` cannot represent `decimal(38,0)` maximum (`10^38 - 1`) or `decimal(38,10)` maximum (a 38-digit coefficient). `SqlDecimal` (128-bit/38) is the relevant .NET model, but it has no exponent or display scale, so it is a starting point, not the whole design. [V for limits; I for conclusion]

#### A.5.3 Why an exponent field is required, not just a scale

A scale-only representation `c / 10^s` with `s` 0..38 can represent SQL Server/ASE `decimal(38,s)` exactly, but it cannot represent Oracle values such as `1e125` (would need 126 integer digits) or `1e-130` (would need scale 130). Oracle's `NUMBER` is a floating decimal with a base-100 exponent, and its column scale can be negative. A database-first type that claims to map Oracle exactly must carry an **exponent** as well as a display scale. [V for Oracle range; I for design]

The recommended form is:

```
value = (-1)^sign * coefficient * 10^exponent
```

with a coefficient of up to 38 decimal digits, a signed decimal exponent (int16 covers Oracle's range and SQL Server/ASE), and a separate display scale. The coefficient can be normalized (no trailing zeros) for arithmetic/equality; the display scale preserves `1.20` even when the coefficient is `12` and exponent `-1`. [I]

#### A.5.4 Display scale and trailing zeros

The databases differ in where display scale lives:

- **SQL Server:** column `scale` metadata; the value bytes do not carry it. `decimal(9,2)` value `1` displays `1.00`.
- **Sybase ASE:** column `scale` metadata; same pattern.
- **Oracle:** column `DATA_SCALE` metadata; the internal value is normalized and generally does not preserve literal trailing zeros; observed column storage can retain a zero digit, but this is not a reliable display-scale mechanism.
- **PostgreSQL:** `dscale` in the value; `1.2300` stores `dscale=4` and displays `1.2300`.

Implication: **carry a display scale in the obo value, and also carry column precision/scale metadata in the database adapter.** On fetch, set the value's display scale from PostgreSQL `dscale` or from the column metadata for SQL Server/ASE/Oracle. On bind, use the column precision/scale to validate/round; do not assume the value's display scale equals the column scale. Equality, ordering and hashing must be numeric and ignore display scale; formatting and database binding use it. [I]

#### A.5.5 Column precision/scale metadata and exact binding

The type should not bake in a column's `(p,s)`; those are schema metadata and vary per column. Keep a separate descriptor, for example:

```
DecimalColumn { precision, scale, nullable, dbtype, name }
```

Binding rules:

- `precision` is the maximum significant digits; reject/overflow if the rounded coefficient needs more than `precision`.
- `scale` may be negative (Oracle, PostgreSQL 15+); apply the corresponding power-of-ten rounding to the left of the point.
- Rounding on bind is **explicit**: either the caller chooses a mode, or the adapter uses the target DB's documented mode and records it. Do not silently round a value that does not fit; return `Result`/error for an exactness requirement.
- On fetch, preserve the column's precision/scale and the value's display scale; `NULL` is separate from zero.
- SQL Server treats `(p,s)` pairs as distinct types; obo should not create a distinct static type per pair, but the adapter must pass the pair on every bind.

[I, grounded in the database docs above]

#### A.5.6 NaN, Infinity, signed zero and negative zero

- SQL Server `decimal`/`numeric`: no NaN or Infinity; overflow is an error or `NULL` depending on session settings. [V]
- Sybase ASE `numeric`/`decimal`: no NaN/Infinity documented; overflow is an error. [V for type limits; I for error behavior]
- Oracle `NUMBER`: no NaN; Oracle documents positive/negative infinity representations used to indicate overflow, not normal values. A database adapter should treat them as errors/overflow markers, not as ordinary decimals. [V: OCCI `Number` docs]
- PostgreSQL `numeric`: `NaN`, `Infinity`, `-Infinity`; `NaN` compares equal to itself and greater than all non-NaN values. [V]
- Signed zero: none of the database decimal types has a meaningful negative zero; SQL Server/Oracle/ASE/PostgreSQL numeric use ordinary zero. Do not add signed-zero semantics to the fixed decimal. [V for absence in docs; I for design]

Recommendation: the fixed `decimal` is **finite only**. PostgreSQL special values map to the arbitrary `numeric` tier or return an explicit `Result` error; do not coerce them to zero/float. [I]

#### A.5.7 Arithmetic, intermediate precision and rounding

A 38-digit × 38-digit multiplication produces up to **76 digits**. A 128-bit coefficient (max ~3.4e38) cannot hold the exact product, but a **256-bit intermediate** can (2^256 ~ 1.15e77). Addition/subtraction after exponent alignment can grow to 39 digits and may need rounding back to 38. Division needs guard digits and a defined result scale; a 256-bit numerator over a 128-bit denominator gives enough quotient digits for a 38-digit result, with one final rounding. [I, based on the arithmetic bounds]

Recommended arithmetic policy:

- **Add/sub:** align exponents exactly; result dscale = max(dscale_a, dscale_b); if the coefficient exceeds 38 digits, round once with the requested mode or return inexact/overflow.
- **Multiply:** exact 256-bit product; result dscale = dscale_a + dscale_b (subject to the type's maximum); round once to 38 digits if needed; checked variant reports precision loss.
- **Divide:** explicit result scale (or a documented default); compute with guard digits; round once; division by zero traps/returns `Result`.
- **Remainder:** truncating division semantics, sign follows dividend; define explicitly.
- **Round/quantize:** explicit scale and mode; no global/thread-local context.
- **No double rounding:** round only at the final step. [I]

Rounding modes: half-even, half-away-from-zero, half-toward-zero, toward-zero/truncate, away-from-zero, floor, ceiling, unnecessary/inexact-error. The database defaults are mostly **half-away from zero**:

- SQL Server `ROUND` "breaks ties by rounding half away from zero (also known as commercial rounding)": `ROUND(1.15,1) = 1.2`, `ROUND(-1.15,1) = -1.2`. [V]
- Oracle `ROUND(number)`: for positive n, `FLOOR(n * 10^integer + 0.5) * 10^-integer`; for negative n, `-ROUND(-n, integer)`; negative integer rounds left of the point. This is half-away from zero. Oracle also warns that `ROUND` on a `NUMBER` may differ by 1 in the rounded digit from the same value in floating point because of internal representation; match against `NUMBER`, not binary float. [V: Oracle 19c SQL Reference, `ROUND (number)`]
- PostgreSQL `numeric` rounds ties away from zero. [V]
- Sybase ASE documents commercial rounding for `round` in its SQLScript/compatible material; SAP IQ's compatible `ROUND` is half-away when `ROUND_TO_EVEN` is off. Treat ASE tie behavior as [V for commercial rounding, O for exact wording/version]. [V/O]
- .NET `System.Decimal` is the outlier: half-even. [V]

**Revised recommendation:** use **half-away from zero for database scale conversion and for the default `round`/`quantize`**, because the main use case is DB round-trip and the DBs use commercial rounding; keep half-even as an explicit mode for accounting policies that prefer it. Any scale-changing operation must record its mode. Division should not have a hidden rounding default; require an explicit scale/mode or use a documented checked default. This reverses the original report's half-even default, which was chosen for .NET/Python compatibility. [I, based on the cited DB behavior]

#### A.5.8 Value type versus heap type, and the two-tier design

The fixed decimal must be a **value type**: 24 bytes, unowned, copied like `int`, no allocation per operation. That is the only way it can work in obo's current single-ownership, no-GC model and be a natural numeric type. A record-based or heap `BigDecimal` cannot be the primary type: every value/result would allocate. [I]

The arbitrary `numeric` tier is naturally a **heap/owned** type: a variable-length coefficient (base-10^9 limbs or base-10000 digits), sign, exponent/weight, and display scale. It is needed only for PostgreSQL unconstrained/large values, >38-digit values, Oracle raw 39–40-digit values if required, and special values. Do not force it into the fixed 24-byte layout. [I]

#### A.5.9 ABI and layout implications

The revised value is 24 bytes (3 × 8-byte words), or 32 bytes if that is simpler to align:

```
word 0: coefficient bits 0..63
word 1: coefficient bits 64..127
word 2: bits 0..15   int16 exponent (two's complement)
        bits 16..31  uint16 display scale
        bits 32..39  sign/flags (bit 0 sign; other bits reserved/zero)
        bits 40..63  padding/zero
```

A 24-byte value with 8-byte alignment fits a 3-word copy; a 32-byte value is easier for some SIMD/ABI paths but wastes 8 bytes. Start with 24 bytes, measure, and do not assume JIT-style register classification: obo's compiler emits its own layout. [I]

Implications by location (these update the original 16-byte sketch):

- **Locals:** 24-byte stack slots, 8-byte aligned; not one 8-byte slot.
- **Parameters:** each decimal parameter occupies 24 bytes in the parameter area; offsets are `16 + 24*(n-1-i)` with alignment, not `16 + 8*(n-1-i)`.
- **Returns:** a 24-byte return does not fit `RAX`. Use a hidden destination pointer supplied by the caller (simplest, works for all helpers) or a multi-register convention (`RAX:RDX:RCX`). A hidden pointer must copy into a local if the callee can assign to the parameter; document the aliasing rule.
- **Record fields:** offsets computed from a size/alignment function, not `8 * index`; a 24-byte field aligns to 8; descriptors remain pointer-free (decimal is unowned), but record size/offsets change.
- **Arrays:** new array kind with `esize = 24`; `rt_append` copies 24 bytes, `rt_truncate` zeroes 24-byte elements, element load/store moves three words, `for x in xs` copies 24 bytes. The array block remains pointer-free.
- **Globals:** `gsize` advances by 24 with alignment; all-zero bits must be a valid decimal (`coefficient 0`, exponent 0, dscale 0, sign 0).
- **Constants:** 24 bytes in rodata; `.sym` constants need 48 hex digits (24 bytes) or an equivalent encoding; `Sym.value` (currently an `int`) needs an offset/index or a new field; `SYM_ABI`/format version bumps.
- **`Result[decimal]`:** ok payload is 24 bytes; descriptor size 24 and three stores; `try`/`match` copy 24 bytes; err variant remains unchanged. This makes `Result[decimal]` usable for parse/checked arithmetic at one heap cell per success.
- **Optionals:** `?decimal` is not representable as a zero word (zero is a valid decimal). Do not support `?decimal`; use `Result[decimal]` or a record with a flag.
- **Ownership:** `isOwned(decimal) = false`, `isRef(decimal) = false`; the ownership checker does not track decimal values. `Result[decimal]`/arrays/records containing decimal are owned as containers.

All of these are [I] based on the original report's verified one-word assumptions; the exact offsets/return convention are design choices to prototype. [V for current layout; I for revised layout]

#### A.5.10 Database wire/API implications

| Database | Wire/host bytes | Conversion obo needs | Client model / metadata |
|---|---|---|---|
| SQL Server | TDS: sign byte (0 neg/1 nonneg) + little-endian 4/8/12/16-byte magnitude; TYPE_INFO precision/scale; on-page observed same sign + LE magnitude | Encode/decode width tier; strip/append sign; ensure coefficient×10^scale fits tier; preserve p/s | `SqlDecimal` (4×uint, 38) or direct TDS; `GetSqlDecimal`, not `GetDecimal`, for lossless |
| Oracle | VARNUM: exponent/sign byte + 1–20 base-100 mantissa bytes; positive digit+1; negative 101-digit + 102 terminator; 21 internal/22 VARNUM | Base-100 encode/decode; exponent bias; negative complement/terminator; column scale metadata | `OracleDecimal.BinData`/OCI `SQLT_VNU`; `OracleDecimal.Value` loses >28 digits |
| Sybase ASE | TDS 5.0 `TDS_NUMN`/`TDS_DECN`: length/precision/scale + sign (0 pos/1 neg) + base-256 magnitude; client `CS_NUMERIC` p1–77 | Base-256 encode/decode; sign polarity opposite SQL Server; verify byte order | `CS_NUMERIC`; ASE server p≤38; separate codec |
| SQL Anywhere | Internal/wire not public in reviewed docs; storage formula only | Use a SQL Anywhere-specific driver; values >38 digits need arbitrary numeric | Column precision/scale metadata; no ASE assumption |
| PostgreSQL | Binary: ndigits/weight/sign/dscale + int16 base-10000 digits; text exact | Base-10000 encode/decode; dscale; special values; typmod | libpq binary/text; preserve dscale; map NaN/Inf to numeric/error |

Rules for all adapters:

- **No float conversions.** Never pass a decimal through `double`/`float`; database drivers may expose strings, structs, or 38-digit types, but a float path loses exactness.
- **Preserve column metadata.** Precision, scale, nullable and type name must survive the round-trip; they determine rounding/validation and display.
- **Explicit rounding only.** A bind that must reduce scale takes a mode; a fetch that exposes more digits than the column allows is a bug.
- **No helper process.** Conversion code is C in the runtime/driver layer, called in-process by obo; the decimal type itself has no dependency on ODBC/OCI/libpq/TDS.
- **Golden byte vectors.** Test TDS/VARNUM/PostgreSQL binary/Sybase bytes against known values, not just value equality.

[I, grounded in the cited formats]

### A.6 Representation options and revised recommendation

#### A.6.1 Options considered

| Option | Layout / size | Precision and range | Pros | Cons | Verdict |
|---|---|---|---|---|---|
| **1. .NET 96-bit 16-byte** | 16 B; 96-bit coefficient + sign + scale 0–28 | 28–29 digits; ±7.92e28 | Compact; proven; allocation-free | Cannot hold SQL Server/ASE 38-digit values; no exponent; Oracle range impossible | Background only; superseded |
| **2. Scale-only 128-bit** | 20–24 B; 128-bit coefficient + sign + scale 0–38 | 38 digits; scale 0–38; max 10^38-1; min 1e-38 | Simple; exact SQL Server/ASE decimal(38,s) | No Oracle exponent/range/negative scale; loses Oracle 1e125/1e-130 | Not sufficient for Oracle |
| **3. 128-bit coefficient + exponent + dscale (recommended)** | 24 B (16 coeff + int16 exp + uint16 dscale + sign/flags) | 38 digits; exponent int16; dscale uint16; covers Oracle 1e-130..9.99e125 and PG ≤38-digit dscale | Compact; value semantics; covers priority DBs; 256-bit intermediates | More ABI than 16 B; needs exponent/dscale arithmetic; 39–40 Oracle raw not covered | **Recommended primary** |
| **4. 256-bit coefficient + exponent + dscale** | ~40 B (32 coeff + metadata) | Up to ~77 digits (2^256 ≈ 1.15e77); enough for 76-digit products and Oracle 39–40 raw | Exact stored 76-digit intermediates; future headroom | ~1.7× value size/ABI; 512-bit for full 256×256; overkill for finance amounts | Conditional upgrade |
| **5. Base-10^9 limb coefficient** | 5×32-bit = 20 B coeff + metadata (~24–32 B) | 45 digits; 10-limb intermediates (90 digits) | Easy decimal rounding/formatting; 45-digit headroom; 32-bit limb code | Conversion to base-256/100/10000 by division; 5-limb add/sub loops; no direct DB wire base | Good for arbitrary `numeric`; not primary fixed |
| **6. Variable-length/arbitrary with inline small-value** | Inline buffer + heap overflow | Arbitrary | One type for everything | ABI/ownership complexity; value size/escape; slow common path | Later/never as primary |
| **7. Two-tier fixed `decimal` + arbitrary `numeric`** | Option 3 now; heap numeric later | 38 digits fixed; arbitrary second tier | Best fit for priority DBs now; PostgreSQL later; clean value/heap split | Two types/conversions; more API surface | **Recommended path** |

[I, with [V] limits from §A.1–A.4]

#### A.6.2 Recommended primary design

**Use a 24-byte fixed `decimal` value type:**

- **Coefficient:** 128-bit unsigned integer, 0..10^38-1; 38 significant digits; normalized (no trailing zeros) for canonical values.
- **Sign:** one bit/byte.
- **Exponent:** signed 16-bit decimal exponent; value = `(-1)^sign * coefficient * 10^exponent`. This covers Oracle's `1e-130..9.99e125`, SQL Server/ASE scale 0..38, and PostgreSQL values with ≤38 significant digits and reasonable exponents.
- **Display scale:** uint16, 0..65535; enough for Oracle 0..127 and PostgreSQL dscale 0..16383. It is presentation/quantum metadata, not an invariant of the numeric value.
- **Arithmetic:** 256-bit intermediates for 38×38 products (up to 76 digits); add/sub align exponents; division uses guard digits and one final rounding; overflow/inexact errors are explicit.
- **Semantics:** finite only; no NaN/Infinity/signed zero; numeric equality/ordering/hash ignore display scale; formatting/binding use display scale.
- **ABI:** 24-byte value, value semantics, hidden destination pointer for returns or a three-register convention; fields/arrays/globals/params/returns/constants/`Result` all updated; `.sym` ABI bump.
- **Metadata:** a separate `DecimalColumn` descriptor carries precision, scale (signed), nullable and DB type; binding/rounding is explicit and DB-aware.

Why this over the alternatives:

- **Over .NET 96-bit:** it can hold all documented SQL Server/ASE/Oracle 38-digit values and Oracle's exponent range, while preserving display scale.
- **Over scale-only 128-bit:** Oracle's exponent/negative scale/1e125 values are representable without a second type.
- **Over 256-bit storage:** 38 digits is the documented priority limit and finance amounts do not need more than 38 significant digits; doubling every value to ~40 bytes would tax every local/field/array for a rare case. 256-bit arithmetic intermediates give exact products without doubling storage.
- **Over base-10^9 as the primary:** SQL Server/ASE are binary-coefficient databases; a 128-bit binary coefficient is compact and maps naturally to their magnitudes. Base-10^9 is better reserved for the arbitrary `numeric` tier, where decimal rounding/formatting dominates and variable length is expected.
- **Over variable-length:** a 24-byte value keeps the common path allocation-free and ABI-predictable, which is the original report's strongest argument for a compiler primitive.

[I]

**Conditional 256-bit upgrade.** If real Oracle data contains raw 20-mantissa-byte values with 39–40 significant digits and those must round-trip exactly, either (a) widen the fixed decimal's coefficient to 256 bits (about 40 bytes/value; 512-bit intermediates for full generality), or (b) store those values in the arbitrary `numeric` tier and accept an explicit conversion at the Oracle boundary. Decide after measuring real data, not from the documented 38-digit limit alone. [I; see §A.2.5]

#### A.6.3 Two-tier path

- **Tier 1, `decimal` (after floats):** the 24-byte fixed type above. It is the workhorse for SQL Server, ASE, Oracle documented values, and PostgreSQL `numeric(p,s)` with p ≤ 38.
- **Tier 2, `numeric` (later):** arbitrary precision, heap/owned, base-10^9 limbs (or PostgreSQL-style base-10000 digits), signed exponent/weight, display scale, `NaN`/`±Infinity`. It handles PostgreSQL unconstrained columns, >38-digit values, Oracle 39–40 raw values if needed, and scientific/aggregated quantities.
- **Conversion:** `decimal` -> `numeric` is exact; `numeric` -> `decimal` is explicit and returns `Result`/error if the value does not fit 38 digits or the exponent/dscale range. Do not silently truncate.

[I]

#### A.6.4 Scale/exponent semantics in detail

| Question | Recommendation | Rationale |
|---|---|---|
| Value form | `(-1)^sign * coefficient * 10^exponent`; coefficient normalized; display scale separate | Covers Oracle exponent range and SQL/ASE scale; avoids redundant trailing zeros |
| Equality/ordering/hash | Numeric; ignore display scale and representation | `1.0 == 1.00`; dictionary keys behave correctly |
| Display/trailing zeros | Preserve `dscale`; format with exactly `dscale` fractional digits; explicit `normalize` strips zeros | Audit/reporting need `1.20`; keys/serialization need canonical form |
| Addition/subtraction | Align exponents; result dscale = max(inputs); exact coefficient; round once to 38 digits | Matches SQL/ASE/PostgreSQL display-scale conventions |
| Multiplication | Result dscale = sum of input dscales (cap at type max); exact 256-bit product; round once if >38 digits | `1.20 * 1.2 = 1.440`; exact intermediate |
| Division | Explicit result scale/mode; compute guard digits; round once; div0 error | Avoid hidden low-precision defaults (e.g. `shopspring`'s 16) |
| Round/quantize | Explicit scale and mode; `UNNECESSARY` errors if digits discarded | Validation/audit |
| Negative scale | Column scale may be negative; value rounds left of point at bind/fetch; `dscale` remains ≥0 | Oracle/PG negative scale |
| Column p/s | Separate metadata; bind validates/rounds; fetch sets dscale | SQL Server treats `(p,s)` as type; schema varies |
| Overflow | Normalized coefficient > 38 significant digits (after stripping trailing zeros) -> overflow/inexact error; exponent out of int16 -> overflow | No silent wrap/saturation; `10^38` normalizes to coefficient 1, exponent 38 and is not an overflow of the type (a SQL Server column may still reject it) |
| Underflow | Exponent below int16 -> overflow/error; otherwise only via explicit rounding to zero, recording inexact; no separate underflow value | Matches decimal semantics |
| NaN/Inf | Not in fixed `decimal`; `numeric` tier or explicit error | DB differences |
| Signed zero | None; zero canonical positive | Avoids serialization ambiguity |
| Currency | Not in `decimal`; separate `Money` with currency/scale policy | ISO 4217 minor units vary |

[I, grounded in §A.1–A.5]

#### A.6.5 Revised staging and effort

**M0 — format/semantics oracles (no compiler or DB changes):**
- Python `decimal`/custom oracle for 38-digit arithmetic, exponent/dscale, rounding modes, overflow.
- Golden byte-format vectors for TDS decimal(38,0)/(38,10), Oracle VARNUM, PostgreSQL binary, and provisional Sybase ASE; no DB products required to encode/decode published formats.
- Boundary tables from §A.8.

**M1 — scalar fixed decimal:** new 24-byte `decimal` kind; literals; locals/parameters/returns; `+ - * /`, unary minus, comparisons; `int`/`string` conversions; parse/format; `round`; traps/checked variants. This derisks the 24-byte ABI and 128-bit arithmetic.

**M2 — containers and symbols:** record fields, arrays, globals, constants, `Result[decimal]`, `.sym` type/const form (48 hex digits), ABI/format bumps, module cross-checks, DB metadata descriptor type.

**M3 — database adapters (in-process C, no helper):** SQL Server TDS first (priority, best-documented); Oracle OCI/VARNUM; Sybase ASE TDS with byte-order verification; PostgreSQL typmod adapter (binary/text). Golden and integration tests per DB.

**M4 — later:** arbitrary `numeric` (PostgreSQL/unconstrained/39–40 Oracle), float interop after floats, Money/currency library.

Effort is larger than the original estimate: 24-byte ABI (not 16), exponent/dscale arithmetic, 256-bit intermediates, and four priority database adapters (SQL Server, Oracle, ASE, PostgreSQL; SQL Anywhere later). It is **larger than `Result` or I/O and comparable to or larger than the module work**, with a large byte-format/arithmetic test matrix. It still belongs after floats, and the DB adapters should follow the decimal language feature rather than precede it. [I]

### A.7 Database compatibility matrix

| Product / version | SQL type | Precision | Scale | Magnitude / exponent | Storage / wire | Client model | Obo mapping | Notes |
|---|---|---|---:|---|---|---|---|---|
| SQL Server 2016+/2022/2025 | `decimal`/`numeric` | 1–38 (default 18) | 0–p (default 0) | Binary coefficient / 10^scale | 5/9/13/17 B; TDS sign + LE magnitude; observed on-page same | `System.Decimal` (28) or `SqlDecimal` (38) | Fixed `decimal` (38 digits, dscale) | Full fit; use `SqlDecimal`/TDS, not float |
| Oracle 19c/23ai/26ai | `NUMBER(p,s)` | 1–38 | -84..127 | Base-100 coefficient × 100^e; range 1e-130..9.99e125 | 22 B SQL ref; 21 B internal; 22 B VARNUM; exponent+mantissa | `OracleDecimal` (38); `Value` loses >28; `BinData` | Fixed `decimal` for 38 digits; `numeric` for 39–40 raw | Exponent/dscale from column; negative scale |
| Sybase ASE 16.x | `numeric`/`decimal` | 1–38 (default 18) | 0–38 (default 0) | Base-256 magnitude / 10^scale | 2–17 B; TDS 5.0 length/precision/scale + sign (0 pos/1 neg) + base-256; on-page undocumented | `CS_NUMERIC` (client p1–77) | Fixed `decimal` (38 digits, dscale) | Client/TDS not BCD; sign/byte order differ from SQL Server |
| SQL Anywhere 17 | `DECIMAL`/`NUMERIC` | 1–127 (default 30) | 0–127 (default 6/0) | Exact decimal; internal format not public | Storage estimate formula; separate engine/protocol | SQL Anywhere driver | Fixed `decimal` only if ≤38 digits; else `numeric` | Do not conflate with ASE |
| PostgreSQL 14–18 | `numeric`/`decimal` | 1000 typmod; 131072 unconstrained | -1000..1000 typmod; dscale in value | Base-10000 digits; weight/dscale | Variable; binary ndigits/weight/sign/dscale/digits | libpq binary/text; string/decimal | Fixed `decimal` if p≤38 finite; else `numeric` | Infinity since PG14; NaN long-standing; ties away from zero |
| Oracle `BINARY_FLOAT`/`BINARY_DOUBLE` (contrast) | binary float | 24/53 bits | — | IEEE 754 binary32/binary64 | 4/8 B | float/double | **Not** `decimal`; explicit float only | Approximate; never for money/decimal round-trip |

Sources: §A.1–A.4 and §8. [V for documented limits; O for observed on-page/sign/byte-order details; I for mapping]

### A.8 Boundary round-trip test plan

No database product is run for this research; this is the implementation test plan. [I]

#### A.8.1 Unit/golden tests (no DB connection)

1. **Arithmetic vectors:** 38-digit max/min, 10^k boundaries, scale 0/10/38, 38×38 products up to 76 digits, exact/inexact division, all rounding modes at midpoints, overflow/underflow, zero, negative values.
2. **Format vectors:**
   - SQL Server TDS/on-page: `decimal(38,0)` max/min, `decimal(38,10)` max/min, `decimal(9,2)` `123.45`/`-123.45`/`1.00`/`0.00`, zero, and the sample coefficient in §A.1.5.
   - Oracle VARNUM: `1`, `2767`, `-2767`, `100000`, `123.456`, `-123.456`, `123456.789`, `-123456.789`, zero (`128`), max/min 38-digit values; negative terminator.
   - PostgreSQL binary: `1.2300`, `0.0001`, `-123.456`, `12345678901234567890.12345`, zero (no digits), NaN/Inf; dscale/weight.
   - ASE CS_NUMERIC/TDS: sign `0/1`, base-256 magnitude, precision/scale; provisional byte order until verified against ASE.
3. **Parse/format:** 38-digit literals, exponent notation, display scale preservation, `1.20` vs `1.2`, `1.2300`, max/min, no float path.
4. **Equality/hash:** `1.0` vs `1.00` equal, hash equal; display scale differs; canonical `normalize`.
5. **ABI/layout:** record/array/global/parameter/return/`Result[decimal]`/`.sym` constant with 24-byte values; alignment; copy/zero; memory budget.
6. **Mutation tests:** off-by-one sign, exponent, dscale, length tier, byte order, terminator; wrong rounding tie direction; missing overflow.

#### A.8.2 Integration tests (when a DB is available)

Schema cases:

- SQL Server: `decimal(38,0)`, `decimal(38,10)`, `decimal(38,38)`, `decimal(1,0)`, `decimal(1,1)`, `decimal(9,2)`.
- Oracle: `NUMBER(38,0)`, `NUMBER(38,10)`, `NUMBER(6,-2)`, `NUMBER(4,5)`, unconstrained `NUMBER`.
- Sybase ASE: `numeric(38,0)`, `numeric(38,10)`, `numeric(18,4)`.
- SQL Anywhere: `DECIMAL(38,10)`, `DECIMAL(127,0)`, `DECIMAL(127,127)`.
- PostgreSQL: `NUMERIC(38,10)`, `NUMERIC(2,-3)`, `NUMERIC(3,5)`, unconstrained `numeric`.

Values:

- `0`, `±1`, `±(10^38-1)`, `±(10^28 - 10^-10)`, `1e-38`, `1e-10`, `1e125`, `9.99...e125` (Oracle), `1e-130` (Oracle), `123.89` (`NUMBER(6,-2)`), `.000127` (`NUMBER(4,5)`), `1.20` vs `1.2`, `1.2300`, `0.0001`, `-123.456`, `NULL`, PostgreSQL `NaN`/`Infinity`/`-Infinity`.
- 39–40 significant-digit Oracle values if present (raw 20-mantissa-byte data).

Round-trip checks:

1. Write from obo -> DB -> read back; compare numeric value, sign, display scale/column scale, precision/scale metadata, and bytes (TDS/VARNUM/PG binary) where available.
2. Read DB value -> obo -> write back -> compare bytes/value.
3. No float conversion anywhere in the path.
4. Overflow/rounding: values exceeding column precision/scale; midpoint ties; DB-specific session settings (SQL Server `ARITHABORT`/`ANSI_WARNINGS`); Oracle precision error vs scale rounding; PostgreSQL integer-digit error vs fraction rounding.
5. NULL: DB NULL -> obo null/`Result`/nullable wrapper -> DB NULL; never zero.
6. Metadata: SQL Server `sys.columns.precision/scale/max_length`; Oracle `ALL_TAB_COLUMNS.DATA_PRECISION/DATA_SCALE`; PostgreSQL `atttypmod`/`format_type`; Sybase catalog precision/scale; SQL Anywhere catalog.
7. Fuzz: random 38-digit coefficients, exponents, dscales; encode/decode each format; compare to Python oracle; assert no silent precision loss.

Acceptance: exact round-trip for all priority DB boundary values; documented exceptions only (Oracle 39–40 raw, PostgreSQL >38/special, SQL Anywhere >38); no silent rounding, no float, no helper process. [I]

### A.9 Open questions and decision gates

| Question | Why it matters | Recommended action |
|---|---|---|
| Do real Oracle values use 20 mantissa bytes / 39–40 significant digits? | A 128-bit coefficient cannot hold 39–40 digits | Measure `DUMP`/`BinData` on real data; if yes, 256-bit coefficient or `numeric` tier |
| Exact ASE TDS 5.0 sign/byte order | SAP wording is contradictory; wrong order corrupts values | Test against a real ASE 16.x instance before coding; isolate codec |
| Which Sybase product/version? | ASE p38 vs SQL Anywhere p127; different wire | Name product/version in requirements; separate adapters |
| Oracle column scale for expressions/unconstrained `NUMBER` | Value does not carry display scale | Test OCI/ODP.NET metadata; define fallback (dscale from value text or 0) |
| SQL Server implicit-conversion tie rule vs `ROUND` | `ROUND` is half-away; implicit `CAST` rounding tie rule is not explicit | Test `CAST` midpoints under target compatibility level; match server |
| PostgreSQL binary vs text | Binary preserves dscale/digits exactly; drivers may expose text more easily | Prefer binary; fall back to text only with exact parse and dscale handling |
| Default rounding policy | DB-compatible half-away vs accounting half-even | Make mode explicit; default half-away for DB/`round`; half-even option |
| 24-byte vs 32-byte value, hidden pointer vs registers | ABI cost/risk | Prototype 24-byte, hidden destination pointer; measure code size/performance |
| Display scale in value vs metadata | Oracle/ASE/SQL Server rely on metadata; PG stores dscale | Carry dscale; allow column metadata to override/set it |
| Oracle ±Infinity overflow markers | Not normal values; mapping to fixed decimal would be wrong | Return overflow/error; map to `numeric` special only if requested |
| Decimal literal >38 digits | Silent rounding would hide data loss | Reject/`Result` error by default; explicit rounded conversion |
| Money/currency | ISO 4217 minor units vary | Separate `Money` type/policy, not `decimal` |

[I]

---

## 2. .NET `System.Decimal` precisely

**Background (pre-database-first).** This section describes .NET `System.Decimal` precisely and is retained for comparison; it is not the revised target. The database-first target is in §A. [I]

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
| **SQL Server `decimal(38)`** | 5, 9, 13 or 17 B by precision; sign + little-endian binary magnitude; fixed precision/scale | Up to 38 digits; fixed p/s | Rounds on conversion; DB engine policy | None | Database storage/compute | Good fixed-schema money; not a language value type |
| **Oracle `NUMBER`** | 1–22 B; base-100 exponent + 1–20 mantissa bytes | 38 digits guaranteed; scale -84..127; ~1e-130..9.99e125 | DB `ROUND` half-away; column scale rounds | No NaN; ±Infinity overflow markers | Database storage/compute | Excellent DB-first reference; needs exponent + dscale |
| **Sybase ASE `numeric`/`decimal`** | 2–17 B by precision; sign + base-256 magnitude | 38 digits; scale 0..38 | Commercial/half-away `round` | None documented | Database storage/compute | Good; sign/byte order differs from SQL Server; not BCD |
| **SQL Anywhere `DECIMAL`** | Variable; documented storage estimate | 127 digits; scale 0..127 | DB engine | None documented | Database storage/compute | Separate engine; >38 digits needs arbitrary numeric |
| **PostgreSQL `numeric`** | Variable-length; base-10000 digits | Up to 131072 digits before / 16383 after decimal; dscale in value | Ties away from zero in numeric; DB engine | `NaN`, `Infinity`, `-Infinity` | Heap; docs: "very slow compared to integer or floating-point" | Excellent precision; database, not an in-process value |
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

**Database decimal types (2026-10-28 update) [V].** SQL Server fixes precision and scale at declaration, supports up to 38 digits, stores 5/9/13/17 bytes depending on precision, and uses a sign byte plus a little-endian binary magnitude. Oracle `NUMBER` has 38 guaranteed digits, scale -84..127, a base-100 exponent/mantissa, and a range of about 1e-130..9.99e125. Sybase ASE `numeric`/`decimal` has 38 digits, scale 0..38, and a sign byte plus a base-256 magnitude (not BCD); SQL Anywhere is a separate product with 127 digits. PostgreSQL `numeric` is arbitrary precision (up to 131072 digits before and 16383 after the decimal point), exact where possible, variable-length, stores display scale in the value, supports NaN/Infinity in unconstrained columns, and is documented as "very slow compared to the integer types, or to the floating-point types." These are not just schema-level references: they are the target of the database-first design in §A. The relevant fixed width is 38 digits, not the 16-byte/28-29-digit .NET `System.Decimal`; a `SqlDecimal`-style 128-bit coefficient is the starting point, with an exponent and display scale added.

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
- **Runtime calls** use the C ABI, at most four arguments, return in `RAX` (`rtCall`, lines 2334-2392). A 24-byte return or a fifth argument needs a new convention (hidden result pointer or multi-register return).
- **Constants** in source are primitive: `constDecl` requires a compile-time `const` item and stores `s.value = x.val` (`src/obo.obo` 8233-8243). In `.sym`, constants are only `int`/`byte` (16 hex digits), `bool`, or `string` (`hex "..."`) (`appConst` 9020-9045; `symConstDecl` 9748-9785).
- **`.sym` type expressions** print a built-in by `t.name` (`appTypeExpr`, 8924-8952) and read built-ins by name (`symParseType`, 9384-9431). A built-in `decimal` is easy to add syntactically, but the ABI version (`SYM_ABI = 1`) must change if layout/calling convention changes.
- **Optionals are reference-only**: `parseType` rejects `?T` unless `nonNull(T)` (record/closed/array/handle/result); `?decimal` would be rejected as "only reference types can be optional" unless that rule changes.
- **No methods, interfaces or lambdas yet** (roadmap phases 7-9 in `README.md`). A library decimal cannot have `a + b` or `a.round(2)` syntax; it would be `dec::add(a, b)` and allocation-returning functions.
- **The compiler is self-hosting and single-pass, no AST/IR.** Adding a type means changing the scanner, the combined parser/typechecker, the direct x86-64 emitter, the `.sym` reader/writer and the runtime; there is no IR in which to lower a 24-byte value. `make promote` requires stage 2 == stage 3 (`Makefile`).

### 4.2 Design options

| Option | What it is | Pros | Cons | Verdict |
|---|---|---|---|---|
| **A. Built-in 16-byte .NET-style** | New primitive `decimal`, 16-byte value, 96-bit coefficient + scale/sign; runtime C helpers; operators | True value semantics; no heap; compact; proven | Cannot hold SQL Server/ASE 38-digit values or Oracle exponent range; 96-bit coefficient; 16-byte ABI | **Superseded for DB-first; retained as .NET background** |
| **B. Library type from two 64-bit words / record** | `ref record { lo: int; hi: int; scale: int }` or `{ coeff: int; scale: int }`; functions | No compiler/runtime/ABI/seed change; can start now; semantics prototype | Heap allocation per value/result; single-ownership moves; no operators; no `.sym` constants; not a value type; 64-bit coefficient only unless manual 128-bit | Prototype only; not final |
| **B'. Library fixed-point over `int64`** | One `int`, implicit scale (cents/mills/micros), checked functions | Simplest; fastest; no compiler change; exact for chosen scale | 18-19 digits; fixed scale; no scale metadata; overflow wraps unless checked; not general finance | Interim for a narrow tool |
| **C. Built-in 64-bit fixed point** | New 8-byte primitive with an implicit/encoded scale | Simpler ABI than 24 bytes; fast | Range/precision too small; currency scales vary; still a compiler primitive | Not recommended as the general decimal; maybe a separate `money` later |
| **D. Arbitrary precision** | Library `[]int` limbs + scale; big integer algorithms | Unlimited precision | Allocation/ownership per op; no value semantics; large implementation; no operators; `.sym`/const complexity | Later second tier (`numeric`), recommended for PostgreSQL/unconstrained |
| **E. IEEE decimal128 built-in** | 16-byte BID/DPD, 34 digits, NaN/Inf | 34 digits; IEEE/DB interop | 113-bit coefficient, special values, cohorts/quantum, more complex arithmetic; still software | Only if decimal128 interop is a requirement |
| **F. Built-in 24-byte DB-first decimal (recommended)** | New primitive `decimal`, 24-byte value: 128-bit coefficient + sign + int16 exponent + uint16 dscale; 256-bit intermediates | 38 digits; covers SQL Server/ASE/ documented Oracle and PG p≤38; value semantics; no heap; display scale | Pervasive 24-byte layout/ABI/codegen change; exponent/dscale arithmetic; software; high test/seed risk | **Recommended after floats** |
| **G. Two-tier fixed `decimal` + arbitrary `numeric`** | Option F now; heap arbitrary numeric later | Covers priority DBs now and PostgreSQL/39–40-digit values later; clean value/heap split | Two types/conversions; larger API surface | **Recommended long-term path** |

**Why a compiler primitive rather than a library type.** The language has no value records. A record is a heap-allocated reference. A library decimal would make every `amount` a pointer, every arithmetic result a fresh object, every assignment a move, and every temporary subject to the ownership checker's rules. It would also be impossible to use as a `const` (records are not constants) or in a `.sym` constant. A built-in decimal makes the value unowned and copyable, exactly like `int`; `isOwned(decimal)` can return false, so the ownership analysis does not need to track it. This is the single strongest reason to make decimal a primitive. [V for no value records/records-ref; I for design conclusion]

**Why not 64-bit fixed point as the language type.** It is tempting because obo already has 64-bit `int` and fast x86 arithmetic. But it has no scale identity, only 18-19 total digits, and ISO currency scales vary. A fixed-point library is a good interim for a specific finance tool; it is not a general `decimal` that the language can offer. [V for ranges/minor units; I for conclusion]

### 4.3 How a built-in decimal would fit (24-byte DB-first sketch)

**Superseded 2026-10-28.** The original option A sketch was 16 bytes with a 96-bit coefficient and scale 0–28. The database-first addendum revises it to **24 bytes: 128-bit coefficient + sign + int16 decimal exponent + uint16 display scale**; the rest of this subsection keeps the original ABI analysis but uses the revised sizes. §A.6 is authoritative for the representation; this section maps it onto obo's current one-word compiler/runtime. [I]

**Representation.** A 24-byte value, for example three 64-bit words:

- words 0–1: the 128-bit unsigned coefficient, little-endian limbs;
- word 2: bits 0–15 signed decimal exponent (two's complement), bits 16–31 display scale (uint16), bits 32–39 sign/flags (bit 0 sign, other bits reserved/zero), bits 40–63 zero padding.

The value is `(-1)^sign * coefficient * 10^exponent`; the display scale is presentation metadata. A 128-bit coefficient holds `10^38 - 1` with room to spare, and an int16 exponent covers Oracle's range and SQL Server/ASE scale. The exact bit layout is a design choice; a 32-byte value (four words, with 8 bytes of metadata/padding) is an acceptable simpler-alignment alternative. [I]

**Type identity and `.sym`.** Add a singleton `tDecimal` with `kind = "decimal"`, `name = "decimal"`, `owner = ""`, `uid = 0`. `same` returns true only for that singleton; `assignable` allows only `decimal` to `decimal`; `isOwned` false; `isRef` false; `nonNull` false (no `?decimal` initially). `appTypeExpr` prints `decimal`; `symParseType` recognizes `decimal` alongside `int`/`byte`/`bool`/`string`. Because it is a built-in primitive, its identity is stable across modules without a `uid` or `.sym` type definition. The `.sym` ABI version must be bumped when the layout changes; `obosym` format may also need a bump if decimal constants add a grammar form. [V for mechanism; I for the new kind]

**Layout and ABI.** This is the largest change. Every place that assumes 8 bytes needs a size/alignment function:

- **Locals/parameters/returns.** Locals currently get 8-byte stack slots; parameters are at `16 + 8*(n-1-i)`; returns come back in `RAX`. A 24-byte decimal needs three 8-byte slots (or a 24-byte aligned slot), a 24-byte parameter area with adjusted offsets, and a return convention: a hidden pointer to a caller-provided destination is the simplest and works for all helpers; alternatively use `RAX:RDX:RCX`. A hidden pointer must copy into a local if the callee can assign to the parameter. [V for current ABI; I for options]
- **Record fields.** `fieldList` must compute offsets from a size/alignment function, not `8 * index`. Fields after a decimal shift; descriptors list pointer/string offsets only, and a decimal has neither, so descriptor contents do not need new marking, but record size and field offsets do. [V for field code; I for impact]
- **Arrays.** `rt_alloc_array` currently sets `esize = 1` for `[]byte` and 8 otherwise. A new array kind (for example kind 7) with `esize = 24` is needed; `rt_append` must copy 24 bytes, `rt_truncate` must zero 24-byte elements, element load/store must move three words, and `for x in xs` must copy a 24-byte element. The array block remains pointer-free, so marking/freeing is unchanged. [V for runtime; I for changes]
- **Globals.** `gsize` is advanced by 8; a decimal global needs 24 and zero-initialization is valid (all-zero bits = coefficient 0, exponent 0, display scale 0, positive). [V/I]
- **Constants.** A decimal constant needs 24 bytes in rodata and a way for `Sym.value` (an `int`) to refer to them. Options: store an offset/index, add a field to `Sym`, or materialize the constant as a rodata address. Source `const` can use a decimal literal; `.sym` can encode 48 hex digits (the raw 24 bytes) under `const X: decimal = hex "..."`, which reuses the existing hex-string style and preserves exponent/display scale exactly. No decimal arithmetic folding is required initially; literals and constants can be materialized from rodata. [V for const/Sym; I for design]

  A subtle bootstrap issue: the compiler is itself an obo program with only 64-bit `int`s, and it cannot call the compiled program's runtime C helpers at compile time. Parsing a 38-digit decimal literal into a 128-bit coefficient therefore needs either (a) a small 128-bit decimal parser in `src/obo.obo` using two 64-bit or four 32-bit limbs, (b) a restriction that literals fit a 64-bit coefficient (about 18-19 digits) until the compiler-side parser is written, or (c) storing the literal text in rodata and parsing it at program startup, which makes it a runtime operation that can fail and is unsuitable for `const`/`.sym`. Option (a) is the clean long-term route and is a contained but non-trivial part of the effort; option (b) is a reasonable first vertical slice. Constant folding of decimal arithmetic can be skipped entirely: a decimal `const` can be a rodata value, and the `.sym` form can carry its raw bits. [I]
- **`Result[decimal]`.** The ok variant's descriptor size is currently 8 and `storeCellField` stores one word. A 24-byte payload needs descriptor size 24 and three stores; `try`/`match` must copy 24 bytes. The err variant stays 16 bytes. This makes `Result[decimal]` possible for `parse`/checked arithmetic, at the cost of one heap cell per success. [V for Result layout; I for changes]
- **Optionals.** `?decimal` is not representable as a zero word (0 is a valid decimal). Do not support it initially; if an optional decimal is needed, use `Result[decimal]` or a record with a flag. [I]

**Codegen.** The current `Item` is an 8-byte value in a register, constant, memory location, element or condition. A decimal item needs either:

1. **three words** in `Item` (coefficient low/high and metadata), with add/sub inline (`add`/`adc`) and multiply/divide/round via helpers; or
2. **a stack-slot address** in `Item`, with all operations calling runtime helpers that take destination/source pointers. This is simpler to get correct, slower, and matches the "wide value lives in a stack temp" model.

A pragmatic first implementation is (2): materialize decimal expressions into 24-byte stack temporaries, call C helpers for add/sub/mul/div/compare/round, and load/store fields/arrays with three-word moves. Later, inline add/sub and comparisons can be optimized. This avoids writing a 128×128 -> 256-bit multiply and a 256/128 divide in the raw x86 encoder. [I]

**Runtime helpers.** Add C functions in `runtime/runtime.c` for `add`, `sub`, `mul`, `div`, `rem`, `compare`, `round/quantize`, `parse`, `format`, `int->decimal`, `decimal->int`, and scale normalization. GCC on x86-64 supports `unsigned __int128` even in the freestanding build, so 64×64 -> 128 products and 128/64 divisions can use it; 128×128 -> 256 and 256/128 need 32- or 64-bit limb code (the .NET `DecCalc` multi-word algorithms are a good reference, extended to 256 bits). Formatting must build strings without libc; parsing must be hand-written. Helpers can be called with pointers (`rt_dec_add(dst, a, b)`) to avoid the 4-argument/`RAX` limit, or the compiler's `rtCall` can be extended to more arguments and a hidden-result pointer. [V for runtime/GCC; I for helper design]

**Rounding.** The original report used half-even (matching .NET). The database-first revision uses **half-away from zero for database scale conversion and the default `round`/`quantize`**, because SQL Server, Oracle, PostgreSQL `numeric` and ASE use commercial/half-away rounding; half-even remains an explicit mode. A separate built-in or library function `round(x, scale, mode)` exposes modes: half-away (default), half-even, half-toward-zero, down/truncate, up/away, floor, ceiling, and `UNNECESSARY` (error if any non-zero digit is discarded). There is no global context or thread-local state; the mode is an explicit argument. This avoids Python/Java context complexity and is single-pass friendly. [I; database behavior in §A.5.7]

**Errors.** obo has no exceptions. There are two realistic models:

1. **Operators trap** on overflow and division by zero (like an `int` division-by-zero SIGFPE, but with a clear runtime message), and explicit checked functions (`addChecked`, `divChecked`, `parse`) return `Result[decimal]` with error codes. This keeps `a + b` ergonomic and allocation-free.
2. **All operators return `Result[decimal]`**, which is safe but allocates a cell for every operation and makes arithmetic unreadable.

Recommendation: model 1. Error codes: overflow, division by zero, invalid literal, inexact/rounding required, scale out of range. `parse` always returns `Result[decimal]`; `format` returns `string` (or `Result[string]` only if formatting can fail). [I]

**Scale preservation.** Preserve the scale by default, as .NET does: `1.20m` stays `1.20`, `1.20 + 1.20` is `2.40`, and `1.00 / 2` is `0.50`. Equality/ordering/hash are numeric and ignore scale. Provide an explicit `normalize(x)` that strips trailing zeros and canonicalizes zero for keys/serialization. This gives audit-friendly display without making arithmetic surprising. [I]

**Conversions.**

- `int`/`byte` -> decimal: exact (all 64-bit integers fit in 128 bits / 38 digits).
- decimal -> `int`/`byte`: truncate toward zero? .NET truncates toward zero. Alternatively provide explicit floor/ceil/round. Overflow is an error (`Result` or trap).
- decimal -> `string`: invariant plain decimal with stored scale; optional `format(x, scale, mode)` quantizes first.
- `string` -> decimal: `parse`/`tryParse` returning `Result`; accept optional sign, digits, `.`, optional exponent initially; reject grouping unless a style is requested.
- decimal <-> future float/double: explicit only; decimal -> float may lose precision; float -> decimal rounds/overflows and rejects NaN/Inf. Do this after floats exist.
- `int(decimal)` and `decimal(int)` syntax can reuse the existing conversion-call syntax (`int(x)`, `string(x)`); add `decimal(int)` and `decimal(string)` but make the string form `Result`-returning or a separate `parse` to avoid a hidden failure. [I]

**JSON/`jq` interop.** JSON numbers are arbitrary-precision textually, but RFC 8259's interoperability note assumes IEEE 754 binary64; standard `jq` stores numbers as doubles and only preserves untouched literals (via its decnum literal backend); arithmetic converts to double and loses precision. A lossless finance tool should serialize money as a JSON **string** by default (or a number only when the consumer is known to preserve decimals), and should not expect `jq` arithmetic to preserve 38 digits. [V: RFC 8259 §6; jq manual; I: mapping recommendation]

**Bootstrap and seed.** The compiler is built by the seed compiler (`seed/obo`) and must reach a fixed point. New decimal syntax cannot appear in `src/obo.obo` until the seed understands it. The compiler-side support (scanner token, type kind, layout, `.sym` reader/writer) can be written using only existing `int`/`string`/record constructs, so the old seed can compile it; the new compiler then supports decimal for user programs and tests. The runtime helpers are C and are built by GCC, not by the obo compiler. `make promote` runs stage 2 and stage 3 and compares bytes; a decimal feature should be promoted once, after the fixed point and suite pass, exactly like `Result` and I/O. [V: `Makefile`; I: bootstrap strategy]

### 4.4 Recommendation and minimum viable feature

**Recommendation: implement a built-in, 24-byte, database-first decimal after floats; do not make a library record the final design, and do not copy the 16-byte `System.Decimal` layout.** [I]

A staged path that respects the current focus:

- **M0 (design/semantics and byte-format prototype, no compiler change):** Write a Python `decimal`-based oracle for 38-digit arithmetic, exponent/display-scale, rounding modes and overflow, plus golden byte-format vectors for TDS decimal(38,0)/(38,10), Oracle VARNUM, PostgreSQL binary and provisional Sybase ASE. No database product is required to encode published formats; integration tests come later. This is a library-only first step and can be kept as tests.
- **M1 (scalar vertical slice):** new 24-byte `decimal` kind; literals `1.23m`; locals, parameters and returns; `+ - * /`, unary `-`, comparisons; `int`/`string` conversions; `round`; trap on overflow/div0; invariant format. No fields/arrays/globals yet. This derisks the 24-byte codegen/ABI, 128-bit coefficient and 256-bit intermediates.
- **M2 (containers and symbols):** record fields, arrays, globals, constants, `Result[decimal]`, `.sym` type/const forms (48 hex digits), ABI/format bumps, module cross-checks, checked `Result` APIs, `parse`, and the DB column-metadata descriptor. This is the usable finance feature.
- **M3 (database adapters, in-process C):** SQL Server TDS first; Oracle OCI/VARNUM; Sybase ASE TDS with byte-order verification; PostgreSQL typmod adapter. Preserve precision/scale/nullable metadata, never use float, no helper process.
- **M4 (later):** arbitrary `numeric` for PostgreSQL/unconstrained/39–40-digit Oracle; float interop after floats; currency/Money library on top.

Minimum viable feature (M1+M2): 128-bit coefficient, 38 significant digits, sign, int16 exponent, uint16 display scale; `m` literals; operators and comparisons; display-scale preservation; half-away default plus explicit rounding modes; no NaN/Inf/signed-zero/currency; trap errors plus checked `Result` variants; `int`/`string` conversions; fields/arrays/globals/params/returns/constants/`Result[decimal]`; `?decimal` not supported; float interop deferred; database adapters in M3. [I]

A **limited library-only first step** is worthwhile only as an interim: a `fixed` module over `int64` with a scale constant, checked add/sub/mul/div/round/parse/format; or a Python/C byte-format oracle. It can serve a narrow tool or prototype the finance/DB API, but it is not the language decimal and should not block or replace the built-in feature. [I]

---

## 5. Finance semantics

### 5.1 Exact decimal versus binary float

Binary floating point cannot represent most decimal fractions exactly. The canonical example is `0.1`: it has no exact `double`/`float` representation, while decimal can represent it exactly. Microsoft's own guidance says decimal is appropriate for currency amounts, interest rates and similar numbers, and that `0.1` is exactly representable in decimal but not in `double`/`float`. Python's docs make the same point: `Decimal.from_float(0.1)` is `0.100000000000000005551115123125782702158...`, not `0.1`. For ledgers, sums, reconciliations and audit reports, use decimal, not binary float. [V: Microsoft C# floating-point types page; Python decimal docs]

### 5.2 Division, rounding and scale

Decimal addition, subtraction and multiplication are exact within the coefficient/scale limits; division is where policy enters. `1/3` has no finite decimal expansion. A finance system must decide:

- the **result scale** (for example 2 for USD amounts, 3 for KWD, 4 for CLF, 6-12 for FX rates, 0-9 for quantities);
- the **rounding mode** (database-first default: half-away from zero for DB scale conversion and `round`; half-even is available and remains the least biased choice for some accounting policies; floor/ceiling for some regulations);
- **when** to round (usually once at the final monetary amount, not at every intermediate step, to avoid accumulated bias);
- whether a result must be exact (`UNNECESSARY`/inexact error), which is valuable for validation.

.NET's default is half-even for both arithmetic and `Decimal.Round`; Python's default context is half-even; SQL Server `ROUND`, Oracle `ROUND`, PostgreSQL `numeric` and ASE use commercial/half-away rounding. There is no universal finance default. Because the main use case is database round-trip, use **half-away from zero as the database-conversion/`round` default**, keep half-even as an explicit mode, and record the mode used in reports. [V for defaults; I for policy recommendation]

### 5.3 Scale and trailing zeros

Scale is both numeric metadata and presentation metadata. `1.20` and `1.2` are numerically equal, but `1.20` communicates two decimal places and is the customary money presentation. .NET preserves trailing zeros, Python preserves them and even documents `Decimal('1.30') + Decimal('1.20') == Decimal('2.50')` as "the customary presentation for monetary applications." Preserve scale by default; make normalization explicit for keys, canonical JSON, and deduplication. Equality/ordering/hash should be numeric so `1.0` and `1.00` do not create two different keys. [V: .NET Decimal remarks; Python decimal docs; I for obo policy]

Database-first note: SQL Server, ASE and Oracle expose the display scale primarily as **column metadata**, while PostgreSQL stores `dscale` **in the value**. The obo value carries a display scale, and the database adapter reconciles it with the column metadata; see §A.5.4. [V for DB behavior; I for design]

### 5.4 Overflow and precision limits

The database-first target is a 128-bit coefficient, about **38 significant digits**, plus an exponent and display scale. That is much larger than the original 96-bit/28–29-digit target, and it is the right minimum for SQL Server, ASE and documented Oracle values:

- `10^38 - 1 = 99,999,999,999,999,999,999,999,999,999,999,999,999`, which fits a 128-bit unsigned coefficient with room to spare (2^128 ≈ 3.4e38).
- SQL Server/ASE `decimal(38,10)` has 28 integer digits and 10 fractional digits; its maximum is `9999999999999999999999999999.9999999999` (coefficient `10^38 - 1`). At scale 2 the maximum magnitude is about 1e36 units; at scale 10 about 1e28; at scale 38 the maximum is just under 1.
- Oracle `NUMBER` has 38 guaranteed digits but a range of about `1e-130` to `9.99e125`; this needs an exponent field, not just a scale 0–38. A 128-bit coefficient with an int16 exponent covers the documented range.
- A 38×38 multiplication produces up to **76 digits**, which needs a **256-bit intermediate** for exactness before the final rounding to 38 digits. A 128-bit coefficient alone cannot hold the intermediate product.
- PostgreSQL is arbitrary precision (up to 131072 digits before and 16383 after); values above 38 significant digits, or with exponents outside int16, need the arbitrary `numeric` tier.
- Crypto assets, high-precision unit prices, scientific quantities or aggregated products may also need arbitrary precision.

The important design rule is to treat "38 digits" as **total significant digits**, not "38 integer digits plus 38 fractional digits." Column precision/scale metadata defines the schema limit; the value checks overflow explicitly. For fixed-point int64, overflow is worse: obo's `int` arithmetic wraps silently, so every fixed-point operation must use checked helpers. [V for ranges/limits; I for domain guidance]

**Background: the original 96-bit analysis.** A 96-bit coefficient is about 7.9e28 total significant digits. At scale 2 the maximum magnitude is about 7.9e26 units; at scale 6 about 7.9e22; at scale 9 about 7.9e19; at scale 18 about 7.9e10. That is enormous for fiat amounts but cannot represent SQL Server/ASE `decimal(38)` maximum values or Oracle's exponent range; the 96-bit target is retained only as .NET background. [V for arithmetic; I for conclusion]

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

A built-in 24-byte decimal is allocation-free for locals, fields and array elements, but arithmetic is software and roughly an order of magnitude slower than `int64`/`double`, with division the worst. That is acceptable for ledgers, reports and CLI tools, not for tight numeric loops. A library record decimal would allocate per operation and add ownership moves, which is much worse. A fixed-point `int64` is the fastest option when its scale/range fit. [V for .NET performance; I for obo extrapolation]

### 5.8 Verdict on decimal for finance (database-first revision)

**.NET-style 28–29-digit decimal is no longer the right target; a 38-digit, exponent-and-display-scale decimal is.** The database-first type keeps the good .NET semantics — base-10 exactness, display-scale preservation, no NaN/Infinity in the fixed type, value semantics, no per-value heap — but raises the coefficient to 128 bits/38 digits and adds an exponent and display scale for Oracle and database round-trips. `System.Decimal` remains a useful reference for scale/trailing-zero behavior, and `SqlDecimal` is the closer .NET model for SQL Server, but neither is the complete target. The type should not pretend to be arbitrary precision; PostgreSQL/unconstrained/39–40-digit values go to the later `numeric` tier. Do not add currency or locale to the type. [I]

---

## 6. Compiler/runtime impact and sequencing

### 6.1 Phases touched

| Phase | Change | Risk | Rough size [I] |
|---|---|---|---|
| Scanner / literals | New decimal token, `m`/`M` suffix, exponent, lookahead so `1..3` remains a range and `1.` is not a field access; store literal text/value; 38-digit/exponent syntax | Low-medium | 150-300 lines |
| Type model / checker | `decimal` kind, `tDecimal` singleton, `same`/`assignable`/`isOwned`/`isRef`/`nonNull`, conversions, reject `?decimal`, DB metadata descriptor | Low-medium | 250-500 lines |
| Layout / ABI | 24-byte size/alignment for locals, parameters, returns, fields, arrays, globals, `Result` payload; new array kind/esize; hidden-pointer/return convention; bump `SYM_ABI` | High | 700-1,400 lines |
| Constants / `.sym` | 24-byte rodata constants, `Sym.value` representation, `const` parser, `.sym` decimal const form (48 hex digits), reader/writer, interface hash, format/ABI bump | Medium-high | 300-600 lines |
| Codegen | Wide `Item` (three words), literal materialization, `+ - * / %`, unary minus, comparisons, compound assignment, conversions, calls/returns, load/store, arrays/fields | High | 800-1,600 lines |
| Runtime C | 128-bit coefficient add/sub/mul/div/rem/compare, exponent/dscale normalization, round/quantize, parse, format, int conversions, error traps; 256-bit intermediates; no libc | High | 1,000-2,000 lines |
| DB metadata/conversion | In-process TDS SQL Server, Oracle VARNUM/OCI, Sybase ASE TDS, PostgreSQL binary/text; column precision/scale/nullable; byte-format goldens; no helper | High | 800-1,500 lines |
| Tests / mutants / docs | Arithmetic oracle, exponent/dscale/rounding edge cases, parse/format fuzz, ABI/`.sym` goldens, arrays/fields/Result tests, DB byte-format round-trips, docs examples, mutant coverage | High | 2,000-4,000 lines/test data |
| Seed promotion | Bootstrap with old seed, fixed point, memory budget, review, merge, `make promote` once | Medium | gates |

The **128-bit coefficient arithmetic** does not have to be written in the raw encoder. C helpers using `unsigned __int128` plus 64-bit limbs are the pragmatic route; 128×128 products go into 256-bit buffers, and 256/128 division needs limb code. The compiler only needs to emit a call and move 24-byte values. Inline `add`/`sub` (`add`/`adc`) and comparison can come later as an optimization. [I]

### 6.2 Effort and risk relative to Result, I/O and modules

The repository's own history gives a scale:

- `Result[T]`/`try`: **+480 compiler lines** in `src/obo.obo` (`d7fe98a`).
- Incremental I/O: **+107 compiler lines**, **+189 runtime C lines**, plus test programs and a runtime harness (`21f1cee`, `59ab1bd`).
- Modules Stage 1: **+305** and **+575** compiler lines across stages; Stage 2 symbol files: **+1,784 compiler lines** (`60d0888`, `dcf5f3f`, `d104bb6`).

A database-first decimal primitive is **larger than `Result` or I/O** and **comparable to or larger than the module work in compiler surface**, plus a substantial C arithmetic/format/parse layer, four priority database conversion paths (SQL Server, Oracle, ASE, PostgreSQL; SQL Anywhere later) and a much larger test matrix. The 24-byte layout/ABI, exponent/display-scale arithmetic and byte-format compatibility make it riskier than the original 16-byte estimate. It is not a routine feature. [I, based on the cited diff sizes and §A.6.5]

### 6.3 Why it should remain after floats

- Floats are 8-byte, hardware-supported (SSE), and simpler to add; they establish the real-literal, conversion and formatting patterns that decimal can reuse.
- The current roadmap puts floats at position 11; decimal is explicitly after them. The current focus is stdlib/`eza`, and no current tool needs decimal: `sort` already compares decimal-looking numbers as exact digit strings without floating point (`FORK.md` around line 3255), and `jq` numbers are double-based anyway.
- Decimal is finance-specific, 24-byte and software; doing it before floats would spend the harder feature first and delay the tool roadmap. Database adapters should follow the decimal language feature, not precede it, so the in-process interop work lands on a stable type. [V for roadmap/status/FORK; I for sequencing]

### 6.4 Limited library-only first step versus built-in

A library-only first step can be useful but is not a substitute:

- **`fixed` module over `int64`:** no compiler/runtime/seed change; checked functions; scale constant; exact for one currency/scale; can be used by a narrow finance tool now. It proves the API and tests, but cannot be a general `decimal`.
- **Record-based decimal:** two `int`s or a manual 128-bit implementation in a `ref record`; no compiler change, but one allocation per value/result, ownership moves, no operators, no constants. Useful as a semantics prototype only.
- **Python/C byte-format oracle:** encode/decode TDS, Oracle VARNUM, PostgreSQL binary and provisional ASE bytes against published formats; no DB product needed. Useful for M0, but not a language type.
- **Built-in 24-byte decimal:** the real feature; requires the compiler/runtime work above. This is the recommended destination.

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
- A Python `decimal`/custom oracle with **38-digit precision**, exponent/display-scale and explicit rounding modes for arithmetic/scale/rounding cases; expected values for DB-specific scale rules (division, trailing zeros, half-away ties) should be recorded as goldens rather than assumed identical to Python (Python contexts/scale rules differ in division and formatting).
- ABI/`.sym` goldens: record/array/Result layouts with decimal fields, 24-byte constants (48 hex digits), cross-module constants, alignment/copy/zero tests.
- Database byte-format goldens: SQL Server TDS decimal(38,0)/(38,10), Oracle VARNUM, PostgreSQL binary, provisional ASE; plus integration round-trips when DBs are available.
- Memory budget: a 24-byte type will increase user-program/test memory if used; the compiler itself need not use decimal, so the compiler budget impact should be small, but the seed promotion must still meet the existing cap.

`make promote` remains the final gate: stage 2 == stage 3, suite green, then copy stage 2 to `seed/obo` once. [V: Makefile/tests paths; I: test plan]

---

## 7. Open questions and recommended defaults

| Question | Recommended default | Rationale |
|---|---|---|
| Type name | `decimal` (lowercase, like `int`/`byte`/`bool`/`string`); no `Decimal` alias | obo primitives are lowercase; one spelling avoids type-identity confusion |
| Literal suffix | `m`/`M` required for decimal literals (`1m`, `1.23m`, `1.5e6m`) | C#-compatible; leaves bare real literals for future floats/doubles; `d`/`D` should mean double later |
| Precision / scale | 128-bit coefficient, 38 significant digits, sign, int16 decimal exponent, uint16 display scale; DB column precision/scale separate | SQL Server/ASE need 38 digits; Oracle needs exponent range and scale -84..127; 16-byte/28-29 digits is insufficient |
| Database metadata | Separate `DecimalColumn` descriptor (precision, signed scale, nullable, DB type); explicit bind/fetch conversion; fetch sets display scale | SQL Server treats `(p,s)` as distinct types; Oracle/ASE rely on column scale; PostgreSQL stores dscale |
| Oracle negative scale | Support -84..127 in column metadata; round left of the point on bind/fetch; exponent handles the value | `NUMBER(6,-2)` stores 123.89 as 100; scale is column metadata, not value bytes |
| Sybase product | Name ASE vs SQL Anywhere (vs IQ) explicitly; separate adapters and limits | ASE p38, SQL Anywhere p127; different wire/storage; native ASE is not BCD |
| Two-tier arbitrary numeric | Later `numeric` for PostgreSQL unconstrained, >38 digits, Oracle 39–40 raw, NaN/Infinity | A 128-bit fixed decimal is not arbitrary precision |
| Rounding default | Half-away from zero for database scale conversion and `round`/`quantize`; half-even available; explicit modes | SQL Server, Oracle, PostgreSQL `numeric` and ASE use commercial/half-away rounding; .NET half-even is the outlier |
| Rounding modes | Half-away (default), half-even, half-toward-zero, down/truncate, up/away, floor, ceiling, unnecessary | Covers invoice/tax/regulatory policies without global context |
| Scale preservation | Preserve by default; explicit `normalize`; numeric equality/hash | Audit/display need trailing zeros; keys/serialization need canonical form |
| Error model | Operators trap on overflow/div0; checked functions and `parse` return `Result[decimal]` | Keeps `a+b` ergonomic and allocation-free; recovery available where needed |
| Signed zero / NaN / Infinity | Fixed `decimal` is finite only; zero canonical positive; division by zero is an error; PostgreSQL NaN/Inf maps to `numeric`/error; Oracle ±Infinity overflow markers are errors | Avoids special-value rules and serialization ambiguity; DBs differ |
| Formatting / culture | Invariant C locale, plain decimal, no grouping by default; formatting/currency helpers separate | Deterministic audit output; no locale state in a single-pass language |
| Currency | Not in `decimal`; separate `Money`/currency code and scale policy | ISO minor units vary 0-4; one type cannot encode currency policy |
| JSON / `jq` | Serialize money as a JSON string by default; number only when the consumer is known to preserve decimals; do not rely on `jq` arithmetic | RFC 8259 binary64 interoperability; jq arithmetic converts to double |
| Fixed-point 64-bit first? | Not as the language type; optional `int64` library interim for a narrow tool | 18-19 digits, fixed scale, silent overflow; not general finance |
| Optionals | No `?decimal` initially; use `Result[decimal]` or a record with a flag | Zero is a valid decimal; a sentinel word cannot represent nil |
| Containers / ABI | Support fields, arrays, globals, parameters, returns, constants and `Result[decimal]`; 24-byte value; hidden-pointer or multi-register return; bump `SYM_ABI` | A decimal that cannot live in records/arrays is not usable; ABI must change exactly once |
| Float interop | After floats; explicit conversions only; decimal->float lossy, float->decimal rounded/overflow-checked | Floats are position 11; no implicit mixing (C# rule) |
| IEEE decimal128 compatibility | Not initially | 34 digits/NaN/Inf/BID-DPD are a different, larger design; revisit only for interop |

---

## 8. Sources

URLs were retrieved on 2026-10-28. Vendor documentation pages are living documents; version/date information is noted where the page provides it. Source-code citations pin a commit or release tag. Items marked **[O]** in the text are observed/implementation-derived, not vendor contracts.

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

### SQL Server / TDS / ADO.NET (database-first addendum)

- SQL Server `decimal` and `numeric` (precision 1–38, scale 0–p, storage 5/9/13/17 bytes, conversion/rounding/overflow, `sys.columns` metadata): https://learn.microsoft.com/en-us/sql/t-sql/data-types/decimal-and-numeric-transact-sql?view=sql-server-ver17 (page date 2026-10-13)
- SQL Server `ROUND` (ties round half away from zero, commercial rounding): https://learn.microsoft.com/en-us/sql/t-sql/functions/round-transact-sql?view=sql-server-ver17 (page date 2025-03-31)
- [MS-TDS] Tabular Data Stream Protocol, v20260617, revision 42.0 (published 2026-06-17), §2.2.5.4.2 fixed-length types, §2.2.5.4.3 variable-length types, §2.2.5.5.1.6 Decimals and Numerics, §2.2.5.6 Type Info: https://learn.microsoft.com/en-us/openspecs/windows_protocols/ms-tds/b46a581a-39de-4745-b076-ec4dbb7d13ec and PDF https://winprotocoldocs-bhdugrdyduf5h2e4.b02.azurefd.net/MS-TDS/%5bMS-TDS%5d.pdf
- [MS-TDS] Variable-Length Data Types (token values `DECIMALNTYPE 0x6A`, `NUMERICNTYPE 0x6C`; valid lengths 0x05/0x09/0x0D/0x11): https://learn.microsoft.com/en-us/openspecs/windows_protocols/ms-tds/ce3183a6-9d89-47e8-a02f-de5a1a1303de
- SQL Server Science, "How SQL Server Stores DECIMAL and NUMERIC, Down to the Bytes" (observed SQL Server 2019 `DBCC PAGE` layout; sign byte, little-endian magnitude, `sys.columns` metadata): https://www.sqlserverscience.com/internals/how-sql-server-stores-decimal/
- `System.Data.SqlTypes.SqlDecimal` (four unsigned 4-byte integers, max precision/scale 38, range ±10^38-1): https://learn.microsoft.com/en-us/dotnet/api/system.data.sqltypes.sqldecimal?view=net-10.0 (page date 2025-07-01)
- ADO.NET "SQL Server Data Type Mappings" (.NET `Decimal` max scale 28 vs SQL Server 38; `decimal`/`numeric` map to `SqlDbType.Decimal`; `GetSqlDecimal`): https://learn.microsoft.com/en-us/dotnet/framework/data/adonet/sql-server-data-type-mappings (page date 2026-10-19)
- `dotnet/runtime` `SQLDecimal.cs` at commit `ee61e035dc191ec606d1fd83dfa171a8914feb04` (fields `_bStatus`, `_bLen`, `_bPrec`, `_bScale`, `_data1.._data4`; `s_NUMERIC_MAX_PRECISION = 38`; `WriteTdsValue`, `AdjustScale`, `ConvertToPrecScale`): https://github.com/dotnet/runtime/blob/ee61e035dc191ec606d1fd83dfa171a8914feb04/src/libraries/System.Data.Common/src/System/Data/SQLTypes/SQLDecimal.cs

### Oracle (database-first addendum)

- Oracle Database 19c SQL Language Reference, "Data Types" (`NUMBER(p,s)`: p 1–38, s -84..127, 1–22 bytes, negative scale, rounding/overflow examples; `BINARY_FLOAT`/`BINARY_DOUBLE`): https://docs.oracle.com/en/database/oracle/oracle-database/19/sqlrf/Data-Types.html
- Oracle Database 18c/19c OCI Programmer's Guide, "Data Types": `NUMBER` internal format (21 bytes, exponent/sign byte, base-100 mantissa, digit+1/101-digit, 102 terminator, 19 guaranteed bytes = 38 digits), `VARNUM` (22 bytes), Table 5-3 examples: https://docs.oracle.com/en/database/oracle/oracle-database/18/lnoci/data-types.html
- Oracle Database 19c SQL Language Reference, `DUMP`: https://docs.oracle.com/en/database/oracle/oracle-database/19/sqlrf/DUMP.html
- Oracle Database 19c SQL Language Reference, `ROUND (number)` (positive formula `FLOOR(n*10^integer+0.5)*10^-integer`; negative symmetric): https://docs.oracle.com/en/database/oracle/oracle-database/19/sqlrf/ROUND-number.html
- Oracle C++ Call Interface Programmer's Guide, `Number` class (38 digits; range 1e-130 to 9.999...e125; zero exact; ±Infinity overflow markers; no NaN; scale not preserved): https://docs.oracle.com/en/database/oracle/oracle-database/19/lncpp/number-class.html
- ODP.NET Developer's Guide, `OracleDecimal` structure (38 precision; .NET Decimal 28; `.Value` throws above 28; `ToString`/`BinData`): https://docs.oracle.com/en/database/oracle/oracle-database/26/odpnt/OracleDecimalStructure.html
- Observed `DUMP` examples (Oracle forum/AskTom and Stack Overflow; `Typ=2`, `193,2` for 1, negative terminator, zero `128`, `NUMBER(30,2)` trailing-zero case): https://asktom.oracle.com/ords/asktom.search?tag=integer-shows-as-both-number22-and-number38 and https://stackoverflow.com/questions/26279538 — **[O]**, not vendor contracts.

### Sybase / SAP (database-first addendum)

- SAP ASE 16.1 Reference Manual: Building Blocks, document version 1.0, 2025-02-28, §2.1.1.2 "Decimal Datatypes" (precision 1–38, scale 0–38, storage formula `ceiling(precision/log10(256))+1`, `numeric(18,4)`=9 bytes, max 17 bytes): https://help.sap.com/doc/a6124864bc2b101491dec8028d04e7cd/16.1.0.0/en-US/SAP_ASE_Reference_Manual_Blocks_en.pdf
- SAP Help, "Decimal Datatypes" (ASE 16.0.3): https://help.sap.com/docs/SAP_ASE/2df920cd5b1644e5a065d55b637d5e92/aab84005bc2b1014a9e4de77b205c641.html?version=16.0.3.0&locale=en-US
- SAP ASE SDK 16.1, "Real, Float, Numeric, and Decimal Types" (`CS_NUMERIC` struct; precision 1–77 client, scale 0–77; base-256 magnitude; sign byte 0 positive/1 negative; byte-order wording): https://help.sap.com/docs/SAP_ASE_SDK/9e462472157c4fe48a6c36d2e75f417d/bfe22cc56db61014bf8f040071e613d7.html?locale=en-US&version=16.1.0.1
- Sybase Client-Library 15.5 Reference, "Real, float, numeric, and decimal types": https://infocenter.sybase.com/help/topic/com.sybase.infocenter.dc32840.1550/html/ctref/ctref362.htm
- Sybase TDS 5.0 Functional Specification v3.4 (`TDS_NUMN 0x6C`, `TDS_DECN 0x6A`, length/precision/scale, base-256): http://www.sybase.com/content/1013412/tds34.pdf — link now redirects to a marketing page; token details corroborated by FreeTDS, so treat the vendor PDF as **[O]** here.
- FreeTDS `src/tds/numeric.c` and `src/tds/data.c` at commit `dda3cd532574bfd0cfd349a946736bfc5d264738` (sign mapping, `tds_numeric_bytes_per_prec`, `tds_swap_numeric` between Microsoft TDS 7+ and Sybase-style internal): https://github.com/FreeTDS/freetds/blob/dda3cd532574bfd0cfd349a946736bfc5d264738/src/tds/numeric.c and https://github.com/FreeTDS/freetds/blob/dda3cd532574bfd0cfd349a946736bfc5d264738/src/tds/data.c
- SAP SQL Anywhere 17 SQL Reference, "DECIMAL Data Type", page 146 (precision 1–127, default 30; scale 0–127, default 6/0; storage formula): https://help.sap.com/doc/0ad6ee207bdc48c58ab2acfcad1b2300/17.0.01/en-US/SQL-Anywhere-Server-SQL-Reference-en.pdf
- SAP Help, "DECIMAL Data Type" (SQL Anywhere): https://help.sap.com/docs/SAP_SQL_Anywhere/93079d4ba8e44920ae63ffb4def91f5b/81f6a2486ce21014ada995e5b3ab34f6.html?locale=en-US

### PostgreSQL (database-first addendum)

- PostgreSQL current documentation, "Numeric Types" (`numeric`/`decimal`, precision 1000, scale -1000..1000, 131072/16383 implementation limits, variable storage, NaN/Infinity, ties away from zero, negative scale): https://www.postgresql.org/docs/current/datatype-numeric.html
- PostgreSQL 14 release notes, "Add support for Infinity and -Infinity values in the numeric data type (Tom Lane)": https://www.postgresql.org/docs/14/release-14.html
- PostgreSQL `src/backend/utils/adt/numeric.c` at `REL_18_0`, commit `3d6a828938a5fa0444275d3d2f67b64ec3199eb7` (NBASE=10000, NumericShort/NumericLong, sign/dscale/weight, special values, NumericVar, numeric_send/numeric_recv, max weight): https://github.com/postgres/postgres/blob/3d6a828938a5fa0444275d3d2f67b64ec3199eb7/src/backend/utils/adt/numeric.c
- PostgreSQL `src/include/utils/numeric.h` at the same commit (`NUMERIC_MAX_PRECISION 1000`, `NUMERIC_MIN_SCALE -1000`, `NUMERIC_MAX_SCALE 1000`, `NUMERIC_MAX_DISPLAY_SCALE 1000`): https://github.com/postgres/postgres/blob/3d6a828938a5fa0444275d3d2f67b64ec3199eb7/src/include/utils/numeric.h

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
