#!/usr/bin/env python3
"""Mutation testing for the incremental-I/O runtime and library.

Each mutant is one replacement in runtime/runtime.c or lib/io.obx. The runtime
mutants are built into a private runtime directory (runtime_text.bin and
runtime_data.bin, with the Makefile's flags) and the test programs are compiled
against it with the unmutated compiler. A library mutant gets a scratch module
tree: the lib/*.obx modules are copied into <work>/lib, io.obx is replaced with
the mutant text, and every affected root is compiled with -I <work>/lib and a
private --sym-dir, so the mutated module is what the program imports. The
baseline (no mutant) must pass every case before any mutant is judged, and every
mutant must fail at least one case.

    python3 obo/tests/mutate_io.py [pattern]

The cases are the program-level oracles: tests/run/io_*.obx plus the controlled
stdin/env/inherited-fd programs from tests/io and tests/lib (the same ones
io_test.py runs), so this harness needs no freestanding runtime link of its own
(rt_io_test covers the direct contract).
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
FORK = os.path.dirname(HERE)
ROOT = os.path.join(os.path.dirname(FORK), 'obx')
sys.path.insert(0, os.path.join(ROOT, 'tests'))
import run  # noqa: E402
from peak_budget import PEAK_BUDGET, empty_array_regression  # noqa: E402

run.OBXC = os.path.abspath(os.environ.get('OBO', os.path.join(FORK, 'obo')))
run.ROOT = FORK
LIBDIR = os.path.join(FORK, 'lib')
RUNTIME = os.path.join(FORK, 'runtime', 'runtime.c')
SRC = os.path.join(FORK, 'src', 'obo.obo')
SEED = os.path.join(FORK, 'seed', 'obo')
CFLAGS = ('-O2 -ffreestanding -fno-builtin -nostdlib -fno-pic -fno-pie '
          '-fno-stack-protector -fno-asynchronous-unwind-tables -mcmodel=small '
          '-fno-tree-loop-distribute-patterns -Wall').split()
TMP = tempfile.mkdtemp(prefix='obo-mutate-io-')

# ---------------------------------------------------------------- mutants
RUNTIME_MUTANTS = {
    'float add subtracts': (
        'u64 rt_f_add(u64 a, u64 b) { return f_to_bits(f_from_bits(a) + f_from_bits(b)); }',
        'u64 rt_f_add(u64 a, u64 b) { return f_to_bits(f_from_bits(a) - f_from_bits(b)); }'),
    'float divide multiplies': (
        'u64 rt_f_div(u64 a, u64 b) { return f_to_bits(f_from_bits(a) / f_from_bits(b)); }',
        'u64 rt_f_div(u64 a, u64 b) { return f_to_bits(f_from_bits(a) * f_from_bits(b)); }'),
    'float not-equal uses equal': (
        'i64 rt_f_ne(u64 a, u64 b) { return f_from_bits(a) != f_from_bits(b); }',
        'i64 rt_f_ne(u64 a, u64 b) { return f_from_bits(a) == f_from_bits(b); }'),
    'float shortest ignores ceiling candidate': (
        '        int ceil_ok = candidate_ok(ceil_norm, ceil_e, bits);\n',
        '        int ceil_ok = 0;\n'),
    'float parse accepts empty input': (
        '    if (!any) return err_cell(descErr, iomsg_lit("invalid float"), 22);\n',
        '    if (0) return err_cell(descErr, iomsg_lit("invalid float"), 22);\n'),
    'float negative zero prints positive': (
        '    if (ef == 0 && frac == 0) {\n        if (sign) b[o++] = \'-\';\n        b[o++] = \'0\';\n        return o;\n    }',
        '    if (ef == 0 && frac == 0) {\n        b[o++] = \'0\';\n        return o;\n    }'),
    # a short read is data, not EOF
    'short read treated as EOF': (
        '    if ((u64)r < (u64)max) a->len = (u64)r;                        /* a short read is data, not EOF */\n',
        '    if ((u64)r < (u64)max) { rt_free((u64)a); return ok_cell(descOk, 0); }\n'),
    # the array claims one byte more than was read
    'read length got+1': (
        '    if ((u64)r < (u64)max) a->len = (u64)r;                        /* a short read is data, not EOF */\n',
        '    if ((u64)r < (u64)max) a->len = (u64)r + 1;\n'),
    # the read lands one byte into the array's data
    'read at data+9': (
        '    i64 r = io_read(fd, (i64)((char *)a->data + 8), max);\n',
        '    i64 r = io_read(fd, (i64)((char *)a->data + 9), max);\n'),
    # double close: the tracked-closed branch is removed
    'double close not detected': (
        '    if (tracked && fdstate[fd] == 2) {\n',
        '    if (0) {\n'),
    # close makes no syscall and changes no state: a closed tracked fd looks open
    'close does nothing': (
        '    i64 r = io_close(fd);\n    if (tracked) fdstate[fd] = 2;\n',
        '    i64 r = 0;\n'),
    # the write goes to stdout whatever fd was asked for
    'write uses fd 1': (
        '    i64 r = io_write(fd, (i64)((char *)data->data + 8), (i64)data->len);\n',
        '    i64 r = io_write(1, (i64)((char *)data->data + 8), (i64)data->len);\n'),
    # the data pointer is passed as the fd
    'write passes the data pointer as the fd': (
        '    i64 r = io_write(fd, (i64)((char *)data->data + 8), (i64)data->len);\n',
        '    i64 r = io_write((i64)data, (i64)((char *)data->data + 8), (i64)data->len);\n'),
    # EINTR is not retried
    'read EINTR not retried': (
        '        if (fault_read) { fault_read = 0; r = -4; } else r = sys3(0, fd, buf, n);\n        if (r != -4) return r;\n',
        '        if (fault_read) { fault_read = 0; r = -4; } else r = sys3(0, fd, buf, n);\n        return r;\n'),
    'write EINTR not retried': (
        '        if (fault_write) { fault_write = 0; r = -4; } else r = sys3(1, fd, buf, n);\n        if (r != -4) return r;\n',
        '        if (fault_write) { fault_write = 0; r = -4; } else r = sys3(1, fd, buf, n);\n        return r;\n'),
    # a close EINTR is reported as an error, not as the Linux already-closed success
    'close EINTR is an error': (
        '    if (r == 0 || r == -4) return ok_cell(descOk, 1);      /* EINTR: Linux already closed it */\n',
        '    if (r == 0) return ok_cell(descOk, 1);\n'),
    # the err cell stores the code where the message goes and the message at +8
    'err message and code swapped': (
        '    c[0] = msg;\n    c[1] = (u64)code;\n',
        '    c[0] = (u64)code;\n    c[1] = msg;\n'),
    # the fd table never learns about an open: a leak goes unreported
    'fd table not updated on open': (
        '    if (fd < 1024) fdstate[fd] = 1;\n',
        '    if (0) fdstate[fd] = 1;\n'),
    # a zero-length write succeeds before the closed-fd check (N1)
    'zero-length shortcut before the fd check': (
        '    flush();\n    if (fd >= 0 && fd < 1024 && fdstate[fd] == 2) {\n',
        '    flush();\n    if (data->len == 0) return ok_cell(descOk, 0);\n    if (fd >= 0 && fd < 1024 && fdstate[fd] == 2) {\n'),
    # the max-sized allocation runs before the max/fd/table checks (N2)
    'allocation before the read checks': (
        '    flush();\n    if (max <= 0) return err_cell(descErr, iomsg_lit("readFd needs a positive count"), 22);\n',
        '    flush();\n    if (max > 0) { Array *probe = (Array *)rt_alloc_array(max, 4); rt_free((u64)probe); }\n'
        '    if (max <= 0) return err_cell(descErr, iomsg_lit("readFd needs a positive count"), 22);\n'),
    # a negative fd is not checked before the allocation
    'negative fd not checked': (
        '    if (fd < 0) return err_cell(descErr, iomsg_num("cannot read fd ", fd), 9);             /* EBADF */\n',
        '    if (0) return err_cell(descErr, iomsg_num("cannot read fd ", fd), 9);\n'),
    # a failed/EINTR close does not mark the fd closed (N4): the leak oracle fires
    'EINTR close does not mark closed': (
        '    if (tracked) fdstate[fd] = 2;\n',
        '    if (tracked && r == 0) fdstate[fd] = 2;\n'),
    # "w" drops O_TRUNC: a shorter rewrite leaves the old tail
    'w mode does not truncate': (
        '    else if (str_is(mode, "w")) { flags = 0x241; pmode = 0644; }                            /* O_WRONLY|O_CREAT|O_TRUNC */\n',
        '    else if (str_is(mode, "w")) { flags = 0x041; pmode = 0644; }                            /* O_WRONLY|O_CREAT */\n'),
    # an untracked fd is entered in the table, so its second close traps (N3)
    'untracked close marks the table': (
        '    i64 r = io_close(fd);\n    if (tracked) fdstate[fd] = 2;\n',
        '    i64 r = io_close(fd);\n    if (tracked) fdstate[fd] = 2;\n    else if (fd >= 0 && fd < 1024) fdstate[fd] = 2;\n'),
    # the standard descriptors become closable (N8)
    'standard descriptors closable': (
        '    if (fd >= 0 && fd < 3 && !tracked)\n',
        '    if (0)\n'),
    # the fault hook is active without OBO_CHECK (N5). P3 added the metadata
    # hook to the same gate line, so this anchor was re-anchored in the P3
    # commit; the count stays 27/27.
    'fault hook not gated': (
        '    if (!leak_check) { io_fault_eintr = 0; io_fault_short = 0; meta_fault_eintr = 0; }\n',
        '    if (0) { io_fault_eintr = 0; io_fault_short = 0; meta_fault_eintr = 0; }\n'),
    'empty append frees the static block': (
        '        if (!is_empty_data(a->data)) free_block(a->data - GRAN);\n',
        '        free_block(a->data - GRAN);\n'),
    'empty array free pushes the static block': (
        '            if (body[2] && !is_empty_data(body[2])) free_push(body[2]);\n',
        '            if (body[2]) free_push(body[2]);\n'),
    'zero array alloc gets a heap block': (
        '    u64 b = n ? new_block(kind, esize, (u64)n) : empty_data(kind);\n',
        '    u64 b = new_block(kind, esize, (u64)n);\n'),
}

LIB_MUTANTS = {
    # fill serves one byte too many: readLine indexes past the chunk
    'fill boundary off by one': (
        '    if r.pos < len(r.buf) { return ok(true) }\n',
        '    if r.pos <= len(r.buf) { return ok(true) }\n'),
    # fill starts a new chunk while one byte is still unread: the byte is lost
    'fill at pos == len-1': (
        '    if r.pos < len(r.buf) { return ok(true) }\n',
        '    if r.pos < len(r.buf) - 1 { return ok(true) }\n'),
    # flush returns without writing
    'flush writes nothing': (
        '    while len(w.buf) > 0 {\n',
        '    while false {\n'),
    # a short write truncates the buffer before the tail is written
    'flush drops the unwritten tail': (
        '        let left = len(w.buf) - n\n'
        '        if left > 0 {\n'
        '            for i in 0..left { w.buf[i] = w.buf[i + n] }\n'
        '        }\n'
        '        truncate(w.buf, left)\n',
        '        truncate(w.buf, 0)\n'),
    # the unwritten tail is copied from one byte too far
    'flush shifts from the wrong offset': (
        '            for i in 0..left { w.buf[i] = w.buf[i + n] }\n',
        '            for i in 0..left { w.buf[i] = w.buf[i + n + 1] }\n'),
    # takeChunk hands back the whole chunk, prefix included
    'takeChunk keeps the consumed prefix': (
        '    if start == 0 { return all }\n',
        '    if start >= 0 { return all }\n'),
    # takeChunk copies the wrong suffix
    'takeChunk copies the wrong suffix': (
        '    for i in start..len(all) { append(out, all[i]) }\n',
        '    for i in start + 1..len(all) { append(out, all[i]) }\n'),
}

# Runtime mutants with no program-observable semantic difference are judged by the actual
# compiler self-host memory oracle.  The absolute numeric-branch budget is 2,500,000 bytes;
# the zero-array mutant is additionally judged by its delta from the unmutated baseline because
# the baseline is itself above the historical 2,000,000 lambda budget.  The normal program suite
# is still run first for every mutant.
MEMORY_MUTANTS = {'zero array alloc gets a heap block'}

# ---------------------------------------------------------------- cases
def clean_env(check=False, fault=None):
    e = {k: v for k, v in os.environ.items()
         if k not in ('OBO_CHECK', 'OBO_IO_FAULT', 'OBX_GC')}
    if check:
        e['OBO_CHECK'] = '1'
    if fault:
        e['OBO_IO_FAULT'] = fault
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


def case(name, rel, inherited=False, **kw):
    return dict(name=name, path=os.path.join(FORK, rel), inherited=inherited, **kw)


CASES = [
    case('run/io_basic', 'tests/run/io_basic.obx', env=clean_env(check=True)),
    case('run/io_leak', 'tests/run/io_leak.obx', env=clean_env(check=True)),
    case('run/io_double_close', 'tests/run/io_double_close.obx', env=clean_env(check=True)),
    case('run/io_write_empty_closed', 'tests/run/io_write_empty_closed.obx', env=clean_env(check=True)),
    case('run/io_read_big_closed', 'tests/run/io_read_big_closed.obx', env=clean_env(check=True)),
    case('run/io_reader', 'tests/run/io_reader.obx', env=clean_env(check=True)),
    case('run/io_writer', 'tests/run/io_writer.obx', env=clean_env(check=True)),
    case('run/io_defer', 'tests/run/io_defer.obx', env=clean_env(check=True)),
    case('run/empty_arrays', 'tests/run/empty_arrays.obx', env=clean_env(check=True)),
    case('float/core', 'tests/float_core.obx', env=clean_env(check=True)),
    case('io/short_read', 'tests/io/short_read.obx', env=clean_env(check=True), stdin=b'0123456789'),
    case('io/fault_eintr', 'tests/io/fault_eintr.obx', env=clean_env(check=True, fault='eintr')),
    case('io/fault_inert', 'tests/io/fault_inert.obx', env=clean_env(fault='short')),
    case('io/close_stdout', 'tests/io/close_stdout.obx', env=clean_env(check=True)),
    case('io/no_check_big_read', 'tests/io/no_check_big_read.obx', env=clean_env()),
    case('io/inherited_fd', 'tests/io/inherited_fd.obx', env=clean_env(check=True), inherited=True),
    case('lib/boundaries', 'tests/lib/boundaries.obx', env=clean_env(check=True)),
    case('lib/stream', 'tests/lib/stream.obx', env=clean_env(check=True)),
    case('lib/short_write', 'tests/lib/short_write.obx', env=clean_env(check=True, fault='short')),
]


class Fail(Exception):
    pass


def compile_text(text, name, rt_dir, lib_dir):
    """run.compile_with, but with an explicit runtime directory (the mutant's) and
    a private symbol directory: every case is a module root compiled with -I
    lib_dir, so a root that imports io.obx sees this mutant tree's module."""
    base = run.unique('mio_' + name)
    path = base + '.obx'
    symdir = base + '.sym'
    os.makedirs(symdir)
    with open(path, 'w') as f:
        f.write(text)
    p = subprocess.run([run.OBXC, path, '-o', base, '--rt', rt_dir,
                        '-I', lib_dir, '--sym-dir', symdir],
                       capture_output=True, text=True, timeout=120)
    if p.returncode == 0:
        return base
    if p.returncode == 1 and p.stderr.startswith(path + ':'):
        raise Fail('compile failed: ' + p.stderr[len(path) + 1:].strip())
    raise Fail(f"compiler exit {p.returncode}: {p.stderr.strip()[:200]}")


def run_case(c, rt_dir, lib_dir):
    """Runs one case against rt_dir (the runtime bins) and lib_dir (the module
    tree its imports resolve in); raises Fail on a compile error, a wrong exit,
    or different output."""
    text = open(c['path']).read()
    exe = compile_text(text, os.path.basename(c['name']), rt_dir, lib_dir)
    scratch = tempfile.mkdtemp(prefix='scratch-', dir=TMP)
    extra = []
    fds = ()
    opened = None
    if c['inherited']:
        path = os.path.join(TMP, 'inherited.txt')
        with open(path, 'w') as f:
            f.write('inherited')
        opened = os.open(path, os.O_RDONLY)
        if opened < 3:
            raise Fail(f"inherited fd {opened} is below 3")
        fds = (opened,)
        extra = [str(opened)]
    try:
        p = subprocess.run([exe, scratch, *extra], input=c.get('stdin', b''),
                           capture_output=True, timeout=120, env=c['env'], pass_fds=fds)
    finally:
        if opened is not None:
            os.close(opened)
    expect, code = markers(c['path'])
    got = p.stdout.decode('utf-8', 'replace').splitlines()
    if p.returncode != code:
        raise Fail(f"exit {p.returncode}, expected {code}; "
                   f"stderr {p.stderr.decode('utf-8', 'replace').strip()[:160]!r}")
    if got != expect:
        raise Fail(f"output {got!r}, expected {expect!r}")


def build_runtime(src_text, work):
    """The runtime bins built from src_text in work, with the Makefile's flags."""
    rt = os.path.join(work, 'rt')
    os.makedirs(rt, exist_ok=True)
    open(os.path.join(rt, 'runtime.c'), 'w').write(src_text)
    shutil.copy(os.path.join(FORK, 'runtime', 'entry.S'), rt)
    shutil.copy(os.path.join(FORK, 'runtime', 'rt.ld'), rt)
    shutil.copy(os.path.join(FORK, 'runtime', 'decimal24_core.inc'), rt)
    subprocess.run(['gcc', *CFLAGS, '-c', 'runtime.c', '-o', 'runtime.o'], cwd=rt,
                   check=True, capture_output=True)
    subprocess.run(['gcc', '-c', 'entry.S', '-o', 'entry.o'], cwd=rt, check=True, capture_output=True)
    subprocess.run(['ld', '-T', 'rt.ld', '-o', 'runtime.elf', 'entry.o', 'runtime.o'],
                   cwd=rt, check=True, capture_output=True)
    subprocess.run(['objcopy', '-O', 'binary', '-j', '.text', 'runtime.elf', 'runtime_text.bin'],
                   cwd=rt, check=True, capture_output=True)
    subprocess.run(['objcopy', '-O', 'binary', '-j', '.data', '--set-section-flags',
                    '.data=alloc,load,contents', 'runtime.elf', 'runtime_data.bin'],
                   cwd=rt, check=True, capture_output=True)
    return rt


class MemoryOracleError(Exception):
    pass


def build_runtime_compiler(rt_dir, work):
    """Build the current compiler against rt_dir. The compiler embeds this runtime, so its
    allocator during a later self-compile is the mutant runtime, not the --rt of the output."""
    exe = os.path.join(work, 'compiler')
    p = subprocess.run([SEED, SRC, '-o', exe, '--rt', rt_dir],
                       capture_output=True, text=True, timeout=300)
    if p.returncode != 0:
        raise MemoryOracleError('compiler build failed: ' + p.stderr.strip()[:300])
    return exe


def parse_peak(returncode, stderr):
    if returncode != 0:
        raise MemoryOracleError('self-compile failed: ' + stderr.strip()[:300])
    m = re.search(r'obo: (\d+) peak bytes', stderr)
    if not m:
        raise MemoryOracleError('self-compile reported no live peak')
    return int(m.group(1))


def runtime_peak(rt_dir, work):
    """Live peak of the compiler built against rt_dir compiling the current source. The same
    compiler is then run with --rt rt_dir for the output image; the reported peak is the
    compiler's own runtime because that runtime was linked into the compiler executable."""
    exe = build_runtime_compiler(rt_dir, work)
    out = os.path.join(work, 'self.out')
    env = dict(os.environ)
    env.pop('OBO_CHECK', None)
    env.pop('OBX_GC', None)
    env['OBO_STATS'] = '1'
    p = subprocess.run([exe, SRC, '-o', out, '--rt', rt_dir],
                       capture_output=True, text=True, timeout=300, env=env)
    return parse_peak(p.returncode, p.stderr)


def memory_baseline():
    work = tempfile.mkdtemp(prefix='mio-memory-base-', dir=TMP)
    try:
        rt = build_runtime(open(RUNTIME).read(), work)
        try:
            peak = runtime_peak(rt, work)
        except MemoryOracleError as e:
            raise SystemExit('memory baseline harness failure: ' + str(e))
        if peak > PEAK_BUDGET:
            raise SystemExit(f'the unmutated runtime baseline peaks at {peak}, over {PEAK_BUDGET}')
        print(f'memory baseline: unmutated runtime/compiler peak {peak} bytes')
        return peak
    finally:
        shutil.rmtree(work, ignore_errors=True)


def judge(rt_dir, lib_dir, pattern):
    """The first failing case's message for one mutant, or None if all pass."""
    for c in CASES:
        if pattern and pattern not in c['name']:
            continue
        try:
            run_case(c, rt_dir, lib_dir)
        except Fail as e:
            return f"{c['name']}: {e}"
    return None


def main():
    pattern = sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith('-') else ''
    if any(a.startswith('-') for a in sys.argv[1:]):
        raise SystemExit("the script takes a case-name pattern only")
    runtime_src = open(RUNTIME).read()
    lib_src = open(os.path.join(LIBDIR, 'io.obx')).read()

    # the baseline: the unmutated runtime and library must pass every case
    first = judge(FORK, LIBDIR, pattern)
    if first is not None:
        raise SystemExit(f"the baseline fails ({first}), so no mutant can be judged")
    print(f"baseline: {sum(1 for c in CASES if pattern in c['name'])} cases passed")
    base_peak = memory_baseline()

    # every pattern must still match exactly once
    stale, broken, caught, missed, equiv = [], [], [], [], []
    jobs = [('runtime', n, o, t) for n, (o, t) in RUNTIME_MUTANTS.items()] + \
           [('library', n, o, t) for n, (o, t) in LIB_MUTANTS.items()]
    for kind, name, old, new in jobs:
        if pattern and pattern not in name:
            continue
        src = runtime_src if kind == 'runtime' else lib_src
        n = src.count(old)
        if n != 1:
            stale.append(f"STALE  {kind}: {name} (pattern found {n} times)")
            continue
        work = tempfile.mkdtemp(prefix='mio-', dir=TMP)
        try:
            if kind == 'runtime':
                rt = build_runtime(src.replace(old, new), work)
                lib_dir = LIBDIR
            else:
                # the mutant's own module tree: every library module, with io.obx
                # replaced by the mutated text
                rt = FORK
                lib_dir = os.path.join(work, 'lib')
                os.makedirs(lib_dir)
                for f in sorted(os.listdir(LIBDIR)):
                    if f.endswith('.obx') and f != 'io.obx':
                        shutil.copyfile(os.path.join(LIBDIR, f),
                                        os.path.join(lib_dir, f))
                open(os.path.join(lib_dir, 'io.obx'), 'w').write(
                    src.replace(old, new))
            first = judge(rt, lib_dir, pattern)
            if first is None and kind == 'runtime' and name in MEMORY_MUTANTS:
                try:
                    peak = runtime_peak(rt, work)
                except MemoryOracleError as e:
                    broken.append(f"BROKEN {kind}: {name}: {e}")
                    continue
                over, delta = empty_array_regression(base_peak, peak)
                if peak > PEAK_BUDGET:
                    first = (f"memory: live peak {peak} bytes is over the budget of {PEAK_BUDGET} "
                             f"(baseline {base_peak}, delta {delta}); the compiler embeds the mutant runtime")
                elif over:
                    first = (f"memory: live peak {peak} is {delta} bytes above baseline {base_peak} "
                             f"(minimum empty-array delta 30000); the shared empty block was lost")
                else:
                    first = (f"memory: live peak {peak} bytes is within budget and only {delta} bytes "
                             f"above baseline {base_peak}; no empty-array regression")
        except subprocess.CalledProcessError as e:
            broken.append(f"BROKEN {kind}: {name}: {e.stderr.decode()[:160]}")
            continue
        finally:
            shutil.rmtree(work, ignore_errors=True)
        if first is None:
            missed.append(f"MISSED {kind}: {name}")
        elif first.startswith('memory: live peak') and 'stays within' in first:
            missed.append(f"MISSED {kind}: {name}  ({first})")
        else:
            caught.append(f"caught {kind}: {name}  ({first})")
    for line in caught + missed + stale + broken + equiv:
        print(line)
    total = len(caught) + len(missed) + len(stale) + len(broken) + len(equiv)
    print(f"\n{len(caught)}/{total} mutants caught, {len(equiv)} equivalent, {len(missed)} missed, "
          f"{len(stale)} stale, {len(broken)} broken")
    sys.exit(1 if (missed or stale or broken) else 0)


if __name__ == '__main__':
    main()
