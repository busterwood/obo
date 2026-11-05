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
| `grep` | 544,672 | 476,488 |
| `zoxide` | 589,536 | 352,496 |
| `jq` | 1,114,456 (libc, libm) | 875,280 |

(obo's static binaries were 90 to 119 KB. `grep` is large because it carries
the regex library. `jq` carries the regex and JSON libraries and jq's
builtins, and links libm for its math builtins.)

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

Input is read in 128 KiB chunks and never held whole. When no match can
hold a newline, the regex's lazy DFA runs over a chunk and reports the lines
with a match, a batch at a time; the other lines are never looked at. A
pattern with a rare required literal (`needle` in `needle[0-9]+`) looks for
that literal first. `-w` is one regex (the word with a non-word character or
a line end on each side), so no line is checked twice. A pattern that can
match a newline (`[[:space:]]`) is checked line by line from each match. A
line longer than a chunk is carried to the next one.

Not supported, each with an error or a note in the header comment of the file:
back-references (`\1`, the engine runs in linear time and has none), `--color=always`,
`-T -U -z -d -D`, `--help` text, and a locale (the input is UTF-8; a word is
ASCII letters, digits and `_`; `\<` and `\>` are both `\b`). A file is binary
because of a NUL byte only, never because of an encoding error.

Speed on 100 MB (1.8 million lines) against GNU grep 3.11, in seconds, best
of 5 (GNU, obx native, obx through C; before is the build at `1c6d06c`):

| Search | GNU | native | C | native before | C before |
|---|---:|---:|---:|---:|---:|
| `needle5007` (a literal) | 0.026 | 0.014 | 0.014 | 0.090 | 0.020 |
| `-c omega` | 0.084 | 0.061 | 0.059 | 0.151 | 0.069 |
| `-v -c omega` | 0.096 | 0.071 | 0.073 | 0.164 | 0.083 |
| `-c '[0-9]*7 alpha'` | 0.070 | 0.015 | 0.015 | 0.352 | 0.231 |
| `-c -E '(alpha\|beta) gamma'` | 0.106 | 0.062 | 0.059 | 0.144 | 0.070 |
| `-c -i NEEDLE` | 0.061 | 0.018 | 0.017 | 0.096 | 0.036 |
| `-c -w eta` | 0.204 | 0.190 | 0.180 | 1.123 | 0.618 |
| `-c -v '^0'` (every line) | 0.047 | 0.048 | 0.042 | 0.468 | 0.326 |
| `-c -E 'a.*z.*e'` | 0.210 | 0.106 | 0.091 | 0.455 | 0.343 |
| `-n omega` | 0.224 | 0.194 | 0.160 | 0.376 | 0.274 |
| `-o 'needle[0-9]*'` | 0.055 | 0.014 | 0.015 | 0.096 | 0.024 |
| `-o -w eta` | 0.386 | 0.330 | 0.298 | 1.653 | 0.902 |
| `-n -w eta` | 0.392 | 0.321 | 0.288 | 1.381 | 0.845 |

Output goes to a pipe (with `/dev/null` as its output, GNU stops at the
first match). One measured case is still behind GNU: `-c -E '^[0-9]+7[0-9]'`
(0.055 native, 0.050 C, 0.040 GNU). The data:

    python3 -c '
    import random; r = random.Random(1); n = 0; i = 0; out = []
    w = "alpha beta gamma delta epsilon zeta eta theta iota kappa lambda mu nu xi omicron pi rho sigma tau upsilon phi chi psi omega".split()
    while n < 10**8:
        l = "%07d " % i + " ".join(r.choice(w) for _ in range(r.randint(4, 14))) + (" needle%d" % i if i % 1000 == 7 else "")
        out.append(l); n += len(l) + 1; i += 1
    print("\n".join(out))' > big.txt

## zoxide

`zoxide.obx` is a port of zoxide 0.9.9, the "smarter cd". It reads and writes
the real zoxide's database (`db.zo`, bincode version 3), so you can switch
between the two at any time. Frecency scores, aging, the keyword rules, the
`_ZO_*` variables, the messages and the exit codes are zoxide's.

    compiler/out/obxc cmd/zoxide.obx --native -o zoxide
    compiler/out/obxc cmd/zoxide.obx -o zoxide

Supported:

- `add [-s SCORE] PATH...`, `remove PATH...`.
- `query [KEYWORD...]` with `-a/--all`, `-l/--list`, `-s/--score`,
  `--exclude PATH`, `--base-dir PATH` and `-i/--interactive`. Keywords match
  in order, the last one in the last path component, ignoring case. Query
  sorts by score and saves the file, removes entries that
  `_ZO_EXCLUDE_DIRS` matches, and removes missing directories unused for
  three months, as zoxide does.
- `init SHELL [--cmd NAME] [--no-cmd] [--hook none|prompt|pwd]` for bash,
  elvish, fish, nushell, posix (and `ksh`), powershell, tcsh, xonsh and zsh.
  The output is byte for byte zoxide's. `zoxide_init.obx` is generated from
  zoxide's askama templates by `zoxide_gen.py`; do not edit it by hand.
- `import --from autojump|z|fasd [--merge] FILE`.
- `_ZO_DATA_DIR` (else `$XDG_DATA_HOME/zoxide`, else `~/.local/share/zoxide`),
  `_ZO_ECHO`, `_ZO_EXCLUDE_DIRS` (zoxide's glob syntax), `_ZO_MAXAGE`
  (aging when the sum of the ranks passes it), `_ZO_RESOLVE_SYMLINKS`,
  `_ZO_FZF_OPTS`. The database is written to a temporary file and renamed.
- Command-line errors, `--help` and `--version` copy clap's text and exit
  status 2, for the cases tested; other errors print `zoxide: message` and the
  causes as anyhow does, with exit status 1. A corrupt database gives zoxide's
  messages.
- The current time is `time.unixSeconds()`; no library function was added.

Not supported or different:

- `edit` (it exits with an error).
- clap's "a similar argument exists" tips.
- Case folding is ASCII only (zoxide lowercases all of Unicode).
- Entries with equal scores keep their file order; zoxide's sort is unstable,
  and agrees only up to 20 entries.
- `query -i` collects every candidate before it starts fzf, which zoxide
  streams.
- The home directory comes from `$HOME`, else `/etc/passwd`.

### Tests

[../tests/zoxide.py](../tests/zoxide.py) is a differential test against the
real zoxide 0.9.9 (outside the gates). Each of its 362 cases is a list of steps
on real temporary directories. Every step runs in two worlds with separate
`_ZO_DATA_DIR`s, ours and the reference, and stdout, stderr, the exit status
and then the whole database (paths, order, ranks bit for bit, times within five
seconds) must be equal. The cases cover add and query ranking, the keyword
rules, excluding the current directory, missing directories, score output and
rounding, remove, aging, every `init` shell with every hook and `--cmd` form, the
environment variables, import, corrupt databases, command-line errors, and
cross-compatibility (a step with `slot=1` runs the reference where the
others run ours, so one database is written by both). The clock cannot be
set, so scores come from databases written with fixed ages.

    python3 tests/zoxide.py --ref /path/to/zoxide -j 8        # native build
    python3 tests/zoxide.py --c --ref /path/to/zoxide -j 8    # C build

Install the reference into a scratch directory, not `~/.cargo/bin`:
`CARGO_INSTALL_ROOT=/tmp/zoxide-ref cargo install zoxide --locked --version
0.9.9` (0.10 needs a newer rustc). Our binary runs with `OBX_CHECK=1`.
Sizes in bytes: `zoxide` is 589,536 native (static) and 352,496 through C.

## jq

`jq.obx` is a port of jq 1.7.1, the command-line JSON processor, built on
[../lib/json.obx](../lib/json.obx) and [../lib/regex.obx](../lib/regex.obx).
It is split into five files:

| File | Contents |
|---|---|
| `jq.obx` | the command line |
| `jq_parse.obx` | the lexer, the parser (parser.y's grammar and precedence) and name resolution |
| `jq_eval.obx` | the evaluator |
| `jq_lib.obx` | value operations, formats, dates, math, regexes and jq's JSON error messages |
| `jq_builtins.obx` | jq's `builtin.jq`, with jq's MIT notice |

    compiler/out/obxc cmd/jq.obx --native -o jq
    compiler/out/obxc cmd/jq.obx -o jq

### Supported

- **Options:** `-n -r -j -a -c -s -e -S -R -C -M -f`, `--tab`,
  `--indent n`, `--arg`, `--argjson`, `--slurpfile`, `--rawfile`, `--args`,
  `--jsonargs`, `--unbuffered`, `--exit-status`, `-h`, `-V` and
  `--build-configuration`. They parse as jq's `main.c` does, including
  combined short options. The help text, messages and exit codes (0, 1, 2,
  3, 4, 5, and `halt_error`'s) are jq's.
- **Control and binding:**
  - paths and assignments: `=`, `|=`, `+=` and the other arithmetic
    updates, `//=`, `del`, `paths`, `getpath`, `setpath`, `delpaths`;
  - `reduce`, `foreach`, `label`/`break`, `try`/`catch` and `?`;
  - destructuring, with `?//` alternatives;
  - `def` with closure and `$` parameters, and recursion;
  - string interpolation with formats;
  - `$__loc__`, `$ENV`, `$ARGS`, `input`, `inputs` and `input_filename`.
- **Builtins:** all 220 names that `builtins` lists, with jq's error
  texts. They include:
  - the formats `@text @json @html @uri @csv @tsv @sh @base64 @base64d`;
  - dates (`strptime`, `strftime`, `mktime`, `gmtime`, `todate` and the
    rest);
  - the math library;
  - `tostream`, `fromstream` and `truncate_stream`;
  - `test`, `match`, `capture`, `scan`, `splits` and `sub`/`gsub`, with
    named captures and every flag (`g i x n s p l`).
- **Numbers** keep their literal, as in jq 1.7: `100000000000000000001`
  and `1.000` print as written, in decNumber's canonical form.
- **Laziness:** generators are lazy, so `first(range(1; infinite))`,
  `limit(3; repeat(1))` and `label $out | ...` stop as soon as they have
  their outputs. The header of `jq_eval.obx` explains how.
- **Input errors:** invalid JSON input gives jq's messages, positions and
  exit status.

### Not supported or different

- **Not supported:** modules (`import`, `include`, `modulemeta`, `-L`),
  `--stream`, `--stream-errors`, `--seq` and `--raw-output0`. A program
  that uses `import` or `include` gets jq's "module not found" error; the
  options say they are not supported and exit with status 2.
- **Colours:** `-C` is accepted but writes no colours.
- **Regular expressions** are RE2's syntax (lib/regex), not Oniguruma's:
  - no lookaround and no backreferences;
  - `\b` and `\w` are ASCII.

  Such patterns fail with a "Regex failure" error, where jq would match.
  Error messages for the common pattern errors use Oniguruma's wording.
- **Time zones:** `localtime`, `strflocaltime` and `%Z` read only the name
  at the start of `$TZ`, not the tz database, so local time is UTC.
- **Constant folding:** jq folds constant object keys, so `{(1+1): 3}` is a
  compile error in jq and a runtime error here.
- **Input:** standard input and files are read whole before parsing starts,
  rather than streamed.

### Tests

[../tests/jq.py](../tests/jq.py) is a differential test against the real
jq 1.7.1, outside the gates, modelled on `zoxide.py`. Every case runs both
programs with the same arguments, standard input and files (in a fresh
directory, with `TZ=UTC` and `LC_ALL=C`). It wants the same exit status,
standard output and standard error, with two normalisations: the
program's own path, and the text after "Regex failure: ". There are two
kinds of case:

- 583 programs run as `jq -nc PROGRAM`;
- 286 command-line cases, which cover the options, inputs, files, errors
  and exit codes.

`--jqtest` also fetches jq's own `tests/jq.test` and runs it through the
command line, comparing outputs as values (`--jqtest-file` takes a local
copy instead).

    python3 tests/jq.py --ref /path/to/jq -j 8                 # native build
    python3 tests/jq.py --c --ref /path/to/jq -j 8             # C build
    python3 tests/jq.py --ref /path/to/jq -j 8 --jqtest        # and jq.test

Build the reference from the release tarball into a scratch directory
(`./configure --with-oniguruma=builtin --prefix=/tmp/jq-ref && make
install`). Our binary runs with `OBX_CHECK=1`, so a leak fails a case.

Results on both builds:

- 869 of 869 cases pass.
- jq.test: 430 of 430 pass. The 17 tests that use modules are counted
  apart; 1 of them passes.
- jq's other test files (run the same way):

| File | Passed | Notes |
|---|---:|---|
| `man.test` | 222 of 224 | the reference gets the same 222 |
| `onig.test` | 38 of 40 | a lookahead and a Unicode `\b` |
| `manonig.test` | 17 of 17 | |
| `optional.test` | 3 of 3 | |
| `base64.test` | 7 of 7 | |

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
