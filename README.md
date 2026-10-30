# obo

obo is a small Oberon-derived language and compiler for Linux x86-64, built to
answer one question: **can a language keep Oberon's simplicity and manage memory
without a garbage collector, and without Rust's complications?**

It uses single ownership, borrowed references and run-time-checked handles — the
group borrowing model designed by Nick Smith and explained by Evan Ovadia in
[Valen's Memory Safety](https://verdagon.dev/blog/valen-group-borrowing) — with
no collector, no lifetime annotations and no `unsafe`. The compiler is
single-pass: no AST, no intermediate representation, and x86-64 machine code
written straight into a static ELF executable.

**History:** obo grew out of **obx**, the original garbage-collected dialect and
compiler. obx is kept in `obx/` as the historical reference; the current compiler
is `obo/src/obo.obo`. See [HISTORY.md](HISTORY.md) for the obx language,
architecture and tests, the ownership fork, milestone chronology and earlier
measurements.

## Current state (30 October 2026)

The promoted seed is **obo 0.8, symbol format 4, ABI 1** (507,905 bytes,
sha256 `673ae7ba…`). The current source adds `sleepMs` and builds a 512,001-byte
**obo 0.8.1** compiler from that seed; stage 2 == stage 3, checked with `cmp`. The
new compiler is not promoted here. Version policy: additive builtin/runtime extensions use patch releases such as 0.8.1; the 0.9 and 0.10 numbers stay reserved for the lambda stages.

- **Language.** Single ownership, moves, borrowed `&T` references, checked `^T`
  handles, effect summaries, `Result[T]`/`try`, incremental I/O, modules and
  text `.sym` symbol files, methods, and first-class interfaces (owning `I`/`?I`,
  borrowed `&I`/`&?I`, dynamic dispatch and effect tiers). The full reference is
  [obo/LANGUAGE.md](obo/LANGUAGE.md).
- **Compiler.** 12,071 lines / 455,137 bytes of obo source. The current source
  adds first-class interfaces and `sleepMs`, and stays interface-free itself, so
  the pre-promotion 0.6 seed can build it. The 0.6 seed (454,657 bytes, sha256 `90fcad7e…`) and the
  0.5 compiler (425,985 bytes) are historical.
- **Tools.** `cat`, `wc`, `sort`, `head`, `tail` and the partial `eza`
  reimplementation are written in obo and built by `make tools` / `make eza`.
  Differential gates compare them with GNU coreutils 9.4 (`obo/tests/tools.py`)
  and pinned eza 0.23.3 (`obo/tests/eza.py`), with recorded goldens as fallback.
- **Modules.** Both stages are implemented: source imports and exports
  (`module`, `import "Name.obx" [as alias]`, `pub`, `alias::name`, `--deps`,
  `-I`) and `.sym` files with `--check`, `--sym-dir`, `--emit-sym`, `--version`
  and staleness/version checks. A full build still compiles every module body
  from source; `--check` checks against dependencies' symbol files, including
  dependencies without sources.
- **Library and tools.** The ten modules under `obo/lib/` are `strings`, `paths`,
  `sort`, `time`, `cli`, `io`, `fmt`, `errors`, `fs` and `env`. Each tool is a
  module root and imports only what it uses; `obo/tools/build_tools.py` compiles
  it directly with the ordinary compiler, with nothing concatenated. The exact
  subsets and caveats are in [obo/cmd/README.md](obo/cmd/README.md).

| Current metric | Value |
|---|---:|
| Compiler source | 12,071 lines, 455,137 bytes |
| Compiler and seed (`--version`) | seed 507,905 bytes, sha256 `673ae7ba…`, `obo 0.8`; compiler 512,001 bytes, `obo 0.8.1`, sym-format 4, abi 1 |
| Self-host fixed point | stage 2 == stage 3, 512,001 bytes; the 0.8 seed builds the 0.8.1 compiler |
| Self-compile wall time | 0.14 s (5 runs, median on this host; host-dependent) |
| Self-compile live peak | 1,947,712 bytes (port comparison) |
| Total allocated | 44,626,480 bytes (port comparison) |
| Maximum resident set | 3,640 KB (5 runs on this host) |
| Live-memory budget | 2,000,000 bytes (52,288 spare) |
| obo suite | 1,416 tests; 103 `module_test` checks; `sym_test.py` 38/38; `sym_fuzz.py` 200/200; `port_test` 558/558 with 9 encoder groups; `port_compare` 1,428/1,428; 57 doc examples; 28/28 import checks; 16/16 stdlib roots |
| Tools differential gate | 509 checks vs GNU coreutils 9.4; 4 memory cases |
| Tool binaries | cat 45,057; wc 49,153; sort 73,729; head 45,057; tail 53,249 bytes (static, runtime included) |
| eza E2 differential gate | 554/554 non-memory cases (465 exact reference goldens, 72 live including 22 pty, 17 own) + 5/5 memory cases |
| eza E2 binary | 221,185 bytes (static, runtime included; reference eza 0.23.3 is 2,294,768) |
| eza 10k-entry live peak | worst 2,190,480 bytes; `EZA_PEAK_MAX` 2,752,512 (a distinct eza cap, not the compiler budget) |
| eza 10k-entry max RSS | 2,560 KiB in the recorded run; reference about 24.6 MB long / 12-14 MB other views |
| Minimal static executable | 32,769 bytes (runtime included) |

The compiler budget is 2,000,000 bytes live and 52,288 bytes remain spare, so no
slot was cut and the budget was not raised. The current tree peaks at 1,947,712
bytes in the port comparison (1,947,584 in the direct stage-2 compile). The live
peak varies by a few hundred bytes with the source path and total allocation by
about 2 KB.

## Performance

These are one host's measurements, not portable constants: an AMD Ryzen 7
8745HS under WSL2, CPython 3.12.3, GNU coreutils 9.4, pinned eza 0.23.3 and
**obo 0.8 sym-format 4 abi 1**, main `9290396`. The table is the second of two
default runs on 30 October 2026; the first run agreed within normal variance
except for the eza row, which was the outlier (55.9 ms obo / 66.9 ms pinned;
the second run is shown). The benchmark uses warm page cache, one pinned core,
medians of repeated runs, timed output to `/dev/null`, `LC_ALL=C` and `TZ=UTC`;
the command is `python3 obo/bench/cli/run.py` (add `--eza PATH`, or have a
matching eza on `PATH`, for the pinned-eza row). Full tables, binary sizes,
memory figures, methodology and caveats are in [PERFORMANCE.md](PERFORMANCE.md);
how to run and read the suite is in
[obo/bench/cli/README.md](obo/bench/cli/README.md). The earlier 0.5 / 0.6
sample is historical and lives in [HISTORY.md](HISTORY.md).

| Workload | obo | Comparison |
|---|---:|---:|
| Process startup | 0.24 ms | Python 12.8 ms (53.7x slower) |
| `fib(32)` recursive | 10.6 ms | Python 216.9 ms (20.5x slower) |
| Sum below 100,000,000, explicit loop | 23.4 ms | Python 7,511 ms (321.5x slower) |
| Sieve of Eratosthenes to 10,000,000 | 135.2 ms | Python 2,392 ms (17.7x slower) |
| `wc -lwc`, 110 MB | 131.7 ms | Python 474.5 ms (3.60x slower); GNU 1,038 ms (7.88x slower) |
| `sort`, 1,000,000 lines | 162.9 ms | Python 602.0 ms (3.70x slower); GNU 256.5 ms (1.57x slower) |
| `sort -n`, 1,000,000 lines | 255.1 ms | GNU 434.7 ms (1.70x slower); Python 516.4 ms (2.02x slower) |
| `eza`, 10,000 names | 30.4 ms (pre-help 212,993 B; not re-measured) | pinned eza 59.2 ms (1.94x slower); Python 16.3 ms and GNU `ls` 6.55 ms are faster |

The compiler is a 512,001-byte static binary built by the 0.8 seed; the tools are cat 45,057, wc
49,153, sort 73,729, head 45,057 and tail 53,249 bytes; eza is 221,185 bytes against 2,294,768 for the
pinned eza 0.23.3 reference. Every obo binary is freestanding and static, with
the runtime included. The honest shape of these numbers is in
[PERFORMANCE.md](PERFORMANCE.md): pure compute is far ahead of CPython, while
`wc -l` is at Python parity and behind GNU, eza is slower than Python and `ls`
on names (its case is size and memory), and the sort rows are fixture-specific.

## Roadmap

The next language work is **lambdas**: Stage A at 0.9 and Stage B at 0.10, with
symbol format 5; `^I` follows Stage B. The design is in
[obo/design/lambdas-design.md](obo/design/lambdas-design.md).

| Work | State |
|---|---|
| Lambdas | next: Stage A 0.9, Stage B 0.10, symbol format 5; `^I` follows Stage B |
| Peephole/optimizer audit | evidence-gated |
| More tools: `fd`, `hexyl`, `tokei`, `jq` | later |
| Floats, concurrency, networking, TUI | deliberately later; `jq` and `hyperfine` would need the float decision first |

Also not yet done: generic modules (not planned), value records, `inout`
parameters, a `>>>` logical shift, and debug info. Effect summaries for functions
that call each other through forward headings remain a checker limit. Current
design notes are indexed in [obo/design/README.md](obo/design/README.md); they are
authoritative documents, not history.

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
  that survives marks a gap in the tests. The mutation suites run fail-fast, with a
  smoke subset first and parallel cases; a verdict cache was designed, found unsound,
  and is not shipped. The port mutants try the check that caught them in the last
  authoritative run first (`MUTATE_CATCHERS=0` turns this off), then run the full
  comparison on a pass or an unknown check, so this changes the order and not the
  verdict. `MUTATE_TIMINGS=path` records per-stage/per-mutant timings outside the tree.
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
- **Problems reported plainly**, including holes in work already done; a known
  hole stays in the bug list until it is closed, not quietly dropped.

## Finding your way

| | |
|---|---|
| `obo/src/obo.obo` | obo's compiler, in obo, built by `obo/seed/obo` (promote with `make promote`) |
| `obo/seed/obo` | the trusted previous stage |
| `obo/LANGUAGE.md` | the current language reference, including known holes |
| `obo/lib/` | the ten source modules: `strings`, `paths`, `sort`, `time`, `cli`, `io` (buffered `Reader`/`Writer`, `writeByte`), `fmt`, `errors`, `fs` and `env` |
| `obo/cmd/` | the tools: `cat`, `wc`, `sort`, `head`, `tail` and `eza`, with `README.md` and their subsets |
| `obo/tests/` | obo's suite, mutants, the I/O, tools and eza gates |
| `obo/tests/io_test.py` | the runtime and streaming-I/O gates |
| `obo/tests/meta_test.py` | the metadata runtime and the `fs` module |
| `obo/tests/elf_test.py` | the emitted image's runtime-data segment |
| `obo/tests/tools.py` | the tools against GNU coreutils, with models and goldens |
| `obo/tests/tools/` | recorded GNU/own goldens for the tools gate |
| `obo/runtime/` | obo's runtime: no collector, leak and double-free counting |
| `obo/FORK.md` | design decisions, milestones, measurements and findings |
| `obo/design/` | authoritative design notes (interfaces, lambdas, tools, numerics) |
| `obo/tools/build_tools.py` | builds the tool module roots directly (no concatenation) |
| `obo/tools/` | the patch scripts behind each change |
| `HISTORY.md` | the obx predecessor, the ownership fork, milestones and earlier measurements |
| `obx/` | the historical GC compiler, suite and stage tests (see `HISTORY.md`) |
| `PERFORMANCE.md` | the current benchmark result, full tables, methodology and caveats |

```
cd obo && make                           # obo: build from the committed seed
cd obo && make tools                     # build/tools/{cat,wc,sort,head,tail}
cd obo && make eza                       # build/eza/eza
cd obo && python3 tests/doc_check.py     # LANGUAGE.md examples
cd obo && python3 tests/run_fork.py      # obo's tests, leak check always on
cd obo && python3 tests/elf_test.py      # the image: trimmed runtime-data segment
cd obo && python3 tests/io_test.py --rt --programs --tools  # I/O runtime and programs
cd obo && python3 tests/mutate_fork.py   # obo's mutants
cd obo && python3 tests/mutate_io.py     # runtime/library mutants
cd obo && python3 tests/mutate_harness_test.py  # the mutation harness's own tests (fake builders)
cd obo && python3 tests/meta_test.py     # the metadata runtime and fs module
cd obo && python3 tests/mutate_meta.py   # metadata runtime/entry.S and compiler mutants
cd obo && python3 tests/port_test.py     # the port: tokens and encoder, against obo
cd obo && python3 tests/port_compare.py  # the port, stage B (everything), with the memory budget
cd obo && python3 tests/mutate_port.py   # the port's mutants
cd obo && python3 tests/sym_test.py      # symbol files: writer, reader, --check, staleness
cd obo && python3 tests/sym_fuzz.py      # deterministic .sym corruption fuzzing
cd obo && python3 tests/tools.py         # the tools vs GNU; models/goldens without it
cd obo && python3 tests/tools.py --require-reference   # tools vs GNU coreutils (release checklist)
cd obo && python3 tests/tools.py --memory              # the four pinned tool memory cases
cd obo && python3 bench/bench.py         # memory and time against the collector
cd obo && ./obo --deps tests/modules/run/basic/main.obx -I tests/modules/run/basic  # dependencies first
cd obo && mkdir -p /tmp/syms && ./obo --check src/obo.obo --sym-dir /tmp/syms        # check without an executable
cd obo && ./obo --version                # obo 0.8.1 sym-format 4 abi 1
cd obo && ./obo --reemit-sym /tmp/syms/obo.sym   # canonical re-emission (test hook)
```

The historical obx build and test commands are in
[HISTORY.md](HISTORY.md#building-obx).

## License

obo is licensed under the Apache License, Version 2.0. The full text is in
[LICENSE](LICENSE); [NOTICE](NOTICE) records the copyright. Unless a file says
otherwise, the compiler, runtime, tools, tests and documentation are covered.

Contributions are accepted under the same license; by contributing you agree that
your contribution is licensed under the Apache License, Version 2.0.
