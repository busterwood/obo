#!/usr/bin/env python3
"""Source-module import and compile matrix (P1/P2).

    python3 obo/tests/imports.py

Cases:
  * every stdlib module (io, strings, paths, sort, time, cli, fmt, errors)
    compiles through a tiny importer root built with `-I obo/lib`;
  * every tool source (cmd/cat.obx, wc.obx, sort.obx) compiles with its
    own relative `../lib/...` imports and no -I;
  * the design 4.2 P2 tool-matrix checks: every tool import is an explicit
    `../lib/*.obx` path (a cmd/-local decoy with a library module's name is
    ignored), `cmd/sort.obx` imports no sort module, and a scratch cmd/ tree
    with io/fmt/errors/strings decoys beside cat.obx still builds and runs
    against the real lib/ modules;
  * obo/lib holds exactly the P2 module set: io, strings, paths, sort, time,
    cli, fmt and errors. The exact-set check proves env.obx and fs.obx are not
    part of P1/P2 (and that no leftover compatibility module exists);
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
task requires every tool to compile, so the tools are added here (additive);
P2 keeps them, now under cmd/, and adds fmt/errors to the stdlib importers.
"""
import atexit
import os
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
FORK = os.path.dirname(HERE)                      # the obo tree: --rt base
LIBDIR = os.path.join(FORK, 'lib')
PROGDIR = os.path.join(FORK, 'cmd')
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


def check(name, ok, detail=''):
    """A direct assertion (design 4.2 P2 static tool-matrix checks)."""
    global passed
    if not ok:
        failures.append(f"{name}: {detail}")
        return
    passed += 1
    print(f"ok   {name}")


IMPORTERS = {
    'io': ('import "io.obx" as io\n'
           'fn main() { println(io::clampChunk(4)) }\n', '4\n'),
    'strings': ('import "strings.obx" as strings\n'
                'fn main() { println(strings::cmp("a", "b")) }\n', '-1\n'),
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
    'fmt': ('import "fmt.obx" as fmt\n'
            'fn main() { println(fmt::digits(123)) }\n', '3\n'),
    'errors': ('import "errors.obx" as errors\n'
               'fn main() { println(errors::text(2)) }\n',
               'No such file or directory\n'),
}

TOOLS = ['cat.obx', 'wc.obx', 'sort.obx']

WANT_LIB = {'cli.obx', 'errors.obx', 'fmt.obx', 'io.obx', 'paths.obx',
            'sort.obx', 'strings.obx', 'time.obx'}


def main():
    global passed
    if not os.path.exists(OBXC):
        print(f"no compiler at {OBXC}: run make first")
        sys.exit(2)

    # The exact P1/P2 lib module set: env/fs belong to later stages.
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

    # design 4.2 P2: the tool imports are the explicit ../lib paths, so no
    # same-named file in cmd/ can capture one; sort imports no sort module.
    tool_text = {}
    for name in TOOLS:
        tool_text[name] = open(os.path.join(PROGDIR, name)).read()
    wrong = []
    for name in TOOLS:
        for m in re.finditer(r'^import\s+"([^"]+)"', tool_text[name], re.M):
            path = m.group(1)
            if not (path.startswith('../lib/') and path.endswith('.obx')):
                wrong.append(f'{name}: {path}')
    check('tool imports are ../lib paths', not wrong, ', '.join(wrong))
    check('sort.obx imports no sort module',
          'import "sort.obx"' not in tool_text['sort.obx'],
          'cmd/sort.obx must not import sort.obx')

    # design 4.2 P2: a cmd/-local decoy with a library module's name must not
    # capture the tool's `../lib/...` import. Copy cmd/cat.obx and lib/*.obx into
    # a scratch tree, add io/fmt/errors/strings decoys beside the tool, compile
    # with no -I and run it: only the real lib modules can satisfy cat.
    decoy_root = workdir()
    decoy_cmd = os.path.join(decoy_root, 'cmd')
    os.makedirs(decoy_cmd)
    shutil.copytree(LIBDIR, os.path.join(decoy_root, 'lib'))
    shutil.copyfile(os.path.join(PROGDIR, 'cat.obx'),
                    os.path.join(decoy_cmd, 'cat.obx'))
    for mod in ('io', 'fmt', 'errors', 'strings'):
        write(decoy_cmd, mod + '.obx',
              f'module {mod}\npub fn marker(): int {{ return 99 }}\n')
    exe = os.path.join(workdir(), 'exe')
    p = compile_with(os.path.join(decoy_cmd, 'cat.obx'), exe)
    if p.returncode != 0:
        check('decoy beside cmd/ tool ignored', False,
              f'cat.obx did not compile: {p.stderr.strip()[:300]}')
    else:
        r = subprocess.run([exe], input=b'x', capture_output=True,
                           timeout=120)
        check('decoy beside cmd/ tool ignored',
              r.returncode == 0 and r.stdout == b'x',
              f'exit {r.returncode}, stdout {r.stdout!r}, '
              f'stderr {r.stderr[:200]!r}')

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
    total = 1 + len(IMPORTERS) + len(TOOLS) + 3 + 7
    print(f"\n{passed}/{total} import/compile checks passed "
          f"({len(IMPORTERS)} stdlib importers, {len(TOOLS)} tools, "
          f"3 static tool-matrix checks, 7 collision/decoy trees, "
          f"1 lib set), {len(failures)} failures")
    sys.exit(1 if failures else 0)


if __name__ == '__main__':
    main()
