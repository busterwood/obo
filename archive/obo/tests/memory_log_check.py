#!/usr/bin/env python3
"""Checked-log regression corpus for the compiler's nonFreshLog lifecycle.

Builds a private compiler with NF_CHECK=true (the production constant is false),
then compiles and runs sources that exercise a pre-loop defer slice, loop
truncation, nested lambda islands with a non-empty outer log, break/continue,
try/Result, defer, homes and many literal bodies. Any NFCHECK range/orphan/
cleanup report fails the check.

    python3 obo/tests/memory_log_check.py
"""
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
FORK = os.path.dirname(HERE)
SEED = os.path.join(FORK, 'seed', 'obo')
SRC = os.environ.get('OBO_MUTANT_SRC', os.path.join(FORK, 'src', 'obo.obo'))
RT = FORK


def run(cmd, **kw):
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=300, **kw)
    if p.returncode != 0:
        raise SystemExit('FAIL ' + ' '.join(cmd) + '\n' + (p.stderr or p.stdout))
    return p


def source_text():
    return open(SRC).read().replace('const NF_CHECK = false', 'const NF_CHECK = true', 1)


def heavy(n):
    out = ['var G = 0', 'fn one(): Result[int] { return ok(1) }', 'fn main() {']
    for i in range(n):
        if i % 4 == 0:
            out += [f'    let f{i} = fn (x: int): int {{',
                    '        var j = 0',
                    '        while j < x {',
                    '            if j == 2 { j += 1; continue }',
                    '            if j == 5 { break }',
                    '            G += j',
                    '            j += 1',
                    '        }',
                    '        defer { G += 1 }',
                    '        return j',
                    '    }']
        elif i % 4 == 1:
            out += [f'    let f{i} = fn (x: int): Result[int] {{',
                    '        let v = try one()',
                    '        return ok(v + x)',
                    '    }']
        elif i % 4 == 2:
            out += [f'    let f{i} = fn (x: int): int {{',
                    '        let inner = fn (y: int): int { return y + 1 }',
                    '        return x',
                    '    }']
        else:
            out += [f'    let f{i} = fn (x: int): int {{',
                    '        var a = 0',
                    '        var b = 1',
                    '        var j = 0',
                    '        while j < x { a += b; b = a - b; j += 1 }',
                    '        return a',
                    '    }']
    out += ['    println(G)', '}']
    return '\n'.join(out) + '\n'


def main():
    d = tempfile.mkdtemp(prefix='memlog-')
    checked = os.path.join(d, 'obo-nfcheck')
    src = os.path.join(d, 'obo-nfcheck.obo')
    open(src, 'w').write(source_text())
    run([SEED, src, '-o', checked, '--rt', RT])

    cases = {
        'defer-before-loop': '''type Item = ref record { v: int; kids: []Item }
fn main() {
    var a = new Item{v: 1, kids: new []Item(0)}
    var k = &a
    defer { a.kids = new []Item(0) }
    var i = 0
    while i < 2 { k = &a; i += 1 }
    println(k.v)
}
''',
        'island-outer-log': '''type Node = ref record { n: int }
fn main() {
    var a = new Node{n: 0}
    var r = &a
    var i = 0
    while i < 3 {
        a = new Node{n: i}
        let f = fn (x: int): int {
            var j = 0
            while j < x { j += 1 }
            defer { j += 1 }
            return j
        }
        i += 1
    }
    println(0)
}
''',
        'break-continue-try': '''var G = 0
fn one(): Result[int] { return ok(1) }
fn work(): Result[int] {
    var i = 0
    while i < 5 {
        defer { G += 1 }
        if i == 1 { i += 1; continue }
        if i == 4 { break }
        let v = try one()
        G += v
        i += 1
    }
    return ok(G)
}
fn main() {
    let w = work()
    match w {
        ok(v) { println(v) }
        err(m, c) { println(m) }
    }
}
''',
        'many-lambdas': heavy(48),
    }
    for name, text in cases.items():
        p = os.path.join(d, name + '.obx')
        exe = os.path.join(d, name)
        open(p, 'w').write(text)
        run([checked, p, '-o', exe, '--rt', RT, '--sym-dir', os.path.join(d, name + '-sym')])
        run([exe, os.path.join(d, name + '-run')])
    print('memory log check: ok (%d checked sources)' % len(cases))


if __name__ == '__main__':
    main()
