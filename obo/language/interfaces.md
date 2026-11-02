Part of the obo language reference. Index and core syntax: [LANGUAGE.md](../LANGUAGE.md).

## 5b. Interfaces

An interface is a named set of methods that a record or closed type can implement. A value of interface type is
either an **owned box** (`I`, `?I`) or a **borrowed pair** (`&I`, `mut &I`, `&?I`); an interface is never a value
of its own kind with fields or identity. Interfaces arrived in `0.8`; additive
builtin/runtime extensions use patch releases such as the historical `0.8.1`.
Lambdas are `0.9`, numeric floats are `0.10`/format 6, Stage B capture lists are
`0.11`/format 6, the impure member tier is `0.17`/format 10, decimal members are
`0.18`/format 11, decimal function values and captures are `0.19`/format 12,
and handles to interfaces (`^I`) are a later phase.

### Declarations and implementations

```text
type Shape = interface {
    pure fn area(): int
    mut fn grow(k: int)
    pure mut fn bump(): int
}

type Square = ref record { s: int }
fn (q: &Square) area(): int { return q.s * q.s }
fn (mut q: &Square) grow(k: int) { q.s += k }
fn (mut q: &Square) bump(): int { q.s += 1; return q.s }
impl Shape for Square
```

- `type I = interface { ... }` declares an interface; it needs at least one member, is never forward-declared,
  and a member is `[pure | impure] [mut] fn name(params)[: T]`. `pure`, `impure` and `mut` come before `fn`
  (`pure` and `impure` are mutually exclusive); `pub` is not allowed on a member (mark the interface type `pub`).
  A member may take or return a decimal (0.18), and a decimal is never a receiver.
  A member cannot take or return a function value.
- A member's receiver is parameter 0: borrowed for `fn`, mutable for `mut fn`; it is not written in the member.
  Explicit parameters are owned `p: T`, borrowed `p: &T`, or mutable borrowed `mut p: &T`. `mut p: T` is the
  existing `mut needs a reference parameter` error. Interface types may appear as parameter and return types.
- `impl I for T` is a conformance declaration with no body. `T` is a record or closed type defined in this
  module, by its defining name, not a variant and not an alias; `I` is an interface (qualified `M::I` across
  modules). Interfaces, implementations and methods are declared before use. A type may implement several
  interfaces, and two interfaces may share member names.
- The implementation must match every member exactly: receiver mode (`mut fn` needs `mut &T`, `fn` needs `&T`),
  argument count, argument types, argument modes, and return type. A `pure` member needs a method that is pure.
  A plain or `mut` member (the **local** tier) needs a method that writes no owned value through a handle, frees
  no global, does not call an uncompiled function, and frees nothing below a parameter the member does not
  declare `mut`. An `impure` member may be implemented by any method, including a pure or plain one: its summary
  is the maximum, so every caller is conservatively invalidated. A method must be defined before its
  implementation.
- A `pub impl` is visible to importers; a private implementation is not. An imported private implementation
  is reported as `module 'M' has no exported implementation of 'I' for 'T' (only 'pub' implementations are
  visible)`.

### Owned values, moves and conversions

`I` is a box holding the object pointer and its vtable; `?I` is nil or a box. Converting a record or closed
value into `I` at a typed destination allocates a box and **moves** the value into it. A variant uses its
closed parent's implementation and vtable.

```obo
// example: runs
// prints 25
// prints 13
// prints 25
type Shape = interface {
    pure fn area(): int
    mut fn grow(k: int)
}
type Square = ref record { s: int }
fn (q: &Square) area(): int { return q.s * q.s }
fn (mut q: &Square) grow(k: int) { q.s += k }
impl Shape for Square

fn total(xs: &[]Shape): int {
    var sum = 0
    for s in xs { sum += s.area() }
    return sum
}

fn main() {
    let a: Shape = new Square{s: 3}
    a.grow(2)
    println(a.area())                       // 25
    let xs = new []Shape(0)
    append(xs, new Square{s: 2})
    append(xs, new Square{s: 3})
    println(total(&xs))                     // 13
    let o: ?Shape = new Square{s: 5}
    if o != nil { println(o.area()) }       // 25
}
```

The typed destinations are `let`/`var` with a type, assignment to a typed place, a call argument, an
`append` element, a `replace` value, a constructor field, `return`, and an `ok` payload that already has the
interface type. Inference never creates an interface: `let e = &c` stays `&Circle` and `let e = c` stays `Circle`.

Rules and refusals:

- `T` or a variant that implements `I` moves into a box. A borrowed `T` cannot become owned: `cannot use a
  borrowed 'T' as owned interface 'I': move the value in, or take the interface as '&I'`.
- An owned `I` moves as a box, with no allocation; `I -> ?I` widens. An `&I` only borrows; moving out of it is
  `cannot move out of a reference: a reference only borrows`.
- `?T -> ?I` and `?T -> I` are not conversions in this version: `cannot use ?T as interface 'I' (it may be nil:
  take it into a local, return if nil, then convert the local)`. Narrow a taken local and move that local:

  ```obo
  // example: runs
  // prints 9
  type Shape = interface { pure fn area(): int }
  type Square = ref record { s: int }
  fn (q: &Square) area(): int { return q.s * q.s }
  impl Shape for Square
  type Holder = ref record { o: ?Square }

  fn main() {
      let h = new Holder{o: new Square{s: 3}}
      let t = take(h.o)
      if t == nil { return }
      let s: Shape = t
      println(s.area())
  }
  ```

  In an `if`/`else`, consume the local in both arms: `if t != nil { let s: Shape = t; append(out, s) } else {
  drop(t) }`. A field cannot be narrowed directly; take it into a local first.
- Another interface does not convert: `cannot use interface 'I2' as 'I': interface values do not convert`.
  Arrays, strings, scalars and `Result[T]` are not records: `cannot use <t> as interface 'I'`. An `ok(new T)`
  where `Result[I]` is wanted is refused with the store-first hint. A `^T` is not owned: `cannot use ^T as
  interface 'I'`.
- A conversion needs a visible implementation. A local missing one is `type 'T' does not implement interface
  'I'`; a foreign one is `module 'M' has no exported implementation of 'I' for 'T' (only 'pub' implementations
  are visible)`.

Owned interface values follow the record ownership rules without exception. `let e = d` moves the box; a
by-value parameter consumes it; assigning an `I` field frees the old box and its object; scope exit, `drop`,
`truncate`, array/`Result` teardown and global teardown free boxes. `take` works only on optionals and returns
an optional; `replace(place, new)` moves the old value out of a non-optional field, element or global and
stores the new one. A plain `let` cannot move out of a field or element.

### Borrowed interfaces

`&I` is a pointer to a `{obj, vtable}` pair. It is second-class: parameters, locals and `for` walk variables
only; never stored in a field, element or global, never returned. Every `&I` local owns its own pair slot, and
`let`, `var` and re-point copy both words into it (the B1 rule), so a value saved from an earlier loop pass
does not follow a later conversion. A concrete `&c` argument uses a frame cell for the call; an owned `I` place
is borrowed without a cell by pointing at its box.

```obo
// example: runs
// prints 16 16
// prints 25
type Shape = interface {
    pure fn area(): int
    mut fn grow(k: int)
}
type Square = ref record { s: int }
fn (q: &Square) area(): int { return q.s * q.s }
fn (mut q: &Square) grow(k: int) { q.s += k }
impl Shape for Square

fn describe(d: &Shape): int { return d.area() }
fn bump(mut d: &Shape): int {
    d.grow(1)
    return d.area()
}

fn main() {
    let c = new Square{s: 4}
    let d: &Shape = &c
    println(d.area(), " ", describe(&c))          // 16 16
    println(bump(d))                              // 25
}
```

An `&I` is a reference local, so the reference rules apply: a visible change that may free the target
invalidates it, a loop must re-point it on every path or none, and it cannot be pointed elsewhere while a
`defer` uses it. `&?I` is only a parameter or a walk variable (explicit and inferred locals are refused:
`an optional interface reference can only be a parameter or a walk variable; narrow it to a '&I' local`).
A non-nil `&?I` narrows to `&I`; take a copied `&I` local with `let d: &I = o`.

### Calls, effects and globals

Any designator of type `I`, `&I` or a narrowed `&?I` can be a receiver: a local, parameter, field, element,
global, `Result` value, or walk variable. A call through an interface is a direct call on the box's object
(argument 0), with the object and vtable loaded before the explicit arguments. A `mut` member needs a writable
receiver. The caller uses the member's synthetic summary, not the implementation's:

- `pure` means the member frees nothing and writes no owned value through a handle. It may still write scalars,
  globals and its receiver, so `pure` does **not** forbid those writes.
- A plain or `mut` member may free only below its own `mut` parameters (the receiver for `mut fn`), so a call
  invalidates references below those places. It never frees a global or writes through a handle.
- An `impure` member has the maximum summary: it may free anything and write through a handle. A call through
  it invalidates every live reference and handle, and a reference borrowed through a handle cannot be read
  afterwards. The calling function inherits that maximum, so it is itself impure for later tier checks.

A reference borrowed through a handle can be passed only to a member that cannot free; otherwise the message
is `cannot pass a reference that borrows through a handle to interface method 'I::m': it might free what the
handle names`. A later argument that may free the receiver is refused: `argument N of interface method 'I::m'
may free what the receiver points into, and runs after it; compute it before the call`.

Global initializers may contain owned conversions, borrowing an owned `I` global, and moving an owned `I` value
into a global. A concrete-to-`&I` conversion that would need a cell is refused: `cannot create a borrowed
interface value in a global initializer: pass the concrete value to a function that makes it`. An interface
method call anywhere in a global initializer is refused: `an interface method cannot be called in a global
initializer; call a function that does it, as in var G = initG()`.

### Equality, handles and limits

Interface values cannot be compared: `interface values cannot be compared; test an optional with '== nil' or
'!= nil'`. `?I == nil` and `?I != nil` are allowed and narrow; `I == nil` keeps `cannot compare I with nil`.
`handle` still names records and closed types only in this phase: `handles name records and closed types, not I`
(and `not ?I` for an optional). `^I`, `alive` of an interface and a `pure mut` write through a handle are later
work.

Not in this phase: interface-to-interface conversion, structural typing, variance, superinterfaces, downcasts,
type tests, owned-receiver members, method values, `?T -> ?I`, and `^I`. An interface
member cannot take or return a function value; the lambda firewall is part of the lambda phase. An interface
value cannot be `new`-ed, used as a constant, `match`ed, or printed: `new I` is `'I' is not a record type`,
`new []I(n > 0)` starts as nil (use `[]?I` or start empty), and a missing initializer is the existing nil error.
