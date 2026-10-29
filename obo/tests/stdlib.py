#!/usr/bin/env python3
"""P1 stdlib tests: every pub API of strings, paths, sort, time and cli.

    python3 obo/tests/stdlib.py

Each tests/stdlib/*.obx file is a headerless test root compiled with
`-I obo/lib`, the obo tree as --rt, and a private --sym-dir. Roots import
support.obx from their own directory, so the module resolution exercised is
the real importer-directory-first one. A root prints its single success line;
the // expect: markers are compared in order and // expect-exit: sets the
expected exit code (default 0). A root marked
    // expect-compile-error: <substring>
must fail to compile with exit 1 and that substring on stderr (the explicit
absence tests for paths::base/dir/stem/clean).

Files with a `module Name` header (support.obx) are support modules, not
cases; they are compiled through each root that imports them. The discovered
root and module filenames are compared against EXPECTED_ROOTS and
EXPECTED_MODULES, so a case that accidentally gains a module header (and thus
silently stops running) fails the run. All programs run
under OBO_CHECK=1 and OBX_GC=poison, so a leak or use-after-free fails the
case. The compiler is $OBO (default obo/obo); all paths are absolute, so the
runner works from any cwd. No output is written outside the test temp dir.

Exit status is 1 when any case fails; a failed compile or a mismatched line is
reported with the compiler's or program's own message. Nothing is skipped
silently: a root with no // expect: marker is a failure.
"""
import atexit
import os
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
FORK = os.path.dirname(HERE)                      # the obo tree: --rt and -I base
LIBDIR = os.path.join(FORK, 'lib')
CASEDIR = os.path.join(HERE, 'stdlib')
OBXC = os.path.abspath(os.environ.get('OBO', os.path.join(FORK, 'obo')))
TMP = tempfile.mkdtemp(prefix='obo-stdlib-')
atexit.register(shutil.rmtree, TMP, ignore_errors=True)

# Pinned inventory: a case file that accidentally gains a `module` header is
# reclassified as a support module and silently stops running, so the
# discovered roots and support modules are checked against these lists.
EXPECTED_ROOTS = [
    'cli_errors.obx', 'cli_helpers.obx', 'cli_parse.obx',
    'errors_basic.obx', 'fmt_basic.obx',
    'paths_absent_base.obx', 'paths_absent_clean.obx',
    'paths_absent_dir.obx', 'paths_absent_stem.obx', 'paths_table.obx',
    'sort_orders.obx', 'strings_basic.obx', 'strings_natcmp.obx',
    'strings_quote.obx', 'strings_split_join.obx', 'time_civil.obx',
]
EXPECTED_MODULES = ['support.obx']

failures = []


def clean_env():
    """The runner's environment with checking on and freed memory poisoned."""
    e = {k: v for k, v in os.environ.items()
         if k not in ('OBO_CHECK', 'OBO_IO_FAULT', 'OBX_GC')}
    e['OBO_CHECK'] = '1'
    e['OBX_GC'] = 'poison'
    return e


def markers(path):
    expect, code, compile_error = [], 0, None
    for line in open(path):
        m = re.search(r'// expect: ?(.*)$', line)
        if m:
            expect.append(m.group(1))
        m = re.search(r'// expect-exit: *(-?\d+)', line)
        if m:
            code = int(m.group(1))
        m = re.search(r'// expect-compile-error: *(.*?)\s*$', line)
        if m:
            compile_error = m.group(1)
    return expect, code, compile_error


def is_module(text):
    return re.search(r'^module\s+\w+', text, re.M) is not None


def first_difference(expect, got):
    for i in range(max(len(expect), len(got))):
        e = expect[i] if i < len(expect) else '<nothing>'
        g = got[i] if i < len(got) else '<nothing>'
        if e != g:
            return f"output line {i + 1}: expected {e!r}, got {g!r}"
    return "output differs"


def case(path):
    name = os.path.basename(path)
    text = open(path).read()
    if is_module(text):
        return 'module'
    expect, code, compile_error = markers(path)
    if compile_error is None and not expect:
        failures.append(f"{name}: no // expect: marker and no compile-error marker")
        return 'fail'

    symdir = tempfile.mkdtemp(prefix='sym-', dir=TMP)
    work = tempfile.mkdtemp(prefix='case-', dir=TMP)
    exe = os.path.join(work, 'exe')
    p = subprocess.run([OBXC, path, '-o', exe, '--rt', FORK, '-I', LIBDIR,
                        '--sym-dir', symdir],
                       capture_output=True, text=True, timeout=120)
    if compile_error is not None:
        if p.returncode == 1 and compile_error in p.stderr:
            print(f"ok   {name} (compile error)")
            return 'pass'
        failures.append(f"{name}: expected exit 1 with {compile_error!r}; "
                        f"exit {p.returncode}; stderr {p.stderr.strip()[:400]!r}")
        return 'fail'
    if p.returncode != 0:
        failures.append(f"{name}: did not compile (exit {p.returncode}): "
                        f"{p.stderr.strip()[:400]}")
        return 'fail'

    scratch = tempfile.mkdtemp(prefix='scratch-', dir=TMP)
    r = subprocess.run([exe, scratch], capture_output=True, text=True,
                       timeout=120, env=clean_env())
    got = r.stdout.splitlines()
    problems = []
    if r.returncode != code:
        problems.append(f"exit {r.returncode}, expected {code}")
        if r.stderr.strip():
            problems.append(f"stderr {r.stderr.strip()[:300]!r}")
    if got != expect:
        problems.append(first_difference(expect, got))
    if problems:
        failures.append(f"{name}: " + '; '.join(problems))
        return 'fail'
    print(f"ok   {name}")
    return 'pass'


def main():
    if not os.path.exists(OBXC):
        print(f"no compiler at {OBXC}: run make first")
        sys.exit(2)
    files = sorted(f for f in os.listdir(CASEDIR) if f.endswith('.obx'))
    root_names = []
    module_names = []
    roots = modules = passed = 0
    for f in files:
        path = os.path.join(CASEDIR, f)
        if is_module(open(path).read()):
            module_names.append(f)
            case(path)
            modules += 1
        else:
            root_names.append(f)
            roots += 1
            passed += case(path) == 'pass'
    if root_names != EXPECTED_ROOTS:
        missing = sorted(set(EXPECTED_ROOTS) - set(root_names))
        extra = sorted(set(root_names) - set(EXPECTED_ROOTS))
        failures.append(f"test roots changed (missing {missing}, unexpected "
                        f"{extra}); update EXPECTED_ROOTS -- a case with a "
                        f"module header no longer runs")
    if module_names != EXPECTED_MODULES:
        failures.append(f"support modules changed (got {module_names}, want "
                        f"{EXPECTED_MODULES}); update EXPECTED_MODULES")
    if roots == 0:
        failures.append(f"no test roots under {CASEDIR}")
    for f in failures:
        print('FAIL', f)
    print(f"\n{passed}/{roots} stdlib roots passed, {modules} support modules, "
          f"{len(failures)} failures")
    sys.exit(1 if failures else 0)


if __name__ == '__main__':
    main()
