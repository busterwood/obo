#!/usr/bin/env python3
"""Ownership fuzzing for obo: random programs over owned optionals, a growable
array of nodes, strings, globals, moves, borrowed references and handles, with
the expected output computed in Python.

Two profiles:
  safe  only operations the rules allow in every order. obo must accept every
        program, and every handle is used only while its object is alive; a
        rejection is a false positive (a rule stricter than needed).
  full  also moves, element changes, truncation, references held across them,
        and dangling handles. The generator models the rules as it goes, so each
        program is labelled one of:
          valid    obo must accept it and run it to the modelled output, with no
                   leaks (exit 5), no double frees (exit 6) and no reads of
                   poisoned memory;
          invalid  obo must reject it: a use of a moved variable, or of a
                   reference that a change has invalidated;
          trap     obo accepts it, and it stops with exit 7 at the first use of a
                   handle whose object has been freed, after the modelled output.

    python3 obo/tests/own_fuzz.py [--count N] [--profile safe|full|both]
    python3 obo/tests/own_fuzz.py SEED [--safe]      print one program and its output
"""
import os
import random
import re
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
FORK = os.path.dirname(HERE)
OBO = os.environ.get('OBO', os.path.join(FORK, 'obo'))
TMP = tempfile.mkdtemp(prefix='obo-own-fuzz-')

PRELUDE = [
    "type Node = ref record { value: int; next: ?Node }",
    "type Tag = ref record { v: int; s: string }",
    "var gs = \"\"",
    "var gxs = new []Node(0)",
    "var ga: ?Node = nil",
    "fn mk(v: int): Node { return new Node{value: v} }",
    "fn push(l: ?Node, v: int): Node { return new Node{value: v, next: l} }",
    "fn sum(l: &?Node): int { var t = 0; var p = l; while p != nil { t += p.value; p = p.next }; return t }",
    "fn bump(mut l: &?Node) { var p = l; while p != nil { p.value += 1; p = p.next } }",
    "fn total(xs: &[]Node): int { var t = 0; for n in xs { t += n.value }; return t }",
    "fn eat(l: ?Node): int { return sum(l) }",
    "fn two(mut a: &Node, mut b: &Node) { a.value += 1; b.value += 10 }",
    "fn stepArr(xs: &[]Node, mut n: &Node) { n.value += len(xs) }",
    "fn bumpRef(mut n: &Node) { n.value += 1 }",
    "fn gdrop() { ga = nil }",
    "fn shrinkInt(mut a: &[]Node): int { truncate(a, 0); return 1 }",
    "fn fresh(): []Node { return new []Node(0) }",
    "fn f2() { gdrop() }",
    "fn viaF2(l: &?Node): int { f2(); return sum(l) }",
    "fn sumFree(l: &?Node): int { gdrop(); return sum(l) }",
    "fn mixG(mut p: &?Node, q: &?Node): int { bump(p); return sum(q) }",
    "fn mixGP(p: &?Node): int { return mixG(ga, p) }",
    "fn pq(mut a: &[]Node, b: &Node): int { return shrinkInt(a) + b.value }",
    "fn bumpAll(mut a: &[]Node, b: &Node): int { return len(a) + b.value }",
    "fn gp(ys: &[]Node): int { return bumpAll(gxs, ys[0]) }",
    "fn galias(): int { var q = &ga; bump(ga); return sum(q) }",
    "fn galias2(mut p: &?Node): int { var q = &ga; bump(p); return sum(q) }",
    "fn setGs(v: int): string { gs = \"g\" + string(v); return \"!\" }",
    "fn mkstr(v: int): string { return \"a\" + string(v) }",
    "type Box = ref record { items: []int }",
    "type Pair = ref record { a: Node; b: ?Node }",
    "fn tick(mut a: &[]int): int { append(a, 1); return 0 }",
    "type Shape = closed {",
    "    Circle { r: int }",
    "    Rect { w, h: int }",
    "}",
    "fn area(s: &Shape): int { match s { Circle { return 3 * s.r * s.r } Rect { return s.w * s.h } } }",
]
NAMES = 'abc'          # owned optional locals
STRVARS = ['sa', 'sb', 'gs']


class Fail(Exception):
    pass


class Obj:
    __slots__ = ('v', 'next', 'alive')

    def __init__(self, v, nxt=None):
        self.v, self.next, self.alive = v, nxt, True


def free(o):
    """Frees an owned chain of nodes: what the owner gave up is no longer alive."""
    while o is not None:
        o.alive = False
        o = o.next


def chain_sum(o):
    t = 0
    while o is not None:
        t += o.v
        o = o.next
    return t


def chain_bump(o):
    while o is not None:
        o.v += 1
        o = o.next


class State:
    def __init__(self):
        self.opt = {n: None for n in NAMES}     # the owned optionals a, b, c
        self.moved = {n: False for n in NAMES}  # moved out by a plain '=' (full profile)
        self.xs = []                            # the local array of owned nodes
        self.gxs = []                           # the global array of owned nodes
        self.ga = None                          # the global owned optional chain
        self.strs = {'sa': '', 'sb': '', 'gs': ''}
        self.ss = []                            # the local array of strings
        self.tags = []                          # the local array of records holding a string
        self.refs = {}                          # name -> [node, still valid?]
        self.handles = {}                       # name -> node the handle names
        self.olds = {}                          # name -> value returned by replace()
        self.out = []                           # the expected output lines


class Stmt:
    """One generated statement: its source lines, and what it does to the model."""

    def __init__(self, lines, run, trap=False):
        self.lines, self.run, self.trap = lines, run, trap


def indent(lines):
    return ['    ' + l if l else l for l in lines]


def flat(stmts):
    return [l for s in stmts for l in s.lines]


def invalidate(st):
    for ref in st.refs.values():
        ref[1] = False


class Gen:
    def __init__(self, seed, full):
        self.seed = seed
        self.r = random.Random(seed)
        self.full = full
        self.st = State()
        self.invalid = False
        self.trapped = False
        self.lines = []
        self.nrefs = 0
        self.nhandles = 0
        self.nold = 0
        self.extra = []                         # helper functions an invalid program needs

    def live(self):
        return [n for n in NAMES if not self.st.moved[n]]

    def k(self):
        return self.r.randint(0, 9)

    def pick(self, makers):
        for _ in range(10):
            s = self.r.choice(makers)()
            if s is not None:
                return s
        return None

    # ---- statements that never move or free anything a handle or reference may
    # ---- name: valid in any order, so they may also form loop and branch bodies

    def sum_stmt(self):
        live = self.live()
        if not live:
            return None
        x = self.r.choice(live)
        return Stmt([f"println(sum({x}))"], lambda st: st.out.append(str(chain_sum(st.opt[x]))))

    def total_stmt(self):
        return Stmt(["println(total(xs))"], lambda st: st.out.append(str(sum(o.v for o in st.xs))))

    def forprint_stmt(self):
        return Stmt(["for n in xs { println(n.value) }"], lambda st: st.out.extend(str(o.v) for o in st.xs))

    def append_stmt(self):
        v = self.k()

        def run(st):
            st.xs.append(Obj(v))
        return Stmt([f"append(xs, mk({v}))"], run)

    def bump_stmt(self):
        live = self.live()
        if not live:
            return None
        x = self.r.choice(live)
        return Stmt([f"bump({x})"], lambda st: chain_bump(st.opt[x]))

    def field_stmt(self):
        live = self.live()
        if not live:
            return None
        x, v = self.r.choice(live), self.k()

        def run(st):
            if st.opt[x] is not None:
                st.opt[x].v += v
        return Stmt([f"if {x} != nil {{ {x}.value += {v} }}"], run)

    def valid_refs(self):
        return [n for n, (node, ok) in self.st.refs.items() if ok]

    def ref_read_stmt(self):
        refs = self.valid_refs()
        if not refs:
            return None
        n = self.r.choice(refs)
        return Stmt([f"println({n}.value)"], lambda st: st.out.append(str(st.refs[n][0].v)))

    def ref_write_stmt(self):
        refs = self.valid_refs()
        if not refs:
            return None
        n, v = self.r.choice(refs), self.k()

        def run(st):
            st.refs[n][0].v += v
        return Stmt([f"{n}.value += {v}"], run)

    def str_concat_stmt(self):
        n, v = self.r.choice(STRVARS), self.k()

        def run(st):
            st.strs[n] += str(v)
        return Stmt([f"{n} = {n} + string({v})"], run)

    def str_copy_stmt(self):
        d, s = self.r.sample(STRVARS, 2)

        def run(st):
            st.strs[d] = st.strs[s]
        return Stmt([f"{d} = {s}"], run)

    def str_print_stmt(self):
        n = self.r.choice(STRVARS)
        return Stmt([f"println({n})"], lambda st: st.out.append(st.strs[n]))

    def str_eq_stmt(self):
        a, b = self.r.choice(STRVARS), self.r.choice(STRVARS)
        return Stmt([f"println({a} == {b})"],
                    lambda st: st.out.append('true' if st.strs[a] == st.strs[b] else 'false'))

    def ss_append_stmt(self):
        v = self.k()

        def run(st):
            st.ss.append('w' + str(v))
        return Stmt([f"append(ss, \"w\" + string({v}))"], run)

    def ss_print_stmt(self):
        return Stmt(["println(len(ss))"], lambda st: st.out.append(str(len(st.ss))))

    def ss_set_stmt(self):
        if not self.st.ss:
            return None
        i, v = self.r.randrange(len(self.st.ss)), self.k()

        def run(st):
            st.ss[i] = 'q' + str(v)
        return Stmt([f"ss[{i}] = \"q\" + string({v})"], run)

    def ss_copy_stmt(self):
        if not self.st.ss:
            return None
        d, i = self.r.choice(STRVARS), self.r.randrange(len(self.st.ss))

        def run(st):
            st.strs[d] = st.ss[i]
        return Stmt([f"{d} = ss[{i}]"], run)

    def tag_append_stmt(self):
        v = self.k()

        def run(st):
            st.tags.append((v, 't' + str(len(st.tags) + v)))
        return Stmt([f"append(tags, new Tag{{v: {v}, s: \"t\" + string(len(tags) + {v})}})"], run)

    def tag_print_stmt(self):
        if not self.st.tags:
            return None
        i = self.r.randrange(len(self.st.tags))

        def run(st):
            v, s = st.tags[i]
            st.out.append(f"{v} {s}")
        return Stmt([f"println(string(tags[{i}].v) + \" \" + tags[{i}].s)"], run)

    def tag_trunc_stmt(self):
        n = self.r.randrange(len(self.st.tags) + 1)

        def run(st):
            del st.tags[n:]
        return Stmt([f"truncate(tags, {n})"], run)

    def mix_self_stmt(self):
        """One owned local passed both as the mut and as the shared argument: the same object."""
        live = self.live()
        if not live:
            return None
        x = self.r.choice(live)

        def run(st):
            chain_bump(st.opt[x])
            st.out.append(str(chain_sum(st.opt[x])))
        return Stmt([f"println(mixG({x}, {x}))"], run)

    def mix_global_stmt(self):
        """A global passed mut beside a reference parameter: the global may be the same object."""
        live = self.live()
        if not live:
            return None
        x = self.r.choice(live)

        def run(st):
            chain_bump(st.ga)
            st.out.append(str(chain_sum(st.opt[x])))
        return Stmt([f"println(mixGP({x}))"], run)

    def alias_param_stmt(self):
        """A mut array and an element of another array, both parameters: they may be one array."""
        self.extra_fn("fn pf(mut xs: &[]Node, ys: &[]Node): int { return pq(xs, ys[0]) }")
        return self.invalid_stmt([])

    def global_ref_free_stmt(self):
        """A reference into the global chain, then a call that frees the chain: the reference dangles."""
        r = f"r{self.nrefs}"
        self.nrefs += 1
        return self.invalid_stmt([f"var {r} = &ga", "gdrop()", f"println(sum({r}))"])

    def handle_index_call_stmt(self):
        """A handle to a record's array, indexed by a call that may change it: rejected."""
        n = self.nrefs
        self.nrefs += 1
        return self.invalid_stmt([f"var b{n} = new Box{{items: new []int(0)}}", f"let hx{n} = handle(b{n})",
                                  f"hx{n}.items[tick(b{n}.items)] = 9"])

    def ref_move_stmt(self):
        """A reference parameter passed where ownership is taken: rejected."""
        self.extra_fn("fn mvRef(l: &?Node): int { return eat(l) }")
        return self.invalid_stmt([])

    def callee_free_global_stmt(self):
        """A recursive call that passes a global the callee's own body frees: rejected."""
        self.extra_fn("fn frX(n: int, l: &?Node): int { if n > 0 { frX(n - 1, ga) }; gdrop(); return sum(l) }")
        return self.invalid_stmt([])

    def alias_global_elem_stmt(self):
        """A mut array parameter beside an element of a global array."""
        self.extra_fn("fn pgl(mut xs: &[]Node): int { return pq(xs, gxs[0]) }")
        return self.invalid_stmt([])

    def global_alias_stmt(self):
        """A reference into the global chain, a mut change to it, then a read: the change is allowed."""
        def run(st):
            chain_bump(st.ga)
            st.out.append(str(chain_sum(st.ga)))
        return Stmt(["println(galias())"], run)

    def gs_pin_stmt(self):
        """A string read before a call that changes it: the value read is the one kept."""
        d, v = self.r.choice(STRVARS), self.k()

        def run(st):
            old = st.strs['gs']
            st.strs['gs'] = 'g' + str(v)
            st.strs[d] = old + '!'
        return Stmt([f"{d} = gs + setGs({v})"], run)

    def str_call_stmt(self):
        """A string returned by a call, used at once and dropped."""
        v = self.k()
        if self.r.random() < 0.5:
            return Stmt([f"println(len(mkstr({v})))"],
                        lambda st: st.out.append(str(len('a' + str(v)))))
        d = self.r.choice(STRVARS)

        def run(st):
            st.strs[d] = 'a' + str(v)
        return Stmt([f"{d} = mkstr({v})"], run)

    def ss_loop_stmt(self):
        """Two strings appended to ss, each copied out of the loop, then ss is freed."""
        d = self.r.choice(STRVARS)

        def run(st):
            n = len(st.ss)
            st.ss += ['w' + str(n), 'w' + str(n + 2)]
            st.strs[d] = st.ss[-1]
            st.out.append(st.strs[d])
            st.ss = []
        return Stmt(["append(ss, \"w\" + string(len(ss)))", "append(ss, \"w\" + string(len(ss) + 1))",
                     "for sv in ss {", f"    {d} = sv", "}", "truncate(ss, 0)", f"println({d})"], run)

    def shape_stmt(self):
        """A closed value reallocated as another variant, matched each time."""
        v = self.k()
        n = f"s{self.nrefs}"
        self.nrefs += 1

        def run(st):
            st.out.append(str(3 * v * v))
            st.out.append(str(v * 2))
        return Stmt([f"var {n}: Shape = new Circle{{r: {v}}}", f"println(area({n}))",
                     f"{n} = new Rect{{w: {v}, h: 2}}", f"println(area({n}))"], run)

    def global_alias2_stmt(self):
        """A reference to the global chain, then a change through a parameter that may be
        that chain: the reference borrows the whole chain, so it stays valid."""
        def run(st):
            chain_bump(st.ga)
            st.out.append(str(chain_sum(st.ga)))
        return Stmt(["println(galias2(ga))"], run)

    def sibling_ref_stmt(self):
        """A reference to one field, then a write to its sibling: the reference stays valid."""
        v = self.k()
        n = self.nrefs
        self.nrefs += 1
        return Stmt([f"var pr{n} = new Pair{{a: new Node{{value: {v}, next: nil}}, b: nil}}",
                     f"var qr{n} = &pr{n}.a",
                     f"pr{n}.b = new Node{{value: 1, next: nil}}",
                     f"println(qr{n}.value)"],
                    lambda st: st.out.append(str(v)))

    def two_elems_stmt(self):
        """Two mut arguments at two known elements of one array: they are different, so allowed."""
        v = self.k()
        n = self.nrefs
        self.nrefs += 1

        def run(st):
            st.out.append(str(v + 1))
            st.out.append('12')
        return Stmt([f"var er{n} = new []Node(0)",
                     f"append(er{n}, new Node{{value: {v}, next: nil}})",
                     f"append(er{n}, new Node{{value: 2, next: nil}})",
                     f"two(er{n}[0], er{n}[1])",
                     f"println(er{n}[0].value)",
                     f"println(er{n}[1].value)"], run)

    def g_append_stmt(self):
        v = self.k()

        def run(st):
            st.gxs.append(Obj(v))
        return Stmt([f"append(gxs, mk({v}))"], run)

    def g_total_stmt(self):
        return Stmt(["println(total(gxs))"], lambda st: st.out.append(str(sum(o.v for o in st.gxs))))

    def ga_push_stmt(self):
        v = self.k()

        def run(st):
            st.ga = Obj(v, st.ga)
        return Stmt([f"ga = push(take(ga), {v})"], run)

    def ga_sum_stmt(self):
        return Stmt(["println(sum(ga))"], lambda st: st.out.append(str(chain_sum(st.ga))))

    def ga_bump_stmt(self):
        return Stmt(["bump(ga)"], lambda st: chain_bump(st.ga))

    def alive_handles(self):
        return [h for h, o in self.st.handles.items() if o.alive]

    def h_read_stmt(self):
        hs = self.alive_handles()
        if not hs:
            return None
        h = self.r.choice(hs)
        return Stmt([f"println({h}.value)"], lambda st: st.out.append(str(st.handles[h].v)))

    def h_write_stmt(self):
        hs = self.alive_handles()
        if not hs:
            return None
        h, v = self.r.choice(hs), self.k()

        def run(st):
            st.handles[h].v += v
        return Stmt([f"{h}.value += {v}"], run)

    def h_alive_stmt(self):
        if not self.st.handles:
            return None
        h = self.r.choice(sorted(self.st.handles))
        return Stmt([f"println(alive({h}))"],
                    lambda st: st.out.append('true' if st.handles[h].alive else 'false'))

    def common(self):
        makers = [self.sum_stmt, self.sum_stmt, self.total_stmt, self.forprint_stmt,
                  self.append_stmt, self.bump_stmt, self.field_stmt,
                  self.str_concat_stmt, self.str_copy_stmt, self.str_print_stmt, self.str_eq_stmt,
                  self.ss_append_stmt, self.ss_print_stmt, self.ss_set_stmt, self.ss_copy_stmt,
                  self.tag_append_stmt, self.tag_append_stmt, self.tag_print_stmt,
                  self.mix_self_stmt, self.mix_global_stmt, self.global_alias_stmt,
                  self.gs_pin_stmt, self.str_call_stmt, self.shape_stmt,
                  self.sibling_ref_stmt, self.two_elems_stmt, self.global_alias2_stmt,
                  self.g_append_stmt, self.g_total_stmt, self.ga_push_stmt, self.ga_sum_stmt,
                  self.ga_bump_stmt, self.h_read_stmt, self.h_write_stmt, self.h_alive_stmt]
        makers += [self.old_sum_stmt, self.owned_deep_stmt]
        if self.full:
            makers += [self.ref_read_stmt, self.ref_write_stmt, self.stepArr_stmt]
        return makers

    def inner(self):
        return self.pick(self.common())

    def body(self):
        return [self.inner() for _ in range(self.r.randint(1, 3))]

    # ---- top-level statements: may move, take, free and invalidate

    def push_stmt(self):
        x, v = self.r.choice(NAMES), self.k()
        if self.st.moved[x]:
            def run(st):
                st.opt[x] = Obj(v)
                st.moved[x] = False
            return Stmt([f"{x} = push(nil, {v})"], run)
        srcs = [y for y in self.live() if y != x]
        if srcs and self.r.random() < 0.4:
            y = self.r.choice(srcs)

            def run(st):
                old = st.opt[x]
                st.opt[x] = Obj(v, st.opt[y])
                st.opt[y] = None
                free(old)
            return Stmt([f"{x} = push(take({y}), {v})"], run)

        def run(st):
            st.opt[x] = Obj(v, st.opt[x])
        return Stmt([f"{x} = push(take({x}), {v})"], run)

    def take_stmt(self):
        live = self.live()
        if not live:
            return None
        x = self.r.choice(live)
        y = self.r.choice([n for n in NAMES if n != x])

        def run(st):
            if not st.moved[y]:
                free(st.opt[y])
            st.opt[y] = st.opt[x]
            st.opt[x] = None
            st.moved[y] = False
        return Stmt([f"{y} = take({x})"], run)

    def move_stmt(self):
        live = self.live()
        if not live:
            return None
        x = self.r.choice(live)
        y = self.r.choice([n for n in NAMES if n != x])

        def run(st):
            if not st.moved[y]:
                free(st.opt[y])
            st.opt[y] = st.opt[x]
            st.moved[x] = True
            st.moved[y] = False
        return Stmt([f"{y} = {x}"], run)

    def loop_stmt(self):
        n = self.r.randint(0, 3)
        body = self.body()

        def run(st):
            for _ in range(n):
                for s in body:
                    s.run(st)
        return Stmt([f"for i in 0..{n} {{"] + indent(flat(body)) + ["}"], run)

    def if_stmt(self):
        live = self.live()
        if not live:
            return None
        x, t = self.r.choice(live), self.r.randint(0, 12)
        then_b, else_b = self.body(), self.body()

        def run(st):
            for s in (then_b if chain_sum(st.opt[x]) > t else else_b):
                s.run(st)
        lines = ([f"if sum({x}) > {t} {{"] + indent(flat(then_b)) + ["} else {"]
                 + indent(flat(else_b)) + ["}"])
        return Stmt(lines, run)

    def elem_set_stmt(self):
        if not self.st.xs:
            return None
        i, v = self.r.randrange(len(self.st.xs)), self.k()

        def run(st):
            free(st.xs[i])
            st.xs[i] = Obj(v)
            invalidate(st)
        return Stmt([f"xs[{i}] = mk({v})"], run)

    def truncate_stmt(self):
        if not self.st.xs:
            return None
        n = self.r.randrange(len(self.st.xs))

        def run(st):
            for o in st.xs[n:]:
                free(o)
            del st.xs[n:]
            invalidate(st)
        return Stmt([f"truncate(xs, {n})"], run)

    def ref_new_stmt(self):
        if not self.st.xs:
            return None
        i = self.r.randrange(len(self.st.xs))
        name = f"r{self.nrefs}"
        self.nrefs += 1

        def run(st):
            st.refs[name] = [st.xs[i], True]
        return Stmt([f"var {name} = &xs[{i}]"], run)

    def g_set_stmt(self):
        if not self.st.gxs:
            return None
        i, v = self.r.randrange(len(self.st.gxs)), self.k()

        def run(st):
            free(st.gxs[i])
            st.gxs[i] = Obj(v)
        return Stmt([f"gxs[{i}] = mk({v})"], run)

    def g_trunc_stmt(self):
        if not self.st.gxs:
            return None
        n = self.r.randrange(len(self.st.gxs))

        def run(st):
            for o in st.gxs[n:]:
                free(o)
            del st.gxs[n:]
        return Stmt([f"truncate(gxs, {n})"], run)

    def gdrop_stmt(self):
        def run(st):
            free(st.ga)
            st.ga = None
        return Stmt(["gdrop()"], run)

    def ss_trunc_stmt(self):
        if not self.st.ss:
            return None
        n = self.r.randrange(len(self.st.ss))

        def run(st):
            del st.ss[n:]
        return Stmt([f"truncate(ss, {n})"], run)

    def old_sum_stmt(self):
        if not self.st.olds:
            return None
        o = self.r.choice(sorted(self.st.olds))
        return Stmt([f"println(sum({o}))"], lambda st: st.out.append(str(chain_sum(st.olds[o]))))

    def stepArr_stmt(self):
        refs = self.valid_refs()
        if not refs or not self.st.xs:
            return None
        n = self.r.choice(refs)

        def run(st):
            st.refs[n][0].v += len(st.xs)
            for other, ref in st.refs.items():     # a mut change through n invalidates the rest
                if other != n:
                    ref[1] = False
        return Stmt([f"stepArr(xs, {n})"], run)

    # ---- top-level ownership statements

    def drop_stmt(self):
        live = self.live()
        if not live:
            return None
        x = self.r.choice(live)

        def run(st):
            free(st.opt[x])
            st.opt[x] = None
            st.moved[x] = True
        return Stmt([f"drop({x})"], run)

    def replace_stmt(self):
        live = self.live()
        if not live:
            return None
        x, v = self.r.choice(live), self.k()
        name = f"o{self.nold}"
        self.nold += 1

        def run(st):
            st.olds[name] = st.opt[x]
            st.opt[x] = Obj(v)
        return Stmt([f"let {name} = replace({x}, push(nil, {v}))"], run)

    def eat_stmt(self):
        live = self.live()
        if not live:
            return None
        x = self.r.choice(live)

        def run(st):
            st.out.append(str(chain_sum(st.opt[x])))
            free(st.opt[x])
            st.opt[x] = None
            st.moved[x] = True
        return Stmt([f"println(eat({x}))"], run)

    def pair(self):
        live = self.live()
        if len(live) < 2:
            return None
        return self.r.sample(live, 2)

    def if_move_stmt(self):
        pr = self.pair()
        if pr is None:
            return None
        x, y = pr
        t = self.r.randint(0, 12)
        kind = 'one' if self.full and self.r.random() < 0.1 else self.r.choice(['both', 'none'])
        # 'one' moves on one branch only, which the rules reject
        if kind == 'one':
            self.invalid = True
        then_mv = kind in ('both', 'one')
        else_mv = kind == 'both'
        then_b = [f"    {y} = {x}"] if then_mv else []
        else_b = [f"    {y} = {x}"] if else_mv else []

        def run(st):
            if kind == 'both':
                free(st.opt[y]) if not st.moved[y] else None
                st.opt[y] = st.opt[x]
                st.moved[x] = True
                st.moved[y] = False
        return Stmt([f"if sum({x}) > {t} {{"] + then_b + ["} else {"] + else_b + ["}"], run)

    def loop_move_stmt(self):
        pr = self.pair()
        if pr is None:
            return None
        x, y = pr
        n, v = self.r.randint(1, 3), self.k()
        restore = not (self.full and self.r.random() < 0.1)   # without it the loop changes states
        if not restore:
            self.invalid = True
        body = [f"    {y} = {x}"] + ([f"    {x} = push(nil, {v})"] if restore else [])

        def run(st):
            if not restore:
                return
            for _ in range(n):
                free(st.opt[y])
                st.opt[y] = st.opt[x]
                st.opt[x] = Obj(v)
        return Stmt([f"for i in 0..{n} {{"] + body + ["}"], run)

    def bumpRef_stmt(self):
        refs = self.valid_refs()
        if not refs:
            return None
        n = self.r.choice(refs)

        def run(st):
            st.refs[n][0].v += 1
            for other, ref in st.refs.items():
                if other != n:
                    ref[1] = False
        return Stmt([f"bumpRef({n})"], run)

    # ---- statements the rules reject: the program is invalid

    def invalid_stmt(self, lines):
        self.invalid = True
        return Stmt(lines, lambda st: None)

    def and_move_stmt(self):
        pr = self.pair()
        if pr is None:
            return None
        x, y = pr
        return self.invalid_stmt([f"if sum({x}) > 0 && eat({y}) > 0 {{", "    println(1)", "}"])

    def field_move_stmt(self):
        live = self.live()
        if not live:
            return None
        x = self.r.choice(live)
        return self.invalid_stmt([f"if {x} != nil {{", f"    let t = {x}.next", "}"])

    def ref_dead_stmt(self):
        """Reads a reference that a change has invalidated: the program is invalid."""
        bad = [n for n, (node, ok) in self.st.refs.items() if not ok]
        if not bad:
            return None
        return self.invalid_stmt([f"println({self.r.choice(bad)}.value)"])

    def choose_ref_stmt(self):
        """A reference pointed at one of two locals, re-pointed on one path only, then read; nothing
        is changed. Valid: each path's binding is the one read on that path."""
        pr = self.pair()
        if pr is None:
            return None
        x, y = pr
        r = f"r{self.nrefs}"
        self.nrefs += 1

        def run(st):
            st.out.append(str(chain_sum(st.opt[y] if len(st.xs) > 0 else st.opt[x])))
        # the reference lives in its own block, so no later change inside a loop reaches it
        return Stmt(["if len(xs) >= 0 {", f"    var {r} = &{x}", f"    if len(xs) > 0 {{ {r} = &{y} }}",
                      f"    println(sum({r}))", "}"], run)

    def branch_free_stmt(self):
        """A reference re-pointed on one path only, then a change under the root the other path
        points into, then a read: the program is invalid (the read may see freed memory)."""
        pr = self.pair()
        if pr is None:
            return None
        x, y = pr
        r = f"r{self.nrefs}"
        self.nrefs += 1
        return self.invalid_stmt([f"var {r} = &{x}", f"if len(xs) > 0 {{ {r} = &{y} }}",
                                  f"{x} = nil", f"println(sum({r}))"])

    def mut_call_dead_stmt(self):
        """A mutable call through one reference invalidates another, which is then read."""
        if not self.st.xs:
            return None
        a, b = f"r{self.nrefs}", f"r{self.nrefs + 1}"
        self.nrefs += 2
        i, j = self.r.randrange(len(self.st.xs)), self.r.randrange(len(self.st.xs))
        call = self.r.choice([f"bumpRef({a})", f"stepArr(xs, {a})"])
        return self.invalid_stmt([f"var {a} = &xs[{i}]", f"var {b} = &xs[{j}]", call,
                                  f"println({b}.value)"])

    def discard_stmt(self):
        """An owned result that is never stored: it is freed at once."""
        v = self.k()
        return Stmt([f"mk({v})"], lambda st: None)

    def handle_set_stmt(self):
        hs = sorted(self.st.handles)
        if not hs:
            return None
        return self.invalid_stmt([f"{self.r.choice(hs)}.value = shrinkInt(xs)"])

    def move_root_stmt(self):
        r, z = f"r{self.nrefs}", f"z{self.nold}"
        self.nrefs += 1
        self.nold += 1
        return self.invalid_stmt([f"var {r} = &xs[0]", f"let {z} = xs", f"println({r}.value)"])

    def free_global_stmt(self):
        return self.invalid_stmt([f"println({self.r.choice(['sumFree', 'viaF2'])}(ga))"])

    def extra_fn(self, text):
        """Adds a helper function to this program only: it is invalid, so the program is."""
        if text not in self.extra:
            self.extra.append(text)

    def ro_write_stmt(self):
        self.extra_fn("fn wr(n: &Node) { n.value = 5 }")
        return self.invalid_stmt([])

    def ro_pass_stmt(self):
        self.extra_fn("fn passRO(n: &Node) { bumpRef(n) }")
        return self.invalid_stmt([])

    def walk_assign_stmt(self):
        return self.invalid_stmt(["for n in xs {", "    xs = new []Node(0)", "}"])

    def handle_borrow_stmt(self):
        """A borrow through a handle, used, then an int field of its object written: valid.
        The write frees nothing, and the borrow is not used after it."""
        if not self.st.xs:
            return None
        h, q = f"h{self.nhandles}", f"q{self.nold}"
        self.nhandles += 1
        self.nold += 1
        v = self.k()

        def run(st):
            o = st.xs[0]
            st.out.append(str(chain_sum(o.next)))
            o.v = v
        # in a block: a reference through a handle lives only as long as the statement (any later
        # change in a loop would invalidate it, and the model does not track that)
        return Stmt(["{"] + indent([f"let {h} = handle(xs[0])", f"let {q} = &{h}.next", f"println(sum({q}))",
                                    f"{h}.value = {v}"]) + ["}"], run)

    def handle_borrow_dead_stmt(self):
        """A borrow through a handle, a change that might free (through the handle, through a
        handle temporary, or a call), then a use of the borrow: rejected."""
        h, q = f"h{self.nhandles}", f"q{self.nold}"
        self.nhandles += 1
        self.nold += 1
        change = self.r.choice([f"{h}.next = nil", "handle(xs[0]).next = nil", "gdrop()"])
        # its own element first, so that xs[0] exists whatever the program has done so far
        return self.invalid_stmt([f"append(xs, mk({self.k()}))", f"let {h} = handle(xs[0])",
                                  f"let {q} = &{h}.next", change, f"println(sum({q}))"])

    def owned_deep_stmt(self):
        """A reference into an owned variable's element, a scalar written through a handle to that
        element (which frees nothing), then the reference used: valid."""
        q, h = f"q{self.nold}", f"h{self.nhandles}"
        self.nold += 1
        self.nhandles += 1
        v, w = self.k(), self.k()

        def run(st):
            st.xs.append(Obj(v))
            st.xs[0].v = w
            st.out.append(str(chain_sum(st.xs[0].next)))
        # in a block, so that the reference ends with the statement: a reference through a handle
        # is invalidated by any later change in a loop that it lives across
        return Stmt(["{"] + indent([f"append(xs, mk({v}))", f"let {q} = &xs[0].next", f"let {h} = handle(xs[0])",
                                    f"{h}.value = {w}", f"println(sum({q}))"]) + ["}"], run)

    def owned_deep_writer_stmt(self):
        """A reference into an owned variable's element, a writer called through a handle to that
        element, and no use of the reference afterwards: valid (the writer frees what it points into,
        and nothing uses it)."""
        q = f"q{self.nold}"
        self.nold += 1
        v = self.k()
        self.extra_fn("fn cutNext(h: ^Node) { h.next = nil }")

        def run(st):
            st.xs.append(Obj(v))
            o = st.xs[0]
            free(o.next)
            o.next = None
            invalidate(st)
        return Stmt(["{"] + indent([f"append(xs, mk({v}))", f"let {q} = &xs[0].next", "cutNext(handle(xs[0]))"]) + ["}"],
                    run)

    def owned_deep_dead_stmt(self):
        """A reference into an owned variable's element, a change through a handle to that element
        (named, a handle temporary, or a writer called with one), then a use of the reference:
        rejected, since the change frees what the reference points into."""
        q, h = f"q{self.nold}", f"h{self.nhandles}"
        self.nold += 1
        self.nhandles += 1
        self.extra_fn("fn cutNext(h: ^Node) { h.next = nil }")
        self.extra_fn("fn cutVia(h: ^Node) { cutNext(h) }")      # a writer two calls down
        change = self.r.choice([[f"let {h} = handle(xs[0])", f"{h}.next = nil"],
                                ["handle(xs[0]).next = nil"],
                                ["cutNext(handle(xs[0]))"],
                                ["cutVia(handle(xs[0]))"]])
        return self.invalid_stmt([f"append(xs, mk({self.k()}))", f"let {q} = &xs[0].next"] + change
                                 + [f"println(sum({q}))"])

    def owned_deep_arg_stmt(self):
        """A reference into an owned variable's element passed to a writer: rejected, since the
        writer may free what the reference points into."""
        q = f"q{self.nold}"
        self.nold += 1
        self.extra_fn("fn cutArg(l: &?Node, h: ^Node) { h.next = nil }")
        return self.invalid_stmt([f"append(xs, mk({self.k()}))", f"let {q} = &xs[0].next",
                                  f"cutArg({q}, handle(xs[0]))"])

    def elem_write_stmt(self):
        a, b = f"r{self.nrefs}", f"r{self.nrefs + 1}"
        self.nrefs += 2
        return self.invalid_stmt([f"var {a} = &xs[0]", f"var {b} = &xs[1]",
                                  f"{a}.next = push(nil, 3)", f"println({b}.value)"])

    def two_self_stmt(self):
        a = f"r{self.nrefs}"
        self.nrefs += 1
        return self.invalid_stmt([f"var {a} = &xs[0]", f"two({a}, {a})"])

    def field_call_stmt(self):
        return self.invalid_stmt(["xs[0] = mk(shrinkInt(xs))"])

    def temp_stmt(self):
        v = self.k()
        form = self.r.randrange(3)
        if form == 0:
            return self.invalid_stmt([f"println(mk({v}).value)"])
        if form == 1:
            return self.invalid_stmt([f"println(sum(push(nil, {v})))"])
        return self.invalid_stmt(["println(len(fresh()))"])

    def two_stmt(self):
        refs = self.valid_refs()
        if not refs:
            return None
        n = self.r.choice(refs)
        return self.invalid_stmt([f"two({n}, {n})"])

    def h_new_stmt(self):
        srcs = [(n, lst) for n, lst in (('xs', self.st.xs), ('gxs', self.st.gxs)) if lst]
        if not srcs:
            return None
        src, lst = self.r.choice(srcs)
        i = self.r.randrange(len(lst))
        h = f"h{self.nhandles}"
        self.nhandles += 1

        def run(st):
            st.handles[h] = (st.xs if src == 'xs' else st.gxs)[i]
        return Stmt([f"let {h} = handle({src}[{i}])"], run)

    def h_dead_stmt(self):
        """Uses a handle whose object has been freed: the program stops there."""
        dead = [h for h, o in self.st.handles.items() if not o.alive]
        if not dead:
            return None
        h = self.r.choice(sorted(dead))
        return Stmt([f"println({h}.value)"], lambda st: None, trap=True)

    def h_kill_stmt(self):
        """Replaces the element a live handle names, so the handle dangles."""
        for h in sorted(self.alive_handles()):
            target = self.st.handles[h]
            for src, lst in (('xs', self.st.xs), ('gxs', self.st.gxs)):
                i = next((k for k, o in enumerate(lst) if o is target), None)
                if i is not None:
                    v = self.k()

                    def run(st, src=src, i=i, v=v):
                        lst2 = st.xs if src == 'xs' else st.gxs
                        free(lst2[i])
                        lst2[i] = Obj(v)
                        invalidate(st)
                    return Stmt([f"{src}[{i}] = mk({v})"], run)
        return None

    def inject_stmt(self):
        """A statement that uses memory the rules no longer allow: the program is invalid."""
        opts = []
        for x in NAMES:
            if self.st.moved[x]:
                opts.append(f"println(sum({x}))")
        for n, (node, ok) in self.st.refs.items():
            if not ok:
                opts.append(f"println({n}.value)")
        if not opts:
            return None
        self.invalid = True
        return Stmt([self.r.choice(opts)], lambda st: None)

    def step(self):
        if self.full and self.r.random() < 0.5:
            s = self.h_dead_stmt()
            if s is not None:
                return s
        makers = self.common() + [self.choose_ref_stmt] * 2 + [self.push_stmt] * 4 + [self.take_stmt] * 2 \
            + [self.loop_stmt, self.if_stmt, self.g_set_stmt, self.g_trunc_stmt, self.ss_loop_stmt,
               self.gdrop_stmt, self.ss_trunc_stmt, self.tag_trunc_stmt, self.h_new_stmt, self.h_new_stmt,
               self.h_kill_stmt, self.h_kill_stmt, self.handle_borrow_stmt, self.owned_deep_writer_stmt,
               self.drop_stmt, self.replace_stmt, self.eat_stmt, self.if_move_stmt,
               self.loop_move_stmt, self.discard_stmt]
        if self.full:
            makers += [self.move_stmt] * 2 + [self.elem_set_stmt, self.truncate_stmt,
                                              self.ref_new_stmt, self.h_dead_stmt, self.bumpRef_stmt]
            if self.r.random() < 0.1:
                s = self.ref_dead_stmt()
                if s is not None:
                    return s
            if self.r.random() < 0.04:
                s = self.mut_call_dead_stmt()
                if s is not None:
                    return s
            if self.r.random() < 0.03:
                s = self.inject_stmt()
                if s is not None:
                    return s
        return self.pick(makers)

    def bad_stmt(self):
        """One statement that breaks a rule. The kinds take turns by seed, so each one
        turns up regularly; if the turn's kind does not fit here, one is picked at random."""
        ops = [self.and_move_stmt, self.field_move_stmt, self.temp_stmt,
               self.two_stmt, self.handle_set_stmt, self.move_root_stmt,
               self.free_global_stmt, self.field_call_stmt, self.ro_write_stmt,
               self.ro_pass_stmt, self.walk_assign_stmt, self.handle_borrow_dead_stmt,
               self.elem_write_stmt, self.two_self_stmt, self.alias_param_stmt,
               self.alias_global_elem_stmt, self.global_ref_free_stmt,
               self.handle_index_call_stmt, self.ref_move_stmt, self.callee_free_global_stmt,
               self.owned_deep_dead_stmt, self.owned_deep_arg_stmt, self.branch_free_stmt]
        s = ops[self.seed % len(ops)]()
        return s if s is not None else self.pick(ops)

    def program(self):
        count = self.r.randint(4, 20)
        # about one program in three breaks one rule, at a random point; in every other
        # block of 20 seeds each program does, so each kind of bad statement recurs
        # (bad_stmt picks the kind by seed) and the mutation runs see every one
        due = self.full and (self.seed // 20) % 2 == 0
        bad_at = self.r.randrange(count) if self.full and (self.r.random() < 0.3 or due) else None
        for idx in range(count):
            s = self.bad_stmt() if idx == bad_at else None
            if s is None:
                s = self.step()
            if s is None:
                continue
            s.run(self.st)
            self.lines += s.lines
            if s.trap:
                self.trapped = True
                break
        head = ["fn main() {",
                "    var a: ?Node = nil",
                "    var b: ?Node = nil",
                "    var c: ?Node = nil",
                "    var xs = new []Node(0)",
                "    var sa = \"\"",
                "    var sb = \"\"",
                "    var ss = new []string(0)",
                "    var tags = new []Tag(0)"]
        return "\n".join(PRELUDE + self.extra + head + indent(self.lines) + ["}"]) + "\n"


def generate(seed, full):
    """Returns (program text, expected output lines, kind), kind being valid, invalid or trap."""
    g = Gen(seed, full)
    text = g.program()
    kind = 'invalid' if g.invalid else ('trap' if g.trapped else 'valid')
    return text, g.st.out, kind


def check(seed, full):
    """Returns 'ran', 'rejected' or 'trapped' when the outcome is as expected; raises Fail otherwise."""
    text, expect, kind = generate(seed, full)
    tag = ('full' if full else 'safe') + str(seed)
    path = os.path.join(TMP, tag + '.obx')
    exe = os.path.join(TMP, tag)
    with open(path, 'w') as f:
        f.write(text)
    b = subprocess.run([OBO, path, '-o', exe, '--rt', FORK], capture_output=True, text=True, timeout=60)
    regen = f"(regenerate with: python3 obo/tests/own_fuzz.py {seed}{'' if full else ' --safe'})"
    if b.returncode != 0:
        if not re.search(r':\d+:\d+: ', b.stderr):
            raise Fail(f"compiler failed without a source location (exit {b.returncode}): "
                       f"{b.stderr.strip()[:300]}\n{path}")
        if kind != 'invalid':
            raise Fail(f"false rejection: {b.stderr.strip()}\n{path}\n{regen}")
        return 'rejected'
    if kind == 'invalid':
        raise Fail(f"missed error: obo accepted a use of moved or invalidated memory\n{path}\n{regen}")
    run = subprocess.run([exe], capture_output=True, text=True, timeout=30,
                         env=dict(os.environ, OBO_CHECK='1', OBX_GC='poison'))
    want_exit = 7 if kind == 'trap' else 0
    if run.returncode != want_exit:
        raise Fail(f"exit {run.returncode}, expected {want_exit}: {run.stderr.strip()[:300]}\n{path}\n{regen}")
    if kind == 'trap' and 'has been freed' not in run.stderr:
        raise Fail(f"exit 7 without the handle message: {run.stderr.strip()[:300]}\n{path}\n{regen}")
    got = run.stdout.splitlines()
    if got != expect:
        i = next((k for k in range(min(len(got), len(expect))) if got[k] != expect[k]), min(len(got), len(expect)))
        raise Fail(f"output line {i + 1}: expected {expect[i] if i < len(expect) else '<end>'!r}, "
                   f"got {got[i] if i < len(got) else '<end>'!r}\n{path}\n{regen}")
    return 'trapped' if kind == 'trap' else 'ran'


def tests(count):
    """(name, check) pairs for run_fork.py: the first count seeds of each profile."""
    out = []
    for full in (False, True):
        for seed in range(1, count + 1):
            out.append((f"own-fuzz/{'full' if full else 'safe'}/{seed}",
                        lambda s=seed, f=full: check(s, f)))
    return out


def main():
    args = sys.argv[1:]
    if args and args[0].isdigit():
        full = '--safe' not in args
        text, expect, kind = generate(int(args[0]), full)
        print(text, end='')
        print(f"-- expected ({kind}):")
        print('\n'.join(expect))
        return
    count = int(args[args.index('--count') + 1]) if '--count' in args else 300
    which = args[args.index('--profile') + 1] if '--profile' in args else 'both'
    profiles = {'safe': [False], 'full': [True], 'both': [False, True]}[which]
    failures = []
    for full in profiles:
        name = 'full' if full else 'safe'
        counts = {'ran': 0, 'rejected': 0, 'trapped': 0}
        for seed in range(1, count + 1):
            try:
                counts[check(seed, full)] += 1
            except Fail as e:
                failures.append(f"[{name} {seed}] {e}")
        print(f"{name}: {counts['ran']} valid ran correctly, {counts['rejected']} invalid rejected, "
              f"{counts['trapped']} dangling handles trapped as expected, of {count}")
    for f in failures[:20]:
        print("FAIL", f)
    print(f"\n{len(failures)} failures in {count * len(profiles)} programs")
    sys.exit(1 if failures else 0)


if __name__ == '__main__':
    main()
