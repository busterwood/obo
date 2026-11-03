# obx performance

obx builds a program in two ways:

- **native**: `obxc --native`, the compiler's own x86-64 backend. It writes an
  ELF executable directly, with no gcc and, unless the program calls C
  libraries, no libc.
- **via C**: `obxc`, which writes C and runs `gcc -O2`.

Both are built from the same IR and must behave identically; the gate checks
that on 1,532 programs. This page measures what each costs. Measured on 3
November 2026 at `aac6659`, after the fourth native performance pass and the
first concurrency stage; the rows the fifth pass (inlining in the IR, branch
`perf5`) moved were measured again after it, and the task and server
sections at stage C1b (sockets and timers). The numbers are host-dependent: use them to compare
the columns, not as portable constants.

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
| fib | 307 ms | 314 ms | 1.02 | recursive calls |
| loop | 258 ms | 414 ms | 1.60 | two overflow-checked operations per iteration |
| array | 99 ms | 165 ms | 1.67 | checked indexing |
| alloc | 120 ms | 63 ms | 0.53 | 20 million records allocated and freed |
| lists | 36 ms | 27 ms | 0.75 | linked list build, sum, drop |
| strings | 72 ms | 47 ms | 0.65 | concatenation and `toStr` |
| trees | 23 ms | 20 ms | 0.87 | binary trees |
| tokens | 15 ms | 11 ms | 0.73 | byte scanning and hashing |

- **Native is ahead on allocation and strings** (alloc, lists, strings,
  tokens; trees narrowly). Since the fifth pass small functions and glue
  are inlined before code generation (lists: `push` and the list's drop in
  the loop). Its freestanding runtime has a size-class
  allocator with no locks or libc bookkeeping, and the string and index
  checks are generated inline.
- **gcc is ahead on tight arithmetic loops** (loop, array; fib is
  level). It unrolls and schedules; the native backend is a direct code
  generator with register allocation by live ranges, which also keeps
  values across calls in callee-saved registers
  ([design/native.md](obx/design/native.md) section 4). `loop` is the widest
  gap.
- **Code placement matters.** Native fib runs at 1,547M, 1,690M or 2,020M
  cycles with identical instructions, depending only on where its code
  falls relative to 64-byte boundaries. The generated code starts at a
  multiple of 64 and every function at a multiple of 16, so a change in the
  runtime's size no longer moves it, but a change in the functions before
  it still can: fib's gain and trees' loss in the fifth pass (8% each way)
  are placement alone. Before the alignment fix, merging the concurrency
  runtime had silently cost fib 20%.

## eza

[obx/cmd/eza.obx](obx/cmd/eza.obx), a 1,600-line implementation of the
common subset of eza 0.23.3. `LC_ALL=C TZ=UTC`; the long listings use
`--bytes --time-style=long-iso -n`, which this eza requires:

| listing | output | via C | native |
|---|---:|---:|---:|
| `-T /usr/share` (tree) | 2.1 MB | 105 ms | 111 ms |
| `-T -l /usr/share` (long tree) | 3.7 MB | 194 ms | 201 ms |
| `-l /usr/lib/x86_64-linux-gnu` | 51 KB | 4 ms | 4 ms |

The golden suite (`python3 obx/cmd/eza_gate.py -j 12`) passes on both
builds: 482 cases, 72 live-only cases skipped.

## The compiler

The obx compiler compiling itself (`obx/compiler/main.obx`), built each way:

| | gcc-built | native-built |
|---|---:|---:|
| self-build to an executable (`--native`) | 343 ms | 407 ms |
| self-build to C (`--emit-c`) | 216 ms | 242 ms |
| full build through gcc | about 14 s, nearly all of it gcc | — |
| compiler binary | 1,535,256 bytes (dynamic, glibc) | 2,381,865 bytes (static, no libc) |

The source is 19,897 lines of obx and 2,528 lines of C runtime (the C
backend's `rt.h` and the native backend's freestanding `rt.c`, each now
with task stacks and switching).

- The native-built compiler is 1.1-1.2x slower than the gcc-built one
  (1.6x before the fifth pass): a compiler is call-heavy and branchy, which
  is gcc's strength, and inlining in the IR closed most of the gap.
- `--native` itself does more work since the fifth pass (inlining, and
  code generation of the larger IR): with the gcc-built compiler, whose own
  code does not change, building the compiler natively takes about 20%
  longer than before (273 ms then, on smaller sources); building eza, 10%.
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

The compiler had grown between the third and fourth passes (it builds
the IR for both backends since then), so its two pairs of rows do not
compare with each other. The fourth pass cut fib's cycles by 23% (2,188M
to 1,690M); the self-build's and eza's by about 1% and 2%. The fifth cut
the self-build's cycles by 12% (1,699M to 1,496M; the compiler's own work
without the pass, `--emit-c`, by 17%), eza's by 9% (233M to 213M) and the
lists benchmark's by 16%.

The earlier steps and the per-commit figures are in
[obx/design/native.md](obx/design/native.md) sections 4 and 8. What remains
is mostly work the code itself asks for (allocation, string concatenation,
reference counts, copies of large value records), not register use.

## Binary sizes

| program | via C | native |
|---|---:|---:|
| empty program | 24,384 bytes (dynamic, glibc) | 49,152 bytes (static) |
| eza | 202,672 bytes (dynamic, glibc) | 290,752 bytes (static) |
| compiler | 1,535,256 bytes (dynamic, glibc) | 2,381,865 bytes (static) |

A native executable carries its own runtime instead of linking libc. When a
program calls C libraries (libm, sqlite3, libpq), it is dynamically linked
against them.

## Tasks

The cost of the concurrency primitives (`lib/task.obx`, `lib/net.obx`, SPEC
16), from `obx/bench/tasks.obx`, ns per operation, best of 3 runs (measured
again at stage C1b, the event loop):

| operation | via C | native |
|---|---:|---:|
| task switch (`task.yield`) | 6.3 | 18.2 |
| start a task (stack reused) | 34 | 26 |
| start plus a wait that parks (round trip) | 91 | 80 |
| the same inside `task.within` (a timer set and removed) | 107 | 316 |
| wait on a finished task | 22 | 24 |
| promise complete | 12 | 17 |
| `Shared.with` | 3.4 | 7.1 |
| a `task.sleep(1)` that fires, beyond a yield | 20–200 | 180–260 |
| a socket read that finds data (no park) | 653 | 707 |
| a one-byte round trip between two tasks over loopback TCP | 181,000 | 205,000 |

The task operations are far below a system call. The event loop added about
6 ns to a native park (the deadline and timer checks) and nothing measurable
to C's. The native runtime has no vDSO (it is static and makes raw system
calls), so the `clock_gettime` behind every timer is a real system call of
about 250 ns: native timers cost that much more. The `sleep` row is noisy:
the 1 ms sleep's overshoot is divided among 10,000 tasks. The round trip is
this machine's loopback: a Python ping-pong over loopback TCP takes
190,000 ns with two blocking threads and 205,000 ns with epoll on one
thread. The native switch is slower than C's because it goes through more
of the generic task code; it has not been tuned yet.

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
| bench fib | 452 ms | 321 ms | 351 ms |
| bench alloc | 483 ms | 121 ms | 63 ms |
| bench loop | 208 ms | 264 ms | 420 ms |
| eza `-T -l /usr/share` | 418 ms | 189 ms | 205 ms |
| compiler self-build | 223 ms | 252 ms | 406 ms |

The fib row's obx figures are from after the fourth native pass; the rest
are from the day of the comparison. `python3 obx/bench/bench.py --obo` adds obo's column to the benchmarks
(build `archive/obo/obo` first with `make -C archive/obo`).
