#!/usr/bin/env python3
"""Differential test of the native backend (design/native.md, section 7).

usage: run_native.py [--compiler PATH] [--lib] [--redteam] [-j N] [-v] [filters...]

For every run test and every test of tests/native (C libraries through dynamic linking; a
test with `// needs: name` is skipped when lib<name>.so* is not installed), with --lib also
lib/tests/run, with --redteam the red-team programs that are meant to compile, builds the
program with the C backend
(cached by tools/cbuild.py) and with `obxc --native`, runs both with the test's stdin, with
and without OBX_CHECK=1, and compares stdout, stderr and the exit code. Each test is
  pass         identical in both runs
  unsupported  the native backend refused it cleanly (`native: unsupported: ...`)
  differ       anything else: a different result, a crash, a failed native build
  skip         (red team) the C build fails too: a program meant to be rejected;
               a library it needs (`// needs: name`) is not installed; or both builds
               exit 77 (a live service the test needs is missing)
Only differ fails (exit 1). -v lists the unsupported tests with their reasons. Without filters
it also builds the small programs of PARTS natively and checks which runtime functions their
maps list and how large they are (the runtime is linked by the sections a program reaches).
"""
import argparse, collections, glob, os, re, subprocess, sys, tempfile
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', 'tools'))
import cbuild  # noqa: E402
import run as runpy  # noqa: E402  (tests/run.py: test discovery and directives)

OBXC = os.path.normpath(os.path.join(HERE, '..', 'compiler', 'out', 'obxc'))
LIB_TESTS = os.path.normpath(os.path.join(HERE, '..', 'lib', 'tests', 'run'))
REDTEAM = os.path.normpath(os.path.join(HERE, '..', 'redteam'))
NATIVE = os.path.join(HERE, 'native')
LIBDIRS = ['/usr/lib/x86_64-linux-gnu', '/lib/x86_64-linux-gnu', '/usr/lib64', '/usr/lib', '/usr/local/lib', '/lib']
TIMEOUT = 10
os.environ.setdefault('OBX_LIB', os.path.normpath(os.path.join(HERE, '..', 'lib')))   # for compilers outside out/


def redteam():
    out = []
    for d in ('', 'round2', 'round3', 'round4', 'round5'):
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
    tests += [os.path.join(NATIVE, f) for f in sorted(os.listdir(NATIVE)) if f.endswith('.obx')]
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


def missing_lib(path):
    """The first `// needs: name` library of a test that is not installed, or None."""
    with open(path, encoding='utf-8') as f:
        for line in f:
            m = re.match(r'\s*//\s*needs:\s*(\S+)', line)
            if m and not any(glob.glob(os.path.join(d, 'lib%s.so*' % m.group(1))) for d in LIBDIRS):
                return m.group(1)
    return None


def one(path, obxc):
    rel = os.path.relpath(path, os.path.join(HERE, '..', '..'))
    if missing_lib(path):
        return rel, 'skip', ''
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
            if a == b and a[0] == runpy.SKIP_EXIT and d['exit'] != runpy.SKIP_EXIT:
                return rel, 'skip', ''       # both runs found a live service missing
            if a != b and want(d, b) and not want(d, a):
                return rel, 'pass', 'note: the C build differs from the directives; native matches them'
            if a != b:
                why = []
                for name, x, y in zip(('exit', 'stdout', 'stderr'), a, b):
                    if x != y:
                        why.append('%s: C %s, native %s' % (name, short(x), short(y)))
                return rel, 'differ', ('OBX_CHECK=1: ' if check else '') + '; '.join(why)
    return rel, 'pass', ''


# The runtime parts a program links (design/native.md, "Reachability"): only the runtime
# sections reachable from what the program uses. (program, the most bytes its executable may
# have, runtime functions its map must list, ... must not list, its output)
PARTS = [
    ('fn main() {\n}\n', 16384, ['rt_init', 'rt_segv', 'rt_n_restore', 'rt_n_start'],
     ['rt_ctx_swap', 'rt_float_to_str', 'malloc', 'rt_str_concat'], ''),
    ('fn main() {\n    println("hello")\n}\n', 20480, ['rt_init', 'rt_write_fd'],
     ['rt_ctx_swap', 'rt_float_to_str'], 'hello\n'),
    ('import "task"\n\nfn inc(x: int): int {\n    task.sleep(1)\n    return x + 1\n}\n\n'
     'fn main() {\n    task.scope(fn(s: mut &task.Scope) { println(intToStr(s.start(inc, 41).wait().unwrap())) })\n}\n',
     65536, ['rt_ctx_swap', 'rt_init'], ['rt_float_to_str'], '42\n'),
    # dynamically linked (libm's tgamma; pow and the others are the runtime's own): libc's entry, so no rt_n_start; rt_n_libc kept
    ('extern "c" fn tgamma(x: float): float\n\nfn main() {\n    println(floatToStr(tgamma(5.0)))\n}\n',
     65536, ['rt_init', 'rt_float_to_str'], ['rt_n_start', 'rt_ctx_swap'], '24\n'),
]


def runtime_parts(obxc):
    """Failures of PARTS (the size of an executable, which runtime functions it has)."""
    bad = []
    with tempfile.TemporaryDirectory() as t:
        for i, (src, most, has, hasnt, out) in enumerate(PARTS):
            p = os.path.join(t, 'p%d.obx' % i)
            exe = os.path.join(t, 'p%d' % i)
            mp = os.path.join(t, 'p%d.map' % i)
            with open(p, 'w') as f:
                f.write(src)
            r = subprocess.run([obxc, p, '--native', '-o', exe], capture_output=True, text=True,
                               env=dict(os.environ, OBX_NATIVE_MAP=mp))
            if r.returncode != 0:
                bad.append('parts %d: the build failed: %s' % (i, r.stderr.strip()))
                continue
            names = {line.split()[2] for line in open(mp) if len(line.split()) == 3}
            size = os.path.getsize(exe)
            if size > most:
                bad.append('parts %d: %d bytes, more than %d' % (i, size, most))
            for n in has:
                if n not in names:
                    bad.append('parts %d: the runtime function %s is missing' % (i, n))
            for n in hasnt:
                if n in names:
                    bad.append('parts %d: the runtime function %s is linked, unused' % (i, n))
            r = subprocess.run([exe], capture_output=True, text=True, timeout=TIMEOUT,
                               env=dict(os.environ, OBX_CHECK='1'))
            if r.returncode != 0 or r.stdout != out:
                bad.append('parts %d: exit %d, output %r' % (i, r.returncode, r.stdout))
    return bad


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
    parts = [] if a.filters else runtime_parts(obxc)
    for why in parts:
        print('DIFFER runtime parts: ' + why)
    print('native: %d pass, %d differ, %d unsupported, %d total%s' % (
        count['pass'], count['differ'], count['unsupported'], len(results) - count['skip'],
        ' (%d skipped: rejected by the compiler or a library missing)' % count['skip'] if count['skip'] else '')
        + ('; runtime parts: %s' % ('%d failed' % len(parts) if parts else 'ok') if not a.filters else ''))
    sys.exit(1 if count['differ'] or parts else 0)


if __name__ == '__main__':
    main()
