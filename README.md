# obx

obx is a small Oberon-derived language with single ownership, borrowed `&T`
references and no garbage collector, and with generics from day one: Decimal,
Money, Option, Result and the containers are ordinary library code, not
compiler features. Its compiler is written in obx, compiles itself, and emits
C or native x86-64 ELF executables.

Start with [obx/README.md](obx/README.md); the language is
[obx/SPEC.md](obx/SPEC.md), the stages and their state are
[obx/PLAN.md](obx/PLAN.md).

```
obx/compiler/build.sh                          # obx/compiler/out/obxc from the C seed (gcc only)
obx/compiler/out/obxc prog.obx -o prog         # through C
obx/compiler/out/obxc prog.obx --native -o prog
obx/tools/gate.sh quick                        # the suites; `full` adds the red-team rounds
```

## Status

As of 2026-11-03:

- **The compiler** is written in obx (about 18,200 lines) and compiles itself
  to a fixed point. One IR feeds two backends: C for gcc, and a native x86-64
  backend that writes ELF executables with no gcc, statically linked, or
  dynamically linked when a program calls C libraries. The native compiler
  builds itself to the same fixed point. Its C is checked in as the seed, so
  a build needs only gcc.
- **Programs:** an [eza](obx/cmd/README.md) port, 1,600 lines against obo's
  3,482, byte-identical to it on obo's golden suite and twice as fast on long
  tree listings; [SQLite and Postgres](obx/lib/README.md) libraries with no C
  shims, about half the size of obo's.
- **Performance:** native and gcc-built programs are within a small factor
  of each other and of obo; native leads on allocation and strings, gcc on
  tight arithmetic loops. The figures are in
  [PERFORMANCE.md](PERFORMANCE.md).
- **Checks:** a conformance suite, the compiler, library and eza tests, a
  native-against-C comparison over 1,491 programs, and five red-team rounds
  under ASan and UBSan (`obx/tools/gate.sh full`, about 35 s).
- **Next:** concurrency, decided in
  [obx/design/concurrency.md](obx/design/concurrency.md): coroutines with
  futures inside a process, several processes for several cores, and no
  threads.

## Layout

| Path | Contents |
|---|---|
| [obx/](obx/README.md) | the language, its compiler, library, programs and tests |
| [archive/obo/](archive/obo/README.md) | obo 0.19, the ownership fork obx grew from; retired 2026-11-02 |
| [archive/obx-ref/](archive/obx-ref/README.md) | obx's Python reference compiler; retired 2026-11-02 |
| `archive/obx/` | the original garbage-collected obx dialect and compiler |
| [HISTORY.md](HISTORY.md) | how the project got here: mainline obx, the obo fork and its releases, the new obx |
| [PERFORMANCE.md](PERFORMANCE.md) | obx native, obx via C and obo compared: benchmarks, eza, the compiler |

## History, in short

Mainline obx was a garbage-collected, single-pass, self-hosting compiler. obo
forked it to test whether Oberon's simplicity could manage memory without a
collector and without Rust's complications (single ownership, borrows, checked
handles, after Nick Smith's and Evan Ovadia's
[group borrowing](https://verdagon.dev/blog/valen-group-borrowing)), and
reached 0.19 with modules, methods, interfaces, lambdas and decimals. Each new
kind of value had to be wired through the whole compiler, so on 2026-11-02 the
project restarted as the new obx: obo's ownership model with generics
([the decision](obx/design/generic-core-rethink.md)). obo and the Python
reference compiler that settled the new design are archived; obx's own
compiler is the only one under development.

## License

obx is licensed under the Apache License, Version 2.0. The full text is in
[LICENSE](LICENSE); [NOTICE](NOTICE) records the copyright. Unless a file says
otherwise, the compilers, runtimes, tools, tests and documentation are covered.

Contributions are accepted under the same license; by contributing you agree that
your contribution is licensed under the Apache License, Version 2.0.
