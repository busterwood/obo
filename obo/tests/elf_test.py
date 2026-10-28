#!/usr/bin/env python3
"""The image's runtime-data segment: only its nonzero prefix is in the file.

The runtime's data image ends in zero-filled .bss (obuf, the heap state, the
free lists), so the compiler puts the full length in p_memsz and only the
nonzero prefix in p_filesz; the loader zero-fills the rest, as it already does
for the compiler's own globals segment (filesz 0). This checks a produced
executable's program headers, that the file ends where the runtime-data
segment's file bytes end instead of carrying the zero tail, and that a normal
program compiled by the same compiler still runs.

    python3 obo/tests/elf_test.py
    python3 obo/tests/elf_test.py --source SRC   # build SRC with the seed, then check

The compiler is $OBO, or obo/obo (made from the source by the seed). --source
builds a compiler from SRC with seed/obo, as the mutation harnesses do.

run_fork.py imports check() and runs it against the compiler under test, so a
mutation of the segment emission fails the ordinary suite as well.
"""
import os
import shutil
import struct
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
FORK = os.path.dirname(HERE)
SEED = os.path.join(FORK, 'seed', 'obo')
REF = os.path.abspath(os.environ.get('OBO', os.path.join(FORK, 'obo')))
RT_DATA = 0x20000000
GLOBAL_BASE = 0x0C000000
PT_LOAD = 1
PAGE = 0x1000

PROGRAM = 'module image\n\nfn main() {\n    println("runtime data segment")\n}\n'
EXPECTED = b"runtime data segment\n"


class Fail(Exception):
    pass


def program_headers(data):
    """[(type, flags, offset, vaddr, filesz, memsz, align)] for an ELF64 image"""
    if data[:4] != b'\x7fELF' or data[4] != 2 or data[5] != 1:
        raise Fail("not a 64-bit little-endian ELF image")
    phoff = struct.unpack_from('<Q', data, 0x20)[0]
    phentsize = struct.unpack_from('<H', data, 0x36)[0]
    phnum = struct.unpack_from('<H', data, 0x38)[0]
    if phentsize != 56:
        raise Fail(f"program header entry size is {phentsize}, not 56")
    out = []
    for i in range(phnum):
        at = phoff + i * phentsize
        typ, flags = struct.unpack_from('<II', data, at)
        off, vaddr, _paddr, filesz, memsz, align = struct.unpack_from('<QQQQQQ', data, at + 8)
        out.append((typ, flags, off, vaddr, filesz, memsz, align))
    return out


def last_nonzero(data):
    """The index one past the last nonzero byte, 0 for an all-zero image"""
    n = len(data)
    while n > 0 and data[n - 1] == 0:
        n -= 1
    return n


def check(compiler, workdir=None):
    """Raise Fail unless compiler emits the trimmed runtime-data segment and a
    program it compiles runs. workdir is a scratch directory, or None for a
    private one (run_fork calls check() once per suite, possibly in parallel)."""
    rt_data = os.path.join(FORK, 'runtime_data.bin')
    if not os.path.isfile(rt_data):
        raise Fail(f"no runtime data at {rt_data}: run make first")
    rt = open(rt_data, 'rb').read()
    trimmed = last_nonzero(rt)
    own = workdir is None
    work = tempfile.mkdtemp(prefix='obo-elf-') if own else workdir
    try:
        src = os.path.join(work, 'image.obx')
        exe = os.path.join(work, 'image')
        with open(src, 'w') as f:
            f.write(PROGRAM)
        b = subprocess.run([compiler, src, '-o', exe, '--rt', FORK],
                           capture_output=True, text=True, timeout=120)
        if b.returncode != 0:
            raise Fail(f"the compiler does not build the test program: {b.stderr.strip()[:300]}")
        data = open(exe, 'rb').read()
        phdrs = program_headers(data)
        loads = [p for p in phdrs if p[0] == PT_LOAD]
        rtph = [p for p in loads if p[3] == RT_DATA]
        if len(rtph) != 1:
            raise Fail(f"expected one PT_LOAD at the runtime data address {RT_DATA:#x}, found {len(rtph)}")
        _typ, _flags, off, vaddr, filesz, memsz, align = rtph[0]
        if vaddr != RT_DATA:
            raise Fail(f"runtime data vaddr is {vaddr:#x}")
        if align != PAGE or off % PAGE != 0:
            raise Fail(f"runtime data segment is not page aligned (offset {off:#x}, align {align:#x})")
        if filesz >= memsz:
            raise Fail(f"runtime data filesz {filesz} is not below memsz {memsz}: the zero tail is in the file")
        if memsz != len(rt):
            raise Fail(f"runtime data memsz is {memsz}, not the image length {len(rt)}")
        if filesz != trimmed:
            raise Fail(f"runtime data filesz is {filesz}, not last-nonzero+1 = {trimmed}")
        size = os.path.getsize(exe)
        if size != off + filesz:
            raise Fail(f"the file is {size} bytes, not {off + filesz}: bytes follow the segment's file data")
        # the compiler's own globals segment is memory-only and unchanged
        gph = [p for p in loads if p[3] == GLOBAL_BASE]
        if len(gph) != 1:
            raise Fail(f"expected one PT_LOAD at the globals address {GLOBAL_BASE:#x}, found {len(gph)}")
        if gph[0][4] != 0 or gph[0][5] < 8:
            raise Fail(f"the globals BSS segment changed: filesz {gph[0][4]}, memsz {gph[0][5]}")
        r = subprocess.run([exe], capture_output=True, timeout=60,
                           env=dict(os.environ, OBO_CHECK='1'))
        if r.returncode != 0 or r.stdout != EXPECTED or r.stderr:
            raise Fail(f"the program runs wrongly: exit {r.returncode}, stdout {r.stdout[:80]!r}, stderr {r.stderr[:80]!r}")
        return filesz, memsz
    finally:
        if own:
            shutil.rmtree(work, ignore_errors=True)


def build(src, out):
    """Build a compiler from src with the seed: the mutation gates' judge"""
    b = subprocess.run([SEED, src, '-o', out, '--rt', FORK],
                       capture_output=True, text=True, timeout=120)
    if b.returncode != 0:
        raise Fail(f"the compiler does not build: {b.stderr.strip()[:300]}")


def main():
    args = sys.argv[1:]
    if args[:1] == ['--source'] and len(args) == 2:
        work = tempfile.mkdtemp(prefix='obo-elf-')
        try:
            exe = os.path.join(work, 'obo')
            build(args[1], exe)
            filesz, memsz = check(exe, work)
        finally:
            shutil.rmtree(work, ignore_errors=True)
        print(f"ok: runtime data filesz {filesz}, memsz {memsz}; the program runs")
        return
    if args:
        raise SystemExit(f"unknown arguments {args!r}")
    if not os.path.exists(REF):
        raise SystemExit(f"no compiler at {REF}: run make first")
    filesz, memsz = check(REF)
    print(f"ok: runtime data filesz {filesz}, memsz {memsz}; the program runs")


if __name__ == '__main__':
    try:
        main()
    except Fail as e:
        print(f"FAIL {e}")
        sys.exit(1)
