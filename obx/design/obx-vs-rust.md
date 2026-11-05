# obx and Rust: a comparison of language features

Status: written 2026-11-04 against main at `624b22f`. This is a comparison
note, not a design decision. It describes obx as shipped (the spec in
[../SPEC.md](../SPEC.md), the library in [../lib/](../lib/README.md), the
compiler and the programs in [../cmd/](../cmd/README.md)) and Rust as of
stable 1.8x, edition 2024. Things that are proposed, planned or experimental
in obx are marked **(proposal)**, **(planned)** or **(experimental)**.

How the claims were checked: every statement about obx rests on the spec, the
library source, a design note or a program compiled with
`obx/compiler/out/obxc` (through C and, where it matters, `--native`).
Complete programs below were compiled and run; snippets that show a rejected
program quote the compiler's real message. Rust is described from knowledge of
the language, not from a project in this repository.

## 1. Summary

**obx** is a small systems language in the Oberon line: few keywords, few
rules, and a compiler small enough to be written in the language itself (it
is: about 23,700 lines of obx, compiling itself to a fixed point). Memory is
managed by single ownership with moves and drops, no collector. Borrows
(`&T`, `mut &T`) exist but are *second class*: they may be a parameter, a
`let`, a function result, or a field or element of a value that is then second
class too (a *borrow-class* value: a view, a token, an iterator). Because no
such value can outlive the function that made it or the caller that passed
it, the checker needs no lifetimes and works on one function at a time with
seven rules. Strings are reference counted and behave as copy values.
Generics are checked where they are written and compiled per instance.
Concurrency is cooperative tasks on one thread, plus worker processes; there
are no threads in obx code. The compiler emits C (built by gcc) or native
x86-64 ELF directly.

**Rust** is a large systems language built around ownership, borrowing with
explicit lifetimes, and traits. Borrows are first class: they can live in
structs, be returned, and be put in iterators, and the borrow checker proves
they are valid. Its type system (traits, associated types, const generics,
trait objects, `async`, macros, `unsafe`) is much broader. It compiles
through LLVM, has threads with `Send`/`Sync`, and has a very large ecosystem.

Both languages reject use-after-free and double free at compile time, without
a collector. Rust also rejects data races between threads; obx has no threads
in its own code. The difference is how much they let you express in exchange: Rust accepts more programs and
needs more concepts; obx accepts fewer and needs fewer.

### At a glance

| Feature | obx | Rust | Notes |
|---|---|---|---|
| Memory management | ownership, drops, no GC | ownership, drops, no GC | same family |
| Borrows | `&T`, `mut &T`, second class | `&T`, `&mut T`, first class | obx stores borrows only in second-class values |
| Lifetimes | none | explicit, inferred, elided | |
| Borrow check scope | one function, seven rules | whole program through signatures | |
| Borrow region | to last use, by name and statement | non-lexical (to last use, by control flow) | obx rejects a little more |
| Integers | `int`(64), `i32 u32 u64 byte i128 u128` | `i8..i128`, `u8..u128`, `isize`, `usize` | |
| Overflow | traps (exit 10); `+% -% *%` wrap | panics in debug, wraps in release | obx always checks |
| Floats | `float` (64-bit) only | `f32`, `f64` | |
| Decimal | `Decimal` (38 digits) in `std`, literals are decimal | crates | library in obx, literal syntax in obx |
| Strings | `str`: counted, immutable bytes, shared substrings | `String`, `&str`: valid UTF-8 | |
| Arrays | `[]T` growable, `&[T]` slice | `Vec<T>`, `[T; N]`, `&[T]` | no fixed-size arrays in obx |
| Structs | `record` (boxed), `value record` (inline) | `struct` (inline), `Box` explicit | |
| Enums | `closed` / `value closed` | `enum` | |
| Option / Result | library types in `std` | library types in `std` | obx has `try`, Rust has `?` |
| Pattern matching | one level, no guards | nested, guards, ranges, bindings | |
| Generics | `[T: Ord + copy]`, checked at definition | `<T: Ord + Copy>`, checked at definition | both monomorphize |
| Interfaces / traits | structural, constraints only | nominal `impl`, also dyn | obx has no interface values yet |
| Dynamic dispatch | none | `dyn Trait` | obx **(planned)** |
| Closures | non-escaping `&fn` only | `Fn`/`FnMut`/`FnOnce`, escaping | obx **(planned)** escaping |
| Iterators | `for x in it` over any `next(): Option[T]`; no lazy closure adapters | `Iterator` and adapters | obx items never borrow the iterator |
| Errors | `Result` + `try`; traps and `panic` exit | `Result` + `?`; panic with unwinding | |
| Concurrency | tasks on one thread, worker processes | threads, `Send`/`Sync`, `async` | |
| Unsafe | no `unsafe`; `extern` private to a module | `unsafe` blocks, FFI | |
| Macros | none | declarative and procedural | |
| Compile-time evaluation | `const` of literals, operators, tables | `const fn`, `const` generics | |
| Package manager | none | cargo, crates.io | |
| Formatter / linter | none | rustfmt, clippy | |
| Backends | C via gcc; own native x86-64 | LLVM (also Cranelift, GCC in progress) | |

## 2. Syntax, modules and visibility

obx has a brace syntax with newlines ending statements (like Go): a line ends
a statement when its last token is an identifier, a literal, a closing
bracket, or `return`/`break`/`continue`/`never`. `;` also works. Comments are
`//` only. There are 28 keywords. Types come after names. Variables are `let`
(fixed) or `var` (assignable). A local cannot shadow another local or
parameter of the same function (it may shadow a top-level name).

```obx
fn gcd(a: int, b: int): int {
    var x = a
    var y = b
    while y != 0 {
        let t = y
        y = x % y
        x = t
    }
    return x
}
```

```rust
fn gcd(a: i64, b: i64) -> i64 {
    let (mut x, mut y) = (a, b);
    while y != 0 {
        let t = y;
        y = x % y;
        x = t;
    }
    x
}
```

Differences a Rust programmer will notice:

- Blocks are not expressions. There is `if c { a } else { b }` and
  `match` in expression position, but every branch must be exactly one
  expression (or `return`/`break`/`continue`), and `else` is required.
- A function with a result type must `return` explicitly.
- No shadowing inside a function, so the Rust idiom `let x = x.trim();` is
  not available.
- Integer literals take their type from context; there are no suffixes.
  There are no implicit numeric conversions: `i32 + int` is an error (the
  compiler said: `operator + needs operands of the same type, not i32 and
  int`).
- Fields in a record declaration are separated by `;` or newlines, not commas
  (a comma is a syntax error).
- Raw strings use backticks and may span lines.

**Modules.** One file is one module. `import "path/name"` (or `as other`)
makes the public names available as `name.x`. A path starting with `./` or
`../` is relative to the importing file; a bare path is looked up only in the
library roots, so a local file can never shadow a library module. Imports
cannot form a cycle. Declaration order does not matter. `std` is imported
into every module and its public names are unqualified.

```obx
import "strings"
import "./ast" as syntax

pub fn shout(s: str): str { return strings.toUpper(s) }
```

```rust
use crate::ast as syntax;

pub fn shout(s: &str) -> String { s.to_uppercase() }
```

**Visibility.** Everything is private to its module unless marked `pub`;
record fields are separately private unless marked `pub`. There is no
`pub(crate)` or `pub(super)`; the module is the only boundary. Methods can
only be declared in the module that declares the type (so there is nothing
like Rust's orphan rule to learn, but also no extension methods on foreign
types). Rust has nested modules, `mod` trees, `use` with globs and renames,
re-exports (`pub use`), and crates as a second level; obx has files and
imports. Imported names are always qualified.

**Assessment.** obx's syntax is smaller and regular. Rust's is larger but
more expressive: block expressions, `if let`, `let else`, labelled breaks,
shadowing and attributes are all absent in obx.

## 3. Types

### 3.1 Integers, floats, booleans

| obx | Rust |
|---|---|
| `int` (64-bit signed) | `i64` (there is no `int`; `isize` is pointer-sized) |
| `i32`, `u32`, `u64`, `byte` | `i32`, `u32`, `u64`, `u8` |
| `i128`, `u128` | `i128`, `u128` |
| (none) | `i8`, `i16`, `u16`, `i64`, `usize`, `isize` |
| `float` (64-bit) | `f64` (and `f32`) |
| `bool` | `bool` |

Arithmetic is checked in obx, always: `+ - *` on integers stop the program on
overflow (exit code 10), as do division by zero, `MIN / -1`, a shift count
outside `0..width-1`, and a lossy conversion. `+% -% *%` wrap. The library
has `checkedAdd` and friends returning `Option`. Rust panics on overflow in
debug builds only, and wraps in release unless `overflow-checks` is on; it has
`wrapping_*`, `checked_*`, `saturating_*` and `overflowing_*` methods.

Conversions are written as the type name called like a function (`byte(n)`,
`int(f)`), and stop the program when the value does not fit. Rust uses `as`
(truncating, never panics) and `try_from`/`into`.

Verified: with `big: i32 = 2147483647`, `big + one` (where `one = 1`) ended
with `obx: integer overflow at t4.obx:28` and exit code 10, and
`n +% 1` gave `-2147483648`.

### 3.2 Decimal

Fractional literals are exact decimals by default. `Decimal` is ordinary
library code in `std` (a coefficient below 10^38, an exponent and a display
scale); the compiler's only part is a lang item, `fromFraction(units, scale)`,
which a literal like `19.99` calls. Operators `+ - *` are exact and stop the
program when not representable; `div`, `quantize` and `round` take a
rounding mode and return `Result`. `float` is the type to use for binary
floating point, and a literal takes it from the expected type.

```obx
let price = 19.99            // Decimal
let f = 1.5
println(f * 2.0)             // 3.00  (Decimal keeps its display scale)
println(0.1 + 0.2)           // 0.3
let a: float = 0.1
let b: float = 0.2
println(a + b)               // 0.30000000000000004
```

`Money[C: Currency]` is a library type over `Decimal` where the currency is a
type, so adding dollars to euros does not compile. Rust has no decimal in
`std`; programs use crates such as `rust_decimal` or `bigdecimal`, and `0.1 +
0.2` is a float. Decimal in obx is a deliberate domain feature (money
arithmetic); Rust leaves it to the ecosystem.

### 3.3 Strings and text

See section 13 for the longer comparison. In short: `str` is an immutable
byte string, reference counted inside the runtime, and a copy type for the
language. It need not be valid UTF-8. `s[a..b]` is a substring that shares
the bytes (no copy), and is itself an ordinary `str`.

### 3.4 Arrays, slices, vectors

`[]T` is a growable array that owns its elements, inline, for every `T`. It
is both Rust's `Vec<T>` and, with `array(n, v)`, a way to get `n` copies.
There is no fixed-size array type like `[T; N]`. `&[T]` and `mut &[T]` are
slices (pointer and length): the same idea as Rust's `&[T]` and `&mut [T]`.
Indexing and slicing are bounds-checked (exit 3). `const` tables
(`const POW10: []u128 = [1, 10, 100]`) live in read-only static data and are
seen as `&[T]`.

```obx
var xs: []int = []
append(mut &xs, 3)
append(mut &xs, 1)
for x in xs { println(x) }
let tail = xs[1..]            // a slice, a borrow
```

```rust
let mut xs: Vec<i64> = Vec::new();
xs.push(3);
xs.push(1);
for x in &xs { println!("{x}"); }
let tail = &xs[1..];
```

`append`, `pop`, `swap`, `len`, `replace`, `move` and `array` are built-in
functions, not methods. Everything else (`insert`, `removeAt`, `retain`,
`dedup`, `reverse`, `sortBy`, `binarySearch`, `filter`, `mapTo`) is a
function in `collections`.

A difference with real consequences: two `mut` slices of disjoint ranges of
one array cannot be passed to one call (`any two indexes into the same array
overlap`). The compiler says so:

```text
'xs' is borrowed mutably twice in one call: a mut & argument may not overlap
another borrowed or moved argument
```

Rust's `split_at_mut` solves this with a little `unsafe` inside `std`. obx
has no equivalent today.

### 3.5 Records and structs; value and reference records

obx has two record kinds, chosen at the declaration.

```obx
type Point = value record { x: int; y: int }      // inline, copied
type Node = record { name: str; kids: []Node }    // boxed, owned
```

- `record` lives on the heap; a variable holds a pointer. It is always
  owned (moved, never copied), and may be recursive.
- `value record` is stored inline in variables, fields and elements. It is a
  copy type if every field is a copy type, otherwise it is moved.

Rust has one kind: a `struct` is inline, and you add `Box<T>` where you need
indirection (recursion, large values). `#[derive(Clone, Copy)]` decides
whether it is copied. In obx the placement is a property of the type, and
the compiler never makes you write the box. In Rust placement is a property
of each use; this is more flexible and also more to write.

A record literal names every field (there are no defaults and no `..base`
update form; the author's own notes list this as a pain point). Records can
be taken apart with `let P{a, b: x, c: _} = e`, where `e` must be a value
(use `move(x)` for a local).

obx tuples do not exist; use named records. Rust tuples and tuple structs are
common and idiomatic.

### 3.6 Enums, variants, sum types

```obx
type Shape = closed {
    Circle(float)
    Rect { w: float; h: float }
}
```

```rust
enum Shape {
    Circle(f64),
    Rect { w: f64, h: f64 },
}
```

`closed` is boxed and may be recursive; `value closed` is inline and sized by
its largest variant plus a tag. Variant names are written qualified
(`Shape.Circle(2.0)`), except the prelude's `Some`, `None`, `Ok` and `Err`.
A closed type whose variants have no fields gets built-in `==` and `!=`.
There are no explicit discriminants or casts to integers, and no `impl`
blocks (methods are free-standing `fn (s: &Shape) name()` declarations).

### 3.7 Option and Result

Both are plain library types, not compiler features:

```obx
type Option[T] = value closed { Some(T); None }
```

(`Result[T, E]` is `Ok(T)` / `Err(E)`.) The methods mirror Rust's: `isSome`,
`unwrap`, `expect`, `unwrapOr`, `map`, `mapErr`, `ok`, `err`. The README's list for
`Option` and `Result` has no `and_then` or `ok_or`, and there is no `try` on
`Option`: the compiler said `try needs a
closed type with variants Ok and Err (like Result), not Option[int]`. The
compiler knows exactly two things about them: `fromFraction` and that `try`
needs variants named `Ok` and `Err` (the lang items in spec section 15).

## 4. Pattern matching

obx `match` is on a closed value, or on an integer, `byte` or `str` with
literal (or constant) arms and a required `else`. An arm names a variant and
binds its fields one level deep; it can name several variants without
bindings. Arms must cover every variant or end with `else`.

```obx
fn area(s: &Shape): float {
    return match s {
        Shape.Circle(r) { 3.14159 * r * r }
        Shape.Rect(w, h) { w * h }
    }
}
```

```rust
fn area(s: &Shape) -> f64 {
    match s {
        Shape::Circle(r) => 3.14159 * r * r,
        Shape::Rect { w, h } => w * h,
    }
}
```

Verified: a `match` that omits a variant fails with `match on Op is not
exhaustive: missing Mul`.

What obx patterns do not have (per the grammar, section 3 of the spec):
nested patterns (a pattern is an identifier or `_`), guards (`if`), ranges,
or-patterns inside a pattern, `@` bindings, tuple patterns, `if let`,
`while let`, `let else`, and slice patterns. The match arms of a `match` on
a place borrow the fields (owned fields are bound as `&T`, copy fields as
copies); `match` on a temporary consumes it. Rust patterns are a much richer
language and are one of its most-liked features. For obx the cost is a
chain of nested `match` or `if` statements.

## 5. Ownership, moves, copies and drop

The two models agree on the main points:

- One owner per value. Using an owned value where a value is expected moves
  it; using it afterwards is an error.
- Copy types (primitives, function pointers, `value record`s of copy types)
  are copied. Everything else is moved.
- Values are dropped when their owner goes out of scope, in reverse order of
  declaration. Fields drop in reverse declaration order in obx (Rust drops
  fields in declaration order). Temporaries die at the end of the statement.
- A `drop` method is the destructor; it is how a wrapper closes a C resource.

```obx
type File = record { path: str }
fn (f: mut &File) drop() { println("closing " + f.path) }

fn main() {
    {
        let f = File{path: "a.txt"}
        println("using " + f.path)
    }   // prints "closing a.txt" here
}
```

```rust
struct File { path: String }
impl Drop for File {
    fn drop(&mut self) { println!("closing {}", self.path); }
}
```

Moving a value and then using it is rejected:
`'b' was moved and has no value here`.

Differences:

- **Only whole locals and by-value parameters can be moved out of.** Moving
  out of a field, an element, a borrow or a global is an error; use
  `replace(mut &p, v)`, `swap` or `pop`. Rust allows partial moves out of a
  struct you own and out of `Box`.
- **No drop flags.** Where paths join and a variable was moved on only some
  of them, the compiler drops it at the end of the paths that did not move
  it, and treats it as moved afterwards. Rust uses runtime drop flags for
  conditional initialisation.
- **No `Clone`, no `derive`.** There is nothing like `.clone()`. Copying an
  owned value is something you write. `T: copy` is a constraint for
  types that are bitwise copyable, library helpers like `copyOf` need it.
- **`str` is a copy type** even though it is heap data (reference counted).
  This removes the `String` / `&str` / `Cow` question (section 13).
- Moves are not allowed inside `&&` / `||` operands or `while` conditions.
- Long chains of boxed values (a linked list) are dropped without deep
  recursion, including when a `drop` method detaches the rest of the chain.
  In Rust a naive `Drop` on a long `Box` list overflows the stack.
- Globals of owned types are not allowed. Mutable state belongs to `main` or
  a context record passed down. Copy globals, constant tables, and set-once
  globals (`var CONFIG: once Config`, with `set`, `get`, `isSet`) exist. Rust
  has `static`, `static mut` (unsafe), `OnceLock`, `LazyLock`, and
  thread-locals.
- A leak check is built in: running with `OBX_CHECK=1` makes live objects at
  exit an error (exit 5). The tests run that way.

## 6. Borrowing

This is the centre of the design difference.

### 6.1 obx: second-class borrows and seven rules

A borrow `&T` (read) or `mut &T` (write) may appear as a parameter, a
receiver, a `let` binding or a function result, and as a field of a value
record or value closed type, an array element, or the argument of a type
parameter declared `borrow` (`Option[&str]`). A type that holds a borrow is
second class too: it cannot be a global, a field of a boxed type, a closure's
result, a task's argument, or a key or value of a `Map`. Because no borrow can
outlive the function that holds it, the compiler checks them within one
function. The rules (spec section 10):

1. **Second class.** Borrows and the types that hold them appear only in the
   places above; a value that holds borrows (not a plain borrow) may also be
   a `var`.
2. **Roots.** Every borrow expression has a root: the local or parameter its
   place starts from. Borrowing a global, or a temporary into a `let`, is an
   error. A value that holds borrows has the roots of everything in it (a join
   when it was built from several).
3. **Calls.** While a call's arguments are evaluated, places already borrowed
   or to be moved are held; a later argument may not mutably borrow, assign
   or move a place that overlaps. (Borrows of a receiver or argument are
   "taken at the call", so `p.node(K, p.block())` is accepted, which resembles
   Rust's two-phase borrows.)
4. **Frozen roots.** While a `let` borrow, or a variable holding borrows, is
   in scope, its roots cannot be assigned, moved or mutably borrowed.
   `for x in xs` freezes `xs` for the loop body.
5. **Results.** A function returning a borrow must have exactly one borrowed
   parameter, and every `return` must borrow a place rooted at it. A result
   that holds borrows may come from several such *sources*, and has the join
   of their roots at the call; a parameter that itself holds borrows lends
   what it holds, not itself (this is Swift's `copy` dependence, and why an
   iterator's items outlive the next call to `next`).
6. **Mutation.** Assigning through `p`, or calling a `mut &Self` method,
   requires `p` to be a `var`, or rooted at a `mut &` borrow.
7. **Variables.** A `var` holding borrows keeps the roots of the first value
   stored in it; every later store must borrow from them.

Rule 4 ends at the last use: a borrow freezes its root while its name occurs
later in its block, or anywhere in a loop around the point that it is
declared outside of, or while something live borrows it. (It was lexical, to
the end of the block, until the borrow-limits work; see
borrow-types.md 12.3.)

Examples, all compiled:

```obx
fn largest[T: Ord](xs: &[T]): &T {       // one borrowed parameter: fine
    var best = 0
    for i in 1..len(xs) {
        if xs[i].cmp(&xs[best]) > 0 { best = i }
    }
    return &xs[best]
}
```

```text
fn longer(a: &str, b: &str): &str { ... }
   -> longer returns a borrow, so it needs exactly one borrowed parameter, not 2

type Holder = record { name: &str }
   -> field name of Holder is &str, which holds borrows: only a value record
      or value closed type can hold them

var xs: []int = [1]
let first = &xs[0]
append(mut &xs, 2)
println(first)
   -> 'xs' is borrowed by 'first' and cannot be mutably borrowed while that
      borrow is in scope        (`first` is used again after the append)

for x in xs { if x == 2 { append(mut &xs, 9) } }
   -> 'xs' is borrowed by the for loop and cannot be mutably borrowed ...
```

Moving `println(first)` above the `append` makes the third example compile
(checked).

### 6.2 Rust: lifetimes and the borrow checker

Rust borrows are first class. A reference can be stored in a struct, held in
an iterator, returned, and its validity is expressed with lifetime parameters
(`fn longer<'a>(a: &'a str, b: &'a str) -> &'a str`). The checker uses
non-lexical lifetimes: a borrow ends at its last use. The signature is the
contract, so the check of a function body and of its callers are separate.

```rust
struct Tokens<'a> { rest: &'a str }          // stores a borrow: impossible in obx
impl<'a> Iterator for Tokens<'a> {
    type Item = &'a str;
    fn next(&mut self) -> Option<&'a str> { /* ... */ None }
}
```

### 6.3 What each can express that the other cannot

Only in Rust (not expressible in obx today):

- A struct or iterator that holds a borrow and outlives its creator's
  caller, or sits in a heap structure (`Box<Parser<'a>>`,
  `HashMap<&'a str, i32>`). obx's borrow-holding values are value records
  and arrays on the stack of the function that has the roots.
- A lending iterator (items that borrow the iterator), and a function that
  stores views of one parameter into another (`fn into<'a>(s: &'a str,
  out: &mut Vec<&'a str>)`): obx has no annotation to say so.
- A function returning a borrow of one of several inputs, with the lifetime
  relationships stated (`longer`). obx requires exactly one borrowed
  parameter.
- Borrowing from a global or `'static` data into a long-lived structure.
- Disjoint mutable borrows of fields via pattern matching, and of slice
  halves through `split_at_mut`. obx refuses two `mut` slices of one array.
- Liveness by control flow: obx counts a later use by name, so a use in a
  branch that cannot run after the conflict (`if c { xs.push(1); return }
  use(r)`) still freezes the root, as does a reassigned holder until its last
  assignment.
- Reference-holding closures that escape (`impl Fn + 'a`).
- Walking a linked structure by borrow: obx notes that `cur = &cur.next`
  is not possible since a borrow is never rebound, so such a walk recurses
  (PLAN.md, language backlog).

Only (or more simply) in obx:

- No lifetime syntax at all; error messages are about named variables and the
  one rule that failed. A reader never needs to write or read `'a`.
- Per-function checking is easier to reason about and cheaper to compile.
- A `for` loop over an array borrows it automatically (rule 4) without an
  iterator object.
- Types that hold borrows need no lifetime parameter: `type Token = value
  record { text: &str; kind: int }` and `[]Token` are checked through the
  roots of the values, not a `'a` in every signature.
- Borrows of a receiver and its arguments are "taken at the call", so
  `p.node(K, p.block())` is accepted. Rust's two-phase borrows accept the
  same shape for a method receiver (`v.push(v.len())`).

obx rejects more programs that Rust accepts; this is a deliberate cost (see
section 17). The language backlog in PLAN.md lists the cases the author hit
while writing the compiler: reading an element of an array of records into a
local needs a copy type, so the checker writes long `pg.types[d].tparams[i]`
paths in full (about 250 of them in the semantic analysis), and loops that
intern while walking a list copy the list first.

### 6.4 Borrow-class types

A type that holds borrows inherits the second-class rules (design note
`design/borrow-types.md`; spec sections 4.5, 6.1 and 10). A shared `&str` is
the string's header by value, uncounted, so a substring view costs no count:

```obx
type Token = value record { text: &str; kind: int }
type Lexer = value record { src: &str; pos: int }
fn (lx: mut &Lexer) next(): Option[Token] { ... }   // items borrow what lx borrows

var toks: []Token = []
for t in Lexer{src: &src, pos: 0} { append(mut &toks, t) }   // src frozen while toks lives
for line in strings.lines(&text) { ... }                       // views, not copies
```

```rust
struct Token<'a> { text: &'a str, kind: u8 }
struct Lexer<'a> { src: &'a str, pos: usize }
impl<'a> Iterator for Lexer<'a> { type Item = Token<'a>; fn next(&mut self) -> Option<Token<'a>> { ... } }
```

The obx version names no lifetime; the price is the limits listed in 6.3
(no heap containers of views, no lending iterators, no mixed sources) and
liveness by name rather than by control flow. Measured gain: a word-statistics program over 100 MB using
`strings.lines` and view-returning `fields` instead of counted pieces runs
-50% instructions (C backend) and -30% (native).

## 7. Generics and constraints versus traits

Both languages monomorphize and check generic code where it is written, not
at the instance. In obx:

```obx
fn largest[T: Ord](xs: &[T]): &T { ... }
fn max[T: Ord + copy](a: T, b: T): T { ... }
type Map[K: Eq + Hash, V] = record { ... }
```

```rust
fn largest<T: Ord>(xs: &[T]) -> &T { /* ... */ }
fn max<T: Ord + Copy>(a: T, b: T) -> T { /* ... */ }
struct Map<K: Eq + Hash, V> { /* ... */ }
```

Rules in obx (spec section 6):

- A type parameter is **owned unless constrained by `copy`**. In generic code
  `T` is moved, dropped and borrowed as an owned value, whatever it becomes
  later. Rust's default is the same in effect: a generic `T` is moved unless
  `T: Copy`.
- Its methods and operators are exactly those of its constraints. `a < b` on
  `T: Ord` calls `cmp`; on an unconstrained `T` it is an error at the
  definition. The compiler said: `T has no method 'toStr' (its constraints
  do not provide it)`.
- Inference works from arguments, then from the expected type. Type
  arguments that cannot be inferred are written in square brackets,
  `parse[int](s)` (Rust: `parse::<i64>(s)`). Methods may be constrained for one impl:
  `fn (m: &Map[K, V: copy]) get(k: &K): Option[V]` exists only where `V` is
  a copy type.
- Instantiation must terminate: `f[T]` calling `f[Option[T]]` is an error.
- No specialization, no blanket impls, no associated types, no const
  generics, no higher-kinded features, no `where` clauses with arbitrary
  predicates, no `impl Trait` in argument position, no variance or lifetime
  parameters. Rust has all of these except specialization (which is
  unstable).

Because type parameters can be constrained by associated functions, obx
types can carry behaviour without data:

```obx
type Currency = interface { fn code(): str; fn scale(): int }
type USD = value record { }
fn (USD) code(): str { return "USD" }        // associated function
fn (USD) scale(): int { return 2 }
```

`Money[C: Currency]` calls `C.scale()`; in Rust the same shape is a trait
with associated functions or associated consts and a `PhantomData<C>`
field.

Each distinct instance is compiled once per program with its own code and
drop glue (as in Rust).

## 8. Methods, interfaces and trait objects

### 8.1 Methods

A method is a function with a receiver, declared outside the type:

```obx
fn (a: mut &Account) deposit(n: int) { a.balance += n }
fn (p: &Point) norm2(): int { return p.x * p.x + p.y * p.y }
fn (Decimal) parse(s: str): Result[Decimal, DecError] { ... }   // associated
```

The receiver mode is explicit: by value, `&T`, or `mut &T`. A receiver with no
name declares an associated function (`T.name(...)`). Methods may be declared
only in the module that declares the type; `std` declares the methods of the
primitives. Rust has `impl` blocks, trait methods, inherent methods in the
same crate, and extension traits for foreign types.

Operators are methods: `a + b` is `a.add(b)`, `a == b` is `a.eq(&b)`,
`a < b` is `a.cmp(&b) < 0`. This is how `Decimal` and `Money` get operators
in library code. It is the same idea as Rust's `Add`, `PartialEq` and
`PartialOrd` traits, without needing the trait to be named.

### 8.2 Interfaces

```obx
type Ord = interface { fn (&Self) cmp(other: &Self): int }
```

A type satisfies an interface **structurally**: it has methods of the same
names, parameter and result types, and receiver modes. There is no `impl`
declaration. `std` defines `Eq`, `Ord`, `Hash` and `Show` and implements
them for the primitives. A user type satisfies `Ord` by declaring
`fn (p: &Person) cmp(o: &Person): int`.

This is closer to Go's interfaces than to Rust's traits. A consequence: a
type can satisfy an interface by accident, and there is no way to say "this
type is meant to implement X" (no `derive`, no `impl` that the compiler
checks against the interface at the declaration).

### 8.3 Interface values versus trait objects

**obx has no interface values.** An interface can only constrain a type
parameter:

```text
type Shape = interface { fn (&Self) area(): float }
let s: Shape = Sq{s: 2.0}
   -> Shape is an interface: it can only constrain a type parameter
```

`box I` and `&I` (dynamic dispatch) are listed as **planned**
(spec section 13). They are to be designed together with escaping closures.
Until then, obx programs use closed types (`match` over variants) for a
fixed set of shapes, and function values for a single behaviour. The
compiler itself needs no interface values, as the plan notes: AST walks are
`match`. The standard library has no `Read`/`Write` traits for the same
reason (design/concurrency.md: "no `io.Reader`/`Writer` interfaces (the
language has no interface values yet)").

Rust has `dyn Trait` with `Box<dyn Trait>`, `&dyn Trait`, `Rc<dyn Trait>`,
object safety rules, and vtables. This is a real gap for plug-in style code
in obx.

## 9. Closures and lambdas

obx has two function-like types:

- `fn(A, B): R`: a pointer to a top-level function (or an instance of a
  generic one). A copy type. Can be stored, returned and passed to tasks.
- `&fn(A, B): R`: a **closure**: it may use the surrounding locals. It is a
  borrow, second class like all borrows: a parameter or a `let`, never stored,
  returned or used as a type argument. It costs no allocation.

```obx
fn countIf[T](xs: &[T], pred: &fn(&T): bool): int {
    var n = 0
    for x in xs { if pred(x) { n += 1 } }
    return n
}

let limit = 18
println(countIf(&people, fn(p) { return p.age >= limit }))   // 2

var total = 0
eachN(4, fn(i) { total += i })                                // total is 6
```

```rust
fn count_if<T>(xs: &[T], pred: impl Fn(&T) -> bool) -> usize {
    xs.iter().filter(|x| pred(*x)).count()
}
let limit = 18;
count_if(&people, |p| p.age >= limit);
```

Capture is always by borrow: a variable the closure assigns is captured
mutably and must be a `var`; the rest as shared borrows. A closure cannot
move a captured variable and cannot capture a global by borrow. While the
closure is in scope its captured roots are frozen: this is rejected,

```text
var g = 0
let h = fn(x: int): int { return x + g }
g = 5
   -> 'g' is borrowed by closure 'h' and cannot be assigned while that borrow
      is in scope
```

and a closure cannot be returned:

```text
fn adder(n: int): &fn(int): int { ... }
   -> a closure type cannot be a result: closures are second class (a
      parameter or a let)
```

Rust has the full set: `Fn`, `FnMut`, `FnOnce`, capture by move, closures
stored in structs, returned as `impl Fn`, or boxed as `Box<dyn Fn>`. Escaping
closures (with an owned environment) are **planned** in obx, together with
interface values. Because closures are second class, tasks take plain `fn`
values and an owned argument rather than a closure (section 14).

## 10. Iterators

The loop forms are:

- `for i in a..b`: counts over `int`.
- `for x in xs`: walks an array or slice; `x` is a copy of each element if the
  element is a copy type, and a `&T` borrow otherwise. The array is frozen
  for the loop.
- `for x in it`: any value with a `fn (mut &Self) next(): Option[T]` method
  (spec section 8). An iterator that holds borrows (`strings.lines(&text)`,
  `m.iter()` over a `Map`, a user `Lexer`) freezes what it borrows for the
  loop, and its items borrow that, not the iterator, so they can be kept.
- `while`.

Bulk operations are library functions that take a closure: `collections.filter`,
`mapTo`, `findBy`, `count`, `any`, `all`, `retain`, `sortBy`; `Map.each` and
`Set.each`. They build whole arrays; nothing is lazy. There is no library
`zip`, `enumerate`, `chain`, `take_while` or `collect`. Adapters that store
only an iterator can be written by users with an interface constraint on a
`borrow` type parameter (`type Enumerate[I: borrow + Iter[X], X: borrow]`,
lib/tests/run/borrow_types_enumerate.obx; `X` must be written at the call,
`enumerate[Words, &str](...)`). Lazy `map` and `filter` would store a
closure, and closures are second class: not possible.

```obx
let ages = [10, 20, 30]
let adults = collections.filter(&ages, fn(a) { return a >= 18 })   // [20, 30]
```

An iterator over a collection is a value that holds a borrow of it; such a
value is borrow-class (section 6.4) and lives only as long as its roots.

In Rust, iterators and their adapters (`map`, `filter`, `zip`, `rev`, `fold`)
are the usual way to write loops, are lazy, compose, and compile to the same
code as hand loops in most cases.

## 11. Error handling

obx has two kinds of failure:

- **Values.** `Result[T, E]` and `Option[T]` are ordinary closed types.
  `try e` yields the `Ok` value or returns `Err(e)` from the function; it
  may appear anywhere in an expression, and owned temporaries already built
  are dropped before the return.
- **Stops.** An overflow, division by zero, bad index, `pop` of an empty
  array, stack overflow, `panic(msg)` or a failed `assert` stops the program
  with a message and a specific exit code: 10 for arithmetic, 3 for index,
  4 for stack overflow, 12 for panic/assert, 2 for out of memory (spec
  section 14). There is no way to catch a stop.

```obx
fn parse(s: str): Result[int, str] {
    match parseInt(s) {
        Some(n) { return Ok(n) }
        None { return Err("not a number: " + s) }
    }
}

fn twice(s: str): Result[int, str] {
    let n = try parse(s)
    return Ok(n * 2)
}
```

```rust
fn parse(s: &str) -> Result<i64, String> {
    s.parse().map_err(|_| format!("not a number: {s}"))
}

fn twice(s: &str) -> Result<i64, String> {
    let n = parse(s)?;
    Ok(n * 2)
}
```

(Run output for `twice("21")` was `42`, for `twice("zz")` it was
`not a number: zz`.)

Differences from Rust:

- `try` works on `Result` only, and the error type must be **the same** `E`
  as the function's. There is no `From` conversion; use `r.mapErr(f)`:
  `try r.mapErr(toError)`. Rust's `?` converts through `From`, which makes
  `Box<dyn Error>` and `thiserror`/`anyhow` patterns easy.
- `try` is a prefix operator in obx (`try parse(s)`) and applies to the whole
  expression after it, so a chain such as Rust's `a()?.b()?` is written with
  a `let` between the steps.
- There is no `Option` + `try` form.
- No unwinding and no `catch_unwind`. A panic in a task stops the program.
  The spec does not describe panic running destructors.
- There is no `Error` trait and no backtraces; errors are plain values such
  as `IoError`, `DecError`, `ParseError`, `Cancelled`.
- Runtime stops are always on in obx; Rust's overflow check and some others
  depend on the profile.
- The library supplies "checked" variants (`checkedAdd` returning `Option`,
  `Decimal.addExact` returning `Result`) for input-derived operands.

## 12. Compile-time evaluation and macros

**const.** `const` names an integer, float, bool, string or fractional literal,
or an operator expression over constants. Overflow in a constant is a compile
error. A `const` may also be an array literal of constants, or a record or
variant literal of constants, of a copy type (a constant table, placed in
read-only static data and seen as `&[T]`). `match` arms can name constants.
There is no `const fn`, no compile-time function calls and no const
generics. Rust's `const fn` can run a
large subset of the language at compile time and is used for lookup tables,
`const` generics and type-level work.

**Macros.** obx has no macros: no declarative macros, no procedural macros,
no attributes, no `derive`. `println`, `assert` and `panic` are functions (the
built-ins are listed in spec section 11). Printing takes one `Show` argument
and building text is done with `+` and `toStr()`; there is no format string.
SQL and HTML "templates" are **planned** as a desugaring onto a library
protocol (spec section 13). Rust's `println!`, `vec!`, `format!`, `derive`,
and crates built on proc macros (serde, clap) are a large part of everyday
Rust. obx replaces some with library functions and loses the rest.

A related feature, **implicit call sites**: a library function whose last
parameter is a `std.Where` may omit it, and the compiler passes the call's
`"file:line"`. The task library uses it to name the user's line when a task
deadlocks. Rust achieves this with `#[track_caller]`.

## 13. Strings and text

|  | obx `str` | Rust |
|---|---|---|
| Representation | immutable byte string, reference counted | `String` (owned, growable), `&str` (borrowed view) |
| Encoding | bytes; UTF-8 not required | valid UTF-8 always |
| Copy | a copy type; copying never copies bytes | `Clone` copies the bytes |
| Substring | `s[a..b]` shares the bytes, is an owned `str` | `&s[a..b]` borrows, must be on char boundaries |
| Indexing | `s[i]` is a `byte`; out of range stops | no `s[i]`; `.as_bytes()[i]`, `.chars()` |
| Building | `+`, `StrBuf` | `String`, `push_str`, `format!` |
| Comparison | bytewise `== < >` | bytewise on UTF-8 |
| Max size | 4 GiB - 1 | `isize::MAX` |

```obx
let s = "hello, world"
let w = s[7..]                      // "world", shares s's bytes
let parts = strings.split(&s, ", ")                   // views of s
println(strings.join(&strings.owned(&parts), "|"))   // hello|world (join takes &[str])
```

```rust
let s = "hello, world";
let w = &s[7..];
let parts: Vec<&str> = s.split(", ").collect();
println!("{}", parts.join("|"));
```

Because a substring is an ordinary `str`, a function returning a piece of its
input does not need a lifetime and the piece can be stored in a record or
returned, which Rust expresses with `&'a str` or `Cow`. The cost is the
count: copying a `str` increments a reference count and dropping decrements
it, and (an inference from "freed with its last copy") a short substring
keeps the whole parent buffer alive. Rust's `&str` has no run-time cost and no
retention, and in exchange a struct holding one is tied to a lifetime.

Text support is in `strings`: `decodeAt`, `runes`, `runeCount`, `fromRune`,
`isValidUtf8`, `lossy`, `displayWidth`. Source files are UTF-8, but outside
comments and literals the source is ASCII (identifiers are ASCII). `strlen("héllo")` through C was
6 (the bytes of the string). Rust's `char` is a Unicode scalar; obx has no `char`
type, a `'a'` literal is a `byte`, and runes are `int`.

## 14. Collections

| Collection | obx | Rust |
|---|---|---|
| Growable array | `[]T` (built in) | `Vec<T>` |
| Hash map | `Map[K: Eq + Hash, V]` in `std` | `HashMap` |
| Hash set | `collections.Set[T]` | `HashSet` |
| Heap | `collections.Heap[T: Ord]` | `BinaryHeap` |
| Sorted map/set | none | `BTreeMap`, `BTreeSet` |
| Deque | none | `VecDeque` |
| Linked list | write one with `record` and `Option` | `LinkedList` |
| Sorting | `sort` (heap sort, in place, not stable) in std; `collections.sortBy`/`sortStable` (stable merge) | `sort`, `sort_unstable`, `sort_by` |

`Map` keeps insertion order, uses open addressing, and hashes strings and
128-bit integers with SipHash-1-3 under a per-process key (as Rust's std does)
to resist hash flooding. Lookups take borrowed keys: `m.has(&k)`,
`m.get(&k): Option[V]` (only for copy `V`), `m.at(k): &V`, `m.atMut(k)`,
`m.atOrPut(k, d)`, `m.remove(&k)`. There is no `entry` API; `atOrPut` is the
closest. A record used as a key needs hand-written `eq` and `hash` methods
(no `derive(Hash)`).

```obx
var m: Map[str, int] = newMap()
m.put("a", 1)
println(m.getOr(&"a", 0))                              // 1
match m.get(&"zz") { Some(v) { println(v) } None { println("none") } }
```

```rust
let mut m: HashMap<String, i64> = HashMap::new();
m.insert("a".into(), 1);
println!("{}", m.get("a").copied().unwrap_or(0));
```

There is no JSON, no `serde`, no CSV module in the library today. The library
does have `regex` (RE2 syntax, a port of Go's `regexp` with a lazy DFA),
`http` client and server, `net`, `url`, `time`, `fs`, `io`, `proc`, and
bindings to SQLite, PostgreSQL and SQL Server.

## 15. Concurrency

**obx** has no threads in obx code (spec section 13, "a shared-nothing
decision, for safety and for performance"). The model:

- **Tasks.** The `task` library runs stackful coroutines on one thread,
  scheduled cooperatively with an `epoll` loop for sockets and timers. Every
  task starts on a `task.Scope`, and the scope returns only when all its
  tasks have finished (structured concurrency). A task runs until it *parks*
  (`wait`, `yield`, `sleep`, a socket call that would block). A task that
  never parks keeps all others waiting.
- **Futures and promises.** `s.start(f, arg)` returns an owned `Future[T]`;
  dropping it cancels the task. `Promise[T]` is a future completed by hand.
  Cancellation and deadlines (`task.within(ms, f)`) are cooperative and show
  up as `Err(Cancelled...)` from the next call that can park.
- **Shared state.** `Shared[T]` is a counted handle; `sh.with(fn(x: mut &T)
  {...})` is the only access, and nothing else runs during `with`. Parking
  inside `with` stops the program (exit 13). `Frozen[T]` lends only `&T`.
- **No channels** (dropped on purpose; they would be library code on `Shared`
  and `Promise`).
- **Cores.** `task.workers(n, f)` forks `n` worker processes under a
  supervisor that restarts crashed workers with backoff, forwards
  `SIGTERM`, and shares one accept queue (`SO_REUSEPORT`/`EPOLLEXCLUSIVE`).
  Workers share no memory.
- **Blocking C calls.** An `extern ... blocking` function runs on a pool of
  helper threads (default 4) while the task parks. Those threads run only C.

```obx
import "task"

type Counter = record { n: int }
type Job = value record { c: task.Shared[Counter]; step: int }

fn work(j: Job): int {
    for i in 0..3 {
        j.c.with(fn(c: mut &Counter) { c.n += j.step })
        task.yield().unwrap()
    }
    return j.step
}

fn main() {
    let c = task.shared(Counter{n: 0})
    task.scope(fn(s: mut &task.Scope) {
        s.spawn(work, Job{c: c, step: 1})
        s.spawn(work, Job{c: c, step: 10})
    })
    c.with(fn(x: mut &Counter) { println(x.n) })     // 33
}
```

(Compiled with `--native` and run: it printed `33`.)

The data-race rule is therefore structural rather than a pair of marker
traits: a task is started with a *plain function value* and one *owned*
argument. A closure and a borrow are rejected by the ordinary rules (a
closure is not a `fn` value; a borrow is not a type argument). So a task
reaches nothing of its starter's except what was moved into it, and
`Shared`/`Frozen` values.

**Rust** has OS threads (`std::thread`, scoped threads), channels
(`mpsc`), `Mutex`, `RwLock`, atomics, and the `Send`/`Sync` marker traits
that make data races a compile error across threads. `async`/`await` is a
language feature with a runtime in a crate (tokio, async-std), and async
functions are state machines that introduce function colouring. Rust gets
all cores in one process; obx uses processes.

Assessment:

- obx's model is simple to state and has no function colouring: any function
  can park if it calls a parking library function, and `Where` makes the
  deadlock and misuse reports name the user's line. The costs: one core per
  process, a CPU-bound task starves every other task in the process, and
  parking inside `with` is a run-time stop, not a compile error (the spec
  says it becomes a compile error when the compiler has effect summaries,
  which is **planned**).
- Rust's model is far more general (parallel computation, lock-free data
  structures, rayon) and proves more at compile time, at the price of
  `Arc<Mutex<_>>`, `'static` bounds on spawned closures, and `Pin` in async.
- Measured costs from PERFORMANCE.md (ns per operation, native): task
  switch 12.5, start a task 21, `Shared.with` 6.4. Via C a switch is 6.5.

## 16. Unsafe code and C interop

obx has no `unsafe` keyword. The safety promise "covers obx code that uses no
externs of its own" (PLAN.md): a module that declares its own `extern` takes
responsibility for what happens. To keep that local:

- An `extern` declaration is **always private** to its module (`pub extern` is
  an error). A module that needs C exposes safe wrappers, usually an owned
  type with a `drop` method that closes the C resource.
- Parameter and result types are limited to integers, `float`, `bool`, `ptr`,
  `mut &ptr`, `mut &int`, `str`, and arrays or slices of numbers (passed as a
  pointer valid for the call). `ptr` is an opaque copy type with only `==`,
  `!=` and `isNull`. A `str` argument is passed NUL-terminated and stops the
  program if it contains a NUL. A `str` result copies the C string.
- Varargs (`printf`), C callbacks (`extern fn(ptr, ptr): i32`, a top-level
  function only) and `blocking` calls (section 15) are supported.
- Two library-only intrinsics, `intoPtr` and `fromPtr`, exist only in modules
  loaded from a library root; elsewhere the names are ordinary identifiers.
- The spec describes no header parsing or binding generator; declarations
  are written by hand.
- Calls to extern functions are unchecked.

```obx
extern "c" fn getpid(): i32
extern "c" fn strlen(s: str): int

fn main() {
    println(getpid() > 0)        // true
    println(strlen("héllo"))     // 6
}
```

```rust
unsafe extern "C" {
    safe fn getpid() -> i32;           // `safe` items: edition 2024
    unsafe fn strlen(s: *const std::ffi::c_char) -> usize;
}
```

Rust's `unsafe` is a general escape hatch inside a safe language: raw
pointers, `transmute`, unions, `static mut`, FFI, and `unsafe impl` for
`Send`. It is scoped to blocks and functions, and the ecosystem has audit
tools. obx provides less and marks nothing (the author decided against
marking unchecked code, PLAN.md "Deferred"); in exchange the whole
surface is a handful of rules. The cost is that there is no way to write a
low-level data structure in obx itself without C or the library intrinsics.
The standard library's own C-facing code is in `ffi.obx`, `sqlite.obx`,
`postgres.obx` and `mssql.obx`.

## 17. Tooling and ecosystem

|  | obx | Rust |
|---|---|---|
| Compiler | `obxc`, self-hosted; C via gcc, or `--native` ELF | `rustc` (LLVM) |
| Build | `obxc file.obx -o out`; imports found from the file and library roots | `cargo build` |
| Package manager | none; library roots (`-L`) only | cargo, crates.io, lockfiles, features |
| Tests | no in-language test framework; suites run by Python scripts (`// expect:`, `// exit:`, `// error:` comments); `OBX_CHECK=1` leak check | `#[test]`, `cargo test`, doc tests |
| Formatter | none | `rustfmt` |
| Linter | none | `clippy` |
| Docs | Markdown design notes | `rustdoc` |
| IDE support | none documented | rust-analyzer |
| Debugging | none documented for native output | gdb/lldb with debuginfo |
| Cross-compilation | native backend is x86-64 Linux ELF only | many targets |
| Bootstrap | C seed (`seed/obxc.c`) + gcc builds the compiler | needs an earlier `rustc` |
| Compile errors | one error, then stop (`file:line: message`) | many, with spans and suggestions |

The library is 24 modules and about 22,000 lines of obx (regex and its
generated Unicode tables are about a third of that); `std` is the prelude. Programs shipped in `obx/cmd`: `eza` (1,609 lines, matching eza
0.23.3 output byte for byte on a golden suite), `grep`, `cat`, `wc`, `sort`,
`head`, `tail`, `zoxide`, an HTTP/1.1 server and a load client. The compiler is
23,710 lines of obx (PERFORMANCE.md). The ecosystem is the repository.
Rust's is hundreds of thousands of crates.

The build is deliberately simple: a program is a file plus its imports.
Native executables are static with no libc unless the program calls C
libraries; an empty program is 12,288 bytes natively and a hello program is
16,416 bytes (PERFORMANCE.md).

## 18. Performance model

What the two languages guarantee at run time:

- **Checks.** obx always checks array and string bounds and integer
  arithmetic, and the checks cannot be turned off. The native backend removes
  those it can prove (loop conditions, passed checks, induction variables).
  Rust checks bounds always (removed when provable) and overflow only in debug
  builds by default.
- **Allocation.** Boxed `record`s and `closed` values are always on the heap in
  obx. Rust puts structs on the stack unless boxed. The native backend has
  a size-class allocator with no locks.
- **Reference counts.** `str` is counted and so is `Shared`. Every other
  value is single-owner and has no count. Copying a `str` costs a count
  update; Rust's `&str` costs nothing and `String::clone` copies bytes.
- **No collector, no pauses.** Same as Rust.
- **Monomorphization.** Both. Code size grows with instances.
- **Backends.** `--native` is obx's own x86-64 backend (no gcc, no libc); the
  default path is C compiled with `gcc -O2`. The two are held to identical
  behaviour on 1,616 programs. Rust uses LLVM, which does more optimization
  (vectorization, inlining across crates, link-time optimization).

Numbers stated in PERFORMANCE.md (host-dependent, AMD Ryzen 7 8745HS, WSL2;
these compare obx's two backends, and the only comparison to Rust is the
regex engine):

| benchmark | via C | native | native/C |
|---|---:|---:|---:|
| fib (recursive calls) | 331 ms | 330 ms | 1.00 |
| loop (two checked ops per iteration) | 267 ms | 213 ms | 0.80 |
| array (checked indexing) | 104 ms | 91 ms | 0.88 |
| alloc (20 million records allocated and freed) | 122 ms | 65 ms | 0.54 |
| strings (concatenation and `toStr`) | 76 ms | 47 ms | 0.62 |
| sieve (to 10 million, ten rounds) | 182 ms | 127 ms | 0.70 |

On the sieve the document reports a hand-written unchecked C version at
135 ms, so the checked native obx matches plain C there.

- **Against Rust's `regex` crate 1.12.3**, obx's port of RE2 takes about 1.3
  to 2.6 times Rust's time through C, and 2.0 to 4.0 times natively, on most
  patterns of the table, and about half Rust's time on `(?m)^ERROR .*timeout$`
  (PERFORMANCE.md "Regex"). The stated reasons: Rust's
  `memchr` uses AVX2 and the runtime SSE2, and per-match costs.
- **Against GNU grep 3.11 and ripgrep** (obx `cmd/grep.obx`): ahead of GNU on 19 of 21
  cases, behind ripgrep wherever a literal can be found with SIMD.
- The compiler compiling itself with `--native` takes 472 ms when built by gcc
  and 529 ms when built natively; a full build through gcc takes 17 s.

There is no published obx-versus-Rust comparison of general code outside regex
and grep, so no broader claim is made here.

## 19. What obx has that Rust doesn't

- A decimal type with exact arithmetic, rounding modes, and a decimal
  literal syntax, plus `Money[C]` with the currency in the type. All is
  library code. (Rust: crates.)
- Always-on arithmetic and conversion checks, with a distinct wrapping
  operator spelling (`+%`).
- No lifetimes: borrow errors are about named variables, and there is
  no lifetime syntax to learn.
- Reference-counted strings that are copy values, with free substrings, so
  text code rarely has to choose between owned and borrowed.
- A choice of record placement (`record` boxed, `value record` inline) at
  the type, not at each use.
- `try` anywhere in an expression, with the already built owned temporaries
  dropped on the early return.
- Built-in leak checking in tests (`OBX_CHECK=1`).
- Stackful coroutine tasks with structured scopes, deadlines and
  cancellation, and no `async`/`await` colouring; worker processes under a
  supervisor in the library.
- A native backend that produces small static executables without gcc or
  libc, and a compiler that compiles itself in half a second.
- Implicit `Where` call-site argument, used by library functions to report the
  caller's line.
- Set-once globals (`once`) and constant tables as language features.
- Hash flooding resistance by default in `Map`.
- A very small language: the spec is about 1,200 lines.

## 20. What Rust has that obx doesn't

- First-class borrows and lifetimes: structs and iterators that hold
  references in heap structures or beyond their creator, lending iterators,
  borrow regions by control flow, and signatures that relate several
  lifetimes.
- Traits with explicit `impl`, associated types and consts, blanket impls,
  const generics, `impl Trait`, trait objects (`dyn`), operator traits, and
  `derive`.
- Closures that escape (`Box<dyn Fn>`, `impl Fn`, `move`), and
  `Fn`/`FnMut`/`FnOnce`.
- Lazy iterator adapters that take closures, and a library of adapters.
- Rich patterns: nested, guards, ranges, `if let`, `while let`, `let else`.
- Macros (declarative and procedural), attributes, `derive`, `format!`.
- `const fn` and compile-time computation.
- Threads, `Send`/`Sync`, atomics, locks, channels, `async`/`await`.
- `unsafe` and raw pointers for writing low-level code in the language.
- Many integer and float widths, `usize`, `char`, tuples, fixed-size
  arrays, `Cow`, `Rc`/`Arc`/`Cell`/`RefCell`, `Box<dyn Error>`.
- A package manager, formatter, linter, test framework, documentation tool,
  IDE support, debugger integration and cross-compilation.
- An ecosystem and a decade of stability; a mature optimizer (LLVM).
- Several errors reported at once, with spans and suggestions.
- Unicode-aware `char` and `String` APIs, with UTF-8 guaranteed.

## 21. Where obx's simpler model is a deliberate trade-off

The obx documents give reasons for most of the places where it does less than
Rust. Seen as trade-offs:

1. **Second-class borrows.** Only second-class values hold borrows, so
   nothing needs a lifetime, and the checker is seven rules applied to one
   function. The price is the programs rejected in section 6.3 and a library
   without lazy adapters. The author found the price real while writing the
   compiler (PLAN.md backlog items 7, 8, 10 and 11); borrow-class types
   (section 6.4) buy back views, token streams and iterators without
   lifetimes.
2. **Counted strings.** One string type, copy semantics, free substrings.
   The price is a reference count on copy and parent retention by
   substrings, against Rust's free `&str` and its lifetime parameters.
3. **Structural interfaces, no interface values (yet).** Small and flexible;
   no `impl` ceremony and no orphan rule. The price is accidental
   satisfaction, no dynamic dispatch, and no stored closures, which are
   **planned** for the same design step.
4. **Checked arithmetic always.** Overflow is a bug, so it is a stop. Wrapping
   is spelled differently. The price is some speed, which the native backend
   recovers by proving checks away.
5. **No threads in obx code.** A shared-nothing decision. It removes data
   races, locks and function colouring; the cost is that one process uses one
   core for obx code, and several cores means several processes
   (design/concurrency.md argues this is also the faster architecture for
   servers).
6. **No `unsafe`, extern hidden in modules.** The unsafe surface is small and
   always behind a wrapper type with a `drop` method. The price is that
   low-level code needs C, not Rust-style `unsafe`.
7. **Library, not compiler.** Option, Result, Decimal, Money, Map and
   formatting are library code over a handful of lang items. The design
   note (generic-core-rethink.md) records the reason: each type that was a
   compiler feature in the predecessor language had to be wired through every
   place a value can appear. The price is that obx has no macro system to
   remove the boilerplate the library then needs (hand-written `eq`, `hash`,
   `cmp`, `toStr`).
8. **Small tooling.** One compiler, one error at a time, no package manager.
   This follows the project's choices (self-hosted, a C seed so that gcc is
   the only dependency) and is an actual gap for anyone coming from cargo.

Nothing here is a claim that one language is better. Rust optimises for
expressiveness inside the safe subset, and pays in concepts; obx optimises
for a language that fits in one head and a compiler that fits in one
repository, and pays by rejecting or lacking some programs that Rust
handles.

## Appendix: how obx claims were verified

Programs compiled and run at `624b22f` with `OBX_LIB=obx/lib`
`obx/compiler/out/obxc` (built from the C seed with `compiler/build.sh`):
records, closed types, match, `try` and `Result`, `Map`, generics
with `Ord`, closures, a mutable capture, substrings, `strings.split`/`join`,
decimals and floats, `const` tables, `once` globals, a `drop` method,
destructuring, extern `getpid`/`strlen`, and a task program with `Shared`
(also native). Programs that the compiler rejected, with the message quoted
above: a borrow field, a use after move, a borrow frozen across `append`,
a result borrowing two parameters, an interface value, a closure result, a
mixed `i32 + int`, a non-exhaustive match, an unconstrained generic method
call, a mutation inside `for`, two `mut` slices of one array, `try` on an
`Option`, and an assignment under a closure borrow.

Not verified by a program: the claim that a substring keeps its parent
alive (read from "shares the bytes" and "freed with its last copy"); that
a panic skips destructors (the spec does not say); the exact final shape of
the `borrow-types` proposal (now design/borrow-types.md and spec section 10).
