# The obo language

obo is a small language in the Wirth tradition, with C-style braces. It has no
garbage collector. Every owned value has exactly one owner, which frees it at a
known point, and the compiler rejects any program that could read freed memory.

This is the language reference for the compiler in `obo/src/obo.obo`. It is
split into a core guide (this file) and topic chapters under
[`obo/language/`](language/README.md). Section numbers are stable: the chapters
keep the numbers they had when the reference was one file, and this file adds
section 0. Rules that are planned rather than implemented are marked as such;
[section 12](language/holes.md) lists the known holes.

The examples are checked against the compiler. A block marked
`// example: runs` is a complete program that must compile and exit 0, and each
`// prints` line gives one line of expected output. `// exit: N` gives an
expected exit code. A block marked `// example: error: TEXT` must be rejected
with a message containing TEXT. A block marked `// example: compiles` must
compile, but is a known problem rather than a model of good code.

## Where to read next

| Chapter | File | Contents |
|---|---|---|
| 0 | this file | source form, literals, operators, declarations, statements, `match` |
| 1 | [types.md](language/types.md) | the type table, records and fields, floats, decimals |
| 2–3 | [memory.md](language/memory.md) | ownership, moves and the lifetime rules |
| 4 | [references.md](language/references.md) | borrowed `&T`, invalidation, the one ownership rule |
| 5 | [handles.md](language/handles.md) | checked `^T` handles for graphs and back-references |
| 5a | [methods.md](language/methods.md) | receivers, method syntax, dispatch and ownership at the call |
| 5b | [interfaces.md](language/interfaces.md) | first-class interfaces, tiers, dynamic dispatch |
| 5c–5d | [lambdas.md](language/lambdas.md) | function types, lambdas and captures |
| 6 | [modules.md](language/modules.md) | modules, imports, exports, `.sym` files and `--check` |
| 7 | [results.md](language/results.md) | `Result[T]`, error values and `try` |
| 8 | [builtins.md](language/builtins.md) | built-in functions, I/O, templates and metadata |
| 9 | [runtime.md](language/runtime.md) | runtime checks and exit codes |
| 10 | [errors.md](language/errors.md) | compile-error reference |
| 11 | [obx.md](language/obx.md) | differences from the obx predecessor |
| 12 | [holes.md](language/holes.md) | known holes and planned changes |

## 0. The core language

This section is enough to read and write ordinary obo programs. The chapters
above give the exact ownership rules and the less common features.

### 0.1 Source form

A source file is a sequence of bytes, usually UTF-8. Identifiers are ASCII: a
letter or `_`, followed by letters, digits or `_`. Outside string and character
literals and comments, source text must be ASCII. Columns count bytes, not
characters.

A file starts with exactly one module declaration, and the file's basename must
match it: `module main` lives in `main.obx`. Imports come after the module line
and before any declaration (section 6).

Whitespace separates tokens. Comments start with `//` and run to the end of the
line; there are no block comments, and a `//` inside a string is text.

A newline ends a statement after an identifier, a numeric, character or string
literal, `true`, `false`, `nil`, `return`, `break`, `continue`, `)`, `]`, `}` or
a template literal. A newline after an operator or `,` does not end the
statement, so a long expression may be split across lines. A semicolon is
optional wherever a newline already ends a statement, and is required to put two
statements on one line:

```obo
// example: runs
// prints 7
// prints 8
module main

fn main() {
    let a = 3
    let b = 4; println(a + b)
    println(a +
        b + 1)
}
```

### 0.2 Literals

**Integers** are decimal or hexadecimal. `_` is ignored between digits, so
`1_000_000` and `0xFFFF_FFFF` read as written. A decimal literal must fit a
signed 64-bit `int`; a hexadecimal literal takes the low 64 bits, so
`0xFFFF_FFFF_FFFF_FFFF` is `-1`. A decimal literal that is too large is a
compile error.

**Characters** are one byte in single quotes with the escapes `\n`, `\t`, `\r`,
`\\`, `\"`, `\'` and `\0`; a non-ASCII byte is rejected. Character literals have
type `byte`, and `byte` widens to `int` in arithmetic.

**Strings** are double-quoted bytes with the same escapes. An unrecognised
escape keeps the character after the backslash (`"\q"` is `"q"`), but an
unrecognised escape in a character literal is an error. Strings are immutable
and reference counted; section 1 covers indexing and concatenation.

**Booleans** are `true` and `false`. The **nil** literal is `nil`; it may only be
used where an optional or reference type is expected, and only compared with
`==` and `!=`.

**Floats** are written with a fractional part or exponent and an `f` or `F`
suffix: `1f`, `1.5f`, `1.5e3F`. The suffix is required, because a bare
fractional literal is a decimal. Float literals convert exactly to binary64; a
finite literal that would round to infinity is a compile error.

**Decimals** are bare fractional or exponent literals: `12.34`, `1.2300`,
`0.0011e3`. There is no `m` or `d` suffix. An integer literal is always `int`,
never `decimal`, and the written fractional digits are the display scale. A
literal needing more than 38 significant digits, or an exponent outside the
decimal range, is a compile error.

```obo
// example: runs
// prints 1000000 -1
// prints 65 1
// prints hi 2.5 12.3400
module main

fn main() {
    println(1_000_000, " ", 0xFFFF_FFFF_FFFF_FFFF)
    let c = 'A'
    println(c, " ", c - 'A' + 1)
    let s = "hi"
    println(s, " ", 1.5f + 1f, " ", 12.3400)
}
```

String literals concatenate with `+`; there is no implicit conversion from
numbers, so write `string(n)`. A string indexes to a `byte` with `s[i]`, and
`len(s)` is its byte length. Sections 1 and 8 cover the rest.

### 0.3 Operators and precedence

Operators bind in the usual Go-like order, from tightest to loosest:

| Level | Operators | Notes |
|---|---|---|
| unary | `!` `-` `~` `&` | `!` needs `bool`; `-`/`~` need `int` (and `-` also floats/decimals); `&place` borrows |
| 2 | `*` `/` `%` `<<` `>>` `&` | `&` here is bitwise and |
| 3 | `+` `-` `|` `^` | `+` also concatenates strings |
| 4 | `<` `<=` `>` `>=` `==` `!=` | |
| 5 | `&&` | short-circuit, `bool` only |
| 6 | `||` | short-circuit, `bool` only |

`&` is unary borrow or binary bitwise-and depending on position. `&&`, `||` and
`!` work only on `bool`; `&&` and `||` evaluate the right operand only when
needed. There is no logical `&`/`|` on bools.

Integer arithmetic is 64-bit two's complement and wraps on overflow. `/`
truncates toward zero and `%` takes the sign of the dividend; a zero divisor
traps at run time (SIGFPE, section 9). Shift counts are taken modulo 64, and
`>>` is arithmetic. `~` is bitwise complement.

`+ - * %` also work on `decimal` (exact, with traps on inexactness), and
`+ - * /` on `float`; `%` is not defined for floats, and decimal `/` is not an
operator at all (use `decimal::div`). Mixing `int`, `float` and `decimal` in one
operation or comparison is an error; convert explicitly with `float(i)` or
`decimal(i)`. There is no float-to-int, decimal-to-int or decimal-to-float
conversion in v1: use `int(x)`/`byte(x)` only on integers, `decimal::toInt` for a
decimal, and `parseFloat` for text.

Comparisons return `bool`. Strings compare with `==` and `!=` only; references
and optionals compare for identity with `==` and `!=` (a handle is tested with
`alive(h)`); interfaces and function values cannot be compared.

Compound assignment `+=`, `-=` and `*=` works on `int`, and `+=`, `-=`, `*=`
and `%=` on `decimal`. `/=` is not supported anywhere. Strings have no `+=`;
write `s = s + t`.

### 0.4 Declarations and scope

A module body contains `import` lines followed by declarations, each optionally
marked `pub` for export (section 6):

```obo
// example: runs
// prints 3
// prints 6
module main

const LIMIT = 2

type Pair = ref record { first, second: int }

var counter: int = 1

fn add(a: int, b: int): int { return a + b }

fn main() {
    var total: int
    total = add(LIMIT, counter)
    let p = new Pair{first: total, second: total}
    println(p.first)
    println(p.first + p.second)
}
```

- `const NAME = expr` requires a value known at compile time. Arithmetic on
  constants is folded (`const N = 2 + 3` is `5`); a decimal constant must be a
  literal or a unary-minus literal.
- `type Name = ref record { ... }` declares a record; `type Name = closed { ... }`
  declares a variant type; `type Name = Other` is an alias; `type Name`
  forward-declares a record that is defined later (section 1).
- `var name = expr`, `var name: T = expr`, `var name: T` and `let name = expr`
  declare locals or globals. `var` is assignable; `let` is bound once. `let`
  always needs a value. `var` without a value starts at zero (the empty string
  for `string`) and `nil` for optionals; a non-optional record, closed value,
  interface, handle, function value or array always needs a value.
- A function is `fn name(params): T { ... }`; omit `: T` for a function that
  returns nothing. A declaration with no body, `fn name(params): T` followed by
  no `{`, is a forward heading for a function defined later in the file.
- Parameters are `name: T` (moved or copied in), `name: &T` (read-only borrow)
  and `mut name: &T` (mutable borrow). There are no default, named or variadic
  parameters, and no overloading.
- Local `var`/`let` declarations and parameters shadow an outer name; a block is
  a new scope, and `defer` is block-scoped.
- The program root must define `fn main()`; an imported module may not.

Methods and interfaces add declaration forms of their own; see sections 5a
and 5b.

### 0.5 Statements

A function body is a block `{ ... }`. The statements are:

- **Expression statement.** A call, or a `try` expression. A bare value is an
  error ("this expression does nothing"), because it would be discarded.
- **Assignment.** `place = expr`, or a compound form from section 0.3. A place
  is a local, parameter, field, array element or a place reached through `&T` or
  `^T`; assignment moves an owned value and frees the old one.
- **`if cond { ... } else if cond { ... } else { ... }`.** The condition must be
  `bool`; the braces are required; an `if` is a statement, not an expression.
- **`while cond { ... }`.** The condition is checked before each iteration and
  must be `bool`; `while true` is the usual infinite loop.
- **`for i in start..end { ... }`.** `start` and `end` are `int` expressions,
  evaluated once. The range is half-open: `0..3` runs for `0`, `1`, `2`. The
  loop variable is a fresh `let` binding on each iteration.
- **`for x in array { ... }`.** Walks a local array or an array parameter. An
  owned element is borrowed for the iteration, not moved out; `x` is a read-only
  binding of the element, so `x.field = v` changes the element but `x = ...`
  does not compile. The array must be a local or parameter; write
  `let items = &p.items` to borrow a field first.
- **`break` and `continue`.** Leave or continue the innermost loop. Both are
  errors outside a loop, and each runs the defers of the blocks it leaves.
- **`return expr`** (or `return` in a void function). Runs the defers of the
  function's blocks and returns. A value must be returned on every path. There
  is no `return` from inside a `defer`.
- **`defer statement` or `defer { ... }`.** Runs the statement when its block is
  left, in reverse order for several defers, including on `return`, `break`,
  `continue` and a propagated `try` error. A defer body may not return.
- **`match`.** Section 0.6.
- **A nested block `{ ... }`.** Introduces a scope and runs its defers at exit.

```obo
// example: runs
// prints 0 1 2
// prints 4
// prints 10
module main

fn main() {
    for i in 0..3 {
        if i > 0 { print(" ") }
        print(i)
    }
    println("")

    var n = 0
    while n < 10 {
        n += 2
        if n == 6 { continue }
        if n == 8 { break }
    }
    println(n / 2)

    let xs = new []int(0)
    for i in 0..4 { append(xs, i * i) }
    var total = 0
    for x in xs { total += x }
    println(total - 4)
}
```

The braces around `if`, `while`, `for` and `match` bodies are mandatory. There
is no `switch`, `do`, `goto` or labelled loop; use `match`, `while` and
`break`/`continue`.

### 0.6 Match

`match` inspects an `int`, a `byte`, a `closed` type or a `Result`. The subject
of a closed-type or `Result` match must be a whole local variable or parameter,
not an arbitrary expression; an `int` match may use any expression.

**Integer and byte matches** list constant expressions, separated by commas,
one arm per list. Duplicate values are errors, and `else` is required and must
be last:

```obo
// example: runs
// prints low
// prints other
module main

fn label(n: int) {
    match n {
        0, 1, 2 { println("low") }
        3 { println("three") }
        else { println("other") }
    }
}

fn main() {
    label(1)
    label(9)
}
```

**Closed-type matches** name variants without their module qualifier
(`Circle { ... }`, not `Shape::Circle`); every variant must be covered or an
`else` arm must be present, and `else` is last. The subject is narrowed to the
variant inside the arm, so its fields are read directly:

```obo
// example: runs
// prints 12
// prints 5
module main

type Shape = closed {
    Circle { r: int }
    Rect { w, h: int }
}

fn area(s: Shape): int {
    match s {
        Circle { return 3 * s.r * s.r }
        Rect { return s.w * s.h }
    }
}

fn main() {
    let a: Shape = new Circle{r: 2}
    let b: Shape = new Rect{w: 5, h: 1}
    println(area(a))
    println(area(b))
}
```

**Result matches** are covered in [results.md](language/results.md): the
non-consuming form narrows to `ok`/`err` (both arms required, no `else`), and
the consuming form binds `ok(v)` / `err(m, c)` and moves the payload out.

An arm's body is a block. A match is a statement, not an expression: an arm that
needs to produce a value assigns it or returns.

### 0.7 Expressions and places

An expression is made of literals, names, calls, operators and these forms:

- **Designator and place.** A designator is a name, a field access `p.field`, an
  index `a[i]`, or the same reached through `&T` or `^T`. Those are places and
  may be assigned; a call result is a value, not a place.
- **Call.** `f(a, b)`, `p.method(a)`, `obj.member(a)` for interface values.
  Arguments are evaluated left to right; there are no named or default
  arguments. A call that might free what a reference points into is checked
  against every live reference (section 4).
- **`new T{field: value, ...}`** constructs a record or a closed variant. Every
  non-optional reference or array field must be supplied; omitted scalars and
  optionals start at zero/nil. `new []T(n)` makes an array of `n` zero
  elements (`n >= 0`, checked at run time).
- **`ok(v)` and `err(message, code)`** construct a `Result`; `try e` propagates
  a failure (section 7).
- **`take(o)`** moves the value out of an optional and leaves `nil`;
  **`drop(x)`** frees an owned value early; **`replace(place, value)`** replaces
  and returns the old value (section 8).
- **`&place`** borrows an owned record, closed value or array instead of moving
  it; the handle type `^T` is created with `handle(place)` (section 5).
- **`fn [captures](params): T { ... }`** is a lambda (section 5c).
- **`try`** may only be the value of a `let`/`var`/assignment or a statement,
  and only in a function returning a `Result` (section 7).

Function values, interfaces and methods are expressions too; their syntax is in
sections 5a–5d.

### 0.8 Optionals and nil

An optional type `?T` holds a `T` or `nil`. An optional is the only place `nil`
is allowed, and it must be checked before the payload is used: field access,
indexing, a method call and a by-value use of a `?T` are errors until a check
narrows it. The check may be an `if`/`while` condition, a `&&` operand, or an
early `return`/`break`/`continue` after `== nil`:

```obo
// example: runs
// prints 6
module main

type Node = ref record { value: int; next: ?Node }

fn value(o: ?Node): int {
    if o == nil { return 0 }
    return o.value
}

fn main() {
    let n = new Node{value: 6, next: nil}
    println(value(n))
}
```

Narrowing is invalidated by assigning to the optional, and a defer body sees no
narrowing from the code around it. `take(o)` moves the payload out of an
optional and leaves `nil`; use it when the value itself is needed. A non-optional
`T` can never be `nil`. Optional interfaces narrow to `&I` in their own way; see
[interfaces.md](language/interfaces.md).

### 0.9 Type summary

The type table, record and closed-type declarations, private fields and the
float and decimal representations are in [section 1](language/types.md). The
short version:

| Type | Meaning | Owned |
|---|---|---|
| `int`, `byte`, `bool` | numbers and booleans, copied by value | no |
| `float` | IEEE 754 binary64, copied by value | no |
| `decimal` | exact 38-digit decimal, 24 bytes, copied by value | no |
| `string` | immutable bytes | yes (reference counted) |
| `[]T` | growable array of `T` | yes |
| `ref record { ... }` | a record, declared with `type` | yes |
| `closed { ... }` | a variant type, declared with `type` | yes |
| `?T` | a value that may be `nil` | as `T` |
| `&T` | a borrowed reference (section 4) | no |
| `^T` | a checked, non-owning handle (section 5) | no |
| `Result[T]` | success or a message and code (section 7) | yes |

Records and closed types may refer to each other through a forward declaration
`type Sym`; the fields become usable once the type is defined. A record field
is private to its defining module unless declared `pub`.
