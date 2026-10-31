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

## Current state (31 October 2026)

The released compiler is **obo 0.11, symbol format 6, ABI 1** (585,729 bytes,
sha256 `01fa9ed3…`), with Stage B capture lists and the configurable heap
runtime. `seed/obo` is the promoted 0.11 seed (same size and hash). The 0.10
seed (569,345 bytes, `30124be9…`) built the first 0.11 image once; the heap
runtime update re-promoted the seed from the 0.11 image. Floats were released
at `0.10`/format 6; Stage B adds capture lists without changing the one-word
ABI or symbol format. Format 5 is not accepted.
Version policy: additive builtin/runtime extensions use patch releases such as
the historical 0.8.1; floats are 0.10, and Stage B is 0.11.

- **Language.** Single ownership, moves, borrowed `&T` references, checked `^T`
  handles, effect summaries, `Result[T]`/`try`, incremental I/O, modules and
  text `.sym` symbol files, methods, first-class interfaces (owning `I`/`?I`,
  borrowed `&I`/`&?I`, dynamic dispatch and effect tiers), structural function
  types and non-capturing lambda literals with indirect calls, IEEE 754
  binary64 floats with shortest round-trip formatting and a checked parser,
  and Stage B explicit capture lists (`fn [a, b](...)`) with call-scoped
  read-only borrows, scalar snapshots (including one-word float snapshots) and
  the per-kind call-site firewall. Interface handles `^I` are later. The full
  reference is [obo/LANGUAGE.md](obo/LANGUAGE.md).
- **Compiler.** 13,883 lines / 524,165 bytes of obo source. Numeric floats add
  the `float` type, exact literal/constant conversion, arithmetic, formatting,
  `parseFloat` and format-6 symbol support to the 0.9/format-5 lambda line;
  Stage B adds capture-list parsing, recursive incomplete/interface
  classification, scalar float snapshots and the call-site capture binding.
  The 0.10 seed (569,345 bytes, sha256 `30124be9…`) built the first 0.11
  compiler once; the heap runtime update re-promoted the 0.11 seed. The
  historical 0.9 and 0.8 seeds and their sizes are historical.
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
| Compiler source | 13,883 lines, 524,165 bytes |
| Compiler (`--version`) | 585,729 bytes, sha256 `01fa9ed3…`, `obo 0.11`, sym-format 6, abi 1 |
| Seed (`--version`) | promoted obo 0.11: 585,729 bytes, sha256 `01fa9ed3…`, `obo 0.11`, sym-format 6, abi 1; the previous 0.10 seed (569,345 bytes, `30124be9…`, `obo 0.10`, sym-format 6, abi 1) built the first 0.11 image; the historical 0.9 seed is 540,673 bytes, `3d369273…`, `obo 0.9`, sym-format 5 |
| Self-host fixed point | stage 2 == stage 3 == compiler, 585,729 bytes, `01fa9ed3…`; the promoted 0.11 seed reproduces the same image from source |
| Self-compile wall time | 0.15 s (three timed direct runs on this host; host-dependent) |
| Self-compile live peak | 2,085,872 bytes direct; 2,086,272 bytes port comparison (budget 2,500,000) |
| Total allocated | 46,373,536 bytes direct; 46,378,000 bytes port comparison |
| Maximum resident set | 4,028 KB direct |
| Live-memory budget | 2,500,000 bytes: 414,128 spare direct / 413,728 port comparison; the separate empty-array oracle requires a 30,000-byte same-source regression (measured 46,528 at baseline 2,086,320 -> mutant 2,132,848) |
| obo suite | 1,644 `run_fork` tests; 112 `module_test`; `sym_test.py` 44/44; `sym_fuzz.py` 200/200; `port_test` 582/582 with 10 encoder groups; `port_compare` 1,641/1,641; 66 doc examples; 28/28 import checks; 16/16 stdlib roots |
| Float suite | `float_f1.py` 19 groups in normal and `--noregs` builds (12,580 power-of-two values each); `lambda_float_capture` passes in both modes; `memory_log_check` 4 sources |
| Seed comparison | `OBO_REF=obo/seed/obo` 1,476/1,476 identical flat cases against the 0.11 seed, exit 0; no version skips. Of the default 1,641 cases, 1,476 are flat and 165 are non-flat (154 module tree/symcheck, 4 CLI, 5 special, 1 reemit and 1 token check); the 165 non-flat cases run separately |
| Mutation gates | `mutate_fork` 680/710 + 30 equivalent, 0 missed/stale/broken; `mutate_port` 531/531; `mutate_io` 36/36 (including the self-host memory oracle); `mutate_meta` 71/82 + 11 equivalent; harness 27/27 |
| Tools differential gate | 509 checks vs GNU coreutils 9.4; 4 memory cases |
| Tool binaries | cat 53,249; wc 57,345; sort 81,921; head 53,249; tail 61,441 bytes (static, runtime included) |
| eza E2 differential gate | reference-golden run not executed here: pinned eza is not on the host `PATH`; own binary gate and 5/5 memory cases pass |
| eza E2 binary | 229,377 bytes (static, runtime included; reference eza 0.23.3 is 2,294,768) |
| eza 10k-entry live peak | worst 2,184,368 bytes; `EZA_PEAK_MAX` 2,752,512 (a distinct eza cap, not the compiler budget) |
| Minimal static executable | 32,769 bytes (runtime included) |

The compiler budget is 2,500,000 bytes live, with the separate 30,000-byte
empty-array oracle unchanged. This Stage B branch peaks at 2,085,872 bytes in
three direct self-compiles and 2,086,272 bytes in the port comparison: 414,128
and 413,728 bytes spare respectively. No check was weakened. The live peak
varies by a few hundred bytes with the source path and total allocation by
about 2 KB. The historical lambda release budget remains 2,000,000 in its own
record. Stage B is merged, promoted and pushed.

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

The compiler is a 585,729-byte static binary; the 0.10 seed built the first
0.11 image and the 0.11 seed now reproduces the promoted image. The tools are
cat 53,249, wc 57,345, sort 81,921, head 53,249 and tail 61,441 bytes; eza is
229,377 bytes against 2,294,768 for the pinned eza 0.23.3 reference. Every obo
binary is freestanding and static, with the runtime included. The benchmark rows
above are the recorded 0.8 line. The honest shape of these numbers is in
[PERFORMANCE.md](PERFORMANCE.md): pure compute is far ahead of CPython, while
`wc -l` is at Python parity and behind GNU, eza is slower than Python and `ls`
on names (its case is size and memory), and the sort rows are fixture-specific.

### Heap limit and address space

The freestanding runtime reserves the heap with `mmap(MAP_PRIVATE |
MAP_ANONYMOUS | MAP_NORESERVE)`. The compiled default is **4 GiB**
(4,294,967,296 bytes) and the compiled ceiling is **16 GiB**
(17,179,869,184 bytes). `OBO_HEAP_LIMIT` may lower or raise the arena inside that
range:

- the value is a canonical unsigned whole number of bytes (`4294967296`) or the
  same with an exact binary suffix `KiB`, `MiB` or `GiB` (`4GiB`);
- it must be at least 64 KiB, a multiple of 16 bytes, and at most 16 GiB;
- signs, whitespace, leading zeros, fractions, exponents, unknown suffixes,
  empty values and overflow are rejected. There is **no silent clamp**: an
  invalid or above-ceiling value prints `obo: invalid OBO_HEAP_LIMIT: <value>`
  to stderr and exits 2 before any mapping is made.

For example, `OBO_HEAP_LIMIT=64KiB` gives a small bounded arena and
`OBO_HEAP_LIMIT=16GiB` raises it to the compiled ceiling. The one-object cap is
independent of the arena: an object's rounded size may not exceed 0xFFFFFFF0
bytes, so its payload is at most 4 GiB - 32 bytes even in a 16 GiB heap. This is
what keeps the size word's low 32 bits unambiguous while a string's reference
count uses the high half.

`MAP_NORESERVE` reserves address space, not RAM: only touched pages become
resident, so a sparse 16 GiB heap with a few small objects has a small RSS. The
heap cap excludes runtime metadata and native libraries. The starts bitmap is
one bit per 16-byte granule (32 MiB at 4 GiB, 128 MiB at 16 GiB), and the
free/mark worklist scales with the arena from 128 KiB to 512 MiB of virtual
address space. Linux overcommit policy, `RLIMIT_AS`, cgroups and any host memory
limit still apply; a failed mapping exits cleanly with
`obx: cannot reserve heap` or `obx: cannot reserve heap metadata`, never a
segfault. Freed blocks return to the runtime's free lists, not to the OS, so
fragmentation and the high-water mark remain part of a process's footprint;
`OBO_STATS=1` reports live peak and total allocated bytes, not RSS. The 5 GiB
address-space cap in `port_compare.py` is a harness safety limit, not a runtime
default.

Regression note from the runtime-only heap update: the 4 GiB default maps a
32 MiB starts bitmap and a 512 MiB worklist; the 16 GiB ceiling maps a 128 MiB
starts bitmap and the same capped 512 MiB worklist. The recorded 16 GiB
`heap_test` run peaks at about 16.625 GiB of virtual address space and 264 KiB of
maximum RSS while touching only a few pages. The change adds no runtime
jump-table slot, so every existing index, including the hosted exit slot 59, is
unchanged and decimal's future appended slots do not collide. Compiler source,
version, symbol format and ABI are unchanged; the compiler image differs from
the earlier 0.11 image (`198f3268…`) because the runtime text is embedded in
every executable, and the promoted seed is the matching `01fa9ed3…` image.

## Roadmap

**Lambdas Stage A** (non-capturing literals, structural function types and
indirect calls) was `0.9`/format 5. Numeric floats are released as
`0.10`/format 6 (floats first, before Stage B). Stage B capture lists are
released as `0.11`/format 6, including one-word float snapshots, and the seed is
0.11. `^I` remains later work. The design is in
[obo/design/lambdas-design.md](obo/design/lambdas-design.md).

| Work | State |
|---|---|
| Lambdas Stage A | merged and promoted at 0.9/format 5 |
| Numeric floats | released at 0.10/format 6/ABI 1 |
| Lambdas Stage B | merged, promoted and pushed at 0.11/format 6/ABI 1; `^I` follows Stage B |
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
cd obo && python3 tests/heap_test.py     # heap limits, allocation bounds, sparse 16 GiB
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
cd obo && ./obo --version                # obo 0.11 sym-format 6 abi 1 (promoted 0.11 seed)
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
