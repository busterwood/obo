# Decimal ↔ database boundary design

**Date:** 2026-10-31
**Status:** DESIGN ONLY, for independent review. No source, adapter, runtime,
seed, version, format or ABI change is made by this pass. No version is
reserved.
**Baseline:** `main` at `51ce9e7680a90df9e3d5e38a9b469a460a2ca968` (0.12
decimal scalar tier released). Worktree
`/home/chris/.local/share/opencode/worktree/fa36f3/decimal-db-design`, branch
`decimal-db-design`.
**Scope owner:** `obo/experimental/db/{postgres,sqlite}` and the approved
decimal design `obo/design/decimal-language-design.md` §5.

## 0. Summary of decisions

1. The database wire form is **exact decimal text**, never binary numeric and
   never a float. PostgreSQL parameters use text format with explicit OID
   `1700`; PostgreSQL results are read from text-format `PGresult` bytes.
2. The emitted text is the value's **display form** (`decimal::format`: fixed
   notation with exactly `dscale` fractional digits) when that form is exact.
   If the display scale was capped and would hide a nonzero digit, the adapter
   emits `decimal::canonical` instead. Neither form rounds.
3. `decimal::check(value, p, s)` is the only precision/scale gate. It is exact,
   accepts PostgreSQL's negative scale `-1000..1000`, and never rounds.
   `param_decimal_checked` / `bind_decimal_checked` reject a value that the
   declared `(p,s)` would round or overflow; the unchecked forms leave that to
   the server and are documented as such.
4. SQLite stores decimal as **TEXT**. `decimal()` accepts TEXT and INTEGER
   storage, rejects REAL and BLOB, and never calls `sqlite3_column_double`.
   NUMERIC/REAL affinity columns are documented traps, not a supported storage
   policy.
5. NULL is distinct from zero and from every decimal error. The v1 API is
   **error-only**: callers test `is_null` (or `column_value`) first; a defensive
   NULL error code `1001` exists but is not the normal path. A tagged
   `DecimalOrNull` wrapper is deferred because decimal record/variant fields
   are not implemented in 0.12 (`obo/src/obo.obo:5448`).
6. PostgreSQL typmod is decoded with the upstream formula
   `((p << 16) | (s & 0x7ff)) + 4`; `-1` means unconstrained. Read-back
   validates the decoded `(p,s)` with `decimal::check`; the column scale is
   never inferred from the numeric text alone.
7. No `decimal(p,s)` language type, no `?decimal`, no binary numeric protocol,
   no COPY/arrays/intervals, and no decimal in records in this pass.

## 1. Scope and non-goals

**In scope**

- scalar `decimal` values crossing the two merged experimental adapters;
- PostgreSQL `numeric`/`decimal` OID 1700, text parameters and text results;
- SQLite TEXT/INTEGER storage, affinity and storage-class checks;
- canonical text, NULL, precision/scale validation, error codes, sizes;
- the composition contract with the approved `sql` template design.

**Out of scope (explicit non-goals)**

- decimal inside records, closed variants, arrays, globals, constants,
  captures, interfaces or method parameters (rejected today;
  `obo/LANGUAGE.md:126-131`, `obo/src/obo.obo:5448`, `:5382`);
- a language `decimal(p,s)` type or any implicit coercion (`decimal-language-design.md:84`);
- `?decimal` and any nullable-decimal representation that the compiler cannot
  express (`decimal-language-design.md:42`);
- binary numeric wire formats (`numeric_send`/`numeric_recv`), COPY, arrays,
  intervals, money/currency, server-side prepared statements;
- float transport of any kind, including `sqlite3_column_double`, `strtod`,
  `atof`, `numeric` binary decoding, and `decimal::toFloat`;
- schema/ORM/catalog introspection, migrations, connection policy, TLS;
- starting a database, creating roles/databases/schema/data, or changing
  server configuration.

## 2. Value and text model

### 2.1 Accepted text grammar

The database boundary accepts exactly the `decimal::parse` grammar
(`decimal-language-design.md:117`) and applies it to the whole cell:

```text
db-decimal := ['+'|'-'] ( digits [ '.' digits? ] | '.' digits )
              [ ('e'|'E') ['+'|'-'] digits ]
```

with these rules:

- whole input is consumed; no leading or trailing whitespace;
- ASCII only; embedded NUL and every byte `> 0x7f` are invalid;
- no grouping separators, no underscores, no `NaN`, no `Infinity`, no bare
  sign, no empty string;
- a trailing point (`1.`) and a leading point (`.5`) are accepted by the
  private parser (`dec24_text.h:11-16`) but are **never emitted**. The shared
  adapters accept them because `decimal::parse` does; a second, stricter
  grammar would need a separate scanner. The emitted form has no bare point.
- leading zeros are accepted (`0001.2300` = `1.2300`); trailing fractional
  zeros are significant for `dscale` and are preserved;
- the coefficient is normalised by stripping trailing zeros; more than 38
  significant digits is `PRECISION` (7), a nonzero exponent outside `int16` is
  `EXPONENT` (8), `dscale > 65535` is `DISPLAY_RANGE` (9), and syntax is
  `INVALID` (4), in the precedence fixed by `dec24_text.h:27-33`;
- `-0`, `+0` and `0` all become canonical positive zero (no negative zero);
  zero has exponent 0 but keeps its written `dscale` (`decimal-language-design.md:28`).

### 2.2 Emitted text

Two exact forms are defined, both already implemented in the private core:

| Form | Producer | Shape | Purpose |
|---|---|---|---|
| display | `decimal::format` (`dec24_text.c:388`) | fixed notation, exactly `dscale` fractional digits, no exponent | parameter binding, PG-compatible text, display scale preservation |
| canonical | `decimal::canonical` (`dec24_text.c:291`) | minimal exact text; fixed up to 128 bytes, exact scientific (`d e±N`) above | fallback when display cannot be exact; value keys |

Emitted rules:

- optional `-` only (never `+`), no leading zeros in the integer part except
  the single `0` before the point;
- display always writes the point when `dscale > 0`, even for zero
  (`0.0000`); `dscale == 0` has no point;
- canonical writes `0` for zero, never `0.0`, never `-0`; it writes no
  trailing fraction zeros and no trailing point;
- scientific canonical uses lowercase `e`, no `+` sign, and no exponent
  leading zeros (`1.23e-80`, not `1.23E-080`);
- the display form is preferred. The adapter falls back to canonical **only**
  when `format` returns `DEC_INEXACT` (capped display scale would drop a
  nonzero digit; `decimal-language-design.md:118,130`);
- PostgreSQL accepts both forms (its `numeric_in` accepts `e/E`, signs,
  leading/trailing spaces and since PG 16 even underscores; we emit none of
  the permissive extras). SQLite stores the bytes verbatim.

### 2.3 Canonical, display and equality

`1.0 == 1.00` is true (`decimal-language-design.md:57`). Text equality is not
value equality: `"1.0"` and `"1.00"` are different display texts, and
`"1.0"` and `"1"` are different display texts for the same value. The shared
canonical text is value-only (`"1"` for both), so it can be used as a key;
display text is presentation.

### 2.4 Limits and maximum sizes

| Quantity | Limit | Source |
|---|---|---|
| coefficient significant digits | 38 (`<= 10^38-1`) | `decimal-language-design.md:26` |
| exponent | `int16`, `-32768..32767` | same |
| display scale | `uint16`, `0..65535` | same |
| quantize/div target scale | `0..32768` | `decimal-language-design.md:106` |
| maximum exact text | 98,342 bytes | `DEC_TEXT_MAX_OUTPUT`, `dec24_text.h:62` |
| PostgreSQL numeric display scale | `0..16383` | `NUMERIC_DSCALE_MAX` 0x3FFF |
| PostgreSQL numeric precision in DDL | `1..1000` | `NUMERIC_MAX_PRECISION` |
| PostgreSQL numeric scale in DDL | `-1000..1000` | `NUMERIC_MIN/MAX_SCALE` |
| adapter SQL text | 1 MiB | `postgres_adapter.c:29`, `sqlite_adapter.c:22` |
| adapter value/parameter buffer | 16 MiB | `postgres_adapter.c:30`, `sqlite_adapter.c:23` |
| parameters per statement | 32 | `postgres_adapter.c:28` |

The core's 98,342-byte bound is exactly the worst case: 32,805 integer
positions (exponent 32767 plus 38 significant digits) + 65,535 fractional
digits + sign + point. It is also the largest text the adapters will accept
from a peer for binding.

## 3. PostgreSQL

### 3.1 OID and typmod

`OID_NUMERIC = 1700` is already exported (`postgres.obx:32`) and is the only
accepted decimal result OID. `decimal` is a SQL alias for `numeric` and reports
the same OID. Domains over `numeric` have a different OID and are rejected in
v1; resolve them in SQL (`col::numeric`) or add catalog resolution later.

A `PGresult` typmod is not the same as the `dscale` stored in a value. The
upstream encoding (`make_numeric_typmod`, `src/backend/utils/adt/numeric.c`) is:

```text
typmod = ((precision << 16) | (scale & 0x7ff)) + 4      // VARHDRSZ = 4
precision = ((typmod - 4) >> 16) & 0xffff
scale     = ((((typmod - 4) & 0x7ff) ^ 1024) - 1024)    // sign-extend 11 bits
```

`typmod == -1` means unconstrained numeric. Values `0..3` are not valid
numeric typmods; other negative values are invalid. PostgreSQL accepts
negative scale only since version 15; when a server reports one, the adapter
supports it. Test vectors:

| Declaration | typmod |
|---|---:|
| `numeric(1,0)` | 65540 |
| `numeric(10,4)` | 655368 |
| `numeric(2,-3)` | 133121 |
| `numeric(3,5)` | 196617 |
| `numeric(38,0)` | 2490372 |
| `numeric(1000,1000)` | 65537004 |
| `numeric(1000,-1000)` | 65537052 |
| unconstrained `numeric` | -1 |

`column_type_oid()` and `column_type_modifier()` already exist and validate
the column index before reading typmod (`postgres.obx:381-391`). The new
`decimal_type()` decodes them into an all-int record; no decimal field is
needed. Nullability is **not** available in `PGresult` and is not exposed.

### 3.2 Text on the wire

- `execute()` requests result format 0 (text); `execute_binary()` requests 1
  (`postgres.obx:278-285`). Decimal read-back is defined only for text rows.
- Text-format `numeric` output is PostgreSQL's `numeric_out`: sign, fixed
  notation, exactly `dscale` fractional digits, no exponent, no leading zeros,
  no `NaN`/`Infinity` except for the special values. PG numeric has no negative
  zero (`make_result_safe` forces zero positive).
- Special values `NaN`, `Infinity`, `-Infinity` are legitimate PostgreSQL
  `numeric` values but are not obo decimals (`decimal-language-design.md:106`).
  The read path must recognise those exact strings and return `1004`
  (`NONFINITE`), not feed them to the parser and report a generic syntax error.
- `execute_binary()` on a numeric column returns the upstream binary form
  (`int16 ndigits, weight, sign, dscale, digits`). v1 never decodes it:
  `decimal()` returns `1003` (`BINARY_UNSUPPORTED`).

### 3.3 Parameter binding

`Param` is an ordinary record of `kind`, `i`, `data` (`postgres.obx:45-49`); it
has no decimal field, so binding formats the value immediately. Add two kinds:

```obo
pub const PARAM_DECIMAL = 5
pub const PARAM_DECIMAL_NULL = 6
```

- `PARAM_DECIMAL`: `data` holds the exact UTF-8/ASCII text from §2.2; the
  packet builder validates the grammar, rejects NUL/empty/oversize, and writes
  it as kind 5. The C adapter maps kind 5 to `paramTypes[i] = 1700`,
  `paramFormats[i] = 0`, `paramLengths[i] = len`, `paramValues[i] = text`.
- `PARAM_DECIMAL_NULL`: a typed SQL NULL with `types[i] = 1700`,
  `paramValues[i] = NULL`. This matters for `SELECT $1` where the server has
  no target type to infer; plain `param_null()` keeps OID 0 and is still
  correct for `INSERT ... VALUES ($1)` and for `$1::numeric`.
- Never interpolate. The SQL string contains only `$1..$n`; the decimal text
  is always a separate length-delimited parameter (`postgres_adapter.c:3-17`,
  `:197-198`).
- The C adapter re-validates the decimal grammar before `PQexecParams`
  (defence in depth; the obo builder already emits valid text). It rejects
  `NaN`, `Infinity`, `1_000`, whitespace, quotes, `0x`, a bare sign, empty text,
  embedded NUL, a `+` mantissa sign, more than one point and non-ASCII bytes.
  Its kind bound (`postgres_adapter.c:149`) grows from `PG_PARAM_BOOL` to
  `PG_PARAM_DECIMAL_NULL`; text length is capped at 98,342 bytes.
- NULL, zero and empty text are three different things: NULL is a NULL
  parameter, zero is the text `0`/`0.0000`, and empty text is always rejected.

`param_decimal_checked(value, p, s)` calls `decimal::check` before building the
packet. This is the only way to guarantee that a `numeric(p,s)` target will not
silently round: PostgreSQL itself rounds fractional excess and raises
`numeric field overflow` for integer excess
(`apply_typmod`/`round_var`). `param_decimal` and `param_decimal_checked` bind
the same value; the checked form only adds the exact-fit gate. The caller must
supply the target column's `(p,s)`; the adapter cannot discover it from `$n`
or from a `RowDescription` that does not exist before execution.

### 3.4 Read-back

`Rows.decimal(row, col)` performs, in order:

1. `is_null` on the exact cell; NULL → `1001`, out of range → `2`;
2. if the `Rows` came from `execute_binary`, → `1003`;
3. `column_type_oid == 1700`, else `1002`;
4. `column_type_modifier` decode: `-1` is unconstrained; `>= 4` must decode to
   `p 1..1000`, `s -1000..1000`, else `1012`; `0..3` or `< -1` is `1012`;
5. take the exact bytes from `bytes()` (not `text()`, which loses embedded NUL
   and maps NULL to a different error); reject empty, NUL or non-ASCII as
   `1006`;
6. exact special strings (`NaN`, `Infinity`, `-Infinity`) → `1004`;
7. `decimal::parse`; map `4→1006`, `7→1007`, `8→1008`, `9→1009`;
8. if constrained, `decimal::check(v, p, s)`; map `3→1010`, `7→1011`,
   `6→1012`. A conforming server value always passes; a failure means the
   value would not fit the declared metadata, never that it should be rounded.

The value keeps the `dscale` that `decimal::parse` derives from the text. For
`s >= 0`, PostgreSQL prints exactly `s` fractional digits (including trailing
zeros), so the column display scale is recovered exactly. For `s < 0`,
PostgreSQL prints zero fractional digits; `dscale` becomes 0 and the negative
scale lives only in the typmod (`DecimalType.scale`).

`1.0000` read from an unconstrained `numeric` keeps `dscale = 4`. `1` read
from `numeric(10,4)` prints `1.0000` and keeps `dscale = 4`. `12000` read from
`numeric(2,-3)` keeps `dscale = 0`; the scale is `-3` in the metadata.

A value already rounded by the server (by another writer, or by an unchecked
bind into `numeric(p,s)`) is indistinguishable on read from a value that was
stored exactly. `check` validates the stored value against the declared
metadata; it cannot recover the pre-rounding value. Preventing new rounding is
the job of `param_decimal_checked`/`bind_decimal_checked`.

### 3.5 Precision/scale validation

The exact-fit predicate used by `decimal::check` is, for a value
`v = (-1)^sign * c * 10^e` (the stored coefficient need not be normalised):

```text
k := e + s
fits(v,p,s) := c == 0
            or ( (k >= 0 or trailing_zeros(c) >= -k)
                 and digits(c) + k <= p )
```

- `k < 0` and `trailing_zeros(c) < -k` means `value * 10^s` is not an
  integer; accepting it would require rounding → `DEC_INEXACT` (adapter
  `1010`).
- `digits(c) + k > p` means the scaled integer needs more than `p` digits →
  `DEC_PRECISION_OVERFLOW` (adapter `1011`). The expression is invariant under
  stripping trailing zeros from `c` (`digits-1`, `e+1`), so normalisation is
  not required before the test.
- `p` outside `1..1000` or `s` outside `-1000..1000` → `DEC_UNSUPPORTED_RANGE`
  (adapter `1012`).
- `dscale` is ignored: precision/scale are numeric metadata, not display
  metadata. `1.2300` and `1.23` fit the same `(p,s)`.

This predicate is exact and allocation-free; it never materialises
`10^p`. Examples:

| value | p | s | result |
|---|---:|---:|---|
| `1.2345` | 10 | 4 | fits |
| `1.23456` | 10 | 4 | `1010` (would round) |
| `123456.7890` | 10 | 4 | fits (9 significant digits) |
| `1234567.890` | 10 | 4 | `1011` (needs 11) |
| `99999999999999999999999999999999999999` | 38 | 0 | fits |
| `10^38` | 38 | 0 | `1011` (39 digits) |
| `0` | 1 | 0 | fits |
| `0.001` | 1 | 3 | fits |
| `0.01` | 1 | 3 | `1011` |
| `99000` | 2 | -3 | fits |
| `1500` | 2 | -3 | `1010` (not a multiple of 1000) |
| `-1.2300` | 10 | 4 | fits |

`decimal::check` returns the value unchanged on success; it never quantizes.
The required workflow is `quantize` first if rounding is wanted, then `check`.

### 3.6 PostgreSQL `dscale` limit and unrepresentable values

PostgreSQL stores display scale in 14 bits (`NUMERIC_DSCALE_MAX = 16383`) and
rejects text whose computed `dscale` exceeds it with SQLSTATE 22003
("value overflows numeric format"). Because `numeric_in` computes
`dscale = max(0, fractional_digits - exponent)`, any nonzero value with
`exponent < -16383` is **not representable in PostgreSQL numeric**, regardless
of how it is spelled. `1e-16383` is the boundary; `1e-16384` is rejected by
the server.

The PostgreSQL binder therefore needs the value's exponent:

- if the value is nonzero and `exponent < -16383` → `1013`
  (`PG_RANGE`), before building the packet;
- else if the display text would have `dscale > 16383` (only possible through
  trailing zeros), emit canonical text instead; value is preserved, display
  scale is not;
- else emit display text.

`decimal::exponent(d) -> int` (and, for symmetry and tests,
`decimal::dscale(d) -> int`) is a prerequisite for this pre-classification.
`exponent` must return the **normalised** exponent: the stored exponent plus
the number of trailing zeros in the coefficient. The stored coefficient is not
guaranteed normalised (arithmetic may keep `1.440` as `1440 * 10^-3`), and the
raw word would falsely reject `1000 * 10^-16384` = `1e-16381`, which
PostgreSQL can store. Without the accessor the adapter can still bind display
text and let the server raise 22003, but that is a server error, not a
deterministic adapter code. See §10, O6.

Positive magnitude is not a problem: obo's maximum is exponent 32767 plus 38
significant digits (~32,805 integer digits), below PostgreSQL's ~131,072-digit
unconstrained limit. Constrained precision `p <= 1000` is handled by
`decimal::check`.

### 3.7 PostgreSQL error mapping

| Code | Name | Meaning |
|---:|---|---|
| 2 | adapter plumbing | row/column out of range, stale/poisoned token, malformed packet, size |
| 3 | allocation | adapter/packet/buffer allocation failed |
| 1001 | `DB_NULL` | SQL NULL (defensive; prefer `is_null`) |
| 1002 | `DB_NOT_DECIMAL` | result OID is not 1700, or a non-decimal storage class |
| 1003 | `DB_BINARY` | decimal read requested from `execute_binary` rows |
| 1004 | `DB_NONFINITE` | `NaN`, `Infinity`, `-Infinity` |
| 1006 | `DB_TEXT_INVALID` | empty/NUL/non-ASCII/grammar-invalid text |
| 1007 | `DB_PRECISION` | more than 38 significant digits |
| 1008 | `DB_EXPONENT` | nonzero exponent outside `int16` |
| 1009 | `DB_DISPLAY_RANGE` | `dscale` outside `uint16` |
| 1010 | `DB_SCALE` | `check`: value would need rounding at declared scale |
| 1011 | `DB_RANGE` | `check`: integer part exceeds declared precision |
| 1012 | `DB_TYPMOD` | invalid typmod or invalid `(p,s)` metadata |
| 1013 | `DB_PG_RANGE` | value not representable in PostgreSQL numeric (`exp < -16383`) |

A SQL execution failure remains what it already is: `execute()` returns
`ok(Rows)` with `status() == PGRES_FATAL_ERROR` and copied
`error_message()` (`db/README.md:103-106`). `decimal()` never folds a server
error into a decimal error; callers must check `status()` first. The codes
above are deterministic data/plumbing codes, not SQLSTATEs.

## 4. SQLite

### 4.1 Storage classes and affinity

SQLite has no decimal type. The supported representation is **TEXT** containing
the exact text from §2.2. A value read from TEXT is parsed by the same
`decimal::parse`; TEXT survives byte-for-byte, including trailing zeros and
`dscale`.

Storage-class rules for `decimal(col)`:

| `sqlite3_column_type` | Action |
|---|---|
| `SQLITE_NULL` (5) | NULL (1001 if `decimal` is called directly) |
| `SQLITE_TEXT` (3) | read `column_text`/`column_bytes` exact bytes; parse |
| `SQLITE_INTEGER` (1) | read SQLite's exact i64-to-text rendering; parse with `dscale = 0` |
| `SQLITE_FLOAT` (2) | reject `1005` (`INEXACT_STORAGE`); never call `sqlite3_column_double` |
| `SQLITE_BLOB` (4) | reject `1002`; decimal is not a BLOB |

INTEGER is accepted because SQLite's documented INTEGER→TEXT rendering is the
exact ASCII i64 (`db/README.md:167-172`), not a float. It necessarily has
`dscale = 0`.

**Affinity traps** (from `sqlite.org/datatype3.html` §3):

- a declared type `TEXT`, `VARCHAR`, `CHAR`, `CLOB` gives TEXT affinity: text
  is stored as text;
- `NUMERIC`, `DECIMAL(p,s)`, `BOOLEAN`, `DATE`, `DATETIME`, or an unknown
  declared type gives NUMERIC affinity: a well-formed numeric text is converted
  to INTEGER or REAL. `1.2300` becomes REAL `1.23` (dscale lost); an integer
  outside the signed 64-bit range becomes REAL; a value beyond about 15.95
  significant decimal digits loses digits irreversibly;
- `INTEGER`/`INT` affinity behaves like NUMERIC for storage;
- `REAL`/`DOUBLE`/`FLOAT` affinity forces REAL;
- no declared type or `BLOB` gives BLOB affinity (TEXT is preserved).

Therefore: **declare decimal columns as `TEXT`** (preferably in a `STRICT`
table). The adapter cannot change a column's affinity at bind time; a TEXT bind
into a NUMERIC-affinity column is converted by SQLite before storage. The
tests must demonstrate the trap, not hide it. `PRAGMA table_info`/declared-type
inspection is not part of the binding path.

SQLite TEXT has no numeric ordering: `ORDER BY decimal_text` is lexicographic
(`"10" < "9"`), and canonical scientific fallback is not sortable numerically.
Numeric ordering must happen in obo after parsing, or use a separate exact
numeric key later; do not `CAST(... AS NUMERIC)`.

### 4.2 Binding

`bind_text` already copies exact bytes and rejects embedded NUL
(`sqlite.obx:229-238`, `sqlite_adapter.c:410-446`). Add free functions (not
methods: decimal method parameters are rejected in v1,
`obo/src/obo.obo:11483-11484`):

```obo
fn bind_decimal(s: &Stmt, index: int, value: decimal): Result[bool]
fn bind_decimal_checked(s: &Stmt, index: int, value: decimal,
                        p: int, s: int): Result[bool]
```

- `bind_decimal` formats to display/canonical text and calls the existing
  `bind_text` path; the bound storage class is TEXT.
- `bind_decimal_checked` first calls `decimal::check(value, p, s)` and returns
  `1010`/`1011`/`1012` if the declared schema would round or overflow; it does
  not rely on SQLite (which has no `(p,s)` enforcement).
- NULL uses `bind_null`; zero binds `"0"` or `"0.0000"`; empty text is never a
  decimal and must not be bound by these functions.
- Never bind REAL. A REAL bind is the float path and is forbidden.

### 4.3 Read-back

`Stmt.decimal(col)` performs:

1. `column_type(col)` bounds-checked (`sqlite.obx:262-266`); `SQLITE_NULL` →
   `1001`; `SQLITE_FLOAT` → `1005`; `SQLITE_BLOB` → `1002`;
2. for TEXT/INTEGER, `column_value(col)` (which uses
   `sqlite3_column_text` + `sqlite3_column_bytes`;
   `sqlite_adapter.c:480-491`) or `column_bytes` for exact TEXT bytes;
3. reject empty/NUL/non-ASCII as `1006`;
4. special strings `NaN`/`Infinity`/`-Infinity` → `1004`;
5. `decimal::parse`; map statuses as in §3.7;
6. no typmod check (SQLite has none).

`column_value` returns `Result[?[]byte]` with nil for NULL and an empty array
for empty text (`sqlite.obx:275-290`); `column_text` is the string convenience
and rejects NUL (`sqlite.obx:295-302`). Decimal read uses the byte form so that
NUL/empty are classified consistently, and checks `column_type` **before**
asking SQLite for text: `sqlite3_column_text` on a REAL formats it to about 15
significant digits, which would silently lose exactness
(`sqlite.org/c3ref/column_blob.html`).

The four storage classes remain distinguishable; `typeof()` in SQL is a useful
test oracle. No silent conversion is permitted: REAL is not rounded, truncated
or formatted; it is rejected.

### 4.4 SQLite error mapping

Same decimal codes as §3.7, plus:

| Code | Name | Meaning |
|---:|---|---|
| 1005 | `DB_INEXACT_STORAGE` | SQLite REAL storage class |
| 1002 | `DB_NOT_DECIMAL` | SQLite BLOB storage class or non-decimal input |

SQLite's own `SQLITE_*` codes remain the result of `prepare`/`step`/`bind`
failures (`sqlite.obx:15-41`); the decimal codes above are returned only by the
decimal helpers. Plumbing codes 2 and 3 keep their existing meaning. Because
SQLite returns codes such as 7/8/9 for `NOMEM`/`READONLY`/`INTERRUPT`, the
decimal namespace deliberately starts at 1000 to avoid collisions.

## 5. Shared contract

### 5.1 One grammar, two emitters

Both adapters use the same `encode_decimal(value, max_dscale)` helper:

```obo
fn encode_decimal(value: decimal, max_dscale: int): Result[string] {
    let f = decimal::format(value)
    match f {
        ok(s) {
            let ds = fraction_digits(s)
            if max_dscale < 0 || ds <= max_dscale {
                return ok(s)
            }
            // The value is exactly representable; only the display scale is not.
            if value != 0.0 && decimal::exponent(value) < -max_dscale {
                return err("value is not representable at this scale", 1013)
            }
            return ok(decimal::canonical(value))
        }
        err(m, c) {
            if c != 3 { return err(m, c) }          // only INEXACT falls back
            if value != 0.0 && decimal::exponent(value) < -max_dscale {
                return err("value is not representable at this scale", 1013)
            }
            return ok(decimal::canonical(value))
        }
    }
}
```

`max_dscale = 16383` for PostgreSQL, `65535` for SQLite. `fraction_digits`
counts the characters after the point in `format`'s fixed text (0 when there
is no point); it is a local helper, not a new decimal API. `decimal::exponent`
is a prerequisite (§3.6, §8). The fallback is not a rounding step: canonical
is exact by construction, and `format` has already returned `INEXACT` rather
than dropping digits. A `format` error other than `INEXACT` is unreachable for
a valid 24-byte value; if one occurs, map it to `1009` rather than propagate a
core status into the adapter namespace.

### 5.2 NULL semantics

- NULL is not zero, not empty text, and not an error value. `is_null` or the
  `?[]byte` nil from `value`/`column_value` identifies it.
- Binding NULL uses `param_null()`/`param_decimal_null()`/`bind_null`; binding
  zero uses decimal text; empty text is rejected.
- A SQL `NULL` result cell still has the numeric OID/typmod, so type checks
  can run before the NULL check; the value check must run after.

### 5.3 Display-scale preservation

| Path | Recovered `dscale` |
|---|---|
| PG text, `s >= 0` | exact (`s` fraction digits printed) |
| PG text, `s < 0` | 0; scale is metadata only |
| PG text, unconstrained | the value's stored dscale, as printed |
| PG canonical fallback | derived from the exponent, not the original cap |
| SQLite TEXT, display form | exact |
| SQLite TEXT, canonical form | derived from the exponent |
| SQLite INTEGER | 0 |

A round trip is therefore **value-exact**, not necessarily display-exact. Tests
compare values with `==` and canonical text; they assert display scale only
where the path guarantees it. PostgreSQL cannot preserve `dscale > 16383`, and
no adapter can preserve a `dscale` that was already capped by arithmetic
(§4.1 of the decimal design).

The following are not recoverable from a database round trip and must not be
promised: a `dscale` above the transport limit; a negative column scale
(metadata only); a constrained column's precision when the result is an
expression or an unconstrained column; the original spelling (`+1`,
`0001.2300`, `1.`); the choice between display and canonical fallback; the
declared type name or domain OID; and nullability (absent from `PGresult`).

### 5.4 Sizes, bounds and NUL

- SQL text 1 MiB; PostgreSQL parameters 32; value buffers 16 MiB. SQLite
  parameters use SQLite's own variable limit and the adapter's index check.
- Decimal parameter text is validated to `1..98342` bytes; values outside are
  rejected before allocation.
- PostgreSQL unconstrained output can reach ~147 KB; the 16 MiB buffer and the
  38-digit/`int16` parse limits bound what is accepted.
- SQLite TEXT/BLOB lengths come from `sqlite3_column_bytes`; embedded NUL is
  detected from the length, not from `strlen`.
- PostgreSQL `PQgetvalue` bytes are length-delimited but numeric text cannot
  contain NUL; the adapter checks anyway and returns `1006`.
- Empty text is never zero and never NULL.

### 5.5 Composition with `sql` templates

The approved template design binds a value as a parameter and splices only a
`Sql` fragment (`template-literals-design.md:118-142`, `:425-439`):

```obo
let q = sql`SELECT * FROM account WHERE balance = ${amount}`
```

When `SqlArg` gains its decimal case, it must be `Decimal { v: decimal }`
(`template-literals-design.md:318-328`, `:434-439`), rendered by the adapter
through the same `encode_decimal` and OID 1700. It must **not** be preformatted
into SQL text, quoted, or turned into a float. A `Sql` fragment is raw
structure; a decimal is always a bind. NULL in a template is `sqlNull()` or a
future nullable wrapper, never the text `"NULL"`. Until decimal record/variant
fields exist, a decimal hole in `sql` is a compile error rather than a silent
stringification. `sql` stays dialect-neutral; the PostgreSQL adapter chooses
`$n`/OID 1700 and the SQLite adapter chooses `?n`/TEXT.

## 6. API sketches (0.11/0.12 obo)

All types below are ordinary records with `int`, `bool`, `string` or `[]byte`
fields; none has a decimal field, because that is rejected in 0.12
(`obo/src/obo.obo:5448`). Free functions take decimal parameters; methods may
**return** `Result[decimal]` but may not take or return a bare `decimal`
(`obo/src/obo.obo:11483-11484`).

### 6.1 PostgreSQL additions

```obo
pub const PARAM_DECIMAL = 5
pub const PARAM_DECIMAL_NULL = 6

// Error codes specific to the decimal boundary.
pub const DEC_DB_NULL = 1001
pub const DEC_DB_NOT_DECIMAL = 1002
pub const DEC_DB_BINARY = 1003
pub const DEC_DB_NONFINITE = 1004
pub const DEC_DB_TEXT_INVALID = 1006
pub const DEC_DB_PRECISION = 1007
pub const DEC_DB_EXPONENT = 1008
pub const DEC_DB_DISPLAY_RANGE = 1009
pub const DEC_DB_SCALE = 1010
pub const DEC_DB_RANGE = 1011
pub const DEC_DB_TYPMOD = 1012
pub const DEC_DB_PG_RANGE = 1013

// Result metadata; all-int so it is representable today.
pub type DecimalType = ref record {
    precision: int
    scale: int
    constrained: bool
}

pub fn param_decimal(value: decimal): Result[Param]
pub fn param_decimal_checked(value: decimal, p: int, s: int): Result[Param]
pub fn param_decimal_null(): Param

pub fn (r: &Rows) decimal_type(col: int): Result[DecimalType]
pub fn (r: &Rows) decimal(row: int, col: int): Result[decimal]
```

SQLite exports the same `DEC_DB_*` constants plus
`DEC_DB_INEXACT_STORAGE = 1005`. `Rows` gains one field; internal constructors
set it:

```obo
pub type Rows = ref record { tok: int; fmt: int }   // fmt 0 text, 1 binary
```

`param_decimal` is a free function because the value would otherwise be a
decimal argument to a method. `Rows.decimal` is a method returning
`Result[decimal]`, which is allowed (the return type is `Result`, not decimal).
`decimal_type` returns `constrained = false`, `precision = 0`, `scale = 0` for
typmod `-1`.

Implementation shape (illustrative; valid 0.12 constructs):

```obo
fn pg_decimal_text(value: decimal): Result[string] {
    return encode_decimal(value, 16383)
}

pub fn param_decimal(value: decimal): Result[Param] {
    let txt = try pg_decimal_text(value)
    let raw = encode_string(txt)
    if has_nul(&raw) { return err("decimal text contains an embedded NUL", 1006) }
    return ok(new Param{kind: PARAM_DECIMAL, i: 0, data: raw})
}

pub fn (r: &Rows) decimal(row: int, col: int): Result[decimal] {
    if r.fmt != 0 { return err("binary numeric result is not supported", 1003) }
    let meta = try r.decimal_type(col)
    let isn = try r.is_null(row, col)
    if isn { return err("SQL NULL", 1001) }
    let b = try r.bytes(row, col)          // non-optional []byte; rejects NULL
    if len(b) == 0 { return err("empty numeric text", 1006) }
    if has_nul(&b) { return err("numeric text contains an embedded NUL", 1006) }
    let txt = string(b)
    if txt == "NaN" || txt == "Infinity" || txt == "-Infinity" {
        return err("non-finite numeric is not representable", 1004)
    }
    let d = try decimal::parse(txt)
    if meta.constrained {
        let checked = try decimal::check(d, meta.precision, meta.scale)
        return ok(checked)
    }
    return ok(d)
}
```

`try` propagates the parse/check codes unchanged; the final implementation maps
`3/6/7` from `check` to `1010/1012/1011` and `4/7/8/9` from `parse` to
`1006/1007/1008/1009`, as specified in §3.4. The sketch omits that mapping for
readability; the mapping is normative.

### 6.2 SQLite additions

```obo
pub fn bind_decimal(s: &Stmt, index: int, value: decimal): Result[bool]
pub fn bind_decimal_checked(s: &Stmt, index: int, value: decimal,
                            p: int, s: int): Result[bool]
pub fn (s: &Stmt) decimal(col: int): Result[decimal]
```

Implementation shape:

```obo
pub fn bind_decimal(s: &Stmt, index: int, value: decimal): Result[bool] {
    let txt = try encode_decimal(value, 65535)
    return s.bind_text(index, txt)
}

pub fn (s: &Stmt) decimal(col: int): Result[decimal] {
    let t = try s.column_type(col)
    if t == SQLITE_NULL { return err("SQL NULL", 1001) }
    if t == SQLITE_FLOAT { return err("REAL storage is not exact", 1005) }
    if t == SQLITE_BLOB { return err("BLOB is not decimal text", 1002) }
    let v = try s.column_value(col)
    if v == nil { return err("SQL NULL", 1001) }
    let b = v
    if len(b) == 0 { return err("empty decimal text", 1006) }
    if has_nul(&b) { return err("decimal text contains an embedded NUL", 1006) }
    return decimal::parse(string(b))
}
```

Again, parse codes are mapped to `1006-1009` by the final implementation.

### 6.3 The NULL wrapper question

The v1 shape is **error-only plus an explicit NULL test**:

```obo
let isn = rows.is_null(row, col)
match isn {
    ok(isnull) {
        if isnull {
            // SQL NULL: distinct from 0 and from any decimal error
        } else {
            let d = rows.decimal(row, col)
            match d {
                ok(v) { /* use v */ }
                err(m, c) { /* c is never 1001 on this path */ }
            }
        }
    }
    err(m, c) { /* row/column/plumbing error */ }
}
```

A tagged wrapper cannot be written today. Records and closed variants reject
decimal fields (`obo/src/obo.obo:5448`), and `?decimal` is not representable
(`decimal-language-design.md:42`). The two candidate shapes for later are:

```obo
// Later, after decimal fields land (M2 containers). Not valid today.
pub type DecimalOrNull = closed {
    Missing { }
    Present { v: decimal }
}
pub fn (r: &Rows) decimal_or_null(row: int, col: int): Result[DecimalOrNull]
```

or an owned record `{ present: bool; value: decimal }`. Both add a 24-byte
decimal field and need the field descriptor/stride work. Until then,
`Result[decimal]` with a reserved NULL code is only a defensive fallback; it is
not proposed as a public wrapper because it conflates NULL with failure in the
`err` arm and forces callers to special-case code `1001`.

## 7. Tests

### 7.1 Shared text and value vectors

Use the existing independent model `obo/experimental/decimal/tests/decimal_text_oracle.py`
(`parse_text`, `canonical_text`, `display_text`, `build_text_cases`) and extend
it with:

- `check(value,p,s)` vectors from §3.5, including negative scale;
- typmod encode/decode vectors from §3.1;
- PG `dscale` boundary (`16383` accept, `16384` reject);
- SQLite affinity vectors.

Parser/format cases (all must be no-float, exact):

| Input | Status | Value / note |
|---|---|---|
| `0` | ok | zero, dscale 0 |
| `0.00` | ok | zero, dscale 2 |
| `-0` | ok | positive zero |
| `1.2300` | ok | dscale 4; display `1.2300`; canonical `1.23` |
| `0001.2300` | ok | accepted on read; display `1.2300` |
| `1.5e3` | ok | dscale 0; display `1500` |
| `1.2300e-2` | ok | dscale 6; display `0.012300` |
| 38 nines | ok | maximum coefficient |
| 39 significant digits | 7 | precision |
| `1e32767` | ok | exponent boundary |
| `1e32768` | ok | rescued coefficient `10`, exp 32767 |
| `1e32804` | ok | rescued coefficient `10^37`, exp 32767 |
| `1e32805` | 8 | cannot rescue within 38 digits |
| `1e-32768` | ok | minimum exponent |
| `1e-32769` | 8 | below `int16` |
| `1e-16383` | ok | PG lower boundary |
| `1e-16384` | ok in obo, `1013` for PG binding | not PG-representable |
| `0e999999` | ok | zero keeps exponent 0, dscale 0 |
| `0.` + 65535 zeros | ok | dscale boundary |
| `1.` or `.5` | ok by `decimal::parse`; see O7 | not emitted |
| `+1`, `-1` | ok | sign |
| empty, `+`, `-`, `.`, `1..2`, `1e`, `1e+` | 4 | syntax |
| ` 1`, `1 `, `1_000`, `0x10` | 4 | no whitespace/underscore/hex |
| `NaN`, `Infinity`, `-Infinity` | 4 (special-cased as 1004 on DB read) | non-finite |
| `1\x002`, `1é` | 4 | NUL / non-ASCII |

### 7.2 PostgreSQL tests

Deterministic fake-libpq C tests (`obo/experimental/db/tests/postgres_adapter_test.c`):

- decimal parameter kind 5: bytes `1.2300`, `paramTypes == 1700`,
  `paramFormats == 0`, `paramLengths == 6`, exact bytes;
- decimal NULL kind 6: `paramTypes == 1700`, `paramValues == NULL`,
  `isnull == 1`;
- invalid decimal packets: empty, `+1`, `1..2`, `1e`, `1_000`, `0x10`, `NaN`,
  `Infinity`, `1\x00`, 98,343-byte text → `pg_exec_typed` returns `-2` and
  leaks no buffers;
- kind 7 and 33 parameters → `-2`;
- existing text/int/bool/bytea/NULL OIDs unchanged.

Live SELECT-only tests (opt-in `CINTEROP_TEST_DSN`; no DDL):

- `SELECT $1::numeric` with bound `1.2300` → text `1.2300`, OID 1700,
  typmod -1, decimal dscale 4;
- `SELECT $1::numeric(10,4)` with `1.2300` → `1.2300`; with `1.23456`,
  unchecked the server rounds to `1.2346` (demonstrates why
  `param_decimal_checked` exists), checked returns `1010` before sending;
- `SELECT $1::numeric(2,-3)` with `12000` → text `12000`, typmod 133121,
  scale -3; with `1500`, checked returns `1010`;
- `SELECT 1.0000::numeric`, `1::numeric(10,4)`, `1.2345::numeric(2,3)`
  (server overflow) — read/mapping;
- `SELECT 'NaN'::numeric, 'Infinity'::numeric, '-Infinity'::numeric` →
  `1004` each;
- `SELECT NULL::numeric, 0::numeric` → NULL code and zero dscale 0;
- `SELECT 1e-16383::numeric` → ok; `SELECT 1e-16384::numeric` → server error
  (and a bound `1e-16384` returns `1013` before the call);
- 38-digit value `999...9::numeric` → ok; 39 significant digits → `1007`;
  `SELECT 1e40000::numeric` → `1008` (PostgreSQL can store it; obo's exponent
  range cannot); `1e-16384::numeric` is rejected by the server instead;
- `execute_binary("SELECT 1.23::numeric")` then `decimal()` → `1003`;
- typmod decode: `decimal_type` on the cast columns returns
  `(10,4)`, `(2,-3)`, `(1,0)`, unconstrained;
- a numeric expression with typmod -1 is not checked; a value that would not
  fit a constrained column can only be observed through a cast and is rejected
  by the server before read-back.

Oracle: `psql -At` for `numeric::text`, `pg_typeof`, and `format_type` on
cast expressions; `pg_attribute.atttypmod` only for pre-existing objects, never
for a table created by the test. Python `decimal` is only a secondary check,
never the authority. No server configuration, role, database, schema or data
creation.

### 7.3 SQLite tests

Real `libsqlite3.so.0`, in-memory and `/tmp` file, with Python `sqlite3` as an
independent `typeof`/`quote` oracle (existing pattern,
`db/README.md:205-227`):

- TEXT round trip: bind `1.2300`, `typeof(v) == 'text'`, read exact bytes,
  dscale 4, value equality, canonical `1.23`;
- TEXT column with `TEXT` affinity stores text unchanged;
- NUMERIC-affinity trap: `CREATE TABLE t(v NUMERIC)`, bind text `1.2300`,
  `typeof(v) == 'real'`, `v == 1.23` (REAL), adapter `decimal()` returns
  `1005`; the test asserts the trap and the rejection;
- INTEGER storage: `SELECT 9223372036854775807` → text exact, dscale 0;
  `-9223372036854775808`; `0`; `typeof == 'integer'`;
- REAL storage: `SELECT 1.23` (REAL) → `1005`; `1e300`, `-0.0` → `1005`;
- BLOB storage: `x'312e3233'` → `1002`;
- NULL vs empty: `NULL` → `1001` (or nil from `column_value`), `''` →
  `1006`, `'0'` → zero dscale 0;
- bad text: `' 1'`, `'1 '`, `'1_000'`, `'0x10'`, `'1..2'`, `'NaN'`,
  `'Infinity'`, `'1\x002'` (bound with `bind_text`/BLOB or inserted via SQL) →
  `1004`/`1006` as appropriate;
- 38-digit boundary, 39 significant digits (`1007`), dscale 32768 and 65535
  TEXT round trips, large magnitude `1e32767` / `1e-32768`;
- `decimal::div(1.0, 3.0, 32768, HALF_AWAY)` (38 significant digits at
  exponent -32768) round-trips through SQLite TEXT but returns `1013` on a
  PostgreSQL bind, because PostgreSQL cannot store digits below `10^-16383`;
- cross-adapter: bind the same values to PG numeric and SQLite TEXT, read
  both, compare decimal `==` and canonical text; assert display scale
  separately where guaranteed;
- no float path: source scan for `strtod`, `atof`, `sscanf`, `%f`,
  `column_double`, `numeric_recv`; `nm -u` check that the adapter binaries do
  not import `strtod`/`pow`/`ldexp`.

### 7.4 Sanitizers and ownership

Run the existing ASAN/UBSAN C suites with the new packet kinds and invalid
vectors; assert no buffer leaks (`cres_live_count() == 0`) and no double free
on every rejection path. Statement/connection live counters must return to 0.

## 8. Phasing and dependencies

**P0 — in-memory decimal text surface (must land first).**

The core already implements `dec_parse`, `dec_format_display` and
`dec_format_canonical` (`obo/runtime/decimal24_core.inc:1970`, `:2314`,
`:2217`; private reference `obo/experimental/decimal/dec24_text.c`), but 0.12
exposes only `div`, `quantize`, `toInt` and `fromInt`
(`obo/LANGUAGE.md:97-100`; `obo/src/obo.obo:3522-3559`). The DB boundary needs:

- `decimal::parse(s: string) -> Result[decimal]` with the §2.1 grammar and
  statuses;
- `decimal::format(d: decimal) -> Result[string]` (display; `INEXACT` on cap);
- `decimal::canonical(d: decimal) -> string` (exact minimal text);
- `decimal::check(value: decimal, p: int, s: int) -> Result[decimal]` with
  `p 1..1000`, `s -1000..1000`, no rounding;
- `decimal::exponent(d: decimal) -> int` (normalised exponent) and
  `decimal::dscale(d: decimal) -> int` (stored display scale) for the
  PostgreSQL `-16383` pre-check and tests.

`print`/`println` are not sufficient: they write to stdout and `string(decimal)`
is deliberately an error (`obo/src/obo.obo:7004`). No new core algorithm is
needed for parse/format; the work is the builtin surface, Result mapping and
tests. This tier should also add the `check` predicate to the Python oracle.

**P1 — PostgreSQL binding.**

Add `PARAM_DECIMAL`/`PARAM_DECIMAL_NULL`, `param_decimal`,
`param_decimal_checked`, `param_decimal_null`, the C grammar validator and
`paramTypes = 1700`; add packet tests. No read path yet.

**P2 — PostgreSQL read-back.**

Add `Rows.fmt`, `decimal_type`, `decimal`, typmod decode and the error mapping.
No C changes beyond what P1 needs (`pq_ftype`, `pq_fmod`, `pq_getisnull`,
`pq_value_buf` already exist).

**P3 — SQLite.**

Add `bind_decimal`, `bind_decimal_checked`, `Stmt.decimal`, storage-class
rejection and the affinity traps. Bind directly reuses `bind_text`; read
reuses `column_type`/`column_value`.

**P4 — oracles and gates.**

Extend `decimal_text_oracle.py` with `check`/typmod cases; add live PG vectors;
add Python SQLite `typeof`/`quote` vectors; run ASAN/UBSAN, live counters, and
the no-float source/`nm` checks.

**P5 — `sql` template decimal (`SqlArg.Decimal`).**

Blocked on decimal record/variant fields (M2 containers). Once available, the
renderer uses the same `encode_decimal` and the adapters' OID/TEXT paths. Until
then a decimal hole is a compile error.

### 8.1 API, schema and manifest impact

- `Param` keeps its fields; only new `kind` constants and constructors are
  added. Existing `param_text`/`param_int`/`param_bool`/`param_blob`/
  `param_null` calls are unchanged.
- `Rows` gains `fmt: int`. The experimental packages construct `Rows` only
  internally (`execute_fmt`, `make_empty_rows`); any external code that builds
  `new Rows{tok: ...}` must add `fmt`, and any `.sym` consumer must rebuild.
  This is an experimental public-type change, not a compiler format/ABI bump.
- The C declaration lists in `postgres.cbind`/`sqlite.cbind` are unchanged;
  only the hashed sources change. No new libpq/libsqlite declarations, no new
  library dependency, no `-lm`.
- Existing read helpers (`value`, `text`, `bytes`, `column_value`,
  `column_text`, `column_bytes`, `column_int`) keep their semantics; decimal
  helpers are additive.
- No database schema migration is performed or prescribed by the adapter.
  PostgreSQL columns should be `numeric` (constrained only where the caller
  supplies the matching `(p,s)` to a checked bind); SQLite columns should be
  declared `TEXT`. Existing NUMERIC-affinity SQLite data that SQLite already
  converted to REAL cannot be recovered by the adapter and reads as `1005`.

**Later (explicitly not this work):** binary numeric send/recv, COPY, arrays,
intervals, domains over numeric, catalog/schema introspection, prepared
statement caches, `DecimalOrNull` closed type, nullable-column metadata,
arbitrary-precision (>38 digit) language values, decimal `<->` SQL Server /
Oracle / ASE adapters.

## 9. Rejected alternatives

- **Binary numeric wire (`numeric_send`/`numeric_recv`).** It is exact but
  PostgreSQL-specific (base-10000 digits, its own header and digit encoding),
  needs a second implementation and byte-format goldens, and buys nothing over
  text at this scale. Text is already exact and human-auditable. Deferred.
- **Float transport (`double`, `REAL`, `sqlite3_column_double`, `strtod`).**
  Lossy, violates the no-float money rule, and silently changes values.
- **String-only, unvalidated decimal.** Passing arbitrary TEXT to PG relies on
  the server's permissive input (whitespace, underscores, `NaN`); reading
  arbitrary TEXT into obo without `parse` bypasses the 38-digit/exponent
  contract. The adapter validates the grammar and parses.
- **Implicit rounding.** Letting PG's `numeric(p,s)` round, or SQLite's
  NUMERIC affinity convert to REAL, hides value changes. `check` rejects;
  affinity is documented and REAL is rejected.
- **NULL as zero or empty.** Destroys the NULL distinction and is
  unrecoverable. NULL is a separate code/state.
- **Client-side formatting without typmod.** Formatting `1.23456` and letting a
  `numeric(10,4)` column round is exactly the silent-rounding failure. The
  typmod is read and `check` gates the value; writes use checked params.
- **`Result[decimal]`'s `err` arm as a general NULL wrapper.** It conflates
  NULL with failure; only a reserved defensive code is allowed.
- **`?decimal`, sentinel zero, or `decimal` as a pointer/handle.** Not
  representable and against the approved value model.
- **SQLite `DECIMAL(p,s)`/`NUMERIC` columns as the storage policy.** Affinity
  converts to INTEGER/REAL; only TEXT is exact.
- **Interpolating decimal text into SQL.** Injection and formatting hazards;
  always bind.
- **`text()`/`string(cell)` for decimal reads.** Loses NUL classification and
  can hide empty vs NULL; use length-delimited bytes.

## 10. Open choices and recommendations

| # | Choice | Recommendation |
|---|---|---|
| O1 | v1 NULL representation: error-only vs tagged wrapper | **Error-only** with `is_null` first and defensive code 1001. The closed `DecimalOrNull` needs decimal record/variant fields; add it in M2, not now. |
| O2 | PostgreSQL text vs binary | **Text**, both parameters and results. Revisit binary only with a measured need and byte-format goldens. |
| O3 | SQLite TEXT enforcement | Read-time storage-class checks plus documentation/tests; do not auto-inspect declared types on bind. Optional `column_decltype()` for diagnostics later. Accept INTEGER; reject REAL/BLOB. |
| O4 | Automatic typmod check on read | **Yes.** A conforming server value passes; a mismatch is a hard `1010`/`1011`, never a silent fit. |
| O5 | Typed decimal NULL parameter | **Add `PARAM_DECIMAL_NULL`** (OID 1700, NULL value) for `SELECT $1`; keep `param_null()` for inference contexts. |
| O6 | `decimal::exponent`/`dscale` accessors | **Add them.** They are two word reads from the existing 24-byte layout and make the PG `-16383` boundary deterministic (`1013`). Without them, bind display text and map server SQLSTATE 22003. |
| O7 | Read grammar: accept bare `.5`/`1.` | **Accept** (matches `decimal::parse`; PG output never contains them). Rejecting would require a second scanner for no value gain. Never emit them. |
| O8 | Error-code namespace | **1000+** for decimal boundary codes; keep SQLite `SQLITE_*` and PG status separate. Do not reuse core 1/3/4/5/6/7/8/9 in adapter results. |
| O9 | SQLite INTEGER read | **Accept** as exact scale-0; document dscale 0 and the NUMERIC-affinity conversion. |
| O10 | Canonical fallback notation | **Scientific** (existing core policy) for values whose fixed text exceeds 128 bytes. PG accepts it; SQLite stores it; it is exact. Document that it is not numerically ordered as TEXT. |
| O11 | `database/sql`-style scanning | Do not add a generic scanner API in v1; explicit `decimal`/`decimal_type`/`is_null` calls keep the contract auditable. |

## 11. References

Repository (line numbers at baseline `51ce9e7`):

- `obo/design/decimal-language-design.md:26-28` layout, `:42` no `?decimal`,
  `:57` value equality, `:84` no `decimal(p,s)`, `:106` scale/mode,
  `:110` statuses, `:114-119` conversions/format, `:123-132` display cap,
  `:134-142` §5 boundary, `:156-172` decisions.
- `obo/LANGUAGE.md:70-131` released decimal tier, `:2433-2436` known holes.
- `obo/src/obo.obo:1295` type, `:5382` no decimal arrays, `:5448` no decimal
  record fields, `:7004` no `string(decimal)`, `:11483-11484` no decimal method
  parameters/returns.
- `obo/runtime/decimal24_core.inc:1970` `dec_parse`, `:2217`
  `dec_format_canonical`, `:2314` `dec_format_display`.
- `obo/experimental/decimal/dec24_text.h:11-52` private grammar and format
  protocol; `dec24_text.c:44-156` parse, `:291-336` canonical, `:388-418`
  display.
- `obo/experimental/decimal/tests/decimal_text_oracle.py` independent model
  (`parse_text`, `canonical_text`, `display_text`, `build_text_cases`).
- `obo/experimental/db/README.md:19-23` exact bytes/no float, `:96-119`
  PG params/OIDs/values, `:167-172` SQLite column text, `:243-252` known limits.
- `obo/experimental/db/postgres/postgres.obx:32` OID 1700, `:39-49` Param,
  `:103-128` constructors, `:138-180` append_param, `:184-202` packet,
  `:278-285` execute, `:316-370` read, `:381-391` OID/typmod.
- `obo/experimental/db/postgres/postgres_adapter.c:3-17` packet/OIDs,
  `:78-89` integer text validation, `:146-195` switch, `:197-198`
  `PQexecParams`.
- `obo/experimental/db/sqlite/sqlite.obx:43-47` storage classes, `:217-247`
  binds, `:262-329` column reads.
- `obo/experimental/db/sqlite/sqlite_adapter.c:410-446` text/blob bind,
  `:480-503` column text/blob, `:506-510` int64.
- `obo/design/template-literals-design.md:118-142` SQL binds/fragments,
  `:318-328` `SqlArg`, `:425-439` database values.

External:

- PostgreSQL 18 numeric types: <https://www.postgresql.org/docs/current/datatype-numeric.html>
  (precision/scale, negative scale, rounding, NaN/Infinity, limits).
- PostgreSQL `numeric.c`: <https://raw.githubusercontent.com/postgres/postgres/master/src/backend/utils/adt/numeric.c>
  (`make_numeric_typmod`, `numeric_typmod_precision/scale`, `numeric_in/out`,
  `apply_typmod`, `NUMERIC_DSCALE_MAX`).
- PostgreSQL `numeric.h`: <https://raw.githubusercontent.com/postgres/postgres/master/src/include/utils/numeric.h>
  (`NUMERIC_MAX_PRECISION=1000`, scale `-1000..1000`).
- SQLite datatypes and affinity: <https://www.sqlite.org/datatype3.html>
  (storage classes, NUMERIC/TEXT/REAL affinity, ~15.95 significant digits).
- SQLite `sqlite3_column_*`: <https://www.sqlite.org/c3ref/column_blob.html>
  (type conversions, `column_bytes`, NUL-terminated text, REAL rendering).
