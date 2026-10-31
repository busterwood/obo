# obo performance

This page records the benchmark run of obo 0.13: the compiler, four small
programs, and the `cat`, `wc`, `sort` and `eza` tools, compared with CPython
3.12.3 and GNU coreutils 9.4. It was measured on 31 October 2026 on current
main at commit `8cd46f1`. The obo side is `obo 0.13 sym-format 7 abi 1`; the
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

Two full default runs took 83.3 s and 86.3 s on the host below. The tables are
the second run. The numbers are host-dependent; use them for relative
comparison, not as portable constants.

## Host and versions

* CPU: AMD Ryzen 7 8745HS with Radeon 780M graphics.
* Environment: WSL2, Linux-6.18.54.1-microsoft-standard-WSL2-x86_64-with-glibc2.39.
* Python: CPython 3.12.3.
* References: GNU coreutils 9.4. The pinned eza v0.23.3 reference was absent
  from `PATH`, so its row was skipped.
* Compiler: `obo 0.13 sym-format 7 abi 1`, 647,169 bytes, sha256
  `4ec4e0d6…`; the promoted seed is the same image. The benchmark programs and
  the `cat`, `wc`, `sort` and `eza` tools below were built from main `8cd46f1`
  by the benchmark runner; `head` and `tail` were built from the same tree with
  `make -C obo tools`.
* Date and commit: 31 October 2026, main `8cd46f1`.

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
| Process startup | obo `hello` (static binary) | 0.20 ms | 1.00x |
| Process startup | `python3 -c pass` | 12.3 ms | 62.56x |
| Process startup | `python3 -c print("hi")` | 12.4 ms | 62.90x |
| `fib(32)`, recursive | obo `fib` | 10.2 ms | 1.00x |
| `fib(32)`, recursive | `python3 fib.py` | 206.5 ms | 20.22x |
| Sum below 100,000,000, explicit loop | obo `loopsum` | 22.2 ms | 1.00x |
| Sum below 100,000,000, explicit loop | `python3 loopsum.py` | 8,046 ms | 361.70x |
| Sum below 100,000,000, `sum(range(n))` reference | `python3 sumrange.py` | 1,058 ms | 47.54x |
| Sieve of Eratosthenes to 10,000,000 | obo `sieve` | 142.5 ms | 1.00x |
| Sieve of Eratosthenes to 10,000,000 | `python3 sieve.py` | 2,560 ms | 17.97x |

The `sum(range(n))` row is a different algorithm (Python's C-level builtin),
shown for scale and compared with the obo explicit loop, not an equivalent
implementation.

## CLI tools

| Workload | Implementation | Median | Ratio |
|---|---|---:|---:|
| `wc -l`, 110,000,000 B | obo `wc -l` | 89.9 ms | 1.00x |
| `wc -l`, 110,000,000 B | `python3 wc_lines.py` | 82.1 ms | 0.91x |
| `wc -l`, 110,000,000 B | GNU `wc -l` | 13.6 ms | 0.15x |
| `wc -lwc`, 110,000,000 B | obo `wc -lwc` | 136.9 ms | 1.00x |
| `wc -lwc`, 110,000,000 B | `python3 wc_full.py` | 519.2 ms | 3.79x |
| `wc -lwc`, 110,000,000 B | GNU `wc -lwc` | 1,137 ms | 8.30x |
| Sort 1,000,000 lines | obo `sort` | 204.5 ms | 1.00x |
| Sort 1,000,000 lines | `python3 sort.py` | 604.5 ms | 2.96x |
| Sort 1,000,000 lines | GNU `sort` | 274.3 ms | 1.34x |
| `sort -n`, 1,000,000 lines | obo `sort -n` | 263.2 ms | 1.00x |
| `sort -n`, 1,000,000 lines | GNU `sort -n` | 490.1 ms | 1.86x |
| `sort -n`, 1,000,000 lines | `python3 sorted(key=int)` | 556.6 ms | 2.11x |
| `eza`, 10,000 names | obo `eza` | 29.9 ms | 1.00x |
| `eza`, 10,000 names | `python3 listdir + sort` | 16.8 ms | 0.56x |
| `eza`, 10,000 names | GNU `ls -1` | 6.62 ms | 0.22x |

The pinned eza reference row was skipped because no eza matching
`obo/tests/eza/VERSION` was on `PATH`; the eza rows above compare the repo's
own eza with Python and GNU `ls`.

## Binary sizes

These are freestanding static executables with the obo runtime included; the
four small programs are the same size because each links the same runtime
text/data.

| Program | Bytes |
|---|---:|
| `hello` | 69,633 |
| `fib` | 69,633 |
| `loopsum` | 69,633 |
| `sieve` | 69,633 |
| `cat` (tool) | 81,921 |
| `wc` (tool) | 86,017 |
| `sort` (tool) | 110,593 |
| `head` (tool) | 81,921 |
| `tail` (tool) | 94,209 |
| `eza` (tool) | 258,049 |
| obo compiler | 647,169 |
| seed compiler | 647,169 |

The compiler is the promoted 0.13 fixed point: stage 2 and stage 3 are the
same 647,169-byte image, sha256 `4ec4e0d6…`, and `seed/obo` is that image. The
runner built the benchmark programs and the `cat`, `wc`, `sort` and `eza`
tools from the same tree; `head` and `tail` are from `make -C obo tools`. The
eza binary's sha256 is `845b2c18…`. Every obo binary is freestanding and
static, with the runtime included.

## Memory

### eza

Ten thousand entries, `OBO_STATS=1 OBO_CHECK=1 OBX_GC=poison`:

| View | Live peak (bytes) | Total allocated (bytes) | Max RSS (KiB) |
|---|---:|---:|---:|
| Restricted long | 2,184,368 | 15,529,296 | 2,560 |
| Names | 2,183,712 | 6,568,032 | 2,560 |
| Tree | 2,184,496 | 7,049,888 | 2,560 |
| Colour | 2,183,968 | 18,088,352 | 2,560 |
| Grid | 2,184,144 | 5,794,048 | 2,560 |

The live cap is `EZA_PEAK_MAX = 2,752,512` bytes, unchanged. The pinned
reference was not available on this host, so no reference-memory comparison is
recorded here.

### sort

Peak RSS on the 9,000,000-byte, 1,000,000-line fixed 8-digit fixture, measured
with `/usr/bin/time -v` on 31 October 2026:

| Case | Peak RSS |
|---|---:|
| obo `sort` (default order) | 40,832 KiB (39.9 MiB) |
| obo `sort -n` | 79,872 KiB (78.0 MiB) |
| GNU `sort`, pinned to one core | 58,892 KiB (57.5 MiB) |
| GNU `sort`, default threads (unpinned) | 136,716 KiB (133.5 MiB) |

The obo rows and the pinned GNU row ran on one CPU; the default-threads GNU row
ran unpinned. The default order holds one input buffer plus one start offset
per line; `-n` adds four int arrays indexed by line. Memory is linear in the
input, and there is no external merge.

### Compiler

| Metric | Value |
|---|---:|
| Self-compile wall time | 0.201 s (5 runs, median; range 0.198-0.205 s) |
| Live peak compiling itself | 2,207,824 bytes |
| Total allocated | 49,506,992 bytes |
| Maximum resident set | 4,224 KB |
| Live-memory budget | 2,500,000 bytes (292,176 spare) |
| Compiler size | 647,169 bytes, sha256 `4ec4e0d6…` |

The direct self-compile was run from `obo/` as
`OBO_STATS=1 ./obo src/obo.obo -o /tmp/self.obo --rt .`; the port comparison
peaks at 2,208,192 bytes. The live peak varies by a few hundred bytes with the
compiler source path. Leaks are caught separately by the port gate's leak
check.

## Runtime micro-benchmarks

`python3 obo/bench/micro.py -n 9`:

| Benchmark | Median wall time (ms) | Min-max (ms) | Peak RSS (MB) | Instructions in code segment |
|---|---:|---:|---:|---:|
| loop | 337 | 305-430 | 0.1 | 31 |
| fib | 494 | 485-519 | 0.1 | 65 |
| array | 253 | 246-386 | 16.0 | 85 |
| alloc | 518 | 504-568 | 0.1 | 42 |

These are medians of 9 timed runs on the same host. Peak RSS comes from the
`peak.c` helper and the instruction count from the program's code segment, as
described in `obo/bench/micro.py`. The loop and array rows have the widest
spread; treat the table as single-host micro numbers, not constants.

## Dialect comparison

`python3 obo/bench/bench.py`, after building the historical obx compiler with
`make -C obx`: the same four workloads in both dialects, with peak RSS and the
best wall time of 3 runs. Outputs agree across the compared runs.

| Workload | Lines changed | GC default | GC tuned | GC aggressive | obo |
|---|---|---:|---:|---:|---:|
| lists | 1 of 22 | 8.4 MB, 27 ms | 1.2 MB, 29 ms | 0.5 MB, 38 ms | 0.2 MB, 29 ms |
| strings | 0 of 11 | 8.8 MB, 63 ms | 1.6 MB, 67 ms | 1.0 MB, 98 ms | 0.4 MB, 63 ms |
| tokens | 2 of 67 | 11.9 MB, 26 ms | 7.4 MB, 28 ms | 5.5 MB, 33 ms | 2.4 MB, 21 ms |
| trees | 1 of 25 | 15.1 MB, 33 ms | 10.1 MB, 39 ms | 9.0 MB, 51 ms | 5.0 MB, 21 ms |

`Lines changed` counts lines that differ between the two sources. The obo
compiler uses substantially less memory than the obx collector in these
workloads, with comparable times; it is faster on tokens and trees.

## Interpretation

* **Pure compute.** The three compute rows are about 18x to 362x faster than
  CPython: fib 20.2x, sieve 18.0x and the explicit 100,000,000-iteration sum
  361.7x. Process startup is about 63x faster (0.20 ms against 12.3-12.4 ms),
  which matters for tools invoked repeatedly from a shell.
* **`wc`.** `-l` is slightly behind Python's C `bytes.count` (89.9 ms against
  82.1 ms) and well behind GNU's SIMD-assisted `memchr` loop (13.6 ms). `-lwc`
  is a different story: one pass over the data beats Python's chunked counter
  by 3.79x and GNU's word pass by 8.30x on this fixture.
* **`sort`.** The stable radix sort beats Python's C `list.sort` by 2.96x and
  GNU `sort` by 1.34x on the measured 1,000,000-line fixture, and the numeric
  radix path beats GNU `sort -n` by 1.86x and the plain-integer Python
  reference by 2.11x. These are fixture-specific wins: prefix sharing,
  duplicate-heavy input and variable line width can reverse the GNU
  comparison.
* **`eza`.** Name-only listing is 29.9 ms, slower than Python's
  `listdir + sorted` (16.8 ms) and GNU `ls -1` (6.62 ms). The eza headline is
  not raw listing speed: it is the 258,049-byte static binary, the roughly
  2.18 MB live peak at 10,000 entries, and the differential gates documented in
  [`obo/cmd/README.md`](obo/cmd/README.md).
* **Dialect.** On these four workloads the obo compiler holds less memory than
  the historical obx collector at its default or tuned settings, and is faster
  on the token and tree workloads.

## Caveats

* **Host-dependent.** CPU, governor, WSL, background load and thermal state
  move the numbers, especially the sub-millisecond startup row. Two full default
  runs on this host differed most on obo `sort` (242.7 ms in the first run,
  204.5 ms here), Python's explicit loop (7,460 vs 8,046 ms) and
  `sum(range)` (882 vs 1,058 ms). Other obo rows stayed mostly within about 3%,
  with eza at 5% (fib 10.3/10.2 ms, loopsum 22.8/22.2 ms, sieve
  139.6/142.5 ms, `sort -n` 264.1/263.2 ms, eza 31.4/29.9 ms); startup moved
  from 0.22 to 0.20 ms. Compare rows within one table, not across runs or
  machines.
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
