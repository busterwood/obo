# obx performance

obx builds a program in two ways:

- **native**: `obxc --native`, the compiler's own x86-64 backend. It writes an
  ELF executable directly, with no gcc and, unless the program calls C
  libraries, no libc.
- **via C**: `obxc`, which writes C and runs gcc (`-O2 -march=x86-64-v3
  -mmove-max=128 -mstore-max=128 -ffp-contract=off -falign-functions=64
  -falign-loops=32`).

Both are built from the same IR and must behave identically; the gate checks
that on every test program. This page is the current report: every figure
was measured on 6 November 2026 on `main` after `38e114e` (Decimal, maps,
code placement and the gcc move-width flags merged), on a quiet machine. How
the numbers got here, the native backend's passes, the earlier figures and
the obo record, is in [HISTORY.md](HISTORY.md), "The performance record".
The numbers are host-dependent: use them to compare the columns, not as
portable constants.

## Host and method

- AMD Ryzen 7 8745HS (8 cores, 16 threads; CPUs 2k and 2k+1 are the two
  hardware threads of core k, and all cores share one L3), WSL2, Linux 6.18,
  gcc 13.3, glibc 2.39. Programs require an x86-64-v3 CPU (SPEC, exit code
  16).
- Wall clock, best of N runs (11 for the benchmarks and the compiler, 9 for
  eza, 5 or 3 where a table says so), warm cache, output to `/dev/null` or a
  pipe. Cycle and instruction counts are `perf stat`, user mode, least of N,
  pinned to one CPU. Timings vary by 5–10% between runs on this machine;
  below about 50 ms, process start-up is part of the figure.
- A server and its load client, or two measurements at once, never share a
  core: the HTTP and TLS benchmarks put the server on CPU 0 and the client
  on CPU 2.
- This machine's memory-bound figures move with its state from day to day:
  the plain C sieve, whose code does not change, took 125 ms on 5 November
  and 142 ms on 6 November. Compare columns within this page, not with the
  figures in HISTORY.md.
- Every row checks its outputs first: both builds print the same result and
  are leak free under `OBX_CHECK=1`, and the tools' outputs equal the
  reference tool's.

## Benchmarks

`python3 obx/bench/bench.py -n 11` ([obx/bench/](obx/bench/README.md)):

| benchmark | via C | native | native/C | what it does |
|---|---:|---:|---:|---|
| fib | 327 ms | 363 ms | 1.11 | recursive calls |
| loop | 270 ms | 215 ms | 0.80 | two overflow-checked operations per iteration |
| array | 102 ms | 90 ms | 0.88 | checked indexing |
| alloc | 119 ms | 61 ms | 0.51 | 20 million records allocated and freed |
| lists | 37 ms | 29 ms | 0.77 | linked list build, sum, drop |
| strings | 79 ms | 42 ms | 0.53 | concatenation and `toStr` |
| trees | 24 ms | 19 ms | 0.80 | binary trees |
| tokens | 18 ms | 12 ms | 0.65 | byte scanning and hashing |
| sieve | 187 ms | 133 ms | 0.71 | the sieve of Eratosthenes to 10 million, ten rounds |
| maps | 183 ms | 185 ms | 1.01 | `Map[str, int]` with short and long keys, `Map[int, int]` (keyed hashing) |

- **Native is ahead on allocation and strings** (alloc, lists, strings,
  tokens, trees). Small functions and glue are inlined before code
  generation; the freestanding runtime has a size-class allocator with no
  locks or libc bookkeeping and an 8-byte block header; the string and index
  checks are generated inline.
- **Tight loops are native's too** (loop, array). The native backend tests at
  the bottom of loops and aligns their bodies, reads array headers once per
  loop, and drops the bounds and overflow checks that conditions, passed
  checks and induction variables prove cannot fail
  ([design/native.md](obx/design/native.md) sections 4 and 8). gcc does more
  (it unrolls, schedules and strength-reduces); obx's checks stay where they
  can fail, so array keeps one bounds check per element (its bound is
  unrelated to the array's length).
- **The sieve is at plain C's speed.** A hand-written C version with no
  checks (`obx/bench/c/sieve.c`, `gcc -O2`, bench.py's "plain C" column)
  took 142 ms in the same run. Native obx runs as many instructions as it
  (1,414M) in as many cycles (about 600M each); through C obx keeps the
  overflow and bounds checks the native backend proves away.
- **fib** is calls and returns: native runs 4,305M instructions in 1,690M
  cycles wherever its code falls; gcc's build takes about 1,520M.
- **maps** is level. `str`, `i128`, `u128` and `Decimal` hash with
  SipHash-1-3 under a per-process key, and `Map` mixes a per-process seed
  into its slots (lib/README.md, "Keyed hashing"). Map's index slots are
  `u32`: an entry number with the high bits of the mixed hash beside it, so
  a probe skips other keys' slots without loading their entries; a Map holds
  at most 2^31 entries. The probes wait on memory: what is left is the
  misses on the keys' strings and the entries.
- **Code placement.** The same instructions can take different cycles
  depending on where they fall relative to 32- and 64-byte boundaries. Native
  functions start at multiples of 64 and the native backend aligns loop
  heads; gcc gets `-falign-functions=64 -falign-loops=32`, in programs and in
  the native runtime. `python3 obx/tools/placement.py` builds a benchmark
  eight times with its code shifted by 0, 8, ..., 56 bytes (native:
  `OBX_PADB=n`; C: `.nops n` after each function's label in gcc's assembly)
  and reports the spread. Native fib, loop, array and alloc move by under
  1%, trees and sieve by 2–4%; C loop, alloc and array under 0.5%, sieve 2%.
  Left: C fib moves by 22% when the code inside the function moves (its
  jumps are not aligned; gcc's `-falign-jumps` would put nops on the hot
  path), and maps and C trees vary as much between runs of one binary on a
  loaded machine, so that is memory, not placement.

## Regex

`python3 obx/bench/regex/bench.py -n 7` ([obx/bench/regex/](obx/bench/regex/bench.py)),
the benchmark set of [design/regex.md](obx/design/regex.md) section 4. The
corpus is 3.2 MB written by `gen_corpus.py` from a fixed seed (prose with the
names the patterns look for, a log, C-like source; ASCII). Each driver times
its own searches after reading the file: the best of 5 searches, best of 7
runs, in ms. "Matches" is the same for every driver (the script checks);
`(a*)*b` runs over 100,000 `a`s. Go is go1.26.4's `regexp`, Rust the `regex`
crate 1.12.3, and rg is ripgrep 14.1.1 counting matches as a whole process,
start-up and file reading included: a reference point, not a like-for-like
figure.

| pattern | matches | obx native | obx via C | Go | Rust | rg |
|---|---:|---:|---:|---:|---:|---:|
| `Sherlock Holmes` | 926 | 0.19 | 0.22 | 0.48 | 0.08 | 1.8 |
| `Sherlock\|Holmes\|Watson\|Irene` | 7,350 | 1.13 | 0.85 | 133.12 | 0.42 | 3.0 |
| `\w+` (all matches) | 669,532 | 60.65 | 36.11 | 140.25 | 17.10 | 41.5 |
| `(?i)sherlock` | 4,280 | 0.94 | 0.73 | 69.09 | 0.46 | 3.5 |
| `[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}` | 848 | 0.19 | 0.11 | 76.83 | 0.09 | 2.0 |
| `(?m)^ERROR .*timeout$` | 197 | 0.32 | 0.40 | 30.76 | 0.59 | 2.5 |
| `(?P<y>\d{4})-(?P<m>\d{2})-(?P<d>\d{2})` (captures) | 10,755 | 4.03 | 3.15 | 71.45 | 1.28 | 8.5 |
| `(a*)*b` over 100,000 `a`s | 0 | 0.00 | 0.00 | 3.30 | 0.00 | 1.6 |

- **obx and Rust.** The engine has RE2's lazy DFA and Rust's literal
  searches (design/regex.md, "R2 as built"). Through C, obx takes 1.2 times
  Rust's time on the email pattern, 1.6 on `(?i)`, 2.0 on the alternation,
  2.1 on `\w+`, 2.5 on the captures, 2.8 on the literal, and two thirds of
  Rust's time on the `ERROR` lines (a prefilter on `ERROR ` and the suffix
  check on `timeout`); natively 2.0 to 3.5 times Rust's time, and about half
  on the `ERROR` lines.
- **Where the gap is.** The literal: Rust's memchr uses AVX2 (32 bytes a
  step), the runtime SSE2 (16). `\w+` and the captures are per-match costs
  (670,000 and 10,755 matches): each match is a forward and a reverse DFA
  run plus, for captures, the backtracker on the span; Rust's inner loops are
  tighter (unchecked indexing, a byte DFA with no rune decode). The native
  build loses most on these DFA loops (1.7 times its C build on `\w+`): the
  native backend's bounds and overflow checks in the inner loop, not the
  library.
- **`(a*)*b`** takes no time: the text has no `b`, and every match ends with
  one (the suffix check), so the search stops before any matcher runs. The
  DFA on such patterns is covered by the tests (`regex_linear`,
  `regex_dfa`), not by this row.
- **obx and Go.** obx is 2 to 700 times faster than Go's `regexp`, which has
  no DFA.
- **Go's matchers** (the Pike VM, the backtracker and one-pass), which the
  engine uses for short texts (under 128 bytes), for one-pass patterns, and
  when the DFA's cache fills too often, run 1.4 to 1.8 times as fast through
  gcc as natively: short loops over the fields of a `mut &` record's arrays,
  each access with its bounds check and each index sum with its overflow
  check, which gcc drops more of.
- **Linear time.** The library test `regex_linear` checks that 100,000 bytes
  take about ten times as long as 10,000 for six such patterns under the
  Pike VM, the backtracker and the DFA (also in a tiny cache), among them
  `(a|b)*a(a|b){20}`, whose DFA has 2^21 states; `regex_dfa` does the same
  on random text, where the cache fills and the search falls back to the
  Pike VM.

## eza

[obx/cmd/eza.obx](obx/cmd/eza.obx), a 1,600-line implementation of the
common subset of eza 0.23.3. `LC_ALL=C TZ=UTC`; the long listings use
`--bytes --time-style=long-iso -n`, which this eza requires. Best of 9; both
builds write byte-identical listings.

| listing | output | via C | native |
|---|---:|---:|---:|
| `-T /usr/share` (tree) | 2.1 MB | 105 ms | 106 ms |
| `-T -l /usr/share` (long tree) | 3.7 MB | 196 ms | 198 ms |
| `-l /usr/lib/x86_64-linux-gnu` | 51 KB | 4 ms | 4 ms |

The golden suite (`python3 obx/cmd/eza_gate.py -j 12`) passes on both
builds.

## grep

[obx/cmd/grep.obx](obx/cmd/grep.obx) against GNU grep 3.11 on 100 MB of
generated text (1.8 million lines; the generator is in
[obx/cmd/README.md](obx/cmd/README.md)). Best of 5, output to a pipe,
`LC_ALL=C` (under `C.UTF-8` GNU grep's `-w` takes 2.3 times as long; the obx
port has no locale). Every output equals GNU grep's.

| search | GNU | native | via C |
|---|---:|---:|---:|
| `needle5007` (a literal) | 28 ms | 17 ms | 17 ms |
| `-c omega` | 86 ms | 59 ms | 57 ms |
| `-c '[0-9]*7 alpha'` | 71 ms | 17 ms | 18 ms |
| `-c -w eta` | 208 ms | 187 ms | 170 ms |
| `-c -v '^0'` (every line) | 50 ms | 47 ms | 41 ms |
| `-c -E 'a.*z.*e'` | 217 ms | 101 ms | 94 ms |
| `-n omega` | 261 ms | 178 ms | 157 ms |
| `-o -w eta` | 390 ms | 336 ms | 294 ms |

What makes it fast:

- **Lines, not matches.** The regex library's `matchingLines` runs the lazy
  DFA over a chunk and reports the lines with a match. It starts again after
  each such line, with no call per line.
- **A required literal first.** `[0-9]*7 alpha` must contain `7 alpha`; a
  memory search finds it and only its line is checked.
- **`-w` as one regex.** The word with a non-word character or a line end on
  each side.
- **Output in pieces.** The prefix, the line and the newline go into a byte
  buffer; no string is built per line.

### On 1 GB

The same generator run to 10^9 bytes (18.2 million lines), the cases above
and more pattern shapes. Best of 3, output piped to `md5sum`, the file in the
page cache, `LC_ALL=C`. ripgrep (14.1.1, one file; `-E` dropped, since rg's
regexes are extended already) is for reference; it has no `-x` and its `-f`
was not measured. On every case the native and C builds printed exactly what
GNU grep printed, and so did ripgrep.

| search | GNU | ripgrep | native | via C |
|---|---:|---:|---:|---:|
| `needle5007` (a literal, one line) | 283 ms | 85 ms | 186 ms | 187 ms |
| `-c zzz` (no match) | 353 ms | 76 ms | 183 ms | 176 ms |
| `-c omega` | 873 ms | 390 ms | 614 ms | 614 ms |
| `-v -c omega` | 994 ms | 581 ms | 741 ms | 754 ms |
| `-c '[0-9]*7 alpha'` | 725 ms | 96 ms | 192 ms | 180 ms |
| `-c -E '(alpha\|beta) gamma'` | 1,096 ms | 411 ms | 645 ms | 614 ms |
| `-c -i NEEDLE` | 625 ms | 117 ms | 185 ms | 168 ms |
| `-c -i 'OMEGA BETA'` | 714 ms | 455 ms | 227 ms | 205 ms |
| `-c -w eta` | 2,081 ms | 1,689 ms | 1,901 ms | 1,742 ms |
| `-c -v '^0'` (every line) | 571 ms | 644 ms | 457 ms | 435 ms |
| `-c -E 'a.*z.*e'` | 2,112 ms | 1,702 ms | 1,055 ms | 948 ms |
| `-c -E 'needle(1\|2)[0-9]{3}7'` | 572 ms | 78 ms | 193 ms | 178 ms |
| `-c -E '^[0-9]+7[0-9]'` | 456 ms | 814 ms | 582 ms | 550 ms |
| `-c -E '(alpha\|omega) .*(beta\|psi)$'` | 2,554 ms | 941 ms | 1,233 ms | 1,103 ms |
| `-c -x '[0-9]* alpha beta gamma delta'` | 411 ms | — | 186 ms | 196 ms |
| `-c -F -f` five fixed strings | 1,295 ms | — | 1,664 ms | 1,613 ms |
| `-n omega` | 2,201 ms | 1,435 ms | 2,059 ms | 1,834 ms |
| `omega` (prints 8.8 million lines) | 1,623 ms | 927 ms | 1,557 ms | 1,385 ms |
| `-o 'needle[0-9]*'` | 605 ms | 103 ms | 196 ms | 186 ms |
| `-o -w eta` | 3,845 ms | 2,946 ms | 3,412 ms | 2,936 ms |
| `-n -w eta` | 3,885 ms | 3,269 ms | 3,327 ms | 3,112 ms |

The times scale with the file: each is about ten times its 100 MB figure, so
nothing in the obx port grows worse than linearly. The literal searches
(`needle`, `zzz`, `-o 'needle'`) read the 1 GB at memory speed, so they move
most with the machine's state, for every tool alike.

**Against GNU grep:** obx is ahead on 19 of 21 cases, often by 2 to 4 times.

**Still behind GNU:**
- `-F -f` with several fixed strings, 1.25 to 1.3 times slower. Each string
  becomes a branch of one regex, where GNU and ripgrep use a multi-string
  search (Aho-Corasick, Teddy).
- `^[0-9]+7[0-9]`, 1.2 to 1.3 times slower.

**Against ripgrep:** ripgrep is faster wherever a literal or a rare required
string can be found with SIMD (`needle`, `zzz`, `-x`, `-o`). obx is faster on
case-insensitive patterns (`-i 'OMEGA BETA'`), on `a.*z.*e` and
`^[0-9]+7[0-9]`, and when every line matches (`-v '^0'`). The two are close
on `-w`.

## jq

[obx/cmd/jq.obx](obx/cmd/jq.obx), the jq 1.7.1 port, against jq 1.7.1 built
from its release tarball (`./configure --with-oniguruma=builtin`, the default
`-O2`). Wall times; figures vary by 5–10% between runs.

**Large input.** `python3 obx/bench/json/bench.py --jq JQ`, on an 80 MB file
of 182,290 generated records. Best of 3; the output goes to a file, and the
three outputs of each filter must be identical.

| filter | jq 1.7.1 | native | via C |
|---|---:|---:|---:|
| `.` | 2.50 s | 1.69 s | 1.65 s |
| `-c .` | 2.24 s | 1.69 s | 1.79 s |
| `length` | 1.59 s | 1.25 s | 1.13 s |
| `[.[].items[].qty] \| add` | 1.90 s | 1.27 s | 1.19 s |
| `map(select(.active and .score > 0) \| {id, city: .address.city})` | 1.75 s | 1.38 s | 1.40 s |
| `group_by(.address.city) \| map({city: .[0].address.city, n: length})` | 2.15 s | 2.09 s | 1.82 s |

The same script times the JSON library on the file (ms, best of 3), against
Python's `json`:

| step | obx native | obx via C | Python |
|---|---:|---:|---:|
| parse | 794 | 589 | 913 |
| compact output | 552 | 268 | 794 |
| pretty output | 599 | 401 | 2,962 |
| query | 218 | 110 | 45 |
| visit | 218 | 95 | 45 |

**Computation.** Programs with no input (`jq -n`), best of 5 (`group_by`
best of 9); the outputs are identical.

| program | jq 1.7.1 | native | via C |
|---|---:|---:|---:|
| `1` (start-up) | 2 ms | 5 ms | 3 ms |
| `reduce range(3000000) as $i (0; . + $i)` | 503 ms | 540 ms | 364 ms |
| `[range(1000000)] \| map(. * 2) \| add` | 417 ms | 281 ms | 236 ms |
| `[range(200000) \| tostring] \| sort \| length` | 123 ms | 91 ms | 67 ms |
| `def fib: ...; 25 \| fib` (recursive, 250,000 calls) | 61 ms | 111 ms | 94 ms |
| `[limit(100000; repeat(1))] \| length` | 46 ms | 11 ms | 11 ms |
| `[range(100000) \| {a: ., b: "x\(.)"}] \| group_by(.a % 10) \| map(length)` | 251 ms | 285 ms | 252 ms |
| `[range(50000) \| tostring \| test("7$")] \| map(select(.)) \| length` | 74 ms | 79 ms | 61 ms |

**Where the port wins.** Wherever JSON is parsed or written: jq builds a
decNumber for every literal, and its writer goes through `jv_dump_term`; the
JSON library keeps the literal's text and writes it back. Also on simple
generators: `limit`, `range` and `map` are native in the port, and
continuation calls cost less than jq's bytecode with its fork points.
Compiled regexes are cached for the run.

**Where jq wins:**

- **Calls of jq-defined functions.** `fib` is 1.5–2× slower: each call pushes
  frames and closures, and runs its body through a capturing continuation.
- **Start-up.** The port parses and resolves `builtin.jq` on every run
  (2 ms).
- **Sorting objects by key.** `sort_by` and `group_by` on objects are a
  little slower: the comparison walks the counted boxes.
- **Queries over a parsed document**, in the library table: Python's dicts
  are faster to walk than the library's counted boxes.

The library's own design and numbers are in
[obx/design/json.md](obx/design/json.md), section 9.

## Markdown

[obx/cmd/cmark.obx](obx/cmd/cmark.obx) (the `markdown` library) against cmark
0.31.2 (`-O3` release build, cmake), rendering 10 MB documents with
`--unsafe` to a pipe. Best of 3, seconds (`python3 obx/tests/markdown.py
--perf --ref CMARK`):

| document | cmark | obx native | obx via C |
|---|---:|---:|---:|
| the spec, repeated to 10 MB | 0.18 | 0.21 | 0.18 |
| the repo's Markdown files, repeated to 10 MB | 0.22 | 0.23 | 0.20 |
| the spec's examples, repeated to 10 MB | 0.62 | 0.78 | 0.72 |

The obx renderer takes 0.9 to 1.25 times cmark's time. cmark's pathological
inputs (nested emphasis, brackets and block quotes 30,000 to 100,000 deep, a
million backticks, unclosed constructs) all finish in well under a second,
within a small factor of cmark; the one quadratic case (a run of blank lines
inside deeply nested lists) is cut by the same early exit as cmark's. The
parser keeps the tree in one array of 200-byte nodes: the 10 MB spec takes
114 MB of memory at its peak (cmark: 68 MB).

## The compiler

The obx compiler compiling itself (`obx/compiler/main.obx`), built each way,
best of 11, interleaved:

| | gcc-built | native-built |
|---|---:|---:|
| self-build to an executable (`--native`) | 589 ms | 635 ms |
| self-build to C (`--emit-c`) | 378 ms | 352 ms |
| full build through gcc (`build.sh`, from the seed) | 25 s, nearly all of it gcc | — |
| compiler binary | 2,753,712 bytes (dynamic, glibc) | 2,978,481 bytes (static, no libc) |

The compiler is 36,600 lines of obx (1.66 MB, 409 KB of it the generated
runtime tables `rt.obx` and `rtx.obx`) and 5,400 lines of C runtime (the C
backend's `rt.h` and the native backend's freestanding `rt.c`). The gcc-built
`--native` self-build runs 4,288M instructions in 2,331M cycles; the
native-built one 4,900M (1.14 times) in 2,501M.

- The native-built compiler is about 1.08 times slower than the gcc-built
  one on `--native` and slightly faster on `--emit-c`. What is left is spread
  thin: it is in the mix (more register-to-register moves, stores to the
  stack and compares than gcc's: spills, and loads and bounds checks repeated
  from one statement to the next), not in a few functions. A compiler is
  call-heavy and branchy, which is gcc's strength; inlining in the IR closed
  much of the gap.
- Both backends start from one IR: `--native` does not print and re-read C,
  and `--emit-c` builds the IR before printing it.

### Judging a change

A backend pass is judged by `obx/tools/perfsuite.py`, not by the self-build
alone: each pass adds to the compiler's own sources, and the compiler is one
program.

    python3 obx/tools/perfsuite.py main BRANCH     # or two compiler paths

It builds both revisions' native compilers from their seeds, then measures
them on inputs frozen at one revision (`7d519d1`; moved only on purpose,
with a note here):

- **Compile suite:** the frozen compiler (`--native` and `--emit-c`) and the
  frozen eza, grep and zoxide, compiled by each compiler. This is the compile
  speed a user sees: the new analysis's cost minus what the compiler gains
  from being compiled better itself.
- **Run suite:** the frozen bench programs and grep, sort, wc and eza, built
  by each compiler and run on fixed inputs (a generated 100 MB text). Both
  builds must print the same output.

Each suite is summarised as the geometric mean of new/base. On two builds of
the same code, instructions are identical and cycles vary by up to ±3% on one
program and ±0.5% on the means, so instructions decide and cycles must agree
in sign. A pass is kept when:

- the run suite gains at least 1% (geometric mean), or 5% on at least two
  programs with none worse than 1%, and the compile suite costs at most 1%;
  or
- the compile suite gains and the run suite is not worse.

The compile suite's total cost over `7d519d1` stays under 5%
(`perfsuite.py 7d519d1 main`). Because the inputs are frozen, a change to
the library (std's Map, say) shows in perfsuite only through the compiler,
not in the frozen programs; measure such changes with bench.py and `perf
stat` as well. The passes tried and shelved are in HISTORY.md.

## Binary sizes

| program | via C | native |
|---|---:|---:|
| empty program | 58,840 bytes (dynamic, glibc) | 12,288 bytes (static) |
| hello | 63,104 bytes | 16,416 bytes (static) |
| `import "regex"`, one call (`regex.quote`) | 63,288 bytes | 20,512 bytes |
| eza | 242,808 bytes (dynamic, glibc) | 246,288 bytes (static) |
| HTTP server (`cmd/httpd.obx`) | 330,072 bytes | 390,056 bytes (dynamic) |
| compiler | 2,753,712 bytes (dynamic, glibc) | 2,978,481 bytes (static) |

Every C build carries the start-up CPU check and the inline math primitives
of `rt.h` (about 5 KB); a C build that calls a ported math function also
carries that function's C. The native backend generates only the functions
and globals reachable from `main` (design/native.md section 5,
"Reachability"), as gcc drops a C program's unused static functions: an
imported module costs only what the program uses of it. The runtime is linked
the same way, by sections, as `ld --gc-sections` would: every function and
datum of rt.c is a section of its own in `rtx.obx`, and the link keeps those
reachable from the runtime functions the program calls. Native functions
start at multiples of 64 bytes (code placement, above), which costs about 1%
of a large program's size.

A native executable carries its own runtime instead of linking libc. When a
program calls C libraries (libssl, sqlite3, libpq), it is dynamically linked
against them.

## Math

The float math functions are the runtime's own (musl 1.2.5, one C text for
both backends, `obx/compiler/obxmath.inc`), not libm's. sqrt, floor, ceil,
trunc, round, rint, fabs, copysign and fma are single instructions (sqrtsd,
roundsd, masks, vfmadd213sd). The polynomials of exp, exp2, log, log2, pow,
sin and cos use explicit fma; everything else is compiled with
`-ffp-contract=off`, so a result's bits do not depend on the gcc version or
the backend. The native runtime compiles the text apart (`native/math.c`,
with `-ffixed-xmm8` to `-ffixed-xmm15`), so that a native program keeps its
doubles in `xmm8`-`xmm14` across the calls.

5M calls of one function in a loop, the argument varying
(`obx/bench/math/calls.obx`), ms, best of 7, pinned, both builds interleaved;
both print the same checksum. Cycles per call in brackets (user cycles, least
of 7, divided by 5M):

| function | via C | native |
|---|---:|---:|
| exp | 10 (8.2) | 11 (9.5) |
| ln | 14 (11.7) | 15 (12.7) |
| sin | 8 (6.2) | 9 (7.1) |
| pow | 44 (40.1) | 45 (41.8) |
| atan | 19 (16.6) | 18 (16.0) |
| tan | 21 (18.6) | 19 (16.9) |

What is left on exp and sin is the call. gcc inlines the kernel into the
loop and keeps its polynomial constants in registers (exp: 36 operations an
iteration); the native loop calls it, and the kernel loads its constants on
every call (49 operations). The native loop is bound by dispatch, not by a
latency chain (`perf`: 85% of the dispatch slots used, the rest mostly
back-end stalls). The loop around the call is seven instructions: the
argument moved from its register, the call, `addsd` into the sum's register,
`addsd` of a rodata constant into the argument's, the count and the branch.

Mixing the native code's legacy SSE with the runtime's VEX code costs nothing
here. In a test loop, legacy SSE around a call of VEX code ran at the speed of
VEX everywhere (5.0 cycles an iteration). Only dirty upper halves of the
`ymm` registers slow it (7.2), and gcc's `vzeroupper` keeps them clean.
Explicit fma in more kernels (atan, tan, expm1) is the deterministic way to
get more of the speed that contraction would give.

## Decimal

`obx/bench/decimal`: micro-benchmarks of the Decimal operations
(`dbench.obx`, M0 to M10 on 4096 fixed-seed inputs each) and `run.py`, which
builds them with two trees and compares user cycles and instructions (`perf
stat`, least of N). `diff.py` is the differential test that every change to
Decimal must pass: 11 operations on 10^6 inputs each, old and new tree,
native and C, identical text, errors and scales.

Cycles per operation (the difference between 50 rounds and none over the
4096 inputs, least of 5, pinned):

| operation | native | via C |
|---|---:|---:|
| add, 6 / 19 digits at 2 places (M2) | 264 / 260 | 103 / 98 |
| mul, 6 / 19 digits (M3) | 319 / 317 | 137 / 138 |
| div, 12 by 6 digits at scale 2 (M5:s2) | 609 | 259 |
| quantize to 2 places from 6 digits (M6) | 247 | 91 |
| parse, 20 digits (M7) | 312 | 177 |
| format, 38 digits fixed (M8:f38) | 441 | 355 |
| cmp, same leading digit position (M10:diff) | 87 | 41 |

The C build is about twice as fast as native on the arithmetic: a Decimal is
a 32-byte value, which gcc keeps in registers through an inlined operation,
while the native backend keeps u128 values on the stack between operations.
The digit count is a compare tree, trailing zeros are stripped by exact
division, long division is Knuth's algorithm D in base 2^64, text is written
in 10^19 chunks two digits at a time, and parse reads eight digits at a time
(design/decimal-perf-research.md, section 8).

The C build's `-mmove-max=128 -mstore-max=128` exist for Decimal. At
x86-64-v3 gcc otherwise copies a 32-byte value passed by value with one
32-byte load straight after the 8- and 16-byte stores that wrote it; store
forwarding cannot serve that load, and every such call stalls (C Decimal add
1.4 times the cycles of the same build without AVX). A 16-byte load straight
after a callee's two 8-byte stores (a returned Decimal) still stalls; passing
such values by pointer in the generated C would remove it.

## Tasks

The cost of the concurrency primitives (`lib/task.obx`, `lib/net.obx`, SPEC
16), from `obx/bench/tasks.obx`, ns per operation, best of 3 runs:

| operation | via C | native |
|---|---:|---:|
| task switch (`task.yield`) | 6.9 | 12.9 |
| start a task (stack reused) | 40 | 21 |
| start plus a wait that parks (round trip) | 100 | 75 |
| the same inside `task.within` (a timer set and removed) | 117 | 91 |
| wait on a finished task | 27 | 13 |
| promise complete | 11 | 11 |
| `Shared.with` | 3.4 | 6.0 |
| a `task.sleep(1)` that fires, beyond a yield | −102–196 | −262–428 |
| a socket read that finds data (no park) | 733 | 724 |
| a one-byte round trip between two tasks over loopback TCP | 197,000 | 195,000 |
| a `blocking` extern call run inline (no other task could run) | 3.0 | 3.4 |
| the same through the helper pool (another task is parked) | 271–304 | 433–451 |
| the same through the pool, the process on one CPU | 8,000 | 6,050 |

The task operations are far below a system call. The native runtime reads
the clock through the vDSO, as libc does (design/native.md, section 2). The
`sleep` row is noisy: the 1 ms sleep's overshoot is divided among 10,000
tasks. The round trip is this machine's loopback: a Python ping-pong over
loopback TCP takes about 190,000 ns with two blocking threads and 205,000 ns
with epoll on one thread. The native switch is slower than C's because it
goes through more of the generic task code; it has not been tuned.

The blocking rows call C's `abs` through a `blocking` extern (SPEC 12).
Inline it is a plain extern call plus a test of the scheduler's state.
Through the pool, the task parks, a helper thread runs the call and the event
loop wakes the task: the helpers and the event loop each spin up to 50 µs
before they sleep, so a busy pool round trip needs no system call. When the
process may use only one CPU (`taskset -c 0`) the spinning is off and every
call costs an `eventfd` write and an `epoll_wait`. For scale: one
`sqlite3_step` over a cached table takes 30 ns, so SQLite's steps in a
program with other tasks cost 10 to 20 times more; a query that reads the
disk does not notice.

## HTTP server

`obx/cmd/httpd.obx` on the server library `obx/lib/httpserver.obx`
(design/http-server.md): a keep-alive HTTP/1.1 server on one thread, one task
per connection, with every limit and timeout of the design on, the hangup
cancellation armed around each handler, the request parsed strictly and the
reply built by the library. Measured by `python3 obx/cmd/http_gate.py
--servers native,c,go,node`: the server on CPU 0, the load client
`obx/cmd/httpload.obx` (native, one thread) on CPU 2, another core. 10,000
idle keep-alive connections are opened first and stay open during the load;
each load client sends `GET /` and waits for the reply, 5 s per row. Go
1.26's net/http (GOMAXPROCS=1) and Node 24's http serve the same reply, as
references. This machine's numbers move by 5 to 10% from one run to the next.

| | obx native | obx via C | Go net/http | Node 24 |
|---|---:|---:|---:|---:|
| RSS per idle connection | 15.3 KB | 14.9 KB | 19.8 KB | 9.5 KB |
| RSS with 10,000 idle connections | 153 MiB | 148 MiB | 195 MiB | 139 MiB |
| 1 client: requests/s | 4,280 | 4,480 | 3,790 | 3,650 |
| 10 clients: requests/s | 37,300 | 39,300 | 34,800 | 32,900 |
| 100 clients: requests/s | 65,600 | 68,700 | 44,500 | 43,100 |
| 1 client: latency p50 / p99 | 219 / 420 µs | 212 / 393 µs | 254 / 410 µs | 260 / 452 µs |
| 10 clients: latency p50 / p99 | 259 / 401 µs | 248 / 366 µs | 268 / 503 µs | 289 / 524 µs |
| 100 clients: latency p50 / p99 | 1.47 / 2.80 ms | 1.43 / 2.09 ms | 2.10 / 6.36 ms | 2.07 / 4.95 ms |

What a request costs the server beyond reading and writing the socket: the
router's lookup (the router is a `task.Frozen`, read by every connection's
task at once), the strict head parse (every field checked), a `poll` system
call when the hangup cancellation is armed (it checks for a half-close that
came with the request, which must not cancel it), the `Date` and
`X-Content-Type-Options` headers and the counters.

An idle connection's resident cost is two pages of its task's stack (8 KiB;
the stack is 256 KiB of address space), a 4 KiB read buffer, a 512-byte
output buffer, and the request and response records with their header lists,
kept for reuse. Under the gate's `OBX_CHECK=1`, `GET /shutdown` with the
10,000 connections open drains them all and ends the program in 0.5 to 0.6 s,
exit 0, no leak. One client is bound by this machine's loopback round trip;
with 100 clients the single-threaded load client may be the limit as much as
the server. The `ulimit -n` here is 1,048,576; the gate raises the soft
limit when it can and stops when it cannot.

### The HTTP client

`httpload --client` sends the same load through one `http.Client`
(`obx/lib/http.obx`: `get` and `text()` per request, `maxPerHost` equal to
the connections), against the native server on CPU 0, the loader on CPU 2,
5 s per row, the median of three alternating rounds (the native columns:
`http_gate.py --servers native --client --conns 1,100,1000`), beside
httpload's own loop (a fixed request, only Content-Length parsed). No idle
connections are held open here.

| connections | raw, native | client, native | raw, via C | client, via C |
|---:|---:|---:|---:|---:|
| 1: requests/s | 4,360 | 4,200 | 4,440 | 4,400 |
| 100: requests/s | 68,800 | 57,000 | 68,700 | 58,600 |
| 1,000: requests/s | 57,200 | 49,900 | 54,000 | 47,200 |
| 1: latency p50 / p99 | 219 / 380 µs | 225 / 400 µs | 214 / 383 µs | 218 / 384 µs |
| 100: latency p50 / p99 | 1.43 / 2.59 ms | 1.72 / 2.79 ms | 1.41 / 2.53 ms | 1.67 / 2.82 ms |
| 1,000: latency p50 / p99 | 16.4 / 21.6 ms | 19.6 / 27.5 ms | 17.5 / 25.0 ms | 20.4 / 28.7 ms |

The client does more work per request than the raw loop: it builds a full
request, parses every reply header, works out the framing and goes through
the pool. With the loader's core full, that is 83% to 87% of the raw
throughput from 100 connections on; with one connection the loopback round
trip is the limit and they are level. The design's gate asked for parity, so
it is not met (design/http-client.md, section 8).

### With a Postgres pool

`python3 obx/cmd/http_gate.py --servers native,c --db 4,16`: the same server
and load, each request `GET /db`, which takes a connection from a
`postgres.Pool` of 4 or 16 connections, runs `SELECT $1::int8 + 1` with the
request's number, and returns the connection. PostgreSQL 16 on the same
machine, over its Unix socket; the queries go through libpq's asynchronous
API, so each request's task parks on the socket and the server's one thread
serves the 10,000 idle connections and the load meanwhile.

| | native, pool 4 | via C, pool 4 | native, pool 16 | via C, pool 16 |
|---|---:|---:|---:|---:|
| 1 client: requests/s | 2,920 | 3,000 | 2,840 | 2,750 |
| 10 clients: requests/s | 22,300 | 21,900 | 19,500 | 19,400 |
| 100 clients: requests/s | 21,400 | 20,300 | 22,800 | 21,800 |
| 1 client: latency p50 / p99 | 324 / 570 µs | 316 / 582 µs | 333 / 606 µs | 344 / 619 µs |
| 10 clients: latency p50 / p99 | 436 / 656 µs | 443 / 698 µs | 506 / 774 µs | 509 / 788 µs |
| 100 clients: latency p50 / p99 | 4.53 / 6.23 ms | 4.78 / 6.29 ms | 4.22 / 7.61 ms | 4.47 / 6.46 ms |

One client pays two round trips, the HTTP one and the server's to
PostgreSQL (about 100 µs). From 10 clients on, the server's one thread is the
limit: each request is about 40 µs of it, mostly system calls (the HTTP read
and write, and libpq's send, poll and receive on the database socket). With
100 clients, most of them wait in the pool's queue (first come, first
served), which is where the 4 ms comes from. After the load, `/shutdown` with
the 10,000 idle connections open takes about 0.6 s, exit 0, no leak under
`OBX_CHECK=1`, every pool connection idle and nobody waiting.

The library uses libpq's asynchronous API rather than `PQexecParams` as a
`blocking` extern on the helper pool: it needs no thread per running query, a
waiting query can be cancelled (`PQcancel`) when its task is, and its cost is
the one thread's, the same budget the HTTP side uses; more cores come from
processes (below).

### With worker processes

`python3 obx/cmd/http_gate.py --workers 1,2,4,8 --db 4`: `httpd ADDR POOL
WORKERS`, n worker processes under `task.workers` sharing one listener made
before the fork, each with its own event loop and (for `/db`) its own pool of
4 PostgreSQL connections. No idle connections; 256 keep-alive load
connections spread over 8 load processes (`httpload -p 8`), 5 s per row,
`OBX_CHECK=1`. Pinning: the n workers on the first n of CPUs 0, 2, 4, 6, 1,
3, 5, 7 (1 to 4 workers each have a core of their own; 8 workers share four
cores with SMT), the load on CPUs 8-15 (the other four cores); the gate
stops if the two sets would share a core. PostgreSQL is not pinned.
Requests/s, then latency p50 / p99 in ms:

| workers | native `/` | via C `/` | native `/db` | via C `/db` |
|---:|---:|---:|---:|---:|
| 1 | 60,500; 4.25 / 6.04 | 59,500; 4.27 / 6.16 | 19,100; 13.4 / 16.6 | 19,100; 12.7 / 20.0 |
| 2 | 98,400; 2.40 / 5.72 | 99,700; 2.45 / 4.72 | 34,200; 7.31 / 9.43 | 32,400; 7.62 / 14.6 |
| 4 | 120,200; 1.80 / 6.56 | 129,200; 1.70 / 5.67 | 51,100; 4.58 / 10.7 | 50,600; 4.79 / 12.2 |
| 8 | 128,400; 1.62 / 6.46 | 127,800; 1.62 / 6.70 | 64,800; 3.27 / 12.1 | 66,300; 3.53 / 9.89 |

`/` scales 1.6 to 1.7 times at 2 workers and 2.0 to 2.2 at 4, then stops:
the ceiling is the machine's, the loopback TCP of both sides and the WSL2
virtual machine's cross-CPU wake-ups, not the workers. `/db` keeps scaling to
8 workers (3.4 to 3.5 times): each request is mostly waiting for PostgreSQL,
whose backends run on whatever CPUs are free, and 8 workers keep 32 queries
in flight. A listener of its own in each worker (`httpd ... WORKERS
reuseport`, `SO_REUSEPORT`) is the other mode; the shared listener is the
default because connections waiting in a killed worker's own accept queue
are reset with its socket.

The kill test (4 workers, `/`, the load reconnecting after an error): a third
of the way in, `SIGKILL` to one worker. Natively the worker held 83
connections and 83 requests were lost (via C 51 and 51): the ones in flight
on its connections, at most one each. The supervisor started it again after
109 ms (112 ms via C; the 100 ms backoff, and a crash after the worker is
ready never counts toward giving up), it accepted from the same queue, and
the run kept 128,000 req/s natively (115,900 via C) over the 5 s. The
`/shutdown` that follows ends every worker drained, exit 0, the restart
counted.

## TLS

`obx/lib/tls.obx` over OpenSSL 3.0.13 (design/tls.md), and https in the http
client. Measured by `python3 obx/cmd/tls_bench.py`:

- `obx/cmd/tlsbench.obx`, built natively and via C, against OpenSSL's own
  clients and curl; the server on CPU 0 and the client on CPU 2;
- a P-256 ECDSA certificate from a test CA, verified by every client; TLS
  1.3, TLS_AES_256_GCM_SHA384, X25519; each row the median of three runs.
- **Handshakes:** full handshakes one after another (connect, handshake,
  close_notify, no resumption) against `openssl s_server -www`.
- **Bulk:** one GET of 1 GiB from `tlsbench serve` (an https server written
  with `tls`, native), its body streamed in 64 KiB reads.
- **Requests:** 20,000 small GETs over kept-alive connections to the same
  server (5,000 for curl, one URL list).

| | obx native | obx via C | OpenSSL / curl |
|---|---:|---:|---:|
| handshakes/s (s_server is the limit) | 781 | 778 | 578 (`openssl s_time -new`) |
| client CPU per handshake | 524 µs | 528 µs | 587 µs (`s_time`) |
| GET 1 GiB: MB/s | 1,947 | 1,956 | 2,007 (curl) |
| GET 1 GiB: client CPU | 0.47 s | 0.47 s | 0.46 s (curl) |
| requests/s, 1 connection | 4,420 | 4,280 | 3,250 (curl) |
| requests/s, 10 connections | 29,960 | 30,760 | |

**Handshakes.** A handshake's client side costs what OpenSSL's own client
costs. Most of the 0.5 ms is in OpenSSL 3.0: the per-connection `SSL` setup
(provider lookups), verifying the ECDSA chain and the X25519 exchange. The
engine's memory BIOs and the parking add nothing measurable. The rate is
bound by s_server, which handshakes one connection at a time.

**Bulk.** Bulk throughput is decryption: AES-GCM with AES-NI, under 0.5 s of
client CPU for 1 GiB, the same as curl's. The bulk row moves by up to a third
between runs on this machine.

**Requests.** The one-connection row is the loopback round trip, and it moves
by 15 to 25% from run to run. For scale, plain http to `httpd` reaches about
4,300 per second on one connection (above). These runs are also the H2
gate's "1,000 https requests over kept-alive connections"
(design/http-client.md), under no `OBX_CHECK`; the tests run under
`OBX_CHECK=1` with no leak.

## Reproducing

```sh
obx/compiler/build.sh                                  # out/obxc from the seed
python3 obx/bench/bench.py -n 11
python3 obx/bench/regex/bench.py -n 7                  # the regex table (Go, Rust and rg built by it)
python3 obx/bench/json/bench.py --jq JQ                # the JSON library and jq large-input tables
python3 obx/tests/markdown.py --perf --ref CMARK       # the Markdown table
python3 obx/cmd/eza_gate.py -j 12                      # eza correctness on both builds
obx/compiler/out/obxc obx/cmd/eza.obx --native -o eza  # and without --native for the C build
obx/tools/native_selfbuild.sh                          # the natively built compiler
obx/compiler/out/obxc obx/bench/tasks.obx --native -o tasks && ./tasks
obx/compiler/out/obxc obx/bench/math/calls.obx --native -o calls && ./calls exp
python3 obx/bench/decimal/run.py --base DIR            # Decimal, two trees compared
python3 obx/tools/placement.py                         # code placement spreads
python3 obx/cmd/http_gate.py                           # the server table (about 2 minutes)
python3 obx/cmd/http_gate.py --servers native --client --conns 1,100,1000   # the client table
python3 obx/cmd/http_gate.py --servers native,c --db 4,16   # with Postgres ($OBX_PG_DSN)
python3 obx/cmd/http_gate.py --workers 1,2,4,8 --db 4  # worker processes (about 3 minutes)
python3 obx/tests/httpsrvgen.py -n 100000              # the server's parsing against Go and llhttp
python3 obx/cmd/tls_bench.py                           # TLS handshakes, bulk and https requests (about 2 minutes)
```
