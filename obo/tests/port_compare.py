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
import run  # noqa: E402  (for error_cases)

REF = os.path.abspath(os.environ.get('OBO_REF', os.path.join(FORK, 'obo')))
SRC = os.path.join(FORK, 'src', 'obo.obo')
LIBDIR = os.path.join(FORK, 'lib')
# live peak, in bytes, of the port compiling itself. Lower it when memory improves; raise it only
# for a reason recorded in FORK.md. The current size-fixed tree measures 1,636,176 bytes live and
# 35,076,608 bytes total allocation (FORK.md, "Compiler size fixes"); the budget stays 2,000,000
# (363,824 spare above that peak). The figure is for the current tree and varies by a few hundred
# bytes with the source path. Leaks are caught separately, by port_test's leak check.
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


def outcome(exe, src_path, out_path, scratch, compile_limit=120, run_limit=60, capped_run=False,
            module_args=()):
    """('ok', (exit code, stdout, stderr)) for a program that compiles, run with
    OBO_CHECK=1 and a fresh scratch directory as its argument (as run.py does);
    ('error', 'line:col: message') or ('crash', ...) for one that does not. The case's
    directory is written <tmp> in the result, so outcomes from different directories
    compare. Also returns the seconds the compile and the run took. capped_run puts the
    compile and the run under the address-space cap (the port's own, in quick mode)."""
    tmp = os.path.dirname(src_path)
    t0 = time.monotonic()
    try:
        p = subprocess.run([exe, src_path, '-o', out_path, '--rt', FORK, *module_args],
                           capture_output=True, text=True,
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


def cases_of(src, seed=False):
    """(name, text) of every case: the programs, src itself, and the error cases.
    A program that imports modules is compiled as a module root (see the module
    arguments in compare); there is no source injection.

    In the seed comparison (seed=True) the compiler-feature cases are skipped:
    the promoted seed predates the P3 metadata builtins, so it rejects
    tests/run/meta.obx and tests/errors/meta.obx (whose cases use statPath and
    friends) while the port compiles them; the default comparison (obo and the
    port from the same source) covers them. This is the only seed-mode
    exclusion besides the module trees; it disappears when the seed is promoted
    with P3."""
    cases = []
    files = sorted(glob.glob(os.path.join(FORK, 'tests', 'run', '*.obx')) + glob.glob(os.path.join(FORK, 'examples', '*.obx')) +
                   glob.glob(os.path.join(FORK, 'bench', '*.obx')) + glob.glob(os.path.join(ROOT, 'tests', 'run', '*.obx')) +
                   glob.glob(os.path.join(ROOT, 'examples', '*.obx')) + [src])
    for f in files:
        if seed and f in SEED_SKIP:
            continue
        text = open(f, 'rb').read()
        cases.append((os.path.relpath(f, ROOT), text))
    for f in sorted(glob.glob(os.path.join(FORK, 'tests', 'errors', '*.obx')) + glob.glob(os.path.join(ROOT, 'tests', 'errors', '*.obx'))):
        if seed and f in SEED_SKIP:
            continue
        for name, text, _, _ in run.error_cases(f):
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

# The P3 compiler-feature cases use the metadata builtins, which the promoted
# seed predates. The seed comparison skips these files (see cases_of); the
# default comparison covers them, and the exclusion goes away with the promote.
SEED_SKIP = {os.path.join(FORK, 'tests', 'run', 'meta.obx'),
             os.path.join(FORK, 'tests', 'errors', 'meta.obx')}


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


def tree_outcome(exe, kind, case_dir, out_path, scratch, symdir, compile_limit=120, run_limit=60, capped_run=False):
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
    if kind == 'symcheck':
        # a committed symdir is copied, because --check writes the checked module's .sym
        fresh_dir(symdir)
        for f in os.listdir(os.path.join(case_dir, 'symdir')):
            shutil.copyfile(os.path.join(case_dir, 'symdir', f), os.path.join(symdir, f))
        args = [exe, '--check', os.path.join(case_dir, 'main.obx'), '-I', case_dir, '--sym-dir', symdir]
        t0 = time.monotonic()
        try:
            p = subprocess.run(args, capture_output=True, text=True, timeout=compile_limit,
                               preexec_fn=capped() if capped_run else None)
        except subprocess.TimeoutExpired:
            return ('crash', f"no result in {compile_limit:.0f} s"), compile_limit, 0
        t1 = time.monotonic()
        return ('check', p.returncode, tree_norm(p.stderr, case_dir).replace(symdir, '<sym>'), sym_map(symdir)), t1 - t0, 0
    fresh_dir(symdir)
    t0 = time.monotonic()
    try:
        p = subprocess.run(tree_args(exe, case_dir, out_path, symdir), capture_output=True, text=True,
                           env=dict(os.environ, OBO_CHECK='1'), timeout=compile_limit,
                           preexec_fn=capped() if capped_run else None)
    except subprocess.TimeoutExpired:
        return ('crash', f"no result in {compile_limit:.0f} s"), compile_limit, 0
    t1 = time.monotonic()
    syms = sym_map(symdir)
    if p.returncode != 0:
        return ('error', p.returncode, tree_norm(p.stderr, case_dir).replace(symdir, '<sym>'), syms), t1 - t0, 0
    shutil.rmtree(scratch, ignore_errors=True)
    os.mkdir(scratch)
    try:
        r = subprocess.run([out_path, scratch], capture_output=True, timeout=run_limit,
                           preexec_fn=capped(run._limits) if capped_run else run._limits,
                           env=dict(os.environ, OBO_CHECK='1'))
    except subprocess.TimeoutExpired:
        return ('ok', ('timeout', b'', b''), syms), t1 - t0, run_limit
    t2 = time.monotonic()
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


def check_each_module(exe, case_dir, symdir, tmp, tag, compile_limit=120, capped_run=False):
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
            p = subprocess.run(args, capture_output=True, text=True, timeout=compile_limit,
                               preexec_fn=capped() if capped_run else None)
        except subprocess.TimeoutExpired:
            out[f] = ('timeout',)
            continue
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


def tree_both(name, kind, case_dir, tmp, quick, port):
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
        o, tc, tr = tree_outcome(REF, kind, case_dir, ref_exe, ref_scratch, ref_sym)
        ref_checks = {}
        if kind in ('run',) and o[0] == 'ok':
            ref_checks = check_each_module(REF, case_dir, ref_sym, tmp, 'ref-' + tag)
        ref = REF_TREE[name] = (o, ref_checks, tc, tr)
    o, ref_checks, tc, tr = ref
    limits = (min(120, max(10, 20 * tc)), min(60, max(5, 20 * tr))) if quick else (120, 60)
    o2, tc, tr = tree_outcome(port, kind, case_dir, port_exe, port_scratch, port_sym, *limits, capped_run=quick)
    if quick and o != o2 and timed_out(o2):
        o2, tc, tr = tree_outcome(port, kind, case_dir, port_exe, port_scratch, port_sym, 120, 60, capped_run=True)
    port_checks = {}
    if kind in ('run',) and o2[0] == 'ok':
        port_checks = check_each_module(port, case_dir, port_sym, tmp, 'port-' + tag,
                                        limits[0], capped_run=quick)
    return (o, o2, ref_checks, port_checks)


def token_outcome(exe):
    p = subprocess.run([exe, '--tokens', os.path.join(MODDIR, 'tokens.obx')],
                       capture_output=True, text=True, timeout=60)
    return (p.returncode, p.stdout, p.stderr)


CLI_CASES = [
    ('--version', lambda exe, d: [exe, '--version']),
    ('unknown option', lambda exe, d: [exe, '--not-an-option']),
    ('missing value', lambda exe, d: [exe, os.path.join(d, 'main.obx'), '-I']),
    ('check with -o', lambda exe, d: [exe, '--check', os.path.join(d, 'main.obx'), '-o', 'x', '--sym-dir', d]),
]


def special_run(cmd, quick, cwd=None):
    try:
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


def special_emit_sym(exe, root, quick):
    tree = os.path.join(root, 't')
    os.makedirs(tree)
    src = os.path.join(tree, 'single.obx')
    open(src, 'w').write('module single\npub fn f(): int { return 1 }\nfn main() { println(f()) }\n')
    sym = os.path.join(root, 'sym')
    os.makedirs(sym)
    p = special_run([exe, src, '--emit-sym', '-o', os.path.join(root, 'p'), '--rt', FORK, '--sym-dir', sym], quick)
    if p is None:
        return ('timeout',)
    return (p.returncode, special_norm(p.stderr, root), special_syms(sym))


def special_emit_headerless(exe, root, quick):
    src = os.path.join(root, 'nohdr.obx')
    open(src, 'w').write('fn main() { }\n')
    sym = os.path.join(root, 'sym')
    os.makedirs(sym)
    p = special_run([exe, src, '--emit-sym', '-o', os.path.join(root, 'p'), '--rt', FORK, '--sym-dir', sym], quick)
    if p is None:
        return ('timeout',)
    return (p.returncode, special_norm(p.stderr, root), special_syms(sym))


def special_check_headerless(exe, root, quick):
    src = os.path.join(root, 'nohdr.obx')
    open(src, 'w').write('fn main() { }\n')
    sym = os.path.join(root, 'sym')
    os.makedirs(sym)
    p = special_run([exe, '--check', src, '--sym-dir', sym], quick)
    if p is None:
        return ('timeout',)
    return (p.returncode, special_norm(p.stderr, root), special_syms(sym))


def special_default_symdir(exe, root, quick):
    tree = os.path.join(root, 't')
    os.makedirs(tree)
    open(os.path.join(tree, 'lib.obx'), 'w').write('module lib\npub fn f(): int { return 1 }\n')
    open(os.path.join(tree, 'main.obx'), 'w').write('module main\nimport "lib.obx"\nfn main() { println(lib::f()) }\n')
    p = special_run([exe, os.path.join(tree, 'main.obx'), '-o', os.path.join(root, 'p'), '--rt', FORK, '-I', tree],
                    quick, cwd=root)
    if p is None:
        return ('timeout',)
    return (p.returncode, special_norm(p.stderr, root), special_syms(tree), special_syms(root))


def special_sourceless_build(exe, root, quick):
    tree = os.path.join(root, 't')
    os.makedirs(tree)
    lib = os.path.join(tree, 'lib.obx')
    open(lib, 'w').write('module lib\npub fn f(): int { return 1 }\n')
    open(os.path.join(tree, 'main.obx'), 'w').write('module main\nimport "lib.obx"\nfn main() { println(lib::f()) }\n')
    sym = os.path.join(root, 'sym')
    os.makedirs(sym)
    p = special_run([exe, os.path.join(tree, 'main.obx'), '-o', os.path.join(root, 'p'), '--rt', FORK,
                     '-I', tree, '--sym-dir', sym], quick)
    if p is None or p.returncode != 0:
        return ('setup', p.returncode if p else 'timeout')
    os.unlink(lib)
    p = special_run([exe, os.path.join(tree, 'main.obx'), '-o', os.path.join(root, 'p2'), '--rt', FORK,
                     '-I', tree, '--sym-dir', sym], quick)
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

REEMIT_CASES = [
    ('reemit high pfree bits', os.path.join(MODDIR, 'sym', 'reemit_highbits', 'symdir', 'many.sym')),
]


def compare_trees(tmp, quick, port):
    """(same, total, failures) for the module trees, the .sym fixtures, the CLI and
    the token dump."""
    if os.path.realpath(REF) == SEED_REF:
        return 0, 0, []
    same, total, failures = 0, 0, []
    for name, kind, case_dir in tree_cases():
        total += 1
        a, b, ref_checks, port_checks = tree_both(name, kind, case_dir, tmp, quick, port)
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
    cli_dir = os.path.join(tmp, 'cli')
    os.makedirs(cli_dir, exist_ok=True)
    open(os.path.join(cli_dir, 'main.obx'), 'w').write('module main\nfn main() { }\n')
    for name, make in CLI_CASES:
        total += 1
        try:
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
        total += 1
        rroot = fresh_dir(os.path.join(tmp, 'special-ref-' + name.replace(' ', '-')))
        proot = fresh_dir(os.path.join(tmp, 'special-port-' + name.replace(' ', '-')))
        try:
            a = fn(REF, rroot, False)
            b = fn(port, proot, quick)
        except Exception as e:  # noqa: BLE001 - a harness problem is a failure, not a crash
            failures.append(f"special {name}: {e!r}")
            continue
        if a == b:
            same += 1
        else:
            failures.append(f"special {name}: obo {a!r}; port {b!r}")
    for name, path in REEMIT_CASES:
        total += 1
        try:
            a = subprocess.run([REF, '--reemit-sym', path], capture_output=True, timeout=60)
            b = subprocess.run([port, '--reemit-sym', path], capture_output=True, timeout=60)
            ao = (a.returncode, a.stdout, a.stderr)
            bo = (b.returncode, b.stdout, b.stderr)
        except subprocess.TimeoutExpired:
            failures.append(f"reemit {name}: a run timed out")
            continue
        if ao == bo:
            same += 1
        else:
            failures.append(f"reemit {name}: obo {ao[0]}; port {bo[0]}")
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
                module_args=module_args(case + '.refsym', needs_modules))
        o, tc, tr = ref
        limits = (min(120, max(10, 20 * tc)), min(60, max(5, 20 * tr))) if quick else (120, 60)
        args = module_args(case + '.portsym', needs_modules)
        p = outcome(PORT, case, case + '.port', scratch, *limits, capped_run=quick,
                    module_args=args)[0]
        if quick and o != p and timed_out(p):
            # a timeout on a loaded machine is not a mismatch: judge the case again with the full limits
            p = outcome(PORT, case, case + '.port', scratch, 120, 60, capped_run=True,
                        module_args=args)[0]
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
