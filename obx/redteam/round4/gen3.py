#!/usr/bin/env python3
"""gen3.py outdir: if and match expressions (SPEC 7.8) against the statement forms.

Program A uses an if or match expression X in some context. Its twin B replaces X by a call to a
generated function that does the same with the statement form (`if c { return a } else { return b }`
or `match k { 1 { return a } ... }`). Per SPEC 7.8 (only the chosen branch runs, its temporaries are
dropped when it ends after its value is taken, the value is a temporary of the statement) the two
print the same lines, drops included. diff.py compares them under ASan and the leak check."""
import os, sys, itertools, random
RNG = random.Random(int(os.environ.get('SEED', '4')))
KEEP = float(os.environ.get('KEEP', '0.22'))  # the fraction of the combinations written

PRELUDE = '''type D = record { s: str }
fn (d: mut &D) drop() { println("drop " + d.s) }
fn mk(s: str): D { println("mk " + s); return D{s: s} }
fn show(s: str): str { println("show " + s); return s }
fn id(d: D): D { println("id " + d.s); return d }
fn cnt(s: str): int { println("cnt " + s); return len(s) }
fn take(a: D, x: str, b: D) { println("take " + a.s + x + b.s) }
fn takeD(a: D, x: D, b: D) { println("takeD " + a.s + x.s + b.s) }
fn takeI(a: D, x: int, b: D) { println("takeI " + a.s + x.toStr() + b.s) }
'''

BR = {
    'str': ['"lit"', 'mk("a").s', 'mk("a").s + mk("b").s', 'show("x")', 'mk(show("y")).s', 'show(mk("w").s)'],
    'D': ['mk("a")', 'id(mk("b"))', 'mk(show("q"))', 'mk(mk("r").s + "!")'],
    'int': ['1', 'cnt("n")', 'len(mk("a").s)', 'cnt(mk("m").s) + cnt("o")'],
}
CTX = {
    'str': ['let r = {X}\n    println("r " + r)', 'println({X} + mk("z").s)', 'take(mk("p"), {X}, mk("q"))',
            'let n = len({X})\n    println(n)'],
    'D': ['let r = {X}\n    println("r " + r.s)', 'println(({X}).s + mk("z").s)', 'takeD(mk("p"), {X}, mk("q"))',
          'let n = len(({X}).s)\n    println(n)'],
    'int': ['let r = {X}\n    println(r)', 'println({X} + cnt(mk("z").s))', 'takeI(mk("p"), {X}, mk("q"))'],
}

def main():
    out = sys.argv[1]
    os.makedirs(out, exist_ok=True)
    n = 0
    for t in BR:
        for (a, b, e), ctx, form in itertools.product(itertools.product(BR[t], BR[t], BR[t][:3]), CTX[t],
                                                     ('if', 'match', 'nested')):
            if form == 'if':
                X = f'if c {{ {a} }} else {{ {b} }}'
                F = f'fn pick(c: bool, k: int): {t} {{\n    if c {{ return {a} }} else {{ return {b} }}\n}}\n'
            elif form == 'match':
                X = f'match k {{ 1 {{ {a} }} 2 {{ {b} }} else {{ {e} }} }}'
                F = (f'fn pick(c: bool, k: int): {t} {{\n    match k {{ 1 {{ return {a} }} 2 {{ return {b} }} '
                     f'else {{ return {e} }} }}\n}}\n')
            else:
                X = f'if c {{ match k {{ 1 {{ {a} }} else {{ {b} }} }} }} else {{ {e} }}'
                F = (f'fn pick2(k: int): {t} {{\n    match k {{ 1 {{ return {a} }} else {{ return {b} }} }}\n}}\n'
                     f'fn pick(c: bool, k: int): {t} {{\n    if c {{ return pick2(k) }} else {{ return {e} }}\n}}\n')
            for c, k in (('true', 1), ('false', 2), ('true', 3)):
                head = f'fn main() {{\n    let c = {c}\n    let k = {k}\n    '
                A = PRELUDE + head + ctx.replace('{X}', X) + '\n    println("end")\n}\n'
                B = PRELUDE + F + head + ctx.replace('{X}', 'pick(c, k)') + '\n    println("end")\n}\n'
                n += 1
                if RNG.random() > KEEP:
                    continue
                name = f'x{n:04d}'
                open(os.path.join(out, name + 'A.obx'), 'w').write(f'// {t} {form} c={c} k={k}\n' + A)
                open(os.path.join(out, name + 'B.obx'), 'w').write(B)
    print(len(os.listdir(out)) // 2, 'pairs of', n)

main()
