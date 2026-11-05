# Borrow-class types: borrows inside values

Status: decided 2026-11-05 (D1-D10 as recommended in section 9), implemented
on branch `borrow-types-v2` (rebased on main, the prototype's gaps closed;
section 11). SPEC.md now has the rules (sections 4.1, 4.5, 6.1, 7.8, 8, 9.4,
10, 16); section 4 below is the proposal they came from. The first prototype
is branch `borrow-types` (section 10), kept as it was.

## 1. Motivation

A borrow in obx is second class (SPEC 10 rule 1): it can be a parameter, a
receiver, a `let` or a result, and nothing else. That rule makes the checker
simple and keeps lifetimes out of the language, but three common things cannot
be written without either copying or a callback:

- **Splitting without counting.** Splitting a line into `[]str` (as strings'
  split did before this work):
  every piece takes a reference on the line's block and gives it back when the
  array is dropped. A `[]&str` of views would cost nothing per piece, but an
  array element cannot be a borrow.
- **Tokenizers.** A token is naturally `{ text: &str; kind: int }`, a view of
  the source plus a tag. Today it must hold a `str` (counted) or two ints that
  every user turns back into a substring.
- **Iterators.** `Map.each(f)` takes a closure; there is no `for (k, v) in m`
  because an iterator would have to hold a borrow of the map, and a loop item
  would have to be a value holding borrows.

The proposal: allow borrows inside value types, and make every type that holds
one *borrow-class*: second class like a borrow, with a root, frozen while it is
in scope, returned only rooted at a parameter. Nothing new is written in
function signatures; the checking stays per function.

## 2. Research

### Rust: lifetimes as types

Rust puts a lifetime parameter on every type that holds a reference
(`struct Token<'a> { text: &'a str }`), and checks a function body against its
signature alone. Elision rules fill in the common cases: one input lifetime
goes to every output, and `&self` methods return borrows of `self`
([reference: lifetime elision](https://doc.rust-lang.org/reference/lifetime-elision.html),
[the book, ch. 10.3](https://doc.rust-lang.org/book/ch10-03-lifetime-syntax.html)).
`Iterator::next(&mut self) -> Option<Self::Item>` returns items that do not
borrow the iterator: the item type cannot name the `&mut self` lifetime
([std::iter::Iterator](https://doc.rust-lang.org/std/iter/trait.Iterator.html)),
which is what lets `collect` keep them all. An iterator whose items borrow the
iterator itself (a "lending" or "streaming" iterator) needed generic
associated types, stabilised in 2022, and is still awkward
([GATs stabilisation](https://blog.rust-lang.org/2022/10/28/gats-stabilization.html),
[the push for GATs](https://blog.rust-lang.org/2021/08/03/GATs-stabilization-push/),
[an alternative to lifetime GATs](https://sabrinajewson.org/blog/the-better-alternative-to-lifetime-gats)).

Lessons: (1) the source of a result's borrows is the whole question; Rust
answers it with names, elision covers most code. (2) Items that borrow the
collection, not the iterator, are the useful default; lending iterators are a
separate, rarer thing.

### Swift: non-escapable types and lifetime dependencies (the closest design)

[SE-0446](https://github.com/swiftlang/swift-evolution/blob/main/proposals/0446-non-escapable.md)
adds `~Escapable`: a type whose values cannot escape the scope they were made
in. A non-escapable value cannot be stored in an escapable type, a class or a
global, cannot be captured by an escaping closure, cannot be passed to
`Task.init`. Generic code opts in with `<T: ~Escapable>`; without it a type
parameter is escapable, so existing generics need no review. This is exactly
the shape proposed here, with `T: borrow` for `T: ~Escapable`.
[SE-0447 `Span`](https://github.com/swiftlang/swift-evolution/blob/main/proposals/0447-span-access-shared-contiguous-storage.md)
is the first big user: a non-escapable view of contiguous storage, obx's
`&[T]` / `&str` as a value.

Results are tied to arguments by lifetime dependencies
([pitch #2](https://forums.swift.org/t/pitch-2-lifetime-dependencies-for-non-escapable-values/78821),
[the reference guide](https://github.com/swiftlang/swift/blob/main/docs/ReferenceGuides/LifetimeAnnotation.md),
[implementation](https://github.com/swiftlang/swift/pull/85998),
[the earlier `dependsOn` pitch](https://github.com/apple/swift-evolution/pull/2305)):
`@lifetime(borrow x)` (the result borrows `x` itself; `x` is frozen while the
result lives) and `@lifetime(copy x)` (the result inherits `x`'s own
dependency: it borrows what `x` borrows, and may outlive `x`). A result may
depend on several arguments ("conjoined" dependence: it lives no longer than
any of them), and `immortal` marks no dependence. The default rules are the
interesting part:

- a non-escapable result of the same type as a non-escapable parameter copies
  that parameter's dependency;
- an `inout` non-escapable parameter (a `mutating` method's `self`) gets
  `@lifetime(a: copy a)`: what is stored into it must already have its
  dependency;
- with exactly one parameter, an escapable one gives `borrow`, an `inout` one
  `&` (a mutable borrow); a non-escapable one has no default;
- several candidates need an explicit annotation.

Lessons: (1) the copy/borrow distinction is what makes iterators work: the
`next` of a non-escapable iterator copies the iterator's dependency, so items
borrow the collection. (2) Most signatures need nothing written. (3) Swift
needs annotations for multiple sources; obx can instead take the *join* of
all sources (section 3, rule R5), which is conservative and needs nothing.

### Hylo: mutable value semantics, projections

Hylo has no first-class references at all
([spec](https://github.com/hylo-lang/specification/blob/main/spec.md),
[subscripts](https://docs.hylo-lang.org/language-tour/subscripts),
[repository](https://github.com/hylo-lang/hylo)). Access to a part of a value
is a *projection*, made by a subscript that `yield`s it, and it lives only
until the end of the expression or binding that uses it. While an object is
projected immutably it is immutable; while projected mutably it is
inaccessible. An object may *capture* a projection when it is initialised,
and a product type's captured projections are those of the stored values that
initialised it: the same "a value holding borrows inherits their roots" rule
as here. Hylo's `for` desugars over `make_iterator()` and `next()` returning an
optional, also as here.

Lessons: second-class references scale further than one would expect when
values may hold projections and the holder inherits the root; freezing the
root (Hylo's "immutable during the projection") is the whole runtime model.

### Briefly

- **Mojo** names the origin of a reference in its type (`ref [origin] T`),
  with origins inferred inside functions and written in signatures
  ([lifetimes and origins](https://docs.modular.com/mojo/manual/values/lifetimes/)):
  Rust's model with friendlier syntax.
- **Vale** checks at run time: a generational reference compares a generation
  number on each dereference
  ([generational references](https://verdagon.dev/blog/generational-references)).
  No static rules, a cost per use; not obx's trade.
- **Austral** has regions as types and a lexical `borrow` statement
  ([borrowing](https://austral.github.io/tutorial/borrowing)): lexical freezing
  like obx's rule 4, but the region is named in every type.
- **C++** has the problem without the rules: `std::string_view` and `std::span`
  dangle whenever the owner dies first
  ([string_view](https://en.cppreference.com/w/cpp/string/basic_string_view)).
  `[[clang::lifetimebound]]` marks the parameter a result borrows, Swift's
  `borrow` by hand
  ([lifetimebound](https://clang.llvm.org/docs/AttributeReference.html#lifetimebound)),
  and the lifetime profile infers "points-to sets" per function
  ([Lifetime.pdf](https://github.com/isocpp/CppCoreGuidelines/blob/master/docs/Lifetime.pdf)),
  close to the per-function root sets here.

## 3. The proposal in brief

- **R1 Borrow-class types.** A type is borrow-class when it is a borrow or a
  slice, an array of a borrow-class element, a value record or value closed
  type with a borrow-class field, an instance with a borrow-class argument for
  a `borrow` type parameter, or a type parameter declared `T: borrow`. Only
  value types hold borrows: a boxed record or closed type cannot (it may
  outlive any root on the heap), nor a global or a constant.
- **R2 Second class.** A borrow-class value is like a borrow: it can be a
  parameter, a `let`, a `var` (unless it is a plain borrow), a field or element
  of a borrow-class value, a type argument of a `borrow` parameter, or a
  result; not a global, a field of a boxed type, a closure's result, or a type
  argument of an ordinary parameter (which keeps it out of tasks and of every
  existing generic container).
- **R3 One root.** Every borrow-class value has a root: what its borrows point
  into. A value made from parts has the join of their roots; the join of two
  variables freezes both. A borrow read out of a borrow-class value has that
  value's root, not the place it was read from.
- **R4 Frozen roots.** A `let`, `var`, parameter, `for` or `match` binding of a
  borrow-class value freezes its root while in scope (rule 4), mutably if the
  value holds a `mut &`.
- **R5 Results.** A function returning a borrow-class value has as sources its
  parameters that are or hold borrows. Each return must be rooted at a source:
  at a plain borrow parameter itself (Swift's `borrow`), or, for a parameter
  that is or borrows a borrow-class value, at what that value borrows (Swift's
  `copy`). At a call the result's root is the join of the sources' roots. A
  plain borrow result keeps "exactly one source".
- **R6 Vars.** A `var` of a borrow-class type has one root for its life: the
  first store binds it (the root must be declared before the var, and, inside
  a loop the var is outside of, must not be a `var`), every later store must
  have the same root.
- **R7 Views.** `&s[a..b]` of a `str` is a view: a `&str` rooted where `s` is,
  sharing the bytes uncounted; so is `s[a..b]` where a `&str` is expected.

## 4. Proposed spec text

### 4.5 Borrows (replaces the first paragraph)

> `&T` borrows a `T` for reading; `mut &T` borrows it for writing. A borrow is
> second class (section 10): it can be the type of a parameter, a receiver, a
> `let`, a function result, a field of a value record or value closed type, an
> array element, or a type argument of a parameter declared `borrow`; such a
> record, closed type or array holds borrows, and is second class too.
>
> A shared `&str` is a view: `&s[a..b]` borrows the bytes `a..b` of `s` without
> copying or counting them, and so does `s[a..b]` where a `&str` is expected.

and the last paragraph gains:

> Assigning to a field or element of borrow type stores a new borrow there; it
> does not write through the old one.

### 6.1 Parameters and constraints (adds)

> A constraint may be `borrow`: `type Option[T: borrow]`. Only a parameter
> declared `borrow` takes a type argument that holds borrows (section 10); in
> the body, a value of such a parameter is treated as holding borrows.

### 10. Borrows (rules 1, 4 and 5 replaced; rule 7 added)

> A type *holds borrows* when it is a borrow or a slice, an array whose element
> holds borrows, a value record or value closed type with a field that holds
> borrows, an instance whose argument for a `borrow` type parameter holds
> borrows, or a type parameter declared `borrow`.
>
> 1. **Second class.** A borrow, and any value of a type that holds borrows,
>    may be a parameter, receiver, `let`, function result, field of a value
>    record or value closed type, array element, or type argument of a
>    `borrow` type parameter; a value that holds borrows but is not itself a
>    borrow may also be a `var`. None may be a global, a constant, a field of a
>    boxed record or closed type, a closure's result, or a type argument of
>    another type parameter.
> 2. **Roots.** (unchanged for borrows.) A value that holds borrows has the
>    root of the borrows it holds: a value built from parts has the roots of
>    all of them; a borrow read from a field or element of such a value has
>    that value's root.
> 4. **Frozen roots.** While a `let` borrow, or a variable, parameter, `for` or
>    `match` binding of a type that holds borrows, is in scope, its roots are
>    frozen: they may not be assigned, moved, or mutably borrowed, and nothing
>    under them may be assigned. A value holding a `mut &` freezes its roots
>    against any other use. A `for x in xs` loop freezes `xs` the same way.
> 5. **Results.** A function whose result holds borrows has as its *sources*
>    the parameters (and receiver) that are or hold borrows; it must have one,
>    and a result that is a plain borrow exactly one. Every `return` must be
>    rooted at its sources: at a borrowed parameter itself, or, when that
>    parameter holds borrows or borrows a value that does, at what it borrows.
>    At the call site the result has the roots of the arguments passed for the
>    sources (for the second kind, the roots of what they hold), and rule 4
>    applies to them while the result is in scope.
> 7. **Variables.** A `var` of a type that holds borrows has one set of roots
>    for its whole scope, given by the first value stored in it that holds
>    borrows; every later store must have roots among them. The roots must be
>    declared before the variable, in its block or around it; the first store
>    inside a loop around which the variable is declared may not take a `var`
>    as a root.

### 16. Tasks (adds)

> A task's argument and result may not hold borrows: `start`'s type
> parameters are not `borrow`.

## 5. The iterator protocol

> `for x in e`, when `e` is not a range, an array or a slice, iterates: the
> value of `e` is moved (or copied) into a hidden variable `it`, and the loop
> runs as
>
> ```
> { var it = e; while true { match it.next() { Some(x) { body } None { break } } } }
> ```
>
> `next` must take `mut &Self` and return an `Option`. When the iterator holds
> borrows, `x` has the iterator's roots (rule 5: `next`'s receiver borrows a
> value holding borrows, so its result borrows what the iterator borrows), so
> the body may keep `x` after the next call to `next`.

Items never borrow the iterator: `fn (it: mut &It) next(): Option[&int] {
return Some(&it.count) }` is rejected (section 9, D5). That rules out lending
iterators and is what makes `collect`-style code (append every item to an outer
`var`) check.

## 6. Examples (all run on the prototype, both backends)

Split into views (lib/tests/run/borrow_types_split.obx):

```
fn splitRef(s: &str, sep: byte): []&str {
    var out: []&str = []
    var start = 0
    for i in 0..len(s) {
        if s[i] == sep {
            append(mut &out, &s[start..i])
            start = i + 1
        }
    }
    append(mut &out, &s[start..len(s)])
    return out
}

let parts = splitRef(&line, ',')   // parts freezes line
for p in parts { println(p) }      // p: &str, rooted at line
```

A token stream:

```
type Token = value record { text: &str; kind: int }
type Lexer = value record { src: &str; pos: int }

fn (lx: mut &Lexer) next(): Option[Token] {
    ...
    return Some(Token{text: lx.src[start..lx.pos], kind: kind})   // rooted at what lx borrows
}

var toks: []Token = []
let lx = Lexer{src: &src, pos: 0}
for t in lx { append(mut &toks, t) }     // the tokens borrow src, not lx
```

Map iteration (experimental std API):

```
for e in m.iter() {                 // MapIter[K, V] borrows m; m is frozen in the loop
    println(e.key + "=" + intToStr(e.value))
}
```

Records of views returned through a borrow (lib/tests/run/borrow_types_values.obx):

```
type Span = value record { text: &str; at: int }
fn textOf(sp: &Span): &str { return sp.text }    // rooted at what sp borrows
fn id[T: borrow](x: T): T { return x }
let o: Option[&str] = Some(&line[0..1])
```

An `enumerate` adapter is expressible with an interface constraint on a
borrow type parameter (lib/tests/run/borrow_types_enumerate.obx):

```
type Iter[X: borrow] = interface { fn (mut &Self) next(): Option[X] }
type Indexed[X: borrow] = value record { index: int; item: X }
type Enumerate[I: borrow + Iter[X], X: borrow] = value record { inner: I; i: int }
fn (e: mut &Enumerate[I, X]) next(): Option[Indexed[X]] { ... }
fn enumerate[I: borrow + Iter[X], X: borrow](it: I): Enumerate[I, X] { ... }

for e in enumerate[Words, &str](Words{src: &s, pos: 0}) { ... }
```

`X` is named only in `I`'s constraint, so it is not inferred: the call writes
both type arguments. Inferring it from the constraint (the argument's `next`)
would make `enumerate(it)` work; not done. It is not in std. `filter` and
`map` adapters would store a closure, and closures stay second class: not
possible.

## 7. What stays impossible

- Storing borrows in anything that may outlive its root: boxed records, maps
  and sets of views (`Map[&str, int]`: its type parameters are not `borrow`),
  globals, task arguments, closure results.
- Lending iterators (items that borrow the iterator).
- A function that mixes sources: `fn into(s: &str, out: mut &[]&str)` cannot
  append views of `s` to `out`, which must keep the root it has. Swift writes
  `@lifetime(out: copy out, copy s)`; obx would need a similar annotation.
- Choosing per call between results: `fn pick(a: &str, b: &str): &str` still
  needs exactly one borrowed parameter (rule 5 for plain borrows).
- Rebinding a `var` to a different root; ending a freeze before the end of its
  block (rule 4 is lexical).
- Self-reference: a value cannot hold a view of its own field.

## 8. Alternatives

- **Rust-style lifetimes** (`Token['a]`). Fully general, including lending
  iterators and mixed sources, at the cost of a parameter in every type and
  signature and an inference that users must understand. Against obx's "no
  lifetime annotations" line; not recommended.
- **Swift-style explicit dependencies** (`@lifetime(copy it)` on signatures).
  The same model as here with annotations where defaults run out. Recommended
  as the *escape hatch* only (D6): the defaults here (copy for borrow-class
  sources, borrow otherwise, join for several) cover every example above.
- **Status quo plus counting inference.** Keep borrows second class and make
  a split into `[]str` cheaper: the compiler could prove a `[]str` of substrings never
  outlives the line and drop the counting. Fragile (any store defeats it),
  invisible to users, and does nothing for tokenizers or iterators. Measured
  gain is the same ceiling as section 10's (only the counting goes).

## 9. Decisions (taken 2026-11-05: each as recommended)

- **D1 Multiple roots.** A value built from several roots gets their join, and
  freezes all of them. Recommended: keep (Swift's conjoined dependence;
  no annotation). Alternative: reject and require one root.
- **D2 `mut &` fields.** Allowed in the prototype: a value holding a `mut &`
  freezes its root against all use and its borrow is reached only through the
  holder. Recommended: allow, lightly used; reconsider if it complicates the
  checker further.
- **D3 Spelling `T: borrow`.** Alternatives: `T: ?borrow`, `T: second`,
  `T: view`. Recommended: `borrow` (reads as "may be a borrow").
- **D4 Which std types are `borrow`.** The prototype marks only `Option`.
  Recommended: also `Result`; not `Map`, `Set`, `Heap` (heap containers).
- **D5 Copy vs borrow dependency.** The prototype chooses by type: a source
  that is or borrows a borrow-class value gives `copy` (the result borrows what
  it borrows), any other gives `borrow`. Recommended: keep as the default; it
  is Swift's default and is what makes iterators work. A method of a
  borrow-class receiver therefore cannot return a borrow of one of its own int
  fields (`&it.count`); see D6.
- **D6 An override annotation.** For mixed sources, `pick`, and borrowing a
  borrow-class receiver itself, something like Swift's
  `@lifetime(borrow it)` / `@lifetime(out: copy s)`. Recommended: defer until
  real code needs it; the defaults covered every example here.
- **D7 `var` of plain borrows.** Still rejected (only aggregates may be `var`).
  Recommended: keep; a `var` borrow is a reborrow and adds little.
- **D8 Drop methods on borrow-class types.** Not rejected by the prototype; a
  drop method sees borrows that are still valid (the root is frozen past the
  drop). Recommended: forbid until there is a use.
- **D9 Closures.** Closures may capture borrow-class variables (they are second
  class already) but not return them. Recommended: keep.
- **D10 Views of `str` by expected type.** `s[a..b]` is a view where `&str` is
  expected and an owned `str` otherwise. Recommended: keep; `&s[a..b]` is the
  explicit form.

## 10. The prototype

Branch `borrow-types`, self-hosted compiler, C and native backends.

- **Representation.** A shared `&str` is the string's header `{block, off,
  len}` by value, uncounted (commit "check, emit: a shared &str is the
  string's header"); `mut &str` stays a pointer. Generic code reaches it
  through holes (H_ADDR, H_DEREF, H_SHARE) filled per instance. A view is
  `rt_str_view` (rt.h, native/rt.c): the same block, no reference taken.
  Everything else (records with `&T` fields, `[]&str`, `Option[&T]`) needed no
  backend change: borrows are pods.
- **sema.obx.** `T: borrow` (`TParam.borrow`); `isBorrowClass` (by declaration,
  terminating on types that name larger instances of themselves);
  `holdsMut`; borrow types allowed as fields, elements and type arguments;
  boxed types, globals and constants rejected; `checkBorrowTarg` for every
  type argument; `FnDecl.bsrc`, a result's sources.
- **check.obx.** Roots of values that hold borrows (`Val.cr`, `Var.croot`),
  pseudo roots for what parameters borrow, join roots, rebasing a borrow read
  from a field or element onto its holder's root, `storeRoot` (R6),
  `bindBC` (R4), `checkResult` (R5), call results from sources, views, the
  iterator protocol (`forIter`).
- **std.obx.** `Option[T: borrow]`; `MapIter`, `MapEntry`, `Map.iter`.
- **Tests.** lib/tests/run/borrow_types_{split,values}.obx (expect lines; both
  backends through run_native --lib) and 15 error tests
  lib/tests/error/borrow_types_*.obx: a borrow in a boxed record, a global,
  assigning and moving a frozen root, a var outliving its root (block and
  loop), a temporary root, returning borrows of a local, a result without a
  source, two roots in one var, a lending method, a type argument without
  `borrow`, mutating a map while iterating, a task argument, a closure result.
  Five existing tests were updated: what they rejected is now allowed, so they
  test what still is (`Option[&int]` became a `Box[&int]` without `borrow`,
  `var xs: []&int` a global, redteam round 2's `es_arrlit_of_slice_typed`
  now assigns the frozen root, and two messages).

Measured (bench in the prototype session: a 20-field line split 200 000
times, every piece's length summed; `perf stat`, user instructions and best of
three for cycles):

| backend | split into `[]str` | splitRef (`[]&str`) | instructions | cycles |
|---|---|---|---|---|
| C | 1 808.6 M instr, 380.6 M cycles | 1 576.8 M instr, 338.0 M cycles | -12.8% | -11.2% |
| native | 1 784.2 M instr, 421.4 M cycles | 1 592.0 M instr, 322.9 M cycles | -10.8% | -23.4% |

Both use `indexOf` for the separator, which dominates; the saving is the
counting of 4 M pieces, about 58 instructions each.

Known gaps of the prototype (not of the design; all closed in section 11):

- A value holding borrows passed by value is not held while the call's later
  arguments run (rule 3): `f(splitRef(&a, ","), take(move(a)))` with an owned
  `a` is not caught.
- `if` and `match` expressions giving a value that holds borrows lose its root,
  and binding the result is rejected ("cannot tell what the borrows ... point
  into"); statement forms work.
- A `for` over a record literal (`for t in Lexer{...}`) does not parse: the
  literal's brace is taken for the loop body (an existing grammar limit).
- Drop methods on borrow-class types are not rejected (D8).
- The pseudo root of a parameter is named `*p` in messages.

## 11. borrow-types-v2

Branch `borrow-types-v2`: the prototype rebased on main (with perf15's str
hoisting and perf19), the gaps of section 10 closed, D4 and D8 done, error
messages reviewed, the experimental views API in `strings`, and two programs
converted.

**Rebase.** perf15 (loops.obx) hoists a str's header when its root is a
local; a `&str` parameter is now that local itself, so hoisting fires as
before or more (the compiler: 211 hoisted headers on both; the trap test
lib/tests/run/str_hoist_trap.obx 3 against 2). rt.obx and rtx.obx were
regenerated from main's with `rt_str_view` added.

**Gaps closed.**

- `if` and `match` expressions give a value holding borrows the join of
  their branches' roots (lib/tests/run/borrow_types_ifx.obx). A plain borrow
  as the result stays rejected (SPEC 7.8 already said so).
- Rule 3: a value holding borrows passed by value holds its roots while the
  later arguments run (lib/tests/error/borrow_types_call_hold.obx: "'words'
  cannot be moved here: it overlaps a borrow held by the call being
  evaluated").
- D8: "Cursor cannot have a drop method: it holds borrows (a type that holds
  borrows cannot have one)"; also for a type with a `borrow` type parameter
  ("its type parameter T is declared borrow, so it may hold borrows").
- D4: `Result[T: borrow, E: borrow]`. A `try` on a result holding borrows
  keeps its root, and the `Err` it returns must be rooted at the function's
  sources (lib/tests/run/borrow_types_result.obx,
  lib/tests/error/borrow_types_try_err.obx).
- Messages: a parameter's pseudo root reads "what 'sp' borrows", a join
  "'a' and 'b'", a loop's hidden iterator "the iterator of the for loop at
  line N"; temporaries say to bind the temporary first; a parameter store
  says the caller fixes what a parameter borrows; a lending method says a
  value holding borrows lends what it holds, not itself.
- `for t in Lexer{src: &s, pos: 0}` parses: in a `for` header a name followed
  by `{` and `field:` is a literal (a statement cannot start with `f:`).

**Compile cost** (tools/perfsuite.py, frozen inputs, best of 5, against main
4c796a4): compile suite +0.52% instructions (+1.0% cycles, best of 9); run
suite -0.09% (maps -1.35%,
the rest unchanged). The representation change alone (shared `&str` by
value) costs +0.05% (`--emit-c`) and +0.12% (`--native`) compiling the
compiler; the rest is the checker's root tracking, spread over the front end
(no single hot spot found; computing call sources only when the callee's
result may hold borrows and making holes only for bare type parameters took
it from about +0.9% to +0.5%).

**Views API** (lib/strings.obx): first added beside the counted functions,
then made the default (below): `split`, `splitN`, `fields` and `cut` give
views, `owned` keeps them, `lines` and `fieldsIter` iterate. These numbers
were taken with the first version, as `[]str` against `[]&str` pieces. A
word-statistics program over text100.txt (100 MB;
lines, words, the longest word, lines whose third word is "omega"), best of 7:

| version | C instr | C cycles | native instr | native cycles |
|---|---|---|---|---|
| counted split + counted fields | 7 313 M | 2 440 M | 8 055 M | 2 063 M |
| `lines` + counted fields | 4 481 M | 1 682 M | 6 503 M | 1 791 M |
| `lines` + `fields` (views) | 3 650 M | 1 393 M | 5 681 M | 1 562 M |
| change, first to last | -50.1% | -42.9% | -29.5% | -24.3% |

Part of the `lines` step is `memchr` (`strFindByte`) against the counted
split's byte loop; counted to view `fields` alone is -18.5% / -17.2% (C) and
-12.6% / -12.8% (native).

**zoxide import** (cmd/zoxide.obx, `strings.lines` instead of a counted split; the
pieces of a line are views until the path is kept): 300 000 z-format lines,
best of 5, same database: -10.8% instructions / -9.4% cycles (C), -6.5% /
-5.7% (native).

**The compiler's lexer: not converted.** A token's text is not always a
slice of the source: escaped string literals are built (`StrBuf`), integer
literals are normalised to decimal (`toDecimal`), and symbols use the `SYMS`
table, so `Tok.s: &str` would need a second root (a table of built strings)
and one root per value (rule 7) rules that out without an index-based
redesign. The `Parser` that holds the tokens is a boxed `record` that also
owns the `Ast`; it would have to become a value record. And the gain is
small: the parser copies every name it keeps into an owned `Node.s`, so only
the token's own count goes. The compiler and the std modules it reads are
about 300 000 tokens, about 120 000 of them names, keywords or strings
counted today; at the roughly 20-60 instructions a counted piece costs
(section 10's bench) that is 2-7 M instructions of the 2 350 M it takes to
compile itself, 0.1-0.3%, not worth the redesign.

**grep, sort and wc** were not converted: they already work on byte offsets
into one buffer (sort and wc on `[]byte`, grep on positions in one `str`),
which is what views would give them.

**Decisions taken beyond D1-D10** (each the conservative option):

- A plain borrow as the result of an `if` or `match` expression stays an
  error; only values holding borrows get the join. (Lifted in section 12.1.)
- D8 also forbids a drop method on a type with a `borrow` type parameter.
- (Superseded below: string literals are now accepted as `&str`, and the
  separators are `&str`.)
- `X` in `enumerate[I: borrow + Iter[X], X: borrow]` is not inferred from
  the constraint.

**Left.** D6 (an override annotation) when real code needs one; inferring
type arguments named only in constraints; library adapters (`enumerate`) if
wanted; a `lines` that also drops a `"\r"`; the remaining compile cost.

### Follow-up: literals as views, views by default (2026-11-05)

Two changes approved after the first round.

**A string literal is a `&str` where one is expected** (SPEC 4.1, 10). Its
bytes are static, so it has no root (`strLit` in check.obx: the literal's
header as a `&str` with root `R_FREE`); a `str` constant counts as a literal.
It may be passed, stored in a value holding borrows, joined with any root
(`if c { Some(&a) } else { Some("none") }`) and returned. A function with no
borrowed parameter may now return a borrow or a value holding borrows when
every one it returns is static (`fn kind(k: int): &str { return "word" }`);
otherwise "cannot return a borrow of 'local': kindName has no borrowed
parameter, so its result may only borrow static strings (literals and
constants)". A `mut &` result still needs exactly one `mut &` parameter.
Tests: lib/tests/run/borrow_types_literal.obx, error tests
borrow_types_static_result, borrow_types_static_mut and borrow_types_no_source
(rewritten: `[]&str` with only static pieces is now a valid sourceless
result).

**Views are strings' default.** `split(s: &str, sep: &str): []&str` replaces
the counted split and the experimental view split; `splitN` (n < 0: all) and
`fields` return views; `Cut`'s `before` and `after` are views; `owned(&pieces):
[]str` keeps an array past its text; `lines` and `fieldsIter` stay. std's
`split(s: str, sep: byte): []str` (the prelude's split at a byte) is a
different function and is unchanged. Callers converted: cmd/zoxide.obx,
cmd/httpload.obx, the lib tests and test support, redteam round 2.

What the rules made awkward:

- A text replaced by a piece of itself (`match strings.cut(&out, sep) {
  Some(x) { out = x.before + ... } }`) is rejected: `x` freezes `out` for the
  arm. Two places in the test support now slice by index instead.
- Pieces kept in a loop body freeze the text for the rest of the block, so a
  parser that splits a header and then advances its cursor (`s = s[i + 4..]`)
  needs the pieces in an inner block (lib/tests/support/srvtest.obx).
- Splitting a temporary (`strings.split(&io.read(p).unwrap(), "\n")` in a
  `for` header) needs the text bound to a `let` first. (Lifted in section 12.2.)
- `join` takes `&[str]`, so joining views goes through `owned` (or a loop).
  A `join` over `&[]&str` would need a second function or a generic one.
- A view does not get a count, but converting it to `str` (an argument of a
  `str` parameter, std's `indexOf`) takes one; strings' own functions use the
  `strFind` intrinsic, which reads the header without a count.

## 12. borrow-limits: lifting the remaining limits

Branch `borrow-limits` (2026-11-05) lifts the four limits listed at the end of
section 11, one commit each. Soundness first: each relaxation keeps rejecting
every program that could leave a borrow dangling (the error tests named below,
and an adversarial pass at the end).

### 12.1 Plain borrows from if and match expressions

`let v: &str = if b { "lit" } else { &s }` and `if b { &a } else { &c }` are
accepted (SPEC 7.8). The expression gives a borrow when the expected type is a
borrow or a slice, or, with no expected type, when a branch is written `&p` or
`mut &p` (or is itself such an expression); every branch must then be a
borrow, and a string literal branch is a view of static bytes. The result's
root is the join of the branches' roots (the same join values holding borrows
already had), a literal adds none, and a `mut &` result must borrow the same
variable in every branch. The result is rooted at the join with an empty path:
a call holds each member whole while its later arguments run (`hold` now
expands a join; `frozen` too), and `let` freezes all of them.

Slices come with it: a slice is a borrow with the same root and the same
second-class rules, so `total(if b { &xs[0..1] } else { &ys })` needed only the
same join. Closures stay rejected: a closure's environment is built in the
branch (a C block-local struct), and its captures are a list held per capture,
not a root, so a join would neither keep the environment alive nor merge the
capture lists.

Two holes found on the way, in the existing join for values holding borrows,
are closed by the same check (`branchRoot`):

- an arm giving a borrow of a variable it binds (`match f() { Some(x) {
  Some(&x) } ... }` as an `Option[&str]`: `x` is dropped when the arm ends;
  heap-use-after-free under ASan before);
- a branch giving a borrow of a temporary it made (`show(if b { Some(&mk()) }
  else { None })`: the branch drops its temporaries when it ends).

Also: a function without sources now gives its static borrow result no root
(SPEC 10 rule 5 said so), so `let k = kind(2)` binds it.

Tests: lib/tests/run/borrow_ifx_plain.obx; lib/tests/error/borrow_ifx_*
(return_root, mut_two_roots, assign_root, call_move, temp, value_temp, mixed),
borrow_matchx_* (mut_two_roots, arm_binding, arm_binding_plain). Two
conformance error tests (x1_ifx_borrow, x1_ifx_slice) and red-team round 4's
i20 rejected what is now allowed; they now test the mut-two-roots and
temporary cases.

### 12.2 Temporaries in a `for` header and a `match` scrutinee

A statement's temporaries live until the statement ends, and a `for` loop or
a `match` statement is one statement: `for p in strings.split(&mk(), ",")`
keeps `mk()`'s text alive through the whole loop. The checker already let the
loop variable hold views of it (loop and arm bindings may hold a temporary
root), but a `let` in the body copying them carried the bare temporary root,
so `let keep = tk` was rejected ("cannot hold borrows of a temporary").

Rule as built: where a `for` loop (over an array or an iterator) or a `match`
statement binds a value holding borrows of its own temporaries, the
temporary root is replaced by a hidden local made for that statement
(`stmtRoot`; messages call it "the temporary in the header of the for loop
at line N" or "the temporary matched at line N"). It is declared after every
variable around the statement and lives as long as the temporary does, so a
body `let` may hold its views, while anything declared before the statement
(an outer `var keep: []&str`) cannot: the usual "does not live as long as"
check rejects it, and returning one is rejected as before. A `match`
expression's arms are not covered: its value outlives the arms, so they still
cannot give views of the scrutinee.

A hole closed on the way: `joinRoots` let a temporary win a join, so a value
built from a variable and a temporary (`both(&a, &mk())`) lost the variable's
root. A loop over it could then assign `a` while an item still viewed it, and
a match arm likewise (heap-use-after-free under ASan); a call could move `a`
while an earlier argument held it. A temporary now joins through a stand-in
variable (`tmpVar`, flagged `tv`), so the join keeps every root and still
counts as a temporary (`hasTmp`) wherever a bare temporary root was refused.

Tests: lib/tests/run/borrow_for_temp.obx; lib/tests/error/borrow_for_temp_*
(escape, derived, return), borrow_match_temp_escape, borrow_matchx_temp,
borrow_for_join_temp, borrow_match_join_temp, borrow_call_join_temp. Callers:
http_server_log.obx and time_clock.obx now split their temporaries in the
header.
