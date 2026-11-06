# History

This page records where the project came from: mainline **obx** (the original
garbage-collected dialect), the **obo** ownership fork and its per-stage release
records, and the **new obx**: obo's ownership model with generics, in
[obx/](obx/README.md), whose first stages are the last chapter. Start with
[README.md](README.md).

obo grew out of mainline obx, the original garbage-collected Oberon-derived
dialect and compiler. obo began as an ownership fork of obx — originally
`obo-on-obx.obx`, a copy of `obx/src/obxc.obx` built by `obx/obxc` — proved the
ownership model, became self-hosting, and reached a released 0.19 compiler with
modules, methods and first-class interfaces. On 2026-11-02 the project
restarted as the new obx with generics, and obo 0.19 was retired.

Both predecessors are archived: mainline obx in `archive/obx/`
and obo in [archive/obo/](archive/obo/README.md) (its language reference
[LANGUAGE.md](archive/obo/LANGUAGE.md), design record
[FORK.md](archive/obo/FORK.md) and [design notes](archive/obo/design/README.md)).
Paths below are as they were when written: mainline obx's `obx/...` is now
`archive/obx/...`, and `obo/...` is `archive/obo/...`.

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
documented in [obo/LANGUAGE.md](archive/obo/LANGUAGE.md), not part of mainline obx.

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
[PERFORMANCE.md](archive/obo/PERFORMANCE.md) and
[obo/bench/cli/README.md](archive/obo/bench/cli/README.md).

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

## The new obx (2026-11-02 onwards)

obo 0.19 worked, but every new kind of value (decimals, then interfaces, then
lambdas) had to be wired by hand through the whole compiler. On 2026-11-02
the project restarted: keep obo's ownership model, add generics from the
first day, and make Decimal, Money, Option, Result and the containers
ordinary library code ([the decision](obx/design/generic-core-rethink.md)).
The original garbage-collected obx moved to `archive/obx/`, and the new
language took its name. The stages are in [obx/PLAN.md](obx/PLAN.md); the
first six were done on 2026-11-02, the first day.

### Stages

1. **The spec.** [SPEC.md](obx/SPEC.md) draft 1, about 580 lines, about the
   size of obo's core guide; 931 lines now, after the decisions below.
2. **A Python reference compiler** (`ref/`, obx to C, 3,800 lines) settled
   the design quickly: 387 conformance programs and 77 of its own tests.
3. **The library** (`obx/lib`): 9 modules, 3,300 lines at first, written in
   obx with no compiler change but a few `obx_*` C helpers. Its pain points
   became the first language backlog.
4. **The self-hosted compiler** (`obx/compiler`): a port of `ref/`, module by
   module (lexer and parser, sema, the body checker and C emitter), checked
   mechanically: identical `--dump-ast`, `--dump-types` and C on all 2,427
   test files and tens of thousands of mutants. It reached its bootstrap
   fixed point the same afternoon, and its own C became the checked-in seed,
   so a build needs only gcc.
5. **The native backend, then the ports.** An x86-64 backend writing ELF
   executables without gcc: a freestanding runtime (`native/rt.c`, raw
   system calls), an IR, instruction selection, an encoder and an ELF
   writer, with static and then dynamic linking for C libraries (libm,
   sqlite3, libpq). The natively built compiler builds itself to the same
   fixed point. Then eza (1,600 lines against obo's 3,482, passing obo's
   golden suite on both backends) and the SQLite and Postgres libraries,
   with no C shims, about half the size of obo's.
6. **Retirements.** `ref/` went to [archive/obx-ref/](archive/obx-ref/README.md)
   and obo 0.19 to [archive/obo/](archive/obo/README.md) on the evening of
   2026-11-02; obx's own compiler is the only one under development.

### Red-team rounds

Five rounds, each a separate review that wrote adversarial programs against
the spec, ran them under ASan, UBSan and the leak check, and reported
findings with a minimal program each. The findings were fixed before the
next round, and every corpus stays in the full gate (`obx/redteam/`):

| Round | Reviewed | Findings |
|---|---|---|
| 1 | the reference compiler and draft 1 | 25, with spec decisions for the fixes |
| 2 | slices, shared substrings, the str layout | 12 (5 high) |
| 3 | closures, set-once globals, destructuring, imports | 14 |
| 4 | borrows taken at the call, `if`/`match` expressions | 6, plus 5,180 generated A/B pairs and 12,000 mutants |
| 5 | the language batch | 3 (compound assignment after a move, a global str passed to C, output at exit) |

### The language batch

Writing the compiler, the library and eza in obx showed where the language
chafed. Fourteen changes were made in one batch on 2026-11-02, among them
multi-variant `match` arms, `s[i]` on `str`, built-in `==` on fieldless
closed types, raw strings, `var` receivers, the `Option` and `Result`
helpers, and `return`, `break` and `continue` as a whole arm of an `if` or
`match` expression. The library and eza were then simplified with them.

### Performance and one IR (2026-11-02 to 2026-11-03)

Three performance passes on the native backend: locals in registers and
inlined runtime checks; small functions in place and results built in their
destinations; then registers by live ranges and operands in place, which
cut 9% of the self-build's instructions and 13% of eza's. At first the
native backend read the C that the C backend wrote back in (`cfront`), a
deliberate stepping stone. On 2026-11-03 the checker began to build trees
that are lowered straight to the IR; C is printed from the IR, and `cfront`
was deleted. The C and every native executable stayed byte-identical over
2,926 programs. The figures are in [PERFORMANCE.md](PERFORMANCE.md).

### Concurrency decided (2026-11-03)

[obx/design/concurrency.md](obx/design/concurrency.md): stackful coroutines
on one thread per process, with no `async` colouring; futures, promises and
a `Shared` value as the built-ins, not channels; several cores through
several processes; and no threads, a shared-nothing decision taken for
performance as much as for safety. Not yet built.

## The performance record, 2026-11-03 to 2026-11-06

PERFORMANCE.md as it stood at `05cdac6`, before it became a report of the
current numbers only. It holds the native backend's passes (the progress
tables, the passes tried and shelved, how a pass is judged), each section's
earlier figures and what changed them (keyed hashing, Map's tagged index,
code placement, the math port, the Decimal work, the gcc move-width flags),
and the obo record. Each figure carries its own date or commit; the tables
were last measured on 5 November 2026 at `d14a04f` unless they say
otherwise. The current numbers are in [PERFORMANCE.md](PERFORMANCE.md).

obx builds a program in two ways:

- **native**: `obxc --native`, the compiler's own x86-64 backend. It writes an
  ELF executable directly, with no gcc and, unless the program calls C
  libraries, no libc.
- **via C**: `obxc`, which writes C and runs `gcc -O2`.

Both are built from the same IR and must behave identically; the gate checks
that on every test program. This page measures what each costs. Every table
was measured again on 5 November 2026 at `d14a04f` (the math functions, the
borrow-class types and TDS stage T2 all merged), on a quiet machine, unless
it says otherwise; the Benchmarks table was measured again on 6 November at
`38e114e` (Decimal, maps, code placement and the gcc move-width flags merged).
The native backend's progress tables, the passes tried
and shelved, the Math section's before-and-after figures and the obo record
are history, each with its own date or commit. The numbers are
host-dependent: use them to compare the columns, not as portable constants.

### Host and method

- AMD Ryzen 7 8745HS (16 threads), WSL2, Linux 6.18, gcc 13, glibc 2.39.
- Wall clock, best of N runs (11 for the benchmarks and the compiler, 9
  for eza), warm cache, output to `/dev/null`. Timings vary by 5–10%
  between runs on this machine; below about 50 ms, process start-up is part
  of the figure.
- Every row checks its outputs first: both builds of each benchmark print the
  same result and are leak free under `OBX_CHECK=1`, and both eza builds write
  byte-identical listings.

### Benchmarks

`python3 obx/bench/bench.py -n 11` ([obx/bench/](obx/bench/README.md)):

| benchmark | via C | native | native/C | what it does |
|---|---:|---:|---:|---|
| fib | 327 ms | 363 ms | 1.11 | recursive calls |
| loop | 270 ms | 215 ms | 0.80 | two overflow-checked operations per iteration |
| array | 102 ms | 90 ms | 0.88 | checked indexing |
| alloc | 119 ms | 61 ms | 0.51 | 20 million records allocated and freed |
| lists | 37 ms | 29 ms | 0.77 | linked list build, sum, drop |
| strings | 79 ms | 42 ms | 0.53 | concatenation and `toStr` |
| trees | 24 ms | 19 ms | 0.80 | binary trees |
| tokens | 18 ms | 12 ms | 0.65 | byte scanning and hashing |
| sieve | 187 ms | 133 ms | 0.71 | the sieve of Eratosthenes to 10 million, ten rounds |
| maps | 183 ms | 185 ms | 1.01 | `Map[str, int]` with short and long keys, `Map[int, int]` (keyed hashing) |

Native fib runs the same 4,305M instructions as after the nineteenth pass.
On 5 November it took 1,855M cycles, against 1,550M then, because the math
functions had moved its code (code placement, below); with functions at
multiples of 64 it takes 1,690M wherever its code falls (400 ms -> 363 ms).

The sieve's 187 / 133 ms against 170 / 116 on 5 November is the machine,
not obx: the instructions are the same (native 1,414M), and the plain C
sieve, whose code did not change, went from 125 ms to 142 ms in the same
run; native and plain C now both take about 600M cycles.

The sieve also has a hand-written C version with no checks
(`obx/bench/c/sieve.c`, `gcc -O2`, bench.py's "plain C" column): 142 ms in
the same run. Native obx runs as many instructions as it (1,414M) and about
as many cycles (497M against 508M, best of 11 under `perf stat`); through C
obx runs 1,428M and 741M, because gcc keeps the overflow and bounds checks
the native backend proves away (main's native build: 3,042M and 1,002M).

- **Native is ahead on allocation and strings** (alloc, lists, strings,
  tokens; trees narrowly). Since the fifth pass small functions and glue
  are inlined before code generation (lists: `push` and the list's drop in
  the loop). Its freestanding runtime has a size-class
  allocator with no locks or libc bookkeeping, and the string and index
  checks are generated inline.
- **Tight loops are now native's too** (loop, array; fib is 1.11, see code placement below). Since
  the sixth pass the native backend tests at the bottom of loops and
  aligns their bodies, reads array headers once per loop, and drops the
  bounds and overflow checks that conditions, passed checks and induction
  variables prove cannot fail ([design/native.md](obx/design/native.md)
  sections 4 and 8). gcc does more (it unrolls, schedules and
  strength-reduces); obx's checks stay where they can fail, so array keeps
  one bounds check per element (its bound is unrelated to the array's
  length). Before the pass these were the widest gaps (1.59 and 1.67).
- **The sieve is at plain C's speed** since the seventh pass: the inner
  loop `j = j + i` keeps no bounds or overflow check (j stays at least its
  start since i >= 1, and `j <= n` with the array's length n + 1 proves the
  index), the fill of `array(n + 1, true)` is `rep stosb`, the count of
  primes has no branch (`cmp; sbb`, as gcc's) and no overflow check (a
  counter grows at most once an iteration), the inner loop sits inside one
  cache line, and the native heap keeps the freed 10 MB block for the next
  round instead of faulting its pages in again.
- **Keyed hashing costs maps 6-10%** (branch `hashfrozen`). `str`,
  `i128`, `u128` and `Decimal` hash with SipHash-1-3 under a per-process
  key, and `Map` mixes a per-process seed into its slots (lib/README.md,
  "Keyed hashing"); before, `str` was FNV-1a. maps (200,000 keys of each
  kind, put, four rounds of lookups, misses) went from 242 to 256 ms via C
  and 320 to 351 ms native (bench.py -n 11, the same run). Under `perf
  stat` (least cycles of 5): native 2564M instructions and 1327M cycles to
  3669M and 1456M, via C 1835M and 953M to 2180M and 1011M. 2 million
  hashes of a 6-byte key: native 109M to 186M cycles, C 91M to 125M; of a
  60-byte key native 466M to 511M, C 420M to 283M (SipHash takes 8 bytes a
  step, FNV one). The native code read each byte through a checked
  `strByte`, 8 per word. Branch `maphash` adds the internal intrinsic
  `strWord(s, i)` (8 bytes as a little-endian u64, one range check per
  block; inline `mov r64, [base+idx]` natively, memcpy in C) and
  `sipKeyedStr` uses it for its blocks (hash values unchanged). maps
  (`perf stat`, best of 5): native 2671M instructions and 1190M cycles to
  2218M and 1159M, via C 2187M and 998M to 1785M and 908M; bench.py -n 11:
  246 to 242 ms via C, 301 to 291 ms native (1.22 to 1.20). The cycle gain
  is small because the instructions removed were cheap (IPC 2.24 to 1.91);
  the tail (under 8 bytes) is still byte by byte. Elsewhere the cost is in the noise: the self-build 1991M to 1980M
  cycles (4891M to 5018M instructions), eza `-T` 210M to 209M native and
  180M to 181M via C, tokens unchanged; compiling the compiler with the
  gcc-built `obxc --native`, 1662M to 1695M cycles (2%; `sipKeyedStr` is
  1.9% of its samples). Two compiler changes came with it: a shift of a
  `u64`, `u32` or `byte` by a constant below its width is a plain C
  operator (it was a runtime call, which gcc could not fold into a rotate),
  and the native backend emits `rol` for `x << k | x >> (64 - k)`.
- **Map's index holds a hash tag** (branch `mapfast`). maps went from
  245 to 182 ms via C and 292 to 188 ms native (bench.py -n 11, the same
  run; 1.19 to 1.03); under `perf stat` (least cycles, interleaved) via C
  1,764M instructions and 921M cycles to 1,855M and 675M, native 2,218M and
  1,153M to 2,151M and 703M. Three changes, each measured alone:
  - `Map`'s index slots are `u32`, an entry number with 32 - b bits of the
    mixed hash above it (2^b slots), so a probe skips other keys' slots
    without loading their entries, and the index is half the size: via C
    947M to 699M cycles (-26%), native 1,172M to 966M (-18%); more
    instructions (1,764M to 1,961M via C), fewer misses. A Map now holds at
    most 2^31 entries (lib/README.md).
  - `sipKeyedStr` is a plain block loop, its tail one word read (strings of
    8 bytes or more) and its last rounds written out: via C 685M to 646M
    cycles (-5.7%), native 962M to 939M; 60-byte keys hashed natively 502M
    to 415M.
  - The native heap's block header is 8 bytes, not 16, and its classes
    16k + 8 bytes, so a 64-byte string with its header fills 72 bytes, not
    80 (design/native.md, the heap): native maps 926M to 709M cycles
    (-23%; its long keys had missed the cache four times as often as C's),
    alloc -1.5% instructions.
  Tried and dropped (cycles within the noise, under 3%): `str`'s `eq` and
  `hash` taking `&str` (no copy on a lookup; -44M instructions via C, -49M
  native); the native backend computing `x = x op y` and rotates of a
  register local in place (a SipHash round 14 instructions, not 26; native
  maps 2,151M to 1,923M instructions but 703M to 701M cycles, the hash of
  60-byte keys -6% cycles; reverted, kept in history); scaled indexes by
  `lea`/`shl` instead of `imul`, and no range check on `x >> (n & 63)`
  (-30M instructions via C, -8M native). The probes wait on memory: what
  is left is the misses on the keys' strings and the entries.
- **Code placement matters.** Native fib runs at 1,547M, 1,690M or 2,020M
  cycles with identical instructions, depending only on where its code
  falls relative to 64-byte boundaries. The generated code starts at a
  multiple of 64 and every function at a multiple of 16, so a change in the
  runtime's size no longer moves it, but a change in the functions before
  it still can: fib's gain and trees' loss in the fifth pass (8% each way)
  are placement alone. Before the alignment fix, merging the concurrency
  runtime had silently cost fib 20%. The same happened twice more at the
  C1b merge, with the same instructions: native alloc lost 26% when the
  event loop moved the runtime's allocator, and fib via C 11% when `rt.h`
  grew. The native runtime and gcc's builds of programs now put functions
  at multiples of 64 (`-falign-functions=64`), which restored both.

  The cure was then checked by moving the code: `python3
  obx/tools/placement.py` builds a benchmark eight times with its code
  shifted by 0, 8, ..., 56 bytes (native: `OBX_PADB=n` puts n bytes before
  every function's alignment, as growth of the code ahead would; C: gcc's
  assembly with `.nops n` after each function's label) and takes the best
  of 7 cycles of each. Before, with native functions at multiples of 16 and
  C without loop alignment, the spreads (max over min) were: native fib
  9.9% (1,690M or 1,855M), sieve 5% (maps and trees 5-6%); C fib 22%, loop
  60% (1,253M or 2,009M, the loop moving across a 32-byte block), sieve
  14%, maps 39%. Native functions now start at multiples of 64
  (`FUNC_ALIGN`, 1% of a program's size: the compiler 2,927,673 to
  2,956,345 bytes, eza 242,152 to 246,248, an empty program and hello
  unchanged) and gcc gets `-falign-loops=32` (also in the runtime's
  flags), which puts every loop head at a multiple of 32 at no cost in
  cycles. Native fib: 0.0% (1,690M everywhere), loop, array, alloc 0.2-0.8%,
  trees 2%, sieve 4%; C loop 0.2% (1,253M), alloc and array under 0.5%,
  sieve 2%. Left: C fib still moves by 22% when the code inside the function
  moves (its entry is aligned, but a 100-byte function's jumps and calls fall
  where they fall; the build we ship is at the fast end, 1,522M), and maps
  (22%, both backends) and trees via C (5%) vary by the same amount
  between runs of one binary on a loaded machine: their branch and cache
  miss counts are the same at every shift, so it is not placement. Loop heads
  and function entries are aligned; the jumps inside a function are not
  (gcc's `-falign-jumps` and `-falign-labels` were not tried: they run nops
  on the hot path).

### Regex

`python3 obx/bench/regex/bench.py -n 7` ([obx/bench/regex/](obx/bench/regex/bench.py)),
the benchmark set of [design/regex.md](obx/design/regex.md) section 4. The corpus is 3.2 MB written by
`gen_corpus.py` from a fixed seed (prose with the names the patterns look
for, a log, C-like source; ASCII). Each driver times its own searches after
reading the file: the best of 5 searches, best of 7 runs, in ms. "Matches"
is the same for every driver (the script checks); `(a*)*b` runs over
100,000 `a`s. Go is go1.26.4's `regexp`, Rust the `regex` crate 1.12.3
(built offline from the cargo cache), and rg is ripgrep 14.1.1 counting
matches as a whole process, start-up and file reading included: a
reference point, not a like-for-like figure.

| pattern | matches | obx native | obx via C | Go | Rust | rg |
|---|---:|---:|---:|---:|---:|---:|
| `Sherlock Holmes` | 926 | 0.16 | 0.20 | 0.46 | 0.07 | 1.6 |
| `Sherlock\|Holmes\|Watson\|Irene` | 7,350 | 1.35 | 0.91 | 133.08 | 0.41 | 2.7 |
| `\w+` (all matches) | 669,532 | 61.31 | 36.87 | 138.18 | 16.76 | 39.8 |
| `(?i)sherlock` | 4,280 | 1.11 | 0.80 | 69.94 | 0.45 | 3.1 |
| `[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}` | 848 | 0.20 | 0.12 | 75.93 | 0.09 | 1.7 |
| `(?m)^ERROR .*timeout$` | 197 | 0.26 | 0.33 | 31.34 | 0.58 | 1.8 |
| `(?P<y>\d{4})-(?P<m>\d{2})-(?P<d>\d{2})` (captures) | 10,755 | 4.13 | 3.27 | 70.63 | 1.26 | 7.7 |
| `(a*)*b` over 100,000 `a`s | 0 | 0.00 | 0.00 | 3.31 | 0.00 | 1.5 |

- **obx and Rust.** The engine has RE2's lazy DFA and Rust's literal
  searches (design/regex.md, "R2 as built"). Through C, obx takes 1.3 times Rust's
  time on the email pattern, 1.8 on `(?i)`, 2.2 on the alternation and
  `\w+`, 2.6 on the captures, 2.9 on the literal, and about half Rust's time on
  the `ERROR` lines (a prefilter on `ERROR ` and the suffix check on
  `timeout`); natively 2.2 to 3.7 times Rust's time, and again about half
  on the `ERROR` lines. These are
  the cases the plan aimed at; none is past three times through C.
- **Where the gap is.** The literal: Rust's memchr uses AVX2 (32 bytes a
  step), the runtime SSE2 (16). `\w+` and the captures are per-match
  costs (670,000 and 10,755 matches): each match is a forward and a reverse
  DFA run plus, for captures, the backtracker on the span; Rust's inner
  loops are tighter (unchecked indexing, a byte DFA with no rune decode).
  The native build loses most on these DFA loops (1.7 times its C build on
  `\w+`): the native backend's bounds and overflow checks in the inner loop
  (see below), not the library.
- **`(a*)*b`** takes no time: the text has no `b`, and every match ends
  with one (the suffix check), so the search stops before any matcher runs.
  The DFA on such patterns is covered by the tests (`regex_linear`,
  `regex_dfa`), not by this row.
- **obx and Go.** obx is 2 to 650 times faster than Go's `regexp`, which
  has no DFA.
- **Go's matchers** (the Pike VM, the backtracker and one-pass), which the
  engine uses for short texts (under 128 bytes), for one-pass patterns, and
  when the DFA's cache fills too often, run 1.4 to 1.8 times as fast
  through gcc as natively. The profile is the Pike VM's `add` and `step`:
  short loops over the fields of a `mut &` record's arrays, each access
  with its bounds check and each index sum with its overflow check. gcc
  keeps the arrays' addresses in registers and drops most of the checks;
  the native backend keeps fewer. This is a native-backend matter (the
  code generator, not the language).
- **Linear time.** The library test `regex_linear` checks that 100,000
  bytes take about ten times as long as 10,000 for six such patterns under
  the Pike VM, the backtracker and the DFA (also in a tiny cache), among
  them `(a|b)*a(a|b){20}`, whose DFA has 2^21 states; `regex_dfa` does the
  same on random text, where the cache fills and the search falls back to
  the Pike VM.

### eza

[obx/cmd/eza.obx](obx/cmd/eza.obx), a 1,600-line implementation of the
common subset of eza 0.23.3. `LC_ALL=C TZ=UTC`; the long listings use
`--bytes --time-style=long-iso -n`, which this eza requires:

| listing | output | via C | native |
|---|---:|---:|---:|
| `-T /usr/share` (tree) | 2.1 MB | 103 ms | 107 ms |
| `-T -l /usr/share` (long tree) | 3.7 MB | 191 ms | 193 ms |
| `-l /usr/lib/x86_64-linux-gnu` | 51 KB | 4 ms | 4 ms |

The golden suite (`python3 obx/cmd/eza_gate.py -j 12`) passes on both
builds: 482 cases, 72 live-only cases skipped.

### grep

[obx/cmd/grep.obx](obx/cmd/grep.obx) against GNU grep 3.11 on 100 MB of
generated text (1.8 million lines; the generator is in
[obx/cmd/README.md](obx/cmd/README.md)). Best of 5, output to a pipe,
`LC_ALL=C` (under `C.UTF-8` GNU grep's `-w` takes 2.3 times as long; the obx
port has no locale):

| search | GNU | native | via C | native at `1c6d06c` |
|---|---:|---:|---:|---:|
| `needle5007` (a literal) | 25 ms | 15 ms | 15 ms | 90 ms |
| `-c omega` | 84 ms | 59 ms | 59 ms | 151 ms |
| `-c '[0-9]*7 alpha'` | 69 ms | 16 ms | 16 ms | 352 ms |
| `-c -w eta` | 202 ms | 186 ms | 177 ms | 1,123 ms |
| `-c -v '^0'` (every line) | 47 ms | 47 ms | 42 ms | 468 ms |
| `-c -E 'a.*z.*e'` | 208 ms | 114 ms | 92 ms | 455 ms |
| `-n omega` | 254 ms | 167 ms | 151 ms | 376 ms |
| `-o -w eta` | 383 ms | 335 ms | 302 ms | 1,653 ms |

What made the difference:

- **Lines, not matches.** The regex library's `matchingLines` runs the
  lazy DFA over a chunk and reports the lines with a match. It starts again
  after each such line, with no call per line.
- **A required literal first.** `[0-9]*7 alpha` must contain `7 alpha`; a
  memory search finds it and only its line is checked.
- **`-w` as one regex.** The word with a non-word character or a line end
  on each side. Before, each candidate line was checked again.
- **Output in pieces.** The prefix, the line and the newline go into a
  byte buffer; no string is built per line.
- The nineteenth is shrink-wrapping of the cheapest kind: a function that
begins with `if c { return v }` on its parameters (comparisons of
parameters with each other or with small constants, `v` nothing, a constant
or a parameter) tests before the prologue, in the argument registers, and
returns with a bare `ret`. `tools/perfsuite.py main perf19 -n 5`: run suite
-1.66% instructions, -1.76% cycles (fib -23.5% instructions, -8.3% cycles;
every other program within 0.0% of its instructions: the loops, trees, maps
and tools begin with no such guard, or with one that returns a record or
loads through a pointer), compile suite -0.82% instructions, -1.34% cycles
(compiler `--native` -0.87%, `--emit-c` -1.08%, grep -1.02%, eza -0.75%,
zoxide -0.38%). 51 of the 1,078 functions the backend generates for the
compiler begin with guards. The sampled profile (4,360M instructions) puts
the whole of the callee-saved pushes of those functions' early exits at
about 0.4% of the instructions (about 1% with the frame setup and
restores); the measured gain is that.
- **Compiling zoxide** had become 18% slower with perf15 (1,049M to 1,239M
instructions on the frozen inputs). The cause was zoxide's generated init
functions: 1,347 `b.add("...")` calls, the loop of `StrBuf.add` inlined at
up to 229 of them per function, two hoisted string locals for each loop,
and a register allocator whose check of a register scanned every local
already given one. Two fixes: `allocRegs` keeps the given locals per
register and `meets` builds no arrays (byte-identical code; zoxide 1,234M
to 1,133M, the compiler -0.3%), and `zoxide_gen.py` emits one `b.add` per
run of literal text (203 calls instead of 1,347; every rendering
unchanged). Together, compiling zoxide from main: 1,234M to 533M
instructions (-57%), and its binary 594 KB to 352 KB.
- **`appendStr`** (a std intrinsic: a string's bytes at the end of a
`[]byte` in one copy) makes `StrBuf.add` a call instead of a loop of
`append(strByte)` per byte. Building 20 strings of 400,000 words each:
native 1,782M to 899M instructions, 670M to 364M cycles (-46%); via C 485M
to 269M cycles (-45%). With no inlined loop at each `add`, compiling zoxide
takes 471M to 435M instructions (-7.6%), the compiler -0.5%.

**The runtime's memcpy.** See "Native backend progress".

User-mode instructions for the native build fell from 1,186M to 886M
(`-c omega`), 5,045M to 155M (`[0-9]*7 alpha`), 16,466M to 2,545M (`-w`)
and 7,451M to 672M (`-v '^0'`).

#### On 1 GB

The same generator run to 10^9 bytes (18.2 million lines), the cases above
and more pattern shapes. Best of 3, output piped to `md5sum`, the file in
the page cache, `LC_ALL=C` (GNU grep's `-w` takes 2.3 times as long under
`C.UTF-8`). ripgrep (14.1.1, one file; `-E` dropped, since rg's regexes
are extended already) is for reference; it has no `-x` and its `-f` was
not measured. On every case the native and C builds printed exactly what
GNU grep printed, and so did ripgrep.

| search | GNU | ripgrep | native | via C |
|---|---:|---:|---:|---:|
| `needle5007` (a literal, one line) | 257 ms | 29 ms | 137 ms | 139 ms |
| `-c zzz` (no match) | 325 ms | 24 ms | 132 ms | 132 ms |
| `-c omega` | 842 ms | 340 ms | 593 ms | 600 ms |
| `-v -c omega` | 960 ms | 541 ms | 704 ms | 726 ms |
| `-c '[0-9]*7 alpha'` | 698 ms | 43 ms | 143 ms | 145 ms |
| `-c -E '(alpha\|beta) gamma'` | 1,071 ms | 372 ms | 595 ms | 581 ms |
| `-c -i NEEDLE` | 605 ms | 65 ms | 173 ms | 160 ms |
| `-c -i 'OMEGA BETA'` | 660 ms | 405 ms | 193 ms | 171 ms |
| `-c -w eta` | 2,025 ms | 1,652 ms | 1,892 ms | 1,785 ms |
| `-c -v '^0'` (every line) | 551 ms | 605 ms | 434 ms | 402 ms |
| `-c -E 'a.*z.*e'` | 2,079 ms | 1,647 ms | 1,147 ms | 920 ms |
| `-c -E 'needle(1\|2)[0-9]{3}7'` | 528 ms | 26 ms | 174 ms | 150 ms |
| `-c -E '^[0-9]+7[0-9]'` | 430 ms | 765 ms | 547 ms | 504 ms |
| `-c -E '(alpha\|omega) .*(beta\|psi)$'` | 2,493 ms | 903 ms | 1,197 ms | 1,088 ms |
| `-c -x '[0-9]* alpha beta gamma delta'` | 374 ms | — | 134 ms | 133 ms |
| `-c -F -f` five fixed strings | 1,261 ms | — | 1,613 ms | 1,571 ms |
| `-n omega` | 2,169 ms | 1,301 ms | 1,977 ms | 1,760 ms |
| `omega` (prints 8.8 million lines) | 1,530 ms | 793 ms | 1,472 ms | 1,327 ms |
| `-o 'needle[0-9]*'` | 539 ms | 37 ms | 139 ms | 133 ms |
| `-o -w eta` | 3,780 ms | 2,766 ms | 3,371 ms | 2,969 ms |
| `-n -w eta` | 3,770 ms | 3,075 ms | 3,341 ms | 2,996 ms |

The times scale with the file: each is about ten times its 100 MB figure,
so nothing in the obx port grows worse than linearly.

**Against GNU grep:** obx is ahead on 19 of 21 cases, often by 2 to 5 times.

**Still behind GNU:**
- `-F -f` with several fixed strings, 1.25 times slower. Each string becomes a branch of one regex, where GNU and ripgrep use a multi-string search (Aho-Corasick, Teddy).
- `^[0-9]+7[0-9]`, 1.2 times slower.

**Against ripgrep:** ripgrep is faster wherever a literal or a rare
required string can be found with SIMD (`needle`, `zzz`, `-x`, `-o`).
obx is faster on case-insensitive patterns (`-i 'OMEGA BETA'`), on
`a.*z.*e` and `^[0-9]+7[0-9]`, and when every line matches (`-v '^0'`). The two are
close on `-w`.

### jq

[obx/cmd/jq.obx](obx/cmd/jq.obx), the jq 1.7.1 port, against jq 1.7.1
built from its release tarball (`./configure --with-oniguruma=builtin`,
the default `-O2`). Best of 3 (large input) or 5 (computation) wall
times; figures vary by 5–10% between runs.

**Large input.** `python3 obx/bench/json/bench.py`, run on an 80 MB file
of 182,290 generated records. The output goes to a file, and the three
outputs of each filter must be identical.

| filter | jq 1.7.1 | native | via C |
|---|---:|---:|---:|
| `.` | 2.41 s | 1.68 s | 1.69 s |
| `-c .` | 2.18 s | 1.59 s | 1.62 s |
| `length` | 1.49 s | 1.14 s | 1.04 s |
| `[.[].items[].qty] \| add` | 1.81 s | 1.28 s | 1.20 s |
| `map(select(.active and .score > 0) \| {id, city: .address.city})` | 1.79 s | 1.39 s | 1.41 s |
| `group_by(.address.city) \| map({city: .[0].address.city, n: length})` | 2.10 s | 1.97 s | 1.74 s |

**Computation.** Programs with no input (`jq -n`); the outputs are
identical.

| program | jq 1.7.1 | native | via C |
|---|---:|---:|---:|
| `1` (start-up) | 2 ms | 4 ms | 3 ms |
| `reduce range(3000000) as $i (0; . + $i)` | 493 ms | 503 ms | 346 ms |
| `[range(1000000)] \| map(. * 2) \| add` | 411 ms | 269 ms | 244 ms |
| `[range(200000) \| tostring] \| sort \| length` | 117 ms | 82 ms | 62 ms |
| `def fib: ...; 25 \| fib` (recursive, 250,000 calls) | 59 ms | 113 ms | 91 ms |
| `[limit(100000; repeat(1))] \| length` | 44 ms | 11 ms | 8 ms |
| `[range(100000) \| {a: ., b: "x\(.)"}] \| group_by(.a % 10) \| map(length)` | 223 ms | 281 ms | 245 ms |
| `[range(50000) \| tostring \| test("7$")] \| map(select(.)) \| length` | 71 ms | 79 ms | 58 ms |

**Where the port wins.** It is faster wherever JSON is parsed or written.
jq builds a decNumber for every literal, and its writer goes through
`jv_dump_term`. The JSON library keeps the literal's text and writes it
back. The port also wins on simple generators: `limit`, `range` and `map`
are native in the port, and continuation calls cost less than jq's
bytecode with its fork points.

**Where jq wins:**

- **Calls of jq-defined functions.** `fib` is 1.5–2× slower: each call
  pushes frames and closures, and runs its body through a capturing
  continuation.
- **Start-up.** The port parses and resolves `builtin.jq` on every run
  (2 ms).
- **Sorting objects by key.** `sort_by` and `group_by` on objects are a
  little slower: the comparison walks the counted boxes.

**What made the difference.** Two changes on the branch:

- **Expressions with at most one output run to completion first.** In
  continuation-passing style the rest of the program otherwise runs
  inside each operand, and `25 | fib` overflowed the stack. This also
  saves stack traffic generally.
- **Compiled regexes are cached** for the run. `test("7$")` over 50,000
  strings went from 245 ms to 78 ms.

`json.cmpArrays` also stopped copying elements, which took sorting 100,000
one-element arrays from 271 ms to 156 ms. The library's own numbers
against Python's `json` are in [obx/design/json.md](obx/design/json.md),
section 9.

### The compiler

The obx compiler compiling itself (`obx/compiler/main.obx`), built each way:

| | gcc-built | native-built |
|---|---:|---:|
| self-build to an executable (`--native`) | 522 ms | 588 ms |
| self-build to C (`--emit-c`) | 338 ms | 344 ms |
| full build through gcc | 17 s, nearly all of it gcc | — |
| compiler binary | 2,724,960 bytes (dynamic, glibc) | 2,917,065 bytes (static, no libc) |

Best of 11. The compiler's source is a third larger than at the last
measurement (1.64 MB of `.obx` against 1.24 MB on branch `perf13`; 322 KB
of it is the math port's runtime text, carried as string tables in `rt.obx`
and `rtx.obx`), and the gcc-built `--native` self-build runs 4,214M
instructions against 3,706M: 14% more work for 32% more input. On 4
November on branch `perf13` (after the thirteenth pass, best of 11 on a
machine other builds were loading) the four times were 472, 529, 288 and
297 ms; at `1ccdc03` just before it the
same runs gave 542, 589, 309 and 307 ms, and the gcc-built compiler ran the
`--native` self-build in 3,706M instructions and 1,758M cycles against
3,985M and 2,068M. At `fd6f747` (after the ninth pass) the times were 509,
570, 286 and 300 ms; after the eighth, 493, 589, 274 and 310 ms on 270 fewer
lines of compiler. At
`5900ba3` (bounds checks compared in place) the change is below the timing
noise: the self-build's cycles went from 2,176M to about 2,164M, and on a
later run of the same machine the `fd6f747` native compiler and the
`5900ba3` one both took 589-594 ms (median of 21), the gcc-built one
533-546 ms. The
source is 23,710 lines of obx and 4,756 lines of C runtime (the C backend's
`rt.h` and the native backend's freestanding `rt.c`, each with task stacks
and switching, the event loop, timers, sockets and the SSE2 string
searches), not counting `rt.obx` and `rtx.obx`, which are generated from
them.

- The native-built compiler is 1.03-1.12x slower than the gcc-built one
  (1.1-1.2x before the ninth pass, 1.55x before the fifth). It runs 1.20x
  the instructions (4,441M against 3,706M after the thirteenth pass, which
  sped up the gcc-built compiler more; 1.17x at `5900ba3`, 1.18x
  at `fd6f747`, 1.48x before the ninth, 1.89x before the fifth) at a higher rate per cycle. What is left
  is spread thin: of the 347 functions that take longer native, the 20
  worst hold 43% of the difference. It is in the mix (twice gcc's share of
  register-to-register moves, three times its stores to the stack, half again as
  many compares: spills, and loads and bounds checks repeated from one
  statement to the next), not in a few functions.
  A compiler is call-heavy and branchy, which is gcc's strength; inlining
  in the IR closed much of the gap.
- `--native` itself does more work since the fifth pass (inlining, and
  code generation of the larger IR): with the gcc-built compiler, whose own
  code does not change, building the compiler natively takes about 20%
  longer than before (273 ms then, on smaller sources); building eza, 10%.
  The sixth pass's loop optimizations add 9% to the instructions of that
  build (2,817M to 3,069M); the natively built compiler, whose own loops
  they speed up, comes out even.
- Since both backends start from one IR (2026-11-03), `--native` is about
  30% faster than before, because it no longer prints and re-reads C, and
  `--emit-c` is about 50% slower, because it now builds the IR before
  printing. Winning back the C path is listed in design/native.md section 8.

#### Native backend progress

User-mode instructions (`perf stat`), the native-built compiler building
itself, native eza `-T /usr/share` and native bench fib:

| step | self-build | eza `-T` | fib |
|---|---:|---:|---:|
| before the third performance pass | 1,817M | 757M | |
| after it (live-range registers, operands in place) | 1,649M | 659M | |
| before the fourth, on later sources | 3,938M | 659M | 6,127M |
| after it (temporaries in callee-saved registers) | 3,923M | 649M | 5,630M |
| before the fifth, on later sources | 4,022M | 649M | 5,630M |
| after it (inlining in the IR, value records in fields) | 3,747M | 642M | 5,630M |
| at `2ce4860` (with C1b; the compiler 1,200 lines larger) | 3,973M | 642M | 5,630M |
| before the sixth, on later sources | 4,732M | 642M | 5,630M |
| after it (loops rotated and aligned, `loops.obx`) | 4,673M | 634M | 5,630M |
| before the seventh, on later sources | 4,821M | 634M | 5,630M |
| after it (the sieve: steps, lengths, counters, fills) | 4,815M | 633M | 5,630M |
| before the eighth, on later sources | 5,665M | 632M | 5,629M |
| after it (the regex engine: loads out of loops, memory operands, sites) | 5,388M | 632M | 5,629M |
| before the ninth, on later sources | 5,800M | 632M | 5,629M |
| after it (the compiler: operands in place, record literals in their slots, value records' fields, borrowed str parameters) | 4,617M | 594M | 5,629M |
| at `5900ba3` (bounds checks compare in place; the library's `io.Reader` in the compiler's sources) | 4,585M | 594M | 5,629M |
| before the thirteenth, on later sources (`1ccdc03`) | 4,668M | 596M | 5,630M |
| after it (the compiler's own data and walks: 64-byte IR nodes, shape tables, one use count) | 4,441M | 596M | 5,630M |
| before the fourteenth, on later sources | 4,434M | 596M | 5,630M |
| after it (aggregate copies of 32 bytes and more through `xmm15`; small record literals returned straight into registers) | 4,359M | 594M | 5,630M |
| before the fifteenth, on later sources (`5590fb6`) | 4,479M | 596M | 5,630M |
| after it (`str` headers out of loops) | 4,469M | 580M | 5,630M |
| the sixteenth, on `perf16` only (assembler load reuse; shelved, not merged), main's 4,399M on the same sources | 4,439M | 577M | 5,630M |
| the nineteenth (guards that return early run before the prologue), main's 4,387M on the same sources | 4,349M | 577M | 4,305M |

The compiler had grown between the third and fourth passes (it builds
the IR for both backends since then), so its two pairs of rows do not
compare with each other. The fourth pass cut fib's cycles by 23% (2,188M
to 1,690M); the self-build's and eza's by about 1% and 2%. The fifth cut
the self-build's cycles by 12% (1,699M to 1,496M; the compiler's own work
without the pass, `--emit-c`, by 17%), eza's by 9% (233M to 213M) and the
lists benchmark's by 16%. The sixth is about loops: bench loop from 11,000M
instructions and 2,007M cycles to 8,000M and 1,003M, array from 4,615M and
782M to 2,413M and 410M; the self-build and eza gain about 1% in
instructions, nothing measurable in cycles.
The eighth (the regex engine) cut
the self-build's instructions by 5% and its cycles by 6% (2,392M to
2,246M), at 9% more instructions for the gcc-built compiler's `--native`
(3,342M to 3,649M: the effects walks and summaries of `loops.obx`, the
second register choice and the sites). The ninth (the compiler itself) cut
the self-build's instructions by 20% and its cycles by 9% (2,385M to
2,176M), eza's instructions by 6%, and the native compiler's binary by 10%.
The thirteenth changes no generated code (every program's output is
byte-identical), only the compiler's own work: `ir.Node` shrinks from 88
to 64 bytes (string and float payloads in side tables), simplify's
address-taken walk folds into the use count, x64gen's tree walks dispatch on
a shared table of node shapes, and the second count of uses goes. The
self-build's instructions fall 4.9% (4,668M to 4,441M) and its cycles 11%
(best of 11: 2,213M to 1,973M; the spread on a loaded machine was about
50M). Most of the cycles are the node shrink alone (6%, fewer cache
misses for 1% of the instructions). The gcc-built compiler gains more: 7%
of its instructions (3,985M to 3,706M) and 15% of its cycles (2,068M to
1,758M). Per-commit figures are in design/native.md section 8.

The fourteenth is small: copies of records of 32 bytes or more (the 64-byte
`ir.Node` among them) go 16 bytes at a time through `xmm15`, and a returned
record literal of whole eightbytes loads `rax`/`rdx` from its fields instead
of through a frame temporary. The self-build's instructions fall 1.7%
(4,434M to 4,359M; its cycles 0.2%, below the noise), maps 0.4% of the
instructions and 2% of the cycles, eza `-T` 0.5% and 1.3%; fib, grep and
the rest do not change. It is smaller than the ~250M extra stack stores
suggested: a store is cheap, and `movdqu` copies of 16 to 31 bytes made the
self-build 5% slower in cycles (loads that two 8-byte stores had just
written cannot be forwarded), so they stay 8 bytes at a time. 32-byte AVX
copies were no faster than 16-byte ones.

The fifteenth reads a `str`'s data pointer and length once before a loop
(`loops.obx`; a byte is then `*(sd + rt_sidx(i, n, w))`, and the bounds
check is proved away when `i` steps up from 0 under `i < n`). The
self-build is level (4,479M to 4,469M instructions, 2,000M to 1,989M
cycles; the gcc-built compiler's `--native` runs 0.4% more instructions
for the analysis); maps loses 7% of its instructions and 5% of its cycles
(1.24 to 1.17 times the C build), sort 8% of both, `grep -n` 5% and 4%,
tokens 7% of its instructions; `grep -cE 'a.*z.*e'` takes 8% more cycles on
4% fewer instructions (one dependent-load-bound loop; not understood).

**The runtime's memcpy.** On this machine, `rep movsb` copies about 15
times slower when the destination is 1 to about 32 bytes past the source,
modulo 4 KiB. A string made from a byte array hit that case: 80% of a
literal grep was that one copy. memcpy now copies 64 bytes a step with SSE2
in that case (`grepfast`). The self-build moved from 4,660M to 4,668M
instructions; its cycles did not change (2,305M and 2,307M).

The earlier steps and the per-commit figures are in
[obx/design/native.md](obx/design/native.md) sections 4 and 8. What remains
is mostly work the code itself asks for (allocation, string concatenation,
reference counts, copies of large value records), not register use.

#### Judging a pass

From 2026-11-04 a backend pass is judged by `obx/tools/perfsuite.py`, not
by the self-build alone. The self-build was a moving target: each pass
adds to the compiler's own sources, so part of every reported cost was just
more code to compile. And the compiler is one program; every other program
only gains.

    python3 obx/tools/perfsuite.py main perfNN     # or two compiler paths

It builds both revisions' native compilers from their seeds, then measures
them on inputs frozen at one revision (`7d519d1`; moved only on purpose,
with a note here):

- **Compile suite:** the frozen compiler (`--native` and `--emit-c`) and
  the frozen eza, grep and zoxide, compiled by each compiler. This is the
  compile speed a user sees: the new analysis's cost minus what the
  compiler gains from being compiled better itself.
- **Run suite:** the frozen bench programs and grep, sort, wc and eza,
  built by each compiler and run on fixed inputs (a generated 100 MB text).
  Both builds must print the same output.

Each suite is summarised as the geometric mean of new/base. On two builds
of the same code, instructions are identical and cycles vary by up to ±3%
on one program and ±0.5% on the means, so instructions decide and cycles
must agree in sign. A pass is kept when:

- the run suite gains at least 1% (geometric mean), or 5% on at least two
  programs with none worse than 1%, and the compile suite costs at most
  1%; or
- the compile suite gains and the run suite is not worse.

The compile suite's total cost over `7d519d1` stays under 5%
(`perfsuite.py 7d519d1 main`). The shelved passes below were judged on the
self-build alone; `cse-experiment` and `perf12` deserve a second look under
these rules. Optimisation levels (an `-O` for the expensive passes) come
only if a pass worth having breaks the budget.

#### Tried and shelved

Optimizations that worked but did not pay for themselves on the self-build.
Each is a local branch with its numbers in the commit message; none is merged.
The self-build compiles these passes too, so an analysis's own cost counts
against what its code saves.

| branch | what | code saved | analysis cost | net |
|---|---|---:|---:|---:|
| `cse-experiment` (`05023cf`) | reuse loads and checked element addresses along straight-line runs (1,063 reuses) | 1.1% | 2.8% | +2% instructions |
| `perf11` (`d79469c`) | move the field loads of a split value record (`let n = g.u.nodes[x]`) to their first use, or into the `if` branch that uses them | none (+0.1%) | 0.7% | +0.8% |
| `perf12` (`187dcef`) | read such fields again after calls that type-based write sets prove cannot change them, instead of saving them on the stack | 0.7% | 4% | +3.7% |
| `perf16` (`b88f4d3`) | the assembler remembers which register holds `[base+disp]` (4 entries, invalidated by writes, labels, calls, stores) and drops or turns into a move the loads of held places | 0.16% (7M of 4,399M; 12 KB of 2.46 MB of code) | 1.07% (47M) | +0.9% instructions, +2% cycles |
| `perf17` (`bf50b1d`) | the range facts (checks proved by guards and ranges) in functions without a loop that have a check and an `if` (2 more of fib's checks go) | below noise on the self-build (325 overflow checks made wrapping against 231); fib -5.9% instructions, -23% cycles; the other benchmarks equal | 2.75% of the native compiler's instructions (120M) | +2.8% self-build; perfsuite: compile suite +1.05% instructions, run suite -0.38% instructions, -2.25% cycles (fib alone) |
| `perf18` (not built) | interprocedural register use (callers keep values in caller-saved registers across calls to callees that do not clobber them); the callee-saved opening thresholds swept | not built: 761 of 25,358 static call sites (3%) reach a leaf function (55 of 1,241); the threshold sweep gave -0.14% (64/48) and +0.06% (96/80) instructions | | below noise |
| `perf20` (not built) | branch layout and jump threading (thread jumps to jumps, invert a jcc over a jmp, drop a jmp to the next instruction, rotate loops) | not built: the native self-build has 524,303 instructions, 47,904 jcc and 8,522 jmp; 1,019 branches go to a jmp, 98 are a jcc over a jmp, 34 a jmp to the next instruction (about 1,150 sites, 2% of branches); loops are already rotated (1,926 backward jcc against 275 backward jmp, 127 of those at a top test). Dynamic: 427M instructions, 99M branches; the ceiling is about 0.5% of instructions | | below the 1% threshold |
| `perf15` (not built) | `str` locals and parameters as register pairs, the first item of the pass | not attempted: see design/native.md section 8 | | |
| `perf21` (measured, not built) | constant folding after inlining: constant operands on both sides, `if`/`while`/`?:` on a constant condition, `switch` on a constant, a compare of a local with itself, construct-then-match on a local only ever assigned constants | not built: in the self-build's 406,000 IR nodes after inlining, 424 `E_BIN` have two constant operands (0.10%; 16 in loops), 407 conditions are constant (0.10%; 69 in loops), 338 casts of constants (x64gen takes them as immediates), 108 checked `rt_add/sub/mul/div` calls on constants, 5 self-compares, and 3 tests of a local that is only assigned constants; following single-assignment constant locals adds just 38 conditions. The perfsuite programs have 0 to 156 each (grep 156 of 81,743 nodes, sort 129 of 18,572 but 91 are its own constants) | | below 0.3% of static code at best, under 0.1% dynamic |

`perf17` fails "Judging a pass": the run suite gains in cycles but on one
program (fib), and the compile suite costs 1.05%. It is committed on its
branch with the tests (`lib/tests/run/range_facts_*`) that show the checks
stay where they must. A cheaper test for which functions to analyze (the
compiler has hundreds with a check and an `if`) would be the way to retry it.

`perf16` is the sound version of the idea behind `cse-experiment`, at the
assembler (nothing walks the IR): a reload is skipped only when the register
that loaded it, or one that stored it, still holds it. It finds little
because the generator evaluates everything into `rax`: the chain
`mov rax,[rbx]; mov rax,[rax]; mov rax,[rax+0x38]` destroys its own
intermediate values, and the next statement's `g.u.nodes.len` starts from
`rbx` again. Statically 3.5% of the 8-byte loads have a holder (a model of
the binary's basic blocks: 733 would vanish, 3,040 become register moves, and
the moves save no instruction); dynamically the self-build runs 7M fewer
instructions and the benchmarks 0 to 2% (maps -2.1%, lists -0.7%, the rest
within 0.1%). The bookkeeping is 4 to 5 instructions a write of a register
and a scan of 4 entries per load, store and move: 47M instructions in the
self-build (the budget was 10M). A cheaper table would not change the sign:
all of the saving is 0.16%. The cycle changes in the table of design/native.md
section 8 (fib -23%, wc +13%) are layout, as the instruction counts are equal.
Under "Judging a pass" (`perfsuite.py 7d519d1 perf16`) it still fails:
the compile suite costs 0.59% of its instructions, the run suite gains
0.19% (maps 2.1%, lists 0.7%, the rest under 0.2%). The run suite's cycles
gain 2.2%, but that is fib's 23% on identical instructions, which is layout.

`perf15`'s second part, the hoisting of `str` headers out of loops, was
kept: the self-build is level (0.2% fewer instructions in the native
compiler, 0.4% more in the gcc-built one's analysis), the string-scanning
benchmarks gain 4% to 8% of their cycles. Its first try gave the data
pointer full weight in the register choice and cost sipKeyedStr (the hash
behind every `Map[str, V]`) 40% of its cycles in the self-build: the
register went from a loop-carried temporary to the pointer, and the
temporary into a store-to-load forward. The second made hoisted headers
that got no register put the `rt_str_byte` calls back (x64gen's `remat`
for a place that is no plain load): it cost 1% of the self-build's
instructions in the second register choice and the walk, and was dropped
for a frame load, one load a use instead of three.

`mid-ir-m1` (2026-11-05) is stage M1 of design/mid-ir.md. It adds a mid IR,
liveness and a linear-scan allocator that decides and reports, all behind
`--native -O`. The code is unchanged: x64gen still generates it, and `-O`
builds are byte-identical. The stage fails its own gate:

- **Cost.** 638M instructions on the self-build. That is 3.02K per mid
  instruction against a target of 3K.
- **Stack accesses (10^depth weights).** The target was 30% fewer than
  x64gen. The model predicts -8.4% on the self-build, +7.5% on the compiler
  at main's sources and +13.9% on the run tests.
- **Callee-saved saves.** -22% on the self-build and -42% on the run tests.

Aggregates stay in the frame, and x64gen already avoids more of their copies
than the lowering does. Even a perfect allocator would only take the rest of
the stack traffic down by about half. Details and the M2 preconditions are
in design/mid-ir.md, "M1 as built". The default path is unchanged: perfsuite
(`0fee39d` against the branch) gives +0.03% compile-suite instructions and
identical run-suite instructions.

What they taught:

- **Fusion:** a statement placed between `bool c = ...` and its `if (c)` stops x64gen fusing the compare with the branch. That alone cost perf11's first version 0.9%.
- **Few fields move:** most fields of a split record are read on more than one path, so few loads can be sunk.
- **The hottest loads change:** x64gen writes `Node.k` and `Node.a` itself while it generates code, so the hottest field loads really can change across its calls.
- **Precise effects are expensive:** per-component call summaries make `loops.obx`'s existing hoisting do several times more work. That was two-thirds of perf12's cost.
- **The bar for a retry:** an analysis like these needs to cost under about 10M instructions (0.2%) to win. That means effect sets with constant-time deduplication, and summaries computed once.

Also tried on `grepfast` (in the library and the runtime), not committed:

| what | effect | why not |
|---|---|---|
| in the DFA's search loop, take a transition with no flag bits in a fast path before the old `< T_MATCH` test | the same instructions on `\w+`, 5% more cycles (63 ms to 67 ms) | the extra test and branch cost more than they saved; not marking start states when there is no prefilter got the same effect |
| memcpy: align the destination to 64 bytes, then `rep movsb` | no change (about 25 ms per 100 MB either way) | the slow case depends on the distance between source and destination, not on alignment |
| a line prefilter that switches itself off, like the literal ones | `-c -v '^0'` slower (69 ms to 326 ms) | once off, every byte took the slow path; the line prefilter now stays on |

### Markdown

[obx/cmd/cmark.obx](obx/cmd/cmark.obx) (the `markdown` library) against cmark
0.31.2 (`-O3` release build, cmake), rendering 10 MB documents with `--unsafe`
to a pipe. Best of 3, seconds (`python3 obx/tests/markdown.py --perf`):

| document | cmark | obx native | obx via C |
|---|---:|---:|---:|
| the spec, repeated to 10 MB | 0.17 | 0.22 | 0.19 |
| the repo's Markdown files, repeated to 11 MB | 0.23 | 0.27 | 0.25 |
| the spec's examples, repeated to 10 MB | 0.58 | 0.73 | 0.62 |

The obx renderer takes 1.1 to 1.3 times cmark's time. The earlier table
(0.37 to 2.59 s) was timed with the wall clock, `time.time()`, which WSL2
steps when it syncs with the host, on a loaded machine; the script now
uses `time.perf_counter()` and fails a run that exits with an error.

cmark's pathological inputs (nested emphasis, brackets and block quotes
30,000 to 100,000 deep, a million backticks, unclosed constructs) all finish
in well under a second, within a small factor of cmark; the one quadratic case
(a run of blank lines inside deeply nested lists) is cut by the same early exit
as cmark's. The parser keeps the tree in one array of 200-byte nodes: the 10 MB spec
takes 114 MB of memory at its peak (cmark: 68 MB).

### Binary sizes

| program | via C | native |
|---|---:|---:|
| empty program | 58,840 bytes (dynamic, glibc) | 12,288 bytes (static) |
| hello | 59,008 bytes | 16,416 bytes (static) |
| `import "regex"`, one call (`regex.quote`) | 63,288 bytes | 16,416 bytes |
| eza | 238,352 bytes (dynamic, glibc) | 242,152 bytes (static) |
| HTTP server (`cmd/httpd.obx`) | 321,592 bytes | 381,824 bytes (dynamic) |
| compiler | 2,724,960 bytes (dynamic, glibc) | 2,917,065 bytes (static) |

Every C build carries the start-up CPU check and the inline math
primitives of `rt.h` (about 5 KB); a C build that calls a ported math
function also carries that function's C. The native backend generates only the functions and globals
reachable from `main` (design/native.md section 5, "Reachability"), as gcc
drops a C program's unused static functions: an imported module costs only
what the program uses of it. The runtime is linked the same way, by
sections, as `ld --gc-sections` would: every function and datum of rt.c is
a section of its own in `rtx.obx`, and the link keeps those reachable from
the runtime functions the program calls. Of the runtime's 62 KB of code and
read-only data, an empty program links 3.2 KB (start-up, the stack-overflow
handler, the exit path), hello 4.0 KB, eza 19 KB, the HTTP server 37 KB (the
event loop, sockets, tasks, timers) and the compiler 27 KB. The runtime's
bss is still most of the empty program's memory (135 KB of 151: the stdout
buffer and the allocator's free lists), but not of its file. The compiler itself stays the same size: what it
saves of the runtime it now carries as the runtime's section and relocation
tables. Speed did not change: the runtime's functions keep their 64-byte
alignment, and the self-build, eza `-T`, bench alloc and fib run the same
instructions in the same cycles within noise (design/native.md, section 5,
"Reachability").

A native executable carries its own runtime instead of linking libc. When a
program calls C libraries (libm, sqlite3, libpq), it is dynamically linked
against them.

### Tasks

The cost of the concurrency primitives (`lib/task.obx`, `lib/net.obx`, SPEC
16), from `obx/bench/tasks.obx`, ns per operation, best of 3 runs:

| operation | via C | native |
|---|---:|---:|
| task switch (`task.yield`) | 7.0 | 12.5 |
| start a task (stack reused) | 42 | 22 |
| start plus a wait that parks (round trip) | 103 | 73 |
| the same inside `task.within` (a timer set and removed) | 119 | 93 |
| wait on a finished task | 26 | 14 |
| promise complete | 11 | 12 |
| `Shared.with` | 3.4 | 6.2 |
| a `task.sleep(1)` that fires, beyond a yield | 73–309 | −182–75 |
| a socket read that finds data (no park) | 748 | 730 |
| a one-byte round trip between two tasks over loopback TCP | 213,000 | 199,000 |
| a `blocking` extern call run inline (no other task could run) | 3.2 | 4.0 |
| the same through the helper pool (another task is parked) | 286–323 | 398–437 |
| the same through the pool, the process on one CPU | 8,200 | 6,500 |

The task operations are far below a system call. The event loop added about
6 ns to a native park (the deadline and timer checks) and nothing measurable
to C's. The native runtime reads the clock through the vDSO, as libc does
(design/native.md, section 2): about 18 ns a read, against 240 ns for the
`clock_gettime` system call it made until 2026-11-03, when the timed wait
was 339 ns natively; the batched rows (start, wait on a finished task,
promise complete) also lost the two system calls per 50 operations of the
benchmark's own clock reads, 4 to 5 ns each. The `sleep` row is noisy:
the 1 ms sleep's overshoot is divided among 10,000 tasks (natively from
−220 to 286 over 9 runs, C from −105 to 134). The round trip is
this machine's loopback: a Python ping-pong over loopback TCP takes
190,000 ns with two blocking threads and 205,000 ns with epoll on one
thread. The native switch is slower than C's because it goes through more
of the generic task code; the fifth pass's inlining took it from 17.6 to
12.8 ns (12.5 to 14.5 from run to run), and it has not been tuned otherwise.

The blocking rows call C's `abs` through a `blocking` extern (SPEC 12).
Inline it is a plain extern call plus a test of the scheduler's state. Through
the pool, the task parks, a helper thread runs the call and the event loop
wakes the task: the helpers and the event loop each spin up to 50 µs before
they sleep, so a busy pool round trip needs no system call. When the
process may use only one CPU (`taskset -c 0`) the spinning is off and every
call costs an `eventfd` write and an `epoll_wait`. For scale: one
`sqlite3_step` over a cached table takes 30 ns, so SQLite's steps in a
program with other tasks cost 10 to 20 times more (0.4 to 0.8 µs); a
query that reads the disk does not notice.

### HTTP server

`obx/cmd/httpd.obx` on the server library `obx/lib/httpserver.obx`
(design/http-server.md, stage S1): a keep-alive HTTP/1.1 server on one
thread, one task per connection, with every limit and timeout of the design
on, the hangup cancellation armed around each handler, the request parsed
strictly and the reply built by the library. Measured by
`python3 obx/cmd/http_gate.py --servers native,c,go,node`: the server on
CPU 0, the load client `obx/cmd/httpload.obx` (native, one thread) on CPU 2,
another core (measured again on 6 November 2026 at `75aa87b`; until then the
load ran on CPU 1, CPU 0's SMT sibling, where it took cycles from the
server it measured).
10,000 idle keep-alive connections are opened first and stay open during
the load; each load client sends `GET /` and waits for the reply, 5 s per
row. Go 1.26's net/http (GOMAXPROCS=1) and Node 24's http serve the same
reply, as references. This machine's numbers move by 5 to 10 % from one run
to the next.

| | obx native | obx via C | Go net/http | Node 24 |
|---|---:|---:|---:|---:|
| RSS per idle connection | 15.3 KB | 14.9 KB | 19.8 KB | 9.5 KB |
| RSS with 10,000 idle connections | 153 MiB | 148 MiB | 195 MiB | 139 MiB |
| 1 client: requests/s | 4,280 | 4,480 | 3,790 | 3,650 |
| 10 clients: requests/s | 37,300 | 39,300 | 34,800 | 32,900 |
| 100 clients: requests/s | 65,600 | 68,700 | 44,500 | 43,100 |
| 1 client: latency p50 / p99 | 219 / 420 µs | 212 / 393 µs | 254 / 410 µs | 260 / 452 µs |
| 10 clients: latency p50 / p99 | 259 / 401 µs | 248 / 366 µs | 268 / 503 µs | 289 / 524 µs |
| 100 clients: latency p50 / p99 | 1.47 / 2.80 ms | 1.43 / 2.09 ms | 2.10 / 6.36 ms | 2.07 / 4.95 ms |

What a request costs the server beyond reading and writing the socket: the
router's lookup (the router is a `task.Frozen`, read by every connection's
task at once), the strict head parse (every field checked), a `poll` system
call when the hangup cancellation is armed (it checks for a half-close that
came with the request, which must not cancel it), the `Date` and
`X-Content-Type-Options` headers and the counters. With the `requestId` and
`accessLog` middleware on (the log written to `/dev/null`) the server gave
about 10 % fewer requests per second at 100 clients (S1's measurement).

An idle connection's resident cost is two pages of its task's stack (8 KiB;
the stack is 256 KiB of address space), a 4 KiB read buffer, a 512-byte
output buffer, and the request and response records with their header
lists, kept for reuse. Under the gate's `OBX_CHECK=1`, `GET /shutdown` with
the 10,000 connections open drains them all and ends the program in 0.5 s,
exit 0, no leak. One client is bound by this machine's loopback round trip
(above, about 210 µs); with 100 clients the single-threaded load client may
be the limit as much as the server. The `ulimit -n` here is 1,048,576; the
gate raises the soft limit when it can and stops when it cannot.

#### The HTTP client

`httpload --client` sends the same load through one `http.Client`
(`obx/lib/http.obx`: `get` and `text()` per request, `maxPerHost` equal to
the connections), against the native server on CPU 0, the loader on CPU 2
(another core; measured again on 6 November 2026, previously on CPU 1),
5 s per row, the median of three alternating rounds (the native columns:
`http_gate.py --servers native --client --conns 1,100,1000`), beside httpload's own
loop (a fixed request, only Content-Length parsed). No idle connections
are held open here, so the raw numbers differ a little from the table
above.

| connections | raw, native | client, native | raw, via C | client, via C |
|---:|---:|---:|---:|---:|
| 1: requests/s | 4,360 | 4,200 | 4,440 | 4,400 |
| 100: requests/s | 68,800 | 57,000 | 68,700 | 58,600 |
| 1,000: requests/s | 57,200 | 49,900 | 54,000 | 47,200 |
| 1: latency p50 / p99 | 219 / 380 µs | 225 / 400 µs | 214 / 383 µs | 218 / 384 µs |
| 100: latency p50 / p99 | 1.43 / 2.59 ms | 1.72 / 2.79 ms | 1.41 / 2.53 ms | 1.67 / 2.82 ms |
| 1,000: latency p50 / p99 | 16.4 / 21.6 ms | 19.6 / 27.5 ms | 17.5 / 25.0 ms | 20.4 / 28.7 ms |

The client does more work per request than the raw loop: it builds a full
request, parses every reply header, works out the framing and goes through
the pool. With the loader's core full, that is 83 % to 87 % of the raw
throughput from 100 connections on; with one connection the loopback round
trip is the limit and they are level. The design's gate asked for parity,
so it is not met (design/http-client.md, section 8).

#### With a Postgres pool

`python3 obx/cmd/http_gate.py --db 4,16` (measured before the move of the
load to another core, with the load on CPU 1): the same server and load, each
request `GET /db`, which takes a connection from a `postgres.Pool` of 4 or
16 connections, runs `SELECT $1::int8 + 1` with the request's number, and
returns the connection. PostgreSQL 16 on the same machine, over its Unix
socket; the queries go through libpq's asynchronous API, so each request's
task parks on the socket and the server's one thread serves the 10,000 idle
connections and the load meanwhile. No Go or Node reference: neither has a
Postgres driver installed here.

| | native, pool 4 | via C, pool 4 | native, pool 16 | via C, pool 16 |
|---|---:|---:|---:|---:|
| 1 client: requests/s | 2,810 | 2,790 | 2,820 | 2,780 |
| 10 clients: requests/s | 21,300 | 21,400 | 18,700 | 19,400 |
| 100 clients: requests/s | 20,600 | 21,100 | 23,000 | 23,600 |
| 1 client: latency p50 / p99 | 336 / 633 µs | 341 / 626 µs | 337 / 607 µs | 344 / 606 µs |
| 10 clients: latency p50 / p99 | 458 / 674 µs | 454 / 676 µs | 530 / 751 µs | 508 / 733 µs |
| 100 clients: latency p50 / p99 | 4.70 / 6.68 ms | 4.60 / 6.48 ms | 4.23 / 6.21 ms | 4.14 / 5.99 ms |

One client pays two round trips, the HTTP one and the server's to
PostgreSQL (about 100 µs: a lone connection does 11,000 queries a second).
From 10 clients on, the server's one thread is the limit: each request is
about 40 µs of it, mostly system calls (the HTTP read and write, and
libpq's send, poll and receive on the database socket). With 100 clients,
90 of them wait in the pool's queue, which is where the 4 ms comes from. A
pool's waiting queue is first come, first served. After the load,
`/shutdown` with the 10,000 idle connections open takes about 0.5 s, exit
0, no leak under `OBX_CHECK=1`, every pool connection idle and nobody
waiting.

The other way to run libpq, `PQexecParams` as a `blocking` extern on the
helper pool, was measured without HTTP (tasks each with their own
connection, queries per second, either backend):

| tasks | asynchronous (the library) | `blocking`, 4 helpers | `blocking`, 16 helpers |
|---:|---:|---:|---:|
| 1 | 11,200 | 11,300 | |
| 4 | 40,000 | 32,000 | 38,000 |
| 16 | 40,000 | 32,000 | 79,000 |

The asynchronous path is bound by the program's one thread (16 tasks: 0.14
s user and 0.58 s system time of 0.74 s); the helpers make their system
calls on other cores. The library keeps the asynchronous API: it needs no
thread per running query, a waiting query can be cancelled (`PQcancel`)
when its task is, and its cost is the one thread's, the same budget the
HTTP side uses; more cores come from processes.

#### With worker processes

`python3 obx/cmd/http_gate.py --workers 1,2,4,8 --db 4`: `httpd ADDR POOL
WORKERS`, n worker processes under `task.workers` sharing one listener
made before the fork, each with its own event loop and (for `/db`) its own
pool of 4 PostgreSQL connections. No idle connections this time; 256
keep-alive load connections spread over 8 load processes (`httpload -p 8`),
5 s per row, `OBX_CHECK=1`. Pinning with `taskset`: the n workers on the
first n of CPUs 0, 2, 4, 6, 1, 3, 5, 7 (this Ryzen's CPUs 2k and 2k+1 are
the two hardware threads of core k, so 1 to 4 workers each have a core of
their own and 8 workers share four cores with SMT), the load on CPUs 8-15
(the other four cores). PostgreSQL is not pinned. Requests/s, then latency
p50 / p99 in ms:

| workers | native `/` | via C `/` | native `/db` | via C `/db` |
|---:|---:|---:|---:|---:|
| 1 | 54,700; 4.58 / 7.04 | 57,500; 4.42 / 6.48 | 19,100; 13.1 / 16.3 | 18,400; 13.6 / 17.5 |
| 2 | 97,700; 2.45 / 5.46 | 98,100; 2.52 / 5.27 | 32,800; 7.60 / 9.93 | 31,600; 7.87 / 10.5 |
| 4 | 124,300; 1.75 / 6.06 | 119,200; 1.82 / 6.56 | 48,300; 4.72 / 12.4 | 47,500; 4.93 / 11.9 |
| 8 | 126,200; 1.65 / 6.66 | 124,600; 1.66 / 6.78 | 62,800; 3.81 / 9.92 | 64,200; 3.75 / 9.63 |

`/` scales 1.7 to 1.8 times at 2 workers and 2.1 to 2.3 at 4, then stops:
the ceiling is the machine's, the loopback TCP of both sides and the WSL2
virtual machine's cross-CPU wake-ups, not the workers (at 4 workers neither
side is busy all the time). `/db` keeps scaling to 8 workers (3.3 to 3.5
times): each request is mostly waiting for PostgreSQL, whose backends run
on whatever CPUs are free, and 8 workers keep 32 queries in flight. A
listener of its own in each worker (`httpd ... WORKERS reuseport`,
`SO_REUSEPORT`) is the other mode; the shared listener is the default
because connections waiting in a killed worker's own accept queue are reset
with its socket.

The kill test (4 workers, `/`, the load reconnecting after an error): a
third of the way in, `SIGKILL` to one worker. Natively the worker held 42
connections and 40 requests were lost (via C 68 and 67): the ones in flight
on its connections, at most one each. The supervisor started it again
after 105 ms (the 100 ms backoff; `http.workerPolicy`: a crash after the
worker is ready never counts toward giving up), it accepted from the same
queue, and the run kept 123,900 req/s natively (120,400 via C) over the 5 s.
The `/shutdown` that follows ends every worker drained, exit 0, the restart
counted.

The differential parsing test (`python3 obx/tests/httpsrvgen.py -n
100000`, 16 threads) sends 100,000 generated request streams to both obx
builds and Go, and parses them with llhttp, in about 160 s.

### TLS

`obx/lib/tls.obx` over OpenSSL 3.0.13 (design/tls.md), and https in the
http client. Measured by `python3 obx/cmd/tls_bench.py`.

Setup:
- `obx/cmd/tlsbench.obx`, built natively and via C, against OpenSSL's own
  clients;
- the same local servers for both, the server on CPU 0 and the client on
  CPU 2;
- a P-256 ECDSA certificate from a test CA, verified by every client;
  TLS 1.3, TLS_AES_256_GCM_SHA384, X25519;
- each row the median of three runs.

Workloads:
- **Handshakes:** full handshakes one after another (connect, handshake,
  close_notify, no resumption) against `openssl s_server -www`.
- **Bulk:** one GET of 1 GiB from `tlsbench serve` (an https server written
  with `tls`, native), its body streamed in 64 KiB reads.
- **Requests:** 20,000 small GETs over kept-alive connections to the same
  server (5,000 for curl, one URL list).

| | obx native | obx via C | OpenSSL / curl |
|---|---:|---:|---:|
| handshakes/s (s_server is the limit) | 760 | 753 | 538 (`openssl s_time -new`) |
| client CPU per handshake | 521 µs | 528 µs | 593 µs (`s_time`) |
| GET 1 GiB: MB/s | 1,933 | 2,034 | 1,959 (curl) |
| GET 1 GiB: client CPU | 0.43 s | 0.44 s | 0.49 s (curl) |
| requests/s, 1 connection | 4,010 | 3,740 | 3,040 (curl) |
| requests/s, 10 connections | 29,100 | 29,300 | |

**Handshakes.** A handshake's client side costs what OpenSSL's own client
costs. Most of the 0.5 ms is in OpenSSL 3.0:
- the per-connection `SSL` setup (provider lookups);
- verifying the ECDSA chain;
- the X25519 exchange.

The engine's memory BIOs and the parking add nothing measurable. The rate
is bound by s_server, which handshakes one connection at a time. s_time
gets fewer per second against the same server, and spends more client CPU
on each.

**Bulk.** Bulk throughput is decryption: AES-GCM with AES-NI, 0.4 s of
client CPU for 1 GiB. The engine's extra copy (socket to BIO to the
caller's buffer) shows as a little less CPU than curl's, not more.

**Requests.** The one-connection row is the loopback round trip, and it
moves by 15 to 25 % from run to run (3,240 to 4,010 natively across runs). For
scale, plain http to `httpd` reaches about 4,200 per second on one
connection (above).

These runs are also the H2 gate's "1,000 https requests over kept-alive
connections" (design/http-client.md): 20,000 per row, on 1 and 10
connections, under no `OBX_CHECK`. The tests run under `OBX_CHECK=1` with
no leak.

### Reproducing

```sh
obx/compiler/build.sh                                  # out/obxc from the seed
python3 obx/bench/bench.py -n 11
python3 obx/bench/regex/bench.py -n 7                  # the regex table (Go, Rust and rg built by it)
python3 obx/bench/json/bench.py --jq JQ                # the JSON library and jq large-input tables
python3 obx/tests/markdown.py --perf --ref CMARK      # the Markdown table
python3 obx/cmd/eza_gate.py -j 12                      # eza correctness on both builds
obx/compiler/out/obxc obx/cmd/eza.obx --native -o eza  # and without --native for the C build
obx/tools/native_selfbuild.sh                          # the natively built compiler
obx/compiler/out/obxc obx/bench/tasks.obx --native -o tasks && ./tasks
python3 obx/cmd/http_gate.py                           # the server table (about 2 minutes)
python3 obx/cmd/http_gate.py --servers native,c --db 4,16   # with Postgres ($OBX_PG_DSN)
python3 obx/cmd/http_gate.py --workers 1,2,4,8 --db 4   # worker processes (about 3 minutes)
python3 obx/tests/httpsrvgen.py -n 100000                              # the server's parsing against Go and llhttp
python3 obx/cmd/tls_bench.py                           # TLS handshakes, bulk and https requests (about 2 minutes)
```

### For the record: obo

obo 0.19, obx's predecessor, was retired on 2026-11-02 and is archived in
[archive/obo/](archive/obo/README.md) with its own
[performance record](archive/obo/PERFORMANCE.md). On the same host and day,
obx was within a small factor of it everywhere and well ahead on allocation
and long eza listings:

| | obo 0.19 | obx via C | obx native |
|---|---:|---:|---:|
| bench fib | 452 ms | 331 ms | 330 ms |
| bench alloc | 483 ms | 121 ms | 63 ms |
| bench loop | 208 ms | 264 ms | 420 ms |
| eza `-T -l /usr/share` | 418 ms | 189 ms | 205 ms |
| compiler self-build | 223 ms | 252 ms | 406 ms |

The fib row's obx figures are from `2ce4860`; the rest are from the day of
the comparison. `python3 obx/bench/bench.py --obo` adds obo's column to the benchmarks
(build `archive/obo/obo` first with `make -C archive/obo`).

### Math (2026-11-05)

The float math functions are the runtime's own (musl 1.2.5, one C text for both
backends, `obx/compiler/obxmath.inc`), not libm's. sqrt, floor, ceil, trunc,
round, rint, fabs, copysign and fma are single instructions (sqrtsd, roundsd,
masks, vfmadd213sd). On a loop of sqrt, floor, ceil, trunc and round: native
393 ms to 340 ms, C backend 200 ms to 100 ms. A native program that uses math
is a static ELF (33 KB against 29 KB dynamic) and starts in 1.12 ms instead of
1.38 ms with libm and libc loaded; the CPU check at startup costs about 0.5 us.
The C backend's gcc needs 0.23 s more for a program that calls the ported
functions (0.74 s against 0.51 s: 170 KB of C text).

5M iterations of one call in a loop (ms, best of 7; libm of glibc 2.39 before,
the runtime's own after): exp C 21 to 8, native 32 to 33; log C 19 to 13,
native 32 to 32; sin C 36 to 19, native 46 to 37; pow C 42 to 38, native 60 to 54;
atan C 33 to 26, native 54 to 36; tan C 45 to 40, native 76 to 58. The
polynomials of exp, exp2, log, log2, pow, sin and cos use explicit fma. The
same text compiled with FMA contraction everywhere is 10 to 35% faster still in a
throughput loop (exp 1.4 ns, log 1.9, pow 6.6, atan 3.1, tan 6.5 per call) but
its bits would depend on the gcc, so the rest stays plain; explicit fma in more
kernels (atan, tan, expm1) is the deterministic way to get it.

The native backend moved xmm0 to xmm1 with `movsd xmm1, xmm0`, which merges and
so waits for the old xmm1; after a call that was the last write inside the callee,
which tied each iteration of a float loop to the callee's whole latency (the
first port of exp ran at half the speed of glibc's for that reason). It uses
`movapd` now.

### Decimal (2026-11-05)

`obx/bench/decimal`: micro-benchmarks of the Decimal operations (`dbench.obx`,
M0 to M10 on 4096 fixed-seed inputs each) and `run.py`, which builds them with
two trees and compares user cycles and instructions (`perf stat`, least of N).
`diff.py` is the differential test that every change to Decimal must pass: 11
operations on 10^6 inputs each, old and new tree, native and C, identical text,
errors and scales. The branch `decperf` (design/decimal-perf-research.md,
section 8) changed digits(), the trailing-zero strip, u128 text, the 256-by-128
division, parse, and the native backend's u128 code. Against main at 641945a,
least of 9, one CPU (new/old cycles):

| Operation | native | via C |
|---|---|---|
| digits(): 1 / 19 / 38 digits | 0.61 / 0.22 / 0.12 | 0.85 / 0.36 / 0.21 |
| add: 6 / 19 digits | 0.61 / 0.45 | 0.90 / 0.63 |
| mul: 6 / 19 digits | 0.49 / 0.31 | 0.83 / 0.54 |
| div: numerator below 2^128 / above, divisor above 2^64 | 0.64 / 0.072 | 0.91 / 0.16 |
| quantize to 2 places: 6 / 38 digits | 0.58 / 0.51 | 0.93 / 0.70 |
| parse: 10 / 38 digits | 0.49 / 0.30 | 0.87 / 0.47 |
| format: 6 / 38 digits | 0.93 / 0.49 | 0.98 / 0.47 |
| rem: exponent gap 1 / 40 | 0.64 / 0.068 | 0.92 / 0.18 |
| cmp | 0.27 | 0.29 |

Per operation after the change (about, cycles, native / C, from the same run,
input set-up included): add of 6-digit values 280 / 135, mul 330 / 165, parse of
20 digits 320 / 180, format of 38 digits 450 / 380, cmp 80 / 35 to 40. The C build is still about twice as fast as
native on the arithmetic: native u128 values live on the stack between
operations.

The C figures above are at `-march=x86-64-v3` before `-mmove-max=128
-mstore-max=128` (`38e114e`). At x86-64-v3 gcc copies a 32-byte value such as
a Decimal passed by value with one 32-byte `ymm` load straight after the 8-
and 16-byte stores that wrote it; store forwarding cannot serve that load,
and every such call stalls. That made the C build of Decimal code 25-40%
slower than the same build at x86-64-v2 (bisected to `-mavx`, Zen 4). The
two flags keep gcc's copies to 16 bytes, in programs and the native runtime;
the native backend never had the stall. Via C, least of 5 cycles, one CPU
(x86-64-v2 / v3 / v3 with the flags):

| case | v2 | v3 | v3 + flags |
|---|---:|---:|---:|
| add, 6 digits (M2:6) | 386M | 537M | 425M |
| add, 19 digits (M2:19) | 384M | 546M | 398M |
| trailing-zero strip, none to strip (M4:0) | 386M | 531M | 395M |
| quantize, 6 digits (M6:6) | 365M | 480M | 371M |
| div, big (M5:big) | 95M | 123M | 97M |
| rem, gap 40 (M9:40) | 150M | 159M | 147M |

So the per-operation C costs above are now about a fifth lower for add and
quantize. What remains on M2:6 is BMI2 (gcc's `mulx`), left as it is. A
16-byte load straight after a callee's two 8-byte stores (a returned
Decimal) still stalls in every build; passing such values by pointer in the
generated C would remove it. On the other bench programs the flags change no
instruction counts, and natively (perfsuite) none at all.
