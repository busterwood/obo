"""The checker: every function body is checked once, generic or not (SPEC 6.2).

Type parameters are opaque: a T is owned unless `T: copy`, and its methods come
only from its constraints. The checker decides every move, copy, borrow and
drop, and writes the function as a C template: C text with holes for whatever
depends on the type arguments (C types, drop and dup glue, callees). The
emitter renders the template once per instance (emit.py).
"""
from syntax import CompileError, N
from sema import (INTS, PRIMS, INT, BOOL, STR, FLOAT, BYTE, VOID, RANGES, TypeDecl, Variant, FnDecl,
                  Const, Global, tstr, subst, has_tp, unify, decl_env, is_copy, field_types)


def cstr(s):
    if isinstance(s, str):
        s = s.encode('utf-8')
    return '"' + ''.join(chr(b) if 32 <= b < 127 and b not in (34, 92, 63) else '\\%03o' % b
                         for b in s) + '"'


def is_pod(t):
    """True when a value of t needs neither dup nor drop (decidable without type arguments)."""
    k = t[0]
    if k == 'prim':
        return t[1] != 'str'
    if k in ('fn', 'ref', 'slice'):
        return True
    if k in ('arr', 'tp', 'self'):
        return False
    d = t[1]
    return d.value and d.drop is None and all(is_pod(ft) for _, ft in field_types(t))


def is_lit(e):
    return e.kind in ('int', 'frac')


class Var:
    def __init__(self, name, c, t, mutable, kind, where):
        self.name, self.c, self.t, self.mutable, self.kind, self.where = name, c, t, mutable, kind, where
        self.state, self.broot = 'live', None


class Tmp:
    def __init__(self, c, t):
        self.c, self.t, self.live = c, t, True
        self.name = 'a temporary'


class Val:
    def __init__(self, c, t, cat, tmp=None, root=None, path=(), mutable=False, viaref=False,
                 var=None, glob=None, noreturn=False):
        self.c, self.t, self.cat, self.tmp, self.root, self.path = c, t, cat, tmp, root, path
        self.mutable, self.viaref, self.var, self.glob, self.noreturn = mutable, viaref, var, glob, noreturn


class Scope:
    def __init__(self):
        self.vars, self.freezes = [], []


class Loop:
    def __init__(self, nscopes, nframes, entry, infinite):
        self.nscopes, self.nframes, self.entry, self.infinite, self.breaks = nscopes, nframes, entry, infinite, []


class Sig:
    def __init__(self, label, kind, params, ret, tps=(), recv=None, fd=None, c=None, tpt=None,
                 bind=None, nown=0):
        self.label, self.kind, self.params, self.ret, self.tps = label, kind, params, ret, list(tps)
        self.recv, self.fd, self.c, self.tpt, self.bind, self.nown = recv, fd, c, tpt, bind or {}, nown


def fn_sig(fd, bind=None):
    recv = fd.recv_mode if fd.recv_mode in ('val', 'ref', 'mut') else None
    nown = len(fd.ast.tparams)
    return Sig(fd.label, 'extern' if fd.extern is not None else 'fn', [p[1] for p in fd.params], fd.ret,
               fd.tps, recv, fd, bind=bind, nown=nown)


INTRINSICS = {
    'strByte': (['s', INT], BYTE, 'rt_str_byte', True),
    'strSlice': (['s', INT, INT], STR, 'rt_str_sub', True),
    'strFromBytes': ([('slice', BYTE, False)], STR, 'rt_str_from_bytes', False),
    'strToBytes': (['s'], ('arr', BYTE), 'rt_str_to_bytes', False),
    'intToStr': ([INT], STR, 'rt_int_to_str', False),
    'i128ToStr': ([('prim', 'i128')], STR, 'rt_i128_to_str', False),
    'u128ToStr': ([('prim', 'u128')], STR, 'rt_u128_to_str', False),
    'u64ToStr': ([('prim', 'u64')], STR, 'rt_u64_to_str', False),
    'floatToStr': ([FLOAT], STR, 'rt_float_to_str', False),
    'parseFloat': (['s', ('ref', FLOAT, True)], BOOL, 'rt_parse_float', False),
    'writeFd': ([INT, 's'], INT, 'rt_write_fd', False),
    'readFile': (['s', ('ref', STR, True)], INT, 'rt_read_file', False),
    'args': ([], ('arr', STR), 'rt_args', False),
    'getenv': (['s', ('ref', STR, True)], BOOL, 'rt_getenv', False),
    'strFromC': ([('prim', 'ptr')], STR, 'rt_str_from_c', False),
}
BUILTINS = {'len', 'append', 'pop', 'swap', 'replace', 'move', 'array', 'panic', 'assert', 'exit',
            'isNull'}
OPS = {'+': 'add', '-': 'sub', '*': 'mul', '/': 'div', '%': 'rem'}
CMPS = ('==', '!=', '<', '<=', '>', '>=')


class Ctx:
    def __init__(self, fd):
        self.fd, self.lines, self.holes, self.ind = fd, [], [], 1
        self.scopes, self.frames, self.loops, self.pending = [], [], [], []
        self.dead, self.nomove, self.n = False, [], 0
        self.module = fd.module

    def fresh(self, p='t'):
        self.n += 1
        return f"{p}{self.n}"


class Checker:
    def __init__(self, prog):
        self.prog = prog
        self.ctx = None

    def err(self, msg, where):
        raise CompileError(msg, where)

    # ---------------------------------------------------------- templates

    def H(self, *hole):
        self.ctx.holes.append(hole)
        return f"\x01{len(self.ctx.holes) - 1}\x02"

    def T(self, t):
        if t[0] == 'prim':
            return CTYPES[t[1]]
        if t[0] == 'slice':
            return 'obx_slice'
        return self.H('T', t)

    def emit(self, s):
        self.ctx.lines.append('  ' * self.ctx.ind + s)

    def W(self, where):
        return cstr(where)

    def drop(self, c, t):
        if not is_pod(t):
            self.emit(self.H('drop', t, c) + ';')

    def dup(self, c, t):
        return c if is_pod(t) else self.H('dup', t, c)

    # ---------------------------------------------------------- functions

    def check_all(self):
        for m in self.prog.modules.values():
            for fd in m.fns:
                if fd.extern is None:
                    self.check_fn(fd)
            for d in m.scope.values():
                if isinstance(d, Global):
                    self.global_init(d)

    def check_fn(self, fd):
        ctx = self.ctx = Ctx(fd)
        sc = Scope()
        for n, t, isvar, w in fd.params:
            v = Var(n, 'p_' + n, t, isvar, 'param', w)
            sc.vars.append(v)
            if n == fd.bparam:
                fd.bvar = v
        ctx.scopes.append(sc)
        if fd.name == 'main' and fd.owner is None and fd.module is self.prog.main:
            if fd.params or fd.ret != VOID or fd.tps:
                self.err("main takes no parameters and returns nothing", fd.where)
        self.block(fd.ast.body, scope=False)
        if not ctx.dead:
            if fd.ret != VOID:
                self.err(f"{fd.label} may reach its end without a return", fd.where)
            self.drop_scope(sc)
        fd.template = (ctx.lines, ctx.holes)
        self.ctx = None

    # ---------------------------------------------------------- scopes and drops

    def lookup_var(self, name):
        for sc in reversed(self.ctx.scopes):
            for v in reversed(sc.vars):
                if v.name == name:
                    return v
        return None

    def declare(self, v):
        if self.lookup_var(v.name) is not None:
            self.err(f"'{v.name}' is already declared in this function (no shadowing)", v.where)
        self.ctx.scopes[-1].vars.append(v)

    def drop_frame(self, fr):
        for tmp in reversed(fr):
            if tmp.live:
                self.drop(tmp.c, tmp.t)

    def drop_scope(self, sc):
        for v in reversed(sc.vars):
            if v.state == 'live' and v.t[0] != 'ref':
                self.drop(v.c, v.t)

    def cleanup(self, nscopes=0, nframes=0):
        for fr in reversed(self.ctx.frames[nframes:]):
            self.drop_frame(fr)
        for sc in reversed(self.ctx.scopes[nscopes:]):
            self.drop_scope(sc)

    def snapshot(self):
        return {v: v.state for sc in self.ctx.scopes for v in sc.vars}

    def restore(self, snap):
        for v, s in snap.items():
            v.state = s

    def join(self, states, where):
        """states: list of (snapshot, dead). Sets the state after the join."""
        live = [s for s, d in states if not d]
        if not live:
            self.ctx.dead = True
            return
        for s in live[1:]:
            for v in s:
                if v in live[0] and s[v] != live[0][v]:
                    self.err(f"'{v.name}' is moved on one path but not another", where)
        self.restore(live[0])
        self.ctx.dead = False

    def block(self, b, scope=True):
        ctx = self.ctx
        if scope:
            ctx.scopes.append(Scope())
        stop = unreachable = False
        for s in b.stmts:
            if ctx.dead:
                if stop:
                    self.err("unreachable statement: the statement before it always returns, breaks, "
                             "continues or stops the program", s.where)
                unreachable, ctx.dead = True, False     # after a loop that never ends: checked anyway
            self.push_frame()
            self.stmt(s)
            stop = ctx.dead and s.kind in ('return', 'break', 'continue', 'expr')
            fr = self.pop_frame()
            if not ctx.dead:
                self.drop_frame(fr)
        ctx.dead = ctx.dead or unreachable
        if scope:
            sc = ctx.scopes.pop()
            if not ctx.dead:
                self.drop_scope(sc)

    def push_frame(self):
        self.ctx.frames.append([])
        self.ctx.pmarks = getattr(self.ctx, 'pmarks', []) + [len(self.ctx.pending)]

    def pop_frame(self):
        m = self.ctx.pmarks.pop()
        del self.ctx.pending[m:]
        return self.ctx.frames.pop()

    def holdtmp(self, c, t):
        n = self.ctx.fresh('t')
        self.emit(f"{self.T(t)} {n} = {c};")
        if is_pod(t):
            return Val(n, t, 'rv')
        tmp = Tmp(n, t)
        self.ctx.frames[-1].append(tmp)
        return Val(n, t, 'tmp', tmp=tmp, root=tmp, mutable=True)

    # ---------------------------------------------------------- ownership checks

    def frozen(self, root, kind, where):
        if not isinstance(root, Var):
            return
        for sc in self.ctx.scopes:
            for r, mut, by in sc.freezes:
                if r is root and (kind != 'read' or mut):
                    verb = {'assign': 'assigned', 'move': 'moved', 'mut': 'mutably borrowed',
                            'read': 'used'}[kind]
                    self.err(f"'{root.name}' is borrowed by {by} and cannot be {verb} while that "
                             "borrow is in scope", where)

    def overlap(self, a, b):
        # '*' marks the result of a borrow-returning call: somewhere under the argument.
        for x, y in zip(a, b):
            if x == '*' or y == '*':
                return True
            if x != y:
                return False
        return True

    def check_pending(self, root, path, mut, where, move=False):
        """A move or assignment may not touch a place held by a call (or operator) in progress."""
        if root is None:
            return
        for r, p, m, c in self.ctx.pending:
            if r is root and self.overlap(p, path):
                name = getattr(root, 'name', 'a temporary')
                what = 'moved' if move else 'assigned'
                self.err(f"'{name}' cannot be {what} here: it overlaps a borrow held by the call "
                         "being evaluated", where)

    def hold(self, v, mut, cid, where):
        """Register a borrowed argument of call cid (SPEC 10 rule 3, and nested calls)."""
        root = v.root
        if root is None or isinstance(root, Tmp):
            return
        for r, p, m, c in self.ctx.pending:
            if r is root and self.overlap(p, v.path) and ((c is cid and (mut or m)) or (c is not cid and mut)):
                name = getattr(root, 'name', '?')
                if c is cid:
                    self.err(f"'{name}' is borrowed{' mutably' if mut or m else ''} twice in one call: "
                             "a mut & argument may not overlap another borrowed or moved argument", where)
                self.err(f"'{name}' cannot be mutably borrowed here: an enclosing call already holds a "
                         "borrow of it", where)
        self.ctx.pending.append((root, v.path, mut, cid))

    def consume(self, v, where):
        """Take ownership of the value of v: copy (dup) a copy type, move otherwise."""
        if v.t == VOID:
            self.err("this expression has no value", where)
        if v.cat == 'tmp':
            v.tmp.live = False
            return v.c
        if v.cat == 'rv':
            return v.c
        if is_copy(v.t):
            return self.dup(v.c, v.t)
        if v.var is not None and not v.viaref and v.path == () and v.var.t[0] != 'ref':
            var = v.var
            if var.state != 'live':
                self.err(f"'{var.name}' was moved and has no value here", where)
            if self.ctx.nomove:
                self.err(f"cannot move '{var.name}' {self.ctx.nomove[-1]}", where)
            self.frozen(var, 'move', where)
            self.check_pending(var, (), False, where, move=True)
            var.state = 'moved'
            return v.c
        name = getattr(v.root, 'name', None)
        if v.viaref:
            self.err(f"cannot move a {tstr(v.t)} out of a borrow{' (' + name + ')' if name else ''}: it is "
                     "not a copy type", where)
        if v.glob is not None:
            self.err(f"cannot move out of global {v.glob.name}", where)
        self.err(f"cannot move a {tstr(v.t)} out of a field or element"
                 f"{' of ' + repr(name) if name else ''}; use replace, swap or pop", where)

    def deref(self, v):
        if v.t[0] != 'ref':
            return v
        return Val(f"(*{v.c})", v.t[1], 'place', root=v.root, path=v.path, mutable=v.t[2], viaref=True)

    def borrow(self, v, mut, where, recv=False):
        """A borrow of the place, temporary or value v."""
        if v.t[0] == 'slice':           # &s and mut &s are the slice s (SPEC 4.2a)
            if mut and not v.t[2]:
                self.err("cannot make a mut slice from a shared slice", where)
            if mut:
                self.frozen(v.root, 'mut', where)
            return Val(v.c, ('slice', v.t[1], mut), 'rv', root=v.root, path=v.path)
        if v.t[0] == 'ref':
            if mut and not v.t[2]:
                self.err(f"cannot mutably borrow through the shared borrow "
                         f"'{getattr(v.root, 'name', '?')}'", where)
            if mut:
                self.frozen(v.root, 'mut', where)
            return Val(v.c, ('ref', v.t[1], mut), 'rv', root=v.root, path=v.path)
        if v.t == VOID:
            self.err("this expression has no value", where)
        if v.cat == 'place':
            if v.glob is not None:
                if recv and not mut:
                    v = self.holdtmp(self.dup(v.c, v.t), v.t)
                    return self.borrow(v, mut, where)
                self.err(f"cannot borrow global '{v.glob.name}'", where)
            if mut:
                if not v.mutable:
                    self.immutable(v, where, 'mutably borrow')
                self.frozen(v.root, 'mut', where)
            c = f"(&{v.c})"
        elif v.cat == 'tmp':
            c = f"(&{v.c})"
        else:
            n = self.ctx.fresh('m')
            self.emit(f"{self.T(v.t)} {n} = {v.c};")
            c = f"(&{n})"
        return Val(c, ('ref', v.t, mut), 'rv', root=v.root, path=v.path)

    def immutable(self, v, where, verb):
        r = v.root
        name = getattr(r, 'name', '?')
        if isinstance(r, Var):
            vb = verb[:-3] if verb.endswith(' to') else verb
            if r.t[0] in ('ref', 'slice') and not r.t[2]:
                self.err(f"cannot {vb} through '{name}': it is a shared borrow (&), not mut &", where)
            if v.viaref and r.t[0] not in ('ref', 'slice'):
                self.err(f"cannot {vb} through a shared borrow or slice of '{name}'", where)
            if r.kind == 'param':
                self.err(f"cannot {verb} parameter '{name}': declare it `var {name}`", where)
            self.err(f"cannot {verb} '{name}': it is declared with let, not var", where)
        self.err(f"cannot {verb} this place", where)

    def effects(self, e):
        """Whether evaluating e may run code that changes a value read before it in the statement:
        a call, try, an operator method or fromFraction. Conservative where types are unknown."""
        if not isinstance(e, N):
            return False
        k, lits = e.kind, ('int', 'char', 'str', 'bool')
        if k in ('call', 'try', 'frac') or (k == 'un' and e.op == '-' and e.e.kind not in lits):
            return True
        if k == 'bin' and (e.op in OPS or e.op in CMPS) and e.l.kind not in lits and e.r.kind not in lits:
            return True
        if k == 'name' and self.lookup_var(e.v) is None:
            d = self.prog.lookup(self.ctx.module, e.v)
            if isinstance(d, Const) and self.prog.const_value(d)[2] == 'frac':
                return True
        for v in e.__dict__.values():
            for x in (v if isinstance(v, list) else [v]):
                if any(isinstance(y, N) and self.effects(y) for y in (x if isinstance(x, tuple) else [x])):
                    return True
        return False

    def snap_val(self, v):
        """Read a place (or compute a value) now, before later side effects in the statement."""
        if v.cat == 'rv' and v.t != VOID and v.t[0] != 'ref' and not v.c.replace('_', 'a').isalnum():
            return self.holdtmp(v.c, v.t)
        if v.cat != 'place' or v.t[0] == 'ref' or not is_copy(v.t):
            return v
        return self.holdtmp(self.dup(v.c, v.t), v.t)

    # ---------------------------------------------------------- statements

    def stmt(self, s):
        getattr(self, 's_' + s.kind)(s)

    def s_block(self, s):
        self.block(s)

    def s_expr(self, s):
        v = self.expr(s.e, None)
        if v.noreturn:
            self.ctx.dead = True

    def coerce(self, v, t, where, what='value'):
        if v.t == t:
            return v
        if v.t[0] == 'ref' and t[0] != 'ref':
            d = self.deref(v)
            if d.t == t:
                return d
        if v.t[0] == 'ref' and t[0] == 'ref' and v.t[1] == t[1] and (v.t[2] or not t[2]):
            return Val(v.c, t, v.cat, root=v.root, path=v.path, var=v.var)
        if t[0] == 'slice':
            s = self.as_slice(v)
            if s is not None and s.t[1] == t[1] and (s.t[2] or not t[2]):
                return Val(s.c, t, s.cat, root=s.root, path=s.path)
        self.err(f"{what} is {tstr(v.t)}, not {tstr(t)}", where)

    def as_slice(self, v):
        """v as a slice: a slice, or a borrow of an array (&xs, &[]T) viewed whole; else None."""
        if v.t[0] == 'slice':
            return v
        if v.t[0] == 'ref' and v.t[1][0] == 'arr':
            return Val(f"rt_as_slice({v.c})", ('slice', v.t[1][1], v.t[2]), 'rv', root=v.root, path=v.path)
        return None

    def s_let(self, s):
        ctx = self.ctx
        exp = self.prog.resolve_type(s.ty, ctx.module, ctx.fd.env, 'let' if not s.mutable else 'var',
                                     s.where) if s.ty else None
        v = self.expr(s.e, exp)
        if v.t == VOID:
            self.err(f"the initializer of '{s.name}' has no value", s.where)
        cn = ctx.fresh(f"v_{s.name}_")
        if (exp is None and v.t[0] in ('ref', 'slice')) or (exp is not None and exp[0] in ('ref', 'slice')):
            if exp is not None:
                v = self.coerce(v, exp, s.where, f"the initializer of '{s.name}'")
            if s.mutable:
                self.err(f"a borrow cannot be stored in var '{s.name}': borrows are second class "
                         "(use let)", s.where)
            root = v.root
            if not isinstance(root, Var):
                self.err(f"cannot borrow a temporary into let '{s.name}': the temporary does not outlive "
                         "the statement", s.where)
            var = Var(s.name, cn, v.t, False, 'local', s.where)
            var.broot = root
            self.emit(f"{self.T(v.t[1])}* {cn} = {v.c};" if v.t[0] == 'ref' else f"obx_slice {cn} = {v.c};")
            self.declare(var)
            ctx.scopes[-1].freezes.append((root, v.t[2], f"'{s.name}'"))
            return
        if exp is not None:
            v = self.coerce(v, exp, s.where, f"the initializer of '{s.name}'")
        c = self.consume(v, s.where)
        self.emit(f"{self.T(v.t)} {cn} = {c};")
        self.declare(Var(s.name, cn, v.t, s.mutable, 'local', s.where))

    def place_type(self, e):
        """The type of a place expression, without evaluating it (None if unknown)."""
        k = e.kind
        if k == 'name':
            v = self.lookup_var(e.v)
            if v is not None:
                return v.t[1] if v.t[0] == 'ref' else v.t
            d = self.prog.lookup(self.ctx.module, e.v)
            return d.ty if isinstance(d, Global) else None
        if k == 'field':
            b = self.place_type(e.e)
            if b and b[0] == 'ref':
                b = b[1]
            if b and b[0] == 'inst' and b[1].form == 'record':
                for n, ft, _ in b[1].fields:
                    if n == e.name:
                        return subst(ft, decl_env(b))
            return None
        if k == 'index':
            b = self.place_type(e.e)
            if b and b[0] == 'ref':
                b = b[1]
            return b[1] if b and b[0] in ('arr', 'slice') else None
        return None

    def self_in_drop(self, v, where):
        fd = self.ctx.fd
        if isinstance(fd.owner, TypeDecl) and fd.owner.drop is fd and isinstance(v.root, Var) \
                and v.root.name == fd.recv_name and v.root.kind == 'param' and not v.path:
            self.err("a drop method may not assign or replace the whole of its receiver", where)

    def s_assign(self, s):
        ctx = self.ctx
        tg = s.target
        if tg.kind == 'name':
            var = self.lookup_var(tg.v)
            if var is None:
                d = self.prog.lookup(ctx.module, tg.v)
                if isinstance(d, Const):
                    self.err(f"cannot assign to constant {tg.v}", s.where)
                if not isinstance(d, Global):
                    self.err(f"unknown name '{tg.v}'", s.where)
            elif var.t[0] != 'ref':
                if not var.mutable:
                    if var.kind == 'param':
                        self.err(f"cannot assign to parameter '{var.name}': declare it `var {var.name}`",
                                 s.where)
                    self.err(f"cannot assign to '{var.name}': it is declared with let, not var", s.where)
                self.frozen(var, 'assign', s.where)
                if s.op is None:
                    v = self.coerce(self.expr(s.e, var.t), var.t, s.where, f"the value assigned to '{var.name}'")
                    c = self.consume(v, s.where)
                    self.store(Val(var.c, var.t, 'place', var=var), c, var.state == 'live')
                    var.state = 'live'
                    return
        if tg.kind not in ('name', 'field', 'index'):
            self.err("cannot assign to this expression", s.where)
        if s.op is None:
            # p = e evaluates e first: a value read now must not see the effects of the target
            v = self.expr(s.e, self.place_type(tg))
            mark = len(ctx.pending)
            if self.effects(tg):
                v = self.snap_val(v)
            if v.cat == 'place' and not is_copy(v.t):
                self.hold(v, None, object(), s.where)
            p = self.place(tg)
            del ctx.pending[mark:]
            v = self.coerce(v, p.t, s.where, "the assigned value")
            c = self.consume(v, s.where)
            self.store(p, c, True)
            return
        p = self.place(tg)
        if p.var is not None and p.var.state != 'live':
            self.err(f"'{p.var.name}' was moved and has no value here", s.where)
        lv = self.snap_val(self.rd(p)) if self.effects(s.e) else p
        r = self.binop(s.op, lv, s.e, s.where, p.t, held=p)
        c = self.consume(self.coerce(r, p.t, s.where, "the result"), s.where)
        live = not (p.var is not None and p.var.state != 'live')
        self.store(p, c, live)
        if p.var is not None:
            p.var.state = 'live'

    def place(self, e):
        """Evaluate an assignment target as a mutable place."""
        v = self.expr(e, None)
        if v.t[0] == 'ref':
            if not v.t[2]:
                self.err(f"cannot assign through '{getattr(v.root, 'name', '?')}': it is a shared borrow "
                         "(&), not mut &", e.where)
            v = self.deref(v)
        if v.cat != 'place':
            self.err("cannot assign to a value that is not a place", e.where)
        self.self_in_drop(v, e.where)
        if v.glob is None:
            if not v.mutable:
                self.immutable(v, e.where, 'assign to')
            self.frozen(v.root, 'assign', e.where)
            self.check_pending(v.root, v.path, True, e.where)
        return v

    def store(self, p, c, drop_old):
        if is_pod(p.t) or not drop_old:
            self.emit(f"{p.c} = {c};")
            return
        n = self.ctx.fresh('nw')
        self.emit(f"{{ {self.T(p.t)} {n} = {c};")
        self.ctx.ind += 1
        self.drop(p.c, p.t)
        self.emit(f"{p.c} = {n}; }}")
        self.ctx.ind -= 1

    def cond(self, e):
        ctx = self.ctx
        self.push_frame()
        v = self.expr(e, BOOL)
        if v.t[0] == 'ref':
            v = self.deref(v)
        if v.t != BOOL:
            self.err(f"a condition must be bool, not {tstr(v.t)}", e.where)
        cn = ctx.fresh('c')
        self.emit(f"bool {cn} = {v.c};")
        self.drop_frame(self.pop_frame())
        return cn

    def s_if(self, s):
        ctx = self.ctx
        c = self.cond(s.cond)
        snap = self.snapshot()
        self.emit(f"if ({c}) {{")
        ctx.ind += 1
        self.block(s.then)
        ctx.ind -= 1
        r1 = (self.snapshot(), ctx.dead)
        self.restore(snap)
        ctx.dead = False
        if s.els:
            self.emit("} else {")
            ctx.ind += 1
            self.block(s.els)
            ctx.ind -= 1
        self.emit("}")
        self.join([r1, (self.snapshot(), ctx.dead)], s.where)

    def loop(self, body, where, infinite, setup=None, entry=None):
        ctx = self.ctx
        lp = Loop(len(ctx.scopes), len(ctx.frames), entry or self.snapshot(), infinite)
        ctx.loops.append(lp)
        ctx.scopes.append(Scope())
        if setup:
            setup(ctx.scopes[-1])
        self.block(body, scope=False)
        sc = ctx.scopes.pop()
        if not ctx.dead:
            self.drop_scope(sc)
            self.loop_state(lp, where)
        ctx.loops.pop()
        self.restore(lp.entry)
        if infinite:
            self.join([(b, False) for b in lp.breaks] or [({}, True)], where)
        else:
            for b in lp.breaks:
                for v, st in lp.entry.items():
                    if b.get(v) != st:
                        self.err(f"'{v.name}' is moved inside the loop", where)
            ctx.dead = False

    def loop_state(self, lp, where):
        for v, st in lp.entry.items():
            if v.state != st:
                self.err(f"'{v.name}' is moved inside the loop and not assigned again before the next "
                         "iteration", where)

    def s_while(self, s):
        ctx = self.ctx
        infinite = s.cond.kind == 'bool' and s.cond.v
        self.emit("while (1) {")
        ctx.ind += 1
        entry = self.snapshot()
        if not infinite:
            ctx.nomove.append('in a while condition (it runs on every iteration)')
            c = self.cond(s.cond)
            ctx.nomove.pop()
            self.emit(f"if (!{c}) break;")
        self.loop(s.body, s.where, infinite, entry=entry)
        ctx.ind -= 1
        self.emit("}")

    def s_break(self, s):
        lp = self.cur_loop(s, 'break')
        self.cleanup(lp.nscopes, lp.nframes)
        lp.breaks.append({v: st for v, st in self.snapshot().items() if v in lp.entry})
        self.emit("break;")
        self.ctx.dead = True

    def s_continue(self, s):
        lp = self.cur_loop(s, 'continue')
        self.cleanup(lp.nscopes, lp.nframes)
        self.loop_state(lp, s.where)
        self.emit("continue;")
        self.ctx.dead = True

    def cur_loop(self, s, what):
        if not self.ctx.loops:
            self.err(f"{what} outside a loop", s.where)
        return self.ctx.loops[-1]

    def s_for(self, s):
        ctx = self.ctx
        if s.b is not None:
            a = self.rd(self.expr(s.a, INT))
            if self.effects(s.b):
                a = self.snap_val(a)
            b = self.rd(self.expr(s.b, INT))
            if a.t != INT or b.t != INT:
                self.err(f"a for range needs int bounds, not {tstr(a.t)} and {tstr(b.t)}", s.where)
            end = ctx.fresh('end')
            self.emit(f"int64_t {end} = {b.c};")
            iv = Var(s.name, ctx.fresh(f"v_{s.name}_"), INT, False, 'local', s.where)
            self.emit(f"for (int64_t {iv.c} = {a.c}; {iv.c} < {end}; {iv.c}++) {{")
            ctx.ind += 1
            self.loop(s.body, s.where, False, lambda sc: self.declare(iv))
            ctx.ind -= 1
            self.emit("}")
            return
        v = self.expr(s.a, None)
        if v.t[0] == 'ref':
            v = self.deref(v)
        if v.t[0] not in ('arr', 'slice'):
            self.err(f"for ... in needs an array, a slice or a range a..b, not {tstr(v.t)}", s.where)
        et = v.t[1]
        ap, i = ctx.fresh('ap'), ctx.fresh('i')
        if v.cat == 'rv' and v.t[0] == 'arr':
            v = self.holdtmp(v.c, v.t)
        if v.cat == 'place' and v.glob is not None:
            self.err("cannot iterate over a global", s.where)
        self.emit(f"obx_slice {ap} = {v.c if v.t[0] == 'slice' else f'rt_as_slice(&{v.c})'};")
        self.emit(f"for (int64_t {i} = 0; {i} < {ap}.len; {i}++) {{")
        ctx.ind += 1
        elem = f"(({self.T(et)}*){ap}.data)[{i}]"
        xn = ctx.fresh(f"v_{s.name}_")

        def setup(sc):
            if isinstance(v.root, Var):
                sc.freezes.append((v.root, False, 'the for loop'))
            if is_copy(et):
                self.emit(f"{self.T(et)} {xn} = {self.dup(elem, et)};")
                self.declare(Var(s.name, xn, et, False, 'local', s.where))
            else:
                self.emit(f"{self.T(et)}* {xn} = &{elem};")
                x = Var(s.name, xn, ('ref', et, False), False, 'local', s.where)
                x.broot = v.root
                self.declare(x)
        self.loop(s.body, s.where, False, setup)
        ctx.ind -= 1
        self.emit("}")

    def s_return(self, s):
        ctx = self.ctx
        fd = ctx.fd
        ret = fd.ret
        if s.e is None:
            if ret != VOID:
                self.err(f"return needs a {tstr(ret)} value in {fd.label}", s.where)
            self.cleanup()
            self.emit("return;")
        else:
            if ret == VOID:
                self.err(f"{fd.label} returns nothing, but this return has a value", s.where)
            v = self.expr(s.e, ret)
            if ret[0] in ('ref', 'slice'):
                if v.t[0] not in ('ref', 'slice'):
                    self.err(f"{fd.label} returns a borrow: write return &place", s.where)
                v = self.coerce(v, ret, s.where, "the returned borrow")
                r = v.root
                while isinstance(r, Var) and r.broot is not None:
                    r = r.broot
                if r is not getattr(fd, 'bvar', None):
                    name = getattr(v.root, 'name', 'a temporary')
                    self.err(f"cannot return a borrow of {name!r}: a borrow result must be rooted at the "
                             f"borrowed parameter '{fd.bparam}', not a local or temporary", s.where)
                c = v.c
            else:
                v = self.coerce(v, ret, s.where, "the returned value")
                c = self.consume(v, s.where)
            rn = ctx.fresh('r')
            self.emit(f"{self.T(ret)} {rn} = {c};")
            self.cleanup()
            self.emit(f"return {rn};")
        ctx.dead = True

    def s_match(self, s):
        ctx = self.ctx
        v = self.expr(s.e, None)
        base = v.t[1] if v.t[0] == 'ref' else v.t
        if base == VOID:
            self.err("match needs a value", s.where)
        if base[0] == 'prim' and base[1] in INTS + ('str',):
            return self.match_lits(s, self.rd(v), base)
        if base[0] != 'inst' or base[1].form != 'closed':
            self.err(f"match needs a closed type, an integer, a byte or a str, not {tstr(base)}", s.where)
        d = base[1]
        env = decl_env(base)
        borrowing = v.t[0] == 'ref' or v.cat == 'place'
        sep = '.' if d.value else '->'
        if borrowing:
            p = self.deref(v)
            if p.glob is not None:
                p = self.holdtmp(self.dup(p.c, p.t), p.t)
            sp = ctx.fresh('sp')
            self.emit(f"{self.T(base)}* {sp} = &{p.c};")
            acc = f"(*{sp})"
            root = p.root
        else:
            if d.drop is not None:
                self.err(f"cannot destructure a {d.name}: it has a drop method (match on a place instead)",
                         s.where)
            acc = ctx.fresh('s')
            self.emit(f"{self.T(base)} {acc} = {self.consume(v, s.where)};")
        snap = self.snapshot()
        seen, results, first = set(), [], True
        has_else = False
        for arm in s.arms:
            if arm.lits is not None:
                self.err("literal arms need an integer, byte or str scrutinee", arm.where)
            if arm.variant is None:
                has_else = True
                self.emit("} else {" if not first else "{")
            else:
                vr = next((x for x in d.variants if x.name == arm.variant), None)
                if vr is None:
                    self.err(f"{arm.variant} is not a variant of {tstr(base)}", arm.where)
                if vr.index in seen:
                    self.err(f"{arm.variant} is matched twice", arm.where)
                seen.add(vr.index)
                self.emit(("if" if first else "} else if") + f" ({acc}{sep}tag == {vr.index}) {{")
            first = False
            ctx.ind += 1
            self.restore(snap)
            ctx.dead = False
            sc = Scope()
            ctx.scopes.append(sc)
            if arm.variant is None:
                if not borrowing:
                    self.drop(acc, base)
            else:
                fs = vr.fields
                if arm.binds is not None and len(arm.binds) != len(fs):
                    self.err(f"{vr.name} has {len(fs)} fields, but the pattern binds {len(arm.binds)}",
                             arm.where)
                for i, (fn, fa, _) in enumerate(fs):
                    ft = subst(fa, env)
                    fc = f"{acc}{sep}u.V_{vr.name}.f_{fn}"
                    bn = arm.binds[i] if arm.binds else None
                    bn = None if bn == '_' else bn
                    cn = ctx.fresh(f"v_{bn}_")
                    if not borrowing:
                        if bn:
                            self.emit(f"{self.T(ft)} {cn} = {fc};")
                            self.declare(Var(bn, cn, ft, False, 'local', arm.where))
                        else:
                            self.drop(fc, ft)
                    elif bn:
                        if is_copy(ft):
                            self.emit(f"{self.T(ft)} {cn} = {self.dup(fc, ft)};")
                            self.declare(Var(bn, cn, ft, False, 'local', arm.where))
                        else:
                            self.emit(f"{self.T(ft)}* {cn} = &{fc};")
                            x = Var(bn, cn, ('ref', ft, False), False, 'local', arm.where)
                            x.broot = root if isinstance(root, Var) else None
                            self.declare(x)
                            if isinstance(root, Var) and not sc.freezes:
                                sc.freezes.append((root, False, f"the match binding '{bn}'"))
                if not borrowing and not d.value:
                    self.emit(f"rt_free({acc});")
            self.block(arm.body)
            ctx.scopes.pop()
            if not ctx.dead:
                self.drop_scope(sc)
            ctx.ind -= 1
            results.append((self.snapshot(), ctx.dead))
        if not has_else and len(seen) != len(d.variants):
            missing = [x.name for x in d.variants if x.index not in seen]
            self.err(f"match on {tstr(base)} is not exhaustive: missing {', '.join(missing)}", s.where)
        if s.arms:
            self.emit("}")
        self.join(results, s.where)

    def match_lits(self, s, v, t):
        ctx = self.ctx
        sv = ctx.fresh('sv')
        self.emit(f"{self.T(t)} {sv} = {v.c};")
        snap = self.snapshot()
        results, first, has_else = [], True, False
        for arm in s.arms:
            if arm.lits is None and arm.variant is not None:
                self.err(f"a match on {tstr(t)} takes literal arms, not {arm.variant}", arm.where)
            if arm.lits is None:
                has_else = True
                self.emit("} else {" if not first else "{")
            else:
                conds = []
                for k, x in arm.lits:
                    if t == STR:
                        if k != 'str':
                            self.err("a match on str takes string literals", arm.where)
                        conds.append(f"rt_str_eq({sv}, {self.H('str', x)})")
                    else:
                        if k == 'str':
                            self.err(f"a match on {tstr(t)} takes integer literals", arm.where)
                        lo, hi = RANGES[t[1]]
                        if not lo <= x <= hi:
                            self.err(f"literal {x} does not fit {tstr(t)}", arm.where)
                        conds.append(f"{sv} == {self.cint(x, t)}")
                self.emit(("if" if first else "} else if") + f" ({' || '.join(conds)}) {{")
            first = False
            ctx.ind += 1
            self.restore(snap)
            ctx.dead = False
            self.block(arm.body)
            ctx.ind -= 1
            results.append((self.snapshot(), ctx.dead))
        if not has_else:
            self.err(f"a match on {tstr(t)} needs an else arm (it is not exhaustive)", s.where)
        self.emit("}")
        self.join(results, s.where)

    # ---------------------------------------------------------- expressions

    def expr(self, e, exp):
        return getattr(self, 'x_' + e.kind)(e, exp)

    def rd(self, v):
        """Read a value: a borrow of a copy type reads what it points to."""
        if v.t[0] == 'ref':
            return self.deref(v)
        return v

    def cint(self, v, t):
        n = t[1]
        if n in ('i128', 'u128'):
            ct = '__int128' if n == 'i128' else 'unsigned __int128'
            u = v & (2**128 - 1)
            hi, lo = u >> 64, u & (2**64 - 1)
            return f"(({ct})(((unsigned __int128){hi}ULL << 64) | {lo}ULL))"
        if n == 'int':
            return f"{v}LL" if v != -2**63 else "(-9223372036854775807LL-1)"
        if n == 'u64':
            return f"{v}ULL"
        if n == 'float':
            return repr(float(v))
        return f"(({CTYPES[n]}){v})"

    def x_int(self, e, exp):
        t = exp if exp is not None and exp[0] == 'prim' and (exp[1] in INTS or exp[1] == 'float') else INT
        if t == FLOAT:
            return self.fconst(e.v, e.where)
        lo, hi = RANGES[t[1]]
        if not lo <= e.v <= hi:
            self.err(f"integer literal {e.v} does not fit {t[1]} (out of range)", e.where)
        return Val(self.cint(e.v, t), t, 'rv')

    def fconst(self, x, where):
        return Val(repr(self.prog.cfit((FLOAT, x, 'float'), where)[1]), FLOAT, 'rv')

    def x_char(self, e, exp):
        return Val(f"((uint8_t){e.v})", BYTE, 'rv')

    def x_str(self, e, exp):
        return Val(self.H('str', e.v), STR, 'rv')

    def x_bool(self, e, exp):
        return Val('true' if e.v else 'false', BOOL, 'rv')

    def x_type(self, e, exp):
        self.err("a type is not a value", e.where)

    def x_frac(self, e, exp):
        neg = e.v.startswith('-')
        whole, frac = e.v.lstrip('-').split('.')
        if exp == FLOAT:
            return self.fconst(e.v, e.where)
        digits = int(whole + frac) * (-1 if neg else 1)
        lo, hi = RANGES['int']
        if not lo <= digits <= hi:
            self.err(f"fractional literal {e.v} has too many digits", e.where)
        args = [Val(f"{digits}LL", INT, 'rv'), Val(f"{len(frac)}LL", INT, 'rv')]
        if exp is not None:
            sig = self.assoc_sig(exp, 'fromFraction', e.where, quiet=True)
            if sig is not None and [subst(p, sig.bind) for p in sig.params] == [INT, INT]:
                return self.call(sig, None, args, e.where, exp)
        d = self.prog.lookup(self.ctx.module, 'fromFraction')
        if not isinstance(d, FnDecl):
            self.err("a fractional literal needs a function fromFraction(int, int) in scope", e.where)
        return self.call(fn_sig(d), None, args, e.where, exp)

    def static_ns(self, e):
        """Like static, but only a module, a type or a function: something that is not a value."""
        st = self.static(e)
        return None if st and st[0] == 'decl' and isinstance(st[1], (Global, Const)) else st

    def static(self, e):
        """Interpret e as a module, type or top-level declaration, if it is one."""
        ctx = self.ctx
        if e.kind == 'name':
            if self.lookup_var(e.v) is not None:
                return None
            if e.v in ctx.fd.env:
                return ('type', ctx.fd.env[e.v])
            if e.v in PRIMS:
                return ('type', ('prim', e.v))
            d = self.prog.lookup(ctx.module, e.v)
            if d is not None:
                return ('decl', d)
            if e.v in ctx.module.imports:
                return ('module', ctx.module.imports[e.v])
            return None
        if e.kind == 'field':
            b = self.static(e.e)
            if b and b[0] == 'module':
                return ('decl', self.prog.lookup_in(ctx.module, b[1], e.name, e.where))
            return None
        if e.kind == 'index':
            b = self.static(e.e)
            if b and b[0] == 'decl' and isinstance(b[1], (TypeDecl, FnDecl, Variant)):
                return ('decl', b[1], [self.texpr(x) for x in e.items])
            return None
        return None

    def texpr(self, x):
        k = x.kind
        if k == 'type':
            return x.ty
        if k == 'name':
            return ('name', [x.v], [], x.where)
        if k == 'field' and x.e.kind == 'name':
            return ('name', [x.e.v, x.name], [], x.where)
        if k == 'index':
            b = self.texpr(x.e)
            if b[0] == 'name' and not b[2]:
                return ('name', b[1], [self.texpr(i) for i in x.items], x.where)
        if k == 'borrow':
            return ('ref', self.texpr(x.e), x.mut)
        self.err("expected a type", x.where)

    def static_type(self, st, where):
        """A type from a static ('decl', TypeDecl[, targs]) or ('type', t)."""
        if st[0] == 'type':
            return st[1]
        d = st[1]
        if not isinstance(d, TypeDecl):
            return None
        if len(st) == 2 and d.tparams and d.form != 'alias':
            return ('inst', d, None)
        a = ('name', [d.name], st[2] if len(st) > 2 else [], where)
        return self.resolve_named(d, a, where)

    def resolve_named(self, d, a, where):
        mod = self.ctx.module
        if d.module is not mod and self.prog.lookup(mod, d.name) is not d:
            # a qualified name: resolve it in its own module, then the args here
            args = tuple(self.prog.resolve_type(x, mod, self.ctx.fd.env, 'targ', where) for x in a[2])
            if len(args) != len(d.tparams):
                self.err(f"{d.name} takes {len(d.tparams)} type arguments, not {len(args)}", where)
            if d.form == 'alias':
                return subst(self.prog.alias_target(d), dict(zip(d.tparams, args)))
            self.prog.check_targs(args, d.tparams, mod, where)
            return ('inst', d, args)
        return self.prog.resolve_type(a, mod, self.ctx.fd.env, 'targ', where)

    def x_name(self, e, exp):
        var = self.lookup_var(e.v)
        if var is not None:
            if var.state != 'live':
                self.err(f"'{var.name}' was moved and has no value here", e.where)
            self.frozen(var, 'read', e.where)
            mutable = var.mutable
            return Val(var.c, var.t, 'place', root=var, mutable=mutable, var=var)
        if e.v in BUILTINS or e.v in INTRINSICS:
            self.err(f"{e.v} is a built-in function; call it", e.where)
        st = self.static(e)
        if st is None:
            self.err(f"unknown name '{e.v}'", e.where)
        return self.static_value(st, e, exp)

    def static_value(self, st, e, exp):
        if st[0] == 'module':
            self.err(f"{st[1].name} is a module, not a value", e.where)
        if st[0] == 'type':
            self.err(f"{tstr(st[1])} is a type, not a value", e.where)
        d = st[1]
        targs = st[2] if len(st) > 2 else None
        if isinstance(d, Const):
            return self.const_val(d, exp, e.where)
        if isinstance(d, Global):
            return Val(d.cname, d.ty, 'place', root=None, mutable=True, glob=d)
        if isinstance(d, Variant):
            if d.fields:
                self.err(f"variant {d.name} has fields: write {d.name}(...)", e.where)
            return self.construct_variant(d, [], e.where, exp, targs)
        if isinstance(d, FnDecl):
            if d.extern is not None:
                self.err(f"extern function {d.name} cannot be used as a value", e.where)
            bind = {}
            if targs is not None:
                if len(targs) != len(d.tps):
                    self.err(f"{d.name} takes {len(d.tps)} type arguments, not {len(targs)}", e.where)
                for tp, a in zip(d.tps, targs):
                    bind[tp] = self.prog.resolve_type(a, self.ctx.module, self.ctx.fd.env, 'targ', e.where)
            ft = ('fn', tuple(p[1] for p in d.params), d.ret)
            if d.tps and len(bind) < len(d.tps) and exp is not None and exp[0] == 'fn':
                unify(ft, exp, bind, set(d.tps))
            if len(bind) < len(d.tps):
                self.err(f"cannot infer the type arguments of {d.name}; write {d.name}[...]", e.where)
            targs = tuple(bind[tp] for tp in d.tps)
            self.prog.check_targs(targs, d.tps, self.ctx.module, e.where)
            return Val(self.H('fn', d, targs), subst(ft, bind), 'rv')
        self.err(f"{d.name} is a type, not a value", e.where)

    def const_val(self, c, exp, where):
        ty, x, kind = self.prog.const_value(c)
        if kind == 'typed':
            if ty[0] == 'prim' and ty[1] in INTS:
                return Val(self.cint(x, ty), ty, 'rv')
            if ty == FLOAT:
                return Val(repr(float(x)), FLOAT, 'rv')
            kind = ty[1]
        if kind == 'int':
            if ty == BYTE:
                return Val(f"((uint8_t){x})", BYTE, 'rv')
            return self.x_int(N('int', where, v=x), exp)
        if kind == 'frac':
            return self.x_frac(N('frac', where, v=x), exp)
        if kind == 'float':
            return Val(repr(float(x)), FLOAT, 'rv')
        if kind == 'bool':
            return Val('true' if x else 'false', BOOL, 'rv')
        return Val(self.H('str', x), STR, 'rv')

    def x_field(self, e, exp):
        st = self.static_ns(e.e)
        if st is not None:
            if st[0] == 'module':
                d = self.prog.lookup_in(self.ctx.module, st[1], e.name, e.where)
                return self.static_value(('decl', d), e, exp)
            self.err(f"{e.name} on a type is an associated function: call it", e.where)
        v = self.expr(e.e, None)
        return self.field_of(v, e.name, e.where)

    def field_of(self, v, name, where):
        v = self.deref(v)
        t = v.t
        if t[0] != 'inst' or t[1].form != 'record':
            if t[0] == 'inst' and t[1].form == 'closed':
                self.err(f"{tstr(t)} is a closed type: use match to reach its fields", where)
            self.err(f"{tstr(t)} has no field '{name}'", where)
        d = t[1]
        f = next((f for f in d.fields if f[0] == name), None)
        if f is None:
            self.err(f"{tstr(t)} has no field '{name}'", where)
        if not f[2] and d.module is not self.ctx.module:
            self.err(f"field '{name}' of {d.name} is private to module {d.module.name}", where)
        ft = subst(f[1], decl_env(t))
        sep = '.' if d.value else '->'
        return Val(f"{v.c}{sep}f_{name}", ft, 'place' if v.cat != 'rv' else 'rv', root=v.root,
                   path=v.path + (name,), mutable=v.mutable, viaref=v.viaref, glob=v.glob)

    def x_index(self, e, exp):
        st = self.static_ns(e.e)
        if st is not None:
            if st[0] == 'decl' and len(st) == 2 and isinstance(st[1], (FnDecl, Variant)):
                st = ('decl', st[1], [self.texpr(x) for x in e.items])
            return self.static_value(st, e, exp)
        if len(e.items) != 1:
            self.err("an index takes one expression", e.where)
        v = self.deref(self.expr(e.e, None))
        if v.t == STR:
            if self.effects(e.items[0]):
                v = self.snap_val(v)
            i = self.int_index(e.items[0])
            return Val(f"rt_str_byte({v.c}, {i}, {self.W(e.where)})", BYTE, 'rv')
        if v.t[0] not in ('arr', 'slice'):
            self.err(f"cannot index a {tstr(v.t)}", e.where)
        if v.cat == 'rv':
            v = self.holdtmp(v.c, v.t)
        mark = len(self.ctx.pending)
        self.hold(v, False, object(), e.where)
        i = self.int_index(e.items[0])
        del self.ctx.pending[mark:]
        et = v.t[1]
        c = f"(({self.T(et)}*)({v.c}).data)[rt_idx({i}, ({v.c}).len, {self.W(e.where)})]"
        if v.t[0] == 'slice':
            return Val(c, et, 'place', root=v.root, path=v.path + ('[]',), mutable=v.t[2], viaref=True)
        return Val(c, et, 'place', root=v.root, path=v.path + ('[]',), mutable=v.mutable,
                   viaref=v.viaref, glob=v.glob)

    def x_sub(self, e, exp):
        """xs[a..b], xs[a..], xs[..b] of an array or slice (a slice, SPEC 4.2a) or of a str (a
        substring sharing its bytes)."""
        v = self.deref(self.expr(e.e, None))
        W = self.W(e.where)
        mut = exp is not None and exp[0] == 'slice' and exp[2]
        if v.t == STR:
            if self.effects(e.lo) or self.effects(e.hi):
                v = self.snap_val(v)
            lo = self.int_index(e.lo) if e.lo is not None else '0'
            hi = self.int_index(e.hi) if e.hi is not None else f"({v.c}).len"
            return self.holdtmp(f"rt_str_sub({v.c}, {lo}, {hi}, {W})", STR)
        if v.t[0] not in ('arr', 'slice'):
            self.err(f"cannot slice a {tstr(v.t)}", e.where)
        if v.t[0] == 'arr':
            if v.cat == 'rv':
                v = self.holdtmp(v.c, v.t)
            if v.glob is not None:
                self.err(f"cannot slice global '{v.glob.name}'", e.where)
            if mut and not v.mutable:
                self.immutable(v, e.where, 'take a mut slice of')
            if mut:
                self.frozen(v.root, 'mut', e.where)
            base = f"rt_as_slice(&{v.c})"
        else:
            if mut and not v.t[2]:
                self.err("cannot make a mut slice from a shared slice", e.where)
            base = v.c
        mark = len(self.ctx.pending)
        self.hold(v, False, object(), e.where)
        lo = self.int_index(e.lo) if e.lo is not None else '0'
        hi = self.int_index(e.hi) if e.hi is not None else '-1'
        del self.ctx.pending[mark:]
        n = self.ctx.fresh('sv')
        self.emit(f"obx_slice {n} = rt_sub({base}, {lo}, {hi}, sizeof({self.T(v.t[1])}), {W});")
        return Val(n, ('slice', v.t[1], mut), 'rv', root=v.root, path=v.path)

    def int_index(self, ie):
        i = self.rd(self.expr(ie, INT))
        if i.t != INT:
            self.err(f"an index must be an int, not {tstr(i.t)}", ie.where)
        if ie.kind == 'int':
            return i.c
        n = self.ctx.fresh('ix')
        self.emit(f"int64_t {n} = {i.c};")
        return n

    def x_borrow(self, e, exp):
        if e.e.kind == 'sub':                # &xs[a..b], mut &xs[a..b]: the slice itself
            v = self.x_sub(e.e, ('slice', None, e.mut))
            return v if v.t[0] == 'slice' else self.borrow(v, e.mut, e.where)
        inner_exp = exp[1] if exp is not None and exp[0] == 'ref' else None
        if exp is not None and exp[0] == 'slice' and exp[1] is not None:
            inner_exp = ('arr', exp[1])
        v = self.expr(e.e, inner_exp)
        return self.borrow(v, e.mut, e.where)

    def x_un(self, e, exp):
        v = self.rd(self.expr(e.e, exp))
        t = v.t
        if e.op == '!':
            if t != BOOL:
                self.err(f"! needs a bool, not {tstr(t)}", e.where)
            return Val(f"(!{v.c})", BOOL, 'rv')
        if e.op == '~':
            if t[0] != 'prim' or t[1] not in INTS:
                self.err(f"~ needs an integer, not {tstr(t)}", e.where)
            return Val(f"(({CTYPES[t[1]]})~{v.c})", t, 'rv')
        if t[0] == 'prim':
            if t[1] in INTS:
                return Val(f"rt_neg_{t[1]}({v.c}, {self.W(e.where)})", t, 'rv')
            if t == FLOAT:
                return Val(f"(-{v.c})", t, 'rv')
            self.err(f"unary - is not defined for {tstr(t)}", e.where)
        return self.op_call(v, 'neg', [], e.where, '-')

    def x_bin(self, e, exp):
        if e.op in ('&&', '||'):
            return self.logic(e)
        if is_lit(e.l) and not is_lit(e.r):
            r = self.rd(self.expr(e.r, exp if e.op not in CMPS else None))
            lt = r.t if e.op not in ('<<', '>>') else exp
            lv = self.rd(self.expr(e.l, lt))
            return self.binop(e.op, lv, r, e.where, exp)
        hint = exp if e.op not in CMPS and exp is not None and exp[0] == 'prim' else None
        lv = self.rd(self.expr(e.l, hint))
        if self.effects(e.r):
            lv = self.snap_val(lv)
        return self.binop(e.op, lv, e.r, e.where, exp)

    def binop(self, op, lv, r, where, exp, held=None):
        """lv: the evaluated left operand; r: the right operand (an AST node or a Val)."""
        lv = self.rd(lv)
        lt = lv.t
        if isinstance(r, Val):
            rv = r
        else:
            rexp = INT if op in ('<<', '>>') else lt
            mark = len(self.ctx.pending)
            if (held or lv).cat == 'place':
                self.hold(held or lv, False, object(), where)
            rv = self.expr(r, rexp)
            del self.ctx.pending[mark:]
        rv = self.rd(rv)
        rt = rv.t
        W = self.W(where)
        if lt[0] == 'prim' and rt[0] == 'prim':
            n = lt[1]
            if op in ('<<', '>>'):
                if n not in INTS or rt != INT:
                    self.err(f"{op} needs an integer and an int shift count, not {tstr(lt)} and {tstr(rt)}",
                             where)
                return Val(f"rt_{'shl' if op == '<<' else 'shr'}_{n}({lv.c}, {rv.c}, {W})", lt, 'rv')
            if lt != rt:
                self.err(f"operator {op} needs operands of the same type, not {tstr(lt)} and {tstr(rt)}",
                         where)
            if n in INTS:
                if op in CMPS:
                    return Val(f"({lv.c} {op} {rv.c})", BOOL, 'rv')
                f = {'+': 'add', '-': 'sub', '*': 'mul', '/': 'div', '%': 'rem'}.get(op)
                if f:
                    return Val(f"rt_{f}_{n}({lv.c}, {rv.c}, {W})", lt, 'rv')
                if op in ('+%', '-%', '*%'):
                    return Val(f"rt_w{ {'+%': 'add', '-%': 'sub', '*%': 'mul'}[op]}_{n}({lv.c}, {rv.c})",
                               lt, 'rv')
                if op in ('&', '|', '^'):
                    return Val(f"(({CTYPES[n]})({lv.c} {op} {rv.c}))", lt, 'rv')
            elif n == 'float':
                if op in CMPS:
                    return Val(f"({lv.c} {op} {rv.c})", BOOL, 'rv')
                if op in ('+', '-', '*', '/'):
                    return Val(f"({lv.c} {op} {rv.c})", lt, 'rv')
            elif n == 'bool' and op in ('==', '!='):
                return Val(f"({lv.c} {op} {rv.c})", BOOL, 'rv')
            elif n == 'ptr' and op in ('==', '!='):
                return Val(f"({lv.c} {op} {rv.c})", BOOL, 'rv')
            elif n == 'str':
                if op == '+':
                    return self.holdtmp(f"rt_str_concat({lv.c}, {rv.c})", STR)
                if op in ('==', '!='):
                    return Val(f"({'' if op == '==' else '!'}rt_str_eq({lv.c}, {rv.c}))", BOOL, 'rv')
                if op in CMPS:
                    return Val(f"(rt_str_cmp({lv.c}, {rv.c}) {op} 0)", BOOL, 'rv')
            self.err(f"operator {op} is not defined for {tstr(lt)}", where)
        if lt[0] == 'prim':
            self.err(f"operator {op} needs operands of the same type, not {tstr(lt)} and {tstr(rt)}", where)
        if op in OPS:
            return self.op_call(lv, OPS[op], [rv], where, op)
        if op in ('==', '!='):
            r = self.op_call(lv, 'eq', [rv], where, op)
            return Val(r.c if op == '==' else f"(!{r.c})", BOOL, 'rv')
        if op in CMPS:
            r = self.op_call(lv, 'cmp', [rv], where, op)
            return Val(f"({r.c} {op} 0)", BOOL, 'rv')
        self.err(f"operator {op} is not defined for {tstr(lt)}", where)

    def op_call(self, recv, name, args, where, op):
        sig = self.method_sig(recv.t, name, where, quiet=True)
        if sig is None:
            self.err(f"operator {op} on {tstr(recv.t)} needs a method {name}"
                     f"{' (from a constraint such as Ord)' if name == 'cmp' else ''}", where)
        # The operand is passed as the method declares: borrowed for a & parameter.
        conv = []
        for a, p in zip(args, sig.params[1:] if sig.recv else sig.params):
            conv.append(self.borrow(a, False, where) if p[0] == 'ref' and a.t[0] != 'ref' else a)
        return self.call(sig, recv, conv, where, None, op=True)

    def logic(self, e):
        ctx = self.ctx
        ctx.nomove.append('inside && or ||')
        self.push_frame()
        lv = self.rd(self.expr(e.l, BOOL))
        if lv.t != BOOL:
            self.err(f"{e.op} needs bool operands, not {tstr(lv.t)}", e.where)
        ln = ctx.fresh('b')
        self.emit(f"bool {ln} = {lv.c};")
        self.drop_frame(self.pop_frame())
        self.emit(f"if ({'' if e.op == '&&' else '!'}{ln}) {{")
        ctx.ind += 1
        self.push_frame()
        rv = self.rd(self.expr(e.r, BOOL))
        if rv.t != BOOL:
            self.err(f"{e.op} needs bool operands, not {tstr(rv.t)}", e.where)
        self.emit(f"{ln} = {rv.c};")
        self.drop_frame(self.pop_frame())
        ctx.ind -= 1
        self.emit("}")
        ctx.nomove.pop()
        return Val(ln, BOOL, 'rv')

    def x_try(self, e, exp):
        ctx = self.ctx
        v = self.expr(e.e, None)
        t = v.t
        if v.t[0] == 'ref':
            self.err("try needs an owned value, not a borrow", e.where)
        a = self.okerr(t)
        if a is None:
            self.err(f"try needs a closed type with variants Ok and Err (like Result), not {tstr(t)}",
                     e.where)
        ret = ctx.fd.ret
        r = self.okerr(ret)
        if r is None:
            self.err(f"try needs a function whose result type has an Err variant (like Result), and "
                     f"{ctx.fd.label} returns {tstr(ret)}", e.where)
        if a[3] != r[3]:
            self.err(f"try: the error type {tstr(a[3])} is not the Err type {tstr(r[3])} of "
                     f"{ctx.fd.label}", e.where)
        d = t[1]
        if d.drop is not None:
            self.err(f"try cannot take apart a {d.name}: it has a drop method", e.where)
        sep = '.' if d.value else '->'
        sn = ctx.fresh('tr')
        self.emit(f"{self.T(t)} {sn} = {self.consume(v, e.where)};")
        self.emit(f"if ({sn}{sep}tag == {a[2].index}) {{")
        ctx.ind += 1
        en = ctx.fresh('e')
        self.emit(f"{self.T(a[3])} {en} = {sn}{sep}u.V_Err.f_{a[2].fields[0][0]};")
        if not d.value:
            self.emit(f"rt_free({sn});")
        rv = self.build(ret, r[2], [en])
        rn = ctx.fresh('r')
        self.emit(f"{self.T(ret)} {rn} = {rv};")
        self.cleanup()
        self.emit(f"return {rn};")
        ctx.ind -= 1
        self.emit("}")
        oc = f"{sn}{sep}u.V_Ok.f_{a[0].fields[0][0]}"
        if not d.value:
            on = ctx.fresh('ok')
            self.emit(f"{self.T(a[1])} {on} = {oc};")
            self.emit(f"rt_free({sn});")
            oc = on
        return self.holdtmp(oc, a[1])

    def okerr(self, t):
        if t[0] != 'inst' or t[1].form != 'closed':
            return None
        vs = {v.name: v for v in t[1].variants}
        ok, er = vs.get('Ok'), vs.get('Err')
        if ok is None or er is None or len(ok.fields) != 1 or len(er.fields) != 1:
            return None
        env = decl_env(t)
        return (ok, subst(ok.fields[0][1], env), er, subst(er.fields[0][1], env))

    # ---- literals and constructors

    def build(self, t, variant, cs):
        """C text that builds a value of record or closed type t from field C texts."""
        d = t[1]
        fs = variant.fields if variant else d.fields
        if d.value:
            if variant:
                inner = ', '.join(f".f_{f[0]} = {c}" for f, c in zip(fs, cs))
                return f"(({self.T(t)}){{.tag = {variant.index}, .u.V_{variant.name} = {{{inner}}}}})"
            inner = ', '.join(f".f_{f[0]} = {c}" for f, c in zip(fs, cs)) or '0'
            return f"(({self.T(t)}){{{inner}}})"
        n = self.ctx.fresh('o')
        self.emit(f"{self.T(t)} {n} = rt_alloc(sizeof(*{n}));")
        if variant:
            self.emit(f"{n}->tag = {variant.index};")
        pre = f"{n}->u.V_{variant.name}." if variant else f"{n}->"
        for f, c in zip(fs, cs):
            self.emit(f"{pre}f_{f[0]} = {c};")
        return n

    def construct_variant(self, vr, args, where, exp, targs, named=None):
        d = vr.decl
        if d.module is not self.ctx.module and not d.pub:
            self.err(f"{vr.name} is not public", where)
        tps = d.tparams
        bind = {}
        if targs is not None:
            if len(targs) != len(tps):
                self.err(f"{d.name} takes {len(tps)} type arguments, not {len(targs)}", where)
            for tp, a in zip(tps, targs):
                bind[tp] = self.prog.resolve_type(a, self.ctx.module, self.ctx.fd.env, 'targ', where)
        elif exp is not None and exp[0] == 'inst' and exp[1] is d:
            bind = dict(zip(tps, exp[2]))
        fts = [f[1] for f in vr.fields]
        names = [f[0] for f in vr.fields]
        order = None
        if named is not None:
            args = self.order_inits(names, named, vr.name, where)
            order = self.init_order
        if len(args) != len(fts):
            self.err(f"variant {vr.name} takes {len(fts)} values, not {len(args)}", where)
        sig = Sig(vr.name, 'ctor', fts, ('inst', d, tuple(('tp', p) for p in tps)), tps, bind=bind)
        sig.variant = vr
        return self.call(sig, None, args, where, exp, order=order)

    def order_inits(self, names, inits, tname, where):
        given = {}
        for n, x, w in inits:
            if n not in names:
                self.err(f"{tname} has no field '{n}'", w)
            if n in given:
                self.err(f"field '{n}' is given twice", w)
            given[n] = x
        for n in names:
            if n not in given:
                self.err(f"field '{n}' of {tname} needs a value", where)
        self.init_order = [names.index(n) for n, _, _ in inits]
        return [given[n] for n in names]

    def x_lit(self, e, exp):
        st = self.static(e.ty)
        if st is None or st[0] != 'decl':
            if st is not None and st[0] == 'type':
                self.err(f"{tstr(st[1])} cannot be built with {{...}}", e.where)
            self.err("unknown type in a literal", e.where)
        d = st[1]
        targs = st[2] if len(st) > 2 else None
        if isinstance(d, Variant):
            return self.construct_variant(d, None, e.where, exp, targs, named=e.inits)
        if not isinstance(d, TypeDecl):
            self.err(f"{d.name} is not a record type", e.where)
        if d.form == 'alias':
            t = self.static_type(st, e.where)
            if t[0] != 'inst':
                self.err(f"{d.name} is not a record type", e.where)
            d, targs = t[1], None
            if exp is None or exp[1] is not d:
                exp = t
        if d.form != 'record':
            self.err(f"{d.name} is not a record type{': build one of its variants' if d.form == 'closed' else ''}",
                     e.where)
        if d.module is not self.ctx.module:
            for f in d.fields:
                if not f[2]:
                    self.err(f"cannot build a {d.name} here: field '{f[0]}' is private to module "
                             f"{d.module.name}", e.where)
        tps = d.tparams
        bind = {}
        if targs is not None:
            if len(targs) != len(tps):
                self.err(f"{d.name} takes {len(tps)} type arguments, not {len(targs)}", e.where)
            for tp, a in zip(tps, targs):
                bind[tp] = self.prog.resolve_type(a, self.ctx.module, self.ctx.fd.env, 'targ', e.where)
        elif exp is not None and exp[0] == 'inst' and exp[1] is d:
            bind = dict(zip(tps, exp[2]))
        names = [f[0] for f in d.fields]
        args = self.order_inits(names, e.inits, d.name, e.where)
        sig = Sig(d.name, 'ctor', [f[1] for f in d.fields], ('inst', d, tuple(('tp', p) for p in tps)),
                  tps, bind=bind)
        sig.variant = None
        return self.call(sig, None, args, e.where, exp, fieldnames=names, order=self.init_order)

    def x_arrlit(self, e, exp):
        et = exp[1] if exp is not None and exp[0] == 'arr' else None
        vals = []
        for i, it in enumerate(e.items):
            v = self.rd(self.expr(it, et)) if False else self.expr(it, et)
            if v.t[0] == 'ref':
                v = self.deref(v)
            if et is None:
                et = v.t
            if v.t != et:
                self.err(f"array element is {tstr(v.t)}, not {tstr(et)}", it.where)
            if any(self.effects(x) for x in e.items[i + 1:]):
                v = self.snap_val(v)
            vals.append(v)
        if et is None:
            self.err("cannot infer the element type of [] here: it needs an expected type", e.where)
        if et == VOID:
            self.err("an array element needs a value", e.where)
        cs = [self.consume(v, e.where) for v in vals]
        n = self.ctx.fresh('a')
        self.emit(f"obx_arr {n} = rt_arr_new({len(cs)}, sizeof({self.T(et)}));")
        for i, c in enumerate(cs):
            self.emit(f"(({self.T(et)}*){n}.data)[{i}] = {c};")
        tmp = Tmp(n, ('arr', et))
        self.ctx.frames[-1].append(tmp)
        return Val(n, ('arr', et), 'tmp', tmp=tmp, root=tmp, mutable=True)

    # ---- calls

    def x_call(self, e, exp):
        f = e.fn
        if f.kind == 'name' and self.lookup_var(f.v) is None:
            if f.v in BUILTINS and (f.v != 'isNull' or self.prog.lookup(self.ctx.module, f.v) is None):
                mark = len(self.ctx.pending)
                r = getattr(self, 'b_' + f.v)(e, exp)
                del self.ctx.pending[mark:]
                return r
            if f.v in INTRINSICS and self.prog.lookup(self.ctx.module, f.v) is None:
                ps, ret, cf, loc = INTRINSICS[f.v]
                ps = [STR if p == 's' else p for p in ps]
                sig = Sig(f.v, 'intr', ps, ret, c=cf)
                sig.loc = loc
                return self.call(sig, None, e.args, e.where, exp)
        targs = None
        if f.kind == 'index' and f.e.kind == 'field' and self.static_ns(f.e.e) is None and self.static(f) is None \
                and all(self.typeish(x) for x in f.items):
            targs = [self.texpr(x) for x in f.items]
            f = f.e
        if f.kind == 'field':
            st = self.static_ns(f.e)
            if st is not None and st[0] != 'module':
                t = self.static_type(st, f.where)
                if t is None:
                    self.err(f"{getattr(st[1], 'name', '?')} has no associated function {f.name}", f.where)
                sig = self.assoc_sig(t, f.name, e.where)
                return self.call(sig, None, e.args, e.where, exp)
            if st is None:
                recv = self.expr(f.e, None)
                base = recv.t[1] if recv.t[0] == 'ref' else recv.t
                sig = self.method_sig(base, f.name, e.where, quiet=base[0] == 'inst')
                if sig is None:
                    fv = self.field_of(recv, f.name, e.where)
                    return self.call_value(fv, e, exp)
                return self.call(sig, recv, e.args, e.where, exp, targs=targs)
        st = self.static(f)
        if st is not None:
            if st[0] == 'type':
                return self.convert(st[1], e)
            if st[0] == 'module':
                self.err(f"{st[1].name} is a module", e.where)
            d = st[1]
            ta = st[2] if len(st) > 2 else None
            if isinstance(d, Variant):
                return self.construct_variant(d, e.args, e.where, exp, ta)
            if isinstance(d, FnDecl):
                return self.call(fn_sig(d), None, e.args, e.where, exp, targs=ta, alltargs=True)
            if isinstance(d, TypeDecl):
                if d.form == 'alias':
                    t = self.static_type(st, e.where)
                    if t[0] == 'prim':
                        return self.convert(t, e)
                self.err(f"{d.name} is a type: build it with {d.name}{{...}}", e.where)
            if isinstance(d, (Const, Global)) and d.__class__ is Global:
                return self.call_value(self.static_value(st, f, None), e, exp)
            self.err(f"{d.name} is not a function", e.where)
        return self.call_value(self.expr(f, None), e, exp)

    def typeish(self, x):
        """Whether x (in x.m[...]) can be a type argument rather than an index."""
        if x.kind == 'type':
            return True
        if x.kind in ('borrow', 'index'):
            return self.typeish(x.e)
        st = self.static_ns(x) if x.kind in ('name', 'field') else None
        return st is not None and (st[0] == 'type' or isinstance(st[1], TypeDecl))

    def call_value(self, fv, e, exp):
        fv = self.rd(fv)
        if fv.t[0] != 'fn':
            self.err(f"cannot call a {tstr(fv.t)}", e.where)
        sig = Sig('function value', 'fnval', list(fv.t[1]), fv.t[2], c=fv.c)
        return self.call(sig, None, e.args, e.where, exp)

    def assoc_sig(self, t, name, where, quiet=False):
        if t[0] == 'tp':
            ms = [m for m in self.prog.iface_members(t, name) if m[0] is None]
            if not ms:
                if quiet:
                    return None
                self.err(f"{tstr(t)} has no associated function '{name}' in its constraints", where)
            _, ps, ret, _ = ms[0]
            return Sig(f"{tstr(t)}.{name}", 'iassoc', ps, ret, tpt=t)
        if t[0] == 'inst' and t[2] is None:
            d = t[1]
            fd = d.methods.get(name)
            bind = {}
        else:
            fd = self.prog.methods_of(t).get(name)
            bind = {}
            if fd is not None:
                unify(fd.recv_type, t, bind, set(fd.tps))
        if fd is None or fd.recv_mode != 'assoc':
            if quiet:
                return None
            if fd is not None:
                self.err(f"{fd.label} is a method: call it on a value", where)
            self.err(f"{tstr(t) if t[2] is not None or t[0] != 'inst' else t[1].name} has no associated "
                     f"function '{name}'", where)
        self.visible(fd, where)
        return fn_sig(fd, bind)

    def visible(self, fd, where):
        if not fd.pub and fd.module is not self.ctx.module:
            self.err(f"{fd.label} is not public in module {fd.module.name}", where)

    def method_sig(self, t, name, where, quiet=False):
        if name == 'drop' and t[0] == 'tp' and self.prog.iface_members(t, name):
            self.err("a drop method cannot be called directly", where)
        if t[0] == 'tp':
            ms = [m for m in self.prog.iface_members(t, name) if m[0] is not None]
            if not ms:
                if quiet:
                    return None
                self.err(f"{tstr(t)} has no method '{name}' (its constraints do not provide it)", where)
            mode, ps, ret, _ = ms[0]
            rp = t if mode == 'val' else ('ref', t, mode == 'mut')
            return Sig(f"{tstr(t)}.{name}", 'imeth', [rp] + ps, ret, recv=mode, tpt=t)
        fd = self.prog.methods_of(t).get(name)
        if fd is None or fd.recv_mode == 'assoc':
            if quiet and fd is None:
                return None
            if fd is not None:
                self.err(f"{fd.label} is an associated function: call it as {tstr(t)}.{name}(...)", where)
            self.err(f"{tstr(t)} has no method '{name}'", where)
        if name == 'drop' and fd.owner is not None and getattr(fd.owner, 'drop', None) is fd:
            self.err("a drop method cannot be called directly", where)
        self.visible(fd, where)
        bind = {}
        unify(fd.recv_type, t, bind, set(fd.tps))
        return fn_sig(fd, bind)

    def call(self, sig, recv, args, where, exp, targs=None, alltargs=False, op=False, fieldnames=None,
             order=None):
        ctx = self.ctx
        tps = set(sig.tps)
        bind = dict(sig.bind)
        if targs is not None:
            own = sig.tps if alltargs else sig.tps[len(sig.tps) - sig.nown:]
            if len(targs) != len(own):
                self.err(f"{sig.label} takes {len(own)} type arguments, not {len(targs)}", where)
            for tp, a in zip(own, targs):
                bind[tp] = self.prog.resolve_type(a, ctx.module, ctx.fd.env, 'targ', where)
        if exp is not None and exp != VOID and sig.ret[0] != 'ref' and has_tp(sig.ret, tps):
            trial = dict(bind)
            if unify(sig.ret, exp, trial, tps):
                bind = trial
        allargs = ([recv] if recv is not None else []) + list(args)
        if len(allargs) != len(sig.params):
            n = len(sig.params) - (1 if recv is not None else 0)
            self.err(f"{sig.label} takes {n} arguments, not {len(args)}", where)
        vals, cid, mark = [None] * len(allargs), object(), len(ctx.pending)
        order = order or list(range(len(allargs)))      # evaluation order (source order of a literal)
        for pos, i in enumerate(order):
            a, pt = allargs[i], sig.params[i]
            isrecv = recv is not None and i == 0
            what = f"the receiver of {sig.label}" if isrecv else \
                f"argument {i + (0 if recv is not None else 1)} of {sig.label}"
            if fieldnames is not None:
                what = f"field '{fieldnames[i]}' of {sig.label}"
            if isinstance(a, Val):
                v = a
            else:
                pe = subst(pt, bind)
                v = self.expr(a, pe if not has_tp(pe, tps) else ('slice', None, pe[2]) if pe[0] == 'slice' else None)
            if pt[0] == 'slice':
                sv = self.as_slice(v)
                if sv is None:
                    self.err(f"{what} is {tstr(subst(pt, bind))}, not {tstr(v.t)}: pass a slice or a borrow "
                             f"({'mut &' if pt[2] else '&'}x)", where)
                if pt[2] and not sv.t[2]:
                    self.err(f"{what} needs a mut slice, not {tstr(sv.t)}", where)
                v = Val(sv.c, ('slice', sv.t[1], pt[2]), 'rv', root=sv.root, path=sv.path)
                if not unify(pt[1], v.t[1], bind, tps):
                    self.err(f"{what} is {tstr(subst(pt, bind))}, not {tstr(v.t)}", where)
                self.hold(v, pt[2], cid, where)
                vals[i] = ('b', v)
            elif pt[0] == 'ref':
                if v.t[0] != 'ref' and not isrecv:
                    self.err(f"{what} is {tstr(subst(pt, bind))}, not {tstr(v.t)}: pass a borrow "
                             f"({'mut &' if pt[2] else '&'}x)", where)
                if pt[2] and v.t[0] == 'ref' and not v.t[2]:
                    name = f" ('{v.root.name}' is a shared borrow)" if isinstance(v.root, Var) else ''
                    self.err(f"{what} needs a mut borrow, not {tstr(v.t)}{name}", where)
                v = self.borrow(v, pt[2], where, recv=isrecv)
                if not unify(pt[1], v.t[1], bind, tps):
                    self.err(f"{what} is {tstr(subst(pt, bind))}, not {tstr(v.t)}", where)
                self.hold(v, pt[2], cid, where)
                vals[i] = ('b', v)
            else:
                if v.t[0] == 'ref':
                    v = self.deref(v)
                if v.t == VOID:
                    self.err(f"{what} has no value", where)
                if not unify(pt, v.t, bind, tps):
                    self.err(f"{what} is {tstr(subst(pt, bind))}, not {tstr(v.t)}", where)
                if any(self.effects(allargs[j]) for j in order[pos + 1:]):
                    v = self.snap_val(v)
                if v.cat == 'place' and not is_copy(v.t):
                    self.hold(v, None, cid, where)
                vals[i] = ('v', v)
        missing = [tp.name for tp in sig.tps if tp not in bind]
        if missing:
            self.err(f"cannot infer the type argument {', '.join(missing)} of {sig.label}; give it "
                     "explicitly or declare the result type", where)
        if sig.tps and len(set(sig.tps)) == len(sig.tps):
            self.prog.check_targs(tuple(bind[tp] for tp in sig.tps), sig.tps, ctx.module, where)
        ctx.pending[mark:] = [e for e in ctx.pending[mark:] if e[2] is not None]
        cs = []
        for kind, v in vals:
            if kind == 'b' or sig.kind in ('intr', 'extern'):
                cs.append(v.c)
            else:
                cs.append(self.consume(v, where))
        del ctx.pending[mark:]
        ret = subst(sig.ret, bind)
        if sig.kind == 'ctor':
            t = ret
            if not sig.variant and not t[1].value or sig.variant and not t[1].value:
                pass
            return self.holdtmp(self.build(t, sig.variant, cs), t)
        if sig.kind == 'fn':
            text = f"{self.H('fn', sig.fd, tuple(bind[tp] for tp in sig.tps))}({', '.join(cs)})"
        elif sig.kind == 'imeth':
            text = self.H('imeth', sig.tpt, sig.label.split('.')[-1], sig.recv, cs[0], tuple(cs[1:]))
        elif sig.kind == 'iassoc':
            text = f"{self.H('iassoc', sig.tpt, sig.label.split('.')[-1])}({', '.join(cs)})"
        elif sig.kind == 'fnval':
            text = f"({sig.c})({', '.join(cs)})"
        elif sig.kind == 'intr':
            if sig.loc:
                cs.append(self.W(where))
            text = f"{sig.c}({', '.join(cs)})"
        else:
            cs2 = []
            for (kind, v), c in zip(vals, cs):
                cs2.append(f"rt_cstr({c}, {self.W(where)})" if v.t == STR else c)
            text = f"x_{sig.fd.name}({', '.join(cs2)})"
            if STR in [v.t for _, v in vals]:       # free the NUL-terminated copies after the call
                v = self.holdtmp(text, ret) if ret != VOID else self.emit(text + ';')
                self.emit("rt_cfree();")
                return v or Val('', VOID, 'rv')
        if ret == VOID:
            self.emit(text + ';')
            return Val('', VOID, 'rv')
        if ret[0] in ('ref', 'slice'):
            bi = [i for i, p in enumerate(sig.params) if p[0] in ('ref', 'slice')]
            src = vals[bi[0]][1] if len(bi) == 1 else None
            n = ctx.fresh('b')
            self.emit(f"{self.T(ret[1])}* {n} = {text};" if ret[0] == 'ref' else f"obx_slice {n} = {text};")
            root = src.root if src is not None else None
            path = src.path + ('*',) if src is not None else ()
            return Val(n, ret, 'rv', root=root, path=path)
        return self.holdtmp(text, ret)

    def convert(self, t, e):
        if t[0] != 'prim' or t[1] in ('bool', 'str', 'ptr'):
            self.err(f"{tstr(t)} cannot be called as a conversion", e.where)
        if len(e.args) != 1:
            self.err(f"the conversion {t[1]}(x) takes one argument", e.where)
        v = self.rd(self.expr(e.args[0], None))
        s, d = v.t, t[1]
        W = self.W(e.where)
        if s[0] != 'prim' or s[1] not in INTS + ('float',):
            self.err(f"cannot convert {tstr(s)} to {d}", e.where)
        if s == t:
            return v
        if d == 'float':
            return Val(f"((double){v.c})", t, 'rv')
        if s == FLOAT:
            return Val(f"rt_cvf_{d}({v.c}, {W})", t, 'rv')
        if s[1] == 'u128':
            return Val(f"rt_cvu_{d}({v.c}, {W})", t, 'rv')
        return Val(f"rt_cvs_{d}((__int128){v.c}, {W})", t, 'rv')

    # ---- built-in functions

    def nargs(self, e, n):
        if len(e.args) != n:
            self.err(f"{e.fn.v} takes {n} argument{'s' if n != 1 else ''}, not {len(e.args)}", e.where)

    def arr_arg(self, e, mut, slices=False):
        v = self.expr(e.args[0], ('slice', None, mut) if slices else None)
        if slices and v.t[0] != 'slice':
            s = self.as_slice(v) if v.t[0] == 'ref' and (v.t[2] or not mut) else None
            if s is None:
                self.err(f"{e.fn.v} needs a mut slice or mut &array as its first argument, not {tstr(v.t)}",
                         e.where)
            v = s
        if slices:
            if mut and not v.t[2]:
                self.err(f"{e.fn.v} needs a mut slice, not {tstr(v.t)}", e.where)
            v = self.borrow(v, mut, e.where)
            self.hold(v, mut, object(), e.where)
            return v
        if v.t[0] != 'ref' or (mut and not v.t[2]):
            self.err(f"{e.fn.v} needs {'mut &' if mut else '&'}array as its first argument, not "
                     f"{tstr(v.t)}", e.where)
        if v.t[1][0] != 'arr':
            self.err(f"{e.fn.v} needs an array, not {tstr(v.t[1])}", e.where)
        return self.builtin_borrow(v, mut, e.where)

    def builtin_borrow(self, v, mut, where):
        v = self.borrow(v, mut, where)
        self.hold(v, mut, object(), where)
        return v

    def b_len(self, e, exp):
        self.nargs(e, 1)
        v = self.deref(self.expr(e.args[0], None))
        if v.t == STR:
            return Val(f"((int64_t)({v.c}).len)", INT, 'rv')
        if v.t[0] == 'slice':
            return Val(f"({v.c}).len", INT, 'rv')
        if v.t[0] != 'arr':
            self.err(f"len needs an array, a slice or a str, not {tstr(v.t)}", e.where)
        return Val(f"({v.c}).len", INT, 'rv')

    def b_isNull(self, e, exp):
        self.nargs(e, 1)
        v = self.rd(self.expr(e.args[0], None))
        if v.t != ('prim', 'ptr'):
            self.err(f"isNull needs a ptr, not {tstr(v.t)}", e.where)
        return Val(f"({v.c} == 0)", BOOL, 'rv')

    def b_append(self, e, exp):
        self.nargs(e, 2)
        a = self.arr_arg(e, True)
        et = a.t[1][1]
        v = self.expr(e.args[1], et)
        v = self.coerce(v, et, e.where, "the appended value")
        c = self.consume(v, e.where)
        n = self.ctx.fresh('ap')
        self.emit(f"{self.T(et)} {n} = {c};")
        self.emit(f"*({self.T(et)}*)rt_arr_push({a.c}, sizeof({self.T(et)})) = {n};")
        return Val('', VOID, 'rv')

    def b_pop(self, e, exp):
        self.nargs(e, 1)
        a = self.arr_arg(e, True)
        et = a.t[1][1]
        return self.holdtmp(f"*({self.T(et)}*)rt_arr_pop({a.c}, sizeof({self.T(et)}), {self.W(e.where)})", et)

    def b_swap(self, e, exp):
        self.nargs(e, 3)
        a = self.arr_arg(e, True, True)
        i = self.rd(self.expr(e.args[1], INT))
        if self.effects(e.args[2]):
            i = self.snap_val(i)
        j = self.rd(self.expr(e.args[2], INT))
        if i.t != INT or j.t != INT:
            self.err("swap needs int indexes", e.where)
        et = a.t[1]
        self.emit(f"rt_swap({a.c}, {i.c}, {j.c}, sizeof({self.T(et)}), {self.W(e.where)});")
        return Val('', VOID, 'rv')

    def b_replace(self, e, exp):
        self.nargs(e, 2)
        p = self.expr(e.args[0], None)
        if p.t[0] != 'ref' or not p.t[2]:
            self.err(f"replace needs mut &place as its first argument, not {tstr(p.t)}", e.where)
        self.self_in_drop(p, e.where)
        p = self.builtin_borrow(p, True, e.where)
        t = p.t[1]
        v = self.coerce(self.expr(e.args[1], t), t, e.where, "the new value")
        c = self.consume(v, e.where)
        n = self.ctx.fresh('old')
        self.emit(f"{self.T(t)} {n} = *{p.c};")
        self.emit(f"*{p.c} = {c};")
        if is_pod(t):
            return Val(n, t, 'rv')
        tmp = Tmp(n, t)
        self.ctx.frames[-1].append(tmp)
        return Val(n, t, 'tmp', tmp=tmp, root=tmp, mutable=True)

    def b_move(self, e, exp):
        self.nargs(e, 1)
        a = e.args[0]
        if a.kind != 'name' or self.lookup_var(a.v) is None:
            self.err("move needs a local variable", e.where)
        v = self.expr(a, None)
        if v.t[0] == 'ref':
            self.err(f"cannot move the borrow '{a.v}'", e.where)
        return self.holdtmp(self.consume(v, e.where), v.t)

    def b_array(self, e, exp):
        self.nargs(e, 2)
        n = self.rd(self.expr(e.args[0], INT))
        if n.t != INT:
            self.err("array needs an int length", e.where)
        if self.effects(e.args[1]):
            n = self.snap_val(n)
        et = exp[1] if exp is not None and exp[0] == 'arr' else None
        v = self.rd(self.expr(e.args[1], et))
        if not is_copy(v.t):
            self.err(f"array needs a copy value, and {tstr(v.t)} is not a copy type", e.where)
        ctx = self.ctx
        vn, an, i = ctx.fresh('fill'), ctx.fresh('a'), ctx.fresh('i')
        self.emit(f"{self.T(v.t)} {vn} = {self.consume(v, e.where)};")
        self.emit(f"obx_arr {an} = rt_arr_fill({n.c}, sizeof({self.T(v.t)}), {self.W(e.where)});")
        self.emit(f"for (int64_t {i} = 0; {i} < {an}.len; {i}++) (({self.T(v.t)}*){an}.data)[{i}] = "
                  f"{self.dup(vn, v.t)};")
        self.drop(vn, v.t)
        tmp = Tmp(an, ('arr', v.t))
        ctx.frames[-1].append(tmp)
        return Val(an, ('arr', v.t), 'tmp', tmp=tmp, root=tmp, mutable=True)

    def b_panic(self, e, exp):
        self.nargs(e, 1)
        v = self.rd(self.expr(e.args[0], STR))
        if v.t != STR:
            self.err(f"panic needs a str, not {tstr(v.t)}", e.where)
        self.emit(f"rt_panic({v.c}, {self.W(e.where)});")
        return Val('', VOID, 'rv', noreturn=True)

    def b_assert(self, e, exp):
        self.nargs(e, 1)
        v = self.rd(self.expr(e.args[0], BOOL))
        if v.t != BOOL:
            self.err(f"assert needs a bool, not {tstr(v.t)}", e.where)
        self.emit(f"if (!({v.c})) rt_panic({self.H('str', b'assertion failed')}, {self.W(e.where)});")
        return Val('', VOID, 'rv')

    def b_exit(self, e, exp):
        self.nargs(e, 1)
        v = self.rd(self.expr(e.args[0], INT))
        if v.t != INT:
            self.err(f"exit needs an int, not {tstr(v.t)}", e.where)
        self.emit(f"rt_exit({v.c});")
        return Val('', VOID, 'rv', noreturn=True)

    # ---------------------------------------------------------- globals

    def global_init(self, g):
        self.ctx = Ctx(FnDecl(N('fn', g.where, name='<global>', pub=False, extern=None, tparams=[]), g.module))
        self.ctx.fd.env = {}
        try:
            c = self.const_init(g.ast.e, g.ty)
        except CompileError as e:
            raise CompileError(f"the initializer of global {g.name} must be constant: {e.msg}", e.where)
        g.init = (c, self.ctx.holes)
        self.ctx = None

    def const_init(self, e, t):
        if t[0] == 'prim':
            v = self.prog.const_as(self.prog.fold(e, self.ctx.module, t), t, e.where)
            if t == STR:
                return self.H('gstr', v)
            if t == BOOL:
                return 'true' if v else 'false'
            return self.cint(v, t)
        if t[0] != 'inst':
            self.err("not a constant", e.where)
        d = t[1]
        env = decl_env(t)
        if e.kind == 'lit' and d.form == 'record':
            args = self.order_inits([f[0] for f in d.fields], e.inits, d.name, e.where)
            cs = [self.const_init(a, subst(f[1], env)) for a, f in zip(args, d.fields)]
            return '{' + ', '.join(f".f_{f[0]} = {c}" for f, c in zip(d.fields, cs)) + '}'
        if d.form == 'closed':
            fn = e.fn if e.kind == 'call' else e.ty if e.kind == 'lit' else e
            st = self.static(fn)
            if st and st[0] == 'decl' and isinstance(st[1], Variant) and st[1].decl is d:
                vr = st[1]
                if e.kind == 'lit':
                    args = self.order_inits([f[0] for f in vr.fields], e.inits, vr.name, e.where)
                else:
                    args = e.args if e.kind == 'call' else []
                if len(args) != len(vr.fields):
                    self.err(f"variant {vr.name} takes {len(vr.fields)} values", e.where)
                cs = [self.const_init(a, subst(f[1], env)) for a, f in zip(args, vr.fields)]
                inner = ', '.join(f".f_{f[0]} = {c}" for f, c in zip(vr.fields, cs))
                return f"{{.tag = {vr.index}, .u.V_{vr.name} = {{{inner}}}}}"
        self.err("not a constant", e.where)


from sema import depth  # noqa: E402

CTYPES = {'int': 'int64_t', 'i32': 'int32_t', 'u32': 'uint32_t', 'u64': 'uint64_t', 'byte': 'uint8_t',
          'i128': '__int128', 'u128': 'unsigned __int128', 'float': 'double', 'bool': 'bool',
          'str': 'obx_str', 'ptr': 'void*', 'void': 'void'}
