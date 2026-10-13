#!/usr/bin/env python3
"""Test runner for obo, the ownership fork.

Reuses the mainline runner (../../tests/run.py) with obo as the compiler and the
fork's runtime. Every program runs with OBO_CHECK=1, so a program that ends with
live objects fails (exit 5), as does a double free (exit 6); freed memory is
poisoned, so use after free shows up as garbage.

    python3 fork/tests/run_fork.py [-k pattern] [-v]

  fork/tests/run/*.obx      programs, with // expect: comments as in mainline
  fork/tests/errors/*.obx   ownership errors, as in mainline (// === cases)
"""
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
FORK = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(os.path.dirname(FORK), 'tests'))
os.environ['OBO_CHECK'] = '1'
import run  # noqa: E402

run.OBXC = os.path.abspath(os.environ.get('OBO', os.path.join(FORK, 'obo')))
run.ROOT = FORK                                   # --rt: the fork's runtime


def main():
    args = sys.argv[1:]
    pattern = args[args.index('-k') + 1] if '-k' in args else ''
    verbose = '-v' in args
    tests = []
    for f in sorted(os.listdir(os.path.join(HERE, 'run'))):
        if f.endswith('.obx'):
            p = os.path.join(HERE, 'run', f)
            tests.append((f"run/{f}", lambda p=p: run.program_test(p)))
    for f in sorted(os.listdir(os.path.join(HERE, 'errors'))):
        if f.endswith('.obx'):
            for name, src, lo, hi in run.error_cases(os.path.join(HERE, 'errors', f)):
                tests.append((f"errors/{name}", lambda s=src, lo=lo, hi=hi: run.error_test(s, lo, hi)))
    tests = [t for t in tests if pattern in t[0]]
    t0 = time.perf_counter()
    failed = 0
    for name, fn in tests:
        try:
            fn()
            if verbose:
                print(f"ok   {name}")
        except Exception as e:
            failed += 1
            print(f"FAIL {name}\n     " + str(e).replace('\n', '\n     '))
    print(f"\n{len(tests) - failed} passed, {failed} failed in {time.perf_counter() - t0:.1f} s")
    sys.exit(1 if failed else 0)


if __name__ == '__main__':
    main()
