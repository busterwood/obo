# obx

obx is a language for systems programs, the kind that run close to the
machine. Each value has one owner. Other code can borrow a value (`&T`) for
a while. There is no garbage collector, so the compiler decides when memory
is freed. It also has generics.

The compiler is written in obx and compiles itself. It builds programs
straight to x86-64 executables, or through C and gcc.

Most of the library is ordinary obx code, not compiler features.
Decimal, money, `Option`, `Result` and the containers are all library code.

## Quick start

Run these from the repository root. The first build needs only gcc.

```
obx/compiler/build.sh                            # build the compiler from its C seed
obx/compiler/out/obxc prog.obx --native -o prog  # a native x86-64 program, no gcc
obx/compiler/out/obxc prog.obx -o prog           # the same program through C
obx/tools/gate.sh quick                          # run the test suites
```

A small program:

```
fn main() {
    let price = 19.99m
    let total = price * 3.0m
    println("total: " + total.toStr())
    let rate = 0.5f
    println("rate: " + rate.toStr())
}
```

It prints `total: 59.970` and `rate: 0.5`.

Three things to know first:

- A number with a fraction needs a suffix. `1.5m` is a Decimal and `1.5f` is a float.
  A bare `1.5` is a compile error.
- Programs need an x86-64-v3 CPU, which means AVX2 and the instructions
  around it. Intel Haswell (2013) and AMD Excavator (2015) or later have them.
  On an older CPU a program stops with exit code 16.
- The owner frees a value. The compiler checks that borrows never outlive
  the value they lend.

## What is in it

### The compiler

- Two backends share one intermediate form.
  - **Native:** writes an ELF executable with no gcc. The program is static,
    or dynamically linked when it calls C libraries.
  - **C:** writes C and runs gcc.
- The compiler is about 38,000 lines of obx. Under a third of that is
  generated runtime tables.
- Its C is checked in as a seed. A first build needs only gcc.

### The library

The library is in [obx/lib/](obx/lib/README.md). Its main areas:

| Area | Modules |
|---|---|
| Core | `std` (the prelude: `Option`, `Result`, `Decimal`, `Map`), `collections`, `strings`, `money`, `math` |
| Files and time | `io`, `fs`, `os`, `time`, `tz` (time zones) |
| Text and data | `regex` (RE2 syntax, linear time), `json`, `markdown` (CommonMark), `codepage` |
| Network | `net` (TCP), `tls` (OpenSSL 3), `url`, `http` (client), `httpserver` (server) |
| Tasks and processes | `task` (tasks, futures, worker processes), `proc` (other programs) |
| Databases | `sqlite`, `postgres`, `sqlserver`, `mssql`, `oracle`, with `sqlrows` and `sqldialect` |
| C interop | `ffi` (C memory), `crypto` (digests and AES from libcrypto) |

Tasks run concurrently on one thread. A task pauses while it waits, so one
thread can serve many connections. For more cores, `task.workers` forks
worker processes. The design is in
[obx/design/concurrency.md](obx/design/concurrency.md).

### The programs

The programs are in [obx/cmd/](obx/cmd/README.md):

- **eza**: the common subset of eza 0.23.3, including the long view (`-l`, `-lh`).
- **cat, wc, sort, head, tail, grep**: subsets of the GNU tools, with their output and exit codes.
- **zoxide**: the `cd` helper. It reads and writes the real zoxide's database.
- **jq**: a port of jq 1.7.1. It has no modules.
- **cmark**: a CommonMark renderer, like cmark 0.31.2.
- **httpd** and **httpload**: an HTTP/1.1 server and its load client.
- **tlsbench**: a TLS benchmark.

### Database clients

| Client | Database | How it connects |
|---|---|---|
| `sqlite` | SQLite | libsqlite3 |
| `postgres` | PostgreSQL | native, over the protocol 3.0 (`pgwire`): TLS, SCRAM |
| `postgres_libpq` | PostgreSQL | libpq; kept as the test oracle for `postgres` |
| `sqlserver` | SQL Server | native, over the TDS protocol |
| `mssql` | SQL Server | ODBC |
| `oracle` | Oracle Database | native, over the TNS protocol, ported from python-oracledb |

`sqlserver`, `postgres` and `oracle` can stream rows. `conn.stream` decodes
each row into the program's own record. Memory stays flat: it is the same
at 100,000 and at 1,000,000 rows. See
[obx/design/db-streaming.md](obx/design/db-streaming.md).

The Oracle client supports OUT binds, REF CURSORs and TLS.

## Performance

These figures come from [PERFORMANCE.md](PERFORMANCE.md). They were
measured on one 8-core test machine, so compare the columns, not the
absolute numbers.

- **Native code** takes 0.5 to 1.1 times the time of the same program built
  through C, on ten benchmarks.
- **Native Decimal** takes about 1 to 1.4 times C's cycles. It was 2 to 2.5 times.
- **Float math** in the native backend takes within about 15% of C's cycles per call.
- **HTTP server:** one thread serves 65,600 requests a second to 100 clients.
  Go's `net/http` serves 44,500 on the same setup. With 8 worker processes
  the server reaches about 128,000.
- **Database streaming:** 1,000,000 rows take 8 to 14 MB of memory when
  streamed. Buffering the same rows takes 109 to 365 MB.
- **Small programs:** hello world is 16 KB natively, with no libc.

## Where to read more

| Document | What it covers |
|---|---|
| [obx/README.md](obx/README.md) | the language and the layout of the repository |
| [obx/SPEC.md](obx/SPEC.md) | the language definition |
| [obx/PLAN.md](obx/PLAN.md) | the stages, their gates and their state |
| [PERFORMANCE.md](PERFORMANCE.md) | the measured numbers and how they were taken |
| [HISTORY.md](HISTORY.md) | how the project got here, with dated entries |
| [obx/design/](obx/design/) | design notes for each stage and subsystem |
| [obx/lib/README.md](obx/lib/README.md) | the library: every module |
| [obx/cmd/README.md](obx/cmd/README.md) | the programs and their tests |
| [obx/tests/README.md](obx/tests/README.md) | the test suites and the gate |

## Where it came from

obx grew out of two earlier compilers. Both are archived.

- **Mainline obx** ([archive/obx/](archive/obx/)) was the original Oberon
  dialect, with a garbage collector.
- **obo** ([archive/obo/](archive/obo/README.md)) was an ownership fork. Its
  borrowing drew on Nick Smith's and Evan Ovadia's
  [group borrowing](https://verdagon.dev/blog/valen-group-borrowing). It
  reached version 0.19. Every new kind of value had to be wired through the
  whole compiler.

On 2 November 2026 the project restarted as obx. It kept obo's ownership
model and added generics. The full story is in [HISTORY.md](HISTORY.md).

## License

obx is licensed under the Apache License, Version 2.0. The full text is in
[LICENSE](LICENSE); [NOTICE](NOTICE) records the copyright. Unless a file says
otherwise, the compilers, runtimes, tools, tests and documentation are covered.

Contributions are accepted under the same license; by contributing you agree that
your contribution is licensed under the Apache License, Version 2.0.
