Part of the obo language reference. Index and core syntax: [LANGUAGE.md](../LANGUAGE.md).

## 5a. Methods

A method is a function whose first, hidden parameter is a **receiver**. It is
declared at top level with the receiver in parentheses after `fn`:

```text
fn (r: T) name(params) [: Ret]        // owned receiver
fn (r: &T) name(params) [: Ret]       // read-only borrow
fn (mut r: &T) name(params) [: Ret]   // mutable borrow
```

Exactly one receiver, with a name. `fn () name(...)` (no receiver), a second
receiver name, and `fn (r: &T) (...)` (no method name) are errors. `mut` goes
before the receiver's name, as for parameters; `mut r: T` and `&mut T` reuse the
parameter errors. A receiver literally named `mut` cannot be spelled: the
optional mode word is consumed first (the same lexical limitation parameters
have).

The receiver type must be a **record or closed type already defined in the same
module**. Aliases to it are transparent, but an imported type, a variant, a
built-in or derived type (`int`, `string`, `[]T`, `?T`, `^T`, `Result[T]`), and
an incomplete forward type are refused. Methods are declared after the type's
definition; a method needs a forward heading to be called before its body, like a
function.

```obo
// example: runs
// prints 2 b
// prints 2
type Builder = ref record { items: []string; n: int }
type Frozen = ref record { items: []string; n: int }

fn make(): Builder { return new Builder{items: new []string(0), n: 0} }
fn (b: &Builder) count(): int { return b.n }
fn (b: &Builder) at(i: int): string { return b.items[i] }
fn (mut b: &Builder) add(s: string) {
    append(b.items, s)
    b.n += 1
}
fn (b: Builder) freeze(): Frozen {
    let items = replace(b.items, new []string(0))
    return new Frozen{items: items, n: b.n}
}

fn main() {
    let b = make()
    b.add("a")
    b.add("b")
    println(b.count(), " ", b.at(1))
    let f = b.freeze()               // b is moved into the method and freed
    println(f.n)
}
```

Inside the body the receiver is an ordinary parameter:
`r: &T` is read-only (`r.f = v`, `append(r.a, x)`, and `mut` method calls on it
are refused); `mut r: &T` may change fields, append, and free below the receiver;
`r: T` is owned and is freed at return unless returned, passed on or dropped. A
field cannot be moved out of an owned receiver: use `replace(r.f, new ...)` or
return `r` itself.

A method body has module-level access to every field of its receiver type, so a
`mut &T` method can maintain private state and a `&T` method can read it. A
public method is the external surface for that state. An external call to a
foreign **private field** used as a method (`x.privateName()`) does not leak the
field: it reports the no-public-field-or-method message (section 10). A public
field on a mixed record, and any field called as a method inside its own module,
keeps the ordinary `has a field ..., not a method` message.

### Calls

A call is written `place.name(args...)`; the receiver is the designator before
`.name(`. It may be a local or parameter, a global (`alias::g` too), a field, a
constant- or variable-index element, a reference through any of these, or a value
reached through a handle:

```obo
// example: runs
// prints 3
// prints 8
type Item = ref record { v: int }
type Bag = ref record { first: Item; items: []Item }

fn (i: &Item) get(): int { return i.v }
fn (mut i: &Item) bump() { i.v += 1 }

fn main() {
    let bag = new Bag{first: new Item{v: 3}, items: new []Item(0)}
    append(bag.items, new Item{v: 6})
    println(bag.first.get())
    bag.items[0].bump()
    println(bag.items[0].get() + 1)
}
```

Dispatch is **static**, by the receiver's declared type: there is no dynamic
dispatch or vtable, and no method value. `b.m` where `m` is a method is the
error `method '<T>::<m>' cannot be used as a value: call it with '(' after its
name`. Two types may have methods with the same name; a method and a top-level
function may share a name (methods are a separate namespace, while a method and
a field/variant of its own receiver type may not collide).

A variant-typed value resolves the methods of its closed type (one parent step):
a `Shape`-typed local and a `Circle`-typed value with static type `Circle` both
call `Shape::area`:

```obo
// example: runs
// prints 12
// prints 27
type Shape = closed { Circle { r: int } Rect { w: int; h: int } }

fn (s: &Shape) area(): int {
    match s {
        Circle { return 3 * s.r * s.r }
        Rect { return s.w * s.h }
    }
}

fn main() {
    let s: Shape = new Circle{r: 2}
    println(s.area())
    let c = new Circle{r: 3}
    println(c.area())
}
```

A method call **ends the designator**: `b.m().n` and `b.m()[0]` are syntax
errors at the enclosing statement (`expected ';' or newline, found .`), not
store-first errors. Store the result in a local to continue. A function-call
result keeps its existing rules: `make().m()` with an owned record, array,
optional or `Result` result is the store-first error before the member name; a
handle-valued result (`mk().m()`) follows the handle rules.

### Ownership at the call

The receiver is argument 0 and is converted and checked exactly like an argument,
before the explicit arguments run: it is moved for `r: T`, borrowed for
`&`/`mut`, held while the later arguments run, and included in the aliasing and
argument-order checks. So an owned receiver consumes a whole local; a field,
element, global, reference or a whole handle dereference cannot be moved
(`cannot move out of ...`), and an owned receiver that is the **whole object a
handle names** is refused explicitly:

```obo
// example: error: cannot call a method that takes its receiver by value through a handle
type Node = ref record { v: int }

fn (n: Node) take(): int { return n.v }

fn main() {
    let n = new Node{v: 1}
    let h = handle(n)
    println(h.take())
}
```

A field or element reached through a handle (`h.child.m()`) is a field/element
move and takes that existing error. A `mut` receiver through a read-only binding
reports `the receiver of method '<T>::<m>' needs a mut reference, but this one is
read-only`. A method call on an optional needs the usual `!= nil` narrowing
first; on a built-in or derived type it is `<t> has no methods`.

### Modules, exports and symbol files

Methods are private unless declared `pub`, and `pub` is sticky across a forward
heading and its body. An exported method requires its receiver type to be
exported (`exported '<T>::<m>' uses private type '<T>'; mark '<T>' pub too`).
Importers call methods on values of the exported type: `let b = lib::make();
b.push("x")`. A method name is never qualified as `lib::m`; only the receiver
may be (`lib::g.m()`). A private method of an imported type is invisible in a
full build and in `--check` alike, with the same
`module '<m>' has no exported member '<T>::<m>' (only 'pub' declarations are
visible)`. Transitive access is allowed: if module A's exported function returns
a type defined in module L, a module that imports only A can call L's exported
methods on the result.

The `.sym` carries an exported method as a source-like function whose first
parameter is the receiver (always `p0`), in a method group between the globals
and the functions; the receiver's mode, type, name and summary are interface
data, while parameter names are not. `--check` reconstructs methods from the
symbol file and checks calls against them, including a source-less dependency.
A method named `main` is allowed and is not the program entry: only the root's
plain `fn main` is looked up.

```obo
// example: modules check
// file: main.obx
module main
import "lib.obx"
fn main() {
    let b = lib::make()
    println(b.count())
}
// file: lib.obx
module lib
pub type Builder = ref record { n: int }
pub fn make(): Builder { return new Builder{n: 1} }
pub fn (b: &Builder) count(): int { return b.n }
// prints 1
```

The method diagnostics are in section 10; `T::m` (and `lib::T::m` for another
module) is the spelling throughout.
