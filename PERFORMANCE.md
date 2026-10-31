# obo performance

This page records the benchmark run of obo 0.14: the compiler, four small
programs, and the `cat`, `wc`, `sort` and `eza` tools, compared with CPython
3.12.3 and GNU coreutils 9.4. It was measured on 31 October 2026 on current
main at commit `1db9995`. The obo side is `obo 0.14 sym-format 7 abi 1`; the
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

Two full default runs took 79.6 s and 78.9 s on the host below. The tables are
the second run. The numbers are host-dependent; use them for relative
comparison, not as portable constants.

## Host and versions

* CPU: AMD Ryzen 7 8745HS with Radeon 780M graphics.
* Environment: WSL2, Linux-6.18.54.1-microsoft-standard-WSL2-x86_64-with-glibc2.39.
* Python: CPython 3.12.3.
* References: GNU coreutils 9.4. The pinned eza v0.23.3 reference was absent
  from `PATH`, so its row was skipped.
* Compiler: `obo 0.14 sym-format 7 abi 1`, 655,361 bytes, sha256
  `63437c3c…`; the promoted seed is the same image. The benchmark programs and
  the `cat`, `wc`, `sort` and `eza` tools below were built from main `1db9995`
  by the benchmark runner; `head` and `tail` were built from the same tree with
  `make -C obo tools`.
* Date and commit: 31 October 2026, main `1db9995`.

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
| Process startup | `python3 -c pass` | 11.8 ms | 58.54x |
| Process startup | `python3 -c print("hi")` | 11.8 ms | 58.38x |
| `fib(32)`, recursive | obo `fib` | 10.2 ms | 1.00x |
| `fib(32)`, recursive | `python3 fib.py` | 204.9 ms | 20.04x |
| Sum below 100,000,000, explicit loop | obo `loopsum` | 22.1 ms | 1.00x |
| Sum below 100,000,000, explicit loop | `python3 loopsum.py` | 7,179 ms | 324.68x |
| Sum below 100,000,000, `sum(range(n))` reference | `python3 sumrange.py` | 889.3 ms | 40.22x |
| Sieve of Eratosthenes to 10,000,000 | obo `sieve` | 135.9 ms | 1.00x |
| Sieve of Eratosthenes to 10,000,000 | `python3 sieve.py` | 2,204 ms | 16.22x |

The `sum(range(n))` row is a different algorithm (Python's C-level builtin),
shown for scale and compared with the obo explicit loop, not an equivalent
implementation.

## CLI tools

| Workload | Implementation | Median | Ratio |
|---|---|---:|---:|
| `wc -l`, 110,000,000 B | obo `wc -l` | 78.1 ms | 1.00x |
| `wc -l`, 110,000,000 B | `python3 wc_lines.py` | 71.9 ms | 0.92x |
| `wc -l`, 110,000,000 B | GNU `wc -l` | 11.4 ms | 0.15x |
| `wc -lwc`, 110,000,000 B | obo `wc -lwc` | 126.4 ms | 1.00x |
| `wc -lwc`, 110,000,000 B | `python3 wc_full.py` | 471.8 ms | 3.73x |
| `wc -lwc`, 110,000,000 B | GNU `wc -lwc` | 1,005 ms | 7.95x |
| Sort 1,000,000 lines | obo `sort` | 173.3 ms | 1.00x |
| Sort 1,000,000 lines | `python3 sort.py` | 585.3 ms | 3.38x |
| Sort 1,000,000 lines | GNU `sort` | 245.7 ms | 1.42x |
| `sort -n`, 1,000,000 lines | obo `sort -n` | 196.6 ms | 1.00x |
| `sort -n`, 1,000,000 lines | GNU `sort -n` | 476.7 ms | 2.43x |
| `sort -n`, 1,000,000 lines | `python3 sorted(key=int)` | 495.1 ms | 2.52x |
| `eza`, 10,000 names | obo `eza` | 29.8 ms | 1.00x |
| `eza`, 10,000 names | `python3 listdir + sort` | 16.1 ms | 0.54x |
| `eza`, 10,000 names | GNU `ls -1` | 6.42 ms | 0.22x |

The pinned eza reference row was skipped because no eza matching
`obo/tests/eza/VERSION` was on `PATH`; the eza rows above compare the repo's
own eza with Python and GNU `ls`.

## Binary sizes

These are freestanding static executables with the obo runtime included; the
four small programs are the same size because each links the same runtime
text/data.

| Program | Bytes |
|---|---:|
| `hello` | 73,729 |
| `fib` | 73,729 |
| `loopsum` | 73,729 |
| `sieve` | 73,729 |
| `cat` (tool) | 86,017 |
| `wc` (tool) | 90,113 |
| `sort` (tool) | 114,689 |
| `head` (tool) | 86,017 |
| `tail` (tool) | 98,305 |
| `eza` (tool) | 262,145 |
| obo compiler | 655,361 |
| seed compiler | 655,361 |

The compiler is the promoted 0.14 fixed point: stage 2 and stage 3 are the
same 655,361-byte image, sha256 `63437c3c…`, and `seed/obo` is that image. The
runner built the benchmark programs and the `cat`, `wc`, `sort` and `eza`
tools from the same tree; `head` and `tail` are from `make -C obo tools`. The
eza binary's sha256 is `4d30009f…`. Every obo binary is freestanding and
static, with the runtime included.

## Memory

### eza

Ten thousand entries, `OBO_STATS=1 OBO_CHECK=1 OBX_GC=poison`:

| View | Live peak (bytes) | Total allocated (bytes) | Max RSS (KiB) |
|---|---:|---:|---:|
| Restricted long | 2,184,368 | 15,529,296 | 2,608 |
| Names | 2,183,712 | 6,568,032 | 2,608 |
| Tree | 2,184,496 | 7,049,888 | 2,608 |
| Colour | 2,183,968 | 18,088,352 | 2,608 |
| Grid | 2,184,144 | 5,794,048 | 2,608 |

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
| GNU `sort`, default threads (unpinned) | 136,972 KiB (133.8 MiB) |

The obo rows and the pinned GNU row ran on one CPU; the default-threads GNU row
ran unpinned. The default order holds one input buffer plus one start offset
per line; `-n` adds four int arrays indexed by line. Memory is linear in the
input, and there is no external merge.

### Compiler

| Metric | Value |
|---|---:|
| Self-compile wall time | 0.195 s (5 runs, median; range 0.194-0.203 s) |
| Live peak compiling itself | 2,238,928 bytes |
| Total allocated | 49,850,816 bytes |
| Maximum resident set | 4,352 KB |
| Live-memory budget | 2,500,000 bytes (261,072 spare) |
| Compiler size | 655,361 bytes, sha256 `63437c3c…` |

The direct self-compile was run from `obo/` as
`OBO_STATS=1 ./obo src/obo.obo -o /tmp/self.obo --rt .`; the port comparison
peaks at 2,239,056 bytes. The live peak varies by a few hundred bytes with the
compiler source path. The 0.195 s median is essentially unchanged from 0.201 s
on 0.13 (within run-to-run noise on this host). Leaks are caught separately by
the port gate's leak check.

## Runtime micro-benchmarks

`python3 obo/bench/micro.py -n 9`:

| Benchmark | Median wall time (ms) | Min-max (ms) | Peak RSS (MB) | Instructions in code segment |
|---|---:|---:|---:|---:|
| loop | 224 | 220-230 | 0.1 | 31 |
| fib | 480 | 478-502 | 0.1 | 65 |
| array | 244 | 242-248 | 16.0 | 85 |
| alloc | 500 | 493-891 | 0.1 | 42 |

These are medians of 9 timed runs on the same host. Peak RSS comes from the
`peak.c` helper and the instruction count from the program's code segment, as
described in `obo/bench/micro.py`. The loop median is lower than the 337 ms
recorded for 0.13, but a 0.13 recheck measured 238 ms, so that movement is host
variance rather than a clear code change. The alloc run had one 891 ms outlier;
treat the table as single-host micro numbers, not constants.

## Dialect comparison

`python3 obo/bench/bench.py`, after building the historical obx compiler with
`make -C obx`: the same four workloads in both dialects, with peak RSS and the
best wall time of 3 runs. Outputs agree across the compared runs.

| Workload | Lines changed | GC default | GC tuned | GC aggressive | obo |
|---|---|---:|---:|---:|---:|
| lists | 1 of 22 | 8.4 MB, 28 ms | 1.2 MB, 29 ms | 0.5 MB, 39 ms | 0.2 MB, 32 ms |
| strings | 0 of 11 | 8.8 MB, 62 ms | 1.6 MB, 66 ms | 1.0 MB, 98 ms | 0.4 MB, 68 ms |
| tokens | 2 of 67 | 11.9 MB, 26 ms | 7.4 MB, 30 ms | 5.6 MB, 33 ms | 2.4 MB, 21 ms |
| trees | 1 of 25 | 15.1 MB, 35 ms | 10.1 MB, 37 ms | 9.0 MB, 46 ms | 5.0 MB, 22 ms |

`Lines changed` counts lines that differ between the two sources. The obo
compiler uses substantially less memory than the obx collector in these
workloads, with comparable times; it is faster on tokens and trees.

## Interpretation

* **Pure compute.** The three compute rows are about 16x to 325x faster than
  CPython: fib 20.0x, sieve 16.2x and the explicit 100,000,000-iteration sum
  324.7x. Process startup is about 58x faster (0.20 ms against 11.8 ms), which
  matters for tools invoked repeatedly from a shell.
* **`wc`.** `-l` is slightly behind Python's C `bytes.count` (78.1 ms against
  71.9 ms) and well behind GNU's SIMD-assisted `memchr` loop (11.4 ms). `-lwc`
  is a different story: one pass over the data beats Python's chunked counter
  by 3.73x and GNU's word pass by 7.95x on this fixture.
* **`sort`.** The stable radix sort beats Python's C `list.sort` by 3.38x and
  GNU `sort` by 1.42x on the measured 1,000,000-line fixture, and the numeric
  radix path beats GNU `sort -n` by 2.43x and the plain-integer Python
  reference by 2.52x. These medians are lower than the 0.13 run on this host,
  but the two 0.14 runs differed by about 2% on `sort` and 8% on `sort -n`;
  treat the movement as partly host noise. These are fixture-specific wins:
  prefix sharing, duplicate-heavy input and variable line width can reverse the
  GNU comparison.
* **`eza`.** Name-only listing is 29.8 ms, slower than Python's
  `listdir + sorted` (16.1 ms) and GNU `ls -1` (6.42 ms). The eza headline is
  not raw listing speed: it is the 262,145-byte static binary, the roughly
  2.18 MB live peak at 10,000 entries, and the differential gates documented in
  [`obo/cmd/README.md`](obo/cmd/README.md).
* **Dialect.** On these four workloads the obo compiler holds less memory than
  the historical obx collector at its default or tuned settings, and is faster
  on the token and tree workloads.

## Caveats

* **Host-dependent.** CPU, governor, WSL, background load and thermal state
  move the numbers, especially the sub-millisecond startup row. The two 0.14
  runs differed most on Python startup (13.0 vs 11.8 ms), the explicit loop
  (24.2 vs 22.1 ms), `wc -l` (72.7 vs 78.1 ms) and `sort -n` (212.2 vs
  196.6 ms). The remaining obo rows moved by 1.7-3.2% (`fib` 2.9%, `sieve`
  3.2%, `sort` 2.1%, eza 1.7%). Compared with the earlier 0.13 run, most rows
  are within that run's spread; the sort and `wc -l` medians are lower here.
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
