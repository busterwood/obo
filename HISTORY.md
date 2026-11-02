# History

This page records where obo came from and how it got here, including the
per-stage release records. Start with [README.md](README.md) for the current
state, build instructions and roadmap.
The language reference and design records remain current, not historical:
[obo/LANGUAGE.md](obo/LANGUAGE.md), [obo/FORK.md](obo/FORK.md) and
[obo/design/README.md](obo/design/README.md).

obo grew out of **obx**, the original garbage-collected Oberon-derived dialect
and compiler. obo began as an ownership fork of obx — originally
`obo-on-obx.obx`, a copy of `obx/src/obxc.obx` built by `obx/obxc` — proved the
ownership model, became self-hosting, and is now a released 0.19 compiler (the
0.19 seed is promoted) with modules,
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
- **0.9.** Lambda Stage A: non-capturing literals, structural function types and
  indirect calls. `OBO_VERSION` 0.9, symbol format 5, ABI 1.
- **0.10.** Numeric floats: exact literal/constant conversion, arithmetic and
  formatting. `OBO_VERSION` 0.10, symbol format 6, ABI 1.
- **0.11.** Lambda Stage B: explicit capture lists, including one-word float
  snapshots. `OBO_VERSION` 0.11, symbol format 6, ABI 1.
- **0.12.** The decimal scalar tier: the 24-byte value, exact `+ - * %`,
  comparisons, `decimal::div`/`quantize`/`toInt`, and display scale.
  `OBO_VERSION` 0.12, symbol format 6, ABI 1.
- **0.13.** Private-by-default record fields with per-field `pub` and opaque
  `priv`/`priviface` `.sym` slots. `OBO_VERSION` 0.13, symbol format 7, ABI 1.
- **0.14.** The decimal text API: `parse`, `format`, `canonical`, `check`,
  `exponent` and `dscale`. `OBO_VERSION` 0.14, symbol format 7, ABI 1.
- **0.15.** Compiler-known `sql`/`html` template literals and the HTML escaping
  library. `OBO_VERSION` 0.15, symbol format 8, ABI 1. The follow-up runtime
  cleanup removed the disabled collector and the `gc` builtins (16 source
  lines).
- **0.16.** Decimal storage: record fields, `[]decimal`, globals and literal
  constants. `OBO_VERSION` 0.16, symbol format 9, ABI 1.
- **0.17.** Impure interface members with the maximum synthetic summary.
  `OBO_VERSION` 0.17, symbol format 10, ABI 1.
- **0.18.** Decimal methods and interface members, including
  `Result[decimal]`, plus the interface vtable-offset and `recordParamFree`
  fixes. `OBO_VERSION` 0.18, symbol format 11, ABI 1.
- **0.19.** Decimal function values, lambda parameters/returns and 24-byte
  decimal capture snapshots. `OBO_VERSION` 0.19, symbol format 12, ABI 1. Merged
  as `147f7fc` and released; the 757,761-byte seed (sha256 `8327ca50…`) is
  promoted at `ba02031` and is the fixed point.

D5 (`lib/decimals.obx`) and D6 (the exact database read bridge,
`DecimalOrNull`/`getDecimal`, merged as `4a32742`) are library-only additions;
they change no compiler, runtime, symbol format or seed. D6 is released.

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

## Release records

These are the detailed records for each language and library stage. The README
describes only the current state.

### Decimal 0.12 (historical scalar tier)

**Historical (0.12 line).** This section records the 0.12 release; it is not the
current limit. Fields, arrays, globals and constants arrived in 0.16 (later in
these records).

Decimal is the released 24-byte exact scalar tier on `obo 0.12`, symbol format
6, ABI 1 (append-only: runtime slots 60-72; slot 59 stays reserved/baseline and
is the hosted-exit slot in the cinterop build). Source is 14,608 lines /
550,897 bytes; the compiler is 643,073 bytes, sha256 `d98d6b5f…`, and stage 2 ==
stage 3. It self-compiles at 2,194,160 bytes live (port comparison 2,194,560)
and allocates 49,033,600 bytes, under the 2,500,000 budget. `run_fork` is 1,705
tests, `module_test` 113, `port_compare` 1,701/1,701 default, and the
seed comparison was 1,535/1,535 against the then-promoted 0.12 seed (it was
1,477/1,477 against the 0.11 seed, whose decimal cases were `(0, 12)` skips). `sym_test` 44/44, `port_test` 598/598, `float_f1` 19 groups in both
register modes, `doc_check` 69 examples, `mutate_decimal` 22/22, and the
reviewed D1/D2 runner remains 32,719 C checks.

The tier supports literals, locals/parameters/returns, exact `+ - * %`, unary
minus, comparisons, display printing, explicit int conversions and
`decimal::div`/`quantize`/`toInt`, including `Result[decimal]`. At 0.12 it was a
scalar minimum: there were no decimal globals, constants, record fields, arrays,
captures, methods, interfaces or function values; no public `format`/`parse`
stdlib; no database wire adapter, `decimal(p,s)`, float interop or owned/mut
decimal; and the reviewed core keeps its strict quantize target limit of 32768.
The 40-49 ids are trap diagnostic labels only; `Result` APIs carry core
statuses (1, 3, 4, 5, 6), as documented in `obo/LANGUAGE.md` section 9.

### Field visibility 0.13

Record fields are private to the defining module unless declared `pub`. The
`0.13`/format-7 change adds mixed records, module-level access, constructor-only
external construction for records with any private field, private field types
with the closure check relaxed for them, and opaque one-word `priv`/`priviface`
slots in `.sym` that preserve layout without writing private names or types. The
exported `pf`/`pfree` summary paths may still name a private field; that leak is
accepted in v1 and means renaming such a field changes the interface hash while a
field-list-only change may not. Closed-variant fields stay public. `fs::Dir` is a
deliberate public-field v1 exception; `io::Reader`/`Writer` are private (with
`bufferedBytes`), `html::Html` is private (with `raw`/`toText`), and the
experimental DB/C wrappers hide their registry tokens while keeping explicit
raw-token escape hatches such as `buffer::wrap`/`rawToken`.

### Template literals 0.15 (released)

Compiler-known `sql`/`sqlRaw` and `html`/`htmlRaw` backtick tags are released in
0.15 and still carried by the promoted 0.19 seed. SQL holes become ordered
bind parameters in public `Sql`/`SqlArg` records; literal chunks stay raw text,
`Sql` fragments splice, `sql.copy`, `sqlJoin`, `sqlWhere`, `sqlNull` and
`sqlBytes` compose explicitly, and `[]Sql` never expands implicitly. Decimal
arguments use exact `DecimalText` bytes (display form, canonical only when the
display scale was capped; never rounded); `sqlWhere` parenthesizes every
predicate. HTML holes are context-escaped for text, quoted attributes and
allow-listed URLs; URL values are validated as assembled values (a scheme may
not span holes); `Html` fragments splice only as text, `[]Html` concatenates,
and script/style/comment/name/unquoted/srcdoc/style/`on*` holes are compile
errors. `Sql` stays a dialect-neutral text/parameter pair. `obo/lib/sqldialect.obx`
now renders PostgreSQL `$n`, SQLite `?`, SQL Server TDS `@pN`, SQL Server ODBC
`?` and Oracle `:n`; the experimental PostgreSQL and SQLite packages add
`execute_sql` and `prepare_sql`/`bind_sql`, and `db/mssql` adds a first-slice
SQL Server ODBC adapter. Oracle, a SQL Server template bridge, `SqlArg.Float`
binding and identifier/`ORDER BY` allow-lists remain later work.

### Decimal storage 0.16 (D1–D3)

**Released 0.16; earlier release.** This section records the 0.16 release; the
0.17–0.19 lines carry the storage tier unchanged.

The storage tier makes `decimal` storable without changing the ABI number. It is
one release, `0.16`/format 9/ABI 1: `.sym` gains decimal field types with
24-byte offsets, `priv24`, decimal array signatures, decimal globals and the
`hex` constant payload, so a format-8 reader must reject those files and format 9
was the only accepted input at 0.16. ABI 1 is unchanged because no existing
layout or calling convention moves; the runtime additions are appended (the
`[]decimal` slot 94 and array kind 7).

**Record fields (D1).** A `decimal` field takes 24 bytes inline in its record or
closed-variant payload, in declaration order, 8-byte aligned, with no padding or
reordering; `sizeOf` drives both the source layout and the `.sym` field-list
reader, so the two agree on every offset. An omitted decimal field is zero. Reads,
whole writes, `+=`/`-=`/`*=`/`%=`, record literals and narrowed variant reads
work directly, through `&T`/`mut &T` borrows and through `^T` handles; a
compound assignment computes into a temporary and then copies it into the field.
A field may not be assigned from a call that might free its record (`cannot
assign to a field or element across a call`). A private decimal field is written
as the opaque `priv24` slot, so its type and name do not leak but its width does.

**`[]decimal` arrays (D2).** A `[]decimal` is a pointer-free block of kind 7 with
24-byte inline elements. `new []decimal(n)` zero-fills; `len`, `append`,
`truncate`, indexing, `xs[i] op= v` and `for d in xs` work as for other arrays,
and it can be a local, field, `[][]decimal` element, parameter (`&[]decimal`,
`mut &[]decimal`), return, `Result` payload or optional payload. An index out of
range traps with `index out of range` (exit 3), read or write, including a
negative index. A `[]decimal` cannot be printed or converted with `string`, and a
decimal element cannot be taken, replaced or borrowed. The object-size limit
applies: `new []decimal(n)` above 178,956,969 traps with `obx: allocation size
overflow` (exit 2), and an `append` that must double an array already holding
2^27 decimals traps the same way.

**Globals (D3).** A decimal global takes 24 bytes of the zero-initialised global
data, so `var g: decimal` starts at zero with no initialiser and no runtime
code. `var g = expr` accepts any decimal expression, including calls and
products, and a record global may be built from a record literal with decimal
fields. Initialisers run in the entry code with dedicated hidden global
temporaries, so nested literals and decimal-returning calls do not share one
slot. Module initialisers run in import order (a dependency before its importer)
and declarations run in source order. Every read, write, argument, field,
element and return goes through `copyDecimal`; a decimal global is never a
one-word load, and its value is captured before a later operand runs. A
`[]decimal` global is accepted too, with the ordinary owned lifecycle of a
pointer-valued global.

**Constants (D3).** A decimal constant is a literal or a unary-minus literal
(`const RATE = 0.0825`); arithmetic is not folded, so `const C = 1.0 + 2.0`, a
call, another constant, `-(1.5)` or `--1.5` is refused with `decimal constants
must be a literal; arithmetic is not folded`. A constant takes no storage; each
use materialises its three words into a temporary. `-0.00` is positive zero and
keeps its written display scale. A public constant is written to `.sym` as
`const NAME: decimal = hex "<48 lowercase hex digits>"`, the 24 value bytes in
memory order. The reader rejects uppercase hex, a wrong length, a non-hex
character, reserved bits 40..63 of the third word, a sign other than 0/1, a zero
with sign 1 or a nonzero exponent, and a coefficient above `10^38-1`; a
source-less `--check` builds the value from those bytes alone.

**Status.** D4a, decimal method and interface-member parameters and
returns (including the interface vtable offset and the `pfree` mask fixes), is
released as `0.18`/format 11; D4b, decimal function
values, lambdas and 24-byte capture snapshots, is released as
`0.19`/format 12 (merge `147f7fc`, seed `ba02031`) and described below. D5, the
`lib/decimals.obx` module (`floor`/`ceil`/`trunc`/`round`/`roundAway`, `abs`/
`neg`/`sign`/`isZero`/`cmp`/`min`/`max`, `sum`, largest-remainder `allocate` and
`allocateWeighted`, and `formatGrouped`), is released with the 0.17 line as a
library-only addition; it changes no compiler, runtime, format or seed. D6, the
database read bridge (`DecimalOrNull` and `getDecimal` in each adapter), is
released (merge `4a32742`) and described below; writes already travel losslessly
as `DecimalText`, and there is no `SqlArg.Decimal` or `decimal(p,s)` in this
tier. No decimal storage or function-value stage remains; a typed database write
path (`SqlArg.Decimal`/`decimal(p,s)`) and further containers remain later, as
do `^I`, named function values and stored closures. `string(decimal)` stays a
compile error; use `decimal::format`.

### Impure interface members 0.17 (earlier release)

**Released 0.17; earlier release.** This section records the 0.17 release; the
0.18 and 0.19 lines carry the impure tier unchanged, with later symbol-format
bumps for decimal method/interface signatures and function values.

The third interface-member tier is one release, `0.17`/format 10/ABI 1. A member
is `[pure | impure] [mut] fn name(params)[: T]`; both `impure fn` and
`impure mut fn` are accepted, the marker comes before `mut` and `fn`, and
`pure` and `impure` are mutually exclusive. `impure` is contextual, so a method
may still be named `impure`; using it anywhere but on an interface member is a
declaration error.

**The maximum summary.** An impure member's synthetic summary is
`freesAll = true`, `writesHandles = true`, `pfree = 0`, with no named freed
globals: the maximum a function summary can express. No per-member or
per-implementation summary is serialized; the source parser and the `.sym`
reader rebuild it from the markers through the same `setMemberSummary`, so they
cannot diverge. A call through an impure member therefore invalidates every live
reference and handle, refuses a deep argument, a deep capturing closure, a
reference borrowed through a handle and a handle receiver, and sets the caller's
own `freesAll` and `writesHandles`, so the calling function is itself impure for
every later tier check and caller. Because the summary is the maximum, a forged
`impure` marker can only over-invalidate, which is the safe direction.

**Implementation and dispatch.** An impure member may be implemented by any
method that matches the signature, including a pure or plain one: the
declaration is an upper bound and every caller pays for it. A `pure` member still
requires a pure method, and a plain member keeps its local-tier checks and
messages; the pure and plain tiers are unchanged. There is no vtable or
code-generation change: the member's metadata drives the caller, and the vtable
still has one code address per member. `SYM_ABI` stays 1.

**`.sym` and format.** The interface line carries the canonical marker
(`impure fn ...`, `impure mut fn ...`); duplicate or conflicting markers are
structural errors, and the writer never emits the summary itself. This is a
grammar change, so `SYM_FORMAT` is 10 and `OBO_VERSION` is 0.17; the format check
runs before the body, so a 0.16 reader reports `has format 10; this compiler
reads 9; rebuild it` rather than reading the marker. The 0.17 compiler rejects
formats 6, 7, 8 and 9.

**Still to come: the handler goal (I2).** The language can now express an impure
handler for `http1server`: the application implements `Handler` with
`impure handle(req: &Request): Response`, a data-carrying response record, while
the server owns the listener and connection and the implementation owns its state
and teardown. `http1server` will be able to accept that handler once I2 lands; it
still declares its handler as a function value, and I2 changes it to this
interface. One constraint: an impure member cannot be called through an
interface stored in a record or a global, so the server must keep the handler in
a local or pass it as an `&I` parameter, not in a `Server` field or a global; I2
changes `experimental/http1server` and its design note, not the compiler. Async
libpq and a deferred response follow after I2. `^I` remains later (D4a is
released as 0.18, D4b as 0.19, D5 as the library-only `lib/decimals.obx`, and
D6 is released).

### Decimal methods and interface members 0.18 (released, previous release)

**Released 0.18; previous release.** This section records the 0.18 release; the
0.19 line carries the method and interface tier unchanged and adds D4b.
The 0.18 stage, D4a, makes a decimal a method parameter and return value,
including `Result[decimal]`, and an interface member parameter and return in
all four tiers (`pure`, plain, `mut` and `impure`). It is one release,
`0.18`/format 11/ABI 1 (merge `e47a8c8`, seed `769a359`). A decimal parameter
is a 24-byte by-value slot, and a
decimal return uses the hidden destination pointer already used by a direct
function. A decimal is still never a receiver (`&decimal` is not borrowable),
and function values with decimal parameters or returns stay refused until D4b.

**The interface call.** The push order is now: vtable, the hidden destination
pointer when the member returns a decimal, the object (parameter 0), then the
explicit arguments. That puts the destination pointer directly above the
visible parameters, where a method compiled for a direct call reads it, while
the vtable stays above it. The dispatch load is therefore the explicit argument
byte count plus 8 for the object, plus 8 more when the destination pointer is
present, not the old `8*n` word count. With no decimal return the layout and
code are unchanged.

**`recordParamFree`.** The old `len(params) - 1 - (g.offset - 16) / 8` mapping
assumed every parameter is one word. A 24-byte decimal after a `mut` reference
made the derived index too small, so the `pfree` bit and its `pf` row could be
dropped: a free-summary soundness bug, because a caller could keep a reference
into the `mut` argument across the call. The mapping now walks the signature
from the last parameter and converts the offset to a parameter index through
each type's byte size (24 for decimal, 8 otherwise). An offset that is not on
the signature is a fail-closed internal error, not a silently dropped summary.

**`.sym` and format 11.** The grammar is unchanged: the same signature text now
means decimal parameters and returns. Without the format bump a 0.17 reader
would parse the signature and miscompile it: its interface call path raises
`internal error: a decimal-returning interface or function value is not supported`
for a decimal return, and a decimal parameter would dispatch with the old
`8*n` vtable offset and corrupt the call. The change is therefore a meaning
change, and the format check runs before the body: the 0.18 reader accepts
format 11 only and rejects 6–10 before reading the body
(`has format 10; this compiler reads 11; rebuild it`). `SYM_ABI` stays 1
because the vtable shape, descriptors, argument stride and return protocol do
not move.

**Status.** D4b adds decimal function values, non-capturing lambdas with
decimal signatures and 24-byte Stage B capture snapshots; it defines the
destination-pointer and cell layout for a call through a function value. D4b is
released as `0.19`/format 12/ABI 1 (merge `147f7fc`, seed `ba02031`) and is
described below. D6,
the database read bridge (`DecimalOrNull` and `getDecimal` in each adapter), is
released (merge `4a32742`); writes already travel losslessly as `DecimalText`.
D5 is the released library-only `lib/decimals.obx`. No decimal stage remains.

### Decimal function values, lambdas and captures 0.19 (released)

D4b makes a decimal a function-type parameter and return, a lambda parameter and
return, and a capturing lambda's value. It is released as `0.19`/format 12/ABI 1
(merge `147f7fc`); `seed/obo` is the same 757,761-byte image (sha256
`8327ca50…`, promoted at `ba02031`) and the image reaches its own fixed point.
A decimal-returning value call pushes the function cell **above** the hidden
destination, so the callee keeps reading the destination at
`16 + visible parameter bytes` (`A`) on every call kind and the cell sits at
`24 + A` (at `16 + A` for a non-decimal or `Result[decimal]` return, which is
one word). The lambda environment pseudo-parameter is gone from
both the local parameter list and `FnRec.params`; the body reads the cell at
`16 + A`, plus 8 for a decimal return. A captured decimal is a 24-byte snapshot
of its three value words in the cell `[code][capture...]`, at byte offset
`8 + the earlier slot sizes`; every other slot is 8 bytes. A capture is taken
when the literal is evaluated, so a later write to the outer local does not
change it. `mut`/`move` captures, `&decimal`, named function values, stored
closures and `string(decimal)` remain out of scope. Cross-module `DecFn` aliases
and `apply` functions round-trip through `.sym`.

**Why format 12.** The function-type text itself did not change. A 0.18 reader
would accept the same text in a format-11 file, but it fails closed at
`callFnWith` with
`internal error: a decimal-returning function value is not supported`, so it
rejects the call rather than miscompiling it. The change is still a meaning
change because the call layout is new, so the format check runs before the body
and this compiler rejects a format-11 file
(`has format 11; this compiler reads 12; rebuild it`). `SYM_ABI` stays 1 because
the descriptors, argument stride and return protocol do not move.

### Decimal library D5 (released on the 0.17 line, library only)

`lib/decimals.obx` was merged as `ed20a4c` (release note `0138651`). It adds
`floor`/`ceil`/`trunc`/`round` (HALF_EVEN) and `roundAway` (HALF_AWAY),
`abs`/`neg`/`sign`/`isZero`/`cmp`/`min`/`max`, `sum`, largest-remainder
`allocate`/`allocateWeighted` (the parts sum exactly to the total at the
requested scale, up to 32768), and `formatGrouped` with a caller-chosen
separator byte. Malformed arguments return `EINVAL` (22) or the decimal scale
code; arithmetic overflow traps like `*`/`+`. It is library-only: the compiler,
runtime, symbol format and seed were unchanged. Independent review returned
REVISE on a stale import matrix and a high-scale `unitAt` overflow; both were
fixed, and the re-check was APPROVE WITH NITS. The import matrix now covers
thirteen `lib/` modules (31/31) and the stdlib suite has 24 roots.

### Decimal database read bridge (D6, library only)

D6 is the exact decimal **read** side of the experimental database adapters,
released as merge `4a32742`. It is library-only: it changes no compiler,
runtime, symbol format, ABI or seed. Each package has its own
`DecimalOrNull = closed { Null {}; Value { v: decimal } }` and a `getDecimal`
accessor: `postgres::getDecimal(rows, row, col)`,
`sqlite::getDecimal(stmt, col)` (a SQLite result is a streaming statement) and
`mssql::getDecimal(rows, col)`, the last with `mssql::DecimalType` and
`Rows.decimal_type(col)`.

`getDecimal` returns `Null{}` for SQL NULL and `Value{v: 0}` for a present zero;
NULL is never zero or empty text and wins over the storage checks. PostgreSQL
uses the `numeric(p,s)` typmod scale when `s >= 0` and exactly zero-pads a short
text (never rounds); negative scale is metadata only, and unconstrained numeric
keeps the text's dscale. SQLite treats TEXT as exact and keeps its written
display scale, reads INTEGER as an exact i64 at dscale 0 (the accepted
NUMERIC/INTEGER-affinity loss of scale), and refuses REAL as
`DEC_DB_INEXACT_STORAGE` and BLOB as `DEC_DB_NOT_DECIMAL`. SQL Server reads the
declared precision/scale from `SQLDescribeCol`, accepts only `decimal`/`numeric`,
and applies `decimal::check(v,p,s)`; `float`/`real`, text and binary columns are
refused rather than parsed, and `money`/`smallmoney` arrive as `SQL_DECIMAL`
exact text. The SQL Server live suite is now 24 sections, including
`decimal_or_null`. Writes remain `DecimalText`; there is still no
`SqlArg.Decimal`, a binary numeric path or `?decimal`.

### Seed lineage and version policy

The seed is promoted only after a compiler stage merges. The 0.19 image
(757,761 bytes, sha256 `8327ca50…`) was promoted at `ba02031` and is both
`seed/obo` and the compiler. Its immediate predecessor was the 0.18 image
(757,761 bytes, sha256 `ca513536…`, promoted at `769a359`), which built the 0.19
image once. The 0.17 image (757,761 bytes, sha256 `29ffadc8…`, promoted at
`e0b9686`) built the 0.18 image once, and the 0.15 seeds built the 0.16 line.

Version policy: additive builtin or runtime extensions use patch releases such as
the historical 0.8.1; feature stages take a minor version and, when the meaning of
a symbol file changes, a symbol-format bump. The releases were: floats `0.10`/
format 6, Stage B `0.11`/format 6, decimal `0.12`/format 6, field visibility
`0.13`/format 7, decimal text `0.14`/format 7, templates `0.15`/format 8, decimal
storage `0.16`/format 9, impure interface members `0.17`/format 10, decimal
methods and interface members `0.18`/format 11, and decimal function values and
captures `0.19`/format 12. Each compiler accepted only its own format, so old
`.sym` files are rebuilt.

### Binary sizes, runtime pages and budgets

The live-memory budget was raised from 2,500,000 to 2,750,000 bytes on
1 November 2026. The separate 30,000-byte empty-array oracle was unchanged. The
historical lambda release budget remains 2,000,000 in its own record. The 0.15
line peaked at 2,519,296 bytes direct; the decimal storage tier raised the peak
without raising the cap again, 0.17 interface-member checking added 3,152 bytes,
0.18 decimal method/interface codegen added 1,328 bytes, and 0.19 function-value
and capture codegen added 2,912 bytes.

The 0.16 runtime text added one 4,096-byte page to each produced tool, eza and
minimal binary compared with 0.15, whose runtime cleanup had removed 4,096 bytes
from each. The compiler itself grew by 61,440 bytes with the new storage code.
Those tool, eza and minimal sizes then stayed unchanged through 0.18 and 0.19
because code generation outside lambda frames and decimal method, interface and
value calls did not change. The 0.15 runtime cleanup is `b61f189`.

### Heap ceiling history

The 64 GiB ceiling update added no runtime jump-table slot, so every existing
index, including the hosted exit slot 59, was unchanged. The then-current 0.11
image `198f3268…` differed from its predecessor only because the runtime text is
embedded in every executable, and the 0.12 release `d98d6b5f…` also added only
appended slots. The decimal storage tier appended `rt_append_dec` at slot 94 and
array kind 7; the hosted build's census slot moved to 95 because slot 94 now
exists in both builds.
