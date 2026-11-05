#!/usr/bin/env python3
"""lib/json.obx against JSONTestSuite (github.com/nst/JSONTestSuite, MIT).

    python3 obx/tests/jsonsuite.py [--c] [--suite DIR] [-v]

The suite is not in the repository: --suite names a checkout (its
test_parsing directory is used), else it is cloned into a temporary
directory (git, network) and removed afterwards. A small obx program built
with compiler/out/obxc (native, or through C with --c) parses each file with
json.parse, run with OBX_CHECK=1, and prints ok or the error.

y_ files must parse and n_ files must fail; i_ files may do either, and are
counted. Parsing is also checked for round trips: the compact text of a value
that parsed must parse again to an equal value. A crash (a panic or a signal)
fails any file."""
import argparse
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
OBX = os.path.dirname(HERE)
OBXC = os.environ.get('OBXC') or os.path.join(OBX, 'compiler', 'out', 'obxc')

CHECK = r'''
import "json"
import "io"
import "os"

fn main() {
    for path in os.arguments() {
        match io.read(path) {
            Ok(b) {
                match json.parse(b) {
                    Ok(v) {
                        let back = json.parse(v.toStr())
                        match back {
                            Ok(w) { println(if w.eq(&v) { "ok" } else { "roundtrip differs" }) }
                            Err(e) { println("roundtrip fails: " + e.toStr()) }
                        }
                    }
                    Err(e) { println("error " + e.toStr()) }
                }
            }
            Err(e) { println("unreadable") }
        }
    }
}
'''


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--c', action='store_true', help='build through C, not native')
    ap.add_argument('--suite', help='a JSONTestSuite checkout')
    ap.add_argument('-v', action='store_true', help='list every i_ result')
    a = ap.parse_args()
    tmp = tempfile.mkdtemp(prefix='jsonsuite-')
    try:
        suite = a.suite
        if not suite:
            suite = os.path.join(tmp, 'JSONTestSuite')
            r = subprocess.run(['git', 'clone', '-q', '--depth', '1',
                                'https://github.com/nst/JSONTestSuite.git', suite])
            if r.returncode != 0:
                print('cannot fetch JSONTestSuite; give --suite DIR')
                return 2
        data = os.path.join(suite, 'test_parsing')
        src = os.path.join(tmp, 'check.obx')
        with open(src, 'w') as f:
            f.write(CHECK)
        exe = os.path.join(tmp, 'check')
        cmd = [OBXC, src, '-o', exe] + ([] if a.c else ['--native'])
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0:
            print('build failed:', r.stdout, r.stderr)
            return 1
        names = sorted(n for n in os.listdir(data) if n.endswith('.json'))
        results = {}
        env = dict(os.environ, OBX_CHECK='1')
        # one run per file: a crash then names its file
        for n in names:
            r = subprocess.run([exe, os.path.join(data, n)], capture_output=True, env=env, timeout=60)
            out = r.stdout.decode('utf-8', 'replace').strip()
            if r.returncode != 0:
                out = 'crash (exit %d): %s' % (r.returncode, r.stderr.decode('utf-8', 'replace').strip()[:200])
            results[n] = out
        fails = []
        iok = ierr = 0
        for n in names:
            out = results[n]
            if out.startswith('crash') or out.startswith('roundtrip') or out == 'unreadable':
                fails.append((n, out))
            elif n.startswith('y_') and out != 'ok':
                fails.append((n, out))
            elif n.startswith('n_') and out == 'ok':
                fails.append((n, 'accepted'))
            elif n.startswith('i_'):
                if out == 'ok':
                    iok += 1
                else:
                    ierr += 1
                if a.v:
                    print('%-50s %s' % (n, out))
        ny = sum(1 for n in names if n.startswith('y_'))
        nn = sum(1 for n in names if n.startswith('n_'))
        for n, why in fails:
            print('FAIL %s: %s' % (n, why))
        print('%d y_ and %d n_ files, %d failed; i_: %d accepted, %d refused (%s)' % (
            ny, nn, len(fails), iok, ierr, 'C' if a.c else 'native'))
        return 1 if fails else 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == '__main__':
    sys.exit(main())
