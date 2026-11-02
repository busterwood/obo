# obx benchmarks

The obo benchmark programs (`obo/bench/*_obo.obx`) ported to obx, to get a
performance baseline for the reference compiler (`obx/ref`: Python to C, then
`gcc -O1`). The `*_gc.obx` variants are not ported: they test the old collector
dialect, and nothing in them that the `_obo` versions lack.

    python3 obx/bench/bench.py [-n RUNS] [--only NAME ...]

For each program it builds the obx version (default flags), the same C with
`gcc -O2` (via `obxc.py --emit-c`, the same flags otherwise; `ref` is not
changed), and the obo version with `obo/obo`. It checks that the three outputs
are equal and that the obx program is leak-free (`OBX_CHECK=1`), then reports
the best wall time of N runs (default 5), `OBX_CHECK` unset. Programs: `fib`,
`loop`, `array`, `alloc`, `lists`, `strings`, `trees`, `tokens`. `tokens` reads
`obx/compiler/sema.obx` (any text file works; the obo workload's original input
lives in an archive that is no longer in the tree).

## Results

WSL2, gcc, best of 5, wall clock. The obo toolchain (`obo/obo`, a static ELF
compiler) is present and works; every output matched.

| benchmark | obo | obx (-O1, default) | obx/obo | obx (-O2) | -O2/obo | output |
|---|---:|---:|---:|---:|---:|---|
| fib | 472 ms | 333 ms | 0.70 | 326 ms | 0.69 | 102334155 |
| loop | 222 ms | 446 ms | 2.01 | 275 ms | 1.24 | 999999999000000000 |
| array | 235 ms | 140 ms | 0.60 | 104 ms | 0.44 | 101333200000000 |
| alloc | 516 ms | 278 ms | 0.54 | 240 ms | 0.47 | 200000030000000 |
| lists | 30 ms | 65 ms | 2.15 | 60 ms | 1.99 | 501300 |
| strings | 63 ms | 128 ms | 2.04 | 101 ms | 1.61 | 11228000 |
| trees | 21 ms | 45 ms | 2.19 | 44 ms | 2.13 | 1441751 |
| tokens | 19 ms | 23 ms | 1.24 | 19 ms | 1.01 | 239950 8600 |

Timings under 50 ms are noisy (process start-up is a part of them); read those
ratios as "about 2x", not as exact.

## Differences from the obo programs

Same work and same output, written the obx way: `Option[Node]` / `Option[Tree]`
for the links (a value closed type around a pointer) instead of `?Node`; the
list `sum` recurses over borrows (10,000 deep) because a borrow cannot be
rebound to walk a chain; `strings` uses `+` and `toStr` on `int`; `tokens`
builds each identifier in a `StrBuf` (obo used a `[]byte`) and holds `str`
text (refcounted). obo's `alloc` uses a `ref record`; obx a boxed `record`.

## Notes

* **Where obx is faster (fib, array, alloc).** obo's compiler does little
  optimisation (locals in stack slots); gcc does register allocation, so call
  and indexing loops are quicker even with obx's checks. In `array` every
  access is `rt_idx(i, len)` plus `rt_add_int` (a checked add), and gcc still
  beats obo by 1.7x to 2.3x. `alloc` is faster too although it calls
  `calloc`/`free` 20 million times, which means obo's allocator is slower than
  glibc's here.
* **`loop` is the one case where flags matter.** The hot loop is
  `v_s = rt_add_int(v_s, rt_mul_int(v_i, 2, "loop.obx:7"), "loop.obx:7")`,
  two overflow-checked operations per iteration. At -O1 it takes 446 ms (2.0x
  obo), at -O2 275 ms (1.24x). -O2 also helps `array` (140 to 104 ms) and
  `strings` (128 to 101 ms), and does nothing for the allocation-bound
  programs. Moving `ref` to -O2 would be a cheap win; the checks are not what
  hurts (the branch is never taken), the -O1 code generation is.
* **Allocation-bound programs (lists, trees, strings) are about 2x slower
  than obo.** `perf record --sort dso` on the -O1 binaries shows libc
  (malloc/free) at 37% (trees), 52% (lists), 54% (strings) and 81% (alloc) of
  the samples. Each obx node is a separate `calloc`, and freeing is not free:
  the generated drop glue is not a recursive function but a work list
  (`rt_dpush` / `rt_drun`, so a long chain cannot overflow the stack, SPEC 9.4).
  For a tree node the drop pushes three actions (`rt_free_act`, the left
  `Option` glue, the right `Option` glue), then pops and calls each through a
  function pointer; the `Option` glue pushes the child's glue again. That is
  roughly 5 indirect calls per node against obo's one free. `rt_alloc` is also
  `calloc` plus a live-object counter increment even when `OBX_CHECK` is
  unset. The remaining ~60% of `trees` time in the program itself is this glue
  and the recursion with a checked add (`rt_add_int` in `check`).
* **`strings`**: every `+` allocates a new refcounted block (`rt_str_concat`),
  and `"word" + i.toStr() + "-" + round.toStr()` makes 7 temporaries, each
  allocated and freed; `println` and `toStr` also go through `str`. obo's
  version does the same number of operations but with its own cheaper
  allocator.
* **`tokens` is close to parity** (1.0x at -O2): the work is byte scanning
  and hashing, with few allocations per byte. `StrBuf.add` and `toStr` copy
  byte by byte / once, which does not show at this size.
* The overflow checks themselves (`rt_add_int` etc., never taken) cost little
  once gcc optimises; I did not isolate them with a no-check build because
  `ref` is not to be changed.

## Possible performance bugs (not fixed; `ref` was not touched)

1. `gcc -O1` leaves `loop` 1.6x slower than -O2; consider -O2 as the default.
2. Drop glue pushes and pops a heap work list for every boxed node even when
   the type cannot form a long chain (a non-recursive record, or a recursive
   one where the depth cannot matter); `Cell` in `alloc` is cheap only because
   it has no fields to drop. A direct `free` for leaf-like cases and a
   recursive call for the fixed-depth case would remove most of the `trees`
   and `lists` overhead.
3. `rt_alloc` always uses `calloc` and always counts live objects, though the
   leak check is only on with `OBX_CHECK=1`; zeroing is needed for none of
   `alloc`'s fields (they are all assigned).
