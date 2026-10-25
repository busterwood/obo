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

# program, most stack-slot instructions allowed
PROGRAMS = {
    'the tight loop': (open(os.path.join(FORK, 'bench', 'loop_obo.obx')).read(), 0),
    'registers given back at the end of a scope': (SCOPES, 10),
    'the hottest local is declared last': (RANKED, 18),
}


def main():
    tmp = tempfile.mkdtemp(prefix='obo-homes-')
    port = os.path.join(tmp, 'port')
    b = subprocess.run([REF, SRC, '-o', port, '--rt', FORK], capture_output=True, text=True)
    if b.returncode != 0:
        raise SystemExit('FAIL the port does not compile: ' + b.stderr)
    bad = 0
    for name, (text, most) in PROGRAMS.items():
        src = os.path.join(tmp, 'p.obx')
        open(src, 'w').write(text)
        exe = os.path.join(tmp, 'p')
        c = subprocess.run([port, src, '-o', exe, '--rt', FORK], capture_output=True, text=True)
        if c.returncode != 0:
            raise SystemExit('FAIL ' + name + ': ' + c.stderr)
        n = sum(1 for _, t in micro.listing(exe) if re.search(r'\(%rbp\)', t))
        if n > most:
            bad += 1
            print(f'FAIL {name}: {n} instructions use a stack slot, at most {most} allowed')
        else:
            print(f'ok   {name}: {n} stack-slot instructions')
    sys.exit(1 if bad else 0)


if __name__ == '__main__':
    main()
