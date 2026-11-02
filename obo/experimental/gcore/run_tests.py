#!/usr/bin/env python3
"""Runs every examples/*.gc (expected output, exit 0, no leaks) and every
errors/*.gc (rejected with the expected message)."""
import glob, os, subprocess, sys
here = os.path.dirname(os.path.abspath(__file__))
gc = [sys.executable, os.path.join(here, 'gcore.py')]
fails = 0
for p in sorted(glob.glob(os.path.join(here, 'examples', '*.gc'))):
    want = [l.split('// expect: ', 1)[1].rstrip('\n') for l in open(p) if l.startswith('// expect: ')]
    if subprocess.run(gc + [p]).returncode != 0:
        print('FAIL compile', p); fails += 1; continue
    r = subprocess.run([os.path.splitext(p)[0] + '.bin'], capture_output=True, text=True)
    got = r.stdout.splitlines()
    if r.returncode != 0 or got != want:
        print('FAIL', p, r.returncode, r.stderr.strip()); fails += 1
        for a, b in zip(want, got + [''] * len(want)):
            if a != b: print('  want', repr(a), 'got', repr(b))
    else:
        print('ok  ', os.path.relpath(p, here))
for p in sorted(glob.glob(os.path.join(here, 'errors', '*.gc'))):
    want = next(l.split('// error: ', 1)[1].strip() for l in open(p) if l.startswith('// error: '))
    r = subprocess.run(gc + [p], capture_output=True, text=True)
    if r.returncode != 1 or want not in r.stderr:
        print('FAIL', p, 'want', repr(want), 'got', repr(r.stderr.strip())); fails += 1
    else:
        print('ok  ', os.path.relpath(p, here), '->', r.stderr.strip().split(': ', 2)[-1])
for f in glob.glob(os.path.join(here, '*', '*.bin')) + glob.glob(os.path.join(here, '*', '*.c')):
    os.remove(f)
print('failures:', fails)
sys.exit(1 if fails else 0)
