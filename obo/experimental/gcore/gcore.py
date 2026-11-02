#!/usr/bin/env python3
"""gcore: a throwaway prototype of a small, uniform obo core.

It tests one claim: with generics, value records, operator methods and
associated (static) functions, the types obo builds into its compiler
(Result, Option, decimal, money, containers) can be ordinary library code, and
single ownership still composes with them.

Pipeline: source -> tokens -> AST -> per-instance check + C emission
(monomorphization) -> gcc. Every generic function and type is checked once per
instantiation, against concrete types, so `T` being owned or copyable is a plain
fact at check time.

The compiler knows exactly two library names ("lang items"): a fractional
literal such as 12.34 calls `fromFraction(1234, 2)`, and `try` needs variants
named `Ok` and `Err`. It has no other knowledge of any library type.

Deliberately missing (see README.md): reference invalidation (obo's section 4
checker, which is orthogonal to generics), handles, interfaces as values,
closures, modules, strings beyond literals, integer overflow checks.
"""
import os
import subprocess
import sys

# ----------------------------------------------------------------- errors

class CompileError(Exception):
    def __init__(self, msg, where=''):
        super().__init__(f"{where}: {msg}" if where else msg)
        self.msg = msg
        self.where = where

# ----------------------------------------------------------------- lexer

KEYWORDS = {'type', 'fn', 'let', 'var', 'if', 'else', 'while', 'for', 'in',
            'return', 'match', 'record', 'closed', 'interface',
            'true', 'false', 'try'}
SYMS = ['..', '==', '!=', '<=', '>=', '&&', '||', '{', '}', '(', ')', '[', ']',
        ',', ':', ';', '.', '&', '+', '-', '*', '/', '%', '<', '>', '=', '!']


class Tok:
    def __init__(self, k, v, where):
        self.k, self.v, self.where = k, v, where


def lex(src, fname):
    toks, i, line, n = [], 0, 1, len(src)

    def ends_stmt():
        if not toks:
            return False
        t = toks[-1]
        return (t.k in ('id', 'int', 'frac', 'str')
                or (t.k == 'kw' and t.v in ('return', 'true', 'false'))
                or (t.k == 'sym' and t.v in (')', ']', '}')))

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
        elif c.isdigit():
            j = i
            while j < n and src[j].isdigit():
                j += 1
            if j + 1 < n and src[j] == '.' and src[j + 1].isdigit():
                k = j + 1
                while k < n and src[k].isdigit():
                    k += 1
                toks.append(Tok('frac', src[i:k], w))
                i = k
            else:
                toks.append(Tok('int', src[i:j], w))
                i = j
        elif c.isalpha() or c == '_':
            j = i
            while j < n and (src[j].isalnum() or src[j] == '_'):
                j += 1
            word = src[i:j]
            toks.append(Tok('kw' if word in KEYWORDS else 'id', word, w))
            i = j
        elif c == '"':
            j, s = i + 1, ''
            while src[j] != '"':
                if src[j] == '\\':
                    s += {'n': '\n', 't': '\t', '"': '"', '\\': '\\'}[src[j + 1]]
                    j += 2
                else:
                    s += src[j]
                    j += 1
            toks.append(Tok('str', s, w))
            i = j + 1
        else:
            for s in SYMS:
                if src.startswith(s, i):
                    toks.append(Tok('sym', s, w))
                    i += len(s)
                    break
            else:
                raise CompileError(f"unexpected character {c!r}", w)
    w = f"{fname}:{line}"
    toks.append(Tok('sym', ';', w))
    toks.append(Tok('eof', '', w))
    return toks

# ----------------------------------------------------------------- parser

class N:
    def __init__(self, kind, where, **kw):
        self.kind = kind
        self.where = where
        self.__dict__.update(kw)


class Parser:
    BIN = [['||'], ['&&'], ['==', '!=', '<', '>', '<=', '>='], ['+', '-'],
           ['*', '/', '%']]

    def __init__(self, toks):
        self.t, self.i, self.nolit = toks, 0, 0

    def peek(self, o=0):
        return self.t[self.i + o]

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

    def expect(self, k, v=None):
        t = self.next()
        if t.k != k or (v is not None and t.v != v):
            raise CompileError(f"expected {v or k}, found {t.v or t.k!r}", t.where)
        return t

    def sym(self, v):
        return self.expect('sym', v)

    def skipsemi(self):
        while self.atsym(';'):
            self.next()

    # declarations

    def program(self):
        decls = []
        self.skipsemi()
        while not self.at('eof'):
            if self.atkw('type'):
                decls.append(self.typedecl())
            elif self.atkw('fn'):
                decls.append(self.fndecl())
            else:
                t = self.peek()
                raise CompileError(f"expected a declaration, found {t.v!r}", t.where)
            self.skipsemi()
        return decls

    def tparams(self):
        ps = []
        if self.atsym('['):
            self.next()
            while True:
                name = self.expect('id').v
                con = None
                if self.atsym(':'):
                    self.next()
                    con = self.expect('id').v
                ps.append((name, con))
                if not self.atsym(','):
                    break
                self.next()
            self.sym(']')
        return ps

    def typedecl(self):
        where = self.next().where
        name = self.expect('id').v
        tps = self.tparams()
        self.sym('=')
        if self.at('id', 'value'):           # contextual: 'value' is a field name elsewhere
            self.next()
            self.expect('kw', 'record')
            return N('type', where, name=name, tparams=tps, form='value', fields=self.fields())
        if self.atkw('record'):
            self.next()
            return N('type', where, name=name, tparams=tps, form='record', fields=self.fields())
        if self.atkw('closed'):
            self.next()
            self.sym('{')
            self.skipsemi()
            vs = []
            while not self.atsym('}'):
                vn = self.expect('id').v
                vs.append((vn, self.fields()))
                self.skipsemi()
            self.sym('}')
            return N('type', where, name=name, tparams=tps, form='closed', variants=vs)
        if self.atkw('interface'):
            self.next()
            self.sym('{')
            self.skipsemi()
            members = []
            while not self.atsym('}'):
                self.expect('kw', 'fn')
                members.append(self.expect('id').v)
                self.params()
                if self.atsym(':'):
                    self.next()
                    self.type()
                self.skipsemi()
            self.sym('}')
            return N('type', where, name=name, tparams=tps, form='interface', members=members)
        raise CompileError("expected value record, record, closed or interface", where)

    def fields(self):
        self.sym('{')
        self.skipsemi()
        fs = []
        while not self.atsym('}'):
            names = [self.expect('id').v]
            while self.atsym(','):
                self.next()
                names.append(self.expect('id').v)
            self.sym(':')
            t = self.type()
            fs.extend((n, t) for n in names)
            self.skipsemi()
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
                recv = (rn, self.type())
            else:
                recv = (None, self.type())      # static: an associated function
            self.sym(')')
        name = self.expect('id').v
        tps = self.tparams()
        ps = self.params()
        ret = None
        if self.atsym(':'):
            self.next()
            ret = self.type()
        return N('fn', where, name=name, recv=recv, tparams=tps, params=ps, ret=ret,
                 body=self.block())

    def params(self):
        self.sym('(')
        ps = []
        while not self.atsym(')'):
            n = self.expect('id').v
            self.sym(':')
            ps.append((n, self.type()))
            if self.atsym(','):
                self.next()
        self.sym(')')
        return ps

    def type(self):
        if self.atsym('&'):
            self.next()
            return ('ref', self.type())
        if self.atsym('['):
            self.next()
            self.sym(']')
            return ('arr', self.type())
        name = self.expect('id').v
        args = []
        if self.atsym('['):
            self.next()
            args.append(self.type())
            while self.atsym(','):
                self.next()
                args.append(self.type())
            self.sym(']')
        return ('name', name, args)

    # statements

    def block(self):
        where = self.sym('{').where
        self.skipsemi()
        ss = []
        while not self.atsym('}'):
            ss.append(self.stmt())
            self.skipsemi()
        self.sym('}')
        return N('block', where, stmts=ss)

    def cond(self):
        self.nolit += 1
        e = self.expr()
        self.nolit -= 1
        return e

    def stmt(self):
        t = self.peek()
        w = t.where
        if t.k == 'kw':
            if t.v in ('let', 'var'):
                self.next()
                name = self.expect('id').v
                ty = None
                if self.atsym(':'):
                    self.next()
                    ty = self.type()
                self.sym('=')
                return N('let', w, name=name, ty=ty, e=self.expr(), mutable=(t.v == 'var'))
            if t.v == 'if':
                return self.ifstmt()
            if t.v == 'while':
                self.next()
                c = self.cond()
                return N('while', w, cond=c, body=self.block())
            if t.v == 'for':
                self.next()
                name = self.expect('id').v
                self.expect('kw', 'in')
                self.nolit += 1
                a = self.expr()
                self.sym('..')
                b = self.expr()
                self.nolit -= 1
                return N('for', w, name=name, a=a, b=b, body=self.block())
            if t.v == 'return':
                self.next()
                if self.atsym(';') or self.atsym('}'):
                    return N('return', w, e=None)
                return N('return', w, e=self.expr())
            if t.v == 'match':
                self.next()
                scrut = self.cond()
                self.sym('{')
                self.skipsemi()
                arms = []
                while not self.atsym('}'):
                    aw = self.peek().where
                    if self.atkw('else'):
                        self.next()
                        arms.append(N('arm', aw, variant=None, binds=None, body=self.block()))
                    else:
                        vn = self.expect('id').v
                        binds = None
                        if self.atsym('('):
                            self.next()
                            binds = []
                            while not self.atsym(')'):
                                binds.append(self.expect('id').v)
                                if self.atsym(','):
                                    self.next()
                            self.sym(')')
                        arms.append(N('arm', aw, variant=vn, binds=binds, body=self.block()))
                    self.skipsemi()
                self.sym('}')
                return N('match', w, e=scrut, arms=arms)
        e = self.expr()
        if self.atsym('='):
            self.next()
            return N('assign', w, target=e, e=self.expr())
        return N('expr', w, e=e)

    def ifstmt(self):
        w = self.next().where
        c = self.cond()
        then = self.block()
        els = None
        if self.atkw('else'):
            self.next()
            if self.atkw('if'):
                els = N('block', self.peek().where, stmts=[self.ifstmt()])
            else:
                els = self.block()
        return N('if', w, cond=c, then=then, els=els)

    # expressions

    def expr0(self):
        save, self.nolit = self.nolit, 0
        e = self.expr()
        self.nolit = save
        return e

    def expr(self, lvl=0):
        if lvl == len(self.BIN):
            return self.unary()
        left = self.expr(lvl + 1)
        while self.peek().k == 'sym' and self.peek().v in self.BIN[lvl]:
            op = self.next()
            left = N('bin', op.where, op=op.v, l=left, r=self.expr(lvl + 1))
        return left

    def unary(self):
        t = self.peek()
        if t.k == 'sym' and t.v in ('-', '!', '&'):
            self.next()
            return N('un', t.where, op=t.v, e=self.unary())
        if t.k == 'kw' and t.v == 'try':
            self.next()
            return N('try', t.where, e=self.unary())
        return self.postfix(self.primary())

    def postfix(self, e):
        while True:
            t = self.peek()
            if self.atsym('.'):
                self.next()
                name = self.expect('id').v
                if self.atsym('('):
                    e = N('mcall', t.where, recv=e, name=name, args=self.args())
                else:
                    e = N('field', t.where, e=e, name=name)
            elif self.atsym('['):
                self.next()
                i = self.expr0()
                self.sym(']')
                e = N('index', t.where, e=e, i=i)
            else:
                return e

    def args(self):
        self.sym('(')
        save, self.nolit = self.nolit, 0
        a = []
        while not self.atsym(')'):
            a.append(self.expr())
            if self.atsym(','):
                self.next()
        self.sym(')')
        self.nolit = save
        return a

    def primary(self):
        t = self.next()
        w = t.where
        if t.k == 'int':
            return N('int', w, v=int(t.v))
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
            if self.atsym(']'):
                self.next()
                et = self.type()
                self.sym('{')
                self.sym('}')
                return N('arrlit', w, et=et, items=[])
            save, self.nolit = self.nolit, 0
            items = []
            while not self.atsym(']'):
                items.append(self.expr())
                if self.atsym(','):
                    self.next()
            self.sym(']')
            self.nolit = save
            return N('arrlit', w, et=None, items=items)
        if t.k == 'id':
            if t.v[0].isupper():
                targs = []
                if self.atsym('['):
                    self.next()
                    targs.append(self.type())
                    while self.atsym(','):
                        self.next()
                        targs.append(self.type())
                    self.sym(']')
                if self.atsym('.'):
                    self.next()
                    m = self.expect('id').v
                    return N('scall', w, tname=t.v, targs=targs, name=m, args=self.args())
                if self.atsym('{') and not self.nolit:
                    return N('lit', w, name=t.v, targs=targs, inits=self.inits())
                raise CompileError(f"expected '{{' or '.' after the type name {t.v}", w)
            if self.atsym('('):
                return N('call', w, name=t.v, args=self.args())
            return N('name', w, v=t.v)
        raise CompileError(f"unexpected {t.v or t.k!r}", w)

    def inits(self):
        self.sym('{')
        save, self.nolit = self.nolit, 0
        self.skipsemi()
        fs = []
        while not self.atsym('}'):
            n = self.expect('id').v
            self.sym(':')
            fs.append((n, self.expr()))
            if self.atsym(','):
                self.next()
            self.skipsemi()
        self.sym('}')
        self.nolit = save
        return fs

# ----------------------------------------------------------------- types
#
# A concrete type is a tuple: ('prim', name), ('arr', T), ('ref', T) or
# ('inst', DeclName, (args...)). Type parameters never appear in a concrete
# type: generic code is only ever checked with its parameters substituted.

PRIMS = {'int', 'bool', 'str', 'void'}
INT, BOOL, STR, VOID = ('prim', 'int'), ('prim', 'bool'), ('prim', 'str'), ('prim', 'void')


def mangle(t):
    k = t[0]
    if k == 'prim':
        return t[1]
    if k == 'ref':
        return 'R' + mangle(t[1])
    if k == 'arr':
        return 'A_' + mangle(t[1]) + '_'
    return t[1] + ('_L' + '_'.join(mangle(a) for a in t[2]) + '_J' if t[2] else '')


def tstr(t):
    k = t[0]
    if k == 'prim':
        return t[1]
    if k == 'ref':
        return '&' + tstr(t[1])
    if k == 'arr':
        return '[]' + tstr(t[1])
    return t[1] + ('[' + ', '.join(tstr(a) for a in t[2]) + ']' if t[2] else '')


def astr(a):
    if a[0] == 'ref':
        return '&' + astr(a[1])
    if a[0] == 'arr':
        return '[]' + astr(a[1])
    return a[1] + ('[' + ', '.join(astr(x) for x in a[2]) + ']' if a[2] else '')


def cstr(s):
    out = '"'
    for ch in s:
        out += {'\n': '\\n', '\t': '\\t', '"': '\\"', '\\': '\\\\'}.get(ch, ch)
    return out + '"'

# ----------------------------------------------------------------- compiler

class Var:
    def __init__(self, name, c, t, mutable):
        self.name, self.c, self.t, self.mutable = name, c, t, mutable
        self.state = 'live'


class Tmp:
    def __init__(self, c, t):
        self.c, self.t, self.live = c, t, True


class Val:
    """An evaluated expression. cat: 'const' (a copyable rvalue), 'tmp' (an
    owned rvalue held in a statement temporary), 'local' or 'place' (an
    lvalue: a field, an element, or the target of a borrow)."""
    def __init__(self, c, t, cat, var=None, tmp=None, viaref=False):
        self.c, self.t, self.cat, self.var, self.tmp, self.viaref = c, t, cat, var, tmp, viaref


class Inst:
    def __init__(self, cname, d, env, params, ret, label):
        self.cname, self.d, self.env = cname, d, env
        self.params, self.ret, self.label = params, ret, label


class Ctx:
    def __init__(self, inst):
        self.inst = inst
        self.lines = []
        self.ind = 1
        self.scopes = []
        self.frames = []
        self.dead = False
        self.nomove = 0
        self.n = 0

    def emit(self, s):
        self.lines.append('  ' * self.ind + s)

    def fresh(self, p='t'):
        self.n += 1
        return f"{p}{self.n}"


class Compiler:
    def __init__(self, decls):
        self.types, self.fns, self.methods, self.variants = {}, {}, {}, {}
        for d in decls:
            if d.kind == 'type':
                if d.name in self.types or d.name in PRIMS:
                    raise CompileError(f"type {d.name} is declared twice", d.where)
                self.types[d.name] = d
                if d.form == 'closed':
                    for i, (vn, _) in enumerate(d.variants):
                        self.variants[vn] = (d, i)
        for d in decls:
            if d.kind != 'fn':
                continue
            if d.recv:
                ra = d.recv[1]
                base = ra[1] if ra[0] == 'ref' else ra
                if base[0] != 'name':
                    raise CompileError("a receiver must name a type", d.where)
                key = base[1]
                rtps = [a[1] for a in base[2] if a[0] == 'name' and not a[2]
                        and a[1] not in self.types and a[1] not in PRIMS]
                d.all_tparams = [(n, None) for n in rtps] + d.tparams
                d.key = key
                if (key, d.name) in self.methods:
                    raise CompileError(f"{key}.{d.name} is declared twice", d.where)
                self.methods[(key, d.name)] = d
            else:
                d.all_tparams = d.tparams
                d.key = None
                if d.name in self.fns:
                    raise CompileError(f"function {d.name} is declared twice", d.where)
                self.fns[d.name] = d
        self.need = []              # concrete inst/arr types, in discovery order
        self.needset = set()
        self.instances = {}
        self.work = []
        self.protos = []
        self.bodies = []
        self.ctx = None

    # ---- types

    def addneed(self, t):
        if t[0] in ('inst', 'arr') and t not in self.needset:
            self.needset.add(t)
            self.need.append(t)

    def env_of(self, t):
        d = self.types[t[1]]
        return {n: a for (n, _), a in zip(d.tparams, t[2])}

    def resolve(self, a, env, where):
        k = a[0]
        if k == 'ref':
            return ('ref', self.resolve(a[1], env, where))
        if k == 'arr':
            t = ('arr', self.resolve(a[1], env, where))
            self.addneed(t)
            return t
        n, args = a[1], a[2]
        if n in env and not args:
            return env[n]
        if n in PRIMS and not args:
            return ('prim', n)
        d = self.types.get(n)
        if d is None:
            raise CompileError(f"unknown type {n}", where)
        if d.form == 'interface':
            raise CompileError(f"{n} is an interface: it can only constrain a type parameter", where)
        if len(args) != len(d.tparams):
            raise CompileError(f"{n} takes {len(d.tparams)} type arguments, not {len(args)}", where)
        t = ('inst', n, tuple(self.resolve(x, env, where) for x in args))
        for (pn, con), at in zip(d.tparams, t[2]):
            if con:
                self.check_constraint(pn, con, at, where)
        self.addneed(t)
        return t

    def partial(self, a, bind, tps, where):
        """Resolve a callee's type AST with the type parameters bound so far,
        or return None if one is still unbound."""
        def free(x):
            if x[0] in ('ref', 'arr'):
                return free(x[1])
            if x[1] in tps and not x[2] and x[1] not in bind:
                return True
            return any(free(y) for y in x[2])
        if free(a):
            return None
        return self.resolve(a, bind, where)

    def unify(self, a, t, bind, tps):
        k = a[0]
        if k == 'ref':
            return t[0] == 'ref' and self.unify(a[1], t[1], bind, tps)
        if k == 'arr':
            return t[0] == 'arr' and self.unify(a[1], t[1], bind, tps)
        n, args = a[1], a[2]
        if n in tps and not args:
            if n in bind:
                return bind[n] == t
            bind[n] = t
            return True
        if n in PRIMS:
            return t == ('prim', n)
        if t[0] != 'inst' or t[1] != n or len(t[2]) != len(args):
            return False
        return all(self.unify(x, y, bind, tps) for x, y in zip(args, t[2]))

    def is_copy(self, t):
        if t[0] in ('prim', 'ref'):
            return True
        if t[0] == 'arr':
            return False
        return self.types[t[1]].form == 'value'

    def ctype(self, t):
        k = t[0]
        if k == 'prim':
            return {'int': 'int64_t', 'bool': 'bool', 'str': 'const char*', 'void': 'void'}[t[1]]
        if k == 'ref':
            return self.ctype(t[1]) + '*'
        if k == 'arr':
            return mangle(t) + '*'
        return mangle(t) + ('' if self.types[t[1]].form == 'value' else '*')

    def typekey(self, t):
        if t[0] == 'ref':
            return self.typekey(t[1])
        if t[0] == 'prim':
            return t[1]
        if t[0] == 'arr':
            return '[]'
        return t[1]

    def check_constraint(self, pname, con, t, where):
        idecl = self.types.get(con)
        if idecl is None or idecl.form != 'interface':
            raise CompileError(f"{con} is not an interface", where)
        key = self.typekey(t)
        for m in idecl.members:
            if (key, m) not in self.methods:
                raise CompileError(f"{tstr(t)} does not satisfy {con} (type parameter {pname}): "
                                   f"it has no method '{m}'", where)

    # ---- function instances

    def instance(self, d, targs, where):
        key = (id(d), targs)
        if key in self.instances:
            return self.instances[key]
        env = {n: t for (n, _), t in zip(d.all_tparams, targs)}
        for (n, con), t in zip(d.all_tparams, targs):
            if con:
                self.check_constraint(n, con, t, where)
        base = (d.key + '_' if d.key else '') + d.name
        cname = 'f_' + base + ('__' + '_'.join(mangle(t) for t in targs) if targs else '')
        label = (d.key + '.' if d.key else '') + d.name
        if targs:
            label += '[' + ', '.join(tstr(t) for t in targs) + ']'
        params = []
        if d.recv and d.recv[0] is not None:
            params.append((d.recv[0], self.resolve(d.recv[1], env, d.where)))
        for pn, pa in d.params:
            params.append((pn, self.resolve(pa, env, d.where)))
        ret = self.resolve(d.ret, env, d.where) if d.ret else VOID
        inst = Inst(cname, d, env, params, ret, label)
        self.instances[key] = inst
        self.work.append(inst)
        ps = ', '.join(f"{self.ctype(t)} p_{n}" for n, t in params) or 'void'
        self.protos.append(f"static {self.ctype(ret)} {cname}({ps});")
        return inst

    def compile(self):
        main = self.fns.get('main')
        if main is None:
            raise CompileError("no fn main")
        self.instance(main, (), main.where)
        while self.work:
            self.emit_instance(self.work.pop(0))
        return self.assemble()

    def emit_instance(self, inst):
        ctx = Ctx(inst)
        self.ctx = ctx
        d = inst.d
        try:
            ctx.scopes.append([])
            for n, t in inst.params:
                ctx.scopes[-1].append(Var(n, 'p_' + n, t, False))
            self.block(d.body, scope=False)
            if not ctx.dead:
                if inst.ret != VOID:
                    raise CompileError(f"{inst.label} may reach its end without returning", d.where)
                self.drop_scope(ctx.scopes[-1])
            ctx.scopes.pop()
        except CompileError as e:
            if d.tparams or (d.recv and d.all_tparams):
                if ' (in ' not in e.msg:
                    raise CompileError(f"{e.msg} (in {inst.label})", e.where)
            raise
        ps = ', '.join(f"{self.ctype(t)} p_{n}" for n, t in inst.params) or 'void'
        self.bodies.append(f"static {self.ctype(inst.ret)} {inst.cname}({ps}) {{")
        self.bodies.extend(ctx.lines)
        self.bodies.append("}")

    # ---- scopes, temporaries and drops

    def emit(self, s):
        self.ctx.emit(s)

    def lookup(self, name, where):
        for sc in reversed(self.ctx.scopes):
            for v in reversed(sc):
                if v.name == name:
                    return v
        raise CompileError(f"unknown name '{name}'", where)

    def drop(self, c, t):
        if not self.is_copy(t):
            self.emit(f"drop_{mangle(t)}({c});")

    def drop_frame(self, fr):
        for tmp in reversed(fr):
            if tmp.live:
                self.drop(tmp.c, tmp.t)

    def drop_scope(self, sc):
        for v in reversed(sc):
            if v.state == 'live' and not self.is_copy(v.t):
                self.drop(v.c, v.t)

    def cleanup_all(self):
        """On return or a failed try: free every live temporary of the
        statements being evaluated, then every live owned local."""
        for fr in reversed(self.ctx.frames):
            self.drop_frame(fr)
        for sc in reversed(self.ctx.scopes):
            self.drop_scope(sc)

    def snapshot(self):
        return {v: v.state for sc in self.ctx.scopes for v in sc}

    def restore(self, snap):
        for v, s in snap.items():
            v.state = s

    def merge(self, s1, d1, s2, d2, where):
        if d1 and d2:
            self.ctx.dead = True
            return
        if d1 or d2:
            self.restore(s2 if d1 else s1)
            self.ctx.dead = False
            return
        for v in s1:
            if s1[v] != s2[v]:
                raise CompileError(f"'{v.name}' is moved on one path but not another", where)
        self.restore(s1)
        self.ctx.dead = False

    def block(self, b, scope=True):
        ctx = self.ctx
        if scope:
            ctx.scopes.append([])
        for s in b.stmts:
            if ctx.dead:
                break
            ctx.frames.append([])
            self.stmt(s)
            fr = ctx.frames.pop()
            if not ctx.dead:
                self.drop_frame(fr)
        if scope:
            sc = ctx.scopes.pop()
            if not ctx.dead:
                self.drop_scope(sc)

    # ---- ownership operations on values

    def consume(self, v, where):
        if self.is_copy(v.t):
            return v.c
        if v.cat == 'tmp':
            if self.ctx.nomove and v.tmp not in self.ctx.frames[-1]:
                raise CompileError("cannot move a value inside && or ||", where)
            v.tmp.live = False
            return v.c
        if v.cat == 'local':
            var = v.var
            if var.state != 'live':
                raise CompileError(f"'{var.name}' was moved and has no value here", where)
            if self.ctx.nomove:
                raise CompileError(f"cannot move '{var.name}' inside && or ||", where)
            var.state = 'moved'
            return v.c
        if v.viaref:
            raise CompileError(f"cannot move a {tstr(v.t)} out of a borrow", where)
        raise CompileError(f"cannot move a {tstr(v.t)} out of a field or element; "
                           "use pop() or a consuming match on its owner", where)

    def deref(self, v):
        if v.t[0] == 'ref':
            return Val(f"(*{v.c})", v.t[1], 'place', viaref=True)
        return v

    def rd(self, v):
        """Read a value: a borrow of a copyable type reads through."""
        if v.t[0] == 'ref' and self.is_copy(v.t[1]):
            return self.deref(v)
        return v

    def lvalue(self, v):
        if v.cat in ('local', 'place', 'tmp'):
            return v.c
        n = self.ctx.fresh('m')
        self.emit(f"{self.ctype(v.t)} {n} = {v.c};")
        return n

    def holdtmp(self, c, t):
        if self.is_copy(t):
            return Val(c, t, 'const')
        tmp = Tmp(c, t)
        self.ctx.frames[-1].append(tmp)
        return Val(c, t, 'tmp', tmp=tmp)

    # ---- statements

    def stmt(self, s):
        getattr(self, 's_' + s.kind)(s)

    def s_expr(self, s):
        self.expr(s.e)

    def s_let(self, s):
        env = self.ctx.inst.env
        exp = self.resolve(s.ty, env, s.where) if s.ty else None
        v = self.rd(self.expr(s.e, exp))
        if v.t == VOID:
            raise CompileError("this expression has no value", s.where)
        if v.t[0] == 'ref':
            raise CompileError(f"cannot store a borrow ({tstr(v.t)}) in a local", s.where)
        if exp and v.t != exp:
            raise CompileError(f"cannot use {tstr(v.t)} as {tstr(exp)}", s.where)
        for var in self.ctx.scopes[-1]:
            if var.name == s.name:
                raise CompileError(f"'{s.name}' is already declared in this block", s.where)
        c = self.consume(v, s.where)
        cn = self.ctx.fresh(f"v_{s.name}_")
        self.emit(f"{self.ctype(v.t)} {cn} = {c};")
        self.ctx.scopes[-1].append(Var(s.name, cn, v.t, s.mutable))

    def s_assign(self, s):
        tg = s.target
        if tg.kind == 'name':
            var = self.lookup(tg.v, s.where)
            if not var.mutable:
                raise CompileError(f"cannot assign to '{var.name}': declare it with var", s.where)
            v = self.rd(self.expr(s.e, var.t))
            if v.t != var.t:
                raise CompileError(f"cannot assign {tstr(v.t)} to {tstr(var.t)}", s.where)
            c = self.consume(v, s.where)
            if var.state == 'live':
                self.drop(var.c, var.t)
            self.emit(f"{var.c} = {c};")
            var.state = 'live'
            return
        if tg.kind not in ('field', 'index'):
            raise CompileError("cannot assign to this expression", s.where)
        v = self.rd(self.expr(s.e))
        c = self.consume(v, s.where)
        place = self.expr(tg)
        if place.t != v.t:
            raise CompileError(f"cannot assign {tstr(v.t)} to {tstr(place.t)}", s.where)
        if self.is_copy(v.t):
            self.emit(f"{place.c} = {c};")
        else:
            old = self.ctx.fresh('old')
            self.emit(f"{{ {self.ctype(v.t)} {old} = {place.c}; {place.c} = {c}; "
                      f"drop_{mangle(v.t)}({old}); }}")

    def cond_bool(self, e):
        ctx = self.ctx
        ctx.frames.append([])
        v = self.rd(self.expr(e, BOOL))
        if v.t != BOOL:
            raise CompileError(f"a condition must be bool, not {tstr(v.t)}", e.where)
        cn = ctx.fresh('c')
        self.emit(f"bool {cn} = {v.c};")
        self.drop_frame(ctx.frames.pop())
        return cn

    def s_if(self, s):
        ctx = self.ctx
        c = self.cond_bool(s.cond)
        snap = self.snapshot()
        self.emit(f"if ({c}) {{")
        ctx.ind += 1
        self.block(s.then)
        ctx.ind -= 1
        s1, d1 = self.snapshot(), ctx.dead
        self.restore(snap)
        ctx.dead = False
        if s.els:
            self.emit("} else {")
            ctx.ind += 1
            self.block(s.els)
            ctx.ind -= 1
        self.emit("}")
        self.merge(s1, d1, self.snapshot(), ctx.dead, s.where)

    def loop_body(self, body, where):
        ctx = self.ctx
        snap = self.snapshot()
        self.block(body)
        if not ctx.dead:
            for v, st in snap.items():
                if v.state != st:
                    raise CompileError(f"'{v.name}' is moved inside the loop", where)
        self.restore(snap)
        ctx.dead = False

    def s_while(self, s):
        ctx = self.ctx
        self.emit("while (1) {")
        ctx.ind += 1
        c = self.cond_bool(s.cond)
        self.emit(f"if (!{c}) break;")
        self.loop_body(s.body, s.where)
        ctx.ind -= 1
        self.emit("}")

    def s_for(self, s):
        ctx = self.ctx
        a = self.rd(self.expr(s.a, INT))
        b = self.rd(self.expr(s.b, INT))
        if a.t != INT or b.t != INT:
            raise CompileError("a for range needs int bounds", s.where)
        end = ctx.fresh('end')
        self.emit(f"int64_t {end} = {b.c};")
        iv = Var(s.name, ctx.fresh(f"v_{s.name}_"), INT, False)
        self.emit(f"for (int64_t {iv.c} = {a.c}; {iv.c} < {end}; {iv.c}++) {{")
        ctx.ind += 1
        ctx.scopes.append([iv])
        self.loop_body(s.body, s.where)
        ctx.scopes.pop()
        ctx.ind -= 1
        self.emit("}")

    def s_return(self, s):
        ctx = self.ctx
        ret = ctx.inst.ret
        if s.e is None:
            if ret != VOID:
                raise CompileError(f"return needs a {tstr(ret)} value", s.where)
            self.cleanup_all()
            self.emit("return;")
        else:
            v = self.rd(self.expr(s.e, ret))
            if v.t != ret:
                raise CompileError(f"cannot return {tstr(v.t)} from a function returning {tstr(ret)}",
                                   s.where)
            c = self.consume(v, s.where)
            rn = ctx.fresh('r')
            self.emit(f"{self.ctype(ret)} {rn} = {c};")
            self.cleanup_all()
            self.emit(f"return {rn};")
        ctx.dead = True

    def s_match(self, s):
        ctx = self.ctx
        v = self.expr(s.e)
        base = v.t[1] if v.t[0] == 'ref' else v.t
        if base[0] != 'inst' or self.types[base[1]].form != 'closed':
            raise CompileError(f"match needs a closed type, not {tstr(base)}", s.where)
        d = self.types[base[1]]
        env = self.env_of(base)
        consuming = v.t[0] != 'ref' and v.cat in ('local', 'tmp')
        sn = ctx.fresh('s')
        if consuming:
            self.emit(f"{self.ctype(base)} {sn} = {self.consume(v, s.where)};")
        else:
            src = f"(*{v.c})" if v.t[0] == 'ref' else v.c
            self.emit(f"{self.ctype(base)} {sn} = {src};")
        seen = set()
        snap = self.snapshot()
        results = []
        first = True
        for arm in s.arms:
            if arm.variant is None:
                self.emit("} else {" if not first else "{")
                vi, fields = None, None
            else:
                if arm.variant not in self.variants or self.variants[arm.variant][0] is not d:
                    raise CompileError(f"{arm.variant} is not a variant of {tstr(base)}", arm.where)
                vi = self.variants[arm.variant][1]
                if vi in seen:
                    raise CompileError(f"{arm.variant} is matched twice", arm.where)
                seen.add(vi)
                fields = d.variants[vi][1]
                self.emit(("if" if first else "} else if") + f" ({sn}->tag == {vi}) {{")
            first = False
            ctx.ind += 1
            self.restore(snap)
            ctx.dead = False
            ctx.scopes.append([])
            if arm.variant is None:
                if consuming:
                    self.emit(f"drop_{mangle(base)}({sn});")
            else:
                vname = arm.variant
                if arm.binds is not None and len(arm.binds) != len(fields):
                    raise CompileError(f"{vname} has {len(fields)} fields; bind all of them or none",
                                       arm.where)
                for i, (fn, fa) in enumerate(fields):
                    ft = self.resolve(fa, env, arm.where)
                    acc = f"{sn}->u.V_{vname}.m_{fn}"
                    bound = arm.binds[i] if arm.binds else None
                    if consuming:
                        if bound:
                            cn = ctx.fresh(f"v_{bound}_")
                            self.emit(f"{self.ctype(ft)} {cn} = {acc};")
                            ctx.scopes[-1].append(Var(bound, cn, ft, False))
                        else:
                            self.drop(acc, ft)
                    elif bound:
                        cn = ctx.fresh(f"v_{bound}_")
                        if self.is_copy(ft):
                            self.emit(f"{self.ctype(ft)} {cn} = {acc};")
                            ctx.scopes[-1].append(Var(bound, cn, ft, False))
                        else:
                            self.emit(f"{self.ctype(ft)}* {cn} = &{acc};")
                            ctx.scopes[-1].append(Var(bound, cn, ('ref', ft), False))
                if consuming:
                    self.emit(f"rt_free({sn});")
            self.block(arm.body)
            sc = ctx.scopes.pop()
            if not ctx.dead:
                self.drop_scope(sc)
            ctx.ind -= 1
            results.append((self.snapshot(), ctx.dead))
        has_else = any(a.variant is None for a in s.arms)
        if not has_else and len(seen) != len(d.variants):
            missing = [vn for i, (vn, _) in enumerate(d.variants) if i not in seen]
            raise CompileError(f"match is not exhaustive: missing {', '.join(missing)}", s.where)
        self.emit("}")
        live = [r for r in results if not r[1]]
        if not live:
            ctx.dead = True
            return
        for st, _ in live[1:]:
            for var in st:
                if st[var] != live[0][0][var]:
                    raise CompileError(f"'{var.name}' is moved on one path but not another", s.where)
        self.restore(live[0][0])
        ctx.dead = False

    # ---- expressions

    def expr(self, e, exp=None):
        return getattr(self, 'x_' + e.kind)(e, exp)

    def x_int(self, e, exp):
        return Val(f"{e.v}LL", INT, 'const')

    def x_str(self, e, exp):
        return Val(cstr(e.v), STR, 'const')

    def x_bool(self, e, exp):
        return Val('true' if e.v else 'false', BOOL, 'const')

    def x_frac(self, e, exp):
        # Lang item: the compiler knows only that a fractional literal calls
        # fromFraction(units, scale). It does not know what that returns.
        if 'fromFraction' not in self.fns:
            raise CompileError("a fractional literal needs fn fromFraction(units: int, scale: int)",
                               e.where)
        whole, frac = e.v.split('.')
        args = [N('int', e.where, v=int(whole + frac)), N('int', e.where, v=len(frac))]
        return self.call(self.fns['fromFraction'], None, args, e.where, exp=exp)

    def x_name(self, e, exp):
        var = self.lookup(e.v, e.where)
        if var.state != 'live':
            raise CompileError(f"'{var.name}' was moved and has no value here", e.where)
        return Val(var.c, var.t, 'local', var=var)

    def x_un(self, e, exp):
        if e.op == '&':
            v = self.expr(e.e)
            if v.t[0] == 'ref':
                return v
            return Val(f"(&{self.lvalue(v)})", ('ref', v.t), 'const', var=v.var)
        v = self.rd(self.expr(e.e))
        if e.op == '!':
            if v.t != BOOL:
                raise CompileError(f"! needs a bool, not {tstr(v.t)}", e.where)
            return Val(f"(!{v.c})", BOOL, 'const')
        if v.t == INT:
            return Val(f"(-{v.c})", INT, 'const')
        if v.t[0] == 'inst':
            return self.op_method(v, 'neg', [], e.where)
        raise CompileError(f"unary - is not defined for {tstr(v.t)}", e.where)

    OPS = {'+': 'add', '-': 'sub', '*': 'mul', '/': 'div', '%': 'rem'}

    def x_bin(self, e, exp):
        ctx = self.ctx
        if e.op in ('&&', '||'):
            lv = self.rd(self.expr(e.l, BOOL))
            if lv.t != BOOL:
                raise CompileError(f"{e.op} needs bool operands", e.where)
            ln = ctx.fresh('b')
            self.emit(f"bool {ln} = {lv.c};")
            self.emit(f"if ({'' if e.op == '&&' else '!'}{ln}) {{")
            ctx.ind += 1
            ctx.frames.append([])
            ctx.nomove += 1
            rv = self.rd(self.expr(e.r, BOOL))
            if rv.t != BOOL:
                raise CompileError(f"{e.op} needs bool operands", e.where)
            self.emit(f"{ln} = {rv.c};")
            ctx.nomove -= 1
            self.drop_frame(ctx.frames.pop())
            ctx.ind -= 1
            self.emit("}")
            return Val(ln, BOOL, 'const')
        lv = self.rd(self.expr(e.l))
        rv = self.rd(self.expr(e.r, lv.t))
        if lv.t == INT and rv.t == INT:
            if e.op in ('/', '%'):
                f = 'rt_div' if e.op == '/' else 'rt_rem'
                return Val(f"{f}({lv.c}, {rv.c}, {cstr(e.where)})", INT, 'const')
            if e.op in self.OPS:
                return Val(f"({lv.c} {e.op} {rv.c})", INT, 'const')
            return Val(f"({lv.c} {e.op} {rv.c})", BOOL, 'const')
        if lv.t == BOOL and rv.t == BOOL and e.op in ('==', '!='):
            return Val(f"({lv.c} {e.op} {rv.c})", BOOL, 'const')
        if lv.t[0] == 'inst':
            if e.op in self.OPS:
                return self.op_method(lv, self.OPS[e.op], [rv], e.where)
            key = self.typekey(lv.t)
            if e.op in ('==', '!=') and (key, 'eq') in self.methods:
                r = self.op_method(lv, 'eq', [rv], e.where)
                return Val(r.c if e.op == '==' else f"(!{r.c})", BOOL, 'const')
            r = self.op_method(lv, 'cmp', [rv], e.where)
            return Val(f"({r.c} {e.op} 0)", BOOL, 'const')
        raise CompileError(f"operator {e.op} is not defined for {tstr(lv.t)} and {tstr(rv.t)}",
                           e.where)

    def op_method(self, recv, name, args, where):
        d = self.methods.get((self.typekey(recv.t), name))
        if d is None:
            raise CompileError(f"{tstr(recv.t)} has no method '{name}' for this operator", where)
        return self.call(d, recv, args, where)

    def x_field(self, e, exp):
        v = self.deref(self.expr(e.e))
        t = v.t
        if t[0] != 'inst':
            raise CompileError(f"{tstr(t)} has no fields", e.where)
        d = self.types[t[1]]
        if d.form == 'closed':
            raise CompileError(f"{tstr(t)} is a closed type: use match to reach its fields", e.where)
        fa = dict(d.fields).get(e.name)
        if fa is None:
            raise CompileError(f"{tstr(t)} has no field '{e.name}'", e.where)
        ft = self.resolve(fa, self.env_of(t), e.where)
        sep = '.' if d.form == 'value' else '->'
        return Val(f"{v.c}{sep}m_{e.name}", ft, 'place', viaref=v.viaref)

    def arr_of(self, v, where):
        v = self.deref(v)
        if v.t[0] != 'arr':
            raise CompileError(f"{tstr(v.t)} is not an array", where)
        return v

    def x_index(self, e, exp):
        a = self.arr_of(self.expr(e.e), e.where)
        i = self.rd(self.expr(e.i, INT))
        if i.t != INT:
            raise CompileError("an index must be an int", e.where)
        return Val(f"{a.c}->data[rt_idx({i.c}, {a.c}->len, {cstr(e.where)})]", a.t[1], 'place',
                   viaref=a.viaref)

    def x_lit(self, e, exp):
        d = self.types.get(e.name)
        vname = None
        if d is None:
            if e.name not in self.variants:
                raise CompileError(f"unknown type or variant {e.name}", e.where)
            d, vi = self.variants[e.name]
            vname = e.name
            fields = d.variants[vi][1]
        else:
            if d.form == 'closed':
                raise CompileError(f"construct a variant of {e.name}, not {e.name} itself", e.where)
            if d.form == 'interface':
                raise CompileError(f"{e.name} is an interface", e.where)
            fields = d.fields
        tps = [n for n, _ in d.tparams]
        bind = {}
        if e.targs:
            if len(e.targs) != len(tps):
                raise CompileError(f"{d.name} takes {len(tps)} type arguments", e.where)
            for n, ta in zip(tps, e.targs):
                bind[n] = self.resolve(ta, self.ctx.inst.env, e.where)
        elif exp and exp[0] == 'inst' and exp[1] == d.name:
            bind = dict(zip(tps, exp[2]))
        fd = dict(fields)
        given = [n for n, _ in e.inits]
        for n in given:
            if n not in fd:
                raise CompileError(f"{e.name} has no field '{n}'", e.where)
        for n, _ in fields:
            if n not in given:
                raise CompileError(f"field '{n}' of {e.name} needs a value", e.where)
        vals = []
        for n, ex in e.inits:
            fa = fd[n]
            v = self.rd(self.expr(ex, self.partial(fa, bind, tps, e.where)))
            if not self.unify(fa, v.t, bind, tps):
                raise CompileError(f"field '{n}' of {e.name} is {astr(fa)}, not {tstr(v.t)}", e.where)
            vals.append((n, v))
        missing = [n for n in tps if n not in bind]
        if missing:
            raise CompileError(f"cannot infer {', '.join(missing)} for {e.name}; "
                               f"write {e.name}[...]{{...}}", e.where)
        t = ('inst', d.name, tuple(bind[n] for n in tps))
        self.resolve(('name', d.name, [('name', n, []) for n in tps]), bind, e.where)
        cs = [(n, self.consume(v, e.where)) for n, v in vals]
        m = mangle(t)
        if d.form == 'value':
            inner = ', '.join(f".m_{n} = {c}" for n, c in cs) or '0'
            return Val(f"(({m}){{{inner}}})", t, 'const')
        tn = self.ctx.fresh('o')
        self.emit(f"{m}* {tn} = rt_alloc(sizeof({m}));")
        if vname:
            self.emit(f"{tn}->tag = {vi};")
            for n, c in cs:
                self.emit(f"{tn}->u.V_{vname}.m_{n} = {c};")
        else:
            for n, c in cs:
                self.emit(f"{tn}->m_{n} = {c};")
        return self.holdtmp(tn, t)

    def x_arrlit(self, e, exp):
        env = self.ctx.inst.env
        et = self.resolve(e.et, env, e.where) if e.et else (exp[1] if exp and exp[0] == 'arr' else None)
        vals = []
        for it in e.items:
            v = self.rd(self.expr(it, et))
            if et is None:
                et = v.t
            if v.t != et:
                raise CompileError(f"array element is {tstr(v.t)}, not {tstr(et)}", e.where)
            vals.append(v)
        if et is None:
            raise CompileError("cannot infer the element type; write []T{}", e.where)
        t = ('arr', et)
        self.addneed(t)
        cs = [self.consume(v, e.where) for v in vals]
        tn = self.ctx.fresh('a')
        self.emit(f"{mangle(t)}* {tn} = arr_new_{mangle(t)}();")
        for c in cs:
            self.emit(f"arr_push_{mangle(t)}({tn}, {c});")
        return self.holdtmp(tn, t)

    def x_try(self, e, exp):
        # Lang item: try needs a closed type with one-field variants Ok and Err.
        ctx = self.ctx
        v = self.expr(e.e)
        t = v.t
        ret = ctx.inst.ret

        def okerr(tt):
            if tt[0] != 'inst' or self.types[tt[1]].form != 'closed':
                return None
            d = self.types[tt[1]]
            vs = {vn: (i, fs) for i, (vn, fs) in enumerate(d.variants)}
            if 'Ok' not in vs or 'Err' not in vs or len(vs['Ok'][1]) != 1 or len(vs['Err'][1]) != 1:
                return None
            env = self.env_of(tt)
            return (vs['Ok'][0], vs['Ok'][1][0][0], self.resolve(vs['Ok'][1][0][1], env, e.where),
                    vs['Err'][0], vs['Err'][1][0][0], self.resolve(vs['Err'][1][0][1], env, e.where))
        a = okerr(t)
        if a is None:
            raise CompileError(f"try needs a value with Ok and Err variants, not {tstr(t)}", e.where)
        r = okerr(ret)
        if r is None:
            raise CompileError(f"try needs a function returning Ok/Err, and {ctx.inst.label} "
                               f"returns {tstr(ret)}", e.where)
        if a[5] != r[5]:
            raise CompileError(f"try: the error type {tstr(a[5])} is not {tstr(r[5])}", e.where)
        sn = ctx.fresh('tr')
        self.emit(f"{self.ctype(t)} {sn} = {self.consume(v, e.where)};")
        self.emit(f"if ({sn}->tag == {a[3]}) {{")
        ctx.ind += 1
        en = ctx.fresh('e')
        self.emit(f"{self.ctype(a[5])} {en} = {sn}->u.V_Err.m_{a[4]};")
        self.emit(f"rt_free({sn});")
        rn = ctx.fresh('r')
        rm = mangle(ret)
        self.emit(f"{rm}* {rn} = rt_alloc(sizeof({rm}));")
        self.emit(f"{rn}->tag = {r[3]};")
        self.emit(f"{rn}->u.V_Err.m_{r[4]} = {en};")
        self.cleanup_all()
        self.emit(f"return {rn};")
        ctx.ind -= 1
        self.emit("}")
        on = ctx.fresh('ok')
        self.emit(f"{self.ctype(a[2])} {on} = {sn}->u.V_Ok.m_{a[1]};")
        self.emit(f"rt_free({sn});")
        return self.holdtmp(on, a[2])

    # ---- calls

    def x_call(self, e, exp):
        b = getattr(self, 'b_' + e.name, None)
        if b is not None:
            return b(e, exp)
        d = self.fns.get(e.name)
        if d is None:
            raise CompileError(f"unknown function '{e.name}'", e.where)
        return self.call(d, None, e.args, e.where, exp=exp)

    def x_mcall(self, e, exp):
        recv = self.expr(e.recv)
        d = self.methods.get((self.typekey(recv.t), e.name))
        if d is None:
            raise CompileError(f"{tstr(recv.t)} has no method '{e.name}'", e.where)
        if d.recv[0] is None:
            raise CompileError(f"{d.key}.{e.name} is an associated function; call it as "
                               f"{d.key}.{e.name}(...)", e.where)
        return self.call(d, recv, e.args, e.where, exp=exp)

    def x_scall(self, e, exp):
        t = self.resolve(('name', e.tname, e.targs), self.ctx.inst.env, e.where)
        d = self.methods.get((self.typekey(t), e.name))
        if d is None or d.recv[0] is not None:
            raise CompileError(f"{tstr(t)} has no associated function '{e.name}'", e.where)
        tps = [n for n, _ in d.all_tparams]
        bind = {}
        self.unify(d.recv[1], t, bind, tps)
        return self.call(d, None, e.args, e.where, bind=bind, exp=exp)

    def call(self, d, recv, args, where, bind=None, exp=None):
        tps = [n for n, _ in d.all_tparams]
        bind = dict(bind or {})
        cargs = []          # (Val or None, C text or None, param kind)
        borrowed = set()
        if recv is not None:
            ra = d.recv[1]
            rbase = ra[1] if ra[0] == 'ref' else ra
            rt = recv.t[1] if recv.t[0] == 'ref' else recv.t
            if not self.unify(rbase, rt, bind, tps):
                raise CompileError(f"the receiver is {tstr(rt)}, not {astr(rbase)}", where)
            if ra[0] == 'ref':
                if recv.t[0] == 'ref':
                    cargs.append((None, recv.c))
                else:
                    cargs.append((None, f"(&{self.lvalue(recv)})"))
                    if recv.cat == 'local':
                        borrowed.add(recv.var)
            elif recv.t[0] == 'ref':
                if not self.is_copy(rt):
                    raise CompileError(f"{d.key}.{d.name} takes its receiver by value, and a "
                                       f"{tstr(rt)} cannot be moved out of a borrow", where)
                cargs.append((None, f"(*{recv.c})"))
            else:
                cargs.append((recv, None))
        if len(args) != len(d.params):
            raise CompileError(f"{d.name} takes {len(d.params)} arguments, not {len(args)}", where)
        for i, ((pn, pa), a) in enumerate(zip(d.params, args), 1):
            if isinstance(a, Val):
                v = a
            else:
                v = self.expr(a, self.partial(pa, bind, tps, where))
            if pa[0] == 'ref':
                if v.t[0] == 'ref':
                    targ, c = v.t, v.c
                    if v.var is not None:
                        borrowed.add(v.var)
                else:
                    targ, c = ('ref', v.t), f"(&{self.lvalue(v)})"
                    if v.cat == 'local':
                        borrowed.add(v.var)
                cargs.append((None, c))
            else:
                if v.t[0] == 'ref':
                    if not self.is_copy(v.t[1]):
                        raise CompileError(f"argument {i} of {d.name}: cannot move a {tstr(v.t[1])} "
                                           "out of a borrow", where)
                    v = self.deref(v)
                targ = v.t
                cargs.append((v, None))
            if not self.unify(pa, targ, bind, tps):
                want = self.partial(pa, bind, tps, where)
                raise CompileError(f"argument {i} of {d.name} is "
                                   f"{tstr(want) if want else astr(pa)}, not {tstr(targ)}", where)
        cs = []
        for v, c in cargs:
            if c is None:
                if v.cat == 'local' and v.var in borrowed and not self.is_copy(v.t):
                    raise CompileError(f"cannot move '{v.var.name}' while another argument "
                                       "borrows it", where)
                c = self.consume(v, where)
            cs.append(c)
        if d.ret and exp is not None and any(n not in bind for n in tps):
            self.unify(d.ret, exp, bind, tps)
        missing = [n for n in tps if n not in bind]
        if missing:
            raise CompileError(f"cannot infer {', '.join(missing)} for {d.name}; "
                               "give the result a declared type", where)
        inst = self.instance(d, tuple(bind[n] for n in tps), where)
        text = f"{inst.cname}({', '.join(cs)})"
        if inst.ret == VOID:
            self.emit(text + ';')
            return Val('', VOID, 'const')
        tn = self.ctx.fresh('t')
        self.emit(f"{self.ctype(inst.ret)} {tn} = {text};")
        return self.holdtmp(tn, inst.ret)

    # ---- built-in functions (not types: these are the runtime's primitives)

    def nargs(self, e, n):
        if len(e.args) != n:
            raise CompileError(f"{e.name} takes {n} arguments", e.where)

    def b_print(self, e, exp, nl=False):
        for a in e.args:
            v = self.rd(self.expr(a))
            if v.t not in (INT, BOOL, STR):
                raise CompileError(f"print takes int, bool or str, not {tstr(v.t)}; "
                                   "call its show method", e.where)
            self.emit(f"rt_print_{v.t[1]}({v.c});")
        if nl:
            self.emit("rt_nl();")
        return Val('', VOID, 'const')

    def b_println(self, e, exp):
        return self.b_print(e, exp, True)

    def b_len(self, e, exp):
        self.nargs(e, 1)
        v = self.rd(self.expr(e.args[0]))
        if v.t == STR:
            return Val(f"((int64_t)strlen({v.c}))", INT, 'const')
        a = self.arr_of(v, e.where)
        return Val(f"{a.c}->len", INT, 'const')

    def b_append(self, e, exp):
        self.nargs(e, 2)
        a = self.arr_of(self.expr(e.args[0]), e.where)
        v = self.rd(self.expr(e.args[1], a.t[1]))
        if v.t != a.t[1]:
            raise CompileError(f"cannot append {tstr(v.t)} to {tstr(a.t)}", e.where)
        self.emit(f"arr_push_{mangle(a.t)}({a.c}, {self.consume(v, e.where)});")
        return Val('', VOID, 'const')

    def b_pop(self, e, exp):
        self.nargs(e, 1)
        a = self.arr_of(self.expr(e.args[0]), e.where)
        tn = self.ctx.fresh('t')
        self.emit(f"{self.ctype(a.t[1])} {tn} = arr_pop_{mangle(a.t)}({a.c}, {cstr(e.where)});")
        return self.holdtmp(tn, a.t[1])

    def b_swap(self, e, exp):
        self.nargs(e, 3)
        a = self.arr_of(self.expr(e.args[0]), e.where)
        i = self.rd(self.expr(e.args[1]))
        j = self.rd(self.expr(e.args[2]))
        self.emit(f"arr_swap_{mangle(a.t)}({a.c}, {i.c}, {j.c}, {cstr(e.where)});")
        return Val('', VOID, 'const')

    def b_drop(self, e, exp):
        self.nargs(e, 1)
        v = self.expr(e.args[0])
        if v.cat != 'local':
            raise CompileError("drop needs a local variable", e.where)
        self.drop(self.consume(v, e.where), v.t)
        return Val('', VOID, 'const')

    def b_streq(self, e, exp):
        self.nargs(e, 2)
        a = self.rd(self.expr(e.args[0]))
        b = self.rd(self.expr(e.args[1]))
        return Val(f"(strcmp({a.c}, {b.c}) == 0)", BOOL, 'const')

    def b_charAt(self, e, exp):
        self.nargs(e, 2)
        s = self.rd(self.expr(e.args[0]))
        i = self.rd(self.expr(e.args[1]))
        return Val(f"((int64_t)(unsigned char)({s.c})[rt_idx({i.c}, (int64_t)strlen({s.c}), "
                   f"{cstr(e.where)})])", INT, 'const')

    # ---- output

    def assemble(self):
        # Close the set of types: every field type of a needed type is needed.
        i = 0
        while i < len(self.need):
            t = self.need[i]
            i += 1
            if t[0] == 'arr':
                self.addneed(t[1])
                continue
            for ft in self.field_types(t):
                if ft[0] == 'ref':
                    raise CompileError(f"{tstr(t)} cannot hold a borrow ({tstr(ft)})",
                                       self.types[t[1]].where)
                self.addneed(ft)
        out = [RUNTIME]
        for t in self.need:
            out.append(f"typedef struct {mangle(t)} {mangle(t)};")
        done = set()

        def define(t):
            if t in done:
                return
            done.add(t)
            if t[0] == 'arr':
                out.append(f"struct {mangle(t)} {{ {self.ctype(t[1])}* data; int64_t len, cap; }};")
                return
            d = self.types[t[1]]
            env = self.env_of(t)
            for ft in self.field_types(t):
                if ft[0] == 'inst' and self.types[ft[1]].form == 'value':
                    define(ft)
            if d.form == 'value':
                for fn, fa in d.fields:
                    ft = self.resolve(fa, env, d.where)
                    if not self.is_copy(ft):
                        raise CompileError(f"value record {tstr(t)} cannot hold the owned field "
                                           f"'{fn}' ({tstr(ft)})", d.where)
            if d.form in ('value', 'record'):
                body = ' '.join(f"{self.ctype(self.resolve(fa, env, d.where))} m_{fn};"
                                for fn, fa in d.fields) or 'char m__;'
                out.append(f"struct {mangle(t)} {{ {body} }};")
            else:
                vs = []
                for vn, fs in d.variants:
                    body = ' '.join(f"{self.ctype(self.resolve(fa, env, d.where))} m_{fn};"
                                    for fn, fa in fs) or 'char m__;'
                    vs.append(f"struct {{ {body} }} V_{vn};")
                out.append(f"struct {mangle(t)} {{ int64_t tag; union {{ {' '.join(vs)} }} u; }};")
        for t in self.need:
            define(t)
        owned = [t for t in self.need if not self.is_copy(t)]
        for t in owned:
            out.append(f"static void drop_{mangle(t)}({self.ctype(t)} p);")
        for t in self.need:
            if t[0] == 'arr':
                m, ce = mangle(t), self.ctype(t[1])
                out.append(f"static {m}* arr_new_{m}(void) {{ return rt_alloc(sizeof({m})); }}")
                out.append(f"static void arr_push_{m}({m}* a, {ce} v) {{ if (a->len == a->cap) "
                           f"{{ a->cap = a->cap ? a->cap * 2 : 4; a->data = realloc(a->data, "
                           f"a->cap * sizeof({ce})); }} a->data[a->len++] = v; }}")
                out.append(f"static {ce} arr_pop_{m}({m}* a, const char* w) {{ if (a->len == 0) "
                           f"rt_panic(\"pop from an empty array\", w); return a->data[--a->len]; }}")
                out.append(f"static void arr_swap_{m}({m}* a, int64_t i, int64_t j, const char* w) "
                           f"{{ rt_idx(i, a->len, w); rt_idx(j, a->len, w); {ce} x = a->data[i]; "
                           f"a->data[i] = a->data[j]; a->data[j] = x; }}")
        for t in owned:
            m = mangle(t)
            lines = [f"static void drop_{m}({self.ctype(t)} p) {{", "  if (!p) return;"]
            if t[0] == 'arr':
                if not self.is_copy(t[1]):
                    lines.append(f"  for (int64_t i = 0; i < p->len; i++) "
                                 f"drop_{mangle(t[1])}(p->data[i]);")
                lines.append("  free(p->data);")
            else:
                d = self.types[t[1]]
                env = self.env_of(t)
                if d.form == 'record':
                    for fn, fa in d.fields:
                        ft = self.resolve(fa, env, d.where)
                        if not self.is_copy(ft):
                            lines.append(f"  drop_{mangle(ft)}(p->m_{fn});")
                else:
                    for i, (vn, fs) in enumerate(d.variants):
                        for fn, fa in fs:
                            ft = self.resolve(fa, env, d.where)
                            if not self.is_copy(ft):
                                lines.append(f"  if (p->tag == {i}) "
                                             f"drop_{mangle(ft)}(p->u.V_{vn}.m_{fn});")
            lines.append("  rt_free(p);")
            lines.append("}")
            out.extend(lines)
        out.extend(self.protos)
        out.extend(self.bodies)
        out.append(MAIN)
        return '\n'.join(out) + '\n'

    def field_types(self, t):
        d = self.types[t[1]]
        env = self.env_of(t)
        if d.form == 'closed':
            return [self.resolve(fa, env, d.where) for _, fs in d.variants for _, fa in fs]
        return [self.resolve(fa, env, d.where) for _, fa in d.fields]


RUNTIME = r'''#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
#include <stdbool.h>
#include <string.h>
static int64_t rt_live = 0, rt_total = 0;
static void* rt_alloc(size_t n) { void* p = calloc(1, n ? n : 1); if (!p) { fputs("out of memory\n", stderr); exit(2); } rt_live++; rt_total++; return p; }
static void rt_free(void* p) { if (p) { rt_live--; free(p); } }
static void rt_panic(const char* m, const char* w) { fflush(stdout); fprintf(stderr, "panic: %s at %s\n", m, w); exit(3); }
static int64_t rt_idx(int64_t i, int64_t n, const char* w) { if (i < 0 || i >= n) rt_panic("index out of range", w); return i; }
static int64_t rt_div(int64_t a, int64_t b, const char* w) { if (b == 0) rt_panic("integer division by zero", w); if (b == -1 && a == INT64_MIN) rt_panic("integer division overflow", w); return a / b; }
static int64_t rt_rem(int64_t a, int64_t b, const char* w) { if (b == 0) rt_panic("integer division by zero", w); if (b == -1) return 0; return a % b; }
static void rt_print_int(int64_t v) { printf("%lld", (long long)v); }
static void rt_print_bool(bool v) { fputs(v ? "true" : "false", stdout); }
static void rt_print_str(const char* s) { fputs(s, stdout); }
static void rt_nl(void) { putchar('\n'); }
'''

MAIN = r'''int main(void) {
  f_main();
  fflush(stdout);
  if (getenv("GC_STATS")) fprintf(stderr, "allocations: %lld\n", (long long)rt_total);
  if (rt_live != 0) { fprintf(stderr, "leak: %lld objects still live\n", (long long)rt_live); return 5; }
  return 0;
}'''


def compile_files(paths):
    decls = []
    for p in paths:
        with open(p) as f:
            decls.extend(Parser(lex(f.read(), os.path.basename(p))).program())
    return Compiler(decls).compile()


def main(argv):
    here = os.path.dirname(os.path.abspath(__file__))
    args = argv[1:]
    run = '--run' in args
    emit_c = '--emit-c' in args
    prelude = '--no-prelude' not in args
    files = [a for a in args if not a.startswith('--')]
    if not files:
        print("usage: gcore.py FILE.gc [--run] [--emit-c] [--no-prelude]", file=sys.stderr)
        return 2
    paths = ([os.path.join(here, 'std.gc')] if prelude else []) + files
    try:
        c = compile_files(paths)
    except CompileError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    if emit_c:
        sys.stdout.write(c)
        return 0
    base = os.path.splitext(files[0])[0]
    cfile, exe = base + '.c', base + '.bin'
    with open(cfile, 'w') as f:
        f.write(c)
    r = subprocess.run(['gcc', '-std=gnu11', '-O1', '-w', '-o', exe, cfile])
    if r.returncode != 0:
        print("error: the C compiler failed (a gcore bug)", file=sys.stderr)
        return 4
    if run:
        return subprocess.run([exe]).returncode
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
