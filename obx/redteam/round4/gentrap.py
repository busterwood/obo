#!/usr/bin/env python3
"""gentrap.py outdir [n]: trap order (SPEC 7, 7.4, 7.5) against a statement-by-statement twin.

Program A makes one statement whose operands may stop the program (an index, a checked operator,
a conversion), print (a call) or change an array that a later place uses. Its twin B evaluates the
same operands one `let` at a time in the order of SPEC 7 (operands and arguments left to right, an
operator after its operands; `p = e` evaluates e first; the indexes of a borrow taken at the call
are checked in position and the place is found again at the call), so the two must print the same
lines and stop with the same exit code and message. diff.py compares them; build with gcc (its
argument order is the one that differed from clang's). SEED and n (default 3000) pick the programs."""
import os, random, sys

PRELUDE = '''type B = record { v: int }
fn (b: mut &B) add(n: int) { b.v += n; println("add " + b.v.toStr()) }
type P = record { x: int; y: int }
fn p(s: str, v: int): int { println(s); return v }
fn cl(xs: mut &[]int): int { println("cl"); xs = []; return 1 }
fn g2(a: int, b: int): int { println("g2"); return a +% b }
fn f3(a: int, b: int, c: int) { println("f3 " + a.toStr() + " " + b.toStr() + " " + c.toStr()) }
fn h(x: mut &int, y: int) { x += y; println("h " + x.toStr()) }
fn hr(x: &int, y: int) { println("hr " + x.toStr() + " " + y.toStr()) }
'''
HEAD = '''fn main() {
    var xs = [1, 2, 3]
    var xss = [[1, 2], [3]]
    var bs = [B{v: 1}, B{v: 2}]
    let s = "ab"
    var z0 = 0
    var z1 = 1
    let mx = 9223372036854775807
    let mn = 0 - 9223372036854775807 - 1
    let m1 = 0 - 1
'''
# leaves: (text, may it clear xs)
LEAVES = ['1', '2', 'k', 'xs[1]', 'xs[5]', 'xs[m1]', '10 / z0', '10 / z1', 'mx + 1', 'mn + 1', 'p("a", 2)',
          'p("b", 0)', 'xs[p("i", 7)]', '-mn', 'int(i32(mx))', 'int(i32(k))', 'int(s[4])', 'int(s[0])',
          'cl(mut &xs)', 'xs[2]']
NOCL = [x for x in LEAVES if 'cl(' not in x]


class Gen:
    def __init__(self, rng):
        self.rng, self.n = rng, 0

    def tree(self, d, idx=False):
        r = self.rng
        if d == 0 or r.random() < 0.45:
            return ('leaf', r.choice(NOCL if idx else LEAVES))
        k = r.choice(['bin', 'bin', 'bin', 'call', 'ifx', 'matchx', 'neg', 'idx', 'sidx'])
        if k == 'bin':
            return ('bin', r.choice(['+', '-', '*', '/', '%', '<<']), self.tree(d - 1, idx), self.tree(d - 1, idx))
        if k in ('call', 'ifx', 'matchx'):
            return (k, self.tree(d - 1, idx), self.tree(d - 1, idx))
        if k == 'neg':
            return ('neg', self.tree(d - 1, idx))
        return (k, self.tree(d - 1, True))

    def text(self, t):
        k = t[0]
        if k == 'leaf':
            return f'({t[1]})' if ' / ' in t[1] or ' + ' in t[1] else t[1]
        if k == 'bin':
            return f'({self.text(t[2])} {t[1]} {self.text(t[3])})'
        if k == 'call':
            return f'g2({self.text(t[1])}, {self.text(t[2])})'
        if k == 'ifx':
            return f'if c {{ {self.text(t[1])} }} else {{ {self.text(t[2])} }}'
        if k == 'matchx':
            return f'match k {{ 1 {{ {self.text(t[1])} }} else {{ {self.text(t[2])} }} }}'
        if k == 'neg':
            return f'-({self.text(t[1])})'
        if k == 'idx':
            return f'xs[{self.text(t[1])}]'
        return f'int(s[{self.text(t[1])}])'

    def var(self):
        self.n += 1
        return f't{self.n}'

    def flat(self, t, out, ind):
        """Statements (into out) that compute t one step at a time; returns the variable."""
        k, pad = t[0], '    ' * ind
        v = self.var()
        if k == 'leaf':
            out.append(f'{pad}let {v} = {t[1]}')
        elif k == 'bin':
            a, b = self.flat(t[2], out, ind), self.flat(t[3], out, ind)
            out.append(f'{pad}let {v} = {a} {t[1]} {b}')
        elif k == 'call':
            a, b = self.flat(t[1], out, ind), self.flat(t[2], out, ind)
            out.append(f'{pad}let {v} = g2({a}, {b})')
        elif k in ('ifx', 'matchx'):
            out.append(f'{pad}var {v} = 0')
            out.append(f'{pad}if {"c" if k == "ifx" else "k == 1"} {{')
            a = self.flat(t[1], out, ind + 1)
            out.append(f'{pad}    {v} = {a}')
            out.append(f'{pad}}} else {{')
            b = self.flat(t[2], out, ind + 1)
            out.append(f'{pad}    {v} = {b}')
            out.append(f'{pad}}}')
        elif k == 'neg':
            a = self.flat(t[1], out, ind)
            out.append(f'{pad}let {v} = -{a}')
        elif k == 'idx':
            a = self.flat(t[1], out, ind)
            out.append(f'{pad}let {v} = xs[{a}]')
        else:
            a = self.flat(t[1], out, ind)
            out.append(f'{pad}let {v} = int(s[{a}])')
        return v

    def stmt(self):
        """(A statement, B statements) for one random context."""
        r = self.rng
        T = lambda idx=False: self.tree(2, idx)
        ctx = r.choice(['print', 'f3', 'late', 'lateshared', 'recv', 'append', 'replace', 'swap', 'array',
                        'assign', 'compound', 'arrlit', 'reclit', 'for', 'index2'])
        B = []
        if ctx == 'print':
            e = T()
            return f'println({self.text(e)})', B + [f'    println({self.flat(e, B, 1)})']
        if ctx == 'f3':
            es = [T() for _ in range(3)]
            vs = [self.flat(e, B, 1) for e in es]
            return f'f3({", ".join(self.text(e) for e in es)})', B + [f'    f3({", ".join(vs)})']
        if ctx in ('late', 'lateshared', 'replace'):
            i, e = T(True), T()
            a = self.flat(i, B, 1)
            B.append(f'    let q{a} = xs[{a}]')
            b = self.flat(e, B, 1)
            if ctx == 'replace':
                return (f'let o = replace(mut &xs[{self.text(i)}], {self.text(e)})\n    println(o)',
                        B + [f'    let o = replace(mut &xs[{a}], {b})', '    println(o)'])
            f, m = ('h', 'mut &') if ctx == 'late' else ('hr', '&')
            return f'{f}({m}xs[{self.text(i)}], {self.text(e)})', B + [f'    {f}({m}xs[{a}], {b})']
        if ctx == 'recv':
            i, e = T(True), T()
            a = self.flat(i, B, 1)
            B.append(f'    let q{a} = bs[{a}].v')
            b = self.flat(e, B, 1)
            return f'bs[{self.text(i)}].add({self.text(e)})', B + [f'    bs[{a}].add({b})']
        if ctx == 'append':
            i, e = T(True), T()
            a = self.flat(i, B, 1)
            B.append(f'    let q{a} = len(xss[{a}])')
            b = self.flat(e, B, 1)
            tail = '    println(len(xss[0]) + len(xss[1]))'
            return (f'append(mut &xss[{self.text(i)}], {self.text(e)})\n' + tail,
                    B + [f'    append(mut &xss[{a}], {b})', tail])
        if ctx == 'swap':
            i, j = T(True), T(True)
            a, b = self.flat(i, B, 1), self.flat(j, B, 1)
            tail = '    println(xs[0] * 100 + xs[1] * 10 + xs[2])'
            return (f'swap(mut &xs, {self.text(i)}, {self.text(j)})\n' + tail,
                    B + [f'    swap(mut &xs, {a}, {b})', tail])
        if ctx == 'array':
            i, j = T(), T()
            a, b = self.flat(i, B, 1), self.flat(j, B, 1)
            tail = '    println(len(arr))'
            return (f'let arr = array({self.text(i)}, {self.text(j)})\n' + tail,
                    B + [f'    let arr = array({a}, {b})', tail])
        if ctx == 'assign':
            i, e = T(True), T()
            b = self.flat(e, B, 1)
            a = self.flat(i, B, 1)
            tail = '    println(xs[0] * 100 + xs[1] * 10 + xs[2])'
            return (f'xs[{self.text(i)}] = {self.text(e)}\n' + tail, B + [f'    xs[{a}] = {b}', tail])
        if ctx == 'compound':
            i, e = T(True), T()
            a = self.flat(i, B, 1)
            B.append(f'    let old{a} = xs[{a}]')
            b = self.flat(e, B, 1)
            B.append(f'    let new{a} = old{a} {r.choice("+-*")} {b}')
            op = B[-1].split()[-2]
            tail = '    println(xs[0] * 100 + xs[1] * 10 + xs[2])'
            return (f'xs[{self.text(i)}] {op}= {self.text(e)}\n' + tail, B + [f'    xs[{a}] = new{a}', tail])
        if ctx == 'arrlit':
            es = [T() for _ in range(3)]
            vs = [self.flat(e, B, 1) for e in es]
            tail = '    println(arr[0] +% arr[1] +% arr[2])'
            return (f'let arr = [{", ".join(self.text(e) for e in es)}]\n' + tail,
                    B + [f'    let arr = [{", ".join(vs)}]', tail])
        if ctx == 'reclit':
            e1, e2 = T(), T()
            a, b = self.flat(e1, B, 1), self.flat(e2, B, 1)
            tail = '    println(pp.x * 10 +% pp.y)'
            return (f'let pp = P{{y: {self.text(e1)}, x: {self.text(e2)}}}\n' + tail,
                    B + [f'    let pp = P{{y: {a}, x: {b}}}', tail])
        if ctx == 'for':
            e1, e2 = T(), T()
            a, b = self.flat(e1, B, 1), self.flat(e2, B, 1)
            body = ' { n += 1; if n > 3 { break }; println(i) }'
            return (f'var n = 0\n    for i in {self.text(e1)}..{self.text(e2)}' + body,
                    B + ['    var n = 0', f'    for i in {a}..{b}' + body])
        i, j = T(True), T(True)
        a = self.flat(i, B, 1)
        B.append(f'    let q{a} = len(xss[{a}])')
        b = self.flat(j, B, 1)
        return f'println(xss[{self.text(i)}][{self.text(j)}])', B + [f'    println(xss[{a}][{b}])']


def main():
    out = sys.argv[1]
    n = int(sys.argv[2]) if len(sys.argv) > 2 else 3000
    os.makedirs(out, exist_ok=True)
    rng = random.Random(int(os.environ.get('SEED', '7')))
    for x in range(n):
        g = Gen(rng)
        a, b = g.stmt()
        head = HEAD + f'    let c = {rng.choice(["true", "false"])}\n    let k = {rng.choice([1, 2])}\n'
        A = PRELUDE + head + '    ' + a + '\n    println("end")\n}\n'
        Bp = PRELUDE + head + '\n'.join(b) + '\n    println("end")\n}\n'
        open(os.path.join(out, f't{x:04d}A.obx'), 'w').write(f'// {a.splitlines()[0]}\n' + A)
        open(os.path.join(out, f't{x:04d}B.obx'), 'w').write(Bp)
    print(n, 'pairs')


main()
