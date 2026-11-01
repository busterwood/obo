# obo CLI benchmarks (on-demand)

This directory compares obo programs and the repo's command-line tools with
equivalent Python 3 programs, and with GNU tools and the pinned eza where a
reference is useful. It is a manual tool for occasional comparisons on one
host.

**It is not a gate.** No script under `obo/tests/` and no CI job runs it. The
numbers are host-dependent; treat them as relative measurements from one run,
not as portable facts. The recorded table below is an obo 0.15 run measured on
1 November 2026 at main `57298f3`. Everything on the obo side was built from
that tree by `run.py`; the repo's own eza is the 270,337-byte binary (sha256
`53bf3a06…`). The pinned eza reference was not present on `PATH`, so that row
was skipped and no reference binary was substituted. The historical 0.5 / 0.6
sample and its provenance are in [`HISTORY.md`](../../../HISTORY.md).

## What it measures

| Workload | obo side | Python side | Reference rows |
|---|---|---|---|
| process startup | `hello.obx`, a static binary printing one line | `python3 -c pass`, `python3 -c print("hi")` | — |
| `fib(32)` | `fib.obx`, recursive | `fib.py`, the same call tree | — |
| sum of integers below `100,000,000` | `loopsum.obx`, explicit `while` loop | `loopsum.py`, the same loop | `sumrange.py`: Python's C-level `sum(range(n))`, a different algorithm |
| sieve of Eratosthenes `<= 10,000,000` | `sieve.obx` | `sieve.py`, the same bytearray sieve | — |
| `wc -l` on a 110,000,000-byte file | repo `wc -l` | `wc_lines.py`, chunked newline count | GNU `wc -l` |
| `wc -lwc` on the same file | repo `wc -lwc` | `wc_full.py`, chunked line/word/byte count | GNU `wc -lwc` |
| sort 1,000,000 lines | repo `sort` | `sort.py`, `bytes.split(b'\n')` then `list.sort` | GNU `sort` |
| `sort -n` 1,000,000 lines | repo `sort -n` | `sort_n.py`, `sorted(lines, key=int)` (plain-integer reference) | GNU `sort -n` |
| eza 10,000 names | repo `eza`, name-only non-TTY | `listdir.py`, `sorted(os.listdir(...))` | GNU `ls -1`; the pinned eza when available |

The ratio column is **this row's median divided by its base median**. The base
is the first row of the row's own workload (always the obo row), except for the
`sum(range(n))` reference: that row computes the same sum as the explicit loop
by a different algorithm, so it is compared with the obo `loopsum` row of the
explicit-loop group. `1.00x` is the base, `20x` means twenty times slower than
the base, and `0.15x` means this row took 0.15 of the base's time (it is
faster).

Every obo result is checked against its Python equivalent before anything is
timed: outputs must agree byte for byte, and `wc` counts numerically; the sort
rows (including `sort -n` against GNU) and eza rows are also compared with
their GNU/reference output. The `sort -n` Python row is used only when
`sorted(lines, key=int)` is byte-identical on the fixture, and is skipped with
a note otherwise. A build failure, a command failure or a mismatch aborts the
run with exit status 2; obo rows are never silently skipped. A reference row
whose tool is absent (or whose eza does not match the pin) is skipped with a
note.

## How to run

From any current directory (paths resolve relative to the script):

```sh
python3 obo/bench/cli/run.py                  # default sizes, about 80 s here
python3 obo/bench/cli/run.py --quick          # smaller sizes, under ~20 s here
python3 obo/bench/cli/run.py --reps 7         # timed repetitions per row
python3 obo/bench/cli/run.py --keep           # keep the temp build/fixtures
python3 obo/bench/cli/run.py --json           # JSON to stdout, for archiving
python3 obo/bench/cli/run.py --eza /path/to/eza
```

The `--json` document is printed to stdout; no raw JSON capture is archived in
the repository.

Requirements:

* Linux with the repo's build prerequisites (`make`, `gcc`, `ld`, `objcopy`);
  `run.py` builds the compiler with `make -C obo obo` and builds `cat`, `wc`,
  `sort` and `eza` with `obo/tools/build_tools.py` into private temporary
  directories (`cat` only so the binary-size report covers every tools build).
* `python3` on `PATH` (the version and path are printed in the header).
* GNU coreutils for the `wc`, `sort` and `ls` reference rows; they are skipped
  with a note if missing or not GNU.
* The pinned eza reference is optional. `run.py` uses `eza` from `PATH` (or
  `--eza PATH`) only when its `--version` first three lines and binary sha256
  match `obo/tests/eza/VERSION`; otherwise that row is skipped with a note. The
  reference binary is never hardcoded or vendored.

The working directory for builds and fixtures is a private
`tempfile.mkdtemp(prefix='obo-bench-cli-')`; `--keep` prints its path and leaves
it in place. The only build output inside the repository is the compiler and
runtime binaries that `make -C obo obo` normally produces (gitignored).

## Sample output

One recorded run on the host named in the header, at `main` commit `57298f3`.
The pinned eza reference was absent from `PATH`, so the suite skipped that row
with a note (this run passed no `--eza` path):

```text
obo CLI benchmark suite (on-demand; NOT a gate, not run by CI)
repo:      /path/to/checkout
git:       main @ 57298f3
compiler:  obo 0.15 sym-format 8 abi 1
python:    Python 3.12.3  (/usr/bin/python3)
cpu:       AMD Ryzen 7 8745HS w/ Radeon 780M Graphics
kernel:    Linux-6.18.54.1-microsoft-standard-WSL2-x86_64-with-glibc2.39
pin:       CPU 15 (runner and children, single core)
mode:      default, 5 reps light / 3 heavy, startup 3x40 runs
fixtures:  text 110,000,000 B, sort 1,000,000 lines, eza 10,000 names
env:       LC_ALL=C LANG=C TZ=UTC; OBO_*/OBX_*/PYTHON*/POSIXLY_CORRECT unset; timed stdout/stderr to /dev/null

implementation                       median     ratio

process startup (hello; 40 runs per repetition)
  obo hello (static binary)           0.24 ms     1.00x
  python3 -c pass                     15.6 ms    66.34x
  python3 -c print("hi")              15.4 ms    65.51x

fib(32) recursive
  obo fib                             11.6 ms     1.00x
  python3 fib.py                     240.9 ms    20.82x

sum of integers below 100,000,000 (explicit loop)
  obo loopsum                         24.9 ms     1.00x
  python3 loopsum.py                 8,828 ms   353.84x

sum(range(100,000,000)) (reference; a different algorithm)
  python3 sum(range)                 1,087 ms    43.55x

sieve of Eratosthenes <= 10,000,000
  obo sieve                          174.1 ms     1.00x
  python3 sieve.py                   2,933 ms    16.85x

wc -l on 110,000,000 B
  obo wc -l                           90.0 ms     1.00x
  python3 wc_lines.py                 85.9 ms     0.95x
  GNU wc -l (ref)                     15.4 ms     0.17x

wc -lwc on 110,000,000 B
  obo wc -lwc                        146.0 ms     1.00x
  python3 wc_full.py                 595.5 ms     4.08x
  GNU wc -lwc (ref)                1,144 ms       7.84x

sort 1,000,000 lines
  obo sort                           319.3 ms     1.00x
  python3 sort.py                    848.8 ms     2.66x
  GNU sort (ref)                     352.1 ms     1.10x

sort -n 1,000,000 lines (numeric order)
  obo sort -n                        378.1 ms     1.00x
  GNU sort -n (ref)                  697.7 ms     1.85x
  python3 sorted(key=int) (ref)      629.7 ms     1.67x

eza 10,000 names
  obo eza                             35.2 ms     1.00x
  python3 listdir + sort              20.4 ms     0.58x
  GNU ls -1 (ref)                     8.43 ms     0.24x

binary sizes:
  hello                81,921 B
  fib                  81,921 B
  loopsum              81,921 B
  sieve                81,921 B
  cat (tool)           94,209 B
  wc (tool)            98,305 B
  sort (tool)         122,881 B
  eza (tool)          270,337 B

notes:
  - pinned eza: no eza on PATH (pass --eza PATH); its reference row is skipped

total: 100.3 s on this host; the numbers are host-dependent.
```

The obo-side binaries used for these medians were built from main `57298f3`:
cat 94,209, wc 98,305, sort 122,881, head 94,209, tail 106,497, eza 270,337
(sha256 `53bf3a06…`), and the four bench programs 81,921 each. The compiler is
the 0.15 fixed point (700,417 bytes, sha256 `8ca8e59e…`); stage 2 and stage 3
match, and `seed/obo` is the promoted 0.15 image (same size and hash). The 0.14
seed (655,361 bytes, `d7b0c755…`) built it once. The pinned eza reference
was absent, so its timing row was skipped rather than substituted. The earlier
0.5 / 0.6 sample is preserved in [`HISTORY.md`](../../../HISTORY.md).

`--quick` changes the workload sizes as well as the repetition counts, so its
absolute times are not comparable with default rows: `fib(30)`, a 10,000,000
loop, a sieve to 1,000,000, an 11,000,000-byte text file, 100,000 sort lines and
2,000 directory names.

## Methodology

* **Warm page cache.** Each command runs untimed warmups before its timed
  repetitions: one run normally and five processes for the startup row.
  Fixtures are written just before the run, so on a host with spare RAM the
  110 MB text file and the sort input are in page cache. This is not a
  cold-I/O benchmark.
* **Output to `/dev/null`.** Timed runs use `/dev/null` for stdout and stderr,
  so terminal or pipe costs are not measured; a failed timed run is rerun with
  pipes to report its stderr. Correctness runs capture the outputs they check.
* **Median.** Default: 5 repetitions for light rows and 3 for heavy rows, and
  the median of those repetitions is reported. The startup row is a median of 3
  batches of 40 processes each, timed as one wall-clock interval per batch and
  divided by the batch size; `--reps N` sets the counts.
* **Single core.** When `os.sched_setaffinity` is available the runner pins
  itself (and so every child) to one CPU and prints the number. Pinning the
  runner rather than wrapping each command in `taskset` keeps the wrapper's
  exec time out of the 0.2 ms startup row. If pinning is unavailable the header
  says so.
* **Locale and environment.** `LC_ALL=C`, `LANG=C`, `TZ=UTC`; stdout is not a
  TTY; the GNU `ls` and pinned eza reference rows pass `--color=never`
  explicitly. The child environment is the caller's with `OBO_*`, `OBX_*`,
  `PYTHON*` and `POSIXLY_CORRECT` removed, so `OBO_CHECK`, `OBO_STATS`,
  `OBX_GC`, `PYTHONPATH` and similar settings cannot change what is measured.
* **No-`subprocess.run(timeout=)` in timed runs.** Its polling adds about a
  millisecond, which would swamp the startup row; timed runs use a blocking
  `waitpid` with a `SIGALRM` deadline instead. Correctness runs use ordinary
  subprocess timeouts.
* **Builds.** The benchmarks compile through the repo compiler with its own
  `--rt`; `cat`, `wc`, `sort` and `eza` are built by `tools/build_tools.py`
  (the same module-root rule as `make tools`/`make eza`) into a private output
  directory with a private symbol directory.

## Limitations

* **Host-dependent.** CPU model, governor, WSL, background load and thermal
  state all move the numbers, especially the sub-millisecond startup row.
  Compare rows within one printed table, not across runs or machines.
* **Warm cache, one core, no I/O concurrency.** These are single-file,
  single-process measurements with warm page cache; they say nothing about
  cold-cache I/O, parallel throughput or memory use. Peak memory is the subject
  of `obo/bench/bench.py` and the `--memory` modes of `obo/tests/tools.py` and
  `obo/tests/eza.py`.
* **Idiomatic Python, not matching internals.** The Python equivalents do the
  same work but lean on C: `bytes.count`, `bytes.split`, `list.sort`,
  `sorted(os.listdir)`. That is intentional — these are tool-versus-tool
  comparisons — but a ratio is not a pure language/codegen comparison. In the
  other direction, obo's `wc` scans only the requested columns after wc-fast,
  so its `wc -l` is at parity with Python's C `bytes.count` (and still behind
  GNU's optimized `-l`), while its `wc -lwc` beats both Python and GNU's word
  pass on this fixture. obo's `sort` (a stable MSD radix sort over one input
  buffer, with ordered-input detection) leads both Python's C `list.sort` and
  GNU `sort` on that fixture, and its numeric radix `sort -n` leads GNU
  `sort -n` and the plain-integer Python reference on the numeric fixture.
* **`sort -n` numeric row.** It uses the same 1,000,000 fixed-width,
  zero-padded 8-digit unsigned integers as the default sort row, so GNU's
  strnumcmp semantics reduce here to plain integer order (no signs, blanks,
  fractions or overflow). Fixed width makes numeric order and byte order
  coincide, so both sort rows produce the same output bytes; the numeric row
  isolates the numeric key path, not a different result. `sort_n.py` is a
  plain-integer reference (`sorted(key=int)`), kept only when byte-identical
  and skipped with a note otherwise. obo's `sort -n` uses a stable numeric
  radix sort and beats GNU `sort -n` on this fixture (378.1 ms against
  697.7 ms in the recorded run); that is one fixture, not a general claim.
* **`sum(range(n))` is a reference, not an equivalent.** It is Python's
  C-level builtin and is labeled as a different algorithm; it is in the table to
  show what the same arithmetic costs without a Python-level loop.
* **`wc` word rule.** GNU's C-locale rule counts printable `0x21..0x7E` bytes as
  word bytes and ignores NUL/controls/high bytes; `wc_full.py` treats every
  non-whitespace byte as part of a word. The generated fixture is plain ASCII,
  where the two agree, and the run compares the counts (it would fail on a
  fixture where they diverge).
* **References are optional.** A missing GNU tool or a mismatched eza is a
  skipped reference row with a note, never a silent pass; obo rows are never
  skipped.
* **Binary sizes** are the sizes of the compiled static obo programs and tools
  in that run; each obo executable links the runtime text/data, so the four
  small programs are the same size.

## Files

| File | Purpose |
|---|---|
| `run.py` | the runner: builds, fixtures, checks, timing, table/JSON |
| `hello.obx` | startup row only |
| `fib.obx`, `fib.py` | recursive `fib(n)` |
| `loopsum.obx`, `loopsum.py` | explicit loop sum `0..n-1` |
| `sumrange.py` | reference: Python `sum(range(n))` |
| `sieve.obx`, `sieve.py` | sieve of Eratosthenes |
| `wc_lines.py`, `wc_full.py` | Python `wc -l` and `wc -lwc` equivalents |
| `sort.py` | Python byte-order sort equivalent |
| `sort_n.py` | Python plain-integer numeric sort reference (`sorted(key=int)`) |
| `listdir.py` | Python `listdir + sorted` equivalent |

## Not part of the gates

The repo's case discovery globs only the top level: `obo/tests/port_compare.py`
and `obo/tests/port_test.py` use `obo/bench/*.obx`, which does not recurse into
`obo/bench/cli/`. These programs are therefore not part of the port gates, and
`obo/tests/doc_check.py` does not read this directory. CI
(`.github/workflows/ci.yml`) does not invoke `obo/bench/` at all.
