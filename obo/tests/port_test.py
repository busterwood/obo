#!/usr/bin/env python3
"""The self-hosting port: obo/src/obo.obo is obo's compiler rewritten in the obo
dialect (ownership, references, handles). It is built with ./obo and must behave
exactly like ./obo itself, with no leaks. Its --tokens and --encode modes are
checked here: identical token streams on every source file we have, identical
encoder output, and identical results on fuzzed scanner input. The whole
compiler is checked against obo in port_compare.py.

    python3 obo/tests/port_test.py"""
import glob
import os
import random
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
FORK = os.path.dirname(HERE)
ROOT = os.path.join(os.path.dirname(FORK), 'obx')
sys.path.insert(0, os.path.join(ROOT, 'tests'))
import stage_test  # noqa: E402  (scanner fragments and encoder scripts)

REF = os.path.join(FORK, 'obo')
PORT = '/tmp/obo_own_test'
TMP = '/tmp/obo_port_inputs'


def run(exe, mode, path):
    p = subprocess.run([exe, mode, path], capture_output=True, env=dict(os.environ, OBO_CHECK='1'), timeout=60)
    if p.returncode not in (0, 1):
        raise SystemExit(f"{os.path.basename(exe)} {mode} {path}: exit {p.returncode}: {p.stderr[:200]!r}")
    return p.stdout


def compare(mode, path, label, failures):
    a, b = run(REF, mode, path), run(PORT, mode, path)
    if a != b:
        la, lb = a.decode('latin-1').splitlines(), b.decode('latin-1').splitlines()
        i = next((k for k in range(min(len(la), len(lb))) if la[k] != lb[k]), min(len(la), len(lb)))
        failures.append(f"{label}: line {i + 1}: obo {la[i] if i < len(la) else '<end>'!r}, port {lb[i] if i < len(lb) else '<end>'!r}")


def main():
    b = subprocess.run([REF, os.path.join(FORK, 'src', 'obo.obo'), '-o', PORT, '--rt', FORK],
                       capture_output=True, text=True)
    if b.returncode != 0:
        raise SystemExit("the port does not compile: " + b.stderr)
    os.makedirs(TMP, exist_ok=True)
    files = sorted(glob.glob(os.path.join(ROOT, 'tests', 'run', '*.obx')) +
                   glob.glob(os.path.join(ROOT, 'tests', 'errors', '*.obx')) +
                   glob.glob(os.path.join(ROOT, 'examples', '*.obx')) +
                   glob.glob(os.path.join(FORK, 'tests', '*', '*.obx')) +
                   glob.glob(os.path.join(FORK, 'bench', '*.obx')) +
                   [os.path.join(FORK, 'src', 'obo.obo'),
                    os.path.join(ROOT, 'src', 'obxc.obx')])
    failures, count = [], 0
    for f in files:
        compare('--tokens', f, os.path.relpath(f, ROOT), failures)
        count += 1
    r = random.Random(7)
    for case in range(300):
        data = ''.join(r.choice(stage_test.FRAGMENTS) + (' ' if r.random() < 0.3 else '')
                       for _ in range(r.randint(1, 120))).encode('latin-1')
        path = os.path.join(TMP, f'fuzz{case}.txt')
        open(path, 'wb').write(data)
        compare('--tokens', path, f'scanner fuzz {case}', failures)
        count += 1
    for part in ('memory', 'registers', 'immediates', 'jumps'):
        path = os.path.join(TMP, f'enc_{part}.txt')
        open(path, 'w').write('\n'.join(stage_test.encoder_script(part)) + '\n')
        compare('--encode', path, f'encoder {part}', failures)
        count += 1
    for f in failures[:10]:
        print("FAIL", f)
    print(f"\nport tokens and encoder: {count - len(failures)} of {count} identical, no leaks")
    sys.exit(1 if failures else 0)


if __name__ == '__main__':
    main()
