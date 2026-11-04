#!/usr/bin/env python3
"""Checks the native backend's x86-64 encoder (compiler/x64.obx) against GNU as.

    python3 obx/tools/x64_encode_test.py [--n 4000] [--seed 1] [--obxc PATH]

Makes random instructions of every form the encoder has, in the encoder's test format,
encodes them with `obxc --encode-test` and assembles the same instructions (Intel syntax)
with `as`; the bytes must be identical. On a difference it prints the first instruction
that differs, with both encodings.
"""
import argparse
import os
import random
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
OBXC = os.path.normpath(os.path.join(HERE, '..', 'compiler', 'out', 'obxc'))

R64 = ['rax', 'rcx', 'rdx', 'rbx', 'rsp', 'rbp', 'rsi', 'rdi', 'r8', 'r9', 'r10', 'r11', 'r12', 'r13', 'r14', 'r15']
R32 = ['eax', 'ecx', 'edx', 'ebx', 'esp', 'ebp', 'esi', 'edi'] + [f'r{i}d' for i in range(8, 16)]
R16 = ['ax', 'cx', 'dx', 'bx', 'sp', 'bp', 'si', 'di'] + [f'r{i}w' for i in range(8, 16)]
R8 = ['al', 'cl', 'dl', 'bl', 'spl', 'bpl', 'sil', 'dil'] + [f'r{i}b' for i in range(8, 16)]
REGS = {8: R64, 4: R32, 2: R16, 1: R8}
PTR = {8: 'qword', 4: 'dword', 2: 'word', 1: 'byte'}
ALU = ['add', 'or', 'adc', 'sbb', 'and', 'sub', 'xor', 'cmp']
CC = ['o', 'no', 'b', 'ae', 'e', 'ne', 'be', 'a', 's', 'ns', 'p', 'np', 'l', 'ge', 'le', 'g']
SSE = ['addsd', 'mulsd', 'subsd', 'divsd', 'sqrtsd', 'movsd']


def rnd_imm(r, bits=32):
    k = r.random()
    if k < 0.3:
        return r.randint(-128, 127)
    if k < 0.5:
        return r.choice([0, 1, -1, 127, -128, 128, -129, 2**31 - 1, -2**31])
    return r.randint(-2**(bits - 1), 2**(bits - 1) - 1)


def rnd_mem(r):
    k = r.random()
    disp = r.choice([0, 0, 8, -8, 127, -128, 128, 4096, -4096, r.randint(-2**31, 2**31 - 1)])
    if k < 0.08:
        return (-1, -1, 1, r.randint(0, 2**31 - 1))
    if k < 0.12:
        return (-2, -1, 1, disp)
    base = r.randint(0, 15)
    if k < 0.55:
        return (base, -1, 1, disp)
    index = r.choice([i for i in range(16) if i != 4])
    scale = r.choice([1, 2, 4, 8])
    if k < 0.62:
        return (-1, index, scale, disp)
    return (base, index, scale, disp)


def mem_text(m):
    b, i, s, d = m
    if b == -2:
        return f'm:{b},{i},{s},{d}', f'[rip{d:+d}]'
    if b < 0 and i < 0:
        return f'm:-1,-1,1,{d}', f'[{d}]'
    parts = []
    if b >= 0:
        parts.append(R64[b])
    if i >= 0:
        parts.append(f'{R64[i]}*{s}')
    t = '+'.join(parts)
    if d or (b < 0):
        t += f'{d:+d}'
    return f'm:{b},{i},{s},{d}', f'[{t}]'


def gen(r):
    """One instruction: (encoder test line, as text)."""
    k = r.randint(0, 35)
    size = r.choice([1, 2, 4, 8])
    a, b = r.randint(0, 15), r.randint(0, 15)
    if k == 0:
        op = r.randint(0, 7)
        return f'alu {ALU[op]} {size} {a} {b}', f'{ALU[op]} {REGS[size][a]}, {REGS[size][b]}'
    if k == 1:
        op, sz = r.randint(0, 7), r.choice([4, 8])
        v = rnd_imm(r)
        return f'alui {ALU[op]} {sz} {a} {v}', f'{ALU[op]} {REGS[sz][a]}, {v}'
    if k == 2:
        op = r.randint(0, 7)
        mt, ma = mem_text(rnd_mem(r))
        return f'alum {ALU[op]} {size} {a} {mt}', f'{ALU[op]} {REGS[size][a]}, {PTR[size]} ptr {ma}'
    if k == 3:
        return f'test {size} {a} {b}', f'test {REGS[size][a]}, {REGS[size][b]}'
    if k == 4:
        return f'mov {size} {a} {b}', f'mov {REGS[size][a]}, {REGS[size][b]}'
    if k == 5:
        mt, ma = mem_text(rnd_mem(r))
        return f'load {size} {a} {mt}', f'mov {REGS[size][a]}, {PTR[size]} ptr {ma}'
    if k == 6:
        mt, ma = mem_text(rnd_mem(r))
        return f'store {size} {mt} {a}', f'mov {PTR[size]} ptr {ma}, {REGS[size][a]}'
    if k == 7:
        mt, ma = mem_text(rnd_mem(r))
        v = rnd_imm(r, 8 * min(size, 4))
        return f'storei {size} {mt} {v}', f'mov {PTR[size]} ptr {ma}, {v}'
    if k == 8:
        mt, ma = mem_text(rnd_mem(r))
        sz = r.choice([1, 2, 4, 8])
        if sz in (1, 2):
            return f'loadzx {sz} {a} {mt}', f'movzx {R32[a]}, {PTR[sz]} ptr {ma}'
        return f'loadzx {sz} {a} {mt}', f'mov {REGS[sz][a]}, {PTR[sz]} ptr {ma}'
    if k == 9:
        mt, ma = mem_text(rnd_mem(r))
        sz = r.choice([1, 2, 4, 8])
        if sz == 4:
            return f'loadsx {sz} {a} {mt}', f'movsxd {R64[a]}, dword ptr {ma}'
        if sz == 8:
            return f'loadsx {sz} {a} {mt}', f'mov {R64[a]}, qword ptr {ma}'
        return f'loadsx {sz} {a} {mt}', f'movsx {R64[a]}, {PTR[sz]} ptr {ma}'
    if k == 10:
        sz = r.choice([1, 2, 4])
        if sz == 4:
            return f'zx 4 {a} {b}', f'mov {R32[a]}, {R32[b]}'
        return f'zx {sz} {a} {b}', f'movzx {R32[a]}, {REGS[sz][b]}'
    if k == 11:
        sz = r.choice([1, 2, 4])
        if sz == 4:
            return f'sx 4 {a} {b}', f'movsxd {R64[a]}, {R32[b]}'
        return f'sx {sz} {a} {b}', f'movsx {R64[a]}, {REGS[sz][b]}'
    if k == 12:
        mt, ma = mem_text(rnd_mem(r))
        return f'lea {a} {mt}', f'lea {R64[a]}, {ma}'
    if k == 13:
        v = r.choice([rnd_imm(r), r.randint(0, 2**32 - 1), r.randint(-2**63, 2**63 - 1)])
        if 0 <= v < 2**32:
            return f'movri {a} {v}', f'mov {R32[a]}, {v}'
        if -2**31 <= v < 2**31:
            return f'movri {a} {v}', f'mov {R64[a]}, {v}'
        return f'movri {a} {v}', f'movabs {R64[a]}, {v}'
    if k == 14:
        sz = r.choice([4, 8])
        if r.random() < 0.5:
            v = rnd_imm(r)
            return f'imuli {sz} {a} {b} {v}', f'imul {REGS[sz][a]}, {REGS[sz][b]}, {v}'
        return f'imul {sz} {a} {b}', f'imul {REGS[sz][a]}, {REGS[sz][b]}'
    if k == 15:
        op = r.choice(['not', 'neg', 'mul', 'imul', 'div', 'idiv'])
        sz = r.choice([4, 8])
        return f'un {op} {sz} {a}', f'{op} {REGS[sz][a]}'
    if k == 16:
        op, sz = r.choice(['shl', 'shr', 'sar']), r.choice([4, 8])
        return f'shcl {op} {sz} {a}', f'{op} {REGS[sz][a]}, cl'
    if k == 17:
        op, sz = r.choice(['shl', 'shr', 'sar']), r.choice([4, 8])
        n = r.randint(1, 31 if sz == 4 else 63)
        return f'shi {op} {sz} {a} {n}', f'{op} {REGS[sz][a]}, {n}'
    if k == 18:
        c = r.randint(0, 15)
        return f'setcc {c} {a}', f'set{CC[c]} {R8[a]}'
    if k == 19:
        c = r.randint(0, 15)
        return f'cmov {c} {a} {b}', f'cmov{CC[c]} {R64[a]}, {R64[b]}'
    if k == 20:
        return f'push {a}', f'push {R64[a]}'
    if k == 21:
        return f'pop {a}', f'pop {R64[a]}'
    if k == 22:
        return f'callr {a}', f'call {R64[a]}'
    if k == 23:
        return f'jmpr {a}', f'jmp {R64[a]}'
    if k == 24:
        w = r.choice([('ret', 'ret'), ('leave', 'leave'), ('cqo', 'cqo'), ('cdq', 'cdq'), ('movsb', 'rep movsb'), ('stosb', 'rep stosb'),
                      ('stos 1', 'rep stosb'), ('stos 2', 'rep stosw'), ('stos 4', 'rep stosd'), ('stos 8', 'rep stosq'),
                      ('ud2', 'ud2')])
        return w
    if k == 25:
        op = r.choice(SSE)
        return f'sse {op} {a} {b}', f'{op} xmm{a}, xmm{b}'
    if k == 26:
        return f'ucomisd {a} {b}', f'ucomisd xmm{a}, xmm{b}'
    if k == 27:
        return f'xorpd {a} {b}', f'xorpd xmm{a}, xmm{b}'
    if k == 28:
        mt, ma = mem_text(rnd_mem(r))
        return f'movsdload {a} {mt}', f'movsd xmm{a}, qword ptr {ma}'
    if k == 29:
        mt, ma = mem_text(rnd_mem(r))
        return f'movsdstore {mt} {a}', f'movsd qword ptr {ma}, xmm{a}'
    if k == 30:
        return f'movqxr {a} {b}', f'movq xmm{a}, {R64[b]}'
    if k == 31:
        return f'movqrx {a} {b}', f'movq {R64[a]}, xmm{b}'
    if k == 33:
        op = r.randint(0, 7)
        mt, ma = mem_text(rnd_mem(r))
        v = rnd_imm(r, 8 * min(size, 4))
        return f'alumi {ALU[op]} {size} {mt} {v}', f'{ALU[op]} {PTR[size]} ptr {ma}, {v}'
    if k == 35:
        op = r.randint(0, 7)
        mt, ma = mem_text(rnd_mem(r))
        return f'alumr {ALU[op]} {size} {mt} {a}', f'{ALU[op]} {PTR[size]} ptr {ma}, {REGS[size][a]}'
    if k == 32:
        return (f'cvtsi2sd {a} {b}', f'cvtsi2sd xmm{a}, {R64[b]}') if r.random() < 0.5 else \
               (f'cvttsd2si {a} {b}', f'cvttsd2si {R64[a]}, xmm{b}')
    return (f'shld {a} {b}', f'shld {R64[a]}, {R64[b]}, cl') if r.random() < 0.5 else \
           (f'shrd {a} {b}', f'shrd {R64[a]}, {R64[b]}, cl')


def assemble(lines, t):
    s = os.path.join(t, 'x.s')
    o = os.path.join(t, 'x.o')
    b = os.path.join(t, 'x.bin')
    with open(s, 'w') as f:
        f.write('.intel_syntax noprefix\n.text\n' + '\n'.join(lines) + '\n')
    r = subprocess.run(['as', '-o', o, s], capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit('as failed:\n' + r.stderr[:3000])
    subprocess.run(['objcopy', '-O', 'binary', '-j', '.text', o, b], check=True)
    return open(b, 'rb').read().hex()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--n', type=int, default=4000)
    ap.add_argument('--seed', type=int, default=1)
    ap.add_argument('--obxc', default=OBXC)
    args = ap.parse_args()
    r = random.Random(args.seed)
    cases = [gen(r) for _ in range(args.n)]
    with tempfile.TemporaryDirectory() as t:
        inp = os.path.join(t, 'in.txt')
        with open(inp, 'w') as f:
            f.write('\n'.join(c[0] for c in cases) + '\n')
        p = subprocess.run([args.obxc, '--encode-test', inp], capture_output=True, text=True)
        if p.returncode != 0:
            sys.exit('obxc --encode-test failed:\n' + p.stderr[:3000])
        mine = p.stdout.split('\n')[:len(cases)]
        theirs = assemble([c[1] for c in cases], t)
        if ''.join(mine) == theirs:
            print(f'x64 encoder: {len(cases)} instructions identical to as')
            return
        for c, m in zip(cases, mine):
            a = assemble([c[1]], t)
            if a != m:
                print(f'x64 encoder: differs on {c[0]!r} ({c[1]}): obxc {m}, as {a}')
                sys.exit(1)
        print('x64 encoder: the byte streams differ but no single instruction does')
        sys.exit(1)


if __name__ == '__main__':
    main()
