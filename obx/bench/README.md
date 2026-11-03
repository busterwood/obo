# obx benchmarks

Eight small programs, each building the same program with both backends,
native and via C:

    python3 obx/bench/bench.py [-n RUNS] [--only NAME ...] [--obo]

For each program it builds `NAME.obx` with `obx/compiler/out/obxc` (or
`$OBXC`) twice, with `--native` and through C (`gcc -O2`). It checks that the
two print the same result and are leak free (`OBX_CHECK=1`), then reports the
best wall time of N runs (default 5) with `OBX_CHECK` unset.

| program | what it does |
|---|---|
| `fib` | recursive calls |
| `loop` | two overflow-checked operations per iteration |
| `array` | checked indexing |
| `alloc` | 20 million records allocated and freed |
| `lists` | a linked list built, summed and dropped |
| `strings` | concatenation and `toStr` |
| `trees` | binary trees |
| `tokens` | byte scanning and hashing; reads `obx/compiler/sema.obx` (any text file works) |

The results, with eza and the compiler, are in
[PERFORMANCE.md](../../PERFORMANCE.md).

`--obo` adds a column for obx's predecessor, the archived obo 0.19
(`archive/obo/obo`; build it with `make -C archive/obo`), running the same
programs from `archive/obo/bench/*_obo.obx`. The programs came from obo's
benchmarks and do the same work, written the obx way: `Option[Node]` links
instead of nullable references, the list `sum` recursing over borrows,
`StrBuf` for building identifiers.

## Notes

- **Native and C.** The native backend is ahead on allocation and strings
  (its own size-class allocator, checks generated inline) and, since its
  loop pass, on the tight loops too (loop, array); fib is level.
- **`loop`** is two overflow-checked operations per iteration. The native
  backend proves both cannot overflow (`i < n` bounds `i` and `i * 2`) and
  runs the loop at one cycle an iteration; gcc keeps the checks.
- **`array`** keeps one bounds check per element natively: the loop runs to
  `n`, which the compiler cannot relate to the array's length.
- **Allocation-bound programs** (lists, trees): through C, about half the
  time is libc's `malloc` and `free`, one block per node.
- **`strings`**: every `+` allocates a new reference-counted block, and
  `"word" + i.toStr() + "-" + round.toStr()` makes two intermediate blocks.
  Building one block for a whole `a + b + c` chain would remove them.
- **Live-object counting** (`OBX_CHECK`) is a branch on a flag read once at
  start-up: about 3% of instructions on `alloc` at most, within noise in
  cycles.
