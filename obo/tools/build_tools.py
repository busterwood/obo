#!/usr/bin/env python3
"""Build the obo command-line tools as source-module roots.

    python3 obo/tools/build_tools.py [TOOL ...] [--out DIR] [--obo PATH] [--rt DIR]

Each tool source under cmd/ is compiled directly by the ordinary compiler;
there is no concatenation. The tools import from ../lib (io, strings, fmt and
errors, as needed), so the compiler compiles those modules
dependency-first and writes their .sym files. The rule within a file is still
declare-before-use.

The default tools are cat, wc and sort. Paths resolve to absolute paths, so the
builder's behavior does not depend on the caller's cwd: ROOT is the directory
holding this script's tools/ (that is, obo/); --obo defaults to $OBO or
ROOT/obo, --rt to ROOT, --out to ROOT/build/tools. The built binaries live under
--out, and every requested tool must compile for the exit status to be 0.

Every build gets its own symbol directory, <out>/sym, which the builder creates:
symbol-file writes truncate in place, so a shared writable directory across
concurrent builds is forbidden. Callers that build concurrently pass distinct
--out directories (tests/tools.py and tests/io_test.py use private temporary
ones), and the generated directory is ignored with the rest of build output.

There is no cache and no --force: rebuilding three tools is cheap, and a cache
would add staleness bugs. A compile failure is reported with the compiler's
message, with absolute paths under ROOT shortened to repo-relative names (so an
error in a tool names cmd/<tool>.obx at its real line); the builder exits 2.
The phase-2 probe compiles (does not run) a tiny well-typed program that names
every incremental-I/O builtin; a failure means phase 2 (openFile, readFd,
writeFd, closeFile, stdin, stdout, stderr and lib/io.obx) is missing.

tests/tools.py imports build() to make its private temporary build, so the build
rule lives in exactly one place.
"""
import argparse
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # .../obo
TOOLS = ('cat', 'wc', 'sort')
SRCDIR = 'cmd'                       # the tool roots live in obo/cmd/
LIB_MODULES = ('io.obx', 'strings.obx', 'fmt.obx', 'errors.obx')

PROBE = '''// phase-2 builtin probe: compile only, never run
fn main() {
    let out = stdout()
    let err = stderr()
    let inp = stdin()
    let data = new []byte(1)
    data[0] = 'x'
    let wr = writeFd(out, data)
    match wr {
        ok { }
        err { }
    }
    let rd = readFd(inp, 1)
    match rd {
        ok { }
        err { }
    }
    let o = openFile("probe", "r")
    match o {
        ok {
            let fd = o.value
            let c = closeFile(fd)
            match c {
                ok { }
                err { }
            }
        }
        err { }
    }
    println(err)
}
'''


class BuildError(Exception):
    """A setup or compile failure; the caller reports it and exits 2."""


def _read(path):
    try:
        with open(path, 'r', encoding='utf-8', newline='') as f:
            return f.read()
    except OSError as e:
        raise BuildError(f"cannot read {path}: {e.strerror}")


def _symdir(out):
    """The private symbol directory for one build output."""
    d = os.path.join(out, 'sym')
    os.makedirs(d, exist_ok=True)
    return d


def _compile(obo, src, out, rt, symdir):
    p = subprocess.run([obo, src, '-o', out, '--rt', rt, '--sym-dir', symdir],
                       capture_output=True, text=True)
    return p.returncode, p.stdout, p.stderr


def _map_error(stderr):
    """Shorten absolute paths under ROOT to repo-relative names, so a diagnostic
    names the real source file and line the way a reader sees the tree."""
    prefix = ROOT + os.sep
    return '\n'.join(line.replace(prefix, '') for line in stderr.splitlines()) + \
        ('\n' if stderr else '')


def probe(obo, rt, out):
    """Compile the well-typed phase-2 builtin probe; BuildError if it fails."""
    os.makedirs(out, exist_ok=True)
    symdir = _symdir(out)
    src = os.path.join(out, '.phase2_probe.obo')
    exe = os.path.join(out, '.phase2_probe')
    with open(src, 'w', encoding='utf-8', newline='') as f:
        f.write(PROBE)
    try:
        rc, _, err = _compile(obo, src, exe, rt, symdir)
    finally:
        for p in (src, exe):
            try:
                os.unlink(p)
            except OSError:
                pass
    if rc != 0:
        first = err.strip().splitlines()[0] if err.strip() else f"exit {rc}"
        raise BuildError(
            "phase 2 (incremental I/O) is a prerequisite: the builtin probe failed "
            f"to compile: {first}")


def build(names, out, obo, rt):
    """Build the named tools into out; return their absolute paths.

    Raises BuildError for a missing compiler/runtime/source or a compile failure."""
    if not names:
        raise BuildError("no tools requested")
    for name in names:
        if name not in TOOLS:
            raise BuildError(f"unknown tool '{name}' (choose from {', '.join(TOOLS)})")
    for name in names:
        _read(os.path.join(ROOT, SRCDIR, name + '.obx'))
        for module in LIB_MODULES:
            path = os.path.join(ROOT, 'lib', module)
            if not os.path.isfile(path):
                raise BuildError(
                    "phase 2 (incremental I/O) is a prerequisite: lib/io.obx is missing"
                    if module == 'io.obx' else f"library module missing: lib/{module}")
            _read(path)
    if not os.path.isfile(obo) or not os.access(obo, os.X_OK):
        raise BuildError(f"compiler not found or not executable: {obo}")
    for f in ('runtime_text.bin', 'runtime_data.bin'):
        if not os.path.isfile(os.path.join(rt, f)):
            raise BuildError(f"runtime file missing: {os.path.join(rt, f)}")

    os.makedirs(out, exist_ok=True)
    symdir = _symdir(out)
    probe(obo, rt, out)

    built = []
    for name in names:
        src = os.path.join(ROOT, SRCDIR, name + '.obx')
        exe = os.path.join(out, name)
        rc, _, err = _compile(obo, src, exe, rt, symdir)
        if rc != 0:
            mapped = _map_error(err)
            if not mapped.strip():
                mapped = f"compiler exited {rc} with no message"
            raise BuildError(f"{name}: compile failed\n{mapped.rstrip()}")
        if not os.path.isfile(exe):
            raise BuildError(f"{name}: the compiler reported success but produced no {exe}")
        built.append(exe)
    return built


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('tools', nargs='*', default=list(TOOLS),
                    help=f"tools to build (default: {' '.join(TOOLS)})")
    ap.add_argument('--out', default=os.path.join(ROOT, 'build', 'tools'))
    ap.add_argument('--obo', default=os.environ.get('OBO') or os.path.join(ROOT, 'obo'))
    ap.add_argument('--rt', default=ROOT)
    a = ap.parse_args(argv)
    names = a.tools or list(TOOLS)
    try:
        built = build(names, os.path.abspath(a.out), os.path.abspath(a.obo),
                      os.path.abspath(a.rt))
    except BuildError as e:
        print(f"build_tools.py: {e}", file=sys.stderr)
        return 2
    for exe in built:
        print(f"built {exe}")
    return 0


if __name__ == '__main__':
    sys.exit(main())
