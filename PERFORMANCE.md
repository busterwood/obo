# obx performance

obx builds a program in two ways:

- **native**: `obxc --native`, the compiler's own x86-64 backend. It writes an
  ELF executable directly, with no gcc and, unless the program calls C
  libraries, no libc.
- **via C**: `obxc`, which writes C and runs `gcc -O2`.

Both are built from the same IR and must behave identically; the gate checks
that on 1,616 programs. This page measures what each costs. Measured on 3
November 2026 at `2ce4860`, after the fifth native performance pass
(inlining in the IR) and concurrency stage C1b (sockets and timers), on a
quiet machine; the HTTP server table is from C1b's own run. The loop and
array rows, the compiler's `--native` cost and the sixth pass's row are from
the sixth native performance pass (loops; branch `perf6`), measured the same
day; the blocking-call rows and the server-with-Postgres table are from
concurrency stage C1c (branch `c1c`), the sieve row and the seventh
pass's row from the seventh (branch `sieve`), and the worker-process
tables from concurrency stage C2 (branch `c2`), measured the same day. The
eighth pass's row is from that pass (branch `perf8`), the regex table from
regex phase R2 (branch `r2`), both on 3 November; the compiler table and
the binary sizes were measured again on 4 November. The numbers are
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

## The compiler

The obx compiler compiling itself (`obx/compiler/main.obx`), built each way:

| | gcc-built | native-built |
|---|---:|---:|
| self-build to an executable (`--native`) | 493 ms | 589 ms |
| self-build to C (`--emit-c`) | 274 ms | 310 ms |
| full build through gcc | 15 s, nearly all of it gcc | — |
| compiler binary | 1,938,784 bytes (dynamic, glibc) | 2,711,761 bytes (static, no libc) |

Measured on 4 November at `4cf3de5` (after the eighth pass, reachability,
the runtime linked by sections and regex phase R2; after the sixth pass the
times were 407, 490, 242 and 267 ms on 2,500 fewer lines of compiler). The
source is 23,437 lines of obx and 4,756 lines of C runtime (the C backend's
`rt.h` and the native backend's freestanding `rt.c`, each with task stacks
and switching, the event loop, timers, sockets and the SSE2 string
searches), not counting `rt.obx` and `rtx.obx`, which are generated from
them.

- The native-built compiler is 1.1-1.2x slower than the gcc-built one
  (1.55x before the fifth pass). It runs 1.48x the instructions (5,763M
  against 3,905M for `--native`; 1.51x after the sixth pass, 1.89x before
  the fifth) at a higher rate per cycle.
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
second register choice and the sites).

The earlier steps and the per-commit figures are in
[obx/design/native.md](obx/design/native.md) sections 4 and 8. What remains
is mostly work the code itself asks for (allocation, string concatenation,
reference counts, copies of large value records), not register use.

## Binary sizes

| program | via C | native |
|---|---:|---:|
| empty program | 53,680 bytes (dynamic, glibc) | 12,288 bytes (static) |
| hello | 53,896 bytes | 16,416 bytes (static) |
| `import "regex"`, one call (`regex.quote`) | 54,080 bytes | 20,512 bytes |
| eza | 233,256 bytes (dynamic, glibc) | 270,824 bytes (static) |
| HTTP server (`cmd/httpd.obx`) | 316,584 bytes | 439,160 bytes (dynamic) |
| compiler | 1,963,360 bytes (dynamic, glibc) | 2,711,761 bytes (static) |

(4 November 2026, at `da520fe`.) The native backend generates only the functions and globals
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

The tables in this section up to "On the server library" measured
`obx/cmd/httpd.obx` as it was before stage S1, a hand-written server. Since
S1 httpd is built on `obx/lib/httpserver.obx`; its numbers are in [On the
server library (S1)](#on-the-server-library-s1) below.

`obx/cmd/httpd.obx`, a keep-alive HTTP/1.1 server on one thread (one task
per connection; several processes with worker processes, below), measured by `python3 obx/cmd/http_gate.py`: the server on
CPU 0, the load client `obx/cmd/httpload.obx` (native, one thread) on CPU 1.
10,000 idle keep-alive connections are opened first and stay open during
the load; each load client sends `GET /` and waits for the reply, 5 s per
row. Go 1.26's net/http (GOMAXPROCS=1) and Node 24's http serve the same
reply, as references.

| | obx native | obx via C | Go net/http | Node 24 |
|---|---:|---:|---:|---:|
| RSS per idle connection | 13.0 KB | 12.7 KB | 19.8 KB | 9.3 KB |
| RSS with 10,000 idle connections | 124 MiB | 122 MiB | 195 MiB | 138 MiB |
| 1 client: requests/s | 4,890 | 4,900 | 3,430 | 4,040 |
| 10 clients: requests/s | 38,900 | 38,000 | 34,200 | 32,400 |
| 100 clients: requests/s | 69,400 | 65,800 | 46,200 | 42,900 |
| 1 client: latency p50 / p99 | 194 / 328 µs | 196 / 353 µs | 260 / 614 µs | 233 / 439 µs |
| 10 clients: latency p50 / p99 | 252 / 371 µs | 256 / 397 µs | 278 / 473 µs | 295 / 515 µs |
| 100 clients: latency p50 / p99 | 1.44 / 1.90 ms | 1.52 / 2.05 ms | 2.05 / 6.87 ms | 2.18 / 4.96 ms |

An obx connection's resident cost is two pages of its task's stack (8 KiB;
the stack is 256 KiB of address space), the server's 4 KiB read buffer and
the task itself; with a 512-byte buffer it is 9.1 KB. Under the gate's
`OBX_CHECK=1`, `GET /shutdown` with the 10,000 connections open cancels
them all and ends the program in 0.19 s, exit 0, no leak. One client is
bound by this machine's loopback round trip (above, about 190 µs); with 100
clients the single-threaded load client may be the limit as much as the
server. The `ulimit -n` here is 1,048,576; the gate raises the soft limit
when it can and stops when it cannot.

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
| 1: requests/s | 4,320 | 4,530 | 4,760 | 4,410 |
| 100: requests/s | 69,700 | 59,900 | 69,700 | 65,400 |
| 1,000: requests/s | 62,000 | 53,600 | 62,300 | 51,300 |
| 1: latency p50 / p99 | 227 / 325 µs | 217 / 320 µs | 205 / 332 µs | 222 / 362 µs |
| 100: latency p50 / p99 | 1.43 / 1.90 ms | 1.70 / 2.20 ms | 1.43 / 1.85 ms | 1.53 / 2.10 ms |
| 1,000: latency p50 / p99 | 16.2 / 21.2 ms | 18.4 / 25.2 ms | 16.0 / 20.2 ms | 19.6 / 23.0 ms |

The client does about 7 µs of user time per request against the raw
loop's 3.3 µs (system time is the same, about 12 µs): it builds a full
request, parses every reply header, works out the framing and goes through
the pool. With the loader's core full, that is 82% to 94% of the raw
throughput from 100 connections on; with one connection the loopback round
trip is the limit and they are level. The design's gate asked for parity,
so it is not met (design/http-client.md, section 8).

### With a Postgres pool

The C1c gate, `python3 obx/cmd/http_gate.py --db 4,16`: the same server
and load, each request `GET /db`, which takes a connection from a
`postgres.Pool` of 4 or 16 connections, runs `SELECT $1::int8 + 1` with the
request's number, and returns the connection. PostgreSQL 16 on the same
machine, over its Unix socket; the queries go through libpq's asynchronous
API, so each request's task parks on the socket and the server's one thread
serves the 10,000 idle connections and the load meanwhile. No Go or Node
reference: neither has a Postgres driver installed here.

| | native, pool 4 | via C, pool 4 | native, pool 16 | via C, pool 16 |
|---|---:|---:|---:|---:|
| RSS per idle connection | 13.0 KB | 12.7 KB | 13.0 KB | 12.5 KB |
| 1 client: requests/s | 2,790 | 3,300 | 2,850 | 2,530 |
| 10 clients: requests/s | 22,500 | 23,100 | 20,800 | 21,200 |
| 100 clients: requests/s | 23,100 | 23,200 | 25,000 | 27,200 |
| 1 client: latency p50 / p99 | 339 / 575 µs | 287 / 475 µs | 337 / 536 µs | 347 / 1,061 µs |
| 10 clients: latency p50 / p99 | 437 / 615 µs | 425 / 623 µs | 481 / 636 µs | 471 / 636 µs |
| 100 clients: latency p50 / p99 | 4.27 / 5.81 ms | 4.25 / 5.84 ms | 3.86 / 5.84 ms | 3.63 / 5.22 ms |

One client pays two round trips, the HTTP one and the server's to
PostgreSQL (about 100 µs: a lone connection does 11,000 queries a second).
From 10 clients on, the server's one thread is the limit: each request is
about 40 µs of it, mostly system calls (the HTTP read and write, and
libpq's send, poll and receive on the database socket). With 100 clients,
90 of them wait in the pool's queue, which is where the 4 ms comes from;
16 connections help a little because more queries are in flight per pass of
the event loop. A pool's waiting queue is first come, first served. After
the load, `/shutdown` with the 10,000 idle connections open takes 0.18 to
0.23 s, exit 0, no leak under `OBX_CHECK=1`, every pool connection idle and
nobody waiting.

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
HTTP side uses; more cores come from processes (stage C2).

### With worker processes

The C2 gate, `python3 obx/cmd/http_gate.py --workers 1,2,4,8 --db 4
--listen both`: `httpd ADDR POOL WORKERS MODE`, n worker processes under
`task.workers`, each with its own event loop and (for `/db`) its own pool
of 4 PostgreSQL connections. No idle connections this time; 256 keep-alive
load connections spread over 8 load processes (`httpload -p 8`), 5 s per
row, `OBX_CHECK=1`. Pinning with `taskset`: the n workers on the first n of
CPUs 0, 2, 4, 6, 1, 3, 5, 7 (this Ryzen's CPUs 2k and 2k+1 are the two
hardware threads of core k, so 1 to 4 workers each have a core of their
own and 8 workers share four cores with SMT), the load on CPUs 8-15 (the
other four cores). PostgreSQL is not pinned. Requests/s, then latency p50 /
p99 in ms, the default shared listener:

| workers | native `/` | via C `/` | native `/db` | via C `/db` |
|---:|---:|---:|---:|---:|
| 1 | 72,200; 3.46 / 4.89 | 64,200; 4.06 / 5.75 | 22,500; 11.0 / 14.1 | 22,300; 11.1 / 14.4 |
| 2 | 115,500; 2.05 / 4.70 | 113,800; 2.10 / 4.77 | 36,800; 3.42 / 13.4 | 36,200; 6.88 / 8.86 |
| 4 | 134,900; 1.64 / 5.43 | 135,100; 1.65 / 5.38 | 51,100; 4.33 / 10.5 | 50,600; 4.13 / 11.8 |
| 8 | 133,200; 1.58 / 6.13 | 134,100; 1.55 / 6.20 | 71,300; 3.37 / 8.22 | 68,100; 3.53 / 8.79 |

`/` scales 1.6 to 1.8 times at 2 workers and about 1.9 to 2.1 at 4, then
stops: at 4 workers neither side is busy all the time (workers 67 to 91 %
of a CPU, each load process about 52 %, sampled from `/proc`), and the
ceiling is the machine's, the loopback TCP of both sides and the WSL2
virtual machine's cross-CPU wake-ups. With 1,024 connections instead of 256
(more requests per wake-up) native gives 59,800, 99,900, 139,300 and
153,900 req/s for 1, 2, 4 and 8 workers, with all 16 hardware threads 80 to
95 % busy. `/db` keeps scaling to 8 workers (3.1 to 3.2 times): each
request is mostly waiting for PostgreSQL, whose backends run on whatever
CPUs are free, and 8 workers keep 32 queries in flight.

The other listener, `SO_REUSEPORT` (a socket per worker, `httpd ... WORKERS
reuseport`), native: 65,100, 112,700, 134,400, 135,200 req/s on `/` and
22,100, 36,300, 50,700, 70,900 on `/db` for 1, 2, 4 and 8 workers; via C
within 4 % of that. The same rates. It spreads the keep-alive connections
evenly (accepted per worker at 8 workers: 22 to 47, against 18 to 58 shared).

The kill test (4 workers, `/`, the load reconnecting after an error): a
third of the way in, `SIGKILL` to one worker.

| | native, shared | via C, shared | native, reuseport | via C, reuseport |
|---|---:|---:|---:|---:|
| the worker's connections | 36 | 44 | 55 | 59 |
| requests lost | 36 | 44 | 59 | 65 |
| started again after | 105 ms | 108 ms | 111 ms | 113 ms |
| req/s over the 5 s | 132,400 | 129,300 | 131,700 | 129,500 |

With the shared listener the lost requests are exactly the ones in flight
on the killed worker's connections (one each); the supervisor starts it
again after the 100 ms backoff, it accepts from the same queue, and the
`/shutdown` that follows ends every worker drained, exit 0, the restart
counted. With `SO_REUSEPORT` a few more are lost: connections waiting in
the killed worker's own accept queue are reset with its socket. That and
port 0 are why httpd shares one listener by default.

### On the server library (S1)

`cmd/httpd.obx` on `lib/httpserver.obx` (design/http-server.md, stage S1):
the same routes, now through the router, with every limit and timeout of
the design on, the hangup cancellation armed around each handler, the
request parsed strictly and the reply built by the library. The same gate
as the first table (`python3 obx/cmd/http_gate.py --servers
native,c,go,node`: 10,000 idle connections, the server on CPU 0, the load
on CPU 1, 5 s per row), measured 2026-11-03:

| | obx native | obx via C | Go net/http | Node 24 |
|---|---:|---:|---:|---:|
| RSS per idle connection | 15.5 KB | 14.9 KB | 19.9 KB | 9.3 KB |
| RSS with 10,000 idle connections | 155 MiB | 148 MiB | 197 MiB | 138 MiB |
| 1 client: requests/s | 4,490 | 4,360 | 4,110 | 3,990 |
| 10 clients: requests/s | 36,500 | 36,800 | 32,800 | 26,700 |
| 100 clients: requests/s | 61,600 | 61,700 | 45,200 | 29,100 |
| 1 client: latency p50 / p99 | 213 / 362 µs | 223 / 374 µs | 235 / 359 µs | 224 / 510 µs |
| 10 clients: latency p50 / p99 | 267 / 388 µs | 265 / 394 µs | 288 / 509 µs | 349 / 687 µs |
| 100 clients: latency p50 / p99 | 1.59 / 2.52 ms | 1.59 / 2.47 ms | 2.06 / 6.87 ms | 3.19 / 7.46 ms |

This machine's numbers move by 10 % or more from one run to the next, so
the old and the new httpd (both native, no idle connections, 100 clients,
4 s) were also run in turn, seven times each. The medians: the old one
69,200 req/s, p50 1.45 ms, p99 1.88 ms; the new one 64,300 req/s (-7.1 %),
p50 1.53 ms, p99 2.36 ms. The design's target was 60,000 req/s, or within
15 % of the old server. With the `requestId` and `accessLog` middleware on
(the log written to `/dev/null`), the median of five runs was 58,300 req/s,
p50 1.67 ms, p99 2.66 ms, against 65,000 without them in the same runs;
the target there was 50,000. What the library adds per request (not timed one
by one): the router's lookup inside a `Shared.with`, the strict head parse
(every field checked), a `poll` system call when the hangup cancellation
is armed (it checks for a half-close that came with the request, which
must not cancel it), the `Date` and `X-Content-Type-Options` headers and
the counters. An idle connection costs 15.5 KB against 13.0: the request
and response records with their header lists, kept for reuse, and a
512-byte output buffer (4 KiB at first, which made 19.2 KB). After `GET
/shutdown` the drain closes the 10,000 idle connections and the program
ends in 0.5 to 0.6 s, exit 0, no leak.

With a Postgres pool (`--db 4,16`, `GET /db`, as in the C1c table):

| | native, pool 4 | via C, pool 4 | native, pool 16 | via C, pool 16 |
|---|---:|---:|---:|---:|
| 1 client: requests/s | 2,930 | 2,910 | 2,880 | 3,000 |
| 10 clients: requests/s | 21,900 | 22,100 | 19,200 | 19,500 |
| 100 clients: requests/s | 21,700 | 21,700 | 24,300 | 24,400 |
| 1 client: latency p50 / p99 | 332 / 523 µs | 334 / 534 µs | 336 / 525 µs | 321 / 511 µs |
| 10 clients: latency p50 / p99 | 447 / 634 µs | 445 / 623 µs | 521 / 668 µs | 512 / 661 µs |
| 100 clients: latency p50 / p99 | 4.53 / 6.29 ms | 4.51 / 6.33 ms | 4.06 / 5.58 ms | 4.02 / 5.53 ms |

The database's round trip dominates; these are within the C1c table's
spread. With worker processes (`--workers 1,2,4,8 --db 4`, native, the
shared listener, 256 connections): `/` 61,500, 96,100, 127,100 and 128,400
req/s for 1, 2, 4 and 8 workers; `/db` 19,800, 33,500, 49,100 and 63,000.
The kill test lost the 80 requests in flight on the killed worker's 80
connections, and the worker was started again after 104 ms
(`http.workerPolicy`: a crash after the worker is ready never counts
toward giving up).

The differential parsing test (`python3 obx/tests/httpsrvgen.py -n
100000`, 16 threads) sends 100,000 generated request streams to both obx
builds and Go, and parses them with llhttp, in about 160 s.

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
python3 obx/cmd/http_gate.py --workers 1,2,4,8 --db 4 --listen both   # worker processes (about 5 minutes)
python3 obx/tests/httpsrvgen.py -n 100000                              # the server's parsing against Go and llhttp
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
