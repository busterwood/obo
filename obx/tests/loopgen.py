#!/usr/bin/env python3
"""Generated stress test of array loops, for the native backend's loop pass (compiler/loops.obx,
design/native.md section 8, the sixth performance step).

usage: python3 obx/tests/loopgen.py [--compiler PATH] [-n N] [--seed S] [-j N] [--keep DIR]

Each program runs loops over int and byte arrays (while with steps of 1 to 3 up or down, `<=`
bounds, for ranges, for over elements, nested loops, steps held in a local that may be 0,
negative, huge or changed in the loop, sieves over a fresh `array(m, v)` whose inner loop steps
by the outer index and may run past the end) whose bodies read and write elements at
the index and near it, store then load, append, pop, reassign the array, pass it `mut &` to
functions that grow or shrink it, change the index or the bound, break and continue, and call a
closure that appends to the array it captures. Some programs also have functions that loop over
two `mut &` records (the eighth performance step's loads out of loops), reading their fields,
arrays and a child record's, and writing them in every way: stores, methods, a `mut &` to a
member, appends, a whole record replaced, recursion, a closure, an Option field set and matched.
Many programs end in a bounds or overflow trap
partway, with output before it. It is built through C and with --native, both run under
OBX_CHECK=1 (a leak exits 5), and their exit codes, stdout and stderr must be equal. A program
the compiler rejects counts as `nocompile` (the generator's fault, not a failure). Exit 1 on any
difference; differing programs are kept (in --keep, or a temporary directory it names).
"""
import argparse, os, random, shutil, subprocess, sys, tempfile
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, '..'))
OBXC = os.environ.get('OBXC') or os.path.join(ROOT, 'compiler', 'out', 'obxc')

HEAD = '''fn grow(a: mut &[]int, v: int) {
    if len(a) < 40 { append(a, v) }
}

fn shrink(a: mut &[]int): int {
    if len(a) > 1 { return pop(a) }
    return 0
}

fn total(a: &[]int): int {
    var s = 0
    var i = 0
    while i < len(a) {
        s = s + a[i]
        i = i + 1
    }
    return s
}

fn sliceSum(s: &[int]): int {
    var t = 0
    for i in 0..len(s) { t = t + s[i] }
    return t
}

fn bump(a: mut &[]int, i: int) { a[i] = a[i] + 1 }

type P = value record { x: int; y: int }
type Kid = record { v: int; w: []int }
type R = record {
    n: int
    xs: []int
    ys: []int
    p: P
    kid: Kid
    o: Option[Kid]
    bs: []byte
}

fn mkR(n: int): R {
    return R{n: n, xs: [n, 1, 2, 3, 4], ys: [7, n], p: P{x: n, y: 1}, kid: Kid{v: n, w: [1, n]}, o: None,
        bs: [byte(n), 2, 3]}
}

fn rN(r: mut &R) { r.n = r.n + 1 }
fn rXs(r: mut &R) { if len(r.xs) < 30 { append(mut &r.xs, r.n) } }
fn rYs(r: mut &R) { if len(r.ys) > 1 { let t = pop(mut &r.ys) } }
fn rKid(r: mut &R) { r.kid = Kid{v: r.n + 3, w: [r.n, 2, 3]} }
fn rKidV(k: mut &Kid) { k.v = k.v + 2 }
fn rPx(p: mut &P) { p.x = p.x * 2 + 1 }
fn rArr(a: mut &[]int) { if len(a) < 30 { append(a, 5) } }
fn rAll(r: mut &R) { r = mkR(r.n + 1) }
fn rDeep(r: mut &R, d: int) {
    if d > 0 {
        r.p.y = r.p.y + 1
        rDeep(r, d - 1)
    }
}
fn rRead(r: &R): int { return r.n + len(r.xs) + r.kid.v + r.p.x }
fn rSome(r: mut &R) { r.o = Some(Kid{v: r.n, w: [4]}) }

'''

# statements of a loop over the borrowed records r and q (index i): reads of their scalar fields
# and arrays' elements and lengths, and writes of the same in every way (stores, methods, `mut &`
# to a member, appends, whole records replaced, recursion, closures)
RSTMTS = [
    'acc = acc + r.n',
    'acc = acc + r.xs[%(i)s %% len(r.xs)]',
    'acc = acc + r.ys[%(i)s %% len(r.ys)] + len(r.ys)',
    'acc = acc + r.p.x - r.p.y',
    'acc = acc + r.kid.v + r.kid.w[%(i)s %% len(r.kid.w)]',
    'acc = acc + int(r.bs[%(i)s %% len(r.bs)])',
    'acc = acc + q.n + q.xs[0]',
    'acc = acc + r.xs[%(i)s]',
    'acc = acc + rRead(&r)',
    'match r.o { Some(k) { acc = acc + k.v } None { acc = acc + 1 } }',
    'r.n = r.n + 1',
    'q.n = q.n + 2',
    'rN(r)',
    'rN(q)',
    'rXs(r)',
    'rYs(r)',
    'rKid(r)',
    'rKidV(mut &r.kid)',
    'rPx(mut &r.p)',
    'rArr(mut &r.xs)',
    'rArr(mut &r.kid.w)',
    'if acc %% 7 == 3 { rAll(r) }',
    'rDeep(r, 2)',
    'rSome(r)',
    'r.o = None',
    'r.xs[%(i)s %% len(r.xs)] = acc %% 100',
    'r.bs[0] = byte(acc %% 256)',
    'r.kid.v = r.kid.v - 1',
    'r.kid.w = [acc %% 9]',
    'if len(r.xs) < 30 { append(mut &r.xs, %(i)s) }',
    'acc = acc + f(r.n)',
    'acc = acc + g()',
    'if acc > %(big)d { break }',
]


class Gen:
    def __init__(self, rng):
        self.rng = rng
        self.n = 0
        self.lines = []

    def name(self, p):
        self.n += 1
        return '%s%d' % (p, self.n)

    def emit(self, ind, s):
        for line in s.split('\n'):
            self.lines.append('    ' * ind + line)

    def arr(self):
        r = self.rng
        return '[' + ', '.join(str(r.randrange(-5, 30)) for _ in range(r.randrange(3, 12))) + ']'

    def idx(self, i):
        r = self.rng
        return r.choice([i, i, i, '%s + 1' % i, '%s - 1' % i, '%s + 2' % i, 'len(a) - 1 - %s' % i,
                         '%s %% len(a)' % i, '%s / 2' % i, '0', 'j'])

    # a statement of a loop body over index i (in a while: may change i; brk: break/continue allowed)
    def body(self, ind, i, inWhile, depth):
        r = self.rng
        k = r.randrange(22)
        if k == 0:
            self.emit(ind, 'acc = acc + a[%s]' % i)
        elif k == 1:
            x = self.idx(i)
            if r.randrange(3):
                # guarded: the condition proves the index
                self.emit(ind, 'if %s >= 0 && %s < len(a) { acc = acc + a[%s] }' % (x, x, x))
            else:
                self.emit(ind, 'acc = acc + a[%s]' % x)
        elif k == 2:
            self.emit(ind, 'a[%s] = acc %% 50\nacc = acc + a[%s] * 2' % (i, i))
        elif k == 3:
            x = self.idx(i)
            if r.randrange(3):
                self.emit(ind, 'if %s < len(a) && %s >= 0 { a[%s] = a[%s] + 1 }' % (x, x, x, x))
            else:
                self.emit(ind, 'a[%s] = a[%s] + 1' % (x, x))
        elif k == 4:
            self.emit(ind, 'if len(a) < 40 { append(mut &a, %s) }' % i)
        elif k == 5:
            self.emit(ind, 'acc = acc + shrink(mut &a)')
        elif k == 6:
            self.emit(ind, 'grow(mut &a, acc % 7)')
        elif k == 7:
            self.emit(ind, 'if acc %% 5 == 1 { a = %s }' % self.arr())
        elif k == 8 and inWhile:
            self.emit(ind, '%s = %s + %d' % (i, i, r.randrange(1, 3)))
        elif k == 9:
            self.emit(ind, 'n = n - 1' if r.randrange(2) else 'n = n + 1')
        elif k == 10:
            self.emit(ind, 'if acc % 4 == 3 { continue }' if not inWhile else 'if acc > 900 { break }')
        elif k == 11:
            self.emit(ind, 'if acc > %d { break }' % r.randrange(50, 2000))
        elif k == 12 and depth == 0:
            # an inner loop over b
            q = self.name('k')
            self.emit(ind, 'var %s = 0\nwhile %s < len(b) {' % (q, q))
            self.emit(ind + 1, 'acc = acc + b[%s] * a[%s]' % (q, i))
            if r.randrange(3) == 0:
                self.emit(ind + 1, 'b[%s] = b[%s] + 1' % (q, q))
            self.emit(ind + 1, '%s = %s + 1' % (q, q))
            self.emit(ind, '}')
        elif k == 13:
            self.emit(ind, 'acc = acc + total(&a)')
        elif k == 14:
            self.emit(ind, 'bump(mut &a, %s)' % self.idx(i))
        elif k == 15:
            self.emit(ind, 'acc = acc + int(bs[%s %% len(bs)])\nbs[%s %% len(bs)] = byte((int(bs[%s %% len(bs)]) + %s) %% 256)'
                      % (i, i, i, i))
        elif k == 16:
            self.emit(ind, 'acc = acc * %d + a[%s]' % (r.choice([2, 3, 1000003]), i))
        elif k == 17:
            self.emit(ind, 'j = %s\nacc = acc + a[j]' % i)
        elif k == 18:
            self.emit(ind, 'acc = acc + sliceSum(a[0..len(a) / 2])')
        elif k == 19:
            self.emit(ind, 'let %s = a[%s]\na[%s] = %s + 1\nacc = acc + a[%s] - %s'
                      % (self.name('v'), i, i, 'v%d' % self.n, i, 'v%d' % self.n))
        elif k == 20:
            self.emit(ind, 'acc = acc + %s * %d' % (i, r.choice([1, 2, 7, 4611686018427387903])))
        else:
            self.emit(ind, 'acc = acc - a[%s]' % i)

    def bodies(self, ind, i, inWhile):
        if inWhile:
            # the loops that change their index or bound may not end
            self.emit(ind, 'guard = guard + 1\nif guard > 300 { break }')
        for _ in range(self.rng.randrange(1, 5)):
            self.body(ind, i, inWhile, 0)

    def loop(self):
        r = self.rng
        k = r.randrange(12)
        i = self.name('i')
        bound = r.choice(['len(a)', 'n', 'len(a) - 1', '%d' % r.randrange(3, 15)])
        if k <= 2:
            # while i < bound, step at the end (no continue)
            step = r.choice([1, 1, 1, 2, 3])
            cmp = r.choice(['<', '<', '<='])
            self.emit(1, 'var %s = %d\nwhile %s %s %s {' % (i, r.randrange(0, 2), i, cmp, bound))
            self.bodies(2, i, True)
            self.emit(2, '%s = %s + %d' % (i, i, step) if r.randrange(4) else '%s += %d' % (i, step))
            self.emit(1, '}')
        elif k == 3:
            # down from the end
            self.emit(1, 'var %s = len(a) - 1\nwhile %s >= 0 {' % (i, i))
            self.bodies(2, i, True)
            self.emit(2, '%s = %s - 1' % (i, i))
            self.emit(1, '}')
        elif k == 4:
            # down with > and the step first
            self.emit(1, 'var %s = %s\nwhile %s > 0 {\n    %s = %s - 1' % (i, bound, i, i, i))
            self.bodies(2, i, True)
            self.emit(1, '}')
        elif k <= 6:
            # for range
            lo = r.choice(['0', '0', '1'])
            self.emit(1, 'for %s in %s..%s {' % (i, lo, bound))
            self.bodies(2, i, False)
            self.emit(1, '}')
        elif k == 9 or k == 10:
            # while i < bound stepping by a local (the seventh performance step: a variable step
            # s keeps i's lower bound only if s >= 1 on entry and the loop leaves s alone)
            s = self.name('s')
            step = r.choice(['1', '2', '3', '0', '-1', 'n % 4', 'n % 3 + 1', 'n - 6', 'len(a) / 3',
                             'acc % 5', '4611686018427387904', 'j + 1'])
            start = r.choice(['0', '1', '2', 'n', 'len(a) - 1', '9223372036854775800'])
            cmp = r.choice(['<', '<=', '<='])
            bound = r.choice([bound, 'len(a)', 'len(a) - 1', 'len(a) + 2', '9223372036854775807'])
            self.emit(1, 'var %s = %s\nvar %s = %s\nwhile %s %s %s {' % (s, step, i, start, i, cmp, bound))
            self.bodies(2, i, True)
            if k == 10:
                self.emit(2, 'acc = acc + a[%s]' % i)
            if r.randrange(4) == 0:
                # the step changes in the loop
                self.emit(2, '%s = %s - %d' % (s, s, r.randrange(1, 3)))
            self.emit(2, '%s = %s + %s' % (i, i, s) if r.randrange(3) else '%s += %s' % (i, s))
            self.emit(1, '}')
        elif k == 11:
            # a sieve on a fresh array: the inner loop steps by the outer index from its square
            p = self.name('p')
            q = self.name('q')
            m = r.choice(['len(a)', 'n * 3', 'n + len(a)', '64', '70', 'len(a) - 3'])
            v = r.choice(['true', 'false'])
            lim = r.choice(['len(%s) - 1' % p] * 4 + ['len(%s)' % p, 'n', 'n * 3'])
            useWhile = r.randrange(2) == 0
            self.emit(1, 'if true {')
            self.emit(2, 'var %s = array(%s, %s)' % (p, m, v))
            if useWhile:
                self.emit(2, 'var %s = 2\nwhile %s * %s <= %s {' % (i, i, i, lim))
            else:
                self.emit(2, 'for %s in 2..%s {\n    if %s * %s > %s { break }' % (i, r.choice(['len(%s)' % p, 'n']), i, i, lim))
            cond = '%s[%s]' % (p, i) if v == 'true' else '!%s[%s]' % (p, i)
            self.emit(3, 'if %s {' % cond)
            self.emit(4, 'var %s = %s * %s\nwhile %s %s %s {' % (q, i, i, q, r.choice(['<=', '<']), lim))
            self.emit(5, '%s[%s] = %s' % (p, q, 'false' if v == 'true' else 'true'))
            if r.randrange(3) == 0:
                self.emit(5, 'acc = acc + %s' % q)
            self.emit(5, '%s = %s + %s' % (q, q, i))
            self.emit(4, '}')
            self.emit(3, '}')
            if useWhile:
                self.emit(3, '%s = %s + 1' % (i, i))
            self.emit(2, '}')
            # counts of its elements (a count without a branch; a fresh counter is bounded)
            x = self.name('x')
            w = r.randrange(3)
            if w == 0:
                self.emit(2, 'for %s in %s {\n    if %s { acc = acc + 1 }\n}' % (x, p, x))
            elif w == 1:
                self.emit(2, 'for %s in 0..len(%s) {\n    if !%s[%s] { acc += 1 }\n}' % (x, p, p, x))
            else:
                cn = self.name('cn')
                self.emit(2, 'var %s = %s\nfor %s in %s..len(%s) {\n    if %s[%s] { %s = %s + 1 }\n}\nacc = acc + %s'
                          % (cn, r.choice(['0', 'acc', '9223372036854775800']), x, r.choice(['0', '1', 'n']), p, p, x,
                             cn, cn, cn))
            self.emit(1, '}')
        elif k == 7:
            # for over the elements of c (which the bodies leave alone), with a counter
            x = self.name('x')
            self.emit(1, 'var %s = 0\nfor %s in c {' % (i, x))
            self.emit(2, 'acc = acc + %s' % x)
            self.bodies(2, i, False)
            self.emit(2, '%s = %s + 1' % (i, i))
            self.emit(1, '}')
        else:
            # a closure that appends to the array it captures
            f = self.name('f')
            self.emit(1, 'if true {')
            self.emit(2, 'let %s = fn(v: int): int {' % f)
            self.emit(3, 'if len(c) < 30 { append(mut &c, v) }')
            self.emit(3, 'var t = 0\nvar q = 0\nwhile q < len(c) {\n    t = t + c[q]\n    q = q + 1\n}\nreturn t')
            self.emit(2, '}')
            self.emit(2, 'var %s = 0\nwhile %s < %s {' % (i, i, r.randrange(2, 9)))
            self.emit(3, 'acc = acc + %s(%s)' % (f, i))
            self.emit(3, '%s = %s + 1' % (i, i))
            self.emit(2, '}')
            self.emit(1, '}')
        self.emit(1, 'println(intToStr(acc) + " " + intToStr(len(a)) + " " + intToStr(n))')

    # a function looping over borrowed records (the loads (C) may read once before its loops)
    def rfunc(self):
        r = self.rng
        name = self.name('rf')
        lines = ['fn %s(r: mut &R, q: mut &R, n: int): int {' % name, '    var acc = 0',
                 '    let f = fn(v: int): int { return v %% %d }' % r.randrange(2, 9),
                 '    let g = fn(): int { return 3 }']
        for _ in range(r.randrange(1, 3)):
            i = self.name('i')
            if r.randrange(2):
                lines.append('    var %s = 0' % i)
                lines.append('    while %s < %s {' % (i, r.choice(['n', 'len(r.xs)', 'len(r.ys) + 1', '%d' % r.randrange(0, 9)])))
                tail = '        %s = %s + 1' % (i, i)
            else:
                lines.append('    for %s in 0..%s {' % (i, r.choice(['n', 'len(r.xs)', 'len(r.kid.w)', '%d' % r.randrange(0, 9)])))
                tail = None
            # mostly reads, a few writes: the reads with no write of theirs are the ones hoisted
            for _ in range(r.randrange(1, 7)):
                s = r.choice(RSTMTS[:10] * 3 + RSTMTS[10:])
                lines.append('        ' + s % {'i': i, 'big': r.randrange(100, 5000)})
            if r.randrange(3) == 0:
                # an inner loop over q's array
                k = self.name('k')
                lines.append('        for %s in 0..len(q.xs) { acc = acc + q.xs[%s] + r.n }' % (k, k))
            if tail:
                lines.append(tail)
            lines.append('    }')
        lines.append('    return acc')
        lines.append('}')
        self.funcs.append('\n'.join(lines))
        return name

    def program(self):
        r = self.rng
        self.funcs = []
        rfs = [self.rfunc() for _ in range(r.randrange(0, 3))]
        if rfs:
            self.emit(1, 'var ra = mkR(%d)\nvar rb = mkR(%d)' % (r.randrange(0, 5), r.randrange(0, 5)))
            for f in rfs:
                self.emit(1, 'println(intToStr(%s(mut &ra, mut &rb, %d)) + " " + intToStr(rRead(&ra)) + " " + intToStr(rRead(&rb)))'
                          % (f, r.randrange(0, 12)))
        self.emit(1, 'var a: []int = %s' % self.arr())
        self.emit(1, 'var b: []int = %s' % self.arr())
        self.emit(1, 'var c: []int = %s' % self.arr())
        self.emit(1, 'var bs: []byte = [%s]' % ', '.join(str(r.randrange(256)) for _ in range(r.randrange(2, 9))))
        self.emit(1, 'var n = %d\nvar j = 0\nvar acc = 0\nvar guard = 0' % r.randrange(2, 12))
        for _ in range(r.randrange(2, 7)):
            self.loop()
        self.emit(1, 'println(intToStr(total(&a)) + " " + intToStr(total(&b)) + " " + intToStr(total(&c)) + " " + intToStr(j))')
        return HEAD + ''.join(f + '\n\n' for f in self.funcs) + 'fn main() {\n' + '\n'.join(self.lines) + '\n}\n'


def one(k, a, out):
    rng = random.Random(a.seed + k)
    base = os.path.join(out, 'p%d' % k)
    with open(base + '.obx', 'w') as f:
        f.write(Gen(rng).program())
    c = subprocess.run([a.compiler, base + '.obx', '-o', base + '_c'], capture_output=True, text=True)
    if c.returncode != 0:
        os.remove(base + '.obx')
        return ('nocompile', k, (c.stdout + c.stderr).strip())
    nb = subprocess.run([a.compiler, base + '.obx', '--native', '-o', base + '_n'], capture_output=True, text=True)
    if nb.returncode != 0:
        return ('differ', k, 'native build failed: ' + (nb.stdout + nb.stderr).strip())
    env = dict(os.environ)
    env['OBX_CHECK'] = '1'
    try:
        rc = subprocess.run([base + '_c'], env=env, capture_output=True, timeout=20)
        rn = subprocess.run([base + '_n'], env=env, capture_output=True, timeout=20)
    except subprocess.TimeoutExpired:
        return ('differ', k, 'timeout')
    for f in ('_c', '_n'):
        os.remove(base + f)
    if (rc.returncode, rc.stdout, rc.stderr) != (rn.returncode, rn.stdout, rn.stderr):
        return ('differ', k, 'C exit %d, native exit %d: %r' % (rc.returncode, rn.returncode, rn.stderr[:200]))
    os.remove(base + '.obx')
    return ('same', k, 'exit %d' % rc.returncode)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--compiler', default=OBXC)
    ap.add_argument('-n', type=int, default=200)
    ap.add_argument('--seed', type=int, default=None)
    ap.add_argument('-j', type=int, default=os.cpu_count() or 4)
    ap.add_argument('--keep', default=None)
    a = ap.parse_args()
    a.compiler = os.path.abspath(a.compiler)
    if a.seed is None:
        a.seed = random.randrange(1 << 30)
    out = a.keep or tempfile.mkdtemp(prefix='loopgen')
    os.makedirs(out, exist_ok=True)
    with ThreadPoolExecutor(max_workers=a.j) as ex:
        res = list(ex.map(lambda k: one(k, a, out), range(a.n)))
    count = {'same': 0, 'differ': 0, 'nocompile': 0}
    exits = {}
    for kind, k, why in res:
        count[kind] += 1
        if kind == 'differ':
            print('DIFFER p%d.obx: %s' % (k, why))
        elif kind == 'nocompile' and count[kind] <= 3:
            print('nocompile p%d: %s' % (k, why[:300]))
        elif kind == 'same':
            exits[why] = exits.get(why, 0) + 1
    print('loopgen: seed %d: %d same (%s), %d differ, %d not compiled%s' % (
        a.seed, count['same'], ', '.join('%s: %d' % kv for kv in sorted(exits.items())), count['differ'],
        count['nocompile'], ' (programs in %s)' % out if count['differ'] else ''))
    if not count['differ'] and not a.keep:
        shutil.rmtree(out, ignore_errors=True)
    sys.exit(1 if count['differ'] else 0)


if __name__ == '__main__':
    main()
