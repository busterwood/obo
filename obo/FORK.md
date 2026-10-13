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

- `&T` and `&mut T` in parameter and local types. References are borrowed
  pointers to owned objects (records, closed types, arrays): they can't be stored
  in fields, elements or globals, returned, or made from globals or temporaries.
- Borrowing is implicit where the destination is a reference: passing a value to
  a `&` parameter, `var p = list` when `list` is a reference, `p = p.next`.
  `&place` borrows explicitly, as in `var r = &b.items`. Anything owned reached
  through a reference is borrowed automatically, since nothing can be moved out
  through one.
- Writes need a mutable reference (from an owned local or a `&mut` parameter).
- **Invalidation.** Each reference records its root (an owned local or reference
  parameter) and whether it may point below the root's own object. Assigning an
  owned field or element, `take`, `truncate` and passing a `&mut` argument
  invalidate the root's deeper references, except the one the change went
  through; moving, reassigning or freeing the root invalidates them all. An
  invalid reference can't be used until it is pointed somewhere again, with the
  same branch and loop agreement rules as moves.
- Passing a `&mut` argument and another reference into the same root is an
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
element of a walked array as `&mut` invalidated the array itself, though a change
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
- **Syntax.** Small: `&` on read-only parameters and `&mut` on mutating ones
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
itself exceeds `PEAK_BUDGET` (about 3% over the measured 1,266,368 bytes; patch
`tools/port_budget.py`). Lower the budget as memory improves; raise it only with a
reason recorded here.

**Tests.** `python3 obo/tests/mutate_port.py`: 6 mutants of the port, all caught
(two divergences by mismatch, two early frees by dangling-handle traps, two
retentions by the budget).

**Open.** Time: the port is about 50% slower than the GC on the same work and
allocates 26 MB in total; every Item allocates four arrays, and jump lists and
items are copied (copyInts, dupItem). Then the structural move: a committed
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
  parameters `&mut` is fine. A change through a reference frees only things below
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
borrowed from: `f(p: &mut Box) { let x = &g.items; p.items = ...; use x }`,
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

    b.items[0].next = h(b)       // h(b: &mut Box) replaces b.items: writes into freed memory

Today `pinned` only stops the target's root being moved. Both this and the variant
through a reference local (`let e = &b.items[0]; e.next = h(b)`) compile; the leak
check catches them at run time only by luck. Next step.

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
