# obx/compiler: the self-hosted compiler

Stage 4 of [../PLAN.md](../PLAN.md): the obx compiler written in obx, a port
of the reference compiler (`../ref/`, Python) with the same algorithms, checked
mechanically against it. Steps 1 (the front end) and 2 (sema) are done; the
driver parses and resolves declarations for now.

    python3 obx/ref/obxc.py obx/compiler/main.obx -o obxc   # build (ref compiles it to C, then gcc)
    ./obxc --dump-ast file.obx...                           # the parse tree, as ref's --dump-ast
    ./obxc --dump-types file.obx...                         # what sema resolves, as ref's --dump-types
    python3 obx/compiler/run_compare.py                     # build, then compare with ref on every test file
    python3 obx/compiler/run_compare.py --mutants 20000 --seed 2   # also on mutated programs
    python3 obx/compiler/run_compare.py --mode types        # only --dump-types (or --mode ast)
    python3 obx/compiler/run_compare.py --hashseed          # ref's C output is independent of PYTHONHASHSEED

## Layout

| File | Lines | Ports | Contents |
|---|---:|---|---|
| `syntax.obx` | 1,735 | `ref/syntax.py` (915) | errors, tokens, source text (UTF-8 check, newlines), lexer with the ASI rule, the AST arena, the parser |
| `sema.obx` | 1,857 | `ref/sema.py` (865) | paths, module loading and imports, scopes, the type table, type resolution, constraints and `satisfies`, signatures, constant folding |
| `num.obx` | 492 | (Python's `int`, `float`, `repr`) | big integers for constant folding; float parsing and Python's float `repr` |
| `dump.obx` | 396 | `ref/dump.py` (311) | `--dump-ast` |
| `tdump.obx` | 216 | `ref/dump.py` `dump_types` (99) | `--dump-types` |
| `main.obx` | 60 | `ref/obxc.py` (the dump modes) | driver |
| `run_compare.py` | 320 | | build and compare with ref; mutant generators; hash-seed check |
| `tests/types/` | | | programs that cover the dump (constant folding, generics, two modules with one name) |

The obx code is about twice as many lines as the Python it replaces (1,540
against 830 lines without comments and blank lines for the parser). The
difference is mostly what obx does not have: tuples and list comprehensions
(the AST tuples become nodes; lists are built in a local array and copied),
exceptions (an error is a `never` function), and Python's `repr`,
`int(text, base)` and UTF-8 decoder, which ref's messages and literals depend
on and which are written out here (about 200 lines).

sema is 2.1 times its Python (1,620 lines against 785 without comments and
blank lines), plus 420 lines of `num.obx` for what Python's numbers give ref
for free: constant folding uses unbounded integers (a literal may have 1,000
digits, and a message prints the exact out-of-range value) and prints floats
with Python's `repr`. The rest of the growth is tables instead of objects
(`pg.types[d].tparams[i]` for `d.tparams[i]`) and copies of a list before a
loop or a call that mutates the program (rule 4: a `for` over `pg.x` freezes
`pg`; rule 3: `&pg.x` cannot be passed to a `mut &` method of `pg`).

## Design

**The AST is an arena, not a tree of boxed closed types.** Every node is a
`Node` value record in `Ast.nodes`, named by its index; a list is a handle
into `Ast.items` (its length, then the element indexes). A node has a kind
(`N_*`), a line, flags, three strings (`s`, `t`, `u`) and five int slots
(`a` to `e`) whose meaning per kind is listed next to the `N_*` constants.
The reasons:

- Borrows are second class (SPEC 4.5, 10): a symbol table, a declaration
  record or a work list cannot hold a `&Expr` into a boxed tree. The later
  stages refer to AST nodes from everywhere (a FnDecl keeps its body, a
  generic function is rendered once per instance, a template refers back to
  its node); with an arena that reference is an `int`, a copy value that can
  be stored anywhere.
- ref attaches nothing to AST nodes after parsing, but sema and check keep
  per-node facts (types, resolved declarations); with indexes those are side
  arrays indexed by the node, with no change to the tree.
- One growing array instead of one heap object per node, and no recursive
  drop of a deep tree.
- A boxed tree in obx would be walked by `match`, which binds owned fields as
  borrows: natural for a single walk, but every node a pass wants to keep
  would have to be found again by a path.

The cost is that field names are positional (`a`, `b`, ...) and the kinds are
not checked by the type system; accessor functions per kind can be added when
sema needs them. Kinds are `int` constants rather than a closed type because
`match` arms cannot name constants and a closed type of ~55 payload-free
variants would need an `eq` method or a `match` for every comparison.

**Tokens** are value records `{k, op, line, s, v}`. Names, keywords and
symbols share their bytes with the source (a substring is not a copy), as do
string literals without escapes. Integer literals keep their value as decimal
digits of any size (ref keeps a Python int; the range check is sema's), so the
lexer converts hex and binary digits to decimal itself.

**Errors** stop the compiler: `fail(fname, line, msg): never` prints `file:line:
message` (to stderr, or as `error: ...` on stdout when several files are
dumped, so that it stays in order) and exits 1, as ref does with its first
`CompileError`. A run over many files therefore stops at the first syntax error;
`run_compare.py` starts the compiler again from the next file.

**sema's tables.** ref's objects (Module, TypeDecl, Variant, FnDecl, Const,
Global, TypeParam) become value records in arrays of one `Program`, named by
index; a scope maps a name to a kind and an index. A type is an int: types
are interned (hash-consed) in `Program.tys` with their lists in
`Program.tlists`, so the equality ref gets from comparing type tuples is
`==` on ints, and substitution builds new ids. An environment (ref's dict
from type parameters to types) is a flat list of pairs, with -1 for `Self`.
A folded constant is a `CVal` record (ref's `(type, value, kind)` tuple).
ref's `const_value` catches an error in an initializer and raises it again
wrapped ("the initializer of const C is not constant (...)", nested for
constants that refer to constants); with no exceptions, `Program` keeps a
stack of the constants being folded and the error function applies the
wrappers itself. ref's `RecursionError` from `is_copy` on a value type that
contains itself (before the inline-cycle check) is a depth limit of 5,000.

Known differences, none reached by a test: a module's C prefix replaces
every non-ASCII-alphanumeric byte with `_`, where ref keeps Unicode letters
(Python's `isalnum`); absolute paths are normalized fully, where Python keeps
a leading `//`; the nesting limits differ, as below.

**Nesting depth.** ref turns Python's `RecursionError` into `file:1: the
program is nested too deeply`; that limit comes from Python's frame count, so
the dump of about 900 nested parentheses already fails there. This parser
counts its nesting (statements, types, expressions, unary operators) and stops
with the same message above 3,000, well within the C stack. The two do not
agree between those depths; no test is that deep.

## How the port is checked

`run_compare.py` builds the compiler with ref, then dumps every `.obx` file
under `tests/`, `ref/tests/`, `lib/`, `redteam/` and `compiler/` (2,025 files,
44 of them syntax errors) with both compilers, each in one process, and
compares the text; then the same with `--dump-types` (ref in parallel
batches). `--dump-types` stops after sema, so a program whose first error is
in a function body prints its full dump on both sides; its error is compared
in step 3. The syntax-error files are also run one at a time, where
the message goes to stderr, and exit status and stderr must match. The
compiler runs with `OBX_CHECK=1`, so a leak fails the run.

The suites have few syntax errors, so `--mutants N` makes N programs from the
test files by token edits (deletions, insertions of tokens and of bad bytes:
quotes, backslashes, CR, non-ASCII letters and digits, a BOM, invalid UTF-8,
long literals) and compares those too: about 88% of them are syntax errors,
covering every lexer and parser message. Every second mutant instead has one
or two names or numbers replaced (by others from the program, or by type
names, `Self`, `copy`, limits), which mostly parses and reaches sema. The
mutants are written next to the program they come from, as hidden
`.obxmut*.obx` files removed afterwards, so their `./` imports resolve.

## Status

- `--dump-types`: identical for all 2,025 test files (181 of them errors in
  sema or the parser) and for 15,000 mutants (half token edits, half names
  and numbers replaced, which mostly reach sema), in the many-file and the
  one-file form; no leaks. Every sema error message is reached by a test
  (`ref/tests/error/sema_*`: 43 tests added for this) except those that
  cannot happen before bodies are checked (a type parameter declared outside
  a receiver; `never`, no result or a borrow as a type argument, which only
  inference makes) or outside std (a primitive's `drop`, type arguments on a
  primitive receiver); `cannot read` of the main file was compared by hand.
  Random constant-folding programs (3,000 float constants; integer
  expressions in every type with wrapping, shifts, division and their
  errors: 925 constants and 2,076 error messages) fold identically.
- Speed: `--dump-types` on all test files in 7.9 s in one process, 3.4 ms a
  file (ref: 45 to 65 ms a file, 13 s over all cores); most of it is std,
  which is loaded again for every file.

- `--dump-ast`: identical for all 2,025 test files and for 25,000 mutants
  after the round-3 lexer changes (22,000 of them syntax errors; 65,000
  more before them), in both the many-file and the one-file form. No leaks.
- Speed: all 1,969 test files in 0.15 s (ref: 1.0 s in one Python process);
  a 409,000-line file (12.9 MB, 2.7 million dump lines) in 1.4 s (ref:
  15.8 s).
- Next: `check.obx` and `emit.obx` (identical C), then the bootstrap
  (PLAN.md, stage 4, "Order").
