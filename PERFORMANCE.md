# obx performance

obx builds a program in two ways:

- **native**: `obxc --native`, the compiler's own x86-64 backend. It writes an
  ELF executable directly, with no gcc and, unless the program calls C
  libraries, no libc.
- **via C**: `obxc`, which writes C and runs `gcc -O2`.

Both are built from the same IR and must behave identically; the gate checks
that on 1,616 programs. This page measures what each costs. Measured on 3
November 2026 at `2ce4860`, after the fifth native performance pass
(inlining in the IR), on a quiet machine. The loop and array rows, the
compiler's `--native` cost and the sixth pass's row are from the sixth
native performance pass (loops; branch `perf6`), measured the same day; the
blocking-call rows are from concurrency stage C1c (branch `c1c`), the sieve
row and the seventh pass's row from the seventh (branch `sieve`), the
eighth pass's row from the eighth (branch `perf8`) and the regex table from
regex phase R2 (branch `r2`), all on 3 November. The compiler table, the
binary sizes and the HTTP tables were measured again on 4 November. The numbers are
host-dependent: use them to compare the columns, not as portable constants.

## Host and method

- AMD Ryzen 7 8745HS (16 threads), WSL2, Linux 6.18, gcc 13, glibc 2.39.
- Wall clock, best of N runs (11 for the benchmarks, 9 for eza and the
  compiler), warm cache, output to `/dev/null`. Timings vary by 5–10%
  between runs on this machine; below about 50 ms, process start-up is part
  of the figure.
- Every row checks its outputs first: both builds of each benchmark print the
  same result and are leak free under `OBX_CHECK=1`, and both eza builds write
  byte-identical listings.

## Benchmarks

`python3 obx/bench/bench.py -n 11` ([obx/bench/](obx/bench/README.md)):

| benchmark | via C | native | native/C | what it does |
|---|---:|---:|---:|---|
| fib | 331 ms | 330 ms | 1.00 | recursive calls |
| loop | 267 ms | 213 ms | 0.80 | two overflow-checked operations per iteration |
| array | 104 ms | 91 ms | 0.88 | checked indexing |
| alloc | 122 ms | 65 ms | 0.54 | 20 million records allocated and freed |
| lists | 36 ms | 31 ms | 0.85 | linked list build, sum, drop |
| strings | 76 ms | 47 ms | 0.62 | concatenation and `toStr` |
| trees | 24 ms | 20 ms | 0.86 | binary trees |
| tokens | 16 ms | 12 ms | 0.74 | byte scanning and hashing |
| sieve | 182 ms | 127 ms | 0.70 | the sieve of Eratosthenes to 10 million, ten rounds |
| maps | 256 ms | 351 ms | 1.37 | `Map[str, int]` with short and long keys, `Map[int, int]` (keyed hashing) |

The sieve also has a hand-written C version with no checks
(`obx/bench/c/sieve.c`, `gcc -O2`, bench.py's "plain C" column): 135 ms in
the same run. Native obx runs as many instructions as it (1,414M) and about
as many cycles (497M against 508M, best of 11 under `perf stat`); through C
obx runs 1,428M and 741M, because gcc keeps the overflow and bounds checks
the native backend proves away (main's native build: 3,042M and 1,002M).

- **Native is ahead on allocation and strings** (alloc, lists, strings,
  tokens; trees narrowly). Since the fifth pass small functions and glue
  are inlined before code generation (lists: `push` and the list's drop in
  the loop). Its freestanding runtime has a size-class
  allocator with no locks or libc bookkeeping, and the string and index
  checks are generated inline.
- **Tight loops are now native's too** (loop, array; fib is level). Since
  the sixth pass the native backend tests at the bottom of loops and
  aligns their bodies, reads array headers once per loop, and drops the
  bounds and overflow checks that conditions, passed checks and induction
  variables prove cannot fail ([design/native.md](obx/design/native.md)
  sections 4 and 8). gcc does more (it unrolls, schedules and
  strength-reduces); obx's checks stay where they can fail, so array keeps
  one bounds check per element (its bound is unrelated to the array's
  length). Before the pass these were the widest gaps (1.59 and 1.67).
- **The sieve is at plain C's speed** since the seventh pass: the inner
  loop `j = j + i` keeps no bounds or overflow check (j stays at least its
  start since i >= 1, and `j <= n` with the array's length n + 1 proves the
  index), the fill of `array(n + 1, true)` is `rep stosb`, the count of
  primes has no branch (`cmp; sbb`, as gcc's) and no overflow check (a
  counter grows at most once an iteration), the inner loop sits inside one
  cache line, and the native heap keeps the freed 10 MB block for the next
  round instead of faulting its pages in again.
- **Keyed hashing costs maps 6-10%** (branch `hashfrozen`). `str`,
  `i128`, `u128` and `Decimal` hash with SipHash-1-3 under a per-process
  key, and `Map` mixes a per-process seed into its slots (lib/README.md,
  "Keyed hashing"); before, `str` was FNV-1a. maps (200,000 keys of each
  kind, put, four rounds of lookups, misses) went from 242 to 256 ms via C
  and 320 to 351 ms native (bench.py -n 11, the same run). Under `perf
  stat` (least cycles of 5): native 2564M instructions and 1327M cycles to
  3669M and 1456M, via C 1835M and 953M to 2180M and 1011M. 2 million
  hashes of a 6-byte key: native 109M to 186M cycles, C 91M to 125M; of a
  60-byte key native 466M to 511M, C 420M to 283M (SipHash takes 8 bytes a
  step, FNV one). The native code reads each byte through a checked
  `strByte`, 8 per word; a word-load intrinsic would remove most of the
  gap. Elsewhere the cost is in the noise: the self-build 1991M to 1980M
  cycles (4891M to 5018M instructions), eza `-T` 210M to 209M native and
  180M to 181M via C, tokens unchanged; compiling the compiler with the
  gcc-built `obxc --native`, 1662M to 1695M cycles (2%; `sipKeyedStr` is
  1.9% of its samples). Two compiler changes came with it: a shift of a
  `u64`, `u32` or `byte` by a constant below its width is a plain C
  operator (it was a runtime call, which gcc could not fold into a rotate),
  and the native backend emits `rol` for `x << k | x >> (64 - k)`.
- **Code placement matters.** Native fib runs at 1,547M, 1,690M or 2,020M
  cycles with identical instructions, depending only on where its code
  falls relative to 64-byte boundaries. The generated code starts at a
  multiple of 64 and every function at a multiple of 16, so a change in the
  runtime's size no longer moves it, but a change in the functions before
  it still can: fib's gain and trees' loss in the fifth pass (8% each way)
  are placement alone. Before the alignment fix, merging the concurrency
  runtime had silently cost fib 20%. The same happened twice more at the
  C1b merge, with the same instructions: native alloc lost 26% when the
  event loop moved the runtime's allocator, and fib via C 11% when `rt.h`
  grew. The native runtime and gcc's builds of programs now put functions
  at multiples of 64 (`-falign-functions=64`), which restored both.

## Regex

`python3 obx/bench/regex/bench.py -n 7` ([obx/bench/regex/](obx/bench/regex/bench.py)),
the benchmark set of [design/regex.md](obx/design/regex.md) section 4, at
the end of phase R2 (branch `r2`, 3 November 2026). The corpus is 3.2 MB written by
`gen_corpus.py` from a fixed seed (prose with the names the patterns look
for, a log, C-like source; ASCII). Each driver times its own searches after
reading the file: the best of 5 searches, best of 7 runs, in ms. "Matches"
is the same for every driver (the script checks); `(a*)*b` runs over
100,000 `a`s. Go is go1.26.4's `regexp`, Rust the `regex` crate 1.12.3
(built offline from the cargo cache), and rg is ripgrep 14.1.1 counting
matches as a whole process, start-up and file reading included: a
reference point, not a like-for-like figure.

| pattern | matches | obx native | obx via C | Go | Rust | rg |
|---|---:|---:|---:|---:|---:|---:|
| `Sherlock Holmes` | 926 | 0.16 | 0.21 | 0.47 | 0.08 | 1.6 |
| `Sherlock\|Holmes\|Watson\|Irene` | 7,350 | 1.34 | 0.94 | 134.48 | 0.42 | 2.8 |
| `\w+` (all matches) | 669,532 | 68.89 | 38.14 | 136.78 | 17.23 | 41.0 |
| `(?i)sherlock` | 4,280 | 1.13 | 0.81 | 70.88 | 0.46 | 3.2 |
| `[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}` | 848 | 0.22 | 0.12 | 78.55 | 0.09 | 1.8 |
| `(?m)^ERROR .*timeout$` | 197 | 0.28 | 0.33 | 31.83 | 0.59 | 2.0 |
| `(?P<y>\d{4})-(?P<m>\d{2})-(?P<d>\d{2})` (captures) | 10,755 | 4.67 | 3.33 | 72.66 | 1.28 | 7.8 |
| `(a*)*b` over 100,000 `a`s | 0 | 0.00 | 0.00 | 3.33 | 0.00 | 1.6 |

- **obx and Rust.** The engine has RE2's lazy DFA and Rust's literal
  searches (design/regex.md, "R2 as built"). Through C, obx takes 1.3 times Rust's
  time on the email pattern, 1.8 on `(?i)`, 2.2 on the alternation and
  `\w+`, 2.6 on the literal and the captures, and about half Rust's time on
  the `ERROR` lines (a prefilter on `ERROR ` and the suffix check on
  `timeout`); natively 2.0 to 4.0 times Rust's time, and again about half
  on the `ERROR` lines. These are
  the cases the plan aimed at; none is past three times through C.
- **Where the gap is.** The literal: Rust's memchr uses AVX2 (32 bytes a
  step), the runtime SSE2 (16). `\w+` and the captures are per-match
  costs (670,000 and 10,755 matches): each match is a forward and a reverse
  DFA run plus, for captures, the backtracker on the span; Rust's inner
  loops are tighter (unchecked indexing, a byte DFA with no rune decode).
  The native build loses most on these DFA loops (1.8 times its C build on
  `\w+`): the native backend's bounds and overflow checks in the inner loop
  (see below), not the library.
- **`(a*)*b`** takes no time: the text has no `b`, and every match ends
  with one (the suffix check), so the search stops before any matcher runs.
  The DFA on such patterns is covered by the tests (`regex_linear`,
  `regex_dfa`), not by this row.
- **obx and Go.** obx is 2 to 650 times faster than Go's `regexp`, which
  has no DFA.
- **Go's matchers** (the Pike VM, the backtracker and one-pass), which the
  engine uses for short texts (under 128 bytes), for one-pass patterns, and
  when the DFA's cache fills too often, run 1.4 to 1.8 times as fast
  through gcc as natively. The profile is the Pike VM's `add` and `step`:
  short loops over the fields of a `mut &` record's arrays, each access
  with its bounds check and each index sum with its overflow check. gcc
  keeps the arrays' addresses in registers and drops most of the checks;
  the native backend keeps fewer. This is a native-backend matter (the
  code generator, not the language).
- **Linear time.** The library test `regex_linear` checks that 100,000
  bytes take about ten times as long as 10,000 for six such patterns under
  the Pike VM, the backtracker and the DFA (also in a tiny cache), among
  them `(a|b)*a(a|b){20}`, whose DFA has 2^21 states; `regex_dfa` does the
  same on random text, where the cache fills and the search falls back to
  the Pike VM.

## eza

[obx/cmd/eza.obx](obx/cmd/eza.obx), a 1,600-line implementation of the
common subset of eza 0.23.3. `LC_ALL=C TZ=UTC`; the long listings use
`--bytes --time-style=long-iso -n`, which this eza requires:

| listing | output | via C | native |
|---|---:|---:|---:|
| `-T /usr/share` (tree) | 2.1 MB | 105 ms | 117 ms |
| `-T -l /usr/share` (long tree) | 3.7 MB | 200 ms | 201 ms |
| `-l /usr/lib/x86_64-linux-gnu` | 51 KB | 4 ms | 4 ms |

The golden suite (`python3 obx/cmd/eza_gate.py -j 12`) passes on both
builds: 482 cases, 72 live-only cases skipped.

## grep

[obx/cmd/grep.obx](obx/cmd/grep.obx) against GNU grep 3.11 on 100 MB of
generated text (1.8 million lines; the generator is in
[obx/cmd/README.md](obx/cmd/README.md)). Best of 5, output to a pipe,
measured on 4 November 2026 (branch `grepfast`):

| search | GNU | native | via C | native at `1c6d06c` |
|---|---:|---:|---:|---:|
| `needle5007` (a literal) | 26 ms | 14 ms | 14 ms | 90 ms |
| `-c omega` | 84 ms | 61 ms | 59 ms | 151 ms |
| `-c '[0-9]*7 alpha'` | 70 ms | 15 ms | 15 ms | 352 ms |
| `-c -w eta` | 204 ms | 190 ms | 180 ms | 1,123 ms |
| `-c -v '^0'` (every line) | 47 ms | 48 ms | 42 ms | 468 ms |
| `-c -E 'a.*z.*e'` | 210 ms | 106 ms | 91 ms | 455 ms |
| `-n omega` | 224 ms | 194 ms | 160 ms | 376 ms |
| `-o -w eta` | 386 ms | 330 ms | 298 ms | 1,653 ms |

What made the difference:

- **Lines, not matches.** The regex library's `matchingLines` runs the
  lazy DFA over a chunk and reports the lines with a match. It starts again
  after each such line, with no call per line.
- **A required literal first.** `[0-9]*7 alpha` must contain `7 alpha`; a
  memory search finds it and only its line is checked.
- **`-w` as one regex.** The word with a non-word character or a line end
  on each side. Before, each candidate line was checked again.
- **Output in pieces.** The prefix, the line and the newline go into a
  byte buffer; no string is built per line.
- The nineteenth is shrink-wrapping of the cheapest kind: a function that
begins with `if c { return v }` on its parameters (comparisons of
parameters with each other or with small constants, `v` nothing, a constant
or a parameter) tests before the prologue, in the argument registers, and
returns with a bare `ret`. `tools/perfsuite.py main perf19 -n 5`: run suite
-1.66% instructions, -1.76% cycles (fib -23.5% instructions, -8.3% cycles;
every other program within 0.0% of its instructions: the loops, trees, maps
and tools begin with no such guard, or with one that returns a record or
loads through a pointer), compile suite -0.82% instructions, -1.34% cycles
(compiler `--native` -0.87%, `--emit-c` -1.08%, grep -1.02%, eza -0.75%,
zoxide -0.38%). 51 of the 1,078 functions the backend generates for the
compiler begin with guards. The sampled profile (4,360M instructions) puts
the whole of the callee-saved pushes of those functions' early exits at
about 0.4% of the instructions (about 1% with the frame setup and
restores); the measured gain is that.
- **Compiling zoxide** had become 18% slower with perf15 (1,049M to 1,239M
instructions on the frozen inputs). The cause was zoxide's generated init
functions: 1,347 `b.add("...")` calls, the loop of `StrBuf.add` inlined at
up to 229 of them per function, two hoisted string locals for each loop,
and a register allocator whose check of a register scanned every local
already given one. Two fixes: `allocRegs` keeps the given locals per
register and `meets` builds no arrays (byte-identical code; zoxide 1,234M
to 1,133M, the compiler -0.3%), and `zoxide_gen.py` emits one `b.add` per
run of literal text (203 calls instead of 1,347; every rendering
unchanged). Together, compiling zoxide from main: 1,234M to 533M
instructions (-57%), and its binary 594 KB to 352 KB.
- **`appendStr`** (a std intrinsic: a string's bytes at the end of a
`[]byte` in one copy) makes `StrBuf.add` a call instead of a loop of
`append(strByte)` per byte. Building 20 strings of 400,000 words each:
native 1,782M to 899M instructions, 670M to 364M cycles (-46%); via C 485M
to 269M cycles (-45%). With no inlined loop at each `add`, compiling zoxide
takes 471M to 435M instructions (-7.6%), the compiler -0.5%.

**The runtime's memcpy.** See "Native backend progress".

User-mode instructions for the native build fell from 1,186M to 886M
(`-c omega`), 5,045M to 155M (`[0-9]*7 alpha`), 16,466M to 2,545M (`-w`)
and 7,451M to 672M (`-v '^0'`).

### On 1 GB

The same generator run to 10^9 bytes (18.2 million lines), the cases above
and more pattern shapes, at `c80b952`. Best of 3, output piped to `md5sum`,
the file in the page cache; two other builds were running on the machine,
so treat differences under about 10% as noise. ripgrep (14.1.1, one file)
is for reference. On every case the native and C builds printed exactly
what GNU grep printed.

| search | GNU | ripgrep | native | via C |
|---|---:|---:|---:|---:|
| `needle5007` (a literal, one line) | 261 ms | 35 ms | 137 ms | 140 ms |
| `-c zzz` (no match) | 335 ms | 30 ms | 139 ms | 138 ms |
| `-c omega` | 859 ms | 348 ms | 605 ms | 605 ms |
| `-v -c omega` | 991 ms | 564 ms | 723 ms | 741 ms |
| `-c '[0-9]*7 alpha'` | 719 ms | 49 ms | 148 ms | 148 ms |
| `-c -E '(alpha\|beta) gamma'` | 1,097 ms | 383 ms | 629 ms | 599 ms |
| `-c -i NEEDLE` | 746 ms | 72 ms | 181 ms | 163 ms |
| `-c -i 'OMEGA BETA'` | 1,316 ms | 414 ms | 196 ms | 175 ms |
| `-c -w eta` | 4,819 ms | 1,704 ms | 1,995 ms | 1,839 ms |
| `-c -v '^0'` (every line) | 574 ms | 628 ms | 448 ms | 417 ms |
| `-c -E 'a.*z.*e'` | 2,151 ms | 1,708 ms | 1,115 ms | 955 ms |
| `-c -E 'needle(1\|2)[0-9]{3}7'` | 557 ms | 35 ms | 179 ms | 162 ms |
| `-c -E '^[0-9]+7[0-9]'` | 438 ms | 782 ms | 573 ms | 530 ms |
| `-c -E '(alpha\|omega) .*(beta\|psi)$'` | 2,580 ms | 950 ms | 1,257 ms | 1,131 ms |
| `-c -x '[0-9]* alpha beta gamma delta'` | 390 ms | 31 ms | 143 ms | 138 ms |
| `-c -F -f` five fixed strings | 1,308 ms | 395 ms | 1,734 ms | 1,656 ms |
| `-n omega` | 2,417 ms | 1,382 ms | 2,129 ms | 1,864 ms |
| `omega` (prints 8.8 million lines) | 1,842 ms | 825 ms | 1,522 ms | 1,366 ms |
| `-o 'needle[0-9]*'` | 570 ms | 46 ms | 147 ms | 143 ms |
| `-o -w eta` | 6,826 ms | 2,942 ms | 3,504 ms | 3,057 ms |
| `-n -w eta` | 7,267 ms | 3,193 ms | 3,539 ms | 3,179 ms |

The times scale with the file: each is about ten times its 100 MB figure,
so nothing in the obx port grows worse than linearly.

**Against GNU grep:** obx is ahead on 19 of 21 cases, often by 2 to 5 times.

**Still behind GNU:**
- `-F -f` with several fixed strings, 1.3 times slower. Each string becomes a branch of one regex, where GNU and ripgrep use a multi-string search (Aho-Corasick, Teddy).
- `^[0-9]+7[0-9]`, also 1.3 times slower.

**Against ripgrep:** ripgrep is faster wherever a literal or a rare
required string can be found with SIMD (`needle`, `zzz`, `-x`, `-o`).
obx is faster on case-insensitive patterns (`-i 'OMEGA BETA'`), on
`a.*z.*e` and `^[0-9]+7[0-9]`, and when every line matches (`-v '^0'`). The two are
close on `-w`.

## jq

[obx/cmd/jq.obx](obx/cmd/jq.obx), the jq 1.7.1 port, against jq 1.7.1
built from its release tarball (`./configure --with-oniguruma=builtin`,
the default `-O2`). Measured on 5 November 2026 (branch `jq`), best of
3 or 5 wall times, on a machine shared with other agents: figures vary by
10–20% between runs.

**Large input.** `python3 obx/bench/json/bench.py`, run on an 80 MB file
of 182,290 generated records. The output goes to a file, and the three
outputs of each filter must be identical.

| filter | jq 1.7.1 | native | via C |
|---|---:|---:|---:|
| `.` | 2.48 s | 1.86 s | 1.93 s |
| `-c .` | 2.20 s | 1.71 s | 1.67 s |
| `length` | 1.42 s | 1.15 s | 1.09 s |
| `[.[].items[].qty] \| add` | 1.77 s | 1.30 s | 1.23 s |
| `map(select(.active and .score > 0) \| {id, city: .address.city})` | 1.69 s | 1.36 s | 1.44 s |
| `group_by(.address.city) \| map({city: .[0].address.city, n: length})` | 1.97 s | 1.92 s | 1.82 s |

**Computation.** Programs with no input (`jq -n`); the outputs are
identical.

| program | jq 1.7.1 | native | via C |
|---|---:|---:|---:|
| `1` (start-up) | 2 ms | 4 ms | 3 ms |
| `reduce range(3000000) as $i (0; . + $i)` | 475 ms | 479 ms | 333 ms |
| `[range(1000000)] \| map(. * 2) \| add` | 388 ms | 267 ms | 233 ms |
| `[range(200000) \| tostring] \| sort \| length` | 112 ms | 82 ms | 64 ms |
| `def fib: ...; 25 \| fib` (recursive, 250,000 calls) | 57 ms | 110 ms | 87 ms |
| `[limit(100000; repeat(1))] \| length` | 43 ms | 10 ms | 8 ms |
| `[range(100000) \| {a: ., b: "x\(.)"}] \| group_by(.a % 10) \| map(length)` | 207 ms | 271 ms | 226 ms |
| `[range(50000) \| tostring \| test("7$")] \| map(select(.)) \| length` | 70 ms | 78 ms | 59 ms |

**Where the port wins.** It is faster wherever JSON is parsed or written.
jq builds a decNumber for every literal, and its writer goes through
`jv_dump_term`. The JSON library keeps the literal's text and writes it
back. The port also wins on simple generators: `limit`, `range` and `map`
are native in the port, and continuation calls cost less than jq's
bytecode with its fork points.

**Where jq wins:**

- **Calls of jq-defined functions.** `fib` is 1.5–2× slower: each call
  pushes frames and closures, and runs its body through a capturing
  continuation.
- **Start-up.** The port parses and resolves `builtin.jq` on every run
  (2 ms).
- **Sorting objects by key.** `sort_by` and `group_by` on objects are a
  little slower: the comparison walks the counted boxes.

**What made the difference.** Two changes on the branch:

- **Expressions with at most one output run to completion first.** In
  continuation-passing style the rest of the program otherwise runs
  inside each operand, and `25 | fib` overflowed the stack. This also
  saves stack traffic generally.
- **Compiled regexes are cached** for the run. `test("7$")` over 50,000
  strings went from 245 ms to 78 ms.

`json.cmpArrays` also stopped copying elements, which took sorting 100,000
one-element arrays from 271 ms to 156 ms. The library's own numbers
against Python's `json` are in [obx/design/json.md](obx/design/json.md),
section 9.

## The compiler

The obx compiler compiling itself (`obx/compiler/main.obx`), built each way:

| | gcc-built | native-built |
|---|---:|---:|
| self-build to an executable (`--native`) | 472 ms | 529 ms |
| self-build to C (`--emit-c`) | 288 ms | 297 ms |
| full build through gcc | 17 s, nearly all of it gcc | — |
| compiler binary | 2,000,616 bytes (dynamic, glibc) | 2,507,065 bytes (static, no libc) |

Measured on 4 November on branch `perf13` (after the thirteenth pass, best
of 11 on a machine other builds were loading; at `1ccdc03` just before it the
same runs gave 542, 589, 309 and 307 ms, and the gcc-built compiler ran the
`--native` self-build in 3,706M instructions and 1,758M cycles against
3,985M and 2,068M). At `fd6f747` (after the ninth pass) the times were 509,
570, 286 and 300 ms; after the eighth, 493, 589, 274 and 310 ms on 270 fewer
lines of compiler. At
`5900ba3` (bounds checks compared in place) the change is below the timing
noise: the self-build's cycles went from 2,176M to about 2,164M, and on a
later run of the same machine the `fd6f747` native compiler and the
`5900ba3` one both took 589-594 ms (median of 21), the gcc-built one
533-546 ms. The
source is 23,710 lines of obx and 4,756 lines of C runtime (the C backend's
`rt.h` and the native backend's freestanding `rt.c`, each with task stacks
and switching, the event loop, timers, sockets and the SSE2 string
searches), not counting `rt.obx` and `rtx.obx`, which are generated from
them.

- The native-built compiler is 1.03-1.12x slower than the gcc-built one
  (1.1-1.2x before the ninth pass, 1.55x before the fifth). It runs 1.20x
  the instructions (4,441M against 3,706M after the thirteenth pass, which
  sped up the gcc-built compiler more; 1.17x at `5900ba3`, 1.18x
  at `fd6f747`, 1.48x before the ninth, 1.89x before the fifth) at a higher rate per cycle. What is left
  is spread thin: of the 347 functions that take longer native, the 20
  worst hold 43% of the difference. It is in the mix (twice gcc's share of
  register-to-register moves, three times its stores to the stack, half again as
  many compares: spills, and loads and bounds checks repeated from one
  statement to the next), not in a few functions.
  A compiler is call-heavy and branchy, which is gcc's strength; inlining
  in the IR closed much of the gap.
- `--native` itself does more work since the fifth pass (inlining, and
  code generation of the larger IR): with the gcc-built compiler, whose own
  code does not change, building the compiler natively takes about 20%
  longer than before (273 ms then, on smaller sources); building eza, 10%.
  The sixth pass's loop optimizations add 9% to the instructions of that
  build (2,817M to 3,069M); the natively built compiler, whose own loops
  they speed up, comes out even.
- Since both backends start from one IR (2026-11-03), `--native` is about
  30% faster than before, because it no longer prints and re-reads C, and
  `--emit-c` is about 50% slower, because it now builds the IR before
  printing. Winning back the C path is listed in design/native.md section 8.

### Native backend progress

User-mode instructions (`perf stat`), the native-built compiler building
itself, native eza `-T /usr/share` and native bench fib:

| step | self-build | eza `-T` | fib |
|---|---:|---:|---:|
| before the third performance pass | 1,817M | 757M | |
| after it (live-range registers, operands in place) | 1,649M | 659M | |
| before the fourth, on later sources | 3,938M | 659M | 6,127M |
| after it (temporaries in callee-saved registers) | 3,923M | 649M | 5,630M |
| before the fifth, on later sources | 4,022M | 649M | 5,630M |
| after it (inlining in the IR, value records in fields) | 3,747M | 642M | 5,630M |
| at `2ce4860` (with C1b; the compiler 1,200 lines larger) | 3,973M | 642M | 5,630M |
| before the sixth, on later sources | 4,732M | 642M | 5,630M |
| after it (loops rotated and aligned, `loops.obx`) | 4,673M | 634M | 5,630M |
| before the seventh, on later sources | 4,821M | 634M | 5,630M |
| after it (the sieve: steps, lengths, counters, fills) | 4,815M | 633M | 5,630M |
| before the eighth, on later sources | 5,665M | 632M | 5,629M |
| after it (the regex engine: loads out of loops, memory operands, sites) | 5,388M | 632M | 5,629M |
| before the ninth, on later sources | 5,800M | 632M | 5,629M |
| after it (the compiler: operands in place, record literals in their slots, value records' fields, borrowed str parameters) | 4,617M | 594M | 5,629M |
| at `5900ba3` (bounds checks compare in place; the library's `io.Reader` in the compiler's sources) | 4,585M | 594M | 5,629M |
| before the thirteenth, on later sources (`1ccdc03`) | 4,668M | 596M | 5,630M |
| after it (the compiler's own data and walks: 64-byte IR nodes, shape tables, one use count) | 4,441M | 596M | 5,630M |
| before the fourteenth, on later sources | 4,434M | 596M | 5,630M |
| after it (aggregate copies of 32 bytes and more through `xmm15`; small record literals returned straight into registers) | 4,359M | 594M | 5,630M |
| before the fifteenth, on later sources (`5590fb6`) | 4,479M | 596M | 5,630M |
| after it (`str` headers out of loops) | 4,469M | 580M | 5,630M |
| the sixteenth, on `perf16` only (assembler load reuse; shelved, not merged), main's 4,399M on the same sources | 4,439M | 577M | 5,630M |
| the nineteenth (guards that return early run before the prologue), main's 4,387M on the same sources | 4,349M | 577M | 4,305M |

The compiler had grown between the third and fourth passes (it builds
the IR for both backends since then), so its two pairs of rows do not
compare with each other. The fourth pass cut fib's cycles by 23% (2,188M
to 1,690M); the self-build's and eza's by about 1% and 2%. The fifth cut
the self-build's cycles by 12% (1,699M to 1,496M; the compiler's own work
without the pass, `--emit-c`, by 17%), eza's by 9% (233M to 213M) and the
lists benchmark's by 16%. The sixth is about loops: bench loop from 11,000M
instructions and 2,007M cycles to 8,000M and 1,003M, array from 4,615M and
782M to 2,413M and 410M; the self-build and eza gain about 1% in
instructions, nothing measurable in cycles.
The eighth (the regex engine) cut
the self-build's instructions by 5% and its cycles by 6% (2,392M to
2,246M), at 9% more instructions for the gcc-built compiler's `--native`
(3,342M to 3,649M: the effects walks and summaries of `loops.obx`, the
second register choice and the sites). The ninth (the compiler itself) cut
the self-build's instructions by 20% and its cycles by 9% (2,385M to
2,176M), eza's instructions by 6%, and the native compiler's binary by 10%.
The thirteenth changes no generated code (every program's output is
byte-identical), only the compiler's own work: `ir.Node` shrinks from 88
to 64 bytes (string and float payloads in side tables), simplify's
address-taken walk folds into the use count, x64gen's tree walks dispatch on
a shared table of node shapes, and the second count of uses goes. The
self-build's instructions fall 4.9% (4,668M to 4,441M) and its cycles 11%
(best of 11: 2,213M to 1,973M; the spread on a loaded machine was about
50M). Most of the cycles are the node shrink alone (6%, fewer cache
misses for 1% of the instructions). The gcc-built compiler gains more: 7%
of its instructions (3,985M to 3,706M) and 15% of its cycles (2,068M to
1,758M). Per-commit figures are in design/native.md section 8.

The fourteenth is small: copies of records of 32 bytes or more (the 64-byte
`ir.Node` among them) go 16 bytes at a time through `xmm15`, and a returned
record literal of whole eightbytes loads `rax`/`rdx` from its fields instead
of through a frame temporary. The self-build's instructions fall 1.7%
(4,434M to 4,359M; its cycles 0.2%, below the noise), maps 0.4% of the
instructions and 2% of the cycles, eza `-T` 0.5% and 1.3%; fib, grep and
the rest do not change. It is smaller than the ~250M extra stack stores
suggested: a store is cheap, and `movdqu` copies of 16 to 31 bytes made the
self-build 5% slower in cycles (loads that two 8-byte stores had just
written cannot be forwarded), so they stay 8 bytes at a time. 32-byte AVX
copies were no faster than 16-byte ones.

The fifteenth reads a `str`'s data pointer and length once before a loop
(`loops.obx`; a byte is then `*(sd + rt_sidx(i, n, w))`, and the bounds
check is proved away when `i` steps up from 0 under `i < n`). The
self-build is level (4,479M to 4,469M instructions, 2,000M to 1,989M
cycles; the gcc-built compiler's `--native` runs 0.4% more instructions
for the analysis); maps loses 7% of its instructions and 5% of its cycles
(1.24 to 1.17 times the C build), sort 8% of both, `grep -n` 5% and 4%,
tokens 7% of its instructions; `grep -cE 'a.*z.*e'` takes 8% more cycles on
4% fewer instructions (one dependent-load-bound loop; not understood).

**The runtime's memcpy.** On this machine, `rep movsb` copies about 15
times slower when the destination is 1 to about 32 bytes past the source,
modulo 4 KiB. A string made from a byte array hit that case: 80% of a
literal grep was that one copy. memcpy now copies 64 bytes a step with SSE2
in that case (`grepfast`). The self-build moved from 4,660M to 4,668M
instructions; its cycles did not change (2,305M and 2,307M).

The earlier steps and the per-commit figures are in
[obx/design/native.md](obx/design/native.md) sections 4 and 8. What remains
is mostly work the code itself asks for (allocation, string concatenation,
reference counts, copies of large value records), not register use.

### Judging a pass

From 2026-11-04 a backend pass is judged by `obx/tools/perfsuite.py`, not
by the self-build alone. The self-build was a moving target: each pass
adds to the compiler's own sources, so part of every reported cost was just
more code to compile. And the compiler is one program; every other program
only gains.

    python3 obx/tools/perfsuite.py main perfNN     # or two compiler paths

It builds both revisions' native compilers from their seeds, then measures
them on inputs frozen at one revision (`7d519d1`; moved only on purpose,
with a note here):

- **Compile suite:** the frozen compiler (`--native` and `--emit-c`) and
  the frozen eza, grep and zoxide, compiled by each compiler. This is the
  compile speed a user sees: the new analysis's cost minus what the
  compiler gains from being compiled better itself.
- **Run suite:** the frozen bench programs and grep, sort, wc and eza,
  built by each compiler and run on fixed inputs (a generated 100 MB text).
  Both builds must print the same output.

Each suite is summarised as the geometric mean of new/base. On two builds
of the same code, instructions are identical and cycles vary by up to ±3%
on one program and ±0.5% on the means, so instructions decide and cycles
must agree in sign. A pass is kept when:

- the run suite gains at least 1% (geometric mean), or 5% on at least two
  programs with none worse than 1%, and the compile suite costs at most
  1%; or
- the compile suite gains and the run suite is not worse.

The compile suite's total cost over `7d519d1` stays under 5%
(`perfsuite.py 7d519d1 main`). The shelved passes below were judged on the
self-build alone; `cse-experiment` and `perf12` deserve a second look under
these rules. Optimisation levels (an `-O` for the expensive passes) come
only if a pass worth having breaks the budget.

### Tried and shelved

Optimizations that worked but did not pay for themselves on the self-build.
Each is a local branch with its numbers in the commit message; none is merged.
The self-build compiles these passes too, so an analysis's own cost counts
against what its code saves.

| branch | what | code saved | analysis cost | net |
|---|---|---:|---:|---:|
| `cse-experiment` (`05023cf`) | reuse loads and checked element addresses along straight-line runs (1,063 reuses) | 1.1% | 2.8% | +2% instructions |
| `perf11` (`d79469c`) | move the field loads of a split value record (`let n = g.u.nodes[x]`) to their first use, or into the `if` branch that uses them | none (+0.1%) | 0.7% | +0.8% |
| `perf12` (`187dcef`) | read such fields again after calls that type-based write sets prove cannot change them, instead of saving them on the stack | 0.7% | 4% | +3.7% |
| `perf16` (`b88f4d3`) | the assembler remembers which register holds `[base+disp]` (4 entries, invalidated by writes, labels, calls, stores) and drops or turns into a move the loads of held places | 0.16% (7M of 4,399M; 12 KB of 2.46 MB of code) | 1.07% (47M) | +0.9% instructions, +2% cycles |
| `perf17` (`bf50b1d`) | the range facts (checks proved by guards and ranges) in functions without a loop that have a check and an `if` (2 more of fib's checks go) | below noise on the self-build (325 overflow checks made wrapping against 231); fib -5.9% instructions, -23% cycles; the other benchmarks equal | 2.75% of the native compiler's instructions (120M) | +2.8% self-build; perfsuite: compile suite +1.05% instructions, run suite -0.38% instructions, -2.25% cycles (fib alone) |
| `perf18` (not built) | interprocedural register use (callers keep values in caller-saved registers across calls to callees that do not clobber them); the callee-saved opening thresholds swept | not built: 761 of 25,358 static call sites (3%) reach a leaf function (55 of 1,241); the threshold sweep gave -0.14% (64/48) and +0.06% (96/80) instructions | | below noise |
| `perf20` (not built) | branch layout and jump threading (thread jumps to jumps, invert a jcc over a jmp, drop a jmp to the next instruction, rotate loops) | not built: the native self-build has 524,303 instructions, 47,904 jcc and 8,522 jmp; 1,019 branches go to a jmp, 98 are a jcc over a jmp, 34 a jmp to the next instruction (about 1,150 sites, 2% of branches); loops are already rotated (1,926 backward jcc against 275 backward jmp, 127 of those at a top test). Dynamic: 427M instructions, 99M branches; the ceiling is about 0.5% of instructions | | below the 1% threshold |
| `perf15` (not built) | `str` locals and parameters as register pairs, the first item of the pass | not attempted: see design/native.md section 8 | | |
| `perf21` (measured, not built) | constant folding after inlining: constant operands on both sides, `if`/`while`/`?:` on a constant condition, `switch` on a constant, a compare of a local with itself, construct-then-match on a local only ever assigned constants | not built: in the self-build's 406,000 IR nodes after inlining, 424 `E_BIN` have two constant operands (0.10%; 16 in loops), 407 conditions are constant (0.10%; 69 in loops), 338 casts of constants (x64gen takes them as immediates), 108 checked `rt_add/sub/mul/div` calls on constants, 5 self-compares, and 3 tests of a local that is only assigned constants; following single-assignment constant locals adds just 38 conditions. The perfsuite programs have 0 to 156 each (grep 156 of 81,743 nodes, sort 129 of 18,572 but 91 are its own constants) | | below 0.3% of static code at best, under 0.1% dynamic |

`perf17` fails "Judging a pass": the run suite gains in cycles but on one
program (fib), and the compile suite costs 1.05%. It is committed on its
branch with the tests (`lib/tests/run/range_facts_*`) that show the checks
stay where they must. A cheaper test for which functions to analyze (the
compiler has hundreds with a check and an `if`) would be the way to retry it.

`perf16` is the sound version of the idea behind `cse-experiment`, at the
assembler (nothing walks the IR): a reload is skipped only when the register
that loaded it, or one that stored it, still holds it. It finds little
because the generator evaluates everything into `rax`: the chain
`mov rax,[rbx]; mov rax,[rax]; mov rax,[rax+0x38]` destroys its own
intermediate values, and the next statement's `g.u.nodes.len` starts from
`rbx` again. Statically 3.5% of the 8-byte loads have a holder (a model of
the binary's basic blocks: 733 would vanish, 3,040 become register moves, and
the moves save no instruction); dynamically the self-build runs 7M fewer
instructions and the benchmarks 0 to 2% (maps -2.1%, lists -0.7%, the rest
within 0.1%). The bookkeeping is 4 to 5 instructions a write of a register
and a scan of 4 entries per load, store and move: 47M instructions in the
self-build (the budget was 10M). A cheaper table would not change the sign:
all of the saving is 0.16%. The cycle changes in the table of design/native.md
section 8 (fib -23%, wc +13%) are layout, as the instruction counts are equal.
Under "Judging a pass" (`perfsuite.py 7d519d1 perf16`) it still fails:
the compile suite costs 0.59% of its instructions, the run suite gains
0.19% (maps 2.1%, lists 0.7%, the rest under 0.2%). The run suite's cycles
gain 2.2%, but that is fib's 23% on identical instructions, which is layout.

`perf15`'s second part, the hoisting of `str` headers out of loops, was
kept: the self-build is level (0.2% fewer instructions in the native
compiler, 0.4% more in the gcc-built one's analysis), the string-scanning
benchmarks gain 4% to 8% of their cycles. Its first try gave the data
pointer full weight in the register choice and cost sipKeyedStr (the hash
behind every `Map[str, V]`) 40% of its cycles in the self-build: the
register went from a loop-carried temporary to the pointer, and the
temporary into a store-to-load forward. The second made hoisted headers
that got no register put the `rt_str_byte` calls back (x64gen's `remat`
for a place that is no plain load): it cost 1% of the self-build's
instructions in the second register choice and the walk, and was dropped
for a frame load, one load a use instead of three.

`mid-ir-m1` (2026-11-05) is stage M1 of design/mid-ir.md. It adds a mid IR,
liveness and a linear-scan allocator that decides and reports, all behind
`--native -O`. The code is unchanged: x64gen still generates it, and `-O`
builds are byte-identical. The stage fails its own gate:

- **Cost.** 638M instructions on the self-build. That is 3.02K per mid
  instruction against a target of 3K.
- **Stack accesses (10^depth weights).** The target was 30% fewer than
  x64gen. The model predicts -8.4% on the self-build, +7.5% on the compiler
  at main's sources and +13.9% on the run tests.
- **Callee-saved saves.** -22% on the self-build and -42% on the run tests.

Aggregates stay in the frame, and x64gen already avoids more of their copies
than the lowering does. Even a perfect allocator would only take the rest of
the stack traffic down by about half. Details and the M2 preconditions are
in design/mid-ir.md, "M1 as built". The default path is unchanged: perfsuite
(`0fee39d` against the branch) gives +0.03% compile-suite instructions and
identical run-suite instructions.

What they taught:

- **Fusion:** a statement placed between `bool c = ...` and its `if (c)` stops x64gen fusing the compare with the branch. That alone cost perf11's first version 0.9%.
- **Few fields move:** most fields of a split record are read on more than one path, so few loads can be sunk.
- **The hottest loads change:** x64gen writes `Node.k` and `Node.a` itself while it generates code, so the hottest field loads really can change across its calls.
- **Precise effects are expensive:** per-component call summaries make `loops.obx`'s existing hoisting do several times more work. That was two-thirds of perf12's cost.
- **The bar for a retry:** an analysis like these needs to cost under about 10M instructions (0.2%) to win. That means effect sets with constant-time deduplication, and summaries computed once.

Also tried on `grepfast` (in the library and the runtime), not committed:

| what | effect | why not |
|---|---|---|
| in the DFA's search loop, take a transition with no flag bits in a fast path before the old `< T_MATCH` test | the same instructions on `\w+`, 5% more cycles (63 ms to 67 ms) | the extra test and branch cost more than they saved; not marking start states when there is no prefilter got the same effect |
| memcpy: align the destination to 64 bytes, then `rep movsb` | no change (about 25 ms per 100 MB either way) | the slow case depends on the distance between source and destination, not on alignment |
| a line prefilter that switches itself off, like the literal ones | `-c -v '^0'` slower (69 ms to 326 ms) | once off, every byte took the slow path; the line prefilter now stays on |

## Markdown

[obx/cmd/cmark.obx](obx/cmd/cmark.obx) (the `markdown` library) against cmark
0.31.2 (`-O3` release build, cmake), rendering 10 MB documents with `--unsafe`
to a pipe. Best of 3, seconds, 5 November 2026 (`python3 obx/tests/markdown.py
--perf`):

| document | cmark | obx native | obx via C |
|---|---:|---:|---:|
| the spec, repeated to 10 MB | 0.37 | 0.59 | 0.45 |
| the repo's Markdown files, repeated to 11 MB | 0.37 | 0.67 | 0.58 |
| the spec's examples, repeated to 10 MB | 1.58 | 1.54 | 2.59 |

cmark's pathological inputs (nested emphasis, brackets and block quotes
30,000 to 100,000 deep, a million backticks, unclosed constructs) all finish
in well under a second, within a small factor of cmark; the one quadratic case
(a run of blank lines inside deeply nested lists) is cut by the same early exit
as cmark's. The parser keeps the tree in one array of 200-byte nodes: the 10 MB spec
takes 114 MB of memory at its peak (cmark: 68 MB).

## Binary sizes

| program | via C | native |
|---|---:|---:|
| empty program | 53,680 bytes (dynamic, glibc) | 12,288 bytes (static) |
| hello | 53,896 bytes | 16,416 bytes (static) |
| `import "regex"`, one call (`regex.quote`) | 54,080 bytes | 20,512 bytes |
| eza | 233,256 bytes (dynamic, glibc) | 250,344 bytes (static) |
| HTTP server (`cmd/httpd.obx`) | 316,584 bytes | 439,160 bytes (dynamic) |
| compiler | 2,000,616 bytes (dynamic, glibc) | 2,507,065 bytes (static) |

(4 November 2026, at `da520fe`; eza at `fd6f747`, the compiler on branch `perf13`.) The native backend generates only the functions and globals
reachable from `main` (design/native.md section 5, "Reachability"), as gcc
drops a C program's unused static functions: an imported module costs only
what the program uses of it. The runtime is linked the same way, by
sections, as `ld --gc-sections` would: every function and datum of rt.c is
a section of its own in `rtx.obx`, and the link keeps those reachable from
the runtime functions the program calls. Of the runtime's 62 KB of code and
read-only data, an empty program links 3.2 KB (start-up, the stack-overflow
handler, the exit path), hello 4.0 KB, eza 19 KB, the HTTP server 37 KB (the
event loop, sockets, tasks, timers) and the compiler 27 KB. The runtime's
bss is still most of the empty program's memory (135 KB of 151: the stdout
buffer and the allocator's free lists), but not of its file. The compiler itself stays the same size: what it
saves of the runtime it now carries as the runtime's section and relocation
tables. Speed did not change: the runtime's functions keep their 64-byte
alignment, and the self-build, eza `-T`, bench alloc and fib run the same
instructions in the same cycles within noise (design/native.md, section 5,
"Reachability").

A native executable carries its own runtime instead of linking libc. When a
program calls C libraries (libm, sqlite3, libpq), it is dynamically linked
against them.

## Tasks

The cost of the concurrency primitives (`lib/task.obx`, `lib/net.obx`, SPEC
16), from `obx/bench/tasks.obx`, ns per operation, best of 3 runs:

| operation | via C | native |
|---|---:|---:|
| task switch (`task.yield`) | 6.5 | 12.5 |
| start a task (stack reused) | 36 | 21 |
| start plus a wait that parks (round trip) | 95 | 75 |
| the same inside `task.within` (a timer set and removed) | 108 | 90 |
| wait on a finished task | 23 | 14 |
| promise complete | 11 | 11 |
| `Shared.with` | 3.4 | 6.4 |
| a `task.sleep(1)` that fires, beyond a yield | 46–153 | 37–74 |
| a socket read that finds data (no park) | 699 | 692 |
| a one-byte round trip between two tasks over loopback TCP | 193,000 | 183,000 |
| a `blocking` extern call run inline (no other task could run) | 3.3 | 3.5 |
| the same through the helper pool (another task is parked) | 310–590 | 640–660 |
| the same through the pool, the process on one CPU | 7,900 | 5,300 |

The task operations are far below a system call. The event loop added about
6 ns to a native park (the deadline and timer checks) and nothing measurable
to C's. The native runtime reads the clock through the vDSO, as libc does
(design/native.md, section 2): about 18 ns a read, against 240 ns for the
`clock_gettime` system call it made until 2026-11-03, when the timed wait
was 339 ns natively; the batched rows (start, wait on a finished task,
promise complete) also lost the two system calls per 50 operations of the
benchmark's own clock reads, 4 to 5 ns each. The `sleep` row is noisy:
the 1 ms sleep's overshoot is divided among 10,000 tasks (natively from
−220 to 286 over 9 runs, C from −105 to 134). The round trip is
this machine's loopback: a Python ping-pong over loopback TCP takes
190,000 ns with two blocking threads and 205,000 ns with epoll on one
thread. The native switch is slower than C's because it goes through more
of the generic task code; the fifth pass's inlining took it from 17.6 to
12.8 ns (12.5 to 14.5 from run to run), and it has not been tuned otherwise.

The blocking rows call C's `abs` through a `blocking` extern (SPEC 12).
Inline it is a plain extern call plus a test of the scheduler's state. Through
the pool, the task parks, a helper thread runs the call and the event loop
wakes the task: the helpers and the event loop each spin up to 50 µs before
they sleep, so a busy pool round trip needs no system call. When the
process may use only one CPU (`taskset -c 0`) the spinning is off and every
call costs an `eventfd` write and an `epoll_wait`. For scale: one
`sqlite3_step` over a cached table takes 30 ns, so SQLite's steps in a
program with other tasks cost 10 to 20 times more (0.4 to 0.8 µs); a
query that reads the disk does not notice.

## HTTP server

`obx/cmd/httpd.obx` on the server library `obx/lib/httpserver.obx`
(design/http-server.md, stage S1): a keep-alive HTTP/1.1 server on one
thread, one task per connection, with every limit and timeout of the design
on, the hangup cancellation armed around each handler, the request parsed
strictly and the reply built by the library. Measured on 4 November 2026 by
`python3 obx/cmd/http_gate.py --servers native,c,go,node`: the server on
CPU 0, the load client `obx/cmd/httpload.obx` (native, one thread) on CPU 1.
10,000 idle keep-alive connections are opened first and stay open during
the load; each load client sends `GET /` and waits for the reply, 5 s per
row. Go 1.26's net/http (GOMAXPROCS=1) and Node 24's http serve the same
reply, as references. This machine's numbers move by 5 to 10 % from one run
to the next.

| | obx native | obx via C | Go net/http | Node 24 |
|---|---:|---:|---:|---:|
| RSS per idle connection | 15.5 KB | 14.9 KB | 19.8 KB | 10.3 KB |
| RSS with 10,000 idle connections | 155 MiB | 148 MiB | 195 MiB | 147 MiB |
| 1 client: requests/s | 4,140 | 4,470 | 4,070 | 3,850 |
| 10 clients: requests/s | 36,500 | 37,600 | 35,200 | 33,700 |
| 100 clients: requests/s | 62,800 | 66,300 | 44,400 | 43,800 |
| 1 client: latency p50 / p99 | 224 / 442 µs | 215 / 380 µs | 237 / 380 µs | 246 / 421 µs |
| 10 clients: latency p50 / p99 | 261 / 418 µs | 257 / 398 µs | 268 / 461 µs | 285 / 467 µs |
| 100 clients: latency p50 / p99 | 1.56 / 2.85 ms | 1.48 / 2.78 ms | 2.13 / 6.56 ms | 2.09 / 4.75 ms |

What a request costs the server beyond reading and writing the socket: the
router's lookup (the router is a `task.Frozen`, read by every connection's
task at once), the strict head parse (every field checked), a `poll` system
call when the hangup cancellation is armed (it checks for a half-close that
came with the request, which must not cancel it), the `Date` and
`X-Content-Type-Options` headers and the counters. With the `requestId` and
`accessLog` middleware on (the log written to `/dev/null`) the server gave
about 10 % fewer requests per second at 100 clients (S1's measurement).

An idle connection's resident cost is two pages of its task's stack (8 KiB;
the stack is 256 KiB of address space), a 4 KiB read buffer, a 512-byte
output buffer, and the request and response records with their header
lists, kept for reuse. Under the gate's `OBX_CHECK=1`, `GET /shutdown` with
the 10,000 connections open drains them all and ends the program in 0.5 s,
exit 0, no leak. One client is bound by this machine's loopback round trip
(above, about 210 µs); with 100 clients the single-threaded load client may
be the limit as much as the server. The `ulimit -n` here is 1,048,576; the
gate raises the soft limit when it can and stops when it cannot.

### The HTTP client

`httpload --client` sends the same load through one `http.Client`
(`obx/lib/http.obx`: `get` and `text()` per request, `maxPerHost` equal to
the connections), against the native server on CPU 0, the loader on CPU 1,
5 s per row, the median of three alternating rounds, beside httpload's own
loop (a fixed request, only Content-Length parsed). No idle connections
are held open here, so the raw numbers differ a little from the table
above.

| connections | raw, native | client, native | raw, via C | client, via C |
|---:|---:|---:|---:|---:|
| 1: requests/s | 4,430 | 4,390 | 4,560 | 4,380 |
| 100: requests/s | 66,200 | 59,000 | 65,200 | 61,000 |
| 1,000: requests/s | 56,000 | 48,600 | 53,400 | 49,500 |
| 1: latency p50 / p99 | 214 / 401 µs | 218 / 406 µs | 212 / 378 µs | 217 / 402 µs |
| 100: latency p50 / p99 | 1.49 / 2.11 ms | 1.69 / 2.53 ms | 1.50 / 2.24 ms | 1.60 / 2.19 ms |
| 1,000: latency p50 / p99 | 17.6 / 22.1 ms | 19.9 / 24.3 ms | 18.2 / 23.0 ms | 19.5 / 24.1 ms |

The client does more work per request than the raw loop: it builds a full
request, parses every reply header, works out the framing and goes through
the pool. With the loader's core full, that is 87 % to 94 % of the raw
throughput from 100 connections on; with one connection the loopback round
trip is the limit and they are level. The design's gate asked for parity,
so it is not met (design/http-client.md, section 8).

### With a Postgres pool

`python3 obx/cmd/http_gate.py --db 4,16`: the same server and load, each
request `GET /db`, which takes a connection from a `postgres.Pool` of 4 or
16 connections, runs `SELECT $1::int8 + 1` with the request's number, and
returns the connection. PostgreSQL 16 on the same machine, over its Unix
socket; the queries go through libpq's asynchronous API, so each request's
task parks on the socket and the server's one thread serves the 10,000 idle
connections and the load meanwhile. No Go or Node reference: neither has a
Postgres driver installed here.

| | native, pool 4 | via C, pool 4 | native, pool 16 | via C, pool 16 |
|---|---:|---:|---:|---:|
| 1 client: requests/s | 3,050 | 3,050 | 2,930 | 2,720 |
| 10 clients: requests/s | 21,600 | 23,300 | 19,900 | 19,400 |
| 100 clients: requests/s | 22,000 | 22,700 | 22,600 | 23,700 |
| 1 client: latency p50 / p99 | 315 / 541 µs | 316 / 514 µs | 322 / 598 µs | 344 / 655 µs |
| 10 clients: latency p50 / p99 | 451 / 666 µs | 422 / 593 µs | 499 / 704 µs | 506 / 806 µs |
| 100 clients: latency p50 / p99 | 4.39 / 6.34 ms | 4.32 / 5.78 ms | 4.25 / 6.88 ms | 4.11 / 5.91 ms |

One client pays two round trips, the HTTP one and the server's to
PostgreSQL (about 100 µs: a lone connection does 11,000 queries a second).
From 10 clients on, the server's one thread is the limit: each request is
about 40 µs of it, mostly system calls (the HTTP read and write, and
libpq's send, poll and receive on the database socket). With 100 clients,
90 of them wait in the pool's queue, which is where the 4 ms comes from. A
pool's waiting queue is first come, first served. After the load,
`/shutdown` with the 10,000 idle connections open takes about 0.5 s, exit
0, no leak under `OBX_CHECK=1`, every pool connection idle and nobody
waiting.

The other way to run libpq, `PQexecParams` as a `blocking` extern on the
helper pool, was measured without HTTP (tasks each with their own
connection, queries per second, either backend):

| tasks | asynchronous (the library) | `blocking`, 4 helpers | `blocking`, 16 helpers |
|---:|---:|---:|---:|
| 1 | 11,200 | 11,300 | |
| 4 | 40,000 | 32,000 | 38,000 |
| 16 | 40,000 | 32,000 | 79,000 |

The asynchronous path is bound by the program's one thread (16 tasks: 0.14
s user and 0.58 s system time of 0.74 s); the helpers make their system
calls on other cores. The library keeps the asynchronous API: it needs no
thread per running query, a waiting query can be cancelled (`PQcancel`)
when its task is, and its cost is the one thread's, the same budget the
HTTP side uses; more cores come from processes.

### With worker processes

`python3 obx/cmd/http_gate.py --workers 1,2,4,8 --db 4`: `httpd ADDR POOL
WORKERS`, n worker processes under `task.workers` sharing one listener
made before the fork, each with its own event loop and (for `/db`) its own
pool of 4 PostgreSQL connections. No idle connections this time; 256
keep-alive load connections spread over 8 load processes (`httpload -p 8`),
5 s per row, `OBX_CHECK=1`. Pinning with `taskset`: the n workers on the
first n of CPUs 0, 2, 4, 6, 1, 3, 5, 7 (this Ryzen's CPUs 2k and 2k+1 are
the two hardware threads of core k, so 1 to 4 workers each have a core of
their own and 8 workers share four cores with SMT), the load on CPUs 8-15
(the other four cores). PostgreSQL is not pinned. Requests/s, then latency
p50 / p99 in ms:

| workers | native `/` | via C `/` | native `/db` | via C `/db` |
|---:|---:|---:|---:|---:|
| 1 | 58,800; 4.28 / 6.44 | 57,400; 4.38 / 6.63 | 18,700; 13.4 / 17.5 | 19,900; 12.3 / 15.3 |
| 2 | 95,500; 2.56 / 5.15 | 96,000; 2.52 / 6.07 | 33,000; 7.64 / 10.3 | 34,400; 7.31 / 10.2 |
| 4 | 128,200; 1.71 / 5.85 | 116,200; 1.87 / 6.58 | 49,700; 4.88 / 11.5 | 50,300; 4.72 / 12.0 |
| 8 | 123,500; 1.68 / 7.01 | 129,600; 1.60 / 6.60 | 63,500; 3.77 / 9.90 | 66,000; 3.83 / 9.72 |

`/` scales about 1.6 times at 2 workers and 2.0 to 2.2 at 4, then stops:
the ceiling is the machine's, the loopback TCP of both sides and the WSL2
virtual machine's cross-CPU wake-ups, not the workers (at 4 workers neither
side is busy all the time). `/db` keeps scaling to 8 workers (3.3 to 3.4
times): each request is mostly waiting for PostgreSQL, whose backends run
on whatever CPUs are free, and 8 workers keep 32 queries in flight. A
listener of its own in each worker (`httpd ... WORKERS reuseport`,
`SO_REUSEPORT`) is the other mode; the shared listener is the default
because connections waiting in a killed worker's own accept queue are reset
with its socket.

The kill test (4 workers, `/`, the load reconnecting after an error): a
third of the way in, `SIGKILL` to one worker. Natively the worker held 45
connections and 45 requests were lost (via C 112 and 112): exactly the ones
in flight on its connections, one each. The supervisor started it again
after 109 ms (the 100 ms backoff; `http.workerPolicy`: a crash after the
worker is ready never counts toward giving up), it accepted from the same
queue, and the run kept 124,000 req/s natively (129,700 via C) over the 5 s.
The `/shutdown` that follows ends every worker drained, exit 0, the restart
counted.

The differential parsing test (`python3 obx/tests/httpsrvgen.py -n
100000`, 16 threads) sends 100,000 generated request streams to both obx
builds and Go, and parses them with llhttp, in about 160 s.

## TLS

`obx/lib/tls.obx` over OpenSSL 3.0.13 (design/tls.md), and https in the
http client. Measured on 5 November 2026 by `python3 obx/cmd/tls_bench.py`.

Setup:
- `obx/cmd/tlsbench.obx`, built natively and via C, against OpenSSL's own
  clients;
- the same local servers for both, the server on CPU 0 and the client on
  CPU 2;
- a P-256 ECDSA certificate from a test CA, verified by every client;
  TLS 1.3, TLS_AES_256_GCM_SHA384, X25519;
- each row the median of three runs.

Workloads:
- **Handshakes:** full handshakes one after another (connect, handshake,
  close_notify, no resumption) against `openssl s_server -www`.
- **Bulk:** one GET of 1 GiB from `tlsbench serve` (an https server written
  with `tls`, native), its body streamed in 64 KiB reads.
- **Requests:** 20,000 small GETs over kept-alive connections to the same
  server (5,000 for curl, one URL list).

| | obx native | obx via C | OpenSSL / curl |
|---|---:|---:|---:|
| handshakes/s (s_server is the limit) | 722 | 723 | 522 (`openssl s_time -new`) |
| client CPU per handshake | 516 µs | 515 µs | 590 µs (`s_time`) |
| GET 1 GiB: MB/s | 1,866 | 1,934 | 1,788 (curl) |
| GET 1 GiB: client CPU | 0.41 s | 0.43 s | 0.47 s (curl) |
| requests/s, 1 connection | 3,240 | 4,030 | 2,900 (curl) |
| requests/s, 10 connections | 28,400 | 28,000 | |

**Handshakes.** A handshake's client side costs what OpenSSL's own client
costs. Most of the 0.5 ms is in OpenSSL 3.0:
- the per-connection `SSL` setup (provider lookups);
- verifying the ECDSA chain;
- the X25519 exchange.

The engine's memory BIOs and the parking add nothing measurable. The rate
is bound by s_server, which handshakes one connection at a time. s_time
gets fewer per second against the same server, and spends more client CPU
on each.

**Bulk.** Bulk throughput is decryption: AES-GCM with AES-NI, 0.4 s of
client CPU for 1 GiB. The engine's extra copy (socket to BIO to the
caller's buffer) shows as a little less CPU than curl's, not more.

**Requests.** The one-connection row is the loopback round trip, and it
moves by 15 % from run to run (3,240 to 3,550 natively in other runs). For
scale, plain http to `httpd` reaches about 4,400 per second on one
connection (above).

These runs are also the H2 gate's "1,000 https requests over kept-alive
connections" (design/http-client.md): 20,000 per row, on 1 and 10
connections, under no `OBX_CHECK`. The tests run under `OBX_CHECK=1` with
no leak.

## Reproducing

```sh
obx/compiler/build.sh                                  # out/obxc from the seed
python3 obx/bench/bench.py -n 11
python3 obx/cmd/eza_gate.py -j 12                      # eza correctness on both builds
obx/compiler/out/obxc obx/cmd/eza.obx --native -o eza  # and without --native for the C build
obx/tools/native_selfbuild.sh                          # the natively built compiler
obx/compiler/out/obxc obx/bench/tasks.obx --native -o tasks && ./tasks
python3 obx/cmd/http_gate.py                           # the server table (about 2 minutes)
python3 obx/cmd/http_gate.py --servers native,c --db 4,16   # with Postgres ($OBX_PG_DSN)
python3 obx/cmd/http_gate.py --workers 1,2,4,8 --db 4   # worker processes (about 3 minutes)
python3 obx/tests/httpsrvgen.py -n 100000                              # the server's parsing against Go and llhttp
python3 obx/cmd/tls_bench.py                           # TLS handshakes, bulk and https requests (about 2 minutes)
```

## For the record: obo

obo 0.19, obx's predecessor, was retired on 2026-11-02 and is archived in
[archive/obo/](archive/obo/README.md) with its own
[performance record](archive/obo/PERFORMANCE.md). On the same host and day,
obx was within a small factor of it everywhere and well ahead on allocation
and long eza listings:

| | obo 0.19 | obx via C | obx native |
|---|---:|---:|---:|
| bench fib | 452 ms | 331 ms | 330 ms |
| bench alloc | 483 ms | 121 ms | 63 ms |
| bench loop | 208 ms | 264 ms | 420 ms |
| eza `-T -l /usr/share` | 418 ms | 189 ms | 205 ms |
| compiler self-build | 223 ms | 252 ms | 406 ms |

The fib row's obx figures are from `2ce4860`; the rest are from the day of
the comparison. `python3 obx/bench/bench.py --obo` adds obo's column to the benchmarks
(build `archive/obo/obo` first with `make -C archive/obo`).
