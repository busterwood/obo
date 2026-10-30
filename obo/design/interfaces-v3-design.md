# Interfaces v3 — the consolidated design

Status: **design only**, for review. Baseline: branch `first-class-interfaces-design` at `bec8fd1` (main's
compiler is `obo/src/obo.obo`, 11,027 lines; the promoted seed `obo/seed/obo` is `obo 0.6 sym-format 2 abi 1`
and has methods but no interfaces). Nothing is implemented. The only repository writes of this pass are this
file, a version-number shift in `lambdas-design.md`, and `README.md` in this directory.

This note replaces `first-class-interfaces-design.md` (the "v2 note", not chosen) and
`owning-interfaces-design.md` (the "owning note", chosen direction) as the thing to implement. v3 is complete for the value layer, calls, effects, `.sym` encoding and versions. For the declaration grammar,
`impl` grammar and `.sym` impl lines it cites the 0.7 note `interfaces-design.md`, and only for rules that are
unchanged: §2.1 and §2.2 (declaration and `impl` grammar), §4.2 (registry and vtable), §4.3 (matching, with the
changes in section 3.3 below) and §4.4 (closed types, variants, identity). Every rule that differs from the 0.7
note is stated here in full, and v3 quotes every diagnostic it relies on.

User decisions this note carries, not reopened: **owning-first**, and **version `0.8`, `SYM_FORMAT 4`, `SYM_ABI
1`** for interfaces, with lambdas shifted to Stage A `0.9` and Stage B `0.10`, both format `5`.

How claims were checked: compiler behaviour against `obo.obo` and `runtime.c` (anchored by function name, since
line numbers will shift), and idioms against the 0.6 seed with record analogues (probe output is quoted where it
matters). The interface code itself has not been run; the paused 0.7 branch
(`/home/chris/obx/.claude/worktrees/interfaces-impl`, read-only) is the reference for the parts that are reused.

---

## 0. Summary

1. **Interfaces get the three record tiers.** `I` / `?I` is an owning value, `&I` / `mut &I` is borrowed, `^I`
   is a checked link (phase 2). Each tier follows the existing rules for `T`, `&T`, `^T`. No fourth kind of
   value.
2. **`I` is a box**: a one-word pointer to a 16-byte payload `{obj, vtable}`. `obj` is an owned pointer to the
   target. One descriptor `[16][1][0][0]` serves every interface. `RT_ALLOC_RECORD` allocates **32 bytes** in
   total (16-byte header plus 16-byte payload). `rt_free` frees the target with the box. `isOwned(I)` is true,
   which gives moves, frees, descriptors, `take`, `replace`, arrays, `Result`, globals and teardown for free.
   The runtime does not change.
3. **Converting into `I` moves the concrete value into a new box**, as closed types already widen (`let s: Shape
   = new Circle{r: 2}`).
4. **`&I` is a pointer to a `{obj, vtable}` pair.** The pair is an `I` box (borrowing an `I` place) or a frame
   cell around a borrowed concrete object (`describe(&c)`). Every `&I` local owns its own pair slot and copies
   the pair on `let`, `var` and re-point (B1).
5. **A call through `I`, `&I` or `^I` is a direct method call on the box's object**, argument 0, with `obj` and
   `vtable` loaded before the other arguments run (S4). No `RT_DEREF` except for `^I`.
6. **Member effect tiers.** `pure` keeps its meaning. A plain `fn` / `mut fn` member is the **local** tier: it
   may free below its own `mut` reference parameters (the receiver for `mut fn`) and its owned parameters, and
   nothing else. The synthetic summary is per member and is **rebuilt from the markers and parameter modes
   alone**, identically in a full build and from a `.sym` (section 7.4).
7. **Parameter modes:** `p: I` is owned (mode 0), `p: &I` mode 1, `mut p: &I` mode 2, `mut p: I` is refused with
   the existing message.
8. **`take` is for optionals and returns an optional; `replace` moves out of a non-optional field, element or
   global.** `?T → ?I` is not a conversion in v1; the verified idiom is a `take`n or narrowed local moved into a
   typed `I` local (section 5.3).
9. **Lambdas:** the interface firewall stays, and `containsIface` must recurse into record and closed fields
   (option (a), section 10). The earlier claim that the firewall already holds was wrong.
10. **Versions:** interfaces `0.8` / format 4 / ABI 1; lambdas `0.9` / `0.10` / format 5. A 0.7-era file (where
    `p0: I` means a borrowed view) is rejected by the exact `compiler` and format checks, never misread.
11. **Round 1 keeps `obo.obo` interface-free**, so the 0.6 seed builds the 0.8 compiler. One seed promotion,
    after review.

---

## 1. Scope and non-goals

Not in scope (extension points only): interface-to-interface conversion, structural typing, variance,
superinterfaces; downcasts, type tests, reflection; owned-receiver members (`fn (c: Circle) finish()` through
`I`); method values; generics; an impure member tier (a third marker); `^I` from a non-boxed target (phase 2b);
the one-member-interface lambda conversion (constraints only, section 10.3); devirtualisation; refactoring the
compiler, library or tools to use interfaces; function types inside interface members.

Project invariants hold unchanged: single pass, no AST/IR, source read once, self-hosting fixed point (stage2 ==
stage3), explicit seed promotion after review, no test weakening, textual `.sym` with source and interface
hashes, one compiler feature in flight, design review before implementation.

---

## 2. The model

| | owned | borrowed (second-class) | checked link (storable, non-owning) |
|---|---|---|---|
| records / closed | `T`, `?T` | `&T`, `mut &T`, `&?T` | `^T`, `?^T` |
| **interfaces** | **`I`, `?I`** | **`&I`, `mut &I`, `&?I`** (params; section 6) | **`^I`, `?^I`** (phase 2a) |
| functions (lambdas) | — | `fn(...)` values | — |

An interface type in each position behaves like a record type in that position. The only difference is that a
member call is dispatched through a vtable.

**Invariant (cycle freedom).** Ownership stays a tree. A box owns exactly its object. Exactly one place (a
local, parameter, field, element, global or `Result` payload) owns a box. `&I` and `^I` own nothing. A move
transfers the unique owner, so a value cannot become part of itself, and `rt_free` (a walk of descriptors)
terminates and frees each block once. Back-links are `^`.

---

## 3. Declarations, parameter modes and impl checks

### 3.1 Declarations

The grammar and the contextual-word rules of 0.7 §2.1 are unchanged (`interface` after `=` only when followed by
`{`; `pure` and `mut` come before `fn`, in that order; at least one member; duplicate names are errors; no `pub`
on a member; an alias, import or export of the interface type is a normal named type; a forward shell `type I`
followed by a definition is refused). The changes:

- A member's parameter and return types may be any type, including `I`, `?I`, `[]I`, `Result[I]`, `&I`, `mut p:
  &I` and `&?I`. The 0.7 rules "an interface parameter is a borrowed view" and "an interface return is rejected"
  are gone.
- A member may name its own interface (`fn merge(p0: Shape): Shape`): the type symbol is declared before the
  members are parsed.
- A member has no receiver in its textual signature. The receiver is parameter 0 of the synthetic symbol: mode 1
  for `fn`, mode 2 for `mut fn`.

### 3.2 Parameter modes and their encoding

| source spelling | mode | meaning |
|---|---|---|
| `p: I`, `p: ?I`, `p: []I` | 0 | owned: the argument moves in; the callee frees it at return unless it moves it on |
| `p: &I` | 1 | borrowed read-only |
| `mut p: &I` | 2 | borrowed mutable (member calls may be `mut`; the box cannot be replaced) |
| `p: &?I`, `mut p: &?I` | 1, 2 | borrowed optional (section 6.4) |
| `mut p: I` | — | refused with the existing message below |

Probe on the seed (record analogue, `fn f(mut c: Circle)` and `fn (mut c: Circle) m()`):

```text
p1.obo:2:19: mut needs a reference parameter: write mut name: &T
p1b.obo:2:18: mut needs a reference parameter: write mut name: &T
```

The `.sym` reader's own text for the same error is `mut needs a reference parameter: write mut p0: &T`
(`symFnDecl`). v3 reuses both texts; no new message. Mechanics, all by existing code once `isOwned` accepts the
interface kind:

- **Parser.** `parseRefType` accepts `&I` because `isOwned(I)` is true; `fnDecl`'s parameter loop sets mode 2
  for `mut` only when the mode is already 1, so `mut p: I` hits the existing error. The interface member
  parameter parser must use the same rule (the paused branch factored it as `parseParams`; reuse it with this
  rule).
- **Writer.** For every function, method and interface member parameter with mode > 0 it emits `&` before the
  type, and `mut ` before the name for mode 2 (`appParams` / `appMethod` already do this). The paused branch's
  member writer (`appIfaceParams`) had an exemption `kind != "iface"` that omitted `&` for interface-typed
  parameters; **v3 deletes that exemption**. A member parameter is therefore written `p0: Shape` (owned), `p0:
  &Shape`, `mut p0: &Shape`, `p0: &?Shape`.
- **Reader.** After `&` the type must satisfy `isOwned`; without `&`, `mut` is the error above; a plain `pN: I`
  is mode 0. The same code reads interface member parameters (the paused `symIfaceDef`).
- **Mode-0 comparison** (a full build re-reads the file it just wrote) compares each live member parameter's
  type **and mode**, the receiver mode, the `pure` marker, the return type, count and order. A reader that
  ignores mode would accept a `.sym` where `p0: &I` became `p0: I`.

### 3.3 Impl check

`impl I for T` is checked as in 0.7 §4.3 with these differences. For every member the receiver type must have a
method of that name already **defined** (compiled) and:

- **receiver mode** equal (`fn` ↔ `fn (r: &T)`, `mut fn` ↔ `fn (mut r: &T)`);
- **parameters**: same count, identical types (`same`: the interface type is compared by identity, aliases
  transparent) and **identical modes 0/1/2**. `p: I` against `p: &I` is a mismatch;
- **return**: identical type (an interface return is now legal);
- **effects**: the tier check of section 7.2.

A mismatch of receiver mode, parameter mode or return type is refused by the comparison above: `fn (c: &Circle) grow(k: int)` implementing `mut fn grow(k: int)` gives `method 'Circle::grow' does not match interface '<I>' method 'grow': the receiver must be 'mut &Circle'` (E-impl-mismatch; mutant X38, section 12).

A variant value uses its closed parent's implementation (0.7 §4.4). One implementation per `(type, interface)`;
a downstream module may implement a dependency's interface for its own type; a private `impl` is invisible to
importers (0.7 §2.2). A method named `main` is allowed and is not the entry.

---

## 4. Representation

```text
I (owned box, heap, 32 bytes with header)   &I from &d (d: I)   &I from &c (c: Circle)
+-----------------+                          pointer to d's     frame cell, no allocation
| header  16 B    |                          box                 +-------------+
+-----------------+                                              | obj    ptr  | -> c (borrowed)
| obj     own  8  | --> target                                   | vtable u64  |
| vtable  u64  8  |                                              +-------------+
+-----------------+
```

- **Box.** A record whose descriptor is `descriptor(16, [0], [])`, that is `[16][1][0][0]` (size 16, one owned
  offset at 0, no strings), emitted once per program, shared by every interface type. `RT_ALLOC_RECORD` →
  `alloc_raw` rounds `16 + 16` to a 16-byte granule: **32 bytes**, one granule. `rt_free(box)` pushes `obj` and
  then frees the target through its own header descriptor, so variants and targets that own further boxes are
  covered. No runtime change; `SYM_ABI` stays 1.
- **Vtable.** One 8-byte code address per member, in interface declaration order, emitted at the `impl` once every method is defined; slot `k` is at offset `8*k`.
- **`&I`.** A one-word pointer to a 16-byte `{obj, vtable}` pair. The callee cannot tell, and need not know,
  whether the pair is a box or a frame cell. A frame cell lives in the converting function's frame, which is
  sound because `&I` cannot escape that frame (it is never stored or returned).
- **B1: each `&I` local owns its pair slot.** A `let` or `var` of type `&I` and every re-point (`best = cur`)
  copy the two words `{obj, vtable}` into the local's own 16-byte frame slot. A `for` walk variable over `[]I` or
  `[]?I` is not such a local: it is the element's box pointer (a reference to the element place), not a copied
  pair, and it cannot be re-pointed, so it needs no own slot.
  They never copy a pointer to another local's slot or to a conversion site's cell. Reason: a conversion site
  inside a loop rewrites its one cell on every pass, so a `&I` saved from an earlier pass would silently follow
  the new target while its binding still looks valid. Parameters are pointers to the caller's pair, which is
  stable for the call. **Per-site cells exist only for call arguments** (`f(&c)`), which are consumed within the
  call. The copied `obj` is still governed by the local's reference binding, so freeing the box or its target
  invalidates the local as for any `&T`.
- **S4: dispatch order.** For a call through `I`, `&I` or `^I`: resolve the receiver place; for `^I`,
  dereference the handle first (the existing handle path); load `obj` from `[p]` and `vtable` from `[p+8]`
  **together, before any explicit argument is evaluated**; hold `obj` as argument 0 and keep `vtable` in a slot
  that survives argument evaluation; evaluate and push the explicit arguments; then `call [vtable + 8*slot]`.
  Because the receiver is loaded first, an argument that frees the box can never turn into a read of freed
  memory, and the existing argument-order check (below) refuses such an argument anyway. Argument 0 is the
  method ABI's `&T` / `mut &T` receiver.
- **`?I`** is 0 or a box pointer, as `?T`.
- **ABI.** Every position is one word; record layout is one word per field; array elements are 8 bytes;
  descriptors use the existing format (`isOwned` adds an interface field to the record descriptor, an `I`
  element to the array, an `I` payload to `okVariantOf`'s descriptor). No new runtime call.

---

## 5. Conversions

A conversion happens at a **typed destination**: `let`/`var` with a type, assignment to a typed place, a call
argument (including the value argument of `replace` and the element of `append`), a constructor field, a
`return`, a `?I` field assignment. `ok(x)` takes `x` as already typed. Inference never produces an interface:
`let e = &c` stays `&Circle`, `let e = c` stays `Circle`. A conversion needs code (a box allocation or a cell),
so it is a new step at the call sites of `checkAssign`, which today only checks `assignable`.

### 5.1 The table

| source | to `I` | to `&I` |
|---|---|---|
| owned record or closed `T` that implements `I`, held in a local, parameter, field, element or global | **moves** it into a new box (the usual move rules; a field or element cannot be moved by plain assignment, use `take` or `replace`) | borrows it: frame cell, built only after `borrowBind` succeeds, as passing an owned value to a `&T` parameter does |
| owned temporary: `new T{...}` or a call result of type `T` | **moves** it into a new box | `store this value in a variable first: a reference cannot borrow from a temporary` (the existing error; S2) |
| variant `V` of a closed `C` that implements `I` | moves `V` into a box with the `(C, I)` vtable; `obj` is the variant object (one parent step, 0.7 §4.4) | borrows it with the `(C, I)` vtable |
| `&place`, or a bare reference of such a `T` | `cannot use a borrowed 'T' as owned interface 'I': move the value in, or take the interface as '&I'` | frame cell; the binding is the place's borrow binding |
| `I`: an owned place | moves the box, no allocation | borrows the box (pointer to it, no cell) |
| `I`: a temporary (`make()` returning `I`) | moves the box | the store-first error above (S2) |
| `&I` | `cannot move out of a reference: a reference only borrows` (existing) | copies the pair into the local's own slot, or passes the pointer for an argument |
| `^T` | `cannot use ^T as interface 'I'` (as `^Box` to `&Box` is refused today: `argument 1 of f: cannot use ^Box as Box`) | same |
| `?T`, `?I` | `?T`: refused, section 5.3. `?I` unnarrowed: `'x' may be nil: check it with '!= nil' first` | same |
| another interface `I2` | `cannot use interface 'I2' as 'I': interface values do not convert` | same |
| array, string, `Result`, scalar | `cannot use <t> as interface 'I'` (no `&` hint) | same |
| `nil` | to `?I` only | — |
| no visible `impl` | local: `type 'T' does not implement interface 'I'`; foreign: `module 'M' has no exported implementation of 'I' for 'T' (only 'pub' implementations are visible)` (parent closed type for a variant), identical in full and `--check` builds (0.7 §6.4) | same |

The order of checks is source kind, then implementation.

### 5.2 Optional and `Result` destinations (S3)

- **`T → ?I`** (for example `return new Circle{...}` from `fn f(): ?Shape`): boxes as for `I` and then widens as
  `T → ?T` does. `nil → ?I` is 0.
- **`I → ?I`**, **`?I → ?I`**: moves, no conversion.
- **Variants** to `I`, `?I`, `&I`: the parent's vtable, as in the table.
- **`ok(new Circle{...})` where the function returns `Result[Shape]`:** `ok` has no expected type. The seed's
  analogue (closed `Shape`) says:

  ```text
  p12a.obo:2:53: return value: cannot use Result[Circle] as Result[Shape]
  ```

  v3 keeps that error and appends `; store it first: let s: Shape = ...; return ok(s)`. `ok(s)` with
  `s: Shape` works (probed). `Result[I]` is a normal owned `Result` whose `ok` payload is an owned
  box.

### 5.3 `?T → ?I`: decision, with the probe

**Decision: no new conversion in v1; use the idiom.** `take(o)` returns `?T`, so `let s: I = take(o)` is invalid
(the seed's analogue: `p3c.obo:4:25: initialising 'b': cannot use ?Circle as Shape (it may be nil: check it with
'!= nil' first)`). The idiom is a **narrowed optional local moved into a typed non-optional local**, which the
seed accepts when the nil case leaves the block. Verified on the seed (analogue: variant `Circle` of closed
`Shape`, the same widening that `T → I` mirrors), under `OBO_CHECK=1`, exit 0:

```obo
fn wrap(o: ?Circle): ?Shape {
    if o == nil { return nil }
    let s: Shape = o              // moves the narrowed o into a Shape
    return s
}
// and for a stored optional:
let t = take(h.o)                 // t: ?Circle, h.o is nil from here on
if t == nil { return }
let s: Shape = t                  // accepted
// as an if/else, both arms must consume t:
if t != nil { let s: Shape = t; append(out, s) } else { drop(t) }
```

Two things the probes showed, to document in LANGUAGE.md: `if t != nil { consume t }` with no `else` is `'t' is
moved on one path but not another; move it on every path or none`, and a *field* cannot be narrowed (`if h.o !=
nil { let s: Shape = h.o }` is `cannot use ?Circle as Shape (it may be nil...)`), so take it into a local first. That `may be nil` message is for an owned `h`; with a `mut h: &H` parameter the seed instead reports `cannot move out of a reference: declare 's' as &Shape to borrow`.
For interfaces `Circle`/`Shape` become `T`/`I` and the box is built at the `let`.

Why not a consuming conversion: the seed already widens `?Circle → ?Shape` (probe `p3d`), so `?T → ?I` is the
natural expectation, but it needs a nil test plus a box branch at every conversion site, and refusing it now
costs nothing (accepting more later breaks no program). v1 refuses `?T → ?I` and `?T → I` with `cannot use ?T as
interface 'I' (it may be nil: take it into a local, return if nil, then convert the local)`. This is a default
the user may revisit (section 15).

### 5.4 Global initializers (7b)

The global init chain is straight-line code in the entry stub (`compileProgram` emits the stub, then `varDecl`
fixes up `initLink` before each global's initializer; there is no function prologue and `curFn == nil`).
`newLocal` allocates from the per-function `frame` counter, so a frame cell has no home there. Rule:

- **Allowed in an initializer:** owned conversions (`var G: Shape = new Circle{r: 5}`, `var O: ?Shape = new
  Sq{s: 2}`; probed with the closed analogue: compiles), borrowing an `I` place as `&I` (a pointer to the box,
  no cell; `var N = areaOf(G)` with `G: Shape`), and moving an owned `I` value into a global with no call through
  it (as v3 allows for owned values, section 5.1).
- **Refused, 0.8:** an interface method call anywhere in a global initializer, with or without arguments:
  `an interface method cannot be called in a global initializer; call a function that does it, as in var G = initG()`
  (E10). The init chain is straight-line code with no frame (`compileProgram` emits no prologue), so there is no
  frame slot to hold the `vtable` across the arguments; the workaround is trivial.
- **Refused:** a conversion that needs a cell, i.e. a concrete `&c` or `c` to a `&I` parameter (`var G = f(&g)`
  with `f(d: &I)`): `cannot create a borrowed interface value in a global initializer: pass the concrete value
  to a function that makes it`.

---

## 6. Ownership and positions

### 6.1 Rules

These are the record rules without exception. `let e = d` moves `d`. A by-value parameter `d: I` consumes the
argument. Assigning to an `I` field frees the old box and its target. `take(o)` on a `?I` place leaves nil and
returns `?I`. `drop(d)` frees the box and target. Scope exit frees boxes owned by locals. Freeing a record frees
its interface fields. `truncate` and freeing an array free the element boxes. Freeing a `Result[I]` frees the
payload box. Global teardown frees global boxes (`compileProgram` frees every global with `isOwned`).
Move-on-one-path errors, loop rules and `defer` rules apply unchanged.

**`take` versus `replace`.** `take` is for **optionals only** and returns an optional. A plain assignment cannot
move out of a field, element or global. To move a non-optional `I` (or any owned) value out of a field, element
or global, use `replace(place, new value)`, which returns the old value and stores the new one (the new value
converts to the place's type). The seed shows both rules:

```text
p2.obo:6:20: take needs an optional variable, field or element holding an owned value
p15.obo:5:32: cannot move out of a field or element; use take() on an optional one      (replace(xs[0], xs[0]))
replace on a field, an element and a global, each returning the old value: compiles and runs
```

So `shapes[0]` cannot be moved out by `let s = shapes[0]`; write `let s = replace(shapes[0], new Square{s: 0})`,
or for a `[]?I` element `take(shapes[0])`. `replace` and `take` cannot name a reference local or a `&I`
parameter (`replace cannot change 'r': it is immutable`).

### 6.2 Positions

Allowed: fields `I` / `?I`; arrays `[]I` / `[]?I` (with `new []I(n > 0)` refused as for any non-null element:
`array elements of type I would start as nil; use []?I, or start empty with new []I(0) and append`); globals;
`Result[I]`; locals; parameters; function, method and member **returns of `I` / `?I`**. `&I` / `mut &I`:
parameters and locals only, never stored or returned. `&?I`: section 6.4.

Never allowed: `new I` (`'I' is not a record type`), `const` of interface type (`constant value must be known at
compile time`), `match`, `println`, `alive` or `handle` on `&I`, and the comparisons of 6.3. A missing `I` field
or an `I` local without a value is the existing `T needs a value for d (it cannot be nil)` / `'d' of type I
needs an initial value (it cannot be nil)`.

### 6.3 Equality (7c)

The seed compares `T == T` and `?T == ?T` for records by pointer identity (probes: `a == b` on two records
prints `false`; `?Box == ?Box` is accepted; `Box == nil` is `cannot compare Box with nil`). For interfaces
identity is not meaningful (a box versus a frame-cell pair), and **a plain compare would silently succeed**:
`relation` treats an `iface` kind as neither a reference nor a mismatch, and a `?I == ?I` is accepted as `refs`.
So `relation` gets an explicit refusal: if either operand type is `I`, `&I`, `?I` or `&?I` and the other operand
is not `nil`, the error is `interface values cannot be compared; test an optional with '== nil' or '!= nil'`.
`?I == nil` and `?I != nil` are allowed and narrow a local as for `?T`. `I == nil` keeps the existing `cannot
compare I with nil`.

### 6.4 `&?I` (7d)

`&?I` is a reference to an optional interface place, following `&?T`. The seed (probe `p5a`) lets `fn get(o:
&?Box)` narrow with `o != nil`, read and (when `mut`) write through `o`, but not set it to nil (`'o' is
immutable`) or move out of it; field places are not narrowable, so `&?T` parameters are how code calls into an
optional field. v3 gives `&?I` the same meaning, representation (0, or a pointer to a pair) and narrowing (`o !=
nil` narrows a parameter or walk variable to `&I`, then `o.m()` dispatches), restricted by where the pair can
live:restricted by where the pair can live:

- **Allowed:** a **parameter** (`o: &?I`, `mut o: &?I`) and a **`for` walk variable** over `[]?I` / `&[]?I` (the
  element's box pointer, a reference to the element place, not a copied pair and never a cell). Sources for an argument: a `?I` or `I` place (the box word), `&?I`,
  `&I`, a concrete borrow (a per-site cell, valid for the call).
- **Refused:** an explicit or inferred **local** of type `&?I` (`let r: &?I = h.o`, `var r = &h.o` with `h.o:
  ?I`): `an optional interface reference can only be a parameter or a walk variable; narrow it to a '&I' local`.
  A `&I` local copies its pair (B1), but a nil-or-pair local would need a flag; the refusal keeps the rule
  uniform and costs a `if o != nil { let d: &I = o ... }` copy.
- Inside a narrowed `&?I`, `let d: &I = o` is an ordinary `&I` local (copies the pair).

### 6.5 References are reference locals (F4, explicit)

`&I` is registered as a reference local exactly as `&T` is: a `let` / `var` of type `&I`, an `&I` parameter and
an `&I` walk variable go through `makeRefLocal` / the reference-parameter path, with `borrowBind` of the source.
The conversion of a borrowed concrete value or an `I` place to `&I` must therefore produce a **borrowed item
carrying the source's binding**, so that `varDecl`'s reference-local branch (`declRef > 0 || x.borrowed`) takes
over. Consequently the existing rules apply to `&I` unchanged: invalidation by visible changes, loop re-point
and join rules, and "cannot point elsewhere while a defer uses it". The seed's wording for the last, on `&T`:

```text
p7a.obo:8:2: cannot point 'k' elsewhere while a defer uses it: the defer runs where its block is left, and would use the new target
```

---

## 7. Calls, receivers and effect tiers

### 7.1 Receivers and the call path

Any designator of type `I`, `&I`, `&?I` narrowed, or (phase 2a) `^I` can be a receiver: a local or parameter,
`r.d`, `xs[i]`, a global (`G`, `lib::G`), `h.d` through a handle, a narrowed `?I` **local** or `&?I` parameter,
`r.value` in a `Result` arm, and a `for` walk variable. These are the receiver forms of direct methods
(LANGUAGE.md §5a). The receiver is the box's object, borrowed `&` or `mut &` per the member's marker, and goes
through the same argument-0 code as a direct method: `borrowBind`, hold, alias and argument-order checks,
deep-receiver checks, handle borrows and global checks. A call result of type `I` is an owned temporary, so
`make().m()` is the existing store-first error. A `mut` member needs a writable binding: `the receiver of
interface method 'I::m' needs a mut reference, but this one is read-only` (method wording).

### 7.2 The tiers and the impl check

| member marker | the implementation `c` must satisfy (checked at `impl`) | synthetic summary `S(m)` |
|---|---|---|
| `pure fn`, `pure mut fn` | `pureFn(c) && !c.writesHandles` (the paused branch's `handleSafe`) | pure: `pfree 0`, no flags |
| `fn`, `mut fn` (**local**) | `!c.writesHandles && !c.freesAll && len(c.freed) == 0 && (c.pfree & ~S(m).pfree) == 0` | `pfree` bit on every mode-2 parameter (the receiver for `mut fn`); no flags; no globals |

`c.addr >= 0` is guaranteed by "defined before the `impl`". The local check reads the *final* summary of `c`: a
method that calls a function that is still only a forward heading carries `freesAll` and is refused
(conservative, sound). Probes of concrete summaries on the seed (`--sym-dir`), which the local tier relies on:

```text
fn (mut p0: &Node) deep(p1: int) pfree 0x1 pf 0 "!/kids/[0]"   // recursive mut method: pfree, not freesall
fn (mut p0: &Node) scalar(p1: int)                              // writes a scalar: pure
fn (mut p0: &Node) both(p1: int) pfree 0x1 pf 0 "=/k"           // assigns an owned field
fn (p0: &Node) count(): int                                     // pure
```

So a recursive `mut` implementation satisfies the local tier, and `scalar` can implement `pure mut fn` while
`both` and `deep` can implement only `mut fn`. Note that for a member with a read-only receiver and no `mut`
parameter, `fn` and `pure fn` have the same check and the same summary; the marker still round-trips and is part
of the interface hash.

### 7.3 What the caller does with `S(m)`

The caller applies the summary exactly as it applies a direct call's:

- **Deep receiver plus local member: accepted.** `shapes[i].grow(k)` invalidates references below `shapes[i]`,
  as `bag.items[0].reset()` would. The deep-receiver refusal (a deep argument plus `writesHandles` or
  `freesAll`) can never trigger on an interface call, because no member summary has those bits.
- **pfree bits with no `pf` rows mean "anywhere below".** `callFrees` takes the zero-row branch (`if n == 0 {
  callFreeEvent(br, apath, true) }`), so the synthetic summary needs no rows. It contains only whole-parameter
  events; the per-place rows of a direct call are per implementation and meaningless at the box's place.
- **Handle-borrowed receivers** (`h.d.m()`, a `^I` receiver) are references borrowed through a handle. The
  existing rule passes them only to calls that cannot free (`pureFn`): pure members and local members with
  neither a `mut` receiver nor a `mut` parameter. Otherwise the seed's wording (for `Box::reset`: `cannot pass a
  reference that borrows through a handle to method 'Box::reset': it might free what the handle names`) applies
  with the interface display, `... to interface method 'I::m': ...`.
- **Global receivers:** `mightFree(f, g)` is false for every member (no tier frees a global); a `mut` member on
  `G` frees below `G` through its pfree bits.
- **Propagation.** A function that calls a local member inherits that call's pfree events; it does not become
  `freesAll`.
- **S6: the summary is per member, never per implementation.** A tighter summary from one implementation would
  be unsound for the others. A `mut fn` member is never `pureFn`; every call to it invalidates references below
  the receiver.

### 7.4 Rebuilding the summary from a `.sym` (finding 2)

The `.sym` member line carries only the markers and the parameter list. A new helper, used by **both** the
interface declaration parser and the `.sym` reader (one function, so the two cannot diverge), builds the
synthetic symbol:

```text
inputs: pure (bool), pmodes[0..n) with pmodes[0] = 1 (fn) or 2 (mut fn)
freesAll = false; writesHandles = false; freed = []; addr = 0 (final)
pfree = 0
if not pure:
    for i in 0 .. min(n, 62): if pmodes[i] == 2 { pfree |= 1 << i }
no pf rows
```

Indices 62 and above need no bit: `mutFrees` already answers true for `i >= 62`. A `pure` member ignores its
mode-2 parameters (the impl check, `pureFn`, already guarantees nothing is freed below them). Worked example:

```text
type Shape = interface { pure fn area(): int; mut fn grow(p0: int); fn peek(p0: &Shape, mut p1: &Shape); pure mut fn bump() }
   area : pfree 0                    (pure)
   grow : pfree 0x1                  (receiver, mode 2)
   peek : pfree 0x4                  (p1 is parameter 2; p0 is mode 1, no bit)
   bump : pfree 0                    (pure mut: receiver mode 2, but pure)
```

The reader never reads a summary for a member, so a file cannot claim less than the markers imply. A forged
`pure` marker is the defining module's check to catch (importers trust the declaration; the LANGUAGE.md trust
model). **Required test (S-2, 12.3):** the same library is built with its source present and with the source
removed (`--check` against the committed `.sym` only); a matrix of calls (deep receiver, handle receiver, global
receiver, argument that frees the receiver, `mut` alias, reference kept across a `mut` member) must be accepted
and rejected identically, with identical messages. Reconstruction mutants: 12.4.

### 7.5 What the local tier costs

An implementation that writes owned values through a handle, or frees a global, cannot implement a non-`pure`
member in v1; it is still an ordinary method, just not callable through an interface. If a tool needs one, the
extension is a third marker with the old worst-case summary. Not in v1.

---

## 8. `.sym` encoding and versions

### 8.1 Representation

An interface is a normal exported named type with a source-like definition and no shell line (0.7 §6.1; its
definition line pre-registers the type so a member can name it):

```text
type Shape = interface { pure fn area(): int; mut fn grow(p0: int); fn merge(p0: Shape): Shape; fn peek(p0: &Shape, mut p1: &Shape); fn each(p0: &?Shape) }
type Holder = ref record { d: Shape; maybe: ?Shape; items: []Shape; r: Result[Shape] }
impl Shape for Circle
fn make(p0: int): Shape
var Current: ?Shape
```

- Members are in declaration order, `;`-separated, parameters canonical `p0, p1, ...` (explicit parameters
  only), markers `pure`, `mut`, `fn` in that order. The receiver mode is `fn` (read-only) or `mut fn`.
- Parameters use the spellings of section 3.2: `pN: I` is **owned**, `pN: &I` borrowed, `mut pN: &I` mutable
  borrowed, `pN: &?I` borrowed optional. `mut pN: I` is a file error (`mut needs a reference parameter: write
  mut p0: &T`). Returns are ordinary type expressions (`: Shape`, `: ?Shape`, `: []Shape`, `: Result[Shape]`).
  `^I` is a file error until phase 2a.
- `impl` lines, canonical group order and `--reemit-sym`: as 0.7 §6.1-§6.3. The groups are shells
  (records/closed only), type definitions (record, closed, interface), aliases, constants, globals, methods,
  **implementations**, functions. No cell layout, vtable address, method address or member summary appears in a
  `.sym`; the summary is rebuilt (section 7.4).
- The interface hash covers member markers, modes, types and returns and `pub impl` lines; a private impl or a
  private body changes only the source hash.

Writer, reader and mode-0 comparison: section 3.2. The reader also rejects an interface partial definition, a
duplicate member, a bad marker order, a function type inside a member, and a mode-0 disagreement naming the
member.

The reader does **not** re-run the tier check (7.2) on `impl` lines. The defining module checked it when it compiled the impl, and importers trust that check (the `.sym` trust model, LANGUAGE.md §6, 'Versions and the trust model'). A full build re-validates the freshly written `.sym` against the live compiled interface. `--check` and full builds still agree, because both rebuild member summaries from the interface's member markers with the same helper (7.4), never from impls.

### 8.2 Versions and why a bump (finding 1)

**`OBO_VERSION "0.8"`, `SYM_FORMAT 4`, `SYM_ABI 1`.** `--version` prints `obo 0.8 sym-format 4 abi 1`.

- **Format 4.** The grammar looks the same as the paused 0.7 branch's, but the **meaning of a member line
  changed**: in 0.7, `pN: I` was a borrowed view and `&I` was not a spelling; in v3, `pN: I` is an owned value
  and `&I` is the borrow. The reader requires exact `obosym`, `abi` and `compiler` lines (LANGUAGE.md, "The
  `.sym` format and hashes"), so a bump makes a 0.7-era file unreadable by a 0.8 compiler (`has format 3; this
  compiler reads 4; rebuild it`) instead of misreading its parameters. The paused branch is unmerged, so no
  released artifact is stranded; the bump is insurance for local build trees.
- **ABI 1.** Layouts, descriptors, calling convention (one word per argument), global addressing, summary
  meaning and initialisation order are unchanged. The box and the pair are internal to one image and are gated
  by the exact format and compiler match; no new runtime call.
- **Lambdas shift** (edited in `lambdas-design.md`, numbers only): the interfaces baseline is `0.8`/format 4, so
  Stage A is `0.9`/**format 5** and Stage B is `0.10`/**format 5**. Lambdas planned format 4 on top of format 3;
  on top of format 4 they need 5. Stage B changes only the compiler version. Fixture values below are the
  lambdas ones after the shift.
- **`^I` 2a**, later: expected `0.11`, format 5 (the grammar already has `^T`), ABI 1; assigned when scheduled.

### 8.3 Fixtures

At this baseline there are 88 committed `.sym` files (85 under `tests/modules/sym/*/symdir`, 3 goldens): 87
`obosym 2` and one `obosym 3` (the `format` fixture); 87 `compiler "0.6"` and one `"9.9"`; 87 `abi 1` and one
`abi 9` (counted from the tree; recount at implementation). Migrate **every** one in the same change as the
version constants:

- all to `obosym 4` / `compiler "0.8"` / `abi 1`;
- except `format`: `obosym 5`, expected `has format 5; this compiler reads 4; rebuild it`;
- `compiler`: format 4 with `"9.9"`, expected `was built by compiler '9.9'; this is '0.8'; rebuild it`;
- `version`: format 4, `compiler "0.8"`, `abi 9`, expected `has ABI 9; this compiler uses 1; rebuild it`;
- the 3 goldens regenerated explicitly with `sym_test.py --update` after the golden tree gains an interface, an
  impl and the new positions. Headers are outside the interface hash, so the migration itself changes no hash;
- `tests/sym_test.py`: `OBO_VERSION = '0.8'`, `SYM_FORMAT = 4`; the version table, `handwritten_interface`,
  `b6_stub_completion`. `sym_fuzz.py`'s `change_version` already derives the next format from the file (it adds
  1), so it needs no number change;
- `port_compare.py` `SEED_FILE_MIN`: `(0, 8)` for every new interface run and error file (the 0.6 seed cannot
  compile them); module/sym trees rely on the new compiler's own gates;
- a new committed stale file (S-4 in 12.3): a hand-written `obosym 3` / `compiler "0.7"` interface file with
  `p0: I`, expected to be rejected as `has format 3; this compiler reads 4; rebuild it`.

---

## 9. `^I` — phase 2

### 9.1 2a: a handle to a box (after lambdas Stage B)

`handle(place)` with `place : I` gives a plain `^I`: the existing one-word token naming the box, no new
representation. A use dereferences the box (exit 7 if it was freed), then dispatches (section 4, S4). Replacing
or freeing the box (field reassignment, `drop`, `truncate`) bumps its generation. `alive(h)`, `?^I`, storage in
fields, arrays and globals, the handle-borrow rules and the owner-tracking rules for `let` handles (LANGUAGE.md
§5) apply unchanged. The parser's `handles name records and closed types, not I` is relaxed for interface kinds
only. In 0.8 there is no `^I`: `handle(x)` on any `I`, `&I` or `?I` value fails with the existing error `handles name records and closed types, not I` (section 11).

- **S1 (phase 2a): `handle(x)` works only on an owned `I` place** (a local, field, element or global). It refuses `&I`,
  `mut &I` and `for` walk variables: `cannot make a handle from a borrowed interface value; make it from the
  place that owns it`. The type cannot tell a box from a frame cell, and `rt_handle` on a frame cell would
  compute a token from a stack address. The direct form with a `&Circle` parameter is accepted today (`fn f(b:
  &Box): ^Box { return handle(b) }` compiles on the seed), so this is a new rule with its own test. A `?I` place
  is refused as `?T` is today (`handle needs a record or closed-type value that is not optional, not ?Box`); the
  interface text is `handle needs a non-optional owned interface place, not ?I`.
- **Shared sinks (7a), narrowed.** A `mut fn` member may free below its receiver, so the handle-borrow rule
  refuses it through a handle (7.3). A shared sink reachable by `^I` must therefore expose **`pure mut`** writes
  (append, scalar writes), for example `pure mut fn write(buf: &[]byte)`. A sink whose `write` replaces its
  buffer cannot be `pure`; the caller goes through the owning place (`opts.out.write(buf)`) instead. Observers,
  back-links and registries over boxed objects work as for records.
- **Dispatch test (phase 2a, E6-style).** A call through a `^I` receiver dereferences the handle and loads `obj` and `vtable` before the arguments (S4). An argument that frees the box is refused by the existing argument-order rule (section 11, 'later argument frees the receiver').

### 9.2 2b: a link to a concrete target (deferred)

`^I` from `^Circle` or `handle(c)` with `c: Circle` needs a pair carrying the concrete target's token and the
`(Circle, I)` vtable (the v2 note's `{token, vtable}` cell). If adopted, 2a uses the same cell with the token of
the box's object. Defer until a tool needs a stored polymorphic link to an object that must keep its concrete
type.

---

## 10. Lambdas

### 10.1 Firewall decision (finding 6): option (a)

The lambdas design (`lambdas-design.md`, §5.2, the helpers `containsFn` / `containsIface`) forbids interfaces in
function types, lambda headers and captures. Its `containsIface` recurses through array, optional, handle,
result and function types **but not record or closed fields**, on the premise that "a record or closed type
cannot contain a function or interface field". That premise is false once interfaces may be fields (this note,
6.2). So `fn(&Holder)` with an `I` field in `Holder`, and a captured `Holder`, would pass the firewall. The
owning note's §9.1 and §13.2 ("the firewall holds") were wrong.

**Rule: `containsIface` recurses into the fields of a record and into the fields of every variant of a closed
type**, with a visited list so a recursive type terminates (a separate list with the same role as the
`closureSeen` of `closureCheck`, which is cleared per declaration and so cannot be shared). The list is truncated at the start of every top-level `containsIface` call, and only the recursive helper appends to it; otherwise a stale list from an earlier query could make a recursive type return false. A record or closed type that is still an incomplete forward shell (`type Holder` declared, fields not yet defined) also makes `containsIface` return true, since its fields are unknown and it must be treated conservatively. `containsFn` is
unchanged (a record still cannot hold a function field). It applies wherever lambdas calls `rejectIface`:
function-type parameters and returns, lambda headers, and the capture classifier. New messages when the hit is
indirect (the direct messages of the lambdas note are unchanged):

- `a function value cannot take or return '<T>': it holds an interface value`
- `cannot capture '<x>': its type holds an interface value; function values cannot take interface values in v1`
- `a function value cannot take or return '<T>': it is not yet defined; define it before this function type` (a forward shell)

Why not option (b), allowing records that hold interfaces. A pure body with read-only borrows can only call
members whose summary has no pfree bit, so no member could free; for a body that is pure by `pureFn(hidden) &&
!hidden.writesHandles` and sees records only as read-only borrows or snapshots, this argument is sound by the
tier rules of section 7. It is *not adopted now* because the lambdas design's reviewed capture, hold and handle
rules were written assuming no interface anywhere, owned `I` parameters and returns, `&I` in function types and
`^I` tokens in captures each add cases, and this pass may not edit lambdas semantics. Option (a) is the
conservative fix; (b) becomes the first relaxation step below, with the argument as its basis.

**Action for the lambdas implementation:** apply the recursion above; add tests for `fn(&Holder)`, a captured
`Holder`, an alias of such a record, a variant that holds an interface, a recursive record (no hang), and a forward shell (E9a). The
dated note in `lambdas-design.md` points here.

### 10.2 Relaxation order (later; each reuses an existing rule)

1. Records that hold `I`, and `&I`, in function types and read-only captures (the argument of 10.1(b));
   comparators over interface values need a `sortIndices` over `&[]Shape` (no generics).
2. Owned `I` in function types and lambda returns (`fn(): Shape`; allocating is not a free).
3. Captures: an owned `I` local is a read-only borrow of the whole local, `&I` a call-scoped pointer copy, `^I`
   a token copy.
4. Function types in member signatures (`fn each(f: fn(&Entry))`): grammar and `.sym` only.

Call-scoped behaviour is a lambda (`fn(...)`, second-class, pure); stored or escaping behaviour is an owning
interface value (the box is the heap cell with a destructor an owned closure would need). If function types ever
gain `mut &T` parameters they carry the same two tiers as members.

### 10.3 Lambda-to-interface conversion (S5), constraints only

Not part of 0.8, 0.9 or 0.10. If a lambda literal at a typed destination whose type is a one-member interface
later becomes an anonymous record plus a synthesised `impl`, boxed as `I`, it must keep:

- **Borrowed captures cannot escape.** Records, arrays, optionals and `Result`s are captured as read-only
  borrows and `&T` / `&I` as pointer copies into the creator's frame; a boxed environment outlives that frame,
  so the conversion is refused if any capture is a borrow or a function value.
- **Allowed captures:** scalar snapshots, handle tokens (including `^I`), strings (reference count incremented;
  listed as strings in the environment descriptor), and explicitly moved owned values. The mode is explicit at
  the literal (for example `fn [move x]`); a plain capture never turns into a move because the destination is an
  interface.
- **Owned captures are read-only in the body** (`&T`, or `mut &T` for a `mut fn` member): the box may be called
  many times, so the body never moves one out.
- **The environment record's descriptor** lists owned captures as owned fields and strings as strings, so
  `rt_free` on the box releases them.

---

## 11. Diagnostics

Style as LANGUAGE.md §10: lower case, single quotes around names, no trailing period, types through `tname`;
interface members as `I::m` (`M::I::m` when the module is not current); labels `argument N of f` for a function
and `argument N of interface method 'I::m'` for a member. "Existing" means the seed's text, reused. Existing
messages reused unchanged: `mut needs a reference parameter: write mut name: &T` (reader: `... write mut p0:
&T`) for `mut p: I`; `'I' is not a record type`; `handles name records and closed types, not I` (before 2a);
`array elements of type I would start as nil; ...`; `T needs a value for d (it cannot be nil)`; `'d' of type I
needs an initial value (it cannot be nil)`; `take needs an optional variable, field or element holding an owned
value`; `drop needs a local variable holding an owned value`; `cannot move out of a reference: a reference only
borrows`; `store this value in a variable first: a reference cannot borrow from a temporary`; `'x' may be nil:
check it with '!= nil' first`; `argument N of f needs a mut reference, but this one is read-only`; `'d' may
point to freed memory...`; `cannot point 'k' elsewhere while a defer uses it: ...`.

**New: declarations, implementations, conversions**

| situation | message |
|---|---|
| empty / duplicate / order / `pub` / forward shell | `an interface needs at least one method`; `interface 'I' already has method 'm'`; `'pure' and 'mut' come before 'fn'`; `'pub' is not allowed on an interface member; mark the interface type pub`; `an interface cannot be forward-declared: 'I' is already declared` |
| missing name; function type in a member | `expected a method name in the interface`; `an interface method cannot use a function value; function types are not supported in interfaces` |
| `&?I` local | `an optional interface reference can only be a parameter or a walk variable; narrow it to a '&I' local` |
| receiver not record/closed | `an interface can only be implemented by a record or closed type, not <t>` |
| foreign receiver | `cannot implement an interface for imported type '<M>::<T>'; declare the implementation in the type's own module` |
| variant receiver | `cannot implement an interface for variant '<V>'; declare it on its closed type '<C>'` |
| incomplete interface or receiver | `type '<T>' is declared but not yet defined` |
| duplicate pair | `type '<T>' already implements interface '<I>'` |
| interface target not an interface | `'X' is not an interface type` |
| missing method / not yet defined | `type 'T' has no method 'm' to implement interface 'I'` / `method 'T::m' must be defined before the implementation of interface 'I'` |
| signature mismatch | `method 'T::m' does not match interface 'I' method 'm': <detail>`: `the receiver must be 'mut &T'`, `it takes N argument(s), not M`, `argument N must be <t>, not <t>`, `argument N must be '<mode>'` (`'I'`, `'&I'`, `'mut &I'`), `it must return <t>, not <t>` |
| impure for a `pure` member | `method 'T::m' is not pure, but interface 'I' declares 'm' pure` |
| local tier | `method 'T::m' cannot implement 'I::m': ` then one of `it writes an owned value through a handle, or calls a function that is not defined yet`, `it frees global 'g'`, `it might free anything, because it calls a function that is not compiled yet; define that function first`, `it frees below a parameter that the member does not declare mut`; the first two continue `; interface methods may only change what is below their own mut parameters` |
| borrow as owned | `cannot use a borrowed 'T' as owned interface 'I': move the value in, or take the interface as '&I'` |
| `?T` source | `cannot use ?T as interface 'I' (it may be nil: take it into a local, return if nil, then convert the local)` |
| other sources | `cannot use interface 'I2' as 'I': interface values do not convert`; `cannot use <t> as interface 'I'`; `cannot use ^T as interface 'I'` |
| no visible impl | `type 'T' does not implement interface 'I'`; `module 'M' has no exported implementation of 'I' for 'T' (only 'pub' implementations are visible)` |
| `ok(...)` | `return value: cannot use Result[T] as Result[I]; store it first: let s: I = ...; return ok(s)` |
| cell in a global initializer | `cannot create a borrowed interface value in a global initializer: pass the concrete value to a function that makes it` |
| interface method call in a global initializer | `an interface method cannot be called in a global initializer; call a function that does it, as in var G = initG()` |
| `&I` or `&?I` returned, or stored in a field, element or global | the existing `&T` rule, unchanged: `references cannot be global` for a global (the compiler's declaration check). LANGUAGE.md §10 has no separate text for a return, field or element; LANGUAGE.md §4 states the rule: `References are for parameters and local variables only. They cannot be stored in fields, elements or globals, and cannot be returned.` |
| equality | `interface values cannot be compared; test an optional with '== nil' or '!= nil'` |

(implementation, 2026-10-30) The first local-tier text is the merged `writesHandles` text. `afterCall` sets `writesHandles` for a call to a function that is not defined yet, so a method that makes such a call is refused with `it writes an owned value through a handle, or calls a function that is not defined yet`, not with a separate text. The `freesAll` text stays for the forged-input case: a `.sym` that marks a library function `freesall` without `writeshandles`. The check comes after the `writesHandles` check, so an honest input never reaches it.

**New: calls and handles**

| situation | message |
|---|---|
| unknown member / used as a value / field-style | `interface 'I' has no method 'm'` / `interface method 'I::m' cannot be used as a value: call it with '(' after its name` / `interface 'I' has no fields; it has methods only` |
| arity / argument type | `interface method 'I::m' takes N argument(s), not M`; `argument N of interface method 'I::m': cannot use <t> as <t>` |
| `mut` member, read-only receiver | `the receiver of interface method 'I::m' needs a mut reference, but this one is read-only` |
| member that may free, through a handle | `cannot pass a reference that borrows through a handle to interface method 'I::m': it might free what the handle names` |
| global receiver it might free | `cannot call interface method 'I::m' on a reference into global 'g': it might free it` |
| later argument frees the receiver | `argument N of interface method 'I::m' may free what the receiver points into, and runs after it; compute it before the call` |
| mut alias with the receiver | `argument N of interface method 'I::m' may point inside the receiver, which is mut` / `the receiver of interface method 'I::m' may point inside argument N, which is mut` |
| `handle` on an `I`, `&I` or `?I` value, 0.8 (no `^I`) | `handles name records and closed types, not I` (existing) |
| phase 2a: `handle` of a borrowed `I` (S1) / of `?I` | `cannot make a handle from a borrowed interface value; make it from the place that owns it` / `handle needs a non-optional owned interface place, not ?I` |

Lambda firewall: the indirect-hit messages of 10.1, and the forward-shell message of 10.1. Symbol file (the 0.7 §8.4 rows that v3 relies on, exact text): `duplicate implementation of 'I' for 'T'`; `'X' is not an interface type`; `the receiver of an implementation must be a type defined in this module`; `an implementation's receiver cannot be a variant; use its closed type`; an alias receiver spelling: `the receiver of an implementation must be the defining name of a type in this module`; `type 'I' is declared but not yet defined`; `expected a method name in the interface`; `duplicate interface method 'm'`; `an interface needs at least one method`; a live/file disagreement: `symbol file '<p>' disagrees with the compiled interface of '<m>' for <decl>; rebuild it`, where `<decl>` is `interface 'I'`, `interface method 'I::m'`, `implementation 'I for T'` or `the implementation order`; plus the stale-file
lines `has format <n>; this compiler reads 4; rebuild it`, `has ABI 9; this compiler uses 1; rebuild it`, `was
built by compiler '9.9'; this is '0.8'; rebuild it`.

---

## 12. Tests and mutants

Every case is written with the change; no existing assertion is deleted or relaxed. Paths follow the tree:
`tests/run/*.obx`, `tests/errors/*.obx` (`// === name` blocks, `// error: text`),
`tests/modules/{run,errors,sym}`. All run tests execute under `OBO_CHECK=1` (the leak and double-free oracle).

### 12.1 Run tests

- **R1 `ifaces_basic`:** factory returning `Shape`; owned mixed list via `append(shapes, new T{...})`;
  `shapes[i].grow(k)` (deep receiver, local member); `describe(&c)`; `total(&[]Shape)`; `Result[Shape]` with
  `ok(s)`/`try`.
- **R2 `ifaces_b1_loop` (B1):** a `&I` conversion site inside a loop and a `best: &I` saved from an earlier
  pass; the printed element is the saved one, not the last. Also `best = cur` and a walk variable saved across
  passes.
- **R3 `ifaces_ownership`:** `let e = d` moves; a by-value parameter consumes; assigning an `I` field frees old
  box and target; `take` on `?I`; `replace` on a non-optional `I` field, element and global; `drop`; `truncate`;
  nested boxes (an object owning `I` fields); `Result[I]`; global `I` / `?I` teardown.
- **R4 `ifaces_variants`:** closed-type variants to `I`, `?I`, `&I` (parent vtable); `T → ?I`; `nil → ?I`; the
  5.3 idiom in its three shapes (early return, `take` into a local, `else { drop(t) }`).
- **R5 `ifaces_calls`:** every receiver form of 7.1; a member with an owned argument; a member returning `I`;
  recursion through boxes; `&?I` parameter narrowing and a walk variable over `[]?I`; `mut d: &I` calling `mut`
  members.
- **R6 `ifaces_refs`:** `&I` locals (`let d: &I = &c`, re-point, join, loop re-point); a defer using a `&I`
  unchanged since declaration (accepted).
- **R7 `ifaces_global_init`:** `var G: Shape = new Circle{...}`, `var O: ?Shape`, `var N = areaOf(G)`, a moved owned `I` global. An interface method call in an initializer is refused (E10), which removes the risk of section 14 item 6.
- **R8 `ifaces_handles` (phase 2a):** `handle(shapes[1])`, `alive`, a `pure mut` write through `^Shape`, exit 7
  after the box is replaced.
- **R9 `ifaces_homes`:** an interface-heavy program in `homes_check` (both register modes) and `port_compare`,
  identical output.
- **M-R `modules/run/iface_*`:** cross-module dispatch; a downstream module implementing a dependency's
  interface; transitive access; a `pub impl`.

### 12.2 Error tests

- **E1 `ifaces_decl`:** every declaration row of section 11; `mut p: I` in a function, a method receiver and a
  member.
- **E2 `ifaces_conv`:** borrow as owned; **S2** `describe(new Circle{...})` and `describe(make())` to `&I`; `&I
  → I`; the `?T` refusal and the idiom's two failure forms (no `else`; narrowing a field); `ok(new T)` hint;
  `^T`; no impl (local, foreign, variant); `nil → I`; the global-initializer cell refusal.
- **E3 `ifaces_eq`:** `I == I`, `?I == ?I`, `?I == I`, `I == nil` refused; `?I != nil` accepted.
- **E4 `ifaces_pos`:** `new I`, `const`, `new []I(3)`, missing field, no initializer, `match`, `println`,
  `alive`, `drop(&I)`, `&?I` local.
- **E5 `ifaces_tiers`:** the impl check: writes through a handle; frees a global; calls a forward heading; a
  `pure` member with an impure impl.
- **E-impl-mismatch:** a method `fn (c: &Circle) grow(k: int)` implementing member `mut fn grow(k: int)` is refused with the 0.7 mismatch text (section 11): `method 'Circle::grow' does not match interface '<I>' method 'grow': the receiver must be 'mut &Circle'`.
- **E6 `ifaces_calls`:** `mut` member on a read-only receiver; **a handle receiver with a `mut fn` member
  (refused) versus a `pure mut` member (accepted)**; deep receiver with an argument that frees it; global
  receiver; mut alias; unknown member, used as a value, field-style, arity, argument type. Phase 2a: a `^I` receiver whose later argument frees the box (9.1).
- **E7 `ifaces_refs`:** stale `&I` after the owner is reassigned and after `drop`; `&I` stored or returned; loop
  invalidation; defer re-point (F4).
- **E8 `ifaces_handle` (phase 2a):** **S1** `handle(d)` with `d: &I`, `mut d: &I` and a walk variable; `handle` of `?I` (the refusal text of 9.1).
- **E8a `ifaces_handle` (0.8):** `handle(d)` with `d: Shape` gives `handles name records and closed types, not Shape`.
- **E9 (in the lambdas error files):** `fn(&Holder)`, a captured `Holder`, an alias, a variant holder, a
  recursive record (terminates); the 10.1 messages.
- **E9a (forward shell, in the lambdas error files):** `type Holder` declared, then `fn visit(f: fn(&Holder): bool)`, then `type Holder = ref record { d: Shape }`; expects `a function value cannot take or return 'Holder': it is not yet defined; define it before this function type` at the `fn visit` line.
- **E10 `ifaces_global_init`:** `var G: Shape = new Circle{...}` then `var A = G.area()` is refused with `an interface method cannot be called in a global initializer; call a function that does it, as in var G = initG()`.

### 12.3 Module and `.sym` tests

- **S-1 `modules/sym/iface_sig`** (the member-signature fixture, replacing v2 §7.4 with the v3 spellings).
  `lib.obx` exports an interface whose members cover, in one declaration order (a sibling `dep.obx` exports
  `T2`, so the writer has a real foreign type to qualify as `dep::T2`): `pure fn m0(p0: &T, p1: int): T`; `pure
  mut fn m1(mut p0: &T)`; `pure fn m2(p0: &I, mut p1: &I): string` (borrowed read-only and mutable interface
  parameters); `fn m3(p0: ?T, p1: []T): Result[int]`; `fn m4(p0: dep::T2): dep::T2`; `pure fn m5(p0: &I): I`
  (self-referential borrow, owned interface return); `fn m6(p0: ?I, p1: []I): ?I` (owned, mode 0); `fn m7(p0:
  I): Result[I]` (owned interface parameter); `fn m8(p0: &?I)` and `fn m9(mut p0: &?I)`; a no-parameter `fn
  m10()`, and a `mut`/`pure` combination for each receiver mode. `main.obx` converts a borrow and an owned value
  and calls them; `symdir/lib.sym` and `symdir/main.sym` are committed; `--check main` exits 0; `--reemit-sym`
  is byte-identical; a full build re-reads the file in mode 0 and compares every member, so a writer that drops
  `mut`, drops `&`, changes a mode or a return type, or reorders members fails.
- **S-2 `modules/sym/iface_tier` (reconstruction).** One library with members `pure fn`, `pure mut fn`, `fn`,
  `mut fn`, one with a `mut p: &T` parameter and one with a `mut p: &I` parameter, with matching impls. The call
  matrix of 7.4 is compiled (a) from source and (b) with the library source removed and only `lib.sym` present;
  exit codes and stderr must be identical, and `--reemit-sym` byte-identical.
- **S-3 `modules/sym/forged_writeshandles`** (kept from 0.7 §7.5): a hand-written `lib.sym` declaring `f(p0: &I)
  writeshandles` with no `freesall`; `main` passes it a `&I` that borrows through a handle; `--check` must exit
  1 with the handle-borrow error. It pins the existing `pureFn` / `writesHandles` trust boundary for ordinary
  functions (member summaries cannot be forged; they are rebuilt).
- **S-4 versions:** the migrated `format` / `compiler` / `version` fixtures and goldens of 8.3, and a committed
  stale `obosym 3` / `compiler "0.7"` file.
- **S-5 positions:** a `.sym` with an interface field, array, global, `Result`, optional and return (accepted);
  `mut p0: I` (rejected with the reader text); `^I` (rejected before 2a); a duplicate member; a bad marker
  order.
- **S-6 impl lines:** duplicate, foreign receiver, variant receiver, alias spelling (0.7 §9.3), and in
  `modules/errors` a private `impl` that is invisible to importers with identical text in full and `--check`
  builds.
- `RICH_LIB` / `RICH_MAIN` in `sym_test.py` gain the S-1 signatures, a private `impl` **explicitly declared**,
  and a source-less check.

### 12.4 Mutants

Each has a named catcher; none is accepted as equivalent without a recorded reason. Counts are recorded at
implementation.

- **Conversion and ownership (R1-R4, E2):** X1 box descriptor owns nothing; X2 conversion does not consume the
  source; X3 variant boxed with its own vtable instead of the parent's; X4 `T → ?I` skips the widening or `nil →
  ?I` allocates; X9 (S2) a temporary may borrow into a cell; X35 `replace` on an `I` field does not return or
  free correctly.
- **`&I` (R2, R6, E7, E8):** X5 (B1) a `&I` local aliases the per-site cell; X6 re-point stores a pointer, not a
  pair copy; X7 `let` copies `obj` but not `vtable`; X10 (S1, phase 2a) `handle` accepts `&I`; X11 a `&I` local is not
  registered as a reference local (F4).
- **Dispatch (R5, E6):** X8 (S4) `obj` or `vtable` loaded after the arguments.
- **Tiers (E5, R1, R5):** X12-X14 the impl check drops `!writesHandles`, `!freesAll` or the freed-globals
  conjunct; X15 it drops `pfree ⊆ S(m)` (caught by a forged-method module test and E5); X16 a `pure` member
  checked with the local tier; X17 a local member checked with the pure tier; X38 `implDecl` skips the receiver-mode / parameter-mode / return-type comparison between a member and its method (E-impl-mismatch).
- **Reconstruction (S-2):** X18 receiver bit omitted for `mut fn`; X19 bits on mode-1 instead of mode-2; X20
  only the first mode-2 parameter; X21 `freesAll` or `writesHandles` set (valid calls rejected); X22 a `pure
  mut` member gets the receiver bit; X23 reader ignores `pure`; X24 parser and reader derive differently (reader
  skips indices >= 2).
- **Encoding (S-1, S-2, S-5, E1):** X25 writer restores the 0.7 `kind != "iface"` exemption; X26 writer drops
  `mut` on a member parameter; X27 writer swaps two modes, drops a return type or reorders members; X28 reader
  parses `pN: I` as mode 1; X29 reader accepts `mut pN: I`; X30 mode-0 comparison ignores parameter mode; X31
  the source parser accepts `mut p: I`.
- **Other rules:** X32 `relation` lets `I == I` or `?I == ?I` through (E3); X33 a `&?I` local is accepted (E4);
  X34 a cell is built in a global initializer (E2); X36 `OBO_VERSION` stays `0.6` or `SYM_FORMAT` stays 2, instead of becoming `0.8` / 4 (S-4);
  X37 `containsIface` does not recurse into records, or drops its guard (E9).

---

## 13. Implementation steps and reuse

Round 1 keeps `obo.obo` free of interface syntax, so the 0.6 seed builds the 0.8 compiler. The feature branch
does not touch `seed/obo`; one promotion follows review. One feature in flight.

1. **Data model, declarations, impls.** The `iface` type kind; `isOwned` and `nonNull` true for it; `same` by
   identity; the member parameter parser (rule 3.2); the new summary helper (7.4); `implDecl` with the tier
   check (7.2). Compile-only; E1, E5.
2. **`.sym` and the version bump.** Writer (delete the `kind != "iface"` exemption), reader, mode-0 comparison,
   `OBO_VERSION "0.8"` / `SYM_FORMAT 4`, fixture migration (8.3). Format checkpoint: S-1, S-2, S-4, S-5.
3. **Box and owned conversion.** The box descriptor; the typed-destination step (section 5) at the `checkAssign`
   call sites; moves, variants, `?I`, `ok` hint, the `?T` refusal. R1 (owned part), R3, R4, E2.
4. **Calls and dispatch.** Interface receivers on the direct-method path (7.1); S4 load order; the caller's use
   of the synthetic summary. R5, E6.
5. **`&I`.** Frame cells for arguments, per-local pair slots (B1), borrow of an `I` place, `&I` locals and walk
   variables registered as reference locals, `&?I` parameters and walk variables. R2, R6, E7, E4.
6. **Equality, `handle` refusal, global initializer.** `relation`, `handle` (the 0.8 refusal of section 11; the S1 rule waits for phase 2a, section 9.1), `varDecl` init-chain rule (the call refusal). R7, E3, E8a, E10.
7. **Docs and gates.** LANGUAGE.md section on interfaces, README phase row, FORK record; mutants; all gates, named as in the lambda note's 10.9: `run_fork.py` (run, errors, module trees, sym tests, own-fuzz), `module_test.py`, `sym_test.py` (golden compare, no `--update`), `sym_fuzz.py`, `doc_check.py`, `homes_check.py` and `homes_edge.py` (the homes and no-registers checks, R9), `elf_test.py`, `io_test.py`, `meta_test.py`; `port_test.py`, `port_compare.py`, `mutate_fork.py`, `mutate_port.py`, `mutate_io.py`; `imports.py`, `stdlib.py`, `tools.py` and `eza.py` unchanged and green; the self-hosting fixed point (stage2 == stage3); the peak within budget;
   peak measurement (14.5); review; merge; one seed promotion to 0.8. `^I` 2a is a later change after lambdas
   Stage B.

**Reused from the paused 0.7 branch** (read-only; names as in that branch): the `Impl` record, `ifaceSyms`,
`impls`, `ifaceMethodsOf`, `findIfaceMethod`, `findImplFor`, `implMissing`, `implDecl` (its `handleSafe`
conjunct replaced by the tier check), `ifaceDecl` with the factored `parseParams`, `finishTypeName` /
`symFinishTypeName` (the `::`-first order of 0.7 §2.1), `appIface`, `appImpl`, `symIfaceDef`, `symImplDecl`,
`symHasShell`, and `ifaceCell` as the `&I` frame cell (holding `obj`, not a token). **Rewritten:** `ifaceValue`
(becomes the table of 5.1), `ifaceCall` (becomes the direct-method path plus S4), `appIfaceParams`, `ifacePure`
/ `handleSafe` (replaced by 7.2). **New:** the box descriptor, `isOwned` / `nonNull` for the kind, owned
conversion, per-local pair slots, the summary helper, the `relation` and `handle` refusals, the `&?I` rules.
**Dropped from 0.7/v2:** the token cell and `RT_HANDLE` at conversion, second-class-only positions, the
bound/unrooted provenance category, the owned-argument rule, the `ownsCell` / `isOwned` split.

---

## 14. Risks

1. **Conversion and move paths.** Every typed destination must move or box exactly once. The leak oracle on all
   run tests, including variants, nested boxes, `Result[I]`, globals, `truncate`.
2. **Local-tier soundness.** The impl check must reject `writesHandles`, freed globals, `freesAll` and pfree on
   a non-`mut` parameter, against the summary in 7.4. The mutants X12-X24 pin each conjunct and each bit.
   Importers trust the declaration; the defining module checks it (the existing trust model).
3. **Frame-cell `&I`.** It must never escape; B1's loop test (R2) and the reference rules (6.5) are the guard.
4. **Dispatch codegen.** Two loads plus an indirect call on the existing method-call path; run the homes /
   no-registers checks on an interface-heavy program (R9).
5. **Memory budget.** Not raised. Current figures on main (`PERFORMANCE.md`, `FORK.md`): the compiler compiling
   itself peaks at **1,728,768** bytes live against `PEAK_BUDGET` 2,000,000 in `tests/port_compare.py`,
   **271,232 spare**. The paused 0.7 branch, with 12,175 source lines, measured **1,907,280 live, 92,720 spare**
   (its `FORK.md`). The "about 92 KB" figure in the independent assessment is that branch's, not main's. v3 adds
   conversion, per-local slots and the `&?I` rules on top of the 0.7 code, and lambdas Stage A and B add more
   afterwards, so the margin could fall below 92 KB. Measure at the step-7 checkpoint and after each lambdas
   stage; if the budget is exceeded, stop and report to the user, do not raise it.
6. **Interface calls in a global initializer.** Refused in 0.8: an interface method call anywhere in a global initializer is an error (section 5.4, E10). The init chain has no frame, so it has no slot to hold the `vtable` across the arguments. The risk is removed by the refusal; the workaround is a function that does the call. R7 tests what stays allowed (owned conversions, the `&I` place borrow, the moved `I` global).
7. **`reaches()` is not type-aware**: `&Shape` and `&Circle` parameters count as possibly aliasing, so some
   calls are falsely rejected, never wrongly accepted.
8. **Lambdas amendment.** The lambda implementation must apply 10.1.

---

## 15. Open questions

None blocks implementation. Three defaults the user may revisit, each cheap to change later because it only
widens acceptance:

1. `?T → ?I` is refused with an idiom (5.3); a consuming conversion can be added later.
2. No impure member tier (7.5).
3. `&?I` is a parameter and walk-variable type only (6.4).

---

## Appendix A. Review findings and their resolutions

**Owning-design review** (folded in, carried forward):

| id | finding | resolution | section |
|---|---|---|---|
| B1 | a `&I` saved from an earlier loop pass follows a rewritten per-site cell | each `&I` local owns its pair slot; `let`/`var`/re-point copy; parameters point at the caller's pair; per-site cells for call arguments only | 4, R2, X5-X7 |
| S1 | `handle(d)` with `d: &I` computes a token from a stack address | resolved, effective in phase 2a (`handle` only on an owned `I` place; walk variables refused); in 0.8 the existing error applies | 9.1, E8, E8a |
| S2 | owned temporaries borrowed into `&I` would leak | the existing store-first error | 5.1, E2 |
| S3 | `T → ?I`, variants, `ok(...)` unspecified | specified | 5.2 |
| S4 | load order of `obj`/`vtable` unspecified | both loaded before the arguments | 4 |
| S5 | lambda-to-interface captures could escape or double-move | constraints | 10.3 |
| S6 | per-implementation summary would be unsound | per member | 7.3 |

**Latest independent assessment:**

| # | finding | resolution | section |
|---|---|---|---|
| 1 | version | `0.8` / format 4 / ABI 1; lambdas `0.9` / `0.10` / format 5; every fixture statement updated | 8.2, 8.3 |
| 2 | local-tier summary from `.sym` | one helper builds it from markers and modes in both paths; test S-2; mutants X18-X24 | 7.4, 12.3 |
| 3 | modes and encoding | `I` mode 0, `&I` 1, `mut p: &I` 2, `mut p: I` refused (existing text, probed); writer exemption deleted; reader and mode-0 comparison; fixture list rewritten | 3.2, 12.3 S-1 |
| 4 | `take` vs `replace` | `take` optional only, `replace` for non-optional fields, elements, globals; probed | 6.1 |
| 5 | `?T → ?I` | idiom verified on the seed (narrowed local moved to a typed local; early exit or `else { drop(t) }`); no new conversion | 5.3 |
| 6 | lambda firewall | option (a): recurse into record and closed fields with a visited list; the earlier "firewall holds" claim was wrong | 10.1 |
| 7a | `^I` shared sinks | narrowed: `pure mut` writes only, else go through the owner | 9.1 |
| 7b | `&I` in a global initializer | cell-needing conversions refused; owned conversions and `&` of an `I` place allowed | 5.4 |
| 7c | equality | refused explicitly (the seed accepts record identity); only `== nil` / `!= nil` | 6.3 |
| 7d | `&?I` | parameter and walk variable, following `&?T`; locals refused | 6.4 |
| 7e | memory budget | recorded as a risk with verified numbers; not raised | 14.5 |
| 7f | test inventory | explicit | 12 |
| 7g | cycle freedom | stated as an invariant | 2 |
| 7h | box size | 16-byte payload, 32 bytes allocated (header 16, `alloc_raw` granule 16) | 4 |

**v2-note findings (do not apply to owning; why v3 is immune):**

| id | why immune |
|---|---|
| F1 `take`/`replace` hands out a frame address | `I` places hold heap boxes; `&I` locals and parameters cannot be taken from or replaced (`replace cannot change 'r': it is immutable`) |
| F2 `replace` plus in-place cell overwrite | there is no cell overwrite: assignment to an `I` place allocates a new box and frees the old; `replace(xs[i], xs[i])` is a move out of an element, which is refused |
| F3 cell frees not counted | box frees are frees of an `isOwned` place, recorded like any owned free; `&I` cells are never freed |
| F4 `var` view re-pointed around `defer`/loops | `&I` is a reference local (6.5): the defer re-point rule and loop rules apply; X11 pins the registration |
| F5 cleanup of returned cells | an `I` return is an owned temporary under the existing store-first and move rules; there are no returned cells |

**Wrong or stale claims found in the earlier notes** (beyond the listed findings): the owning note's §9.1 /
§13.2 firewall claim (10.1); its version statements (0.7 / format 3, lambdas 0.8 / 0.9 / format 4); its "a
field, element or global needs `take`" (6.1); its `?T → ?I` snippet `let s: I = take(o)` (5.3); and
`lambdas-design.md` §6.5's statement that `sym_fuzz.py`'s `change_version` hard-codes the next format (it
derives it from the file; the text was only renumbered, not otherwise changed). The 0.7 note's fixture count of
76 `.sym` files is stale (88 at this baseline).

**Final review (2026-10-30)**

| id | finding | resolution | section |
|---|---|---|---|
| A1 | `containsIface` cannot see the fields of a forward shell (`type Holder` declared, then `fn visit(f: fn(&Holder): bool)`, then the definition), so a function type over it passes the firewall | an incomplete forward shell makes `containsIface` return true; a function value over it is refused with `a function value cannot take or return '<T>': it is not yet defined; define it before this function type`; test E9a | 10.1, 11, 12.2 |
| A2 | a visited list left over from an earlier query could make a recursive type return false | the list is truncated at the start of every top-level `containsIface` call; only the recursive helper appends | 10.1 |
| A3 | the S1 handle refusal (E8, X10) was listed as 0.8 work, though the rule belongs to phase 2a | in 0.8, `handle` on any `I`, `&I` or `?I` gives the existing `handles name records and closed types, not I`; the S1 refusal, E8 and X10 are marked phase 2a; new 0.8 test E8a | 9.1, 11, 12.2, 12.4, 13 step 6 |
| A4 | 5.4 and 14.6 contradicted each other on interface calls in global initializers, and left a vtable-hold option open | 0.8 refuses any interface method call in a global initializer (`an interface method cannot be called in a global initializer; ...`); the vtable-hold option is removed; test E10; R7 and the risk note updated | 5.4, 11, 12.1 R7, 12.2 E10, 14 item 6 |
| A5 | the header claimed that a reader needed neither older note; diagnostics cited 0.7 sections without their text; the vtable layout was implicit | the header states what v3 is complete for and what it cites; every diagnostic it relies on is quoted from the 0.7 §8.2 and §8.4 tables; vtable layout stated in section 4 | header, 4, 11 |
| A6 | whether the reader re-runs the tier check on `impl` lines | it does not: the defining module checked it (the `.sym` trust model, LANGUAGE.md §6); full builds and `--check` agree because both rebuild summaries from the markers | 8.1 |
| A7 | nits (a)-(h): X36 named the wrong baseline (3 / 0.7); the field-narrowing probe did not say which case it covered; no mismatch mutant or test; walk-variable slot rule contradicted between sections; gates unnamed; no return or store diagnostics; no 2a dispatch test note; no appendix rows for this review | (a) X36 names `0.6` / 2; (b) the `may be nil` text is for an owned `h`, with `mut h: &H` the seed reports the move-out text; (c) X38 and E-impl-mismatch; (d) a walk variable over `[]I` or `[]?I` is the element's box pointer, in 4 and 6.4; (e) gates named in 13 step 7; (f) return and store rows in 11; (g) 2a dispatch test note in 9.1; (h) this table | 12.4, 5.3, 3.3, 4, 6.4, 13, 11, 9.1 |

The earlier review's claim that the lambda firewall holds was wrong (10.1; listed with the other wrong claims above).
