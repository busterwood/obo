#!/usr/bin/env python3
"""Stage B of the port: the ported compiler (obo/src/obo.obo, built by obo)
must compile every program and error case as obo does, and its executables must
behave as obo's do: the same compile outcome (success, or the same error message at
the same line:col), and for a program that compiles, the same stdout, stderr and exit
code when run with OBO_CHECK=1. The executables' bytes may differ, so code generation
can change; the fixed point (make promote) still compares the compiler's own bytes.
The compiler itself must have no leaks and stay within its memory budget.

A program that imports library modules is compiled as a module root with
-I obo/lib and a private --sym-dir per compiler. The seed comparison compiles
those roots too; only the separate module-tree cases stay out of its flat
Stage B count.

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
sys.path.insert(0, HERE)
import run  # noqa: E402  (for error_cases)
import mutate_stats  # noqa: E402  (optional timings and counters)

REF = os.path.abspath(os.environ.get('OBO_REF', os.path.join(FORK, 'obo')))
SRC = os.path.join(FORK, 'src', 'obo.obo')
LIBDIR = os.path.join(FORK, 'lib')
# live peak, in bytes, of the port compiling itself. Lower it when memory improves; raise it only
# for a reason recorded in FORK.md. The P6a tree measures 1,646,816 bytes live and 35,353,648 bytes
# total allocation on main after the 0.5 promote (FORK.md, "Terminal-size metadata: slot 39"); the
# budget stays 2,000,000 (353,184 spare above that peak). The figure is for the current tree and
# varies by a few hundred bytes with the source path. Leaks are caught separately, by port_test's
# leak check.
PEAK_BUDGET = 2_000_000


# REF's outcome for each case text, with the seconds its compile and run took: a mutant's
# comparisons (mutate_port.py) reuse the outcomes of the comparison before them
REF_OUTCOMES = {}

# quick mode: the port's compiles and runs may map at most this much address space, so a runaway mutant fails fast.
# The runtime reserves about 4.5 GiB up front (a 4 GiB heap, a 32 MiB starts table, a 512 MiB mstack), so a
# 4 GiB cap would stop the runtime itself; the cap is 5 GiB, which leaves room for the process.
ADDRESS_SPACE = 5 << 30


class HarnessError(RuntimeError):
    """A bug or setup failure in the harness, not an outcome of the mutant. It is
    raised, never turned into a caught mutant: a broken helper must fail the gate
    or abort the baseline, otherwise every mutant could look caught."""


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


def outcome(exe, src_path, out_path, scratch, compile_limit=120, run_limit=60, capped_run=False,
            module_args=(), stats=None):
    """('ok', (exit code, stdout, stderr)) for a program that compiles, run with
    OBO_CHECK=1 and a fresh scratch directory as its argument (as run.py does);
    ('error', 'line:col: message') or ('crash', ...) for one that does not. The case's
    directory is written <tmp> in the result, so outcomes from different directories
    compare. Also returns the seconds the compile and the run took. capped_run puts the
    compile and the run under the address-space cap (the port's own, in quick mode).
    stats, when given, counts the subprocesses (see mutate_stats.Stats)."""
    tmp = os.path.dirname(src_path)
    t0 = time.monotonic()
    try:
        if stats is not None:
            stats.add('subprocesses')
        p = subprocess.run([exe, src_path, '-o', out_path, '--rt', FORK, *module_args],
                           capture_output=True, text=True,
                           env=dict(os.environ, OBO_CHECK='1'), timeout=compile_limit,
                           preexec_fn=capped() if capped_run else None)
    except subprocess.TimeoutExpired:
        return ('crash', f"no result in {compile_limit:.0f} s"), compile_limit, 0
    t1 = time.monotonic()
    if stats is not None:
        stats.add('compiles')
    if p.returncode == 1 and p.stderr.startswith(src_path + ':'):
        return ('error', p.stderr[len(src_path) + 1:].strip().replace(tmp, '<tmp>')), t1 - t0, 0
    if p.returncode != 0:
        return ('crash', f"exit {p.returncode}: {p.stderr.strip().replace(tmp, '<tmp>')[:200]}"), t1 - t0, 0
    shutil.rmtree(scratch, ignore_errors=True)
    os.mkdir(scratch)                    # the same path for both runs, so output naming it agrees
    try:
        if stats is not None:
            stats.add('subprocesses')
        r = subprocess.run([out_path, scratch], capture_output=True, timeout=run_limit,
                           preexec_fn=capped(run._limits) if capped_run else run._limits,
                           env=dict(os.environ, OBO_CHECK='1'))
    except subprocess.TimeoutExpired:
        return ('ok', ('timeout', b'', b'')), t1 - t0, run_limit
    t2 = time.monotonic()
    if stats is not None:
        stats.add('runs')
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


def cases_of(src, seed=False):
    """(name, text) of every case: the programs, src itself, and the error cases.
    A program that imports modules is compiled as a module root (see the module
    arguments in compare); there is no source injection.

    In the seed comparison (seed=True) a case is skipped while the seed predates
    the builtins it uses: a 0.2 seed rejects tests/run/meta.obx and
    tests/errors/meta.obx (statPath and friends, 0.3), a 0.3 seed rejects the
    P5a cases (isatty and getenv, 0.4) and any case importing a library module
    that uses them (io.obx's isTty), and a 0.4 seed rejects the P6a cases
    (terminalWidth, 0.5) and every case importing io.obx (io::termWidth). A
    promoted 0.5 seed compares them. The default comparison (obo and the port
    from the same source) always covers every file. This is the only seed-mode
    exclusion besides the module trees."""
    cases = []
    files = sorted(glob.glob(os.path.join(FORK, 'tests', 'run', '*.obx')) + glob.glob(os.path.join(FORK, 'examples', '*.obx')) +
                   glob.glob(os.path.join(FORK, 'bench', '*.obx')) + glob.glob(os.path.join(ROOT, 'tests', 'run', '*.obx')) +
                   glob.glob(os.path.join(ROOT, 'examples', '*.obx')) + [src])
    for f in files:
        text = open(f, 'rb').read()
        if seed and seed_skips(f, text):
            continue
        cases.append((os.path.relpath(f, ROOT), text))
    for f in sorted(glob.glob(os.path.join(FORK, 'tests', 'errors', '*.obx')) + glob.glob(os.path.join(ROOT, 'tests', 'errors', '*.obx'))):
        for name, text, _, _ in run.error_cases(f):
            if seed and seed_skips(f, text):
                continue
            cases.append((name, text.encode()))
    return cases



# ---------------------------------------------------------------- module trees
#
# Stage 1/2 modules: compile the same tree with both compilers (same -I and case
# directory, a private --sym-dir for each), compare the compile outcome, the run,
# every emitted .sym byte-for-byte, and --check of every module source that has a
# header. `symcheck` cases are check-only: a root source plus committed symdir
# fixtures, which exercise the reader's structural and staleness validation and
# the check-mode writer. These tree cases are compared in the default run; the
# seed comparison's Stage B count has always excluded them.

MODDIR = os.path.join(FORK, 'tests', 'modules')
SEED_REF = os.path.realpath(os.path.join(FORK, 'seed', 'obo'))
REF_TREE = {}

# The compiler-feature cases use builtins that arrived after the seed was
# promoted, so the seed cannot compile them. Each file is skipped in the seed
# comparison until the seed's `--version` reaches the version that introduced
# its builtins; a promoted seed at that version compares it (P3 metadata after
# 0.3, P5a isatty/getenv after 0.4, P6a terminalWidth after 0.5). A case that
# imports a library module using a newer builtin is skipped the same way (P5a
# put isatty in io.obx, so io_reader/io_writer needed a 0.4 seed; P6a adds
# io::termWidth, so every io.obx importer now needs a 0.5 seed). The decision
# reads `REF --version`, cached per executable, so it follows the seed
# automatically across a promote.
SEED_FILE_MIN = {
    os.path.join(FORK, 'tests', 'run', 'meta.obx'): (0, 3),
    os.path.join(FORK, 'tests', 'errors', 'meta.obx'): (0, 3),
    os.path.join(FORK, 'tests', 'run', 'env.obx'): (0, 4),
    os.path.join(FORK, 'tests', 'errors', 'env.obx'): (0, 4),
    os.path.join(FORK, 'tests', 'run', 'term.obx'): (0, 5),
    os.path.join(FORK, 'tests', 'errors', 'term.obx'): (0, 5),
    # sleepMs is an additive 0.8.1 builtin; the promoted 0.8 seed cannot parse it.
    os.path.join(FORK, 'tests', 'run', 'sleep.obx'): (0, 9),
    os.path.join(FORK, 'tests', 'errors', 'sleep.obx'): (0, 9),
    os.path.join(FORK, 'tests', 'run', 'methods.obx'): (0, 6),
    os.path.join(FORK, 'tests', 'run', 'methods_refs.obx'): (0, 6),
    os.path.join(FORK, 'tests', 'run', 'methods_fields.obx'): (0, 6),
    os.path.join(FORK, 'tests', 'run', 'methods_closed.obx'): (0, 6),
    os.path.join(FORK, 'tests', 'run', 'methods_move.obx'): (0, 6),
    os.path.join(FORK, 'tests', 'run', 'methods_mut.obx'): (0, 6),
    os.path.join(FORK, 'tests', 'run', 'methods_handle.obx'): (0, 6),
    os.path.join(FORK, 'tests', 'run', 'methods_defer_try.obx'): (0, 6),
    os.path.join(FORK, 'tests', 'run', 'methods_recursion.obx'): (0, 6),
    os.path.join(FORK, 'tests', 'run', 'methods_homes.obx'): (0, 6),
    os.path.join(FORK, 'tests', 'run', 'methods_static.obx'): (0, 6),
    os.path.join(FORK, 'tests', 'run', 'methods_names.obx'): (0, 6),
    os.path.join(FORK, 'tests', 'run', 'methods_origins.obx'): (0, 6),
    os.path.join(FORK, 'tests', 'errors', 'methods.obx'): (0, 6),
    os.path.join(FORK, 'tests', 'run', 'ifaces_decl.obx'): (0, 8),
    os.path.join(FORK, 'tests', 'run', 'ifaces_owned.obx'): (0, 8),
    os.path.join(FORK, 'tests', 'run', 'ifaces_calls.obx'): (0, 8),
    os.path.join(FORK, 'tests', 'run', 'ifaces_effects.obx'): (0, 8),
    os.path.join(FORK, 'tests', 'run', 'ifaces_borrowed.obx'): (0, 8),
    os.path.join(FORK, 'tests', 'run', 'ifaces_b1_loop.obx'): (0, 8),
    os.path.join(FORK, 'tests', 'run', 'ifaces_refs.obx'): (0, 8),
    os.path.join(FORK, 'tests', 'run', 'ifaces_homes.obx'): (0, 8),
    os.path.join(FORK, 'tests', 'errors', 'ifaces_decl.obx'): (0, 8),
    os.path.join(FORK, 'tests', 'errors', 'ifaces_tiers.obx'): (0, 8),
    os.path.join(FORK, 'tests', 'errors', 'ifaces_impl_mismatch.obx'): (0, 8),
    os.path.join(FORK, 'tests', 'errors', 'ifaces_owned.obx'): (0, 8),
    os.path.join(FORK, 'tests', 'errors', 'ifaces_calls.obx'): (0, 8),
    os.path.join(FORK, 'tests', 'errors', 'ifaces_pos.obx'): (0, 8),
    os.path.join(FORK, 'tests', 'errors', 'ifaces_refs.obx'): (0, 8),
    os.path.join(FORK, 'tests', 'run', 'lambdas.obx'): (0, 9),
    os.path.join(FORK, 'tests', 'run', 'lambdas_higher.obx'): (0, 9),
    os.path.join(FORK, 'tests', 'run', 'lambdas_effects.obx'): (0, 9),
    os.path.join(FORK, 'tests', 'run', 'lambdas_sort.obx'): (0, 9),
    os.path.join(FORK, 'tests', 'run', 'lambdas_homes.obx'): (0, 9),
    os.path.join(FORK, 'tests', 'run', 'lambdas_scope.obx'): (0, 9),
    os.path.join(FORK, 'tests', 'run', 'lambdas_env.obx'): (0, 9),
    os.path.join(FORK, 'tests', 'run', 'lambdas_env_type.obx'): (0, 9),
    os.path.join(FORK, 'tests', 'run', 'lambdas_lifetime.obx'): (0, 9),
    os.path.join(FORK, 'tests', 'run', 'lambdas_types.obx'): (0, 9),
    os.path.join(FORK, 'tests', 'errors', 'lambdas.obx'): (0, 9),
    os.path.join(FORK, 'tests', 'errors', 'lambdas_step1.obx'): (0, 9),
}
SEED_LIB_MIN = {'io.obx': (0, 5), 'env.obx': (0, 4)}
_SEED_VERSIONS = {}


def ref_version(ref):
    """(major, minor) from `REF --version`, or None if it cannot be read."""
    key = os.path.realpath(ref)
    if key in _SEED_VERSIONS:
        return _SEED_VERSIONS[key]
    v = None
    try:
        p = subprocess.run([ref, '--version'], capture_output=True, text=True, timeout=60)
        if p.returncode == 0:
            m = re.match(r'obo\s+(\d+)\.(\d+)', p.stdout.strip())
            if m:
                v = (int(m.group(1)), int(m.group(2)))
    except (OSError, subprocess.TimeoutExpired):
        v = None
    _SEED_VERSIONS[key] = v
    return v


def import_min(text):
    """The newest version a case's library imports need, or None."""
    if isinstance(text, bytes):
        text = text.decode('latin-1')
    need = None
    for line in text.splitlines():
        s = line.strip()
        if not s.startswith('import "'):
            continue
        v = SEED_LIB_MIN.get(os.path.basename(s.split('"', 2)[1]))
        if v is not None and (need is None or v > need):
            need = v
    return need


def seed_skips(path, text):
    """True while the seed predates the builtins the case file uses. A file's
    own version and its library imports are both required: an explicit entry
    does not hide a newer library (env.obx is 0.4 itself but imports io.obx for
    io::isTty; it is io.obx's own terminalWidth definition that the 0.4 seed
    cannot parse)."""
    need = SEED_FILE_MIN.get(path)
    imp = import_min(text)
    if imp is not None and (need is None or imp > need):
        need = imp
    if need is None:
        return False
    v = ref_version(REF)
    return v is not None and v < need


def tree_cases():
    out = []
    for kind in ('run', 'errors', 'deps', 'sym'):
        base = os.path.join(MODDIR, kind)
        if not os.path.isdir(base):
            continue
        for name in sorted(os.listdir(base)):
            d = os.path.join(base, name)
            if not os.path.isdir(d):
                continue
            # only directories with a root source are tree cases; `golden` is a fixture
            if kind == 'run' or kind == 'errors' or kind == 'deps':
                if os.path.exists(os.path.join(d, 'main.obx')):
                    out.append((f"modules/{kind}/{name}", kind, d))
            elif kind == 'sym':
                if os.path.exists(os.path.join(d, 'main.obx')):
                    out.append((f"modules/sym/{name}", 'symcheck', d))
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


def fresh_dir(path):
    shutil.rmtree(path, ignore_errors=True)
    os.makedirs(path)
    return path


def tree_args(exe, case_dir, out_path, symdir):
    args = [exe, os.path.join(case_dir, 'main.obx')]
    if out_path is not None:
        args += ['-o', out_path]
    args += ['--rt', FORK, '-I', case_dir, '--sym-dir', symdir]
    for d in tree_includes(case_dir):
        args += ['-I', d]
    return args


def tree_norm(text, case_dir):
    for p in (case_dir + os.sep, case_dir + '/'):
        text = text.replace(p, '')
    return text.strip()


def sym_map(d):
    out = {}
    if os.path.isdir(d):
        for f in sorted(os.listdir(d)):
            if f.endswith('.sym'):
                with open(os.path.join(d, f), 'rb') as fh:
                    out[f] = fh.read()
    return out


def tree_outcome(exe, kind, case_dir, out_path, scratch, symdir, compile_limit=120, run_limit=60,
                 capped_run=False, stats=None):
    if kind == 'deps':
        args = [exe, '--deps', os.path.join(case_dir, 'main.obx'), '-I', case_dir]
        for d in tree_includes(case_dir):
            args += ['-I', d]
        t0 = time.monotonic()
        try:
            if stats is not None:
                stats.add('subprocesses')
            p = subprocess.run(args, capture_output=True, text=True, timeout=compile_limit,
                               preexec_fn=capped() if capped_run else None)
        except subprocess.TimeoutExpired:
            return ('crash', f"no result in {compile_limit:.0f} s"), compile_limit, 0
        t1 = time.monotonic()
        if stats is not None:
            stats.add('compiles')
        return ('deps', p.returncode, tree_norm(p.stdout, case_dir), tree_norm(p.stderr, case_dir)), t1 - t0, 0
    if kind == 'symcheck':
        # a committed symdir is copied, because --check writes the checked module's .sym
        fresh_dir(symdir)
        for f in os.listdir(os.path.join(case_dir, 'symdir')):
            shutil.copyfile(os.path.join(case_dir, 'symdir', f), os.path.join(symdir, f))
        args = [exe, '--check', os.path.join(case_dir, 'main.obx'), '-I', case_dir, '--sym-dir', symdir]
        t0 = time.monotonic()
        try:
            if stats is not None:
                stats.add('subprocesses')
            p = subprocess.run(args, capture_output=True, text=True, timeout=compile_limit,
                               preexec_fn=capped() if capped_run else None)
        except subprocess.TimeoutExpired:
            return ('crash', f"no result in {compile_limit:.0f} s"), compile_limit, 0
        t1 = time.monotonic()
        if stats is not None:
            stats.add('compiles')
        return ('check', p.returncode, tree_norm(p.stderr, case_dir).replace(symdir, '<sym>'), sym_map(symdir)), t1 - t0, 0
    fresh_dir(symdir)
    t0 = time.monotonic()
    try:
        if stats is not None:
            stats.add('subprocesses')
        p = subprocess.run(tree_args(exe, case_dir, out_path, symdir), capture_output=True, text=True,
                           env=dict(os.environ, OBO_CHECK='1'), timeout=compile_limit,
                           preexec_fn=capped() if capped_run else None)
    except subprocess.TimeoutExpired:
        return ('crash', f"no result in {compile_limit:.0f} s"), compile_limit, 0
    t1 = time.monotonic()
    if stats is not None:
        stats.add('compiles')
    syms = sym_map(symdir)
    if p.returncode != 0:
        return ('error', p.returncode, tree_norm(p.stderr, case_dir).replace(symdir, '<sym>'), syms), t1 - t0, 0
    shutil.rmtree(scratch, ignore_errors=True)
    os.mkdir(scratch)
    try:
        if stats is not None:
            stats.add('subprocesses')
        r = subprocess.run([out_path, scratch], capture_output=True, timeout=run_limit,
                           preexec_fn=capped(run._limits) if capped_run else run._limits,
                           env=dict(os.environ, OBO_CHECK='1'))
    except subprocess.TimeoutExpired:
        return ('ok', ('timeout', b'', b''), syms), t1 - t0, run_limit
    t2 = time.monotonic()
    if stats is not None:
        stats.add('runs')
    sb = scratch.encode()
    return ('ok', (r.returncode, r.stdout.replace(sb, b'<scratch>'), r.stderr.replace(sb, b'<scratch>')), syms), t1 - t0, t2 - t1


def header_of(path):
    try:
        with open(path, 'r', errors='replace') as f:
            for line in f:
                if line.strip().startswith('//'):
                    continue
                return line.strip().startswith('module ')
    except OSError:
        return False
    return False


def check_each_module(exe, case_dir, symdir, tmp, tag, compile_limit=120, capped_run=False, stats=None):
    """The outcome of --check for every source in the tree that has a module header,
    against a copy of the build's sym directory. Both compilers must agree."""
    out = {}
    n = 0
    for f in sorted(os.listdir(case_dir)):
        path = os.path.join(case_dir, f)
        if not f.endswith('.obx') or not os.path.isfile(path) or not header_of(path):
            continue
        cd = fresh_dir(os.path.join(tmp, f'check-{tag}-{n}-sym'))
        for s in os.listdir(symdir):
            if s.endswith('.sym'):
                shutil.copyfile(os.path.join(symdir, s), os.path.join(cd, s))
        n += 1
        args = [exe, '--check', path, '-I', case_dir, '--sym-dir', cd]
        for d in tree_includes(case_dir):
            args += ['-I', d]
        try:
            if stats is not None:
                stats.add('subprocesses')
            p = subprocess.run(args, capture_output=True, text=True, timeout=compile_limit,
                               preexec_fn=capped() if capped_run else None)
        except subprocess.TimeoutExpired:
            out[f] = ('timeout',)
            continue
        if stats is not None:
            stats.add('compiles')
        out[f] = (p.returncode, tree_norm(p.stderr, case_dir).replace(cd, '<sym>'), sym_map(cd).get(f[:-4] + '.sym'))
    return out


def tree_show(o):
    if o[0] == 'ok':
        return f"runs: exit {o[1][0]}, stdout {o[1][1][-80:]!r}, stderr {o[1][2][-80:]!r}"
    if o[0] == 'check':
        return f"check: exit {o[1]}, stderr {o[2][-120:]!r}"
    if o[0] in ('error', 'crash', 'deps'):
        return f"{o[0]} {o[1:3]!r}"
    return f"{o[0]} {o[1]!r}"


def tree_both(name, kind, case_dir, tmp, quick, port, stats=None):
    main = os.path.join(case_dir, 'main.obx')
    tag = name.replace('/', '-')
    ref_exe = os.path.join(tmp, 'tree-ref-' + tag)
    port_exe = os.path.join(tmp, 'tree-port-' + tag)
    ref_scratch = os.path.join(tmp, 'tree-ref-scratch-' + tag)
    port_scratch = os.path.join(tmp, 'tree-port-scratch-' + tag)
    ref_sym = os.path.join(tmp, 'tree-ref-sym-' + tag)
    port_sym = os.path.join(tmp, 'tree-port-sym-' + tag)
    ref = REF_TREE.get(name)
    if ref is None:
        o, tc, tr = tree_outcome(REF, kind, case_dir, ref_exe, ref_scratch, ref_sym, stats=stats)
        ref_checks = {}
        if kind in ('run',) and o[0] == 'ok':
            ref_checks = check_each_module(REF, case_dir, ref_sym, tmp, 'ref-' + tag, stats=stats)
        ref = REF_TREE[name] = (o, ref_checks, tc, tr)
    o, ref_checks, tc, tr = ref
    limits = (min(120, max(10, 20 * tc)), min(60, max(5, 20 * tr))) if quick else (120, 60)
    o2, tc, tr = tree_outcome(port, kind, case_dir, port_exe, port_scratch, port_sym, *limits,
                              capped_run=quick, stats=stats)
    if quick and o != o2 and timed_out(o2):
        o2, tc, tr = tree_outcome(port, kind, case_dir, port_exe, port_scratch, port_sym, 120, 60,
                                  capped_run=True, stats=stats)
    port_checks = {}
    if kind in ('run',) and o2[0] == 'ok':
        port_checks = check_each_module(port, case_dir, port_sym, tmp, 'port-' + tag,
                                        limits[0], capped_run=quick, stats=stats)
    return (o, o2, ref_checks, port_checks)


def token_outcome(exe, stats=None):
    if stats is not None:
        stats.add('subprocesses')
    p = subprocess.run([exe, '--tokens', os.path.join(MODDIR, 'tokens.obx')],
                       capture_output=True, text=True, timeout=60)
    return (p.returncode, p.stdout, p.stderr)


CLI_CASES = [
    ('--version', lambda exe, d: [exe, '--version']),
    ('unknown option', lambda exe, d: [exe, '--not-an-option']),
    ('missing value', lambda exe, d: [exe, os.path.join(d, 'main.obx'), '-I']),
    ('check with -o', lambda exe, d: [exe, '--check', os.path.join(d, 'main.obx'), '-o', 'x', '--sym-dir', d]),
]


def special_run(cmd, quick, cwd=None, stats=None):
    try:
        if stats is not None:
            stats.add('subprocesses')
        return subprocess.run(cmd, capture_output=True, text=True, timeout=60 if quick else 120,
                              cwd=cwd, preexec_fn=capped() if quick else None)
    except subprocess.TimeoutExpired:
        return None


def special_norm(text, root):
    return text.replace(root, '<tmp>').strip()


def special_syms(d):
    out = []
    if os.path.isdir(d):
        for f in sorted(os.listdir(d)):
            if f.endswith('.sym'):
                with open(os.path.join(d, f), 'rb') as fh:
                    out.append((f, fh.read()))
    return tuple(out)


def special_emit_sym(exe, root, quick, stats=None):
    tree = os.path.join(root, 't')
    os.makedirs(tree)
    src = os.path.join(tree, 'single.obx')
    open(src, 'w').write('module single\npub fn f(): int { return 1 }\nfn main() { println(f()) }\n')
    sym = os.path.join(root, 'sym')
    os.makedirs(sym)
    p = special_run([exe, src, '--emit-sym', '-o', os.path.join(root, 'p'), '--rt', FORK, '--sym-dir', sym], quick, stats=stats)
    if p is None:
        return ('timeout',)
    return (p.returncode, special_norm(p.stderr, root), special_syms(sym))


def special_emit_headerless(exe, root, quick, stats=None):
    src = os.path.join(root, 'nohdr.obx')
    open(src, 'w').write('fn main() { }\n')
    sym = os.path.join(root, 'sym')
    os.makedirs(sym)
    p = special_run([exe, src, '--emit-sym', '-o', os.path.join(root, 'p'), '--rt', FORK, '--sym-dir', sym], quick, stats=stats)
    if p is None:
        return ('timeout',)
    return (p.returncode, special_norm(p.stderr, root), special_syms(sym))


def special_check_headerless(exe, root, quick, stats=None):
    src = os.path.join(root, 'nohdr.obx')
    open(src, 'w').write('fn main() { }\n')
    sym = os.path.join(root, 'sym')
    os.makedirs(sym)
    p = special_run([exe, '--check', src, '--sym-dir', sym], quick, stats=stats)
    if p is None:
        return ('timeout',)
    return (p.returncode, special_norm(p.stderr, root), special_syms(sym))


def special_default_symdir(exe, root, quick, stats=None):
    tree = os.path.join(root, 't')
    os.makedirs(tree)
    open(os.path.join(tree, 'lib.obx'), 'w').write('module lib\npub fn f(): int { return 1 }\n')
    open(os.path.join(tree, 'main.obx'), 'w').write('module main\nimport "lib.obx"\nfn main() { println(lib::f()) }\n')
    p = special_run([exe, os.path.join(tree, 'main.obx'), '-o', os.path.join(root, 'p'), '--rt', FORK, '-I', tree], quick, cwd=root, stats=stats)
    if p is None:
        return ('timeout',)
    return (p.returncode, special_norm(p.stderr, root), special_syms(tree), special_syms(root))


def special_sourceless_build(exe, root, quick, stats=None):
    tree = os.path.join(root, 't')
    os.makedirs(tree)
    lib = os.path.join(tree, 'lib.obx')
    open(lib, 'w').write('module lib\npub fn f(): int { return 1 }\n')
    open(os.path.join(tree, 'main.obx'), 'w').write('module main\nimport "lib.obx"\nfn main() { println(lib::f()) }\n')
    sym = os.path.join(root, 'sym')
    os.makedirs(sym)
    p = special_run([exe, os.path.join(tree, 'main.obx'), '-o', os.path.join(root, 'p'), '--rt', FORK,
                     '-I', tree, '--sym-dir', sym], quick, stats=stats)
    if p is None or p.returncode != 0:
        return ('setup', p.returncode if p else 'timeout')
    os.unlink(lib)
    p = special_run([exe, os.path.join(tree, 'main.obx'), '-o', os.path.join(root, 'p2'), '--rt', FORK,
                     '-I', tree, '--sym-dir', sym], quick, stats=stats)
    if p is None:
        return ('timeout',)
    return (p.returncode, special_norm(p.stderr, root))


SPECIAL_CASES = [
    ('emit-sym', special_emit_sym),
    ('emit-sym headerless', special_emit_headerless),
    ('check headerless', special_check_headerless),
    ('default sym-dir', special_default_symdir),
    ('source-less build', special_sourceless_build),
]

def read_symcheck_expect(case_dir):
    """(exit code, stderr lines) an `expect` file states for a symcheck case."""
    path = os.path.join(case_dir, 'expect')
    code, err = 0, []
    if os.path.exists(path):
        for line in open(path).read().splitlines():
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            if line.startswith('exit '):
                code = int(line[5:])
            elif line.startswith('stderr '):
                err.append(line[7:])
            else:
                raise SystemExit(f"{path}: unknown expect line {line!r}")
    return code, err


def symcheck_expect_ok(case_dir, o):
    """True if REF's own symcheck outcome is the one the case intends. This is
    checked before REF and PORT are compared, so a blanket reader regression (a
    header format change, say) fails on the intended positive cases instead of
    passing because both compilers reject the file identically."""
    code, errs = read_symcheck_expect(case_dir)
    if o[0] != 'check' or o[1] != code:
        return False
    # exact equality: the expect file states the whole normalized stderr
    if o[2] != '\n'.join(errs):
        return False
    return True


REEMIT_CASES = [
    ('reemit high pfree bits', os.path.join(MODDIR, 'sym', 'reemit_highbits', 'symdir', 'many.sym')),
]


def special_pair(name, fn, tmp, quick, port, stats=None):
    """(REF outcome, PORT outcome) for one special check. A helper returns data
    (exit code, output, ...), so a different outcome is a mutant difference and
    is returned normally. A helper that raises is a harness bug: it becomes a
    HarnessError and aborts, rather than being appended as a caught mutant. A
    TimeoutExpired is a load/limit outcome, not a crash, and is returned as the
    usual ('timeout',) data so the full path can confirm it."""
    rroot = fresh_dir(os.path.join(tmp, 'special-ref-' + name.replace(' ', '-')))
    proot = fresh_dir(os.path.join(tmp, 'special-port-' + name.replace(' ', '-')))
    try:
        a = fn(REF, rroot, False, stats=stats)
    except subprocess.TimeoutExpired:
        a = ('timeout',)
    except Exception as exc:  # noqa: BLE001 - distinguish a helper bug from mutant data
        raise HarnessError(f"special {name}: REF helper raised {exc!r}") from exc
    try:
        b = fn(port, proot, quick, stats=stats)
    except subprocess.TimeoutExpired:
        b = ('timeout',)
    except Exception as exc:  # noqa: BLE001 - distinguish a helper bug from mutant data
        raise HarnessError(f"special {name}: PORT helper raised {exc!r}") from exc
    return a, b


def _timeout_failure(failures):
    """True when every failure says only that a process timed out. The quick path
    retries a timeout once with full limits (see both and tree_both); if it still
    only times out, the full path decides, so a loaded host is not a catch."""
    if not failures:
        return False
    words = ('timed out', 'timeout', 'no result in')
    return all(any(w in f.lower() for w in words) for f in failures)


def _oom_failure(failures):
    """True when a failure includes an out-of-memory report. It is returned as a
    catch (the unmutated compiler does not run out of memory) but marked for the
    serial re-run, which is the confirmation step; if it was the load, the serial
    run decides instead."""
    return bool(failures) and any('out of memory' in f.lower() for f in failures)


def compare_trees(tmp, quick, port, only=None, stats=None):
    """(same, total, failures) for the module trees, the .sym fixtures, the CLI and
    the token dump. only, when given, is a set of check names (a tree case name,
    'cli NAME', 'special NAME', 'reemit NAME' or 'modules/tokens'): the others are
    not run, which is how a mutant's catcher runs one check without the rest."""
    if os.path.realpath(REF) == SEED_REF:
        return 0, 0, []
    wanted = None if only is None else set(only)
    same, total, failures = 0, 0, []
    for name, kind, case_dir in tree_cases():
        if wanted is not None and name not in wanted:
            continue
        total += 1
        a, b, ref_checks, port_checks = tree_both(name, kind, case_dir, tmp, quick, port, stats=stats)
        if kind == 'symcheck' and not symcheck_expect_ok(case_dir, a):
            failures.append(f"{name}: REF's outcome is not the case's intended one: {tree_show(a)!r}")
            if quick:
                return same, total, failures
        if a == b:
            same += 1
        else:
            failures.append(f"{name}: obo {tree_show(a)!r}; port {tree_show(b)!r}")
            if quick:
                return same, total, failures
        if ref_checks != port_checks:
            failures.append(f"{name}: --check differs: obo {ref_checks!r}; port {port_checks!r}")
            if quick:
                return same, total, failures
    # a .sym fixture tree for the CLI (--check with -o is rejected)
    cli_wanted = wanted is None or any(('cli ' + n) in wanted for n, _ in CLI_CASES)
    cli_dir = os.path.join(tmp, 'cli')
    if cli_wanted:
        os.makedirs(cli_dir, exist_ok=True)
        open(os.path.join(cli_dir, 'main.obx'), 'w').write('module main\nfn main() { }\n')
    for name, make in CLI_CASES:
        if wanted is not None and ('cli ' + name) not in wanted:
            continue
        total += 1
        try:
            if stats is not None:
                stats.add('subprocesses', 2)
            a = subprocess.run(make(REF, cli_dir), capture_output=True, text=True, timeout=60)
            b = subprocess.run(make(port, cli_dir), capture_output=True, text=True, timeout=60)
            ao = (a.returncode, a.stdout, a.stderr)
            bo = (b.returncode, b.stdout, b.stderr)
        except subprocess.TimeoutExpired:
            failures.append(f"cli {name}: a run timed out")
            continue
        if ao == bo:
            same += 1
        else:
            failures.append(f"cli {name}: obo {ao!r}; port {bo!r}")
    for name, fn in SPECIAL_CASES:
        if wanted is not None and ('special ' + name) not in wanted:
            continue
        total += 1
        a, b = special_pair(name, fn, tmp, quick, port, stats=stats)
        if a == b:
            same += 1
        else:
            failures.append(f"special {name}: obo {a!r}; port {b!r}")
    for name, path in REEMIT_CASES:
        if wanted is not None and ('reemit ' + name) not in wanted:
            continue
        total += 1
        try:
            if stats is not None:
                stats.add('subprocesses', 2)
            a = subprocess.run([REF, '--reemit-sym', path], capture_output=True, timeout=60)
            b = subprocess.run([port, '--reemit-sym', path], capture_output=True, timeout=60)
            ao = (a.returncode, a.stdout, a.stderr)
            bo = (b.returncode, b.stdout, b.stderr)
        except subprocess.TimeoutExpired:
            failures.append(f"reemit {name}: a run timed out")
            continue
        # a fixture that should re-emit must do so, canonically, rather than
        # merely agree with a port that also failed
        if a.returncode != 0 or b.returncode != 0:
            failures.append(f"reemit {name}: exit obo {a.returncode}, port {b.returncode}")
        elif not a.stdout or not a.stdout.startswith(b'obosym 5\n') or b'fn many(' not in a.stdout:
            failures.append(f"reemit {name}: obo's output is not canonical: {a.stdout[:80]!r}")
        else:
            modline = next((l for l in a.stdout.splitlines() if l.startswith(b'module ')), b'module many')
            modname = modline.split(b' ', 1)[1].decode()
            again_dir = fresh_dir(os.path.join(tmp, f'reemit-again-{total}'))
            again_path = os.path.join(again_dir, modname + '.sym')
            with open(again_path, 'wb') as fh:
                fh.write(a.stdout)
            if stats is not None:
                stats.add('subprocesses')
            again = subprocess.run([REF, '--reemit-sym', again_path],
                                   capture_output=True, timeout=60)
            if again.returncode != 0 or again.stdout != a.stdout:
                failures.append(f"reemit {name}: a second re-emission differs")
            elif ao == bo:
                same += 1
            else:
                failures.append(f"reemit {name}: obo {ao[0]}; port {bo[0]}")
    if wanted is None or 'modules/tokens' in wanted:
        total += 1
        try:
            a, b = token_outcome(REF, stats=stats), token_outcome(port, stats=stats)
        except subprocess.TimeoutExpired:
            failures.append("modules/tokens: a --tokens run timed out")
            return same, total, failures
        if a == b:
            same += 1
        else:
            failures.append(f"modules/tokens: obo {a[0]}; port {b[0]}")
    return same, total, failures


def case_rank(name):
    """A cheap-first order for the quick (mutant) path: error cases are compile-only,
    then run programs, then examples, then the compiler itself, and the benchmarks
    last. The full path keeps the catalogue's order and still runs everything."""
    if ':' in name and not name.endswith('.obx'):
        return 0                                   # an error case: one compile, no run
    if '/bench/' in name:
        return 4
    if name.endswith('src/obo.obo') or name.endswith('/obo.obo'):
        return 3
    if '/examples/' in name:
        return 2
    return 1


def ordered_cases(cases, quick):
    """The cases to run, in a cheap-first order on the quick path only."""
    if not quick:
        return cases
    return sorted(cases, key=lambda nt: case_rank(nt[0]))


_KNOWN_CHECKS = None


def known_checks():
    """Every check name a catcher may select: flat cases, tree cases, CLI, special,
    reemit and the token dump. Used to reject a stale catcher entry instead of
    silently treating it as a pass. The catalogue is fixed for a run, so it is
    built once."""
    global _KNOWN_CHECKS
    if _KNOWN_CHECKS is None:
        names = set(n for n, _ in cases_of(SRC))
        names.update(n for n, _, _ in tree_cases())
        names.update('cli ' + n for n, _ in CLI_CASES)
        names.update('special ' + n for n, _ in SPECIAL_CASES)
        names.update('reemit ' + n for n, _ in REEMIT_CASES)
        names.add('modules/tokens')
        _KNOWN_CHECKS = names
    return _KNOWN_CHECKS


def run_catchers(keys, flat_text, run_flat, run_tree, stats=None):
    """Run the selected checks in order, before the full comparison. Returns
    (failing key or None, transient or not, unknown keys). A key that is not in
    the catalogue is unknown: it is reported and ignored, never a pass or a catch
    on its own. A transient result is left to the full path, which retries it at
    full limits. So a catcher can only shorten a run when it finds a real
    difference; otherwise every case still runs."""
    unknown = []
    for key in dict.fromkeys(keys):
        if key in flat_text:
            a, b = run_flat(flat_text[key])
            if a != b:
                if timed_out(b):
                    if stats is not None:
                        stats.add('retries')
                    continue
                if b[0] == 'crash' and 'out of memory' in str(b).lower():
                    return key, True, unknown
                return key, False, unknown
        elif key in known_checks():
            failures = run_tree(key)
            if failures:
                if _timeout_failure(failures):
                    if stats is not None:
                        stats.add('retries')
                    continue
                if _oom_failure(failures):
                    return key, True, unknown
                return key, False, unknown
        else:
            unknown.append(key)
    return None, False, unknown


def compare(tmp, src=SRC, quick=False, workers=None, catchers=None, stats=None,
            catchers_only=False):
    """Compares the port built from src with REF on every case, in the directory tmp.
    Returns the report's lines, the failures, and the port's live peak compiling itself
    (None if it did not report one). quick, for a mutant: stops at the first failure,
    and allows each case 20 times what REF took, so a mutant that loops is soon caught.
    workers is the number of case comparisons at once (default: one per core); a
    caller that runs several comparisons side by side passes a smaller number so
    that the total stays about the core count.

    catchers, a mutant only, is a list of check names to try before the full
    comparison (flat case, tree case, CLI, special, reemit or modules/tokens). A
    catcher that differs ends the comparison; one that passes, or is unknown, falls
    through to the full comparison. catchers_only runs just that preface and returns,
    which is how the baseline verifies the catcher paths. stats, when given, receives
    stage seconds and thread-safe subprocess/case counts (see mutate_stats)."""
    if stats is None:
        stats = mutate_stats.Stats()
    PORT = os.path.join(tmp, 'obo_port')
    t0 = time.monotonic()
    stats.add('subprocesses')
    b = subprocess.run([REF, src, '-o', PORT, '--rt', FORK], capture_output=True, text=True)
    build = time.monotonic() - t0
    stats.set('build_seconds', round(build, 3))
    if b.returncode != 0:
        if quick:
            stats.set('first_failure_stage', 'build')
            return [], ["the port does not compile: " + b.stderr.strip()[:200]], None
        raise SystemExit("the port does not compile: " + b.stderr)
    lines, failures, peak = [], [], None
    try:
        stats.add('subprocesses')
        p = subprocess.run([PORT, src, '-o', os.path.join(tmp, 'self'), '--rt', FORK], capture_output=True, text=True,
                           env=dict(os.environ, OBO_CHECK='1', OBO_STATS='1'),
                           timeout=min(120, max(10, 20 * build)) if quick else 120,
                           preexec_fn=capped() if quick else None)
        m = re.search(r'obo: (\d+) peak bytes, (\d+) allocated', p.stderr)
    except subprocess.TimeoutExpired:
        p, m = subprocess.CompletedProcess([], 'timeout', '', ''), None
    if p.returncode != 0 or not m:
        failures.append(f"memory: no OBO_STATS report (exit {p.returncode}): {p.stderr.strip()[:200]}")
        stats.set('first_failure_stage', 'memory')
    else:
        peak, total = int(m.group(1)), int(m.group(2))
        lines.append(f"memory: compiling itself, the port peaks at {peak:,} bytes live "
                     f"(budget {PEAK_BUDGET:,}) and allocates {total:,} in total")
        if peak > PEAK_BUDGET:
            failures.append(f"memory: live peak {peak:,} bytes is over the budget of {PEAK_BUDGET:,}")
            stats.set('first_failure_stage', 'memory')
    if quick and failures:
        return lines, failures, peak
    modules = os.path.realpath(REF) != SEED_REF
    cases = cases_of(src, seed=not modules)

    def imports_module(text):
        return any(line.startswith(b'import "') for line in text.splitlines())

    def module_args(symdir, needs_modules):
        # the default comparison passes the module flags to every case; the seed
        # comparison needs them only for the module roots, and those roots stay in
        if not modules and not needs_modules:
            return ()
        os.makedirs(symdir, exist_ok=True)
        return ['-I', LIBDIR, '--sym-dir', symdir]

    def both(text):
        h = hashlib.sha1(text).hexdigest()[:16]
        case = os.path.join(tmp, f'case-{h}.obx')
        open(case, 'wb').write(text)
        scratch = os.path.join(tmp, f'scratch-{h}')
        needs_modules = imports_module(text)
        ref = REF_OUTCOMES.get(text)
        if ref is None:
            ref = REF_OUTCOMES[text] = outcome(
                REF, case, case + '.ref', scratch,
                module_args=module_args(case + '.refsym', needs_modules), stats=stats)
        o, tc, tr = ref
        limits = (min(120, max(10, 20 * tc)), min(60, max(5, 20 * tr))) if quick else (120, 60)
        args = module_args(case + '.portsym', needs_modules)
        p = outcome(PORT, case, case + '.port', scratch, *limits, capped_run=quick,
                    module_args=args, stats=stats)[0]
        if quick and o != p and timed_out(p):
            # a timeout on a loaded machine is not a mismatch: judge the case again with the full limits
            stats.add('retries')
            p = outcome(PORT, case, case + '.port', scratch, 120, 60, capped_run=True,
                        module_args=args, stats=stats)[0]
        return o, p

    # Catchers: a mutant's known first failing check, run before the full sweep.
    # The full sweep still runs on a pass (or an unknown name), so this changes
    # the order, not the verdict.
    if quick and catchers:
        flat_text = {name: text for name, text in cases}
        t = time.monotonic()
        before = stats.get('subprocesses', 0)
        stats.set('catcher_cases', len(list(dict.fromkeys(catchers))))
        key, transient, unknown = run_catchers(
            catchers, flat_text, both,
            lambda k: compare_trees(tmp, quick, PORT, only={k}, stats=stats)[2],
            stats=stats)
        stats.set('catcher_seconds', round(time.monotonic() - t, 3))
        stats.set('catcher_subprocesses', stats.get('subprocesses', 0) - before)
        if unknown:
            stats.set('unknown_catchers', unknown)
        if key is not None:
            stats.set('first_failure_stage', 'catcher')
            stats.set('first_failure_case', key)
            stats.set('catcher_transient', transient)
            return [f"FAIL {key}"], [f"catcher {key} differs from REF"], peak
        if catchers_only:
            return lines + [f"catchers: {stats.get('catcher_cases', 0)} passed"], [], peak

    ordered = ordered_cases(cases, quick)
    unique = list(dict.fromkeys(text for _, text in ordered))
    name_of = {}
    for name, text in ordered:
        name_of.setdefault(text, name)
    results = {}
    t = time.monotonic()
    before = stats.get('subprocesses', 0)
    width = workers or os.cpu_count() or 1
    with concurrent.futures.ThreadPoolExecutor(width) as pool:
        pending = {}
        it = iter(unique)
        stop = False

        def fill():
            # bound the in-flight cases, as run_fork does: with a failure, the
            # cases not started are cancelled and nothing waits behind them
            while not stop and len(pending) < width:
                try:
                    text = next(it)
                except StopIteration:
                    return
                pending[pool.submit(both, text)] = text

        fill()
        while pending:
            done, _ = concurrent.futures.wait(pending, return_when=concurrent.futures.FIRST_COMPLETED)
            for f in done:
                text = pending.pop(f)
                a, b = f.result()
                results[text] = (a, b)
                if quick and a != b and not stop:
                    stats.set('first_failure_stage', 'flat')
                    stats.set('first_failure_case', name_of.get(text, text[:80]))
                    stop = True
            if stop:
                for f in pending:
                    f.cancel()
                break
            fill()
    stats.set('flat_seconds', round(time.monotonic() - t, 3))
    stats.set('flat_cases', len(results))
    stats.set('flat_subprocesses', stats.get('subprocesses', 0) - before)
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
    # A definitive flat failure already fails the mutant: the module trees add
    # nothing to the verdict and are skipped on the quick path.
    if quick and failures:
        return lines + ["FAIL " + f for f in failures[:12]], failures, peak
    t = time.monotonic()
    before = stats.get('subprocesses', 0)
    tree_same, tree_total, tree_failures = compare_trees(tmp, quick, PORT, stats=stats)
    stats.set('tree_seconds', round(time.monotonic() - t, 3))
    stats.set('tree_cases', tree_total)
    stats.set('tree_subprocesses', stats.get('subprocesses', 0) - before)
    if tree_failures and stats.get('first_failure_stage') is None:
        stats.set('first_failure_stage', 'trees')
        stats.set('first_failure_case', tree_failures[0][:120])
    failures += tree_failures
    same += tree_same
    total = len(cases) + tree_total
    lines += ["FAIL " + f for f in failures[:12]]
    lines.append(f"\nstage B: {same} of {total} behave the same")
    return lines, failures, peak


if __name__ == '__main__':
    main()
