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
