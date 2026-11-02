#!/usr/bin/env python3
"""obo vs obx benchmark table.

For each program NAME it builds obx/bench/NAME.obx with the compiler
(obx/compiler/out/obxc, or $OBXC; gcc -O2) and archive/obo/bench/NAME_obo.obx with the
archived obo compiler (archive/obo/obo, if it runs). It checks that the outputs are equal and that the
obx program is leak free (OBX_CHECK=1), then prints the best wall time of N runs
of each.

    python3 obx/bench/bench.py [-n RUNS] [--only NAME ...]
"""
import argparse
import os
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
OBXC = os.environ.get('OBXC') or os.path.join(ROOT, 'obx', 'compiler', 'out', 'obxc')
OBO = os.path.join(ROOT, 'archive', 'obo')
OBO_SRC = os.path.join(OBO, 'bench')
NAMES = ['fib', 'loop', 'array', 'alloc', 'lists', 'strings', 'trees', 'tokens']
TOKENS_INPUT = os.path.join(ROOT, 'obx', 'compiler', 'sema.obx')   # any text file; same for both


def args_for(name):
    return [TOKENS_INPUT] if name == 'tokens' else []


def run(exe, name, env_extra=None):
    env = dict(os.environ)
    env.pop('OBX_CHECK', None)
    env.pop('OBO_CHECK', None)
    env.update(env_extra or {})
    t0 = time.perf_counter()
    p = subprocess.run([exe] + args_for(name), capture_output=True, text=True, env=env)
    return p.returncode, p.stdout.strip(), p.stderr.strip(), time.perf_counter() - t0


def best(exe, name, n):
    ts = []
    for _ in range(n):
        code, out, err, dt = run(exe, name)
        if code != 0:
            raise SystemExit(f"{exe} exited {code}: {err}")
        ts.append(dt)
    return min(ts), out


def build_obx(name, tmp):
    exe = os.path.join(tmp, name + '_obx')
    subprocess.run([OBXC, os.path.join(HERE, name + '.obx'), '-o', exe], check=True)
    return exe


def build_obo(name, tmp):
    exe = os.path.join(tmp, name + '_obo')
    compiler = os.path.join(OBO, 'obo')
    r = subprocess.run([compiler, os.path.join(OBO_SRC, name + '_obo.obx'), '-o', exe, '--rt', OBO],
                       capture_output=True, text=True)
    if r.returncode != 0:
        return None
    return exe


def fmt(t):
    return f"{t * 1000:.0f} ms" if t is not None else "n/a"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('-n', type=int, default=5, help='runs per program (best is reported)')
    ap.add_argument('--only', nargs='*')
    a = ap.parse_args()
    names = a.only or NAMES
    tmp = tempfile.mkdtemp(prefix='obxbench')
    rows = []
    for name in names:
        obx = build_obx(name, tmp)
        obo = build_obo(name, tmp)
        code, out, err, _ = run(obx, name, {'OBX_CHECK': '1'})
        if code != 0:
            raise SystemExit(f"{name}: obx with OBX_CHECK=1 exited {code}: {err}")
        t_obx, out_obx = best(obx, name, a.n)
        t_obo, out_obo = (None, None)
        if obo:
            t_obo, out_obo = best(obo, name, a.n)
        if obo and out_obo != out_obx:
            raise SystemExit(f"{name}: outputs differ: obx {out_obx!r}, obo {out_obo!r}")
        rows.append((name, t_obo, t_obx, out_obx))
    if all(r[1] is None for r in rows):
        print("note: the obo compiler did not build anything; obo columns skipped", file=sys.stderr)
    print("| benchmark | obo | obx | obx/obo | output |")
    print("|---|---:|---:|---:|---|")
    for name, t_obo, t_obx, out in rows:
        r = f"{t_obx / t_obo:.2f}" if t_obo else "n/a"
        print(f"| {name} | {fmt(t_obo)} | {fmt(t_obx)} | {r} | {out} |")


if __name__ == '__main__':
    main()
