#!/usr/bin/env python3
"""Decimal micro-benchmarks: one library (or compiler) against another.

    python3 obx/bench/decimal/run.py --base DIR [--new DIR] [-n RUNS]
                                     [--only CASE ...] [--work DIR] [--backend native|c|both]

DIR is an obx tree (the directory that holds compiler/ and lib/, e.g. obx/ of a
checkout, or obx/ of `git archive main obx/compiler obx/lib | tar -x`). Its
compiler is DIR/compiler/out/obxc (built with build.sh when missing) and its
library DIR/lib. --new defaults to this checkout's obx/.

dbench.obx is built by both, natively and through C. Every case must print
the same checksum for both; then each runs RUNS times under
`perf stat -e instructions:u,cycles:u`, base and new interleaved, and the
table shows the least of each (cycles in millions) and new/base.
"""
import argparse
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
OBX = os.path.dirname(os.path.dirname(HERE))


def tree(d):
    d = os.path.abspath(d)
    obxc = os.path.join(d, 'compiler', 'out', 'obxc')
    if not os.path.exists(obxc):
        subprocess.run(['sh', os.path.join(d, 'compiler', 'build.sh')], check=True, stdout=subprocess.DEVNULL)
    return obxc, os.path.join(d, 'lib')


def build(t, native, out):
    obxc, lib = t
    cmd = [obxc, os.path.join(HERE, 'dbench.obx'), '-o', out] + (['--native'] if native else [])
    subprocess.run(cmd, check=True, env=dict(os.environ, OBX_LIB=lib))
    return out


def perf(exe, case):
    r = subprocess.run(['perf', 'stat', '-x,', '-e', 'instructions:u,cycles:u', exe, case],
                       capture_output=True, text=True, check=True)
    vals = {}
    for line in r.stderr.splitlines():
        f = line.split(',')
        if len(f) > 2 and f[0].isdigit():
            vals[f[2].split(':')[0]] = int(f[0])
    return r.stdout.strip(), vals['instructions'], vals['cycles']


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--base', required=True)
    ap.add_argument('--new', default=OBX)
    ap.add_argument('-n', type=int, default=5)
    ap.add_argument('--only', nargs='*')
    ap.add_argument('--work', default='/tmp/dbench')
    ap.add_argument('--backend', default='both', choices=['native', 'c', 'both'])
    a = ap.parse_args()
    os.makedirs(a.work, exist_ok=True)
    base, new = tree(a.base), tree(a.new)
    backends = [b for b in ('native', 'c') if a.backend in (b, 'both')]
    exes = {}
    for b in backends:
        exes[b] = (build(base, b == 'native', os.path.join(a.work, 'base_' + b)),
                   build(new, b == 'native', os.path.join(a.work, 'new_' + b)))
    cases = a.only or subprocess.run([exes[backends[0]][0], 'list'], capture_output=True, text=True,
                                     check=True).stdout.split()
    print('| case | backend | base Mcyc | new Mcyc | new/base cyc | base Minstr | new Minstr | new/base instr |')
    print('|---|---|---|---|---|---|---|---|')
    for case in cases:
        for b in backends:
            eb, en = exes[b]
            best = {'b': [None, None], 'n': [None, None]}
            outs = set()
            for _ in range(a.n):
                for k, exe in (('b', eb), ('n', en)):
                    out, ins, cyc = perf(exe, case)
                    outs.add(out)
                    cur = best[k]
                    cur[0] = ins if cur[0] is None else min(cur[0], ins)
                    cur[1] = cyc if cur[1] is None else min(cur[1], cyc)
            if len(outs) != 1:
                sys.exit(f'{case} {b}: checksums differ: {sorted(outs)}')
            (bi, bc), (ni, nc) = best['b'], best['n']
            print(f'| {case} | {b} | {bc / 1e6:.1f} | {nc / 1e6:.1f} | {nc / bc:.3f} | '
                  f'{bi / 1e6:.1f} | {ni / 1e6:.1f} | {ni / bi:.3f} |', flush=True)


if __name__ == '__main__':
    main()
