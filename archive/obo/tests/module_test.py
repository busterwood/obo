#!/usr/bin/env python3
"""Stage 1 module tests: source imports, exports, type identity and effects.

Each case is a directory under tests/modules/:

  run/<case>/main.obx      a program (comments as in tests/run) plus its modules
  errors/<case>/main.obx   a root that must fail, plus its modules and an
                           `expect` file: `exit N` and exact `stderr ...` lines
                           (absolute case paths are normalized to basenames)
  deps/<case>/main.obx     a `--deps` case, same `expect` form (`stdout ...` lines)

The root is compiled with -I <case dir> and a private output name, then run
with OBO_CHECK=1 and a scratch directory, so leaks and double frees fail.

    python3 obo/tests/module_test.py [-k pattern]
"""
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
FORK = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(os.path.dirname(FORK), 'obx', 'tests'))
import run  # noqa: E402

OBO = os.path.abspath(os.environ.get('OBO', os.path.join(FORK, 'obo')))
RT = FORK
MOD = os.path.join(HERE, 'modules')
TOKENS = os.path.join(MOD, 'tokens.obx')
os.environ.setdefault('OBO_CHECK', '1')


def normalize(text, case_dir):
    """A case directory's absolute paths become the basenames the expect file uses."""
    for p in (case_dir + os.sep, case_dir + '/'):
        text = text.replace(p, '')
    return text.strip()


def parse_run_comments(src):
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
    return expect, exit_code, stderr_subs


def read_expect(case_dir):
    exit_code, err, out = 0, [], []
    path = os.path.join(case_dir, 'expect')
    if os.path.exists(path):
        for line in open(path).read().splitlines():
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            if line.startswith('exit '):
                exit_code = int(line[5:])
            elif line.startswith('stderr '):
                err.append(line[7:])
            elif line.startswith('stdout '):
                out.append(line[7:])
            else:
                raise run.Fail(f"{path}: unknown expect line {line!r}")
    return exit_code, err, out


def case_includes(case_dir):
    """Extra -I directories for a case: one per line of its `includes` file."""
    out = []
    path = os.path.join(case_dir, 'includes')
    if os.path.exists(path):
        for line in open(path).read().splitlines():
            line = line.strip()
            if line and not line.startswith('#'):
                out.append(os.path.join(case_dir, line))
    return out


def compile_args(case_dir, exe=None):
    main = os.path.join(case_dir, 'main.obx')
    args = [OBO, main]
    if exe is not None:
        args += ['-o', exe]
    args += ['--rt', RT, '-I', case_dir]
    for d in case_includes(case_dir):
        args += ['-I', d]
    # every module build writes .sym files; a private sym-dir keeps tests/modules clean
    symdir = run.unique('module_sym_' + os.path.basename(case_dir))
    os.makedirs(symdir, exist_ok=True)
    args += ['--sym-dir', symdir]
    return args


def compile_root(case_dir, tag):
    main = os.path.join(case_dir, 'main.obx')
    exe = run.unique('module_' + tag + '_' + os.path.basename(case_dir))
    p = subprocess.run(compile_args(case_dir, exe), capture_output=True, text=True, timeout=120)
    return main, exe, p


def run_case(case_dir):
    main = os.path.join(case_dir, 'main.obx')
    src = open(main).read()
    expect, exit_code, stderr_subs = parse_run_comments(src)
    main, exe, p = compile_root(case_dir, 'run')
    if p.returncode != 0:
        raise run.Fail("did not compile: " + normalize(p.stderr, case_dir))
    code, out, err = run.run_exe(exe)
    problems = []
    if code != exit_code:
        problems.append(f"exit code {code}, expected {exit_code}" + (f"; stderr: {err.strip()}" if err else ""))
    for s in stderr_subs:
        if s not in err:
            problems.append(f"stderr lacks {s!r}; got {err.strip()!r}")
    got = out.splitlines()
    if got != expect:
        problems.append(run.first_difference(expect, got))
    if problems:
        raise run.Fail('\n'.join(problems))


def error_case(case_dir):
    exit_code, err, out = read_expect(case_dir)
    main, exe, p = compile_root(case_dir, 'err')
    got = normalize(p.stderr, case_dir)
    if p.returncode != exit_code:
        raise run.Fail(f"exit {p.returncode}, expected {exit_code}; stderr: {got}")
    want = '\n'.join(err)
    if got != want:
        raise run.Fail(f"stderr {got!r}, expected {want!r}")
    if out:
        raise run.Fail("an error case has stdout expectations")


def deps_case(case_dir):
    exit_code, err, out = read_expect(case_dir)
    main = os.path.join(case_dir, 'main.obx')
    args = [OBO, '--deps', main, '-I', case_dir]
    for d in case_includes(case_dir):
        args += ['-I', d]
    p = subprocess.run(args, capture_output=True, text=True, timeout=120)
    got_err = normalize(p.stderr, case_dir)
    got_out = [normalize(line, case_dir) for line in p.stdout.splitlines()]
    want_err = '\n'.join(err)
    problems = []
    if p.returncode != exit_code:
        problems.append(f"exit {p.returncode}, expected {exit_code}")
    if got_err != want_err:
        problems.append(f"stderr {got_err!r}, expected {want_err!r}")
    if got_out != out:
        problems.append(f"stdout {got_out!r}, expected {out!r}")
    if problems:
        raise run.Fail('; '.join(problems))


def deps_o_rejected():
    p = subprocess.run([OBO, '--deps', os.path.join(MOD, 'deps', 'order', 'main.obx'),
                        '-o', 'out'], capture_output=True, text=True, timeout=60)
    want = 'obxc: -o cannot be used with --check or --deps'
    if p.returncode != 2 or p.stderr.strip() != want:
        raise run.Fail(f"exit {p.returncode}: {p.stderr.strip()!r}, expected exit 2 and {want!r}")


def tokens_case():
    """The scanner's Stage 1 token: 67 is :: and 66 is still try."""
    p = subprocess.run([OBO, '--tokens', TOKENS], capture_output=True, text=True, timeout=60)
    want = ['1:1 ident a', '1:3 ::', '1:6 ident b', '1:8 try', '2:1 eof']
    if p.returncode != 0:
        raise run.Fail(f"exit {p.returncode}: {p.stderr.strip()!r}")
    got = p.stdout.splitlines()
    if got != want:
        raise run.Fail(run.first_difference(want, got))


def _dirs(kind):
    base = os.path.join(MOD, kind)
    if not os.path.isdir(base):
        return []
    return [(name, os.path.join(base, name)) for name in sorted(os.listdir(base))
            if os.path.isdir(os.path.join(base, name))]


def tests():
    out = []
    for name, d in _dirs('run'):
        out.append((f"modules/run/{name}", lambda d=d: run_case(d)))
    for name, d in _dirs('errors'):
        out.append((f"modules/errors/{name}", lambda d=d: error_case(d)))
    for name, d in _dirs('deps'):
        out.append((f"modules/deps/{name}", lambda d=d: deps_case(d)))
    out.append(("modules/deps/o_rejected", deps_o_rejected))
    out.append(("modules/tokens", tokens_case))
    return out


def main():
    pattern = sys.argv[sys.argv.index('-k') + 1] if '-k' in sys.argv else ''
    failed = 0
    todo = [(n, f) for n, f in tests() if pattern in n]
    for name, fn in todo:
        try:
            fn()
        except Exception as e:  # noqa: BLE001 - report the case, keep going
            failed += 1
            print(f"FAIL {name}\n     " + str(e).replace('\n', '\n     '))
        else:
            print(f"ok   {name}")
    print(f"\n{len(todo) - failed} passed, {failed} failed")
    sys.exit(1 if failed else 0)


if __name__ == '__main__':
    main()
