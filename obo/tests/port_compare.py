#!/usr/bin/env python3
"""Stage B of the port: the ported compiler (obo/src/obo.obo, built by obo)
must compile every program and error case as obo does, and its executables must
behave as obo's do: the same compile outcome (success, or the same error message at
the same line:col), and for a program that compiles, the same stdout, stderr and exit
code when run with OBO_CHECK=1. The executables' bytes may differ, so code generation
can change; the fixed point (make promote) still compares the compiler's own bytes.
The compiler itself must have no leaks and stay within its memory budget.

A tests/run program that starts with "// prelude: io" is compiled with
obo/lib/io.obo concatenated before it, exactly as run_fork.py does; otherwise
both compilers would reject the library's names and the case would compare two
identical errors without compiling anything.

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
LIB = os.path.join(FORK, 'lib', 'io.obo')
# live peak, in bytes, of the port compiling itself. Lower it when memory improves; raise it only
# for a reason recorded in FORK.md. Raised from 983,000 on 2026-10-26 by the user's decision: the
# measured peak is 954,896 bytes (FORK.md, "Peak memory (done)"), and a budget about 3% above it
# left new checker work only about 28 KB of headroom. Leaks are caught separately, by port_test's leak check.
PEAK_BUDGET = 2_000_000


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
    """(name, text) of every case: the programs, src itself, and the error cases.
    A program that starts with "// prelude: io" gets the library before it, as
    run_fork.py's program_test does, so the case compiles the library and the
    program with both compilers rather than comparing two identical rejections."""
    cases = []
    lib = open(LIB, 'rb').read()
    for f in sorted(glob.glob(os.path.join(FORK, 'tests', 'run', '*.obx')) + glob.glob(os.path.join(FORK, 'examples', '*.obx')) +
                    glob.glob(os.path.join(FORK, 'bench', '*.obx')) + glob.glob(os.path.join(ROOT, 'tests', 'run', '*.obx')) +
                    glob.glob(os.path.join(ROOT, 'examples', '*.obx')) + [src]):
        text = open(f, 'rb').read()
        if text.startswith(b'// prelude: io'):
            text = lib + b'\n' + text
        cases.append((os.path.relpath(f, ROOT), text))
    for f in sorted(glob.glob(os.path.join(FORK, 'tests', 'errors', '*.obx')) + glob.glob(os.path.join(ROOT, 'tests', 'errors', '*.obx'))):
        for name, text, _, _ in run.error_cases(f):
            cases.append((name, text.encode()))
    return cases



# ---------------------------------------------------------------- module trees
#
# Stage 1 modules: compile the same tree with both compilers (same -I and case
# directory), compare the compile outcome, and for one that compiles, its run
# (OBO_CHECK=1, a scratch directory of its own). --deps cases compare the
# depth-first post-order. The seed compiler predates modules, so these are
# skipped when REF is seed/obo. Each tree is compiled once by REF per process;
# mutants reuse those outcomes.

MODDIR = os.path.join(FORK, 'tests', 'modules')
SEED_REF = os.path.realpath(os.path.join(FORK, 'seed', 'obo'))
REF_TREE = {}


def tree_cases():
    out = []
    for kind in ('run', 'errors', 'deps'):
        base = os.path.join(MODDIR, kind)
        if not os.path.isdir(base):
            continue
        for name in sorted(os.listdir(base)):
            d = os.path.join(base, name)
            if os.path.isdir(d):
                out.append((f"modules/{kind}/{name}", kind, d))
    return out


def tree_includes(case_dir):
    out = []
    path = os.path.join(case_dir, 'includes')
    if os.path.exists(path):
        for line in open(path).read().splitlines():
            line = line.strip()
            if line and not line.startswith('#'):
                out.append(os.path.join(case_dir, line))
    return out


def tree_args(exe, case_dir, out_path):
    args = [exe, os.path.join(case_dir, 'main.obx')]
    if out_path is not None:
        args += ['-o', out_path]
    args += ['--rt', FORK, '-I', case_dir]
    for d in tree_includes(case_dir):
        args += ['-I', d]
    return args


def tree_norm(text, case_dir):
    for p in (case_dir + os.sep, case_dir + '/'):
        text = text.replace(p, '')
    return text.strip()


def tree_outcome(exe, kind, case_dir, out_path, scratch, compile_limit=120, run_limit=60, capped_run=False):
    if kind == 'deps':
        args = [exe, '--deps', os.path.join(case_dir, 'main.obx'), '-I', case_dir]
        for d in tree_includes(case_dir):
            args += ['-I', d]
        t0 = time.monotonic()
        try:
            p = subprocess.run(args, capture_output=True, text=True, timeout=compile_limit,
                               preexec_fn=capped() if capped_run else None)
        except subprocess.TimeoutExpired:
            return ('crash', f"no result in {compile_limit:.0f} s"), compile_limit, 0
        t1 = time.monotonic()
        return ('deps', p.returncode, tree_norm(p.stdout, case_dir), tree_norm(p.stderr, case_dir)), t1 - t0, 0
    t0 = time.monotonic()
    try:
        p = subprocess.run(tree_args(exe, case_dir, out_path), capture_output=True, text=True,
                           env=dict(os.environ, OBO_CHECK='1'), timeout=compile_limit,
                           preexec_fn=capped() if capped_run else None)
    except subprocess.TimeoutExpired:
        return ('crash', f"no result in {compile_limit:.0f} s"), compile_limit, 0
    t1 = time.monotonic()
    if p.returncode != 0:
        return ('error', p.returncode, tree_norm(p.stderr, case_dir)), t1 - t0, 0
    shutil.rmtree(scratch, ignore_errors=True)
    os.mkdir(scratch)
    try:
        r = subprocess.run([out_path, scratch], capture_output=True, timeout=run_limit,
                           preexec_fn=capped(run._limits) if capped_run else run._limits,
                           env=dict(os.environ, OBO_CHECK='1'))
    except subprocess.TimeoutExpired:
        return ('ok', ('timeout', b'', b'')), t1 - t0, run_limit
    t2 = time.monotonic()
    sb = scratch.encode()
    return ('ok', (r.returncode, r.stdout.replace(sb, b'<scratch>'), r.stderr.replace(sb, b'<scratch>'))), t1 - t0, t2 - t1


def tree_show(o):
    if o[0] == 'ok':
        return f"runs: exit {o[1][0]}, stdout {o[1][1][-80:]!r}, stderr {o[1][2][-80:]!r}"
    return f"{o[0]} {o[1:]!r}"


def tree_both(name, kind, case_dir, tmp, quick, port):
    main = os.path.join(case_dir, 'main.obx')
    ref_exe = os.path.join(tmp, 'tree-ref-' + name.replace('/', '-'))
    port_exe = os.path.join(tmp, 'tree-port-' + name.replace('/', '-'))
    ref_scratch = os.path.join(tmp, 'tree-ref-scratch-' + name.replace('/', '-'))
    port_scratch = os.path.join(tmp, 'tree-port-scratch-' + name.replace('/', '-'))
    ref = REF_TREE.get(name)
    if ref is None:
        ref = REF_TREE[name] = tree_outcome(REF, kind, case_dir, ref_exe, ref_scratch)
    o, tc, tr = ref
    limits = (min(120, max(10, 20 * tc)), min(60, max(5, 20 * tr))) if quick else (120, 60)
    p = tree_outcome(port, kind, case_dir, port_exe, port_scratch, *limits, capped_run=quick)
    if quick and o != p[0] and timed_out(p[0]):
        p = tree_outcome(port, kind, case_dir, port_exe, port_scratch, 120, 60, capped_run=True)
    return o, p[0]


def token_outcome(exe):
    p = subprocess.run([exe, '--tokens', os.path.join(MODDIR, 'tokens.obx')],
                       capture_output=True, text=True, timeout=60)
    return (p.returncode, p.stdout, p.stderr)


def compare_trees(tmp, quick, port):
    """(same, total, failures) for the Stage 1 module trees and the token dump."""
    if os.path.realpath(REF) == SEED_REF:
        return 0, 0, []
    same, total, failures = 0, 0, []
    for name, kind, case_dir in tree_cases():
        total += 1
        a, b = tree_both(name, kind, case_dir, tmp, quick, port)
        if a == b:
            same += 1
        else:
            failures.append(f"{name}: obo {tree_show(a)!r}; port {tree_show(b)!r}")
            if quick:
                return same, total, failures
    total += 1
    try:
        a, b = token_outcome(REF), token_outcome(port)
    except subprocess.TimeoutExpired:
        failures.append("modules/tokens: a --tokens run timed out")
        return same, total, failures
    if a == b:
        same += 1
    else:
        failures.append(f"modules/tokens: obo {a[0]}; port {b[0]}")
    return same, total, failures


def compare(tmp, src=SRC, quick=False, workers=None):
    """Compares the port built from src with REF on every case, in the directory tmp.
    Returns the report's lines, the failures, and the port's live peak compiling itself
    (None if it did not report one). quick, for a mutant: stops at the first failure,
    and allows each case 20 times what REF took, so a mutant that loops is soon caught.
    workers is the number of case comparisons at once (default: one per core); a
    caller that runs several comparisons side by side passes a smaller number so
    that the total stays about the core count."""
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
    with concurrent.futures.ThreadPoolExecutor(workers or os.cpu_count()) as pool:   # the programs run for seconds
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
    tree_same, tree_total, tree_failures = compare_trees(tmp, quick, PORT)
    failures += tree_failures
    same += tree_same
    total = len(cases) + tree_total
    lines += ["FAIL " + f for f in failures[:12]]
    lines.append(f"\nstage B: {same} of {total} behave the same")
    return lines, failures, peak


if __name__ == '__main__':
    main()
