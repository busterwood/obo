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

# program, most stack-slot instructions allowed, and a pattern that no instruction may match
PROGRAMS = {
    'the tight loop': (open(os.path.join(FORK, 'bench', 'loop_obo.obx')).read(), 0, None),
    'registers given back at the end of a scope': (SCOPES, 10, None),
    'the hottest local is declared last': (RANKED, 18, None),
    'a runtime call in a loop pushes no register of a local': (RUNTIME, 0, r'push +%r1[2-5]'),
    'a returned local is read where it is': (RETURN, 1, r'mov +%r1[2-5],%r(bx|8|9|1[01])'),
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
