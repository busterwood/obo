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
in the port: 549 of 549 behave the same on the merged branch (539 before main's loop inversion, 462 before main's step 3). The port compiling itself is a fixed point
(obo builds p1 from the port, p1 builds p2, p2 builds p3; p1 = p2 = p3).

**Now behavioural** (decided with the optimization plan, below): each case must have the
same compile outcome in both (success, or the same message at the same line:col), and a
program that compiles must, when run with `OBO_CHECK=1` and a scratch directory as its
argument, give the same exit code, stdout and stderr. The executables' bytes may differ.
Stage B does not compare files a program writes into its scratch directory; a timeout on
both sides counts as agreement; and the compiler's own byte identity (stage 2 against stage 3)
is checked by `make promote`, not here.
The fixed point still compares bytes (`make promote`: stage 2 = stage 3). The reference is
`./obo`; `OBO_REF=seed/obo` compares against the seed instead, which is the comparison
that sees a change in code generation before it is promoted.

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
The allocation work has since lowered the live peak to 1,045,392 bytes (before Phase 1, which
raised it to about 1,079,400); the budget is unchanged.
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

**Assignment targets across calls (closed; see "Assignment targets: every form" below).**
Found while checking whether anything else assumed disjoint parameters, and independent of
aliasing: the target's address is computed before the right-hand side runs, so a call there
can free the target.

    b.items[0].next = h(b)       // h(mut b: &Box) replaces b.items: writes into freed memory

## Assignment targets: every form (done)

**Holes.** The check was made only for plain `=`. Three more red-team passes found the same
hole in every other form that locates a target, or an array, before a later call in its own
expression, all accepted:

- compound `+=`, `-=`, `*=`: `a[0].v += rep(a)` (use after free), through a handle
  (`h.v += grow(n)`, double free), a mut parameter, and a global field;
- `append`, `truncate`, `replace` whose array or target is a field or element, a handle
  temporary or handle, or a global: `append(a[0].kids, shrink(a))` (segfault),
  `truncate(b.items, mk(b))`, `replace(a[0].kids, rep(a))` (double free),
  `append(G.items, mk())`, `append(h.items, cut(h))`, and `append(garr, bumpArr())` on a
  whole global array;
- an index with a call in it: `b.items[mk(b)].v` (a read of a freed header: "index out of
  range" instead of the element), `b.items[mk(b)] = v`, `replace(b.items[mk(b)], ...)`,
  `take(b.items[mk(b)])`, `let r = &b.items[mk(b)]`, and the same through a mut array
  parameter. The index form is the one a call replaces the array in: the array's header is
  loaded into a register before the index runs.

**Rule, as implemented** (`src/obo.obo`):

- "Pure" here is `pureFn`: a callee that is compiled, frees no global, and takes no `mut` argument.
  A by-value owned parameter does not make a callee impure. The callee frees the object it is given,
  but the caller's argument is a move: `consume` counts it on the local (`touches`), and the checks
  below see that count change. So `eat(xs)` with `fn eat(a: []int)` is pure, and it is refused
  wherever `xs` is held across the call.
- `checkTargetAcrossCall` is the check `=` already made, now shared with compound assignment. It
  applies when the target is reached through a handle, through a global, or is a field or element
  (`xs == nil`), and a non-pure call ran while the value was computed.
- `ownerTouch(root)` is the sum of `touches` over the owned locals whose move frees the array that a
  designator holds: the root itself, or for a reference, every local in its binding's roots (the
  per-path binding of the reference, see section 4). A `^` handle (`handleSym`) and a reference
  parameter (whose owner is in the caller) contribute nothing. A binding's roots are stable between
  the snapshot and the check, and `touches` only grows, so a sum changes exactly when one of them moved.
  (Merge decision: `main`'s `ownerOf` read `refRoot`, which the per-path binding replaced. Taking the sum
  keeps `main`'s rule, a move of any owner the reference may point into is refused.)
- `heldTouch(root)` is `ownerTouch(root)` taken before the call, and `heldLost(root, t0)` reports that an
  owner was moved since (`ownerTouch(root) != t0`), or that `root` is a reference that a move or a change invalidated (`moved`).
  Both are taken for the root of the designator, so a field, an element, a multi-dimensional path, a
  record literal, or a reference is checked the same way as a whole local.
- The index check in `designatorT` snapshots before the array is loaded and compares after the index.
  A non-pure call in the index is still refused when the array is not a whole local (as before).
  `heldLost` refuses an index that moves the owner or invalidates a reference root.
- `append`, `truncate` and `replace` take the same snapshot of the operand's root before the value
  runs. An assignment (`=` or compound) whose target is reached through a field, an element or a
  reference (`x.deref`) does too, after the value is consumed. A whole local assigned directly
  (`xs = eat(xs)`) is not checked: the statement stores a new value into the variable after the call.
- `pinned` (a move of the root while it is assigned into) and `heldArray` (the call-count rule for
  `append` and `truncate`) are as before.
- A `mut` argument is not a move: a `mut` array parameter is read-only, so no call can reassign or
  replace its header. A `mut` argument does invalidate references into it, as before.
- The messages say "compute the value first" (or "compute the index first").

**Accepted, unchanged:** a pure call beside a field; a compound on a whole global scalar
(`gtotal += f()`); a whole local array with a call beside it that only borrows it or takes it
`mut` (`xs[g(xs)]` with `g(xs: &[]T)`, `xs[h(xs)]` with `h(mut xs: &[]T)`, `append(xs, h(xs))`); a
borrowed field indexed by `&`-taking call (`b.items[g(b)]` with `g(b: &Bag)`), a grid indexed by a
borrow of itself (`g[0][h(g)]` with `h(a: &[][]int)`), a reference indexed by a pure call that
borrows its owner (`r[lastOf(xs)]`); a move in a later operand, after the index is closed
(`b.items[0].v + eat(b)`); a call that moves a different array in an index (`ys[eat(xs)]`); a
for-range bound that moves a whole local array (no array is held there); a whole local array
reassigned from a call that takes it by value (`xs = f(xs)`); and the call-first form of each.

**Refused, a move of the owner while the array is held:** where `eat` takes its argument by value,
and the array or its root is held across the call:

- a whole local: `xs[eat(xs)]`, `xs[eat(xs)] = v`, `&xs[eat(xs)]`, `append(xs, eat(xs))`,
  `truncate(xs, eat(xs))`, `replace(xs, eat(xs))`, and a for-range bound that does the same;
- a field's array of a local record `b`: `b.items[eat(b)]` as a read, an assignment, a compound
  assignment, `&b.items[eat(b)]`, `take(...)`, `replace(b.items[eat(b)], v)` and a for-range bound;
  a record literal that moves `b` in the index (`b.items[idx(new Holder{bg: b})]`, with no call that
  moves `b` otherwise);
- a nested record's array: `n.kids[eat(n)]`; an element's array: `xs[0].kids[eat(xs)]`; a grid's
  inner index: `g[0][eat(g)]`;
- a record parameter's array, the parameter passed by value: `h.items[eat(h)]` with `h: Bag`;
- a reference, whether to a whole local (`let r = &xs; r[eat(xs)]`), to a field's array
  (`let r = &b.items; r[eat(b)]`), to an element's array (`let r = &xs[0].kids; r[eat(xs)]`), or
  a reference into a reference's index (`let q = &r[eat(xs)]`); a reference through a `^` handle
  indexed by a move of a local the handle may name (`let r = &h.items; r[eat(b)]`);
- a plain or compound assignment through a reference whose value moves the array it borrows
  (`r[0] = eat(xs)`, `r[0].v += eat(xs)`).

Before this change the field, element, grid, literal, reference and handle forms, and the plain and
compound assignments through a reference, compiled, and read or wrote a freed header (exit 3 or a
segfault in the probes that were run).

**Held places: a free counts as a move (the second review).** The rule above checks the owner for a
move. Two holes remained, both about a free. (A) A pure call consumes the value of a `replace` or `take`
that empties the array or record the statement holds: `b.items[sinkArr(replace(b.items, new []Node(0)))]`
read a freed header (rc=3), `xs[0].v += sinkI(replace(xs, ...))` wrote into freed memory, and
`xs[0].kids = sinkRes(replace(xs, ...))` freed a record twice (rc=6); `append(xs, sinkNode(replace(xs, ...)))`
and `truncate` crashed. (B) A handle is the root of a held place. `heldLost` looked at the handle local,
which no move touches: `let h = handle(b); h.items[eatBag(b)]` (rc=3), `let h = handle(xs[0]); h.kids[eatN(xs)]`
(rc=139).

Mechanism (`src/obo.obo`, `heldPush` and after). Each index, `replace`, `append`/`truncate`, and assignment
through a field, element or reference pushes the place it holds before its value or index runs, and pops it
after. A held place is keyed by what it is in. An owned place (`heldKey` is the owner, `heldPath` the path
inside it) is the place of a whole owned local. A reference local is held once per root of its binding, at the
binding's path in that root: a reference after a join may point into several owners, and each is held, so a move
or free of any of them is seen. A handle
made by `let h = handle(b)` from a whole owned local carries that local as its origin (`hOrigin` on the
handle's symbol and on each designator through it), so its held places are owned places of `b`. A handle
without an origin (a handle parameter, or one made from a field or element) and a reference parameter are
identities (`heldForeign`): a free through the same identity is compared by its raw path, and every other
free hits them. A whole local array (`heldWhole`) is not hit by a free through an identity or by a free of
unknown owner, since no handle or parameter names an array's header; a free of its own owner still hits it. `freeEvent(x, place, raw,
origin, ident)` classifies a free the same way. A move (`consume` calls `heldMove`) hits every owned place
of the moved local, and every identity. An assignment's own target (`heldPushStore`) is exempt from a free
of exactly its own place, since `take` leaves that place nil and the store then writes a valid place; a free
of an ancestor still hits. The checks compare the flag after the value or index ran ("cannot index 'b' with
an index that replaces or moves what it holds", "cannot index through a handle with an index that moves or
frees something", "cannot append/truncate an array with a value or count that replaces or moves what it
holds", "cannot assign through a place with a value that replaces or moves what it is in").

Holes closed in this round (tests/errors/held_places.obx): a handle's array appended or truncated with a value
or count that moves its owner (`append(h.items, keep(bag))`, `truncate(h.items, cnt(bag))`) was accepted and
crashed (rc=139), because the handle flag was lost between the designator and the hold; it is now carried in
`lastViaHandle`. The other cases there are the earlier forms (index, reference index, assignment, compound
assignment, a field assigned a value that moves its own local), each one refused.

Two survivors of the mutants were argued equivalent in the first version of this section. They are not:
(1) `take` can free a held place. A handle made inside a narrowed block, `if o != nil { hh = handle(o) }`, names
the object that `take(o)` empties, so `hh.kids[sinkN(take(o))]` read freed memory (rc=139, probe p2/t1). (2) The
reference's path in its root matters: a free strictly below a reference's path is not covered by the moved flag,
`let r = &xs[0]; r.kids[sinkI(replace(xs[0].kids, ...))]` (rc=3, probe p/q1). Both are error tests now
(`held_places.obx`), and the mutants that drop the take's free and the reference's path are caught by them.

Accepted, with outputs (tests/run/held_places.obx, tests/run/call_targets.obx): a take of a sibling field
beside the indexed array (`b.items[sinkN(take(h.opt))]`); a replace of a sibling field through a handle
(`h.items[sinkN(replace(h.opt, nil))]`); a temporary handle to a named local (`b.items[sinkN(take(handle(b).opt))]`,
whose origin is `b`); a replace of the handle's own array assigned back to it (`h.items = sinkArrRet(replace(h.items,
...))`); a local array indexed by a replace or a take through a handle of the owner; a mut parameter's array
indexed by a take of a sibling field of the parameter, and a mut parameter's field assigned the take of that
field (`p.opt = passOpt(take(p.opt))`, sound: the take empties the target before the store writes it); a handle to
one local indexed by a move of another local (`hob.items[sinkBag(ot)]`, since the handle names `ob` alone); a
whole record replaced and its old value consumed in a later index of the new record's array; a handle to one
object indexed with a temporary of another; an element taken from the array indexed (`xo[sinkOpt(take(xo[1]))] = v`,
which frees an element and not the array header); and an assignment whose value takes its own target
(`tk.opt = passOptIB(take(tk.opt))`).

Refused: a move of a local that a handle was made from, inside a loop. A handle `let h = handle(a)` made from a whole owned local records that local as its origin, so its held places are keyed to `a`. A whole move of `a` (`var c = a`, `var c = id(a)`, a record field, `replace(a, ...)`, `take(a)`) or an assignment over `a` clears that origin on every `let` handle that names `a` (`originMove`). Such a handle then has no origin: it is an identity, which every move and free reaches, so a later index through it that moves or frees something is refused by the held-place checks (probes u1, u3 and the optional take, in `tests/errors/held_places.obx`). This is sound because a move does not free the object: `c` holds the same object, and the handle still names it by its generation. The one case the clearing does not cover is a loop, because the body is compiled once: a whole move of `a` inside a loop that is deeper than the declaration of a live `let` handle with origin `a` is refused ("cannot move 'a' inside a loop while handle 'h' made from it is in scope"), since the next iteration would still see the origin. A handle declared inside the loop is fresh each iteration and is not refused (`c_fresh_loop`). A temporary `handle(a)` records no origin on any symbol, so a move after it is accepted (`c_p1_let_then_move` to `c_p5_temp_then_move`, run tests). The origin list (`originSyms`) keeps only the handles in scope: a block's handles leave it when the block closes, because their symbols are freed with it (a dangling entry there was a compiler abort in an early version of this rule). An assignment over `a` clears the origins, and it also frees the handle's object: `let h = handle(a); a = nb(); h.items[0]` compiled and trapped at run time on the handle's generation check; it is now refused at the use (see "Assignment over a handle's owner (done)"). A handle made from a field, or through another handle, has no origin; its place is an identity, and a move of any local while it is held is refused, because `heldMove` hits every identity entry (`let h = handle(p.first)`, and the chained `hh.hb.items[...]`). A handle parameter has no origin, and the same conservative rule applies (f3 is kept refused). A move or free of a local the callee declared (not a by-value parameter, not a global) no longer reaches a reference parameter's place, because a reference cannot point into the callee's own object; this is the false positive `p.items[sinkBag(t)]` (f1, f2) and its replace and take forms (f4, f5). A by-value parameter's object and a global are still hit. Refused still: f8, two `mut` parameters where a free through one is indexed through the other, since the two may name the same object. A reference to a field of a reference parameter is bound to the whole parameter (`itemBind` drops the field path through a deref), so `let r = &p.ys; p.xs[sinkI(replace(r, ...))]` is refused although the fields differ; the direct form `p.xs[sinkI(replace(p.ys, ...))]` is accepted. This is a precision limit of the binding model, left as it is. The refused forms are the cases in `tests/errors/held_places.obx`. Known false positive (probe f2): a handle used only before a loop blocks a move of its owner inside the loop, because the loop rule asks whether the handle is in scope, not whether it is used in the loop. An attempt to close it (ask whether the loop named the handle before the move) was unsound and was reverted, see "Assignment over a handle's owner (done)".

Not closed: the argument-order hole `two(&xs[0], eatArr(xs))`, a reference into an array passed in one argument with the array moved by a later argument of the same call. It is a known hole, left alone in this round as the coordinator asked; the probes are `review16/ra/ra1`, `review16/p/a1` and `review16/ra/ra3`, listed with the holes above. (Closed since: see "Argument order (done)".)

Probes that were written but are not valid programs on either compiler (typing or syntax errors, so they tested
nothing) are h1-h13, k5-k8, z1-z5, w2, x3, y4, y9 in the probe set (`review14/`).

**Tests.** `tests/errors/call_targets.obx` has 58 cases (the earlier 59, less the handle-of-another-local case that is now accepted: its run control is in `tests/run/call_targets.obx`). `tests/errors/held_places.obx` has 35 cases (the defer pairs pin that `loopOriginCheck` relies on defers never moving: a move inside a defer is refused with "cannot move" while a let handle from an enclosing loop is in scope, both when the handle is declared before the loop and inside the loop body): the probes above, the review17 handle probes H1-H9, the compound and assignment forms, the origin move in a loop, the replace and take holes (u1, u3, the optional take), a replace of the owner's array through a handle, handles made from a field and from a global, a chained handle, the free through a reference to a handle, and the by-value parameter moves and frees. `tests/run/held_places.obx` has 30 functions with outputs: the accepted forms above, the callee-local cases f1, f2, f4, f5, the origin moves that are now accepted (plain, call result and record field: `c_mv_sibling`, `c_mv_call`, `c_mv_field`), the temporary-handle forms p1-p5 (`c_p1_let_then_move` to `c_p5_temp_then_move`), and a handle declared in a loop body whose owner is moved there (`c_fresh_loop`). The nit about the last-designator globals (`lastPlace`, `lastRaw`, `lastOrigin`, `lastIdent`, `lastViaHandle`): they are set once, at the end of `designatorT` after its index expressions are parsed, and each reader takes them straight after `designator()`, so a nested `expression()` cannot leave a stale value; the only path past `nameItem` without setting them is after an error. No probe found a case.

**Mutants.** `tests/mutate_fork.py`: 146 entries, 138 caught, 8 equivalent (listed in its `EQUIVALENT` table with the reason, and reported as EQUIV). The 15 origin-rule mutants (`ORIGIN_MUTANTS`) cover the clearing hooks in `originMove` (consume, replace, assignment), the truncation of `originSyms` at a block's `hmark`, the loop depth in `loopOriginCheck` (`<=`, and the check dropped), the depth recorded for a let handle, a var handle given an origin, and a temporary handle given none; each is killed by a named error or run case. Four of the new entries are equivalent: the take hook (a take needs its optional owner widened first, and only an assignment over a narrowed owner does that, so the assignment hook has already cleared the same origins) and the `!x.viaHandle && !x.deref` guards on the replace, take and assignment hooks (a deref designator has no sym, and a handle local is not owned, so `originMove` returns early). The held-place mutants are: a replace or take that does not free, moves that do not reach held places or do not free handle targets, identity frees that do not reach their entries or ignore the path, owned frees that ignore the path, the assignment exemption removed for owned places and not applied through handles, origin ignored when holding and when freeing, whole local arrays hit by identity frees, reference parameters given their owner's place, each hold removed, the binding's path ignored, and the second review's gaps: an origin without the non-handle or whole-path guard, a global origin, chained handles keeping the first origin, the handle-root and reference-root joins (held and freed), unknown frees hitting only whole arrays, every held place whole, moves and frees reaching reference parameters from callee locals, and a by-value parameter counted as a callee local. The first four equivalents: `origin needs no non-handle designator` and `origin needs no whole path` (a designator through a handle, or with a path, has no sym, so `wholeOwnerOf` gives nil); `origin of a global is kept` (`owned` is set only by `addOwnedLocal`, so a global never has an origin, and the storage test is redundant); and `a handle root of a reference is not an identity (held)` (equivalent because the older `heldLost` check on the reference local catches the case, not the held entry: under the mutant `foreign` is false, so `heldMove` does not hit). `tests/mutate_port.py`: 128 entries, 128 caught, judged at the committed budget of 1,351,000 (see "The port's memory budget" below); the port mutants change `src/obo.obo` and are judged by `port_compare`.

**Fuzzer** (`tests/own_fuzz.py`): invalid kinds, `replace_sink_index_stmt` (a field's array indexed by a pure
call that consumes a replace of that array), `compound_target_stmt`, `append_target_stmt`,
`truncate_target_stmt`, `index_target_stmt`, `index_assign_target_stmt`, each a field of a local
record whose array a call in the same expression replaces; whole-local kinds, `moved_index_stmt`,
`moved_assign_stmt`, `moved_append_stmt`, `moved_truncate_stmt`, `moved_replace_stmt`, each a local
array passed by value to a call in the expression that uses it; and `moved_root_index_stmt`, a field's
array indexed by a by-value call on its record. All are rejected.

**Messages.** A reference invalidated in a loop and then continued past said "is moved inside
the loop; it must have a value again before continue"; it now says it is invalidated and must be
pointed again. Done, as "Invalid on one path" below: the join no longer reports "'p' is invalidated on one path but not another". A reference invalid on one path is refused at a use, and a reference never used afterwards is not an error (the red-team probe `red2/n12.obo`, which is not in the repository, had that shape; `run/one_path.obx` has it).

**The port.** It compiles unchanged under the new rules (no spot needed rewriting).

**The port's memory budget, after the merge of main.** `PEAK_BUDGET` stays at 1,351,000, and the merged port passes it. The port compiling itself peaks at 1,246,576 bytes live, a margin of 104,424 bytes under the budget. The merge of main's loop inversion (`62292f3`) added 17,152 bytes to the 1,229,424 this branch measured before it, so the margin is smaller than before the merge. The origin rule (`originSyms`, `originMove`, the block marks) added 4,560 bytes to the 1,224,864 measured before it. The merge of main's step 3 (defer uses) added 13,760 bytes to the 1,211,104 this branch measured before it. The budget was not raised.

Before the origin rule, this branch's compiler changes added 18,272 bytes to the peak: main's source measures about 1,079,400 bytes
with the same method, and this branch's source 1,097,776. (The figure moves by about 150 bytes
with the paths on the command line.) The cause was not separated out (the
branch changes about 70 lines of the compiler source); it is small against the margin.

`mutate_port` judges each mutant by `port_compare`, so with the budget over the peak every mutant
fails the memory check and "caught" means nothing. Before the merge the runs used a temporary budget
of 1,777,000, which was not committed; the merged run needs no change, and all 68 mutants are caught
at the committed budget. The memory mutants (for example "parameters kept") are caught only by the
budget, so the mutate_port result is meaningful only while the budget passes.

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
checked only references rooted at the handle. The decided rule (`LANGUAGE.md` section 12):
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
re-point on the next iteration. The program is in `LANGUAGE.md` section 12, where it is an error
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
  applies it at each exit from the block (`markSuspects`). (Superseded: `selfDefer` was removed, see the
  follow-up below; the same suspicion is now a logged free replayed at the exit.) A read in the block before its end is
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
`LANGUAGE.md` section 12). Every other loop-carried read of a suspect reference is a real error.

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

**Holes found, not fixed here.** This one is accepted by HEAD's seed too, and by the current
compiler. Still open after the follow-up below. (The reference re-pointed on one branch is fixed, and a re-point
inside a defer body is refused: see "Reference bindings per path (done)" below.)
- Argument evaluation order: a deep argument is checked before a later argument's call frees what it
  points into: `use2(first, mk(h))` (`scratchpad/review5/p/e1_arg_order.obx`, and `e2`). Closed since: see
  "Argument order (done)".

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
in section 12.

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
- The loop check (`loopBindCheck`, near line 2264, with `bindCovers` near line 1800). A change that may
  free something (`invalidate` and `invalidateDeep` call `markCarriedFreed`) marks each reference local
  that is not pointed afresh on every path (`carryFreed`, a fourth flag block in `PState`). A read of a
  marked reference records its use (`freedUse`), and a marked reference at the end of the body is carried
  round the loop. A loop is an error where such a read, or such a carried reference, has a binding the
  body was not checked with (`bindCovers`), and where a binding comes back read-only that the body was
  checked against as writable. A re-point clears the mark, so a reference pointed afresh after the free
  is accepted (`run/ref_bindings.obx`, `loopCache`). The marks replace the frees counter that gated the
  check before. The counter was read before a while condition, so a free in the condition was missed,
  and it could not tell a free on one path from one on every path. A linked-list walk (`p = p.next`
  with `p` read at the top) stays accepted, since nothing in its body frees.
- A defer body that points a reference declared before it at something else is refused (`deferMark`,
  `declaredBefore`, the error "cannot point 'k' elsewhere in a defer"). The body runs where its block is
  left, at every exit, and its binding effects at compile time are discarded when the body is restored
  (`restoreDeferred`). Applying them at each exit would need the state at each exit, so the simple rule
  is taken: the change is refused. Declaring the reference inside the defer body is still allowed.
- The writable rule comes from the loop check, not from `bindCovers`: `bindCovers` is sound as coded
  (a write checked against the entry binding must be allowed through the back-edge binding, so
  "writable wherever the entry is" is the rule), and its comment said it the other way round. The
  case where a reference is writable where the loop starts and read-only where it starts again is an
  error (`errors/ref_bindings.obx`, "a reference writable where a loop starts, read-only where the loop
  starts again").
- Future work: applying a defer body's re-point at each exit in place of the refusal. (A change made
  through a reference used to be taken to reach the whole root it borrows from; see "Reference-local
  paths (done)".)
- Stage B now compares behaviour: the output of the port compiled by the seed and by the new compiler,
  not the bytes of the code. A byte difference in a return spill (the retLeak case) is harmless and is
  accepted. In this build stage B is 418 of 418 identical.

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

**Memory.** The port compiling itself has a live peak of 1,097,408 bytes against the budget of
1,351,000 (`tests/port_compare.py`), and allocates 13,885,696 bytes in all. Main's allocation work,
merged here, had 1,045,296; the difference is the per-path bindings and their marks (52,112 bytes). Before that work the same
change measured 1,758,240, over the budget, and the cause was not the new data: the image buffer grew
past 262,144 bytes and was reallocated while both blocks were live. Padding HEAD by about 4 KB of
emitted code reproduced the jump (1,727,616). Main's `elfImage` now sizes the image once, which removes
it. The per-state arrays and the bindings arena are small: measured before the merge, no function ended
with more than 32 bindings, and no state covered more than 9 owned locals.

**Tests.**
- `tests/errors/ref_bindings.obx`: 17 cases. The first nine are a re-point on one branch of an `if`,
  through a handle with a deep reference, in a recursive walk, in a match arm, in an else-if chain, in a
  loop, two roots changed, a change under the second root of a join, and the items at two indexes. Three
  more: a reference joined from a place and the place below it, a reference shallow on one path and deep
  on the other, and a reference writable on one path and read-only on the other. Five more for the
  defer and loop rules: a defer re-point in an if, the same in a loop with a break, a while condition
  that frees a global the reference points into, a loop that frees what a reference points into before
  reading it at the top, and a reference writable where a loop starts and read-only where it starts again.
- `tests/run/ref_bindings.obx`: eight accepted programs with their output (`choose`, `both`, `newRoot`,
  `loopPoint`, `underIndex`, `selfParent` for the hang above, `deferLocal`, a defer that points a
  reference declared inside its own body, and `loopCache`, a loop that frees and re-points before the read).
- `tests/errors/handle_deep.obx`, "a defer that re-points a reference declared before it is refused":
  the case that expected the re-point to be tracked now expects the refusal at the defer.
- `tests/own_fuzz.py`: two statement kinds. `choose_ref_stmt` (valid): a reference pointed at one of two
  locals, re-pointed on one path, read, with no change. `branch_free_stmt` (invalid): a reference
  re-pointed on one path, a change under the root the other path points into, then a read.
- `tests/mutate_port.py`: six new mutants, each caught by a named case: a binding that is not joined
  (last path wins), a join that keeps only the first root, a join that is deep only where both paths are
  (`errors/ref_bindings.obx`, "a re-point in one arm of a match", and "the same, through a handle, with a
  deep reference"), the spec's shorter-place join, a join that is writable where either path is, and no
  loop binding check. Six more fork mutants (`tests/mutate_fork.py`) cover the marks and the defer rule:
  frees that mark nothing, a read of a marked reference that notes nothing, the writable trigger dropped,
  a defer re-point not refused, a re-point that keeps its mark, and a loop start that forgets the marks
  (the case of a free in the condition). A mutant that restored a defer body's binding only when the
  body made a self call was removed: with the defer re-point refused, nothing it changes is observable,
  so it is equivalent. Eighteen existing port mutants, and twelve fork mutants, whose patterns the
  rewrite had replaced were retargeted to the same behaviour in the new code (`tools/port_ref_mutants.py`
  and `tools/fork_ref_mutants.py`).

**Seed.** The rule is promoted after the suite and mutants pass, following `make promote`.

## Loop marks, defer frees and the open holes (follow-up, done)

A review of the loop check and of defer bodies, after the per-path bindings. Its items are numbered as
the review gave them. The rules are in `LANGUAGE.md` sections 4 and 12.

- **FP-1, loop-entry scoping (done).** A free before a loop marked the references inside it (the marks
  were one flag block for the whole function). A loop now starts with the marks from before it kept
  aside, and joins back where it ends (`loopMarksSave`, `loopMarksJoin`), so a mark set in a loop
  reaches the code after it, and an outer mark does not reach a read inside. `probes fp2_two_loops`
  and `FP5_free_then_loop` print 1 2 2, and the realistic `FP7_realistic` prints 28.
- **Overlap (FP-1, the check).** Each change that may free something is logged while a loop or a deferred
  body is compiled (`freeLog`, `logFree`, `FreeRec`: kind, root, place, and the reference locals that are
  not pointed afresh). A loop's error is raised only where a logged free overlaps the binding the reference
  has on the back edge (`hitBy`, `applyExit`), so a free of something the reference does not point into is
  no longer an error (`unrelatedFree`, `unrelated2` and `freeBetweenLoops` in `run/ref_bindings.obx`). The
  marks themselves are still coarse, and the overlap decides. `preMark` is the case with a free before the
  loop, and the reference re-pointed after it.
- **FP-2, the writable rule (done).** A reference writable where a loop starts and read-only where it starts
  again was an error on every such loop. It is now an error only when it is written through the reference
  before it is pointed again (`writeUse`, set where an assignment writes through a reference, compared with
  `useBefore`). `readOnlyLoop` (run) is accepted and `walk` (error, written before the re-point) is still
  refused.
- **H-1, frees on the way out of a loop (done).** A free in a `while` condition, or on a `break` or `continue`
  path, was lost at the loop's exit. Each exit now records the frees logged on its way out (`Loop.exits`,
  `Loop.conts`, with `exitAt` and `contAt` as log positions), and the loop applies them to the references
  when it ends (`applyExit`). `carry_out_break` and `carry_out_cond` are rejected.
- **H-2, a defer body's frees (done, replay).** The body's frees were applied where it is declared. The
  option taken is to replay them where the block is left. The frees are taken out of the log at the
  declaration and kept with the block (`Block.frees`, `deferStat`), and `callDefers` replays them at each
  exit from the block (normal end, `break`, `continue`, `return`: `replayFrees`). The body's moves are not
  taken where it is declared (the moved flags are restored, the owner's moves are kept). The other option,
  refusing a defer body's frees, would refuse valid programs, so replay was chosen. A re-point in a defer
  body stays refused, since replaying a binding change needs the state at each exit (see future work above).
  `defer_hole1` and `unsafe_defer_free_loop` are rejected.
- **Recursive self-call, deep carry.** The self-call branch of `afterCall` now also marks the deep references
  it may free (`markCarriedDeep`), as the other changes do. The check asked for was a case where the innermost
  call frees through a handle and an outer loop reads a deep reference into the freed place on its next pass
  (`scratchpad` `mk/self3.obx`). The seed and this compiler both reject it ("'k' may point to freed memory",
  at the read), so no hole is shown. `mk/self2.obx` (a re-point after the call) is accepted and prints 7 on
  each of its fourteen lines. The explicit `markSuspects` at block end for a deferred self-call (`Block.selfDefer`)
  was removed: the free that `markSuspects` logs is replayed at the same exit, so it was a copy of that replay.
- **Memory.** The first run of this follow-up measured 1,412,368 bytes, over the budget. The cause was
  `freeArena`, which kept every logged free for the whole compile. The outermost loop now remembers where its
  records begin (`loopLogStart`, `arenaBase`), and truncates the arena back to that point at its end, when no
  record is referenced any more: a block that holds a record is inside the loop and has closed by then. The
  live peak is 1,134,304 bytes against the budget of 1,351,000 (before this follow-up: 1,097,408). The budget is
  not raised. Allocation in all is 15,377,184 bytes.

**Review 3 (fixed).** Two regressions from the entry above were found in review and are fixed.
- A write through a `mut` argument, `append`, or `truncate` through a reference did not set the write
  mark (`writeUse`), so the writable rule missed it: `k` passed to a mut parameter, or appended to,
  on a pass where it is read-only on the next. The writes now go through `noteWrite`, the same mark
  the assignment and `invalidateChange` set (`callFn`, the `append` and `truncate` builtin). Probes
  `w_mutarg`, `w_nestedarg`, `w_defer_mutarg`, `w_append`, `AP_ro_append_loop` are rejected again, as
  by 82dba34. `replace`, `take` and `truncate` already marked through `invalidateChange`; the
  assignment marks through its own deref test.
- A deferred change was replayed without the reference it was made through, so the reference's
  ancestors (the one a child write was taken from) were moved: `let s = &p.kids[0]; defer { s.kids =
  ... }` rejected a read of `p` after the block. The free log now keeps the `via` of each change
  (`FreeRec.via`), and the replay passes it (`replayFrees`). The loop's exits and back-edge test use
  the same exception (`freeHits` takes the local, and skips references the change's `via` is or is
  derived from, as `invalidate` does). `FP_defer_via_child_ancestor`, `_fn` and `_loop` are accepted;
  a deferred write to the parent's own array (`p.kids = ...`) is still refused where a reference into
  that array is read after the block (the error case "a deferred write through a reference frees what
  a reference into its place points into"). The loop case
  `loopContChild` covers the exit path. The defer probe `l7_defer_in_loop_repoint_each` stays rejected,
  as the non-defer form is by the seed.
- Tests: `tests/errors/ref_bindings.obx`: three cases (a mut argument and an append through a
  reference read-only on the next pass; a deferred write that frees what a reference into its parent
  points into, read after the block). `tests/run/ref_bindings.obx`: `deferChild`, `deferChildLoop`,
  `deferHandleChild`, `loopContChild`, `mutArgOk`. Mutants in `tests/mutate_fork.py`: a mut argument
  is not a write, an append is not a write, a deferred change or deep change replayed without its
  reference, a deferred change not replayed, and a logged free that ignores its reference. 69 of 69 are
  caught before the merge with `main`; 85 of 85 after it (`main`'s mutants included).

**Merge with `main` (8e3eb92).** The conflicts were in `src/obo.obo` (`callFn`, `designatorT`, `assignmentOrCall`,
`consume`), `tests/mutate_port.py`, `tests/mutate_fork.py`, `tests/own_fuzz.py`, and the docs. Both rules are kept.
Decisions that were not a choice between the two rules:
- `ownerOf` (`main`, reads `refRoot`) became `ownerTouch` (see the paragraph on targets across calls above).
- The mutant "deferred self-call not applied at block close" was dropped from `mutate_port.py`: its anchor
  (`selfDefer`) no longer exists. The mutant "reference roots not checked for moves or invalidation" was
  retargeted to `ownerTouch`.
- `own_fuzz.py` runs both sets of operations (my `branch_free_stmt` and `main`'s target and move kinds).
- `break` and `continue` in a loop now report "is invalidated inside the loop" for a reference and "is moved"
  for an owned local. This is `main`'s wording, and the same probes are rejected as before.
Measured after the merge: `run_fork` 325 of 325, `port_test` 415 of 415, `port_compare` 476 of 476 with a peak of
1,161,072 bytes (budget 1,351,000), `own_fuzz --count 600` with 0 failures in 1200 programs, `mutate_fork` 85 of 85,
`mutate_port` 73 of 73, `doc_check` 24 examples with no problems, `bench/bench.py` run for all four workloads.

**Open after this follow-up** (accepted by the seed too; each reads freed memory when run):
- (H-3 and the defer-read hole are closed: see "Defer uses (done)" below.)
- Argument evaluation order (`mk/arg_order_fresh.obx`: `use2(first, mk(h))`, where `mk` frees what `first`
  points into). The argument is checked before a later argument's call runs. Closed since: see "Argument
  order (done)".
- The precision limits in "Reference bindings per path": a change through a reference reaches its whole root,
  and the known false positive in `LANGUAGE.md` section 12 (a re-point in the same iteration, with the reference
  suspect at the top of the next pass).

**Tests and checks.**
- `tests/errors/ref_bindings.obx`: 24 cases, covering the loop exits (break, continue, a while condition), the
  defer body at block exit and in a loop, a recursive call that frees through a handle on its innermost pass,
  and a loop that frees before an inner loop and reads at the top of the next pass.
- `tests/run/ref_bindings.obx`: 17 accepted programs with their output, including `freeBetweenLoops`,
  `unrelatedFree`, `deferTemp`, `deferRepoint`, `readOnlyLoop` with `readOnlyCaller`, `preMark`, `unrelated2`
  and `deferEarly`.
- `tests/mutate_fork.py`: 63 mutants, all caught. Added: a loop start keeps the marks before it, a loop end
  joins them, the loop check ignores the overlap, a loop exit ignores frees on its way, a deferred body's frees
  are not replayed, its moves are kept where it is declared. Two stale anchors were retargeted (`loop moves
  unchecked`, `aliases lose non-deep references too`). A mutant that dropped a loop exit's join of the back-edge
  bindings was removed: the exit already joins them after the loop, so it is equivalent.
- `tests/mutate_port.py`: 49 mutants, all caught. Anchors retargeted to the new text: the back-edge check, the
  handle hit test (`hitBy`), the self-call counter, the break and continue recording (`exits`, `conts`), the
  defer's restore of suspicion (`restoreDeferred`), the defer's re-point, the stale `callee writes keep deep
  refs` anchor (the `invalidateDeep` call in `afterCall`), and the stale `break suspicion` and `continue`
  anchors. A mutant on the replay of a deferred self-call was not added:
  the port source has no deferred self-calls, so stage B does not see it.
- `tests/run_fork.py`: 274 passed, 0 failed. `tests/port_test.py`: 403 of 403 identical, no leaks.
  `tests/port_compare.py`: stage B 425 of 425 identical; the memory check passes (see Memory).
  `tests/own_fuzz.py --count 600`: 0 failures in 1200 programs. `tests/doc_check.py`: 22 examples, 0 problems.

## Defer uses (done)

**The holes.** A defer body is compiled at its declaration but runs at each exit of its block. Its checks
used the bindings and validity the references had at the declaration, so two kinds of program compiled
and read or freed the wrong memory: a defer that reads a reference which is re-pointed, or whose target is
changed or freed, after the defer statement (`defer println(first.v)`, then `bag.items = new ...`;
`defer println(k.v)`, then `k = &b; b = new ...`), and a defer that writes or frees through a reference
re-pointed after the declaration (`defer { k.items = new []Item(0) }`, then `k = &b`: the replay
invalidated `a`, the binding at the declaration, though `k` named `b` at the exit; also `k.items[0] = ...`
and a mut call `bumpb(k)`).

**The rule, as implemented: record the uses, check them at each exit.** `LANGUAGE.md` section 4.
- Every name occurrence of an outer reference local inside a defer body is recorded (`noteDeferUse`, called
  from `nameItem`; `deferUses`, one flag per `ownedLocals` index below the body's `deferMark`). Reads,
  writes, mut arguments and calls are all name occurrences, so one hook covers them. `deferStat` stores,
  per defer, the references used and their bindings at the declaration (`Block.uses`, `useBinds`,
  `useFrom`; `before.binds`). A defer inside a defer body passes its uses of outer locals outwards, so the
  enclosing defer checks them too.
- `callDefers` runs from each exit (normal end in `closeBlock`; `break`, `continue`, `return` through
  `runDefers`). For each defer, newest first, `checkDeferUses` refuses: a reference whose binding is not the
  targets within those at the declaration (`withinDeclared`: every root and place of the exit binding is
  covered by the declaration binding, and a reference writable then is still; the parent is not part of the
  target, so a reference derived from one that is re-pointed is still accepted). Narrowing is sound: the
  body was checked against the wider binding, and the frees replayed from it were made for every root of
  it. A reference that was joined from two places and is pointed at one again before the exit is accepted;
  one that was bound to one place and is joined with another at the exit (a re-point on one path) is not.
  The writable test cannot be reached by a program today (a binding of one root is writable or not for
  all its paths), so no mutant covers it: "cannot point 'k' elsewhere while a defer uses it"; a reference that is invalidated
  (`moved`): "'k' is used by a defer but may point to freed memory at the block's end"; and a suspect one
  (a recursive call that writes through a handle; reported at the end of the body like the other suspect
  errors, `notePending`). Then the defer's own frees are replayed (`replayFrees(bi, d)`, now per defer;
  `Block.freeFrom`), before the next older defer is checked, so a newer defer that frees what an older one
  reads is refused too.
- Why it is sound: a defer body was checked against a state in which each outer reference it uses had the
  recorded binding and was valid. The check at the exit shows the state it runs in has the same binding
  (same targets) and is valid, so the body's checks, and the frees replayed from it, hold at the exit.
  Nothing else in the body depends on the outer state: owned locals it reads cannot be moved after the
  defer (`deferUse`), and the owned places under them are read or written at run time, where a bounds or
  nil check applies. The error is reported at the exit, not at the change.
- A cleaner design (checking the body against the exit state) would need the body compiled once per exit,
  which a single-pass compiler with no AST does not have. The strictness is accepted: a block that points
  a reference elsewhere after a defer that uses it is refused, even where the defer would be harmless.
- Not changed: argument evaluation order (still open then; closed by "Argument order (done)" below).

**Tests.** `tests/errors/defer_uses.obx`: 22 cases, one for each probe (the re-point with a free, the
re-point alone, the free after the defer, a free by a call, a write, an indexed write, a mut call), the
exits (break, continue, return with and without a value, for a free and for a re-point), an inner block,
a re-point on one path, two defers (a newer one frees what an older one reads, and a reference re-pointed
under two defers), a defer inside a defer, and a recursive call. `tests/run/defer_uses.obx`: fifteen
accepted programs: `defer close(f)` and `defer println(x.v)` with the reference untouched, a change or
re-point of another reference, a re-point before the defer or after the block, a re-point to the same
place, a parent re-pointed, a defer that frees something else, a defer that reads and then frees, a loop
with break and continue, a reference made in the loop body, a return from a loop, nested blocks with
two defers, and a reference declared inside the body. `tests/own_fuzz.py`: `defer_ref_stmt` (valid) and
`defer_repoint_stmt` (invalid). The `review15/p/FP_defer_via_child_*` programs and step 2's
`run/ref_bindings.obx` cases still pass.

**Mutants** (`DEFER_USE_MUTANTS` in `tests/mutate_fork.py`, also run by `tests/mutate_port.py`): the
re-point check disabled; an exit binding allowed beyond the declared targets;
the invalidation check disabled; the suspect report disabled; no check at the
normal end, at a break or continue, at a return without a value, at a return with a value; the uses not
recorded; the check not limited to the defer's own uses (it would include a newer defer's, which its own
frees may have invalidated: `twoDefers` in the run test); an inner defer's uses not passed outwards, or
passed without the guard for the body's own locals (`innerOwn`); a newer defer's frees applied after the older one's
check. One existing mutant was retargeted (`replayFrees(bi, i)`). The "not checked at the block's end" mutant
keeps that exit's replay and skips only the check. A mutant that starts a defer's check at the first use
(instead of its own `useFrom`) is equivalent: the extra uses are older defers', and the state only gains
invalidations between the two checks, so what it refuses the older defer's own check refuses too.

**Strictness note.** Besides the re-point after a used defer, a function that returns a value from which
the defer's reference is derived is refused: `defer ... ; return bag` where the defer uses `k = &bag`
(the return moves `bag` and invalidates `k` before the defer runs). This matches main's refusal of
`defer ... bag.items ...; return bag` (the owned local a defer reads cannot be moved).

## Invalid on one path: reported at the use (done)

**The false positive.** A reference invalidated on one path only was an error at the join.
`let p = &bag.items[0]; if c { bag.items = new []Item(0) }`, with `p` never used again, was refused at the
`if`: "'p' is invalidated on one path but not another; point it again on every path or none". The same held for
an `if` or `match` whose join was followed by a re-point of `p`, and for a loop that invalidated a reference on
one pass and never read it, which was refused at the loop's end ("'p' is invalidated inside the loop"). The
three probes `if c { ... }` with and without `else`, and a re-point after the join, all failed on HEAD.

**The rule.** The `moved` flag of a reference local is its invalidation, and it is now a may-flag: where paths
join it is the union of their flags, and a use refuses it, as a reference invalid on every path always was.
- `joinSuspect`, `joinStates` and `absorb` (the joins of an `if`, a `match`, the states a loop is left in, and
  its back edge) take the union for reference locals. `stateDiff` no longer reports a difference in a reference
  local, so `sameStates`, the loop's end, and the `break` and `continue` checks now concern owned locals only,
  where "moved on one path but not another" and "moved inside the loop" are unchanged.
- A use is refused by the checks that already existed: `nameItem` (a read, a pass to a call, a re-borrow, and a
  write through the reference, which `designatorT` checks through `movedRef`), and `checkDeferUses` at the block's
  end (a defer that uses it). A re-point clears the flag, as before.
- Loops. The body is compiled once, from the state the loop is entered in, so an invalidation on the way round
  (the body's end, or a `continue`) is not seen at the top by the body's checks. `loopCarryCheck` reports the
  case: where the back edge leaves a reference invalid, and the body reads it before it is pointed afresh, the
  read is the error. The position is the last such read (`Sym.staleLine`, `staleCol`, recorded by `designatorT`),
  and the message is the use's. The loop's exits carry the invalidation after it, in `loopBodyV`: a while
  condition, or a for loop's test, is reached from the top with the back edge's state, and a `break` on a later
  pass sees it too (the states a loop is left in are computed from its entry state, so the back edge is added).
- The message at a use is the one an all-path invalidation always had, now `freedRefMsg`. A write through an
  invalid reference now gets it too: it said "'p' was moved and has no value here", the owned-value message.

**Why nothing accepted before is refused now.** Before this change every join where the flag differed was an
error, and so was every back edge that invalidated a reference. So a program that compiled had the same flag on
every path to every join and no invalidation on the back edge, and the union is that same flag. The change only
accepts more programs: those where an invalidation on some path reaches no use. A use of a reference invalid on
some path is refused, as a use of one invalid on every path already was.

**Tests.**
- `tests/run/one_path.obx`: seven shapes, all accepted and run (`ifElse`: a change on the then path of an
  if/else; `elseOnly`: on the else path; `ifOnly`: an if without else; `loopBreak`: a loop that breaks after the
  change; `loopNoRead`: a loop that invalidates the reference on its first pass and never reads it; `repoint`: a
  re-point after the join, then a read; `repointOther`: the else path re-points, the then path invalidates).
- `tests/errors/one_path_uses.obx`: eleven cases. Each has a use after a shape: a read after an if/else, a write
  after an if without else, a pass to a call after an else-only change, a read after a `match` arm, a defer at the
  block's end (refused at the `}`: "is used by a defer"), a read after a loop left by `break`, a read at the top of
  a loop, a read in a `while` condition, a read after a `continue` on an earlier pass, a read after a `while` loop
  left through its condition, and a re-borrow (`let q = &p.kids`).
- Existing error tests whose expected line or message changed. Each marker is now on the line that reports, and
  each change is justified:
  1. `errors/ref_bindings.obx`, "a continue after a call that frees what the reference points into on an earlier
     pass". The error was at the `while` line ("invalidated inside the loop"). The loop is valid by itself, and
     the invalid `k` is read after it (`println(k.v)`, on the path the `continue` leaves), so the marker moved to
     that read.
  2. `errors/ref_bindings.obx`, "a deferred free in a loop body of what the reference points into, read at the top
     of the next pass". Refused at the top read, `println(k.v)` in the body, which is the read the case names. Was the
     `while` line.
  3. `errors/ref_bindings.obx`, "a recursive call that frees through a handle on its innermost pass, and a deep
     reference read on the next pass". The error was the join at `if d == 0 { h.kids = ... }`, which has no use of
     `k` after it. Now the error is the existing self-call check of this loop, "a recursive call in this loop writes
     through a handle", at the `while` line; it was masked by the join error. The same loop without the handle write
     compiles, so that check is the one that applies. The marker moved to the `while`.
  4. `errors/references.obx`, "reference invalidated in a loop" and 5. "reference invalidated on one branch". Neither
     reads `n` after the change, so both compile now. A use, `println(n != nil)`, was added after each, and the
     marker is on it: the cases test the refusal, which is now at a use.
  6. `errors/references.obx`, "a reference invalidated in a loop, then continued past". The marker moved from the
     `continue` to `println(k.v)` on the next pass, where the invalid `k` is read.
  No other error test changed. The owned-value messages in `errors/ownership.obx` are unchanged.
- Mutants. `tests/mutate_fork.py`: `ONE_PATH_MUTANTS`, twelve new, each caught by a named case: a join that drops
  an invalidation (if, and match), the back edge that drops it, an owned-style difference at a join, the loop top
  that does not check it or checks a reference that is not read, the loop top that checks a for-each variable that
  its own pass points afresh (the `s != lv` exclusion), the wrong position of the report, the loop's exit
  that drops it, a read that is not refused, a write that gets the owned message, and a re-point that keeps the flag.
  The for-each one is caught by `run/one_path.obx`, `forEachRebind`: a change to the item a for-each variable pointed
  into on the first pass, and a read of it on the second pass, accepted with the sum 10. `tests/mutate_port.py`
  imports them (as it does the defer mutants). The stale "branch states not compared"
  mutant, whose anchor was the `isRefLocal` branch that was removed, is retargeted to the same check.
  `mutate_fork`: 150 of 150 non-equivalent caught, with 8 equivalent (main's `EQUIVALENT` list), 158 in all.
  `mutate_port`: 140 of 140 caught, after the merge with main's origin and held-place mutants.
- Equivalent, not planted. The statement `if cs.flags[i] && !condSt.flags[i] { ownedLocals[i].moved = true }` after a
  while loop's condition exit (in `whileStat`) can be removed with no test failing (`run_fork` 361 of 361 with
  `OBO` pointed at a build without it). It is redundant: `applyExit` sets flags only for reference locals, so for
  any other local `cs.flags[i]` equals `condSt.flags[i]` and the test is false; for a reference local the test is
  true only where `cs.flags[i]` is, and `joinSuspect(&cs)`, which runs next with nothing in between that touches
  `moved` (`loopdepth`, `loopMarksJoin`, `loopLogEnd`, the narrowing truncation), sets `moved` for every reference
  local with that flag. The statement is kept: it states the rule where the condition's state is made, and removing it
  would rest the rule on `joinSuspect` alone.
- Memory. `PEAK_BUDGET` is unchanged at 1,351,000. The port's live peak was 1,192,816 bytes at the one-path commit,
  against 1,183,728 before this change (+9,088): the two `Sym` fields, `freedRefMsg`, `loopCarryCheck`, and the checks.
  After the merge with main's held places and origins it is 1,257,088 bytes (main's additions, not this change's).

**Open.**
- The self-call check of a loop (`loopSelfCheck`) still reports at the loop, not at the read it is about. It is the
  existing rule, its message says "in this loop", and it was not part of the join.
- The check is "may": a use reached by an invalidation on any path is refused, whatever the path's condition. This is
  the same as for a reference invalid on every path, and is not value-sensitive.
- In nested loops the carried check reports the last read that was not pointed afresh, which may be in the inner loop.

**Language.** `LANGUAGE.md` section 4 has the rule, with an example that runs and one that is refused at the read.
Section 8 drops the two messages that a join or a loop's end no longer raise for a reference ("is invalidated inside
the loop", "is invalidated on one path but not another"), and the use message now lists the cases it covers.

**Measured** (on the merge of main at 03296b6): `run_fork` 410 passed, 0 failed. `port_test` 422 of 422 identical, no leaks. `port_compare` Stage B 561 of 561 identical, the port's peak 1,257,088 bytes against the budget 1,351,000. `own_fuzz --count 300 --profile both`: 0 failures in 600 programs. `doc_check`: 27 examples, 0 problems. `bench/bench.py` runs all four workloads (its obx side needs `obx/obxc`, built from the worktree's `obx`). `make promote`: stage2 equals stage3; the seed was then restored to the committed one, so the merge commit does not change the seed.

## Argument order (done)

**The hole.** A call's arguments were checked as if no argument could invalidate another. They run left to
right, so a later argument could move, free or change what an earlier reference argument points into before
the callee ran. `two(&xs[0], eatArr(xs))` printed a poisoned word (`123402283122688`), and
`use2(first, mk(h))`, with `first = &bag.items[0]` and `mk` replacing `h.items`, printed 1 for 42 (with
`OBO_CHECK=1`, which poisons freed memory; valgrind is not installed here). Both, and the 25 shapes in
`tests/errors/arg_order.obx`, compiled on main (03296b6). Run, most print a poisoned or wrong value or exit
3 on a freed header; the rest pass a dangling reference to a callee that happens not to read through it.

**The rule, as implemented** (`holdArg`, `argOrderCheck`, `releaseArgs` before `callFn` in `src/obo.obo`).
A reference argument (to a `&` or `mut` parameter) is held while the later arguments run, as a live `let`
reference to the same place would be. The holder is a reference local of its own, with the argument's
binding (`borrowBind`), pushed on `ownedLocals`. So every existing invalidation reaches it with the same
precision: a move (`consume`), `replace`, `take`, a mut argument, a callee's free of a global (`afterCall`),
a change through a handle (`invalidateDeep`), and a call that might free when the argument borrows through a
handle. A disjoint place is told apart by the binding's path,
as for a `let`. After each later argument (and after its move, when it is passed by value) the holders are
checked: an invalidated one is refused at that argument ("argument 3 of f may free what argument 1 points
into, and runs after it; compute it before the call"). A suspect one (a recursive call in a function that
writes through a handle) is noted and reported at the end of the body, as the other suspect uses are. The
holders are dropped when the last argument has run; the callee's own effects are checked by the existing
call rules. `writeFile`'s bytes are held the same way while its flag runs: `writeFile(p, b.data, zap(b))`
wrote 0 bytes from a freed header on main.

The held-place list (`heldPush`) was not used: it keys a place by owner and identity for the index,
`append` and assignment checks, and is hit only by moves and frees. A reference argument is also lost to a
callee's frees of a global, to a write through a handle and to a mut argument, which the reference
bindings already track. So the holder is a reference local, and it reuses them.

**Pool.** The holder symbols are reused (`argTemps`, `argTempsUsed`), so a call costs no new symbol after
the first at its depth. The ones made inside a function are dropped with its symbols at its end
(`truncate(argTemps, tempMark)`). A holder is never named, and it is not in any loop's states (a loop's
states cover only the locals it began with), so its freshness and loop marks are not read. An invalidated
holder stops the compile, so only its suspicion is reset when it is reused. A holder made at the top level
(a global's initializer) is kept for the whole compile, which `run/arg_order.obx` `holderReuse` covers.

**Not held:** an argument passed by value (an int, a copy of a handle, a moved owned value), and an inner
call's arguments once that call has returned (`add(two(&xs[0], 0), eatArr(xs))`). A handle passed by value
whose target a later argument frees is checked where it is used (exit 7), as any handle is.

**Accepted** (`tests/run/arg_order.obx`, 21 lines of output, also accepted by the seed): a later argument
that reads the array or an element, takes a sibling field, moves another local, or changes a scalar through
a mut argument or a handle; an int read from the place a later argument moves; an inner call finished before
the move; a whole object passed and a later argument that frees below it through a handle; two references
into one array; a let reference read by a later argument; a handle passed by value; and `writeFile` with a
flag that reads the bytes.

**Refused** (`tests/errors/arg_order.obx`, 25 cases, all compiled on main): an element, a field's element,
a field's array, a whole local and a whole local array; the array passed by value to the same call (`keep(&xs[0], xs)`,
and the `let` form, both with a callee that drops the array first); a reference through a handle and through a
handle temporary with the owner moved; `replace` and `take` in a later argument; the freeing call nested
in a later argument, its result passed on, and a reference held by an inner call; the third argument
freeing the first, the second held and the third freeing it, two held and the first freed; a later
reference argument whose index replaces the array; a mut argument; a reference parameter's element with a
handle writer; a global's element with a call that frees the global; a reference through a handle with a call
that might free; a recursive call in an argument; and `writeFile`.

**Known false positives** (found in review, about 90 probes; each is refused the same way the `let` form is,
since a holder has a `let` reference's precision: a `mut` argument invalidates the deep references into its
root whatever the callee does with it):
- `two(&xs[0], upd(xs, 1))`, where `upd(mut a: &[]Node, k: int)` only writes an element's scalar;
- `rd(h.xs, grow(h.xs))` and `two(&h[0], tr(h))`, where the callee only appends to the `mut` array (an append
  frees nothing);
- `two(&b.items[0], bumpN(b))`, where `bumpN(mut b: &Bag)` only changes the scalar `b.n`.

A whole-object argument beside such a call is accepted (`twoBag(b, bumpN(b))`, run test `mutScalar`). A
narrower rule would need a summary of what a callee changes through each `mut` parameter, which the
compiler does not keep.

**Mutants** (`ARG_ORDER_MUTANTS` in `tests/mutate_fork.py`, imported by `tests/mutate_port.py`): 20, all
caught, none equivalent: no hold, no check after a reference or a by-value argument, the pool never
grows, a holder that is not a reference local, keeps its old binding, suspicion or index, a freed or
suspect holder not reported, the check reaching enclosing calls' holders, only the newest holder
refused, holders not reused, left in `ownedLocals`, not released after the arguments, kept past their
function, and the four `writeFile` mutants (bytes not held, held under another argument's index, flag not
checked, holder not released). The index mutant is caught by the `writeFile` error case, whose message
names argument 2.

**Memory.** The port compiling itself peaks at 1,251,616 bytes live (budget 1,351,000, not raised), 5,040
more than the 1,246,576 before.

**Measured.** `run_fork` 424 of 424, `port_test` 422 of 422 identical with no leaks, `port_compare` stage B
575 of 575, `own_fuzz --count 300 --profile both` 0 failures in 600 programs, `doc_check` 27 examples with no
problems, `mutate_fork` 158 of 166 caught with the 8 equivalents already listed, `mutate_port` 148 of 148,
`bench/bench.py` run for all four workloads, `make promote` a fixed point (the seed is not committed).

**Merge with `main` (07af3c9, the one-path may-flag).** Only `FORK.md` conflicted; both sections are kept.
A reference local invalidated on one path is now invalid after the join, and is reported only where it is
used (`joinSuspect`, `joinStates`, `absorb`, `loopCarryCheck`, the exit join in `loopBodyV`). Holders do not
take part. A holder lives only between a call's first argument and its last; it is released before the
statement goes on, and no snapshot, join or loop state is taken inside an argument list. The joins work over
the locals a state covers. `releaseArgs` truncates `ownedLocals` to the length before the call's arguments,
which the merged code does not change. Two pins changed with the merge. Two mutants were caught through the
message "'an argument' is invalidated on one path but not another", which main removed: holders left live
after the call, and `writeFile`'s holder not released. The first is still caught (a holder freed with its
function is left in `ownedLocals`). The second was missed after the merge. `writeRead` now comes first in
`run/arg_order.obx`, so the functions after it take holders from the pool it used, and the mutant's stale
count is an index out of range. `reuseAfterChange` was added: a holder reused by a second call after a
change between the two. Measured after the merge: `run_fork` 436 of 436, `port_test` 424 of 424 identical
with no leaks, `port_compare` stage B 587 of 587, `own_fuzz --count 300 --profile both` 0 failures in 600
programs, `doc_check` 29 examples with no problems, `mutate_fork` 170 of 178 caught with the 8 equivalents
already listed, `mutate_port` 160 of 160, `bench/bench.py` run for all four workloads, `make promote` a fixed
point (the seed was restored to main's). The port peaks at 1,273,232 bytes live (budget 1,351,000, not
raised). Main's source alone measures 1,257,104 in the same way, so the merged rule costs 16,128 bytes,
more than the 5,040 it cost on 03296b6. The cause was not separated out: the two changes' costs do not add
up, as the image-buffer growth in "Reference bindings per path" did not.

**Not covered.** No existing test expectation changed, and the port needed no rewrite. Methods did not
exist yet at this point (section 12 of `LANGUAGE.md`); the methods record at the end of this file
supersedes this sentence. The fuzzer has an argument-order kind (see "Handle-owner and reference false positives").

## Assignment over a handle's owner (done)

**The gap.** An assignment over an owned local frees its old object, and a `let` handle made from that
local names the old object. The assignment cleared the handle's origin (`originMove`) but was not a free
event for it, so `let h = handle(a); a = nb(); h.items[0]` compiled and stopped at run time on the
generation check (exit 7). Safe, but a free the compiler sees, missed. On main (37bdfeb) these all
compiled and exited 7: the plain form; the assignment in an `if`, in a `match` arm and in a loop; an
optional owner assigned a value or `nil`; `a = take(o)` over an optional owner; a by-value parameter
assigned; and `drop(a)`, which frees the same way. A compound assignment applies only to ints, so it
frees nothing. A whole `mut` parameter cannot be assigned (it is a reference).

**The rule** (`originFree`, `deadHandleCheck`, `armBlock` in `src/obo.obo`). An assignment over a whole
owned local `a` (the `originMove(xs, false)` hook) and `drop(a)` mark every `let` handle whose origin is
`a` as dead (`originDead`, one entry per `originSyms` entry, truncated with it at a block's end). A use
through a dead handle, the first deref in a designator (`h.f`, `h.f = v`, `h.items[i]`), is refused:
"handle 'h' may name a freed object: 'a' was assigned or dropped after the handle was made from it; make
the handle again". `alive(h)`, a copy of `h` and passing `h` are not uses, so a handle that is not used
again is not refused. An assignment over a local whose value was moved away frees nothing; the move has
already cleared the origin, so no handle matches.

The refusal is at the use, not at the assignment, because that has the fewer false positives: refusing
every assignment while a handle from `a` is in scope would refuse `c_asg_clears_origin` in
`run/held_places.obx`, which uses the handle only before the assignment.

- **Paths.** The dead marks are a may-flag, as for references ("Invalid on one path"). An `if` and both
  kinds of `match` save them (`deadSave`), start each arm from the saved marks (`armBlock`), and leave
  the union of the arms that go on (`deadSet`). An arm that ends does not go on to what follows the
  statement, so its marks are not in the union. If it ends in a `break` or a `continue` (`jumpSeq` changed
  during the arm), its marks are kept in `Loop.exitDead` and applied to the marks after the loop; if it
  returns, they are dropped. A `defer` body runs where its block is left, after the uses that follow it,
  so its marks are taken back where it is declared.
- **Loops.** The body is compiled once, so a use of the handle that comes earlier in the text than the
  free runs after it on the next iteration, and the marks cannot see it. The first version refused the free
  only when the loop had named the handle earlier (`usedAt` against `Loop.useFrom`); the reviewer found
  that unsound (an `if` that frees in one arm and uses in the other, hole 2). Now an assignment or drop of
  the owner inside a loop that a `let` handle from it was declared outside of is recorded (`pendSym`,
  `pendDepth`, its place), and `pendingFreeCheck` reports it where the outermost such loop ends
  (`loops[hDepth]`), if the handle was named anywhere in the loop (`usedAt` after `Loop.useFrom`): "cannot
  assign over the owner of handle 'h' inside a loop that uses the handle". A `drop` in a loop is refused
  earlier, as a move. `usedAt` is bumped for every tracked handle (`Sym.hDepth` > 0: set, to the loop depth plus 1, when it is declared with an origin), not only while it still has an origin: a move of the owner clears the origin, and the
  first version then stopped counting the uses.
- **f2 is a known false positive again.** The first version limited the move rule (`loopOriginCheck`) to
  loops that had named the handle before the move. That is unsound: `take(a); a = nb(); pr(h.n)` in a loop
  clears the origin at the move, so the use is never checked, and the next iteration uses a freed object
  (exit 7). The base rule is restored exactly: a move of the owner in a loop a handle from it was declared
  outside of is refused even if the loop never names the handle (the old f2).
- **Defers.** A defer body runs at the block's end, after any later free of the owner, so the marks at the
  `defer` statement say nothing about it. The handles a defer body names (declared before it; `usedAt` after
  the body began) are kept in `deferH` with the block they belong to (`deferAt`, dropped at `closeBlock`);
  `originFree` refuses a free of the owner of any of them: "cannot assign over 'a' while a defer declared
  earlier uses handle 'h' made from it" (and "cannot drop"). A free before the defer is declared is the
  dead mark's job (the body takes the marks from the declaration).
- **After a break.** `for ... { if c { a = nb(); break } }  pr(h.n)` is refused, and correctly: the use
  after the loop is reached through the break, so the handle may be dead there. What was a real false
  positive was the fall-through code in the loop: the break arm's marks used to flow into the code after
  the `if`. `for ... { let h = handle(a); if c { a = nb(); break }; pr(h.n) }` is accepted now (`breakArm`,
  `continueArm`). With the handle declared outside the loop, the use in the loop is still refused (the
  free-and-use rule above), a conservative choice.

- **Round 2: the assignment no longer clears the origin.** An assignment cleared `hOrigin` (`originMove`),
  but the dead marks of the path it was on could be discarded later (a return arm's, a defer body's, a
  break arm's, which go to the state after the loop), and the later real free then found no origin: `if k == 2
  { a = nb(); return }  a = nb()  pr(h.n)` and `defer { a = nb() }  a = nb()  pr(h.n)` compiled and exited 7.
  Now an assignment (`originMove(.., false)`) only calls `originFree` and leaves the origins alone: the
  dead mark is the only record of it, and a path-dependent mark can no longer lose information. The
  origin is cleared by a move only (which was not path-aware then; it is now, see "Origins per path (done)"). Two consequences. (1) The loop move rule
  (`loopOriginCheck`) skips a handle that is dead (`originDead`): it may not be used in the loop at all, since
  every use is refused, so the rule has nothing to protect (`c_asg_clears_origin` in `run/held_places.obx`).
  (2) A `drop` of the owner in a loop is now judged by the free rule (refused only if the loop uses the
  handle), not by the move rule, so a loop that drops the owner and never names the handle is accepted.

- **Round 3: defers in nested blocks and loop bodies.** `deadSet(&deadBefore)` takes a defer body's marks
  back where it is declared, and the first fix re-applied them nowhere, so a defer in an `if`, a bare block or
  a loop body freed the owner unseen. Now `originFree` records the owners a defer body assigns over or drops
  (`deferFrees`, kept per defer as `dfBlock`, `dfIndex`, `dfOwner`, dropped at `closeBlock`), and `callDefers`
  replays them with `originFree` at each place the block's defers run (the end of the block, a `return`, a
  `break`, a `continue`). The replay marks the handles dead on that path (so a break arm's marks reach
  `Loop.exitDead` with the defer's), and in a loop records the pending free, so a defer in a loop body is
  judged by the loop rule: it runs on every iteration. The replay skips the check against the defers declared
  earlier (`replayFree`): it was made when the body was compiled. The loop error is reported at the end of the
  body (the `}`), where the defer runs.
- **Round 3: a `let` copy of a dead handle** takes the mark of the handle it copies (`varDecl`). Known false
  positives kept: `while true { ...; break }` shapes where the loop is left on every path (f1), a use that
  cannot be reached (f7), and `a = nb(); let g = h` in a loop (g2), where the copy counts as a naming of `h`
  in the loop (`usedAt`) although a copy is not a use.

**Not covered** (each still traps at run time, exit 7; none is a memory hole):
- (done, see "Field and element origins (done)") a handle made from a field or element of a local owner. Still
  untracked: one made through a reference parameter, a reference local, another handle, or from a global; a
  write through a handle without an origin is not seen;
- a `var` handle, a handle in a record, and a `let` copy made after the assignment from a handle that was not made from the owner (it gets no origin). A `let` copy of a dead handle inherits the mark (round 3);
- a move of the owner, then a use of the handle, where no loop rule applies: `let h = handle(a); take(a);
  pr(h.n)` (the callee frees its argument), also with `a = nb()` after the move, in a loop with `let h`
  inside it (reviewer probes c3 and c8). The move clears the handle's origin, so it falls back to the
  run-time check (exit 7); this is the documented behavior of a handle without an origin, the same on 37bdfeb,
  and not a regression. Marking handles dead at every move would refuse `var c = a; h.n`, which is safe;
- false positives kept: `if alive(h) { pr(h.n) }` after the assignment is refused (the use is guarded, the
  compiler does not narrow on `alive`); a handle used in a loop that frees its owner is refused wherever
  the use is, even on a path that cannot follow the free (a use before a `break` arm that frees);
- `alive(h)` in a loop counts as a naming, so `while ... { alive(h); a = nb() }` is refused at the
  assignment although `alive` does not use the object.

**Tests.** `tests/errors/handle_frees.obx`, 44 cases (6 more in round 3: a defer in an `if` block, a bare block, a loop body that uses the handle, a loop body with the handle used after, and a `let` copy of a dead handle, also in a loop; 5 more in round 2: a return arm, in a loop, a defer body, an else arm, a break arm, each followed by a real free) (the 22 of the first version, with the loop messages changed, and 11 for the review: a move, then an assignment, in a `for`, in a `while` and with the handle used after the loop; an assignment in one arm with the use in the other, in a `for`, a `while`, a `match`, and an inner loop; a drop in an arm; a defer that uses the handle, then an assignment, a drop, and an assignment in an inner block): the plain form and the doc's runtime-trap form, an
`if` without else, the then, else and else-if arms, the first and else arms of a closed `match` and of an
int `match`, a loop that uses the handle before the assignment (body, `while` condition, outer loop of a
nested one), a loop that may run before a later use, a break path, an optional owner assigned `nil` and a
`take`, a by-value parameter, `drop` (plain and in a loop), a `let` copy of the handle, and a write
through the handle. `tests/run/handle_frees.obx`, 16 shapes with output: no handle, a handle made
after, a temporary handle, writes through the handle and to a scalar field of the owner, a handle unused
after, a handle in a closed block, `alive` after, the owner moved first, another owner's handle, a use
in the else arm, a returning arm, a use in another match arm, a `defer` that assigns, a fresh handle
after a closed block's dead one, an assignment in a loop with the handle used only before it, a free in a break arm and a continue arm with the handle made inside the loop, a free in a loop that never names the handle, a returning arm, and defers that use another owner's handle, no handle, or are in a closed block.
`held_places.obx` (run), `call_targets.obx`, `one_path.obx` and `arg_order.obx` are unchanged and pass.

Existing expectations that changed, each still refused or still trapping:
1. `run/trap_dangling.obx` ran the exact shape now refused, to show the exit-7 trap. It now keeps the
   handle in a record (`l.to.v`), which the compiler does not track, so it still shows the trap; the old
   program is the second case of `errors/handle_frees.obx`. The `LANGUAGE.md` example in section 5
   changed the same way, and a refused example was added.
2. (The f2 changes to `errors/held_places.obx` and `run/handle_frees.obx` were reverted with f2.)
4. `errors/held_places.obx`, "a take of an optional owner after its handle was made": the `o = nil`
   that widens the owner for the `take` frees the handle's object, so the index through `h` is refused
   by the new rule at the same line, with the new message. This is the case the `EQUIVALENT` note on the
   take hook describes: an assignment always comes first.

**Mutants** (`HANDLE_FREE_MUTANTS` in `tests/mutate_fork.py`, imported by `tests/mutate_port.py`): 42,
all caught, none equivalent: the assignment and drop hooks, the dead mark, the loop refusal (dropped,
without the use test, checked at the innermost loop), the use check, a mark that refuses every handle, the
naming count (also once a move clears the origin), the loop's start count, the mark list's append and truncation, the arm's
start, its join, its return and break handling (a break arm's marks kept for after the loop, counted, applied, not applied to a return), the defer handles (recorded, all, other owners', kept after the block), the then, else and else-if arms, the two `if` joins,
the arms and else arms of both matches and their joins, and the defer's restore.

**Memory.** `PEAK_BUDGET` is now 2,000,000 (raised by the user, not by this change). After the merge of main (d5ba3a5) the port compiling itself peaks at 981,984 bytes live. The Phase 2 retry in `fnDecl` needs no change to its restore list: the first pass runs to the end of the body, so `originSyms`, `originDead`, `deferH`, the `df*` lists and the pending frees are empty again.

**Measured** (on 37bdfeb plus this change): `run_fork` 460 of 460, `port_test` 426 of 426 identical with
no leaks, `port_compare` stage B 611 of 611, `own_fuzz --count 300 --profile both` 0 failures in 600
programs, `doc_check` 30 examples with no problems, `mutate_fork` 198 of 206 caught with the 8
equivalents already listed (all 28 new ones caught), `bench/bench.py` run for all four workloads,
`make promote` a fixed point (the seed is not committed). `mutate_port` was stopped by the host for low
memory after 102 of its entries, all caught; the new group, imported last, was not reached in that run.

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

Status: Phases 0, 1 (loop inversion included) and 2 done; Phase 3 measured and stopped, the peephole pass built together with loop-head alignment (see below). Phases are ordered by expected value per unit of cost.
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
below is handled (done: Stage B compares behaviour, see open decisions).

**Done** (patch `tools/port_cheap_codegen.py`; loop inversion is described under "Loop inversion (done)"):

- **Static stack depth** was already tracked: `depth` counts every push, pad and
  return address, and `rtCall` and `callFn` pad only when the depth is misaligned. The pads
  that remain (2,685 in the port's own code) are needed: they come from the registers
  pushed around runtime calls, the arguments among them, and from odd argument counts.
  Fewer would need a different call sequence, for example not pushing rbx and r12 to r15
  around runtime calls (the C runtime preserves them, and obo has no collector to scan
  them); that is a separate decision.
- **No `sub rsp,0x0`.** The frame size is known only after the body, so for a function
  without locals the 7 bytes are cut out then and the body moves back (`dropFrame`). Jumps
  within the body are relative and move with it; calls out of it, to the runtime or to
  functions already compiled, are recorded in `farCalls` and adjusted, and calls waiting for
  a later function are moved in its `fixups`. Nothing else holds a position in a finished
  body.
- **`push imm`** for a constant argument of an obo call (`6A ib` or `68 id`).
- **Constants straight to argument registers** for runtime calls: `mov edi,imm` instead
  of loading a pool register, pushing it around the call, copying it and popping it. With
  one push fewer, the pad usually goes as well: allocating a record went from 7
  instructions to 2.

Measured with the four Phase 0 workloads, each built by a compiler at each step and the
runs interleaved (31 per build; median, range in brackets). The loop's executable is the
same at every step, so its column shows the noise, about 1%; alloc's range is wide on
WSL. The last row counts the instructions of the port's own source (as at 59c7701)
compiled by each step.

| Benchmark | Baseline (seed) | + push imm | + constant runtime arguments | + no `sub rsp,0x0` |
|---|---|---|---|---|
| loop | 564 ms (554-635), 36 instr | 566 (551-609), 36 | 567 (553-618), 36 | 566 (557-705), 36 |
| fib | 613 ms (602-655), 57 instr | 613 (596-651), 56 | 614 (602-650), 56 | 602 (590-655), 54 |
| array | 403 ms (391-462), 105 instr | 402 (389-454), 105 | 405 (393-446), 99 | 402 (393-477), 99 |
| alloc | 473 ms (426-907), 56 instr | 480 (432-918), 56 | 482 (447-1530), 51 | 474 (459-528), 51 |
| port's code | 44,983 instr | 44,130 | 40,723 | 40,638 |

The port's code is 9.7% fewer instructions (258,784 to 246,496 bytes of executable), and
it compiles itself in 29 to 30 ms against 30 to 31 ms, within noise. fib, where every call
ran a `sub rsp,0x0`, is 2% faster; the other times did not change. No step regressed.

Memory: compiling itself, the port allocates 12,057,056 bytes in total (through `tests/port_compare.py`), down from 12,266,272
(fewer loads mean fewer Items; the constants are kept in one array, `argConsts`, since an
array per runtime call added 315 KB). Its live peak rose from 1,045,392 to 1,079,424 bytes,
within the budget of 1,351,000, because the peak moved: with less code to hold, the image
phase at the end is no longer the highest point. The highest is now the moment the code
buffer grows from 128 KB to 256 KB and both are live, partway through the source (measured
with a runtime that records the block allocated at the peak: 262,176 bytes, against 544 at
the end before).

Mutants (`tests/mutate_port.py`), all caught: the pad dropped at a misaligned runtime call
and at a misaligned obo call, and a `push imm` not counted in the depth (each trapped by the
runtime's alignment check, exit 4, in the behavioural comparison); `push imm8` used for a
value that needs imm32 (caught by the objdump cross-check, below); a constant runtime
argument lost; and for the dropped frame, calls out of the body not adjusted, calls waiting
for a later function not moved, calls to compiled functions not recorded, and a frame
dropped although there are locals.

Gaps closed after review: `tests/run/code_shapes.obx` has pending forward calls from
functions before and after zero-frame ones (several calls, with and without locals or
parameters), and constants at the push imm boundaries (127, 128, -128, -129, 2^31-1, -2^31,
2^31, -2^31-1 and others). It catches, through Stage B alone, a shift of every waiting call
(`> at` replaced by `>= 0`, or by no test), `fits8` with `<= 128` or from -129, and an imm32
masked to 31 bits. `argConsts` is shared by `rtCall` calls, so each call now owns the part
from its base index up and truncates back to it, which makes a re-entry from `load()`
harmless (chosen over a guard or a local array, which allocates per call). One mutant is
equivalent: dropping `!a.strTemp` from the constant path of `rtCall` changes nothing, since
a constant never has `strTemp` set, so the guard is defensive and left in.

**The encoder against objdump.** obo's encoder had no oracle of its own: `port_test.py`
compared it with `./obo`, built from the same source. It now also runs mainline's objdump
groups (`obx/tests/encoder_test.py`) against the port, and `group_push_immediates` for the
new form.

**Loop inversion: design (built after the per-path bindings merged, since it
rewrote the same loop functions; see "Loop inversion (done)" below).** A `while` reads its condition before its body, but the
inverted loop emits it after: `jmp cond; body: ...; cond: C; jcc body`. Three ways to get there
in one pass:

1. *Relocate the condition's bytes.* Compile the condition where it is read, as now, but stop
   before its final jump. Copy its bytes out, truncate the code back to the loop's top, emit
   `jmp cond`, compile the body, then append the bytes again and move what they hold by the
   distance d they moved: calls out of them (the entries `farCalls` gained while the condition
   was compiled) get rel32 - d; calls waiting for a later function (fixups in the condition's
   range) get + d; and the condition item's true and false lists, whose links are positions
   threaded through rel32 fields ("Allocation (done)"), get + d on every link and on each head.
   Jumps inside the condition are relative and need nothing. Then `jcc cc` to the body, the
   true list fixed to the body, and the false list and `break` fall through. `continue` goes
   to the moved condition. A condition holds no defers, breaks or continues, since it is an
   expression. The checker sees the condition and then the body once each, in source order,
   as now.
2. *Move the body instead.* Emit the condition at the top and move the body after it. The body
   holds far more positions: nested loops' break and continue chains, defer subroutines,
   fixups of every kind. Rejected.
3. *Compile the condition twice.* Save the scanner's state (the source is one string, so
   rewinding is cheap) and compile the condition again after the body, keeping the top copy
   as a guard. No relocation, but the checker would run the condition twice: moves,
   invalidations, `selfCalls` and `useSeq` counts and error reports would happen twice, at two
   states, and the code would grow by the condition. It is a token pre-scan in all but name,
   which is deferred with Phase 2.

Recommended: (1). It shares `dropFrame`'s bookkeeping (`farCalls`, fixups), adds the chain
walk, keeps the checker untouched and the code size the same. A `for` loop needs no relocation:
its test is generated code over locals stored before the loop, so it can simply be emitted
after the body. Mutants: each kind of position not moved (a call out, a waiting call, a link
of each list); tests: a `while` whose condition calls a function declared later, concatenates
a string, indexes an array and uses `&&` and `||`.

#### Loop inversion (done)

Built as option 1 above, in `whileStat` and `forStat`. The checker is untouched: it sees the condition
once, where it is read, and the body after it, with the same snapshots, free log positions and loop
marks as before. Only the bytes move.

- A `while` compiles its condition where it is read, copies the bytes out (`lifted`, a stack shared by
  nested loops, so no array per loop) and cuts the code back to the loop's top. It emits `jmp cond`,
  compiles the body, and appends the bytes after it, followed by `jcc body`. The false list and `break`
  fall through to the loop's end; the true list goes to the body.
- Moved by d, the condition's bytes keep these positions current: its calls out (the `farCalls` entries
  added since the condition began) get rel32 - d and the entry + d; its calls waiting for a later function
  are taken out of those functions' fixups when the code is cut back (`waitFn`, `waitAt`, found only when
  `fwdCalls` changed) and put back + d; and the true and false lists are walked, + d on the head and on
  every link (`shiftWaits`). Jumps inside the condition are relative.
- Nothing else holds a code position during a condition. Checked: the free log, `Loop.exits/conts` and
  their `exitAt/contAt` are indexes into the checker's own logs, not code; string temporaries and the
  register state are checker and allocator state (all registers are free where a condition ends);
  `pendLine` and the error positions are source positions; there is no line table. Defers and `break` and
  `continue` cannot occur in an expression, and defer subroutines in the body do not move.
- `continue` goes to the moved condition. `while true` keeps its old shape (nothing to move).
- A `for` loop emits `jmp test; top: body; cont: increment; test: ...; jl top`. For the array form the
  head still loads the index and the array to fetch the element, and the test loads them again, so that
  form executes two more loads and one fewer jump per iteration.

Measured with `bench/micro.py -n 11`, the same machine and session, the compiler of main (4a6c49d) and
this one in turn:

| Benchmark | Main | Inverted |
|---|---|---|
| loop | 576 ms (539-581), 36 instr, 12 per iteration | 534 ms (528-543), 36 instr, 11 per iteration |
| fib | 629 ms (602-636), 54 instr | 591 ms (558-605), 54 instr |
| array | 403 ms (369-419), 99 instr | 401 ms (392-413), 99 instr |
| alloc | 471 ms (431-955), 51 instr | 471 ms (462-698), 51 instr |

The loop is 7% faster (one `jmp` fewer per iteration; the static count is unchanged because the entry
`jmp` replaces it). fib's difference is within the run-to-run range (it has no loop; an earlier
interleaved run showed 602 to 614 ms), array and alloc did not change: within noise. The port's own
code (the same source compiled by each compiler): 50,267 instructions and 184,052 bytes of code
before, 50,327 and 184,388 after (the array `for` test); compiling itself takes 36.1 ms before and
35.8 ms after, no change. `tests/port_compare.py`: the live peak is 1,169,952 bytes (budget 1,351,000;
main 1,161,088 for its own source) and 15.82 MB allocated against 15.56 MB, which comes from the
source being 2% longer, not from the lifting (the lifted code and the waiting calls are on shared
stacks).

Mutants (`tests/mutate_port.py`, all caught by `tests/run/loop_inversion.obx` or the comparison): calls
out of the condition not adjusted, and not moved in `farCalls`; waiting calls not adjusted, and not
lifted; links of the lists not adjusted; the head of the true list and of the false list not
adjusted; `continue` going to the body, and to the end of the condition; the `for` test jumping back to
the wrong place. `continue` to the old position of the condition is the loop's `jmp cond`, which
reaches the condition anyway, so that mutant is equivalent and not planted. The test has a `while`
condition that calls a later function, concatenates strings, indexes arrays, uses `&&` and `||`
(chains of three in each direction), calls the runtime and allocates, and `while true`; with `continue`,
`break`, nested loops, a loop in a function with no frame, and defers in the body.

### Phase 2: register allocation for scalar locals (planned; see "Phase 2 (done)" below for what was built)

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

### Phase 2 (done)

Built in `src/obo.obo`; the pre-scan of one function (the user's decision, recorded under "Open
decisions") is the only departure from a single pass.

**Design.**

- *Registers.* `rbx` and `r8`-`r15` are the expression pool. A local lives in one of `r15`, `r14`,
  `r13`, `r12` (a "home"), taken out of the free list while the local is in scope, so it is never
  handed out as a temporary (`release` refuses it; `setFree` leaves it out). The pool keeps `rbx`,
  `r8`-`r11` at least. `rax`, `rcx`, `rdx` stay scratch (division, shifts) and `rdi`, `rsi`, `rdx`,
  `rcx` the C argument registers. No register is pinned; there is no frame pointer change.
- *Candidates.* `int` and `bool` locals (`var`, `let`, the variable and limit of a `for a..b`) and
  by-value parameters. The language cannot borrow a scalar (`only records, closed types and arrays
  can be borrowed`), so no scalar ever has its address taken; what stays in memory is everything
  else: `byte`, strings, owned values, the element variable of an array walk, the `match` subject
  slot, and anything declared inside a `defer` body (a defer subroutine is compiled apart and may run
  where other locals hold the registers).
- *Choice.* `scanFunction` reads the raw text of the body once (no lexer state, so a lexical error is
  still reported by the real scanner, where it was): the names declared (parameters, `var`, `let`,
  `for`) with their uses weighted 1/8/64/512 by loop depth, and the calls to the program's own
  functions, weighted the same way. A name gets a register at its declaration if its uses minus twice
  the weighted calls reach `HOME_MIN` = 8, a home is free, and fewer free homes than names ranked above
  it that are still to be declared. Registers are given back at the end of the block, so sequential
  scopes reuse them. The scan also bounds what a statement holds in registers (below) and allows only
  `7 - that` homes (at most `REG_HOMES` = 4), keeping the pool large enough: with a fixed four, 7 of 1,400
  random programs failed with "out of registers" that compiled before. A cap from the nesting of the value, then
  from the destination too, still missed flat chains and continued lines; the compile-again rule below is what
  makes the allocation never refuse a program that compiles without it.
- *Running out of registers: compile again (complete by construction).* Two rounds of review each found a
  shape the scan's estimate of a statement's register need missed (a flat chain of element operands holds a
  base and an index per operand as the precedence levels stack up; a statement continued over lines), so the
  scan is only a heuristic for which locals get registers (and for how many, so that the retry below is
  rare), and safety does not depend on it. When `alloc` finds no temporary free and locals hold registers,
  `giveUpHomes` makes those registers temporaries again (as in a function with no locals in registers) and the
  pass goes on to the end of the function, its code to be thrown away. If it reaches the end with no error,
  `fnDecl` rewinds and compiles the function again with no registers for locals (`homeRetry`, then
  `fnBody(..., false)`). Why that is exact:
  - Before the switch, the pass needs at most the temporaries the function with all its locals in memory
    needs (a local in a register is read in place, where one in memory is loaded into a temporary), and a
    local's register is not a temporary. So the function with no registers for locals cannot have run out
    earlier. After the switch the pass has at least as many free registers as that function. If it runs out
    of registers then, so does that function, and the error (same message, the line and column of the token
    where it fails) is the real one. If it does not, the second pass, which is exactly the function compiled
    with no locals in registers, may still run out later, and then that is the error.
  - So a program is refused with "out of registers" exactly when `obo-noregs` refuses it. Only for a function
    with two errors can the one reported differ (the first pass may reach a checker error before the position
    where the second runs out of registers, or the reverse); the checker's own messages are those of main.
  - What a pass changes and the second must find as it was: the code (cut back to the function's start), the
    calls waiting for functions declared later (`fixups` of the unresolved ones, cut back to the start), the
    scanner (position, line, column, last token and the current token), `selfCalls`, `userCalls`, `useSeq`,
    `fwdCalls`, `argTempsUsed`, what the function is recorded to free (`f.freed`, `freesAll`, `writesHandles`),
    and the pool of argument holders (`argTemps`, cut back to its length at the start: the first pass's holders
    are symbols of the arena it truncates, so a kept handle would dangle; `p_held` holds a reference argument
    while a later one runs, in a retried function) The symbols and
    bindings of the pass are truncated at its end as they always were; the string pool keeps what the first
    pass added (a literal is found again by its text). The snapshot is eleven integers and a string; nothing
    is copied. The counters and the freed set are only compared inside one pass or re-derived, so their
    restoring has no mutant (equivalent); every other item has one.
  - A function starts with the free list in pool order (`fnBody` calls `setFree(0)`), whatever the function
    before it left, so a retried function is compiled from the same state as in `obo-noregs`, and gives the
    same bytes: `tests/homes_edge.py` compiles a program of retried functions only with both compilers and
    compares the executables (mutant: the line removed).
  - The restores of `f.freed`, `freesAll` and `writesHandles` (and of the counters) are defensive: the second pass
    records the same facts again, and the check of a recursive call that writes through a handle is deferred to
    the end of the function, so no test discriminates them; they are kept so that the second pass starts from
    the state of the first one's start.
  - The first pass must not report what the second would: an error it meets after the switch is the error
    the second would meet, since the checker does not read registers.
  `tests/run/register_pressure.obx` has the shapes that were refused (destinations of every kind at depths 3
  to 6, flat chains of 3 to 6 operands joined by `+` and `*`, statements continued over lines with and without
  a comment after the operator, and `p_retry`, a retried function with calls waiting for a later function).
  `tests/homes_edge.py` compares `obo` with `obo-noregs` on 1,028 statements (those shapes at depths 1 to 12, 400
  random nested ones and 400 random flat chains with line continuations): all agree (119 are refused by
  both). The scan still reads a line that ends in an operator as continued.
- *Calls.* Deviation from the plan's callee-save: the pool holds `rbx` and `r12`-`r15` as temporaries,
  so a callee may clobber any of them without saving, and the existing convention (the caller pushes
  the registers in use) covers the homes too. A home is "in use", so `callFn` pushes the homes in scope
  with the live temporaries around an obo call, and pops them after; nothing changes in prologues,
  epilogues, `return`, or defer subroutines, so there is no register to restore on any exit. Runtime
  calls do not push homes: the C runtime keeps `rbx` and `r12`-`r15` (checked: `runtime.c` is plain C,
  `entry.S` only touches `r11`; panics and `exit` do not return). Pushes go through `pushR`, so the
  alignment pad counts them.
- *Items.* A home is a `reg` item with `home` set. `load` copies it (callers change what they get);
  `asReg` reads it in place (compare, argument push, runtime arguments, indexes, stores, return value).
  A store to a home is a `mov`; `+=`, `-=`, `*=` change it in place; `x * const` uses the three-operand
  `imul`. The checker is untouched: no `mode == "mem"` test of the checker applies to an int or a
  bool, and the diff of compile outcomes against main (exit code, stdout, stderr, so every message and
  position) is empty over 266 programs and error cases (tests/errors, tests/run, examples, bench).
- *Off switch.* `const REG_HOMES = 0` in the source; `make obo-noregs` builds that compiler. No CLI flag.
- *Encoder.* No new encodings: r12-r15 were already pool registers (`port_test.py`'s objdump groups pass).

**Gate.** The loop (`micro.py --disasm loop`) has no memory access:

```
401021:	mov    $0x0,%r14d          s
401027:	mov    $0x0,%r13d          i
40102d:	jmp    0x40104c
401032:	imul   $0x2,%r13,%rbx
401039:	mov    %r14,%r8
40103c:	add    %rbx,%r8
40103f:	mov    %r8,%r14
401042:	mov    %r13,%r9
401045:	add    $0x1,%r9
401049:	mov    %r9,%r13
40104c:	cmp    %r15,%r13           n in r15
40104f:	jl     0x401032
```

(`s = s + e` and `i = i + 1` still go through a temporary; an in-place form is left for a peephole.)

Measured with `bench/micro.py -n 11` (`OBO=./seed/obo` for main's compiler), same machine and
session; median ms (range), instructions in the code segment:

| Benchmark | Main | Registers |
|---|---|---|
| loop | 569 (564-687), 36 | 434 (424-441), 28 |
| fib | 623 (619-630), 54 | 618 (614-631), 54 |
| array | 417 (409-429), 99 | 239 (235-246), 87 |
| alloc | 485 (468-531), 51 (review, 15 runs: 574 ms) | 515 (498-1705), 44 (review: 571 ms) |

loop is 24% faster, array 43%. fib has no loop and its `n` scores 5, under the threshold, so its code
is unchanged. alloc showed about 6% slower in my three interleaved repeats, but the review's 15
interleaved runs measured 574 ms on main against 571 ms on the branch: the difference is noise (the time
is the runtime's allocation and free; the branch runs 7 instructions fewer).

The port compiling its own source (`make obo-noregs` against `obo`, same source): 53,016 to 52,735
instructions and 196,957 to 194,704 bytes of code (a compiler is calls and branches, so few
locals qualify); the compiler built either way compiles itself in 46 ms, no difference. The
pre-scan costs time: the same compiler takes 43.9 ms with `REG_HOMES = 0` and 46.4 ms with 4, 6%.
`tests/port_compare.py`: live peak 1,217,456 bytes (main 1,183,600, budget 1,351,000 unchanged), 18.5 MB
allocated, 501 of 501 behave the same. These are figures on the Phase 2 branch's base; on main after the merge the
port's peak was 1,323,680 bytes (see "Peak memory" below).

**Mutants** (`tests/mutate_port.py`): the homes not pushed around an obo call; a home released as a
temporary; a runtime call clobbering `r15`; an array walk's element variable in a register; one
register for two live locals; a parameter not loaded; an uninitialised local not zeroed; a compound
assignment on a copy; `x * const` writing the local; `load` of a home uncopied; a `for` limit sharing
the variable's register. Those that change only speed (the scan counting nothing, the ranking
ignored, a register not given back at a scope's end, a for limit's register leaking, a bool parameter
left in memory, `return` copying a local, a runtime call pushing the locals' registers) are caught by
`tests/homes_check.py`, which counts stack-slot instructions in seven compiled programs and refuses a
pattern in two; the compile-again rule by
`register_pressure.obx` (no registers given up, code not rewound, waiting calls kept, scanner or token not
restored); a bare hot local as a
statement (`|| x.home` in the "does nothing" check) by `tests/errors/register_alloc.obx`. The terms of the cap
(destination, call arguments, nesting) have no mutants: they only decide how often the function is compiled
again, which `tests/homes_edge.py` and `register_pressure.obx` show cannot change what is accepted. Not planted, equivalent: the prologue and
epilogue mutants of the plan (there are none), a home allowed in a `defer` body (a defer's registers
are dead when it runs), and a home handed out again at register exhaustion (the homes are the last
registers in pool order, so only an expression needing more registers than the pool has would reach
one). `tests/run/register_alloc.obx` covers more candidates than registers, locals across calls,
recursion and runtime calls, parameters, bools, address-taken beside plain locals, nested and
shadowing scopes, `for` loops, defers, early returns from loops, compound assignment, mut parameters
and by-value passing; its expected output was taken from main's compiler.

### Phase 3: calling convention

Pass arguments of obo-to-obo calls in registers instead of pushing them on the stack.
Do this only if Phase 2's numbers show that call overhead matters on the fib benchmark.

*Gate:* fib improves; runtime calls are unchanged.

**Measured; not built (decision).** Phase 2 left fib unchanged (618 ms against 623, 54 instructions), so
the question is what register arguments could still save. `perf stat` on fib(40): 5,795,308,001
instructions, 2,854,422,135 cycles (IPC 2.03), 596 ms; 331,160,281 calls, so 17.5 instructions and 8.6
cycles a call. The code of one call (`micro.py --disasm fib`): the caller computes `n - 1` in a
temporary and pushes it (1 store); the callee reads `n` from `[rbp+16]` three times (`n < 2`, the
`return n`, `n - 1` and `n - 2` on the two paths, so one or two loads a call); the caller then pops it
with `add $0x10,%rsp`. A register convention removes the push and the `add rsp`, and the loads of `n`.
But `n` is live across the first recursive call, so the callee must still keep it in memory (a spilled
slot, or a home pushed by the caller around the call); the store-to-load forwarding on `n` stays on the
path. Upper bound, measured: I hand-wrote both conventions of fib in assembler (one `push rdi` in the
prologue spilling the argument, the second call's alignment pad kept, nothing else different) and ran
fib(40) 12 times each, interleaved: mean 0.676 s with stack arguments, 0.649 s with `rdi` (4.0%, with a
spread of about 0.04 s in each, so within noise). The saving is 2 to 3 instructions of 17.5. The real
implementation would also need the argument-order holds, the homes' push around a call, the retry, and
a spill fallback for more than six arguments, with no further gain on fib.

Decision: the gate's premise (call overhead worth about 5% or more on fib) does not hold, since the best
case is about 4% and not distinguishable from noise. Phase 3 is stopped, nothing is built, and
`src/obo.obo` and the seed are unchanged. The cost of fib is the call/return pair and the stack-carried
`n`, which a calling convention does not change; Phase 4 (checks) does not touch it either.

### Phase 4: checks and allocation

- **Redundant checks.** Measured, not built (see "Redundant checks" below). Hoist or remove bounds, handle and nil checks inside loops,
  using facts from loop conditions. Reuse the narrowing machinery where it fits.
- **Allocation.** The cause of the port's ~50% gap (Stage B, above): four arrays per
  `Item`, and copies in `copyInts` and `dupItem`. Measure with Phase 0's allocation
  benchmark before changing anything. Done: see "Allocation (done)" below.

*Gate:* each change shows a measured gain on its benchmark.

### Redundant checks (measured; not built)

`array_obo.obx` is the only benchmark with bounds checks in a hot loop. Its inner loop (`micro.py --disasm array`)
does three `a[i]` accesses per iteration (`a[i] = a[i] + r`, then `total + a[i]`), each with a reload of `a`, a
`cmp (%reg),%i; jb`, and a load of the data pointer. Upper bound, measured: a compiler with `boundsCheck` emitting
nothing (invalid code, for measurement only) against the current one, `perf stat`, 5 interleaved runs:

| | time | instructions | cycles |
|---|---|---|---|
| with the 3 checks | 0.22 to 0.24 s | 4,900,731,697 | 1,048,134,155 |
| no checks (upper bound) | 0.18 to 0.19 s | 3,700,731,657 | 816,309,458 |

So all three checks cost about 22% of this benchmark. But none of them can be removed from a fact that is available:

- **Loop-condition facts.** The loop is `while i < n` with `n` a separate constant, not `len(a)`; no benchmark except tokens (below)
  has `i < len(a)` or `for i in 0..len(a)`. A
  fact `i < len(a)` needs `a` unchanged in the whole body, which a single-pass compiler does not know when it
  emits the loop head: it would need a token pre-scan of the body plus `mightFree`/`pureFn` for every call in it
  and the alias paths (`mut` calls, handles, references, globals). The benchmark would have to be rewritten to
  show a gain, which the gate does not allow as evidence.
- **Same index, same array, within a statement or body.** Only this shape occurs in `array_obo`. Removing the
  second and third checks needs a fact (array local, index home) that dies at every call, label, assignment to
  either, and write through an alias, in an emitter where those events are spread over many paths (`fixHere`,
  `rtCall`, `callFn`, every store form, loop-back edges). Best case is 2 of the 3 checks, perhaps 10 to 15%, on
  this one benchmark; the cost is a new invalidation surface in code that has no test for a missed kill.
- **Handle and nil checks.** No benchmark runs a handle check in a hot loop (`alloc`, `lists`, `trees` are
  dominated by allocation), so there is nothing to measure.
- **`for x in a`** already has no per-element bounds check (the walked array is frozen by `iterating`).

**Re-examined with `tokens_obo.obx`**, which does have guarded indexing: `while i < len(src)` and
`... && isDigit(src[i])` in `scan` (`src` is a `&[]byte`), and `for i in 0..len(s) { ... s[i] }` in `hash` (`s` is
a string). Upper bound, measured the same way (a compiler whose `boundsCheck` emits nothing, so it removes every
bounds check in the program, not only the guarded ones; input `obx/src/obxc.obx`, 7 interleaved runs, `perf stat`):

| | time | instructions | cycles |
|---|---|---|---|
| with checks | 0.02 s | 289,064,128 | 87,238,714 |
| no bounds checks (upper bound) | 0.02 s | 284,708,525 (-1.5%) | 84,991,583 (-2.6%) |

The guarded checks are a subset of that, so the real gain is under 2.6% in cycles and not distinguishable in wall
time. The workload is dominated by allocation and string work, not by indexing. Below the gate.

Decision: no check is removed or hoisted. The gate (a measured gain on a benchmark that has the pattern) is not met
for the loop-condition facts, and the one pattern that exists is left until a benchmark makes the case.
`src/obo.obo`, the tests and the seed are unchanged.

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

`PEAK_BUDGET` stays at 1,351,000. On the merged branch the peak is 1,211,104 bytes, a margin of 139,896 bytes. That margin is less than the
mutant "parameters kept" adds (about 190 KB), so the budget check now catches that mutant directly; the
`KEEPS_ALIVE` check in `tests/mutate_port.py` still applies on its own.

Not done: the per-call arrays in `rtCall` and `rtCall0/1/2` (`regs`, `temps`, the
argument arrays), the `fixups` array of each Sym, and the snapshot copies (`copyBools`).
Item creations (about 32,000 per self-compile, mostly loads and voids) were left as they are.

### Peak memory (done)

Measured on the port compiling its own source, with `OBO_STATS=1` and a temporary runtime that
recorded what was live at the peak (not committed). At main (37bdfeb) there were two peaks of
nearly the same height. The first, 1,273,200 bytes, came when the code buffer grew from 128 KB to
256 KB: both blocks were live (393 KB), with the source text (203 KB), the runtime's code and
data (75 KB, read before the compile), 541 symbols at 288 bytes each plus four arrays each
(fixups, params, pmodes, freed: at least 80 bytes per array, empty or not), and 454 types with two
arrays each. The second, 1,265,344 bytes, came when the image was allocated: the image (300 KB),
the 256 KB code buffer and all the tables. Each row below is one change, measured after it. The
peak moves between the two points, so a saving depends on the order the changes were made in.

| Change | Live peak (bytes) | Total allocated (bytes) |
|---|---|---|
| Main at 37bdfeb | 1,273,200 | 18,497,008 |
| Code in 16 KB chunks, each allocated at full size (`emit`, `codeByte`, `setCode`, `truncCode`): growing the code never copies it | 1,224,944 | 18,271,872 |
| Symbols, types, bindings, `universe`, `globals` and the string keys freed before the image is built (`releaseTables`) | 1,157,104 | 18,296,816 |
| The runtime read before the compile only to check it can be read, then again for the image (`runtimeReadable`) | 1,082,320 | 18,409,792 |
| One array, optional and handle type per base (`derived`, with `arrayOf`, `optOf`, `handleOf`): 494 types live at the peak became 52 | 968,032 | 18,307,696 |
| The last chunk kept apart (`cur`, written by index), so that `emit` only stores a byte | 970,480 | 18,372,512 |
| The four function-only arrays of `Sym` (`fixups`, `params`, `pmodes`, `freed`) moved to a `FnRec` record, held by a handle; the other symbols share `noFnRec` (about 104 KB at the peak) | 954,912 | 19,321,216 |

The peak falls by 302,720 bytes (24%). `tests/port_compare.py` measures 970,512 (it was 1,273,136),
against the budget of 1,351,000, which is unchanged. The last row costs 2,448 bytes (`cur` is a
chunk of its own, copied into a new one when full), but `emit` stays a store and a test. The
checker rejects the cheaper forms: with a `replace` or `truncate` of `cur` in `emit`, a call that
emits may free, and the existing assignments to a field across such a call (`x.F = waitJump(x.F,
jcc(...))`) are refused. The peak is now late in the compile, with the code nearly
complete: the symbols with their arrays (about 370 KB), the source (205 KB) and the code (197 KB)
are most of it. The output did not change: every program and error case compiles to the same bytes
and messages as main's compiler, also with 32-byte chunks, which cross a chunk boundary in nearly
every cut-back of the code (`truncCode`). The port compiling itself runs 0.5% fewer instructions
(662.4 million against 666.0 million on the same source) and takes 418 page faults rather than 517.
Its wall time, about 41 to 42 ms at the minimum on WSL, is within the noise.

A shared derived type is never changed once made, and types are compared by structure (`same`),
so sharing them changes no result. Only the basic types are compared by identity.

**Current figure, on main with Phase 2.** The table above was measured on this branch's own base, before Phase 2 (register allocation). On main, with Phase 2 merged and this work on top of it, the port's live peak compiling itself is 1,059,280 bytes (`tests/port_compare.py`). Phase 2 alone had measured 1,323,680 bytes on main, so this work brought the peak down by 264,400 bytes. The difference from 970,512 (88,768 bytes) comes from Phase 2's register allocation.

**Budget lowered, 1,351,000 to 1,091,000** (measured live peak 1,059,280 bytes, about 3% over). The peak memory work lowered the peak, so the budget follows it. The budget named in the paragraphs above was 1,351,000 when they were written.

**Budget lowered, 1,091,000 to 983,000** (measured live peak 954,912 bytes, about 3% over). The function records moved out of `Sym` (the row above). `FnRec` holds what only a function has: the calls waiting for its address, its parameters and their modes, and the globals it might free. `Sym` holds a handle to it, `fnRec`. A function gets its record in `fnDecl`, and every other symbol holds `noFnRec`, one shared record that nothing writes, as `bind` does with `noBind`. The output did not change: the 44 programs and the port's source compile to the same bytes and messages as main's compiler.

**Budget raised, 983,000 to 2,000,000** (measured live peak about 0.95 MB), on 2026-10-26, by the user's decision. A budget about 3% above the peak left new checker work only about 28 KB of headroom (983,000 less 954,912 bytes). Leaks are still caught separately, by the leak check in `tests/port_test.py`, so the budget no longer has to guard them. `mutate_port` now checks memory mutants against the baseline peak plus 3%.

**Budget raised, 2,000,000 to 2,500,000** (2,500,000 *decimal bytes*), on 2026-10-31, by the user's explicit decision: "yes, increase budget to 2.5MB for ALL work". This is the shared compiler live-peak cap for all workstreams, including Stage B and the future float branch; the historical 2,000,000 Stage B cap is superseded. The original Stage B breach remains historical: the first complete Stage B build measured 2,001,872 bytes (port self) and 2,001,664 (seed-on-source), and the process note (comment trimming instead of stopping) is preserved in the private checkpoint logs. The corrected Stage B checkpoint measures about 2,001,856 bytes, so the new cap is an authorization, not an optimization claim, and leaves roughly half a MB of headroom.

The cap is defined once in `tests/peak_budget.py` and imported by both `tests/port_compare.py` and `tests/mutate_io.py`, so they cannot drift. The zero-array allocation mutant no longer falls to the absolute cap once the baseline is above 2,000,000, so the same module defines `EMPTY_ARRAY_MIN_DELTA = 30_000`: `mutate_io` requires the mutant's same-source self-compile peak to exceed the unmutated baseline by at least that floor, while still running the semantic I/O cases first. The current same-source measurement is baseline 2,001,904 -> mutant 2,046,992, delta 45,088 bytes; all 30 I/O mutants are caught. A failed stats report or a failed self-compile is a harness `BROKEN` result, not a catch. This adds no ownership/reference check relaxation; the EZA and tool memory budgets remain pinned and separate.

At the time of the budget decision above Stage B was still version-neutral and experimental at `0.9`/format 5/ABI 1. The release order from the user was floats `0.10`/format 6/ABI 1 first; Stage B followed as `0.11`/format 6/ABI 1 on top of the released float base. The final Stage B candidate and its migration are recorded at the end of this file; the historical `0.9` checkpoint text remains as history.

### Peephole pass (measured; built later, with loop-head alignment: see the next section)

The obx design (`HISTORY.md`, "Future work: an obx peephole optimizer") still applies: an
emit-time pass that keeps the compiler single-pass. The plan said to revisit it only if
the reloads that remain after Phase 2 are measurable on the benchmarks. They are not.

**What remains in the hot loops** (`micro.py --disasm`, on main at c9510c6):

| Pattern | Where | Found |
|---|---|---|
| store, then reload of the same slot | alloc: `mov %rbx,-8(%rbp)` then `mov -8(%rbp),%r8` | 1 per iteration; the loop is 25 ns of runtime allocation |
| mov chain `mov A,T; op X,T; mov T,A` (`x = x op y` on a local in a register) | loop (2), array (2) | the only pattern in the two tight loops |
| mov chain `mov m,T; mov T,r` | fib's base case (`mov 0x10(%rbp),%r8; mov %r8,%rax`) | 1 per call on half the calls; register moves are eliminated at rename |
| a second load of one slot (`mov -8(%rbp),%r10; mov -8(%rbp),%r11`) | array: 3 reloads of the array local per iteration | 1 avoidable |
| push, then pop of the same register | none adjacent (fib's `push`/`pop` have a call between) | 0 |
| jump to the next instruction | none (loop inversion left none) | 0 |
| compare with 0 after an op that set the flags | none (the compares are against `n` or a constant) | 0 |

**Upper bounds**, by hand-patching the compiled loop and counting with `perf stat` (Ryzen 7 8745HS, WSL):

- loop, `s = s + i*2; i = i + 1` as `add %rbx,%r14` and `add $1,%r13`: 9 to 5 instructions
  per iteration, and 1.60 to 1.00 cycles per iteration when the loop is laid out well
  (1,603,703,457 to 1,003,487,335 cycles for 1e9 iterations).
- array, the two chains folded: 4,900,731,697 to 4,100,731,731 instructions, but 1,036,857,494 to
  1,035,125,140 cycles (-0.2%) when the patched loop keeps the alignment of the original. Adding the
  shared array load: 1,036,198,175. The loop is bound by the store-to-load dependence through `a[i]`
  (5.2 cycles per element), not by instruction count. (A first patch that moved the loop to another
  alignment ran 19% slower with no instruction changed: see the layout effect below.)
- alloc and fib: the removable instructions are 1 to 3 of 44 and 54, among a runtime allocation and a
  call/ret chain; not measurable.

**The built pass.** A fold in `store`: the emitters note the copy of a local into a temporary and the
operation on it (add, sub, and, or, xor, imul by a register or an immediate, and `x * const`), and
when the store copies that temporary back into the same local, with the code ending where the
operation does and no position asked for (`pc()`) since the copy began, the three instructions are
replaced by the operation on the local. It was built and passed everything it was run against
(`run_fork` 441 plus a new `tests/run/peephole.obx`, `port_test`, `port_compare` Stage B 592 of 592 in my run
and 593 of 593 in the review, with a live peak of 969,648 bytes in mine and 969,712 in the review
(the patched figures move by a few dozen bytes between builds; main's, measured by `port_compare.py` on this
branch, is 954,912, and `PEAK_BUDGET` stays 983,000), `homes_check`, `homes_edge`, and the 10 mutants that the
comparison catches; an 11th, speed-only, caught by `homes_check`, was added later) and then measured with the
compiler of main and this one, interleaved, 7 runs, minimum cycles:

| | instructions | cycles |
|---|---|---|
| loop | 9.00e9 to 5.00e9 (-44%) | 2,008,549,766 to 2,005,327,336 (-0.2%) |
| array | 4.90e9 to 4.10e9 (-16%) | 1,039,606,821 to 1,010,995,649 (-2.8%) |
| alloc | 4.30e9 to 4.26e9 (-1%) | 2,314,391,888 to 2,366,360,719 (+2.2%) |
| fib | unchanged | 2,853,620,039 to 2,853,716,305 |
| lists, strings | unchanged | -0.3%, -0.4% |
| tokens, trees | unchanged | +9.2%, -4.5% (same instructions: noise from layout) |

The loop did not get faster although it runs 44% fewer instructions. Both versions run 2.0 cycles per
iteration, and the reason is where the loop sits, not what it holds: on this CPU a loop that
straddles a 64-byte line can cost 2 cycles per iteration whatever it contains. The same five-instruction
loop, assembled at 32 alignments, ran at 1.0 to 1.2 cycles in 19 of them, 1.3 to 1.8 in 4 and 2.0 in 9;
the nine-instruction loop of main ran at 1.60 in 14 and at 1.8 to 2.4 in the other 18 (mean 1.87 against
1.40 cycles, -25%). So the fold would pay on average over layouts, but the committed benchmarks do not
show it, and the noise between identical programs (tokens +9%, trees -4.5%) is larger than the array's 2.8%.

**Decision at the time: not built; no change to `src/obo.obo`, the tests or the seed. Superseded: the fold was built with loop-head alignment (next section), which is the lever this paragraph ends by naming.** The gate is a measured gain
of a few percent on a real benchmark, and none of the eight shows one outside the layout noise. The
change is kept as `tools/peephole_fold.patch` (a diff against c9510c6 that adds the pass, the test and
11 mutants; 10 are caught by the comparison and one, the fold never applied, by `homes_check.py`). Not
planted, because equivalent today: the check that no position was asked for between the copy and its
store (no emitter asks for one there) and the clearing of the notes in `truncCode`. If the fold is
wanted later, the lever the numbers point to is first the alignment of loop tops (padding the target
of a backward jump to 16 bytes), measured the same way.

### Loop-head alignment (built, with the peephole fold)

The target of every backward jump (a while loop's body, a `while true`, a for loop's body) starts on a 32-byte
boundary (`alignLoop`, `LOOP_ALIGN`), padded with multi-byte nops. The code is at `CODE_BASE`, a multiple of 4096, so a
position that is a multiple of 32 is an address that is. In an inverted loop and a for loop the padding sits after the
jump to the test and is never executed; a `while true` runs it once.

**What had to change first: `dropFrame`.** A function with no locals used to cut the seven bytes of `sub rsp, 0` out
and move its body back, which made every position taken in the body wrong by 7 and aligned nothing in the hot loops
(they are in frameless functions: the loop benchmark's `main` has no locals). It now moves the entry instead: the four
bytes of `push rbp; mov rbp, rsp` are rewritten 7 bytes on, right before the body, the seven bytes in front are dead
(`int3`), and `f.addr` is the new entry. The body does not move, so nothing in it is repointed except the calls to the
function: its fixups (calls from earlier functions waiting for it) and its calls to itself (the entries of
`farCalls` whose target is the old entry; `getRel` is unsigned, so the target is compared modulo 2^32). Every other call
out of the body is relative to a place that did not move, which is also why the old shifting of other functions' fixups
is gone. The retry (Phase 2) calls `truncCode(f.addr)` before any drop, with the old entry still the real one. An
earlier trial that overwrote the `sub` with an executed 7-byte nop cost every frameless call an instruction (fib +5.7%
instructions) and tokens +2.7%; this one executes nothing extra.

The peephole fold of `tools/peephole_fold.patch` is merged with it (its tests and 11 mutants; the patch file stays as
the record of the base it was measured on). On its own the fold gains only on the benchmarks whose loop sits well; with
the loop heads aligned it pays on the loop.

Measured on main (bb54c20), Ryzen 7 8745HS under WSL, `perf stat` cycles (`:u`), variants interleaved, 25 runs,
minimum. Other agents were running suites: load average 2 to 13 during the runs, so the per-run spread was 5 to 200%
and only the minimum is used; differences under about 3% are noise. `eE` = entry move alone, `eP` = entry move and
32-byte padding, `eF` = those and the fold.

| cycles vs main | eE (entry only) | eP (+ pad32) | eF (+ fold) |
|---|---|---|---|
| loop | 2.006e9 (0.0%) | 1.605e9 (-20.1%) | 1.002e9 (-50.1%), 9 to 5 instructions per iteration |
| fib | 0.0% | 0.0% | 0.0% |
| array | -0.1% | 0.0% | -0.3% (the fold's -3.0% did not reappear) |
| alloc | +2.2% | -0.6% | +1.5% (spread 70 to 220%: noise) |
| lists, strings | within 1.3% | within 1.8% | within 2.4% |
| tokens | +0.3% | +1.2% | +1.8% (spread 10 to 47%) |
| trees | -5.3% | -3.7% | -6.7% (spread 7 to 32%: noise) |

Earlier measurements on the way: 16-byte padding alone put the array loop in a bad place (+19% cycles with the same
instructions), 32 bytes did not; the executed-nop variant of `dropFrame` showed tokens +2.7% reproducibly at 41 runs
(0.289e9 instructions in both), the entry move shows +0.3% to +1.8% with a spread larger than that.

**Tests.** `tests/run/loop_align.obx` (calls to a frameless function from itself, from before it through a forward
heading and from after it, mutual recursion, every loop kind, a condition calling a later function, a function compiled
again); `tests/homes_check.py` finds the backward-jump targets in the disassembly of a program with every loop kind, in a
function with a frame, one without and one that was compiled again, and requires them on 32-byte boundaries.
`tests/mutate_port.py` has 7 `align:` mutants (no padding, wrong padding, each loop kind unpadded, no padding in the
retry; caught by `homes_check`) and 4 `entry:` mutants (self-calls, waiting calls, entry not set, prologue not moved;
caught by the comparison). The old `dropped frame:` mutants of the shifting version were replaced by these. `homes_check` also checks the padding bytes: the nops before each loop head must be exactly what `alignLoop` emits for their length (compared with the bytes, not only the disassembly), and the port compiling itself must show every length from 1 to 31 (4 `align: ... nop contents` mutants). Final run after the merge: `mutate_port` 250 of 250 caught, `mutate_fork` 212 of 220 with the 8 equivalents, `run_fork` 488, `port_test` 431 of 431, `port_compare` Stage B 639 of 639, `homes_edge` 1028 with 0 differing, `own_fuzz --count 300 --profile both` 0 failures in 600, `doc_check` 29 examples.

**Limits.** The alignment is only as good as the loop's size: a loop longer than 32 bytes can still straddle a line.
The dead bytes cost 7 bytes per frameless function. The port's live peak compiling itself is 1,016,080 bytes after merging main at 18da3e5 (main alone: 981,984; the growth is the fold's and the padding's own code and the dead bytes in the compiled port), under the budget of 2,000,000.

### Rules for every phase

- Each rewrite gets a mutant in `tests/mutate_port.py`.
- Benchmark times and instruction counts are recorded here before and after.
- Work happens on a branch in a worktree. `seed/obo` changes only through `make
  promote`, after the fixed point and the suite pass.

### Open decisions

- **Byte identity** (resolved: dropped, compare behaviour). `tests/port_compare.py`
  required the port's executables to match byte for byte, and any optimization changes
  the bytes. The `--no-opt` flag proposed here was not built. Stage B now compares
  behaviour: the same compile outcome (success, or the same error at the same
  line:col), and for a program that runs, the same stdout, stderr and exit code with
  `OBO_CHECK=1`. The fixed point (stage 2 = stage 3, in `make promote`) still compares
  bytes, since the compiler compiles itself.
- **Pre-scan** (resolved: allowed). A token pre-scan of one function is acceptable under the
  single-pass goal, as decision 5's "buffer one function at a time". Built for Phase 2; see
  "Phase 2 (done)".

## Handle-owner and reference false positives (done)

A batch of known false positives, each fixed by one rule, with its unsafe neighbours kept refused. Accepted
shapes: `tests/run/fp_loops.obx`, `tests/run/fp_refs.obx`. Neighbours: `tests/errors/fp_loops.obx`,
`tests/errors/fp_refs.obx`. Mutants: `FP_MUTANTS` in `tests/mutate_fork.py` (also run by `mutate_port.py`).
The write-up named `FP_join_carry_other_path` was not in this file; the shape found and fixed under that
name is item 1.

1. **Marks of an arm that does not leave the loop (join carry).** `armBlock` kept an arm's dead marks for
   after the loop when the arm ended and *any* break or continue had been compiled in it, including those of a
   loop nested in the arm. An arm that frees, runs an inner loop that breaks, and returns, wrongly put the free
   after the outer loop. The count is now per loop depth (`depthJumps`, `jumpsHere`, `addJump`): only jumps
   that leave the loop the arm is in count. Test: `returnWithInnerLoop` (run), the `break`/`continue`/`return`
   arm mixes (errors).
2. **A loop that re-points a reference, then reads it.** `loopSelfCheck` asked whether the loop read the
   reference (`usedAt`); it now asks whether it read it *before pointing it afresh* on every path to the read
   (`usedAt` of a ref local is set only for a read that is not fresh, the notion `loopBindCheck` already
   uses, and by a `for` walk of the reference). The condition of a `while` is compiled after `clearFresh`,
   so its reads count. Reads on one path only, in an inner loop, in the condition, passes to calls, walks
   and `k = &k.kids[0]` stay errors. The `LANGUAGE.md` section 12 example is now an accepted example.
3. **A field of a ref param taking the whole param's binding.** For a reference *parameter* the path of
   the designator is now kept (`designatorT` sets `x.path`, `itemBind` uses it), so `&p.ys` and `p.xs` are
   different places, in invalidation (`p.ys = ...` leaves `&p.xs` valid), in the argument check
   (`f(p.xs, p.ys)`) and in the held places. A parameter's own object has bind `[itself]`, so the path is
   relative to the right root; other parameters and globals still match by `reaches` regardless of path.
   Through a reference *local* the path was still dropped (the whole root); see "Reference-local paths (done)". While testing the held-place
   form (`replace(r, ...)` with `var r = &p.ys`) a hole that predates this batch showed up: `replace` and
   `take` of a reference local wrote the local's slot and handed back the borrowed object, which the caller
   then freed (exit 6, a double free, with the seed too). They are now refused (`replace cannot change 'r':
   it is a reference`); three held-place error cases that used the form now expect that message.
4. **`mut`-argument precision.** Each function keeps `FnRec.pfree`, a bit per parameter: it may free
   something below the reference it got (`recordFree` sets it for a parameter's root, so every site that
   already recorded a free for globals records it, including a `mut` argument passed on and a reference
   local made from the parameter). `callFn` invalidates a `mut` argument's roots only if `mutFrees(f, i)`:
   unknown callees (heading only, being compiled, i.e. recursion, or `freesAll`/`writesHandles`) and the
   62nd parameter on count as freeing. `pureFn` is unchanged, so the handle forms (`rd(h.xs, grow(h.xs))`,
   `two(&h[0], tr(h))`) stay refused. Pinned expectations changed: `references.obx` `change`/`reset` had
   empty bodies and now free something; in `own_fuzz.py` `stepArr` and `bumpRef` (scalar writes) no longer
   invalidate other references, and the invalid case uses a new `cutRef` that frees. The register retry
   (`homeRetry`) recompiles a function with its `pfree` bits kept: `run/mut_retry.obx` (a retried `mut` callee
   that only writes a scalar, a reference into another part of the argument is accepted) and
   `errors/mut_retry.obx` (the same callee assigning over a field, refused). A mutant that clears `pfree` and
   records nothing in the second pass fails both error cases.
5. **`alive(h)` is no use.** A bare-name argument restores the handle's `usedAt` (`lastNamed`, `unname`), so
   a loop that asks and frees is accepted and a handle moved by a loop can still be asked about. `alive(f(h))`
   and any other expression still count. The same restore serves item 8 (copies).
6. **f2, the loop-move rule.** A move of the owner in a loop is no longer refused at the move: it is
   pending (`pendPush`, as for frees) and judged where the loop ends. Refused (same message, same place) if the
   loop names the handle anywhere; otherwise accepted, and the handle is marked `movedLoop`: naming it again
   anywhere later, even after the loop, is refused. That keeps the earlier unsoundness out (a move clears the
   origin, so `take(a); a = nb(); h.n` would trap) and adds no new gap after the loop (`let h = handle(a);
   loop { take(a); break }; h.n` stays refused). Moves are never dropped by item 7's rule.
7. **A free in a loop is refused wherever the handle is used.** A block that always ends in a `break` or a
   `return` and has no `continue` in it drops the frees it made at its own loop level (`pendBlockEnds`): for a
   return, any handle; for a break, only handles declared in the scope directly outside the loop it leaves
   (`pendDepth` is then the index of that loop, the one the handle is declared outside of). A handle declared
   further out is judged where its outer loop ends, since that loop repeats. Frees made in a loop inside the
   block repeat and stay. The uses after the loop are still seen by the dead marks (`Loop.exitDead`). Covers f1 (`while true { ...; break }`) and the f7 shape of a use
   before a free that ends the loop.
8. **g2 (done), f1 (done), f7 (partly).** g2: a `let g = h` copy of a handle that is dead at that point
   does not count as naming `h` (`unname`), since `g` inherits the mark and every use of it is refused; a copy
   of a handle not known to be dead there counts. The mark is a may-flag, so a copy after a free on one path
   only is also exempt, soundly: `g` is marked dead on the merged path. f7 as a use that no path can reach
   (for instance guarded by a condition the compiler cannot evaluate) is not fixable in a single pass and stays.

**Left alone.** `rd(h.xs, grow(h.xs))` and `two(&h[0], tr(h))` (items for `pureFn`): a callee with a `mut`
parameter is still impure for the held-array and handle checks, because `pureFn` also feeds
`userCalls`, which the index checks use; relaxing it needs its own review. `if alive(h) { use(h) }` was still
refused then; see "Narrowing on alive".

**Fuzzer.** `own_fuzz.py` has an argument-order kind: `arg_order_stmt` (valid: the freeing or moving argument
first, `holdO(renewAt(xs, i), &xs[j])`; a scalar `mut` call after a reference; `take` in an argument beside a
reference to another local; `sum2(take(x), take(y))`, `sum2(take(x), take(x))`) with a model, and
`arg_order_bad_stmt` (invalid: a reference then a call that renews, moves, or `replace`s what it points into,
a global cleared, `sum2(x, take(x))`), also one of the `bad_stmt` kinds.

**Memory.** The port compiling itself peaks at about 1.03 million bytes live (981,888 before). The first
version stored `pfree` as a `[]bool` per function and measured 1,080,464; a bit mask removed 42 KB. The rest
is the source growing. `PEAK_BUDGET` (2,000,000) is untouched.

## Narrowing on alive (done)

**The gap.** `var a = nb(); let h = handle(a); if c { a = nb() }; if alive(h) { pr(h.n) }` was refused ("handle
'h' may name a freed object"), since the dead mark of `h` (`originDead`, a may-flag set by `originFree`) was
still set from the path with the assignment.

**Why it is sound.** The marks exist only for `let` handles made from a whole owned local, and the only thing
that sets one is a free of that owner (assignment over it, `drop`). A freed object never lives again (the
generation), so on a path where the mark is set `alive(h)` is false: the arm taken when it is true can only be
reached on the paths where the mark is clear. Clearing the mark at the start of the arm is the narrowing; the
frees inside the arm set it again by themselves (`originFree`), so no other rule is needed for "the narrowing
ends at a free": a free in the block, in a nested loop, in a nested arm, all mark the handle dead and later uses
are refused exactly as before. The loop rules are untouched (`pendPush`, `pendBlockEnds`, `movedLoop`,
`Loop.exitDead`, `usedAt`): an owner freed in a loop that names the handle is still refused wherever the guard
is, so `while c { if alive(h) { pr(h.n) }  a = nb() }` stays a (conservative) error. Handles with no origin
(`var`, fields, elements, parameters) were never checked statically and are not touched.

**The rule** (`ifStat`, `conjunction` and the `alive` builtin in `src/obo.obo`). A condition is a guard when
`alive(h)` with a bare name `h` is its first token (after one `!`), found by the token index (`tokSeq`,
`guardStart`): the builtin records `guardSym`, the token after it (`guardFollow`) and `freeSeq` (the frees so far)
into globals. `ifStat` then narrows if no owner was freed meanwhile (`guardFree == freeSeq`) and:
- `alive(h)` followed by `{` or `&&`, and no `||` was compiled in the condition (`orSeq`): the then-arm starts from
  the marks with `h` cleared (`d0[gi] = nil`). The accumulated marks after the `if` (`dacc`) start from the marks
  before, so the mark is back after the block, merged with what the block did.
- `!alive(h)` followed by `{` (the whole condition): the else arm (also an `else if`) starts with `h` cleared
  (`e0`). If the then-arm ends (`t1`: return, break, continue) the marks after the `if` have `h` cleared in
  `dacc` before the else arm's marks are added, so code after an early exit is narrowed until a free.
- `&&`: while the conjunction compiles the operands after `alive(h)` (`guardOpen`), the mark is cleared and put back
  (`guardMark`) when the conjunction ends, before any `||` operand runs and before the arm, so `alive(h) && h.n > 0`
  is accepted and the rest of the condition cannot leak the narrowing into an else arm. Only the conjunction that
  opened it restores (`mine`).
Refused (kept as before): `while alive(h)`; the else arm of a positive guard; `alive(f(h))` or a field (not bare);
`alive` not first (`c && alive(h)`), compared, `(alive(h))`, `!!alive(h)`; `alive(h) || c`; `alive(h) && c || d`
and any `||` in the condition (even in parentheses, a precision limit); `!alive(h) && c` or `|| c` as an early
exit; a guard on a different handle or a copy (copies carry their own marks). A copy `let g = h` made inside
the narrowed arm is live there, and dies with the arm's block. The early exit does not reach out of its block.

**Review fixes (round 2).** The first version had two holes and a gap, all found by review; the programs are
in `tests/errors/alive_narrow.obx`.
1. *A negated guard opened the conjunction.* `!alive(h) && h.n > 0` compiled the operands after `&&` with `h`
   narrowed, exactly where `h` is dead. `ifStat` now sets `guardNeg` before the condition and `conjunction`
   opens the guard only for a positive one.
2. *The early-exit narrowing left a loop that may run no time.* Marks used to only grow, so after a loop they
   were what the body left; the early exit clears one inside the body (`dacc[gi]`), and that state was taken as the state
   after the loop (`while c { if !alive(h) { return } ... }  h.n`, a `for` over an empty range or array, a
   `while flag()`, a body ending in `break`, nested loops, an else arm narrowed in a loop). `loopBodyV` now saves
   the marks at its start (`dEntry`) and unions them back into the marks after the loop, whichever way it is left
   (all loop forms go through `loopBodyV`). This only adds marks, so no pre-existing accepted shape changes.
3. *A move of the owner in a narrowed region.* A move clears the handle's origin but leaves its dead mark, which
   used to keep the use refused (`if c { a = nb() }; eat(a); h.n`); narrowing cleared the mark, so
   `if c { a = nb() }; if !alive(h) { return }; eat(a); h.n` was accepted (and traps: `eat` frees it). Now
   narrowing that clears a non-nil mark remembers it (`originNar`, per `originSyms` entry, sticky, truncated with
   the block; `narrowMark`), and `originMove` for a move of the owner marks such a handle dead again. Narrowing
   that clears nothing remembers nothing, so a move with no earlier free behaves as on main (`moveNoFree` in the
   run test: the handle still names the object that moved into another variable). The flag is sticky and not
   path-aware: a move in the other arm of an earlier narrowing also marks the handle dead (conservative).
Mutants: negated guard opens the conjunction, loop without the entry marks, move keeps narrowed handle usable,
narrowing not remembered, narrowing always remembered.

**Left.** A condition cannot free an owner (no assignment in an expression), so `guardFree == freeSeq` is a
defensive check that no test can reach (an equivalent mutant is not listed for it). A move of the owner followed
by its drop elsewhere (`consume(a)`) clears the origin and was never tracked; it is the same gap as outside a guard.
A loop whose body frees the owner and guards a use is still refused (above). `while alive(h)` is not narrowed:
the loop machinery compiles the body once and the back edge would need the same proof.

**Tests.** Accepted: `tests/run/alive_narrow.obx`. Neighbours: `tests/errors/alive_narrow.obx` (free before the
use, drop, free in a loop in the block, loop that uses and frees, use after the if, else arm, other handle, copy,
`alive(f(h))`, not first, compared, `||`, `&&` with `||`, negated forms, early exit then free, early exit in a
block, double negation, parentheses, `while`, guard in a loop that frees later, break out of the arm, dead mark
back after a conjunction, nested conjunction, else of a conjunction). Mutants: `ALIVE_MUTANTS` in
`tests/mutate_fork.py` (also run by `mutate_port.py`). No fuzzer kind: `own_fuzz.py` makes handles from elements,
which have no origin and are checked at run time only, so there is nothing for the guard to narrow.

**Memory.** The port compiling itself peaks at about 1.06 million bytes live (1.03 million before); the budget
`PEAK_BUDGET` (2,000,000) is untouched. The ifStat copies of the marks are transient.

## Origins per path (done)

**The gap.** A move of a handle's owner cleared the handle's origin (`hOrigin`) for the rest of the function,
on every path. `let h = handle(a); if c { var d = a; return } else { a = nb() }; pr(h.n)` compiled, because the
then arm had cleared the origin before the else arm's free was compiled, so the free marked nothing; the use
trapped at run time (exit 7). The same for an `if` with no else, `else if`, `match`, an arm that continues or
breaks in a loop, and a `drop` after the `if`.

**The rule** (`originLive`, `originMove`, `originFree`, `liveClear`, `liveJoin`, `deadSave`, `deadSet`,
`armBlock`, `ifStat`, both matches and `loopBodyV` in `src/obo.obo`). Each `originSyms` entry has a live mark
(`originLive`): the owner while some path to the point has not moved it. It is set where the handle is made (a
`let` copy of a handle with an origin too) and cleared by a whole move of the owner (`originMove`, every way:
`var c = a`, passing it, `take`, `replace`, a field). `originFree` (an assignment over the owner, `drop`, the replay of a
defer's frees) marks a handle dead only while its live mark is set; a handle whose owner was moved on every path
to the free is not freed by it (the object belongs to the new owner). The mark never comes back: a refill of the
owner (`a = nb()` after the move) does not reset it, since the next free then frees the refill, not the handle's
object. The live marks are saved and restored with the dead marks (`deadSave` returns both, `deadSet` takes
both back) and merge as a union (a may-flag, like the dead marks): after an `if`, a `match` or an `else if`,
a handle is live if some arm that goes on to the code after it left it live, or, with no else (a `match` with no
else arm), if the path that runs no arm did (`liveJoin`). The union starts empty (`liveClear`), not from the marks
before the statement as the dead marks do, since a live mark only goes away. An arm that ends does not take part:
a return, and a break or a continue (whose paths reach the code after the loop, see below). Hence a move in an
arm that returns leaves the handle live for the paths that go on, and a free of the owner there marks it dead.
A loop ends with the live marks it began with (`dEntry`): the loop may run no time, and a mark only goes away along
a path, so the marks at the entry are those of one of the paths out.

`hOrigin` itself is still cleared flow-insensitively by a move. The free events and the loop move rule
(`loopOriginPend`: a move of the owner inside a loop that a handle from it was declared outside of, "cannot move
'a' inside a loop while handle 'h' made from it is in scope", or `movedLoop` if the loop never names it) test the live
mark, and so does the guard against a defer that uses the handle (`originFree`, "while a defer declared
earlier uses handle"). The loop rule used `hOrigin`, so after a move in an arm that returned (which cleared it for
the whole function) a move inside a loop went unseen, although the handle was live on the path that went on
(`if c { eat(a); return }  while ... { println(h.n); var d = a; a = nb() }`, and a zero-trip loop that moves and
refills followed by `a = nb()`; both compiled and exited 7). The remaining users of `hOrigin` stay on it, each
for a reason: (1) the expression's `hOrigin` (`x.hOrigin = s.hOrigin`, and `hOrig` in the designator) decides
whether a held place is keyed to the owner or is an identity, which every move and free hits. A cleared `hOrigin`
is the safe side there; keying a place to the owner on the paths that did not move it would let a free of the
owner in a callee pass unseen on the paths that did. (2) `originMove` re-marks a narrowed handle dead (`originNar`) when the move hits the handle's live mark, not its `hOrigin`
(it was the last user of `hOrigin` among the dead-mark consumers). With `hOrigin` a returning arm that moved the owner earlier made the
re-mark skip: `if c { eat(a); return }  if c2 { a = nb() }  if alive(h) { eat(a); h.n }` compiled and exited 7, while the
same without the earlier arm was refused; and `if alive(h) { c = a; a = nb(3); h.n }` was refused only without the earlier arm.
Both now behave alike: refused. The second is a false positive that was already there (the object lives on in `c`; the
re-mark cannot tell a move into a variable from a move into a callee that frees it). The held-place decision (1) stays on
`hOrigin`: keying a place to the owner is only sound where no path has cleared the origin, and a cleared `hOrigin` merely falls
back to the identity treatment, which every move or free hits. (3) The `let` copy takes `x.hOrigin` for the same identity treatment, but is tracked (`originSyms`,
`hDepth`, the dead and live marks of the handle it copies) whenever the copied handle is live, so a copy made after
a move in an arm that returned (`if c { eat(a); return }  let g = h  a = nb()  g.n`) is refused like the handle
itself; the copy has no origin, which is an identity.

**Why it only tightens.** A dead mark is added exactly where the old rule added one, and in more places: a
free that the old rule skipped because a textually earlier move (on another path, in an arm that ended, in an
earlier iteration) had cleared the origin. The live mark is nil only when every path that reaches the point
moved the owner; there the old rule skipped the free too. No dead mark is removed.

**Documented behaviour kept.** `var c = a; h.n` is accepted (a move with no free; `c_asg_clears_origin` and the
neighbours), `eat(a); h.n` stays on the run-time check. A move on every path followed by an assignment over the
owner is accepted (`bothMove` in `tests/run/origin_paths.obx`); a move on one path only needs the owner refilled
on that path (a move on one path that does not refill is "moved on one path but not another"), and then an
assignment after the merge is refused, because it frees the object on the paths that did not move.

**Left.** The marks do not follow a condition: `if c { move+refill }  if c { a = nb() }  h.n` is refused although
the two ifs agree (see the false positives below). A move in a defer body is refused outright, so the defer needs
no live handling beyond the restore. Still on the run-time check: the owner moved away and back (`var c = a; a = nb();
a = c; a = nb(); h.n`: the live mark does not come back with the object), a move into a by-value callee followed by
assignments (`eat(a); a = nb(); a = nb(); h.n`, the documented callee-frees case), and a move in an `if` condition
that returns (`if mv(a) { return }  a = nb()  h.n`).

**Known false positives (path-correlated conditions).** The marks do not know that two conditions agree, so a free
that is safe because the same condition guards the move and the free is refused:
`if k == 1 { c = a; a = nb(2) }  println(h.n)  if k == 1 { a = nb(3) }  println(h.n)`: on the path where k is not 1
nothing is freed, and where it is 1 the second `a = nb(3)` frees the refill, but the first `if` leaves the handle live
(the other path did not move) and the second frees it. Likewise with a defer that uses the handle
and with a loop whose body repeats the condition. A generator found 8 of these in 400 seeds, all with `k == const`
conditions. Rewrite the program so that the move and the free are in one arm, or make the handle again after the
move. The old rule accepted them only because any move cleared the origin for good, which also missed the real cases
above.
A handle with no origin, a field handle and the other "Not covered" shapes above are unchanged.

**Tests.** Refused: `tests/errors/origin_paths.obx` (the then arm moves and returns and the else arm assigns
over; a hand-away (`eat(a)`); no else; the arm falls through after refilling; a `drop` after; else-if both
ways; a `match` with an else, with none, and with every arm but one moving; a loop with a continue arm, with a
break arm; a defer declared after a moving return arm; a copy made before one; a nested if). Accepted:
`tests/run/origin_paths.obx` (a plain move, a moving return arm, a freeing return arm, every arm moving in an
if, in a match and in an else-if chain, a move then two assignments, a use in the moving arm, a loop with a
continue arm and a break arm). Mutants: `PATH_MUTANTS` in `tests/mutate_fork.py` (also run by `mutate_port.py`).
All the programs in `tests/run`, `examples`, `bench` and the compiler itself compile the same with the old and the new
compiler. No fuzzer kind: the fuzzer makes handles from elements, which have no origin.

**Memory.** One more array (`originLive`) with one entry per tracked handle, and the saved marks are twice as
long, transient: see the port's peak in the commit message.

## Field and element origins (done)

**The gap.** `let h = handle(p.child); p.child = nb(); h.n` compiled and stopped at run time (exit 7): a handle
made from a field or element had no origin, because tracking a place needs a hook on every way the place can be
emptied. Since then the compiler has the per-path marks (originLive, originDead), `placeIn` and per-entry paths.

**Where a place origin is stored.** `Item.pOwner` and `Item.pPath` carry it from the `handle(...)` builtin to the
`let`. The handle then has one `originSyms` entry like a whole-owner handle, with the same `originLive` (the owner,
while some path has not moved it) and `originDead`, plus one new array, `originPath` (one string per entry): `""` for a
handle to the owner's whole object, otherwise the place as `designatorT` writes it (`/child`, `/kids/[2]`,
`/rows/[]/cols/[]`). `hOrigin` stays nil for it, so the held-place keying (identity treatment) is unchanged. A `let`
copy takes the live mark, dead mark and path of the handle it copies. Block ends truncate `originPath` with the
others. Made only when the designator has a path, does not go through a handle, and its root is an owned local
(`wholeOwnerOf`): not a reference parameter or local, not another handle, not a global (those keep no origin).

**What frees it** (`originFreeAt(r, path, strict, what)`, `placeHit`). An event at `path` of owner `r` marks dead each
live entry of `r` whose place the event is at or above (`segsPrefix(path, place)`; a variable index is `[]`, which
overlaps every element, in either direction). `strict` events free what lies below the place but not the place
(the place has more segments than the event): a `mut` call given the place, `truncate` of the array. Events:
- assignment over a field or element, `replace` (the old value is gone from the place whether or not it lives on:
  a false positive if kept), and `take` (the hook is kept for symmetry but cannot fire: a handle cannot be made through
  an optional, so no place lies under a taken one; no mutant);
- `truncate(a, n)`, strict; with a constant `n` an element with a constant index below `n` is kept (`truncKeep`), unless the
  array's place is unknown (a joined reference), where no index is known (review finding F2);
- the owner assigned over or dropped (path `""`, as before);
- a change through a reference: at `placeIn(r, i, path)` in each owned local of its binding. A place not known
  (`""` from a join, or a reference not pointed afresh in a loop) is strictly below the root, so it never frees a
  handle to the whole object;
- a `mut` argument to a callee with `mutFrees`: strict, at the binding's path of the argument (`""` = every place in
  the root, not the whole object).
A move of the owner clears the live mark (`originMove`) as for a whole owner; a move in a loop with the handle
outside is refused (`loopOriginPend`). Moving a field or element out is not possible (only `take` / `replace`).

**A free through a handle does not stop tracking.** The first version cleared the live mark of every place handle at a
change through a handle, a callee that writes through one, and a recursive call (a handle can name any object, so a
slot of a local owner may have been emptied unseen). The review showed this lost certain frees of unrelated owners
(`hq = handle(q); hq.other = nb(); p = np()` was accepted for a handle to `p.child`), and the argument for keeping the
mark is sound: a later free of the place frees whatever the slot holds then, and the only way that is not the handle's
object is that the object was moved out first and the slot refilled. That case (`let old = replace(g.child, nb())` through
a handle, then `p.child = nb()`) is now a known false positive (errors/field_origins.obx), as it was never a hole. No
event clears a live mark except a move of the owner. A write through a handle that has an origin (`let g = handle(p)`;
`g.child = nb()`, `replace(g.child, ..)`, `truncate(g.kids, 0)`) is a free of that place of `p` (`placeFreeVia`). A
write through a handle without an origin is not seen, and the use is checked at run time.

**What a `mut` callee frees (call summaries by place).** `FnRec.pfree` still says whether a callee may free below its
parameter i; new rows in the global arrays `pfFn`, `pfIdx`, `pfEv` say where: `"="+place` (the value in a place is freed:
assignment, `replace`, `take`) or `"!"+place` (what lies below the place is freed, not the place: `truncate`), the place
relative to the parameter, from `recordFreeChange` through the reference's binding (`placeIn`). `"!"` alone is anywhere
below. A callee that calls another with `mut` takes the callee's rows composed with the argument's place
(`callFrees`, `callFreeEvent`) into its own; an unknown place (a joined reference), a recursive callee, a forward heading, a
callee that writes through a handle (`freesAll`) and the 62nd parameter are `"!"`. A parameter has at most `PF_MAX` (4) rows,
then all become `"!"`. At a call with a `mut` argument that points at a place of an owned local, each row is an event at
the argument's place plus the row's (`originFreeAt`): `touchOther(p)` (`p.other = ..`) leaves a handle to `p.child` alone, and
`resetT(t)` (`t.par.child = ..`) one to `t.spare.child`. Rows are not read for anything else: `pureFn` and `mutFrees`
read only `FnRec.pfree` (set with the rows), and `userCalls` reads `pureFn`. Memory: rows exist only for callees that
free below a `mut` parameter (a few dozen in the compiler).

**Loops and defers.** Shared with owners: `originFreeAt` calls `pendPush` (a free of the place in a loop that
uses a handle declared outside is refused, "cannot assign over a place in the owner of handle 'h' inside a loop that uses
the handle"), `loopOriginPend` for a move, the defer guard (a free of the place while an earlier defer uses the handle),
and defer frees are recorded with their place and strictness (`deferFrees`, `deferFreeEv`, `dfEv`) and replayed
exactly (truncate's constant count is not kept in a defer replay: conservative).

**Message.** A use of a dead place handle: "handle 'h' may name a freed object: a place in 'p' was assigned over,
replaced, taken, truncated or reached by a call that frees below it, after the handle was made from it; make the handle
again".

**Memory.** One string array entry per tracked handle (`originPath`), two more `Item` fields (transient), the summary
rows, code. After the merge with "Move bits (done)", the port compiling itself peaks at 1,126,736 bytes live,
45,872 bytes (4.24%) above main's 1,080,864 before the merge (budget 2,000,000 unchanged, `MEMORY_MARGIN`
untouched). The exact count moves by about a hundred bytes with the argument paths (`port_compare` reported
1,126,720, 1,126,736 and 1,126,800 in different runs). The "before" figure this section first stated, 1,058,384,
predates the narrowed re-mark merge and is stale.

**Move bits (merged).** Main's move bits meet this work in three places. `movesFn(f)` is now `!pureFn(f)`, so `userCalls`
counts a call only when it might free; the place tracking does not read `userCalls`, and the rows do not add a second
path into moving: `mutFrees` and `pureFn` read `FnRec.pfree`, which `recordParamFree` sets next to the rows, so a
`mut` callee that frees below a place is moving exactly when the bit says so. A truncate of a `[]string` through a
handle now calls `recordFreeChange(a, true)`: main's string condition under this branch's `strict` argument, so the
function is `freesAll` (its calls are moving) and the event is below the array, like the string references it drops;
the `placeFreeIn`/`placeFreeVia` calls and `truncKeep` are unchanged by the merge: they were already strict. The
string-index pin (`indexHasCall`, `designatorT`) is orthogonal to origins: a string's byte designator names no
handle-able object (a handle needs a record or a closed type), and `root`, `deref`, `path`, `pOwner` and `pPath` are
still set after the designator loop.

**Not covered / left.**
- Handles made through a reference (parameter or local), through another handle, or from a global: no origin.
- A write through a handle without an origin (a handle to a place, a parameter) is not seen; the use is checked at run time.
- False positives (each refuses a program that is safe; the run-time check would have passed it):
  - `replace` of the place whose old value lives on, and the same through a handle (the object moved out, the slot refilled);
  - `truncate` with a count that is not constant (`len(xs) - 1`) frees every element handle of the array, and so does a
    `mut` callee that truncates (`cut1(mut ks)` is `"!"` below the array: the summary keeps no count);
  - a truncate in a defer replays without its constant count;
  - a reference made outside a loop and written inside it is anywhere in its root (the existing loop rule), so its writes
    free every place handle of the root;
  - a reference joined from two places is anywhere below the root; a third distinct place a callee frees is "anywhere";
  - an element or field changed on a path that is not taken (`if nflag() { p.kids[0] = .. }`) or under a condition that agrees with
    a move: the marks are path-insensitive, as for owners; a variable index frees every element handle of the array.
- A `var` handle, a handle in a record, and a handle parameter, as before.

**Tests.** `tests/errors/field_origins.obx` (59 refused shapes), `tests/run/field_origins.obx` (accepted neighbours:
sibling field, scalar, descendant, other constant element, other array, append, truncate that keeps, mut calls that write
a scalar or free below a sibling or below the place itself, references elsewhere, remade handle, alive guard, loops,
defers, handle through a held handle, callee writing through a handle, whole-object handle) and
`run/field_origins_param.obx`, `_global.obx`, `_handle.obx` (fallbacks that trap, exit 7).
`own_fuzz.py`: `field_origin_stmt` (valid), `field_origin_dead_stmt` (invalid); a dead handle from the local array that
the fuzzer's model expects to trap now may also be rejected ("either").
**Mutants:** `FIELD_MUTANTS` in `tests/mutate_fork.py` (also in `mutate_port.py`), 59 entries: 55 caught, four
equivalent (listed). The `FnRec.pfree` side is `FP_MUTANTS`: after the refactor into `recordParamFree` its three stale
patterns were re-anchored (`frees through a parameter are not recorded`, `a parameter is recorded by the wrong place`,
`a mut argument passed on is not recorded as freed`), the last now caught by `errors/fp_refs.obx` ("a mut argument
passed on: the caller inherits the free of the global it passed"), and the fourth, `the frees of a reference parameter
are recorded as a global`, is equivalent because `recordFree`'s `g.isParam && g.isRef` guard is unreachable after the
refactor (the reason is in `EQUIVALENT`).

## Pure calls with mut parameters (done)

**Rule.** `pureFn(f)` is now true when f is compiled, is not being compiled, has `freesAll` clear, frees no
global, and `mutFrees(f, i)` is false for every `mut` parameter i (`FnRec.pfree`: nothing freed below the
reference it got; `freesAll`/`writesHandles`, forward headings, recursion and the 62nd parameter on count as
freeing). Before, any `mut` parameter made f impure. `rd(h.xs, grow(h.xs))`, `two(&h.xs[0], bumpAll(h.xs))`
and a handle borrow held across `grow(ys)` are accepted when `grow` only appends and `bumpAll` only writes scalars.

**What each user of pureFn needed.**
- `afterCall`'s `invalidate(handleSym, ...)` and `callFn`'s "reference that borrows through a handle may not go
  to a call that might free": they protect against a free of any object a handle could name. A free below a
  `mut` argument, a free of a global, a handle write (`freesAll`) and unknown callees are all in `pureFn`, so
  these now use the precise rule. Passing a handle borrow to a callee that frees below any of its own `mut`
  arguments is refused as before (a handle can name any object; per-argument precision against the handle
  root was not attempted).
- `userCalls` (the index, held-array, `append`/`truncate`, `replace`, assignment-target checks): these guard an
  array address or index loaded before a call. `append` records no free (`recordFree` is called for `truncate`,
  not `append`) but reallocates the array, so a callee that appends through a `mut` parameter makes the loaded
  address stale (`h.xs[grow(h.xs)]`). Summaries have no "may move" bit, so these stay conservative: a new
  `movesFn(f)` was `!pureFn(f)` or any `mut` parameter, and only it incremented `userCalls`. Replaced by the rule in
  "Move bits (done)": the premise (append moves a loaded address) was wrong.

**Left.** Nothing about `append` (see "Move bits (done)": `userCalls` no longer counts a callee only because it has a
`mut` parameter). A handle borrow passed to a callee whose only freeing `mut` parameter receives an unrelated array is
still refused.

**Tests.** `tests/run/pure_mut.obx` (accepted shapes, refused by the seed); `tests/errors/pure_mut.obx`
(truncate, element replace, two calls deep, recursion, forward headings with and without a `mut`
parameter, handle writer, global free, the second `mut` parameter freeing, and the index/`append` cases that
stay refused). `FP_MUTANTS`: 7 new. `own_fuzz.py`: `handle_pure_mut_stmt` (valid) and
`handle_freeing_mut_stmt` (invalid).

**Memory.** The port peaks at 1,052,768 bytes live (budget 2,000,000, unchanged).

## Reference-local paths (done)

**Rule.** A change through a reference local is at the place the reference points at plus the path written,
not anywhere in the root. With `var r = &a.ys`: `r[0] = ...` is at `/ys/[0]` of `a`, `&a.xs[0]` and `&a.zs`
stay valid, `s = &r.zs` is `/ys/zs`, and a `mut` call given `r` frees below `/ys` only.

**Where the path is stored.** Nothing new is stored per reference. `RefBind` already had `paths` per root
(item 3 above). `designatorT` now sets `x.path` for every root that is not a handle (it used to skip
reference locals). `placeIn(r, i, xpath)` is the one place the rule lives: for the i-th entry of r's binding,
a non-deep entry (the root's whole object) gives `xpath`; a deep entry with a known path gives `path + xpath`;
a deep entry with path `""` (anywhere in the root) gives `""`, the whole root. It is used by `itemBind` (a
reference made from `r.f`), by `invalidateChange` (assignment, `replace`, `take`, `append`, `truncate`) and,
by the entry's own `paths[p]`, by the `mut`-argument invalidation in `callFn` (which used `a.path`, "" for a
reference local). Held places (`heldAt`, in `heldPush` and `freeEvent`) use the same place; a deep entry with
no known place is the new held place `anyPlace` ("*"), which `heldUnder` treats as under and over every
place in its root (before, `""` plus the path written was used, narrower than the truth).

**Merges.** `joinPath` now returns the same place or `""`: the two earlier shapes (the lower place of a nested
pair, as item "Path join" above argued for hits) are wrong for a change through the reference, since the place
plus the path written is not nested the same way (`/a` + `/c` against `/a/b` + `/c`). The common prefix is
unsound too (see above). `joinBind` keeps a whole-object entry and a deep entry of one root as two entries
(it used to OR the depth flags), since the whole object and a place inside it need different paths for the
same change. `coversPlace` has a strict form (same place, or anywhere) for `withinDeclared`; the loop check keeps
the old hit-coverage form, because the loop rule below makes the strict one unnecessary there.

**Loops (the soundness point).** The body of a loop is compiled once, with the place a reference had on entry.
A reference declared before the loop and not pointed afresh on every path to the use (`!r.fresh`) may be
elsewhere on the next iteration, so while `recording > 0` `placeIn` gives the whole root for it (a change
through it, a reference made from it) and `callFn` invalidates the whole root for it as a `mut` argument, and
`heldAt` gives `anyPlace`. A reference declared in the loop, or re-pointed before the use, has its place. Without
this, `while .. { r[0] = ..; r = &a.ys }` with `r` first `&a.xs` and a `keep = &a.ys[0]` read after was accepted
and read freed memory (found by thinking, covered by `errors/ref_local_paths.obx`). Walks of the form
`p = p.next` need nothing: the derived binding of a non-fresh `p` is anywhere.

**Copies in loops (found by review).** A copy of a non-fresh reference local (`let s = r`, `s = r`, an argument
binding) took `r`'s entry place, and `s` is fresh, so `s[0] = ...` was judged at `/xs` while the next iteration had `r`
at `/ys`. `itemBind` now gives such a copy (`!r.fresh && recording > 0`, not a deref) deep entries with path "" (anywhere),
which `placeIn`, `heldAt`, the argument overlap check and the `mut` invalidation all read. Tests: five cases in
`errors/ref_local_paths.obx`; mutant "a copy of a reference not pointed afresh in a loop keeps its place". The review's
`m4` (a held place under a non-fresh reference, a pure function replacing an element in the index) shows the mutant that
dropped `held == anyPlace` from `heldUnder` is not equivalent; it is caught now.

**Known false positive.** `let r = &xs` before a loop and `r[0] = replace(r[0], mk(i))` inside it is refused: the held
target and the free are both `anyPlace` (r is not fresh in the loop), and the store exemption does not apply to
`anyPlace`, since two unknown places may be an ancestor and a descendant. Point `r` inside the loop, or name `xs[0]`.

**A hole that predated this**: `r` joined from two places had path `""`, and a held place or a free under it was
recorded as the path written alone, so `a.ys[0].kids[f(replace(r[0], ..))]` with `r` either `&a.xs` or `&a.ys`
compiled (seed). Fixed by `anyPlace`; `errors/ref_local_paths.obx` has it.

**What is left at the whole root (and why).** A change through a handle or a reference borrowed through one
(`invalidateDeep`); a join of two different places in one root, including a whole object and a place inside it (a
set of paths per root would be needed; one string is kept for memory); a `for` walk's variable (`walkBind`, anywhere);
a non-fresh reference in a loop (above); another reference parameter or a global that a changed parameter reaches
(`reaches`, which ignores paths across roots); any call that may free the root (`afterCall`). A third field after
a join of two fields is therefore refused. A variable index is the element wildcard `[]`, which `segsPrefix`
already treats as overlapping every known element.

**Fuzzer.** `own_fuzz.py`: `ref_local_path_stmt` (valid: references to two elements or two fields, a change through
one, a read of the other, with a model); `elem_write_stmt` and `mut_call_dead_stmt` (invalid) now read a reference
into the place that was changed (they used a reference to another element, which is valid now).

**Tests.** `tests/run/ref_local_paths.obx` (20 accepted shapes), `tests/errors/ref_local_paths.obx` (overlap, prefix,
join on one path, loop re-point, derived references, variable index, handle, `mut` call, other parameter, global,
`truncate`/`replace`/`take`, defer, held places). **Mutants:** `REF_PATH_MUTANTS` in `tests/mutate_fork.py`, one per
part of the rule (also run by `mutate_port.py`); three are equivalent with reasons given there.

**Memory.** The port compiling itself peaks at 1,058,384 bytes live (1,052,768 before; budget 2,000,000 unchanged,
`MEMORY_MARGIN` unchanged): no new field, only code.

## Move bits (done)

**Rule.** `movesFn(f)`, the test that makes a call count in `userCalls`, is `!pureFn(f)`: a call counts only if the
callee might free (a global, anything through a handle, something below a `mut` argument by `FnRec.pfree`) or is
not known (forward heading, itself, the 63rd parameter on). No new bit is stored. The task asked for a per-parameter
"may move or reallocate" summary; looking for what a reallocation can invalidate showed there is nothing for it to
protect, so the summary would be a bit that is never consulted.

**Why append moves nothing a caller holds.** An array value is the address of a header (`Array`: length,
capacity, data pointer, element size), allocated apart from its data block. `rt_append` replaces the data block
and updates the header in place (`runtime.c`); `truncate` shrinks the header in place. For arrays the compiler never holds a
data pointer or a length across code that can run a call: an `elem` item keeps the *header* in a register, and
`resolve` reads the length (bounds check) and the data pointer from it when the access is made, after the index and
the right-hand side have run. A reference to an element is the element object (a record) or the inner array's
header, never the slot (`&xs[0]` of an `[]int` is refused), so it survives a reallocation (`LANGUAGE.md`: appending
does not invalidate references; checked at run time with `OBO_CHECK`, which poisons freed memory, in
`run/move_bits.obx`). What a `userCalls` check can lose is therefore the header itself, or the record that holds it,
and that happens only when something is freed: a free of the array, an assignment over it or over the field that holds
it, `replace`/`take`, a free of the owner, or a move of the owner (the last is `heldLost`, not `userCalls`). Every one
of these is already a free recorded in `pfree`, `freed` or `freesAll`, or an unknown callee. Truncating a scalar array
records a free too (only through a handle is a scalar truncate exempt), so it stays moving: conservative, since a
truncated array only fails its bounds check.

**Strings (found by review, fixed with a pin).** The paragraph above is true for arrays and `[]byte`. It is false for
the string-index path of `designatorT`: it loads the string pointer (`load(x)`), keeps it in a register across the
index, and reads the byte lazily afterwards. Strings are reference counted and are not `isOwned`, so assigning over a
string field or element (`b.s = ""`, the `dtype.kind == "string"` branch of `assignmentOrCall`) emits `RT_STR_DEC` and
records no free and no invalidation: such a callee has `pfree == 0` and is pure. With the old rule the mut parameter made
it moving by accident; with `movesFn = !pureFn` the string was read after being freed (`b.s[clrB(b)]` printed the poison
byte). The same read was already broken through a handle writer (`h.s[clrH(h)]`; `freesAll` does not count a string
assignment either) and through a global string. Fix, at the point that holds the pointer and not in the summaries:
when the tokens of the index have a `(` before its closing `]` (`indexHasCall`: it reads the tokens with the lexer's own
`next()` and counts bracket tokens, then puts the scanner back as fnDecl's second pass does: pos, line, col, last, sym,
sval, ival, tline, tcol, tokSeq; a first version scanned the text and was fooled by a `"` in a char literal or in a
comment, found by review; no textual heuristic is left; a parenthesis that is not a call only costs a pin) and the string is not a
literal, `RT_STR_INC` is taken on the loaded pointer before the index runs, and when the index has been checked the byte
is loaded at once (`load` on the byte item, through a copy of the pointer register) and the reference is given back with
`RT_STR_DEC`. An index with no call is compiled as before, so a string-heavy workload such as tokenising with `s[i]` or
`s[i + 1]` is untouched. Every route (mut, handle, global, reference local, element of a `[]string`, nested record)
is covered, with no summary bit. Other string consumers (concatenation, comparison, `substr`, arguments, `println`, `len`)
go through `pinStr`/`consume` and were checked with a callee that clears the string (`OBO_CHECK` poison): all read it
correctly. A truncate of a `[]string` through a handle now records a free like any other truncate (`recordFreeChange` when
the element type is a string): it decrements the strings, and the summary used to say it frees nothing. A consequence of
the pin: the byte of a string index with a call is a value in a register, not a lazy read-only memory item; nothing can
assign to it either way.

**What each `userCalls` check needed.** All of them hold a header or a record address loaded before the call, so all
use the one rule; none needed a different bit.
- Index across a call (`designatorT`: through a handle, a global, a field, element or reference). Header held. Changed:
  an appending or scalar-writing callee with a `mut` parameter is accepted.
- `append`/`truncate` with a held array (the value or count calls). Header held, `rt_append` reads it afterwards. Changed
  the same way.
- `replace` through a handle, field or element, and the assignment targets (`checkTargetAcrossCall`, compound
  assignment): the record or array the target is in is held. Changed the same way. The old text of
  `errors/assignments.obx` used a `mut` callee that freed nothing; its callee now assigns over the field.
- The retry saves (`sUser = userCalls` and the restore before the second pass): unchanged. The count is rebuilt by the
  second pass, which sees the same summaries. Nothing to OR: `pfree` and `freed` are only appended to or kept
  (`truncate(freed, sFreed)` drops only what the abandoned pass added, as before). The second pass records every free
  again, so the bits are the same either way; `errors/move_bits.obx` has a retried callee that assigns over the
  field.

**Argument identity (not done).** An index into `xs` across a call that frees below an unrelated `ys` is still
refused: the free is a single bit per parameter, and the counter says only "a call that might free happened". A rule
that compared the held root with the roots of the freeing `mut` arguments needs the held roots at the call, an alias
test between a held reference parameter and the argument (`reaches`), and a separate answer for globals, handles and
unknown callees; it was not built. Whole locals are not affected (their place is fixed, `wholeLocal`).

**What is left conservative.** A callee that truncates a scalar array through a `mut` parameter; a callee that
replaces an element (`a[0] = mk(2)`, a free of the old element only: the header is held, not the element); a callee
that frees below a different argument than the held one; a recursive callee's call in its own body; a forward heading;
the 63rd and later parameters; handle writers.

**Tests.** `tests/run/move_bits.obx`: accepted shapes (local, other array, field, handle, mut parameter, global, a
reference held across an appending call), each with a callee that appends 20 elements so the data block moves.
`tests/errors/move_bits.obx`: the neighbours (append plus truncate, two calls deep, recursion, forward heading,
truncate, assignment over, replace, take, an append holding the array across a replace, a handle-rooted array,
a global array, a reference held across a truncate or an element assignment, the Phase 2 retry callee, a handle
writer, the 63rd parameter). Cases that depended on the old rule were changed: `errors/assignments.obx`,
`errors/call_targets.obx` and `errors/pure_mut.obx` use a callee that really frees; `own_fuzz.py`'s
`handle_index_call_stmt` (invalid) uses `rebox` and `move_bits_stmt` (valid, with a model) is new. LANGUAGE.md section 12 has the rule
and its example now assigns over the field.
**Mutants:** `MOVE_MUTANTS` in `tests/mutate_fork.py` (also run by `mutate_port.py`), eleven keys. The last six are
the `[]string` truncate through a handle and the five string-pin/look-ahead mutants (`tests/run/string_index_pin.obx`
is their test, `own_fuzz.py` `str_index_pin_stmt` its fuzz kind); the first five are the moving rules (none, every,
or any `mut` parameter counting as moving), the 63rd parameter, and truncate recording no free. The old mutant "a
call with a mut parameter that appends does not count as moving" is gone: it is now the rule.

**Memory.** No field, no bit: only code removed. On main after move-bits, `port_compare` measures the port compiling
itself at 1,080,864 bytes live (the branch's own run reported 1,080,960); the later field-origins work raised it to
1,126,736, and the exact count moves by about a hundred bytes with the argument paths (see "Field and element
origins (done)").

## Test harness (done)

**Fail-fast.** `run_fork.py -x` stops after the first failing test: a suite with one failing test fails, so a
mutant's verdict is caught on its first failure and the rest of the suite is not paid for. The failing test is still
reported, the summary counts the tests that did not run ("N passed, M failed, K not run"), and the exit is 1 on any
failure (0 otherwise). With `-j` the failure may be reported while other cases are already running: those finish and
are counted, and nothing further is scheduled.

**Smoke subset.** `--smoke` reorders 30 tests (`SMOKE`) to the front of the same process and then runs every other
test as usual. It is a strict subset of the suite and only a reorder, so a failure there is a failure of the full
suite and a pass still runs all the rest; a miss still runs the full suite, so "missed" always means the full suite
passed. The 30 are the tests a real fail-fast run of the 326 mutants failed at first, weighted by how much of the
suite a failure there lets a mutant skip: 138 of the 311 caught mutants failed there first (44%), and the cold
suite cost 186 s CPU (17.4 s wall) against 234 s (55.5 s wall) with no subset. A name that is no longer a test is
reported and ignored, so a renamed case can cost time, never a verdict.

**Parallel cases.** `-j/--jobs` (default 1) runs that many independent cases at once in `run_fork.py` and
`port_test.py`; at 1 the report is unchanged. Every case has a name of its own (the output counter is locked) and
`run_exe` gives each its own scratch directory (`mkdtemp`), so no two cases share a file; `port_test.py` writes its
scanner fuzz inputs and encoder scripts before the pool starts, and its objdump cross-checks stay serial because
they share a temporary work list. In the mutation runs the outer and inner counts are one concurrency budget, not
two: they multiply to about the core count.

**Mutation defaults.** `mutate_fork.py` runs one mutant per core with one test inside each (`MUTATE_JOBS` defaults
to the core count, `MUTATE_INNER_JOBS` to the cores divided by it, so 16/1 on this host), measured fastest: 16/1 took
21.9 s wall and 203 s CPU, against 27.2 s for 8/2 and 50.7 s for 4/4. The suite is mostly short-lived compiler
processes, so overlapping tests inside a mutant only adds spawn contention. `mutate_port.py` runs 4 mutants with the
core count divided by 4 comparison workers each (4 x 4 on this host), measured fastest on a 28-mutant sample: 4 x 4
took 41.9 s wall and 166 s CPU against 95.2 s and 309 s for the old 4 x 16 fan-out, 2.3x faster in wall time and
1.9x in CPU. Both scripts reject a job count that is not a positive integer before a mutant starts.

**Measured.** CPU is the fair comparison; the wall numbers came from a loaded host. `mutate_fork.py` now takes
about 17.5 to 19.5 s wall and 174 to 230 s CPU, against about 170 s wall and 574 s CPU before the harness.
`mutate_port.py` takes about 432 to 486 s wall and 1,925 to 2,107 s CPU, against about 1,652 s wall and 5,596 s
CPU. Every verdict was identical name-for-name to the old harness in all modes tested.

**The verdict cache (not shipped).** A cache of mutant verdicts, keyed by the mutated source and by a digest of the
shared inputs taken at startup, was built and then removed before merge: a test file changed in the middle of a run
and reverted before the run ended is invisible to both the startup digest and a save-time re-digest, because the
file is already back, so a missed mutant could be stored and later reported as caught. A sound cache needs a
snapshot or lock of the inputs for the whole run; that is future work. No cache is shipped.

## Result and try (done)

**Rule.** `Result[T]` is a built-in parameterized type: one owned cell with two variants, `ok` (field `value: T`)
and `err` (fields `message: string` at offset 0, `code: int` at 8). `ok(v)`/`err(message[, code])` build a cell;
`match` inspects it, non-consumingly or with the consuming `ok(v)`/`err(m, c)` bindings; `try e` propagates an
error out of a function whose result is a `Result`, unwrapping an ok payload. `err(...)` is typed `errval` and is
assignable to any `Result[T]`; `try` is allowed only in a tail position (a `let`/`var` initializer, an assignment
right-hand side, or a statement) and not in `main` or a defer. There is no `Result[void]`: a success with no
value uses `Result[bool]`, `Result[int]`, or a typed optional payload.

**Shape.** `Type` gained `resultOf: ?^Type`; `derived("result", base)` caches the `Result[T]` in `base.resultOf`,
exactly as `arrayOf`/`optOf`/`handleOf` do. `derivedResult` makes the per-`T` `ok` record (field `value` at 0,
descriptor `descriptor(8, ptrs, strs)` with `ptrs = [0]` iff `isOwned(T)` and `strs = [0]` iff `T` is a string)
and appends the one shared `err` variant (`errVariantType`, descriptor `descriptor(16, [], [0])`). The ok
descriptor is the tag of an ok cell, so every mention of `Result[int]` must be the same `Type` object: the
`derived` cache is the invariant, and `same()` stays pointer-only for `result` rather than comparing bases.
`Result` is invariant: `Result[byte]` is not a `Result[int]`. The shared err descriptor is what makes
`T != U` propagation sound: an err cell built for one success type has the same tag and layout as one for any
other, and every free and match reads the cell's own header descriptor. `isOwned` adds `result` and `errval`;
`nonNull` and `isRef` add `result`; `assignable` accepts `errval` for a `Result` (and, through the optional
path, for `?Result[T]`). `errval` is a singleton type and is not a nameable type: `parseType` cannot write it,
and inference for a bare `let e = err(...)` (local or global) is rejected.

**Surface.** `Result` is the universe's type constructor `Result[T]` (`Result` alone says "Result needs a type");
`^Result[T]` is refused by the handle check and `&Result[T]` is allowed because it is owned. `ok`/`err` are
ordinary builtins and can be shadowed. `try` is the only new keyword: token 66 (after the punctuation, so the
existing 5 to 65 token numbers do not move), `makeTokNames`/`makeKeywords` gain it, `keywordTok` special-cases it
because its place in the keyword list is not its token number, and `semiAfter` does not include it (a newline
after `try` does not end the statement). `ok` rejects a void expression, `nil`, a borrowed value and `errval`;
`err` takes a string message (consumed, so a heap message is pinned before the code is parsed) and an int code
(default 0). `callExempt` and `plausible` name `ok`, `err` and `try`, so the pre-scan does not count a constructor
as a user call and does not guess a Result local as an int; `homeFor` only takes a declared int/bool anyway, so a
wrong guess there can only waste ranking weight.

**Match.** `matchStat` accepts kind `result`. The non-consuming form narrows the scrutinee like a closed type:
`r.value` in the ok arm, `r.message`/`r.code` in the err arm; both arms are required and `else` is a hard error
("a Result match names the ok and err arms; 'else' is not needed"). Strings and scalars can be copied out,
optionals can be `take`n, owned records/arrays cannot be moved out. The consuming form is detected by one
scanner lookahead before the first tag compare (first variant name followed by `(`, saved and restored exactly as
`indexHasCall` does); `consume(scrutinee)` runs BEFORE `matchStat`'s `s0 = snapshot()` (pre-consume, every arm's
`restore(s0)` would reset `moved` and the arm's shell free plus scope exit would double-free), after copying the
cell pointer into an unnamed slot. The tag loads come from that slot, not from the moved symbol. Each arm's
prologue (field extraction, field zeroing, shell free) is emitted only after that arm's tag compare, and the
payload locals are declared after the arm's `openBlock`, so the arm's own exit (or `runDefers` on a return,
break or continue) frees them; the err message is an ordinary `isStr` local that owns the cell's transferred
reference, and the ok payload is added as an owned/string local per `T`. The arm replicates `block`'s
`pendBlockEnds` and `armBlock`'s dead-mark/`exitDead` bookkeeping (factored as `armTail`). Both arms must bind or
neither may.

**`try` semantics.** Preconditions: a tail position (`trySite > 0 && tryNest == 0`); not in a defer; the
enclosing function returns a `Result` (`main` returns nothing, and there is no function in a global initializer);
the operand is a `Result` (an optional Result gets the nil-check hint, `errval` gets "no success type"); and the
operand is moveable. The operand is consumed with the existing `consume`, so a whole local or parameter moves, a
field/element/global/reference is refused, and the defer-use and `&&`/`||` checks apply. On ok: the 32-bit tag at
`[cell-16]` is compared with the shared err descriptor (a 32-bit load: an 8-byte one would include the
generation), the payload word is loaded, the field is zeroed, and the shell is freed with `RT_FREE`. On err: the
cell is parked in an unnamed slot outside `ownedLocals`, `runDefers(fnBlock)` runs the defers and frees the live
owned locals, and the slot is loaded into `RAX` before the epilogue. The payload item is a register, owned if `T`
is, with `strTemp` set for `T == string` (the shell's reference is transferred). Diagnostics are reported at the
`try` token; `consume`'s own errors stay at the operand.

**Error-path isolation (blocking point B3).** Every path mark is captured AFTER `consume(e)`: the post-consume
`snapshot()`, `deadSave()`, the `freeLog` length, the seven pending-array lengths (`pendSym`, `pendDepth`,
`frLine`, `frCol`, `pendWhat`, `pendOwn`, `pendNest`), `freeSeq`, and the owned locals' `iterating` flags (saved
and cleared, so the for-walk check does not fire on a returning path that abandons the walk). After the error
block those are restored or truncated; the ok path then continues with the operand moved and the ok payload in
its register. The pending diagnostic (`pendLine`/`pendCol`/`pendMsg`) is deliberately NOT restored: the error
path is a real exit, so a defer-use suspicion raised while its `callDefers` runs is real, and `notePending`
already keeps the earliest location. Everything else is unchanged or must persist and is not over-restored:
`originSyms`, `originPath`, `originNar`, `deferFrees`/`deferFreeEv` (safe because `try` in a defer is rejected,
so `inDefer` is false), `deferH`/`deferAt`, `dfBlock`/`dfIndex`/`dfOwner`/`dfEv`, the `held*` arrays (consume's
`heldMove` effect persists; the error block does not touch them), `userCalls`/`selfCalls`/`fwdCalls`/`farCalls`,
`depth`, `argTemps`/`argConsts`, `narrowStack`, `loops`, `blocks`, `pinned`, the function summaries, and
`freeArena` (outside a loop `logFree` appends nothing, and inside one the outermost `loopLogEnd` drops it). The
capture order is load-bearing: a pre-consume snapshot resets `moved` (double free at scope exit) and a
pre-consume `deadSave` resurrects an `originLive` mark that `originMove` cleared.

**Codegen.** The ok and err cells are `RT_ALLOC_RECORD` allocations whose size comes from the descriptor (both
round to one 32-byte block and are zeroed on allocation); payload/message stores use the `newExpr` field-store
pattern (no `store()`, which would release the cell register). `try` emits: park the cell; capture the marks; load
and compare the tag; on ok load the payload, zero the field, `RT_FREE` the shell and jump over; on err
`runDefers(fnBlock)`, load the slot into `RAX`, `leave; ret`. The payload register stays allocated across the
error block, so the pool cannot reuse it. A string success sets `strTemp`; a discarded `try f()` string statement
drops the reference. The ok/err constructors and the match use the existing descriptor/tag machinery; no runtime
routine or layout change was needed (`runtime.c` is untouched). Function recompilation after a register-pressure
retry reuses the cached types and descriptors; rodata from an abandoned pass stays referenced by the cache.

**I/O (Phase 2, not implemented here).** `openFile`, `readFd`, `writeFd` and `closeFile` remain Phase 2. When
they are added: pin or materialise earlier arguments before parsing a later one that can run user code
(`pinStr(path)` before the mode, `asReg(fd)` before `max`/`data`, like `writeFile` does); `rtCall`/`argReg` place
at most four arguments in registers, so a helper takes at most four (`openFile(path, mode, descErr, descOk)` etc.)
and a fifth needs a descriptor block or an extended calling convention; the Result layout becomes a runtime
contract (payload size, field offsets, descriptor shape) and must be recorded next to the object-header comment
in `runtime.c`; a helper that allocates more than once while holding earlier results must root them in
`tmp_root[0]`/`tmp_root[1]`, even though `rt_collect` currently returns; a pinned string argument's temporary
reference is released by `rtCall` after the call, so a helper must not retain the path/mode/data pointer (a
message that names a path copies it); `readFd` must reject `max <= 0` (`err(..., EINVAL)`, EINVAL=22), retry
`EINTR`, free a partially built array on failure, treat 0 bytes as EOF (`ok(nil)` after freeing the empty array),
and truncate `len` to a short read (a byte array owns no elements); `writeFd` retries `EINTR` and reports a short
positive write as `ok(count)` (the caller loops) and never frees the borrowed data; `openFile` maps `r`/`w`/`a`
(and later `r+`/`w+`) and reports errno; `closeFile` treats `EINTR` as success (Linux has already closed the
descriptor) and returns `ok(true)` or `err(..., errno)`. Allocation failure stays the runtime's fatal exit 2.
`readFile` keeps returning `?[]byte`; the two idioms mean different things (absence versus failure with a
reason), and converting it would be a breaking change for every test, example and the compiler.

**Tests.** `tests/run/result.obx` (62 expected output lines) covers construction and both match forms, strings
(copied, tried with a transferred heap reference, consumed, discarded), owned, array, optional and nested
payloads, a propagation chain, `T != U`, loops and for-ranges, defers and their order, the for-walk suppression, loop free-log
isolation, a defer replay in a loop (a handle used after the `try`), the pending arrays restored after the error
block (a defer inside an outer loop whose normal replay is outside an inner loop, replayed on the compiler's
error path inside that inner loop, which uses the handle), a field assignment whose value is a pure `try` call,
a record field, `[]Result` with `truncate`, `?Result` with `take` and at scope exit, `replace`, a record-held
error returned as a different
`U`, a global result, match through `&Result`, recursion, `err` rebuilt in an err arm, `try ok(...)`, a moved
result reassigned, the consuming match with terminating arms, a bare `ok`/`err` statement, and the
consume-before-snapshot ordering. `tests/errors/result.obx` has 52 exact-line cases: every precondition and
position-rule message, the constructor and type errors, invariance, identity comparison, missing/duplicate/`else`
arms, the mixed binding forms, and a defer use invalidated by the change the `try` operand makes.
`own_fuzz.py` gains the `result` profile (50 seeds in the suite): six helper shapes called with random
arguments, with the output and both success/failure paths modelled in Python.

**Mutants.** `RESULT_MUTANTS` in `tests/mutate_fork.py` (the Result mutants run only there; `mutate_port.py` does
not import them), 33 keys: the tag compare,
the 8-byte tag load, returning the success value or the cell on error, no shell free, no payload zeroing, no
consume, a pre-consume snapshot, skipped defers, skipped `freeOwned`, kept free log, kept for-walk marks, no state
restore, no dead-mark restore, a descriptor per err site, the ok descriptor's owned and string lists (both ways),
the err descriptor's string list and swapped offsets, no consume in `ok`/`err`, `errval` not owned, no `strTemp`,
the consuming match's consume placement / shell free / message zeroing / message ownership, missing and duplicate
arms, `else` on a Result, and the function-result check. All 33 are caught by the suite; the pending-array
truncation is caught by `pendErrCase` in `tests/run/result.obx`: with the truncation removed, the error path's
`callDefers` pushes an entry inside the inner loop, and the pending check where that loop ends refuses a valid
program (the defer's own replay, outside the inner loop, pushes no entry at that depth). The suite is 899 tests
(849 before the fuzz profile): 796 existing plus 52 error cases, one run program and 50 result fuzz seeds.

**Memory.** `port_compare.py` measures the port compiling itself at 1,229,344 bytes live on main (1,229,408 on the
branch; the figure is path-dependent), against a budget of 2,000,000 and 1,126,736 on main just before this
branch (+102,608: the new `Type` field is 8 bytes per type, the
descriptors are a few words per result type, and the rest is code). Per-seed and per-file counts are unchanged
elsewhere.

**Seed and round 2.** The compiler source does not use the new feature in this branch (its own local variables named
`ok` are ordinary variables that shadow the builtin, as before), so it is still built by the current seed; `make promote` is not part of the branch. Round 2, after the seed is promoted:
`compileFile` returns `Result[bool]`, its four `exit(2)` sites become `return err("obxc: ...", 2)`, and `main`
matches the result and keeps exit code 2, with `readFile`/`writeFile`, `--tokens` and the encoder untouched.

**Decisions.** Consuming match is part of the feature (phase 1), not an add-on. The error payload is inline
(`message`, `code`) rather than a named `Error` record. `err(...)` is `errval` for now, so an error value has
exactly one use (a Result destination) and a bare `let e = err(...)` needs a declared type. `try` is tail-only;
the restriction is load-bearing for the leak argument. `try` is not allowed in `main`: a helper plus `match` is
the top-level pattern, and `exit(1)` is used rather than `exit(r.code)` because errno 2–7 collides with the
runtime's trap namespace. `Result` comparison is identity `==`/`!=` only. `ok`/`err` stay shadowable and `try` is
a reserved word. There is no `Result[void]`; errno lives in `code` for the Phase 2 I/O API; `readFile` keeps
`?[]byte`.

## Incremental I/O (done)

**Rule.** An open file is an `int` descriptor with an explicit `closeFile(fd)`; `defer closeFile(fd)` is
the cleanup pattern. There is no `File` type and the language does not track fd ownership. `Result[T]`
(phase 1) carries every failure; the runtime builds the I/O cells itself, from descriptors the compiler
passes. Buffering lives in obo (`obo/lib/io.obo`), not in the builtins: a `Reader`/`Writer` owns a
`[]byte` and borrows its fd, and a tool flushes it explicitly.

**Resource model.** `openFile(path, mode)` returns `Result[int]`. The modes are exactly `"r"`, `"w"`
(`O_WRONLY|O_CREAT|O_TRUNC`, 0644) and `"a"` (`O_WRONLY|O_CREAT|O_APPEND`, 0644); a constant other than
those three is a compile error, and a computed one is `err("unknown open mode", EINVAL)`. A path of 4096
bytes or more is `ENAMETOOLONG` (36) before conversion; an empty path or an embedded NUL is `EINVAL` (22,
from `cpath`). `openFile` retries `EINTR` internally (a FIFO or device open can be interrupted). After a
successful `open` it marks the number open in the fd table when it is below 1024. The returned fd may be
0/1/2 if the standard descriptor was closed before the program started, so the runtime tests `fd < 0`, not
`fd <= 0`.

**Builtins.** `readFd(fd, max)` is one read: `max <= 0` is `err(..., EINVAL)` before the max-sized
allocation; a
negative fd is `err(..., EBADF)` before the allocation; a tracked closed fd is exit 9 under `OBO_CHECK`,
otherwise `err(..., EBADF)`, with no syscall; then `rt_alloc_array(max, 4)` and one read (retrying
`EINTR`). A short read is data: the array's `len` is truncated to the count. `got == 0` is EOF, and the
result is `ok(nil)` after freeing the empty array; an empty non-nil array never means EOF. `writeFd(fd,
data)` borrows the array, checks the tracked state first (so an empty write to a closed fd is the fd
error, never `ok(0)`), makes one write (retrying `EINTR`), returns `ok(n)` for a short positive count and
`err(..., EIO)` for 0 with data. `closeFile(fd)` classifies before the kernel: a table-state-0 0/1/2 is
refused with `err("cannot close standard stream fd N", EINVAL)` and is not touched; a tracked closed fd is
exit 9 under `OBO_CHECK` or `err(EBADF)`; a tracked open fd is closed and the table marks it closed on any
attempt, even a failed one (the kernel may already have released it, and a retry would be a double close);
any other number goes to the kernel and stays untracked, so a second close is the kernel's EBADF, never
exit 9. `close` treats `EINTR` as success (Linux has closed the descriptor). `stdin()`, `stdout()` and
`stderr()` are zero-argument builtins returning 0, 1 and 2; they are ordinary identifiers in `universe`
and may be shadowed.

**Runtime contract (descriptors, cells, tmp_root).** The compiler passes the shared err descriptor
(`errVariantType().desc`, `[16, 0, 1, 0]`: message at +0, code at +8) and the cached ok variant
`derived("result", payload).variants[0].desc` as the last two arguments of each helper
(`rt_open_file(path, mode, descErr, descOk)` etc.). `rt_alloc_record` reads `desc[0]`, so a scalar ok cell
is `[8, 0, 0]` and an owned one `[8, 1, 0, 0]`. The header's low 32 bits are the match tag, and `match`
compares against the cached variants: a fresh `okVariantOf(payload)` descriptor would miss both arms (and
`try` would not reveal it, because it compares only the err descriptor). Messages are proper kind-3 obo
strings, one allocation each, copying the path bytes or the decimal fd; no cell retains a caller pointer.
The helpers follow the runtime's two-allocation rule: the message is rooted in `tmp_root[0]` while the
err cell is allocated, and the readFd array while the ok cell is allocated, with the root cleared after
the stores. Allocation failure remains the fatal exit 2. `readFile`/`writeFile` keep their raw syscalls
and never enter the fd table, so the compiler's own file use cannot report phantom leaks.

**Argument order.** Every I/O argument is parsed with `nestedExpr()`, so a `try` in an argument is refused
by position ("cannot be part of a larger expression"): a `try` error return would otherwise bypass the
release of a pinned string temporary. `openFile` pins the path with `pinStr` before the mode runs: in
`openFile(b.name, g())`, `g` may assign over `b.name` and free the bytes the runtime would otherwise
convert; the pin's reference is dropped by `rtCall` after the call. `readFd`/`writeFd`/`closeFile`
materialise the fd before a later argument runs: in `readFd(gfd, f())`, `f` may change `gfd`; in
`readFd(b.fd, bump(b))`, the mut callee may write the field; in `writeFd(gfd, rows[setGfd(0)])`, the index
call may change the global. `writeFd`'s data is last and borrowed, so no hold is needed; `noTemp(data)`
refuses a transient call result (`writeFd(fd, makeBytes())`), which would have no owner during the call,
and runs before `materialise` as `writeFile` does. `rtCall` now refuses more than four arguments: `argReg`
maps index 4 and higher to RCX and would silently clobber argument 3; a fifth argument needs a descriptor
block or a stack convention.

**Buffered library.** `obo/lib/io.obo` defines `Reader` and `Writer` (an owned `[]byte`, a borrowed fd,
`pos`/`size`, and `hitEof` for the Reader). `fill` reads one chunk with `readFd(fd, size)` and treats only
`nil` as EOF; `newReader`/`newWriter` clamp only the maximum (1 MiB) and raise a non-positive size to 1.
`takeChunk` moves the unconsumed bytes out (`replace(r.buf, new []byte(0))`, plus one suffix copy when
`pos > 0`) because passing `r.buf` to a `mut` writer is refused by the mut-overlap rule; `pump` and
`count` stream through it. `readLine` returns `Result[?[]byte]`: nil is EOF at a line start, a non-nil
empty array is an empty line, `'\n'` is stripped and `'\r'` kept, and a line longer than the chunk is
returned whole. `readByte` returns `ok(-1)` at EOF; `readN` returns nil only if EOF came before any byte;
`eof` performs no I/O. `flush` loops on short counts, shifting the unwritten tail to the front, and keeps
the unwritten bytes on error so the caller may retry. `write`/`writeBytes` append a byte at a time and
flush at the threshold; a string argument is reference-counted by the call as usual. The tools then lived
in `obo/programs/` (now `obo/cmd/`; outside `port_compare`'s example glob) and were built by concatenating the library first;
`run_fork.py` applied the same rule to a `tests/run` file that starts with `// prelude: io`. (Both rules
were replaced by source modules; see "The command-line tools as source modules (done)".) `cat` uses
`fill` + `takeChunk` + a flush per chunk (so a pipe delivers each chunk at once), not `readN`, which would
block until the full count or EOF. `wc` counts newlines and bytes in one streaming pass.

**Standard streams.** 0/1/2 are plain ints, never owned and never tracked. `closeFile` refuses them at
table state 0, so the runtime's `out_fd` and the shell's descriptors cannot be closed through the API.
`writeFd(stdout(), ...)` and `readFd(stdin(), ...)` flush the runtime's 64 KiB text buffer first, so
`print("a"); writeFd(stdout(), b); println("c")` keeps its order and a prompt appears before a blocking
read. The Writer buffer is separate: a tool must call `flush`, and `exit()` flushes only the runtime
buffer.

**Leaks and errors.** The runtime keeps `fdstate[1024]` in `.bss` (0 never seen, 1 open via `openFile`, 2
closed). Under `OBO_CHECK=1`, a normal end with an open tracked fd prints `obo: file descriptors leaked: N`
and exits 8 before the object-leak check; a double close prints `obo: double close of file descriptor N`
and exits 9; a read/write on a tracked closed fd prints `obo: use of a closed file descriptor N` and exits
9. Without `OBO_CHECK` the same calls are `err(..., EBADF)` and do not reach the kernel for a tracked
closed fd. `exit()` bypasses `rt_finish`, so it runs no defers, frees no owned locals and runs no leak
checks. The `OBO_IO_FAULT=eintr|short` hook is test-only and inert unless `OBO_CHECK=1` (the environment
order is arbitrary, so the gate is applied after the scan); `eintr` injects one synthetic `-EINTR` before
the next read, write and close, and `short` caps each write at 3 bytes. A synthetic close-EINTR does not
close the kernel fd, so it tests only the ok(true)/table-closed semantics. `r.code` is the positive errno;
the trap codes overlap numerically with errnos, so a program must not `exit(r.code)`. A descriptor number
can be reused by the kernel after a close, so an `int` the program keeps past its `closeFile` may later
read or close a different file; the table tracks numbers, not owners, and cannot detect that.

**Tests.** `errors/io.obx` has 12 cases (the type/mode/count checks, the transient data, the no-argument
accessor, and the B4 try-in-an-argument case with the complete prelude `Box`/`b`/`getMode`/`makeBytes`).
`tests/run/io_basic.obx` covers the three modes, `"w"` truncation (a shorter rewrite leaves no stale
tail), ENOENT, EBADF for a read-only/write-only fd, an empty file, bytes 0..255, a 1 MiB file, a computed
max and reopening; `io_match.obx` matches every builtin in the consuming form (the ok-descriptor oracle:
a wrong tag misses the arm, and `try` would not see it); `io_pin_path.obx` checks the original file's
content after `g()` frees the path string; `io_pin_fd.obx` checks the global read, the indexed write and
the record field; `io_stdout_order.obx` checks text/byte ordering, the accessors and the stdout refusal;
`io_double_close`, `io_after_close`, `io_write_empty_closed`, `io_read_big_closed` and `io_leak` are the
exit-9/8 oracles; `io_defer.obx` is the `defer closeFile(fd)` regression (the block exits normally under
`OBO_CHECK=1`, which exits 5 for a leaked defer Result cell and 8 for an unclosed fd, then the program
reopens and reads the file). `io_reader.obx` and `io_writer.obx` (built with the library prelude, and
`port_compare.py` applies that rule too) cover chunk sizes 1..16, a 100 KiB line, empty versus nil, EOF
mid-line, readByte/readN/eof and flush thresholds. `tests/rt_io_test.c` links `runtime.c` directly with
its own `_entry`: it checks the cell layout/tags, descriptor-driven frees (no leak at `rt_finish`), EOF as
a nil payload, EINVAL/EBADF precedence (max, then fd, then the table, then the allocation), the N1/N2
orderings, exits 0/8/9 in one process per case, the EINTR close-state marking (and a failed non-EINTR
close still marking the table closed), `"w"` truncation, the unknown-mode error, and the gated hooks with
and without check. `tests/io_test.py` runs that harness and the controlled programs: cat streaming from a
pipe before EOF, a 100 KiB binary round trip, wc's counts, short reads, EINTR, short writes, the inert
hook, the no-check Result codes, the inherited-fd double close (Python passes an fd >= 3, the program
parses `args()[2]` with a decimal loop, and the case runs under `OBO_CHECK=1` so a mutant that marks the
untracked fd closed exits 9 instead of returning code 9), the stdout refusal, the four filesystem-free
path/mode errors (`tests/io/path_errors.obx`: empty and NUL paths EINVAL 22, a 4096-byte path
ENAMETOOLONG 36, and a computed unknown mode EINVAL 22 with its message), and the library boundaries.
The tool smoke cases are `obo/cmd/cat.obx` and `wc.obx` (at that stage they were in
`obo/programs/`; P2a moved them to `cmd/`).

**Mutants.** `IO_MUTANTS` in `tests/mutate_fork.py` (also imported by `mutate_port.py`: the port compiles
the I/O cases too, so its Stage B comparison catches each of them), 11 patterns, each matching once: no
path pin, no fd materialise in readFd and in writeFd, noTemp dropped, data consumed, a fresh ok variant,
swapped closeFile descriptors, `expression()` for the mode argument, a mode that is ignored, swapped
stdout/stderr, and max passed as the fd. All are caught by the run/error files above. `tests/mutate_io.py`
(new) judges 20 runtime and 7 library mutants by building a private runtime directory with the Makefile
flags (or a mutated `io.obo` text) and running the same program-level oracles: short read as EOF, a read
length of got+1, a read at data+9, double close undetected, a close that does nothing (caught by
`io_basic`'s close-then-return leak, not by `io_leak`, which leaks either way), fd 1 and data-pointer fds,
read/write EINTR, close EINTR as an error, swapped err message/code, the fd table not updated on open, the
N1 zero-length shortcut, N2 allocation before the checks, an unchecked negative fd, an EINTR close that
does not mark the table, `"w"` without `O_TRUNC`, an untracked close that does, closable standard
descriptors, the ungated hook, and the library's fill boundary (both forms), lost flush, a dropped flush
tail, a flush shift from the wrong offset, and takeChunk's prefix and suffix errors. The baseline must
pass every case first; 27/27 are caught.

**Fuzz.** `own_fuzz.py` gains `io_roundtrip` and `io_append`: a random payload (empty or up to 300 bytes,
with NUL and 0xFF forced), a random chunk size and read max; the generated program writes in chunks with a
short-count loop, reopens, reads back (and for `io_append`, reopens `"a"` and appends first), and prints
the length and a byte checksum that Python computes. 50 seeds of each run in `run_fork` under
`OBO_CHECK=1`.

**Memory.** `port_compare.py` measures the port compiling itself at 1,244,528 bytes live, against the
2,000,000 budget (1,229,344 on main, so +15,184: the seven universe symbols, the builtin branches and
the three Result-call helpers; the peak is the port compiling `src/obo.obo`, so no test text is in it).
The 1 KiB fd table lives in the runtime data segment of
every image, not in the obo heap: `runtime_data.bin` grew by exactly 1,024 bytes (66,272 to 67,296) and
`runtime_text.bin` by 4,303 (8,849 to 13,152) for the helpers. The compiler source does not use the
builtins, so the port's peak grows only with its code.

**Measured (follow-up round).** `io_test.py --rt --programs --tools`: 0 failures. `run_fork`: 1024
passed, 0 failed. `port_test`: 483 of 483 identical, no leaks (9 encoder groups checked against objdump).
`port_compare`: stage B 1025 of 1025, the memory check within the 2,000,000 budget. `homes_check`: every
check passes; `homes_edge`: 1028 statements, 119 refused by both, 0 differ. `doc_check`: 42 examples, 0
problems. `mutate_fork`: 409 of 429 caught, with the 20 equivalents (0 missed, 0 stale, 0 broken).
`mutate_io`: 27 of 27 caught. `mutate_port`: 377 of 377 caught. The fixed point holds (stage2 equals
stage3) and `seed/obo` is unchanged.

**Seed and round 2.** The seed already includes the I/O builtins (promoted on main at `c69b258`), so
`make promote` is not part of this follow-up. Round 2 (optional, recommended
against in phase 2) would move `compileFile` to the Result-returning builtins; `readFile`/`writeFile` are
one-shot whole-file operations, so changing them would churn the fixed point for no tool benefit. The
builtins are exercised by the tests and by phase 3.

**Decisions.** An int descriptor in v1; revisit a `File` type in phase 5 if `eza` makes the fd discipline
error-prone or a general destructor exists. The runtime builds the Result cells (one C place for the
layout) rather than returning raw counts and building cells in obo. Messages are copies, never retained
argument pointers. `readLine` returns bytes (`Result[?[]byte]`): optional strings are rejected by
`parseType`, and strings canonicalise empty to 0, so an empty line and EOF would be indistinguishable.
`readByte` uses `ok(-1)` because an optional byte is not a type. Only the maximum buffer size is clamped,
and `readN`'s count is the caller's. `closeFile` refuses 0/1/2 but follows the table for a number
`openFile` actually placed there. SIGPIPE keeps its default disposition (a write to a closed pipe kills
the process, like coreutils); it is not a Result. The fd table is 1024 bytes; fds at or above it are
untracked and the kernel still handles them.

## First command-line tools (done)

The first programs written for their own sake rather than to test the language:
`cat`, `wc` and `sort` in `obo/programs/` (now `obo/cmd/`), with a separate differential gate
(`obo/tests/tools.py`) against GNU coreutils. They use only the language, the
phase-2 runtime and `lib/io.obo`, so they change no compiler, runtime or seed
source and the two-round self-hosting rule does not apply. The implementation
base is main at `0db2ef4` (phase 1 merged, the phase-2 I/O seed promoted); phase
2's `openFile`/`readFd`/`writeFd`/`closeFile` and `lib/io.obo` are the
precondition, and the builder probes them. **Do not run `make promote` for this
work**: the tools are outside the compiler's fixed point, and the tools gate is
separate from `make promote`.

**Tool set and subset.** `cat [-nbsET]`, `wc [-lwc]`, `sort [-nru]`, with `--`,
`-` (stdin) and clustered/permuted options. Exit codes: 0 success, 1 for any
`cat`/`wc` error or usage, 2 for any `sort` error or usage. Deliberately absent:
cat `-v -e -t -A -u`, wc `-m -L --files0-from`, sort `-k -t -f -b -d -i -M -h
-V -s -c -C -o -m -z --parallel`, every long option, and `--help`/`--version`
(they are unrecognized long options, pinned by own goldens). `head` is a natural
next tool; `tail` needs `seek`/`readAt` or a bounded implementation. The user
subset, message forms and caveats are in `obo/cmd/README.md`.

**Layout and build (Phase 3; superseded by the tools-as-modules record).** There
were no modules yet, so `obo/tools/build_tools.py`
concatenated a generated header (relative source paths, no timestamp, so the
generated source is reproducible), `lib/str.obo`, `lib/io.obo` and the tool, in
that order. The rule was that a prelude may use only earlier preludes, and a
declaration may use only earlier declarations in its own file. The builder
resolves every path to an absolute path, checks the compiler, runtime `.bin`s and
sources, compiles (never runs) a well-typed phase-2 probe naming every I/O
builtin, and maps a compile error's generated line back to the source file and
line. It has no cache and no `--force`. `obo/Makefile` gains
`.PHONY: tools tools-check tools-memory all clean`; `tools` depends only on
`obo`, `runtime_text.bin` and `runtime_data.bin`, never on `lib/io.obo`, so the
builder's clear `phase 2 (incremental I/O) is a prerequisite` message is
reachable instead of `make`'s missing-target error. `.PHONY` is required because
`obo/tools/` is an existing directory (a non-phony `tools` target is silently
"up to date") and because the tool/library sources are deliberately not Make
prerequisites. `make clean` removes `build/`, which `.gitignore` now ignores.

**Library additions.** `lib/str.obo` is new and dependency-free:
`isSpace`, `isBlank`, `isPrint`, `isWordByte`, `cmpBytes`, `errText`, `digits`
and `appendPadded`. `lib/io.obo` gains one function, `writePadded`, appended at
the end (after `pump`/`count`, which stay after their callees). It is
self-contained (an inline digit-width loop, not `str.digits`) so the phase-2
tests and docs that compile `io.obo` alone still work; it is generic formatting,
used by wc. `tests/lib/write_padded.obx` exercises it over widths 0..8, a padded
zero, a value wider than the width, writer thresholds 1, 3, 8 and 65536, and
byte-compares the file; `io_test.py --programs` runs it. No other library
signature changed.

**The harness.** `tests/tools.py` is a separate gate, not in `run_fork.py` and
not in `make promote`. It builds the tools with the same builder into a private
temporary directory, then runs each case in its own directory with relative
operands, a clean environment (`PATH=/usr/bin:/bin`, `LC_ALL=C`, `TZ=UTC`,
per-case `HOME`/`TMPDIR`; no inherited `OBO_*`/`OBX_*`), stdin `DEVNULL` or a
pipe, stdout/stderr captured to capped files (`RLIMIT_FSIZE` 16 MiB), and
process-group timeouts (10 s, 60 s for slow cases). Our tool additionally runs
under `OBO_CHECK=1` and `OBX_GC=poison`, so a successful run also proves no
leaks or fds; runtime traps (`obo:`/`obx:` lines, exits 3-9) are tool defects.

The reference is GNU coreutils 9.4, resolved only through the pinned
`/usr/bin:/bin` and executed as `[tool, *args]` with `executable=<absolute
path>`: `argv[0]` is the bare tool name, so GNU prints the same `<tool>: `
prefix as ours (an absolute invocation would print `/usr/bin/cat: ...`). Every
reference process (version probing, cases, model-vs-GNU, `--record`) uses that
invocation; a defensive normalizer replaces a leading `<ref_path>: ` on *every*
stderr line and warns, and goldens are validated to contain no `/`-leading line.
The report prints each resolved path, kind and version line.

Each case has exactly one oracle tag, plus the orthogonal `portable` marker:

- `modeled` (the default): GNU >= 9 is the primary oracle, with stdout and exit
  exact, success stderr empty, and file-error stderr exact for quote-free
  operands. Without a usable reference, the Python model is the oracle. Frozen
  `gnu` goldens, where present, are also compared.
- `usage`: our messages only, pinned by hand-written own goldens; the reference
  is never invoked.
- `writeerror`: stdout is `/dev/full` (a sink). Our stdout/stderr/exit are
  pinned by own goldens; GNU's stdout (empty) and exit are exact and its stderr
  is shape-compared. Models are never consulted (they have no stdout destination
  and do not implement ENOSPC). `cat empty > /dev/full` is *modeled*, not a
  write error: no write is issued, GNU exits 0 with empty stderr, and the model
  returns `(0, b"", b"")`; `/dev/full` rejects even a zero-length write, so the
  case also pins that the Writer never issues an empty write. `wc` always prints
  a line, so its `/dev/full` case stays `writeerror`.

The `portable` marker selects the forced `--reference=busybox` subset (cat byte
copy and default `sort`, including default `dups`; no `-n`/`-r`/`-u` case is
marked, and wc has none in v1 because its padding is implementation-specific). `--reference=any` ignores the marker and shape-compares
stderr. Checked-in goldens are binary: `golden/gnu/<tool>/<case>.*` recorded with
`--record` from GNU 9.4 (modeled, golden-marked cases only; no 1 MiB outputs),
and `golden/own/<tool>/*` hand-written for the seven usage and six write-error
cases. `--record` applies the prefix normalizer, refuses a `/`-leading line, and
refuses to overwrite a differing golden without `--force`; a `VERSION` mismatch
is a note. `--check-models` (default on) compares every modeled case's model with
GNU on stdout/exit (and exact stderr) and exits 2 on disagreement, so the
fallback cannot rot. `--reference=none` never probes a reference; models are the
oracle for modeled cases and own goldens for usage/write-error.

The matrix is 191 cases (cat 70, wc 63, sort 58) plus eight serial checks:
cat streams a pipe before EOF (plain and `-n`), stdin is not read when it is not
an operand (all three tools), and three pipelines (`cat | wc -l`,
`sort | head -n 1`, `cat -n | sort -n`). 13 cases are portable-marked, 36 have
recorded GNU goldens and 13 have own goldens; 10 are slow (`--fast` skips them).
The case list, tags and markers print with `--list`.

**Per-tool semantics.** cat is a byte-stream transform over its operands with
state across files: `%6d\t` numbering from 1, `-b` overriding `-n` in either
order, `-s` emitting at most one consecutive blank line (squeezed lines are not
numbered), `-E` writing `$` only before emitted newlines, `-T` writing `^I` for
tabs. `-E` also follows GNU's CR rule: a CR immediately before an emitted
newline is held and written as `^M`, so CRLF renders `^M$`; the hold spans
Reader chunks, operands and read errors, and a final unpaired CR is written
literally at stream EOF (a second CR can become the new pending one). `-T` alone
leaves CR alone. A blank line is zero bytes; spaces, tabs, NUL and a CR make a
line nonblank (a CR-only line is numbered by `-n`/`-b` and never squeezed). An
open/read error prints, sets failed and continues; a write error prints once,
sets fatal, stops reading and never opens a later operand. The transformed
scratch is flushed at 64 KiB. wc uses GNU's C-locale word rule (whitespace ends
a word, `0x21..0x7E` starts or continues one, every other byte is ignored), a
fresh count per operand, the documented no-`stat` width, totals iff more than
one operand, and delayed output flushed before a failure exit. sort preflights
every named operand in order (the first unopenable one wins, so a missing file
after a directory is still `cannot read`), reads all lines per operand with
`readLine` (final partial line included, per-operand finalization), sorts an
index array with a stable bottom-up merge, compares `-n` keys as exact decimals
(no floating point; 40-digit numbers keep their order), applies `-r` to the
last-resort line comparison too, and deduplicates `-u` with a key-only
comparison so the first input occurrence survives. Only ASCII `0-9` are numeric
digits. GNU 9.4's `strnumcmp` additionally treats a leading byte `0x80` inside
the integer leading-zero run as a zero (`\x801` compares equal to `1`); that is
outside the documented ASCII numeric grammar and is deliberately not reproduced,
and high-byte numeric inputs stay out of the differential matrix. Nothing is
written before all input is read. The `-E` CR rule was found by the independent
implementation review after the first green run; the matrix now has CRLF,
cross-file, chunk-boundary, double-CR and final-CR cases with recorded GNU
goldens, and the model implements the same rule.

**Acceptance and gates.** `python3 tests/tools.py --require-reference` passes:
191 matrix cases plus the eight serial checks, 0 failed, with exact stdout/exit,
exact file-error stderr for GNU 9.4 (the bare-`argv[0]` rule), clean
model-vs-GNU, and clean goldens. `--fast` passes with 10 slow cases skipped;
`--memory` passes both pinned cases under 1 MiB live heap; `--reference=none`
passes with models/own goldens and no reference process. The compiler gates are
untouched and still pass: `run_fork`, `port_test`, `port_compare` (which
concatenates `lib/io.obo` for `// prelude: io`), `io_test --rt --programs
--tools`, `rt_io_test`, `mutate_io` (27/27; the added `writePadded` does not
alter its library patterns), `mutate_port`, `mutate_fork`, `doc_check`,
`homes_check`/`homes_edge`. `seed/obo` and `src/obo.obo`/`runtime` are unchanged;
no promotion is part of this work. Exact current numbers are in Measurements.

**Risks.** (1) wc's width without `stat`: unreadable regular files, readable
non-regular files and regular-file stdin diverge; they are documented and kept
out of padded differential cases, and the unreadable fixture is one byte with a
checked digit precondition. (2) The models could share a spec bug with the
tools; model-vs-GNU on every modeled case is a hard failure, and the goldens are
recorded from GNU. (3) The reference may be absent or different: classification
detects GNU old/BusyBox/unknown, exact stderr is GNU >= 9 only, shape elsewhere,
and `--require-reference` makes GNU >= 9 mandatory. (4) Locale leaks would move
sort order, wc words and strerror text; the environment is built from a clean
base with `LC_ALL=C`. (5) sort is in memory; huge inputs can exhaust it (exit 2),
documented. (6) GNU quotes exotic operands and ours print them verbatim; exact
stderr cases use quote-free names. (7) The `tools` Make target collides with the
existing `obo/tools/` directory; `.PHONY` is mandatory and verified. (8) The
defensive prefix normalizer is per line, so even a hypothetical bypass of the
invocation rule would still normalize every diagnostic line. (9) GNU 9.4's
`strnumcmp` treats a leading `0x80` inside the integer leading-zero run as a
zero digit (`\x801` equals `1`); that is outside the documented ASCII numeric
grammar, is deliberately not reproduced, and high-byte numeric inputs are kept
out of the differential matrix. The first green run missed GNU's `-E` CR rule;
the implementation review found it, and the matrix, model, goldens and docs now
pin it (CRLF, cross-file, chunk-boundary, double-CR and final-CR cases).

**Later tools.** `head` is implemented in `cmd/head.obx` as a streaming first
lines/bytes tool with GNU's multiple-operand headers (see `cmd/README.md`);
`tail` waits for `seek`/`readAt` or a documented bounded version; `eza`
needs `readDir`/`stat`/`isatty`, `fd` needs directory walking, and `jq` needs
parsing and associative containers. All use `obo/cmd/` and the `tests/tools.py`
spec table as the extension point.

**Decisions.** A separate gate, not part of `make promote`; the release
checklist runs `tests/tools.py --require-reference` then `tests/tools.py
--memory`. The reference always has a bare `argv[0]` (a normalizer is a safety
net, not the mechanism). Usage and write-error text is ours, pinned by own
goldens; GNU is never asked for it. The empty-input `/dev/full` case is modeled
so it gets model-vs-GNU coverage; the write-error shape rule stays strict for
cases that write. The `portable` marker is orthogonal to the oracle tag. The
tools keep their own semantics (cat's state machine, wc's width, sort's numeric
parser) and share only generic byte/formatting helpers. `LANGUAGE.md` gains no
tool/library examples: `doc_check.py` compiles every `// example:` block
standalone, so a block using `Reader` would fail without a prelude extension.
`writePadded` is self-contained rather than depending on `str.obo`, so every
phase-2 harness that compiles `lib/io.obo` alone keeps working.

**Measurements** (this host; GNU coreutils 9.4, Python 3.12.3). Build: three
tools including the probe in 0.03 s. Gate: 191 matrix cases + 8 serial checks
(199 checks), 0 failed (`--require-reference` and `--reference=none`), 0 failed
with `--fast` (10 skipped), 2 of 2 memory cases. Live heap peaks (`OBO_STATS=1`): plain `cat` on the 16 MiB
newline-free sparse input 230,352 bytes; `wc -lwc` on the same 66,384 bytes;
flag-heavy `cat -nET` on a 1 MiB all-newline file 295,936 bytes (the bounded
64 KiB scratch; the design's whole-chunk-batch shape measured 1,638,592, so the
bounded form is kept); `sort` on the 50,000-line/888,890-byte fixture 4,111,488
bytes in 0.06 s; `sort` on the 20,000-line/1,080,000-byte fixture 2,449,104
bytes in 0.05 s; `sort -n` on the 50,000-line fixture 8,634,208 bytes in 0.08 s
(numeric keys cost one record per line). Sort is O(input). Byte-copying `cat`
streams a pipe before EOF (the serial liveness check), so `fill` plus a flush
per chunk is in use, not `readN`.

## Multiple modules: Stage 1 (done)

Source-level modules, the first stage of the approved module design
(`/tmp/obo-module-design-v2.md`, §17 Stage 1). Stage 2 (symbol files,
`--check`, `--sym-dir`, `--emit-sym`, staleness) was not built at Stage 1; see
the Stage 2 record below. The compiler itself remains one file and uses no
imports.

**Surface.** `module Name` is the first token (comments and blank lines aside).
An imported module must have a header and its file must be `Name.obx`; the root
is exempt and a headerless root is `_root` (reserved, and included in the
duplicate-name table). `import "path.obx" [as alias]` precedes all declarations;
paths resolve against the importing file's directory, then each `-I` in order,
and absolute paths as written; the default alias is the declared module name.
`pub` (never `export`) marks a top-level `fn`, `type`, `const`, `var` or `let`
exported, and is sticky across a forward heading and its definition. Everything
else is private. Importers use `alias::name` for functions, types, variants,
constants, globals, `new`, `[]/?/^/Result` and `&`; `match` arms stay bare.
There are no unqualified, selective or wildcard imports and no re-exports.

**Module identity (B4).** Canonical identity is the lexically normalized
resolved path: repeated `/` collapse, `.` segments go, `..` pops a previous
segment (a leading `..` of a relative path stays), and a leading `/` stays.
Dedup, the cycle stack and duplicate-name checks are all keyed on it, so
`lib.obx` and `./lib.obx` are one module and one initialization. Symlinks are
not resolved (the runtime has no `readlink`/`realpath`); a symlinked copy is a
different path, so module trees use canonical paths, and if both spellings are
reached the duplicate-name check reports the same module name at two paths. `_root` cannot be declared
by a dependency and the root is in the same duplicate table.

**Compilation model.** `compileUnit` saves the scanner (the `ScanState` record:
source, position, line/column, last/current token, token text and value,
`tokSeq`, token position and `srcPath`) and the current `Module` and
`visibleGlobals`, starts the dependency's scanner, stores its `Module` under the
normalized path, parses its header and imports, compiles its declarations into
the shared code/rodata/gsize/string pool/descriptor area and returns; the
importer's scanner and context are restored. Imports are acyclic, so a
dependency is complete before its importer: addresses are final (no fixups
across a module), and effect summaries are final at every cross-module call.
`compileProgram` emits the one entry prologue (`RT_INIT`, then the init-chain
jump), compiles the root, requires the root's own `main`, fixes the chain, calls
`main`, frees the globals of every module in compile order (dependency-first,
each own global once, imported globals never by the importer), calls
`RT_FINISH`, and patches the global-end placeholder with the final `gsize`.
Dependencies initialize once (a diamond compiles/initializes the shared module
once); init order is depth-first left-to-right in import order.

**Globals and effects.** Three lists replace the single process-wide `globals`:
`curModule.globals` (the declaration scope; `lookup` and `declare` use it and
never search imports), `visibleGlobals` (the module's own globals plus its direct
imports' exports; `afterCall` invalidates references into these) and
`programGlobals` (every module's own globals in compile order, for teardown).
`waitFn`/`waitAt`, `fnDecl`'s forward-fixup cleanup, forward type/function
matching and the module-end completeness checks all use `curModule.globals`; an
import is at top level, so no wait entry spans a module boundary.

`afterCall` propagates **every** entry of the callee's `FnRec.freed` with
`recordFree` independently of `visibleGlobals`, then invalidates the visible
globals the callee may free (**B2**). The v1 design's "iterate visibleGlobals
and record each one the callee frees" loses transitive frees: in A (exports `g`,
`wipe`), B (`mid` calls `a::wipe`), C (`outer` calls `b::mid`), D (imports A and
C) a reference `&a::g` across `c::outer()` must be rejected even though C never
imports A, because C's summary must carry `A::g`. The independent loop is the
fix; the four-module case and the folded mutant are in the tests. `recordFree`
ignores `curFn == nil`, so a top-level initializer adds no function summary.

**Type identity (B7).** `Type.owner` is the defining module and `Type.uid` the
FNV-1a 64 of `owner::name`; `Type.name` stays the local name. The defining
declaration is the only place that names a type; an importer's `a::T` resolves
through the defining module's live export table, so one key is one `^Type` and
one descriptor in the whole image: allocate in A, free/match in B. Derived types
(`[]`, `?`, `^`, `Result`) stay canonical through the shared base object and its
caches. A type alias (`type A = lib::T`, `type P = Dot`, `type X = []T`) is a
symbol whose type is the existing object: it creates no `Type` and never writes
`owner`, `uid` or `name`. In Stage 1 the **live export table is the registry**:
an imported name resolves to the defining module's `^Sym` and `^Type`, so
`mightFree(f, g)` compares the same object for a private/transitive global too
and no separate keyed global stub is needed until `.sym` can name one before its
module is loaded (B6, Stage 2). Two aliases to one imported type, in one module or
several, still denote one object. Structurally identical records in two modules
are different types, and assignment between them is refused with both names
qualified.

**Exports and the private closure (B5).** `declare` sets a top-level symbol's
owner and adds it to `visibleGlobals`; `pub` sets `exported` (variants inherit
their closed type's flag). `findExport` scans a module's globals for an exported
name, so private declarations are unreachable even within the same source tree.
At module end `checkExports` walks every exported type, function parameter and
result, and global type through `[]`, `?`, `^`, `Result`, record fields and
**every closed variant's fields**; a private named type of the same module is
reported with the variant named. Imported types are not traversed (their own
module checked them), so an exported alias to a foreign type is accepted.

**Diagnostics.** `tname` qualifies a named type as `owner::name` when the owner
is not the current module; `qualifiedSymName` does the same for functions and
globals, and `describe`, `callFn`'s arity/argument messages, move/immutability
messages and the global target/index messages use it. Inside the defining
module names stay bare, so all single-file messages are unchanged. Names print
the declared module name, not the import alias; an alias may print its
underlying type. Reserved type names (`int`, `byte`, `bool`, `string`, `void`,
`nil`, `Result`), `const nil`, the qualified match-arm hint and the `main` rules
are implemented with the design's messages.

**Scanner and pre-scan.** `::` is token 67 after `try` at 66 (the Result work
landed first); the token-names string ends `... >> try ::`. `punct2` scans it,
and `scanFunction` treats a `::` while a `new` type name is pending as part of
the name (it sets `prevNew` again), so `new lib::Node{...}` still counts as a
record literal in the register-pressure estimate. The `homeRetry` fallback
remains the safety net; the mutant that removes the teaching is documented as
equivalent because a wrong estimate only changes homes, not semantics.

**`--deps`.** `obo --deps SOURCE [-I DIR]...` scans headers and import blocks
only, and prints normalized resolved paths in depth-first post-order
(dependencies first, root last, deduped). Missing files exit 2; cycles,
duplicate names, a missing imported header and the basename rule exit 1 with
the build's messages. `-o` is refused. It writes nothing.

**Tests.** `tests/module_test.py` adds 78 cases to `run_fork` (21 accepted run
trees, 49 rejected trees with exact normalized stderr, 6 `--deps` trees, the
`-o` rejection and a `--tokens` case). `port_compare` compiles every module tree
with both compilers and compares compile outcome, run output/exit and `--deps`
output; it also compares the `::` token dump, and uses a private tree directory
per case. `port_test` checks `T_TRY = 66`, `T_COLONCOLON = 67`, the names string
and the `::` dump directly, and adds an obo-local scanner fragment list
(`::`, `import`, `as`, `pub`) without touching `obx/tests/stage_test.FRAGMENTS`.
`doc_check` learns multi-file examples (`// example: modules` with `// file:`
lines); the LANGUAGE example and a private-access error example are checked.

**Mutants.** 38 Stage 1 module mutants in `MODULE_MUTANTS`, shared with
`mutate_port`: exports both ways, variant export, the closure (including B5),
reserved names, `const nil`, import placement, aliases, cycles, duplicate names,
basename, B4 normalization, B2's two loops, imported global identity, pmodes,
mut-callee precision, double compilation, programGlobals, scanner restore,
import binding, B7 alias mutation, `_root`, dependency `main`, `pub` placement,
path suffix, `-I`, global offsets, the token constant and name, and the
pre-scan. Measured: 37 caught, 1 documented equivalent (the pre-scan estimate);
`mutate_fork` and `mutate_port` both see them through the module cases (the
whole gate's final counts are below).

**Measurements** (this host). `src/obo.obo` grew from 8,018 to 8,756 lines.
The port compiling itself peaks at **1,364,240 bytes live** and allocates
28,136,432 in total (budget 2,000,000; the Phase 3 pre-module peak was 1,244,480,
so the module context and qualified-name work costs 119,760 bytes, 9.6%). The fixed point is
exact: `./obo src/obo.obo` and the stage-2 compiler's output are byte-identical.
`run_fork`: 1,102 passed, 0 failed. `port_compare`: 1,102 of 1,102 behave the
same. Against the old seed (`OBO_REF=seed/obo`, which skips the new module
trees), 1,024 of 1,025 single-file cases match: the one difference is
`obx/tests/errors/syntax.obx`'s `import foo`, whose old "imports are not
supported yet" is now "expected a module path after 'import'", because imports
exist. That is the intended surface change, not a single-file behaviour change;
everything else, including every obo program and error case, matches the seed. `port_test`: 486 of 486 identical, 9 encoder groups. `doc_check`: 44
examples, 0 problems. `mutate_fork`: 446 of 467 caught, 21 equivalent (cold,
including the module cases; the module subgroup is 37 of 38 caught + 1
equivalent). `mutate_port`: 414 of 414 caught. All single-file programs, errors,
benchmarks, homes checks and the tools suite are unchanged.

**Limitations, by design for Stage 1.** No `.sym` writer/reader, `--check`,
`--sym-dir`, `--emit-sym`, `--version`, object files or linker; a full build
recompiles every module body (milliseconds at this scale), and `--check`'s
"same verdict without code" claim was Stage 2's job (done in the next record). Symlink identity is
lexical. `--deps` streams its post-order, so a duplicate-name error can follow
the paths of the modules already completed (it writes no files). A module
alias's diagnostics use the declared module name, not the alias. Module names are
not reserved: a module named `int` or `Result` is accepted only with an explicit
alias; Stage 2 later decided (below) that such a name is spelled `int::T`/`Result::T`.
Two spellings that normalize to one path dedup; a file reached under a path that
normalizes differently is a different module (and the duplicate-name check
reports it when the headers agree). The compiler stays one
file; splitting it (or the stdlib and tools) is the next stage, with a later
`.sym` format and promotion after review.

**Risks found and handled.** The module-context refactor touches the scanner,
the three global views, forward fixups, the init chain and teardown; the
single-file suite, the port comparison (1,102 cases plus the compiler's own
compile), the memory cap and the byte fixed point all stay green. The B2 and B4
counterexamples and the B5/B7 cases are tests, not comments. The pre-scan's `::`
is taught, with the documented fallback if a future emitter misses a form. The
remaining risk at Stage 1 time was Stage 2's interface identity; the Stage 2
record below writes and validates symbol files and keeps the live-binding
invariant total, so descriptor ownership is still single by construction.

---

# Multiple modules with symbol files: Stage 2 (done)

Stage 2 was implemented in the worktree `modules-sym` on top of `01a66c0` (the
merged Stage 1 tree), merged at `744809c`, and the seed promoted once at
`3e17141`. The compiler is still one file (`src/obo.obo`, no imports of its
own), and the two-round rule held: the old Stage 1 seed built the Stage 2
source. This record covers the `.sym` writer and
reader, `--check`/`--sym-dir`/`--emit-sym`/`--version`, staleness and version
errors, the tests, the mutants and the measurements. The design note is
`/tmp/obo-module-design-v2.md`; this record states what was built and any
deviation.

## What separate compilation means here

Interfaces are separate; code is not. A full build compiles every module body
from source, dependency-first, into one image, and writes every module's `.sym`.
At an import it binds the dependency's **live** interface (types, globals,
functions, descriptors) and reads the `.sym` only to validate it: version lines,
source hash, dep/depiface pairs, body hash, declaration structure and effect
summaries must agree with the live objects. The `.sym` is therefore never the
semantic source of truth in a build, which keeps one `^Type` per key and one
descriptor per type by construction. `--check` is the mode where the file is the
interface: it compiles and checks the checked module's body against dependency
`.sym` files, reconstructs imported types with `desc = 0`, gives imported
functions a known dummy address (`0`, not `-1`) and imported globals unique dummy
offsets (`GLOBAL_BASE + 0x01000000 + 8*i`), writes the checked module's `.sym`,
discards the code and writes no executable. `--check` is not a build cache and
never compiles a dependency's source; a present dependency source only validates
the header and the `source` hash.

## The format

Text, line-oriented, parsed by the compiler's scanner. The header is `obosym 4`
(one identifier; `obo-sym` would scan as three tokens), `abi 1`,
`compiler "0.8"`, `source 0x…`, one `dep <module> "<path as imported>"` per
unique direct dependency in first-import order, and `interface 0x…`. The body is
`module <name>`, one `depiface <module> 0x…` per dependency in the same order,
then declarations. `source` is FNV-1a 64 of the source bytes exactly;
`interface` is FNV-1a 64 of the exact bytes after the interface line (module name,
deduped depiface order and every exported declaration and summary). Dep paths are
outside the hash. No address, offset, descriptor, pointer, timestamp or absolute
path appears.

Canonical emission order: shells for exported record/closed types (source order),
their definitions (source order), aliases, constants, globals, methods
(declaration order), functions.
Private declarations are skipped without reordering the exported ones. A record
lists fields in declaration order (offsets are recomputed as one 8-byte word
each); a closed type lists variants in declaration order. Field and signature
types are emitted as their underlying type, never an alias name. Constants are
primitive (int/byte as 16 hex digits, bool as true/false, string as
`hex "<bytes>"`, including `hex ""`); globals are `var`/`let` with no
initializer; function parameters are always canonical `p0`, `p1`, … (renaming a
source parameter changes only the source hash). A method is a source-like
heading whose first parameter is the receiver, `fn ([mut ]p0: [&]T) m(...)`; the
receiver is always `p0`, so `pfree` bit 0 and `pf 0 …` are its summary, and only
exported methods are emitted. Summary items are emitted
`freesall`, `writeshandles`, `pfree`, `frees N key…`, then `pf` rows by parameter
and insertion order; the reader accepts any order. `frees` keys are
`Module::name`, may name a private or transitive global, and a key for a module
not loaded creates an incomplete global stub.

**Variant shells.** Shells are emitted for exported record and closed types, not
for variants. Two rules make this safe (the second was added after the
independent review found a counterexample): definitions are emitted in
*definition-line* order, not at the symbol's forward-declaration position, and
the check-mode reader moves a completed shell's symbol to its definition line.
So a forward `pub type Holder` declared before
`pub type Shape = closed { Circle { … } … }` and defined after it is emitted
after Shape, and `?Circle` resolves; a record defined *before* a closed type
cannot name its variants because variants are declared only inside the closed
definition (and a forward `type V` followed by a closed variant `V` is a
duplicate declaration). Aliases and functions come after every definition.
Tests: `sym/forward_variant_check` (build, definition order, `--check`,
byte-identical re-emission), the committed `forward_variant` port fixture, and
the golden `expected/lib.sym`, whose `Fwd` is forward-declared before `Shape`
and defined after it; `sym/roundtrip_rich` covers an alias to a variant
(`CircleAlias`) and `modules/run/registry` covers `type Point = Dot`.

**Module names `int` and `Result`.** Stage 1 accepts these module names only with
an explicit import alias. Stage 2 keeps that behaviour and makes the names
representable: a qualified type is parsed before primitive/`Result[T]`
recognition, so `int::T`, `Result::T`, `Result[T]` and a module named `Result`
exporting `T` all round-trip. Tested by `sym/module_names_int_result` (build,
run, re-emit, `--check`).

## Hashes and versions

FNV-1a 64 with offset basis `0xcbf29ce484222325` and prime `0x100000001b3`, 64-bit
wrapping. Three exact-match version values: `obosym` (grammar and meaning),
`abi` (layout, descriptor format, calling convention, global addressing, effect
summaries, initialization order) and `compiler` (`OBO_VERSION`, currently `0.8.1`;
`--version` prints `obo 0.8.1 sym-format 4 abi 1`). Additive builtin/runtime
extensions use an approved patch release (0.8 -> 0.8.1), while 0.9/0.10 stay
reserved for the lambda stages; `obosym` stays 4 and `abi` stays 1. Errors use
the per-line §12 wording: `has format <n>; this compiler reads <m>`, `has ABI <n>; this compiler
uses <m>`, `was built by compiler '<v>'; this is '<w>'`, each with
`; rebuild it` and exit 2 — not one combined message. Bump `OBO_VERSION` with a
compiler feature, `obosym` on a grammar/meaning change and `abi` on a layout/ABI
change; a missed bump affects `--check` against stale artifacts, not a full build.

**Trust model.** `.sym` files are trusted build outputs. FNV catches accidental
staleness and corruption; it does not resist an adversary who can write the
sym-dir, because a forged well-formed interface can make the checker accept an
unsound program (it can claim a callee frees nothing). Do not `--check` against
untrusted symbol files, and do not treat a `.sym` as a security boundary. A
stronger digest/signature is future work if interfaces are ever distributed.

## Type identity, aliases, stubs and transitive frees

- The canonical key is `Module::Name`. The defining declaration is the only place
  that sets `owner`, `uid`, `name`, fields, variants, parent or `desc`; the type
  registry has one `^Type` per key. In a build the registry is filled by the live
  compiler; in `--check` shells and definitions reconstruct it with `desc = 0`.
- **B7:** an alias key maps to the existing `Type` and never creates or renames
  one. Two aliases in one module and another module's alias all denote one object
  and one descriptor; alias lines carry no owner/uid. Tested by
  `sym/b7_aliases` and the Stage 1 registry case.
- **B6:** one `^Sym` per global key. A summary key for a module not yet loaded
  creates an incomplete stub; the defining module's later `.sym` completes that
  same symbol (kind/type/dummy offset), never a second one. `sym/b6_stub_order`
  checks both load orders and the four-module use-after-free is rejected.
- **B2:** `afterCall` propagates every `f.fnRec.freed` entry independently of the
  visible-globals invalidation loop, and the summaries carry those keys across
  modules (including a middle module that cannot name the defining module).
  `sym/b6_stub_order` and `modules/errors/transitive_free` cover it.
- Effect summaries cross the boundary exactly: parameter modes, canonical names,
  `pfree`, `frees` keys with counts, and `pf` rows. Tests accept a valid imported
  append-only/pure callee and reject an imported truncating callee, a read-only
  reference passed to `mut`, a deep reference passed to a handle writer, and a
  mut callee that frees a field (`sym/effects_check`; the port fixtures
  `effects_pfree` and the b6 fixtures).

## Root rules and CLI

One emission rule: dependencies always write their `.sym`; a build with at least
one import writes the root's when it has a header; a single-file build writes none
unless `--emit-sym`; `--check` always writes the checked module's. `--emit-sym`
is a flag on a build (it still writes the executable and accepts `-o`) and
requires a `module` header; a headerless root is rejected with a clear source
error. A checked root is exempt from the file-name rule (it is the root of its
check graph) and its `.sym` is named by its declared module name; a checked
dependency must match `<basename>.sym` and its `module` line. A checked module may
have a private `main` (omitted from its `.sym`) or an exported `main` (listed; an
importer rejects it with the source-language message, exit 1). `--check`,
`--deps` and `--emit-sym` are mutually exclusive; `-o` is rejected in
`--check`/`--deps`; a missing option value says it needs a value; unknown options
exit 2.

**Default sym-dir deviation.** Design §8.2 says the default is the root source's
directory; the branch brief's phrase "generated `.sym` directories are gitignored
(default `.obo/sym` …)" was read as the build script's directory, not the
compiler default, and the implementation follows the design: without
`--sym-dir`, `<root source dir>` holds the `.sym` files. `make`/`build_tools.py`
use `.obo/sym` explicitly, `.gitignore` ignores `/obo/.obo/` and `**/.obo/sym/`,
and the committed goldens under `tests/modules/golden/expected` are not ignored.
Tests always pass a private temp `--sym-dir`, so the repository is not polluted.
The sym-dir is a flat module-name namespace, must already exist and be writable
(no `mkdir`/`rename` in the runtime), and is not writer-concurrency-safe:
parallel builds use private directories. Generated `.sym` files are never
committed; the golden test only compares unless `sym_test.py --update` is run
explicitly.

## Validation and residual risks

The reader validates before indexing or dereferencing: exact 16-hex hashes and
uids, non-empty dep paths, dep/depiface partner count and order, body hash before
parsing, shells complete and definitions shelled, duplicate type keys/exports/
fields, unknown types (including a foreign private type), zero-variant closed
types, parameter modes, `mut` implies `&`, borrowed types owned, return types
never references, primitive constants and ranges, `frees` counts and unique keys,
`pf` index/event path, and `pf`/`pfree` consistency (bits 0..61; 62/63 ignored; a
row below 62 implies its bit and a set bit has a row). Malformed input is a clean
`obxc: <sympath>:<line>:<col>: symbol file: <detail>` (exit 2); `sym_fuzz.py`
checks 200 seeded mutations for no signal/trap/hang and a deterministic outcome
digest.

Two limitations are recorded rather than claimed solved:

- **Live-interface validation is defence in depth.** In a build the dependency's
  `.sym` is written by `writeSym` from the live module and read immediately by the
  same process at the import point; there is no program input between the write
  and the read, so a disagreement would require an external writer or an I/O
  corruption race. All module trees exercise the validation path with valid
  output, but no test can feed it a disagreeing file without changing a build into
  a check. No mutant was added for disabling it (it would be unkillable by
  program input); the risk is recorded here. The check-mode reader path shares the
  same structure code and is heavily mutated and fuzzed.
- **Reader-side `pf` row reconstruction** is mutated too (`sym: imported pf rows
  dropped`): reconstructed functions lose their rows, so re-emission differs and
  `sym/roundtrip_rich`/`sym/handwritten_interface` (and the port reemit case)
  kill it. It is not equivalent. Writer omission of rows is killed byte-for-byte
  by the `.sym` comparison as well.
- **Dummy imported-global offsets** are not observable by a mutant:
  `--check` discards code and the checker uses symbol identity, not offsets, so
  an overlapping dummy offset changes no verdict. Recorded as a gap rather than
  forced into an artificial test.

**Review fixes.** The independent review found three blocking defects, all fixed
with regression tests and mutants. (B1) The stale-source sentinel was the value
-1, so a changed source whose FNV-1a hash had the top bit set skipped the
staleness check; `validateDepSource` now sets a `symSrcFound` flag and every
64-bit hash is compared (`sym: negative source hash treated as absent`, the
negative-hash part of `sym/stale_missing_corrupt_versions`, and the
`negative_stale` port fixture). (B2) A forward-declared record naming a later
closed type's variant was emitted at its forward position and rejected by
`--check`; definitions now follow definition-line order, the check-mode symbol
line is set at the definition, and mode-0 order validation sorts by definition
line (`sym/forward_variant_check`, `forward_variant` port fixture, golden `Fwd`,
and the definition-order mutants). (B3) A nested `readSym` cleared `symMode`, so
a malformed `.sym` with a depiface was reported as a source error; `readSym`
saves and restores its caller's mode (`sym/nested_sym_error`, `nested_corrupt`
port fixture, and the `symMode` mutant). Non-blocking items fixed too:
check-mode shell uid validation (`sym: shell uid not validated`, wrong-uid
fixture), a real B6 stub-creation test (`sym/b6_stub_completion` and the
`b6_stub_*` port fixtures), the reader pf-rows mutant, a fuzz corpus with a
middle module, a private freed global and duplicate imports, dependency-basename/
group-order/key-prefix/variant-parent mutants, registry clearing in
`releaseTables`, deletion of the stale `effects_pfree/main.sym`, `--reemit-sym`
and `-o` documentation, and scoping the Stage 1 text below.

## Tests, mutants and measurements

- `tests/sym_test.py`: 27 named tests covering writer round trip
  (`--reemit-sym` byte identity) for every production, determinism, golden
  fixtures, source-vs-interface hashes, private declarations, parameter rename,
  depiface propagation, stale/missing/corrupt/format/ABI/compiler errors,
  source-less check and full-build refusal, sym-dir errors, root/`--emit-sym`
  rules, check mode and CLI, malformed structures with pinned details,
  hand-written interfaces (arbitrary parameter names, summary order, `pfree 0`,
  a `pf` row at index 62, high `pfree` bits), B6 both orders, B7 aliases,
  effect precision, scanner restore, reader-not-lookup, `int`/`Result` modules,
  checked-root basename, duplicate-import dedup, default sym-dir, headerless
  check, and transitive type loading. Wired into `run_fork.py` (three sym tests
  in `SMOKE`), so `mutate_fork` judges them.
- `tests/sym_fuzz.py`: 200 seeded mutations (truncation, line edits, token and
  hash-digit replacement, version/dep/summary corruption, unknown declarations,
  half rehashed to reach structural validators), 10 s timeout and a 5 GiB address
  cap; exit 0/2, or exit 1 only for source-language interface rules (exported
  `main`, `_root`, basename mismatch, a removed export that breaks the checked
  source); same digest on repeated runs.
- `port_compare.py`: per-compiler private sym dirs, every emitted `.sym` compared
  byte-for-byte, `--check` compared for every header-bearing source of each run
  tree, 35 `modules/sym` fixtures (valid, stale, corrupt, version, depiface,
  main-export, B6 both orders, transitive type, and the malformed structures
  above), five build/CLI special cases (emit-sym, headerless emit/check, default
  sym-dir, source-less build), a `--reemit-sym` comparison for high pfree bits,
  CLI comparisons (`--version`, unknown option, missing value, `-o` with
  `--check`) and the token dump.
- Golden fixtures: `tests/modules/golden/tree` and
  `tests/modules/golden/expected/{lib,main,other}.sym`, regenerated with the
  final compiler; `sym_test.py --update` is the only writer.
- `MODULE_MUTANTS` gains 77 `sym:` entries (writer groups/summaries/hashes/
  dependency lines; reader version/hash/structure/malformed summaries; type and
  global registries, B6, B7; imported address/pfree/pmodes/freed keys; root
  rules, check flag, CLI, default sym-dir, source-less message; the review's
  negative-sentinel, pf-rows, basename, definition-order, key-prefix,
  variant-parent, uid and symMode cases). Each pattern occurs exactly once and
  each is killed by a named test: measured 77/77 by `mutate_fork` (run_fork
  judge) and 77/77 by `mutate_port` (port_compare judge).
  No Stage 2 equivalent was needed. The full totals are recorded in README after
  the cold gate.
- The final fixed point is exact: `obo` == stage 2 == stage 3, 493,280 bytes,
  sha256 `b54d5da7b6ee6ab6685b3fe70d7824c18201689aa650cb9dff6f81d9ccf35e0a`;
  the compiler source is 10,440 lines / 381,767 bytes, sha256
  `597b807864dcad48a56a861e2ce7cd166f005f55278fb81dc1b530e37086cb9a`.
  **These are the pre-size-fix figures.** The "Compiler size fixes" record at the
  end of this file supersedes them: the next-stage compiler is 421,889 bytes
  (B == C), and the seed was 493,280 before the size-fix promotion.
- Measured port memory compiling itself on the merged tree: **1,634,720 bytes
  live**, 35,121,184 allocated (budget 2,000,000; 365,280 spare). Stage 1 was
  1,364,240 / 28,136,432, so Stage 2 costs +270,480 live / +6,984,752 allocated.
  The compiler source grew from 8,756 to 10,440 lines (321,043 → 381,767
  bytes); the built compiler is 493,280 bytes and the promoted seed is the same
  binary. The fixed point (stage 2 == stage 3 == seed, and the merged-tree
  suite) is recorded in README; `make promote` was run once after the merge.

## Build script and tool deferral

`build_tools.py` creates `obo/.obo/sym` and passes `--sym-dir` to every compile;
the Makefile makes the directory an order-only prerequisite (creating or touching
it does not rebuild the tools) and `clean` removes it. The tools are still
single-file roots built by concatenating preludes, so no `.sym` is written today
and `--deps`-based Make prerequisites are deferred until a tool becomes a module
root; converting the tools/stdlib to modules is explicitly out of Stage 2 scope.
A module tool will build unchanged with the same command. (Superseded: the tools
and their libraries became source modules in the next record.)

## Rejected alternatives (Stage 2)

Objects/linker and per-module relocations (Stage 3); binary `.sym`; a `.sym`
carrying addresses/descriptors (two tags for one type); unqualified or wildcard
imports and re-exports; `alias.name`; making the compiler a multi-file module
tree; absolute paths/timestamps in `.sym`; a compiler self-hash in the header
(breaks obo/port byte comparison and depends on invocation); a `.sym`-based build
cache (`--check` compiles bodies every run); raising the memory budget or the
mutant margin.

# The command-line tools as source modules (done)

Implemented on branch `tools-modules` on top of `3e17141` (the merged Stage 2
tree) and merged at `4e733a6`. There is no compiler, runtime or seed change:
this is the first tool and library migration to source modules, done with the
Stage 2 language as merged.

## What changed

- `obo/lib/str.obo` became `obo/lib/strings.obx` (`module strings`); every
  declaration is `pub` (`isSpace`, `isBlank`, `isPrint`, `isWordByte`,
  `cmpBytes`, `errText`, `digits`, `appendPadded`).
- `obo/lib/io.obo` became `obo/lib/io.obx` (`module io`); `Reader`, `Writer` and
  every function are `pub`. The module stays dependency-free: `writePadded` (and
  its own digit loop) remains in `io` for this migration, and no `fmt` module was
  added.
- `obo/programs/{cat,wc,sort}.obx` became module roots. Each imports
  `../lib/io.obx` as `io` and `../lib/strings.obx` as `strings` and qualifies
  every library name (`io::Writer`, `io::newReader`, `io::fill`,
  `strings::errText`, ...). They remained in `programs/`, not `cmd/`, at that
  stage; flags, messages, output bytes and exit codes were unchanged. (P2a
  later moved them to `obo/cmd/`; see the P2a record at the end of this file.)
- `obo/tools/build_tools.py` compiles each root directly; the generated header
  and all concatenation are gone. Every build gets its own `<out>/sym`, created
  by the builder (`build/tools/sym` for `make tools`). Concurrent builds must
  use distinct outputs: one output has one sym dir and one probe name. Compile
  diagnostics name the real source files;
  `build()` keeps its signature for `tests/tools.py` and the builtin probe.
- `tests/run/io_reader.obx` and `tests/run/io_writer.obx` and
  `tests/lib/{boundaries,stream,short_write,write_padded}.obx` are module roots
  importing `io.obx`; they are compiled with `-I obo/lib` and a private
  `--sym-dir`. `run_fork.py`, `port_compare.py`, `io_test.py` and `mutate_io.py`
  no longer inject the library text: `run.py` gained an optional `extra_args`
  for module roots. `port_compare` compiles the module roots with per-case
  module flags in both the default and `OBO_REF=seed/obo` comparisons; only the
  separate module-tree cases stay out of the seed count. `mutate_io.py`'s
  library mutants now copy the `lib/*.obx`
  modules into a scratch tree, replace `io.obx` with the mutant and compile the
  affected roots against `-I <scratch>/lib`; runtime mutants keep the real lib.
- `make tools` no longer creates `.obo/sym`. Generated symbol files live under
  `build/tools/sym`, already ignored with `obo/build/`; the generic
  `--sym-dir .obo/sym` convention and its ignore rule remain for other builds.

## Gates (merged tree)

**These are pre-size-fix figures.** The "Compiler size fixes" record at the end
of this file supersedes the compiler and seed sizes, the peak and the mutant
counts; the seed was 493,280 before the size-fix promotion.

`make`; `make tools`; `tests/tools.py --require-reference` 199/199; `--memory`
2/2; `io_test.py --rt --programs --tools` 0 failures; `run_fork.py` 1129
passed; `port_test.py` 486/486; `port_compare.py` 1147/1147 with a
1,634,720-byte peak (budget 2,000,000), and `OBO_REF=seed/obo
port_compare.py` 1025/1025; `mutate_io.py` 27/27 caught; `mutate_fork.py`
523/544 caught + 21 equivalent; `mutate_port.py` 491/491; `sym_test.py`
27/27; `sym_fuzz.py` 200/200; `doc_check.py` 45 examples; `homes_check.py`;
`homes_edge.py` 1028 statements, 119 refused by both, 0 differ;
`bench/bench.py` runs. Fixed point `obo` == stage 2 == stage 3 == seed,
sha256 `b54d5da7b6ee6ab6685b3fe70d7824c18201689aa650cb9dff6f81d9ccf35e0a`,
493,280 bytes. The compiler source, runtime and seed are byte-identical to
`3e17141`.

## Deviations and limitations

The P2 checklist in `/tmp/obo-stdlib-eza-design-v2.md` asks for `lib/fmt.obx`
(with `writePadded` and `digits` moved there), a `writeByte` addition and a move
to `cmd/`. Those belong to the wider stdlib split and were deliberately left out
here: `writePadded` stays in `io`, `strings` keeps `digits`/`appendPadded`, and
the tools stayed in `programs/` with their existing relative imports (P2a later
moved them to `cmd/`). The module
build produces binaries of the same size as the old concatenated build but not
the same bytes (module compile order and initialization change code layout); the
differential gate compares behavior, and tool stdout, stderr and exit codes are
byte-identical. The two `--memory` cases pass well under the 1,048,576-byte case
limit; memory figures vary by build path, and no behaviour difference was
measured.

## Stage 3 sketch

Objects at module granularity (code, rodata, descriptor bytes and relocations for
calls, globals, strings and descriptors), a link pass and a build cache keyed by
the interface hash. Stage 2 deliberately keeps one image and one descriptor per
type, so Stage 3 can add objects without changing the interface format or the
type-identity rules.

# Compiler size fixes (done)

Implemented on branch `size-fixes` on top of `4e733a6` (the merged
tools-modules tree); main (`7fa68db` and `b3f369f`) was merged into the branch,
and the branch was merged into main at `134e6a5`. The seed was promoted once
after the merge: `seed/obo` and `obo` are both B (421,889 bytes). Main is
pushed; the branch is not pushed separately.

## What changed

- **Epilogue.** `epilogue()` emits `leave; ret` (`0xC9 0xC3`, two bytes)
  instead of `mov rsp,rbp; pop rbp; ret` (`0x48 0x89 0xEC 0x5D 0xC3`, five).
  Its three call sites are the early return in `tryExpr`, `returnStat` and
  `fnBody`; every compiled function has the `push rbp; mov rsp,rbp` prologue
  (or `dropFrame`'s copy of it), so `leave` is exactly the sequence it
  replaces. Prologues, frames, register allocation and loop alignment are
  unchanged. In the new compiler 931 of 931 returns are `leave; ret`; the
  pre-fix compiler had 930 old epilogues and no others.
- **Runtime data segment.** `dataFileLen(&[]byte)` returns one past the last
  nonzero byte (only the trailing zero run is trimmed). `elfImage` allocates
  and copies that prefix and emits it as the segment's `p_filesz`, while
  `p_memsz` stays the full `runtime_data.bin` length. The loader zero-fills
  the rest, the mechanism the compiler already relies on for its own globals
  segment (`filesz 0`, `memsz 0x5f8`). The vaddr and page alignment are
  unchanged. The offset is still `pageUp` of the preceding parts, so it moves
  by whole pages when the code shrinks (A's runtime data at 0x68000 becomes
  0x67000 in B; the tool `cat`'s 0xa000 becomes 0x9000). The globals, runtime
  text and rodata segment shapes are unchanged.

## Measurements

The source grew from 10,440 lines / 381,767 bytes to 10,448 lines / 382,329
bytes, sha256 `44770afa7da4e8090aefc1f8a4e644379e16e19d177d4a5939bc1bca4c0854e3`.
The pre-fix seed was 493,280 bytes, sha256
`b54d5da7b6ee6ab6685b3fe70d7824c18201689aa650cb9dff6f81d9ccf35e0a`; the
promoted seed is B, 421,889 bytes, sha256
`55dab101d819d9eeb1b408da64a2c1a96033e46399bd79bb130f63bf3ff76e98`.

Fixed point, built on the merged-tree path (`make` uses the committed seed):

| build | built by | file bytes | code bytes | runtime data filesz/memsz | sha256 |
|---|---:|---:|---:|---|---|
| A | seed (pre-fix) | 493,280 | 373,429 | 0x106e0 / 0x106e0 | `804ec94d…` |
| B | A | **421,889** | **370,476** | **1 / 0x106e0** | `55dab101…` |
| C | B | 421,889 | 370,476 | 1 / 0x106e0 | `55dab101…` |

`cmp B C` passes: B == C byte for byte. A is still wrapped by the seed's old
emitter (full data segment, old epilogue encoding in A's own code), but A's
own emitter is the new one, so every program A builds is trimmed. B was
promoted as the seed; `make` now builds C == B, so `seed/obo` == `obo` == B/C.
A exists only as the pre-promotion build.

Epilogue saving alone: a source with only the epilogue change, compiled by the
seed and then by itself, has code 370,380 bytes (from 373,365) and file 489,184
(from 493,280): **-2,985 code bytes**. The decomposition is 930 epilogues x 3
nominal bytes (`leave; ret` is two bytes, the old sequence five) = 2,790, plus
32 bytes from the shorter compiled `epilogue()` body and 163 bytes of
layout/alignment effects = 2,985. The combined compiler has 931 epilogues
because the runtime-data change adds a function: 931 x 3 + 32 + 160 = 2,985 for
the epilogue component. Its net code is 370,476, i.e. 2,889 below the pre-fix
373,365, because the runtime-data emission change as a whole (the `dataFileLen`
helper plus the changed copy and header code) adds 96 bytes.

Every produced executable loses the 67,295-byte zero run (the runtime data
image has exactly one nonzero byte, `out_fd = 1`, at offset 0; last nonzero + 1
= 1); a produced file shrinks by 4,096 more when its code crosses a page
boundary (`cat`):

| binary | before | after | delta |
|---|---:|---:|---:|
| minimal (`module mini; fn main() {}`) | 95,968 | 28,673 | -67,295 |
| tool `cat` | 108,256 | 36,865 | -71,391 |
| tool `wc` | 108,256 | 40,961 | -67,295 |
| tool `sort` | 112,352 | 45,057 | -67,295 |

The extra 4,096 bytes for `cat` are a code page boundary: its code crossed one
while the runtime data shrank. The examples compile to 28,673 bytes and produce
output identical to the pre-fix compiler's.

Segment table for B (`readelf -lW`):

```
Program Headers:
  Type           Offset   VirtAddr           PhysAddr           FileSiz  MemSiz   Flg Align
  LOAD           0x000000 0x0000000000400000 0x0000000000400000 0x05b72c 0x05b72c R E 0x1000
  LOAD           0x05c000 0x0000000008000000 0x0000000008000000 0x0068e0 0x0068e0 R   0x1000
  LOAD           0x000000 0x000000000c000000 0x000000000c000000 0x000000 0x0005f8 RW  0x1000
  LOAD           0x063000 0x0000000010000000 0x0000000010000000 0x003360 0x003360 R E 0x1000
  LOAD           0x067000 0x0000000020000000 0x0000000020000000 0x000001 0x0106e0 RW  0x1000
  GNU_STACK      0x000000 0x0000000000000000 0x0000000000000000 0x000000 0x000000 RW  0x10
```

The runtime data segment is the last part of the image: `0x67000 + 1` is the
file size, so the 67,295-byte zero run is gone. gdb at `starti` shows
`0x20000000 = 1` and zeros at `0x20000ff0` and `0x200106d0`; the Linux loader
zero-fills the tail, exactly as it does the compiler's globals.
`dataFileLen` trims only the trailing run: `tests/elf_test.py` checks a
synthetic image with a zero gap before a later nonzero byte (filesz = last
nonzero + 1) and an all-zero image (filesz 0, offset at the end of the file).

Memory compiling itself with the new compiler (`port_compare.py`): **1,636,176
bytes live**, 35,076,608 allocated (budget 2,000,000; 363,824 spare). The
pre-fix Stage 2 figures were 1,634,720 / 35,121,184; the peak stays well inside
the budget. A direct `OBO_STATS=1` self-compile, with a different source path,
reports 1,636,064 / 35,075,296. Self-compile wall time is 0.11 s (median of 5;
A and B). Maximum resident set is 2,860 KB for both A and B in 5 runs each on
this host (the pre-fix Stage 2 figure was 2,988 KB; RSS varies with load and
page residency).

## Tests

- `tests/elf_test.py` parses a produced executable's program headers and
  asserts: one `PT_LOAD` at `RT_DATA`, `memsz` = the `runtime_data.bin` length,
  `filesz` = last nonzero + 1 (strictly below `memsz` when the image ends in
  zeros), the segment's file bytes are the runtime data prefix `rt[:filesz]`,
  the segment is page aligned with the same vaddr, the file ends at
  `p_offset + p_filesz`, and the compiler's globals segment still has filesz 0.
  It then runs a program compiled by the same compiler. Synthetic data images
  cover the trailing-run rule and the prefix bytes (a zero gap before a later
  nonzero byte, and an all-zero image). It is run by `run_fork.py` as
  `image/runtime_data_segment` (1,130 tests) and standalone in the documented
  gate list.
- `SIZE_MUTANTS` in `tests/mutate_fork.py`, five entries, each pattern unique:
  the old full-length segment, `p_memsz` shrunk to `p_filesz`, and a copy of
  only the first prefix byte (all caught by the image test; the last only by
  the synthetic zero-gap image, since the real prefix is one byte), the old
  two-instruction epilogue (equivalent: `leave` is defined as exactly those two
  instructions), and an `int3` return (caught by the executable tests).
  `mutate_port.py` imports the group; the three segment mutants are judged by
  `elf_test.py --source` (the comparison sees the same outcomes and peak), the
  others by the ordinary comparison.

## Gates (merged tree)

`make`; `run_fork.py` 1,130 passed; `port_compare.py` 1,147/1,147 with a
1,636,176-byte peak, and `OBO_REF=seed/obo port_compare.py` 1,025/1,025;
`port_test.py` 486/486 identical, no leaks, 9 encoder groups against objdump;
`doc_check.py` 45 examples; `homes_check.py`; `homes_edge.py` 1,028 statements
0 differ; `io_test.py --rt --programs --tools` 0 failures; `tools.py
--require-reference` 199/199; `tools.py --memory` 2/2; `sym_test.py` 27
passed; `sym_fuzz.py` 200/200; `bench/bench.py` all four workloads; the new
`elf_test.py`. `mutate_fork.py`: 527/549 caught, 22 equivalent (the new group:
four caught, one equivalent); `mutate_port.py` 495/495; `mutate_io.py` 27/27.
The fixed point B == C; B was promoted, and the post-promotion checks
(`make`, `run_fork` 1,130, both `port_compare` runs 1,147/1,147 and 1,025/1,025,
`sym_test` 27, `sym_fuzz` 200/200, `doc_check` 45, `elf_test`) pass with
`seed/obo` == `obo` == B/C.

## Limitations and residual risks

- The zero-fill relies on Linux's ELF loader for `p_filesz < p_memsz`; the
  runtime is Linux x86-64 only (direct syscalls), and the compiler already
  relied on this for its globals segment.
- Produced executables are not byte-identical to the pre-fix compiler's: the
  runtime data tail is gone and the epilogue encoding changed. No test compares
  produced bytes; the fixed point is B == C, and every behavioural comparison
  passes.
- The segment check requires `filesz == last nonzero + 1`; it requires that to
  be strictly below `memsz` only when the image does end in zeros (the current
  zero-filled .bss tail). An image whose last byte is nonzero correctly gets
  `filesz == memsz` and is accepted.
- Before the promotion, `make` built A (493,280); after the promotion, `make`
  builds C == B (421,889), and `seed/obo` == `obo`.

# The tools move to `cmd/` (P2a, done)

Implemented on branch `eza-p2a` on top of `265564c` (the approved v7 design; the
size-fixed seed is `84558da`). This is the path/source-move part of the design's
P2 checklist (§4.1, §10.1, §10.3); it changes no compiler, runtime, seed,
library module or tool semantics. The API half of P2 (add `fmt`/`errors`, remove
the four compatibility functions, switch the tools) stays for P2b.

## What changed

- `obo/programs/{cat,wc,sort}.obx` and `obo/programs/README.md` moved to
  `obo/cmd/` with `git mv`. The tool sources are byte-identical apart from the
  three header comments that pointed at `programs/README.md`; their
  `import "../lib/io.obx"`/`"../lib/strings.obx"` paths resolve unchanged
  because `cmd/` and `programs/` are at the same depth under `obo/`.
- `obo/tools/build_tools.py` gained `SRCDIR = 'cmd'` and both source joins use
  it; the docstring and diagnostic text name `cmd/<tool>.obx`. The single
  `build()` entry point, `_compile`, `_read`, `_symdir`, `_map_error`, `probe`
  and `BuildError` are unchanged. Still no `-I` and no concatenation.
- `obo/Makefile` (comment above the `tools` target), `README.md` (status, tree,
  build/status paths), `obo/tests/io_test.py` (the `tools` section docstring)
  and the historical FORK records now name `cmd/` or mark the old path as past.
  `tests/tools.py` needed no change: it calls `build_tools.build` and never
  spells the source directory. `tests/mutate_io.py` and `tests/run_fork.py` do
  not touch the tools at all.
- No `programs/` live path remains. The approved design note
  (`design/stdlib-eza-design-v7.md`) deliberately keeps its pre-P2 `programs/`
  references as the plan of record and was not edited.

## Gates (this branch)

`make` (seed == `obo`, sha256 `55dab101…`); `make tools`; `tests/tools.py
--require-reference` 199/199; `--memory` 2/2; `io_test.py --rt --programs
--tools` 0 failures; `run_fork.py` 1,130 passed; `elf_test.py` ok;
`port_compare.py` 1,147/1,147 and `OBO_REF=seed/obo port_compare.py`
1,025/1,025 (live peak 1,636,336 bytes in the worktree, budget 2,000,000);
`mutate_io.py` 27/27; `port_test.py` 486/486; `mutate_fork.py` 527/549 + 22
equivalent; `mutate_port.py` 495/495; `sym_test.py` 27/27; `sym_fuzz.py`
200/200; `doc_check.py` 45 examples; `homes_check.py`; `homes_edge.py` 1,028
statements, 0 differ.

## Notes and residual risks

The tool binaries on that branch had the same sizes as the recorded pre-P1
figures (cat 36,865; wc 40,961; sort 45,057; historical: P1's whole-module
strings API later changed them, and the merged P2 sizes are in the P2b record);
the move changes no tool semantics, and the
differential gate compares stdout, stderr and exit exactly.
`io_test.py`'s `--programs` flag names the test-program section (`tests/io` and
`tests/lib`), not the old tool directory; its spelling is CLI surface and was
kept. The compatibility functions (`strings::cmpBytes`, `errText`, `digits`,
`appendPadded`) and `io::writePadded` are still present and still used; P2b
removes them together with the `fmt`/`errors` switch. (It did: see the P2b
record below.)

# The fmt/errors split and the tool API switch (P2b, done)

Implemented on branch `eza-p2b` on top of a temporary merge of P1 (`5990686`)
and P2a (`efcac6f`), HEAD `ec244e0`. P2b is the API half of P2 and assumes the
P2a move. Main's reviewed P1 merge (`37d7b88`, including the split-cluster fix
`e03634e` and its EXPECTED_ROOTS inventory) was then merged in cleanly on this
branch and the full gate was re-run on the merged tree with unchanged counts. The change makes no compiler, runtime, seed or design edit, and there
is no `make promote`.

## What changed

- `obo/lib/fmt.obx` (new): `digits` and `appendPadded` moved from `strings`,
  and `writePadded` moved from `io`, with unchanged bodies and output bytes,
  plus `modeString` for the P4 long view (eza's type characters `. d l | s b c
  ?`, rwx triples with `s`/`S` and `t`/`T`; 10 characters, no xattr `@`). The
  design's signed writers (`intWidth`/`writeInt`/`writeIntRight`/
  `writeIntLeft`), `writeStringPad` and `octal` have no P2 caller and are not
  added.
- `obo/lib/errors.obx` (new): `text` (the merged `errText` plus ERANGE 34),
  `osError` (`<text> (os error N)`), `debugPath` (Rust 1.87 `OsStr`/`Path`
  Debug quoting: quotes, `\"`/`\'`/`\\`, `\0`, `\t`/`\r`/`\n`, `\u{h}` for
  other control bytes and DEL, valid UTF-8 bytes copied), `report`, and
  `EXIT_OK`/`EXIT_FAIL`/`EXIT_PATH`/`EXIT_USAGE`/`EXIT_PERMISSION` =
  0/1/2/3/13.
- `obo/lib/io.obx`: removed `writePadded`; added `writeByte` (append plus the
  existing threshold flush and error rule); `clampChunk` and the rest of the
  public surface are unchanged.
- `obo/lib/strings.obx`: removed `cmpBytes`, `errText`, `digits` and
  `appendPadded`; `cmp` is the surviving byte-order body and the rest of the
  section 5.1 API is unchanged. The header's example path is now `obo/cmd/`.
- The tools: `cmd/cat.obx` uses `fmt::appendPadded` and `errors::text` (it no
  longer imports `strings`); `cmd/wc.obx` uses `fmt::writePadded`,
  `fmt::digits` and `errors::text`; `cmd/sort.obx` uses `strings::cmp` and
  `errors::text` and imports no `sort`. Flags, messages, output bytes and exit
  codes are unchanged.
- `obo/tools/build_tools.py`: `LIB_MODULES` includes `fmt.obx` and
  `errors.obx`; the single `build()` entry point, `SRCDIR='cmd'`, no `-I` and
  no concatenation are unchanged.
- `obo/tests/lib/write_padded.obx`: `fmt::writePadded`, with
  `import "fmt.obx"` added and the `io` import and its Writer/`write`/`flush`
  uses kept.
- `obo/tests/mutate_io.py`: the seven library patterns were re-checked against
  the edited `io.obx`; each still matches exactly once, so no anchor text moved
  and the count stays 27/27 (20 runtime + 7 library). Per the design's coverage
  boundary there are no new `writeByte`/`fmt`/`errors` mutants; those functions
  are covered by byte equality (`write_padded`, and cat/wc by the differential
  gate), not by mutation.
- `obo/tests/imports.py` (stale in the P1+P2a base): the tool roots are now
  `obo/cmd/`, the `strings` importer calls `strings::cmp` (the removed
  `strings::digits` call is gone), `fmt`/`errors` importers are added, and the
  exact lib set is the eight P2 modules. It also gains the design 4.2 P2
  tool-matrix checks: every tool import is an explicit `../lib/*.obx` path,
  `cmd/sort.obx` imports no sort module (`sort.obx` or `../lib/sort.obx`), and
  a scratch `cmd/` tree with
  `io`/`fmt`/`errors`/`strings` decoys beside `cat.obx` still builds and runs
  against the real `lib/`. That is 22/22 checks (17 P1 + 2 module importers + 3
  tool-matrix); nothing was removed.
- `obo/tests/stdlib/strings_basic.obx`: the temporary `cmpBytes` agreement
  block is removed with the function; the `cmp` block remains.
- New P2 coverage roots: `tests/stdlib/errors_basic.obx` (every `text` code
  including ERANGE 34, `osError` formatting, `debugPath` quoting against the
  rustc 1.87 forms, `EXIT_*` values), `tests/stdlib/fmt_basic.obx` (`digits`,
  `appendPadded`, all nine section 5.4 `modeString` examples plus block/char/
  unknown, and `writePadded` bytes), and `tests/lib/write_byte.obx`
  (`io::writeByte` threshold, flush-loop and EBADF error semantics, run by
  `io_test.py` with and without the short-write fault). `tests/stdlib.py`
  EXPECTED_ROOTS lists the 16 roots; EXPECTED_MODULES keeps `support.obx`.
- Docs: `README.md` (module list and imports, tool binary sizes, memory
  variance, phase 5 status), `obo/LANGUAGE.md` (io `writeByte`; the module
  list), `obo/cmd/README.md` (modules and imports), `obo/Makefile` (tools
  comment), `obo/tests/io_test.py` (programs docstring),
  `obo/tests/port_compare.py` (budget comment), `obo/FORK.md` (this record,
  the P2a record's tool-build reason and its `programs/` mention).
  `obo/design/` is untouched.

## groupDecimal

Not added. Design sections 5.4 and 7.8 make it conditional on P0 showing
`--bytes` grouping under `LC_ALL=C`. The P0 reference prints plain digits:
`long_sizes` has `1048576` and `8388608`, and `long_long` has `1234567`, with
no separators.

## Gates (this branch)

`make tools`; `tests/tools.py --require-reference` 199/199; `tests/tools.py
--memory` 2/2 (cat peak 230,352; wc peak 66,384); `tests/io_test.py --rt
--programs --tools` 0 failures; `tests/imports.py` 22/22 (8 stdlib importers,
3 tools, 3 tool-matrix, 7 collision/decoy trees, 1 lib set); `tests/stdlib.py`
16/16 roots (1 support module); `tests/run_fork.py` 1,130/1,130;
`tests/elf_test.py` (filesz 1, memsz 67,296; the program runs);
`tests/port_compare.py` 1,147/1,147 with a 1,636,336-byte live peak (budget
2,000,000) and 35,078,496 bytes total allocation; `OBO_REF=seed/obo
tests/port_compare.py` 1,025/1,025; `tests/mutate_io.py` 27/27 (0 missed, 0
stale, 0 broken); `tests/port_test.py` 504/504 identical, no leaks, 9 encoder
groups; `tests/mutate_fork.py` 527/549 caught + 22 equivalent;
`tests/mutate_port.py` 495/495 caught; `tests/sym_test.py` 27/27;
`tests/sym_fuzz.py` 200/200; `tests/doc_check.py` 45 examples;
`tests/homes_check.py`;
`tests/homes_edge.py` 1,028 statements, 0 differ; `bench/bench.py` runs to
completion (four workloads, identical outputs; `obx/obxc` built for it).
`tests/port_test.py` counts 504 tasks on this merged tree, not 486: P1 added 15
`tests/stdlib` files (14 roots and the support module) and P2b adds three test
files (two stdlib roots and `tests/lib/write_byte.obx`), and the harness
compiles every `tests/*/*.obx` file; all 504 are identical.

## Notes and residual risks

- Imported modules are compiled whole into each tool, so the sizes track the
  module set, not P2b alone: the pre-P1 tree (`265564c`) measured cat 36,865 /
  wc 40,961 / sort 45,057, the P1+P2a base (`ec244e0`) measured 49,153 /
  49,153 / 53,249, and this merged P2 tree measures 40,961 / 53,249 / 53,249.
  P2b's net effect on that base is cat -8,192, wc +4,096, sort 0. Output,
  stderr and exit bytes are unchanged (199/199 and 2/2).
- The wider `fmt` API (`intWidth`/`writeInt`/`writeIntRight`/`writeIntLeft`/
  `writeStringPad`/`octal`) is explicitly P4's: the eza long view needs
  left- and right-aligned numeric and date cells, and P2 ships only the four
  functions above, now covered by `fmt_basic` and `write_padded`.
- `modeString`, `osError`, `debugPath` and `io::writeByte` had no P2 tool
  caller (P4 uses them); the new `errors_basic`, `fmt_basic` and `write_byte`
  roots cover them, including all nine section 5.4 `modeString` examples and
  the rustc 1.87 `debugPath` forms. `digits`/`appendPadded`/`writePadded` are
  exercised by `fmt_basic`, `write_padded`, cat and wc. `errors::report`
  writes only to stderr and is not asserted (the stdlib runner compares stdout
  on success), and the tools do not call it.
- `tests/imports.py` is 22/22 rather than the P1 17/17 because the module set
  grew by two importers and the design 4.2 P2 tool-matrix adds three checks;
  no check was removed or skipped. Its sort guard rejects both `sort.obx` and
  `../lib/sort.obx` imports.
- No `writeByte`/`fmt`/`errors` mutants (design section 10.3 coverage
  boundary); `mutate_io` stays 27/27.

# Runtime metadata: slots 33-36 (P3, done)

The eza/stdlib design v7 sections 5.9, 6.1-6.9, 7.9 and 11.1. Four append-only
slots, no renumbering of 0-32; no `getcwd`, no `fstatat(dirfd,name)`, no raw
`syscall` builtin, no `resultCall2`, no new descriptor kind, no runtime-retained
program pointers.

| Slot | Builtin | Result |
|---|---|---|
| 33 | `readDirFd(fd, max)` | `Result[?[]byte]` |
| 34 | `statPath(path)` | `Result[[]int]` |
| 35 | `lstatPath(path)` | `Result[[]int]` |
| 36 | `readLinkPath(path)` | `Result[[]byte]` |

P5a/P6a append 37/38 and 39 later; each is its own compiler feature with its own
gate and promote.

**ABI (verified in the C harness).** x86-64 Linux `struct stat` is 144 bytes:
st_dev 0, st_ino 8, st_nlink 16, st_mode 24 (u32), st_uid 28, st_gid 32, pad 36,
st_rdev 40, st_size 48, st_blksize 56, st_blocks 64, st_atim 72, st_mtim 88,
st_ctim 104. `newfstatat` is 262 with its flags in r10, so the runtime gains
`sys4` (r10 bound with a register asm operand, clobbering rcx/r11/memory); every
other new call is `sys3`: `readlink` 89, `getdents64` 217. `AT_FDCWD` is -100 and
`AT_SYMLINK_NOFOLLOW` 0x100; raw returns are negative errno. The 14-int array
order is `mode, size, mtime, mtime_ns, uid, gid, nlink, ino, dev, rdev, blksize,
blocks, atime, ctime` (the design's, not the kernel's); atime/ctime are seconds,
mtime has nanoseconds, and ino/dev/rdev are copied as 64-bit values (inodes above
INT64_MAX are unrepresentable and not expected). `cpath` accepts 4095 bytes,
rejects 4096+ with ENAMETOOLONG, and rejects empty paths and embedded NULs with
EINVAL; `readlink` uses a 4096-byte stack buffer and treats a full 4096-byte
return as ENAMETOOLONG (the API limit is 4095), never a silent truncation.

**Runtime.** `rt_read_dir_fd` flushes text output, then checks in this order:
max <= 0 (EINVAL), max > 1 MiB (EINVAL), fd < 0 (EBADF), a tracked closed fd
(exit 9 under OBO_CHECK, otherwise `err(..., EBADF)`), then allocates and calls
getdents64. A short buffer is data (`len` is set to the count), only 0 is EOF,
and EOF is `ok(nil)` after the empty array is freed. `rt_stat_path`/
`rt_lstat_path` share `do_stat`, which converts the path, calls newfstatat with
flags 0 or `AT_SYMLINK_NOFOLLOW`, and copies the 14 fields from the 144-byte
struct into an owned int array. `rt_read_link` returns the raw bytes with no NUL
in an owned byte array. Every helper builds its Result cell from the compiler's
shared err descriptor and the cached ok variant; the only multi-allocation
points root their earlier value in `tmp_root[0]`. `getdents64`, `newfstatat` and
`readlink` retry EINTR; the test-only `OBO_META_FAULT=eintr` hook injects one
synthetic EINTR per call kind and is inert unless `OBO_CHECK=1` (gated after the
environment scan, like the I/O hook). The runtime data segment grew by 32 bytes
(the fault flags and alignment); runtime_text.bin by 2,320 (13,152 -> 15,472);
runtime.elf is 92,536 bytes.

**Compiler.** The four names join `universe`, `callExempt` and the non-int
"plausible" negative list. `readDirFd` parses the fd with `nestedExpr`, checks
`isInt`, materialises it before the max argument runs (as `readFd` does), checks
the max's type, rejects constant counts outside 1..1048576, materialises max,
and returns `resultCall4(RT_READ_DIR_FD, fd, max, derived("opt",
derived("array", tByte)))`. `statPath`/`lstatPath` and `readLinkPath` take one
string, pin it with `pinStr`, and return `resultCall3` with
`derived("array", tInt)` / `derived("array", tByte)`. The compiler source does
not use the new builtins (two-round rule), so the 0.2 seed still builds it.

**The `fs` module.** `lib/fs.obx` is self-contained (builtins only; no import of
`paths`). Constants: `STAT_*` (0-13, `STAT_FIELDS` 14), `KIND_*` (the DT_*
values 0/1/2/4/6/8/10/12) and `DIR_BUF` 32768. Types: `Stat`, `DirEntry`
(name/ino/kind) and `Dir` (borrowed fd; owned path, raw buffer and chunk string;
pos/used/eof/size). `open` is `openFile(path, "r")`; the operand string is owned
by the Dir and is the entry-path base. `next` refills when the buffer is
exhausted, treats `nil` as EOF, copies the chunk once with `string(buf)`, and
validates every record (reclen >= 20, pos+reclen <= used, a NUL inside the
record): a bad record is `err("bad directory entry", EIO=5)` without advancing.
Each name is a `substr` copy; "." and ".." are skipped by name. The kind is
d_type; for DT_UNKNOWN `next` lazily lstats `entryPath(dir.path, name)` and uses
`typeOf(mode)`, leaving KIND_UNKNOWN when that lstat fails. (The design's
Type-resolution bullet is eza's `File::filetype()` fallback; `DirEntry` exposes
only the resolved kind, so the fallback is realised in `next`. No mounted
filesystem here returns DT_UNKNOWN, so the crafted-buffer unit test is the
exercise; the P0 DT_UNKNOWN probe stays unverified on this host.)
`close` returns ok(false) with no syscall when fd < 0, otherwise closes, sets
fd = -1 whatever the Result says, and is idempotent. `statArray(path, follow)` is
the hot form; `stat`/`lstat` build the record and free the array; `readlink`
returns `readLinkPath`'s raw bytes. `typeOf` maps the S_IFMT bits; the predicates
use it; `isExecutableFile` is regular && owner-execute (0x040);
`sizeForLong` is size for a regular file and -1 otherwise.

**Build.** `tools/build_tools.py` adds `fs.obx` to `LIB_MODULES` and, after the
phase-2 compile-only probe, a phase-3 metadata probe that builds and RUNS a
tiny `statPath` program on a file it creates, checking the type, size and field
count under `OBO_CHECK=1`: a compile failure catches an old compiler, and a run
failure (a wrong slot, a trap or a leak) catches an old `runtime_text.bin`,
which a compile-only probe cannot see. `tests/imports.py` moves to the
nine-module set with an `fs` importer.

**Tests.** `tests/rt_meta_test.c` links runtime.c with its own `_entry` and
creates every fixture through raw syscalls: a file with a known size/mode and
pinned atime/mtime (the 14 fields against a fresh raw newfstatat at their
canonical offsets, with hardcoded expectations for the known values), stat vs lstat on symlink-to-file and
symlink-to-dir, a directory's type, a pre-1970 mtime (-100/-200), a uid/gid
oracle from a host path whose owner and group differ (`stat_owner`; /etc/shadow
here), ENOENT/EINVAL/ENAMETOOLONG (a 4095-byte multi-component path reaches the
kernel, while a single 4095-byte component is the kernel's own ENAMETOOLONG
from NAME_MAX; 4096 is the runtime's cpath check), readlink bytes/dangling/non-symlink
EINVAL/missing ENOENT/a 4095-byte target, readDirFd's whole records with . and ..
visible raw and nil EOF, the max/cap/fd/table order (a bad max never allocates),
a regular-file fd's ENOTDIR, a small max's kernel EINVAL, the tracked-closed-fd
exit 9 and its no-check EBADF, and the EINTR hook with and without OBO_CHECK.
Every case except the checked readdir_closed trap ends in rt_finish, so
under OBO_CHECK=1 those are also the leak oracle.
`tests/meta_test.py` builds it and runs `tests/meta/*.obx` against Python
fixtures: names/kinds/inodes vs os.listdir/os.lstat (no "." or ".."), all 14
statArray fields vs os.stat/os.lstat for a pinned file, a symlink and a
directory, and the Stat record's 14 fields for fs::stat/fs::lstat on a symlink
(stat_record), raw readlink bytes (including a 4095-byte target) and the error
codes, entry paths via paths::join for an absolute operand outside the cwd and
for a "../entries" operand, close idempotence, open/close with no leak, the
fs-level errors, crafted DT_UNKNOWN/d_type-wins/dot-skip/malformed-record
records, and the dedicated missing-close exit 8. `tests/run/meta.obx` covers the
four builtins at the program level (non-consuming and consuming matches, try
propagation, owned payloads) and the fd-materialisation case:
`readDirFd(gfd, changeGfd())` must read the fd as it was, so it prints "pin dir
inside"; a compiler that does not materialise fd prints "pin dir other".
`tests/errors/meta.obx` has 16 compile cases.

**Mutants.** `tests/mutate_meta.py` has two independent runners and measured
counts. The runtime/entry.S runner builds each mutant runtime in a private
directory and judges it with rt_meta_test.c (rebuilt against the mutant
runtime.c) and the whole tests/meta corpus (11 program invocations, 10
sources; entry_paths runs with two operands) compiled against the mutant
runtime;
an entry.S mutant also runs tests/run/io_basic.obx first, whose calls go
through the slot table (the C harnesses have their own `_entry` and cannot see
entry.S), so the representative I/O check cannot be skipped:
**30/31 caught + 1 equivalent** (the 4096-byte readlink target cannot be
created; the kernel maximum is 4095). The compiler runner builds a mutated
compiler from src/obo.obo with the unmutated compiler and requires every
tests/errors/meta.obx case to keep its exact diagnostic and every program to
keep its baseline outcome: **9/12 caught + 3 equivalent** (the path pin is
unobservable with one argument; callExempt and plausible only affect
register-allocation scoring). 0 missed, 0 stale, 0 broken. The stale
`mutate_io` hook-gating anchor was re-anchored in the P3 commit (the gate line
now also clears `meta_fault_eintr`), and mutate_io stays **27/27**.

**Deviations and limits.** (1) The design's `Type resolution` is placed in
`fs::next`, not deferred to eza, because `DirEntry` has no raw-d_type field; the
resolved kind is what P4 consumes. (2) `port_compare`'s seed comparison skips
the two new P3 case files only while the reference seed's `--version` is below
0.3: the 0.2 seed does not know the metadata builtins and would reject them
while the port compiles them. The default comparison covers them; a promoted
0.3 seed has no exclusion and compares the whole corpus (1,042/1,042, checked
with a temporary 0.3 seed copy) without any edit. (3) A
4096-byte symlink target is refused by the kernel, so the runtime's r == 4096
branch is defence in depth and untestable here. (4) The path limit and the
getdents64-to-stat race remain (cmd/README.md and design 6.7); the future fix is
`statAt`/`readlinkAt` on the Dir fd. (5) There is no getcwd; a relative operand
is resolved by the kernel against the process cwd. (6) The fault state is five
ints (the gate plus one per call kind), not the design's single int; the runtime
data segment still grew by 32 bytes, the alignment the design allowed for.

**Version.** `OBO_VERSION` is 0.3 (design 6.9). `tests/sym_test.py` moved its
module constant and its three literal `compiler "0.3"` expectations in the same
change; every committed `.sym` fixture and golden under `tests/modules` was
rebuilt with the compiler line 0.3 (the line is not part of either hash), and
`--check` must not be run against pre-bump sym dirs. The seed is NOT promoted
on this branch: the orchestrator reviews, merges and promotes once. With the
seed still at 0.2, `make` builds the 0.3 compiler from source as before; the
seed-built `obo`, the 0.3 compiler's stage 2 and stage 3 are all the same
421,889-byte binary (checked with `cmp` on the branch), but `make promote` (and
its run_fork gate) remains the orchestrator's step after review.

**Measured (this branch, merged with main at `f87e144`, the P2b merge on top of `7b1c8b8`).** `make`;
`make tools`; `tests/tools.py --require-reference` 199/199; `--memory` 2/2;
`tests/io_test.py --rt --programs --tools` 0 failures; `tests/imports.py`
23/23 (9 stdlib importers, 3 tools, 3 tool-matrix checks, 7 collision/decoy
trees, 1 lib set); `tests/stdlib.py` 16/16 roots (1 support module); `tests/run_fork.py`
1,147/1,147 (1,130 + 16 error cases + 1 program); `tests/elf_test.py` (filesz 1,
memsz 67,328); `tests/port_compare.py` 1,164/1,164 (1,147 + 17), live peak
1,642,416 (budget 2,000,000; the 0.3 compiler compiling itself reports
1,642,160 live / 35,250,272 allocated, the port 1,642,416 / 35,253,408; +6,240 from the design baseline 1,636,176 and
+6,080 from the P2b worktree's 1,636,336; allocation 35,253,408, +176,800);
`OBO_REF=seed/obo tests/port_compare.py` 1,025/1,025 pre-promote (the seed is
0.2, so the version-gated skip applies) and 1,042/1,042 with a temporary 0.3
seed copy (the post-promote simulation); `tests/mutate_fork.py` 527/549 + 22 equivalent;
`tests/mutate_port.py` 495/495; `tests/mutate_io.py` 27/27; `tests/port_test.py`
516/516 identical (504 + the 12 new `.obx` files), 9 encoder groups;
`tests/sym_test.py` 27/27 at 0.3; `tests/sym_fuzz.py` 200/200; `tests/doc_check.py`
46 examples (45 + the metadata example); `tests/homes_check.py`;
`tests/homes_edge.py` 1,028 statements, 119 refused by both, 0 differ;
`bench/bench.py` runs to completion (obx built for it); `tests/meta_test.py`
27 checks (16 rt cases + 11 program invocations); `tests/mutate_meta.py` 30/31 + 1 and 9/12 + 3 as above. Tool
binaries unchanged (cat 40,961; wc 53,249; sort 53,249 bytes).

# eza E0: the first working binary (P4, done)

The eza/stdlib design v7 sections 2.2, 4.1/4.2, 5, 7, 9, 10.1 and 11.1 P4. No
compiler, runtime or seed change; the branch starts at the final P3 tree
(`e702029`) and merges main `e8703b2` (P3 merged, 0.3 seed promoted), then
rebuilds from that promoted seed: `seed/obo` == `obo` == stage 2 == stage 3,
421,889 bytes, sha256 `4892cea9…`. Nothing in this phase is promoted.

**What changed.** New `obo/cmd/eza.obx`, a headed root (`module eza`) importing
all nine `../lib/...` modules. `tools/build_tools.py` gains the `EZA`/`ALL`
split and the full library prerequisite set (nine modules; `build(['eza'], ...)`
is the one entry point), the Makefile gains `eza`, `eza-check` and `eza-memory`
with a private `build/eza/sym`, `tests/eza.py` builds the binary through
`build_tools.build` and gates it, and `tests/imports.py` compiles four tool
roots (25/25) plus the eza sort-import rule. Sixteen own usage-error goldens
are recorded under `golden/own/`; the two own memory cases are run by
`--memory`, not recorded as goldens. The fixture generator gains a `selflink/`
tree (`d/self -> .`) for the dot-target cases.

**Command line.** The parser is `cli.parse` over the complete E0 FlagSpec table:
every supported flag, plus every eza-valid flag this build rejects so its value
is consumed with eza's value mode before this build's own unsupported-option
error (`--width=N`, `-I=`, `-L x`, `--time-style=iso`). The supported surface is
`-1`/`--oneline`, restricted `-l`/`--long`, counted `-a`/`--all`, `-A`,
`-d`/`--treat-dirs-as-files`/`--list-dirs`, `-D`, `-f`, `-r`, `-s`/`--sort`
(name/filename, Name/Filename, size/filesize, modified/date/time/mod/new/newest,
extension/Extension/ext/Ext, type), `--group-directories-first` and
`--group-directories-last`, `-F`/`--classify` (`auto`/`always`/`never`,
`automatic` only after `=`), `-B`/`--bytes`, `-n`/`--numeric`, `--no-user`,
`--no-permissions`, `--no-filesize`, `--no-time`, `--time-style`, and the
no-op non-TTY forms of `--color`/`--colour`/`--icons`. `--help`, `--version`,
`-?` and `-v` are deliberately absent from the parser and take the ordinary
unknown-argument path.

Value deduction follows eza's last-occurrence rule by probe, not only the
design: `--sort=foo --sort=name` lists (the earlier bad value is ignored) and
`--sort=foo --sort=bar` reports `"bar"`; the same holds for `--classify`,
`--color`/`--colour` and `--icons`. The cli accept lists for those flags are
therefore empty and `deduce` validates the effective value with eza's exact
no-setting/choices wording. `--sort=default` is not an eza value and takes the
same wording; E0's valid-but-later values (`.name`/`.Name`, changed/accessed/
created, inode, none, age/old/oldest) take this build's own message. Time-style
is a documented narrowing: `long-iso` is required with `-l`, `long-iso` and
`default` are accepted and ignored without it, and the other eza styles are
this build's error.

**Model and ordering.** One operand or none (zero means `.`; multiple operands,
the stdin file-name mode, tree, grid and colour are out). Operand existence is
`lstat`, so a dangling symlink is listed; a directory operand that is a symlink
to a directory is followed for listing unless `-d`. A directory listing shows
names only, an operand shows `parentPrefix(parent) + name` with the whole
parent escaped once. A parented `..` operand keeps its prefix (`-d sub/..` is
`sub/..`, `-d 'x y/..'` is `'x y'/..`, `-d /..` is `/..`); only an empty
parent makes `-d ..` print `..`. This is a P0-probe contradiction of design
7.2's "`.`/`..` return themselves" and is tested by committed
`dirs/d_sub_dotdot`, `dirs/d_spaced_dotdot`, `dirs/d_dot_dotdot` and live
root/dotdot cases.

Synthetic `.`/`..` records are built before the real entries when `-a` is
counted twice, are exempt from hidden filtering, and obey `-D`/`-f`. The dots
are real entries in eza's model: `.` is `lstat(operand)` (so for a
symlink-to-directory operand it is the link itself, `. -> target`, not the
target directory), `..` is `lstat(join(operand,".."))`, and their kind/mode/
size/time/uid drive type sorting, filtering and grouping. Target display and
classification reorient the raw target against `dir.path` itself, not its
parent — eza's File-at-dir.path quirk — so `-aa links/realdirlink` is
`. -> realdir` with no classify marker, while `-aa selflink/d/self` is
`. -> .`, classifies and groups as a directory; a trailing slash resolves the
operand first. The `.` extension key is `File::ext(operand)` and `..` is None,
never the target's extension. These are P0-probe contradictions of design
7.2/7.3, covered by the committed `aa_symlink_dir`, `aa_selflink`, `-aa -D/-f`,
type/extension/group and long cases.

Sorting permutes index arrays through `lib/sort.obx`: a stable field sort and,
under `-r`, a stable descending comparison, then the directory grouping (first
wins when both grouping flags are given; a real symlink to a directory groups
as a directory, while the synthetic dot follows its operand-base target
above). The `-r` rule follows the P0 live probe, not the design
note's vector reversal: comparator-equal keys keep readdir order (probed on
equal sizes, fold-tied names, and the extension/type name fallbacks), while
distinct keys sort descending. The committed goldens have distinct keys, so
both rules agree there; live tie cases (`names/tie_Aa_reverse`,
`names/tie_pic_reverse`, `sortkeys/size_reverse_ties`,
`sortkeys/modified_reverse_ties`) and the regenerated REFERENCE.md
`size_reverse_ties`/`modified_reverse_ties`/`tie_*_reverse` probes pin it.
Extension/Ext use the design's three-pass pipeline and the folded or
case-sensitive name fallback; type pre-orders by case-sensitive natural name then rank. Quoting is
`QuoteSpaces`; classify markers go on the target of a symlink and on the name
otherwise. Targets resolve as `paths::join(base, raw)` with `base` the listing
operand (including the synthetic `.`, hence the quirk above) or the single
operand's parent, and are classified with a following `stat`, while the link's
own cells stay `lstat`; a broken target shows the whole raw target escaped and
no marker, and an unreadable target shows no arrow.

Restricted long is `-l --bytes --time-style=long-iso` with `-n` or `--no-user`;
the columns are permissions (`fmt::modeString`), right-aligned byte size with
`-` for non-regular entries, optional left-aligned numeric uid, left-aligned
long-iso UTC date (`-` before the epoch), and the name. `--no-permissions`,
`--no-filesize` and `--no-time` suppress a column and its separator; with all
suppressed the line is the name alone. Widths are computed over the displayed
set, and a `-d` invocation uses its one entry. `-aa -l` on a real directory is
live-only (the dot rows are the listed directory and its host parent); the
committed symlink-operand long cases have fixture-owned dot rows.

**Errors.** eza-shaped statuses: 0 success, 1 write error (bare
`No space left on device (os error 28)`), 2 input metadata failure
(`"path": ... (os error N)` from `lstat`), 3 usage/unsupported, and the
permission class as the P0 probe recorded it: a permission-denied directory
prints the warning and the `Skipped 1 directories ...` summary but keeps
status 0. `SIGPIPE` keeps the disposition it inherits: E0 installs no
handler, so the default parent gets the probe's signal 13, while an inherited
ignore turns a closed pipe into an EPIPE write error.

**Task-scope decisions vs the design note (no design edits).** The P4 scope
asked for `--no-permissions`, `--no-filesize`, `--no-time` and
`--group-directories-last`, which design v7 lists as later/rejected (§7.8,
§7.11). They are implemented and their output matches the pinned 0.23.3
reference byte-for-byte on the live probes, including `-r` and `-aa` for the
grouping flag and `-n`/`--no-user` interactions for the suppressed columns.
Everything else outside E0 is rejected with this build's own message.

**Measured (this branch).** eza binary 126,977 bytes (static, runtime
included); the pinned reference binary is 2,294,768 bytes. On 10,000 entries
under `OBO_STATS=1 OBO_CHECK=1 OBX_GC=poison`: restricted long peaks at
2,185,008 live heap bytes (16,166,544 allocated) and 2,432 KiB max RSS; the
name-only listing peaks at 2,184,384 (6,565,280 allocated) and 2,432 KiB.
`EZA_PEAK_MAX` is 2,752,512 bytes (the first long measurement plus a 25.97%
margin). GNU time on
the same tree measures the reference at about 24.3-25.4 MB (long) and 12.0 MB
(names) max RSS across the recorded gate runs (for example 24,760/11,992 and
25,388/11,988 KiB); our tool is about 2.4 MB in both cases. Reference memory is
printed for comparison and never gated.

**Gates (this branch).** `make`; `make tools`; `make eza`;
`tests/eza.py --require-reference` 272/272 non-memory cases (214 exact
reference goldens replayed against the pinned reference, 42 live cases, 16 own
goldens; 274 cases including the 2 memory cases), 0 failed; `tests/eza.py
--memory` 2/2 with the peaks/allocations above and no leak or double-close
line; `tests/tools.py --require-reference` 199/199; `tests/tools.py --memory`
2/2; `tests/io_test.py --rt --programs --tools` 0 failures; `tests/imports.py`
25/25 (9 stdlib importers, 4 tools, 4 static tool-matrix checks, 7
collision/decoy trees, 1 lib set; the P3 pre-eza count was 23/23);
`tests/stdlib.py` 16/16 roots (1 support module); `tests/run_fork.py`
1,147/1,147; `tests/elf_test.py` (filesz 1, memsz 67,328);
`tests/port_compare.py` 1,164/1,164, live peak 1,642,416 (budget 2,000,000)
and 35,253,408 allocated; `OBO_REF=seed/obo tests/port_compare.py`
1,042/1,042 with the promoted 0.3 seed (no version-gated skip remains);
`tests/mutate_fork.py` 527/549 + 22 equivalent; `tests/mutate_port.py`
495/495; `tests/mutate_io.py` 27/27; `tests/mutate_meta.py` 30/31 + 1 and
9/12 + 3; `tests/port_test.py` 516/516 identical (9 encoder groups);
`tests/sym_test.py` 27/27 at 0.3; `tests/sym_fuzz.py` 200/200;
`tests/doc_check.py` 46 examples; `tests/homes_check.py`;
`tests/homes_edge.py` 1,028 statements, 119 refused by both, 0 differ;
`tests/meta_test.py` 27 checks; `bench/bench.py` runs to completion (obx built
for it). Beyond the committed matrix, a 600-combination differential over the
supported flags and E0 sort fields (including `-r`, the grouping flags, `-F`,
`-D`/`-f`, the dot filters and restricted long) and a 112-case operand-display
and `-d` differential over relative, normalized, spaced, absolute and `..`
operands matched the pinned reference byte-for-byte in stdout, stderr and
status; the B1/B2 fixes and the self-link/spaced-target dot quirks were then
verified against the reference on the operand, symlink, group, filter, type,
extension and long forms, and REFERENCE.md was regenerated with the pinned
binary (360 probes, including the equal-key `-r` and B1/B2 probes). The
compiler, runtime and seed are untouched by P4.

**Deviations and limits.** (1) Non-TTY only: on a TTY E0 prints lines with no
colour until E2. (2) Zero operands mean `.`; eza instead reads stdin names when
stdin is not a TTY (P0 records this; E0 does not implement it). (3) Restricted
long only: plain `-l` is an error. (4) Dates are UTC (`TZ=UTC` pinned); eza
uses local time. (5) `--time-style` accepts only `long-iso` (with `-l`) or
`long-iso`/`default` (without), a narrowing of eza's accept-and-ignore rule.
(6) No user/group names, human sizes, device major/minor, xattrs, ACLs or
invalid-UTF-8 lossy display. (7) The path-based `fs` API keeps the 4096-byte
joined-path limit and the getdents-to-stat race (design 6.7). (8) Permission
status follows the P0 probe (0), not the man page's 13. (9) Multiple operands
are this build's own usage error. (10) `-r` is a stable descending comparison,
not the design note's vector reversal (which reverses ties); the live
reference probes settle this and the tie cases are live-only, so no recorded
byte changes. (11) The long cells live in the `Entry` records (one lazy lstat
per entry) rather than the design's parallel int arrays; the measured
10,000-entry peak is recorded and capped, and the user-visible output is
unchanged. Sort-key arrays are locals of the ordering helper and are freed
before rendering. (12) Two further design contradictions follow the P0 probe:
a parented `..` operand keeps its parent prefix (design 7.2 said `.`/`..`
return themselves), and a synthetic `.` on a symlink-to-directory operand is
the symlink, with its target reoriented against the operand path itself
(design 7.3 modeled it as a directory). Both are covered by committed goldens,
live cases and REFERENCE.md probes.

**Residual risks.** The four live root-target cases (`eza /tmp`,
`eza -F=always /tmp`, `eza /` and `eza -F=always /`) compare sequential
listings of the host's directories; on a busy host another process can create
or remove an entry between the two runs. The harness now re-runs a mismatching
live-only case once; only when a program's own output changed between the two
attempts does it skip with "live case unstable: the host listing changed
between runs" (never a pass, and a stable difference remains a failure). The xattr `@`
marker is not queried; the harness skips long goldens when the host adds
ambient xattrs. The memory cap tracks a fixed 10,000-entry fixture; a
compiler/codegen change that changes the runtime's per-object sizes moves the
peak and must be re-measured rather than raised (the cap is not the compiler's
`PEAK_BUDGET`). No E1/E2 feature is present, and the binary is not promoted.
# Environment metadata: slots 37-38 (P5a, done)

The eza/stdlib design v7 sections 5.8, 8.5 and 11.1, on the promoted 0.3 seed
(`e8703b2`, 421,889 bytes). Two append-only slots, no renumbering of 0-36; no
Result cell, no descriptor, no new descriptor kind, no allocation in isatty and
no runtime-retained program pointer.

| Slot | Builtin | Result |
|---|---|---|
| 37 | `isatty(fd)` | `bool` |
| 38 | `getenv(name)` | `?[]byte` |

## What changed

- `runtime/runtime.c`: `rt_isatty` is false for fd < 0 before any syscall and
  otherwise `ioctl(fd, TCGETS 0x5401, buf) == 0`, with a 64-byte buffer for the
  kernel's 36-byte `struct termios` and the design's EINTR retry. It never reads
  the fd table: a pipe, a regular file, `/dev/null`, a directory or a tracked
  closed descriptor is false (ENOTTY/EBADF), and a number the kernel reused is
  observed as it is. `rt_getenv` records `env_base = sbase + 8*(argc+2)` in
  `rt_init`, rejects an empty name, an embedded NUL or any `=` in the name,
  scans `envp` for the first `name=` entry and copies the value into an owned
  byte array; a present empty value is a non-nil empty array and absent is
  nil. Nothing of `envp` is retained. The test-only `OBO_META_FAULT=eintr`
  hook now also injects one EINTR for the ioctl (five fault ints, still inert
  unless `OBO_CHECK=1`). Runtime text grew 448 bytes (15,472 -> 15,920); the
  data image is unchanged at 67,328 bytes (one global word and one fault int
  fit the existing alignment).
- `runtime/entry.S`: slots 37/38 appended in both sequences, after
  `rt_read_link`.
- `src/obo.obo`: the universe and callExempt names; `getenv` also leaves the
  `plausible` scalar guess (isatty's bool is a safe guess); builtin branches
  with the fd materialised and the name pinned; `rtCall1` for both; slots
  37/38. No `resultCall` and no descriptor, so no shared-cell contract is
  involved. `OBO_VERSION` is 0.4.
- `lib/env.obx`: `get`/`has`/`value`/`getOr` exactly as 5.8, with `has`'s
  required local and no helper named `string` (the B3 rule); `lib/io.obx`:
  `isTty`.
- Tests: `rt_meta_test.c`'s `isatty_checks` (negative fds, a regular file,
  `/dev/null`, both pipe ends, a directory, a tracked closed fd, a reused
  number, and the pty slave as the one true answer), `getenv_real` (rt_init's
  `env_base` formula and the fixed `OBO_META_ENV_*` environment) and
  `getenv_crafted` (first match, empty names, `=`, prefixes, a malformed entry,
  an embedded NUL, and owned copies that survive env mutation); `meta_test.py`
  fixes the environment for every case and adds `tests/meta/env_api.obx` and
  `env_shadow.obx` (the B3 orders), `tests/run/env.obx` and
  `tests/errors/env.obx`. `mutate_meta.py` gains 14 runtime/entry.S and 12
  compiler mutants: the compiler runner replays `tests/run/env.obx` and
  `tests/errors/env.obx`, and the runtime runner judges the mutated runtime
  with `rt_meta_test.c` and the `tests/meta` corpus (including
  `env_api.obx` and `env_shadow.obx`).

## Gates (this branch)

**Measured.** `make`; `make tools`; `tests/tools.py --require-reference`
199/199; `--memory` 2/2; `tests/io_test.py --rt --programs --tools` 0
failures; `tests/imports.py` 26/26 on the P4-merged tree (10 stdlib importers, 4
tools, 4 tool-matrix checks, 7 collision/decoy trees, 1 lib set; 24/24
before the eza tool and its sort-import check joined); `tests/stdlib.py`
16/16 roots (1 support module); `tests/run_fork.py` 1,157/1,157 (1,147 + 9
error cases + 1 program); `tests/elf_test.py` (filesz 1, memsz 67,328);
`tests/port_compare.py` 1,174/1,174 (1,052 flat + 122 module trees), live peak
1,645,424 (budget 2,000,000, 354,576 spare) and 35,359,504 allocated;
`OBO_REF=seed/obo tests/port_compare.py` 1,040/1,040 pre-promote (the 0.3 seed
cannot parse `io.obx`'s isTty or the env module, so the two P5a case files
and the two cases importing `io.obx` are version-gated out) and 1,052/1,052 with a
temporary 0.4 seed copy (the post-promote simulation; the real 0.3 seed was
restored, sha256 `4892cea9...`); `tests/mutate_fork.py` 527/549 + 22
equivalent; `tests/mutate_port.py` 495/495; `tests/mutate_io.py` 27/27;
`tests/mutate_meta.py` 69 mutants: runtime/entry.S 43/45 caught + 2
equivalent, compiler 17/24 caught + 7 equivalent, 0 missed, 0 stale, 0 broken
(the previous P3 counts were 30/31 + 1 and 9/12 + 3); `tests/port_test.py`
520/520 identical (516 + 4 new `.obx` files), 9 encoder groups;
`tests/sym_test.py` 27/27 at 0.4; `tests/sym_fuzz.py` 200/200;
`tests/doc_check.py` 47 examples (46 + the environment example);
`tests/homes_check.py`; `tests/homes_edge.py` 1,028 statements, 119 refused by
both, 0 differ; `bench/bench.py` runs to completion (obx built for it);
`tests/meta_test.py` 32 checks (19 rt cases + 13 program invocations). Tool
binaries unchanged (cat 40,961; wc 53,249; sort 53,249 bytes).

**After merging P4 (main `e53a5e8`, eza E0).** The P4 eza gate re-runs under
the 0.4 compiler and is unchanged: `tests/eza.py --require-reference`
272/272 against the pinned eza 0.23.3 (sha256 `10457dde...`), `--memory`
2/2 (10,000-entry live peaks 2,185,008 long / 2,184,384 names, under
EZA_PEAK_MAX 2,752,512; max RSS 2,432 KiB both, reference about 24.2/12.0
MB), eza binary 126,977 bytes, reference binary 2,294,768 bytes; the
reference goldens are unchanged and the E0/P5a features coexist (eza uses
no `env`/`isatty` yet, and the P5a builtins do not change the produced eza
image size). All other P5a counts above are unchanged on the merged tree.

**Fixed point without promote.** The seed-built `obo`, the 0.4 compiler's
stage 2 and stage 3 are all the same 425,985-byte image (sha256
`d9dc0e93...`; checked with `cmp`), against the 421,889-byte 0.3 seed. The
compiler source is 10,516 lines / 385,978 bytes (P3: 10,492 / 384,760).

## Deviations and limits

- The getenv embedded-NUL rule is defence in depth and has an equivalent
  mutant: an environment entry cannot contain a NUL, so the byte scan cannot
  match past one either way. The rule still returns nil in the test.
- The pty master's TCGETS also succeeds on this host's kernel (Linux 6.18);
  the test asserts the slave, which is always a terminal, and keeps pipe, file,
  `/dev/null`, a directory and a closed descriptor as the false cases. That is
  the design's portable rule; the master is deliberately not asserted.
- The pre-promote seed comparison is 1,040/1,040 rather than the P3 1,042: the
  0.3 seed cannot parse `io.obx` once it calls isatty, so 4 files / 12 cases
  are version-gated out of it: `tests/run/env.obx` (1 case),
  `tests/errors/env.obx` (9), and `tests/run/io_reader.obx` (1) and
  `tests/run/io_writer.obx` (1), which import `io.obx`. The default
  comparison always covers them, and a temporary 0.4 seed copy compares the
  whole flat corpus (1,052/1,052), so the promote loses nothing.
- A name containing `=` cannot be passed through `subprocess`'s env mapping
  (Python rejects it), so the `=` rule and first-match order are tested with
  the crafted `envp` in `rt_meta_test.c`; the fixed real environment covers
  present, empty and absent.
- `tests/run/env.obx` shares its file name with the library module it imports.
  run_fork, port_compare and mutate_meta copy each root to a unique file
  name before compiling, so the import resolves to `lib/env.obx` and there
  is no cycle; the two meta_test roots are named `env_api`/`env_shadow`
  for the same reason. `env_base` stays a non-static global, as
  `rt_entry_sp` is; the image is a byte concatenation, so no program
  symbol can collide with it.

# Terminal-size metadata: slot 39 (P6a, done)

The eza/stdlib design v7 sections 6.1, 8.5 and 11.1, on the promoted 0.4 seed
(`829dd52`, 425,985 bytes). One append-only slot, no renumbering of 0-38; no
Result cell, no descriptor, no new descriptor kind, no allocation and no
runtime-retained program pointer.

| Slot | Builtin | Result |
|---|---|---|
| 39 | `terminalWidth(fd)` | `int` |

## What changed

- `runtime/runtime.c`: `rt_term_width` is 0 for fd < 0 before any syscall and
  otherwise `ioctl(fd, TIOCGWINSZ 0x5413, ws)`, returning the `ws_col` of the
  8-byte `struct winsize { u16 row, col, xpixel, ypixel }`. A failed ioctl
  (ENOTTY/EBADF) and a zero column count (unknown; a fresh pty may be 0x0) are
  0. It never reads the fd table: a closed descriptor is not a fatal use, and
  a number the kernel reused is observed as it is. The test-only
  `OBO_META_FAULT=eintr` hook injects one EINTR for this ioctl too (six fault
  ints, still inert unless `OBO_CHECK=1`). Runtime text grew 160 bytes
  (15,920 -> 16,080); the data image is unchanged at 67,328 bytes (the extra
  fault int fits the existing alignment).
- `runtime/entry.S`: slot 39 appended in both sequences, after `rt_getenv`.
- `src/obo.obo`: the universe and callExempt names and the builtin branch, with
  the fd materialised and `rtCall1` returning `int`. The int result is a safe
  `plausible` scalar guess, so no negative-list entry (isatty's bool is the
  same rule); no `resultCall` and no descriptor. `OBO_VERSION` is 0.5.
- `lib/io.obx`: `termWidth` (unchanged otherwise).
- Tests: `rt_meta_test.c`'s `termwidth_checks` (negative fds, a regular file,
  `/dev/null`, both pipe ends, a directory, a tracked closed fd, a reused
  number, the pty slave with TIOCSWINSZ sizes — the column, not the row — and
  a zero column count as unknown) and the terminalWidth call in `case_eintr`;
  `meta_test.py` adds `tests/meta/term_api.obx` (both wrappers on
  non-terminals), `tests/run/term.obx` and `tests/errors/term.obx`.
  `mutate_meta.py` gains 7 runtime/entry.S and 6 compiler mutants: the row
  instead of the column, an always-80 result, an off-by-one, TIOCSWINSZ for
  TIOCGWINSZ, a true answer for a negative fd, the removed EINTR retry and the
  slot 38/39 entry swap; the compiler runner replays `tests/run/term.obx` and
  `tests/errors/term.obx`, and the runtime runner's pty oracle is
  `termwidth_checks`. The older universe and callExempt mutant anchors were
  re-anchored to the new lines (they contain `terminalWidth` now), not skipped.
- Version: `OBO_VERSION` 0.4 -> 0.5; `tests/sym_test.py`'s constant and its
  `compiler "0.5"` literals, and every committed `.sym` fixture/golden, were
  updated in the same commit. Hashes do not cover the compiler line, so no
  interface hash changed.

## Gates (this branch)

**Measured.** `make`; `make tools`; `tests/tools.py --require-reference`
199/199; `--memory` 2/2; `tests/io_test.py --rt --programs --tools` 0
failures; `tests/imports.py` 26/26 (10 stdlib importers, 4 tools, 4
tool-matrix checks, 7 collision/decoy trees, 1 lib set); `tests/stdlib.py`
16/16 roots (1 support module); `tests/run_fork.py` 1,165/1,165 (1,157 + 1
run program + 7 error cases); `tests/elf_test.py` (filesz 1, memsz 67,328);
`tests/port_compare.py` 1,182/1,182 (1,060 flat + 122 tree, CLI and encoder
cases), live peak 1,646,976 (budget 2,000,000, 353,024 spare) and 35,355,536
allocated;
`OBO_REF=seed/obo tests/port_compare.py` 1,049/1,049 pre-promote and
1,182/1,182 with a temporary 0.5 seed copy (the post-promote simulation; the
real 0.4 seed was restored, sha256 `d9dc0e93…`);
`tests/mutate_fork.py` 527/549 + 22 equivalent; `tests/mutate_port.py`
495/495; `tests/mutate_io.py` 27/27; `tests/mutate_meta.py` 82 mutants:
runtime/entry.S 50/52 caught + 2 equivalent, compiler 21/30 caught + 9
equivalent, 0 missed, 0 stale, 0 broken (P5a: 43/45 + 2 and 17/24 + 7);
`tests/port_test.py` 523/523 identical (520 + 3 new `.obx` files), 9 encoder
groups; `tests/sym_test.py` 27/27 at 0.5; `tests/sym_fuzz.py` 200/200;
`tests/doc_check.py` 48 examples (47 + the terminalWidth example);
`tests/homes_check.py`; `tests/homes_edge.py` 1,028 statements, 119 refused by
both, 0 differ; `bench/bench.py` runs to completion (obx built for it);
`tests/meta_test.py` 34 checks (20 rt cases + 14 program invocations); tool
binaries unchanged (cat 40,961; wc 53,249; sort 53,249 bytes).

**eza E1 (after merging P5b).** `tests/eza.py --require-reference` 396/396
non-memory cases against the pinned eza 0.23.3 (sha256 `10457dde…`), 0
failed; `--memory` 4/4 with 10,000-entry live peaks 2,189,984 long /
2,189,360 names / 2,190,112 tree / 2,189,584 colour, under `EZA_PEAK_MAX`
2,752,512; max RSS 2,560 KiB each; eza binary 172,033 bytes (sha256
`6f0e8472…`), reference 2,294,768. On the merged tree `tests/tools.py
--require-reference` is 199/199, `tests/imports.py` 26/26, `tests/stdlib.py`
16/16 and `tests/doc_check.py` 48 examples.

**Fixed point without promote.** The seed-built `obo`, the 0.5 compiler's
stage 2 and stage 3 are all the same 425,985-byte image (sha256
`05542e89…`; checked with `cmp`), against the 425,985-byte 0.4 seed
(`d9dc0e93…`). The compiler source is 10,527 lines / 386,590 bytes (P5a:
10,516 / 385,978). Self-compile wall time is 0.13 s (5 runs, 0.12-0.16 s) and
max RSS 2,976 KB (3 runs), in line with P5a's host figures.

## Deviations and limits

- The pre-promote seed comparison is 1,049/1,049. The full flat corpus is
  1,060 (P5a's 1,052 + 8 new term cases: `tests/run/term.obx` and the seven
  `tests/errors/term.obx` cases). The 0.4 seed cannot parse `io.obx` once it
  calls `terminalWidth`, so it skips 11 of them: the three flat cases
  importing `io.obx` (`tests/run/env.obx`, `tests/run/io_reader.obx` and
  `tests/run/io_writer.obx`), `tests/run/term.obx`, and the seven
  `tests/errors/term.obx` cases. 1,060 - 11 = 1,049. The default comparison
  and the 0.5-seed simulation cover all 1,182, so the promote loses nothing;
  no test was weakened. The skip now requires the maximum of a file's own
  minimum and its imports' minimum, so `env.obx`'s explicit 0.4 does not hide
  `io.obx`'s 0.5.
- Two new compiler mutants are genuinely equivalent and recorded, as P5a's
  were: omitting `terminalWidth` from `callExempt` only changes the
  register-allocation pre-scan weighting, and not materialising the fd before
  a single-argument call is unobservable (there is no later argument, and
  `rtCall` loads the memory operand at the same point).
- The entry.S slot 38/39 swap is killed by `env/api` before `term_api`
  (both go through the table); the runner reports the first failure. The
  wrong-slot compiler mutant (slot 38, `getenv`) crashes because `rt_getenv`
  dereferences the descriptor number as a name pointer; the changed outcome
  (exit -11) is a kill, not a broken mutant.
- The pty oracle sets `TIOCSWINSZ` before reading, because this host's fresh
  pty reports 0x0; it checks a zero column count is 0 (unknown) and that
  setting the size from either end updates the same terminal. The program
  corpus cannot open a pty from obo (there is no ioctl builtin), so its pty
  cases stay in `rt_meta_test.c`.
- `tests/run/term.obx` shares its name with the builtin feature but no module;
  `tests/meta/term_api.obx` is named apart from `io.obx` so the import
  resolves to `lib/io.obx`.

# eza E1: colour, tree and `--no-quotes` (P5b, done)

The eza/stdlib design v7 sections 7.6/7.7/7.11, 8.1/8.2, 9.3/9.4/9.7 and
11.1 P5b, on the promoted 0.5 seed (main `cc6c420`; P0-P6a merged). P5b
itself makes no compiler, runtime, seed or Makefile change: every E1 feature
is in `obo/cmd/eza.obx` and its tests.

**What changed.** `cmd/eza.obx` gains the E1 view rules and the colour
machinery. The effective view is eza's last-of-`-l`/`-1`/`-T` flag (a last
`-1` makes `-T` a plain lines listing; a last `-T` with any `-l` is a long
tree), `-T` sets the tree DirAction, `-L` is parsed only when the tree action
is active (Rust `usize::from_str` wording; accepted up to 2^64-1, ignored
without `-T`), tree plus two `-a` is `Option --tree is useless given --all
--all`, and `--no-quotes` selects the outer NoQuotes style. `--color`/
`--colour` accepts `always` (no longer an own error); the default is auto, or
never when `NO_COLOR` is present at all, and an explicit value overrides it.
`auto` calls the P5a `io::isTty(stdout())` builtin.

The colour layer mirrors the reference's nu-ansi-term 0.50.1 model: a `Style`
of eight attributes plus background/foreground; the default filekind,
file-type and `LS_COLORS`/`EZA_COLORS` palettes (di, ln, ex, fi and `*.EXT`);
a Pair parser with zero-trimmed SGR numbers, 256-colour and RGB forms,
canonical re-serialisation (attributes, background, foreground), non-SGR
values overriding a slot with the empty style, empty pairs dropped, and the
last matching extension pair winning; EZA over LS, EXA as the fallback (an
empty present EZA still wins), and `reset`/`reset:` disabling the default
file-type palette. Every line is built as a vector of styled text bits and
emitted with the reference's AnsiStrings difference rule (one prefix, the
minimal diff between adjacent pieces, one final reset), which is what makes
the SGR bytes exact. The default file-type tables from
`src/info/filetype.rs` are constants; the compiled-source heuristic is the
one part not implemented. Long cells stay plain (long-column colour is
deferred with a written note); names, targets, arrows, operand parent
prefixes and tree glyphs are coloured.

The tree is the details view with names only unless `-l` is effective. It
uses an explicit frame stack (no recursion, no descriptor held across levels):
each directory is read, filtered, prepared, sorted and closed before its first
child prints; `-L` stops when `level <= depth`; only lstat directories are
descended; empty directories print their line; an unreadable directory prints
a red `<Permission denied (os error 13)>` child and continues with status 0.
Long tree widths are global across the whole tree (a measuring pass runs
first) and an error row is padded to the table width. Filtering follows the
reference's recursion rules (`-D` alone keeps directories; `-f` drops the
directory root in the files-list filter; both keeps everything at a level).

**Tests.** `tests/eza.py` gains the `colour` and `tree` families and seven
`--no-quotes` quoting cases: 400 cases, 396 non-memory (329 exact reference
goldens, 54 live including eight pty auto-colour cases, 13 own usage errors)
and four memory cases (the P4 pair, the tree 10,000-entry case and the
coloured 10,000-entry case). The
`colours` fixture pins one name per default file-type category, the hidden
file, an executable, styled and broken targets; the colour goldens are exact
SGR bytes recorded from the pinned binary. TTY cases run on a raw 80x24 pty
and are live-only; `--record` refuses pty goldens. `probe.py` has a new `e1`
group (29 re-probes) and `tests/eza/E1-PROBES.md` records the exact palette,
LS/EZA semantics, view-flag rules, permission child and `--no-quotes` bytes
from the same pinned sha256. The old own cases `own_no_quotes`,
`own_color_always` and `bad_level` are gone (`bad_level` is now a reference
golden); no E0 case was weakened.

**Review fixes.** A file named `#` is Temp (the reference's starts/ends-with
rule has no length guard); `-L +` is Rust InvalidDigit, not Empty; tree `-r`
uses the reference's ascending-then-vector-reverse order (which reverses
ties) before the dirs-first pass, while lines/long keep the stable descending
comparison; displayed option values use Rust Debug escaping; stale RGB
components are cleared when a later SGR token re-kinds a colour; and the
colour 10,000-entry memory case is under the unchanged cap. The theme
configuration (`EZA_CONFIG_DIR`, `XDG_CONFIG_HOME`, `~/.config/eza/theme.yml`)
is not read.

**Probe-wins deviations.** Tree plus `-d` does **not** conflict without
`EZA_STRICT` (the reference probe prints the tree; strict mode is out), so
this build does the same. Hidden files, setuid/setgid/sticky names and
broken-link *names* have no separate SGR in the lines/tree palette (the
link name stays cyan; the broken arrow is red and the broken target
red-underlined). `*.EXT` matching is by the reference's definition order, not
longest suffix. Long-column colour, the EZA UI keys (`xx`, `or`, `pi`/`so`/
`bd`/`cd`, the permission/size/user/date/git/filename styles), real `?*[]`
globs, mount-point style, unknown LS keys as filename globs, the
compiled-source heuristic and non-UTF-8 lossy display (E2) remain out.

**Measured (this branch, merged 0.5 tree).** eza binary 172,033 bytes
(sha256 `6f0e8472…`; static, runtime included; reference 2,294,768).
`tests/eza.py --require-reference`: 396/396
non-memory, 0 failed (reference sha256 `10457dde...`); `--memory`: 4/4, live
peaks 2,189,984 (restricted long), 2,189,360 (names), 2,190,112 (tree) and
2,189,584 (colour) bytes, max RSS 2,560 KiB each, under the unchanged
`EZA_PEAK_MAX = 2,752,512`; the reference measures about 25/12/14/12 MB max
RSS on the same trees.

**Gates (this branch).** After merging main `cc6c420` (0.5), the required
P5b gate was re-run: `make`; `make tools`; `make eza`;
`tests/eza.py --require-reference` 396/396; `--memory` 4/4;
`tests/tools.py --require-reference` 199/199; `tests/imports.py` 26/26;
`tests/stdlib.py` 16/16; `tests/doc_check.py` 48 examples. The compiler-suite
figures below were measured on the pre-merge 0.4 tree
(`829dd52`) before the coordinator scoped the P5b gate to the E1
differential, tools/stdlib, the compiler-unchanged confirmation and memory:
`tests/tools.py --require-reference` 199/199; `--memory` 2/2;
`tests/io_test.py --rt --programs --tools` 0 failures; `tests/imports.py`
26/26; `tests/stdlib.py` 16/16; `tests/run_fork.py` 1,157; `tests/elf_test.py`;
`tests/port_compare.py` 1,174/1,174 live (peak 1,645,424;
35,359,504 allocated) and the seed comparison 1,052/1,052;
`tests/mutate_fork.py` 527/549 + 22 equivalent; `tests/mutate_io.py` 27/27;
`tests/mutate_meta.py` runtime 43/45 + 2 equivalent and compiler 17/24 + 7
equivalent; `tests/port_test.py` 520/520; `tests/sym_test.py` 27/27;
`tests/sym_fuzz.py` 200/200; `tests/doc_check.py` 47 examples;
`tests/homes_check.py` and `tests/homes_edge.py` 1,028 statements / 119
refused / 0 differ; `tests/meta_test.py` 32; `bench/bench.py`.
`tests/mutate_port.py` was started but stopped mid-run and is **not**
claimed.

# eza E2: grid, width sources, UTF-8 and lossy names (P6b, done)

The eza/stdlib design v7 sections 8.3/8.4, 9 and 10, on a tree that merges
`eza-p5b` HEAD `036174d` (the `021f4ee` E1 review fixes plus main `cc6c420`,
including the P6a review nits and the promoted 0.5 seed) into the P6b branch.
P6b makes no compiler, runtime, seed or Makefile change; the merge's only
non-eza files are main's P6a review fixes and seed, which were already
reviewed on their own branches.

**What changed.** `lib/strings.obx` gains a UTF-8 decoder (`utf8Step`), Rust
`from_utf8_lossy` maximal-subpart `lossy()`, `isValidUtf8`, display widths
(`displayWidth`, `quotedWidth`) from a generated unicode-width 0.1.14 range
table (the crate behind ansi-width, which eza's grid measures with), and
code-point `natcmp`/`natcmpFold` with Rust `char::is_whitespace` skipping and
simple lowercase (ASCII, Latin-1, Latin Extended-A, basic Greek/Cyrillic,
fullwidth A-Z). The table was verified over every Unicode scalar value against
the crate (identical hash and counts: 6,261 zero-width, 923,290 width-1,
182,512 width-2, one width-3); the folded comparator leaves 1,234 scalar
values (542 ranges) outside its blocks unfolded, with U+0130 the only
multi-character expansion left unfolded (a pairwise natcmp matrix against
natord-plus-plus 2.0.0 agrees except the U+0130 pairs).
`cmd/eza.obx` gains eza's `Mode::deduce` view rules (a last `-1` is Lines;
otherwise any `-l` is a long view whose subtype is chosen by the last of
`-T`/`-G` — long tree, GridDetails/rejected, or plain long — and with no `-l`
the last of `-T`/`-G` picks tree or grid; no view flag grids when
terminal-ish), `TerminalWidth` (`--width` >= 1
Set, 0 Automatic and suppressing `COLUMNS`; `COLUMNS` Set including 0;
invalid-UTF-8 COLUMNS ignored; automatic width from `io::termWidth(1)` only
on a non-zero terminal, else the 80 fallback for explicit `-G`), automatic
classify from the same terminal-ish flag but automatic colour from real
stdout isatty, the uutils_term_grid 0.7.0 layout (2-space filling, TTB or `-x`,
last fitting column count, no truncation, unpadded last column), grid cells
with the marker on the link and the broken-link name in the broken style, and
lossy names/operand parents/targets for display, sorting, ext keys and colour
while raw bytes stay for lookup. `-l` with `-G` (GridDetails) is rejected with
this build's own message. A rows=0 terminal is invisible to `io::termWidth`
(only columns are exposed): the reference treats it as non-terminal-ish (Lines
by default, no auto-classify markers anywhere, explicit `-G` still grids),
while this build treats it as terminal-ish; the `pty_zero_rows*` probes record
the reference (accepted 0.5-runtime limitation).

**Merge resolutions.** `cmd/eza.obx` keeps the E2 view/width/grid code and the
E1 fixes: the removed `treeFlag`, `debugText` Debug escaping, the `-L +`
InvalidDigit rule, `sortFieldOrder`/`sortEntriesTree` (tree `-r` sorts
ascending then reverses the vector; lines/long keep stable descending), the
`#` Temp rule and the colour-kind setters. The E1 fix was propagated to E2:
`--width`/`COLUMNS` parse errors now ASCII-Debug-escape their value and a
lone `+` is InvalidDigit, with goldens added; non-ASCII non-printables
(`\u{a0}`, combining marks, format controls, U+0085, U+2028, U+FEFF) are
copied raw where Rust's Debug escapes them, a documented divergence pinned by
the `width_nonascii_escape` probe. Grid sorting uses the lines/long model
(probed `--sort=size -r` keeps tie readdir order in a grid; the tree is the
only vector-reversing view). `tests/eza.py`, `fixtures.py` and `probe.py`
combine both case sets; `E2-PROBES.md` is regenerated with the merged
`--title` mechanism.

**Probes and deviations.** Design 8.3's claim that `--width` enables automatic
colour is wrong: only classify/icons use `is_a_tty` (probed). `COLUMNS=0` is a
real Set(0) grid while `--width=0` is Automatic and suppresses COLUMNS;
`terminal_size` is None for a zero row or column, so a 0-column pty is not
terminal-ish (a zero-row terminal cannot be seen through `io::termWidth` and is
a documented limitation). The reference panics from a usize overflow for
widths within two of `usize::MAX`; this build clamps at 2^40 and prints a
one-row grid instead. GridDetails and icons stay out.

**Tests.** 551 cases: 546 non-memory (462 exact reference goldens, 72 live
including 22 pty, 12 own) and five memory cases (long, names, tree, colour,
grid). New families are `grid` (102) and `utf8` (34) plus seventeen E2 error
cases; new fixtures are `gridmix`, `utf8`, `utf8bad`, `utf8tie`, `utf8fold`
and `utf8ws`. Goldens were recorded from the pinned reference (sha256
`10457dde...`); `--reference=none` passes 474 goldens/own and skips the 72
live cases.

**Measured (merged tree).** eza binary 212,993 bytes (sha256 `53133d23…`;
static, runtime included; reference 2,294,768; the hash moved from the first
merged build because source line numbers are embedded in the image and the
review-fix comments changed them); tools cat 40,961, wc 45,057, sort 77,825
bytes (main's wc-fast is in wc; P6b's width functions add 17,152 bytes and
the other strings layer 7,840 bytes to strings-importing tools, sort's
+24,576-byte file growth being page-rounded). `tests/eza.py
--require-reference`: 546/546 non-memory, 0 failed; `--memory`: 5/5, live
peaks 2,190,032 long / 2,189,408 names / 2,190,160 tree / 2,189,632 colour /
2,189,808 grid, max RSS 2,560 KiB each, under the unchanged
`EZA_PEAK_MAX = 2,752,512`; the reference measures about 24.6/12.0/14.2/12.1/
12.0 MB max RSS on the same trees.

**Gates.** After merging main 557936f (wc-fast plus the bench/cli suite):
`make`; `make tools`; `make eza`; `tests/eza.py --require-reference` 546/546;
`--memory` 5/5; `tests/tools.py --require-reference` 222/222 (wc-fast);
`tests/tools.py --memory` 2/2; `tests/stdlib.py` 16/16; `tests/imports.py`
26/26; `tests/doc_check.py` 48 examples. Under the scoped P6b instruction the
compiler mutation/port/sym suites were not re-run and are not claimed.

# Methods: receivers, static dispatch and symbol format 2 (P7/0.6, done and promoted)

**What changed.** `fn (r: T) m(...)`, `fn (r: &T) m(...)` and
`fn (mut r: &T) m(...)` declare a method on a local record or closed type;
`place.name(args)` calls it. The receiver is hidden parameter `p0`, with
`pmodes[0]` 0/1/2, so the existing ownership, borrow, effect-summary, pfree and
pf machinery applies unchanged (bit 0 is the receiver). Static dispatch only: one
method per `(Type, name)`, one parent step for a variant, no method values, no
chaining (`b.m().n` is `expected ';' or newline, found .`), and an owned result
of a function call is still the store-first error before the member name.

The compiler keeps one flat `methodSyms` registry of `Sym`s in declaration order,
keyed logically by `(canonical Type, name)`; `Sym.recv` is `tVoid` for a plain
function. `findMethodIn/findMethod/methodKey/methodDisplay` are the table. The
parser breaks the postfix designator at `. name (`, runs the existing tail on the
receiver (so a field, element, reference or handle receiver is finalized and its
value use recorded, including when the statement was parsed as an assignment
target), then calls `methodCall` and returns its result directly. `callFn`
became a wrapper over `callFnWith(f, recv, hasRecv, line, col)`: the receiver is
converted, checked, held/moved and pushed as argument 0; every plain-function
label and index is unchanged. Receiver place registers are excluded from the
saved-register set (home registers used as an index are not), and no temporary
`Item` owns the receiver.

The one new soundness rule is an owned receiver that is the whole object named by
a handle (`h.m()`): the dereference is a register temporary and `consume` has no
owner to mark, so it is refused explicitly. A field or element through a handle
(`h.child.m()`, `h.items[i].m()`) is a `mem`/`elem` item and takes the existing
field/element move error; `&`/`mut` receivers through handles follow the existing
pure-callee rule. Methods are declared only on complete local record/closed types
(aliases transparent); fields and variants share the member namespace; methods
are a separate namespace from top-level names; `pub` is sticky and an exported
method needs an exported receiver. Declaration order is source order with forward
headings per `(Type, name)`. The while-condition lift, the home-retry fixup
cleanup, the module-end heading check and `checkExports` (with
`truncate(closureSeen, 0)` per method) all scan `methodSyms`; `waitFn` now holds
symbols rather than globals indexes.

The symbol format is `obosym 2`, compiler `0.6`, ABI `1`: a method is emitted as
`fn ([mut ]p0: [&]T) name(p1: ...): Ret summary` in a method group between the
globals and the functions, and the receiver is always parameter 0. `--check`
reconstructs methods and checks calls against them, including a source-less
dependency; a private method of a foreign type gives the same module-member
error in a full build and in `--check`. A method named `main` is readable and is
not the program entry. `--version` is `obo 0.6 sym-format 2 abi 1`.

**Tests and fixtures.** Thirteen `tests/run/methods*.obx` programs (receiver
modes, fields/elements/globals/narrowed optionals, B1/B3 receiver shapes and
owned returns, closed types, moves, mut/append, iterative handle walks,
defer/try, forward headings and mutual recursion, a while-condition forward
method, the home-retry case with the call inside the hot loop, static dispatch,
names/aliases and field origins). `tests/errors/methods.obx` has 69 sections
covering every declaration and call diagnostic, the B2 loop/re-point cases
(including mut, recursive, handle and defer variants), the owned-through-handle
message, field-through-handle, chaining/temporaries and method-body names. New
module trees: `run/methods`, `methods_closed`, `methods_identity`,
`methods_transitive`, `method_main`; `errors/private_method`,
`unknown_method_foreign`, `foreign_method`, `foreign_field_call`,
`pub_method_private_type`, `method_heading`, `owned_receiver_column` and
`mut_receiver_column`; `sym/methods`, `method_main`,
`methods_transitive_check`, `private_method_check` and `method_bad_receiver`.
`errors/arg_order.obx` gains a mixed by-value/reference signature pinning the
plain function alias numbering, and `sym_test.py` gained method coverage in the
rich round trip, `hash_methods`, `malformed_methods` and
`methods_sourceless_check`, plus a hand-written method line with a receiver name
that is not `p0` and summary items in reverse order. the golden tree gained
`worth`/`bump`/`taken` methods and was regenerated explicitly with
`sym_test.py --update`. All 79 pre-existing committed `.sym` files were migrated
in the same change (76 `modules/sym` fixtures and 3 goldens): format 2 / compiler
`0.6` / ABI 1, with `format` at `obosym 3`, `compiler` at `"9.9"` and `version` at
ABI 9. Every `modules/sym` case now has an `expect` file, and `port_compare`
asserts REF's own outcome against it before comparing PORT, so a blanket reader
regression cannot pass as "both compilers reject it alike"; `REEMIT_CASES` are
asserted exit 0, canonical and idempotent. `sym_fuzz.py` mutates the format to the next
format read from the seed (so a future bump cannot make it a no-op) and its seed
declares a method. `port_compare.py` skips the method programs and
`errors/methods.obx` only while the reference seed is below `(0, 6)`.

**Mutants.** 38 method mutants were added, including parser/declaration,
method-table lookup, receiver finalization and read marks, owned-handle, label
and display, writer group/private/summary/mode, reader receiver/main/order and
the mode-0 comparisons. Four are recorded equivalent with reasons: the
`closureSeen` truncation (only an erroring private type or a skipped foreign type
can be in the set, so sharing it cannot hide an error), and the three mode-0
method order/missing/signature checks (a full build reads the `.sym` it just
wrote; only `--check` can see a forged file, and by design it trusts it). The
retry-cleanup mutant was made observable by putting the forward-headed method
call inside the hot loop of `run/methods_homes.obx` (the stale rel32 corrupts
executed code; without the scan the program segfaults). Existing mutant patterns
moved by the `callFn`/designator refactor were retargeted (15 changed patterns:
11 in the fork dictionary and 4 port-only), not deleted. Three pre-existing port
mutants that had been counted caught only because the mutated compiler no longer
built (`break suspicion not recorded`, `loop-carried suspicion from continue
dropped`, `deferred self-call suspicion kept at the defer`, now `deferred self-call
suspicion not replayed`) were rewritten to valid source mutations in the live code, with their dead shadowed duplicates
removed; each is now caught behaviourally by `handle_deep.obx`. The call refactor
left `saveRegs()` dead, so it was removed.

**Gates (this branch, then the promoted tree at `67799b1`).** `make` with the
pre-promotion 0.5 seed built `obo` 0.6, 454,657
bytes, sha256 `90fcad7e…`; the promoted 0.6 seed rebuilds the same image, so
`seed/obo` == `obo` == stage 2 == stage 3 (`cmp`) and `--version` is
`obo 0.6 sym-format 2 abi 1`. `make tools` builds cat
40,961, wc 45,057 and sort 69,633 bytes; `make eza` builds 212,993 bytes.
`run_fork.py` 1,264 passed; `module_test.py` 91/91; `port_test.py` 537/537 with 9 encoder groups;
`port_compare.py` 1,283/1,283 stage B and 1,143/1,143 against the 0.6 seed;
`sym_test.py` 30/30; `sym_fuzz.py` 200/200; `doc_check.py` 53 examples;
`homes_check.py` and `homes_edge.py` green; `imports.py` 26/26; `stdlib.py`
16/16; `io_test.py`, `meta_test.py` 34 checks, `elf_test.py` green;
`tools.py --require-reference` 280/280 and `--memory` 2/2; `eza.py
--require-reference` 546/546 (pinned eza 0.23.3, sha256 `10457dde…`) and
`--memory` 5/5 with the unchanged 2,752,512-byte cap. `mutate_fork.py` 561/587
caught with 26 equivalents, 0 missed/stale/broken; `mutate_io.py` 27/27;
`mutate_meta.py` runtime 50/52 (2 equivalent) and compiler 21/30 (9 equivalent),
0 missed; `mutate_port.py` full cold sweep on the revised harness and final
tree caught 529/529, with 0 missed, 0 stale, 0 broken and no build-failure
catches (the three repaired mutants are caught behaviourally by
`handle_deep.obx`; the four method equivalents are excluded as in the fork
harness). Self-compile: live peak 1,728,768 bytes and
38,752,320 allocated on the main tree, within the unchanged 2,000,000-byte
budget (271,232 spare); the figure depends on the source path (a longer
worktree path reports 1,728,928), and the compiler source is 11,027 lines /
407,615 bytes. `bench/bench.py` completed
all four workloads and their outputs agree.

**Deviations and limits.** The incomplete-type message reuses the existing
`<T> is declared but not yet defined` text rather than the design note's
`type '<T>' ...` spelling. Receiver type, mode and ownership diagnostics point
at the receiver's start (threaded from the designator); a method's own
type/name/visibility errors point at the method name. The two column-pinned module cases
(`owned_receiver_column`, `mut_receiver_column`) hold this.
The design's literal `b.store(clear(b))` is not an error when the outer receiver
is a whole `&Bag` and `clear` only frees below it (the bag itself stays valid);
the tests use an element receiver and a callee that frees below the receiver,
which is the soundness rule, with the measured message. Optionals of `int` or
`string` do not exist, so the nil-receiver row is tested with `?T`/`?Bag`; a
method-valued expression and chaining errors use the measured parser texts.
Interfaces, dynamic dispatch, method values, chaining, static/UFCS calls, methods
on built-ins/derived/foreign types and an `eza` refactor remain out of scope; the
compiler's own source stays method-free, so the pre-promotion 0.5 seed built it,
and the promoted 0.6 seed rebuilds it to the same 454,657-byte image. The frozen design note's remaining
wording nits (the `closureSeen` fixture wording, receiver parens on one line, and
the literal `b.store(clear(b))` example) are recorded for a later documentation
pass; the design file itself is untouched.

# Interfaces: owning values, borrowed pairs and dispatch (v3/0.8, implemented and promoted)

Status: steps 1 to 6 are implemented and independently reviewed (the final step-6 source review approved
`8b8ca74`). Step 7 adds this record, the LANGUAGE.md section, permanent tests and the complete mutant/gate run.
The design branch was merged into main at `61dd4ec`, the implementation branch at `302b5d9`, and the seed was
promoted once to 0.8 (`seed/obo` is 507,905 bytes, sha256 `673ae7ba…`, `obo 0.8 sym-format 4 abi 1`). The
compiler source remains free of interface syntax, so the pre-promotion 0.6 seed built the 0.8 compiler. `^I` is
phase 2a, after lambdas Stage A (0.9; Stage B was originally numbered 0.10 with symbol format 5,
and is now planned as 0.11 after the released numeric floats). The superseded owning/v2 notes
and `interfaces-design.md` remain historical; `interfaces-v3-design.md` is the authority.

## What is in 0.8

- **The three value tiers, interface spelling.** `I` / `?I` are owned boxes; `&I` / `mut &I` / `&?I` are
  borrowed pairs; `^I` is not implemented yet. An `I` box is a pointer to `{obj, vtable}` (32 bytes with the
  header); the object is owned and `rt_free` frees it with the box. `isOwned(I)` is true, so moves, fields,
  arrays, `Result`, globals, `take`, `replace`, `drop` and teardown use the existing owned rules.
- **Declarations and implementations.** `type I = interface { ... }` with `[pure] [mut] fn` members;
  `impl I for T` for a record or closed type of the defining module, after the methods. A member's receiver is
  parameter 0 (borrowed or mutable). Explicit parameters are owned `p: T`, borrowed `p: &T` or mutable
  `mut p: &T`; `mut p: T` is refused. A type may implement several interfaces; a variant uses its closed
  parent's implementation and vtable. A member cannot take or return a function value.
- **Conversions.** A concrete record or closed value (including a variant) moves into a new box at a typed
  destination; `T -> ?I` boxes then widens, and `nil -> ?I` is 0. `I -> I` moves the box, `I -> ?I` widens.
  A borrowed source is refused with the move-in/`&I` hint; `?T -> I`/`?I` is refused with the narrow-local
  idiom; another interface, arrays, scalars, strings, `Result[T]` and `^T` have their own existing refusals.
  `ok(new T)` where `Result[I]` is wanted keeps the store-first hint. Inference never produces an interface.
- **Borrowed pairs.** A `&I` is a pointer to a copied pair. Parameters, locals and `for` walk variables;
  never a field, element, global or return. Every `&I` local owns its pair slot and `let`/`var`/re-point copy
  both words (B1), so a value saved from an earlier loop pass does not follow a later conversion. A concrete
  argument uses a per-site frame cell; borrowing an owned `I` place points at its box without a cell. `&?I` is a
  parameter or walk variable only, and narrows to `&I`; explicit/inferred `&?I` locals are refused.
- **Calls and effects.** An `I`, `&I` or narrowed `&?I` designator is a receiver (local, parameter, field,
  element, global, `Result` value, walk variable). Dispatch is a direct call on `obj` with the vtable slot; both
  words are loaded before the explicit arguments (S4). The caller uses the member's synthetic summary: a
  `pure` member frees nothing and writes no owned value through a handle (it may still write scalars, globals
  and its receiver); a plain/`mut` member may free only below its own `mut` parameters. A member never frees a
  global or writes through a handle. Handle-borrowed receivers, global receivers, later freeing arguments and
  mut aliases keep their existing checks under the interface display.
- **Globals and guards.** Owned conversions, borrowing an owned `I` global and moving an owned `I` into a
  global are allowed in a global initializer; a conversion that needs a cell and any interface method call are
  refused with the v3 texts (E10). Interface values cannot be compared; only `== nil` / `!= nil` on `?I` are
  allowed. `handle` still names records and closed types only in 0.8.
- **Versions and symbols.** `OBO_VERSION "0.8"`, `SYM_FORMAT 4`, ABI 1. Interfaces have a definition line, no
  shell; implementations follow methods; member markers and modes are encoded and the reader rebuilds each
  member's summary with the same helper as the source path.

The compiler's own source is still interface-free, so this round's bootstrap does not use the feature.

## Permanent tests

The interface run files are `ifaces_decl`, `ifaces_owned`, `ifaces_calls`, `ifaces_effects`, `ifaces_borrowed`,
`ifaces_b1_loop`, `ifaces_refs`, `ifaces_global_init` and the new `ifaces_homes`. Step 7 closes the R4/R5 gaps
without a new giant file:

- R4: `run/ifaces_owned` adds the third `?T -> I` idiom shape (`else { drop(t) }`), the early-return and
  take-into-local shapes, and a variant borrowed as `&I` through the parent's vtable.
- R5: `run/ifaces_calls` adds a member that takes owned `Chain` arguments (including `?Chain` and `[]Chain`) and
  returns an owned `Chain`.
- R9: `run/ifaces_homes` is an interface-heavy program (owned/borrowed/optional/nested/many-argument calls,
  a saved `&I` pair, a group, a walk). `homes_check.py` compiles and runs it with the register-home compiler and
  with a compiler rebuilt from the same source with `REG_HOMES = 0`, requiring identical output, and also checks
  its stack-slot count and loop alignment. `port_compare.py` includes the same file as a normal run case.

The error files are `ifaces_decl`, `ifaces_conv` (in `ifaces_owned`), `ifaces_eq`, `ifaces_pos`,
`ifaces_tiers`, `ifaces_impl_mismatch`, `ifaces_calls`, `ifaces_refs`, `ifaces_handle` (0.8 E8a) and
`ifaces_global_init` (E10); the module and `.sym` cases are `modules/{run,errors,sym}` and the `sym_test.py`
S-1 to S-6 cases. E8 (phase 2a), E9 and E9a (lambda phase) are not present because their features are not
implemented in 0.8; the design IDs are not invented for this phase.

## Mutant coverage (design section 12.4, X1-X38)

`mutate_fork.py` now runs the interface suite with each mutant. A named variant such as X36a/X36b or X38a-X38e
is a subdivision of the design's row, not a new design ID. X39/X40 were never design IDs; the two extra
dispatch mutations are now description-named extras.

| id | verdict | catcher or exact reason |
|---|---|---|
| X1 | caught | `run/ifaces_owned` under OBO_CHECK=1 (leaked boxes/objects) |
| X2 | caught | `run/ifaces_owned` (double free) |
| X3 | caught | `run/ifaces_owned` (a variant boxed with the wrong vtable) |
| X4 | caught | `run/ifaces_owned` (nil optional allocates) |
| X5 | caught | `run/ifaces_b1_loop` (saved pair follows the site) |
| X6 | caught | `run/ifaces_b1_loop` (re-point stores only a pointer) |
| X7 | caught | `run/ifaces_b1_loop` (vtable word not copied) |
| X8 | equivalent | the faithful S4 mutation reads the receiver's `obj` and `vtable` after every explicit argument. `argOrderCheck` holds the receiver as argument 0 and refuses a later argument that moves or frees its box (the mut-alias check also refuses a mut argument that points inside it); the box pointer and the two dispatch slots keep the same layout, so legal source observes the same box and table. E6's `h.d.grow(swap(h))` is refused identically; see the precise reason in `mutate_fork.py`. |
| X9 | caught | E2 store-first: `errors/ifaces_owned` and `errors/ifaces_refs` |
| X10 | deferred, phase 2a | there is no `^I`; the 0.8 handle refusal (E8a) is a separate extra mutation |
| X11 | caught | `run/ifaces_refs` / B1 loop and defer rules |
| X12 | caught | `errors/ifaces_tiers` and `modules/errors/iface_tier_handle` |
| X13 | caught | `sym/iface_forged_freesall` (source-less `--check` of the defining module) |
| X14 | caught | `errors/ifaces_tiers` and `modules/errors/iface_tier_global` |
| X15 | equivalent | signature mode equality (X38d) plus S(m) from the same modes makes a source method's pfree mask a subset; `callFn` records pfree only on mode-2 arguments and a mode-1 parameter cannot be changed. The branch only guards a forged *method* summary, which `implDecl` never sees (the reader trusts imported impls, and a root cannot implement for an imported type). The full suite passes without it; no honest input can distinguish it. |
| X16 | caught | `errors/ifaces_tiers` and `errors/ifaces_calls` pure cases |
| X17 | caught | valid local-tier implementations (`run/ifaces_decl`, `ifaces_calls`, `ifaces_refs`) are rejected |
| X18 | caught | `modules/run/iface_tier_bits` and the source-less tier check |
| X19 | caught | `modules/run/iface_tier_bits` |
| X20 | caught | `modules/run/iface_tier_bits` (second mut parameter) |
| X21 | caught | `run/ifaces_effects` (a plain member through a handle) |
| X22 | caught | `run/ifaces_effects` (a pure `mut` member through a handle) |
| X23 | caught | `modules/run/iface_call_tiers` source-less check |
| X24 | caught | `sym/iface_summary_matrix` (reader mode at index >= 2 differs on re-emission) |
| X25 | caught | golden/round-trip symbol tests |
| X26 | caught | golden/round-trip member mode |
| X27 | caught | golden/round-trip return type and member order |
| X28 | caught | `iface_sig` full build / re-emission |
| X29 | caught | `sym/iface_reader_refusals` |
| X30 | equivalent | a full build compares a `.sym` its own writer just wrote from the same live interface; `--check` (mode 1) does not run the mode-0 comparison, and a forged file is trusted by design. Writer modes are pinned by the golden and round trip (X26). |
| X31 | caught | `errors/ifaces_decl` (source `mut p: I`) |
| X32 | caught | `errors/ifaces_eq` |
| X33 | caught | `errors/ifaces_pos` / `errors/ifaces_owned` (`&?I` local) |
| X34 | caught | `errors/ifaces_owned` / `errors/ifaces_global_init` (cell in a global) |
| X35 | caught | `run/ifaces_owned` (`replace` returns the old interface) |
| X36a | caught | symbol version/stale tests and the golden header |
| X36b | caught | symbol version/stale tests and the golden header |
| X37 | deferred, lambda phase | `containsIface` and function values do not exist in 0.8; inventing an equivalent would test code that is not there |
| X38a | caught | `errors/ifaces_impl_mismatch` receiver mode |
| X38b | caught | `errors/ifaces_impl_mismatch` argument count |
| X38c | caught | `errors/ifaces_impl_mismatch` argument type |
| X38d | caught | `errors/ifaces_impl_mismatch` argument mode |
| X38e | caught | `errors/ifaces_impl_mismatch` return type |
| extra: 0.8 handle refusal | caught | `errors/ifaces_handle` |
| extra: global-init call guard | caught | `errors/ifaces_global_init` |
| extra: wrong vtable slot | caught | `run/ifaces_calls` |
| extra: member summary not applied | caught | `errors/ifaces_calls` and `run/ifaces_effects` |
| extra: wrong vtable stack slot | caught | crashes the first dispatch (`run/ifaces_calls`); a corruption fault, not an S4 load-order fault |
| extra: obj/vtable words swapped | caught | two-site role swap; crashes the first dispatch (`run/ifaces_calls`, `run/ifaces_b1_loop`); a corruption fault, not an S4 load-order fault |

Counts: 49 table rows: 47 active interface entries (44 caught, 3 equivalent X8/X15/X30) plus the two
deferred rows X10 and X37. All 47 active entries were run; 0 missed/stale/broken. The 36 design rows minus
X10 and X37 are all represented; the remaining active entries are the two renamed corrupt-dispatch extras, the
X36 split, the X38 subdivisions and the four other extras. No test assertion was deleted or relaxed.

ID check against the design: E8a, E10 and E-impl-mismatch are current tests; E8 (phase 2a) and E9/E9a (lambda
phase) are not implemented and are not claimed. X38 is present as five comparison variants. X39 and X40 are not
design IDs and no longer appear. The two old S4 corruption mutations (a wrong stack slot and a two-site role
swap) are descriptive extras, not X8 or X8b: they crash the first dispatch and do not exercise load order.

## Gates (this branch, then the promoted main tree)

These were run from `obo/` at the review-fix tree (a8bea53 plus this scoped test/doc change) with the 0.6 seed
building the 0.8 compiler, and repeated after promotion on main. The source is interface-free, so the bootstrap
is the same fact the implementation steps used.

- `run_fork.py`: 1,408 passed, 0 failed.
- `module_test.py`: 103 passed, 0 failed.
- `sym_test.py`: 38 passed, 0 failed, golden compare with no `--update`.
- `sym_fuzz.py`: 200/200 clean.
- `doc_check.py`: 56 examples, 0 problems.
- `homes_check.py`: all programs green, including the interface-heavy program in both register modes and the
  32 padding lengths.
- `homes_edge.py`: 1,028 statements, 119 refused by both, 0 differ.
- `elf_test.py`, `io_test.py` and `meta_test.py`: green (34 metadata checks, 0 failures).
- `port_test.py`: 556 of 556 identical, no leaks, 9 encoder groups.
- `port_compare.py`: 1,420 of 1,420 behave the same; the port peaks at **1,945,760 bytes live** (budget
  `2,000,000`, 54,240 spare) and allocates 44,588,160 bytes in total. A direct `OBO_STATS=1 ./seed/obo
  src/obo.obo` run measures 1,945,680 live / 44,586,880 allocated, so the margin is about 54 KB, not the 92 KB
  of the paused 0.7 branch. The source growth crossed a 16 KiB image chunk as recorded for step 6; the step-7
  docs and tests add no compiler source and do not move the peak.
- `imports.py`: 26/26; `stdlib.py`: 16/16.
- `tools.py --require-reference`: 280/280.
- `eza.py --require-reference`: **546 passed, 0 failed, 0 skipped**, run as
  `PATH=/tmp/eza-install-sim-F2odpb:$PATH make eza-check` (command-scoped PATH only). The reference is the
  pinned eza v0.23.3 binary with sha256 `10457dde...a8f7cd8`, exactly `tests/eza/VERSION`; `REFERENCE.md`
  records it as the P0 probe artifact at that path, and its `--version` output matches the pin. `make
  eza-memory`: 5/5, worst peak 2,190,160 bytes under `EZA_PEAK_MAX` 2,752,512. No golden was changed. The
  earlier 474/0/72-skipped fallback is superseded by this strict run.
- Self-hosting: stage2 == stage3 (`cmp`, 507,905 bytes), seed-built `obo` == stage2, and `--version` is
  `obo 0.8 sym-format 4 abi 1`. The promoted `seed/obo` is the same image (sha256 `673ae7ba…`); the 0.6 seed
  (454,657 bytes, sha256 `90fcad7e…`) is historical.
- `mutate_fork.py` full: 605 of 634 mutants caught, 29 equivalent, 0 missed/stale/broken. The interface
  subset is 47 active entries (X10 and X37 are deferred rows, not run): 44 caught and 3 equivalent (X8, X15,
  X30), matching the table above. The new X8 is the faithful delayed-read mutation; the two renamed corruption
  extras are still caught.
- `mutate_port.py` full: 529 of 529 mutants caught, 0 missed/stale/broken.
- `mutate_io.py` full: 27 of 27 caught, 0 missed/stale/broken.

Eight pre-existing patterns in `mutate_fork.py` and four in `mutate_port.py` had been left stale by earlier
refactors, including the interface-source work (moved argument lines, the reader's two-pattern names, the shell
predicate, and the alias/method lookup and receiver-consume lines). They were retargeted to the current source
and still express the same mutation; none was deleted or relabelled. The full runs above are the evidence that
every pattern matches once and every mutant is judged.

## Limits and deferred phases

- `^I`, the S1 handle rule, E8 and X10 are phase 2a, after lambdas Stage B. `handle` keeps the 0.8 refusal and
  E8a covers it. `containsIface`, function values, E9, E9a and X37 belong to the lambda stage; they are not
  claimed as current tests.
- The local tier is conservative: a method that writes through a handle or frees a global cannot implement a
  plain interface member. `pure` does not forbid scalar, global or receiver writes; it guarantees no frees and
  no owned writes through a handle.
- S4 is pinned by a faithful mutation (X8) that moves the actual `obj`/`vtable` reads after the explicit
  arguments while keeping argument 0 and the vtable slot in place. It is equivalent on legal source: E6 refuses
  a later argument that may free or move the receiver's box, and the mut-alias check refuses one that points
  inside the receiver, so no legal argument can change the place, box or table between the reads and the
  indirect call. The dispatch run tests pin the valid words and slot. The two
  old X8/X8b mutations were corruption faults (a wrong stack slot and swapped roles), not load-order tests;
  they are now named as descriptive extras.
- `?T -> ?I` is not a conversion; the documented idiom is a narrowed local. `&?I` is parameter/walk-variable
  only. Interfaces cannot be compared or handled in 0.8.
- The compiler source, runtime, seed and `PEAK_BUDGET` are unchanged. The peak remains under 2,000,000; if a
  future change exceeded it, the rule is to stop and report rather than raise the budget.

## Mutation harness: catcher-first port and timings (2026-10-30)

The mutation gates keep every check and every verdict; this change only shortens the work done before a
mutant is caught, and records where the time went.

**Usage.** The normal commands are unchanged (`python3 tests/mutate_fork.py`, `python3
tests/mutate_port.py`). `MUTATE_CATCHERS=0` disables the catcher order and runs every comparison in full.
`MUTATE_TIMINGS=1` writes JSON lines to a new file under the system temporary directory; set it to a path
to choose the file. Timings never appear on stdout and never change a verdict. `python3
tests/mutate_harness_test.py` runs the harness's own regression tests with fake builders, in a second or so.
The timing header records the source, seed, runtime, test-tree and harness hashes, the git head, the core
count and the load average; an `end` record records the final wall time and the harness hashes again.
Per-mutant records add wall seconds, stage seconds, subprocess/compile/run counts, case counts, first
failure, serial retries and the actual outer/inner job counts. Wall seconds are per stage; subprocess counts
are summed over concurrent work, so they are not wall time.

The counters are DIRECT `subprocess.run` launches made by the harness, not descendants. The two gates count
different things: `mutate_fork` counts the seed build and the `run_fork` suite, while `mutate_port` counts
each case compile/run inside its comparison. They are not comparable with each other, and a case count such
as run_fork's 1408 is not a launch count. The identity in the header is a record, not a lock or an
immutability guarantee; nothing is reused across runs. The full-run logs from this branch predate the final
review nits (probe-all, actual summary, special-helper errors), so they are **pre-final provenance only** for
counts and per-mutant walls, not the final integrated gate evidence. The final integrated run on 0.8.1 is
recorded in the next section.

**Catcher-first port mutants.** `tests/mutant_catchers.tsv` maps each port mutant to the check that first
failed in the authoritative post-promotion `mutate_port` log at `27f09de` (529/529 caught). The table is a
check order, not a verdict cache and not a corpus of expected results:

- A catcher that differs from REF catches the mutant (a real outcome difference; the memory peak check is
  unchanged and still runs first for every mutant).
- A catcher that passes, an unknown/stale check name, or a pass after a timeout falls through to the full
  comparison, which still runs every case.
- An out-of-memory catcher difference is returned as caught but is marked for the existing serial re-run
  (the same confirmation `mutate_fork` uses); a load-induced OOM therefore still gets a full re-judgement.
- The unmutated compiler runs **every distinct check selected by the chosen mutants** through the exact
  catcher-only path before any mutant is judged, not one example per kind. On the full selection that is 265
  distinct checks in about 4.0 s and 363 direct subprocess launches. A failure, unknown name or exception
  there aborts the gate, so a broken non-representative case cannot make every mutant look caught.
- A special helper raises `HarnessError` (or lets `TimeoutExpired` become `('timeout',)` data) instead of
  being appended as a caught mutant. A helper bug therefore aborts the gate; a real difference in the
  helper's returned outcome is still an ordinary mutant catch, and a timeout is left to the full/confirmation
  path rather than being fatal.
- Equivalent mutants still run the full suite; no verdict is recorded from a catcher.
- The absolute live-peak budget (`PEAK_BUDGET`) is checked first and always. The relative 3% margin over the
  unmutated peak is evaluated only for a mutant that survived every check and returned a peak, so it is
  redundant with the absolute budget today; it is not a weaker first check and is not removed.
- The map covers 479 mutants; 24 are caught by the mandatory self-compile/memory check before the catchers,
  and the 26 encoder/image/shape/edge mutants keep their dedicated scripts. If the catalogue changes, the
  stale name is reported and the full comparison decides. Nothing is reused across runs, so the unsafe
  startup/end-hash verdict cache removed earlier is not reintroduced; the timing header states the inputs
  and the run assumes they do not change while it is in progress.

The quick path also orders the full fallback cheap-first (error cases, run programs, examples, the compiler
itself, benchmarks last) and skips the module trees once a flat case has definitively failed. The
non-mutant `port_compare.py` keeps its original catalogue order and still runs everything.

**Measured evidence.** Same host, 16 cores, `MUTATE_JOBS=1`, `MUTATE_INNER_JOBS=4`, warm reference cache,
one port comparison at a time; before from the earlier targeted profile, after from the same seven mutants
with catchers on. These are wall seconds, not a controlled benchmark.

| Port mutant | before | after | after path |
|---|---:|---:|---|
| `error message changed` | 1.948 | 0.328 | flat catcher |
| `condition test compares with 1` | 1.688 | 0.325 | flat catcher |
| `callee frees not recorded` | 4.640 | 0.327 | flat catcher |
| `parameters kept` | 0.309 | 0.303 | memory (unchanged) |
| `sym: imported pf rows dropped` | 5.706 | 0.354 | reemit catcher |
| `sym: --version reports the wrong format` | 5.572 | 0.320 | CLI catcher |
| `align: loops not padded` | 0.749 | 0.761 | `homes_check` (unchanged) |

The full port run after the change caught **529/529**, with 479 caught by a catcher, 24 by the memory
check and 26 by their dedicated script; one OOM catcher was serial-confirmed. Per-mutant walls summed to
469.7 s (mean 0.888 s, median 0.394 s) with four outer mutants at a time; the full baseline comparison took
6.696 s. Before the change the same gate had no per-stage timings, so the matched sample above is the
like-for-like before evidence, not the old log's elapsed-time bracket.

**Fork changes.** `run_fork.py` already includes `module_test.tests()` and `sym_test.tests()`, so
`mutate_fork.build_and_run` no longer runs those two suites again after it. The baseline and judge paths are
still both run and still validated separately (full, then fail-fast with the smoke subset and the inner
count). On this host the fork baseline fell from 3.211 s in the earlier sample to 2.663 s, and the full-run
X8 equivalent from 3.193 s to 2.594 s; the 0.55 s duplicate module/.sym work is gone. The full fork run's
per-mutant statuses, which the final summary now counts directly after serial re-runs, are **607/634 caught,
27 equivalent**; mean per-mutant wall 1.864 s, median 0.751 s, seven serial re-runs. They match the
authoritative log line for line. Two names in the `EQUIVALENT` catalogue are caught by their suite
(`place origin: a callee without rows frees nothing` and `place origin: a callee records every change as not
strict`); they are not reclassified, and the gate prints a separate note naming them. The old summary line
counted `EQUIVALENT` membership instead of final status, which is why historical logs say 605 caught / 29
equivalent; that was a reporting bug, and the historical release evidence is left as it was rather than
rewritten. `mutate_io.py` remained 27/27.

**Limits.** The catcher map is only as good as the catalog it was learned from; a changed first failure
costs a full fallback, not a wrong verdict. The current outliers are real work under the mutant (compiling a
large benchmark through a slower mutant, or a mutated program that runs long), not harness overhead. The
timings count a stage's wall once and its subprocesses as a sum of direct launches, so they should be
compared stage by stage, not added into a single wall figure or compared across the two gates.

## Mutation harness: final integrated 0.8.1 validation (2026-10-30)

One frozen-tree run of all four mutation gates at `29492ba` (the reviewed merge `9fc2dfd` plus the
metadata-anchor repair `29492ba`), with the default jobs and no source or test edits while the matrix ran.
Provenance: source sha256 `8f9f9a1d4b962fff670f147c2d935f498b5cbae84338c47251c62b44b04b7f7b` (12,071 lines,
455,137 bytes), unchanged seed sha256 `673ae7ba13cec45be14eeb99d8f638e8418ed2ac0cd91787aa4a18c54a407374`,
compiler 512,001 bytes sha256 `8ac35d769e86e7dd4c3ce980d1e4a65eee51957e1ad2c008e57389ebcef9f590`
(`obo 0.8.1`), runtime `a12f3edf…`, entry `7b9bf0ea…`, `rt.ld` `ca3d7a64…`, and tests tree
`09262ed86f35aef4fcc9b6982135bab6758f10d46d05549e42fe263cd872237d`. The timing headers and end records
repeat these hashes. Logs are in `/tmp/opencode/fast-gate-integration-20261030-1828` (temporary).

| Gate | Result | Gate wall | CPU (user+sys) | Per-mutant wall sum; median / p95 | Paths / retries |
|---|---:|---:|---:|---:|---|
| `mutate_fork` | 607/634 caught, 27 equivalent | 183.259 s | 575.12 + 283.44 s | 1,201.162 s; 0.759 / 4.594 s | full suite; 7 serial confirmations |
| `mutate_port` | 529/529 caught | 118.532 s | 206.53 + 44.61 s | 427.051 s; 0.347 / 0.714 s | 479 catcher, 24 memory, 26 dedicated, 0 full fallback; 1 serial confirmation |
| `mutate_io` | 27/27 caught | 6.85 s | 6.04 + 1.94 s | not instrumented | sequential; 0 missed/stale/broken |
| `mutate_meta` | 71/82 caught, 11 equivalent (50/52 runtime + 21/30 compiler) | 128.60 s | 46.58 + 20.08 s | not instrumented | 0 missed/stale/broken |

The port gate's fresh baseline full comparison took 5.842 s and peaked at 1,947,712 live bytes (budget
2,000,000); the pre-mutant catcher probe ran all 265 distinct checks in 5.083 s with 363 direct launches and
passed. The fork baseline and judge each ran 1,416 cases. Direct-launch counts are the harness's own
`subprocess.run` calls, not descendants: 1,268 mutant-stage launches in `mutate_fork` (1,272 with
baseline/judge), and 1,840 in `mutate_port`'s mutant stages (3,299 in the full baseline and 363 in the
catcher probe); do not compare them across gates.

The fork's final per-name statuses match the old post-promotion log exactly after serial re-runs: 607
caught and 27 equivalent, with 0 status changes (the old summary's 605/29 was the membership bug). The two
catalog-equivalent entries `place origin: a callee without rows frees nothing` and `place origin: a callee
records every change as not strict` are caught by the suite in both runs and are still listed as
equivalents, with the separate notice. `mutate_port` and `mutate_io` likewise have 0 per-name changes. The
seven repaired metadata anchors are now caught or equivalent as in the older 0.8 run (three `callExempt`
entries remain equivalent); no new transition was hidden. The 0.8.1 sleep tests added no new fork mutant
status changes.

Host: 16 cores, AMD Ryzen 7 8745HS, load averages 0.14 to 3.13 during the matrix; fork ran 16 outer x 1
inner, port 4 x 4, meta 4, io sequential. These are gate walls from the harness end records (GNU time for
io/meta), not a controlled whole-gate benchmark. The per-mutant walls sum concurrent work, so they exceed
the gate wall; they are not CPU time. Outliers were real mutant work, not harness overhead: fork
`interfaces v3 X7` 55.02 s, `X5` 45.05 s, `X4` 36.36 s, `image: the epilogue returns with int3` 32.56 s
and `io: closeFile swaps the descriptors` 22.96 s; port `inverted for loop: test jumps back to its own top`
35.34 s, `retry: waiting calls of the first pass kept` 27.14 s and `image: the epilogue returns with int3`
26.78 s. The older 0.8 before/after sample remains supporting evidence only, as labelled above.

**Limits.** The timing headers identify the inputs but are not a lock; no verdict is reused. The io and meta
runners do not emit per-mutant timings, so only their gate walls and CPU are reported. The metadata anchor
repair changed the tests-tree digest and is reflected above; the compiler, runtime and seed did not change.
## Stage A lambdas: implementation, memory recovery and final gates

Stage A was implemented on the isolated lambdas branch and is now merged and
promoted on `main` as `obo 0.9`, symbol format 5, ABI 1. The reviewed feature
commit is `7bd905e9426433da14ab9c094d9eb55288c9a302`; the merge commit is
`5b1a04b3d613e7586cdca4d1e3efe1c82f93d6cd`. The historical promoted `0.8` seed
(`507,905` bytes, sha256 `673ae7ba…`) built the merged compiler once for the
promotion; `seed/obo` is now the same `540,673`-byte, sha256 `3d369273…`
compiler. Stage A adds structural function types, non-capturing
`fn (...) { ... }` literals, the one-word cell ABI with a hidden environment
argument and indirect `call qword ptr [cell]` dispatch, the second-class
position firewall, the pure-callback rules and 0.9/format-5 symbol support.
Capture lists, owned/read-only captures, `^I` and Stage B remain deferred. The
feature evidence below is retained; the release record and post-promotion gates
are in "Stage A release: merge, one promotion and post-promotion gates".

The first implementation crossed the 2,000,000-byte compiler self-compile budget
at 2,060,608 live bytes. The recovery kept the budget and checks unchanged and
added two reviewed pieces:

- zero-capacity arrays share four static read-only data blocks (kinds 1/2/4/6),
  never written or freed, with the array header ABI unchanged; appends still
  allocate a fresh heap block and preserve doubling growth. The runtime text is
  17,168 bytes and data 67,360 bytes.
- the per-`FreeRec` `nonFresh` slice became two integer bounds into one global
  `nonFreshLog`; the flat log is saved/restored with `freeLog`/`freeArena`, and
  `loopLogEnd` trims only the records it removes. The checked NF_CHECK corpus
  covers flat ranges, pre-loop defer slices, loop truncation, nested islands,
  break/continue/try/defer/homes and many literal bodies.

On the pre-merge feature tree, after codegen, the compiler self-compile peaks are
three identical direct runs at `1,963,248` bytes and a stage-3 peak of `1,963,296`;
the port comparison peaks at
`1,963,616`. The final source is `488,954` bytes, the binary `540,673` bytes, and
the emitted code is `478,526` bytes in 29 full chunks plus 3,390 bytes in the
current chunk. Direct headroom is 36,752 bytes. Stage 2 equals stage 3 byte for
byte. These are compiler-self measurements; the 120/240/480-literal private
workloads are large-input compile tests, not the compiler budget.

Permanent regression catchers include the outer-name lifetime test, the
non-env implicit-capture cases, the bounded 100/200/400 call-site arena test,
the checked NF_CHECK harness and the direct empty-array runtime cases. The
runtime empty-block guards, static-free and unknown-kind contracts are also
exercised privately; permanent mutation catalog routing is recorded in the
Step 4 mutant matrix below.


### Stage A final gate evidence (historical feature branch `2da9ed1` + Step 4, pre-merge 0.8 seed)

The final Step 4 source is `488,954` bytes (12,953 lines, sha256 `5ba392c4…`), the
compiler binary `540,673` bytes (`3d369273…`), and emitted code `478,526` bytes:
29 full chunks plus 3,390 bytes in the current chunk (12,994 spare). The unchanged
0.8 seed is `673ae7ba…`; runtime text/data are 17,168/67,360 bytes. Three
self-compile runs peak at 1,963,248 live bytes, stage 3 at 1,963,296, and the port
comparison at 1,963,616 (hard budget 2,000,000); stage 2 == stage 3 byte for byte.
Maximum RSS is 3,640 KiB. The early implementation crossed the budget at
2,060,608; the approved empty-array and flat-`nonFreshLog` recovery removed that
growth without changing the budget or checks.

Functional gates on the frozen source/tests: `run_fork` **1544 passed**, including
the checked NF_CHECK log gate and the 100/200/400 call-site arena bound; `module_test`
**106**; `sym_test` **42** with golden comparison and no `--update`; `sym_fuzz`
200/200; `doc_check` **62 examples, 0 problems**; `homes_check` **13** with lambda and
interface programs in both register modes plus loop-head padding; `homes_edge` 1028
statements, 119 refused, 0 differ; `io_test` 0 failures; `meta_test` 34; `elf_test`
ok; `port_test` **573/573** with 10 encoder groups; `port_compare` **1551/1551**
behave the same; `tools.py` **509**; strict pinned `eza.py` **554** with reference
sha256 `10457dde…`. `memory_log_check` passed four checked sources.

Mutation gates:

| Gate | Result |
|---|---|
| `mutate_fork` | **651/679 caught, 28 equivalent** (679 names / 681 anchors) |
| `mutate_port` | **529/529 caught** (529 names / 534 unique replacement tuples) |
| `mutate_io` | **30/30 caught** (three new runtime empty-array guards; the zero-array heap-allocation mutant is caught by the specialized self-host memory oracle: baseline compiler peak 1,963,664, mutant-runtime compiler peak 2,008,336, budget 2,000,000) |
| `mutate_meta` | **71/82 caught, 11 equivalent** (50/52 runtime, 21/30 compiler) |

New Stage A mutants: 44 caught and one reasoned equivalent (`X5` constructor
recursion is defence in depth because every constructor rejects a function base
at formation). The standalone seed comparison was run from the repository root
as `OBO_REF=obo/seed/obo python3 obo/tests/port_compare.py`: it reported a
`(0, 8)` reference and **1269/1269** identical outcomes, with 14 legitimate
`(0, 9)` skips (10 lambda run files, 2 lambda error files, and the 0.8.1
`sleepMs` run/error files). Existing pre-0.9 cases were retained. The new set covers function-type identity/writer/reader, position
firewalls and aliases, interface firewall/X37 recursion and incomplete shells,
forward aliases, lambda parsing/contexts, purity/active calls, hidden env, cell
and argument ABI, zero-frame cell entry, arena-local synthetic callees, outer-name
stack restore and nonFreshLog range/trim/island restore. Stage B capture-list and
capture-binding mutants are explicitly deferred, not counted as equivalents.

### Stage A release: merge, one promotion and post-promotion gates

Status: **merged and promoted on `main` as `0.9`; superseded by the released
`0.10` numeric floats line.** The reviewed feature commit is
`7bd905e9426433da14ab9c094d9eb55288c9a302`; the merge commit is
`5b1a04b3d613e7586cdca4d1e3efe1c82f93d6cd` (parents
`75b061cc396c83e38a6053cdcd3015bd0b9cc00c` and `7bd905e…`). The reviewed source
is `488,954` bytes / `12,953` lines, sha256 `5ba392c4…`; `runtime/runtime.c` is
`194e6059…`; the compiler is `540,673` bytes, sha256 `3d369273…`, `--version`
`obo 0.9 sym-format 5 abi 1`.

Before the promotion the old seed was preserved as a private regular snapshot
at `/tmp/opencode/lambda-stage-a-postpromotion-20261031T055721Z/old-seed-obo-0.8`:
`507,905` bytes, sha256 `673ae7ba13cec45be14eeb99d8f638e8418ed2ac0cd91787aa4a18c54a407374`,
`obo 0.8 sym-format 4 abi 1` (historical). The ignored runtime bins were rebuilt
from the merged runtime first (`runtime_text.bin` `da033ab3…`, `runtime_data.bin`
`0bd93a12…`); the stale main-worktree artifacts were not reused. The merged
compiler was then built from the preserved old seed, reproduced `3d369273…`, and
made the fixed point `stage2 == stage3 == seed/obo == obo`. The old 0.8 seed's
own build of the 0.9 source peaked at `2,070,368` bytes (allocated
`47,614,080`); that bootstrap compile is the historical seed, not the promoted
compiler, and is outside the compiler self-compile budget. `make promote` was
run exactly once with the Makefile recipe unchanged; its log is
`…/promote.log`. The new seed is `540,673` bytes, sha256 `3d369273…`,
`obo 0.9 sym-format 5 abi 1`; the old seed remains the bootstrap proof, not a
tracked extra seed.

Post-promotion gates on the frozen source/tests and promoted seed (logs in
`/tmp/opencode/lambda-stage-a-postpromotion-20261031T055721Z/`): `run_fork`
**1,544 passed**; checked `NF_CHECK` **4/4**; `module_test` **106**; `sym_test`
**42** with golden comparison and no `--update`; `sym_fuzz` **200/200**;
`doc_check` **62**; `homes_check` **13**; `homes_edge` **1,028 statements, 119
refused by both, 0 differ**; `elf_test` ok; `io_test` **0 failures**;
`meta_test` **34**; `port_test` **573/573** with 10 encoder groups;
`port_compare` **1,551/1,551**; standalone seed comparison
`OBO_REF=obo/seed/obo` **1,394/1,394** with no skips; harness **25**; imports
**28/28**; stdlib **16/16**; `tools.py --require-reference` **509** and
`--memory` **4**; strict pinned `eza.py --require-reference` **554** and
`--memory` **5** (`eza` sha256 `10457dde…`).

Self-compile measurements on the promoted tree: three direct runs each peak
`1,963,264` bytes (RSS `3,640` KiB; total allocated `42,922,368`); the port
comparison peaks `1,963,344` bytes and allocates `42,923,648`; stage 2/3 peaks
`1,963,280`/`1,963,328` and `cmp` is clean. All are below the unchanged
`2,000,000` budget (36,736/36,656 spare). Tool sizes are cat 45,057, wc 49,153,
sort 73,729, head 45,057, tail 53,249 and eza 221,185 bytes.

Mutation gates (run sequentially, full fallback, no verdict cache):
`mutate_fork` **651/679 caught + 28 equivalent** in 182.989 s; `mutate_port`
**529/529** in 129.933 s; `mutate_io` **30/30**, including the zero-array memory
oracle with baseline `1,963,440` and mutant-runtime peak `2,008,112` over the
unchanged `2,000,000` cap; `mutate_meta` **71/82 caught + 11 equivalent**
(50/52 runtime, 21/30 compiler). The fork/port timing headers record git HEAD
`5b1a04b…`, source `5ba392c4…`, seed `3d369273…`, runtime `194e6059…` and the
frozen tests digest `1aa27be5…`; the end records show 0 missed/stale/broken.
The two catalog-equivalent fork mutants caught by the suite are reported, not
reclassified.

The historical feature-branch record above (0.8 seed, `2da9ed1` + Step 4)
retains its own numbers, including the pre-merge 1,269/1,269 seed comparison and
the 14 old-seed skips; those are history, not the promoted state. The strict
compiler budget remains `2,000,000` bytes; no 2.5 MB allowance is authorized or
used. If a 2.5 MB cap were ever approved, the absolute-budget zero-array memory
mutant would no longer be caught and would need a relative-budget regression
review first.
## Float F1 checkpoint (unreleased, WIP)

This isolated branch adds the first scalar binary64 slice. It is **not a
release candidate** and does not assign a numeric `OBO_VERSION`, `SYM_FORMAT`
or `SYM_ABI`. The source remains `0.9`/format 5/ABI 1 for this checkpoint; a
float `.sym` has a type writer/reader path, but float constants and a
format-version decision are unimplemented.

Implemented: `f`/`F` literals with exact compile-time decimal-to-binary64
conversion (exact base-2^16 big integer plus sticky, ties-to-even), bare fraction/exponent
rejection with an `f`-suffix diagnostic, `float` as a one-word unowned type,
`+ - * /`, unary minus, IEEE comparisons, `float(int)`, `isNaN`/`isInf`/
`signbit`, and a checkpoint-only `floatBits` bit oracle. Float values are raw
bits in GPR/stack words; freestanding C helpers 41–55 use C `double`/SSE2
internally and integer-only arguments/results. Seed 0.8 still builds the
compiler source because the compiler itself uses no float syntax.

The original F1/F2 checkpoint measured `2,028,976` peak bytes; the exact
converter checkpoint is `2,027,248` direct (`2,027,264` stage 3; port
`2,027,616`), source `13,390` lines / `503,098` bytes, binary `561,153` bytes,
runtime text/data `23,904`/`67,360`.

**Numeric-branch budget (user-approved 2026-10-31).** The numeric worktree
uses `obo/tests/peak_budget.py` with `PEAK_BUDGET = 2_500_000` decimal bytes.
This is not a change to the historical lambda release budget, which remains
`2,000,000` in its own record. The zero-array heap-allocation mutant is
additionally judged by the same-source self-compile delta: after the exact
converter the latest measured baseline is 2,030,784 and the mutant 2,076,512, a
45,728-byte regression (historical pre-float delta 44,672); the shared oracle
requires at least 30,000 bytes over baseline. This keeps the
empty-array saving caught even when both peaks are below the new absolute cap.
No check was weakened.

Tests after the converter/separator fix: `tests/float_f1.py` passes 12 groups (pinned
literal bits, 295 finite seeded random literals, exact fixed counterexamples,
long mantissas, arithmetic/NaN/±Inf/signed-zero/subnormal,
scalars/params/returns, lambda/function-type calls, containers, module
signature, int conversion, exponent bounds, error cases, GC poison);
`run_fork.py` 1544 passed; `port_test.py` 573/573; `sym_test.py` 42 passed;
`doc_check.py` 62 examples, 0 problems. The audit's independent literal corpus
(199 finite cases) and parse corpus (384 cases) both pass. Unimplemented:
shortest formatting, float constants/`.sym` release format, float-to-int, and
the full container/symbol release matrix.

## Float F2 checkpoint (unreleased, WIP)

Adds a first working text path without changing the one-word float ABI or the
compiler identity. `print`/`println` accept float, `string(float)` returns a
string, and `parseFloat(string): Result[float]` is the explicit checked
parser. There is no implicit string/numeric conversion and no float-to-int yet.

The F2 formatter was a provisional 17-significant-digit scientific notation,
not claimed shortest. **F3A below replaces it with a shortest round-trip
formatter**; special spellings remain `nan`, `inf`, `-inf` and `-0`, and NaN
payloads are not printed or specified.
`parseFloat` accepts finite decimal/exponent text, `nan`, `inf`/`infinity` and
signs, returns EINVAL 22 for malformed text and ERANGE 34 for overflow;
underflow yields signed zero/subnormal. Literal overflow remains a compile
error, distinct from this runtime `Result`.

Implementation: runtime slots 56–58 append after 40. After the independent
audit, both compiler and runtime use an exact base-2^16/2^32 big-integer
converter: up to 1200 significant decimal digits plus sticky, exact rational
comparison, binary search against binary64 neighbours, and a midpoint/tie
decision. This replaces the broken approximate 4x32 accumulator; it is not
`strtod`, host float, `long double` rounding, libc or libgcc. The F2 formatting path was provisional; F3A replaces it with the exact
shortest formatter described below. Runtime text/data are
`23,904`/`67,360`; no undefined runtime symbols.

After the exact converter and separator fix, direct self-compile is
`2,027,248` peak bytes (stage-3 `2,027,264`; port `2,027,616`), source
`13,390` lines / `503,098` bytes, binary `561,153` bytes. Under the approved
`2,500,000` cap with `472,752` bytes headroom. Fixed point holds (stage2 == stage3). `tests/float_f1.py` has 11
groups including 250 parse/format candidates (247 finite round-trip cases),
six exact audit counterexamples with pinned SHA-256 text, long mantissas,
huge-exponent bounds, compiler underscore separators (including a >1200-digit
separated fraction), parse strictness, special spellings, malformed/overflow
`Result` codes, and expected bits from an independent Fraction model. The independent audit
corpora pass; targeted fix re-review is still required.

Unimplemented at F2: shortest-round-trip formatting and float constants; F3A
below adds both. The public `.sym` release format/version, float-to-int and the
final API/style decision remain open.

## Float F3A checkpoint (unreleased, WIP)

**Formatter.** The provisional 17-digit display path is replaced by a shortest
round-trip formatter. It computes the exact finite decimal expansion with the
runtime big integers, then tries 1..17 significant digits; each candidate is
rounded to the nearest decimal (ties-to-even) and accepted only when the exact
same parser maps it back to the original bits. The first accepted precision is
the shortest. At each precision both the floor and ceiling decimal-grid
candidates are tested; if both round-trip, the canonical nearest to the exact
binary value is chosen (ties-to-even). Output uses fixed notation for decimal
exponents -6..20 and scientific notation otherwise, with `e±NN`; `-0`, `nan`,
`inf` and `-inf` keep their prior spellings. No x87/long-double result
determines correctness.

**Constants.** Source `const X = 1.5f` and `const X = -0f` are usable; unary
minus is a sign-bit const fold. Other constant arithmetic is **not** folded and
is rejected with the existing “constant value must be known” diagnostic rather
than silently using integer operations. Exported float constants use the WIP
symbol format's raw 16-hex-bit payload, e.g. `const Half: float =
0x3fe0000000000000`. Float constants require exactly `0x` plus 16 lowercase
hex digits; decimal, short/wide, uppercase, underscore and signed forms are
rejected structurally after rehashing in the reader matrix. `--check`
source-less and `--reemit-sym` are byte-tested.
This is format-5 WIP, not a release-compatible format: float is a new builtin
type, so the format must be bumped before merge/release.

**Positions.** Tests cover record and closed fields, arrays append/truncate,
zero/initialized globals, locals and register-home-disabled builds, direct and
method parameters/returns, interface dispatch with float members, structural
function types with mixed int/float arguments, lambda hidden-env calls,
`Result[float]`, module aliases/constants/globals, malformed symbol input and
GC poison. The one-word stack/GPR ABI is unchanged; no Stage B captures or
decimal values are involved.

**Tests.** `tests/float_f1.py` passes 19 groups under the normal compiler and
under `obo-noregs`: separator restoration, exact fixed counterexamples,
199-case literal audit, 384-case parse audit, 1,000-case shortest property
plus all 2,046 normal power-of-two bit patterns/neighbours and all subnormal
powers, both signs (12,580 values), against independent Fraction bits and a
Python repr digit-count reference, 250 parse/format round-trip candidates,
constants/source-less symbols, a rehashed malformed float-constant reader
matrix, and the value-position matrix. A private 10,000-value parse/format/parse run completed in 1.142 s
runtime (10 batches; 5.29 s compile); format-only 1,000 values took 0.107 s vs
0.001 s for the old provisional path (the both-neighbour search roughly doubles
the old one-candidate exact formatter). Full old gates: `run_fork` 1544,
`port_compare` 1551, `port_test` 573, `sym_test` 42, `doc_check` 62,
`mutate_io` 30/30, harness 27. No new F3B mutant/release matrix is claimed.

**Memory.** Final direct self-compile is `2,030,320` peak bytes (three runs;
stage-3 `2,030,336`; port `2,030,688`, RSS 31,488 KiB), source `13,417` lines /
`504,413` bytes, binary `569,345` bytes, runtime text/data `26,272`/`67,360`.
Headroom under the unchanged `2,500,000` cap is `469,680` bytes. `nm -u runtime.o`
is empty and the fixed point holds (`30124be9…`). Release identity is the
candidate `0.10`/format 6/ABI 1; Stage B is next planned as `0.11`.

## Float F3B preparation (historical; superseded by the 0.10 candidate below)

`floatBits` is no longer a public builtin. It is an internal diagnostic available
only with the explicit `--test-float-bits` CLI flag; without it the compiler
reports `floatBits is an internal test diagnostic; pass --test-float-bits`.
The flag is carried by the standalone float runner and the reader gate; normal
programs and the compact mandatory gate do not use it. This is a test-only
switch, not an environment-variable checks bypass.

**Mandatory functional integration.** `tests/float_core.obx` is a compact
program registered in `run_fork.py` (formatted literals, arithmetic,
comparisons, parse Result, constants, fields, arrays, globals, function value,
interface dispatch and `Result[float]`). `tests/float_errors.obx` registers
eight compile-error cases. `run_fork.py` also runs the source-less
`float_f1.py --reader-only` gate. These are in the normal `run_fork` suite, so
`mutate_fork` builds the actual mutated compiler source for them; the compact
core is also a `mutate_io` case, compiled and run against the actual mutant
runtime with its own `rt_dir`. The full 12,580-value power-of-two matrix remains
in standalone `tests/float_f1.py` (19 groups) and is not repeated in every
mutant invocation.

**Focused mutant catalog.** `mutate_fork` gains float literal-separator and
float-constant reader mutants (3/3 caught in a targeted run). `mutate_io` gains
float add/div/comparison, negative-zero formatting, shortest ceiling-candidate
and empty-parse mutants (6/6 caught against the actual mutant runtime). These
are preparatory targeted checks, not a claim that the full fork/port/meta
matrix has been rerun.

Current gates after this preparation: `run_fork` **1554 passed**, `port_compare`
**1551/1551** (old cases; the new float core is not in its flat old-file set),
`port_test` 573/573, `sym_test` 42, `doc_check` 62, `mutate_io` **36/36**,
`mutate_harness_test` 27, standalone `float_f1` **19 groups** under normal and
`obo-noregs`. The release identity/format decision is still open; this is not a
release candidate and no final full matrix is claimed.

## Float 0.10 release candidate (format 6)

Status: **released on `main`; the promotion and post-promotion evidence are in
"Float 0.10 release: merge, one promotion and post-promotion gates" below.** The
text in this section is the frozen candidate record, written before integration.
The user approved floats before Stage B: the candidate identity is
**`OBO_VERSION = 0.10`, `SYM_FORMAT = 6`, `SYM_ABI = 1`**. ABI 1 is intentional:
float remains one word in GPR/stack, the parameter/return convention is the same,
and runtime slots 0..40 retain their old prefix; slots 41..58 append. Stage B is
now next planned as **0.11**, not 0.10.

**Fixture migration.** 91 committed `.sym` fixtures moved from format 5 /
`compiler "0.9"` to format 6 / `compiler "0.10"`; the intentional format fixture
moved from 6 to 7 (expected `has format 7; this compiler reads 6`), `compiler
"9.9"` and `abi 9` exceptions remain. Headers are outside the interface hash, so
body hashes were not recomputed. `sym_test.py` was updated to actual compiler
identity; no `--update` was used. Public float constants use exactly `0x` plus 16
lowercase hex bits in format 6; format 5 is rejected before the float body is
read. A new `modules/run/float_exports` source case covers float aliases,
constants, globals, record fields, arrays and functions.

**Frozen release gates (source/tests at this checkpoint):**
`run_fork` **1555**, `memory_log_check` 4 sources, `float_f1` **19 groups** in
normal and `obo-noregs` modes, `module_test` **107**, `sym_test` **42**,
`sym_fuzz` **200/200**, `doc_check` **62**, `homes_check` and `homes_edge`
pass, `elf_test`, `io_test`, `meta_test` **34**, `port_test` **573/573 with 10
encoder groups**, default `port_compare` **1552/1552**, 0.8 seed compare
**1269/1269**, imports **28/28**, stdlib **16/16**, tools **509/509 + memory
4/4**, pinned eza **554/554 + memory 5/5**. Full `mutate_fork` **654/682 caught,
28 equivalent**, `mutate_port` **531/531**, `mutate_io` **36/36**, `mutate_meta`
**71/82 caught, 11 equivalent**, harness **27/27**. The 0.9-seed comparison is a
non-gate diagnostic: it reports expected format/header differences (1439/1552)
because the old 0.9 compiler emits format 5.

**Final measurements:** direct self-compile **2,030,320** live bytes (three
runs), stage-3 **2,030,336**, port **2,030,688** (RSS 31,488 KiB), headroom under
2,500,000 **469,680**; source **13,417 lines / 504,413 bytes**, compiler binary
**569,345 bytes**, runtime text/data **26,272/67,360**, `nm -u runtime.o` empty,
fixed point sha256 **`30124be958a772db7b0236b212ee526fd209c3b357a080beeaf57c6966a1cd83`**.
The compiler is still built by the local old 0.8 seed; main's promoted 0.9 seed
and the future 0.10 promotion are integration concerns after independent review.
No merge, push, promotion or seed copy was done.

## Float 0.10 release: merge, one promotion and post-promotion gates

Status: **released on `main` as `obo 0.10`, symbol format 6, ABI 1.** The reviewed
candidate is `fdfac1c01bf6ce141fe086be648e7476bb770e24` (fixes `ae4b339` and
gate preparation `614ab55`); it was merged with `--no-ff` from the promoted 0.9
line at merge commit `bf0abdb`. The only overlap with the 0.9 release was
documentation; it was resolved by keeping the Stage A record and adding the
numeric sections, with no source, runtime or test semantic change. The candidate
evidence is `/tmp/opencode/float-0.10-release-final-20261031-090739` (13 input
sha256 entries verified against the candidate tree; full frozen gate log set).
This release adds the separate post-promotion evidence in
`/tmp/opencode/float-0.10-postpromotion-20261031T091344Z` (private).

**Pre-promotion build and fixed point.** The old seed was preserved as
`old-seed-obo-0.9`, `540,673` bytes, sha256
`3d36927389beee355a64cea61e34d3f015c21c149493c45848effad3917cab37`,
`obo 0.9 sym-format 5 abi 1`. The ignored runtime objects and bins were removed
and rebuilt from the merged `runtime/runtime.c` and `runtime/entry.S`, then the
compiler was built with the explicit command `./seed/obo src/obo.obo -o obo
--rt .`. It is `569,345` bytes, sha256
`30124be958a772db7b0236b212ee526fd209c3b357a080beeaf57c6966a1cd83`,
`obo 0.10 sym-format 6 abi 1`; source is `13,417` lines / `504,413` bytes,
sha256 `ebc1dad2…`; runtime text/data are `26,272`/`67,360` bytes, hashes
`2c111527…`/`0bd93a12…`. Three direct self-compiles are byte-identical to the
compiler (`30124be9…`) and peak at `2,030,368`, `2,030,368`, `2,030,368` live
bytes; stage 2 == stage 3 (`cmp` clean) at `30124be9…`. The old 0.9 seed builds
the source to the same image, so the main seed did not regress the merge.

**One promotion.** `make promote` was run once with the recipe unchanged; its log
is `promote.log`. It rebuilt stage 2 and stage 3, ran `run_fork` (**1,555
passed**) on the promoted stage-2 compiler, and copied `.stage2` to `seed/obo`.
The seed moved from `540,673` to `569,345` bytes, sha256 `30124be9…`, and the
post-promotion tree rebuilt the compiler from that seed to the same hash. No
other seed copy was made.

**Post-promotion gates on the frozen source/tests and promoted seed.** Logs,
paths and timings are in the private evidence directory; the same commands are
listed there. `run_fork` **1,555 passed, 0 failed**; `memory_log_check` **4
sources**; `float_f1.py` **19 groups** under the normal compiler and **19 groups**
under `OBO=noregs` (12,580 power-of-two values each); `module_test` **107/107**;
`sym_test` **42/42** with golden comparison and no `--update`; `sym_fuzz`
**200/200**; `doc_check` **62 examples, 0 problems**; `homes_check` **13**;
`homes_edge` **1,028 statements, 119 refused by both, 0 differ**; `elf_test` ok;
`io_test` **0 failures**; `meta_test` **34**; `port_test` **573/573** with **10
encoder groups**; default `port_compare` **1,552/1,552**; the standalone seed
comparison `OBO_REF=obo/seed/obo` is **1,394/1,394 identical with zero version
skips** (seed mode excludes the 158 module-tree cases by design, so the default
1,552 is the full matrix); `imports` **28/28**; `stdlib` **16/16**; `tools.py
--require-reference` **509/509** and `--memory` **4/4**; strict pinned `eza.py
--require-reference` **554/554** (reference sha256 `10457dde…`) and `--memory`
**5/5**. Tool sizes are cat `53,249`, wc `57,345`, sort `81,921`, head `53,249`
and tail `61,441`; eza is `229,377` bytes. The eza 10k worst live peak is
`2,184,368` bytes under the unchanged, separate `EZA_PEAK_MAX = 2,752,512`.

**Identity after the gates.** Source `ebc1dad2…` (`13,417` lines / `504,413`
bytes), runtime `e0db363a…`/`30ececb4…`, runtime text/data
`2c111527…`/`0bd93a12…`, compiler and seed both `30124be9…` / `569,345` bytes,
and `--version` `obo 0.10 sym-format 6 abi 1`. The final fixed-point rebuild from
the promoted seed is stage 2 == stage 3 == seed == compiler. Three direct
self-compiles peak at `2,030,368`; stage 3 peaks at `2,030,320`; the port
comparison peaks at `2,030,448` and allocates `44,519,440` (direct allocation
`44,518,160`). All are under the unchanged `2,500,000` numeric cap
(`469,632`/`469,552` spare); the separate `EMPTY_ARRAY_MIN_DELTA = 30_000`
oracle is unchanged.

**Mutation gates.** `mutate_fork` **654/682 caught, 28 equivalent**, 0
missed/stale/broken (190.26 s); `mutate_port` **531/531** (160.82 s);
`mutate_io` **36/36**, including the empty-array self-host memory oracle
(19.94 s); `mutate_meta` **71/82 caught, 11 equivalent** (`50/52` runtime,
`21/30` compiler; 148.54 s); `mutate_harness_test` **27/27** (0.35 s). No test
assertion or oracle was weakened, and no verdict was cached.

**Fixture check.** All 92 committed `.sym` files carry the new identity: 89 are
`obosym 6` / `abi 1` / `compiler "0.10"`; the three intentional exceptions are
the format fixture (`obosym 7`, expected to be rejected), the compiler fixture
(`compiler "9.9"`) and the ABI fixture (`abi 9`). Across the 92 changed files the
bodies after the three header lines are byte-identical to the pre-float fixtures;
only the format/compiler header (and, for the format fixture, its intentional
format bump) changed.

**Scope.** No Stage B capture lists, `^I`, decimal, template work or new language
scope was included at this checkpoint; Stage B was later released as `0.11`. The local untracked
`parse_actual.txt` and `parse_run.err` files were not touched, staged or
deleted. The release commits contain the merge, the promoted seed and the release
documentation only; evidence logs remain private under `/tmp/opencode`.

## Stage B capture-list release candidate (0.11, format 6, ABI 1)

Status: **historical release-candidate checkpoint (implemented on the isolated
review branch before merge); it was later merged, promoted and pushed as 0.11.**
The six Stage B commits are the five
original commits forward-ported onto main `4a072cf5f5db8fe661ac65ff6de06e6ec`
(the released floats line) with `git rebase`, plus the final `765df3b`
completion commit; nothing was merged. The approved F1/F2 float work,
firewall/source fixes and the shared 2,500,000-byte budget survived the rebase;
documentation conflicts were resolved by keeping main's released record and
adding the Stage B text, never by blind overwrite. The promoted `seed/obo`
remains the released **0.10** compiler; no seed copy was made.

**Identity and fixed point.** `OBO_VERSION = "0.11"`, `SYM_FORMAT = 6`,
`SYM_ABI = 1`. Source is `13,883` lines / `524,165` bytes; the compiler is
`585,729` bytes, sha256
`198f3268eb4fa24fb657a4ee72829149d6c3bfec6be4cc30e6b8480368633c9b`. Stage 2,
stage 3 and the compiler are byte-identical, and the released 0.10 seed builds
that same image from source. Three direct self-compiles peak at `2,085,872`
bytes live and allocate `46,373,536`; the port comparison peaks at `2,086,272`
and allocates `46,378,000` (`413,728` spare under the unchanged 2,500,000 cap).
The separate empty-array oracle is unchanged: baseline `2,086,320` -> mutant
`2,132,848`, delta `46,528`, floor `30,000`.

**Capture semantics.** The capture list is explicit and resolves only enclosing
locals/parameters; globals and constants are used directly. `int`/`byte`/`bool`
and now `float` are one-word value snapshots; a float snapshot stores its raw
binary64 bits with no conversion, and float aliases/parameters take the same
mode. Record, closed, array, optional and `Result` captures remain read-only
borrows of the whole local, so float fields, elements and `Result[float]`
payloads are read through the existing reference/narrowing machinery. `mut`,
`move`/owned, string and interface captures remain unsupported; a carrier that
holds both a float and an interface still fails the interface firewall. A
captured float write reports `cannot change captured 'x': captures are read-only;
pass a mutable parameter instead`, not the generic immutable-let message.

The P2 review fix is included: the incomplete-carrier query recurses through
`?`/`[]`/`^`/`Result` and record/closed fields, terminates on cycles with a
visited list, and runs before the interface classifier. A direct shell and a
wrapped shell both report `<T> is declared but not yet defined`; a genuinely
interface-bearing carrier keeps the interface message. The hidden environment
is still one read-only `[code][caps...]` word in the creator's frame; the
one-word value ABI, format 6 and ABI 1 are unchanged.

**Gates.** `run_fork` **1,644 passed**; `module_test` **112**; `sym_test`
**44/44**; `sym_fuzz` **200/200** (seed 20261028); `doc_check` **66 examples,
0 problems**; `homes_check` and `homes_edge` pass; `memory_log_check` 4
sources; `elf_test`, `io_test`, `meta_test` 34 and `imports` 28/28 pass;
`stdlib` 16/16; `port_test` **582/582** with 10 encoder groups; default
`port_compare` **1,641/1,641**; `float_f1` **19 groups** in normal and
`--noregs` builds; the new `run/lambda_float_capture.obx` passes in both
register modes. `tools.py --require-reference` **509/509** and `--memory`
**4/4**; eza memory **5/5** (the pinned eza reference is not on this host's
`PATH`, so the reference-golden run was not executed).

**Mutation gates.** `mutate_fork` **680/710 caught + 30 equivalent**, 0
missed/stale/broken (the catalog includes the new B26 recursive-incomplete,
B27 float-snapshot and B28 float-write-diagnostic mutants; B12 and B25 remain
the two documented defensive equivalents). `mutate_port` **531/531**;
`mutate_io` **36/36** including the empty-array self-host oracle;
`mutate_meta` **71/82 caught + 11 equivalent**; `mutate_harness_test`
**27/27**. Older Stage A mutants are retargeted only where Stage B changed the
shared argument helper's text; no expectation was relaxed and no false
equivalence was introduced.

**Symbol fixtures and seed comparison.** All committed `.sym` fixtures moved
to `obosym 6` / `compiler "0.11"` (headers only; body hashes were not
recomputed). The three intentional exceptions retain `obosym 7`, `compiler
"9.9"` and `abi 9`. Against the released 0.10 seed, `OBO_REF=obo/seed/obo`
reports **1,393/1,393 identical**, exit 0, with no unexpected skips. The nine
Stage B `(0, 11)` entries (8 run files plus `errors/closures.obx`) carry an
explicit minimum in `tests/port_compare.py` and are skipped only because the
0.10 seed cannot compile capture syntax. The default 1,641 comprises 1,476 flat
and 165 non-flat cases (154 module tree/symcheck, 4 CLI, 5 special, 1 reemit and
1 token check); the 248-case difference is those 165 non-flat cases plus 83
skipped flat cases (8 run files and 75 error cases). A promoted 0.11 seed would
compare all of them.

**Evidence and remaining risk.** Logs and a manifest are under
`/tmp/opencode/lambda-stage-b-final-20261031-...` (private). Remaining work is
independent review and integration; the seed is not promoted, `^I`/owned/string/
interface captures remain later, and the eza reference-golden gate needs the
pinned binary on `PATH`. No push, merge, promotion, seed copy, decimal or
template work was performed.

## Decimal language integration (0.12 release, format 6, ABI 1)

**Status: released on branch `decimal-language`, rebased on the released 0.11
HTML-escaping main `34b798bc4c76cf41362d3b374c13b80e7b43f73b`. The independent
review is closed and the fixes are approved; the parent merges and promotes this
branch. This branch does not promote.** After that promotion `seed/obo` is the
same 0.12 image (643,073 bytes, sha256 `d98d6b5f…`); the released 0.11 seed
(585,729 bytes, sha256 `01fa9ed3…`) built this image once. The approved design is
`design/decimal-language-design.md`; the reviewed arithmetic/text core stays in
`experimental/decimal/` and is copied unchanged into
`runtime/decimal24_core.inc` (provenance hashes in that file and in the package's
`PROVENANCE.md`). The release adds only the compiler/runtime minimum tier.

**Identity and fixed point.** `OBO_VERSION = "0.12"`, `SYM_FORMAT = 6`,
`SYM_ABI = 1`. Source is 14,608 lines / 550,897 bytes; the compiler is 643,073
bytes, sha256 `d98d6b5f5dce123457fce1ba781ee6aeabd3a361c1c5708fff1a7874b1e23e40`.
Stage 2 and stage 3 equal the compiler. Three direct self-compiles peak at
2,194,160 bytes live and allocate 49,033,600; the port comparison peaks at
2,194,560 and allocates 49,038,064, all under the 2,500,000 budget. The
empty-array oracle still has a 47,840-byte delta (baseline 2,194,608), above
the 30,000-byte floor. Before promotion, a normal `println("hello")` built by the 0.11
seed and by the 0.12 compiler against this runtime is byte-identical (69,633
bytes); the 0.11 seed builds the 0.12 image once.

**Language and ABI.** A bare fractional/exponent literal is exact decimal;
integers stay `int`; `f`/`F` stays float; no `m`/`d` suffix. Literals carry a
128-bit coefficient, `int16` exponent and `uint16` display scale; `1.2300`
prints `1.2300` and `-0.00` is canonical positive zero. Out-of-range literals
are compile errors. Decimal values are 24 bytes (three words: coefficient
lo/hi, packed exponent/dscale/sign). Parameters are by value in 24-byte stack
slots; decimal returns use a hidden destination pointer at
`16 + visible-parameter-bytes`; locals use 24-byte slots. `+ - * %` are exact
and trap rather than round; unary minus preserves scale; comparisons ignore
scale. `/` and `/=` are compile errors directing `decimal::div(a, b, scale,
mode)`. `decimal::div` and `decimal::quantize` require an explicit `0..32768`
scale and mode and return `Result[decimal]`; `decimal::toInt(d, mode)` returns
`Result[int]`. `decimal(i)`/`decimal::fromInt(i)` are the explicit int
conversions; float interop is absent and `int(d)` points to `toInt`.
`print`/`println` preserve display scale and fall back to canonical text rather
than rounding when a capped multiply display scale hides a nonzero digit.

The tier supports decimal locals, parameters/returns and `Result[decimal]`
(including `ok`, consuming `match` and `try`). Globals, constants, record
fields, arrays (24-byte stride), captures, methods, interfaces and function
values are refused with explicit diagnostics; `?decimal`, `&decimal`, owned/mut
decimal and raw bits are absent. Captures, owned/mut decimals, stdlib
`format`/`parse`, `decimal(p,s)`, database adapters and DEC display beyond the
core's 32768 strict quantize limit remain later work.

**Traps and Result codes.** Decimal exact arithmetic (`+ - * %`) traps with
exit 10 and diagnostic ids 40-49: 40 not exactly representable, 41 overflow, 42
division by zero, 43 invalid, 44 unsupported range, 45 precision overflow, 46
exponent range, 47 display range, 48 buffer too small, 49 other. The ids are
trap diagnostics only. `decimal::div`/`quantize`/`toInt` never trap for value
errors; their `Result` error code is the reviewed core status: 1 overflow,
3 inexact/`UNNECESSARY`, 4 invalid, 5 division by zero, 6 unsupported range;
7/8/9 are reserved core text/binding statuses not currently produced by these
APIs, and 2/10 stay internal. Request scale/mode are validated before the core:
constant invalid values are compile errors, runtime scale outside 0..32768
returns 6, runtime mode outside 0..7 returns 4. `+ - * %` keep the two main trap
messages `obo: decimal result is not exactly representable` and
`obo: decimal overflow`.

**Runtime/build.** `runtime/runtime.c` includes `decimal24_core.inc`; the
Makefile and the mutation harnesses carry that dependency/copy. Entry slots
59-72: slot 59 is the baseline reserved slot and the hosted-exit slot in the
cinterop table, so decimal slots are 60-72 in both builds. `runtime_text.bin`
grows to 53,888 bytes and `runtime_data.bin` is 67,360; the
freestanding runtime and compiler have no undefined symbols (`nm -u` empty), no
libc/libm/libgcc/`__udivti3`, and the reviewed D1/D2 freestanding runner still
reports 32,719 C checks, 0 failures, with empty `forbidden_hits`/`nm -u`.

**Review fixes (a21e407 -> release).** The multiply display cap now comes from
the exact result's coefficient/exponent, so operand order cannot change display
scale (`123...678.0 * 0.1` and `0.1 * 123...678.0` both print one fractional
digit; `1e38 * 0.1` and `0.1 * 1e38` both print no point). Explicit decimal
requests are a two-word `[scale, mode]` block validated before the core: constant
invalid values are compile errors, runtime scale outside 0..32768 returns core
status 6, runtime mode outside 0..7 returns core status 4, and no value can alias
a different valid request. `Result` errors carry core statuses; the 40-49 ids are
trap diagnostics only. `decimal` is a reserved type name. The design text now
matches the immediate-stack literal materialisation, and the pre-existing
lexer-wide underscore-placement gap is noted.

**Gates (this rebased release).** The pre-decimal 0.11 HTML-main baseline counts are
`run_fork` 1,646, `port_compare` 1,642, `port_test` 583 and `module_test` 112
(with `sym_test` 44 and `float_f1` 19 unchanged). The decimal addition is 58
flat cases plus one module tree: 14 run files, 44 error cases and
`modules/run/decimal`. `run_fork` is **1,705 passed** (1,646 + 59 including the
module tree); `module_test` **113** (112 + 1); `sym_test` **44/44**; `port_test`
**598/598** (583 + 15 decimal runtime/slot checks); default `port_compare`
**1,701/1,701** (1,642 + 58 flat + 1 module tree); `OBO_REF=obo/seed/obo`
**1,477/1,477** (the one HTML case plus the old flat cases; decimal cases stay
skipped by `(0, 12)`); `float_f1` **19 groups** normal and `--noregs`;
`doc_check` **69 examples, 0 problems**; `sym_fuzz` 200/200; `imports` **29/29**
including the HTML library; `stdlib` 16/16; `io_test`/`meta_test`/`memory_log_check`/`elf_test` pass;
`mutate_decimal` **22/22 caught** (focused compiler and runtime paths in a
separate gate; the historical `mutate_fork`/`mutate_port` catalogs are otherwise
unchanged);
`mutate_io` **36/36**; `mutate_meta` **71/82 caught + 11 equivalent**. The
decimal D1/D2 evidence is in `/tmp/opencode/decimal-integration-rebase`; all
gates were rerun on the rebased tree, not reused from the pre-rebase branch.

**Symbol fixtures.** All committed `.sym` fixtures moved to `compiler "0.12"`
(the version line only; body hashes unchanged), and the `compiler/expect`
fixture now expects the 0.12 stale message. Format remains `obosym 6`, ABI 1.
The stale-version mutant pattern in `mutate_fork` moved from 0.11 to 0.12.
