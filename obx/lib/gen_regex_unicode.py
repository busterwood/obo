#!/usr/bin/env python3
"""Generates obx/lib/regex_unicode.obx: the Unicode tables of the regex
module (R1 of design/regex.md): the general categories and scripts for
\\p{..}, their case-folding companions (FoldCategory, FoldScript), the
category aliases (\\p{Letter} and the like), and simple case folding (Go's
unicode.SimpleFold) for (?i).

The data is read from Go's unicode tables (src/unicode/tables.go of the
installed Go, Unicode 15.0.0 for Go 1.21 to 1.26), which Go's regexp uses, so
the tables here are the ones Go matches with. No network is needed.

    python3 obx/lib/gen_regex_unicode.py [--goroot DIR] [--check]

--check compares SimpleFold for every code point with Go's own (needs go).
"""
import argparse, os, re, subprocess, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, 'regex_unicode.obx')
MAX_RUNE = 0x10FFFF
UPPER_LOWER = MAX_RUNE + 1


def goroot(arg):
    if arg:
        return arg
    try:
        r = subprocess.run(['go', 'env', 'GOROOT'], capture_output=True, text=True)
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.strip()
    except OSError:
        pass
    return '/usr/local/go'


def parse_tables(src):
    """name -> list of (lo, hi, stride) for every `var X = &RangeTable{...}`."""
    tables = {}
    for m in re.finditer(r'^var (\w+) = &RangeTable\{\n(.*?)^\}', src, re.S | re.M):
        name, body = m.group(1), m.group(2)
        rs = [(int(a, 16), int(b, 16), int(c)) for a, b, c in
              re.findall(r'\{(0x[0-9a-fA-F]+), (0x[0-9a-fA-F]+), (\d+)\}', body)]
        tables[name] = rs
    return tables


def parse_map(src, var):
    m = re.search(r'^var %s = map\[string\]\*RangeTable\{\n(.*?)^\}' % var, src, re.S | re.M)
    return dict(re.findall(r'"(\w+)":\s*(\w+),', m.group(1)))


def parse_aliases(src):
    m = re.search(r'^var CategoryAliases = map\[string\]string\{\n(.*?)^\}', src, re.S | re.M)
    return dict(re.findall(r'"(\w+)":\s*"(\w+)",', m.group(1)))


def parse_var_aliases(src):
    """`X = _X` lines of the var blocks: exported name -> table variable."""
    return dict(re.findall(r'^\t(\w+)\s*= (_\w+)', src, re.M))


def case_ranges(src):
    m = re.search(r'^var _CaseRanges = \[\]CaseRange\{\n(.*?)^\}', src, re.S | re.M)
    out = []
    for a, b, d in re.findall(r'\{(0x[0-9A-Fa-f]+), (0x[0-9A-Fa-f]+), d\{([^}]*)\}\}', m.group(1)):
        ds = [UPPER_LOWER if x.strip() == 'UpperLower' else int(x) for x in d.split(',')]
        out.append((int(a, 16), int(b, 16), ds))
    return out


def case_orbit(src):
    m = re.search(r'^var caseOrbit = \[\]foldPair\{\n(.*?)^\}', src, re.S | re.M)
    return {int(a, 16): int(b, 16) for a, b in re.findall(r'\{(0x[0-9A-Fa-f]+), (0x[0-9A-Fa-f]+)\}', m.group(1))}


def simple_fold_all(cranges, orbit):
    """SimpleFold(r) for every r, as Go's unicode.SimpleFold computes it."""
    def convert(case, r, cr):
        lo, _, ds = cr
        delta = ds[case]
        if delta > MAX_RUNE:   # UpperLower: alternate upper and lower from lo
            return lo + (((r - lo) & ~1) | (case & 1))
        return r + delta
    fold = {}
    for lo, hi, ds in cranges:
        for r in range(lo, hi + 1):
            if r in orbit:
                continue
            l = convert(1, r, (lo, hi, ds))
            f = l if l != r else convert(0, r, (lo, hi, ds))
            if f != r:
                fold[r] = f
    for r, t in orbit.items():
        if t != r:
            fold[r] = t
    return fold


def expand(ranges):
    """(lo, hi, stride) -> sorted list of (lo, hi) with stride 1, merged where they touch."""
    out = []
    for lo, hi, st in ranges:
        if st == 1:
            pairs = [(lo, hi)]
        else:
            pairs = [(c, c) for c in range(lo, hi + 1, st)]
        for a, b in pairs:
            if out and a <= out[-1][1] + 1:
                out[-1] = (out[-1][0], max(out[-1][1], b))
            else:
                out.append((a, b))
    return out


def ints(xs, per=12, indent='    '):
    lines = []
    for i in range(0, len(xs), per):
        lines.append(indent + ', '.join('0x%X' % x for x in xs[i:i + per]))
    return ',\n'.join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--goroot')
    ap.add_argument('--check', action='store_true')
    a = ap.parse_args()
    root = goroot(a.goroot)
    path = os.path.join(root, 'src', 'unicode', 'tables.go')
    src = open(path, encoding='utf-8').read()
    version = re.search(r'const Version = "([^"]+)"', src).group(1)
    tables = parse_tables(src)
    varalias = parse_var_aliases(src)
    cats = parse_map(src, 'Categories')
    foldcats = parse_map(src, 'FoldCategory')
    aliases = parse_aliases(src)

    def table(name):
        v = varalias.get(name, name)
        return tables[v]

    # One flat table of (lo, hi) pairs; each class is a slice of it.
    scripts = parse_map(src, 'Scripts')
    foldscripts = parse_map(src, 'FoldScript')
    # Categories first, then scripts (Go looks the name up in that order).
    entries = [(n, cats[n], foldcats.get(n)) for n in sorted(cats)]
    num_categories = len(entries)
    entries += [(n, scripts[n], foldscripts.get(n)) for n in sorted(scripts)]
    flat, index = [], []          # index: name, start, count (pairs) of the class, then of its fold table
    for n, tvar, fvar in entries:
        rs = expand(table(tvar))
        start = len(flat) // 2
        for lo, hi in rs:
            flat += [lo, hi]
        if fvar:
            fr = expand(tables[fvar])
            fstart = len(flat) // 2
            for lo, hi in fr:
                flat += [lo, hi]
        else:
            fr, fstart = [], 0
        index.append((n, start, len(rs), fstart, len(fr)))

    fold = simple_fold_all(case_ranges(src), case_orbit(src))
    keys = sorted(fold)
    fold_flat = []
    for k in keys:
        fold_flat += [k, fold[k]]

    if a.check:
        check(fold)

    # canonical alias names (as regexp/syntax's canonicalName makes them)
    def canon(s):
        b, first = [], True
        for c in s:
            if c in '_- ':
                continue
            if first:
                c = c.upper(); first = False
            else:
                c = c.lower()
            b.append(c)
        return ''.join(b)
    alias_list = sorted((canon(k), v) for k, v in aliases.items())

    out = []
    out.append('// regex_unicode: Unicode tables for the regex module (generated; do not edit).')
    out.append('//')
    out.append('// Generated by obx/lib/gen_regex_unicode.py from Go\'s unicode tables')
    out.append('// (src/unicode/tables.go, Unicode %s), which Go\'s regexp matches with.' % version)
    out.append('// The data is Unicode\'s (Unicode License v3); the table layout follows Go\'s')
    out.append('// (BSD-3-Clause, Copyright 2009 The Go Authors; see NOTICE).')
    out.append('')
    out.append('pub const UNICODE_VERSION = "%s"' % version)
    out.append('pub const MAX_RUNE = 0x10FFFF')
    out.append('')
    out.append('// The general categories (the first NUM_CATEGORIES) and the scripts. Each class is a run of')
    out.append('// (lo, hi) pairs in RANGES: CLASS_START[i], CLASS_COUNT[i] pairs, and the')
    out.append('// code points that fold into it (Go\'s FoldCategory) at FOLD_START[i],')
    out.append('// FOLD_COUNT[i].')
    out.append('pub const NUM_CATEGORIES = %d' % num_categories)
    out.append('pub const CLASS_NAMES: []str = [%s]' % ', '.join('"%s"' % n for n, *_ in index))
    out.append('pub const CLASS_START: []i32 = [%s]' % ', '.join(str(x[1]) for x in index))
    out.append('pub const CLASS_COUNT: []i32 = [%s]' % ', '.join(str(x[2]) for x in index))
    out.append('pub const FOLD_START: []i32 = [%s]' % ', '.join(str(x[3]) for x in index))
    out.append('pub const FOLD_COUNT: []i32 = [%s]' % ', '.join(str(x[4]) for x in index))
    out.append('')
    out.append('// Long names (canonical: first letter upper, the rest lower, no _ - or space)')
    out.append('// and the category each names.')
    out.append('pub const ALIAS_NAMES: []str = [%s]' % ', '.join('"%s"' % k for k, _ in alias_list))
    out.append('pub const ALIAS_TARGETS: []str = [%s]' % ', '.join('"%s"' % v for _, v in alias_list))
    out.append('')
    out.append('// %d pairs.' % (len(flat) // 2))
    out.append('pub const RANGES: []i32 = [')
    out.append(ints(flat))
    out.append(']')
    out.append('')
    out.append('// Simple case folding: (r, SimpleFold(r)) for every r that folds, sorted by r')
    out.append('// (%d pairs). SimpleFold maps each code point to the next one of its' % len(keys))
    out.append('// equivalence class, in order, wrapping around: A -> a -> A, K -> k -> K (Kelvin) -> K.')
    out.append('pub const FOLD_PAIRS: []i32 = [')
    out.append(ints(fold_flat))
    out.append(']')
    out.append('')
    out.append('pub const MIN_FOLD = 0x%X' % keys[0])
    out.append('pub const MAX_FOLD = 0x%X' % keys[-1])
    out.append('')
    out.append('''// The next code point of r's case-folding orbit (r itself when none), as Go's
// unicode.SimpleFold.
pub fn simpleFold(r: int): int {
    if r < MIN_FOLD || r > MAX_FOLD {
        return r
    }
    if r < 0x80 {
        if r >= 0x41 && r <= 0x5A { return r + 32 }     // A-Z
        if r >= 0x61 && r <= 0x7A {                      // a-z
            if r == 0x6B { return 0x212A }               // k -> Kelvin sign
            if r == 0x73 { return 0x17F }                // s -> long s
            return r - 32
        }
        return r
    }
    var lo = 0
    var hi = len(FOLD_PAIRS) / 2
    while lo < hi {
        let m = (lo + hi) / 2
        let k = int(FOLD_PAIRS[2 * m])
        if k == r { return int(FOLD_PAIRS[2 * m + 1]) }
        if k < r { lo = m + 1 } else { hi = m }
    }
    return r
}

// The index of a category in CLASS_NAMES (exact name), or -1.
pub fn classIndex(name: str): int {
    for i in 0..NUM_CATEGORIES {
        if CLASS_NAMES[i] == name { return i }
    }
    return -1
}

// The index of a script in CLASS_NAMES (exact name), or -1.
pub fn scriptIndex(name: str): int {
    for i in NUM_CATEGORIES..len(CLASS_NAMES) {
        if CLASS_NAMES[i] == name { return i }
    }
    return -1
}

// The category a canonical long name stands for, or "".
pub fn aliasOf(name: str): str {
    for i in 0..len(ALIAS_NAMES) {
        if ALIAS_NAMES[i] == name { return ALIAS_TARGETS[i] }
    }
    return ""
}

// Whether r is in the class (pairs CLASS_START[i] .. + CLASS_COUNT[i]).
pub fn inClass(i: int, r: int): bool {
    var lo = int(CLASS_START[i])
    var hi = lo + int(CLASS_COUNT[i])
    while lo < hi {
        let m = (lo + hi) / 2
        if r < int(RANGES[2 * m]) {
            hi = m
        } else if r > int(RANGES[2 * m + 1]) {
            lo = m + 1
        } else {
            return true
        }
    }
    return false
}''')
    text = '\n'.join(out) + '\n'
    with open(OUT, 'w') as f:
        f.write(text)
    print('wrote %s: Unicode %s, %d categories and scripts, %d range pairs, %d fold pairs, %d aliases' % (
        OUT, version, len(index), len(flat) // 2, len(keys), len(alias_list)))


def check(fold):
    prog = r'''package main
import ("fmt"; "unicode")
func main() {
	for r := rune(0); r <= unicode.MaxRune; r++ {
		if f := unicode.SimpleFold(r); f != r { fmt.Printf("%x %x\n", r, f) }
	}
}
'''
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, 'main.go')
        open(p, 'w').write(prog)
        r = subprocess.run(['go', 'run', p], capture_output=True, text=True)
        if r.returncode != 0:
            sys.exit('go run failed: ' + r.stderr)
        got = {}
        for ln in r.stdout.strip().split('\n'):
            a, b = ln.split()
            got[int(a, 16)] = int(b, 16)
    if got != fold:
        diff = sorted(set(got.items()) ^ set(fold.items()))[:10]
        sys.exit('SimpleFold differs from Go: %s' % diff)
    print('SimpleFold agrees with Go for every code point (%d folding)' % len(got))


if __name__ == '__main__':
    main()
