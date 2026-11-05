#!/usr/bin/env python3
"""The JSON benchmark's Python driver: the same steps as json_bench.obx with Python's json
(its C accelerator), the best of REPS runs of each, the file read once beforehand.

    python3 obx/bench/json/py_bench.py FILE [REPS]      prints: STEP SIZE MS

Python reads numbers as int or float, so its writers drop trailing zeros (12.50 becomes
12.5) and its compact size differs from obx's and jq's, which keep the literal.
"""
import json, sys, time


def best_of(reps, f):
    best = None
    res = None
    for _ in range(reps):
        t0 = time.perf_counter_ns()
        res = f()
        t = time.perf_counter_ns() - t0
        best = t if best is None else min(best, t)
    return res, best


def report(step, size, ns):
    print('%s %d %.2f' % (step, size, ns / 1e6))


def main():
    path = sys.argv[1]
    reps = int(sys.argv[2]) if len(sys.argv) > 2 else 3
    with open(path, encoding='utf-8') as f:
        text = f.read()
    v, t = best_of(reps, lambda: json.loads(text))
    report('parse', len(v), t)
    s, t = best_of(reps, lambda: json.dumps(v, ensure_ascii=False, separators=(',', ':')))
    report('compact', len(s.encode('utf-8')), t)
    s, t = best_of(reps, lambda: json.dumps(v, ensure_ascii=False, indent=2))
    report('pretty', len(s.encode('utf-8')), t)
    n, t = best_of(reps, lambda: sum(it['qty'] for rec in v for it in rec['items']))
    report('query', n, t)
    report('visit', n, t)   # Python has one way


if __name__ == '__main__':
    main()
