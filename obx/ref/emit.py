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
DEEP = ('deep',)        # in Emitter.reach: a type too deep to follow (a non-terminating instantiation)


def pod(t):
    k = t[0]
    if k == 'prim':
        return t[1] != 'str'
    if k in ('fn', 'ref', 'slice', 'clo'):
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
        self.reaches = {}
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
        if k in ('slice', 'clo'):
            return 'obx_slice' if k == 'slice' else 'obx_clo'
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

    # Drop glue. A type is dropped by direct (recursive) C functions unless it is a chain type (it
    # lies on a cycle of the type graph of fields and elements, generic instances included, so its
    # values can nest without bound) or has a drop method. Those are dropped by actions on an
    # explicit stack (rt_dpush/rt_drun, SPEC 9.4); an action gets the object pointer of a boxed
    # value, the address of any other. The order is that of SPEC 9.2 either way.

    def reach(self, t):
        """The non-pod types reachable from the fields or elements of t (DEEP: too deep to tell)."""
        r = self.reaches.get(t)
        if r is None:
            r, todo = set(), self.kids(t)
            while todo:
                x = todo.pop()
                if x not in r:
                    r.add(x)
                    todo += self.kids(x) if depth(x) <= MAX_DEPTH + 8 else [DEEP]
            self.reaches[t] = r
        return r

    def kids(self, t):
        ks = [t[1]] if t[0] == 'arr' else [ft for _, ft in field_types(t)] if t[0] == 'inst' else []
        return [k for k in ks if not pod(k)]

    def chain(self, t):
        r = self.reach(t)
        return t in r or DEEP in r

    def quiet(self, t):
        """Dropping t runs no drop method and does not nest without bound: it may run at any time."""
        return not self.chain(t) and not any(x[0] == 'inst' and x[1].drop for x in self.reach(t) | {t})

    def listed(self, t):
        return self.chain(t) or t[0] == 'inst' and t[1].drop is not None

    def cases(self, t, acc, f):
        """f(fields) for the record t or each variant of the closed type t (in a switch on its tag);
        fields: (type, C place) pairs, pod ones left out, in declaration order."""
        d, env = t[1], decl_env(t)

        def us(fs, pre):
            return [(ft, f"{acc}{pre}f_{n}") for n, ft in ((n, subst(ft, env)) for n, ft, _ in fs) if not pod(ft)]
        if d.form == 'record':
            return f(us(d.fields, ''))
        return ([f"switch ({acc}tag) {{"] + [f"case {v.index}: {' '.join(f(us(v.fields, f'u.V_{v.name}.')))} break;"
                                            for v in d.variants] + ["}"])

    def push(self, t, loc):
        """Lines that push the drop of the value at loc, and whether the actions read loc's memory: a
        boxed value is pushed by pointer, a chain value type without a drop method by its parts."""
        if t[0] == 'inst' and not t[1].value:
            return [f"rt_dpush({self.glue_fn('act', t)}, {loc});"], False
        if t[0] == 'inst' and self.chain(t) and t[1].drop is None:
            refs = []

            def parts(fs):
                out = []
                for ft, l in fs:
                    ls, r = ([self.drop_c(ft, l) + ';'], False) if self.quiet(ft) else self.push(ft, l)
                    out += ls
                    refs.append(r)
                return out
            return self.cases(t, loc + '.', parts), any(refs)
        return [f"rt_dpush({self.glue_fn('act', t)}, &{loc});"], True

    def rest(self, t):
        """The body of an action that drops the fields or elements of t and frees its memory: what
        must not nest is pushed (fields in declaration order, so that they run last to first;
        elements last to first), the rest is dropped now; a container that a pushed action reads
        is freed by an action pushed first."""
        if t[0] == 'arr':
            et, ec = t[1], self.ctype(t[1])
            el = f"(({ec}*)a->data)[i]"
            if not self.chain(et):
                return ["obx_arr* a = p;", f"for (int64_t i = 0; i < a->len; i++) {self.drop_c(et, el)};",
                        "rt_arr_free(*a);"]
            ls, refs = self.push(et, el)
            loop = f"for (int64_t i = a->len - 1; i >= 0; i--) {{ {' '.join(ls)} }}"
            return ["obx_arr* a = p;"] + (["if (a->data) rt_dpush(rt_free_act, a->data);", loop] if refs
                                          else [loop, "rt_arr_free(*a);"])
        boxed, ct = not t[1].value, self.ctype(t)

        def seq(fs):
            # a field after the last pushed one is dropped now (they run first), as is a quiet one
            k = max([i + 1 for i, (ft, _) in enumerate(fs) if self.chain(ft)] + [0])
            out, refs = [], False
            for ft, loc in fs[:k]:
                ls, r = ([self.drop_c(ft, loc) + ';'], False) if self.quiet(ft) else self.push(ft, loc)
                out += ls
                refs |= r
            out += [self.drop_c(ft, loc) + ';' for ft, loc in reversed(fs[k:])]
            if boxed:
                out = ["rt_dpush(rt_free_act, v);"] + out if refs else out + ["rt_free(v);"]
            return out
        return [f"{ct}{'' if boxed else '*'} v = p;"] + self.cases(t, 'v->', seq)

    def glue_fn(self, kind, t):
        key = (kind, t)
        if key not in self.glue:
            self.glue[key] = f"gl{len(self.glue)}_{kind}"
            self.gluework.append(key)
        return self.glue[key]

    def make_glue(self, kind, t):
        n, ct = self.glue[(kind, t)], self.ctype(t)
        body = []
        if kind == 'tramp':     # a plain function seen as a closure: the environment is the function
            ps = [self.ctype(p) for p in t[1]]
            head = f"static {self.ctype(t[2])} {n}({', '.join(['void* e'] + [f'{p} a{i}' for i, p in enumerate(ps)])})"
            ret = '' if self.ctype(t[2]) == 'void' else 'return '
            self.protos.append(head + ';')
            self.bodies.append(f"{head} {{ {ret}(({ct})e)({', '.join(f'a{i}' for i in range(len(ps)))}); }}")
            return
        if kind == 'drop':
            boxed = t[0] == 'inst' and not t[1].value
            if self.listed(t):
                body = [f"rt_drun({self.glue_fn('act', t)}, {'v' if boxed else '&v'});"]
            elif t[0] == 'arr':
                el = f"(({self.ctype(t[1])}*)v.data)[i]"
                body = [f"for (int64_t i = 0; i < v.len; i++) {self.drop_c(t[1], el)};"] * (not pod(t[1]))
                body.append("rt_arr_free(v);")
            else:
                body = self.cases(t, 'v->' if boxed else 'v.',
                                  lambda fs: [self.drop_c(ft, loc) + ';' for ft, loc in reversed(fs)])
                body += ["rt_free(v);"] if boxed else []
            self.protos.append(f"static void {n}({ct} v);")
            self.bodies.append(f"static void {n}({ct} v) {{\n  " + '\n  '.join(body) + "\n}")
            return
        if kind in ('act', 'rest'):
            boxed = t[0] == 'inst' and not t[1].value
            self.protos.append(f"static void {n}(void* p);")
            if not self.listed(t):
                body = [f"{self.glue_fn('drop', t)}({'p' if boxed else f'*({ct}*)p'});"]
            elif kind == 'act' and t[0] == 'inst' and t[1].drop is not None:
                # The drop method runs first; the fields are dropped by a second action, pushed
                # before the call, so that what the method defers (ddrop) is dropped before them.
                body = [f"rt_dpush({self.glue_fn('rest', t)}, p);"] + ([f"{ct} v = p;"] if boxed else []) + \
                       [f"{self.instance(t[1].drop, t[2])}({'&v' if boxed else f'({ct}*)p'});"]
            else:
                body = self.rest(t)
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
        if k == 'ddrop':        # a drop when a drop method returns: onto the worklist (SPEC 9.4)
            t, c = subst(h[1], env), self.render(h[2], holes, env)
            if pod(t) or self.quiet(t):
                return self.drop_c(t, c)
            ls, refs = self.push(t, c)      # what reads c's memory is pushed with a heap copy of c
            return f"rt_ddefer({self.glue_fn('act', t)}, &{c}, sizeof({c}))" if refs else ' '.join(ls)
        if k == 'dup':
            return self.dup_c(subst(h[1], env), self.render(h[2], holes, env))
        if k in ('str', 'gstr'):
            return self.strlit(h[1], k == 'gstr')
        if k == 'fn':
            return self.instance(h[1], tuple(subst(a, env) for a in h[2]))
        if k == 'cl':
            return f"{env['@']}_c{h[1]}"
        if k == 'tramp':
            return self.glue_fn('tramp', subst(h[1], env))
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
        env['@'] = name
        ps = ', '.join(f"{self.ctype(subst(t, env))} p_{n}" for n, t, _, _ in fd.params) or 'void'
        head = f"static {self.ctype(subst(fd.ret, env))} {name}({ps})"
        self.protos.append(head + ';')
        lines, holes = fd.template
        body = '\n'.join(self.render(l, holes, env) for l in lines)
        self.bodies.append(f"{head} {{\n{body}\n}}")
        for k, ret, cps, ls in fd.lifted:      # its closures
            ps = ', '.join(['void* cv_'] + [f"{self.ctype(subst(t, env))} {c}" for c, t in cps])
            head = f"static {self.ctype(subst(ret, env))} {name}_c{k}({ps})"
            self.protos.append(head + ';')
            body = '\n'.join(self.render(l, holes, env) for l in ls)
            self.bodies.append(f"{head} {{\n  void** ce_ = cv_; (void)ce_;\n{body}\n}}")

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
                cn = fd.extern is not None and fd.ast.cname
                if cn and cn not in seen:
                    seen.add(cn)
                    ps = ', '.join(self.extern_c(t) for _, t, _, _ in fd.params) or 'void'
                    externs.append(f"extern {self.extern_c(fd.ret)} x_{cn}({ps}) __asm__(\"{cn}\");")
                    if fd.extern not in ('c', '') and fd.extern not in libs:
                        libs.append(fd.extern)
        entry = self.instance(main, ())
        # SPEC 5.2: at exit a module's set-once globals are dropped before those of the modules it
        # imports, each module's in reverse declaration order; a dropped one is marked (state 2) first.
        # Copy globals are dropped after them all (that runs no code), so no drop method sees them freed.
        globs, onces, copies, post = [], {}, [], []

        def visit(m):
            if m not in onces:
                onces[m] = []
                for sub in m.imports.values():
                    visit(sub)
                post.append(m)
        for m in [self.prog.std] + list(self.prog.modules.values()):
            visit(m)
        for m in post:
            for g in m.scope.values():
                if not hasattr(g, 'init'):
                    continue
                c, holes = g.init
                t = getattr(g, 'ty', None) or g.table
                if c is None:           # a set-once global: 0 empty, 1 set, 2 dropped
                    globs.append(f"static {self.ctype(t)} {g.cname}; static uint8_t {g.cname}_set;")
                    d = '' if pod(t) else f"if ({g.cname}_set == 1) {{ {g.cname}_set = 2; {self.drop_c(t, g.cname)}; }} "
                    onces[m].insert(0, f"{d}{g.cname}_set = 2;")
                elif t[0] == 'arr':     # a constant table: read-only static data seen as a slice
                    items = ', '.join(self.render(x, holes, {}) for x in c)
                    globs.append(f"static const {self.ctype(t[1])} {g.cname}_d[{len(c) or 1}] = {{{items or '0'}}};")
                    globs.append(f"static const obx_slice {g.cname} = {{(void*){g.cname}_d, {len(c)}}};")
                else:
                    cq = 'const ' if hasattr(g, 'table') else ''
                    globs.append(f"static {cq}{self.ctype(t)} {g.cname} = {self.render(c, holes, {})};")
                    if not pod(t):
                        copies.insert(0, self.drop_c(t, g.cname) + ';')
        gdrops = [x for m in reversed(post) for x in onces[m]] + copies
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
