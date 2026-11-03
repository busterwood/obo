#!/usr/bin/env python3
"""Differential test against a previous compiler: what changed between two builds of obxc.

usage: OBX_PREV=path/to/old/obxc run_prev.py [--compiler PATH] [--native] [--lib] [--redteam]
           [--gen N] [--seed S] [--eza] [-j N] [filters...]

Every run and error test (tests/run.py), with --lib lib/tests/run, with --redteam the red-team
programs of rounds 1-5 (as tests/run_native.py finds them), with --gen N also N programs from
redteam/round5/gen.py (a fresh random seed unless --seed), is built by both compilers (the C
backend through gcc, cached by tools/cbuild.py; --native: `obxc --native`). When either build
fails, the two compilers' exit codes and messages must be the same; when both succeed, both
programs run with the test's stdin under OBX_CHECK=1 and must give the same exit code, stdout
and stderr. --eza builds cmd/eza.obx with both and compares a few listings of lib/ the same way.

Each program is `same` or `differ` (or `skip`: a library it needs is not installed). Only differ
fails (exit 1).
"""
import argparse, collections, os, random, subprocess, sys, tempfile
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, '..'))
sys.path.insert(0, os.path.join(ROOT, 'tools'))
import cbuild  # noqa: E402
import run as runpy  # noqa: E402
import run_native  # noqa: E402

OBXC = os.path.join(ROOT, 'compiler', 'out', 'obxc')
GEN = os.path.join(ROOT, 'redteam', 'round5', 'gen.py')
TIMEOUT = 10
EZA_ARGS = [[], ['-1'], ['-l', '--no-time'], ['-la', '--sort=size', '--no-time'], ['-T', '--level=2'],
            ['-lT', '--no-time', '--no-user'], ['-G', '--width=100'], ['--icons=never', '-R', '--level=2'], ['-x', '-s', 'ext']]
os.environ.setdefault('OBX_LIB', os.path.join(ROOT, 'lib'))


def build(obxc, path, exe, native, cwd):
    """(ok, (exit, stdout, stderr) of the compiler)."""
    try:
        if native:
            r = subprocess.run([obxc, path, '--native', '-o', exe], capture_output=True, timeout=120, cwd=cwd)
            res = (r.returncode, r.stdout, r.stderr)
        else:
            rc, out, err = cbuild.build_obx(path, exe, obxc=obxc, timeout=120, cwd=cwd)
            res = (rc, out.encode(), err.encode())
    except subprocess.TimeoutExpired:
        res = ('timeout', b'', b'')
    return res[0] == 0, res


def execute(argv, stdin, cwd):
    env = dict(os.environ, OBX_CHECK='1')
    try:
        r = subprocess.run(argv, input=stdin, capture_output=True, timeout=TIMEOUT, env=env, cwd=cwd)
        return r.returncode, r.stdout, r.stderr
    except subprocess.TimeoutExpired:
        return 'timeout', b'', b''


def diff(a, b):
    why = []
    for name, x, y in zip(('exit', 'stdout', 'stderr'), a, b):
        if x != y:
            why.append('%s: prev %s, new %s' % (name, run_native.short(x), run_native.short(y)))
    return '; '.join(why)


def one(path, prev, new, native, runs):
    """runs: argv lists to run the program with (None: once, with the test's stdin)."""
    rel = os.path.relpath(path, os.path.dirname(ROOT))
    if run_native.missing_lib(path):
        return rel, 'skip', ''
    d = runpy.parse_directives(path)
    stdin = ''.join(l + '\n' for l in d['stdin']).encode()
    with tempfile.TemporaryDirectory(prefix='obxprev_') as tmp:
        pexe, nexe = os.path.join(tmp, 'p.out'), os.path.join(tmp, 'n.out')
        pok, pres = build(prev, path, pexe, native, tmp)
        nok, nres = build(new, path, nexe, native, tmp)
        if not (pok and nok):
            return (rel, 'same', '') if pres == nres else (rel, 'differ', 'build: ' + diff(pres, nres))
        for args in runs or [[]]:
            a = execute([pexe] + args, stdin, runs and ROOT or tmp)
            b = execute([nexe] + args, stdin, runs and ROOT or tmp)
            if a != b:
                return rel, 'differ', ' '.join(args) + ': ' + diff(a, b)
    return rel, 'same', ''


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--compiler', default=OBXC, help='the new compiler (default compiler/out/obxc)')
    ap.add_argument('--native', action='store_true', help='build with --native, not gcc')
    ap.add_argument('--lib', action='store_true', help='also the library tests')
    ap.add_argument('--redteam', action='store_true', help='also the red-team programs (rounds 1-5)')
    ap.add_argument('--gen', type=int, default=0, help='also N generated programs (round5/gen.py)')
    ap.add_argument('--seed', type=int, default=None)
    ap.add_argument('--eza', action='store_true', help='also cmd/eza.obx on a few listings')
    ap.add_argument('-j', type=int, default=os.cpu_count() or 4)
    ap.add_argument('filters', nargs='*')
    a = ap.parse_args()
    prev = os.environ.get('OBX_PREV')
    if not prev:
        sys.exit('run_prev.py: set OBX_PREV to the previous compiler')
    prev, new = os.path.abspath(prev), os.path.abspath(a.compiler)
    tests = [p for k, p in runpy.find_tests()]
    if a.lib or a.redteam:
        extra = run_native.find(a.lib, a.redteam)
        tests += [t for t in extra if t not in tests]
    with tempfile.TemporaryDirectory(prefix='obxgen_') as gen:
        if a.gen:
            seed = a.seed if a.seed is not None else random.randrange(1 << 30)
            subprocess.run([sys.executable, GEN, gen, str(a.gen), str(seed)], check=True, stdout=subprocess.DEVNULL)
            print('generated %d programs, seed %d' % (a.gen, seed))
            for dirpath, _, files in os.walk(gen):
                tests += [os.path.join(dirpath, f) for f in sorted(files) if f.endswith('.obx')]
        if a.filters:
            tests = [t for t in tests if any(f in t for f in a.filters)]
        jobs = [(t, None) for t in tests]
        if a.eza:
            jobs.append((os.path.join(ROOT, 'cmd', 'eza.obx'), [args + ['lib'] for args in EZA_ARGS]))
        with ThreadPoolExecutor(max_workers=a.j) as ex:
            results = list(ex.map(lambda j: one(j[0], prev, new, a.native, j[1]), jobs))
    count = collections.Counter(r[1] for r in results)
    for rel, kind, why in results:
        if kind == 'differ':
            print('DIFFER %s: %s' % (rel, why))
    print('prev: %d same, %d differ, %d skipped, %d total' % (count['same'], count['differ'], count['skip'], len(results)))
    sys.exit(1 if count['differ'] else 0)


if __name__ == '__main__':
    main()
