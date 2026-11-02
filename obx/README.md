# obx

obx is the next generation of the language: obo's ownership model (single
ownership, `&T` borrows, `^T` checked handles, no collector) with generics from
day one. Decimal, money, Option, Result and the containers are library code,
not compiler features.

The decision and the plan are in
[obo/design/generic-core-rethink.md](../obo/design/generic-core-rethink.md)
(decisions G1 to G5, 2026-11-02). In short:

- Each module is parsed to a tree, then checked and compiled in one walk per
  instance. Modules compile separately; symbol files carry signatures and the
  bodies of generic code.
- Generic code is checked where it is written: `T` is owned unless `T: copy`,
  and its methods come only from its constraints.
- A Python reference compiler settles the design first. The real compiler is
  then written in obx, compiles itself, and emits native x86-64 ELF. C is a
  stepping stone.

obo 0.19 (`../obo/`) is frozen at bug fixes only. The original GC compiler that
obo forked from is in `../archive/obx/`.

Source files use the `.obx` extension.

## Layout

| Path | Contents |
|---|---|
| `ref/` | the Python reference compiler (Python to C), grown from the gcore prototype |

## Status

Stage 1 (the core spec) is next. `ref/` is still the throwaway prototype:

    python3 ref/gcore.py ref/examples/money.obx --run
    python3 ref/run_tests.py
