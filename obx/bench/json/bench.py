#!/usr/bin/env python3
"""The JSON benchmark (design/json.md): obx's json library against Python's json, and
cmd/jq.obx against jq 1.7.1, on one generated file of about 80 MB.

    python3 obx/bench/json/bench.py [-n RUNS] [--mb MB] [--data FILE] [--jq PATH] [--no-c]

The data comes from gen_data.py (a fixed seed) into a temporary directory, unless --data
names a file to reuse. Two tables:

- library: json_bench.obx (built natively and through C) and py_bench.py each time their
  steps in-process, the file already read: parse, compact and pretty writing, and a query
  (the sum of every "qty"), through items() and field() and through eachItem (visit; the
  same code for Python). Best of RUNS.
- jq: whole processes, best of RUNS wall times, output to a file: jq 1.7.1 (--jq, $JQ_REF
  or jq on PATH, if it is 1.7.1; else the column is skipped) and cmd/jq.obx natively and
  through C, running the same filters. The outputs of each filter must be identical.

The library's compact output must also equal `jq -c .`'s, byte for byte.
"""
import argparse, hashlib, os, shutil, subprocess, sys, tempfile, time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, '..', '..', '..'))
OBXC = os.environ.get('OBXC') or os.path.join(ROOT, 'obx', 'compiler', 'out', 'obxc')
JQ_SRC = os.path.join(ROOT, 'obx', 'cmd', 'jq.obx')
STEPS = ['parse', 'compact', 'pretty', 'query', 'visit']
FILTERS = [
    ('identity', ['.']),
    ('compact', ['-c', '.']),
    ('length', ['length']),
    ('sum qty', ['[.[].items[].qty] | add']),
    ('select', ['-c', 'map(select(.active and .score > 0) | {id, city: .address.city})']),
    ('group', ['-c', 'group_by(.address.city) | map({city: .[0].address.city, n: length})']),
]


def build(src, out, native):
    cmd = [OBXC, src, '-o', out] + (['--native'] if native else [])
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit('building %s failed: %s' % (src, r.stderr[-800:]))


def parse(out):
    res = {}
    for line in out.strip().split('\n'):
        step, size, ms = line.split()
        res[step] = (int(size), float(ms))
    return res


def driver(cmd, env=None):
    env = dict(os.environ, **(env or {}))
    env.pop('OBX_CHECK', None)
    r = subprocess.run(cmd, capture_output=True, text=True, env=env)
    if r.returncode != 0:
        sys.exit('%s exited %d: %s' % (cmd[0], r.returncode, r.stderr[-400:]))
    return parse(r.stdout)


def wall(cmd, out_path, runs):
    best = None
    env = dict(os.environ)
    env.pop('OBX_CHECK', None)
    for _ in range(runs):
        with open(out_path, 'wb') as f:
            t0 = time.perf_counter()
            r = subprocess.run(cmd, stdout=f, stderr=subprocess.PIPE, env=env)
            t = time.perf_counter() - t0
        if r.returncode != 0:
            sys.exit('%s exited %d: %s' % (' '.join(cmd), r.returncode, r.stderr[-400:]))
        best = t if best is None else min(best, t)
    with open(out_path, 'rb') as f:
        digest = hashlib.sha256(f.read()).hexdigest()
    return best, digest


def find_jq(given):
    for c in [given, os.environ.get('JQ_REF'), shutil.which('jq')]:
        if not c:
            continue
        try:
            v = subprocess.run([c, '--version'], capture_output=True, text=True).stdout.strip()
        except OSError:
            continue
        if v == 'jq-1.7.1':
            return c
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('-n', '--runs', type=int, default=3)
    ap.add_argument('--mb', type=float, default=80)
    ap.add_argument('--data')
    ap.add_argument('--jq')
    ap.add_argument('--no-c', action='store_true', help='skip the builds through C')
    a = ap.parse_args()
    tmp = tempfile.mkdtemp(prefix='obx-json-bench-')
    try:
        data = a.data
        if not data:
            data = os.path.join(tmp, 'data.json')
            subprocess.run([sys.executable, os.path.join(HERE, 'gen_data.py'), data, str(a.mb)], check=True)
        print('data: %s, %.1f MB' % (data, os.path.getsize(data) / 1048576))
        kinds = [('native', True)] + ([] if a.no_c else [('C', False)])
        lib = {}
        for name, native in kinds:
            exe = os.path.join(tmp, 'json_bench_' + name)
            build(os.path.join(HERE, 'json_bench.obx'), exe, native)
            r = subprocess.run([exe, data], capture_output=True, text=True, env=dict(os.environ, OBX_CHECK='1'))
            if r.returncode != 0:
                sys.exit('json_bench (%s) under OBX_CHECK=1 failed: %s' % (name, r.stderr[-400:]))
            lib['obx ' + name] = driver([exe, data, str(a.runs)])
        lib['Python'] = driver([sys.executable, os.path.join(HERE, 'py_bench.py'), data, str(a.runs)])
        cols = list(lib)
        print('\nlibrary, ms (best of %d)' % a.runs)
        print('%-8s ' % 'step' + ' '.join('%12s' % c for c in cols))
        for s in STEPS:
            print('%-8s ' % s + ' '.join('%12.1f' % lib[c][s][1] for c in cols))
        for s in ('parse', 'query', 'visit'):
            sizes = {lib[c][s][0] for c in cols}
            if len(sizes) != 1:
                sys.exit('%s results differ: %s' % (s, {c: lib[c][s][0] for c in cols}))

        jq = find_jq(a.jq)
        exes = []
        if jq:
            exes.append(('jq 1.7.1', jq))
        else:
            print('\nnote: no jq 1.7.1 (--jq, $JQ_REF or PATH); its column is skipped')
        for name, native in kinds:
            exe = os.path.join(tmp, 'jq_' + name)
            build(JQ_SRC, exe, native)
            exes.append(('obx jq ' + name, exe))
        out = os.path.join(tmp, 'out')
        if jq:
            first = os.path.join(tmp, 'json_bench_native')
            with open(out, 'wb') as f:
                subprocess.run([first, data, '--compact'], stdout=f, check=True)
            with open(out, 'rb') as f:
                mine = f.read()
            ref = subprocess.run([jq, '-c', '.', data], capture_output=True).stdout
            if mine != ref:
                sys.exit('the library\'s compact output differs from jq -c .')
        print('\njq, whole process, s (best of %d)' % a.runs)
        print('%-9s ' % 'filter' + ' '.join('%14s' % e[0] for e in exes))
        for fname, args in FILTERS:
            times = []
            digests = set()
            for _, exe in exes:
                t, d = wall([exe] + args + [data], out, a.runs)
                times.append(t)
                digests.add(d)
            if len(digests) != 1:
                sys.exit('outputs differ for %s' % fname)
            print('%-9s ' % fname + ' '.join('%14.2f' % t for t in times))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == '__main__':
    main()
