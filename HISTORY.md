# History

This page records where obo came from and how it got here. Start with
[README.md](README.md) for the current state, build instructions and roadmap.
The language reference and design records remain current, not historical:
[obo/LANGUAGE.md](obo/LANGUAGE.md), [obo/FORK.md](obo/FORK.md) and
[obo/design/README.md](obo/design/README.md).

obo grew out of **obx**, the original garbage-collected Oberon-derived dialect
and compiler. obo began as an ownership fork of obx — originally
`obo-on-obx.obx`, a copy of `obx/src/obxc.obx` built by `obx/obxc` — proved the
ownership model, became self-hosting, and is now the 0.8 compiler with modules,
methods and first-class interfaces. obx remains in `obx/` as the historical
reference; it is not the current compiler.

## OBX: mainline obx (the original dialect)

A self-hosting, single-pass compiler for a small Oberon-derived language with
C-style braces, written in the Wirth tradition: no AST, no intermediate
representation, and x86-64 machine code written straight into a static Linux ELF
executable with no assembler or linker involved. The compiler is written in obx.

### Building obx

```
cd obx
make                         # the runtime (needs gcc), then obxc from seed/obxc
./obxc examples/shapes.obx --rt .
./examples/shapes
./run_tests.sh               # the full suite, under twenty seconds
```

### The obx language

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

obo later added `Result[T]`/`try`, incremental I/O, source modules,
`.sym` symbol files, methods and interfaces. Those are current language features,
documented in [obo/LANGUAGE.md](obo/LANGUAGE.md), not part of mainline obx.

### Safety checks at compile time

- Non-null references by default: field access and indexing on a `?T` are rejected until a
  `!= nil` check (in `if`, `while`, `&&`, or an early `return`) narrows it.
- Narrowing is invalidated by assignment, and assigning a possibly-nil value inside a
  loop to a variable checked outside that loop is an error. Defer bodies see no
  narrowing, since they run later.
- `new` must supply every non-null reference field; non-null variables need an
  initial value; arrays of non-null references are rejected.
- `match` must cover every variant or have an `else`.
- Functions must return on every path; `let` bindings cannot be assigned.

### Architecture

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

### Tests

`./run_tests.sh` runs about 920 tests in under twenty seconds (`-k name` filters,
`-v` lists every test). Details of each kind are in the docstring of
`tests/run.py`.

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

### Self-hosting

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

#### Changing the compiler

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

### Future work: an obx peephole optimizer

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
difference against the bootstrap, which must be reviewed and then promoted; each
rewrite gets a mutant, so the suite proves it is exercised; and the benchmarks show
whether the gain is real.

## OBO: from ownership fork to the 0.8 compiler

The ownership fork first kept obx's source shape but replaced collection with
single ownership, borrowed references and checked handles. It had its own runtime,
then became self-hosting in obo and retired `obo-on-obx.obx` to `obo/legacy/`. The
port's compiler is `obo/src/obo.obo`; it is built by the committed
`obo/seed/obo`, and `make promote` checks the fixed point and the suite before
replacing that seed.

### Version chronology

- **0.3 (P3).** Runtime metadata builtins (`readDirFd`, `statPath`, `lstatPath`,
  `readLinkPath`), `lib/fs.obx`, and `OBO_VERSION` 0.3. The seed was promoted.
- **0.4 (P5a).** Environment builtins (`isatty`, `getenv`), `lib/env.obx`,
  `io::isTty` and `OBO_VERSION` 0.4. The seed was promoted.
- **0.5 (P6a).** The terminal-size builtin `terminalWidth`, `io::termWidth` and
  `OBO_VERSION` 0.5. Its compiler recomputed itself to the same 425,985-byte
  binary (`obo` == stage 2 == stage 3, checked with `cmp`); the seed was promoted
  (sha256 `05542e89…`).
- **0.6 (P7).** Methods: receiver syntax `fn (r: T|&T|mut &T) name(...)`,
  `place.name(args)` calls, static dispatch, receiver ownership and module/symbol
  support. `OBO_VERSION` 0.6, symbol format 2, ABI 1. The 454,657-byte 0.6 seed
  (sha256 `90fcad7e…`) was promoted at `67799b1`; it is historical.
- **0.8 (P8).** First-class interfaces: owning `I`/`?I`, borrowed `&I`/`&?I`,
  dynamic dispatch and effect tiers. `OBO_VERSION` 0.8, symbol format 4, ABI 1.
  The 507,905-byte seed (sha256 `673ae7ba…`) was promoted. `^I` is not in 0.8;
  it follows lambda Stage B.

`Result[T]`/`try`, incremental I/O and modules (source imports plus `.sym` files
and `--check`) landed before and alongside those versions and remain current. The
eza work added no compiler change: P4 built the E0 core, P5b added E1 colour and
tree support, and P6b added E2 grid, width sources, UTF-8 handling and lossy
names. The differential gate reached 546/546 non-memory cases plus 5/5 memory
cases against the pinned eza 0.23.3.

### Phase record

The roadmap that led to 0.8, as it stood at that release:

| Phase | Outcome |
|---|---|
| 0. Ownership compiler, self-hosting fixed point | done |
| 1. `Result[T]`, `ok`/`err`, `try`, consuming `match` | done |
| 2. Incremental I/O: `openFile`/`readFd`/`writeFd`/`closeFile`, standard fds, `Reader`/`Writer` | done |
| 3. `cat`, `wc`, `sort` and the GNU differential gate | done |
| 4. Multiple modules | done: Stage 1 source imports, Stage 2 `.sym`/`--check`; the tools and their libraries are module roots |
| 5. Stdlib modules and the `eza` core | done: the ten `lib/` modules, the `eza` core E0/E1/E2 and the `wc`/`sort` performance work, with their differential gates |
| 6. Methods | done: receivers, static dispatch, receiver ownership, module/`.sym` support (0.6) |
| 6a. Interfaces | done: owning `I`/`?I`, borrowed `&I`/`&?I`, dispatch, effect tiers, globals; compiler 0.8, symbol format 4 |
| 6b. Lambdas | next: Stage A 0.9, Stage B 0.10, symbol format 5; `^I` follows Stage B |
| 7. Peephole/optimizer audit | evidence-gated |
| 8. More tools: `fd`, `hexyl`, `tokei`, `jq` | later |

### Historical measurements

The compiler source grew from 4,552 lines at the early port stage to 11,027 lines
in the 0.6 tree. The 0.8 interfaces source is 12,056 lines / 454,332 bytes.
Total allocation grew with it, while the live peak stayed within the 2 MB budget.
The recorded progression was 1,244,480 bytes at the Phase 3 merge, 1,364,240
after the Stage 1 module context, and 1,634,720 after Stage 2's symbol-file
writer and reader. The size fixes measured 1,636,176; P3's metadata builtins
1,642,416; P5a's environment builtins 1,645,424; P6a's terminal-size builtin
1,646,816; P7's methods 1,728,768. Interfaces brought it to 1,945,680, still
54,320 under the budget, so no slot was cut and the budget was not raised.

The earlier 12.1 MB allocation and 30 ms figures in the direction record below
describe the smaller pre-Result, pre-I/O compiler. The old 493,280-byte compiler
and seed, the tool binaries 108,256/108,256/112,352, and the earlier 90,848-byte
minimal figure are historical. The minimal executable measured before the
zero-run fix was 95,968 bytes; that fix removed a 67,295-byte zero run from every
produced executable (the tool `cat` lost 4,096 more because its code crossed a
page boundary).

Whole modules are compiled into each tool, so the sizes track the imported module
set: the pre-P1 tree (`265564c`) measured cat 36,865 / wc 40,961 / sort 45,057,
the P1+P2a base (`ec244e0`) measured 49,153 / 49,153 / 53,249, and the merged
tree measured 40,961 / 45,057 / 69,633. P2b's net effect on that base was
cat -8,192, wc +4,096, sort 0, with byte-identical observable output; wc-fast and
the sort rewrite account for the later changes.

The eza gate history: P4 measured 272/272, `--memory` 2/2, binary 126,977 bytes,
10,000-entry live peaks 2,185,008/2,184,384 bytes and max RSS 2,432 KiB. P5b
re-measured E1 at 396/396, `--memory` 4/4, binary 172,033 bytes (sha256
`6f0e8472…`), peaks 2,189,984/2,189,360/2,190,112/2,189,584 bytes and 2,560 KiB
max RSS. P6b measured E2 at 546/546 non-memory, `--memory` 5/5, binary 212,993
bytes (sha256 `53133d23…`), peaks 2,190,032/2,189,408/2,190,160/2,189,632/
2,189,808 bytes and 2,560 KiB max RSS, with the cap unchanged.

For historical comparison, obx's collector-based compiler is 3,222 lines and
172,768 bytes; it compiles its own 88,731-byte source in about 0.01 s and peaks at
8,832 KB resident. obo's source is much larger and carries more checks, so wall
time is not like for like; the memory result is the point: about 1.24 MB live
against 8.8 MB resident at the early comparison.

### Historical benchmark sample (0.5 / 0.6)

This is the recorded 0.5 / sym-format 1 CLI sample. obo 0.6 added methods at
compile time only, and the 0.5 and 0.6 seeds built byte-identical benchmark and
tool binaries, so the runtime rows still described 0.6. The compiler itself is
now 0.8, but the timings below are the old runtime sample. They are historical,
not the current measurement. The current sample is in
[PERFORMANCE.md](PERFORMANCE.md) and
[obo/bench/cli/README.md](obo/bench/cli/README.md).

| Workload | obo | Comparison |
|---|---:|---:|
| Process startup | 0.20 ms | Python 11.0 ms (54.6x slower) |
| `fib(32)` recursive | 10.0 ms | Python 206.4 ms (20.6x slower) |
| Sum below 100,000,000, explicit loop | 21.8 ms | Python 7,343 ms (337x slower) |
| Sieve of Eratosthenes to 10,000,000 | 127.4 ms | Python 2,474 ms (19.4x slower) |
| `wc -lwc`, 110 MB | 124.8 ms | Python 454.9 ms (3.65x slower); GNU 972.0 ms (7.79x slower) |
| `sort`, 1,000,000 lines | 158.7 ms | Python 545.3 ms (3.44x slower); GNU 233.6 ms (1.47x slower) |
| `sort -n`, 1,000,000 lines | 197.3 ms | GNU 398.7 ms (2.02x slower); Python 477.9 ms (2.42x slower) |
| `eza`, 10,000 names | 27.8 ms | pinned eza 57.0 ms (2.05x slower); Python 15.9 ms and GNU `ls` 6.49 ms are faster |

### Direction recorded during the port

In order:

1. **Assignment targets across calls** (done). In `b.items[0].next = h(b)` the
   target's address is computed before `h` runs, so a call that frees it could write
   into freed memory. Assigning to a field or element from a call that might free
   is now an error; the value is computed first. The same holds for compound
   assignment, `append`, `truncate`, `replace`, and an index whose index calls such a
   function (LANGUAGE.md section 12).
2. **One kind of reference** (done). `&T` is the only reference type. A parameter
   that changes what it points to is declared `mut p: &T`; `&mut T` is an error with
   that hint. A local reference is changeable exactly when what it borrows is.
3. **Finer paths, inferred** (done). A reference records the field or constant-index
   element it borrows, and a change invalidates only the references under the place
   it changed: changing `w.b` leaves a reference to `w.a` valid, and two mutable
   elements at known different indexes can be passed together. Changes through a
   reference are still coarse.
4. **The port's speed.** It allocated 28.6 MB to compile itself, and now allocates 12.1 MB
   (measured below). Steps 1 to 3 should let it shed workarounds written for stricter rules (copies, index loops, storing
   temporaries first), and each one removed is also a measure of simplicity: how
   far obo code must differ from straightforward code. A change through a handle now
   frees what references inside something point into, and the compiler checks that (done);
   no rewrite of the port's own code was needed to satisfy the rule. A reference's binding is
   now kept for each path, so a reference re-pointed on one branch is checked on every path (done; `obo/FORK.md`, "Reference bindings per path").
5. **The move** (done). obo's compiler is the port, built by a committed seed
   (`obo/seed/obo`), with `make promote` checking the fixed point and the suite before
   replacing the seed. The mutants run against the port, and `obo-on-obx.obx` is
   retired to `obo/legacy/`.
6. **Then the language again**: modules (done: source imports, `pub`,
   `alias::name`, `--deps`, `.sym` symbol files, `--check` and separate checking),
   methods (done in 0.6), interfaces (done in 0.8), and the rest of the roadmap
   (lambdas next), each designed to fit the single pass and the ownership model.

**How we will know it worked:** the compiler uses clearly less memory than a tuned
collector, at the same speed; the port reads like ordinary code; and every error
message says what to do instead.

Measured (item 4, after items 1 to 3): compiling each compiler's own source takes
about 0.01 to 0.02 s in both. The obo port (4552 lines) had a live peak of 1.3 MB and
allocated 28.6 MB before the allocation work; after it, 1.05 MB and 12.3 MB (FORK.md,
Phase 4). The cheaper code of Phase 1 brought the total to 12.1 MB and moved the peak to
1.08 MB, reached now while the code buffer grows (FORK.md, Phase 1); the checks across calls
brought it to 1.10 MB; loop inversion (one jump per iteration instead of two, FORK.md) made
the tight loop 7% faster and left the live peak at 1.17 MB. The checks added since raised it
to 1.27 MB; code kept in fixed-size chunks, the tables freed before the image, the runtime
read after the compile and shared derived types brought it to 0.97 MB (FORK.md, "Peak memory"). On main, with Phase 2's register allocation, the live peak was 1.06 MB; Phase 2 accounts for the difference. Moving the function-only fields of `Sym` into a per-function record (`FnRec`) brought it to 0.95 MB (954,912 bytes). obx's compiler (3222 lines), with the
collector, peaks at 8.8 MB. Wall time is about 30 ms, down from 33 ms. Also done:
eight loops that only walked an array now borrow it and loop with `for` (the index form was a workaround for the walk rule). The loop over `globals` at exit now
walks by borrowing too. It was blocked by two false positives: a self-call counted as
freeing anything (fixed in the checker, `run/globals.obx`, `errors/references.obx`), and
the `rtCall`/`rtCall1` cycle through a forward heading (broken in the source). Effect
summaries across mutually recursive groups remain a checker limit. The walks over `f.fnRec.freed`
and `t.variants` now borrow through their handles and loop with `for`. The rule, in one
sentence: a reference borrowed through a handle is invalidated by any change, through a
handle or a handle temporary included, and by any call that might free, and cannot be passed
to such a call. Index loops remain over `f.fnRec.pmodes`, `f.fnRec.fixups`, `t.fields` and `rec.fields` (each reached
through a handle) and over the globals `code` and `ownedLocals`; this change did not rewrite them.
