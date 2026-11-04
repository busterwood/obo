#!/usr/bin/env python3
"""Random differential test of the regex library (lib/regex.obx) against Go's regexp.

usage: python3 obx/tests/regexgen.py [--compiler PATH] [-n N] [--texts T] [--textlen L] [--seed S]...
                                     [--native] [--fast] [--keep DIR]

Generates N random patterns (literals over a small alphabet with case-folding pairs and
multi-byte runes, classes, Perl, POSIX and Unicode classes, anchors and word boundaries,
greedy and lazy repeats with counts, capturing, named and non-capturing groups, flag groups,
alternation, and some invalid patterns) and T random texts for each, then runs them through
tests/regex/regexgen_driver.go (`go run`) and tests/regex/regexgen_driver.obx (built through C,
or with --native through the native backend, run under OBX_CHECK=1). For each pattern the compile
error must be the same, and for each text all submatch indexes (leftmost-first and
leftmost-longest), ReplaceAllString with "<$1|${n0}|$0>" and Split. The obx driver also runs each
text under the Pike VM, the backtracker and the lazy DFA (plain, with every literal search, in a
tiny cache) forced and reports any matcher that disagrees.
Exit 1 on any difference; the cases and both outputs are kept (in --keep, or a temporary
directory it names). Each --seed runs separately (default: one random seed). --fast is the
gate's mode: 400 patterns, 8 texts, both backends. Exits 0 with a note when go is missing.
"""
import argparse, os, random, shutil, subprocess, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, '..'))
OBXC = os.environ.get('OBXC') or os.path.join(ROOT, 'compiler', 'out', 'obxc')

# Text alphabet: ASCII letters with their case pairs, digits, space, newline, punctuation, and
# runes that fold specially: e/E with acute, sharp s, long s (folds with s), Kelvin sign (folds
# with k), a Greek sigma trio, a 3-byte and a 4-byte rune.
ALPHA = ['a', 'b', 'c', 'A', 'B', 'k', 'K', 's', 'S', 'x', '0', '1', ' ', '\n', '-', '_', '.',
         'é', 'É', 'ß', 'ſ', 'K', 'σ', 'Σ', 'ς', '☺', '\U0001F600']
LIT = ['a', 'b', 'c', 'A', 'k', 's', 'x', '0', ' ', '-', '_', 'é', 'ß', 'K', 'σ', '☺',
       '\\.', '\\n', '\\x{17f}', '\\x41', '\\-', '\\t']
CLASS_ITEMS = ['a', 'b', 'c', 'a-c', 'A-C', 'k', 's', 'x-z', '0-9', '\\d', '\\w', '\\s', '\\D', '\\W', '\\pL', '\\p{Greek}',
               '\\PL', '[:alpha:]', '[:digit:]', '[:^space:]', '[:word:]', '\\n', 'é', 'σ-ω', '-', '.',
               '\\x{1F600}', '☺', '\\\\', '\\]']
ESCAPES = ['\\d', '\\D', '\\w', '\\W', '\\s', '\\S', '\\pL', '\\p{Lu}', '\\PL', '\\p{Greek}', '\\pN', '\\p{^L}',
           '[[:alpha:]]', '[[:^alpha:]]', '\\Qa.b\\E', '.', '.', '.']
ANCHORS = ['^', '$', '\\A', '\\z', '\\b', '\\B']
INVALID = ['a**', 'a{2,1}', '(?=a)', '(?!a)', '(?<=a)', '\\1', '(a)\\1', 'a++', 'a*+', '[z-a]', '(abc', 'abc)',
           'x{1001}', '\\8', '(?P<n>a)(?P<n>b)', '[[:foo:]]', '\\pX', '\\p{Foo}', '(?i', '(?z)', '\\', '[a',
           '*a', 'a{1000}{1000}', '(?P<>a)', '(?P<a-b>c)', '\\C', '\\Z', 'a{,2}', '\xff']


class Gen:
    def __init__(self, rnd):
        self.r = rnd
        self.ngroup = 0
        self.nnamed = 0

    def atom(self, d):
        r = self.r
        k = r.random()
        if d <= 0 or k < 0.35:
            return r.choice(LIT)
        if k < 0.45:
            neg = '^' if r.random() < 0.25 else ''
            return '[' + neg + ''.join(r.choice(CLASS_ITEMS) for _ in range(r.randint(1, 3))) + ']'
        if k < 0.55:
            return r.choice(ESCAPES)
        if k < 0.63:
            return r.choice(ANCHORS)
        if k < 0.88:
            inner = self.alt(d - 1)
            g = r.random()
            if g < 0.4:
                self.ngroup += 1
                return '(' + inner + ')'
            if g < 0.55:
                self.nnamed += 1
                return '(?P<n%d>' % (self.nnamed - 1) + inner + ')'
            if g < 0.8:
                return '(?:' + inner + ')'
            flags = ''.join(r.sample('imsU', r.randint(1, 2)))
            if r.random() < 0.2:
                flags += '-' + r.choice('imsU')
            return '(?' + flags + ':' + inner + ')'
        return '(' + self.alt(d - 1) + ')'

    def rep(self, d):
        a = self.atom(d)
        if self.r.random() < 0.35:
            k = self.r.random()
            if k < 0.25:
                op = '*'
            elif k < 0.5:
                op = '+'
            elif k < 0.7:
                op = '?'
            else:
                n = self.r.randint(0, 3)
                m = self.r.choice(['', ',', ',%d' % (n + self.r.randint(0, 2))])
                op = '{%d%s}' % (n, m)
            if self.r.random() < 0.25:
                op += '?'
            return a + op
        return a

    def concat(self, d):
        return ''.join(self.rep(d) for _ in range(self.r.randint(1, 4)))

    def alt(self, d):
        n = 1 if self.r.random() < 0.7 else self.r.randint(2, 3)
        return '|'.join(self.concat(d) for _ in range(n))

    def pattern(self):
        self.ngroup = 0
        self.nnamed = 0
        r = self.r
        if r.random() < 0.04:
            p = r.choice(INVALID)
            return p if r.random() < 0.5 else self.alt(1) + p
        p = self.alt(r.randint(1, 3))
        if r.random() < 0.15:
            p = '(?' + ''.join(r.sample('imsU', r.randint(1, 2))) + ')' + p
        return p

    def text(self, pat):
        r = self.r
        if r.random() < 0.3:
            # Built from the pattern's literal characters, which match more often.
            chars = [c for c in pat if c.isalnum() or c in ' -_\n'] or ALPHA
            return ''.join(r.choice(chars + ALPHA[:3]) for _ in range(r.randint(0, self.textlen)))
        return ''.join(r.choice(ALPHA) for _ in range(r.randint(0, self.textlen)))


def write_cases(path, seed, n, ntexts, textlen):
    g = Gen(random.Random(seed))
    g.textlen = textlen
    with open(path, 'w') as f:
        for _ in range(n):
            p = g.pattern()
            f.write('P\t' + p.encode('utf-8', 'surrogateescape').hex() + '\n')
            for _ in range(ntexts):
                f.write('T\t' + g.text(p).encode('utf-8').hex() + '\n')


def build_obx(compiler, tmp, native):
    exe = os.path.join(tmp, 'driver_native' if native else 'driver_c')
    cmd = [compiler, os.path.join(HERE, 'regex', 'regexgen_driver.obx'), '-o', exe] + (['--native'] if native else [])
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit('building the obx driver failed:\n' + r.stdout + r.stderr)
    return exe


def build_go(tmp):
    exe = os.path.join(tmp, 'driver_go')
    env = dict(os.environ, GOTOOLCHAIN='local', GOFLAGS='-mod=mod', GO111MODULE='off')
    r = subprocess.run(['go', 'build', '-o', exe, os.path.join(HERE, 'regex', 'regexgen_driver.go')],
                       capture_output=True, text=True, env=env)
    if r.returncode != 0:
        sys.exit('building the Go driver failed:\n' + r.stdout + r.stderr)
    return exe


def show(line):
    """A result line with its hex fields decoded, for a report."""
    out = []
    for w in line.split(' '):
        if w.startswith('h') and all(c in '0123456789abcdef' for c in w[1:]):
            out.append(repr(bytes.fromhex(w[1:]).decode('utf-8', 'replace')))
        else:
            out.append(w)
    return ' '.join(out)


def compare(gout, oout, label):
    g = gout.split('\n')
    o = oout.split('\n')
    diffs = [l for l in o if l.startswith('D ')]
    o = [l for l in o if not l.startswith('D ')]
    pat = ''
    nd = 0
    for i in range(max(len(g), len(o))):
        a = g[i] if i < len(g) else '<missing>'
        b = o[i] if i < len(o) else '<missing>'
        if a.startswith('P '):
            pat = a
        if a != b:
            nd += 1
            if nd <= 10:
                print('%s: differs for %s\n  go:  %s\n  obx: %s' % (label, show(pat), show(a), show(b)))
    for d in diffs[:10]:
        print('%s: matchers disagree: %s' % (label, show(d)))
    return nd + len(diffs)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--compiler', default=OBXC)
    ap.add_argument('-n', type=int, default=1000)
    ap.add_argument('--texts', type=int, default=10)
    ap.add_argument('--textlen', type=int, default=12, help='the longest text (default 12)')
    ap.add_argument('--seed', type=int, action='append')
    ap.add_argument('--native', action='store_true')
    ap.add_argument('--fast', action='store_true')
    ap.add_argument('--keep')
    a = ap.parse_args()
    if shutil.which('go') is None:
        print('regexgen: go is not installed; skipped')
        return 0
    seeds = a.seed or [random.randrange(1 << 30)]
    backends = [False, True] if a.fast else [a.native]
    if a.fast:
        a.n, a.texts = 400, 8
    tmp = a.keep or tempfile.mkdtemp(prefix='regexgen_')
    os.makedirs(tmp, exist_ok=True)
    gexe = build_go(tmp)
    oexes = [(nat, build_obx(a.compiler, tmp, nat)) for nat in backends]
    total = 0
    for seed in seeds:
        cases = os.path.join(tmp, 'cases_%d.txt' % seed)
        write_cases(cases, seed, a.n, a.texts, a.textlen)
        gout = subprocess.run([gexe, cases], capture_output=True, text=True, errors='surrogateescape', check=True).stdout
        open(cases + '.go.out', 'w', errors='surrogateescape').write(gout)
        for nat, exe in oexes:
            label = 'seed %d %s' % (seed, 'native' if nat else 'C')
            env = dict(os.environ, OBX_CHECK='1')
            r = subprocess.run([exe, cases], capture_output=True, text=True, errors='surrogateescape', env=env, timeout=600)
            open(cases + ('.native' if nat else '.c') + '.out', 'w', errors='surrogateescape').write(r.stdout)
            if r.returncode != 0:
                print('%s: the obx driver exited %d: %s' % (label, r.returncode, r.stderr[-500:]))
                total += 1
                continue
            nd = compare(gout, r.stdout, label)
            total += nd
            print('%s: %d patterns x %d texts, %d differences' % (label, a.n, a.texts, nd))
    if total:
        print('regexgen: %d differences; cases and outputs in %s' % (total, tmp))
        return 1
    if not a.keep:
        shutil.rmtree(tmp)
    print('regexgen: no differences')
    return 0


if __name__ == '__main__':
    sys.exit(main())
