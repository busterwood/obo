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

As of 2026-11-04:

- **The compiler** is written in obx (about 23,400 lines) and compiles itself
  to a fixed point. One IR feeds two backends: a native x86-64 backend that
  writes ELF executables with no gcc (static, or dynamically linked when a
  program calls C libraries), and C for gcc. The natively built compiler
  builds itself to the same fixed point. Its C is checked in as the seed, so
  a first build needs only gcc.
- **Library:** strings, collections, files and I/O, time, math, TCP with
  name lookup, URLs, an [HTTP/1.1 client](obx/lib/README.md) (pooled
  keep-alive connections, strict reply framing, redirects, deadlines; plain
  http for now), an [HTTP/1.1 server library](obx/lib/README.md#httpserver)
  (Go 1.22 routing, middleware, strict parsing against request smuggling,
  the limits and timeouts against slow and flooding clients, cancellation
  of a handler and its database query when the client hangs up, graceful
  drain, worker processes), regular expressions (RE2 syntax, linear time: Go's `regexp`
  ported, with RE2's lazy DFA and literal prefilters added; 2 to 650 times
  faster than Go's and, through C, 1.3 to 2.6 times Rust's `regex`), exact decimals and money, and [SQLite, Postgres and SQL
  Server](obx/lib/README.md) adapters that call the C libraries (SQL Server
  through ODBC) directly with no shims.
- **Programs:** [eza](obx/cmd/README.md), the common subset of the eza file
  lister in 1,600 lines, byte-identical to the real eza on 482 golden cases;
  `cat`, `wc`, `sort`, `head` and `tail` (a GNU coreutils subset, ported from
  obo), matching GNU coreutils 9.4 on 523 cases with both backends; and
  `grep`, a GNU grep subset on the regex library, matching GNU grep 3.11 on
  346 cases; and `zoxide`, a port of the smarter-cd tool that reads and
  writes the real zoxide's database, matching zoxide 0.9.9 on 362 cases.
- **Performance:** the native backend is level with gcc -O2 or ahead of it
  on nine of ten benchmarks (fib, loops over arrays, the sieve, allocation,
  strings); maps, with keyed hashing, take 1.37 times gcc's time, the regex
  engine's inner loops 1.4 to 1.8 times, and a natively built compiler
  about 1.2 times. A native executable carries only the code and runtime
  parts it reaches, with no libc: hello is 16 KB, eza 250 KB, cat 46 KB. eza lists
  3.7 MB of long tree in about 200 ms. The figures are in
  [PERFORMANCE.md](PERFORMANCE.md).
- **Checks:** a conformance suite, the compiler, library, eza and tool tests, a
  native-against-C comparison over 1,616 programs, and five red-team rounds
  under ASan and UBSan (`obx/tools/gate.sh full`, about 2.5 minutes).
- **Concurrency:** decided in
  [obx/design/concurrency.md](obx/design/concurrency.md): coroutines with
  futures inside a process, several processes for several cores, no
  threads for obx code. Stage C1 is built: `import "task"` (scopes,
  futures, promises, `Shared`, sleep and deadlines; its traps name the
  user's lines), `import "net"` (TCP sockets that park the task, on one
  epoll loop), and `blocking` C calls that park the task while a helper
  thread runs only the C function (SQLite's queries); Postgres queries park
  on libpq's socket, with a connection pool. The HTTP/1.1 server library
  holds 10,000 idle keep-alive connections at 15 KB each and serves 63,000
  requests/s on one core (Go's net/http 44,000), or 22,000 with a Postgres
  query each through a pool (PERFORMANCE.md). Stage C2 is built too:
  `task.workers` forks worker processes under a supervisor that restarts a
  crashed one and drains them all on `SIGTERM`, signals arrive as events of
  the loop, and a listener made before the fork is shared by the workers.
  With 4 workers the server serves 128,000 requests/s (the machine's
  loopback limit with the load on the other cores), with 8 workers 63,000
  with a Postgres query each; a worker killed under load loses only the
  requests in flight on its connections and is back in about 110 ms.

## Layout

| Path | Contents |
|---|---|
| [obx/](obx/README.md) | the language, its compiler, library, programs and tests |
| [PERFORMANCE.md](PERFORMANCE.md) | native and C builds measured: benchmarks, regex, eza, the compiler, binary sizes, tasks, the HTTP server |
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
