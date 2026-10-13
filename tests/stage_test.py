"""Stage tests: the compiler under test against bootstrap/obxc, the trusted
previous stage (see bootstrap/README.md).

  tokens/*    every test program, error case and example, and obxc's own
              source: identical token streams, including scanner errors
  scanfuzz/*  random text assembled from awkward fragments: identical tokens
  encode/*    scripts of encoder commands covering every instruction form and
              addressing mode: identical bytes, line by line
  compile/*   every test program, example, fuzz program and obxc itself:
              byte-identical executables
  errors/*    every error case: the same line, column and message
  fixedpoint  the compiler compiles itself, and that result compiles itself
              again: the two must be byte-identical

A deliberate change to code generation or to messages shows up as differences
here. Review them, then `make promote` makes the new compiler the bootstrap.
"""
import os
import random
import subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
OBXC_SRC = os.path.join(ROOT, 'selfhost', 'obxc.obx')


def run_mode(compiler, mode, data, unique, Fail):
    path = unique('input')
    with open(path, 'wb') as f:
        f.write(data)
    import run
    import tempfile
    with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
        p = subprocess.run([compiler, mode, path], stdout=out, stderr=err, timeout=60, preexec_fn=run._limits)
        out.seek(0)
        err.seek(0)
        stdout, stderr = out.read(), err.read(4096)
    if p.returncode not in (0, 1):
        raise Fail(f"{os.path.basename(compiler)} {mode} exited with {p.returncode}: "
                   f"{stderr.decode(errors='replace')[:300]}")
    return stdout.decode('latin-1').splitlines()


def first_diff(want, got, what):
    for i in range(max(len(want), len(got))):
        w = want[i] if i < len(want) else '<nothing>'
        g = got[i] if i < len(got) else '<nothing>'
        if w != g:
            return f"{what} {i + 1}: bootstrap {w!r}, new {g!r}"
    return None


def compare_mode(mode, data, new, old, unique, Fail, what):
    d = first_diff(run_mode(old, mode, data, unique, Fail), run_mode(new, mode, data, unique, Fail), what)
    if d:
        raise Fail(d)


FRAGMENTS = [
    'fn', 'module', 'x', '_a1', 'iffy', 'return', 'nil', 'true', ' ', ' ', '\t', '\n', '\n', '\r\n',
    '0', '7', '123', '1_000', '9223372036854775807', '9223372036854775808', '00012',
    '0x1F', '0xffff_ffff_ffff_ffff', '0x1_0000_0000_0000_0000', '0x', '0x_', '0X0000000000000000001',
    "'a'", "'\\n'", "'\\''", "'\\\\'", "'\\q'", "''", "'ab'", "'\xc3\xa9'", "'\\", "'",
    '"hi"', '"a\\tb\\"c"', '"\\q"', '""', '"unterminated', '"esc at end\\', '"\xc3\xa9"', '"a\\\nb"',
    '// comment', '//', '/', '..', '...', '==', '=', '!=', '!', '<=', '<<', '<', '>>=', '&&', '&',
    '||', '|', '^', '~', '+=', '-=', '*=', '(', ')', '{', '}', '[', ']', ',', ';', ':', '.', '?', '%',
    '@', '#', '$', '`', '\x00', '\x7f', '\xff', '\xc3',
]




def scan_fuzz(seed, new, old, unique, Fail):
    r = random.Random(seed)
    for case in range(25):
        parts = []
        for _ in range(r.randint(1, 120)):
            f = r.choice(FRAGMENTS)
            if r.random() < 0.7 and f in ('@', '#', '$', '`', '\x00', '\x7f', '\xff', '\xc3', "''", "'",
                                           '"unterminated', '0x', '0x_', "'\\", "'ab'", '"esc at end\\'):
                f = ' '
            parts.append(f)
            if r.random() < 0.3:
                parts.append(' ')
        data = ''.join(parts).encode('latin-1')
        try:
            compare_mode('--tokens', data, new, old, unique, Fail, 'token')
        except Fail as e:
            raise Fail(f"case {case}: {e}\ninput: {data!r}")


DISPS = [0, 8, -8, 127, -128, 128, -129, 0x12345, -0x12345, 2 ** 31 - 1, -2 ** 31]


def mem_specs():
    for base in range(16):
        for index in [-1] + [i for i in range(16) if i != 4]:
            for disp in DISPS:
                for scale in ((8,) if index < 0 else (1, 8)):
                    yield f"m:{base},{index},{disp},{scale}"
    for a in (0x0C000000, 0x08000000):
        yield f"m:-1,-1,{a},8"


def encoder_script(part):
    """Each part is a self-contained script covering one family of forms."""
    lines = []
    if part == 'memory':
        ops = [(0x8b, 1, 0, '-'), (0x89, 1, 0, '-'), (0x03, 1, 0, '-'), (0x3b, 1, 0, '-'),
               (0x0faf, 1, 0, '-'), (0x23, 1, 0, '-'), (0x0fb6, 1, 0, '-'), (0x88, 0, 1, '-'),
               (0xc6, 0, 0, '41'), (0xc7, 1, 0, 'fbffffff'), (0xf7, 1, 0, '-')]
        for k, spec in enumerate(mem_specs()):
            opc, wide, byte, imm = ops[k % len(ops)]
            reg = (k * 7) % 16
            if opc in (0xc6, 0xc7):
                reg = 0
            if opc == 0xf7:
                reg = 7
            lines.append(f"op {opc:#x} {reg} {spec} {wide} {byte} {imm}")
    elif part == 'registers':
        for opc in (0x8b, 0x03, 0x2b, 0x3b, 0x23, 0x0b, 0x33, 0x0faf, 0x85):
            for d in range(16):
                for s in range(16):
                    lines.append(f"op {opc:#x} {d} r:{s} 1 0 -")
        for r in range(16):
            for ext in (2, 3, 7):
                lines.append(f"op 0xf7 {ext} r:{r} 1 0 -")
            for ext in (4, 7):
                lines.append(f"op 0xc1 {ext} r:{r} 1 0 3f")
                lines.append(f"op 0xd3 {ext} r:{r} 1 0 -")
            for cc in range(16):
                lines.append(f"op {0x0f90 + cc:#x} 0 r:{r} 0 1 -")
            lines.append(f"op 0xfb6 {r} r:{r} 1 1 -")
            lines.append(f"op 0x69 {r} r:{r} 1 0 e8030000")
            lines.append(f"push {r}")
            lines.append(f"pop {r}")
    elif part == 'immediates':
        values = [0, 1, 7, 255, 2 ** 31 - 1, 2 ** 31, 2 ** 32 - 1, 2 ** 32, -1, -128, -2 ** 31,
                  -2 ** 31 - 1, 2 ** 40, -2 ** 40, 2 ** 63 - 1, -2 ** 63]
        for r in range(16):
            for v in values:
                lines.append(f"movri {r} {v}")
        for o in (0, 1, 4, 5, 6, 7):
            for r in range(16):
                for v in (0, 1, -1, 127, -128, 128, -129, 2 ** 31 - 1, -2 ** 31):
                    lines.append(f"aluri {o} r:{r} {v}")
            for spec in ("m:5,-1,-16,8", "m:13,-1,0,8", "m:12,-1,8,8", "m:-1,-1,201326608,8", "m:3,9,8,8"):
                for v in (1, -1, 200, 0x08000000):
                    lines.append(f"aluri {o} {spec} {v}")
    elif part == 'jumps':
        for cc in range(16):
            lines.append(f"jcc {cc} 0")
            lines.append(f"jcc {cc} 100000")
        lines += ["jmp 0", "jmp 5000", "call 0", "call 70000"]
    return lines




def outcome(compiler, data, name, compile_with, CompileError):
    """('ok', bytes of the executable) or ('error', message)"""
    try:
        exe = compile_with(compiler, data, name)
        return ('ok', open(exe, 'rb').read())
    except CompileError as e:
        return ('error', str(e))


def compare_compile(data, name, new, old, compile_with, CompileError, Fail):
    a = outcome(old, data, name, compile_with, CompileError)
    b = outcome(new, data, name, compile_with, CompileError)
    if a == b:
        return
    if a[0] != b[0] or a[0] == 'error':
        raise Fail(f"bootstrap: {a[1] if a[0] == 'error' else 'compiles'}\n     new:       "
                   f"{b[1] if b[0] == 'error' else 'compiles'}")
    x, y = a[1], b[1]
    n = next((i for i in range(min(len(x), len(y))) if x[i] != y[i]), min(len(x), len(y)))
    raise Fail(f"executables differ, first at file offset {n:#x} (sizes {len(x)} and {len(y)})")


def fixed_point(new, compile_with, CompileError, Fail):
    data = open(OBXC_SRC, 'rb').read()
    try:
        s2 = compile_with(new, data, 'stage2')
        s3 = compile_with(s2, data, 'stage3')
    except CompileError as e:
        raise Fail(f"obxc does not compile itself: {e}")
    if open(s2, 'rb').read() != open(s3, 'rb').read():
        raise Fail("the compiler built by the compiler under test differs from the one it builds")


def tests(new, old, root, compile_with, CompileError, Fail, unique, error_cases, nfuzz):
    out = []
    files = []
    for d, ext in (('tests/run', '.obx'), ('examples', '.obx')):
        for f in sorted(os.listdir(os.path.join(root, d))):
            if f.endswith(ext):
                files.append((f"{d.split('/')[-1]}/{f}", open(os.path.join(root, d, f), 'rb').read(), False))
    for f in sorted(os.listdir(os.path.join(HERE, 'errors'))):
        if f.endswith('.obx'):
            for name, text, _, _ in error_cases(os.path.join(HERE, 'errors', f)):
                files.append((f"errors/{name}", text.encode('utf-8'), True))
    files.append(("selfhost/obxc.obx", open(OBXC_SRC, 'rb').read(), False))
    for name, data, is_error in files:
        out.append((f"tokens/{name}", lambda d=data: compare_mode('--tokens', d, new, old, unique, Fail, 'token')))
        group = 'errors' if is_error else 'compile'
        out.append((f"{group}/{name.split('/', 1)[1] if is_error else name}",
                    lambda d=data, n=name: compare_compile(d, n, new, old, compile_with, CompileError, Fail)))
    for seed in range(1, nfuzz + 1):
        out.append((f"scanfuzz/seed{seed}", lambda s=seed: scan_fuzz(s, new, old, unique, Fail)))
    for part in ('memory', 'registers', 'immediates', 'jumps'):
        script = ('\n'.join(encoder_script(part)) + '\n').encode()
        out.append((f"encode/{part}", lambda s=script: compare_mode('--encode', s, new, old, unique, Fail, 'command')))
    import fuzz
    for seed in range(1, nfuzz + 1):
        out.append((f"compile/fuzz{seed}", lambda s=seed: compare_compile(
            fuzz.program(s)[0].encode(), f"fuzz{s}", new, old, compile_with, CompileError, Fail)))
    out.append(("fixedpoint", lambda: fixed_point(new, compile_with, CompileError, Fail)))
    return out
