# obx performance

obx builds a program in two ways:

- **native**: `obxc --native`, the compiler's own x86-64 backend. It writes an
  ELF executable directly, with no gcc and, unless the program calls C
  libraries, no libc.
- **via C**: `obxc`, which writes C and runs `gcc -O2`.

Both are built from the same IR and must behave identically; the gate checks
that on 1,540 programs. This page measures what each costs. Measured on 3
November 2026 at `2ce4860`, after the fifth native performance pass
(inlining in the IR) and concurrency stage C1b (sockets and timers), on a
quiet machine; the HTTP server table is from C1b's own run. The loop and
array rows, the compiler's `--native` cost and the sixth pass's row are from
the sixth native performance pass (loops; branch `perf6`), measured the same
day; the sieve row and the seventh pass's row from the seventh (branch
`sieve`). The numbers are
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
| self-build to an executable (`--native`) | 407 ms | 490 ms |
| self-build to C (`--emit-c`) | 242 ms | 267 ms |
| full build through gcc | 15 s, nearly all of it gcc | — |
| compiler binary | 1,673,896 bytes (dynamic, glibc) | 2,549,321 bytes (static, no libc) |

Measured after the sixth pass merged (`perf6`). The source is 21,405 lines of obx and 3,085 lines of C runtime (the C
backend's `rt.h` and the native backend's freestanding `rt.c`, each with
task stacks and switching, the event loop, timers and sockets).

- The native-built compiler is 1.1-1.2x slower than the gcc-built one
  (1.55x before the fifth pass). It runs 1.51x the instructions (4,644M
  against 3,069M for `--native`; 1.89x before the fifth pass) at a higher
  rate per cycle.
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

The earlier steps and the per-commit figures are in
[obx/design/native.md](obx/design/native.md) sections 4 and 8. What remains
is mostly work the code itself asks for (allocation, string concatenation,
reference counts, copies of large value records), not register use.

## Binary sizes

| program | via C | native |
|---|---:|---:|
| empty program | 42,760 bytes (dynamic, glibc) | 61,440 bytes (static) |
| eza | 216,880 bytes (dynamic, glibc) | 303,040 bytes (static) |
| compiler | 1,673,896 bytes (dynamic, glibc) | 2,549,321 bytes (static) |

Every program carries the whole runtime, now including the event loop,
timers and sockets (C1b) and the 64-byte function alignment: the empty
program grew by 18 KB via C and 12 KB native on 3 November. Leaving out
the parts a program does not use is not done yet.

A native executable carries its own runtime instead of linking libc. When a
program calls C libraries (libm, sqlite3, libpq), it is dynamically linked
against them.

## Tasks

The cost of the concurrency primitives (`lib/task.obx`, `lib/net.obx`, SPEC
16), from `obx/bench/tasks.obx`, ns per operation, best of 3 runs:

| operation | via C | native |
|---|---:|---:|
| task switch (`task.yield`) | 6.5 | 12.8 |
| start a task (stack reused) | 36 | 26 |
| start plus a wait that parks (round trip) | 95 | 78 |
| the same inside `task.within` (a timer set and removed) | 108 | 339 |
| wait on a finished task | 23 | 19 |
| promise complete | 11 | 16 |
| `Shared.with` | 3.4 | 6.4 |
| a `task.sleep(1)` that fires, beyond a yield | 46–153 | 131–321 |
| a socket read that finds data (no park) | 699 | 683 |
| a one-byte round trip between two tasks over loopback TCP | 193,000 | 171,000 |

The task operations are far below a system call. The event loop added about
6 ns to a native park (the deadline and timer checks) and nothing measurable
to C's. The native runtime has no vDSO (it is static and makes raw system
calls), so the `clock_gettime` behind every timer is a real system call of
about 250 ns: native timers cost that much more. The `sleep` row is noisy:
the 1 ms sleep's overshoot is divided among 10,000 tasks. The round trip is
this machine's loopback: a Python ping-pong over loopback TCP takes
190,000 ns with two blocking threads and 205,000 ns with epoll on one
thread. The native switch is slower than C's because it goes through more
of the generic task code; the fifth pass's inlining took it from 17.6 to
12.8 ns, and it has not been tuned otherwise.

## HTTP server

`obx/cmd/httpd.obx`, a keep-alive HTTP/1.1 server on one thread (one task
per connection), measured by `python3 obx/cmd/http_gate.py`: the server on
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

## Reproducing

```sh
obx/compiler/build.sh                                  # out/obxc from the seed
python3 obx/bench/bench.py -n 11
python3 obx/cmd/eza_gate.py -j 12                      # eza correctness on both builds
obx/compiler/out/obxc obx/cmd/eza.obx --native -o eza  # and without --native for the C build
obx/tools/native_selfbuild.sh                          # the natively built compiler
obx/compiler/out/obxc obx/bench/tasks.obx --native -o tasks && ./tasks
python3 obx/cmd/http_gate.py                           # the server table (about 2 minutes)
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
