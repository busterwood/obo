# obo performance

This page records the benchmark run of obo 0.8: the compiler, four small
programs, and the `cat`, `wc`, `sort` and `eza` tools, compared with CPython
3.12.3, GNU coreutils 9.4 and the pinned eza 0.23.3 reference. It was measured
on 30 October 2026 at main `9290396` (the pre-help benchmark tree), as the
second of two default runs. The obo side is `obo 0.8 sym-format 4 abi 1`; the
benchmark programs and tools were built from that tree by
`obo/bench/cli/run.py`. That tree's eza is the 212,993-byte binary (sha256
`53133d23…`) used for the eza timing row; the current merged help build is
221,185 bytes and was not re-benchmarked. The times below were re-measured, not carried
forward from the historical 0.5 / 0.6 sample in [HISTORY.md](HISTORY.md). The
suite is on-demand, not a gate; its source is `obo/bench/cli/`, and the command
is:

```sh
python3 obo/bench/cli/run.py
```

The optional pinned-eza row needs the reference binary passed with `--eza
PATH` (or an eza matching the pin on `PATH`); otherwise that row is skipped.

The run took 83.3 s on the host below. The numbers are host-dependent; use
them for relative comparison, not as portable constants.

## Host and versions

* CPU: AMD Ryzen 7 8745HS with Radeon 780M graphics.
* Environment: WSL2, Linux-6.18.54.1-microsoft-standard-WSL2-x86_64-with-glibc2.39.
* Python: CPython 3.12.3.
* References: GNU coreutils 9.4; pinned eza v0.23.3, binary sha256
  `10457dde…`.
* Compiler: `obo 0.8 sym-format 4 abi 1`, the promoted seed; the benchmark
  tree `9290396` built the programs and tools catalogued below. The eza timing
  row used that tree's 212,993-byte binary (sha256 `53133d23…`). Every median
  on this page was measured on 30 October 2026; the current 221,185-byte eza
  help build (with the larger sleep runtime) was not re-benchmarked.

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
  checked against GNU `sort` output and the eza rows against the pinned
  reference, where available. A mismatch aborts the run.
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
| Process startup | `python3 -c pass` | 12.8 ms | 53.72x |
| Process startup | `python3 -c print("hi")` | 12.2 ms | 51.50x |
| `fib(32)`, recursive | obo `fib` | 10.6 ms | 1.00x |
| `fib(32)`, recursive | `python3 fib.py` | 216.9 ms | 20.54x |
| Sum below 100,000,000, explicit loop | obo `loopsum` | 23.4 ms | 1.00x |
| Sum below 100,000,000, explicit loop | `python3 loopsum.py` | 7,511 ms | 321.52x |
| Sum below 100,000,000, `sum(range(n))` reference | `python3 sumrange.py` | 937.6 ms | 40.13x |
| Sieve of Eratosthenes to 10,000,000 | obo `sieve` | 135.2 ms | 1.00x |
| Sieve of Eratosthenes to 10,000,000 | `python3 sieve.py` | 2,392 ms | 17.69x |

The `sum(range(n))` row is a different algorithm (Python's C-level builtin),
shown for scale and compared with the obo explicit loop, not an equivalent
implementation.

## CLI tools

| Workload | Implementation | Median | Ratio |
|---|---|---:|---:|
| `wc -l`, 110,000,000 B | obo `wc -l` | 78.9 ms | 1.00x |
| `wc -l`, 110,000,000 B | `python3 wc_lines.py` | 73.8 ms | 0.94x |
| `wc -l`, 110,000,000 B | GNU `wc -l` | 12.0 ms | 0.15x |
| `wc -lwc`, 110,000,000 B | obo `wc -lwc` | 131.7 ms | 1.00x |
| `wc -lwc`, 110,000,000 B | `python3 wc_full.py` | 474.5 ms | 3.60x |
| `wc -lwc`, 110,000,000 B | GNU `wc -lwc` | 1,038 ms | 7.88x |
| Sort 1,000,000 lines | obo `sort` | 162.9 ms | 1.00x |
| Sort 1,000,000 lines | `python3 sort.py` | 602.0 ms | 3.70x |
| Sort 1,000,000 lines | GNU `sort` | 256.5 ms | 1.57x |
| `sort -n`, 1,000,000 lines | obo `sort -n` | 255.1 ms | 1.00x |
| `sort -n`, 1,000,000 lines | GNU `sort -n` | 434.7 ms | 1.70x |
| `sort -n`, 1,000,000 lines | `python3 sorted(key=int)` | 516.4 ms | 2.02x |
| `eza`, 10,000 names | obo `eza` | 30.4 ms | 1.00x |
| `eza`, 10,000 names | `python3 listdir + sort` | 16.3 ms | 0.54x |
| `eza`, 10,000 names | GNU `ls -1` | 6.55 ms | 0.22x |
| `eza`, 10,000 names | pinned eza v0.23.3 | 59.2 ms | 1.94x |

## Binary sizes

These are freestanding static executables with the obo runtime included; the
four small programs are the same size because each links the same runtime
text/data.

| Program | Bytes |
|---|---:|
| `hello` | 28,673 |
| `fib` | 28,673 |
| `loopsum` | 28,673 |
| `sieve` | 28,673 |
| `cat` (tool, benchmark tree) | 40,961 |
| `wc` (tool, benchmark tree) | 45,057 |
| `sort` (tool, benchmark tree) | 69,633 |
| `eza` (tool, benchmark tree) | 212,993 |
| `eza` (current merged help build) | 221,185 |
| obo compiler (benchmark tree, promoted 0.8 seed) | 507,905 |
| obo compiler (current 0.8.1, not promoted) | 512,001 |
| pinned eza v0.23.3 reference (not obo code) | 2,294,768 |

The compiler is the promoted 0.8 seed (sha256 `673ae7ba…`); the current source
builds a 512,001-byte `obo 0.8.1` from that seed (not promoted), and the seed
still rebuilds itself to the same 507,905 bytes. The benchmark programs, `cat`,
`wc`, `sort` and the 212,993-byte `eza` binary used for the CLI timings were
built from benchmark tree `9290396` (pre-help). The current tree's `eza` is
221,185 bytes after adding `--help` on the larger sleep runtime. The current
`cat`, `wc`, `sort`, `head` and `tail` are 45,057, 49,153, 73,729, 45,057 and
53,249 bytes. The timings were not re-measured, and the listing benchmark does
not execute help. The current eza binary is about 10.4x smaller than the pinned
reference. The 0.5 seed and its 425,985-byte compiler (sha256 `05542e89…`) are
historical.

## Memory

### eza

Ten thousand entries, `OBO_STATS=1 OBO_CHECK=1 OBX_GC=poison`:

| View | Live peak (bytes) | Max RSS (KiB) |
|---|---:|---:|
| Restricted long | 2,190,352 | 2,560 |
| Names | 2,189,728 | 2,560 |
| Tree | 2,190,480 | 2,560 |
| Colour | 2,189,952 | 2,560 |
| Grid | 2,190,128 | 2,560 |

The values are from the help build on 30 October 2026 (help is not executed by
these entry paths, so the small difference from the earlier E2 record is the
random temporary-path length); reruns varied 2,304-2,560 KiB across views
(names, colour and grid often 2,432). The live cap is
`EZA_PEAK_MAX = 2,752,512` bytes, unchanged. For comparison,
the pinned reference measures about 24.6 MB max RSS in long view and 12-14
MB in the other views on the same trees; that measurement is printed by the
gate and is not gated.

### sort

Recorded peak RSS on the 9,000,000-byte, 1,000,000-line fixed 8-digit fixture
(the tool binary is unchanged; these figures were not re-measured in the
30 October CLI run):

| Case | Peak RSS |
|---|---:|
| obo `sort` (default order) | 40.8 MB |
| obo `sort -n` | 79.9 MB |
| GNU `sort`, default threads | 136.8 MB |
| GNU `sort`, pinned to one core | about 59 MB |
| obo's former readLine + index merge, `sort -n` | 135.6 MB |

The default order holds one input buffer plus one start offset per line; `-n`
adds four int arrays indexed by line. Memory is linear in the input, and there
is no external merge.

### Compiler

| Metric | Value |
|---|---:|
| Self-compile wall time | 0.14 s (5 runs, median; host-dependent) |
| Live peak compiling itself | 1,947,712 bytes |
| Total allocated | 44,626,480 bytes |
| Maximum resident set | 3,640 KB |
| Live-memory budget | 2,000,000 bytes (52,288 spare) |

The live peak varies by a few hundred bytes with the compiler source path (the
main-tree measurement is shown). Leaks are caught separately by the port gate's
leak check.

## Interpretation

* **Pure compute.** The three compute rows are about 18x to 322x faster than
  CPython: fib 20.5x, sieve 17.7x and the explicit 100,000,000-iteration sum
  321.5x. Process startup is about 54x faster (0.24 ms against 12.8 ms), which
  matters for tools invoked repeatedly from a shell.
* **`wc`.** `-l` is close to Python's C `bytes.count` (78.9 ms against 73.8 ms,
  so slightly behind) and well behind GNU's SIMD-assisted `memchr` loop
  (12.0 ms). `-lwc` is a different story: one pass over the data beats Python's
  chunked counter by 3.60x and GNU's word pass by 7.88x on this fixture.
* **`sort`.** The stable radix sort beats Python's C `list.sort` by 3.70x and
  GNU `sort` by 1.57x on the measured 1,000,000-line fixture, and the numeric
  radix path beats GNU `sort -n` by 1.70x and the plain-integer Python
  reference by 2.02x. These are fixture-specific wins: a 73 MB file whose
  lines share a 64-byte prefix measured 614 ms for obo against 461 ms for GNU
  in an earlier clean rerun (GNU wins there), while a 22 MB random
  variable-length file measured 256 ms against GNU's 312 ms, and
  duplicate-heavy variable-length input can favour GNU as well. The old
  readLine + index merge, which measured in seconds, is gone; the current sort
  is a radix sort.
* **`eza`.** Name-only listing is 1.94x faster than the pinned eza reference,
  but Python's `listdir + sorted` (16.3 ms) and GNU `ls -1` (6.55 ms) are faster
  than obo's 30.4 ms. The eza headline is not raw listing speed: it is the
  221,185-byte current binary (10.4x smaller than the reference; the timing run
  used benchmark tree `9290396`'s 212,993-byte build), the roughly 2.19 MB live
  peak at 10,000 entries, and the gate's 554/554 non-memory differential cases
  (465 exact reference goldens, 72 live including 22 pty, 17 own) plus 5/5
  memory peak/leak cases.

## Caveats

* **Host-dependent, and the eza row is noisy.** CPU, governor, WSL, background
  load and thermal state move the numbers, especially the sub-millisecond
  startup row. The first of the two 30 October runs measured eza at 55.9 ms
  (pinned reference 66.9 ms); the second, shown above, measured 30.4 ms
  (pinned 59.2 ms). Other rows agreed within normal run-to-run variance.
  Compare rows within one table, not across runs or machines.
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

## Further reading

* [`obo/bench/cli/README.md`](obo/bench/cli/README.md): how to run the suite,
  the recorded sample, methodology and limitations.
* [`obo/cmd/README.md`](obo/cmd/README.md): the supported tool subsets, their
  behaviour and their differential gates.
* [`HISTORY.md`](HISTORY.md): the earlier 0.5 / 0.6 benchmark sample and its
  provenance, preserved as history rather than presented as current.
