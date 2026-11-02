# obx/compiler: the self-hosted compiler

Stage 4 of [../PLAN.md](../PLAN.md): the obx compiler written in obx, a port
of the reference compiler (`../ref/`, Python) with the same algorithms, checked
mechanically against it. Step 1 (the front end) is done; the driver only
parses for now.

    python3 obx/ref/obxc.py obx/compiler/main.obx -o obxc   # build (ref compiles it to C, then gcc)
    ./obxc --dump-ast file.obx...                           # the parse tree, as ref's --dump-ast
    python3 obx/compiler/run_compare.py                     # build, then compare with ref on every test file
    python3 obx/compiler/run_compare.py --mutants 20000 --seed 2   # also on mutated programs
    python3 obx/compiler/run_compare.py --hashseed          # ref's C output is independent of PYTHONHASHSEED

## Layout

| File | Lines | Ports | Contents |
|---|---:|---|---|
| `syntax.obx` | 1,840 | `ref/syntax.py` (889) | errors, tokens, source text (UTF-8 check, newlines), lexer with the ASI rule, the AST arena, the parser |
| `dump.obx` | 388 | `ref/dump.py` (298) | `--dump-ast` |
| `main.obx` | 63 | `ref/obxc.py` (`--dump-ast` only) | driver |
| `run_compare.py` | 256 | | build and compare with ref; mutant generator; hash-seed check |

The obx code is about twice as many lines as the Python it replaces (1,660
against 800 lines without comments and blank lines for the parser). The
difference is mostly what obx does not have: tuples and list comprehensions
(the AST tuples become nodes; lists are built in a local array and copied),
a conditional expression, exceptions (an error is a `never` function), and
Python's `repr`, `int(text, base)` and UTF-8 decoder, which ref's messages and
literals depend on and which are written out here (about 200 lines).

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

**Nesting depth.** ref turns Python's `RecursionError` into `file:1: the
program is nested too deeply`; that limit comes from Python's frame count, so
the dump of about 900 nested parentheses already fails there. This parser
counts its nesting (statements, types, expressions, unary operators) and stops
with the same message above 3,000, well within the C stack. The two do not
agree between those depths; no test is that deep.

## How the port is checked

`run_compare.py` builds the compiler with ref, then dumps every `.obx` file
under `tests/`, `ref/tests/`, `lib/`, `redteam/` and `compiler/` (1,969 files,
44 of them syntax errors) with both compilers, each in one process, and
compares the text. The syntax-error files are also run one at a time, where
the message goes to stderr, and exit status and stderr must match. The
compiler runs with `OBX_CHECK=1`, so a leak fails the run.

The suites have few syntax errors, so `--mutants N` makes N programs from the
test files by token edits (deletions, insertions of tokens and of bad bytes:
quotes, backslashes, CR, non-ASCII letters and digits, a BOM, invalid UTF-8,
long literals) and compares those too: about 88% of them are syntax errors,
covering every lexer and parser message.

## Status

- `--dump-ast`: identical for all 1,969 test files and for 25,000 mutants
  after the round-3 lexer changes (22,000 of them syntax errors; 65,000
  more before them), in both the many-file and the one-file form. No leaks.
- Speed: all 1,969 test files in 0.15 s (ref: 1.0 s in one Python process);
  a 409,000-line file (12.9 MB, 2.7 million dump lines) in 1.4 s (ref:
  15.8 s).
- Next: `sema.obx` (`--dump-types`), then `check.obx` and `emit.obx`
  (identical C), then the bootstrap (PLAN.md, stage 4, "Order"). sema needs
  ref's `CRESERVED` pattern and `CHELPERS` list (the `obx_` helpers an extern
  may name) word for word.
