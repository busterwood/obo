# obx conformance tests

Black-box tests of the language in `../SPEC.md`. They only use the compiler
driver, `obxc file.obx -o out`.

    python3 obx/tests/run.py [--compiler PATH] [-j N] [filters...]
    python3 obx/tests/compiler/run_tests.py [--conformance] [filters...]

A filter is a substring of the test path (`s10_`, `error/s06`, `trap`). The
default compiler is `$OBXC`, else `../compiler/out/obxc` (build it from the C
seed with `obx/compiler/build.sh`; a `.py` path is run with python3, anything
else is executed directly).

`compiler/` holds the compiler tests: compiler choices and runtime limits
rather than the spec (`run/`, `error/`, and `unit/`, run tests built with
`// cflags:` such as `-DRT_STR_MAX=64`). They were the reference compiler's own
tests until ref was retired (2026-11-02, `../../archive/obx-ref/`), and use the
same format; `--conformance` runs this suite as well. Compile timeout 20 s, run timeout 10 s. One line per
failure, then `N passed, M failed`; exit status 1 on any failure.

## Test tiers and the build cache

Quick gate, after every change (`obx/tools/gate.sh quick`):

    python3 obx/tools/seed_check.py           # builds compiler/out/obxc from the seed; seed fixed point
    python3 obx/tests/run.py                  # conformance
    python3 obx/tests/compiler/run_tests.py   # compiler tests
    python3 obx/lib/run_tests.py -j 8
    python3 obx/tests/run_native.py --lib     # native backend against the C backend
    bash obx/tools/native_selfbuild.sh

The seed check builds `compiler/out/obxc` from `compiler/seed/obxc.c`, has it
compile the current sources (stage 2), stage 2 compile them again (stage 3),
and requires the C of stages 2 and 3 to equal the seed; so a compiler change
needs `compiler/reseed.sh` before the gate passes.

Full gate, at the end of a fix round and before a merge (`obx/tools/gate.sh full`):
the quick gate plus the red-team harnesses, rounds 1 to 5 (`obx/redteam/rt.py`,
`round2/rt.py`, `round3/rt.py`, `round4/rt.py`, `round5/rt.py`) under ASan+UBSan, and the native
backend on the red-team corpora and on 200 + 200 generated programs (`obx/tests/vrgen.py`,
`obx/tests/loopgen.py`). The
script prints a summary with the time of each
step and the total, and exits 1 on any failure.

`python3 obx/cmd/eza_gate.py -j 8` (or `python3 obx/tests/eza.py`) is the eza golden
suite for `cmd/eza.obx`, outside the gates (about 2.5 minutes; see
[../cmd/README.md](../cmd/README.md)).

`python3 obx/cmd/tools_gate.py -j 8` (or `python3 obx/tests/tools.py`, `--c` for the
C build, `--memory` for the leak checks) runs `cmd/cat.obx`, `wc`, `sort`, `head` and
`tail` against GNU coreutils and their own goldens (`tests/tools/`), and `grep` against
GNU grep (346 cases, no goldens: skipped without a GNU grep), also outside the gates.

`python3 obx/tests/zoxide.py [--c] [--ref PATH] [-k PATTERN] [-j N]` builds `cmd/zoxide.obx` and runs
362 cases against the real zoxide 0.9.9 (`--ref`, `$ZOXIDE_REF` or `zoxide` on PATH; without
one it exits 2), outside the gates. See [../cmd/README.md](../cmd/README.md).

`OBX_PREV=old/obxc python3 obx/tests/run_prev.py [--native] [--lib] [--redteam] [--gen N]
[--eza]` compares the compiler against a previous build, outside the gates. Both compilers
build every run and error test, plus the library tests, the red-team rounds 1 to 5, N
fresh round-5 programs and eza. The two builds must fail alike, or the two programs must
print and exit alike under OBX_CHECK=1. Use it when a compiler change is meant to keep
every program's behaviour.

`python3 obx/tests/vrgen.py [-n N] [--seed S] [--keep DIR]` generates programs that pass
value records with heap fields through small functions (by value, by borrow, receivers,
results, copies, array elements, match arms, loops, `x = f(x)`), builds each through C and
natively, and requires the same output and exit code under OBX_CHECK=1: a check of the
native backend's inlining and record rewrites (compiler/opt.obx), where a leak shows.

`python3 obx/tests/loopgen.py [-n N] [--seed S] [--keep DIR]` does the same with programs of
array loops (steps up and down, `<=` bounds, for ranges, nested loops; bodies that read and
write near the index, store then load, append, pop, reassign the array, pass it `mut &`,
change the index or the bound, break, continue, call a closure that appends to the array it
captures), many ending in a bounds or overflow trap: a check of the native loop pass
(compiler/loops.obx).

`python3 obx/compiler/fuzz.py [-n N] [--seed S] [--cc]` is a mutation fuzzer for the
compiler, outside the gates: every mutant of a test program must compile or be rejected
with one `file:line: message`, never crash or leak.

Every runner that builds a binary goes through `obx/tools/cbuild.py`: obxc emits the
C (`--emit-c-json`), and the binary is cached under the hash of the C text (rt.h is part
of it), the exact C compiler command and the compiler's version. A test that did not
change in C is not compiled again. The cache is `$OBX_CACHE` (default
`~/.cache/obx-build`), trimmed to `$OBX_CACHE_MB` (default 2000) MB; `OBX_NOCACHE=1`
turns it off. Compile errors produce no C and are never cached.

`OBXC` chooses the compiler for every runner: `obx/compiler/out/obxc` by default, or
another build, e.g. `OBXC=/tmp/obxc python3 obx/tests/run.py`. The gate's first step
rebuilds `compiler/out/obxc` from the seed; run the gate with `OBXC` unset.

## Test format

Every `*.obx` under `run/` and `error/` is one test, except that a directory
containing `main.obx` is one multi-file test (the other files are its modules;
`main.obx` is compiled and carries the directives). Directives are `//` comment
lines anywhere in the file:

| Directive | Where | Meaning |
|---|---|---|
| `// expect: text` | run | one line of expected stdout, in order; `// expect:` alone is an empty line. No `expect` lines means empty stdout. |
| `// stderr: text` | run | one line of expected stderr, in order; when a test has any, stderr is compared exactly (a runtime error's message) |
| `// exit: N` | run | expected exit code (default 0) |
| `// stdin: text` | run | a line fed to stdin (unused so far) |
| `// error: words` | error | compile must fail and the output must contain `words` (case-insensitive). `a\|b` accepts either; empty accepts any failure. Several lines must all match. |
| `// line: N` | error | the output must contain `:N:` |
| `// needs: name` | run | the test is skipped when no `lib<name>.so*` is installed (a C library its externs link) |

A run test that exits with code 77 (and does not expect it) is reported as skipped:
the library tests that need a live database server exit 77 when there is none.

`run/` tests are run with `OBX_CHECK=1`, so a leaked object makes the exit
code 5 and fails the test. Stdout is compared exactly. Stderr is checked only
when the test has `// stderr:` lines.
Error tests prefer a name that must occur in any sane message (the offending
variable, function, variant or type, written with a distinctive spelling such
as `zed`, `CONSTX`, `fixedvar`); where a message could use several wordings the
`a|b` form lists them. Tests whose failure reason is not pinned down by the spec
(syntax errors) use an empty `// error:`.

## Conventions the tests rely on

- Only `println`/`print` with one `int` or `str` argument. Everything else is
  converted first (`int(b)`, `if c { println("ok") }`).
- Calls pass borrows explicitly (`f(&x)`, `f(mut &x)`); method calls borrow the
  receiver implicitly.
- `[]` is the empty array literal; its type comes from context.
- User types stand in for library types wherever possible (a `Dec` value record
  for Decimal, `D` records with a `drop` that prints for ownership order).
  `Option`, `Result`, `Map`, `sort`, `Ord` are the only std facilities used.

## Coverage by spec section (tests = run / error)

| Section | Topic | Run | Error |
|---|---|---:|---:|
| 2 | lexical: ASI, literals, char/string escapes, comments, bad literals | 11 | 9 |
| 3 | precedence, no comparison chain, literal-in-condition, postfix | 8 | 1 |
| 4 | primitives, arrays, value/boxed records, closed types, aliases, nominal types | 25 | 10 |
| 5 | modules (multi-file), consts, globals, assoc fns, function values, interfaces | 25 | 28 |
| 6 | generics: fns, types, methods, constraints, inference, definition-time checks | 23 | 17 |
| 7 | overflow/division/shift/conversion traps, wrapping, operators as methods, fractional literals, try, temporaries, eval order | 52 | 12 |
| 8 | statements, for/while, match (int/str/byte/closed, borrowing vs consuming, exhaustiveness) | 25 | 19 |
| 9 | moves, one-path/loop moves, drop order, destructors, replace/pop/swap | 23 | 18 |
| 10 | borrows: all six rules, accepted and rejected | 15 | 36 |
| 11 | builtins: len, append, pop, swap, replace, move, array, assert, exit | 7 | 0 |
| 12 | extern "c": strlen, abs, getpid, atoi, malloc/free wrapper with drop, strdup/strFromC, `mut &int` | 8 | 4 |
| 14 | runtime exit codes 2, 3, 12 | 11 | 0 |

Exit code 10 is in section 7 (`s07_trap_*`); exit 5 (leak) is exercised by
every run test through `OBX_CHECK`. Section 13/15 are not testable.

## Places where the spec is ambiguous, and what the tests assume

Syntax and typing
1. **Passing a borrow variable onward.** Tests never pass a `&T` binding to a
   function (`f(l)` vs `f(&l)`); they use method calls on it instead. The spec
   does not say whether `eval(l)` or `eval(&l)` is right when `l: &Expr`.
2. **Explicit borrows at call sites.** Tests write `f(&x)`/`f(mut &x)`. The
   spec shows `append(mut &a, v)` but the prototype wrote `sort(xs)`.
3. **Empty array literal** is written `[]` (grammar), not `[]T{}` as in the
   prototype std. The element type comes from the context.
4. **Returning a borrow:** tests write `return &o.d` / `return &xs[0]`. Rule 5
   says "return a place rooted at it" without giving the syntax.
5. **Mutable-borrow results** (`fn f(a: mut &T): mut &U`) are not tested.
6. **Integer literal type in a binary operation** (`x + 1` with `x: i32`) is
   assumed to take the other operand's type. Fractional literals are only used
   where the expected type is a declaration, parameter, field or return type
   (plus one deliberate test, `s07_float_literal_in_binary_op`, which assumes
   `float + 3.0f` types the literal as `float`).
7. **`Some(3)`/`None`/`Ok(v)`/`Err(e)`** are positional constructors with no
   `{}`, as in section 4.4 (the prototype used `Some{value: x}`). `None` needs
   an expected type.
8. **Match arms on imported variants.** The grammar only allows an `IDENT` arm
   name, so tests never match on a closed type from another module in `main`;
   a function in the declaring module does the match (`s05_mod_qualified_variants`).
9. **Match on a place binds copy fields as copies and owned fields as `&T`**;
   bindings are used only for reads and method calls.
10. **A function whose body is a total `match` where every arm returns** is
    never used for "falls off the end"; tests assign to a `var` and return after.
11. **`return` as last token before `}`/newline** (`s02_asi_return`).
12. **Multi-line call arguments** (newline after a literal inside parentheses)
    are not tested; the ASI rule as written would end the statement there.
13. **A bare expression statement that yields an owned value** (`mk(7)`,
    `pop(mut &xs)`) is allowed and drops the value at the end of the statement.
14. **`let` re-declaration / shadowing** of a name in the same or an inner scope
    is never used.
15. **Variables never used / unused `var`** are not errors or warnings that fail
    compilation.

Library
16. `print`/`println` accept `int` and `str`; their behaviour with `bool`,
    `float`, `byte`, `Decimal` is not relied on. `println("")` prints a blank line.
17. **Ord/Eq for `int` and `str`** exist (`cmp`, `eq`), so `maxOf("a","b")` works.
18. **`Map`** has `newMap()`, `put(k, v)`, `get(&k): Option[V]`, `has(&k)`,
    `size()`, with `&K` borrowed keys (as in the prototype). `sort(mut &xs)`
    takes a `mut &[]T` (the prototype wrote `sort(&xs)`). The spec gives no
    signatures for either.
19. **`strByte(s, i)`** exists and returns an integer type convertible with
    `int(...)`; used once to test string index out of range (exit 3).
20. **`fromFraction`**: a user-defined top-level `fromFraction` is not tested
    (does it shadow std's or clash?). `d.ddd` -> `fromFraction(digits, n)` with
    `digits` the integer value of the digit string (`0.05m` -> `(5, 2)`,
    `75.00m` -> `(7500, 2)`). Decimal's own behaviour is not tested beyond
    `let d = 1.25m` compiling (`s07_fraction_default_decimal`).

Semantics
21. **Receiver must be a place for a borrow** (7.5) versus "temporaries" (7.7):
    tests treat `mk().get()` with `get(&Self)` as an error
    (`s10_receiver_must_be_place`) but allow field reads (`mk(5).id`) and
    by-value receivers (`P{...}.sum()`) on temporaries. This is the test most
    likely to be wrong either way.
22. **Drop timing of match-consumed unbound fields** is not observed; tests only
    check it happens before the statement after the `match`.
23. **Assignment drops the old value after evaluating the new one** (the new
    value is built first, then the old is dropped, then the store).
24. **A moved-from path that ends in `return`** does not take part in the join
    (`s09_move_branch_returns` is accepted).
25. **Rule 4 ends at the last use**: `s10_frozen_ends_at_last_use` (a run
    test; it was the error test `s10_frozen_lexical_after_last_use` while rule
    4 was lexical) assigns the root once `r` is no longer used.
26. **Neg of MIN** (`-MIN`) traps with exit 10 (it is overflow).
27. **Float division by zero / NaN** do not trap; only `int(f)` of NaN/huge does.
28. **A `str` containing NUL passed to extern**, `exit(code)` with live owned
    values (is the leak check skipped?), and **constant overflow in a `const`
    initializer** (compile error or run-time trap?) are not tested because the
    spec does not give the exit code or phase.
29. **Global var initializers**: only integer/string constants and constant
    expressions are used; whether a record literal counts as constant is open.
    The "method call on a global works on a temporary copy" rule is not tested.
30. **`for x in xs` with a `var xs` and an owned element** freezes `xs` (reads
    such as `len(xs)` are allowed in the body).
31. **Variant name clash** between a user closed type and std's `Option`/`Result`
    variants is not tested.
32. **Error messages**: tests only assume the offending name/type appears; the
    `a|b` alternatives in `// error:` are guesses at wordings (`move`, `borrow`,
    `infer`, `instantiat`, ...). A reasonable compiler failing one of these
    should be fixed in the test, not necessarily the compiler.

## Possible spec problems noticed

- Section 10 rule 3 says a moved argument may not overlap a borrowed one, but
  nothing mentions a *copied* argument that reads the same place
  (`f(&n, n.v)`); that is presumably fine but unstated.
- The ASI rule makes `foo(\n 1\n)` ambiguous (see 12 above).
- `match` on a borrowed place whose arm assigns to the scrutinee (`o = None`
  inside `Some(d) {...}`) is not covered by rule 4; the bindings are borrows
  but are not `let`s.
- Section 9.3 says a consuming match drops "fields not bound"; it does not say
  whether that happens at the start of the match or the end of the arm.
- `ptr` is "only for extern declarations and their wrappers", but nothing in the
  rules stops a `ptr` local or field elsewhere.
- `exit(code)` "without drops" contradicts `OBX_CHECK=1` leak checking unless
  the check is also skipped; section 14 does not say.
- `Option` is `value closed { Some(T); None }` in 4.4 but `std.obx` once
  used named fields (since resolved in the library).
