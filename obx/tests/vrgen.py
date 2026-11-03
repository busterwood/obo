#!/usr/bin/env python3
"""Generated stress test of value records with heap fields passed through small functions, for the
native backend's inlining and record rewrites (compiler/opt.obx, design/native.md section 8).

usage: python3 obx/tests/vrgen.py [--compiler PATH] [-n N] [--seed S] [-j N] [--keep DIR]

Each program passes value records with `str` fields (every other program also an `Option` field)
around small functions: by value, by borrow, as receivers, results, copies, array elements,
`replace`, match arms, loops and `x = f(x)`. It is built through C and with --native, both run
under OBX_CHECK=1 (a leak exits 5), and their exit codes, stdout and stderr must be equal. A
program the compiler rejects counts as `nocompile` (the generator's fault, not a failure). Exit 1
on any difference; differing programs are kept (in --keep, or a temporary directory it names).
"""
import argparse, os, random, shutil, subprocess, sys, tempfile
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, '..'))
OBXC = os.environ.get('OBXC') or os.path.join(ROOT, 'compiler', 'out', 'obxc')

HEAD = '''type U = value record { k: int; t: str }
type T = value record { a: int; s: str; u: U; b: int@OF@ }
type W = value record { x: T; y: T }

fn mk(i: int): T { return T{a: i, s: "s" + intToStr(i), u: U{k: i * 2, t: "t" + intToStr(i)}, b: i@OMK@} }
fn mkw(i: int): W { return W{x: mk(i), y: mk(i + 1)} }
fn (x: T) ra(): int { return x.a }
fn (x: T) rs(): str { return x.s }
fn (x: T) ru(): U { return x.u }
fn (x: T) rt(): str { return x.u.t }
fn (x: T) none(): int { return 7 }
fn (x: T) all(): str { return intToStr(x.a) + x.s + intToStr(x.u.k) + x.u.t + intToStr(x.b)@OALL@ }
fn (x: &T) bs(): str { return x.s }
fn (x: &T) ba(): int { return x.a + x.u.k }
fn pa(x: T): int { return x.a + 1 }
fn ps(x: T): str { return x.s }
fn pn(x: T, y: T): int { return x.b + y.b }
fn pick(x: T, y: T): T { return if x.a > y.a { x } else { y } }
fn same(x: T): T { return x }
fn upd(x: T): T {
    var y = x
    y.a += 1
    return y
}
fn sets(x: T, v: str): T {
    var m = x
    m.s = v
    return m
}
fn swp(x: T): T { return T{a: x.a, s: x.u.t, u: U{k: x.u.k, t: x.s}, b: x.b@OSWP@} }
fn wx(w: W): T { return w.x }
fn (w: W) wy(): str { return w.y.s }
fn get(i: int): Result[int, T] { return if i % 2 == 0 { Ok(i) } else { Err(mk(i)) } }
fn opt(i: int): Option[T] { return if i % 3 == 0 { None } else { Some(mk(i)) } }
fn hash(s: str): int { return len(s) }
'''

RO = '''fn (x: T) ro(): str {
    match x.o {
        Some(q) { return q.t }
        None { return "" }
    }
}
'''


def head(opt_field):
    subs = {'@OF@': '; o: Option[U]', '@OMK@': ', o: Some(U{k: i, t: "o" + intToStr(i)})',
            '@OALL@': ' + (match x.o { Some(q) { q.t } None { "-" } })', '@OSWP@': ', o: None'}
    h = HEAD
    for k, v in subs.items():
        h = h.replace(k, v if opt_field else '')
    return h + (RO if opt_field else '')


def gen(rng, opt_field):
    lines = []
    vs = []        # T variables: (name, mutable)
    n = 0

    def v():
        return rng.choice(vs)[0]

    def newname():
        nonlocal n
        n += 1
        return 'v%d' % n

    def mutable():
        return rng.choice([x for x in vs if x[1]])[0]

    lines.append('    var arr: []T = []')
    lines.append('    for i in 0..4 { append(mut &arr, mk(i + 10)) }')
    lines.append('    var acc = 0')
    for i in range(3):
        nm = newname()
        vs.append((nm, True))
        lines.append('    var %s = mk(%d)' % (nm, rng.randrange(50)))
    lines.append('    let w1 = mkw(3)')
    reads = ['%s.ra()', '%s.none()', 'hash(%s.rs())', 'hash(%s.rt())', 'hash(%s.all())', '%s.ba()', 'hash(%s.bs())',
             'pa(%s)', 'hash(ps(%s))', '%s.ru().k', 'hash(%s.ru().t)', '%s.a', 'hash(%s.s)', '%s.u.k']
    if opt_field:
        reads.append('hash(%s.ro())')
    for _ in range(rng.randrange(15, 45)):
        r = rng.randrange(20)
        if r < 5:
            lines.append('    acc += %s' % (rng.choice(reads) % v()))
        elif r == 5:
            nm = newname()
            src = rng.choice(['%s' % v(), 'arr[%d]' % rng.randrange(4), 'same(%s)' % v(), 'pick(%s, %s)' % (v(), v()),
                              'swp(%s)' % v(), 'upd(%s)' % v(), 'wx(w1)', 'mk(%d)' % rng.randrange(9),
                              'sets(%s, "z" + intToStr(acc))' % v()])
            lines.append('    let %s = %s' % (nm, src))
            vs.append((nm, False))
        elif r == 6:
            t = mutable()
            f = rng.choice(['upd(%s)', 'same(%s)', 'swp(%s)', 'sets(%s, "q")', 'pick(%s, mk(acc %% 30))'])
            lines.append('    %s = %s' % (t, f % t))
        elif r == 7:
            t = mutable()
            lines.append('    %s = %s' % (t, rng.choice(['arr[%d]' % rng.randrange(4), v(), 'mk(acc % 20)'])))
        elif r == 8:
            lines.append('    append(mut &arr, %s)' % v())
        elif r == 9:
            lines.append('    match get(acc + %d) {\n        Ok(q) { acc += q }\n        Err(e) { acc += %s }\n    }'
                         % (rng.randrange(5), rng.choice(reads) % 'e'))
        elif r == 10:
            lines.append('    match opt(acc + %d) {\n        Some(e) { acc += %s }\n        None { acc += 1 }\n    }'
                         % (rng.randrange(5), rng.choice(reads) % 'e'))
        elif r == 11:
            lines.append('    for i in 0..3 {\n        let c = arr[(i + acc) %% len(arr)]\n        acc += %s\n    }'
                         % (rng.choice(reads) % 'c'))
        elif r == 12:
            lines.append('    if acc %% 2 == 0 { acc += %s } else { acc += %s }'
                         % (rng.choice(reads) % v(), rng.choice(reads) % v()))
        elif r == 13:
            lines.append('    acc += pn(%s, %s)' % (v(), v()))
        elif r == 14:
            lines.append('    acc += hash(w1.wy()) + wx(w1).a')
        elif r == 15:
            lines.append('    %s.s = %s.s + "m"' % (mutable(), v()))
        elif r == 16:
            lines.append('    acc += hash(arr[%d].s) + arr[%d].ra()' % (rng.randrange(4), rng.randrange(4)))
        elif r == 17:
            o = newname()
            lines.append('    let %s = replace(mut &arr[%d], %s)\n    acc += %s.ra()' % (o, rng.randrange(4), v(), o))
        elif r == 18:
            lines.append('    acc += %s.ra() + hash(%s.rs())' % (v(), v()))
        else:
            lines.append('    println(intToStr(acc) + " " + %s.all())' % v())
    lines.append('    println(intToStr(acc))')
    for nm, _ in vs:
        lines.append('    println(%s.all())' % nm)
    return head(opt_field) + 'fn main() {\n' + '\n'.join(lines) + '\n}\n'


def one(k, a, out):
    rng = random.Random(a.seed + k)
    base = os.path.join(out, 'p%d' % k)
    with open(base + '.obx', 'w') as f:
        f.write(gen(rng, k % 2 == 1))
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
    return ('same', k, '')


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
    out = a.keep or tempfile.mkdtemp(prefix='vrgen')
    os.makedirs(out, exist_ok=True)
    with ThreadPoolExecutor(max_workers=a.j) as ex:
        res = list(ex.map(lambda k: one(k, a, out), range(a.n)))
    count = {'same': 0, 'differ': 0, 'nocompile': 0}
    for kind, k, why in res:
        count[kind] += 1
        if kind == 'differ':
            print('DIFFER p%d.obx: %s' % (k, why))
        elif kind == 'nocompile' and count[kind] <= 3:
            print('nocompile p%d: %s' % (k, why[:200]))
    print('vrgen: seed %d: %d same, %d differ, %d not compiled%s' % (
        a.seed, count['same'], count['differ'], count['nocompile'],
        ' (programs in %s)' % out if count['differ'] else ''))
    if not count['differ'] and not a.keep:
        shutil.rmtree(out, ignore_errors=True)
    sys.exit(1 if count['differ'] else 0)


if __name__ == '__main__':
    main()
