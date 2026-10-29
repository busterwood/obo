# Methods (phase 7) — design note

Status: **design only**, for independent review. Baseline: `main` at
`211cb2f` (`Merge branch 'docs-final'`), read-only in the worktree
`/home/chris/obx/.claude/worktrees/methods-design` on branch
`methods-design`. The compiler source is 10,527 lines / 386,590 bytes
(`obo/src/obo.obo`), `LANGUAGE.md` 1,853 lines, `FORK.md` 5,085 lines; the
promoted seed `obo/seed/obo` is 425,985 bytes and reports
`obo 0.5 sym-format 1 abi 1`. Nothing is implemented by this pass: no source,
test, seed, runtime or tool file changes. The only repository write is this
file.

Scope is the user's roadmap phase 7: receiver syntax such as
`fn (r: &T) name(...)`, receiver modes `&` / `mut` / owned, desugared to
ordinary functions, **static dispatch only**, **no method values**, usable on
records and closed types in modules and in the tools. Interfaces and lambdas
are later phases; this note only records their extension points. Do not
over-scope.

Every claim about the current compiler below was checked against
`obo/src/obo.obo` at this baseline; `file:line` references are to that file.
Behaviour probes were compiled with the compiler built from this tree by
`seed/obo`.

---

## 0. Recommendation in one page

1. **Syntax.** A method is a top-level declaration
   `fn (r: RECV) name(params...) [": " Ret] ("{" body "}" | newline)`, optionally
   preceded by `pub`. `RECV` is exactly one of `r: T`, `r: &T`,
   `mut r: &T`. Methods are called `place.name(args...)`; the receiver is the
   designator chain before the `.name(`, and the name is never module-qualified.
2. **No new token or keyword.** After `fn`, `(` (rather than an identifier)
   begins a method; after `. name`, `(` (rather than anything else) begins a
   method call. Both decisions use the current token only: no lookahead, no
   backtracking, no AST. `fn (` is invalid source today (`expected identifier`
   probe), so the syntax is purely additive.
3. **One new compiler record field.** `Sym.recv: ^Type`, a sentinel `tVoid`
   for plain functions. Methods live in one program-wide registry
   `methodSyms: []^Sym` in declaration order, logically keyed by the canonical
   receiver `^Type` pointer (one image, small counts). No `Type` or `Module`
   layout change, no per-type allocation. The writer filters the registry by
   `owner` and `exported`.
4. **The receiver is parameter 0.** Everything existing then works: parsing
   `&T` through `parseRefType`/`refMode` (`obo.obo:3590`), `Param` mode
   0/1/2 (`obo.obo:7792`), `fnBody`'s parameter setup
   (`obo.obo:8140-8162`), `FnRec.params/pmodes/pfree` (`obo.obo:1110`),
   name resolution and moves for a normal parameter.
5. **Call emission is the existing call sequence plus a receiver.** `callFn`
   (`obo.obo:4760`) becomes a wrapper around `callFnWith(f, recv, hasRecv,
   rline, rcol)`; the receiver is converted, checked, held/moved, and pushed
   exactly like argument 0, before the explicit arguments. Function-call
   behaviour and messages must stay byte-identical.
6. **Ownership is free.** `&` receivers are ordinary read-only reference
   parameters; `mut &` receivers ordinary `mut` reference parameters (pfree
   bit 0 is the receiver); owned receivers ordinary by-value parameters. The
   call site reuses `borrowBind`, `consume`, `holdArg`, `argOrderCheck`,
   `noteWrite`, `mutFrees` and the mutation/handle invalidation loops. The
   only new soundness check is that an **owned receiver through a handle is
   rejected**: `designatorT` materializes a handle dereference in a register
   (`obo.obo:5586-5600`), and `consume` returns early for a register
   (`obo.obo:2878`), so a receiver passed that way would free the object the
   handle names behind its owner's back.
7. **Rejections are precise**, not silent dereferences: methods are declared
   only on a record or closed type **defined in the same module** (aliases to
   it are transparent); no methods on `int`/`byte`/`bool`/`string`/`[]T`/
   `?T`/`^T`/`Result[T]`; no owned receiver through a handle; no owned
   receiver on a field, element, global or reference (the existing `consume`
   messages).
8. **Static dispatch by the receiver's static type.** A variant-typed value
   (`Circle` in a `match` arm) also uses the methods of its closed type
   (`Shape`), by one parent step. There is no dynamic dispatch, no vtables,
   and one method per `(type, name)` (no overloading, hence no ambiguity).
9. **Member namespace.** Methods and fields cannot share a name in either
   direction (this is checkable at declaration time because a type's
   definition always precedes its methods); methods may share names with
   top-level functions/types/consts/globals/import aliases. A method may not
   be named a variant of its own closed receiver type.
10. **`pub` is sticky** across a forward heading and the body, exactly like
    functions. An exported method requires its receiver type to be exported;
    the existing `closureCheck` reports it as
    `exported 'Builder::push' uses private type 'Builder'; mark 'Builder' pub too`.
    Importers reach methods through values of the exported type; the method
    name is never used as `alias::method`.
11. **`.sym`** gets a source-like method line, its own declaration group
    (after globals, before functions), and a key `Type::name` for duplicate
    and ordering checks. The receiver is always parameter `p0`; names are
    canonicalised. Format becomes `obosym 2`, compiler `0.6`, ABI stays `1`
    (calling convention, layout, descriptors and summary meaning are
    unchanged; a method is a function whose first parameter is the receiver).
12. **Visibility is mode-independent.** On a foreign type, a method call that
    does not resolve to an exported method always reports the existing
    module-member error, whether the full build knows a private method or
    `--check` only has the symbol file. Locally it reports
    `type 'T' has no method 'm'`.
13. **No method values.** `f()` functions are already callable only
    (`obo.obo:5512`); methods follow the same rule. `b.m` where `m` is a
    visible method is a specific error, not a borrow or a field.
14. **Compiler source stays in the 0.5 subset** in round 1 (no methods in
    `obo.obo`), so `seed/obo` can build it. The seed is promoted once after
    review, as usual.
15. **`eza` is not refactored now.** Section 10 lists the concrete receivers
    it could adopt later; the differential tools/eza tests must not change
    when that refactor happens.

The two decisions worth the user's eye rather than only a reviewer's are the
member-namespace strictness (section 12.1) and variant dispatch through the
closed type (12.2); all other defaults are technical and reversible.

---

## 1. Scope and non-goals

In scope:

- declaration and call syntax, receiver modes, static dispatch;
- records and closed types, including tests that return, match and free them;
- declarations and calls in modules, exported through symbol files, with the
  existing `--check`, `--sym-dir`, `--emit-sym` and `--reemit-sym` surface;
- the ownership/borrow/effect-summary interactions and their exact errors;
- tests, mutants, docs and gates needed to merge safely.

Not in scope (extension points only):

- interfaces, dynamic dispatch, vtables;
- lambdas / function values (and therefore method values);
- generic methods (there is no generics feature);
- inheritance or subtyping (a closed type is the only "parent" relation and
  only one step is used);
- static/associated functions, UFCS, `Type::method(value)` calls, operator
  overloading;
- methods on built-in or derived types (`string`, `[]T`, `?T`, `^T`,
  `Result[T]`), and method declarations for foreign types;
- method chaining on an owned call result, or calling a method on a temporary
  (`make().m()`), because a call result is not a designator;
- separate compilation of method bodies (there are no object files; a full
  build recompiles every body);
- debug information;
- refactoring `eza`, the library or the compiler to use methods.

The project invariants hold unchanged: single pass, no AST/IR, source read
once, self-hosting fixed point, explicit seed promotion only after review, no
test weakening, textual `.sym` with source/interface hashes, one compiler
feature in flight, design review before implementation.

---

## 2. Syntax and grammar

### 2.1 Declaration

```text
MethodDecl  ::= "pub"? "fn" "(" Receiver ")" IDENT "(" [Params] ")" [":" Type]
                ( "{" StatementSeq "}" | NEWLINE )        // NEWLINE = forward heading
Receiver    ::= "mut"? IDENT ":" ReceiverType
ReceiverType::= "&" TypeName | TypeName
Params      ::= Param ("," Param)*
Param       ::= "mut"? IDENT ":" ("&" Type | Type)
```

- Exactly one receiver, with a name. `fn () name(...)`, `fn (a, b: &T) name(...)`
  and `fn (r: &T) (args)` are errors (section 7).
- `mut` goes before the receiver's name, exactly as for parameters:
  `mut r: &T`. `mut r: T` is the existing "write mut r: &T" error;
  `&mut T` is the existing `parseRefType` hint (`obo.obo:3594`).
- Modes: `r: T` (owned), `r: &T` (read-only borrow), `mut r: &T` (mutable
  borrow). No `^T`, `?T`, `&?T`, `[]T`, `string`, `Result[T]`.
- `TypeName` must resolve to a **record or closed type that is already
  defined in the current module** (`Type.incomplete` false,
  `Type.owner == curModuleName`, `Type.parent == nil`). A type alias to such a
  type is transparent and accepted; an alias to an imported type is rejected
  as imported.
- The receiver parens must be on one line with the method name and the
  argument `(`, as with `fn name(` today: a newline after `)` scans as a
  statement separator (`semiAfter`, `obo.obo:224-230`). Argument lists may
  span lines normally.
- Methods are top-level declarations only (the language has no nested
  functions); `pub` is allowed as for functions and is sticky across a
  forward heading and its body.
- Declaration order: the receiver type's definition must precede the method.
  The type may be forward-declared for other types, but a method cannot be
  declared between `type T` and `type T = ref record {...}`; the existing
  incomplete-type error is used. This removes the need for a second
  field/method collision check in `fieldList`.

### 2.2 Calls

```text
Call ::= Designator "." IDENT "(" [Args] ")"
```

- The call is recognised inside the existing postfix designator loop
  (`designatorT`, `obo.obo:5577`): after `. ident`, if the current token is
  `(` (`T_LPAREN`), it is a method call; otherwise the existing field access
  runs. No whitespace sensitivity: `b.push (1)` is also a call.
- The **receiver must be a designator**: a name (local, parameter, global, or
  `alias::name` global) followed by any number of `.field`, `[index]` and
  implicit handle dereferences. `(b).m()`, `make().m()` and `new T{...}.m()`
  are not method-call syntax. `make().m()` reaches the existing
  "store this value in a variable first" check when `make()` returns an owned
  value, or a syntax error when `new` is the base; neither is a new rule.
- The method name is never qualified: `b.push(1)`, not `lib::b.push` (the
  receiver may be `lib::g`), and there is no `Builder::push(b, 1)` form.
  `Builder::push` resolves `Builder` as a module alias and fails with the
  existing `'Builder' is not a module` error.
- A method call terminates the designator: `b.m().n` and `b.m()[0]` are
  rejected by the existing postfix checks (an owned call result is a
  temporary; `int`/`byte`/`bool`/`string` results cannot be fields or
  elements). There is no chaining on an owned result; store it in a local.
- A method call is an ordinary expression value: it can be a statement (its
  result discarded and freed by the existing rules), a `let`/`var`
  initializer, a `return` value, an argument, an index, a condition, part of
  `&&`/`||`, a record field value, a global initializer, the operand of
  `try`, or the statement of a `defer`, subject to all existing position and
  ownership rules.
- `try b.m()` is allowed exactly when `try f()` is: the operand is a fresh
  call result of type `Result[T]`, and the method body's return type is a
  `Result`. `defer b.close()` works (an owned receiver there is rejected by
  the existing "cannot move ... inside defer" rule).

### 2.3 Names, members, collisions, redeclaration

- A method's key is `(receiver type object, method name)`. Receiver modes are
  **not** part of the key: there is no overloading, so a second method with
  the same type and name is a duplicate whatever its receiver mode.
- A record's fields and its methods share one member namespace: neither may
  use a name the other uses. Enforced at the method declaration (record
  fields and, for a closed type, every variant's fields and every variant
  name). Because a type definition always precedes its methods, no inverse
  check in `fieldList` is needed.
- Methods are a **separate namespace from top-level declarations**: a method
  `push` and a function `push`, a type `push`, a const `push`, a global or an
  import alias may coexist. `push(x)` and `x.push()` resolve differently and
  unambiguously.
- Two different types may have same-named methods; dispatch is by the static
  receiver type.
- A method may not be named a variant of its own closed receiver type.
- Duplicate and forward-heading rules mirror functions
  (`fnDecl`, `obo.obo:8231-8254`):
  - a heading with no body, then a definition with the same receiver type and
    name and an identical signature (including the receiver mode), defines
    it; the `pub` flag is sticky;
  - a second definition is `method 'T::m' is already defined`;
  - a mismatched definition is `method 'T::m' does not match its forward
    heading`;
  - a heading with no body at module end is
    `method 'T::m' has a forward heading but no body`;
  - repeated identical headings are allowed, as for functions.
- Declare before use: a call to a method with no earlier declaration or
  forward heading is an error. The single-pass compiler cannot know a later
  declaration without a method pre-scan; forward headings are the answer,
  exactly as for functions (`lookup`, `obo.obo:2457-2470`).

### 2.4 Visibility and module qualification

- Methods are private unless declared `pub`. Exporting a method requires
  exporting the receiver type (checked by the existing `closureCheck`,
  `obo.obo:8327-8356`, run for methods in `checkExports`).
- Importers call exported methods on values whose static type is the
  importing module's type: `let b = lib::make(); b.push("x")`. They never
  name the method through the module. A private method of a foreign type is
  invisible in a full build even though the live `Type`/registry contains it.
- A module may not declare a method on a foreign type, so methods are always
  defined beside their receiver and there is no orphan or coherence problem.
- Qualified names in diagnostics use the declared module name, not the import
  alias: `lib::Builder::push`; inside the module `Builder::push`.

### 2.5 Examples

These become `// example:` blocks in `LANGUAGE.md` at implementation time.

```obo
type Builder = ref record { items: []string; n: int }
type Frozen = ref record { items: []string; n: int }

fn make(): Builder { return new Builder{items: new []string(0), n: 0} }

fn (b: &Builder) count(): int { return b.n }

fn (b: &Builder) at(i: int): string { return b.items[i] }

fn (mut b: &Builder) add(s: string) {
    append(b.items, s)
    b.n += 1
}

fn (mut b: &Builder) clear() { b.items = new []string(0) }

// owned receiver: the caller's builder is moved in, and freed at return
fn (b: Builder) freeze(): Frozen {
    let items = replace(b.items, new []string(0))   // a field cannot be moved out
    return new Frozen{items: items, n: b.n}
}

fn main() {
    let b = make()
    b.add("a")
    b.add("b")
    println(b.count(), " ", b.at(1))      // 2 b
    let f = b.freeze()                    // b is moved; f owns the items
    println(f.n)
}
```

`replace(b.items, ...)` on an owned parameter's field is existing, valid
language (verified against the current compiler); `return b` or reading a
scalar/string field are the other ways an owned receiver can be consumed.
See section 3.3.

```obo
type Shape = closed { Circle { r: int } Rect { w: int; h: int } }

fn (s: &Shape) area(): int {
    match s {
        Circle { return 3 * s.r * s.r }
        Rect { return s.w * s.h }
    }
}

fn main() {
    let s: Shape = new Circle{r: 2}    // a closed-typed local
    println(s.area())
    match s {
        Circle { println(s.area()) }   // the arm narrows s to Circle; the
        Rect { println(0) }            // method is still Shape::area
    }
    let c = new Circle{r: 3}           // static type Circle
    println(c.area())                  // resolved through its closed type
}
```

A `new Circle{...}` expression itself has static type `Circle`, so it is not
a `match` scrutinee (the existing rule: `match needs ... closed type ... not
Circle`); that is unrelated to methods. Its methods resolve through the
closed type, which is what makes constructed variant values usable.

---

## 3. Types, receiver modes and ownership

### 3.1 The hidden first parameter

A method is compiled as a function whose parameter 0 is the receiver:

| source receiver | `FnRec.params[0]` | `pmodes[0]` | how the body sees it |
|---|---|---|---|
| `r: T` | `T` | 0 (owned) | an owned parameter: freed at return unless moved/returned |
| `r: &T` | `T` | 1 (`&`) | a read-only reference parameter |
| `mut r: &T` | `T` | 2 (`mut`) | a mutable reference parameter |

`fnBody` already builds exactly these locals from `Param` (`obo.obo:8140-8162`):
a mode-0 owned parameter joins `ownedLocals`; a mode>0 parameter becomes
`isRef` with `singleBind` and `kind = "let"` so it cannot be re-pointed.
`recordParamFree`'s index arithmetic
(`pi = len(params) - 1 - (offset - 16) / 8`, `obo.obo:3424-3433`) therefore
maps the receiver to bit 0 of `pfree`, and a receiver's place in `pf` rows is
index 0. No new ownership machinery is introduced.

### 3.2 Call-site matrix

The receiver is converted by the same per-argument code as a normal argument,
in this order: type check, borrow/ownership, effect and invalidation, push.

| receiver expression | `r: T` (owned) | `r: &T` | `mut r: &T` |
|---|---|---|---|
| whole local or parameter | **move** (`consume`), local marked moved, handles/references invalidated (`heldMove`) | borrow the place | borrow, requires a writable binding (`borrowBind(...).writable`) |
| field of a local/parameter | rejected: `cannot move out of a field or element; use take() on an optional one` | borrow that field (path tracked) | borrow that field; write is at the field's place |
| array element | rejected as above | borrow (constant index is a place; variable index is any element) | same, writable |
| global (`G` or `lib::G`) | rejected: `cannot move out of global 'G'; use take() on an optional one` | borrow; the existing "reference into a global to a callee that might free it" rule applies | same, plus the existing global mut-call rules |
| reference parameter/local | rejected by `consume`: `cannot move out of a reference: a reference only borrows` | re-borrow through the reference's binding | re-borrow; read-only bindings reject the write |
| through a handle (`h.field`, `h`) | the whole object `h` is **rejected explicitly**; a field/element `h.child` is rejected by `consume`'s field/element rule (section 3.5) | borrow through the handle (`handleSym` root): the callee must be pure under the existing handle-borrow rule | same; the callee must be pure under the same rule |
| optional (`?T`) | rejected by the existing nil check unless a local was narrowed | same | same |
| temporary / call result | not method-call syntax (section 2.2) | not method-call syntax; a `&` receiver on a temporary would be the existing "store this value in a variable first: a reference cannot borrow from a temporary" | same |

A `mut` receiver is legal on a `let` local when the object is writable. This
is existing language semantics for `mut` parameters, not a new rule: the
tests already pass a `let` record to `mut b: &Bag` (`tests/run/pure_mut.obx`
line 66). `mut` belongs to the parameter, not the type.

The receiver is evaluated first, then the explicit arguments left to right.
It is held while the later arguments run exactly as a reference argument
would be (`holdArg`/`argOrderCheck`, `obo.obo:4689-4725`), so
`b.store(clear(b))` is refused if `clear` may free what `b` points into.

### 3.3 Inside the body

The body is compiled by `fnBody` unchanged, so all existing rules apply as
they do to a function with the receiver as its first parameter:

- **`r: &T`** is read-only: `r.field = v`, passing `r` to a `mut`
  parameter, `append`/`truncate` through it, and calls to `mut` methods on it
  are rejected with the existing read-only-reference messages.
- **`mut r: &T`** is writable: fields may be assigned, `mut` arguments passed,
  and calls that free below `r` are recorded in `pfree` bit 0 and its `pf`
  rows. Writing through `r` keeps `r` itself valid (existing rule).
- **`r: T`** is owned: it is freed at return, may be returned (`return r`),
  moved onward into a local/field/global or a call, and `drop(r)` is allowed.
  It is **not** legal to move a field out of it (`return r.items`): that is
  the existing field-move rule. To extract a field, use `replace(r.field,
  new ...)` (allowed on an owned local's field) or return `r` itself.
- `defer` and `try` are the normal statement forms. A `&`/`mut` receiver
  used by a defer is subject to the defer-use rules; an owned receiver moved
  after a defer that reads it is refused.
- Recursion and mutual recursion use the existing forward-heading machinery.
  A recursive method's `mut` summary is judged at the end of the body, as
  `mutFrees` does for `f == curFn` (`obo.obo:3348-3357`).

### 3.4 `mut` receivers, aliasing and effect summaries

Because the receiver is argument 0, all existing call-site checks see it:

- **Aliasing:** `b.swap(b.child)` where the receiver is `mut` is
  `argument 1 of method 'B::swap' may point inside the receiver, which is
  mut`; the existing pair loop (`obo.obo:4832-4849`) is reused with a
  receiver-aware label. The reverse direction (a mut argument that the
  receiver points into) gets
  `the receiver of method 'B::swap' may point inside argument 1, which is
  mut`. A `&` receiver and a read-only alias are fine.
- **Held arguments:** a later argument that frees/moves what the receiver
  points into is
  `argument 1 of method 'B::clear' may free what the receiver points into,
  and runs after it; compute it before the call`.
- **Invalidation:** a `mut` receiver call that may free below the receiver
  invalidates references into it, through `mutFrees(f, 0)` and the
  `pfree`/`pf` rows; a call that only writes scalars or appends leaves them
  valid. A `mut` receiver call that frees the receiver's array kills a
  handle made from an element (`field origins`). These are the existing
  rules with parameter 0.
- **Globals and handles:** a `mut` receiver that may free a global, or that
  writes through a handle, carries the same summary bits as a function
  (`frees ...`, `writeshandles`, `freesall`).
- **Assignment across a method call:** a method that might free counts in
  `userCalls` through `movesFn`, so the target-located-before-a-call checks
  fire exactly as for functions (`checkTargetAcrossCall`).

### 3.5 Rejections specific to methods (summary)

- Declaring on a foreign type, a variant, a built-in/derived type, or an
  incomplete type: rejected (section 7).
- Owned receiver that is the **whole object named by a handle** (`h.m()`):
  rejected with
  `cannot call a method that takes its receiver by value through a handle: it
  would free the object; take it into a local first`. Rationale: the handle
  dereference is materialized as a register (`obo.obo:5586-5600`) and
  `consume` has no owner to mark (`obo.obo:2878`), so the object would be
  freed without its owner knowing; `take` into a local first is the fix.
  The whole dereference is a register item (`rtCall1` returns a `reg`); a
  field or element reached through a handle (`h.child.m()`,
  `h.items[i].m()`) is a `mem`/`elem` item and is not this case: it is a
  field/element move and gets the existing `cannot move out of a field or
  element` error from `consume` (`Item.path` is deliberately "" for every
  handle item, so the item mode is the discriminator, not the path).
- Owned receiver on a field/element/global/reference: the existing `consume`
  messages, unchanged.
- A `mut` receiver through a read-only binding:
  `the receiver of method 'T::m' needs a mut reference, but it is read-only`.
- Optional receiver without narrowing: the existing
  `'<x>' may be nil: check it with '!= nil' first` (for a local/reference
  local; an optional field cannot be narrowed, so take it into a local).
- A method call on `[]T`, `string`, `int`, `byte`, `bool` or `Result[T]`:
  `<t> has no methods`.
- Method value use, field call, unknown method, private method: section 7.

### 3.6 Static dispatch and no method values

Dispatch is resolved from the receiver's **static** item type at the call
site; there is no runtime method lookup and no value representing a method.
A call is always a direct rel32 call to one address. A variant-typed value
resolves its closed type's methods with one `Type.parent` step (section
12.2); it is not inheritance: variants cannot declare methods and closed
types have no parent.

---

## 4. Compiler architecture

### 4.1 Single-pass recognition

The parser already consumes one token at a time (`next`, `obo.obo:404`) with
no lookahead and no rewind except the explicit whole-function retry for
register homes. Both method decisions need only the current token:

- at top level, `compileUnit` sees `T_FN`, calls `next()`, then `fnDecl`
  (`obo.obo:8518-8521`). `fnDecl` reads an identifier for a function; if the
  current token is `(` (`T_LPAREN`) it is a method. Today `fn (` is invalid
  (`p14.obo:1:4: expected identifier` probe), so nothing valid changes.
- in the postfix loop of `designatorT` (`obo.obo:5577`), after `next()`,
  `ident()` has read the member name and `sym` is already `T_LPAREN` for a
  method call. There is no ambiguity to resolve by lookahead: fields and
  methods cannot share a name, and there are no function-typed values.

There is no AST and no desugaring step: the declaration registers a symbol
and compiles the body immediately through `fnBody`; the call emits directly
through the refactored `callFn` (section 4.4).

**Declaration-order consequence:** like functions, methods are visible only
after their declaration; unlike functions, the receiver type must already be
defined, because the method's key is the canonical `^Type`. A later-declared
method needs a forward heading. A pre-scan of the type's methods is not
available without buffering the module, which the single-pass architecture
does not do.

### 4.2 The method registry

Add to the compiler:

```obo
// every method of the whole compilation, in declaration order; each symbol's
// recv is its canonical receiver type (tVoid for a plain function)
var methodSyms = new []^Sym(0)
```

`Sym` gains one field:

```obo
    recv: ^Type              // the receiver type of a method; tVoid for a plain function
```

`newSym` (`obo.obo:1130`) initialises `recv: tVoid`. `releaseTables`
(`obo.obo:10361`) clears `methodSyms`.

**Why a flat registry rather than `Type.methods`:** the whole program is one
image; the canonical receiver `^Type` pointer *is* the type identity in a
full build and in `--check`, so a scan filtered by pointer is exact. The
count of methods in a program is small (dozens), and lookup happens only at
method call sites, so O(all methods) is irrelevant. It costs one new field
per `Sym` and one array, versus a `Type` layout change and an empty array
allocation for every type (`newType`, `obo.obo:899`). If profiling ever
matters, a per-`Type` list is a local change behind the same helper. The
logical method table, in the task's terms, is keyed by `(receiver Type,
name)`; physically it is this registry. There is no per-module table: the
writer filters by `owner`, which is exactly the module's declaration order.

Helpers (near the symbol tables, e.g. by `findSym`):

```obo
fn findMethodIn(t: ^Type, name: string): ?^Sym   // recv == t && name
fn findMethod(t: ^Type, name: string): ?^Sym {   // one parent step for variants
    let m = findMethodIn(t, name)
    if m != nil { return m }
    let p = t.parent
    if p != nil { return findMethodIn(p, name) }
    return nil
}
fn isMethod(s: ^Sym): bool { return s.recv != tVoid }
fn methodKey(s: ^Sym): string { return s.recv.name + "::" + s.name }      // one module
fn methodDisplay(s: ^Sym): string {                                      // diagnostics
    if s.owner != "" && s.owner != curModuleName {
        return s.owner + "::" + s.recv.name + "::" + s.name
    }
    return s.recv.name + "::" + s.name
}
```

The receiver type is set at declaration (`s.recv = recvType`) and by the
symbol reader. `tVoid` is a safe sentinel: a receiver can never be `void`.

### 4.3 Parser and checker changes

`fnDecl` (`obo.obo:8194`) is extended at the top, not duplicated:

```obo
var isMethod = false
var recvName = ""
var recvType = tVoid
var recvMode = 0
if sym == T_LPAREN {
    isMethod = true
    // parse exactly one receiver into recvName/recvType/recvMode, then ')'
}
let name = ident()
...
```

- The existing argument-list parse, return-type parse, forward-heading
  matching, `pub` stickiness, body emission and retry loop are shared. The
  receiver is prepended to the `params` array, so `fnBody` needs no change
  other than the display name it uses in errors.
- The program-main check `name == "main" && !curModule.isRoot` is guarded by
  `!isMethod`.
- For a method, the symbol is not declared with `declare` (it must not enter
  `curModule.globals`, `visibleGlobals`, `exports`, `programGlobals` or
  `lookup`). Instead:
  - `methodSyms` is searched for `recvType` + `name` for the forward-heading
    case;
  - a new symbol gets `kind = "fn"`, `storage = ""`, `owner =
    curModuleName`, `recv = recvType`, `line = hline`, a fresh `fnRec`, and
    is appended to `methodSyms` and `symArena`;
  - the receiver is parameter 0, then the explicit params.
- Receiver validation (section 7 messages) happens before the symbol is
  created: `kind` must be record/closed, `owner == curModuleName`,
  `parent == nil`, `!incomplete`; a field collision is checked on the
  receiver type (record fields; for a closed type, every variant's fields and
  variant names).
- Method call resolution in `designatorT`'s `T_DOT` branch
  (`obo.obo:5601`), after the handle dereference and the existing
  incomplete/optional checks, and **before** the closed/record field logic:

```obo
if sym == T_LPAREN {
    x = methodCall(x, fname, dline, dcol)     // helper: checks and callFnWith
} else if x.typ.kind == "closed" { ... existing ... }
else if x.typ.kind != "record" { ... existing ... }
else { ... existing field access ... }
```

`methodCall`:

1. If the static type is not record/closed: error `<t> has no methods`.
2. `let m = findMethod(t, fname)`.
3. If `t.owner != "" && t.owner != curModuleName` and
   `(m == nil || !m.exported)`: error
   `module '<owner>' has no exported member '<T>::<fname>' (only 'pub'
   declarations are visible)`. `<T>` is `m.recv.name` when `m` exists, else
   `t.name` (or `t.parent.name` for a variant). This is deliberately the
   same in a full build and in `--check`, whether a private method exists or
   not.
4. If `m == nil`: if the static type is a record and has a field `fname`,
   error `<T> has a field '<fname>', not a method; a field cannot be called`;
   otherwise error `type '<T>' has no method '<fname>' (declare it before
   use; use a forward heading for mutual recursion)`.
5. `callFnWith(m, x, true, dline, dcol)` (rest of section 4.4).

For `.name` **without** `(` on a **record** receiver with no field, if a
*visible* method exists, error
`method '<T>::<m>' cannot be used as a value: call it with '(' after its
name`. On a foreign type with no visible method, fall through to the existing
`has no field` error, so a private method's existence is not leaked. A
closed-type receiver keeps the existing
`<T> is a closed type: use 'match' to reach its fields` message: a closed
type has no fields, and that check runs before any method-value check.

### 4.4 Call emission: `callFnWith`

`callFn` (`obo.obo:4760-4903`) becomes:

```obo
fn callFn(f: ^Sym): Item { return callFnWith(f, voidItem(), false, 0, 0) }
```

`callFnWith(f, recv, hasRecv, rline, rcol)` keeps everything and changes only
the argument loop:

- `start = hasRecv ? 1 : 0`; `pad` still uses `n = len(f.fnRec.params)`
  because the receiver is pushed as well, so the stack shape is identical to
  a function with the same parameter count.
- The loop runs `i in 0..n`. For `i == 0 && hasRecv`, `a = recv` with
  `argLine/argCol = rline/rcol`; otherwise it parses an argument exactly as
  now, with `if i > start { expect(T_COMMA, "") }` and arity errors counting
  `n - start` and `i - start`.
- `checkAssign(f.fnRec.params[i], a, what)` uses
  `what = "the receiver of method '<methodDisplay(f)>'"` for `i == 0`, and
  `"argument <i - start + 1> of method '<display>'"` otherwise.
- The mode branch (`borrowBind`, writable check, `noteWrite`, `holdArg`,
  `argOrderCheck`) and the owned branch (`consume`, `argOrderCheck`) are
  unchanged. The held-argument bookkeeping needs a display convention: the
  receiver is 0 and a source argument is `i - start + 1`; plain calls are
  `i + 1`. Today `holdArg` stores the 0-based `i` and `argOrderCheck` prints
  `t.value + 1`; either keep that and encode the receiver specially, or
  store the 1-based/method display number consistently and have the printer
  use it directly. Either way `argOrderCheck`, the mut-alias loop, the
  deep-into-handle loop and the global loop must format through one helper
  so that the receiver prints as `the receiver` and arguments as
  `argument N`, and plain function messages stay byte-identical:
  - `argument <j> of method '<m>' may free what the receiver points into, and runs after it; compute it before the call`
  - `argument <j> of method '<m>' may free what argument <k> points into, ...`
  - `argument <j> of method '<m>' may point inside the receiver, which is mut`
  - `the receiver of method '<m>' may point inside argument <j>, which is mut`
  - the handle-borrow and global-reference messages use the same method
    display in place of the function name.
- The receiver is pushed by the same `pushI`/`pushR` sequence as an argument,
  so a `mut`/`&` receiver is the first argument and an owned receiver is the
  moved pointer. Then the call is the existing `E8 rel32` with fixups, and
  `afterCall`/`userCalls` run unchanged.
- **There is no name mangling.** The method is an ordinary `fn` symbol with a
  code address; the only name is the diagnostic display `T::m` (or
  `M::T::m`). Calls bind the symbol at compile time and emit a direct call,
  exactly as for a function; there is no linker, vtable or dynamic lookup.
- **Function-call behaviour and every existing message must be
  byte-identical**: `hasRecv = false` takes the old labels and old indices.
  The existing `tests/errors/arg_order.obx` and friends are the gate.

`callMethod` adds the receiver-specific check before calling `callFnWith`:
for an owned receiver that is a whole handle dereference (`x.viaHandle` and
`x.mode == "reg"`, the result of the `RT_DEREF` call; `x.path` is "" for
every handle item, so it cannot be used to tell a field from the whole
object), reject (section 3.5). Other illegal owned places, including a field
or element reached through a handle (a `mem`/`elem` item), are rejected by
`consume` with the existing messages.

### 4.5 Existing scans that must learn about methods

Methods are `fn` symbols that are not in `curModule.globals`. Every place
that scans the globals for forward-declared functions must also scan
`methodSyms`, or a method's fixups will be missed:

| site | why | change |
|---|---|---|
| `whileStat` condition lift, `obo.obo:7057-7068` | the condition is emitted into `lifted` and re-emitted after the body; calls to forward-headed functions have their fixups removed and re-added with a displacement (`obo.obo:7107`). A method call in a `while` condition to a forward-headed method would otherwise keep a stale rel32. | scan `methodSyms` with `addr < 0` as well |
| `fnDecl` register-home retry cleanup, `obo.obo:8279-8285` | when a function body is thrown away and recompiled, fixups of forward-headed functions that point into the discarded range are dropped. Method fixups must be dropped too. | scan `methodSyms` as well |
| module-end heading check, `obo.obo:8539-8547` | report a method heading with no body | loop over `methodSyms` where `owner == mod.name && addr < 0` |
| `checkExports`, `obo.obo:8358-8373` | exported method signatures must be closed over private types | loop over `methodSyms` where `owner == curModuleName && exported`, `closureCheck` receiver + params + return, `declName = methodDisplay(f)` |
| symbol writer, `obo.obo:9273-9276` | emit the method group | section 5 |
| symbol order check, `obo.obo:9571-9630` | compare the method order | section 5 |

One implementation detail in the first row: `waitFn`/`waitAt`
(`obo.obo:1442-1443`) currently store an **index into `curModule.globals`**
and the re-add loop dereferences it (`obo.obo:7107`). Methods are not in
globals, so the wait list must identify a symbol directly: change `waitFn`
to an array of `^Sym` handles (`[]^Sym`), or add a parallel kind/index
encoding. Everything else in the lift mechanism is unchanged.

The `main` check in the symbol reader (`symFnDecl`, `obo.obo:9920-9924`)
must be gated on `!isMethod`, or a method legitimately named `main` would be
rejected on re-read.

### 4.6 No new tokens or keywords

The scanner, token constants (`obo.obo:36-103`), keyword table
(`obo.obo:145-158`) and `--tokens`/port token tests are untouched. The
method syntax reuses `fn`, `(`, `)`, `.`, `:`, `&`, `mut` (an identifier
word, handled as today) and identifiers. `.sym` reuses `fn` and the same
receiver form. This also keeps `port_test.py`'s token checks unchanged.

---

## 5. Symbol files and modules

### 5.1 Representation

A method is emitted as a `fn` declaration with a receiver paren group, using
the source grammar and canonical parameter names `p0`, `p1`, ... (the
receiver is always `p0`):

```text
fn (p0: &Builder) len(): int
fn (p0: &Builder) at(p1: int): string
fn (mut p0: &Builder) clear() pfree 0x1 pf 0 "=/items"
fn (p0: Builder) count(): int
```

- The receiver type is written as the **defining name** of the type in this
  module (aliases are never emitted; `appTypeExpr` already emits the
  underlying type, `obo.obo:9011-9039`). It must be a complete, exported,
  non-variant record/closed type of this module.
- The receiver is parameter 0 for summaries: `pfree` bit 0, `pf 0 ...`,
  `frees N ...`, `freesall`, `writeshandles` are exactly as for functions.
- Only **exported** methods are emitted (a method is exported only if it is
  `pub` and its type is `pub`; the build rejects the other combination).
- Parameter names are syntax only; renaming the receiver or an argument
  changes the source hash, not the interface hash. The receiver mode and
  type, method name, argument types/modes, return type and summary are
  interface data.

Canonical group order in the body (one deterministic order, matching the
reader's expectations):

1. record/closed type shells (source order),
2. type definitions (definition-line order, as today),
3. type aliases,
4. constants,
5. globals,
6. **methods (declaration order)**,
7. functions (declaration order).

### 5.2 Writer

- `appFn` (`obo.obo:9137`) is split into a heading part and a summary part;
  `appMethod` emits `fn (` + optional `mut ` + `p0: ` + optional `&` +
  receiver type + `) ` + name + `(` + `p1...` + `)` + return + the shared
  summary. `appFn`'s output for functions is unchanged byte-for-byte.
- `symBody` (`obo.obo:9211`) adds a methods pass between the globals and
  functions passes, iterating `methodSyms` filtered by `owner == mod.name &&
  exported`.
- `OBO_VERSION` becomes `"0.6"` (`obo.obo:109`) and `SYM_FORMAT` becomes
  `2` (`obo.obo:110`). **`SYM_ABI` stays 1**: record layout, descriptor
  format, calling convention (the receiver is just argument 0), global
  addressing, effect-summary meaning and initialization order are unchanged;
  only the symbol grammar and the declaration model change. `--version`
  therefore prints `obo 0.6 sym-format 2 abi 1`.
- The interface hash is the FNV of the body, so method lines and their order
  are interface data; a dependency's method change changes every dependent's
  `depiface`, and a rebuild is required, as for any exported function.

### 5.3 Reader

`symFnDecl` (`obo.obo:9916`) accepts the method form:

1. If the token after `fn` is `(`, parse exactly one receiver:
   optional `mut`, a receiver name (any identifier), `:`, optional `&`,
   `symParseType`, `)`.
2. Read the method name (T_IDENT). The program-main check applies only to
   plain functions.
3. Read the argument list and return type as today.
4. Validate and resolve the receiver:
   - a `mut` receiver must have `&`; a receiver without `&` is owned
     (mode 0); with `&` mode 1, with `mut` mode 2;
   - the type must be a record or closed type, **defined by this module**:
     owner == `symCurMod.name`, `name` non-empty, `parent == nil`;
   - in the `.sym`, the receiver must be named by the type's defining name
     (`resolved.name` equals the token); an alias spelling is rejected;
   - the type must be exported (it was resolved through `findExport`), and its
     definition must already have been read in this file:
     `symHas(symDefined, name)` must be true (both modes; in `--check` the
     type would otherwise still be incomplete), else
     `method 'T::m' comes before the definition of type 'T'`;
   - a second method with the same `(type, name)` claims the same key and is
     `duplicate exported name 'T::m'`.
5. Build `ptypes = [receiver] + args` and `pmodes = [receiverMode] + args`,
   and validate the summary exactly as for functions (`pfree`/`pf` index 0
   is the receiver). Record `methodKey(f)` in `symMethodOrder` in **both**
   modes, after validation, so the mode-0 order comparison has the file's
   list.
6. Mode 0 (full build): `findMethodIn(liveType, name)` must find the live
   method; it must be exported; compare every parameter type/mode, return
   type, `freesall`, `writeshandles`, `pfree`, `frees` list and `pf` rows;
   any difference is `symDisagree("method 'T::m'")`. Do not construct or
   overwrite live symbols.
7. Mode 1 (`--check`): create the `fn` symbol (fresh `fnRec`, `addr = 0` as
   for functions, owner, `exported = true`, `recv`) and attach it to
   `methodSyms`. Do not put it in `symCurMod.globals` or `exports`: methods
   are reached through types, never by bare name.

New reader state:

- `var symMethodOrder = new []string(0)`, reset in `readSym`
  (`obo.obo:10322-10329`) with the other order lists.
- `symCompareOrder` (`obo.obo:9571`) builds the live methods from
  `methodSyms` filtered by `owner == symCurMod.name && exported`, in
  registry (declaration) order, maps them to `methodKey`, and compares with
  `symMethodOrder`; mismatch is `symDisagree("the method order")`.
- `symFinish` mode 0 (`obo.obo:9632-9650`): every live exported method's key
  must be in `symSeen`; otherwise `symDisagree("method 'T::m'")`. Extra file
  methods already fail during parsing.
- Mode 1's exports loop is unchanged (methods are not globals).

`--reemit-sym` works because the reconstructed module's method symbols carry
their `recv` and the writer filters by owner; the writer's canonical output
must round-trip byte-identically.

### 5.4 Type identity and cross-module calls

- The receiver is a canonical `Type` object: in a full build the defining
  module's object is shared with every importer (type identity, LANGUAGE §6);
  in `--check` the reconstructed object is created by the type shell and
  reused by every method line and every use. One key is one method table.
- A method on `lib::Builder` is found by scanning the registry for
  `recv == <the same Type object>` and the name; visibility is
  `m.owner == curModuleName || m.exported`.
- Structurally identical records in two modules are different types with
  different methods; aliases are transparent.
- A variant's parent pointer (set by both `typeDecl` and `symTypeDecl`) is
  the only parent step used.

### 5.5 Errors and staleness in symbol files

All reader failures use the existing clean form
`obxc: <sympath>:<line>:<col>: symbol file: <detail>` (exit 2) or
`symDisagree` (exit 2). New details:

| situation | detail / message |
|---|---|
| `fn` then `(` but no receiver name | `expected a receiver name` |
| `mut p0: Builder` in a file | `mut needs a reference receiver: write mut p0: &T` |
| receiver `[]int`, `int`, `string`, `?T`, `^T`, `Result[T]` | `the receiver must be a record or closed type defined in this module, not <t>` |
| receiver is a foreign type or an alias | `the receiver must be the defining name of a type in this module` |
| receiver is a variant | `the receiver cannot be a variant; declare the method on '<C>'` |
| method line before its type's definition | `method '<T>::<m>' comes before the definition of type '<T>'` |
| duplicate method key | `duplicate exported name '<T>::<m>'` |
| live signature/summary differs (mode 0) | `symbol file '<p>' disagrees with the compiled interface of '<m>' for method '<T>::<m>'; rebuild it` |
| live exported method missing from the file | the same, for method `'T::m'` |
| method order differs (mode 0) | the same, for `the method order` |
| format 1 file | `has format 1; this compiler reads 2; rebuild it` |
| compiler `0.5` file | `was built by compiler '0.5'; this is '0.6'; rebuild it` |
| malformed receiver type, unknown type, bad `pfree`/`pf`, duplicate summary item | the existing reader messages, with index 0 meaning the receiver |

A source-less dependency whose `.sym` has methods checks and compiles
normally in `--check`; a full build still requires every source
(`LANGUAGE.md` §6 rules unchanged). `--deps` is untouched.

---

## 6. Interaction with existing features

| feature | interaction |
|---|---|
| records | the primary receiver type; fields and methods share a member namespace |
| closed types | methods may be declared on the closed type; a variant-typed value calls them through one parent step; methods may not be declared on a variant |
| arrays | not a receiver type; `xs.m()` is `[]T has no methods`; a record element can be a receiver (`xs[i].m()`) |
| strings | not a receiver type; `s.m()` is `string has no methods`; `string` fields are ordinary fields |
| handles | an expression through a handle dereferences before method lookup, so `h.m()` is a method on the handle's record type; `&`/`mut` receivers follow the existing borrow-through-handle rules (a call that might free is refused); `^T` is not a receiver type; an owned receiver through a handle is refused |
| optionals | `?T` is not a receiver type; after `if x != nil` narrowing a local has type `T` and its methods work; an optional field must be put in a local first (no field narrowing exists) |
| generics | none exist; methods add none |
| `Result`/`try` | methods may return `Result[T]`; `try b.load()` works because `try` parses its operand with `factor` and consumes a fresh call result; `Result[T]` is not a receiver type; `match` narrowing of a result then allows a method on the payload |
| `defer` | method calls are ordinary statements in a defer; an owned receiver there is refused by the existing defer-move rule; receiver references used by a defer follow the defer-use rules |
| field origins / move bits | receiver move/borrow/invalidation is argument 0 of the existing call path; a `mut` receiver's `pf`/`pfree` events kill handles made from its fields; an owned receiver's move calls `heldMove` through `consume` |
| assignment targets | a method that might free counts in `userCalls`, so target-located-before-a-call checks fire as for functions |
| modules | methods travel with their type in the `.sym`; no re-exports; `--check`/`--sym-dir` unchanged |
| globals / initializers | a method call may appear in a global initializer if the method is declared before it; summaries in initializers add nothing (existing rule) |
| `main` | a method named `main` is not the program entry (the entry is looked up in `curModule.globals`); the dependency-main check applies only to plain functions |
| the compiler itself | stays method-free in round 1 so the 0.5 seed builds it; no adoption now |

---

## 7. Diagnostics

Style follows `LANGUAGE.md` §10: lower-case, single quotes around names,
`<T>::<m>` for a method (module-qualified as `M::T::m` when the message does
not already name the module), no trailing period.

### 7.1 Declaration

| situation | message |
|---|---|
| `fn () name` | `a method needs a receiver, as in 'fn (r: &T) name(...)'` |
| more than one receiver name | `a method has exactly one receiver` |
| `mut r: T` | `mut needs a reference receiver: write mut r: &T` |
| receiver is int/byte/bool/string/array/optional/handle/Result | `a method's receiver must be a record or closed type declared in this module, not <t>` |
| receiver is a foreign named type | `cannot declare a method on imported type '<M>::<T>'; declare it in the type's own module` |
| receiver is a variant | `cannot declare a method on variant '<V>'; declare it on its closed type '<C>'` |
| receiver type incomplete | `type '<T>' is declared but not yet defined` |
| field collision (record) | `type '<T>' already has a field '<f>'; a method cannot have the same name` |
| field collision (closed variant) | `type '<C>' already has a field '<f>' in variant '<V>'; a method cannot have the same name` |
| variant-name collision | `type '<C>' already has a variant '<V>'; a method cannot have the same name` |
| duplicate method | `method '<T>::<m>' is already defined` |
| heading mismatch | `method '<T>::<m>' does not match its forward heading` |
| heading with no body | `method '<T>::<m>' has a forward heading but no body` |
| `pub` method on a private type | `exported '<T>::<m>' uses private type '<T>'; mark '<T>' pub too` |
| missing name after the receiver | `expected a method name after the receiver` |

### 7.2 Calls

| situation | message |
|---|---|
| unknown method on a local type | `type '<T>' has no method '<m>' (declare it before use; use a forward heading for mutual recursion)` |
| unknown/private method on a foreign type | `module '<M>' has no exported member '<T>::<m>' (only 'pub' declarations are visible)` |
| receiver is a built-in/derived type | `<t> has no methods` |
| receiver type incomplete | `type '<T>' is declared but not yet defined` |
| nil optional receiver | `'<x>' may be nil: check it with '!= nil' first` |
| `&`/`mut` receiver on a temporary | `store this value in a variable first: a reference cannot borrow from a temporary` |
| `mut` receiver on a read-only binding | `the receiver of method '<T>::<m>' needs a mut reference, but it is read-only` |
| owned receiver on a field/element | `cannot move out of a field or element; use take() on an optional one` |
| owned receiver on a global | `cannot move out of global '<g>'; use take() on an optional one` |
| owned receiver on a reference | `cannot move out of a reference: a reference only borrows` |
| owned receiver through a handle | `cannot call a method that takes its receiver by value through a handle: it would free the object; take it into a local first` |
| `x.f(` where `f` is a field | `type '<T>' has a field '<f>', not a method; a field cannot be called` |
| method name used as a value (`b.m`) | `method '<T>::<m>' cannot be used as a value: call it with '(' after its name` |
| arity | `method '<T>::<m>' takes <N> argument(s), not <M>` / `... not more` |
| wrong receiver/argument type | `the receiver of method '<T>::<m>': cannot use <t> as <t>` / `argument <n> of method '<T>::<m>': cannot use <t> as <t>` |
| later argument frees the receiver | `argument <n> of method '<T>::<m>' may free what the receiver points into, and runs after it; compute it before the call` |
| mut receiver overlaps an argument | `argument <n> of method '<T>::<m>' may point inside the receiver, which is mut` (or `the receiver of method '<T>::<m>' may point inside argument <n>, which is mut`) |
| handle-borrow receiver to a freeing callee | `cannot pass a reference that borrows through a handle to <T>::<m>: it might free what the handle names` |
| reference into a global to a freeing method | `cannot pass a reference into global '<g>' to <T>::<m>: it might free it` |

Function-call messages are unchanged byte-for-byte.

---

## 8. Tests and mutants

Every case below is written with the change (never after), and no existing
assertion is deleted or relaxed.

### 8.1 `tests/run/` programs

| file | covers |
|---|---|
| `methods.obx` | all three receiver modes; field access beside methods; a method calling another method; methods returning int/string/record; calls in `println`, `let`, `if`, `return`; a local whose field and method names differ |
| `methods_fields.obx` | receivers that are fields, array elements (constant and variable index), globals, and narrowed reference locals/optionals; `for x in xs { x.m() }` |
| `methods_closed.obx` | methods on a closed type called through a `Shape`-typed local; a variant value resolving through its parent; a method whose body `match`es the receiver; both `&` and owned closed receivers |
| `methods_move.obx` | owned receiver consumes a local (leak check proves the free), returns a new record, returns `r` itself, uses `replace` to extract a field; an owned method on an owned parameter moves it onward |
| `methods_mut.obx` | a `mut` receiver that writes scalars and appends (a reference into the receiver stays valid), called on a `let` local; a non-mutating `&` method; a `mut` method on an array element |
| `methods_handle.obx` | `&` and pure-`mut` methods through `h.field`/`h` (accepted under the existing handle rules); a graph node type with `?^Node` fields whose methods walk the graph |
| `methods_defer_try.obx` | a `Result`-returning method and `let v = try b.load()`; `match b.load()`; `defer b.close()`; a `defer` inside a method body |
| `methods_recursion.obx` | forward headings, mutual recursion between two methods, self-recursive tree walk, and a forward-headed method called from a `while` condition (the lifted-condition fixup case of §4.5) |
| `methods_static.obx` | two types with same-named methods return different values; a top-level function with the same name as a method; no dynamic dispatch |
| `methods_names.obx` | a method named `len`, `main`, `push` beside a function `push`; a method whose name matches another type's field/method; an alias receiver spelling |

### 8.2 `tests/errors/methods.obx`

One `// ===` section per error, with the exact text from section 7:
declaring on a variant/foreign/built-in/incomplete type; duplicate method;
heading mismatch; heading with no body; field/variant-name collision; pub
method on a private type; unknown local method; private foreign method (a
module error case, not here); call on string/array; nil optional receiver;
mut receiver read-only; owned receiver on a field/global/reference/handle;
`x.f()` where f is a field; `b.m` as a value; arity (too few/too many);
receiver type mismatch is unreachable through lookup, so the argument-type
cases cover checkAssign; method value use; a call before declaration without
a heading; a move inside `&&`/`||`/`defer` of an owned receiver; a later
argument freeing the receiver; mut-receiver aliasing; receiver through a
handle to a freeing method; no method on `Result`.

### 8.3 Modules and symbol files

| case | what |
|---|---|
| `modules/run/methods` | `lib` exports a `pub` record with `&`, `mut` and owned `pub` methods; `main` calls all of them; one `mut` method frees below the receiver so a reference invalidation crosses the module boundary |
| `modules/run/methods_closed` | exported closed type with a method; `main` calls it on a variant value through the closed type |
| `modules/errors/private_method` | calling a private method of an imported type: exact `module ... has no exported member ...` (exit 1) |
| `modules/errors/foreign_method` | declaring a method on an imported type (exit 1) |
| `modules/errors/pub_method_private_type` | `pub` method whose receiver type is private (exit 1) |
| `modules/errors/method_heading` | a method heading with no body at module end (exit 1) |
| `modules/sym/methods` | a `symcheck` tree (the `modules/sym` kind read by `port_compare.py`) whose root calls an imported method; the source-less `--check` behaviour itself is covered by the `sym_test.py` tree below and by a `// example: modules check` block in `LANGUAGE.md` |

`sym_test.py` additions:

- `RICH_LIB` gains `&`, `mut` (one freeing, one pure), and owned `pub`
  methods, plus a private method proving omission; `RICH_MAIN` calls them;
  `roundtrip_rich`, `determinism`, `hash_bytes`, `check_mode_and_cli` cover
  the writer/reader/`--reemit-sym`.
- `hash_private_and_rename`: private method body change → source hash only;
  receiver/argument rename → source hash only (and the body still has `p0`/
  `p1`); method rename or receiver-mode change → interface hash; adding a
  private method → interface unchanged; adding a `pub` method → interface
  changes; reordering two `pub` methods → interface changes.
- `effects_check`: a `mut` method that frees below its receiver rejects a
  caller's reference into the receiver in the importing module (summary from
  the `.sym`); a `mut` method that only writes scalars/appends does not; a
  method that frees a global propagates through `frees`; a method that
  writes through a handle sets `writeshandles`.
- `malformed_structures`: with a method line as the fixture: duplicate
  method key; receiver `int`; receiver `&[]int`; receiver a foreign type;
  receiver a variant; `mut p0: T`; receiver alias spelling; method before
  the type definition; a `pfree` bit 0 with no `pf` row (and a row without
  the bit); unknown summary item; a method line for an unknown type; a
  second method line for the same key. The reader validates summary
  **consistency** (bit/row agreement, index range), not semantic truth: a
  forged `pfree 0x1 pf 0 "..."` on an owned-receiver method is structurally
  accepted, and a full build's mode-0 comparison against the live summary
  rejects it, exactly as for functions.
- `sourceless_and_build_requirement`/`check_mode_and_cli`: `--check` against
  a method-bearing dependency with the source deleted; the checked module's
  own `.sym` contains its methods; a 0.5/format-1 file is rejected with the
  format message; `--version` is `obo 0.6 sym-format 2 abi 1`.
- `golden`/golden tree: `lib.obx` gains two `pub` methods and one private
  method; the committed `expected/*.sym` are regenerated with
  `sym_test.py --update` (the explicit path; the gate only compares).

Note on `pf 0` for an owned receiver: the reader validates that a `pfree`
bit and its rows agree and that the index exists; it does not re-derive
whether the summary is truthful (symbol files are trusted build outputs).
The mode-0 comparison against the live summary catches a forged difference
in a full build; `--check` trusts the file, as it already must.

### 8.4 Docs

- `LANGUAGE.md`: a new `## 5a. Methods` section after Handles (lettered so
  the many existing "section N" references do not shift), covering
  declaration, receiver modes, calls, ownership, module qualification,
  collision rules and no method values; examples marked `// example:`
  (checked by `doc_check.py`); the version line
  `obo 0.6 sym-format 2 abi 1`, the `.sym` sample with a method line, the
  error table additions, and removal of methods from the planned list in
  §12.
- `README.md`: feature table/not-yet-done list, `--version` sample, the
  `.sym` description (format 2), and the size/hash rows after the final
  build (numbers recorded at merge, not invented).
- `FORK.md`: a "Methods (done)" record after implementation, following the
  file's convention, with the final gates and measurements.

### 8.5 Existing tests and fixtures that must change

- `tests/sym_test.py`: `OBO_VERSION = '0.6'`, `SYM_FORMAT = 2`; the literal
  symbol snippets at lines ~420, ~694 and ~1060 (`compiler "0.5"`,
  `obosym 1`) updated to the new format/version.
- `tests/modules/golden/tree/lib.obx` + `expected/*.sym`: methods added,
  goldens regenerated once, deliberately. The golden gate still compares
  byte-for-byte.
- `tests/port_compare.py`: `SEED_FILE_MIN` entries `(0, 6)` for each new
  `tests/run/methods*.obx` and `tests/errors/methods.obx`; the module trees
  are already outside the seed comparison. This is not a weakening: the
  entry is version-gated and stops applying when the seed reports 0.6.
- `tests/mutate_fork.py` (and `mutate_port.py` where shared): if the
  `callFn` refactor moves text matched by existing mutant patterns, those
  patterns must be retargeted to the new exact text. Likely affected:
  `read-only references can be passed as mut`, `mut aliases allowed`,
  `calls check only one root`, `calls ignore depth`, the `ARG_ORDER_MUTANTS`
  block, and `temporaries can be dropped`. The harness fails on a stale
  pattern by design; retarget, do not delete.
- `tests/port_test.py`: no change; it checks token constants and scanner
  fragments, and does not assert the compiler version.
- No existing `tests/run`/`tests/errors` program changes: the feature is
  additive.
- `tests/own_fuzz.py`: unchanged; it does not generate methods (recording
  that as a limit is better than an unmodelled fuzz).

### 8.6 Mutants

Add a `METHOD_MUTANTS` block (exact snippets captured from the final source;
a stale snippet fails the gate):

| mutant | expected catcher |
|---|---|
| owned receiver treated as `&` (no consume) | errors: use-after-move of the receiver, or the leak/double-free oracle in `methods_move` |
| owned receiver through a handle allowed (drop the `viaHandle` check) | `errors/methods`: the rejection case |
| receiver mode ignored (always mode 0/1) | receiver-mode matrix and read-only errors |
| `mut` receiver not checked for writability | `errors/methods`: read-only receiver |
| receiver not inserted as parameter 0 (push nothing/shift by one) | any `methods` run program (wrong output/crash) |
| receiver not held across later arguments | `errors/methods`: `b.store(clear(b))` |
| mut-receiver invalidation dropped (`mutFrees` bit 0 forced false) | `run/methods_mut` + `effects_check` cross-module |
| receiver omitted from `pfree`/`pf` when writing the `.sym` | cross-module invalidation (`modules/run/methods`, `effects_check`) |
| writer omits the method group | roundtrip/golden |
| writer emits private methods | hash/golden + `modules/errors/private_method` |
| reader attaches a method to the wrong type (uses name only) | two types with same method name (`run/methods_static`, `modules/run/methods`) |
| reader visibility check dropped | `modules/errors/private_method` |
| reader accepts a foreign/variant/built-in receiver | `malformed_structures` cases |
| method order not compared | `symCompareOrder` case |
| field/method collision check dropped (either direction) | `errors/methods` declaration cases |
| duplicate method allowed | `errors/methods` duplicate case |
| forward-heading signature match dropped | `errors/methods` mismatch case |
| module-end heading check dropped | `modules/errors/method_heading` |
| while-condition lift ignores method fixups | `run/methods_recursion` (forward-headed method called in a `while` condition) |
| method call resolves with no type check (any type's method) | `errors/methods` wrong-type cases |
| method value allowed | `errors/methods` method-value case |
| unknown-method/field-call message regressions | exact-error tests |
| `checkExports` skips methods | `modules/errors/pub_method_private_type` |
| `main` method treated as program main | a module with a method named `main` compiles (`run/methods_names`) |

The while-lift and home-retry mutants are the subtle ones; if the
implementation cannot make one observable, record it as equivalent with the
reason (the project's convention) rather than claiming it caught.

---

## 9. Implementation plan

### 9.1 Ordered steps

1. **Data model, no behaviour:** `Sym.recv`, `methodSyms`,
   `findMethodIn/findMethod/isMethod/methodKey/methodDisplay`, `releaseTables`
   reset. Build with the 0.5 seed; the full existing suite must pass
   unchanged.
2. **Declarations:** receiver parsing and validation in `fnDecl`; registry
   append; duplicate/forward-heading rules; field/variant collision;
   module-end heading check; `checkExports` loop; the two fixup scans
   (`whileStat` lift, `fnDecl` retry); the `main` gating. Add declaration
   errors and declaration-only run programs.
3. **Calls:** the `designatorT` method branch; `callFnWith`; receiver place
   rules; labels. Add receiver-mode/move/borrow/alias/arg-order tests.
   Existing function-call messages must not change.
4. **Symbol files:** version bump; `appMethod`; writer group; reader;
   order lists; `symFinish`; mode-0 comparison; fixtures/goldens/constants.
   Regenerate goldens explicitly.
5. **Docs and examples:** `LANGUAGE.md`, `README.md`, `doc_check`.
6. **Mutants and gates:** add the method mutants; retarget stale patterns;
   run every gate (9.5); measure size/peak; fixed point.
7. **Independent review, fixes, merge, then one `make promote`** (source
   changed). The compiler source must remain free of methods until after the
   promote; using methods in `obo.obo` is a possible later round 2, not this
   phase.

### 9.2 Files and functions

- `obo/src/obo.obo`: `Sym` (`:992`), `newSym` (`:1130`), registry helpers
  near `findSym` (`:2450`), `fnDecl` (`:8194`), `fnBody` display name
  (`:8114`), `designatorT` (`:5561`), `callFn`/`callFnWith` (`:4760`),
  `argOrderCheck` (`:4709`), `consume` (`:2861`), `borrowBind` (`:2964`),
  `checkExports` (`:8358`), `whileStat` lift (`:7025`), module end
  (`:8539`), `appFn`/`appMethod` (`:9137`), `symBody` (`:9211`),
  `symCompareOrder` (`:9571`), `symFinish` (`:9632`), `symFnDecl` (`:9916`),
  `readSym` (`:10167`), `releaseTables` (`:10361`), constants (`:106-111`).
- Tests: new `tests/run/methods*.obx`; `tests/errors/methods.obx`; new
  `tests/modules/{run,errors}/...`; `tests/sym_test.py`; goldens;
  `tests/port_compare.py`; `tests/mutate_fork.py`.
- Docs: `LANGUAGE.md`, `README.md`, `FORK.md` (after implementation).
- No change: runtime (`obo/runtime/*`), `obo/lib/*`, `obo/cmd/*`,
  `obo/tools/*`, `tests/own_fuzz.py`, the seeds.

### 9.3 Risks

1. **`callFn` is shared by every call.** The refactor must be mechanically
   label-preserving for functions. Gate: the existing error tests
   (`arg_order`, `call_targets`, `pure_mut`, ...) plus new receiver tests.
2. **Receiver held across later arguments** is a soundness rule, not a
   convenience. Gate: `b.store(clear(b))` must be refused at the argument.
3. **Owned receiver through a handle** is the one genuinely new soundness
   hazard (the `consume` register early-return). Keep the explicit
   rejection and its error test.
4. **Lifted `while` conditions** move code after the body; a method call to
   a forward-headed method in the condition must have its fixup relocated
   with the condition. Forgetting this is a silent miscompile. Gate: a
   dedicated run case.
5. **Register-home retry** truncates code; forward-headed method fixups in
   the discarded range must be dropped like function fixups.
6. **Symbol reader/writer drift** is caught by `--reemit-sym`, the golden
   byte comparison, and mode-0 live comparison; keep all three.
7. **Visibility differs between a full build (live private methods) and
   `--check` (absent)**. The message policy (section 4.3) must be
   mode-independent; module tests run a full build and `--check` trees run
   the checked mode.
8. **Memory/size:** one pointer-sized `recv` field per `Sym` and one
   registry array; no per-type allocations. Measure the compiler's peak (currently 1,646,816
   live on main, budget 2,000,000). Do not raise `PEAK_BUDGET` without the
   user (project rule); if needed, a parallel `methodRecvs` array or a lazy
   structure is the fallback.
9. **Declaration-order friction:** the receiver type must be defined first;
   forward method headings are matched per `(type, name)`. A heading on one
   type does not define a same-named method on another.
10. **Seed compatibility:** all implementation code uses the 0.5 language
    subset; the tests use methods but the compiler's own source does not.

### 9.4 Size and complexity

Estimated `src/obo.obo`: +350 to +450 lines (10,527 → about 10,900, +3.5%),
plus tests (roughly 800-1,200 lines across `.obx`/Python fixtures/goldens)
and docs. The feature is medium complexity, on the order of the Result or
I/O phases: no runtime, no new tokens, no new ABI, but a shared-call-path
refactor and a symbol-format bump.

### 9.5 Gates

Per the project's normal set, on the merged tree:

- `run_fork.py` (run, errors, module trees, sym tests, own-fuzz),
  `module_test.py`, `sym_test.py` (golden compare, no `--update`),
  `doc_check.py`, `homes_check.py`, `elf_test.py`, `io_test.py`;
- `port_test.py`, `port_compare.py` (including the seed comparison with the
  new `(0, 6)` skips), `mutate_fork.py`, `mutate_port.py`, `mutate_io.py`;
- `tools.py` and `eza.py` unchanged and green (no library/tool/source
  changes);
- fixed point: `make obo` with the seed, then stage 2 == stage 3
  (`cmp`), and the measured compiler peak within budget;
- one seed promotion after the independent review, only if the source
  changed.

---

## 10. Where `eza` can adopt methods later (not now)

The tools and library were written with the record as the first parameter;
methods are a mechanical migration, but it is a separate branch after methods
merge and the seed is promoted, with the differential outputs unchanged.
Concrete receivers:

- `obo/lib/io.obx`: `pub type Reader`/`Writer` are exported records. The
  natural methods are `fn (mut r: &Reader) fill(): Result[bool]`,
  `readLine`, `readN`, `readByte`, `takeChunk`, `fn (r: &Reader) eof(): bool`,
  and `fn (mut w: &Writer) flush/write/writeBytes/writeByte`. Call sites
  `io::fill(r)` become `r.fill()`; inside the module `fill(r)` becomes
  `r.fill()`.
- `obo/lib/fs.obx`: `fn (mut d: &Dir) next(mut e: &DirEntry): Result[bool]`
  and `fn (mut d: &Dir) close(): Result[bool]`; `fs::next(d, e)` becomes
  `d.next(e)`, `fs::close(d)` becomes `d.close()`. `Stat` predicates are
  mode-based and stay functions.
- `obo/cmd/eza.obx`: `Entry` methods `fillStat(path)` (currently
  `fillStat(e, path)`), `fillTarget(path, base)`, `extHas(operand)`,
  `extKey(operand)`; `View` methods `displayName(e, quoteSpaces)` and
  `entryPath(e)` (the receiver is the type the body reads most); `Opts`
  predicates `dotsEnabled()`, `hiddenEnabled()`, `needStat()`. The call
  sites are the current first-argument calls (`fillStat(e, path)`,
  `displayName(v, e, q)`, `entryExtHas(es[i], operand)`, ...).
- `cli::Option`/`Parsed` and `sort` are candidates but not necessary; array
  and string helpers stay functions because built-in types are not method
  targets in phase 7.

Do not refactor the compiler itself in this phase; whether it ever uses
methods is a separate decision after the promote.

---

## 11. Compatibility

- **Source syntax is additive.** `fn (` is invalid today
  (`expected identifier`); `x.f()` where `f` is a field is invalid today
  (statement: `this expression does nothing`; expression: a parse error);
  `x.m` where `m` is not a field is invalid today (`has no field`). No valid
  program changes meaning. The only new declaration-time restriction
  (field/method name collisions) applies to code that does not exist yet.
- **Calls that were fields stay fields**: `x.f` with no `(` is unchanged;
  `let f = x.f` is unchanged. Methods cannot collide with fields, so no
  field access is ever reinterpreted.
- **`.sym` files are not compatible across the format bump.** A 0.5 compiler
  rejects a 0.6 file (`has format 2; this compiler reads 1`), and a 0.6
  compiler rejects a 0.5 file (`has format 1; this compiler reads 2`). This
  is the designed outcome of a grammar/meaning change; rebuild, do not
  reuse stale artifacts. The ABI number stays 1 because compiled programs
  are unaffected.
- **`--check`, `--sym-dir`, `--emit-sym`, `--deps`** keep their semantics;
  `--deps` does not read declarations at all.
- **The compiler's own source and the seed:** round 1 uses no methods, so
  the 0.5 seed builds the 0.6 compiler; the fixed point is checked with
  stage 2/stage 3 as usual. Only after the promote could the compiler adopt
  methods.
- **The golden `.sym` fixtures are regenerated explicitly**, not silently:
  the committed header changes (`obosym 2`, compiler `0.6`) and the golden
  tree gains methods.
- **`--version`** changes from `obo 0.5 sym-format 1 abi 1` to
  `obo 0.6 sym-format 2 abi 1`; README/LANGUAGE/tests are updated with the
  change.

---

## 12. Open questions and recommended defaults

Only genuine choices are listed. Defaults are the recommendation; each is
small and reversible except the first two, which shape the surface.

1. **Field/method collision (user-surface).** Default: **forbid** a method
   and a field with the same name on one type (and a method named like a
   variant of its closed type). Alternative: allow, since `x.m` and `x.m()`
   are syntactically distinguishable. Forbidding keeps one member
   namespace, avoids surprise, and is easy because fields precede methods.
2. **Variant dispatch (user-surface).** Default: **one parent step**, so a
   `Circle`-typed value (`let c = new Circle{...}`, or a value narrowed to
   `Circle` in a `match` arm) can call `Shape::area()`. Alternative: only
   values statically typed as the closed type may call its methods (the
   workaround is to move the value into a closed-typed local, e.g.
   `let s: Shape = c`). The parent step makes methods on closed types
   actually usable and is unambiguous because variants cannot declare
   methods.
3. **Method namespace (user-surface).** Default: **separate from top-level
   declarations**: a method `push` and a function `push` can coexist.
   Alternative: share the module namespace, which would forbid such natural
   pairs for no grammatical gain. (Methods are still keyed by receiver type,
   so two types may share a method name.)
4. **Owned receivers in phase 7.** Default: **include** (`r: T`). The
   roadmap asks for it; the call-site move and body ownership already work.
   Alternative: defer owned receivers and ship `&`/`mut` only, which would
   remove `methods_move` and one soundness case.
5. **Handles.** Default: **allow `&`/`mut` receivers through a handle
   expression** under the existing handle-borrow rules (only callees that
   cannot free), and **refuse owned receivers through a handle**.
   Alternative: refuse all handle receivers in phase 7 for the smallest
   rule set. Allowing follows field access, which already dereferences
   handles.
6. **`.sym` representation and versions.** Default: source-like
   `fn (p0: &T) m(p1: ...)`, `obosym 2`, compiler `0.6`, ABI `1`.
   Alternatives: a new `method T m(...)` word (still no new token, but a new
   grammar word) or a `methods` section; or bumping ABI (not warranted: no
   layout/calling-convention change). Reviewer-level.
7. **Method named `main`.** Default: **allow**; it is not the program main,
   and the two checks that assume functions are gated on `!isMethod`. The
   alternative is a special-case prohibition.
8. **Method calls on temporaries / chaining.** Default: **not supported**;
   the receiver must be a designator, and an owned call result must be
   stored. This follows from no method values and references not being
   returnable; supporting it would need an owned-temporary receiver rule
   and is a separate design.
9. **Diagnostic spelling.** Default: `T::m` (and `M::T::m`), matching
   `Module::name`; alternatives `T.m` or a quoted pair. Reviewer-level.
10. **Declare-before-use for methods.** Default: **yes**, with forward
    headings (the single-pass constraint; buffering a module is against the
    architecture). Not really open.

The user-level items are 1, 2 and 3 (and, weakly, 4 and 5); the rest are
technical defaults for the reviewer.

---

## 13. Appendix: worked details

### 13.1 Sample `.sym` fragment

```text
obosym 2
abi 1
compiler "0.6"
source 0x0123456789abcdef
dep io "io.obx"
interface 0xfedcba9876543210
module lib
depiface io 0x1111111111111111
type Builder uid 0x3333333333333333
type Builder = ref record { items: []string; n: int }
fn (p0: &Builder) len(): int
fn (p0: &Builder) at(p1: int): string
fn (mut p0: &Builder) add(p1: string)
fn (mut p0: &Builder) clear() pfree 0x1 pf 0 "=/items"
fn (p0: Builder) count(): int
fn make(): Builder
```

`add` appends and writes a scalar, so it frees nothing below the receiver
(no summary). `clear` assigns a new array over `items`, freeing the old one,
so `pfree` bit 0 and the `pf 0 "=/items"` event are emitted. `count` takes
the builder by value; the receiver is an owned parameter and has no `pfree`
bit.

### 13.2 Call lowering

`b.count()` with `b: Builder` local:

1. `designatorT` reads `b`, gets a `mem` item of type `Builder`;
   the postfix loop sees `.`, reads `count`, sees `(`.
2. `findMethod(Builder, "count")` returns the owned-receiver symbol.
3. `callFnWith`: pad for one parameter; `checkAssign(Builder, b-item,
   "the receiver of method 'Builder::count'")`; mode 0 so `consume(a)` marks
   `b` moved and invalidates handles/references; push the pointer.
4. No explicit arguments; `expect(T_RPAREN)`; `E8 rel32` to the method's
   address (or a fixup for a forward heading); `afterCall`; result is the
   return value (or `void`).

`b.add("x")` with `mut`:

1. same until lookup; mode 2.
2. `borrowBind(b)` gives a writable binding of the local; `noteWrite(b)`;
   `holdArg(binding, 0)`.
3. `checkAssign(Builder, ...)`; `push` the address; parse `"x"`, `consume`
   the string, push; argument-order and aliasing loops see the receiver as
   argument 0.
4. `mutFrees(f, 0)` decides whether the caller's references into `b` are
   invalidated; `userCalls` increments if the method might free.

### 13.3 Review checklist for the implementation

- [ ] Existing function call messages unchanged (run the error tests before
      and after the `callFn` refactor).
- [ ] Receiver is held across later arguments; `b.store(clear(b))` refused.
- [ ] Owned receiver through a handle refused, not silently freed.
- [ ] Method calls in a lifted `while` condition to forward-headed methods
      have relocated fixups (run case + inspection of `waitFn`/`fixups`).
- [ ] Register-home retry drops forward method fixups in discarded code.
- [ ] Full build (`--sym-dir`) and `--check` (source-less) produce the same
      diagnostics for private/unknown foreign methods.
- [ ] `--reemit-sym` byte-identical; golden compare without `--update`.
- [ ] A method named `main` compiles in a dependency and is not treated as
      the program main.
- [ ] `--version`, `sym_test.py` constants, golden headers and README docs
      all say `0.6`/format `2`/ABI `1`.
- [ ] No methods in `src/obo.obo` in round 1; `seed/obo` still builds it.
- [ ] No test assertion deleted; stale mutant patterns retargeted, not
      removed; peak budget not raised without the user.
