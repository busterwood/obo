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

## httpd and httpload

`httpd.obx` is a small HTTP/1.1 server on one thread, the gate of
concurrency stage C1b (design/concurrency.md): one task per connection,
keep-alive by default, pipelined requests answered in order. `GET /` says
hello, `GET /stats` gives its counters, `GET /db` (when started with a
pool size, `httpd ADDR POOL`: stage C1c) runs one query through a
`postgres.Pool` of that many connections (`$OBX_PG_DSN`, default the local
socket), and `GET /shutdown` stops it
gracefully: the accept loop is cancelled and with it every connection's
task (an idle one is parked in `read`, which returns `ECANCELED`), every
socket is closed by its drop, and the program prints its counters and ends.
The connection tasks are started with `Scope.spawn`, so that nobody has to
keep their futures. It has no idle timeout (a `task.within` around the
read of a request head would be one).

`httpload.obx` is its load client: CONNS keep-alive connections, each a task
sending `GET PATH` (default `/`) one at a time for SECONDS seconds, then one
line with the requests per second and the latency percentiles.

    compiler/out/obxc cmd/httpd.obx --native -o httpd && ./httpd 127.0.0.1:8080
    compiler/out/obxc cmd/httpload.obx --native -o httpload && ./httpload 127.0.0.1:8080 10 5

`http_gate.py` is the gate: for the native and the C build (and Go's
net/http and Node's http as references, when installed) it opens 10,000
idle keep-alive connections, measures the server's RSS per connection, runs
httpload with 1, 10 and 100 clients while they stay open, checks that they
still answer, and shuts the server down under `OBX_CHECK=1` (exit 0, no
leak, every connection closed). With `--db 4,16` it runs each obx server
once per pool size with the load on `/db`, and checks after the shutdown
that the pool has nobody waiting. The numbers are in
[../../PERFORMANCE.md](../../PERFORMANCE.md).

    python3 cmd/http_gate.py                     # about 2 minutes
    python3 cmd/http_gate.py --idle 1000 --servers native,c --seconds 2
    python3 cmd/http_gate.py --servers native,c --db 4,16   # with Postgres
