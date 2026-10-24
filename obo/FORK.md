# obo: an ownership fork of obx

An experiment: does single ownership with group borrowing (Nick Smith's model, as
explained in Evan Ovadia's "Group Borrowing" article) use substantially less memory
than obx's garbage collector, at an acceptable cost in syntax and rules? Mainline
obx is unaffected; this directory is a separate compiler, `obo`, and runtime.

## Agreed decisions

1. **Goal.** Lower peak memory on a benchmark set (lists, trees, strings, a
   compiler-like workload), with modest extra syntax. The baseline is mainline obx
   with a *tuned* collector (lower heap growth factor), so we measure what
   ownership buys rather than what GC tuning would.
2. **Shape.** `obo-on-obx.obx` started as a copy of `obx/src/obxc.obx`. It is written in
   mainline obx (with GC), built by `obx/obxc`, and is not self-hosting until the
   model is proven. It has its own runtime in `runtime/`.
3. **Ownership.** Single ownership for records and arrays: assignment and passing
   by value move; owners free what they own at scope exit (reusing `defer`'s
   scope-exit machinery). Strings are immutable and reference counted (no cycles
   are possible). Back-references and graphs use generational handles, checked at
   run time.
4. **Borrowing, smallest useful subset first.** References are second-class: they
   may be parameters and locals, never stored in fields or returned. Group rules on
   top: mutating through a reference invalidates references into child groups
   (array elements, variant payloads, owned pointees); `mut` marks parameters that
   mutate; paths only where needed. No `unsafe` initially.
5. **Single pass.** Kept. Move and invalidation tracking are forward facts per
   function, like narrowing, with the same conservative loop rule (a fact
   established outside a loop may not be invalidated inside it). If that proves
   too restrictive, buffer one function at a time, and record why here.
6. **Testing.** The mainline oracles, plus: ownership error tests (must be
   rejected, with exact messages); a counting runtime, so every program must end
   with zero live objects (leaks and double frees both show up); poison mode for
   use-after-free.
7. **Milestones**, each ending in a decision to continue, adjust or stop:
   1. Owned records and arrays, moves, freeing, leak counting.
   2. Second-class references, with group invalidation for array elements.
   3. Closed types and variant payloads as child groups.
   4. Port the examples and benchmarks; measure against tuned GC.

## Milestone 1: done

Records, closed types and arrays are owned; strings are not yet (see below).

**Rules, as implemented**
- Assigning, passing or returning an owned value moves it. A moved local can't be
  used until it is given a new value; giving a live local a new value frees the
  old one first. Owners free what they own at scope exit, after the scope's
  defers, and on every early exit (`return`, `break`, `continue`).
- Moving out of a field, element or global is an error. `take(x)` moves out of an
  optional variable, field or element and leaves `nil`.
- Moves must agree across paths: both branches of an `if`, every `match` arm
  (and falling through an integer `match` with no `else`), and every iteration
  of a loop, which must end (and `break` or `continue`) with the same states it
  started with. No moves inside `&&` or `||` operands or inside `defer`, and a
  local that a `defer` reads can't be moved afterwards.
- A local can't be moved while it is being assigned through (`r.child = f(r)`),
  and assigning through an owned global across a call is an error, since the
  call could free what the assignment points into.
- Owned temporaries must be stored before use (`make().x`, `len(make())`,
  comparing them); a call whose owned result is discarded frees it at once.
- Iterating over owned elements with `for x in arr` waits for milestone 2's
  references; indexing works.
- Owned globals are freed when `main` returns.

**Runtime.** No collector: `rt_free` frees an object and everything it owns by
walking the type descriptors (which now list only owning fields), iteratively, so
a million-node list frees without recursion. Growing an array frees the old data
block; `truncate` frees the removed elements. With `OBO_CHECK=1`, a program that
ends with live objects exits with code 5 and a count; freeing anything twice exits
with code 6; `OBX_GC=poison` poisons freed memory. `gcstats()` prints live objects
and peak bytes.

**Tests.** `python3 obo/tests/run_fork.py` (every program runs with the leak
check on) and `python3 obo/tests/mutate_fork.py`: 15 mutants that free too little,
free too much or skip a check, all caught, the first kind by exact leak counts.

**First measurements** (peak resident memory, via a small C launcher; a list
workload, building and consuming lists while one small list stays live):

| Workload | GC (mainline) | Ownership (obo) |
|---|---|---|
| 300 lists of 10,000 nodes | 8.6 MB | 0.26 MB |
| 30 lists of 100,000 nodes | 11.3 MB | 3.2 MB |
| 30 lists of 1,000,000 nodes | 87.4 MB | 31.5 MB |

obo tracks the live data almost exactly. The GC pays for its 8 MB minimum heap on
small programs and its 2x growth factor on large ones; as agreed, milestone 4
compares against a tuned collector before drawing conclusions.

**Open for later**
- ~~Strings are still unmanaged~~: done in milestone 1b, below.
- Without a collector, the 16-byte object header could shrink, which matters
  for small objects (a list node is half header today).
- M1 programs feel restrictive: reading through an owned structure without
  consuming it needs milestone 2's references (`sum(list)` consumes the list).

## Milestone 1b: reference-counted strings (done)

Every string location (local, parameter, field, element, global) owns one
reference. Storing a string from another location takes a new reference; storing
a fresh one (from `+`, `string(n)`, `substr`, a call) hands its own over.
Overwriting or leaving scope drops a reference; records and arrays drop their
strings when freed; fresh strings that are only looked at (`println(a + b)`) are
dropped straight after. A string read from a location and then held while more
code runs (`g + f()`, where `f` might replace `g`) takes a reference of its own
first. Literals live in read-only data and cost nothing. The count lives in the
high half of the object header's size word. The leak check now counts strings.

Two things the tests caught: a fresh string passed to the drop routine was dropped
twice (the drop call is now its own drop), and a hazard test that silently tested
nothing, because `"g" + string(2)` folds into a literal at compile time.

## Milestone 2: references (done)

- `&T` in parameter and local types, with `mut` before a parameter name that changes what it points to. References are borrowed
  pointers to owned objects (records, closed types, arrays): they can't be stored
  in fields, elements or globals, returned, or made from globals or temporaries.
- Borrowing is implicit where the destination is a reference: passing a value to
  a `&` parameter, `var p = list` when `list` is a reference, `p = p.next`.
  `&place` borrows explicitly, as in `var r = &b.items`. Anything owned reached
  through a reference is borrowed automatically, since nothing can be moved out
  through one.
- Writes need a mutable reference (from an owned local or a `mut` parameter).
- **Invalidation.** Each reference records its root (an owned local or reference
  parameter) and whether it may point below the root's own object. Assigning an
  owned field or element, `take`, `truncate` and passing a mut argument
  invalidate the root's deeper references, except the one the change went
  through; moving, reassigning or freeing the root invalidates them all. An
  invalid reference can't be used until it is pointed somewhere again, with the
  same branch and loop agreement rules as moves.
- Passing a mut argument and another reference into the same root is an
  error. `for x in arr` over owned elements borrows each element; the array
  walked must be a local or parameter, and moving or reassigning it inside the
  loop is an error (this also closes a latent M1 hole where the loop could read a
  freed array).

**Measurements**, the same programs in both dialects apart from `&` on a
parameter, peak resident memory, leak check on:

| Workload | GC (mainline) | Ownership (obo) |
|---|---|---|
| Lists, summed by borrowing | 8.6 MB | 0.26 MB |
| 1 million short strings over 200 rounds | 9.0 MB | 0.38 MB |

**Tests.** 54 fork tests (program tests with leak checks, 47 ownership and
reference error cases); 32 mutants covering freeing, string counts, moves and
references, all caught.

**Next.** Milestone 3 (closed types' variant payloads as child groups) is small
now that references exist. Milestone 4: port the examples and a compiler-like
workload, tune mainline's collector (growth factor and minimum heap) for a fair
baseline, and decide.

## Milestone 3: variants (done)

Mostly a finding. In the article's model a variant holds its payload inline, so
replacing it destroys the payload in place and needs its own child group. In obx
a variant is a separate heap object reached through a pointer, so replacing one
frees an object, which milestone 2's invalidation already covers; reassigning a
matched variable already ended its narrowing. `tests/run/variants.obx` and
`tests/errors/variants.obx` pin this down.

Writing those tests exposed a false positive in the coarse rule: passing an
element of a walked array as mut invalidated the array itself, though a change
through the element can only free things below it. References now record the
reference they were derived from, and a change through a reference spares its
ancestors (sound because ownership is a tree; re-pointing an ancestor re-links
its descendants to its own parent). A test proves descendants are still
invalidated, and a mutant proves the exception isn't over-permissive.

## Milestone 4: verdict

**Method.** `python3 obo/bench/bench.py` runs four workloads written in both
dialects, checks they give identical output (obo with the leak check on), and
measures peak resident memory (through a small C launcher) and the best of three
wall times. Mainline's collector runs at its default (collect after allocating
200% of live data, minimum 8 MB) and at two tunings now available through
`OBX_GC_GROWTH` and `OBX_GC_MIN_KB`: tuned (100%, 1 MB) and aggressive (50%, 256 KB).

| Workload | Lines changed | GC default | GC tuned | GC aggressive | obo |
|---|---|---|---|---|---|
| lists | 1 of 22 | 8.4 MB, 60 ms | 1.2 MB, 42 ms | 0.5 MB, 73 ms | 0.2 MB, 54 ms |
| strings | 0 of 11 | 8.8 MB, 113 ms | 1.6 MB, 114 ms | 1.0 MB, 168 ms | 0.4 MB, 113 ms |
| tokens | 2 of 67 | 11.9 MB, 39 ms | 7.4 MB, 46 ms | 5.6 MB, 54 ms | 2.4 MB, 38 ms |
| trees | 1 of 25 | 15.1 MB, 38 ms | 10.1 MB, 46 ms | 9.0 MB, 65 ms | 5.0 MB, 36 ms |

`tokens` is the compiler-like workload: it tokenises obxc's own source into token
records ten times and interns every identifier in a hash table of string buckets.

The examples, ported to obo (`obo/examples/`), give the same output as mainline
with no leaks: `hello` and `defer` unchanged, `shapes` 2 of 38 lines changed,
`lists` 5 of 45.

**What it shows**
- **Memory.** obo uses 2 to 6 times less than even the aggressive collector, and
  3 to 8 times less than the tuned one; its peak tracks live data.
- **Time.** obo matches the default collector and beats the tuned and aggressive
  ones, which pay for collecting more often. The exception: freeing a list node
  by node is slower than sweeping in bulk (lists: 54 ms against tuned GC's 42 ms).
- **Syntax.** Small: `&` on read-only parameters and `mut` on mutating ones (later, `&mut` was dropped for `mut` before the name)
  cover nearly every change. The one awkward pattern is a global, which can't be
  borrowed and must be taken out and put back.
- **Rules.** Real but learnable: `take` instead of moving out of fields, storing
  temporaries before borrowing them, no moves inside `&&` or `||` or `defer`. The
  error messages say what to do instead.
- **Cost to the compiler.** obo-on-obx.obx is 3,898 lines against obxc's 3,222: 21% more.

**Caveats.** The workloads are small, and none has a graph-shaped data structure:
there are no back-references or cycles, and the generational handles agreed for
those aren't built. The coarse invalidation rule could still produce false
positives in larger code, as the ancestor case did.

**Recommendation: continue.** The trade the fork set out to test holds up:
substantially less memory, comparable speed, and a modest, local syntax cost. The
decisive next test is the one the agreed shape deferred: port the compiler itself
to obo. obxc is 3,200 lines with real graph structure (a variant points to its
closed type, which lists its variants; symbols point to types), so it will force
generational handles and stress the invalidation rules on genuine code. If obo
can compile itself with the same fixed point mainline reaches, the case for
making ownership the language's memory model is strong.

## Self-hosting port: Stage B

`obo/src/obo.obo` is obo's compiler written in the obo dialect. Stage B's
test, `python3 obo/tests/port_compare.py`, builds it with obo and compiles every
fork and mainline program, example, benchmark and error case with both, requiring
byte-identical executables and identical error messages, with the leak check on
in the port: 329 of 329 identical. The port compiling itself is a fixed point
(obo builds p1 from the port, p1 builds p2, p2 builds p3; p1 = p2 = p3).

**Memory, first measurement.** The decisive comparison: the same compiler logic
compiling the same source (the port's own) to the same bytes, once on the GC
(obo, built by mainline) and once with ownership (the port).

| | Peak memory | Time |
|---|---|---|
| GC default | 9.4 MB | 27 ms |
| GC tuned | 2.5 MB | 27 ms |
| GC aggressive | 1.9 MB | 27 ms |
| ownership, as first ported | 2.4 MB | 40 ms |
| ownership, local symbols freed | 1.5 MB | 40 ms |

As first ported, ownership did no better than the tuned collector. The cause was
retention, not fragmentation (live peak 2.17 MB against a heap high-water mark of
2.32 MB): `symArena` was append-only, so all 2,004 symbols lived until exit, each
with four arrays, where the GC frees a function's locals once it ends.

**Step 1 (done): free a function's symbols when it ends.** `fnDecl` marks the
arena before creating the parameters and truncates back to the mark after both
blocks close; nothing outside the blocks holds a local's handle, and any stale
handle traps. Live peak 2.17 MB to 1.27 MB. Patch: `tools/port_reclaim_syms.py`.

**Memory budget.** `OBO_STATS=1` makes the runtime print its exact live peak and
total allocation to stderr at exit (`tools/obo_stats.py`); the figures are
repeatable, unlike resident memory. port_compare fails if the port compiling
itself exceeds `PEAK_BUDGET` (about 3% over the measured figure; patch
`tools/port_budget.py`). Lower the budget as memory improves; raise it only with a
reason recorded here.

**Budget raised, 1,305,000 to 1,351,000** (measured live peak 1,311,184 bytes, about 3% over).
The allocation work has since lowered the live peak to 1,045,392 bytes; the budget is unchanged.
The reason: soundness fixes need per-path state for suspect references. The state at each join
now carries every local's suspicion (see "Deep references" below), which added 10,432 bytes.
More is coming: per-path reference bindings need the same kind of state. The allocation work
("Allocation (done)", above) reclaimed the room, and the budget is left for a separate decision.

**Tests.** `python3 obo/tests/mutate_port.py`: 6 mutants of the port, all caught
(two divergences by mismatch, two early frees by dangling-handle traps, two
retentions by the budget).

**Open.** Time: the port is about 50% slower than the GC on the same work and
allocated 28.6 MB in total before the allocation work, now 12.3 MB ("Allocation (done)"),
with items still allocated per use. Then the structural move: a committed
bootstrap built from the port, a promote rule with the fixed point, the remaining
mutants retargeted at the port, and obo-on-obx.obx retired to legacy.

## Aliasing: group borrowing's core rule (done)

**Direction.** The aim is Oberon's simplicity without a collector and without
Rust's complications. Most of Rust's difficulty comes from shared-xor-mutable:
nobody may change an object while a reference to it exists. Group borrowing keeps
only the rule that matters, no use after free (Ovadia, "Valen's Memory Safety",
October 2026). obo had part of Rust's rule: two references into one root, one of
them mut, could not be passed to a call. That rule is gone.

**Rules, as implemented** (`tools/m7_aliasing.py`, mirrored in the port by
`tools/port_m7_aliasing.py`)
- Several references, mut or not, may name one object: `bump(n, n)` with both
  parameters `mut` is fine. A change through a reference frees only things below
  its object, never the object itself (only owners free whole objects), so
  references to the object stay valid.
- A change under a root invalidates references that may point inside any root it
  *reaches*: a change under a reference parameter reaches the other reference
  parameters and every global (the parameter may be any of them); a change under a
  global reaches no parameter, because a function that might free under a global
  is never given references into it (the call-site check on effect summaries, and
  callers inherit effects, so this holds by induction up the call chain).
- At a call, a mut argument conflicts only with another argument that may point
  inside it (a deep one) from a root its changes reach. Valen's headline shape,
  a read-only whole and a mut part (`step(b, b.items[1])`), is accepted.
- Still conservative until paths are finer: two deep arguments, one mut
  (`twoMut(b.items[0], b.items[1])`), are rejected, since without field and element
  paths they might be one inside the other.

**A hole this closed.** A reference parameter could be a global the function
borrowed from: `f(mut p: &Box) { let x = &g.items; p.items = ...; use x }`,
called as `f(g)`, compiled and read freed memory (segfault with a reused block).
Nothing tracked that a parameter might be a global.

**Tests.** `tests/errors/aliasing.obx` (11 cases: 3 were holes, 4 are safe only
because aliasing used to be forbidden, 4 are the new call rule) and
`tests/run/aliasing.obx` (aliased mut references, the read-whole/change-part shape,
and two guards against the rule growing too strict: a plain copy of one parameter
survives a change through another, and a change under a global leaves a
parameter's references alone). The old "mut alias of one root" error case is
removed: it is now allowed. Mutants: 7 new (changes reaching too few roots, too
many, calls checking too little or too much), 2 stale ones retargeted; 48 of 48
caught. Stage B: 340 of 340 identical, within the memory budget, fixed point holds.

**Open: assignment targets across calls.** Found while checking whether anything
else assumed disjoint parameters, and independent of aliasing: the target's address
is computed before the right-hand side runs, so a call there can free the target.

    b.items[0].next = h(b)       // h(mut b: &Box) replaces b.items: writes into freed memory

Today `pinned` only stops the target's root being moved. Both this and the variant
through a reference local (`let e = &b.items[0]; e.next = h(b)`) compile; the leak
check catches them at run time only by luck. Next step.

## Borrowing through a handle (done)

**Direction.** The checker refused `&h.field` where `h` is a handle, which forced index
loops over `f.freed` and `t.variants` in the port. The decided rule allows the borrow,
and treats it as able to point into any object: a reference borrowed through a handle is
invalidated by anything that might free anything.

**Rules, as implemented** (`src/obo.obo`)
- `handleSym` (after `newSym`) is the root of such a reference. `borrowRoot` returns it for a
  designator that went through a handle, so a reference copied or borrowed from one is
  rooted there too, and so are the elements a `for` walk gives out.
- `invalidate`: a reference rooted at `handleSym` is hit by every change that might free,
  whatever its root (a scalar written through a handle frees nothing and invalidates nothing).
- `afterCall`: a call that is not `pureFn` invalidates every such reference.
- `recordFree`: a change through a handle (`freeRoot` returns `handleSym` when the designator
  went through one) marks the current function `freesAll`, so callers treat it as a call that
  might free. Without this, a pure-looking callee that writes a field through a handle would
  leave its callers' borrows valid.
- `callFn`: a reference rooted at `handleSym` may not be passed to a call that is not pure.
- `invalidateChange` (assignment, `replace`, `take`, `truncate`): a change through a handle
  also invalidates the handle-rooted references, even when the designator has no root, as
  `handle(p).items = ...` or `mk(p).items = ...` has none. Without this a handle temporary's
  change was not seen at all.

**Free sites checked** (each one invalidates through `invalidateChange`, `invalidate`,
`afterCall` or a root that reaches a handle-rooted reference; the nil-root early return in
`invalidate` is safe, since a nil root is a temporary or a constant, and no reference can
point into either):
- scope exit and `return` (`freeOwned`): invalidates each owned local.
- a move of a local (`consume`), and so `drop`: invalidates the local.
- assignment to an owned place (`assignmentOrCall`): invalidates the local, the global, or the
  root of a field or element, and records the free through `freeRoot`.
- `replace`, `take`: invalidate the place's root, and through a handle the handle-rooted
  references (`invalidateChange`); `take` and `replace` record `freeRoot`.
- `truncate`: invalidates the array's root, and through a handle the handle-rooted references,
  and records `freeRoot`.
- a `mut` argument (`callFn`): invalidates its root.
- a call's frees of globals (`afterCall`, `mightFree`): invalidates the global.
- a dropped call result (`rtCall1(RT_FREE, x)`): a fresh object that no reference can name.
- the frees at exit of globals: nothing is live after `main`.
- strings (`RT_STR_DEC`): not owned records, and never borrowed.

**Holes found while checking** (the first two are closed by "Deep references" below):
- A change through a handle, `h.items = ...`, or a call that makes one, invalidated references
  rooted at the handle, but not `&bag.items[0]` taken from an owned variable. Closed.
- A read-only parameter `p` and a handle `h` naming the same object: `let a = &p.items`,
  then `h.items = ...` frees that array, and `len(a)` read it. Closed.
- Not a hole, a false positive: a `return` inside a `for` walk of an owned local's reference
  is rejected with "cannot change what ... points into" (the frees at the return).
- Not a hole, a false positive: a walk over a handle-rooted reference whose body declares an
  owned local is rejected at the body's end, since freeing the local is a change and a handle
  could name it. The same walk over an owned variable's reference is accepted.

**Tests.** `tests/run/handle_walks.obx` (a walk through a handle parameter, a pure body,
an element change under the walk, and a borrow in `main`). `tests/errors/handles.obx`: the
old "cannot borrow through a handle" case became "passed to a call that might free", and 14
cases were added (a call in the walk body, an owned assignment, `take`, an assignment through
another handle, `truncate`, a borrow used after a call, a borrow used after a call that writes
through a handle, and an element used after its array is replaced; then, through a handle
temporary such as `handle(p).items`: an assignment, an assignment through a handle returned by
a call, `truncate`, `replace` and `take`, and a call whose body makes such an assignment).
`tests/errors/handle_calls.obx` is separate because the shared prelude of `handles.obx`
declares a global, and any invalidation of a global also reaches a handle-rooted reference,
which hides a missing call invalidation; the two cases here have no global: a self-call,
and a call that writes through a handle.

**Mutants.** `tests/mutate_port.py`, 6 new: borrows survive calls (caught by
`handle_calls.obx`, self-call), borrows survive other changes (caught by `handles.obx`, walk
with an owned assignment), borrows passable to a freeing call (caught by `handles.obx`, `sink`),
handle writes not recorded (caught by `handle_calls.obx`, `bump`), handle temporaries
invalidate nothing (caught by `handles.obx`, assignment through `handle(p).items`), and handle
temporary assignments record no free (caught by `handles.obx`, `zap`). `tests/mutate_fork.py`:
two index-loop anchors retargeted to the borrow form, and "borrowing through handles allowed"
renamed "handle borrows are untracked", since its message no longer exists.

**Fuzzer.** `tests/own_fuzz.py` modelled every handle borrow as an error. Now `handle_borrow_stmt`
is a valid kind (a borrow, a use, then an int field written through the handle, which frees
nothing), and `handle_borrow_dead_stmt` is an invalid one (a borrow, then a change that might
free, through the named handle, through a handle temporary, or by a call, then a use). The
compiler with the "handle temporaries invalidate nothing" mutant accepts 6 of them in 1000
full-profile seeds; the fixed compiler rejects them all. Coverage is thin: few generated
programs contain a handle borrow.

**Seed.** The checker change was promoted first, before the loops changed, because the old
seed rejects a borrow through a handle.

## Deep references (done)

**Direction.** A change through a handle frees the old owned children of the handle's
target (`h.items = ...` frees the items). A reference that points inside something, such
as `&bag.items[0]` or `&h.items`, could then read freed memory, while the existing rule
checked only references rooted at the handle. The decided rule (`LANGUAGE.md` section 10):
only owned changes count, a scalar written through a handle invalidates nothing, and a
reference to a whole object stays valid.

**Rules, as implemented** (`src/obo.obo`)
- `Sym.refDeep` marks a reference inside something (from a field or element, or from a
  reference that is itself deep). `invalidateDeep(via)` invalidates each live deep reference
  that is not an ancestor of the changed place; the error is the existing one.
- `invalidateOwnedChange(x)`: an owned change that goes through a handle, or through a
  reference with `freeRoot` `handleSym`, calls `invalidateDeep`. Used by assignment,
  `replace`, `take`, and `truncate` of an owned array (a scalar array frees nothing, so a
  `truncate` through a handle records no free; this refines the old unconditional `recordFree`).
- `writesHandles` (a `Sym` flag on functions): set by a direct handle free (`recordFree`),
  inherited in `afterCall` from any callee that has it, and assumed for a forward-declared
  callee (`addr < 0`). A call to such a function invalidates every deep reference.
- `callFn`: a deep argument may not go to a writer. A forward-declared callee is named
  separately ("its body is not known yet"). The self-call case is deferred (below).
- Self-calls are deferred to the body's end (`notePending`, `pendLine`, `pendMsg`). A self-call
  marks live deep references `suspect`; a later use of a suspect reference is noted; at the end
  of the body, if the function writes through a handle, the first noted position is reported.
  A loop that makes a self-call (`loopSelfCheck`, `selfCalls`) is noted the same way.
  Suspicion is per path. `snapshot` carries each local's suspicion beside its move state, and a
  re-point clears `suspect` on its own path only. The joins of `if`, `match` and loops keep the
  suspicion of every path (`joinSuspect`, `joinStates`); a loop's `break` and `continue` carry
  theirs out of the loop (`Loop.susp`, `joinFlags`); and a `while` condition's self-calls count
  for the loop check (`whileStat` takes `selfBefore` before the condition). A `for` range bound
  runs once, before the loop, so its self-calls need no loop check: the ordinary rule sees them.

The port needed no changes. Compiled with the rule, `src/obo.obo` has 0 rejections, so the
type-based narrowing was not needed. The port's peak live memory rose from 1,294,016 to
1,300,752 bytes when it compiles itself (the two new `Sym` fields). The per-path suspicion
below raised it to 1,311,184, over the 1,305,000 budget, which was raised to 1,351,000 (see
"Memory budget" above).

**Tests.** `tests/errors/handle_deep.obx`: 13 cases for the rule as described here, then six for
the per-path suspicion (below). The four from the holes list: `cut(h)` then a use of `first` (the
`d4` shape), a `both(p, h)` with `let a = &p.items` (the `g1` shape), a writer two calls
down, and a change through a reference borrowed through a handle (`e = &h.items`;
`e[0].kids = ...`), which the seed also accepted and read freed memory. Then a forward-declared
writer, a deep argument to a forward-declared writer, a deep argument to a direct writer, to a
writer two calls down, a handle temporary's field change, a `truncate` through a handle, a
self-call passing a deep argument in a writer, a self-call followed by a use of the deep
reference, and a loop that calls a self-writer while using a deep reference.
`tests/run/handle_deep.obx` is the valid side: a deep reference survives a scalar write and a
scalar truncate through a handle, reads through a handle, a recursive read-only walk, a
recursive writer whose only reference is re-pointed after the call, and a recursive writer that
passes only the whole object. It prints 11 values (expected output in source order) and passes
on both compilers. The last value is a reference re-pointed on both branches of an `if` after a
recursive call, then read before a writer: each path points it afresh, so it is accepted.

**Mutants.** `tests/mutate_port.py`, 18 new. The runner reports the first failing case for each
mutant, named here:
- body write doesn't invalidate deep refs: `run/handle_deep.obx` (the first failing case).
- callee writes keep deep refs: `handle_deep.obx`, the `cut` case.
- flag not inherited from callees: `references.obx` (an existing case, a free two calls down
  through a forward heading), then the `leaf`/`mid` case in `handle_deep.obx`.
- forward heading not assumed to write: `handle_deep.obx`, the `cutLater` case.
- forward heading deep argument allowed: `handle_deep.obx`, the `putLater` case.
- call-site deep-argument check dropped: `handle_deep.obx`, the `putLater` case (it expects the
  "cannot pass" message).
- deferred self-call check dropped: `handle_deep.obx`, the `walk` case.
- self-call suspects not marked: `handle_deep.obx`, the `rec` case.
- suspect uses not noted: `handle_deep.obx`, the `rec` case.
- re-pointing keeps suspicion: `run/handle_deep.obx`, the `repoint` function.
- loop self-call check dropped: `handle_deep.obx`, the `loopRec` case.
- change through a borrowed handle reference doesn't invalidate deep refs: `handle_deep.obx`,
  the `e = &h.items` case.
- shallow references invalidated by handle writes: `run/handle_deep.obx`, the `let q = &bag`
  line in `main`.
- scalar truncate invalidates deep references: `run/handle_deep.obx`, `trimVals`.
- scalar truncate through a handle is a write: `run/handle_deep.obx`, `clearVals`.
- suspect not joined at if or match: `handle_deep.obx`, "a recursive call on one branch of an if"
  (the `if` join) and "a recursive call in one match arm" (the `match` join). Both are accepted
  without the joins, as is the one-branch re-point case.
- loop self-call counter taken after the condition: `handle_deep.obx`, "a recursive call in a while
  condition".
- break suspicion not recorded: `handle_deep.obx`, "a recursive call before a break, with a
  reference made shallow before the loop's end" (the reference is re-pointed to a whole object on
  the other path, so the loop check does not see it).

The six new error cases, for the per-path rule: the one-branch re-point (fails on the pre-fix
compiler), the while-condition self-call (fails on the pre-fix compiler), the `if` join and the
`match` arm (both rejected by the pre-fix compiler too, through its global flag), a for range bound
with a self-call (rejected by both: the bound runs once), and the break case (fails on the pre-fix
compiler). The pre-fix compiler is the working-tree source built by HEAD's seed.

**Known false positive.** A loop that re-points a reference at the top of each iteration, then makes
the recursive call, is valid, but the loop check rejects it, since the check does not see the
re-point on the next iteration. The program is in `LANGUAGE.md` section 10, where it is an error
example.

**Loops and defers (follow-up, done).** Review found two holes and two false positives in the
rules above. All four are fixed.
- Loop-carried suspicion (a hole). The loop check read `refDeep` at the body's end, which is
  flow-insensitive: a shallow re-point on any path hid a suspicion carried to the next iteration,
  so a read at the top of the body or in the condition was accepted. Each loop now records, per
  local, the suspicion where its next iteration starts (`Loop.back`: the body's end if it falls
  through, and every `continue`, recorded in `statement`). `loopSelfCheck` reports a reference that
  is suspect there and that the loop reads. A read is counted by `designatorT` (`Sym.usedAt`
  against `useSeq`, not for a plain re-pointing), and a while condition's reads count because
  the count is taken before the condition. The per-loop sticky flag suggested in review would
  also reject a loop that reads nothing; the path-sensitive state does not. Nested loops: a
  self-call in an inner loop is checked by the inner loop, and its break and continue suspicion
  reaches the outer loop through `joinFlags`, so the outer loop checks it too.
- A body that always leaves a loop (a false positive, `p/h3`). Its end is not reached, so
  `loopBodyV` takes the suspicion after the loop from the entry state (`restore(entry)`), as it
  does for moves.
- A deferred recursive call (a false positive, `j/OK_N`). The body of a `defer` is compiled
  where the statement is, but runs where its block ends. `deferStat` takes back the suspicion its
  body's self-call made (`suspects`, `restoreSuspects`) and sets `Block.selfDefer`; `callDefers`
  applies it at each exit from the block (`markSuspects`). A read in the block before its end is
  accepted, and a read after the block is rejected. `break` and `continue` record their
  suspicion after `runDefers`, so a deferred call's suspicion reaches the loop's states.
- The loop variable of a for walk is rebound each iteration, so its suspicion is not carried
  around the loop (`loopSelfCheck` skips `lv`).
- A second review found two more holes, both fixed. A for walk reads its array on every iteration
  without going through `designatorT`, so a walk over a suspect reference was not counted as a read
  (a regression of the read test): `forStat` now counts the walked reference before the body. And
  a `defer` body that re-points a reference cleared its suspicion where the statement is, though
  the re-point runs only at the block's end (this one predates the follow-up): `deferStat` now
  takes back the body's effect on suspicion whether or not it made a recursive call. Tests: two
  error cases (a walk over a reference with a recursive call in its body; a defer that re-points a
  reference has not run yet) and three mutants, each caught by its case.

Known false positive, still open: a loop that reads a reference after re-pointing it in the same
iteration, when the reference is suspect where the next iteration starts (the example in
`LANGUAGE.md` section 10). Every other loop-carried read of a suspect reference is a real error.

**Tests.** `tests/errors/handle_deep.obx`: seven cases. A read at the top of a loop body after a
call on one path, a read in the condition, a read in an inner loop's second iteration, a read by an
outer loop after a `break` out of an inner loop, a read at the top after a `continue` (the
`continue` path only keeps the reference deep), a read after a deferred call's block, and a
read at the top of a loop after a deferred call in its body. The compiler before this change (the
working tree, as it stood when review began) accepted the body, condition and `continue` cases; the
other four were rejected too, each at another line or for another reason.
`tests/run/handle_deep.obx`: five valid programs that the old rule rejected (`retLeak`: a loop body that always returns; `deferRec` and `deferInIf`: a
use before a deferred call runs; `loopRepoint` and `loopBothRepoint`: a reference pointed afresh
after each recursive call), and `unusedRef`, a loop whose recursive call leaves a reference suspect
that nothing reads; the old rule accepted it too, and it guards the read test.
The review's control `p/h2c` (listed as rejected) is a valid program, not a hole: its
re-point is on both branches after the call, so it prints `2 2 2 9 9 9` and is now accepted.

**Mutants.** `tests/mutate_port.py`, 7 new, each failing the cases named here (checked per case
with the mutant compiler, so the list is every case that fails, not only the first one the port
comparison prints):
- loop-carried suspicion from the body's end dropped: the body-read case, the condition case, the
  nested and break cases, the deferred-loop case, and two older cases (`a loop calls a recursive
  writer`, `a recursive call in a while condition`); seven cases in all.
- loop-carried suspicion from `continue` dropped: the `continue` case.
- terminated body's suspicion kept: `run/handle_deep.obx` (`retLeak`).
- loop check reports unread references: `run/handle_deep.obx` (`unusedRef`).
- reads in a loop not counted: the same cases as the first mutant, and the `continue` case.
- deferred call's suspicion kept at the defer statement: `run/handle_deep.obx` (`deferRec`) and the
  after-block case.
- deferred call not applied where the block ends: the after-block case and the deferred-loop case.
  Two of the older mutants were retargeted to the new source text: self-call marking (now
  `markSuspects`) and break recording.

`tests/mutate_fork.py`: two stale anchors retargeted to `invalidateOwnedChange`: "handle temporary
assignments record nothing" and "element writes invalidate nothing". Final run 51 of 51.

**Fuzzer.** `tests/own_fuzz.py`: `owned_deep_stmt` (valid; a deep borrow from an owned element,
a handle change to a different place, then a use), `owned_deep_writer_stmt` (valid; a writer
that changes nothing a deep reference points into), `owned_deep_dead_stmt` (invalid; a deep
borrow, then a change through a named handle, a temporary, or a call, then a use),
`owned_deep_arg_stmt` (invalid; a deep reference passed to a writer). The writer call in the
invalid kinds includes a writer two calls down. Valid kinds are wrapped in a bare block, and the
handle borrow kind (`handle_borrow_stmt`) too, so its reference ends with the statement; without
that the generator's own later loops tripped the existing rule (seed 229, which the committed
seed also rejects). Detection at 300 seeds per profile, 600 programs, with the six fuzzer mutants
built from the final source: body write 6 failures, callee writes keep deep refs 3, flag not
inherited 2, call-site deep-argument check dropped 6; scalar truncate through a handle and
shallow references invalidated 0 each. Those last two are covered by the run and error cases
above, not by the fuzzer.

**Holes found, not fixed here.** Both are accepted by HEAD's seed too, and by the current
compiler. They will be fixed next. (The reference re-pointed on one branch is fixed: see
"Reference bindings per path (done)" below.)
- Uses inside `defer` bodies are not checked against later changes: `defer println(first.v)`, then
  `bag.items = new ...` (`scratchpad/review5/p/p03_defer_nohandle.obx`).
- Argument evaluation order: a deep argument is checked before a later argument's call frees what it
  points into: `use2(first, mk(h))` (`scratchpad/review5/p/e1_arg_order.obx`, and `e2`).

**Seed.** The rule is promoted after the suite and mutants pass, following `make promote`.

## Reference bindings per path (done)

**The hole.** A reference local's binding (what it may point into) was one for the whole function,
so a re-point on one branch overwrote it for every path. `var k = &a; if a.v == 2 { k = &b };
a = new ...; println(k.v)` compiled on HEAD and printed 0 from freed memory. Of the first nine cases in
`tests/errors/ref_bindings.obx`, seven compiled on HEAD though they must fail (the if, the handle form
with a deep reference, the recursive walk, the match arm, the else-if chain, the loop, and the items at
two indexes).

**The rule, as implemented: track both roots.** A binding is per path, and where paths join it is the
union, so a reference re-pointed on one branch may point into the places of both branches, and a change
under either is seen. The rule is in `LANGUAGE.md` section 4, with the example, and the join is stated
in section 10.

- `RefBind` (`src/obo.obo`, near line 881) is an immutable record: the roots, the deep flag and the
  place for each root, writable, and the parent reference. `arenaBind` makes them; `fnDecl` releases
  them when the function ends (`bindMark`). `PState` (near line 891) holds, per owned local, its moved,
  suspect and fresh flags and its binding. `snapshot`, `copyState`, `restore`, `joinStates` and
  `absorb` carry it through ifs, matches, loops, breaks, continues and defers.
- The join (`joinBind`, near line 1744, and `joinPath`, near line 1726): roots are the union; deep is
  OR; writable is AND; the parent is kept only where the two are equal. For places, equal is the same
  place; when one place is under the other (its path is a prefix of the other's), the join is the
  longer, so a change to the shorter place and every change under it is seen; any other pair joins to
  the whole root (`""`).
- Invalidation (`invalidate`, near line 1665) tests each binding entry: a change reaches an entry when
  the change is at its root and the entry lies under the change's place, or when the root is the handle.
  A re-point (the assignment, near line 3786) makes a new binding; it does not change the old one.
- The loop check (`loopBindCheck`, near line 2234, with `bindCovers` near line 1797): when a loop body
  frees something (`freeCount`, bumped by `invalidate` and `invalidateDeep`), a reference read before it
  is pointed afresh in the body is an error unless its binding at the end of the body is covered by the
  binding the body was checked with. The gate on frees is what keeps linked-list walks accepted (`p =
  p.next` in a loop that reads `p` at its top): a body that frees nothing cannot free what the next
  iteration reads, since the frees before the loop are in its entry state. Without the gate, 63 valid
  fuzzer programs were rejected.

**Path join: the spec's rule is unsound.** The first design took the common prefix of two places as the
join, and called the shorter place "more conservative". It is not: a join of `/a/b` and `/a/c` to `/a`
misses a change at `/a/b`, which frees the place one of the paths points at, and a join of `/a` and
`/a/b` to `/a` misses a change at `/a/b` too. The rule above is the sound one. The mutant that keeps
the shorter place is caught by `errors/ref_bindings.obx`, "a reference joined from a place and the place
below it".

**A self-parent hang, fixed.** `let w = &root; var k = &root; k = &k.kids[0]; k.kids = new []Item(0);
println(w.v)` never finished on HEAD (timeout, exit 124). `k = &k.kids[0]` made `k` its own parent, and
`ancestorOf` followed the parent chain forever. The re-point now keeps the old parent when the new one
would be an ancestor of the reference itself (assignment, near line 3786). The case is
`run/ref_bindings.obx`, `selfParent`, which prints 1.

**Memory.** The port compiling itself has a live peak of 1,083,760 bytes against the budget of
1,351,000 (`tests/port_compare.py`), and allocates 13,680,928 bytes in all. Main's allocation work,
merged here, had 1,045,296; the difference is the per-path bindings (38 KB). Before that work the same
change measured 1,758,240, over the budget, and the cause was not the new data: the image buffer grew
past 262,144 bytes and was reallocated while both blocks were live. Padding HEAD by about 4 KB of
emitted code reproduced the jump (1,727,616). Main's `elfImage` now sizes the image once, which removes
it. The per-state arrays and the bindings arena are small: measured before the merge, no function ended
with more than 32 bindings, and no state covered more than 9 owned locals.

**Tests.**
- `tests/errors/ref_bindings.obx`: 12 cases. The first nine are a re-point on one branch of an `if`,
  through a handle with a deep reference, in a recursive walk, in a match arm, in an else-if chain, in a
  loop, two roots changed, a change under the second root of a join, and the items at two indexes. Three
  more: a reference joined from a place and the place below it, a reference shallow on one path and deep
  on the other, and a reference writable on one path and read-only on the other.
- `tests/run/ref_bindings.obx`: six accepted programs with their output (`choose`, `both`, `newRoot`,
  `loopPoint`, `underIndex`, and `selfParent` for the hang above).
- `tests/own_fuzz.py`: two statement kinds. `choose_ref_stmt` (valid): a reference pointed at one of two
  locals, re-pointed on one path, read, with no change. `branch_free_stmt` (invalid): a reference
  re-pointed on one path, a change under the root the other path points into, then a read.
- `tests/mutate_port.py`: six new mutants, each caught by a named case: a binding that is not joined
  (last path wins), a join that keeps only the first root, a join that is deep only where both paths are
  (`errors/ref_bindings.obx`, "a re-point in one arm of a match", and "the same, through a handle, with a
  deep reference"), the spec's shorter-place join, a join that is writable where either path is, and no
  loop binding check. Eighteen existing port mutants, and twelve fork mutants, whose patterns the
  rewrite had replaced were retargeted to the same behaviour in the new code (`tools/port_ref_mutants.py`
  and `tools/fork_ref_mutants.py`).

**Seed.** The rule is promoted after the suite and mutants pass, following `make promote`.

## Milestone 1 design notes (as planned)

- **Freeing reuses the type descriptors.** The compiler already emits, for every
  record type, a descriptor listing its pointer fields, and every array records
  whether its elements are pointers. With references second-class, every pointer
  stored in the heap is owning (apart from handles), so one runtime routine can
  free an object and everything it owns by walking descriptors: no per-type
  destructor code needs generating.
- **Moves are tracked like narrowing.** A local holding an owned value is
  *live*, *moved*, or *maybe moved*. Using a moved local is an error; assigning to
  it makes it live again (freeing the old value first if it was live). At scope
  exit, live owned locals are freed; maybe-moved ones are a compile error at the
  merge point, or need a run-time flag (decide in M1; the error is simpler).
- **Leak oracle.** The runtime counts allocations and frees. With `OBO_CHECK=1`,
  a program that ends with live objects (globals excepted, or freed at exit) fails
  with a distinct exit code, so every test also tests for leaks.
- **Starting point.** `obo` is currently identical to obxc. The first change is
  the runtime: replace the collector with explicit free plus counting, keeping the
  allocator's size classes and poisoning.

## Optimization plan

Status: Phase 0 done; Phases 1 to 4 planned, not started. Phases are ordered by expected value per unit of cost.
Each has a gate; a phase that misses its gate is recorded here and stopped.

**Baseline.** A loop `s = s + i * 2` over `i < n` compiles with every local in a stack
slot. `s` and `i` are loaded and stored on every iteration, `i` is loaded three times,
and the loop takes a second branch (`jmp`) back to the test. Both `s` and `i` are
loop-carried, so each iteration waits on store-to-load forwarding: the cost is
latency on the critical path, not only instruction count.

**Ordering.** Register allocation (Phase 2) removes most of what a peephole pass would
remove, so the peephole comes after it and may never be built.

### Phase 0: measure (done)

The four workloads are `obo/bench/loop_obo.obx`, `fib_obo.obx`, `array_obo.obx` and
`alloc_obo.obx`. Each prints a checksum, which `obo/bench/micro.py` checks, and each
runs once with `OBO_CHECK=1` and must exit 0 (no leaks, no double frees). The script
builds the compiler with `make`, times each workload, and counts its code segment.
Rerun it with:

    python3 obo/bench/micro.py            # the table below; 5 timed runs each
    python3 obo/bench/micro.py --disasm loop

Measured at commit 4e11fb2 on an AMD Ryzen 7 8745HS under WSL2, with the seed
compiler and gcc 13 / GNU objdump 2.42. Times are without `OBO_CHECK`, the normal
configuration.

| Benchmark | Time, median of 5 (ms) | Instructions in code segment | Notes |
|---|---|---|---|
| loop | 576 (range 576-587) | 36 | `while i < n { s = s + i * 2 }` with n = 1e9. Loop body is 12 instructions: 5 loads, 2 stores, 2 branches. |
| fib | 619 (616-633) | 57 | `fib(40)`: about 331 million calls, about 1.9 ns each. Arguments are pushed on the stack. |
| array | 408 (406-414) | 105 | 1e6 elements built by `append`, then 200 rounds of `a[i] = a[i] + r` and `total += a[i]`. Three bounds checks per iteration, each reloading the array header. Peak RSS 16.0 MB. |
| alloc | 489 (434-514) | 56 | 2e7 records, each allocated and freed in its own iteration. Peak live 32 bytes, 640 MB allocated in total. |

Peak RSS is 0.1 MB for loop, fib and alloc, and 16.0 MB for array (the 1e6-element
array). The script reads it with `bench/peak.c`, the helper `bench.py` uses. A Python
`wait4` would report the parent's RSS too, because a forked child starts with the
parent's pages.

Code segment: the program's code, from the entry point (0x401000) to the end of its
executable segment (0x40109b). The runtime's text, at 0x10000000, is not counted.

The tight loop's body, from `micro.py --disasm loop`:

```
40103a:	mov    -0x18(%rbp),%rbx
40103e:	cmp    -0x8(%rbp),%rbx
401042:	jge    0x40106f
401048:	mov    -0x10(%rbp),%r8
40104c:	mov    -0x18(%rbp),%r9
401050:	imul   $0x2,%r9,%r9
401057:	add    %r9,%r8
40105a:	mov    %r8,-0x10(%rbp)
40105e:	mov    -0x18(%rbp),%r10
401062:	add    $0x1,%r10
401066:	mov    %r10,-0x18(%rbp)
40106a:	jmp    0x40103a
```

The plan's description holds. `i` (`-0x18`) is loaded three times, at 40103a, 40104c and
40105e, and `s` (`-0x10`) is loaded and stored. `n` (`-0x8`) is loaded again by the `cmp`.
The loop has a `jge` exit test at the top and a `jmp` back to it.

One surprise: 576 ms for 1e9 iterations is 0.58 ns each, about 2.2 cycles at the
3.8 GHz nominal clock. A loop whose two loop-carried values each go through a
store-to-load round trip would usually be limited to several cycles per iteration by
that round trip. This CPU may forward rbp-relative stores with almost no latency (memory
renaming), so the latency part of the plan's motivation may be weaker here than
assumed. Phase 2's measured gain will show whether it is.

*Gate:* met. These figures are the baseline for Phases 1 to 4.

### Phase 1: cheap codegen

- **Loop inversion.** Emit `jmp cond`, the body, then the condition at the bottom with
  a backward conditional jump. One branch per iteration instead of two.
- **Static stack depth.** Track the depth at compile time. Drop the alignment pads
  (`sub rsp,0x8` / `add rsp,0x8`) around runtime calls when the depth is already
  aligned, and drop `sub rsp,0x0` for functions with no locals.
- **Immediate and push forms.** `push imm` instead of `mov` then `push`.

*Gate:* the suite passes, benchmarks do not regress, and the byte-identity check
below is handled (see open decisions).

### Phase 2: register allocation for scalar locals

- **Candidates.** `int` and `bool` locals and parameters whose address is never taken.
  No borrow of a scalar, no handle.
- **Choice.** A pre-scan of the function's tokens counts uses of each local. The most
  used candidates get rbx, r12–r15 for the whole function.
- **Calls.** The existing convention already preserves live registers: the caller
  pushes them around calls. Check whether the push set has to grow.
- **Single pass.** The pre-scan reads one function's tokens and emits its code once.
  That is the one deviation from the single-pass rule, and it follows decision 5's
  "buffer one function at a time". It is recorded here so it can be revisited.

*Gate:* in the tight loop, the allocated locals make no memory accesses; the benchmark
improves by a measured amount; fib does not regress. A mutant that drops a register
save around a call must be caught by the suite.

### Phase 3: calling convention

Pass arguments of obo-to-obo calls in registers instead of pushing them on the stack.
Do this only if Phase 2's numbers show that call overhead matters on the fib benchmark.

*Gate:* fib improves; runtime calls are unchanged.

### Phase 4: checks and allocation

- **Redundant checks.** Hoist or remove bounds, handle and nil checks inside loops,
  using facts from loop conditions. Reuse the narrowing machinery where it fits.
- **Allocation.** The cause of the port's ~50% gap (Stage B, above): four arrays per
  `Item`, and copies in `copyInts` and `dupItem`. Measure with Phase 0's allocation
  benchmark before changing anything. Done: see "Allocation (done)" below.

*Gate:* each change shows a measured gain on its benchmark.

### Allocation (done)

Measured on the port compiling its own source (`obo/src/obo.obo`, 4,552 lines) with
`OBO_STATS=1` through `tests/port_compare.py`. Each row is one change, measured after
it; every kept change left Stage B at 396 of 396 identical.

| Change | Total allocated (bytes) | Live peak (bytes) |
|---|---|---|
| Baseline at 4e11fb2 | 28,648,672 | 1,326,112 |
| Jump lists as chains: a jump's rel32 field links to the next waiting jump (0 ends) | 23,068,272 | 1,323,712 |
| Narrowings as linked lists, shared between branches | 17,830,672 | 1,321,328 |
| Scanner holds the source as one string; tokens by `substr` | 16,479,488 | 1,319,760 |
| Source freed before the image is built | 16,481,840 | 1,177,168 |
| ELF image allocated once, at its final size | 16,206,832 | 1,044,560 |
| Register sets as bit masks, not arrays | 13,960,992 | 1,044,400 |
| No placeholder Item for a name or a runtime call's result | 12,266,272 | 1,045,392 |

Wall time, the minimum of 21 to 41 runs on WSL: about 33 ms at baseline and 28 to 30 ms
after the register change; the last change is within run-to-run noise. The baseline
peak is 1,326,112 bytes as measured here (the brief's 1,326,016 was an earlier figure).

Tried and dropped: freeing the code buffer as soon as the image has copied it. The
live peak fell by 416 bytes (1,044,400 to 1,043,984), which was not worth the extra
reassignment, since the peak is reached while the image is being copied.

`PEAK_BUDGET` stays at 1,351,000. The new peak leaves a margin of 305,608 bytes, which is
more than the mutant "parameters kept" adds (about 190 KB), so that mutant was no longer
caught by the budget. It is now caught when its peak rises above the unmutated peak
(`KEEPS_ALIVE` in `tests/mutate_port.py`).

Not done: the per-call arrays in `rtCall` and `rtCall0/1/2` (`regs`, `temps`, the
argument arrays), the `fixups` array of each Sym, and the snapshot copies (`copyBools`).
Item creations (about 32,000 per self-compile, mostly loads and voids) were left as they are.

### Peephole pass (not planned)

The obx design (`README.md`, "Future work: a peephole optimizer") still applies: an
emit-time pass that keeps the compiler single-pass. Revisit it only if reloads that
remain after Phase 2 are measurable on the benchmarks.

### Rules for every phase

- Each rewrite gets a mutant in `tests/mutate_port.py`.
- Benchmark times and instruction counts are recorded here before and after.
- Work happens on a branch in a worktree. `seed/obo` changes only through `make
  promote`, after the fixed point and the suite pass.

### Open decisions

- **Byte identity.** `tests/port_compare.py` requires the port's executables to match
  byte for byte, and any optimization changes the bytes. Proposed: the compiler gets
  an `--no-opt` flag, and `port_compare` uses it for the identity check. The suite
  and the fixed point run with optimization on.
- **Pre-scan.** Confirm that a token pre-scan is acceptable under the single-pass goal
  before Phase 2 starts.
