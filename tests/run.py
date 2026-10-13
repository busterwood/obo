#!/usr/bin/env python3
"""obx test suite.

    python3 tests/run.py               run everything
    python3 tests/run.py -k optional   only tests whose name contains "optional"
    python3 tests/run.py -v            list every test, not just failures
    python3 tests/run.py -x stage/     everything except the stage comparisons
    python3 tests/run.py --fuzz 200    more randomised expression programs

The compiler under test is ./obxc (built by make from selfhost/obxc.obx), or
$OBXC if set.

Test kinds
  tests/run/*.obx      Programs compiled and run. Expectations are comments:
                         // expect: <exact stdout line>        (in order)
                         // expect-exit: <code>                (default 0; -8 = SIGFPE)
                         // expect-stderr: <substring>
                       Each gets a scratch directory of its own as argv[1].
                       Each also runs under OBX_GC=stress (a collection at every
                       allocation, freed memory poisoned) unless it contains
                       "// no-gc-stress". Normal runs use OBX_GC=poison.
  tests/errors/*.obx   Programs that must fail to compile. A file may hold many
                       cases, each starting with a line "// === <name>"; lines
                       before the first case are a prelude shared by all of them.
                       Each case has exactly one "// error: <substring>" comment
                       on the line where the error must be reported.
  tests/golden/*.out   Expected stdout of examples/<name>.obx; a line "*" matches
                       any single line.
  encoder              Every x64 instruction form the code generator uses, run
                       through obxc --encode and cross-checked against objdump.
  fuzz                 Random expression programs checked against Python.
  determinism          Compiling the same source twice gives identical output.
  stage                The compiler under test compared with bootstrap/obxc, the
                       trusted previous stage: compiled programs, error messages,
                       token streams and encoder output must all be identical,
                       and the compiler must reach a fixed point compiling itself.
                       A deliberate change shows up here; see bootstrap/README.md.
  tokens               Token dumps of selected files against golden copies in
                       tests/tokens/, so scanner positions are pinned down.
  gcstress             The run/ programs again, collecting at every allocation.
"""
import concurrent.futures as cf
import os
import re
import subprocess
import sys
import tempfile
import time
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

# The compiler under test: ./obxc by default (make builds it); tests/mutate.py
# points OBXC at mutated compilers.
OBXC = os.path.abspath(os.environ.get('OBXC', os.path.join(ROOT, 'obxc')))
BOOTSTRAP = os.path.join(ROOT, 'bootstrap', 'obxc')
TMP = tempfile.mkdtemp(prefix='obx-tests-')


class Fail(Exception):
    pass


class CompileError(Exception):
    """A compile error, as 'line:col: message'."""


_counter = [0]
_counter_lock = __import__('threading').Lock()


def unique(name):
    with _counter_lock:
        _counter[0] += 1
        return os.path.join(TMP, f"{_counter[0]}_{re.sub(r'[^A-Za-z0-9_.-]', '_', name)}")


def compile_with(compiler, src, name):
    """Compiles src (str or bytes) with the given obxc; returns the executable's
    path, or raises CompileError with obxc's 'line:col: message'."""
    base = unique(name)
    path = base + '.obx'
    with open(path, 'wb') as f:
        f.write(src.encode('utf-8') if isinstance(src, str) else src)
    p = subprocess.run([compiler, path, '-o', base, '--rt', ROOT], capture_output=True, text=True, timeout=120)
    if p.returncode == 0:
        return base
    if p.returncode == 1 and p.stderr.startswith(path + ':'):
        raise CompileError(p.stderr[len(path) + 1:].strip())
    raise Fail(f"compiler failed with exit code {p.returncode}: {p.stderr.strip()[:300]}")


def build(src, name):
    return compile_with(OBXC, src, name)


def _limits():
    import resource
    resource.setrlimit(resource.RLIMIT_FSIZE, (16 << 20, 16 << 20))   # runaway output
    resource.setrlimit(resource.RLIMIT_CPU, (30, 30))                 # runaway loops


def run_exe(exe, timeout=60, gc_mode='poison'):
    """Runs a test program with output going to files, so a runaway program
    cannot exhaust the runner's memory. By default freed memory is poisoned,
    so reading anything the collector freed by mistake shows up as garbage."""
    env = dict(os.environ, OBX_GC=gc_mode)
    scratch = tempfile.mkdtemp(prefix='scratch-', dir=TMP)    # argv[1]: a directory of its own
    with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
        p = subprocess.run([exe, scratch], stdout=out, stderr=err, timeout=timeout,
                           preexec_fn=_limits, env=env)
        out.seek(0)
        err.seek(0)
        return (p.returncode, out.read(1 << 20).decode('utf-8', 'replace'),
                err.read(1 << 16).decode('utf-8', 'replace'))


# ---------------------------------------------------------------- program tests
def program_test(path, gc_mode='poison'):
    src = open(path).read()
    expect, exit_code, stderr_subs = [], 0, []
    for line in src.splitlines():
        m = re.search(r'// expect: ?(.*)$', line)
        if m:
            expect.append(m.group(1))
        m = re.search(r'// expect-exit: *(-?\d+)', line)
        if m:
            exit_code = int(m.group(1))
        m = re.search(r'// expect-stderr: *(.*)$', line)
        if m:
            stderr_subs.append(m.group(1).strip())
    try:
        exe = build(src, gc_mode + '_' + os.path.basename(path))
    except CompileError as e:
        raise Fail(f"did not compile: {e}")
    code, out, err = run_exe(exe, gc_mode=gc_mode)
    got = out.splitlines()
    problems = []
    if code != exit_code:
        problems.append(f"exit code {code}, expected {exit_code}" + (f"; stderr: {err.strip()}" if err else ""))
    for s in stderr_subs:
        if s not in err:
            problems.append(f"stderr lacks {s!r}; got {err.strip()!r}")
    if got != expect:
        problems.append(first_difference(expect, got))
    if problems:
        raise Fail('\n'.join(problems))


def first_difference(expect, got):
    for i in range(max(len(expect), len(got))):
        e = expect[i] if i < len(expect) else '<nothing>'
        g = got[i] if i < len(got) else '<nothing>'
        if e != '*' and e != g:
            return f"output line {i + 1}: expected {e!r}, got {g!r}"
    return "output differs"


def golden_test(name):
    src = open(os.path.join(ROOT, 'examples', name + '.obx')).read()
    expect = open(os.path.join(HERE, 'golden', name + '.out')).read().splitlines()
    try:
        exe = build(src, 'example_' + name)
    except CompileError as e:
        raise Fail(f"did not compile: {e}")
    code, out, err = run_exe(exe)
    got = out.splitlines()
    if code != 0:
        raise Fail(f"exit code {code}; stderr: {err.strip()}")
    if len(got) != len(expect) or any(e != '*' and e != g for e, g in zip(expect, got)):
        raise Fail(first_difference(expect, got))


# ------------------------------------------------------------------ error tests
def error_cases(path):
    lines = open(path).read().split('\n')
    starts = [i for i, l in enumerate(lines) if l.startswith('// ===')]
    if not starts:
        return [(os.path.basename(path), '\n'.join(lines), 0, len(lines))]
    prelude_end = starts[0]
    cases = []
    for k, s in enumerate(starts):
        e = starts[k + 1] if k + 1 < len(starts) else len(lines)
        name = lines[s][6:].strip() or f"case{k + 1}"
        text = [l if (i < prelude_end or s <= i < e) else '' for i, l in enumerate(lines)]
        cases.append((f"{os.path.basename(path)}:{name}", '\n'.join(text), s, e))
    return cases


def error_test(src, lo, hi):
    lines = src.split('\n')
    marks = [(i + 1, re.search(r'// error: *(.*?)\s*$', l).group(1))
             for i, l in enumerate(lines) if re.search(r'// error:', l)]
    if len(marks) != 1:
        raise Fail(f"test must have exactly one '// error:' comment, has {len(marks)}")
    line, sub = marks[0]
    try:
        build(src, 'error_case')
    except CompileError as e:
        msg = str(e)
        got_line = int(msg.split(':')[0])
        if got_line != line or sub not in msg:
            raise Fail(f"expected error at line {line} containing {sub!r}\n       got {msg}")
        return
    raise Fail(f"compiled, but expected error at line {line}: {sub!r}")


# ------------------------------------------------------------- golden tokens
def tokens_test(name):
    """obxc --tokens on tests/tokens/NAME.in must match NAME.tokens exactly."""
    inp = os.path.join(HERE, 'tokens', name + '.in')
    want = open(os.path.join(HERE, 'tokens', name + '.tokens'), 'rb').read().decode('latin-1').splitlines()
    with tempfile.TemporaryFile() as out:
        subprocess.run([OBXC, '--tokens', inp], stdout=out, timeout=30, preexec_fn=_limits)
        out.seek(0)
        got = out.read().decode('latin-1').splitlines()
    if got != want:
        raise Fail(first_difference(want, got))


# ----------------------------------------------------------- file permissions
def executable_bit_test():
    """writeFile's executable flag, checked from outside the program."""
    src = """fn main() {
    let d = args()[1]
    let b = new []byte(1)
    if !writeFile(d + "/x", b, true) || !writeFile(d + "/p", b, false) { exit(1) }
}
"""
    exe = build(src, 'executable_bit')
    scratch = tempfile.mkdtemp(prefix='perm-', dir=TMP)
    p = subprocess.run([exe, scratch], capture_output=True, timeout=30)
    if p.returncode != 0:
        raise Fail(f"exit code {p.returncode}")
    x = os.stat(os.path.join(scratch, 'x')).st_mode & 0o777
    n = os.stat(os.path.join(scratch, 'p')).st_mode & 0o777
    if not (x & 0o100) or (n & 0o111):
        raise Fail(f"modes were {oct(x)} and {oct(n)}; expected the first executable and the second not")


# ------------------------------------------------------------------ determinism
def determinism_test(path):
    src = open(path, 'rb').read()
    a, b = build(src, 'det_a'), build(src, 'det_b')
    if open(a, 'rb').read() != open(b, 'rb').read():
        raise Fail("two compilations of the same source differ")


# ---------------------------------------------------------------------- driver
def main():
    args = sys.argv[1:]
    verbose = '-v' in args
    pattern = args[args.index('-k') + 1] if '-k' in args else ''
    exclude = args[args.index('-x') + 1] if '-x' in args else None
    if not os.path.exists(OBXC):
        print(f"no compiler at {OBXC}: run make first")
        sys.exit(2)
    nfuzz = int(args[args.index('--fuzz') + 1]) if '--fuzz' in args else 40
    tests = []                                              # (name, fn)
    rdir = os.path.join(HERE, 'run')
    for f in sorted(os.listdir(rdir)):
        if f.endswith('.obx'):
            p = os.path.join(rdir, f)
            tests.append((f"run/{f}", lambda p=p: program_test(p)))
            tests.append((f"determinism/{f}", lambda p=p: determinism_test(p)))
            if '// no-gc-stress' not in open(p).read():
                tests.append((f"gcstress/{f}", lambda p=p: program_test(p, 'stress')))
    edir = os.path.join(HERE, 'errors')
    for f in sorted(os.listdir(edir)):
        if f.endswith('.obx'):
            for name, src, lo, hi in error_cases(os.path.join(edir, f)):
                tests.append((f"errors/{name}", lambda s=src, lo=lo, hi=hi: error_test(s, lo, hi)))
    for f in sorted(os.listdir(os.path.join(HERE, 'golden'))):
        if f.endswith('.out'):
            n = f[:-4]
            tests.append((f"golden/{n}", lambda n=n: golden_test(n)))
    tests.append(("io/executable-bit", executable_bit_test))
    for f in sorted(os.listdir(os.path.join(HERE, 'tokens'))):
        if f.endswith('.in'):
            tests.append((f"tokens/{f[:-3]}", lambda n=f[:-3]: tokens_test(n)))
    import encoder_test
    for name, fn in encoder_test.tests(Fail, OBXC, unique):
        tests.append((f"encoder/{name}", fn))
    import stage_test
    for name, fn in stage_test.tests(OBXC, BOOTSTRAP, ROOT, compile_with, CompileError, Fail, unique,
                                     error_cases, nfuzz):
        tests.append((f"stage/{name}", fn))
    import fuzz
    for seed in range(1, nfuzz + 1):
        tests.append((f"fuzz/seed{seed}", lambda s=seed: fuzz.check(s, build, run_exe, Fail)))
    tests = [t for t in tests if pattern in t[0] and (exclude is None or exclude not in t[0])]

    def run_one(t):
        name, fn = t
        t0 = time.perf_counter()
        try:
            fn()
            return name, None, time.perf_counter() - t0
        except Fail as e:
            return name, str(e), time.perf_counter() - t0
        except subprocess.TimeoutExpired:
            return name, "timed out", time.perf_counter() - t0
        except Exception:
            return name, "test crashed:\n" + traceback.format_exc(), time.perf_counter() - t0

    t0 = time.perf_counter()
    failures = []
    groups = {}
    with cf.ThreadPoolExecutor(max_workers=os.cpu_count() or 4) as ex:
        for name, err, dt in ex.map(run_one, tests):
            g = name.split('/')[0]
            ok, total = groups.get(g, (0, 0))
            groups[g] = (ok + (err is None), total + 1)
            if err:
                failures.append((name, err))
                print(f"FAIL {name}\n     " + err.replace('\n', '\n     '))
            elif verbose:
                print(f"ok   {name} ({dt * 1000:.0f} ms)")
    print()
    for g, (ok, total) in groups.items():
        print(f"  {g:<12} {ok}/{total}")
    print(f"\n{len(tests) - len(failures)} passed, {len(failures)} failed "
          f"in {time.perf_counter() - t0:.1f} s")
    sys.exit(1 if failures else 0)


if __name__ == '__main__':
    main()
