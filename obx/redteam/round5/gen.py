#!/usr/bin/env python3
"""Random programs for SPEC 7.8 (return/break/continue as a whole branch of an if or match
expression) and SPEC 9.1 (a variable moved on some paths of an if or match is dropped on the
others), round 5.

Each program has one function f(k) whose statements nest if/match expressions (with exit arms
in the middle of calls, record and array literals, sums), if statements, for and while loops,
consumed and borrowed match scrutinees, and moves of owned locals on some paths. The branch
decisions are bits of k; main runs f for every k. Every owned value (D) prints when it is made
(+v), taken (tv) and dropped (~v). The generator tracks moves so that the programs are meant to
compile. There is no reference model: the oracle (rt.py, `// expect-any`) is a clean ASan+UBSan
run with OBX_CHECK=1 (a double drop is a double free, a missed one a leak, a use after a drop a
use after free) and the native build printing exactly what the C build prints.

usage: gen.py outdir [count] [seed]
"""
import os, random, sys

PRELUDE = '''type D = record { v: int }
fn (d: mut &D) drop() { print("~" + intToStr(d.v) + " ") }
fn mk(v: int): D {
    print("+" + intToStr(v) + " ")
    return D{v: v}
}
fn take(d: D): int {
    print("t" + intToStr(d.v) + " ")
    return d.v
}
fn rd(d: &D): int { return d.v }
fn pair(a: D, b: int): int { return a.v + b }
fn two(a: D, b: D): int { return a.v + b.v }
fn add(a: int, b: int): int { return a + b }
fn (d: &D) plus(n: int): int { return d.v + n }
fn (d: D) into(n: int): int { return d.v * 2 + n }
type R = record { a: D; b: int; c: D }
fn sumR(r: R): int { return r.a.v + r.b + r.c.v }
fn sumA(xs: []D): int {
    var t = 0
    for x in xs { t += x.v }
    return t
}
type E = closed { A(D); B; C(int, D) }
fn mkE(k: int, v: int): E {
    if k % 3 == 0 { return E.A(mk(v)) }
    if k % 3 == 1 { return E.B }
    return E.C(v, mk(v + 1))
}
fn bit(k: int, j: int): bool { return (k >> j) & 1 == 1 }
fn s(n: int): str { return intToStr(n) }
'''


class G:
    def __init__(self, rnd):
        self.r = rnd
        self.ids = 0
        self.nvars = 0
        self.nbits = 0

    def id(self):
        self.ids += 1
        return self.ids * 10

    def bitc(self):
        j = self.r.randrange(6)
        self.nbits = max(self.nbits, j + 1)
        return ('' if self.r.random() < 0.7 else '!') + f'bit(k, {j})'

    # st: {'alive': {name: loopdepth}, 'loop': depth, 'inwhilecond': bool}
    def movable(self, st):
        return [v for v, d in st['alive'].items() if d == st['loop']]

    def readable(self, st):
        return list(st['alive'].keys())

    def exitarm(self, st):
        opts = ['return acc', 'return -1']
        if st['loop'] > 0:
            opts += ['break', 'continue']
        return self.r.choice(opts)

    def branch2(self, st, gen, depth):
        """Two branches of an if expression: (textA, textB), joining the states."""
        sa = clone(st)
        sb = clone(st)
        ea = self.r.random() < 0.25
        eb = (not ea) and self.r.random() < 0.25
        a = self.exitarm(sa) if ea else gen(sa, depth + 1)
        b = self.exitarm(sb) if eb else gen(sb, depth + 1)
        live = [x for x, e in ((sa, ea), (sb, eb)) if not e]
        join(st, live)
        return a, b

    def arms(self, st, gen, depth, n, bind=None):
        """n arm bodies for a match expression (one may exit), joining the states.
        bind: per arm, the owned binding the arm declares (or None)."""
        outs, live = [], []
        ex = self.r.randrange(n) if self.r.random() < 0.4 else -1
        for i in range(n):
            s2 = clone(st)
            b = bind[i] if bind else None
            if b:
                s2['alive'][b] = s2['loop']
            if i == ex:
                outs.append(self.exitarm(s2))
            else:
                outs.append(gen(s2, depth + 1))
                if b:
                    s2['alive'].pop(b, None)     # the arm's own binding: dropped at its end if kept
                live.append(s2)
        join(st, live)
        return outs

    def iexpr(self, st, depth=0):
        r = self.r
        leaf = depth >= 3 or r.random() < 0.3
        if leaf:
            opts = ['lit', 'mkv', 'mkv']
            if self.readable(st):
                opts += ['field', 'rd']
            if self.movable(st):
                opts += ['take', 'take']
            k = r.choice(opts)
            if k == 'lit':
                return str(r.randrange(5))
            if k == 'mkv':
                return f'mk({self.id()}).v'
            if k == 'field':
                return r.choice(self.readable(st)) + '.v'
            if k == 'rd':
                v = r.choice(self.readable(st))
                return r.choice([f'rd(&{v})', f'{v}.plus(1)'])
            v = r.choice(self.movable(st))
            del st['alive'][v]
            return f'take({v})'
        k = r.choice(['pair', 'two', 'add', 'plus', 'sumR', 'sumA', 'ifx', 'ifx', 'mx', 'mxe', 'recv', 'into',
                      'str'])
        if k == 'pair':
            a = self.dexpr(st, depth + 1)
            return f'pair({a}, {self.iexpr(st, depth + 1)})'
        if k == 'two':
            a = self.dexpr(st, depth + 1)
            return f'two({a}, {self.dexpr(st, depth + 1)})'
        if k == 'add':
            a = self.iexpr(st, depth + 1)
            return f'add({a}, {self.iexpr(st, depth + 1)})'
        if k == 'plus':
            a = self.iexpr(st, depth + 1)
            return f'({a} + {self.iexpr(st, depth + 1)})'
        if k == 'sumR':
            a = self.dexpr(st, depth + 1)
            b = self.iexpr(st, depth + 1)
            return f'sumR(R{{a: {a}, b: {b}, c: {self.dexpr(st, depth + 1)}}})'
        if k == 'sumA':
            xs = [self.dexpr(st, depth + 1) for _ in range(r.randrange(1, 4))]
            return f'sumA([{", ".join(xs)}])'
        if k == 'ifx':
            c = self.bitc()
            a, b = self.branch2(st, self.iexpr, depth)
            return f'(if {c} {{ {a} }} else {{ {b} }})'
        if k == 'mx':
            a, b, c = self.arms(st, self.iexpr, depth, 3)
            return f'(match k % 3 {{ 0 {{ {a} }} 1 {{ {b} }} else {{ {c} }} }})'
        if k == 'mxe':
            # a consumed scrutinee with owned bindings
            self.nvars += 1
            d1 = f'd{self.nvars}'
            self.nvars += 1
            d2 = f'd{self.nvars}'
            a, b, c = self.arms(st, self.iexpr, depth, 3, [d1, None, d2])
            return (f'(match mkE(k + {r.randrange(3)}, {self.id()}) {{ E.A({d1}) {{ {a} }} E.B {{ {b} }} '
                    f'E.C(_, {d2}) {{ {c} }} }})')
        if k == 'recv':
            d = self.dexpr(st, depth + 1)
            return f'{d}.plus({self.iexpr(st, depth + 1)})'
        if k == 'into':
            d = self.dexpr(st, depth + 1)
            return f'{d}.into({self.iexpr(st, depth + 1)})'
        a = self.iexpr(st, depth + 1)
        return f'len(s({a}) + s({self.iexpr(st, depth + 1)}))'

    def dexpr(self, st, depth=0):
        r = self.r
        leaf = depth >= 3 or r.random() < 0.45
        if leaf:
            if self.movable(st) and r.random() < 0.3:
                v = r.choice(self.movable(st))
                del st['alive'][v]
                return v
            return f'mk({self.id()})'
        k = r.choice(['ifx', 'mx'])
        if k == 'ifx':
            c = self.bitc()
            a, b = self.branch2(st, self.dexpr, depth)
            return f'(if {c} {{ {a} }} else {{ {b} }})'
        a, b = self.arms(st, self.dexpr, depth, 2)
        return f'(match k % 2 {{ 0 {{ {a} }} else {{ {b} }} }})'

    def stmts(self, st, ind, n, depth):
        out = []
        for _ in range(n):
            out += self.stmt(st, ind, depth)
        return out

    def stmt(self, st, ind, depth):
        r = self.r
        p = '    ' * ind
        opts = ['acc', 'acc', 'let', 'letd', 'print']
        if depth < 2:
            opts += ['if', 'if', 'for', 'while']
        k = r.choice(opts)
        if k == 'acc':
            return [p + f'acc += {self.iexpr(st)}']
        if k == 'print':
            return [p + f'print(s({self.iexpr(st)}) + " ")']
        if k == 'let':
            self.nvars += 1
            y = f'y{self.nvars}'
            return [p + f'let {y} = {self.iexpr(st)}', p + f'acc += {y}']
        if k == 'letd':
            e = self.dexpr(st)
            self.nvars += 1
            st['alive'][f'x{self.nvars}'] = st['loop']
            return [p + f'let x{self.nvars} = {e}']
        if k == 'if':
            c = self.bitc()
            sa = clone(st)
            sb = clone(st)
            a = self.stmts(sa, ind + 1, r.randrange(1, 3), depth + 1)
            drop_own(sa, st)
            if r.random() < 0.3:
                join(st, [sa, sb])
                return [p + f'if {c} {{'] + a + [p + '}']
            b = self.stmts(sb, ind + 1, r.randrange(1, 3), depth + 1)
            drop_own(sb, st)
            join(st, [sa, sb])
            return [p + f'if {c} {{'] + a + [p + '} else {'] + b + [p + '}']
        if k == 'for':
            s2 = clone(st)
            s2['loop'] += 1
            self.nvars += 1
            i = f'i{self.nvars}'
            body = self.stmts(s2, ind + 1, r.randrange(1, 4), depth + 1)
            return [p + f'for {i} in 0..{r.randrange(1, 4)} {{'] + body + [p + '}']
        self.nvars += 1
        j = f'j{self.nvars}'
        s2 = clone(st)
        s2['loop'] += 1
        body = self.stmts(s2, ind + 1, r.randrange(1, 3), depth + 1)
        return ([p + f'var {j} = 0', p + f'while {j} < {r.randrange(1, 4)} {{', p + f'    {j} += 1'] + body +
                [p + '}'])

    def program(self):
        st = {'alive': {}, 'loop': 0}
        body = self.stmts(st, 1, self.r.randrange(2, 6), 0)
        n = 1 << max(self.nbits, 1)
        lines = ['// generated by gen.py (round 5): SPEC 7.8 exit arms and SPEC 9.1 moves on some paths',
                 '// expect-any', PRELUDE.rstrip('\n'), 'fn f(k: int): int {', '    var acc = 0'] + body
        lines += ['    return acc', '}', 'fn main() {', f'    for k in 0..{n} {{', '        print(s(k) + ": ")',
                  '        let r = f(k)', '        println("= " + s(r))', '    }', '}']
        return '\n'.join(lines) + '\n'


def clone(st):
    return {'alive': dict(st['alive']), 'loop': st['loop']}


def drop_own(inner, outer):
    """Variables declared inside a block are gone at its end."""
    for v in list(inner['alive']):
        if v not in outer['alive']:
            del inner['alive'][v]


def join(st, live):
    """After the paths in live join: alive only if alive on every live path (a partial move is a
    move after the join); no live path: keep the state (the generator never makes all exit)."""
    if not live:
        return
    keep = {v: d for v, d in st['alive'].items() if all(v in s['alive'] for s in live)}
    st['alive'] = keep


def main():
    out = sys.argv[1]
    count = int(sys.argv[2]) if len(sys.argv) > 2 else 100
    seed = int(sys.argv[3]) if len(sys.argv) > 3 else 5
    os.makedirs(out, exist_ok=True)
    for i in range(count):
        g = G(random.Random(seed * 100003 + i))
        with open(os.path.join(out, f'g{seed}_{i:04d}.obx'), 'w') as f:
            f.write(g.program())


if __name__ == '__main__':
    main()
