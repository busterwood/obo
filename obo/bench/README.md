# obo benchmarks

Two independent, on-demand benchmark directories. Neither is a gate and neither
is run by CI.

* `bench.py` — milestone-4 memory/time comparison of the two dialects: each
  workload in both dialects (`NAME_gc.obx` for mainline obx, `NAME_obo.obx` for
  obo), measured for peak resident memory and time.

  ```sh
  python3 obo/bench/bench.py
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
