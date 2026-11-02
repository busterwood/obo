# obo language review and improvement plan (2026-11)

**Status:** assessment and proposed plan, for the user's review. Docs only; no
version is reserved. Each stage below takes the next free version at its merge,
as the other design notes do.
**Baseline:** `main` at `a1175e4` (obo 0.19, symbol format 12, ABI 1; seed
`ba02031`).
**Scope:** the language as a programmer meets it, not the compiler's code
quality. The compiler is cited only where a language rule comes from it, or
where a stage below needs a starting point.

## 1. Method

Read in full: `obo/LANGUAGE.md` (sections 1 to 12), the obx language section of
`HISTORY.md` (the base syntax that `LANGUAGE.md` defers to), and the "not yet
done" list in `README.md`. Read as real programs: `cmd/head.obx`,
`lib/sort.obx` and the declarations of `lib/strings.obx`.

Probed: thirteen small programs were compiled with `obo 0.19` and, where they
compiled, run with `OBX_GC=poison`. They target the places an ownership model
usually leaks (narrowing, aliasing, closures, arm-scoped variants) and the
integer edge cases. The programs are reproduced in Appendix A.

| # | Probe | Result |
|---|---|---|
| p1 | reassign a `Result` local inside its `ok` arm, then read `.value` | rejected (`Result[int] has no fields`) |
| p2 | reassign a closed-type local inside a variant arm, then read the old variant's field | rejected |
| p3 | `f(h, h)` where the `mut` argument clears an optional the other argument narrowed | rejected (`may be nil`) |
| p4 | narrow `h.o`, call a `mut` function that clears it, read it | rejected |
| p5 | borrowed variant changed by a `mut` call inside its `match` arm | rejected (`may point to freed memory`) |
| p6, p7 | `int` overflow: `9223372036854775807 + 1` | wraps silently to `-9223372036854775808` |
| p7 | `byte(300)` | silently `44` |
| p8 | build and drop a 1,000,000-node list | fine |
| p9 | lambda captures a whole array; callee truncates it through a `mut` argument, then calls the lambda | memory-safe: index trap, exit 3 |
| p10 | lambda captures `&xs[0]`; callee truncates `xs` | rejected at the call |
| q1 | `println` then index out of range | output flushed, `obx: index out of range at line 5`, exit 3 |
| q2 | `println` then `7 % 0` | SIGFPE, exit 136, **the earlier `println` output is lost** |
| q3 | `INT_MIN / -1` | SIGFPE, exit 136 |

No probe found a use-after-free. Every aliasing and narrowing attack was
rejected or ended in a defined trap. The weak spots are integer arithmetic and
how much the programmer has to predict.

## 2. Verdict

The memory model works and is worth keeping as it is. Single ownership, `&T`
borrows that cannot be stored or returned, and generation-checked `^T` handles
give memory safety with no collector, no lifetime annotations and no `unsafe`,
which was the project's question.

The cost has moved rather than disappeared. Programs stay short, but the rules
that decide whether a program compiles are long (section 4 of `LANGUAGE.md` is
the longest section), and several depend on facts that are not written in the
source: what a callee's body happens to do, and the order in which functions are
declared. The README's stated aim is "Oberon's simplicity without Rust's
complications". The model meets the "without Rust's complications" part: there
are no lifetimes to write. It is drifting from the "Oberon's simplicity" part:
a programmer cannot always predict what will be accepted without running the
compiler.

The plan in section 6 keeps the model and works on three things: making the
hidden facts explicit, removing restrictions that come from missing compiler
machinery rather than from the model, and giving integers the same failure
discipline that `decimal` already has.

## 3. Strengths to preserve

These are constraints on the plan: no stage may weaken them.

- **Ownership model.** Owners free, borrows are second-class, handles cover
  back-links and graphs. One page explains it (`README.md`, "The model, in one
  page").
- **No annotations in the common case.** A function with only owned and `&`
  parameters needs nothing written beyond its types. Section 6's effect
  declarations must stay optional for private functions.
- **Closed types, exhaustive `match`, `Result`/`try`.** The shared err cell
  makes propagation cheap and keeps the original message and code across
  success types.
- **Explicit capture lists.** Scalars and decimals are snapshots, owned values
  are read-only borrows of the whole local, and the cell lives in the creator's
  frame. The p9/p10 probes show the per-kind call-site firewall doing its job.
- **`decimal`.** Bare fractional literals are exact; `/` is a compile error;
  `div`, `quantize` and `toInt` take an explicit scale and mode and return
  `Result`; overflow traps with exit 10. This is the right default for the
  finance domain, and is the model section 6 proposes for `int`.
- **Templates.** A `sql` hole is always a bind parameter and never text, so
  injection is impossible by construction. HTML holes are escaped by context and
  refused where no safe encoder exists.
- **Field privacy, `.sym` interfaces, `--check`.** Private by default, a
  readable text interface, and checking against symbol files alone.
- **Diagnostics and tested documentation.** Messages name the fix, and every
  `// example:` block in `LANGUAGE.md` is checked by `tests/doc_check.py`.

## 4. Findings

Ordered by how much they affect someone writing obo. Each finding has
evidence, why it matters, and the recommendation that section 6 turns into a
stage.

### F1. Whether a call is accepted depends on the callee's body (high)

**Evidence.** `LANGUAGE.md` §4: a reference borrowed through a handle "cannot be
passed to a call that might free", and a call might free unless it "frees no
global, frees nothing below any of its `mut` arguments (writing a scalar or
appending is fine), writes no owned value through a handle, is compiled before
the call, and is not recursive". The same summary decides reference
invalidation after `mut` calls, handle place tracking ("a callee that sets
`p.other` leaves a handle to `p.child` alone"), the assignment-target rule
(§12), interface implementation tiers (§5b) and Stage B capture checks (§5d).
The summary is inferred (`FnRec.pfree`, `FnRec.freed`, `Sym.freesAll`,
`Sym.writesHandles`, the `pf` rows) and never written in the source.

**Why it matters.** The §4 example `shrink` shows the effect: adding
`spare = new []Node(0)` to a callee's body makes a caller elsewhere stop
compiling, with an error at the caller. Across modules the `.sym` hash records
the change (`pfree`/`frees`/`pf` are in the interface body), so it is caught,
but the API contract is still implicit. The reader of `fn shrink(xs: &[]Node):
int` cannot tell which callers it allows.

**Recommendation.** Effect declarations on function headings, using the tier
vocabulary interfaces already have, optional for private functions and required
for `pub` ones. The inferred summary is checked against the declaration, so an
over-reach is reported in the callee, where the change was made. Stage L4.

### F2. Declaration order changes what is legal (high)

**Evidence.** §4: "a heading not yet defined ... count[s] as freeing anything";
§5c: a lambda may not "call a forward-headed function whose summary is not
final"; §12: recursive calls are judged when the body is complete and are
pessimistic about handle writes; §5: "The compiler does not know that two
conditions agree ... (known false positive)". `README.md`: "Effect summaries for
functions that call each other through forward headings remain a checker limit."

**Why it matters.** Moving a function below its caller, or splitting it into
two mutually recursive functions, can turn an accepted program into a rejected
one without changing what it does. This is the single-pass compiler's structure
showing through as a language rule.

**Recommendation.** The same declarations as F1: a forward heading that declares
its tier gives callers before the body that tier instead of "frees anything",
and a recursive call uses the declared tier, checked when the body completes.
Stage L4.

### F3. "Store it in a local first" restrictions pile up (high)

**Evidence.**

- §3: "A temporary value must be stored in a variable before it is used ...
  `println(make().v)` is an error."
- §5a: "A method call ends the designator: `b.m().n` and `b.m()[0]` are syntax
  errors", and `make().m()` with an owned result is the store-first error.
- §7: `try` only as the whole value of a `let`/`var`/assignment or as a
  statement; not as an argument, field, index, condition, return value, match
  scrutinee or right operand. "The rule is a designed restriction, not a hole:
  lifting it needs cleanup of live temporaries (drop glue)."
- §7: `try` is not allowed in `main` (it returns nothing), so every program root
  matches its results by hand (`cmd/eza.obx` does exactly this).
- §5c/§5d: function values cannot be returned or stored; a capturing lambda may
  only be a direct call argument.

**Why it matters.** Each rule is defensible alone; together they make ordinary
code longer than it needs to be, and the first three have one cause: the
compiler cannot yet free an owned temporary at the end of the statement, or on
a `try` early return. The compiler already has the mechanism in one place: §8
"a template builder is a hidden owned local freed on every exit".

**Recommendation.** Statement-scoped owned temporaries, built on hidden locals,
plus cleanup of already-pushed owned arguments on a `try` early return. That
lifts the first three restrictions together. The lambda restrictions are part of
the model (no escaping closures without heap environments) and stay. Stage L2.

### F4. Integer failures are silent or unrecoverable (high)

**Evidence.** Probes p6, p7, q2, q3. `int` `+ - *` wrap; `byte(x)` keeps the low
8 bits (documented in the obx section of `HISTORY.md`); `x / 0`, `x % 0` and
`INT_MIN / -1` die on SIGFPE (exit 136, a core dump), and buffered `print`
output written before the fault is lost. By contrast an index trap (q1) flushes,
prints a message with the line, and exits 3. §9 only says "Division by zero
stops the program with a hardware trap (SIGFPE)."

**Why it matters.** `decimal` was designed to never round or overflow silently,
for the finance domain. `int` is what counts, indexes, sizes and money-in-cents
code uses, and it has the opposite policy. Losing earlier output on a crash also
makes the failure hard to diagnose.

**Recommendation.** Stage L1, in two parts. First, without debate: division and
remainder by zero and `INT_MIN / -1` trap like an index error (flush, message
with the line, a new exit code). Second, a decision for the user: trap on `int`
overflow with explicit wrapping operators, and make `byte(x)` checked. The
compiler itself needs wrapping (FNV-1a at `obo.obo:2727`, `2735`, `2743`
multiplies by `0x100000001b3`), so the wrapping spelling must exist before the
trap does.

### F5. "Pure" means three different things (medium)

**Evidence.** §5b: a `pure` interface member "may still write scalars, globals
and its receiver, so `pure` does **not** forbid those writes". §5c: "Every
function value is pure in the compiler's existing predicate: its hidden body may
allocate, write scalar globals, append ..., perform I/O builtins"; the error is
`a lambda must be pure`. Internally, "pure" means "frees nothing and writes no
owned value through a handle".

**Why it matters.** Every other language uses "pure" for "no observable side
effects". A reader of `pure fn area(): int` will assume more than obo promises,
and a reader of "a lambda must be pure" will not expect `println` to be allowed
inside it.

**Recommendation.** Name the tiers for what they guarantee, and choose the
vocabulary once, together with F1's declarations. Proposed in Stage L4: `nofree`
(replaces `pure`), unmarked (the local tier) and `impure` (unchanged), with
`pure` accepted as a deprecated spelling for one release.

### F6. `let` freezes the name, not the value (medium)

**Evidence.** `let b = make(); b.add("a")` mutates through a `let` (§5a example),
as does `let bag = ...; append(bag.items, ...)` (§4). §4: "`mut` belongs to the
parameter, not the type: a local reference is changeable exactly when the object
it borrows is." The obx section says only "`let` = immutable binding".

**Why it matters.** Mutability is decided in three places: `var`/`let` (the name
only), `mut` on parameters (deep), and the owner for local references. In an
ownership language the owner holds the whole tree, so a deep `let` would be cheap
to check, and it would make `&` from a `let` read-only, which is what a reader
expects.

**Recommendation.** Measure first, then decide. Stage L5 adds a counting
diagnostic, reports the number of affected sites in the tree, and only then
proposes the change.

### F7. No nullable decimal (medium, domain-specific)

**Evidence.** §1: "`?decimal` is not representable". The database read bridge
D6 works around it with `DecimalOrNull`/`getDecimal`
(`decimal-db-boundary-design.md`).

**Why it matters.** A nullable `NUMERIC` column is ordinary in the finance and
database domain the language is aiming at, and a wrapper type for one column
type is the kind of special case the rest of the language avoids.

**Recommendation.** `?decimal` without an allocation, using a spare bit of the
24-byte value as the nil tag. The `.sym` reader already insists that "bits 40 to
63 of the last word" are zero, so they are free for a tag. Stage L6.

### F8. The specification is hard to read as a specification (medium)

**Evidence.** `LANGUAGE.md` mixes four kinds of material: the language; the ABI
("the cell sits above it at `24 + visible parameter bytes`"); release history
and seed hashes (§5c ends with a paragraph of sha256 prefixes); and compiler
internals (`symCompareFields`, `fieldIndex`, `containsIface` in §6). The base
syntax (`if`, `while`, `for`, `match`, `defer`, literals, operators) is not in
it at all: its introduction refers to "the obx language section of HISTORY.md", which is a
history document. There is no grammar.

**Why it matters.** It makes the language hard to review as a whole (this review
had to reconstruct it from three files), it hides the core model under release
detail, and every feature stage edits a 3,165-line file.

**Recommendation.** Split the document, without changing any rule. Stage L0.

### F9. Smaller points (low)

- **Result codes and exit codes overlap.** §9 already warns that `exit(r.code)`
  makes ENOENT look like out-of-memory (both 2). Recorded; no change proposed
  beyond the new code in L1 avoiding the errno range collision as much as the
  existing scheme allows.
- **Errors are untyped.** An `err` carries a string and an `int`. Without
  generics a typed error payload would need a new built-in form. Recorded, not
  planned.
- **File descriptors are plain `int`s.** §8 admits a stale `int` may close a
  reused descriptor. The ownership model is not used for the one resource that
  most needs it. Explicit close is the decided policy for `foreign` handles
  (`cinterop-boundary-design.md`); an owned descriptor type would follow the same
  policy. Recorded, not planned.
- **Equal decimals can print differently.** `1.0 == 1.00` is `true` and they
  print `1.0` and `1.00`. Defensible (display scale is metadata), but §1 should
  say so where `==` is defined. Fixed in L0.

### F10. No generics (recorded; the decision stands)

`README.md` lists "generic modules (not planned)". The decision is not reopened
here. Its cost is visible and will grow: `lib/sort.obx` sorts by returning
`[]int` permutations for each key type (`orderStrings`, `orderInts`,
`orderNatural`), `Result` is the only parameterized type and is built in, and
there is no map type in `lib/`. The roadmap's next tools, `fd` and especially
`jq`, need maps and a dynamic value type. When those tools are planned, the
design should say explicitly how they get maps (a per-key-type library, a
built-in like `Result`, or revisiting this decision).

## 5. What is not proposed

- No change to the ownership, borrow or handle rules themselves.
- No escaping closures, stored function values or heap environments.
- No implicit conversions.
- No generics (F10).
- No user destructors or automatic resource release (consistent with the
  explicit-close decision for `foreign` handles).

## 6. Plan

Seven stages. L0 and L1 are small and independent and can run in parallel
worktrees. L2 is the largest ergonomics gain. L3 is a decision gate only. L4 is
the largest design change and needs the L3 decisions. L5 and L6 are independent
of each other and can follow in either order.

Every compiler stage keeps the existing gates: the full test suite, the mutant
catchers, `tests/doc_check.py`, `OBO_CHECK=1` leak/double-free checks, the
self-host fixed point, and one seed promotion after merge.

| Stage | Change | Language change | `.sym` format | Size |
|---|---|---|---|---|
| L0 | split the specification | none | no | docs |
| L1a | integer division traps | runtime behaviour only | no | small |
| L1b | checked `int` overflow, wrapping operators, checked `byte()` | yes (decision D1) | no | medium |
| L2 | statement-scoped temporaries; `try` anywhere; `main` may return a `Result` | yes, only relaxations | no | large |
| L3 | decision gate for L4 | — | — | — |
| L4 | effect tier vocabulary and declarations | yes | yes | large |
| L5 | deep `let` (measure, then decide) | yes (decision D4) | maybe | medium |
| L6 | `?decimal` | yes | yes | medium |

### L0. Split the specification (docs only)

**Goal.** A reader can learn the language from one document that contains only
the language.

**Changes.**

1. `obo/LANGUAGE.md` becomes the language reference: types, declarations,
   statements, expressions, ownership, references, handles, methods,
   interfaces, lambdas, modules (source-level rules only), results, built-ins,
   runtime traps and exit codes, compile errors, known holes.
2. Move the base syntax from the obx section of `HISTORY.md` into it, as its
   new first sections, so `LANGUAGE.md` no longer defers to history. Add a
   grammar in EBNF, derived from the parser, as an appendix.
3. Move ABI and layout material (frame offsets, cell layouts, 24-byte slots,
   hidden destinations, vtable byte counts) to a new `obo/ABI.md`.
4. Move the `.sym` format, hashes, versions and trust model (§6 "The `.sym`
   format and hashes" to "Versions and the trust model", and the `.sym` rows of
   the error tables) to a new `obo/SYMFILE.md`.
5. Move release history, seed sizes and hashes out of the reference into
   `FORK.md`, leaving one "current version" line in `LANGUAGE.md`.
6. Remove compiler identifiers (`symCompareFields`, `fieldIndex`,
   `containsIface`, `recordParamFree`) from the reference; they belong in the
   ABI or symbol-file document if anywhere.
7. Add the F9 note on decimal `==` and display scale.

**Gates.** `tests/doc_check.py` reads `LANGUAGE.md` by fixed path; it must run
the same number of examples before and after (examples that move to `ABI.md` or
`SYMFILE.md` are either registered with the checker or kept in `LANGUAGE.md`).
No example changes its text or expected output.

**Exit.** `LANGUAGE.md` contains no sha256 prefix, no frame offset and no
compiler function name, and does not link to `HISTORY.md` for syntax.

### L1a. Integer division traps (no decision needed)

**Goal.** Integer division and remainder fail the way an index error does.

**Changes.**

- Before each `idiv` (the `0xF7 /7` and `/6` emissions near `obo.obo:10640` and
  `10898`), test the divisor for zero, and for `-1` with a dividend of
  `INT_MIN`. On failure call a runtime trap that flushes pending output, prints
  `obo: integer division by zero at line N` or `obo: integer division overflow
  at line N`, and exits with a new code.
- Exit code **11**, the first unused one (§9 uses 2 to 10). Add it to the §9
  table.
- A constant zero divisor is a compile error, matching the constant checks on
  `readFd` counts and decimal scales.
- The divisor test is skipped when the divisor is a non-zero constant other than
  `-1`, which covers most real divisions.

**Tests.** Run tests for `/0`, `%0`, `INT_MIN / -1`, `INT_MIN % -1`, a variable
divisor, a compound `/=`/`%=`; an error test for a constant zero; a check that
output before the trap is flushed. Mutant catchers for each skipped branch.

**Gates.** The benchmarks under `obo/bench` and the self-build time in
`PERFORMANCE.md` move by less than 1 %.

### L1b. Checked integer arithmetic (decision D1)

**Goal.** `int` overflow and narrowing are never silent, as `decimal` overflow
is never silent.

**Proposal.**

- `+`, `-`, `*`, unary `-` and the compound forms on `int` trap on signed
  overflow: a `jo` after `add`/`sub`/`imul`/`neg` to the same trap routine as
  L1a, message `obo: integer overflow at line N`, exit 11.
- Constant folding checks overflow and reports a compile error.
- Wrapping operators for code that wants wrapping (hashes, PRNGs, checksums):
  `+%`, `-%`, `*%` and their compound forms. Shifts and bitwise operators are
  unchanged (they never overflow); `<<` keeps its documented mod-64 count.
- `byte(x)` traps when `x` is outside `0..255`. Code that wants the low 8 bits
  writes `byte(x & 0xFF)`, which the compiler recognises and does not test.
- Hex literals that "wrap" today (`0xFFFF_FFFF_FFFF_FFFF` is `-1`) keep their
  meaning: they are bit patterns, not arithmetic.

**Migration.** The compiler's three FNV-1a loops (`obo.obo:2727`, `2735`, `2743`)
become `*%`; so must any hash in `lib/` and `experimental/`. There are 49
`byte(` sites in the compiler and 138 in `lib/` and `cmd/`; each is either
already in range by construction (a digit, a masked value) or becomes
`byte(x & 0xFF)`. The migration must happen in the stage before the trap is
turned on, with the trap behind a flag until the tree is clean, because the
compiler builds itself.

**Alternatives for D1.**

1. *Trap by default, wrapping operators (proposed).* Matches the `decimal`
   policy; one new operator family.
2. *Wrap by default, checked builtins* (`checked::add(a, b): Result[int]`).
   No migration; overflow stays silent where nobody thought about it.
3. *Trap in a debug build only.* Two semantics for one program; rejected for
   a language that has no build modes today.

**Gates.** As L1a, with a looser bound agreed at the decision (an overflow check
per add is typically a few percent on arithmetic-heavy code; the self-build is
the reference workload).

### L2. Statement-scoped temporaries

**Goal.** Lift the store-first rule, the designator-ending rule for calls, and
the `try` position rule, without changing ownership.

**Rule (proposed text for the reference).** An owned value produced by a call,
`new`, or `ok`/`err` that is not stored is a *temporary*. It lives until the end
of the statement that made it, and is freed there, after the statement's own
effects and before the next statement. On every exit from the statement
(normal end, `try` early return, `break`, `continue`, `return`), all temporaries
made so far are freed in reverse order of creation. A temporary may be read
through (`make().v`), indexed (`make()[0]`), called on (`make().m()`), borrowed
for the statement (`f(&make())`), or moved (passing it by value moves it out,
and it is then not freed at the end).

**Mechanism.**

1. *Hidden locals.* Each temporary is given a hidden frame slot when it is
   created, in the same way the template builder gets "a hidden owned local freed
   on every exit" (§8). Because it is an ordinary owned local to the checker,
   the existing move, borrow, argument-holding and invalidation rules apply to it
   unchanged; a `&` borrow of a temporary is held like a `let` reference.
   Starting points: `noTemp` (`obo.obo:5302`) and the designator loop check at
   `obo.obo:9749`, which become the creation points instead of errors; the
   reference-borrow refusal at `obo.obo:4650` is lifted for statement scope only.
2. *Designators after calls.* The parser continues the designator after a call
   (`b.m().n`, `b.m()[0]`, `f().g()`) instead of ending it.
3. *`try` in nested positions.* On the err path of a `try`, the compiler emits
   cleanup for what the statement has already built: the hidden temporaries made
   so far, and any owned arguments already pushed for an enclosing call (their
   types and stack offsets are known statically at the `try`). This replaces the
   `tryNest`/`trySite` refusals at `obo.obo:10689`–`10690`. `try` stays refused
   inside `defer`.
4. *`main` may return a `Result`.* `fn main(): Result[int]` is allowed. `ok(n)`
   exits with `n`; `err(m, c)` prints `<program>: m` on standard error and exits
   with 1. Defers and owned locals are freed first, so `OBO_CHECK` still runs.
   `fn main()` with no result is unchanged. (Decision D3 covers the exit code on
   `err`.)

**Limits kept.** A temporary cannot outlive its statement: `let r = &make()` is
still refused (the borrow would outlive the value). A capturing lambda is still
only a direct call argument. A statement whose temporaries would be freed in a
`while` condition is evaluated per iteration, so its temporaries are freed per
iteration.

**Tests.** Run tests for each lifted form; `OBO_CHECK=1` leak and double-free
runs for temporaries on every exit kind, including a `try` err inside the third
argument of a call whose first two arguments are owned; error tests for the kept
limits; the doc examples in §3, §5a and §7 that show the old errors change to
show the new rule; fuzzing through `tests/own_fuzz.py` with temporaries enabled.

**Exit.** `println(make().v)`, `b.m().n`, `eat(try get(1))`,
`return ok(try f() + 1)` and `try` in `main` (with a `Result` main) compile and
pass `OBO_CHECK=1`.

### L3. Decision gate

Before L4 starts, the user decides D2 (cross-module precision) and D5 (tier
spelling). L4's design note is written against those answers.

### L4. Effect tiers: one vocabulary, declarable on functions

**Goal.** Fix F1, F2 and F5 together: state effects where they can be read,
keep inference where nothing is declared, and stop calling a non-pure tier
"pure".

**Vocabulary (decision D5; proposed).**

| Tier | Meaning | Today |
|---|---|---|
| `nofree` | frees nothing, writes no owned value through a handle; may write scalars, globals, the receiver, and do I/O | interface `pure`; the lambda predicate |
| *(unmarked)* | local: may free only below its own `mut` parameters; frees no global; writes no owned value through a handle | interface plain/`mut` member |
| `impure` | anything | interface `impure` |

`pure` is accepted as a deprecated spelling of `nofree` for one release, with a
warning, then removed. Diagnostics say "must not free" instead of "must be
pure".

**Declarations on functions.**

```text
fn total(xs: &[]Node): int nofree { ... }
pub fn reset(mut b: &Builder) { ... }            // unmarked = local, declared
pub fn run(h: ^App) impure { ... }
fn later(x: &Node): int nofree                    // forward heading with a tier
```

The tier goes after the result type (or after `)` when there is none), on
headings, bodies and methods alike. A heading and its body must agree.

- *Private functions.* The tier is optional. Without one, the summary is inferred
  exactly as today. With one, the inferred summary must fit within it, or the
  function's own body is the error: `'shrink' frees global 'spare', but it is
  declared nofree`.
- *Public functions.* The tier is required on `pub fn` and `pub` methods. The
  unmarked form counts as a declaration of the local tier, so existing `pub`
  functions that already fit need no edit; a `pub` function that frees a global
  or writes through a handle must say `impure` (the migration counts these).
- *Forward headings.* A heading with a tier gives callers before the body that
  tier instead of "frees anything" (F2). A heading without one keeps today's
  pessimism.
- *Recursion.* A recursive call in a function with a declared tier uses the
  declared tier, checked against the completed body.
- *Lambdas.* The lambda rule becomes "a lambda body must be `nofree`", and the
  message says so.

**Cross-module precision (decision D2).** Today the `.sym` carries the precise
inferred summary (`pfree`, `frees`, `pf` paths) and importers use it.

1. *Declared tier plus precise summary (proposed first).* The `.sym` keeps the
   precise summary and adds the tier; the precise summary must fit the tier.
   Importers keep using the precise summary, so no caller in the tree loses
   precision, and a body change that exceeds the tier is caught in the callee.
   The interface hash still changes when the precise summary changes, as today.
2. *Declared tier only.* Importers see only the tier, so a body change within the
   tier never changes the interface hash and never breaks a caller. Some callers
   that rely on precise `pf` paths (for example `io::Reader`'s private `buf`
   path, §6) may be rejected.

Proposed: implement 1, and add a `--tier-only` measurement flag that applies 2
during a build, to count how many sites in `cmd/`, `lib/` and `experimental/`
would be rejected. Revisit 2 with that number.

**`.sym` and versions.** New format: a `nofree`/`impure` marker before `fn` on
function and method headings (the interface member syntax already has the
shape), and `nofree` replacing `pure` on interface members. The reader rebuilds
member summaries from markers as today; for functions it checks the precise
summary fits the marker. Format bump; `SYM_ABI` unchanged (no layout or calling
convention change).

**Compiler anchors.** `Sym.freesAll`/`writesHandles`, `FnRec.pfree`/`freed`,
`mightFree` (`obo.obo:5068`), `setMemberSummary` (`obo.obo:13481`), the `.sym`
writer `appFnSummary` (`obo.obo:15087`) and reader, and the forward-heading
paths that set `freesAll`.

**Tests.** Accept/refuse pairs for each tier on functions, methods, forward
headings and recursion; the F2 programs (a callee moved below its caller, a
mutually recursive pair) compile when declared; `.sym` golden updates; the
`--tier-only` count recorded in the design note; mutants that weaken the
fit check.

**Exit.** The §4 `shrink` example's error moves from the caller to `shrink`
when `shrink` is declared `nofree`; reordering functions with declared tiers
never changes acceptance.

### L5. Deep `let` (decision D4, after measurement)

**Goal.** `let` makes the owned value read-only, not only the name.

**Step 1: measure.** Add a counting diagnostic (`--count-let-mutation`, no
behaviour change) that reports each place where a `let`-bound owned local is
mutated: a field or element write, `append`/`truncate`, a `mut` method call, a
`mut` argument, `take`/`replace` below it. Record the count for the compiler,
`lib/`, `cmd/` and `experimental/` in this note.

**Step 2: decide.** If the count is manageable, propose: a `let`-bound owned
value is read-only in full, `&` of it is read-only, and the fix is `var`. Moving
it (passing it by value, returning it) is still allowed, since the new owner
decides. The migration is mechanical (`let` to `var` at each counted site) and
can be done by the compiler's diagnostic output.

**Alternative.** Keep shallow `let` and say so plainly in the reference
("`let` fixes the name; use `&` parameters for read-only access").

### L6. `?decimal`

**Goal.** A nullable decimal with no allocation, usable wherever `decimal` is.

**Representation.** The third word of a decimal holds the sign, exponent and
display scale, and bits 40 to 63 must be zero today (the `.sym` reader already
refuses them). One of those bits is the nil tag; nil is all-zero words with the
tag set. A `?decimal` is 24 bytes, like a `decimal`.

**Language.** `?decimal` locals, parameters, returns, fields, `[]?decimal`,
`Result[?decimal]`, globals; `== nil`, `!= nil` and narrowing exactly as for
other optionals; assigning `nil`. No `take` (a decimal is not owned). Arithmetic
needs a narrowed value. Printing follows whatever rule other optionals have.

**Boundaries.** The `sql` template accepts a `?decimal` hole (nil becomes
`sqlNull()`), and the D6 bridge gains `getOptDecimal` returning `?decimal`;
`DecimalOrNull` stays for one release.

**`.sym`.** Format bump for the `?decimal` type spelling and 24-byte optional
slots; `SYM_ABI` bump only if the tag bit counts as a layout change (decision at
the stage's design note).

## 7. Decisions for the user

| # | Decision | Proposed | Needed before |
|---|---|---|---|
| D1 | `int` overflow policy and `byte()` | trap by default, `+% -% *%`, checked `byte()` | L1b |
| D2 | what importers see of a callee's effects | declared tier plus precise summary; measure tier-only | L4 |
| D3 | exit code for `err` returned from `main` | 1, with `<program>: message` on stderr | L2 |
| D4 | deep `let` | decide after the L5 count | L5 step 2 |
| D5 | tier spelling | `nofree` / unmarked / `impure`; `pure` deprecated for one release | L4 |

## 8. Order and rationale

1. **L0 and L1a in parallel.** Both are small and independent. L0 makes every
   later stage's documentation change local; L1a closes a crash that loses
   output.
2. **L2.** The largest ergonomics gain, no design decision beyond D3, and pure
   relaxation: every program accepted today is still accepted.
3. **L1b**, once D1 is decided. It needs a migration of the tree before the trap
   is enabled, so it is best done after L2 lands, when the tree is quiet.
4. **L3 then L4.** The largest design change; it needs D2 and D5.
5. **L5 and L6** in either order, after L4.

This differs from the order given in conversation (temporaries, effects,
integers, renaming, spec split) only in moving the two small, independent items
(L0, L1a) to the front, and in folding the renaming into L4 so the vocabulary
changes once.

## Appendix A. Probe programs

p1, `Result` arm reassignment (rejected):

```obo
fn get(n: int): Result[int] {
    if n < 0 { return err("bad", 7) }
    return ok(n)
}
fn main() {
    var r = get(3)
    match r {
        ok { r = get(-1); println(r.value) }
        err { println(r.message) }
    }
}
```

p2, variant arm reassignment (rejected):

```obo
type Shape = closed { Circle { r: int } Rect { w: int; h: int } }
fn main() {
    var s: Shape = new Circle{r: 1}
    match s {
        Circle { s = new Rect{w: 5, h: 6}; println(s.r) }
        Rect { println(s.w) }
    }
}
```

p3, aliased `mut` and read-only arguments (rejected):

```obo
type Box = ref record { v: int }
type Holder = ref record { o: ?Box }
fn f(mut a: &Holder, b: &Holder) {
    if b.o != nil { a.o = nil; println(b.o.v) }
}
fn main() {
    var h = new Holder{o: new Box{v: 1}}
    f(h, h)
}
```

p4, narrowing across a `mut` call (rejected):

```obo
type Box = ref record { v: int }
type Holder = ref record { o: ?Box }
fn clear(mut a: &Holder) { a.o = nil }
fn main() {
    var h = new Holder{o: new Box{v: 1}}
    if h.o != nil { clear(h); println(h.o.v) }
}
```

p5, borrowed variant changed in its arm (rejected):

```obo
type Shape = closed { Circle { r: int } Rect { w: int; h: int } }
type Holder = ref record { s: Shape }
fn swap(mut a: &Holder) { a.s = new Rect{w: 5, h: 6} }
fn main() {
    var h = new Holder{s: new Circle{r: 1}}
    let p = &h.s
    match p {
        Circle { swap(h); println(p.r) }
        Rect { println(p.w) }
    }
}
```

p7, silent integer wrap and narrowing (prints `-9223372036854775808`, `98000`,
`44`):

```obo
fn main() {
    var x = 9223372036854775807
    x += 1
    println(x)
    let s = "abc"
    let b = s[1]
    println(int(b) * 1000)
    let bb: byte = byte(300)
    println(bb)
}
```

p9, whole-array capture with a truncating callee (index trap, exit 3):

```obo
type Item = ref record { v: int }
fn apply(mut xs: &[]Item, f: fn(): int): int { truncate(xs, 0); return f() }
fn main() {
    var xs = new []Item(0)
    append(xs, new Item{v: 4})
    println(apply(xs, fn [xs](): int { return xs[0].v }))
}
```

p10, deep capture with a truncating callee (rejected: `argument 2 of apply
captures a place that argument 1 changes, which is mut`):

```obo
type Item = ref record { v: int }
fn apply(mut xs: &[]Item, f: fn(): int): int { truncate(xs, 0); return f() }
fn main() {
    var xs = new []Item(0)
    append(xs, new Item{v: 4})
    let r = &xs[0]
    println(apply(xs, fn [r](): int { return r.v }))
}
```

q1 and q2, trap output (q1 prints `before` and the index message, exit 3; q2
prints nothing and dies on SIGFPE, exit 136):

```obo
fn main() {
    println("before")
    var xs = new []int(1)
    let i = 5
    println(xs[i])
}
```

```obo
fn main() {
    println("before")
    let z = 0
    println(7 % z)
}
```

q3, `INT_MIN / -1` (SIGFPE, exit 136):

```obo
fn main() {
    let a = -9223372036854775807 - 1
    let m = -1
    println(a / m)
}
```

p8 (a 1,000,000-node list built with `take` and dropped at scope exit) ran and
exited 0; p6 combined the overflow of p7 with the division of q2 and lost the
overflow line's output to the SIGFPE.
