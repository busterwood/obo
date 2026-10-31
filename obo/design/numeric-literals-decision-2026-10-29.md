# Numeric literal policy: exact by default, float by suffix

**Date:** 2026-10-29
**Status:** User-approved language direction. The numeric branch now implements the float-first candidate at `0.10`/format 6; decimal remains after floats. Floats were accelerated ahead of the older roadmap position by explicit user decision.
**Scope:** literal and conversion policy for the planned `float` and `decimal` types. The eza/stdlib phases and the approved eza design are unaffected.

## Decision

obo keeps both an exact decimal type and IEEE 754 binary floating point, and makes exactness the default for fractional literals:

- **`decimal`** is the exact type: 24-byte, 128-bit coefficient, 38 significant digits, sign, exponent and display scale, finite only (per `decimal-research-2026-10-28.md`).
- **`float`** is IEEE 754 binary64 initially. It is required for database approximate columns, math, measurement, graphics and JSON binary64 interoperability.
- A fractional or exponent literal with no suffix is **decimal**; the `f`/`F` suffix marks a **float**.

| Literal | Type | Notes |
|---|---|---|
| `1` | `int` | unchanged |
| `1.5` | `decimal` | exact; scale 1 |
| `1.50` | `decimal` | scale 2; trailing zero preserved for display and database binding |
| `1.5e3` | `decimal` | exact 1500; exponent notation is exact |
| `1f`, `1.5f` | `float` | binary64; `F` accepted |
| `1.5e3f` | `float` | binary64 |

There is no `m` suffix and no implicit float literal. The earlier `1.23m` examples in the decimal research are superseded for literal syntax.

## Conversions

- `int` to `decimal`: implicit; always exact.
- `int` to `float`: explicit (`float(i)`); large integers lose precision.
- `decimal` to/from `float`: explicit only; document rounding and overflow.
- `decimal` plus `float`: compile error; convert explicitly.
- A float-typed context does not silently accept a bare `1.5`: write `1.5f` or `float(1.5)`. The approximation is visible at the literal or conversion.
- Decimal literal scale is preserved (`1.50` has scale 2). Numeric equality ignores scale. Exponent-literal normalization and display-scale rules are part of the decimal implementation design.

## Value semantics

- `decimal` is finite only: no NaN, infinity or signed zero; overflow and division by zero trap, with checked `Result` variants later.
- `float` follows IEEE 754 binary64: NaN, positive/negative infinity and signed zero. Their literal and API spellings are a later design detail.
- Decimal is never used for approximate values, and float is never used for money or exact database round-trips.

## Database mapping

- Exact database types (`decimal`/`numeric`, Oracle `NUMBER`, and exact money types) map to `decimal`.
- Approximate database types (SQL Server `float`/`real`, Oracle `BINARY_FLOAT`/`BINARY_DOUBLE`, PostgreSQL `real`/`double precision`, Sybase `float`/`real`) map to `float`.
- There is no float conversion path for exact decimal values and no decimal path for approximate values. Database precision/scale-aware binding remains later work.

## Sequencing

- Floats remain at roadmap position 11; decimal remains after floats. The eza/stdlib work is unaffected.
- During the float-only phase there is no decimal type, so a bare fractional or exponent literal is a compile error with a diagnostic directing `1.5f` for float. There is no temporary float default that would later change meaning.
- When decimal lands, bare `1.5` becomes decimal and existing `f`-suffixed code is unchanged.
- If the order is ever changed so decimal comes first, this literal policy still applies; it is independent of which type is implemented first.

## Open implementation details (not part of the approved rule)

- Decimal exponent-literal parsing and normalization, display-scale rules, and literals above 64 bits in the self-hosted compiler.
- Whether `float` gains a binary32/`real` companion and what suffix or type names it uses (binary32 read into binary64 is exact; writing it needs range checks).
- Decimal math functions (`sqrt`, `sin`, and similar): float-only with explicit conversions, or a separate decimal library.
- Special-value literal and API syntax for float NaN and infinities.
- Exact diagnostic wording.
- Whether any optional `m` alias is ever accepted (recommended: no).
- Equality, hashing and shortest-round-trip formatting details.
- The decimal implementation itself (24-byte ABI, arithmetic, database adapters), scheduled after floats.

## References

- `decimal-research-2026-10-28.md` — decimal type design and database formats; its literal `m` examples are superseded by this note.
- `stdlib-eza-design-v7.md` — current phase design; floats and decimal are not part of it.
- Roadmap: floats position 11, decimal after floats.
