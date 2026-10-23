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
| `'<x>' may be nil: check it with '!= nil' first` | check an optional before using its fields. |
| `cannot pass a reference that borrows through a handle to <f>: it might free what the handle names` | a reference borrowed through a handle may go only to a call that cannot free anything; pass the handle instead, or make the call pure. |
| `cannot change what '<r>' points into while a for loop walks it` | a change, or a call that might free, while a loop walks `<r>`; make it after the loop. |
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

fn replaceKids(mut n: &Node): Node {        // changes the array the target lives in
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

**Coarse through references.** A change made through a reference, such as `p.next = x`
where `p` is a reference, is not yet traced by its path: it invalidates every reference
into the whole root it reaches.

**Handle changes and owned references (known hole).** A change through a handle, such as
`h.items = ...` or `handle(p).items = ...` (a handle temporary, which has no name), or a call
that makes one, invalidates borrows through handles (section 4),
but not a borrow taken from an owned variable, such as `&bag.items[0]`. The program below
compiles and then reads freed memory. The planned fix: a change through a handle, and a
call that might make one, invalidates every reference.

```obo
// example: compiles
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
    println(first.v)             // reads freed memory: no compile error
}
```

**Planned.** The language features listed as not yet done in the root
`README.md` also apply: methods and interfaces, lambdas, generic modules, `try`
with result types, multiple modules, value records, `inout` parameters, and debug
information.
