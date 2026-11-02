# Symbol files (`.sym`)

Part of the obo language reference. Index and core syntax: [LANGUAGE.md](LANGUAGE.md).

This is the `.sym` format, its hashes and the version/trust model. The
source-level rules for writing and checking symbol files are in
[language/modules.md](language/modules.md).

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
  identifier, so the reader ignores it when matching field lists. It compares
  the live field's interface-carrier answer with the parsed flag, never a
  recomputation from the placeholder, so `--reemit-sym` keeps `priviface`. Each
  per-field carrier query starts with a clean visited set.
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
