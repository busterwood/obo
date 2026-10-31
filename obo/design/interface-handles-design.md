# Interface handles (`^I`) — phase 2a design

Status: **design only, for review.** Baseline: `main` at `c47383fff8ab6251c3c254577c24047ad24762b7`
(2026-10-31). The tree is already the released `0.11`/format 6/ABI 1 compiler with lambdas
Stage B capture lists; interfaces v3 (`interfaces-v3-design.md`) is implemented for `I`, `?I`,
`&I`, `mut &I`, `&?I` and the effect tiers. This note designs only `^I` phase **2a**: a checked
handle to an **owned interface box**. It does not implement, test, bump a version, reserve a
version, touch the seed, or change `runtime.c`.

This note is an addendum to `interfaces-v3-design.md` for the parts it covers. The v3 rules for
`I`, `?I`, `&I`, `&?I`, conversions, effect tiers and `.sym` encoding stand, except the stale
version numbers and the `^I` sketch in v3 §8.2 and §9.1, which this note replaces. The baseline
runtime token, `rt_handle`/`rt_deref`/`rt_alive`, is the existing checked-handle machinery, not a
new runtime facility.

Evidence for the probes and the tree check is in
`/tmp/opencode/interface-handles-design-20261031-162132` (private). Sources were read only;
small private probe programs were run with the current compiler for the claims marked
“probed”. No repository test was changed or run as part of this design pass.

---

## 0. Recommendation in one page

1. **`^I` is a `^T`-style one-word token naming the box.** The token is the existing
   `rt_handle` token: low 32 bits are the box's granule offset in the heap, high 32 bits are the
   box's block generation. It names the **box**, not the target object. `rt_deref` validates the
   token and returns the box address; dispatch reads `[box]` (object) and `[box+8]` (vtable).
   There is no new representation, runtime slot or ABI word.
2. **`handle(place)` accepts only an owned `I` place.** A local, parameter, field, element,
   global or a narrowed owned `?I` local is accepted. A `&I`, `mut &I`, `&?I`, a `for` walk
   variable, or a field reached through a reference is refused with the S1 message. A `?I` place
   must be narrowed first. `handle(^T)`/`handle(c: T)` (a link to a concrete target) is **not**
   2a; it is phase 2b.
3. **All `^T` storage positions work in 2a without new code:** locals, parameters, returns,
   record/closed fields, array elements, globals, `Result[^I]`, and `?^I` in every such position.
   `^I` is non-owning and copyable; `?^I` is 0 or a token. `new []^I(n > 0)` is refused exactly as
   for any non-null element; use `[]?^I` or append to `[]^I(0)`. `&^I` is not a type.
4. **A call through `^I` is the existing interface call preceded by one `rt_deref`.** The
   existing `pushIfaceReceiver` loads the object and vtable **before any explicit argument runs**
   (S4). A later argument that may free the box is refused by the existing argument-order rule;
   the receiver is held with the `handleSym` root, and any change reaches a handle-rooted binding.
5. **Liveness is the box's generation.** `alive(h)` is `rt_alive`; a dead token is false. A use
   after free exits 7 with the existing message. Frees bump the generation. **A move does not.**
   The prior phrase “generation bumped on move” is not the runtime behaviour and is rejected:
   moving a box is not a free, the box keeps its address and generation, and a handle remains
   valid exactly as `^T` handles do. Reassignment, `drop`, and `truncate` free and therefore bump;
   `replace` hands the old box back and only an eventual free bumps it.
6. **Effects: a handle-borrowed receiver may call only members that cannot free.** `fn` members
   are allowed (their summary has no pfree bit on a read-only receiver), `pure` and `pure mut`
   members are allowed, and a `mut fn` member is refused because its synthetic summary has a pfree
   bit on the receiver. A shared sink reached by `^I` must therefore expose `pure mut` writes
   (scalar writes, append, writes through a mut parameter that frees nothing) or be reached through
   the owning `I` place. This is the existing v3 §7.2/§7.3 rule applied to the `^I` receiver; it is
   not a new effect tier.
7. **Equality is refused.** `^I == ^I`, `?^I == ?^I`, `^I == ?^I` and `^I == nil` are errors;
   `?^I == nil` / `?^I != nil` are allowed and narrow. A specific new message is recommended
   (section 6.3); the old interface-equality message is an acceptable alternative.
8. **Stage B captures:** a direct `^I` local can be captured in 2a as a **token snapshot**
   (`LambdaCap.kind == 2`, the existing handle row). This needs no environment layout change.
   Owned `I`, `&I`, `&?I`, `?I` and `?^I` captures remain unsupported in 2a; records carrying any
   interface remain rejected. A function type still cannot mention `^I`; the capture is a hidden
   body local, not a function-type parameter.
9. **`.sym`:** `^I` becomes an accepted type expression. No new declaration lines, fields,
   summaries or metadata; the interface/body hash covers the `^`-spelled type normally. ABI stays
   1. **Recommended identity for the next free slot: `OBO_VERSION "0.12"`, `SYM_FORMAT 7`,
   `SYM_ABI 1`**; this note deliberately reserves nothing. If decimal or another feature lands
   first, use the next free compiler/format number. If maintainers treat an accepted type
   spelling as not a format change, format 6 is the alternative; the compiler line still forces
   a rebuild.
10. **Phase 2b is separate:** `^I` from `^Circle` or `handle(c)` needs a `{token, vtable}` pair
   and is not a drop-in extension of 2a. Do not force a pair into 2a; see section 14.

---

## 1. Goals, scope and non-goals

### Goals

- Specify an implementable 2a that reuses the released 0.11 type system, box, vtable, handle
  runtime and Stage B capture machinery.
- Fix the constructor, positions, liveness, copy/move, dispatch order, effect provenance, capture
  compatibility, diagnostics, `.sym` and test/mutation inventory precisely enough to implement
  without reopening v3.
- State the risks around the `2_500_000`-byte compiler budget and the phase 2a/2b boundary.

### Non-goals

- Any compiler, runtime, test, seed, version or `.sym` change in this pass.
- `^I` from a concrete target (`^Circle`, `handle(c)`, a variant), a pair cell, or any second
  representation. Phase 2b, only if a tool needs it.
- Relaxing the interface firewall for owned `I`, `&I`, `&?I`, `?I` or interface-carrying records in
  lambda headers, parameters, returns or captures.
- A new effect tier, a new `pure` meaning, `writesHandles` provenance, or a new runtime table.
- Devirtualisation, method values, superinterfaces, variance, downcasts, reflection.
- Using interfaces or `^I` in the compiler's own source (the 0.11 seed must keep building it).
- Any change in the concurrently active decimal-language, db-adapters or html-lib areas.

Project invariants unchanged: single pass, no AST/IR, source read once, self-hosting fixed point,
textual `.sym` with hashes, one feature in flight, explicit seed promotion after review, no test
weakening.

---

## 2. What 0.11 actually is: corrections to the v3 recap

The v3 note was written against `0.8`/format 4 and a future lambdas Stage B. The current baseline
is later. These are the assumptions this design uses:

| v3 statement | actual at `c47383f` | consequence for 2a |
|---|---|---|
| Interfaces are `0.8`/format 4/ABI 1; lambdas Stage A `0.9`, Stage B `0.10` | `OBO_VERSION "0.11"`, `SYM_FORMAT 6`, `SYM_ABI 1` (`obo/src/obo.obo:113-115`); Stage B capture lists are released | `^I` is the next feature after 0.11, not after a future Stage B |
| `^I` expected `0.11`/format 5 (v3 §8.2) | 0.11 is already format 6 | ignore that reservation; choose the next free number at implementation |
| Stage B rejects interface captures and `^I` is later | Stage B is released; handles are an existing capture kind (`LambdaCap.kind == 2`, `captureSyms`/hidden prologue); interface-bearing types are rejected by `containsIface` | a direct `^I` capture can reuse the handle row; no new capture cell |
| `handle(x)` on any `I` gives `handles name records and closed types, not I` (0.8 step 6) | that is the actual 0.11 behaviour (probed: `handle(d: Shape)` and `handle(r: &Shape)` both give the message; `handle(o: ?Shape)` gives it with `?Shape`) | 2a replaces that refusal for the accepted owned cases only |
| “Replacing or freeing the box bumps its generation” (v3 §9.1) | the generation is bumped by `free_block` only (`runtime.c:369-383`); allocation preserves it on reuse (`alloc_raw`) | moves do not bump; reassign/drop/truncate bump when they free; `replace` may keep the old box alive |
| The local-tier summary means a plain `mut fn` may not be called through a handle | true: `pureFn` (`obo.obo:4292`) and the handle-borrow check in `callFnWith` (`obo.obo:6338-6350`) enforce it | `^I` inherits the rule; only `fn`, `pure`, `pure mut` calls pass |
| A `^I` phase 2a test/error inventory exists | `tests/errors/ifaces_handle.obx` holds the 0.8-era E8a refusals; no run/`^I` test exists | rewrite E8a, add the 2a run/error/module/sym tests |
| Compiler budget is 2,000,000 in v3 risk text | `PEAK_BUDGET = 2_500_000` (`tests/port_compare.py`, `tests/peak_budget.py`); current 0.11 self-compile peaks at 2,085,872 direct / 2,086,272 port (`FORK.md`) | 413,728 spare; measure, do not raise |
| 88 `.sym` fixtures | 95 committed `.sym` files: 94 `obosym 6`/`compiler "0.11"`, one `obosym 7` and one `"9.9"` | migrate headers at implementation; recount then |

The v3 design is otherwise authoritative for the box, vtable, `&I` pair slots, parameter modes,
effect summaries and `.sym` declaration encoding. This note does not re-specify those.

---

## 3. Representation

### 3.1 One word: the existing `^T` token, applied to the box

`I` is already a one-word pointer to a 32-byte heap box (`boxInto`, `boxDescriptor`):

```text
box (32 bytes with header):      handle token (one word):
+----------------------+         +-------------------------------+
| header 16 B          |         | generation (high 32)          |
+----------------------+         | granule offset (low 32)       |
| obj      own  8      | --> obj +-------------------------------+
| vtable   u64  8      |         the token names the box, not obj
+----------------------+
```

- The token is exactly `rt_handle(box)` (`runtime.c:474-477`): `(box - heap_start)/16` in the low
  32 bits and the box header's high-half generation in the high 32 bits. `rt_deref` validates
  `is_start` and the generation (`runtime.c:479-491`) and returns the box address.
- The target object and vtable are read from `[box]` and `[box+8]`, exactly as an owned/borrowed
  interface receiver does (v3 §4). The token does **not** name the target. This is why a box free
  invalidates the link in one check: the box and its target are freed together.
- `^I` is the existing type kind `handle` with `baseOf(t) == I`. The current generic handle code
  already gives it `isRef == false`, `nonNull == true`, `assignable` by base, `tname == "^" + I`,
  `appTypeExpr`/`symTypeText` emitting `^I`, storage in any position, and
  `rt_handle`/`rt_deref`/`rt_alive` code generation. The only changes are where the language
  deliberately narrows the type or constructor.
- **There is no kind field in the token.** The “kind” is static: only a `^I` can be produced from
  an `I` place, and only an interface base accepts `^I`. The runtime header's low 32 bits are the
  box descriptor pointer, not a handle kind. Adding a kind field would need a new token format
  and runtime check and buys nothing here. The prior “index+generation-kind” wording is corrected
  to **offset+generation**; the static type supplies the kind.

### 3.2 Why the existing machinery is sufficient (proof obligations)

1. **Token validation precedes any load.** `rt_deref` checks the block start and generation; on
   failure it exits 7 before returning a box. `handle_target` never dereferences an unvalidated
   object. A stale token can never be used to read `[box]` or `[box+8]`.
2. **Box and target lifetimes coincide.** The box is the unique owner of `obj`; `rt_free(box)`
   follows the box descriptor (one owned word at offset 0), frees the target through its own
   descriptor, then frees the box. The language has no operation that extracts `obj` from a box,
   replaces it in place, or frees the target separately. Therefore “box alive” implies “object and
   vtable alive”.
3. **The vtable is rodata.** It is written once at `impl` and never freed; a valid box's vtable
   word is immutable for the box's lifetime.
4. **Generations distinguish reuse.** `free_block` increments the header generation before putting
   the block on a free list (`runtime.c:377`); `alloc_raw` preserves the incremented value when
   reusing a block (it reads the old high half before zeroing). A new box at the same address
   therefore has a different generation. The only residual is a 32-bit generation wrap after
   2^32 frees of the same block, the existing `^T` residual.
5. **No alias can free a box behind the owner.** `I` values are move-only and unique; `&I`/`&?I`
   borrow; `^I` does not own. Only the owning place (or a function it is moved to) frees a box.
   A `^I` cannot be `drop`ped, `take`n or `replace`d because it is not owned.
6. **The token is copyable without ownership.** Copying a `^I` copies one word; there is no
   reference count and no destructor. `alive` and every use check the box generation.

### 3.3 Alternatives: runtime table or pair box (rejected for 2a)

| alternative | why not in 2a |
|---|---|
| Runtime side table `{kind, box, generation}`, token = index+generation | Needs a new runtime allocation/table, a new builtin slot or table lifecycle, and a way to release entries. The box header already holds the generation and the token already addresses it. The table is only interesting if the token must name something that is not a heap block (2b's pair). |
| A heap pair `{token, vtable}` and `^I` = pointer to the pair | The owned `I` box already contains `{obj, vtable}`; the pair would duplicate the vtable and add an allocation/lifetime problem (who frees the pair? frame vs heap?). It would also make `^I` a pointer, not a one-word checked token, and require a new ownership story. Defer to 2b. |
| Raw box pointer without generation | A freed box can be reused; a raw pointer would read a new object or freed memory. This is exactly what `^T` solves. |
| Token naming the target object (`rt_handle(obj)`) | One word cannot carry both the target token and the `(T, I)` vtable. It also loses the box as the single liveness unit. This is a 2b shape. |
| A tagged pair pointer with low-bit kind | A future 2b option, but 2a must not reserve bits or change the ABI. Do not implement now. |

### 3.4 `?^I`

`?^I` is the existing optional of a handle: the zero word is nil, a nonzero word is a `^I` token.
It is not a box; it owns nothing. It narrows with `!= nil` exactly like `?T`, and the narrowed
type is `^I` (not `I`). `alive(o)` requires the narrowed `^I`; `alive(?^I)` is the existing
`alive needs a handle, not ?^I`. `?^I` itself gets no equality beyond nil; see section 6.3.

---

## 4. Type formation and the constructor

### 4.1 Type expressions

`parseType` currently accepts `^T` only when `T` is a record or closed type
(`obo/src/obo.obo:4895-4900`); `symParseType` has the same restriction
(`obo/src/obo.obo:12432-12438`). Phase 2a changes both conditions to accept an `iface` base as
well:

```text
^T                     accepted as today, T record/closed
^I                     accepted in 2a, I interface
^?I, ^[]I, ^&I         still errors (the base is not a record/closed/interface)
```

The existing generic `handle` type operations then work. Keep the existing error text for a
non-handle base in 2a (`handles name records and closed types, not <t>`) to avoid churning every
`^int`/`^Result` diagnostic; document that interfaces are now also accepted. A later cleanup can
change the wording to “records, closed types and interfaces”, but that is a diagnostic-only
decision, not part of the semantics.

The source and `.sym` readers must both accept the base; otherwise a writer/reader pair or
`--check` diverges. They call different functions (`parseType` and `symParseType`), so both edits
are required. The interface member parser already routes through `parseType`/`symParseType`; a
member parameter or return may therefore be `^I`, `?^I`, `[]^I`, etc. `&^I` is refused by the
existing borrow check (`isOwned(^I)` is false): `only records, closed types and arrays can be
borrowed, not ^I`.

The writer (`appTypeExpr`, `obo.obo:11747`; `symTypeText`, `obo.obo:12278`) already emits `^`
for a handle, so `^I` is written canonically with no new syntax. The interface hash covers the
type expression; a type changing from `I` to `^I` or a new `^I` field/parameter changes the body
hash normally.

### 4.2 `handle(place)` rules

The builtin is at `obo/src/obo.obo:6693-6715`. Replace the blanket
`if isIfaceVal(x.typ) error` with this ordered rule:

| source expression at `handle(...)` | result |
|---|---|
| `d` where `d: I` is an owned local/parameter, or a whole field/element/global of an owned place | accepted: `^I` token of the box; the existing `hOrigin`/`pOwner` tracking is filled exactly as for `^T` |
| narrowed owned `?I` local/parameter (`if o != nil { handle(o) }`) | accepted: the box is still owned by `o`; `x.borrowed` is false and the narrowed type is `I` |
| `&I`, `mut &I`, `&?I` narrowed, a `for` walk variable over `[]I`/`[]?I`, or a field reached through a reference | refused: `cannot make a handle from a borrowed interface value; make it from the place that owns it` (S1) |
| a `?I` place that is not narrowed | refused: `handle needs a non-optional owned interface place, not ?I` (the type is rendered by `tname`) |
| an `I` temporary (`make()`, `handle(...)` result) | existing `handle needs a variable, field or element` |
| a `^T`, `^I` or `?^I` value | not an interface place: existing `handle needs a record or closed-type value that is not optional, not ^T` / `... not ^I` / `... not ?^I`; 2a does not overload `handle` |
| a non-interface `T`, `&T`, `?T` | unchanged `^T` handling and unchanged errors |

Implementation details:

- The current branch tests `isIfaceVal(x.typ)` before the mode/type test. That is why 0.11 reports
  the interface message for `handle(r: &Shape)` and `handle(o: ?Shape)` (probed). 2a needs the
  interface test after the temporary/mode check and must distinguish borrowed from owned:
  `x.borrowed` is set for reference locals/parameters, walk variables and fields reached through
  a reference. Do not accept on `x.typ.kind == "iface"` alone.
- `handle(o)` on a narrowed `?I` uses `x.sym == o`, `x.typ == I`, `x.borrowed == false`; the token
  names the same box and the origin owner is `o` if it is a local/parameter. The local may be
  `let` or `var`; the box is still owned by the local's slot.
- A field of an owned record parameter or an owned local (`p.d`) is an owned place; it is accepted
  and tracked by `pOwner = wholeOwnerOf(x.root)` (existing `handle` code). A field of a `&Holder`
  parameter is a borrowed place and is refused, even if the field's runtime value is a box: the
  caller's binding, not the field's runtime contents, decides.
- A global `G: I` or an owned field of a global is accepted; the existing `handle` code has no
  `hOrigin` for globals, so liveness is runtime-only, exactly as for a `^T` into a global.
- `handle(place)` does not consume or move the box. The place keeps ownership; the token is a
  non-owning copy. The `hOrigin`/`pOwner` fields are compile-time tracking only.

### 4.3 What is deliberately not a constructor

- **No `handle(^T)` or `handle(c)` for a concrete target in 2a.** A concrete link needs the
  `(T, I)` vtable, which is not in the target object. A `^Circle` token plus a vtable needs the 2b
  pair. Do not infer a vtable from a `^Circle` at 2a.
- **No implicit `handle(?I)`**, even if the optional is provably non-nil in the current path except
  through the existing narrowing: write `if o != nil { let h = handle(o) ... }`. This keeps one
  rule and a testable diagnostic.
- **No `handle(&I)`**, even though a `&I` points at a `{obj, vtable}` pair: `&I` may point at a
  frame cell built for a call, not a heap box. `rt_handle` on a frame cell would compute a token
  from a stack address, which is exactly S1. The same refusal covers walk variables.
- **No `handle` of a `^I`**; a handle of a handle is not meaningful, and a token copy already
  exists.

---

## 5. Liveness and `alive`

### 5.1 Runtime rules

`alive(h)` calls `rt_alive(h)` (`runtime.c:493`), which is `handle_target(h) != 0`; it does not
dereference and cannot exit. For `^I` the target is the box:

- token zero: `?^I` nil, not a `^I`; `alive` cannot see a nil without narrowing.
- box still allocated and generation matches: true.
- box freed (drop/reassign/truncate/owner scope exit), or its block reused: false.
- box moved to another owner: true. The address and generation are unchanged.
- box's target freed: cannot happen while the box is alive (3.2 item 2).

A use (`h.m(...)`, `h` as a receiver, or any future field access) dereferences through
`rt_deref`, which exits 7 with `obo: the object this handle names has been freed` on a stale token
(`runtime.c:487-491`). The message is existing and unchanged; “object” is the box here.

### 5.2 Compile-time tracking

`handle(place)` sets the existing `hOrigin` (whole owned local) or `pOwner`/`pPath` (field or
element of an owned local) fields. The existing origin machinery then applies unchanged:

- assigning over the owner, `drop(owner)`, `truncate` of an array that frees the element, and a
  `mut` call that frees below the owner mark the `let` handle dead;
- a later use is a compile error:
  `handle 'h' may name a freed object: 'b' was assigned or dropped after the handle was made from it; make the handle again`
  or the field/element form;
- a move of the owner does **not** mark the handle dead: the box is still alive, and the existing
  rule “safe after a free only if every path moved the owner” is about the eventual free, not the
  move. The origin is cleared so the compiler no longer keys the handle to that local, but the
  runtime token remains valid;
- loops, `defer`, `alive` narrowing, `break`/`continue` and join rules are the existing `^T`
  rules. In particular, a handle made before a loop that assigns over its owner inside the loop is
  refused when the loop uses the handle, and a `defer` that uses the handle blocks an owner free
  after it.

A `^I` local is not owned, so it is not in `ownedLocals` as an owner; only the *box owner* is.
A `?^I` local is also not owned. Copying a `^I` into a field, array, global or another local does
not create an origin; liveness is runtime-checked (this matches `^T` handles kept in `var` or
records).

### 5.3 Frees, moves and `replace`: the corrected event table

| event | generation / token | `alive(h)` |
|---|---|---|
| `handle(d)` | token of the box with current generation | true |
| copy `let g = h` | same token; no free | true |
| move the owning `I` (`let e = d`, passing it, returning it) | box unchanged; no generation bump | true (until a free of the new owner) |
| assign over the owner / `drop(owner)` | old box freed by `rt_free`; `free_block` bumps the generation | false |
| `truncate` an array of `I` past an element | removed boxes freed; each generation bumped | false for those elements |
| `replace(place, newbox)` | old box is moved out and returned, not necessarily freed; no bump at `replace` | true if the returned old box is still alive, false once it is dropped/freed; the compiler conservatively marks a place-tracked handle dead |
| scope exit / global teardown | boxes freed; generation bumped | false |
| free of the target object | impossible separately; the box owns it | n/a |

The important correction is the move row. A runtime bump on move would require a new call on
every move and would invalidate handles that are still valid; it is not proposed. `replace` is a
move out, not a free: the compiler may refuse a later use of a handle to the replaced place for
conservatism even when the old box is kept, and that false positive is accepted (same as `^T`).

### 5.4 Examples

```obo
type Shape = interface { pure fn area(): int; pure mut fn bump(): int }
type Square = ref record { s: int }
fn (q: &Square) area(): int { return q.s * q.s }
fn (mut q: &Square) bump(): int { q.s += 1; return q.s }
impl Shape for Square

fn main() {
    var d: Shape = new Square{s: 2}
    let h = handle(d)                 // ^Shape, token names the box
    println(h.area())                 // deref box, load obj/vtable, call
    println(h.bump())                 // pure mut through the handle is allowed
    d = new Square{s: 5}              // frees the old box: h is dead
    println(alive(h))                 // false
    // println(h.area())              // compile error (tracked) or exit 7 (untracked copy)
}
```

A moved box stays alive:

```obo
fn moveIt(d: Shape): ^Shape {
    let h = handle(d)
    let e = d                         // move the box; h still names it
    return h                           // the box is owned by e (not returned) -> runtime-only danger
}
// at the caller the box may be freed when moveIt returns; the returned token is then dead.
// A returned handle is a token; it does not extend box lifetime.
```

The last example is deliberately a warning: returning a `^I` from a function that owns the box
only in a local is legal as a token but the box dies at return. `alive` catches it; a use exits 7.
This is identical to returning a `^T` to a local. The compiler may be able to warn in a later
enhancement, but 2a does not add a new lifetime analysis.

---

## 6. Copy/move, ownership, equality and positions

### 6.1 `^I` is a copyable non-owning token

- `let g = h`, assignment to a `var`, passing, returning, storing in a field/element/global,
  `append` and `ok(h)` **copy the word**. The source remains usable. No move bit, free event or
  destructor is involved.
- A `^I` local has no owner; reassigning it just overwrites the token. No box is freed.
- A `^I` local is non-null: `var h: ^I` without an initializer uses the existing
  “needs an initial value (it cannot be nil)” rule; use `?^I` for a nil-capable local.
- `?^I` copies 0 or a token. It starts nil; assignment, widening from `^I`, and narrowing are
  ordinary optional operations.
- A `^I` lets a `pure mut` member mutate the target even when the handle binding is `let` (a
  handle is an alias that grants mutable access to the object, exactly as a `^T` does); it does not
  change the ownership or the box's `I` binding. A `mut fn` member is still refused through a
  handle by the effect rule (section 7.4), so the mutable access available through `^I` is the
  pure-mut subset.

### 6.2 No `drop`, `take` or `replace` on a `^I`

Because `isOwned(^I)` is false, the existing messages apply unchanged:

- `drop(h)` -> `drop needs a local variable holding an owned value`
- `take(h)` / `take(o)` where `o: ?^I` -> `take needs an optional variable, field or element holding an owned value`
- `replace(h, ...)` / `replace(o, ...)` -> `replace needs a variable, field or element holding an owned value`

Use plain assignment for a `^I`/`?^I` place. This is an important distinction from `I`: replacing
an `I` place frees the old box; replacing a `^I` place only changes a token.

### 6.3 Equality is forbidden

The current `relation` (`obo.obo:8386`) refuses `I`/`?I` comparisons but would treat a handle as a
scalar, so `^I == ^I` would silently compare tokens. Phase 2a adds a refusal before the generic
handle comparison:

| expression | result |
|---|---|
| `h1 == h2`, `h1 != h2` (`^I`) | error; recommended `interface handles cannot be compared; test an optional with '== nil' or '!= nil'` |
| `o1 == o2`, `o1 != o2` (`?^I`) | same |
| `h == o`, `o == h` | same |
| `o == nil`, `o != nil` | allowed; `?^I` nil test and narrowing |
| `h == nil` / `h != nil` | existing `cannot compare ^I with nil` (a `^I` is non-null) |

Why forbid token identity? Two `^I` tokens to the same box compare equal, but a token is not an
interface value identity and a future 2b pair/token representation would change the meaning. The
existing v3 rule is that interface values have no equality; a `^I` must not reintroduce identity
by the back door. If the user prefers consistency with existing diagnostics, reuse
`interface values cannot be compared; test an optional with '== nil' or '!= nil'`; this is an open
choice (section 16).

### 6.4 Positions

All positions that `^T` supports are supported in 2a, because the representation is the same
one-word checked token. No special case is needed in `isOwned`, descriptors or teardown.

| position | `^I` / `?^I` | notes |
|---|---|---|
| local / parameter / return | yes | `^I` non-null; `?^I` optional; `^I` parameters are mode 0 (copy), `&^I` is refused |
| record / closed field | yes | field is not owned; record descriptor lists no pointer for it; `?^I` may be omitted/nil |
| array `[]^I` | yes | `new []^I(0)` then `append`; `new []^I(n>0)` is the existing “elements would start as nil; use []?^I” error |
| array `[]?^I` | yes | `new []?^I(n)` is allowed, nil elements |
| global | yes | non-owned; global teardown does not free it; token may dangle at runtime if its box is freed |
| `Result[^I]` / `Result[?^I]` | yes | the Result shell is owned; the payload token is not; `match`/`try` copy it |
| `&^I`, `mut &^I` | no | `only records, closed types and arrays can be borrowed, not ^I`; a token already shares |
| `new ^I`, `const`, `match` on `^I`, `println` | no | existing type errors (`^I` is not a record, not a constant type, not matchable, not printable) |
| `drop` / `take` / `replace` | no | existing owned-place errors (6.2) |

`?^I` is supported in all the optional positions of the table; `?^I` capture is discussed in
section 9. A `Result[?^I]` payload is a 0/token word; `ok(o)` and `try` work as for any optional
base. `new []?^I(n)` is the only pre-sized array form for a handle base.

---

## 7. Calls and dispatch

### 7.1 The designator path is already there

`designatorT` (`obo.obo:7208`) dereferences every handle when a `.` follows
(`obo.obo:7238-7255`):

```text
if sym == T_DOT && x.typ.kind == "handle":
    deadHandleCheck(x.sym)          // compile-time origin check for a tracked let handle
    x = rtCall1(RT_DEREF, x, baseOf(x.typ))
    viaHandle = true
```

With `baseOf(^I) == I`, this produces an item of type `I` and a `viaHandle` flag. The following
`.` resolves the member through `methodCall` (`obo.obo:7420`), which sees `t.kind == "iface"` and
calls `ifaceMethodCall` (`obo.obo:7410`) -> `callFnWith(m, recv, true, ...)`. No new dispatch
path is needed. The existing `rt_deref` exits 7 on a dead token.

### 7.2 S4: dereference and load before the arguments

The exact sequence for `h.m(a, b)` is:

1. Resolve `h` (if `?^I`, the source must have been narrowed with `!= nil`).
2. Load the token and call `rt_deref`; the box address is validated and returned. This happens
   before the `m` name is resolved, hence before any argument.
3. `ifaceMethodCall` resolves `m` in the interface's members and checks arity/types.
4. `callFnWith` treats the receiver as argument 0: `checkAssign`, `borrowBind`, alias/mut checks,
   `argOrderCheck` and `holdArg` run.
5. `pushIfaceReceiver` (`obo.obo:6087-6098`) loads `vtable = [box+8]`, pushes it above the future
   arguments, loads `obj = [box]`, and pushes it as argument 0. **Both loads happen before any
   explicit argument expression runs** (the loop reaches `i == 0` first).
6. Explicit arguments run left to right; `argOrderCheck` and alias checks run after each.
7. `call [vtable + 8*slot]` (`callFnWith`, `obo.obo:6389-6395`); the vtable word is at
   `[rsp + 8*n]`.

S4 is already implemented for `I`/`&I`; `^I` inherits it because the deref merely turns the
receiver into an `I` item before the same path. No extra slot is needed: the vtable word lives on
the stack above the arguments, and the object is argument 0.

### 7.3 Why a later argument cannot free the box (the argument-order proof)

The receiver binding is held by `holdArg(b, 0, false)` where `borrowBind` for a `viaHandle` item
returns `singleBind(handleSym, ...)` (`itemBind`, `obo.obo:3867`). `hitBy`
(`obo.obo:4045`) treats **any** change as reaching a binding whose root is `handleSym`:

```text
if r == handleSym || r == root && ... { return true }
```

Therefore any later argument that moves/frees/assigns/replaces/takes/truncates a place, or calls
a function that may free, calls `invalidate(...)`/`heldMove(...)` and sets the held receiver's
`moved` flag. The next `argOrderCheck` reports:

```text
argument N of interface method 'I::m' may free what the receiver points into, and runs after it; compute it before the call
```

This is stronger than needed (any free anywhere reaches a handle-rooted receiver), but sound. A
later argument that only calls a demonstrably pure function does not invalidate `handleSym`
(`afterCall` skips `invalidate(handleSym, ...)` when `pureFn(f)`), so it is accepted; it cannot free
the box.

The existing probe with a `^T` receiver confirms the mechanism:

```text
arg_handle.obx:8:12: argument 1 of method 'Box::bump' may free what the receiver points into,
and runs after it; compute it before the call
```

For `^I` the same path yields the interface-method wording above. An actual test must pin both the
accepted pure-later-argument case and the refused freeing-later-argument case.

### 7.4 Receiver marker and the effect check

- `fn` (read-only receiver): member summary has no pfree bit for the receiver; `pureFn` is true;
  a `^I` receiver is accepted.
- `pure fn`: same, plus the pure guarantee; accepted.
- `pure mut fn`: receiver is mode 2 but the member is pure, so `setMemberSummary` leaves pfree 0;
  `pureFn` is true; accepted. This is the shared-sink case.
- `mut fn` (local tier): receiver mode 2, non-pure, so pfree bit 0 is set; `pureFn` is false. The
  handle-borrow loop in `callFnWith` reports
  `cannot pass a reference that borrows through a handle to interface method 'I::m': it might free what the handle names`.
  A `^I` receiver cannot call it, even if the concrete implementation happens not to free: the
  summary is per member (v3 S6), not per implementation.
- A member with a `mut p: &T` argument that may free: its synthetic summary has a pfree bit for
  that argument, so `pureFn` is false and a `^I` receiver is refused by the same conservative
  rule. A pure mut member with a mut parameter that frees nothing is accepted.

### 7.5 Global initializers and temporaries

- `ifaceMethodCall` already refuses any interface method call in a global initializer
  (`an interface method cannot be called in a global initializer; ...`). This applies to `^I`
  too; `handle(G)` itself may be built in the init chain (no frame cell), but a call through it
  must be moved into a function. This is consistent with v3 §5.4/E10.
- A handle-valued call result (`makeH().m()`) follows the existing `^T` rule: `^I` is not owned,
  so the store-first check does not apply; the deref is made on the temporary token. The call is
  still subject to S4 and the argument checks.
- A `^I` returned from a function is a token; it does not extend the box's lifetime. `alive` is
  the safe test. No new lifetime analysis in 2a.

---

## 8. Effects, `writesHandles`, and shared sinks

### 8.1 What the released §7 actually says

V3 §7.2 defines `pure` as `pureFn(c) && !c.writesHandles` for the implementation and gives the
synthetic member summary from markers and modes. V3 §7.3 applies that summary at the caller,
including the handle-borrow rule: a reference borrowed through a handle can be passed only to a
callee that cannot free (`pureFn`). `pure` is **not** “no writes”: a pure member may write
scalars, globals and its receiver, and may append; it may not free and may not write an owned
value through a handle.

The current compiler implements exactly this: `setMemberSummary` (`obo.obo:10614`) sets no bits
for a pure member; `pureFn` (`obo.obo:4292`) is true for a pure member; `afterCall`
(`obo.obo:4392`) skips `invalidate(handleSym, ...)` for a pure callee. The existing
`tests/run/ifaces_effects.obx` proves that a plain `fn` and a `pure mut` member through a record
handle are accepted and leave a borrowed reference valid; `tests/errors/ifaces_calls.obx` proves a
`mut fn` member through a handle is refused with the handle-borrow message.

### 8.2 Consequence for `^I` shared sinks

A `^I` receiver is a handle-borrowed receiver. Therefore:

- a shared sink behind `^I` must expose its writes as **`pure mut fn`** if callers are to use the
  handle: scalar field writes, appends to arrays, and writes through mut parameters that free
  nothing are pure by the existing predicate;
- a sink whose `write` **replaces or frees** its buffer field cannot be pure (it frees below the
  receiver and gets a pfree bit); callers must use the owning `I` place
  (`opts.out.write(...)`, `d.write(...)`), where a `mut fn` is allowed;
- a `pure mut` sink may still not free through the handle; its implementation is checked by
  `implDecl` (`pure` tier -> `pureFn && !writesHandles`) and its summary is the per-member pure
  summary.

This is the v3 §9.1 7a recommendation, now grounded in the actual 0.11 summary code rather than
the old prose. No new `writesHandles` rule is needed.

### 8.3 Provenance

- Creating a handle (`rt_handle`), copying it, `alive`, and calling a pure member do not set
  `writesHandles`.
- A function that takes a `^I` parameter and calls pure/pure-mut members is pure with respect to
  handles; it cannot free the box, because it cannot call a freeing member and cannot drop the
  token.
- A function that stores a token in a global/field writes a scalar, not an owned value; that is
  not `writesHandles`. A non-owning registry of `^I` is therefore allowed in a pure callback,
  subject to the ordinary global-write/purity rules.
- A function that passes a `^I` to another function inherits that callee's summary as usual. If
  the callee writes through a handle (it would have to be doing so through some other handle-
  rooted reference, not the `^I` interface call, because `mut fn` is refused), the caller becomes
  `writesHandles` through the existing `afterCall`.
- No provenance category is stored in the token or `.sym`; the token is a value, and the compiler
  tracking is the same origin/handle tracking as `^T`.

---

## 9. Captures and lambdas at 0.11

### 9.1 The released Stage B rules

Stage B capture modes (`lambdas-design.md` §4.2, `LANGUAGE.md` §5d) are:

| captured local | mode | body sees |
|---|---|---|
| record/closed/array/optional/`Result` | read-only whole-local borrow (kind 1) | read-only reference |
| `&T` | reference copy (kind 1) | read-only reference |
| `int`/`byte`/`bool`/`float` | one-word snapshot (kind 0) | `let` local |
| `^T` | token copy (kind 2) | `let` handle local |
| function value | cell pointer copy (kind 3) | `let` function-value local |
| string | rejected | — |
| interface value, or a type carrying one | rejected | — |

The classifier is in `lambdaExpr` (`obo.obo:7622-7682`): it calls `deadHandleCheck` for a handle,
rejects `string`, checks `incompleteCarrier`, rejects `isIfaceVal(ct)`, then rejects
`containsIface(ct)`, and finally chooses kind 0/1/2/3. `captureBind` (`obo.obo:1659`) holds the
call-site binding for kind 1 roots and kind 2 origin owners.

### 9.2 `^I` capture: allowed in 2a (recommended)

A direct `^I` local/parameter is a **handle**, so it takes the existing kind 2 path:

- `ct.kind == "handle"` and `baseOf(ct).kind == "iface"`;
- `deadHandleCheck` still runs (a tracked `let h = handle(d)` from a whole owner gets the usual
  dead-handle check);
- the interface classifier must not reject it. The smallest change is in the **capture classifier**
  only: when `ct.kind == "handle" && baseOf(ct).kind == "iface"`, skip the `containsIface(ct)`
  rejection, while keeping `isIfaceVal(ct)` for `I`/`?I` and keeping all other carriers rejected.
  Do **not** remove the handle recursion from `containsIface` itself: function-type formation
  (`rejectIfaceFn`) still has to see through `^I`;
- `ck = 2`; the environment word is the token; the hidden body's hidden local is a normal `let`
  `^I` (not a reference local), so `alive(h)`, member calls and token copies work;
- `captureBind` kind 2 adds the `hOrigin` owner when the captured handle is a tracked whole-owner
  `let` handle; otherwise there is no static binding, and liveness is runtime-checked (the same
  residual as `^T` field/element handles).

This is safe because the token is a snapshot, not a borrow: it does not extend the box's lifetime,
and every use checks the generation. The lambda is call-scoped, so the creator's frame remains
live; if the box is freed behind the compiler's tracking, the body exits 7, not a UAF.

A `^I` capture does **not** allow a `^I` function-type parameter or return. `rejectIfaceFn` still
recurses through a handle to its interface base and refuses `fn(^I): ...` with the direct existing
message `a function value cannot take or return an interface value`. The capture is a hidden body
local only; no function type changes, and `containsIface` itself is unchanged.

### 9.3 Owned `I`, `&I`, `&?I`, `?I`, `?^I`, and carriers

| capture | 2a | reason / message |
|---|---|---|
| `^I` | allowed (recommended) | token snapshot; kind 2; no new environment layout |
| owned `I` / `?I` | rejected | would be a read-only borrow of the box (`&I` semantics) and needs a carrier/binding mode; existing `cannot capture interface value '<x>'; function values cannot take interface values in v1` |
| `&I` / `mut &I` / narrowed `&?I` | rejected | B1 pair pointer semantics; a one-word capture cell cannot copy the `{obj, vtable}` pair into a body-owned pair slot; the declared type is `I`/`?I`, so the direct `cannot capture interface value '<x>'; ...` message applies |
| `?^I` | rejected in 2a (recommended) | narrow to a `^I` local and capture that; the generic optional rule would otherwise try a read-only optional borrow, not a token snapshot. Existing carrier message; a specific “narrow first” message is an open choice |
| record/closed/array/`Result` carrying `I`, `&I`, `^I` | rejected | `containsIface` recurses into fields; function types cannot take interface values in v1. Do not carve out a record that merely holds a `^I` in 2a; the direct handle is the supported form |
| string | rejected | unchanged |

The owned-`I` and `&I` captures are the v3 §10.2 relaxation steps 1–3. They remain later work; 2a
does not change the firewall except for the direct `^I` handle row that the released Stage B table
already has.

### 9.4 Capture lifetime and effects

- A `^I` capture from a whole-owner `let h = handle(d)` carries the owner in `captureBind`; a later
  argument that moves/frees `d` is refused at the creating call by `argOrderCheck` (the capture
  holder has `argIsCapture` and the owner root). A `^I` captured from a field/element or a copied
  token has no static roots; a later free is runtime-checked (exit 7) or caught by the ordinary
  handle-liveness residual.
- The lambda body must be pure. A call through the captured `^I` to a `mut fn` member is refused
  by the handle-borrow rule; pure/pure-mut calls are allowed. A body that only calls `alive` and
  pure members stays pure.
- A `^I` capture cannot be used to store the handle beyond the call: a capturing literal is still
  only a direct call argument; the environment is the creator's frame. No change to Stage B
  storage rules.
- `?^I` capture is deferred; the idiom is
  `if o != nil { let h: ^I = o; use(fn [h](...) { ... }) }` (with the lambda in the call
  argument position). This is an explicit narrowing and keeps the capture kind unambiguous.

---

## 10. `.sym`, hashes and versions

### 10.1 Encoding

`^I`, `?^I`, `[]^I`, `Result[^I]`, `[]?^I` and `Result[?^I]` are ordinary type expressions. The
writer already emits `^` for `handle` (`appTypeExpr`, `symTypeText`), and the reader only needs
`symParseType` to accept an `iface` base. Example:

```text
type Shape = interface { pure fn area(): int; pure mut fn bump(): int }
type Registry = ref record {
    current: Shape
    link: ^Shape
    maybe: ?^Shape
    links: []^Shape
    last: Result[^Shape]
}
fn first(xs: []^Shape): ^Shape
fn find(link: ?^Shape): ?^Shape
type Sink = interface { pure mut fn write(p0: ^Shape): int }
impl Sink for Registry
```

No new declaration group, member marker, summary item, vtable description, descriptor, field or
global metadata is needed. `^I` is not an owned field, so no record/array/Result descriptor
changes. `Result[^I]`'s ok variant has no owned payload word; `?^I` is a zero/token word.

### 10.2 Hashes and provenance

- The interface hash covers the exact declaration body after the `interface` line, including type
  expressions. A `^I` field/parameter/return changes the hash as any other type would.
- `source` is unchanged as a hash function; a source using `^I` has a different source hash
  naturally.
- `depiface` hashes change when a dependency's exported interface changes to/from `^I`.
- There is no per-handle provenance in `.sym`: a token is not a declaration, and no address,
  generation or box layout is written. The `.sym` trust model is unchanged: a forged file can
  claim a `^I` type but cannot forge a runtime token; the runtime generation check is the backstop.
- Function/member summaries are unchanged. A `^I` parameter is mode 0 and non-owned; it cannot
  contribute pfree. A function that only uses `^I` via pure/pure-mut calls has no new flags. The
  reader rebuilds member summaries from markers/modes exactly as in 0.11.

### 10.3 Version recommendation (no reservation)

**Recommended at implementation: `OBO_VERSION "0.12"`, `SYM_FORMAT 7`, `SYM_ABI 1`.** Reasons:

- `OBO_VERSION` is bumped for every compiler feature; 0.11 is taken.
- `SYM_FORMAT` is the grammar/meaning version. `symParseType` newly accepts a type spelling that
  0.11 rejects (`^Shape`), and `--check` can now see `^I` in declarations. Under the documented
  policy (“bump `obosym` on a grammar or meaning change”), this is format 7. It also makes a
  0.11-generated file fail with `has format 6; this compiler reads 7; rebuild it` rather than only
  the compiler-line message.
- `SYM_ABI` stays 1: one-word values, record layout, descriptors, calling convention, global
  addressing, effect-summary meaning and initialization order are unchanged; no runtime slot or
  descriptor changes.

The design deliberately **reserves nothing**. If decimal-language or another feature lands first,
the next free version/format is used; if maintainers judge an accepted type spelling not to be a
format change, format 6 is the fallback. In either case the compiler line changes and every
committed `.sym` fixture must be rebuilt/regenerated explicitly; there is no mixed-version
compatibility promise.

### 10.4 Compatibility table (0.11 / next / next+1)

| artifact / compiler | 0.11 (format 6) | phase 2a with format 7 | phase 2a with format 6 |
|---|---|---|---|
| 0.11 `.sym`, no `^I` | builds | rejected by compiler line (and format 7); rebuild | rejected by compiler line; rebuild |
| 0.11 `.sym` containing `^I` | cannot be produced; reader rejects `^Shape` | accepted by the 2a compiler only after rebuild | accepted only after rebuild |
| phase 2a `.sym` containing `^I` | rejected by compiler/format; 0.11 parser also rejects `^Shape` | builds | builds |
| runtime image / ABI | ABI 1 | ABI 1; no runtime change | ABI 1; no runtime change |
| 0.11 seed | builds the 2a compiler if `obo.obo` uses no `^I` | same | same |

A 0.13 or later compiler is not reserved; if another feature consumes the number, phase 2a uses
the next free one. The phase 2a compiler source must remain free of `^I` syntax so the released
0.11 seed can bootstrap it; the `^I` tests are user programs compiled by the new compiler, with
`port_compare.py` minima set for the new version. One seed promotion follows review.

---

## 11. Diagnostics

Style as `LANGUAGE.md` §10; types through `tname`; members as `I::m`. “Existing” is the 0.11 text.

### 11.1 New or changed

| situation | message |
|---|---|
| `handle` of `&I`, `mut &I`, `&?I` narrowed, a `for` walk variable, or a field through a reference | `cannot make a handle from a borrowed interface value; make it from the place that owns it` |
| `handle` of a `?I` place | `handle needs a non-optional owned interface place, not ?I` |
| equality of two `^I`/`?^I` values (other than nil) | `interface handles cannot be compared; test an optional with '== nil' or '!= nil'` (or reuse the existing interface-values text; open choice) |
| direct `^I` capture | accepted; no message |
| owned `I`/`?I` or `&I`/`&?I` capture | existing `cannot capture interface value '<x>'; function values cannot take interface values in v1` (the reference's declared type is `I`/`?I` at the capture classifier) |
| `?^I` or interface-carrying record/closed/array/`Result` capture | existing `cannot capture '<x>': its type holds an interface value; function values cannot take interface values in v1` (a specific “narrow ?^I first” message is optional) |
| `.sym` rewrite of a 0.11 file | existing `has format 6; this compiler reads 7; rebuild it` / `was built by compiler '0.11'; this is '0.12'; rebuild it` (numbers at implementation) |

### 11.2 Existing messages reused unchanged

- Type formation: `handles name records and closed types, not <t>` (the list is now incomplete for
  interfaces; see 4.1); `only records, closed types and arrays can be borrowed, not ^I` for `&^I`.
- `handle` not a place: `handle needs a variable, field or element`; `handle needs a record or
  closed-type value that is not optional, not <t>` for a `^T`/`^I`/`?^I` source.
- `alive` of `?^I`: `alive needs a handle, not ?^I`.
- Optional use: `'o' may be nil: check it with '!= nil' first`.
- `drop`: `drop needs a local variable holding an owned value`.
- `take`: `take needs an optional variable, field or element holding an owned value`.
- `replace`: `replace needs a variable, field or element holding an owned value`.
- `^I == nil`: `cannot compare ^I with nil`.
- Liveness: `handle 'h' may name a freed object: '<owner>' was assigned or dropped after the handle was made from it; make the handle again`; the field/element variant; the loop/defer forms of `LANGUAGE.md` §5.
- Runtime: `obo: the object this handle names has been freed` (exit 7).
- Calls: `argument N of interface method 'I::m' may free what the receiver points into, and runs after it; compute it before the call`; `cannot pass a reference that borrows through a handle to interface method 'I::m': it might free what the handle names`; `the receiver of interface method 'I::m' needs a mut reference, but this one is read-only`; `interface 'I' has no method 'm'`; arity/type texts; global-initializer text.
- Firewall: `fn(^I)` is the direct text `a function value cannot take or return an interface value`
  (`rejectIfaceFn` recurses through the handle); a record/closed/array/optional/`Result` carrier
  keeps the existing `... '<T>': it holds an interface value` text, and an incomplete shell keeps
  its “not yet defined” text.

No existing assertion is to be weakened. The E8a 0.8-era expectations in
`tests/errors/ifaces_handle.obx` are replaced by 2a cases; the old refusal is no longer the
expected result for an owned `I` place.

---

## 12. Compiler and runtime implementation plan

### 12.1 Source anchors at `c47383f`

| area | anchor | 2a change |
|---|---|---|
| type parser | `parseType` `obo.obo:4848`, caret branch `4895-4900` | accept `b.kind == "iface"` |
| sym type parser | `symParseType` `obo.obo:12386`, caret branch `12432-12438` | accept `b.kind == "iface"` |
| generic type ops | `isRef` `1342`, `nonNull` `1347`, `assignable` `1370`, `tname` `1326-1334`, `appTypeExpr` `11747`, `symTypeText` `12278` | no change |
| handle builtin | `handle` branch `obo.obo:6693-6715` | owned/borrowed/optional interface rules; keep `^T` |
| alive/drop/take/replace | `6799`, `6780`, `6822`, `6717` | no change; existing generic rules |
| equality | `relation` `8386`, interface check `8409-8412` | refuse interface handles except nil |
| box/vtable | `boxDescriptor` `5702`, `boxInto` `5759`, `ifacePairCell` `5776` | no change |
| dispatch | `designatorT` handle deref `7238-7255`, `ifaceMethodCall` `7410`, `methodCall` `7420` | no change expected; `^I` reaches the existing path |
| S4 load | `pushIfaceReceiver` `6087-6098`, receiver loop `6185-6266` | no change; verify with tests |
| effect check | `setMemberSummary` `10614`, `pureFn` `4292`, `afterCall` `4392`, handle-borrow check `6338-6350` | no change; tests pin behaviour |
| captures | `lambdaExpr` `7572`, classifier `7622-7682`, `captureBind` `1659`, hidden prologue `10492-10520` | in the classifier only, skip `containsIface` for a direct handle-of-iface; leave `containsIface`/`rejectIfaceFn` unchanged; keep `ck=2` |
| `.sym` reader/writer | `appTypeExpr` `11747`, `symTypeText` `12278`, `symParseType` `12386` | accept and emit `^I` |
| runtime | `rt_handle` `runtime.c:474`, `handle_target` `479`, `rt_deref` `487`, `rt_alive` `493`, `free_block` `371-383` | none |
| constants | `OBO_VERSION`/`SYM_FORMAT`/`SYM_ABI` `obo.obo:113-115` | bump at implementation |

### 12.2 Steps

1. **Type and reader (compile-only).** Add the two parser edits, the `isIfaceHandle` helper and
   the equality refusal. Compile `^I` declarations, fields, params, returns, arrays and `Result`
   positions; run source and `.sym` parse tests. No code generation yet.
2. **Constructor and liveness.** Implement the owned/borrowed/optional constructor rule; set the
   existing `hOrigin`/`pOwner` as for `^T`; verify `handle(d)`/`handle(p.d)`/`handle(shapes[i])`/
   narrowed `?I` and all S1 refusals. Add `alive`, `?^I`, storage and copy tests.
3. **Dispatch and effects.** No new dispatch code; run the call matrix (pure, `pure mut`, `fn`,
   `mut fn` refused, later arg frees, later pure arg, global init, receiver through field/element/
   global/returned handle). Verify S4 load order with the existing code and the tests. Add the
   shared-sink cases.
4. **Captures.** Exempt direct `^I` from the capture firewall, keep `?^I`/`I`/`&I` rejected, add
   run/error tests; verify `captureBind` and the hidden prologue. No function-type changes.
5. **`.sym` and versions.** Update writer/reader if not already; bump constants to the next free
   values; migrate every fixture header explicitly; regenerate goldens only with
   `sym_test.py --update`; add a stale-format fixture. Update `sym_test.py`
   `OBO_VERSION`/`SYM_FORMAT`, `port_compare.py` minima and any version table.
6. **Docs and gates.** `LANGUAGE.md` §5b/§5 handles, this design's README entry, `FORK.md`
   evidence; all gates; self-host fixed point; peak; review; one seed promotion. `^I` is not used
   in `obo.obo` itself, so the 0.11 seed builds the 0.12 compiler.

### 12.3 Runtime and slots

**No `runtime.c` change and no new runtime slot.** The existing slots are:

```text
RT_HANDLE = 26
RT_DEREF  = 27
RT_ALIVE  = 28
```

`rt_handle` is called on the box; `rt_deref` validates and returns the box; `rt_alive` checks it.
`rt_free` already frees the box's owned object through the box descriptor. No descriptor, array
kind, Result payload or vtable layout changes. `SYM_ABI 1` stays.

### 12.4 Budget and risk

The current compiler self-compiles at **2,085,872** bytes live (three direct runs) and the port
comparison at **2,086,272**, against `PEAK_BUDGET = 2_500_000`; spare is **413,728** bytes
(`FORK.md`, `tests/port_compare.py`, `tests/peak_budget.py`). Phase 2a is a small parser/
constructor/classifier change with no runtime data structures, so the expected peak delta is
small; nevertheless:

- measure the compiler peak after step 1 and after step 6 (and after decimal, if it lands first);
- do not raise the budget; if the peak is over, stop and report to the user;
- the budget is the approved `2,500,000`, not the historical 2,000,000 in older notes;
- the `^I` tests add user-program memory, not compiler heap, except for the usual compile of the
  test source.

### 12.5 Gates

`run_fork.py` (run/errors/modules/sym/own-fuzz), `module_test.py`, `sym_test.py` (golden compare,
no blind `--update`), `sym_fuzz.py`, `doc_check.py`, `homes_check.py`, `homes_edge.py`,
`elf_test.py`, `io_test.py`, `meta_test.py`, `port_test.py`, `port_compare.py`,
`mutate_fork.py`, `mutate_port.py`, `mutate_io.py`, `imports.py`, `stdlib.py`, `tools.py` and
`eza.py` unchanged and green; self-hosting fixed point (stage2 == stage3); peak within budget. The
new `^I` run/error/module files need `port_compare.py` minimum-version entries because the 0.11
seed cannot compile `^I`.

---

## 13. Tests and mutants

Paths follow the tree. All run tests execute under `OBO_CHECK=1` where applicable.

### 13.1 Run tests

- **R-2a-1 `tests/run/ifaces_handles.obx`:** `handle(shapes[1])`; `alive`; `pure fn` call through
  `^I`; `pure mut` scalar write through `^I`; `?^I` nil/narrow; `^I` in a local, parameter,
  return, field, array, global and `Result` payload; copy token; move the box and still use the
  handle; `truncate` and reassign to kill it; exit-7 case in a separate expected-exit run.
- **R-2a-2 `tests/run/ifaces_handle_sink.obx`:** an object with a `pure mut fn write` and a
  `pure mut fn append`, called through `^I`; a plain `fn` observer; a reference held across the
  call; a `mut fn` variant is in the error file, not run.
- **R-2a-3 `tests/run/ifaces_handle_fields.obx`:** handle to a field/element of an owned record,
  nested box; `handle(p.d)` and `handle(shapes[1])`; owner move vs free; loop/defer rules; `alive`
  false after the element is removed.
- **R-2a-4 `tests/run/lambda_iface_handle.obx`:** capture a direct `^I` in a lambda passed to a
  pure helper; body calls `alive` and a pure member; a captured token whose box is freed before the
  call is runtime-checked or refused by the owner binding; no owned/`&I` capture.
- **R-2a-5 `tests/run/ifaces_handle_global.obx`:** `var H: ^Shape` global assigned a token;
  `alive`; an interface call in a global initializer is refused in E-2a, the workaround is a
  function; global `?^I`.

### 13.2 Error tests

- **E-2a-1 `tests/errors/ifaces_handle.obx` (rewrite E8a):** `handle(d)` owned accepted in a
  separate run; `handle(r: &I)`, `mut &I`, `&?I`, `for` walk variable, field through a borrowed
  record, `handle(o: ?I)`, `handle(make())`; exact S1/optional/temporary texts.
- **E-2a-2 `tests/errors/ifaces_handle_calls.obx`:** `h.m()` with `mut fn` through `^I` refused;
  `pure mut` accepted; a later argument that frees the box refused; a later pure argument
  accepted; receiver read-only/mut wording; global init; unknown member/arity/type; `^I` receiver
  on a nil optional without narrowing.
- **E-2a-3 `tests/errors/ifaces_handle_eq.obx`:** `^I == ^I`, `?^I == ?^I`, `?^I == ^I`,
  `^I == nil` refused; `?^I == nil`/`!= nil` accepted.
- **E-2a-4 `tests/errors/ifaces_handle_pos.obx`:** `&^I`, `mut &^I`, `new []^I(1)`, `new I`,
  `const`, `match`, `println`, `drop`, `take`, `replace`, `alive(?^I)`, uninitialized `^I`.
- **E-2a-5 `tests/errors/closures.obx` additions:** capture `I`, `?I`, `&I`, `?^I`, a record
  holding `^I`; `fn(^I): ...`; exact existing carrier/interface messages; direct `^I` capture
  accepted in a run case, not error.
- **E-2a-6 `tests/errors/ifaces_handle_owner.obx`:** compile-time dead handle after owner
  assignment/drop, field/element form, loop/defer forms; a move that keeps the box is accepted and
  a later free is refused/runtime; `replace` false-positive documented.

### 13.3 Module and `.sym` tests

- **S-2a-1 `tests/modules/run/ifaces_handle`:** exported interface/impl, `^I` field/param/return,
  cross-module call, `--check` and full build; same output.
- **S-2a-2 `tests/modules/sym/iface_handle_sig`:** a `lib.obx` with `^I`, `?^I`, `[]^I`,
  `Result[^I]`, `[]?^I`, `Result[?^I]` in fields, params, returns and a member; `main.obx` calls
  them; committed `lib.sym`/`main.sym`; `--check` exits 0; `--reemit-sym` byte-identical; a full
  build re-reads and compares the member modes/types.
- **S-2a-3 `.sym` errors:** `^int`/`^Result[...]` still rejected with the old text; a forged
  `^I` in a non-interface position rejected; stale format/compiler/ABI fixtures; `^I` in a
  function type or capture carrier rejected.
- **S-2a-4 fixture migration:** all committed `.sym` headers to the new compiler/format; the
  intentional `format` fixture moves one past the new format; `sym_test.py` constants/table;
  goldens regenerated explicitly; recount at implementation (95 now, not v3's 88).

### 13.4 Mutants (each with a catcher; record equivalents)

| id | mutation | catcher |
|---|---|---|
| H1 | `parseType` accepts `^I` but `symParseType` does not (or vice versa) | S-2a-2, `--check`/full divergence |
| H2 | `handle` accepts `&I`/walk variable | E-2a-1 (S1) |
| H3 | `handle` accepts `?I` without narrowing | E-2a-1 |
| H4 | `handle` token computed from the target object rather than the box | R-2a-1 dispatch / crash |
| H5 | `rt_deref` generation check removed or `alive` ignores the generation | R-2a-1 reuse/dead test |
| H6 | receiver is loaded after the explicit arguments (S4) | E-2a-2 later-free; note the legal-source equivalence (arg-order refuses the free), so pair it with H7 |
| H7 | the `^I` receiver is not held as `handleSym` (or `argOrderCheck` skips it) | E-2a-2 later-free |
| H8 | `mut fn` through `^I` accepted (pure check dropped) | E-2a-2 |
| H9 | `pure mut` through `^I` refused (over-conservative) | R-2a-2 |
| H10 | equality compares `^I` tokens | E-2a-3 |
| H11 | owned `I`/`&I` capture accepted or `^I` capture stores a box pointer instead of a token | R-2a-4/E-2a-5/leak oracle |
| H12 | `?^I` nil test/narrowing accepted unchecked | E-2a-2/E-2a-3 |
| H13 | `handle` of a field through a borrowed record accepted | E-2a-1 |
| H14 | `.sym` writer drops `^` or reader rejects `iface` base | S-2a-2 |
| H15 | version/format constants not bumped or fixtures not migrated | S-2a-4 |
| H16 | `^I` treated as owned (drop/take/free) | E-2a-4/leak oracle |
| H17 | the capture exemption is applied to a record carrying `^I`, or `containsIface`/`rejectIfaceFn` is globally relaxed so `fn(^I)` passes | E-2a-5 |
| H18 | `handle` returns a `^T`/concrete type instead of `^I` | type/round-trip test |

H6's S4 order is equivalent on accepted source if H7 holds, because the argument-order check
refuses every later free; record that explicitly rather than claiming a runtime catch. H6 is
still a useful code-review mutant for the load order.

---

## 14. Staged 2a and 2b

### 2a (this design)

- token-to-box `^I`; owned `I` places only; narrowed `?I` accepted; `?^I`;
- all `^T` storage positions, copy semantics, `alive`, equality refusal;
- dispatch through the existing S4 path; effect rules pure/pure-mut only;
- direct `^I` capture; owned `I`/`&I`/`?^I` captures rejected; `fn(^I)` rejected;
- no runtime/ABI change; `.sym` accepts `^I`; one version/format bump at implementation.

### 2b (separate, not designed here)

- `^I` from `^Circle` or `handle(c: Circle)`, and maybe `handle(^T)`; a `{token, vtable}` pair
  carrying the concrete target token and the `(T, I)` vtable. This is the v2 note's pair cell.
- It is **not** a drop-in extension of the 2a token: a pair pointer, a tagged token, a runtime
  table, or a new type/format is required. Do not reserve a bit or force a pair into 2a.
- Later capture relaxations: owned `I` as a read-only box borrow, `&I` pair copies, `?^I`, and
  `^I` in function-type parameters/returns if the lambda environment and call bindings can express
  them. Each needs its own design/review.
- A `mut fn` through `^I` remains refused unless a future member summary carries enough
  provenance to prove the receiver is not freed. Do not relax it by trusting one implementation.

---

## 15. Alternatives rejected

1. **Runtime handle table.** More state, new runtime calls, no benefit over the box header.
2. **Pair box for 2a.** Duplicates the vtable, needs a pair lifetime, breaks the one-word token.
3. **Raw pointer `^I`.** UAF after free; loses exit 7.
4. **Token naming the target object.** Cannot carry the vtable in one word; loses box liveness.
5. **Bump generation on move.** Not a free; would require runtime work and invalidate live links;
   contradicts `^T`. Only frees bump.
6. **Allow `handle(&I)` / walk variables.** Token from a frame cell / pair address; S1.
7. **Allow `handle(?I)` implicitly.** No nil representation in a `^I`; require narrowing.
8. **Owned equality on `^I`.** Reintroduces interface identity and will not survive 2b.
9. **Allow `mut fn` through `^I`.** The per-member summary may free the receiver; only
   pure/pure-mut is sound. Shared sinks use `pure mut` or the owner.
10. **Allow owned `I`/`&I` captures in 2a.** Needs a carrier/binding mode and a 16-byte pair copy;
    Stage B's one-word capture layout does not express it.
11. **Use `I` itself as the handle.** It owns and would free the box; a `^I` must not own.
12. **Treat `^I` as a new `Type` kind.** The generic handle machinery already covers it; a new kind
    would duplicate storage, `.sym`, ownership and capture rules.
13. **Do a format bump without a compiler bump (or vice versa).** Both are exact-match gates; the
    project policy requires both for a new feature/format.

---

## 16. Unresolved choices and recommendations

| # | choice | recommendation | why / cost |
|---|---|---|---|
| 1 | version and format | next free; if available `0.12`/format 7/ABI 1 | grammar accepts a new type spelling; compiler line forces rebuild anyway |
| 2 | direct `^I` capture | allow in 2a | existing handle kind 2; no layout change; safe token snapshot |
| 3 | `?^I` capture | defer; narrow to `^I` first | generic optional capture would try a borrow, not a token; keep one supported form |
| 4 | owned `I`/`&I` captures | defer to 2b/later | needs a new carrier/binding and pair copy; v3 §10.2 steps 1–3 |
| 5 | generation on move | do not bump; correct the prior wording | runtime only knows free; move is not a free; `^T` semantics |
| 6 | equality message | specific `interface handles cannot be compared...` | clearer than the interface-value text; reuse is acceptable |
| 7 | `^int`/non-interface handle text | keep existing text in 2a | avoid diagnostic churn; document the expanded set |
| 8 | `handle` of a narrowed `?I` local | allow | it is still an owned place; the nil test is explicit; costs no representation |
| 9 | `mut fn` through `^I` | keep refused; only `fn`/`pure`/`pure mut` | per-member summary; soundness of the handle-borrow rule |
| 10 | 2b representation | separate design; no reservation now | pair/tag/table choice is not forced by 2a |
| 11 | direct `^I` in `Result`/arrays/globals | include in 2a | same one-word token and existing generic positions; tests prove it |
| 12 | `^I` capture of a token whose owner is untracked | accept with runtime residual | same as `^T` field/element handles; owner-tracked cases are compile-checked |

None blocks implementation of 2a. Choices 1–4 affect acceptance only; choices 5–9 are defaults
that can be revisited later (mostly by accepting more, which does not break programs); choice 10
is the 2b boundary.

---

## 17. Appendix: evidence and probe notes

Evidence directory:
`/tmp/opencode/interface-handles-design-20261031-162132/`

- `worktree-verify.txt`: `HEAD` is `c47383fff8ab6251c3c254577c24047ad24762b7`, no tracked changes
  before the design edits, and the worktree is the isolated `interface-handles-design` tree.
- `probes.txt` and the five `.obx` sources:
  - `arg_handle.obx` (a `^T` receiver, later owned argument) compiles to the existing
    `argument 1 of method 'Box::bump' may free what the receiver points into, and runs after it`
    error; this is the argument-order mechanism `^I` inherits.
  - `current_handle.obx` (`handle(d: Shape)`) gives
    `handles name records and closed types, not Shape` at 0.11, the behaviour 2a replaces for the
    owned case.
  - `current_borrowed.obx` (`handle(r: &Shape)`) gives the same interface message at 0.11; 2a
    must give the S1 borrowed message.
  - `current_optional.obx` (`handle(o: ?Shape)`) gives
    `handles name records and closed types, not ?Shape`; 2a gives the non-optional owned-place
    message.
  - `current_errors.obx` shows `^Shape` is rejected at 0.11 in a type position.
  - `capture-probes.txt` (`capture_iface.obx`, `capture_r.obx`, `capture_o.obx`) confirms the
    released Stage B direct capture messages for owned `I`, `&I` and `?I`: all three use
    `cannot capture interface value '<x>'; function values cannot take interface values in v1`,
    which is why 2a's rejection rows distinguish those from the carrier message for `?^I` and
    interface-holding records.
- Source/runtime facts checked directly: `OBO_VERSION`/`SYM_FORMAT`/`SYM_ABI` at
  `obo.obo:113-115`; `rt_handle`/`handle_target`/`rt_deref`/`rt_alive` at `runtime.c:474-493`;
  generation bump in `free_block` at `runtime.c:377`; `pushIfaceReceiver` at `obo.obo:6087`;
  `handle` branch at `obo.obo:6693`; capture classifier at `obo.obo:7622`; `relation` at
  `obo.obo:8386`; `symParseType` at `obo.obo:12386`.
- No repository test was run. The probes are private and outside the worktree.
