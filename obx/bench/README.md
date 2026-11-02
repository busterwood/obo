# obx benchmarks

The obo benchmark programs (`obo/bench/*_obo.obx`) ported to obx, to get a
performance baseline for the reference compiler (`obx/ref`, now retired to
`archive/obx-ref/`: Python to C, then `gcc -O2`). The self-hosted compiler
writes the same C, so the results below hold for it. The `*_gc.obx` variants are not ported: they test the old collector
dialect, and nothing in them that the `_obo` versions lack.

    python3 obx/bench/bench.py [-n RUNS] [--only NAME ...]

For each program it builds the obx version (`obx/compiler/out/obxc` or
`$OBXC`, default flags) and the
obo version with `obo/obo`. It checks that the outputs are equal and that the
obx program is leak-free (`OBX_CHECK=1`), then reports the best wall time of N
runs (default 5), `OBX_CHECK` unset. Programs: `fib`, `loop`, `array`, `alloc`,
`lists`, `strings`, `trees`, `tokens`. `tokens` reads `obx/compiler/sema.obx`
(any text file works; the obo workload's original input lives in an archive
that is no longer in the tree).

## Results

WSL2, gcc 13, best of 11, wall clock, 2026-11-02; every output matched. *Before*
is `ref` as of the baseline (`gcc -O1`, and the same C at `-O2`); *after* is
`ref` with the fixes below (`gcc -O2` by default, direct drop glue for types
that cannot form a chain, `malloc` and no live-object count without
`OBX_CHECK`, 64-bit digit loop in `toStr`). Both runs in the same session.

| benchmark | obo | before (-O1) | before, -O2 | after | after/obo | output |
|---|---:|---:|---:|---:|---:|---|
| fib | 455 ms | 330 ms | 333 ms | 356 ms | 0.78 | 102334155 |
| loop | 212 ms | 438 ms | 267 ms | 263 ms | 1.24 | 999999999000000000 |
| array | 236 ms | 135 ms | 105 ms | 101 ms | 0.43 | 101333200000000 |
| alloc | 496 ms | 278 ms | 228 ms | 122 ms | 0.25 | 200000030000000 |
| lists | 28 ms | 66 ms | 58 ms | 35 ms | 1.26 | 501300 |
| strings | 63 ms | 118 ms | 97 ms | 84 ms | 1.33 | 11228000 |
| trees | 20 ms | 45 ms | 44 ms | 25 ms | 1.26 | 1441751 |
| tokens | 19 ms | 23 ms | 18 ms | 17 ms | 0.89 | 239950 8600 |

Timings under 50 ms are noisy (process start-up is a part of them). `fib` is
not slower for anything the fixes changed: its code is the same instruction
for instruction (4.64 G instructions both), only placed at another address,
which costs 10% more cycles on this machine; layout noise, not a regression.

## Differences from the obo programs

Same work and same output, written the obx way: `Option[Node]` / `Option[Tree]`
for the links (a value closed type around a pointer) instead of `?Node`; the
list `sum` recurses over borrows (10,000 deep) because a borrow cannot be
rebound to walk a chain; `strings` uses `+` and `toStr` on `int`; `tokens`
builds each identifier in a `StrBuf` (obo used a `[]byte`) and holds `str`
text (refcounted). obo's `alloc` uses a `ref record`; obx a boxed `record`.

## Notes

* **Where obx is faster (fib, array, alloc, tokens).** obo's compiler does
  little optimisation (locals in stack slots); gcc does register allocation,
  so call and indexing loops are quicker even with obx's checks (every array
  access is `rt_idx(i, len)` plus a checked add). `alloc` allocates and frees
  20 million records; with `malloc` and a direct `free` it is 4x faster than
  obo, whose allocator is slower than glibc's here.
* **`loop`** is two overflow-checked operations per iteration; `-O1` made it
  2x obo, `-O2` 1.24x. The checks themselves (never taken) cost little once
  gcc optimises; the `-O1` code generation was what hurt.
* **Allocation-bound programs (lists, trees, strings) are about 1.3x obo.**
  Before the fixes a tree node's drop pushed three actions on the drop work
  list (`rt_free_act`, the left and right `Option` glue) and the `Option` glue
  pushed the child again: about 5 indirect calls per node. Now a chain type's
  action frees its node itself and pushes each child once (one call per
  node), and a type that cannot form a chain is freed by plain recursive C.
  `perf` still puts about half of `lists` and `trees` in libc's `malloc` and
  `free` (one block per node); obo's own allocator is cheaper. A size-class
  free list in `rt_alloc` would be the next step; not done.
* **`strings`**: every `+` allocates a new refcounted block (`rt_str_concat`),
  and `"word" + i.toStr() + "-" + round.toStr()` makes 2 intermediate blocks.
  Building one block for a whole `a + b + c` chain would remove them, but it
  changes how `check.py` evaluates and holds the operands; left for now.
  `toStr` divided in 128 bits per digit (a libgcc call), now in 64.
* **`tokens`**: byte scanning and hashing, few allocations per byte.
* **Live-object counting** (`OBX_CHECK`) is a branch on a flag read once at
  start-up. `perf stat` on `alloc` (20 M allocations): 3.08 G instructions
  with the branch, 3.06 G counting always, 2.98 G not counting at all; the
  cycles are within noise of each other (about 3% at most).
