# The obo language reference

[`LANGUAGE.md`](../LANGUAGE.md) is the core guide: source form, literals,
operators, declarations, statements, `match` and a type summary. This directory
holds the detailed chapters. Section numbers are the ones the reference has
always used; the core guide is section 0 and the index below maps every number
to its file.

| Section | File | Contents |
|---|---|---|
| 0 | [LANGUAGE.md](../LANGUAGE.md) | source form, literals, operators, declarations, statements, `match` |
| 1 | [types.md](types.md) | type table, records and fields, floats, decimals |
| 2–3 | [memory.md](memory.md) | ownership, moves and lifetime |
| 4 | [references.md](references.md) | borrowed `&T`, invalidation, the one ownership rule |
| 5 | [handles.md](handles.md) | checked `^T` handles |
| 5a | [methods.md](methods.md) | receivers, methods and dispatch |
| 5b | [interfaces.md](interfaces.md) | first-class interfaces and tiers |
| 5c–5d | [lambdas.md](lambdas.md) | function types, lambdas and captures |
| 6 | [modules.md](modules.md) | modules, imports, exports, `.sym` files, `--check` |
| 7 | [results.md](results.md) | `Result[T]`, error values and `try` |
| 8 | [builtins.md](builtins.md) | built-in functions, I/O, templates and metadata |
| 9 | [runtime.md](runtime.md) | runtime checks and exit codes |
| 10 | [errors.md](errors.md) | compile-error reference |
| 11 | [obx.md](obx.md) | differences from obx |
| 12 | [holes.md](holes.md) | known holes and planned changes |
| — | [grammar.md](grammar.md) | an informative EBNF of the accepted syntax |
| — | [ABI.md](../ABI.md) | value, call, vtable and capture layouts |
| — | [SYMFILE.md](../SYMFILE.md) | `.sym` format, hashes and versions |

Start with the core guide for the syntax, then read sections 2–4 for the
ownership model, which is what makes obo different. The remaining chapters are
reference: read the one for a feature when you use it.

Every `obo` example in these files is checked against the compiler by
`obo/tests/doc_check.py`. A block marked `// example: runs` is a complete
program with expected output; `// example: error: TEXT` must be rejected with
that message; `// example: compiles` is a known problem, not a model of good
code.

The design records in [`obo/design/`](../design/README.md) explain why the
language is shaped this way; they are authoritative design documents, not
tutorials. The cross-cutting decision record is
[`obo/FORK.md`](../FORK.md).
