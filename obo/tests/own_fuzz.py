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

A third profile, result, is a valid-only generator for Result[T], ok/err, try and
the consuming match: a fixed set of helpers (try unwrapping, error propagation,
nested results, an owned payload, a consuming match, strings, a discarded try, a
loop) called with random arguments, with both the ok and error paths modelled in
Python.

    python3 obo/tests/own_fuzz.py [--count N] [--profile safe|full|both]
    python3 obo/tests/own_fuzz.py SEED [--safe]      print one program and its output
"""
import atexit
import os
import random
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
FORK = os.path.dirname(HERE)
OBO = os.environ.get('OBO', os.path.join(FORK, 'obo'))
TMP = tempfile.mkdtemp(prefix='obo-own-fuzz-')
atexit.register(shutil.rmtree, TMP, ignore_errors=True)

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
    "fn cutRef(mut n: &Node) { n.next = nil }",
    "fn eatArr(a: []Node): int { return len(a) }",
    "fn eatNode(n: Node): int { return n.value }",
    "fn holdN(n: &Node, k: int): int { return n.value + k }",
    "fn holdO(k: int, n: &Node): int { return k + n.value }",
    "fn renewAt(mut a: &[]Node, i: int): int { a[i] = mk(100 + i); return i }",
    "fn bumpAt(mut a: &[]Node, i: int): int { a[i].value += 1; return 0 }",
    "fn sumTake(l: ?Node): int { return sum(l) }",
    "fn sum2(x: ?Node, y: ?Node): int { return sum(x) + sum(y) }",
    "fn clearG(): int { gxs = new []Node(0); return 0 }",
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
    "fn setB(mut pr: &Pair) { pr.b = nil }",
    "fn setA(mut pr: &Pair) { pr.a = mk(7) }",
    "fn viaSetB(mut pr: &Pair) { setB(pr) }",
    "fn tick(mut a: &[]int): int { append(a, 1); return 0 }",
    "type SBox = ref record { s: string }",
    "fn clrSBox(mut b: &SBox): int { b.s = \"\"; let t = \"ZZZZZZZZ\" + string(len(b.s)); return len(t) - len(t) }",
    "fn clrSBoxH(h: ^SBox): int { h.s = \"\"; let t = \"ZZZZZZZZ\" + string(len(h.s)); return len(t) - len(t) }",
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
        self.hsrc = {}                          # name -> the array it was made from, xs (local) or gxs (global)
        self.olds = {}                          # name -> value returned by replace()
        self.out = []                           # the expected output lines


class Stmt:
    """One generated statement: its source lines, and what it does to the model."""

    def __init__(self, lines, run, trap=False, either=False):
        self.lines, self.run, self.trap, self.either = lines, run, trap, either


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
        self.either = False                     # the program ends in a use of a dead handle made from a local array
        self.sdead = set()                      # handles made from xs that a call with a mut array may have freed
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
        """A handle to a record's array, indexed by a call that replaces the array: rejected."""
        n = self.nrefs
        self.nrefs += 1
        self.extra_fn("fn rebox(mut b: &Box): int { b.items = new []int(0); append(b.items, 2); return 1 }")
        return self.invalid_stmt([f"var b{n} = new Box{{items: new []int(0)}}", f"let hx{n} = handle(b{n})",
                                  f"hx{n}.items[rebox(b{n})] = 9"])

    def str_index_pin_stmt(self):
        """A string field indexed by a call that clears it through a mut parameter or through a handle: the
        index holds a reference of its own, so the byte read is the one of the string as it was (valid)."""
        n = self.nrefs
        self.nrefs += 1
        k = self.k() % 7
        first = self.r.choice("abcdefgh")
        lines = [f"var sbx{n} = new SBox{{s: \"{first}bcdefg\" + string({k})}}",
                 f"println(sbx{n}.s[clrSBox(sbx{n})])",
                 f"sbx{n}.s = \"{first}bcdefg\" + string({k})",
                 f"let shx{n} = handle(sbx{n})",
                 f"println(shx{n}.s[clrSBoxH(shx{n})])"]

        def run(st):
            st.out.append(str(ord(first)))
            st.out.append(str(ord(first)))
        return Stmt(["{"] + indent(lines) + ["}"], run)

    def move_bits_stmt(self):
        """A handle's, a field's and a parameter's array indexed, appended to and assigned into by a
        value that calls a function that only appends to the array (an append updates the array's header
        in place, so nothing loaded is stale): valid."""
        n = self.nrefs
        self.nrefs += 1
        k = self.k() % 5 + 1
        lines = [f"var b{n} = new Box{{items: new []int(0)}}", f"let hx{n} = handle(b{n})",
                 f"hx{n}.items[tick(b{n}.items)] = {k}", f"b{n}.items[tick(b{n}.items)] += {k}",
                 f"append(b{n}.items, tick(b{n}.items))", f"println(b{n}.items[0] * 100 + len(b{n}.items))"]

        def run(st):
            st.out.append(str(2 * k * 100 + 4))
        return Stmt(["{"] + indent(lines) + ["}"], run)

    def target_call_stmt(self, kind):
        """A field of a local record, targeted by a compound assignment, append, truncate or an
        index, while a call in the same expression replaces the record's array: rejected."""
        n = self.nrefs
        self.nrefs += 1
        self.extra_fn("fn rebox(mut b: &Box): int { b.items = new []int(0); append(b.items, 2); return 1 }")
        head = [f"var b{n} = new Box{{items: new []int(0)}}", f"append(b{n}.items, 1)"]
        if kind == 'compound':
            tail = [f"b{n}.items[0] += rebox(b{n})"]
        elif kind == 'append':
            tail = [f"append(b{n}.items, rebox(b{n}))"]
        elif kind == 'truncate':
            tail = [f"truncate(b{n}.items, rebox(b{n}))"]
        elif kind == 'index':
            tail = [f"println(b{n}.items[rebox(b{n})])"]
        else:
            tail = [f"b{n}.items[rebox(b{n})] = 3"]
        return self.invalid_stmt(head + tail)

    def compound_target_stmt(self):
        return self.target_call_stmt('compound')

    def append_target_stmt(self):
        return self.target_call_stmt('append')

    def truncate_target_stmt(self):
        return self.target_call_stmt('truncate')

    def index_target_stmt(self):
        return self.target_call_stmt('index')

    def index_assign_target_stmt(self):
        return self.target_call_stmt('assign')

    def moved_local_stmt(self, kind):
        """A whole local array passed by value to a call in the same expression that uses the array:
        the call moves it, so the array's header is freed under the operation: rejected."""
        n = self.nrefs
        self.nrefs += 1
        self.extra_fn("fn eatInts(a: []int): int { return len(a) - 1 }")
        head = [f"var v{n} = new []int(0)", f"append(v{n}, 1)"]
        if kind == 'index':
            tail = [f"println(v{n}[eatInts(v{n})])"]
        elif kind == 'assign':
            tail = [f"v{n}[eatInts(v{n})] = 3"]
        elif kind == 'append':
            tail = [f"append(v{n}, eatInts(v{n}))"]
        elif kind == 'truncate':
            tail = [f"truncate(v{n}, eatInts(v{n}))"]
        else:
            tail = [f"let o{n} = replace(v{n}, new []int(eatInts(v{n})))"]
        return self.invalid_stmt(head + tail)

    def moved_root_index_stmt(self):
        """A field's array, indexed by a call that takes the record it is in by value: the call
        moves the record and frees its array under the index, so it is rejected. Nothing in the
        call is impure (pureFn), so only the move of the record shows it."""
        n = self.nrefs
        self.nrefs += 1
        self.extra_fn("fn eatBox(b: Box): int { return 0 }")
        return self.invalid_stmt([f"var b{n} = new Box{{items: new []int(0)}}", f"append(b{n}.items, 1)",
                                  f"println(b{n}.items[eatBox(b{n})])"])

    def replace_sink_index_stmt(self):
        """A field's array, indexed by a pure call whose argument is a replace of that array: the
        replace frees what the index holds, so the statement is rejected (a free in a held place)."""
        n = self.nrefs
        self.nrefs += 1
        self.extra_fn("fn sinkArrI(a: []int): int { return 0 }")
        return self.invalid_stmt([f"var b{n} = new Box{{items: new []int(0)}}", f"append(b{n}.items, 1)",
                                  f"println(b{n}.items[sinkArrI(replace(b{n}.items, new []int(0)))])"])

    def moved_index_stmt(self):
        return self.moved_local_stmt('index')

    def moved_assign_stmt(self):
        return self.moved_local_stmt('assign')

    def moved_append_stmt(self):
        return self.moved_local_stmt('append')

    def moved_truncate_stmt(self):
        return self.moved_local_stmt('truncate')

    def moved_replace_stmt(self):
        return self.moved_local_stmt('replace')

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
        # a handle made from the local array is tracked by its place (FORK.md, "Field and element origins"): after a
        # mut call that may free below the array it is not used again, although the model keeps its object alive
        return [h for h, o in self.st.handles.items() if o.alive and h not in self.sdead]

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
            st.refs[n][0].v += len(st.xs)          # it frees nothing, so the other references stay valid
        return Stmt([f"stepArr(xs, {n})"], run)

    # ---- argument order: the arguments of one call run left to right, so a reference taken by an earlier
    # ---- argument is held while a later one runs, and a move or free in an earlier one has happened before
    # ---- a later one looks

    def arg_order_stmt(self):
        """A call whose arguments are in an order that is valid: the argument that frees or moves comes
        first (and the reference after it is taken from what is left), or it touches nothing the reference
        names (another local, or a scalar of an element)."""
        if not self.st.xs:
            return None
        n = len(self.st.xs)
        i, j = self.r.randrange(n), self.r.randrange(n)
        kind = self.r.choice(['renew_first', 'bump', 'take_other', 'take_both', 'take_twice'])
        live = self.live()
        if kind == 'renew_first':
            def run(st):
                free(st.xs[i])
                st.xs[i] = Obj(100 + i)
                invalidate(st)
                st.out.append(str(i + st.xs[j].v))
            return Stmt([f"println(holdO(renewAt(xs, {i}), &xs[{j}]))"], run)
        if kind == 'bump':
            def run(st):
                st.xs[j].v += 1                      # a scalar: nothing is freed, so no reference is invalid
                st.out.append(str(st.xs[i].v))
            return Stmt([f"println(holdN(&xs[{i}], bumpAt(xs, {j})))"], run)
        if kind == 'take_other' and live:
            x = self.r.choice(live)

            def run(st):
                chain = st.opt[x]
                st.opt[x] = None
                free(chain)
                st.out.append(str(st.xs[i].v + chain_sum(chain)))
            return Stmt([f"println(holdN(&xs[{i}], sumTake(take({x}))))"], run)
        if kind == 'take_both' and len(live) >= 2:
            x, y = self.r.sample(live, 2)

            def run(st):
                cx, cy = st.opt[x], st.opt[y]
                st.opt[x] = st.opt[y] = None
                free(cx)
                free(cy)
                st.out.append(str(chain_sum(cx) + chain_sum(cy)))
            return Stmt([f"println(sum2(take({x}), take({y})))"], run)
        if kind == 'take_twice' and live:
            x = self.r.choice(live)

            def run(st):
                chain = st.opt[x]
                st.opt[x] = None
                free(chain)
                st.out.append(str(chain_sum(chain)))
            return Stmt([f"println(sum2(take({x}), take({x})))"], run)
        return None

    def arg_order_bad_stmt(self):
        """The same calls with the arguments the other way round, or a later argument that frees what an
        earlier one points into: rejected."""
        kinds = ['renew', 'eat', 'replace']
        if self.st.gxs:
            kinds.append('global')
        if self.live():
            kinds.append('moved')
        kind = self.r.choice(kinds)
        if kind == 'moved':
            x = self.r.choice(self.live())
            return self.invalid_stmt([f"println(sum2({x}, take({x})))"])
        if kind == 'global':
            i = self.r.randrange(len(self.st.gxs))
            return self.invalid_stmt([f"println(holdN(&gxs[{i}], clearG()))"])
        if not self.st.xs:
            return None
        i = self.r.randrange(len(self.st.xs))
        call = {'renew': f"renewAt(xs, {i})", 'eat': "eatArr(xs)",
                'replace': f"eatNode(replace(xs[{i}], mk(7)))"}[kind]
        return self.invalid_stmt([f"println(holdN(&xs[{i}], {call}))"])

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
            st.refs[n][0].v += 1                   # it frees nothing, so the other references stay valid
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

    def defer_ref_stmt(self):
        """A reference read by a defer in a block of its own; nothing is changed or re-pointed. Valid."""
        x = self.r.choice(self.live()) if self.live() else None
        if x is None:
            return None
        r = f"r{self.nrefs}"
        self.nrefs += 1

        def run(st):
            st.out.append(str(chain_sum(st.opt[x])))
        return Stmt(["{", f"    var {r} = &{x}", f"    defer println(sum({r}))", "}"], run)

    def defer_repoint_stmt(self):
        """A reference read by a defer, then pointed at another local before the block ends: invalid
        (the defer runs at the block's end and would read the new target)."""
        pr = self.pair()
        if pr is None:
            return None
        x, y = pr
        r = f"r{self.nrefs}"
        self.nrefs += 1
        return self.invalid_stmt(["{", f"    var {r} = &{x}", f"    defer println(sum({r}))",
                                  f"    {r} = &{y}", "}"])

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
        """A mutable call that frees through one reference invalidates another, which is then read."""
        if not self.st.xs:
            return None
        a, b = f"r{self.nrefs}", f"r{self.nrefs + 1}"
        self.nrefs += 2
        i = self.r.randrange(len(self.st.xs))
        call = f"cutRef({a})"                      # it frees what the node points to, below the array
        # the second reference is into the place the call frees (a reference to another element is valid)
        return self.invalid_stmt([f"var {a} = &xs[{i}]", f"var {b} = &xs[{i}].next", call,
                                  f"println(sum({b}))"])

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

    def handle_pure_mut_stmt(self):
        """A borrow through a handle, a call with a mut parameter that frees nothing (it writes an
        element's scalar), then a use of the borrow: valid."""
        h, q = f"h{self.nhandles}", f"q{self.nold}"
        self.nhandles += 1
        self.nold += 1
        v = self.k()

        def run(st):
            st.xs.append(Obj(v))
            st.xs[0].v += 1
            st.out.append(str(chain_sum(st.xs[0].next)))
        return Stmt(["{"] + indent([f"append(xs, mk({v}))", f"let {h} = handle(xs[0])", f"let {q} = &{h}.next",
                                    "bumpAt(xs, 0)", f"println(sum({q}))"]) + ["}"], run)

    def handle_freeing_mut_stmt(self):
        """The same, with a call whose mut parameter is freed below (it truncates the array): rejected."""
        h, q = f"h{self.nhandles}", f"q{self.nold}"
        self.nhandles += 1
        self.nold += 1
        return self.invalid_stmt([f"append(xs, mk({self.k()}))", f"let {h} = handle(xs[0])",
                                  f"let {q} = &{h}.next", "shrinkInt(xs)", f"println(sum({q}))"])

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
        """A change through a reference to an element, then a read through a reference into the place that
        was changed (the element's own field): invalid. A reference to another element stays valid (see
        ref_local_path_stmt)."""
        a, b = f"r{self.nrefs}", f"r{self.nrefs + 1}"
        self.nrefs += 2
        return self.invalid_stmt(["{", "    var ys = new []Node(0)", f"    append(ys, mk({self.k()}))",
                                  f"    var {a} = &ys[0]", f"    let {b} = &ys[0].next",
                                  f"    {a}.next = push(nil, 3)", f"    println(sum({b}))", "}"])

    def ref_local_path_stmt(self):
        """References to two elements of one array (or two fields of one record); a change through one,
        then a read through the other, which points at a place the change cannot free. Valid."""
        a, b = f"r{self.nrefs}", f"r{self.nrefs + 1}"
        self.nrefs += 2
        k0, k1 = self.k(), self.k()
        form = self.r.randrange(3)
        if form == 0:
            body = [f"    var {a} = &ys[0]", f"    let {b} = &ys[1]", f"    {a}.next = push(nil, 3)",
                    f"    println({b}.value)", f"    println(sum({a}))"]
            out = [str(k1), str(k0 + 3)]
        elif form == 1:
            body = [f"    var {a} = &ys[1]", f"    let {b} = &ys[0]", f"    {a}.next = push(nil, 4)",
                    f"    println({b}.value)"]
            out = [str(k0)]
        else:
            body = [f"    let {a} = &ys[0].next", f"    let {b} = &ys[1].next", "    ys[0].next = push(nil, 5)",
                    f"    println(sum({b}))"]
            out = [str(0)]
        lines = ["{", "    var ys = new []Node(0)", f"    append(ys, mk({k0}))", f"    append(ys, mk({k1}))"] + body + ["}"]

        def run(st):
            st.out.extend(out)
        return Stmt(lines, run)

    def field_origin_stmt(self):
        """A handle to an element of a local array, or to a field of a local record, a change that does not
        reach its place (another element, a sibling field, a scalar, an append, a truncate that keeps it, a mut
        call that frees below it or writes a scalar), then a read through the handle. Valid: the handle is
        tracked by its place (FORK.md, "Field and element origins") and these changes are not refused."""
        h = f"h{self.nhandles}"
        self.nhandles += 1
        k0, k1, k2 = self.k(), self.k(), self.k()
        form = self.r.randrange(8)
        arr = ["    var ys = new []Node(0)", f"    append(ys, mk({k0}))", f"    append(ys, mk({k1}))",
               f"    append(ys, mk({k2}))"]
        rec = [f"    var pr = new Pair{{a: mk({k0}), b: nil}}"]
        if form == 0:
            body = arr + [f"    let {h} = handle(ys[1])", "    ys[0] = mk(7)", "    ys[2] = mk(8)", "    append(ys, mk(9))",
                          f"    println({h}.value)"]
            out = [str(k1)]
        elif form == 1:
            body = rec + [f"    let {h} = handle(pr.a)", "    pr.b = mk(3)", "    pr.a.value += 2",
                          f"    println({h}.value)"]
            out = [str(k0 + 2)]
        elif form == 2:
            body = arr + [f"    let {h} = handle(ys[1])", "    truncate(ys, 2)", f"    println({h}.value)"]
            out = [str(k1)]
        elif form == 3:
            body = arr + [f"    let {h} = handle(ys[1])", "    cutRef(ys[1])", "    bumpRef(ys[2])", f"    println({h}.value)"]
            out = [str(k1)]
        elif form == 4:
            body = arr + ["    var i = 1", f"    let {h} = handle(ys[i])", "    bumpRef(ys[i])", "    cutRef(ys[i])",
                          f"    println({h}.value)"]
            out = [str(k1 + 1)]
        elif form == 5:
            body = rec + [f"    let {h} = handle(pr.a)", f"    pr.b = mk({k1})", "    cutRef(pr.a)",
                          f"    println({h}.value)", "    pr.a = mk(5)", f"    let g{h} = handle(pr.a)",
                          f"    println(g{h}.value)"]
            out = [str(k0), "5"]
        elif form == 7:
            body = rec + [f"    let {h} = handle(pr.a)", "    setB(pr)", "    viaSetB(pr)", f"    println({h}.value)"]
            out = [str(k0)]
        else:
            body = arr + ["    var other = new []Node(0)", "    append(other, mk(6))", f"    let {h} = handle(ys[0])",
                          "    other[0] = mk(7)", "    truncate(other, 0)", f"    println({h}.value)"]
            out = [str(k0)]
        lines = ["{"] + body + ["}"]

        def run(st):
            st.out.extend(out)
        return Stmt(lines, run)

    def field_origin_dead_stmt(self):
        """A handle to an element or a field, the place or one above it emptied (assigned over, replaced,
        truncated, reached by a mut call that frees below it, changed through a reference, or the owner
        assigned over), then a read through the handle: invalid, the handle is dead on every path."""
        h = f"h{self.nhandles}"
        self.nhandles += 1
        k0, k1 = self.k(), self.k()
        arr = ["    var ys = new []Node(0)", f"    append(ys, mk({k0}))", f"    append(ys, mk({k1}))"]
        rec = [f"    var pr = new Pair{{a: mk({k0}), b: nil}}"]
        forms = [
            arr + [f"    let {h} = handle(ys[1])", "    ys[1] = mk(7)"],
            arr + [f"    let {h} = handle(ys[0])", "    var i = 1", "    ys[i] = mk(7)"],
            arr + ["    var i = 1", f"    let {h} = handle(ys[i])", "    ys[0] = mk(7)"],
            arr + [f"    let {h} = handle(ys[1])", "    truncate(ys, 1)"],
            arr + [f"    let {h} = handle(ys[1])", "    renewAt(ys, 0)"],
            arr + [f"    let {h} = handle(ys[0])", "    shrinkInt(ys)"],
            arr + [f"    let {h} = handle(ys[0])", "    var r = &ys", "    r[0] = mk(7)"],
            arr + [f"    let {h} = handle(ys[1])", "    ys = new []Node(0)"],
            arr + [f"    let {h} = handle(ys[1])", "    let old = replace(ys[1], mk(7))", "    println(old.value)"],
            rec + [f"    let {h} = handle(pr.a)", "    pr.a = mk(7)"],
            rec + [f"    let {h} = handle(pr.a)", "    setA(pr)"],
            rec + [f"    let {h} = handle(pr.a)", "    let g = handle(pr)", "    g.a = mk(7)"],
            rec + [f"    let {h} = handle(pr.a)", "    pr = new Pair{a: mk(7), b: nil}"],
            rec + [f"    let {h} = handle(pr.a)", "    let old = replace(pr.a, mk(7))", "    println(old.value)"],
            rec + [f"    let {h} = handle(pr.a)", "    let g = handle(pr.a)", "    pr.a = mk(7)", f"    println(g.value)"],
        ]
        body = self.r.choice(forms)
        return self.invalid_stmt(["{"] + body + [f"    println({h}.value)", "}"])

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
            st.hsrc[h] = src
        return Stmt([f"let {h} = handle({src}[{i}])"], run)

    def h_dead_stmt(self):
        """Uses a handle whose object has been freed: the program stops there."""
        dead = [h for h, o in self.st.handles.items() if not o.alive]
        if not dead:
            return None
        h = self.r.choice(sorted(dead))
        # a handle made from the local array was usually freed where the compiler sees it (the program is then
        # rejected, at this use), and otherwise it traps; one made from the global array always traps
        return Stmt([f"println({h}.value)"], lambda st: None, trap=True, either=self.st.hsrc.get(h) == 'xs')

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
        makers = self.common() + [self.choose_ref_stmt] * 2 + [self.ref_local_path_stmt] + [self.field_origin_stmt] + [self.defer_ref_stmt] + [self.push_stmt] * 4 + [self.take_stmt] * 2 \
            + [self.loop_stmt, self.if_stmt, self.g_set_stmt, self.g_trunc_stmt, self.ss_loop_stmt,
               self.gdrop_stmt, self.ss_trunc_stmt, self.tag_trunc_stmt, self.h_new_stmt, self.h_new_stmt,
               self.h_kill_stmt, self.h_kill_stmt, self.handle_borrow_stmt, self.handle_pure_mut_stmt, self.move_bits_stmt, self.str_index_pin_stmt, self.owned_deep_writer_stmt,
               self.drop_stmt, self.replace_stmt, self.eat_stmt, self.if_move_stmt,
               self.loop_move_stmt, self.discard_stmt]
        if self.full:
            makers += [self.move_stmt] * 2 + [self.elem_set_stmt, self.truncate_stmt,
                                              self.ref_new_stmt, self.h_dead_stmt, self.bumpRef_stmt,
                                              self.arg_order_stmt]
            if self.r.random() < 0.1:
                s = self.ref_dead_stmt()
                if s is not None:
                    return s
            if self.r.random() < 0.04:
                s = self.mut_call_dead_stmt()
                if s is not None:
                    return s
            if self.r.random() < 0.01:
                s = self.arg_order_bad_stmt()
                if s is not None:
                    return s
            if self.r.random() < 0.12:
                s = self.arg_order_stmt()
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
               self.ro_pass_stmt, self.walk_assign_stmt, self.handle_borrow_dead_stmt, self.handle_freeing_mut_stmt,
               self.elem_write_stmt, self.two_self_stmt, self.alias_param_stmt,
               self.alias_global_elem_stmt, self.global_ref_free_stmt,
               self.handle_index_call_stmt, self.ref_move_stmt, self.callee_free_global_stmt,
               self.owned_deep_dead_stmt, self.owned_deep_arg_stmt, self.branch_free_stmt, self.defer_repoint_stmt,
               self.compound_target_stmt, self.append_target_stmt, self.truncate_target_stmt,
               self.index_target_stmt, self.index_assign_target_stmt,
               self.moved_index_stmt, self.moved_assign_stmt, self.moved_append_stmt,
               self.moved_truncate_stmt, self.moved_replace_stmt, self.moved_root_index_stmt,
               self.replace_sink_index_stmt, self.arg_order_bad_stmt, self.field_origin_dead_stmt]
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
            if re.search(r'\b(renewAt|shrinkInt|pq)\(xs\b', ' '.join(s.lines)):
                self.sdead |= {h for h, src in self.st.hsrc.items() if src == 'xs'}
            if s.trap:
                self.trapped = True
                self.either = s.either
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
    kind = 'invalid' if g.invalid else (('either' if g.either else 'trap') if g.trapped else 'valid')
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
        if kind == 'either' and 'may name a freed object' in b.stderr:
            return 'rejected'
        if kind not in ('invalid', 'either'):
            raise Fail(f"false rejection: {b.stderr.strip()}\n{path}\n{regen}")
        return 'rejected'
    if kind == 'invalid':
        raise Fail(f"missed error: obo accepted a use of moved or invalidated memory\n{path}\n{regen}")
    run = subprocess.run([exe], capture_output=True, text=True, timeout=30,
                         env=dict(os.environ, OBO_CHECK='1', OBX_GC='poison'))
    want_exit = 7 if kind in ('trap', 'either') else 0
    if run.returncode != want_exit:
        raise Fail(f"exit {run.returncode}, expected {want_exit}: {run.stderr.strip()[:300]}\n{path}\n{regen}")
    if kind in ('trap', 'either') and 'has been freed' not in run.stderr:
        raise Fail(f"exit 7 without the handle message: {run.stderr.strip()[:300]}\n{path}\n{regen}")
    got = run.stdout.splitlines()
    if got != expect:
        i = next((k for k in range(min(len(got), len(expect))) if got[k] != expect[k]), min(len(got), len(expect)))
        raise Fail(f"output line {i + 1}: expected {expect[i] if i < len(expect) else '<end>'!r}, "
                   f"got {got[i] if i < len(got) else '<end>'!r}\n{path}\n{regen}")
    return 'trapped' if kind in ('trap', 'either') else 'ran'


# ---------------------------------------------------------------- result profile
# Result[T], ok/err, try and the consuming match: only valid programs, with the
# output computed as the program is generated. The helper bodies are fixed; the
# main body calls a random mix of them with random arguments, so both the ok and
# the error path of try are reached in most seeds.
RESULT_HELPERS = [
    "type RNode = ref record { v: int }",
    "fn src(n: int): Result[int] {",
    "    if n % 4 == 3 { return err(\"e\" + string(n), n) }",
    "    return ok(n)",
    "}",
    "fn strsrc(n: int): Result[string] {",
    "    if n % 4 == 3 { return err(\"e\" + string(n), n) }",
    "    return ok(\"s\" + string(n))",
    "}",
    "fn show(r: Result[int]) {",
    "    match r {",
    "        ok { println(\"ok \", r.value) }",
    "        err { println(\"err \", r.message, \" \", r.code) }",
    "    }",
    "}",
    "fn pass(n: int, k: int): Result[int] {",
    "    let v = try src(n)",
    "    return ok(v + k)",
    "}",
    "fn nest(n: int): Result[Result[int]] {",
    "    let inner = src(n)",
    "    return ok(inner)",
    "}",
    "fn nestUse(n: int): Result[int] {",
    "    let y = try try nest(n)",
    "    return ok(y + 7)",
    "}",
    "fn ownedUse(n: int): Result[int] {",
    "    let node = try src(n)",
    "    let r = ok(new RNode{v: node})",
    "    let x = try r",
    "    return ok(x.v * 2)",
    "}",
    "fn matchUse(n: int): Result[int] {",
    "    let r = src(n)",
    "    match r {",
    "        ok(v) { return ok(v * 3) }",
    "        err(m, c) { return err(m, c) }",
    "    }",
    "}",
    "fn strUse(n: int): Result[int] {",
    "    let s = try strsrc(n)",
    "    return ok(len(s))",
    "}",
    "fn discardUse(n: int): Result[int] {",
    "    try strsrc(n)",
    "    return ok(1)",
    "}",
    "fn loopUse(lim: int): Result[int] {",
    "    var i = 0",
    "    var sum = 0",
    "    while i < lim {",
    "        let node = new RNode{v: i}",
    "        let v = try src(i)",
    "        sum += node.v + v",
    "        i += 1",
    "    }",
    "    return ok(sum)",
    "}",
]


def result_program(seed):
    """(program text, expected output lines) for one result-profile seed."""
    r = random.Random(seed * 7919 + 17)
    calls, expect = [], []

    def err(n):
        return n % 4 == 3

    def emsg(n):
        return "e" + str(n)

    def out(n, val):
        expect.append(f"err {emsg(n)} {n}" if err(n) else f"ok {val}")

    for _ in range(r.randint(3, 7)):
        kind = r.randrange(6)
        n = r.randrange(0, 31)
        if kind == 0:
            k = r.randrange(0, 10)
            calls.append(f"    show(pass({n}, {k}))")
            out(n, n + k)
        elif kind == 1:
            calls.append(f"    show(nestUse({n}))")
            out(n, n + 7)
        elif kind == 2:
            calls.append(f"    show(ownedUse({n}))")
            out(n, n * 2)
        elif kind == 3:
            calls.append(f"    show(matchUse({n}))")
            out(n, n * 3)
        elif kind == 4:
            calls.append(f"    show(strUse({n}))")
            out(n, len("s" + str(n)))
        elif kind == 5:
            if r.randrange(2) == 0:
                calls.append(f"    show(discardUse({n}))")
                out(n, 1)
            else:
                lim = r.randrange(1, 9)
                calls.append(f"    show(loopUse({lim}))")
                total, i, failed = 0, 0, False
                while i < lim:
                    if err(i):
                        expect.append(f"err {emsg(i)} {i}")
                        failed = True
                        break
                    total += i + i
                    i += 1
                if not failed:
                    expect.append(f"ok {total}")
    text = "\n".join(RESULT_HELPERS + ["fn main() {"] + calls + ["}", ""])
    return text, expect


def check_result(seed):
    """Compile and run one result-profile seed; its output and the leak check must match."""
    text, expect = result_program(seed)
    tag = "result" + str(seed)
    path = os.path.join(TMP, tag + '.obx')
    exe = os.path.join(TMP, tag)
    with open(path, 'w') as f:
        f.write(text)
    regen = f"(regenerate with: python3 obo/tests/own_fuzz.py {seed})"
    b = subprocess.run([OBO, path, '-o', exe, '--rt', FORK], capture_output=True, text=True, timeout=60)
    if b.returncode != 0:
        raise Fail(f"false rejection: {b.stderr.strip()}\n{path}\n{regen}")
    run = subprocess.run([exe], capture_output=True, text=True, timeout=30,
                         env=dict(os.environ, OBO_CHECK='1', OBX_GC='poison'))
    if run.returncode != 0:
        raise Fail(f"exit {run.returncode}: {run.stderr.strip()[:300]}\n{path}\n{regen}")
    got = run.stdout.splitlines()
    if got != expect:
        i = next((k for k in range(min(len(got), len(expect))) if got[k] != expect[k]), min(len(got), len(expect)))
        raise Fail(f"output line {i + 1}: expected {expect[i] if i < len(expect) else '<end>'!r}, "
                   f"got {got[i] if i < len(got) else '<end>'!r}\n{path}\n{regen}")
    return 'ran'


def tests(count):
    """(name, check) pairs for run_fork.py: the first count seeds of each profile."""
    out = []
    for full in (False, True):
        for seed in range(1, count + 1):
            out.append((f"own-fuzz/{'full' if full else 'safe'}/{seed}",
                        lambda s=seed, f=full: check(s, f)))
    for seed in range(1, count + 1):
        out.append((f"own-fuzz/result/{seed}", lambda s=seed: check_result(s)))
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
