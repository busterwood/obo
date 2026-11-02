# Generic core rethink (2026-11)

Status: decided 2026-11-02 (G1 to G5 in section 7), not started. Written 2026-11-02 after the language review
(`language-review-2026-11.md`). It answers one question from the user: obo has
added decimals, Result, templates and other features as special cases in the
compiler. Would a language with generics from the start have needed a smaller,
simpler compiler, and should we start again?

Short answer: yes, and yes. Most of the compiler's recent growth comes from
one cause: each new kind of value has to be wired through every place a value
can appear. Generics alone do not fix that. Generics plus **value records**,
**operators as methods** and **associated functions** do. A throwaway
prototype (`obx/ref/`) supports the claim: it builds Decimal,
Money, Option, Result, a generic sort and a generic Map as library code, keeps
single ownership, and its compiler has no knowledge of any of those types.

Since obo has no users, this note recommends a restart: a new core language
and compiler with generics from day one. It keeps obo's ownership model and
drops the compiler's single-pass, no-AST rule.

## 1. What the history shows

Compiler growth per feature (`obo/src/obo.obo`, now 17,233 lines), measured
from the merge commits:

| Feature | Compiler lines added |
|---|---:|
| Decimal (0.12 to 0.18: 10 commits, 6 releases) | about 1,430 |
| Interfaces (0.8) | 1,162 |
| SQL/HTML templates (0.15) | 990 |
| Lambdas (A + B) | 1,262 |
| Modules (source + `.sym`) | 843 + 1,828 |
| Methods | 576 |
| Floats | 493 |
| Result and `try` | 480 |
| Field privacy | 132 |

The decimal work tells the story. After the first 741 lines (0.12), each later
step threaded the same type through one more position: fields (D1), elements
(D2), returns (D3), globals and constants (D4a/b), operand order, a multiply
cap. `kind == "decimal"` now appears 44 times in the compiler, next to tests
for `opt` (27), `string` (25), `fn` (25), `result` (17), `record` (16),
`array` (16), `iface` (15), `handle` (14) and `float` (9).

This is a **type × position matrix**. Each built-in type has to be handled in
each position a value can occupy: local, parameter, return, field, element,
global, constant, capture, Result payload, interface method, function type,
`.sym` encoding. Each new type adds a row. Each new position (lambda captures,
for example) adds a column that every existing type must fill. The compiler
grows as rows × columns.

The library shows the other side of the same gap. `lib/sort.obx` has the same
merge sort written twice (`mergeStrings`, `mergeInts`), and sorts through
permutation arrays (`orderStrings` returns `[]int`) because a sort cannot take
"any array whose elements can be compared". Any new key type means a third copy.

## 2. Is decimal a special case of something general?

The user asked whether decimal is a specialization of a generic case, such as
money. Mostly yes, with one correction to the direction:

- **Decimal** is a *value type with operators*: a fixed-size value, copied
  rather than owned, with `+ - * <` defined on it. Dates, durations, 128-bit
  ids, fixed-point rates and complex numbers have the same shape. In obo each
  of these would be another row in the matrix.
- **Money** is a *generic value type over Decimal*: `Money[C: Currency]`,
  where the currency is a type supplying associated functions (`C.code()`,
  `C.scale()`) and holding no data. Adding dollars to yen becomes a type error
  at no run-time cost.
- **Option, Result, Map, Set, sort** are *generic containers and algorithms*.
  In obo, `Result` is a compiler feature (480 lines) and `Map` does not exist.

So decimal is not a special case of money. Money is built on decimal, and both
are instances of the same general facility. With that facility, neither needs
the compiler. The compiler only needs the primitive decimal is built from: a
128-bit integer, or 64-bit multiply-high and divide intrinsics. That is one
primitive, not a decimal type.

The general facility has four parts. All four are needed.

1. **Generics** (type parameters on types and functions, with interface
   constraints).
2. **Value records**: multi-word records that are copied, not owned, and live
   inline in locals, fields, elements and returns. This is what removes the
   matrix. A value record is handled in each position *once*, for all value
   records.
3. **Operators as methods**: `a + b` calls `a.add(b)`, `a < b` calls `a.cmp(&b)`.
4. **Associated (static) functions**: `C.scale()` on a type parameter, so a
   type can carry behaviour without data.

"Make everything generic" is not the goal. The goal is a small core plus
general mechanisms, so that new kinds of values become library code.

## 3. The prototype

`obx/ref/` is a throwaway Python compiler for a small obo-like
language that emits C. It exists to test the claims above, not as a starting
point for real code.

| Part | Lines |
|---|---:|
| Lexer | 73 |
| Parser (to an AST) | 374 |
| Type representation and errors | 36 |
| Checker + C emitter (monomorphizing), C runtime text, driver | 1,097 |
| Blank lines, comments, docstrings | 211 |
| `gcore.py` total | 1,791 |
| `std.gc` prelude (Option, Result, Eq/Ord/Show, Decimal, Money, sort, Map) | 203 |

It has: generic types and functions with interface constraints; type argument
inference from arguments and from the expected type (`var m: Map[str, int] =
newMap()`); value records, records, closed types, arrays; single ownership with
moves, borrows (`&T`, second-class), drop glue for every instantiated owned
type, path-sensitive move checking, borrow-and-move rejection; statement
temporaries; `try` anywhere in an expression; checked division; a run-time leak
check (every test must end with zero live objects).

The compiler knows two library names, as lang items: a fractional literal
`12.34` calls `fromFraction(1234, 2)`, and `try` needs variants named `Ok`
and `Err`. It has no other knowledge of any library type.

Tests (`run_tests.py`, all passing):

- `examples/money.gc`: `Money[USD]` and `Money[JPY]`, with `+`, `<`,
  `times(rate)` rounding to the currency's minor unit (2 for USD, 0 for JPY).
- `examples/containers.gc`: one `sort[T: Ord]` used on `[]int`, `[]Person`
  (owned records, moved not copied) and `[]Money[USD]`; one `Map[K: Eq, V]`
  used as `Map[str, int]` and `Map[str, Person]`, including replacement,
  which frees the old value.
- `examples/results.gc`: library `Result[int, str]`, `try` inside a loop
  body and inside call arguments; a failing `try` frees the owned temporary
  built earlier in the same expression; `println(makeBox(3).v)` with no
  store-first rule.
- Rejections: Money[USD] + Money[JPY]; a currency missing `scale`; sorting a
  type with no `cmp`; `Map[str, Node].get` (would copy an owned value);
  `dup[T]` instantiated with an owned T (use after move); a value moved on one
  path only; moving a value that another argument borrows.

### Per-feature comparison

| Feature | obo compiler lines | gcore compiler lines | gcore library lines |
|---|---:|---:|---:|
| Decimal | ~1,430 | ~10 (literal lang item) | ~75 |
| Money with currency safety | none (no feature) | 0 | ~35 |
| Result and `try` | ~480 | ~60 (`try` lowering) | 4 |
| Option | built in (`opt`, 27 kind tests) | 0 | 4 |
| Sort over any ordered type | none (per-type copies in lib) | 0 | 9 |
| Map | none | 0 | ~35 |
| Operators on library types | n/a | ~20, shared by every type | n/a |
| The generic machinery itself | n/a | about 150, estimated (unify, inference, instances, constraints) | n/a |

Caveats, so the table is not over-read:

- The prototype's Decimal has a 64-bit coefficient with no overflow checks.
  obo's has 128 bits, checked arithmetic and a multiply cap. A real library
  decimal would be several hundred lines and needs the 128-bit primitive. It
  would still be library code, and the compiler would still not grow.
- The prototype emits C. obo emits x86-64 directly. Codegen size is not
  compared.
- The prototype omits large parts of obo: the reference-invalidation checker
  (section 4 of the language rules), handles, effects, closures, interfaces as
  values, modules and `.sym` files, templates, floats, strings beyond literals.
  None of these are removed by generics. Section 5 says which ones change.

### What the prototype taught

1. **Ownership and monomorphization compose well.** Each generic body is
   checked once per instantiation against concrete types, so "is T owned or
   copied?" is a plain fact at check time. `dup[int]` compiles and `dup[Node]`
   is rejected, with the error labelled `(in dup[Node])`. Drop glue comes out
   per instance with no extra design.
2. **The cost is C++-style errors** (superseded by decision G3: check at the definition). Errors in generic bodies appear at the
   instantiation, not the definition. `Map.get` with an owned V is rejected
   only when someone writes `Map[str, Node].get`. The fix is a built-in
   `copy` constraint, and eventually checking generic bodies against their
   constraints at the definition (Go/Rust style). Start with instance
   checking, since it is much smaller, and add `copy` early.
3. **Store-first rules disappear.** With an AST and per-statement temporary
   frames, temporaries, method calls on call results, and `try` anywhere are
   about 40 lines together. Finding F3 of the review (L2 in its plan) is a
   design issue of single-pass emission, not something to patch.
4. **Declaration order disappears.** Collecting all declarations before
   checking removes finding F2.
5. **Constraints are cheap when static.** Interfaces as constraints cost a
   method-name check and nothing at run time. Interfaces as values (obo's
   `I box`) are a separate feature that can come later.
6. **One open design gap:** closed types are always heap-allocated in the
   prototype, so `Option[Decimal]` allocates. A real core needs inline
   ("value") closed types, or at least inline Option. This also answers L6
   (`?decimal`) from the review: it becomes `Option[Decimal]` with no extra
   compiler work.

## 4. The single-pass question

obo's compiler emits code while it parses, with no tree. That rule kept it
small early on. It is also the root of findings F2 (declaration order) and F3
(store-first rules), and it makes generics awkward: a generic body has to be
kept until its type arguments are known.

There are two ways to keep a generic body:

- **Re-parse it.** Keep the token range and compile it again per
  instantiation. obo already compiles imported modules from source, so this
  fits the current design. It works, but every error and every rule then
  depends on re-entering the parser in the right state.
- **Keep an AST.** Parse once, then walk the tree once per instantiation,
  checking and emitting together. This is what gcore does. There is still no
  separate IR and no optimizer, so the compiler stays small. "Single pass"
  becomes "one pass per instance over a tree".

Recommendation: drop "single pass, no AST" as an invariant and adopt
"parse to an AST, then one check-and-emit walk per instance". This is the key
decision. The rest of this note follows from it.

## 5. What carries over, what changes

Carries over unchanged:

- Single ownership, moves, `&T` as a second-class borrow, `^T`
  generation-checked handles, the reference-invalidation rules.
- The runtime design, the x86-64 code generation knowledge, ELF output.
- The integer rules decided in the review: checked division (L1a) and checked
  arithmetic with explicit wrapping operators (L1b) become core rules.
- The test corpus as acceptance tests, translated to the new syntax where needed.
- eza, fd and jq as the targets that prove the language.

Changes:

- **Built-in types become library:** decimal, Result, Option, and the
  containers obo never had. The compiler keeps primitives (int widths, float,
  bool, byte, str), arrays, borrows, handles.
- **Effects (L4).** Inferred effects work naturally with monomorphization,
  since each instance has a concrete body to summarise. Declared tiers on
  public functions still apply. Constraints may need effect tiers too
  (`T: Ord` where `cmp` must be `nofree`). This needs design before it is
  built.
- **Interfaces** become constraints first, values (`I box`, `&I`) second.
- **Templates (sql/html)** become a desugaring to calls on a library
  protocol, instead of 990 lines in the compiler. This needs a small design
  of its own.
- **Modules and `.sym` files.** Generic code must be exported as source or
  AST, so instantiations can be made in the importing module. The symbol
  file format changes. Oberon's key-based checking still applies.

## 6. Options

**A. Evolve obo.** Add value records first (this alone would have made
decimals D1 to D4b free), then generic modules by recompiling module source
with type arguments, as Modula-3 and Ada do. Lowest risk per step. But it
keeps the single-pass design, so F2 and F3 stay, and each step is a change to
a 17,000-line compiler whose per-type branches would then need removing.

**B. Restart (decided, G2).** Write a new core language and compiler with
generics, value records, operator methods and associated functions from day
one, and the ownership model unchanged. Since obo has no users, there is no
migration cost beyond our own libraries and eza.

Why B: generics touch every part of a checker (types, inference,
ownership, drop glue, effects, symbol files). Adding them to obo means
changing most of the compiler anyway, and removing the special cases on top.
A new core gets the review's L2 (temporaries, try anywhere) and F2
(declaration order) for free, and does not inherit 44 decimal branches.

The risk in B is the usual one for rewrites: losing working features and
spending months before parity. The stages below limit that with gates.

## 7. Plan for option B

Each stage ends with a gate. If a gate fails, stop and reconsider before
going further.

1. **Core spec.** Write the new core rules (grammar in EBNF, types, value vs
   owned, generics and constraints, operators, lang items, ownership rules
   carried from obo, integer rules from the review). Short, one document.
   *Gate:* the spec fits in about the size of the current core guide (440 lines).
2. **Reference compiler.** Grow gcore into a full reference implementation of
   the spec (Python to C): add inline closed types, the `copy` constraint,
   integer widths, strings, handles, the reference-invalidation checker,
   closures, modules. Port a slice of obo's tests. Python is quick to change
   while the design settles. *Gate:* the reference stays under about 5,000
   lines and the ported tests pass with zero leaks.
3. **Library.** Write the real 128-bit decimal, money, Option, Result, Map,
   Set, sort, strings and fmt as library code in the new language. *Gate:*
   no compiler change is needed for any of them, apart from the 128-bit
   primitive.
4. **Self-hosting compiler.** Write the real compiler in the new language,
   compiled by the reference compiler, then by itself. It emits C first
   (G5). Bootstrap from a checked-in seed, then retire the Python compiler.
5. **Native backend and tools.** Direct x86-64 ELF with the runtime built
   in, as obo does now, so the compiler alone builds a program. Retire the C
   backend once native passes the full suite. Then port eza. *Gate:* eza in the new
   language is shorter than eza in obo and runs at least as fast.
6. **Retire obo 0.19.** Freeze it as the previous generation.

Decisions needed before stage 1:

| # | Decision | Recommendation |
|---|---|---|
| G1 | Drop the single-pass, no-AST invariant? | **Decided 2026-11-02:** yes, per module. Each module is parsed to a tree, then checked and compiled in one walk per instance. Modules compile separately; symbol files carry signatures plus the bodies of generic code, and importers compile the instances they use. |
| G2 | Restart (B) or evolve (A)? | **Decided 2026-11-02:** restart. obo 0.19 is frozen at bug fixes only and its parts are reused (ownership rules, x86-64 codegen, runtime, symbol-file keys, decimal arithmetic as a library, tests). Name: open, default keep "obo". |
| G3 | Check generic bodies per instance or at definition? | **Decided 2026-11-02:** at the definition, for types and ownership. T is owned unless `T: copy`; its methods and operators come only from its constraints; interfaces carry full signatures. Code generation and effects stay per instance. First job of stage 2: add this to the prototype and measure it (revisit if it is much more than a few hundred lines). |
| G4 | Reference compiler in Python, or go straight to a self-hosted compiler? | **Decided 2026-11-02:** Python reference first, grown from gcore, to settle the design. Hand off when the language has generics, value records, closed types, modules, strings, Map and Result: write the real compiler in the new language, compile it with the reference, then self-host from a checked-in seed and retire the Python compiler. |
| G5 | First backend: C or direct x86-64? | **Decided 2026-11-02:** the end goal is a self-hosting compiler that emits native x86-64 ELF. C is an intermediate step: the reference compiler emits C, and the first self-hosted compiler emits C to reach self-hosting sooner. A direct x86-64 ELF backend follows before eza is ported; the C backend is retired once native passes the full test suite, unless portability is wanted by then. |

## 8. Relation to the language review

The review's plan (`language-review-2026-11.md`) assumed evolving obo. Under
option B:

- L0 (spec split) becomes stage 1 of this plan.
- L1a and L1b (integer traps) become core rules from the start.
- L2 (temporaries, try anywhere, Result main) is solved by the design.
- L3 is no longer needed as a gate.
- L4 (effect tiers) still applies and must be extended to constraints.
- L5 (deep let) is decided in the new spec.
- L6 (`?decimal`) becomes `Option[Decimal]`, given inline closed types.
