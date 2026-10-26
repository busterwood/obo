#!/usr/bin/env python3
"""Register allocation is a speed feature, so a program cannot tell whether it works: this checks the
code. The port (SRC, built by obo/obo) compiles three programs and the instructions with a stack-slot
operand, `(%rbp)`, are counted: none in the tight loop; none where hot locals are declared one scope
after another (a register must be free again where its block ends); and few where the hottest local
is declared last, after lukewarm ones (the ranking must keep a register for it).

    python3 obo/tests/homes_check.py"""
import os
import re
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
FORK = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(FORK, 'bench'))
import micro  # noqa: E402

SRC = os.path.join(FORK, 'src', 'obo.obo')
REF = os.path.join(FORK, 'obo')

SCOPES = """
fn main() {
    var t = 0
    for a in 0..10 {
        var x = 0
        while x < a { t += x; x += 1 }
    }
    for b in 0..10 {
        var y = 0
        while y < b { t += y; y += 1 }
    }
    for c in 0..10 {
        var z = 0
        while z < c { t += z; z += 1 }
    }
    println(t)
}
"""

RANKED = """
fn main() {
    var a = 0
    var b = 0
    var c = 0
    var d = 0
    var i = 0
    while i < 10 { a += 1; b += 1; c += 1; d += 1; i += 1 }
    var hot = 0
    var j = 0
    while j < 10 {
        var k = 0
        while k < 10 { hot += k; hot += j; hot += k; k += 1 }
        j += 1
    }
    println(a + b + c + d + hot)
}
"""

RUNTIME = """
fn main() {
    var i = 0
    var t = 0
    while i < 10 {
        t += i
        println(t)
        i += 1
    }
}
"""

RETURN = """
fn sum(n: int): int {
    var s = 0
    var i = 0
    while i < n { s += i; i += 1 }
    return s
}
fn main() { println(sum(5)) }
"""

BOOLPARAM = """
fn count(n: int, flag: bool): int {
    var t = 0
    var i = 0
    while i < n {
        if flag { t += i }
        if !flag { t -= 1 }
        i += 1
    }
    return t
}
fn main() { println(count(5, true)) }
"""

MORE = """
fn main() {
    var t = 0
    for a in 0..3 { for x in 0..a { t += x } }
    for b in 0..3 { for y in 0..b { t += y } }
    for c in 0..3 { for z in 0..c { t += z } }
    for d in 0..3 { for w in 0..d { t += w } }
    println(t)
}
"""

# the peephole (FORK.md, "Peephole pass"): `x = x op y` on a local in a register is one instruction on the
# local, in every form, also at the first instruction of a loop body (a jump target); no temporary is copied back
FOLDS = """
fn main() {
    var t = 1
    var i = 0
    while true {
        i = i + 1
        t = t + i
        t = t - 3
        t = t * i
        t = t * 3
        t = t & 0xFFFF
        if i >= 10 { break }
    }
    println(t)
}
"""
BACK = r'mov +%r(bx|8|9|1[01]),%r1[2-5]|mov +%r1[2-5],%r(bx|8|9|1[01])'

# loop heads (FORK.md, "Loop-head alignment"): the target of every backward jump is on a 32-byte boundary, in a function
# with a frame and in one without (whose entry moves, not its body), for every kind of loop
LOOPS = """
fn framed(n: int): int {
    var xs = new []int(0)
    for i in 0..n { append(xs, i) }
    var t = 0
    for x in xs { t += x }
    return t
}

fn bare(n: int): int {
    var t = 0
    var i = 0
    while i < n {
        var j = 0
        while j < i { t += j; j += 1 }
        i += 1
    }
    for k in 0..n { t += k }
    while true {
        t += 1
        if t > 100000 { break }
    }
    return t
}

type P = ref record { x: int; y: int }
var ga = new []int(0)
var gp = new P{x: 1, y: 2}
var gps = new []P(0)

fn many(n: int): int {                  // a statement runs out of registers with locals in them: compiled again with none
    var v = 1
    var i = 0
    var u = 2
    var w = 3
    while i < n { v += i; u += v; w += u; v += w; u += i; w += v; v += u; w += i; u += w; i += 1 }
    ga[i & 3] = gps[i & 1].x + gp.x * ga[i & 3]
    ga[i & 3] += gps[i & 1].x + gp.x * ga[i & 3]
    return v + u + w
}

fn main() {
    append(ga, 1)
    append(ga, 2)
    append(ga, 3)
    append(ga, 4)
    append(gps, new P{x: 1, y: 2})
    append(gps, new P{x: 3, y: 4})
    println(framed(10) + bare(10) + many(3))
}
"""
ALIGN = 32
JUMP = re.compile(r'^j\w+\s+0x([0-9a-f]+)')


def loop_heads(rows):
    heads = set()
    for addr, t in rows:
        m = JUMP.match(t)
        if m and int(m.group(1), 16) < addr:
            heads.add(int(m.group(1), 16))
    return heads


# program, most stack-slot instructions allowed, and a pattern that no instruction may match
PROGRAMS = {
    'the tight loop': (open(os.path.join(FORK, 'bench', 'loop_obo.obx')).read(), 0, BACK),
    'registers given back at the end of a scope': (SCOPES, 10, None),
    'the hottest local is declared last': (RANKED, 18, None),
    'a runtime call in a loop pushes no register of a local': (RUNTIME, 0, r'push +%r1[2-5]'),
    'a returned local is read where it is': (RETURN, 1, r'mov +%r1[2-5],%r(bx|8|9|1[01])'),
    'x = x op y is done in place': (FOLDS, 0, BACK),
    'loop heads are aligned': (LOOPS, 200, None),
    'a bool parameter is a candidate': (BOOLPARAM, 2, None),
    'the limit of a for loop is given back with its variable': (MORE, 16, None),
}


def main():
    tmp = tempfile.mkdtemp(prefix='obo-homes-')
    port = os.path.join(tmp, 'port')
    b = subprocess.run([REF, SRC, '-o', port, '--rt', FORK], capture_output=True, text=True)
    if b.returncode != 0:
        raise SystemExit('FAIL the port does not compile: ' + b.stderr)
    bad = 0
    for name, (text, most, forbid) in PROGRAMS.items():
        src = os.path.join(tmp, 'p.obx')
        open(src, 'w').write(text)
        exe = os.path.join(tmp, 'p')
        c = subprocess.run([port, src, '-o', exe, '--rt', FORK], capture_output=True, text=True)
        if c.returncode != 0:
            raise SystemExit('FAIL ' + name + ': ' + c.stderr)
        rows = micro.listing(exe)
        n = sum(1 for _, t in rows if re.search(r'\(%rbp\)', t))
        heads = loop_heads(rows)
        loose = sorted(h for h in heads if h % ALIGN)
        if loose:
            bad += 1
            print(f'FAIL {name}: the loop head at {loose[0]:#x} is not on a {ALIGN}-byte boundary')
            continue
        if name == 'loop heads are aligned' and len(heads) < 7:
            bad += 1
            print(f'FAIL {name}: only {len(heads)} loop heads found, expected at least 7')
            continue
        hit = [t for _, t in rows if forbid and re.search(forbid, t)]
        if hit:
            bad += 1
            print(f'FAIL {name}: {hit[0]}')
        elif n > most:
            bad += 1
            print(f'FAIL {name}: {n} instructions use a stack slot, at most {most} allowed')
        else:
            print(f'ok   {name}: {n} stack-slot instructions')
    sys.exit(1 if bad else 0)


if __name__ == '__main__':
    main()
