# Owning interface values — an alternative value layer for interfaces

Status: **design only**, for review against
`obo/design/first-class-interfaces-design.md` (the "v2 note"). Baseline: `main`
at `638b2f0`, worktree `first-class-interfaces-design`. Nothing is
implemented; the only change is this file.

This note proposes a different **value layer** for interfaces. It keeps from
the v2 note and the paused 0.7 branch: the declaration grammar, nominal
identity, explicit `[pub] impl`, one vtable per `(type, interface)` in
declaration order, variants through the closed parent, no
interface-to-interface conversion, the symbol-file skeleton, the MEDIUM
member-signature fixture (v2 §7.4), the `handleSafe` fixture (v2 §7.5) and the
diagnostics-style fixes (v2 §9). It replaces v2 §3–§4.9, §5.3 and §6.3–§6.4.

---

## 0. Summary

1. **Interfaces get the three tiers records already have.** `I` is an
   **owning** value, `&I` / `mut &I` is a **borrowed** value, and `^I` is a
   **checked link**. Each tier uses the existing rules for `T`, `&T` and `^T`.
   No fourth kind of value is added.
2. **`I` is a box.** It is a one-word pointer to a 16-byte heap record
   `{obj, vtable}`. `obj` is an owned pointer to the target object. The box
   descriptor lists `obj` as its one owned field, so the existing `rt_free`
   frees the target together with the box. `isOwned(I)` is true. Moves,
   `take`, `replace`, `drop`, scope exit, fields, arrays, globals, `Result`
   payloads and returns all work as they do for records. The runtime does not
   change and the ABI stays 1.
3. **Converting into `I` moves the concrete value in**, the same way closed
   types already widen (`let s: Shape = new Circle{r: 2}`). For example,
   `append(shapes, new Circle{...})` and `fn make(): Shape { return new
   Circle{...} }` both work.
4. **`&I` is the 0.7 value layer under an honest name.** It is a pointer to a
   `{obj, vtable}` pair. The pair is either an `I` box (when borrowing an `I`
   place) or a frame cell around a borrowed concrete object (`describe(&c)`,
   with no allocation). `&I` follows the reference rules: parameters and
   locals only, invalidated by visible changes, never stored or returned.
5. **A call through `I` or `&I` is a method call on the box's object.** The
   receiver is argument 0 on the existing direct-method path, so the
   deep-receiver checks, holds and argument-order checks are the ones direct
   calls already use. The HIGH defect (v2 §0 item 1) came from a separate
   receiver path, and that path no longer exists. Dispatch needs no `RT_DEREF`:
   ownership and references keep the target alive.
6. **Interface members get a "local" effect tier as the default.** `pure fn`
   keeps its meaning. A plain `fn` / `mut fn` member promises **local
   effects**: it may free below its own `mut` reference parameters (including
   the receiver) and its owned parameters. It may not write owned values
   through a handle or free a global. Its synthetic summary is therefore
   precise rather than worst-case. As a result `shapes[i].grow()` and
   `opts.out.write(buf)` are accepted exactly as the direct calls
   `bag.items[0].bump()` and `opts.out.writeBuf(buf)` would be. An
   implementation method with global or handle effects cannot implement an
   interface member in v1 (section 6.3).
7. **`^I` comes second and starts as a handle to a box.** `handle(d)` on an
   `I` place gives an ordinary one-word `^I`, checked on use (exit 7), with
   the existing handle rules. A link to a concrete target that is not boxed
   (`^I` from `^Circle`) is the v2 note's `{token, vtable}` cell. It is
   deferred until a tool needs it (section 8).
8. **Lambdas fit without a Stage A revision.** Second-class `fn(...)` values
   play the role of the borrowed tier for code. Stored or escaping behaviour
   is an owning interface value. If an escaping closure is wanted later, the
   path is a Java-style lambda conversion to a one-member interface, which
   reuses the box. No separate owned function type is needed (section 9).
9. **The version stays at `0.7`, format 3, ABI 1**, for the v2 note's reasons
   (§7.6): 0.7 is unpromoted, the `.sym` grammar is unchanged and the box is
   internal.

---

## 1. Why change the v2 value layer

The v2 note's `I` is copyable and does not own its target. Without a GC,
such a value is a polymorphic `^T`, and the v2 rules say so. The target is
checked on every dispatch (exit 7). Stored and returned values are
"unrooted" and limited to pure members. A call through such a value cannot
take an owned argument, and a function cannot return a new object as `I`.
Every one of those rules is sound. Together they mean that the plain
spelling `I` behaves like a handle, while the patterns that users from Java
or Go reach for first need **ownership**:

| pattern | v2 note | this note |
|---|---|---|
| factory `fn make(): Shape { return new Circle{...} }` | compile error (definite escape) | works: moves into a box |
| owned mixed list `append(shapes, new Circle{...})` | refused (owned conversion); needs a separate owner for each object | works |
| `shapes[i].grow(k)` where `grow` frees below its receiver | refused (unrooted receiver, impure member) | works (local tier, deep receiver) |
| `opts.out.write(buf)` through a stored writer, owned `buf` | refused (owned-argument rule, and impure) | works |
| `describe(&c)` with `describe(d: &Shape)` | works | works, with no allocation |
| `let e = d` | copy; both remain usable | move, as for records; share through `&I` or `^I` |
| target freed while a stored value refers to it | exit 7 at the next call | impossible for `I`; exit 7 for `^I` |
| view of a local stored in a record that is later returned | exit 7 at runtime | cannot be written (`&I` cannot be stored) |
| keep a `Circle` field and also hold a polymorphic link to it | works (view) | `&I` within a call; stored only with `^I` from a concrete target (deferred) |
| new compiler concepts | a copyable non-owned cell, a bound/unrooted provenance category with joins, the owned-argument rule | none for `I` and `&I` (owned and reference rules); one member effect tier |

What the v2 note does better: copying is free, and you can hold a stored
polymorphic link to an object whose concrete type you also keep. That is the
last-but-one row, and it is exactly `^I` from a concrete target (section 8).
The v2 machinery is the right design for **that tier**. It should not be the
default meaning of `I`.

---

## 2. The model

| | owned | borrowed (second-class) | checked link (storable, non-owning) |
|---|---|---|---|
| records / closed types | `T` | `&T`, `mut &T` | `^T`, `?^T` |
| **interfaces** | **`I`, `?I`** | **`&I`, `mut &I`** | **`^I`, `?^I`** (phase 2) |
| functions (lambdas v1) | — (see section 9) | `fn(...)` values | — |

An interface type in each position behaves like a record type in the same
position. The only difference is that a member call is dispatched through a
vtable. Everything else is ownership, references and handles as described in
LANGUAGE.md §2–§5.

---

## 3. Representation

```text
I (owned box, heap)          &I from &d (d: I)       &I from &c (c: Circle)
+-------------+              pointer to d's box      frame cell (no alloc)
| obj    own  | --> target                           +-------------+
| vtable u64  |                                      | obj    ptr  | --> c (borrowed)
+-------------+                                      | vtable u64  |
                                                     +-------------+
```

- **Box.** A record with descriptor `[16][1][0][0]` (size 16, one owned
  offset at 0, no strings), emitted once per program. `RT_ALLOC_RECORD`
  allocates it. `rt_free(box)` pushes `obj`, and `obj` is then freed through
  its own header descriptor. This is the existing walk (`runtime.c`
  `rt_free`), so it also covers variant objects and targets that own further
  boxes. Ownership stays a tree, so there are no cycles and no reference
  counts.
- **`&I`.** A one-word pointer to a 16-byte `{obj, vtable}` pair. The callee
  cannot tell, and does not need to know, whether the pair is a box or a
  frame cell. It never frees through `&I`; re-pointing a reference local
  changes nothing (LANGUAGE.md §4). A frame cell lives in the converting
  function's frame, which is sound because `&I` cannot escape that frame.
  This is exactly the paused 0.7 `ifaceCell`.
- **Dispatch.** Load `obj` from `[p]` and `vtable` from `[p+8]`. Push `obj` as
  argument 0 (the method ABI's `&T` / `mut &T` receiver). Push the explicit
  arguments, then `call [vtable + 8*slot]`. There is no `RT_DEREF`. A `^I`
  receiver first dereferences the handle to reach the box, using the existing
  handle path.
- **`?I`** is 0 or a box pointer, the same as `?T`.
- **ABI.** Every position is one word, record layout is one word per field,
  array elements are 8 bytes (an owned element kind) and descriptors use the
  existing format. `SYM_ABI` stays 1.

---

## 4. Conversions

A conversion happens at a typed destination (`let`/`var` with a type,
assignment to a typed place, a parameter, a constructor field, `append`, a
return), as in v2 §4.2. `ok(x)` takes `x` as already typed.

| source | to `I` | to `&I` |
|---|---|---|
| owned record / closed / variant `T` that implements `I` (a local, `new T{...}`, a call result) | **moves** it into a new box | borrows it (frame cell), as passing an owned value to a `&T` parameter does |
| `&place` or a bare reference of such a `T` | `cannot use a borrow as owned interface 'I': move the value in, or take '&I'` | frame cell; the binding is the place's borrow binding |
| `I` (owned) | moves the box (no allocation) | borrows the box |
| `&I` | `cannot move out of a reference` (existing message) | copies the reference |
| `^T` (phase 2b) | refused: a handle does not own its object | borrowed through a handle; follows the handle borrow rules |
| `?T`, `?I` | narrow first (`'<x>' may be nil: check it with '!= nil' first`) | same |
| another interface `I2` | `cannot use interface 'I2' as 'I': interface values do not convert` | same |
| array, string, `Result`, scalar | `cannot use <t> as interface 'I'` (no `&` hint) | same |
| `nil` | to `?I` only | — |

The message ordering from v2 §4.2 is kept, so no source gets a dead-end hint.

---

## 5. Ownership rules

These are the record rules without exception. `let e = d` moves `d`. A
by-value parameter `d: I` consumes the argument. Assigning to an `I` field
frees the old box and its target. `take(o)` on a `?I` place leaves nil.
`replace(p, v)` returns the old box. `drop(d)` frees the box and its target.
Scope exit frees boxes owned by locals. Freeing a record frees its interface
fields. `truncate` and freeing an array free the element boxes. Freeing a
`Result[I]` frees the payload box. Global teardown frees global boxes. A
plain assignment cannot move out of a field, element or global (use `take`).
Move-on-one-path errors, loop rules and `defer` rules all apply unchanged.

Positions: fields `I` / `?I`, `[]I` / `[]?I` (with `new []I(n > 0)` refused
as for any non-null element), globals, `Result[I]`, and function, method and
member returns. `&I` is allowed for parameters and locals only. Never allowed:
`new I`, `const` of interface type, `==`, `match`, `println`, and `alive` on
`I` or `&I`. Use `alive` on a `^I`.

`mut d: &I` lets the callee call `mut` members. As with `mut &T`, it does
not let the callee replace the box. A callee that needs to replace an
interface field takes the record that holds it.

---

## 6. Calls and effects

### 6.1 Receivers

Any designator of type `I`, `&I` or (phase 2) `^I` can be a receiver: `d`,
`r.d`, `xs[i]`, `G`, `lib::G`, `h.d`, a narrowed `?I`, `r.value` in a
`Result` arm, and a `for` walk variable over `[]I`. These are exactly the
receiver forms of direct methods (LANGUAGE.md §5a "Calls"). The receiver is
the box's object, borrowed `&` or `mut &` according to the member's marker,
and it goes through the same argument-0 code as a direct method:
`borrowBind`, hold, alias and argument-order checks, deep-reference checks,
handle borrows and global checks. A call result of type `I` is an owned
temporary, so `make().m()` is the existing store-first error, as for records.

### 6.2 Member effect tiers

| member marker | the implementation must satisfy | synthetic call summary |
|---|---|---|
| `pure fn`, `pure mut fn` | `pureFn(m) && !m.writesHandles` (unchanged) | frees nothing reachable from the caller |
| `fn`, `mut fn` (**local**, the default) | `!m.writesHandles`, frees no global, not `freesAll`; may free below its `mut` reference parameters (the receiver for `mut fn`) and may free its owned parameters | `pfree` set on each `mut` reference parameter and, for `mut fn`, on the receiver; `freesAll` and `writesHandles` false; no globals freed |

The caller applies this summary exactly as it would a direct call's summary.
Consequences:

- **Deep receiver plus local member: accepted.** `shapes[i].grow(k)`
  invalidates references below `shapes[i]`, as `bag.items[0].reset()` would.
  The deep-receiver refusal (deep argument plus `writesHandles` or `freesAll`)
  can never trigger on an interface call, because no member summary has those
  bits. The v2 note's HIGH reproduction cannot be written: its `imp` assigns
  an owned field through a handle, so `impl D for C` is refused at the impl
  with `'C::imp' cannot implement 'D::imp': it writes an owned value through
  a handle; interface methods may only change what is below their own mut
  parameters`.
- **Handle-borrowed receivers** (`h.d.m()`, a `^I` receiver) are references
  borrowed through a handle. The existing rule accepts them only for calls
  that cannot free: pure members, and local members with neither a `mut`
  receiver nor `mut` parameters.
- **Global receivers**: `mightFree(f, g)` is false for every member, because
  no tier frees a global. A `mut` member on `G` frees below `G` with the
  existing pfree events.
- **Propagation.** A function that calls a local member gets that call's
  pfree events, as from a direct call. It does not become `freesAll`.

### 6.3 What the local tier costs

An implementation whose method writes owned values through a handle, or
frees a global, cannot implement a non-`pure` member in v1. Such a method is
still an ordinary method. It just cannot be called through an interface.
This is the trade for interface calls that are exact rather than worst-case.
If a tool needs such a member, the extension is a third marker (spelling
open, for example `impure fn`) whose summary is the v2 worst case. Deep,
handle and unrooted receivers would refuse it, as the v2 table does.
Recommendation: leave it out until a real use appears.

---

## 7. Worked example (shape only)

```obo
type Shape = interface {
    pure fn area(): int
    mut fn grow(k: int)                    // local: may free below its receiver
}
type Circle = ref record { r: int; log: []int }
type Square = ref record { s: int }

fn (c: &Circle) area(): int { return 3 * c.r * c.r }
fn (mut c: &Circle) grow(k: int) { c.r += k; c.log = new []int(0) }   // frees the old log
impl Shape for Circle
fn (q: &Square) area(): int { return q.s * q.s }
fn (mut q: &Square) grow(k: int) { q.s += k }
impl Shape for Square

fn make(kind: int): Shape {                // factory: the new object moves into a box
    if kind == 0 { return new Circle{r: 1, log: new []int(0)} }
    return new Square{s: 2}
}

fn total(xs: &[]Shape): int {              // borrowed walk, pure member
    var t = 0
    for s in xs { t += s.area() }
    return t
}

fn describe(d: &Shape): int { return d.area() }

fn main() {
    var shapes = new []Shape(0)
    append(shapes, make(0))
    append(shapes, new Square{s: 3})       // moves the square in
    shapes[0].grow(2)                      // deep receiver, local member: accepted
    println(total(shapes))
    let c = new Circle{r: 5, log: new []int(0)}
    println(describe(&c))                  // frame cell; c keeps its concrete type
    let h = handle(shapes[1])              // phase 2: ^Shape, checked on use
    println(h.area())
}
```

---

## 8. `^I` — phase 2

- **2a: a handle to a box.** `handle(place)` where `place : I` gives a plain
  `^I`. It is the existing one-word token naming the box, with no new
  representation. A use dereferences the box (exit 7 if it was freed), then
  dispatches. Replacing or freeing the box (field reassignment, `drop`,
  `truncate`) bumps its generation. `alive(h)`, `?^I`, storage in fields,
  arrays and globals, the handle-borrow rules and the owner-tracking rules for
  `let` handles (LANGUAGE.md §5) all apply unchanged. This covers observers,
  back-links, registries and shared sinks over boxed objects.
- **2b: a link to a concrete target** (`^I` from `^Circle` or `handle(c)` with
  `c: Circle`). The pair has to carry the concrete target's token and the
  `(Circle, I)` vtable, which is the v2 note's `{token, vtable}` cell. If 2b
  is adopted, 2a uses the same cell with the token of the box's object.
  Freeing the box frees that object, so the semantics are the same. Defer 2b
  until a tool needs a stored polymorphic link to an object that must also
  keep its concrete type.

---

## 9. Fit with lambdas and the rest of the language

### 9.1 Lambdas v1 need no change

The lambdas design (`obo/design/lambdas-design.md`) makes every function value
second-class (parameters, locals, arguments), its captures read-only borrows
or snapshots scoped to the call, and every lambda body pure. That is the
**borrowed tier for code**, and it lines up with `&I`. Neither can be stored
or returned, and both are sound because the creator's frame is live. Stage A
and Stage B can proceed as written. The interface firewall in lambdas §12
still holds for v1.

### 9.2 The firewall becomes cheap to lift, in this order

Each of these is now a reuse of an existing rule rather than a
cross-feature contract.

1. **`&I` in function types.** `fn(&Shape, &Shape): int` is a read-only
   reference parameter, like `fn(&Item, &Item): int`. A lambda body is pure.
   With the tiers in 6.2, a read-only `&I` can call `pure` members, and local
   members without `mut` parameters. So comparators and predicates over
   interface values work, and no provenance rule is needed:

   ```obo
   sortIndices(&order, &shapes, fn (a: &Shape, b: &Shape): int { return a.area() - b.area() })
   ```

   (Without generics, this needs a `sortIndices` over `&[]Shape`.)
2. **Owned `I` in function types and lambda returns.** `fn(): Shape` is a
   factory callback. Lambdas may already return owned values; a box is one.
   Allocating is not a free, so the body stays pure.
3. **Captures.** An owned `I` local is captured as a read-only borrow of the
   whole local (the record rule: store the box pointer, and the body sees
   `&I`). A `&I` is captured as a reference copy. A `^I` is captured as a
   token copy (the handle rule). The lambdas §4.2 reason for rejecting
   interface captures ("the cell/token representation is still settling") no
   longer applies. The capture table gains no new row, because each
   interface tier uses its tier's row.
4. **Function types in member signatures** (`fn each(f: fn(&Entry))`). This
   needs grammar and `.sym` changes only. A member that calls a pure function
   value stays in its tier.

### 9.3 Escaping behaviour is an interface, not an owned function type

Lambdas §14.3 lists "owned/escaping closures" as an extension needing "a
heap cell with a destructor". The box already is one. Instead of adding a
third value kind for code, the recommended division of labour is:

- **call-scoped behaviour** uses a lambda (`fn(...)`, second-class, pure,
  cheap);
- **stored behaviour** uses an owning interface value (a record with state
  plus an `impl`), which can be stored, returned, moved and dropped.

The later ergonomic step is Java's: a lambda literal at a typed destination
whose type is a **one-member interface** becomes an anonymous record holding
the moved (owned) captures plus a synthesised `impl`, boxed as `I`. The
environment record's descriptor frees the owned captures, and `rt_free` on
the box frees the environment, so owned captures get their destructor for
free. This answers lambdas §4.7 (why owned captures are deferred) without a
call-scoped destructor convention. Method values bound to an owned receiver
fall out the same way. This is a separate design after both features land.
It is recorded here so that neither feature closes the door: Stage A's
`fn(...)` spelling should stay the second-class type, and owned callbacks
should be spelled as interfaces.

### 9.4 One effect vocabulary

`pure` currently has three uses: interface members, the lambda body rule, and
handle-safe calls. The local tier (6.2) is the natural second word, and it is
the lambda design's own "impure callback" extension (§14.3). If function
types ever gain `mut &T` parameters, a function type can carry the same
tiers: pure, or local (may free below its `mut` parameters). The caller then
treats an indirect call like a direct call with that summary. Use the same
words for both features.

### 9.5 The rest of the language

- **Closed types and interfaces are the two sides of polymorphism.** A closed
  type is a closed set with exhaustive `match`; an interface is an open set
  with dispatch, extendable by downstream modules. Under this note both
  convert the same way (`let s: Shape = new Circle{...}`) and both own their
  value.
- **`Result[I]`, modules and `.sym`.** The payload is an owned box. The
  vtable travels in the box, so a downstream module can implement a
  dependency's interface for its own type and hand over owned values.
- **Strings** stay the only reference-counted type. Nothing here adds
  sharing.
- **Tools.** The motivating case in this repository is an owned sink or
  formatter in an options record (`opts.out: Writer` in `eza`/`cat`, with a
  buffered writer whose `write` replaces its buffer). That is a local-tier
  `mut` member called on a field receiver, which this note accepts and the v2
  note refuses.
- **No generics.** `[]I` is the only heterogeneous container. Helpers like
  `sortIndices` still need one copy per element type.

### 9.6 Tensions

- **Moves.** `let e = d` moves, and Java/Go users will notice. It is the same
  rule they already meet for records, and `&I`/`^I` give sharing.
- **The concrete type is gone after boxing.** There is no downcast or
  `instanceof`. A type test looks feasible later because the object header
  already holds its descriptor. That needs a check that descriptors are
  unique per type across modules. Out of scope here.
- **Owned-receiver members** (`fn (c: Circle) finish()` through `I`) become
  possible, because the box owns the object, but they stay out of v1.

---

## 10. Symbols, versions, reuse

- **`.sym`.** `I` and `?I` are allowed in every type position; `&I` and
  `mut &I` in parameters; `^I` in phase 2. Member lines carry `p0: &I`,
  `mut p0: &I` and `p0: I`. The v2 §7.4 fixture keeps its list with these
  spellings. Note that `p0: I` changes meaning from the paused 0.7 branch
  (a borrowed view) to an owned value; those fixtures are regenerated, not
  migrated. The member effect tier is the presence or absence of `pure`, so
  the grammar does not change.
- **Version.** `0.7`, format 3, ABI 1 (v2 §7.6 reasoning). Lambdas remain
  0.8 and 0.9, format 4. `^I` (2a) is a later minor feature, after lambdas
  Stage B.
- **Reused from the paused 0.7 branch.** Declarations, impls, vtables, the
  registry helpers, the `.sym` writer and reader, the frame cell (now `&I`),
  and the purity impl check (extended with the local tier).
- **New.** The box descriptor, owned conversion at typed destinations,
  `isOwned`/`nonNull` for `I`, interface receivers on the direct-method path,
  and the local-tier check and summary.
- **Dropped compared with v2.** The heap cell for stored views, the copy
  semantics, the bound/unrooted provenance category and its joins, the
  owned-argument rule, the deferred receiver dereference, the definite-escape
  check, and the `ownsCell`/`isOwned` split.

No line estimate is given. The expectation is less code than v2 §11.4,
because the owned and borrowed tiers reuse existing paths, but this has not
been measured.

---

## 11. Risks

1. **Conversion and move paths.** Every typed destination must move or box
   exactly once. Run the `OBO_CHECK=1` leak and double-free oracle on all run
   tests, including variants, nested boxes (a target that owns `I` fields),
   `Result[I]`, globals and `truncate`.
2. **Local-tier soundness.** The impl check must reject any method with
   `writesHandles`, freed globals, `freesAll`, or pfree on a non-`mut`
   parameter. Needed mutants: drop each conjunct; set the synthetic pfree on
   the wrong parameter; omit the receiver bit for `mut fn`. A forged `.sym`
   test, like v2 §7.5, should cover a member summary that claims less than
   the implementation does. Importers trust the declaration, and the defining
   module checks it.
3. **Frame-cell `&I`.** It must never escape: no storage, no return, no
   capture beyond the call. This reuses the reference rules, and needs tests
   for re-pointing, joins and `defer`.
4. **Dispatch codegen.** Two loads plus an indirect call on the existing
   method-call stack and padding path. Run the homes/no-registers checks on an
   interface-heavy program (v2 §10.7).
5. **Allocation.** One 16-byte box per owned conversion. Measure the
   compiler's peak only if the compiler adopts interfaces (not in round 1).

---

## 12. Decisions for the user

1. **Owning-first (this note) or views-first (the v2 note).** Recommended:
   owning-first. The v2 view design becomes phase 2b of `^I`.
2. **Local effect tier as the default member contract**, with no worst-case
   tier in v1. Recommended: yes. The cost is section 6.3.
3. **`^I` 2a (handle to a box) after lambdas; 2b only on demand.**
   Recommended: yes.
4. **Escaping callbacks as interfaces**, with a later one-member-interface
   lambda conversion, rather than an owned function type. Recommended: yes.
   This requires no change to the lambda Stage A/B plan.
5. **Version 0.7 / format 3 / ABI 1.** Recommended: yes.
