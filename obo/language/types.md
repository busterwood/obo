Part of the obo language reference. Index and core syntax: [LANGUAGE.md](../LANGUAGE.md).

## 1. Types

| Type | Meaning | Owned |
|---|---|---|
| `int`, `byte`, `bool` | numbers and booleans, copied by value | no |
| `float` | IEEE 754 binary64, copied by value | no |
| `decimal` | exact 38-digit decimal, 24 bytes, copied by value (v1 scalar tier) | no |
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

A record field is **private to its defining module by default**. `pub` before a
field name makes that field part of the exported interface; it applies to every
name in a comma group, and a mixed record spells the private and public fields as
separate declarations:

```obo
// example: runs
// prints 12
module main

pub type Counter = ref record {
    pub value: int          // public
    step: int               // private to this module
    pub label, unit: string // both public
}

fn main() {
    var c = new Counter{value: 10, step: 2, label: "n", unit: "u"}
    c.value += c.step
    println(c.value)
}
```

Inside the defining module every field is accessible, whether `pub` or not.
Importers may read, write, borrow, move, `take`, `replace` and name in
`new T{...}` only public fields. External construction is allowed only when
**every** field is public; otherwise the module must provide a constructor
function. A field's type is not part of the interface when the field is private:
a private field may use a private named type, and the export closure check skips
it. A `pub` field of a private type is still the existing export error, because
an importer must be able to name the field's type.

Closed-variant fields remain public in v1; `pub` on a variant field is rejected.
An aliased imported type takes the underlying type's field visibility and owner.
The `.sym` representation and the exact diagnostics are in section 6 and
section 10.

### Float

`float` is IEEE 754 binary64 in one 8-byte word. The `f`/`F` suffix marks a
float literal (`1f`, `1.5f`, `1.5e3F`); a bare fractional or exponent literal is
exact `decimal` (next section), so write the suffix in a float context. There is
no implicit int/float or decimal/float conversion: use `float(i)`. Mixed
float/int and float/decimal arithmetic and comparison are errors. Float is
finite, infinite and NaN-capable IEEE data: runtime arithmetic
uses IEEE overflow to infinity and standard NaN behavior; comparisons are
unordered for NaN (`==` false, `!=` true, ordered comparisons false), and
`+0 == -0`. A finite source literal whose exact value rounds beyond binary64 is
a compile error, never an implicit infinity.

Float values are unowned one-word scalars and work in locals, parameters,
returns, fields, arrays, globals, `Result[float]`, methods, interfaces and
function types. Formatting is shortest round-trip with deterministic ties-to-even,
using fixed notation for decimal exponents -6..20 and scientific `e±NN` outside
it; special spellings are `nan`, `inf`, `-inf` and `-0`. `parseFloat(s)` is the
explicit checked string parser and returns `Result[float]`; malformed input is
code 22 and overflow code 34, while underflow gives signed zero/subnormal.
There is no `float -> int` conversion in this release.

Float constants are raw binary64 bit patterns in `.sym`; format 6 requires
exactly `0x` plus 16 lowercase hexadecimal digits. The format is float-specific;
format 5 does not carry the float public grammar. `floatBits` is an internal test
diagnostic, available only with `--test-float-bits`, not a public API.

### Decimal (0.12 release scalar tier)

`decimal` is an exact finite decimal value: a 128-bit coefficient (at most
`10^38 - 1`), an `int16` exponent, a `uint16` display scale and a sign, in 24
bytes. There is no NaN, infinity or negative zero. A bare fractional or
exponent literal is decimal (`12.34`, `0.0011e3`, `1.2300`); an integer literal
is still `int`; `f`/`F` still marks float. There is no `m`/`d` suffix. The
written fractional digits are the display scale, so `1.2300` prints `1.2300`.
A literal needing more than 38 significant digits, or an exponent/display scale
outside the representation, is a compile error.

```obo
// example: runs
// prints 2.40
// prints 1.440
// prints 0.012300
// prints true
fn main() {
    println(1.20 + 1.2)
    println(1.20 * 1.2)
    println(1.2300e-2)
    println(1.0 == 1.00)
}
```

`+`, `-`, `*` and `%` are exact; a result that is not exactly representable
traps with exit 10 (codes 40-41), never silently rounds. `%` is the truncating
remainder with the sign of the dividend; a zero divisor traps (code 42).
Comparisons compare numeric value only, ignoring display scale. `/` and `/=` are
compile errors; use `decimal::div(a, b, scale, mode)`, which returns
`Result[decimal]` and requires an explicit scale `0..32768` and mode.
`decimal::quantize(v, scale, mode)` rounds once at the target scale and
`decimal::toInt(d, mode)` is the checked `Result[int]` conversion.

```obo
// example: runs
// prints 0.50
fn main() {
    let r = decimal::div(1.00, 2.0, 2, HALF_AWAY)
    match r {
        ok(v) { println(v) }
        err(m, c) { println("err ", c) }
    }
}
```

`decimal(i)` and `decimal::fromInt(i)` convert an int/byte exactly; there is no
implicit int-to-decimal conversion. `int(d)` is a compile error pointing to
`decimal::toInt`. Float-to-decimal and decimal-to-float conversions are not in
v1. `print`/`println` preserve the display scale; if a capped multiply display
scale would hide a nonzero fractional digit, they print the exact canonical
form instead of rounding.

The public text surface returns `Result` values:

| Call | Result |
|---|---|
| `decimal::parse(s)` | `Result[decimal]`: strict text grammar, preserving display scale |
| `decimal::format(d)` | `Result[string]`: fixed display text with exactly `dscale` digits |
| `decimal::canonical(d)` | `Result[string]`: exact minimal value text, ignoring `dscale` |
| `decimal::check(v, p, s)` | `Result[decimal]`: exact fit for declared precision/scale |
| `decimal::exponent(d)` | normalised exponent as `int` |
| `decimal::dscale(d)` | stored display scale as `int` |

`parse` accepts an optional sign, mandatory integer digits, an optional
fraction with at least one digit and an optional exponent, all ASCII; empty
text, whitespace, grouping, underscores, NUL, non-ASCII, `.5`, `1.`, `NaN` and
`Infinity` are invalid. `-0` is positive zero and the written display scale is
preserved. `format` never rounds: a capped `dscale` that would drop a nonzero
digit returns code 3. `canonical` is `0` for zero, fixed at or below 128 bytes
of magnitude text and lowercase scientific above it. `check` requires
`value*10^s` to be an integer with at most `p` digits, `p` 1..1000 and `s`
-1000..1000; it ignores `dscale` and never rounds. Text API error messages are
exact and carry the core status as the `Result` code (3, 4, 6, 7, 8, 9 or 10).

```obo
// example: runs
// prints 1 1
// prints 1230.0
// prints 1230
// prints 4 obo: invalid decimal text
// prints 3 obo: value does not fit the declared scale without rounding
fn main() {
    let p = decimal::parse("+1.2300e3")
    match p {
        ok(v) {
            println(decimal::dscale(v), " ", decimal::exponent(v))
            let f = decimal::format(v)
            match f { ok(s) { println(s) } err(m, c) { println(c) } }
            let c = decimal::canonical(v)
            match c { ok(s) { println(s) } err(m, c) { println(c) } }
        }
        err(m, c) { println("err ", c) }
    }
    let bad = decimal::parse(".5")
    match bad {
        ok(v) { println("unexpected") }
        err(m, c) { println(c, " ", m) }
    }
    let k = decimal::check(1.23456, 10, 4)
    match k {
        ok(v) { println("ok") }
        err(m, c) { println(c, " ", m) }
    }
}
```

```obo
// example: error: decimal division needs an explicit scale and mode
fn main() { let x = 1.0 / 2.0 }
```

In this tier decimals work as locals, parameters and returns (three-word
by-value slots and a hidden destination pointer for returns), in
`Result[decimal]`, and as record fields (0.16). A decimal field takes 24 bytes
inline in its record or closed-variant payload, in declaration order and on an
8-byte boundary; an omitted decimal field is zero. Reads, `=`, `+=`, `-=`, `*=`,
`%=` and record literals work through a handle (`^T`) and through a `&T` or
`mut &T` borrow. A compound assignment computes into a temporary and then copies
it into the field. A decimal operand is read before the operand after it runs, as an
int operand is: in `r.inr.a * f(r)` the left value is the one `r.inr.a` had before `f`
ran, even if `f` replaces the record or writes the field. The same holds for each
decimal or int argument of `decimal::div`, `quantize`, `toInt` and `check` that a later
argument follows. Like an int field, a field may not be assigned from a call
that might free its record (`cannot assign to a field or element across a
call`). A decimal global works (0.16): it takes 24 bytes of the zero-initialised
global data, so `var g: decimal` starts at zero, and
`var g = expr` takes any decimal expression, including a call or a product, and a
record global may be built from a record literal with decimal fields: a global
initializer may build values as a local may. Module
initializers run in import order: a dependency's before its importer's, and
declarations in source order. A decimal
constant is a literal or a unary-minus literal (`const RATE = 0.0825`,
`const NEG = -1.5`): arithmetic is not folded, so `const C = 1.0 + 2.0` is refused
with `decimal constants must be a literal; arithmetic is not folded`. A constant
takes no storage of its own; each use materialises its three words into a
temporary. `-0.00` is positive zero and keeps its written display scale. Decimal
methods and interface members work (0.18): a decimal is a method parameter and
return value, including `Result[decimal]`, and a decimal-returning method is called
directly or through an interface's vtable. A decimal is never a receiver, and a
decimal member can be `pure`, plain, `mut` or `impure` like any other. Decimal
function values and non-capturing lambdas work (0.19): a function type may take
and return a decimal, a decimal-returning value call keeps the hidden
destination at `16 + visible parameter bytes` and puts the value cell above it,
and a captured decimal is a 24-byte value snapshot. `mut`/`move` captures,
`&decimal`, stored closures and named function values remain out of scope. `?decimal`
is not representable; there is no raw-bits access and no owned or mutable decimal.

```obo
// example: runs
// prints 3.750 3.000
type Line = ref record { qty: int; price: decimal; total: decimal }

fn main() {
    let l = new Line{qty: 3, price: 1.25, total: 0.00}
    l.total = l.price * 3.0
    l.price += 0.25
    println(l.total, " ", l.price * 2.0)
}
```

A global and a constant (0.16):

```obo
// example: runs
// prints 0.0825 0 1.0825 0.00
const RATE = 0.0825
const NEG0 = -0.00
var total: decimal

fn main() {
    println(RATE, " ", total, " ", RATE + 1.0, " ", NEG0)
}
```

A decimal method and an interface member (0.18):

```obo
// example: runs
// prints 13.30
// prints 11.00
type Account = ref record { v: decimal }
fn (a: &Account) add(x: decimal, y: decimal): decimal { return a.v + x + y }
type Ledger = interface { pure fn add(x: decimal, y: decimal): decimal }
impl Ledger for Account

fn main() {
    let a = new Account{v: 10.00}
    println(a.add(1.10, 2.20))
    let l: Ledger = a
    println(l.add(0.50, 0.50))
}
```

`[]decimal` (0.16) is an array of decimals, stored inline at a 24-byte stride. It
is created with `new []decimal(n)` (zero-filled) or `new []decimal(0)` and then
`append`ed, and it works where other arrays do: locals, fields, elements of
`[][]decimal`, parameters (`&[]decimal`, `mut &[]decimal`), returns, `Result` and
optional payloads. An element reads and writes like a decimal local (`xs[i]`,
`xs[i] = v`, `xs[i] += v`), and `for d in xs` copies each element. `len`,
`truncate` and `append` work as for other arrays. An index outside the array traps
with `index out of range` and exit code 3, read or write, and a negative index traps
the same way. An `append` takes a decimal value; there is no implicit conversion from
`int`. A `[]decimal` cannot be printed, converted with `string`, taken or replaced
element by element, or borrowed element by element; a decimal element is not an owned
value. The ordering rule for record fields applies to elements too: a decimal element
or value is read before the operand after it runs. The call-ordering rules of section 12
(`xs[f(xs)]`, `append(xs, f(xs))`) apply to `[]decimal` exactly as to `[]int`.

An array of decimals is limited by the runtime's object size (under 4 GiB, 24 bytes
an element). `new []decimal(n)` with n above 178,956,969 traps with
`obx: allocation size overflow` and exit code 2, before any memory is taken. An
`append` that must double an array already holding 134,217,728 decimals (2^27)
traps the same way, so the append limit is lower than the `new` limit.

```obo
// example: runs
// prints 3.75 2 0
fn main() {
    var xs = new []decimal(0)
    append(xs, 1.5)
    append(xs, 2.25)
    xs[0] += xs[1]
    let z = new []decimal(1)
    println(xs[0], " ", len(xs), " ", z[0])
}
```
