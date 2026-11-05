#!/usr/bin/env python3
"""Builds the native runtime (native/rt.c) and writes compiler/rtx.obx: its sections (code,
read-only data, data, bss), their relocations, its global symbols and the interface header
native/obxrt.h, for the native backend (design/native.md, "Runtime").

    python3 obx/compiler/gen_rtx.py          # after changing native/rt.c or obxrt.h

The runtime is compiled by gcc, freestanding (no libc), with every function and every datum in
a section of its own (-ffunction-sections -fdata-sections). The libgcc members it needs (the
128-bit helpers) are taken from libgcc.a as a linker takes them from an archive: the first
member that defines a symbol still undefined, until none is. Nothing is linked here: rtx.obx
keeps each object's sections apart, with their relocations, and the native backend's link
(x64gen.link) keeps the sections reachable from what the program uses, lays them out and applies
the relocations. rtx.obx is checked in, so building the compiler needs only gcc on the seed; the
bytes depend on the gcc version that ran this.
"""
import os
import struct
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
NAT = os.path.join(HERE, 'native')
OUT = os.path.join(HERE, 'rtx.obx')
# -falign-functions=64: without it a change elsewhere in rt.c moved the allocator and cost
# bench alloc 26% of its cycles with the same instructions (2026-11-03).
# -march=x86-64-v3: the CPU level programs require; -ffp-contract=off: the math port's floats keep
# their bits (no a * b + c fused unless written as fma). -mmove-max/-mstore-max=128: no ymm struct
# copies, whose loads straight after narrower stores stall store forwarding (see main.obx gccCmd).
CFLAGS = ['-O2', '-march=x86-64-v3', '-mmove-max=128', '-mstore-max=128', '-ffp-contract=off', '-falign-functions=64', '-std=gnu11', '-ffreestanding', '-fno-builtin', '-nostdlib', '-fno-pic', '-fno-pie',
          '-fno-stack-protector', '-fno-asynchronous-unwind-tables', '-fno-tree-loop-distribute-patterns',
          '-fcf-protection=none', '-fno-common', '-ffunction-sections', '-fdata-sections',
          '-Wall', '-Wno-builtin-declaration-mismatch', '-Werror']

# Where the backend lays the runtime out (design/native.md, "ELF layout"): its code and
# read-only data from the page after the ELF headers, its data and bss from 1 GiB.
TEXT_ADDR = 0x401000
DATA_ADDR = 0x40000000

# Section kinds, in layout order (rtx.obx's SEC_KIND).
K_CODE, K_RODATA, K_DATA, K_BSS = 0, 1, 2, 3

# Relocation types (ELF x86-64), as rtx.obx's REL_TYPE keeps them; PLT32 becomes PC32 (no PLT:
# every function is the runtime's own).
R_64, R_PC32, R_PLT32, R_32, R_32S = 1, 2, 4, 10, 11

SHT_PROGBITS, SHT_SYMTAB, SHT_NOBITS, SHT_RELA, SHT_REL = 1, 2, 8, 4, 9
SHF_WRITE, SHF_ALLOC, SHF_EXEC, SHF_TLS = 1, 2, 4, 0x400
SHN_UNDEF, SHN_ABS, SHN_COMMON = 0, 0xfff1, 0xfff2
STB_LOCAL, STB_GLOBAL, STB_WEAK = 0, 1, 2
STT_FUNC, STT_SECTION = 2, 3
# sections of an object that are not part of the program
DROP = ('.eh_frame', '.comment', '.note')


def fail(msg):
    sys.exit('gen_rtx.py: ' + msg)


def run(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        fail(f"{' '.join(cmd)} failed:\n{r.stdout}{r.stderr}")
    return r.stdout


# ---------------------------------------------------------------- reading ELF objects

class Obj:
    """A relocatable ELF64 x86-64 object: its sections, symbols and relocations."""

    def __init__(self, name, b):
        self.name = name
        if b[:4] != b'\x7fELF' or b[4] != 2 or b[5] != 1:
            fail(f'{name}: not a little-endian ELF64 file')
        etype, mach = struct.unpack_from('<HH', b, 16)
        if etype != 1 or mach != 62:
            fail(f'{name}: not a relocatable x86-64 object')
        shoff, = struct.unpack_from('<Q', b, 40)
        shentsize, shnum, shstrndx = struct.unpack_from('<HHH', b, 58)
        self.secs = []
        for i in range(shnum):
            nm, ty, fl, _addr, off, size, link, info, al, ent = struct.unpack_from('<IIQQQQIIQQ', b, shoff + i * shentsize)
            self.secs.append(dict(nm=nm, type=ty, flags=fl, off=off, size=size, link=link, info=info,
                                  align=max(al, 1), ent=ent))
        strtab = self.secs[shstrndx]
        for s in self.secs:
            s['name'] = cstr(b, strtab['off'] + s['nm'])
            s['data'] = b'' if s['type'] == SHT_NOBITS else b[s['off']:s['off'] + s['size']]
        self.syms = []
        for s in self.secs:
            if s['type'] == SHT_SYMTAB:
                st = self.secs[s['link']]
                for j in range(s['size'] // 24):
                    nm, info, _other, shndx, value, size = struct.unpack_from('<IBBHQQ', s['data'], j * 24)
                    self.syms.append(dict(name=cstr(b, st['off'] + nm), bind=info >> 4, type=info & 15,
                                          shndx=shndx, value=value, size=size))
        self.relas = []         # (section index, [(offset, type, symbol index, addend)])
        for s in self.secs:
            if s['type'] == SHT_REL:
                fail(f'{name}: {s["name"]}: REL relocations (only RELA are read)')
            if s['type'] == SHT_RELA:
                rs = []
                for j in range(s['size'] // 24):
                    off, info, add = struct.unpack_from('<QQq', s['data'], j * 24)
                    rs.append((off, info & 0xffffffff, info >> 32, add))
                self.relas.append((s['info'], rs))

    def defines(self):
        return [y['name'] for y in self.syms
                if y['bind'] in (STB_GLOBAL, STB_WEAK) and y['shndx'] != SHN_UNDEF]

    def undefined(self):
        return [y['name'] for y in self.syms
                if y['bind'] in (STB_GLOBAL, STB_WEAK) and y['shndx'] == SHN_UNDEF and y['name']]


def cstr(b, off):
    return b[off:b.index(b'\0', off)].decode()


def archive(path):
    """The members of an ar archive (GNU format), in order: (name, bytes)."""
    b = open(path, 'rb').read()
    if b[:8] != b'!<arch>\n':
        fail(f'{path}: not an ar archive')
    out = []
    longnames = b''
    p = 8
    while p + 60 <= len(b):
        h = b[p:p + 60]
        name = h[:16].decode().rstrip()
        size = int(h[48:58].decode().strip())
        data = b[p + 60:p + 60 + size]
        p += 60 + size + (size & 1)
        if name == '/' or name == '/SYM64/':
            continue
        if name == '//':
            longnames = data
            continue
        if name.startswith('/'):
            o = int(name[1:])
            name = longnames[o:longnames.index(b'/\n', o)].decode()
        out.append((name.rstrip('/'), data))
    return out


# ---------------------------------------------------------------- the runtime's pieces

def load(rt_o, libgcc):
    """rt.o and the libgcc members it needs (transitively), as a linker takes archive members."""
    objs = [Obj('rt.o', open(rt_o, 'rb').read())]
    members = [Obj(n, d) for n, d in archive(libgcc)]
    defined = set(objs[0].defines())
    taken = set()
    while True:
        need = [n for o in objs for n in o.undefined() if n not in defined]
        if not need:
            return objs
        name = need[0]
        m = next((m for m in members if m.name not in taken and name in m.defines()), None)
        if m is None:
            fail(f"undefined symbol '{name}' (not in rt.c nor libgcc)")
        taken.add(m.name)
        objs.append(m)
        defined.update(m.defines())


def kind_of(o, s):
    if s['flags'] & SHF_TLS:
        fail(f'{o.name}: {s["name"]}: thread-local data')
    if s['type'] == SHT_NOBITS:
        return K_BSS
    if s['type'] != SHT_PROGBITS:
        fail(f'{o.name}: {s["name"]}: an allocated section of type {s["type"]}')
    if s['flags'] & SHF_EXEC:
        return K_CODE
    if s['flags'] & SHF_WRITE:
        return K_DATA
    return K_RODATA


def collect(objs):
    """The sections in layout order, the global symbols and the relocations."""
    secs = []                   # dict(obj, idx, name, kind, align, size, data)
    where = {}                  # (object number, section index) -> the section's number
    for oi, o in enumerate(objs):
        for si, s in enumerate(o.secs):
            if not s['flags'] & SHF_ALLOC or s['name'].startswith(DROP):
                continue
            secs.append(dict(obj=oi, idx=si, name=s['name'], kind=kind_of(o, s), align=s['align'],
                             size=s['size'], data=s['data']))
    # code, then read-only data, data, bss; within a kind, objects' and sections' order
    secs.sort(key=lambda x: x['kind'])
    for n, x in enumerate(secs):
        where[(x['obj'], x['idx'])] = n
    # global symbols
    glob = {}                   # name -> (section, offset, weak)
    for oi, o in enumerate(objs):
        for y in o.syms:
            if y['bind'] == STB_LOCAL or y['shndx'] == SHN_UNDEF:
                continue
            if y['shndx'] == SHN_COMMON:
                fail(f"{o.name}: '{y['name']}' is a COMMON symbol (compile with -fno-common)")
            if y['shndx'] == SHN_ABS or (oi, y['shndx']) not in where:
                fail(f"{o.name}: '{y['name']}' is not in a section of the program")
            weak = y['bind'] == STB_WEAK
            have = glob.get(y['name'])
            if have and not have[2] and not weak:
                fail(f"'{y['name']}' is defined twice")
            if not have or have[2]:
                glob[y['name']] = (where[(oi, y['shndx'])], y['value'], weak)
    # relocations: (section, offset, type, target section, addend), the addend made relative to
    # the target section (the symbol's offset in it added)
    rels = []
    for oi, o in enumerate(objs):
        for si, rs in o.relas:
            if (oi, si) not in where:
                if o.secs[si]['flags'] & SHF_ALLOC and not o.secs[si]['name'].startswith(DROP):
                    fail(f'{o.name}: relocations for a section that is not kept')
                continue
            sec = where[(oi, si)]
            for off, ty, symi, add in rs:
                y = o.syms[symi]
                if ty == R_PLT32:
                    ty = R_PC32
                if ty not in (R_64, R_PC32, R_32, R_32S):
                    fail(f'{o.name}: {o.secs[si]["name"]}+{off:#x}: relocation type {ty}')
                if y['shndx'] == SHN_UNDEF:
                    if y['name'] not in glob:
                        fail(f"{o.name}: undefined symbol '{y['name']}'")
                    tgt, val, _ = glob[y['name']]
                elif y['bind'] != STB_LOCAL and y['name'] in glob:
                    tgt, val, _ = glob[y['name']]     # a global: its one definition (weak overridden)
                elif y['shndx'] in (SHN_ABS, SHN_COMMON) or (oi, y['shndx']) not in where:
                    fail(f"{o.name}: a relocation against '{y['name']}', in no section of the program")
                else:
                    tgt, val = where[(oi, y['shndx'])], y['value']
                rels.append((sec, off, ty, tgt, val + add))
    rels.sort(key=lambda r: (r[0], r[1]))
    # a name for the perf map: the function at the section's start, else the section's name
    for n, x in enumerate(secs):
        o = objs[x['obj']]
        best = None
        for y in o.syms:
            if y['shndx'] == x['idx'] and y['value'] == 0 and y['type'] != STT_SECTION and y['name']:
                rank = (y['type'] == STT_FUNC, y['bind'] != STB_LOCAL)
                if best is None or rank > best[0]:
                    best = (rank, y['name'])
        x['label'] = best[1] if best else x['name']
    syms = sorted((nm, s, v) for nm, (s, v, _) in glob.items())
    return secs, syms, rels


# ---------------------------------------------------------------- writing rtx.obx

def lit(b):
    out = []
    for x in b:
        c = chr(x)
        if c == '"' or c == '\\':
            out.append('\\' + c)
        elif 32 <= x < 127:
            out.append(c)
        else:
            out.append('\\x%02x' % x)
    return '"' + ''.join(out) + '"'


def strlit(s):
    for ch in s:
        if not (32 <= ord(ch) < 127):
            fail(f"a byte obx cannot write in a literal: {ord(ch)}")
    return '"' + s.replace('\\', '\\\\').replace('"', '\\"') + '"'


def chunks(b, n=48):
    return [b[i:i + n] for i in range(0, len(b), n)]


def ints(name, xs, per=12):
    for x in xs:
        if not -2**31 <= x < 2**31:
            fail(f'{name}: {x} does not fit an i32')
    o = [f"pub const {name}: []i32 = ["]
    for i in range(0, len(xs), per):
        o.append('    ' + ' '.join(f'{x},' for x in xs[i:i + per]))
    return o + ["]", ""]


def strs(name, xs):
    return [f"pub const {name}: []str = ["] + [f"    {strlit(x)}," for x in xs] + ["]", ""]


def main():
    with tempfile.TemporaryDirectory() as t:
        obj = os.path.join(t, 'rt.o')
        run(['gcc'] + CFLAGS + ['-I', NAT, '-c', os.path.join(NAT, 'rt.c'), '-o', obj])
        libgcc = run(['gcc', '-print-libgcc-file-name']).strip()
        objs = load(obj, libgcc)
    secs, syms, rels = collect(objs)
    blob = bytearray()
    offs = []
    for x in secs:
        offs.append(len(blob))
        blob += x['data']
    first = []
    r = 0
    for n in range(len(secs) + 1):
        while r < len(rels) and rels[r][0] < n:
            r += 1
        first.append(r)
    header = open(os.path.join(NAT, 'obxrt.h')).read().split('\n')
    size = {k: sum(x['size'] for x in secs if x['kind'] == k) for k in range(4)}
    o = ["// Generated by gen_rtx.py from native/rt.c, libgcc and obxrt.h: do not edit.",
         "// The native runtime as sections (each function and datum its own), their relocations",
         "// and the global symbols, which x64gen's link keeps the reachable ones of and lays out;",
         "// and the C interface the backend parses before a program (design/native.md).",
         "",
         f"pub const TEXT_ADDR = {TEXT_ADDR}    // code, then read-only data",
         f"pub const DATA_ADDR = {DATA_ADDR}    // data, then bss",
         "",
         "// Section kinds; the sections are in this order (layout order).",
         f"pub const K_CODE = {K_CODE}",
         f"pub const K_RODATA = {K_RODATA}",
         f"pub const K_DATA = {K_DATA}",
         f"pub const K_BSS = {K_BSS}",
         "",
         "// Relocation types (ELF x86-64).",
         f"pub const R_64 = {R_64}",
         f"pub const R_PC32 = {R_PC32}",
         f"pub const R_32 = {R_32}",
         f"pub const R_32S = {R_32S}",
         ""]
    o += ["// Per section: a name (the function at its start, for perf maps), kind, alignment, size,",
          "// where its bytes start in BYTES (bss: none), its first relocation (SEC_REL has one more)."]
    o += strs("SEC_NAMES", [x['label'] for x in secs])
    o += ints("SEC_KIND", [x['kind'] for x in secs])
    o += ints("SEC_ALIGN", [x['align'] for x in secs])
    o += ints("SEC_SIZE", [x['size'] for x in secs])
    o += ints("SEC_OFF", offs)
    o += ints("SEC_REL", first)
    o += ["// Relocations, by section and offset: the place (section, offset), the type, and the",
          "// target: a section and the addend relative to its start."]
    o += ints("REL_OFF", [x[1] for x in rels])
    o += ints("REL_TYPE", [x[2] for x in rels])
    o += ints("REL_TARGET", [x[3] for x in rels])
    o += ints("REL_ADDEND", [x[4] for x in rels])
    o += ["// The global symbols: name, section, offset in it."]
    o += strs("SYM_NAMES", [n for n, s, v in syms])
    o += ints("SYM_SECS", [s for n, s, v in syms])
    o += ints("SYM_OFFS", [v for n, s, v in syms])
    o += ["// The bytes of the code, read-only data and data sections, one after another.",
          "pub const BYTES: []str = ["]
    o += [f"    {lit(c)}," for c in chunks(bytes(blob))]
    o += ["]", ""]
    o += strs("HEADER", header)
    text_out = '\n'.join(o)
    old = open(OUT).read() if os.path.exists(OUT) else None
    if text_out != old:
        with open(OUT, 'w') as f:
            f.write(text_out)
    print(f"rtx.obx: {len(secs)} sections from {len(objs)} objects (code {size[K_CODE]} bytes, rodata "
          f"{size[K_RODATA]}, data {size[K_DATA]}, bss {size[K_BSS]}), {len(rels)} relocations, {len(syms)} symbols")


if __name__ == '__main__':
    main()
