"""--dump-ast: a canonical, line-oriented dump of a parsed module (ref/README.md, "AST dump").

One node per line, indented two spaces per depth. A line is `label: body`, where
the label is the field name in the parent (`module` for the root) or the index
in a list, and the body is `-` (absent), `[n]` (a list; its n items follow,
labelled 0..n-1), or a node: `kind line scalar=value...`, its child fields on
the lines after it in a fixed order. The line is `-` for the type and pattern
tuples that carry none. Scalars: names bare, `true`/`false`, `-` for none,
integers in decimal, string values quoted (see q). The self-hosted compiler
prints the same text (compiler/dump.obx).
"""


def q(b):
    """A quoted string: printable ASCII as is except backslash and quote, other bytes as \\xHH."""
    if isinstance(b, str):
        b = b.encode('utf-8')
    out = ['"']
    for c in b:
        if c == 92:
            out.append('\\\\')
        elif c == 34:
            out.append('\\"')
        elif 32 <= c < 127:
            out.append(chr(c))
        else:
            out.append('\\x%02x' % c)
    out.append('"')
    return ''.join(out)


def flag(b):
    return 'true' if b else 'false'


def opt(v):
    return '-' if v is None else v


def names(xs):
    return '-' if xs is None else '[' + ','.join(xs) + ']'


def line(where):
    return where.rsplit(':', 1)[1]


class Dumper:
    def __init__(self):
        self.out = []

    def put(self, depth, label, body):
        self.out.append('  ' * depth + label + ': ' + body)

    def lst(self, depth, label, items, each):
        self.put(depth, label, f'[{len(items)}]')
        for i, x in enumerate(items):
            each(depth + 1, str(i), x)

    def opt_node(self, depth, label, x, each):
        if x is None:
            self.put(depth, label, '-')
        else:
            each(depth, label, x)

    # ---- declarations

    def module(self, fname, imports, decls):
        self.out.append('module: ' + fname)
        self.lst(1, 'imports', imports,
                 lambda d, l, n: self.put(d, l, f"import {line(n.where)} path={q(n.path)} alias={opt(n.alias)}"))
        self.lst(1, 'decls', decls, self.decl)
        return self.out

    def decl(self, d, l, n):
        w, k = line(n.where), n.kind
        if k == 'type':
            head = f"type {w} name={n.name} pub={flag(n.pub)} form={n.form}"
            if n.form in ('record', 'closed'):
                head += f" value={flag(n.value)}"
            self.put(d, l, head)
            self.lst(d + 1, 'tparams', n.tparams, self.tparam)
            if n.form == 'record':
                self.lst(d + 1, 'fields', n.fields, self.field)
            elif n.form == 'closed':
                self.lst(d + 1, 'variants', n.variants, self.variant)
            elif n.form == 'interface':
                self.lst(d + 1, 'members', n.members, self.member)
            else:
                self.ty(d + 1, 'target', n.target)
        elif k == 'fn':
            cname = q(n.cname) if n.extern is not None else '-'
            ext = q(n.extern) if n.extern is not None else '-'
            self.put(d, l, f"fn {w} name={n.name} pub={flag(n.pub)} extern={ext} cname={cname}")
            self.opt_node(d + 1, 'recv', n.recv, self.recv)
            self.lst(d + 1, 'tparams', n.tparams, self.tparam)
            self.lst(d + 1, 'params', n.params, self.param)
            self.opt_node(d + 1, 'ret', n.ret, self.ty)
            self.opt_node(d + 1, 'body', n.body, self.stmt)
        elif k == 'const':
            self.put(d, l, f"const {w} name={n.name} pub={flag(n.pub)}")
            self.opt_node(d + 1, 'ty', n.ty, self.ty)
            self.expr(d + 1, 'e', n.e)
        else:
            self.put(d, l, f"global {w} name={n.name} pub={flag(n.pub)} once={flag(n.once)}")
            self.ty(d + 1, 'ty', n.ty)
            self.opt_node(d + 1, 'e', n.e, self.expr)

    def tparam(self, d, l, tp):
        name, cons, w = tp[-3:]
        self.put(d, l, f"tparam {line(w)} name={name}")
        self.lst(d + 1, 'cons', cons, self.ty)

    def field(self, d, l, f):
        n, t, pub = f
        self.put(d, l, f"field - name={n} pub={flag(pub)}")
        self.ty(d + 1, 'ty', t)

    def variant(self, d, l, v):
        vn, fs, named, w = v
        self.put(d, l, f"variant {line(w)} name={vn} named={flag(named)}")
        self.lst(d + 1, 'fields', fs, self.field)

    def member(self, d, l, m):
        self.put(d, l, f"member {line(m.where)} name={m.name} mode={opt(m.mode)}")
        self.lst(d + 1, 'params', m.params, self.param)
        self.opt_node(d + 1, 'ret', m.ret, self.ty)

    def recv(self, d, l, r):
        self.put(d, l, f"recv - name={opt(r[0])}")
        self.ty(d + 1, 'ty', r[1])

    def param(self, d, l, p):
        n, t, isvar, w = p
        self.put(d, l, f"param {line(w)} name={n} var={flag(isvar)}")
        self.ty(d + 1, 'ty', t)

    # ---- types (tuples)

    def ty(self, d, l, t):
        k = t if isinstance(t, str) else t[0]
        if k == 'copy':
            self.put(d, l, "copy -")
        elif k in ('ref', 'slice'):
            self.put(d, l, f"{k} - mut={flag(t[2])}")
            self.ty(d + 1, 't', t[1])
        elif k == 'arr':
            self.put(d, l, "arr -")
            self.ty(d + 1, 't', t[1])
        elif k == 'never':
            self.put(d, l, f"never {line(t[1])}")
        elif k == 'fnt':
            self.put(d, l, f"fnt {line(t[3])}")
            self.lst(d + 1, 'params', t[1], self.ty)
            self.opt_node(d + 1, 'ret', t[2], self.ty)
        elif k == 'name':
            self.put(d, l, f"name {line(t[3])} parts={names(t[1])}")
            self.lst(d + 1, 'args', t[2], self.ty)
        elif k == 'tparam':
            self.tparam(d, l, t)
        else:
            raise AssertionError(t)

    # ---- statements

    def stmt(self, d, l, s):
        w, k = line(s.where), s.kind
        if k == 'block':
            self.put(d, l, f"block {w}")
            self.lst(d + 1, 'stmts', s.stmts, self.stmt)
        elif k == 'let':
            self.put(d, l, f"let {w} name={s.name} mutable={flag(s.mutable)}")
            self.opt_node(d + 1, 'ty', s.ty, self.ty)
            self.expr(d + 1, 'e', s.e)
        elif k == 'destructure':
            self.put(d, l, f"destructure {w} mutable={flag(s.mutable)}")
            self.ty(d + 1, 'ty', s.ty)
            self.lst(d + 1, 'fields', s.fields,
                     lambda d2, l2, f: self.put(d2, l2, f"fieldpat {line(f[2])} field={f[0]} bind={f[1]}"))
            self.expr(d + 1, 'e', s.e)
        elif k == 'if':
            self.put(d, l, f"if {w}")
            self.expr(d + 1, 'cond', s.cond)
            self.stmt(d + 1, 'then', s.then)
            self.opt_node(d + 1, 'els', s.els, self.stmt)
        elif k == 'while':
            self.put(d, l, f"while {w}")
            self.expr(d + 1, 'cond', s.cond)
            self.stmt(d + 1, 'body', s.body)
        elif k == 'for':
            self.put(d, l, f"for {w} name={s.name}")
            self.expr(d + 1, 'a', s.a)
            self.opt_node(d + 1, 'b', s.b, self.expr)
            self.stmt(d + 1, 'body', s.body)
        elif k == 'return':
            self.put(d, l, f"return {w}")
            self.opt_node(d + 1, 'e', s.e, self.expr)
        elif k in ('break', 'continue'):
            self.put(d, l, f"{k} {w}")
        elif k == 'match':
            self.put(d, l, f"match {w}")
            self.expr(d + 1, 'e', s.e)
            self.lst(d + 1, 'arms', s.arms, self.arm)
        elif k == 'assign':
            self.put(d, l, f"assign {w} op={opt(s.op)}")
            self.expr(d + 1, 'target', s.target)
            self.expr(d + 1, 'e', s.e)
        elif k == 'expr':
            self.put(d, l, f"expr {w}")
            self.expr(d + 1, 'e', s.e)
        else:
            raise AssertionError(k)

    def arm(self, d, l, a):
        vpath = getattr(a, 'vpath', None)
        self.put(d, l, f"arm {line(a.where)} variant={opt(a.variant)} vpath={names(vpath)} binds={names(a.binds)}")
        if a.lits is None:
            self.put(d + 1, 'lits', '-')
        else:
            self.lst(d + 1, 'lits', a.lits, self.lit)
        (self.stmt if a.body.kind == 'block' else self.expr)(d + 1, 'body', a.body)

    def lit(self, d, l, x):
        k, v = x
        self.put(d, l, f"{k} - v={q(v) if k == 'str' else names(v) if k == 'name' else v}")

    # ---- expressions

    def expr(self, d, l, e):
        w, k = line(e.where), e.kind
        if k == 'int':
            self.put(d, l, f"int {w} v={e.v} neg={flag(getattr(e, 'neg', False))}")
        elif k in ('char', 'frac', 'name'):
            self.put(d, l, f"{k} {w} v={e.v}")
        elif k == 'str':
            self.put(d, l, f"str {w} v={q(e.v)}")
        elif k == 'bool':
            self.put(d, l, f"bool {w} v={flag(e.v)}")
        elif k == 'arrlit':
            self.put(d, l, f"arrlit {w}")
            self.lst(d + 1, 'items', e.items, self.expr)
        elif k == 'closure':
            self.put(d, l, f"closure {w}")
            self.lst(d + 1, 'params', e.params, self.cparam)
            self.opt_node(d + 1, 'ret', e.ret, self.ty)
            self.stmt(d + 1, 'body', e.body)
        elif k == 'bin':
            self.put(d, l, f"bin {w} op={e.op}")
            self.expr(d + 1, 'l', e.l)
            self.expr(d + 1, 'r', e.r)
        elif k == 'un':
            self.put(d, l, f"un {w} op={e.op}")
            self.expr(d + 1, 'e', e.e)
        elif k == 'borrow':
            self.put(d, l, f"borrow {w} mut={flag(e.mut)}")
            self.expr(d + 1, 'e', e.e)
        elif k == 'try':
            self.put(d, l, f"try {w}")
            self.expr(d + 1, 'e', e.e)
        elif k == 'field':
            self.put(d, l, f"field {w} name={e.name}")
            self.expr(d + 1, 'e', e.e)
        elif k == 'sub':
            self.put(d, l, f"sub {w}")
            self.expr(d + 1, 'e', e.e)
            self.opt_node(d + 1, 'lo', e.lo, self.expr)
            self.opt_node(d + 1, 'hi', e.hi, self.expr)
        elif k == 'index':
            self.put(d, l, f"index {w}")
            self.expr(d + 1, 'e', e.e)
            self.lst(d + 1, 'items', e.items, self.expr)
        elif k == 'call':
            self.put(d, l, f"call {w}")
            self.expr(d + 1, 'fn', e.fn)
            self.lst(d + 1, 'args', e.args, self.expr)
        elif k == 'lit':
            self.put(d, l, f"lit {w}")
            self.expr(d + 1, 'ty', e.ty)
            self.lst(d + 1, 'inits', e.inits, self.init)
        elif k == 'ifx':
            self.put(d, l, f"ifx {w}")
            self.expr(d + 1, 'cond', e.cond)
            self.expr(d + 1, 'then', e.then)
            self.expr(d + 1, 'els', e.els)
        elif k == 'matchx':
            self.put(d, l, f"matchx {w}")
            self.expr(d + 1, 'e', e.e)
            self.lst(d + 1, 'arms', e.arms, self.arm)
        elif k == 'type':
            self.put(d, l, f"targ {w}")
            self.ty(d + 1, 'ty', e.ty)
        else:
            raise AssertionError(k)

    def cparam(self, d, l, p):
        n, t, w = p
        self.put(d, l, f"cparam {line(w)} name={n}")
        self.opt_node(d + 1, 'ty', t, self.ty)

    def init(self, d, l, x):
        n, e, w = x
        self.put(d, l, f"init {line(w)} name={n}")
        self.expr(d + 1, 'e', e)


def dump_ast(fname, imports, decls):
    return '\n'.join(Dumper().module(fname, imports, decls)) + '\n'


# ---------------------------------------------------------------- --dump-types

def dump_types(prog):
    """--dump-types: what sema computes before bodies are checked (ref/README.md, "Types dump"),
    for every module in load order, then the program-wide tables. Types are written in full:
    a declared type by its C name (unique), a type parameter by its name and a number
    (`T#3`, numbered in the order the dump first meets them, so identity is visible)."""
    import os
    from sema import TypeDecl, FnDecl, Const, Global, VOID, is_copy
    out, tpn = [], {}

    def tp(p):
        if id(p) not in tpn:
            tpn[id(p)] = (len(tpn) + 1, p)      # keep p alive, so its id is not reused
        return f"{p.name}#{tpn[id(p)][0]}"

    def ts(t):
        k = t[0]
        if k == 'prim':
            return t[1]
        if k == 'arr':
            return '[]' + ts(t[1])
        if k == 'ref':
            return ('mut &' if t[2] else '&') + ts(t[1])
        if k == 'slice':
            return ('mut &[' if t[2] else '&[') + ts(t[1]) + ']'
        if k in ('fn', 'clo'):
            r = '' if t[2] == VOID else ': ' + ts(t[2])
            return ('&' if k == 'clo' else '') + 'fn(' + ', '.join(ts(p) for p in t[1]) + ')' + r
        if k == 'tp':
            return tp(t[1])
        if k == 'self':
            return 'Self'
        return t[1].cname + ('[' + ', '.join(ts(a) for a in t[2]) + ']' if t[2] else '')

    def tparam(ind, p):
        out.append(f"{ind}tparam {tp(p)} copy={flag(p.copy)} cons=[{', '.join(ts(c) for c in p.cons)}]")

    def val(v):
        ty, x, kind = v
        if kind == 'table':
            return f"table {ts(ty)}"
        k = kind
        if kind == 'typed':
            k = 'float' if ty == ('prim', 'float') else ty[1] if ty[1] in ('str', 'bool') else 'int'
        s = repr(float(x)) if k == 'float' else q(x) if k == 'str' else flag(x) if k == 'bool' else str(x)
        return f"{kind} {ts(ty) if ty else '-'} {s}"

    for m in prog.modules.values():
        out.append(f"module {m.name} file={os.path.basename(m.path)} cprefix={m.cprefix} std={flag(m.is_std)}")
        for alias, sub in m.imports.items():
            out.append(f"  import {alias} {sub.cprefix}")
        out.append("  scope" + ''.join(' ' + n for n in m.scope))
        for d in m.scope.values():
            w = line(d.where)
            if isinstance(d, TypeDecl):
                cp = drop = '-'
                if d.form in ('record', 'closed'):
                    cp = flag(is_copy(('inst', d, tuple(('tp', p) for p in d.tparams))))
                    drop = d.drop.label if d.drop else '-'
                out.append(f"  type {d.name} {w} pub={flag(d.pub)} form={d.form} value={flag(d.value)} "
                           f"cname={d.cname} copy={cp} drop={drop}")
                for p in d.tparams:
                    tparam('    ', p)
                for n, ft, pub in d.fields:
                    out.append(f"    field {n} pub={flag(pub)} {ts(ft)}")
                for v in d.variants:
                    out.append(f"    variant {v.index} {v.name} {line(v.where)} named={flag(v.named)}")
                    for n, ft, _ in v.fields:
                        out.append(f"      field {n} {ts(ft)}")
                for mb in d.members:
                    out.append(f"    member {mb.name} {line(mb.where)} mode={opt(mb.mode)} "
                               f"params=[{', '.join(ts(p) for p in mb.params)}] ret={ts(mb.ret)}")
                if d.form == 'alias':
                    out.append(f"    target {ts(d.target)}")
                if d.methods:
                    out.append("    methods" + ''.join(' ' + n for n in d.methods))
            elif isinstance(d, Const):
                out.append(f"  const {d.name} {w} pub={flag(d.pub)} cname={d.cname} {val(d.value)}")
            elif isinstance(d, Global):
                out.append(f"  global {d.name} {w} pub={flag(d.pub)} once={flag(d.ast.once)} cname={d.cname} "
                           f"type={ts(d.ty)}")
        for fd in m.fns:
            ext = '-' if fd.extern is None else q(fd.extern)
            cname = '-' if fd.extern is None else q(fd.ast.cname)
            rt = '-' if fd.recv_type is None else ts(fd.recv_type)
            out.append(f"  fn {fd.label} {line(fd.where)} pub={flag(fd.pub)} extern={ext} cname={cname} "
                       f"recv={opt(fd.recv_mode)} recvname={opt(fd.recv_name)} recvtype={rt}")
            for p in fd.tps:
                tparam('    ', p)
            for n, t, isvar, pw in fd.params:
                out.append(f"    param {n} {line(pw)} var={flag(isvar)} {ts(t)}")
            out.append(f"    ret {ts(fd.ret)} bparam={opt(fd.bparam)}")
    for p, ms in prog.prim_methods.items():
        if ms:
            out.append(f"prim {p} methods" + ''.join(' ' + n for n in ms))
    for n, v in prog.prelude.items():
        out.append(f"prelude {n} {v.decl.cname}.{v.name}")
    for c, (sig, fd) in prog.externs.items():
        out.append(f"extern {c} {fd.module.cprefix}.{fd.name}")
    return '\n'.join(out) + '\n'
