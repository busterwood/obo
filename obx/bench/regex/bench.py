#!/usr/bin/env python3
"""The regex benchmark table (design/regex.md section 4): obx (native and via C) against Go's
regexp and Rust's regex crate, with ripgrep as a reference point.

    python3 obx/bench/regex/bench.py [-n RUNS] [--reps R] [--no-rust] [--no-rg]

Writes the corpus (gen_corpus.py, about 3 MB, the same every time) to a temporary directory,
builds regex_bench.obx with obxc (obx/compiler/out/obxc, or $OBXC) natively and through C,
go/main.go with go, and rust/ with `cargo build --release --offline` (skipped, with a note,
when cargo or the regex crate is not available offline). Each driver times every pattern
itself (the best of REPS searches, after reading the corpus); the table keeps the best of
RUNS runs of each driver. The match counts of all drivers must agree. The obx drivers also
run once under OBX_CHECK=1 (leak free). rg is `rg --count-matches PATTERN FILE`, the whole
process (start-up and reading the file included), best of RUNS: a reference, not a like
for like.
"""
import argparse, os, shutil, subprocess, sys, tempfile, time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, '..', '..', '..'))
OBXC = os.environ.get('OBXC') or os.path.join(ROOT, 'obx', 'compiler', 'out', 'obxc')
NAMES = ['literal', 'alternation', 'words', 'nocase', 'email', 'errors', 'dates', 'hostile']
PATTERNS = {
    'literal': r'Sherlock Holmes',
    'alternation': r'Sherlock|Holmes|Watson|Irene',
    'words': r'\w+',
    'nocase': r'(?i)sherlock',
    'email': r'[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}',
    'errors': r'(?m)^ERROR .*timeout$',
    'dates': r'(?P<y>\d{4})-(?P<m>\d{2})-(?P<d>\d{2})',
    'hostile': r'(a*)*b',
}


def parse(out):
    res = {}
    for line in out.strip().split('\n'):
        name, count, ms = line.split()
        res[name] = (int(count), float(ms))
    return res


def run_driver(cmd, runs, env=None):
    best = None
    for _ in range(runs):
        r = subprocess.run(cmd, capture_output=True, text=True, env=env)
        if r.returncode != 0:
            sys.exit('%s exited %d: %s' % (cmd[0], r.returncode, r.stderr[-400:]))
        res = parse(r.stdout)
        if best is None:
            best = res
        else:
            best = {k: (v[0], min(v[1], best[k][1])) for k, v in res.items()}
    return best


def build_rust(tmp):
    if shutil.which('cargo') is None:
        print('note: cargo not found; the Rust column is skipped')
        return None
    env = dict(os.environ, CARGO_TARGET_DIR=os.path.join(tmp, 'rust-target'))
    r = subprocess.run(['cargo', 'build', '--release', '--offline', '--quiet'], cwd=os.path.join(HERE, 'rust'),
                       capture_output=True, text=True, env=env)
    if r.returncode != 0:
        print('note: the Rust driver did not build offline (the regex crate is not in the cargo cache?); '
              'the Rust column is skipped:\n  ' + r.stderr.strip().split('\n')[-1])
        return None
    return os.path.join(tmp, 'rust-target', 'release', 'regex_bench')


def rg_time(path, pat, runs):
    best = None
    count = None
    for _ in range(runs):
        t = time.perf_counter()
        r = subprocess.run(['rg', '--count-matches', '--no-config', '-e', pat, path], capture_output=True, text=True)
        dt = (time.perf_counter() - t) * 1000
        count = int(r.stdout.strip() or 0)
        best = dt if best is None else min(best, dt)
    return count, best


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('-n', type=int, default=5, help='runs of each driver (default 5)')
    ap.add_argument('--reps', type=int, default=5, help='searches per pattern in a run (default 5)')
    ap.add_argument('--no-rust', action='store_true')
    ap.add_argument('--no-rg', action='store_true')
    a = ap.parse_args()
    tmp = tempfile.mkdtemp(prefix='regexbench_')
    try:
        corpus = os.path.join(tmp, 'corpus.txt')
        subprocess.run([sys.executable, os.path.join(HERE, 'gen_corpus.py'), corpus], check=True, capture_output=True)
        hostile = os.path.join(tmp, 'hostile.txt')
        open(hostile, 'w').write('a' * 100000)
        src = os.path.join(HERE, 'regex_bench.obx')
        drivers = []
        for label, flags in (('obx native', ['--native']), ('obx via C', [])):
            exe = os.path.join(tmp, 'obx_' + ('n' if flags else 'c'))
            subprocess.run([OBXC, src, '-o', exe] + flags, check=True)
            r = subprocess.run([exe, corpus, '1'], capture_output=True, text=True, env=dict(os.environ, OBX_CHECK='1'))
            if r.returncode != 0:
                sys.exit('%s under OBX_CHECK=1 exited %d: %s' % (label, r.returncode, r.stderr[-400:]))
            drivers.append((label, exe))
        if shutil.which('go'):
            exe = os.path.join(tmp, 'go_bench')
            subprocess.run(['go', 'build', '-o', exe, os.path.join(HERE, 'go', 'main.go')], check=True,
                           env=dict(os.environ, GO111MODULE='off', GOTOOLCHAIN='local'), capture_output=True)
            drivers.append(('Go', exe))
        else:
            print('note: go not found; the Go column is skipped')
        if not a.no_rust:
            exe = build_rust(tmp)
            if exe:
                drivers.append(('Rust', exe))
        env = dict(os.environ)
        env.pop('OBX_CHECK', None)
        results = [(label, run_driver([exe, corpus, str(a.reps)], a.n, env)) for label, exe in drivers]
        use_rg = not a.no_rg and shutil.which('rg')
        cols = [l for l, _ in results] + (['rg'] if use_rg else [])
        print('corpus %d bytes; best of %d runs x %d searches; ms' % (os.path.getsize(corpus), a.n, a.reps))
        print()
        print('| pattern | matches | ' + ' | '.join(cols) + ' |')
        print('|---|---:|' + '---:|' * len(cols))
        bad = False
        for name in NAMES:
            counts = {res[name][0] for _, res in results}
            # Go and obx count each date match with its year group: the same number.
            row = ['`%s`' % PATTERNS[name].replace('|', '\\|'), str(results[0][1][name][0])]
            if len(counts) != 1:
                bad = True
                row[1] += ' (DIFFER: %s)' % ', '.join('%s %d' % (l, r[name][0]) for l, r in results)
            row += ['%.2f' % res[name][1] for _, res in results]
            if use_rg:
                c, t = rg_time(hostile if name == 'hostile' else corpus, PATTERNS[name], a.n)
                row.append('%.1f' % t)
            print('| ' + ' | '.join(row) + ' |')
        return 1 if bad else 0
    finally:
        shutil.rmtree(tmp)


if __name__ == '__main__':
    sys.exit(main())
