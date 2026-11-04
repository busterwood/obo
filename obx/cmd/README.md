# Programs

## eza

`eza.obx` implements the common subset of the eza 0.23.3 file lister, with
the same output byte for byte. `eza -?` lists what it does; everything else
is rejected with an error, never silently ignored.

It is one file. The parts (argument parsing, deduction, entries and sorting,
colour, the three views) share two types, `Opts` and `Entry`, and nothing in
them is useful to another program yet; a split would only add imports and
`pub`. What is general went to the library instead (`fs.readlink`,
`fs.Kind`, `io.isTerminal`, `io.terminalWidth`, `strings.displayWidth`,
`strings.lossy`, the stdout write errors; see [../lib/README.md](../lib/README.md)).

Build it from `obx/`:

    compiler/out/obxc cmd/eza.obx --native -o eza    # native x86-64
    compiler/out/obxc cmd/eza.obx -o eza             # through C

### Tests

The tests are a golden suite recorded from the real eza 0.23.3, which came
from obo, obx's predecessor: the harness [../tests/eza.py](../tests/eza.py)
with its cases, fixtures and goldens in `../tests/eza/`. It builds
`eza.obx` with `compiler/out/obxc` and runs every case; `eza_gate.py` runs
it with `--reference=none`:

    python3 cmd/eza_gate.py -j 8                 # native build, all cases
    python3 cmd/eza_gate.py --c -j 8             # C build
    python3 cmd/eza_gate.py --exe ./eza -k tree  # a given binary, some cases

Other options go to the harness (`-k PATTERN`, `-x`, `-v`); the reference
defaults to `--reference=none`, so the goldens are the oracle. A full run
takes about 2.5 minutes (`-j 8`), most of it the harness building fixtures.
72 cases are skipped: they need a live eza or a terminal and have no
golden. Our eza runs with `OBX_CHECK=1`, so a leak fails a case.

## cat, wc, sort, head, tail, grep

`cat.obx`, `wc.obx`, `sort.obx`, `head.obx` and `tail.obx` are ports of obo's
small tools (`archive/obo/cmd/`): a deliberate subset of GNU coreutils 9.x
with its output, error messages and exit codes. The header comment of each
file lists the options, the edge cases and what is rejected (`cat -nbsET`,
`wc -lwc`, `sort -nru`, `head -n/-c`, `tail -n/-c/-f` and `--lines`/`--bytes`;
every other option, long ones included, is an error as in obo). Each is one
file. Input goes through `io.Reader` in 64 KiB chunks (a small `Reader`
wrapper in each file keeps the chunk and the unused part of it), file
metadata through `fs.stat` (`tail -f` tells a file by `dev` and `ino`); no
tool declares an `extern`, and the native builds are static with no libc.
`tail` reads a regular file (also as standard input) backwards from its end,
as GNU does, so `tail -n 5` of a 1 GB file takes 3 ms instead of 1.5 s; a
pipe or a terminal is streamed through a ring of the last lines or bytes.
`sort` keeps obo's radix sorts (the default order and the exact `-n` decimal
order) unchanged.

Build from `obx/`:

    compiler/out/obxc cmd/cat.obx --native -o cat    # native x86-64
    compiler/out/obxc cmd/cat.obx -o cat             # through C

### Tests

[../tests/tools.py](../tests/tools.py) is obo's differential gate with its
cases, models and goldens (`../tests/tools/golden/`): every case runs our tool
and GNU coreutils (from `/usr/bin:/bin`) and compares stdout, stderr and the
exit status, and the `usage` and `writeerror` cases are pinned by own goldens.
`tools_gate.py` runs it:

    python3 cmd/tools_gate.py -j 8                    # native build, GNU reference
    python3 cmd/tools_gate.py --c -j 8                # C build
    python3 cmd/tools_gate.py -j 8 --reference=none   # models and goldens only
    python3 cmd/tools_gate.py --memory                # 16 MiB inputs, leak check

Our tools run with `OBX_CHECK=1`, so a leak fails a case. 523 cases pass for
each build against GNU 9.4 (520 with `--reference=none`), and 346 `grep` cases
against GNU grep 3.11 (869 in all; they have no model or golden, so
`--reference=none` skips them). `--memory` replaces
obo's peak-heap budget (which needed `OBO_STATS`) by a clean exit with no
runtime report on the large-input cases (four for the coreutils, four for `grep`).

| Tool | native (static) | through C |
|---|---:|---:|
| `cat` | 45,904 | 73,840 |
| `wc` | 50,016 | 78,096 |
| `sort` | 115,624 | 111,632 |
| `head` | 50,304 | 74,488 |
| `tail` | 71,016 | 92,768 |
| `grep` | 528,000 | 461,640 |

(obo's static binaries were 90 to 119 KB. `grep` is large because it carries
the regex library.)

### grep

`grep.obx` is a new tool, not an obo port: a GNU grep 3.x subset on
[../lib/regex.obx](../lib/regex.obx), tested against GNU grep 3.11. Patterns
are basic (the default, with GNU's `\( \) \{ \} \| \+ \?`), extended (`-E`),
fixed (`-F`) or the library's Perl syntax (`-P`, one pattern). The program
translates a pattern to the library's syntax and compiles it leftmost-longest,
so `-o` prints what GNU prints. Options: `-e -f -i -v -w -x -c -l -L -m -o -q
-s -b -H -h --label -n -Z -A -B -C -NUM -a -I --binary-files -r -R --include
--exclude --exclude-dir --group-separator --no-group-separator`, with GNU's
long names and unique prefixes. Messages, `--` separators, `file:` prefixes and
exit codes (0, 1, 2) are GNU's. A file with a NUL byte is binary: the message
`grep: x: binary file matches` goes to stderr, as in 3.11.

Input is read in 128 KiB chunks and never held whole. The regex runs over a
whole chunk with `(?m)`, so only the lines that match are looked at; a match
that spans a newline (`[[:space:]]`) and every candidate line of `-w` are
checked line by line. A line longer than a chunk is carried to the next one.

Not supported, each with an error or a note in the header comment of the file:
back-references (`\1`, the engine runs in linear time and has none), `--color=always`,
`-T -U -z -d -D`, `--help` text, and a locale (the input is UTF-8; a word is
ASCII letters, digits and `_`; `\<` and `\>` are both `\b`). A file is binary
because of a NUL byte only, never because of an encoding error.

Speed on 100 MB (1.4 million lines) against GNU grep 3.11, in seconds (GNU,
obx native, obx through C):

| Search | GNU | native | C |
|---|---:|---:|---:|
| `needle5007` (a literal) | 0.07 | 0.09 | 0.02 |
| `-c omega` | 0.08 | 0.16 | 0.08 |
| `-v -c omega` | 0.10 | 0.17 | 0.08 |
| `-c '[0-9]*7 alpha'` | 0.07 | 0.35 | 0.24 |
| `-c -E '(alpha\|beta) gamma'` | 0.11 | 0.15 | 0.07 |
| `-c -i NEEDLE` | 0.06 | 0.11 | 0.04 |
| `-c -w eta` | 0.21 | 1.12 | 0.61 |
| `-c -v '^0'` (every line) | 0.05 | 0.47 | 0.32 |
| `-c -E 'a.*z.*e'` | 0.22 | 0.46 | 0.35 |

The slow cases are those where most lines match or need a line-by-line check.

## httpd and httpload

`httpd.obx` is a small HTTP/1.1 server on one thread, the gate of
concurrency stages C1b, C1c and C2 (design/concurrency.md) and since stage
S1 the example program of the server library, `lib/httpserver.obx`
(design/http-server.md; [../lib/README.md](../lib/README.md)). It is about
230 lines: four handlers, a router and a call to `http.serve`. `GET /`
says hello, `GET /stats` gives its counters, `GET /db` (when started with
a pool size, `httpd ADDR POOL`: stage C1c) runs one query through a
`postgres.Pool` of that many connections (`$OBX_PG_DSN`, default the local
socket), and `GET /shutdown` stops it gracefully: the library's drain
closes the listener and the idle connections, lets the requests in flight
finish (with `Connection: close`), and `serve` returns its counters, which
the program prints. `SIGTERM` and `SIGINT` do the same.
[database-example.md](database-example.md) walks through the `/db` code and
how a request flows through the pool and the event loop.

Everything else is the library's: one task per connection, keep-alive,
pipelined requests answered in order, the strict parsing (a request two
parsers could read differently gets a 4xx and the connection is closed),
404 and 405, the head, body, idle and write timeouts, the per-route
deadline (30 s), the cancellation of a handler whose client hangs up (a
`/db` query is then cancelled on the server), and the limits on heads,
bodies and connections (`maxConns` is raised to 30,000 for the gates). The
library's defaults are in [../lib/README.md](../lib/README.md).

Before S1 httpd was a hand-written loop over `lib/http.obx`'s head parser.
On the library it does 64,300 requests/s against 69,200 at 100 clients
(native, the median of seven alternating runs), and an idle connection
costs 15.5 KB of RSS against 13.0 ([../../PERFORMANCE.md](../../PERFORMANCE.md)).

With a third argument, `httpd ADDR POOL WORKERS` (stage C2), it serves from
WORKERS processes under `task.workers`: the listener is made first and
shared by all of them, each has its own event loop, its own counters (so
`/stats` answers for the worker that took the connection, and says which)
and its own Postgres pool, WORKERS × POOL connections in all. `/shutdown`
on any worker, or `SIGTERM` or `SIGINT` to the server, stops every worker
gracefully; each prints its counters, then the supervisor one line per
worker. A crashed worker is started again; with `http.workerPolicy()`
crashes after a worker is serving never make the supervisor give up, so
only a worker that cannot start can stop the server. A fourth argument `reuseport`
gives each worker a listener of its own instead (`SO_REUSEPORT`), for
comparison.

`httpload.obx` is its load client: CONNS keep-alive connections, each a task
sending `GET PATH` (default `/`) one at a time for SECONDS seconds, then one
line with the requests per second and the latency percentiles. A reply
with `Connection: close` (the server's `maxRequestsPerConn`, 1,000) makes
the task connect again; that is not an error. `-p PROCS`
spreads the connections over PROCS processes (`task.workers` again), so
that one client process is not the limit; `-r` reconnects after an error.
With `--client` the tasks send through one `http.Client` per process
(`maxPerHost` its connections) instead, each request a `get` and a
`text()`: the HTTP client's gate. Reaching `dial` makes the native build
dynamically linked (glibc's resolver).

    compiler/out/obxc cmd/httpd.obx --native -o httpd && ./httpd 127.0.0.1:8080
    compiler/out/obxc cmd/httpload.obx --native -o httpload && ./httpload 127.0.0.1:8080 10 5
    ./httpd 127.0.0.1:8080 4 8                    # 8 workers, a pool of 4 each
    ./httpload -p 8 127.0.0.1:8080 256 5 /db

`http_gate.py` is the gate: for the native and the C build (and Go's
net/http and Node's http as references, when installed) it opens 10,000
idle keep-alive connections, measures the server's RSS per connection, runs
httpload with 1, 10 and 100 clients while they stay open, checks that they
still answer, and shuts the server down under `OBX_CHECK=1` (exit 0, no
leak, every connection closed). With `--db 4,16` it runs each obx server
once per pool size with the load on `/db`, and checks after the shutdown
that the pool has nobody waiting. With `--workers 1,2,4,8` it measures the
worker processes instead: requests/s and latency for each count, on `/`
and (with `--db P`) `/db`, the workers and the load pinned to different
cores with `taskset`, then a clean shutdown of every worker; and a kill
test, `SIGKILL` to one worker under load, which must lose only the
requests in flight on its connections and be started again. The numbers
are in [../../PERFORMANCE.md](../../PERFORMANCE.md).

    python3 cmd/http_gate.py                     # about 2 minutes
    python3 cmd/http_gate.py --idle 1000 --servers native,c --seconds 2
    python3 cmd/http_gate.py --servers native,c --db 4,16   # with Postgres
    python3 cmd/http_gate.py --workers 1,2,4,8 --db 4 --listen both   # worker processes
