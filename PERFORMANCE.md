# obx performance

This page compares the three ways a program in this repository can be built,
measured on 3 November 2026 on main after the `perf2` and `ir` merges
(`1dc82b5`):

- **obx native**: `obxc --native`, the compiler's own x86-64 backend, no gcc.
- **obx via C**: `obxc`, which writes C and runs `gcc -O2`.
- **obo**: the archived obo 0.19 compiler (`archive/obo/obo`), which writes
  machine code itself. obo's own performance record is
  [archive/obo/PERFORMANCE.md](archive/obo/PERFORMANCE.md).

The numbers are host-dependent: use them to compare the columns, not as
portable constants.

## Host and method

- AMD Ryzen 7 8745HS (16 threads), WSL2, Linux 6.18, gcc 13, glibc 2.39.
- Wall clock, best of N runs (11 for the benchmarks, 9 for eza and the
  compiler), warm cache, output to `/dev/null`. Timings vary by 5–10%
  between runs on this machine; below about 50 ms, process start-up is part
  of the figure.
- Every row checks its outputs first: the three builds of each benchmark
  print the same result, both obx builds are leak free under `OBX_CHECK=1`,
  and the three eza builds write byte-identical listings.

## Benchmarks

`python3 obx/bench/bench.py -n 11` ([obx/bench/](obx/bench/README.md); the
obo programs ported to obx):

| benchmark | obo | obx via C | obx native | C/obo | native/obo | what it does |
|---|---:|---:|---:|---:|---:|---|
| fib | 452 ms | 321 ms | 460 ms | 0.71 | 1.02 | recursive calls |
| loop | 208 ms | 264 ms | 420 ms | 1.27 | 2.02 | two overflow-checked operations per iteration |
| array | 234 ms | 102 ms | 168 ms | 0.44 | 0.72 | checked indexing |
| alloc | 483 ms | 121 ms | 63 ms | 0.25 | 0.13 | 20 million records allocated and freed |
| lists | 28 ms | 37 ms | 35 ms | 1.32 | 1.25 | linked list build, sum, drop |
| strings | 61 ms | 74 ms | 52 ms | 1.21 | 0.84 | concatenation and `toStr` |
| trees | 19 ms | 24 ms | 21 ms | 1.25 | 1.08 | binary trees |
| tokens | 18 ms | 16 ms | 13 ms | 0.89 | 0.70 | byte scanning and hashing |

- **Native is ahead of C on allocation and strings** (alloc, strings, tokens;
  lists and trees narrowly): its freestanding runtime has a size-class allocator with no locks
  or libc bookkeeping, and the string and index checks are generated inline.
- **gcc wins on tight arithmetic loops** (fib, loop, array): it unrolls,
  schedules and keeps everything in registers across calls; the native
  backend is a straightforward code generator with register allocation by
  live ranges (design/native.md section 4). `loop` is the widest gap.
- Against obo, obx is faster on 4 of 8 benchmarks either way (and level on
  fib natively). The small linked-structure programs (lists, trees) and
  `loop` are slower than obo in both builds.

## eza

[obx/cmd/eza.obx](obx/cmd/eza.obx), a port of obo's eza (1,600 lines against
obo's 3,482), against obo's eza binary. `LC_ALL=C TZ=UTC`; the long listings
use `--bytes --time-style=long-iso -n`, which the ports require:

| listing | output | obo | obx via C | obx native |
|---|---:|---:|---:|---:|
| `-T /usr/share` (tree) | 2.1 MB | 143 ms | 103 ms | 117 ms |
| `-T -l /usr/share` (long tree) | 3.7 MB | 418 ms | 189 ms | 205 ms |
| `-l /usr/lib/x86_64-linux-gnu` | 51 KB | 5 ms | 4 ms | 4 ms |

The long tree is about twice as fast as obo's in either obx build. The
golden suite
(`python3 obx/cmd/eza_gate.py -j 12`) passes on both builds: 482 cases, 72
live-only skipped.

## The compiler

The obx compiler built two ways, compiling itself (`obx/compiler/main.obx`),
and obo 0.19 compiling itself:

| | obo 0.19 | obx, gcc-built | obx, native-built |
|---|---:|---:|---:|
| self-build to an executable | 223 ms | 252 ms (`--native`) | 406 ms (`--native`) |
| self-build to C (`--emit-c`) | — | 193 ms | 257 ms |
| full build through gcc | — | 13.6 s (gcc is 13.4 s of it) | — |
| compiler binary | 757,761 bytes | 1,402,344 bytes (dynamic, glibc) | 2,086,449 bytes (static, no libc) |
| compiler source | 17,233 lines | 18,189 lines of obx and 1,815 of C runtime | same |

- The obx compiler does more than obo's: generics instantiated per type, two
  backends from one IR, dynamic linking, the C runtime and the freestanding
  runtime. It is within 15% of obo's self-build time when gcc-built.
- The native-built compiler is about 1.6x slower than the gcc-built one on
  this workload, the same pattern as fib and loop: the compiler is
  call-heavy and branchy, which is gcc's strength.
- `--emit-c` became about 50% slower when the C began to be printed from the
  IR (the `ir` merge); `--native` became 30% faster, since it no longer
  prints and reparses C. Winning back the C path is listed in
  design/native.md section 8.

### Native backend progress

User-mode instructions (`perf stat`), the native-built compiler building
itself and native eza `-T /usr/share`:

| step | self-build | eza `-T` |
|---|---:|---:|
| before the second performance pass | 1,817M | 757M |
| after it (`perf2`, live-range registers, operands in place) | 1,649M | 659M |

The earlier steps and the per-commit figures are in
[obx/design/native.md](obx/design/native.md) sections 4 and 8. What remains
is mostly work the code itself asks for (allocation, string concatenation,
reference counts, copies of large value records), not register use.

## Binary sizes

| program | obo | obx via C | obx native |
|---|---:|---:|---:|
| eza | 266,241 bytes | 192,912 bytes (dynamic, glibc) | 270,272 bytes (static) |
| compiler | 757,761 bytes | 1,402,344 bytes | 2,086,449 bytes |

A native executable carries its runtime (an empty program is 45,056 bytes)
and needs no libc
unless it calls C libraries; then it is dynamically linked against them.

## Reproducing

```sh
obx/compiler/build.sh                                  # out/obxc from the seed
make -C archive/obo                                    # archive/obo/obo, for the obo column
python3 obx/bench/bench.py -n 11
python3 obx/cmd/eza_gate.py -j 12                      # eza correctness on both builds
obx/compiler/out/obxc obx/cmd/eza.obx --native -o eza  # then time it against archive/obo/build/eza/eza
obx/tools/native_selfbuild.sh                          # the natively built compiler
```
