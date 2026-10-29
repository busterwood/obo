#!/usr/bin/env python3
"""P3 metadata tests: the runtime's direct contract and the fs module.

    python3 obo/tests/meta_test.py [--rt] [--programs]

Sections (both by default):
  rt        builds tests/rt_meta_test.c with runtime.c and runs every case in
            its own process: stat/lstat field values against raw newfstatat and
            hardcoded expectations (all 14 fields and their offsets), symlink
            follow/no-follow, ENOENT/EINVAL/ENAMETOOLONG, a directory's type, a
            pre-1970 mtime, readlink bytes/dangling/errors/a 4095-byte target,
            readDirFd's records and EOF, the max/cap/fd/table check order, a
            regular-file fd's ENOTDIR, the tracked-closed-fd exit 9 (and EBADF
            without OBO_CHECK), and the gated OBO_META_FAULT=eintr hook with and
            without OBO_CHECK. Every case frees its cells and ends in rt_finish,
            so under OBO_CHECK=1 it is also the leak oracle.
  programs  compiles tests/meta/*.obx as module roots with -I obo/lib and a
            private --sym-dir, then runs each against fixtures created here. The
            listing tests compare names/kinds/inodes with Python's os.listdir
            and os.lstat, the stat tests compare all 14 fields with os.stat and
            os.lstat, the readlink tests compare the raw target bytes with
            os.readlink, and the entry-path tests compare paths::join and lstat
            with os.path.join for an operand outside the cwd and for a "../"
            operand. Files with // expect: markers are compared in order (and
            // expect-exit: sets the code). The dedicated missing-close case must
            exit 8 under OBO_CHECK; normal cases must not.

The programs run with OBO_CHECK=1 and OBX_GC=poison, so a leak or a
use-after-free fails them. The compiler is $OBO (default obo/obo); all paths are
absolute, so the runner works from any cwd. Nothing is written outside the test
temp dir.

Exit status is 1 when any case fails; a failed compile or mismatch is reported
with the compiler's or program's own message.
"""
import atexit
import os
import re
import shutil
import socket
import stat
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
FORK = os.path.dirname(HERE)
ROOT = os.path.join(os.path.dirname(FORK), 'obx')
sys.path.insert(0, os.path.join(ROOT, 'tests'))
import run  # noqa: E402

run.OBXC = os.path.abspath(os.environ.get('OBO', os.path.join(FORK, 'obo')))
run.ROOT = FORK
LIBDIR = os.path.join(FORK, 'lib')
CASEDIR = os.path.join(HERE, 'meta')
TMP = tempfile.mkdtemp(prefix='obo-meta-test-')
atexit.register(shutil.rmtree, TMP, ignore_errors=True)

CFLAGS = ('-O2 -ffreestanding -fno-builtin -nostdlib -fno-pic -fno-pie '
          '-fno-stack-protector -fno-asynchronous-unwind-tables -mcmodel=small '
          '-fno-tree-loop-distribute-patterns -Wall').split()

failures = []
passed = 0


def clean_env(check=True, fault=None):
    e = {k: v for k, v in os.environ.items()
         if k not in ('OBO_CHECK', 'OBO_IO_FAULT', 'OBO_META_FAULT', 'OBX_GC')}
    if check:
        e['OBO_CHECK'] = '1'
    if fault:
        e['OBO_META_FAULT'] = fault
    e['OBX_GC'] = 'poison'
    return e


def ok(name):
    global passed
    passed += 1
    print(f"ok   {name}")


def bad(name, detail):
    failures.append(f"{name}: {detail}")


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


# ---------------------------------------------------------------- rt harness
RT_CASES = [
    # case, env, expected exit
    ('stat_file', clean_env(), 0),
    ('stat_link', clean_env(), 0),
    ('stat_dir', clean_env(), 0),
    ('stat_errors', clean_env(), 0),
    ('stat_pre1970', clean_env(), 0),
    ('stat_owner', clean_env(), 0),
    ('readlink_ok', clean_env(), 0),
    ('readlink_errors', clean_env(), 0),
    ('readlink_long', clean_env(), 0),
    ('readdir_ok', clean_env(), 0),
    ('readdir_empty', clean_env(), 0),
    ('readdir_checks', clean_env(), 0),
    ('readdir_closed', clean_env(), 9),
    ('readdir_closed', clean_env(check=False), 0),
    ('eintr', clean_env(fault='eintr'), 0),
    ('eintr', clean_env(check=False, fault='eintr'), 0),
]


def rt_section():
    obj = os.path.join(TMP, 'rt_meta_test.o')
    exe = os.path.join(TMP, 'rt_meta_test')
    subprocess.run(['gcc', *CFLAGS, '-c', os.path.join(HERE, 'rt_meta_test.c'), '-o', obj],
                   check=True, capture_output=True)
    subprocess.run(['ld', '-T', os.path.join(FORK, 'runtime', 'rt.ld'), '-o', exe, obj],
                   check=True, capture_output=True)
    for case, env, want in RT_CASES:
        scratch = tempfile.mkdtemp(prefix='rtmeta-', dir=TMP)
        p = subprocess.run([exe, case, scratch], capture_output=True, timeout=60, env=env)
        tag = case + ('' if env.get('OBO_CHECK') else ' (no check)')
        if p.returncode != want:
            bad(f"rt_meta_test {tag}",
                f"exit {p.returncode}, expected {want}; "
                f"stderr {p.stderr.decode('utf-8', 'replace').strip()[:200]!r}")
        else:
            ok(f"rt_meta_test {tag}")


# ---------------------------------------------------------------- fixtures
KIND_BY_IFMT = {
    stat.S_IFIFO: 1, stat.S_IFCHR: 2, stat.S_IFDIR: 4, stat.S_IFBLK: 6,
    stat.S_IFREG: 8, stat.S_IFLNK: 10, stat.S_IFSOCK: 12,
}


def kind_of(mode):
    return KIND_BY_IFMT.get(stat.S_IFMT(mode), 0)


class Fixture:
    pass


def make_fixtures():
    """One fixture tree per run: an entries directory with one of each type, a
    pinned stat file, symlinks (relative, dangling, 4095-byte target), and a cwd
    directory whose ../entries operand is relative."""
    fx = Fixture()
    base = tempfile.mkdtemp(prefix='meta-fix-', dir=TMP)
    fx.base = base
    fx.entries = os.path.join(base, 'entries')
    os.mkdir(fx.entries)
    os.mkdir(os.path.join(fx.entries, 'sub'))
    with open(os.path.join(fx.entries, 'sub', 'inner'), 'w') as f:
        f.write('z')
    with open(os.path.join(fx.entries, 'file.txt'), 'w') as f:
        f.write('abc')
    os.symlink('file.txt', os.path.join(fx.entries, 'link'))
    os.symlink('sub', os.path.join(fx.entries, 'dirlink'))
    os.mkfifo(os.path.join(fx.entries, 'fifo'))
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        s.bind(os.path.join(fx.entries, 'sock'))
    finally:
        s.close()
    fx.statfile = os.path.join(base, 'statfile')
    with open(fx.statfile, 'w') as f:
        f.write('hello')
    os.chmod(fx.statfile, 0o640)
    os.utime(fx.statfile, ns=(1000000000_111000000, 1234567890_123456789))
    with open(os.path.join(base, 'target'), 'w') as f:
        f.write('1234567')
    fx.rel = os.path.join(base, 'rel')
    os.symlink('target', fx.rel)
    fx.dangling = os.path.join(base, 'dangling')
    os.symlink('missing', fx.dangling)
    fx.longlink = os.path.join(base, 'longlink')
    fx.longtarget = 'a' * 4095
    os.symlink(fx.longtarget, fx.longlink)
    fx.regular = fx.statfile
    fx.missing = os.path.join(base, 'no-such')
    fx.cwd = os.path.join(base, 'cwd')
    os.mkdir(fx.cwd)
    fx.relative = '../entries'
    fx.sub = os.path.join(fx.entries, 'sub')
    return fx


# ---------------------------------------------------------------- programs
def compile_program(name):
    path = os.path.join(CASEDIR, name)
    if not os.path.isfile(path):
        raise SystemExit(f"tests/meta/{name} is missing")
    symdir = tempfile.mkdtemp(prefix='sym-', dir=TMP)
    exe = os.path.join(tempfile.mkdtemp(prefix='meta-', dir=TMP), name[:-4])
    p = subprocess.run([run.OBXC, path, '-o', exe, '--rt', FORK, '-I', LIBDIR,
                        '--sym-dir', symdir],
                       capture_output=True, text=True, timeout=120)
    if p.returncode != 0:
        raise SystemExit(f"tests/meta/{name} did not compile: {p.stderr.strip()[:400]}")
    return exe


def run_program(exe, args, cwd=None, check=True):
    scratch = tempfile.mkdtemp(prefix='scratch-', dir=TMP)
    p = subprocess.run([exe, scratch, *args], capture_output=True, timeout=120,
                       cwd=cwd, env=clean_env(check=check))
    return p


def check_markers(name, path, p, want_exit=0):
    expect, code = markers(path)
    code = want_exit if want_exit else code
    got = p.stdout.decode('utf-8', 'replace').splitlines()
    if p.returncode != code:
        bad(name, f"exit {p.returncode}, expected {code}; "
                  f"stderr {p.stderr.decode('utf-8', 'replace').strip()[:200]!r}")
        return False
    # a dedicated bad case carries // expect but deliberately fails; markers are
    # compared only where the source has some
    if expect and got != expect:
        bad(name, f"output {got!r}, expected {expect!r}")
        return False
    return True


def check_list(name, p, fx):
    """names, kinds and inodes against Python's os.listdir and os.lstat; the
    iterator must never yield . or .."""
    if p.returncode != 0:
        bad(name, f"exit {p.returncode}; stderr {p.stderr.decode('utf-8', 'replace').strip()[:200]!r}")
        return
    lines = p.stdout.decode('utf-8', 'replace').splitlines()
    if not lines or lines[-1] != 'end':
        bad(name, f"listing did not end with 'end': {lines[-6:]!r}")
        return
    got = []
    for line in lines[:-1]:
        parts = line.split('\t')
        if len(parts) != 3:
            bad(name, f"bad line {line!r}")
            return
        got.append((parts[0], int(parts[1]), int(parts[2])))
    want = []
    for n in os.listdir(fx.entries):
        st = os.lstat(os.path.join(fx.entries, n))
        want.append((n, kind_of(st.st_mode), st.st_ino))
    if sorted(got) != sorted(want):
        bad(name, f"listing {sorted(got)!r} != python {sorted(want)!r}")
        return
    if any(n in ('.', '..') for n, _, _ in got):
        bad(name, "fs::next yielded . or ..")
        return
    ok(name)


def stat_line(label, st):
    return f"{label} {st.st_mode} {st.st_size} {st.st_mtime_ns // 1_000_000_000} " \
           f"{st.st_mtime_ns % 1_000_000_000} {st.st_uid} {st.st_gid} {st.st_nlink} " \
           f"{st.st_ino} {st.st_dev} {st.st_rdev} {st.st_blksize} {st.st_blocks} " \
           f"{st.st_atime_ns // 1_000_000_000} {st.st_ctime_ns // 1_000_000_000}"


def check_stat_fields(name, p, paths):
    if p.returncode != 0:
        bad(name, f"exit {p.returncode}; stderr {p.stderr.decode('utf-8', 'replace').strip()[:200]!r}")
        return
    got = p.stdout.decode('utf-8', 'replace').splitlines()
    want = []
    for path in paths:
        want.append(stat_line('stat', os.stat(path)))
        want.append(stat_line('lstat', os.lstat(path)))
    if got != want:
        for i in range(max(len(got), len(want))):
            g = got[i] if i < len(got) else '<missing>'
            w = want[i] if i < len(want) else '<extra>'
            if g != w:
                bad(name, f"line {i + 1}: got {g!r}, want {w!r}")
                return
    ok(name)


def check_readlink(name, p, cases):
    """cases: [(path, expected), ...] where expected is bytes or an errno"""
    if p.returncode != 0:
        bad(name, f"exit {p.returncode}; stderr {p.stderr.decode('utf-8', 'replace').strip()[:200]!r}")
        return
    got = p.stdout.decode('latin-1').splitlines()
    want = []
    for path, exp in cases:
        if isinstance(exp, bytes):
            want.append('ok ' + exp.decode('latin-1'))
        else:
            want.append(f"err {exp}")
    if got != want:
        bad(name, f"output {got!r}, expected {want!r}")
        return
    ok(name)


def check_entry_paths(name, p, fx, operand, cwd=None):
    if p.returncode != 0:
        bad(name, f"exit {p.returncode}; stderr {p.stderr.decode('utf-8', 'replace').strip()[:200]!r}")
        return
    lines = p.stdout.decode('utf-8', 'replace').splitlines()
    if not lines or lines[-1] != 'end':
        bad(name, f"listing did not end with 'end': {lines[-6:]!r}")
        return
    got = []
    for line in lines[:-1]:
        parts = line.split('\t')
        if len(parts) != 3:
            bad(name, f"bad line {line!r}")
            return
        got.append((parts[0], parts[1], int(parts[2])))
    want = []
    for n in os.listdir(fx.entries):
        joined = os.path.join(operand, n)
        st = os.lstat(os.path.join(cwd, joined) if cwd else joined)
        want.append((n, joined, kind_of(st.st_mode)))
    if sorted(got) != sorted(want):
        bad(name, f"entries {sorted(got)!r} != python {sorted(want)!r}")
        return
    ok(name)


def program_section():
    fx = make_fixtures()
    exes = {name: compile_program(name) for name in sorted(os.listdir(CASEDIR))
            if name.endswith('.obx')}

    def exe(name):
        return exes[name]

    # listing: names, kinds and inodes against Python; no . or ..
    p = run_program(exe('list.obx'), [fx.entries])
    check_list('fs/list', p, fx)

    # stat/lstat: all 14 fields against os.stat/os.lstat, for a pinned regular
    # file, a symlink (follow and no-follow) and a directory
    p = run_program(exe('stat_fields.obx'), [fx.statfile, fx.rel, fx.sub])
    check_stat_fields('fs/stat_fields', p, [fx.statfile, fx.rel, fx.sub])

    # readlink: raw target bytes, a dangling target, and the error codes
    cases = [(fx.rel, b'target'), (fx.dangling, b'missing'),
             (fx.longlink, fx.longtarget.encode()),
             (fx.regular, 22), (fx.missing, 2), ('', 22)]
    p = run_program(exe('readlink.obx'), [c[0] for c in cases])
    check_readlink('fs/readlink', p, cases)

    # entry paths: an operand outside the cwd, and a ../ operand from a sibling
    p = run_program(exe('entry_paths.obx'), [fx.entries])
    check_entry_paths('fs/entry_paths absolute', p, fx, fx.entries)
    p = run_program(exe('entry_paths.obx'), [fx.relative], cwd=fx.cwd)
    check_entry_paths('fs/entry_paths ../', p, fx, fx.relative, cwd=fx.cwd)

    # close idempotence (the fd is >= 0 once, then -1); no leak under OBO_CHECK
    p = run_program(exe('close_idempotent.obx'), [fx.entries])
    if check_markers('fs/close_idempotent', os.path.join(CASEDIR, 'close_idempotent.obx'), p):
        ok('fs/close_idempotent')

    # open and close a directory under OBO_CHECK with no leak
    p = run_program(exe('open_close.obx'), [fx.entries])
    if check_markers('fs/open_close', os.path.join(CASEDIR, 'open_close.obx'), p):
        ok('fs/open_close')

    # fs-level errors: missing paths, a non-symlink readlink, a file as a dir
    p = run_program(exe('err_paths.obx'), [fx.regular, fx.missing, fx.entries])
    if check_markers('fs/err_paths', os.path.join(CASEDIR, 'err_paths.obx'), p):
        ok('fs/err_paths')

    # crafted records: the DT_UNKNOWN lazy-lstat fallback, dot skipping and the
    # corrupt-record branches (reclen, bounds, missing NUL) with no advance
    p = run_program(exe('crafted.obx'), [fx.entries])
    if check_markers('fs/crafted', os.path.join(CASEDIR, 'crafted.obx'), p):
        ok('fs/crafted')

    # the dedicated bad case: an unclosed directory exits 8 under OBO_CHECK
    p = run_program(exe('missing_close.obx'), [fx.entries])
    want = os.path.join(CASEDIR, 'missing_close.obx')
    expect, _ = markers(want)
    got = p.stdout.decode('utf-8', 'replace').splitlines()
    if p.returncode != 8:
        bad('fs/missing_close', f"exit {p.returncode}, expected 8 (the leak trap)")
    elif b'file descriptors leaked: 1' not in p.stderr:
        bad('fs/missing_close', f"stderr lacks the leak report: {p.stderr[:200]!r}")
    elif expect and got != expect:
        bad('fs/missing_close', f"output {got!r}, expected {expect!r}")
    else:
        ok('fs/missing_close')


def main():
    args = sys.argv[1:]
    unknown = [a for a in args if a not in ('--rt', '--programs')]
    if unknown:
        raise SystemExit(f"unknown option {unknown[0]!r}")
    which = [a for a in ('--rt', '--programs') if a in args] or ['--rt', '--programs']
    if not os.path.exists(run.OBXC):
        print(f"no compiler at {run.OBXC}: run make first")
        sys.exit(2)
    if '--rt' in which:
        rt_section()
    if '--programs' in which:
        program_section()
    for f in failures:
        print('FAIL', f)
    print(f"\n{passed} metadata checks passed, {len(failures)} failures")
    sys.exit(1 if failures else 0)


if __name__ == '__main__':
    main()
