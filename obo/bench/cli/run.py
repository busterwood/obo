#!/usr/bin/env python3
"""On-demand CLI benchmarks: obo programs and tools against Python 3
equivalents, with GNU tools and the pinned eza as reference points.

    python3 obo/bench/cli/run.py [--quick] [--reps N] [--keep] [--json]
                                 [--eza PATH]

NOT a gate: nothing in obo/tests/ and no CI job runs this script. It builds the
compiler and the repo's wc, sort and eza through the repo's own build rules,
writes its fixtures into a private temporary directory, checks that every obo
result agrees with its Python equivalent (and with a reference where one is
compared), and prints median times. README.md in this directory has the
methodology and the limits of the numbers.
"""
import argparse
import hashlib
import json
import os
import platform
import random
import shlex
import shutil
import signal
import statistics
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))          # obo/bench/cli
FORK = os.path.dirname(os.path.dirname(HERE))              # obo
ROOT = os.path.dirname(FORK)                               # repository root

DEVNULL = subprocess.DEVNULL
PROGRAMS = ('hello', 'fib', 'loopsum', 'sieve')
TEXT_LINE = b'the quick brown fox jumps over the lazy dog %010d\n'

DEFAULT = {
    'fib_n': 32,
    'loop_n': 100_000_000,
    'sieve_n': 10_000_000,
    'text_lines': 2_000_000,          # 55 bytes each: 110,000,000 bytes
    'sort_lines': 1_000_000,
    'names': 10_000,
    'startup_runs': 40,
    'startup_batches': 3,
    'reps_light': 5,
    'reps_heavy': 3,
}
QUICK = {
    'fib_n': 30,
    'loop_n': 10_000_000,
    'sieve_n': 1_000_000,
    'text_lines': 200_000,            # 11,000,000 bytes
    'sort_lines': 100_000,
    'names': 2_000,
    'startup_runs': 20,
    'startup_batches': 2,
    'reps_light': 3,
    'reps_heavy': 2,
}


class BenchError(Exception):
    """A build, fixture, check or command failure; the suite exits 2."""


class Impl:
    """One measured command in a group."""

    def __init__(self, label, cmd, reps, batch=1, warmup_runs=1, reference=False):
        self.label = label
        self.cmd = cmd
        self.reps = reps
        self.batch = batch
        self.warmup_runs = warmup_runs
        self.reference = reference
        self.median = None


class Group:
    """A workload: several implementations timed over the same fixture.

    The ratio printed for each implementation is its median over the base
    implementation's median (the group's first implementation unless base is
    given, as for the sum(range) reference row)."""

    def __init__(self, title, impls, base=None):
        self.title = title
        self.impls = impls
        self.base = base


# ------------------------------------------------------------------- commands

def say(msg):
    print(msg, file=sys.stderr, flush=True)


def child_env():
    env = dict(os.environ)
    env['LC_ALL'] = 'C'
    env['TZ'] = 'UTC'
    return env


ENV = child_env()


class Deadline(Exception):
    """Raised by the SIGALRM handler around a timed command's wait."""


def _on_alarm(signum, frame):
    raise Deadline()


def wait_deadline(p, timeout):
    """p.wait() with a wall-clock deadline and no polling.

    subprocess.run(timeout=...) polls with short sleeps (about 1 ms of added
    overhead, which would swamp the 0.2 ms startup row); a SIGALRM timer
    interrupts a blocking waitpid instead. The caller kills the child on
    Deadline."""
    if timeout is None or not hasattr(signal, 'setitimer'):
        return p.wait()
    old = signal.signal(signal.SIGALRM, _on_alarm)
    signal.setitimer(signal.ITIMER_REAL, timeout)
    try:
        return p.wait()
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, old)


def run_quiet(cmd, what, timeout):
    """One timed run: no pipes, no polling. Returns the exit status."""
    try:
        p = subprocess.Popen(cmd, env=ENV, stdin=DEVNULL,
                             stdout=DEVNULL, stderr=DEVNULL)
    except FileNotFoundError:
        raise BenchError(f"{what}: command not found: {cmd[0]}")
    except PermissionError as e:
        raise BenchError(f"{what}: cannot execute {cmd[0]}: {e.strerror}")
    try:
        return wait_deadline(p, timeout)
    except Deadline:
        p.kill()
        p.wait()
        raise BenchError(f"{what}: timed out after {timeout} s: "
                         f"{shellish(cmd)}") from None


def shellish(cmd):
    return shlex.join(cmd)


def child(cmd, what, capture=False, timeout=600):
    """Run one command: stdout to /dev/null (or returned), stderr discarded on
    success. Any failure, including a timeout, is a BenchError naming the
    command; a failed timed run is run once more with pipes so its stderr can
    be reported."""
    if not capture:
        rc = run_quiet(cmd, what, timeout)
        if rc == 0:
            return None
        err = ''
        try:
            q = subprocess.run(cmd, env=ENV, stdin=DEVNULL, stdout=DEVNULL,
                               stderr=subprocess.PIPE, timeout=timeout)
            err = q.stderr.decode('utf-8', 'replace').strip()
        except (OSError, subprocess.TimeoutExpired):
            pass
        raise BenchError(f"{what}: exit {rc}: {shellish(cmd)}"
                         + (f"\n{err}" if err else ""))
    try:
        p = subprocess.run(cmd, env=ENV, stdin=DEVNULL, stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE, timeout=timeout)
    except FileNotFoundError:
        raise BenchError(f"{what}: command not found: {cmd[0]}")
    except PermissionError as e:
        raise BenchError(f"{what}: cannot execute {cmd[0]}: {e.strerror}")
    except subprocess.TimeoutExpired:
        raise BenchError(f"{what}: timed out after {timeout} s: {shellish(cmd)}")
    if p.returncode != 0:
        err = p.stderr.decode('utf-8', 'replace').strip()
        raise BenchError(f"{what}: exit {p.returncode}: {shellish(cmd)}"
                         + (f"\n{err}" if err else ""))
    return p.stdout


def preview(data, limit=160):
    text = data[:limit].decode('utf-8', 'replace')
    return repr(text) + (f'... ({len(data)} bytes)' if len(data) > limit else '')


def expect_equal(what, left, right):
    if left != right:
        raise BenchError(f"{what}: outputs differ\n  left : {preview(left)}"
                         f"\n  right: {preview(right)}")


def script(name):
    return os.path.join(HERE, name)


def rel(path):
    try:
        return os.path.relpath(path, ROOT)
    except ValueError:
        return path


# --------------------------------------------------------------------- build

def build_compiler():
    """make -C obo obo (the repo's own rule); returns (path, --version line)."""
    try:
        p = subprocess.run(['make', '-C', FORK, 'obo'], env=ENV,
                           capture_output=True, text=True)
    except OSError as e:
        raise BenchError(f"cannot run make: {e}")
    if p.returncode != 0:
        detail = (p.stderr or p.stdout).strip()
        raise BenchError("building the compiler failed: make -C obo obo"
                         + (f"\n{detail}" if detail else ""))
    exe = os.path.join(FORK, 'obo')
    if not os.path.isfile(exe) or not os.access(exe, os.X_OK):
        raise BenchError(f"make reported success but {rel(exe)} is not executable")
    version = child([exe, '--version'], 'obo --version', capture=True, timeout=60)
    return exe, version.decode('utf-8', 'replace').strip()


def build_tools(tmp, compiler):
    """Build wc/sort and eza with tools/build_tools.py into private directories."""
    built = {}
    for names, sub in ((('wc', 'sort'), 'tools'), (('eza',), 'eza')):
        out = os.path.join(tmp, 'build', sub)
        p = subprocess.run(
            [sys.executable, os.path.join(FORK, 'tools', 'build_tools.py'),
             *names, '--out', out, '--obo', compiler, '--rt', FORK],
            env=ENV, capture_output=True, text=True)
        if p.returncode != 0:
            detail = (p.stderr or p.stdout).strip()
            raise BenchError(f"building {' '.join(names)} failed"
                             + (f"\n{detail}" if detail else ""))
        for name in names:
            exe = os.path.join(out, name)
            if not os.path.isfile(exe):
                raise BenchError(f"the tool build produced no {rel(exe)}")
            built[name] = exe
    return built


def compile_programs(tmp, compiler):
    """Compile the .obx benchmark programs with the repo compiler."""
    bindir = os.path.join(tmp, 'bin')
    symdir = os.path.join(tmp, 'sym')
    os.makedirs(bindir)
    os.makedirs(symdir)
    exes = {}
    for name in PROGRAMS:
        src = script(name + '.obx')
        exe = os.path.join(bindir, name)
        p = subprocess.run([compiler, src, '-o', exe, '--rt', FORK,
                            '--sym-dir', symdir],
                           env=ENV, capture_output=True, text=True)
        if p.returncode != 0:
            detail = (p.stderr or p.stdout).strip()
            raise BenchError(f"compiling {rel(src)} failed"
                             + (f"\n{detail}" if detail else ""))
        if not os.path.isfile(exe):
            raise BenchError(f"the compiler reported success but produced no {rel(exe)}")
        exes[name] = exe
    return exes


# ------------------------------------------------------------------ fixtures

def make_text(path, lines):
    """The wc fixture: `lines` copies of a 55-byte sentence, no final surprise."""
    with open(path, 'wb') as f:
        buf = []
        for i in range(lines):
            buf.append(TEXT_LINE % i)
            if len(buf) >= 10000:
                f.write(b''.join(buf))
                buf.clear()
        if buf:
            f.write(b''.join(buf))
    want = len(TEXT_LINE % 0) * lines
    got = os.path.getsize(path)
    if got != want:
        raise BenchError(f"text fixture: wrote {got} bytes, expected {want}")
    return got


def make_ints(path, lines):
    """The sort fixture: deterministic 8-digit numbers, one per line."""
    rng = random.Random(20261029)
    with open(path, 'w', encoding='ascii', newline='') as f:
        for start in range(0, lines, 10000):
            n = min(10000, lines - start)
            f.write(''.join('%08d\n' % rng.randrange(100_000_000) for _ in range(n)))
    return os.path.getsize(path)


def make_names(path, count):
    """The eza fixture: `count` plain, fixed-width, zero-padded names."""
    os.makedirs(path)
    for i in range(count):
        with open(os.path.join(path, 'entry-%05d.dat' % i), 'wb'):
            pass
    return count


def make_fixtures(tmp, sizes):
    fx = {
        'text': os.path.join(tmp, 'text.txt'),
        'ints': os.path.join(tmp, 'ints.txt'),
        'names': os.path.join(tmp, 'names'),
    }
    fx['text_bytes'] = make_text(fx['text'], sizes['text_lines'])
    fx['ints_bytes'] = make_ints(fx['ints'], sizes['sort_lines'])
    make_names(fx['names'], sizes['names'])
    return fx


# ----------------------------------------------------------------- resources

def pin_self():
    """Pin this process (and so every child) to one CPU when the OS can.

    Doing it here rather than wrapping each command in taskset keeps the
    wrapper's own exec time out of the startup row. Returns the CPU number or
    None."""
    try:
        cpus = sorted(os.sched_getaffinity(0))
    except (AttributeError, OSError):
        return None
    if not cpus:
        return None
    cpu = cpus[-1]
    try:
        os.sched_setaffinity(0, {cpu})
        if os.sched_getaffinity(0) != {cpu}:
            return None
    except OSError:
        return None
    return cpu


def gnu_tool(name, skipped):
    path = shutil.which(name)
    if not path:
        skipped.append(f"GNU {name}: not found on PATH; its reference row is skipped")
        return None
    try:
        out = child([path, '--version'], f"{name} --version", capture=True, timeout=30)
    except BenchError:
        skipped.append(f"GNU {name}: {path} does not report --version; "
                       "its reference row is skipped")
        return None
    if b'GNU coreutils' not in out:
        skipped.append(f"GNU {name}: {path} is not GNU coreutils; "
                       "its reference row is skipped")
        return None
    return path


def pinned_eza(explicit, skipped):
    """The eza binary if it matches tests/eza/VERSION; else None with a note.

    The reference is optional: PATH is searched unless --eza names a binary,
    and a missing or different binary skips only the reference row."""
    path = explicit or shutil.which('eza')
    if not path:
        skipped.append("pinned eza: no eza on PATH (pass --eza PATH); "
                       "its reference row is skipped")
        return None
    path = os.path.abspath(path)
    if not os.path.isfile(path) or not os.access(path, os.X_OK):
        skipped.append(f"pinned eza: {path} is not executable; "
                       "its reference row is skipped")
        return None
    try:
        p = subprocess.run([path, '--version'], env=ENV, stdin=DEVNULL,
                           capture_output=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as e:
        skipped.append(f"pinned eza: {path} --version failed ({e}); "
                       "its reference row is skipped")
        return None
    if p.returncode != 0:
        skipped.append(f"pinned eza: {path} --version exited {p.returncode}; "
                       "its reference row is skipped")
        return None
    live = p.stdout.decode('utf-8', 'replace').strip().splitlines()
    pin_path = os.path.join(FORK, 'tests', 'eza', 'VERSION')
    try:
        with open(pin_path, encoding='utf-8') as f:
            pin = f.read().splitlines()
    except OSError:
        skipped.append(f"pinned eza: cannot read {rel(pin_path)}; "
                       "its reference row is skipped")
        return None
    want = [line.strip() for line in pin[:3]]
    if [line.strip() for line in live[:3]] != want:
        live_version = (live[1] if len(live) > 1 else (live[0] if live else 'unknown')).strip()
        skipped.append(f"pinned eza: {path} reports {live_version!r}, "
                       f"pinned {want[1] if len(want) > 1 else 'unknown'!r}; "
                       "its reference row is skipped")
        return None
    digest = None
    for line in pin[3:]:
        if line.startswith('binary-sha256:'):
            digest = line.split(':', 1)[1].strip()
    if digest:
        try:
            with open(path, 'rb') as f:
                got = hashlib.sha256(f.read()).hexdigest()
        except OSError as e:
            skipped.append(f"pinned eza: cannot hash {path}: {e.strerror}; "
                           "its reference row is skipped")
            return None
        if got != digest:
            skipped.append(f"pinned eza: {path} sha256 does not match VERSION; "
                           "its reference row is skipped")
            return None
    version = want[1].split()[0] if len(want) > 1 and want[1].split() else 'pinned'
    return path, version


# ---------------------------------------------------------------- benchmarks

def wc_counts(out, count, what):
    fields = out.split()
    if len(fields) < count:
        raise BenchError(f"{what}: expected {count} counts, got {preview(out)}")
    try:
        return tuple(int(field) for field in fields[:count])
    except ValueError:
        raise BenchError(f"{what}: non-numeric wc output: {preview(out)}")


def build_groups(sizes, exes, tools, fixtures, py, gnu, eza):
    """Check each pair's output, then return the groups to measure."""
    light = sizes['reps_light']
    heavy = sizes['reps_heavy']
    groups = []

    # process startup: one short process, many times, per-process time
    hello_out = child([exes['hello']], 'obo hello', capture=True)
    print_out = child([py, '-c', 'print("hi")'], 'python3 print', capture=True)
    pass_out = child([py, '-c', 'pass'], 'python3 pass', capture=True)
    expect_equal('startup: obo hello vs python print', hello_out, b'hi\n')
    expect_equal('startup: python3 -c print("hi") vs obo hello', print_out, hello_out)
    expect_equal('startup: python3 -c pass vs empty', pass_out, b'')
    startup = sizes['startup_runs']
    groups.append(Group(
        f'process startup (hello; {startup} runs per repetition)', [
            Impl('obo hello (static binary)', [exes['hello']],
                 sizes['startup_batches'], batch=startup, warmup_runs=5),
            Impl('python3 -c pass', [py, '-c', 'pass'],
                 sizes['startup_batches'], batch=startup, warmup_runs=5),
            Impl('python3 -c print("hi")', [py, '-c', 'print("hi")'],
                 sizes['startup_batches'], batch=startup, warmup_runs=5),
        ]))

    # fib
    n = sizes['fib_n']
    obo_fib = child([exes['fib'], str(n)], 'obo fib', capture=True)
    py_fib = child([py, script('fib.py'), str(n)], 'python3 fib', capture=True)
    expect_equal('fib: obo vs python', obo_fib, py_fib)
    groups.append(Group(f'fib({n}) recursive', [
        Impl('obo fib', [exes['fib'], str(n)], light),
        Impl('python3 fib.py', [py, script('fib.py'), str(n)], light),
    ]))

    # explicit loop sum, then Python's C-level sum(range) as a reference
    n = sizes['loop_n']
    loop_cmd = [exes['loopsum'], str(n)]
    py_loop_cmd = [py, script('loopsum.py'), str(n)]
    obo_loop = child(loop_cmd, 'obo loopsum', capture=True)
    py_loop = child(py_loop_cmd, 'python3 loopsum', capture=True)
    expect_equal('sum loop: obo vs python', obo_loop, py_loop)
    loop_group = Group(f'sum 0..{n:,} explicit loop', [
        Impl('obo loopsum', loop_cmd, heavy),
        Impl('python3 loopsum.py', py_loop_cmd, heavy),
    ])
    groups.append(loop_group)
    sumrange_cmd = [py, script('sumrange.py'), str(n)]
    py_range = child(sumrange_cmd, 'python3 sum(range)', capture=True)
    expect_equal('sum(range): matches the explicit loop', py_range, obo_loop)
    groups.append(Group(f'sum(range({n:,})) (reference; a different algorithm)', [
        Impl('python3 sum(range)', sumrange_cmd, heavy, reference=True),
    ], base=loop_group.impls[0]))

    # sieve
    n = sizes['sieve_n']
    obo_sieve = child([exes['sieve'], str(n)], 'obo sieve', capture=True)
    py_sieve = child([py, script('sieve.py'), str(n)], 'python3 sieve', capture=True)
    expect_equal('sieve: obo vs python', obo_sieve, py_sieve)
    groups.append(Group(f'sieve of Eratosthenes <= {n:,}', [
        Impl('obo sieve', [exes['sieve'], str(n)], heavy),
        Impl('python3 sieve.py', [py, script('sieve.py'), str(n)], heavy),
    ]))

    # wc -l
    text = fixtures['text']
    obo_l = child([tools['wc'], '-l', text], 'obo wc -l', capture=True)
    py_l = child([py, script('wc_lines.py'), text], 'python3 wc_lines', capture=True)
    want_l = wc_counts(obo_l, 1, 'obo wc -l')
    expect_equal('wc -l: obo vs python', wc_counts(py_l, 1, 'python3 wc_lines'), want_l)
    wc_l_impls = [
        Impl('obo wc -l', [tools['wc'], '-l', text], heavy),
        Impl('python3 wc_lines.py', [py, script('wc_lines.py'), text], heavy),
    ]
    if gnu.get('wc'):
        gnu_l = child([gnu['wc'], '-l', text], 'GNU wc -l', capture=True)
        expect_equal('wc -l: obo vs GNU', wc_counts(gnu_l, 1, 'GNU wc -l'), want_l)
        wc_l_impls.append(Impl('GNU wc -l (ref)', [gnu['wc'], '-l', text], heavy,
                               reference=True))
    groups.append(Group(f'wc -l on {fixtures["text_bytes"]:,} B', wc_l_impls))

    # wc -lwc
    obo_lwc = child([tools['wc'], '-lwc', text], 'obo wc -lwc', capture=True)
    py_lwc = child([py, script('wc_full.py'), text], 'python3 wc_full', capture=True)
    want_lwc = wc_counts(obo_lwc, 3, 'obo wc -lwc')
    expect_equal('wc -lwc: obo vs python',
                 wc_counts(py_lwc, 3, 'python3 wc_full'), want_lwc)
    wc_lwc_impls = [
        Impl('obo wc -lwc', [tools['wc'], '-lwc', text], heavy),
        Impl('python3 wc_full.py', [py, script('wc_full.py'), text], heavy),
    ]
    if gnu.get('wc'):
        gnu_lwc = child([gnu['wc'], '-lwc', text], 'GNU wc -lwc', capture=True)
        expect_equal('wc -lwc: obo vs GNU',
                     wc_counts(gnu_lwc, 3, 'GNU wc -lwc'), want_lwc)
        wc_lwc_impls.append(Impl('GNU wc -lwc (ref)',
                                 [gnu['wc'], '-lwc', text], heavy, reference=True))
    groups.append(Group(f'wc -lwc on {fixtures["text_bytes"]:,} B', wc_lwc_impls))

    # sort
    ints = fixtures['ints']
    obo_sort = child([tools['sort'], ints], 'obo sort', capture=True)
    py_sort = child([py, script('sort.py'), ints], 'python3 sort', capture=True)
    expect_equal('sort: obo vs python', obo_sort, py_sort)
    sort_impls = [
        Impl('obo sort', [tools['sort'], ints], heavy),
        Impl('python3 sort.py', [py, script('sort.py'), ints], heavy),
    ]
    if gnu.get('sort'):
        gnu_sort = child([gnu['sort'], ints], 'GNU sort', capture=True)
        expect_equal('sort: obo vs GNU', obo_sort, gnu_sort)
        sort_impls.append(Impl('GNU sort (ref)', [gnu['sort'], ints], heavy,
                               reference=True))
    groups.append(Group(f'sort {sizes["sort_lines"]:,} lines', sort_impls))

    # eza name-only listing
    names = fixtures['names']
    obo_eza = child([tools['eza'], names], 'obo eza', capture=True)
    py_list = child([py, script('listdir.py'), names], 'python3 listdir', capture=True)
    expect_equal('eza: obo vs python', obo_eza, py_list)
    eza_impls = [
        Impl('obo eza', [tools['eza'], names], light),
        Impl('python3 listdir + sort', [py, script('listdir.py'), names], light),
    ]
    if gnu.get('ls'):
        gnu_ls = child([gnu['ls'], '--color=never', '-1', names],
                       'GNU ls -1', capture=True)
        expect_equal('eza: obo vs GNU ls', obo_eza, gnu_ls)
        eza_impls.append(Impl('GNU ls -1 (ref)',
                              [gnu['ls'], '--color=never', '-1', names], light,
                              reference=True))
    if eza:
        ref, version = eza
        ref_out = child([ref, '--color=never', names], 'pinned eza', capture=True)
        expect_equal('eza: obo vs pinned eza', obo_eza, ref_out)
        eza_impls.append(Impl(f'pinned eza {version} (ref)',
                              [ref, '--color=never', names], light, reference=True))
    groups.append(Group(f'eza {sizes["names"]:,} names', eza_impls))

    return groups


def measure(impl, what):
    for _ in range(impl.warmup_runs):
        child(impl.cmd, what)
    samples = []
    for _ in range(impl.reps):
        t0 = time.perf_counter()
        for _ in range(impl.batch):
            child(impl.cmd, what)
        samples.append((time.perf_counter() - t0) / impl.batch)
    impl.median = statistics.median(samples)


# ------------------------------------------------------------------- reporting

def fmt_ms(seconds):
    ms = seconds * 1000.0
    if ms < 10:
        return f'{ms:.2f} ms'
    if ms < 1000:
        return f'{ms:.1f} ms'
    return f'{ms:,.0f} ms'


def git_info():
    def git(*args):
        try:
            p = subprocess.run(['git', '-C', ROOT, *args], capture_output=True,
                               text=True)
        except OSError:
            return None
        return p.stdout.strip() if p.returncode == 0 else None
    return git('rev-parse', '--short', 'HEAD'), git('branch', '--show-current')


def cpu_model():
    try:
        with open('/proc/cpuinfo', encoding='utf-8', errors='replace') as f:
            for line in f:
                if line.startswith('model name'):
                    return line.split(':', 1)[1].strip()
    except OSError:
        pass
    return platform.processor() or platform.machine() or 'unknown'


def make_header(args, sizes, compiler_version, py, py_version, cpu, fixtures):
    commit, branch = git_info()
    return {
        'repo': ROOT,
        'git_branch': branch or 'unknown',
        'git_commit': commit or 'unknown',
        'compiler': compiler_version,
        'python': py_version,
        'python_path': py,
        'cpu': cpu_model(),
        'kernel': platform.platform(),
        'pinned_cpu': cpu,
        'mode': 'quick' if args.quick else 'default',
        'reps_light': sizes['reps_light'],
        'reps_heavy': sizes['reps_heavy'],
        'startup_runs': sizes['startup_runs'],
        'startup_batches': sizes['startup_batches'],
        'environment': 'LC_ALL=C TZ=UTC; stdout to /dev/null; stderr checked',
        'fixtures': {
            'text_bytes': fixtures['text_bytes'],
            'text_lines': sizes['text_lines'],
            'sort_lines': sizes['sort_lines'],
            'sort_bytes': fixtures['ints_bytes'],
            'eza_names': sizes['names'],
        },
    }


def binary_sizes(exes, tools):
    order = [('hello', exes['hello']), ('fib', exes['fib']),
             ('loopsum', exes['loopsum']), ('sieve', exes['sieve']),
             ('wc (tool)', tools['wc']), ('sort (tool)', tools['sort']),
             ('eza (tool)', tools['eza'])]
    return {label: os.path.getsize(path) for label, path in order}


def print_table(header, groups, sizes, skipped, elapsed):
    print('obo CLI benchmark suite (on-demand; NOT a gate, not run by CI)')
    print(f"repo:      {header['repo']}")
    print(f"git:       {header['git_branch']} @ {header['git_commit']}")
    print(f"compiler:  {header['compiler']}")
    print(f"python:    {header['python']}  ({header['python_path']})")
    print(f"cpu:       {header['cpu']}")
    print(f"kernel:    {header['kernel']}")
    if header['pinned_cpu'] is not None:
        print(f"pin:       CPU {header['pinned_cpu']} (runner and children, single core)")
    else:
        print('pin:       none (sched_setaffinity unavailable)')
    print(f"mode:      {header['mode']}, {header['reps_light']} reps light / "
          f"{header['reps_heavy']} heavy, startup "
          f"{header['startup_batches']}x{header['startup_runs']} runs")
    print(f"fixtures:  text {header['fixtures']['text_bytes']:,} B, "
          f"sort {header['fixtures']['sort_lines']:,} lines, "
          f"eza {header['fixtures']['eza_names']:,} names")
    print(f"env:       {header['environment']}")
    print()

    width = max(len(impl.label) for group in groups for impl in group.impls)
    print(f"{'implementation':<{width}}  {'median':>12}  {'ratio':>8}")
    for group in groups:
        print()
        print(group.title)
        base = group.base or group.impls[0]
        for impl in group.impls:
            ratio = impl.median / base.median if base.median else float('nan')
            print(f"  {impl.label:<{width}}  {fmt_ms(impl.median):>12}  {ratio:>7.2f}x")

    print()
    print('binary sizes:')
    for label, size in sizes.items():
        print(f'  {label:<16} {size:>10,} B')

    if skipped:
        print()
        print('notes:')
        for note in skipped:
            print(f'  - {note}')
    print()
    print(f'total: {elapsed:.1f} s on this host; the numbers are host-dependent.')


def json_doc(header, groups, sizes, skipped, elapsed):
    rows = []
    for group in groups:
        base = group.base or group.impls[0]
        for impl in group.impls:
            rows.append({
                'workload': group.title,
                'implementation': impl.label,
                'median_s': round(impl.median, 6),
                'ratio': round(impl.median / base.median, 4) if base.median else None,
                'reference': impl.reference,
            })
    return {
        'suite': 'obo/bench/cli',
        'gate': False,
        'header': header,
        'benchmarks': rows,
        'binary_sizes': sizes,
        'skipped_references': skipped,
        'total_s': round(elapsed, 3),
    }


# ---------------------------------------------------------------------- main

def run(args):
    sizes = dict(QUICK if args.quick else DEFAULT)
    if args.reps is not None:
        sizes['reps_light'] = args.reps
        sizes['reps_heavy'] = args.reps
        sizes['startup_batches'] = args.reps

    py = shutil.which('python3') or sys.executable
    py_version = child([py, '--version'], 'python3 --version',
                       capture=True, timeout=60).decode('utf-8', 'replace').strip()

    cpu = pin_self()
    tmp = tempfile.mkdtemp(prefix='obo-bench-cli-')
    t0 = time.perf_counter()
    try:
        say('building the compiler (make -C obo obo)')
        compiler, compiler_version = build_compiler()
        say('building wc, sort and eza')
        tools = build_tools(tmp, compiler)
        say('compiling the benchmark programs')
        exes = compile_programs(tmp, compiler)
        say('generating fixtures')
        fixtures = make_fixtures(tmp, sizes)

        skipped = []
        gnu = {name: gnu_tool(name, skipped) for name in ('wc', 'sort', 'ls')}
        eza = pinned_eza(args.eza, skipped)

        say('checking that obo, Python and reference outputs agree')
        groups = build_groups(sizes, exes, tools, fixtures, py, gnu, eza)

        say(f'measuring {sum(len(g.impls) for g in groups)} implementations '
            f'({"quick" if args.quick else "default"})')
        for gi, group in enumerate(groups, 1):
            for impl in group.impls:
                measure(impl, impl.label)
                say(f'  [{gi}/{len(groups)}] {group.title}: {impl.label} '
                    f'... {fmt_ms(impl.median)}')

        elapsed = time.perf_counter() - t0
        header = make_header(args, sizes, compiler_version, py, py_version,
                             cpu, fixtures)
        sizes_map = binary_sizes(exes, tools)
        if args.json:
            print(json.dumps(json_doc(header, groups, sizes_map, skipped, elapsed),
                             indent=2))
        else:
            print_table(header, groups, sizes_map, skipped, elapsed)
    finally:
        if args.keep:
            say(f'kept the working directory: {tmp}')
        else:
            shutil.rmtree(tmp, ignore_errors=True)
    return 0


def parse_args(argv):
    ap = argparse.ArgumentParser(
        description='On-demand obo CLI benchmarks against Python 3, with GNU '
                    'tools and the pinned eza as reference points. '
                    'Not a gate; not run by CI.')
    ap.add_argument('--quick', action='store_true',
                    help='smaller fixtures and fewer repetitions (about 10 s here)')
    ap.add_argument('--reps', type=int, metavar='N',
                    help='timed repetitions per row; also the startup batch count '
                         '(default: 5 light / 3 heavy; quick: 3 / 2)')
    ap.add_argument('--keep', action='store_true',
                    help='keep the private fixture and build directory')
    ap.add_argument('--json', action='store_true',
                    help='print a JSON document instead of the table')
    ap.add_argument('--eza', metavar='PATH',
                    help='eza binary for the reference row; it must still match '
                         'tests/eza/VERSION, else the row is skipped (default: '
                         'eza from PATH)')
    a = ap.parse_args(argv)
    if a.reps is not None and a.reps < 1:
        ap.error('--reps must be at least 1')
    return a


def main(argv=None):
    args = parse_args(argv)
    try:
        return run(args)
    except BenchError as e:
        print(f'bench: {e}', file=sys.stderr)
        print('bench: rerun with --keep to inspect the build and fixtures',
              file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130


if __name__ == '__main__':
    sys.exit(main())
