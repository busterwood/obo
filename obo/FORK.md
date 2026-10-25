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
in the port: 539 of 539 behave the same on the merged branch (462 before main's step 3). The port compiling itself is a fixed point
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

Refused: a move of a local that a handle was made from, inside a loop. A handle `let h = handle(a)` made from a whole owned local records that local as its origin, so its held places are keyed to `a`. A whole move of `a` (`var c = a`, `var c = id(a)`, a record field, `replace(a, ...)`, `take(a)`) or an assignment over `a` clears that origin on every `let` handle that names `a` (`originMove`). Such a handle then has no origin: it is an identity, which every move and free reaches, so a later index through it that moves or frees something is refused by the held-place checks (probes u1, u3 and the optional take, in `tests/errors/held_places.obx`). This is sound because a move does not free the object: `c` holds the same object, and the handle still names it by its generation. The one case the clearing does not cover is a loop, because the body is compiled once: a whole move of `a` inside a loop that is deeper than the declaration of a live `let` handle with origin `a` is refused ("cannot move 'a' inside a loop while handle 'h' made from it is in scope"), since the next iteration would still see the origin. A handle declared inside the loop is fresh each iteration and is not refused (`c_fresh_loop`). A temporary `handle(a)` records no origin on any symbol, so a move after it is accepted (`c_p1_let_then_move` to `c_p5_temp_then_move`, run tests). The origin list (`originSyms`) keeps only the handles in scope: a block's handles leave it when the block closes, because their symbols are freed with it (a dangling entry there was a compiler abort in an early version of this rule). An assignment over `a` clears the origins but is not refused: `let h = handle(a); a = nb(); h.items[0]` compiles and traps at run time on the handle's generation check, as main does too. That is a gap in the held-place model (an assignment is not a free event for handles), left for a separate change. A handle made from a field, or through another handle, has no origin; its place is an identity, and a move of any local while it is held is refused, because `heldMove` hits every identity entry (`let h = handle(p.first)`, and the chained `hh.hb.items[...]`). A handle parameter has no origin, and the same conservative rule applies (f3 is kept refused). A move or free of a local the callee declared (not a by-value parameter, not a global) no longer reaches a reference parameter's place, because a reference cannot point into the callee's own object; this is the false positive `p.items[sinkBag(t)]` (f1, f2) and its replace and take forms (f4, f5). A by-value parameter's object and a global are still hit. Refused still: f8, two `mut` parameters where a free through one is indexed through the other, since the two may name the same object. A reference to a field of a reference parameter is bound to the whole parameter (`itemBind` drops the field path through a deref), so `let r = &p.ys; p.xs[sinkI(replace(r, ...))]` is refused although the fields differ; the direct form `p.xs[sinkI(replace(p.ys, ...))]` is accepted. This is a precision limit of the binding model, left as it is. The refused forms are the cases in `tests/errors/held_places.obx`.

Not closed: the argument-order hole `two(&xs[0], eatArr(xs))`, a reference into an array passed in one argument with the array moved by a later argument of the same call. It is a known hole, left alone in this round as the coordinator asked; the probes are `review16/ra/ra1`, `review16/p/a1` and `review16/ra/ra3`, listed with the holes above.

Probes that were written but are not valid programs on either compiler (typing or syntax errors, so they tested
nothing) are h1-h13, k5-k8, z1-z5, w2, x3, y4, y9 in the probe set (`review14/`).

**Tests.** `tests/errors/call_targets.obx` has 58 cases (the earlier 59, less the handle-of-another-local case that is now accepted: its run control is in `tests/run/call_targets.obx`). `tests/errors/held_places.obx` has 26 cases: the probes above, the review17 handle probes H1-H9, the compound and assignment forms, the origin move in a loop, the replace and take holes (u1, u3, the optional take), a replace of the owner's array through a handle, handles made from a field and from a global, a chained handle, the free through a reference to a handle, and the by-value parameter moves and frees. `tests/run/held_places.obx` has 26 functions with outputs: the accepted forms above, the callee-local cases f1, f2, f4, f5, the origin moves that are now accepted (plain, call result and record field: `c_mv_sibling`, `c_mv_call`, `c_mv_field`), the temporary-handle forms p1-p5 (`c_p1_let_then_move` to `c_p5_temp_then_move`), and a handle declared in a loop body whose owner is moved there (`c_fresh_loop`). The nit about the last-designator globals (`lastPlace`, `lastRaw`, `lastOrigin`, `lastIdent`, `lastViaHandle`): they are set once, at the end of `designatorT` after its index expressions are parsed, and each reader takes them straight after `designator()`, so a nested `expression()` cannot leave a stale value; the only path past `nameItem` without setting them is after an error. No probe found a case.

**Mutants.** `tests/mutate_fork.py`: 131 entries, 127 caught, 4 equivalent (listed in its `EQUIVALENT` table with the reason, and reported as EQUIV). The held-place mutants are: a replace or take that does not free, moves that do not reach held places or do not free handle targets, identity frees that do not reach their entries or ignore the path, owned frees that ignore the path, the assignment exemption removed for owned places and not applied through handles, origin ignored when holding and when freeing, whole local arrays hit by identity frees, reference parameters given their owner's place, each hold removed, the binding's path ignored, and the second review's gaps: an origin without the non-handle or whole-path guard, a global origin, chained handles keeping the first origin, the handle-root and reference-root joins (held and freed), unknown frees hitting only whole arrays, every held place whole, moves and frees reaching reference parameters from callee locals, and a by-value parameter counted as a callee local. The four equivalents: `origin needs no non-handle designator` and `origin needs no whole path` (a designator through a handle, or with a path, has no sym, so `wholeOwnerOf` gives nil); `origin of a global is kept` (`owned` is set only by `addOwnedLocal`, so a global never has an origin, and the storage test is redundant); and `a handle root of a reference is not an identity (held)` (equivalent because the older `heldLost` check on the reference local catches the case, not the held entry: under the mutant `foreign` is false, so `heldMove` does not hit). `tests/mutate_port.py`: 106 entries, 106 caught, judged at the committed budget of 1,351,000 (see "The port's memory budget" below); the port mutants change `src/obo.obo` and are judged by `port_compare`.

**Fuzzer** (`tests/own_fuzz.py`): invalid kinds, `replace_sink_index_stmt` (a field's array indexed by a pure
call that consumes a replace of that array), `compound_target_stmt`, `append_target_stmt`,
`truncate_target_stmt`, `index_target_stmt`, `index_assign_target_stmt`, each a field of a local
record whose array a call in the same expression replaces; whole-local kinds, `moved_index_stmt`,
`moved_assign_stmt`, `moved_append_stmt`, `moved_truncate_stmt`, `moved_replace_stmt`, each a local
array passed by value to a call in the expression that uses it; and `moved_root_index_stmt`, a field's
array indexed by a by-value call on its record. All are rejected.

**Messages.** A reference invalidated in a loop and then continued past said "is moved inside
the loop; it must have a value again before continue"; it now says it is invalidated and must be
pointed again. Not done: "'p' is invalidated on one path but not another" is still reported for
a reference that is never used afterwards (`red2/n12.obo`). A reference invalid on one path and
never used again is harmless, so the report should come only at a use, but that needs the join
state to keep the flag on both paths rather than an error at the join, and the joins are being
changed elsewhere, so it is left.

**The port.** It compiles unchanged under the new rules (no spot needed rewriting).

**The port's memory budget, after the merge of main.** `PEAK_BUDGET` stays at 1,351,000, and the merged port passes it. The port compiling itself peaks at 1,229,424 bytes live, a margin of 121,576 bytes under the budget. The origin rule (`originSyms`, `originMove`, the block marks) added 4,560 bytes to the 1,224,864 measured before it. The merge of main's step 3 (defer uses) added 13,760 bytes to the 1,211,104 this branch measured before it. The budget was not raised.

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

**Holes found, not fixed here.** This one is accepted by HEAD's seed too, and by the current
compiler. Still open after the follow-up below. (The reference re-pointed on one branch is fixed, and a re-point
inside a defer body is refused: see "Reference bindings per path (done)" below.)
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
- Future work: a change made through a reference is taken to reach the whole root that the reference
  borrows from, not the place it names. This is the same as main, and it rejects some valid programs
  (a change to one field through a reference, with a reference to another field read after it). A
  narrower rule is future work, and so is applying a defer body's re-point at each exit in place of the
  refusal.
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
the review gave them. The rules are in `LANGUAGE.md` sections 4 and 10.

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
  points into). The argument is checked before a later argument's call runs.
- The precision limits in "Reference bindings per path": a change through a reference reaches its whole root,
  and the known false positive in `LANGUAGE.md` section 10 (a re-point in the same iteration, with the reference
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
- Not changed: argument evaluation order (still open).

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

Status: Phase 0 done; Phase 1 done except loop inversion (designed below, deferred); Phases 2 and 3 planned. Phases are ordered by expected value per unit of cost.
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

**Done** (patch `tools/port_cheap_codegen.py`), except loop inversion:

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

**Loop inversion: design (deferred until the per-path bindings branch merges, since it
rewrites the same loop functions).** A `while` reads its condition before its body, but the
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

`PEAK_BUDGET` stays at 1,351,000. On the merged branch the peak is 1,211,104 bytes, a margin of 139,896 bytes. That margin is less than the
mutant "parameters kept" adds (about 190 KB), so the budget check now catches that mutant directly; the
`KEEPS_ALIVE` check in `tests/mutate_port.py` still applies on its own.

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

- **Byte identity** (resolved: dropped, compare behaviour). `tests/port_compare.py`
  required the port's executables to match byte for byte, and any optimization changes
  the bytes. The `--no-opt` flag proposed here was not built. Stage B now compares
  behaviour: the same compile outcome (success, or the same error at the same
  line:col), and for a program that runs, the same stdout, stderr and exit code with
  `OBO_CHECK=1`. The fixed point (stage 2 = stage 3, in `make promote`) still compares
  bytes, since the compiler compiles itself.
- **Pre-scan.** Confirm that a token pre-scan is acceptable under the single-pass goal
  before Phase 2 starts.
