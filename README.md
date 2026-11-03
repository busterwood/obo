# obx

obx is a small Oberon-derived systems language with single ownership,
borrowed `&T` references and no garbage collector, and with generics:
Decimal, Money, Option, Result and the containers are ordinary library code,
not compiler features. Its compiler is written in obx, compiles itself, and
builds native x86-64 ELF executables directly or through C.

Start with [obx/README.md](obx/README.md). The language is
[obx/SPEC.md](obx/SPEC.md); the plan and its state are
[obx/PLAN.md](obx/PLAN.md).

```
obx/compiler/build.sh                          # obx/compiler/out/obxc from the C seed (gcc only)
obx/compiler/out/obxc prog.obx --native -o prog
obx/compiler/out/obxc prog.obx -o prog         # through C
obx/tools/gate.sh quick                        # the suites; `full` adds the red-team rounds
```

## Status

As of 2026-11-03:

- **The compiler** is written in obx (about 21,400 lines) and compiles itself
  to a fixed point. One IR feeds two backends: a native x86-64 backend that
  writes ELF executables with no gcc (static, or dynamically linked when a
  program calls C libraries), and C for gcc. The natively built compiler
  builds itself to the same fixed point. Its C is checked in as the seed, so
  a first build needs only gcc.
- **Library:** strings, collections, files and I/O, time, math, TCP, exact
  decimals and money, and [SQLite and Postgres](obx/lib/README.md) adapters
  that call the C libraries directly with no shims.
- **Programs:** [eza](obx/cmd/README.md), the common subset of the eza file
  lister in 1,600 lines, byte-identical to the real eza on 482 golden cases.
- **Performance:** the native backend is level with gcc -O2 or ahead of it
  on every benchmark (fib, loops over arrays, allocation, strings); a
  natively built compiler is about 1.2x slower than gcc's build of it. eza
  lists 3.7 MB of long tree in about 200 ms. The
  figures are in [PERFORMANCE.md](PERFORMANCE.md).
- **Checks:** a conformance suite, the compiler, library and eza tests, a
  native-against-C comparison over 1,540 programs, and five red-team rounds
  under ASan and UBSan (`obx/tools/gate.sh full`, about a minute).
- **Concurrency:** decided in
  [obx/design/concurrency.md](obx/design/concurrency.md): coroutines with
  futures inside a process, several processes for several cores, no
  threads. The first two stages are built: `import "task"` (scopes,
  futures, promises, `Shared`, sleep and deadlines; its traps name the
  user's lines) and `import "net"` (TCP sockets that park the task, on one
  epoll loop). An HTTP/1.1 server example holds 10,000 idle keep-alive
  connections at 13 KB each and serves 69,000 requests/s on one core
  (PERFORMANCE.md). Next: a helper thread pool for blocking C calls, and
  libpq's non-blocking path.

## Layout

| Path | Contents |
|---|---|
| [obx/](obx/README.md) | the language, its compiler, library, programs and tests |
| [PERFORMANCE.md](PERFORMANCE.md) | native and C builds measured: benchmarks, eza, the compiler |
| [HISTORY.md](HISTORY.md) | how the project got here |
| `archive/` | retired predecessors, kept for the record (below) |

## Where it came from

obx grew out of two earlier compilers, both retired and archived:

- **Mainline obx** (`archive/obx/`): the original garbage-collected,
  single-pass, self-hosting Oberon dialect.
- **obo** ([archive/obo/](archive/obo/README.md)): its ownership fork, which
  tested whether Oberon's simplicity could manage memory without a collector
  and without Rust's complications (single ownership, borrows, checked
  handles, after Nick Smith's and Evan Ovadia's
  [group borrowing](https://verdagon.dev/blog/valen-group-borrowing)). It
  reached 0.19 with modules, methods, interfaces, lambdas and decimals, but
  every new kind of value had to be wired through the whole compiler.

On 2026-11-02 the project restarted as today's obx: obo's ownership model
with generics ([the decision](obx/design/generic-core-rethink.md)). A Python
reference compiler settled the design and was then retired
([archive/obx-ref/](archive/obx-ref/README.md)). The full story is in
[HISTORY.md](HISTORY.md).

## License

obx is licensed under the Apache License, Version 2.0. The full text is in
[LICENSE](LICENSE); [NOTICE](NOTICE) records the copyright. Unless a file says
otherwise, the compilers, runtimes, tools, tests and documentation are covered.

Contributions are accepted under the same license; by contributing you agree that
your contribution is licensed under the Apache License, Version 2.0.
