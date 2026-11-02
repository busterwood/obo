Part of the obo language reference. Index and core syntax: [LANGUAGE.md](../LANGUAGE.md).

## 12. Known holes and planned changes

Decimal is the released minimum scalar tier at 0.12; record fields, `[]decimal`,
globals and constants followed in 0.16, and methods and interface members in 0.18:
decimal function values, lambda parameters/returns and captures followed in 0.19 (a
capture is a 24-byte value snapshot; named function values, stored closures,
`&decimal` and `mut`/`move` captures remain out of scope). No
`format`/`parse` stdlib, database adapter, `decimal(p,s)` syntax, float interop
or owned/mut decimal. Display scale is independent metadata, and a multiply
caps the propagated display scale at the 38-digit rule without changing the
value. The reviewed core keeps its strict quantize target limit of 32768. The
0.11 seed built the 0.12 image once; the promotion makes `seed/obo` the same
0.12 image. Underscore placement such as `1_.0` is not validated by the
shared lexer; that is a pre-existing lexer-wide gap, not decimal-specific.
`float` remains a separate pre-existing reserved-type-name gap; `decimal` is
now reserved.

Template literals are released in `0.15`. The v1 scope has no SQL parser or
identifier/`ORDER BY` allow-list API, no array expansion, no user-defined
template tags or public domain registry, no generic `escape`, and no
`string -> Sql`/`Html` conversion. HTML has no script/style/comment/unquoted or
dynamic name encoder and no sanitizer; `HtmlAttrs`/`HtmlAttr` exist as nominal
types but have no v1 hole. `Html` is a claimed-safe convention, not a
type-enforced provenance check; direct `new Html{...}` is private to module
`html`, while `Sql`/`SqlArg` fields are public. `obo/lib/sqldialect.obx`
renders `$n`/`?`/`@pN`/`:n`; the experimental PostgreSQL and SQLite packages
provide the binding bridge, while SQL Server and Oracle adapters and
`SqlArg.Float` binding (`SQL_ARG_FLOAT_UNSUPPORTED`, 1014) are later work.
`DecimalText` is the lossless decimal transport. `obo 0.19` is released as
format 12/ABI 1; the seed is promoted at `ba02031`, and `seed/obo` and the
compiler are both 757,761 bytes (sha256 `8327ca50…`) at the self-host fixed
point.

**Assignment targets across calls (closed).** The target of an assignment is located
before the right-hand side is evaluated, so a call on the right that frees the target
would write into freed memory. The compiler rejects a field or element assignment
whose right-hand side calls a function that might free something:

```obo
// example: error: cannot assign to a field or element across a call
type Node = ref record { v: int; kids: []Node }

fn replaceKids(mut n: &Node): Node {        // frees the array the target is in
    n.kids = new []Node(0)
    return new Node{v: 7, kids: new []Node(0)}
}

fn main() {
    var n = new Node{v: 1, kids: new []Node(0)}
    append(n.kids, new Node{v: 2, kids: new []Node(0)})
    n.kids[0] = replaceKids(n)
    println(n.kids[0].v)
}
```

Compute the value first, then assign it:

```obo
// example: runs
// prints 7
type Node = ref record { v: int; kids: []Node }

fn replaceKids(mut n: &Node): Node {
    return new Node{v: 7, kids: new []Node(0)}
}

fn main() {
    var n = new Node{v: 1, kids: new []Node(0)}
    append(n.kids, new Node{v: 2, kids: new []Node(0)})
    let fresh = replaceKids(n)
    n.kids[0] = fresh
    println(n.kids[0].v)
}
```

The same rule covers every form that locates a target, or an array, before a call later in its
own expression:

- a compound assignment (`+=`, `-=`, `*=`) to a field, element, handle target or global field,
  whose value calls a function that might free it;
- `append`, `truncate` and `replace` whose array (or target) is a field, element, handle, global, or
  reached through a reference parameter, when the value or count calls such a function;
- an index whose index expression calls such a function, when the array is a field or element, is
  reached through a handle or a global, or is a reference parameter. This covers reads, assignment
  targets, `take`, and `&a[f()]`.

A call that takes a value by value moves it, and the callee frees it when the callee returns. So an
index, `append`, `truncate`, `replace` or assignment that holds an array while such a call moves the
local the array lives in is rejected, whatever the array's place: a whole local (`xs[f(xs)]`,
`append(xs, f(xs))`, `truncate(xs, f(xs))`, `replace(xs, f(xs))`), a field (`b.items[f(b)]`), an
element (`xs[0].kids[f(xs)]`), a row of a grid (`g[0][f(g)]`), or a reference to any of these
(`let r = &xs; r[f(xs)]`), when `f` takes that local by value. A record literal that moves the record
in an index (`b.items[g(new Holder{bg: b})]`) is refused too. A call that only borrows (`&`) or takes
a `mut` argument is accepted: a `mut` array parameter cannot be reassigned, so no such call can free
the array's header. The fix is the same: compute the value, or the index, first.

A call is only a problem if it might free. A callee that writes a scalar through a `mut` parameter, or
appends to a `mut` array, does not make these checks refuse: an array is a header of its own that `append`
updates in place when it grows, so `xs.items[grow(xs.items)]`, `h.items[bump(b)] = v` and
`append(b.items, grow(b.items))` are accepted when `grow` and `bump` only append or write scalars. A callee
that assigns over, `replace`s or `take`s something below a `mut` argument, truncates an array through it (a
truncate counts as a free even for scalar elements), is not compiled yet, or calls itself, is refused as before,
whichever of its arguments it frees below.

A string is different: it is not an owned value, so assigning a new value over a string field or element frees
nothing the checks can see, yet the old string is freed if that was its last reference. A string indexed by an
expression that contains a call (`b.s[f(b)]`, `h.s[g(h)]`, `names[0][clear(names)]`) therefore holds a reference of
its own while the index runs and gives it back after the byte is read, so the byte is that of the string as it was
when the index began, and nothing is refused.

```obo
// example: error: cannot index 'xs' with an index that moves it
type Node = ref record { v: int; kids: []Node }
fn eat(a: []Node): int { return 0 }     // takes the array by value: it is moved and freed
fn main() {
    var xs = new []Node(0)
    append(xs, new Node{v: 1, kids: new []Node(0)})
    println(xs[eat(xs)].v)
}
```

A free counts as a move. `replace` and `take` free the old value of the place they empty, so a value
that a pure call consumes can free what the statement holds: `b.items[sinkArr(replace(b.items, new []Node(0)))]`,
`xs[0].v += sinkI(replace(xs, new []Node(0)))` and `append(xs, sinkNode(replace(xs, new []Node(0))))` are refused.
A handle's array indexed by a move of the object it names (`let h = handle(b); h.items[eat(b)]`) is refused too.
A `replace` or `take` of an unrelated place, or of an element beside the array that is indexed, is accepted, and
so is an assignment whose value takes its own target (`t.opt = pass(take(t.opt))`), since `take` leaves that
place empty. A free of an ancestor of the target is still refused. A handle made by `let h = handle(b)` from a
whole owned local names that local. A whole move of `b` (`var c = b`, through a call, into a field, `replace(b, ...)`, `take`) makes that handle name no local (an assignment over `b` does not: the handle keeps naming `b`, and is refused at its next use), so it is checked like a handle without an origin: an index through it that moves or frees something is refused. A move of `b` inside a loop that a live `let` handle from `b` was declared outside of is refused (`cannot move 'b' inside a loop while handle 'h' made from it is in scope`) if the loop names the handle anywhere; if it does not, the move is accepted and the handle may not be named again, even after the loop (`handle 'h' was made from an owner that a loop moved: make the handle again`), except in `alive(h)`. An assignment over `b`, or `drop(b)`, frees the object the handle names, so a use through the handle after it is refused (section 5). A temporary `handle(b)` names nothing afterwards. A handle without that
origin (a handle parameter, or one made from a field or an element) may name any object: a move or a free through
it is refused against anything it might name, and a free through it of another handle's place is compared by path
only when it is the same handle.

```obo
// example: error: cannot index a field, element or reference with an index that calls a function
type Item = ref record { v: int; kids: []Item }
type Bag = ref record { items: []Item }
fn cut(mut b: &Bag): int {
    b.items = new []Item(0)     // frees the array the index is taken from
    return 0
}
fn main() {
    var b = new Bag{items: new []Item(0)}
    append(b.items, new Item{v: 1, kids: new []Item(0)})
    println(b.items[cut(b)].v)  // error: the index is computed after the array is loaded
}
```

**Through references (closed, with limits).** A change made through a reference, such as `p.next = x`
where `p` is a reference, is at the place the reference points at plus the path written (section 4), not
anywhere in the root. What stays at the whole root: a change through a handle or through a reference
borrowed through one, a reference local not pointed afresh in a loop, a join of two different places
(including a whole object and a place inside it), and a `for` walk's variable.

**Changes through a handle, and references inside something (closed).** A change through a
handle, such as `h.items = ...`, `handle(p).items = ...` (a handle temporary, which has no name),
or a call that makes one, frees the old owned children of the handle's target. So it may free what
any reference points into, whatever that reference borrowed from. The rules:

- A reference *inside something* points into a field or element, such as `&bag.items[0]`, or
  `&h.items` through a handle. It is invalidated by a change through a handle, by a change through
  a reference borrowed through a handle, and by a call that writes through a handle.
- A reference to a whole object, such as `&bag` or a parameter, stays valid. Only owners free whole
  objects, and a change through a handle frees only what is below its target.
- A reference inside something may not be passed to a function that writes through a handle, since
  the handle may name the object it is inside. A function that calls such a function writes through a
  handle too, and a function with only a forward heading is assumed to.
- A recursive call is judged when the function's body is complete, so that a recursive tree walk
  stays accepted. If the function writes through a handle, passing a reference inside something to
  the recursive call is an error, and so is using such a reference after the call. In a loop that
  makes the call, a reference that is still suspect where the next iteration starts is an error if
  the loop's condition or body reads it.
- Suspicion is per path. A reference that a recursive call may have freed stays suspect on every path
  where that is so: a join of an `if`, a `match` or a loop keeps the suspicion of each path, and a
  reference re-pointed on one path is still suspect on the others. A loop's next iteration starts
  where its body ends, and at each `continue`; a body that always leaves the loop carries nothing.
- A deferred recursive call runs where its block ends, so the references that are live there become
  suspect at that point. Reads in the block before its end are not affected.

The change that used to compile and read freed memory is now an error:

```obo
// example: error: 'first' may point to freed memory
type Item = ref record { v: int }
type Bag = ref record { items: []Item }
fn cut(h: ^Bag) {
    h.items = new []Item(0)      // frees the items, which the reference below points into
}
fn main() {
    var bag = new Bag{items: new []Item(0)}
    append(bag.items, new Item{v: 1})
    let h = handle(bag)
    let first = &bag.items[0]
    cut(h)
    println(first.v)            // error: 'first' may point to freed memory
}
```

A reference re-pointed on one branch is still suspect on the other, so this is an error:

```obo
// example: error: 'r' may point to freed memory
type Item = ref record { v: int; kids: []Item }
type Bag = ref record { items: []Item }
fn rec(b: &Bag, h: ^Bag, d: int) {
    var r = &b.items[0]
    if d > 0 { rec(b, h, d - 1) }
    if d == 5 { r = &b.items[0] }   // only one path points it afresh
    println(r.v)                   // error: 'r' may point to freed memory
    h.items = new []Item(0)
}
fn main() {}
```

A reference to the whole bag is still valid after the same change:

```obo
// example: runs
// prints 0
type Item = ref record { v: int }
type Bag = ref record { items: []Item }
fn cut(h: ^Bag) {
    h.items = new []Item(0)      // frees the items, which are below the bag
}
fn main() {
    var bag = new Bag{items: new []Item(0)}
    append(bag.items, new Item{v: 1})
    let whole = &bag
    cut(handle(bag))
    println(len(whole.items))
}
```

**References re-pointed on one path (closed).** A reference's binding used to be one for the whole
function, so a re-point on one branch hid the place it had before from the paths that did not take
the branch: the change that freed that place compiled and read freed memory. Each path now keeps its
own binding (section 4), and where paths join, a reference may point into the union of their places.
Two places join as follows: the same place stays the same; any other two places join to the whole
root, which is the most conservative. A rule that kept only the common prefix of the two places
would miss a change at either one, and the lower of two nested places (an earlier version of this rule)
is wrong for a change made through the reference, which is at the place plus the path written. A whole
object and a place inside it stay two entries of the binding, so that a change through the reference is
at each of them.

A defer body may not re-point a reference declared before it (section 4). A loop is checked as well:
where a change in the loop may have freed what a reference points into, while the reference was not
pointed afresh on every path, a read of it on the next iteration is an error, unless the reference is
pointed afresh after the change and before it is read again. A reference whose binding at the end of
the body is not covered by the binding the body was checked with, or which is writable where the loop
starts and read-only where it starts again, is also an error. The rule is per path, as the bindings
are. A change made through a reference is at the place it names, not the whole root (section 4,
"Through references"); a loop's reference that is not pointed afresh is the exception, and keeps the
whole root.

**Loop and defer frees (closed).** A free in a `while` condition, or on a `break` or `continue` path,
was lost at the loop's exit, and a defer body's frees were applied where the body is declared. Both
are applied where they happen now (section 4). Loop marks are kept to their loop, so a free before a
loop does not reject a read inside it that the loop does not change, and a reference that is writable
where a loop starts and read-only where it starts again is an error only when it is written before it
is pointed again.

**Defer uses (closed).** A defer body's reads and writes were checked where the body is declared, not
where its block is left. `{ defer println(first.v); bag.items = new []Item(0) }` read a freed element
at the block's end, and `var k = &a; { defer println(k.v); k = &b; b = new Item{...} }` printed a freed
value. A body that wrote through a reference pointed elsewhere after the declaration freed what the
reference named at the declaration, not what it named at the exit. The rule in section 4 closes these:
the references a defer uses must have the same binding and be valid at each exit. It costs some
strictness: a block that points a reference elsewhere after a defer that uses it is refused, even where
the defer would be harmless.

**Argument order (closed).** A call's argument was checked as if no later argument could change
what it points into, so `use2(first, mk(h))`, where `mk` frees what `first` points into, and
`two(&xs[0], eat(xs))` passed the callee freed memory. A reference argument is now held while the
later arguments run (section 4).

**A read after a re-point (closed).** A loop whose recursive call leaves a reference suspect where
the next iteration starts is an error only if the loop reads that reference *before* pointing it
afresh: a read that follows a re-point on every path to it sees the new binding. This program is valid
and is accepted, since `k` is pointed afresh before each read; a read of `k` above the re-point, on
one path only, in the condition, in an inner loop, or a walk of it, is still an error:

```obo
// example: compiles
type Item = ref record { v: int; kids: []Item }
type Bag = ref record { items: []Item }
fn rec(n: &Item, h: ^Bag, d: int): int {
    if d == 0 {
        h.items = new []Item(0)
        return 0
    }
    var k = &n.kids[0]
    var i = 0
    var s = 0
    while i < 2 {
        k = &n.kids[0]
        s += k.v
        s += rec(n, h, d - 1)
        i += 1
    }
    return s
}
fn main() {}
```

A loop that reads nothing it left suspect before pointing it afresh is accepted, and so is one that
points a reference afresh after each call before reading it on the next iteration. A loop that frees what a reference points
into and points the reference afresh before it reads it again is accepted too (`run/ref_bindings.obx`,
`loopCache`).

**Planned.** The language features listed as not yet done in the root
`README.md` also apply: generic modules, value records, `inout` parameters, and
debug information. Lambdas Stage A and Stage B (sections 5c and 5d), methods
(section 5a) and interfaces (section 5b) are implemented; `^I` remains later.
