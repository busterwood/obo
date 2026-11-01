# obo performance

This page records the benchmark run of obo 0.15: the compiler, four small
programs, and the `cat`, `wc`, `sort` and `eza` tools, compared with CPython
3.12.3 and GNU coreutils 9.4. It was measured on 1 November 2026 at commit
`b61f189`, the promoted runtime-cleanup 0.15 image. The obo side is
`obo 0.15 sym-format 8 abi 1`; the benchmark programs and tools were built from
this tree by `obo/bench/cli/run.py`. The pinned eza 0.23.3 reference was not
present on this host's `PATH`, so that reference row was skipped; no vendored
eza was used. The suite is on-demand, not a gate; its source is
`obo/bench/cli/`, and the command is:

```sh
python3 obo/bench/cli/run.py
```

The pinned-eza row is optional and needs a binary matching
`obo/tests/eza/VERSION` passed with `--eza PATH` (or an eza on `PATH`);
otherwise only that row is skipped.

Two full default runs reported 87.0 s and 84.1 s on the host below. The tables
are the second run. The numbers are host-dependent; use them for relative
comparison, not as portable constants. In particular, the sort rows moved by
about 20% between the two runs (see [Caveats](#caveats)).

## Host and versions

* CPU: AMD Ryzen 7 8745HS with Radeon 780M graphics.
* Environment: WSL2, Linux-6.18.54.1-microsoft-standard-WSL2-x86_64-with-glibc2.39.
* Python: CPython 3.12.3.
* References: GNU coreutils 9.4. The pinned eza v0.23.3 reference was absent
  from `PATH`, so its row was skipped.
* Compiler: `obo 0.15 sym-format 8 abi 1`, 696,321 bytes, sha256
  `4213f9a3…`. Stage 2 and stage 3 are the same image. `seed/obo` is the
  promoted 0.15 image (same size and hash). The previous 700,417-byte 0.15
  image (sha256 `8ca8e59e…`) built it once; the 0.14 seed (655,361 bytes,
  sha256 `d7b0c755…`) built that earlier 0.15 image. The benchmark programs and
  the `cat`, `wc`, `sort` and `eza` tools below were built from `b61f189` by the
  benchmark runner; `head` and `tail` were built from the same tree with
  `make -C obo tools`.
* Date and commit: 1 November 2026, `b61f189`.

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
| Process startup | obo `hello` (static binary) | 0.21 ms | 1.00x |
| Process startup | `python3 -c pass` | 12.5 ms | 58.42x |
| Process startup | `python3 -c print("hi")` | 16.0 ms | 74.93x |
| `fib(32)`, recursive | obo `fib` | 11.6 ms | 1.00x |
| `fib(32)`, recursive | `python3 fib.py` | 219.4 ms | 18.94x |
| Sum below 100,000,000, explicit loop | obo `loopsum` | 24.3 ms | 1.00x |
| Sum below 100,000,000, explicit loop | `python3 loopsum.py` | 7,829 ms | 322.59x |
| Sum below 100,000,000, `sum(range(n))` reference | `python3 sumrange.py` | 963.6 ms | 39.71x |
| Sieve of Eratosthenes to 10,000,000 | obo `sieve` | 136.5 ms | 1.00x |
| Sieve of Eratosthenes to 10,000,000 | `python3 sieve.py` | 2,355 ms | 17.26x |

The `sum(range(n))` row is a different algorithm (Python's C-level builtin),
shown for scale and compared with the obo explicit loop, not an equivalent
implementation.

## CLI tools

| Workload | Implementation | Median | Ratio |
|---|---|---:|---:|
| `wc -l`, 110,000,000 B | obo `wc -l` | 80.4 ms | 1.00x |
| `wc -l`, 110,000,000 B | `python3 wc_lines.py` | 77.3 ms | 0.96x |
| `wc -l`, 110,000,000 B | GNU `wc -l` | 12.2 ms | 0.15x |
| `wc -lwc`, 110,000,000 B | obo `wc -lwc` | 133.4 ms | 1.00x |
| `wc -lwc`, 110,000,000 B | `python3 wc_full.py` | 480.6 ms | 3.60x |
| `wc -lwc`, 110,000,000 B | GNU `wc -lwc` | 1,049 ms | 7.86x |
| Sort 1,000,000 lines | obo `sort` | 170.9 ms | 1.00x |
| Sort 1,000,000 lines | `python3 sort.py` | 598.1 ms | 3.50x |
| Sort 1,000,000 lines | GNU `sort` | 264.2 ms | 1.55x |
| `sort -n`, 1,000,000 lines | obo `sort -n` | 216.6 ms | 1.00x |
| `sort -n`, 1,000,000 lines | GNU `sort -n` | 447.2 ms | 2.06x |
| `sort -n`, 1,000,000 lines | `python3 sorted(key=int)` | 525.5 ms | 2.43x |
| `eza`, 10,000 names | obo `eza` | 31.0 ms | 1.00x |
| `eza`, 10,000 names | `python3 listdir + sort` | 17.2 ms | 0.56x |
| `eza`, 10,000 names | GNU `ls -1` | 6.99 ms | 0.23x |

The pinned eza reference row was skipped because no eza matching
`obo/tests/eza/VERSION` was on `PATH`; the eza rows above compare the repo's
own eza with Python and GNU `ls`.

## Binary sizes

These are freestanding static executables with the obo runtime included; the
four small programs are the same size because each links the same runtime
text/data. Removing the disabled collector and `gc` builtins in the runtime
cleanup shrank every obo binary by 4,096 bytes.

| Program | Bytes |
|---|---:|
| `hello` | 77,825 |
| `fib` | 77,825 |
| `loopsum` | 77,825 |
| `sieve` | 77,825 |
| `cat` (tool) | 90,113 |
| `wc` (tool) | 94,209 |
| `sort` (tool) | 118,785 |
| `head` (tool) | 90,113 |
| `tail` (tool) | 102,401 |
| `eza` (tool) | 266,241 |
| obo compiler | 696,321 |
| seed compiler | 696,321 |

The compiler is the promoted 0.15 fixed point: stage 2 and stage 3 are the same
696,321-byte image, sha256 `4213f9a3…`, and `seed/obo` is that image. The
previous 700,417-byte 0.15 image (sha256 `8ca8e59e…`) built it once; the 0.14
seed (655,361 bytes, sha256 `d7b0c755…`) built that earlier image. The runner
built the benchmark programs and the `cat`, `wc`, `sort` and `eza` tools from the
same tree; `head` and `tail` are from `make -C obo tools`. The eza binary's
sha256 is `7a4e85f5…`. Every obo binary is freestanding and static, with the
runtime included.

## Memory

### eza

Ten thousand entries, `OBO_STATS=1 OBO_CHECK=1 OBX_GC=poison`:

| View | Live peak (bytes) | Total allocated (bytes) | Max RSS (KiB) |
|---|---:|---:|---:|
| Restricted long | 2,184,368 | 15,529,296 | 2,692 |
| Names | 2,183,712 | 6,568,032 | 2,692 |
| Tree | 2,184,496 | 7,049,888 | 2,692 |
| Colour | 2,183,968 | 18,088,352 | 2,692 |
| Grid | 2,184,144 | 5,794,048 | 2,692 |

The live cap is `EZA_PEAK_MAX = 2,752,512` bytes, unchanged; the runtime cleanup
did not change these heap figures. The pinned reference was not available on
this host, so no reference-memory comparison is recorded here.

### sort

Peak RSS on the 9,000,000-byte, 1,000,000-line fixed 8-digit fixture, measured
with `/usr/bin/time -v` on 1 November 2026 (median of three runs; the GNU
default-thread row ranged from 136,716 to 137,100 KiB):

| Case | Peak RSS |
|---|---:|
| obo `sort` (default order) | 40,832 KiB (39.9 MiB) |
| obo `sort -n` | 79,872 KiB (78.0 MiB) |
| GNU `sort`, pinned to one core | 58,892 KiB (57.5 MiB) |
| GNU `sort`, default threads (unpinned) | 136,844 KiB (133.6 MiB) |

The obo rows and the pinned GNU row ran on one CPU; the default-threads GNU row
ran unpinned. The default order holds one input buffer plus one start offset
per line; `-n` adds four int arrays indexed by line. Memory is linear in the
input, and there is no external merge.

### Compiler

| Metric | Value |
|---|---:|
| Self-compile wall time | 0.221 s (5 runs, median; range 0.2199-0.2242 s) |
| Live peak compiling itself | 2,382,928 bytes |
| Total allocated | 52,932,208 bytes |
| Maximum resident set | 4,648 KB |
| Live-memory budget | 2,500,000 bytes (117,072 spare) |
| Compiler size | 696,321 bytes, sha256 `4213f9a3…` |

The direct self-compile was run from `obo/` as
`OBO_STATS=1 ./obo src/obo.obo -o /tmp/self.obo --rt .`; the port comparison
peaks at 2,383,280 bytes and allocates 52,936,512 (116,720 spare). The live peak
varies by a few hundred bytes with the compiler source path. The 0.221 s median
is lower than the previous 0.15 record of 0.278 s but still above 0.195 s on
0.14, while the source is 974 lines larger than 0.14; host conditions moved
both records, so this is not a clean like-for-like code comparison. Leaks are
caught separately by the port gate's leak check.

## Runtime micro-benchmarks

`python3 obo/bench/micro.py -n 9`:

| Benchmark | Median wall time (ms) | Min-max (ms) | Peak RSS (MB) | Instructions in code segment |
|---|---:|---:|---:|---:|
| loop | 237 | 229-253 | 0.1 | 31 |
| fib | 505 | 503-511 | 0.1 | 65 |
| array | 268 | 252-283 | 16.0 | 85 |
| alloc | 530 | 495-786 | 0.1 | 42 |

These are medians of 9 timed runs on the same host. Peak RSS comes from the
`peak.c` helper and the instruction count from the program's code segment, as
described in `obo/bench/micro.py`. All four medians are lower than the previous
0.15 record (loop 253, fib 533, array 280, alloc 548 ms) yet higher than the
0.14 record (loop 224, fib 480, array 244, alloc 500 ms); the CLI reference
rows moved with them, so this is host load rather than a clear code change. The
alloc run had one 786 ms outlier; treat the table as single-host micro numbers,
not constants.

## Dialect comparison

`python3 obo/bench/bench.py`, after building the historical obx compiler with
`make -C obx`: the same four workloads in both dialects, with peak RSS and the
best wall time of 3 runs. Outputs agree across the compared runs.

| Workload | Lines changed | GC default | GC tuned | GC aggressive | obo |
|---|---|---:|---:|---:|---:|
| lists | 1 of 22 | 8.4 MB, 29 ms | 1.2 MB, 29 ms | 0.5 MB, 39 ms | 0.2 MB, 33 ms |
| strings | 0 of 11 | 8.8 MB, 65 ms | 1.6 MB, 70 ms | 1.0 MB, 104 ms | 0.4 MB, 65 ms |
| tokens | 2 of 67 | 11.9 MB, 27 ms | 7.4 MB, 29 ms | 5.6 MB, 35 ms | 2.4 MB, 21 ms |
| trees | 1 of 25 | 15.1 MB, 35 ms | 10.1 MB, 36 ms | 9.0 MB, 47 ms | 5.0 MB, 23 ms |

`Lines changed` counts lines that differ between the two sources. The obo
compiler uses substantially less memory than the obx collector in these
workloads, with comparable times; it is faster on tokens and trees.

## Interpretation

* **Pure compute.** The three compute rows are about 17x to 323x faster than
  CPython: fib 18.9x, sieve 17.3x and the explicit 100,000,000-iteration sum
  322.6x. Process startup is about 58x faster (0.21 ms against 12.5 ms), which
  matters for tools invoked repeatedly from a shell.
* **`wc`.** `-l` is slightly behind Python's C `bytes.count` (80.4 ms against
  77.3 ms) and well behind GNU's SIMD-assisted `memchr` loop (12.2 ms). `-lwc`
  is a different story: one pass over the data beats Python's chunked counter
  by 3.60x and GNU's word pass by 7.86x on this fixture.
* **`sort`.** The stable radix sort beats Python's C `list.sort` by 3.50x and
  GNU `sort` by 1.55x on the measured 1,000,000-line fixture, and the numeric
  radix path beats GNU `sort -n` by 2.06x and the plain-integer Python
  reference by 2.43x. These numbers moved by about 20% between the two runs on
  this host (the first run measured 215.2 ms for `sort` and 267.6 ms for
  `sort -n`), and the previous 0.15 record was slower still; treat that
  movement as host load, not a codegen change. These are fixture-specific
  wins: prefix sharing, duplicate-heavy input and variable line width can
  reverse the GNU comparison.
* **`eza`.** Name-only listing is 31.0 ms, slower than Python's
  `listdir + sorted` (17.2 ms) and GNU `ls -1` (6.99 ms). The eza headline is
  not raw listing speed: it is the 266,241-byte static binary, the roughly
  2.18 MB live peak at 10,000 entries, and the differential gates documented in
  [`obo/cmd/README.md`](obo/cmd/README.md).
* **Dialect.** On these four workloads the obo compiler holds less memory than
  the historical obx collector at its default or tuned settings, and is faster
  on the token and tree workloads.

## Caveats

* **Benchmark-only refresh.** Only the benchmark suite, the compiler
  self-compile, the eza and sort memory measurements and `obo/tests/doc_check.py`
  were rerun for this page. The suite, symbol, port, module, import and stdlib
  counts in the README metric table are the promoted 0.15 release runs, not
  remeasured here.
* **Host-dependent.** CPU, governor, WSL, background load and thermal state
  move the numbers, especially the sub-millisecond startup row. The two 0.15
  runs differed most on `sort` (215.2 vs 170.9 ms), `sort -n` (267.6 vs
  216.6 ms), `sieve` (141.4 vs 136.5 ms), `wc -l` (78.2 vs 80.4 ms) and
  `wc -lwc` (131.6 vs 133.4 ms); Python and GNU rows moved by up to about 20%
  (the `python3 -c print("hi")` row was 13.3 vs 16.0 ms). This host ran faster
  than the previous 0.15 record at `57298f3` on many rows (for example `sort`
  170.9 vs 319.3 ms and `sort -n` 216.6 vs 378.1 ms), so do not read the
  movement as a runtime-cleanup codegen change. Compare rows within one table,
  not across runs or machines.
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
