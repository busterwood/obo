Part of the obo language reference. Index and core syntax: [LANGUAGE.md](../LANGUAGE.md).

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
  Use `take` on an optional one instead. A private field of an imported record
  cannot be the operand of `take`, `replace` or a move at all: field resolution
  reports the no-public-field error before the move is considered. The whole
  record remains movable, passable, returnable, storable and droppable.
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
