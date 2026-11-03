# obx

obx is a systems language with single ownership, `&T` borrows, no collector
and generics. Decimal, money, Option, Result and the containers are library
code, not compiler features. Checked handles (`^T`) are planned.

The decision and the plan are in
[design/generic-core-rethink.md](design/generic-core-rethink.md)
(decisions G1 to G5, 2026-11-02). In short:

- Each module is parsed to a tree, then checked and compiled in one walk per
  instance. Modules compile separately; symbol files carry signatures and the
  bodies of generic code.
- Generic code is checked where it is written: `T` is owned unless `T: copy`,
  and its methods come only from its constraints.
- A Python reference compiler settled the design first. The real compiler is
  written in obx, compiles itself, and emits C or native x86-64 ELF.

Source files use the `.obx` extension.

## Layout

| Path | Contents |
|---|---|
| [SPEC.md](SPEC.md) | the language |
| [PLAN.md](PLAN.md) | the stages, their gates and state; the language backlog |
| [compiler/](compiler/README.md) | the compiler, written in obx: obx to C, or `--native` to x86-64 ELF; its C seed |
| [lib/](lib/README.md) | the library (std, collections, strings, fs, io, os, time, math, money, sqldialect, sqlite, postgres, mssql, ffi, task, net) |
| [cmd/](cmd/README.md) | programs: eza, an HTTP/1.1 server (httpd, with an optional Postgres pool) and its load client |
| [tests/](tests/README.md) | the conformance suite, the compiler tests, the native tests, the eza golden suite and their runners |
| `redteam/` | the red-team corpora and harnesses (rounds 1 to 5; ASan, UBSan, and TSan for the task tests) |
| `tools/` | the gate (`gate.sh`), the build cache (`cbuild.py`), the seed check |
| `design/` | design notes: the generic-core decision, the native backend, concurrency (stages C1, tasks in one process, and C2, worker processes, are built) |
| [bench/](bench/README.md) | benchmarks, native against C (results in [../PERFORMANCE.md](../PERFORMANCE.md)) |

Its predecessors (obo 0.19, the original garbage-collected obx and the
Python reference compiler) are retired and kept in `../archive/`; see
[../HISTORY.md](../HISTORY.md).

## Building and testing

    obx/compiler/build.sh                       # obx/compiler/out/obxc from the C seed (gcc only)
    obx/compiler/out/obxc file.obx --native -o prog
    obx/compiler/out/obxc file.obx -o prog      # through C
    obx/tools/gate.sh quick                     # after every change
    obx/tools/gate.sh full                      # before a merge

See [tests/README.md](tests/README.md) for the suites and the gate.
