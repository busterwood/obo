#!/usr/bin/env python3
"""Stage B of the port: the ported compiler (obo/src/obo.obo, built by obo)
must compile every program and error case as obo does, and its executables must
behave as obo's do: the same compile outcome (success, or the same error message at
the same line:col), and for a program that compiles, the same stdout, stderr and exit
code when run with OBO_CHECK=1. The executables' bytes may differ, so code generation
can change; the fixed point (make promote) still compares the compiler's own bytes.
The compiler itself must have no leaks and stay within its memory budget.

    python3 obo/tests/port_compare.py
    OBO_REF=obo/seed/obo python3 obo/tests/port_compare.py   # against the seed instead"""
import concurrent.futures
import glob
import hashlib
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
FORK = os.path.dirname(HERE)
ROOT = os.path.join(os.path.dirname(FORK), 'obx')
sys.path.insert(0, os.path.join(ROOT, 'tests'))
import run  # noqa: E402  (for error_cases)

REF = os.path.abspath(os.environ.get('OBO_REF', os.path.join(FORK, 'obo')))
SRC = os.path.join(FORK, 'src', 'obo.obo')
# live peak, in bytes, of the port compiling itself: about 3% above the measured
# figure. Lower it when memory improves; raise it only for a reason recorded in FORK.md.
# Set after the function records moved out of the symbols: the measured peak is 954,896 bytes
# (FORK.md, "Peak memory (done)").
PEAK_BUDGET = 983_000


# REF's outcome for each case text, with the seconds its compile and run took: a mutant's
# comparisons (mutate_port.py) reuse the outcomes of the comparison before them
REF_OUTCOMES = {}

# quick mode: the port's compiles and runs may map at most this much address space, so a runaway mutant fails fast.
# The runtime reserves about 4.5 GiB up front (a 4 GiB heap, a 32 MiB starts table, a 512 MiB mstack), so a
# 4 GiB cap would stop the runtime itself; the cap is 5 GiB, which leaves room for the process.
ADDRESS_SPACE = 5 << 30


def capped(then=None):
    """A preexec_fn for the port's own compiles and runs: then (if given), and the address-space cap."""
    def limits():
        import resource
        if then:
            then()
        resource.setrlimit(resource.RLIMIT_AS, (ADDRESS_SPACE, ADDRESS_SPACE))
    return limits


def timed_out(o):
    """True for an outcome that is a load timeout: the compile had no result in time, or the run timed out."""
    return o == ('ok', ('timeout', b'', b'')) or (o[0] == 'crash' and o[1].startswith('no result in'))


def outcome(exe, src_path, out_path, scratch, compile_limit=120, run_limit=60, capped_run=False):
    """('ok', (exit code, stdout, stderr)) for a program that compiles, run with
    OBO_CHECK=1 and a fresh scratch directory as its argument (as run.py does);
    ('error', 'line:col: message') or ('crash', ...) for one that does not. The case's
    directory is written <tmp> in the result, so outcomes from different directories
    compare. Also returns the seconds the compile and the run took. capped_run puts the
    compile and the run under the address-space cap (the port's own, in quick mode)."""
    tmp = os.path.dirname(src_path)
    t0 = time.monotonic()
    try:
        p = subprocess.run([exe, src_path, '-o', out_path, '--rt', FORK], capture_output=True, text=True,
                           env=dict(os.environ, OBO_CHECK='1'), timeout=compile_limit,
                           preexec_fn=capped() if capped_run else None)
    except subprocess.TimeoutExpired:
        return ('crash', f"no result in {compile_limit:.0f} s"), compile_limit, 0
    t1 = time.monotonic()
    if p.returncode == 1 and p.stderr.startswith(src_path + ':'):
        return ('error', p.stderr[len(src_path) + 1:].strip().replace(tmp, '<tmp>')), t1 - t0, 0
    if p.returncode != 0:
        return ('crash', f"exit {p.returncode}: {p.stderr.strip().replace(tmp, '<tmp>')[:200]}"), t1 - t0, 0
    shutil.rmtree(scratch, ignore_errors=True)
    os.mkdir(scratch)                    # the same path for both runs, so output naming it agrees
    try:
        r = subprocess.run([out_path, scratch], capture_output=True, timeout=run_limit,
                           preexec_fn=capped(run._limits) if capped_run else run._limits,
                           env=dict(os.environ, OBO_CHECK='1'))
    except subprocess.TimeoutExpired:
        return ('ok', ('timeout', b'', b'')), t1 - t0, run_limit
    t2 = time.monotonic()
    tmpb = tmp.encode()
    return ('ok', (r.returncode, r.stdout.replace(tmpb, b'<tmp>'), r.stderr.replace(tmpb, b'<tmp>'))), t1 - t0, t2 - t1


def main():
    # a private directory per run, so that two runs (or two worktrees) do not share the port's executable
    tmp = tempfile.mkdtemp(prefix='obo-port-compare-')
    try:
        lines, failures, _ = compare(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    for line in lines:
        print(line)
    sys.exit(1 if failures else 0)


def cases_of(src):
    """(name, text) of every case: the programs, src itself, and the error cases."""
    cases = []
    for f in sorted(glob.glob(os.path.join(FORK, 'tests', 'run', '*.obx')) + glob.glob(os.path.join(FORK, 'examples', '*.obx')) +
                    glob.glob(os.path.join(FORK, 'bench', '*.obx')) + glob.glob(os.path.join(ROOT, 'tests', 'run', '*.obx')) +
                    glob.glob(os.path.join(ROOT, 'examples', '*.obx')) + [src]):
        cases.append((os.path.relpath(f, ROOT), open(f, 'rb').read()))
    for f in sorted(glob.glob(os.path.join(FORK, 'tests', 'errors', '*.obx')) + glob.glob(os.path.join(ROOT, 'tests', 'errors', '*.obx'))):
        for name, text, _, _ in run.error_cases(f):
            cases.append((name, text.encode()))
    return cases


def compare(tmp, src=SRC, quick=False):
    """Compares the port built from src with REF on every case, in the directory tmp.
    Returns the report's lines, the failures, and the port's live peak compiling itself
    (None if it did not report one). quick, for a mutant: stops at the first failure,
    and allows each case 20 times what REF took, so a mutant that loops is soon caught."""
    PORT = os.path.join(tmp, 'obo_port')
    t0 = time.monotonic()
    b = subprocess.run([REF, src, '-o', PORT, '--rt', FORK], capture_output=True, text=True)
    if b.returncode != 0:
        if quick:
            return [], ["the port does not compile: " + b.stderr.strip()[:200]], None
        raise SystemExit("the port does not compile: " + b.stderr)
    build = time.monotonic() - t0
    lines, failures, peak = [], [], None
    try:
        p = subprocess.run([PORT, src, '-o', os.path.join(tmp, 'self'), '--rt', FORK], capture_output=True, text=True,
                           env=dict(os.environ, OBO_CHECK='1', OBO_STATS='1'),
                           timeout=min(120, max(10, 20 * build)) if quick else 120,
                           preexec_fn=capped() if quick else None)
        m = re.search(r'obo: (\d+) peak bytes, (\d+) allocated', p.stderr)
    except subprocess.TimeoutExpired:
        p, m = subprocess.CompletedProcess([], 'timeout', '', ''), None
    if p.returncode != 0 or not m:
        failures.append(f"memory: no OBO_STATS report (exit {p.returncode}): {p.stderr.strip()[:200]}")
    else:
        peak, total = int(m.group(1)), int(m.group(2))
        lines.append(f"memory: compiling itself, the port peaks at {peak:,} bytes live "
                     f"(budget {PEAK_BUDGET:,}) and allocates {total:,} in total")
        if peak > PEAK_BUDGET:
            failures.append(f"memory: live peak {peak:,} bytes is over the budget of {PEAK_BUDGET:,}")
    if quick and failures:
        return lines, failures, peak
    cases = cases_of(src)

    def both(text):
        h = hashlib.sha1(text).hexdigest()[:16]
        case = os.path.join(tmp, f'case-{h}.obx')
        open(case, 'wb').write(text)
        scratch = os.path.join(tmp, f'scratch-{h}')
        ref = REF_OUTCOMES.get(text)
        if ref is None:
            ref = REF_OUTCOMES[text] = outcome(REF, case, case + '.ref', scratch)
        o, tc, tr = ref
        limits = (min(120, max(10, 20 * tc)), min(60, max(5, 20 * tr))) if quick else (120, 60)
        p = outcome(PORT, case, case + '.port', scratch, *limits, capped_run=quick)[0]
        if quick and o != p and timed_out(p):
            # a timeout on a loaded machine is not a mismatch: judge the case again with the full limits
            p = outcome(PORT, case, case + '.port', scratch, 120, 60, capped_run=True)[0]
        return o, p
    results = {}
    with concurrent.futures.ThreadPoolExecutor(os.cpu_count()) as pool:   # the programs run for seconds
        futures = {pool.submit(both, text): text for text in dict.fromkeys(text for _, text in cases)}
        for f in concurrent.futures.as_completed(futures):
            a, b = results[futures[f]] = f.result()
            if quick and a != b:
                pool.shutdown(cancel_futures=True)
                break
    same = 0
    for name, text in cases:
        if text not in results:
            continue
        a, b = results[text]
        if a == b:
            same += 1
        else:
            show = lambda o: o[1] if o[0] != 'ok' else f"runs: exit {o[1][0]}, stdout {o[1][1][-80:]!r}, stderr {o[1][2][-80:]!r}"
            failures.append(f"{name}: obo {show(a)!r}; port {show(b)!r}")
    lines += ["FAIL " + f for f in failures[:12]]
    lines.append(f"\nstage B: {same} of {len(cases)} behave the same")
    return lines, failures, peak


if __name__ == '__main__':
    main()
