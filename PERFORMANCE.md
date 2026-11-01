# obo performance

This page records the benchmark run of obo 0.15: the compiler, four small
programs, and the `cat`, `wc`, `sort` and `eza` tools, compared with CPython
3.12.3 and GNU coreutils 9.4. It was measured on 1 November 2026 on current
main at commit `57298f3`. The obo side is `obo 0.15 sym-format 8 abi 1`; the
benchmark programs and tools were built from this tree by
`obo/bench/cli/run.py`. The pinned eza 0.23.3 reference was not present on this
host's `PATH`, so that reference row was skipped; no vendored eza was used. The
suite is on-demand, not a gate; its source is `obo/bench/cli/`, and the command
is:

```sh
python3 obo/bench/cli/run.py
```

The pinned-eza row is optional and needs a binary matching
`obo/tests/eza/VERSION` passed with `--eza PATH` (or an eza on `PATH`);
otherwise only that row is skipped.

Two full default runs reported 96.5 s and 100.3 s (92.9 s and 96.9 s wall for
the commands) on the host below. The tables are the second run. The numbers are
host-dependent; use them for relative comparison, not as portable constants.

## Host and versions

* CPU: AMD Ryzen 7 8745HS with Radeon 780M graphics.
* Environment: WSL2, Linux-6.18.54.1-microsoft-standard-WSL2-x86_64-with-glibc2.39.
* Python: CPython 3.12.3.
* References: GNU coreutils 9.4. The pinned eza v0.23.3 reference was absent
  from `PATH`, so its row was skipped.
* Compiler: `obo 0.15 sym-format 8 abi 1`, 700,417 bytes, sha256
  `8ca8e59e…`. Stage 2 and stage 3 are the same image. `seed/obo` is the
  promoted 0.15 image (same size and hash); the 0.14 seed (655,361 bytes,
  sha256 `d7b0c755…`) built it once. The benchmark programs and the `cat`,
  `wc`, `sort` and `eza` tools below were built from main `57298f3` by the
  benchmark runner; `head` and `tail` were built from the same tree with
  `make -C obo tools`.
* Date and commit: 1 November 2026, main `57298f3`.

## Methodology

* **Warm cache, one core.** Each command runs untimed warmups first, and the
  runner pins itself and every child to one CPU (CPU 15 in this run). This is
  not a cold-I/O benchmark.
* **Medians.** Default mode takes 5 repetitions for light rows and 3 for heavy
  rows; the startup row is a median of 3 batches of 40 processes, timed as one
  wall-clock interval per batch and divided by the batch size.
* **Quiet timed runs.** Timed stdout and stderr go to `/dev/null`; the child
  environment is the caller's with `OBO_*`, `OBX_*`, `PYTHON*` and
  `POSIXLY_CORRECT` removed, and `LC_ALL=C`, `LANG=C`, `TZ=UTC` are set.
* **Fixtures and correctness.** Default fixtures are a 110,000,000-byte text
  file, 1,000,000 sort lines and 10,000 directory names. Every obo output is
  checked against its Python equivalent before timing; the sort rows are also
  checked against GNU `sort` output and the eza rows against the reference,
  where available. A mismatch aborts the run.
* **Ratios.** A row's ratio is its median divided by its workload's base
  median, which is the first row of that workload (always obo), except for
  `sum(range)`, which is compared with obo's explicit loop. A ratio above 1
  means the row is slower than obo; below 1 means it is faster.

The full runner, fixture definitions and recorded sample are documented in
[`obo/bench/cli/README.md`](obo/bench/cli/README.md).

## CPU and startup

| Workload | Implementation | Median | Ratio |
|---|---|---:|---:|
| Process startup | obo `hello` (static binary) | 0.24 ms | 1.00x |
| Process startup | `python3 -c pass` | 15.6 ms | 66.34x |
| Process startup | `python3 -c print("hi")` | 15.4 ms | 65.51x |
| `fib(32)`, recursive | obo `fib` | 11.6 ms | 1.00x |
| `fib(32)`, recursive | `python3 fib.py` | 240.9 ms | 20.82x |
| Sum below 100,000,000, explicit loop | obo `loopsum` | 24.9 ms | 1.00x |
| Sum below 100,000,000, explicit loop | `python3 loopsum.py` | 8,828 ms | 353.84x |
| Sum below 100,000,000, `sum(range(n))` reference | `python3 sumrange.py` | 1,087 ms | 43.55x |
| Sieve of Eratosthenes to 10,000,000 | obo `sieve` | 174.1 ms | 1.00x |
| Sieve of Eratosthenes to 10,000,000 | `python3 sieve.py` | 2,933 ms | 16.85x |

The `sum(range(n))` row is a different algorithm (Python's C-level builtin),
shown for scale and compared with the obo explicit loop, not an equivalent
implementation.

## CLI tools

| Workload | Implementation | Median | Ratio |
|---|---|---:|---:|
| `wc -l`, 110,000,000 B | obo `wc -l` | 90.0 ms | 1.00x |
| `wc -l`, 110,000,000 B | `python3 wc_lines.py` | 85.9 ms | 0.95x |
| `wc -l`, 110,000,000 B | GNU `wc -l` | 15.4 ms | 0.17x |
| `wc -lwc`, 110,000,000 B | obo `wc -lwc` | 146.0 ms | 1.00x |
| `wc -lwc`, 110,000,000 B | `python3 wc_full.py` | 595.5 ms | 4.08x |
| `wc -lwc`, 110,000,000 B | GNU `wc -lwc` | 1,144 ms | 7.84x |
| Sort 1,000,000 lines | obo `sort` | 319.3 ms | 1.00x |
| Sort 1,000,000 lines | `python3 sort.py` | 848.8 ms | 2.66x |
| Sort 1,000,000 lines | GNU `sort` | 352.1 ms | 1.10x |
| `sort -n`, 1,000,000 lines | obo `sort -n` | 378.1 ms | 1.00x |
| `sort -n`, 1,000,000 lines | GNU `sort -n` | 697.7 ms | 1.85x |
| `sort -n`, 1,000,000 lines | `python3 sorted(key=int)` | 629.7 ms | 1.67x |
| `eza`, 10,000 names | obo `eza` | 35.2 ms | 1.00x |
| `eza`, 10,000 names | `python3 listdir + sort` | 20.4 ms | 0.58x |
| `eza`, 10,000 names | GNU `ls -1` | 8.43 ms | 0.24x |

The pinned eza reference row was skipped because no eza matching
`obo/tests/eza/VERSION` was on `PATH`; the eza rows above compare the repo's
own eza with Python and GNU `ls`.

## Binary sizes

These are freestanding static executables with the obo runtime included; the
four small programs are the same size because each links the same runtime
text/data.

| Program | Bytes |
|---|---:|
| `hello` | 81,921 |
| `fib` | 81,921 |
| `loopsum` | 81,921 |
| `sieve` | 81,921 |
| `cat` (tool) | 94,209 |
| `wc` (tool) | 98,305 |
| `sort` (tool) | 122,881 |
| `head` (tool) | 94,209 |
| `tail` (tool) | 106,497 |
| `eza` (tool) | 270,337 |
| obo compiler | 700,417 |
| seed compiler | 700,417 |

The compiler is the promoted 0.15 fixed point: stage 2 and stage 3 are the same
700,417-byte image, sha256 `8ca8e59e…`, and `seed/obo` is that image. The 0.14
seed (655,361 bytes, sha256 `d7b0c755…`) built it once. The runner built the
benchmark programs and the `cat`, `wc`, `sort` and `eza` tools from the same
tree; `head` and `tail` are from `make -C obo tools`. The eza binary's sha256
is `53bf3a06…`. Every obo binary is freestanding and static, with the runtime
included.

## Memory

### eza

Ten thousand entries, `OBO_STATS=1 OBO_CHECK=1 OBX_GC=poison`:

| View | Live peak (bytes) | Total allocated (bytes) | Max RSS (KiB) |
|---|---:|---:|---:|
| Restricted long | 2,184,368 | 15,529,296 | 2,696 |
| Names | 2,183,712 | 6,568,032 | 2,696 |
| Tree | 2,184,496 | 7,049,888 | 2,696 |
| Colour | 2,183,968 | 18,088,352 | 2,696 |
| Grid | 2,184,144 | 5,794,048 | 2,696 |

The live cap is `EZA_PEAK_MAX = 2,752,512` bytes, unchanged. The pinned
reference was not available on this host, so no reference-memory comparison is
recorded here.

### sort

Peak RSS on the 9,000,000-byte, 1,000,000-line fixed 8-digit fixture, measured
with `/usr/bin/time -v` on 1 November 2026 (median of three runs; the GNU
default-thread row ranged from 136,844 to 137,100 KiB):

| Case | Peak RSS |
|---|---:|
| obo `sort` (default order) | 40,832 KiB (39.9 MiB) |
| obo `sort -n` | 79,872 KiB (78.0 MiB) |
| GNU `sort`, pinned to one core | 58,892 KiB (57.5 MiB) |
| GNU `sort`, default threads (unpinned) | 136,972 KiB (133.8 MiB) |

The obo rows and the pinned GNU row ran on one CPU; the default-threads GNU row
ran unpinned. The default order holds one input buffer plus one start offset
per line; `-n` adds four int arrays indexed by line. Memory is linear in the
input, and there is no external merge.

### Compiler

| Metric | Value |
|---|---:|
| Self-compile wall time | 0.278 s (5 runs, median; range 0.260-0.329 s) |
| Live peak compiling itself | 2,385,424 bytes |
| Total allocated | 52,986,496 bytes |
| Maximum resident set | 4,652 KB |
| Live-memory budget | 2,500,000 bytes (114,576 spare) |
| Compiler size | 700,417 bytes, sha256 `8ca8e59e…` |

The direct self-compile was run from `obo/` as
`OBO_STATS=1 ./obo src/obo.obo -o /tmp/self.obo --rt .`; the port comparison
peaks at 2,385,776 bytes and allocates 52,990,848. The live peak varies by a few
hundred bytes with the compiler source path. The 0.278 s median is higher than
0.195 s on 0.14, but the 0.15 source is 998 lines larger and every benchmark
row on this host ran slower than the 0.14 record, so that movement is not a
clean like-for-like code comparison. Leaks are caught separately by the port
gate's leak check.

## Runtime micro-benchmarks

`python3 obo/bench/micro.py -n 9`:

| Benchmark | Median wall time (ms) | Min-max (ms) | Peak RSS (MB) | Instructions in code segment |
|---|---:|---:|---:|---:|
| loop | 253 | 245-263 | 0.1 | 31 |
| fib | 533 | 524-546 | 0.1 | 65 |
| array | 280 | 272-299 | 16.0 | 85 |
| alloc | 548 | 545-1007 | 0.1 | 42 |

These are medians of 9 timed runs on the same host. Peak RSS comes from the
`peak.c` helper and the instruction count from the program's code segment, as
described in `obo/bench/micro.py`. All four medians are higher than the 0.14
record (loop 224, fib 480, array 244, alloc 500 ms), and the CLI reference rows
rose at the same time, so this is host load rather than a clear code change.
The alloc run had one 1007 ms outlier; treat the table as single-host micro
numbers, not constants.

## Dialect comparison

`python3 obo/bench/bench.py`, after building the historical obx compiler with
`make -C obx`: the same four workloads in both dialects, with peak RSS and the
best wall time of 3 runs. Outputs agree across the compared runs.

| Workload | Lines changed | GC default | GC tuned | GC aggressive | obo |
|---|---|---:|---:|---:|---:|
| lists | 1 of 22 | 8.4 MB, 30 ms | 1.2 MB, 29 ms | 0.5 MB, 44 ms | 0.2 MB, 35 ms |
| strings | 0 of 11 | 8.8 MB, 68 ms | 1.6 MB, 70 ms | 1.0 MB, 108 ms | 0.4 MB, 74 ms |
| tokens | 2 of 67 | 11.9 MB, 28 ms | 7.4 MB, 32 ms | 5.6 MB, 36 ms | 2.4 MB, 23 ms |
| trees | 1 of 25 | 15.1 MB, 47 ms | 10.1 MB, 54 ms | 9.0 MB, 82 ms | 5.0 MB, 24 ms |

`Lines changed` counts lines that differ between the two sources. The obo
compiler uses substantially less memory than the obx collector in these
workloads, with comparable times; it is faster on tokens and trees.

## Interpretation

* **Pure compute.** The three compute rows are about 17x to 354x faster than
  CPython: fib 20.8x, sieve 16.9x and the explicit 100,000,000-iteration sum
  353.8x. Process startup is about 65x faster (0.24 ms against 15.6 ms), which
  matters for tools invoked repeatedly from a shell.
* **`wc`.** `-l` is slightly behind Python's C `bytes.count` (90.0 ms against
  85.9 ms) and well behind GNU's SIMD-assisted `memchr` loop (15.4 ms). `-lwc`
  is a different story: one pass over the data beats Python's chunked counter
  by 4.08x and GNU's word pass by 7.84x on this fixture.
* **`sort`.** The stable radix sort beats Python's C `list.sort` by 2.66x and
  GNU `sort` by 1.10x on the measured 1,000,000-line fixture, and the numeric
  radix path beats GNU `sort -n` by 1.85x and the plain-integer Python
  reference by 1.67x. These medians are higher (slower) than the 0.14 record on
  this host, but the GNU and Python rows rose with them; treat the movement as
  host load rather than a 0.15 regression. These are fixture-specific wins:
  prefix sharing, duplicate-heavy input and variable line width can reverse the
  GNU comparison.
* **`eza`.** Name-only listing is 35.2 ms, slower than Python's
  `listdir + sorted` (20.4 ms) and GNU `ls -1` (8.43 ms). The eza headline is
  not raw listing speed: it is the 270,337-byte static binary, the roughly
  2.18 MB live peak at 10,000 entries, and the differential gates documented in
  [`obo/cmd/README.md`](obo/cmd/README.md).
* **Dialect.** On these four workloads the obo compiler holds less memory than
  the historical obx collector at its default or tuned settings, and is faster
  on the token and tree workloads.

## Caveats

* **Benchmark-only refresh.** Only the benchmark suite, the compiler
  self-compile and `obo/tests/doc_check.py` were rerun for this page. The
  suite, symbol, port and mutation counts in the README metric table are the
  promoted 0.15 release runs, not remeasured here.
* **Host-dependent.** CPU, governor, WSL, background load and thermal state
  move the numbers, especially the sub-millisecond startup row. The two 0.15
  runs differed most on startup (0.20 vs 0.24 ms), `sieve` (133.7 vs
  174.1 ms), `sort -n` (343.2 vs 378.1 ms), `sort` (295.9 vs 319.3 ms) and
  `wc -l` (83.1 vs 90.0 ms); Python and GNU rows moved by up to about 16%.
  `fib` (11.8 vs 11.6 ms), eza (35.2 ms both) and Python `listdir` (20.8 vs
  20.4 ms) stayed within about 2%. Compared with the 0.14 record, nearly every
  absolute median is higher here (for example `fib` 11.6 vs 10.2 ms and GNU
  `sort` 352.1 vs 245.7 ms), so this host was slower overall; do not read the
  movement as a 0.15 codegen change. Compare rows within one table, not across
  runs or machines.
* **Fixture-specific.** The sort and wc results depend on line width, prefix
  sharing, duplication and numeric shape. These are single-file,
  single-process, warm-cache measurements; they say nothing about cold I/O,
  parallel throughput or terminal output.
* **Python is C-backed.** The Python equivalents are idiomatic and lean on C:
  `bytes.count`, `bytes.split`, `list.sort`, `sorted(os.listdir)`. A ratio is
  therefore not a pure language or code-generation comparison.
* **The `sort -n` row is plain integer order.** Its fixture is 1,000,000
  fixed-width, zero-padded 8-digit unsigned integers, so GNU's `strnumcmp`
  semantics reduce to plain integer order (no signs, blanks, fractions or
  overflow). It isolates the numeric path, not every `sort -n` input.
* **`--quick` is not comparable.** It uses smaller fixtures (`fib(30)`, a
  10,000,000 loop, a sieve to 1,000,000, an 11,000,000-byte text file, 100,000
  sort lines and 2,000 names) as well as fewer repetitions, so its absolute
  times do not line up with the tables above.
* **Pinned eza absent.** No eza matching `obo/tests/eza/VERSION` was on this
  host's `PATH`, so the pinned-eza timing and memory rows were skipped rather
  than substituted. The repo's own eza was measured.

## Further reading

* [`obo/bench/cli/README.md`](obo/bench/cli/README.md): how to run the suite,
  the recorded sample, methodology and limitations.
* [`obo/cmd/README.md`](obo/cmd/README.md): the supported tool subsets, their
  behaviour and their differential gates.
* [`HISTORY.md`](HISTORY.md): the earlier 0.5 / 0.6 benchmark sample and its
  provenance, preserved as history rather than presented as current.
