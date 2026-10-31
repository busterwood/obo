# Typed template literals: SQL values and escaped HTML

**Date:** 2026-10-31
**Status:** design proposal, not approved and not implemented. No source, test,
runtime, seed, version or format changes in this pass.
**Baseline:** `main` at `c6c4d3f3ce18f3aa872e4ba0880ccdac7e7257de`; isolated
worktree `/home/chris/.local/share/opencode/worktree/fa36f3/template-literals-design`,
branch `template-literals-design`; compiler 13,550 lines / 509,773 bytes.
Released identity: `obo 0.10`, sym-format 6, ABI 1, 2,500,000-byte budget.
**Evidence:** `/tmp/opencode/template-design-20261031/` (manifest/citations, no
copied articles). Examples are proposals, not compiled examples.

## 0. Recommendation

Add compiler-known `sql` and `html` template tags, plus literal-only trusted
forms `sqlRaw` and `htmlRaw`. A tag is not a user-defined variadic function; the
compiler classifies each hole:

- SQL: scalar -> bound parameter; `Sql` -> composable fragment; `string` never
  becomes SQL text.
- HTML: scalar -> escaped in context; `Html` -> trusted markup, inserted raw.
- Arrays: `[]Sql` joined explicitly (`sqlJoin`, `sqlAnd`, `sqlOr`); `[]Html`
  concatenates. Neither is guessed.
- Raw SQL/HTML requires an explicit literal tag. No `string -> Sql/Html` casts;
  no cross-domain `SafeString`.

This borrows JavaScript's syntax, not its dynamic tag protocol. Obo has no
variadic functions or user-defined generic tuples, so a JS-style tag needs a new
language feature. Compiler-known tags give the compiler the chunks, hole types
and HTML context that a JS library sees only at runtime.

## 1. Scope and threat model

**Goals:** values cannot become SQL syntax; SQL structure composes from typed
fragments; HTML data cannot become markup; trusted markup is explicit; optional
predicates and lists stay readable.

**Non-goals:** DB driver, SQL parser/schema, dialect quoting, full HTML
sanitizer, dynamic tag/attribute names, CSP, JS/CSS encoding, user-defined tags,
template files, or arbitrary `string -> Sql`/`Html`.

**Threat model:** the template author is trusted; request, file, service and
database data are not. SQL values are data; identifiers, sort directions and
keywords are structure and need fixed code or an allow-list. HTML text,
attributes, URLs, script and CSS are distinct contexts. Raw strings are code.
Stored data is still untrusted when it reaches a query or page (second-order
injection). A DB syntax error is a query bug, not an injection escape.

## 2. SQL: values and fragments

```obo
let q = sql`SELECT * FROM account WHERE id = ${id} AND name = ${name}`
```

`id`/`name` become parameters: PostgreSQL renders `id = $1 AND name = $2` with
values `[id, name]`. No SQL quoting or value text is involved.

Composition is type-directed:

```obo
let extra = sqlRaw`WHERE x = y`       // literal only; no holes
let base = sql`SELECT * FROM t`
let q = sql`${base} ${extra}`
```

A `Sql` hole is structure; a `string` hole is always a bind, even if it contains
`WHERE x = y`. `sqlRaw` takes only a literal with no holes, so it cannot consume
request data. Dynamic identifiers/sort directions need a future allow-list API,
never plain strings.

Optional predicates are accumulated, not spliced. `sqlWhere` returns empty for
an empty array, otherwise `WHERE (p AND ...)`, parenthesising predicates so an
inner `OR` cannot capture neighbours. Lists are explicit:

```obo
var where = new []Sql(0)
if name != "" { append(where, sql`name = ${name}`) }
if onlyActive { append(where, sql`active = ${true}`) }
var states = new []Sql(0)
for s in selectedStates { append(states, sql`${s}`) }
if len(states) > 0 {
    append(where, sql`state IN (${sqlJoin(states, sqlRaw`, `)})`)
}
let q = sql`SELECT * FROM account ${sqlWhere(where)}`
```

`[]string` could mean one array-valued parameter or an expanded list, so the
template does not guess; there is no implicit array expansion. An empty `IN` list
must omit the predicate or use a future `= ANY`; `IN ()` is invalid.

**Invalid examples.** `status IS $1` and `active IN FALSE` are invalid SQL and
are not design targets: `IS` expects `NULL`/`TRUE`/`FALSE` or a defined test, and
`IN` expects a list or subquery. Use `x = ${v}` for non-null comparisons and
explicit structure or a future null-aware predicate for null tests. The template
does not validate SQL syntax.

## 3. HTML: context and trust

```obo
let card = html`<div class="card">
  <h2>${title}</h2>
  <a href="/users/${id}" title="${name}">${name}</a>
  <div class="body">${body}</div>
</div>`

let body = html`<p>Hello, <strong>${userName}</strong></p>`
let page = html`<article>${body}</article>`

var rows = new []Html(0)
for u in users { append(rows, html`<li>${u.name}</li>`) }
let list = html`<ul>${rows}</ul>`
```

`title`, `name` and `id` are escaped by context. `body` is `Html` and is inserted
verbatim; a `string` would be escaped. Nested fragments are not escaped twice;
arrays of `Html` concatenate.

V1 contexts:

| context | treatment |
|---|---|
| text content | entity-encode `& < > " '` |
| quoted ordinary attribute | attribute-encode, including the quote |
| URL attribute (`href`, `src`, ...) | scheme allow-list plus URL/attribute encoding; relative allowed |
| unquoted attribute, tag/attribute name | rejected; use literal quoted structure |
| comments, `<script>`, `<style>` | rejected; JS/CSS need typed encoders, not HTML escaping |

`Html` is trusted **content** and is rejected in attributes. `HtmlAttrs` is a
separate trusted attribute-list type for optional attributes; `htmlRaw` is the
only literal raw markup and has no holes. A future `htmlUnsafe(string)` may
exist for trusted sanitizer output, visibly named and never the default. `data:`
URLs need an explicit policy. URL component encoding then attribute encoding
follows OWASP; HTML escaping alone does not stop `javascript:`.

HTML parsing is local to the literal: a state machine tracks data, tag,
attribute, quoted value, comment and raw-text/RCDATA. Holes in
script/style/comment/tag/attribute-name are errors. A trusted `Html` hole is
allowed only in data context; its author owns balance and context, since trusted
fragments may intentionally open elements. This is narrower than Go
`html/template`, but avoids whole-template analysis and does not pretend to
sanitize rich HTML.

## 4. Prior art and why not JS tags

JavaScript tags receive a frozen strings array plus variadic values and may
paste, bind or ignore them. `sql-template-tag` and Slonik use fragments, explicit
`join`, and a trusted `raw`; Slonik permits execution only through its tag.
node-postgres passes text and values separately; Rust `sqlx` separates `push`
from `push_bind`; C# `FormattableString` keeps format plus arguments but leaves
safety to the consumer. TypeScript can type tag shape, not SQL/HTML safety.

HTML prior art separates contexts: Go `html/template` has contextual escaping and
typed `HTML`/`HTMLAttr`/`URL`/`JS`/`CSS`; Razor encodes strings but renders
`IHtmlContent`; Jinja/MarkupSafe distinguish escaped `Markup`; JSX has
`dangerouslySetInnerHTML`; Svelte has `{@html}`; Askama has `| safe`. The lesson
is per-context trusted types and an explicit unsafe path.

Obo cannot copy the JS protocol: fixed arity, homogeneous arrays, and interfaces
cannot hold scalars (LANGUAGE §5a). Variadic tags need tuples/generics and a new
calling convention. Compiler-known tags keep the syntax without that.

## 5. Architecture options

**A. compiler-known tags (recommended):** scanner/parser/type checker know four
tags and the emitter builds typed values. **B. builder API only:** implementable
with records/methods, but verbose and the caller classifies every hole/context;
a fallback and implementation substrate. **C. general tag protocol:** needs
variadic heterogeneous values, tuples, escape policy and a user-tag trust
boundary; defer. **D. hygienic macros/AST:** would generate all of it, but there
is no AST and it is overkill.

## 6. Types and representation

Exact placement (compiler prelude vs stdlib) is an implementation decision; the
semantics are the proposal:

```obo
type Sql = ref record { parts: []string; args: []SqlArg }
type SqlArg = closed {
    Null { }  Int { v: int }  Bool { v: bool }
    Float { v: float }  Str { v: string }  Bytes { v: []byte }
    // Decimal { v: decimal } when decimal exists
}
type Html = ref record { text: string }       // escaped/trusted content
type HtmlAttr = ref record { text: string }   // one complete attribute
type HtmlAttrs = ref record { text: string }  // trusted attribute list
```

`Sql` alternates text and arguments, not text with placeholders. Joining A and B
merges A's last text with B's first and appends args; rendering emits `$1..$N` in
traversal order, with no regex scan or renumbering. `SqlArg` is a closed union
because it is the only existing way to hold a heterogeneous scalar list:
interfaces cannot hold scalars and generic arrays do not exist. Each parameter
allocates a small owned variant; `Str`/`Bytes` own payloads. That is feasible but
costs allocations and needs a byte-array copy/move rule; a packed form can come
later. `int`/`byte` share `Int`; `float` binds as binary64, never formatted into
SQL; `decimal` is reserved for the future exact type. `Html` stores escaped text;
`HtmlAttr`/`HtmlAttrs` are separate because markup is not an attribute fragment.
There is no `SafeString` or cross-domain conversion: trust is grammar-specific.

## 7. Grammar, lowering and cost

```text
TagExpr := TAG "`" (Chunk | Hole)* "`"
TAG     := "sql" | "sqlRaw" | "html" | "htmlRaw"
Hole    := "${" Expr "}"
Chunk   := raw source text, with \` and \${ escapes
```

Tag names are contextual: `sql` immediately followed by a backtick is a tag;
`sql` elsewhere remains an identifier, and `sql::join` remains a normal qualified
call. `sqlRaw`/`htmlRaw` reject holes. Chunks are raw, not C-escaped, so SQL/HTML
backslashes and newlines survive; only `` \` `` and `\${` escape.

Compiler anchors: `scanString`/`next` (scanner), `factor`/`designator`
(expressions), `isOwned`/`assignable` (checks), `newLocal`/`callFn` (emission).
The scanner uses a template-mode stack: a backtick enters chunk mode; `${` emits
the chunk, saves position, scans the nested expression, restores and resumes.
Semicolon insertion is off in chunks; a nested template pushes another mode. This
is still one read and one pass, with no AST.

Lowering `sql` emits a hidden builder local: append text; append a `SqlArg` per
scalar; append a `Sql` hole's parts/args; finish and return the owned `Sql`.
`html` builds an `Html` string using the state at each hole. Hidden builders and
temporaries are owned locals, freed on every block exit, preserving
left-to-right evaluation and existing argument/borrow checks. Runtime-valued tags
are not global initializers; literal raw tags may become constants later. Errors
point at the hole.

This costs a scanner stack, a local HTML state machine, new type checks and
builder emission, plus runtime allocations. The compiler has about 470 KB of
headroom, but implementation must measure against 2,500,000. There is no SQL
parser, HTML sanitizer or AST, so the work is bounded.

## 8. Safety, escaping and ownership

- **SQL values are not escaped.** They are sent separately; quotes, `$1`, `?`
  and NUL remain data (NUL/binary is a driver concern). `sqlRaw` is syntax, not
  a sanitizer; identifiers and `ORDER BY` need allow-lists.
- **No accidental raw.** A `string` hole is always a bind; only `sqlRaw`/
  `htmlRaw` literals are raw. V1 rejects `$` followed by digits in `sqlRaw`
  because the renderer reserves `$n`; `$tag$` quoting remains.
- **No double escape.** Strings are escaped once; `Html` is trusted and inserted
  once. `Html` in an attribute or `HtmlAttrs` in data is a compile error.
- **Structure stays literal.** Tag/attribute names cannot be holes; optional
  attributes come from `HtmlAttrs`/`HtmlAttr`, not string-built `name="value"`.
  URLs use scheme validation plus component encoding, not HTML escaping alone.
- **Trusted fragments can change structure.** `Html` is inserted verbatim and may
  open/close elements; the author owns balance/context. V1 does not re-parse it;
  sanitizing untrusted rich HTML is separate.
- **Ownership.** Inserting a fragment consumes it (or needs an explicit copy);
  reuse needs two builds. `SqlArg.Bytes` must not silently move a caller's array:
  require `sqlBytes` copy or document the move. The query owns strings/bytes
  until execution; a driver borrows them for the call. `Result` errors do not
  change this. No DB connection/schema/service is part of this design.

## 9. Database values and interop

V1 holes: `int`, `byte`, `bool`, `string`, `float`, `[]byte`, plus `SqlArg` for
explicit nulls. `sqlNull()` is distinct from an empty fragment: omit a predicate
by not appending it; bind NULL by inserting `sqlNull()`. Untyped NULL works where
the server infers type; `SELECT $1` may need `CAST($1 AS ...)`. Optional `?T` is
not auto-encoded in v1; match and choose `sqlNull()` or a value. Typed nulls are
open.

When `decimal` lands, bind it as exact `SqlArg.Decimal` (or a lossless adapter
form), never float or pre-rounded text. Float parameters stay binary64 and are
not money. Byte parameters preserve length and embedded NUL. `Sql` is
dialect-neutral: an adapter renders text and values separately, PostgreSQL's
extended protocol being the reference. Future in-process C adapters can consume
that pair; no helper process is allowed.

## 10. Alternatives and unresolved decisions

Rejected: ordinary string interpolation; one cross-domain `SafeString`; JS-style
user tags; automatic array expansion; SQL quoting/escaping instead of binding;
identifiers as parameters; HTML sanitizer as default escaping; and full
cross-template context propagation (the no-AST compiler cannot do it in v1).

Decisions for the user:

1. **Delimiter:** backticks + `${}` (recommended) or a quoted tagged form?
2. **Type ownership:** compiler-predeclared `Sql`/`Html` (recommended for
   opacity) or ordinary exported records (public fields allow forging)?
3. **Reuse:** consume fragments on composition (recommended) or deep-copy?
4. **Lists:** explicit `[]Sql` joins (recommended) or compiler array expansion?
5. **NULL:** untyped `sqlNull()` + casts (recommended v1) or typed nulls?
6. **HTML scope:** text + quoted attrs + URL validation (recommended v1), or
   typed JS/CSS/attribute builders immediately?
7. **Raw:** literal-only tags (recommended) or a runtime `unsafe` constructor?
8. **Identifiers:** defer entirely, or add allow-list `sqlIdentifier` with
   vendor quoting?
9. **Naming:** reserve `sql`, `html`, `Sql`, `Html` globally (recommended) or
   use qualified stdlib modules?
10. **Schedule/version:** after planned `0.11` Stage B, or separate next
    feature? Needs the user's roadmap decision.

## 11. Compatibility, acceptance and roadmap

The syntax is additive: a backtick is currently unexpected, and `sql` followed by
a backtick is invalid today. Existing programs do not change meaning. Ordinary
record types need no `.sym`/ABI change; compiler-built-in types would need a
symbol/version design. No seed promotion, format or ABI change is proposed; no
version is reserved. Scheduling must respect "one compiler feature in flight".

Future acceptance:

- **SQL:** binds, raw fragments, nesting, joins, `[]Sql`, empty lists, duplicate
  and mixed values, deterministic `$n` numbering; corpus covers quotes, `$1`,
  `?`, NUL and Unicode; raw with a hole is an error.
- **HTML:** text/attribute/URL/script/style/comment/tag/attribute-name matrix;
  nested and array `Html`; empty arrays; double escape; XSS corpus stays text.
- **Ownership:** leak/double-free tests for builders, args, fragments, loops and
  early exits; consume-once; byte copy/move; no use-after-free through DB calls.
- **Compiler:** exact error line/column; `.sym` round-trip if exported; fixed
  point, suites and 2,500,000-byte budget unchanged; no float arithmetic in
  escape paths (integer scans); escape benchmarks.
- **Scope:** no DB connection, schema, service, install or tool refactor.

Roadmap: (1) scanner/syntax and raw tags; (2) SQL builder, `SqlArg`, binds,
nested fragments, joins; (3) HTML text/attrs, nested/array fragments, state
machine; (4) URL validation, `HtmlAttrs`, NULL/bytes rules; (5) adapters later.
User-defined tags need a separate variadic/tuple design.

## 12. References

External sources consulted 2026-10-31 (full URLs below; no article copies):

- ECMA-262 template literals/`GetTemplateObject`:
  https://tc39.es/ecma262/multipage/ecmascript-language-expressions.html#sec-template-literals
- MDN template literals:
  https://developer.mozilla.org/en-US/docs/Web/JavaScript/Reference/Template_literals
- TypeScript template literal types:
  https://www.typescriptlang.org/docs/handbook/2/template-literal-types.html
- node-postgres queries:
  https://node-postgres.com/features/queries
- `sql-template-tag`:
  https://github.com/blakeembrey/sql-template-tag
- Slonik:
  https://github.com/gajus/slonik#sql-fragments
- Rust `sqlx` `QueryBuilder`:
  https://docs.rs/sqlx/latest/sqlx/struct.QueryBuilder.html
- .NET `FormattableString`:
  https://learn.microsoft.com/en-us/dotnet/api/system.formattablestring?view=net-9.0
- PostgreSQL extended query protocol:
  https://www.postgresql.org/docs/current/protocol-flow.html#PROTOCOL-FLOW-EXT-QUERY
- PostgreSQL lexical structure:
  https://www.postgresql.org/docs/current/sql-syntax-lexical.html
- OWASP SQL injection prevention:
  https://cheatsheetseries.owasp.org/cheatsheets/SQL_Injection_Prevention_Cheat_Sheet.html
- OWASP XSS prevention:
  https://cheatsheetseries.owasp.org/cheatsheets/Cross_Site_Scripting_Prevention_Cheat_Sheet.html
- Go `html/template`:
  https://pkg.go.dev/html/template
- Jinja autoescaping:
  https://jinja.palletsprojects.com/en/stable/api/#autoescaping
- MarkupSafe:
  https://markupsafe.palletsprojects.com/en/stable/
- Askama syntax:
  https://askama.readthedocs.io/en/stable/template_syntax.html
- ASP.NET Core Razor:
  https://learn.microsoft.com/en-us/aspnet/core/mvc/views/razor?view=aspnetcore-9.0
- React `dangerouslySetInnerHTML`:
  https://react.dev/reference/react-dom/components/common#dangerously-setting-the-inner-html
- Svelte basic markup:
  https://svelte.dev/docs/svelte/basic-markup
- SQLite host-parameter limits:
  https://www.sqlite.org/limits.html#max_variable_number
