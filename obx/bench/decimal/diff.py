#!/usr/bin/env python3
"""Decimal differential test: one library (or compiler) against another.

    python3 obx/bench/decimal/diff.py --base DIR [--new DIR] [-c COUNT] [--work DIR]

DIR is an obx tree as for run.py (compiler/out/obxc and lib/). ddiff.obx is
built by both trees, natively and through C; the four programs run COUNT
random inputs per operation (default 1000000) and must print the same
per-operation hashes. On a difference the first differing result line is
shown (ddiff ... OP dump).
"""
import argparse
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
OBX = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)
from run import tree  # noqa: E402


def build(t, native, out):
    obxc, lib = t
    cmd = [obxc, os.path.join(HERE, 'ddiff.obx'), '-o', out] + (['--native'] if native else [])
    subprocess.run(cmd, check=True, env=dict(os.environ, OBX_LIB=lib))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--base', required=True)
    ap.add_argument('--new', default=OBX)
    ap.add_argument('-c', '--count', type=int, default=1000000)
    ap.add_argument('--work', default='/tmp/ddiff')
    a = ap.parse_args()
    os.makedirs(a.work, exist_ok=True)
    trees = {'base': tree(a.base), 'new': tree(a.new)}
    exes = {}
    for k, t in trees.items():
        for b in ('native', 'c'):
            exes[(k, b)] = build(t, b == 'native', os.path.join(a.work, f'ddiff_{k}_{b}'))
    with ThreadPoolExecutor(4) as ex:
        outs = dict(zip(exes, ex.map(lambda e: subprocess.run([e, str(a.count)], capture_output=True, text=True,
                                                                check=True).stdout, exes.values())))
    ref = outs[('base', 'native')]
    print(ref, end='')
    bad = False
    for key, out in outs.items():
        if out == ref:
            continue
        bad = True
        print(f'DIFFERENT: {key[0]} {key[1]}')
        for lr, lo in zip(ref.splitlines(), out.splitlines()):
            if lr != lo:
                op = lr.split()[0]
                d1 = subprocess.run([exes[('base', 'native')], str(a.count), op, 'dump'], capture_output=True,
                                    text=True).stdout.splitlines()
                d2 = subprocess.run([exes[key], str(a.count), op, 'dump'], capture_output=True,
                                    text=True).stdout.splitlines()
                for x, y in zip(d1, d2):
                    if x != y:
                        print(f'  {op}: base native: {x[:300]}\n  {op}: {key[0]} {key[1]}: {y[:300]}')
                        break
                break
    print('FAIL' if bad else f'PASS: {len(ref.splitlines())} operations x {a.count} inputs, 4 builds agree')
    sys.exit(1 if bad else 0)


if __name__ == '__main__':
    main()
