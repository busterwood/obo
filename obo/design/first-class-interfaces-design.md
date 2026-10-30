# First-class interface values (interfaces v2) — design note

Status: **design only**, for independent review. Baseline: `main` at `638b2f0`
(`Merge branch 'lambdas-design': lambdas design note`), read-only in the worktree
`/home/chris/obx/.claude/worktrees/first-class-interfaces-design` on branch
`first-class-interfaces-design`. The paused 0.7 interfaces implementation is
read-only at `/home/chris/obx/.claude/worktrees/interfaces-impl`, commit
`771f441` (`obo: add interfaces, dynamic dispatch and symbol format 3 (0.7)`),
not merged and not promoted. The compiler source on `main` is 11,027 lines
(`obo/src/obo.obo`), `LANGUAGE.md` 2,081 lines, `FORK.md` 5,235 lines; the
promoted seed `obo/seed/obo` reports `obo 0.6 sym-format 2 abi 1`. Nothing is
implemented by this pass: no compiler, test, seed, runtime, tool or library
change. The only repository write is this file and one commit on the design
branch; nothing is pushed.

**This note supersedes the value-layer sections of
`obo/design/interfaces-design.md`.** It supersedes that note's §2.3 (creation
sources and destinations), §3 (second-class values, frame-resident cell,
ownership and nil), §4.6 (performance of conversion/copy), §5.3/§5.4 (the
conversion helper and the bare-receiver call), §6 (storage-position
restrictions in symbols), §7 (ownership interactions built on second-class
values), §8 (diagnostics that assert storage/return/nil rejection), §9 (tests
that assert those restrictions), the value-layer parts of §10, §12 decisions
3, 4, 5, 6 and 9, and §13's value/lifetime exclusions. It **retains and builds
on** the declaration grammar (§2.1–2.2), nominal interface identity, explicit
`[pub] impl`, the per-`(type, interface)` vtable and implementation matching
(§4.2–4.3), variants and one parent step (§4.4), the purity idea (§4.5, with
the amendments below), the compiler registry shape (§5.1–5.2), the symbol-file
skeleton for interface/impl declarations (§6), and the exclusions that are not
about the value layer (interface-to-interface conversion, owned receivers,
structural typing, generics, devirtualisation). Where the two notes disagree
about a value, storage, lifetime or purity rule, this note wins.

**Revision after the paused 0.7 reviews.** The 0.7 compiler/ownership review
found one HIGH soundness defect and the symbols/format review found one MEDIUM
coverage gap; both are folded in here, not merely fixed on the paused branch:

1. **Deep receiver (HIGH).** The 0.7 `ifaceCall` computed the receiver binding
   but never fed its `deepLine` into the `writesHandles` refusal; explicit
   arguments did enter it. An impure interface call on a *deep* receiver
   (`let d: D = &b.c; d.imp(handle(b))`) therefore compiled, and the callee
   could free the receiver through the handle argument, then read freed
   memory. The direct call `c.imp(handle(b))` is refused. Section 4.7 makes
   the receiver **argument 0** for every argument check and refuses an impure
   member on any deep receiver before codegen; section 10 carries the exact
   full/`--check`/module/read-only/mut regression and a mutant. The old note's
   §3.5, §4.5 and §9.6(h) acceptance of the deep receiver call was wrong and is
   superseded.
2. **Interface-member signature coverage (MEDIUM).** Every committed `.sym`
   interface line exercised only `int`/`void` signatures. A writer mutant that
   dropped `mut` on an interface-member parameter passed `run_fork`,
   `module_test`, `sym_test` and `port_compare`. Section 7.4 and section 10
   require committed fixtures and mode-0/reemit tests for `p0: &T`,
   `mut p0: &T`, `p0: I`, `mut p0: I`, record and foreign parameter types,
   non-`int` and interface returns, self-referential members, and
   writer/reader mutants for that encoding.
3. The lower findings are addressed: the `handleSafe` `!writesHandles`
   conjunct is kept as a defence and pinned by a forged source-less `.sym`
   test (section 7.5); the interface-heavy program is actually run under the
   homes and no-registers checks (section 10.7), not only listed in
   `port_compare.py`; the diagnostics avoid the quoted-bare-function-name,
   unsupported-source, `type`/`for`-contextual and RICH_LIB mistakes of the
   0.7 branch (section 9); and the foreign no-implementation variant fixture
   is typed as the **variant**, so the parent step is exercised (section
   10.5).

The design assumes the project invariants unchanged: single pass, no AST/IR,
source read once, self-hosting fixed point, textual symbol files with hashes,
no test weakening, one compiler feature in flight, explicit seed promotion
only after review. Every claim about the implementation below is anchored by
function name, not line number, because the interfaces merge will shift lines.

---

## 0. Recommendation in one page

1. **A first-class interface value is one word: a pointer to a 16-byte
   immutable cell `{token, vtable}`.** `token` is the existing runtime handle
   token (offset plus generation); `vtable` is the `(concrete type, interface)`
   vtable address in read-only data. The value is a **snapshot view**: copying
   it copies the pair, never the target; mutating the target through one copy
   is visible through every copy.
2. **Cells are uniquely owned; there is no reference count.** A local or
   parameter of interface type is a one-word pointer to a **frame cell** (two
   hidden 8-byte slots in the current frame). A stored position (record field,
   array element, global, `Result` payload) or a return value owns a
   **heap cell**. Copying a stored value to a local copies the 16-byte pair
   into a frame cell; storing copies it into a heap cell. No cell is ever
   shared between two owners, so no retain/release, no cell cycles and no new
   runtime slot are needed. The cell is an ordinary record-like block with a
   descriptor of size 16 and no owned children: `RT_ALLOC_RECORD` allocates it
   and `RT_FREE` frees it. The one-word ABI is unchanged (`SYM_ABI 1`).
3. **Interface values are copyable like strings, not move-only.** `let e = d`,
   a field/element copy, a by-value argument, a constructor field and a return
   all copy the value; the source stays usable. `var d: I` is allowed and
   reassignment overwrites the local's own frame cell. This removes the
   "bound once" surprise. The target is shared, as a Java/Go interface shares
   its object; only the small view cell is copied.
4. **Storage and returns are allowed everywhere a type may appear**, subject
   to the shape rules: record and closed-type fields, `?I` fields, `[]I` and
   `[]?I`, globals, `Result[I]`, `Result[?I]`, function/method returns and
   interface-member returns. `I` is non-null; `?I` is the optional interface
   and its nil representation is the zero word (no nil cell). A record field
   `d: I` must be initialised; `d: ?I` may be omitted and starts nil.
   `new []I(0)` works; `new []I(n)` with `n > 0` is refused because the
   elements would start nil; `new []?I(n)` is allowed.
5. **Owned concrete conversion is still refused.** `let d: I = c` for an owned
   record/closed value is an error with the `'&'`/`handle(...)` remedies. A
   borrow (`&place` or a bare reference) and a handle (`^T`) are the sources.
   Interface-to-interface conversion, `&I`, `^I`, `new I`, comparison, `match`
   and `alive` on interface values remain out.
6. **The lifetime model is a documented checked view.** Every dispatch loads
   the token and calls `RT_DEREF`; a freed target exits 7, never a read of
   freed memory. While a value is **bound** (created from a borrow, or copied
   from a bound value, including through a parameter), the existing reference
   rules apply: a visible move/free/assignment of any root invalidates it and
   a later use is a compile error. Storing or returning a value **drops its
   binding**; reading it back yields an **unrooted** value. A returned value
   bound only to the current function's owned locals or owned parameters is a
   compile error because it is certainly dangling (section 4.6). Everything
   else is a checked view: the token is the backstop.
7. **Purity is what makes the checked view sound.** An **impure** member may be
   called only through a **bound, whole-object** receiver whose roots are not
   handles and not globals the member might free. A **deep** receiver, a
   **handle-rooted** receiver and an **unrooted** receiver (stored, reloaded,
   returned, or `?I` narrowed from such) require a **pure** member. The
   receiver is argument 0 for the deep check; this is the HIGH defect fix.
   `pure mut` members (scalar writes, appends) are allowed through stored
   values; a `mut` member that frees owned state below the target is impure and
   is not. A call that passes a handle-rooted or unrooted interface value may
   not pass an **owned** argument (mode 0 and an owned type: record, closed,
   array, owned optional or `Result`; strings, handles, scalars and interface
   values are copyable/non-owning and allowed), because the argument might own
   the target; a call through a bound value uses the precise owner/ancestor
   checks.
   Section 4.7 states the exact rules.
8. **Nominal identity, explicit `impl`, per-`(type, interface)` vtables,
   declaration-order slots, variants via the closed parent, no
   interface-to-interface conversion and no owned receivers are preserved.**
   The vtable is still built at the `impl` when every concrete method is
   defined; addresses live in rodata and never in a `.sym`. A downstream
   module can implement an interface for its own type and pass a value to a
   dependency's function; the cell carries the vtable.
9. **Mutability is the target's, not the cell's.** For a bound value,
   writability comes from the binding: `let d: I = &b` is writable when `b`'s
   place is; a read-only reference gives a read-only target. `mut d: I`
   parameters are writable targets; `d: I` parameters are read-only. For an
   unrooted value, storage has discarded the original capability; a stored
   value is a **new non-owning capability**, as a handle is, and may call a
   `pure mut` member. A `pure mut` member cannot free the target, so this is
   safe. A `mut` member that can free remains restricted to bound writable
   whole-object receivers.
10. **The redesign stays `OBO_VERSION "0.7"`, `SYM_FORMAT 3`, `SYM_ABI 1`.**
    0.7 was never merged or promoted, so there is no compatibility promise to
    break; the `.sym` grammar is unchanged (interface definitions/impls
    already exist) and the new storage positions use existing type
    expressions. The paused branch's fixtures are replaced, not migrated. This
    leaves the lambda plan unchanged: Stage A `0.8`/format 4, Stage B `0.9`/
    format 4. If the review insists that a semantics change needs a format
    bump, the fallback is `0.8`/format 4 and the lambda design needs a
    one-line version revision, not a semantic one (section 7.6).
11. **The lambda firewall is kept for v1.** Function types still take no
    interface parameters and do not return interface values; lambdas do not
    capture interface values; interface members do not use function types. The
    first-class representation makes some of these possible later (an
    interface parameter is one word), but allowing them now would merge two
    features and force a lambda design revision. Stage A does not need a
    revision (section 8).
12. **Implementation reuses the declaration, vtable, symbol and purity
    skeleton of the paused branch and rewrites the value layer.** `ifaceValue`,
    `ifaceCell`, `ifaceCall`, the var/field/array/global/return restrictions,
    the effect/binding integration and the symbol positions are the rewrite;
    `ifaceSyms`/`impls`/`findImplFor`/`implDecl`/`appIface`/`symIfaceDef` and
    their tests are largely reusable (section 11). The compiler source stays
    interface-free in round 1 so the 0.6 seed builds it; one seed promotion
    follows review.

**The two genuine forks** are (a) impure dispatch through stored values and
(b) factories that return a newly created target. The recommended v1 answers
both with the checked view: stored values call pure members only, and a
function cannot return a view of its own local target. The alternatives —
runtime pinning or owning interface values that keep the target alive — are
larger follow-up features (sections 12.2 and 13). They do not block the
storage/return redesign the user asked for, but they are the honest boundary
of no-GC, single-ownership interfaces.

---

## 1. Scope and non-goals

In scope:

- the value-layer redesign: representation, cells, copying, storage,
  initialisation, nil/optional, returns and lifetime;
- exact rules for record fields, arrays, globals, `Result` payloads, returns,
  nested aggregates and their destructors;
- the target-binding and purity rules that keep dispatch sound without
  owning the target;
- the HIGH deep-receiver fix and its regression, and the medium symbol
  coverage gap;
- the symbols and version decision, the fixture plan and the lambda
  interaction;
- tests, mutants, docs and the implementation path.

Out of scope (extension points are in section 13):

- **owning interface values**: moving a concrete target into an interface
  value so the value keeps it alive (the `Box<dyn Trait>` shape), target
  reference counting or a GC;
- **runtime pinning** of interface targets during a call;
- impure dispatch through stored/unrooted values;
- factories returning a view of a newly created local target;
- interface-to-interface conversion, structural typing, variance,
  superinterfaces/embedding, runtime type tags, downcasts, reflection and
  `alive` on interface values;
- owned receivers in interface method sets; method values; function values in
  interfaces or lambdas that take/return/capture interface values; generics;
- devirtualisation, inlining or other optimisation;
- refactoring `eza`, the library, the tools or the compiler to use
  interfaces. The compiler source stays interface-free in round 1.

The project invariants hold unchanged. The 0.7 implementation branch remains
read-only; this note does not modify it.

---

## 2. The problem, and what "first-class" must mean here

The approved 0.7 design makes interface values **second-class**: parameters
and `let` locals only, no fields, arrays, globals, `Result` payloads, returns
or optionals; no `var`; no nil. The user's decision (2026-10-30) is that this
is an unacceptable surprise for people coming from Java/Go, especially when
lambdas — also described as second-class — are more usable. The 0.7 merge is
paused; 0.6 remains promoted; 0.7 is not promoted.

A redesign has to answer four questions that the 0.7 design avoided by
restriction:

1. **Where does the value's representation live when it outlives the frame?**
   The 0.7 cell is frame-resident; a stored/returned cell would dangle.
2. **What happens when a copy is stored or passed?** The 0.7 value is a
   pointer to a frame cell that a callee borrows for the call; a stored copy
   needs a lifetime independent of the frame.
3. **What keeps the target alive?** The 0.7 design relies on the caller's
   borrow binding plus the purity contract. A stored value has no binding.
4. **What does nil mean?** The 0.7 design has no nil interface; first-class
   fields and optionals make nil unavoidable unless `?I` is carved out.

The project constraints narrow the answer space: no GC; every owned value has
one owner and is freed at a known point; references are second-class borrows;
handles are non-owning tokens with generation checks; values are one word; the
compiler is single-pass and emits text symbols with hashes; the compiler is
self-hosting; one feature is in flight. This note treats "first-class" as:
**a value that can be stored, copied, passed and returned like a string, with
a defined, checked lifetime for its target**. It does not mean "owns its
target" or "keeps its target alive"; that is the owning-value extension.

The good parts of 0.7 that are preserved are listed in section 0, item 8. The parts
that changed are exactly the value representation, storage positions, copy
semantics, nil/initialisation, lifetime and the purity rules that depend on
provenance.

---

## 3. Representation options

All options keep interface types nominal and keep the vtable-per-
`(concrete type, interface)` design. They differ in what a value is and where
it lives.

### 3.1 (a) Refcounted heap cell

A value is one word: a pointer to an immutable heap cell
`{refcount, token, vtable}`. Constructing a value allocates a cell. Copying
retains it; dropping releases it; the last release frees the cell. The cell
owns no target, so there are no cycles through cells.

**What it buys.** One-word values and ABI everywhere; a copy is a pointer plus
a retain/release; cell identity is shared but unobservable (interface equality
is out); the cell can live as long as any copy without any one owner; storage,
returns, fields and arrays are uniform.

**What it costs.**

- Every interface-typed local/parameter becomes an owned, reference-counted
  binding: scope exit, reassignment, argument passing and every path join
  need a release; every copy and every read of a stored value needs a retain.
  The compiler already does this for strings (`isStr`, `RT_STR_INC`/
  `RT_STR_DEC`, `freeOwned`, descriptors' string lists), so the pattern is
  known, but it is pervasive and easy to get wrong on early-return/`try`/
  `defer`/loop paths.
- The destructor story reaches record fields, arrays, globals and `Result`
  payloads: a record descriptor cannot simply list an interface field as an
  owned object, because `rt_free` would free the cell even when copies exist;
  it must release it. Arrays and `Result` payloads need the same distinction.
  Either descriptors gain a third offset list (interface-cell refs) or the
  runtime inspects the cell kind and releases; both are a real runtime/
  descriptor change.
- The cell is shared, so a cell's lifetime is no longer tied to one owner.
  That is a new ownership concept in a language whose records and arrays are
  uniquely owned; only strings (immutable byte cells) are shared today.
- The allocator cost is per **construction**, including a local
  `let d: I = &c` that never escapes; the 0.7 frame cell avoided that. A
  refcounted design needs heap cells for all live values because a local may
  be stored.

**One-word ABI.** Yes: a value, parameter, field, array element, global and
return are one word. The refcount and cell layout are internal; they would be
gated by the compiler/format, not the ABI. But record/array/Result
destructors and the runtime free path change, which is the main cost.

### 3.2 (a′) Heap cell with unique ownership — the recommended refinement

A value is one word: a pointer to a 16-byte immutable cell
`{token, vtable}`. The cell is **uniquely owned** by the place that holds it:

- a local or parameter holds a pointer to a **frame cell** (16 bytes in the
  current frame);
- a record field, array element, global, `Result` payload or return value
  owns a **heap cell** allocated with the existing record allocator.

Copies copy the 16-byte pair, never the cell pointer. Since no cell is ever
shared, there is no refcount and no retain/release. The cell contains no
owned pointer (the token is not an owning pointer and the vtable is rodata),
so it is an ordinary record with a descriptor of size 16 and no owned/string
fields. `RT_ALLOC_RECORD` allocates it; the existing `rt_free` descriptor walk
frees it. A record field of interface type is an ordinary owned pointer field
in the descriptor, so freeing the record frees the field's cell (not the
target). An array of interface values uses the existing owned-element array
kind; `truncate` and array free free the element cells. A `Result[I]` payload
is an owned pointer field in the ok descriptor and is freed with the result
cell. A global interface value is freed in the existing global teardown loop
once that loop is taught that an interface place owns a cell (section 6.3).
A return value is a heap cell owned by the caller.

**Why unique ownership is better than refcounting here.**

- Cells own nothing, are immutable, and have no observable identity. Sharing a
  cell buys only allocation/copy savings; it buys no semantics.
- Unique ownership keeps the language's single-owner model: a stored cell has
  exactly one owner and is freed at a known point, like every record. There is
  no shared lifetime to reason about and no refcount path in the compiler.
- It reuses the existing allocator, descriptor walk, array kinds, Result
  cleanup and global teardown; the runtime does not change. The 16-byte cell
  descriptor is generated by the existing `descriptor(size, ptrs, strs)`
  helper with no owned or string offsets.
- It avoids allocating a heap cell for a local construction: locals and
  parameters are frame cells, as in 0.7. A heap cell is allocated only when a
  value is stored or returned (or when a stored value is updated and needs a
  new cell; overwriting an existing uniquely-owned cell in place is allowed).
- The copy is 16 bytes, not one word. That is the price; it is a two-load/
  two-store copy, and it happens on a copy to a local/argument, not on every
  read of a stored field if the compiler is smart (it copies into the
  receiver/argument temp it needs anyway). No allocation, no branch.

**Cost.** A heap cell per stored interface value; a 16-byte copy per copy to
a frame cell; a heap cell per return. This is the same asymptotic cost as a
small `struct {token, vtable}` box, with deterministic free. It is the
smallest change that makes storage and returns possible without a new
runtime, new descriptor grammar or an ABI bump.

**One-word ABI.** Yes. A value is a cell pointer; parameters, fields, array
elements, globals and returns are one word. Record layout stays one word per
field; array element size stays eight bytes (the array stores cell pointers);
the return register is one word; descriptors are the existing format. The
cell is internal and format-gated; it never appears in a `.sym`.

### 3.3 (b) Move-only owned heap cell

A value is one word: a pointer to an owned heap cell `{token, vtable}`. The
value is moved on assignment, storage, return and by-value passing; `&I`
would borrow the cell; the cell is freed at the owning place's death.

**What it buys.** No copy, no refcount, one word, deterministic free. A
stored/returned value owns its cell; a moved value cannot be aliased.

**Why it is rejected.** It is hostile to the Java/Go expectation the user
asked for. `fn describe(d: I)` consumes the caller's value; `let e = d` moves;
a loop over `[]I` would have to move or borrow elements; passing an interface
value to two functions needs `&I` and a second reference layer; a field cannot
be read without moving or borrowing. The language would need `&I` borrows,
move bits for interface values, array-element move rules and a
reference-to-interface-value design, all to avoid a 16-byte copy. It is more
work than (a′) and worse ergonomics. A move-only owned **target** value is a
different feature (section 3.6), not this representation.

### 3.4 (c) Fat inline 16-byte value

A value is the pair itself `{token, vtable}`, 16 bytes, copied inline. No heap
and no refcount.

**What it buys.** No allocation, no cell lifetime, copy is two words, and the
value is self-contained. A nil pair `{0, 0}` is possible.

**Why it is rejected.** It forces ABI 2:

- record fields are currently one 8-byte word and descriptors use 8-byte
  offsets; a 16-byte field changes record layout and descriptor offsets;
- array elements are 8 bytes (or 1 for `[]byte`); `[]I` would need 16-byte
  elements and a new element-size/kind story;
- a `Result[I]` payload is 8 bytes; it would become 16;
- a parameter is one word; a 16-byte parameter needs a hidden pointer or two
  stack slots, and a return needs two registers or a hidden result pointer;
- the compiler's `Item`/register model is one word; the whole expression and
  call machinery assumes one value word.

The compiler could hide the fat value behind a pointer for parameters and
returns (which is (a′) by another name) or change every layout path (ABI 2).
The second is a large, cross-cutting change for no semantic gain over a
uniquely-owned cell; the first is the recommended design. Fat values also do
not solve target lifetime: the token still has to be dereferenced, and a
stored fat value has the same unrooted/purity questions. ABI 2 would move the
lambda Stage A target to a new format and add churn to every fixture for a
representation that buys allocation-free storage. Reject for v1; record as an
optimisation if profiling ever shows cell allocation dominates.

### 3.5 (d) Handle-rooted-only storage, borrow-rooted values call-scoped

Two flavours of the same `I`:

- a value constructed from a handle can be stored/returned, because it is a
  self-contained token;
- a value constructed from a borrow stays parameters/locals-only, exactly as
  in 0.7, because its target is only guaranteed for the call.

**What it buys.** The smallest change from 0.7: keep the frame cell for
borrow-rooted values and add a storage representation (a heap cell or a fat
pair) for handle-rooted values. Provenance is tracked statically; the compiler
knows at every expression which flavour it has.

**Why it is rejected as the primary design.** It does not meet the user's
expectation: storing an ordinary borrowed object would require an explicit
`handle(c)` first, which is the very ceremony that makes interfaces feel
second-class. It also still needs a storage representation and lifetime story
for the handle flavour, and stored handle-rooted values still require pure
members for impure dispatch; the ergonomic win over the checked view is small.
The insight worth keeping is provenance: **bound values are checked and can
support impure dispatch; unrooted values are pure-only**. That is folded into
the recommended design; the storage split is not.

### 3.6 (e) GC, target reference counting or full ownership

A value could own its target: an owning cell frees the target when the last
value dies. This makes impure dispatch safe (the target cannot die while any
value exists), makes factories work, and gives the closest Java-like
semantics. Variants: a tracing GC; a target refcount in every object; a
refcounted cell that owns the target (`Rc<dyn Trait>`); moving the target into
a move-only owning cell (`Box<dyn Trait>`).

**Why it is out for v1.**

- A tracing GC contradicts the no-GC invariant and the explicit
  free/leak-check model (`OBO_CHECK` exit 5/6, visible frees, references
  invalidated by changes). It would change every ownership rule.
- Target reference counting is shared ownership of records, which the
  language does not have. Strings are the only shared cells; they are
  immutable and contain no owned pointers. A record target owns fields,
  arrays, strings and possibly other interface values; making it shared
  changes `drop`, assignment, references and handles, and creates cycles
  (a target with an interface field pointing back). Cycles would leak without
  a GC.
- A refcounted cell that owns the target (`Rc<dyn Trait>`) is a new owning
  value kind: construction moves the target (`let d: I = c` consumes `c`);
  copies share the cell; the last release frees the target; a target type
  that contains interface values can form cycles unless forbidden; the
  compiler must track owning vs viewing cells and their destructors at every
  position; descriptors need interface-cell refs or a runtime kind; and the
  purity rules still need to distinguish owning from viewing values when a
  parameter may receive either. It is a coherent follow-up feature (section
  13), not a v1 representation.
- A move-only owning cell (`Box<dyn Trait>`) gives factories and safe impure
  dispatch but loses copyability; it is option (b) with an owned target. It is
  Rust-like, not Java/Go-like, and needs the move/reference layer rejected in
  section 3.3.

The recommended design keeps the cell non-owning and makes the purity rules
carry the safety. The two user-visible consequences are stated up front:
stored values call pure members only, and a function cannot return a view of
its own local target. Both are the price of not owning the target.

### 3.7 Trade-off table and recommendation

| option | value size/ABI | copy | storage/returns | target lifetime | impure dispatch through stored | compiler/runtime cost | verdict |
|---|---|---|---|---|---|---|---|
| (a) refcounted heap cell | 1 word; ABI 1 | retain/release | yes | token-checked | pure-only (unrooted) | new runtime kind + release paths; heavy binding lifecycle | viable, but more than needed |
| **(a′) unique heap cell** | **1 word; ABI 1** | **16-byte pair copy** | **yes** | **token-checked** | **pure-only (unrooted)** | **reuse allocator/descriptor/array/Result; rewrite value layer** | **recommended** |
| (b) move-only cell | 1 word; ABI 1 | move | yes | token-checked | pure-only unless borrowed | move bits + `&I` surface; poor ergonomics | reject |
| (c) fat 16-byte | 2 words; **ABI 2** | 2-word copy | yes | token-checked | pure-only | record/array/Result/call ABI rewrite | reject v1 |
| (d) handle-only storage | 1 word / storage form | copy | only handle-rooted | token-checked | pure-only | two provenance flavours; ceremony | reject as primary |
| (e) owning/GC | varies | share/move | yes | owned | safe | new ownership model; cycles/GC; large | extension |

**Recommendation: (a′), a uniquely-owned 16-byte cell.** It is the smallest
representation that makes interface values storable and returnable while
keeping the one-word ABI, the existing runtime and descriptor format, the
single-owner model and the existing vtable/module machinery. The refcount in
(a) is unnecessary because the cell owns nothing and has no observable
identity; unique ownership is simpler and more in keeping with the language.
If profiling later shows the 16-byte copy or the per-store allocation is a
problem, sharing/refcounting can be added as an internal optimisation with no
language or ABI change; that is an implementation decision, not a semantic
one.

---

## 4. Chosen semantics and exact rules

### 4.1 What a value is

An interface value is a **pair** `{token, vtable}`:

```text
frame cell (locals/parameters):      heap cell (stored values/returns):
+----------------+                   +----------------+
| token     u64  |                   | token     u64  |
| vtable    u64  |                   | vtable    u64  |
+----------------+                   +----------------+
```

- `token` is the existing `^T` handle token: the target's offset in 16-byte
  units in the low 32 bits, the block generation in the high 32 bits
  (`runtime.c` `rt_handle`/`handle_target`/`rt_deref`). A zero token can only
  come from a nil handle, which the type system prevents (`^T` is non-null,
  `?^T` is narrowed).
- `vtable` is the address of the `(concrete type, interface)` vtable in
  read-only data, or a dummy in `--check`.
- The pair is immutable **from the language's point of view**. A cell is
  uniquely owned and never shared, so an implementation may overwrite it in
  place on assignment; this is not observable because no operation exposes a
  cell pointer.
- A local or parameter of type `I` is a one-word pointer to a frame cell; a
  local or parameter of type `?I` is a one-word pointer that is 0 for nil or
  points to a frame cell. A stored `I` is a pointer to a heap cell; a stored
  `?I` is 0 or a pointer to a heap cell.
- The cell contains no owned value. Freeing a cell never frees the target.
  The target is owned elsewhere or not owned at all.
- **A frame cell never escapes.** It is referenced only by the local,
  parameter or temp that owns it. A by-value argument passes a pointer to a
  caller frame temp for the duration of the call; the callee copies the pair
  if it stores, appends, returns or copies it. Every destination that owns a
  value (field, element, global, `Result` payload, return) materialises a
  heap cell, either by copying the pair into a new cell or by transferring a
  fresh heap temp. A local copy into another local is a new frame cell. This
  is the invariant that lets the cell stay 16 bytes and the ABI stay one
  word.

### 4.2 Construction sources and destinations

A conversion happens at a **typed destination**: an expression position whose
static type is exactly an interface type (aliases transparent). There is no
`as` and no conversion operator. The destination can be:

- a `let`/`var` initializer;
- the right-hand side of an assignment to an interface-typed place (field,
  array element, global);
- an argument to an interface-typed parameter (function, method, interface
  member, `append` element);
- a constructor field of interface type;
- a return expression of a function/method whose declared return is interface
  typed;
- the value of `ok(...)` only when the payload expression is already an
  interface value (there is no expected type at `ok`, so `ok(&c)` cannot
  infer an interface; write a typed local first).

The sources:

| source expression | allowed | binding of the new value |
|---|---|---|
| same interface type `d` (copy) | yes | the source's binding (bound values), or unrooted (stored/returned) |
| same interface type through `?I` (copy) | yes | nil stays nil; a non-nil `I` widens to `?I` and keeps its binding (ordinary optional widening, not a conversion) |
| `nil` to `?I` | yes | nil, no binding |
| `&place` of record/closed `T` | yes | `borrowBind` of the place: roots + deeps + paths + writability |
| bare reference parameter/local of `T` (`r`, not `&r`) | yes | the reference's own binding |
| field/element reached through a reference (`r.f`, `r[i]`) | yes, already borrowed | the reference's binding plus the path |
| handle value `^T` (local, field, call, `handle(x)`) | yes | `singleBind(handleSym, true, "", true, nil)` |
| variant `V` whose parent closed type `C` implements `I` | yes (borrow/handle) | the parent's impl, one step; the data pointer is the variant object |
| owned record/closed/variant `T` | no | `cannot use owned 'T' as interface 'I': borrow it with '&' or make a handle with handle(...)` |
| optional `?T` | no | `cannot use '?T' as interface 'I': check it with '!= nil' first, then borrow the value with '&'` |
| array `[]T`, string, `Result[T]`, another built-in | no | `cannot use <t> as interface 'I'` (unsupported source; do not suggest `&`) |
| `?I` to `I` | no (narrow first) | `'<x>' may be nil: check it with '!= nil' first` |
| another interface `I2` | no | `cannot use interface 'I2' as 'I': interface values do not convert; convert from the original value` |
| `nil` to `I` | no | `an interface value cannot be nil; use ?I` |
| `I` to `?I` (widening) | yes | a copy of the pair; nil is not involved |
| `^T` whose `T` does not implement | no | the no-implementation error (`implMissing`) |

Resolution order matters: the same-type copy, another-interface error, handle
source, borrow source, optional source, array/string/Result source, and the
owned record/closed source are distinguished **before** any generic owned
message, so an array or `Result` never gets the "borrow it with `&`" dead end
the 0.7 compiler produced (review finding).

A handle conversion copies the token without reading the object, so a stale
handle field is caught by the first `RT_DEREF`. A borrow conversion calls
`RT_HANDLE`, which reads the object's header to capture its generation; a
hidden free before the conversion is the same residual class as a hidden
reference use, and the token guards frees after construction (section 4.6).

There is still **no interface-to-interface conversion**. A value's dynamic
type may implement several interfaces, but the pair has no type tag and the
type system is nominal; convert from the original concrete value.

### 4.3 Storage positions, initialisation and nil

| position | allowed | initialisation / default |
|---|---|---|
| record/closed/variant field `d: I` | yes | constructor must give it; `T needs a value for d (it cannot be nil)` |
| field `d: ?I` | yes | may be omitted; the zeroed field is nil |
| array `[]I` | yes | `new []I(0)` empty; `new []I(n)` with n>0 is `array elements of type I would start as nil; use []?I, or start empty with new []I(0) and append`; `append` stores a copy |
| array `[]?I` | yes | `new []?I(n)` has nil elements; append/assignment |
| global `var/let G: I` | yes | must have an initializer; the initializer is compiled in the global-init chain and may construct a cell; the cell is owned by the global and freed at teardown |
| global `var/let G: ?I` | yes | initializer may be `nil`; teardown skips 0 |
| function/method return `I` / `?I` | yes | returns an owned heap cell / 0 or a heap cell |
| interface-member return `I` / `?I` | yes | same; the vtable slot returns one word |
| `Result[I]`, `Result[?I]` | yes | `ok(d)` copies the value into a heap cell payload; `ok(nil)` needs a typed optional; `err` unchanged |
| `[]I` inside a record/array/Result | yes | nested aggregates use the same rules |
| `&I`, `mut &I`, `^I`, `?^I`, `new I` | no | the existing borrow/handle/constructor rules (`only records, closed types and arrays can be borrowed, not I`; `handles name records and closed types, not I`; `'I' is not a record type`) |
| constant `const X: I` | no | constants are scalars/strings only (existing rule) |
| `match`/`==`/`!`/`println`/`alive`/`handle`/`drop` on a value | no | type errors; `drop` needs an owned value and an interface value is copyable (like a string); `take`/`replace` apply to places (below) |

Record layout stays one word per field. A record with interface fields has
those field offsets in its descriptor's **owned pointer** list; the cell it
points to has no owned children, so the descriptor walk frees the cell and
stops. A record dereferenced through a handle is still writable (the existing
handle rule); assigning an interface field through a handle overwrites or
frees the field's cell, not the target.

obo has no array-literal syntax: `new []I(0)` plus `append` is the only
construction path for `[]I`, which is also why `new []I(n>0)` is refused.
A local `I` needs an initializer (`'<d>' of type I needs an initial value (it
cannot be nil)`); a local `?I` follows the existing optional-local rule and
starts nil with no initializer, or may be given `nil`.

An interface value is **not nil** when its type is `I`; `?I` is the optional.
The nil representation is the zero cell pointer, not an allocated nil cell.
`?I` narrows with the existing `!= nil` forms; the narrowed value is a
non-optional `I` view of the same cell (same binding). `take(o)` on a `?I`
place moves the cell pointer out and leaves 0; `replace(place, v)` on an
`I`/`?I` place returns the old value and stores the new one; `drop` on a local
interface value is refused because the local is a frame-cell view, not an
owned heap value. (`take`/`replace` on interface places is a small extension
of the existing owned-place helpers; section 6.3.)

### 4.4 Copying, `let`/`var`, assignment and aliasing

- **Copy is value copy of the pair.** `let e = d` copies the pair into a new
  frame cell; the source remains usable. A field/element/global read copies
  into a frame cell. A by-value argument copies into a caller frame temp. A
  constructor field / array append / Result payload allocates a heap cell and
  copies. A copy never shares a cell, so assigning over one value cannot
  change another value's pair.
- **Target sharing is intentional.** Two copies of one value name the same
  target (same token). A `mut` call through one is visible through the other;
  a visible free of the target invalidates both bindings if they are bound.
  This is the same target sharing a handle has, but with dispatch through a
  vtable.
- **`let` and `var`.** A `let` binding cannot be reassigned; a `var` binding
  can. `var d: I = source` is allowed; `d = e` copies `e`'s pair into `d`'s
  existing frame cell (allocating the cell at the declaration site even when
  the initial value is nil for `?I`) and updates `d`'s binding to `e`'s. A
  `?I` local assigned `nil` stores 0; a stored `?I` place assigned nil frees
  its old cell. The 0.7 "must be declared with let" error is removed.
- **Inference.** `let e = d` where `d` is interface-typed is an interface
  value copy, not a reference local. `let e = &b` remains an ordinary
  reference to `b`; `let e = b` remains an owned `b`. The inferred form must
  not take the reference-local branch merely because the source is an
  interface value; that branch created the 0.7 second-class copy.
- **Independence of the cell, dependence of the target.** A local copy of a
  field survives the field being reassigned (the pair was copied); its token
  still names the target, which may be alive or may trap. A copy of a value
  whose cell is freed by `truncate`/record free survives (frame cell copy);
  it owns no heap memory.
- **Freeing a cell is not freeing the target.** Reassigning a field, freeing a
  record, truncating an array of interfaces or freeing a `Result[I]` frees the
  interface cell(s), not the targets. References and other interface values
  into those targets remain valid; only an owner free/assignment of the
  target itself invalidates them. The compiler's invalidation/effect analysis
  must follow this distinction: an interface field is an owned **cell**
  pointer for destructor purposes but a non-owning **view** of the target for
  reference purposes. No `&I` exists, so no reference can point at a cell.
- **No moves.** Interface values never participate in `consume`/move bits and
  are never freed by `freeOwned`; they are copyable like strings. `drop`,
  `take` and `replace` are defined only where they make sense for a place or
  an optional; a local `let d` is not an owned value to drop.
- **Moving an owner is conservative.** Moving an owned target (`var e = c`,
  passing `c`, `drop(c)`) invalidates every bound interface value rooted at
  `c`, even though a move preserves the object under its new owner. The
  compiler does not follow the new owner; re-convert from the new owner
  (`let d2: I = &e`). This is the same known false rejection references have
  today, and it is safe: the value is not used while its provenance is
  unknown.

### 4.5 Cases: fields, arrays, globals, `Result`, returns, loops, defer

- **Constructor.** `new T{d: &c, o: nil}`: `d: I` gets a heap cell with the
  pair; `o: ?I` is 0. Omitted `d: I` is the existing missing-field error;
  omitted `d: ?I` is nil. A constructor field value is evaluated before the
  store; a later constructor field that frees the source does not change the
  copied pair.
- **Field assignment.** `r.d = v`: evaluate `v` to a cell pointer (frame or
  heap); if `r.d` has a heap cell, overwrite its pair (the field's cell is
  unique and unaliased); if `r.d` is nil (`?I`), allocate; if `v` is a fresh
  heap temp, transfer it. Assigning nil to `?I` frees the field's old cell.
  `r.d = &c` is a conversion at the field's typed destination; the new cell
  holds the token; the old cell is freed (the old target is not).
- **Arrays.** `append(xs, v)` allocates a heap cell for the new element and
  copies the pair (or transfers a fresh heap temp). `xs[i] = v` overwrites
  the element's existing cell in place; assigning nil to `[]?I` frees it.
  `truncate` and array free free element cells through the existing owned
  array kind. `new []I(0)` then appends is the supported growth path.
- **For loops.** `for x in xs` over `[]I` copies the pair into the loop
  variable's frame cell each iteration (a snapshot); the loop variable is a
  value, not a reference, and calls through it are unrooted (pure-only).
  `[]?I` yields a `?I` snapshot (0 or a frame cell) and needs narrowing.
  Appending during the walk is still allowed (append does not move elements);
  truncating or replacing elements frees/overwrites cells, but the loop
  variable's frame copy is independent; its target token is checked.
- **Globals.** A global interface value is a one-word cell pointer owned by
  the global. Global initializers run in declaration order in the init chain;
  they may allocate a heap cell and call `RT_HANDLE` (as `new T{...}` already
  allocates) or copy another global. The init chain is not a source function,
  so the global path calls `ifaceStore` directly with a heap cell and never
  needs a frame cell; the 0.7 "outside a function" rejection is removed for
  globals. A global may be `?I` and nil. The global teardown loop is extended
  to free interface-cell globals (a `?I` nil is skipped by `rt_free`).
  Assigning a global from a local-bound value is subject to the escape rule
  (section 4.6). A borrow of a global record may be converted; the token is
  checked on every dispatch.
- **`Result[I]`.** `ok(d)` allocates a result cell whose ok payload is a heap
  cell; the ok descriptor lists the payload offset as an owned pointer, so
  freeing the result frees the payload cell. `ok(nil)` needs a typed optional
  (`let n: ?I = nil; ok(n)`); `ok(&c)` has no expected interface type and is
  rejected. `match` on `Result[I]`:
  - non-consuming `ok { r.value ... }` copies the pair into a frame cell; the
    result still owns the payload;
  - consuming `ok(v)` moves the payload cell out; binding `v` to a local
    copies the pair into a frame cell and frees the payload cell (or transfers
    if stored immediately);
  - `err` and `try` behave as for any `Result`; `try` on `Result[I]` yields an
    owned heap-cell value which, bound to a local, is copied and freed.
- **Returns.** `return &c` / `return d` copies the pair into a newly allocated
  heap cell and returns its pointer; the callee's frame cells can die. If the
  expression is already a fresh heap temp (a function return, a transfer), the
  pointer is returned. A `?I` return may be 0. The caller owns the returned
  cell; binding it to a local copies the pair and frees the cell; storing it
  transfers. The return is **unrooted** at the caller. A call used as a
  statement is an owned temporary: the existing temporary/free rules free the
  return cell (the target is unaffected).
- **Defer.** `defer d.m()` uses the local's frame cell at block exit; the
  existing defer-use/replay/invalidation rules apply. If the target was
  visibly freed before the defer runs, the defer-use check refuses. A defer
  cannot store or move; storing a value in a defer body follows the ordinary
  rules if it is a call effect.
- **Loops.** Interface locals/copies use static frame slots; a copy site in a
  loop reuses its slot. No cell escapes unless stored. A `var` interface local
  reassigned in a loop is an ordinary value assignment; the loop binding
  checks apply to its target binding as they do to a reference local.
- **`?I` and `try`/`ok`/`take`/`replace`.** A narrowed `?I` is an `I` view;
  `take(o)` moves the cell out and nils; `replace(p, v)` returns the old pair
  and stores the new; `drop` is not available on interface locals. A `?I`
  method call requires narrowing first (`'o' may be nil: check it with
  '!= nil' first`).

### 4.6 Lifetime: the checked-view model

This is the central semantic decision. An interface value **does not keep its
target alive**. It is a checked view.

**Target binding.** A value may carry a compile-time **binding**: the same
`RefBind` used for references (roots, deeps, paths, writability), or the
handle root, or nothing (unrooted).

- constructed from `&place` / a bare reference: the source binding;
- constructed from a handle: a handle root (writable, no path);
- copied from a bound value: the same binding;
- copied from a stored place / read back: **no binding** (unrooted);
- returned: **no binding** at the caller (unrooted);
- a parameter: a root at the parameter symbol; the caller supplies and checks
  the actual argument binding (the 0.7 parameter model, reused).

**Compile errors (visible, in the same function).**

- A use of a **bound** value after a visible move, free, `drop`, assignment
  or change that reaches any binding root invalidates the binding, exactly as
  for a reference local: the existing `freedRefMsg` (`'<d>' may point to
  freed memory: something under what it points into was changed; point it
  again first`) is reported at the use. A `var` reassignment revives the
  value with a new binding. Copies of a bound value that are bound themselves
  are invalidated together.
- **Definite escape.** Returning an interface value whose binding has an
  **owned local or owned parameter of the current function** as a root is
  refused: the root is freed at return, so the returned token is certainly
  stale. The message is `cannot return an interface value that borrows '<x>':
  the target is freed when the function returns; keep the target in a
  longer-lived place or return a handle`. Storing a bound value into a global
  with such a root is refused for the same reason (`cannot store an interface
  value that borrows '<x>' in a global: the target is freed when the function
  returns`). Storing into a local's field/element is allowed (it dies with the
  frame); storing into a field/element reached through a parameter, handle,
  global or reference is allowed but the value becomes an unrooted checked
  view, because the compiler cannot prove the container's lifetime (residual,
  below).
- Past a **stored** position the binding is gone. Reading a field/element/
  global/`Result` payload, or receiving a return value, yields an unrooted
  value. A later visible free of the target is **not** a compile error for
  the stored value (the compiler no longer knows the relation); dispatch is
  the backstop.

**Runtime exit (the backstop).** Every dispatch loads the token from the cell
and calls `RT_DEREF`; if the target's generation no longer matches, the
program exits 7 with the existing message (`obo: the object this handle names
has been freed`). This is not a use-after-free: the generation check is the
same one handles use. It is the documented behaviour for a checked view whose
target died behind the compiler's back or after the value left its binding.
The cell itself never dangles: locals and parameters point into live frames;
stored/returned cells are uniquely owned and freed by their owners; a copy
owns its own pair. **The receiver token is dereferenced after the explicit
arguments have been evaluated** (section 5.3), so a hidden free in a later
argument is also exit 7 at the dereference rather than a freed object pointer
handed to the callee. This is a strengthening over the 0.7 call order.

**Residual limits, recorded not hidden.**

- A stored view of a target owned by a function-local object is a time bomb:
  the object dies at return and the first dispatch exits 7. The compiler
  catches the direct `return &local`/global-store case but cannot follow a
  view stored in a local record that is later returned or stored. This is the
  same class as a handle stored in a field after its owner dies; the token is
  the guarantee.
- A hidden free the compiler cannot see (a forged/aliased path, a handle
  write) is exit 7 at the next dispatch, never a read of freed memory.
- There is no `alive` on an interface value. Keep the handle used to construct
  it and call `alive(h)` if a probe is needed; the answer can go stale. Adding
  `alive(I)` is an extension.
- The **factory** consequence: `fn make(): I { let c = new Circle; return &c }`
  is a compile error (definite escape). A factory can return a view of a
  parameter, a global, a handle-backed target, or a stored value; it cannot
  return a view of a newly created local target. Returning a new object as an
  interface needs owning interface values (section 13). This is a genuine
  user-level fork (section 12.2).

### 4.7 Purity, effects and receiver provenance

The purity contract is preserved and extended from bindings to the new
provenance categories. The synthetic member summary is unchanged: a `pure`
member has `freesAll = false`, `writesHandles = false`, `pfree = 0`; an
impure member sets `freesAll = true` and `writesHandles = true` so callers
invalidate conservatively.

**Receiver rule table.** The receiver is **argument 0**. Its binding and
provenance are computed exactly like an explicit argument's; every root is
scanned.

| receiver | member | `mut` receiver mode | result |
|---|---|---|---|
| bound, whole-object, no handle root, no global root the member might free | pure | allowed | allowed |
| bound, whole-object, same | impure | allowed if `binding.writable`; global/handle/alias checks apply | allowed; `afterCall` effects apply (it may free below the receiver) |
| bound, **deep** (any root `deeps[p]` or non-empty path) | pure | allowed | allowed |
| bound, **deep** | impure | **refused** (the HIGH fix) | refused |
| bound through a **handle** (any depth) | pure | allowed | allowed if no owned argument (below) |
| bound through a **handle** | impure | refused | refused |
| bound into a **global** the member `mightFree` | impure | refused | refused |
| **unrooted** (stored, reloaded, returned, or `?I` narrowed from such) | pure | allowed (new capability) | allowed if no owned argument (below) |
| **unrooted** | impure | refused | refused |
| nil `?I` | any | compile error before (narrow first) | — |

Exact rules:

- **The receiver is argument 0.** `ifaceCall` records the receiver's binding
  and its `deepLine`/`deepCol` and feeds it into the same `writesHandles`/
  `freesAll` refusal used for explicit deep arguments. If any root is deep and
  the member is not pure, the call is refused at the receiver:
  `cannot call interface method 'I::m' on a reference inside something: it
  writes through a handle, which could free it`. This is the direct-call rule
  applied to the receiver. It is not an after-call invalidation: the callee
  holds and uses the receiver pointer **during** the call, so the refusal must
  happen before codegen.
- **Deep receiver plus pure member is allowed.** A pure member cannot free a
  handle, a global or below a reference parameter; it may write scalars or
  append, which does not free the receiver or its container. `pure mut` on a
  deep receiver is allowed if the binding is writable.
- **Handle-rooted receiver.** Any root `handleSym` requires `handleSafe(f)`
  (`pureFn(f) && !f.writesHandles`); an impure member is refused with the
  existing `cannot call interface method 'I::m' through a handle: it is not
  pure, and might free what the handle names`.
- **Unrooted receiver.** No binding means no proof that the target is alive
  during the call and no proof that the callee cannot free it. An impure
  member is refused: `cannot call interface method 'I::m' through an interface
  value that does not keep its target alive: it is not pure, and might free
  the target`. A pure member is allowed; the token still traps if the target
  is already dead.
- **Global root.** If any root is a global and `mightFree(f, g)`, the call is
  refused with the existing global message, including when the global is the
  receiver's container.
- **Writability.** For a bound receiver, a `mut` member requires
  `binding.writable`; a read-only reference or a field through a read-only
  reference is refused. For an unrooted receiver there is no binding; a
  `pure mut` member is allowed (the stored value is a new non-owning
  capability, like a handle; a read-only capability does not survive storage,
  and the handle model already allows writing through a stored handle). A
  `mut` **impure** member is refused through an unrooted receiver by the
  purity rule.
- **Accepted impure whole-object calls.** The callee may free **below** the
  receiver through a `mut` argument; `afterCall`/`mutFrees` invalidate deep
  references and record pfree events as today. The receiver object itself is
  not freed: it is not owned by the callee, and the caller's binding checks
  (plus the hold) prevent a later argument from freeing its owner. This part
  of the 0.7 effect model is retained; only the deep-receiver acceptance was
  wrong. This has the **same hidden-alias residual as a direct reference
  call**: a callee that writes through a handle held in a field or global
  could free an object the compiler cannot alias, and the receiver pointer
  would dangle during the call. The language already accepts that residual for
  references; a token check at dispatch entry cannot catch a mid-call free.
  A `pure` member has no such residual, and handle-rooted/unrooted receivers
  are pure-only precisely because they cannot rely on the caller's binding.
  A future runtime pin (section 13) would turn this residual into a checked
  exit.
- **Interface arguments.** The same provenance table applies to every
  interface-typed argument:
  - a bound, whole-object argument to an impure callee is checked against the
    callee's summary and the other arguments (hold/alias/global/handle);
  - a deep argument to a callee that `writesHandles`/`freesAll` is refused
    (`cannot pass an interface value that points inside something to <f>: it
    writes through a handle, which could free it`), for functions, methods and
    interface members alike;
  - a handle-rooted argument requires `handleSafe(f)` (existing);
  - an unrooted argument requires `handleSafe(f)` and the message `cannot
    pass an interface value that does not keep its target alive to <f>: it
    might free the target`;
  - `mut d: I` arguments need a writable binding for bound values; an
    unrooted argument is allowed only if the callee is handle-safe (its
    impure uses would make it unsafe), so it can call only pure/pure-mut
    members.
- **Owned arguments and handle-rooted/unrooted interface values.** A pure
  callee may free its owned parameters; that is how 0.7's purity proof stays
  usable. If the callee has an owned parameter that owns the interface value's
  target, it can free the owner during the call and then use the target. The
  caller cannot relate an owned argument to a **handle-rooted** or
  **unrooted** interface value's target, because those have no precise
  binding. Rule: **a call that passes a handle-rooted or unrooted interface
  value (as receiver or argument) may not pass any owned argument — a mode-0
  argument whose type is owned (`isOwned(t)`: record, closed, array, owned
  optional or `Result`), **including an owned receiver of a concrete method
  call** — regardless of argument order.** Strings, handles, scalars and
  interface values are copyable or non-owning and are not restricted by this
  rule. The message is `cannot pass an owned argument to
  <f> beside an interface value that does not keep its target alive: the
  argument might own the target`. This generalizes the 0.7 handle-rooted
  conservatism (where any owned move invalidated the held handle) and closes
  the earlier-argument hole; it also covers a fresh `new Owner{c: c}`
  temporary, which can own the target by moving `c`. For a
  **bound** interface value the precise binding checks apply instead: an
  owned argument that owns or is an ancestor of the target invalidates the
  binding and the call is refused by `consume`/hold/`argOrderCheck`; an
  unrelated owned argument is allowed. A `mut` reference argument that may
  free below itself makes the callee not `handleSafe`, so an unrooted or
  handle-rooted call is already refused; a bound call's alias checks apply.
  The cost is real and recorded: a stored interface value cannot be called
  with an owned record/closed/array/optional/Result argument in v1; pass a
  reference or a copyable value (int, string, handle), or move the argument
  into storage first. The deferred receiver dereference
  still protects against a free inside an argument *expression* (a nested
  pure call that frees its own owned argument), which is a separate order
  hazard.
- **Purity propagation unchanged.** A function that calls an impure interface
  member becomes impure (`freesAll`/`writesHandles`), so passing a
  handle-rooted or unrooted value to it is refused; a function that calls only
  pure members stays pure. The `pure` implementation check is still
  `pureFn(method) && !method.writesHandles`.
- **`handleSafe`.** Keep `pureFn(f) && !f.writesHandles`. It is redundant with
  compiler-generated summaries (both flags are set together for the base
  cases), but a forged source-less `.sym` could set `writeshandles` without
  `freesall`; the check is cheap defence and is pinned by a fixture (section
  7.5). If review prefers, record it as equivalent with that reason; the
  recommendation is to keep and test it.
- **Classifying a receiver.** A local/parameter value's provenance is a
  per-path property, like a reference binding: it is set at construction,
  preserved by copies, updated by `var` assignment, and joined at control-flow
  merges (a value bound on one path and unrooted on another is unrooted). A
  `?I` narrowed from a bound/optional inherits the optional's provenance. A
  stored read/returned value is unrooted. The existing binding-join machinery
  is reused; the implementation adds an interface-value local category. It is
  not a reference local in the ownership sense: the value is copyable, its
  frame cell is never freed by `freeOwned`, and the value itself remains
  usable after a target change. Only its **target binding** becomes stale; a
  method call through a stale binding is refused, and a `var` reassignment
  revives it.

### 4.8 Method receiver modes and mutability

The interface member markers remain `fn`, `mut fn`, `pure fn`,
`pure mut fn`, in that order. A member without `mut` has a read-only target;
with `mut`, a writable target. There is no owned receiver and no `move`
receiver: an interface value never owns the target, so it cannot consume it.

- Bound value: writability is the binding's `writable` bit (whole local or
  writable parameter: writable; read-only reference: not; field/element
  inherits; handle root: writable).
- `mut d: I` parameter: writable target inside the callee; the caller checks
  the argument's writability. `d: I` is read-only; passing a writable value is
  a legal downgrade. `mut ?I` is not a spelling: a `?I` parameter is a
  copyable optional value (mode 0), and the callee narrows it before a `mut`
  member call.
- Unrooted value: no binding; `pure mut` allowed (new capability), `mut`
  impure refused by purity.
- The receiver never reassigns the interface value; `mut` is about the
  target, not the cell. A `var` local is reassignable with ordinary `=`,
  which is separate.

### 4.9 Does the interface value own the target?

**No.** This is the single most important sentence in the note. A cell owns
nothing; a record field/array element/global/Result payload owns its **cell**,
not the target. Freeing a value frees at most the 16-byte cell; the target is
freed by its owner, or traps at the next dispatch if it was freed. This is
what keeps the design inside single ownership and no GC. It is also why
impure dispatch through stored values and factories returning new targets are
limited; sections 12 and 13 record the extensions.

---

## 5. Dispatch and vtables

### 5.1 Static versus dynamic, receiver forms

| receiver expression | resolution | code |
|---|---|---|
| record/closed/variant concrete value | static method call (methods phase) | direct `E8 rel32` |
| interface-typed local/parameter/field/element/global/`Result` payload | dynamic: slot from the interface, code from the cell's vtable | load cell pair, `RT_DEREF`, indirect call |

The receiver of an interface call may be **any designator of interface type**,
not only a bare local/parameter: `r.d.m()`, `xs[i].m()`, `G.m()`,
`lib::G.m()`, `h.d.m()` (a field through a handle), a narrowed `?I` `o.m()`,
and a payload `r.value.m()` in a `Result` arm. A call result of interface
type may also be a receiver (`make().m()`), like a handle-valued result: the
compiler copies the pair into a receiver temp and frees the returned cell
after the copy, so the existing "store this value first" rule for owned
record/array results does not apply to an interface result (the return cell
owns nothing). The designator is evaluated to a cell pointer; the compiler
copies the pair into a hidden receiver temp frame cell (a snapshot) before
evaluating the explicit arguments. This is required for value semantics: a
later argument that assigns over the receiver's local/field must not change
the pair the callee sees. The receiver binding is registered as argument 0
and held across the later arguments. A field/element/global receiver is
**unrooted** for purity (its target is not the field), so it can call pure
members only; this is the price of storage and is documented.

A method call still ends the designator: `d.m().n` and `d.m()[0]` are syntax
errors at the enclosing construct. `d.name` without `(` is the method-value
error. `d.field` on an interface value is `interface 'I' has no fields; it has
methods only`. `?I` requires narrowing before `.name(`.

### 5.2 The implementation registry and the vtable

Unchanged from 0.7: one `Impl` per `(receiver type, interface)`; slots in
interface declaration order; each slot is the concrete method's final code
address; methods must be **defined** before the `impl` so `dropFrame` has
already run; a variant uses the closed parent's vtable and methods; an
implementation must be declared in the receiver type's own module; a `pub
impl` requires exported interface and receiver types; a private impl is not
written to the `.sym`; `findImplFor` is visibility-filtered and mode-
independent; no address ever enters a `.sym`. A downstream module can
implement an interface for its own type and pass a value to a dependency's
function; the vtable travels in the cell and the dependency loads it.

### 5.3 The call

`ifaceCall(recv, name, ...)`:

1. Resolve `findIfaceMethod(recv.typ, name)`; a miss is the no-method error.
2. Evaluate the receiver designator to a pair; copy the pair into a hidden
   receiver temp frame cell; save the cell pointer in a hidden slot; record
   the receiver binding and its line/col.
3. Check `mut` receiver mode against the binding's writability (or the
   unrooted pure-mut rule); record the write.
4. Register the receiver binding as argument 0 and hold it.
5. Reserve argument 0's stack slot (a placeholder) and evaluate/push the
   explicit arguments left to right exactly like a direct call: owned
   arguments consume; `&`/`mut &` arguments borrow and are held; interface
   arguments convert at the parameter's typed destination and carry their
   bindings; every argument enters the deep/handle/global/alias/order checks.
6. Apply the receiver-as-argument-0 checks: deep + impure -> error; handle
   root + non-handle-safe -> error; global root + `mightFree` -> error;
   unrooted + impure -> error. This is the HIGH fix. These checks use the
   statically known binding and are done before the call.
7. **After all arguments have been evaluated and pushed**, reload the
   receiver cell from its hidden slot, load the vtable, load the token, call
   `RT_DEREF`, and store the object pointer into the reserved argument-0
   slot. Deferring the dereference until after the arguments is deliberate:
   a hidden free in a later argument now exits 7 at the dereference instead
   of leaving the callee a freed object pointer. The visible-free checks
   (hold/argument order) still refuse what they can see; the deferred deref
   is the backstop for what they cannot. The argument stack is complete, so
   the runtime call's padding must account for the pushed words (a
   codegen risk; see section 11.3).
8. Apply effects: impure mut receiver invalidates references below every
   root and records pfree events; accepted impure whole-object receiver
   invalidates deep references; `afterCall` propagates summaries; pure calls
   leave references valid.
9. Emit `call qword ptr [vtable + 8*slot]`, with the existing stack padding
   and register save/restore. A `I`/`?I` result is an owned heap cell (or 0)
   owned by the caller; a stored-into destination may transfer it.
10. Return an item with the member's return type and result ownership; a
    method call ends the designator.

The receiver temp is a frame cell; the callee's parameter 0 is the target
object pointer obtained by `RT_DEREF`, so the member ABI is the existing
method ABI (`p0` = `&T`/`mut &T` object pointer), not a cell pointer. The
interface value's cell is only the dispatch descriptor. Interface-typed
**member parameters/returns** are one-word values (cell pointers) like any
other interface parameter; the vtable slot's signature is the member's own.

**Fallback if the deferred dereference is judged too invasive.** Dereference
the token before the arguments as 0.7 does, and hold an **unrooted** receiver
as an unknown root (`singleBind(handleSym, ...)` would be the conservative
spelling): any later argument whose evaluation may free anything (a
possibly-freeing call, an owned free/move, truncate, take/replace, a
handle/global write) invalidates the held receiver and is refused with
`argument N of interface method 'I::m' may free what the receiver points
into, and runs after it; compute it before the call`. The deferred
dereference is recommended because it is stronger (hidden frees become exit
7) and more permissive (a pure call through a stored value may have a freeing
argument if the target survives); the fallback is sound but conservative.
Either way, the receiver's binding/hold checks and the purity table above are
required.

### 5.4 Performance

- Construction from a borrow: one `RT_HANDLE`, two stores into a frame cell
  (no heap allocation for a local).
- Construction from a handle: two stores.
- Copy to a local/argument: a 16-byte pair copy into a frame cell.
- Store/return: one `RT_ALLOC_RECORD` (a 16-byte cell) plus a pair copy; free
  with the owning record/array/global/Result/return temp via the existing
  `RT_FREE`.
- Dispatch: one `RT_DEREF` (bounds/generation check), one vtable load, one
  indirect call. No devirtualisation in v1.
- No refcount operations, no new runtime slot, no new descriptor format, no
  per-call heap allocation. The per-store allocation is the main new cost;
  it is bounded and local.
- A pure member lets callers keep references valid and accepts deep/unrooted/
  handle receivers; an impure member is intentionally conservative. Code
  that needs precision should expose a pure member where the implementation
  genuinely is pure.

---

## 6. Compiler architecture

### 6.1 Data model

`Type.kind == "iface"`; interface types keep the 0.7 shape: owner, name, uid,
empty fields/variants, `desc = 0`, incomplete until the definition is parsed.
`ifaceSyms` (synthetic members) and `impls`/`implArena` (implementations) are
unchanged. Helpers `ifaceMethodsOf`, `findIfaceMethod`, `ifaceMethodDisplay`,
`ifacePure`, `implKeyOf`, `implDisplay`, `implBase`, `findImplIn`,
`findImplFor`, `implMissing`, `handleSafe` are reused.

New/changed classifiers:

- `nonNull(iface)` is **true** (for `?`, `new []T(n)` and missing-field
  checks); `nonNull(?I)` is false as usual.
- `isOwned(iface)` stays **false** (an interface value is copyable, not a
  move-owned local). A new helper `ownsCell(t)` is true for `iface`, and for
  `opt`/nested constructors whose base owns a cell; it drives:
  - record descriptor owned-pointer offsets (`fieldList`),
  - array element kind (`newExpr`: kind 2 for owned elements),
  - `okVariantOf` payload ownership,
  - global teardown,
  - `take`/`replace` place checks where a cell is owned.
  This distinction is essential: making `isOwned(iface)` true would put
  interface locals into `freeOwned` and `consume` as move-owned records,
  which is wrong for copyable values.
- `same(I, I)` is pointer identity; aliases transparent. `assignable(I, I)`
  is true; concrete-to-interface and handle-to-interface remain false so only
  a typed destination performs a conversion. `assignable(?I, nil)` is true.
- `tname(I)` is owner-qualified across modules; `[]I`, `?I`, `Result[I]`
  display normally.

### 6.2 Declarations and type formation

Reuse the 0.7 contextual `interface` branch after the `=` of a `type`
declaration and the `impl` branch at top level. `type`, `for` and `fn` remain
**keyword tokens**; only `interface`, `impl`, `pure` and `mut` are contextual
identifiers (the docs must not call `type`/`for` contextual). The interface
type symbol is declared before its members so a member may name the interface
itself; a forward interface shell is still rejected; two interfaces still
cannot mutually reference each other in v1 (no forward shell), but a member
may return its own interface (`fn next(): I`) or reference it in a parameter.

Type formation changes:

- `parseType`/`symParseType`: `?I` is allowed (`nonNull(I)` is true);
  `[]I` and `Result[I]` are allowed; `^I` stays rejected by the handle rule;
  `&I` stays rejected by the reference rule (an interface is not a borrowable
  owned record/array). `symFinishTypeName` keeps the `::`-before-primitives
  order.
- `fieldList`/`symFieldList`: interface fields are allowed; the descriptor
  records an interface field in the owned-pointer list; a `?I` field is
  optional/nilable and may be omitted.
- `varDecl`/`symGlobalDecl`: interface locals and globals are allowed; `var`
  is allowed; a global needs an initializer unless it is `?I`; the initializer
  is compiled in the init chain.
- `fnDecl`/`symFnDecl`, interface members: interface parameter types and
  return types are allowed; interface params are mode 1 (read-only target) or
  mode 2 (`mut`); a return may be `I`, `?I`, `[]I`, `Result[I]` or a named
  record containing interfaces, subject to declare-before-use. A function
  type as an interface member parameter/return stays rejected (lambda
  firewall).
- `newExpr`: `new I` is rejected (not a record); `new []I(n)` uses the
  non-null element rule; `new []?I(n)` is allowed.
- `ok`: an interface payload is allowed only when the value is already
  interface-typed (no destination inference); `ownsCell` drives the ok
  descriptor; `ok(nil)` still needs a typed optional.
- `relation`, `match`, `println`, `alive`, `handle`, `drop`, indexing:
  interface-specific errors as in section 9.
- `closureCheck`/`checkExports`: interface fields/arrays/returns/params are
  traversed; an exported interface/impl still requires exported constituent
  types; a self-referential interface terminates via `closureSeen`.

### 6.3 The value layer

New/rewritten helpers (names indicative; the implementation chooses):

- `ifaceFrameCell(pair)`: allocate two hidden frame slots, store token and
  vtable, return a one-word item pointing at the cell (the 0.7 `ifaceCell`
  shape, now used for locals/parameters/temps).
- `ifaceLoad(src)`: copy a stored cell's pair into a fresh frame cell; the
  source may be a frame cell or a heap cell.
- `ifaceStore(dst, src)`: copy a pair into a destination's heap cell; free the
  old cell for a nil/incompatible destination; transfer a fresh owned heap
  temp when possible; overwrite an existing uniquely-owned cell in place.
- `ifaceReturn(src)`: allocate a heap cell and copy the pair, or transfer a
  fresh heap temp; return the pointer. Zero for `?I` nil.
- `ifaceValue(dst, x, what)`: the conversion helper with the source table in
  §4.2. It returns an `IfaceVal { item, bind }`; the item is a frame cell
  pointing at the pair (or a fresh heap temp in a store/return path), and the
  bind is the target binding (or nil).
- `ifaceCellDesc`: a single 16-byte descriptor emitted lazily before the first
  allocating site (`descriptor(16, [], [])`); the cell is allocated with
  `RT_ALLOC_RECORD` and freed by the existing `RT_FREE`. In `--check` code is
  discarded; the vtable is a dummy and no cell is executed.
- `ownsCell(t)` and the destructor hooks:
  - record/closed descriptor: interface field offsets go in the owned-pointer
    list; `rt_free` frees each field cell (no target traversal);
  - arrays: kind 2 for `I`/`?I` elements; `rt_free` frees each non-zero element
    cell;
  - `Result`: `okVariantOf(base)` lists the payload offset when `ownsCell(base)`;
    `rt_free` frees the payload cell;
  - globals: the teardown loop frees interface-cell globals;
  - returns: the caller owns the returned cell; `freeOwned`/temporary cleanup
    frees it after a copy or transfers it.
  No runtime change is needed: the cell is a plain record with no owned
  children; `rt_free` frees it and stops.
- Optional helpers: zero is nil; `take`/`replace` on interface places reuse
  the owned-place machinery with `ownsCell`; `drop` remains for owned values
  only.
- For loops: an interface element copies the pair into the loop variable's
  frame cell (the loop variable is a value, not a reference); `?I` copies 0
  or the pair.
- `freeOwned` skips interface locals (they are frame cells, not owned heap
  values); the local's frame cell needs no free.
- `compileProgram` teardown: free interface-cell globals with `RT_FREE`
  (zero is safe); do not free the target.

### 6.4 Binding, invalidation and effects

- Interface-typed locals and parameters carry a target binding (roots, deeps,
  paths, writable) or nil. They are a new local category:
  - they are added to the local/path state used by loop joins and invalidation
    (so a visible free can mark them stale), but they are **not**
    `isRefLocal` in the ownership sense: they are copyable values, not
    borrows, and `freeOwned` must skip their frame cells;
  - a copy preserves the binding; a `var` assignment replaces it with the
    RHS binding and participates in path joins;
  - a read of a stored place / return value produces an unrooted item.
- `invalidate`/`invalidateDeep`/`markSuspects`/`loopBindCheck` are extended to
  this category: a hit marks the binding stale; a later use reports
  `freedRefMsg`. A `var` reassignment revives it. The loop/defers rules are
  reused.
- `ifaceCall`:
  - receiver evaluation to a snapshot temp;
  - receiver-as-argument-0 checks: deep + impure, handle root + non-handle-
    safe, global root + `mightFree`, unrooted + impure;
  - `mut` writability (or unrooted pure-mut);
  - argument conversions and the existing hold/alias/order/handle/global
    loops, with interface arguments carrying bindings;
  - effects: impure mut receiver pfree/invalidation per root; accepted impure
    whole-object afterCall; pure calls leave references valid.
- Interface parameters: the parameter symbol is a root (writable for mode 2);
  the caller checks the argument's binding. The owned-argument rule (§4.7) is
  enforced in the call's argument loop: if any interface receiver/argument is
  handle-rooted or unrooted, a mode-0 argument is refused before codegen;
  bound values rely on `consume`/hold/`argOrderCheck` for the precise
  owner/ancestor case.
- `afterCall`, `pureFn`, `mutFrees`, `callFrees`, `recordParamFree`,
  `holdArg`, `argOrderCheck` are reused with interface labels. Function-call
  messages must remain byte-identical; only interface calls use the interface
  wording.
- `pureFn`/`handleSafe` unchanged; the `pure` impl check unchanged.

### 6.5 Designator and call plumbing

`designatorT` must accept an interface-typed field/element/global/`Result`
payload as a value and then `.name(` as an interface call. The receiver is no
longer required to be a bare local/parameter. The designator tail after a
method call is not applied (a call ends the designator), as for methods. A
method reference without `(` is the method-value error; a field-style access
on an interface value is the no-fields error.

### 6.6 Symbol writer/reader

Reuse the 0.7 interface definition (`type I = interface { ... }`), impl group,
`appIface`/`appIfaceParams`, `appTypeDef`, `appImpl`, `symIfaceDef`,
`symImplDecl`, order lists and mode-0 comparison. Changes:

- `appIface`/`appIfaceParams` emit interface-member returns and parameter modes
  exactly (`p0: &T`, `mut p0: &T`, `p0: I`, `mut p0: I`, `p0: ?T`, `p0: []T`,
  `p0: Result[int]`, a foreign `lib::T`); `appTypeDef` emits interface fields,
  `?I`, `[]I`, `Result[I]`; `appFn`/`appMethod` emit interface returns.
- `symParseType` accepts `?I`, `[]I`, `Result[I]`; `symFieldList` accepts
  interface fields; `symGlobalDecl` accepts interface globals;
  `symFnDecl`/`symIfaceDef` accept interface params/returns; the `^I`/`&I`
  rejections remain; a function type in an interface member remains rejected.
- Mode 0 compares every member name, receiver mode, purity, parameter type,
  parameter mode, return type and order; the parameter-mode indexing must be
  exercised by the committed fixtures (section 7.4). `--reemit-sym` must be
  byte-identical for all of them.
- The interface hash is unchanged in kind: interface member signatures and
  exported impls are interface data; adding an interface field/global/return/
  Result/optional changes the module body hash and every dependant's
  `depiface`. Private impls and bodies change only the source hash.
- No address (vtable, cell, method) is ever written. `--check` reconstructs
  the types and uses dummy vtables; a source-less dependency with interface
  storage/returns checks with exit 0.

---

## 7. Symbols, diagnostics scope and versioning

### 7.1 Representation

```text
type Drawable = interface {
    pure fn area(): int
    pure fn name(): string
    pure mut fn scale(p0: int)
    fn link(p0: Drawable): Drawable           // v2: interface parameter and return
}
type Holder = ref record { d: Drawable; maybe: ?Drawable; items: []Drawable; r: Result[Drawable] }
impl Drawable for Shape
fn make(): Drawable
var Current: ?Drawable
```

- Members are in declaration order, `;`-separated, parameter names canonical
  `p0`, `p1`, ...; the receiver is not in the textual signature. Markers are
  `pure` then `mut` then `fn`.
- An interface-typed parameter is `pN: I` / `mut pN: I` (one word; no `&`); a
  reference parameter is `pN: &T` / `mut pN: &T`; a return is a normal type
  expression. An interface return is written `: I` (or `: ?I`, `: []I`,
  `: Result[I]`).
- Interface definitions have no shell; the definition line registers the
  type. A record field/global/return of interface type is an ordinary type
  expression. The impl line is unchanged.
- No cell layout, vtable address, method address or effect summary of an
  interface member appears in a `.sym`. The interface member's purity is the
  `pure` marker; its synthetic summary is reconstructed.

### 7.2 Writer and reader

The 0.7 writer/reader are reused with the position and return changes in
§6.6. The canonical group order is unchanged (shells for records/closed only;
type definitions; aliases; constants; globals; methods; implementations;
functions). `symText` hashes the body; a private impl is not emitted and
changes only the source hash. `--reemit-sym` reconstructs and re-emits
interfaces, impls and all new positions byte-identically.

### 7.3 Type identity and cross-module use

The interface `^Type` is the key; aliases are transparent; two structurally
identical interfaces are distinct; a conversion is keyed by the canonical
type; a variant uses the parent closed type's implementation; `findImplFor`
is visibility-filtered identically in a full build and `--check`. A module may
implement an interface for a type it owns; a downstream module may implement
for its own type and pass a value to a dependency; transitive access works.
A stored interface field/array/global/Result in a module's `.sym` is just a
type; the impl may be in a downstream module, and the vtable travels in the
cell at run time. No address in the `.sym`.

### 7.4 The committed interface-member signature fixture (MEDIUM finding)

The 0.7 branch had no committed `.sym` fixture for interface-member parameter
modes or non-`int` signatures; a writer mutant that dropped `mut` survived
all gates. V2 must commit a dedicated tree and mode-0/reemit tests:

- `tests/modules/sym/iface_sig/lib.obx` with a `pub` interface whose members
  cover, in one declaration order (a sibling `dep.obx` exports `T2`, so the
  writer has a genuinely foreign type to qualify as `dep::T2`; a module does
  not qualify its own types):
  - `pure fn m0(p0: &T, p1: int): T` — a read-only reference parameter, a
    non-`int` record return;
  - `pure mut fn m1(mut p0: &T)` — a `mut` reference parameter, void;
  - `pure fn m2(p0: I, mut p1: I): string` — interface parameters, read-only
    and mutable, a string return;
  - `fn m3(p0: ?T, p1: []T): Result[int]` — optional, array, `Result`
    parameters;
  - `fn m4(p0: dep::T2): dep::T2` — a foreign/qualified parameter and return;
  - `pure fn m5(other: I): I` — a self-referential parameter and interface
    return;
  - `fn m6(p0: ?I, p1: []I): ?I` — optional and array interface values in a
    member signature (mode 0, one word each);
  - a no-parameter `fn m7()` (void) and a `mut`/`pure` combination for each
    receiver mode.
- `tests/modules/sym/iface_sig/main.obx` imports lib, converts a borrow and a
  handle, and calls the members with the right modes; `symdir/lib.sym` and
  `symdir/main.sym` are committed; `--check` main is exit 0; `--reemit-sym`
  is byte-identical; a full build (not only `--check`) reads the freshly
  written `lib.sym` in mode 0 and compares every member, so a writer that
  drops `mut`, drops `&`, changes an interface parameter mode, changes a
  return type or reorders members fails.
- `sym_test.py` `RICH_LIB`/`RICH_MAIN` gain the same signatures (the 0.7
  RICH_LIB only had `int`/void members), plus a private impl explicitly
  declared (do not repeat the 0.7 claim that RICH_LIB had one), an interface
  field/array/global/return/`Result`/optional, and a source-less check.
- Mutants (section 10.8): writer drops `mut` on a member parameter; writer
  drops `&`; writer drops `p0: I`/`mut p0: I`; writer swaps two parameter
  modes; writer drops or changes a return type; writer drops an interface
  field/array/global/Result; reader ignores a member mode/type/return;
  mode-0 indexing regresses. Each has a named catcher; no mutant is accepted
  as equivalent unless it is mode-0-only and the full build's re-read makes it
  unobservable (record the reason).

### 7.5 `handleSafe` defence fixture

Keep `handleSafe(f) = pureFn(f) && !f.writesHandles` and test it with a
hand-written source-less dependency:

- `tests/modules/sym/forged_writeshandles/symdir/lib.sym` declares an exported
  interface `I`, an exported function `f(p0: I) writeshandles` (no `freesall`),
  and nothing else; `main.obx` converts `handle(x)` to `I` and calls
  `lib::f(d)`.
- `--check main` must exit 1 with the handle-rooted interface argument error.
  Without the `!writesHandles` conjunct, `pureFn(f)` is true (no freesAll, no
  freed globals, no pfree) and the call would be accepted; the fixture pins
  the defence.
- If the reviewer prefers recording the conjunct as equivalent, the reason is
  that compiler-generated summaries set `freesall` and `writeshandles`
  together for every base case; the forged-file case is the only observable
  difference. Recommendation: keep and test.

### 7.6 Version and format decision

**Recommendation: stay `OBO_VERSION "0.7"`, `SYM_FORMAT 3`, `SYM_ABI 1`.**
Rationale:

- 0.7 was never merged or promoted; the seed is 0.6 and no user has a 0.7
  artifact contract. Reusing the unreleased number avoids a second interface
  version and leaves the lambda plan untouched.
- The `.sym` **grammar** is unchanged: interface definitions and impl lines
  already exist; the new storage positions use existing type expressions
  (`?I`, `[]I`, `Result[I]`, an interface return). The change relaxes which
  positions are legal, and a 0.7/format-3 file written by the paused compiler
  never contains an interface field/array/global/return/Result/optional (the
  paused compiler rejected them), so every paused file is a subset the new
  reader accepts. A new-format-3 file with an interface field is rejected by
  the old compiler only in its semantic checks; the old compiler is unmerged
  and is not a compatibility target.
- `SYM_ABI` stays 1: one-word parameters/returns/fields/elements/globals,
  record layout one word per field, array element size 8, the same
  descriptors (the cell is an ordinary record descriptor; interface fields
  are ordinary owned pointer offsets), and no new runtime call. The cell
  layout is internal and format-gated; it never crosses a `.sym`. The runtime
  slots are unchanged (no new slot is needed because the cell uses
  `RT_ALLOC_RECORD`/`RT_FREE`).
- The paused branch's 76 `.sym` fixtures and 3 goldens are **replaced**, not
  migrated: the v2 branch regenerates them after the value-layer change.
  The version fixtures mutate from `obosym 3`/`abi 1`/`compiler "0.7"` to
  format 4, ABI 9 and compiler "9.9" respectively. `--version` prints
  `obo 0.7 sym-format 3 abi 1`.
- **Fallback if review requires a format bump:** move to `0.8`/format 4, leave
  ABI 1, migrate every fixture and version table to the new base, and shift
  the lambda design to Stage A `0.9`/format 4 and Stage B `1.0`/format 4. That
  is a version-only revision of the lambda note; its semantics do not change.
  This is a genuine review fork; the recommendation is to avoid the churn
  because 0.7 is unpromoted.

### 7.7 Diagnostics scope

Interface-member signature errors must be exercised in both the source parser
and the reader; the exact messages are in section 9. The `.sym` reader must
reject `^I`, `&I`, a function type in an interface member, an interface
partial definition, a duplicate member, a bad mode marker and a mode-0
disagreement naming the member. It must accept the new storage positions and
interface returns in both full and `--check` modes.

---

## 8. Interaction with lambdas

The lambda design (`obo/design/lambdas-design.md`) is a separate feature in
flight. Its v1 firewall excludes:

- interface types in function types (parameters and returns) and in lambda
  headers (`containsIface`);
- capturing an interface value in a closure;
- function types in interface member signatures;
- interface values as function-value parameters/returns (ordinary functions
  may take interface parameters).

**Recommendation: keep the firewall for v1; the lambda design does not need a
revision before Stage A.** With first-class interfaces the excluded cases
become *possible* but not free:

| case | why it is possible now | why it stays out in v1 |
|---|---|---|
| function type taking `I` | an interface value is one word (a cell pointer); the hidden lambda parameter would be one word | the lambda body is compiled once and pure; an interface parameter is unrooted inside the body (the caller's binding is not in the function type), so it could call pure members only; the caller's argument checks would need the lambda's purity invariant plus the interface provenance rules; this is a new cross-feature contract |
| function type returning `I` | the indirect call returns one word; the lambda could copy a parameter or a stored value into a heap cell | the lambda body would have to allocate and transfer a cell; the function-type ABI's return ownership and the caller's cleanup need a rule; the lambda's purity check would have to allow the allocation and the copy |
| `&I` parameter in a function type | — | `&I` is not a type in v2 (references only to owned record/closed/array); it is rejected by the existing borrow rule |
| capturing an interface value | a capture cell could store a snapshot pair (16 bytes) or a copy of a cell pointer | captures are read-only and call-scoped; an interface capture would need its cell copied into the environment (not a pointer to a `var` local), plus the binding/provenance rules; strings are rejected for the same lifetime reason and interfaces should settle first |
| interface member taking/returning a function value | the member grammar could accept `fn(...)` | this changes the interface signature grammar and `.sym` encoding, and an interface member implemented by a function value would need the lambda purity/ABI contract; it is a separate feature |

The firewall is enforced in the same places as the lambda design says:
`containsIface` in the function-type constructor and lambda header (source and
reader), the capture resolver, and the interface member parser/reader. V2
must not accidentally relax them: an interface field/array/global/return does
not make an interface type legal inside a function type. The lambda design's
purity invariant (every function value is pure) remains compatible: an
interface value with an unrooted binding can call only pure members, and a
lambda body cannot free a global or write through a handle.

The lambda design's version baseline (0.7/format 3 -> 0.8/format 4 for Stage
A) is unchanged under the recommended version decision. If the format-bump
fallback is taken, the lambda note needs a one-line version update (Stage A
0.9/format 4, Stage B 1.0/format 4); its semantics do not change. A later
extension (after both features promote) can add interface parameters/returns
to function types, read-only interface captures, and function types in
interface members, in that order, with a new design note.

---

## 9. Diagnostics

Style follows `LANGUAGE.md` §10: lower-case, single quotes around names, no
trailing period, types through `tname`, interface methods as `I::m`
(module-qualified when foreign). A function-call argument label uses the
**bare** function name (`argument N of f`); a method or interface call uses
the quoted display (`argument N of method 'T::m'`, `argument N of interface
method 'I::m'`). The 0.7 design quoted `<f>` for functions; v2 matches the
compiler's existing `callDisplay`/`argLabel` formatting.

### 9.1 Declarations and types

| situation | message |
|---|---|
| empty interface | `an interface needs at least one method` |
| duplicate member | `interface 'I' already has method 'm'` |
| bad member modifier order | `'pure' and 'mut' come before 'fn'` |
| `pub` on a member | `'pub' is not allowed on an interface member; mark the interface type pub` |
| forward interface shell | `an interface cannot be forward-declared: 'I' is already declared` |
| duplicate definition | the existing `'I' is already declared in this scope` |
| interface borrowed/handled | the existing `only records, closed types and arrays can be borrowed, not I`, `handles name records and closed types, not I` |
| `new I` | the existing `'I' is not a record type` |
| `^I`, `&I` | the existing handle/borrow errors |
| interface field/array/Result/global/return | **now allowed** (positive tests; no rejection) |
| `I` value nil | `an interface value cannot be nil; use ?I` |
| local `I` without an initializer | `'<d>' of type I needs an initial value (it cannot be nil)` |
| missing `I` field | the existing `T needs a value for d (it cannot be nil)` |
| `new []I(n>0)` | the existing `array elements of type I would start as nil; use []?I, or start empty with new []I(0) and append` |
| function type in an interface member | `an interface method cannot use a function value; function types are not supported in interfaces` (lambda firewall) |
| interface in a function type / lambda header | `a function value cannot take or return an interface value` (lambda firewall) |
| interface capture | `cannot capture interface value '<x>'; function values cannot take interface values in v1` |

### 9.2 Conversion

| situation | message |
|---|---|
| owned record/closed/variant source | `cannot use owned 'T' as interface 'I': borrow it with '&' or make a handle with handle(...)` |
| optional source | `cannot use '?T' as interface 'I': check it with '!= nil' first, then borrow the value with '&'` |
| `?I` to `I` without narrowing | `'<x>' may be nil: check it with '!= nil' first` |
| array/string/`Result`/built-in source | `cannot use <t> as interface 'I'` (unsupported source; no `&` hint) |
| another interface | `cannot use interface 'I1' as 'I2': interface values do not convert; convert from the original value` |
| nil to `I` | `an interface value cannot be nil; use ?I` |
| local receiver, no visible impl | `type 'T' does not implement interface 'I'` |
| foreign receiver, no exported impl | `module 'M' has no exported implementation of 'I' for 'T' (only 'pub' implementations are visible)`; a variant names the parent closed type |
| unknown member | `interface 'I' has no method 'm'` |
| member as a value | `interface method 'I::m' cannot be used as a value: call it with '(' after its name` |
| field-style access | `interface 'I' has no fields; it has methods only` |
| mut member on a read-only value | `the receiver of interface method 'I::m' needs a mut interface value, but '<d>' is read-only` |
| arity / argument type | the existing call wording with the interface display |
| read-only interface argument to `mut d: I` | `argument N of f needs a mut interface value, but this one is read-only` (bare `f` for a function; quoted display for a method/interface method) |

### 9.3 Purity and lifetime

| situation | message |
|---|---|
| impure member through a handle | `cannot call interface method 'I::m' through a handle: it is not pure, and might free what the handle names` |
| impure member through a deep receiver | `cannot call interface method 'I::m' on a reference inside something: it writes through a handle, which could free it` |
| impure member through an unrooted/stored/returned receiver | `cannot call interface method 'I::m' through an interface value that does not keep its target alive: it is not pure, and might free the target` |
| impure member on a global-rooted receiver it might free | `cannot call interface method 'I::m' on a reference into global 'g': it might free it` |
| deep interface argument to a callee that writes handles | `cannot pass an interface value that points inside something to <f>: it writes through a handle, which could free it` (for an interface member, `<f>` is `interface method 'I::m'`) |
| handle-rooted interface argument to an unsafe callee | `cannot pass an interface value that borrows through a handle to <f>: it might free what the handle names` |
| unrooted interface argument to an unsafe callee | `cannot pass an interface value that does not keep its target alive to <f>: it might free the target` |
| later argument frees the receiver | `argument N of interface method 'I::m' may free what the receiver points into, and runs after it; compute it before the call` |
| mut alias with the receiver | the existing method wording with the interface display |
| owned argument or owned concrete receiver beside a handle-rooted/unrooted interface value | `cannot pass an owned argument to <f> beside an interface value that does not keep its target alive: the argument might own the target` (for a receiver, the wording names the receiver as argument 0) |
| bound value after a visible free | the existing `'<d>' may point to freed memory: something under what it points into was changed; point it again first` |
| return of a view of a local/owned parameter | `cannot return an interface value that borrows '<x>': the target is freed when the function returns; keep the target in a longer-lived place or return a handle` |
| store into a global of a view of a local/owned parameter | `cannot store an interface value that borrows '<x>' in a global: the target is freed when the function returns` |
| equality | `interface values cannot be compared` |
| `match`, indexing, `println`, `alive`, `handle`, `drop`, `&d` | the existing type errors; `drop` says `drop needs a local variable holding an owned value` |

### 9.4 Symbol-file diagnostics

The 0.7 symbol messages remain, extended:

- malformed interface/member: `expected a method name in the interface`,
  `duplicate interface method 'm'`, `an interface needs at least one method`,
  `'pure' and 'mut' come before 'fn'`;
- `p0: &I` in a member/function signature: `only records, closed types and
  arrays can be borrowed, not I`;
- a function type in a member: `an interface method cannot use a function
  value; function types are not supported in interfaces`;
- an interface field/global/array/Result/return/capture in a **function
  type** (lambda firewall): the lambda messages;
- duplicate/foreign/variant/alias impl: the 0.7 messages;
- live/file disagreement: `symbol file '<p>' disagrees with the compiled
  interface of '<m>' for <decl>; rebuild it`, with `interface 'I'`,
  `interface method 'I::m'`, `implementation 'I for T'`, `the implementation
  order` or the new position as `<decl>`;
- format/ABI/compiler: `has format <n>; this compiler reads 3; rebuild it`,
  `has ABI 9; this compiler uses 1; rebuild it`, `was built by compiler '9.9';
  this is '0.7'; rebuild it`.

### 9.5 Docs corrections to avoid repeating

- `type`, `for` and `fn` are **keyword tokens**; `interface`, `impl`, `pure`
  and `mut` are contextual identifiers. Do not call `type`/`for` contextual.
- The read-only mut-interface-argument message uses the bare function name for
  a function, and a quoted method/interface display for a method/interface
  call (see the table).
- The unsupported-source table distinguishes owned record/closed (borrow/
  handle hint), optional (narrow first) and array/string/Result (unsupported);
  no dead-end `&` hint for the last group.
- The RICH_LIB private-impl claim and any mutant count from the 0.7 branch are
  not repeated; v2 declares its own private impl and records exact counts at
  implementation.
- The interface-heavy homes program is actually run by the homes checks
  (section 10.7), not merely listed in `port_compare.py`.

---

## 10. Tests, mutants and gates

Every case is written with the change; no existing assertion is deleted or
relaxed. The paused 0.7 branch's second-class negative tests are **superseded**
by the new contract: the positions they rejected (fields, arrays, globals,
`Result`, returns, `var`, nil) are now positive tests, and the old negative
tests are replaced by the new acceptance and the new error cases. This is a
contract change, not a weakening; the new tests must cover the old feature's
remaining rejections (`&I`, `^I`, `new I`, equality, match, owned conversion,
interface-to-interface, function types). The 0.7 reviewer's gate baseline
(`run_fork` 1,369, module 107, sym 33, `sym_fuzz` 200, `port_compare` 1,392
and 1,143, tools 280, eza 546+5, all green) is the coverage bar; the v2 branch
must preserve equivalent coverage or better.

### 10.1 Run programs (`tests/run/`)

| file | covers |
|---|---|
| `ifaces.obx` | declarations, impl, borrow/convert, copy, dispatch, aliases, `var`, inferred copy; no storage |
| `ifaces_mut.obx` | `mut` members, writable/read-only bound targets, `mut d: I`, scalar/append writes, a `let` local calling `mut`, unrooted `pure mut` |
| `ifaces_handle.obx` | handle conversions, pure read-only/pure mut members through handles, handle fields, a stale handle; `ifaces_handle_dead.obx` exit 7 |
| `ifaces_closed.obx` | closed type impl, variant source, parent step, match-narrowed source |
| `ifaces_params.obx` | `d: I`/`mut d: I`, passing/copying, Result-returning members with `try`, `defer d.close()` |
| `ifaces_alias.obx` | aliases, structurally identical interfaces dispatching separately, two interfaces sharing a method |
| `ifaces_scope.obx` | blocks, loops, recursion, defers, conversions in arguments, no cell escape |
| `ifaces_effects.obx` | pure vs impure; bound whole-object impure accepted; deep receiver impure refused; unrooted impure refused; handle impure refused; pure mut keeps references; accepted impure whole-object afterCall invalidates deep refs; joined multi-root receiver; global-rooted refusal |
| **`ifaces_store.obx`** | fields `I`/`?I`, constructor/missing field/omitted optional, `[]I`/`[]?I`, `new []I(0)`, `new []I(n)` error, append/assign/truncate, `for` snapshots, globals and initialisation order, `Result[I]`/`Result[?I]`, `ok`/match (consuming and not)/`try`, nested aggregates, copies to locals/args, field assignment, target mutation visible through copies, record/array/result free, `take`/`replace`, no `drop` |
| **`ifaces_return.obx`** | returning stored/parameter/global/handle-backed views, `?I` nil, `Result[I]`, `try`, caller unrooted/pure-only, `make().m()` on an interface-returning call (the return cell is copied and freed), a direct return of a local-bound view is a compile error (errors file) |
| **`ifaces_lifetime.obx`** | bound local invalidated by a visible free; `var` revival; stored view after target free exits 7; returned view after target free exits 7; handle view exit 7; cell copies survive field reassignment/truncate; target mutation visible through copies; **a hidden free in a later argument exits 7 at the deferred receiver dereference** (stored and bound receivers); no UAF |
| **`ifaces_purity.obx`** | bound whole impure allowed; deep impure refused; unrooted impure refused; handle impure refused; pure members allowed everywhere; unrooted `pure mut` allowed; unrooted passed to a handle-safe pure function; unrooted passed to an impure function refused; an owned record/closed/array/optional/Result argument (or an owned concrete method receiver) beside a handle-rooted or unrooted interface receiver/argument -> error, in both argument orders; a string/int/handle argument beside the same value accepted; a bound interface value with an unrelated owned argument accepted; a bound value with the target's owner as an owned argument refused |
| **`ifaces_deep_recv.obx`** | the exact HIGH reproduction, plus a read-only member variant and a mut variant, full build; the module tree and `--check` variants |
| `ifaces_homes.obx` | interface-heavy: cells, copies, receiver/arg temps, stored field/array/global reads/writes, calls in loops, nested calls, register pressure; **actually run** under `homes_check.py` and `homes_edge.py`/`obo-noregs` |

### 10.2 Error programs (`tests/errors/ifaces.obx`, `tests/errors/ifaces_lifetime.obx`)

One `// ===` section per error with the exact section 9 text:

- declaration/type: empty interface, duplicate member, forward shell, `pub`
  member, function type in a member, `&I`, `^I`, `new I`, `I` nil, missing
  `I` field, `new []I(n)`, interface equality/match/println/alive/handle/drop,
  function type/lambda-header interface (firewall), interface capture.
- conversion: owned record/closed/variant (hint), optional (narrow-first, not
  the owned hint), array/string/Result (unsupported, not the owned hint),
  `?I` without narrowing, nil to `I`, interface-to-interface, missing impl,
  private foreign impl, foreign no-impl, variant no-impl (parent named).
- calls/effects: deep receiver impure (the HIGH case), unrooted impure,
  handle impure, global impure, later arg frees the receiver, mut alias,
  deep/handle/unrooted interface argument to an unsafe callee, read-only
  argument to `mut d: I` (bare function name), arity, argument types, unknown
  member, member as value, field access.
- lifetime: bound local after visible free; return of a local/owned-parameter
  view; global store of a local/owned-parameter view; owned argument beside a
  handle-rooted or unrooted interface value (both argument orders).
- impl: duplicate, foreign/variant/built-in/incomplete receiver, missing
  method, heading-only method, signature mismatches (receiver mode, parameter
  type, parameter mode, return), impure impl of a pure member, `pub impl` with
  private types, impl before methods/conversion.

The run-vs-error split: a stored/returned view whose target dies is a **run**
test with `// expect-exit: 7`; a visible free in the same function is an
**error** test with the exact message. The reviewer's deep-receiver
reproduction is an error test; the mutant that removes the check must fail it.

### 10.3 Modules and symbol files

| case | what |
|---|---|
| `modules/run/ifaces` | lib exports a pub interface, records, methods (read-only/mut/pure/impure), pub impls; main converts/borrows/calls across the boundary; a mut impure call invalidates a reference |
| `modules/run/ifaces_store` | interface fields/arrays/globals/returns/`Result` across a module; main stores and returns values; source-less check |
| `modules/run/ifaces_return` | a factory returns a stored/parameter/global/handle-backed view; caller pure-only; a local-target return is an error tree |
| `modules/run/ifaces_handle`, `ifaces_closed`, `ifaces_transitive`, `ifaces_downstream`, `ifaces_identity`, `ifaces_private_methods` | the 0.7 cases, updated for storage where useful |
| `modules/errors/private_impl`, `foreign_no_impl`, `foreign_no_impl_variant`, `pub_impl_private_type`, `foreign_impl`, `impl_order`, `deep_recv`, `unrooted_impure`, `return_local` | exact errors; **`foreign_no_impl_variant` is typed as the variant** (`let a: lib::A = new lib::A{x: 1}; let d: lib::D = &a`), so the parent closed type `S` is named by `implMissing`; the `--check` twin repeats it |
| `modules/sym/iface_sig`, `iface_store`, `forged_writeshandles`, `ifaces_transitive_check`, `private_impl_check`, `foreign_no_impl_check`, `foreign_no_impl_variant_check` | committed `.sym` trees, source-less checks exit 0/1, `--reemit-sym`, mode-0; the forged `writeshandles` case |
| version fixtures | base `obosym 3`/`compiler "0.7"`/`abi 1`; mutated `format 4`, `abi 9`, compiler `"9.9"` |
| golden tree | `lib.obx` gains an interface with modes/types/returns, interface fields/arrays/globals/returns/`?I`/`Result[I]`; the 3 `expected/*.sym` are regenerated explicitly with `sym_test.py --update` (the gate compares only) |

`sym_test.py` additions:

- `RICH_LIB`/`RICH_MAIN` with the full member signature table, an interface
  store, a private impl explicitly, a source-less check;
- `roundtrip_rich`, `determinism`, `hash_bytes`, `check_mode_and_cli`,
  `--reemit-sym`;
- `hash_private_and_rename`: an interface member mode/return/param-type/
  order change changes the interface hash; adding/removing a member, an
  interface field/array/global/return/Result/optional changes the nested
  module's body hash and the dependant's `depiface`; a private impl/body
  change affects only the source hash; a `pub` impl addition/order change
  affects the receiver module's interface hash;
- `malformed_structures`: the new positions accepted; `^I`, `&I`, function
  types in interface members, interface in function types, duplicate members,
  `p0: &I`, forward shell, bad mode, mode-0 disagreement;
- `effects_check`: the purity table cases, the deep receiver, unrooted,
  handle, global, joined roots, owned-argument-beside-unrooted/handle
  (including an owned concrete receiver);
- `sourceless_and_build_requirement`/`check_mode_and_cli`: source-less
  interface storage/returns exit 0; the forged `writeshandles` dependency is
  refused; stale formats are rejected; `--version` is 0.7/3/1;
- version table: format 4, ABI 9, compiler "9.9", each mutated from the new
  base; if the format-bump fallback is taken, the base becomes 0.8/4/1 and the
  mutated values become 5/9.9/ABI 9;
- fixture migration: all 76 `tests/modules/sym/*/symdir` files and 3 goldens
  are regenerated for the new base and body hashes; headers are outside the
  interface hash, the bodies are not. The paused branch's fixtures are
  replaced, not copied.

### 10.4 Existing tests and fixtures

- `tests/sym_test.py`: `OBO_VERSION = '0.7'`, `SYM_FORMAT = 3`; the version
  table and literal snippets updated; `handwritten_interface`/
  `b6_stub_completion` use format 3/0.7.
- `module_names_int_result` retains the type-declaration-alias case
  (`type IntT = int::T`, `type ResT = Result::T`) and the `::`-first rule.
- `tests/sym_fuzz.py`: `change_version` mutates the format to 4; the fuzz
  seed may include interface fields/arrays/returns/`Result`/optional and the
  full member signature set; it must not weaken.
- `tests/port_compare.py`: new run/error files get `(0, 7)` seed skips; the
  module trees are outside the seed comparison; `symcheck`/`REEMIT` cases are
  extended. The skip stops when the seed reports 0.7.
- `tests/mutate_fork.py`/`mutate_port.py`: new `IFACE_MUTANTS`; retarget the
  moved designator/call patterns; a stale pattern fails the gate by design;
  record mode-0-only equivalents with the reason.
- `tests/doc_check.py`: the new `LANGUAGE.md` examples compile and run.
- No existing run/error program changes except where the contract changed
  (second-class negatives replaced by first-class positives).

### 10.5 The foreign variant fixture fix (INFO finding)

Both `tests/modules/errors/foreign_no_impl_variant` and
`tests/modules/sym/foreign_no_impl_variant_check` currently type the local as
the **closed** type `lib::S`, so the conversion goes through the closed type
directly and does not exercise the variant-to-parent step. V2 types it as the
variant:

```obo
let a: lib::A = new lib::A{x: 1}
let d: lib::D = &a
```

The expected error names the parent closed type `lib::S`, not the variant
`lib::A`; the `--check` twin asserts the same. A mutant that skips the parent
step must fail this fixture.

### 10.6 Mutants

The exact count is recorded at implementation; the 0.7 count was off and is
not repeated. The targets:

- **declaration/impl**: missing impl, wrong interface identity, variant parent
  step, owned source hint, optional narrow-first vs owned hint, array/Result
  unsupported vs owned hint, interface-to-interface, interface field/array/
  global/return/Result/optional accepted (positive tests), missing `I` field,
  nil to `I`, `new []I(n)`, function type in a member, interface in a function
  type;
- **value layer**: heap cell omitted on store (frame pointer escapes); store
  does not copy the pair (assignment visible through a copy); return does not
  allocate (frame pointer escapes); local copy does not copy the pair;
  record descriptor omits an interface field (leak/double free); array kind
  not 2 (leak); `?I` array kind; Result payload free omitted; global teardown
  omitted; `take`/`replace` cell ownership; optional nil not freed on
  overwrite; cell freed while a copy exists (double free); in-place overwrite
  of a shared cell;
- **dispatch**: vtable slot order, wrong vtable, token not dereferenced,
  **receiver dereferenced before the arguments** (a later argument's hidden
  free becomes a UAF instead of exit 7), receiver temp not a snapshot (a
  later argument assignment changes the callee's pair), receiver not held,
  **deep receiver check dropped** (the HIGH reproduction), unrooted purity
  check dropped, handle purity dropped, global check, all-roots scan,
  later-argument hold;
- **purity/effects**: impure summary treated pure; read-only impure summary
  `writesHandles` dropped; pure summary treated impure; accepted whole-object
  afterCall deep invalidation dropped; owned-argument-beside-unrooted/handle
  check dropped (both argument orders and an owned concrete receiver);
  `handleSafe` `!writesHandles` dropped (forged `.sym`);
- **symbols**: writer drops `mut` on a member parameter (the MEDIUM finding),
  drops `&`, drops `p0: I`/`mut p0: I`, swaps modes, drops/changes a return,
  drops an interface field/array/global/Result/optional, reader ignores a
  member mode/type/return, mode-0 indexing, reemit, hash, private impl
  omission, foreign variant parent.
- A mutant is caught by a named positive or exact-error test; a mode-0-only
  mutant that a full build's immediate re-read makes unobservable is recorded
  as equivalent with the reason, never claimed as caught.

### 10.7 The homes/no-registers test (LOW finding)

The 0.7 `ifaces_homes.obx` was only listed in `port_compare.py`; it was never
run under `homes_check.py` or `homes_edge.py`. V2 makes it real:

- add an interface-heavy source to `homes_check.py`'s program set (the file
  compiles fixed sources, not `tests/run` files): interface locals, copies to
  locals, receiver/argument temps, stored field/array/global reads/writes,
  calls in a loop, nested calls, a `Result[I]` return; assert no stack-slot
  operand in the hot loop and few in the ranked case;
- add the same source (or `tests/run/ifaces_homes.obx`) as a fixed case in
  `homes_edge.py`, compile and run it with both `obo` and `obo-noregs`, and
  compare outputs; also run it under the `OBO_CHECK=1` leak oracle;
- keep it in `port_compare.py` with the seed skip;
- the docs must describe this real wiring, not the 0.7 claim.

### 10.8 Gates

On the merged tree, the normal set: `run_fork.py`, `module_test.py`,
`sym_test.py` (golden compare, no `--update`), `sym_fuzz.py`,
`doc_check.py`, `homes_check.py`, `homes_edge.py`, `elf_test.py`,
`io_test.py`, `meta_test.py`; `port_test.py`, `port_compare.py` (seed
comparison and symcheck expect/REEMIT), `mutate_fork.py`, `mutate_port.py`,
`mutate_io.py`; `imports.py`, `stdlib.py`, `tools.py`, `eza.py` unchanged and
green. Fixed point: `make obo` with the 0.6 seed, stage2 == stage3 (`cmp`),
the compiler peak within budget, and the `OBO_CHECK=1` leak/double-free
oracle on the new run programs. Differential outputs of `eza`/tools/library
unchanged. Independent review, then merge and one seed promotion.

---

## 11. Implementation path and risks

### 11.1 What is reusable from the paused 0.7 branch

Reusable with edits:

- `Type.kind == "iface"`, `ifaceSyms`, `impls`, `implArena`, `Impl`,
  `ifaceMethodsOf`, `findIfaceMethod`, `ifaceMethodDisplay`, `ifacePure`,
  `implKeyOf`, `implDisplay`, `implBase`, `findImplIn`, `findImplFor`,
  `implMissing`, `handleSafe`;
- the contextual `interface`/`impl` parsing, `finishTypeName`, forward-shell
  rejection, self-reference, export closure, `closureCheck`, `checkExports`;
- `ifaceDecl`, `implDecl` matching and vtable emission (vtables are
  unchanged), the synthetic member symbols and purity summaries;
- `appIface`/`appIfaceParams`/`appTypeDef`/`appImpl`, `symIfaceDef`,
  `symImplDecl`, the order lists, mode-0 comparison and `--reemit-sym`;
- the 0.7 tests for declarations, impl matching, vtables, closed/variant,
  declarations across modules, private impls and the version fixtures (the
  ones that are not value-layer negatives).

Rewrite:

- `ifaceCell` -> frame/heap split, `ifaceLoad`/`ifaceStore`/`ifaceReturn`,
  the 16-byte cell descriptor;
- `ifaceValue` -> the source table, typed destinations, optional/nil, the
  unsupported-source messages;
- `varDecl`/`fieldList`/`newExpr`/`okVariantOf`/global init and teardown/
  `forStat`/`take`/`replace`/`drop` for interface values;
- `designatorT`/`ifaceCall` -> any designator receiver, snapshot temp,
  receiver-as-argument-0 checks, unrooted purity, return cells;
- the binding/invalidation integration for interface locals/parameters;
- the effect rules for deep/handle/unrooted receivers and the owned-argument
  rule;
- the symbol reader/writer positions and returns;
- the tests that encode the second-class restrictions.

### 11.2 Ordered steps

0. Rebase on `main` (0.6 seed); keep the paused branch read-only; copy the
   reusable code as a starting point, not the value layer.
1. **Data model and classifiers, no behaviour.** `Type.kind`; registries;
   `nonNull`/`isOwned`/`ownsCell`; `same`/`assignable`; release tables. Build
   with the 0.6 seed; the existing suite passes unchanged.
2. **Declarations and type formation.** Allow the new positions and returns;
   keep `^I`/`&I`/`new I` out; interface fields/arrays/globals/Result/
   optional; the missing-field/non-null rules; closure/export checks.
   Add declaration positives and exact negatives.
3. **Value layer.** Frame cell, heap cell, load/store/return, copies,
   optionals/nil, constructor/field/array/global/Result/return, take/replace,
   for loops, destructor hooks (`ownsCell` in descriptors/arrays/Result/
   globals), check-mode. **Checkpoint A:** the whole existing suite plus the
   new value tests; measure peak and allocations; fix leaks/double frees
   before symbols.
4. **Bindings, purity and dispatch.** Interface local/parameter target
   bindings, invalidation, joins, stale uses, definite-escape errors; the
   receiver-as-argument-0 deep check; unrooted/handle/global rules; the
   owned-argument rule; argument conversions; effects. Add the HIGH
   regression, full/check/module/read-only/mut, and the purity table tests.
5. **Symbols.** New positions/returns, member signature fixtures, writer/
   reader/mode-0/reemit, fixtures/goldens, version decision. **Checkpoint B:**
   source-less checks exit 0; all fixtures regenerate; goldens explicitly.
6. **Docs, tests, mutants, gates.** `LANGUAGE.md` (a new interfaces section),
   `README.md`, `FORK.md` after implementation, `PERFORMANCE.md`/bench
   historical notes; `doc_check.py`; the homes/no-regs wiring; the full gate
   set; differential outputs.
7. **Review, merge, promote.** Independent review; merge; one `make promote`
   on main because the source changed. The compiler source stays
   interface-free in round 1, so the 0.6 seed builds the 0.7 compiler.

### 11.3 Risks

1. **The deep-receiver mid-call free (HIGH).** The receiver must be argument 0
   for the deep/`writesHandles` check, not invalidated after the call. The
   exact reproduction, the full/`--check`/module/read-only/mut variants and
   the mutant are load-bearing.
2. **Value-cell ownership paths.** Every store/return must copy or transfer;
   no frame cell may escape; every stored cell must be freed exactly once by
   its record/array/global/Result/return owner. `OBO_CHECK=1` leak/double-free
   oracle on all run tests; descriptor/array/Result/global hooks. The
   deferred receiver dereference changes the call layout (a reserved
   argument-0 slot filled after the arguments, and an `RT_DEREF` runtime call
   with the argument stack already pushed); stack padding/depth and register
   home retries must be tested. If it is too invasive, the conservative
   unrooted-hold fallback in section 5.3 is the sound alternative.
3. **Bindings and purity.** The new interface-local category must participate
   in invalidation/joins without being treated as a freeable owned local or a
   reference; the deep/unrooted/handle/global table and the owned-argument
   rule must be exact. False rejections are acceptable if recorded;
   a missed check is a soundness bug.
4. **Optional/nil and take/replace.** Zero-pointer nil, narrowing, freeing a
   stored optional cell on overwrite, `take`/`replace` ownership, `ok(nil)`.
5. **Checked-view residual.** Stored/returned views of dead targets exit 7;
   factories cannot return a new local target. Document and test; do not
   claim Java/Go target liveness.
6. **Symbol fixture churn and member-signature coverage.** The MEDIUM finding:
   committed fixtures for every mode/type/return, mode-0/reemit, and mutants.
   A full build's re-read, not only roundtrip, must catch a writer mode drop.
7. **Version/ABI claim.** Staying 0.7/format 3 is defensible only because 0.7
   is unpromoted and the grammar is unchanged; if review disagrees, the
   fallback is 0.8/format 4 and a lambda version revision.
8. **Allocation/peak/homes.** A heap cell per store/return; measure the
   compiler's live peak against the 2,000,000 budget; run the homes/no-regs
   test; do not raise `PEAK_BUDGET` without the user.
9. **Effect conservatism.** Deep/unrooted/pure rules and the owned-argument
   rule can reject useful programs; record the accepted/rejected cases, and keep
   `pure` as the escape hatch. The `pure` implementation check is conservative
   for forward headings/recursion; document the ordering.
10. **Lambda coordination.** Keep the firewall; do not let the new positions
    relax `containsIface`; the compiler source stays in the pre-lambda subset.
11. **Superseded 0.7 tests/docs.** Delete/replace only the tests whose contract
    changed; keep the rest; do not copy the 0.7 deep-receiver acceptance, the
    quoted-function-name wording, the unsupported-source table, the
    contextual `type`/`for` claim or the RICH_LIB/retarget claims.

### 11.4 Size and complexity

Estimated `src/obo.obo`: +1,300 to +1,800 lines over the 0.6 baseline (about
12,300–12,800 total), plus 2,000–2,800 lines of tests, fixtures, goldens,
Python harness and docs. This is larger than the 0.7 value layer because it
adds the storage/return/optional/ownership/binding machinery, but it reuses
the declaration/vtable/symbol skeleton and the runtime. The real cost is in
the type checker, the value-cell ownership paths and the binding/purity
integration, not in code emission. Medium-high complexity; one feature in
flight; one seed promotion after review.

### 11.5 The smallest shippable first-class increment

The full scope is one feature and should land as one reviewed change, because
splitting the `.sym`/fixture/version work would pay the migration cost twice.
If implementation pressure forces a smaller first landing, the smallest
**coherent** increment is:

1. the representation (a′) — frame cells and uniquely-owned heap cells;
2. record/closed **fields** `I`/`?I` and **returns** `I`/`?I` — the two
   positions that prove storage and the checked-view lifetime;
3. the full purity/deep/unrooted receiver rules and the owner/destructor
   hooks those positions need;
4. the `.sym` field/return positions and the committed member-signature
   fixture.

Arrays, globals, `Result[I]`, `interface-member returns` and the
lambda-adjacent surface are then mechanical repetitions of the same `ownsCell`
hooks; they can land in the same branch before merge, but they are not needed
to validate the semantic core. Do **not** ship a smaller increment that keeps
the 0.7 frame cell for fields/returns (it would dangle) or that omits the
deep-receiver rule (it is the HIGH defect). A first increment without arrays/
globals/Result would be a temporary branch state, not a promoted version: the
version bump and seed promotion happen once, after the complete feature
passes the gates.

---

## 12. Decisions, open user decisions and genuine forks

### 12.1 Decisions recorded for the implementation

1. **Representation:** a one-word pointer to a uniquely-owned 16-byte
   `{token, vtable}` cell; frame cells for locals/parameters, heap cells for
   stored values/returns; no refcount; value copies of the pair.
2. **Storage/returns:** fields, `?I` fields, `[]I`, `[]?I`, globals,
   `Result[I]`, `Result[?I]`, function/method/interface-member returns.
3. **Copy semantics:** copyable like strings; `let` and `var`; no moves; no
   `drop` on interface locals.
4. **Nil:** `I` non-null; `?I` optional; zero-word nil; narrow first.
5. **Conversion sources:** same interface copy, borrow, handle; owned
   concrete rejected; optional/array/Result unsupported with their own
   messages; no interface-to-interface.
6. **Lifetime:** checked view; token deref; visible invalidation of bound
   values; definite-escape compile errors for return/global store of a view of
   an owned local/parameter; stored/returned values unrooted.
7. **Purity:** impure members only through bound whole-object receivers;
   deep/handle/unrooted require pure; the receiver is argument 0; the
   owned-argument rule for handle-rooted/unrooted values; `handleSafe` keeps
   `!writesHandles`.
8. **Mutability:** binding writability for bound values; unrooted `pure mut`
   as a new non-owning capability; `mut` about the target, not the cell.
9. **Semantics preserved:** nominal identity, explicit `impl`, per-pair
   vtables, declaration-order slots, variants via the closed parent, no
   interface-to-interface, no owned receivers, modules/symbols.
10. **Version:** 0.7/format 3/ABI 1; lambda plan unchanged; fallback
    0.8/format 4 only if review requires.
11. **Lambda firewall:** keep for v1; no revision before Stage A.
12. **Round 1:** no interface syntax in the compiler/library/tools/eza; the
    0.6 seed builds the compiler; one promote after review.

### 12.2 Genuine user-level forks (recommended defaults)

These are recorded because they change the language's ergonomics, not just
the implementation. The review does not need to reopen them, but the user
should know the boundary.

1. **Impure dispatch through stored interface values.** V1 refuses; a stored
   value can call only pure (including `pure mut`) members, and a call through
   it cannot pass an owned argument (section 4.7). If the user needs a stored
   list of mutating interface values or `d.add(ownedItem)` through a stored
   value, the options are:
   - runtime pinning: the target is marked in-use for the call; a free of a
     pinned target is a new runtime exit (e.g. exit 10) rather than a UAF.
     This allows impure calls through unrooted values at the cost of a
     per-call runtime check and a new failure mode; it does not help
     factories.
   - owning interface values: the value owns/keeps the target alive
     (refcounted cell or target); impure dispatch is safe, but the target
     ownership model changes (section 3.6, section 13).
   **Default:** pure-only stored dispatch in v1; revisit after the first
   users/tools need it.
2. **Factories returning new targets.** `fn make(): I` cannot return a view of
   a local it creates (compile error). It can return a view of a parameter, a
   global, a handle-backed target, or a stored value. **Default:** checked
   views in v1; owning interface values are the follow-up if factories are
   required. This is the most likely reason to take the owning-value
   extension.
3. **Version.** Stay 0.7/format 3 (recommended) vs 0.8/format 4 (lambda plan
   shifts by one version). **Default:** stay.
4. **Owned concrete conversion** (`let d: I = c` moving `c` into the value).
   Out in v1; part of the owning-value extension. **Default:** reject with the
   `&`/`handle` hint.
5. **`var` and copy semantics.** Allowed/recommended; the alternative
   (let-only) is the 0.7 surprise and is rejected.
6. **Nil.** `I` non-null, `?I` optional; recommended and consistent with
   `^T`/`?^T`.
7. **Lambda firewall.** Keep; no Stage A revision. **Default:** keep.

A review that prefers a different answer to fork 1 or 2 should record it as a
scope change: both are larger than the value-layer redesign and would reopen
the single-ownership/no-GC argument.

---

## 13. Scope exclusions and extension points

- **Owning interface values.** A value that keeps its target alive: a
  refcounted cell that owns the target (`Rc<dyn Trait>`) or a move-only owning
  cell (`Box<dyn Trait>`). Extension: add an `owned` construction
  (`let d: I = c`, consuming `c`), a cell ownership flag or a separate owning
  type, target free on last release, cycle policy (forbid targets that contain
  owning interface values, or accept leaks), destructors at every position,
  and purity rules that distinguish owning from viewing values. This is the
  path to factories and impure stored dispatch; it is a second feature, not a
  patch.
- **Runtime pinning.** Mark the target in-use for a call; freeing a pinned
  target exits instead of corrupting. Extension: a pin word/header, a runtime
  slot, an exit code, and call-entry/exit code in `ifaceCall`; it allows
  impure calls through unrooted values but adds a runtime failure mode.
- **Target GC.** Out by invariant; it would replace the ownership checker and
  the deterministic free/leak model.
- **`alive(I)`.** A token probe for an interface value; extension of the
  `alive` builtin; the original handle is the current probe.
- **Interface-to-interface conversion, type tags, downcasts, reflection,
  embedding/superinterfaces, variance, structural typing.** Need a runtime
  type tag/itable and a slot-mapping rule; out.
- **Owned receivers in interface methods.** A non-owning value cannot move its
  target; an owning value would make this possible; out.
- **Method values, function values in interfaces, interface values in
  lambdas/function types.** The lambda firewall (section 8); a later design
  after both features promote. The order is: function types taking interface
  parameters (one word, pure body), returning interface values (caller-owned
  cell), read-only interface captures (environment pair copy), then interface
  members with function types (grammar/symbol change).
- **`mut`/owned interface captures.** Tied to the capture design; out.
- **Devirtualisation/inlining.** A later optimisation; must not change the
  surface or the purity invariant.
- **Adopting interfaces in the compiler/library/tools/eza.** A separate branch
  after promotion, with differential outputs unchanged.

---

## 14. Appendix: worked details and review checklist

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
type Drawable = interface { pure fn area(): int; pure fn name(): string; pure mut fn scale(p0: int); fn link(p0: Drawable): Drawable }
type Holder = ref record { d: Drawable; maybe: ?Drawable; items: []Drawable; r: Result[Drawable] }
fn (p0: &Shape) area(): int
fn (p0: &Shape) name(): string
fn (mut p0: &Shape) scale(p1: int)
fn (p0: &Shape) link(p1: Drawable): Drawable
impl Drawable for Shape
fn make(): Drawable
var Current: ?Drawable
```

`Shape` has a shell (closed); `Drawable` has none (the definition registers
it). `Holder` shows an interface field, optional, array and `Result`. The
member modes and the interface return are interface data; the vtable for
`(Shape, Drawable)` has the members in declaration order. No method, vtable
or cell address appears.

### 14.2 Lowering examples

`let d: Drawable = &c; d.area()`:

1. `&c` is a borrowed item of type `Circle`; `ifaceValue` resolves the
   `(Shape, Drawable)` impl (one parent step from `Circle`), calls
   `RT_HANDLE`, allocates a frame cell, stores `{token, vtable}`, and makes
   the local a one-word pointer to it with the binding root `c`.
2. The receiver is a bare local; the compiler copies the pair into a hidden
   receiver temp, holds the binding as argument 0, loads the token, calls
   `RT_DEREF`, pushes the object pointer, and emits
   `call qword ptr [vtable + 0]`.
3. `afterCall` sees the pure synthetic summary: no invalidation, no
   `userCalls`.

`r.d = &c; let e = r.d; e.area()`:

1. `r.d = &c` allocates a heap cell (or overwrites `r.d`'s existing cell),
   stores the pair, and frees the old cell if `?I` was nil.
2. `let e = r.d` copies the pair into a frame cell; the target is shared; the
   binding is dropped (unrooted) because it came from storage.
3. `e.area()` is allowed only because `area` is pure; an impure member is the
   unrooted error. If `c` was freed, `RT_DEREF` exits 7.

`fn make(): I { let c = new Circle; return &c }` is a compile error
(definite escape). `fn id(d: I): I { return d }` returns a copy of the pair in
a heap cell; the caller gets an unrooted view (pure-only); the target is the
caller's argument and stays alive if the caller keeps it.

The HIGH reproduction:

```obo
type C = ref record { v: int }
type Owner = ref record { c: C }
type D = interface { fn imp(o: ^Owner): int }

fn (c: &C) imp(o: ^Owner): int { o.c = new C{v: 99}; return c.v }
impl D for C

fn main() {
    let b = new Owner{c: new C{v: 1}}
    let d: D = &b.c
    println(d.imp(handle(b)))
    println(b.c.v)
}
```

V2 refuses at `d.imp(...)`: the receiver binding is root `b`, path `/c`,
deep; `imp` is impure (`writesHandles`/`freesAll`); the receiver-as-
argument-0 check reports `cannot call interface method 'D::imp' on a
reference inside something: it writes through a handle, which could free it`.
The direct call `c.imp(handle(b))` is refused by the same rule at the method
receiver. A pure `imp` would be allowed. Full build, `--check`, a module
boundary, a read-only member and a mut member are all tested.

### 14.3 Review checklist

- [ ] The value is one word; locals/parameters are frame cells; stored
      positions/returns are uniquely-owned heap cells; no cell is shared; no
      refcount/new runtime slot; the 16-byte cell descriptor is emitted and
      freed by the existing record walk.
- [ ] Fields (`I`, `?I`), arrays (`[]I`, `[]?I`, `new []I(0)` vs `n>0`),
      globals, `Result[I]`/`Result[?I]`, returns and nested aggregates all
      construct, copy, free and check mode consistently.
- [ ] Copies are value copies; `var` and assignment work; no move bits; a
      stored read/return is unrooted; a local copy is independent of the cell.
- [ ] `I` non-null, `?I` zero nil, narrowing, `take`/`replace`; no `drop` on
      interface locals.
- [ ] The source table gives the correct owned/optional/array/Result/foreign
      messages; no dead-end `&` hint.
- [ ] Bound values are invalidated by visible frees/moves; definite escapes
      (return/global store of a local/parameter view) are compile errors; the
      checked-view exit 7 is tested.
- [ ] The receiver is argument 0: deep + impure refused, handle + impure
      refused, unrooted + impure refused, global + mightFree refused; a pure
      member (including `pure mut`) is allowed; `mut` writability; accepted
      whole-object impure effects.
- [ ] Interface arguments follow the same table; handle/unrooted arguments
      need `handleSafe`; the owned-argument rule is tested in both argument
      orders.
- [ ] Vtables: declaration order, defined-before-impl, variant parent,
      downstream impl, private impl, no address in `.sym`.
- [ ] Symbols: member modes (`&`, `mut &`, `I`, `mut I`), record/foreign
      params, non-int/interface/self-referential returns, field/array/global/
      Result/optional positions, mode-0 and `--reemit-sym`; the MEDIUM fixture
      and mutants; the forged `writeshandles` defence.
- [ ] The foreign variant fixture is typed as the variant; the homes/no-regs
      test is actually wired; the docs nits are not repeated.
- [ ] All fixtures/goldens regenerate explicitly; the version is 0.7/3/1;
      the lambda firewall is intact; the compiler source is interface-free;
      one seed promotion after review.
