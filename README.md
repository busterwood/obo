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
compiler. That compiler is kept in `archive/obx/` as the historical reference; the
current compiler is `obo/src/obo.obo`. See [HISTORY.md](HISTORY.md) for the obx language,
architecture and tests, the ownership fork, the milestone chronology, the release
records for each stage and earlier measurements.

## Current state (2 November 2026)

**obo 0.19 is released**: symbol format 12, ABI 1, 757,761 bytes, sha256
`8327ca50…`. `seed/obo` and the compiler are the same promoted image (commit
`ba02031`) and reach their own fixed point. The compiler reads format 12 only:
formats 6–11 are rejected, so rebuild old `.sym` files.

- **Language.** Single ownership, moves, borrowed `&T` references, checked `^T`
  handles, effect summaries, `Result[T]`/`try`, incremental I/O, modules and
  text `.sym` symbol files, methods, first-class interfaces (owning `I`/`?I`,
  borrowed `&I`/`&?I`, dynamic dispatch and the `pure`, plain, `mut` and
  `impure` member tiers), structural function types and non-capturing lambda
  literals with indirect calls, IEEE 754 binary64 floats with shortest
  round-trip formatting and a checked parser, Stage B explicit capture lists
  (`fn [a, b](...)`) with call-scoped read-only borrows, scalar snapshots and
  the per-kind call-site firewall, the exact 24-byte `decimal` scalar (bare
  fractional literals, exact `+ - * %`, comparisons, explicit
  `div`/`quantize`/`toInt`, display-scale preservation) including record and
  variant fields, `[]decimal` arrays at a 24-byte stride, decimal globals and
  literal constants, decimal method and interface-member parameters and returns,
  decimal function values, lambda parameters/returns and 24-byte capture
  snapshots, and private-by-default record fields with `pub` per field, mixed
  records, opaque `priv`/`priviface` `.sym` slots and constructor-only external
  construction. Compiler-known `sql`/`html` template literals type their bind
  parameters, compose fragments and escape HTML contextually.
  Interface handles `^I`, named function values and stored closures are not
  implemented. The reference starts at [obo/LANGUAGE.md](obo/LANGUAGE.md), with
  topic chapters in [obo/language/](obo/language/README.md).
- **Compiler.** 17,233 lines / 659,375 bytes of obo source, single-pass with no
  AST or intermediate representation, emitting x86-64 into a static ELF image.
  It implements the language above, symbol format 12 and the full gate suite.
  The experimental cinterop v2 boundary adds `foreign` handles, owned and
  borrowed C pointers, body-less `native fn` declarations, generated `_out`
  records and a leak census for its own libpq, SQLite, PostgreSQL and SQL Server
  (ODBC) packages; it has no threads and no stable API. The runtime has no
  collector and counts leaks and double frees.
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
- **Library and tools.** The thirteen modules under `obo/lib/` are `strings`,
  `paths`, `sort`, `time`, `cli`, `io`, `fmt`, `errors`, `fs`, `env`, `html`,
  `sqldialect` and `decimals`. `lib/decimals.obx` provides
  `floor`/`ceil`/`trunc`/`round`/`roundAway`, `abs`/`neg`/`sign`/`isZero`/`cmp`/
  `min`/`max`, `sum`, largest-remainder `allocate`/`allocateWeighted` (the parts
  always sum exactly to the total), and `formatGrouped` with a caller-chosen
  separator byte. Each tool is a module root and imports only what it uses;
  `obo/tools/build_tools.py` compiles it directly with the ordinary compiler,
  with nothing concatenated. The exact subsets and caveats are in
  [obo/cmd/README.md](obo/cmd/README.md).
  The systems library is in `obo/experimental/sys`; its core phases P0–P3 are
  implemented. P1b adds rename/link/mkdir/chmod/sync, transactional
  `replaceAtomic`, `openBeneath`, RFC3339, environment snapshots and bounded
  logging; P3 adds spawn/exec, pipes and redirects, reaping and process groups,
  signalfd-backed signals, cwd/chdir and terminal raw mode. P2C compression is a
  separate optional adapter, and threads remain design-only.

| Current metric | Value |
|---|---:|
| Compiler source | 17,233 lines, 659,375 bytes |
| Compiler (`--version`) | 757,761 bytes, sha256 `8327ca50…`, `obo 0.19`, sym-format 12, abi 1 |
| Seed (`--version`) | promoted obo 0.19 at `ba02031`: the same 757,761-byte image, sha256 `8327ca50…`, sym-format 12, abi 1 |
| Self-host fixed point | stage 2 == stage 3 == compiler == seed, 757,761 bytes, `8327ca50…` |
| Self-compile wall time | 0.252 s one stage (5 runs, median; range 0.2473-0.2549 s) and 0.500 s for the two-stage seed → stage 2 → stage 3 build; host-dependent, measured on the 0.16 line on 1 November 2026 and carried unchanged because later lines changed compiler checks, lambda frames and decimal dispatch, not any benchmarked non-decimal path |
| Self-compile live peak | 2,590,496 bytes direct (`heap_test`); 2,590,480 bytes port comparison (budget 2,750,000) |
| Total allocated | 58,005,840 bytes direct; 58,009,088 bytes port comparison |
| Maximum resident set | 5,092 KB (measured on the 0.16 line on 1 November 2026, carried) |
| Live-memory budget | 2,750,000 bytes: 159,504 spare direct / 159,520 port comparison; the separate empty-array oracle requires a 30,000-byte same-source regression (currently measured at 53,984) |
| obo suite | 1,884 `run_fork` tests; 151 `module_test`; `sym_test.py` 59/59; `sym_fuzz.py` 200/200; `port_test` 643/643 with 10 encoder groups; `port_compare` 1,859/1,859 default; promoted-seed comparison 1,655/1,655; 83 doc examples; 31/31 import checks; 24/24 stdlib roots |
| Float suite | `float_f1.py` 19 groups in normal and `--noregs` builds (12,580 power-of-two values each); `lambda_float_capture` passes in both modes; `memory_log_check` 4 sources |
| Seed comparison | `OBO_REF=obo/seed/obo` against the promoted 0.19 seed is 1,655/1,655. The flat count omits the 204 module-tree, CLI, special, re-emit and `modules/tokens` checks the default comparison adds, so the default (self) comparison is 1,859/1,859 |
| Mutation gates | `mutate_fork` 727/757 caught + 30 equivalent, 0 missed/stale/broken; `mutate_port` 540/540 caught, 0 missed/stale/broken; `mutate_meta` 71/82 caught + 11 equivalent, 0 missed/stale/broken (runtime/entry.S 50/52 + 2 equivalent; compiler 21/30 + 9 equivalent); `mutate_decimal` 117/118 caught + 1 equivalent (the by-address capture mutant), 0 missed/stale/broken, with an explicit baseline; `mutate_io` 36/36, 0 equivalent, 0 missed/stale/broken (including the self-host memory oracle, empty-array delta 53,984 bytes) |
| Tools differential gate | 509 checks vs GNU coreutils 9.4; 4 memory cases |
| Tool binaries | cat 94,209; wc 98,305; sort 122,881; head 94,209; tail 106,497 bytes (static, runtime included) |
| eza E2 differential gate | own-binary gate: 482 passed, 0 failed, 72 live-only skips; pinned reference eza is not on the host `PATH` |
| eza E2 binary | 270,337 bytes (static, runtime included; sha256 `84211d5d…`; reference eza 0.23.3 is 2,294,768) |
| eza 10k-entry live peak | worst 2,184,496 bytes (Tree; Restricted long 2,184,368); `EZA_PEAK_MAX` 2,752,512 (a distinct eza cap, not the compiler budget) |
| Minimal static executable | 81,921 bytes (runtime included; a page-rounded `module mini; fn main() {}` binary) |

The compiler budget is 2,750,000 bytes live, with the separate 30,000-byte
empty-array oracle unchanged. The current line peaks at 2,590,496 bytes in
direct self-compiles and 2,590,480 in the port comparison: 159,504 and 159,520
bytes spare. Total allocation is 58,005,840 bytes direct and 58,009,088 bytes in
the port comparison. No memory check is weakened. The live peak varies by a few
hundred bytes with the source path and total allocation by about 1.3 KB.

## Performance

These are one host's measurements, not portable constants: an AMD Ryzen 7
8745HS under WSL2, CPython 3.12.3, GNU coreutils 9.4 and **obo 0.16 sym-format
9 abi 1**, `50cca0a`, measured on 1 November 2026. Later lines changed compiler
checks, lambda frames and decimal dispatch, not any benchmarked non-decimal path
or the runtime, so this table is carried unchanged from its 0.16 measurement; it
was not re-run afterwards.
The table is the last of three default runs; the runs differed most on `sort`
(168.6–237.5 ms), `sort -n` (216.4–237.3 ms), `sieve` and `wc -l`, and
Python/GNU reference rows moved by up to about 20%. The benchmark uses warm page
cache, one pinned core,
medians of repeated runs, timed output to `/dev/null`, `LC_ALL=C` and `TZ=UTC`;
the command is `python3 obo/bench/cli/run.py` (add `--eza PATH`, or have a
matching eza on `PATH`, for the optional pinned-eza row). The pinned eza 0.23.3
reference was not on this host's `PATH`, so that row was skipped. The full
tables, binary sizes, memory figures, methodology and caveats are in
[PERFORMANCE.md](PERFORMANCE.md); how to run and read the suite is in
[obo/bench/cli/README.md](obo/bench/cli/README.md). Earlier benchmark samples
are in [HISTORY.md](HISTORY.md).

| Workload | obo | Comparison |
|---|---:|---:|
| Process startup | 0.21 ms | Python 12.5 ms (58.7x slower) |
| `fib(32)` recursive | 11.2 ms | Python 218.9 ms (19.5x slower) |
| Sum below 100,000,000, explicit loop | 23.7 ms | Python 8,139 ms (343.6x slower) |
| Sieve of Eratosthenes to 10,000,000 | 128.7 ms | Python 2,348 ms (18.3x slower) |
| `wc -l`, 110 MB | 79.8 ms | Python 74.3 ms (0.93x); GNU 12.2 ms (0.15x) |
| `wc -lwc`, 110 MB | 134.0 ms | Python 485.0 ms (3.62x slower); GNU 1,057 ms (7.89x slower) |
| `sort`, 1,000,000 lines | 168.6 ms | Python 583.3 ms (3.46x slower); GNU 258.2 ms (1.53x slower) |
| `sort -n`, 1,000,000 lines | 216.4 ms | GNU 469.4 ms (2.17x slower); Python 550.2 ms (2.54x slower) |
| `eza`, 10,000 names | 32.0 ms | Python 17.7 ms and GNU `ls` 6.93 ms are faster; pinned eza row skipped |

The compiler is the 757,761-byte 0.19 fixed point (sha256 `8327ca50…`), and
`seed/obo` is the same promoted image. The tools are cat 94,209, wc 98,305,
sort 122,881, head 94,209 and tail 106,497 bytes; eza is 270,337 bytes (sha256
`84211d5d…`); the minimal static `fn main() {}` binary is 81,921 bytes. Every
obo binary is freestanding and static, with the runtime included. The tool, eza
and minimal sizes are unchanged across recent lines because code generation
outside lambda frames and decimal method, interface and value calls did not
change, and because the 0.19 lambda-frame change does not alter those binaries.
The benchmark rows above were measured on the 0.16 line; later lines do not move
them. The honest shape of these numbers is unchanged: pure compute is far ahead
of CPython, while `wc -l` is close to Python and behind GNU, eza is slower than
Python and `ls` on names (its case is size and memory), and the sort rows are
fixture-specific and host-load sensitive.

### Heap limit and address space

The freestanding runtime reserves the heap with `mmap(MAP_PRIVATE |
MAP_ANONYMOUS | MAP_NORESERVE)`. The compiled default is **4 GiB**
(4,294,967,296 bytes) and the compiled ceiling is **64 GiB**
(68,719,476,736 bytes). `OBO_HEAP_LIMIT` may lower or raise the arena inside that
range:

- the value is a canonical unsigned whole number of bytes (`4294967296`) or the
  same with an exact binary suffix `KiB`, `MiB` or `GiB` (`4GiB`);
- it must be at least 64 KiB, a multiple of 16 bytes, and at most 64 GiB;
- signs, whitespace, leading zeros, fractions, exponents, unknown suffixes,
  empty values and overflow are rejected. There is **no silent clamp**: an
  invalid or above-ceiling value prints `obo: invalid OBO_HEAP_LIMIT: <value>`
  to stderr and exits 2 before any mapping is made.

For example, `OBO_HEAP_LIMIT=64KiB` gives a small bounded arena and
`OBO_HEAP_LIMIT=64GiB` raises it to the compiled ceiling. The 64 GiB figure is
the handle limit, not an arbitrary policy: a handle's low 32 bits are a
heap-relative granule offset, so 2^32 granules of 16 bytes address exactly
64 GiB. At that ceiling the final granule offset is 0xffffffff and the high
32-bit generation field is untouched; a future compiled ceiling that needs a
granule at offset 2^32 fails a build-time assertion and the runtime guard,
instead of silently truncating handles. The
one-object cap is independent of the arena: an object's rounded size may not
exceed 0xFFFFFFF0 bytes, so its payload is at most 4 GiB - 32 bytes even in a
64 GiB heap. This is what keeps the size word's low 32 bits unambiguous while a
string's reference count uses the high half.

`MAP_NORESERVE` reserves address space, not RAM: only touched pages become
resident, so a sparse 64 GiB heap with a few small objects has a small RSS. The
heap cap excludes runtime metadata and native libraries. The starts bitmap is
one bit per 16-byte granule (32 MiB at 4 GiB, 512 MiB at 64 GiB), and the
free/mark worklist scales with the arena from 128 KiB to 512 MiB of virtual
address space. Linux overcommit policy, `RLIMIT_AS`, cgroups and any host memory
limit still apply; a failed mapping exits cleanly with
`obx: cannot reserve heap` or `obx: cannot reserve heap metadata`, never a
segfault. Freed blocks return to the runtime's free lists, not to the OS, so
fragmentation and the high-water mark remain part of a process's footprint;
`OBO_STATS=1` reports live peak and total allocated bytes, not RSS. The 5 GiB
address-space cap in `port_compare.py` is a harness safety limit, not a runtime
default.

The 4 GiB default maps a 32 MiB starts bitmap and a 512 MiB worklist; the 64 GiB
ceiling maps a 512 MiB starts bitmap and the same capped 512 MiB worklist, about
65 GiB of virtual address space in all. `heap_test` needs about 49 GiB of
virtual address space for its mandatory sparse 48 GiB case; the 64 GiB cases
need about 66 GiB and skip cleanly below that. The recorded sparse 64 GiB case
runs in 0.00 s with 264 KiB maximum RSS while touching only a few pages. The
decimal storage tier appends `rt_append_dec` at runtime slot 94 and array kind
7; the hosted build's census slot is 95 because slot 94 now exists in both
builds, and no existing index, including hosted exit slot 59, changes.

## Roadmap

No decimal storage or function-value stage remains. The next language step is
**I2**: `http1server` accepts an interface handler with a data-carrying
response, kept in a local or `&I` parameter rather than a `Server` field or
global; async libpq and a deferred response follow it. After that come `^I`,
named function values and stored closures. A typed database write path
(`SqlArg.Decimal`/`decimal(p,s)`) and further containers are later work. The
experimental packages remain probes, not a stable API. `^I`'s phase 2a design is
in [obo/design/interface-handles-design.md](obo/design/interface-handles-design.md).
The lambda design is in [obo/design/lambdas-design.md](obo/design/lambdas-design.md).

| Work | State |
|---|---|
| I2 HTTP handler | next: an `http1server` interface handler and data-carrying response, with the handler in a local or `&I` parameter, not a `Server` field or global; async libpq and a deferred response after that |
| `^I`, named function values, stored closures | not implemented; `^I` phase 2a design recorded |
| Typed database write path | later: `SqlArg.Decimal`/`decimal(p,s)`; writes currently travel as exact `DecimalText` bytes |
| Systems P2C compression | optional streaming-codec adapter, separate from the P0–P3 core |
| Concurrency and TUI | threads are design-only and unapproved ([obo/design/systems-threads-design.md](obo/design/systems-threads-design.md)); TUI later |
| Peephole/optimizer audit | evidence-gated |
| More tools: `fd`, `hexyl`, `tokei`, `jq` | later |
| Networking, TLS, HTTP | implemented in experimental packages only (HTTP/1.1 and HTTP/2 server cores, TLS 1.2/1.3 adapter, sockets/reactor), unstable and not a framework or stable API; no web framework, async runtime or process supervision |

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
| `obo/LANGUAGE.md` | the core language guide: syntax, literals, statements, `match` |
| `obo/language/` | the reference chapters: types, ownership, references, handles, methods, interfaces, lambdas, modules, results, builtins, runtime, errors, known holes |
| `obo/ABI.md` | value, call, vtable and capture layouts |
| `obo/SYMFILE.md` | the `.sym` text format, hashes and version/trust model |
| `obo/lib/` | the thirteen source modules: `strings`, `paths`, `sort`, `time`, `cli`, `io` (buffered `Reader`/`Writer`, `writeByte`), `fmt`, `errors`, `fs`, `env`, `html`, `sqldialect` and `decimals` |
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
| `HISTORY.md` | the obx predecessor, the ownership fork, milestones, release records and earlier measurements |
| `archive/obx/` | the historical GC compiler, suite and stage tests (see `HISTORY.md`) |
| `obx/` | the next generation: obx with generics, after the 2026-11 restart decision (see `obx/README.md`) |
| `PERFORMANCE.md` | the full benchmark record: tables, methodology and caveats (the README holds the table and its provenance) |

```
cd obo && make                           # obo: build from the committed seed
cd obo && make tools                     # build/tools/{cat,wc,sort,head,tail}
cd obo && make eza                       # build/eza/eza
cd obo && python3 tests/doc_check.py     # language reference examples
cd obo && python3 tests/run_fork.py      # obo's tests, leak check always on
cd obo && python3 tests/elf_test.py      # the image: trimmed runtime-data segment
cd obo && python3 tests/io_test.py --rt --programs --tools  # I/O runtime and programs
cd obo && python3 tests/heap_test.py     # heap limits, allocation bounds, sparse 64 GiB
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
cd obo && ./obo --version                # obo 0.19 sym-format 12 abi 1 (seed and compiler are the same promoted image)
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
