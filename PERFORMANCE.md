# obx performance

obx builds a program in two ways:

- **native**: `obxc --native`, the compiler's own x86-64 backend. It writes an
  ELF executable directly, with no gcc and, unless the program calls C
  libraries, no libc.
- **via C**: `obxc`, which writes C and runs `gcc -O2`.

Both are built from the same IR and must behave identically; the gate checks
that on 1,512 programs. This page measures what each costs. Measured on 3
November 2026 at `aac6659`, after the fourth native performance pass and the
first concurrency stage. The numbers are host-dependent: use them to compare
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
| fib | 321 ms | 351 ms | 1.09 | recursive calls |
| loop | 263 ms | 425 ms | 1.61 | two overflow-checked operations per iteration |
| array | 103 ms | 167 ms | 1.62 | checked indexing |
| alloc | 121 ms | 65 ms | 0.53 | 20 million records allocated and freed |
| lists | 38 ms | 35 ms | 0.92 | linked list build, sum, drop |
| strings | 76 ms | 51 ms | 0.67 | concatenation and `toStr` |
| trees | 24 ms | 20 ms | 0.80 | binary trees |
| tokens | 16 ms | 13 ms | 0.80 | byte scanning and hashing |

- **Native is ahead on allocation and strings** (alloc, strings, tokens;
  lists and trees narrowly). Its freestanding runtime has a size-class
  allocator with no locks or libc bookkeeping, and the string and index
  checks are generated inline.
- **gcc is ahead on tight arithmetic loops** (loop, array, and narrowly
  fib). It unrolls and schedules; the native backend is a direct code
  generator with register allocation by live ranges, which also keeps
  values across calls in callee-saved registers
  ([design/native.md](obx/design/native.md) section 4). `loop` is the widest
  gap.
- **Code placement matters.** Native fib runs at 1,690M or 2,020M cycles
  with identical instructions, depending only on where its code falls
  relative to 64-byte boundaries. The generated code now starts at a
  multiple of 64 and every function at a multiple of 16, so a change in the
  runtime's size no longer moves it. Before that fix, merging the
  concurrency runtime had silently cost fib 20%.

## eza

[obx/cmd/eza.obx](obx/cmd/eza.obx), a 1,600-line implementation of the
common subset of eza 0.23.3. `LC_ALL=C TZ=UTC`; the long listings use
`--bytes --time-style=long-iso -n`, which this eza requires:

| listing | output | via C | native |
|---|---:|---:|---:|
| `-T /usr/share` (tree) | 2.1 MB | 108 ms | 119 ms |
| `-T -l /usr/share` (long tree) | 3.7 MB | 198 ms | 215 ms |
| `-l /usr/lib/x86_64-linux-gnu` | 51 KB | 4 ms | 4 ms |

The golden suite (`python3 obx/cmd/eza_gate.py -j 12`) passes on both
builds: 482 cases, 72 live-only cases skipped.

## The compiler

The obx compiler compiling itself (`obx/compiler/main.obx`), built each way:

| | gcc-built | native-built |
|---|---:|---:|
| self-build to an executable (`--native`) | 273 ms | 422 ms |
| self-build to C (`--emit-c`) | 208 ms | 268 ms |
| full build through gcc | about 14 s, nearly all of it gcc | — |
| compiler binary | 1,491,976 bytes (dynamic, glibc) | 2,152,409 bytes (static, no libc) |

The source is 18,696 lines of obx and 2,528 lines of C runtime (the C
backend's `rt.h` and the native backend's freestanding `rt.c`, each now
with task stacks and switching).

- The native-built compiler is about 1.6x slower than the gcc-built one, the
  same pattern as fib and loop: a compiler is call-heavy and branchy, which
  is gcc's strength.
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

The compiler had grown between the third and fourth passes (it builds
the IR for both backends since then), so its two pairs of rows do not
compare with each other. The fourth pass cut fib's cycles by 23% (2,188M
to 1,690M); the self-build's and eza's by about 1% and 2%.

The earlier steps and the per-commit figures are in
[obx/design/native.md](obx/design/native.md) sections 4 and 8. What remains
is mostly work the code itself asks for (allocation, string concatenation,
reference counts, copies of large value records), not register use.

## Binary sizes

| program | via C | native |
|---|---:|---:|
| empty program | 24,384 bytes (dynamic, glibc) | 49,152 bytes (static) |
| eza | 202,672 bytes (dynamic, glibc) | 274,368 bytes (static) |
| compiler | 1,491,976 bytes (dynamic, glibc) | 2,152,409 bytes (static) |

A native executable carries its own runtime instead of linking libc. When a
program calls C libraries (libm, sqlite3, libpq), it is dynamically linked
against them.

## Tasks

The cost of the concurrency primitives (`lib/task.obx`, SPEC 16), from
`obx/bench/tasks.obx`, ns per operation, best of 3 runs:

| operation | via C | native |
|---|---:|---:|
| task switch (`task.yield`) | 6.6 | 17.6 |
| start a task (stack reused) | 35 | 24 |
| start plus a wait that parks (round trip) | 83 | 68 |
| wait on a finished task | 25 | 22 |
| promise complete | 11 | 16 |
| `Shared.with` | 3.2 | 6.9 |

All of these are far below a system call (a socket read or write is
1,000–5,000 ns). The native switch is slower than C's because it goes
through more of the generic task code; it has not been tuned yet.

## Reproducing

```sh
obx/compiler/build.sh                                  # out/obxc from the seed
python3 obx/bench/bench.py -n 11
python3 obx/cmd/eza_gate.py -j 12                      # eza correctness on both builds
obx/compiler/out/obxc obx/cmd/eza.obx --native -o eza  # and without --native for the C build
obx/tools/native_selfbuild.sh                          # the natively built compiler
obx/compiler/out/obxc obx/bench/tasks.obx --native -o tasks && ./tasks
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
