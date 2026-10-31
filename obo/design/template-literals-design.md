# Template domains: typed SQL and context-escaped HTML

**Date:** 2026-10-31
**Status:** **APPROVED DESIGN DECISIONS 2026-10-31.** The five decisions in §13
are approved; syntax/API details remain proposals; not implemented. The
implementation slot is the next compiler feature after Stage B `0.11` ships.
No source, test, runtime, seed, version or format changes in this pass.
**Revision 3 (final decisions):** records the approved choices. SQL semantics and
examples are unchanged. HTML remains a separate domain instance with
contextual escaping, not parameter binding.
**Baseline:** `main` at `c6c4d3f3ce18f3aa872e4ba0880ccdac7e7257de`; isolated
worktree `/home/chris/.local/share/opencode/worktree/fa36f3/template-literals-design`,
branch `template-literals-design`; compiler 13,550 lines / 509,773 bytes.
Released identity: `obo 0.10`, sym-format 6, ABI 1, 2,500,000-byte budget.
**Evidence:** `/tmp/opencode/template-design-20261031/` (manifest/citations, no
copied articles). Examples are proposals, not compiled examples.

## 0. Recommendation

Add built-in compiler-known `sql` and `html` template tags, plus literal-only
raw forms `sqlRaw` and `htmlRaw`. They are two **instances of one template-domain
mechanism**, not one generic escape: each has a nominal fragment type, an
`ENCODE` policy for values, `SPLICE` for claimed-safe fragments, array semantics
and context rules.

- SQL: scalar -> bound parameter; `Sql` -> fragment; `string` never SQL text.
- HTML: scalar -> context-escaped; `Html` -> claimed-safe markup, inserted raw.
- Arrays: `[]Sql` joined explicitly; `[]Html` concatenates. Neither is guessed.
- Raw SQL/HTML needs an explicit literal tag; no `string -> Sql/Html` cast and
  no cross-domain `SafeString`.

This borrows JavaScript's syntax, not its dynamic tag protocol. Obo lacks
variadic/user-generic tuples, so a JS-style tag would need a new language
feature. Compiler-known tags give the compiler the chunks, hole types and HTML
context a JS library sees only at runtime. The domain descriptor is internal;
public tags stay separate in v1.

## 1. Scope and threat model

**Goals:** values cannot become SQL syntax; SQL structure composes from typed
fragments; HTML data cannot become markup; trusted markup is explicit; optional
predicates and lists stay readable.

**Non-goals:** DB driver, SQL parser/schema, dialect quoting, full HTML
sanitizer, dynamic tag/attribute names, CSP, JS/CSS encoding, user-defined
tags/domains, template files, or arbitrary `string -> Sql`/`Html`.

**Threat model:** the template author is trusted; request, file, service and
database data are not. SQL values are data; identifiers, sort directions and
keywords are structure and need fixed code or an allow-list. HTML text,
attributes, URLs, script and CSS are distinct contexts. Raw strings are code.
Stored data is still untrusted when it reaches a query or page (second-order
injection). A DB syntax error is a query bug, not an injection escape.

## 2. The shared abstraction: template domains

SQL and HTML are two instances of one semantic pattern, a **template domain**.
A domain is the compiler-known policy for one output language. It defines:

1. the nominal **fragment type** (`Sql`, `Html`) and its documented
   constructors (the tag and the literal-only raw tag); it is an ordinary
   standard-library record with public fields, so trust is convention-level;
2. the **value policy** for an ordinary hole: SQL binds a parameter, HTML
   performs the context-specific escape;
3. **splice** rules for a fragment: SQL merges text/parameters, HTML appends
   already-safe markup;
4. **array/join** policy: `[]Sql` is joined explicitly; `[]Html` concatenates;
5. **context/validation** rules: SQL value-vs-structure and identifiers; HTML
   text/attribute/URL and rejected script/style/tag-name contexts;
6. a **once-only trust policy**: encoded data is not treated as a fragment, and
   a fragment is not encoded a second time. This is enforced by the documented
   API and review/lint, not by private fields.

For every hole the domain chooses exactly one action:

```text
ENCODE(value, context)   -> domain-specific encoded representation
SPLICE(trustedFragment)  -> domain-specific composition
```

That is the unification. It is not "turn everything into escaped text": the
encoded representation is different in each domain. A plain `string` is always
`ENCODE`; it is never auto-spliced as SQL syntax or trusted HTML. A fragment is
always `SPLICE`; it is not re-escaped. The compiler rejects a value that is
neither a supported scalar nor that domain's fragment type.

The trust is **documented convention, not type enforcement**. Because the
fragment types are ordinary public-field records, `new Sql{text: ..., params:
...}` and `new Html{text: ...}` are legal direct constructions; they are
equivalent to an explicit raw constructor. Documentation and lint require
construction through the tags/raw constructors. The compiler distinguishes the
types, but it cannot certify provenance.

A conceptual domain descriptor is useful for the compiler, but it is **not a
proposed public Obo interface**:

```text
domain = {
  name, fragmentType, rawTag,
  encode(context, value) -> emission | typeError,
  splice(fragment)     -> emission,
  combine(a, b), join(parts, separator),
  contextState, render
}
```

Obo has no associated types or generic modules, so this is an internal table of
type IDs and lowering functions in v1, not a user-extensible interface. An
implementation may share the fragment-builder skeleton (`texts` plus opaque
hole payloads), but the public values stay nominal and domain-specific. Nominal
means distinct named types, not private constructors. There is deliberately no
`Template`, `Fragment`, `SafeString` or `Escapable` supertype: a common
supertype would invite passing a `Sql` where an `Html` is expected, the classic
confused-deputy failure. A Slack-style mrkdwn fragment or any third output
language would be another domain: safety in one grammar says nothing about
another. `Sql`/`Html` are built-in instances, not the whole universe.

## 3. SQL: values and fragments

In the SQL domain, `ENCODE(value)` appends an argument and a placeholder, not
quoted text; `SPLICE(Sql)` merges text and parameters. The encoded result is a
text/parameter pair, not bytes, which is why SQL is not just another HTML escaper.

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

## 4. HTML: context and trust

In the HTML domain, `ENCODE(value, context)` returns bytes that are safe as text
or as a quoted attribute value; `SPLICE(Html)` appends already-safe markup. There
is no parameter side channel, unlike SQL, and no attribute context is allowed to
accept an `Html` fragment.

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

All v1 context violations are compile-time errors with a diagnostic at the hole;
the exact wording remains a proposal. No fallback escapes a forbidden context.

`Html` is **claimed-safe content** by convention and is rejected in attributes.
`HtmlAttrs` is a separate claimed-safe attribute-list type for optional attributes; `htmlRaw` is the
only literal raw markup and has no holes. A future `htmlUnsafe(string)` may
exist for trusted sanitizer output, visibly named and never the default. `data:`
URLs need an explicit policy. URL component encoding then attribute encoding
follows OWASP; HTML escaping alone does not stop `javascript:`.

HTML parsing is local to the literal: a state machine tracks data, tag,
attribute, quoted value, comment and raw-text/RCDATA. Holes in
script/style/comment/tag/attribute-name are errors. An `Html` hole is allowed
only in data context; its author owns balance and context, since claimed-safe
fragments may intentionally open elements. This is narrower than Go
`html/template`, but avoids whole-template analysis and does not pretend to
sanitize rich HTML.

## 5. The two domains side by side

| aspect | SQL domain | HTML domain |
|---|---|---|
| ordinary value | `ENCODE` -> placeholder plus bound argument; no value text | `ENCODE` -> entity/URL bytes for the current context |
| trusted fragment | `SPLICE(Sql)` -> merge text and parameter lists | `SPLICE(Html)` -> append already-safe markup bytes |
| arrays | `[]Sql` joined explicitly; empty list is the caller's decision | `[]Html` concatenates; empty array is empty output |
| context | value vs syntax; identifiers and sort order are fixed/allow-listed | text vs quoted attribute vs URL; script/style/tag names rejected |
| once-only | a bound value never becomes text; a fragment is trusted once | a value is encoded once; `Html` is never encoded again |
| errors | unsupported hole type; hole in raw literal; `$n` collision | `Html` in attribute; hole in script/style/tag name; unsafe URL |

`HtmlAttrs` is the HTML domain's trusted attribute-list fragment; it is not a
`Sql` fragment and cannot be spliced into SQL. `SqlArg` is the SQL domain's
bound-value representation; it cannot be spliced into HTML.

```obo
let q = sql`SELECT ${someHtml}`   // error: Html is not a SQL value or fragment
let p = html`<p>${someSql}</p>`   // error: Sql is not Html or a scalar
```

## 6. Why escaping and parameterization are different

HTML escaping is a **transformation** of a value into bytes that the HTML parser
will read as text or as a quoted attribute value. SQL parameterization is not a
string transformation at all: the value is removed from the SQL text and sent
beside it, so the SQL parser never sees it. Quoting a value into SQL is a
different, dialect- and configuration-dependent operation and is not equivalent
(and is not safe for identifiers). Parameterization also preserves the typed
value—exact decimal, binary length, NULL—rather than flattening it to text.

Both still fit `ENCODE` because both are domain-owned encodings, but their
codomains differ: SQL produces a placeholder plus a side value; HTML produces
safe bytes. For this reason a public generic `escape(value)` is wrong. SQL
`ENCODE` may need to append to a parameter list and later choose a dialect
placeholder; HTML `ENCODE` needs parser context and URL policy. The shared
abstraction is the typed hole, the trusted fragment, composition and once-only
trust—not one encoder function.

## 7. Prior art and why not JS tags

SQL builders separate data from structure: node-postgres passes text and values
separately; `sql-template-tag`/Slonik compose fragments with explicit `join` and
trusted `raw`; Rust `sqlx` separates `push` from `push_bind`; C#
`FormattableString` keeps format plus arguments but leaves safety to its
consumer. TypeScript can type tag shape, not SQL safety.

HTML prior art separates contexts: Go `html/template` has contextual escaping and
typed `HTML`/`HTMLAttr`/`URL`/`JS`/`CSS`; Razor encodes strings but renders
`IHtmlContent`; Jinja/MarkupSafe distinguish escaped `Markup`; JSX and Svelte
make raw HTML explicit (`dangerouslySetInnerHTML`, `{@html}`); Askama has
`| safe`. The lesson is per-context trusted types, not one global safe string.

Obo cannot copy the JS tag protocol: fixed arity, homogeneous arrays, and
interfaces cannot hold scalars (LANGUAGE §5a). Compiler-known domains avoid
needing variadic tuples while preserving static checks.

## 8. Architecture options

**A. compiler-known domains (recommended):** the scanner/parser know `sql`,
`sqlRaw`, `html` and `htmlRaw`; the emitter uses one internal descriptor per
domain. **B. builder API only:** per-domain records/methods, implementable now
but verbose and caller-classified; a fallback/rejected alternative, not the
primary interface. **C. general tag protocol / user-defined domains:** deferred.
**D. macros/AST:** would generate it, but no AST and overkill.

User-defined domains would need associated fragment/raw types, opaque
constructors, a tag signature binding literal chunks and typed holes, a
compile-time encode/splice/join/context policy and a trust declaration. Obo
lacks associated types, generic modules and variadic heterogeneous tuples, so
this is an internal mechanism in v1, not a public `TemplateDomain` API.

### Separate standard-library tags in v1

The tags remain separate: `sql`/`sqlRaw` and `html`/`htmlRaw`, with separate
helpers and fragment types. There is no generic `template` tag, no
`escape(value)` and no public domain registry. No general user-defined tag
protocol exists in v1, and user-defined tags stay unsupported until the language
supports heterogeneous typed value lists. Choosing the tag is choosing the
grammar and documented trust convention. That is less extensible than
JavaScript tags, and it is what keeps the checks tractable.

### Extension path

After both built-ins pass, factor the shared walker and descriptor without
changing the public tags. Public domains are a later language-feature decision,
gated on heterogeneous typed value lists plus the prerequisites above; until
then user code can use the builder API but cannot register a tag.

## 9. Types and representation

These are ordinary standard-library records with **public fields**, not opaque
builtins. The compiler knows their type IDs/field layout so the tags can lower to
them; that is not a private constructor.

```obo
type Sql = ref record { text: []string; params: []SqlArg }
type SqlArg = closed {
    Null { }  Int { v: int }  Bool { v: bool }
    Float { v: float }  Str { v: string }  Bytes { v: []byte }
    // Decimal { v: decimal } when decimal exists
}
type Html = ref record { text: string }       // claimed-safe content
type HtmlAttr = ref record { text: string }   // one complete attribute
type HtmlAttrs = ref record { text: string }  // claimed-safe attribute list
```

`Sql` alternates text and parameters, not text with placeholders. Joining A and B
merges A's last text with B's first and appends params; rendering emits `$1..$N`
in traversal order, with no regex scan or renumbering. `SqlArg` is a closed union
because it is the only existing way to hold a heterogeneous scalar list:
interfaces cannot hold scalars and generic arrays do not exist. Each parameter
allocates a small owned variant; `Str`/`Bytes` own payloads. That is feasible but
costs allocations and needs a byte-array copy/move rule; a packed form can come
later. `int`/`byte` share `Int`; `float` binds as binary64, never formatted into
SQL; `decimal` is reserved for the future exact type. `Html` stores text that the
calling convention declares safe; `HtmlAttr`/`HtmlAttrs` are separate because
markup is not an attribute fragment.

Direct construction is legal and is an explicit raw operation:
`new Sql{text: ..., params: ...}` and `new Html{text: ...}` are equivalent to
using a raw constructor. Tags and `sqlRaw`/`htmlRaw` are the documented route;
docs/lint enforce that convention. There is no `SafeString` or cross-domain
conversion, and the compiler cannot enforce provenance because the fields are
public.

## 10. Grammar, lowering and cost

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

Lowering is one template walk with a domain hook. The shared walker emits hidden
builder locals, appends literal chunks, evaluates holes left-to-right and frees
temporaries on every block exit. The SQL hook appends text and `SqlArg` values
(or splices `Sql` text/params) and later renders placeholders. The HTML hook
chooses the context escaper, appends bytes, or splices `Html`; it maintains the
local HTML context state. Hidden builders are owned locals, preserving existing
argument/borrow checks. Runtime-valued tags are not global initializers; literal
raw tags may become constants later. Errors point at the hole.

This costs a scanner stack, an internal domain dispatch, an HTML state machine,
new type checks and builder emission, plus runtime allocations. The compiler has
about 470 KB of headroom, but implementation must measure against 2,500,000.
There is no SQL parser, HTML sanitizer or AST, so the work is bounded.

## 11. Safety, escaping and ownership

- **SQL values are not escaped.** They are sent separately; quotes, `$1`, `?`
  and NUL remain data (NUL/binary is a driver concern). `sqlRaw` is syntax, not
  a sanitizer; identifiers and `ORDER BY` need allow-lists.
- **No accidental raw.** A `string` hole is always a bind or escape; only
  `sqlRaw`/`htmlRaw` literals are documented raw constructors. V1 rejects `$`
  followed by digits in `sqlRaw` because the renderer reserves `$n`; `$tag$`
  quoting remains. Because the records have public fields, direct construction
  is legal and equivalent to raw; tags/raw are a convention backed by docs/lint,
  not type enforcement.
- **No double escape.** Strings are escaped once; `Html` is claimed-safe and
  inserted once. `Html` in an attribute or `HtmlAttrs` in data is a compile
  error. These are type-level checks, not provenance checks.
- **No cross-domain splice.** Only `Sql` may be spliced into SQL and only `Html`
  into HTML. There is no `SafeString` coercion; a domain's trust is not portable.
- **Structure stays literal.** Tag/attribute names cannot be holes; optional
  attributes come from `HtmlAttrs`/`HtmlAttr`, not string-built `name="value"`.
  URLs use scheme validation plus component encoding, not HTML escaping alone.
- **Claimed-safe fragments can change structure.** `Html` is inserted verbatim
  and may open/close elements; the author owns balance/context. V1 does not
  re-parse it; sanitizing untrusted rich HTML is separate.
- **Ownership.** Splicing **consumes** the fragment (move semantics, matching
  obo ownership); reuse requires explicit `sql.copy(fragment)` or
  `html.copy(fragment)`. There is no auto-copy and no borrow-only outer-fragment
  lifetime rule. The copy helper must clone owned text/params/bytes. A query owns
  its strings/bytes until execution; a driver borrows them for the call.
  `SqlArg.Bytes` must not silently move a caller's array: use `sqlBytes` copy or
  document the move. `Result` errors do not change this. No DB
  connection/schema/service is part of this design.

```obo
let base = sql`WHERE region = ${region}`
let a = sql`SELECT * FROM t ${sql.copy(base)}`
let b = sql`SELECT * FROM u ${base}`       // consumes base

let h = html`<b>${name}</b>`
let p1 = html`<div>${html.copy(h)}</div>`
let p2 = html`<section>${h}</section>`      // consumes h
```

## 12. Database values and interop

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

## 13. Approved decisions (2026-10-31)

These five decisions are approved and are not reopened:

1. **Syntax.** Use compiler-known built-in `sql`/`html` backtick tags. There is
   no general user-defined tag protocol in v1, and no user-defined tags until
   the language supports heterogeneous typed value lists. A builder-only API is
   only a fallback/rejected alternative, not the primary interface.
2. **Fragment reuse.** Splicing **consumes** the fragment (move semantics,
   matching obo ownership). Reuse requires explicit `sql.copy(fragment)` or
   `html.copy(fragment)`; there is no auto-copy and no borrow-only outer-fragment
   lifetime rule.
3. **Type ownership.** `Sql`, `SqlArg`, `Html`, `HtmlAttr` and `HtmlAttrs` are
   ordinary standard-library records with **public fields**, not opaque builtins
   and not private fields. Direct construction is legal and equivalent to an
   explicit raw constructor: `new Sql{text: ..., params: ...}` and
   `new Html{text: ...}`. Documentation/lint require construction through the
   tags or raw constructors. Trust is convention-level; the design does not claim
   type-enforced safety. The types stay distinct and there is no shared
   `SafeString`.
4. **HTML v1 scope.** Support text content, quoted attribute values and URL
   attributes with scheme validation. Reject holes in script/style, unquoted
   attributes, tag names, attribute names and comments, with clear diagnostics.
5. **Scheduling.** Implement after Stage B `0.11` ships, as the next compiler
   feature. Do not reserve a version number. No implementation is authorized
   now.

The SQL examples and domain model are unchanged. Remaining proposal details,
which do not reopen these decisions: exact backtick escaping and raw-tag
spelling; helper signatures including `sql.copy`/`html.copy` and byte/argument
copy semantics; HTML attribute-context grammar, parser state machine and exact
diagnostic text; typed NULL and identifier APIs; list-expansion policy; and
`.sym` export/version mechanics.

Rejected alternatives remain: ordinary string interpolation; one cross-domain
`SafeString` or generic `escape(value)`; a public `TemplateDomain` supertype;
JS-style user tags; builder API as the primary interface; automatic array
expansion; SQL quoting/escaping instead of binding; identifiers as parameters;
HTML sanitizer as default escaping; and full cross-template context propagation
(the no-AST compiler cannot do it in v1).

## 14. Compatibility, migration, acceptance and roadmap

**Migration:** none for existing programs. The syntax is additive: a backtick is
currently unexpected, and `sql` followed by a backtick is invalid today. Existing
programs do not change meaning. The fragment types are ordinary standard-library
records with public fields, so no new ABI is required; `.sym` exposure and format
details will be settled during syntax review. No seed promotion, format or ABI
change is proposed, and no version number is reserved.

**Approved schedule:** implement only after Stage B `0.11` ships, as the next
compiler feature. This pass authorizes no implementation.

Future acceptance:

- **SQL:** binds, raw fragments, nesting, joins, `[]Sql`, empty lists, duplicate
  and mixed values, deterministic `$n` numbering; corpus covers quotes, `$1`,
  `?`, NUL and Unicode; raw with a hole is an error. The agreed SQL examples and
  semantics must remain unchanged.
- **HTML:** text/quoted-attribute/URL support; compile errors for
  script/style/comment/unquoted-attribute/tag-name/attribute-name holes; nested
  and array `Html`; empty arrays; double escape; XSS corpus stays text.
- **Domain:** shared walker produces the same SQL or HTML result; `Sql` in an
  HTML hole and `Html`/`HtmlAttrs` in a SQL hole are compile errors; no generic
  escape entry point; once-only trust has a regression test in both domains.
- **Ownership:** leak/double-free tests for builders, args, fragments, loops and
  early exits; splicing consumes; `sql.copy`/`html.copy` works; no auto-copy;
  byte copy/move; no use-after-free through DB calls.
- **Convention:** direct `Sql{text: ..., params: ...}`/`Html{text: ...}`
  construction is documented as raw and is not misrepresented as type-enforced
  safety; docs/lint guidance is checked.
- **Compiler:** exact error line/column; `.sym` round-trip if exported; fixed
  point, suites and 2,500,000-byte budget unchanged; no float arithmetic in
  escape paths (integer scans); escape benchmarks.
- **Scope:** no DB connection, schema, service, install or tool refactor.

Roadmap is gated on Stage B `0.11`: (1) scanner/syntax and raw tags; (2) SQL
builder, `SqlArg`, binds, nested fragments, joins; (3) HTML text/attrs,
nested/array fragments, state machine; (4) extract/refactor the shared walker and
internal domain descriptor only after both built-ins pass, keeping public types
distinct; (5) URL validation, `HtmlAttrs`, NULL/bytes rules; (6) adapters later.
User-defined domains remain a later feature with the prerequisites in §8; until
then user code can use the builder API but cannot register a tag.

## 15. References

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
