# The obo language

obo is a small language in the Wirth tradition, with C-style braces. It has no
garbage collector. Every owned value has exactly one owner, which frees it at a
known point, and the compiler rejects any program that could read freed memory.

This describes the language as the compiler in `obo/src/obo.obo`
implements it. Syntax not covered here (declarations, `if`, `while`, `for`,
`match`, `defer`, closed types, constants, literals, operators) is the same as
obx; see the language section of the root `README.md`. Rules that are planned
rather than implemented are marked as such, and section 10 lists known holes.

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
  frees no global, changes through no `mut` parameter, writes no owned value through a
  handle, is compiled before the call, and is not recursive. The reference cannot be passed to
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

fn main() {
    var b = new Box{v: 1}
    let h = handle(b)
    println(h.v)                   // prints 1
    b = new Box{v: 2}              // frees the first box
    println(h.v)                   // exits 7 here
}
```

Handles can be stored in fields and optional fields (`?^Node`), and they are
checked for `nil` in the same way as other optionals. A handle needs a variable,
field or element, and must name a record or closed type.

## 6. Built-in functions

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
| `args()` | the command-line arguments, as `[]string` |
| `exit(code)` | stop with the given exit code |
| `take(x)` | move out of an optional variable, field or element; leaves `nil` |
| `drop(x)` | free a local variable now, rather than at scope exit |
| `replace(place, value)` | store `value` in `place` and return the old value, which the caller owns |
| `handle(x)` | make a handle to `x` (section 5) |
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

## 7. Runtime checks and exit codes

| Exit code | Cause |
|---|---|
| 2 | negative array length, or out of memory |
| 3 | index out of range (array, string, `substr` or `truncate`), with the line |
| 4 | an internal error, such as a misaligned stack in a runtime call |
| 5 | with `OBO_CHECK=1`: the program ended with live objects (a leak) |
| 6 | with `OBO_CHECK=1`: a double free |
| 7 | a handle was used after its object was freed |

Division by zero stops the program with a hardware trap (SIGFPE).

Environment variables:

- `OBO_CHECK=1` counts allocations and frees, and makes leaks and double frees
  exit with codes 5 and 6.
- `OBX_GC=poison` overwrites freed memory, so a use of freed memory shows up as
  garbage rather than as a value that happens to still be there.
- `OBO_STATS=1` prints the exact live peak and total allocation at exit.

## 8. Compile errors

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
| `'<r>' may point to freed memory: something under what it points into was changed; point it again first` | a change freed what `<r>` pointed into. Borrow it again after the change. |
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
| `'<r>' is invalidated inside the loop; point it again before the end of each iteration` | a `continue` or the end of the body leaves `<r>` pointing into something a change in the loop freed; point it again before the loop goes round. |
| `'<r>' is invalidated on one path but not another; point it again on every path or none` | a change freed what `<r>` points into on one path only, typically a recursive call that changes on its innermost pass; point it again on every path, or none. |
| `the references in this loop change too often to be checked; point them afresh in the loop` | the loop's references did not settle within the checker's passes; point the references afresh inside the loop. |
| `take needs an optional variable, field or element holding an owned value` | `take` applies only to optional owned places. |
| `replace needs a variable, field or element holding an owned value` | `replace` applies only to owned places. |
| `drop needs a local variable holding an owned value` | `drop` applies only to local owned variables. |
| `handles name records and closed types, not <t>` | `^T` is only for records and closed types. |

## 9. Differences from obx

- There is no garbage collector. Memory is freed at scope exit, on reassignment,
  or by `drop`, and the compiler checks the freeing.
- References (`&T`) borrow owned values. They are checked by the
  compiler rather than by runtime collection.
- Handles (`^T`) are the way to write back-references and graphs. They are checked
  at run time.
- New built-ins: `take`, `drop`, `replace`, `handle`, `alive`.
- `gc()` does nothing.
- Strings are reference counted rather than collected.

## 10. Known holes and planned changes

**Assignment targets across calls (closed).** The target of an assignment is located
before the right-hand side is evaluated, so a call on the right that frees the target
would write into freed memory. The compiler rejects a field or element assignment
whose right-hand side calls a function that might free something:

```obo
// example: error: cannot assign to a field or element across a call
type Node = ref record { v: int; kids: []Node }

fn replaceKids(mut n: &Node): Node {        // the body changes nothing: the error is from taking n as mut
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
whole owned local names that local. A whole move of `b` (`var c = b`, through a call, into a field, `replace(b, ...)`, `take`) or an assignment over `b` makes that handle name no local, so it is checked like a handle without an origin: an index through it that moves or frees something is refused. A move of `b` inside a loop that a live `let` handle from `b` was declared outside of is refused (`cannot move 'b' inside a loop while handle 'h' made from it is in scope`). A temporary `handle(b)` names nothing afterwards. A handle without that
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

**Coarse through references.** A change made through a reference, such as `p.next = x`
where `p` is a reference, is not yet traced by its path: it invalidates every reference
into the whole root it reaches.

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
Two places join as follows: the same place stays the same; when one place lies under the other, the
join is the lower place, which every change to the upper one reaches too; any other two places join
to the whole root, which is the most conservative. A rule that kept only the common prefix of the two
places would miss a change at the lower one, and is not used.

A defer body may not re-point a reference declared before it (section 4). A loop is checked as well:
where a change in the loop may have freed what a reference points into, while the reference was not
pointed afresh on every path, a read of it on the next iteration is an error, unless the reference is
pointed afresh after the change and before it is read again. A reference whose binding at the end of
the body is not covered by the binding the body was checked with, or which is writable where the loop
starts and read-only where it starts again, is also an error. The rule is per path, as the bindings
are. Its precision is the same as before, and it is coarse in one place: a change made through a
reference is taken to reach the whole root the reference borrows from, not the place it names, so a
valid program can be rejected that changes one field through a reference and reads a reference to
another field. That is a false rejection, not an unsound acceptance. A narrower rule is future work.

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

**Known false positive (open).** The loop check does not follow each read to the re-point that
precedes it in the same iteration. This program is valid, since `k` is pointed afresh before each
read, but the loop is rejected because `k` is suspect where the next iteration starts and the loop
reads it:

```obo
// example: error: a recursive call in this loop writes through a handle
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

A loop that reads nothing it left suspect is accepted, and so is one that points a reference afresh
after each call before reading it on the next iteration. A loop that frees what a reference points
into and points the reference afresh before it reads it again is accepted too (`run/ref_bindings.obx`,
`loopCache`).

**Planned.** The language features listed as not yet done in the root
`README.md` also apply: methods and interfaces, lambdas, generic modules, `try`
with result types, multiple modules, value records, `inout` parameters, and debug
information.
