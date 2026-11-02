#!/usr/bin/env python3
"""Combinatorial generator for SPEC 7.5 (borrows taken at the call), with a differential oracle.

Each program A makes one call whose borrow argument (or receiver, or append/replace target) is a
place P of the local `rs` (or `r`), and whose later argument M mutates that root. Its twin B
evaluates M first into a variable and then makes the call (`let t = M; call(P, t)`). Per SPEC 7.5
the two must behave alike (P's indexes are constants in range, so the check in position cannot
trap). usage: gen.py outdir  (writes A and B files; diff.py runs them)"""
import os, sys, itertools

PRELUDE = '''type In = record { ys: []int; s: str }
type R = record { v: []int; inn: In; n: int }
type V = value record { n: int; s: str }
fn mkR(k: int): R { return R{v: [k, k + 1], inn: In{ys: [k * 10, k * 10 + 1], s: "s" + k.toStr()}, n: k} }
fn (r: mut &R) push(k: int) { append(mut &r.v, k); println("push " + len(r.v).toStr() + " " + r.n.toStr()) }
fn (r: &R) show(k: int) { println("show " + len(r.v).toStr() + " " + r.inn.s + " " + k.toStr()) }
fn (i: mut &In) add(k: int) { append(mut &i.ys, k); i.s = i.s + "+"; println("add " + i.s) }
fn useI(p: mut &int, k: int) { p += k; println("useI " + p.toStr()) }
fn readI(p: &int, k: int) { println("readI " + (p + k).toStr()) }
fn useA(p: mut &[]int, k: int) { append(mut &p, k); println("useA " + len(p).toStr() + " " + p[0].toStr()) }
fn readA(p: &[]int, k: int) { println("readA " + len(p).toStr() + " " + k.toStr()) }
fn useS(p: mut &str, k: int) { p = p + k.toStr(); println("useS " + p) }
fn useR(p: mut &R, k: int) { p.n += k; p = mkR(p.n); println("useR " + p.n.toStr()) }
fn grow(rs: mut &[]R): int { var i = 0; while i < 20 { append(mut &rs, mkR(100 + i)); i += 1 }; return 1 }
fn shrink(rs: mut &[]R): int { while len(rs) > 1 { let x = pop(mut &rs) }; return 2 }
fn clear(rs: mut &[]R): int { rs = []; return 3 }
fn reset(rs: mut &[]R): int { rs = [mkR(7), mkR(8), mkR(9)]; return 4 }
fn repl(rs: mut &[]R): int { let o = replace(mut &rs[1], mkR(50)); return 5 }
fn sw(rs: mut &[]R): int { swap(mut &rs, 0, 1); swap(mut &rs, 1, 2); return 6 }
fn clrIn(rs: mut &[]R): int { var i = 0; while i < len(rs) { rs[i].v = []; rs[i].inn = In{ys: [], s: ""}; i += 1 }; return 7 }
fn growIn(rs: mut &[]R): int { var i = 0; while i < 30 { append(mut &rs[1].v, i); append(mut &rs[1].inn.ys, i); i += 1 }; return 8 }
fn resetR(r: mut &R): int { r = mkR(60); return 9 }
fn clrR(r: mut &R): int { r.v = []; r.inn.ys = []; return 10 }
fn growR(r: mut &R): int { var i = 0; while i < 30 { append(mut &r.v, i); i += 1 }; return 11 }
fn run(f: &fn(): int): int { return f() }
fn bump(i: mut &int): int { i += 1; return 12 }
'''

# place templates on the array root rs (type, text); I, J replaced by indexes
ARR_PLACES = [('R', 'rs[I]'), ('[]int', 'rs[I].v'), ('int', 'rs[I].v[J]'), ('[]int', 'rs[I].inn.ys'),
              ('int', 'rs[I].inn.ys[J]'), ('int', 'rs[I].n'), ('In', 'rs[I].inn'), ('str', 'rs[I].inn.s')]
REC_PLACES = [('[]int', 'r.v'), ('int', 'r.v[J]'), ('int', 'r.inn.ys[J]'), ('In', 'r.inn'), ('str', 'r.inn.s'),
              ('R', 'r')]
ARR_MUT = ['grow(mut &rs)', 'shrink(mut &rs)', 'clear(mut &rs)', 'reset(mut &rs)', 'repl(mut &rs)', 'sw(mut &rs)',
           'clrIn(mut &rs)', 'growIn(mut &rs)', 'len(rs)']
REC_MUT = ['resetR(mut &r)', 'clrR(mut &r)', 'growR(mut &r)', 'len(r.v)']
WRAP = ['{m}', 'if c {{ {m} }} else {{ 0 }}', 'match k {{ 1 {{ {m} }} else {{ 0 }} }}',
        'run(fn(): int {{ return {m} }})', '{m} + {m}']

def calls(t, p, m):
    if t == 'int':
        return [f'useI(mut &{p}, {m})', f'readI(&{p}, {m})']
    if t == '[]int':
        return [f'useA(mut &{p}, {m})', f'readA(&{p}, {m})', f'append(mut &{p}, {m})',
                f'let o = replace(mut &{p}, [{m}])']
    if t == 'R':
        return [f'{p}.push({m})', f'{p}.show({m})', f'useR(mut &{p}, {m})']
    if t == 'In':
        return [f'{p}.add({m})']
    if t == 'str':
        return [f'useS(mut &{p}, {m})', f'let o = replace(mut &{p}, "r" + ({m}).toStr())']

def program(decl, stmt, tail):
    return PRELUDE + 'fn main() {\n    let c = true\n    let k = 1\n' + decl + '    ' + stmt + '\n' + tail + '}\n'

ARR_DECL = '    var rs: []R = [mkR(1), mkR(2), mkR(3)]\n'
ARR_TAIL = ('    var i = 0\n    while i < len(rs) { println(rs[i].n.toStr() + " " + len(rs[i].v).toStr() + " " + '
            'len(rs[i].inn.ys).toStr() + " " + rs[i].inn.s); i += 1 }\n')
REC_DECL = '    var r = mkR(1)\n'
REC_TAIL = '    println(r.n.toStr() + " " + len(r.v).toStr() + " " + len(r.inn.ys).toStr() + " " + r.inn.s)\n'

def twin(stmt, call_m, m):
    """B: evaluate m first, then the call with t."""
    return f'let t = {m}\n    ' + call_m.replace('\x00', 't')

def main2(out):
    """Set 2: the root is a borrow parameter (rs: mut &[]R); the index is a variable i that the later
    argument may bump; optionally the call is made inside a later argument of a late call rs[0].push."""
    os.makedirs(out, exist_ok=True)
    n = 0
    muts = [m.replace('mut &rs', 'rs') for m in ARR_MUT] + ['bump(mut &i)', 'bump(mut &i) + clear(rs)']
    for (t, p0), mu, w in itertools.product(ARR_PLACES, muts, WRAP[:3]):
        p = p0.replace('I', 'i').replace('J', '1')
        m = w.format(m=mu)
        for cm in calls(t, p, '\x00'):
            pa = p.replace('rs[i]', 'rs[i0]')
            head = '    let c = true\n    let k = 1\n    var i = 1\n'
            body_a = head + '    ' + cm.replace('\x00', m) + '\n'
            body_b = head + '    let i0 = i\n    let t = ' + m + '\n    ' + cm.replace(p, pa).replace('\x00', 't') + '\n'
            for nest in (False, True):
                def prog(body):
                    if nest:      # the call made inside a later argument of a late call on rs[0]
                        return (PRELUDE + 'fn inner(rs: mut &[]R): int {\n' + body + '    return 0\n}\n'
                                'fn work(rs: mut &[]R) {\n    rs[0].push(inner(rs))\n}\nfn main() {\n' + ARR_DECL +
                                '    work(mut &rs)\n' + ARR_TAIL + '}\n')
                    return (PRELUDE + 'fn work(rs: mut &[]R) {\n' + body + '}\nfn main() {\n' +
                            ARR_DECL + '    work(mut &rs)\n' + ARR_TAIL + '}\n')
                name = f'p{n:04d}'
                open(os.path.join(out, name + 'A.obx'), 'w').write(f'// {p} | {mu} | {w} | nest={nest}\n' + prog(body_a))
                open(os.path.join(out, name + 'B.obx'), 'w').write(prog(body_b))
                n += 1
    print(n, 'pairs')

def main():
    out = sys.argv[1]
    if len(sys.argv) > 2 and sys.argv[2] == '2':
        return main2(out)
    os.makedirs(out, exist_ok=True)
    n = 0
    for places, muts, decl, tail, tag in ((ARR_PLACES, ARR_MUT, ARR_DECL, ARR_TAIL, 'a'),
                                         (REC_PLACES, REC_MUT, REC_DECL, REC_TAIL, 'r')):
        for (t, p0), mu, w in itertools.product(places, muts, WRAP):
            for I, J in ((1, 1), (2, 0)):
                p = p0.replace('I', str(I)).replace('J', str(J))
                m = w.format(m=mu)
                for ci, cm in enumerate(calls(t, p, '\x00')):
                    a = program(decl, cm.replace('\x00', m), tail)
                    b = program(decl, twin(None, cm, m), tail)
                    name = f'{tag}{n:04d}'
                    open(os.path.join(out, name + 'A.obx'), 'w').write(f'// {p} | {mu} | {w}\n' + a)
                    open(os.path.join(out, name + 'B.obx'), 'w').write(b)
                    n += 1
    print(n, 'pairs')

main()
