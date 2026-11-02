"""The emitter: renders checked templates once per instance, from main (SPEC 6.4).

Holes in a template name what depends on type arguments; rendering substitutes
the instance's type arguments and asks for C types, drop and dup glue and
callee instances, which are themselves rendered later (a worklist).
"""
import re
from syntax import CompileError
from sema import INTS, VOID, STR, subst, unify, depth, decl_env, field_types, tstr
from check import CTYPES, cstr

HOLE = re.compile('\x01(\\d+)\x02')
MAX_DEPTH, MAX_INSTANCES = 24, 20000


def pod(t):
    k = t[0]
    if k == 'prim':
        return t[1] != 'str'
    if k in ('fn', 'ref', 'slice'):
        return True
    if k == 'arr':
        return False
    d = t[1]
    return d.value and d.drop is None and all(pod(ft) for _, ft in field_types(t))


class Emitter:
    def __init__(self, prog):
        self.prog = prog
        self.names = {}          # inst/fn type -> C name
        self.structs = []        # inst types needing a struct definition
        self.fntypes = []        # C typedef lines for function types
        self.insts, self.work = {}, []
        self.glue, self.gluework = {}, []
        self.strs = {}
        self.protos, self.bodies = [], []

    # ---- types

    def ctype(self, t):
        k = t[0]
        if k == 'prim':
            return CTYPES[t[1]]
        if k == 'arr':
            return 'obx_arr'
        if k == 'ref':
            return self.ctype(t[1]) + '*'
        if k == 'slice':
            return 'obx_slice'
        if k in ('tp', 'self'):
            raise CompileError(f"internal error: unsubstituted type {tstr(t)}")
        if t not in self.names:
            if depth(t) > MAX_DEPTH + 8:
                ds, x = [], t
                while x[0] in ('inst', 'arr', 'ref') and (x[0] != 'inst' or x[2]):
                    ds += [x[1]] if x[0] == 'inst' else []
                    x = x[2][0] if x[0] == 'inst' else x[1]
                d = next((y for y in ds if not y.module.is_std), ds[0] if ds else None)
                raise CompileError(f"instantiation of type {d.name if d else tstr(t)[:40]} does not terminate: "
                                   f"its type arguments grow without bound ({tstr(t)[:80]}...)",
                                   d.where if d else '')
            if k == 'fn':
                ps = ', '.join(self.ctype(p) for p in t[1]) or 'void'
                r = self.ctype(t[2])
                n = f"fn{len(self.names)}"
                self.fntypes.append(f"typedef {r} (*{n})({ps});")
            else:
                n = f"S{len(self.names)}_{t[1].cname}"
                self.structs.append(t)
            self.names[t] = n
        n = self.names[t]
        return n if k == 'fn' or t[1].value else n + '*'

    def struct_body(self, t):
        d = t[1]
        env = decl_env(t)

        def fields(fs):
            out = ' '.join(f"{self.ctype(subst(ft, env))} f_{n};" for n, ft, _ in fs)
            return out or 'char _e;'
        if d.form == 'record':
            return f"struct {self.names[t]} {{ {fields(d.fields)} }};"
        us = ' '.join(f"struct {{ {fields(v.fields)} }} V_{v.name};" for v in d.variants)
        return f"struct {self.names[t]} {{ int64_t tag; union {{ {us} char _e; }} u; }};"

    # ---- glue

    def drop_c(self, t, c):
        if pod(t):
            return '((void)0)'
        if t == STR:
            return f"rt_str_drop({c})"
        return f"{self.glue_fn('drop', t)}({c})"

    def dup_c(self, t, c):
        if pod(t):
            return c
        if t == STR:
            return f"rt_str_dup({c})"
        return f"{self.glue_fn('dup', t)}({c})"

    def flat(self, t):
        """Dropping t frees memory only (no drop method, no boxed value): it may run inline."""
        if t[0] != 'inst':
            return t[0] != 'arr' or self.flat(t[1])
        return t[1].value and t[1].drop is None and all(self.flat(ft) for _, ft in field_types(t))

    def fdrop(self, t, loc):
        """Drop the field or element at loc inside a dbody action: now if flat, else pushed."""
        if pod(t):
            return ''
        if t == STR:
            return f"rt_str_drop({loc});"
        b = self.glue_fn('dbody', t)
        return f"{b}(&{loc});" if self.flat(t) else f"rt_dpush({b}, &{loc});"

    def glue_fn(self, kind, t):
        key = (kind, t)
        if key not in self.glue:
            self.glue[key] = f"{kind}{len(self.glue)}"
            self.gluework.append(key)
        return self.glue[key]

    def make_glue(self, kind, t):
        n, ct = self.glue[(kind, t)], self.ctype(t)
        body = []
        if kind == 'drop':
            self.protos.append(f"static void {n}({ct} v);")
            self.bodies.append(f"static void {n}({ct} v) {{ rt_drun({self.glue_fn('dbody', t)}, &v); }}")
            return
        if kind == 'dbody':
            # Drop as actions on an explicit stack (rt_drun): no C recursion per boxed value, and
            # the order of SPEC 9.2 is kept (fields last to first, elements first to last).
            self.protos.append(f"static void {n}(void* p);")
            if t[0] == 'arr':
                et, ec = t[1], self.ctype(t[1])
                body = ["obx_arr* a = p;", "if (a->data) rt_dpush(rt_free_act, a->data);"]
                if not pod(et):
                    if self.flat(et):
                        body.append(f"for (int64_t i = 0; i < a->len; i++) {self.fdrop(et, f'(({ec}*)a->data)[i]')}")
                    else:
                        body.append(f"for (int64_t i = a->len - 1; i >= 0; i--) "
                                    f"rt_dpush({self.glue_fn('dbody', et)}, &(({ec}*)a->data)[i]);")
            else:
                d, env = t[1], decl_env(t)
                if d.value:
                    body = [f"{ct}* v = p;"] + ([f"{self.instance(d.drop, t[2])}(v);"] if d.drop else [])
                else:
                    body = ([f"{self.instance(d.drop, t[2])}(({ct}*)p);"] if d.drop else []) + \
                        [f"{ct} v = *({ct}*)p;", "rt_dpush(rt_free_act, v);"]
                if d.form == 'record':
                    body += [self.fdrop(ft, f"v->f_{fn}") for fn, ft in field_types(t)]
                else:
                    body.append("switch (v->tag) {")
                    for vr in d.variants:
                        ds = [self.fdrop(subst(ft, env), f"v->u.V_{vr.name}.f_{fn}") for fn, ft, _ in vr.fields]
                        body.append(f"case {vr.index}: {' '.join(ds)} break;")
                    body.append("}")
            self.bodies.append(f"static void {n}(void* p) {{\n  " + '\n  '.join(body) + "\n}")
            return
        self.protos.append(f"static {ct} {n}({ct} v);")
        d = t[1]
        if d.form == 'record':
            body = [f"v.f_{fn} = {self.dup_c(ft, f'v.f_{fn}')};" for fn, ft in field_types(t)]
        else:
            env = decl_env(t)
            body.append("switch (v.tag) {")
            for vr in d.variants:
                ds = []
                for fn, ft, _ in vr.fields:
                    f = f"v.u.V_{vr.name}.f_{fn}"
                    ds.append(f"{f} = {self.dup_c(subst(ft, env), f)};")
                body.append(f"case {vr.index}: {' '.join(ds)} break;")
            body.append("}")
        self.bodies.append(f"static {ct} {n}({ct} v) {{\n  " + '\n  '.join(body) + "\n  return v;\n}")

    # ---- holes

    def strlit(self, b, glob=False):
        if b not in self.strs:
            self.strs[b] = f"sl{len(self.strs)}"
        n = self.strs[b]
        return f"{{(obx_sblk*)&{n}b, 0, {len(b)}}}" if glob else n

    def render(self, text, holes, env):
        return HOLE.sub(lambda m: self.hole(holes[int(m.group(1))], holes, env), text)

    def hole(self, h, holes, env):
        k = h[0]
        if k == 'T':
            return self.ctype(subst(h[1], env))
        if k == 'drop':
            return self.drop_c(subst(h[1], env), self.render(h[2], holes, env))
        if k == 'dup':
            return self.dup_c(subst(h[1], env), self.render(h[2], holes, env))
        if k in ('str', 'gstr'):
            return self.strlit(h[1], k == 'gstr')
        if k == 'fn':
            return self.instance(h[1], tuple(subst(a, env) for a in h[2]))
        t = subst(h[1], env)
        fd = self.prog.methods_of(t).get(h[2])
        bind = {}
        if fd is None or not unify(fd.recv_type, t, bind, set(fd.tps)):
            raise CompileError(f"internal error: {tstr(t)} has no method {h[2]}")
        name = self.instance(fd, tuple(bind[tp] for tp in fd.tps))
        if k == 'iassoc':
            return name
        _, _, _, mode, recv, args = h
        recv = self.render(recv, holes, env)
        if mode == 'ref' and fd.recv_mode == 'val':
            recv = self.dup_c(t, f"(*{recv})")
        return f"{name}({', '.join([recv] + [self.render(a, holes, env) for a in args])})"

    # ---- instances

    def instance(self, fd, targs):
        key = (fd, targs)
        if key not in self.insts:
            for a in targs:
                if depth(a) > MAX_DEPTH:
                    raise CompileError(f"instantiation of {fd.label} does not terminate: its type arguments "
                                       f"grow without bound ({tstr(a)[:80]}...)", fd.where)
            if len(self.insts) > MAX_INSTANCES:
                raise CompileError(f"too many instances (instantiation of {fd.label} does not terminate)",
                                   fd.where)
            lab = re.sub(r'\W', '_', fd.label)
            self.insts[key] = f"f{len(self.insts)}_{fd.module.cprefix}_{lab}"
            self.work.append(key)
        return self.insts[key]

    def make_instance(self, fd, targs):
        name = self.insts[(fd, targs)]
        env = dict(zip(fd.tps, targs))
        ps = ', '.join(f"{self.ctype(subst(t, env))} p_{n}" for n, t, _, _ in fd.params) or 'void'
        head = f"static {self.ctype(subst(fd.ret, env))} {name}({ps})"
        self.protos.append(head + ';')
        lines, holes = fd.template
        body = '\n'.join(self.render(l, holes, env) for l in lines)
        self.bodies.append(f"{head} {{\n{body}\n}}")

    def extern_c(self, t):
        if t == STR:
            return 'const char*'
        return self.ctype(t)

    # ---- the program

    def program(self, main, rt):
        externs, libs = [], []
        seen = set()
        for m in self.prog.modules.values():
            for fd in m.fns:
                if fd.extern is not None and fd.name not in seen:
                    seen.add(fd.name)
                    ps = ', '.join(self.extern_c(t) for _, t, _, _ in fd.params) or 'void'
                    externs.append(f"extern {self.extern_c(fd.ret)} x_{fd.name}({ps}) __asm__(\"{fd.name}\");")
                    if fd.extern not in ('c', '') and fd.extern not in libs:
                        libs.append(fd.extern)
        entry = self.instance(main, ())
        globs, gdrops = [], []
        for m in self.prog.modules.values():
            for g in m.scope.values():
                if g.__class__.__name__ == 'Global':
                    c, holes = g.init
                    globs.append(f"static {self.ctype(g.ty)} {g.cname} = {self.render(c, holes, {})};")
                    if not pod(g.ty):
                        gdrops.insert(0, self.drop_c(g.ty, g.cname) + ';')
        while self.work or self.gluework:
            if self.work:
                self.make_instance(*self.work.pop())
            else:
                self.make_glue(*self.gluework.pop())
        i = 0
        while i < len(self.structs):           # field types may name more types
            self.struct_body(self.structs[i])
            i += 1
        order, done = [], set()

        def visit(t):
            if t in done:
                return
            done.add(t)
            for _, ft in field_types(t):
                while ft[0] == 'inst' and ft[1].value and ft not in done:
                    visit(ft)
                    break
            order.append(t)
        for t in self.structs:
            visit(t)
        out = [rt]
        out += [f"typedef struct {self.names[t]} {self.names[t]};" for t in self.structs]
        out += self.fntypes
        out += [self.struct_body(t) for t in order]
        for b, n in self.strs.items():
            out.append(f"static struct {{ int64_t rc; char data[{len(b) + 1}]; }} {n}b = {{-1, {cstr(b)}}};")
            out.append(f"static const obx_str {n} = {self.strlit(b, True)};")
        out += externs + globs + self.protos + self.bodies
        out.append(f"int main(int argc, char** argv) {{ rt_init(argc, argv); {entry}(); {' '.join(gdrops)} "
                   "rt_finish(); return 0; }")
        return '\n'.join(out) + '\n', libs
