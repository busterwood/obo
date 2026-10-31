#!/usr/bin/env python3
"""Incremental-I/O tests that need controlled stdin, environment or inherited fds.

    python3 obo/tests/io_test.py [--rt] [--programs] [--tools]

Sections (all by default):
  rt        builds tests/rt_io_test.c with runtime.c and runs every case in its
            own process: the Result cell layout and tags, error codes and the
            N1/N2 precedence, close-state marking (an EINTR or a failed close
            still marks the table), "w" truncation, the unknown-mode error,
            exits 0/8/9, the gated OBO_IO_FAULT hooks with and without
            OBO_CHECK, sleepMs timing/bounds/EINTR, and no leak on the normal
            paths.
  programs  compiles tests/io/*.obx (standalone) and tests/lib/*.obx (as module
            roots importing lib modules such as io.obx and fmt.obx, with
            -I obo/lib and a private sym dir) and
            runs each with its stdin, env and extra arguments; the // expect: and
            // expect-exit: markers are checked as in run_fork.py.
  tools     builds cmd/{cat,wc}.obx with tools/build_tools.py (module
            roots; nothing is concatenated), checks cat streams a pipe before
            EOF, a 100 KiB binary round trip, wc's line/word/byte counts, and
            both on a file. The full tool semantics live in tests/tools.py.

The compiler is $OBO (default obo/obo); --rt points every program at this tree.
"""
import atexit
import os
import re
import select
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
FORK = os.path.dirname(HERE)
ROOT = os.path.join(os.path.dirname(FORK), 'obx')
sys.path.insert(0, os.path.join(ROOT, 'tests'))
import run  # noqa: E402

sys.path.insert(0, os.path.join(FORK, 'tools'))
import build_tools  # noqa: E402

run.OBXC = os.environ.get('OBO', os.path.join(FORK, 'obo'))
run.ROOT = FORK
LIBDIR = os.path.join(FORK, 'lib')
TMP = tempfile.mkdtemp(prefix='obo-io-test-')
atexit.register(shutil.rmtree, TMP, ignore_errors=True)

CFLAGS = ('-O2 -ffreestanding -fno-builtin -nostdlib -fno-pic -fno-pie '
          '-fno-stack-protector -fno-asynchronous-unwind-tables -mcmodel=small '
          '-fno-tree-loop-distribute-patterns -Wall').split()

failures = []


def clean_env(check=False, fault=None, meta_fault=None):
    """The runner's environment without the I/O variables, then the requested ones."""
    e = {k: v for k, v in os.environ.items()
         if k not in ('OBO_CHECK', 'OBO_IO_FAULT', 'OBO_META_FAULT', 'OBX_GC')}
    if check:
        e['OBO_CHECK'] = '1'
    if fault:
        e['OBO_IO_FAULT'] = fault
    if meta_fault:
        e['OBO_META_FAULT'] = meta_fault
    e['OBX_GC'] = 'poison'
    return e


def markers(path):
    expect, code = [], 0
    for line in open(path).read().splitlines():
        m = re.search(r'// expect: ?(.*)$', line)
        if m:
            expect.append(m.group(1))
        m = re.search(r'// expect-exit: *(-?\d+)', line)
        if m:
            code = int(m.group(1))
    return expect, code


def check_run(name, p, expect, code):
    got = p.stdout.decode('utf-8', 'replace').splitlines()
    if p.returncode != code:
        failures.append(f"{name}: exit {p.returncode}, expected {code}; "
                        f"stderr {p.stderr.decode('utf-8', 'replace').strip()[:200]!r}")
    elif got != expect:
        failures.append(f"{name}: output {got!r}, expected {expect!r}")
    else:
        print(f"ok   {name}")


def compile_text(name, text):
    return run.compile_with(run.OBXC, text, 'io_' + name)


def case_io(name, env, stdin=b'', args=(), pass_fds=()):
    path = os.path.join(HERE, 'io', name)
    text = open(path).read()
    exe = compile_text(name, text)
    scratch = tempfile.mkdtemp(prefix='scratch-', dir=TMP)
    expect, code = markers(path)
    p = subprocess.run([exe, scratch, *args], input=stdin, capture_output=True,
                       timeout=120, env=env, pass_fds=pass_fds)
    check_run(name, p, expect, code)


def case_lib(name, env):
    path = os.path.join(HERE, 'lib', name)
    text = open(path).read()
    symdir = tempfile.mkdtemp(prefix='sym-', dir=TMP)
    exe = run.compile_with(run.OBXC, text, 'lib_' + name,
                           extra_args=['-I', LIBDIR, '--sym-dir', symdir])
    scratch = tempfile.mkdtemp(prefix='scratch-', dir=TMP)
    expect, code = markers(path)
    p = subprocess.run([exe, scratch], capture_output=True, timeout=120, env=env)
    check_run('lib/' + name, p, expect, code)


# ---------------------------------------------------------------- rt harness
RT_CASES = [
    # case, env, expected exit
    ('layout', clean_env(check=True), 0),
    ('read_eof', clean_env(check=True), 0),
    ('max_zero', clean_env(check=True), 0),
    ('neg_big', clean_env(check=True), 0),
    ('std_close', clean_env(check=True), 0),
    ('write_read', clean_env(check=True), 0),
    ('leak', clean_env(check=True), 8),
    ('leak', clean_env(), 0),
    ('double_close', clean_env(check=True), 9),
    ('double_close', clean_env(), 0),
    ('empty_write_closed', clean_env(check=True), 9),
    ('empty_write_closed', clean_env(), 0),
    ('closed_big_read', clean_env(check=True), 9),
    ('closed_big_read', clean_env(), 0),
    ('close_ebadf', clean_env(check=True), 0),
    ('close_ebadf', clean_env(), 0),
    ('trunc', clean_env(check=True), 0),
    ('unknown_mode', clean_env(check=True), 0),
    ('eintr', clean_env(check=True, fault='eintr'), 0),
    ('short', clean_env(check=True, fault='short'), 0),
    ('inert', clean_env(fault='short'), 0),
    ('sleep', clean_env(check=True), 0),
    ('sleep_eintr', clean_env(check=True, meta_fault='eintr'), 0),
    ('empty_arrays', clean_env(check=True), 0),
    ('static_free', clean_env(check=True), 6),
    ('unknown_kind', clean_env(check=True), 2),
]


def rt_section():
    obj = os.path.join(TMP, 'rt_io_test.o')
    exe = os.path.join(TMP, 'rt_io_test')
    subprocess.run(['gcc', *CFLAGS, '-c', os.path.join(HERE, 'rt_io_test.c'), '-o', obj],
                   check=True, capture_output=True)
    subprocess.run(['ld', '-T', os.path.join(FORK, 'runtime', 'rt.ld'), '-o', exe, obj],
                   check=True, capture_output=True)
    for case, env, want in RT_CASES:
        scratch = tempfile.mkdtemp(prefix='rtio-', dir=TMP)
        path = os.path.join(scratch, 'f')
        p = subprocess.run([exe, case, path], capture_output=True, timeout=60, env=env)
        tag = case + ('' if env.get('OBO_CHECK') else ' (no check)')
        if p.returncode != want:
            failures.append(f"rt_io_test {tag}: exit {p.returncode}, expected {want}; "
                            f"stderr {p.stderr.decode('utf-8', 'replace').strip()[:200]!r}")
        else:
            print(f"ok   rt_io_test {tag}")


# ---------------------------------------------------------------- programs
def program_section():
    check = clean_env(check=True)
    case_io('short_read.obx', check, stdin=b'0123456789')
    case_io('fault_eintr.obx', clean_env(check=True, fault='eintr'))
    case_io('fault_inert.obx', clean_env(fault='short'))
    case_io('no_check_double_close.obx', clean_env())
    case_io('no_check_after_close.obx', clean_env())
    case_io('no_check_empty_write.obx', clean_env())
    case_io('no_check_big_read.obx', clean_env())
    case_io('close_stdout.obx', check)
    case_io('arg_errors.obx', check)
    case_io('path_errors.obx', check)
    case_lib('boundaries.obx', check)
    case_lib('stream.obx', check)
    case_lib('short_write.obx', clean_env(check=True, fault='short'))
    case_lib('short_write.obx', check)
    case_lib('write_padded.obx', check)
    case_lib('write_byte.obx', check)
    case_lib('write_byte.obx', clean_env(check=True, fault='short'))

    # inherited fd: Python opens the file (the number must be >= 3, or the test
    # would exercise the standard-stream refusal instead of the untracked close)
    fdpath = os.path.join(TMP, 'inherited.txt')
    with open(fdpath, 'w') as f:
        f.write('inherited')
    fd = os.open(fdpath, os.O_RDONLY)
    try:
        if fd < 3:
            failures.append(f"inherited fd is {fd}, expected >= 3")
            return
        case_io('inherited_fd.obx', clean_env(check=True), args=(str(fd),), pass_fds=(fd,))
    finally:
        os.close(fd)


# ---------------------------------------------------------------- tools
def build_tool(name):
    out = tempfile.mkdtemp(prefix='tool-', dir=TMP)
    return build_tools.build([name], out, run.OBXC, run.ROOT)[0]


def run_tool_file(exe, data, timeout=120):
    return subprocess.run([exe], input=data, capture_output=True, timeout=timeout,
                          env=clean_env(check=True))


def tool_section():
    cat = build_tool('cat')
    wc = build_tool('wc')

    # streaming: ten bytes through a pipe while stdin stays open
    p = subprocess.Popen([cat], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                         stderr=subprocess.PIPE, env=clean_env(check=True))
    try:
        p.stdin.write(b'0123456789')
        p.stdin.flush()
        got = b''
        deadline = 5.0
        while len(got) < 10:
            ready, _, _ = select.select([p.stdout], [], [], deadline)
            if not ready:
                failures.append('tools/cat: the first 10 bytes did not stream before EOF')
                break
            chunk = os.read(p.stdout.fileno(), 10 - len(got))
            if not chunk:
                failures.append('tools/cat: stdout closed early')
                break
            got += chunk
        if got == b'0123456789':
            print('ok   tools/cat streams a pipe')
    finally:
        p.stdin.close()
        p.wait(timeout=30)
    if p.returncode != 0:
        failures.append(f"tools/cat: exit {p.returncode}: {p.stderr.read()[:200]!r}")

    # a 100 KiB binary round trip, NULs included
    data = bytes(range(256)) * 400 + b'\x00tail\n'
    r = run_tool_file(cat, data)
    if r.returncode != 0 or r.stdout != data:
        failures.append(f"tools/cat: 100 KiB binary differs (exit {r.returncode})")
    else:
        print('ok   tools/cat 100 KiB binary')

    # wc: text, empty and binary (stdin is a pipe, so padding uses GNU's 7 floor)
    for data, want in [(b'one\ntwo\nthree\n', '      3       3      14'),
                       (b'', '      0       0       0'),
                       (bytes(range(256)) * 391, '    391     391  100096')]:
        r = run_tool_file(wc, data)
        if r.returncode != 0 or r.stdout.decode() != want + '\n':
            failures.append(f"tools/wc: {r.stdout!r}, expected {want!r} (exit {r.returncode})")
        else:
            print(f"ok   tools/wc {want}")

    # both on a file, not a pipe
    path = os.path.join(TMP, 'tools.in')
    with open(path, 'wb') as f:
        f.write(b'alpha\nbeta\n')
    with open(path, 'rb') as f:
        r = subprocess.run([cat], stdin=f, capture_output=True, timeout=120, env=clean_env(check=True))
    if r.returncode != 0 or r.stdout != b'alpha\nbeta\n':
        failures.append('tools/cat: file input differs')
    else:
        print('ok   tools/cat file')
    with open(path, 'rb') as f:
        r = subprocess.run([wc], stdin=f, capture_output=True, timeout=120, env=clean_env(check=True))
    # a regular-file stdin is still unknown to the tool, so width 7
    want = '      2       2      11'
    if r.returncode != 0 or r.stdout.decode() != want + '\n':
        failures.append(f"tools/wc file: {r.stdout!r}, expected {want!r}")
    else:
        print('ok   tools/wc file')


def main():
    args = sys.argv[1:]
    unknown = [a for a in args if a not in ('--rt', '--programs', '--tools')]
    if unknown:
        raise SystemExit(f"unknown option {unknown[0]!r}")
    which = [a for a in ('--rt', '--programs', '--tools') if a in args] or \
            ['--rt', '--programs', '--tools']
    if '--rt' in which:
        rt_section()
    if '--programs' in which:
        program_section()
    if '--tools' in which:
        tool_section()
    for f in failures:
        print('FAIL', f)
    print(f"\n{len(failures)} failures")
    sys.exit(1 if failures else 0)


if __name__ == '__main__':
    main()
