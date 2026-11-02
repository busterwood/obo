Part of the obo language reference. Index and core syntax: [LANGUAGE.md](../LANGUAGE.md).

## 5c. Function types and non-capturing lambdas

A **function type** is structural and is written `fn(ParamTypes): Ret`; an omitted return is
`void`. Parameters are owned (no marker) or read-only `&`; `mut` is not allowed. Two function
types are the same only when their parameter count, modes, types and return are the same. There
is no variance, subtyping or inference.

```obo
// example: compiles
type Item = ref record { n: int }
type Cmp = fn(int, int): int
type Pred = fn(&Item): bool
type Mapping = fn(fn(int): int): int
type DecFn = fn(decimal): decimal
fn main() { println(0) }
```

Function types are allowed as function/method/lambda **parameter** types, local `let`/`var`
variable types (with an initializer) and type aliases. A decimal parameter or return is
allowed (`fn(decimal): decimal`, `fn(): decimal`); a `&decimal` is refused because a decimal
cannot be borrowed. They are not allowed as returns, fields, globals, constants,
array/optional/handle/`Result` components, references, interface members or receivers. An
interface inside a function type is refused, including through a record or closed-type
field.

A **non-capturing lambda literal** is `fn (Params) [: Ret] { Body }`. It has no name and no
capture list. The body is an ordinary function body and must return a value on every path when
its return type is not `void`. Parameter names, `mut` rules and interface/return restrictions
are checked at the header.

```obo
// example: runs
// prints 3
// prints 6
// prints 8
fn apply(x: int, f: fn(int): int): int { return f(x) }

fn twice(x: int): int { return x * 2 }

fn main() {
    let inc = fn (x: int): int { return x + 1 }
    println(apply(2, inc))
    println(apply(3, fn (x: int): int { return x * 2 }))
    println(apply(4, fn (x: int): int { return twice(x) }))
}
```

A function value is **second-class**: it may be a parameter, a local (with an initializer) or
a call argument, and may be copied or assigned. It is never owned, returned, stored in a field,
array, global, constant, `Result`, optional, handle or interface, and cannot be compared with
another function value or with `nil`. A function value is called by its bare local/parameter
name with ordinary call syntax; the result ends the designator, so store a record/array result
before using `.` or `[]`. A function value has no fields or methods.

```obo
// example: runs
// prints 9
// prints 9
type Item = ref record { n: int }
fn use(f: fn(&Item): int, x: &Item): int { return f(x) }

fn main() {
    let get = fn (x: &Item): int { return x.n }
    let item = new Item{n: 9}
    println(use(get, &item))
    let add = fn (a: int, b: int): int { return a + b }
    println(add(4, 5))
}
```

The value representation and frame layout are in [ABI.md](../ABI.md): a
function value is one word, a call dispatches indirectly through its cell, and
a decimal-returning value call keeps the hidden decimal destination where a
direct call reads it.

**Effects.** Every function value is pure in the compiler's existing predicate: its hidden body
may allocate, write scalar globals, append to owned/global arrays, perform I/O builtins, take or
free its owned parameters and call pure functions or other function values. It may not free a
global, free owned state through a handle, call an impure function, call a forward-headed
function whose summary is not final, or call the function/method whose body is still being
compiled. An indirect call is a pure call: it does not invalidate the caller's references,
handles or globals, though its owned arguments are consumed and its owned/string/`Result`
result is the caller's ordinary result. Purity here is the existing compiler predicate, not a
referential-purity guarantee; ref-counted string writes follow their existing rules.

A lambda body cannot use an enclosing local or parameter by implicit capture. A referenced
outer local produces the capture-list diagnostic; capture lists are the Stage B feature
described in section 5d. A lambda cannot appear in a `while` condition,
a `defer` body, a global/constant initializer, or outside a function. A bare lambda is not a
statement and cannot be invoked directly.

```obo
// example: error: is a local of the enclosing function
fn main() {
    let x = 1
    let f = fn (): int { return x }
    println(f())
}
```

```obo
// example: error: a lambda must be pure
var xs: []int = new []int(0)
fn main() {
    let f = fn (): int { truncate(xs, 0); return 0 }
    println(f())
}
```

Stored function values, owned or mutable captures, string/interface captures and impure
callbacks are later work. Stage B capture lists (`fn [x](...)`) are implemented in section 5d;
interface handles `^I` remain later. The release record and seed
history are in [HISTORY.md](../../../HISTORY.md) and [FORK.md](../FORK.md).

## 5d. Capturing lambdas

A capturing lambda writes an explicit capture list: `fn [a, b](Params) [: Ret] { Body }`.
Each name must resolve to a local or parameter of the immediately enclosing function; an
uncaptured enclosing local is an error, and module globals/functions are used directly.
The list is resolved at the literal's source position and the cell is filled left to right.
There is no `mut` or `move` capture syntax.

A scalar `int`/`byte`/`bool`/`float` is captured by **value snapshot**. A float snapshot is
its one raw binary64 word, exactly as stored in a local, parameter or field; no conversion,
rounding or host-float path is involved. A **decimal** is captured by a 24-byte value
snapshot: all three value words (coefficient, exponent, display scale and sign) are copied
when the literal is evaluated, so a later write to the outer local cannot change the cell.
A record, closed type, array, optional or `Result`
is captured as a **read-only borrow of the whole local**. A `&T` reference copies its
read-only binding; a `^T` handle copies its token (a tracked `let` handle also holds its
origin owner); a function value copies its cell pointer. Float aliases and float parameters
follow the scalar snapshot rule, and a record/closed/array/optional/`Result` that holds a
float is allowed through the ordinary read-only borrow, so a float field, element or
`Result[float]` payload is read in the body. A float constant or global is used directly,
without a capture list, like any other global. A string or interface value is rejected, as
is a record/closed/array/optional/`Result` whose type holds an interface value, even when it
also holds a float; `mut`/`move` captures remain unsupported. A forward-declared type is
reported as incomplete, not as interface-bearing: a direct shell and a shell reached through
`?`/`[]`/`^`/`Result` or a record/closed field all report
`<T> is declared but not yet defined`.

The body may read fields/elements, narrow an optional with `if x != nil`, narrow an optional
or `Result` payload in an arm, match a closed type or a `Result` non-consumingly, and call
read-only methods. It may not write, move, drop, take, replace, truncate or append the
captured owned value, point a captured reference elsewhere, or call a `mut` method through
it; a captured scalar or decimal write is
`cannot change captured 'x': captures are read-only; pass a mutable parameter instead`.

```obo
// example: runs
// prints 8
fn apply(x: int, f: fn(int): int): int { return f(x) }
fn main() {
    let n = 7
    println(apply(1, fn [n](x: int): int { return x + n }))
}
```

```obo
// example: runs
// prints 3.75
fn apply(d: decimal, f: fn(decimal): decimal): decimal { return f(d) }
fn main() {
    let n = 2.5
    println(apply(1.25, fn [n](x: decimal): decimal { return x + n }))
}
```

```obo
// example: runs
// prints 6
type Item = ref record { n: int }
fn apply(x: int, f: fn(int): int): int { return f(x) }
fn main() {
    let item = new Item{n: 5}
    println(apply(1, fn [item](x: int): int { return x + item.n }))
}
```

A capturing literal may appear **only as a direct call argument**. It cannot be bound to a
`let`/`var`, assigned, returned, stored in a field/array/global/`Result`/optional, wrapped in
parentheses, or created in a `while` condition, a `defer` body or a global/constant
initializer. The callee may keep it in a local/parameter, copy it, forward it inside the call
chain, call it, or put it in a `defer`; it cannot store it beyond the call or return it. The
creator's frame cell is plain stack memory: no heap allocation, no destructor, no escape.

The hidden environment is **one read-only word**, a pointer to the cell in
the creator's frame; it is not one hidden parameter per capture and it is not a
source name (a user `env` is ordinary). The visible parameter list is the only
parameter list. The cell layout, slot sizes and byte offsets are in
[ABI.md](../ABI.md); capture lists add grammar and a frame cell, not a new
calling convention.

The creating call holds the capture binding across later arguments exactly like a read-only
reference argument. A later argument that moves/frees the captured root is refused; a `mut`
argument that overlaps a **deep** capture is refused. A whole-object capture is accepted even
by an impure callee; a deep capture needs a callee that does not write through a handle and
whose body is known; a handle-rooted capture needs a handle-safe callee; a global-rooted
capture needs a callee that cannot free that global. The check is made at the creating call;
later forwarding relies on the first callee's complete effect summary.

The lambda body itself is pure under the existing predicate: it may allocate, write scalar
globals, append to owned/global arrays, use I/O builtins and call pure functions or function
values, but may not free a global, free owned state through a handle, call an impure or
forward-headed function, or call the function/method whose body is still being compiled. A
receiving callee may still be impure for the per-kind capture rules above.

```obo
// example: error: a capturing lambda may only be passed directly as a call argument
fn apply(x: int, f: fn(int): int): int { return f(x) }
fn main() {
    let n = 1
    let f = fn [n](x: int): int { return x }
    println(apply(1, f))
}
```

```obo
// example: error: cannot change captured 'n'
fn apply(x: int, f: fn(int): int): int { return f(x) }
fn main() {
    let n = 1
    println(apply(1, fn [n](x: int): int { n = 2; return x }))
}
```
