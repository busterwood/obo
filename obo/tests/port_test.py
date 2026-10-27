#!/usr/bin/env python3
"""The self-hosting port: obo/src/obo.obo is obo's compiler rewritten in the obo
dialect (ownership, references, handles). It is built with ./obo and must behave
exactly like ./obo itself, with no leaks. Its --tokens and --encode modes are
checked here: identical token streams on every source file we have, identical
encoder output, and identical results on fuzzed scanner input. Its encoder is also
cross-checked against objdump, which needs no compiler: mainline's groups
(obx/tests/encoder_test.py) and the forms only obo emits (OBO_GROUPS). The whole
compiler is checked against obo in port_compare.py.

    python3 obo/tests/port_test.py [-j N]

  -j, --jobs N  compare up to N inputs at once (default 1). Each input is a file
                of its own and both compilers only read it, so a comparison is
                independent; the failures are reported in input order either way.
                The encoder cross-checks against objdump run afterwards, one at a
                time, because they share the temporary work list.
"""
import concurrent.futures
import glob
import os
import random
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
FORK = os.path.dirname(HERE)
ROOT = os.path.join(os.path.dirname(FORK), 'obx')
sys.path.insert(0, os.path.join(ROOT, 'tests'))
import stage_test  # noqa: E402  (scanner fragments and encoder scripts)
import encoder_test  # noqa: E402  (objdump cross-checks)

REF = os.path.join(FORK, 'obo')
SRC = os.path.join(FORK, 'src', 'obo.obo')


class Fail(Exception):
    pass


def group_push_immediates(Fail, obo, unique):
    """push imm8 and imm32, sign-extended: the arguments of obo calls"""
    cases = [(f"pushi {v}", f"push {encoder_test.hx(v)}")
             for v in (0, 1, -1, 40, 127, -128, 128, -129, 0x08000000, 2 ** 31 - 1, -2 ** 31)]
    encoder_test.check(cases, Fail, obo, unique)


OBO_GROUPS = [group_push_immediates]


def run(exe, mode, path):
    p = subprocess.run([exe, mode, path], capture_output=True, env=dict(os.environ, OBO_CHECK='1'), timeout=60)
    if p.returncode not in (0, 1):
        raise SystemExit(f"{os.path.basename(exe)} {mode} {path}: exit {p.returncode}: {p.stderr[:200]!r}")
    return p.stdout


def compare(mode, path, label):
    """The first difference between obo's output and the port's, or None. The
    inputs are read-only and each comparison names its own case, so threads do
    not share anything."""
    a, b = run(REF, mode, path), run(PORT, mode, path)
    if a != b:
        la, lb = a.decode('latin-1').splitlines(), b.decode('latin-1').splitlines()
        i = next((k for k in range(min(len(la), len(lb))) if la[k] != lb[k]), min(len(la), len(lb)))
        return f"{label}: line {i + 1}: obo {la[i] if i < len(la) else '<end>'!r}, port {lb[i] if i < len(lb) else '<end>'!r}"
    return None


def main():
    args = sys.argv[1:]
    jobs = int(args[args.index('-j') + 1]) if '-j' in args else \
        int(args[args.index('--jobs') + 1]) if '--jobs' in args else 1
    # a private directory per run, so that concurrent runs do not share the port's executable or inputs
    work = tempfile.mkdtemp(prefix='obo-port-test-')
    try:
        check(work, jobs)
    finally:
        shutil.rmtree(work, ignore_errors=True)


def check(work, jobs=1):
    global PORT, TMP
    PORT = os.path.join(work, 'obo_own_test')
    TMP = os.path.join(work, 'inputs')
    b = subprocess.run([REF, SRC, '-o', PORT, '--rt', FORK],
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
    tasks = [('--tokens', f, os.path.relpath(f, ROOT)) for f in files]
    # the scanner fuzz inputs and the encoder scripts are written before any
    # comparison starts, so that no thread writes a shared file
    r = random.Random(7)
    for case in range(300):
        data = ''.join(r.choice(stage_test.FRAGMENTS) + (' ' if r.random() < 0.3 else '')
                       for _ in range(r.randint(1, 120))).encode('latin-1')
        path = os.path.join(TMP, f'fuzz{case}.txt')
        open(path, 'wb').write(data)
        tasks.append(('--tokens', path, f'scanner fuzz {case}'))
    for part in ('memory', 'registers', 'immediates', 'jumps'):
        path = os.path.join(TMP, f'enc_{part}.txt')
        open(path, 'w').write('\n'.join(stage_test.encoder_script(part)) + '\n')
        tasks.append(('--encode', path, f'encoder {part}'))
    if jobs > 1:
        with concurrent.futures.ThreadPoolExecutor(jobs) as pool:
            found = list(pool.map(lambda t: compare(*t), tasks))
    else:
        found = [compare(*t) for t in tasks]
    failures = [f for f in found if f]
    count = len(tasks)
    # objdump first, so that a wrong encoding is reported by the oracle rather than as a difference
    names = []
    unique = lambda name: os.path.join(TMP, f"{name}{len(names)}")
    for group in [g for _, g in encoder_test.tests(Fail, PORT, unique)] + [lambda g=g: g(Fail, PORT, unique) for g in OBO_GROUPS]:
        names.append(group)
        count += 1
        try:
            group()
        except Fail as e:
            failures.insert(0, f"objdump: {e}")
    if not names:
        print("objdump not found: the encoder is not cross-checked")
    for f in failures[:10]:
        print("FAIL", f)
    print(f"\nport tokens and encoder: {count - len(failures)} of {count} identical, no leaks; "
          f"{len(names)} encoder groups checked against objdump")
    sys.exit(1 if failures else 0)


if __name__ == '__main__':
    main()
