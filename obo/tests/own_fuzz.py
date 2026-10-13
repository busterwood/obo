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
    "var gs = \"\"",
    "var gxs = new []Node(0)",
    "var ga: ?Node = nil",
    "fn mk(v: int): Node { return new Node{value: v} }",
    "fn push(l: ?Node, v: int): Node { return new Node{value: v, next: l} }",
    "fn sum(l: &?Node): int { var t = 0; var p = l; while p != nil { t += p.value; p = p.next }; return t }",
    "fn bump(l: &mut ?Node) { var p = l; while p != nil { p.value += 1; p = p.next } }",
    "fn total(xs: &[]Node): int { var t = 0; for n in xs { t += n.value }; return t }",
    "fn eat(l: ?Node): int { return sum(l) }",
    "fn two(a: &mut Node, b: &mut Node) { a.value += 1; b.value += 10 }",
    "fn stepArr(xs: &[]Node, n: &mut Node) { n.value += len(xs) }",
    "fn bumpRef(n: &mut Node) { n.value += 1 }",
    "fn gdrop() { ga = nil }",
    "fn shrinkInt(a: &mut []Node): int { truncate(a, 0); return 1 }",
    "fn fresh(): []Node { return new []Node(0) }",
    "fn f2() { gdrop() }",
    "fn viaF2(l: &?Node): int { f2(); return sum(l) }",
    "fn sumFree(l: &?Node): int { gdrop(); return sum(l) }",
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
        self.r = random.Random(seed)
        self.full = full
        self.st = State()
        self.invalid = False
        self.trapped = False
        self.lines = []
        self.nrefs = 0
        self.nhandles = 0
        self.nold = 0

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
                  self.g_append_stmt, self.g_total_stmt, self.ga_push_stmt, self.ga_sum_stmt,
                  self.ga_bump_stmt, self.h_read_stmt, self.h_write_stmt, self.h_alive_stmt]
        makers += [self.old_sum_stmt]
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
        makers = self.common() + [self.push_stmt] * 4 + [self.take_stmt] * 2 \
            + [self.loop_stmt, self.if_stmt, self.g_set_stmt, self.g_trunc_stmt,
               self.gdrop_stmt, self.ss_trunc_stmt, self.h_new_stmt, self.h_new_stmt,
               self.h_kill_stmt, self.h_kill_stmt,
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
        """One statement that breaks a rule, chosen from all of them."""
        return self.pick([self.and_move_stmt, self.field_move_stmt, self.temp_stmt,
                          self.two_stmt, self.handle_set_stmt, self.move_root_stmt,
                          self.free_global_stmt, self.field_call_stmt])

    def program(self):
        count = self.r.randint(4, 20)
        # about one program in five breaks one rule, at a random point
        bad_at = self.r.randrange(count) if self.full and self.r.random() < 0.2 else None
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
                "    var ss = new []string(0)"]
        return "\n".join(PRELUDE + head + indent(self.lines) + ["}"]) + "\n"


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
