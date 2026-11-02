Part of the obo language reference. Index and core syntax: [LANGUAGE.md](../LANGUAGE.md).

## 6. Modules

Modules: one source file is one **module**, imported modules are compiled
dependency-first into the same image, and importers reach exported declarations
as `alias::name`. Each module also emits a text **symbol file** (`.sym`) holding
its exported interface, hashes and versions; an importer reads that interface and
`--check` can check a module against symbol files alone, including dependencies
whose source is absent. A full build still compiles every module body from source:
this is interface-level separate compilation, not object files or a linker. The
compiler itself stays one file and uses no imports.

### Headers, files and identity

A file may start with `module Name` (comments and blank lines first are fine).
An imported module **must** have a header, and its file must be named
`Name.obx`; the command-line file is the root and is exempt from the naming rule.
A headerless root is internally `_root`; `_root` is reserved and an imported
module may not declare it. Module identity is the **lexically normalized
resolved path**: repeated `/` collapse, `.` goes, `..` pops the previous
segment, a leading `/` stays, and symlinks are not resolved. So `lib.obx` and
`./lib.obx` are one module (compiled and initialized once), while two files
declaring the same `module Name` are the duplicate-name error. Symlinks are not
resolved: two spellings through different links are different identities, so if
both are reached the duplicate-name check reports the same declared name at two
paths. Use canonical module trees.

### Imports and aliases

```text
import "path.obx"
import "path.obx" as alias
```

The header comes first, then all imports, then declarations. An import after a
declaration is an error. A path is resolved against the importing file's
directory first, then each `-I DIR` in command-line order; an absolute path is
used as written, and the missing-module error lists the paths tried. The import
path must end in `.obx`, and its basename must match the declared module name.
The default alias is the declared module name; `as` overrides it. Everything is
qualified: there are no unqualified or selective imports and no re-exports.
Duplicate imports are allowed (the module is compiled and initialized once; two
aliases to it are fine). An alias may not repeat, clash with a top-level
declaration, or be a built-in name; a local may have an alias's name, since
`alias::` is always the module.

Module aliases live in their own namespace. A qualified name resolves the alias
to its module and then the member in that module's exports; a name that is not an
import alias is the error `'<x>' is not a module`, even if a local, global or
type has that name.

### Qualified names

`alias::name` reaches exported functions, types, variants, constants, globals and
record constructors: `lib::make(1)`, `new lib::Node{...}`, `new []lib::Node(0)`,
`?lib::Node`, `^lib::Node`, `Result[lib::Node]`, `lib::Limit`, `&lib::Cache`.
`match` arms stay bare (`Circle { ... }`), because the subject's closed type
names them; writing `lib::Circle` in an arm is refused with
`match arms name a variant without its module: write Circle`.

### Exports and privacy

Everything is private unless marked `pub`; `pub` is allowed only on top-level
`fn` (including a method), `type`, `const`, `var` and `let`. It is sticky across a forward heading and
its definition: the symbol is exported if either declaration says `pub`.
Importers see only the exports; reaching a private function, type, global or
constant reports `module '<m>' has no exported member '<x>' (only 'pub'
declarations are visible)`.

Exporting a closed type exports its variants, so an importer may write
`new lib::Circle{...}`. Exporting a record type exports only the type. A method
is exported only if it is `pub`; exporting a method also requires its receiver
type to be exported.

Record fields have their own visibility, private by default: `pub` before a field
name makes that field accessible to importers, and a mixed record may expose some
fields and hide others. Inside the defining module all fields are ordinary.
Outside it, a private field cannot be read, written, borrowed, moved, `take`n,
`replace`d or named in `new T{...}`. External `new T{...}` is allowed only when
**all** fields are public; a record with any private field requires a constructor
function in its module. A zero-field record is vacuously all-public and may be
constructed externally. Closed-variant fields stay public in v1; `pub` on one is
the exact error in section 10.

Field visibility is applied at the field designator, not at the spelling: a
nested path is checked segment by segment, arrays follow their element type, and
an alias or a global does not bypass the rule. A `pub` field of a private named
type is still the closure error below; a **private** field may use any private
named type because it is not part of the interface.

An exported declaration may not mention a **private** named type of its own
module, directly or through `[]`, `?`, `^`, `Result`, public record fields or a
closed-type variant's fields. Private fields are skipped by this check:

`exported 'Shape' uses private type 'Secret' (variant 'A'); mark 'Secret' pub too`

A private type may mention exported types freely; imported types are fine
because their own module exported them. Type aliases are transparent: `type A =
lib::T` names the one object `lib::T`, and does not create or rename a type.

### Initialization, main and teardown

Imports are compiled at the import statement, before the importer's
declarations. Initializers run dependency-first, depth-first in import order,
once per module (a diamond runs the shared module once); the root's `main`
runs after every initializer. Only the root may declare the plain `fn main`; a
method named `main` is allowed and is not an entry point, and a dependency's
plain `main` is refused (`module '<m>' may not declare 'main'; the program root
provides it`). The root needs `fn main()` with no parameters or result.
After `main` returns, every module's owned and string globals are freed in
compile order (dependencies first), each exactly once, including a global of a
type from another module; then the runtime finishes. `OBO_CHECK=1` therefore
reports no leaks and no double frees across a module tree.

An initializer may call functions declared earlier in its module or in a
dependency, and read or write those modules' globals; it runs with no current
function, so its calls add nothing to any function's effect summary (there is no
caller to record them in).

The P4 `cmd/eza.obx` is the first headed program root (`module eza`): its
imports are the explicit `../lib/...` modules, the build writes an unread
`eza.sym` into the private sym dir, and its `main` returns normally on success
so the runtime's stats and leak checks run. Because `try` is not allowed in
`main`, eza matches each `Result` and exits with the eza-shaped status; the
library calls inside its helpers use `try` as usual.

### Type identity

A named type is one object for the whole compilation: the defining module's
declaration creates it and every importer reuses that object, so `a::T` in B, in
C and in A is the same type, and a record allocated in one module, freed or
matched in another, uses one descriptor address. Two modules that declare
structurally identical records have two different types; assigning one to the
other is an error (`cannot use b::T as a::T`). A type alias is the underlying
type for every rule; two aliases to one imported type, in one module or several,
still denote one object and one descriptor.

### Reserved type names and constants

A type may not be named `int`, `byte`, `bool`, `string`, `void`, `nil`,
`Result`, `decimal`, `Sql`, `SqlArg`, `Html`, `HtmlAttr` or `HtmlAttrs`
(`cannot declare a type named '<name>'`), and `const` of `nil` is
refused (`a constant cannot be nil; use a typed optional var or let`). A module
alias may not use a built-in name.

### The dependency scan: `--deps`

`obo --deps SOURCE [-I DIR]...` reads module headers and import blocks only and
prints the normalized source paths in depth-first post-order: dependencies
before importers, the root last, one per line, deduplicated by normalized path.
It writes nothing, rejects `-o`, and reports missing files (exit 2), cycles
(exit 1), duplicate module names (exit 1), a missing imported header and the
basename rule with the same messages as a build. `make` can use it for tool
source prerequisites.

### Symbol files, `--check` and `--sym-dir`

Every module with a `module Name` header writes `Name.sym` after its declarations
are compiled and its summaries are complete. The file lives in the **symbol
directory**: `--sym-dir DIR`, or the root source's directory by default. The
directory is a flat namespace by module name and must already exist (the runtime
has no `mkdir`); a missing or unwritable directory is
`obxc: symbol-file directory '<dir>' does not exist or is not writable` (exit 2).
Two builds must not share a sym-dir: `writeFile` truncates in place and there is no
atomic rename, so parallel builds and tests use private directories.

The emission rule is one rule:

- dependencies always write their `.sym` (the importer reads them);
- a build with at least one import writes the root's `.sym` too when the root has a
  header;
- a single-file build writes no `.sym` unless `--emit-sym` is given;
- `--check` always writes the checked module's `.sym`, and never an executable.

`--emit-sym` is a flag on a normal build (it still writes the executable and still
accepts `-o`); it requires a `module` header. `--check`, `--deps` and `--emit-sym`
are mutually exclusive; `-o` is rejected with `--check` and `--deps`:
`obxc: -o cannot be used with --check or --deps`.

In a full build the importer binds the **live** interface (the defining module's
types, globals, functions and descriptors) and reads and validates the dependency's
`.sym` against it. `--check` instead reconstructs the interface from the `.sym`:
imported functions get a known dummy address and imported globals unique dummy
offsets, imported types have no descriptor, and the checked module's code is
discarded. `--check` compiles and checks the body on every run; it is not a build
cache. It does not require `main`: a checked module may have a private `main` (its
`.sym` omits it) or an exported `main` (the `.sym` lists it, and an importer rejects
it with `module '<m>' may not declare 'main'; the program root provides it`).

For each dependency, `--check` looks for `<sym-dir>/<basename>.sym`, where the
basename is the import path's file name without `.obx`; the `.sym`'s `module` line
must equal that basename. A dependency's source, if present, is used only to
validate its header (the basename rule) and its `source` hash; it is never
compiled. A source-less dependency is allowed if its `.sym` is valid. A **full
build** still requires every source; if only a `.sym` exists it reports
`obxc: cannot read module '<m>' at <paths>; only --check may use a symbol file
without source` (exit 2). The checked root is exempt from the basename rule (it is
the root of its check graph), and its `.sym` is named by its declared module name.

`--version` prints `obo 0.19 sym-format 12 abi 1`; the numbers are the `obosym`
format, the compiled-program ABI and the compiler version, each checked exactly
when a `.sym` is read. The historical `0.18` release printed
`obo 0.18 sym-format 11 abi 1`, the `0.17` release printed
`obo 0.17 sym-format 10 abi 1`, the `0.16` release printed
`obo 0.16 sym-format 9 abi 1`, the `0.15` release printed
`obo 0.15 sym-format 8 abi 1`, and the `0.14` release printed
`obo 0.14 sym-format 7 abi 1`, `0.12` printed `obo 0.12 sym-format 6 abi 1`,
and `0.8.1` printed
`obo 0.8.1 sym-format 4 abi 1`. A format-6 file is rejected by the exact check;
rebuild it. `--reemit-sym FILE` is a testing hook: it reads a symbol
file with the reader and prints the canonical re-emission, which the symbol-file
tests compare byte-for-byte.

### The `.sym` format and hashes

A `.sym` is text in a language-like declaration syntax, readable by the scanner:

```text
obosym 12
abi 1
compiler "0.19"
source 0x0123456789abcdef
dep io "io.obx"
interface 0xfedcba9876543210
module strings
depiface io 0x1111111111111111
type Builder uid 0x3333333333333333
type Builder = ref record { pub items: []string; priv; pub count: int }
type Node = ref record { pub value: int; pub next: ?Node }
type Holder = ref record { priviface; pub id: int }
type Shape = closed { Circle { pub r: int } Rect { pub w: int; pub h: int } }
type Sized = interface { pure fn size(): int; mut fn fill(mut p0: &Builder) }
type Text = []string
const Limit: int = 0x000000000000000a
const Empty: string = hex "6e6f6e65"
var Cache: []Node
let Default: int
fn (p0: &Builder) length(): int
fn (mut p0: &Builder) reset() pfree 0x1 pf 0 "=/items" frees 1 strings::scratch
impl Sized for Builder
fn split(p0: string, p1: string): []string
fn apply(p0: decimal, p1: fn(decimal): decimal): decimal
```

- The first word is the single identifier `obosym` (not `obo-sym`), then the
  format number; the reader requires exact `obosym`, `abi` and `compiler` lines.
- `source` is the FNV-1a 64 hash of the module's source bytes exactly.
- `dep` lines are the direct dependencies, one per unique module in first-import
  order, with the path **as imported** (relative stays relative). They are
  diagnostic and outside the interface hash.
- `interface` is the FNV-1a 64 hash of the exact bytes after that line. The body
  holds the module name, one `depiface <module> <hash>` per dependency in the same
  order, then the exported declarations. A dependency's interface change therefore
  changes every dependent's interface hash; a body-only change does not. Import
  order is part of the hash because it determines initialization order.
- Declarations are emitted in canonical groups: shells for exported record/closed
  types (source order), their definitions (source order; an exported interface
  has a definition line and no shell), aliases, constants, globals, methods
  (declaration order), implementations (declaration order), functions. Private
  declarations are skipped without reordering the exported ones. No address,
  offset, descriptor, pointer, timestamp or absolute path appears.
- A record definition lists its fields in declaration order (offsets are
  recomputed: a field takes one 8-byte word, or 24 bytes for `decimal`, and each
  field starts where the previous one ends). Every public field is written
  explicitly as `pub name: Type`; a bare `name: Type` is malformed in format 9, so
  a writer bug cannot silently publish a field. A private field is the single
  token `priv`, or `priviface` when its hidden type contains an interface
  (directly or through `[]`, `?`, `^`, `Result`, records or variants), or `priv24`
  when its type is `decimal`. The slot is one word (`priv`, `priviface`) or 24
  bytes (`priv24`) at the next offset; the private name and type are not written,
  so the marker is the only thing that gives the slot its width. `pub priv: int` and
  `pub priviface: int` are ordinary public fields named `priv`/`priviface` and
  round-trip unchanged. A closed definition lists its variants in declaration
  order; every variant field is written with `pub`, and `priv`/`priviface`/`priv24`
  inside a variant is a malformed-symbol error. A public `decimal` field is written
  `pub name: decimal`, and its width comes from its type. Every exported record/closed type has a
  shell line first, so recursive and mutually recursive types resolve. Variants
  are registered by the reader under `Module::Variant` and exported with their
  closed type.
- Field visibility is part of the interface body, so public↔private changes,
  adding/removing/reordering a slot, and a `priv`↔`priviface` change all change
  the interface hash. Renaming or retyping a private field does **not** change it
  unless an exported callable's `pf`/`pfree` summary text changes: a private name
  in a summary path is accepted and not redacted (`io::Reader::buf` is the
  concrete example), so renaming that field changes the hash, `[]byte -> []int`
  keeps it, and `[]byte -> int` changes it because the owned-field `pf` row
  disappears. A retyped field whose summary text is identical also keeps it.
- The reader creates a private slot as a nameless placeholder with the parsed
  `priviface` bit and `int` as its type; an empty name never matches a source
  identifier, so `fieldIndex` ignores it. `symCompareFields` compares the live
  field's carrier answer (`containsIface` on its real type) with the parsed flag,
  never a recomputation from the placeholder, so `--reemit-sym` keeps
  `priviface`. Each per-field carrier query starts with a clean visited set.
- An alias `type A = <typeExpr>` maps the name to the existing type; it never
  re-declares it. Field and signature types are emitted as the underlying type, so
  layout never depends on an alias.
- Constants are primitive: `int`/`byte` as 16 hexadecimal digits (negative values
  wrap), `bool` as `true`/`false`, `string` as `hex "<bytes>"` (including `hex ""`),
  and `decimal` as `hex "<48 digits>"`: the 24 value bytes in memory order, each of
  the three words little-endian, lowercase only. The reader refuses a text that is
  not 48 digits, has an uppercase or non-hex character, a coefficient above 10^38-1,
  a zero with sign 1 or a nonzero exponent, a sign other than 0 or 1, or bits 40 to 63
  of the last word set.
  The reader interns string bytes instead of carrying a rodata address.
- Globals are `var Name: T` or `let Name: T`, with no initializer; a `let` is
  read-only to importers and the defining module owns initialization. A `decimal`
  global takes 24 bytes of zero-initialised global data; every other type takes one
  word. In `--check` an imported global gets a unique dummy offset, also stepping by
  the type's size, so two imported decimal globals never overlap.
- Function headings reuse the source syntax for parameters, `&` and `mut`, but the
  writer **always** emits the canonical names `p0`, `p1`, ...; parameter names are
  syntax only, so renaming a source parameter changes the source hash but not the
  interface hash. A method is a function heading with a source-like receiver
  group, `fn ([mut ]p0: [&]T) name(...)`: the receiver is always parameter 0, so
  `pfree` bit 0 and `pf 0 ...` are its summary; only exported methods are
  emitted. `pfree` is the 62-bit mask of parameters below which the function may
  free; `frees N key...` names the globals it may free (a private or transitive
  `Module::name` key is allowed); `pf i "<event>"` records a precise free place,
  with `=` (the value in the place) or `!` (what lies below it) and a path of
  `/field`, `/[n]` and `/[]` segments. Summary items may be read in any order; the
  writer emits `freesall`, `writeshandles`, `pfree`, `frees`, then `pf` rows by
  parameter and insertion order.
- An exported interface is one definition line, `type I = interface { ... }`,
  with no shell line. Its members follow in declaration order, separated by `;`.
  A member is `fn name(params): T` with an optional result, and its markers come
  before `fn`: `pure` if the member is pure, then `mut` if its receiver is mutable,
  as in `pure mut fn m(p0: int): T`. The receiver is not written (it is parameter 0,
  borrowed for `fn` and mutable for `mut fn`). The explicit parameters are named
  `p0`, `p1`, ... and carry their modes: `p0: T` (owned), `p0: &T` (borrowed) and
  `mut p0: &T` (mutable). A member line has no summary items. The reader builds
  each member's summary from its markers and modes: a `pure` member has an empty
  summary, and any other member has a `pfree` bit on each mutable parameter (the
  receiver included, for `mut fn`), with no flags and no globals.
- An exported implementation is one line after the methods, `impl I for T`. `I` is
  the interface, qualified as `M::I` when it belongs to another module. `T` is the
  receiver's bare name: a record or closed type of this module, not an alias and
  not a variant.
- Only `pub` interfaces and `pub impl` lines are written. A private interface is
  not in the file, and neither is a private `impl`, so an importer cannot name
  either one.

The reader validates hashes, versions, shells, field/variant structure, duplicate
keys and exports, parameter modes, constants, `frees` counts and keys, the
`pf`/`pfree` consistency, and the interface member markers, modes and `impl` lines
before using any value. Malformed files are
`obxc: <sympath>:<line>:<col>: symbol file: <detail>` (exit 2).

### Versions and the trust model

Three exact-match values version a `.sym`: `obosym` (format/grammar and meaning),
`abi` (record layout, descriptor format, calling convention, global addressing,
effect-summary meaning and initialization order), and `compiler` (`OBO_VERSION`).
Bump `OBO_VERSION` with a compiler feature; bump `obosym` on a grammar or meaning
change; bump `abi` on a layout/ABI/effect change. A mismatch is reported with the
line that differs:

- `obxc: symbol file '<p>' has format <n>; this compiler reads <m>; rebuild it`
- `obxc: symbol file '<p>' has ABI <n>; this compiler uses <m>; rebuild it`
- `obxc: symbol file '<p>' was built by compiler '<v>'; this is '<w>'; rebuild it`

FNV-1a is a staleness and integrity check, **not** a cryptographic one. Symbol
files are trusted build outputs: a forged but well-formed interface can make
`--check` accept an unsound program (it can claim a callee frees nothing). Do not
`--check` against untrusted symbol files, and do not treat a `.sym` as a security
boundary. A full build re-reads and validates the freshly written interface, so a
missed version bump only affects `--check` against stale artifacts.

The reader rebuilds each interface member's summary from its markers and parameter
modes, as above, so a caller's effect check uses the marker. It does not re-run the
implementation checks (the signature and effect-tier checks of a method against
its interface) on an `impl` line. A full build checks that the pair is the exported
live implementation, which the defining module's own compile has already checked;
`--check` accepts the line as written. A forged `pure` marker or `impl` line is
therefore not caught by `--check`.

Generated `.sym` files under the build convention (`.obo/sym`) are gitignored;
an arbitrary `--sym-dir` is not automatically ignored. A build without
`--sym-dir` writes next to the root source, so use `--sym-dir .obo/sym` in a
checkout. `tests/modules/golden/expected/*.sym` are
committed byte-goldens and are not ignored. The golden tests never rewrite a fixture in the gate; `sym_test.py
--update` is the explicit regeneration path.

### A checked example

The first file is the root; the second is compiled through `-I`.

```obo
// example: modules
// file: main.obx
module main
import "lib.obx"
fn main() {
    let n = lib::make(4)
    println(lib::value(n))
}
// file: lib.obx
module lib
pub type Node = ref record { v: int }
pub fn make(v: int): Node { return new Node{v: v} }
pub fn value(n: Node): int { return n.v }
// prints 4
```

A source-less check: the same tree is built, the dependency source is deleted, and
`--check` reads only the symbol file.

```obo
// example: modules check
// file: main.obx
module main
import "lib.obx"
fn main() {
    let n = lib::make(4)
    println(lib::value(n))
}
// file: lib.obx
module lib
pub type Node = ref record { v: int }
pub fn make(v: int): Node { return new Node{v: v} }
pub fn value(n: Node): int { return n.v }
// prints 4
```

```obo
// example: modules error: module 'lib' has no exported member 'secret'
// file: main.obx
module main
import "lib.obx"
fn main() { println(lib::secret()) }
// file: lib.obx
module lib
fn secret(): int { return 1 }
```

### Module errors

| Situation | Message | Exit |
|---|---|---|
| missing imported source | `obxc: cannot read module '<m>' at <paths tried>` | 2 |
| imported file has no header | `imported module '<path>' needs a 'module Name' header` | 1 |
| file/header name mismatch | `module '<m>' must be in a file named <m>.obx (found <path>)` | 1 |
| import cycle | `import cycle: a.obx -> b.obx -> a.obx` | 1 |
| duplicate module name | `module '<m>' is declared by both <path1> and <path2>` | 1 |
| `module _root` in a dependency | `module '_root' is reserved for the program root` | 1 |
| import after a declaration | `imports must come before declarations` | 1 |
| repeated alias | `'<a>' is already used as an import alias` | 1 |
| alias vs a declaration | `'<a>' clashes with a top-level declaration` | 1 |
| alias is a built-in name | `cannot use '<a>' as an import alias: it is a built-in name` | 1 |
| qualified name on a non-module | `'<x>' is not a module` | 1 |
| unknown/private member | `module '<m>' has no exported member '<x>' (only 'pub' declarations are visible)` | 1 |
| `pub` in a block | `'pub' is only allowed on top-level declarations` | 1 |
| `pub import` | `'pub' applies to declarations, not imports` | 1 |
| private type in an export | `exported '<x>' uses private type '<T>'; mark '<T>' pub too` | 1 |
| `main` in a dependency | `module '<m>' may not declare 'main'; the program root provides it` | 1 |
| qualified match arm | `match arms name a variant without its module: write Circle` | 1 |
| reserved type name | `cannot declare a type named '<name>'` | 1 |
| `const nil` | `a constant cannot be nil; use a typed optional var or let` | 1 |
| import path not `.obx` | `import paths must end in .obx` | 1 |
| missing `.sym` in `--check` | `obxc: no symbol file for module '<m>' in <dir>; run a build first` | 2 |
| stale `.sym` | `obxc: symbol file '<p>' is stale: '<src>' changed since it was built; run a build` | 2 |
| format mismatch | `obxc: symbol file '<p>' has format <n>; this compiler reads <m>; rebuild it` | 2 |
| ABI mismatch | `obxc: symbol file '<p>' has ABI <n>; this compiler uses <m>; rebuild it` | 2 |
| compiler mismatch | `obxc: symbol file '<p>' was built by compiler '<v>'; this is '<w>'; rebuild it` | 2 |
| corrupt body hash | `obxc: symbol file '<p>' is corrupt (interface hash); rebuild it` | 2 |
| dependency interface mismatch | `obxc: symbol file '<p>' depends on '<d>' with interface hash <h>, but '<d>' has <h2>; rebuild the dependent module` | 2 |
| `.sym` vs a build's live interface | `obxc: symbol file '<p>' disagrees with the compiled interface of '<m>' for <decl>; rebuild it`, where `<decl>` is `interface 'I'`, `interface method 'I::m'`, `implementation 'I for T'` or an order such as `the implementation order` | 2 |
| malformed `.sym` | `obxc: <sympath>:<line>:<col>: symbol file: <detail>` | 2 |
| malformed interface member | `obxc: <sympath>:<line>:<col>: symbol file: <detail>`, where `<detail>` is `'pure' and 'mut' come before 'fn'`, `expected a method name in the interface`, `duplicate interface method '<m>'`, `an interface needs at least one method` or `expected ';' or '}'` | 2 |
| malformed interface parameter | `obxc: <sympath>:<line>:<col>: symbol file: <detail>`, where `<detail>` is `expected a parameter name`, `expected ':' after the parameter name`, `mut needs a reference parameter: write mut p0: &T`, `only records, closed types and arrays can be borrowed, not <t>` or `expected ',' or ')', found <token>` (a `;` between parameters is refused with this one) | 2 |
| malformed interface name | `obxc: <sympath>:<line>:<col>: symbol file: <detail>`, where `<detail>` is `an interface cannot be forward-declared: '<I>' is already declared`, or `duplicate definition of type '<I>'` | 2 |
| malformed `impl` line | `obxc: <sympath>:<line>:<col>: symbol file: <detail>`, where `<detail>` is `expected a receiver type name`, `the receiver of an implementation must be the defining name of a type in this module`, `the receiver of an implementation must be a type defined in this module`, `an implementation's receiver cannot be a variant; use its closed type`, `type '<T>' is declared but not yet defined`, `'<t>' is not an interface type` or `duplicate implementation of '<I>' for '<T>'` | 2 |
| source-less dependency in a build | `obxc: cannot read module '<m>' at <paths>; only --check may use a symbol file without source` | 2 |
| sym-dir missing or unwritable | `obxc: symbol-file directory '<dir>' does not exist or is not writable` | 2 |
| `-o` with `--check`/`--deps` | `obxc: -o cannot be used with --check or --deps` | 2 |

Qualified names in diagnostics are `Module::name` with the declared module name
(not the alias), and types keep the bare name inside their own module. An error
about an imported value says `lib::split` or `lib::Cache`; an error about a
value of an imported type says `a::T` or `[]a::T`. A local alias use may print
the underlying type, because aliases are transparent.

A full build needs every module's source. `--check` is the separate-checking
mode: it needs the checked module's source and its dependencies' symbol files, not
their sources. Symbol files are trusted build outputs, not a security boundary; see
"The `.sym` format and hashes" below.
