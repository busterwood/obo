# The obo language

obo is a small language in the Wirth tradition, with C-style braces. It has no
garbage collector. Every owned value has exactly one owner, which frees it at a
known point, and the compiler rejects any program that could read freed memory.

This describes the language as the compiler in `obo/src/obo.obo`
implements it. Syntax not covered here (declarations, `if`, `while`, `for`,
`match`, `defer`, closed types, constants, literals, operators) is the same as
obx; see the language section of the root `README.md`. Rules that are planned
rather than implemented are marked as such, and section 12 lists known holes.

The examples are checked against the compiler. A block marked
`// example: runs` is a complete program that must compile and exit 0, and each
`// prints` line gives one line of expected output. `// exit: N` gives an
expected exit code. A block marked `// example: error: TEXT` must be rejected
with a message containing TEXT. A block marked `// example: compiles` must
compile, but is a known problem rather than a model of good code.

## 1. Types

| Type | Meaning | Owned |
|---|---|---|
| `int`, `byte`, `bool` | numbers and booleans, copied by value | no |
| `string` | immutable bytes | yes (reference counted) |
| `[]T` | growable array of `T` | yes |
| `ref record { ... }` | a record, declared with `type` | yes |
| `closed { ... }` | a variant type, declared with `type` | yes |
| `?T` | a value that may be `nil`, for example `?Node` | as `T` |
| `&T` | a borrowed reference (section 4) | no |
| `^T` | a handle: a checked, non-owning link (section 5) | no |
| `Result[T]` | success (`ok`) or failure with a message and a code (`err`) | yes (one cell; the payload follows `T`) |

Records and closed types are declared with `type`, and may refer to each other.
A forward declaration `type Sym` lets them do so; the fields are only usable once
the type is defined.

## 2. Ownership

Every owned value has one owner: a local variable, a parameter, a global, a field
or an element. When the owner goes out of scope, the value is freed together with
everything it owns. Assigning a new value to an owner frees the old one first.
Scope exit runs after the scope's `defer` statements.

```obo
// example: runs
// prints 2
type Box = ref record { v: int }

fn main() {
    var b = new Box{v: 1}
    b = new Box{v: 2}      // the first box is freed here
    println(b.v)
}                          // the second box is freed here
```

## 3. Moves

Assigning an owned value to another variable, or passing it by value, moves it.
The source then has no value until it is assigned again.

```obo
// example: error: 'a' was moved and has no value here
type Box = ref record { v: int }

fn eat(b: Box) {}

fn main() {
    let a = new Box{v: 1}
    eat(a)
    println(a.v)
}
```

The rules for moves:

- `take(x)` moves out of an optional variable, field or element and leaves `nil`
  behind. Its result is still optional, so it must be checked before use.

  ```obo
  // example: runs
  // prints empty
  // prints 3
  type Box = ref record { v: int }

  fn main() {
      var o: ?Box = new Box{v: 3}
      let p = take(o)              // o is nil from here on
      if o == nil { println("empty") }
      if p != nil { println(p.v) }
  }
  ```

- A plain assignment cannot move out of a field or element, or out of a global.
  Use `take` on an optional one instead.
- Both branches of an `if`, and every arm of a `match`, must leave each variable
  in the same state. A variable moved on only one path is an error at the merge.

  ```obo
  // example: error: moved on one path but not another
  type Box = ref record { v: int }

  fn eat(b: Box) {}

  fn main() {
      let a = new Box{v: 1}
      if a.v > 0 { eat(a) }
      println(a.v)
  }
  ```

- A loop must leave each variable as it found it, so a variable moved inside a
  loop must get a value again before the loop repeats or exits.
- A move is not allowed inside `&&` or `||`, or inside `defer`.
- A temporary value must be stored in a variable before it is used, so that it
  can be freed. `println(make().v)` is an error; write
  `let m = make()` and then `println(m.v)`.

## 4. References

`&T` is the one reference type. A parameter declared `mut p: &T` may change the object
it points to; one without `mut` may not.
A reference borrows an owned value; it does not own it. A reference stays valid
while nothing it may point into has been changed.

- References are for parameters and local variables only. They cannot be stored
  in fields, elements or globals, and cannot be returned.
- Passing an owned value to a reference parameter borrows it. The caller keeps
  ownership:

  ```obo
  // example: runs
  // prints 6
  type Node = ref record { value: int; next: ?Node }

  fn sum(list: &?Node): int {       // borrows the list
      var t = 0
      var p = list
      while p != nil {
          t += p.value
          p = p.next
      }
      return t
  }

  fn main() {
      let list = new Node{value: 1, next: new Node{value: 2, next: new Node{value: 3, next: nil}}}
      println(sum(list))
  }
  ```

- `&place` borrows explicitly: `let r = &items[0]`.
- `&place` may borrow through a handle: `let items = &h.items` borrows what `h` names. Such
  a reference may point into any object, so it is invalidated by any change to an owned
  value or through a handle, and by any call that might free. A call might free unless it
  frees no global, frees nothing below any of its `mut` arguments (writing a scalar or
  appending is fine), writes no owned value through a handle, is compiled before the call, and is not recursive. The reference cannot be passed to
  a call that might free. A `for` loop that walks it cannot change what it points into, but
  `append` to the array it walks is accepted: an append is not a change, and a walk re-reads
  the array's header each time.

  ```obo
  // example: runs
  // prints 6
  type Node = ref record { value: int }
  type Bag = ref record { items: []Node }

  fn total(xs: &[]Node): int {         // pure: changes nothing and calls nothing that frees
      var t = 0
      for x in xs { t += x.value }
      return t
  }

  fn main() {
      let bag = new Bag{items: new []Node(0)}
      append(bag.items, new Node{value: 1})
      append(bag.items, new Node{value: 2})
      append(bag.items, new Node{value: 3})
      let h = handle(bag)
      let items = &h.items
      println(total(items))
  }
  ```

  ```obo
  // example: error: cannot pass a reference that borrows through a handle to shrink
  type Node = ref record { value: int }
  type Bag = ref record { items: []Node }
  var spare = new []Node(0)
  fn shrink(xs: &[]Node): int {
      spare = new []Node(0)            // frees what spare held, so a call to shrink might free
      return len(xs)
  }
  fn main() {
      let bag = new Bag{items: new []Node(0)}
      let h = handle(bag)
      let items = &h.items
      println(shrink(items))
  }
  ```

  ```obo
  // example: error: 'items' may point to freed memory
  type Node = ref record { value: int }
  type Bag = ref record { items: []Node }
  fn main() {
      var bag = new Bag{items: new []Node(0)}
      append(bag.items, new Node{value: 1})
      let h = handle(bag)
      let items = &h.items
      bag.items = new []Node(0)        // frees the nodes items points into
      println(len(items))
  }
  ```
- A reference's binding is kept for each path. Where a reference is pointed at one place on one
  path and at another on the other, it may point into either, so a change to either place
  invalidates it on the paths where that is possible. A reference pointed at the same place on
  every path is checked against that place alone. Where paths join, the binding is the union of
  their places, so a change under any of them is seen, and a reference that may be writable on
  one path only is read-only after the join. A defer body may not point a reference declared
  before it at something else: the body runs where its block is left, which is not one of the
  paths the binding is kept on, so the change would not be seen there.
- A reference invalidated on one path only is invalid where the paths join, and a use of it is
  refused: a read, a write through it, a pass to a call, a re-borrow, or a use by a defer. A
  reference that is not used after the join is not an error, so an `if`, a `match` or a loop that
  changes what a reference points into on some path is accepted when nothing reads the reference
  afterwards. A re-point after the join makes it valid again. A loop's next pass starts where its
  body ends, so a reference invalidated on the way round is refused where the loop reads it before
  pointing it again, and after the loop if it is read there.

  ```obo
  // example: runs
  // prints 1
  type Item = ref record { v: int }
  type Bag = ref record { items: []Item }
  fn main() {
      var bag = new Bag{items: new []Item(0)}
      append(bag.items, new Item{v: 0})
      let p = &bag.items[0]
      if bag.items[0].v == 0 { bag.items = new []Item(0) }   // p is invalid on this path, and never read
      println(1)
  }
  ```

  ```obo
  // example: error: 'p' may point to freed memory
  type Item = ref record { v: int }
  type Bag = ref record { items: []Item }
  fn main() {
      var bag = new Bag{items: new []Item(0)}
      append(bag.items, new Item{v: 0})
      let p = &bag.items[0]
      if bag.items[0].v == 0 { bag.items = new []Item(0) }
      println(p.v)                   // invalid on one path: refused here, at the read
  }
  ```
- A defer body is checked where it is declared, but runs where its block is left. The references
  declared outside the body that it uses (reads, writes through, passes to a call) must be as they
  were at the declaration at each exit from the block: not pointed at another place since, and not
  invalidated by a change since. The check is made at the normal end, at `break`, `continue` and
  `return`, and a newer defer's frees count for an older one. A reference pointed at another place
  is an error even if nothing is freed, since the defer would use the new target. Owned locals a
  defer reads are covered by the rule that they may not be moved after the defer.

  ```obo
  // example: error: 'first' is used by a defer but may point to freed memory
  type Item = ref record { v: int }
  type Bag = ref record { items: []Item }
  fn main() {
      var bag = new Bag{items: new []Item(0)}
      append(bag.items, new Item{v: 5})
      let first = &bag.items[0]
      {
          defer println(first.v)
          bag.items = new []Item(0)      // frees what first points to, before the defer runs
      }
  }
  ```
- A loop follows the frees on each way out of it (`break`, `continue`, a `while` condition) and on the
  way back to its start, so a free made in a condition or on a `break` path is seen after the loop. A
  free before the loop does not mark a reference inside it unless the loop frees what it points into.
  A reference writable where a loop starts and read-only where it starts again is an error only when
  it is written through before it is pointed again.
- A defer body's frees happen where its block is left, at each exit from the block (normal end,
  `break`, `continue`, `return`); its moves are not taken where it is declared. The frees are
  applied as the body made them: through the same reference, so a reference the change was made
  through, or derived from, stays valid (a deferred write to a child of `p` leaves `p` valid). A
  defer body's re-point of a reference declared before it is still an error, as above.
- A `mut` argument invalidates the references into the place it names only if its callee may free something
  below it: the compiler keeps, for each function, which `mut` parameters it frees through (an assignment
  of an owned value, `replace`, `take`, `truncate` of owned elements, a `mut` call that does, or a write
  through a handle, a recursive call, or a heading not yet defined, which count as freeing anything). A
  callee that only writes scalars, appends, or assigns strings leaves them valid.
- A field of a reference parameter (`&p.ys`) is a place of its own: a change to `p.xs` does not reach it.
  So is a place a reference local points at. With `var r = &a.ys`, a change through `r` (`r[0] = ...`,
  `r = ...` is a re-point and changes nothing), a `mut` call given `r`, and a change to `a.ys` itself
  reach a reference into `a.ys` and leave `&a.xs[0]` and `&a.zs` valid. A change through `r` is at the
  place `r` points at plus the path written, so `s = &r.zs` is the place `a.ys.zs` and a change
  through `s` leaves `&r.xs` valid. The rules for the place:
  - Elements with different constant indexes are different places. An element with an index that is
    not a constant is every element of the array (`&a.ys[i]` may be `&a.ys[0]`), and a constant
    element is one of them: they overlap, and nothing beside the array does.
  - A reference re-pointed has the place of the new target on that path. Where paths join (an `if`, a
    `match`, the end of a loop), a reference may point into the places of both. The same place stays
    one place; any two different places join to the whole root, and so does a reference to a whole
    object (`&n`) joined with one inside it (`&n.kids[0]`): a change through it is then taken to
    reach everything in the root. The places are not narrowed to a common prefix: that would miss a
    change at one of them.
  - A change that reaches the place by another route still reaches it: a change through a handle, a
    `mut` call given the whole root, a function that frees a global the reference points into, and a
    change through another reference parameter or a global, which may name the same object.
  - A reference local that a loop has not pointed afresh since the loop began (it was declared
    before the loop, and is not re-pointed on every path before the use) may be elsewhere on the next
    iteration, and the body is checked once. A change through it, a reference made from it, or a
    `mut` argument it is, inside that loop, is taken to reach the whole root. A reference declared
    in the loop, or pointed afresh before the use, has its place. A copy of such a reference (`let s = r`)
    is anywhere in the same roots. A known false rejection: `r[0] = replace(r[0], x)` through a reference
    declared before the loop is refused inside it; point `r` in the loop, or name the element.
- `replace` and `take` cannot name a reference local (`var r = &p.ys; replace(r, ...)`): a reference only
  borrows, so the old value is not its to give back; name the field or element.
- A mut argument, `append` and `truncate` write through the reference they are given, so a loop that
  passes a reference to a mut parameter, or appends through it, is checked like an assignment: the
  reference must be writable on every pass where it is written before it is pointed again.

  ```obo
  // example: error: 'k' may point to freed memory
  type Item = ref record { v: int }
  fn main() {
      var a = new Item{v: 1}
      var b = new Item{v: 2}
      var k = &a
      if a.v == 2 { k = &b }        // k points at b on one path only
      a = new Item{v: 3}            // frees what k may still point to
      println(k.v)
  }
  ```

  ```obo
  // example: runs
  // prints 2
  type Item = ref record { v: int }
  fn main() {
      var a = new Item{v: 1}
      var b = new Item{v: 2}
      var k = &a
      if b.v == 2 { k = &b } else { k = &b }   // pointed at b on every path
      a = new Item{v: 3}            // a is no longer pointed to: this is accepted
      println(k.v)
  }
  ```
- A call's arguments run left to right, and the callee gets all of them. A reference passed as an
  argument (to a `&` or `mut` parameter, or the bytes of `writeFile`) is held while the later
  arguments run, as a `let` reference to the same place would be: a later argument that moves,
  frees, replaces or changes what it points into is an error at that argument. A later argument
  that only reads, or changes a different place, is accepted, and so is one that frees something
  after an earlier argument's own call has returned. A value passed by copy (an int, a handle) is
  not held: a handle is checked where it is used.

  ```obo
  // example: error: argument 2 of two may free what argument 1 points into
  type Node = ref record { v: int }
  fn eat(a: []Node): int { return 1 }      // takes the array, and frees it
  fn two(n: &Node, k: int): int { return n.v + k }
  fn main() {
      var xs = new []Node(0)
      append(xs, new Node{v: 41})
      println(two(&xs[0], eat(xs)))        // eat frees xs before two reads n
  }
  ```

  ```obo
  // example: runs
  // prints 42
  type Node = ref record { v: int }
  fn eat(a: []Node): int { return 1 }
  fn two(n: &Node, k: int): int { return n.v + k }
  fn main() {
      var xs = new []Node(0)
      append(xs, new Node{v: 41})
      let k = eat(xs)                      // the call first: xs is not borrowed yet
      var ys = new []Node(0)
      append(ys, new Node{v: 41})
      println(two(&ys[0], k))
  }
  ```
- Changing through a reference needs a `mut` parameter. Passing a read-only reference
  to one is an error: `argument 1 of f needs a mut reference, but this one is read-only`.
  `mut` belongs to the parameter, not the type: a local reference is changeable exactly
  when the object it borrows is.
- Several references may name the same object, whether mutable or not. A change
  through one reference only invalidates references that may point *inside* what
  changed, so references to the object itself stay valid.
- A change reaches only the references that lie under the place it changed. A field
  is a place, and so is an element at a constant index: changing `w.b` leaves a
  reference to `w.a` valid, and changing `items[1]` leaves a reference to `items[0]`
  valid. An element whose index is not a constant may be any element, so changing
  `items[i]` invalidates references into all of them, and truncating the array
  invalidates every reference into it. Appending does not invalidate references.

  ```obo
  // example: error: 'first' may point to freed memory
  type Node = ref record { v: int }

  fn main() {
      var items = new []Node(0)
      append(items, new Node{v: 1})
      let first = &items[0]
      items[0] = new Node{v: 2}     // frees the element 'first' points into
      println(first.v)
  }
  ```

  ```obo
  // example: runs
  // prints 1
  // prints 1
  type Node = ref record { v: int }

  fn main() {
      var items = new []Node(0)
      append(items, new Node{v: 1})
      let first = &items[0]
      append(items, new Node{v: 2})  // growing the array keeps 'first' valid
      println(first.v)
      println(items[0].v)
  }
  ```

  ```obo
  // example: runs
  // prints 3
  // prints 7
  type Node = ref record { v: int }

  fn main() {
      var items = new []Node(0)
      append(items, new Node{v: 3})
      append(items, new Node{v: 4})
      let first = &items[0]
      items[1] = new Node{v: 7}     // a different element: 'first' stays valid
      println(first.v)
      println(items[1].v)
  }
  ```

## 5. Handles

`handle(x)` makes a `^T` from a record or closed value held in a variable, field
or element. A handle does not own its object, so it can link back to a parent,
point into a shared arena, or form a graph.

Every use of a handle checks that its object is still alive. If the object has
been freed, the program stops with exit code 7 and the message
`obo: the object this handle names has been freed`. `alive(h)` tells you without
stopping.

```obo
// example: runs
// prints root
type Node = ref record { name: string; parent: ?^Node }

fn main() {
    let root = new Node{name: "root", parent: nil}
    let kid = new Node{name: "kid", parent: handle(root)}
    let p = kid.parent
    if p != nil { println(p.name) }
}
```

```obo
// example: runs
// exit: 7
type Box = ref record { v: int }
type Link = ref record { to: ^Box }

fn main() {
    var b = new Box{v: 1}
    let l = new Link{to: handle(b)}
    println(l.to.v)                // prints 1
    b = new Box{v: 2}              // frees the first box
    println(l.to.v)                // exits 7 here
}
```

Where the compiler can see it, a use of a freed handle is an error instead. A
handle made by `let h = handle(b)` from a whole owned local `b` names `b`'s
object. Assigning over `b`, or `drop(b)`, frees that object, so after it a use
through `h` (`h.v`, `h.v = 1`) is refused, on every path that the assignment may
have run on. `alive(h)`, or a copy of `h`, is not a use; a handle that is not
used again after the assignment is not an error. Make the handle again after the
assignment to use the new object:

```obo
// example: error: handle 'h' may name a freed object: 'b' was assigned or dropped after the handle was made from it
type Box = ref record { v: int }

fn main() {
    var b = new Box{v: 1}
    let h = handle(b)
    println(h.v)
    b = new Box{v: 2}              // frees the first box
    println(h.v)                   // refused: h names it
}
```

Inside a loop, an assignment over the owner is refused when the handle was made
before the loop and the loop uses the handle anywhere in its body, before or after
the assignment in the text, since the next iteration would use the freed object:
`cannot assign over the owner of handle 'h' inside a loop that uses the handle`
(the same for a `drop` of the owner). A free that ends its block in a `break` or a `return`, with no
`continue` in the block, is not followed by another iteration (a `break` leaves only the loop it is in, so a
handle made outside an enclosing loop is still refused; a handle made in the scope directly outside the loop
it leaves is not, since no iteration of that loop follows the break): the loop may use the handle, and a use
after the loop is refused by the paths. `alive(h)`, as a bare name, does not count as the loop using the handle, and
neither does a `let` copy of a handle that is already known to be dead there (the copy is dead too). A handle made
inside the loop is fresh each iteration. A move of the owner (`var c = a`, passing it) on one path
does not hide a later free of the owner on a path that did not move it: `if k { var d = a; return } else { a = nb() }
use(h)` is refused, since the else path freed the object. A handle is safe after a free only if every path that
reaches it moved the owner. The compiler does not
know that two conditions agree, so `if k == 1 { c = a; a = nb(2) }  if k == 1 { a = nb(3) }  use(h)` is refused
although it is safe (known false positive): put the move and the free in one arm. If the arm that frees the object ends in
a `break` or a `continue`, a use of a handle made inside the loop after the `if`
is accepted, since that code runs only on the paths that did not free it; a use
after the loop is reached through the break, and is refused. A `defer` that uses
the handle runs after the free, so a free of the owner after the defer is
declared is refused: `cannot assign over 'b' while a defer declared earlier uses
handle 'h' made from it` (and `cannot drop`). The rule covers `let` handles
made from a whole local, and from a field or element of a local (below). A `var`
handle, a copy kept in a `var` or a record, a handle made through a reference
parameter, another handle or a global, and a handle parameter are checked at run
time, as above.

A `let` handle made from a field or element of an owned local (`handle(p.child)`,
`handle(p.kids[2])`, `handle(xs[i])`) is tracked by its place. It is dead after the
place, or any place above it, is assigned over, replaced, taken or truncated away,
or the owner is assigned over or dropped; directly, through a reference that may
point at or above the place, or by passing a `mut` argument that reaches the place
to a function that frees below its parameter. A change to a sibling field, to
another element with a constant index, to a scalar, or inside the object the handle
names does not reach it, nor does `append` or a `truncate` with a constant count
that keeps the element. A `mut` call frees only the places its callee frees below the
parameter (a callee that sets `p.other` leaves a handle to `p.child` alone); a recursive
or unknown callee, or one that frees many places, frees anywhere below the argument. An element with a variable index is reached by a change
to the array or to any element. A move of the owner is a move, as for a whole
owner. A write through a handle made from the whole owner is a change of the
owner. A write through any other handle is not seen, and the use is checked at run
time. Known false positives: the old value replaced (`replace`) and kept elsewhere, a
`truncate` with a count that is not a constant, and a free on a path the program does
not take.

```obo
// example: error: handle 'h' may name a freed object: a place in 'p'
type Kid = ref record { n: int }
type Par = ref record { child: Kid; other: Kid }

fn main() {
    var p = new Par{child: new Kid{n: 1}, other: new Kid{n: 2}}
    let h = handle(p.child)
    p.other = new Kid{n: 3}        // another place: h is still good
    println(h.n)
    p.child = new Kid{n: 4}        // frees the first child
    println(h.n)                   // refused: h names it
}
```

A test on `alive` narrows the handle: inside the block it guards, `h` is known to be live, so a use is
accepted although the owner may have been freed on some path before. The guard forms are `if alive(h) { ... }`,
`if alive(h) && cond { ... }` (and `cond` itself may use `h`), `if !alive(h) { ... } else { ... }` (the else
arm is narrowed) and `if !alive(h) { exit }` where the block always ends in a `return`, `break` or `continue`
(the code after the `if` is narrowed). `h` must be a bare name and `alive(h)` the first thing in the condition;
`!alive(h)` must be the whole condition, and an `||` anywhere in a condition with `&&` turns the narrowing off.
The narrowing ends at the next assignment over the owner or `drop` of it, which marks the handle dead again, and
it does not outlive the block (or, for the early exit, the enclosing block): after `if alive(h) { ... }` the
handle is as dead as it was before. Refused as before: `while alive(h)`, the else arm of `if alive(h)`,
`alive(f(h))`, `alive(h) == true`, `alive(h) || c`, `!alive(h) && c`, a guard on a copy or another handle, and a
loop that uses the handle and frees its owner anywhere in its body, guard or not.

```obo
// example: runs
// prints 2
type Box = ref record { v: int }

fn main() {
    var b = new Box{v: 1}
    let h = handle(b)
    var c = true
    if c { b = new Box{v: 2} }     // may free the object h names
    if alive(h) { println(h.v) } else { println(2) }
}
```

Handles can be stored in fields and optional fields (`?^Node`), and they are
checked for `nil` in the same way as other optionals. A handle needs a variable,
field or element, and must name a record or closed type.

## 6. Modules

Modules: one source file is one **module**, imported modules are compiled
dependency-first into the same image, and importers reach exported declarations
as `alias::name`. Each module also emits a text **symbol file** (`.sym`) holding
its exported interface, hashes and versions; an importer reads that interface and
`--check` can check a module against symbol files alone, including dependencies
whose source is absent. A full build still compiles every module body from source:
this is interface-level separate compilation, not object files or a linker. The
compiler itself stays one file and uses no imports.

### Headers, files and identity

A file may start with `module Name` (comments and blank lines first are fine).
An imported module **must** have a header, and its file must be named
`Name.obx`; the command-line file is the root and is exempt from the naming rule.
A headerless root is internally `_root`; `_root` is reserved and an imported
module may not declare it. Module identity is the **lexically normalized
resolved path**: repeated `/` collapse, `.` goes, `..` pops the previous
segment, a leading `/` stays, and symlinks are not resolved. So `lib.obx` and
`./lib.obx` are one module (compiled and initialized once), while two files
declaring the same `module Name` are the duplicate-name error. Symlinks are not
resolved: two spellings through different links are different identities, so if
both are reached the duplicate-name check reports the same declared name at two
paths. Use canonical module trees.

### Imports and aliases

```text
import "path.obx"
import "path.obx" as alias
```

The header comes first, then all imports, then declarations. An import after a
declaration is an error. A path is resolved against the importing file's
directory first, then each `-I DIR` in command-line order; an absolute path is
used as written, and the missing-module error lists the paths tried. The import
path must end in `.obx`, and its basename must match the declared module name.
The default alias is the declared module name; `as` overrides it. Everything is
qualified: there are no unqualified or selective imports and no re-exports.
Duplicate imports are allowed (the module is compiled and initialized once; two
aliases to it are fine). An alias may not repeat, clash with a top-level
declaration, or be a built-in name; a local may have an alias's name, since
`alias::` is always the module.

Module aliases live in their own namespace. A qualified name resolves the alias
to its module and then the member in that module's exports; a name that is not an
import alias is the error `'<x>' is not a module`, even if a local, global or
type has that name.

### Qualified names

`alias::name` reaches exported functions, types, variants, constants, globals and
record constructors: `lib::make(1)`, `new lib::Node{...}`, `new []lib::Node(0)`,
`?lib::Node`, `^lib::Node`, `Result[lib::Node]`, `lib::Limit`, `&lib::Cache`.
`match` arms stay bare (`Circle { ... }`), because the subject's closed type
names them; writing `lib::Circle` in an arm is refused with
`match arms name a variant without its module: write Circle`.

### Exports and privacy

Everything is private unless marked `pub`; `pub` is allowed only on top-level
`fn`, `type`, `const`, `var` and `let`. It is sticky across a forward heading and
its definition: the symbol is exported if either declaration says `pub`.
Importers see only the exports; reaching a private function, type, global or
constant reports `module '<m>' has no exported member '<x>' (only 'pub'
declarations are visible)`.

Exporting a closed type exports its variants, so an importer may write
`new lib::Circle{...}`. Exporting a record type exports only the type.

An exported declaration may not mention a **private** named type of its own
module, directly or through `[]`, `?`, `^`, `Result`, record fields or a
closed-type variant's fields:

`exported 'Shape' uses private type 'Secret' (variant 'A'); mark 'Secret' pub too`

A private type may mention exported types freely; imported types are fine
because their own module exported them. Type aliases are transparent: `type A =
lib::T` names the one object `lib::T`, and does not create or rename a type.

### Initialization, main and teardown

Imports are compiled at the import statement, before the importer's
declarations. Initializers run dependency-first, depth-first in import order,
once per module (a diamond runs the shared module once); the root's `main`
runs after every initializer. Only the root may declare `main`; a dependency's
`main` is refused (`module '<m>' may not declare 'main'; the program root
provides it`), and the root needs `fn main()` with no parameters or result.
After `main` returns, every module's owned and string globals are freed in
compile order (dependencies first), each exactly once, including a global of a
type from another module; then the runtime finishes. `OBO_CHECK=1` therefore
reports no leaks and no double frees across a module tree.

An initializer may call functions declared earlier in its module or in a
dependency, and read or write those modules' globals; it runs with no current
function, so its calls add nothing to any function's effect summary (there is no
caller to record them in).

### Type identity

A named type is one object for the whole compilation: the defining module's
declaration creates it and every importer reuses that object, so `a::T` in B, in
C and in A is the same type, and a record allocated in one module, freed or
matched in another, uses one descriptor address. Two modules that declare
structurally identical records have two different types; assigning one to the
other is an error (`cannot use b::T as a::T`). A type alias is the underlying
type for every rule; two aliases to one imported type, in one module or several,
still denote one object and one descriptor.

### Reserved type names and constants

A type may not be named `int`, `byte`, `bool`, `string`, `void`, `nil` or
`Result` (`cannot declare a type named '<name>'`), and `const` of `nil` is
refused (`a constant cannot be nil; use a typed optional var or let`). A module
alias may not use a built-in name.

### The dependency scan: `--deps`

`obo --deps SOURCE [-I DIR]...` reads module headers and import blocks only and
prints the normalized source paths in depth-first post-order: dependencies
before importers, the root last, one per line, deduplicated by normalized path.
It writes nothing, rejects `-o`, and reports missing files (exit 2), cycles
(exit 1), duplicate module names (exit 1), a missing imported header and the
basename rule with the same messages as a build. `make` can use it for tool
source prerequisites.

### Symbol files, `--check` and `--sym-dir`

Every module with a `module Name` header writes `Name.sym` after its declarations
are compiled and its summaries are complete. The file lives in the **symbol
directory**: `--sym-dir DIR`, or the root source's directory by default. The
directory is a flat namespace by module name and must already exist (the runtime
has no `mkdir`); a missing or unwritable directory is
`obxc: symbol-file directory '<dir>' does not exist or is not writable` (exit 2).
Two builds must not share a sym-dir: `writeFile` truncates in place and there is no
atomic rename, so parallel builds and tests use private directories.

The emission rule is one rule:

- dependencies always write their `.sym` (the importer reads them);
- a build with at least one import writes the root's `.sym` too when the root has a
  header;
- a single-file build writes no `.sym` unless `--emit-sym` is given;
- `--check` always writes the checked module's `.sym`, and never an executable.

`--emit-sym` is a flag on a normal build (it still writes the executable and still
accepts `-o`); it requires a `module` header. `--check`, `--deps` and `--emit-sym`
are mutually exclusive; `-o` is rejected with `--check` and `--deps`:
`obxc: -o cannot be used with --check or --deps`.

In a full build the importer binds the **live** interface (the defining module's
types, globals, functions and descriptors) and reads and validates the dependency's
`.sym` against it. `--check` instead reconstructs the interface from the `.sym`:
imported functions get a known dummy address and imported globals unique dummy
offsets, imported types have no descriptor, and the checked module's code is
discarded. `--check` compiles and checks the body on every run; it is not a build
cache. It does not require `main`: a checked module may have a private `main` (its
`.sym` omits it) or an exported `main` (the `.sym` lists it, and an importer rejects
it with `module '<m>' may not declare 'main'; the program root provides it`).

For each dependency, `--check` looks for `<sym-dir>/<basename>.sym`, where the
basename is the import path's file name without `.obx`; the `.sym`'s `module` line
must equal that basename. A dependency's source, if present, is used only to
validate its header (the basename rule) and its `source` hash; it is never
compiled. A source-less dependency is allowed if its `.sym` is valid. A **full
build** still requires every source; if only a `.sym` exists it reports
`obxc: cannot read module '<m>' at <paths>; only --check may use a symbol file
without source` (exit 2). The checked root is exempt from the basename rule (it is
the root of its check graph), and its `.sym` is named by its declared module name.

`--version` prints `obo 0.2 sym-format 1 abi 1`; the numbers are the `obosym`
format, the compiled-program ABI and the compiler version, each checked exactly
when a `.sym` is read. `--reemit-sym FILE` is a testing hook: it reads a symbol
file with the reader and prints the canonical re-emission, which the symbol-file
tests compare byte-for-byte.

### The `.sym` format and hashes

A `.sym` is text in a language-like declaration syntax, readable by the scanner:

```text
obosym 1
abi 1
compiler "0.2"
source 0x0123456789abcdef
dep io "io.obx"
interface 0xfedcba9876543210
module strings
depiface io 0x1111111111111111
type Builder uid 0x3333333333333333
type Builder = ref record { items: []string; count: int }
type Node = ref record { value: int; next: ?Node }
type Shape = closed { Circle { r: int } Rect { w: int; h: int } }
type Text = []string
const Limit: int = 0x000000000000000a
const Empty: string = hex "6e6f6e65"
var Cache: []Node
let Default: int
fn split(p0: string, p1: string): []string
fn reset(mut p0: &Builder) pfree 0x1 pf 0 "=/items" frees 1 strings::scratch
```

- The first word is the single identifier `obosym` (not `obo-sym`), then the
  format number; the reader requires exact `obosym`, `abi` and `compiler` lines.
- `source` is the FNV-1a 64 hash of the module's source bytes exactly.
- `dep` lines are the direct dependencies, one per unique module in first-import
  order, with the path **as imported** (relative stays relative). They are
  diagnostic and outside the interface hash.
- `interface` is the FNV-1a 64 hash of the exact bytes after that line. The body
  holds the module name, one `depiface <module> <hash>` per dependency in the same
  order, then the exported declarations. A dependency's interface change therefore
  changes every dependent's interface hash; a body-only change does not. Import
  order is part of the hash because it determines initialization order.
- Declarations are emitted in canonical groups: shells for exported record/closed
  types (source order), their definitions (source order), aliases, constants,
  globals, functions. Private declarations are skipped without reordering the
  exported ones. No address, offset, descriptor, pointer, timestamp or absolute
  path appears.
- A record definition lists its fields in declaration order (offsets are
  recomputed: one 8-byte word each); a closed definition lists its variants in
  declaration order. Every exported record/closed type has a shell line first, so
  recursive and mutually recursive types resolve. Variants are registered by the
  reader under `Module::Variant` and exported with their closed type.
- An alias `type A = <typeExpr>` maps the name to the existing type; it never
  re-declares it. Field and signature types are emitted as the underlying type, so
  layout never depends on an alias.
- Constants are primitive: `int`/`byte` as 16 hexadecimal digits (negative values
  wrap), `bool` as `true`/`false`, `string` as `hex "<bytes>"` (including `hex ""`).
  The reader interns string bytes instead of carrying a rodata address.
- Globals are `var Name: T` or `let Name: T`, with no initializer; a `let` is
  read-only to importers and the defining module owns initialization.
- Function headings reuse the source syntax for parameters, `&` and `mut`, but the
  writer **always** emits the canonical names `p0`, `p1`, ...; parameter names are
  syntax only, so renaming a source parameter changes the source hash but not the
  interface hash. `pfree` is the 62-bit mask of parameters below which the function
  may free; `frees N key...` names the globals it may free (a private or transitive
  `Module::name` key is allowed); `pf i "<event>"` records a precise free place,
  with `=` (the value in the place) or `!` (what lies below it) and a path of
  `/field`, `/[n]` and `/[]` segments. Summary items may be read in any order; the
  writer emits `freesall`, `writeshandles`, `pfree`, `frees`, then `pf` rows by
  parameter and insertion order.

The reader validates hashes, versions, shells, field/variant structure, duplicate
keys and exports, parameter modes, constants, `frees` counts and keys, and the
`pf`/`pfree` consistency before using any value. Malformed files are
`obxc: <sympath>:<line>:<col>: symbol file: <detail>` (exit 2).

### Versions and the trust model

Three exact-match values version a `.sym`: `obosym` (format/grammar and meaning),
`abi` (record layout, descriptor format, calling convention, global addressing,
effect-summary meaning and initialization order), and `compiler` (`OBO_VERSION`).
Bump `OBO_VERSION` with a compiler feature; bump `obosym` on a grammar or meaning
change; bump `abi` on a layout/ABI/effect change. A mismatch is reported with the
line that differs:

- `obxc: symbol file '<p>' has format <n>; this compiler reads <m>; rebuild it`
- `obxc: symbol file '<p>' has ABI <n>; this compiler uses <m>; rebuild it`
- `obxc: symbol file '<p>' was built by compiler '<v>'; this is '<w>'; rebuild it`

FNV-1a is a staleness and integrity check, **not** a cryptographic one. Symbol
files are trusted build outputs: a forged but well-formed interface can make
`--check` accept an unsound program (it can claim a callee frees nothing). Do not
`--check` against untrusted symbol files, and do not treat a `.sym` as a security
boundary. A full build re-reads and validates the freshly written interface, so a
missed version bump only affects `--check` against stale artifacts.

Generated `.sym` files under the build convention (`.obo/sym`) are gitignored;
an arbitrary `--sym-dir` is not automatically ignored. A build without
`--sym-dir` writes next to the root source, so use `--sym-dir .obo/sym` in a
checkout. `tests/modules/golden/expected/*.sym` are
committed byte-goldens and are not ignored. The golden tests never rewrite a fixture in the gate; `sym_test.py
--update` is the explicit regeneration path.

### A checked example

The first file is the root; the second is compiled through `-I`.

```obo
// example: modules
// file: main.obx
module main
import "lib.obx"
fn main() {
    let n = lib::make(4)
    println(lib::value(n))
}
// file: lib.obx
module lib
pub type Node = ref record { v: int }
pub fn make(v: int): Node { return new Node{v: v} }
pub fn value(n: Node): int { return n.v }
// prints 4
```

A source-less check: the same tree is built, the dependency source is deleted, and
`--check` reads only the symbol file.

```obo
// example: modules check
// file: main.obx
module main
import "lib.obx"
fn main() {
    let n = lib::make(4)
    println(lib::value(n))
}
// file: lib.obx
module lib
pub type Node = ref record { v: int }
pub fn make(v: int): Node { return new Node{v: v} }
pub fn value(n: Node): int { return n.v }
// prints 4
```

```obo
// example: modules error: module 'lib' has no exported member 'secret'
// file: main.obx
module main
import "lib.obx"
fn main() { println(lib::secret()) }
// file: lib.obx
module lib
fn secret(): int { return 1 }
```

### Module errors

| Situation | Message | Exit |
|---|---|---|
| missing imported source | `obxc: cannot read module '<m>' at <paths tried>` | 2 |
| imported file has no header | `imported module '<path>' needs a 'module Name' header` | 1 |
| file/header name mismatch | `module '<m>' must be in a file named <m>.obx (found <path>)` | 1 |
| import cycle | `import cycle: a.obx -> b.obx -> a.obx` | 1 |
| duplicate module name | `module '<m>' is declared by both <path1> and <path2>` | 1 |
| `module _root` in a dependency | `module '_root' is reserved for the program root` | 1 |
| import after a declaration | `imports must come before declarations` | 1 |
| repeated alias | `'<a>' is already used as an import alias` | 1 |
| alias vs a declaration | `'<a>' clashes with a top-level declaration` | 1 |
| alias is a built-in name | `cannot use '<a>' as an import alias: it is a built-in name` | 1 |
| qualified name on a non-module | `'<x>' is not a module` | 1 |
| unknown/private member | `module '<m>' has no exported member '<x>' (only 'pub' declarations are visible)` | 1 |
| `pub` in a block | `'pub' is only allowed on top-level declarations` | 1 |
| `pub import` | `'pub' applies to declarations, not imports` | 1 |
| private type in an export | `exported '<x>' uses private type '<T>'; mark '<T>' pub too` | 1 |
| `main` in a dependency | `module '<m>' may not declare 'main'; the program root provides it` | 1 |
| qualified match arm | `match arms name a variant without its module: write Circle` | 1 |
| reserved type name | `cannot declare a type named '<name>'` | 1 |
| `const nil` | `a constant cannot be nil; use a typed optional var or let` | 1 |
| import path not `.obx` | `import paths must end in .obx` | 1 |
| missing `.sym` in `--check` | `obxc: no symbol file for module '<m>' in <dir>; run a build first` | 2 |
| stale `.sym` | `obxc: symbol file '<p>' is stale: '<src>' changed since it was built; run a build` | 2 |
| format mismatch | `obxc: symbol file '<p>' has format <n>; this compiler reads <m>; rebuild it` | 2 |
| ABI mismatch | `obxc: symbol file '<p>' has ABI <n>; this compiler uses <m>; rebuild it` | 2 |
| compiler mismatch | `obxc: symbol file '<p>' was built by compiler '<v>'; this is '<w>'; rebuild it` | 2 |
| corrupt body hash | `obxc: symbol file '<p>' is corrupt (interface hash); rebuild it` | 2 |
| dependency interface mismatch | `obxc: symbol file '<p>' depends on '<d>' with interface hash <h>, but '<d>' has <h2>; rebuild the dependent module` | 2 |
| `.sym` vs a build's live interface | `obxc: symbol file '<p>' disagrees with the compiled interface of '<m>' for <decl>; rebuild it` | 2 |
| malformed `.sym` | `obxc: <sympath>:<line>:<col>: symbol file: <detail>` | 2 |
| source-less dependency in a build | `obxc: cannot read module '<m>' at <paths>; only --check may use a symbol file without source` | 2 |
| sym-dir missing or unwritable | `obxc: symbol-file directory '<dir>' does not exist or is not writable` | 2 |
| `-o` with `--check`/`--deps` | `obxc: -o cannot be used with --check or --deps` | 2 |

Qualified names in diagnostics are `Module::name` with the declared module name
(not the alias), and types keep the bare name inside their own module. An error
about an imported value says `lib::split` or `lib::Cache`; an error about a
value of an imported type says `a::T` or `[]a::T`. A local alias use may print
the underlying type, because aliases are transparent.

A full build needs every module's source. `--check` is the separate-checking
mode: it needs the checked module's source and its dependencies' symbol files, not
their sources. Symbol files are trusted build outputs, not a security boundary; see
"The `.sym` format and hashes" below.

## 7. Results and try

`Result[T]` is the type of an operation that can fail with a reason. It is a
built-in parameterized type: one owned cell holding one of two variants, `ok`
(one field `value: T`) or `err` (fields `message: string` and `code: int`). The
err variant and its descriptor are shared by every `Result[T]`, so an error cell
made for one success type can be returned from a function whose success type is
another. A result is always one allocated cell; the payload follows `T` (an owned
pointer, a reference-counted string, or a copied scalar or handle).

Construct with `ok(v)` and `err(message[, code])`; the code defaults to 0. `ok`
takes ownership of its value and rejects `nil`, a void expression, a borrowed
value and an error value (`ok(err(...))` has no payload type: type the error in a
`Result` local first). A success with no value needs a typed optional
(`let n: ?Node = nil; return ok(n)`) because `ok(nil)` has no type. `err` takes a
reference to its message, so a heap message it is given is kept alive by the cell.

A result is owned like a record: it is freed at scope exit, can be stored in
fields, arrays, optionals and globals, and its payload or message is freed with
it. `Result` is invariant: `Result[byte]` is not a `Result[int]`, because the ok
tag belongs to the success type. `==` and `!=` compare result cells by identity,
and both sides must be stored values, not `ok(...)`/`err(...)` temporaries;
`nil` comparison is only for `?Result[T]`.

```obo
// example: runs
// prints 3
// prints bad 7
fn get(n: int): Result[int] {
    if n < 0 { return err("bad", 7) }
    return ok(n)
}

fn main() {
    let a = get(3)
    match a {
        ok { println(a.value) }
        err { println(a.message, " ", a.code) }
    }
    let b = get(-1)
    match b {
        ok { println(b.value) }
        err { println(b.message, " ", b.code) }
    }
}
```

`match` narrows the result to the variant, as for a closed type: `r.value` in the
`ok` arm, `r.message` and `r.code` in the `err` arm. Both arms are required and
`else` is not allowed. This match does not consume the result: scalars, handles
and strings can be copied out (`let s = r.value` takes a string reference), and an
optional payload can be `take`n; an owned record or array cannot be moved out,
because the result still owns it.

A consuming match binds the payload instead. Write `ok(v)` and `err(m, c)`; every
arm must bind, or none may. The scrutinee must be a whole local or parameter, not
a reference: it is moved, the shell is freed, and the bindings are ordinary locals
of the arm, freed where the arm's block ends or its `return` leaves. A string
binding owns the cell's reference; returning `err(m, c)` from an `err` arm builds
a new cell from it.

```obo
// example: runs
// prints 5
// prints from 9
type Node = ref record { v: int }

fn get(n: int): Result[Node] {
    if n < 0 { return err("no node", 9) }
    return ok(new Node{v: n})
}

fn main() {
    let r = get(5)
    match r {
        ok(n) { println(n.v) }
        err(m, c) { println(m, " ", c) }
    }
    let e = get(-1)
    match e {
        ok(n) { println(n.v) }
        err(m, c) { println("from ", c) }
    }
}
```

`try e` propagates a failure. Its operand must be an owned `Result`: a whole
local or parameter, or a fresh call result. On ok, `try` moves the payload out and
frees the empty cell; on err, it runs the function's defers, frees its live owned
locals, and returns the error cell itself, so the caller sees the same message and
code. The enclosing function must return a `Result` (of any success type); `main`
does not, so it matches a helper's result instead of trying it.

```obo
// example: runs
// prints 4
// prints deeper 9
fn bottom(n: int): Result[int] {
    if n < 0 { return err("deeper", 9) }
    return ok(n)
}

fn addOne(n: int): Result[int] {
    let v = try bottom(n)
    return ok(v + 1)
}

fn main() {
    let a = addOne(3)
    match a {
        ok { println(a.value) }
        err { println(a.message, " ", a.code) }
    }
    let b = addOne(-1)
    match b {
        ok { println(b.value) }
        err { println(b.message, " ", b.code) }
    }
}
```

The tried result's success type need not be the enclosing function's: a
`Result[[]byte]` error tried inside a `Result[int]` function returns the shared
error cell, and the caller's `err` arm reads it. Nesting works the same way:
`try try f()` with `f: Result[Result[int]]` unwraps both. `try` in a loop is fine
with a fresh local per iteration; a local declared before the loop and moved by
`try` is refused at the back edge, as for any move.

`try` may appear only where its value is the first thing evaluated: the whole
value of a `let` or `var` initializer, an assignment's right-hand side (an
operator may follow on the left, as in `try f() + 1`), or a statement. It may not
be an argument, a record field, an array length, an index, a condition, a return
value, a match scrutinee, a `for` bound, a global initializer, or the right operand
of an operator, because the early return would leave a half-built value or a
pushed argument behind. It may not appear inside `defer`, whose body runs
out of line and cannot return from the function. The rule is a designed
restriction, not a hole: lifting it needs cleanup of live temporaries (drop glue).

```obo
// example: error: a try expression cannot be part of a larger expression
fn get(n: int): Result[int] { return ok(n) }
fn eat(n: int): int { return n }

fn f(): Result[int] {
    let v = eat(try get(1))
    return ok(v)
}

fn main() {}
```

```obo
// example: error: try needs a function that returns a Result; 'main' returns nothing
fn get(n: int): Result[int] { return ok(n) }

fn main() {
    let v = try get(1)
}
```

Other limits:

- A result in a field, element or global cannot be moved out: use `replace` (or
  `take` on an optional one) to get it into a local, then `try` that. `match` also
  needs a whole local; inspect a field through a local reference (`match p`).
- `obj.field = try f()` is refused when the call might free the object holding the
  target (the assignment-target rule, section 12): compute the value in a local
  first. A call that cannot free (a pure callee such as `get` above) is accepted:
  `b.n = try get(3)` compiles.
- Inside a non-consuming `ok` or `err` arm the scrutinee is narrowed to that
  variant, so `try r` there is refused ("try needs a Result value, not ok"):
  `try` the Result before the match.
- A deferred cleanup cannot propagate its error; its result is discarded and
  freed. Record the error and return it from the main path instead.
- `?Result[T]` may be nil; check it with `!= nil` before `try`, which narrows it
  to `Result[T]`. `try` on the optional itself is refused.
- `Result` has no fields until a `match` arm narrows it, and a `Result` value can
  never be `nil`.

```obo
// example: error: a Result match names the ok and err arms; 'else' is not needed
fn get(n: int): Result[int] { return ok(n) }

fn main() {
    let r = get(1)
    match r {
        ok { println(r.value) }
        err { println(r.message) }
        else { }
    }
}
```

```obo
// example: error: ok(nil) has no type
type Node = ref record { v: int }

fn f(): Result[?Node] {
    return ok(nil)
}

fn main() {}
```

```obo
// example: error: cannot use Result[byte] as Result[int]
fn getb(): Result[byte] { return ok(byte(1)) }

fn f(): Result[int] {
    return getb()
}

fn main() {}
```

## 8. Built-in functions

| Function | What it does |
|---|---|
| `print`, `println` | write values to standard output; `println` adds a newline |
| `eprint`, `eprintln` | the same, to standard error |
| `len(x)` | length of a string or array |
| `append(a, x)` | add `x` to the end of array `a`; `x` is moved in |
| `truncate(a, n)` | shorten `a` to `n` elements; the removed elements are freed |
| `substr(s, from, to)` | part of a string |
| `int(x)`, `byte(x)` | conversions |
| `string(x)` | an int's digits, or the string made from a `[]byte` |
| `readFile(path)` | `?[]byte`, or `nil` if the file cannot be read |
| `writeFile(path, data, executable)` | write bytes; returns `bool` |
| `openFile(path, mode)` | `Result[int]`: open `path` with mode `"r"`, `"w"` or `"a"`; the caller closes the descriptor |
| `readFd(fd, max)` | `Result[?[]byte]`: one read of up to `max` bytes; `nil` is end of file |
| `writeFd(fd, data)` | `Result[int]`: one write of the borrowed `[]byte`; a short count is success |
| `closeFile(fd)` | `Result[bool]`: `ok(true)` on success |
| `stdin()`, `stdout()`, `stderr()` | the standard descriptors, 0, 1 and 2 |
| `args()` | the command-line arguments, as `[]string` |
| `exit(code)` | stop with the given exit code |
| `take(x)` | move out of an optional variable, field or element; leaves `nil` |
| `drop(x)` | free a local variable now, rather than at scope exit |
| `replace(place, value)` | store `value` in `place` and return the old value, which the caller owns |
| `handle(x)` | make a handle to `x` (section 5) |
| `ok(v)` | a `Result[T]` success holding `v`, which is moved in |
| `err(message[, code])` | a `Result[T]` failure; code defaults to 0 |
| `alive(h)` | `true` if the handle's object has not been freed |
| `gcstats()` | print the live object count and peak bytes |
| `gc()` | does nothing; there is no collector |

`drop` and `replace` need a local variable or a place holding an owned value:
`drop(r.child)` is rejected.

```obo
// example: runs
// prints 3 4
// prints 1
type E = ref record { v: int }

fn main() {
    var e = new E{v: 3}
    let old = replace(e, new E{v: 4})
    println(old.v, " ", e.v)
    let d = new E{v: 1}
    drop(d)                        // freed now
    println(1)
}
```

### Incremental I/O

`openFile` returns `Result[int]`: the descriptor is a copied scalar with no
owner, and the program closes it with `closeFile`, normally through `defer`.
The modes are exactly `"r"`, `"w"` (`O_CREAT|O_TRUNC`) and `"a"` (`O_CREAT|
O_APPEND`). A constant mode is checked at compile time; any other problem is a
Result: `err("cannot open " + path, errno)`, with errno in `code` (2 for a
missing file, 22 for an empty path or an embedded NUL, 36 for a path of 4096
bytes or more). `readFd(fd, max)` makes one read: `ok(nil)` means end of file,
a non-nil array of 1..max bytes is data, and a count that is zero or negative is
`err("readFd needs a positive count", EINVAL)` before the max-sized array is
allocated. `writeFd(fd, data)` borrows the array and makes one write; a short
count is success, so the caller loops. `closeFile(0/1/2)` is refused with
EINVAL, so a program cannot close the standard streams; `closeFile` on any other
descriptor is a Result, and its error in a `defer` is discarded. A descriptor
number can be reused after a close: the language does not track ownership of the
number, so a stale `int` may read or close a file opened later under the same
number.

`exit(...)` runs no defers: a `defer closeFile(fd)` does not run, and the
operating system reclaims the descriptor. Return from `main` instead when the
`OBO_CHECK` oracles (a leaked fd exits 8; a double close or a use after close
exits 9) should run.

```obo
// example: runs
// prints 22
// prints readFd needs a positive count
fn main() {
    let n = 0
    let r = readFd(stdin(), n)          // computed: EINVAL, and stdin is not read
    match r {
        ok { println("unexpected ok") }
        err { println(r.code); println(r.message) }
    }
}
```

```obo
// example: runs
// prints 0 1 2
// prints HI
// prints 2
fn main() {
    println(stdin(), " ", stdout(), " ", stderr())
    let b = new []byte(2)
    b[0] = 'H'
    b[1] = 'I'
    let r = writeFd(stdout(), b)        // the text above is flushed first
    match r {
        ok(n) { println(""); println(n) }
        err(m, c) { println(m) }
    }
}
```

`obo/lib/io.obx` is a source module (`module io`) that builds buffered
`Reader`/`Writer` types on these builtins (`fill`, `readLine`, `readN`,
`readByte`, `takeChunk`, `flush`, `pump`, `count`) and adds `writeByte`;
importers write `import "../lib/io.obx" as io` (or `"io.obx"` with `-I obo/lib`)
and qualify the names (`io::newReader`, `io::flush`). Its own header documents
the API and the fd-is-borrowed rule. The rest of the stdlib is a set of source
modules under `obo/lib/`: `strings` (`cmp`, split/join, trim, natord and
QuoteSpaces helpers), `paths`, `sort`, `time`, `cli`, `fmt` (`digits`,
`appendPadded`, `writePadded`, `modeString`) and `errors` (C-locale errno
`text`, `osError`, `debugPath` and the eza-shaped `EXIT_*` constants). P2 moved
`writePadded` from `io` and the digit/padding helpers from `strings` into
`fmt`, and removed the four P1 compatibility functions.

## 9. Runtime checks and exit codes

| Exit code | Cause |
|---|---|
| 2 | negative array length, or out of memory |
| 3 | index out of range (array, string, `substr` or `truncate`), with the line |
| 4 | an internal error, such as a misaligned stack in a runtime call |
| 5 | with `OBO_CHECK=1`: the program ended with live objects (a leak) |
| 6 | with `OBO_CHECK=1`: a double free |
| 7 | a handle was used after its object was freed |
| 8 | with `OBO_CHECK=1`: the program ended with file descriptors opened by `openFile` and never closed |
| 9 | with `OBO_CHECK=1`: a tracked file descriptor was closed twice or used after it was closed |

Division by zero stops the program with a hardware trap (SIGFPE).

The `code` of an I/O `Result` is the OS errno, positive (0 means unspecified);
the trap codes 2 to 9 are a different namespace and overlap numerically with
errnos (ENOENT is 2, EBADF is 9), so a program must not do `exit(r.code)`:
a missing input file would look like an out-of-memory trap. Print the message
and choose the exit code deliberately.

Environment variables:

- `OBO_CHECK=1` counts allocations and frees, and makes leaks and double frees
  exit with codes 5 and 6.
- `OBX_GC=poison` overwrites freed memory, so a use of freed memory shows up as
  garbage rather than as a value that happens to still be there.
- `OBO_STATS=1` prints the exact live peak and total allocation at exit.

## 10. Compile errors

The messages below are the compiler's own text; the parts in angle brackets
depend on the program.

| Message | Meaning and fix |
|---|---|
| `'<x>' was moved and has no value here` | `<x>` was given away by a move. Assign it again before using it, or borrow it before the move. |
| `'<x>' is moved on one path but not another` | a branch or match arm moves `<x>` and another does not. Move on both paths, or on neither. |
| `'<x>' is moved inside the loop` | a loop moves `<x>` and does not give it back. |
| `cannot move out of a field or element; use take() on an optional one` | use `take(...)`, or borrow with `&`. |
| `cannot move out of global '<g>'; use take() on an optional one` | the same, for a global. |
| `cannot move '<x>' inside && or \|\|` | move the value outside the condition. |
| `cannot move '<x>' inside defer` | do the move in the body of the block. |
| `store this value in a variable first: otherwise it would never be freed` | a temporary value is used directly; store it in a variable first. |
| `store this value in a variable first: a reference cannot borrow from a temporary` | `&` needs a variable, field or element. |
| `'<r>' may point to freed memory: something under what it points into was changed; point it again first` | a use of `<r>` (a read, a write, a call, a re-borrow, a defer) on a path where a change freed what `<r>` pointed into, including a change on an earlier pass of a loop, or on one path only. Point `<r>` again before the use, or borrow it again after the change. |
| `argument <n> of <f> needs a mut reference, but this one is read-only` | the parameter is `mut`, but the argument was borrowed read-only. |
| `argument <n> of <f> may point inside argument <m>, which is mut` | two arguments may overlap and one of them changes. |
| `argument <n> of <f> may free what argument <m> points into, and runs after it; compute it before the call` | a later argument moves, frees or changes what an earlier reference argument points into; compute it into a local before the call. |
| `argument <n> of <f> makes a recursive call that writes through a handle, which could free what argument <m> points into; compute it before the call` | the same, for a recursive call in a function that writes through a handle (reported at the end of the body). |
| `'<x>' may be nil: check it with '!= nil' first` | check an optional before using its fields. |
| `cannot pass a reference that borrows through a handle to <f>: it might free what the handle names` | a reference borrowed through a handle may go only to a call that cannot free anything; pass the handle instead, or make the call pure. |
| `cannot pass a reference that points inside something to <f>: it writes through a handle, which could free it` | a reference inside a field or element may not go to a function that writes through a handle; pass a reference to the whole object, or the value. |
| `cannot pass a reference that points inside something to <f>: its body is not known yet, so it is assumed to write through a handle, which could free it` | `<f>` is only declared so far: define it before the call, or pass a whole object. |
| `'<r>' may point to freed memory: a recursive call writes through a handle, which could free what it points into` | a recursive call in a function that writes through a handle may free what `<r>` points into; borrow it again after the call. |
| `a recursive call in this loop writes through a handle, which could free what a reference inside something points into on the next iteration` | a loop calls its own function, which writes through a handle; borrow the reference inside the loop, or move the call out of it. |
| `cannot change what '<r>' points into while a for loop walks it` | a change, or a call that might free, while a loop walks `<r>`; make it after the loop. |
| `cannot point '<r>' elsewhere in a defer: a defer body runs where its block is left, and the change would not be seen there` | a defer body points a reference declared before it at something else; do the change in the block, or declare the reference inside the defer body. |
| `cannot point '<r>' elsewhere while a defer uses it: the defer runs where its block is left, and would use the new target` | a defer body uses `<r>` and the block points `<r>` at another place before it is left; point a different reference instead, or declare `<r>` after the defer. |
| `'<r>' is used by a defer but may point to freed memory at the block's end: something under what it points into was changed` | a change after the defer statement freed what `<r>` points into, and the defer runs at the block's end; make the change after the block, or borrow `<r>` inside the defer. |
| `'<r>' is used by a defer but may point to freed memory at the block's end: a recursive call writes through a handle` | the same, for a recursive call in a function that writes through a handle. |
| `'<r>' is pointed somewhere new inside the loop, and read before it is pointed again; point it before the loop, or point it afresh before reading it in the loop` | a loop reads `<r>` before pointing it again, and a change in the loop may have freed what it points into, or it points somewhere the body was not checked with on the next iteration; point it afresh before reading it. |
| `'<r>' is read-only where the loop starts again, and written before it is pointed again; point it afresh before writing it in the loop` | `<r>` is writable where the loop starts but read-only where it starts again, and is written before it is pointed again; point it afresh before writing it. |
| `the references in this loop change too often to be checked; point them afresh in the loop` | the loop's references did not settle within the checker's passes; point the references afresh inside the loop. |
| `take needs an optional variable, field or element holding an owned value` | `take` applies only to optional owned places. |
| `replace needs a variable, field or element holding an owned value` | `replace` applies only to owned places. |
| `drop needs a local variable holding an owned value` | `drop` applies only to local owned variables. |
| `handles name records and closed types, not <t>` | `^T` is only for records and closed types. |
| `handle '<h>' may name a freed object: '<x>' was assigned or dropped after the handle was made from it; make the handle again` | `let <h> = handle(<x>)`, then `<x>` was assigned over or dropped on some path, which freed the object `<h>` names, and `<h>` is used after it. Make the handle again after the assignment. |
| `cannot assign over the owner of handle '<h>' inside a loop that uses the handle` | the loop uses `<h>` somewhere in its body, and the next iteration would use the freed object. Make the handle inside the loop, or assign after it. |
| `cannot assign over '<x>' while a defer declared earlier uses handle '<h>' made from it` | the defer runs at the block's end, after the assignment (or `cannot drop`), and would use the freed object. |
| `cannot move '<x>' inside a loop while handle '<h>' made from it is in scope` | a loop moves (or drops) `<x>` while a handle made from it before the loop is in scope. Make the handle inside the loop. |
| `handle '<h>' was made from an owner that a loop moved: make the handle again` | a loop that did not name `<h>` moved its owner, so `<h>` may not be named after it (except in `alive`). |
| `replace cannot change '<r>': it is a reference` (also `take`) | `<r>` is a reference local; replace or take the field or element it borrows. |
| `try needs a function that returns a Result; '<f>' returns <t>` | `try` outside a function whose result is a `Result`; `main` returns nothing, so match a helper's result instead. |
| `try needs a function that returns a Result; there is no function here` | `try` in a global initializer. |
| `try needs a Result value, not <t>` | the operand of `try` is not a `Result`; the hint names `?Result[T]` (check it with `!= nil`) or `err(...)` (it has no success type). |
| `a try expression may only be the value of a let, var or assignment, or a statement` | `try` in a condition, a return value, a match scrutinee or a `for` bound. |
| `a try expression cannot be part of a larger expression; assign it to a local first` | `try` in an argument, a record field, an array length, an index or the right operand of an operator. |
| `cannot use try in a defer: a deferred statement cannot return from the function` | `try` inside `defer`. |
| `ok needs a value, not a void expression` | `ok` of a call that returns nothing. |
| `ok(nil) has no type: give the payload a name first, as in 'let n: ?Node = nil; ok(n)'` | name the typed optional first. |
| `ok cannot hold an error value: type it as a Result first, as in 'let inner: Result[int] = err(...); ok(inner)'` | `ok(err(...))`. |
| `ok takes ownership of its value: it cannot hold a borrowed reference` | `ok(&x)`. |
| `err needs a string message, not <t>` | the first argument of `err` is not a string. |
| `err needs an int code, not <t>` | the second argument of `err` is not an int. |
| `cannot infer a type for '<x>' from an error value; declare it as Result[T]` | `err(...)` needs a declared `Result` destination. |
| `Result needs a type: write Result[T]` | `Result` without its type argument. |
| `a Result match names the ok and err arms; 'else' is not needed` | `else` in a `Result` match. |
| `a consuming match binds its value in every arm: write ok(v) and err(m, c)` | some arms bind and some do not. |
| `a consuming match needs the Result itself, not a reference` | a consuming match on a reference; match the reference non-consumingly. |
| `a consuming match needs a local variable or parameter holding an owned Result` | a consuming match on something that is not an owned result. |
| `openFile needs a string path, not <t>` | the first argument of `openFile` is not a string. |
| `openFile needs a string mode, not <t>` | the second argument of `openFile` is not a string. |
| `unknown open mode '<m>': use "r", "w" or "a"` | a constant `openFile` mode is not one of the three. |
| `readFd needs an int file descriptor, not <t>` | the first argument of `readFd` is not an int. |
| `readFd needs an int count, not <t>` | the second argument of `readFd` is not an int. |
| `readFd needs a positive count` | a constant `readFd` count is zero or negative. |
| `writeFd needs an int file descriptor, not <t>` | the first argument of `writeFd` is not an int. |
| `writeFd needs a []byte, not <t>` | the second argument of `writeFd` is not a `[]byte`. |
| `closeFile needs an int file descriptor, not <t>` | the argument of `closeFile` is not an int. |

## 11. Differences from obx

- There is no garbage collector. Memory is freed at scope exit, on reassignment,
  or by `drop`, and the compiler checks the freeing.
- References (`&T`) borrow owned values. They are checked by the
  compiler rather than by runtime collection.
- Handles (`^T`) are the way to write back-references and graphs. They are checked
  at run time.
- New built-ins: `take`, `drop`, `replace`, `handle`, `alive`, `ok`, `err`,
  `openFile`, `readFd`, `writeFd`, `closeFile`, `stdin`, `stdout`, `stderr`, and
  the prefix operator `try`.
- `Result[T]` is a built-in parameterized type, inspected with `match` (including the
  consuming `ok(v)`/`err(m, c)` form).
- Modules are source imports: `module Name`, `import "Name.obx" [as alias]`,
  `pub`, and `alias::name`. obx's `module`/`import`/`pub` tokens were accepted
  but unimplemented; obo has no unqualified or selective imports and no
  re-exports in Stage 1.
- `gc()` does nothing.
- Strings are reference counted rather than collected.

## 12. Known holes and planned changes

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
`README.md` also apply: methods and interfaces, lambdas, generic modules, value records, `inout` parameters, and debug
information.
