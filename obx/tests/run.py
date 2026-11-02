#!/usr/bin/env python3
"""obx conformance test runner. See README.md for the test file format.

usage: run.py [--compiler PATH] [-v] [filters...]
`// error: a|b` accepts either word; an empty `// error:` accepts any failure.
A filter is a substring of the test's path relative to the tests directory.
"""
import argparse, os, re, subprocess, sys, tempfile
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
DIRECTIVE = re.compile(r'^\s*//\s*(expect|exit|stdin|error|line):(.*)$')
COMPILE_TIMEOUT = 20
RUN_TIMEOUT = 10


def find_tests():
    tests = []
    for kind in ('run', 'error'):
        root = os.path.join(HERE, kind)
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames.sort()
            if 'main.obx' in filenames:
                tests.append((kind, os.path.join(dirpath, 'main.obx')))
                dirnames[:] = []          # a directory with main.obx is one test
                continue
            for f in sorted(filenames):
                if f.endswith('.obx'):
                    tests.append((kind, os.path.join(dirpath, f)))
    return tests


def parse_directives(path):
    d = {'expect': [], 'exit': 0, 'stdin': [], 'error': [], 'line': None}
    with open(path, encoding='utf-8') as f:
        for raw in f:
            m = DIRECTIVE.match(raw.rstrip('\n').rstrip('\r'))
            if not m:
                continue
            key, val = m.group(1), m.group(2)
            if val.startswith(' '):
                val = val[1:]
            if key == 'expect':
                d['expect'].append(val)
            elif key == 'stdin':
                d['stdin'].append(val)
            elif key == 'exit':
                d['exit'] = int(val.strip())
            elif key == 'error':
                d['error'].append(val.strip())
            elif key == 'line':
                d['line'] = int(val.strip())
    return d


def compiler_cmd(compiler):
    if os.sep in compiler:
        compiler = os.path.abspath(compiler)
    if compiler.endswith('.py'):
        return [sys.executable, compiler]
    return [compiler]


def run_test(kind, path, compiler):
    rel = os.path.relpath(path, HERE)
    d = parse_directives(path)
    with tempfile.TemporaryDirectory(prefix='obxtest_') as tmp:
        out = os.path.join(tmp, 'out')
        try:
            c = subprocess.run(compiler_cmd(compiler) + [path, '-o', out],
                               capture_output=True, text=True, errors='replace',
                               timeout=COMPILE_TIMEOUT, cwd=tmp)
        except subprocess.TimeoutExpired:
            return rel, 'compile timeout'
        except OSError as e:
            return rel, 'cannot run compiler: %s' % e
        msg = (c.stdout or '') + (c.stderr or '')
        if kind == 'error':
            if c.returncode == 0:
                return rel, 'compiled, but an error was expected'
            low = msg.lower()
            for want in d['error']:
                alts = [w.strip().lower() for w in want.split('|')]
                if not any(w in low for w in alts):
                    return rel, 'message lacks %r; got: %s' % (want, msg.strip()[:200])
            if d['line'] is not None and (':%d:' % d['line']) not in msg:
                return rel, 'message lacks line :%d:; got: %s' % (d['line'], msg.strip()[:200])
            return rel, None
        if c.returncode != 0:
            return rel, 'compile failed: %s' % msg.strip()[:300]
        env = dict(os.environ, OBX_CHECK='1')
        stdin = ''.join(l + '\n' for l in d['stdin'])
        try:
            r = subprocess.run([out], input=stdin, capture_output=True, text=True,
                               errors='replace', timeout=RUN_TIMEOUT, env=env, cwd=tmp)
        except subprocess.TimeoutExpired:
            return rel, 'run timeout'
        want = ''.join(l + '\n' for l in d['expect'])
        if r.stdout != want:
            return rel, 'stdout differs\n  want: %r\n  got:  %r\n  stderr: %s' % (
                want, r.stdout, r.stderr.strip()[:200])
        if r.returncode != d['exit']:
            return rel, 'exit code %d, want %d (stderr: %s)' % (
                r.returncode, d['exit'], r.stderr.strip()[:200])
        return rel, None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--compiler', default=os.path.join(HERE, '..', 'ref', 'obxc.py'))
    ap.add_argument('-j', type=int, default=os.cpu_count() or 4)
    ap.add_argument('filters', nargs='*')
    a = ap.parse_args()
    tests = find_tests()
    if a.filters:
        tests = [t for t in tests
                 if any(f in os.path.relpath(t[1], HERE) for f in a.filters)]
    with ThreadPoolExecutor(max_workers=a.j) as ex:
        results = list(ex.map(lambda t: run_test(t[0], t[1], a.compiler), tests))
    failed = 0
    for rel, err in results:
        if err:
            failed += 1
            print('FAIL %s: %s' % (rel, err))
    print('%d passed, %d failed' % (len(results) - failed, failed))
    sys.exit(1 if failed else 0)


if __name__ == '__main__':
    main()
