# obo

obo is a small Oberon-derived language and compiler for Linux x86-64, built to
answer one question: **can a language keep Oberon's simplicity and manage memory
without a garbage collector, and without Rust's complications?**

It uses single ownership, borrowed references and run-time-checked handles — the
group borrowing model designed by Nick Smith and explained by Evan Ovadia in
[Valen's Memory Safety](https://verdagon.dev/blog/valen-group-borrowing) — with
no collector, no lifetime annotations and no `unsafe`. The compiler is
single-pass: no AST, no intermediate representation, and x86-64 machine code
written straight into a static ELF executable.

**History:** obo grew out of **obx**, the original garbage-collected dialect and
compiler. obx is kept in `obx/` as the historical reference; the current compiler
is `obo/src/obo.obo`. See [HISTORY.md](HISTORY.md) for the obx language,
architecture and tests, the ownership fork, milestone chronology and earlier
measurements.

## Current state (1 November 2026)

**obo 0.19 is released**, symbol format 12, ABI 1 (757,761 bytes, sha256
`8327ca50…`), adding decimal function values, lambda parameters/returns and
24-byte decimal capture snapshots. It also carries decimal method and
interface-member parameters and returns, impure interface members, decimal
record fields, `[]decimal` arrays,
decimal globals and literal constants, compiler-known `sql`/`html` template
literals, the decimal text API, private-by-default record fields, exact 24-byte
decimals, Stage B capture lists, the HTML escaping library and the configurable
heap runtime. The D4a method/interface stage, the D5 `lib/decimals.obx` library
and the D6 database read bridge are all released. The experimental cinterop v2
line adds `foreign` handles,
generated `_out` records and a leak census in its own packages (libpq, SQLite,
PostgreSQL and SQL Server via ODBC); it is a trusted-build probe with no threads
and no stable API. The runtime cleanup removed the disabled collector and `gc`
builtins.

The seed is **promoted at `ba02031`**: `seed/obo` and the compiler are the same
757,761-byte 0.19 image (sha256 `8327ca50…`), the D4b merge is `147f7fc`, and
the image reaches its own fixed point. The immediate pre-0.19 seed was the
757,761-byte 0.18 image (sha256 `ca513536…`, promoted at `769a359`), which built
the 0.19 image once; it is historical now. The immediate pre-0.18 seed was the
757,761-byte 0.17 image (sha256 `29ffadc8…`, promoted at `e0b9686`), which built
the 0.18 image once, and the 0.15 seeds built the 0.16 line. Floats were released
at `0.10`/format 6; Stage B is `0.11`/format 6; decimal is `0.12`/format 6;
private-by-default fields are `0.13`/format 7; the decimal text API is
`0.14`/format 7; templates are `0.15`/format 8; decimal storage (fields, arrays,
globals and constants) is `0.16`/format 9; impure interface members are
`0.17`/format 10; decimal methods and interface members are `0.18`/format 11;
decimal function values, lambdas and captures are `0.19`/format 12.
The 0.19 compiler reads format 12 only: formats 6, 7, 8, 9, 10 and 11 are rejected,
so rebuild old `.sym` files.
Version policy: additive builtin/runtime extensions use patch releases such as
the historical 0.8.1; floats are 0.10, Stage B is 0.11, decimal is 0.12,
field visibility is 0.13, decimal text is 0.14, templates are 0.15, decimal
storage is 0.16, impure interface members are 0.17, decimal methods and
interface members are 0.18 and decimal function values and captures are 0.19.

- **Language.** Single ownership, moves, borrowed `&T` references, checked `^T`
  handles, effect summaries, `Result[T]`/`try`, incremental I/O, modules and
  text `.sym` symbol files, methods, first-class interfaces (owning `I`/`?I`,
  borrowed `&I`/`&?I`, dynamic dispatch and effect tiers), structural function
  types and non-capturing lambda literals with indirect calls, IEEE 754
  binary64 floats with shortest round-trip formatting and a checked parser,
  Stage B explicit capture lists (`fn [a, b](...)`) with call-scoped read-only
  borrows, scalar snapshots (including one-word float snapshots) and the
  per-kind call-site firewall, the exact 24-byte `decimal` scalar tier
  (bare fractional literals, exact `+ - * %`, comparisons, explicit
  `div`/`quantize`/`toInt`, display-scale preservation), and private-by-default
  record fields with `pub` per field, mixed records, opaque `priv`/`priviface`
  `.sym` slots and constructor-only external construction, compiler-known
  `sql`/`html` template literals with typed bind parameters, fragment
  composition and contextual HTML escaping, and the 0.16 decimal storage tier:
  24-byte inline record and closed-variant fields (`priv24` in `.sym`),
  `[]decimal` arrays at a 24-byte stride, decimal globals in zero-initialised
  data, and literal or unary-minus decimal constants whose public `.sym` form
  is an exact 48-hex-digit payload. The 0.17 impure interface-member tier adds
  `impure fn`/`impure mut fn`: an impure member has the maximum call summary, so
  a call through it invalidates every live reference and handle and makes the
  caller impure; any signature-matching method may implement it, and the pure
  and plain tiers are unchanged. The 0.18 decimal-method and interface-member
  tier makes a decimal a method parameter and return value, including
  `Result[decimal]`, in every member tier; an interface call loads its vtable by
  the explicit argument byte count rather than `8*n`, and `recordParamFree`
  maps a parameter offset to its index by parameter size. The HTML escaping
  library is in `obo/lib/html.obx`. Interface handles `^I`, named function
  values and stored closures are later; D4b is released at `0.19`/format 12/
  ABI 1 (24-byte decimal function-value and capture frames), and the database
  read bridge D6 is released. An
  experimental cinterop v2 boundary adds
  `foreign` handles, owned and borrowed C pointers, body-less `native fn`
  declarations, generated `_out` records and a leak census for its own libpq,
  SQLite, PostgreSQL and SQL Server (ODBC) packages; it has no threads and no stable API. The full
  reference is [obo/LANGUAGE.md](obo/LANGUAGE.md).
- **Compiler.** 17,233 lines / 659,375 bytes of obo source (the released
  0.15 line was 15,787 / 596,288; the 0.14 line was 14,813 / 559,468).
  Numeric floats add
  the `float` type, exact literal/constant conversion, arithmetic, formatting,
  `parseFloat` and format-6 symbol support to the 0.9/format-5 lambda line;
  Stage B adds capture-list parsing, recursive incomplete/interface
  classification, scalar float snapshots and the call-site capture binding;
  0.12 adds the exact decimal type, literal parser, 24-byte ABI, operators,
  explicit request validation and `Result` APIs; 0.13 adds the `Field` visibility
  flags, source `pub` fields, the designator/construction rules, the relaxed
  private-field closure, `priv`/`priviface` writer/reader and format 7; 0.14
  adds the exact decimal text API; 0.15 adds the compiler-known `sql`/`html`
  backtick templates and format 8, and its follow-up runtime cleanup removes
  the disabled collector and `gc` builtins (16 source lines); 0.16 adds
  `sizeOf`-based 24-byte decimal field layout, the `[]decimal` array kind,
  24-byte decimal globals, literal-only decimal constants, the private
  `priv24` `.sym` marker, the `hex` constant payload and format 9; 0.17 adds
  the `impure` member marker, the maximum `freesAll`/`writesHandles` member
  summary, the impl-tier exemption, the canonical `.sym` marker and format 10;
  0.18 adds decimal method and interface-member parameters and returns
  (including `Result[decimal]`), the explicit-argument-byte vtable offset for
  interface dispatch, the parameter-index `recordParamFree` mapping with a
  fail-closed internal error, and format 11. 0.19 adds decimal function-type
  parameters and returns, the value-call cell above the hidden destination
  (destination at `16 + A`, cell at `24 + A`), the removal of the lambda
  environment pseudo-parameter, 24-byte decimal capture slots, and format 12.
  The experimental cinterop v2 work
  adds the `foreign` record kind, body-less `native fn` declarations and
  generated out records without changing the baseline symbol format or ABI. The
  immediate pre-0.19 seed, the 757,761-byte 0.18 image (sha256 `ca513536…`,
  promoted at `769a359`), built this 0.19 image once; the 0.19 image reaches its
  own fixed point and the promoted seed is the same image at `ba02031`.
- **Tools.** `cat`, `wc`, `sort`, `head`, `tail` and the partial `eza`
  reimplementation are written in obo and built by `make tools` / `make eza`.
  Differential gates compare them with GNU coreutils 9.4 (`obo/tests/tools.py`)
  and pinned eza 0.23.3 (`obo/tests/eza.py`), with recorded goldens as fallback.
- **Modules.** Both stages are implemented: source imports and exports
  (`module`, `import "Name.obx" [as alias]`, `pub`, `alias::name`, `--deps`,
  `-I`) and `.sym` files with `--check`, `--sym-dir`, `--emit-sym`, `--version`
  and staleness/version checks. A full build still compiles every module body
  from source; `--check` checks against dependencies' symbol files, including
  dependencies without sources.
- **Library and tools.** The thirteen modules under `obo/lib/` are `strings`,
  `paths`, `sort`, `time`, `cli`, `io`, `fmt`, `errors`, `fs`, `env`, `html`,
  `sqldialect` and `decimals`. `lib/decimals.obx` is the D5 library-only
  addition released on the 0.17 line (merge `ed20a4c`):
  `floor`/`ceil`/`trunc`/`round`/`roundAway`,
  `abs`/`neg`/`sign`/`isZero`/`cmp`/`min`/`max`, `sum`,
  largest-remainder `allocate`/`allocateWeighted` (the parts always sum exactly
  to the total), and `formatGrouped` with an ASCII thousands separator. It
  changes no compiler, runtime, format or seed. Each tool is a module root and
  imports only what it uses; `obo/tools/build_tools.py` compiles it directly
  with the ordinary compiler, with nothing concatenated. The exact subsets and
  caveats are in [obo/cmd/README.md](obo/cmd/README.md).
  The systems library is in `obo/experimental/sys`; P0/P1a/P1b/P2/P2q and P3
  are merged. P1b is the practical-filesystem slice; P3 adds spawn/exec,
  pipes and redirects, reaping and process groups, signalfd-backed signals,
  cwd/chdir and terminal raw mode. Threads remain design-only, and P2C
  compression is a separate optional adapter.

| Current metric | Value |
|---|---:|
| Compiler source | 17,233 lines, 659,375 bytes |
| Compiler (`--version`) | 757,761 bytes, sha256 `8327ca50…`, `obo 0.19`, sym-format 12, abi 1 |
| Seed (`--version`) | promoted obo 0.19 at `ba02031`: 757,761 bytes, sha256 `8327ca50…`, sym-format 12, abi 1; the immediate pre-0.19 seed is the 757,761-byte 0.18 image (`ca513536…`, `769a359`), which built this image once (historical) |
| Self-host fixed point | stage 2 == stage 3 == compiler == seed, 757,761 bytes, `8327ca50…`; the 0.18 seed built this image once (historical) |
| Self-compile wall time | 0.252 s one stage (5 runs, median; range 0.2473-0.2549 s) and 0.500 s for the two-stage seed → stage 2 → stage 3 build; host-dependent, measured on the 0.16 line on 1 November 2026, and carried unchanged because 0.17–0.19 changed checks, frames and decimal dispatch, not non-decimal codegen |
| Self-compile live peak | 2,590,496 bytes direct (`heap_test`); 2,590,480 bytes port comparison (budget 2,750,000) |
| Total allocated | 58,005,840 bytes direct; 58,009,088 bytes port comparison |
| Maximum resident set | 5,092 KB (measured on the 0.16 line on 1 November 2026, carried) |
| Live-memory budget | 2,750,000 bytes: 159,504 spare direct / 159,520 port comparison; the separate empty-array oracle requires a 30,000-byte same-source regression (measured 53,984 on 0.18) |
| obo suite | 1,884 `run_fork` tests; 151 `module_test`; `sym_test.py` 59/59; `sym_fuzz.py` 200/200; `port_test` 643/643 with 10 encoder groups; `port_compare` 1,859/1,859 default; seed comparisons 1,647/1,647 against the 0.18 seed measured before promotion (the flat 0.19-seed run is 1,655/1,655); 76 doc examples; 31/31 import checks; 24/24 stdlib roots |
| Float suite | `float_f1.py` 19 groups in normal and `--noregs` builds (12,580 power-of-two values each); `lambda_float_capture` passes in both modes; `memory_log_check` 4 sources |
| Seed comparison | `OBO_REF=obo/seed/obo` against the 0.18 seed before promotion is 1,647/1,647; that seed skips the 8 new 0.19 decimal function-value/capture cases. The promoted 0.19 seed's flat run is 1,655/1,655. Both flat counts omit the 204 module-tree, CLI, special, re-emit and `modules/tokens` checks the default comparison adds, so the default (self) comparison is 1,859/1,859; 212 is the 0.18-seed difference (204 + the 8 skipped 0.19 cases) |
| Mutation gates | `mutate_fork` 727/757 caught + 30 equivalent, 0 missed/stale/broken (catalog 757, including the decimal D4a/D4b mutants; five patterns that named the old frame/capture code were repointed); `mutate_port` 540/540 caught, 0 missed/stale/broken; `mutate_meta` 71/82 caught + 11 equivalent, 0 missed/stale/broken (runtime/entry.S 50/52 + 2 equivalent; compiler 21/30 + 9 equivalent); `mutate_decimal` 117/118 caught + 1 equivalent (`capture snapshot taken by address`), 0 missed/stale/broken, with an explicit baseline; `mutate_io` 36/36, 0 equivalent, 0 missed/stale/broken (including the self-host memory oracle, empty-array delta 53,984 bytes). The 0.19 catalog is green: the fork catalog stays at 757, its 30 equivalents are unchanged (the S4 receiver-load-order catalog-equivalent is still caught), the meta 11 are unchanged, and no pattern is stale or broken |
| Tools differential gate | 509 checks vs GNU coreutils 9.4; 4 memory cases |
| Tool binaries | cat 94,209; wc 98,305; sort 122,881; head 94,209; tail 106,497 bytes (static, runtime included) |
| eza E2 differential gate | own-binary gate: 482 passed, 0 failed, 72 live-only skips; pinned reference eza is not on the host `PATH` |
| eza E2 binary | 270,337 bytes (static, runtime included; sha256 `84211d5d…`; reference eza 0.23.3 is 2,294,768) |
| eza 10k-entry live peak | worst 2,184,496 bytes (Tree; Restricted long 2,184,368); `EZA_PEAK_MAX` 2,752,512 (a distinct eza cap, not the compiler budget) |
| Minimal static executable | 81,921 bytes (runtime included; a page-rounded `module mini; fn main() {}` binary) |

The compiler budget is 2,750,000 bytes live (raised from 2,500,000 on 1 November 2026), with the separate 30,000-byte
empty-array oracle unchanged. The 0.19 line peaks at 2,590,496 bytes in direct self-compiles and
2,590,480 in the port comparison: 159,504 and 159,520 bytes spare. Total
allocation is 58,005,840 bytes direct and 58,009,088 bytes in the port
comparison. No memory check was weakened. The live peak varies by a few hundred
bytes with the source path and total allocation by about 1.3 KB. The historical
lambda release budget remains 2,000,000 in its own record. The 0.15 line peaked
at 2,519,296 bytes direct (historical); the decimal storage tier raised the peak
without raising the cap again, 0.17 interface-member checking added 3,152 bytes,
0.18 decimal method/interface codegen added 1,328 bytes, and 0.19 function-value
and capture codegen added 2,912 bytes. The 0.15 runtime
cleanup is also historical (main `b61f189`); the 0.19 seed is promoted at
`ba02031`, and the 0.18 image promoted at `769a359` is historical.

### Template literals 0.15 (released)

Compiler-known `sql`/`sqlRaw` and `html`/`htmlRaw` backtick tags are released in
0.15 and still carried by the promoted 0.19 seed. SQL holes become ordered
bind parameters in public `Sql`/`SqlArg` records; literal chunks stay raw text,
`Sql` fragments splice, `sql.copy`, `sqlJoin`, `sqlWhere`, `sqlNull` and
`sqlBytes` compose explicitly, and `[]Sql` never expands implicitly. Decimal
arguments use exact `DecimalText` bytes (display form, canonical only when the
display scale was capped; never rounded); `sqlWhere` parenthesizes every
predicate. HTML holes are context-escaped for text, quoted attributes and
allow-listed URLs; URL values are validated as assembled values (a scheme may
not span holes); `Html` fragments splice only as text, `[]Html` concatenates,
and script/style/comment/name/unquoted/srcdoc/style/`on*` holes are compile
errors. `Sql` stays a dialect-neutral text/parameter pair. `obo/lib/sqldialect.obx`
now renders PostgreSQL `$n`, SQLite `?`, SQL Server TDS `@pN`, SQL Server ODBC
`?` and Oracle `:n`; the experimental PostgreSQL and SQLite packages add
`execute_sql` and `prepare_sql`/`bind_sql`, and `db/mssql` adds a first-slice
SQL Server ODBC adapter. Oracle, a SQL Server template bridge, `SqlArg.Float`
binding and identifier/`ORDER BY` allow-lists remain later work.

### Decimal storage 0.16 (released, D1–D3; earlier release)

**Released 0.16; earlier release.** This section records the 0.16 release; the
0.17–0.19 lines carry the storage tier unchanged.

The storage tier makes `decimal` storable without changing the ABI number. It is
one release, `0.16`/format 9/ABI 1: `.sym` gains decimal field types with
24-byte offsets, `priv24`, decimal array signatures, decimal globals and the
`hex` constant payload, so a format-8 reader must reject those files and format 9
was the only accepted input at 0.16. ABI 1 is unchanged because no existing
layout or calling convention moves; the runtime additions are appended (the
`[]decimal` slot 94 and array kind 7).

**Record fields (D1).** A `decimal` field takes 24 bytes inline in its record or
closed-variant payload, in declaration order, 8-byte aligned, with no padding or
reordering; `sizeOf` drives both the source layout and the `.sym` field-list
reader, so the two agree on every offset. An omitted decimal field is zero. Reads,
whole writes, `+=`/`-=`/`*=`/`%=`, record literals and narrowed variant reads
work directly, through `&T`/`mut &T` borrows and through `^T` handles; a
compound assignment computes into a temporary and then copies it into the field.
A field may not be assigned from a call that might free its record (`cannot
assign to a field or element across a call`). A private decimal field is written
as the opaque `priv24` slot, so its type and name do not leak but its width does.

**`[]decimal` arrays (D2).** A `[]decimal` is a pointer-free block of kind 7 with
24-byte inline elements. `new []decimal(n)` zero-fills; `len`, `append`,
`truncate`, indexing, `xs[i] op= v` and `for d in xs` work as for other arrays,
and it can be a local, field, `[][]decimal` element, parameter (`&[]decimal`,
`mut &[]decimal`), return, `Result` payload or optional payload. An index out of
range traps with `index out of range` (exit 3), read or write, including a
negative index. A `[]decimal` cannot be printed or converted with `string`, and a
decimal element cannot be taken, replaced or borrowed. The object-size limit
applies: `new []decimal(n)` above 178,956,969 traps with `obx: allocation size
overflow` (exit 2), and an `append` that must double an array already holding
2^27 decimals traps the same way.

**Globals (D3).** A decimal global takes 24 bytes of the zero-initialised global
data, so `var g: decimal` starts at zero with no initialiser and no runtime
code. `var g = expr` accepts any decimal expression, including calls and
products, and a record global may be built from a record literal with decimal
fields. Initialisers run in the entry code with dedicated hidden global
temporaries, so nested literals and decimal-returning calls do not share one
slot. Module initialisers run in import order (a dependency before its importer)
and declarations run in source order. Every read, write, argument, field,
element and return goes through `copyDecimal`; a decimal global is never a
one-word load, and its value is captured before a later operand runs. A
`[]decimal` global is accepted too, with the ordinary owned lifecycle of a
pointer-valued global.

**Constants (D3).** A decimal constant is a literal or a unary-minus literal
(`const RATE = 0.0825`); arithmetic is not folded, so `const C = 1.0 + 2.0`, a
call, another constant, `-(1.5)` or `--1.5` is refused with `decimal constants
must be a literal; arithmetic is not folded`. A constant takes no storage; each
use materialises its three words into a temporary. `-0.00` is positive zero and
keeps its written display scale. A public constant is written to `.sym` as
`const NAME: decimal = hex "<48 lowercase hex digits>"`, the 24 value bytes in
memory order. The reader rejects uppercase hex, a wrong length, a non-hex
character, reserved bits 40..63 of the third word, a sign other than 0/1, a zero
with sign 1 or a nonzero exponent, and a coefficient above `10^38-1`; a
source-less `--check` builds the value from those bytes alone.

**Status.** D4a, decimal method and interface-member parameters and
returns (including the interface vtable offset and the `pfree` mask fixes), is
released as `0.18`/format 11; D4b, decimal function
values, lambdas and 24-byte capture snapshots, is released as
`0.19`/format 12 (merge `147f7fc`, seed `ba02031`) and described below. D5, the
`lib/decimals.obx` module (`floor`/`ceil`/`trunc`/`round`/`roundAway`, `abs`/
`neg`/`sign`/`isZero`/`cmp`/`min`/`max`, `sum`, largest-remainder `allocate` and
`allocateWeighted`, and `formatGrouped`), is released with the 0.17 line as a
library-only addition; it changes no compiler, runtime, format or seed. D6, the
database read bridge (`DecimalOrNull` and `getDecimal` in each adapter), is
released (merge `4a32742`) and described below; writes already travel losslessly
as `DecimalText`, and there is no `SqlArg.Decimal` or `decimal(p,s)` in this
tier. The remaining decimal work is none; `^I`, named function values and stored
closures remain later. `string(decimal)` stays a compile error; use
`decimal::format`.

### Decimal function values, lambdas and captures 0.19 (released)

D4b makes a decimal a function-type parameter and return, a lambda parameter and
return, and a capturing lambda's value. It is released as `0.19`/format 12/ABI 1
(merge `147f7fc`); `seed/obo` is the same 757,761-byte image (sha256
`8327ca50…`, promoted at `ba02031`) and the image reaches its own fixed point.
A decimal-returning value call pushes the function cell **above** the hidden
destination, so the callee keeps reading the destination at
`16 + visible parameter bytes` (`A`) on every call kind and the cell sits at
`24 + A` (at `16 + A` for a non-decimal or `Result[decimal]` return, which is
one word). The lambda environment pseudo-parameter is gone from
both the local parameter list and `FnRec.params`; the body reads the cell at
`16 + A`, plus 8 for a decimal return. A captured decimal is a 24-byte snapshot
of its three value words in the cell `[code][capture...]`, at byte offset
`8 + the earlier slot sizes`; every other slot is 8 bytes. A capture is taken
when the literal is evaluated, so a later write to the outer local does not
change it. `mut`/`move` captures, `&decimal`, named function values, stored
closures and `string(decimal)` remain out of scope. Cross-module `DecFn` aliases
and `apply` functions round-trip through `.sym`.

**Why format 12.** The function-type text itself did not change, but a format-11
reader accepts that text and pushes the value-call cell in the old order, so it
would miscompile a decimal-returning call. The change is therefore a meaning
change: the format check runs before the body, and a format-11 file is rejected
(`has format 11; this compiler reads 12; rebuild it`). `SYM_ABI` stays 1 because
the descriptors, argument stride and return protocol do not move.

### Decimal database read bridge (released, D6)

D6 is the exact decimal **read** side of the experimental database adapters,
released as merge `4a32742`. It is library-only: it changes no compiler,
runtime, symbol format, ABI or seed. Each package has its own
`DecimalOrNull = closed { Null {}; Value { v: decimal } }` and a `getDecimal`
accessor: `postgres::getDecimal(rows, row, col)`,
`sqlite::getDecimal(stmt, col)` (a SQLite result is a streaming statement) and
`mssql::getDecimal(rows, col)`, the last with `mssql::DecimalType` and
`Rows.decimal_type(col)`.

`getDecimal` returns `Null{}` for SQL NULL and `Value{v: 0}` for a present zero;
NULL is never zero or empty text and wins over the storage checks. PostgreSQL
uses the `numeric(p,s)` typmod scale when `s >= 0` and exactly zero-pads a short
text (never rounds); negative scale is metadata only, and unconstrained numeric
keeps the text's dscale. SQLite treats TEXT as exact and keeps its written
display scale, reads INTEGER as an exact i64 at dscale 0 (the accepted
NUMERIC/INTEGER-affinity loss of scale), and refuses REAL as
`DEC_DB_INEXACT_STORAGE` and BLOB as `DEC_DB_NOT_DECIMAL`. SQL Server reads the
declared precision/scale from `SQLDescribeCol`, accepts only `decimal`/`numeric`,
and applies `decimal::check(v,p,s)`; `float`/`real`, text and binary columns are
refused rather than parsed, and `money`/`smallmoney` arrive as `SQL_DECIMAL`
exact text. The SQL Server live suite is now 24 sections, including
`decimal_or_null`. Writes remain `DecimalText`; there is still no
`SqlArg.Decimal`, a binary numeric path or `?decimal`.

### Impure interface members 0.17 (earlier release)

**Released 0.17; earlier release.** This section records the 0.17 release; the
0.18 and 0.19 lines carry the impure tier unchanged, with later symbol-format
bumps for decimal method/interface signatures and function values.

The third interface-member tier is one release, `0.17`/format 10/ABI 1. A member
is `[pure | impure] [mut] fn name(params)[: T]`; both `impure fn` and
`impure mut fn` are accepted, the marker comes before `mut` and `fn`, and
`pure` and `impure` are mutually exclusive. `impure` is contextual, so a method
may still be named `impure`; using it anywhere but on an interface member is a
declaration error.

**The maximum summary.** An impure member's synthetic summary is
`freesAll = true`, `writesHandles = true`, `pfree = 0`, with no named freed
globals: the maximum a function summary can express. No per-member or
per-implementation summary is serialized; the source parser and the `.sym`
reader rebuild it from the markers through the same `setMemberSummary`, so they
cannot diverge. A call through an impure member therefore invalidates every live
reference and handle, refuses a deep argument, a deep capturing closure, a
reference borrowed through a handle and a handle receiver, and sets the caller's
own `freesAll` and `writesHandles`, so the calling function is itself impure for
every later tier check and caller. Because the summary is the maximum, a forged
`impure` marker can only over-invalidate, which is the safe direction.

**Implementation and dispatch.** An impure member may be implemented by any
method that matches the signature, including a pure or plain one: the
declaration is an upper bound and every caller pays for it. A `pure` member still
requires a pure method, and a plain member keeps its local-tier checks and
messages; the pure and plain tiers are unchanged. There is no vtable or
code-generation change: the member's metadata drives the caller, and the vtable
still has one code address per member. `SYM_ABI` stays 1.

**`.sym` and format.** The interface line carries the canonical marker
(`impure fn ...`, `impure mut fn ...`); duplicate or conflicting markers are
structural errors, and the writer never emits the summary itself. This is a
grammar change, so `SYM_FORMAT` is 10 and `OBO_VERSION` is 0.17; the format check
runs before the body, so a 0.16 reader reports `has format 10; this compiler
reads 9; rebuild it` rather than reading the marker. The 0.17 compiler rejects
formats 6, 7, 8 and 9.

**Still to come: the handler goal (I2).** The language can now express an impure
handler for `http1server`: the application implements `Handler` with
`impure handle(req: &Request): Response`, a data-carrying response record, while
the server owns the listener and connection and the implementation owns its state
and teardown. `http1server` will be able to accept that handler once I2 lands; it
still declares its handler as a function value, and I2 changes it to this
interface. One constraint: an impure member cannot be called through an
interface stored in a record or a global, so the server must keep the handler in
a local or pass it as an `&I` parameter, not in a `Server` field or a global; I2
changes `experimental/http1server` and its design note, not the compiler. Async
libpq and a deferred response follow after I2. `^I` remains later (D4a is
released as 0.18, D4b as 0.19, D5 as the library-only `lib/decimals.obx`, and
D6 is released).

### Decimal methods and interface members 0.18 (released, previous release)

**Released 0.18; previous release.** This section records the 0.18 release; the
0.19 line carries the method and interface tier unchanged and adds D4b.
The 0.18 stage, D4a, makes a decimal a method parameter and return value,
including `Result[decimal]`, and an interface member parameter and return in
all four tiers (`pure`, plain, `mut` and `impure`). It is one release,
`0.18`/format 11/ABI 1 (merge `e47a8c8`, seed `769a359`). A decimal parameter
is a 24-byte by-value slot, and a
decimal return uses the hidden destination pointer already used by a direct
function. A decimal is still never a receiver (`&decimal` is not borrowable),
and function values with decimal parameters or returns stay refused until D4b.

**The interface call.** The push order is now: vtable, the hidden destination
pointer when the member returns a decimal, the object (parameter 0), then the
explicit arguments. That puts the destination pointer directly above the
visible parameters, where a method compiled for a direct call reads it, while
the vtable stays above it. The dispatch load is therefore the explicit argument
byte count plus 8 for the object, plus 8 more when the destination pointer is
present, not the old `8*n` word count. With no decimal return the layout and
code are unchanged.

**`recordParamFree`.** The old `len(params) - 1 - (g.offset - 16) / 8` mapping
assumed every parameter is one word. A 24-byte decimal after a `mut` reference
made the derived index too small, so the `pfree` bit and its `pf` row could be
dropped: a free-summary soundness bug, because a caller could keep a reference
into the `mut` argument across the call. The mapping now walks the signature
from the last parameter and converts the offset to a parameter index through
each type's byte size (24 for decimal, 8 otherwise). An offset that is not on
the signature is a fail-closed internal error, not a silently dropped summary.

**`.sym` and format 11.** The grammar is unchanged: the same signature text now
means decimal parameters and returns. Without the format bump a 0.17 reader
would parse the signature and miscompile it: its interface call path raises
`internal error: a decimal-returning interface or function value is not supported`
for a decimal return, and a decimal parameter would dispatch with the old
`8*n` vtable offset and corrupt the call. The change is therefore a meaning
change, and the format check runs before the body: the 0.18 reader accepts
format 11 only and rejects 6–10 before reading the body
(`has format 10; this compiler reads 11; rebuild it`). `SYM_ABI` stays 1
because the vtable shape, descriptors, argument stride and return protocol do
not move.

**Status.** D4b adds decimal function values, non-capturing lambdas with
decimal signatures and 24-byte Stage B capture snapshots; it defines the
destination-pointer and cell layout for a call through a function value. D4b is
released as `0.19`/format 12/ABI 1 (merge `147f7fc`, seed `ba02031`) and is
described above. D6,
the database read bridge (`DecimalOrNull` and `getDecimal` in each adapter), is
released (merge `4a32742`); writes already travel losslessly as `DecimalText`.
D5 is the released library-only `lib/decimals.obx`. No decimal stage remains.

### Field visibility 0.13

Record fields are private to the defining module unless declared `pub`. The
`0.13`/format-7 change adds mixed records, module-level access, constructor-only
external construction for records with any private field, private field types
with the closure check relaxed for them, and opaque one-word `priv`/`priviface`
slots in `.sym` that preserve layout without writing private names or types. The
exported `pf`/`pfree` summary paths may still name a private field; that leak is
accepted in v1 and means renaming such a field changes the interface hash while a
field-list-only change may not. Closed-variant fields stay public. `fs::Dir` is a
deliberate public-field v1 exception; `io::Reader`/`Writer` are private (with
`bufferedBytes`), `html::Html` is private (with `raw`/`toText`), and the
experimental DB/C wrappers hide their registry tokens while keeping explicit
raw-token escape hatches such as `buffer::wrap`/`rawToken`.

### Decimal 0.12 (historical scalar tier)

**Historical (0.12 line).** This section records the 0.12 release; it is not the
current limit. Fields, arrays, globals and constants arrived in 0.16 (above).

Decimal is the released 24-byte exact scalar tier on `obo 0.12`, symbol format
6, ABI 1 (append-only: runtime slots 60-72; slot 59 stays reserved/baseline and
is the hosted-exit slot in the cinterop build). Source is 14,608 lines /
550,897 bytes; the compiler is 643,073 bytes, sha256 `d98d6b5f…`, and stage 2 ==
stage 3. It self-compiles at 2,194,160 bytes live (port comparison 2,194,560)
and allocates 49,033,600 bytes, under the 2,500,000 budget. `run_fork` is 1,705
tests, `module_test` 113, `port_compare` 1,701/1,701 default, and the
seed comparison was 1,535/1,535 against the then-promoted 0.12 seed (it was
1,477/1,477 against the 0.11 seed, whose decimal cases were `(0, 12)` skips). `sym_test` 44/44, `port_test` 598/598, `float_f1` 19 groups in both
register modes, `doc_check` 69 examples, `mutate_decimal` 22/22, and the
reviewed D1/D2 runner remains 32,719 C checks.

The tier supports literals, locals/parameters/returns, exact `+ - * %`, unary
minus, comparisons, display printing, explicit int conversions and
`decimal::div`/`quantize`/`toInt`, including `Result[decimal]`. At 0.12 it was a
scalar minimum: there were no decimal globals, constants, record fields, arrays,
captures, methods, interfaces or function values; no public `format`/`parse`
stdlib; no database wire adapter, `decimal(p,s)`, float interop or owned/mut
decimal; and the reviewed core keeps its strict quantize target limit of 32768.
The 40-49 ids are trap diagnostic labels only; `Result` APIs carry core
statuses (1, 3, 4, 5, 6), as documented in `obo/LANGUAGE.md` section 9.

## Performance

These are one host's measurements, not portable constants: an AMD Ryzen 7
8745HS under WSL2, CPython 3.12.3, GNU coreutils 9.4 and **obo 0.16 sym-format
9 abi 1**, `50cca0a`, measured on 1 November 2026. The 0.17, 0.18 and 0.19
lines changed compiler checks, frames and decimal dispatch (impure-member
checking, decimal method/interface codegen, then function-value and capture
frames), not non-decimal code generation or the runtime, so this table is
carried unchanged from its 0.16 measurement; it was not re-run afterwards.
The table is the last of three default runs; the runs differed most on `sort`
(168.6–237.5 ms), `sort -n` (216.4–237.3 ms), `sieve` and `wc -l`, and
Python/GNU reference rows moved by up to about 20%. The benchmark uses warm page
cache, one pinned core,
medians of repeated runs, timed output to `/dev/null`, `LC_ALL=C` and `TZ=UTC`;
the command is `python3 obo/bench/cli/run.py` (add `--eza PATH`, or have a
matching eza on `PATH`, for the optional pinned-eza row). The pinned eza 0.23.3
reference was not on this host's `PATH`, so that row was skipped. The 0.15 full
tables, binary sizes, memory figures, methodology and caveats are in
[PERFORMANCE.md](PERFORMANCE.md); how to run and read the suite is in
[obo/bench/cli/README.md](obo/bench/cli/README.md). The earlier 0.5 / 0.6
sample is historical and lives in [HISTORY.md](HISTORY.md).

| Workload | obo | Comparison |
|---|---:|---:|
| Process startup | 0.21 ms | Python 12.5 ms (58.7x slower) |
| `fib(32)` recursive | 11.2 ms | Python 218.9 ms (19.5x slower) |
| Sum below 100,000,000, explicit loop | 23.7 ms | Python 8,139 ms (343.6x slower) |
| Sieve of Eratosthenes to 10,000,000 | 128.7 ms | Python 2,348 ms (18.3x slower) |
| `wc -l`, 110 MB | 79.8 ms | Python 74.3 ms (0.93x); GNU 12.2 ms (0.15x) |
| `wc -lwc`, 110 MB | 134.0 ms | Python 485.0 ms (3.62x slower); GNU 1,057 ms (7.89x slower) |
| `sort`, 1,000,000 lines | 168.6 ms | Python 583.3 ms (3.46x slower); GNU 258.2 ms (1.53x slower) |
| `sort -n`, 1,000,000 lines | 216.4 ms | GNU 469.4 ms (2.17x slower); Python 550.2 ms (2.54x slower) |
| `eza`, 10,000 names | 32.0 ms | Python 17.7 ms and GNU `ls` 6.93 ms are faster; pinned eza row skipped |

The compiler is the 757,761-byte 0.19 fixed point (sha256 `8327ca50…`), and
`seed/obo` is the same promoted 0.19 image (same size and hash) at `ba02031`;
its immediate predecessor, the 757,761-byte 0.18 image (`ca513536…`,
`769a359`), built this image once (historical). The tools are cat 94,209,
wc 98,305, sort 122,881, head 94,209 and
tail 106,497 bytes; eza is 270,337 bytes (sha256 `84211d5d…`); the minimal
static `fn main() {}` binary is 81,921 bytes. Every obo binary is freestanding
and static, with the runtime included; the 0.16 runtime text added one
4,096-byte page to each produced tool, eza and minimal binary compared with 0.15
(whose runtime cleanup had removed 4,096 bytes from each), while the compiler
itself grew by 61,440 bytes with the new storage code (historical). Those tool,
eza and minimal sizes are unchanged in 0.18 and 0.19 because code generation
outside decimal method, interface and value calls did not change. The benchmark
rows above were measured on the 0.16 line; 0.17–0.19 change compiler checks,
frames and decimal dispatch, not non-decimal codegen or the runtime, so they do
not move them. The
honest shape of these numbers is unchanged: pure
compute is far ahead of CPython, while `wc -l` is close to Python and behind
GNU, eza is slower than Python and `ls` on names (its case is size and memory),
and the sort rows are fixture-specific and host-load sensitive.

### Heap limit and address space

The freestanding runtime reserves the heap with `mmap(MAP_PRIVATE |
MAP_ANONYMOUS | MAP_NORESERVE)`. The compiled default is **4 GiB**
(4,294,967,296 bytes) and the compiled ceiling is **64 GiB**
(68,719,476,736 bytes). `OBO_HEAP_LIMIT` may lower or raise the arena inside that
range:

- the value is a canonical unsigned whole number of bytes (`4294967296`) or the
  same with an exact binary suffix `KiB`, `MiB` or `GiB` (`4GiB`);
- it must be at least 64 KiB, a multiple of 16 bytes, and at most 64 GiB;
- signs, whitespace, leading zeros, fractions, exponents, unknown suffixes,
  empty values and overflow are rejected. There is **no silent clamp**: an
  invalid or above-ceiling value prints `obo: invalid OBO_HEAP_LIMIT: <value>`
  to stderr and exits 2 before any mapping is made.

For example, `OBO_HEAP_LIMIT=64KiB` gives a small bounded arena and
`OBO_HEAP_LIMIT=64GiB` raises it to the compiled ceiling. The 64 GiB figure is
the handle limit, not an arbitrary policy: a handle's low 32 bits are a
heap-relative granule offset, so 2^32 granules of 16 bytes address exactly
64 GiB. At that ceiling the final granule offset is 0xffffffff and the high
32-bit generation field is untouched; a future compiled ceiling that needs a
granule at offset 2^32 fails a build-time assertion and the runtime guard,
instead of silently truncating handles. The
one-object cap is independent of the arena: an object's rounded size may not
exceed 0xFFFFFFF0 bytes, so its payload is at most 4 GiB - 32 bytes even in a
64 GiB heap. This is what keeps the size word's low 32 bits unambiguous while a
string's reference count uses the high half.

`MAP_NORESERVE` reserves address space, not RAM: only touched pages become
resident, so a sparse 64 GiB heap with a few small objects has a small RSS. The
heap cap excludes runtime metadata and native libraries. The starts bitmap is
one bit per 16-byte granule (32 MiB at 4 GiB, 512 MiB at 64 GiB), and the
free/mark worklist scales with the arena from 128 KiB to 512 MiB of virtual
address space. Linux overcommit policy, `RLIMIT_AS`, cgroups and any host memory
limit still apply; a failed mapping exits cleanly with
`obx: cannot reserve heap` or `obx: cannot reserve heap metadata`, never a
segfault. Freed blocks return to the runtime's free lists, not to the OS, so
fragmentation and the high-water mark remain part of a process's footprint;
`OBO_STATS=1` reports live peak and total allocated bytes, not RSS. The 5 GiB
address-space cap in `port_compare.py` is a harness safety limit, not a runtime
default.

Regression note from the 64 GiB ceiling update: the 4 GiB default maps a 32 MiB
starts bitmap and a 512 MiB worklist; the 64 GiB ceiling maps a 512 MiB
starts bitmap and the same capped 512 MiB worklist, about 65 GiB of virtual
address space in all. `heap_test` needs about 49 GiB of virtual address space
for its mandatory sparse 48 GiB case; the 64 GiB cases need about 66 GiB and
skip cleanly below that. The recorded sparse 64 GiB case runs in 0.00 s with
264 KiB maximum RSS while touching only a few pages. That update added no runtime
jump-table slot, so every existing index, including the hosted exit slot 59, was
unchanged (historical: the then-current 0.11 image `198f3268…` differed from its
predecessor only because the runtime text is embedded in every executable, and
the 0.12 release `d98d6b5f…` also added only appended slots). The 0.16 storage
tier appends `rt_append_dec` at slot 94 and array kind 7; the hosted build's
census slot moves to 95 because slot 94 now exists in both builds. No existing
index, including hosted exit slot 59, changes.

## Roadmap

**Lambdas Stage A** (non-capturing literals, structural function types and
indirect calls) was `0.9`/format 5. Numeric floats are released as
`0.10`/format 6 (floats first, before Stage B). Stage B capture lists are
released as `0.11`/format 6, including one-word float snapshots. The decimal
scalar tier is `0.12`/format 6, the decimal storage tier (fields, arrays,
globals and constants) is `0.16`/format 9/ABI 1, impure interface members are
`0.17`/format 10/ABI 1, and decimal methods and interface members are
`0.18`/format 11/ABI 1. D4b (decimal function values, lambdas and captures) is
released at `0.19`/format 12/ABI 1, and D6 (the database read bridge) is
released, so the remaining decimal work is none. The next options are the I2
HTTP handler, `^I`, named function values and stored closures, P2C compression,
and threads. I2 follows D4b: the `http1server` handler becomes an interface
with a data-carrying response, kept in a local or `&I` parameter, not a `Server`
field or global; async libpq and a deferred response follow it. The
experimental packages remain probes, not a stable API. `^I`'s phase 2a design is
in [obo/design/interface-handles-design.md](obo/design/interface-handles-design.md). The
lambda design is in [obo/design/lambdas-design.md](obo/design/lambdas-design.md).

| Work | State |
|---|---|
| Lambdas Stage A | merged and promoted at 0.9/format 5 |
| Numeric floats | released at 0.10/format 6/ABI 1 |
| Lambdas Stage B | merged, promoted and pushed at 0.11/format 6/ABI 1; `^I` follows Stage B |
| Decimal scalar tier | released at 0.12/format 6/ABI 1 |
| Decimal storage: fields, arrays, globals, constants | released at 0.16/format 9/ABI 1 |
| Decimal methods, interface members, function values and captures | D4a released at 0.18/format 11/ABI 1; D4b released at 0.19/format 12/ABI 1; D6 (the database read bridge, `DecimalOrNull`/`getDecimal`) released; `lib/decimals.obx` (D5) released on the 0.17 line (library only); no decimal stage remains |
| Impure interface members | released at 0.17/format 10/ABI 1; I2 is the next language step: an `http1server` interface handler and data-carrying response, with the handler in a local or `&I` parameter, not a `Server` field or global; async libpq and a deferred response after that |
| cinterop v2 | experimental stages (a)–(h) merged: `foreign` handles, out records, leak census and the libpq/SQLite/PostgreSQL packages, plus a SQL Server ODBC adapter in `db/mssql`; not a stable API, no threads |
| Systems library | `obo/experimental/sys`: P0/P1a/P1b/P2/P2q and P3 merged (P1b the practical-filesystem slice; P3 spawn/exec, pipes, reaping, signals, cwd and terminal); threads design-only, P2C compression a separate optional adapter |
| Peephole/optimizer audit | evidence-gated |
| More tools: `fd`, `hexyl`, `tokei`, `jq` | later |
| Concurrency and TUI | threads are design-only and unapproved ([obo/design/systems-threads-design.md](obo/design/systems-threads-design.md)); TUI later |
| Networking, TLS, HTTP | implemented in experimental packages only (HTTP/1.1 and HTTP/2 server cores, TLS 1.2/1.3 adapter, sockets/reactor), unstable and not a framework or stable API; no web framework, async runtime or process supervision |

Also not yet done: generic modules (not planned), value records, `inout`
parameters, a `>>>` logical shift, and debug info. Effect summaries for functions
that call each other through forward headings remain a checker limit. Current
design notes are indexed in [obo/design/README.md](obo/design/README.md); they are
authoritative documents, not history.

## The model, in one page

```
type Node = ref record { value: int; next: ?Node }
type Box = ref record { items: []Node }

fn step(b: &Box, mut n: &Node) {         // read the whole, change one part
    n.value += len(b.items)
}

fn main() {
    let b = new Box{items: new []Node(0)}   // b owns the box; the box owns its nodes
    for i in 0..3 { append(b.items, new Node{value: i}) }
    step(b, b.items[1])                     // a read-only whole and a mutable part
    println(b.items[1].value)               // 4
    let first = &b.items[0]                 // a reference remembers what it points into
    b.items = new []Node(0)                 // this frees the old nodes...
    println(first.value)                    // ...so using first is a compile error
}                                           // b, and everything it owns, is freed here
```

```
'first' may point to freed memory: something under what it points into was changed; point it again first
```

- **Ownership.** Every record and array has one owner. Assignment and passing by
  value move; owners free what they own at scope exit; `take(x)` moves out of an
  optional and leaves `nil`. Strings are immutable and reference counted.
- **References** (`&T`) borrow. They are second class: parameters and
  locals only, never stored in fields or returned. That one restriction removes the
  need for lifetimes.
- **One rule: no use after free.** Several references, mutable or not, may name
  the same object. A change invalidates only the references that may point *inside*
  what changed, and using an invalidated reference is a compile error. Rust's
  further rule, that nothing may change an object while a reference to it exists,
  is deliberately absent; it is where most of Rust's difficulty comes from.
- **Handles** (`^T`) are for back-references, graphs and shared arenas: generational
  references checked on every use, so a dangling one stops the program (exit 7)
  rather than reading freed memory.
- **Effect summaries.** Each function records which globals it might free. Because
  everything is declared before use, a callee's summary is complete at every call
  site, and only calls that might free count against the rules.
- **Single pass, kept.** Moves, narrowing and invalidation are all forward facts
  within a function. There are no annotations to write beyond `&`, `mut` and `^`.

What we deliberately leave out: lifetime annotations, exclusive borrows, `unsafe`,
and path syntax in user code. Paths are the compiler's business, not the
programmer's.

## How we work

The aim is a compiler you can trust, so the process matters as much as the code.

- **Small, verified steps.** Each change starts with tests that fail, often an
  error case that pins down a hole, then the change, then mutants: deliberate bugs
  planted in a copy of the compiler, each of which the suite must catch. A mutant
  that survives marks a gap in the tests. The mutation suites run fail-fast, with a
  smoke subset first and parallel cases; a verdict cache was designed, found unsound,
  and is not shipped. The port mutants try the check that caught them in the last
  authoritative run first (`MUTATE_CATCHERS=0` turns this off), then run the full
  comparison on a pass or an unknown check, so this changes the order and not the
  verdict. `MUTATE_TIMINGS=path` records per-stage/per-mutant timings outside the tree.
- **Oracles that don't depend on a compiler**: expected output written beside the
  code, objdump for the encoder, a Python model for the fuzzer, a counting runtime
  that fails any program ending with live objects (exit 5) or freeing twice (exit 6).
- **Stages and fixed points.** Each compiler is checked against the trusted
  previous one, and must reproduce itself exactly.
- **Absence made visible.** A property that matters gets an artefact that fails
  when it is lost. Memory is an example: the port compiling itself must stay within
  a recorded live-memory budget, measured exactly by the runtime (`OBO_STATS=1`).
- **Edits by script.** Large changes are made by patch scripts that assert every
  replacement matches exactly once (`obo/tools/`), so they are repeatable and
  reviewable.
- **Problems reported plainly**, including holes in work already done; a known
  hole stays in the bug list until it is closed, not quietly dropped.

## Finding your way

| | |
|---|---|
| `obo/src/obo.obo` | obo's compiler, in obo, built by `obo/seed/obo` (promote with `make promote`) |
| `obo/seed/obo` | the trusted previous stage |
| `obo/LANGUAGE.md` | the current language reference, including known holes |
| `obo/lib/` | the thirteen source modules: `strings`, `paths`, `sort`, `time`, `cli`, `io` (buffered `Reader`/`Writer`, `writeByte`), `fmt`, `errors`, `fs`, `env`, `html`, `sqldialect` and `decimals` |
| `obo/cmd/` | the tools: `cat`, `wc`, `sort`, `head`, `tail` and `eza`, with `README.md` and their subsets |
| `obo/tests/` | obo's suite, mutants, the I/O, tools and eza gates |
| `obo/tests/io_test.py` | the runtime and streaming-I/O gates |
| `obo/tests/meta_test.py` | the metadata runtime and the `fs` module |
| `obo/tests/elf_test.py` | the emitted image's runtime-data segment |
| `obo/tests/tools.py` | the tools against GNU coreutils, with models and goldens |
| `obo/tests/tools/` | recorded GNU/own goldens for the tools gate |
| `obo/runtime/` | obo's runtime: no collector, leak and double-free counting |
| `obo/FORK.md` | design decisions, milestones, measurements and findings |
| `obo/design/` | authoritative design notes (interfaces, lambdas, tools, numerics) |
| `obo/tools/build_tools.py` | builds the tool module roots directly (no concatenation) |
| `obo/tools/` | the patch scripts behind each change |
| `HISTORY.md` | the obx predecessor, the ownership fork, milestones and earlier measurements |
| `obx/` | the historical GC compiler, suite and stage tests (see `HISTORY.md`) |
| `PERFORMANCE.md` | the 0.15 benchmark record: full tables, methodology and caveats (the README holds the 0.16-measured table carried into 0.19) |

```
cd obo && make                           # obo: build from the committed seed
cd obo && make tools                     # build/tools/{cat,wc,sort,head,tail}
cd obo && make eza                       # build/eza/eza
cd obo && python3 tests/doc_check.py     # LANGUAGE.md examples
cd obo && python3 tests/run_fork.py      # obo's tests, leak check always on
cd obo && python3 tests/elf_test.py      # the image: trimmed runtime-data segment
cd obo && python3 tests/io_test.py --rt --programs --tools  # I/O runtime and programs
cd obo && python3 tests/heap_test.py     # heap limits, allocation bounds, sparse 64 GiB
cd obo && python3 tests/mutate_fork.py   # obo's mutants
cd obo && python3 tests/mutate_io.py     # runtime/library mutants
cd obo && python3 tests/mutate_harness_test.py  # the mutation harness's own tests (fake builders)
cd obo && python3 tests/meta_test.py     # the metadata runtime and fs module
cd obo && python3 tests/mutate_meta.py   # metadata runtime/entry.S and compiler mutants
cd obo && python3 tests/port_test.py     # the port: tokens and encoder, against obo
cd obo && python3 tests/port_compare.py  # the port, stage B (everything), with the memory budget
cd obo && python3 tests/mutate_port.py   # the port's mutants
cd obo && python3 tests/sym_test.py      # symbol files: writer, reader, --check, staleness
cd obo && python3 tests/sym_fuzz.py      # deterministic .sym corruption fuzzing
cd obo && python3 tests/tools.py         # the tools vs GNU; models/goldens without it
cd obo && python3 tests/tools.py --require-reference   # tools vs GNU coreutils (release checklist)
cd obo && python3 tests/tools.py --memory              # the four pinned tool memory cases
cd obo && python3 bench/bench.py         # memory and time against the collector
cd obo && ./obo --deps tests/modules/run/basic/main.obx -I tests/modules/run/basic  # dependencies first
cd obo && mkdir -p /tmp/syms && ./obo --check src/obo.obo --sym-dir /tmp/syms        # check without an executable
cd obo && ./obo --version                # obo 0.19 sym-format 12 abi 1 (seed and compiler are the same promoted image)
cd obo && ./obo --reemit-sym /tmp/syms/obo.sym   # canonical re-emission (test hook)
```

The historical obx build and test commands are in
[HISTORY.md](HISTORY.md#building-obx).

## License

obo is licensed under the Apache License, Version 2.0. The full text is in
[LICENSE](LICENSE); [NOTICE](NOTICE) records the copyright. Unless a file says
otherwise, the compiler, runtime, tools, tests and documentation are covered.

Contributions are accepted under the same license; by contributing you agree that
your contribution is licensed under the Apache License, Version 2.0.
