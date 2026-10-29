# obo performance

This page records the final benchmark run of obo 0.5: the compiler, four small
programs, and the `cat`, `wc`, `sort` and `eza` tools, compared with CPython
3.12.3, GNU coreutils 9.4 and the pinned eza 0.23.3 reference. The suite is
on-demand, not a gate; its source is `obo/bench/cli/`, and the command is:

```sh
python3 obo/bench/cli/run.py
```

The run took 78.9 s on the host below. The numbers are host-dependent; use
them for relative comparison, not as portable constants.

## Host and versions

* CPU: AMD Ryzen 7 8745HS with Radeon 780M graphics.
* Environment: WSL2, Linux 6.18.54.1-microsoft-standard-WSL2-x86_64-with-glibc2.39.
* Python: CPython 3.12.3.
* References: GNU coreutils 9.4; pinned eza v0.23.3, binary sha256
  `10457dde…`.
* Compiler: obo 0.5, sym-format 1, abi 1.

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
| Process startup | obo `hello` (static binary) | 0.20 ms | 1.00x |
| Process startup | `python3 -c pass` | 11.0 ms | 54.60x |
| Process startup | `python3 -c print("hi")` | 11.1 ms | 54.66x |
| `fib(32)`, recursive | obo `fib` | 10.0 ms | 1.00x |
| `fib(32)`, recursive | `python3 fib.py` | 206.4 ms | 20.57x |
| Sum below 100,000,000, explicit loop | obo `loopsum` | 21.8 ms | 1.00x |
| Sum below 100,000,000, explicit loop | `python3 loopsum.py` | 7,343 ms | 337.01x |
| Sum below 100,000,000, `sum(range(n))` reference | `python3 sumrange.py` | 881.3 ms | 40.45x |
| Sieve of Eratosthenes to 10,000,000 | obo `sieve` | 127.4 ms | 1.00x |
| Sieve of Eratosthenes to 10,000,000 | `python3 sieve.py` | 2,474 ms | 19.43x |

The `sum(range(n))` row is a different algorithm (Python's C-level builtin),
shown for scale and compared with the obo explicit loop, not an equivalent
implementation.

## CLI tools

| Workload | Implementation | Median | Ratio |
|---|---|---:|---:|
| `wc -l`, 110,000,000 B | obo `wc -l` | 79.7 ms | 1.00x |
| `wc -l`, 110,000,000 B | `python3 wc_lines.py` | 70.1 ms | 0.88x |
| `wc -l`, 110,000,000 B | GNU `wc -l` | 11.3 ms | 0.14x |
| `wc -lwc`, 110,000,000 B | obo `wc -lwc` | 124.8 ms | 1.00x |
| `wc -lwc`, 110,000,000 B | `python3 wc_full.py` | 454.9 ms | 3.65x |
| `wc -lwc`, 110,000,000 B | GNU `wc -lwc` | 972.0 ms | 7.79x |
| Sort 1,000,000 lines | obo `sort` | 158.7 ms | 1.00x |
| Sort 1,000,000 lines | `python3 sort.py` | 545.3 ms | 3.44x |
| Sort 1,000,000 lines | GNU `sort` | 233.6 ms | 1.47x |
| `sort -n`, 1,000,000 lines | obo `sort -n` | 197.3 ms | 1.00x |
| `sort -n`, 1,000,000 lines | GNU `sort -n` | 398.7 ms | 2.02x |
| `sort -n`, 1,000,000 lines | `python3 sorted(key=int)` | 477.9 ms | 2.42x |
| `eza`, 10,000 names | obo `eza` | 27.8 ms | 1.00x |
| `eza`, 10,000 names | `python3 listdir + sort` | 15.9 ms | 0.57x |
| `eza`, 10,000 names | GNU `ls -1` | 6.49 ms | 0.23x |
| `eza`, 10,000 names | pinned eza v0.23.3 | 57.0 ms | 2.05x |

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
| `cat` (tool) | 40,961 |
| `wc` (tool) | 45,057 |
| `sort` (tool) | 69,633 |
| `eza` (tool) | 212,993 |
| obo compiler | 425,985 |
| pinned eza v0.23.3 reference (not obo code) | 2,294,768 |

The compiler is the promoted 0.5 seed (sha256 `05542e89…`), and it rebuilds
itself to the same 425,985 bytes. The eza binary is about 10.8x smaller than
the pinned reference.

## Memory

### eza

Ten thousand entries, `OBO_STATS=1 OBO_CHECK=1 OBX_GC=poison`:

| View | Live peak (bytes) | Max RSS (KiB) |
|---|---:|---:|
| Restricted long | 2,190,032 | 2,560 |
| Names | 2,189,408 | 2,560 |
| Tree | 2,190,160 | 2,560 |
| Colour | 2,189,632 | 2,560 |
| Grid | 2,189,808 | 2,560 |

The live cap is `EZA_PEAK_MAX = 2,752,512` bytes, unchanged. For comparison,
the pinned reference measures about 24.6 MB max RSS in long view and 12.0-14.2
MB in the other views on the same trees; that measurement is printed by the
gate and is not gated.

### sort

Peak RSS on the 9,000,000-byte, 1,000,000-line fixed 8-digit fixture:

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
| Self-compile wall time | 0.13 s (5 runs, median) |
| Live peak compiling itself | 1,646,816 bytes |
| Total allocated | 35,353,648 bytes |
| Maximum resident set | 2,976 KB |
| Live-memory budget | 2,000,000 bytes (353,184 spare) |

The live peak varies by a few hundred bytes with the compiler source path.
Leaks are caught separately by the port gate's leak check.

## Interpretation

* **Pure compute.** The three compute rows are 19x to 337x faster than CPython:
  fib 20.6x, sieve 19.4x and the explicit 100,000,000-iteration sum 337x.
  Process startup is about 55x faster (0.20 ms against 11.0 ms), which matters
  for tools invoked repeatedly from a shell.
* **`wc`.** `-l` is at parity with Python's C `bytes.count` (79.7 ms against
  70.1 ms, so slightly behind) and well behind GNU's SIMD-assisted `memchr`
  loop (11.3 ms). `-lwc` is a different story: one pass over the data beats
  Python's chunked counter by 3.65x and GNU's word pass by 7.79x on this
  fixture.
* **`sort`.** The stable radix sort beats Python's C `list.sort` by 3.44x and
  GNU `sort` by 1.47x on the measured 1,000,000-line fixture, and the numeric
  radix path beats GNU `sort -n` by 2.02x and the plain-integer Python
  reference by 2.42x. These are fixture-specific wins: a 73 MB file whose
  lines share a 64-byte prefix measured 650 ms for obo against 480 ms for GNU,
  and duplicate-heavy variable-length input can favour GNU as well. The old
  readLine + index merge, which measured in seconds, is gone; the current sort
  is a radix sort.
* **`eza`.** Name-only listing is 2.05x faster than the pinned eza reference,
  but Python's `listdir + sorted` (15.9 ms) and GNU `ls -1` (6.49 ms) are
  faster than obo's 27.8 ms. The eza headline is not raw listing speed: it is
  the 212,993-byte binary (10.8x smaller than the reference), the roughly 2.19
  MB live peak at 10,000 entries, and byte-for-byte agreement with the
  reference across the differential gate (546/546 non-memory cases and 5/5
  memory cases).

## Caveats

* **Host-dependent.** CPU, governor, WSL, background load and thermal state
  move the numbers, especially the sub-millisecond startup row. Compare rows
  within one table, not across runs or machines.
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
