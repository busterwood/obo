#!/usr/bin/env python3
"""obx: a single-pass compiler for a small Oberon-derived language, emitting
x86-64 machine code straight into a static Linux ELF executable.

In the Wirth tradition there is no AST and no intermediate representation:
the recursive-descent parser calls the code generator as each construct is
recognised. Operands are described by Items (constant, memory, register or
condition) so that code emission is delayed until the context forces it, and
forward jumps are resolved through fixup lists.

Usage: obx.py source.obx [-o output]
"""
import os
import struct
import sys
import time

# ----------------------------------------------------------------------------
# Memory layout of the generated executable
# ----------------------------------------------------------------------------
IMAGE_BASE = 0x400000
CODE_BASE = 0x401000          # machine code, directly after the ELF headers
RODATA_BASE = 0x08000000      # string literals and type descriptors
GLOBAL_BASE = 0x0C000000      # module variables (zero-filled)
RT_TEXT = 0x10000000          # runtime jump table + code
RT_DATA = 0x20000000          # runtime data

# Runtime entry slots (see entry.S)
RT_INIT, RT_EXIT, RT_ALLOC_RECORD, RT_ALLOC_ARRAY, RT_PRINT_INT, RT_PRINT_STR, \
    RT_PRINT_BOOL, RT_NEWLINE, RT_CONCAT, RT_STREQ, RT_PANIC_BOUNDS, RT_COLLECT, \
    RT_GC_STATS, RT_INT_TO_STR, RT_APPEND, RT_TRUNCATE, RT_BYTES_TO_STRING, RT_SUBSTR, \
    RT_READ_FILE, RT_WRITE_FILE, RT_ARGS, RT_SET_OUTPUT = range(22)

# ----------------------------------------------------------------------------
# Scanner
# ----------------------------------------------------------------------------
KEYWORDS = {
    'module', 'import', 'pub', 'type', 'var', 'let', 'const', 'fn', 'return',
    'if', 'else', 'while', 'for', 'in', 'break', 'continue', 'defer', 'match',
    'ref', 'record', 'closed', 'new', 'nil', 'true', 'false',
}
# A newline after one of these ends the statement (Go-style semicolon insertion).
SEMI_AFTER = {'ident', 'int', 'char', 'string', 'true', 'false', 'nil', 'return',
              'break', 'continue', ')', ']', '}'}
PUNCT = ['..', '==', '!=', '<=', '>=', '&&', '||', '+=', '-=', '*=', '<<', '>>',
         '(', ')', '{', '}', '[', ']', ',', ';', ':', '.', '=', '<', '>',
         '+', '-', '*', '/', '%', '!', '?', '&', '|', '^', '~']


ESCAPES = {'n': '\n', 't': '\t', 'r': '\r', '\\': '\\', '"': '"', "'": "'", '0': '\0'}


class CompileError(Exception):
    pass


ASCII_LETTERS = set('abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ_')
ASCII_DIGITS = set('0123456789')


class Scanner:
    """Works on bytes (held as a latin-1 str, one character per byte), so
    columns count bytes and identifiers are ASCII, as in the obx version."""

    def __init__(self, src):
        if isinstance(src, bytes):
            src = src.decode('latin-1')
        else:
            src = src.encode('utf-8').decode('latin-1')
        self.src = src
        self.pos = 0
        self.line = 1
        self.col = 1
        self.last = None
        self.sym = None
        self.val = None
        self.next()

    def error(self, msg, line=None, col=None):
        raise CompileError(f"{line or self.tline}:{col or self.tcol}: {msg}")

    def _advance(self, n=1):
        for _ in range(n):
            if self.src[self.pos] == '\n':
                self.line += 1
                self.col = 1
            else:
                self.col += 1
            self.pos += 1

    def next(self):
        src = self.src
        while True:
            while self.pos < len(src) and src[self.pos] in ' \t\r\n':
                if src[self.pos] == '\n' and self.last in SEMI_AFTER:
                    self.tline, self.tcol = self.line, self.col
                    self._advance()
                    self._set(';', None)
                    return
                self._advance()
            if src.startswith('//', self.pos):
                while self.pos < len(src) and src[self.pos] != '\n':
                    self._advance()
                continue
            break
        self.tline, self.tcol = self.line, self.col
        if self.pos >= len(src):
            if self.last in SEMI_AFTER:
                self._set(';', None)
            else:
                self._set('eof', None)
            return
        c = src[self.pos]
        if c in ASCII_LETTERS:
            start = self.pos
            while self.pos < len(src) and (src[self.pos] in ASCII_LETTERS or src[self.pos] in ASCII_DIGITS):
                self._advance()
            word = src[start:self.pos]
            self._set(word if word in KEYWORDS else 'ident', word)
        elif c == '0' and self.pos + 1 < len(src) and src[self.pos + 1] in 'xX':
            self._advance(2)
            start = self.pos
            while self.pos < len(src) and (src[self.pos] in '0123456789abcdefABCDEF_'):
                self._advance()
            digits = src[start:self.pos].replace('_', '')
            if not digits:
                self.error("hex literal needs digits")
            v = int(digits, 16)
            if v >= 2 ** 64:
                self.error("integer literal too large")
            self._set('int', v - 2 ** 64 if v >= 2 ** 63 else v)
        elif c in ASCII_DIGITS:
            start = self.pos
            while self.pos < len(src) and (src[self.pos] in ASCII_DIGITS or src[self.pos] == '_'):
                self._advance()
            v = int(src[start:self.pos].replace('_', ''))
            if v >= 2 ** 63:
                self.error("integer literal too large")
            self._set('int', v)
        elif c == "'":
            self._advance()
            if self.pos >= len(src) or src[self.pos] in "'\n":
                self.error("empty character literal")
            ch = src[self.pos]
            if ch == '\\':
                self._advance()
                ch = ESCAPES.get(src[self.pos]) if self.pos < len(src) else None
                if ch is None:
                    self.error("unknown escape in character literal")
            if ord(ch) >= 128:
                self.error("character literal must be a single byte")
            self._advance()
            if self.pos >= len(src) or src[self.pos] != "'":
                self.error("unterminated character literal")
            self._advance()
            self._set('char', ord(ch))
        elif c == '"':
            self._advance()
            out = []
            while True:
                if self.pos >= len(src) or src[self.pos] == '\n':
                    self.error("unterminated string")
                ch = src[self.pos]
                if ch == '"':
                    self._advance()
                    break
                if ch == '\\':
                    self._advance()
                    if self.pos >= len(src) or src[self.pos] == '\n':
                        self.error("unterminated string")
                    esc = src[self.pos]
                    out.append(ESCAPES.get(esc, esc))
                else:
                    out.append(ch)
                self._advance()
            self._set('string', ''.join(out).encode('latin-1'))
        else:
            for p in PUNCT:
                if src.startswith(p, self.pos):
                    self._advance(len(p))
                    self._set(p, None)
                    return
            shown = f"'{c}'" if 33 <= ord(c) <= 126 else f"0x{ord(c):02X}"
            self.error(f"unexpected character {shown}")

    def _set(self, sym, val):
        self.sym = sym
        self.val = val
        self.last = sym


# ----------------------------------------------------------------------------
# Types
# ----------------------------------------------------------------------------
class Type:
    def __init__(self, kind, name=None, base=None):
        self.kind = kind          # int bool string void nil record closed array opt
        self.name = name
        self.base = base          # element type (array) or underlying type (opt)
        self.fields = {}          # record: name -> (offset, type)
        self.parent = None        # record: closed parent type, if a variant
        self.variants = []        # closed: list of variant record types
        self.desc = 0             # record: address of type descriptor
        self.incomplete = False   # declared by 'type Name' and not yet defined
        self.line = 0

    def __repr__(self):
        k = self.kind
        if k == 'array':
            return f"[]{self.base!r}"
        if k == 'opt':
            return f"?{self.base!r}"
        return self.name or k


T_INT = Type('int', 'int')
T_BOOL = Type('bool', 'bool')
T_STRING = Type('string', 'string')
T_BYTE = Type('byte', 'byte')
T_VOID = Type('void', 'void')
T_NIL = Type('nil', 'nil')


def is_int(t):
    """byte values take part in arithmetic as ints."""
    return t.kind in ('int', 'byte')


def is_ref(t):
    """Types represented as a heap pointer (null allowed only for opt and string)."""
    return t.kind in ('record', 'closed', 'array', 'opt', 'string')


def non_null(t):
    return t.kind in ('record', 'closed', 'array')


def same(a, b):
    if a is b:
        return True
    if a.kind != b.kind:
        return False
    if a.kind in ('array', 'opt'):
        return same(a.base, b.base)
    return a.kind in ('int', 'byte', 'bool', 'string', 'void', 'nil')


def assignable(dst, src):
    if same(dst, src):
        return True
    if dst.kind == 'int' and src.kind == 'byte':
        return True
    if dst.kind == 'closed' and src.kind == 'record' and src.parent is dst:
        return True
    if dst.kind == 'opt':
        if src.kind == 'nil':
            return True
        if src.kind == 'opt':
            return assignable(dst.base, src.base)
        return assignable(dst.base, src)
    return False


# ----------------------------------------------------------------------------
# Symbols and items
# ----------------------------------------------------------------------------
class Sym:
    def __init__(self, kind, name, typ=None):
        self.kind = kind          # var let const type fn builtin
        self.name = name
        self.type = typ
        self.storage = None       # 'local' or 'global'
        self.offset = 0           # rbp offset or absolute address
        self.value = None         # const value
        self.addr = None          # fn: code address once defined
        self.fixups = []          # fn: call sites awaiting the address
        self.params = []          # fn: parameter types
        self.ret = T_VOID


RAX, RCX, RDX, RBX, RSP, RBP, RSI, RDI = range(8)
R8, R9, R10, R11, R12, R13, R14, R15 = range(8, 16)
POOL = [RBX, R8, R9, R10, R11, R12, R13, R14, R15]

CC_E, CC_NE, CC_L, CC_GE, CC_LE, CC_G = 4, 5, 12, 13, 14, 15
CC_B = 2
REL = {'==': CC_E, '!=': CC_NE, '<': CC_L, '>=': CC_GE, '<=': CC_LE, '>': CC_G}
SWAP = {CC_E: CC_E, CC_NE: CC_NE, CC_L: CC_G, CC_G: CC_L, CC_LE: CC_GE, CC_GE: CC_LE}


class Item:
    """Describes where a value currently is, so emission can be delayed."""

    def __init__(self, mode, typ):
        self.mode = mode          # const mem reg cond
        self.type = typ
        self.val = 0              # const
        self.base = None          # mem: base register, or None for absolute
        self.index = None         # mem: index register (scaled by 8)
        self.disp = 0             # mem: displacement or absolute address
        self.r = None             # reg
        self.cc = None            # cond: condition code meaning "true"
        self.T = []               # cond: jumps to the true target
        self.F = []               # cond: jumps to the false target
        self.sym = None           # set when the item is a plain variable
        self.nt = []              # narrowings valid when the condition holds
        self.nf = []              # narrowings valid when it does not
        self.readonly = False
        self.width = 8            # mem/elem: bytes per access (8, or 1 for bytes)
        self.scale = 8            # mem: index scale
        self.line = 0             # elem: source line, for the bounds trap


def fits32(v):
    return -2 ** 31 <= v < 2 ** 31


def wrap64(v):
    v &= (1 << 64) - 1
    return v - (1 << 64) if v >= 1 << 63 else v


def tdiv(a, b):
    q = abs(a) // abs(b)
    return q if (a < 0) == (b < 0) else -q


# ----------------------------------------------------------------------------
# Compiler
# ----------------------------------------------------------------------------
class Compiler:
    def __init__(self, src):
        self.s = Scanner(src)
        self.code = bytearray()
        self.rodata = bytearray()
        self.strings = {}
        self.gsize = 0
        self.free = list(POOL)
        self.depth = 0            # bytes pushed beyond the frame (for alignment)
        self.universe = {}
        self.globals = {}
        self.blocks = []          # innermost last: {'syms', 'defers', 'nmark'}
        self.narrow = []          # [sym, type, loopdepth, alive]
        self.loops = []
        self.loopdepth = 0
        self.fn = None
        self.fn_block = 0
        self.frame = 0
        self.in_defer = False
        self.noreturn = False
        for name, t in (('int', T_INT), ('byte', T_BYTE), ('bool', T_BOOL), ('string', T_STRING)):
            self.universe[name] = Sym('type', name, t)
        for name in ('print', 'println', 'len', 'str', 'gc', 'gcstats', 'exit',
                     'append', 'truncate', 'substr', 'eprint', 'eprintln',
                     'readFile', 'writeFile', 'args'):
            self.universe[name] = Sym('builtin', name)

    # ---------------------------------------------------------------- errors
    def error(self, msg):
        self.s.error(msg)

    def expect(self, sym, what=None):
        if self.s.sym != sym:
            got = self.s.sym if self.s.sym not in ('ident', 'int', 'string') else repr(self.s.val)
            if got == ';':
                got = 'end of line'
            self.error(f"expected {what or repr(sym)}, found {got}")
        self.s.next()

    def ident(self):
        if self.s.sym != 'ident':
            self.error("expected identifier")
        name = self.s.val
        self.s.next()
        return name

    def skip_semis(self):
        while self.s.sym == ';':
            self.s.next()

    # ------------------------------------------------------------ x64 encoder
    def pc(self):
        return len(self.code)

    def b(self, *bs):
        self.code.extend(bs)

    def i32(self, v):
        self.code.extend(struct.pack('<i', v))

    def op(self, opc, reg, rm, w=1, imm=b'', byte=False):
        """Emit [REX] opcode ModRM [SIB] [disp] [imm]; rm is a register or (base, index, disp)."""
        if isinstance(rm, int):
            x, bb = 0, rm
            tail = bytes([0xC0 | (reg & 7) << 3 | (rm & 7)])
        else:
            base, index, disp = rm[0], rm[1], rm[2]
            ss = {1: 0, 2: 1, 4: 2, 8: 3}[rm[3] if len(rm) > 3 else 8]
            x, bb = index or 0, base or 0
            if base is None and index is None:
                tail = bytes([0x04 | (reg & 7) << 3, 0x25]) + struct.pack('<i', disp)
            else:
                if disp == 0 and (base & 7) != 5:
                    mod, d = 0, b''
                elif -128 <= disp < 128:
                    mod, d = 1, struct.pack('<b', disp)
                else:
                    mod, d = 2, struct.pack('<i', disp)
                if index is not None:
                    tail = bytes([mod << 6 | (reg & 7) << 3 | 4, ss << 6 | (index & 7) << 3 | (base & 7)]) + d
                elif (base & 7) == 4:
                    tail = bytes([mod << 6 | (reg & 7) << 3 | 4, 0x24]) + d
                else:
                    tail = bytes([mod << 6 | (reg & 7) << 3 | (base & 7)]) + d
        rex = 0x40 | w << 3 | (reg >> 3 & 1) << 2 | (x >> 3 & 1) << 1 | (bb >> 3 & 1)
        # spl, bpl, sil and dil exist only with a REX prefix; al..bl never need one
        if rex != 0x40 or (byte and ((isinstance(rm, int) and 4 <= rm <= 7) or (opc == b'\x88' and 4 <= reg <= 7))):
            self.code.append(rex)
        self.code.extend(opc)
        self.code.extend(tail)
        self.code.extend(imm)

    def mov_rr(self, d, s):
        if d != s:
            self.op(b'\x8b', d, s)

    def mov_ri(self, r, v):
        if 0 <= v < 2 ** 32:
            if r >= 8:
                self.b(0x41)
            self.b(0xB8 + (r & 7))
            self.code.extend(struct.pack('<I', v))
        elif fits32(v):
            self.op(b'\xc7', 0, r, imm=struct.pack('<i', v))
        else:
            self.b(0x48 | (r >> 3), 0xB8 + (r & 7))
            self.code.extend(struct.pack('<q', v))

    def alu_ri(self, o, rm, v):
        if -128 <= v < 128:
            self.op(b'\x83', o, rm, imm=struct.pack('<b', v))
        else:
            self.op(b'\x81', o, rm, imm=struct.pack('<i', v))

    def push(self, r):
        if r >= 8:
            self.b(0x41)
        self.b(0x50 + (r & 7))
        self.depth += 8

    def pop(self, r):
        if r >= 8:
            self.b(0x41)
        self.b(0x58 + (r & 7))
        self.depth -= 8

    def jcc(self, cc):
        self.b(0x0F, 0x80 + cc)
        self.i32(0)
        return self.pc() - 4

    def jmp(self):
        self.b(0xE9)
        self.i32(0)
        return self.pc() - 4

    def fix(self, at, target):
        self.code[at:at + 4] = struct.pack('<i', target - (at + 4))

    def fixlink(self, links, target=None):
        t = self.pc() if target is None else target
        for at in links:
            self.fix(at, t)

    def jmp_to(self, target):
        self.fix(self.jmp(), target)

    def call_abs(self, addr):
        self.b(0xE8)
        self.i32(addr - (CODE_BASE + self.pc() + 4))

    # --------------------------------------------------------- data sections
    def string_lit(self, data):
        if data not in self.strings:
            while len(self.rodata) % 8:
                self.rodata.append(0)
            self.strings[data] = RODATA_BASE + len(self.rodata)
            self.rodata.extend(struct.pack('<Q', len(data)) + data)
        return self.strings[data]

    def descriptor(self, size, ptr_offsets):
        while len(self.rodata) % 8:
            self.rodata.append(0)
        addr = RODATA_BASE + len(self.rodata)
        self.rodata.extend(struct.pack('<QQ', size, len(ptr_offsets)))
        for off in ptr_offsets:
            self.rodata.extend(struct.pack('<Q', off))
        return addr

    # ---------------------------------------------------- registers and items
    def alloc(self):
        if not self.free:
            self.error("expression too complex (out of registers)")
        return self.free.pop(0)

    def release(self, r):
        if r in POOL and r not in self.free:
            self.free.append(r)

    def release_item(self, x):
        if x.mode == 'reg':
            self.release(x.r)
        elif x.mode in ('mem', 'elem'):
            if x.base is not None and x.base != RBP:
                self.release(x.base)
            if x.index is not None:
                self.release(x.index)

    def in_use(self):
        return [r for r in POOL if r not in self.free]

    def mem(self, x):
        return (x.base, x.index, x.disp, x.scale)

    def bounds_check(self, idx, length_operand, line):
        """Trap unless 0 <= idx < length; the unsigned compare catches negatives."""
        self.op(b'\x3b', idx, length_operand)
        self.b(0x72, 14)                                      # jb ok
        self.trap_bounds(line)

    def trap_bounds(self, line):
        """14 bytes that report an index error at line and never return."""
        self.b(0xBF)
        self.i32(line)                                        # mov edi, line
        self.b(0x48, 0x83, 0xE4, 0xF0)                        # and rsp, -16
        self.call_abs(RT_TEXT + 8 * RT_PANIC_BOUNDS)

    def resolve(self, x):
        """Turn an array element item into a memory operand: the bounds check
        and the load of the data pointer happen now, at the moment of access,
        so evaluating other operands first (which may grow the array) is safe."""
        if x.mode == 'elem':
            self.bounds_check(x.index, (x.base, None, 0), x.line)
            self.op(b'\x8b', x.base, (x.base, None, 16))      # header -> data block
            x.mode, x.disp, x.scale = 'mem', 8, x.width
        return x

    def make_const(self, typ, v):
        x = Item('const', typ)
        x.val = v
        return x

    def make_reg(self, typ, r):
        x = Item('reg', typ)
        x.r = r
        return x

    def load(self, x):
        if x.mode == 'reg':
            return x
        if x.mode == 'const':
            r = self.alloc()
            self.mov_ri(r, x.val)
        elif x.mode in ('mem', 'elem'):
            self.resolve(x)
            self.release_item(x)
            r = self.alloc()
            if x.width == 1:
                self.op(b'\x0f\xb6', r, self.mem(x))         # movzx r64, byte
            else:
                self.op(b'\x8b', r, self.mem(x))
        elif x.mode == 'cond':
            r = self.alloc()
            if not x.T and not x.F:
                self.op(b'\x0f' + bytes([0x90 + x.cc]), 0, r, w=0, byte=True)
                self.op(b'\x0f\xb6', r, r, byte=True)
            else:
                x.F.append(self.jcc(x.cc ^ 1))
                self.fixlink(x.T)
                self.mov_ri(r, 1)
                j = self.jmp()
                self.fixlink(x.F)
                self.mov_ri(r, 0)
                self.fixlink([j])
        y = self.make_reg(x.type, r)
        return y

    def load_cond(self, x):
        if x.type.kind != 'bool':
            self.error(f"condition must be bool, not {x.type!r}")
        if x.mode == 'cond':
            return x
        if x.mode == 'mem':
            self.release_item(x)
            self.alu_ri(7, self.mem(x), 0)
        else:
            x = self.load(x)
            self.op(b'\x85', x.r, x.r)
            self.release(x.r)
        y = Item('cond', T_BOOL)
        y.cc = CC_NE
        return y

    def false_jump(self, x):
        """Jump to the false target; fall through when true. Returns the false links."""
        x.F.append(self.jcc(x.cc ^ 1))
        self.fixlink(x.T)
        return x.F

    def store(self, dst, src):
        if src.mode != 'const':
            src = self.load(src)
        self.resolve(dst)
        if dst.width == 1:
            if src.mode == 'const':
                self.op(b'\xc6', 0, self.mem(dst), w=0, imm=bytes([src.val & 255]))
            else:
                self.op(b'\x88', src.r, self.mem(dst), w=0, byte=True)
        elif src.mode == 'const' and fits32(src.val):
            self.op(b'\xc7', 0, self.mem(dst), imm=struct.pack('<i', src.val))
        else:
            src = self.load(src)
            self.op(b'\x89', src.r, self.mem(dst))
        if src.mode == 'reg':
            self.release(src.r)
        self.release_item(dst)

    def materialise(self, x):
        """Make sure an operand survives evaluation of a later operand."""
        if x.mode in ('mem', 'elem', 'cond'):
            return self.load(x)
        return x

    # ---------------------------------------------------------------- calls
    def save_regs(self):
        saved = self.in_use()
        for r in saved:
            self.push(r)
        self.free = list(POOL)
        return saved

    def restore_regs(self, saved):
        for r in reversed(saved):
            self.pop(r)
        self.free = [r for r in POOL if r not in saved]

    def rt_call(self, slot, args, ret=None):
        """Call a runtime routine with the C ABI. Live registers (including the
        arguments) are pushed first so the collector sees every pointer."""
        regs = []
        for a in args:
            a = self.load(a)
            regs.append(a.r)
        saved = self.in_use()
        for r in saved:
            self.push(r)
        for r, abi in zip(regs, (RDI, RSI, RDX, RCX)):
            self.mov_rr(abi, r)
        pad = self.depth % 16
        if pad:
            self.alu_ri(5, RSP, 8)
            self.depth += 8
        self.call_abs(RT_TEXT + 8 * slot)
        if pad:
            self.alu_ri(0, RSP, 8)
            self.depth -= 8
        for r in reversed(saved):
            self.pop(r)
        for r in regs:
            self.release(r)
        if ret is None:
            return None
        r = self.alloc()
        self.mov_rr(r, RAX)
        return self.make_reg(ret, r)

    def call_fn(self, f):
        saved = self.save_regs()
        n = len(f.params)
        pad = (self.depth + 8 * n) % 16
        if pad:
            self.alu_ri(5, RSP, 8)
            self.depth += 8
        self.expect('(')
        for i, pt in enumerate(f.params):
            if self.s.sym == ')':
                self.error(f"{f.name} takes {n} argument(s), not {i}")
            if i:
                self.expect(',')
            a = self.expression()
            self.check_assign(pt, a, f"argument {i + 1} of {f.name}")
            a = self.load(a)
            self.push(a.r)
            self.release(a.r)
        if self.s.sym != ')':
            self.error(f"{f.name} takes {n} argument(s), not more")
        self.expect(')')
        self.b(0xE8)
        self.i32(0)
        at = self.pc() - 4
        if f.addr is not None:
            self.fix(at, f.addr)
        else:
            f.fixups.append(at)
        if n or pad:
            self.alu_ri(0, RSP, 8 * n + pad)
            self.depth -= 8 * n + pad
        self.restore_regs(saved)
        if f.ret is T_VOID:
            return Item('void', T_VOID)
        r = self.alloc()
        self.mov_rr(r, RAX)
        return self.make_reg(f.ret, r)

    # ------------------------------------------------------- scopes, narrowing
    def lookup(self, name):
        for blk in reversed(self.blocks):
            if name in blk['syms']:
                return blk['syms'][name]
        if name in self.globals:
            return self.globals[name]
        if name in self.universe:
            return self.universe[name]
        self.error(f"undeclared identifier '{name}' (declare before use; "
                   f"use a forward heading for mutual recursion)")

    def declare(self, sym):
        scope = self.blocks[-1]['syms'] if self.blocks else self.globals
        if sym.name in scope:
            self.error(f"'{sym.name}' is already declared in this scope")
        scope[sym.name] = sym
        return sym

    def open_block(self):
        self.blocks.append({'syms': {}, 'defers': [], 'nmark': len(self.narrow)})

    def close_block(self, terminated):
        blk = self.blocks.pop()
        if not terminated:
            for d in reversed(blk['defers']):
                self.call_local(d)
        del self.narrow[blk['nmark']:]

    def call_local(self, target):
        self.b(0xE8)
        self.i32(target - (self.pc() + 4))

    def run_defers(self, down_to):
        for blk in reversed(self.blocks[down_to:]):
            for d in reversed(blk['defers']):
                self.call_local(d)

    def new_local(self, name, typ, kind='var'):
        self.frame += 8
        s = Sym(kind, name, typ)
        s.storage = 'local'
        s.offset = -self.frame
        return self.declare(s) if name else s

    def var_type(self, sym):
        for entry in reversed(self.narrow):
            if entry[0] is sym and entry[3]:
                return entry[1]
        return sym.type

    def push_narrowings(self, lst):
        for sym, t in lst:
            self.narrow.append([sym, t, self.loopdepth, True])

    def widen(self, sym):
        for entry in self.narrow:
            if entry[0] is sym and entry[3]:
                if entry[2] != self.loopdepth:
                    self.error(f"'{sym.name}' may become nil here, but it was checked "
                               f"outside this loop; check it inside the loop instead")
                entry[3] = False

    # ---------------------------------------------------------------- types
    def parse_type(self):
        if self.s.sym == '?':
            self.s.next()
            base = self.parse_type()
            if not non_null(base):
                self.error(f"only reference types can be optional, not {base!r}")
            return Type('opt', base=base)
        if self.s.sym == '[':
            self.s.next()
            self.expect(']')
            return Type('array', base=self.parse_type())
        if self.s.sym == 'ref' or self.s.sym == 'closed':
            self.error("record and closed types must be declared with 'type'")
        name = self.ident()
        sym = self.lookup(name)
        if sym.kind != 'type':
            self.error(f"'{name}' is not a type")
        return sym.type

    def field_list(self, rec):
        """{ a, b: int; next: ?Node } -> fields at 8-byte offsets."""
        self.expect('{')
        self.skip_semis()
        offset = 0
        while self.s.sym != '}':
            names = [self.ident()]
            while self.s.sym == ',':
                self.s.next()
                names.append(self.ident())
            self.expect(':')
            t = self.parse_type()
            for n in names:
                if n in rec.fields:
                    self.error(f"duplicate field '{n}'")
                rec.fields[n] = (offset, t)
                offset += 8
            if self.s.sym != '}':
                self.expect(';', "';' or newline")
            self.skip_semis()
        self.expect('}')
        ptrs = [off for off, t in rec.fields.values() if is_ref(t)]
        rec.desc = self.descriptor(offset, ptrs)

    def type_decl(self):
        """type Name = ...   or a forward declaration 'type Name', which lets
        record types refer to each other. A forward-declared type can be used
        in declarations straight away, but its fields and variants only once
        it has been defined."""
        line = self.s.tline
        name = self.ident()
        prior = self.globals.get(name)
        fwd = prior.type if prior is not None and prior.kind == 'type' and prior.type.incomplete else None
        if self.s.sym != '=':
            t = Type('record', name)
            t.incomplete, t.line = True, line
            self.declare(Sym('type', name, t))
            return
        self.expect('=')
        if fwd is None and self.s.sym in ('ref', 'closed') and prior is not None:
            self.error(f"'{name}' is already declared in this scope")
        if self.s.sym == 'ref':
            self.s.next()
            self.expect('record')
            rec = fwd or Type('record', name)
            if fwd is None:
                self.declare(Sym('type', name, rec))  # visible in its own fields
            self.field_list(rec)
            rec.incomplete = False
        elif self.s.sym == 'closed':
            self.s.next()
            ct = fwd or Type('closed', name)
            ct.kind, ct.incomplete = 'closed', False
            if fwd is None:
                self.declare(Sym('type', name, ct))
            self.expect('{')
            self.skip_semis()
            while self.s.sym != '}':
                vname = self.ident()
                v = Type('record', vname)
                v.parent = ct
                self.declare(Sym('type', vname, v))
                self.field_list(v)
                ct.variants.append(v)
                self.skip_semis()
            self.expect('}')
            if not ct.variants:
                self.error("a closed type needs at least one variant")
        else:
            if fwd is not None:
                self.error(f"'{name}' was declared as a forward type: define it as a ref record or closed type")
            self.declare(Sym('type', name, self.parse_type()))

    def check_assign(self, dst, x, what):
        if x.type is T_VOID:
            self.error(f"{what}: expression has no value")
        if dst.kind == 'byte' and x.mode == 'const' and x.type.kind == 'int' and 0 <= x.val <= 255:
            return
        if not assignable(dst, x.type):
            hint = ""
            if x.type.kind == 'nil' and non_null(dst):
                hint = f" ({dst!r} can never be nil; declare it as ?{dst!r})"
            elif dst.kind != 'opt' and x.type.kind == 'opt':
                hint = " (it may be nil: check it with '!= nil' first)"
            self.error(f"{what}: cannot use {x.type!r} as {dst!r}{hint}")

    # ----------------------------------------------------------- expressions
    def designator(self):
        name = self.ident()
        sym = self.lookup(name)
        if sym.kind == 'builtin':
            x = self.builtin(name)
        elif sym.kind == 'fn':
            if self.s.sym != '(':
                self.error("functions can only be called")
            x = self.call_fn(sym)
        elif sym.kind == 'const':
            x = self.make_const(sym.type, sym.value)
        elif sym.kind in ('var', 'let'):
            x = Item('mem', self.var_type(sym) if sym.storage == 'local' else sym.type)
            if sym.storage == 'local':
                x.base, x.disp = RBP, sym.offset
            else:
                x.disp = sym.offset
            x.sym = sym
            x.readonly = sym.kind == 'let'
        elif sym.kind == 'type' and self.s.sym == '(' and sym.type in (T_INT, T_BYTE, T_STRING):
            x = self.conversion(sym.type)
        else:
            self.error(f"'{name}' is a type, not a value (use 'new {name}{{...}}' to make one)")
        if self.s.sym == '(' and sym.kind in ('var', 'let', 'const'):
            self.error(f"'{name}' is not a function")
        while self.s.sym in ('.', '['):
            if x.type.kind == 'opt':
                self.error(f"'{self.describe(x)}' may be nil: check it with '!= nil' first")
            if self.s.sym == '.':
                self.s.next()
                fname = self.ident()
                if x.type.incomplete:
                    self.error(f"{x.type!r} is declared but not yet defined")
                if x.type.kind == 'closed':
                    self.error(f"{x.type!r} is a closed type: use 'match' to reach its fields")
                if x.type.kind != 'record':
                    self.error(f"{x.type!r} has no fields")
                if fname not in x.type.fields:
                    self.error(f"{x.type!r} has no field '{fname}'")
                off, ft = x.type.fields[fname]
                p = self.load(x)
                x = Item('mem', ft)
                x.base, x.disp = p.r, off
            else:
                self.s.next()
                line = self.s.tline
                kind = x.type.kind
                if kind not in ('array', 'string'):
                    self.error(f"{x.type!r} cannot be indexed")
                a = self.load(x)
                i = self.expression()
                if not is_int(i.type):
                    self.error("array index must be int")
                i = self.load(i)
                self.expect(']')
                if kind == 'string':
                    # a string is [len][bytes]; "" may be a null pointer, which has length 0
                    self.op(b'\x85', a.r, a.r)
                    self.b(0x75, 14)                          # jnz: not null
                    self.trap_bounds(line)
                    self.bounds_check(i.r, (a.r, None, 0), line)
                    x = Item('mem', T_BYTE)
                    x.base, x.index, x.disp, x.scale, x.width = a.r, i.r, 8, 1, 1
                    x.readonly = True
                else:
                    # checked and located only when accessed: see resolve()
                    elem = x.type.base
                    x = Item('elem', elem)
                    x.base, x.index, x.line = a.r, i.r, line
                    x.width = 1 if elem.kind == 'byte' else 8
        return x

    def conversion(self, t):
        self.expect('(')
        a = self.expression()
        self.expect(')')
        if t is T_STRING:
            if a.type.kind != 'array' or a.type.base.kind != 'byte':
                self.error(f"string() needs a []byte, not {a.type!r}")
            return self.rt_call(RT_BYTES_TO_STRING, [a], T_STRING)
        if not is_int(a.type):
            self.error(f"{t!r}() needs an int or byte, not {a.type!r}")
        if a.mode == 'const':
            return self.make_const(t, a.val & 255 if t is T_BYTE else a.val)
        a = self.load(a)
        if t is T_BYTE and a.type.kind != 'byte':
            self.alu_ri(4, a.r, 255)                          # keep the low byte
        a.type = t
        return a

    def describe(self, x):
        return x.sym.name if x.sym else "value"

    def builtin(self, name):
        if name in ('print', 'println', 'eprint', 'eprintln'):
            to_stderr = name.startswith('e')
            if to_stderr:
                self.rt_call(RT_SET_OUTPUT, [self.make_const(T_INT, 2)])
            self.expect('(')
            first = True
            while self.s.sym != ')':
                if not first:
                    self.expect(',')
                first = False
                a = self.expression()
                k = a.type.kind
                slot = {'int': RT_PRINT_INT, 'byte': RT_PRINT_INT, 'bool': RT_PRINT_BOOL,
                        'string': RT_PRINT_STR}.get(k)
                if slot is None:
                    self.error(f"cannot print {a.type!r}")
                self.rt_call(slot, [a])
            self.expect(')')
            if name.endswith('ln'):
                self.rt_call(RT_NEWLINE, [])
            if to_stderr:
                self.rt_call(RT_SET_OUTPUT, [self.make_const(T_INT, 1)])
            return Item('void', T_VOID)
        if name == 'args':
            self.expect('(')
            self.expect(')')
            return self.rt_call(RT_ARGS, [], Type('array', base=T_STRING))
        if name in ('readFile', 'writeFile'):
            self.expect('(')
            p = self.expression()
            if p.type.kind != 'string':
                self.error(f"{name} needs a string path, not {p.type!r}")
            if name == 'readFile':
                self.expect(')')
                return self.rt_call(RT_READ_FILE, [p], Type('opt', base=Type('array', base=T_BYTE)))
            p = self.materialise(p)
            self.expect(',')
            d = self.expression()
            if d.type.kind != 'array' or d.type.base.kind != 'byte':
                self.error(f"writeFile needs a []byte, not {d.type!r}")
            d = self.materialise(d)
            self.expect(',')
            e = self.expression()
            if e.type.kind != 'bool':
                self.error("writeFile needs a bool saying whether the file is executable")
            self.expect(')')
            return self.rt_call(RT_WRITE_FILE, [p, d, e], T_BOOL)
        if name in ('append', 'truncate', 'substr'):
            line = self.s.tline
            self.expect('(')
            a = self.expression()
            want = 'string' if name == 'substr' else 'array'
            if a.type.kind != want:
                self.error(f"{name} needs a{'n' if want == 'array' else ''} {want}, not {a.type!r}")
            a = self.materialise(a)
            self.expect(',')
            b = self.expression()
            if name == 'append':
                self.check_assign(a.type.base, b, "appended value")
                self.expect(')')
                self.rt_call(RT_APPEND, [a, b])
                return Item('void', T_VOID)
            if not is_int(b.type):
                self.error(f"{name} needs int positions")
            if name == 'truncate':
                self.expect(')')
                self.rt_call(RT_TRUNCATE, [a, b, self.make_const(T_INT, line)])
                return Item('void', T_VOID)
            b = self.materialise(b)
            self.expect(',')
            c = self.expression()
            if not is_int(c.type):
                self.error(f"{name} needs int positions")
            self.expect(')')
            return self.rt_call(RT_SUBSTR, [a, b, c, self.make_const(T_INT, line)], T_STRING)
        if name in ('gc', 'gcstats'):
            self.expect('(')
            self.expect(')')
            self.rt_call(RT_COLLECT if name == 'gc' else RT_GC_STATS, [])
            return Item('void', T_VOID)
        self.expect('(')
        a = self.expression()
        self.expect(')')
        if name == 'exit':
            if a.type.kind != 'int':
                self.error("exit needs an int")
            self.rt_call(RT_EXIT, [a])
            self.noreturn = True                              # control never comes back
            return Item('void', T_VOID)
        if name == 'str':
            if not is_int(a.type):
                self.error("str needs an int")
            if a.mode == 'const':
                return self.make_const(T_STRING, self.string_lit(str(a.val).encode()))
            return self.rt_call(RT_INT_TO_STR, [a], T_STRING)
        # len
        if a.type.kind == 'string':
            if a.mode == 'const':
                n = struct.unpack_from('<Q', self.rodata, a.val - RODATA_BASE)[0]
                return self.make_const(T_INT, n)
            a = self.load(a)
            self.op(b'\x85', a.r, a.r)
            self.b(0x74, 0)                                   # jz past the load: "" has length 0
            at = self.pc()
            self.op(b'\x8b', a.r, (a.r, None, 0))
            self.code[at - 1] = self.pc() - at
            return self.make_reg(T_INT, a.r)
        if a.type.kind != 'array':
            self.error(f"len needs a string or array, not {a.type!r}")
        a = self.load(a)
        self.op(b'\x8b', a.r, (a.r, None, 0))
        return self.make_reg(T_INT, a.r)

    def new_expr(self):
        self.s.next()
        if self.s.sym == '[':
            t = self.parse_type()
            self.expect('(')
            n = self.expression()
            if not is_int(n.type):
                self.error("array length must be int")
            if non_null(t.base) and not (n.mode == 'const' and n.val == 0):
                self.error(f"array elements of type {t.base!r} would start as nil; "
                           f"use []?{t.base!r}, or start empty with new []{t.base!r}(0) and append")
            self.expect(')')
            kind = 4 if t.base.kind == 'byte' else 2 if is_ref(t.base) else 1
            return self.rt_call(RT_ALLOC_ARRAY, [n, self.make_const(T_INT, kind)], t)
        name = self.ident()
        sym = self.lookup(name)
        if sym.kind == 'type' and sym.type.incomplete:
            self.error(f"'{name}' is declared but not yet defined")
        if sym.kind != 'type' or sym.type.kind != 'record':
            self.error(f"'{name}' is not a record type")
        t = sym.type
        p = self.rt_call(RT_ALLOC_RECORD, [self.make_const(T_INT, t.desc)], t)
        given = set()
        self.expect('{')
        self.skip_semis()
        while self.s.sym != '}':
            fname = self.ident()
            if fname not in t.fields:
                self.error(f"{name} has no field '{fname}'")
            if fname in given:
                self.error(f"field '{fname}' given twice")
            given.add(fname)
            self.expect(':')
            off, ft = t.fields[fname]
            v = self.expression()
            self.check_assign(ft, v, f"field '{fname}'")
            dst = Item('mem', ft)
            dst.base, dst.disp = p.r, off
            if v.mode == 'const' and fits32(v.val):
                self.op(b'\xc7', 0, self.mem(dst), imm=struct.pack('<i', v.val))
            else:
                v = self.load(v)
                self.op(b'\x89', v.r, self.mem(dst))
                self.release(v.r)
            if self.s.sym in (',', ';'):
                self.s.next()
            elif self.s.sym != '}':
                self.expect(',', "',' or '}'")
            self.skip_semis()
        self.expect('}')
        missing = [f for f, (_, ft) in t.fields.items() if non_null(ft) and f not in given]
        if missing:
            self.error(f"{name} needs a value for {', '.join(missing)} (it cannot be nil)")
        return p

    def factor(self):
        sym = self.s.sym
        if sym == 'int':
            v = self.s.val
            self.s.next()
            return self.make_const(T_INT, v)
        if sym == 'string':
            addr = self.string_lit(self.s.val)
            self.s.next()
            return self.make_const(T_STRING, addr)
        if sym in ('true', 'false'):
            self.s.next()
            return self.make_const(T_BOOL, 1 if sym == 'true' else 0)
        if sym == 'nil':
            self.s.next()
            return self.make_const(T_NIL, 0)
        if sym == '(':
            self.s.next()
            x = self.expression()
            self.expect(')')
            return x
        if sym == 'new':
            return self.new_expr()
        if sym == '!':
            self.s.next()
            x = self.factor()
            if x.type.kind != 'bool':
                self.error("'!' needs a bool")
            if x.mode == 'const':
                return self.make_const(T_BOOL, 1 - x.val)
            x = self.load_cond(x)
            y = Item('cond', T_BOOL)
            y.cc, y.T, y.F, y.nt, y.nf = x.cc ^ 1, x.F, x.T, x.nf, x.nt
            return y
        if sym in ('-', '~'):
            self.s.next()
            x = self.factor()
            if not is_int(x.type):
                self.error(f"'{sym}' needs an int")
            if x.mode == 'const':
                return self.make_const(T_INT, wrap64(-x.val if sym == '-' else ~x.val))
            x = self.load(x)
            self.op(b'\xf7', 3 if sym == '-' else 2, x.r)    # neg / not
            x.type = T_INT
            return x
        if sym == 'char':
            v = self.s.val
            self.s.next()
            return self.make_const(T_BYTE, v)
        if sym == 'ident':
            return self.designator()
        self.error("expected an expression")

    MULOPS = ('*', '/', '%', '<<', '>>', '&')
    ADDOPS = ('+', '-', '|', '^')
    ALU = {'+': 0, '|': 1, '&': 4, '-': 5, '^': 6}        # x64 group-1 opcode extensions

    def fold(self, o, a, b):
        if o in ('/', '%') and b == 0:
            self.error("division by zero")
        return wrap64({'+': lambda: a + b, '-': lambda: a - b, '*': lambda: a * b,
                       '/': lambda: tdiv(a, b), '%': lambda: a - tdiv(a, b) * b,
                       '&': lambda: a & b, '|': lambda: a | b, '^': lambda: a ^ b,
                       '<<': lambda: a << (b & 63), '>>': lambda: a >> (b & 63)}[o]())

    def operand(self, y):
        """A right operand as a register or memory operand."""
        if y.mode not in ('mem', 'reg'):
            y = self.load(y)
        return y, (y.r if y.mode == 'reg' else self.mem(y))

    def binop(self, o, x, y):
        """Integer arithmetic, bitwise operators and shifts (count taken mod 64)."""
        if not is_int(x.type) or not is_int(y.type):
            self.error(f"'{o}' needs ints")
        if x.mode == 'const' and y.mode == 'const':
            return self.make_const(T_INT, self.fold(o, x.val, y.val))
        if x.mode == 'const' and o in ('+', '*', '&', '|', '^'):
            x, y = y, x
        if y.mode == 'elem' or (y.mode == 'mem' and y.width == 1):
            y = self.load(y)
        x = self.load(x)
        if o == '*':
            if y.mode == 'const' and fits32(y.val):
                self.op(b'\x69', x.r, x.r, imm=struct.pack('<i', y.val))
            else:
                y, rm = self.operand(y)
                self.op(b'\x0f\xaf', x.r, rm)
                self.release_item(y)
        elif o in ('/', '%'):
            if y.mode == 'const' and y.val == 0:
                self.error("division by zero")
            if y.mode == 'const':
                y = self.load(y)
            y, rm = self.operand(y)
            self.mov_rr(RAX, x.r)
            self.b(0x48, 0x99)                                # cqo
            self.op(b'\xf7', 7, rm)                           # idiv
            self.release_item(y)
            self.mov_rr(x.r, RAX if o == '/' else RDX)
        elif o in ('<<', '>>'):
            ext = 4 if o == '<<' else 7                       # shl / sar
            if y.mode == 'const':
                self.op(b'\xc1', ext, x.r, imm=bytes([y.val & 63]))
            else:
                y, rm = self.operand(y)
                self.op(b'\x8b', RCX, rm)
                self.release_item(y)
                self.op(b'\xd3', ext, x.r)                    # shift by cl
        else:
            code = self.ALU[o]
            if y.mode == 'const' and fits32(y.val):
                self.alu_ri(code, x.r, y.val)
            else:
                y, rm = self.operand(y)
                self.op(bytes([code * 8 + 3]), x.r, rm)
                self.release_item(y)
        x.type = T_INT
        return x

    def term(self):
        x = self.factor()
        while self.s.sym in self.MULOPS:
            o = self.s.sym
            self.s.next()
            if not is_int(x.type):
                self.error(f"'{o}' needs ints")
            x = self.materialise(x)
            x = self.binop(o, x, self.factor())
        return x

    def simple_expr(self):
        x = self.term()
        while self.s.sym in self.ADDOPS:
            o = self.s.sym
            self.s.next()
            if o == '+' and x.type.kind == 'string':
                x = self.materialise(x)
                y = self.term()
                if y.type.kind != 'string':
                    self.error("'+' on a string needs another string (use str(n) for numbers)")
                if x.mode == 'const' and y.mode == 'const':
                    a = self.rodata_string(x.val)
                    c = self.rodata_string(y.val)
                    x = self.make_const(T_STRING, self.string_lit(a + c))
                else:
                    x = self.rt_call(RT_CONCAT, [x, y], T_STRING)
                continue
            if not is_int(x.type):
                self.error(f"'{o}' needs ints")
            x = self.materialise(x)
            x = self.binop(o, x, self.term())
        return x

    def rodata_string(self, addr):
        off = addr - RODATA_BASE
        n = struct.unpack_from('<Q', self.rodata, off)[0]
        return bytes(self.rodata[off + 8:off + 8 + n])

    def relation(self):
        x = self.simple_expr()
        if self.s.sym not in REL:
            return x
        o = self.s.sym
        self.s.next()
        xsym = x.sym if x.mode == 'mem' else None
        x = self.materialise(x)
        y = self.simple_expr()
        ysym = y.sym if y.mode == 'mem' else None
        kx, ky = x.type.kind, y.type.kind
        if kx == 'string' or ky == 'string':
            if kx != ky or o not in ('==', '!='):
                self.error("strings can only be compared with == and !=")
            r = self.rt_call(RT_STREQ, [x, y], T_INT)
            self.op(b'\x85', r.r, r.r)
            self.release(r.r)
            z = Item('cond', T_BOOL)
            z.cc = CC_NE if o == '==' else CC_E
            return z
        refs = is_ref(x.type) or is_ref(y.type) or kx == 'nil' or ky == 'nil'
        if refs:
            if o not in ('==', '!='):
                self.error("references can only be compared with == and !=")
            if not (assignable(x.type, y.type) or assignable(y.type, x.type)):
                self.error(f"cannot compare {x.type!r} with {y.type!r}")
        elif kx != ky and not (is_int(x.type) and is_int(y.type)):
            self.error(f"cannot compare {x.type!r} with {y.type!r}")
        elif kx == 'bool' and o not in ('==', '!='):
            self.error("bools can only be compared with == and !=")
        if x.mode == 'const' and y.mode == 'const':
            v = {'==': x.val == y.val, '!=': x.val != y.val, '<': x.val < y.val,
                 '<=': x.val <= y.val, '>': x.val > y.val, '>=': x.val >= y.val}[o]
            return self.make_const(T_BOOL, int(v))
        cc = REL[o]
        # narrowing: a local optional compared with nil
        narrow = None
        for a, asym, b in ((x, xsym, y), (y, ysym, x)):
            if b.type.kind == 'nil' and a.type.kind == 'opt' and asym and asym.storage == 'local':
                narrow = (asym, a.type.base)
        if x.mode == 'const':
            x, y = y, x
            cc = SWAP[cc]
        if x.mode == 'cond':
            x = self.load(x)
        if y.mode == 'const' and fits32(y.val) and x.mode == 'mem' and x.width == 8:
            self.alu_ri(7, self.mem(x), y.val)
            self.release_item(x)
        else:
            x = self.load(x)
            if y.mode == 'const' and fits32(y.val):
                self.alu_ri(7, x.r, y.val)
            else:
                if y.mode not in ('mem', 'reg') or y.width == 1:
                    y = self.load(y)
                self.op(b'\x3b', x.r, y.r if y.mode == 'reg' else self.mem(y))
                self.release_item(y)
            self.release(x.r)
        z = Item('cond', T_BOOL)
        z.cc = cc
        if narrow:
            (z.nt if o == '!=' else z.nf).append(narrow)
        return z

    def conjunction(self):
        x = self.relation()
        while self.s.sym == '&&':
            self.s.next()
            if x.type.kind != 'bool':
                self.error("'&&' needs bools")
            x = self.load_cond(x)
            flinks = self.false_jump(x)
            mark = len(self.narrow)
            self.push_narrowings(x.nt)
            y = self.load_cond(self.relation())
            del self.narrow[mark:]
            y.F = y.F + flinks
            y.nt = x.nt + y.nt
            y.nf = []
            x = y
        return x

    def expression(self):
        x = self.conjunction()
        while self.s.sym == '||':
            self.s.next()
            if x.type.kind != 'bool':
                self.error("'||' needs bools")
            x = self.load_cond(x)
            x.T.append(self.jcc(x.cc))
            self.fixlink(x.F)
            tlinks = x.T
            mark = len(self.narrow)
            self.push_narrowings(x.nf)
            y = self.load_cond(self.conjunction())
            del self.narrow[mark:]
            y.T = y.T + tlinks
            y.nf = x.nf + y.nf
            y.nt = []
            x = y
        return x

    # ------------------------------------------------------------ statements
    def var_decl(self, kind, is_global=False):
        name = self.ident()
        t = None
        if self.s.sym == ':':
            self.s.next()
            t = self.parse_type()
        if is_global:
            sym = Sym(kind, name, t)
            sym.storage = 'global'
            sym.offset = GLOBAL_BASE + self.gsize
            self.gsize += 8
        if self.s.sym == '=':
            self.s.next()
            if is_global:
                self.begin_init_chunk()
            x = self.expression()
            if t is None:
                if x.type.kind in ('nil', 'void'):
                    self.error(f"cannot infer a type for '{name}' from this value")
                t = x.type
            self.check_assign(t, x, f"initialising '{name}'")
            if is_global:
                sym.type = t
                dst = Item('mem', t)
                dst.disp = sym.offset
            else:
                sym = self.new_local(None, t, kind)
                sym.name = name
                dst = Item('mem', t)
                dst.base, dst.disp = RBP, sym.offset
            self.store(dst, x)
            if is_global:
                self.end_init_chunk()
            self.declare(sym)
        else:
            if t is None:
                self.error(f"'{name}' needs a type or an initial value")
            if non_null(t):
                self.error(f"'{name}' of type {t!r} needs an initial value (it cannot be nil)")
            if kind == 'let':
                self.error(f"'{name}' is immutable and needs a value")
            if is_global:
                sym.type = t
                self.declare(sym)
            else:
                sym = self.new_local(name, t, kind)
                self.op(b'\xc7', 0, (RBP, None, sym.offset), imm=struct.pack('<i', 0))

    def assignment_or_call(self):
        """Returns True if the statement never completes (a call to exit)."""
        line, col = self.s.tline, self.s.tcol
        self.noreturn = False
        x = self.designator()
        if self.noreturn and self.s.sym in (';', '}'):
            return True
        if self.s.sym in ('=', '+=', '-=', '*='):
            o = self.s.sym
            if x.mode not in ('mem', 'elem'):
                self.s.error("cannot assign to this", line, col)
            if x.readonly:
                if x.sym is None:
                    self.s.error("strings are immutable", line, col)
                self.s.error(f"'{x.sym.name}' is immutable (declared with let)", line, col)
            self.s.next()
            dtype = x.sym.type if x.sym else x.type
            if o == '=':
                y = self.expression()
                self.check_assign(dtype, y, "assignment")
                if x.sym and x.sym.storage == 'local' and not assignable(x.type, y.type):
                    self.widen(x.sym)
                self.store(x, y)
            else:
                if dtype.kind != 'int':
                    self.error(f"'{o}' needs an int")
                y = self.expression()
                if not is_int(y.type):
                    self.error(f"'{o}' needs an int")
                if not same(dtype, T_INT):
                    self.error(f"'{o}' needs an int")
                self.resolve(x)
                r = self.alloc()
                self.op(b'\x8b', r, self.mem(x))
                if o == '*=':
                    y = self.load(y)
                    self.op(b'\x0f\xaf', r, y.r)
                    self.release(y.r)
                elif y.mode == 'const' and fits32(y.val):
                    self.alu_ri(0 if o == '+=' else 5, r, y.val)
                else:
                    y = self.load(y)
                    self.op(bytes([(0 if o == '+=' else 5) * 8 + 3]), r, y.r)
                    self.release(y.r)
                self.store(x, self.make_reg(T_INT, r))
        else:
            if x.type is not T_VOID or x.mode != 'void':
                if x.mode != 'reg':
                    self.s.error("this expression does nothing", line, col)
                self.release_item(x)

    def block(self):
        """{ statements } in a new scope. Returns True if it always exits."""
        self.expect('{')
        self.open_block()
        term = self.stat_seq()
        self.expect('}')
        self.close_block(term)
        return term

    def stat_seq(self):
        term = False
        self.skip_semis()
        while self.s.sym not in ('}', 'eof'):
            if term:
                self.error("unreachable code")
            term = self.statement()
            if self.s.sym != '}':
                self.expect(';', "';' or newline")
            self.skip_semis()
        return term

    def statement(self):
        sym = self.s.sym
        if sym in ('var', 'let'):
            self.s.next()
            self.var_decl(sym)
        elif sym == 'ident':
            return self.assignment_or_call() is True
        elif sym == 'if':
            return self.if_stat()
        elif sym == 'while':
            return self.while_stat()
        elif sym == 'for':
            self.for_stat()
        elif sym == 'match':
            return self.match_stat()
        elif sym == 'return':
            self.return_stat()
            return True
        elif sym in ('break', 'continue'):
            self.s.next()
            if not self.loops:
                self.error(f"'{sym}' outside a loop")
            loop = self.loops[-1]
            self.run_defers(loop['blocks'])
            (loop['brk'] if sym == 'break' else loop['cont']).append(self.jmp())
            return True
        elif sym == 'defer':
            self.defer_stat()
        elif sym == '{':
            return self.block()
        else:
            self.error("expected a statement")
        return False

    def if_stat(self):
        self.s.next()
        x = self.load_cond(self.expression())
        flinks = self.false_jump(x)
        mark = len(self.narrow)
        self.push_narrowings(x.nt)
        t1 = self.block()
        del self.narrow[mark:]
        if self.s.sym == 'else':
            self.s.next()
            j = self.jmp()
            self.fixlink(flinks)
            self.push_narrowings(x.nf)
            t2 = self.if_stat() if self.s.sym == 'if' else self.block()
            del self.narrow[mark:]
            self.fixlink([j])
            if t1 and not t2:
                self.push_narrowings(x.nf)
            elif t2 and not t1:
                self.push_narrowings(x.nt)
            return t1 and t2
        self.fixlink(flinks)
        if t1:
            self.push_narrowings(x.nf)     # if p == nil { return } narrows p afterwards
        return False

    def loop_body(self, cont_target=None):
        loop = {'brk': [], 'cont': [], 'blocks': len(self.blocks)}
        self.loops.append(loop)
        self.block()
        self.loops.pop()
        return loop

    def while_stat(self):
        """Returns True for 'while true' with no break: control never falls out."""
        self.s.next()
        top = self.pc()
        x = self.expression()
        forever = x.mode == 'const' and x.type.kind == 'bool' and x.val == 1
        if forever:
            x = Item('cond', T_BOOL)
            flinks = []
        else:
            x = self.load_cond(x)
            flinks = self.false_jump(x)
        self.loopdepth += 1
        mark = len(self.narrow)
        self.push_narrowings(x.nt)
        loop = self.loop_body()
        del self.narrow[mark:]
        self.loopdepth -= 1
        self.fixlink(loop['cont'], top)
        self.jmp_to(top)
        self.fixlink(flinks)
        self.fixlink(loop['brk'])
        return forever and not loop['brk']

    def for_stat(self):
        """for i in a..b { }  or  for x in array { }"""
        self.s.next()
        name = self.ident()
        self.expect('in')
        self.open_block()
        x = self.expression()
        if self.s.sym == '..':
            self.s.next()
            if x.type.kind != 'int':
                self.error("range bounds must be ints")
            var = self.new_local(name, T_INT, 'let')
            vi = Item('mem', T_INT)
            vi.base, vi.disp = RBP, var.offset
            self.store(vi, x)
            y = self.expression()
            if y.type.kind != 'int':
                self.error("range bounds must be ints")
            lim = self.new_local(None, T_INT)
            li = Item('mem', T_INT)
            li.base, li.disp = RBP, lim.offset
            self.store(li, y)
            top = self.pc()
            r = self.alloc()
            self.op(b'\x8b', r, (RBP, None, var.offset))
            self.op(b'\x3b', r, (RBP, None, lim.offset))
            self.release(r)
            exit_link = self.jcc(CC_GE)
            self.loopdepth += 1
            loop = self.loop_body()
            self.loopdepth -= 1
            self.fixlink(loop['cont'])
            self.alu_ri(0, (RBP, None, var.offset), 1)
        else:
            if x.type.kind != 'array':
                self.error(f"cannot iterate over {x.type!r}")
            arr = self.new_local(None, x.type)
            ai = Item('mem', x.type)
            ai.base, ai.disp = RBP, arr.offset
            self.store(ai, x)
            idx = self.new_local(None, T_INT)
            self.op(b'\xc7', 0, (RBP, None, idx.offset), imm=struct.pack('<i', 0))
            var = self.new_local(name, x.type.base, 'let')
            top = self.pc()
            ri, ra = self.alloc(), self.alloc()
            self.op(b'\x8b', ri, (RBP, None, idx.offset))
            self.op(b'\x8b', ra, (RBP, None, arr.offset))
            self.op(b'\x3b', ri, (ra, None, 0))
            exit_link = self.jcc(CC_GE)
            self.op(b'\x8b', ra, (ra, None, 16))              # header -> data block
            if x.type.base.kind == 'byte':
                self.op(b'\x0f\xb6', ri, (ra, ri, 8, 1))
            else:
                self.op(b'\x8b', ri, (ra, ri, 8))
            self.op(b'\x89', ri, (RBP, None, var.offset))
            self.release(ri)
            self.release(ra)
            self.loopdepth += 1
            loop = self.loop_body()
            self.loopdepth -= 1
            self.fixlink(loop['cont'])
            self.alu_ri(0, (RBP, None, idx.offset), 1)
        self.jmp_to(top)
        self.fixlink([exit_link])
        self.fixlink(loop['brk'])
        self.close_block(False)

    def match_stat(self):
        self.s.next()
        line, col = self.s.tline, self.s.tcol
        name = self.ident()
        sym = self.lookup(name)
        if sym.kind not in ('var', 'let') or sym.storage != 'local':
            self.s.error("match needs a local variable or parameter", line, col)
        t = self.var_type(sym)
        if t.kind == 'opt':
            self.s.error(f"'{name}' may be nil: check it with '!= nil' first", line, col)
        if t.incomplete:
            self.s.error(f"{t!r} is declared but not yet defined", line, col)
        if t.kind != 'closed':
            self.s.error(f"match needs a closed type, not {t!r}", line, col)
        tag = self.new_local(None, T_INT)
        r = self.alloc()
        self.op(b'\x8b', r, (RBP, None, sym.offset))
        self.op(b'\x8b', r, (r, None, -16))                   # type tag from the object header
        self.op(b'\x89', r, (RBP, None, tag.offset))
        self.release(r)
        self.expect('{')
        self.skip_semis()
        seen, ends, all_term, has_else = set(), [], True, False
        while self.s.sym != '}':
            if self.s.sym == 'else':
                self.s.next()
                has_else = True
                all_term &= self.block()
                self.skip_semis()
                if self.s.sym != '}':
                    self.error("'else' must be the last arm")
                break
            vname = self.ident()
            v = next((v for v in t.variants if v.name == vname), None)
            if v is None:
                self.error(f"'{vname}' is not a variant of {t!r}")
            if v in seen:
                self.error(f"'{vname}' is matched twice")
            seen.add(v)
            self.alu_ri(7, (RBP, None, tag.offset), v.desc)
            nxt = self.jcc(CC_NE)
            mark = len(self.narrow)
            self.narrow.append([sym, v, self.loopdepth, True])
            all_term &= self.block()
            del self.narrow[mark:]
            ends.append(self.jmp())
            self.fixlink([nxt])
            self.skip_semis()
        self.expect('}')
        missing = [v.name for v in t.variants if v not in seen]
        if missing and not has_else:
            self.s.error(f"match on {t!r} is missing {', '.join(missing)}", line, col)
        self.fixlink(ends)
        return all_term

    def return_stat(self):
        self.s.next()
        if self.in_defer:
            self.error("cannot return from inside defer")
        f = self.fn
        active = any(blk['defers'] for blk in self.blocks[self.fn_block:])
        if f.ret is T_VOID:
            if self.s.sym not in (';', '}'):
                self.error(f"{f.name} does not return a value")
            self.run_defers(self.fn_block)
        else:
            x = self.expression()
            self.check_assign(f.ret, x, "return value")
            x = self.load(x)
            if active:
                slot = self.new_local(None, f.ret)
                self.op(b'\x89', x.r, (RBP, None, slot.offset))
                self.release(x.r)
                self.run_defers(self.fn_block)
                self.op(b'\x8b', RAX, (RBP, None, slot.offset))
            else:
                self.mov_rr(RAX, x.r)
                self.release(x.r)
        self.epilogue()

    def epilogue(self):
        self.b(0x48, 0x89, 0xEC)                              # mov rsp, rbp
        self.b(0x5D, 0xC3)                                    # pop rbp; ret

    def defer_stat(self):
        """Compile the deferred statement out of line as a local subroutine and
        call it at every exit from the enclosing block."""
        self.s.next()
        over = self.jmp()
        entry = self.pc()
        saved = (self.depth, self.narrow, self.loops, self.in_defer)
        self.depth, self.narrow, self.loops, self.in_defer = self.depth + 8, [], [], True
        if self.s.sym == '{':
            self.block()
        else:
            self.open_block()
            self.statement()
            self.close_block(False)
        self.b(0xC3)
        self.depth, self.narrow, self.loops, self.in_defer = saved
        self.fixlink([over])
        self.blocks[-1]['defers'].append(entry)

    # ----------------------------------------------------------- declarations
    def begin_init_chunk(self):
        """Global initialisers become chunks of code linked by jumps, run in
        textual order before main."""
        self.fixlink([self.init_link])

    def end_init_chunk(self):
        self.init_link = self.jmp()

    def fn_decl(self):
        line = self.s.tline
        name = self.ident()
        params = []
        self.expect('(')
        while self.s.sym != ')':
            names = [self.ident()]
            while self.s.sym == ',':
                self.s.next()
                names.append(self.ident())
            self.expect(':')
            t = self.parse_type()
            params += [(n, t) for n in names]
            if self.s.sym != ')':
                self.expect(',', "',' or ')'")
        self.expect(')')
        ret = T_VOID
        if self.s.sym == ':':
            self.s.next()
            ret = self.parse_type()
        f = self.globals.get(name)
        if f is not None and f.kind == 'fn' and f.addr is None:
            if len(f.params) != len(params) or not all(same(a, b) for a, (_, b) in zip(f.params, params)) \
                    or not same(f.ret, ret):
                self.error(f"'{name}' does not match its forward heading")
        else:
            f = self.declare(Sym('fn', name))
            f.params = [t for _, t in params]
            f.ret = ret
            f.line = line
        if self.s.sym != '{':
            return                                            # forward heading
        if f.addr is not None:
            self.error(f"'{name}' is already defined")
        f.addr = self.pc()
        self.fixlink(f.fixups, f.addr)
        self.fn, self.frame, self.depth = f, 0, 0
        # prologue: push rbp; mov rbp, rsp; sub rsp, frame (patched at the end).
        # Every variable is initialised where it is declared, so the frame
        # needs no zeroing; stale slots can only make the stack scan retain
        # a little extra garbage, never free something live.
        self.b(0x55, 0x48, 0x89, 0xE5)
        self.b(0x48, 0x81, 0xEC)
        frame_at = self.pc()
        self.i32(0)
        self.open_block()
        self.fn_block = len(self.blocks) - 1
        n = len(params)
        for i, (pname, pt) in enumerate(params):
            s = Sym('var', pname, pt)
            s.storage = 'local'
            s.offset = 16 + 8 * (n - 1 - i)
            self.declare(s)
        self.expect('{')
        self.open_block()
        term = self.stat_seq()
        self.expect('}')
        if not term and ret is not T_VOID:
            self.error(f"{name} must return a value on every path")
        self.close_block(term)
        self.close_block(term)
        if not term:
            self.epilogue()
        size = (self.frame + 15) & ~15
        self.code[frame_at:frame_at + 4] = struct.pack('<i', size)
        self.fn = None
        assert self.free == POOL or sorted(self.free) == sorted(POOL), "register leak"

    def const_decl(self):
        name = self.ident()
        self.expect('=')
        x = self.expression()
        if x.mode != 'const':
            self.error("constant value must be known at compile time")
        s = Sym('const', name, x.type)
        s.value = x.val
        self.declare(s)

    def module(self):
        # _start: rt_init(stack base, globals start, globals end); run initialisers; main(); exit(0)
        self.b(0x48, 0x89, 0xE7)                              # mov rdi, rsp
        self.b(0xBE)
        self.i32(GLOBAL_BASE)                                 # mov esi, globals
        self.b(0xBA)
        gend_at = self.pc()
        self.i32(0)                                           # mov edx, globals end
        self.call_abs(RT_TEXT + 8 * RT_INIT)
        self.init_link = self.jmp()
        self.skip_semis()
        if self.s.sym == 'module':
            self.s.next()
            self.ident()
            self.expect(';', "newline")
        self.skip_semis()
        while self.s.sym != 'eof':
            if self.s.sym == 'pub':
                self.s.next()
            sym = self.s.sym
            if sym == 'import':
                self.error("imports are not supported yet")
            elif sym == 'fn':
                self.s.next()
                self.fn_decl()
            elif sym == 'type':
                self.s.next()
                self.type_decl()
            elif sym in ('var', 'let'):
                self.s.next()
                self.var_decl(sym, is_global=True)
            elif sym == 'const':
                self.s.next()
                self.const_decl()
            else:
                self.error("expected a declaration")
            if self.s.sym != 'eof':
                self.expect(';', "newline")
            self.skip_semis()
        for s in self.globals.values():
            if s.kind == 'type' and s.type.incomplete:
                self.s.error(f"type '{s.name}' is declared but never defined", s.type.line, 1)
            if s.kind == 'fn' and s.addr is None:
                self.s.error(f"function '{s.name}' has a forward heading but no body", s.line, 1)
        main = self.globals.get('main')
        if main is None or main.kind != 'fn' or main.params or main.ret is not T_VOID:
            self.s.error("the program needs 'fn main()' with no parameters or result",
                         getattr(main, 'line', 1), 1)
        self.fixlink([self.init_link])
        self.b(0xE8)
        self.i32(main.addr - (self.pc() + 4))
        self.b(0x31, 0xFF)                                    # xor edi, edi
        self.call_abs(RT_TEXT + 8 * RT_EXIT)
        self.code[gend_at:gend_at + 4] = struct.pack('<i', GLOBAL_BASE + self.gsize)


def dump_tokens(src):
    """One line per token: 'line:col kind [value]', ending at eof or an error."""
    out = []
    try:
        s = Scanner(src)
        while True:
            k = s.sym
            line = f"{s.tline}:{s.tcol} {k}"
            if k == 'ident':
                line += ' ' + s.val
            elif k in ('int', 'char'):
                line += f' {s.val}'
            elif k == 'string':
                line += f' {len(s.val)}:{s.val.hex()}'
            out.append(line)
            if k == 'eof':
                break
            if len(out) > len(s.src) + 2:                     # every token but ';' consumes input
                out.append(f"{s.tline}:{s.tcol} error scanner made no progress")
                break
            s.next()
    except CompileError as e:
        pos, msg = str(e).split(': ', 1)
        out.append(f"{pos} error {msg}")
    return out


# ----------------------------------------------------------------------------
# ELF writer
# ----------------------------------------------------------------------------
def write_elf(path, code, rodata, gsize, rt_text, rt_data):
    PF_X, PF_W, PF_R = 1, 2, 4
    segs = []                                                 # (vaddr, data, memsz, flags)
    segs.append((IMAGE_BASE, None, 0, PF_R | PF_X))
    segs.append((RODATA_BASE, bytes(rodata) or b'\0', 0, PF_R))
    segs.append((GLOBAL_BASE, b'', max(gsize, 8), PF_R | PF_W))
    segs.append((RT_TEXT, rt_text, 0, PF_R | PF_X))
    segs.append((RT_DATA, rt_data, 0, PF_R | PF_W))
    nph = len(segs) + 1
    out = bytearray(0x1000) + code
    phdrs = []
    phdrs.append((0, IMAGE_BASE, len(out), len(out), PF_R | PF_X))
    for vaddr, data, memsz, flags in segs[1:]:
        while len(out) % 0x1000:
            out.append(0)
        off = len(out) if data else 0
        out += data
        phdrs.append((off, vaddr, len(data), max(memsz, len(data)), flags))
    ehdr = struct.pack('<4sBBBBB7xHHIQQQIHHHHHH', b'\x7fELF', 2, 1, 1, 0, 0,
                       2, 0x3E, 1, CODE_BASE, 64, 0, 0, 64, 56, nph, 64, 0, 0)
    ph = b''
    for off, vaddr, filesz, memsz, flags in phdrs:
        ph += struct.pack('<IIQQQQQQ', 1, flags, off, vaddr, vaddr, filesz, memsz, 0x1000)
    ph += struct.pack('<IIQQQQQQ', 0x6474E551, PF_R | PF_W, 0, 0, 0, 0, 0, 16)  # PT_GNU_STACK
    out[0:len(ehdr)] = ehdr
    out[64:64 + len(ph)] = ph
    with open(path, 'wb') as f:
        f.write(out)
    os.chmod(path, 0o755)


def main():
    args = sys.argv[1:]
    if not args:
        print(__doc__.strip().splitlines()[-1])
        sys.exit(2)
    src_path = args[0]
    out_path = args[args.index('-o') + 1] if '-o' in args else os.path.splitext(src_path)[0]
    here = os.path.dirname(os.path.abspath(__file__))
    with open(src_path, 'rb') as f:
        src = f.read()
    if '--tokens' in args:
        print('\n'.join(dump_tokens(src)))
        return
    t0 = time.perf_counter()
    c = Compiler(src)
    try:
        c.module()
    except CompileError as e:
        print(f"{src_path}:{e}", file=sys.stderr)
        sys.exit(1)
    elapsed = time.perf_counter() - t0
    with open(os.path.join(here, 'runtime_text.bin'), 'rb') as f:
        rt_text = f.read()
    with open(os.path.join(here, 'runtime_data.bin'), 'rb') as f:
        rt_data = f.read()
    write_elf(out_path, c.code, c.rodata, c.gsize, rt_text, rt_data)
    if '-v' in args:
        lines = src.count(b'\n') + 1
        print(f"{src_path}: {lines} lines, {len(c.code)} bytes of code, "
              f"{len(c.rodata)} bytes of data, {elapsed * 1000:.1f} ms", file=sys.stderr)


if __name__ == '__main__':
    main()
