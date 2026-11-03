#!/usr/bin/env python3
"""Runs RE2's exhaustive search tests (Go's regexp/testdata/re2-exhaustive.txt.bz2) through
the regex library: opt-in, as in Go (TestRE2Exhaustive runs only without -short).

    python3 obx/tests/regex/re2_exhaustive.py [--compiler PATH] [--native] [--goroot DIR] [--file F]

The file's quoted strings become h:HEX lines (as gen_go_tests.py does for re2-search.txt), and
a program built from lib/tests/run/regex_re2search/re2.obx reads them and runs every case in
four modes under each matcher, under OBX_CHECK=1. Exit 1 on any failure.
"""
import argparse, os, shutil, subprocess, sys, tempfile, time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, '..', '..'))
sys.path.insert(0, HERE)
import gen_go_tests

OBXC = os.environ.get('OBXC') or os.path.join(ROOT, 'compiler', 'out', 'obxc')
RUNNER = os.path.join(ROOT, 'lib', 'tests', 'run', 'regex_re2search', 're2.obx')

MAIN = '''import "./re2"
import "io"
import "os"

fn main() {
    let lines = io.readLines(os.arguments()[0]).unwrap()
    let s = re2.run(&lines, 50)
    println("re2-exhaustive: " + s.cases.toStr() + " cases, " + s.runs.toStr() + " runs, " + s.skipped.toStr() +
        " skipped, " + s.fails.toStr() + " failures")
    if s.fails > 0 { exit(1) }
}
'''


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--compiler', default=OBXC)
    ap.add_argument('--native', action='store_true')
    ap.add_argument('--goroot')
    ap.add_argument('--file', help='the test file (default: re2-exhaustive.txt.bz2 of the installed Go)')
    a = ap.parse_args()
    path = a.file
    if not path:
        if shutil.which('go') is None and not a.goroot:
            print('re2_exhaustive: go is not installed; skipped')
            return 0
        path = os.path.join(gen_go_tests.goroot(a.goroot), 'src', 'regexp', 'testdata', 're2-exhaustive.txt.bz2')
    tmp = tempfile.mkdtemp(prefix='re2ex_')
    try:
        lines = gen_go_tests.re2_lines(path)
        data = os.path.join(tmp, 'lines.txt')
        open(data, 'w').write('\n'.join(lines) + '\n')
        shutil.copy(RUNNER, tmp)
        open(os.path.join(tmp, 'main.obx'), 'w').write(MAIN)
        exe = os.path.join(tmp, 'main')
        r = subprocess.run([a.compiler, os.path.join(tmp, 'main.obx'), '-o', exe] + (['--native'] if a.native else []),
                           capture_output=True, text=True)
        if r.returncode != 0:
            sys.exit('building the runner failed:\n' + r.stdout + r.stderr)
        t = time.time()
        r = subprocess.run([exe, data], env=dict(os.environ, OBX_CHECK='1'))
        print('%.1f s' % (time.time() - t))
        return r.returncode
    finally:
        shutil.rmtree(tmp)


if __name__ == '__main__':
    sys.exit(main())
