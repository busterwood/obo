Part of the obo language reference. Index and core syntax: [LANGUAGE.md](../LANGUAGE.md).

## 4. References

`&T` is the one reference type. A parameter declared `mut p: &T` may change the object
it points to; one without `mut` may not.
A reference borrows an owned value; it does not own it. A reference stays valid
while nothing it may point into has been changed.

- References are for parameters and local variables only. They cannot be stored
  in fields, elements or globals, and cannot be returned.
- A reference to an interface value (`&I`, `mut &I`) follows exactly these rules.
  In particular it is a parameter or a local, never a field, element, global or
  return type; the compiler refuses those positions with the same messages as
  `&T`, because the spelling in the type position is what it rejects.
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
