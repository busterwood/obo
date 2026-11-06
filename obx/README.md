# obx

obx is a systems language with single ownership, `&T` borrows, no garbage
collector and generics. Source files end in `.obx`.

Decimal, money, `Option`, `Result` and the containers are library code, not
compiler features. Checked handles (`^T`) are planned.

## How the compiler works

- Each module is parsed to a tree. It is then checked and compiled in one
  walk per instance.
- Generic code is checked where it is written. `T` is owned unless
  `T: copy`, and a generic value gets its methods only from its constraints.
- Compiling modules separately, with symbol files, is decided (G1) but not
  built yet. Each compile reads the whole program.
- The compiler is written in obx. It compiles itself to a fixed point and
  emits C or native x86-64 ELF.

The decisions are in
[design/generic-core-rethink.md](design/generic-core-rethink.md)
(G1 to G5, 2026-11-02). The stages and their state are in
[PLAN.md](PLAN.md). A Python reference compiler settled the design first.
It is retired in [../archive/obx-ref/](../archive/obx-ref/README.md).

## Layout

| Path | Contents |
|---|---|
| [SPEC.md](SPEC.md) | the language |
| [PLAN.md](PLAN.md) | the stages, their gates and state |
| [compiler/](compiler/README.md) | the compiler, and its C seed |
| [lib/](lib/README.md) | the library, one module per file |
| [cmd/](cmd/README.md) | programs: eza, the coreutils subset, grep, zoxide, jq, cmark, an HTTP server and its load client, tlsbench |
| [tests/](tests/README.md) | the test suites and their runners |
| [bench/](bench/README.md) | benchmarks, native against C |
| [design/](design/) | design notes, one per stage or subsystem |
| `redteam/` | red-team corpora and harnesses (rounds 1 to 5) |
| `tools/` | the gate (`gate.sh`), the build cache and the seed check |

The build commands are in the [root README](../README.md). Two gate tiers:

- `obx/tools/gate.sh quick` after every change.
- `obx/tools/gate.sh full` before a merge. It adds the red-team rounds, run
  under sanitizers.

See [tests/README.md](tests/README.md) for the suites.
