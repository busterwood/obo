"""obx lexer and parser: source text to an AST of N nodes (SPEC sections 2, 3)."""
import os


class CompileError(Exception):
    def __init__(self, msg, where=''):
        super().__init__(f"{where}: {msg}" if where else msg)
        self.msg, self.where = msg, where


KEYWORDS = {'as', 'break', 'closed', 'const', 'continue', 'else', 'extern', 'false', 'fn',
            'for', 'if', 'import', 'in', 'interface', 'let', 'match', 'mut', 'never', 'once', 'pub', 'record',
            'return', 'true', 'try', 'type', 'var', 'while'}
SYMS = ['+=', '-=', '*=', '/=', '%=', '|=', '&=', '^=', '+%', '-%', '*%', '<<', '>>', '..',
        '==', '!=', '<=', '>=', '&&', '||', '{', '}', '(', ')', '[', ']', ',', ':', ';', '.',
        '&', '|', '^', '~', '+', '-', '*', '/', '%', '<', '>', '=', '!']
ESC = {'n': 10, 't': 9, 'r': 13, '0': 0, '\\': 92, "'": 39, '"': 34}


def isdigit(c):
    return '0' <= c <= '9'


def isalpha(c):
    return 'a' <= c <= 'z' or 'A' <= c <= 'Z'


def isalnum(c):
    return isdigit(c) or isalpha(c)


def read_source(path):
    """The text of a source file; universal newlines, as open() reads it. Invalid UTF-8 is an error."""
    with open(path, 'rb') as f:
        data = f.read()
    try:
        return data.decode('utf-8').replace('\r\n', '\n').replace('\r', '\n')
    except UnicodeDecodeError as e:
        line = data[:e.start].replace(b'\r\n', b'\n').replace(b'\r', b'\n').count(b'\n') + 1
        raise CompileError("the source is not valid UTF-8", f"{os.path.basename(path)}:{line}")


class Tok:
    __slots__ = ('k', 'v', 'where')

    def __init__(self, k, v, where):
        self.k, self.v, self.where = k, v, where

    def __repr__(self):
        return f"{self.k}:{self.v!r}"


def lex(src, fname):
    toks, i, line, n = [], 0, 1, len(src)
    nest = []

    def ends_stmt():
        if not toks or (nest and nest[-1] != '{'):
            return False
        t = toks[-1]
        return (t.k in ('id', 'int', 'frac', 'str', 'char')
                or (t.k == 'kw' and t.v in ('return', 'break', 'continue', 'true', 'false', 'never'))
                or (t.k == 'sym' and t.v in (')', ']', '}')))

    def toolong(j):
        if j - i > 1000:
            raise CompileError(f"a literal longer than 1000 characters ({j - i}) is too long to read", w)

    def escape(j, w):
        c = src[j + 1] if j + 1 < n else ''
        if c == 'x':
            h = src[j + 2:j + 4]
            if len(h) != 2 or any(x not in '0123456789abcdefABCDEF' for x in h):
                raise CompileError("bad \\x escape", w)
            return int(h, 16), j + 4
        if c not in ESC:
            raise CompileError(f"unknown escape \\{c}", w)
        return ESC[c], j + 2

    while i < n:
        c = src[i]
        w = f"{fname}:{line}"
        if c == '\n':
            if ends_stmt():
                toks.append(Tok('sym', ';', w))
            line += 1
            i += 1
        elif c in ' \t\r':
            i += 1
        elif src.startswith('//', i):
            while i < n and src[i] != '\n':
                i += 1
        elif isdigit(c):
            j = i
            if src.startswith(('0x', '0b'), i):
                base = 16 if src[i + 1] == 'x' else 2
                j = i + 2
                while j < n and (isalnum(src[j]) or src[j] == '_'):
                    j += 1
                toolong(j)
                try:
                    v = int(src[i + 2:j].replace('_', ''), base)
                except ValueError:
                    raise CompileError(f"bad integer literal {src[i:j]}", w)
                toks.append(Tok('int', v, w))
                i = j
                continue
            while j < n and (isdigit(src[j]) or src[j] == '_'):
                j += 1
            k = j
            if j < n and src[j] == '.' and j + 1 < n and isdigit(src[j + 1]):    # 5.toStr() is a call
                k = j + 1
                while k < n and (isdigit(src[k]) or src[k] == '_'):
                    k += 1
            if k < n and src[k] in 'eE':        # an exponent: e or E, an optional sign, digits
                e = k + 1 + (src[k + 1:k + 2] in ('+', '-'))
                if not isdigit(src[e:e + 1]):
                    raise CompileError("an exponent needs digits", w)
                k = e
                while k < n and isdigit(src[k]):
                    k += 1
            toolong(k)
            if k > j:
                if k < n and (isalnum(src[k]) or src[k] == '_'):
                    raise CompileError(f"bad fractional literal {src[i:k + 1]}", w)
                toks.append(Tok('frac', src[i:k].replace('_', ''), w))
                i = k
                continue
            if j < n and (isalpha(src[j]) or src[j] == '_'):
                raise CompileError(f"bad integer literal {src[i:j + 1]}", w)
            toks.append(Tok('int', int(src[i:j].replace('_', '')), w))
            i = j
        elif isalpha(c) or c == '_':
            j = i
            while j < n and (isalnum(src[j]) or src[j] == '_'):
                j += 1
            word = src[i:j]
            toks.append(Tok('kw' if word in KEYWORDS else 'id', word, w))
            i = j
        elif c == '"':
            j, out = i + 1, bytearray()
            while True:
                if j >= n or src[j] == '\n':
                    raise CompileError("unterminated string literal", w)
                if src[j] == '"':
                    break
                if src[j] == '\\':
                    b, j = escape(j, w)
                    out.append(b)
                else:
                    out += src[j].encode('utf-8')
                    j += 1
            toks.append(Tok('str', bytes(out), w))
            i = j + 1
        elif c == "'":
            if i + 1 < n and src[i + 1] == '\\':
                v, j = escape(i + 1, w)
            elif i + 1 < n and src[i + 1] not in "'\n":
                bs = src[i + 1].encode('utf-8')
                if len(bs) != 1:
                    raise CompileError("a character literal must be one byte", w)
                v, j = bs[0], i + 2
            else:
                raise CompileError("bad character literal", w)
            if j >= n or src[j] != "'":
                raise CompileError("unterminated character literal", w)
            toks.append(Tok('char', v, w))
            i = j + 1
        elif c == '.' and i + 1 < n and isdigit(src[i + 1]) and not (toks and toks[-1].k in ('id', 'int') or toks and toks[-1].v in (')', ']')):
            raise CompileError("a fractional literal needs digits before the point", w)
        else:
            for s in SYMS:
                if src.startswith(s, i):
                    if s in '([{':
                        nest.append(s)
                    elif s in ')]}' and nest:
                        nest.pop()
                    toks.append(Tok('sym', s, w))
                    i += len(s)
                    break
            else:
                raise CompileError(f"unexpected character {c!r}" if c < '\x80' else f"unexpected character U+{ord(c):04X}", w)
    w = f"{fname}:{line}"
    toks.append(Tok('sym', ';', w))
    toks.append(Tok('eof', '', w))
    return toks


class N:
    def __init__(self, kind, where, **kw):
        self.kind = kind
        self.where = where
        self.__dict__.update(kw)

    def __repr__(self):
        return f"N({self.kind})"


# Type syntax: ('ref', T, mut) ('arr', T) ('fnt', [T], R|None, where) ('name', [parts], [targs], where)

class Parser:
    def __init__(self, toks):
        self.t, self.i, self.nolit = toks, 0, 0

    def peek(self, o=0):
        return self.t[min(self.i + o, len(self.t) - 1)]

    def at(self, k, v=None):
        t = self.t[self.i]
        return t.k == k and (v is None or t.v == v)

    def atsym(self, v):
        return self.at('sym', v)

    def atkw(self, v):
        return self.at('kw', v)

    def next(self):
        t = self.t[self.i]
        self.i += 1
        return t

    def err(self, msg, t=None):
        t = t or self.peek()
        raise CompileError(msg, t.where)

    def expect(self, k, v=None):
        t = self.next()
        if t.k != k or (v is not None and t.v != v):
            found = 'end of line' if t.v == ';' and t.k == 'sym' else (t.v if t.v != '' else t.k)
            if isinstance(found, bytes):
                found = 'a string'
            what = v or {'id': 'a name'}.get(k, k)
            raise CompileError(f"expected {what}, found {found!r}", t.where)
        return t

    def sym(self, v):
        return self.expect('sym', v)

    def ident(self):
        return self.expect('id').v

    def skipsemi(self):
        while self.atsym(';'):
            self.next()

    def sep(self):
        if self.atsym('}'):
            return
        self.sym(';')
        self.skipsemi()

    # ---- declarations

    def module(self):
        imports, decls = [], []
        self.skipsemi()
        while self.atkw('import'):
            w = self.next().where
            path = self.expect('str').v.decode('utf-8')
            alias = None
            if self.atkw('as'):
                self.next()
                alias = self.ident()
            imports.append(N('import', w, path=path, alias=alias))
            self.sep()
        while not self.at('eof'):
            pub = False
            if self.atkw('pub'):
                self.next()
                pub = True
            t = self.peek()
            if t.k == 'kw' and t.v == 'type':
                d = self.typedecl()
            elif t.k == 'kw' and t.v == 'fn':
                d = self.fndecl()
            elif t.k == 'kw' and t.v == 'const':
                w = self.next().where
                name = self.ident()
                ty = self.typeopt()
                self.sym('=')
                d = N('const', w, name=name, ty=ty, e=self.expr0())
                self.sep()
            elif t.k == 'kw' and t.v == 'var':
                w = self.next().where
                name = self.ident()
                self.sym(':')
                once = self.atkw('once') and self.next() is not None
                ty = self.type()
                e = None if once else (self.sym('='), self.expr0())[1]
                d = N('global', w, name=name, ty=ty, e=e, once=once)
                self.sep()
            elif t.k == 'kw' and t.v == 'extern':
                w = self.next().where
                lib = self.expect('str').v.decode('utf-8')
                self.expect('kw', 'fn')
                name = self.ident()
                ps = self.params()
                ret = self.typeopt()
                cname = name
                if self.atkw('as'):
                    self.next()
                    cname = self.expect('str').v.decode('utf-8')
                    if not cname.isidentifier() or not cname.isascii():
                        self.err(f"{cname!r} is not a C function name")
                if pub:
                    raise CompileError(f"extern {name} cannot be pub: an extern is always private to its "
                                       "module (export a safe wrapper instead)", w)
                d = N('fn', w, name=name, recv=None, tparams=[], params=ps, ret=ret, body=None,
                      extern=lib, cname=cname)
                self.sep()
            elif t.k == 'kw' and t.v == 'import':
                self.err("imports must come before the declarations")
            else:
                self.err(f"expected a declaration, found {t.v!r}")
            d.pub = pub
            decls.append(d)
            self.skipsemi()
        return imports, decls

    def typeopt(self):
        if self.atsym(':'):
            self.next()
            return self.type()
        return None

    def tparams(self):
        ps = []
        if self.atsym('['):
            self.next()
            while True:
                ps.append(self.tparam())
                if not self.atsym(','):
                    break
                self.next()
            self.sym(']')
        return ps

    def tparam(self):
        w = self.peek().where
        name = self.ident()
        cons = []
        if self.atsym(':'):
            self.next()
            cons.append(self.constraint())
            while self.atsym('+'):
                self.next()
                cons.append(self.constraint())
        return (name, cons, w)

    def constraint(self):
        if self.at('id', 'copy'):
            self.next()
            return 'copy'
        return self.namedtype()

    def typedecl(self):
        where = self.next().where
        name = self.ident()
        tps = self.tparams()
        self.sym('=')
        value = False
        if self.at('id', 'value') and self.peek(1).k == 'kw' and self.peek(1).v in ('record', 'closed'):
            self.next()
            value = True
        if self.atkw('record'):
            self.next()
            return N('type', where, name=name, tparams=tps, form='record', value=value,
                     fields=self.fields())
        if self.atkw('closed'):
            self.next()
            self.sym('{')
            self.skipsemi()
            vs = []
            while not self.atsym('}'):
                vw = self.peek().where
                vn = self.ident()
                if self.atsym('('):
                    self.next()
                    fs = []
                    while not self.atsym(')'):
                        fs.append((str(len(fs)), self.type(), True))
                        if not self.atsym(')'):
                            self.sym(',')
                    self.next()
                    vs.append((vn, fs, False, vw))
                elif self.atsym('{'):
                    vs.append((vn, self.fields(), True, vw))
                else:
                    vs.append((vn, [], False, vw))
                self.sep()
            self.sym('}')
            return N('type', where, name=name, tparams=tps, form='closed', value=value, variants=vs)
        if self.atkw('interface'):
            self.next()
            self.sym('{')
            self.skipsemi()
            members = []
            while not self.atsym('}'):
                mw = self.expect('kw', 'fn').where
                mode = None
                if self.atsym('('):
                    self.next()
                    if self.atsym('&'):
                        self.next()
                        mode = 'ref'
                    elif self.atkw('mut'):
                        self.next()
                        self.sym('&')
                        mode = 'mut'
                    else:
                        mode = 'val'
                    if not self.at('id', 'Self'):
                        self.err("an interface receiver is Self, &Self or mut &Self")
                    self.next()
                    self.sym(')')
                mname = self.ident()
                ps = self.params()
                members.append(N('member', mw, name=mname, mode=mode, params=ps, ret=self.typeopt()))
                self.sep()
            self.sym('}')
            return N('type', where, name=name, tparams=tps, form='interface', members=members)
        return N('type', where, name=name, tparams=tps, form='alias', target=self.type())

    def fields(self):
        self.sym('{')
        self.skipsemi()
        fs = []
        while not self.atsym('}'):
            pub = False
            if self.atkw('pub'):
                self.next()
                pub = True
            names = [self.ident()]
            while self.atsym(','):
                self.next()
                names.append(self.ident())
            self.sym(':')
            t = self.type()
            fs.extend((n, t, pub) for n in names)
            self.sep()
        self.sym('}')
        return fs

    def fndecl(self):
        where = self.next().where
        recv = None
        if self.atsym('('):
            self.next()
            if self.peek().k == 'id' and self.peek(1).k == 'sym' and self.peek(1).v == ':':
                rn = self.next().v
                self.next()
                recv = (rn, self.recvtype())
            else:
                recv = (None, self.recvtype())
            self.sym(')')
        name = self.ident()
        tps = self.tparams()
        ps = self.params()
        ret = self.typeopt()
        return N('fn', where, name=name, recv=recv, tparams=tps, params=ps, ret=ret,
                 body=self.block(), extern=None)

    def recvtype(self):
        # A receiver type: its type arguments declare the method's type parameters
        # and may carry extra constraints (`Map[K, V: copy]`).
        if self.atsym('&'):
            self.next()
            return ('ref', self.recvtype(), False)
        if self.atkw('mut'):
            self.next()
            self.sym('&')
            return ('ref', self.recvtype(), True)
        w = self.peek().where
        parts = [self.ident()]
        if self.atsym('.'):
            self.next()
            parts.append(self.ident())
        args = []
        if self.atsym('['):
            self.next()
            while True:
                if self.peek().k == 'id' and self.peek(1).v in (':', ',', ']'):
                    args.append(('tparam',) + self.tparam())
                else:
                    args.append(self.type())
                if not self.atsym(','):
                    break
                self.next()
            self.sym(']')
        return ('name', parts, args, w)

    def params(self):
        self.sym('(')
        ps = []
        while not self.atsym(')'):
            isvar = False
            if self.atkw('var'):
                self.next()
                isvar = True
            w = self.peek().where
            n = self.ident()
            self.sym(':')
            ps.append((n, self.type(), isvar, w))
            if not self.atsym(')'):
                self.sym(',')
        self.sym(')')
        return ps

    def type(self):
        if self.atsym('&') or self.atkw('mut'):
            mut = self.next().v == 'mut'
            if mut:
                self.sym('&')
            if self.atsym('[') and self.peek(1).v != ']':
                self.next()
                t = self.type()
                self.sym(']')
                return ('slice', t, mut)
            return ('ref', self.type(), mut)
        if self.atkw('never'):
            return ('never', self.next().where)
        if self.atsym('['):
            self.next()
            self.sym(']')
            return ('arr', self.type())
        if self.atkw('fn'):
            fw = self.next().where
            self.sym('(')
            ps = []
            while not self.atsym(')'):
                ps.append(self.type())
                if not self.atsym(')'):
                    self.sym(',')
            self.sym(')')
            return ('fnt', ps, self.typeopt(), fw)
        return self.namedtype()

    def namedtype(self):
        w = self.peek().where
        parts = [self.ident()]
        if self.atsym('.'):
            self.next()
            parts.append(self.ident())
        args = []
        if self.atsym('['):
            self.next()
            args.append(self.type())
            while self.atsym(','):
                self.next()
                args.append(self.type())
            self.sym(']')
        return ('name', parts, args, w)

    # ---- statements

    def block(self):
        where = self.sym('{').where
        self.skipsemi()
        ss = []
        while not self.atsym('}'):
            st = self.stmt()
            ss.append(st)
            if st.kind in ('if', 'while', 'for', 'match', 'block') and self.prev_brace():
                self.skipsemi()
            else:
                self.sep()
        self.sym('}')
        return N('block', where, stmts=ss)

    def prev_brace(self):
        t = self.t[self.i - 1]
        return t.k == 'sym' and t.v == '}'

    def cond(self):
        self.nolit += 1
        e = self.expr()
        self.nolit -= 1
        return e

    def stmt(self):
        t = self.peek()
        w = t.where
        if t.k == 'sym' and t.v == '{':
            return self.block()
        if t.k == 'kw':
            if t.v in ('let', 'var'):
                self.next()
                if self.peek().k == 'id' and self.peek(1).k == 'sym' and self.peek(1).v in ('.', '{', '['):
                    return self.destructure(w, t.v == 'var')
                name = self.ident()
                ty = self.typeopt()
                self.sym('=')
                return N('let', w, name=name, ty=ty, e=self.expr0(), mutable=(t.v == 'var'))
            if t.v == 'if':
                return self.ifstmt()
            if t.v == 'while':
                self.next()
                c = self.cond()
                return N('while', w, cond=c, body=self.block())
            if t.v == 'for':
                self.next()
                name = self.ident()
                self.expect('kw', 'in')
                a = self.cond()
                b = None
                if self.atsym('..'):
                    self.next()
                    b = self.cond()
                return N('for', w, name=name, a=a, b=b, body=self.block())
            if t.v == 'return':
                self.next()
                if self.atsym(';') or self.atsym('}'):
                    return N('return', w, e=None)
                return N('return', w, e=self.expr0())
            if t.v in ('break', 'continue'):
                self.next()
                return N(t.v, w)
            if t.v == 'match':
                return self.matchstmt()
        e = self.expr0()
        if self.peek().k == 'sym' and self.peek().v in ('=', '+=', '-=', '*=', '/=', '%=', '|=', '&=', '^='):
            op = self.next().v
            return N('assign', w, target=e, op=op[:-1] if op != '=' else None, e=self.expr0())
        return N('expr', w, e=e)

    def destructure(self, w, mutable):
        ty = self.namedtype()
        self.sym('{')
        fs = []
        while True:
            fw = self.peek().where
            f = self.ident()
            b = f
            if self.atsym(':'):
                self.next()
                b = self.ident()
            fs.append((f, b, fw))
            if not self.atsym(','):
                break
            self.next()
        self.sym('}')
        self.sym('=')
        return N('destructure', w, ty=ty, fields=fs, e=self.expr0(), mutable=mutable)

    def matchstmt(self):
        w = self.next().where
        scrut = self.cond()
        self.sym('{')
        self.skipsemi()
        arms = []
        while not self.atsym('}'):
            aw = self.peek().where
            if self.atkw('else'):
                self.next()
                arms.append(N('arm', aw, variant=None, lits=None, binds=None, body=self.block()))
            elif self.peek().k == 'id':
                vpath = [self.next().v]
                while self.atsym('.'):
                    self.next()
                    vpath.append(self.ident())
                vn = vpath[-1]
                binds = None
                if self.atsym('('):
                    self.next()
                    binds = []
                    while not self.atsym(')'):
                        binds.append(self.ident())
                        if not self.atsym(')'):
                            self.sym(',')
                    self.next()
                arms.append(N('arm', aw, variant=vn, vpath=vpath, lits=None, binds=binds, body=self.block()))
            else:
                lits = [self.literal()]
                while self.atsym(','):
                    self.next()
                    lits.append(self.literal())
                arms.append(N('arm', aw, variant=None, lits=lits, binds=None, body=self.block()))
            self.skipsemi()
        self.sym('}')
        return N('match', w, e=scrut, arms=arms)

    def literal(self):
        t = self.next()
        if t.k == 'sym' and t.v == '-' and self.peek().k == 'int':
            return ('int', -self.next().v)
        if t.k in ('int', 'char', 'str'):
            return (t.k, t.v)
        raise CompileError(f"expected a variant name or a literal, found {t.v!r}", t.where)

    def ifstmt(self):
        w = self.next().where
        c = self.cond()
        then = self.block()
        els = None
        if self.atsym(';') and self.peek(1).k == 'kw' and self.peek(1).v == 'else':
            self.next()
        if self.atkw('else'):
            self.next()
            if self.atkw('if'):
                els = N('block', self.peek().where, stmts=[self.ifstmt()])
            else:
                els = self.block()
        return N('if', w, cond=c, then=then, els=els)

    # ---- expressions

    def expr0(self):
        save, self.nolit = self.nolit, 0
        e = self.expr()
        self.nolit = save
        return e

    def expr(self):
        return self.binary(0)

    LEVELS = [['||'], ['&&'], None, ['|', '^', '&'], ['<<', '>>'], ['+', '-', '+%', '-%'],
              ['*', '/', '%', '*%']]
    CMP = ('==', '!=', '<', '<=', '>', '>=')

    def binary(self, lvl):
        if lvl == len(self.LEVELS):
            return self.unary()
        if self.LEVELS[lvl] is None:
            left = self.binary(lvl + 1)
            if self.peek().k == 'sym' and self.peek().v in self.CMP:
                op = self.next()
                left = N('bin', op.where, op=op.v, l=left, r=self.binary(lvl + 1))
                if self.peek().k == 'sym' and self.peek().v in self.CMP:
                    self.err("comparisons do not chain")
            return left
        left = self.binary(lvl + 1)
        while self.peek().k == 'sym' and self.peek().v in self.LEVELS[lvl]:
            op = self.next()
            left = N('bin', op.where, op=op.v, l=left, r=self.binary(lvl + 1))
        return left

    def unary(self):
        t = self.peek()
        if t.k == 'sym' and t.v in ('-', '!', '~', '&'):
            self.next()
            e = self.unary()
            if t.v == '-' and e.kind == 'int' and not getattr(e, 'neg', False):
                return N('int', t.where, v=-e.v, neg=True)
            if t.v == '&':
                return N('borrow', t.where, e=e, mut=False)
            return N('un', t.where, op=t.v, e=e)
        if t.k == 'kw' and t.v == 'mut':
            self.next()
            self.sym('&')
            return N('borrow', t.where, e=self.unary(), mut=True)
        if t.k == 'kw' and t.v == 'try':
            self.next()
            return N('try', t.where, e=self.unary())
        return self.postfix(self.primary())

    def namelike(self, e):
        return e.kind == 'name' or (e.kind in ('field', 'index') and self.namelike(e.e))

    def postfix(self, e):
        while True:
            t = self.peek()
            if self.atsym('.'):
                self.next()
                e = N('field', t.where, e=e, name=self.ident())
            elif self.atsym('['):
                self.next()
                save, self.nolit = self.nolit, 0
                lo = None if self.atsym('..') else self.texpr()
                if self.atsym('..'):
                    self.next()
                    hi = None if self.atsym(']') else self.expr()
                    e = N('sub', t.where, e=e, lo=lo, hi=hi)
                else:
                    items = [lo]
                    while self.atsym(','):
                        self.next()
                        items.append(self.texpr())
                    e = N('index', t.where, e=e, items=items)
                self.nolit = save
                self.sym(']')
            elif self.atsym('('):
                e = N('call', t.where, fn=e, args=self.args('(', ')'))
            elif self.atsym('{') and not self.nolit and self.namelike(e):
                e = N('lit', t.where, ty=e, inits=self.inits())
            else:
                return e

    def texpr(self):
        """A type argument or an index expression; the checker decides which."""
        t = self.peek()
        if (t.k == 'sym' and t.v == '[' and self.peek(1).v == ']') or (t.k == 'kw' and t.v == 'fn'):
            return N('type', t.where, ty=self.type())
        if t.k == 'sym' and t.v == '&' or t.k == 'kw' and t.v == 'mut':
            mut = t.v == 'mut'
            self.next()
            if mut:
                self.sym('&')
            inner = self.texpr()
            if inner.kind == 'type':
                return N('type', t.where, ty=('ref', inner.ty, mut))
            return N('borrow', t.where, e=inner, mut=mut)
        return self.expr()

    def args(self, open_, close):
        self.sym(open_)
        save, self.nolit = self.nolit, 0
        a = []
        self.skipsemi()
        while not self.atsym(close):
            a.append(self.expr())
            self.skipsemi()
            if not self.atsym(close):
                self.sym(',')
                self.skipsemi()
        self.sym(close)
        self.nolit = save
        return a

    def primary(self):
        t = self.next()
        w = t.where
        if t.k == 'int':
            return N('int', w, v=t.v)
        if t.k == 'char':
            return N('char', w, v=t.v)
        if t.k == 'frac':
            return N('frac', w, v=t.v)
        if t.k == 'str':
            return N('str', w, v=t.v)
        if t.k == 'kw' and t.v in ('true', 'false'):
            return N('bool', w, v=(t.v == 'true'))
        if t.k == 'sym' and t.v == '(':
            e = self.expr0()
            self.sym(')')
            return e
        if t.k == 'sym' and t.v == '[':
            self.i -= 1
            return N('arrlit', w, items=self.args('[', ']'))
        if t.k == 'id':
            return N('name', w, v=t.v)
        if t.k == 'kw' and t.v == 'fn':          # a closure (SPEC 4.6)
            self.sym('(')
            ps = []
            while not self.atsym(')'):
                pw = self.peek().where
                ps.append((self.ident(), self.typeopt(), pw))
                if not self.atsym(')'):
                    self.sym(',')
            self.sym(')')
            ret = self.typeopt()
            save, self.nolit = self.nolit, 0
            body = self.block()
            self.nolit = save
            return N('closure', w, params=ps, ret=ret, body=body)
        found = t.v if t.v not in ('', ';') else ('end of line' if t.v == ';' else t.k)
        if isinstance(found, bytes):
            found = 'a string'
        raise CompileError(f"expected an expression, found {found!r}", w)

    def inits(self):
        self.sym('{')
        save, self.nolit = self.nolit, 0
        self.skipsemi()
        fs = []
        while not self.atsym('}'):
            w = self.peek().where
            n = self.ident()
            self.sym(':')
            fs.append((n, self.expr(), w))
            self.skipsemi()
            if not self.atsym('}'):
                self.sym(',')
                self.skipsemi()
        self.sym('}')
        self.nolit = save
        return fs


def parse(src, fname):
    return Parser(lex(src, fname)).module()
