#!/usr/bin/env python3
"""Milestone 4 benchmarks: each workload in both dialects (NAME_gc.obx for
mainline obx, NAME_obo.obx for obo), measured for peak resident memory and
time, with mainline's collector at its default and at two tuned settings.

    python3 obo/bench/bench.py"""
import difflib
import os
import subprocess
import time

HERE = os.path.dirname(os.path.abspath(__file__))
FORK = os.path.dirname(HERE)
ROOT = os.path.join(os.path.dirname(FORK), 'obx')
INPUT = os.path.join(ROOT, 'src', 'obxc.obx')        # read by the tokens workload
GC_CONFIGS = [('GC default', {}),
              ('GC tuned', {'OBX_GC_MIN_KB': '1024', 'OBX_GC_GROWTH': '100'}),
              ('GC aggressive', {'OBX_GC_MIN_KB': '256', 'OBX_GC_GROWTH': '50'})]


def run(exe, env):
    """(peak KB, best wall time in ms of 3 runs, output)"""
    best, peak, out = None, 0, ''
    for _ in range(3):
        t0 = time.perf_counter()
        p = subprocess.run([os.path.join(HERE, 'peak'), exe, INPUT], capture_output=True, text=True, env=env)
        dt = (time.perf_counter() - t0) * 1000
        best = dt if best is None else min(best, dt)
        peak = int(p.stderr.split()[0])
        out = p.stdout.strip()
        if 'exit 0' not in p.stderr:
            raise SystemExit(f"{exe} failed: {p.stderr}")
    return peak, best, out


def changed_lines(a, b):
    diff = difflib.ndiff(open(a).read().splitlines(), open(b).read().splitlines())
    return sum(1 for l in diff if l.startswith('+ '))


def main():
    subprocess.run(['gcc', '-O2', os.path.join(HERE, 'peak.c'), '-o', os.path.join(HERE, 'peak')], check=True)
    work = [f[:-7] for f in sorted(os.listdir(HERE)) if f.endswith('_gc.obx')]
    print("| Workload | Lines changed | " + " | ".join(n for n, _ in GC_CONFIGS) + " | obo |")
    print("|---|---|" + "---|" * (len(GC_CONFIGS) + 1))
    for w in work:
        gc_src, own_src = os.path.join(HERE, w + '_gc.obx'), os.path.join(HERE, w + '_obo.obx')
        gc_exe, own_exe = f'/tmp/bench_{w}_gc', f'/tmp/bench_{w}_own'
        subprocess.run([os.path.join(ROOT, 'obxc'), gc_src, '-o', gc_exe, '--rt', ROOT], check=True)
        subprocess.run([os.path.join(FORK, 'obo'), own_src, '-o', own_exe, '--rt', FORK], check=True)
        cells, outputs = [], set()
        for _, env in GC_CONFIGS:
            peak, ms, out = run(gc_exe, env)
            cells.append(f"{peak / 1024:.1f} MB, {ms:.0f} ms")
            outputs.add(out)
        peak, ms, out = run(own_exe, {'OBO_CHECK': '1'})
        cells.append(f"{peak / 1024:.1f} MB, {ms:.0f} ms")
        outputs.add(out)
        if len(outputs) != 1:
            raise SystemExit(f"{w}: outputs differ: {outputs}")
        total = len(open(own_src).read().splitlines())
        print(f"| {w} | {changed_lines(gc_src, own_src)} of {total} | " + " | ".join(cells) + " |")


if __name__ == '__main__':
    main()
