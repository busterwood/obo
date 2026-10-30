# Interfaces (phase 8) — design note

Status: **design only**, for independent review. Baseline: `main` at `85ce90e`
(`Merge branch 'methods-design': methods design note`), read-only in the worktree
`/home/chris/obx/.claude/worktrees/interfaces-design` on branch
`interfaces-design`. The compiler source is 10,527 lines (`obo/src/obo.obo`),
`LANGUAGE.md` 1,853 lines, `FORK.md` 5,085 lines; the promoted seed
`obo/seed/obo` is 425,985 bytes and reports `obo 0.5 sym-format 1 abi 1`.
Nothing is implemented by this pass: no source, test, seed, runtime or tool file
changes. The only repository writes are this file and its one-line entry in
`obo/design/README.md`.

**The methods implementation is in flight.** The approved methods design
(`obo/design/methods-design.md`) targets `OBO_VERSION "0.6"`, `SYM_FORMAT 2`,
`SYM_ABI 1`, a flat `methodSyms` registry, static dispatch, and a receiver that is
hidden parameter 0. This note designs interfaces **on top of that target**: where
it names `methodSyms`, `Sym.recv`, `callFnWith`, `methodDisplay` or `isMethod`,
the reference is to the methods design, not to the current source. Current
`obosym`/compiler line numbers are quoted because the interfaces implementation
must rebase on the methods merge and the line numbers will then shift; treat
function names as the anchors. The recommended interface version bump is
`OBO_VERSION "0.7"`, `SYM_FORMAT 3`, `SYM_ABI` stays `1`; if the final methods
phase uses different numbers, rebase these.

**Revision after design review.** This version addresses the review's four
blocking findings and the material nits: (1) `findImplFor` is explicitly
visibility-filtered and the missing-impl diagnostics are mode-independent, with
full/check tests for a foreign type with no visible implementation; (2) the
read-only impure interface-call rule is restated precisely: the whole-object
receiver binding survives, but `afterCall` with `writesHandles`/`freesAll`
invalidates every deep reference, including references into the receiver, and a
deep receiver itself; (3) the purity proof now covers owned by-value parameters
through ownership uniqueness, the receiver hold and the unconditional
`handleSym` hit in `hitBy`, with tests; (4) the load-bearing safety cases are
enumerated in section 9.6 with concrete tests, and unobservable ones recorded
with reasons. The final decisions are unchanged: keep the `pure` contract,
explicit `pub impl`, `let`-only interface locals, no nil, `d: I`/`mut d: I`,
variant parent step, no interface-to-interface conversion, no forward shells or
mutual interface parameters, no addresses in `.sym`, and no devirtualisation.

**Revision after the second review.** The factored name-tail helpers are now
specified in the actual `parseType`/`symParseType` order: `T_COLONCOLON` first,
then primitives/`Result`/lookup, with source and `.sym` type-declaration-alias
tests for modules named `int`/`Result`. The owned-argument acceptance case is
rooted explicitly (borrow-rooted receiver or a fresh register temporary,
because any owned-local move hits a `handleSym` binding); the stale-token test
order, the three-version-fixture migration wording and the
"after construction" qualification of hidden-free detection are aligned.

Every claim about the current compiler below was checked against
`obo/src/obo.obo` at this baseline; `file:line` references are to that file, and
`runtime.c` references are to `obo/runtime/runtime.c`. The design assumes the
project invariants unchanged: single pass, no AST/IR, source read once,
self-hosting fixed point, textual symbol files with hashes, no test weakening,
one compiler feature in flight, and explicit seed promotion only after review.

---

## 0. Recommendation in one page

1. **Syntax uses two contextual words and no new token.** An interface is a
   named type declared `type I = interface { ... }`; a member is
   `fn name(...)`, `mut fn name(...)`, `pure fn name(...)` or
   `pure mut fn name(...)`. An implementation is a top-level
   `[pub] impl I for T` with no body. `interface`, `impl`, `pure` and `mut` are
   read contextually; the keyword table and token constants are untouched
   (`obo.obo:36-103`, `145-158`), so `--tokens` and `port_test.py` do not change.
2. **Interface types are nominal and second-class.** They name a method set;
   they are not records, closed types, references, handles, optionals or
   `Result` payloads. Interface values exist only as **function parameters and
   `let` locals** (and transient call-argument values); no fields, arrays,
   globals, constants, `Result` payloads, returns, `?I`, `^I`, `&I`, `[]I` or
   `Result[I]`. There is no nil interface: interface locals must be initialised,
   and a `var` interface local is rejected because interface values are bound
   once.
3. **Three ways to make an interface value, all explicit at a typed
   destination:** copy an existing value of the same interface; convert a
   **borrow** of a concrete record/closed value (`&place`, or an expression
   already of reference type) whose type implements the interface; or convert a
   **handle** `^T` whose base type implements it. An **owned** concrete value is
   rejected with a hint to use `&` or `handle(x)`. A variant value uses the
   implementation of its closed type (one parent step). Interface-to-interface
   conversion is rejected: there is no subtyping, variance or structural
   typing.
4. **Representation is a frame-resident pair, not a fat value.** An interface
   value is a one-word pointer to an immutable 16-byte cell
   `{handle token, vtable}`; locals and parameters remain one word. Borrow
   construction creates the token with the existing `RT_HANDLE` runtime call;
   handle construction reuses the handle's token. Every dispatch does
   `RT_DEREF` on the token (so a freed object is exit 7, never a read of freed
   memory) and then an indirect call through the vtable. No runtime change, no
   calling-convention change, no descriptor change; `SYM_ABI` stays 1.
5. **One vtable per (concrete type, interface)**, emitted when the `impl` is
   declared. Slots are the interface's methods in **declaration order**; each
   slot is the address of the concrete method. The implementation must be
   declared in the concrete type's own module (no orphan impls), and every
   method it uses must already be **defined** (not merely a heading) so the
   vtable addresses are final. Addresses live in read-only data and are never
   written to a `.sym`.
6. **Static and dynamic dispatch are separated by the static type.** A call on
   a concrete record/closed value is the methods phase's direct call. A call on
   an interface-typed value is a vtable call. There is no devirtualisation and
   no implicit conversion at a call site; the receiver must already be
   interface-typed.
7. **Purity is the soundness rule for handle-based values.** A member marked
   `pure` requires every implementation to satisfy the compiler's existing
   `pureFn` predicate plus `!writesHandles` (no global frees, no handle writes,
   no freeing below any parameter). Calls through a value whose binding is
   handle-rooted (a handle conversion, or a borrow through a handle) require a
   pure member. Impure members may be called only through borrow-rooted values
   and are given a deliberately conservative summary. A function that calls an
   impure interface method becomes impure, so the existing rule that rejects
   passing a reference that borrows through a handle to an impure callee
   (`obo.obo:4862`) closes the propagation hole.
8. **Mutability is a property of the target, not of the interface value.**
   `mut fn` members need a writable interface value: `bind.writable` from the
   construction; `mut d: I` parameters are writable, `d: I` parameters are
   read-only. A `let d: I = &b` may still call `mut` methods when `b`'s place is
   writable, exactly as `let b = ...; b.add(...)` may today. The interface local
   itself is never reassigned.
9. **Compiler shape:** `Type.kind == "iface"`; a flat `ifaceSyms` registry of
   synthetic method symbols keyed by the interface `^Type`; a flat `impls`
   registry keyed by `(receiver ^Type, interface ^Type)`; a new `ifaceValue`
   conversion helper used at the two legal sites (`let` initialiser and a
   call-argument conversion), and a new `ifaceCall` sibling of `callFnWith`.
   Interface methods are not in `methodSyms` and are not functions in the
   module's global namespace. Declare-before-use applies to interfaces, impls,
   methods and conversions.
10. **Symbol files** gain an `interface { ... }` type definition and an `impl`
    group after the method group. The interface's method names, order, modes,
    purity, parameter types and return type are interface data; an exported
    impl is interface data, a private impl is not. Format becomes
    `obosym 3`, compiler `0.7`, ABI `1`; every committed `.sym` fixture and the
    goldens migrate in the same change. `--check`, `--sym-dir`, `--emit-sym`,
    `--reemit-sym` and `--deps` keep their semantics.
11. **Implementation waits for methods to merge and promote.** The interfaces
    code uses no interface syntax itself, so the 0.6 seed builds the 0.7
    compiler. Round 1 does not refactor `eza`, the library or the compiler.
12. **Out of scope by construction:** interface fields/arrays/globals/returns,
    interface-to-interface conversion, embedding/superinterfaces, owned
    receivers in interfaces, method values, lambdas, generics, variance,
    dynamic loading, multiple inheritance, nil interfaces, `alive` on
    interfaces, and devirtualisation.

The only decision with a real semantic fork is item 7 (the `pure` contract).
The recommended default is to include it; the alternatives are to support only
borrow-rooted interfaces, or to require every interface method to be pure.
Section 12 records this and the other defaults. None needs user escalation
before review.

---

## 1. Scope and non-goals

In scope:

- interface declaration and implementation syntax;
- interface-typed parameters and locals, the conversion rules, mutability and
  borrow/handle tracking;
- static versus dynamic dispatch, vtables, slot order and implementation
  checking;
- ownership, aliasing, invalidation, effect summaries and the exact errors;
- modules and symbol files, `--check`, cross-module and transitive use;
- tests, mutants, docs and gates needed to merge safely.

Not in scope (extension points only):

- interface values in fields, arrays, globals, constants, `Result` payloads or
  return position; nil interface values;
- interface-to-interface conversion, structural typing, variance and
  superinterfaces/embedding;
- owned receivers in interface method sets; method values; lambdas; generics;
- dynamic loading/object files, multiple inheritance, runtime type tests,
  downcasts, reflection or `alive` on interface values;
- devirtualisation, inlining or other optimisation of interface calls;
- refactoring `eza`, the library, the tools or the compiler to use interfaces;
- interface method bodies inside `impl` blocks: methods stay the phase 7
  top-level declarations, and `impl` is a conformance declaration.

The project invariants hold unchanged: single pass, no AST/IR, source read
once, self-hosting fixed point, explicit seed promotion only after review, no
test weakening, textual `.sym` with source/interface hashes, one compiler
feature in flight, design review before implementation.

---

## 2. Syntax and grammar

### 2.1 Interface declaration

```text
InterfaceDecl ::= "type" IDENT "=" "interface" "{" { NEWLINE } { Member } "}"
Member        ::= [ "pure" ] [ "mut" ] "fn" IDENT "(" [ Params ] ")" [ ":" Type ]
                  ( NEWLINE | ";" )
Params        ::= ParamGroup ( "," ParamGroup )*
ParamGroup    ::= [ "mut" ] IDENT ( "," IDENT )* ":" ( "&" Type | Type )
```

- The word `interface` is contextual: it is recognised only after the `=` of a
  `type` declaration, and only when the current token is **`T_IDENT`**. The
  branch consumes that identifier into `w` and tests `w == "interface"` plus
  the **next** token `{`. If both hold, this is an interface definition.
  Otherwise the consumed identifier starts an alias type expression and a
  factored **name tail** (`finishTypeName(w)`) resolves it exactly as
  `parseType` would after reading a name, **in the same order**:
  `finishTypeName` checks **`T_COLONCOLON` first** and resolves a qualified
  `w::member` through the module alias/export; only then does it take the
  bare-name path, where `int`/`byte`/`bool`/`string` resolve to their primitive
  types (universe type symbols), `Result` followed by `[` parses `Result[T]`,
  and any other name is looked up as a type. This order is load-bearing for a
  symbol-file alias such as `type X = int::T` or `type Y = Result::T` where
  `int`/`Result` is a **module name** (the existing
  `sym/module_names_int_result` precedent); primitives-before-`::` would return
  `int`/`Result` and leave `::T` unconsumed. Source spellings of such an alias
  use the explicit import alias (`ii::T`), because an import alias may not be a
  built-in name; the emitted `.sym` uses the declared module name and is the
  collision case. Tokens `?`, `[]`, `^`, `ref` and `closed` are not
  `T_IDENT`, so `type X = ?T`, `type X = []T`, `type X = ^T` and
  `type X = Result[T]` keep their existing paths (the last enters the branch
  only at the `Result` identifier and is finished by the name tail). Thus an
  existing `type X = interface` alias still means what it meant, and no token
  constant, keyword-table entry or scanner rule changes. As with
  `ref record` and `closed`, `interface` and `{` are on one logical line; a
  newline after `interface` scans as a statement separator and the construct is
  an alias, as today.
- `pure` and `mut` are contextual member modifiers, in that order. A member
  with neither has a read-only (`&T`) receiver; `mut` gives it a mutable
  (`mut &T`) receiver; `pure` is the purity contract of section 4.5. There is no
  syntax for an owned receiver in an interface: a caller of an interface value
  never owns its target.
- An interface needs at least one member. Duplicate member names are an error.
- Members are separated by newlines or `;`, exactly like record fields.
  `ParamGroup` mirrors the function parameter parser: one type and mode can be
  shared by several names (`mut a, b: &T`), and each name is one positional
  parameter. Parameter names are syntax only and are canonicalised in the
  symbol file; the signature's data is the ordered list of types and modes.
- `pub` is not allowed on a member; the interface type's own `pub` exports the
  whole method set. A member's parameter list may use `mut` reference
  parameters and interface-typed parameters like any function.
- An interface is a named type, so it may be aliased (`type D = I`), imported
  (`lib::I`) and exported (`pub type I = interface ...`). It is defined in one
  declaration. The type symbol is declared before the members are parsed, so a
  member may name the interface itself (`fn f(other: I)`); an interface return
  is still rejected.
- **A forward interface shell is rejected.** A bare `type I` followed later by
  `type I = interface {...}` is an error
  (`an interface cannot be forward-declared: 'I' is already declared`). The
  shell may already have been used as a record type in a signature, and the
  single-pass compiler cannot safely change that type's kind afterwards. Two
  interfaces therefore cannot mutually name each other in parameters in v1;
  embedding and interface returns are out anyway, so the supported set loses
  nothing.
- `pub` on the interface definition exports the interface and every member.
  There is no forward interface heading for `pub` to be sticky across.

### 2.2 Implementation declaration

```text
ImplDecl ::= [ "pub" ] "impl" IfaceType "for" TypeName
IfaceType ::= Type             // resolved and validated as an interface type
TypeName  ::= IDENT            // the defining name of a type in this module
```

- The word `impl` is contextual at top level. A declaration cannot otherwise
  start with an identifier, so no existing declaration changes meaning. `pub`
  is allowed exactly as on other top-level declarations.
- The interface part is parsed by the ordinary type parser and must resolve to
  a complete interface type. The receiver must resolve to a **record or closed
  type defined in the current module**, must be complete, and must not be a
  variant. An alias to such a local type is transparent and accepted; the
  symbol writer emits the defining name (`appTypeExpr` already emits underlying
  types, `obo.obo:9011-9039`).
- There is no body, no method list and no forward `impl` heading. The statement
  `impl I for T` asserts conformance; the methods themselves are the phase 7
  top-level declarations. Declare-before-use applies: every method named by the
  interface must already be **defined** (its body compiled), so the vtable's
  addresses are final. Methods cannot be declared on a foreign type anyway, so
  an implementation is always beside its receiver; there is no orphan or
  coherence problem.
- One implementation per `(receiver type, interface type)`. A second one is
  `type 'T' already implements interface 'I'`. Because the receiver must be
  local, two modules cannot both declare it.
- `pub impl` is exported; a private impl is invisible to importers. Conversion
  lookup (`findImplFor`, section 5.1) accepts only
  `impl.owner == curModuleName || impl.exported`, so the same rejection happens
  in a full build (where the private live impl exists) and in `--check` (where
  it was never written). A failed lookup for a foreign receiver reports the
  module-member error, naming the parent closed type for a variant; a local
  receiver with no visible impl reports no-implementation (section 8.3). A
  `pub` impl requires both the interface and the receiver type to be exported
  (the existing export closure rules are extended).
- The implementation's methods may be private. Importers call through the
  interface's signatures, never through the concrete method names. The vtable
  address is live data in the defining module, not a symbol-file address.

### 2.3 Creating interface values

There is no new conversion operator and no `as` expression. A value is coerced
to an interface only at a typed destination, and only in two places:

```text
IfaceLocal ::= "let" IDENT ":" Type "=" IfaceSource
Argument   ::= ... IfaceSource ...              // for an interface-typed parameter
IfaceSource ::= Designator                        // same interface type: a copy
             | "&" Designator                     // a borrow of a concrete value
             | Designator or Call                 // a handle value, type ^T
```

The rules are semantic, not syntactic:

- **Copy:** the source's static type is exactly the destination interface type
  (aliases transparent). `let e: I = d` copies the one-word cell pointer and
  rebinds to the same target. An **inferred copy** `let e = d` is blessed and
  does the same: `varDecl`'s existing reference-local branch sees a borrowed
  value of interface type and creates an interface reference local with the
  same binding. Inference never creates an interface from `&T` or an owned `T`;
  `let e = &b` remains an ordinary reference local to `b`, and `let e = b`
  remains an owned local of `T`. The inferred form is still subject to the
  bound-once rule: `var e = d` is rejected with the same
  `an interface value must be declared with let` error as `var e: I` (the
  generic reference-local branch must check the inferred type before creating
  a mutable reference local).
- **Borrow:** the source is a **borrowed** item of a concrete record or closed
  type `T`. This is produced by `&place` (factor `T_AMP`,
  `obo.obo:5882-5891`), by a read of a reference parameter/local
  (`nameItem` sets `x.borrowed = s.isRef`, `obo.obo:5539-5540`, and the
  designator tail marks a field/element reached through a reference borrowed,
  `obo.obo:5715-5722`), and by a reference expression already of type `&T`.
  `T` must implement `I` (with one parent step for a variant, section 4.4).
- **Handle:** the source's static type is a handle `^T`; `T` must implement
  `I` (one parent step for a variant). The handle token is copied into the
  cell. A handle expression may be a local/parameter, a field, a call result or
  `handle(place)`.
- **Owned:** a bare owned `T` (`let d: I = b`, a field/element, a constructor,
  a function result) is **not** accepted. The error names the remedies:
  `cannot use owned 'T' as interface 'I': borrow it with '&' or make a handle
  with handle(...)`. Passing an owned value to an interface parameter has the
  same rule.
- **Optional:** a `?T` must be narrowed first; the narrowed local is owned and
  still needs `&`. `?I` is not a type.
- **Interface-to-interface:** rejected. `let d2: I2 = d1` where `d1: I1` is
  `cannot use interface 'I1' as 'I2': interface values do not convert; convert
  from the original value`. This holds even if the method sets look alike:
  there is no structural typing, no subtyping and no variance.
- **Bare reference values:** `fn f(r: &Circle) { let d: Drawable = r }` is a
  borrow conversion: `r` is already a borrow of `Circle`. `&r` is accepted too
  but redundant. `h.child` (a field through a handle) is not a borrow and is
  rejected; write `&h.child`.

### 2.4 Calls

```text
Call ::= Designator "." IDENT "(" [ Args ] ")"
```

- Same surface as methods. The decision is by the receiver's **static type**:
  a record or closed receiver uses the methods phase's direct call; an
  interface-typed receiver uses a vtable call. Fields are unreachable on an
  interface value, so every `.name` is a method reference.
- `d.name` without `(` is `interface method 'I::name' cannot be used as a
  value: call it with '(' after its name`. `d.name()` with an unknown name is
  `interface 'I' has no method 'name'`. A field-style error is
  `interface 'I' has no fields; it has methods only`.
- A method call ends the designator, exactly as phase 7's method calls do:
  `d.m().n` and `d.m()[0]` are syntax errors at the enclosing construct.
  The result may be bound, returned, printed, stored in a field/element/global,
  used as an argument, a condition, part of `&&`/`||`, the operand of `try`, or
  the statement of a `defer`, subject to all existing rules.
- A `mut` member needs a writable interface value (section 3.5). A read-only
  value gives `the receiver of interface method 'I::m' needs a mut interface
  value, but 'd' is read-only`.
- Interface values cannot be compared, indexed, matched, borrowed, handled,
  printed, dropped, taken, replaced or passed to `alive`; each reuses the
  existing type error or gets a specific one (section 8).

### 2.5 Examples

These are the proposed LANGUAGE examples; they are not added by this pass.

```obo
type Shape = closed { Circle { r: int } Square { s: int } }

type Drawable = interface {
    pure fn area(): int
    pure fn name(): string
    pure mut fn scale(k: int)
}

fn (s: &Shape) area(): int {
    match s {
        Circle { return 3 * s.r * s.r }
        Square { return s.s * s.s }
    }
}
fn (s: &Shape) name(): string { return "shape" }
fn (mut s: &Shape) scale(k: int) {
    match s {
        Circle { s.r = s.r * k }
        Square { s.s = s.s * k }
    }
}

impl Drawable for Shape

fn describe(d: Drawable): string { return d.name() }       // read-only target
fn grow(mut d: Drawable, k: int) { d.scale(k) }             // mutable target

fn main() {
    let c = new Circle{r: 2}
    let d: Drawable = &c
    println(d.area())          // 12: a vtable call
    println(describe(d))       // shape
    grow(d, 2)                 // d is writable: &c is a writable borrow
    println(d.area())          // 48

    let h = handle(c)
    let dh: Drawable = h
    println(dh.area())         // pure members may be called through a handle
}
```

`scale` only writes scalars, so its summary is pure; because the member is
marked `pure mut`, a handle-based value may call it and `grow` (which calls only
a pure member) is itself pure. If `scale` assigned an owned field it would be
impure: it could no longer implement the `pure` member, and a handle-based call
to an impure member would be refused.

```obo
fn areaOf(r: &Circle): int {
    let d: Drawable = r      // r is already a borrow; no '&' needed
    return d.area()
}
```

```obo
fn bad(c: Circle) {
    let d: Drawable = c      // error: owned value
}
```

---

## 3. Values, representation and ownership

### 3.1 Why second-class

The phase 8 roadmap is deliberately conservative: interface values are
parameters and locals only, with data a borrow `&T` or a handle `^T`. That
restriction is what makes a **frame-resident pair** sound without a new
ownership story:

- every interface value is created in a function activation and used only while
  that activation is live (no return, field, global, array or `Result` escape);
- a cell is immutable; a copy copies the cell pointer, never the pair;
- interface locals are bound once (`let` only), so a construction site's cell is
  rewritten only by re-executing the same site, and no copy can survive to
  observe the rewrite: loop locals die with the iteration, argument cells die
  when the callee returns, and a caller cannot store a callee's cell anywhere;
- recursion creates a fresh frame and therefore fresh cells;
- a `defer` runs before its frame is destroyed.

Consequently no interface value ever dangles *as a cell*. The object it names
is a separate question, handled by the borrow rules plus the runtime handle
token (section 3.6).

### 3.2 The cell, the token and the vtable

An interface value is one word: the address of a 16-byte cell in the current
function's frame:

```text
cell: [ token: u64 ][ vtable: u64 ]       // 16 bytes, 8-byte aligned
```

- `token` is a **runtime handle token** (the same representation as `^T`): the
  object's offset in 16-byte units plus the generation of its heap block
  (`runtime.c:311-315`). A borrow construction creates it with `RT_HANDLE`
  (which reads the header; the construction-liveness limit is in section 3.6);
  a handle construction copies the existing token without reading the object.
- `vtable` is the address of the `(concrete type, interface)` vtable in
  read-only data. `--check` uses a dummy address because it discards code.
- Both construction paths store a token, so dispatch is uniform:
  load `token`, call `RT_DEREF` (`runtime.c:325-330`), push the object pointer
  as method argument 0, and call `[vtable + 8 * slot]`. A freed object exits 7
  at the dereference; the generation check makes reuse at the same address
  safe (`runtime.c:317-323`, `243-246`). The existing `RT_HANDLE` and
  `RT_DEREF` slots are `26` and `27` (`obo.obo:1256-1258`); the runtime does
  not change.
- Using a handle token even for a borrow is an implementation choice that buys
  a uniform dispatch path and a runtime backstop. The language-level borrow is
  still tracked as a reference (section 3.5), so visible frees are compile
  errors rather than runtime exits.
- A cell is not owned: it contains no owned value, needs no free, and
  `freeOwned` (`obo.obo:2515-2533`) ignores it because the local is a
  reference local.

### 3.3 Locals and parameters

- `let d: I = source` allocates the cell (unless the source is an interface
  copy) and a one-word local; the local is registered with `makeRefLocal`
  (`obo.obo:3027-3033`) so the existing reference machinery tracks validity,
  aliasing, loops, defers and moves. It is not an owned local; scope exit frees
  nothing.
- `var d: I = ...`, and the inferred `var e = d`, are rejected:
  `an interface value must be declared with let: it is bound once and cannot be
  reassigned`. There is no interface assignment anywhere; `mut d: I`
  parameters mutate the *target*, not the pair.
- `let d: I` with no initialiser is rejected: `an interface value needs an
  initial value; it cannot be nil`.
- A function parameter is `d: I` (read-only target) or `mut d: I` (mutable
  target). Both are one-word ABI parameters holding a cell pointer; internally
  they are reference-like parameters (`FnRec.pmodes` 1 and 2), so the existing
  parameter setup gives them the ordinary reference-parameter binding
  (`singleBind(s, false, "", mode == 2, nil)`), and the existing effect
  summaries, alias checks and holds work unchanged (`fnBody`,
  `obo.obo:8140-8162`). The slot holds a **cell pointer**, not a `T` pointer;
  the reference machinery uses the parameter's binding and never treats the
  slot's contents as a `T`, so there is no aliasing of the two meanings.
  An interface parameter may point into the caller's object, but the parameter
  binding is abstract exactly as for an ordinary reference parameter: a mut
  call through it records the free on the current function's `pfree`/`pf` row
  for that parameter, and the caller applies it to the actual place. The
  `.sym` spells interface parameters `p0: I` and `mut p0: I`; `&I` is not a
  spelling (and `parseRefType` rejects it because an interface is not owned,
  `obo.obo:3590-3601`).
- Interface-typed parameters may be passed onward, copied to locals and used in
  defers; the callee cannot store them beyond its activation. The binding roots
  at the parameter symbol, and a mut call through it records the free on the
  current function's `pfree`/`pf` row for that parameter
  (`callFrees`/`recordParamFree`, `obo.obo:4740-4759`, `3424-3433`), which is
  how the caller learns the effect.
- The receiver of an interface method call is always a bare local or parameter
  (interface fields and globals do not exist), so the receiver designator is
  one item; no field/index/handle path is possible through an interface.

### 3.4 Construction matrix

| source expression | allowed | binding of the new value |
|---|---|---|
| same interface type `d` | yes, copy | the source's binding through `itemBind` (`obo.obo:2938-2961`) |
| `&place` of record/closed `T` | yes | `borrowBind` of the place (`obo.obo:2964-2973`): root + path + writability |
| bare reference parameter/local of `T` | yes | the reference's own binding (roots, deeps, paths, writability) |
| field/element reached through a reference (`r.f`, `r[i]`) | yes, already borrowed | the reference's binding plus the path |
| handle value `^T` (local, field, call, `handle(x)`) | yes | `singleBind(handleSym, true, "", true, nil)` (`obo.obo:1094-1102`) |
| owned local/field/element/global/constructor/result of `T` | no | `cannot use owned 'T' as interface 'I': ...` |
| optional `?T` | no (narrow first) | existing nil error |
| another interface type | no | interface-to-interface conversion error |
| array/string/`Result`/`^?T` | no | no concrete record/closed type with an implementation |
| global initialiser | no | `an interface value cannot be created outside a function` |

A variant value of type `V` is accepted in the borrow and handle rows when its
closed type `C` implements the interface; the implementation key is `(C, I)` and
the data pointer is the variant object. This mirrors the methods phase's
one-parent-step dispatch.

### 3.5 Writability, mut receivers and aliasing

- The **target's** writability is the binding's `writable` bit produced by the
  construction. A whole local, or a by-value concrete parameter, is writable; a
  read-only reference is not; a field/element inherits the place's writability;
  a field reached through a read-only reference is not writable; every
  handle-rooted value is writable (writes through handles are allowed by the
  existing language). An interface parameter's writability is its declared
  mode, not the caller's value.
- `mut fn` members require `binding.writable`. A `let d: I = &b` is writable
  whenever `b`'s place is writable, so `d.scale(2)` is legal even though `d` is
  a `let`; this is the same rule as a `let` record calling a `mut` method.
  A read-only reference (`fn f(r: &T) { let d: I = r; d.scale(2) }`) is
  rejected.
- `mut d: I` parameters are writable; `d: I` parameters are read-only. Passing
  a writable interface value to a `d: I` parameter is a legal downgrade;
  passing a read-only one to `mut d: I` is
  `argument N of f needs a mut interface value, but this one is read-only`.
- Read-only members may be called on any interface value. The member cannot
  free below its receiver, so its receiver has no pfree bit 0 event. But an
  **impure read-only member's synthetic summary sets `freesAll` and
  `writesHandles`**, and `afterCall` then calls `invalidateDeep(nil)` and
  `invalidate(handleSym, ...)`, and invalidates every visible global. The
  consequence is precise: a whole-object receiver binding (`deep == false`)
  **on an accepted call** stays valid, but **every deep reference, including
  references into the receiver, is invalidated**; a receiver whose own binding
  is deep (a field/element borrow) is itself invalidated. An impure call on a
  global-rooted receiver is refused before codegen by the global check, so it
  is not an exception here. Only a pure read-only member leaves references
  valid. A mut member adds the receiver's pfree events and can invalidate the
  receiver's roots as well. Section 9.6 records the exact accept/reject cases;
  §4.5 states the same rule for effect summaries.
- Aliasing is the existing reference model. Two interface values copied from
  one construction share the cell and bind the same roots. A mut call through
  one invalidates references into the target exactly as a mut method call does
  today; the receiver itself stays valid (`ancestorOf`/`invalidate`,
  `obo.obo:3192-3211`). A later argument that frees the receiver's target is
  refused by the argument hold (`holdArg`/`argOrderCheck`,
  `obo.obo:4692-4721`). The mut-alias pair loop (`obo.obo:4832-4849`) is reused
  with receiver-aware labels.
- A move of the owner (`var c = b`, passing `b` by value, `drop(b)`,
  `replace` on the owner) invalidates an interface value bound to it, because
  `consume`/`invalidate` marks every reference local whose binding is hit
  (`consume`, `obo.obo:2861-2896`; `invalidate`, `3192-3211`). A later use is
  the existing moved-reference error.

### 3.6 Nil, dangling and runtime checks

- **There is no nil interface *cell*.** Interface locals require an
  initialiser; `var` is rejected; `?I` is not a type; no literal produces an
  interface; a parameter always receives a constructed cell from a caller.
  `d == nil` is a type error (interface values cannot be compared). A zero
  handle token can only arise from a nil handle, which the type system
  prevents (`^T` is non-null and `?^T` must be narrowed); if one is ever
  stored, the first `RT_DEREF` exits 7 rather than dereferencing nil. A
  conversion adds no separate nil check.
- **Construction-time liveness.** A borrow conversion calls `RT_HANDLE`, which
  reads the object's header to capture its generation; it does not validate
  that the source borrow is live. The reference rules guarantee that for
  visible frees. A hidden free *before* a borrow conversion would make
  `RT_HANDLE` an unvalidated read, the same residual class as an existing
  hidden reference use; the token guards frees **after** construction. A
  handle conversion copies a token without reading the object, so a stale
  handle field is caught by the first `RT_DEREF` (section 9.6d).
- A **borrow-rooted** value is valid by the reference rules while its binding is
  valid. A hidden free the compiler cannot see **after construction** is still
  caught by the token: `RT_DEREF` exits 7 if the generation is gone.
- A **handle-rooted** value names whatever the handle named at conversion. Its
  binding roots at `handleSym`, exactly like a reference that borrows through a
  handle (`itemBind`, `obo.obo:2938-2941`), so **any visible change or free, and
  any call that might free, invalidates it at compile time**. This is
  deliberately conservative: a handle-rooted interface value may need to be
  made again after an unrelated free, just as a `&h.field` reference does;
  allocation alone does not invalidate it. The value is not registered in the compiler's `originSyms` let-handle
  origin lists, so a `let` handle's specific dead mark is not copied; the
  `handleSym` invalidation and the runtime token together are the guarantee.
  Calls do `RT_DEREF` every time, so a hidden free the compiler cannot see
  **after construction** is exit 7, not a use-after-free. There is no `alive` on interface values; check
  the original handle if a probe is needed, understanding that the answer can
  go stale.
- Runtime exits are the backstop for hidden aliases; visible frees are compile
  errors. This matches the handle rules the language already documents
  (`LANGUAGE.md` §5) and adds no new failure mode beyond exit 7.

---

## 4. Dispatch and vtables

### 4.1 Static versus dynamic

| receiver's static type | resolution | code |
|---|---|---|
| record or closed | static, at the call site, one method per `(type, name)` | direct `E8 rel32` (methods phase) |
| `V` (a variant) calling a closed-type method | static through `Type.parent` | direct call to `C`'s method |
| interface `I` | dynamic: `findIfaceMethod(I, name)` picks the slot; the object's vtable picks the code | `RT_DEREF`, load vtable, indirect call |
| built-in/derived (`int`, `string`, `[]T`, `?T`, `^T`, `Result[T]`) | no methods | existing `has no methods` |

An interface value never implicitly becomes a concrete value, and a concrete
value never implicitly becomes an interface value at a call site; the
destination's declared type drives the explicit conversion (section 2.3).
A local of interface type, or an interface conversion passed as an argument,
is how a value reaches a dynamic call; the callee then dispatches through the
same slot machinery. Allocation alone does not make a value interface-typed,
and (as section 3.6 says) allocation alone does not invalidate a handle-rooted
binding.

### 4.2 The implementation registry and the vtable

For each `impl I for T`, the compiler builds one vtable:

```text
vtable(T, I) = [ address of T::m0, address of T::m1, ... ]
```

- The slot list is `ifaceMethodsOf(I)` in **interface declaration order**. The
  order is canonical interface data and appears in the `.sym`; it is not sorted
  or inferred.
- `T` is the record or closed type the impl names. A variant value uses its
  closed type's vtable; the method expects `&C` and the object is a `C`-layout
  variant, which is exactly what the methods phase's parent step already does.
- The implementation must be in `T`'s own module. The conversion lookup is
  visibility-filtered `findImplFor(recv, I)` (section 5.1): it tries the
  visible `(recv, I)` pair and, for a variant, one visible parent step; a raw
  private pair is used only by the writer/mode-0 validation.
- Methods must be **defined** before the impl. Then their addresses are final
  even though `dropFrame` can move a function's entry (`obo.obo:7777-7791`),
  because that happens inside the method's own `fnDecl` before the impl is
  parsed. A forward heading is refused:
  `method 'T::m' must be defined before the implementation of interface 'I'`.
- The vtable is emitted with `rodAlign`/`rod64` (`obo.obo:1953-1963`) as 8-byte
  absolute code addresses; descriptors already use absolute rodata addresses,
  so no new relocation machinery is needed. No `.sym` contains an address.
- `Impl.vtable` is the address used by conversion sites. In `--check` an
  imported impl gets a dummy address because code is discarded; the checked
  module's own impl gets a real one.
- There is one vtable per pair even if several types or interfaces could share
  code. A type may implement several interfaces, with one vtable each. A
  private impl still gets a vtable for its own module; it is simply not
  exported.
- An implementation may live in a **downstream** module: `lib` declares `I`
  and a function `use(d: I)`, while the root imports `lib`, defines `T`,
  implements `I` and passes a `T` value. The root's vtable address travels in
  the cell; `lib`'s compiled call merely loads it, so no dependency's code
  needs a relocation to the root. This is why the cell, not the callee, carries
  the vtable pointer.

### 4.3 Matching an implementation

For every interface member, the impl requires a method on the receiver type
with the same name and all of:

- **receiver mode**: `fn` requires `fn (r: &T)`, `mut fn` requires
  `fn (mut r: &T)`; modes match exactly;
- **parameters**: same count, same types by type identity (aliases
  transparent), same modes (owned/`&`/`mut`), and interface parameters with the
  same read-only/mutable target mode;
- **return type**: same by type identity; an interface return is impossible;
- **purity**: a `pure` member requires the concrete method to satisfy
  `pureFn(method) && !method.writesHandles` (section 4.5).

A missing method is `type 'T' has no method 'm' to implement interface 'I'`.
A wrong signature names the first difference:
`method 'T::m' does not match interface 'I' method 'm': ...`. Duplicate names
inside one interface are a declaration error. A method named `main` is allowed
and is not the program entry.

Because there is no overloading, one concrete method per `(T, name)` must
satisfy every interface that uses that name. If `I1` and `I2` both declare `m`
with different signatures or receiver modes, a single `T` cannot implement
both; the second impl reports the mismatch. If the signatures agree, one
method satisfies both slots. Dispatch is still never ambiguous: a call through
`I1` uses `I1::m`'s slot and a call through `I2` uses `I2::m`'s slot.

### 4.4 Closed types, variants and identity

- An impl's receiver must be a record or closed type with `parent == nil`; a
  variant cannot be an impl receiver (`declare it on its closed type`).
- A variant-typed value `V` converts to `I` when `V.parent` (the closed type
  `C`) implements `I`. The vtable is `(C, I)`, the slot methods are `C`'s
  methods, and the data pointer is the variant object. This is one parent step,
  the same as methods; closed types have no further parent.
- Interface types are **named**: identity is the canonical `Type` object
  (owner + name + uid), exactly like records. Two modules declaring structurally
  identical interfaces have two types; aliases are transparent; a conversion
  keyed by a pointer never confuses them. In a full build the defining module's
  object is shared; in `--check` the reader reconstructs one object and reuses
  it for every signature, impl and call.
- There is no interface-to-interface conversion, so an `I1` value can never
  become an `I2` value even if the dynamic type implements both. The design
  does not need a runtime type tag or an itable lookup. This is the main reason
  the representation can stay a pair.

### 4.5 The purity contract

Dynamic dispatch means the call site cannot see the implementation's effect
summary. The compiler needs a sound, static summary for every interface call:

- A member **without** `pure` is conservatively impure. Its synthetic call
  summary sets `freesAll = true` and `writesHandles = true`; `freesAll` makes
  `mutFrees` true for every parameter, so:
  - a `mut` receiver invalidates references below the receiver, and records
    "what lies below" events for handles and reference parameters;
  - any `mut` argument may free below that argument;
  - the caller's `freesAll`/`writesHandles` are set by `afterCall`
    (`obo.obo:3459-3491`), and `userCalls` counts the call;
  - the call is not pure, so passing a reference or interface value that
    borrows through a handle is refused by the existing handle-borrow rule
    (`obo.obo:4859-4866`).
  A read-only member keeps `pfree` bit 0 clear (it cannot free below its
  receiver), but its global/handle effects are the worst case. Precisely,
  `afterCall` sees `writesHandles` and calls `invalidateDeep(nil)`, so **every
  deep reference, including references into the receiver, is invalidated**; a
  whole-object receiver binding (`deep == false`) survives on an accepted
  call, while a receiver whose own binding is deep is itself invalidated. An
  impure global-rooted receiver is refused before this point. Only the absent
  pfree bit 0 says the callee cannot free below the receiver; it does not say
  references into it stay valid.
- A member **marked `pure`** requires every implementation to satisfy
  `pureFn(method) && !method.writesHandles`: it is compiled, does not free a
  global, does not write/free through a handle, and frees nothing below any
  *reference* parameter (`pureFn`, `obo.obo:3359-3366`). Owned by-value
  parameters are allowed and may be freed by the callee; section 4.5's proof
  covers why they cannot alias the receiver's target in an accepted call.
  `pure` here is the compiler's ownership/effect predicate, not a promise of no
  observable side effects: a pure `mut` member may still write scalars or
  append. The synthetic call summary then has `freesAll=false`,
  `writesHandles=false`, `freed` empty and `pfree=0`; the call is pure by the
  compiler's own predicate, keeps references valid and may be made through a
  handle-rooted value.
- The purity check happens at the impl, after the method body is compiled and
  before the vtable is emitted. A method that calls a forward-headed function
  is conservatively impure (`pureFn` returns false for `addr < 0`), so the
  ordering rule may force the function's definition earlier. This is the same
  conservative behaviour as direct calls.
- The rule is checked in the defining module; importers trust the interface's
  declaration and recheck only when they rebuild. The interface's purity bits
  are part of its interface hash, so a purity change rebuilds dependants and
  their impls.

**Owned by-value parameters.** `pureFn` does not count mode-0 parameters, and
`fnBody` frees an owned parameter at return, so a pure member may take an owned
argument. This does not break the handle-rooted argument:

- **Uniqueness.** Moving the argument transfers the only owner into the callee;
  the object is freed at the callee's return, after the member call itself has
  completed.
- **No alias can survive the move.** `consume(owner)` calls
  `invalidate(owner, all = true)` (`consume`, `obo.obo:2861-2896`). `hitBy`
  returns true unconditionally for a binding whose root is `handleSym`
  (`obo.obo:3111-3121`), so moving an owned value that is, or **owns through
  owned fields/elements**, the receiver's target invalidates a handle-rooted
  receiver binding; a reference-rooted binding at that owner is hit by the
  same invalidation. A handle or borrow inside the owned value is not ownership
  containment and does not free the target when the owned value is freed.
  The receiver is held with `holdArg`/`argOrderCheck` before the later argument
  runs, so an owned argument that moves the receiver's owner or ancestor is
  refused at that argument (`argument N ... may free what the receiver points
  into ...`, or the moved-reference error if the argument runs first).
- **Conservative consequence for handle-rooted receivers.** Because `hitBy`
  treats every `handleSym`-rooted binding as hit by *any* `invalidate`, moving
  **any owned local** invalidates a handle-rooted receiver or argument holder,
  even when the local is unrelated to the target. The hold check therefore
  refuses an unrelated owned **local** argument beside a handle-rooted
  receiver. The accepted-and-stored case must use a **borrow-rooted receiver
  whose binding has no `handleSym` root** (an ordinary local/field borrow,
  whose roots do not include the moved local) or a **fresh register temporary**
  (`new ...`; `consume` returns early for a register, so nothing is
  invalidated). This is conservative but sound.
- **Early free in the callee.** If the callee frees an owned argument early, an
  accepted call guarantees that the argument is not the receiver's target or
  ancestor: moving such an argument would have invalidated the receiver binding
  and the call would have been rejected. A pure member may therefore free any
  owned argument it receives without endangering the receiver.

Tests: a pure `mut` member appending an unrelated owned argument compiles and
runs when the receiver is an **ordinary borrow-rooted** value (no `handleSym`
root) or the argument is a fresh register temporary; an unrelated owned local
beside a handle-rooted receiver is conservatively refused by the hold check; an
owned argument that is the receiver's owner/ancestor beside a handle-rooted or
ordinary borrow-rooted receiver is rejected (section 9.6e). If review prefers a simpler rule, the fallback is to
ban mode-0 parameters on `pure` members at the interface declaration, impl
check and symbol reader; the proof above is the reason the recommended design
does not need that restriction.

**Why handle-rooted calls need this.** A handle names an object whose owner the
callee does not know. If an impure member could free that object through a
global handle or a global owner while it runs, the receiver pointer obtained by
`RT_DEREF` at entry could be freed during the call. Requiring a pure member for
handle-rooted dispatch means the callee cannot free globals, handles or below
its parameters, so the object the token named at entry cannot be freed during
the call by this call's own code. The same rule is enforced in two places:

1. at the call, when the receiver's binding has a `handleSym` root
   (`cannot call interface method 'I::m' through a handle: it is not pure, and
   might free what the handle names`);
2. at any call that passes a handle-rooted **interface value** to a function or
   method parameter, through the existing
   `b.roots[p] == handleSym && !pureFn(f)` check (`obo.obo:4862`), strengthened
   for interface arguments to `pureFn(f) && !f.writesHandles`.

The second check is what makes the abstraction sound: a function
`fn f(d: I) { d.impure() }` is itself impure (`freesAll`/`writesHandles`
propagate), so no caller may pass it a handle-rooted value. A function that only
calls pure members stays pure and may receive handle-rooted values. A
borrow-rooted value (root local, field, global, or reference parameter) is not
handle-rooted; impure calls are allowed under the reference rules, which know
the owner.

### 4.6 Performance

- Conversion from a borrow costs one `RT_HANDLE` call plus two stores;
  conversion from a handle is two stores; a copy is one pointer copy.
- A dispatch costs one `RT_DEREF` (a bounds/generation check), one vtable load,
  and one indirect call. There is no inlining or devirtualisation in v1; an
  interface call is expected to be slower than a direct method call, in the
  same range as a handle dereference plus a call.
- Pure members let callers keep references valid and avoid the conservative
  invalidation of impure interface calls. Impure members intentionally behave
  like a callee that frees anything, so code that needs precision should expose
  a pure member where the implementation genuinely is pure.
- The vtables, synthetic method records and impl records are small; the design
  adds no runtime table, no heap allocation per interface value and no per-call
  allocation.

---

## 5. Compiler architecture

### 5.1 Data model and registries

`Type.kind` gains `"iface"`. An interface type has an owner and name like any
named type, a uid from `nameType`, an empty field/variant list and
`desc = 0`. It is not owned, not a reference, and not non-null in the optional
sense. `newType` (`obo.obo:899-902`) needs no new field; interface methods are
not stored in `Type`:

```obo
// one synthetic symbol per interface member, in declaration order; f.recv is
// the interface ^Type. These are NOT in methodSyms and are never module globals.
var ifaceSyms = new []^Sym(0)

// one record per declared implementation, in declaration order.
type Impl = ref record {
    iface: ^Type
    recv: ^Type
    owner: string
    exported: bool
    line: int
    vtable: int            // rodata address; 0 in --check for an imported impl
    slots: []^Sym          // the concrete methods, or empty when reconstructed
}
var impls = new []^Impl(0)
var implArena = new []Impl(0)
```

A synthetic interface method symbol reuses `Sym` from the methods phase:
`kind = "ifn"`, `name`, `owner`, `recv = I`, `line`, a fresh `FnRec` whose
`params = [I] + explicit params`, `pmodes = [receiver mode] + explicit modes`,
`ret`, and the synthetic summary of section 4.5
(`addr = 0`, `freesAll`/`writesHandles` from purity, `pfree = 0`). `addr = 0`
is deliberate: it makes `pureFn` treat a pure synthetic as compiled, while
`kind = "ifn"` prevents any direct call path. Helpers:

```obo
fn ifaceMethodsOf(t: ^Type): []^Sym         // ifaceSyms filtered by recv == t
fn findIfaceMethod(t: ^Type, name: string): ?^Sym
fn ifaceMethodDisplay(f: ^Sym): string      // I::m or M::I::m
fn findImplIn(recv: ^Type, iface: ^Type): ?^Impl     // raw pair lookup, no visibility
fn findImplFor(recv: ^Type, iface: ^Type): ?^Impl    // visible only; one parent step for a variant
fn implKey(i: ^Impl): string                // recv.name + " as " + interface key
fn implBase(recv: ^Type): ^Type              // recv, or its closed parent for a variant
fn implMissing(recv: ^Type, iface: ^Type)     // the mode-independent error; never returns
fn handleSafe(f: ^Sym): bool { return pureFn(f) && !f.writesHandles }
```

`findImplFor` is the **only** lookup used by conversions and is
visibility-filtered: it accepts a pair only when
`impl.owner == curModuleName || impl.exported`, then tries the receiver and,
for a variant, one `parent` step. `findImplIn`/the raw registry remain available
for the writer and mode-0 validation, which must see private live impls.
The filter is applied identically in a full build and in `--check`; a private
live impl in another module is rejected even though the full-build registry
contains it. `implMissing` names the parent closed type `C` for a variant `V`:
a foreign `C` gives the module-member error, a local `C` (or a local `V` with a
local or no parent implementation) gives the no-implementation error. No
conversion site may bypass this helper with a raw lookup.

`releaseTables` (`obo.obo:10361-10379`) clears `ifaceSyms`, `impls` and
`implArena` with the other arenas. The registries are declared after `tVoid`
and the `Sym`/`Type` definitions, like the methods phase's `methodSyms`; they
must not be touched by function-local arena truncation, which is safe because
no interface or impl is declared inside a function.

**Why a flat registry again.** The whole program is one image and a canonical
`^Type` is the identity in a full build and in `--check`. Interface and impl
counts are small; call sites scan a few entries. This avoids a `Type` layout
change and per-type list allocation, and matches the methods phase's
`methodSyms` precedent.

### 5.2 Parser and declarations

- `compileUnit` (`obo.obo:8451-8567`) currently dispatches on `sym`. After the
  optional `pub`, an `T_IDENT` whose text is `impl` is a new impl declaration;
  every other identifier remains `expected a declaration`. No valid source
  changes meaning. An `impl` identifier inside a function is still an ordinary
  identifier.
- `typeDecl` (`obo.obo:3968-4059`) gains the contextual branch after `=`
  **only when `sym == T_IDENT`**: save `w = sval`, `next()`, and if
  `w == "interface"` and `sym == T_LBRACE`, parse an interface definition;
  otherwise call a factored `finishTypeName(w)` that reproduces the name tail of
  `parseType` **with its order**: `sym == T_COLONCOLON` first (qualified
  `w::member` through `exportOf`), then the bare name (`int`/`byte`/`bool`/
  `string` universe types, `Result` + `[T]`, or `lookup`). The `?`, `[]`, `^`,
  `ref` and `closed` paths never enter this branch, so existing aliases are
  untouched. The interface branch creates and declares
  the type symbol at the start (a prior `type I` shell is the forward-shell
  error, section 2.1), sets `kind = "iface"`, parses members until `}`, rejects
  an empty interface and duplicate names, checks the export closure later, and
  clears `incomplete`. The type symbol is declared before members so a member
  may name the interface itself (`fn f(other: I)`); an interface return is
  rejected.
- `implDecl` parses the interface type with the ordinary type parser (and
  validates `kind == "iface"`), expects `for` (`T_FOR`), then reads the receiver
  name. It resolves it by ordinary lookup, requires `kind` record/closed,
  `owner == curModuleName`, `!incomplete` and `parent == nil`, checks the
  method set and purity, and appends one `Impl` record. Duplicate keys are
  rejected. `pub` is the declaration's own flag; there is no forward impl and
  no stickiness to consider.
- The interface member parser shares the function parameter parser, with the
  interface-type special case: a plain interface parameter is mode 1 (read-only
  target), `mut` is mode 2. The same special case is in `fnDecl`
  (`obo.obo:8194-8225`) for ordinary functions and methods.
- Restrictions are enforced where types are formed, not only at use:
  - `parseType` (`obo.obo:3892-3933`) rejects `[]I` and `Result[I]`; `?I` is
    already rejected because `nonNull` excludes interfaces (`obo.obo:956-958`);
    `^I` is already rejected by the handle rule; `&I` is already rejected by
    `parseRefType` because an interface is not owned.
  - `fieldList` (`obo.obo:3935-3966`) rejects a field of interface type.
  - `varDecl` (`obo.obo:6356-6484`) rejects a global interface and a `var`
    interface local; local `let` takes the new interface branch.
  - `fnDecl` and interface members reject an interface return type.
  - `ok(...)` rejects an interface payload (defensive; interface items are
    borrowed, and `ok` already rejects borrowed payloads, `obo.obo:4957-4969`).
  - `relation` (`obo.obo:6188-6287`) rejects interface comparisons explicitly,
    so the pair is never compared as an integer.
- `closureCheck` (`obo.obo:8327-8356`) gains an `iface` branch: the interface
  is appended to `closureSeen` (so a self-referential parameter terminates),
  then every member's explicit parameter and return types are checked for
  private local types. `checkExports` (`obo.obo:8358-8375`) also checks every
  exported impl: the interface and the receiver type must both be exported, and
  each error names the private declaration with the existing
  `mark '<T>' pub too` remedy.
- `compileUnit`'s module-end check (`obo.obo:8539-8547`) needs no impl check
  (impls have no body); it keeps checking incomplete types, function headings
  and the methods phase's method headings.

### 5.3 Type checking an interface expression

- `nameItem` (`obo.obo:5509-5559`) already handles a local/parameter of any
  type: it returns a `mem` item with `sym`, `root`, `borrowed = s.isRef` and the
  narrowed type. An interface local is an `isRef` symbol with a `RefBind`, so
  this path needs no change.
- `designatorT`'s postfix loop (`obo.obo:5561-5724`) gains an interface branch
  before the record-field logic. After `.` and the member name, if the token is
  `(`, it is an interface method call; otherwise it is the method-value error.
  The methods phase already restructures the loop to break at `. name (` and to
  run the existing tail on the receiver before calling out; interfaces reuse
  that structure, with `isCall` also covering an interface receiver so a
  statement/defer use is recorded as a read. Without it, a loop-carried
  invalidation of an interface local can hide the previous iteration's use; the
  statement/defer loop regressions are the load-bearing case 9.6c.
- `checkAssign` (`obo.obo:4640-4657`) continues to use `assignable`
  (`obo.obo:968-980`). `assignable(I, I)` is true by pointer identity;
  concrete-to-interface and handle-to-interface are deliberately **false** so
  that only the two legal sites (a `let` initialiser and an argument to an
  interface-typed parameter) can perform a conversion. Every other destination
  (return, field, global, array element, `Result`) rejects the type at
  formation or declaration, so no silent conversion can occur.
- `ifaceValue(dst, x, what)` is the conversion helper. It returns
  `IfaceVal { item: Item, bind: ^RefBind }` (a small record, so `Item`'s layout
  is not touched):
  1. if `x.typ == dst`: copy; `bind = itemBind(x)`; no cell;
  2. else if `x.typ.kind == "iface"`: the interface-to-interface error (a
     different nominal interface never converts);
  3. else if `x.typ.kind == "handle"`: `T = baseOf(x.typ)`;
     `impl = findImplFor(T, dst)`; if nil, `implMissing(T, dst)`;
     `bind = singleBind(handleSym, true, "", true, nil)`;
     token = the handle value; new cell;
  4. else if `x.borrowed` and `x.typ` is a record/closed `T`:
     `impl = findImplFor(T, dst)`; if nil, `implMissing(T, dst)`;
     `bind = borrowBind(x)`; token =
     `rtCall1(RT_HANDLE, x, derived("handle", T))`; new cell;
  5. else the owned/unsupported error (an owned `T`, a borrowed array/string/
     `Result`, or an optional).
  Resolving the impl *before* `borrowBind`/`RT_HANDLE` also avoids a runtime
  token on an unsupported source type. `impl` is the visible `Impl` whose
  `vtable` is stored in the cell.
  The cell is allocated with two hidden frame slots; the token and
  `impl.vtable` are stored; the result item is a `reg` holding the cell
  address, `borrowed = true`, type `dst`, with `IfaceVal.bind` carrying the
  source binding. A copy reuses the source cell and carries the source's
  binding.
- `ifaceValue` rejects `curFn == nil` (`an interface value cannot be created
  outside a function`), because there is no frame for the cell. A global
  initialiser therefore cannot create one, even indirectly as a call argument.
- The one conversion path that does not call `ifaceValue` is an **inferred
  copy** `let e = d`: `varDecl`'s existing reference-local branch sees a
  borrowed value of interface type and creates an interface reference local
  with the same binding and cell pointer. `varDecl` must reject `var e = d`
  with the bound-once error before taking that branch. This path is a copy,
  never a conversion: `let e = &b` remains a reference local of `b`.
- The binding's `writable` bit is the only mutability check. A `mut` member
  call with `!binding.writable` is the read-only error; the receiver is never
  silently downgraded.

### 5.4 The interface call

`ifaceCall(recv, f, rline, rcol)` reuses the methods phase's argument machinery
and effect loops, with the differences that matter:

1. **Resolve** `findIfaceMethod(recv.typ, name)`; a miss is the no-method
   error. The receiver is a bare local/parameter of interface type. A method
   reference without `(` is the method-value error; the interface has no
   fields.
2. **Writability:** if `f`'s receiver mode is 2, require the receiver binding's
   `writable`; otherwise the read-only error. `noteWrite(recv)` records the
   write for loop checks.
3. **Receiver evaluation:** compute `rb = itemBind(recv)` (the receiver is a
   reference-like symbol) and register its holder with `holdArg(rb, 0)` before
   the explicit arguments run, using the same holder pool. Load the cell
   pointer; copy the vtable at `[cell+8]` into a hidden frame slot; load the
   token at `[cell]`; call `RT_DEREF`; push the object pointer as argument 0.
   Saving the vtable before `RT_DEREF` matters because the runtime call may
   clobber any caller-saved register.
4. **Arguments:** exactly `callFnWith`'s loop over `f.fnRec.params[1..]`, with
   `checkAssign` labels `argument N of interface method 'I::m'`; interface
   arguments use `ifaceValue` and their `IfaceVal.bind` instead of
   `borrowBind`; owned arguments use `consume`; every mode branch uses the
   existing `borrowBind` writability check, `holdArg`, `argOrderCheck`, and the
   receiver-aware alias/global/handle loops. Arity counts the explicit
   parameters (`len(params) - 1`).
5. **Handle-rooted check:** scan **every root** of the receiver binding, not
   just the first; a joined reference (`var r = &n; if c { r = &h.field }`) has
   several roots and any `handleSym` root requires `handleSafe(f)`. An impure
   member is the handle error. The same all-roots scan applies to the
   global-rooted check (`mightFree` for each global root). Explicit arguments
   whose binding is handle-rooted are passed to the member with the existing
   handle-borrow check, using `handleSafe(f)` (the member is the callee); if
   the member is impure, the argument is refused.
6. **Effects:** use `f.freesAll`, `f.writesHandles`, `f.fnRec.pfree` and
   `afterCall(f)` exactly as a direct call does. For an impure `mut` receiver,
   `mutFrees(f, 0)` invalidates **every root** of the receiver's binding and
   records the pfree events for each; the alias/global loops likewise scan every
   root. For an impure **read-only** receiver the receiver pfree path
   is skipped (bit 0 is clear), but the synthetic summary's
   `writesHandles`/`freesAll` make `afterCall` call `invalidateDeep(nil)` and
   `invalidate(handleSym, ...)`: every deep reference, including references
   into the receiver, is invalidated, and a receiver whose own binding is deep
   is invalidated too. A whole-object receiver binding survives on an accepted
   call; an impure global-rooted receiver was refused at step 5. The exact
   accept/reject cases are in section 9.6.
7. **Call:** after the args are pushed, load the vtable from the hidden slot
   and emit `FF /2` with a memory operand:
   `call qword ptr [vtable + 8*slot]`. No tag bits are used in this design.
   The vtable register is released after the call. Stack padding and
   `saveRegs`/`restoreRegs` mirror `callFn`.
8. **Result:** `voidItem` for void; otherwise a register of `f.ret`, with
   `strTemp` for a string, owned/consumed by the caller as usual. The call ends
   the designator; the caller's tail is not applied to the result.

The synthetic method's `addr = 0` is never used by this path; `kind == "ifn"`
is checked, so a direct call to it is impossible. There is no name mangling: a
concrete method is an ordinary function symbol with a code address; the vtable
slot supplies the address at run time. The diagnostic display is the interface
method `I::m` (module-qualified when foreign), so error messages do not depend
on the dynamic type.

### 5.5 Function-call plumbing and scans

- The methods phase refactors `callFn` (`obo.obo:4760-4903`) into
  `callFnWith(f, recv, hasRecv, rline, rcol)`. Interfaces add a sibling
  `ifaceCall`; function and direct-method messages must stay byte-identical.
  The argument-order, alias, global-reference, handle-borrow and pfree loops
  are shared helpers (or mechanically duplicated with one display formatter) so
  the receiver prints as `the receiver` and arguments as `argument N` in both
  styles.
- Interface method symbols are not in `methodSyms`, so the methods phase's
  scans (while-lift fixups, register-home retry cleanup, module-end heading
  check, `checkExports`) do not see them. Indirect calls have no rel32 target
  fixups, so neither the lifted `while` condition relocation
  (`obo.obo:6971-6985`, `7025-7118`) nor the `fnDecl` retry cleanup
  (`obo.obo:8279-8285`) needs an interface entry. Hidden cells and vtable slots
  are rbp-relative frame slots, which the same re-emission reuses correctly.
- `isMethod(s)` from the methods phase must be
  `s.kind == "fn" && s.recv != tVoid` **and** the symbol must be a member of
  `methodSyms`; an interface synthetic has `recv == I`, so a `recv != tVoid`
  test alone would misclassify it. All method scans (writer filter, heading
  check, `checkExports`, fixup cleanup) must use the registry/kind form.

### 5.6 No new tokens

The scanner, token constants (`obo.obo:36-103`), keyword table (`obo.obo:145-158`),
`--tokens` and `port_test.py` are untouched. `interface`, `impl`, `pure` and
`mut` are contextual words; `mut` is already an identifier handled
contextually. There is no new naming restriction: the member modifiers come
before `fn`, so a member may still be named `pure` or `mut` (`pure fn pure()` is
a pure member named `pure`), and the existing `mut` limitation for a parameter
or receiver name is unchanged. A type named `interface` remains usable,
including as a bare alias, through the `{` check of section 2.1.

---

## 6. Symbol files and modules

### 6.1 Representation

An interface is a normal exported named type with a source-like definition:

```text
type Drawable = interface { pure fn area(): int; pure fn name(): string; mut fn scale(p0: int) }
```

- An interface has **no shell line**. Its definition line pre-registers the
  `Type` (`kind = iface`, incomplete) before parsing members, so a
  self-referential parameter resolves, and the reader computes the uid as
  `fnv1a(owner + "::" + name)`, exactly as `nameType` does
  (`obo.obo:1727-1732`). Record and closed type shells remain as they are, for
  recursive fields. An interface cannot be referenced before its definition,
  in source or in a symbol file.
- Members are in declaration order, separated by `;`. Parameter names are
  canonical `p0`, `p1`, ... (explicit parameters only; an interface member has
  no receiver in its textual signature) and are syntax only. The marker order is
  `pure`, then `mut`, then `fn`.
- The receiver mode is `fn` = read-only, `mut fn` = mutable; the purity bit is
  `pure`. There is no owned-receiver spelling and no effect summary: purity is
  the only contract.
- Parameter types use the ordinary type expressions. An interface-typed
  parameter is written `pN: I` (read-only target) or `mut pN: I` (mutable
  target); `&I` is not a spelling. An interface return is rejected.
- An implementation is a top-level line in its own group:

```text
impl Drawable for Shape
impl lib::Drawable for Circle
```

  The interface type is emitted by `appTypeExpr` (qualified when foreign); the
  receiver is its defining name in this module. Only exported impls are
  written. No vtable address, slot address, method name or summary is emitted:
  the interface's member order and the concrete method's own `.sym` line are
  the only data needed, and `--check` discards code.

Canonical group order (one deterministic order, matching the reader):

1. record/closed shells (source order); interfaces have no shell,
2. type definitions (record/closed/interface, definition-line order, as today),
3. type aliases,
4. constants,
5. globals,
6. methods (methods phase, declaration order),
7. **implementations (declaration order)**,
8. functions (methods phase, declaration order).

### 6.2 Writer

- `appTypeDef` (`obo.obo:9077-9095`) gains an `iface` branch. `appTypeShell`
  (`obo.obo:9069-9075`) is unchanged but is called only for record/closed
  definitions: a small `symHasShell(s)` is `symDefines(s, modName)` plus
  `kind != iface`. `symDefines` (`obo.obo:9055-9061`) and `symCompareOrder`'s
  definition checks include `iface`; its shell list excludes iface.
- `appFn` (`obo.obo:9137-9185`) and the methods phase's `appMethod` gain a
  parameter writer: mode 0 is `pN: T`, mode 1 is `pN: &T` (or `pN: I` for an
  interface type), mode 2 is `mut pN: &T` (or `mut pN: I`). Existing function
  output is byte-identical.
- A new `appImpl` emits the impl line; a new pass in `symBody`
  (`obo.obo:9211-9279`) between methods and functions iterates `impls`
  filtered by `owner == mod.name && exported`.
- `symText` (`obo.obo:9281-9311`) hashes the body, so interface definitions and
  exported impls are interface data. A private impl is not emitted and changes
  only the source hash.
- The version constants (`obo.obo:109-111`) become `OBO_VERSION "0.7"`,
  `SYM_FORMAT 3`, `SYM_ABI 1`; `--version` prints
  `obo 0.7 sym-format 3 abi 1`.

### 6.3 Reader

- `symTypeDecl` (`obo.obo:9652-9822`) accepts the interface definition. It
  applies the same **`T_IDENT` branch** after `=`: save `w = sval`, `next()`;
  if `w == "interface"` and `sym == T_LBRACE`, it is an interface definition;
  otherwise a factored `symFinishTypeName(w)` reproduces the name tail of
  `symParseType` **with its order**: **`T_COLONCOLON` first**
  (`symQualifiedType`), then `int`/`byte`/`bool`/`string`, then `Result +
  [T]`, then `findExport`. Checking `::` last would break a valid symbol-file
  alias to a module named `int` or `Result` (`type X = int::T`,
  `type Y = Result::T`; FORK.md:3598-3603). With this order `type X =
  Result[T]` and a type named `interface` both round-trip. The `?`, `[]`, `^`,
  `ref` and `closed` branches are untouched. There is no interface shell line. Mode 1 creates and registers the `Type`
  (`kind = iface`, `incomplete = true`, uid from the key) and its exported type
  symbol at the definition before parsing members, so a self-referential
  parameter resolves through `findExport`; mode 0 finds the live `iface` type
  and requires it complete, exported, kind `iface`, with
  `uid == fnv1a(owner + "::" + name)` (there is no file uid to compare). A preceding `type I uid ...` shell for the same name is the
  forward-shell error. The definition claims the exported name with
  `symClaim` in both modes (there is no shell to claim it), and mode 0 checks
  that the live type is exported. The definition parser reads members in order,
  creates the synthetic symbols in mode 1, compares them with the live
  `ifaceMethodsOf(t)` in mode 0 (name, receiver mode, purity, parameter
  types/modes, return type, count and order), and clears `incomplete`.
  Duplicate member names are a file error. At the definition it sets
  `symAfterShells = true` and appends the name to `symDefined` in both modes;
  `symShells` therefore contains only record/closed names, while `symDefined`
  contains interfaces too. An interface
  return is rejected; `[]I`, `Result[I]` and `?I` are rejected by the reader's
  own `symParseType` (not by assuming `parseType` is shared); an interface
  field/global is rejected by `symFieldList`/`symGlobalDecl`. Because an interface definition registers `kind = iface`
  immediately, a later record definition with an interface field is rejected,
  and an earlier record definition that names the interface fails as an
  unknown type (there is no shell to make it forward-referenceable).
- `symFnDecl` (`obo.obo:9916-10102`) gains the interface-parameter rule: after
  parsing the type, an interface type is mode 1 unless `mut` was read, in which
  case mode 2; the existing `mut`-needs-a-reference error applies to every
  other type. An interface return is rejected. Mode 0 compares both type and
  mode as today; `pfree`/`pf` parameter indices are unchanged.
- A new `symImplDecl(mode)` reads `impl`, an interface type, `for`, and a local
  receiver name. It validates the interface kind, the receiver
  (record/closed, not a variant, defined by this module, complete, exported,
  and spelled by its **defining name**: an alias spelling is rejected, as the
  writer never emits one), and the unique pair. Mode 1 creates an `Impl` with `vtable = 0` and empty
  slots; mode 0 requires the live exported impl, compares the pair and does not
  construct or overwrite live data. The line is appended to `symImplOrder` in
  both modes, after validation, for the mode-0 order comparison.
- `symReadDecls` (`obo.obo:10103-10127`) dispatches a leading `T_IDENT`
  `"impl"` to `symImplDecl`; `interface`, `pure` and `mut` stay ordinary
  identifiers.
- New reader state: `symImplOrder: []string`, `symImplSeen: []string`, reset in
  `readSym` (`obo.obo:10322-10337`) with the other order lists. `symFinish`
  (`obo.obo:9632-9650`) mode 0 requires every live exported impl key to be
  seen; extras fail while parsing. `symCompareOrder` (`obo.obo:9571-9630`)
  compares the live exported impls in registry order with `symImplOrder`.
- `--reemit-sym` (`obo.obo:10346-10359`) reconstructs the interface methods and
  impls and re-emits them; the writer's canonical output must round-trip
  byte-identically.

### 6.4 Type identity and cross-module use

- The interface `^Type` is the key. In a full build the defining module's object
  is shared with every importer; in `--check` the interface definition creates
  one object reused by every member, impl and use. An impl's pair is compared by
  pointer.
- An imported implementation is visible only if its `Impl.exported` is true,
  regardless of whether the build has the private live record (`--check` does
  not): `findImplFor` filters on `owner == curModuleName || exported`. A failed
  lookup for a foreign receiver reports the module-member form
  `module 'M' has no exported implementation of 'I' for 'T' (only 'pub'
  implementations are visible)`, where `T` is the parent closed type for a
  variant; a local receiver with no visible impl reports
  `type 'T' does not implement interface 'I'`. Both messages are produced by
  `implMissing` and are identical in full and check modes. A *foreign type with
  no implementation at all* therefore gets the module-member message, not
  "does not implement"; the tests cover full and source-less check.
- A module may implement an interface from a module it imports for a type it
  owns. It may not implement for a foreign type. Two modules cannot both
  implement the same pair.
- Transitive access works: B may import A, obtain a value of a type owned by L
  through A's exported function, convert it to L's exported interface and call
  it, without importing L directly. The canonical types and impls are shared
  in a full build; in `--check` B's dependency graph loads L's `.sym` through
  A's `dep` lines. This mirrors the methods phase's transitive method test.
- The interface hash includes the module name, depiface lines and the
  interface/impl declarations. A change to an interface's members, order,
  modes, purity or a parameter/return type changes its hash and every
  dependant's `depiface`; adding/removing a `pub impl` changes the receiver
  module's interface hash; a private impl or a private method body changes only
  the source hash. Parameter names are canonicalised; renaming a source
  parameter changes the source hash only.
- A full build re-reads the file it just wrote (methods design §5.3); mode-0
  impl/interface comparison is defence in depth. `--check` trusts a
  well-formed symbol file, as it already must.
- `--deps` ignores declarations and is unchanged.

### 6.5 Versioning and old files

- `SYM_FORMAT 3` is required because the grammar and the meaning of a type line
  change (an interface definition and impl lines did not exist), even though
  the ABI is unchanged.
- `SYM_ABI` stays `1`: record layout, descriptor format, calling convention,
  global addressing, effect-summary meaning and initialization order are
  unchanged. The **one-word argument convention is unchanged**: an interface
  parameter is one word (a cell pointer), and interface calls use the existing
  one-pointer argument convention. The **cell layout and vtable layout are
  new**, but they cross module boundaries only inside one image and are
  **gated by `SYM_FORMAT 3`/compiler `0.7` exact-match**, not by the ABI
  number: a 0.6 compiler rejects a format-3 file, and every format-3 reader is
  a 0.7 compiler with the same cell layout. The vtable is new rodata, not a
  descriptor, and is not shared through object files.
- A 0.6/format-2 file is rejected with
  `has format 2; this compiler reads 3; rebuild it`; a 0.7 compiler's file is
  rejected by an older compiler. Rebuild, do not reuse stale artifacts.
- All 76 `tests/modules/sym/*/symdir` files and the 3 golden
  `expected/*.sym` files migrate in the same change to
  `obosym 3`/`compiler "0.7"`/`abi 1`. The three version fixtures are migrated
  too, to the new base plus their rejection value (they are not left at an old
  format): `format` becomes `obosym 4` (expected reads 3), `compiler` becomes
  format 3 with `"9.9"`, `version` becomes format 3/0.7 with ABI 9. Headers are outside
  the interface hash, so the migration itself changes no hash; the golden
  interface and impl additions do, and the goldens are regenerated explicitly
  with `sym_test.py --update`.

---

## 7. Ownership and effects

| feature | interaction |
|---|---|
| borrows | an interface borrow is a reference local: `makeRefLocal`, `borrowBind`, `itemBind`, `invalidate`, `heldPush`, loop/defer states all apply. A move/assignment/drop of the owner invalidates the interface value. |
| handles | a handle conversion copies the token and binds through `handleSym`; dispatch dereferences at every call, so a hidden free exits 7. Like any reference through a handle, the value is invalidated by any visible free/change and by any call that might free, so construct it near its use and re-convert after an unrelated freeing call. Impure members are refused through handle-rooted values; pure ones are allowed. |
| mut receivers | require `binding.writable`; a mut call invalidates references below the target and records pfree events, as a direct mut call does. A pure mut member frees nothing below the target, so references stay valid, matching a direct scalar-writing/append-only method. An **impure read-only** member cannot free below its receiver (no pfree bit 0), but its `writesHandles`/`freesAll` summary makes `afterCall` invalidate every deep reference, including references into the receiver; a whole-object receiver binding survives on an accepted call (a global-rooted impure call is refused) and a deep receiver is itself invalidated. |
| owned values | owned concrete values are rejected as interface sources. A moved owner invalidates the interface value; an interface value is never moved or freed. |
| aliases | copies share the cell and the binding; a mut call through one invalidates references into the target through all bindings; the receiver itself remains valid. |
| `defer` | interface locals and parameters are reference-like; defer-use, replay and invalidation rules apply. A `let` interface local used by a defer is checked at block exits. Cells are frame-resident and live while the defer runs. |
| `try`/`Result` | an interface method may return `Result[T]` (`T` not an interface); `try d.load()` works because `try` parses a fresh call result (`tryExpr`, `obo.obo:5914-5999`). `Result[I]` and an interface return are rejected at type formation. |
| field origins / move bits | interface values are not owned and produce no free events; a mut interface call records pfree events through `callFrees`, so handles made from the target's fields are killed exactly as for a direct mut call. A whole-receiver borrow stays valid when only below it is freed. |
| assignment targets | an impure interface call counts in `userCalls` (`movesFn`), so target-located-before-a-call checks fire as for functions. A pure interface call does not. |
| globals | no interface global. A borrow of a global may be converted; an impure interface call on it is refused if the call may free the global (`mightFree` sees `freesAll`). A pure call is allowed. |
| modules | interface types and exported impls travel in the `.sym`; visibility is mode-independent; no re-exports. |
| `main` | a member or concrete method named `main` is not the entry point; interface members get no `main` check, and the methods phase already gates the source/reader checks on `!isMethod`. |
| the compiler itself | no interface syntax in `obo.obo` in round 1, so the 0.6 seed can build the 0.7 compiler. |

---

## 8. Diagnostics

Style follows `LANGUAGE.md` §10: lower-case, single quotes around names, no
trailing period, types through `tname`, interface methods as `I::m` (and
`M::I::m` when the module is not current).

### 8.1 Interface declaration

| situation | message |
|---|---|
| empty interface | `an interface needs at least one method` |
| duplicate member | `interface 'I' already has method 'm'` |
| member name missing / bad modifier order | `expected a method name in the interface` / `'pure' and 'mut' come before 'fn'` |
| `pub` on a member | `'pub' is not allowed on an interface member; mark the interface type pub` |
| interface return | `an interface method cannot return an interface value; interface values are parameters and locals only` |
| forward interface shell | `an interface cannot be forward-declared: 'I' is already declared` |
| duplicate definition | the existing `'I' is already declared in this scope` |
| interface type borrowed/handled/optional | the existing `only records, closed types and arrays can be borrowed, not I`, `handles name records and closed types, not I`, `only reference types can be optional, not I` |
| interface field | `a record field cannot be an interface value; interface values are parameters and locals only` |
| interface array | `an array cannot hold interface values` |
| interface `Result` payload | `Result cannot hold an interface value` |
| interface global | `an interface value cannot be a global; it is a parameter or a local` |
| interface function/method return | `a function cannot return an interface value; interface values are parameters and locals only` |
| `var` interface local (explicit `var e: I` or inferred `var e = d`) | `an interface value must be declared with let: it is bound once and cannot be reassigned` |
| interface local without initialiser | `an interface value needs an initial value; it cannot be nil` |
| conversion outside a function | `an interface value cannot be created outside a function` |

### 8.2 Implementation

| situation | message |
|---|---|
| interface target not an interface | `'X' is not an interface type` |
| receiver not record/closed | `an interface can only be implemented by a record or closed type, not <t>` |
| foreign receiver | `cannot implement an interface for imported type '<M>::<T>'; declare the implementation in the type's own module` |
| variant receiver | `cannot implement an interface for variant '<V>'; declare it on its closed type '<C>'` |
| incomplete interface or receiver | `type '<T>' is declared but not yet defined` |
| duplicate pair | `type '<T>' already implements interface '<I>'` |
| missing method | `type '<T>' has no method '<m>' to implement interface '<I>'` |
| method not defined yet | `method '<T>::<m>' must be defined before the implementation of interface '<I>'` |
| signature mismatch | `method '<T>::<m>' does not match interface '<I>' method '<m>': <detail>` (`the receiver must be 'mut &T'`, `argument N must be <mode>`, `argument N must be <t>, not <t>`, `it must return <t>, not <t>`) |
| impure implementation of a pure member | `method '<T>::<m>' is not pure, but interface '<I>' declares '<m>' pure` |
| `pub` impl with a private type | `exported implementation of '<I>' for '<T>' uses private type '<X>'; mark '<X>' pub too` |
| duplicate/foreign impl in a `.sym` | `duplicate implementation of 'I' for 'T'` / `the receiver of an implementation must be a type defined in this module` / `an implementation's receiver cannot be a variant; use its closed type` |

### 8.3 Conversion and calls

| situation | message |
|---|---|
| owned source | `cannot use owned '<T>' as interface '<I>': borrow it with '&' or make a handle with handle(...)` |
| unsupported source | `cannot use <t> as interface '<I>'` |
| local receiver, no visible impl | `type '<T>' does not implement interface '<I>'` (for a local variant, `<T>` is the variant; the closed parent is named in the detail if useful) |
| foreign receiver, no exported impl (private or absent) | `module '<M>' has no exported implementation of '<I>' for '<T>' (only 'pub' implementations are visible)`; for a variant `<T>` is the parent closed type `<C>` |
| interface-to-interface | `cannot use interface '<I1>' as '<I2>': interface values do not convert; convert from the original value` |
| unknown member | `interface '<I>' has no method '<m>'` |
| member used as a value | `interface method '<I>::<m>' cannot be used as a value: call it with '(' after its name` |
| field-style access | `interface '<I>' has no fields; it has methods only` |
| mut member on a read-only value | `the receiver of interface method '<I>::<m>' needs a mut interface value, but '<d>' is read-only` |
| arity | `interface method '<I>::<m>' takes <N> argument(s), not <M>` / `... not more` |
| argument type | `argument <n> of interface method '<I>::<m>': cannot use <t> as <t>` |
| handle-rooted impure call | `cannot call interface method '<I>::<m>' through a handle: it is not pure, and might free what the handle names` |
| global-rooted impure call | `cannot call interface method '<I>::<m>' on a reference into global '<g>': it might free it` |
| later argument frees the receiver | `argument <n> of interface method '<I>::<m>' may free what the receiver points into, and runs after it; compute it before the call` |
| mut alias with the receiver | `argument <n> of interface method '<I>::<m>' may point inside the receiver, which is mut` (or `the receiver of interface method '<I>::<m>' may point inside argument <n>, which is mut`) |
| handle-rooted interface argument to an impure callee | `cannot pass an interface value that borrows through a handle to <f>: it might free what the handle names` |
| read-only interface argument to `mut d: I` | `argument <n> of '<f>' needs a mut interface value, but this one is read-only` |
| equality | `interface values cannot be compared` |
| `drop`/`take`/`handle`/`alive`/`&d` | the existing owned/handle/borrow errors, unchanged |

### 8.4 Symbol-file diagnostics

| situation | detail |
|---|---|
| malformed interface/member | `expected a method name in the interface`; `duplicate interface method 'm'`; `an interface needs at least one method`; `an interface method cannot return an interface value; interface values are parameters and locals only` |
| interface in a field/global/array/`Result`, including a function signature | `a record field cannot be an interface value`; `an interface value cannot be a global`; `an array cannot hold interface values`; `Result cannot hold an interface value` |
| `p0: &I` in a function signature | the existing borrow rule, `only records, closed types and arrays can be borrowed, not I` |
| unknown/duplicate impl | `duplicate implementation of 'I' for 'T'`; `'X' is not an interface type`; `the receiver of an implementation must be a type defined in this module`; `an implementation's receiver cannot be a variant; use its closed type`; an alias receiver spelling: `the receiver of an implementation must be the defining name of a type in this module` |
| interface not yet defined | `type 'I' is declared but not yet defined` |
| live/file disagreement | the existing `symbol file '<p>' disagrees with the compiled interface of '<m>' for <decl>; rebuild it`, with `interface 'I'`, `interface method 'I::m'`, `implementation 'I for T'` or `the implementation order` as `<decl>` |
| format/ABI/compiler | `has format <n>; this compiler reads 3; rebuild it`, `has ABI 9; this compiler uses 1; rebuild it`, `was built by compiler '9.9'; this is '0.7'; rebuild it` |

---

## 9. Tests and mutants

Every case is written with the change, never after it; no existing assertion is
deleted or relaxed. The methods phase's harness changes (symcheck `expect`
files, REF/REEMIT assertions, fixture migration) are the precedent.

### 9.1 Run programs (`tests/run/`)

| file | covers |
|---|---|
| `ifaces.obx` | one interface, two record types, borrow conversions, explicit and inferred (`let e = d`) copies, dynamic dispatch, same-named methods on both types returning different values; a concrete static call beside an interface call; `let e = &b` stays a concrete reference and `let e = b` stays owned `T` |
| `ifaces_mut.obx` | `mut` members, writable/read-only targets, `mut d: I` parameters, scalar/append writes keeping references valid, a mut member that frees below the target invalidating a reference (error case in `errors/ifaces.obx`), a `let` interface local calling a mut member |
| `ifaces_handle.obx` | handle conversions, pure read-only and pure mut members through handles, a handle field and `handle(x)`, a handle-rooted value passed to a pure function; `ifaces_handle_dead.obx` stores a handle in a record/field while the owner is alive, frees the owner behind the compiler's `let`-handle tracking, then converts the now-stale field after the free and calls: the first `RT_DEREF` exits 7, marker `// expect-exit: 7` |
| `ifaces_closed.obx` | an impl on a closed type, calls through a closed-typed local, a variant source and a match-narrowed source using the parent impl |
| `ifaces_params.obx` | `d: I` and `mut d: I` parameters, passing/copying interface values onward, a Result-returning interface member with `try`, `defer d.close()` |
| `ifaces_alias.obx` | interface and type aliases, two structurally identical interfaces in one module dispatching separately, a type implementing two interfaces with one shared method |
| `ifaces_scope.obx` | interface locals in blocks and loops, conversions in call arguments, recursion, a defer using an interface local, no assignment/return/field |
| `ifaces_effects.obx` | pure versus impure members; a reference into the receiver stays valid after a pure mut call and is refused after an impure one; an **impure read-only** call leaves a whole-object receiver valid but invalidates a reference into the receiver (and invalidates a deep receiver itself); a pure read-only call keeps both; a global-rooted borrow refused for an impure member; a joined multi-root receiver is checked on every root; a pure mut member stores an unrelated owned argument with an ordinary borrow-rooted receiver and with a fresh `new` temporary through a handle-rooted receiver (while an unrelated owned local beside a handle-rooted receiver is refused) |
| `ifaces_homes.obx` | register pressure with interface cells and calls, run under the homes checks and `obo-noregs` |

### 9.2 Error programs (`tests/errors/ifaces.obx`)

One `// ===` section per error with the exact section 8 text: empty interface;
duplicate member; forward interface shell; `pub` member; interface return;
interface field/array/Result/global; `var` interface (explicit `var e: I` and
inferred `var e = d`); missing initialiser; owned source; non-implementing
type; private foreign impl; interface-to-interface; unknown member; member as a
value; field access; read-only mut receiver; arity; argument types; impure
handle-rooted call; impure global-rooted call; later argument freeing the
receiver; mut alias (both directions); handle-rooted interface argument to an
impure callee; read-only interface argument to `mut d: I`; equality;
`drop`/`take`/`handle`/`alive`/`&d`; `match`; `new I`; indexing; interface
construction in a global initialiser; impl on a variant/foreign/built-in/
incomplete type; duplicate impl; missing method; heading-only method;
signature mismatches (receiver mode, parameter type, parameter mode, return);
impure implementation of a pure member; `pub` impl with private types; missing
`impl` before a conversion; `impl` after the conversion (declare-before-use);
a handle-rooted interface value used after a visible free, an assignment over
its owner, or an impure call (the reference invalidation error). Additional
load-bearing sections: two-level handle propagation (`fn f(d: I) { d.impure() }`
called with a handle-rooted value, versus a pure `g(d: I) { d.pureMember() }`
which accepts it); a joined multi-root receiver
(`var r = &n; if c { r = &h.field }; let d: I = r; d.impure()`), which must be
refused because one joined root is `handleSym`, and an all-local joined
receiver whose mut call invalidates references into every root; a read-only
impure call with a reference into the receiver, and one with a deep receiver;
the `isCall` statement/defer cases (an interface local used as a loop statement
or in a defer, an owner freed afterwards, the next use refused); an owned
argument that is the receiver's owner/ancestor beside a handle-rooted or
borrow-rooted receiver (hold/consume rejection); an unrelated owned **local**
beside a handle-rooted receiver (the conservative `handleSym` invalidation
rejects it, while a borrow-rooted receiver or a fresh `new` temporary accepts
the unrelated owned value); and a handle-rooted value used after a visible free
(compile-time invalidation, not exit 7).

### 9.3 Modules and symbol files

| case | what |
|---|---|
| `modules/run/ifaces` | lib exports a pub interface, pub record types, pub methods (`&`, `mut`, pure and impure) and pub impls; main converts borrows and calls all members; a mut impure call invalidates a reference across the module boundary |
| `modules/run/ifaces_handle` | lib exports a handle-producing function and a pure interface; main converts `handle(c)` and calls pure members; a separate error tree passes a handle-rooted value to an impure function |
| `modules/run/ifaces_closed` | an exported closed type implements an interface; main calls through a variant value |
| `modules/run/ifaces_transitive` | L exports `I`, `T` and a pub impl; A imports L and exports `fn make(): lib::T` and `fn use(d: lib::I)`. B imports only A: `let t = a::make(); a::use(&t)`. B never names or imports L; the conversion target comes from A's exported parameter type `lib::I`, and the impl comes from L's transitively loaded interface. An exported alias (`pub type LI = lib::I`) is the alternative naming route. `modules/sym/ifaces_transitive_check` deletes A's and L's sources, keeps their syms, checks B source-less, and asserts **exit 0** (not merely REF==PORT) |
| `modules/run/ifaces_downstream` | lib exports the interface and a function taking it; main defines a type, a pub impl and the methods, converts a borrow, and calls `lib::use(...)`; the dependency's call dispatches through a vtable emitted in the root's module |
| `modules/run/ifaces_identity` | two interfaces with the same method names/signatures and two impls; calls dispatch through the right vtable; an alias does not create a second type |
| `modules/errors/private_impl` | converting to a dependency's interface whose impl is private: exact module-member error (full build) |
| `modules/sym/private_impl_check` | the same against the dependency's committed `.sym` that omits the private impl: same message, exit 1 |
| `modules/errors/foreign_no_impl` | a foreign record **and** a foreign variant whose closed parent has no implementation at all, converted to a dependency interface: the module-member error; the variant form names the parent closed type `C`, not the variant (full build) |
| `modules/sym/foreign_no_impl_check` | the same source checked source-less against the dependency's syms: the same module-member messages, not `does not implement`, exit 1 |
| `modules/run/ifaces_private_methods` | a `pub impl` whose concrete methods are private: an importer converts `&t` and calls through the interface successfully; the methods are not in the type's `.sym` |
| `modules/errors/pub_impl_private_type` | `pub impl` with a private interface or receiver type: closure error |
| `modules/errors/foreign_impl` | `impl` for an imported type |
| `modules/errors/impl_order` | an `impl` before a method body, or a conversion before its impl: declare-before-use errors |
| `modules/sym/ifaces` | a positive `symcheck` tree: a committed symbol file with an interface and impl, checked source-less, exit 0; includes a pure member and a handle-rooted call |
| golden tree | `lib.obx` gains a pub interface, an impl and a pub method; the 3 `expected/*.sym` are regenerated explicitly |

`sym_test.py` additions:

- `RICH_LIB` gains a pub interface (one pure read-only, one pure mut, one
  impure mut member), a pub record/closed impl, a private impl to prove
  omission, and a function taking the interface; `RICH_MAIN` converts a borrow
  and a handle and calls the members. `roundtrip_rich`, `determinism`,
  `hash_bytes`, `check_mode_and_cli` and `--reemit-sym` cover the writer,
  reader and cross-mode equivalence.
- `hash_private_and_rename`: interface member rename/reorder/purity/mode change
  changes the interface hash; adding/removing a member changes it; a parameter
  rename changes the source hash only; adding/removing a private impl or
  changing a private method body changes the source hash only; adding/removing
  a pub impl changes the receiver module's interface hash; reordering pub impls
  changes it.
- `effects_check`: a pure member called through a handle compiles; an impure
  member through a handle is refused; a mut interface call that frees below the
  receiver rejects a caller's reference into it across a module; a pure mut
  call does not; an **impure read-only** call rejects a caller's deep reference
  into the receiver but accepts a whole-object receiver; a pure read-only call
  accepts both; an impure member marks `freesall`/`writeshandles` in the
  dependent's view; a joined multi-root receiver is invalidated/error-checked
  on every root.
- `malformed_structures`: duplicate interface member; empty
  `type I = interface { }`; interface in a field (including one that names an
  interface defined later, which must fail as an unknown type because there is
  no interface shell); interface return; `[]I` and `Result[I]` in a **function
  signature** as well as in a field/global/type definition; `p0: &I` in a
  function signature (the reader's borrow rule must reject it, not misparse it);
  duplicate impl; impl for a foreign/variant/built-in receiver; impl naming a
  non-interface; impl receiver written as an **alias spelling** (the reader
  requires the defining name); impl before the interface/type definition; a
  `type I uid ...` shell followed by an interface definition (forward-shell
  error); a duplicate interface definition; a missing/extra interface member in
  mode 0 (if observable); a bad purity/mut marker.
- `sourceless_and_build_requirement`/`check_mode_and_cli`: `--check` against a
  source-less dependency with an interface and a pub impl, asserting **exit 0**;
  the checked module's own `.sym` contains its interface/impl lines; a stale
  0.6/format-2 file is rejected with the format message; `--version` is
  `obo 0.7 sym-format 3 abi 1`.
- Version table: format `4` (expected `has format 4; this compiler reads 3`),
  ABI `9`, compiler `"9.9"` (expected `this is '0.7'`), each mutated from the
  new base `obosym 3`/`abi 1`/`compiler "0.7"`.
- `golden`/golden tree: add the interface and impl; regenerate with
  `sym_test.py --update` explicitly; the gate only compares.

Fixture migration: all 76 `modules/sym/*/symdir` files and the 3 goldens are
migrated to the new base `obosym 3`/`compiler "0.7"`/`abi 1`. The three version
fixtures are migrated too, to the new base plus their mutated rejection value:
`format` 4, `compiler` format 3 with `"9.9"`, and `version` format 3/0.7 with
ABI 9. The symcheck `expect` files and the
REF-outcome/REEMIT assertions added by the methods phase are extended to the new
interface cases; positive cases must exit 0, not merely agree with a broken REF.
`sym_fuzz.py`'s `change_version` mutates the format to `4` (not 3) and its seed
may gain an interface/impl line; `own_fuzz.py` is unchanged.

### 9.4 Existing tests and fixtures

- `tests/sym_test.py`: `OBO_VERSION = '0.7'`, `SYM_FORMAT = 3`, the version
  table and the current literal snippets (the baseline writes `obosym 1` and
  `compiler "0.5"`; after methods they are 2/0.6) updated to 3/0.7;
  `handwritten_interface`/`b6_stub_completion` write format 3/0.7.
- Extend `module_names_int_result` with **type-declaration aliases** to modules
  named `int`/`Result`: a `mid` module imports `int.obx as ii` and
  `Result.obx as rr` and exports `pub type IntT = ii::T` and
  `pub type ResT = rr::T` (source aliases must use the explicit import aliases;
  `int`/`Result` cannot be import aliases). Build and run a main that uses
  `mid::IntT`/`mid::ResT`; assert `mid.sym` contains
  `type IntT = int::T` and `type ResT = Result::T`; `--reemit-sym mid.sym`
  byte-identically (the reader's `symFinishTypeName` must check `::` first);
  `--check` main against the tree. This is the type-declaration-alias case the
  existing test lacked (it only has `ii::make()`/`rr::r()` and `Result[int]`).
- Every committed `.sym` fixture as above; header-only migration needs no
  rehash, the goldens are regenerated after their interface/impl additions.
- `tests/sym_fuzz.py`: `change_version` (line ~202) mutates to 4; the fuzz
  accepts a valid interface/impl seed if added, without weakening.
- `tests/port_compare.py`: `SEED_FILE_MIN` entries `(0, 7)` for each new
  `tests/run/ifaces*.obx` and `tests/errors/ifaces.obx`; the keys are the
  absolute `os.path.join(FORK, 'tests', ...)` paths used by the existing map
  (`port_compare.py:174-181`), not bare names. The module trees are outside the
  seed comparison; the `expect`/`REEMIT_CASES` assertions are extended to the
  new symcheck cases. This is not a weakening: the skip stops when the seed
  reports 0.7.
- `tests/mutate_fork.py` (and `mutate_port.py` where shared): add
  `IFACE_MUTANTS`; retarget any pattern moved by the methods/designator/call
  changes; a stale pattern fails the gate by design, so retarget, do not
  delete. Record mode-0-only mutants as equivalent with the reason (a full
  build reads the file it just wrote), as the methods design does.
- `tests/doc_check.py`: the new `// example:` blocks in `LANGUAGE.md` are
  compiled and run at implementation time.
- No existing run/error program changes; the feature is additive.

### 9.5 Mutants (35 targets)

| mutant | expected catcher |
|---|---|
| conversion accepts any concrete type (skip `findImplFor`) | `errors/ifaces`: non-implementing type |
| impl lookup ignores interface identity (matches any impl on T) | `modules/run/ifaces_identity` / two-interface dispatch |
| variant source does not step to the closed parent (or uses a variant impl) | `run/ifaces_closed` conversion/call |
| owned source accepted as a borrow | `errors/ifaces` owned case; leak/double-free oracle |
| interface-in-field check dropped | `errors/ifaces` field case |
| interface-in-array/`Result`/return/global checks dropped | the corresponding error cases |
| `var` interface local / reassignment accepted | `errors/ifaces`; the cell-reuse value test |
| inferred `var e = d` skips the bound-once check in the generic reference-local branch | `errors/ifaces` inferred-`var` case |
| inferred `let e = d` loses the binding/vtable or is rejected | `run/ifaces` inferred-copy case |
| missing initialiser accepted (zero cell) | `errors/ifaces`; run crash/exit 7 |
| read-only mut receiver check dropped | `errors/ifaces` read-only case; `run/ifaces_mut` |
| receiver token not dereferenced before the call | `run/ifaces`/`ifaces_handle` wrong output or exit 7/crash |
| vtable slots reversed or sorted by name | a two-member run case with distinguishable results |
| vtable built from the wrong type's methods | `run/ifaces` T1/T2 dispatch |
| wrong interface's vtable used | `modules/run/ifaces_identity` |
| handle-rooted impure call allowed (drop the `handleSafe` check) | `errors/ifaces` handle case; a run crash/exit |
| purity not checked at the impl (impure method in a pure slot) | `errors/ifaces` purity case; then unsafe handle call |
| impure synthetic summary treated pure (drop `freesall`/`writeshandles`) | a run/error case where a mut interface call frees below the receiver and a reference is used; without invalidation the program compiles and reads freed memory |
| read-only impure member's summary drops `writeshandles` (deep references stay valid) | `ifaces_effects`: a reference into the receiver must be refused after an impure read-only call |
| pure synthetic summary treated impure | a pure handle-rooted call is unexpectedly refused; run test fails |
| receiver not held across later arguments | `errors/ifaces`: `d.store(clearTarget(...))` |
| receiver not recorded as a read in a statement/defer (`isCall` dropped) | 9.6c loop/defer use-after-free error case |
| handle/global check scans only the first root of a joined binding | 9.6b joined multi-root receiver (one root `handleSym`) |
| pure contract lets an owned argument alias the receiver (receiver hold/owned-arg invalidation skipped) | 9.6e owned owner/ancestor argument beside a handle-rooted or borrow-rooted receiver |
| interface copy loses the binding or the cell/vtable | a run case with two copies and two destinations |
| interface-to-interface conversion allowed | `errors/ifaces` conversion case |
| visibility filter dropped from `findImplFor` (`owner == curModuleName || exported`) | `modules/errors/private_impl` (a full build must not use the live private impl) and `modules/sym/private_impl_check` (check mode must agree) |
| a failed `findImplFor` reports no-implementation for a foreign receiver instead of the module-member error | `modules/errors/foreign_no_impl` and `modules/sym/foreign_no_impl_check`; the variant form must name the parent closed type |
| writer omits/emits private impls | golden/roundtrip and the private-impl module case |
| reader ignores impl lines or interface order/modes/purity | `sym_test` roundtrip/golden and the malformed cases |
| interface parameter mode 2 treated as 1 (writability not checked) | `errors/ifaces` read-only argument to `mut d: I`; `run/ifaces_mut` |
| impl accepted before the methods are defined (zero address in the vtable) | `errors/ifaces` ordering case; a run crash |
| interface method arity/labels regress | exact-error tests |
| `finishTypeName`/`symFinishTypeName` checks primitives/`Result` before `T_COLONCOLON` | `sym_test` extended `module_names_int_result`: `type IntT = int::T`/`type ResT = Result::T` fail to reemit/check or leave `::T` unconsumed |
| vtable address stored as a `.sym` address or not fixed after `dropFrame` | roundtrip/golden and the defined-before-impl tests |

Mutants that only remove the mode-0 live comparison (interface/impl order and
signatures) are equivalent for the same reason as the methods design: a full
build writes and immediately reads its own file. Record them as equivalent, not
caught. If the homes/while-lift interaction with interface calls is
unobservable, record that reason too.

### 9.6 Load-bearing safety cases

These are the cases that carry the soundness argument; each has a named test.
If one cannot be made observable, the gate records it as equivalent with the
reason rather than claiming it was caught.

| case | test | expected |
|---|---|---|
| (a) two-level handle propagation | `errors/ifaces`: `fn f(d: I) { d.impureMember() }` (an impure **read-only** member, so the read-only parameter can call it); `let d: I = h; f(d)` | `f` is impure (`freesAll`/`writesHandles` propagate), so passing the handle-rooted value is refused by the handle-borrow/pure check; a pure `fn g(d: I) { d.pureMember() }` is accepted with the same value |
| (b) joined multi-root receiver | `errors/ifaces`: `var r = &n; if c { r = &h.field }; let d: I = r; d.impure()`, arranged so the local root is first and the `handleSym` root is not first | refused because every root of the joined binding is scanned; a first-root-only scan would miss the handle root and compile. An all-local joined receiver's mut call invalidates references into every root (run/error pair in `ifaces_effects`) |
| (c) `isCall` loop/defer use-after-free | `errors/ifaces`: an interface local used as a statement in a loop, owner freed after the call, next iteration refused; a `defer d.m()` with a free before the block end | the next use is refused with the existing moved/freed-reference error (`freedRefMsg`), and the defer with the existing defer-use error. The interface local is a reference local; the statement/defer form must record the receiver as a value use (`isCall`). If the immutable binding makes the loop case observationally equivalent to the existing moved check, record that reason and keep the defer case (which uses `noteDeferUse`) |
| (d) exit 7 after a stale token | `run/ifaces_handle_dead.obx`, marker `// expect-exit: 7`: a handle is stored in a record/field **while its owner is alive**; the owner is then freed behind the compiler's `let`-handle tracking; the now-stale field is converted after the free and called | the first `RT_DEREF` exits 7. A handle-rooted value made *before* the visible free is compile-time invalidated, so the stale token must come from the field, not from a tracked `let` handle |
| (e) pure owned parameter | `run/ifaces_effects`: a pure `mut` member stores an unrelated owned argument with an **ordinary borrow-rooted** receiver (no `handleSym` root), and with a **fresh `new` temporary** through a **handle-rooted** receiver; `errors/ifaces`: an unrelated owned **local** beside a handle-rooted receiver, and an owned argument that is the receiver's owner/ancestor beside a handle-rooted or ordinary borrow-rooted receiver | the ordinary-borrow and fresh-register cases are accepted and stored; the owned-local-beside-handle-rooted case is **conservatively refused** (any owned-local move hits a `handleSym` binding); the owner/ancestor cases are refused by the held receiver/argument check (section 4.5 proof) |
| (f) pub impl, private concrete methods | `modules/run/ifaces_private_methods` | an importer converts `&t` and calls through the interface; the private methods are absent from the type's `.sym`, and the vtable in the defining module still points at them |
| (g) reader malformed shapes | `sym_test.py` `malformed_structures`, plus `ifaces.obx` source cases | `[]I` and `Result[I]` in a function signature (not only a field), an implementation receiver written as an alias spelling, an empty `interface { }`, and `p0: &I` in a function signature are all rejected with the exact section 8 detail |
| (h) read-only impure effects | `run/ifaces_effects.obx` and the module `effects_check` | a whole-object receiver binding survives; a deep reference into the receiver is refused; a deep receiver is itself invalidated; a pure read-only call accepts both |

Two residual limits are recorded, not tested as successes:

- **No nil cell.** Every interface value points to a constructed cell; a zero
  token can only come from a nil handle, which the type system prevents
  (`^T` is non-null and `?^T` must be narrowed), and the first `RT_DEREF` then
  exits 7 rather than dereferencing nil. A conversion does not add a separate
  nil check.
- **Construction-time liveness.** `RT_HANDLE` reads the object's header to
  capture the generation; it does not validate that the source borrow is live.
  The compiler's reference rules guarantee that for visible frees. A hidden
  free *before* a borrow conversion would make `RT_HANDLE` an unvalidated read,
  the same residual class as an existing hidden reference use; the token guards
  frees **after** construction. A handle conversion copies a token and does not
  read the object, so a stale handle field is caught by the first `RT_DEREF`
  (case d).

---

## 10. Implementation plan

### 10.1 Ordered steps

0. **Wait for methods.** Rebase on the methods merge and its one seed promote;
   confirm `0.6`/format 2/ABI 1 and that the seed is 0.6. Do not implement
   interfaces on the current 0.5 source.
1. **Data model, no behaviour.** `Type.kind == "iface"`; `ifaceSyms`/`impls`/
   `Impl`; helpers (`ifaceMethodsOf`, `findIfaceMethod`, `findImplIn`,
   `findImplFor`, displays, `handleSafe`); the classification changes
   (`isOwned` false, `nonNull` false, `isRef` false, `same`/`assignable` pointer
   identity); `releaseTables`. Build with the 0.6 seed; the existing suite must
   pass unchanged.
2. **Declarations and restrictions.** Contextual `interface`/`impl` in
   `compileUnit`/`typeDecl`; member parser; synthetic symbols; self-reference
   and the forward-shell rejection; `implDecl` and method matching;
   `parseType`/`symParseType` array/`Result` restrictions;
   field/global/return/`var`/initialiser checks; `closureCheck`
   and `checkExports`; module-end/reader main gating. Add declaration errors and
   declaration-only run programs.
3. **Conversion and parameters.** `ifaceValue` and its frame cells;
   `varDecl`'s interface branch (including inferred `let e = d`, and the
   `var e = d` rejection); `fnDecl`/`fnBody` interface parameter modes;
   argument conversion in `callFnWith`; visibility-filtered `findImplFor` and
   `implMissing` with the full/check `private_impl`/`foreign_no_impl` tests;
   binding/writability and the read-only errors; copies, loops and defers. Add
   construction/ownership tests.
4. **Dispatch and codegen.** `designatorT`'s interface branch and `isCall`;
   `ifaceCall` (receiver bind/hold, token/deref, hidden vtable slot, argument
   loop, effect checks, indirect call, result); synthetic pure/impure
   summaries; vtable emission at `impl` (methods defined first); handle-rooted
   and global-rooted checks; homes/while-lift tests. Add the run and error
   tests. Existing function-call messages must not change.
   **Checkpoint A (after step 4):** run the entire existing suite plus the new
   run/error tests, the homes checks and the ownership cases; measure the
   compiler's live peak; fix all call/effect/dispatch problems before touching
   the symbol format. This is the last point where a failure is confined to the
   type checker/codegen rather than mixed with the fixture migration.
5. **Symbol files.** Version bump; writer interface definitions and impl group
   plus the parameter writer; reader interface/impl parsing, order lists,
   `symFinish`, mode-0 comparison; migrate all 76 fixtures and the 3 goldens,
   update `sym_test.py`/`sym_fuzz.py`/`port_compare.py`/expect files, add
   positive source-less checks; regenerate goldens explicitly.
6. **Docs and examples.** `LANGUAGE.md` gains a `## 5b. Interfaces` section
   after the methods section (lettered so existing section references do not
   shift), the `.sym` sample with an interface/impl line, the error table and
   the removal of interfaces from §12's planned list; `README.md` updates the
   phase table, "Not yet done", `--version` and `.sym` description;
   `PERFORMANCE.md`/`bench/cli/README.md` keep their historical 0.5 samples and
   gain the same note the methods phase adds; `FORK.md` records the feature
   after implementation. `doc_check.py` runs the new examples.
7. **Mutants and gates.** Add the interface mutants; retarget stale patterns;
   record equivalents; run every gate; measure size and peak; fixed point;
   independent review; merge; one `make promote` after review (seed changed).
   The compiler source stays interface-free in round 1.

### 10.2 Files and functions

`obo/src/obo.obo` (current anchors; they shift after methods):
constants `109-111`; `Type` `882-895`; `newType` `899`; `isOwned` `2497`;
`nonNull` `956`; `isRef` `951`; `same` `960`; `assignable` `968`; `tname`
`937`; `parseType` `3892`; `parseRefType` `3590`; `fieldList` `3935`;
`typeDecl` `3968`; `varDecl` `6356`; `fnDecl` `8194`; `fnBody` `8114`/`8140`;
`nameItem` `5509`; `designatorT` `5561`; `callFn`/`callFnWith` `4760`;
`checkAssign` `4640`; `consume` `2861`; `borrowBind` `2964`; `itemBind`
`2938`; `makeRefLocal` `3027`; `invalidate` `3192`; `afterCall` `3459`;
`mutFrees` `3348`; `pureFn` `3359`; `callFrees` `4740`; `recordParamFree`
`3424`; `holdArg` `4692`; `argOrderCheck` `4709`; `relation` `6188`;
`assignmentOrCall` `6502`; `tryExpr` `5914`; `deferStat` `7626`; `ok`
`4957`; `rodAlign` `1953`; `rod64` `1957`; `descriptor` `1988`;
`closureCheck` `8327`; `checkExports` `8358`; `compileUnit` `8451`;
`appTypeDef` `9077`; `appTypeShell` `9069`; `appFn` `9137`; `symBody` `9211`;
`symText` `9281`; `symParseType` `9471`; `symFieldList` `9522`;
`symTypeDecl` `9652`; `symGlobalDecl` `9886`; `symFnDecl` `9916`;
`symReadDecls` `10103`; `readSym` `10167`; `symCompareOrder` `9571`;
`symFinish` `9632`; `releaseTables` `10361`.
The reader has its own type/field/global parsers (`symParseType`,
`symFieldList`, `symGlobalDecl`); the design does not assume `parseType`,
`fieldList` or `varDecl` are shared with the symbol reader.
New: `Impl`, registries, `ifaceValue`, `IfaceVal`, `ifaceCall`,
`ifaceMethodsOf`, `findIfaceMethod`, `findImplIn`, `findImplFor`, `implBase`,
`implMissing`, `implDecl`, `symImplDecl`, `symFinishTypeName`, `finishTypeName`,
`appImpl`, the interface branch of `appTypeDef`, the frame-cell helper and the
indirect-call emitter.

Tests: new `tests/run/ifaces*.obx`; `tests/errors/ifaces.obx`; new
`tests/modules/{run,errors,sym}/...` trees (including source-less checks and
expect files); `tests/sym_test.py`; the golden tree and `expected/*.sym`; the
76 committed fixtures; `tests/sym_fuzz.py`; `tests/port_compare.py`;
`tests/mutate_fork.py` (and `mutate_port.py` where shared). Docs:
`LANGUAGE.md`, `README.md`, `FORK.md` after implementation,
`PERFORMANCE.md`/`bench/cli/README.md` historical note. No change: runtime
(`obo/runtime/*`), `obo/lib/*`, `obo/cmd/*`, `obo/tools/*`, `tests/own_fuzz.py`,
`elf_test.py`, `io_test.py`, `meta_test.py` beyond running them.

### 10.3 Risks

1. **The hidden-cell lifetime argument is load-bearing.** It depends on the
   no-return/no-field/no-global/no-array/no-`Result` restrictions and on
   interface locals being `let`-only. Any extension changes the representation.
   Gates: the scope/error tests and a cell-reuse dispatch test.
2. **Purity contract timing.** `pureFn` is conservative for forward headings
   and recursion; a pure implementation may be rejected until its callees are
   defined. Document the ordering and test a forward-heading purity rejection.
3. **Conservative effects are broad.** An impure `&` member sets `freesAll` in
   callers and can reject handle-rooted uses far away. Pure members are the
   escape hatch. Record the accepted/rejected cases in `run/ifaces_effects`.
4. **Handle token for borrow conversions.** It adds an `RT_HANDLE` per borrow
   conversion and an `RT_DEREF` per call, and turns a hidden free that the
   compiler cannot see into exit 7 rather than a read of freed memory. Test
   `// expect-exit: 7` by storing a handle in a record/field while its owner is
   alive, freeing the owner behind the compiler's tracking, and converting the
   now-stale field after the free (a handle-rooted value made before a visible
   free is compile-time invalidated, so it cannot supply the exit-7 case).
5. **Vtable address finality.** Methods must be defined before the impl because
   `dropFrame` rewrites entries; the ordering check is a correctness gate, not
   a convenience. A mutant that accepts a heading must be caught.
6. **Symbol/format migration.** 79 fixtures, goldens, version tables,
   `sym_fuzz`, expect files and mode-0 order comparisons must move together.
   Positive source-less checks must assert exit 0; a broken REF can otherwise
   hide a regression by agreeing.
7. **Call/effect plumbing drift.** Function-call messages must stay
   byte-identical while interface labels are added. Shared helpers or a single
   display formatter; run the existing error tests before and after.
8. **Frame/register management.** Cells and hidden vtable slots are frame
   allocations; homes/retry and lifted `while` conditions re-emit rbp-relative
   code. Run `homes_check`, `homes_edge` and the loop cases.
9. **Memory/size.** Synthetic Syms, Impl records and interface Types are small,
   but measure the compiler's live peak against the 2,000,000 budget; do not
   raise `PEAK_BUDGET` without the user. A lazy/parallel structure is the
   fallback.
10. **No devirtualisation.** Interface calls are indirect and slower; state
    this in the docs/performance note rather than claiming parity.
11. **Methods in flight.** The rebase must preserve the methods phase's exact
    messages and symbol behavior; line-number anchors shift. Do not start the
    implementation until methods merge and promote.

### 10.4 Size and complexity

Estimated `src/obo.obo`: +800 to +1,100 lines (after the methods phase, roughly
10,900-11,000 → about 11,700-12,100, +7-10% over the current baseline), plus
tests (roughly 1,500-2,100 lines across `.obx`, Python, symcheck fixtures,
goldens and mutants) and docs. The feature is medium-high complexity: no
runtime, no new tokens, no ABI change, but a new type kind, a conversion path,
an indirect call path, a purity contract, a vtable/impl mechanism and a
symbol-format bump with a 79-file fixture migration. It is larger than the
methods phase. The likely real cost is in the type checker, the symbol reader/
writer and the effect/ownership interactions, not in code emission.

### 10.5 Gates

Per the project's normal set, on the merged tree:

- `run_fork.py` (run, errors, module trees, sym tests, own-fuzz),
  `module_test.py`, `sym_test.py` (golden compare, no `--update`),
  `sym_fuzz.py`, `doc_check.py`, `homes_check.py`, `homes_edge.py`,
  `elf_test.py`, `io_test.py`, `meta_test.py`;
- `port_test.py`, `port_compare.py` (default comparison, symcheck expect
  assertions, seed comparison with the new `(0, 7)` skips),
  `mutate_fork.py`, `mutate_port.py`, `mutate_io.py`;
- `imports.py`, `stdlib.py`, `tools.py`, `eza.py` unchanged and green;
- fixed point: `make obo` with the 0.6 seed, stage 2 == stage 3 (`cmp`), and the
  measured compiler peak within budget;
- one seed promotion after the independent review, on main, only if the source
  changed (it will).

---

## 11. Compatibility and interactions

- **Source is additive.** `type I = interface`, `impl I for T`, `pure fn` and
  `mut fn` inside interface bodies are invalid today; no valid program changes
  meaning. The only new restrictions apply to new constructs.
- **Concrete methods and fields keep their meaning.** A field access is never
  reinterpreted; methods and fields cannot share a name. An interface does not
  add members to a type's namespace.
- **`.sym` is not compatible across the format bump.** A 0.6 compiler rejects a
  0.7 file (`has format 3; this compiler reads 2`) and a 0.7 compiler rejects a
  0.6 file (`has format 2; this compiler reads 3`). Rebuild, do not reuse stale
  artifacts. ABI 1 means existing layouts, the one-word calling convention and
  existing summaries are unchanged; the new cell/vtable layout is format-3
  gated, and only the new declarations and generated interface code are new.
- **`--check`, `--sym-dir`, `--emit-sym`, `--reemit-sym`** keep their
  semantics. `--check` reconstructs interfaces and impls, uses a dummy vtable
  and discards code; it can check a source-less dependency tree with interfaces
  (exit 0 when valid). `--deps` is unchanged.
- **The compiler and seed:** the 0.6 seed builds the 0.7 compiler because round
  1's source is interface-free; the fixed point is stage2 == stage3. Only after
  the promote could the compiler or tools adopt interfaces; that is a separate
  decision.
- **Existing features:** records and closed types are the implementation
  targets; handles supply tokens and liveness; `Result`/`try`/`defer` compose
  with interface members; modules carry interface definitions and impls; no
  generics or lambdas are added; `eza`/lib/tools are untouched.
- **README/LANGUAGE/version** change at implementation, not here:
  `obo 0.7 sym-format 3 abi 1`, the new section 5b, the planned-list update and
  the historical performance note.

---

## 12. Decisions and open questions

These are recorded as recommended defaults so the implementation does not
reopen them; each is small and reversible except the first, which shapes the
representation and the handle-based surface.

1. **Purity contract (recommended: include `pure`).** Handle-based interface
   values need a static guarantee that dispatch cannot free their target. The
   `pure` member marker plus the impl check is the smallest sound rule. The
   fallback if review rejects it is to support only interface values whose
   target is a whole owner borrow (no handle conversion and no borrow through a
   handle), or to require every interface member to be pure (much more
   restrictive). This is the one substantive fork.
2. **Impl visibility (recommended: explicit `pub impl`).** A private impl is
   usable only in its own module; a `pub` impl requires exported interface and
   receiver types. The alternative is to infer export from the two types, which
   removes a declaration but makes public surface implicit.
3. **Interface locals are `let`-only (recommended).** It makes the frame cell
   immutable per binding and gives value semantics. The alternative, `var`
   reassignment, needs a fresh cell per assignment or a fat value and a more
   complex copy story; out of scope.
4. **Interface parameters are `d: I` / `mut d: I` (recommended).** The
   interface value is reference-like; `mut` marks a mutable *target*. The
   alternative, references to interface values (`&I`), would be a second
   reference layer and is out.
5. **No nil interface (recommended).** No initialiser-less value, no nil
   literal, no `?I`; calls always have a cell. The alternative would need a
   null check on every call and a defined meaning for a nil target.
6. **Conversion sources (recommended: same I, a borrowed concrete value, or a
   handle).** Owned values are rejected; arrays/strings/`Result` have no
   methods or impls. The alternative (implicit borrow of an owned place) would
   hide ownership at the call site.
7. **Variant source (recommended: use the closed type's impl).** This matches
   phase 7's one parent step and keeps one vtable per `(closed type, interface)`.
8. **No interface-to-interface conversion (recommended).** The pair has no
   dynamic type tag; even structurally equal interfaces are distinct nominal
   types. An itable/type-tag conversion is a later extension.
9. **Members are `&` or `mut &` receivers only (recommended).** Owned receivers
   cannot be called through a non-owning interface value; there is no syntax
   for them.
10. **Empty interfaces rejected (recommended).** Without downcasts a marker
    interface has no use; the restriction avoids a value with no callable
    member and keeps the grammar's "at least one method" check.
11. **No forward interface shell and no mutual interface parameters
    (recommended).** A `type I` shell followed by an interface definition would
    change the kind of a type that may already have been used as a record, which
    a single-pass compiler cannot make safe. The definition declares its own
    symbol before parsing members, so self-reference works; mutually referencing
    interfaces are out until a kind-stable forward mechanism exists.
12. **Vtable addresses never in `.sym` (recommended).** Full builds use the
    live `Impl`; `--check` uses a dummy and discards code. The alternative
    would put addresses in interface hashes and couple them to code layout.
13. **No devirtualisation (recommended for v1).** A later optimisation can
    specialise a conversion whose dynamic type is statically evident; it must
    not change the surface.

No question genuinely needs the user before review. If the user has a strong
preference on item 1 or 2, the rest of the design is unaffected; item 1 changes
whether handle conversions exist and item 2 changes only visibility.

---

## 13. Scope exclusions and extension points

Explicitly excluded, with the reason and the extension point:

- **Interface fields, arrays, globals, `Result` payloads, returns.** The cell
  is frame-resident and the value is not owned; storing or returning it would
  outlive the frame. Extension: an owned interface value (a heap cell with an
  owner, or a fat two-word value) and a lifetime/ownership story; then fields
  could hold it.
- **Nil interface.** Excluded with the value restrictions. Extension: a
  nullable cell and a uniform nil check; not needed for v1.
- **Interface-to-interface conversion, structural typing, variance.** The pair
  has no dynamic type tag and impls are nominal per `(T,I)`. Extension: a
  runtime type tag/itable and a conversion rule (superinterface embedding or
  structural matching); requires changing the cell and all call sites.
- **Embedding/superinterfaces and mutual interface references.** No
  method-set inclusion or slot mapping, and no forward interface shell.
  Extension: an interface would list superinterfaces, its vtable would
  concatenate slot ranges, conversion would use the tag, and a kind-stable
  forward declaration (or a two-phase interface definition) would allow two
  interfaces to name each other; out.
- **Owned receivers in an interface.** A non-owning interface value cannot
  move its target. Extension: owned interface values first; out.
- **Method values, lambdas, generic interfaces, function values.** They need
  code pointers/closures and generic instantiation, which the project does not
  have. Extension: none in this phase.
- **Dynamic loading, object files, linker, multiple inheritance.** The project
  is one source-compiled image with symbol-file interfaces, not objects.
- **`alive` on interface values, downcasts, reflection, interface equality.**
  They need a dynamic type tag or a defined identity; the pair's identity is a
  cell pointer, which is not a useful language-level equality.
- **Devirtualisation and optimisation.** A later pass can use a statically
  evident concrete type at a conversion; v1 always calls the vtable.
- **Adopting interfaces in the compiler, library, tools or `eza`.** A separate
  branch after this feature's review and promote, with differential outputs
  unchanged.

---

## 14. Appendix: worked details

### 14.1 A `.sym` fragment

```text
obosym 3
abi 1
compiler "0.7"
source 0x0123456789abcdef
dep io "io.obx"
interface 0xfedcba9876543210
module lib
depiface io 0x1111111111111111
type Shape uid 0x3333333333333333
type Shape = closed { Circle { r: int } Square { s: int } }
type Drawable = interface { pure fn area(): int; pure fn name(): string; mut fn scale(p0: int) }
fn (p0: &Shape) area(): int
fn (p0: &Shape) name(): string
fn (mut p0: &Shape) scale(p1: int)
impl Drawable for Shape
fn make(): Shape
```

`Shape` has a shell because it is a closed type; `Drawable` has none: its
definition line registers the type and computes its uid. `area` and `name` are
pure members; `scale` is impure (no `pure` marker), so a handle-rooted value may
call `area`/`name` but not `scale`. The vtable for `(Shape, Drawable)` has three
slots in that order. No method address or vtable address appears in the file.

### 14.2 Call lowering

`let d: Drawable = &c; d.area()` with `c: Circle`:

1. `&c` yields a borrowed `mem` item of type `Circle` (`factor`, `T_AMP`).
2. `ifaceValue` resolves `Circle` → parent `Shape` and `(Shape, Drawable)`,
   calls `RT_HANDLE` on `c`'s object pointer, allocates the frame cell, stores
   the token and the vtable, and makes the `let` local a reference local with
   the binding root `c`.
3. `d.area()` reads the local (a cell pointer), finds `Drawable::area`, checks
   the receiver mode (read-only), loads the token, calls `RT_DEREF`, pushes the
   object pointer, holds the receiver binding, sees no explicit arguments,
   emits `call qword ptr [vtable + 0]`, and returns the `int` result.
4. `afterCall` sees the pure synthetic summary: no `freesAll`, no
   `writesHandles`, no pfree events, so no references are invalidated and
   `userCalls` is not incremented.

`let d: Drawable = h; d.name()` with a handle:

1. `ifaceValue` copies the handle token into a new cell with the vtable; the
   binding roots at `handleSym`.
2. The call finds a pure member; the handle-rooted check passes. It loads the
   token, `RT_DEREF`s (exit 7 if the owner was freed), and calls slot 1.
3. If `name` were impure, the call is refused at compile time and no code is
   emitted.

`mut fn`:

1. `d.scale(2)` with `scale` mode 2 checks `binding.writable`. A borrow of a
   writable local/handle is writable; a read-only reference is not.
2. The receiver holder is registered as argument 0; the argument `2` is
   checked, consumed/pushed; the indirect call uses slot 2.
3. The synthetic summary decides invalidation: an impure `mut` member
   invalidates references below the receiver and records pfree events; a pure
   one (scalar writes/appends) leaves them valid.

### 14.3 Review checklist for the implementation

- [ ] No new token, keyword-table entry or scanner change; `port_test.py`
      passes unchanged.
- [ ] Interface declarations, members, self-reference, the forward-shell
      rejection, aliases and `pub` match section 2.
- [ ] Every interface-field/array/`Result`/global/return/`var`/initialiser
      rejection has an exact error test.
- [ ] Owned sources are rejected with the hint; borrow, bare-reference and
      handle sources are accepted; copies work.
- [ ] Source type `V` uses the parent closed type's impl; a variant impl is
      rejected.
- [ ] The cell is immutable, frame-resident, and copied by pointer; loop,
      recursion, argument and defer cases do not leak or alias a rewritten
      cell.
- [ ] Interface locals are reference locals; moves/frees of the owner
      invalidate them; defers and loops are checked.
- [ ] `mut` members require writability; `d: I`/`mut d: I` parameters and
      call-site downgrades/upgrades have exact errors.
- [ ] Handle-rooted impure calls are refused; a function that calls an impure
      member is impure and cannot receive handle-rooted values; pure members
      work through handles.
- [ ] Vtable slots are in interface declaration order; methods are defined
      before the impl; every signature/mode/return/purity mismatch is caught.
- [ ] `call qword ptr [vtable+8*slot]` is emitted with the correct stack
      padding and register save/restore; `RT_DEREF` traps as exit 7; results
      are owned/void/string correctly.
- [ ] Existing function/direct-method messages are byte-identical; interface
      labels are distinct and exact.
- [ ] Writer/reader: interface definitions, impl lines, parameter modes,
      purity, order lists, mode-0 comparison, `--reemit-sym` byte-identical.
- [ ] All 76 sym fixtures and 3 goldens migrate; goldens regenerate explicitly;
      version/format/ABI fixtures and `sym_fuzz` are updated.
- [ ] Source-less checks assert exit 0; private impls are omitted and refused
      identically in full and check modes; transitive use works in both.
- [ ] No test assertion deleted; stale mutant patterns retargeted; mode-0-only
      mutants recorded equivalent with the reason; peak budget not raised
      without the user.
- [ ] Compiler source stays interface-free in round 1; the 0.6 seed builds it;
      fixed point stage2 == stage3; one promote after review.
