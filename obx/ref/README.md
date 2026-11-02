# obx/ref: the reference compiler

The draft-1 obx language (`../SPEC.md`, sections 1 to 12, 14, 15) compiled to C
by Python, then to a binary by gcc.

    python3 obxc.py file.obx                  # writes ./file next to the source
    python3 obxc.py file.obx -o out           # names the binary
    python3 obxc.py file.obx --run -- a b     # compiles to a temp dir and runs it with arguments
    python3 obxc.py file.obx --emit-c         # prints the generated C
    python3 obxc.py file.obx -L libroot       # another root for `import` paths
    python3 obxc.py --dump-ast a.obx b.obx    # prints the parse tree of each file (see "AST dump")
    python3 obxc.py --dump-types a.obx        # what sema resolves for the program (see "Types dump")
    python3 run_tests.py                      # own tests (tests/run, tests/error, tests/unit)
    python3 run_tests.py --conformance        # also the conformance suite in ../tests

A compile error prints `file:line: message` and exits 1 (one error, then stop).
gcc runs with `-O2 -w` (`-O1` until 2026-11-02: `-O2` builds the largest
program, the self-hosted compiler, in 3.9 s instead of 1.6 s, and runs
`bench/loop` 1.6x faster). An `extern "lib"` links `-llib`, or the library by
its full path when only `libX.so.N` exists (no dev symlink); `"c"` adds nothing.
Run a program with `OBX_CHECK=1` to make a leak (live objects at exit) exit 5;
live objects are counted only then (`rt_init` reads the variable once).

## AST dump

`--dump-ast` parses each file given (no imports are followed, nothing is
checked) and prints its tree in a canonical, line-oriented form, so that the
self-hosted compiler's parser can be compared with this one
(`../compiler/run_compare.py`). It is `dump.py`. With several files each dump
follows a line `== path`, and a file that does not parse prints `error:
message` there instead (exit status 1); with one file the error goes to stderr.

One node per line, indented two spaces per depth. Each line is `label: body`:
the label is the field name in the parent (`module` for the root, the index
for a list item), the body is `-` (absent), `[n]` (a list, whose n items
follow, labelled `0` to `n-1`), or a node `kind line scalar=value ...` whose
child fields follow on the next lines in a fixed order. The line is `-` for
type and pattern tuples that carry none. Scalars are names written bare,
`true`/`false`, `-` for none, decimal integers, name lists `[a,b]`, and
quoted strings (`"..."` with printable ASCII as is, `\\`, `\"`, and every
other byte `\xHH`) for string literals, import paths and extern names.

    module: m.obx
      imports: [0]
      decls: [1]
        0: fn 1 name=main pub=false extern=- cname=-
          recv: -
          tparams: [0]
          params: [0]
          ret: -
          body: block 1
            stmts: [1]
              0: expr 1
                e: call 1
                  fn: name 1 v=println
                  args: [1]
                    0: str 1 v="hi"

The node kinds and their fields in order are those of `dump.py`. An index
or call bracket argument that parses as a type is `targ`; a match arm's
literals are `int`/`char`/`str` nodes with line `-`, and a name in an arm list
(a constant, SPEC 8) is `name - v=[mod,K]`; an arm of one name stays a variant
arm (`variant=K vpath=[K]`), whether it names a variant or a constant. The
expressions of SPEC 7.8 are `ifx` (fields `cond`, `then`, `els`; an `else if`
is an `ifx` in `els`) and `matchx` (fields `e`, `arms`, as `match`, with each
arm's `body` an expression instead of a block).

## Types dump

`--dump-types` loads the program of each file given (std, then the file and
its imports), resolves its declarations (`Program.resolve_all`: type
parameters, constraints, fields, variants, interface members, signatures,
globals' types, constant values) and prints what it computed; no function
body is checked. It is `dump_types` in `dump.py`, and `../compiler` compares
its own sema with it. Several files and errors work as for `--dump-ast`;
an error is any error up to the end of `resolve_all` (syntax in any module,
imports, declarations, constants), so a program whose first error is in a
body prints its full dump.

Modules come in load order (std first; a module before the modules it
imports), each with its declarations in scope order, then its functions
(methods too) in source order; then the program-wide tables:

    module m file=m.obx cprefix=m std=false
      import util util2                  # an alias, the imported module's cprefix
      scope Box N main                   # the module's top-level names, in order
      type Box 2 pub=false form=record value=false cname=m_Box copy=false drop=-
        tparam T#90 copy=false cons=[]
        field v pub=false T#90
        methods get
      const N 3 pub=false cname=k1_m_N typed int 2
      fn Box.get 4 pub=false extern=- cname=- recv=ref recvname=b recvtype=m_Box[T#91]
        tparam T#91 copy=true cons=[]
        param b 4 var=false &m_Box[T#91]
        ret T#91 bparam=-
    prim str methods len ...             # the methods of each primitive, in declaration order
    prelude Some std_Option.Some         # the bare variants
    extern sqrt std.sqrt                 # each C name, with the first extern that declared it

A type is written in full: a primitive by name, `[]T`, `&T`, `mut &T`,
`&[T]`, `mut &[T]`, `fn(A, B): R` (no `: R` without a result), a closure
`&fn(...)`, `Self`, a declared type by its C name (unique in the program) with
its arguments in brackets, and a type parameter by its name and a number,
`T#3`, given in the order the dump first meets each parameter (so that two
parameters with one name can be told apart). A type's `copy` (`-` for an
interface or alias) is `is_copy` of the type applied to its own parameters;
`drop` is the label of its drop method. A member's `mode` is `val`, `ref`,
`mut` or `-` (an associated function); a function's `recv` is also `assoc`,
or `-` when it is not a method. A constant's value is its fold kind (`int`,
`frac`, `float`, `str`, `bool`, `typed` for a constant with a declared type,
or `table` for an array, record or variant constant, which has no folded
value), its type or `-`, and the value: decimal for integers, the literal's
text for an untyped fraction, Python's `repr` for floats, a quoted string.
Lines are the declaration's line in its file.

Not in the dump: a global's initializer value. ref computes it after the
bodies are checked (`check.global_init`), so it belongs to the next stage of
the comparison (the C output).

## Architecture

| File | Lines | Role |
|---|---:|---|
| `syntax.py` | 881 | lexer (with the ASI rule) and parser to `N` nodes |
| `sema.py` | 865 | modules, imports, scopes, types, type parameters and constraints, `satisfies`, typed constant folding |
| `check.py` | 2,359 | checks every function body once and writes it as a C template |
| `emit.py` | 354 | renders templates once per instance, drop/dup glue, struct layouts |
| `obxc.py` | 149 | driver |
| `dump.py` | 401 | `--dump-ast`, `--dump-types` |
| `rt.h` | 460 | the C runtime: allocation counter, traps, str, arrays and slices, checked integers, drop stack, stack-overflow handler |
| `../lib/std.obx` | 1,400 | the prelude (ordinary obx code; moved to `lib/` in stage 3) |

Python total: about 4,950 lines (with `run_tests.py`, 55).

**Checking once, rendering per instance (SPEC 6.2, 6.4).** `check.py` checks a
function body exactly once, generic or not. A type parameter is an opaque type:
owned unless `T: copy`, with only the methods and associated functions of its
constraints. Every ownership decision (move, copy, borrow, drop, temporary) is
made there. The output is a C template: C lines with holes for what depends on
type arguments: a C type, drop glue, dup glue, a callee instance, a
constraint method call (with receiver adaptation: a by-value method on a copy
type satisfies `&Self` and is called with a copy), or a constraint associated
function. `emit.py` starts from `main`, renders each `(function, type
arguments)` instance with the arguments substituted into the holes, and queues
the instances, glue and structs that rendering asks for. Since every decision
was taken at the definition, an instance cannot fail to type-check; the only
render-time error is a non-terminating instantiation (type argument depth > 24).

**C representation.** Every value is owned by one C variable. `str` is a
16-byte value `{block, offset, len}`: the block is a reference-counted
`{rc, bytes..., NUL}` (literals are static blocks with `rc = -1`); copying a
`str` increments the count, and `s[a..b]` is the same block with another
offset and length (no allocation). So a str's bytes are not NUL-terminated in
general, but a NUL always ends its block, so the byte after them can be read.
Offsets and lengths are 32 bits: a str holds less than 4 GiB. Every str with
new bytes is made by `rt_str_new`, which checks the length, computed in 64 bits
by its callers (concatenation, `strFromBytes`, so `StrBuf`, `join` and
`repeat`), against `RT_STR_MAX` (4 GiB − 1) and stops the program like out of
memory (exit 2). `tests/unit` builds with `-DRT_STR_MAX=100` to test that
without gigabytes. Sixteen bytes matter:
a 24-byte str (block, pointer, 64-bit length) is passed in memory, and made
gcc -O1 four times slower on the largest library test. `[]T` is an inline
`{data, len, cap}`; a slice `&[T]` / `mut &[T]` is `{data, len}`. `record` and `closed` are pointers to heap structs; `value
record` and `value closed` are inline structs (so `Option[Decimal]` never
allocates); a closed type is `{tag, union}`. A borrow is a pointer. Function
values are C function pointers. A closure `&fn(...)` is `{code, env}`: its
body is lifted into a C function `<instance>_cN(void* env, params...)`
rendered with the enclosing instance, and `env` is an array of pointers to
the captured variables in the enclosing frame (no allocation). A plain
function passed as a closure gets a trampoline whose `env` is the function
pointer. A constant table is a `static const` C array viewed as a slice; a
set-once global is a static plus a state byte (0 empty, 1 set, 2 dropped). A `drop` method runs before the fields are
dropped (in reverse declaration order, array elements first to last).

**Ownership and borrows.** Variable states (live/moved) are tracked per path
and must agree at joins; diverging paths do not join. Temporaries belong to the
statement and are dropped at its end, including on an early return by `try`.
By-value arguments are moved only after every argument is evaluated, so a
`try` in a later argument still drops the earlier ones. Rule 4 (frozen roots)
is kept per scope; rule 3 is generalised as *held borrows* (see below).

## Implemented

All of sections 2 to 12, 14 and 15: the grammar with ASI (no ASI inside `()`
and `[]`), all integer types including `i128` and `u128` with checked
arithmetic (exit 10) and wrapping `+% -% *%`, shifts, conversions, fractional
literals via `T.fromFraction` or `fromFraction`, operators as methods, `try`
anywhere, statement temporaries, records, value records (copy or owned-inline),
closed types (boxed and recursive, or inline), aliases, modules (`import`,
`as`, `pub`, qualified names and match arms, std prelude, cycle detection,
methods only in the type's module), constants with folding, globals with
copy types and constant initializers (including value record and variant
literals), constant tables and record/variant constants, set-once globals
(`var C: once T`), generic functions, types and methods with constraints and
inference (from arguments, then the expected type), interfaces with methods and
associated functions, function values (including generic instances, also
inside generic code), non-escaping closures (4.6), record destructuring (9.1),
`never` results (5.3), qualified variants (4.4), relative imports (5.1),
fractional literals with exponents (7.3), `extern` functions with `as "c_name"`
and `str` results (12), every built-in of section 11 and the intrinsics, and
the runtime errors of section 14. Slices (4.2a) and shared substrings (4.1) are below.

**Slices.** `&[T]` and `mut &[T]` are borrows (section 4.5), with the same
checks: a slice in a `let` freezes the viewed array's root for its scope, a
slice parameter can root a slice result (rule 5), and a slice argument is held
like any borrow, with the path of the whole array, so it overlaps every other
borrow of that array and its elements (rule 3, as 4.2a says). `xs[a..b]`,
`xs[a..]`, `xs[..b]` (and `xs[..]`) work on array places, through array
borrows, and on slices; the bounds are checked when the slice is made (exit
3). A range is a `mut` slice only where a `mut &[T]` is expected (a parameter,
a typed `let`, `swap`) or when written `mut &xs[a..b]`; `mut` needs a mutable
array (or a `mut` slice). An indexed slice result keeps its root:
`xs[0..2][i]` holds `xs` while `i` is evaluated, and `&xs[1..][0]` is a borrow
rooted at `xs`. A slice cannot be an array element: an array literal or
`array(n, v)` whose element type would be a borrow, slice or closure is an
error (a written `[]&[T]` already was). `&xs` / `mut &xs` and values of type
`&[]T` / `mut &[]T` convert to a slice where one is expected, including for generic
parameters (`sort(mut &xs)` and `sort(mut &xs[2..10])`). `len`, indexing,
element assignment, `swap`, `for x in s` and re-slicing take slices;
`append` and `pop` do not, and a slice does not convert to `&[]T`.

**Substrings.** `s[a..b]`, `s[a..]`, `s[..b]` share the bytes of `s`;
`strSlice(s, a, b)` is the same operation. An `extern` call passes a str's own
bytes when a NUL follows them (a literal, a whole string, a suffix) and
otherwise a NUL-terminated `malloc` copy, freed right after the call; a NUL
inside the bytes still stops the program (exit 12). `readFile`, `getenv` and
`parseFloat` copy what they hand to C.

**std.obx** (now `../lib/std.obx`, the prelude): `Option`, `Result`, `Unit`,
`Eq`, `Ord`, `Hash`, `Show` and their implementations for the primitive types,
`print`/`println`/`eprintln`, `Decimal`, float math methods, `StrBuf` and
string helpers, `sort`, `Map`. The other library modules (`money`, `math`,
`collections`, `strings`, `io`, `fs`, `os`, `time`) are in `../lib` too, which
is a default import root: `import "money"`. See `../lib/README.md`.

**Intrinsics** (callable anywhere unless a module declares the same name):
`strByte(s, i): byte`, `strSlice(s, from, to): str`, `strFromBytes(&[byte])`,
`strToBytes(s): []byte`, `intToStr`, `i128ToStr`, `u128ToStr`, `u64ToStr`,
`floatToStr` (shortest round-trip), `parseFloat(s, out: mut &float): bool`,
`readFile(path, out: mut &str): int` (0, or the errno), `getenv(name, out: mut
&str): bool`, `writeFd(fd, s): int`, `args(): []str`, `strFromC(p): str`,
`isNull(p)`.

## Choices where the spec is silent

1. **Held borrows (rule 3 and nested calls).** While a call is being evaluated,
   the places it has already borrowed (or will move) may not be mutably
   borrowed, assigned or moved by a later argument, at any depth. Within one
   call, a `mut &` may not overlap another borrowed or moved argument. A shared
   borrow or read of a place that an *enclosing* call holds mutably is allowed
   (the outer borrow is not used until that call starts), so
   `m.put(k, m.get(&k).unwrapOr(0) + 1)` compiles. Rule 3 alone would accept
   `use(first(&xs), pop(mut &xs))`, which frees what the first argument points
   to. The same holding applies to the left operand of an operator and the
   array of an index while the rest is evaluated.
2. A type with a `drop` method is never a copy type.
3. A method may add constraints on its receiver's type parameters:
   `fn (m: &Map[K, V: copy]) get(k: &K): Option[V]`. The call site checks them.
4. A move inside a `while` condition is an error (the condition runs on every
   iteration).
5. Only a *shared* borrow of a global in a method receiver takes a temporary
   copy; a `mut &` method on a global is an error (it would mutate the copy).
6. A fractional literal with no expected type is a `Decimal`, so `int(1.5)`
   is an error; write `let f: float = 1.5`.
7. A `match` arm binding a field as a borrow freezes the scrutinee's root only
   when that root is a variable (a temporary cannot be reached otherwise).
8. Messages: `obx: panic: msg at file:line`, `obx: index out of range at
   file:line`, `obx: leak: N live objects at exit at runtime`.
9. An untyped integer constant is folded with exact integers and range-checked
   where it is used (a typed constant at its declaration). A typed constant is
   folded in its type: an out-of-range shift count, a division by zero (also
   for floats), `MIN / -1`, `MIN % -1` or a float overflow is a compile error,
   `<<`, `+%`, `-%` and `*%` wrap and `~` is masked as at run time. A wrapping
   operator needs a typed operand (an untyped constant has no width).
10. A statement after `return`, `break`, `continue`, `panic(...)` or `exit(...)`
    in the same block is an error ("unreachable statement"). Other dead code
    (after `while true {}` without `break`) is still checked, as if reachable.
11. Operands and arguments are evaluated left to right; a place read early is
    copied to a temporary when a later operand or argument could change it (a
    call, `try`, an operator method), so `a[i] + f()` reads `a[i]` first.
    Record and variant literals evaluate their fields in source order. A
   fractional literal on the left takes the type of the right operand, which
   is found by checking that operand and discarding its code
   (`Checker.peek_type`), so the literal's `fromFraction` still runs first.
   An integer literal on the left has no code, so its order does not matter.
12. Drop glue. A *chain type* is one on a cycle of the type graph (fields and
    elements, generic instances included: `Node` and `Option[Node]` in a
    list, `A` and `B` when each holds the other, `T` with a `[]T` field), so
    its values can nest without bound. Every other type is dropped by plain
    recursive C functions (bounded by the type graph): a leaf record is one
    `free`. A chain type, or a type with a `drop` method, is dropped by
    actions on an explicit stack (`rt_dpush`/`rt_drun`), so dropping a
    10-million-node list does not overflow. An action gets the object pointer
    of a boxed value (the address of any other); it pushes what must not nest
    (fields in declaration order, so they run last to first), drops the rest
    at once, and frees its node at the end when no pushed action reads it: a
    list or tree node is one action, one indirect call. A field after the last
    pushed one, or one whose drop is quiet (no drop method in it, bounded), is
    dropped at once; the order is still that of section 9.2. For a type with
    a `drop` method, the glue pushes the field drops first, then calls the
    method; what the method still owns when it returns (its locals and the
    last statement's temporaries) is pushed too (by pointer or by parts, or
    as a heap copy, `rt_ddefer`, when an action would read the local), so it
    is dropped after the method and before the fields without nesting (SPEC
    9.4): a method that detaches its child does not recurse. One that drops a
    child before its last statement does, and overflows the stack on a long
    enough chain (exit 4). Deep recursion in user code is caught by a SIGSEGV
    handler on an alternate stack: `obx: stack overflow`, exit 4.
13. Slices: see above. A slice of a temporary array can be passed to a call
    but not kept in a `let`. A slice of a global is an error (as a borrow of
    a global is).
14. Destructuring `let P{a, c: _} = e` drops the fields that are not bound
    (`_` or not named) right away, in reverse declaration order, then
    declares the bindings in pattern order. A place of an owned type cannot be
    taken apart (it would move out of it); `move(x)` a local first.
15. Variants: a user type may not declare a top-level name that is a prelude
    variant (`Some`, `None`, `Ok`, `Err`); a user variant with such a name is
    still allowed but must be qualified, including in match arms
    (`R.Ok(v)`). `Type.Variant` also works through an alias and a module
    (`m.Shape.Circle(1)`), and through a generic alias without type
    arguments (`R.Ok(3)` with `type R[T] = Result[T, str]`): the variant's
    constructor then takes the alias's type parameters, which are inferred
    as for a call (`Checker.variant_of`, `construct_variant`).
16. Imports: `./x` and `../x` are relative to the importing file; a bare
    `x` is looked up only in the library roots (`../lib`, `-L`), and the
    error for a bare path that exists next to the file suggests `"./x"`.
17. Closures: a closure's parameters and locals may not shadow the enclosing
    function's variables (the no-shadowing rule covers the whole function).
    A closure may not return a borrow. Its captures are found by use: a
    variable is captured mutably when the closure assigns it, borrows it
    `mut`, or (for a `mut &` borrow or slice variable) writes through it or
    passes it on as `mut`. A closure
    argument holds its captures for the call (rule 3, `mut` for mutable
    captures); a `let` closure freezes them for its scope (rule 4).
    Parameter and result types are inferred only from the callee's parameter
    type once its type arguments are known from earlier arguments; otherwise
    they must be written.
18. A set-once global's `get()` is a shared borrow rooted at static data:
    it can be kept in a `let` and freezes nothing. A global of an owned type
    must be `once` (or a constant table). At exit (SPEC 9.4) the set-once
    globals are dropped importers first (a post-order walk of the imports,
    `std` first, reversed), each module's in reverse declaration order; each
    is marked dropped (state 2) before its drop glue runs, so a `get()` in any
    drop method from then on, its own included, stops the program (exit 12)
    instead of reading a half-dropped value. Copy globals (`str` and value
    records holding one) are dropped after all of them: that runs no code, so
    no drop method can see a freed one, and a value a drop method stores into
    one is still freed.
19. Compound assignment `a op= b` (SPEC 7.4): the index expressions in `a` run
    once, before `b`, and `a`'s value is read before `b` when `b` may have an
    effect; the result is stored to the place `a` names, found again after `b`
    (as `a = a + b` would), so `n += f(mut &n)` and `xs[i] += f(mut &xs)`
    compile and the index is checked again at the store. When `a` is reached
    through a slice or a borrow returned by a call (a pointer taken before
    `b`), it is held while `b` is evaluated (rule 3).
20. Passing a `mut &[T]` on (to a slice parameter, `swap`), re-slicing it as
    `mut` (`mut &s[a..b]`, a typed `let`), and a `let` of any `mut` borrow
    or slice borrow the root mutably (rule 4), exactly like `mut &x`.
21. An extern's C name may not be `main`, start with `rt_` or `obx_` (the
    runtime; `sema.CHELPERS`, the `obx_` helpers of `rt.h` for the library,
    are allowed), or have the form of a generated name: `f`, `g`, `k`, `gl`
    or `S`, digits and `_` (instances, globals, constants, glue, structs), or
    `sl` or `fn` and digits (string literals, function types). `x_` is not
    reserved: an extern `foo` is the C identifier `x_foo` bound to the symbol
    `foo` by `__asm__`, so no symbol `x_...` is defined.
22. A number literal longer than 1,000 characters is a compile error, so no
    literal reaches Python's 4,300-digit `int`/`str` limit; a fractional
    literal's scale must fit an `int` (both checked in `syntax.lex` and
    `Checker.x_frac`).
23. If and match expressions (SPEC 7.8): an if expression is `(c ? a : b)`
    in C when neither branch emits a statement, else a result variable
    declared before a C `if`; each branch stores to it before its own
    temporaries are dropped. A match expression is the match statement
    (`s_match`, `match_lits`) with an arm body that stores to a result
    variable. An integer literal branch first typed as `int` is checked again
    with the type of the other branches (the if: the then-branch is redone;
    the match: the literal's store is rewritten). A branch that moves a
    variable the other does not is an error, as for the statements; a
    variable moved by every branch is moved after. Both are primaries, so
    `if c { 1 } else { 2 } + 3` adds to the whole expression.
24. Borrows taken at the call (SPEC 7.5): `Checker.late` decides from the
    syntax of the argument (a local, possibly a borrow variable, then fields
    and indexes whose base type is an array, not a slice); such a borrow is
    not held while the later arguments run and is held (`take`, after
    checking that its root was not moved) once they are. Nothing changes in
    the C: an argument's place is a C expression evaluated with the call
    (every call in an argument is already hoisted into a temporary, and an
    index into an `ix` temporary), so it is found again then. Slice
    parameters (`&[T]`, including `&xs` passed as one) are not late. Of the
    built-ins, `append` and `replace` take their borrow after the value;
    `pop` and `swap` are unchanged.
25. Constants in match arms (SPEC 8): the arm is accepted when `x == K`
    would type-check (an untyped integer constant takes the scrutinee's type
    and is range-checked; a typed one must have exactly that type; a char
    constant is a `byte`). Duplicate values are an error for literals and
    constants alike ("the value 3 is matched twice (3 and K)"). The C stays an
    `if`/`else if` chain, not a `switch`: a `break` in an arm must leave the
    enclosing loop, and `__int128` values cannot be `case` labels.

## Known gaps

- `extern` calls are unchecked, as the spec says; a wrong prototype is undefined behaviour.
- A closure literal cannot be an index argument (`x[fn() {...}]`): there `fn`
  starts a type argument.
- Error recovery: the first error stops compilation.
- The conformance tests `run/s04_array_of_arrays` (expects `len(g[0]) +
  g[1][0]` to be 3; it is 2 + 3 = 5) and `run/s05_mod_generic_type`
  (returns a `T` as `int` from a generic method, which section 6.2 rejects at
  the definition) contradict the spec; the compiler follows the spec.

## Cost of checking generic bodies at the definition

Measured on this compiler: the definition-time-specific code is about 90
lines, about 2.5% of the Python:

- template holes and their rendering: `H`, `T`, `drop`, `dup` and `is_pod` in
  `check.py` (about 25 lines) and `render`/`hole` in `emit.py` (27 lines,
  including receiver adaptation for constraint methods);
- type parameters as opaque types in the checker: the constraint branches of
  `method_sig`/`assoc_sig` and `iface_members` (about 26 lines);
- receiver-declared type parameters and their constraints in `resolve_fn`
  (about 15 lines).

Generics as a whole (substitution, unification and inference, constraint
resolution, `satisfies`, `check_targs`, instance naming and the worklist,
termination) are about 350 lines. Checking per instance instead would have
removed the holes and the opaque-parameter branches but needed a separate
check of every instance, and errors would have pointed into library code at a
use site.
