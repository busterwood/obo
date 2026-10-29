# obo

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

**obo** is the language and compiler this repository is about: single ownership,
borrowed references and run-time-checked handles, with no garbage collector. On the
benchmarks it uses 2 to 6 times less memory than even an aggressively tuned
collector, at similar speed, with small changes to source code. It now also has
its command-line tools, `cat`, `wc` and `sort`, written in obo in
`obo/cmd/` and built by `make tools`, plus the working `eza` E2 core
(`make eza`); separate differential gates compare them with GNU coreutils
(`obo/tests/tools.py`) and the pinned eza 0.23.3 reference
(`obo/tests/eza.py`), with recorded goldens as the fallback. Modules are now implemented in both stages:
source imports and exports (Stage 1: `module`, `import "Name.obx" [as alias]`,
`pub`, `alias::name`, `--deps`, `-I`), and text `.sym` symbol files with
`--check`, `--sym-dir`, `--emit-sym`, `--version` and staleness/version checks
(Stage 2). A full build still compiles every module body from source; `--check`
checks a module against its dependencies' symbol files, including dependencies
without sources. The tools are now module roots: each source in `obo/cmd/`
imports the `../lib/...` modules it uses (`cat` and `wc` take `io`, `fmt` and
`errors`; `sort` takes `errors` only), and `obo/tools/build_tools.py` compiles it directly
with the ordinary compiler; nothing is concatenated. Each build gets a
symbol directory under its output (`build/tools/sym` for `make tools`), created
by the builder. Concurrent builds must use distinct output directories; a shared
output has one symbol directory and one probe name.
The tools live in `cmd/`, and their libraries are source modules under `lib/`:
`strings`, `paths`, `sort`, `time`, `cli`, `io`, `fmt`, `errors`, `fs` and
`env`. `cat`, `wc` and `sort` import only what they use (`cat` and `wc` take
`io`/`fmt`/`errors`; `sort` takes `errors` only); `eza` imports all ten. `fmt`
owns the digit and padding
helpers, and `errors` owns the errno text and the eza-shaped exit constants. The
exact subset, message forms and caveats are in `obo/cmd/README.md`.

**obx** was the original garbage-collected dialect and the compiler obo grew from.
It is kept as the historical reference; the "History: mainline obx" section below
describes it, not the current direction.

The decisive test was the compiler itself. obo's compiler has been rewritten in
the obo dialect (`obo/src/obo.obo`), and it compiled every test program
and error case byte-for-byte as the first obo compiler did, and compiles itself to a
fixed point. Its stage test now compares behaviour (same errors, same output when run),
so code generation can improve. Compiling
its own source it peaked at 1.5 MB resident at the earlier stage (live peak now
1,646,816 bytes on a 386,590-byte source; the 425,985-byte compiler takes
0.11 s; see Current status), against 2.5 MB for the same logic on a tuned
collector and 1.9 MB on an
aggressive one. The larger source and its extra checks account for the wall time.

**obo is the language.** obx remains the historical reference it grew from, as the
Python compiler before it did.

## Performance

These are one host's measurements, not portable constants: an AMD Ryzen 7
8745HS under WSL2, CPython 3.12.3, GNU coreutils 9.4 and obo 0.5. The benchmark
uses warm page cache, one pinned core, medians of repeated runs, timed output
to `/dev/null`, `LC_ALL=C` and `TZ=UTC`; the command is
`python3 obo/bench/cli/run.py` (add `--eza PATH`, or have a matching eza on
`PATH`, for the optional pinned-eza row). Full tables, binary sizes, memory
figures, methodology and caveats are in [PERFORMANCE.md](PERFORMANCE.md); how
to run and read the suite is in [obo/bench/cli/README.md](obo/bench/cli/README.md).

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

The compiler is a 425,985-byte static binary; the tools are cat 40,961, wc
45,057 and sort 69,633 bytes; eza is 212,993 bytes against 2,294,768 for the
pinned eza 0.23.3 reference. Every obo binary is freestanding and static, with
the runtime included. The honest shape of these numbers is in
[PERFORMANCE.md](PERFORMANCE.md): pure compute is far ahead of CPython, while
`wc -l` is at Python parity and behind GNU, eza is slower than Python and `ls`
on names (its case is size and memory), and the sort rows are fixture-specific.

## Current status (29 October 2026)

The ownership compiler is merged and self-hosting. The promoted seed is the 0.5
compiler: `obo/seed/obo` is 425,985 bytes (sha256 `05542e89…`, `--version`
0.5), and `make` rebuilds `obo` from the current source with it (425,985 bytes,
sha256 `05542e89…`). It trims the runtime data segment and uses `leave; ret`
epilogues. `Result[T]`/`try` and incremental I/O are done (merged and seeded);
modules are done in both stages (source imports and `.sym`/`--check`); the
command-line tools (`cat`, `wc`, `sort`) and their GNU differential gate are
done and merged, with `wc` scanning only the requested columns and `sort` using
a stable radix sort plus a numeric radix path for `-n`. P3 added the runtime
metadata builtins (`readDirFd`, `statPath`, `lstatPath`, `readLinkPath`),
`lib/fs.obx` and `OBO_VERSION` 0.3, and its seed was promoted. P4 added the
`eza` E0 lines/restricted-long core with no compiler change. P5a added the
environment builtins (`isatty`, `getenv`), `lib/env.obx`, `io::isTty` and
`OBO_VERSION` 0.4, and its seed was promoted. P6a added the terminal-size
builtin `terminalWidth`, `io::termWidth` and `OBO_VERSION` 0.5; the 0.5
compiler recomputes itself to the same 425,985-byte binary (`obo` == stage 2 ==
stage 3, checked with `cmp`), and the 0.5 seed is promoted. P5b added the eza
E1 work: the default colour palette with `--color`/`--colour`, `NO_COLOR` and
the documented `LS_COLORS`/`EZA_COLORS` subset; `-T`/`--tree` and `-L`; and
`--no-quotes`. P6b added the eza E2 work: grid and width sources, UTF-8 widths
and code-point order, and lossy non-UTF-8 display. The eza differential gate now
measures 546/546 non-memory cases plus 5 memory cases against the pinned eza
0.23.3.

| Metric | Value |
|---|---:|
| Compiler source | 10,527 lines, 386,590 bytes |
| Compiler built by the seed (`obo`, `--version` 0.5) | 425,985 bytes (sha256 `05542e89…`) |
| Stage 2 == stage 3 | 425,985 bytes, identical to the seed-built `obo` (checked with `cmp`) |
| Promoted seed (`--version` 0.5) | 425,985 bytes (sha256 `05542e89…`) |
| Self-compile wall time | 0.11 s (5 runs, median; 0.10-0.12 s on this host) |
| Self-compile live peak | 1,646,816 bytes |
| Total allocated | 35,353,648 bytes |
| Maximum resident set | 2,976 KB (3 runs on this host) |
| Live-memory budget | 2,000,000 bytes (353,184 spare) |
| obo suite | 1,165 tests (`run_fork.py`); `port_compare` 1,182/1,182 (stage B) and 1,060/1,060 (against the seed); 48 doc examples; 26/26 import checks; 16/16 stdlib roots |
| Tools differential gate | 280 checks vs GNU coreutils 9.4; 2 memory cases (module roots) |
| Tool binaries | cat 40,961; wc 45,057; sort 69,633 bytes (static, runtime included) |
| eza E2 differential gate | 546/546 non-memory cases (462 exact reference goldens, 72 live including 22 pty, 12 own) + 5 memory cases |
| eza E2 binary | 212,993 bytes (sha256 `53133d23…`; static, runtime included; reference eza 0.23.3 is 2,294,768) |
| eza 10k-entry live peak | 2,190,032 bytes restricted long / 2,189,408 names / 2,190,160 tree / 2,189,632 colour / 2,189,808 grid; `EZA_PEAK_MAX` 2,752,512 (unchanged) |
| eza 10k-entry max RSS | 2,560 KiB in the recorded run (reruns varied 2,304-2,560 KiB across views); pinned reference about 24.6 MB long / 12-14 MB other views across runs (measurement only) |
| Minimal static executable | 28,673 bytes (runtime included) |

Live peak varies by a few hundred bytes with the source path and total
allocation by about 1.9 KB. The table records the figures measured on the main
checkout (wall time and RSS on this host); a longer worktree path measures
1,646,976 live and 35,355,568 allocated, both far under the 2,000,000-byte
budget. P3's four builtin branches raised the peak by 6,240
bytes from 1,636,176, well inside the cut order of design section 6.8; P5a's
two builtin branches and the longer compiler source raised it by 3,008 more,
and P6a's terminal-size branch brought the main-tree figure to 1,646,816 (the
figure depends on the compiler source path; the lib modules are not compiled
into the compiler), still 353,184 under the budget, so no slot was cut. The
tools (P2b), eza and the `wc`/`sort` performance work did not change the
compiler, runtime or seed; their runtime is included in the tool and eza
binary sizes above. P4's eza gate was re-measured under
the merged 0.4 compiler and is unchanged: 272/272, `--memory` 2/2, binary
126,977 bytes, 10,000-entry live peaks 2,185,008/2,184,384 bytes and max RSS
2,432 KiB; the reference goldens are unchanged. P5b re-measured the E1 gate
on the merged 0.5 tree (396/396, `--memory` 4/4), the binary (172,033 bytes,
sha256 `6f0e8472…`), the four 10,000-entry peaks (2,189,984 / 2,189,360 /
2,190,112 / 2,189,584 bytes) and max RSS (2,560 KiB each) without raising the
memory cap. P6b (after merging P5b's review fixes and main `cc6c420`) measures
the E2 gate at 546/546 non-memory and `--memory` 5/5, binary 212,993 bytes
(sha256 `53133d23…`), the five peaks 2,190,032 / 2,189,408 / 2,190,160 /
2,189,632 / 2,189,808 bytes and 2,560 KiB max RSS each, with the cap
unchanged. Main's wc-fast merge makes `wc` 45,057
bytes and the tools gate 280/280; the radix rewrite leaves `sort` at 69,633
bytes.
Whole modules are compiled into each tool, so the figures track the imported
module set: the pre-P1 tree (`265564c`) measured cat 36,865 / wc 40,961 / sort
45,057, the P1+P2a base (`ec244e0`) measured 49,153 / 49,153 / 53,249, and
the merged tree measures 40,961 / 45,057 / 69,633. P2b's net effect on that
base is cat -8,192, wc +4,096, sort 0, with byte-identical observable output;
wc-fast and the sort rewrite account for the later changes.
The old 493,280-byte compiler and seed, tool binaries
108,256/108,256/112,352, and the earlier 90,848-byte minimal figure are
historical. The minimal executable measured in this tree before the fix was
95,968 bytes (the difference is the minimal source and runtime revision, not
the compiler); the fix removes the 67,295-byte zero run from every produced
executable (the tool `cat` loses 4,096 more because its code crossed a page
boundary).

For historical comparison, obx's collector-based compiler is 3,222 lines and
172,768 bytes; it compiles its own 88,731-byte source in about 0.01 s and peaks at
8,832 KB resident. obo's source is 3.3× larger and carries more checks, so wall time
is not like for like; the memory result is the point: about 1.24 MB live against
8.8 MB resident.

The earlier 12.1 MB allocation and 30 ms figures in "Direction" describe the smaller
pre-Result, pre-I/O compiler. The source has grown from 4,552 lines at that stage to
10,527; total allocation has grown with it, while the live peak stays within the 2 MB
budget (the Stage 1 module context raised it from 1,244,480 bytes at the Phase 3
merge to 1,364,240; Stage 2's symbol-file writer and reader raised it to 1,634,720,
an increase of 270,480 bytes; the size fixes measure 1,636,176, P3's metadata
builtins 1,642,416, P5a's environment builtins 1,645,424, and P6a's
terminal-size builtin 1,646,816, still 353,184 bytes under the budget).

### Toward command-line tools

The target is a small, GC-free language for real command-line tools: first the
coreutils-shaped `cat`, `wc` and `sort`; then `eza` (the maintained exa fork), `fd`,
`hexyl`, `tokei`, and `jq` once the float decision is made.

| Phase | Status |
|---|---|
| 0. Ownership compiler, self-hosting fixed point | done |
| 1. `Result[T]`, `ok`/`err`, `try`, consuming `match` | done |
| 2. Incremental I/O: `openFile`/`readFd`/`writeFd`/`closeFile`, standard fds, `Reader`/`Writer` | done |
| 3. `cat`, `wc`, `sort` and the GNU differential gate | done |
| 4. Multiple modules | done: Stage 1 source imports, Stage 2 `.sym`/`--check`; the tools and their libraries are module roots |
| 5. Stdlib modules and the `eza` core | done: the ten `lib/` modules (P3's `fs`, P5a's `env`), the `eza` core E0/E1/E2 (lines/long, colour/tree, grid/UTF-8/lossy names) and the `wc`/`sort` performance work, with their differential gates |
| 6. Methods, interfaces and lambdas | planned |
| 7. Peephole/optimizer audit | evidence-gated |
| 8. More tools: `fd`, `hexyl`, `tokei`, `jq` | later |

Floats, concurrency, networking and TUI work are deliberately later; `jq` and
`hyperfine` are the first things that would need a float decision. Generic modules
are not planned.

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
  and is not shipped.
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
   methods and interfaces, and the rest of the list under "Not yet done", each
   designed to fit the single pass and the ownership model.

**How we will know it worked:** the compiler uses clearly less memory than a tuned
collector, at the same speed; the port reads like ordinary code; and every error
message says what to do instead.

Measured (item 4, after items 1 to 3): compiling each compiler's own source takes
about 0.01 to 0.02 s in both. The obo port (4552 lines) had a live peak of 1.3 MB and
allocated 28.6 MB before the allocation work; after it, 1.05 MB and 12.3 MB (FORK.md,
Phase 4). The cheaper code of Phase 1 brought the total to 12.1 MB and moved the peak to
1.08 MB, reached now while the code buffer grows (FORK.md, Phase 1); the checks across calls
brought it to 1.10 MB; loop inversion (one jump per iteration instead of two, FORK.md) made the tight loop 7% faster and left the live peak at 1.17 MB. The checks added since raised it to 1.27 MB; code kept in fixed-size chunks, the tables freed before the image, the runtime read after the compile and shared derived types brought it to 0.97 MB (FORK.md, "Peak memory"). On main, with Phase 2's register allocation, the live peak was 1.06 MB; Phase 2 accounts for the difference. Moving the function-only fields of `Sym` into a per-function record (`FnRec`) brought it to 0.95 MB (954,912 bytes). obx's compiler (3222 lines), with the
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

## Finding your way

| | |
|---|---|
| `obo/src/obo.obo` | obo's compiler, in obo, built by `obo/seed/obo` (promote with `make promote`) |
| `obo/seed/obo` | the trusted previous stage |
| `obo/lib/` | the source modules under `lib/`: `strings` (byte helpers), `paths`, `sort`, `time`, `cli`, `io` (buffered `Reader`/`Writer`, `writeByte`), `fmt` (digits, padding, `modeString`) and `errors` (errno text, exit codes) |
| `obo/cmd/` | the first tools: `cat`, `wc`, `sort`, with `README.md` |
| `obo/tests/` | obo's suite, mutants, the I/O gates and the tools differential gate |
| `obo/tests/io_test.py` | the runtime and streaming-I/O gates |
| `obo/tests/meta_test.py` | the metadata runtime and the `fs` module |
| `obo/tests/elf_test.py` | the emitted image's runtime-data segment |
| `obo/tests/tools.py` | the tools against GNU coreutils, with models and goldens |
| `obo/tests/tools/` | recorded GNU/own goldens for the tools gate |
| `obo/runtime/` | obo's runtime: no collector, leak and double-free counting |
| `obo/FORK.md` | design decisions, milestones, measurements and findings |
| `obo/tools/build_tools.py` | builds the tool module roots directly (no concatenation) |
| `obo/tools/` | the patch scripts behind each change |
| `obx/` | history: the original GC compiler, suite and stage tests (see History) |

```
cd obo && make                           # obo: build from the committed seed
cd obo && make tools                     # obo's first tools: build/tools/{cat,wc,sort}
cd obo && python3 tests/run_fork.py      # obo's tests, leak check always on
cd obo && python3 tests/elf_test.py      # the image: trimmed runtime-data segment
cd obo && python3 tests/io_test.py --rt --programs --tools  # I/O runtime and programs
cd obo && python3 tests/mutate_fork.py   # obo's mutants
cd obo && python3 tests/mutate_io.py     # runtime/library mutants
cd obo && python3 tests/meta_test.py     # the metadata runtime and fs module
cd obo && python3 tests/mutate_meta.py   # metadata runtime/entry.S and compiler mutants
cd obo && python3 tests/port_test.py     # the port: tokens and encoder, against obo
cd obo && python3 tests/port_compare.py  # the port, stage B (everything), with the memory budget
cd obo && python3 tests/mutate_port.py   # the port's mutants
cd obo && python3 tests/sym_test.py      # symbol files: writer, reader, --check, staleness
cd obo && python3 tests/sym_fuzz.py      # deterministic .sym corruption fuzzing
cd obo && python3 tests/tools.py         # the tools vs GNU; models/goldens without it
cd obo && python3 tests/tools.py --require-reference   # tools vs GNU coreutils (release checklist)
cd obo && python3 tests/tools.py --memory              # the two pinned tool memory cases
cd obo && python3 bench/bench.py         # memory and time against the collector
cd obo && ./obo --deps tests/modules/run/basic/main.obx -I tests/modules/run/basic  # dependencies first
cd obo && mkdir -p /tmp/syms && ./obo --check src/obo.obo --sym-dir /tmp/syms        # check without an executable
cd obo && ./obo --version                # obo 0.5 sym-format 1 abi 1
cd obo && ./obo --reemit-sym /tmp/syms/obo.sym   # canonical re-emission (test hook)
# history: cd obx && make && ./run_tests.sh
```

---

# History: mainline obx (the original dialect)

This section is kept as history: obx is the GC dialect obo grew from, and the
design record references it. Current obo plans are in the status and roadmap above.

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
- obo adds `Result[T]` with `ok(v)` and `err(message[, code])`, the prefix
  operator `try`, and the consuming `match` form `ok(v) { ... }`/`err(m, c) { ... }`
  (`obo/LANGUAGE.md`, section 7); mainline obx has no result type.
- Source modules (`obo/LANGUAGE.md`, section 6): `module Name`, imported files
  named `Name.obx`, `import "Name.obx" [as alias]` before declarations, `-I DIR`
  search paths, `pub` exports with everything else private, `alias::name`
  qualification, and `obo --deps SOURCE [-I DIR]...` for a tree's source list.
  Dependencies compile first into one image, initialize once and free their own
  globals; a headerless root is `_root`.
- Symbol files (`obo/LANGUAGE.md`, section 6): each module with a header writes a
  text `Name.sym` (format `obosym 1`, ABI 1, compiler `0.5`, FNV-1a source,
  interface and dependency-interface hashes); importers read and validate it, and
  `--check SOURCE [--sym-dir DIR]` checks a module against symbol files alone,
  including source-less dependencies. `--emit-sym` writes a single-file root's
  interface; `--version` prints the three version numbers; `-o` is rejected with
  `--check`/`--deps` (as are the three modes with each other). `--reemit-sym FILE`
  is a test hook that reads a symbol file and prints its canonical re-emission.
  Generated symbol files are ignored under `.obo/sym`; a build without
  `--sym-dir` writes next to the root source, so use `--sym-dir .obo/sym`. The
  tools' builder uses its own `<out>/sym` instead (`obo/build/tools/sym` for
  `make tools`), also ignored with the rest of `obo/build/`.
  `tests/modules/golden/expected/*.sym` are committed byte-goldens.
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
modules, value records, inout parameters, a `>>>` logical shift, and debug info. Each was designed earlier
to fit the single pass. Also: effect summaries for functions that call each other through
forward headings.

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

## License

obo is licensed under the Apache License, Version 2.0. The full text is in
[LICENSE](LICENSE); [NOTICE](NOTICE) records the copyright. Unless a file says
otherwise, the compiler, runtime, tools, tests and documentation are covered.

Contributions are accepted under the same license; by contributing you agree that
your contribution is licensed under the Apache License, Version 2.0.
