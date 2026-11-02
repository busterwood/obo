# obx core language

Status: draft 1, 2026-11-02. This is the stage 1 document of
[generic-core-rethink.md](../obo/design/generic-core-rethink.md). It defines
the language that the reference compiler (`ref/`) implements and that the
self-hosted compiler will implement. Where this document and a compiler
disagree, this document wins until it is changed on purpose.

Choices marked **(default)** were made while writing this draft without a
separate decision from the user; they are the ones to revisit first.

## 1. Principles

- One owner per value, borrows that cannot outlive their owner, no collector.
  Copy types are copied; every other type is moved.
- Generics from day one. The compiler knows primitives, arrays, strings,
  borrows and function values. Option, Result, Decimal, Money, Map and the
  formatting functions are library code.
- Generic code is checked once, where it is written. A generic body may use
  only what its constraints promise, and code generation is per instance.
- Each module is parsed to a tree, checked, then compiled in one walk per
  instance. Declarations may appear in any order.
- Arithmetic is checked: integer overflow, division by zero and a lossy
  conversion stop the program. Wrapping arithmetic is spelled differently.

## 2. Lexical structure

- Source is UTF-8 (invalid UTF-8 is a compile error). A line ends at LF, CRLF
  or a lone CR. Comments run from `//` to the end of the line.
- Identifiers: an ASCII letter or `_`, then ASCII letters, digits and `_`.
  Outside comments, string and character literals the source is ASCII.
- A newline ends a statement when the last token on the line is an
  identifier, a literal, `)`, `]`, `}`, or one of `return break continue
  never`.
  `;` also ends a statement. Inside `(` `)` and `[` `]` a newline never ends
  a statement, so arguments and array literals may span lines.
- Integer literals: `123`, `1_000`, `0xff`, `0b1010`. A character literal
  `'a'` is a `byte`; escapes `\n \t \r \0 \\ \' \" \xHH`.
- A fractional literal has a fraction, an exponent or both: `12.34`,
  `1.5e3`, `2e-7`, `6.02e23` (digits on both sides of a point; the exponent is
  `e` or `E`, an optional sign and digits). It is typed by context; see
  section 7.3. A point not followed by a digit is not part of a literal:
  `5.toStr()` calls `toStr` on `5`.
- A number literal longer than 1,000 characters is a compile error.
- String literals `"..."` use the same escapes and may not span lines.

Keywords:

```
as break closed const continue else extern false fn for if import in
interface let match mut never once pub record return true try type value var
while
```

`value` is reserved only before `record` and `closed`. `copy` and `Self` are
names with a fixed meaning in constraint and interface positions.

## 3. Grammar

```ebnf
module      = { import } { [ "pub" ] topdecl } EOF ;
import      = "import" STRING [ "as" IDENT ] ;
topdecl     = typedecl | fndecl | constdecl | globaldecl | externdecl ;

typedecl    = "type" IDENT [ tparams ] "=" typedef ;
typedef     = [ "value" ] "record" "{" { field } "}"
            | [ "value" ] "closed" "{" { variant } "}"
            | "interface" "{" { member } "}"
            | type ;                               (* alias *)
field       = [ "pub" ] IDENT { "," IDENT } ":" type sep ;
variant     = IDENT [ "(" [ type { "," type } ] ")" | "{" { field } "}" ] sep ;
member      = "fn" [ "(" recvmode ")" ] IDENT "(" [ params ] ")" [ ":" type ] sep ;
recvmode    = "Self" | "&" "Self" | "mut" "&" "Self" ;

tparams     = "[" tparam { "," tparam } "]" ;
tparam      = IDENT [ ":" constraint { "+" constraint } ] ;
constraint  = "copy" | qualname [ targs ] ;

fndecl      = "fn" [ receiver ] IDENT [ tparams ] "(" [ params ] ")"
              [ ":" type ] block ;
receiver    = "(" [ IDENT ":" ] type ")" ;       (* no name: associated fn *)
params      = param { "," param } ;
param       = [ "var" ] IDENT ":" type ;

constdecl   = "const" IDENT [ ":" type ] "=" expr sep ;
globaldecl  = "var" IDENT ":" type "=" expr sep
            | "var" IDENT ":" "once" type sep ;
externdecl  = "extern" STRING "fn" IDENT "(" [ params ] ")" [ ":" type ]
              [ "as" STRING ] sep ;

type        = "never" | "&" type | "mut" "&" type | "[]" type
            | "&" "fn" "(" [ type { "," type } ] ")" [ ":" type ]  (* closure *)
            | "&" "[" type "]" | "mut" "&" "[" type "]"     (* slices *)
            | "fn" "(" [ type { "," type } ] ")" [ ":" type ]
            | qualname [ targs ] ;
targs       = "[" type { "," type } "]" ;
qualname    = IDENT { "." IDENT } ;

block       = "{" { stmt } "}" ;
stmt        = ( "let" | "var" ) IDENT [ ":" type ] "=" expr sep
            | ( "let" | "var" ) qualname [ targs ] "{" fieldpat { "," fieldpat } "}"
              "=" expr sep                                (* destructuring *)
            | "if" expr block [ "else" ( block | ifstmt ) ]
            | "while" expr block
            | "for" IDENT "in" expr [ ".." expr ] block
            | "match" expr "{" { arm(block) } [ "else" block ] "}"
            | "return" [ expr ] sep | "break" sep | "continue" sep
            | block
            | expr [ assignop expr ] sep ;
arm(B)      = qualname [ "(" pattern { "," pattern } ")" ] B
            | item { "," item } B ;
item        = literal | qualname ;                  (* a constant, section 8 *)
pattern     = IDENT | "_" ;
fieldpat    = IDENT [ ":" pattern ] ;
assignop    = "=" | "+=" | "-=" | "*=" | "/=" | "%=" | "|=" | "&=" | "^=" ;

expr        = orexpr ;
orexpr      = andexpr { "||" andexpr } ;
andexpr     = cmpexpr { "&&" cmpexpr } ;
cmpexpr     = bitexpr [ ( "==" | "!=" | "<" | "<=" | ">" | ">=" ) bitexpr ] ;
bitexpr     = shiftexpr { ( "|" | "^" | "&" ) shiftexpr } ;
shiftexpr   = addexpr { ( "<<" | ">>" ) addexpr } ;
addexpr     = mulexpr { ( "+" | "-" | "+%" | "-%" ) mulexpr } ;
mulexpr     = unary { ( "*" | "/" | "%" | "*%" ) unary } ;
unary       = ( "-" | "!" | "~" | "&" | "mut" "&" | "try" ) unary | postfix ;
postfix     = primary { "." IDENT [ targs ] | "[" index "]" | "(" [ args ] ")" } ;
index       = expr | [ expr ] ".." [ expr ] ;
args        = expr { "," expr } ;
primary     = INT | FRACTION | CHAR | STRING | "true" | "false"
            | qualname [ targs ] [ "{" [ inits ] "}" ]
            | "[" [ args ] "]"
            | "(" expr ")"
            | ifexpr
            | "match" expr "{" { arm(branch) } [ "else" branch ] "}"
            | "fn" "(" [ lparam { "," lparam } ] ")" [ ":" type ] block ;  (* closure *)
ifexpr      = "if" expr branch "else" ( branch | ifexpr ) ;
branch      = "{" expr "}" ;
lparam      = IDENT [ ":" type ] ;
inits       = IDENT ":" expr { "," IDENT ":" expr } ;
sep         = ";" | NEWLINE ;
```

The empty array literal is `[]`; it needs an expected type. `X{...}` after a name is a record or variant literal, except in the condition
of `if`, `while`, `for` and `match`, where `{` starts the block (wrap the
literal in parentheses there). `&` is a borrow in unary position and bitwise
and between operands. A comparison does not chain: `a < b < c` is an error.
A statement that starts with `if` or `match` is the statement, not the
expression of section 7.8. In an arm, a single `qualname` is a variant or a
constant, and `arm(B)` with `B` = `block` or `branch` means the arm body is a
block or one expression.

## 4. Types

### 4.1 Primitive types

| Type | Meaning |
|---|---|
| `int` | 64-bit signed integer |
| `i32`, `u32`, `u64`, `byte` | 32-bit signed, 32- and 64-bit unsigned, 8-bit unsigned |
| `i128`, `u128` | 128-bit signed and unsigned integers (what the library decimal is built from) |
| `float` | 64-bit IEEE 754 |
| `bool` | `true` or `false` |
| `str` | immutable byte string |
| `ptr` | an opaque C pointer; only for `extern` declarations and their wrappers |

`str` is reference counted inside the runtime, so for the language it is a
copy type: copying a string never copies its bytes, and it is freed with its
last copy. Its bytes are not required to be UTF-8. `s[a..b]` is the substring
of bytes `a` to `b - 1`; it shares the bytes of `s` (no copy, no allocation)
and is itself an ordinary `str`. `s[a..]` and `s[..b]` run to the end and from
the start. Out-of-range bounds stop the program (exit 3). A string is at most
4 GiB − 1 bytes long; making a longer one stops the program (exit 2).

All primitive types are copy types.

### 4.2 Arrays

`[]T` is a growable array that owns its elements. It is an owned type for every
`T`, including copy `T`. Elements are stored inline. Indexing is checked.

### 4.2a Slices

`&[T]` is a read-only view of a run of elements, and `mut &[T]` a writable one:
a pointer and a length. A slice is a borrow (section 4.5), with the same second
class rules; its root is the root of the array it views. It differs from
`&[]T`/`mut &[]T`, which borrow the array itself: a slice cannot grow, and
`append` and `pop` do not take one.

- `xs[a..b]` on an array place or a slice makes a slice of elements `a` to
  `b - 1` (`a..` and `..b` as for strings). It is `mut &[T]` when the place is
  mutable (section 10, rule 6) and used where a `mut &[T]` is expected, and
  `&[T]` otherwise. Bounds are checked when the slice is made (exit 3). As with
  other borrows (section 7.5), `&xs[a..b]` and `mut &xs[a..b]` are the same
  slice spelled explicitly.
- An array borrow converts where a slice is expected: `&xs` or a `&[]T` passes
  as `&[T]`, and `mut &xs` or a `mut &[]T` as `mut &[T]`. So
  `sort(mut &xs)` and `sort(mut &xs[2..10])` call the same function.
- A slice supports `len`, indexing (element places, checked), assignment to
  elements and `swap` when it is `mut`, `for x in s`, and slicing again.
- For rule 3 of section 10, a slice of an array overlaps every other borrow of
  that array and of its elements, so two `mut` slices of disjoint ranges of
  one array cannot be passed to one call.
- A slice of a global array is an error, like any borrow of a global (constant
  tables are already slices). A slice of a temporary array may be passed to a
  call but not kept in a `let`.
- A range is a `mut &[T]` only where one is expected (a parameter, a typed
  `let`, `swap`) or when written `mut &xs[a..b]`.

### 4.3 Records

```obx
type Point = value record { x: int; y: int }     // inline, copied
type Node = record { name: str; kids: []Node }   // boxed, owned
```

- A `record` lives on the heap; a variable holds a pointer to it. It is
  always owned. A record type may refer to itself.
- A `value record` is stored inline in variables, fields and elements. It is a
  copy type when every field is a copy type, and otherwise an owned type that
  is moved like a record. A value record may not contain itself.

Fields are private to the module unless marked `pub`. A record literal
names every field: `Point{x: 1, y: 2}`. The field values are evaluated in the
order they are written.

### 4.4 Closed types

```obx
type Option[T] = value closed { Some(T); None }
type Expr = closed {
    Num(int)
    Add(Expr, Expr)
    Call { name: str; args: []Expr }
}
```

A closed type has a fixed set of variants. `closed` is boxed and may be
recursive; `value closed` is inline, and its size is that of its largest
variant plus a tag. Copy and owned follow the same rule as records.

Variant names belong to their type and are always written qualified by it:
`Expr.Num(1)`, `Expr.Add(a, b)`, `Rounding.HALF_EVEN`, and from another module
`ast.Expr.Num(1)`. This holds in expressions and in `match` arms. Two closed
types may use the same variant names. The one exception is the prelude:
`Some`, `None`, `Ok` and `Err` may be written bare (and also qualified, as
`Option.Some`), and no other module may declare a top-level name that clashes
with them. A user type may have variants with those names; they are always
written qualified. An alias names the variants of the type it aliases, and a
generic one may leave out its type arguments, which are then inferred like
the type's own: with `type R[T] = Result[T, str]`, `R.Ok(3)` is
`Result[int, str].Ok(3)`, and `R.Ok(v)` is a `match` arm.

A variant with positional fields is built like a call, `Expr.Num(3)`,
`Option.None` or `None`; one with named fields like a record,
`Expr.Call{name: "f", args: []}`.

### 4.5 Borrows

`&T` borrows a `T` for reading; `mut &T` borrows it for writing. A borrow is
second class: it can be the type of a parameter, a receiver, a `let`, or a
function result, and nothing else. Section 10 has the rules.

A borrow is never rebound. Reading a borrow reads the value it points to;
assigning to a `mut &T` variable writes through it, and so does assigning to
a field or element reached through it.

### 4.6 Function values

`fn(A, B): R` is the type of a pointer to a top-level, non-generic function or
an instance of a generic one (`sortBy(xs, byAge)`, `apply(parse[int])`). It is a
copy type. Inside generic code the instance may depend on the type parameters
(`mergeBy[T]` inside `sortStable[T]`).

`&fn(A, B): R` is the type of a **closure**: a function that may use the local
variables around it. A closure is a borrow (section 4.5) and is second class
in the same way: it can be a parameter or a `let`, and it cannot be stored,
returned or used as a type argument. It costs no allocation.

```obx
fn sortBy[T](xs: mut &[T], cmp: &fn(&T, &T): int) { ... }

let minAge = 18
sortBy(mut &people, fn(a, b) { return a.age.cmp(&b.age) })
let adults = count(&people, fn(p: &Person): bool { return p.age >= minAge })
```

- A closure expression `fn(params) { ... }` uses the enclosing function's
  locals and parameters by borrow. A variable it assigns, or borrows mutably,
  is captured mutably and must be a `var`; every other one is captured as a
  shared borrow. A closure cannot move a captured variable, and cannot use a
  global by borrow.
- The parameter and result types may be left out when the expected type is a
  closure type. `return` inside the closure returns from the closure.
- The captures are borrows of their variables' roots for section 10: while the
  closure is in scope (a `let`) or while its call runs (an argument), the
  captured roots are frozen (rule 4), or held (rule 3) for an argument, with
  mutable captures counted as `mut &`.
- A plain function value or a named function converts to a closure type, so a
  parameter of type `&fn(...)` accepts both.
- A closure is called like a function: `cmp(&a, &b)`.
- A closure's parameters and locals may not shadow a variable of the enclosing
  function, and a closure may not return a borrow. A `mut &` variable that the
  closure writes through is a mutable capture without being a `var`.
- Types are inferred for a closure argument once the callee's type arguments
  are known from the arguments before it; otherwise write them.

### 4.7 Copy types and owned types

A type is a **copy type** if it has no `drop` method and it is a primitive, a function type, a
`value record` or `value closed` whose fields are all copy types, or a type
parameter constrained by `copy`. Every other type is **owned**.

### 4.8 Type identity and aliases

Types are nominal: two declarations make two types even with the same fields.
`type A = B` declares an alias; `A` and `B` are then the same type. Generic
types are equal when their declarations and arguments are equal.

## 5. Declarations and modules

### 5.1 Modules

A module is one source file. `import "path/name"` makes the public names of
`name.obx` available as `name.x`; `as other` renames the qualifier. A path that
starts with `./` or `../` is relative to the importing file (`import "./ast"`).
Any other path is looked up only in the library roots given to the compiler
(`import "strings"`), so a local file never shadows a library module, and a
library module never shadows a local one. Imports may not form a cycle. The module `std` is imported into every
module implicitly and its public names are visible unqualified.

Within a module the order of declarations does not matter. Every top-level
name is visible throughout its module; `pub` makes it visible to importers.

### 5.2 Constants and globals

`const` names a compile-time value: an integer, float, bool, string or
fractional literal, or an operator expression over constants. A global `var`
has a copy type and a constant initializer (constants, and value record or
variant literals of constants). A constant expression that overflows is a
compile error. A constant is folded in the arithmetic of its type: a typed
constant that does not fit its type is an error, and `const C: u32 = ~0` is
`0xffffffff`. A global may not be borrowed
(`&g` is an error; a method call that needs a borrow of a global works on a
temporary copy; a `mut &Self` method on a global is an error).

**Constant tables.** A `const` may also be an array literal of constants, or a
value record or variant literal of constants, of a copy type:
`const POW10: []u128 = [1, 10, 100]`. A constant table lives in read-only
static data; it is never freed and never changes. Using its name gives a
`&[T]` slice (section 4.2a), so `POW10[i]`, `len(POW10)` and `for p in POW10`
work, and it can be passed wherever a `&[T]` is expected. It cannot be
appended to or assigned.

**Set-once globals.** `var CONFIG: once Config` declares a global of any type,
owned or copy, that starts empty and is set at most once:

- `CONFIG.set(v)` moves `v` in. Setting it a second time stops the program
  (exit 12).
- `CONFIG.get()` returns a `&Config` borrow. Reading it before it is set
  stops the program (exit 12). `CONFIG.isSet()` tells which.
- Once set, the value never changes and is not freed until the program ends,
  so a borrow from `get()` is valid everywhere and freezes nothing.
- When `main` returns, the set-once globals are dropped (section 9.4). Each
  is first marked dropped, which makes it empty again: `isSet()` is false, and
  `get()` or `set(v)` from then on (in a drop method, including its own) stops
  the program (exit 12).

**Globals of owned types** are otherwise not allowed. Mutable state that owns
heap data belongs to `main` (or a context record it creates) and is passed down
by borrow. Borrowing mutable owned globals soundly would need the effect rules
of section 13; they will be considered only if real programs need more than
copy globals, constant tables and set-once globals.

### 5.3 Functions and methods

```obx
fn gcd(a: int, b: int): int { ... }
fn (p: &Point) norm2(): int { return p.x * p.x + p.y * p.y }
fn (m: mut &Map[K, V]) put(k: K, v: V) { ... }
fn (USD) code(): str { return "USD" }        // associated function
fn (Decimal) parse(s: str): Result[Decimal, str] { ... }
```

- A receiver is a value (`p: T`), a borrow (`p: &T`) or a mutable borrow
  (`p: mut &T`). A receiver with no name declares an associated function,
  called as `T.name(...)` or, on a type parameter, `C.name(...)`.
- The type parameters of a generic receiver type (`Map[K, V]`) are declared
  by the receiver and are in scope in the method. The receiver may add
  constraints for that method only: `fn (m: &Map[K, V: copy]) get(k: &K): Option[V]`
  exists only on instances whose `V` is a copy type.
- Methods may be declared only in the module that declares the type. `std`
  declares the methods of the primitive types.
- `var` before a by-value parameter makes it assignable in the body.
- A method named `drop`, with receiver `mut &T` and no other parameters, is
  the type's destructor (section 9.4). It cannot be called directly.
- A function without a result type returns nothing.
- A function whose result type is `never` does not return: every path through
  its body must end in a call to another `never` function (`panic`, `exit` are
  `never`) or a loop with no `break`. A call to a `never` function ends its
  path like `return` (section 8 and the joins of 9.1). `never` may appear only
  as a result type.
- `var` on a borrow or closure parameter (`&T`, `&[T]`, `&fn()`) is an error:
  a borrow is never rebound.

### 5.4 Interfaces

```obx
type Ord = interface { fn (&Self) cmp(other: &Self): int }
type Currency = interface { fn code(): str; fn scale(): int }
```

An interface is a list of method and associated-function signatures over
`Self`. In draft 1 interfaces are used only as constraints; there are no
interface values (section 13). A type **satisfies** an interface when it has,
for each member, a method or associated function of the same name with the
same parameter and result types (with `Self` replaced by the type) and the same
receiver mode. A by-value receiver on a copy type also satisfies `&Self`.
Satisfaction is structural: there is no `impl` declaration.

`std` defines `Eq` (`eq`), `Ord` (`cmp`), `Hash` (`hash`) and `Show`
(`toStr`) and implements them for the primitive types.

## 6. Generics

### 6.1 Parameters and constraints

Functions and types take type parameters: `fn max[T: Ord + copy](a: T, b: T): T`,
`type Map[K: Eq + Hash, V] = record { ... }`. A constraint is an interface,
possibly generic (`T: Into[str]`), or `copy`.

A type argument is a type: `never` and "no result" are not, so neither can be
a type argument, written or inferred (`call(fn(): never { exit(3) })` with
`fn call[R](f: &fn(): R): R` is an error, and so is `call(hi)` with a `hi`
that returns nothing).

### 6.2 Checking at the definition

A generic body is checked once, with each type parameter as an opaque type:

- A type parameter is owned unless it is constrained by `copy`. It is moved,
  dropped and borrowed by the ownership rules for owned values, whatever it is
  later instantiated with.
- Its methods, associated functions and operators are exactly those of its
  constraints. `a < b` on a `T: Ord` calls `cmp`; on an unconstrained `T` it is
  an error at the definition.
- A generic type's field types are checked with the same rules, and the
  methods of a generic type see the constraints its declaration gives.

An instance therefore cannot fail to compile when its type arguments satisfy
the constraints. Each use names or infers the type arguments and the compiler
checks the constraints there.

### 6.3 Inference

Type arguments of a call are inferred from the argument types, then from the
expected type (`var m: Map[str, int] = newMap()`). Type arguments that cannot
be inferred must be written: `parse[int](s)`. Type arguments of a record or
variant literal are inferred from the field values and the expected type
(`None` needs an expected type).

### 6.4 Instances

Each distinct instance of a generic function or type is compiled once per
program, with its own code and drop glue. Instantiation must terminate: a
generic function that instantiates itself with a larger type (`f[T]` calling
`f[Option[T]]`) is an error.

## 7. Expressions

### 7.1 Integer arithmetic

- `+ - *` on integer types stop the program on overflow. `+% -% *%` wrap.
- `/` and `%` stop the program when the divisor is zero, and on `MIN / -1`.
  Division truncates towards zero; `%` has the sign of the dividend.
  The library's `checkedAdd` ... `checkedRem` methods return `None` where the
  operator would stop, for operands that come from input.
- `<< >>` take a shift count of type `int`; a count outside 0 to width-1
  stops the program. `>>` is arithmetic on signed types. Bits shifted out of
  `<<` are lost.
- `& | ^ ~` are bitwise.
- Both operands of a binary operator must have the same type; there are no
  implicit conversions. An integer literal takes the type its context expects,
  or `int`, and must fit it. In a binary operator, a literal operand takes the
  type of the other operand (`x + 1` with `x: i32` is `i32`; `f * 2.0` with
  `f: float` is `float`).

### 7.2 Conversions

A primitive type name called like a function converts: `byte(n)`, `int(b)`,
`float(n)`, `i128(n)`. An integer conversion stops the program when the value
does not fit. `int(f)` truncates a float and stops the program on NaN or out
of range. There are no other conversions.

### 7.3 Fractional literals

A fractional literal whose expected type is `float` is a float (the nearest
one). Otherwise the literal is written as `units × 10^-scale`, where `units` is
its digits without the point as an `i128` and `scale` is the number of digits
after the point minus the exponent (so `1.50` is `(150, 2)`, `1.5e3` is
`(15, -2)` and `2e-7` is `(2, 7)`). It is then the call
`T.fromFraction(units, scale)` when the expected type `T` has an associated
function `fromFraction(i128, int)`, and the call `fromFraction(units, scale)`
of the function in scope otherwise (std's makes a `Decimal`, so `int(1.5)` is
an error). A literal whose digits do not fit an `i128`, or whose scale does
not fit an `int`, is a compile error.
This is one of the lang items.

### 7.4 Operators on other types

For operands that are not primitive, an operator is a method call:

| Expression | Means |
|---|---|
| `a + b`, `a - b`, `a * b`, `a / b`, `a % b` | `a.add(b)`, `.sub`, `.mul`, `.div`, `.rem` |
| `-a` | `a.neg()` |
| `a == b`, `a != b` | `a.eq(&b)`, `!a.eq(&b)` |
| `a < b` etc. | `a.cmp(&b) < 0` etc. |

`str` has built-in `+` (concatenation), `==` and the comparisons (bytewise).
`bool` has `== != && || !`. A compound assignment `a += b` means `a = a + b`
with `a` evaluated once: the index expressions in `a` run once, before `b`, and,
as in `a = a + b`, the value is stored to the place they name after `b` has run
(so `xs[i] += f(mut &xs)` is allowed, and `i` is checked again when it is
stored to).

### 7.5 Calls and method calls

Arguments are evaluated left to right. A method call `x.m(...)` borrows,
mutably borrows, copies or moves `x` as the receiver declares; the receiver
must be a place or a temporary for a borrow, and a mutable place or a
temporary for a mutable borrow (a temporary lives to the end of the statement,
section 7.7, so `make().name()` is allowed). A method call on a borrow uses what
it points to.

An argument that already has a borrow type is passed to a parameter of a
borrow type as it is: `f(r)` and `f(&r)` both pass the borrow `r`. A `mut &T`
may be passed where `&T` is expected. A copy-type argument is copied when it is
evaluated, so `f(mut &n, n.v)` does not conflict (rule 10.3 concerns borrows
and moves only).

**Borrows are taken at the call.** A borrow argument `&p` or `mut &p` (or a
borrow variable passed as it is), and the implicit borrow of a method
receiver `p`, whose place `p` is made only of a local variable or parameter
(a borrow one is dereferenced), fields and indexes into arrays, is *taken*
when the call is made, after all its arguments have been evaluated. The index
expressions in `p` still run in their position, left to right; when the call
is made the place is found again and its indexes are checked again (exit 3),
for several such borrows in the order of the arguments.
So `p.node(K, p.block())` with `p: mut &Parser`, `xs[i].push(f(mut &xs))` and
`c.emit(c.T(t) + ";")` are allowed. The same holds for the first argument of
`append` and `replace`, taken after the value. A borrow reached through a
slice or through a borrow returned by a call, a slice argument and a closure
argument are taken in their position, as before. A borrow taken at the call
needs its root: if a later argument moved it, that is an error (use after
move). Rule 3 of section 10 applies between the borrows as taken.

### 7.6 try

`try e` requires `e` to have a closed type with variants `Ok(T)` and `Err(E)`
(the second lang item). It yields the `Ok` value, or returns `Err(e)` from the
enclosing function, whose result type must be a closed type with an `Err(E)`
variant of the same `E`. `try` may appear anywhere in an expression; owned
values already built for the statement are dropped before the return.

### 7.7 Temporaries

A value produced inside a statement and not stored lives until the end of
that statement and is then dropped: `println(makeBox(3).v)` is allowed.

### 7.8 Conditional and match expressions

```obx
let sign = if x > 0 { 1 } else if x < 0 { -1 } else { 0 }
let name = match e { Expr.Num(n) { n.toStr() } Expr.Call(f, _) { f } else { "?" } }
```

- `if c { a } else { b }` in expression position is a value; `else` is
  required and `else if` chains. `match e { ... }` in expression position is
  a value; its arms cover the scrutinee as for the match statement (section
  8). Each branch or arm body is exactly one expression.
- Only the chosen branch or arm runs. Its temporaries are dropped when it
  ends, after its value is taken; the value is a temporary of the statement.
- All branches have the same type; the expected type flows into each, and an
  integer literal branch takes the type of the others. A branch whose type is
  `never` (a call to a `never` function) takes the type of the others.
- Ownership is as for the statements: moves are joined across the branches
  (section 9.1), and a match binds, borrows and consumes its scrutinee as in
  section 9.3.
- The result is a value, copy or owned; it may not be a borrow, a slice or a
  closure. A borrow branch is read: `if c { r } else { 0 }` with `r: &int`
  is an `int`, and a field bound as a borrow by a match arm can be read but
  not be the result when it is owned (moving out of a borrow).

## 8. Statements

- `let x = e` binds a name that cannot be assigned again or borrowed
  mutably. `var x = e` binds one that can. Every variable is initialised.
- `if`, `while` and `break`/`continue` are as usual; conditions are `bool`.
- `for i in a..b` counts from `a` up to `b - 1` over `int`; `b` is evaluated
  once. `for x in xs` walks an array: `x` is a copy of each element when the
  element type is a copy type, and a `&T` borrow of it otherwise. The array is
  borrowed for the whole loop.
- `match e { ... }` takes a closed value. An arm names a variant and binds its
  positional fields (`Expr.Add(l, r)`) or named fields in declaration order; `_`
  ignores one. Arms must cover every variant or end with `else`. `match` on an
  integer, `byte` or `str` takes literal arms and requires `else`. Such an arm
  may also name a constant (`K` or `mod.K`, section 5.2), alone or in a list
  with literals (`N_INT, N_FRAC, 7 { ... }`), when `x == K` would type-check:
  the constant has the scrutinee's type, or is an untyped integer constant
  that fits it. Constant tables and globals cannot be arms. Two arms or items
  with the same value are an error.
- `return e` returns. A function with a result type must return on every
  path; falling off the end is an error. A `match` or `if`/`else` whose every arm returns
  counts as returning.
- A local name may not be declared again while another local or parameter of
  the same name is in scope (no shadowing within a function). It may shadow a
  top-level name.
- `p = e` evaluates `e` first, then drops the old value at `p`, then stores.
- A statement after `return`, `break`, `continue`, `panic(...)` or `exit(...)`
  in the same block is an error: it cannot be reached.

## 9. Ownership

### 9.1 Moves

Using an owned value where a value is expected (initialising a variable,
passing a by-value argument, returning, storing in a field or element) moves
it. The source then has no value until it is assigned again.

- `let P{a, b: x, c: _} = e` takes a record apart: each named field is moved
  (or copied) into a new variable (`a` binds field `a`, `b: x` binds field `b`
  as `x`), fields bound to `_` or not named are dropped, and the record itself
  is freed without dropping the moved fields. `e` must be a value, not a place
  (use `move(x)` for a local), unless the record is a copy type. A record type
  with a `drop` method cannot be taken apart, and private fields can be named
  only in the record's module. `var P{...} = e` makes the variables
  assignable. Unbound fields are dropped at once, in reverse declaration
  order; the bound ones are bound in the order of the pattern.
- Only a whole local variable or by-value parameter can be moved out of.
  Moving out of a field, an element, a borrow or a global is an error; use
  `replace`, `swap` or `pop`.
- The state of every variable must agree where paths join: after `if`/`else`,
  after `match`, and at the end of a loop body (for the next iteration and for
  the code after the loop). A variable moved on one path only is an error.
- Moves are not allowed inside `&&` and `||` operands or a `while` condition.

### 9.2 Drops

An owned value is dropped when its owner goes out of scope or is assigned a new
value, and owned temporaries are dropped at the end of their statement.
Variables are dropped in reverse order of declaration. Dropping a value drops
everything it owns: fields in reverse declaration order, then array elements
from first to last.

### 9.3 match and ownership

`match` on a place (a variable, field, element or borrow) borrows it: positional
or named fields of owned types are bound as `&T`, and of copy types as copies.
`match` on any other expression (a call result, `move(x)`) consumes it: the
bindings own the fields, and fields not bound (or bound to `_`) are dropped at
the start of the arm, before its body runs.

A path that ends in `return`, `break`, `continue`, `panic` or `exit` does not
take part in the join of section 9.1.

### 9.4 Destructors

If a type has a `drop` method, dropping a value of that type first calls
`drop` with a mutable borrow, then drops the fields. This is how a wrapper
closes a C resource. A `drop` method may not move out of `self` or assign the
whole of `self`, and no interface member can be satisfied by it.

Dropping a long chain of boxed values (a linked list) does not use stack depth
proportional to its length. This includes a chain whose `drop` method detaches
the rest of the chain into a local variable or a temporary
(`let c = replace(mut &self.next, None)`): what the method still owns when it
returns is dropped then, before the fields, as section 9.2 says, and without
nesting. It does not include a method that drops the rest of the chain before
its last statement (in an inner block, or by passing it to another function
that drops it): the rest of its body must run after that drop, so each node
needs a stack frame, and a long enough chain overflows the stack (exit 4).

Globals are dropped when `main` returns (not after `exit`). The set-once
globals of a module are dropped before those of the modules it imports
(directly or not; `std` last), and within a module in reverse order of
declaration; each is marked dropped before its value is dropped (section 5.2).
Copy globals are dropped after all of them, which runs no code (a copy type
has no `drop` method), so a drop method never reads a freed global.

## 10. Borrows

A borrow points into a value that is owned elsewhere. The compiler enforces
that a borrow is never used after what it points to is freed, with the
following rules, all checked within one function at a time.

1. **Second class.** A borrow type may appear only as a parameter, receiver,
   `let` or function result type. A borrow cannot be stored in a field, an
   element, a global, a `var`, or used as a type argument.
2. **Roots.** Every borrow expression `&p` or `mut &p` has a root: the local
   variable or parameter that the place `p` starts from. Borrowing a global or
   a temporary that does not outlive the statement into a `let` is an error.
3. **Calls.** While a call's arguments are evaluated, the places it has
   already borrowed, or will move, are held: a later argument, at any depth of
   nesting, may not mutably borrow, assign or move a place that overlaps a held
   one. `use(first(&xs), pop(mut &xs))` is an error. A borrow taken at the call
   (section 7.5) is not held while the arguments run; when it is taken it is
   held, and may not overlap another borrow or move of the same call unless
   both are shared. A shared read of a place
   held by a mutable borrow of an enclosing call is allowed
   (`m.put(k, m.get(&k)...)`). Two places overlap when one is a prefix of the
   other; any two indexes into the same array overlap. The same holding covers
   an operator's left operand and an index expression's array.
4. **Frozen roots.** While a `let` borrow is in scope, its root is frozen: it
   may not be assigned, moved, or mutably borrowed, and nothing under it may be
   assigned. A `let r = mut &p` freezes the root against any other use as well,
   except through `r`. A `for x in xs` loop freezes `xs` the same way for the
   loop body.
5. **Results.** A function whose result type is a borrow must have exactly one
   borrowed parameter or receiver, and every `return` must return a borrow of
   a place rooted at it, written `return &p.f` or `return mut &p.f` (or a
   borrow returned by such a call). At the call site the
   result borrows the argument passed there, and rule 4 applies to that
   argument's root while the result is in scope.
6. **Mutation.** Assigning through `p` or calling a `mut &Self` method on `p`
   requires `p` to be a `var`, a `var` parameter, or rooted at a `mut &`
   borrow.

A `match` on a place of an owned type whose arm binds a field as a borrow
freezes the place's root (rule 4) for the body of that arm.

Rule 4 is lexical: a borrow freezes its root until the end of its block, not
until its last use. Use an inner block to end it sooner.

## 11. Built-in functions

These are the only functions the compiler implements. Their names are
reserved: declaring a top-level name that is one of them is an error. Everything else,
including `print`, `println`, string formatting and parsing, is in `std`.

| Function | Meaning |
|---|---|
| `len(a)` | length of an array or string, `int` |
| `append(mut &a, v)` | add `v` (moved) at the end of `a` |
| `pop(mut &a)` | remove and return the last element; stops the program if empty |
| `swap(mut &a, i, j)` | swap two elements of an array |
| `replace(mut &p, v)` | store `v` at place `p` and return the old value |
| `move(x)` | the value of local `x`, which is then moved |
| `array(n, v)` | `n` copies of the copy value `v` |
| `panic(msg)` | print `obx: panic: msg` and stop with exit code 12 |
| `assert(c)` | `panic("assertion failed")` with the line, when `c` is false |
| `exit(code)` | stop the program with `code`, without drops (and without the `OBX_CHECK` leak check) |

String and runtime intrinsics used by `std` (`strByte`, `strSlice`,
`strFromBytes`, `strToBytes`, `intToStr`, `floatToStr`, `parseFloat`,
`writeFd`, `readFile`, `args`, `getenv`) are built-ins too; they are listed
with `std`, not here, because ordinary code calls the `std` wrappers. They are
not reserved: a module's own declaration of the same name wins.

## 12. C interop

```obx
extern "sqlite3" fn sqlite3_open(path: str, out: mut &ptr): i32
extern "c" fn getpid(): i32
```

An `extern` declaration names a C function and the library that provides it
(`"c"` for libc). `as "c_name"` gives the C name when the obx name differs:
`extern "c" fn cAbs(x: i32): i32 as "abs"`. An extern is always private to
the module that declares it (`pub extern` is an error): a module that needs C
exposes safe wrappers, such as an owned type with a `drop` method, never the
raw C function. Two extern declarations in a program with the same C name must have
the same parameter and result types; otherwise it is a compile error.
The C name (given by `as`, or the obx name) may not be one the program's own
C code defines: `main`, a name that starts with `rt_` or `obx_` (the runtime;
its documented `obx_` helpers for the library, such as `obx_errno`, may be
named), or a name of the form the compiler generates (`f1_x`, `g1_x`, `k1_x`,
`gl1_x`, `S1_x`, `sl1`, `fn1`: a prefix, digits, and for the first five an
underscore).

Parameter and result types are limited to integers, `float`, `bool`, `ptr`,
`mut &ptr`, `mut &int` and `str`. A `str` result copies a NUL-terminated
`char *` the C function returns (a NULL result gives `""`); the C memory is
not freed. A `str` argument is passed as
a NUL-terminated pointer valid for the call (the runtime copies the bytes
when the string is a substring without a NUL after it), and stops the program if it
contains a NUL byte (exit code 12). `strFromC(p: ptr): str` copies a
NUL-terminated C string. `ptr` is an ordinary copy type (a wrapper record may
hold one), but the only operations on it are `==`, `!=` and the built-in
`isNull(p)`; only extern functions create one.
Calls to extern functions are unchecked: wrap each in a function or a type
with a `drop` method that makes its use safe.

## 13. Not in draft 1

Each of these is planned and is left out of draft 1 to keep the first
compiler small. None of them needs a change to the rules above.

- **Handles** (`^T`): generation-checked non-owning pointers, as in obo.
- **Escaping closures**: closures that can be stored and returned (with an
  owned environment). They are to be designed together with interface values.
- **Interface values** (`box I`, `&I`): dynamic dispatch.
- **Effects**: per-function summaries of what a call may free, which allow
  owned globals and borrows of them.
- **Templates** (sql, html): to be a desugaring onto a library protocol.
- **Threads.**

## 14. Runtime errors

A stopped program prints `obx: <message> at <file>:<line>` to stderr and
exits with:

| Code | Cause |
|---:|---|
| 2 | out of memory, or a negative array length |
| 3 | index out of range (array or string), or `pop` on an empty array |
| 4 | stack overflow (too deep a recursion) |
| 5 | with `OBX_CHECK=1`: live objects at exit (a leak) |
| 10 | integer overflow, division by zero, bad shift count, lossy conversion |
| 12 | `panic` or `assert` |

## 15. Lang items

The compiler knows these names from the library: `fromFraction` (section
7.3), the variant names `Ok` and `Err` (section 7.6), and the four prelude
variants that may be written bare (`Some`, `None`, `Ok`, `Err`, section 4.4).
It knows no type from the library.
