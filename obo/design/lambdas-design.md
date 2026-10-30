# Lambdas (phase 9) — design note

Status: **design only**, for independent review. Baseline: `main` at
`90ad7d3` (`Merge branch 'docs-06': document the promoted 0.6 seed`), read-only
in the worktree `/home/chris/obx/.claude/worktrees/lambdas-design` on branch
`lambdas-design`. The compiler source is 11,027 lines (`obo/src/obo.obo`),
`LANGUAGE.md` 2,081 lines, `FORK.md` 5,235 lines; the promoted seed
`obo/seed/obo` reports `obo 0.6 sym-format 2 abi 1`. The interfaces
implementation is in flight on another branch and is not merged; this design
targets the interface baseline `0.7`/format `3` and therefore targets
`0.8`/format `4` for Stage A and `0.9`/format `4` for Stage B (section 6.5).
Nothing is implemented by this pass: no source, test, seed, runtime or tool
file changes. The only repository write is this file.

Every claim about the current compiler below was checked against
`obo/src/obo.obo` at this baseline; `file:line` references are to that file,
and function names are the stable anchors because the interfaces merge will
shift every line number. Behaviour was probed with the compiler built from
this tree by `seed/obo` (`make obo`); the probes are quoted where they matter.
The project invariants hold unchanged: single pass, no AST/IR, source read
once, self-hosting fixed point, textual `.sym` with hashes, no test weakening,
one compiler feature in flight, design review before implementation, and one
seed promotion after review.

Scope is the user's roadmap phase 9: **Stage A** non-capturing function
literals, a function type and a call through a function pointer; **Stage B**
capturing closures only as second-class, non-stored values, with borrow
captures scoped to the call, consistent with references being second-class.
Owned captures are included only if a sound design emerges; section 4.7
recommends deferring them. Lambdas exist to serve sorting comparators and tool
callbacks (section 10.5), not to add a general closure language. Do not
over-scope.

---

## 0. Recommendation in one page

1. **Syntax uses only existing tokens.** A lambda literal is an expression
   `fn [CaptureList] (Params) [: Ret] { Body }`; the capture list is present
   only for a capturing lambda. A function type is `fn (ParamTypes) [: Ret]`
   in type position. `fn`, `(`, `)`, `[`, `]`, `,`, `:`, `&` and identifiers
   are already tokens; the scanner, keyword table (`obo.obo:145-158`), token
   constants (`obo.obo:36-103`), `--tokens` and `port_test.py` do not change.
2. **The three `fn` contexts are disjoint and need no lookahead.** At top
   level `fn (` is a method declaration (`obo.obo:8446-8469`); in an
   expression `fn (` is a lambda literal (today: `expected an expression`,
   probed); in a type position `fn (` is a function type (today: `expected
   identifier`, probed, because `parseType` (`obo.obo:3973-4014`) and
   `symParseType` (`obo.obo:9847-9895`) do not accept `T_FN`). No valid
   program changes meaning.
3. **Function values are second-class by construction.** A function value may
   be a parameter, a local, or a call argument. It is never stored in a field,
   array, global, constant or `Result` payload, never returned, and never an
   optional, handle, reference or interface. It is not owned and is copied by
   value. A function type is allowed as a parameter type, a local type and a
   type alias; it is rejected in every other type position.
4. **A function value is one word: a pointer to an immutable cell whose word
   0 is the code address.** A non-capturing lambda uses an 8-byte read-only
   cell `[code]`. A capturing lambda uses a frame-resident cell
   `[code][cap0][cap1]...` in the creating function's frame. The cell address
   is the function value; copying it is a word copy; no ownership, no free, no
   heap allocation.
5. **The hidden lambda body is a normal function with one hidden leading
   parameter, the environment (the cell pointer).** Visible parameters follow
   it. A call through a function value loads the cell, pushes the cell as
   hidden argument 0, pushes the visible arguments, and emits
   `call qword ptr [cell]`: no rel32, no fixup, no vtable, no name mangling.
   The cell is the environment as well as the code holder, so no second
   indirection is needed.
6. **The body is compiled at the literal's source position, single pass.**
   It is a code island: a `jmp` over it, its own prologue/frame, body,
   epilogue and `dropFrame`, then the outer expression resumes. The outer
   function's complete compilation context is saved and restored around it
   (section 5.3). The body is compiled with register homes off (`homes =
   false`), so there is no nested `homeRetry`; the outer function's homes are
   untouched. No AST is built and the source is read once.
7. **Function types are structural and non-generic.** A function type is
   `^Type` with `kind = "fn"`, `base` = the return type and one `Field` per
   parameter whose `name` is `""` (owned) or `"&"` (read-only) and whose
   `typ` is the parameter type. Structural equality is exact: same parameter
   count, modes and types, and same return type; no variance, no subtyping.
   This reuses the existing `Type` layout (`obo.obo:882-895`); no `Type`
   field is added.
8. **Function type parameters are owned or read-only `&` only.** There is no
   `mut &T` parameter in a function type or a lambda header in v1, so an
   indirect call has no mutable reference argument and the mut-alias surface
   stays small. `mut` captures and owned captures are deferred (section 4.7).
9. **Stage B capture lists are explicit:** `fn [a, b](x: int): int { ... }`.
   The list names enclosing locals or parameters; an uncaptured enclosing
   local is an error, and module globals are used directly without capture.
   Explicit lists keep the single-pass compiler single-pass: the capture
   layout is known before the body is emitted, so captures are loaded at the
   hidden function's entry, with no discovery pass and no lazy initialization
   order.
10. **Captures are read-only borrows or value snapshots.** An owned local
    (record, closed type, array, optional, `Result`), or a reference, is
    captured as a read-only pointer (to the whole local's object/word, or to
    the reference's current target); the body cannot mutate or free it.
    `int`/`byte`/`bool` are captured by value (a snapshot); a handle `^T` and
    a function value are captured by value (a token or cell pointer). Strings
    and interface values are rejected in v1. There is no `mut` or owned
    capture syntax.
11. **A capturing lambda may only be passed directly as a call argument.**
    It cannot be bound to a local, stored, returned or otherwise smuggled.
    This is "captures scoped to the call": the creating function is suspended
    for the whole call chain, so the frame cell and every capture stay alive;
    the callee cannot store the value. Non-capturing lambdas are ordinary
    second-class values (parameters, locals, arguments, copies).
12. **Capture bindings join the existing argument checks.** A capturing
    closure argument is held across the later arguments (`holdArg`/
    `argOrderCheck`, `obo.obo:4773-4814`), appears in the mut-alias,
    deep-reference, handle-borrow and global-reference checks
    (`obo.obo:4960-5023`), and is invalidated by the reference machinery. A
    pure callee (which every callback-taking helper can be) accepts deep,
    handle-rooted and global-rooted captures because it cannot free or write
    through them; an impure callee refuses them exactly like a reference
    argument. There are no new ownership rules, only new bindings.
13. **Every function value is pure; an indirect call is a pure call.** A
    lambda body is checked after it is compiled against the compiler's own
    purity predicate (`pureFn` and `!writesHandles`, the `handleSafe` shape
    the interfaces design uses); a body that frees a global, writes through
    a handle, calls an impure function or one whose summary is not final, or
    otherwise becomes `freesAll`/`writesHandles` is rejected (`a lambda must
    be pure...`). It has no mut-reference parameters of its own, so it cannot
    free below one directly.
    Therefore an indirect call may not free a global, write a handle or free
    below a read-only reference argument; it may consume owned arguments (the
    caller moved them, and the call may free them) and return
    owned/string/`Result` values. The call is marked pure in
    the compiler's summary, so the caller's references stay valid and a
    helper that calls a callback can itself be pure. This is what makes a
    comparator on array elements possible: `cmp(&items[i], &items[j])` is a
    pure call and the deep element references are accepted. Impure callbacks
    are out in v1 (section 14); they would need an effect type or a `pure`
    qualifier.
14. **Symbol files gain a function-type expression and nothing else.** The
    writer emits `fn(int, &T): bool` in parameter, alias and (never) return
    or field positions; the reader parses it. A lambda body is local source
    and never appears in a `.sym`; no code or environment address is ever
    written. Format becomes `obosym 4`, compiler `0.8` (Stage A) / `0.9`
    (Stage B), ABI stays `1`, and all committed `.sym` fixtures migrate.
15. **Restrictions keep the nested compiler tractable:** no lambda literal in
    a `while` condition (the condition is lifted and truncated,
    `obo.obo:7251-7252`, then re-emitted, `7309-7310`), none in a `defer`
    body (out-of-line compiled, `obo.obo:7834-7907`), none at top level, and
    a lambda body may not call a function whose body is still being compiled
    (an active enclosing function; its summary is not final). A lambda has no
    name and cannot call itself; recursion through an already-defined pure
    function and a function value is allowed and pure.

The genuine user-facing forks are the conservative capture rule (explicit,
read-only, call-argument-only), the **pure-callback** restriction (a lambda
may not free a global or free owned state through a handle; it can still append to a
global array or write scalar globals), and the exclusion of named function
values (`sort(xs, compare)` needs a lambda wrapper). Section 14 records the
defaults and the extension paths; none blocks review.

---

## 1. Scope and non-goals

In scope:

- a lambda literal expression, non-capturing first (Stage A), capturing with
  an explicit, read-only capture list second (Stage B);
- a function type usable as a parameter type, a local type and a type alias,
  with exact structural compatibility and a call through a function value;
- the ownership, aliasing, argument-order and effect rules for function
  values and capture bindings, including the pure-callback contract (a
  lambda body is pure in the compiler's sense; section 3.4);
- modules and symbol files: function types in exported signatures, `--check`,
  `--sym-dir`, `--emit-sym`, `--reemit-sym`, cross-module callbacks;
- tests, mutants, docs and gates needed to merge safely;
- the intended `sort`/`eza` comparator shape (section 10.5), not the refactor.

Not in scope (extension points only):

- named function values (`let f = compare` where `compare` is a top-level
  function) and method values; a lambda wrapper is the v1 spelling;
- escaping or stored closures; returning a function value; closures in
  fields, arrays, globals, constants, `Result` payloads or optionals;
- `mut` captures, owned captures, and string or interface captures;
- **impure callbacks**: a lambda body that frees a global, writes through a
  handle, frees below a mut-reference parameter or calls a function whose
  body is not final is rejected. A pure callback can still append to a
  global array or write scalar globals; an effect type/`pure` qualifier for
  genuinely impure callbacks is an extension;
- mutable (`mut &T`) parameters in function types or lambda headers;
- function types in interface declarations, interface implementations, or
  as interface values; interfaces of function types;
- generic, variance, inheritance, subtyping or dynamic loading;
- devirtualisation, inlining or optimisation of indirect calls;
- refactoring `obo/src/obo.obo`, `obo/lib/*`, `obo/cmd/*` or the tools to use
  lambdas; the compiler source stays in the pre-lambda subset in round 1;
- threads/concurrency; a closure cannot cross a thread (there are none);
- debug information.

The project invariants hold unchanged (single pass, no AST/IR, self-hosting
fixed point, explicit seed promotion, no test weakening, textual `.sym` with
hashes, one compiler feature in flight).

---

## 2. Syntax and grammar

### 2.1 Lambda literal

```text
LambdaExpr ::= "fn" [ CaptureList ] "(" [ Params ] ")" [ ":" Type ]
               "{" StatementSeq "}"
CaptureList ::= "[" Capture ("," Capture)* "]"
Capture     ::= IDENT
Params      ::= ParamGroup ("," ParamGroup)*
ParamGroup  ::= IDENT ("," IDENT)* ":" ( "&" Type | Type )
```

- `fn` is the existing `T_FN` token (`obo.obo:48`). `[`/`]` are existing
  tokens. Nothing is added to the scanner or keyword table.
- The lambda's parameter syntax is the function parameter syntax without
  `mut`: one type and `&` per group, several names per group, each name one
  positional parameter. `mut` before a lambda parameter is rejected
  (`a lambda parameter cannot be mut; a function value takes owned or
  read-only parameters`), because a function type cannot express it.
- `: Ret` is optional; omitted means `void`. The return type is never
  inferred from the body. A lambda body with `return value` and no declared
  return type is an error (`a lambda that returns a value needs a return
  type`); the body is still parsed and the first bad `return` reports.
- The capture list is present only for a capturing lambda. `fn []` (an empty
  list) is an error; write `fn (` for a non-capturing lambda.
- The body is a required `{ ... }`; a lambda has no forward heading and no
  name.
- **Line rules** follow the existing scanner. A newline after `)` or `]` is
  a statement separator (`semiAfter`, `obo.obo:224-230`), so the `{` of the
  body must be on the same logical line as the closing `)` of the header;
  parameter lists and capture lists may be split after commas (`contChar`
  includes `,`), but the `(` after `]` must be on the same line. The closing
  `}` of a lambda body followed by a newline produces `T_SEMI`, so put the
  `,` or `)` of the enclosing call on the same line as `}`.
- A lambda literal is an expression. It cannot begin a statement by itself
  (`statement`, `obo.obo:7909-7975`, has no `T_FN` case; a bare lambda does
  nothing useful anyway). It cannot be invoked directly (`fn (...)(...)`
  is not a postfix call form); bind it to a local or pass it as an argument.

Examples:

```obo
let inc = fn (x: int): int { return x + 1 }
println(inc(2))

fn apply(x: int, f: fn(int): int): int { return f(x) }
println(apply(3, fn (x: int): int { return x * 2 }))

let log = fn (s: string) { println(s) }
log("hello")

let pair = fn (a: int, b: int): Pair { return new Pair{a: a, b: b} }
```

### 2.2 Function types

```text
FnType   ::= "fn" "(" [ FnParamTypes ] ")" [ ":" Type ]
FnParamTypes ::= FnParamType ("," FnParamType)*
FnParamType  ::= [ "&" ] Type
```

- `fn(int): int`, `fn(&Item, &Item): int`, `fn(): bool`, `fn(string)`,
  `fn(fn(int): int): int` (a higher-order callback). An omitted return is
  `void`.
- A parameter mode is owned (no `&`) or read-only `&`. `mut &T` is not
  allowed; a leading `mut` is not special in type position (the parser
  reports the type `mut` as unknown unless the user declared a type with that
  name, and the trailing `&` then fails; the lambda header has the specific
  diagnostic). This keeps the function type's mode grammar to two cases and
  avoids a contextual-word branch.
- `&` before a non-owned type (`&int`, `&string`, `&fn(...)`) is rejected by
  the same rule as references (section 9). `&T` for an owned type is allowed:
  `&[]T`, `&?T` and `&Result[T]` are ordinary read-only references, exactly
  as in a function declaration.
- A function type is **structural**. Two spellings (through aliases, or two
  modules' structurally equal types) are the same type for assignment and
  `.sym` comparison. No nominal identity, no variance.
- A function type is allowed as:
  - a parameter type of a function, method or lambda (a bare `fn(...)`, not
    `&fn(...)`);
  - a local `let`/`var` type (the local needs an initializer);
  - the right-hand side of a `type` alias (`type Cmp = fn(&Item, &Item):
    int`);
  - an exported type alias (subject to the export closure rules).
- A function type is rejected as: a function/method/lambda/interface-member
  return type; a record or variant field; an array element (`[]fn`); an
  optional (`?fn`); a handle (`^fn`); a reference (`&fn`); a `Result` payload
  (`Result[fn]`); a global; a constant; a method receiver; an interface
  member parameter in v1; and inside a function type as a return type (a
  function value cannot return a function value).
- A type alias to a function type is transparent, like every alias. A
  function type has no owner, name or uid; an alias symbol points at it. The
  `.sym` writer always emits the underlying `fn(...)` expression, never the
  alias name (the existing `appTypeExpr` policy, `obo.obo:9350-9378`).

Examples:

```obo
type Item = ref record { name: string; n: int }
type Cmp = fn(&Item, &Item): int

fn apply(x: int, f: fn(int): int): int { return f(x) }
fn sortI(items: &[]Item, cmp: Cmp) { /* ... */ }
```

### 2.3 Calls

A function-typed local or parameter is called by name with the ordinary call
syntax:

```text
Call ::= Name "(" [ ArgList ] ")"
```

- The parser's designator path already ends at the bare name; the token is
  `T_LPAREN` (`designatorT`, `obo.obo:5706-5715`). Today a non-function local
  followed by `(` is `'<x>' is not a function` (`obo.obo:5713-5715`,
  probed); a local whose type is `fn(...)` now takes the indirect-call path.
  The message for every other value stays byte-identical.
- A function value is called only through a **bare name** (a local or
  parameter). There are no function-typed fields, elements, globals or
  returns, so no designator path can produce one; `(f)(x)` is not a call
  syntax (the postfix loop lives in `designatorT`, not after a parenthesized
  expression), and `f.m()`/`f[i]` are type errors (function values have no
  fields, methods or indexing).
- An indirect call **ends the designator**, exactly like a method call
  (`obo.obo:5755-5761`, `methodCall`): `f().n` and `f()[0]` are syntax
  errors at the enclosing construct. Store the result in a local to continue.
- The arguments are converted and checked exactly as a direct call's:
  - an owned parameter (`fn(T)`) moves the argument (`consume`,
    `obo.obo:2942-2977`), including owned records, arrays, optionals,
    `Result` cells and strings;
  - a read-only `&` parameter borrows the argument (`borrowBind`,
    `obo.obo:3045-3054`), with the existing read-only/writability and
    argument-order rules;
  - scalars, handles and function values are copied (non-owned).
- The call is checked against the function type's parameter count, modes and
  types and its return type. There is no inference and no coercion beyond
  the existing byte/int and variant-to-closed rules; a function type is
  exact.
- A lambda literal itself is not callable in the same expression. Bind it
  to a local (`let f = fn (x: int): int { ... }; f(1)`) or pass it as an
  argument.
- `try f()` is allowed when `f`'s function type returns `Result[T]` and the
  enclosing function returns a `Result` (`tryExpr`, `obo.obo:6111-6195`);
  the operand is a normal call result. `defer f()` is allowed; the callee
  is called at the block's exit.

Examples:

```obo
fn apply(x: int, f: fn(int): int): int { return f(x) }

fn main() {
    let f = fn (x: int): int { return x + 1 }
    println(f(2))                       // 3
    println(apply(3, fn (x: int): int { return x * 2 }))  // 6
    var g = fn (x: int): int { return x - 1 }
    g = fn (x: int): int { return x * 10 }
    println(g(4))                       // 40
    // f().n and f()[0] are syntax errors: an indirect call ends the
    // designator; store the result in a local first
}
```

### 2.4 Capture lists (Stage B)

```text
LambdaExpr ::= "fn" "[" Capture ("," Capture)* "]" "(" ... ")" ... "{ ... }"
Capture    ::= IDENT
```

- A capture name resolves to a local or parameter **of the immediately
  enclosing function** (the function whose body text contains the literal),
  searching the open block scopes at the literal's position. It does not
  resolve a module global, a function, a type or a constant: those are used
  directly inside the body without capture. If a name is an enclosing local,
  it must be captured; the body sees the capture, and the capture shadows a
  global of the same name, preserving lexical scoping.
- A capture list entry is a use of the outer local at the literal's position:
  a moved local, a freed reference or a dead tracked handle is refused with
  the existing message (`'<x>' was moved and has no value here`,
  `'<x>' may point to freed memory: ...`). A handle capture with a tracked
  origin also contributes the origin binding to the call-site checks.
- Captures are evaluated left to right at the literal site and stored in the
  cell. A scalar capture is a snapshot; an owned object, reference, optional,
  `Result`, handle and function value capture stores the current pointer,
  optional word, token or cell pointer (sections 4.2 and 4.3).
- A capture name may not repeat, and may not be the name of one of the
  lambda's parameters (`capture '<x>' is also a parameter of the lambda`).
- There is no capture mode marker: `fn [mut x]`/`fn [move x]` are not
  supported forms (mutable/owned captures are out, section 4.7); the parser
  reports the capture-name/not-a-local or `expected ',' or ']'` error, with a
  hint where practical.
- A capturing lambda literal may appear **only as a direct argument in a
  call** (a function, method or indirect call). It may not appear in a
  `let`/`var` initializer, an assignment right-hand side, a return value, a
  record field or array element (those types are rejected anyway), a global
  initializer, a `while` condition or a `defer` body. The parser tracks the
  argument context and the two prohibited contexts (section 5.2).
- A lambda literal with no capture list is non-capturing even if its body
  names a module global.

Examples:

```obo
fn sortI(items: &[]Item, cmp: fn(&Item, &Item): int) { /* ... */ }

fn main() {
    let step = 1
    let opts = new Opts{descending: false}
    sortI(items, fn [step, opts](a: &Item, b: &Item): int {
        if opts.descending { return (b.n - step) - (a.n - step) }
        return (a.n - step) - (b.n - step)
    })
}
```

`step` is a snapshot; `opts` is a read-only borrow of the whole record. The
body may read `opts.descending` and call read-only methods on `opts`; it may
not assign to it, move it or free it.

### 2.5 Ambiguity and interaction with existing syntax

| construct | today | with lambdas |
|---|---|---|
| top-level `fn name(...)` | function declaration (`obo.obo:8440-8565`) | unchanged |
| top-level `fn (r: &T) name(...)` | method declaration (`obo.obo:8446-8469`) | unchanged |
| top-level `fn (a, b: &T) ...` | method error | unchanged; a top-level lambda is not a declaration |
| expression `fn (` | `expected an expression` (probed) | lambda literal |
| type position `fn (` | `expected identifier` (probed) | function type |
| record literal `new T{...}` | expression (`obo.obo:5923-6007`) | unchanged; a lambda starts with `fn`, so no block/record ambiguity |
| block `{ ... }` | statement/arm body | unchanged; a lambda body's `{` follows its header |
| `x.f` / `x.m()` | field / method | unchanged; function values have neither |
| `f(args)` on a local `f` | `'f' is not a function` (probed) | indirect call if `f`'s type is a function type; unchanged otherwise |
| `let f = x` where `x` is a function name | `functions can only be called` (`obo.obo:5656-5659`) | unchanged; named function values are out (section 14) |

No new token, keyword-table entry or scanner rule is introduced. `.sym`
reuses `fn` in type expressions (section 6).

---

## 3. Semantics: Stage A (non-capturing function literals)

### 3.1 What a function value is

A function value is a non-owned one-word value: the address of an immutable
cell. For a non-capturing lambda the compiler emits an 8-byte read-only cell
containing the absolute code address of the hidden lambda body. The cell is
shared by every execution of the literal (it is static data), so creating a
non-capturing function value costs no runtime work and copying it is a word
copy.

The compiler also creates a hidden function symbol with `kind = "lambda"`, a
fresh `FnRec`, and one hidden leading parameter, the **environment** (the
cell address), followed by the visible parameters. The hidden function's code
is a normal function body: prologue, frame, statements, epilogue, `ret`. The
environment parameter is not visible to source. Its type is `int` (a raw
pointer word) and its mode is read-only (1); the hidden body does not own it.

```text
cell (read-only, non-capturing): [ code_address ]
hidden code: fn (env: int, visible params...) -> ret
```

### 3.2 Second-class rules

| position | allowed | reason |
|---|---|---|
| parameter of a function/method/lambda | yes | one word, no ownership |
| `let`/`var` local | yes (with an initializer) | one word, copyable |
| call argument | yes | ordinary argument conversion |
| assignment to a local of function type | yes | word copy |
| return value | no | one function type covers both kinds; a return could carry a capturing closure out of its frame, and allowing only non-capturing returns would need a capture/purity distinction in the type |
| record/closed-type field | no | function values are parameters and locals only |
| array element, `[]fn`, `Result[fn]` | no | same |
| global, constant | no | same |
| optional `?fn`, handle `^fn`, reference `&fn` | no | same |
| interface value, interface member signature | no (v1) | interfaces are a separate second-class representation |
| method receiver | no | function types have no methods |
| `match`, `==`, `drop`, `take`, `replace`, `handle`, `alive`, `println` | no | not a record/int/handle/owned value |

The first cut deliberately keeps **all** function values second-class, not
just capturing ones: a non-capturing function value is statically
representable, but allowing it into fields or globals would make function
types part of record layout, descriptors, global initialization, `.sym`
declarations and the module surface. That is a different feature (and is
where owned/escaping closures would start). Parameters, locals and arguments
cover the comparator/callback use cases.

### 3.3 Calling

`f(args)` where `f`'s static type is `fn(P...): R`:

1. Evaluate `f` (a local/parameter read; a function-typed parameter is a
   plain word) and keep its cell pointer in a hidden frame temporary for the
   duration of the call.
2. Check the argument count and each argument against the function type's
   parameters and modes, using the existing `checkAssign`/`consume`/
   `borrowBind` rules.
3. Push the cell pointer as hidden argument 0, then the visible arguments
   left to right, with the usual 16-byte padding and live-register saving
   (`callFnWith`'s sequence, `obo.obo:4861-5047`).
4. Load the cell pointer and emit `call qword ptr [cell]`; the cell's word 0
   is the code address. No fixups and no relocation follow: the call is
   absolute through the cell.
5. Clean up the arguments and stack; the result is `R`'s value in `rax` and
   is owned/string-temp exactly as a direct call's result
   (`obo.obo:5041-5046`).

A function value is not a function symbol: there is no `fnRec.fixups`, no
rel32, no name. The hidden body's own direct calls use the existing direct
call machinery.

Examples:

```obo
fn apply(x: int, f: fn(int): int): int { return f(x) }
fn twice(x: int): int { return x * 2 }

fn main() {
    let f = fn (x: int): int { return x + 1 }
    println(f(1))                         // 2
    println(apply(4, f))                  // 5
    println(apply(4, fn (x: int): int { return twice(x) }))
}
```

### 3.4 Effects and recursion

- **Every function value is pure.** After a lambda body is compiled, its
  hidden symbol is checked with the compiler's own predicate:
  `pureFn(hidden) && !hidden.writesHandles`, i.e. it does not free a global
  (`fnRec.freed` empty), is not `freesAll`, does not free through a
  handle, does not free below a mut-reference parameter (it has none), and
  is not a forward heading (`pureFn`, `obo.obo:3440-3446`; the interfaces
  design uses the same `handleSafe` shape). A body that fails is rejected:
  `a lambda must be pure: it may not free a global or write through a
  handle; call a pure function instead`. A body may allocate, read globals,
  write scalar globals, append to a global or owned array, call pure
  functions/methods, call another function value (which is also pure), and
  take/free owned parameters; none of those makes it impure by this
  predicate.
- **An indirect call is a pure call.** Its synthetic callee is
  `addr = 0, freesAll = false, writesHandles = false, pfree = 0, freed = []`,
  so `pureFn` is true and `afterCall` (`obo.obo:3540-3572`) does nothing: the
  caller's references, handles and globals stay valid, `movesFn` is false
  and `userCalls` is not incremented. The call may still consume owned
  arguments (the caller's `consume` runs first, marks them moved and
  invalidates references into them), and its result is owned/string-temp as
  a direct call's. This purity is what allows a comparator helper to pass
  `&items[i]` deep references to the callback.
- **Purity is intrinsic, not a type annotation.** Every function value is a
  lambda, every lambda is checked, and no named function or method is a
  value, so the static type `fn(...)` needs no purity bit; a function-typed
  parameter is guaranteed to receive a pure value. The `.sym` carries no
  purity marker. The cost is that a lambda cannot call an impure named
  function, a forward-headed function (its summary is not final), or write
  through a handle; impure callbacks are a later effect-type extension.
- A lambda has no name, so it cannot refer to itself. A lambda body may call
  the enclosing function or method only if its body is **not** currently
  being compiled; a call to an active enclosing function/method is rejected
  (`cannot call '<f>' from a lambda while it is being compiled: its effects
  are not known yet`) because its summary is incomplete. A call to an
  already-defined function/method is fine if it is pure (otherwise the
  lambda fails the purity check), even if it calls the function value at run
  time (mutual recursion through a value is allowed and pure). A call to a
  forward-headed function/method is allowed in the body, but makes the
  lambda impure and is therefore rejected by the purity check; define the
  callee before the lambda or use a named function for the outer logic. The
  active-call check applies to method calls too: a lambda inside a method
  body calling that same method (on any receiver) is rejected; an ordinary
  self-recursive call from the method's own body is not affected.
- A non-void lambda must return on every path; `return`, `defer`, `try`,
  loops and `match` are the ordinary statement forms, compiled with the
  hidden function as `curFn`.

---

## 4. Semantics: Stage B (capturing closures)

### 4.1 The scope of Stage B

A capturing lambda literal may appear **only as a direct call argument**. It
is a value for the duration of that call; the callee may keep it in a local
or parameter, copy it, pass it onward within the call chain, call it, or
store it in a `defer`; it cannot store it beyond the call or return it. The
creator cannot bind it to a local. This is the narrowest reading of
"second-class, non-stored values with borrow captures scoped to the call" and
it is what makes the environment pointer sound without a new lifetime
analysis: every frame on the call chain, including the creator's, is live
until the outermost call returns, and no function value can outlive its
creator's frame.

A non-capturing lambda remains a full second-class value (parameters, locals,
copies, arguments); only its creation is not restricted to an argument.

### 4.2 Capture modes

The capture list is explicit; the mode is determined by the captured local's
type, not by syntax:

| captured local's type | capture | body sees | call-site binding |
|---|---|---|---|
| record, closed type, array, optional, `Result` | read-only borrow of the whole local (store the object/optional/result word) | a read-only reference `&T` (internal), not ownable | the local itself (whole object); a reference's binding if the captured symbol is itself a reference |
| `&T` reference local/parameter | read-only copy of the reference pointer | a read-only reference `&T` | the reference's binding (roots, deeps, paths) |
| `int`, `byte`, `bool` | value snapshot | a `let` local of that type | none |
| `^T` handle | value copy of the token | a `let` handle local; reading and scalar writes through it are pure, a write that frees an owned field through it makes the body impure and is rejected | the handle's origin owner if it is a tracked `let` handle; otherwise none |
| function type | value copy of the cell pointer | a `let` function-value local | none (the target's captures are guaranteed by the call chain) |
| `string` | rejected in v1 | — | — |
| interface type | rejected in v1 | — | — |

- A read-only owned capture stores the object pointer (for an optional, the
  optional word; for a `Result`, the cell pointer). The hidden function
  loads it into a hidden reference-like local at entry; field access,
  indexing, narrowing and non-consuming `match` work through the existing
  reference machinery. The hidden binding is read-only: writes, moves,
  `drop`, `take`, `replace`, `append` and `mut` method calls are refused by
  the existing messages (`cannot change through '<x>': it is a read-only
  reference`, `cannot move out of a reference: a reference only borrows`,
  etc.).
- A scalar snapshot is a `let` local: reads are allowed, writes are refused
  with `cannot change captured '<x>': captures are read-only; pass a mutable
  parameter instead` (the compiler records capture symbols so the assignment
  path can distinguish a capture from an ordinary immutable local). It is a
  snapshot at creation, not
  a live view.
- A handle capture is a `let` handle local: reading through it is allowed
  (the runtime token checks liveness), re-pointing it is refused, and a
  write that frees an owned field through it makes the body impure and is
  rejected by the purity check; a scalar write through the handle is
  allowed and pure. To leave a result from a callback in v1, append to a
  global array, write a scalar global or a scalar field through a captured
  handle, or return an owned value; a write that frees owned state through a
  captured handle needs the impure-callback extension.
- A function-value capture is a `let` function-value local; it can be
  called. Its own captures are guaranteed by the same call chain.
- Strings are rejected (`cannot capture '<s>': strings are owned; pass it as
  a parameter`) because a copy would need a reference-count increment and a
  matching decrement at the end of the call; owned captures are deferred
  (section 4.7). Interface values are rejected
  (`cannot capture interface value '<x>'; pass it as a parameter`) because
  interfaces are in flight and their cell/token representation should settle
  first.

### 4.3 The environment cell

A capturing lambda's cell is a frame block of `1 + ncap` words:

```text
cell: [ code ][ cap0 ][ cap1 ] ... [ cap(ncap-1) ]
        word 0    word 1 ...
```

- The cell is allocated once per execution of the literal in the creating
  function's frame (`frame += 8 * (1 + ncap)`); its address is
  `rbp - frame`. Word 0 stores `CODE_BASE + entry` (the absolute virtual
  address of the hidden body; `pc()` returns a code offset, `obo.obo:589-593`,
  and descriptors already store absolute rodata addresses, `obo.obo:2059-2070`).
- Each capture word is the pointer/token/snapshot described in 4.2, loaded
  from the outer local at the literal site. The cell is immutable after
  creation; the callee never writes it (the hidden environment parameter is
  read-only).
- The hidden body's prologue loads every capture from `env + 8 + 8*i` into
  its hidden local (a reference-like local for owned/reference captures; a
  normal `let` local for scalars, handles and function values). The capture
  list and layout are known before the body is emitted, so the loads are at
  the top and valid on every path; no lazy load or discovery pass is needed.
- A non-capturing lambda's read-only cell is just `[code]`; it is also passed
  as the environment and the hidden body simply ignores it. The call path is
  identical for both kinds.
- **One hidden environment parameter, not one hidden parameter per capture.**
  The call site, and any callee that receives the value, does not know the
  capture list or layout; per-capture parameters would leak captures into the
  function type or require a variable-length signature. The env pointer is a
  fixed one-word ABI; only the hidden body knows the layout. This is also why
  the function type can stay a small structural type.

### 4.4 Capture lifetime and the soundness argument

- The cell lives in the creating function's frame. A capturing literal is
  only a direct call argument, so the cell is used only while the creating
  function is suspended inside that call (the callee may recurse, defer and
  call the value, but it always returns before the creator resumes). The
  callee cannot store the value beyond its own activation: function values
  cannot be returned, stored in fields/arrays/globals/`Result`, or passed to
  a function that could return them. Therefore the cell address and every
  capture word remain valid for every call of the value.
- Captures of owned objects are read-only, so a callback cannot free or move
  the object its creator owns. The creator's ownership invariants (unique
  owner, free at scope exit/assignment) are unchanged. A callback can read
  fields, index arrays, narrow optionals and match `Result`s; it cannot
  assign over an owned field, truncate an array, `take`, `replace`, `drop` or
  move the captured object. A scalar snapshot is independent. A handle
  capture may be read and scalar-written through; a write that frees an owned
  field through the handle sets `writesHandles` and fails the lambda's purity
  check. A callback that needs to leave a result behind can append to
  a global array or write a scalar global (both pure by the compiler's
  predicate) or return an owned value.
- **No hidden frees.** The environment owns nothing: no capture is owned, no
  destructor is emitted, and the frame block is plain words. There is no
  free to attribute to the wrong owner and no double free. The hidden body's
  purity check also guarantees it does not free a global or write through a
  handle. This is why owned captures (records/arrays/strings) are deferred:
  they would need a destructor run exactly once at the end of the call,
  including on `try` error paths, and a cleanup convention in the call
  sequence (section 4.7).
- **The creator's call-site checks cover the call.** A capturing closure
  argument's capture bindings are held across the later arguments and enter
  the mut-alias/deep/handle/global checks (section 5.4). A pure callee (the
  normal callback helper) cannot free a captured object through its own
  arguments: its function value parameters are read-only, and any mut
  reference parameters it has carry `pfree` 0 (it writes scalars/appends but
  frees nothing below them); it does not write through handles or free
  globals. A later argument that moves/frees a capture is refused at the
  call. An impure callee (one that frees a global for other reasons) is
  treated exactly as today: a deep/handle/global-rooted capture is refused
  like a reference argument. The callee cannot acquire a new reference to
  the capture target except through the arguments at entry, which were
  vetted. It can pass the closure onward, but the same argument checks run
  at each call; the creator's frame stays live throughout.
- **The callee's call is pure.** Calling a function value does not make the
  callee `freesAll`/`writesHandles`; a helper that only calls a callback and
  writes scalars/appends can itself be pure and accept deep element
  references. This is the comparator contract (section 10.5). Impure
  callbacks (a body that frees a global or frees through a handle) would force the
  conservative behaviour; they are out in v1 (section 14).
- **Residual limits:** a handle capture whose target is freed behind the
  compiler's tracking after capture is caught by the runtime token (exit 7),
  not by a compile error; a stale field handle made by the creator before the
  call is the same existing handle residual. A capture of a `var` scalar is a
  snapshot; later outer changes are not seen. A capturing closure passed to a
  pure function that never calls it is inert; the function may call it and
  stay pure. These are recorded, not hidden.

### 4.5 Escapes and storage (rejections)

| attempted use | result |
|---|---|
| capturing literal in a `let`/`var` initializer or assignment RHS | `a capturing lambda may only be passed directly as a call argument` |
| capturing literal in a return value | the return type is not a function type (it cannot be); plus the call-argument rule |
| capturing literal in a record field/array/global/`Result`/optional | the type is rejected at formation (section 2.2) |
| capturing literal in a `while` condition | `a lambda literal cannot appear in a while condition; bind it to a local before the loop` |
| capturing literal in a `defer` body | `a lambda literal cannot appear in a defer body; bind it to a local before the defer` |
| capturing literal at top level/global/constant initializer | `a lambda can only be created inside a function` |
| capturing a string/interface | section 4.2 messages |
| capturing a moved/freed local | the existing moved/freed message at the capture name |
| writing/moving/freeing a read-only capture inside the body | the existing read-only-reference/move messages |
| an impure lambda body (frees a global, frees owned state through a handle, calls an impure or forward-headed function) | `a lambda must be pure: it may not free a global or free owned state through a handle; call a pure function instead` |
| calling the active enclosing function/method | `cannot call '<f>' from a lambda while it is being compiled: its effects are not known yet` |
| a function value in a field/array/global/constant/return/`Result`/optional/handle/reference | the type-formation messages (section 9) |

### 4.6 Captures, methods, `try`/`Result`, `defer`, loops

- **Methods.** A method body is a function; a lambda inside it may capture
  the receiver or a local by name, read-only. A `&` or `mut &` receiver is
  captured as a read-only reference; an owned receiver is a read-only borrow
  of the whole object. A capture of the receiver is allowed while the
  method's call is live; the capture binding is the receiver parameter's
  binding, so the call-site checks apply across a method call. A callback
  can call read-only methods on a captured object; a `mut` method is refused
  because the capture is read-only. A method value is out; a lambda that
  captures the receiver and calls a read-only method is the v1 stand-in for
  a bound method value.
- **`try`/`Result`.** A lambda may return `Result[T]`; `try` inside its body
  works against the hidden function's return type. An indirect call returning
  `Result[T]` can be the operand of `try` at its call site. A captured
  `Result` is read-only; `match` on it is a non-consuming match, and `try`
  on the capture is refused because it would consume a reference. A callback
  returning an owned `Result` is a normal indirect-call result; the caller
  owns it.
- **`defer`.** A lambda body may contain `defer`; the hidden function runs
  its defers at its return. A call through a function-typed local/parameter
  may be the statement of a defer; the value is alive (a non-capturing local
  is read-only data, a capturing parameter is alive for the callee's
  activation and the creator is suspended). A lambda literal may not be
  created in a defer body (section 4.5).
- **Loops.** A capturing literal may be created inside a loop body and
  passed to a call in that iteration; each iteration re-initialises the same
  frame cell at the literal site, and the call completes before the next
  iteration. A non-capturing literal is static data; loops are free. A
  function-typed local is not owned, so loop move/state checks do not track
  it; it can be reassigned freely. A capturing literal cannot be assigned to
  a loop-carried local.
- **Recursion.** A lambda cannot name itself. A lambda body may call an
  already-defined pure function that calls the function value (mutual
  recursion through a value); the direct call's summary is final and the
  call through the value is pure. A call to an active enclosing function is
  rejected (section 3.4).

### 4.7 Why owned captures are deferred

An owned capture (move a record/array/string into the environment) would
require the environment to own a value and run its destructor exactly once
at the end of the call. A capturing literal is an argument expression; if
the callee returns via a `try` error path, an ordinary return, or a defer, the
creator's call still returns and could run the cleanup, but the cleanup must
also run if the callee exits (`exit`), and the argument-call sequence has no
cleanup slot. A plausible design is a call-scoped owned environment,
destroyed immediately after the call returns, with the same order as an owned
argument (strings additionally need a reference-count increment/decrement),
but it needs its own review: it is a different ownership story and it
interacts with `try`/`defer` and the leak checker. The roadmap says owned captures only if a sound design
emerges; the recommendation is to defer them from Stage B and keep the first
cut free of destructors. `mut` captures are deferred for a related reason:
mutating a captured owned value can free below it, a hidden free the caller's
ownership state cannot attribute when the callee invokes the callback. A
read-only owned/reference capture, a scalar snapshot and a read-only handle
capture are the sound subset.

A later extension can add `fn [mut x]` for a mutable borrow whose hidden
binding is writable, with the same argument binding and the existing
`pfree`/`pf` events at the call site; owned captures would need the
call-scoped destructor above.

---

## 5. Compiler architecture

### 5.1 Data model

No new field is added to `Type`, `Sym`, `FnRec`, `Param` or `Item` unless
measurement forces it. The existing records are enough:

```obo
// Type (obo.obo:882-895): kind "fn", base = return type, fields = parameters.
// fields[i].name is "" for an owned parameter and "&" for a read-only one;
// fields[i].typ is the parameter type; fields[i].offset is i.
fn newFnType(ret: ^Type): ^Type           // newType("fn", "", ret)
fn fnAddParam(t: ^Type, p: ^Type, mode: int)
fn fnArity(t: ^Type): int { return len(t.fields) }
fn fnParam(t: ^Type, i: int): ^Type { return t.fields[i].typ }
fn fnMode(t: ^Type, i: int): int { if t.fields[i].name == "&" { return 1 } return 0 }
fn fnRet(t: ^Type): ^Type { return baseOf(t) }
```

- `kind = "fn"`, `name = ""`, `owner = ""`, `uid = 0`, `parent = nil`,
  `variants` empty, `desc = 0`, `incomplete = false`. It is not registered in
  `typeRegistry`; an alias symbol (existing `typeDecl` alias branch,
  `obo.obo:4129-4139`) maps the alias name to the object.
- `isOwned(fn)` is false (`obo.obo:2578-2586`); `isRef(fn)` is false
  (`obo.obo:951-954`); `nonNull(fn)` is true (`obo.obo:956-958`, so an
  uninitialized `var f: fn(...)` is the existing "needs an initial value"
  error); `same` and `assignable` gain the structural branch;
  `tname`/`symTypeText`/`appTypeExpr` gain the expression branch.
- `same(a,b)` for functions: same return (`same(a.base,b.base)`), same arity,
  and for every parameter the same mode name and `same` type. There is no
  recursion through a function type (a function type cannot be forward-
  declared or contain itself), so the recursion terminates.
- `assignable(fn,fn)` is `same`; a byte/int or variant/closed rule never
  applies to functions. No variance: `fn(byte)` is not a `fn(int)`.
- A function type is not a named type; `reservedTypeName` and `nameType` are
  untouched. A type alias gives it a source name, but the writer emits the
  underlying type (`appTypeExpr`, `obo.obo:9350-9378`) and the reader
  reconstructs a structurally equal object.

The hidden lambda symbol is a normal `^Sym` with `kind = "lambda"`, a fresh
`FnRec` (`newFnRec`, `obo.obo:1122-1125`), `name` a diagnostic string
(`"a lambda"`), `owner = curModuleName`, `addr` the code entry, and no
registration in `curModule.globals`, `visibleGlobals`, `exports` or
`programGlobals`. It is not in `methodSyms` either. `callableName`
(`obo.obo:1172-1175`) returns `"a lambda"` for `kind == "lambda"` so body
diagnostics read `a lambda must return a value on every path`,
`a lambda that returns a value needs a return type` (a void lambda with a
`return value`), and `try needs a function that returns a Result; a lambda
returns ...`. The symbol's `FnRec.params` are `[env] + visible parameters`;
its `FnRec.pmodes` are `[1] + visible modes`; its summary is local and never
written to a `.sym`.

`releaseTables` (`obo.obo:10860-10879`) needs no new registry: hidden
symbols live in `symArena` like any function symbol. Nothing refers to them
after the island's code and cell are final, so the implementation may truncate
`symArena`/`fnArena` to the island's marks at that point (the recommended
cleanup) or leave them to the enclosing function's `fnBody` truncation; no
global registry is added either way.

### 5.2 Parser and checker

**Lambda literal.** `factor` (`obo.obo:6014-6093`) gains `if k == T_FN {
return lambdaExpr() }`. `lambdaExpr`:

1. `next()` after `fn`; if `sym == T_LBRACK`, parse a non-empty capture list
   of identifiers, resolving each with the outer-local rules (the capture
   list resolution below); expect `]`, then `(`.
2. Parse the parameter list with the same parameter machinery as `fnDecl`
   (`obo.obo:8485-8511`), rejecting `mut`; parse the optional return type
   with `parseType`; reject a function return type.
3. Build the lambda's function type from the parameter types/modes and
   return; it is the value's type. There is no inference from a destination.
4. Check context: `curFn != nil`; not in a while condition; not in a defer
   body; a capture list requires the call-argument context and rejects a
   non-call context; a capture name must not duplicate a parameter.
5. Create the hidden symbol/`FnRec`; emit the code island (5.3); **check the
   hidden function's purity** (`pureFn(hidden) && !hidden.writesHandles` and
   `hidden.fnRec.pfree == 0`) and reject an impure body with the section 9.2
   message; create the cell (5.4); return the item (`makeConst` for a
   non-capturing read-only cell, a `reg`/frame-address item for a capturing
   cell). If the lambda has captures, record the capture binding and the
   item's function-value metadata.

**Function type.** `parseType` (`obo.obo:3973-4014`) gains a `T_FN` branch at
the top: consume `fn`; reject an immediate `[` (`a function type cannot have
a capture list`); expect `(`; parse parameter types with an optional leading
`&` (reject `&` on a non-owned type with the existing borrow message); expect
`)`; parse an optional `: Ret`; reject a function return type; construct the
`fn` type. `parseRefType` (`obo.obo:3671-3682`) needs no change: `&fn(...)`
fails its `isOwned` check with `only records, closed types and arrays can be
borrowed, not fn(...)`; the same message is used inside a function type
parameter.

**Function-type restrictions** are enforced where types are formed, in source
and in the `.sym` reader:
- `parseType`/`symParseType`: reject `?fn`, `[]fn`, `Result[fn]`, and a
  function return type in the fn branch; `^fn` is rejected by the handle
  rule (`obo.obo:3985-3990`); `&fn` by `parseRefType`.
- `fieldList` (`obo.obo:4016-4047`) and `symFieldList` (`obo.obo:9898-9916`):
  reject a field whose type is `fn` with the function-specific message.
- `varDecl` (`obo.obo:6553-6677`) and `symGlobalDecl`
  (`obo.obo:10274-10302`): reject a global of function type; a global
  lambda initializer is rejected earlier by `curFn == nil`.
- `fnDecl` (`obo.obo:8512-8516`) and `symFnDecl` (`obo.obo:10384-10388`):
  reject a function/method return type of `fn`.
- `constDecl` (`obo.obo:8634-8646`): a lambda is not a compile-time constant;
  the existing message fires.
- `ok` (`obo.obo:5100-5117`): reject a function payload
  (`Result cannot hold a function value`).
- `relation` (`obo.obo:6385-6480`): add a `fn` check before the
  reference/primitive comparison. A fn/fn comparison is
  `function values cannot be compared`; a fn/nil comparison is
  `a function value can never be nil` (check the nil side first). Without
  this, two function values would compare their cell pointers as integers.
  `checkAssign` must not add its usual nil hint (`declare it as ?fn(...)`)
  for a function destination, because `?fn` is not a type; the nil assignment
  reports `a function value can never be nil`.
- `builtin` print (`obo.obo:5137-5163`), `handle` (`5203-5225`), `take`
  (`5331`), `replace` (`5226`), `drop` (`5289`), `alive` (`5308`) keep their
  existing type errors; the function type's `tname` makes them precise
  (`cannot print fn(int): int`, `handles name records and closed types, not
  fn(...)`, etc.).
- Interface member parsing (after interfaces merges) rejects a function type
  in a parameter or return (`an interface method cannot use a function value;
  function types are not supported in interfaces`). There is no interface
  function type in v1.
- `closureCheck` (`obo.obo:8652-8681`) gains a `fn` branch: check every
  parameter type and the return type, so a `pub` function or alias that
  mentions a private type inside a callback signature is refused with the
  existing `mark '<T>' pub too` remedy. `checkExports` (`obo.obo:8683-8706`)
  then covers `pub fn`/method/alias signatures automatically.

**Indirect call.** `designatorT` (`obo.obo:5706-5890`) after `nameItem` and
before the not-a-function check:

```obo
var isFnCall = false
if sym == T_LPAREN && x.typ.kind == "fn" { isFnCall = true }
if sym == T_LPAREN && !isFnCall && (s.kind == "var" || s.kind == "let" || s.kind == "const") {
    error("'" + qname + "' is not a function")
}
...
// the postfix loop does not run when the current token is T_LPAREN
// the existing tail runs on the callee item, then:
if isFnCall { return callValue(x, ...) }
```

The existing message is byte-identical for non-function values. The callee
item's read/use marks are the ordinary local read; the tail is not applied to
the call result, which ends the designator. `callValue` (new) reuses the
argument machinery of `callFnWith` or a factored common loop; it pushes the
hidden environment first, then the visible arguments, and emits
`call qword ptr [cell]` (5.4).

A function value followed by `.` in `designatorT`'s postfix loop must get a
specific error before the record/field logic: `a function value has no fields
or methods; call it with '(' after its name`. Without it, the generic
`<t> has no fields`/`<t> has no methods` messages are true but less useful;
`f[i]` keeps the generic indexing error. A following `.`/`[` on an indirect
call result is a syntax error at the enclosing construct (the call ends the
designator).

**Capture list resolution (Stage B).** At the literal site the outer
function's `blocks` (`obo.obo:1458`) are still open. `lambdaExpr` searches
them innermost-first for each capture name; it does not search
`curModule.globals` for the capture itself (a global is used directly). For a
found symbol:
- call the normal `nameItem` path for the moved/freed/handle-use checks, or
  an equivalent check that preserves those diagnostics; for a handle, also
  call `deadHandleCheck` so a tracked `let` handle already known dead is
  refused at the capture name (an untracked handle is the runtime residual);
- classify it by type (4.2) and reject strings/interfaces;
- create a capture record `LambdaCap { outer: ^Sym; name: string; kind: int;
  typ: ^Type; bind: ?^RefBind }` in declaration order;
- for a handle with a tracked origin, note the origin owner for the
  call-site binding.
An unlisted enclosing local used in the body is diagnosed by the body
lookup: the lambda body is compiled with a fresh block stack and a saved
`outerBlocks` handle; if a name misses the lambda's own blocks but is found
in `outerBlocks`, report `'<x>' is a local of the enclosing function; add it
to the lambda's capture list: fn [<x>](...)`. The pre-created capture symbols
are declared in the lambda's root block, so a listed capture is found
normally and shadows an outer local/global.

**Context flags.** `lambdaExpr` checks a small set of compiler globals:
- `curFn != nil` (a lambda needs a function frame; rejects globals/consts);
- `inWhileCond` (new, set around `whileStat`'s condition `expression()`,
  `obo.obo:7237-7252`) rejects any lambda literal there;
- `inDefer` (existing, `obo.obo:1515`) rejects any lambda literal in a defer
  body;
- `lambdaArgOK > 0` (new counter, incremented around the `nestedExpr()` of a
  call argument in `callFnWith` and in `callValue`) is required for a capture
  list. A non-capturing literal needs no flag.
The flag is a counter so nested calls restore correctly. The type system
prevents a capturing literal from being stored in a field/array/global or
returned, but the flag rejects the explicit capture-list form outside a call
argument with the specific message.

**Function-value metadata.** A capturing literal's capture binding must reach
the call-argument loop. The design uses one compiler global `lastFnBind:
?^RefBind`, set by `lambdaExpr` to the union binding (nil for a non-capturing
literal), cleared at the start of `nestedExpr`/`tailExpr`, and read
immediately after the argument's `nestedExpr()` in `callFnWith`/`callValue`.
The consumer only uses it when the parsed argument's type is `fn`; returns of
function values are prohibited, so a nested call's result can never be a
function value that would inherit a stale bind. The invariant and the
parenthesized/nested cases are tested. If a counterexample appears, the
fallback is a `fnBind` field on `Item` (measure the peak; do not raise the
budget without the user).

### 5.3 Compiling the hidden body (single-pass code island)

`lambdaExpr` compiles the body at its source position. The sequence:

1. Record the code position `over = jmp()` (an unconditional jump over the
   island); `entry = pc()`.
2. Save the outer context into a `LamCtx` record (5.3.1); replace the
   function-level arrays with fresh ones; set `curFn` to the hidden symbol,
   `free`/`homeBits`/`homeCap` to a fresh function with `homes = false`,
   `frame = 0`, `depth = 0`, `blocks` fresh, `ownedLocals` fresh,
   `narrowStack`/`loops` fresh, defer/origin/guard/pending arrays fresh,
   `trySite = tryNest = 0`, `condDepth = 0`, `pinned = nil`,
   `inDefer = false`, `noreturn = false`, and the peephole state invalid.
3. Emit the hidden prologue (`push rbp; mov rbp,rsp; sub rsp, 0` with a
   frame-size patch, as `fnBody`, `obo.obo:8334-8342`); open the root block;
   declare the environment parameter at the ABI offset `16 + 8 * nvis`
   (parameter 0 of `1 + nvis` parameters), then the visible parameters at
   their offsets (`fnBody`'s arithmetic, `obo.obo:8349-8370`); declare the
   capture symbols and emit their loads from `env + 8 + 8*i`; open the body
   block.
4. Parse `{`, `statSeq()`, `}`; the ordinary statements. A non-void
   fall-through is `a lambda must return a value on every path`; a void
   `return v` is `a lambda that returns a value needs a return type`
   (`returnStat` gains a `kind == "lambda"` check for this message).
5. Close the blocks, emit the epilogue if the body did not terminate
   (`obo.obo:8385`), patch the hidden frame size, and call `dropFrame` if the
   hidden frame is zero (`obo.obo:7985-7998`). The final `f.addr` is the
   entry stored in the cell.
6. Restore the outer context (including the outer `curFn`, so the hidden
   symbol is no longer "being compiled"); check `pureFn(hidden) &&
   !hidden.writesHandles` and `hidden.fnRec.pfree == 0`; `fixHere1(over)`;
   emit the cell (5.4) and continue the outer expression.

The island is ordinary code in the same chunk stream. The `jmp` over it
means the outer path never falls into it; the hidden `ret` never falls into
the outer path. The outer `dropFrame` at the end of the outer function only
moves the outer entry; the island is not contiguous with it. `farCalls` is
saved/truncated around the island (the hidden body's direct calls are
relative and already fixed; they do not need the outer `dropFrame`'s repair),
so a call inside the island to a forward-headed function has its fixup on the
callee, not on the island, and the existing `fnDecl` retry cleanup
(`obo.obo:8596-8610`) still sees it when the outer function is retried.

**Why not call `fnBody` directly.** `fnBody` is written for a top-level
function: it resets and truncates `farCalls`, opens/closes blocks, truncates
`bindArena`/`argTemps`/`symArena` to the function's marks, sets `curFn = nil`
and returns a "retry" flag (`obo.obo:8322-8399`). The island needs the same
prologue/parameter/statement/epilogue code but must not release the outer
function's state, must not reset the outer `curFn`, and must keep the outer
`blocks`/`ownedLocals`/`origins`/defers intact. The implementation should
factor the common body skeleton (`prologue`, parameter setup, `statSeq`,
close/epilogue/frame patch) into helpers shared with `fnBody`, or write a
`lambdaBody` that mirrors those ~80 lines with the cleanup replaced by the
context save/restore. Duplicating the whole `fnBody` is rejected: it would
drift.

**Register homes inside the island are off.** `homes = false` makes `pickHome`
return -1 (`obo.obo:3734-3736`) and `homeCap = 0` (`obo.obo:8330-8333`), so
there is no pre-scan and no nested `homeRetry`/`giveUpHomes` (`obo.obo:2149-
2155`). The hidden body's int/bool locals live in memory; captures are loaded
into memory; this is a small cost for a callback body and removes the hardest
piece of nested state (a retry inside a retry). The outer function's homes
are saved/restored and keep working; a captured outer local in a home
register is simply read (a copy), not spilled.

**Code size and dropFrame.** A hidden body with no locals gets its entry
moved by `dropFrame` before the cell is emitted, so the cell always stores
the final `CODE_BASE + f.addr`. The outer function's `dropFrame` is only for
the outer entry; the island's entry is not a call target of any rel32.

#### 5.3.1 The saved context

The outer context record (all arrays are saved as handles, not copied) must
cover at least:

- registers/homes: `homeBits`, `homeCap`, `free`, `homeRetry`;
- frame/code: `frame`, `depth`, `farCalls`, `fwdCalls`, `lifted`, `waitFn`,
  `waitAt`, `barrier`, peephole fields `cpAt`, `cpEnd`, `cpDst`, `cpSrc`,
  `foldAt`, `foldEnd`, `foldHome`, `foldT`, `foldForm`, `foldOpc`, `foldArg`;
- scopes: `blocks`, `fnBlock`, `ownedLocals`, `narrowStack`, `loops`,
  `depthJumps`, `originSyms`, `originDead`, `originLive`, `originPath`,
  `originNar`;
- defers: `deferH`, `deferAt`, `deferFrees`, `deferFreeEv`, `dfBlock`,
  `dfIndex`, `dfOwner`, `dfEv`, `deferUses`, `deferMark`, `inDefer`;
- loop/free logs: `freeLog`, `freeArena`, `arenaBase`, `recording`,
  `pendSym`, `pendDepth`, `frLine`, `frCol`, `pendWhat`, `pendOwn`,
  `pendNest`, `pendLine`, `pendCol`, `pendMsg`, `contSeq`, `brkSeq`,
  `orSeq`, `freeSeq`, `truncKeep`;
- guards/alive: `guardStart`, `guardSym`, `guardFollow`, `guardFree`,
  `guardNeg`, `guardOpen`, `guardIdx`, `guardMark`, `inAlive`;
- names/uses: `lastNamed`, `lastNamedPrev`, `lastDesigName`, `lastPlace`,
  `lastRaw`, `lastOrigin`, `lastIdent`, `lastViaHandle`, `lastFnBind`;
- function state: `curFn`, `selfCalls`, `userCalls`, `useSeq`, `trySite`,
  `tryNest`, `noreturn`, `condDepth`, `pinned`;
- arena marks: `bindArena`, `symArena`, `argTemps`, `argTempsUsed`,
  `farCalls`, `freeArena`, `fnArena` as lengths to truncate after the
  island.

The declarations of most of these are in `obo.obo:1448-1517`, `2573-2576`,
`3695-3701`, `4745-4746`, `4767-4768`, `1072-1087`, `1459-1500`. The list is
the compiler's own function-level state; an implementer should diff it
against `fnBody`/`fnDecl`'s save list (`obo.obo:8573-8628`) and `deferStat`'s
save list (`obo.obo:7838-7873`) rather than trusting a hand count. The
scanner position is **not** restored after the body: the body's tokens are
consumed and the outer expression continues after `}`. Only a function-level
retry rewinds the scanner.

**Peephole.** Do not restore a pending fold across the island. The outer code
before the literal may have a fold pending; the island's code is not on the
fall-through path, so `foldEnd`/`cpEnd` from before cannot be valid after the
island. Invalidate them (`cpEnd = -1; foldEnd = -1`) and continue. Restoring
`barrier` is unnecessary; `pc()` is called for the `jmp` and the fix.

**Retry.** If the outer function runs out of registers and `fnDecl` retries
(`obo.obo:8592-8629`), the island is truncated with the outer code and
recompiled by the second pass. The first pass may have added a read-only cell
to rodata; it is unreferenced and harmless (rodata is not truncated on a
retry today). Do not cache a lambda cell by source position across passes; a
fresh cell is simple and correct.

### 5.4 Code generation: cells, captures and indirect calls

**Non-capturing cell.** After the hidden body's final `f.addr`:
```obo
rodAlign()
rod64(CODE_BASE + f.addr)        // rod64 appends 8 bytes, obo.obo:2024-2034
```
The literal item is `makeConst(fnType, cellAddr)` (`makeConst`,
`obo.obo:1224-1228`); a `const` item of a non-owned type. Word 0 is the code
address. The cell is immutable, 8-byte aligned, in rodata.

**Capturing cell.** After the hidden body, in the outer function:
```obo
frame += 8 * (1 + ncap)
let base = -frame                      // rbp+base is word 0
store [rbp+base] = CODE_BASE + f.addr
for i in 0..ncap { store [rbp+base+8+8*i] = <capture i value> }
return a reg item holding rbp+base
```
The capture value is read from the outer symbol's `localItem`
(`obo.obo:3790-3801`) with `load` (a home register yields a copy; a normal
slot is loaded); no `nameItem` re-use and no double use marks. The reg item
is non-owned; `consume` returns early (`obo.obo:2959`), and the argument push
uses the cell address. A frame address is built with `movRR(r, RBP)` +
`aluRI(5, r, -base)` (or a new `lea` helper if the encoder test prefers; no
new instruction form is strictly needed).

**Hidden prologue capture loads.** For `i` in `0..ncap`:
```obo
rEnv = load [rbp + env.offset]        // the hidden parameter 0 slot
rVal = load [rEnv + 8 + 8*i]
store [rbp + capSym[i].offset] = rVal
```
The capture symbol is an `isRef`/`isRefLocal=false` local for an
owned/reference capture (slot holds the pointer, read-only bind), a normal
`let` local for a scalar/handle/function-value capture. The symbols are
declared in the hidden root block before the body; `nameItem`/field access
then use the ordinary paths.

**Indirect call.** `callValue(x, line, col)`:
1. Materialise the callee cell pointer and store it in a hidden frame temp
   (`newLocal("", tInt, "var")`), so no pool register has to survive the
   arguments. The pointer is either a constant (non-capturing) or a frame
   address read from a local.
2. `saved = usedRegs()` (minus the cell temp's register if any), `pushRegs`,
   `setFree(0)`; `pad = (depth + 8 * (nvis + 1)) % 16`; pad.
3. Push the environment: load the cell pointer from the temp and `pushR` it
   (or `pushI` if it is a constant that fits; `pushI`, `obo.obo:697`).
4. For each visible parameter `i` in `0..nvis`: parse and check the argument
   exactly as `callFnWith` (`obo.obo:4891-4954`): `checkAssign`, mode 0
   `consume`, mode 1 `borrowBind` (read-only; no writability check because
   there is no mode 2), `argOrderCheck`, `holdArg`, then push const/reg.
5. After the arguments, release the holds (`releaseArgs`), check the closing
   `)`, run the deep/handle/global/mut-alias loops on `argBinds` (including
   the capture pseudo-binding of a capturing closure argument), then
   `afterCall` of the pure synthetic callee (which does nothing for a pure
   call; section 8.4).
6. Load the cell pointer from the temp into a register; emit
   `opMem(0xFF, 2, mem(cellReg, -1, 0, 8), false, false)` (`call qword ptr
   [cell]`; `opMem`/`opReg`, `obo.obo:607-649`). The operand is 64-bit by
   default; no REX.W is needed. This is a new emitter form; add exact
   `--encode`/objdump cases (section 10.7).
7. Clean up `8 * (nvis + 1) + pad` bytes and `depth`; `restoreRegs`; the
   result handling is `callFnWith`'s (`f.ret == tVoid` → void; otherwise a
   reg of `f.ret`, `strTemp` for a string; `obo.obo:5041-5046`).

**Synthetic callee symbol.** `callValue` builds a small `^Sym` (kind
`"fncall"`, a fresh `FnRec` whose `params`/`pmodes` come from the function
type, `ret` from the type, `addr = 0`, `freesAll = false`,
`writesHandles = false`, `pfree = 0`, `freed` empty) so the existing
`afterCall`/`mutFrees`/`mightFree`/`argOrderCheck` machinery works unchanged.
The symbol is pure by construction; the lambda purity check (section 3.4) is
what makes that sound. It is never looked up or called directly. The display
name for messages is the callee local's name: `function value '<f>'`; the
argument labels are `argument <n> of function value '<f>'`.

**Pure call effects.** `afterCall` (`obo.obo:3540-3572`) with a pure synthetic
callee does nothing:
- `all` is false (`addr >= 0`, not `curFn`, `freesAll` false);
- no `freed` globals, so no global invalidation;
- `writesHandles` false, so no `invalidateDeep`/handle invalidation;
- `pureFn(f)` true, so no handle-rooted invalidation;
- `mutFrees` false for every parameter (no mode-2 parameters exist anyway);
- `movesFn` false, so `userCalls` is not incremented and
  target-located-before-a-call checks do not fire. (The call may still
  consume owned arguments: `consume` ran in the argument loop and marked the
  caller's local moved; that is the ordinary move bookkeeping, not a call
  effect.)
A pure call can pass deep references, a reference through a handle, or a
reference into a global: the deep/handle/global argument checks all test
`f.writesHandles`/`pureFn`/`mightFree`, which are false for a pure callee, so
they allow them. This is the comparator contract.

**Capture binding at the call.** For a function-typed argument with
`lastFnBind != nil`, `callFnWith`/`callValue` add the binding to `argBinds`
with mode 1 and `argIdx = i`, and `holdArg(bind, i)`. No `borrowBind` and no
push of a reference are performed; the binding is only for holds/checks. The
binding is a union of:
- an owned whole local: `singleBind(local, false, "", false, nil)` (read-only);
- a reference local/parameter: a copy of its `RefBind` with `writable =
  false` (roots/deeps/paths preserved);
- a tracked `let` handle's origin owner: `singleBind(owner, false, "",
  false, nil)`;
- scalars/handles without an origin/function values: no entry.
The union is built with `arenaBind` (`obo.obo:1089-1103`). The pseudo-entry is
the same shape as a reference argument, so the existing loops print
`argument <i> of <f> may point inside argument <j>, which is mut` and the
global/handle/deep messages; the design adds the capture wording in section 9
where the closure is the held argument. The important soundness rule is:
**a capture binding that is deep (`anyDeep`), handle-rooted (`handleSym`) or
global-rooted is refused when the callee is not pure** by the existing
checks. A callback helper is pure (the normal case) and allows them; an
impure callee that also takes the closure (e.g., one that frees a global for
its own reasons) refuses them exactly like a reference argument. The mut
argument overlap check is unchanged and conservative even for a pure callee.

**No hidden calls to the runtime.** A function value is called with the
compiler's own calling convention, not a runtime slot. The runtime is
unchanged; `RT_INIT`/`RT_FINISH`, the I/O/meta slots and the stack-alignment
check are untouched. An indirect call still pads the stack to 16 bytes and
saves the live pool registers, so a runtime call inside the hidden body
keeps the existing alignment contract.

### 5.5 Existing scans and interactions

| site | why | change |
|---|---|---|
| `whileStat` condition lift, `obo.obo:7222-7330` | the condition is copied to `lifted`, truncated (`7251-7252`) and re-emitted after the body (`7309-7310`); a lambda body emitted there would be duplicated/truncated and its cell would point into cut code | reject a lambda literal while `inWhileCond`; an indirect call through a function-typed local in the condition has no rel32 fixup, so it is safe to lift |
| `deferStat`, `obo.obo:7834-7907` | the defer body is compiled out of line with its own state swap; nesting a function compile inside it multiplies the state cases | reject a lambda literal while `inDefer`; calling a function value in a defer is fine |
| `fnDecl` retry, `obo.obo:8592-8629` | outer home retry truncates the code from `f.addr`, which includes the island; forward-headed fixups from the island are >= `f.addr` and the existing scans truncate them (`8596-8610`) | no new scan; the island is recompiled by the retry; homes are off inside so there is no nested retry |
| active-call rule | a call in a lambda body to a function/method whose body is still on the compile stack has an incomplete effect summary | maintain `fnStack: []^Sym`, pushed in `fnDecl` before `fnBody` (methods too) and around the island; `callFnWith`/`methodCall` reject a callee that is in the stack and is not `curFn`; the ordinary self-recursive call (`f == curFn`) keeps its existing handling |
| `dropFrame`, `obo.obo:7985-7998` | rewrites a zero-frame entry and repoints calls to the old entry | called for the island before its cell is emitted; the cell stores the final entry |
| `farCalls` | used by the outer `dropFrame` | save/truncate around the island; the hidden body's own calls are relative and already fixed |
| `methodSyms`/`methodCall` | methods are functions with a receiver | function types are allowed in method parameter lists; no method values; no change to the registry |
| `checkExports`/`closureCheck` | exported signatures must be closed over public types | recurse through function types |
| `relation` | `==` on two function values would compare pointers | reject comparison |
| `releaseTables` | arenas are cleared between compiles | no new registry; hidden symbols live in `symArena` and die with the enclosing function's truncation |

**Arena/state cleanup.** After the island is final and the cell emitted,
restore the fresh arrays and truncate `argTemps`/`farCalls` to their marks.
Do **not** truncate `bindArena` before the literal's capture binding is
built: that binding is a new `RefBind` in `bindArena` (it uses only outer
symbols and their pre-island binds) and must survive until the call argument
loop. The simplest safe rule is to leave the island's `bindArena` entries to
the enclosing function's `fnBody` truncation (`obo.obo:8395`); if memory
matters, `symArena`/`fnArena` may be truncated to the island's marks after
the purity check and before the capture binding is built (nothing else refers
to the hidden symbol; the hidden `FnRec`'s `freed` list holds persistent
global symbols and its `pfree` is 0, so no `pfFn` row points at it). If a
future change gives a hidden lambda a `pfree` bit, the `pf` rows must be
truncated with the symbol; the current restriction (owned/read-only
parameters, read-only captures) keeps that from happening.

### 5.6 No new tokens or keywords

The scanner (`obo.obo:224-467`), token constants (`36-103`), keyword table
(`145-158`), `--tokens` and `port_test.py` are untouched. `fn`, `[`, `]`,
`(`, `)`, `,`, `:`, `&` and identifiers are existing tokens; `mut` remains an
ordinary identifier word. The `.sym` grammar reuses `fn` in type expressions.
The port token tests are unchanged; new source files are tokenized the same
way by both compilers.

---

## 6. Symbol files and modules

### 6.1 Representation

A function type is written source-like in every type expression:

```text
fn(int): int
fn(&Item, &Item): int
fn(): bool
fn(fn(int): int): int
```

- Owned parameters have no marker; read-only parameters have `&`. There is no
  `mut` marker and no capture list (a capture list is local source syntax and
  never appears in a `.sym`). A `void` return is omitted; a return type is
  written after `:`.
- A function type has no shell and no `uid` line. A type alias to it is a
  normal alias:

```text
type Cmp = fn(&Item, &Item): int
```

  The writer emits the underlying function type, never an alias name, in
  function/method parameter and return positions; a `pub type Cmp` line is
  emitted in the alias group when the alias itself is exported.
- Function types may appear only where section 2.2 allows: exported
  function/method parameter types and exported alias right-hand sides. A
  return type, global, constant, record field, array/optional/handle/Result
  component, or interface member in a `.sym` is rejected by the reader with a
  specific detail (section 9.4).
- No lambda body, code address, cell word, environment layout, capture list,
  capture offset, hidden symbol or hidden parameter is ever written. A
  function value that crosses a module boundary is a runtime cell pointer in
  a single image; the `.sym` only says the parameter's type. A function type
  carries no purity marker: every function value is pure by the language rule
  (section 3.4), and a `.sym` cannot create a value, only a type.
- A method with a function-typed parameter is written like any method:
  `fn (p0: &Bag) apply(p1: fn(&Item, &Item): int): int`. The receiver is
  still p0; the `.sym` summary's `pfree`/`pf` indices include p1, but a
  function-typed parameter is mode 0 and never a target of a free below it.
- Interface declarations and impls do not contain function types in v1; a
  malformed `.sym` that does is rejected.

Canonical group order is unchanged (shells, definitions, aliases, constants,
globals, methods, impls, functions). A function type inside a signature is
part of that signature's line; a function-type alias is in the alias group.

### 6.2 Writer

- `appTypeExpr` (`obo.obo:9350-9378`) gains the `fn` branch before the
  name/owner logic: `fn(`, each parameter with `&` for mode 1 then
  `appTypeExpr` of its type, `)`, and `: ` + return if not void. It is used
  by `appParams` (`9477-9487`), `appFn` (`9519-9532`), `appMethod`
  (`9535-9553`), `appTypeAlias` (`9436-9442`) and `appGlobal` (`9467-9473`);
  a function type in a global/field position is never constructed, so the
  writer never emits one there.
- `appFn`/`appMethod` summaries are unchanged; the parameter count includes
  function-typed parameters, whose mode is 0. `pfree` bit indices and `pf`
  rows are unchanged (a function-typed parameter can have a bit in a
  malformed live summary only if a bug set it; the writer emits what the
  live `FnRec` says).
- `symBody` (`obo.obo:9582-9653`) and `symText` (`9656-9686`) need no group
  change; the interface hash covers the new type expressions automatically.
  A lambda body change is source-only, so it changes `source` and not
  `interface`.
- Version constants (`obo.obo:109-111`): Stage A `OBO_VERSION "0.8"`,
  `SYM_FORMAT 4`, `SYM_ABI 1`; Stage B `OBO_VERSION "0.9"`, `SYM_FORMAT 4`,
  `SYM_ABI 1`. `--version` prints `obo 0.8 sym-format 4 abi 1` (Stage A) /
  `obo 0.9 sym-format 4 abi 1` (Stage B).
- Why format 4: the `.sym` grammar gains `fn(...)` type expressions, so a
  format-3 reader must reject a format-4 file and vice versa. Why ABI 1:
  every existing layout, descriptor, calling convention, global address and
  summary meaning is unchanged; a function value is one ordinary argument
  word; the hidden env parameter is internal to lambda code and never
  appears in a `.sym`. The cell layout is new but is gated by the exact
  format/compiler match, as the interfaces design gates its cell/vtable
  layout. If review treats the function-value calling convention as an ABI
  change, the fallback is `SYM_ABI 2` with the same fixture migration plus
  the ABI fixture.

### 6.3 Reader

- `symParseType` (`obo.obo:9847-9895`) gains a `T_FN` branch mirroring
  `parseType`: `fn`, reject `[`, `(`, parameters with an optional `&`
  (reject `&` on a non-owned type, including `&fn(...)`), `)`, optional
  `: Ret`, reject a function return, construct the structural type.
- `symFieldList` (`9898-9916`) rejects a function field with the specific
  detail; `symGlobalDecl` (`10274-10302`) rejects a function global;
  `symConstDecl` (`10211-10273`) never sees a function constant (its type
  check is primitive); `symFnDecl` (`10304-10599`) rejects a function return
  and parses function-typed parameters as mode 0. A method receiver of
  function type is rejected by the existing
  `the receiver must be a record or closed type defined in this module,
  not fn(...)`.
- `symTypeDecl` (`10040-10209`): the alias path accepts a function type and
  registers the alias; a forward shell followed by a function alias is
  rejected (`a function type cannot be forward-declared: '<T>' is already
  declared as a forward type`); a duplicate alias is the existing error.
- `symTypeText` (`9783-9790`) gains the `fn` branch for diagnostics.
- Mode 1 (`--check`) creates the function type object; mode 0 compares the
  live and parsed types with the structural `same`. The reader does not
  create code, cells or hidden symbols; `--check` discards the checked
  module's code, including any lambda body it compiles, and never executes.
- `--reemit-sym` (`obo.obo:10845-10858`) reads mode 1 and re-emits through
  the writer; function types and function-type aliases round-trip
  byte-identically in canonical form.
- The reader's own type/field/global parsers are separate from the source
  parsers; the design adds the restrictions to both, not one.

### 6.4 Type identity and cross-module values

- A function type is structural: the live object in the defining module and
  the reconstructed object in `--check` compare with `same`, not by pointer.
  Two modules' structurally equal function types are compatible, and an
  alias is transparent. This is unlike records/closed types, which are
  nominal by canonical `^Type`.
- A module exports a function type only through a signature or an exported
  alias. The writer emits the underlying `fn(...)`, so an importer does not
  need the dependency's alias to name the parameter type; it can also import
  the alias (`lib::Cmp`) because it is exported. A transitive module that
  obtains a function from A can pass it to L's function if the structural
  types match, without importing L, because the signature carries the type.
- A function value created in module B and passed to module A's exported
  function is a cell pointer: for a non-capturing lambda, a read-only cell in
  B's rodata; for a capturing lambda, a frame cell in B's caller. A's code
  loads and calls it; A's compiler never needs B's source or a `.sym`
  address. The one image contains all code and rodata. `--check` checks the
  call and discards the code; a source-less dependency with a function-typed
  parameter is accepted, and the checked module's lambda is compiled/checked
  but not run.
- A dependency cannot export a function value (no globals/returns), and a
  lambda body is never interface data. A private lambda body change changes
  only the source hash; a function-typed exported signature change changes
  the interface hash and every dependent's `depiface`.
- `--deps` does not parse types and is unchanged.

### 6.5 Versioning and fixtures

- Stage A targets `0.8`/format `4`/ABI `1`; Stage B targets
  `0.9`/format `4`/ABI `1`. If the interfaces merge lands with different
  numbers, shift: Stage A = interfaces version + 1 and format + 1; Stage B =
  Stage A version + 1, format unchanged. The interfaces design currently
  targets `0.7`/format `3`.
- **Fixture migration.** At this baseline there are 85 committed `*.sym`
  files under `obo/tests/modules/sym/*/symdir` plus 3 golden
  `obo/tests/modules/golden/expected/*.sym` = 88 files. The header
  distribution is 87 `obosym 2` and one `obosym 3` (the `format` fixture);
  87 `compiler "0.6"` and one `"9.9"` (the `compiler` fixture); 87 `abi 1`
  and one `abi 9` (the `version` fixture). Recount after interfaces (the
  methods design counted 79 earlier; the tree has grown) and migrate
  **every** committed `.sym` in the same change:
  Stage A — all to `obosym 4` / `compiler "0.8"` / `abi 1`, except `format`
  → `obosym 5` (message `has format 5; this compiler reads 4`), `compiler` →
  format 4 with `"9.9"`, `version` → format 4/0.8 with `abi 9`; the goldens
  are regenerated explicitly with `sym_test.py --update` after the golden
  tree gains a function-typed parameter and alias. Stage B — all files'
  `compiler` line to `"0.9"` (format/ABI unchanged; headers are outside the
  interface hash, so no hash changes), `compiler` fixture stays `"9.9"`,
  `sym_test.py` expects `0.9`; the goldens are re-emitted, byte-identical
  except the header.
- `sym_test.py` constants/literals (`OBO_VERSION`, `SYM_FORMAT`, the version
  table, `handwritten_interface`, `b6_stub_completion`) move with each
  version. `sym_fuzz.py`'s `change_version` mutates the format to `5` (not
  4) and its seed may gain a function-typed line without weakening the
  oracle. `port_compare.py` gets `SEED_FILE_MIN` entries `(0, 8)` for the new
  Stage A run/error files and `(0, 9)` for Stage B; module/sym trees are
  outside the seed comparison and rely on the new compiler's own gates.
- A full build writes the dependency's `.sym` and reads it immediately; the
  reader's mode 0 comparison is defence in depth. A `--check` against a
  forged but well-formed format-4 file trusts it, as the `.sym` trust model
  already says.

---

## 7. Ownership, lifetime and effects

### 7.1 Function values are copied, not owned

A function value is a one-word cell pointer. It is not an owned value:
`isOwned(fn)` is false, so `consume` returns before any move bookkeeping
(`obo.obo:2959`), scope exit does not free it (`freeOwned` only frees owned
locals, `obo.obo:2596-2614`), and assigning one over another abandons the old
cell without a free. A non-capturing cell is read-only static data. A
capturing cell is frame memory, reclaimed when the creating function
returns; it contains no owned value, so there is nothing to free and no
destructor.

A function-typed local may be `let` or `var`; `var` may be reassigned from
another lambda, with the old cell simply abandoned. A function-typed
parameter is one word, copied on entry; a callee may copy it to a local,
pass it onward, call it or use it in a `defer`. It may not return it, store
it or put it in a record/array/global/`Result`.

### 7.2 Capture bindings and the reference model

The only new ownership object is a **capture binding**, used at the call that
receives a capturing closure. It is an ordinary `^RefBind` with read-only
writability; it reuses `holdArg`/`argOrderCheck`, `invalidate`,
`invalidateDeep`, `hitBy`, the mut-alias loop, the global-reference loop and
the handle-borrow loop. No new state is added to `PState`, `RefBind`, `Sym` or
the reference checker. The binding is:

- the captured owned local itself, whole-object (`singleBind(local, false,
  "", false, nil)`);
- the captured reference's own binding (roots/deeps/paths copied, writable
  false);
- a tracked `let` handle's origin owner (singleBind(owner, ...)), if any;
- nothing for a scalar, an untracked handle, or a function value.

It is registered for the function-valued argument exactly like a reference
argument (mode 1) but no reference is pushed. A later argument that moves or
frees a captured root marks the holder moved and `argOrderCheck` refuses
(`argument <j> of <f> may free what argument <i> captures, and runs after
it; compute it before the call`). A mut argument that may overlap a capture
is refused by the pair loop (`argument <i> of <f> captures a place that
argument <j> changes, which is mut`, and the reverse). A capture binding with
a deep place, a handle root or a global root is refused when the callee is
impure (`freesAll`/`writesHandles`), exactly as a reference argument is
today (`cannot pass a reference that points inside something to <f>: it
writes through a handle...`, `cannot pass a reference that borrows through a
handle to <f>: it might free what the handle names`, `cannot pass a reference
into global '<g>' to <f>: it might free it`; the closure wording is in
section 9.3). A **pure** callback helper passes all of them: it cannot free,
free through a handle or free a global, which is what those checks care
about (it may still write scalar fields or append through a mut parameter).
An impure callee for other reasons refuses deep/handle/global captures. A
whole-object local/parameter capture is the safest shape and always passes.

### 7.3 Aliasing, moves and invalidation by example

```obo
fn apply(x: int, f: fn(int): int): int { return f(x) }

fn main() {
    let n = 7
    let b = new Box{v: 1}
    // OK: scalar snapshot and a whole-object read-only capture
    println(apply(1, fn [n, b](x: int): int { return x + n + b.v }))

    // Refused: the later argument frees the captured object
    // apply(1, fn [b](x: int): int { return x + b.v }) with a later
    // argument that drops b: the capture holder is invalidated.

    // Refused: the callback cannot change a read-only capture
    // apply(1, fn [b](x: int): int { b.v = 2; return x })
    // error: cannot change through 'b': it is a read-only reference

    // Refused: a capturing literal cannot be bound
    // let f = fn [n](x: int): int { return x + n }
    // error: a capturing lambda may only be passed directly as a call argument

    // Refused: a function value cannot be stored or returned
    // type Holder = ref record { f: fn(int): int }
    // fn make(): fn(int): int { return fn (x: int): int { return x } }
}
```

A non-capturing lambda is not tracked: it is a constant cell. It may be
stored in a local, copied, reassigned, passed through any number of calls and
used in a defer. Its lifetime is the program image.

### 7.4 Effects of the hidden body

The hidden body is a normal function for the ownership checker: its visible
parameters are ordinary owned/read-only parameters; its capture locals are
read-only references or plain `let` values; its `defer`, `try`, loops and
calls use the existing rules. It cannot free below a read-only capture (no
mut reference/inner mutation is reachable), so no origin-free event reaches
the creator's object; its `pfree` summary concerns only the hidden env and
visible parameters and stays 0. **The body is then checked for
purity** (`pureFn(hidden) && !hidden.writesHandles`, `hidden.fnRec.pfree ==
0`): a body that frees a global, frees through a handle, calls an impure or
forward-headed function, or otherwise sets `freesAll`/`writesHandles` is
rejected with `a lambda must be pure...`. A pure body may allocate, append
to owned/global arrays, write scalar globals, use I/O builtins, call pure
functions/methods and call other (pure) function values. Its hidden summary
is never exported; the indirect call's synthetic callee is pure because the
language guarantees every function value was checked.

A callback's visible parameters may be owned (the call moves an argument into
the callback) and the callback frees it at return like any function. A
callback may return an owned value or a string; the indirect-call result is
owned/`strTemp` by the caller. The hidden body's `pfree` for its visible `&`
parameters is 0 (a read-only reference cannot be freed below); an owned
parameter's free is an ordinary owned-local free, not a summary bit.

### 7.5 Residual limits (recorded, not tested as successes)

- **Handle captures are runtime-checked.** A handle captured by value can
  become stale if its owner is freed behind the compiler's tracking after
  capture; the callback's first `RT_DEREF` exits 7. A tracked `let` handle's
  origin binding is checked at the call site, so a move/drop of its owner in
  a later argument is a compile error; an untracked field/element handle is
  the existing runtime residual.
- **Snapshot semantics.** A scalar capture is the value at closure creation;
  later changes to the outer scalar are not seen. A whole-object capture is a
  pointer, so field/element changes made before the call are seen; the outer
  function cannot run during the call.
- **Pure callbacks only.** A lambda may not free a global, write through a
  handle, or call an impure/forward-headed function; such a body is rejected.
  A callback can still append to a global array, write scalar globals or
  scalar fields through a captured handle, use I/O builtins and return values, which covers comparators, predicates and
  collectors; a genuinely impure callback (one that owns/frees shared state
  or frees through a handle) is a later effect-type extension. This is a
  usability limit, not an unsoundness; section 10.5 shows the comparator
  shape that works.
- **No owned captures.** Section 4.7.
- **No named function values.** Section 14: the function-value ABI always
  has a hidden environment parameter, so a named function (which has none)
  cannot be used directly; a lambda wrapper is the v1 spelling. A compiler-
  generated thunk is the extension.

### 7.6 No hidden frees

The design adds no runtime allocation, no owned capture, no destructor and no
new runtime slot. The only allocations are the frame cell (stack memory) and
the read-only cell (rodata); neither is owned. A function value can never
outlive its creator's frame because it cannot escape the call chain. This is
the property that keeps the feature within the existing "one owner, frees at
a known point" model.

---

## 8. Dispatch and calling convention

### 8.1 Static dispatch only

There is no vtable, no dynamic type tag, no runtime method lookup and no
devirtualisation. A function-typed value's static type is its function type;
calling it is one indirect call through the cell. The compiler never tries to
recover which lambda produced a value: a `let` may be reassigned, a parameter
may receive any matching value, and a callee cannot see the creator's
provenance. The call's effects are those of a pure call (section 8.4),
independent of provenance, because purity is a language invariant of every
function value.

### 8.2 Function type identity and compatibility

- Identity is structural: parameter count, each parameter's mode (`owned` or
  `&`) and type by `same`, and the return type by `same`.
- No overloading, no defaults, no varargs, no variance, no subtyping. A
  `byte` argument to an `int` parameter is allowed by the existing
  `checkAssign` byte-constant rule at the call, but `fn(byte)` is not
  assignable to `fn(int)`.
- An alias is transparent; two modules' structurally equal function types
  are compatible.
- A lambda's self-described type comes only from its header. There is no
  inference from a destination; the destination's function type is checked
  with `checkAssign` after the lambda's type is built.
- A named function, a method and a function value are different things: a
  named function is a direct `^Sym` call (`nameItem`, `obo.obo:5656-5659`);
  a method is a direct call with a receiver (`methodCall`, `obo.obo:5894-
  5921`); a function value is one indirect call. There is no implicit
  conversion among them.

### 8.3 The call-through-cell ABI

```text
function value (one word) ---> cell: [ code_address ][ capture words... ]
                                  word 0            word 1...
indirect call:
    save live registers; pad;
    push cell_address            // hidden parameter 0 (the environment)
    push visible arguments left to right;
    call qword ptr [cell_address];
    pop/cleanup; result in rax
hidden callee:
    fn (env: int, visible params...) -> ret
```

- Visible parameters are the function type's parameters. The hidden env
  parameter is not in the function type and never in a `.sym`.
- The stack layout matches the existing convention: parameter 0 (env) is at
  the highest address; visible parameters follow in order. `fnBody`'s
  parameter-offset arithmetic (`16 + 8 * (n - 1 - i)`) works with the hidden
  parameter list `[env] + visible`.
- The stack is padded to 16 bytes for the callee's runtime calls, exactly as
  `callFnWith` pads for `len(params)` arguments (`obo.obo:4877-4881`), with
  `len(params) + 1` for the hidden env.
- The call is `FF /2` with a memory operand `[cell]`; the cell's word 0 is
  the absolute virtual address. No rel32 and no fixup: a function value can
  be copied, assigned and passed without relocating anything. This is why a
  function value can cross a module boundary without an address in a `.sym`.
- The hidden body's own prologue/epilogue and frame are a normal function's;
  its `ret` returns to the indirect call site. `dropFrame` may move a
  zero-frame hidden entry; the cell is emitted after that and stores the
  final address.
- The engine is single-image, non-PIE with fixed base addresses already used
  by descriptors; a rodata cell holding an absolute code address follows the
  descriptor precedent (`obo.obo:2059-2070`).

### 8.4 Effect summary of an indirect call

The synthetic callee (section 5.4) has `addr = 0`, `freesAll = false`,
`writesHandles = false`, `pfree = 0`, no `freed` list, and the function
type's parameters/return. It is a **pure** callee by the compiler's predicate
(`pureFn` true), which is sound because every lambda body is checked for
purity at its literal (section 3.4) and no named function/method is a value.
Consequences:

- `afterCall` does nothing: no `freesAll` inheritance, no deep/handle/global
  invalidation (`obo.obo:3540-3572`);
- `pureFn` is true, so a deep reference, a handle-rooted reference and a
  reference into a global may be passed to the call; this is what lets a
  comparator receive `&items[i]` from a `&[]Item` parameter;
- no mut parameter exists in a function type, so the pfree loop has nothing
  to invalidate; the capture pseudo-binding is mode 1; the mut-alias checks
  remain conservative;
- `movesFn` is false, so `userCalls` is not incremented and a target located
  before the call is not treated as crossing a freeing call
  (`checkTargetAcrossCall`); the pure call cannot free the target;
- the call still consumes owned arguments (`consume` ran first and marked
  the caller's local moved), and its result is owned/string-temp exactly as a
  direct call's;
- the `pureFn`/`writesHandles` summary is not a type annotation: it is a
  language invariant. A forged `.sym` can declare any function signature, but
  it cannot create a function value; every value comes from a checked lambda.

### 8.5 Performance

- A non-capturing lambda is a read-only 8-byte cell; creating/copying it is
  one word move; calling it is one load of the cell address (usually a
  constant), one load of the code word, an indirect branch and the ordinary
  argument sequence. It is slower than a direct `E8` call and is not
  devirtualised.
- A capturing lambda additionally allocates `1 + ncap` frame words at the
  literal site and fills them; no heap allocation, no reference counts (v1).
  Passing it is one word.
- The compiler source does not use lambdas in round 1, so the compiler's
  size/peak measurement should be close to the interfaces baseline plus the
  new code, not plus emitted callbacks. The tools are untouched in this
  phase; any comparator refactor is a separate branch with its own
  measurement.

---

## 9. Diagnostics

Style follows `LANGUAGE.md` section 10: lower-case, single quotes around
names, no trailing period, types through `tname`. `<T>` is a type,
`<f>` a function/local name, `<x>` a variable, `<n>` an integer.

### 9.1 Declarations and types

| situation | message |
|---|---|
| lambda parameter declared `mut` | `a lambda parameter cannot be mut; a function value takes owned or read-only parameters` |
| lambda `return value` with no return type | `a lambda that returns a value needs a return type` |
| empty capture list | `a capture list needs at least one name` |
| duplicate capture | `capture '<x>' given twice` |
| capture also a lambda parameter | `capture '<x>' is also a parameter of the lambda` |
| function type with a capture list | `a function type cannot have a capture list` |
| function return type | `a function cannot return a function value; function values are parameters and locals only` |
| record/closed field | `a record field cannot be a function value; function values are parameters and locals only` |
| array element/`[]fn` | `an array cannot hold function values` |
| optional | `an optional cannot hold a function value; function values are parameters and locals only` |
| `Result[fn]` | `Result cannot hold a function value` |
| global | `a function value cannot be a global; it is a parameter or a local` |
| constant | `a constant cannot hold a function value` |
| handle type `^fn` / `handle(f)` | the existing handle-rule messages (`handles name records and closed types, not fn(...)` for the type; `handle needs a record or closed-type value that is not optional, not fn(...)` for the builtin) |
| borrowed function type | `only records, closed types and arrays can be borrowed, not fn(...)` (existing) |
| method receiver | `a method's receiver must be a record or closed type declared in this module, not fn(...)` (existing) |
| interface member signature | `an interface method cannot use a function value; function types are not supported in interfaces` |
| forward type then fn alias | `a function type cannot be forward-declared: '<T>' is already declared as a forward type` |
| exported signature mentions a private type inside a fn type | `exported '<f>' uses private type '<T>'; mark '<T>' pub too` (existing, via `closureCheck`) |

### 9.2 Literals and captures

| situation | message |
|---|---|
| lambda at top level/global/const (`curFn == nil`) | `a lambda can only be created inside a function` |
| lambda literal in a while condition | `a lambda literal cannot appear in a while condition; bind it to a local before the loop` |
| lambda literal in a defer body | `a lambda literal cannot appear in a defer body; bind it to a local before the defer` |
| capturing literal outside a call argument | `a capturing lambda may only be passed directly as a call argument` |
| capture name not a local/parameter | `'<x>' is not a local variable or parameter; a lambda uses globals directly, without capturing` |
| capture name is a top-level function | `cannot capture function '<f>'; call it directly` |
| enclosing local not listed in the capture list | `'<x>' is a local of the enclosing function; add it to the lambda's capture list: fn [<x>](...)` |
| capture of a string | `cannot capture '<x>': strings are owned; pass it as a parameter` |
| capture of an interface value | `cannot capture interface value '<x>'; pass it as a parameter` |
| a capture mode word (`fn [mut x]`, `fn [move x]`) | `captures are read-only: write fn [x]; mutable and owned captures are not supported` (the parser reads the second word as a capture name; check for this intent before resolving, or let the not-a-local error stand) |
| a lambda body naming the local it is being assigned to | the existing `undeclared identifier '<x>' (declare before use; use a forward heading for mutual recursion)`; a lambda has no name and cannot recurse directly |
| write/move/free of a read-only capture | the existing read-only-reference/move messages, e.g. `cannot change through '<x>': it is a read-only reference`, `cannot move out of a reference: a reference only borrows` |
| scalar capture assignment | `cannot change captured '<x>': captures are read-only; pass a mutable parameter instead` (the implementation marks capture symbols with a `captureSyms` list or a `capture` flag so `assignmentOrCall` can say this instead of the generic immutable-local message) |
| capture of a moved local | `'<x>' was moved and has no value here` (existing) |
| capture of a freed reference | `'<x>' may point to freed memory: something under what it points into was changed; point it again first` (existing) |
| impure lambda body (frees a global, frees owned state through a handle, calls an impure/forward-headed function) | `a lambda must be pure: it may not free a global or free owned state through a handle; call a pure function instead` |
| call to an active enclosing function or method | `cannot call '<f>' from a lambda while it is being compiled: its effects are not known yet` |

### 9.3 Calls and capture arguments

| situation | message |
|---|---|
| calling a non-function local (unchanged) | `'<x>' is not a function` |
| method name used as a value (unchanged) | `method '<T>::<m>' cannot be used as a value: call it with '(' after its name` |
| arity | `function value '<f>' takes <N> argument(s), not <M>` / `... not more` |
| argument type | `argument <n> of function value '<f>': cannot use <t> as <t>` |
| a following `.`/`[` on an indirect call result | the enclosing construct's syntax error (the call ends the designator) |
| field/method on a function value | `a function value has no fields or methods; call it with '(' after its name` |
| indexing a function value | `fn(...) cannot be indexed` (existing tname form) |
| comparison | `function values cannot be compared` |
| `f == nil` (relation) | `a function value can never be nil` (checked before the general function-value comparison) |
| `f = nil` (assignment) | `a function value can never be nil` |
| `println(f)` | `cannot print fn(...)` (existing) |
| `drop`/`take`/`replace`/`handle`/`alive` | their existing owned/optional/handle messages, with `fn(...)` in `tname` |
| later argument frees a capture | `argument <j> of <f> may free what argument <i> captures, and runs after it; compute it before the call` |
| mut argument overlaps a capture | `argument <i> of <f> captures a place that argument <j> changes, which is mut` / the receiver form |
| deep capture to an **impure** callee (one that frees a global/frees through a handle for its own reasons; a pure callback helper accepts it) | `cannot pass a closure that captures a reference inside something to <f>: it writes through a handle, which could free it` |
| handle-rooted capture to an impure callee | `cannot pass a closure that captures a reference through a handle to <f>: it might free what the handle names` |
| global-rooted capture to an impure callee that may free it | `cannot pass a closure that captures a reference into global '<g>' to <f>: it might free it` |
| capture holder runs after a move | the existing `argument <n> of <f> may free what argument <m> points into...` / capture wording above |

### 9.4 Symbol files

| situation | detail |
|---|---|
| malformed function type | `expected '(' after 'fn'`; `a function type cannot have a capture list`; `expected a parameter type`; `expected ',' or ')'`; `a function type cannot return a function value`; `only records, closed types and arrays can be borrowed, not fn(...)` |
| function field/global/constant/array/optional/Result/return in a file | the section 9.1-specific detail (`a record field cannot be a function value`, `a function value cannot be a global`, `Result cannot hold a function value`, `a function cannot return a function value...`, ...) |
| function type in an interface member | `an interface method cannot use a function value; function types are not supported in interfaces` |
| forward shell then fn alias | `a function type cannot be forward-declared: '<T>' is already declared as a forward type` |
| duplicate alias/name | the existing `duplicate exported name '<name>'` |
| live/file disagreement | the existing `symbol file '<p>' disagrees with the compiled interface of '<m>' for fn '<name>'; rebuild it` (a function-typed signature is compared by structural `same`) |
| format/ABI/compiler | `has format <n>; this compiler reads 4; rebuild it`; `has ABI 9; this compiler uses 1; rebuild it`; `was built by compiler '9.9'; this is '0.8'; rebuild it` (Stage B: `'0.9'`) |

---

## 10. Tests and mutants

Every case is written with the change, never after it; no existing assertion
is deleted or relaxed. Stage A tests use only non-capturing literals; Stage B
adds the capture-list cases.

### 10.1 Stage A run programs (`tests/run/`)

| file | covers |
|---|---|
| `lambdas.obx` | a lambda local and call; a lambda passed directly to a function; a lambda as a method argument; a lambda with no parameters and a void return; parameters owned/`&`; a lambda returning int/string/record/array/`Result`; a copied function value; `var` reassignment; a call in `println`, a `let` initializer, a `while` body, a `return` of a non-function value; a lambda wrapping a named function and a method |
| `lambdas_higher.obx` | a lambda with a function-typed parameter (higher-order); a function value passed onward; a function value returned? no (an error case); a function value in a local passed to another local; a `try` through a callback returning `Result` |
| `lambdas_effects.obx` | an indirect call is pure: a deep reference into an array passed to the callback stays valid after the call; a helper that calls the callback and writes scalars/appends stays pure and can be imported into a pure context; a lambda that appends to a global array or writes a scalar global is accepted; a callback result that is owned/string is freed correctly; an impure lambda body is an error case (10.2) |
| `lambdas_sort.obx` | the comparator-shaped insertion sort of an index array over `[]Item` with a non-capturing `fn(&Item,&Item): int`; ascending and descending; a `[]int` comparator taking ints by value; stable equal-key order; see 10.5 |
| `lambdas_homes.obx` | a function with register-homed int locals and a lambda literal, run under `homes_check`/`homes_edge` and the `obo-noregs` build; the island does not disturb the outer homes |
| `lambdas_scope.obx` | a lambda in a block, a loop body, a match arm and a method body; an indirect call through a function-typed local in an if/while condition (the literal is bound before the loop, never in it); a defer's *call* (not its literal); the same non-capturing literal executed twice uses the same read-only cell |

### 10.2 Stage A error programs (`tests/errors/lambdas.obx`)

One `// ===` section per error with the exact section 9 text:

- declaration: `mut` lambda parameter; return value without a return type;
  empty capture list; function return; field; array; optional; `Result`;
  global; constant; handle; `&fn`; method receiver; interface member (module
  tree if interfaces are merged); forward shell + alias; private type inside
  an exported callback signature (module tree).
- calls: non-function local call (unchanged message); arity (too few/too
  many); argument type/mode (owned vs `&`, `&` to owned, owned to `&`);
  calling a function value with a record argument when it expects `&T`;
  `f().n`, `f()[0]`, `f.m()`, `f[i]`; `f == g`, `f == nil`, `f = nil`;
  `println(f)`, `drop(f)`, `take(f)`, `replace(f, ...)`, `handle(f)`,
  `alive(f)`, `match f`, `for x in f`, `if f`, `f + 1`, `&f`.
- contexts: a lambda literal at top level/global/`const`; in a while
  condition; in a defer body; a call to the active enclosing function; a
  lambda literal used as a statement; a lambda literal invoked directly.
- effects: a function value argument that moves the object a later argument
  needs; an indirect call in an assignment target across a call; an impure
  lambda body (`a lambda must be pure...`) for a body that frees a global,
  frees through a handle, calls an impure function or calls a forward-headed
  function; an impure function that takes a callback and a deep/handle/global
  capture (the existing reference/capture messages).

### 10.3 Stage B run programs (`tests/run/`)

| file | covers |
|---|---|
| `lambda_closures.obx` | `fn [n]` scalar snapshot; `fn [b]` whole record read-only (field reads and a read-only method); `fn [xs]` whole array read-only; a captured optional and `Result` (non-consuming match); capture list order; a capture used inside an if/while in the body; a callback called twice |
| `lambda_refs.obx` | capturing a reference parameter/whole-object reference and a reference into a field/element (accepted when passed to a **pure** callback helper: the pure callee cannot free it; refused when passed to an impure function that also takes the callback); a captured tracked `let` handle whose owner is moved later (refused at the call) |
| `lambda_handles.obx` | capturing a `^T` handle by value and **reading** through it in the callback (pure); a **scalar write** through the captured handle is accepted and pure; a write that **frees an owned field** through it is an error case (`a lambda must be pure...`); a handle capture with a tracked origin refused when the owner is moved; a `// expect-exit: 7` case for an untracked stale field handle |
| `lambda_methods.obx` | a lambda in a method body capturing the `&`/`mut &`/owned receiver read-only; a method taking a callback and calling it; a lambda capturing a `&` receiver and calling a read-only method; a `mut` method call through the capture refused (error case) |
| `lambda_modules.obx` | a module tree: main passes a capturing closure to `lib::apply`, which calls it; a two-hop call (`lib1` → `lib2`) with the closure; a scalar, record and handle capture (read-only); `lib::apply`'s summary is **pure** (it calls a pure callback), so an importer may pass a deep capture; a separate impure function that also takes a callback refuses a deep capture |
| `lambda_sort_capture.obx` | the comparator shape with a captured `Opts` record read-only and a captured scalar; ascending/descending; a sort helper that calls the pure callback and has a `mut` array parameter; the helper is pure and accepts `&items[i]` deep references; a callback appending to a global result array (pure state channel) |
| `closures_scope.obx` | a capturing literal in a loop body (each iteration), in an if arm, in a match arm, in a method call argument; a non-capturing lambda local in a defer; nested lambdas (an inner non-capturing and a capturing inner capturing an outer lambda's local) |

### 10.4 Stage B error programs (`tests/errors/closures.obx`)

- the capture-list diagnostics (unknown/global/function/string/interface/
  duplicate/also-a-parameter/unlisted local);
- capture of a moved local or a freed reference;
- write/move/free/drop/take/replace/append/mut-method on a read-only capture;
- capturing literal in a `let`/assignment/return/field/array/global/
  `Result`/optional/while condition/defer/top level;
- a later argument that moves/frees a capture (the capture wording);
- a mut argument that overlaps a capture (both directions, including a
  method receiver);
- a deep/handle-rooted/global-rooted capture passed to an **impure** callee
  that also takes the callback (one that frees a global/frees through a handle for
  its own reasons); a pure callback helper accepts them (a run case);
- a captured tracked let-handle whose owner is dropped/moved by a later
  argument;
- an acceptance case (a run program, not this error file): a callback called
  after the callee's mut argument frees an unrelated object, where the
  capture binding does not overlap that argument;
- a callback with an owned parameter that is moved out of by a later arg
  (the ordinary argument-order error).

### 10.5 The sorting-comparator and tool-callback shape

The intended use is an **index sort**, not a callback that swaps records (an
array element cannot be moved out). The library/tool API is a helper that
takes an index array and a comparator over borrowed elements:

```obo
// shape only; not added by this pass
fn sortIndices(mut order: &[]int, items: &[]Item, cmp: fn(&Item, &Item): int) {
    var i = 1
    while i < len(order) {
        let v = order[i]
        var j = i - 1
        while j >= 0 && cmp(&items[order[j]], &items[v]) > 0 {
            order[j + 1] = order[j]
            j -= 1
        }
        order[j + 1] = v
        i += 1
    }
}

fn main() {
    // build order = 0..len(items)-1, fill items
    sortIndices(&order, items, fn (a: &Item, b: &Item): int { return a.n - b.n })
    // a captured whole record: read-only
    let opts = new Opts{descending: true}
    sortIndices(&order, items, fn [opts](a: &Item, b: &Item): int {
        if opts.descending { return b.n - a.n }
        return a.n - b.n
    })
}
```

- The comparator's parameters are read-only borrows, so the helper can call
  it on array elements without moving them.
- The helper calls a pure function value, so its own summary stays pure
  (it writes the index array's scalars through a `mut` parameter, which
  `pureFn` allows; it does not truncate/free it). It can pass
  `&items[order[j]]` deep references to the callback; the pure call does not
  invalidate them. The helper can be imported and used in a pure caller.
- The helper's `mut order: &[]int` param and a captured `items` reference:
  if the closure captured `items` and the helper also passed `items` as a
  `mut` argument, the mut-alias check would refuse the overlap; the shape
  above passes `items` only as a `&` reference.
- A callback may append to a global result array or write a scalar global
  and remain pure (append is not a free); it cannot assign over an owned
  global or truncate a global array without becoming impure.
- `int` comparators may take ints by value (`fn(int,int): int`) for a
  `[]int` sort; strings by value (`fn(string,string): int`) if desired (the
  callback owns the string copies and frees them; `pureFn` allows freeing
  owned parameters).
- A `lib/sort.obx` API addition and a `cmd/eza.obx` comparator refactor are a
  separate branch after the lambdas merge and seed promote. The existing
  specialized radix sorts and the differential outputs must not change;
  only new generic helpers or a mechanical comparator replacement may use
  the feature. `eza`'s `Opts` record and `Entry` are natural captures/params
  (read-only `Opts`, `&Entry` parameters).

### 10.6 Modules and symbol files

| case | what |
|---|---|
| `modules/run/lambdas` | `lib` exports `pub fn apply(x: int, f: fn(int): int): int` and a `pub type Cmp = fn(&Item,&Item): int`; main passes non-capturing lambdas, a lambda wrapper for a named function, and a function value from a local; calls work and the function value crosses the module boundary |
| `modules/run/lambda_closures` | main passes a capturing closure to `lib::apply`; lib calls it; scalar/record/handle captures (read-only); a two-hop module call; `lib::apply`'s summary is pure in `lib.sym` (a callback call does not make it impure) |
| `modules/run/lambda_transitive` | L exports a function type alias and a function taking it; A imports L and exports `fn run(cb: lib::Cmp)`; B imports only A and passes a lambda to `a::run`; the structural type travels in A's `.sym` as `fn(...)`, not `lib::Cmp` |
| `modules/sym/lambdas` | a positive `symcheck` tree: a committed `.sym` with `fn(...)` in a signature and a `type Cmp = fn(...)` alias; a source-less checked root passes a lambda; exit 0 |
| `modules/sym/lambda_transitive_check` | the transitive tree source-less; exit 0 (not merely REF==PORT) |
| `modules/errors/lambda_signature` | passing a lambda whose parameter modes/types/return differ from the imported parameter: the exact `argument ... cannot use fn(...) as fn(...)` |
| `modules/errors/lambda_storage` | an attempt to store a function value in an exported record/global/return in a dependency; the source/module error |
| `modules/errors/lambda_capture` | a capturing literal in a `let`/while/defer in a checked module; the section 9 messages |
| golden tree | `lib.obx` gains a pub function with a `fn(&Item,&Item): int` parameter and a `pub type Cmp = fn(...)`; the 3 `expected/*.sym` are regenerated explicitly |

`sym_test.py` additions:
- `RICH_LIB` gains a pub function with a fn-typed parameter, a pub alias and
  (after interfaces) a method with a fn parameter; `RICH_MAIN` passes a
  lambda; `roundtrip_rich`, `determinism`, `hash_bytes`,
  `check_mode_and_cli` and `--reemit-sym` cover writer/reader/cross-mode.
- `hash_private_and_rename`: changing a function type's parameter mode, type
  or return changes the interface hash; a lambda body change (add a
  statement, rename a local) changes only the source hash; adding a private
  lambda doesn't change the interface; adding/removing a `pub` function with
  a fn parameter changes it; reordering exported declarations changes the
  interface as today.
- `malformed_structures` with a fn type as the fixture: `fn` in a field,
  global, return, array, optional, Result, interface member; malformed
  `fn(`, `fn(&int)`, `fn(...): fn(...)`, a missing `)`; alias spelling;
  duplicate alias; forward shell + fn alias; a fn type in a method receiver.
- `sourceless_and_build_requirement`: `--check` against a source-less
  dependency with a fn-typed parameter and alias, asserting exit 0; the
  checked module's own `.sym` contains its fn signature; a stale 0.7/format-3
  file is rejected with `has format 3; this compiler reads 4`; `--version` is
  `obo 0.8 sym-format 4 abi 1` (Stage A) / `0.9` (Stage B).
- Version table: format `5` (expected `has format 5; this compiler reads 4`),
  ABI `9`, compiler `"9.9"` (expected `this is '0.8'` / `'0.9'`), each
  mutated from the new base.
- Golden: regenerate after the golden tree gains the fn parameter/alias.

**Fixture migration.** Section 6.5: 88 files at this baseline (recount after
interfaces); all migrate in the same change; the `format`/`compiler`/`version`
fixtures are migrated to the new base plus their rejection value; the
goldens are regenerated explicitly. Headers are outside the interface hash,
so a header-only migration changes no hash. Stage B changes only the
`compiler` line to `"0.9"` (and the goldens' header) plus `sym_test`'s
expectation.

### 10.7 Existing tests and fixtures

- `tests/sym_test.py`: `OBO_VERSION = '0.8'`, `SYM_FORMAT = 4` (Stage A);
  `OBO_VERSION = '0.9'` (Stage B); version table/literals; the new RICH
  cases; the fixture migration.
- `tests/sym_fuzz.py`: `change_version` mutates to `5`; the fuzz seed may
  gain a fn-typed line; no oracle weakening.
- `tests/port_compare.py`: `SEED_FILE_MIN` `(0, 8)` for each Stage A
  `tests/run/lambdas*.obx` and `tests/errors/lambdas.obx`; `(0, 9)` for the
  Stage B `lambda_closures.obx`/`lambda_refs.obx`/`lambda_handles.obx`/
  `lambda_methods.obx`/`lambda_sort_capture.obx`/`closures_scope.obx` and
  `tests/errors/closures.obx`; the keys are the absolute
  `os.path.join(FORK, 'tests', ...)` paths used by the existing map; module
  trees are outside the seed comparison and use the new compiler's gates.
  Extend the symcheck `expect`/REEMIT assertions.
- `tests/port_test.py`: no token/scanner change; add an `OBO_GROUPS` encoder
  group for `op 255 2 r:N ...` and `op 255 2 m:... ...` (`call r/m64` and
  `call qword ptr [r]`) cross-checked against objdump. The generic `op`
  encoder line already exists (`runEncoderLine`, `obo.obo:804-846`), so the
  test can use it without a new command.
- `tests/mutate_fork.py`/`mutate_port.py`: add `LAMBDA_MUTANTS`; retarget
  patterns moved by `nameItem`/`designatorT`/`callFnWith`/`fnDecl`; a stale
  pattern fails the gate by design, so retarget, do not delete. Record
  mode-0-only mutants as equivalent (a full build reads the file it just
  wrote), as the methods/interfaces designs do.
- `tests/doc_check.py`: new `// example:` blocks in `LANGUAGE.md`.
- No existing run/error program changes; the feature is additive. The
  compiler source uses no lambda/function-type syntax in round 1.
- `tests/own_fuzz.py` is unchanged; it does not generate lambdas (record as
  a limit). `elf_test.py`/`io_test.py`/`meta_test.py` run unchanged.

### 10.8 Mutants (~25 targets; the exact snippets are captured at implementation)

| mutant | expected catcher |
|---|---|
| `same` has no fn branch | wrong-signature acceptance; mode-0 `.sym` disagreement |
| `tname`/`appTypeExpr` has no fn branch | wrong diagnostics/golden |
| `parseType`/`symParseType` fn branch drops a parameter mode | wrong type/mode acceptance |
| fn allowed as a field/global/array/optional/Result/return | the corresponding error case |
| lambda parameter `mut` accepted | errors |
| hidden body compiled in the outer frame/no prologue | run crash/wrong output |
| env not pushed first (or pushed after the args) | shifted arguments/wrong output |
| cell stores a code offset instead of `CODE_BASE+entry` | crash |
| zero-frame hidden entry moved by `dropFrame` but the cell keeps the old entry | zero-frame lambda crash |
| `call qword ptr [cell]` replaced by a direct `E8`/misencoded | run/encoder test |
| indirect result ownership/strTemp dropped | leak/double-free oracle |
| an owned argument at an indirect call not consumed | leak/double-free (the callee also frees it) |
| lambda purity check dropped (impure body accepted, call still marked pure) | errors/lambdas: the impure-lambda cases fail to error; a hidden global/handle free can double-free in a run case |
| indirect call marked `freesAll`/`writesHandles` instead of pure | run/lambdas_sort: deep element references are refused; the helper's `.sym` summary is impure |
| function value comparison allowed | errors |
| function value stored in a field/global/return | errors |
| capturing literal outside a call argument accepted | errors |
| lambda literal in a while condition accepted | miscompile/crash |
| lambda literal in a defer accepted | state corruption/crash |
| capture list unknown/global/function name accepted | errors |
| capture of a moved/freed local accepted | use-after-free error case |
| read-only capture made writable | write compiles/mutates |
| capture binding not held across later args | arg-order use-after-free |
| capture binding not in the mut-alias checks (or in the global/handle/deep checks for an impure callee) | overlap/capture accepted where refused |
| string/interface capture accepted | errors/leak |
| active enclosing function call allowed | unsound summary (record equivalent if unobservable) |
| `.sym` writer omits the fn type/modes | roundtrip/golden |
| `.sym` reader accepts fn in a field/global/return | malformed cases |
| `closureCheck` doesn't recurse fn types | pub private-type case |
| capture/env offset off by one | wrong captured value |

The while/defer and active-call mutants may be equivalent if the context flag
is a pure parser check with no alternative path; record that with the reason
rather than claiming a catch. The memory/peak and size results are gates, not
mutants.

### 10.9 Gates

On the merged tree, per stage:

- `run_fork.py` (run, errors, module trees, sym tests, own-fuzz),
  `module_test.py`, `sym_test.py` (golden compare, no `--update`),
  `sym_fuzz.py`, `doc_check.py`, `homes_check.py`, `homes_edge.py`,
  `elf_test.py`, `io_test.py`, `meta_test.py`;
- `port_test.py`, `port_compare.py` (the default comparison, the symcheck
  expect assertions and the seed comparison with the new `(0, 8)`/`(0, 9)`
  skips), `mutate_fork.py`, `mutate_port.py`, `mutate_io.py`;
- `imports.py`, `stdlib.py`, `tools.py` and `eza.py` unchanged and green
  (no library/tool/source changes);
- fixed point: `make obo` with the seed, then stage2 == stage3 (`cmp`), and
  the measured compiler peak within the budget (do not raise `PEAK_BUDGET`
  without the user);
- one seed promotion after the independent review and merge, only if the
  source changed (it will). Stage A and Stage B each get their own
  review/merge/promote if implemented separately.

---

## 11. Implementation plan

### 11.1 Ordered steps

0. **Wait for interfaces.** Rebase on the interfaces merge and its seed
   promote; confirm `0.7`/format 3/ABI 1 and the fixture count; shift the
   target numbers if interfaces used different values. Do not implement on
   the 0.6 tree while interfaces is in flight.
1. **Data model and restrictions, no behaviour.** The `fn` type encoding and
   helpers (`newFnType`, `fnAddParam`, `fnArity`, `fnParam`, `fnMode`,
   `fnRet`); `same`/`assignable`/`tname`; `isOwned`/`isRef`/`nonNull`;
   `parseType`/`symParseType` branches and every storage restriction;
   `fieldList`/`varDecl`/`fnDecl`/`constDecl`/`ok`/`relation` checks;
   `closureCheck`; interface-member rejection (if merged). Build with the
   0.7 seed; the full existing suite must pass unchanged.
2. **Function types in `.sym`, version bump and fixtures.** `appTypeExpr`,
   `symTypeText`, `appParams`/`appFn`/`appMethod`/`appTypeAlias`;
   `symFieldList`/`symGlobalDecl`/`symFnDecl`/`symTypeDecl` restrictions;
   `OBO_VERSION "0.8"`/`SYM_FORMAT 4`; migrate all committed `.sym`
   fixtures and the goldens; update `sym_test.py`/`sym_fuzz.py`; add a
   module/sym case with a fn-typed parameter and alias (the function is
   declared but not yet called). This is the format checkpoint.
3. **Non-capturing lambdas and indirect calls.** `lambdaExpr` (no capture
   list), the code island, the `LamCtx` save/restore, hidden prologue/
   parameters, `dropFrame`/cell, `callValue`, the `designatorT` path, the
   `call qword ptr [cell]` emitter, the pure synthetic callee and the lambda
   purity check (`pureFn(hidden) && !hidden.writesHandles`), the context
   restrictions (while/defer/top-level/active call), and the missing-cell/
   storage diagnostics. Add the Stage A run/error/module tests; run the
   existing error tests before and after the call-path change to prove direct
   call messages are byte-identical.
   **Checkpoint A:** full suite + new tests + homes checks; measure the
   compiler peak; do not touch captures yet.
4. **Stage A docs, mutants, review, merge, promote 0.8.** LANGUAGE section
   after the interfaces section (lettered `5c` if interfaces is `5b`),
   README phase/status/version, FORK record after implementation; mutants;
   all gates; one promote.
5. **Stage B capture list and environment.** Capture-list parser/resolution,
   `LambdaCap`, capture symbols/prologue, frame cell, `lastFnBind` and the
   capture binding, argument holds/mut-alias/deep/handle/global checks,
   read-only/value rules, call-argument-only/while/defer restrictions,
   string/interface rejection, active-call rule, diagnostics. Add the Stage
   B run/error/module tests.
   **Checkpoint B:** full suite + Stage B tests + homes checks; peak; then
   docs/mutants/review/merge/promote 0.9 (format stays 4).
6. **Adoption (separate branch, after the promote):** a `lib/sort.obx`
   comparator helper and/or an `eza` comparator refactor using the shapes in
   10.5; differential outputs unchanged; no compiler/tool source change in
   this phase.

### 11.2 Files and functions

- `obo/src/obo.obo`:
  - types/helpers: `Type` `882-895`; `newType` `899-902`; `tname` `937-947`;
    `isRef` `951-954`; `nonNull` `956-958`; `same` `960-966`; `assignable`
    `968-980`; `fieldIndex` `983-988`; `Sym` `992-1034`; `FnRec`
    `1111-1118`; `newSym` `1131-1135`; `Item` `1194-1217`; `newItem`
    `1219-1222`; `makeConst` `1224-1228`; `makeReg` `1230-1234`;
    `rodAlign`/`rod64` `2024-2034`; `pc`/`here` `587-593`; `movRI`
    `651-664`; `opReg`/`opMem` `607-649`.
  - parsing/checking: `parseRefType` `3671-3682`; `parseType` `3973-4014`;
    `fieldList` `4016-4047`; `typeDecl` `4049-4140`; `varDecl` `6553-6677`;
    `factor` `6014-6093`; `designatorT` `5706-5890`; `nameItem`
    `5654-5704`; `methodCall` `5894-5921`; `relation` `6385-6480`;
    `checkAssign` `4721-4735`; `tryExpr` `6111-6195`; `returnStat`
    `7798-7830`; `deferStat` `7834-7907`; `whileStat` `7222-7330`;
    `statement` `7909-7975`; `fnDecl` `8440-8632`; `fnBody` `8322-8399`;
    `dropFrame` `7985-7998`; `closureCheck` `8652-8681`; `checkExports`
    `8683-8706`; `compileUnit` `8784-8907`.
  - calls/effects: `consume` `2942-2977`; `itemBind` `3019-3042`;
    `borrowBind` `3045-3054`; `invalidate` `3273-3288`; `invalidateDeep`
    `3293-3303`; `mutFrees` `3429-3433`; `pureFn` `3440-3446`; `afterCall`
    `3540-3572`; `holdArg` `4773-4785`; `argLabel` `4795-4799`;
    `argOrderCheck` `4804-4814`; `callFnWith` `4861-5047`; the mut-alias/
    global/handle/pfree loops `4971-5023`.
  - symbol files: `appTypeExpr` `9350-9378`; `appParams` `9477-9487`;
    `appFn` `9519-9532`; `appMethod` `9535-9553`; `appTypeAlias`
    `9436-9442`; `symBody` `9582-9653`; `symText` `9656-9686`; `symTypeText`
    `9783-9790`; `symParseType` `9847-9895`; `symFieldList` `9898-9916`;
    `symCompareFields` `9918-9925`; `symCompareOrder` `9947-10013`;
    `symFinish` `10014-10038`; `symTypeDecl` `10040-10209`;
    `symConstDecl` `10211-10273`; `symGlobalDecl` `10274-10302`;
    `symFnDecl` `10304-10599`; `symReadDecls` `10601-10623`; `readSym`
    `10665-10836`; `reemitSym` `10845-10858`; `releaseTables`
    `10860-10879`; constants `109-111`.
  - new: `newFnType`/`fnAddParam`/`fnArity`/`fnParam`/`fnMode`/`fnRet`,
    `lambdaExpr`, `LambdaCap`, `lambdaBody`, the `LamCtx` save/restore,
    `callValue`, `fnValueSym`, the indirect-call emitter helper, the
    while/defer/arg context flags, and the capture-list resolver.
- Tests: new `tests/run/lambdas*.obx`, `lambda_closures.obx`,
  `lambda_refs.obx`, `lambda_handles.obx`, `lambda_methods.obx`,
  `lambda_sort.obx`, `lambda_sort_capture.obx`; `tests/errors/lambdas.obx`,
  `closures.obx`; new `tests/modules/{run,errors,sym}/...` trees; the
  migrated `.sym` fixtures and goldens; `sym_test.py`, `sym_fuzz.py`,
  `port_compare.py`, `port_test.py`, `mutate_fork.py`/`mutate_port.py`;
  `doc_check` examples.
- Docs: `LANGUAGE.md` (new section after interfaces, type table, errors,
  planned-list removal, version), `README.md` (phase table, not-yet list,
  version, `.sym` description, size/status), `FORK.md` (after
  implementation), `obo/design/README.md` entry (at implementation time; this
  design pass is asked to commit only this file). `PERFORMANCE.md` and
  `bench/cli/README.md`: keep the version-labelled samples and add the same
  one-line historical note the earlier phases use if they name the compiler
  version; do not re-measure or rewrite the numbers.
- No change: `obo/runtime/*` (no new slot), `obo/lib/*`, `obo/cmd/*`,
  `obo/tools/*` in the compiler phase; `seed/obo` is not touched in the
  feature commit (the promote is on main after review).

### 11.3 Risks

1. **Nested compilation state is the highest risk.** The island must save and
   restore every function-level global; a missed field corrupts the outer
   function after the literal. Gate: the full existing suite plus a run
   program with a lambda inside a complex expression, and a review of the
   save list against `fnBody`/`fnDecl`/`deferStat`. Use a single `LamCtx`
   record, not ad-hoc saves; add fields by diffing the global declarations.
2. **While-lift and defer restrictions are load-bearing.** A lambda literal
   in a while condition would be copied to `lifted`, truncated
   (`obo.obo:7251-7252`) and re-emitted (`7309-7310`) with a cell pointing
   into cut code; a literal in a defer nests a function compile in a state
   swap. The context flags must reject both before any code is emitted.
3. **Indirect-call codegen and ABI.** Env-first push order, `(nvis+1)` padding,
   the cell temp surviving the arguments, `call qword ptr [cell]` encoding,
   the absolute `CODE_BASE + entry`, and zero-frame `dropFrame` entry moves.
   Gates: the run tests, the encoder/objdump group, `elf_test`, homes checks.
4. **The pure-callback restriction is the semantic risk.** A lambda that
   frees a global, frees through a handle, calls an impure/forward-headed
   function or otherwise becomes `freesAll`/`writesHandles` is rejected. The
   purity check is what lets an indirect call be pure and lets a comparator
   receive deep element references; if it is dropped, the soundness argument
   fails (a hidden free) and the comparator may still compile. The
   comparator/collector shapes in 10.5 must compile, and the impure-lambda
   error cases must fail. If real tools need genuinely impure callbacks,
   that is a user-level effect-type extension, not a silent relaxation.
5. **Capture lifetime.** The call-argument-only rule, read-only captures and
   the argument binding checks carry the soundness argument. A missed hold or
   mut-alias entry is a use-after-free; gates: the later-argument and
   mut-overlap error cases, and the cross-module closure run.
6. **Symbol migration.** 88 fixtures (recount after interfaces), goldens,
   version table, `sym_fuzz`, `port_compare` and the reader/writer must move
   together; positive source-less checks must assert exit 0, not merely
   REF==PORT. The 0.4→0.5 and 0.5→0.6 migrations are the precedent.
7. **Seed compatibility.** All implementation code uses the pre-lambda
   subset; the old seed builds the compiler. Tests use lambdas but are
   skipped in the seed comparison until the promote.
8. **Memory/peak.** The design avoids `Type`/`Item` layout changes (function
   parameters use `Type.fields`; the capture binding is a global). The hidden
   symbols and `FnRec`s live in the existing arenas; truncate `symArena`/
   `fnArena` to the island's marks after the purity check if desired (nothing
   refers to them; the hidden summary has no `pf` rows because its parameters
   are owned/read-only and its captures are read-only), but leave `bindArena`
   to the enclosing function's end so the literal's capture binding survives
   to the call argument loop. Measure the peak and do not raise `PEAK_BUDGET`
   without the user. Fallbacks: a `fnBind` Item field (measure) or a
   per-lambda symbol registry (if a stale handle appears).
9. **Encoder change.** `FF /2` is a new emitted form; the objdump group must
   cover both `call r/m64` and `call qword ptr [r]`, including a high
   register (REX.B) if the pool can allocate one.
10. **Intrusive call-path refactor.** `callFnWith` is shared by every direct
    call and method call; the function-value path must not change existing
    messages. Run the existing error tests before/after; keep function and
    method messages byte-identical.

### 11.4 Size and complexity

Estimated `src/obo.obo`: Stage A +550 to +800 lines (function type
representation/parse/write/read, the lambda literal, the island context
save/restore, the indirect call, restrictions/diagnostics), Stage B +350 to
+550 (capture list, environment cell/prologue, argument bindings), roughly
+900 to +1,350 lines total (after interfaces, about 12,000 → 12,900-13,350).
Tests: roughly 1,500-2,100 lines across `.obx`, Python, module/sym trees,
goldens and mutants; docs. The feature is medium-high complexity: no runtime
change and no ABI change under the recommendation, but a nested compile, a
new type kind, a call-path extension and a symbol-format bump with a
fixture migration. Stage A is comparable to methods; Stage B is a smaller
type-checker/codegen addition on top, but the lifetime argument needs careful
review.

### 11.5 Two rounds and dependencies

- Round 1 does not use lambdas or function types in `obo.obo`; the previous
  seed builds the new compiler. After the promote, a later branch may use
  them (e.g., a comparator in the compiler or a library); that is not this
  phase.
- Stage A depends on the interfaces merge and promote (the format/line
  baseline); Stage B depends on Stage A's promote. Do not start Stage A on
  the 0.6 tree.
- The independent review should try to break: the state save/restore, the
  while/defer restrictions, the env/cell offsets, the capture binding
  checks, cross-module closures, the symbol round-trip and the version
  fixtures. Then merge and promote once per stage.

---

## 12. Interaction with methods and interfaces

| feature | interaction |
|---|---|
| functions | a lambda is an unnamed function body; a function value is not a function symbol; a named function is still not a value (section 14) |
| methods | a method may take function-typed parameters and call them; a method cannot return a function value; a method cannot be a function value (no method values); a function value cannot be a receiver (function types have no methods); a lambda in a method body may capture the receiver read-only |
| method values | explicitly out. `b.m` remains `method '<T>::<m>' cannot be used as a value: call it with '(' after its name` (`obo.obo:5768-5772`). A lambda capturing the receiver and calling a **read-only** method is the v1 stand-in; a `mut` method needs a `mut` capture, which is deferred, or a later `pure mut`/effect extension |
| interfaces | v1 excludes function types from interface member signatures, interface values as function parameters/returns, interfaces of function types, and capturing interface values. A lambda cannot implement or convert to an interface. A function value cannot be an interface value. The features are orthogonal; if both merge, add the exclusions in the interface member parser/reader and the capture resolver |
| interface implementations | an impl's methods are ordinary methods; an extra method with a function-typed parameter that is not in the interface is allowed, but a method matched to an interface member cannot have a signature the interface cannot express |
| closures capturing method receivers | allowed read-only (`&`/`mut &` capture a pointer read-only; an owned receiver is a whole-object read-only borrow), subject to the call-site capture binding checks. A `mut` capture of a receiver is deferred with the other mut captures |
| function values as method parameters/returns | parameters yes; returns no (the return rule applies to every function/method/lambda). A method's `.sym` line carries the function type; the reader/mode-0 comparison uses structural `same` |
| methods/interfaces as function values | no method values and no interface method values. A lambda wrapper is the explicit way to get a callback that calls a method |
| purity/effects | every function value is pure, so a function that calls one and otherwise writes scalars/appends can stay pure; a pure interface member could call a function value in a later extension. Interface purity matching does not see function values (they are not in interface signatures). A lambda body cannot call an impure interface member (the purity check rejects it) |

---

## 13. Compatibility

- **Source syntax is additive.** Expression `fn (` is invalid today
  (`expected an expression`, probed); type-position `fn (` is invalid today
  (`expected identifier`, probed); a call on a non-function local is an
  error; a capturing `fn [` is invalid at top level (the method parser
  errors). No valid program changes meaning. The only new restrictions apply
  to constructs that do not exist yet.
- **Function values are second-class**, so they do not change record layout,
  descriptors, global addressing or initialization. `.sym` ABI stays 1 under
  the recommendation; existing records/functions/methods are unchanged.
- **`.sym` is not compatible across format 4.** A 0.7/format-3 compiler
  rejects a format-4 file (`has format 4; this compiler reads 3`) and a
  0.8/format-4 compiler rejects a format-3 file (`has format 3; this compiler
  reads 4`). Rebuild, do not reuse stale artifacts. Stage B keeps format 4
  and changes only the compiler version (a 0.8 compiler rejects a 0.9 file
  with the compiler message).
- **`--check`, `--sym-dir`, `--emit-sym`, `--reemit-sym`** keep their
  semantics; `--check` reconstructs function types, compiles (and discards)
  lambda bodies in the checked source, and accepts a source-less dependency
  with a fn-typed signature. `--deps` is unchanged.
- **The compiler and seed:** the previous seed builds the new compiler because
  the compiler source uses no new syntax; the fixed point is stage2 ==
  stage3. The feature commit does not touch `seed/obo`; one promote per stage
  happens on main after review.
- **`--version`** changes to `obo 0.8 sym-format 4 abi 1` (Stage A) and
  `obo 0.9 sym-format 4 abi 1` (Stage B); README/LANGUAGE/tests/goldens are
  updated.
- **The golden fixtures are regenerated explicitly**, not silently: the
  committed headers change and the golden tree gains a fn-typed parameter
  and alias. Headers are outside the interface hash; the golden bodies'
  hashes change with the new lines.
- **The tools and library are untouched** in this phase; their differential
  outputs cannot change. The adoption branch is separate.

---

## 14. Decisions, open questions, scope exclusions and extension points

### 14.1 Decisions recorded for the implementation

1. **Lambda syntax:** `fn [captures](params): Ret { body }`; no capture list
   for a non-capturing lambda; no new tokens; explicit capture lists.
2. **Function type syntax:** `fn(params): Ret`, owned or read-only `&`
   parameters, no `mut`, structural identity, aliases transparent.
3. **Second-class values:** parameters/locals/arguments only; no
   fields/arrays/globals/constants/returns/`Result`/optionals/handles/
   references/interfaces; no named function values and no method values.
4. **Representation:** one-word cell pointer; read-only cell for a
   non-capturing lambda; frame cell `[code][captures...]` for a capturing
   lambda; a hidden leading env parameter for every lambda body.
5. **Capture rules:** explicit list; read-only borrows for owned values
   (whole local object/word) and references (the reference's binding); value
   snapshots for scalars; value copies for handles and function values;
   strings/interfaces rejected; no `mut`/owned captures in v1.
6. **Scope:** a capturing lambda literal is only a direct call argument; a
   non-capturing lambda is a full second-class value. No lambda literal in a
   while condition or defer body, and none at top level.
7. **Effects/purity:** every function value is pure (the lambda body is
   checked); an indirect call is a pure call, no devirtualisation; a lambda
   may not call an active enclosing function. Impure callbacks are out in
   v1.
8. **Symbols:** function types in parameters/aliases only; no addresses or
   lambda bodies; `obosym 4`/compiler `0.8` (Stage A) and `0.9` (Stage B),
   ABI `1`; all fixtures migrate.
9. **Round 1:** no lambda/function-type syntax in `obo.obo`, the library or
   the tools; adoption is a separate branch after promotion.
10. **Stage A and Stage B are separate merges/promotes**; Stage B is not
    implemented on the Stage A branch before its promote.

### 14.2 Genuine user-level questions (with recommended defaults)

1. **Capture ergonomics.** The recommended Stage B is explicit, read-only,
   call-argument-only. Is that enough for the intended sorting/tool work, or
   does the user want implicit captures, mutable captures, owned captures,
   or capturing closures as locals in the first cut? **Default:** keep the
   conservative rule; add `mut`/owned captures only with a second design and
   the destructor story. This is the one substantive fork; it changes the
   language surface, not just the implementation.
2. **Named function values.** `sort(xs, compare)` with a top-level function
   is not supported; the v1 spelling is a lambda wrapper
   `sort(xs, fn (a: int, b: int): int { return compare(a, b) })`. Is the
   wrapper acceptable, or does the user want named functions as values? A
   named function has no hidden env parameter, so the extension needs a
   thunk/cell convention. **Default:** no named function values in v1.
3. **Impure/stateful callbacks.** Every function value is pure, so a callback
   cannot free a global or free owned state through a captured handle; it can append to
   a global array, write scalar globals and return owned values, which covers
   comparators, predicates and collectors. A lambda that calls a
   forward-headed function is rejected too (its summary is not final); move
   the callee above the lambda. Is that sufficient for the tool refactor, or
   does the user need a genuinely impure callback (an effect type, or a
   `pure`/impure qualifier with conservative call effects)? **Default:** pure
   callbacks in v1; an impure callback form is a later extension.
4. **Strings and interface captures.** Both are rejected. A user can capture
   a record containing a string, or pass the string as a callback parameter.
   **Default:** reject in v1; strings need a reference-count + call-cleanup
   story, interfaces should settle first.
5. **ABI number.** The recommendation is `SYM_ABI 1` because the
   function-value convention is internal and format-gated. If the user/review
   prefers a conservative ABI bump, use 2 and migrate the ABI fixture.
   **Default:** 1.
6. **Stage split.** Implementing Stage A first, promoting, then Stage B is
   recommended. A single 0.8 merge of both is possible but larger and makes
   the capture-lifetime review and the fixture migration land together.
   **Default:** two stages.

### 14.3 Scope exclusions and extension points

- **Owned/escaping closures, function values in records/arrays/globals/
  returns/`Result`.** Excluded with the second-class rule. Extension: an
  owned function value (a heap cell with a destructor, or a fat two-word
  value), an environment ownership story, and a `move`/owned capture; then
  function-typed fields/returns become possible.
- **Named function values and method values.** Extension: a compiler-
  generated thunk lambda whose hidden body ignores env and calls the named
  function/method; for a method value, the thunk captures the receiver (and
  a `mut` receiver needs a mut capture). The thunk is subject to the same
  purity check, so only a pure named function/method can become a value in
  v1 (or the impure-callback extension must land first).
- **`mut` and owned captures.** Extension: `fn [mut x]` with a writable
  hidden binding, the existing pfree/pf events at the call site, and the
  same argument checks; owned captures need the call-scoped destructor
  (section 4.7).
- **String/interface captures.** Extension: a string capture with an
  increment at creation and a decrement after the call (including the `try`
  error path); an interface capture once the interface cell/token
  representation is settled.
- **Function types in interfaces, interface values as callbacks, interfaces
  of function types.** Extension after both features settle; the interface
  member grammar and impl matching would need a function-type expression. A
  pure interface member could call a function value and stay pure; an
  interface value as a callback would need the interface cell/token
  representation to be capturable.
- **Impure/effectful callbacks.** Extension: a `pure`/impure qualifier on
  function types (reusing the interfaces `pure` word) or a named effect
  type; a pure value keeps the pure call path, an impure value uses the
  conservative `freesAll`/`writesHandles` summary and refuses deep/handle/
  global references. This is the extension if the pure-callback rule proves
  too restrictive for tools.
- **Generic callbacks, variance, subtyping.** Out; no generics.
- **Devirtualisation/inlining.** A later optimisation may specialise a
  call whose target is statically evident (a `let` from one lambda with no
  re-assignment); it must not change the surface or the purity invariant.
- **Concurrency, threads, debug info, dynamic loading.** Out; the closures
  are call-scoped and single-threaded.
- **Adopting lambdas in the compiler/library/tools/eza.** A separate branch
  after promotion, differential outputs unchanged.

---

## 15. Appendix: worked details

### 15.1 A `.sym` fragment

```text
obosym 4
abi 1
compiler "0.8"
source 0x0123456789abcdef
dep io "io.obx"
interface 0xfedcba9876543210
module lib
depiface io 0x1111111111111111
type Item uid 0x3333333333333333
type Item = ref record { name: string; n: int }
type Cmp = fn(&Item, &Item): int
fn apply(p0: int, p1: fn(int): int): int
fn (p0: &Item) compare(p1: &Item): int
fn sortI(p0: &[]Item, p1: fn(&Item, &Item): int)
```

`sortI` may be written with the alias `Cmp` in source, but the writer emits
the underlying type, so the `.sym` line is the function type shown; the alias
line `type Cmp = fn(...)` is emitted separately because the alias is exported.
`compare` is a read-only method; a function-typed parameter is mode 0. No
lambda body, purity marker, code address, cell word or capture appears.

### 15.2 Call lowering

`let f = fn (x: int): int { return x + 1 }; println(f(2))`:

1. `factor` sees `T_FN`; `lambdaExpr` parses `(x: int): int`, builds
   `fn(int): int`, creates the hidden symbol and emits:
   `jmp over; <hidden prologue; body; ret>; over:` then a rodata cell
   `[CODE_BASE + entry]`. The `let` stores the cell address in `f`.
2. `f(2)`: `designatorT` reads `f` (a mem item of type `fn(int): int`); the
   current token is `(` and `x.typ.kind == "fn"`, so it is an indirect call.
   The tail records the local read and returns `callValue`.
3. `callValue` stores the cell pointer in a frame temp; pads for
   `nvis + 1 = 2` words; pushes the cell; parses `2` against the function
   type (owned int, `consume` no-op, push); emits
   `call qword ptr [cell]`; cleans up; returns the int result.
4. `afterCall` on the pure synthetic callee does nothing: no
   `freesAll`/`writesHandles`, no deep/handle/global invalidation, no
   `userCalls`. The lambda body was checked for purity before the call.

`fn apply(x: int, f: fn(int): int): int { return f(x) }`:

1. The parameter `x` is an ordinary owned int; `f` is a one-word
   function-typed parameter, mode 0, not owned.
2. Inside the body, `f(x)` is an indirect call; the cell pointer is loaded
   from `f`'s slot, the env is that cell pointer, `x` is pushed as the
   visible argument, and the result is returned.
3. The direct call `apply(3, lambda)` from main places 3 and the lambda cell
   as the two visible arguments; the lambda is a rodata cell address.

`sortIndices(&order, items, fn (a: &Item, b: &Item): int { return a.n - b.n })`:

1. `items` is an owned array local or a `&[]Item` reference; the parameter is
   `&[]Item`, so `borrowBind` borrows it; `&order` is a borrow of the index
   array.
2. The lambda is a non-capturing `fn(&Item,&Item): int`; its cell is rodata;
   the argument push is one word.
3. Inside `sortIndices`, `cmp(&items[order[j]], &items[v])` is an indirect
   call; the two `&` arguments are borrowed element places; the call is pure,
   so the deep element references are accepted and stay valid; the helper's
   own summary stays pure (it writes/scans the index array scalars, which
   `pureFn` allows).
4. A captured `opts` record adds a frame cell with word 0 the code and word 1
   the `opts` pointer; the helper still sees one word and calls it the same
   way; the main compiler checked that `opts` is a whole-object, read-only
   capture and held it across the later arguments. A deep capture (a
   reference into `items`) is also accepted because the helper is pure.

### 15.3 Compile-state checklist for the island

Before the body: save at least the groups in 5.3.1 (homes/registers,
frame/code, scopes, defers, loop/free logs, guards, names/uses, function
state, arena marks). Replace the function-level arrays with fresh ones; set
`curFn` to the hidden symbol; set `homes = false`; reset `depth = frame = 0`,
`trySite = tryNest = 0`, `condDepth = 0`, `pinned = nil`, `inDefer = false`,
`noreturn = false`; invalidate the peephole.

During the body: parse the header, declare the env/visible/capture symbols,
open the root and body blocks, compile `statSeq`, enforce the return-path
rule, close blocks, run the hidden defers, emit the epilogue and patch the
hidden frame, drop a zero-frame entry.

After the body: restore every saved group; check the hidden function's
purity and fix the `jmp`; truncate `argTemps`/`farCalls` to their marks;
truncate `symArena`/`fnArena` only after the purity check if desired, and
**not** `bindArena` before the literal's capture binding is built (build the
binding after the truncation, or leave `bindArena` to the enclosing
function's end); restore the outer arrays; return the hidden entry and the
capture list to the cell/code emission step.

### 15.4 Review checklist for the implementation

- [ ] No new token/keyword/scanner rule; `--tokens` and `port_test.py` pass
      unchanged.
- [ ] Every function-type restriction has an exact error test in source and
      in the `.sym` reader: return, field, array, optional, Result, global,
      constant, handle, borrow, receiver, interface member.
- [ ] The lambda parser rejects `mut` parameters, an empty capture list,
      duplicate captures, a capture that is also a parameter, and a function
      return; a non-void body must return on every path.
- [ ] The three `fn` contexts are distinct; a top-level lambda is not
      silently parsed as a method with a confusing success.
- [ ] The island saves/restores every function-level global; a missed field
      is caught by a run/error test with a lambda inside a complex
      expression, not just a simple statement.
- [ ] A lambda literal in a while condition or defer body is rejected before
      code emission; a call through a function-typed local in a while
      condition is accepted and lifted safely (no rel32).
- [ ] The hidden env is pushed before the visible arguments; padding uses
      `nvis + 1`; the cell temp survives the arguments; `call qword ptr
      [cell]` is encoded and objdump-checked; cleanup/alignment is correct.
- [ ] A zero-frame hidden body's `dropFrame` entry move is reflected in the
      cell; a non-capturing cell stores `CODE_BASE + final entry`.
- [ ] Indirect call results are owned/void/string correctly; an owned or
      string result leaks/double-frees nowhere.
- [ ] Every lambda body is checked for purity; an impure body (frees a
      global, frees through a handle, calls an impure/forward-headed function) is
      rejected. The indirect call's synthetic callee is pure, so the caller's
      deep/handle/global references stay valid; the comparator helper can
      pass `&items[i]`.
- [ ] Function values are rejected in every storage/escape position and
      accepted in parameters/locals/arguments; a function-typed parameter in
      a method and a function-type alias in a module round-trip.
- [ ] Capture lists resolve only enclosing locals/parameters; an uncaptured
      enclosing local is a specific error; globals/functions are used
      directly; scalar snapshots and whole-object read-only captures behave
      as documented.
- [ ] A capturing literal outside a call argument is rejected; one in a
      let/assignment/return/while/defer/top level has the exact message.
- [ ] A capture binding is held across later arguments, appears in the
      mut-alias/global/handle/deep checks, and a later move/free is refused;
      a whole-object or deep capture is accepted by a pure comparator helper;
      an impure callee refuses a deep/handle/global capture.
- [ ] Owned/reference captures cannot be written/moved/freed; a mut method
      call through a capture is refused. A handle capture allows reads and
      scalar writes; a write that frees owned state through it makes the
      lambda impure and is rejected.
- [ ] A lambda body cannot call an active enclosing function; a call to an
      already-defined pure function that calls the value is allowed and pure.
- [ ] `.sym` round-trip/reemit/golden with a fn-typed parameter and alias;
      all committed fixtures migrated; positive source-less checks exit 0;
      version/format/ABI fixtures and `sym_fuzz` updated.
- [ ] `closureCheck` recurses into function types; a pub signature with a
      private type inside a callback is refused.
- [ ] The compiler source uses no lambda/function-type syntax in round 1;
      the previous seed builds it; fixed point stage2 == stage3; peak within
      budget; no seed change in the feature commit.
- [ ] No existing assertion deleted; stale mutant patterns retargeted, not
      removed; mode-0-only/context-flag mutants recorded equivalent with the
      reason; the tools/library/eza outputs unchanged.
