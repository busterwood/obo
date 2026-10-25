#!/usr/bin/env python3
"""Register allocation must never make a program fail to compile that compiles without it. Four hot
locals leave five temporaries, and a statement needs more of them the deeper its value nests and the more
it holds while the value is computed (an element's or field's base and index, the earlier arguments of a
builtin call). This compares obo with the same compiler built with REG_HOMES = 0 (make obo-noregs) on
generated statements at the edge: each destination shape with the value nested 1 to 12 deep, and a
fixed number of random ones (right-nested, mixed, several destinations). The outcomes must agree: both
compile and run alike, or both refuse (main itself runs out of registers beyond a depth of about 7).

    python3 obo/tests/homes_edge.py [random programs, default 400]"""
import os
import random
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
FORK = os.path.dirname(HERE)
SRC = os.path.join(FORK, 'src', 'obo.obo')          # the compiler under test (mutate_port.py points it at a mutant)

SHAPES = [
    'ga[i & 3] = %s', 'ga[i & 3] += %s', 'gp.x = %s', 'gp.y += %s', 'r.y = ga[i & 3] * 3 + %s',
    'ga[ga[i & 3] & 3] = %s', 'ga[ga[i & 3] & 3] += %s', 'ga[(%s) & 3] = %s', 'gps[i & 1].x = %s',
    'gps[i & 1].y = gps[(i + 1) & 1].x * 2 + %s', 'rr.inner.y = %s', 'append(ga, ga[i & 3] + %s)',
    'g = len(substr(s, i & 1, 2 + ((%s) & 3)))', 'g = len(substr(s, (%s) & 1, 2 + ((%s) & 3)))',
    'let q = new P{x: 1, y: %s}\n    g = q.y', 'g = id(%s)', 'if %s > 3 { g = 1 }',
    'if i > 3 || ga[i & 3] > %s { g = 1 }', 'ga[i & 3] = id(%s) + id(%s)',
]
HDR = '''type P = ref record { x: int; y: int }
type R = ref record { inner: P }
var ga = new []int(0)
var gp = new P{x: 1, y: 2}
var gps = new []P(0)
var g = 0
var s = "abcdefgh"
fn id(a: int): int { return a }
fn f(n: int): int {
    var v = 1
    var i = 0
    var u = 2
    var w = 3
    let r = new P{x: 1, y: 2}
    let rr = new R{inner: new P{x: 1, y: 1}}
    while i < n { v += i; u += v; w += u; i += 1 }
    %s
    return v + u + w + g + r.y + rr.inner.y + len(ga) + gp.x + gp.y + gps[0].x + gps[1].y
}
fn main() {
    for q in 0..4 { append(ga, q) }
    append(gps, new P{x: 1, y: 2})
    append(gps, new P{x: 3, y: 4})
    println(f(3))
    println(ga[3], gp.x, gps[0].x)
}
'''


def nested(r, k):
    e = 'v*2'
    for _ in range(k):
        form = r.choice(['(v*2 + %s)', '(%s + u*w)', '(i - %s)', '(w * (%s))', '(%s %% 1000003)']) if r else '(v*2 + %s)'
        e = form % e
    return e


OPERANDS = ['ga[i & 3]', 'ga[(i + 1) & 3]', 'ga[ga[i & 3] & 3]', 'gp.x', 'gp.y', 'gps[i & 1].x', 'gps[(i + 1) & 1].y',
            'rr.inner.y', 'r.x', 'len(s)', 'v', 'u * w', 'i', '3']
DESTS = ['ga[i & 3] = ', 'ga[i & 3] += ', 'ga[(i + 1) & 3] -= ', 'ga[ga[i & 3] & 3] = ', 'gp.x = ', 'gp.y += ',
         'gps[i & 1].x = ', 'rr.inner.y = ', 'r.y = ', 'g = ']


def flat(r):
    """A destination and a chain of 2 to 9 operands joined by + - * and && free of parentheses, some lines
    broken after an operator, with a comment there now and then."""
    s = r.choice(DESTS)
    for j in range(r.randint(2, 9)):
        if j:
            s += ' ' + r.choice(['+', '-', '*', '+', '*'])
            s += r.choice([' ', ' ', '\n        ', ' // goes on\n        ']) if r.random() < 0.4 else ' '
        s += r.choice(OPERANDS)
    return s


def outcome(exe, src, out):
    c = subprocess.run([exe, src, '-o', out, '--rt', FORK], capture_output=True, text=True)
    if c.returncode != 0:
        return ('refused', c.stderr.replace(src, '').strip())
    r = subprocess.run([out], capture_output=True, text=True, env=dict(os.environ, OBO_CHECK='1'))
    return ('ran', r.returncode, r.stdout, r.stderr)


def main():
    n_random = int(sys.argv[1]) if len(sys.argv) > 1 else 400
    subprocess.run(['make', '-C', FORK, 'obo', 'obo-noregs'], check=True, stdout=subprocess.DEVNULL)
    noregs = os.path.join(FORK, 'obo-noregs')
    tmp = tempfile.mkdtemp(prefix='obo-edge-')
    ref = os.path.join(FORK, 'obo')
    if SRC != os.path.join(FORK, 'src', 'obo.obo'):
        ref = os.path.join(tmp, 'mutant')
        b = subprocess.run([os.path.join(FORK, 'obo'), SRC, '-o', ref, '--rt', FORK], capture_output=True, text=True)
        if b.returncode != 0:
            raise SystemExit('FAIL the mutant does not compile: ' + b.stderr)
    bodies = []
    for shape in SHAPES:
        for k in range(1, 13):
            e = nested(None, k)
            bodies.append((f'{shape!r} depth {k}', shape % ((e, e) if shape.count('%s') == 2 else (e,))))
    rnd = random.Random(7)
    for t in range(n_random):
        shape = rnd.choice(SHAPES)
        parts = tuple(nested(rnd, rnd.randint(1, 9)) for _ in range(shape.count('%s')))
        bodies.append((f'random {t}: {shape!r}', shape % parts))
    for t in range(n_random):
        bodies.append((f'flat chain {t}', flat(rnd)))
    bad = refused = 0
    for name, body in bodies:
        src = os.path.join(tmp, 'p.obx')
        open(src, 'w').write(HDR % body)
        a = outcome(noregs, src, os.path.join(tmp, 'a'))
        b = outcome(ref, src, os.path.join(tmp, 'b'))
        refused += a[0] == 'refused'
        if a != b:
            bad += 1
            print(f'FAIL {name}: without registers {a[:2]}, with {b[:2]}')
    print(f'{len(bodies)} statements, {refused} refused by both, {bad} differ')
    sys.exit(1 if bad else 0)


if __name__ == '__main__':
    main()
