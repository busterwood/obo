#!/usr/bin/env python3
"""Differential test of the native backend (design/native.md, section 7).

usage: run_native.py [--compiler PATH] [--lib] [--redteam] [-j N] [-v] [filters...]

For every run test (with --lib also lib/tests/run, with --redteam the red-team programs
that are meant to compile), builds the program with the C backend
(cached by tools/cbuild.py) and with `obxc --native`, runs both with the test's stdin, with
and without OBX_CHECK=1, and compares stdout, stderr and the exit code. Each test is
  pass         identical in both runs
  unsupported  the native backend refused it cleanly (`native: unsupported: ...`)
  differ       anything else: a different result, a crash, a failed native build
  skip         (red team) the C build fails too: a program meant to be rejected
Only differ fails (exit 1). -v lists the unsupported tests with their reasons.
"""
import argparse, collections, os, subprocess, sys, tempfile
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', 'tools'))
import cbuild  # noqa: E402
import run as runpy  # noqa: E402  (tests/run.py: test discovery and directives)

OBXC = os.path.normpath(os.path.join(HERE, '..', 'compiler', 'out', 'obxc'))
LIB_TESTS = os.path.normpath(os.path.join(HERE, '..', 'lib', 'tests', 'run'))
REDTEAM = os.path.normpath(os.path.join(HERE, '..', 'redteam'))
TIMEOUT = 10
os.environ.setdefault('OBX_LIB', os.path.normpath(os.path.join(HERE, '..', 'lib')))   # for compilers outside out/


def redteam():
    out = []
    for d in ('', 'round2', 'round3', 'round4'):
        root = os.path.join(REDTEAM, d)
        for sub in ('cases', ''):
            p = os.path.join(root, sub)
            if os.path.isdir(p):
                out += [os.path.join(p, f) for f in sorted(os.listdir(p)) if f.endswith('.obx')]
        for dirpath, dirnames, filenames in sorted(os.walk(root)):
            dirnames[:] = sorted(x for x in dirnames if x != 'build')
            if 'main.obx' in filenames:
                out.append(os.path.join(dirpath, 'main.obx'))
                dirnames[:] = []
    return sorted(set(out))


def find(lib, red):
    tests = [p for k, p in runpy.find_tests() if k == 'run']
    if red:
        tests += redteam()
    if lib:
        for dirpath, dirnames, filenames in sorted(os.walk(LIB_TESTS)):
            dirnames.sort()
            if 'main.obx' in filenames:
                tests.append(os.path.join(dirpath, 'main.obx'))
                dirnames[:] = []
                continue
            tests += [os.path.join(dirpath, f) for f in sorted(filenames) if f.endswith('.obx')]
    return tests


def execute(exe, stdin, check, cwd):
    env = dict(os.environ)
    env.pop('OBX_CHECK', None)
    if check:
        env['OBX_CHECK'] = '1'
    try:
        r = subprocess.run([exe], input=stdin.encode(), capture_output=True, timeout=TIMEOUT, env=env, cwd=cwd)
        return r.returncode, r.stdout, r.stderr
    except subprocess.TimeoutExpired:
        return 'timeout', b'', b''


def want(d, r):
    """Whether a run's (exit, stdout, stderr) matches the test's directives."""
    return r[0] == d['exit'] and r[1] == ''.join(l + '\n' for l in d['expect']).encode()


def short(b):
    s = b.decode('utf-8', 'replace') if isinstance(b, bytes) else str(b)
    return repr(s if len(s) < 300 else s[:300] + '...')


def one(path, obxc):
    rel = os.path.relpath(path, os.path.join(HERE, '..', '..'))
    d = runpy.parse_directives(path)
    stdin = ''.join(l + '\n' for l in d['stdin'])
    with tempfile.TemporaryDirectory(prefix='obxnat_') as tmp:
        cexe = os.path.join(tmp, 'c.out')
        nexe = os.path.join(tmp, 'n.out')
        try:
            rc, out, err = cbuild.build_obx(path, cexe, obxc=obxc, timeout=60, cwd=tmp)
        except subprocess.TimeoutExpired:
            return rel, 'differ', 'C build timeout'
        if rc != 0:
            if REDTEAM in path:
                return rel, 'skip', ''
            return rel, 'differ', 'C build failed: ' + (out + err)[:300]
        try:
            r = subprocess.run([obxc, path, '--native', '-o', nexe], capture_output=True, text=True, timeout=60, cwd=tmp)
        except subprocess.TimeoutExpired:
            return rel, 'differ', 'native build timeout'
        if r.returncode != 0:
            msg = (r.stdout + r.stderr).strip()
            if 'native: unsupported:' in msg and r.returncode == 1:
                return rel, 'unsupported', msg[msg.index('native: unsupported:') + 21:].splitlines()[0]
            return rel, 'differ', 'native build failed (%s): %s' % (r.returncode, msg[:400])
        for check in (False, True):
            a = execute(cexe, stdin, check, tmp)
            b = execute(nexe, stdin, check, tmp)
            if a != b and want(d, b) and not want(d, a):
                return rel, 'pass', 'note: the C build differs from the directives; native matches them'
            if a != b:
                why = []
                for name, x, y in zip(('exit', 'stdout', 'stderr'), a, b):
                    if x != y:
                        why.append('%s: C %s, native %s' % (name, short(x), short(y)))
                return rel, 'differ', ('OBX_CHECK=1: ' if check else '') + '; '.join(why)
    return rel, 'pass', ''


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--compiler', default=OBXC)
    ap.add_argument('--lib', action='store_true', help='also the library tests')
    ap.add_argument('--redteam', action='store_true', help='also the red-team programs')
    ap.add_argument('-j', type=int, default=os.cpu_count() or 4)
    ap.add_argument('-v', action='store_true')
    ap.add_argument('filters', nargs='*')
    a = ap.parse_args()
    obxc = os.path.abspath(a.compiler)
    tests = find(a.lib, a.redteam)
    if a.filters:
        tests = [t for t in tests if any(f in t for f in a.filters)]
    with ThreadPoolExecutor(max_workers=a.j) as ex:
        results = list(ex.map(lambda p: one(p, obxc), tests))
    count = collections.Counter(r[1] for r in results)
    reasons = collections.Counter()
    for rel, kind, why in results:
        if kind == 'pass' and why:
            print('%s: %s' % (rel, why))
        if kind == 'differ':
            print('DIFFER %s: %s' % (rel, why))
        elif kind == 'unsupported':
            reasons[why.split(' (C line')[0].split(' (runtime header')[0]] += 1
            if a.v:
                print('unsupported %s: %s' % (rel, why))
    if reasons:
        print('unsupported, by reason:')
        for why, n in reasons.most_common(15):
            print('  %4d  %s' % (n, why))
    print('native: %d pass, %d differ, %d unsupported, %d total%s' % (
        count['pass'], count['differ'], count['unsupported'], len(results) - count['skip'],
        ' (%d skipped: rejected by the compiler)' % count['skip'] if count['skip'] else ''))
    sys.exit(1 if count['differ'] else 0)


if __name__ == '__main__':
    main()
