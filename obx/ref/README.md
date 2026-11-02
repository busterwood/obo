# obx/ref: the reference compiler

The draft-1 obx language (`../SPEC.md`, sections 1 to 12, 14, 15) compiled to C
by Python, then to a binary by gcc.

    python3 obxc.py file.obx                  # writes ./file next to the source
    python3 obxc.py file.obx -o out           # names the binary
    python3 obxc.py file.obx --run -- a b     # compiles to a temp dir and runs it with arguments
    python3 obxc.py file.obx --emit-c         # prints the generated C
    python3 obxc.py file.obx -L libroot       # another root for `import` paths
    python3 run_tests.py                      # own tests (tests/run, tests/error)
    python3 run_tests.py --conformance        # also the conformance suite in ../tests

A compile error prints `file:line: message` and exits 1 (one error, then stop).
gcc runs with `-O1 -w`. An `extern "lib"` links `-llib`, or the library by its
full path when only `libX.so.N` exists (no dev symlink); `"c"` adds nothing.
Run a program with `OBX_CHECK=1` to make a leak (live objects at exit) exit 5.

## Architecture

| File | Lines | Role |
|---|---:|---|
| `syntax.py` | 790 | lexer (with the ASI rule) and parser to `N` nodes |
| `sema.py` | 750 | modules, imports, scopes, types, type parameters and constraints, `satisfies`, constants |
| `check.py` | 1,890 | checks every function body once and writes it as a C template |
| `emit.py` | 265 | renders templates once per instance, drop/dup glue, struct layouts |
| `obxc.py` | 100 | driver |
| `rt.h` | 300 | the C runtime: allocation counter, traps, str, arrays, checked integers |
| `../lib/std.obx` | 1,500 | the prelude (ordinary obx code; moved to `lib/` in stage 3) |

Python total: about 3,800 lines.

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
pointer to a reference-counted `{rc, len, bytes, NUL}` (literals are static with
`rc = -1`); copying a `str` increments the count. `[]T` is an inline
`{data, len, cap}`. `record` and `closed` are pointers to heap structs; `value
record` and `value closed` are inline structs (so `Option[Decimal]` never
allocates); a closed type is `{tag, union}`. A borrow is a pointer. Function
values are C function pointers. A `drop` method runs before the fields are
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
literals), generic functions, types and methods with constraints and
inference (from arguments, then the expected type), interfaces with methods and
associated functions, function values (including generic instances), `extern`
functions, every built-in of section 11 and the intrinsics, and the runtime
errors of section 14.

**std.obx** (now `../lib/std.obx`, the prelude): `Option`, `Result`, `Unit`,
`Eq`, `Ord`, `Hash`, `Show` and their implementations for the primitive types,
`print`/`println`/`eprintln`, `Decimal`, float math methods, `StrBuf` and
string helpers, `sort`, `Map`. The other library modules (`money`, `math`,
`collections`, `strings`, `io`, `fs`, `os`, `time`) are in `../lib` too, which
is a default import root: `import "money"`. See `../lib/README.md`.

**Intrinsics** (callable anywhere unless a module declares the same name):
`strByte(s, i): byte`, `strSlice(s, from, to): str`, `strFromBytes(&[]byte)`,
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
   where it is used (a typed constant at its declaration).

## Known gaps

- No stack-depth check: deep recursion crashes (SIGSEGV) instead of a clean exit.
- `extern` calls are unchecked, as the spec says; a wrong prototype is undefined behaviour.
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
