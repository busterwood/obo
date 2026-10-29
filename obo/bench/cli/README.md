# obo CLI benchmarks (on-demand)

This directory compares obo programs and the repo's command-line tools with
equivalent Python 3 programs, and with GNU tools and the pinned eza where a
reference is useful. It is a manual tool for occasional comparisons on one
host.

**It is not a gate.** No script under `obo/tests/` and no CI job runs it. The
numbers are host-dependent; treat them as relative measurements from one run,
not as portable facts.

## What it measures

| Workload | obo side | Python side | Reference rows |
|---|---|---|---|
| process startup | `hello.obx`, a static binary printing one line | `python3 -c pass`, `python3 -c print("hi")` | — |
| `fib(32)` | `fib.obx`, recursive | `fib.py`, the same call tree | — |
| sum `0..100,000,000` | `loopsum.obx`, explicit `while` loop | `loopsum.py`, the same loop | `sumrange.py`: Python's C-level `sum(range(n))`, a different algorithm |
| sieve of Eratosthenes `<= 10,000,000` | `sieve.obx` | `sieve.py`, the same bytearray sieve | — |
| `wc -l` on a 110,000,000-byte file | repo `wc -l` | `wc_lines.py`, chunked newline count | GNU `wc -l` |
| `wc -lwc` on the same file | repo `wc -lwc` | `wc_full.py`, chunked line/word/byte count | GNU `wc -lwc` |
| sort 1,000,000 lines | repo `sort` | `sort.py`, `bytes.split(b'\n')` then `list.sort` | GNU `sort` |
| eza 10,000 names | repo `eza`, name-only non-TTY | `listdir.py`, `sorted(os.listdir(...))` | GNU `ls -1`; the pinned eza when available |

The ratio column is **this row's median divided by the obo row's median** in the
same workload. `1.00x` is the obo baseline, `20x` means twenty times slower than
obo, and `0.15x` means this row took 0.15 of obo's time (it is faster).

Every obo result is checked against its Python equivalent before anything is
timed: outputs must agree byte for byte, and `wc` counts numerically; the sort
and eza rows are also compared with their GNU/reference output. A build failure,
a command failure or a mismatch aborts the run with exit status 2; obo rows are
never silently skipped. A reference row whose tool is absent (or whose eza does
not match the pin) is skipped with a note.

## How to run

From any current directory (paths resolve relative to the script):

```sh
python3 obo/bench/cli/run.py                  # default sizes, about 90 s here
python3 obo/bench/cli/run.py --quick          # smaller sizes, under ~20 s here
python3 obo/bench/cli/run.py --reps 7         # timed repetitions per row
python3 obo/bench/cli/run.py --keep           # keep the temp build/fixtures
python3 obo/bench/cli/run.py --json           # a JSON document, for archiving
python3 obo/bench/cli/run.py --eza /path/to/eza
```

Requirements:

* Linux with the repo's build prerequisites (`make`, `gcc`, `ld`, `objcopy`);
  `run.py` builds the compiler with `make -C obo obo` and builds `wc`, `sort`
  and `eza` with `obo/tools/build_tools.py` into private temporary directories.
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

One recorded run on the host named in the header. On that host the pinned eza
binary happened to be at `/tmp/eza-tar-test2/eza` and was passed with `--eza`;
nothing in the suite knows that path:

```text
obo CLI benchmark suite (on-demand; NOT a gate, not run by CI)
repo:      /home/chris/obx/.claude/worktrees/bench-cli
git:       bench-cli @ cc6c420
compiler:  obo 0.5 sym-format 1 abi 1
python:    Python 3.12.3  (/usr/bin/python3)
cpu:       AMD Ryzen 7 8745HS w/ Radeon 780M Graphics
kernel:    Linux-6.18.54.1-microsoft-standard-WSL2-x86_64-with-glibc2.39
pin:       CPU 15 (runner and children, single core)
mode:      default, 5 reps light / 3 heavy, startup 3x40 runs
fixtures:  text 110,000,000 B, sort 1,000,000 lines, eza 10,000 names
env:       LC_ALL=C TZ=UTC; stdout to /dev/null; stderr checked

implementation                   median     ratio

process startup (hello; 40 runs per repetition)
  obo hello (static binary)       0.22 ms     1.00x
  python3 -c pass                 12.9 ms    58.84x
  python3 -c print("hi")          12.7 ms    57.90x

fib(32) recursive
  obo fib                         10.6 ms     1.00x
  python3 fib.py                 214.6 ms    20.26x

sum 0..100,000,000 explicit loop
  obo loopsum                     22.8 ms     1.00x
  python3 loopsum.py             8,216 ms   360.98x

sum(range(100,000,000)) (reference; a different algorithm)
  python3 sum(range)             906.6 ms    39.83x

sieve of Eratosthenes <= 10,000,000
  obo sieve                      136.9 ms     1.00x
  python3 sieve.py               2,352 ms    17.18x

wc -l on 110,000,000 B
  obo wc -l                      490.2 ms     1.00x
  python3 wc_lines.py             71.2 ms     0.15x
  GNU wc -l (ref)                 11.4 ms     0.02x

wc -lwc on 110,000,000 B
  obo wc -lwc                    485.3 ms     1.00x
  python3 wc_full.py             477.5 ms     0.98x
  GNU wc -lwc (ref)              1,015 ms     2.09x

sort 1,000,000 lines
  obo sort                       2,075 ms     1.00x
  python3 sort.py                631.3 ms     0.30x
  GNU sort (ref)                 297.0 ms     0.14x

eza 10,000 names
  obo eza                         24.9 ms     1.00x
  python3 listdir + sort          22.2 ms     0.89x
  GNU ls -1 (ref)                 7.41 ms     0.30x
  pinned eza v0.23.3 (ref)        63.5 ms     2.55x

binary sizes:
  hello                28,673 B
  fib                  28,673 B
  loopsum              28,673 B
  sieve                28,673 B
  wc (tool)            53,249 B
  sort (tool)          53,249 B
  eza (tool)          126,977 B

total: 93.4 s on this host; the numbers are host-dependent.
```

`--quick` changes the workload sizes as well as the repetition counts, so its
absolute times are not comparable with default rows: `fib(30)`, a 10,000,000
loop, a sieve to 1,000,000, an 11,000,000-byte text file, 100,000 sort lines and
2,000 directory names.

## Methodology

* **Warm page cache.** Each command runs once untimed as a warmup before its
  timed repetitions; fixtures are written just before the run, so on a host
  with spare RAM the 110 MB text file and the sort input are in page cache.
  This is not a cold-I/O benchmark.
* **Output to `/dev/null`.** Timed runs use `/dev/null` for stdout and stderr,
  so terminal or pipe costs are not measured; correctness runs capture output.
* **Median.** Default: 5 repetitions for light rows and 3 for heavy rows, and
  the median of those repetitions is reported. The startup row is a median of 3
  batches of 40 processes each, timed as one wall-clock interval per batch and
  divided by the batch size; `--reps N` sets the counts.
* **Single core.** When `os.sched_setaffinity` is available the runner pins
  itself (and so every child) to one CPU and prints the number. Pinning the
  runner rather than wrapping each command in `taskset` keeps the wrapper's
  exec time out of the 0.2 ms startup row. If pinning is unavailable the header
  says so.
* **Locale and terminal.** `LC_ALL=C`, `TZ=UTC`, stdout is not a TTY; the GNU
  `ls` and pinned eza reference rows pass `--color=never` explicitly.
* **No-`subprocess.run(timeout=)` in timed runs.** Its polling adds about a
  millisecond, which would swamp the startup row; timed runs use a blocking
  `waitpid` with a `SIGALRM` deadline instead. Correctness runs use ordinary
  subprocess timeouts.
* **Builds.** The benchmarks compile through the repo compiler with its own
  `--rt`; `wc`, `sort` and `eza` are built by `tools/build_tools.py` (the same
  module-root rule as `make tools`/`make eza`) into a private output directory
  with a private symbol directory.

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
  other direction, obo's `wc` scans byte by byte and obo's `sort` is a merge
  sort, so its `wc -l` loses to Python's C `bytes.count` while its `wc -lwc`
  beats GNU's word pass on this fixture.
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
| `listdir.py` | Python `listdir + sorted` equivalent |

## Not part of the gates

The repo's case discovery globs only the top level: `obo/tests/port_compare.py`
and `obo/tests/port_test.py` use `obo/bench/*.obx`, which does not recurse into
`obo/bench/cli/`. Verified in the `bench-cli` worktree with the new files
present:

* `python3 obo/tests/port_compare.py` — `stage B: 1182 of 1182 behave the same`
  (exit 0), and `cases_of()` lists none of the files in this directory.
* `python3 obo/tests/doc_check.py` — `48 examples, 0 problems` (this directory
  adds no `obo/LANGUAGE.md` examples; run for completeness).

CI (`.github/workflows/ci.yml`) does not invoke `obo/bench/` at all.
