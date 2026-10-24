#!/usr/bin/env python3
"""Stage B of the port: the ported compiler (obo/src/obo.obo, built by obo)
must compile every program and error case exactly as obo does: byte-identical
executables, identical error messages, and no leaks in the compiler itself."""
import glob
import os
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
FORK = os.path.dirname(HERE)
ROOT = os.path.join(os.path.dirname(FORK), 'obx')
sys.path.insert(0, os.path.join(ROOT, 'tests'))
import run  # noqa: E402  (for error_cases)

REF = os.path.join(FORK, 'obo')
SRC = os.path.join(FORK, 'src', 'obo.obo')
# live peak, in bytes, of the port compiling itself: about 3% above the measured
# figure. Lower it when memory improves; raise it only for a reason recorded in FORK.md.
PEAK_BUDGET = 1_777_000     # raised from 1,351,000 for the target checks: see FORK.md


def outcome(exe, src_path, out_path):
    p = subprocess.run([exe, src_path, '-o', out_path, '--rt', FORK], capture_output=True, text=True,
                       env=dict(os.environ, OBO_CHECK='1'), timeout=120)
    if p.returncode == 0:
        return ('ok', open(out_path, 'rb').read())
    if p.returncode == 1 and p.stderr.startswith(src_path + ':'):
        return ('error', p.stderr[len(src_path) + 1:].strip())
    return ('crash', f"exit {p.returncode}: {p.stderr.strip()[:200]}")


def main():
    # a private directory per run, so that two runs (or two worktrees) do not share the port's executable
    tmp = tempfile.mkdtemp(prefix='obo-port-compare-')
    try:
        compare(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def compare(tmp):
    PORT = os.path.join(tmp, 'obo_port')
    b = subprocess.run([REF, SRC, '-o', PORT, '--rt', FORK], capture_output=True, text=True)
    if b.returncode != 0:
        raise SystemExit("the port does not compile: " + b.stderr)
    cases = []
    for f in sorted(glob.glob(os.path.join(FORK, 'tests', 'run', '*.obx')) + glob.glob(os.path.join(FORK, 'examples', '*.obx')) +
                    glob.glob(os.path.join(FORK, 'bench', '*.obx')) + glob.glob(os.path.join(ROOT, 'tests', 'run', '*.obx')) +
                    glob.glob(os.path.join(ROOT, 'examples', '*.obx')) + [SRC]):
        cases.append((os.path.relpath(f, ROOT), open(f, 'rb').read()))
    for f in sorted(glob.glob(os.path.join(FORK, 'tests', 'errors', '*.obx')) + glob.glob(os.path.join(ROOT, 'tests', 'errors', '*.obx'))):
        for name, text, _, _ in run.error_cases(f):
            cases.append((name, text.encode()))
    same, failures = 0, []
    for k, (name, data) in enumerate(cases):
        src = os.path.join(tmp, f'case{k}.obx')
        open(src, 'wb').write(data)
        a = outcome(REF, src, src + '.ref')
        b = outcome(PORT, src, src + '.port')
        if a == b:
            same += 1
        else:
            show = lambda o: o[1] if o[0] != 'ok' else f"compiles ({len(o[1])} bytes)"
            failures.append(f"{name}: obo {show(a)!r}; port {show(b)!r}")
    p = subprocess.run([PORT, SRC, '-o', os.path.join(tmp, 'self'), '--rt', FORK], capture_output=True, text=True,
                       env=dict(os.environ, OBO_CHECK='1', OBO_STATS='1'), timeout=120)
    m = re.search(r'obo: (\d+) peak bytes, (\d+) allocated', p.stderr)
    if p.returncode != 0 or not m:
        failures.insert(0, f"memory: no OBO_STATS report (exit {p.returncode}): {p.stderr.strip()[:200]}")
    else:
        peak, total = int(m.group(1)), int(m.group(2))
        print(f"memory: compiling itself, the port peaks at {peak:,} bytes live "
              f"(budget {PEAK_BUDGET:,}) and allocates {total:,} in total")
        if peak > PEAK_BUDGET:
            failures.insert(0, f"memory: live peak {peak:,} bytes is over the budget of {PEAK_BUDGET:,}")
    for f in failures[:12]:
        print("FAIL", f)
    print(f"\nstage B: {same} of {len(cases)} identical")
    sys.exit(1 if failures else 0)


if __name__ == '__main__':
    main()
