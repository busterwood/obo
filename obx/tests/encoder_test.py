"""Cross-checks obxc's x64 encoder against objdump.

Each case is one encoder command for `obxc --encode` (see the end of
src/obxc.obx) together with what it should decode to, in objdump's Intel
syntax. All cases in a group are encoded in one obxc run and decoded by one
objdump run; every instruction must decode at exactly the address where it was
emitted, to exactly the expected text."""
import shutil
import subprocess

R64 = ['rax', 'rcx', 'rdx', 'rbx', 'rsp', 'rbp', 'rsi', 'rdi',
       'r8', 'r9', 'r10', 'r11', 'r12', 'r13', 'r14', 'r15']
R32 = ['eax', 'ecx', 'edx', 'ebx', 'esp', 'ebp', 'esi', 'edi'] + [f'r{i}d' for i in range(8, 16)]
R8 = ['al', 'cl', 'dl', 'bl', 'spl', 'bpl', 'sil', 'dil'] + [f'r{i}b' for i in range(8, 16)]
CC = ['o', 'no', 'b', 'ae', 'e', 'ne', 'be', 'a', 's', 'ns', 'p', 'np', 'l', 'ge', 'le', 'g']
DISPS = [0, 8, -8, 127, -128, 128, -129, 0x12345, -0x12345, 2 ** 31 - 1, -2 ** 31]
ABS = [0x0C000000, 0x0C000008, 0x08000000]


def hx(v):
    return hex(v & (2 ** 64 - 1))


def mem_text(m, size='QWORD'):
    base, index, disp = m[0], m[1], m[2]
    scale = m[3] if len(m) > 3 else 8
    if base is None:
        return f"{size} PTR ds:{hex(disp)}"
    s = R64[base]
    if index is not None:
        s += f"+{R64[index]}*{scale}"
    # objdump shows an explicit +0x0 when a disp8 of zero had to be encoded
    if disp or ((base & 7) == 5):
        s += f"+{hex(disp)}" if disp >= 0 else f"-{hex(-disp)}"
    return f"{size} PTR [{s}]"


def mem_operands():
    for base in range(16):
        for index in [None] + [i for i in range(16) if i != 4]:
            for disp in DISPS:
                yield (base, index, disp)
    for a in ABS:
        yield (None, None, a)



def spec(rm):
    """encoder-script form of a register or (base, index, disp[, scale]) operand"""
    if isinstance(rm, int):
        return f"r:{rm}"
    base, index, disp = rm[0], rm[1], rm[2]
    scale = rm[3] if len(rm) > 3 else 8
    return f"m:{-1 if base is None else base},{-1 if index is None else index},{disp},{scale}"


def op(opc, reg, rm, w=1, byte=0, imm='-'):
    return f"op {opc:#x} {reg} {spec(rm)} {w} {byte} {imm}"


def le32(v):
    return (v & 0xFFFFFFFF).to_bytes(4, 'little').hex()


def fits32(v):
    return -2 ** 31 <= v < 2 ** 31


def check(cases, Fail, obxc, unique):
    """cases: list of (encoder command, expected objdump text)."""
    script = unique('encoder') + '.txt'
    with open(script, 'w') as f:
        f.write(''.join(cmd + '\n' for cmd, _ in cases))
    p = subprocess.run([obxc, '--encode', script], capture_output=True, text=True, timeout=120)
    if p.returncode != 0:
        raise Fail(f"obxc --encode failed: {p.stderr.strip()[:300]}")
    lines = p.stdout.splitlines()
    if len(lines) != len(cases):
        raise Fail(f"obxc produced {len(lines)} lines for {len(cases)} commands")
    code = bytearray()
    starts = []
    for h in lines:
        starts.append(len(code))
        code += bytes.fromhex(h)
    path = script + '.bin'
    with open(path, 'wb') as f:
        f.write(code)
    out = subprocess.run(['objdump', '-D', '-b', 'binary', '-mi386:x86-64', '-M', 'intel',
                          '--insn-width=16', path], capture_output=True, text=True).stdout
    decoded = {}
    for line in out.splitlines():
        parts = line.split('\t')
        if len(parts) >= 3 and parts[0].strip().endswith(':'):
            decoded[int(parts[0].strip()[:-1], 16)] = ' '.join(parts[2].split())
    expected = [t for _, t in cases]
    if set(decoded) != set(starts):
        extra = sorted(set(decoded) - set(starts))[:3]
        missing = sorted(set(starts) - set(decoded))[:3]
        idx = starts.index(missing[0]) if missing else max(i for i, s in enumerate(starts) if s < extra[0])
        raise Fail(f"instruction boundaries differ near case {idx} ({cases[idx][0]}): expected {expected[idx]!r}, "
                   f"objdump saw {decoded.get(starts[idx], '?')!r}")
    bad = [(i, expected[i], decoded[s]) for i, s in enumerate(starts) if decoded[s] != ' '.join(expected[i].split())]
    if bad:
        shown = [f"case {i} ({cases[i][0]}): expected {e!r}, got {g!r}" for i, e, g in bad[:5]]
        raise Fail(f"{len(bad)} of {len(cases)} wrong\n" + '\n'.join(shown))


def group_mov_mem(Fail, obxc, unique):
    cases = []
    for k, m in enumerate(mem_operands()):
        r = k % 16
        cases.append((op(0x8b, r, m), f"mov {R64[r]},{mem_text(m)}"))
        r2 = (k * 7) % 16
        cases.append((op(0x89, r2, m), f"mov {mem_text(m)},{R64[r2]}"))
    check(cases, Fail, obxc, unique)


def group_alu_mem(Fail, obxc, unique):
    names = {0x03: 'add', 0x2B: 'sub', 0x3B: 'cmp'}
    cases = []
    for k, m in enumerate(mem_operands()):
        if k % 3:
            continue
        for opc, n in names.items():
            r = (k + opc) % 16
            cases.append((op(opc, r, m), f"{n} {R64[r]},{mem_text(m)}"))
        r = k % 16
        cases.append((op(0x0faf, r, m), f"imul {R64[r]},{mem_text(m)}"))
        cases.append((op(0xf7, 7, m), f"idiv {mem_text(m)}"))
        cases.append((op(0xc7, 0, m, imm=le32(-5)), f"mov {mem_text(m)},{hx(-5)}"))
    check(cases, Fail, obxc, unique)


def group_reg_reg(Fail, obxc, unique):
    cases = []
    for d in range(16):
        for s in range(16):
            cases.append((op(0x8b, d, s), f"mov {R64[d]},{R64[s]}"))
            cases.append((op(0x03, d, s), f"add {R64[d]},{R64[s]}"))
            cases.append((op(0x2b, d, s), f"sub {R64[d]},{R64[s]}"))
            cases.append((op(0x3b, d, s), f"cmp {R64[d]},{R64[s]}"))
            cases.append((op(0x0faf, d, s), f"imul {R64[d]},{R64[s]}"))
            cases.append((op(0x85, s, d), f"test {R64[d]},{R64[s]}"))
        cases.append((op(0xf7, 3, d), f"neg {R64[d]}"))
        cases.append((op(0xf7, 7, d), f"idiv {R64[d]}"))
        for v in (5, -1000, 2 ** 31 - 1):
            cases.append((op(0x69, d, d, imm=le32(v)), f"imul {R64[d]},{R64[d]},{hx(v)}"))
    check(cases, Fail, obxc, unique)


def group_immediates(Fail, obxc, unique):
    cases = []
    values = [0, 1, 7, 255, 2 ** 31 - 1, 2 ** 31, 2 ** 32 - 1, 2 ** 32, -1, -128, -2 ** 31,
              -2 ** 31 - 1, 2 ** 40, -2 ** 40, 2 ** 63 - 1, -2 ** 63]
    for r in range(16):
        for v in values:
            if 0 <= v < 2 ** 32:
                text = f"mov {R32[r]},{hex(v)}"
            elif fits32(v):
                text = f"mov {R64[r]},{hx(v)}"
            else:
                text = f"movabs {R64[r]},{hx(v)}"
            cases.append((f"movri {r} {v}", text))
    alu = {0: 'add', 1: 'or', 4: 'and', 5: 'sub', 6: 'xor', 7: 'cmp'}
    for o, n in alu.items():
        for r in range(16):
            for v in (0, 1, -1, 127, -128, 128, -129, 2 ** 31 - 1, -2 ** 31):
                cases.append((f"aluri {o} {spec(r)} {v}", f"{n} {R64[r]},{hx(v)}"))
        for m in [(5, None, -16), (13, None, 0), (12, None, 8), (None, None, 0x0C000010), (3, 9, 8)]:
            for v in (1, -1, 200, 0x08000000):
                cases.append((f"aluri {o} {spec(m)} {v}", f"{n} {mem_text(m)},{hx(v)}"))
    check(cases, Fail, obxc, unique)


def group_bytes_stack(Fail, obxc, unique):
    cases = []
    for r in range(16):
        for cc in range(16):
            cases.append((op(0x0f90 + cc, 0, r, w=0, byte=1), f"set{CC[cc]} {R8[r]}"))
        cases.append((op(0x0fb6, r, r, byte=1), f"movzx {R64[r]},{R8[r]}"))
        cases.append((f"push {r}", f"push {R64[r]}"))
        cases.append((f"pop {r}", f"pop {R64[r]}"))
    check(cases, Fail, obxc, unique)


def group_jumps(Fail, obxc, unique):
    cases = [(f"jcc {cc} 0", f"j{CC[cc]} 0x0") for cc in range(16)]
    cases += [("jmp 0", "jmp 0x0"), ("call 0", "call 0x0")]
    check(cases, Fail, obxc, unique)


def group_bitwise_shifts(Fail, obxc, unique):
    cases = []
    for d in range(16):
        for s in range(0, 16, 3):
            for opc, n in ((0x23, 'and'), (0x0B, 'or'), (0x33, 'xor')):
                cases.append((op(opc, d, s), f"{n} {R64[d]},{R64[s]}"))
        for ext, n in ((4, 'shl'), (7, 'sar')):
            for k in (1, 8, 63):
                cases.append((op(0xc1, ext, d, imm=f"{k:02x}"), f"{n} {R64[d]},{hex(k)}"))
            cases.append((op(0xd3, ext, d), f"{n} {R64[d]},cl"))
        cases.append((op(0xf7, 2, d), f"not {R64[d]}"))
    for k, m in enumerate(mem_operands()):
        if k % 5 == 0:
            r = k % 16
            cases.append((op(0x23, r, m), f"and {R64[r]},{mem_text(m)}"))
    check(cases, Fail, obxc, unique)


def group_bytes_memory(Fail, obxc, unique):
    """Byte loads and stores, with both scale 1 (byte arrays) and scale 8."""
    cases = []
    for k, m in enumerate(mem_operands()):
        if k % 2:
            continue
        for mm in ((m, (m[0], m[1], m[2], 1)) if m[1] is not None else (m,)):
            r = (k * 5) % 16
            cases.append((op(0x0fb6, r, mm), f"movzx {R64[r]},{mem_text(mm, 'BYTE')}"))
            cases.append((op(0x88, r, mm, w=0, byte=1), f"mov {mem_text(mm, 'BYTE')},{R8[r]}"))
            cases.append((op(0xc6, 0, mm, w=0, imm='41'), f"mov {mem_text(mm, 'BYTE')},0x41"))
            cases.append((op(0x8b, r, mm), f"mov {R64[r]},{mem_text(mm)}"))
    check(cases, Fail, obxc, unique)


GROUPS = [group_mov_mem, group_alu_mem, group_reg_reg, group_immediates, group_bytes_stack, group_jumps,
          group_bitwise_shifts, group_bytes_memory]


def tests(Fail, obxc, unique):
    if not shutil.which('objdump'):
        return []
    return [(g.__name__.replace('group_', ''), lambda g=g: g(Fail, obxc, unique)) for g in GROUPS]
