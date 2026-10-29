# obo benchmarks

Three independent, on-demand benchmark entry points. None is a gate and none is
run by CI.

* `bench.py` — milestone-4 memory/time comparison of the two dialects: each
  workload in both dialects (`NAME_gc.obx` for mainline obx, `NAME_obo.obx` for
  obo), measured for peak resident memory and time. `peak.c` is the small RSS
  helper it builds.

  ```sh
  python3 obo/bench/bench.py
  ```

* `micro.py` — phase-0 micro-benchmarks for the optimization plan
  (`obo/FORK.md`): the loop, fib, array and alloc workloads, with median wall
  time, peak RSS and code-segment instruction counts. It also builds `peak.c`.

  ```sh
  python3 obo/bench/micro.py [--only NAME] [-n RUNS]
  ```

* `cli/` — CLI benchmarks of obo programs and tools against equivalent Python 3
  programs, with GNU tools and the pinned eza as reference rows. See
  `cli/README.md` for methodology, sample output and limits.

  ```sh
  python3 obo/bench/cli/run.py          # about 90 s on a recent host
  python3 obo/bench/cli/run.py --quick  # under ~20 s
  ```

The test harnesses glob only `obo/bench/*.obx` (top level, not recursive), so
the programs under `obo/bench/cli/` are not compiled by the gates.
