# Field visibility: private by default, `pub` per field

Status: design only, revision 2 after independent review (B1-B5, H1-H6). No
compiler, runtime, seed, test or `.sym` change is made by this note, and no
version or symbol-format number is reserved.

Baseline: released `main` `34b798b` (`obo 0.11`, `SYM_FORMAT 6`, `SYM_ABI 1`).
The compiler is `obo/src/obo.obo`; the library is `obo/lib/*.obx`.

The user decision this note implements is settled: **a record field is private
to its defining module unless that field is declared `pub`.** The current
default (every exported record's fields are public) is the bug. This is a
deliberate breaking change, acceptable before 1.0. This note does not reopen
the default.

## Summary of decisions

| Question | Decision |
|---|---|
| Field default | private to the defining module |
| Syntax | `pub name: T`; no `priv` keyword in source |
| Mixed records | allowed; every field independently public or private |
| `.sym` representation | public fields are typed `pub`; each private field is an opaque one-word `priv` or `priviface` slot |
| Private field names/types in `.sym` | not emitted in the field list; `pf`/`pfree` summary paths may still name a private field (accepted v1 leak) |
| External construction | only when **all** fields are `pub`; otherwise a module constructor is required |
| Private field types | may be any type, including private named types; the export closure check skips private fields |
| Interface-carrying hidden fields | encoded as `priviface` so the lambda/interface firewall stays sound in `--check` |
| Closed variants in v1 | field visibility applies to records only; variant fields stay public |
| `pub` on a field of a private type | allowed, not reported |
| Methods and interfaces | private fields are module-level; `pub` methods are the external surface; external impls remain impossible |
| Version | next free `OBO_VERSION` / `SYM_FORMAT` at landing; no reservation; coordinate one bump with `^I` if they land together |

## 1. Semantics

### 1.1 Syntax and scope

A record field declaration gains an optional leading `pub`:

```text
FieldDecl := [ "pub" ] Ident ( "," Ident )* ":" Type
```

`pub` applies to every name in that group. Mixed visibility inside one group is
written as separate declarations:

```obo
pub type Counter = ref record {
    pub value: int          // public
    step: int               // private to module lib
    pub label, unit: string // both public
}
```

`pub` is the same word already used for top-level declarations and methods; the
scanner already emits `T_PUB`. `priv` is **not** a source keyword: `priv: int`
is a field literally named `priv`, and remains private; `pub priv: int` and
`pub priviface: int` are public fields with those names.

A private field is accessible to every declaration in the defining **module**,
not merely to methods of its own type. Functions, methods of other types,
initializers and constructors in the module can read, write, borrow and move it.
This module-level, not type-level, rule is what makes factory functions and
internal helpers possible.

An imported type's field is accessible only if it is `pub` and the type itself
is exported. Type aliases are transparent: an alias of `lib::T` has `lib::T`'s
field visibility and `lib::T`'s owner. Imported types may be named through
`[]`, `?`, `^`, `Result`, a field type, a parameter or a return type; the
visibility rule is applied at the actual field designator, not at the spelling.

`pub` on a field of a **private** type is allowed and produces no diagnostic.
It has no external effect until the type is exported, at which point those
fields already carry the intended visibility. Rejecting it would make field
visibility depend on declaration order or on a later export and would add a
warning channel the compiler does not have.

### 1.2 What "private" prevents

For an importer, a private field cannot be:

- read (`c.step`),
- written (`c.step = 2`, `c.step += 1`),
- borrowed (`&c.step`, `mut &c.step`),
- moved out (`take(c.step)`, `replace(c.step, ...)`), or
- named in a constructor (`new lib::Counter{step: 2}`).

The value as a whole remains movable, passable, returnable, droppable and
storable. Private fields do not make the record opaque as a value; they make its
individual field places inaccessible. A private field's contents can still be
changed by the defining module's methods, which is the intended accessor /
factory pattern.

Inside the defining module all of the above are ordinary field operations. A
`mut &T` method can mutate private fields; a `&T` method can read them; an owned
`T` receiver can move them subject to the existing field-move restriction
(`take`/`replace`).

## 2. Mixed records and interface layout

Mixed records are allowed. The layout rule does not change: every field is one
8-byte word in declaration order (`Field.offset` is `8 * index`). A public field
after a private field therefore keeps its old offset.

The interface file must preserve that offset without exposing the private field.
The chosen representation is an **opaque one-word slot**:

```text
record field list (format 7; the exact number is not reserved)
    { pub value: int; priv; pub label: string }
    { priviface; pub value: int }
```

- `pub name: Type` is one public field at the next word.
- `priv` is one private field at the next word. Its name and type are not
  written.
- `priviface` is one private field whose hidden type contains an interface
  (directly or through `[]`, `?`, `^`, `Result`, records or closed variants).
  It is otherwise a `priv` slot. This marker is required for soundness of the
  existing lambda/interface firewall; see section 6.2.
- The number and positions of slots are exact. The reader reconstructs the
  same offsets by assigning `8 * index`.
- A `priv` slot is one word by definition of this format. If a future type is
  wider than one word (for example the proposed 24-byte `decimal`), the writer
  must not silently use `priv`; that change needs a new format/ABI rule. The
  current ABI's one-word field layout is what makes opacity possible.

The writer emits `pub` explicitly on every public field. A bare `name: Type`
is not accepted in format 7. This makes visibility part of the canonical
grammar and prevents a writer bug from silently producing a public field.

### 2.1 Information-leak tradeoff

Two honest options existed:

1. Emit every field with a visibility marker, name and type. This makes
   diagnostics and live-interface comparison trivial but publishes private
   names and types in every `.sym`, and forces private field types to be
   exportable because the reader cannot resolve a private named type.
2. Emit private fields as opaque slots. This keeps implementation names and
   types out of the field list, allows private field types to be genuinely
   private, and keeps a field-list-only change from changing the interface
   hash. Exported `pf`/`pfree` summaries can still expose a private field path
   and therefore still change the hash (section 2.1). The cost is that the
   reader does not know a private field's name or type.

This design chooses **2**. `.sym` is an interface artifact and is already
trusted build output, not a confidentiality boundary; but avoiding a leak is
still better when it costs one marker. Diagnostics do not need the hidden name:
the caller writes the field name in the access expression, and the diagnostic
uses that spelling. Construction does not need the name either ("it has private
fields").

The field-list consequence is deliberate: changing a private field's name or
its type (while keeping one slot and the same `priv`/`priviface` class) does
**not** change the interface hash *unless it changes an exported callable's
summary*. Changing the number or order of slots does, because it can change
public offsets and external constructibility.

That qualification is required because summaries are not redacted in v1. An
exported function that writes or replaces an owned private field records the
field path in its `pfree`/`pf` row:

```text
pub type R = ref record { n: int; buf: []byte }   // both fields private
pub fn reset(mut r: &R) { r.n = 0; r.buf = new []byte(0) }
fn reset(mut p0: &R) pfree 0x1 pf 0 "=/buf"
```

`buf` is private, yet `"=/buf"` is in the interface. Consequences:

- Renaming `buf` changes the exported summary, so the interface hash **does**
  change, and must.
- Renaming a private scalar that no exported summary names (for example `n`)
  does not change the hash.
- Changing a private field from owned to scalar (or the reverse) can add or
  remove a `pf` row, so the hash changes.
- `io::Reader::buf` and `io::Writer::buf` are the concrete private-field
  examples: their exported methods carry `pf 0 "=/buf"` or `"!/buf"`.
  `fs::Dir::buf` remains public in the v1 exception (section 10.2), but
  `fs::next` carries the same `pf 0 "=/buf"` path; a public field in a summary
  is the same hash category.

Accept this leak for v1. Do **not** invent summary redaction, generic path
names, or a second hash of private field identities. The summaries must remain
exact for ownership checking; redacting a path would make the summary lie.
Document that a private field name may appear in a `.sym` summary line.

## 3. Construction

### 3.1 Rule

An external `new T{...}` is allowed **only when every field of `T` is `pub`.**
If even one field is private, the whole constructor is refused; external code
cannot omit the private fields, default them, or initialize them through a
nested expression. Inside the defining module, any record can be constructed as
before.

The check is on the record's defining module, not on the spelling: `t.owner !=
curModuleName` is the external case, including an alias to an imported type.

```obo
// lib.obx
module lib

pub type Counter = ref record {
    pub value: int
    step: int
}

// A module constructor is mandatory when a field is private.
pub fn newCounter(start: int, step: int): Counter {
    return new Counter{value: start, step: step}
}

pub fn (mut c: &Counter) bump() {
    c.value += c.step
}
```

```obo
// main.obx
module main
import "lib.obx"

fn main() {
    let c = lib::newCounter(10, 2)
    println(c.value)          // 10
    c.value = 12              // a pub field is writable
    c.bump()
    println(c.value)          // 14
    // let bad = new lib::Counter{value: 0, step: 2}
    //   error: cannot construct 'lib::Counter' outside its module: it has
    //   private fields; add a constructor function in module 'lib'
}
```

The constructor function is ordinary code in the defining module. It may take
arguments, validate them, compute defaults, and call other module helpers. It is
also the only way to produce a value whose private fields are non-default.
There is no special constructor syntax and no "private field initializer"
feature.

### 3.2 Edge cases

- **All fields public**: external `new T{...}` is allowed exactly as today,
  including omission of nil-able fields.
- **Zero fields**: vacuously all public; external `new T{}` is allowed. A
  zero-field record is a marker type, not an opaque type; its type visibility is
  controlled by `pub type`, not by fields.
- **Forward declaration**: `new T{...}` before the definition is the existing
  `'T' is declared but not yet defined`; visibility is irrelevant until fields
  exist.
- **Arrays**: `new []T(n)` is not a record constructor and is not subject to
  the all-pub rule. The existing rule still applies: a non-zero length for a
  non-nullable `T` is refused (`array elements ... would start as nil`), so the
  only external form for a record element type is `new []T(0)` plus `append`
  of values obtained from the module. `new []?T(n)` remains allowed.
- **Nested construction**: each `new` is checked independently. A public outer
  record with a field of a private-field inner type cannot be constructed
  externally, because the inner expression is refused first. A public inner
  construction nested inside a private outer construction is checked against
  the outer's rule at the outer site.
- **Globals and constants**: a global initializer is checked like any other
  expression in its module. `pub var G: lib::Counter = lib::newCounter(...)` in
  an importer is fine; `pub var G = new lib::Counter{...}` in an importer is
  refused. In `lib`, both are fine. `const` remains primitive-only, so it
  cannot hold a record.
- **Aliases**: an alias of an imported record is external and follows the
  underlying type's rule.

## 4. Private field types and the export closure rule

The existing rule is that an exported declaration may not mention a private
named type through its record fields:

`exported 'Shape' uses private type 'Secret' (variant 'A'); mark 'Secret' pub too`

This design **relaxes that rule for private fields** and keeps it for public
fields. A private field of an exported record may have any type: a private named
type, a private record, an array or optional of one, an interface, or an
imported type. The field is not in the interface; its type is the defining
module's business.

Consequences:

- An exported record may hide a private implementation type:
  ```obo
  type Cache = ref record { entries: []Entry }   // private type
  pub type Session = ref record {
      pub id: int
      cache: Cache        // allowed; not emitted
  }
  ```
- A `pub` field of a private type is still an error, with the existing
  message. Public fields are part of the interface and must be nameable by an
  importer.
- Methods and constructors inside the module can use the private type freely.
- The compiler's `closureCheck` skips `Field.private` entries; it still
  traverses every public field, every variant field (variants have no private
  fields in v1), every interface member, and every exported function signature.
- The `.sym` reader does not need the private field's type. The one exception is
  the interface-carrier bit, below.

### 4.1 The interface-carrier exception

`containsIfaceIn`/`containsIface` traverse record fields to enforce the lambda
and function-type firewall: a record that carries an interface cannot be a
function value parameter/return or a capture in the current version. If a
private field were emitted as a bare `priv` int, `--check` would reconstruct the
imported record as interface-free and could accept a program a full build
rejects.

The writer therefore emits `priviface` instead of `priv` when the field carries
an interface. The exact test is:

```text
field.privateIface || containsIface(field.typ)
```

The `privateIface` term is mandatory for parsed fields: a `.sym`-reconstructed
private field has `typ == int` as its placeholder, so testing only
`containsIface(field.typ)` would silently re-emit `priv` and lose the marker on
`--reemit-sym`. The reader sets `Field.privateIface` on the placeholder;
`containsIfaceIn` tests that flag before recursing into the placeholder type.
`symCompareFields` compares the live side's `containsIface(liveField.typ)` with
the **parsed flag** (not a recomputation from the parsed placeholder type).
The flag is part of the interface hash. This is the minimum metadata needed to
keep `--check` sound without exposing the hidden type.

Each carrier query must start from a clean visited set. `containsIfaceIn` uses
the global `ifaceSeen` list to terminate recursive records; call
`containsIface` (which truncates `ifaceSeen` first, `obo.obo:1399-1403`) for
each field, or explicitly truncate before each `containsIfaceIn` query. Calling
the recursive helper directly across fields leaks visited state: if a private
field `b: B` is checked first and B's traversal visits C, then a second private
field `c: C` would be marked plain `priv` instead of `priviface`. The fixture in
section 11.3 covers exactly this order.

No other field-type property is needed across the boundary:

- Record values are always owned regardless of their fields.
- Field width is one word in this ABI.
- Function-valued fields are already refused by `fieldList`.
- A private field's descriptor is emitted by the defining module; an importer
  frees a record through the record's own descriptor, never by reconstructing
  the field list.
- `--check` discards code, so placeholder field types are never used to
  allocate or free a value.

## 5. Closed variants

**Decision for v1: field visibility applies to records only. Fields of closed
variants remain public.** This is not an ambiguity left for implementation.

Reasons:

- Exporting a closed type exports all its variants and permits
  `new lib::Circle{...}` and `match` patterns in importers. Private variant
  fields would require a separate construction rule per variant, a privacy rule
  for pattern bindings, and a `.sym` representation for each variant's hidden
  slots.
- Pattern matching is the normal way to read variant fields. Making a variant
  field private while leaving `match` public would either leak it through a
  pattern or make the variant partially un-matchable, which needs a new
  exhaustiveness/privacy design.
- The motivating cases for this change are stateful library records
  (`Reader`, `Writer`, `Connection`, `Html`), not closed unions. Variant data is
  usually intentionally public. (`fs::Dir` stays a public-field v1 exception;
  see section 10.2.)

Therefore:

- The parser's field-list mode for a closed variant sets every `Field.private`
  to `false` (public) by default and rejects `pub` on a variant field:
  `'pub' is not allowed on a variant field; variant fields are always public`.
  A shared `fieldList` must take the variant flag; a bare default-private loop
  would silently make variants private.
- The `.sym` reader rejects `priv`/`priviface` inside a closed definition with
  `private fields are not allowed in a closed variant`.
- The writer emits `pub` on every variant field (canonical format).
- A future complete alternative would apply the same `Field.private` flag to
  variant types, make a pattern binding on a private field an error outside the
  defining module, permit external construction of a variant only when every
  field of that variant is public, and encode each variant's private slots as
  `priv`/`priviface` inside its `{ ... }`. That is a coherent later extension,
  but it is not v1.

## 6. Interactions

### 6.1 Methods

Methods are declared in the receiver type's own module (the existing rule
refuses `impl`/method declarations on imported types). Therefore every method
body has module-level access to private fields. A `pub` method is the external
surface; a private method is internal. A public method may expose computed
values, mutate private state, return private data as a public type, or accept a
callback.

Inside a `&T` method, private fields are readable but not writable. Inside a
`mut &T` method they are writable and may be taken/replaced subject to existing
rules. Inside an owned `T` method, a field cannot be moved out directly; use
`replace` or return the receiver, as today.

A public method and a private field may not have the same name on the same
receiver type (the existing field/method collision rule applies to all fields,
public or private). An external call `x.privateName()` must not report
`type '<q>' has a field '<f>', not a method` and reveal a private field. It
reports this exact diagnostic, identical in a full build and in `--check`:

`type '<q>' has no public field or exported method '<f>' (only 'pub' declarations are visible outside module '<m>'; mark the field 'pub' or add a public method)`

The full build knows the private field; `--check` knows the type has private
slots. `methodCall` uses the same branch for both when no exported method exists
and the type has private fields (section 8).

### 6.2 Interfaces and external implementations

An interface value has methods, not fields; `i.field` remains
`interface 'I' has no fields; it has methods only`. A record with private fields
can implement an interface; importers call the interface's public methods and
never touch the fields.

External implementations are already impossible: `impl I for T` requires `T`
to be defined in the current module, and methods on imported types are refused.
So there is no "foreign impl needs private access" problem and no friend rule.
An exported `impl` is compiled in the defining module with full access; the
vtable and public methods are the interface.

Private fields of interface type are allowed, but the record is still an
interface carrier for the lambda firewall. `priviface` preserves that in
`--check`; a full build uses the live field types. A record with a private
interface field can be captured only where the existing `containsIface` rule
allows it, in both build modes.

### 6.3 Borrows, `replace`, `take` and moves

- `&x.pub` and `mut &x.pub` are ordinary borrows with the existing aliasing
  checks. `&x.priv` fails at field resolution.
- `replace(x.pub, v)` is allowed; `replace(x.priv, v)` fails at field
  resolution. `replace(x, factory())` on the whole record is allowed.
- `take(x.optionalPub)` is allowed where the existing optional rules allow it.
  `take(x.private)` fails at field resolution even if the field is optional.
- A whole record of an imported type can be moved, passed by value, returned,
  stored in an array/field/global, dropped, or freed. Private fields travel with
  it; their ownership is handled by the defining module's descriptor.
- A `mut &T` parameter to a module function can mutate private fields inside the
  module; the caller cannot observe them except through public fields or
  methods.

### 6.4 Nested paths, arrays and globals

- Every path segment is checked. `x.pub.priv` fails at the private segment;
  `x.priv.pub` fails at the first. `x.pub[0].pub` is checked per element type.
- Arrays of imported records follow the element type's visibility. An importer
  can create `new []lib::T(0)`, append values produced by the module, index,
  move elements, and read/write public fields. It cannot read/write private
  fields of an element.
- A `pub var` global of a record type is writable as a whole by importers; its
  public fields are writable; private fields are not. A `pub let` global is
  read-only as a whole, as today. A global initializer in an importer can call a
  factory but cannot use `new` on a private-field type.
- A global's type may have private fields; teardown uses the live descriptor.

### 6.5 Handles (`^T`)

`handle(place)` accepts the same places as today; a handle to a type with
private fields is allowed. `h.pub` reads/writes; `h.priv` is refused at field
resolution. A handle does not bypass visibility, and a private field does not
make `^T` invalid. Dead-handle and origin checks are unchanged.

### 6.6 C interop wrapper tokens

The experimental `Connection { tok: int }`, `Rows { tok: int }`,
`Buffer { tok: int }`, sqlite `Connection`/`Stmt`, and future DB wrapper records
should make `tok` private. External code can then no longer *accidentally* write
`new Connection{tok: 123}`; it must use `connect`/`exec`/the module's
constructors.

This is **not** a capability or security boundary, and the doc must not claim
that forging becomes impossible:

- Keep `buffer::wrap(tok)` as an explicit raw-token constructor. A module may
  deliberately expose a raw-token constructor; that is an API decision, not a
  language hole. `wrap` remains the documented escape for tests and callers who
  already have a token.
- Code in the defining module (including a test helper) can still construct a
  token value.
- C code and the trusted registry remain the authority for stale, wrong-kind and
  forged integers; `cres_peek` still validates the live bit, generation and
  kind.
- The token is still an integer, not an unforgeable capability. Private fields
  reduce accidental construction; they do not stop a copied integer from being
  wrapped by `wrap` or by same-module code.

Tests can still exercise forged/stale tokens without a language escape hatch:

1. **No forging needed for stale/wrong-kind.** Ordinary use produces them:
   `let c = connect(...); c.close(); c.close()` (stale); a `Rows` after its
   connection closes, or a second close, where the wrapper API allows it. The C
   registry rejects before dereference.
2. **Forged integer tests use a test fixture, not a language feature.** Put a
   test-only source copy of the wrapper module in the test (or a separate
   `*_test_support.obx`) that deliberately exposes
   `pub fn forgeForTest(tok: int): Connection { return new Connection{tok: tok} }`
   or keeps the old public field. The shipping module stays private-field; the
   fixture proves the registry rejects the forgery.
3. **C-level tests** can call the adapter with a raw token and cover wrong-kind
   and generation checks directly.

Do not add a general compiler "trusted test module" or friend-module escape for
this. A test copy is honest and keeps the production surface private.

## 7. `.sym`, hashes, versions and `--check`

### 7.1 Grammar

The format is the next free `obosym` after every landed feature; the examples
below show the intended shape, not a reserved number.

```text
obosym 7
abi 1
compiler "0.12"
source 0x...
dep io "io.obx"
interface 0x...
module lib
depiface io 0x...
type Counter uid 0x...
type Counter = ref record { pub value: int; priv }
type Session = ref record { priviface; pub id: int }
type Shape = closed { Circle { pub r: int } Rect { pub w: int; pub h: int } }
...
fn newCounter(p0: int, p1: int): Counter
fn (mut p0: &Counter) bump()
```

Rules:

- Public fields are `pub name: Type`, in declaration order.
- A private field is exactly one `priv` token, or `priviface` if its hidden type
  contains an interface. The writer's exact test is
  `field.privateIface || containsIface(field.typ)`, so a parsed placeholder
  keeps its marker through `--reemit-sym`; each carrier query uses
  `containsIface` (clean visited set), never a cross-field
  `containsIfaceIn` call.
- Bare `name: Type` is an error in a format 7 record definition. This is a
  deliberate strictness, so every public field is visibly public.
- In the `.sym` writer, closed variants emit `pub` on every field; `priv`/
  `priviface` there is an error. This is a format requirement only: source
  variant fields are never annotated, and `pub` in source is rejected
  (section 5). Migration must not add `pub` to variant source fields.
- Shells (`type T uid ...`) have no fields and no visibility marker.
- Interfaces have no fields; their members are unchanged.
- A definition still lists fields in declaration order, and offsets are
  recomputed as one word each. Private slots preserve positions.
- The reader creates `Field{name: "", private: true, privateIface: ...,
  typ: tInt, offset: 8 * index}` for a private slot. Empty names never match a
  source identifier; `fieldIndex` therefore ignores them naturally.
- `symCompareFields` requires: same count; for a private slot, both sides
  private, same offset and the same carrier answer (`containsIface` on the live
  field type versus the **parsed** `privateIface` flag, not a recomputation
  from the parsed placeholder); for a public field, same name, same type and
  same offset, and both public. A live public field against a parsed private
  slot (or the reverse) is `symDisagree`.
- Duplicate public field names remain an error. Private slots have no names, so
  there is no duplicate-name case for them; their count is positional.
- `pub priv: int` and `pub priviface: int` are ordinary public fields named
  `priv`/`priviface`. The leading `pub` disambiguates them from the private
  markers; the reader must accept both, and canonical re-emission must preserve
  them byte-for-byte (section 11.3).
- Unknown field markers, `priv` with a following name or `:`, `priviface` in a
  variant, and a missing `pub` on a non-private field are all malformed
  symbol-file errors with line:col.

### 7.2 Interface hash

The body after `interface` includes the field markers, so:

- changing a field from public to private (or back) changes the hash;
- adding/removing/reordering a public field changes it;
- adding/removing a private slot changes it (the token appears/disappears, and
  public offsets or external constructibility may change);
- changing a private field's name or its type while keeping one `priv` slot and
  the same `priviface` class does **not** change it *if no exported callable's
  summary names that field*;
- renaming or retyping a private field that appears in an exported `pf`/`pfree`
  path **does** change it; for example `io::Reader/Writer.buf` must change the
  hash when its name or ownership changes (`fs::Dir.buf` is public in v1 but
  appears in `fs::next`'s summary and is the same path category);
- changing a private field into an interface carrier (or back) changes it
  because the marker changes;
- `pfree`/`pf` summary paths are not redacted: a private field name can appear
  in the interface, and that leak is accepted in v1;
- private implementation types that are not fields of an exported record do not
  appear at all;
- `depiface` propagates any of these changes to dependents.

A full build re-emits and re-reads the file, so a missed version bump only
affects `--check` against stale artifacts, as today.

### 7.3 Canonical re-emission

`--reemit-sym` uses the same reader and writer. It must be byte-identical for
format 7 files: `pub` explicit on every public field, `priv`/`priviface` for
private slots, fields in declaration order, no names/types for private slots.
Hand-written format 7 input with summary items reordered or parameter names
changed is canonicalized exactly as today; visibility is not optional in the
canonical output.

### 7.4 `--check` with source absent

The imported type is reconstructed from the `.sym`:

- public fields have names and types, so external reads/writes/borrows/moves and
  all-public construction are checked as in a full build;
- private slots have positions and the `priviface` bit, so offsets and the
  "has private fields" construction refusal are correct;
- private field access reports the same no-public-field diagnostic using the
  name the caller wrote;
- the lambda/interface firewall uses `privateIface`; a record carrying a hidden
  interface is rejected in `--check` exactly as in a full build;
- a private field's hidden type cannot be named or used, which is the point.

### 7.5 Old-format rejection, tampering, duplicates and unknown fields

- A format 6 file is rejected by the existing exact check:
  `obxc: symbol file '<p>' has format 6; this compiler reads 7; rebuild it`.
  Every committed golden and generated fixture is migrated in the implementation
  commit; `sym_test.py --update` remains the only regeneration path.
- A full build compares the live interface with the parsed file and reports
  visibility or `priviface` disagreement as
  `obxc: symbol file '<p>' disagrees with the compiled interface of '<m>' for type 'T'; rebuild it`.
- `--check` cannot detect a forged but rehashed visibility change; FNV is an
  integrity/staleness check, not a signature. `.sym` remains trusted build
  output. This is the same trust model as summaries and `pure` markers and must
  be stated in the docs, not papered over.
- A tampered visibility change that is not rehashed is caught by the interface
  hash; a structurally malformed one is caught by the reader.
- Duplicate public fields, unknown markers, `priv` with a name/type, and variant
  private fields are rejected before use.

### 7.6 Version and symbol-format rule

No number is reserved by this note.

- `SYM_FORMAT` becomes the next free format after every landed feature. If this
  lands next after 0.11/format 6, it is format 7; if `^I` or another format
  change lands first, it is the next number after that.
- `OBO_VERSION` becomes the next free compiler version at promotion.
- `SYM_ABI` stays `1`: field visibility does not change layout, descriptors,
  calling convention, global addressing or summaries. The one-word private slot
  preserves the existing layout. A future field width change is an ABI/format
  matter.
- The `^I` design also contemplates a future format bump (it recommends, but
  does not reserve, the next slot). Coordinate: if both land in the same
  release, take **one** format bump that covers both grammar changes; if they
  land sequentially, take the next free number each time. Do not preassign
  `0.12`/format 7 here.
- The `--version` line and `sym_test.py` constants are updated in the
  implementation commit, not this one.

## 8. Compiler implementation plan

All anchors are in `obo/src/obo.obo` at `34b798b`. Line numbers are starting
points, not a contract.

| Area | Change | Anchor |
|---|---|---|
| Field model | add `private: bool`, `privateIface: bool` to `Field` (16 bytes/Field; function-type parameters share `Field`) | `type Field`, 1235; `fnAddParam`, 1305 |
| Source parser | `fieldList` parses optional `T_PUB` before a name group; all names in the group share visibility | `fieldList`, 4944 |
| Record vs variant | `typeDecl` calls `fieldList(rec, true)` for records and `fieldList(v, false)` for variants; variant mode forces public and `pub` there is the exact error | `typeDecl`, 4981; variants 5048-5055 |
| Field lookup | `fieldIndex` ignores private placeholder names (empty); a real private name is found in a full build and then rejected by visibility; add `hasPrivateFields` for the check-mode diagnostic | `fieldIndex`, 1519 |
| Designator access | after `fieldIndex`, reject a foreign private field before `load`; this covers read/write/borrow/move paths; a missing name on a type with private slots uses the same diagnostic | `designatorT`, 7279-7287 |
| Method collision | do not report a foreign private field as "field, not method"; use the one section 9 message when no exported method exists and the type has private fields, identical in full build and `--check` | `methodCall`, 7425 |
| Construction | after resolving `T` and before `RT_ALLOC_RECORD`, refuse a foreign type with any private field; check the whole record, not the given fields | `newExpr`, 7450-7485 |
| Export closure | `closureCheck` skips private fields; public fields and variants unchanged | `closureCheck`, 11013; `checkExports`, 11058 |
| Interface carrier | `containsIfaceIn` consults `Field.privateIface` for reconstructed types; live source types still recurse through real types; each per-field query starts with `containsIface`/a truncate | `containsIface`/`containsIfaceIn`, 1399-1439 |
| Writer carrier | emit `priviface` when `field.privateIface || containsIface(field.typ)` | `appFields`, 11792 |
| Writer | `appFields` emits `pub` on public fields, `priv`/`priviface` on private; variants always `pub`; `appTypeDef` passes the variant flag | `appFields`, 11792; `appTypeDef`, 11871 |
| Sym reader | `symFieldList` parses `pub`/`priv`/`priviface`; private slot is a one-word placeholder; variant mode rejects private markers | `symFieldList`, 12449 |
| Sym comparison | compare count, private/privateIface, offsets; public names/types; no placeholder type comparison | `symCompareFields`, 12470 |
| Sym definition | mode 1 builds placeholders; mode 0 compares; shells unchanged | `symTypeDecl`, 12790 |
| Reader/writer entry | format check, hashes, canonical emission; no new header lines | `readSym`, 13463; `writeSym`, 12162 |
| Diagnostics | exact strings from section 9 | `error`, `errorAt`, `symFileFail`, `symDisagree` |
| Versions | next free `OBO_VERSION` / `SYM_FORMAT` at landing; `SYM_ABI` unchanged | 113-115 |
| `--reemit-sym` | no code change beyond the writer/reader; add round-trip tests | existing path |
| Dependency scan | none; header/import parsing is unaffected | `--deps` |

The compiler has no AST: the parser writes directly into the `Field` list.
Adding `private` and `privateIface` costs **16 bytes per `Field`** (the two
booleans plus alignment), not one word. `Field` is also used for a function
type's parameters (`fnType`/`fnAddParam` at `obo.obo:1297-1313`), so every
function-type parameter pays the same cost, not just record fields. This is a
compiler-memory cost only; it does not change the program ABI or a record's
layout.

The current 0.11 self-compile peak is about `2,085,872` bytes live with
`413,728` bytes spare under the `2,500,000` cap. Expected impact is roughly
`+10,000` to `+30,000` bytes peak (16-byte `Field`s, parser branches,
writer/reader branches, diagnostics); measure at the stage checkpoint,
including the function-type-parameter population, and do not trim other
features. No runtime change is expected.

Suggested ordering (one feature in flight):

1. Parser/Field flag and source semantics (access, construction, closure skip,
   `privateIface` computation).
2. Writer/reader, format bump, golden/fixture migration, `--check` tests.
3. Stdlib and `cmd/eza` migration (section 10).
4. Docs, doc-example migration, mutation targets and the full gate.

## 9. Diagnostics

Exact strings, with `<m>` the declaring module, `<T>` the type's bare name,
`<q>` its qualified display name (`lib::T` where needed), and `<f>` the field
spelling the caller wrote.

| Situation | Exact message |
|---|---|
| Private field read | `type '<q>' has no public field '<f>' (only 'pub' fields are visible outside module '<m>'; mark '<f>' 'pub' or add an accessor)` |
| Private field write | same message (visibility is checked at field resolution, before the write) |
| Private field borrow | same message |
| Private field move/`take`/`replace` | same message |
| Private field used as a method (`x.privateName()`) | `type '<q>' has no public field or exported method '<f>' (only 'pub' declarations are visible outside module '<m>'; mark the field 'pub' or add a public method)` |
| Construction with any private field | `cannot construct '<q>' outside its module: it has private fields; add a constructor function in module '<m>'` |
| `pub` on a variant field | `'pub' is not allowed on a variant field; variant fields are always public` |
| `pub` on a field of a private type | allowed; no diagnostic |
| Calling a constructor function that uses private fields | none; the function is ordinary code and has module access |
| Old symbol format | existing `obxc: symbol file '<p>' has format <n>; this compiler reads <m>; rebuild it` |
| Live/`.sym` visibility disagreement | existing `obxc: symbol file '<p>' disagrees with the compiled interface of '<m>' for type '<T>'; rebuild it` |
| Malformed field marker | `obxc: <p>:<line>:<col>: symbol file: expected 'pub', 'priv' or 'priviface' in a record field list` |
| Private marker with a name/type | `obxc: <p>:<line>:<col>: symbol file: a private field has no name or type` |
| Private marker in a variant | `obxc: <p>:<line>:<col>: symbol file: private fields are not allowed in a closed variant` |
| Duplicate public field | existing `duplicate field '<f>'` |
| Unknown field type | existing `unknown type '<T>'` |

The access message deliberately says "no public field" rather than "private
field" so a typo and a real private field get the same accurate, actionable
message in a full build and in `--check` (where the hidden name is not in the
`.sym`). The caller's own spelling is echoed, and the hint covers both adding
`pub` and adding an accessor. A write/borrow/move does not get a different
message because the operation never gets past field resolution; the tests still
cover all four source forms.

Use the no-public-field wording when the foreign type has at least one private
slot, or when the full build finds the name as a private field. A foreign type
with no private fields keeps the existing `type '<q>' has no field '<f>'` for a
typo. That preserves old diagnostics for all-public records and still makes
`--check` agree with a full build for mixed records.

## 10. Migration

### 10.1 Current audit at `34b798b`

A mechanical recount at this base gives:

- `103` exported record definitions (`pub type ... = ref record`) in
  `.obx`/`.obo`: `9` in `obo/lib` (the 10th grep hit in `html.obx` is a
  comment), `90` in `obo/tests`, `4` in `obo/experimental`, and `0` in
  `obo/cmd`, `obo/examples` and `obo/src`.
- `71` real cross-module record constructions (`new alias::T{...}`): `3` in
  `obo/cmd/eza.obx` (`cli::FlagSpec`, two `fs::DirEntry`) and `68` in tests.
  The raw grep split is `69` tests + `3` cmd = `72`; one of those 69 test hits
  is a comment in `obo/tests/mutate_fork.py:661`, not a construction site. So
  the real split is `68` tests + `3` cmd = `71`; do not count the comment as a
  migration site.
- `147` total `pub type` lines in `.obx`/`.obo` (anchored `^pub type `), of
  which `103` are record definitions and the rest are closed types, interfaces,
  aliases and forward declarations. The earlier `154` came from an unanchored
  grep that counted scanner keyword strings and comments.
- Embedded Python test sources add `28` record definitions and `22` construction
  sites matching `new lib::...{`. That 28/22 is the column-0 fixture scope; a
  broader alias count finds 30 constructions and 40 definition lines in `.py`
  because it includes one-line dict fixtures and comments. Both scopes must be
  audited; the implementation migration list should name the files
  (`sym_test.py`, `float_f1.py`, `sym_fuzz.py`, and any mutate script).
- There are `95` committed `.sym` files: `3` goldens under
  `tests/modules/golden/expected` (regenerable with `sym_test.py --update`) and
  `92` hand-written/controlled fixtures under `tests/modules/sym/*/symdir`
  (not an `--update` target; see step 6).
- The existing wrong-format fixture
  `tests/modules/sym/format/symdir/lib.sym` uses `obosym 7`, and
  `tests/modules/sym/format/expect` expects `has format 7; this compiler reads
  6`. If the next free format for this feature is 7, move that fixture to
  `obosym 8` and update the expect line and `sym_test.py` constants/messages;
  otherwise leave it as the next-future-format probe.
- Single-file tests with `type` (not `pub`) do not need field changes because
  their fields are used in the same module.
- The compiler itself (`obo/src/obo.obo`) is one module and has no `pub type`,
  so it needs no field annotations; the language change still compiles it.

Reproduction:

```sh
rg -n '^pub type [A-Za-z0-9_]* = ref record' obo --glob '*.obx' --glob '*.obo'
rg -n 'new [A-Za-z_][A-Za-z0-9_]*::[A-Z][A-Za-z0-9_]*\{' obo --glob '*.obx'
rg -n '^pub type ' obo --glob '*.obx' --glob '*.obo'
```

### 10.2 Classification of the shipping libraries

| Record | Fields today | Decision | Work |
|---|---|---|---|
| `io::Reader` | `fd`, `buf`, `pos`, `size`, `hitEof` | **private** | `newReader` exists; public methods already cover reading. No field access needed by importers. |
| `io::Writer` | `fd`, `buf`, `size` | **private + accessor** | `newWriter` exists. Add `pub fn bufferedBytes(w: &Writer): int` and migrate `tests/lib/write_byte.obx`'s two `len(w.buf)` checks to it. Move `fmt::writePadded`'s buffer loop into `io` as `pub fn writePadded(mut w: &Writer, n: int, width: int): Result[bool]`; keep `fmt::writePadded` as a delegating wrapper or update `wc`/tests. Do not expose `buf`/`size`. |
| `cli::FlagSpec` | short/long/mode/consume/accept/choices/default | **keep public** | Configuration data; external construction in `eza::mkSpec` is intentional. Adding a constructor for eight fields would be boilerplate with no invariant. |
| `cli::Option` | short/long/value/hasValue | **keep public** | Result data read by `eza::deduce`. A field is not state to protect; accessors would add ~10 call-site changes with no benefit. |
| `cli::Parsed` | options/operands | **keep public** | Same: plain result data; `eza` reads `options`/`operands` directly. |
| `fs::Stat` | 14 stat fields | **keep public** | Plain OS data returned by `stat`; accessor explosion, and callers read subsets. `lstat`/`stat` are the constructors. |
| `fs::DirEntry` | name/ino/kind | **keep public** | A scratch in/out record: `eza` constructs it and `fs::next` fills it. A private version needs a mutable accessor protocol for no safety gain. |
| `fs::Dir` | fd/path/buf/text/pos/used/eof/size | **keep public (v1 exception)** | `tests/meta/crafted.obx:59` constructs `new fs::Dir{...}` with a crafted buffer to exercise `fs::next`'s error branches. A test-only constructor or mutable accessor protocol is not worth it in v1; document `Dir` as a deliberate exception. Its `buf` still appears in `next`'s `pf` summary (section 2.1). |
| `html::Html` | text | **private + `raw`** (decided; see 10.4) | `raw`, `escapeText`, `escapeAttr`, `validateUrl`, `join` construct; add `toText` for rendering. Update `html_escape.obx` direct-construction test to `raw`, and update `lib/html.obx`'s comment. |
| `libpq::Connection`, `Rows`, `Buffer`, `Demo` | tok | **private, except deliberate raw constructors** | Module constructors produce them; `tok` is a registry handle. Keep `buffer::wrap(tok)` as the documented raw-token constructor; the C registry remains the security boundary. |
| sqlite `Connection`, `Stmt` | tok | **private** | Same. |
| postgres `Connection`, `Rows` | tok | **private** | Same. |
| postgres `Param` | kind/i/data | **private** | `param_null`/`param_int`/`param_bool`/`param_text`/`param_blob` already construct; `exec` reads inside the module. |
| test fixtures | varied | **add `pub` to record fields only** unless the test is specifically about privacy | 90 `.obx` definitions plus 28 embedded Python definitions. Closed-variant fields are already public: do **not** add `pub` to them. Add `pub` to record fields to preserve old behavior, then add the new negative fixtures separately. |

The rule of thumb: **data records whose fields are the API stay public; records
whose fields are invariants/state become private.** Public fields are still a
deliberate API commitment; default-private makes that commitment visible.

### 10.3 Mechanical audit and migration procedure

1. **Inventory.** Run the `rg` commands above over `.obx`/`.obo`. Repeat inside
   Python test sources for embedded fixture text:
   ```sh
   rg -n '^pub type [A-Za-z0-9_]* = ref record' obo/tests --glob '*.py'
   rg -n 'new lib::[A-Z][A-Za-z0-9_]*\{' obo/tests --glob '*.py'
   ```
   Save the list of record definitions and constructor sites in the
   implementation commit message or a scratch file (not a generated source
   file). Remember that the `.py` definitions are inside strings and are
   compiled by the tests that write them.
2. **Find external field uses.** For each shipping record, search its field
   names in importers:
   ```sh
   rg -n '\.(fd|buf|pos|size|hitEof|text|tok|kind|data|i)\b' obo/lib obo/cmd obo/experimental
   ```
   Filter by the receiver's type; do not rely on the field name alone.
3. **Classify** each record with the table above. When in doubt, keep a plain
   data record public rather than inventing accessors; when the field is an
   invariant (fd, buffer, iterator position, registry token), make it private
   and add/find a function. Keep `fs::Dir` public in v1.
4. **Annotate shipping records** with `pub` on the record fields that remain
   API. Add constructors/accessors for private ones. Update `fmt`/`io` for
   `Writer`, `tests/lib/write_byte.obx` for `bufferedBytes`, and `html` tests
   for `Html`.
5. **Annotate test fixtures.** For `run/`, `sym/` and embedded Python fixtures
   that intentionally use cross-module record fields, add `pub` to **record**
   fields. Do not add `pub` to closed-variant fields: they are already public
   and the parser rejects `pub` there. For fixtures that only use the record in
   its own module, no annotation is needed. Add new `errors/` fixtures for the
   privacy diagnostics; do not weaken existing tests by changing an error
   fixture into a run fixture.
6. **Regenerate symbol files.** The `3` goldens under
   `tests/modules/golden/expected/*.sym` are regenerable with
   `sym_test.py --update` after reviewing a deliberate writer diff. The `92`
   files under `tests/modules/sym/*/symdir` are hand-written/controlled
   fixtures: migrate them with a scripted, reviewed rewrite for the field
   markers plus targeted hash/format edits, not with `--update`. Also update
   `sym_test.py` constants/assertions, the `tests/modules/sym/format` expect
   line, and the `obosym 7` fixture if 7 becomes the real format (move it to 8).
7. **Docs.** Update `LANGUAGE.md` sections 1, 3, 5a and 6 (record fields,
   construction, methods/exports, `.sym` grammar and module errors), the §10
   compile-error table (new visibility/construction/variant rows), every doc
   example that constructs an imported record, `lib/html.obx`'s public-field
   comment, and `HISTORY.md`/`FORK.md`. Update the root `README.md` version and
   live-peak metrics table. Fix the stale `LANGUAGE.md` `--version` text
   (`obo 0.9 sym-format 5`) while editing §6 if the format bump makes it cheap;
   otherwise leave it for the version-update commit and report it.
8. **Gate.** `module_test.py`, `sym_test.py`, `meta_test.py`, `io_test.py`,
   `sym_fuzz.py`, `float_f1.py`, `doc_check.py`, `stdlib.py`/eza, `port_compare.py`,
   the peak budget, the mutant matrix and the eza golden suite. The compiler
   self-host and fixed point are mandatory.

### 10.4 Html: decided private `text`, and the approved template design

**Decision: private `text`.** The approved template design
(`template-literals-design.md`, revision 3) currently says:

- `Html` is an ordinary standard-library record with **public fields**, so
  `new Html{text: ...}` is legal and is exactly an explicit raw operation;
- trust is documented convention, not type enforcement;
- the compiler cannot enforce provenance because the fields are public.

Under this change, the consistent choice is **private `text` plus the public
`raw`/escaping constructors**:

```obo
pub type Html = ref record {
    text: string          // private to module html
}

pub fn raw(text: string): Html { return new Html{text: text} }
pub fn escapeText(s: string): Html { ... }
pub fn escapeAttr(s: string): Html { ... }
pub fn join(parts: &[]Html): Html { ... }
pub fn toText(h: &Html): string { return h.text }   // read-only render accessor
```

This **revises** the approved design's "public fields" sentence. It does not
change the trust model: `raw` is public and any caller can claim any string is
safe, so trust remains convention-level. What it removes is *accidental*
direct construction and the ability to mutate someone else's `Html` in place.
That is worth the small revision.

Template lowering implications:

- The compiler-generated builder already lowers literal chunks to `raw`,
  text holes to `escapeText`, attribute holes to `escapeAttr`, URL holes to
  `validateUrl`, and composition to `join`; it does not need to write
  `new Html{text: ...}` from an importer.
- If the builder currently accumulates `[]Html` and calls `join`, no change.
  If it wants to append bytes directly, add a public `html::appendTo(mut out:
  &[]byte, h: &Html)` or use `toText`; do not expose `text` as a mutable field.
- The SQL domain is separate; if it also wants private fields, that is a
  separate decision in the template design. Do not make `Sql` private as a
  drive-by in this change.
- Tests that currently do `new html::Html{text: ...}` become `html::raw(...)`;
  tests that read `h.text` use `html::toText(h)` or `join`.

This decision revises the approved template design's public-field sentence; the
revision must be recorded in that design's status line when this lands. The old
public-field option is rejected, not held open: it would keep accidental
construction and retain the only reason `Html` appeared in this migration table
as a public-field exception.

Also update the comment block in `obo/lib/html.obx:14-21`, which currently says
`pub type Html = ref record { text: string }` and "`new html::Html{text: ...}`
is legal". It becomes a private-field record with `raw` as the explicit trusted
constructor; the trust-by-convention wording stays, but the direct-construction
sentence goes.

### 10.5 Compatibility policy

This is a breaking language release, not a compatibility shim release:

- No `--legacy-public-fields` flag and no dual meaning of a record declaration.
  One source meaning keeps the checker and the tests honest.
- Old source fails with the section 9 diagnostics; the fix is `pub` on the field
  or a module constructor/accessor. This is an intentional migration cost.
- Old `.sym` files are rejected by the format check. They are build artifacts;
  rebuild them. Do not add a format-6 compatibility reader.
- Downstream users get a single migration note: "exported record fields are
  private by default; add `pub` to fields that are API, or add constructors and
  accessors." The stdlib migration in the same release is the worked example.
- Because the language is pre-1.0, no deprecation window is promised. If a
  downstream project needs one, it can pin the previous compiler; the old
  compiler cannot read new `.sym` files and vice versa.

Migration size estimate: 9 stdlib record definitions plus 4 experimental
wrapper records (with three non-trivial code changes: `io::Writer`
accessor/padded-write helper, `fmt` delegation, and `html::Html` access), 71 real
constructor sites (68 tests, 3 `cmd/eza`; one comment is not a site), 90 `.obx`
test definitions plus 28 embedded Python definitions and 22 embedded Python
`new lib::` sites, 3 regenerable goldens plus 92 hand-written `.sym` fixtures,
plus doc examples and new fixtures. Keep `fs::Dir` public in v1 rather than
adding a test-only constructor. Most of it is mechanical; the risk is in the
library changes and in missing a cross-module field use in a test that only
fails at the gate.

## 11. Tests, docs and mutation targets

### 11.1 Positive module fixtures (`tests/modules/run/`)

- `field_visibility_pub`: mixed record, external read, write, `&` borrow,
  `mut &` borrow, whole-record move, `take`/`replace` of a pub optional field.
- `field_visibility_mixed_layout`: private first, private middle, private last;
  public fields before/after retain correct offsets and values; zero-field
  record constructed externally.
- `field_visibility_constructor`: private-only record plus a factory; factory
  returns a value, importer calls pub methods and reads pub fields.
- `field_visibility_nested`: public outer field of an imported public-inner
  type; nested `new`; public field path through two modules.
- `field_visibility_array`: `new []lib::T(0)`, append factory values, index,
  read/write pub fields, move elements; `new []?lib::T(n)`.
- `field_visibility_global`: `pub var`/`pub let` of a mixed record; importer
  reads/writes pub fields and assigns a factory value; private field untouched.
- `field_visibility_methods`: pub method reads/mutates private state; method/
  field name collision is tested by its negative companion.
- `field_visibility_handle`: `handle` to a mixed record; `h.pub` works; the
  private-field negative is its own error fixture.
- `field_visibility_private_type_field`: exported record with a private field
  of a private named type; module constructor and method use it; importer only
  sees the public fields.
- `field_visibility_interface_carrier`: a private field of a non-interface type
  that is captured normally; the interface-carrying rejection is the negative
  fixture below.
- `field_visibility_html` (after migration): `raw`, `escapeText`, `join`,
  `toText`; the direct-construction negative is its own error fixture.
- `field_visibility_pf_leak`: B1's both-directions fixture. `pub type R = ref
  record { buf: []byte; n: int }` (both private) and
  `pub fn reset(mut r: &R) { r.buf = new []byte(0); r.n = 0 }` produce
  `type R = ref record { priv; priv }` and
  `fn reset(mut p0: &R) pfree 0x1 pf 0 "=/buf"`. Renaming `n` keeps the
  interface hash; renaming `buf` changes it. The fixture also asserts that
  `"=/buf"` (the private name) is present in the summary and is accepted, not
  redacted.

### 11.2 Negative module fixtures (`tests/modules/errors/`)

- `field_private_read`, `field_private_write`, `field_private_borrow`,
  `field_private_move`: each exact section 9 access message.
- `field_private_construct`: external `new lib::T{...}` with one private field,
  even when all other fields are supplied.
- `field_private_construct_omitted`: omitting the private field still gets the
  same construction error, not a missing-field error.
- `field_private_nested_construct`: public outer, private inner; error points at
  the inner `new`.
- `field_private_variant_pub`: `pub` on a variant field.
- `field_public_private_type`: a `pub` field of a private named type still gets
  the existing closure error.
- `field_private_method`: calling a private field as a method gets the exact
  section 9 message
  `type '<q>' has no public field or exported method '<f>' (only 'pub' declarations are visible outside module '<m>'; mark the field 'pub' or add a public method)`,
  identical in a full build and in `--check`, not `has a field, not a method`.
- `field_private_handle`: `h.priv` is the access error.
- `field_private_interface_capture`: the `priviface` capture rejection, both
  full build and `--check`.
- `field_private_html_construct`: `new html::Html{text: ...}` is the
  construction error after the Html migration.

### 11.3 Symbol-file tests (`tests/sym_test.py`)

- Mixed record round-trip and byte-identical `--reemit-sym`.
- A private-first/middle/last layout: `pub` offsets and private slot count.
- Canonical output always writes `pub`; a bare-field hand-written input is
  rejected.
- `pub priv: int` and `pub priviface: int` round-trip: the reader accepts them
  as public fields named `priv`/`priviface`, and canonical re-emission is
  byte-identical (H4).
- `priv` with a name/colon, `priviface` in a variant, unknown marker: exact
  malformed-symbol messages with line:col.
- Duplicate public field remains an error; private slots have no duplicate-name
  case.
- Interface hash: public↔private changes it; a private slot count/order change
  changes it; a private name/type change with the same class does **not** change
  it *if no exported summary names the field*, and **does** change it when a
  `pf`/`pfree` path names the field (B1 fixture); `priviface` changes it;
  `depiface` updates.
- Full build disagreement: mutate `pub`↔`priv` or `priv`↔`priviface`, rehash,
  and get `symDisagree`.
- **B2 byte-identical re-emission with `priviface`:** build a module with a
  private interface-carrying field, run `--reemit-sym`, and compare all bytes.
  The writer's `field.privateIface || containsIface(field.typ)` term is what
  keeps the marker from degrading to `priv`; a test that reads the re-emitted
  file and checks the marker catches its removal.
- **B2 `--check` capture rejection from the re-emitted file:** delete the
  dependency source, use the re-emitted `.sym`, and confirm that a lambda
  capture/function type that carries the private interface field is rejected
  exactly as in the full build.
- **B1 concrete library summaries:** build `io.obx` and `fs.obx` after the
  migration and assert `io.sym` still has `pf 0 "=/buf"`/`"!/buf"` for the
  Reader/Writer methods and `fs.sym` still has `pf 0 "=/buf"` for `next`;
  `fs::Dir.buf` remains public in the v1 exception, but the summary path is
  unchanged and exposed.
- **B3 multi-field carrier visited state:** define
  `type C = ref record { s: Shape }`, `type B = ref record { c: C }`, and
  `pub type A = ref record { b: B; c: C }` with both fields private. `B`'s
  traversal visits `C`; the second field `c` must still be `priviface`, not
  `priv`. Assert the `.sym` field list is `{ priviface; priviface }`. A
  cross-field `containsIfaceIn` call with a stale `ifaceSeen` fails this.
- `--check` from a source-less dependency with mixed records: public access and
  all-public construction pass; private access/construction fail; private
  carrier capture fails.
- Old format 6 rejection (exact message), and `--version` reports the next free
  numbers. If the next real format is 7, update the existing
  `tests/modules/sym/format` probe from `obosym 7` to `obosym 8` and its expect
  line to `has format 8; this compiler reads 7`; do not leave the probe equal
  to the new real format.
- A tampered visibility change without rehashing is caught by the interface
  hash; a rehashed one is accepted by `--check` and documented as trusted-input
  behavior.

### 11.4 Docs and other gates

- Update `LANGUAGE.md` examples and add runnable examples for:
  `pub` field, private field access error, factory construction, and the `.sym`
  field grammar. `doc_check.py` must compile every `// example: runs` block.
  Add the new visibility/construction/variant rows to the §10 compile-error
  table.
- Update `HISTORY.md`/`FORK.md` release notes and the `.sym` example in
  `LANGUAGE.md` section 6. Document that `pfree`/`pf` summary paths may name a
  private field and that this leak is accepted. Update the root `README.md`
  version and live-peak metrics table. Fix the stale `LANGUAGE.md` `--version`
  text (`obo 0.9 sym-format 5`) if the same edit touches §6.
- Update `obo/lib/html.obx:14-21`: remove the public-field/direct-construction
  sentence and describe private `text` plus `raw` as the explicit trusted
  constructor.
- `module_test.py` gains the new `run/`/`errors/` directories automatically;
  no harness change unless a fixture needs an `includes` file.
- Gates: `meta_test.py`, `io_test.py`, `sym_test.py`, `sym_fuzz.py`,
  `float_f1.py`, `module_test.py`, `doc_check.py`, `stdlib.py`/eza,
  `port_compare.py`, the peak budget, the mutant matrix and the eza golden
  suite. `io_test.py` covers the `Writer` accessor/`write_byte` change;
  `meta_test.py` covers the public `fs::Dir` craft fixture; `sym_fuzz.py` and
  `float_f1.py` cover the embedded Python fixture sources.
- `port_compare.py` must show the compiler self-host, fixed point, and no
  memory regression beyond the measured delta under `2,500,000`.
- `peak_budget.py` remains unchanged; record the measured baseline delta.
- `mutate_port.py`/`mutate_fork.py`/`mutant_catchers.tsv` add targets for:
  - default flipped back to public (remove the field visibility check),
  - access check removed for read/write/borrow,
  - construction check removed,
  - `closureCheck` traversing private fields (should fail the private-type
    fixture),
  - `symCompareFields` ignoring visibility/`priviface` or recomputing from the
    parsed placeholder,
  - writer omitting `pub`/`priv`, or dropping the `field.privateIface` OR term,
  - reader accepting a bare field as public,
  - placeholder offset computed wrong,
  - `containsIfaceIn` ignoring `privateIface`, or a per-field query that does
    not truncate `ifaceSeen`.
- `stdlib.py`/`eza` golden suite: the stdlib migration must keep byte output
  identical; `fmt`/`io` changes are the only behavior-adjacent ones.

## 12. Alternatives rejected

- **Explicit `priv` with public default.** Rejected. It preserves the unsafe
  default, requires annotating almost every existing field in every library,
  and adds a keyword for the common case. Default-private plus `pub` is the
  settled decision.
- **Capitalization or naming convention** (`Text` public, `text` private).
  Rejected: no keyword, silently changed by a rename, conflicts with type names,
  and cannot be checked reliably by a human or a tool.
- **Accessor-only opaque records everywhere.** Rejected: it turns plain data
  records (`Stat`, `DirEntry`, `Option`, `Parsed`, `FlagSpec`) into accessor
  boilerplate, makes construction awkward, and hurts exactly the data-oriented
  code obo is good at. Use private fields for invariants, not for every record.
- **Module-level `pub record { ... }` sugar.** Rejected: it cannot express mixed
  records, which are the point; it also makes all fields public at once and
  invites a second visibility syntax. Per-field `pub` is precise.
- **`pub(read)` / `pub(write)` / `pub(init)`.** Rejected for v1: more grammar,
  more `Field` state, and method/accessor patterns already cover read-only
  exposure. Could come later as an extension if real APIs need
  read-public/write-private fields.
- **Friend modules / `pub(module a, b)`.** Rejected: no module graph privacy
  model, brittle to file moves, and the factory/accessor pattern is simpler.
  Could come later only with a deliberate module-system design.
- **Keep all fields public and rely on lint.** Rejected: not enforceable by the
  compiler, and the user decision explicitly asks for compiler semantics.
- **Emit all field names/types in the `.sym` field list for simpler
  diagnostics.** Rejected in favor of opaque slots; the caller's spelling is
  enough for diagnostics, and hidden types should stay hidden in the field
  list. This does not redact `pf`/`pfree` summary paths: those can still name a
  private field, and v1 accepts that leak (section 2.1). Could be revisited if
  tooling needs a separate debug artifact, but not in the interface file.
- **Private fields in closed variants in v1.** Rejected/deferred; see section 5.
  It is a complete alternative but doubles the pattern/construction/`.sym`
  surface for little current benefit.

## 13. Resolved decisions

These were open in the first revision; they are settled and are not reopened by
this note.

1. **Html.** Private `text` plus public `raw`/escapers/`toText`; the approved
   template design's public-field sentence is explicitly revised. (Section
   10.4.)
2. **Mixed-record `.sym`.** Opaque `priv`/`priviface` slots as specified,
   with the `pf`/`pfree` private-name leak accepted for v1. (Sections 2 and
   2.1.)
3. **Private field types.** Closure relaxation accepted; private named types
   are allowed in private fields, with `priviface` as the only cross-boundary
   carrier property. (Section 4.)
4. **Stdlib data records.** Keep `FlagSpec`, `Option`, `Parsed`, `Stat`,
   `DirEntry` public; keep `fs::Dir` public as a v1 worked exception; make
   `io::Reader`, `io::Writer` (with `bufferedBytes`), `html::Html`, and
   token-bearing wrappers private. Keep `buffer::wrap` as an explicit raw-token
   constructor. (Section 10.2.)
5. **Version coordination.** No reservation: take the next free format for this
   feature, or one shared bump with `^I` if they land together. (Section 7.6.)
6. **`pub` on a field of a private type.** Allowed silently, no warning.
   (Section 1.1.)

## 14. Acceptance checklist

- [ ] A record field is private by default; `pub name: T` makes it public.
- [ ] Inside the module all fields are accessible; outside only `pub` fields.
- [ ] Mixed records preserve offsets; `.sym` has `pub` and `priv`/`priviface`
      slots and round-trips canonically, including `pub priv:` and
      `pub priviface:` and a byte-identical re-emitted `priviface`.
- [ ] External `new` is refused when any field is private; the factory pattern
      is documented and tested.
- [ ] Private field types may be private; public fields still pass the closure
      check.
- [ ] Private field names/types that do not affect an exported summary keep the
      interface hash; those that change a `pf`/`pfree` path change it. The
      accepted private-name leak in summaries is documented.
- [ ] Carrier checks truncate the visited set per field; the B3 two-field
      fixture emits `priviface; priviface`.
- [ ] Closed variant fields stay public; `pub` there is the exact error, and
      migration does not add `pub` to variant fields.
- [ ] Methods, interfaces, borrows/moves, handles, arrays, globals and C
      wrappers follow the interaction rules; the private-field-as-method
      diagnostic is identical in full build and `--check`.
- [ ] Format 6 is rejected; the 3 goldens and 92 hand-written `.sym` fixtures
      are migrated; the `obosym 7` wrong-format probe is moved to 8 if 7 is the
      next real format; `--check` from source-less `.sym` passes the positive
      and negative fixtures.
- [ ] `fs::Dir` remains public; `io::Writer` is private with `bufferedBytes`;
      `tests/lib/write_byte.obx` is migrated.
- [ ] Compiler peak stays under `2,500,000`; the 16-byte-per-`Field` cost
      (including function-type parameters) is measured.
- [ ] Stdlib/cmd migration keeps output byte-identical; `meta_test.py`,
      `io_test.py`, `sym_fuzz.py`, `float_f1.py`, `stdlib.py`/eza, docs and doc
      examples are in the gate; mutation targets catch each removed check.
