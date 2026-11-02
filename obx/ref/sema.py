"""Declarations, types, modules and constraints (SPEC sections 4, 5, 6).

A type is a tuple:
  ('prim', name)  ('arr', T)  ('ref', T, mut)  ('slice', T, mut)  ('fn', (params...), ret)
  ('inst', TypeDecl, (args...))  ('tp', TParam)  ('self',) inside interfaces
"""
import os
from syntax import CompileError, parse

INTS = ('int', 'i32', 'u32', 'u64', 'byte', 'i128', 'u128')
PRIMS = INTS + ('float', 'bool', 'str', 'ptr')
INT, BOOL, STR, FLOAT, BYTE, VOID, NEVER = (('prim', n) for n in ('int', 'bool', 'str', 'float', 'byte', 'void',
                                                                   'never'))
SELF = ('self',)
BITS = {'int': 64, 'i32': 32, 'u32': 32, 'u64': 64, 'byte': 8, 'i128': 128, 'u128': 128}
RESERVED = {'len', 'append', 'pop', 'swap', 'replace', 'move', 'array', 'panic', 'assert', 'exit'}
PRELUDE = ('Some', 'None', 'Ok', 'Err')
RANGES = {'int': (-2**63, 2**63 - 1), 'i32': (-2**31, 2**31 - 1), 'u32': (0, 2**32 - 1),
          'u64': (0, 2**64 - 1), 'byte': (0, 255), 'i128': (-2**127, 2**127 - 1),
          'u128': (0, 2**128 - 1)}


class TParam:
    def __init__(self, name, where, copy=False):
        self.name, self.where, self.copy, self.cons = name, where, copy, []

    def __repr__(self):
        return self.name


class TypeDecl:
    def __init__(self, ast, mod):
        self.ast, self.module, self.name, self.where = ast, mod, ast.name, ast.where
        self.pub, self.form = ast.pub, ast.form
        self.value = getattr(ast, 'value', False)
        self.tparams, self.fields, self.variants, self.members = [], [], [], []
        self.methods, self.drop, self.target, self.resolving = {}, None, None, False
        self.cname = f"{mod.cprefix}_{self.name}"


class Variant:
    def __init__(self, name, decl, index, fields, named, where):
        self.name, self.decl, self.index, self.fields = name, decl, index, fields
        self.named, self.where, self.pub = named, where, decl.pub
        self.module = decl.module


class Member:
    def __init__(self, name, mode, params, ret, where):
        self.name, self.mode, self.params, self.ret, self.where = name, mode, params, ret, where


class FnDecl:
    def __init__(self, ast, mod):
        self.ast, self.module, self.name, self.where, self.pub = ast, mod, ast.name, ast.where, ast.pub
        self.extern = ast.extern
        self.tps, self.params, self.ret = [], [], VOID
        self.recv_mode, self.recv_name, self.recv_type, self.owner = None, None, None, None
        self.template, self.bparam = None, None

    @property
    def label(self):
        if self.owner is None:
            return self.name
        o = self.owner if isinstance(self.owner, str) else self.owner.name
        return f"{o}.{self.name}"


class Const:
    def __init__(self, ast, mod):
        self.ast, self.module, self.name, self.where, self.pub = ast, mod, ast.name, ast.where, ast.pub
        self.value, self.busy, self.table = None, False, None
        self.cname = f"k{len(mod.cprefix)}_{mod.cprefix}_{self.name}"


class Global:
    def __init__(self, ast, mod):
        self.ast, self.module, self.name, self.where, self.pub = ast, mod, ast.name, ast.where, ast.pub
        self.ty, self.value = None, None
        self.cname = f"g{len(mod.cprefix)}_{mod.cprefix}_{self.name}"


class Module:
    def __init__(self, path, name, cprefix, is_std):
        self.path, self.name, self.cprefix, self.is_std = path, name, cprefix, is_std
        self.imports, self.scope, self.fns = {}, {}, []


# ---------------------------------------------------------------- type helpers

def tstr(t):
    k = t[0]
    if k == 'prim':
        return t[1]
    if k == 'arr':
        return '[]' + tstr(t[1])
    if k == 'ref':
        return ('mut &' if t[2] else '&') + tstr(t[1])
    if k == 'slice':
        return ('mut &[' if t[2] else '&[') + tstr(t[1]) + ']'
    if k == 'fn':
        r = '' if t[2] == VOID else ': ' + tstr(t[2])
        return 'fn(' + ', '.join(tstr(p) for p in t[1]) + ')' + r
    if k == 'tp':
        return t[1].name
    if k == 'self':
        return 'Self'
    return t[1].name + ('[' + ', '.join(tstr(a) for a in t[2]) + ']' if t[2] else '')


def subst(t, env):
    if not env:
        return t
    k = t[0]
    if k == 'tp':
        return env.get(t[1], t)
    if k == 'self':
        return env.get('Self', t)
    if k == 'prim':
        return t
    if k == 'arr':
        return ('arr', subst(t[1], env))
    if k in ('ref', 'slice'):
        return (k, subst(t[1], env), t[2])
    if k == 'fn':
        return ('fn', tuple(subst(p, env) for p in t[1]), subst(t[2], env))
    return ('inst', t[1], tuple(subst(a, env) for a in t[2]))


def has_tp(t, tps=None):
    k = t[0]
    if k == 'tp':
        return tps is None or t[1] in tps
    if k in ('prim', 'self'):
        return False
    if k in ('arr', 'ref', 'slice'):
        return has_tp(t[1], tps)
    if k == 'fn':
        return any(has_tp(p, tps) for p in t[1]) or has_tp(t[2], tps)
    return any(has_tp(a, tps) for a in t[2])


def depth(t):
    k = t[0]
    if k in ('arr', 'ref', 'slice'):
        return 1 + depth(t[1])
    if k == 'fn':
        return 1 + max([depth(p) for p in t[1]] + [depth(t[2])])
    if k == 'inst':
        return 1 + max([depth(a) for a in t[2]] + [0])
    return 0


def unify(p, t, bind, tps):
    """Match pattern p (which may mention the type parameters in tps) against t."""
    k = p[0]
    if k == 'tp' and p[1] in tps:
        if p[1] in bind:
            return bind[p[1]] == t
        bind[p[1]] = t
        return True
    if k != t[0]:
        return False
    if k in ('prim', 'tp', 'self'):
        return p == t
    if k == 'arr':
        return unify(p[1], t[1], bind, tps)
    if k in ('ref', 'slice'):
        return p[2] == t[2] and unify(p[1], t[1], bind, tps)
    if k == 'fn':
        return (len(p[1]) == len(t[1]) and all(unify(a, b, bind, tps) for a, b in zip(p[1], t[1]))
                and unify(p[2], t[2], bind, tps))
    return p[1] is t[1] and all(unify(a, b, bind, tps) for a, b in zip(p[2], t[2]))


def decl_env(t):
    return dict(zip(t[1].tparams, t[2]))


def field_types(t):
    d = t[1]
    env = decl_env(t)
    if d.form == 'record':
        return [(n, subst(ft, env)) for n, ft, _ in d.fields]
    return [(n, subst(ft, env)) for v in d.variants for n, ft, _ in v.fields]


def is_copy(t):
    k = t[0]
    if k in ('prim', 'fn', 'ref', 'slice'):
        return True
    if k == 'arr':
        return False
    if k == 'tp':
        return t[1].copy
    if k == 'self':
        return False
    d = t[1]
    if not d.value or d.drop is not None:
        return False
    return all(is_copy(ft) for _, ft in field_types(t))


# ---------------------------------------------------------------- the program

class Program:
    def __init__(self, std_path, libroots):
        self.libroots = libroots
        self.modules = {}
        self.prim_methods = {p: {} for p in PRIMS}
        self.pending_checks = []
        self.resolved = False
        self.prelude = {}           # Some, None, Ok, Err: the variants that may be written bare
        self.externs = {}           # C name -> (signature, first FnDecl)
        self.std = self.load(std_path, None, 'std', True)

    # ---- loading

    def load(self, path, chain, name, is_std=False):
        path = os.path.abspath(path)
        if path in self.modules:
            return self.modules[path]
        chain = (chain or []) + [path]
        try:
            with open(path, encoding='utf-8') as f:
                src = f.read()
        except OSError:
            raise CompileError(f"cannot read {path}")
        fname = os.path.basename(path)
        imports, decls = parse(src, fname)
        used = {m.cprefix for m in self.modules.values()}
        base = ''.join(c if c.isalnum() else '_' for c in name)
        cp, n = base, 1
        while cp in used:
            n += 1
            cp = f"{base}{n}"
        mod = Module(path, name, cp, is_std)
        mod.loading = True
        self.modules[path] = mod
        for imp in imports:
            target = self.find_module(imp.path, os.path.dirname(path), imp.where)
            if target in self.modules and getattr(self.modules[target], 'loading', False):
                names = ' -> '.join(os.path.basename(p)[:-4] for p in chain[chain.index(target):] + [target]) \
                    if target in chain else os.path.basename(target)
                raise CompileError(f"import cycle: {names}", imp.where)
            sub = self.load(target, chain, os.path.basename(imp.path))
            alias = imp.alias or os.path.basename(imp.path)
            if alias in mod.imports:
                raise CompileError(f"module name {alias} is imported twice", imp.where)
            mod.imports[alias] = sub
        mod.loading = False
        self.collect(mod, decls)
        return mod

    def find_module(self, rel, here, where):
        # SPEC 5.1: ./ and ../ are relative to the importing file; any other path is a library module.
        local = rel.startswith(('./', '../'))
        for root in [here] if local else self.libroots:
            p = os.path.abspath(os.path.join(root, rel + '.obx'))
            if os.path.isfile(p):
                return p
        raise CompileError(f"cannot find module {rel}" + ("" if local else " in the library roots (a local "
                           f"module is imported as \"./{rel}\")"), where)

    def collect(self, mod, decls):
        def add(name, d, where):
            if name in mod.scope:
                raise CompileError(f"{name} is declared twice", where)
            if name in PRIMS:
                raise CompileError(f"{name} is a primitive type and cannot be redeclared", where)
            if name in RESERVED:
                raise CompileError(f"{name} is a built-in function; its name is reserved", where)
            if name in PRELUDE and not mod.is_std:
                raise CompileError(f"{name} is a prelude variant (std's {name}); no other module may "
                                   "declare a top-level name that clashes with it", where)
            mod.scope[name] = d
        for a in decls:
            if a.kind == 'type':
                d = TypeDecl(a, mod)
                add(a.name, d, a.where)
                if a.form == 'closed':
                    for i, (vn, fs, named, vw) in enumerate(a.variants):
                        v = Variant(vn, d, i, fs, named, vw)
                        d.variants.append(v)
                        if mod.is_std and vn in PRELUDE:
                            self.prelude[vn] = v
            elif a.kind == 'fn':
                fd = FnDecl(a, mod)
                mod.fns.append(fd)
                if a.recv is None:
                    add(a.name, fd, a.where)
            elif a.kind == 'const':
                add(a.name, Const(a, mod), a.where)
            else:
                add(a.name, Global(a, mod), a.where)

    # ---- names

    def lookup(self, mod, name):
        d = mod.scope.get(name)
        if d is None and not mod.is_std:
            d = self.std.scope.get(name)
            if d is not None and not d.pub:
                d = None
        return d

    def lookup_in(self, mod, target, name, where):
        d = target.scope.get(name)
        if d is None:
            raise CompileError(f"module {target.name} has no {name}", where)
        if not d.pub and target is not mod:
            raise CompileError(f"{name} is not public in module {target.name}", where)
        return d

    # ---- resolution of declarations

    def resolve_all(self):
        mods = list(self.modules.values())
        for m in mods:
            for d in list(m.scope.values()):
                if isinstance(d, TypeDecl):
                    self.make_tparams(d)
        for m in mods:
            for d in list(m.scope.values()):
                if isinstance(d, TypeDecl):
                    self.resolve_typedecl(d)
        for m in mods:
            for fd in m.fns:
                self.resolve_fn(fd)
        for m in mods:
            for d in m.scope.values():
                if isinstance(d, Global):
                    self.resolve_global(d)
                elif isinstance(d, Const):
                    self.const_value(d)
        self.resolved = True
        for args, tparams, mod, where in self.pending_checks:
            self.check_targs(args, tparams, mod, where)
        self.pending_checks = []
        for m in mods:
            for d in m.scope.values():
                if isinstance(d, TypeDecl) and d.value and d.form != 'alias':
                    self.check_inline_cycle(d)

    def make_tparams(self, d):
        seen = set()
        for (n, cons, w) in d.ast.tparams:
            if n in seen:
                raise CompileError(f"type parameter {n} is declared twice", w)
            seen.add(n)
            d.tparams.append(TParam(n, w, copy='copy' in cons))

    def tpenv_of(self, d):
        return {p.name: ('tp', p) for p in d.tparams}

    def resolve_cons(self, tp, cons, mod, env):
        for c in cons:
            if c == 'copy':
                tp.copy = True
                continue
            t = self.resolve_type(c, mod, env, 'constraint')
            tp.cons.append(t)

    def resolve_typedecl(self, d):
        env = self.tpenv_of(d)
        mod = d.module
        for p, (n, cons, w) in zip(d.tparams, d.ast.tparams):
            self.resolve_cons(p, cons, mod, env)
        a = d.ast
        if d.form == 'alias':
            self.alias_target(d)
        elif d.form == 'record':
            names = set()
            for n, ft, pub in a.fields:
                if n in names:
                    raise CompileError(f"field {n} is declared twice", d.where)
                names.add(n)
                d.fields.append((n, self.resolve_type(ft, mod, env, 'field'), pub))
        elif d.form == 'closed':
            for v in d.variants:
                v.fields = [(n, self.resolve_type(ft, mod, env, 'field'), True) for n, ft, _ in v.fields]
        else:
            env = dict(env, Self=SELF)
            for m in a.members:
                ps = [self.resolve_type(pt, mod, env, 'param') for _, pt, _, _ in m.params]
                ret = self.resolve_type(m.ret, mod, env, 'ret') if m.ret else VOID
                d.members.append(Member(m.name, m.mode, ps, ret, m.where))

    def alias_target(self, d):
        if d.target is None:
            if d.resolving:
                raise CompileError(f"type alias {d.name} refers to itself", d.where)
            d.resolving = True
            d.target = self.resolve_type(d.ast.target, d.module, self.tpenv_of(d), 'alias')
            d.resolving = False
        return d.target

    def resolve_type(self, a, mod, env, pos, where=None):
        k = a[0]
        if k in ('ref', 'slice'):
            if pos not in ('param', 'ret', 'let'):
                what = {'field': 'a field', 'elem': 'an array element', 'targ': 'a type argument',
                        'var': 'the type of a var', 'global': 'the type of a global'}.get(pos, 'used here')
                raise CompileError(f"a borrow type cannot be {what}: borrows are second class",
                                   where or self.type_where(a))
            return (k, self.resolve_type(a[1], mod, env, 'inner' if k == 'ref' else 'elem', where), a[2])
        if k == 'arr':
            return ('arr', self.resolve_type(a[1], mod, env, 'elem', where))
        if k == 'fnt':
            ps = tuple(self.resolve_type(p, mod, env, 'param', where) for p in a[1])
            r = self.resolve_type(a[2], mod, env, 'ret', where) if a[2] else VOID
            return ('fn', ps, r)
        if k == 'tparam':
            raise CompileError("a type parameter declaration is only allowed in a receiver", a[3])
        if k == 'never':
            if pos != 'ret':
                raise CompileError("never can only be a result type", where or a[1])
            return NEVER
        _, parts, args, w = a
        if len(parts) == 1:
            n = parts[0]
            if n in env:
                if args:
                    raise CompileError(f"type parameter {n} takes no type arguments", w)
                return env[n]
            if n in PRIMS:
                if args:
                    raise CompileError(f"{n} takes no type arguments", w)
                return ('prim', n)
            d = self.lookup(mod, n)
        else:
            m = mod.imports.get(parts[0])
            if m is None:
                raise CompileError(f"unknown module {parts[0]}", w)
            d = self.lookup_in(mod, m, parts[1], w)
            n = parts[1]
        if d is None:
            raise CompileError(f"unknown type {n}", w)
        if not isinstance(d, TypeDecl):
            raise CompileError(f"{n} is not a type", w)
        targs = tuple(self.resolve_type(x, mod, env, 'targ', w) for x in args)
        if len(targs) != len(d.tparams):
            raise CompileError(f"{n} takes {len(d.tparams)} type arguments, not {len(targs)}", w)
        if d.form == 'interface':
            if pos != 'constraint':
                raise CompileError(f"{n} is an interface: it can only constrain a type parameter", w)
            return ('inst', d, targs)
        if pos == 'constraint':
            raise CompileError(f"{n} is not an interface", w)
        if d.form == 'alias':
            return subst(self.alias_target(d), dict(zip(d.tparams, targs)))
        if self.resolved:
            self.check_targs(targs, d.tparams, mod, w)
        else:
            self.pending_checks.append((targs, d.tparams, mod, w))
        return ('inst', d, targs)

    def type_where(self, a):
        while a[0] in ('ref', 'arr', 'slice'):
            a = a[1]
        return a[3] if a[0] == 'name' else ''

    def check_targs(self, targs, tparams, mod, where):
        env = dict(zip(tparams, targs))
        for p, t in zip(tparams, targs):
            if t[0] in ('ref', 'slice'):
                raise CompileError(f"a borrow type ({tstr(t)}) cannot be a type argument (type parameter "
                                   f"{p.name}): borrows are second class", where)
            if p.copy and not is_copy(t):
                raise CompileError(f"{tstr(t)} is not a copy type (type parameter {p.name} needs copy)",
                                   where)
            for c in p.cons:
                c = subst(c, env)
                why = self.satisfies(t, c, mod)
                if why:
                    raise CompileError(f"{tstr(t)} does not satisfy {tstr(c)} (type parameter "
                                       f"{p.name}): {why}", where)

    def resolve_fn(self, fd):
        a, mod = fd.ast, fd.module
        env = {}
        if fd.extern is not None:
            for n, pt, isvar, w in a.params:
                t = self.resolve_type(pt, mod, env, 'param', w)
                ok = t[0] == 'prim' and t[1] != 'void' or (t[0] == 'ref' and t[2] and t[1][0] == 'prim'
                                                          and t[1][1] in INTS + ('ptr', 'float'))
                if not ok:
                    raise CompileError(f"extern parameter {n} cannot have type {tstr(t)}", w)
                fd.params.append((n, t, False, w))
            if a.ret:
                fd.ret = self.resolve_type(a.ret, mod, env, 'ret')
                if fd.ret[0] != 'prim':
                    raise CompileError(f"an extern function cannot return {tstr(fd.ret)}", a.where)
            sig = ([t for _, t, _, _ in fd.params], fd.ret)
            other = self.externs.setdefault(a.cname, (sig, fd))
            if other[0] != sig:
                raise CompileError(f"extern {fd.name} declares the C function {a.cname} with another signature "
                                   f"than {other[1].name} in module {other[1].module.name}", a.where)
            return
        if a.recv is not None:
            rname, rt = a.recv
            mode = 'val'
            if rt[0] == 'ref':
                mode = 'mut' if rt[2] else 'ref'
                rt = rt[1]
            if rt[0] != 'name':
                raise CompileError("a receiver must name a type", a.where)
            _, parts, args, w = rt
            if len(parts) == 1 and parts[0] in PRIMS:
                if not mod.is_std:
                    raise CompileError(f"methods of {parts[0]} can only be declared in module std, "
                                       "the module of the primitive types", a.where)
                fd.owner = parts[0]
                table = self.prim_methods[parts[0]]
                fd.recv_type = ('prim', parts[0])
                if args:
                    raise CompileError(f"{parts[0]} takes no type arguments", w)
            else:
                d = self.lookup(mod, parts[0]) if len(parts) == 1 else None
                if len(parts) != 1 or not isinstance(d, TypeDecl) or d.form not in ('record', 'closed'):
                    if isinstance(d, TypeDecl) and d.form == 'alias':
                        raise CompileError("a receiver must name the type itself, not an alias", a.where)
                    raise CompileError(f"a method receiver must be a record or closed type declared in "
                                       f"this module, not {'.'.join(parts)}", a.where)
                if d.module is not mod:
                    raise CompileError(f"methods of {d.name} can only be declared in its module "
                                       f"{d.module.name}", a.where)
                if len(args) != len(d.tparams):
                    raise CompileError(f"the receiver {d.name} needs {len(d.tparams)} type arguments", w)
                fd.owner = d
                table = d.methods
                extra = []
                for p, x in zip(d.tparams, args):
                    if x[0] == 'tparam':
                        nm, cons, xw = x[1], x[2], x[3]
                    elif x[0] == 'name' and len(x[1]) == 1 and not x[2]:
                        nm, cons, xw = x[1][0], [], x[3]
                    else:
                        raise CompileError("the type arguments of a receiver must be type parameter names",
                                           w)
                    if nm in env:
                        raise CompileError(f"type parameter {nm} is declared twice", xw)
                    tp = TParam(nm, xw, copy=p.copy)
                    env[nm] = ('tp', tp)
                    fd.tps.append(tp)
                    extra.append(cons)
                denv = dict(zip(d.tparams, [('tp', tp) for tp in fd.tps]))
                for tp, p, cons in zip(fd.tps, d.tparams, extra):
                    tp.cons = [subst(c, denv) for c in p.cons]
                    self.resolve_cons(tp, cons, mod, env)
                fd.recv_type = ('inst', d, tuple(('tp', tp) for tp in fd.tps))
            if rname is None:
                if mode != 'val':
                    raise CompileError("an associated function has no receiver to borrow", a.where)
                mode = 'assoc'
            fd.recv_mode, fd.recv_name = mode, rname
            if a.name in table:
                raise CompileError(f"method {fd.label} is declared twice", a.where)
            table[a.name] = fd
        for n, cons, w in a.tparams:
            if n in env:
                raise CompileError(f"type parameter {n} is declared twice", w)
            tp = TParam(n, w, copy='copy' in cons)
            env[n] = ('tp', tp)
            fd.tps.append(tp)
        for tp, (n, cons, w) in zip(fd.tps[len(fd.tps) - len(a.tparams):], a.tparams):
            self.resolve_cons(tp, [c for c in cons if c != 'copy'], mod, env)
        fd.env = env
        names = set()
        if fd.recv_mode in ('val', 'ref', 'mut'):
            rt = fd.recv_type if fd.recv_mode == 'val' else ('ref', fd.recv_type, fd.recv_mode == 'mut')
            fd.params.append((fd.recv_name, rt, False, a.where))
            names.add(fd.recv_name)
        for n, pt, isvar, w in a.params:
            if n in names:
                raise CompileError(f"parameter {n} is declared twice", w)
            names.add(n)
            t = self.resolve_type(pt, mod, env, 'param', w)
            if isvar and t[0] in ('ref', 'slice'):
                raise CompileError(f"var parameter {n} cannot be a borrow", w)
            fd.params.append((n, t, isvar, w))
        if a.ret:
            fd.ret = self.resolve_type(a.ret, mod, env, 'ret')
        if a.name == 'drop' and fd.owner is not None:
            if fd.recv_mode != 'mut' or len(fd.params) != 1 or fd.ret != VOID or a.tparams:
                raise CompileError("a drop method takes `mut &` receiver and nothing else", a.where)
            if isinstance(fd.owner, str):
                raise CompileError("primitive types cannot have a drop method", a.where)
            fd.owner.drop = fd
        if fd.ret[0] in ('ref', 'slice'):
            bs = [p for p in fd.params if p[1][0] in ('ref', 'slice')]
            if len(bs) != 1:
                raise CompileError(f"{fd.label} returns a borrow, so it needs exactly one borrowed "
                                   f"parameter, not {len(bs)}", a.where)
            if fd.ret[2] and not bs[0][1][2]:
                raise CompileError(f"{fd.label} returns a mut borrow, so its borrowed parameter must "
                                   "be mut &", a.where)
            fd.bparam = bs[0][0]

    def resolve_global(self, g):
        a = g.ast
        g.ty = self.resolve_type(a.ty, g.module, {}, 'global', a.where)
        if not is_copy(g.ty) and not a.once:
            raise CompileError(f"global {g.name} has the owned type {tstr(g.ty)}; globals must have "
                               f"copy types (or be set-once: var {g.name}: once {tstr(g.ty)})", a.where)

    # ---- constants

    def const_value(self, c):
        if c.value is None:
            if c.busy:
                raise CompileError(f"constant {c.name} refers to itself", c.where)
            c.busy = True
            t = self.resolve_type(c.ast.ty, c.module, {}, 'global', c.where) if c.ast.ty else None
            if t is not None and t[0] in ('arr', 'inst'):     # a constant table, or a record/variant constant
                et = t[1] if t[0] == 'arr' else t
                if not is_copy(et):
                    raise CompileError(f"const {c.name}: {tstr(et)} is not a copy type", c.where)
                c.table, c.value, c.busy = t, (t, None, 'table'), False
                return c.value
            if t is None and c.ast.e.kind in ('arrlit', 'lit'):
                raise CompileError(f"const {c.name} needs a declared type", c.where)
            try:
                v = self.fold(c.ast.e, c.module, t)
            except CompileError as e:
                raise CompileError(f"the initializer of const {c.name} is not constant ({e.msg})", c.where)
            if t is not None:
                v = (t, self.const_as(v, t, c.where), 'typed')
            c.value = v
            c.busy = False
        return c.value

    def cfit(self, v, where):
        """Check that a folded number fits its type (an untyped integer: 128 bits)."""
        ty, x, kind = v
        if kind == 'int':
            lo, hi = RANGES[ty[1]] if ty else (-2**127, 2**128 - 1)
            if not lo <= x <= hi:
                raise CompileError(f"constant {x} does not fit {tstr(ty) if ty else 'an integer'} (overflow)",
                                   where)
        elif kind == 'float':
            try:
                x = float(x)
            except OverflowError:
                x = float('inf')
            if x != x or x in (float('inf'), float('-inf')):
                raise CompileError("float constant out of range (overflow)", where)
            v = (ty, x, kind)
        return v

    def cto(self, v, t, where):
        """A folded value v as the type t (an untyped literal or constant takes the type)."""
        ty, x, kind = v
        if ty == t:
            return v
        if ty is None and t == FLOAT and kind in ('int', 'frac'):
            return self.cfit((FLOAT, x, 'float'), where)
        if ty is None and t[0] == 'prim' and t[1] in INTS and kind == 'int':
            return self.cfit((t, x, 'int'), where)
        raise CompileError(f"a {tstr(ty) if ty else kind} constant cannot be {tstr(t)}", where)

    def const_as(self, v, t, where):
        """Convert a folded value (type-or-None, pyvalue, kind) to the type t."""
        if (t == BOOL and v[2] == 'bool') or (t == STR and v[2] == 'str'):
            return v[1]
        return self.cto(v, t, where)[1]

    def fold(self, e, mod, t=None):
        """Fold a constant expression; t is the declared type (its arithmetic is used)."""
        k = e.kind
        if t is not None and (t[0] != 'prim' or t[1] not in INTS + ('float',)):
            t = None
        if k in ('int', 'char'):
            v = (None if k == 'int' else BYTE, e.v, 'int')
            return self.cto(v, t, e.where) if t and k == 'int' else v
        if k == 'frac':
            return self.cto((None, e.v, 'frac'), t, e.where) if t == FLOAT else (None, e.v, 'frac')
        if k == 'str':
            return (STR, e.v, 'str')
        if k == 'bool':
            return (BOOL, e.v, 'bool')
        if k in ('name', 'field'):
            if k == 'name':
                d = self.lookup(mod, e.v)
            elif e.e.kind == 'name' and e.e.v in mod.imports:
                d = self.lookup_in(mod, mod.imports[e.e.v], e.name, e.where)
            else:
                d = None
            if not isinstance(d, Const):
                raise CompileError(f"{e.v if k == 'name' else e.name} is not a constant", e.where)
            v = self.const_value(d)
            if v[2] == 'table':
                raise CompileError(f"{d.name} is a constant table", e.where)
            if v[2] == 'typed':
                return (v[0], v[1], 'float' if v[0] == FLOAT else 'int' if v[0][1] in INTS else v[0][1])
            return self.cto(v, t, e.where) if t and v[2] in ('int', 'frac') else v
        if k == 'un':
            ty, x, kind = self.fold(e.e, mod, t)
            if e.op == '!' and kind == 'bool':
                return (ty, not x, kind)
            if e.op == '~' and kind == 'int':
                return (ty, x ^ (2**BITS[ty[1]] - 1) if ty and ty[1][0] in 'ub' else ~x, kind)
            if e.op == '-' and kind in ('int', 'float'):
                return self.cfit((ty, -x, kind), e.where)
            if e.op == '-' and kind == 'frac':
                return (ty, x[1:] if x.startswith('-') else '-' + x, kind)
        if k == 'bin':
            import operator as o
            cmp = {'==': o.eq, '!=': o.ne, '<': o.lt, '<=': o.le, '>': o.gt, '>=': o.ge}
            sub = None if e.op in cmp or e.op in ('&&', '||') else t
            a = self.fold(e.l, mod, sub)
            b = self.fold(e.r, mod, None if e.op in ('<<', '>>') else sub)
            if e.op in ('<<', '>>'):
                if a[2] != 'int' or b[2] != 'int':
                    raise CompileError(f"{e.op} needs integer constants", e.where)
                w = BITS[a[0][1]] if a[0] else 128
                if not 0 <= b[1] < w:
                    raise CompileError(f"shift count {b[1]} out of range in a constant", e.where)
                if e.op == '>>':
                    return (a[0], a[1] >> b[1], 'int')
                r = a[1] << b[1]
                if a[0]:
                    r &= 2**w - 1
                    if a[0][1][0] not in 'ub' and r >= 2**(w - 1):
                        r -= 2**w
                return self.cfit((a[0], r, 'int'), e.where)
            if a[0] is None and b[0] is not None:
                a = self.cto(a, b[0], e.where)
            elif b[0] is None and a[0] is not None:
                b = self.cto(b, a[0], e.where)
            if a[2] != b[2] or a[0] != b[0]:
                raise CompileError(f"operator {e.op} needs operands of the same type", e.where)
            ty, x, y, kind = a[0], a[1], b[1], a[2]
            ar = {'+': o.add, '-': o.sub, '*': o.mul}
            if e.op in cmp and kind != 'frac':
                return (BOOL, cmp[e.op](x, y), 'bool')
            if kind in ('int', 'float') and e.op in ('/', '%') and y == 0:
                raise CompileError("division by zero in a constant", e.where)
            if kind == 'int':
                if e.op in ar:
                    return self.cfit((ty, ar[e.op](x, y), kind), e.where)
                if e.op in ('/', '%'):
                    q = abs(x) // abs(y) * (1 if (x >= 0) == (y >= 0) else -1)
                    return self.cfit((ty, q if e.op == '/' else x - q * y, kind), e.where)
                bit = {'&': o.and_, '|': o.or_, '^': o.xor}
                if e.op in bit:
                    return (ty, bit[e.op](x, y), kind)
            if kind == 'float' and e.op in ('+', '-', '*', '/'):
                return self.cfit((ty, ar[e.op](x, y) if e.op != '/' else x / y, kind), e.where)
            if kind == 'str' and e.op == '+':
                return (ty, x + y, kind)
            if kind == 'bool' and e.op in ('&&', '||'):
                return (ty, (x and y) if e.op == '&&' else (x or y), kind)
        raise CompileError("not a constant expression", e.where)

    # ---- methods and constraints

    def methods_of(self, t):
        if t[0] == 'prim':
            return self.prim_methods.get(t[1], {})
        if t[0] == 'inst':
            return t[1].methods
        return {}

    def iface_members(self, tp_t, name):
        """Members called `name` that a type parameter's constraints promise."""
        out = []
        for c in tp_t[1].cons:
            env = decl_env(c)
            env['Self'] = tp_t
            for m in c[1].members:
                if m.name == name:
                    out.append((m.mode, [subst(p, env) for p in m.params], subst(m.ret, env), c))
        return out

    def satisfies(self, t, con, mod):
        if con == 'copy':
            return None if is_copy(t) else "it is not a copy type"
        iface = con[1]
        env = decl_env(con)
        env['Self'] = t
        for m in iface.members:
            want_ps = [subst(p, env) for p in m.params]
            want_r = subst(m.ret, env)
            want_mode = m.mode or 'assoc'
            if t[0] == 'tp':
                cands = [(mode or 'assoc', ps, r) for mode, ps, r, _ in self.iface_members(t, m.name)]
            else:
                fd = self.methods_of(t).get(m.name)
                if fd is None or (t[0] == 'inst' and fd is t[1].drop):
                    cands = []
                elif not (fd.pub or fd.module is mod):
                    return f"its method '{m.name}' is not public"
                else:
                    tps = set(fd.tps)
                    bind = {}
                    if not unify(fd.recv_type, t if t[0] != 'tp' else t, bind, tps) or len(bind) != len(tps):
                        cands = []
                    else:
                        ps = [subst(p, bind) for _, p, _, _ in fd.params]
                        if fd.recv_mode in ('val', 'ref', 'mut'):
                            ps = ps[1:]
                        cands = [(fd.recv_mode, ps, subst(fd.ret, bind))]
                        for tp in fd.tps:
                            for c in tp.cons:
                                if self.satisfies(bind[tp], subst(c, bind), mod):
                                    cands = []
                            if tp.copy and not is_copy(bind[tp]):
                                cands = []
            if not cands:
                return f"it has no method '{m.name}'"
            ok = False
            for mode, ps, r in cands:
                if (mode == want_mode or (want_mode == 'ref' and mode == 'val' and is_copy(t))) \
                        and ps == want_ps and r == want_r:
                    ok = True
            if not ok:
                mode, ps, r = cands[0]
                if mode != want_mode:
                    return f"its method '{m.name}' has the wrong receiver mode"
                return f"its method '{m.name}' has the wrong signature"
        return None

    def check_inline_cycle(self, d):
        """A value type may not contain itself (directly or through other value types)."""
        def walk(t, path):
            if t[0] != 'inst' or not t[1].value:
                return
            if t[1] is d and path:
                raise CompileError(f"value type {d.name} contains itself (an infinite type)", d.where)
            if t in path or len(path) > 40:
                return
            for _, ft in field_types(t):
                walk(ft, path + [t])
        walk(('inst', d, tuple(('tp', p) for p in d.tparams)), [])
