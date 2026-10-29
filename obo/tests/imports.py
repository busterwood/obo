#!/usr/bin/env python3
"""P1 import and compile matrix.

    python3 obo/tests/imports.py

Cases:
  * every stdlib module (io, strings, paths, sort, time, cli) compiles through
    a tiny importer root built with `-I obo/lib`;
  * every tool source (programs/cat.obx, wc.obx, sort.obx) compiles with its
    own relative `../lib/...` imports and no -I;
  * obo/lib holds exactly the P1 module set: io, strings, paths, sort, time and
    cli. The exact-set check proves env.obx, fs.obx, fmt.obx and errors.obx are
    not part of P1;
  * collision/decoy trees pin module resolution and the exact diagnostics:
    a headerless imported module, a self import cycle, a mutual import cycle,
    a headerless local decoy shadowing a lib module, a headed local decoy with
    the wrong module name, a positive local shadow that must win over `-I`, and
    an absent member of a real module.
The error trees run with the compiler's cwd set to the tree itself and a
relative source name, so the pinned diagnostics contain the relative file
names ("sort.obx:1:1: ..."), exactly as a user would see them. Every compile
uses its own --sym-dir under the test temp dir; nothing is written outside it.

Exit 1 when any case fails; a failing case prints the compiler's own message.
A compile matrix superset: design 4.2 lists no tool sources for P1, but the P1
task requires every tool to compile, so the tools are added here (additive).
"""
import atexit
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
FORK = os.path.dirname(HERE)                      # the obo tree: --rt base
LIBDIR = os.path.join(FORK, 'lib')
PROGDIR = os.path.join(FORK, 'programs')
OBXC = os.path.abspath(os.environ.get('OBO', os.path.join(FORK, 'obo')))
TMP = tempfile.mkdtemp(prefix='obo-imports-')
atexit.register(shutil.rmtree, TMP, ignore_errors=True)

failures = []
passed = 0


def symdir():
    return tempfile.mkdtemp(prefix='sym-', dir=TMP)


def workdir():
    return tempfile.mkdtemp(prefix='case-', dir=TMP)


def write(directory, name, text):
    path = os.path.join(directory, name)
    with open(path, 'w') as f:
        f.write(text)
    return path


def tree(files):
    d = workdir()
    for name, text in files.items():
        write(d, name, text)
    return d


def compile_with(src, exe, extra=(), cwd=None):
    cmd = [OBXC, src, '-o', exe, '--rt', FORK, '--sym-dir', symdir(), *extra]
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True,
                          timeout=120)


def good(name, src, extra=(), cwd=None, run=False, want_out=None):
    global passed
    exe = os.path.join(workdir(), 'exe')
    p = compile_with(src, exe, extra, cwd)
    if p.returncode != 0:
        failures.append(f"{name}: did not compile (exit {p.returncode}): "
                        f"{p.stderr.strip()[:400]}")
        return
    if run:
        r = subprocess.run([exe], capture_output=True, text=True, timeout=120)
        if r.returncode != 0:
            failures.append(f"{name}: program exit {r.returncode}: "
                            f"{r.stderr.strip()[:300]}")
            return
        if want_out is not None and r.stdout != want_out:
            failures.append(f"{name}: stdout {r.stdout!r}, want {want_out!r}")
            return
    passed += 1
    print(f"ok   {name}")


def bad(name, directory, src, want, extra=()):
    global passed
    exe = os.path.join(workdir(), 'exe')
    p = compile_with(src, exe, extra, cwd=directory)
    if p.returncode != 1:
        failures.append(f"{name}: exit {p.returncode}, want 1; "
                        f"stderr {p.stderr.strip()[:400]!r}")
        return
    if want not in p.stderr:
        failures.append(f"{name}: stderr {p.stderr.strip()[:400]!r} does not "
                        f"contain {want!r}")
        return
    passed += 1
    print(f"ok   {name} (exit 1)")


IMPORTERS = {
    'io': ('import "io.obx" as io\n'
           'fn main() { println(io::clampChunk(4)) }\n', '4\n'),
    'strings': ('import "strings.obx" as strings\n'
                'fn main() { println(strings::digits(123)) }\n', '3\n'),
    'paths': ('import "paths.obx" as paths\n'
              'fn main() { println(paths::isAbsolute("/x")) }\n', 'true\n'),
    'sort': ('import "sort.obx" as sort\n'
             'fn main() {\n'
             '    let o = sort::identity(2)\n'
             '    println(len(o))\n'
             '}\n', '2\n'),
    'time': ('import "time.obx" as time\n'
             'fn main() { println(time::floorDiv(-1, 2)) }\n', '-1\n'),
    'cli': ('import "cli.obx" as cli\n'
            'fn main() { println(cli::longName("--a=b")) }\n', 'a\n'),
}

TOOLS = ['cat.obx', 'wc.obx', 'sort.obx']

WANT_LIB = {'cli.obx', 'io.obx', 'paths.obx', 'sort.obx', 'strings.obx',
            'time.obx'}


def main():
    global passed
    if not os.path.exists(OBXC):
        print(f"no compiler at {OBXC}: run make first")
        sys.exit(2)

    # The exact P1 lib module set: env/fs/fmt/errors must not exist yet.
    got = {f for f in os.listdir(LIBDIR) if f.endswith('.obx')}
    if got != WANT_LIB:
        missing = sorted(WANT_LIB - got)
        extra = sorted(got - WANT_LIB)
        failures.append(f"lib module set: missing {missing}, "
                        f"unexpected {extra}")
    else:
        passed += 1
        print(f"ok   lib module set ({len(WANT_LIB)} modules)")

    importers = workdir()
    for mod in sorted(IMPORTERS):
        text, want_out = IMPORTERS[mod]
        src = write(importers, f'{mod}_root.obx', text)
        good(f'import {mod}', src, extra=['-I', LIBDIR], run=True,
             want_out=want_out)

    for name in TOOLS:
        good(f'tool {name}', os.path.join(PROGDIR, name))

    headerless = tree({
        'main.obx': 'import "a.obx" as a\nfn main() { println("x") }\n',
        'a.obx': 'fn helper(): int { return 1 }\n',
    })
    bad('headerless import', headerless, 'main.obx',
        "a.obx:1:1: imported module 'a.obx' needs a 'module Name' header")

    selfcycle = tree({
        'main.obx': 'import "self.obx" as s\nfn main() { println(s::f()) }\n',
        'self.obx': 'module self\nimport "self.obx" as me\n'
                    'pub fn f(): int { return 1 }\n',
    })
    bad('self cycle', selfcycle, 'main.obx',
        'self.obx:2:1: import cycle: self.obx -> self.obx')

    mutual = tree({
        'main.obx': 'import "m1.obx" as a\nfn main() { println(a::f()) }\n',
        'm1.obx': 'module m1\nimport "m2.obx" as m\n'
                  'pub fn f(): int { return m::g() }\n',
        'm2.obx': 'module m2\nimport "m1.obx" as m\n'
                  'pub fn g(): int { return 1 }\n',
    })
    bad('mutual cycle', mutual, 'main.obx',
        'm2.obx:2:1: import cycle: m1.obx -> m2.obx -> m1.obx')

    decoy = tree({
        'main.obx': 'import "sort.obx" as s\nfn main() { println(s::marker()) }\n',
        'sort.obx': 'fn marker(): int { return 9 }\n',
    })
    bad('headerless decoy', decoy, 'main.obx',
        "sort.obx:1:1: imported module 'sort.obx' needs a 'module Name' header",
        extra=['-I', LIBDIR])

    misnamed = tree({
        'main.obx': 'import "sort.obx" as s\nfn main() { println(s::marker()) }\n',
        'sort.obx': 'module decoy\npub fn marker(): int { return 9 }\n',
    })
    bad('misnamed decoy', misnamed, 'main.obx',
        "sort.obx:1:1: module 'decoy' must be in a file named decoy.obx "
        "(found sort.obx)", extra=['-I', LIBDIR])

    # A local module with the right name wins over the lib module: the program
    # must print the local marker (7), not use lib/sort.obx.
    shadow = tree({
        'main.obx': 'import "sort.obx" as s\nfn main() { println(s::marker()) }\n',
        'sort.obx': 'module sort\npub fn marker(): int { return 7 }\n',
    })
    good('local shadow wins', 'main.obx', extra=['-I', LIBDIR], cwd=shadow,
         run=True, want_out='7\n')

    absent = tree({
        'main.obx': 'import "paths.obx" as p\n'
                    'fn main() { println(p::base("a/b")) }\n',
    })
    bad('absent member', absent, 'main.obx',
        "module 'paths' has no exported member 'base' "
        "(only 'pub' declarations are visible)", extra=['-I', LIBDIR])

    for f in failures:
        print('FAIL', f)
    total = 1 + len(IMPORTERS) + len(TOOLS) + 7
    print(f"\n{passed}/{total} import/compile checks passed "
          f"({len(IMPORTERS)} stdlib importers, {len(TOOLS)} tools, "
          f"7 collision/decoy trees, 1 lib set), {len(failures)} failures")
    sys.exit(1 if failures else 0)


if __name__ == '__main__':
    main()
