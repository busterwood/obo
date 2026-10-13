# obx and obo

A small Oberon-derived language and its compiler, built to answer one question:
**can a language keep Oberon's simplicity and manage memory without a garbage
collector, and without Rust's complications?**

Oberon showed how much a small language and a single-pass compiler can do, but it
relies on a collector. Rust removed the collector, at the price of lifetimes,
exclusive borrows and a borrow checker that dictates program structure. We think
there is a middle way, and this repository is the experiment that tests it: the
group borrowing model (designed by Nick Smith, explained by Evan Ovadia in
[Valen's Memory Safety](https://verdagon.dev/blog/valen-group-borrowing)), built
into a Wirth-style language, with a compiler that has no AST and no intermediate
representation and writes x86-64 machine code straight into an ELF executable.

## Where things stand

There are two dialects of one language.

- **obx** is the original: garbage collected, self-hosting, with about 925 tests and
  47 mutants guarding them. Its compiler, `obx/src/obxc.obx`, is about 3,200 lines.
  Everything from "Reference: mainline obx" below describes it.
- **obo** (in `obo/`) replaces the collector with single ownership, borrowed
  references and run-time-checked handles. On the benchmarks it uses 2 to 6 times
  less memory than even an aggressively tuned collector, at similar speed, with
  small changes to source code.

The decisive test was the compiler itself. obo's compiler has been rewritten in
the obo dialect (`obo/src/obo.obo`), and it compiles every test program
and error case byte-for-byte as obo does, and itself to a fixed point. Compiling
its own source it peaks at 1.5 MB, against 2.5 MB for the same logic on a tuned
collector and 1.9 MB on an aggressive one. It is still about 50% slower.

**Decision: obo is the future of the language.** obx becomes the reference it grew
from, as the Python compiler before it did.

## The model, in one page

```
type Node = ref record { value: int; next: ?Node }
type Box = ref record { items: []Node }

fn step(b: &Box, mut n: &Node) {         // read the whole, change one part
    n.value += len(b.items)
}

fn main() {
    let b = new Box{items: new []Node(0)}   // b owns the box; the box owns its nodes
    for i in 0..3 { append(b.items, new Node{value: i}) }
    step(b, b.items[1])                     // a read-only whole and a mutable part
    println(b.items[1].value)               // 4
    let first = &b.items[0]                 // a reference remembers what it points into
    b.items = new []Node(0)                 // this frees the old nodes...
    println(first.value)                    // ...so using first is a compile error
}                                           // b, and everything it owns, is freed here
```

```
'first' may point to freed memory: something under what it points into was changed; point it again first
```

- **Ownership.** Every record and array has one owner. Assignment and passing by
  value move; owners free what they own at scope exit; `take(x)` moves out of an
  optional and leaves `nil`. Strings are immutable and reference counted.
- **References** (`&T`) borrow. They are second class: parameters and
  locals only, never stored in fields or returned. That one restriction removes the
  need for lifetimes.
- **One rule: no use after free.** Several references, mutable or not, may name
  the same object. A change invalidates only the references that may point *inside*
  what changed, and using an invalidated reference is a compile error. Rust's
  further rule, that nothing may change an object while a reference to it exists,
  is deliberately absent; it is where most of Rust's difficulty comes from.
- **Handles** (`^T`) are for back-references, graphs and shared arenas: generational
  references checked on every use, so a dangling one stops the program (exit 7)
  rather than reading freed memory.
- **Effect summaries.** Each function records which globals it might free. Because
  everything is declared before use, a callee's summary is complete at every call
  site, and only calls that might free count against the rules.
- **Single pass, kept.** Moves, narrowing and invalidation are all forward facts
  within a function. There are no annotations to write beyond `&`, `mut` and `^`.

What we deliberately leave out: lifetime annotations, exclusive borrows, `unsafe`,
and path syntax in user code. Paths are the compiler's business, not the
programmer's.

## How we work

The aim is a compiler you can trust, so the process matters as much as the code.

- **Small, verified steps.** Each change starts with tests that fail, often an
  error case that pins down a hole, then the change, then mutants: deliberate bugs
  planted in a copy of the compiler, each of which the suite must catch. A mutant
  that survives marks a gap in the tests.
- **Oracles that don't depend on a compiler**: expected output written beside the
  code, objdump for the encoder, a Python model for the fuzzer, a counting runtime
  that fails any program ending with live objects (exit 5) or freeing twice (exit 6).
- **Stages and fixed points.** Each compiler is checked against the trusted
  previous one, and must reproduce itself exactly.
- **Absence made visible.** A property that matters gets an artefact that fails
  when it is lost. Memory is an example: the port compiling itself must stay within
  a recorded live-memory budget, measured exactly by the runtime (`OBO_STATS=1`).
- **Edits by script.** Large changes are made by patch scripts that assert every
  replacement matches exactly once (`obo/tools/`), so they are repeatable and
  reviewable.
- **Problems reported plainly**, including holes in work already done. Two
  use-after-free holes in obo were found this way; one is closed, one is next.

## Direction

In order:

1. **Assignment targets across calls** (done). In `b.items[0].next = h(b)` the
   target's address is computed before `h` runs, so a call that frees it could write
   into freed memory. Assigning to a field or element from a call that might free
   is now an error; the value is computed first.
2. **One kind of reference** (done). `&T` is the only reference type. A parameter
   that changes what it points to is declared `mut p: &T`; `&mut T` is an error with
   that hint. A local reference is changeable exactly when what it borrows is.
3. **Finer paths, inferred** (done). A reference records the field or constant-index
   element it borrows, and a change invalidates only the references under the place
   it changed: changing `w.b` leaves a reference to `w.a` valid, and two mutable
   elements at known different indexes can be passed together. Changes through a
   reference are still coarse.
4. **The port's speed.** It allocates 26 MB to compile itself. Steps 1 to 3 should
   let it shed workarounds written for stricter rules (copies, index loops, storing
   temporaries first), and each one removed is also a measure of simplicity: how
   far obo code must differ from straightforward code.
5. **The move** (done). obo's compiler is the port, built by a committed seed
   (`obo/seed/obo`), with `make promote` checking the fixed point and the suite before
   replacing the seed. The mutants run against the port, and `obo-on-obx.obx` is
   retired to `obo/legacy/`.
6. **Then the language again**: methods and interfaces, and the rest of the list
   under "Not yet done", each designed to fit the single pass and the ownership
   model.

**How we will know it worked:** the compiler uses clearly less memory than a tuned
collector, at the same speed; the port reads like ordinary code; and every error
message says what to do instead.

## Finding your way

| | |
|---|---|
| `obx/src/obxc.obx` | obx's compiler, in obx |
| `obx/seed/obxc` | the trusted previous stage, which builds the current compiler |
| `obx/tests/` | obx's suite, mutants and stage comparisons |
| `obo/src/obo.obo` | obo's compiler, in obo, built by `obo/seed/obo` (promote with `make promote`) |
| `obo/legacy/obo-on-obx.obx` | obo's first compiler, in obx: retired, kept for reference |
| `obo/FORK.md` | obo's design decisions, milestones, measurements and findings |
| `obo/runtime/` | obo's runtime: no collector, leak and double-free counting |
| `obo/tools/` | the patch scripts behind each change |

```
cd obx && make && ./run_tests.sh         # obx: build and test (about 8 s)
cd obo && make                           # obo: build (needs obx built first)
cd obo && python3 tests/run_fork.py      # obo's tests, leak check always on
cd obo && python3 tests/mutate_fork.py   # obo's mutants
cd obo && python3 tests/port_test.py     # the port: tokens and encoder, against obo
cd obo && python3 tests/port_compare.py  # the port, stage B (everything), with the memory budget
cd obo && python3 tests/mutate_port.py   # the port's mutants
cd obo && python3 bench/bench.py         # memory and time against the collector
```

---

# Reference: mainline obx

A self-hosting, single-pass compiler for a small Oberon-derived language with
C-style braces, written in the Wirth tradition: no AST, no intermediate
representation, and x86-64 machine code written straight into a static Linux ELF
executable with no assembler or linker involved. The compiler is written in obx.

```
cd obx
make                         # the runtime (needs gcc), then obxc from seed/obxc
./obxc examples/shapes.obx --rt .
./examples/shapes
./run_tests.sh               # the full suite, under twenty seconds
```

## The language

```
module shapes

type Shape = closed {            // closed hierarchy: match must be exhaustive
    Circle { r: int }
    Rect   { w, h: int }
}

type Node = ref record { value: int; next: ?Node }   // ?T may be nil, T never is

fn area(s: Shape): int {
    match s {                    // s narrows to the variant in each arm
        Circle { return 3 * s.r * s.r }
        Rect   { return s.w * s.h }
    }
}

fn sum(list: ?Node): int {
    var total = 0                // local type inference
    var p = list
    while p != nil {             // p is a Node inside the loop
        total += p.value
        p = p.next
    }
    return total
}

fn main() {
    defer println("bye")         // runs at every exit from the block
    let n = new Node{value: 1}   // let = immutable binding
    for i in 0..3 { println(i, " ", area(new Rect{w: i, h: 2})) }
}
```

Implemented:

- `int` (64-bit), `byte`, `bool`, `string` (immutable bytes; zero value is ""),
  `[]T` arrays, `ref record` types, `closed` variant types, `?T` optionals.
- Arrays grow in place with `append(a, x)` and shrink with `truncate(a, n)`; every
  reference sees the change. Indexing is bounds-checked at the moment of access, so
  `a[i] = f()` is safe even if `f` grows `a`. `new []T(0)` works for any element
  type; a non-zero length needs element types that can start as zero.
- Strings index to bytes (`s[i]`), with `substr(s, from, to)`, `len`, `+`, `==`, and
  `string(bytes)` to build one from a `[]byte`.
- `byte` values take part in arithmetic as ints and widen to `int` automatically;
  narrowing needs `byte(x)`, which keeps the low 8 bits. Character literals (`'a'`,
  `'\n'`) are bytes.
- Hex literals (`0xFF`, `0xFFFF_FFFF_FFFF_FFFF` wraps to -1) and Go-precedence
  bitwise operators: `& | ^ << >>` and unary `~`. `>>` is arithmetic and shift counts
  are taken mod 64.
- Forward type declarations (`type Sym`), so record and closed types can refer to
  each other. A forward type can be used in declarations at once; its fields only
  once it is defined.
- `var`/`let` with type inference, `const` with compile-time folding, module globals
  with initialisers that may call earlier functions.
- `if`/`else if`/`else`, `while`, `for i in a..b`, `for x in array`, `break`,
  `continue`, `defer` (block-scoped, Zig style), `return`.
- `match` on a closed type (arms are variants, exhaustive unless there is an `else`)
  or on an `int` or `byte`, where arms are lists of constants:
  `match c { 'a', 'e' { ... } 10 { ... } else { ... } }`. The subject is evaluated
  once; duplicate values are errors; an integer match terminates only with `else`.
- Short-circuit `&&` and `||`, compound assignment `+= -= *=`, string `+` and `==`.
- Built-ins: `print`, `println`, `eprint`, `eprintln` (stderr), `len`,
  `append`, `truncate`, `substr`, conversions `int()`, `byte()`, and `string()`
  (of an int, giving its digits, or of a `[]byte`; of a single byte it is an error,
  since the character and the number are both plausible readings),
  `readFile(path): ?[]byte`, `writeFile(path, data, executable): bool`,
  `args(): []string`, `gc`, `gcstats`, `exit`.
- Automatic semicolon insertion at line ends, as in Go.

## Safety checks at compile time

- Non-null references by default: field access and indexing on a `?T` are rejected until a
  `!= nil` check (in `if`, `while`, `&&`, or an early `return`) narrows it.
- Narrowing is invalidated by assignment, and assigning a possibly-nil value inside a
  loop to a variable checked outside that loop is an error. Defer bodies see no
  narrowing, since they run later.
- `new` must supply every non-null reference field; non-null variables need an
  initial value; arrays of non-null references are rejected.
- `match` must cover every variant or have an `else`.
- Functions must return on every path; `let` bindings cannot be assigned.

## Architecture

The compiler, `src/obxc.obx`, is laid out in the order it is read: scanner,
x64 encoder, types and items, expressions, statements, declarations, ELF writer.

- **Scanner** with Go-style semicolon insertion. It works on bytes: columns count
  bytes and identifiers are ASCII. Token kinds are integer constants.
- **Parser and code generator interleaved**: each `term`, `relation` and statement
  emits code as soon as it is recognised.
- **Items** (`const`, `mem`, `reg`, `cond`) delay emission: a local is `[rbp-16]` until
  something forces a load, constants become immediates, and comparisons stay as
  condition codes with true/false jump lists for `&&`, `||` and `!`.
- **Fixups** resolve forward jumps, calls to forward-declared functions and the frame
  size, which is patched into the prologue once the body is compiled.
- **Registers** are allocated stack-fashion from rbx and r8 to r15; all live registers
  are pushed around every call.
- **Defer** compiles each deferred statement once as a local subroutine and emits a
  `call` at every exit from its block.
- **Global initialisers** become code chunks linked by jumps and run before `main`.
- **Memory**: a mark-sweep collector in a small freestanding C runtime. Heap scanning
  is precise (type descriptors emitted by the compiler); stack and globals are scanned
  conservatively. The contract is that every root, including the arguments of the
  runtime call itself, is on the obx stack whenever the runtime is entered; the
  collector scans only obx frames, never the runtime's own, so the contract is
  enforced rather than met by luck. The runtime is linked at a fixed address and
  reached through a jump table whose stubs check stack alignment.

Runtime errors exit with code 2 (negative array length, out of memory), 3 (index
out of range in an array, string, `substr` or `truncate`, with the line) or 4
(internal error). Division by zero is currently a hardware trap (SIGFPE).

Generated code is roughly at `gcc -O0` speed or a little better, and around 3x slower
than `gcc -O2` on call-heavy code. obxc compiles its own 3,100 lines in about 20 ms,
roughly 150,000 lines a second.

## Tests

`./run_tests.sh` runs about 920 tests in under twenty seconds (`-k name` filters,
`-v` lists every test). Details of each kind are in the docstring of `tests/run.py`.

- **Program tests** (`tests/run/`): one file per feature area, with expected output,
  exit code and stderr written as comments beside the code that produces them.
  Includes runtime traps and garbage collector tests designed to catch premature
  freeing.
- **Error tests** (`tests/errors/`): about 180 programs that must fail, each checked
  for the exact line and message. One file holds many cases separated by
  `// === name`.
- **GC stress**: every program test runs again with `OBX_GC=stress`, which collects
  at every allocation and poisons freed memory, so a missing root shows up as
  corrupted output rather than depending on when a collection happens to land.
  Normal runs use `OBX_GC=poison`. Both modes work on any obx program.
- **Encoder**: around 14,000 x64 instruction forms, every register and addressing
  mode the code generator can emit, cross-checked against objdump.
- **Fuzzing**: random integer and boolean expression programs checked against a
  Python model of 64-bit arithmetic and short-circuit evaluation
  (`--fuzz 2000` for a longer run).
- **Golden output** for the examples, and **determinism** (identical output from
  identical input).

`python3 tests/mutate.py` checks the suite itself: it plants 47 deliberate bugs in
copies of obxc one at a time, builds each with the bootstrap, and confirms the suite
fails without help from the stage tests (`--part 1/4` runs a quarter). When adding
a feature, add a mutant or two that break it; one that survives marks a gap.

The runtime also checks the calling convention on every entry: a misaligned stack
traps immediately with exit code 4 rather than crashing later.

## Self-hosting

`src/obxc.obx` is the obx compiler, written in obx: about 3,100 lines covering
the whole language, the x64 encoder and the ELF writer. It is the only compiler.
The original compiler, written in Python, is kept in `legacy/` for reference only;
nothing builds or tests with it. The test tooling is still Python (the runner, the
fuzzer's model, the mutation driver), since it needs no compiler of its own.

```
cd obx
make                                  # runtime, then obxc built by seed/obxc
./obxc examples/shapes.obx --rt .     # obxc SOURCE [-o OUT] [--rt RUNTIME_DIR]
./run_tests.sh                        # the suite, against ./obxc
make promote                          # make ./obxc the new seed (see below)
```

`seed/obxc` is a committed binary: the trusted previous stage. obxc compiles
its own source in about 20 ms.

### Changing the compiler

Tests use oracles that don't depend on any compiler: expected output, exit codes
and errors written beside the code, objdump for the encoder, a Python model for
the fuzzer, poisoned memory for the collector, and golden token dumps. On top of
those, the **stage tests** compare the compiler under test with the bootstrap on
every program, error case, token stream and encoder script, and check that it
reaches a fixed point compiling itself.

- A refactoring should leave the stage tests passing: nothing observable changes.
- A deliberate change to code generation or messages shows up as stage differences.
  Review them, then `make promote`, which checks the fixed point and the rest of
  the suite before copying the new compiler to `seed/obxc`.
- A new language feature takes two rounds: implement it without using it in
  obxc's source, and promote; then obxc can use it. Integer `match` went this way:
  first implemented, then obxc's token kinds became integer constants and its
  dispatch chains became `match` statements (`tools/intern_tokens.py` did the
  mechanical part), with the stage tests confirming the output was unchanged.

## Not yet done

Methods and interfaces, lambdas, generic
modules, result types with `try`, multiple modules with symbol files, value records,
inout parameters, a `>>>` logical shift, and debug info. Each was designed earlier
to fit the single pass.

### Future work: a peephole optimizer

The generated code is register-based, but unoptimised: a value is often stored to a
local and immediately reloaded (`mov [rbp-8],rbx` then `mov r8,[rbp-8]`), and
`sub rsp,0x0` is emitted for functions with no locals. Options, in the order we would
try them:

- **Emit-time peephole.** Before each instruction is appended, look back at the last
  one or two and rewrite them if they match. This keeps the compiler single-pass. A
  rewrite is only safe if no jump or call target falls between the instructions, so
  the compiler must record its fixup and label positions and stop rewriting there.
- **Post-pass.** A separate pass over the finished code, which must also rewrite every
  jump and call offset. Needs a relocation table the compiler does not yet have.

Rules that any rewrite must keep:

- **Keep the stores.** The runtime's collector scans the obx stack conservatively, so
  a value that lives only in a register could be missed. Only reloads may become
  register moves (`mov r8,rbx`); stores stay.
- **Keep the call sequence.** Live registers are pushed around every runtime call, and
  the stack alignment check depends on the exact stack depth at each call.

Candidate rewrites: reload-after-store to a register move; dropping the zero-size
`sub rsp,0x0`; computing a result directly in `rax` rather than copying it there.

Checks for any rewrite: the encoder test (objdump cross-check) must still pass for
new instruction forms; the stage tests will show every change in generated code as a
difference against the bootstrap, which must be reviewed and then promoted; each rewrite
gets a mutant, so the suite proves it is exercised; and the benchmarks show whether the
gain is real.

## The ownership dialect

`obo/` holds obo, which replaces the collector with single ownership, borrowed
references and handles. It began as an experiment and is now the language's
direction: see "Direction" at the top of this file, and `obo/FORK.md` for the
details.
