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

The released compiler is **obo 0.14, symbol format 7, ABI 1** (655,361 bytes,
sha256 `d7b0c755…`), with the decimal text API, private-by-default record
fields, exact 24-byte decimals, Stage B capture lists, the HTML escaping
library and the configurable heap runtime. `seed/obo` is the promoted **0.14**
image (same size and hash). The 0.13 seed (647,169 bytes, `4ec4e0d6…`) built
the 0.14 image once; the 0.12 seed (643,073 bytes, `d98d6b5f…`) and the 0.11
seed (585,729 bytes, sha256 `01fa9ed3…`) also build it; the 0.14 image reaches
a fixed point.
Floats were released at `0.10`/format 6; Stage B is `0.11`/format 6; decimal is
`0.12`/format 6; private-by-default fields are `0.13`/format 7; the decimal
text API is `0.14`/format 7. Format 6 is not accepted by the 0.14 compiler:
rebuild old `.sym` files.
Version policy: additive builtin/runtime extensions use patch releases such as
the historical 0.8.1; floats are 0.10, Stage B is 0.11, decimal is 0.12,
field visibility is 0.13 and decimal text is 0.14. The template-literal source
on this branch is **0.15, symbol format 8, ABI 1**; it is not promoted, so
`seed/obo` remains the 0.14 image until an independent review and promotion.

- **Language.** Single ownership, moves, borrowed `&T` references, checked `^T`
  handles, effect summaries, `Result[T]`/`try`, incremental I/O, modules and
  text `.sym` symbol files, methods, first-class interfaces (owning `I`/`?I`,
  borrowed `&I`/`&?I`, dynamic dispatch and effect tiers), structural function
  types and non-capturing lambda literals with indirect calls, IEEE 754
  binary64 floats with shortest round-trip formatting and a checked parser,
  Stage B explicit capture lists (`fn [a, b](...)`) with call-scoped read-only
  borrows, scalar snapshots (including one-word float snapshots) and the
  per-kind call-site firewall, the exact 24-byte `decimal` scalar tier
  (bare fractional literals, exact `+ - * %`, comparisons, explicit
  `div`/`quantize`/`toInt`, display-scale preservation), and private-by-default
  record fields with `pub` per field, mixed records, opaque `priv`/`priviface`
  `.sym` slots and constructor-only external construction, and on this branch
  compiler-known `sql`/`html` template literals with typed bind parameters,
  fragment composition and contextual HTML escaping. The HTML escaping library
  is in `obo/lib/html.obx`. Interface handles `^I` are later. The full
  reference is [obo/LANGUAGE.md](obo/LANGUAGE.md).
- **Compiler.** 15,611 lines / 589,373 bytes of obo source on this branch (the released 0.14 line was 14,706 / 555,862). Numeric floats add
  the `float` type, exact literal/constant conversion, arithmetic, formatting,
  `parseFloat` and format-6 symbol support to the 0.9/format-5 lambda line;
  Stage B adds capture-list parsing, recursive incomplete/interface
  classification, scalar float snapshots and the call-site capture binding;
  0.12 adds the exact decimal type, literal parser, 24-byte ABI, operators,
  explicit request validation and `Result` APIs; 0.13 adds the `Field` visibility
  flags, source `pub` fields, the designator/construction rules, the relaxed
  private-field closure, `priv`/`priviface` writer/reader and format 7. The 0.11
  seed (585,729 bytes, sha256 `01fa9ed3…`) built the 0.13 image once; the 0.12
  seed builds the identical image and the fixed point holds.
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
- **Library and tools.** The eleven modules under `obo/lib/` are `strings`,
  `paths`, `sort`, `time`, `cli`, `io`, `fmt`, `errors`, `fs`, `env` and `html`.
  Each tool is a module root and imports only what it uses;
  `obo/tools/build_tools.py` compiles
  it directly with the ordinary compiler, with nothing concatenated. The exact
  subsets and caveats are in [obo/cmd/README.md](obo/cmd/README.md).

| Current metric | Value |
|---|---:|
| Compiler source | 14,813 lines, 559,468 bytes |
| Compiler (`--version`) | 655,361 bytes, sha256 `d7b0c755…`, `obo 0.14`, sym-format 7, abi 1 |
| Seed (`--version`) | promoted obo 0.14: 655,361 bytes, sha256 `d7b0c755…`, sym-format 7, abi 1; the 0.13 seed (647,169 bytes, `4ec4e0d6…`) built this image once; the 0.12 seed (643,073 bytes, `d98d6b5f…`) and the 0.11 seed (585,729 bytes, `01fa9ed3…`) also build it |
| Self-host fixed point | stage 2 == stage 3 == compiler, 655,361 bytes, `d7b0c755…`; the 0.11, 0.12 and 0.13 seeds build this image from source, and the promoted 0.14 seed reproduces it |
| Self-compile wall time | 0.195 s (5 runs, median; range 0.194-0.203 s; host-dependent, measured 31 October 2026) |
| Self-compile live peak | 2,239,264 bytes direct; 2,239,392 bytes port comparison (budget 2,500,000) |
| Total allocated | 49,859,376 bytes direct; 49,860,704 bytes port comparison |
| Maximum resident set | 4,352 KB (measured 31 October 2026) |
| Live-memory budget | 2,500,000 bytes: 260,736 spare direct / 260,608 port comparison; the separate empty-array oracle requires a 30,000-byte same-source regression (measured 48,192) |
| obo suite | 1,757 `run_fork` tests; 142 `module_test`; `sym_test.py` 52/52; `sym_fuzz.py` 200/200; `port_test` 605/605 with 10 encoder groups; `port_compare` 1,745/1,745 default; seed comparisons 1,550/1,550 against the promoted 0.14 seed, 1,535/1,535 against 0.13 and 0.12, and 1,477/1,477 against 0.11; 71 doc examples; 29/29 import checks; 16/16 stdlib roots |
| Float suite | `float_f1.py` 19 groups in normal and `--noregs` builds (12,580 power-of-two values each); `lambda_float_capture` passes in both modes; `memory_log_check` 4 sources |
| Seed comparison | `OBO_REF=obo/seed/obo` (promoted 0.14) is 1,550/1,550; the 0.13 and 0.12 seeds are 1,535/1,535 and the released 0.11 seed is 1,477/1,477; the default (self) comparison is 1,745/1,745 |
| Mutation gates | `mutate_fork` 694/725 caught + 31 equivalent, 0 missed/stale/broken; `mutate_port` 540/540 (5 reader/writer mutants exercised only by `sym_test` are judged by `mutate_fork`); `mutate_meta` 71/82 caught + 11 equivalent; `mutate_decimal` 64/64 with an explicit baseline; `mutate_io` 36/36 (including the self-host memory oracle) |
| Tools differential gate | 509 checks vs GNU coreutils 9.4; 4 memory cases |
| Tool binaries | cat 86,017; wc 90,113; sort 114,689; head 86,017; tail 98,305 bytes (static, runtime included) |
| eza E2 differential gate | own-binary gate: 482 passed, 0 failed, 72 live-only skips; pinned reference eza is not on the host `PATH` |
| eza E2 binary | 262,145 bytes (static, runtime included; reference eza 0.23.3 is 2,294,768) |
| eza 10k-entry live peak | worst 2,184,496 bytes (Tree; Restricted long 2,184,368); `EZA_PEAK_MAX` 2,752,512 (a distinct eza cap, not the compiler budget) |
| Minimal static executable | 32,769 bytes (runtime included) |

The compiler budget is 2,500,000 bytes live, with the separate 30,000-byte
empty-array oracle unchanged. The 0.14 line peaks at 2,239,264 bytes in direct self-compiles and
2,239,392 in the port comparison: 260,736 and 260,608 bytes spare. Total
allocation is 49,859,376 bytes direct and 49,860,704 bytes in the port comparison. No check was
weakened. The live peak varies by a few hundred bytes with the source path and
total allocation by about 2.8 KB. The historical lambda release budget remains
2,000,000 in its own record. The 0.14 release is merged and promoted on main;
`seed/obo` is the matching 0.14 image.

### Template literals 0.15 (implementation branch)

Compiler-known `sql`/`sqlRaw` and `html`/`htmlRaw` backtick tags are implemented
in the source tree but are not promoted to `seed/obo`. SQL holes become ordered
bind parameters in public `Sql`/`SqlArg` records; literal chunks stay raw text,
`Sql` fragments splice, `sql.copy`, `sqlJoin`, `sqlWhere`, `sqlNull` and
`sqlBytes` compose explicitly, and `[]Sql` never expands implicitly. Decimal
arguments use exact `DecimalText` bytes (display form, canonical only when the
display scale was capped; never rounded); `sqlWhere` parenthesizes every
predicate. HTML holes are context-escaped for text, quoted attributes and
allow-listed URLs; URL values are validated as assembled values (a scheme may
not span holes); `Html` fragments splice only as text, `[]Html` concatenates,
and script/style/comment/name/unquoted/srcdoc/style/`on*` holes are compile
errors. The database driver is later: `Sql` is a dialect-neutral
text/parameter pair, and SQL identifiers, `ORDER BY` and `$n` rendering remain
the adapter's or a future allow-list API's job.

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

### Decimal 0.12

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
`decimal::div`/`quantize`/`toInt`, including `Result[decimal]`. It is still a
scalar minimum: there are no decimal globals, constants, record fields, arrays,
captures, methods, interfaces or function values; no public `format`/`parse`
stdlib; no database wire adapter, `decimal(p,s)`, float interop or owned/mut
decimal; and the reviewed core keeps its strict quantize target limit of 32768.
The 40-49 ids are trap diagnostic labels only; `Result` APIs carry core
statuses (1, 3, 4, 5, 6), as documented in `obo/LANGUAGE.md` section 9.

## Performance

These are one host's measurements, not portable constants: an AMD Ryzen 7
8745HS under WSL2, CPython 3.12.3, GNU coreutils 9.4 and **obo 0.14 sym-format
7 abi 1**, main `1db9995`, measured on 31 October 2026. The table is the second
of two default runs; the runs differed most on Python startup, the explicit
loop, `wc -l` and `sort -n` (up to about 10%), while most other obo rows stayed
within about 3%. The benchmark uses warm page cache, one pinned core, medians
of repeated runs, timed output to `/dev/null`, `LC_ALL=C` and `TZ=UTC`; the
command is `python3 obo/bench/cli/run.py` (add `--eza PATH`, or have a matching
eza on `PATH`, for the optional pinned-eza row). The pinned eza 0.23.3
reference was not on this host's `PATH`, so that row was skipped. Full tables,
binary sizes, memory figures, methodology and caveats are in
[PERFORMANCE.md](PERFORMANCE.md); how to run and read the suite is in
[obo/bench/cli/README.md](obo/bench/cli/README.md). The earlier 0.5 / 0.6
sample is historical and lives in [HISTORY.md](HISTORY.md).

| Workload | obo | Comparison |
|---|---:|---:|
| Process startup | 0.20 ms | Python 11.8 ms (58.5x slower) |
| `fib(32)` recursive | 10.2 ms | Python 204.9 ms (20.0x slower) |
| Sum below 100,000,000, explicit loop | 22.1 ms | Python 7,179 ms (324.7x slower) |
| Sieve of Eratosthenes to 10,000,000 | 135.9 ms | Python 2,204 ms (16.2x slower) |
| `wc -l`, 110 MB | 78.1 ms | Python 71.9 ms (0.92x); GNU 11.4 ms (0.15x) |
| `wc -lwc`, 110 MB | 126.4 ms | Python 471.8 ms (3.73x slower); GNU 1,005 ms (7.95x slower) |
| `sort`, 1,000,000 lines | 173.3 ms | Python 585.3 ms (3.38x slower); GNU 245.7 ms (1.42x slower) |
| `sort -n`, 1,000,000 lines | 196.6 ms | GNU 476.7 ms (2.43x slower); Python 495.1 ms (2.52x slower) |
| `eza`, 10,000 names | 29.8 ms | Python 16.1 ms and GNU `ls` 6.42 ms are faster; pinned eza row skipped |

The compiler is the 655,361-byte 0.14 fixed point (sha256 `d7b0c755…`);
`seed/obo` is the same image. The tools are cat 86,017, wc 90,113, sort
114,689, head 86,017 and tail 98,305 bytes; eza is 262,145 bytes. Every obo
binary is freestanding and static, with the runtime included. The benchmark
rows above are the current 0.14 line. The honest shape of these numbers is in
[PERFORMANCE.md](PERFORMANCE.md): pure compute is far ahead of CPython, while
`wc -l` is close to Python and behind GNU, eza is slower than Python and `ls`
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
every executable. The decimal release adds only appended runtime slots, so this
index, including hosted exit slot 59, is unchanged; the promoted seed is the
matching 0.12 `d98d6b5f…` image.

## Roadmap

**Lambdas Stage A** (non-capturing literals, structural function types and
indirect calls) was `0.9`/format 5. Numeric floats are released as
`0.10`/format 6 (floats first, before Stage B). Stage B capture lists are
released as `0.11`/format 6, including one-word float snapshots. Decimal is
released as `0.12`/format 6/ABI 1, and the seed is 0.12. `^I` remains later
work; its phase 2a design is in
[obo/design/interface-handles-design.md](obo/design/interface-handles-design.md). The
lambda design is in [obo/design/lambdas-design.md](obo/design/lambdas-design.md).

| Work | State |
|---|---|
| Lambdas Stage A | merged and promoted at 0.9/format 5 |
| Numeric floats | released at 0.10/format 6/ABI 1 |
| Lambdas Stage B | merged, promoted and pushed at 0.11/format 6/ABI 1; `^I` follows Stage B |
| Decimal scalar tier | released at 0.12/format 6/ABI 1; containers, stdlib and database adapters later |
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
| `obo/lib/` | the eleven source modules: `strings`, `paths`, `sort`, `time`, `cli`, `io` (buffered `Reader`/`Writer`, `writeByte`), `fmt`, `errors`, `fs`, `env` and `html` |
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
cd obo && ./obo --version                # source: obo 0.15 sym-format 8 abi 1 (the 0.14 seed builds it; seed remains 0.14 until promotion)
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
