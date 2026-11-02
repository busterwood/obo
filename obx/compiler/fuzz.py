#!/usr/bin/env python3
"""Mutation fuzzer for the compiler: every mutant must compile or be rejected cleanly.

    python3 obx/compiler/fuzz.py                  # 2000 mutants, seed 1
    python3 obx/compiler/fuzz.py -n 5000 --seed 7
    python3 obx/compiler/fuzz.py --cc             # also gcc -fsyntax-only on the C of every mutant that compiles

Mutants are made from every test program (tests/, lib/, redteam/, compiler/) by token and
byte edits (deterministic for a seed), so they reach the syntax, sema and body-check errors
that the suites barely touch. Each is compiled with `obxc MUTANT --emit-c` under
OBX_CHECK=1. A mutant passes when obxc exits 0 (C written) or exits 1 with one clean
`file:line: message` on stderr; anything else (a trap, a leak: exit 5, a crash, a timeout,
a message in another form) is a failure, and the mutant is kept in --keep (default
compiler/out/fuzz/). A mutant lives next to its source program (as a hidden
.obxmut<k>.obx file, removed afterwards), so its ./ imports still resolve.

The compiler is $OBXC, else compiler/out/obxc (build it with compiler/build.sh).
This was the mutant generator of run_compare.py, which compared obxc with the reference
compiler until ref was retired (archive/obx-ref/).
"""
import argparse, glob, os, random, re, shutil, subprocess, sys
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
OBX = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(OBX, 'tools'))
import cbuild  # noqa: E402

SUITES = ['tests', 'lib', 'redteam', 'compiler']
MESSAGE = re.compile(r'^[^\n]+:\d+: [^\n]+\n?$')
TIMEOUT = 20


def corpus():
    files = set()
    for s in SUITES:
        files.update(glob.glob(os.path.join(OBX, s, '**', '*.obx'), recursive=True))
    return sorted(f for f in files if '/build/' not in f and '/out/' not in f)


TOK = re.compile(r'\s+|//[^\n]*|"(?:\\.|[^"\\\n])*"|\w+|..?', re.S)
EXTRA = ['&', 'mut &', 'move(', ')', '(', '[', ']', '{', '}', 'try ', '-', '0', '..', '.', ',', ';', '\n',
         'T', 'int', 'str', '[]', 'fn ', 'return', 'break', 'None', 'Some(', 'x', 'self', '_', 'match ',
         'value ', '<<', '%', 'let ', 'var ', 'if ', 'else ', 'type ', 'record ', 'closed ', 'pub ',
         'import "x"', 'extern "c" ', 'as ', ':', '=', '+=', '==', '|', 'once ', 'never', '@', '"', "'",
         "'\\", '\\', '0x', '1.', '.5', '1e', '9' * 45, '0x' + 'f' * 40, '"\\x4"', '"\\q"', "'ab'",
         '\r', '\r\n', '\t', '$', '#', '`', '?', '\x01', '\x7f', '\xe9', '€', '²', 'abé',
         '﻿', '"é"', "'é'", '"\\é"',
         # sema: declarations, constraints, constants
         'copy', 'copy, ', 'Self', 'interface ', 'drop', 'alias', 'u8', 'i128', 'u128', 'float', 'bool',
         '+%', '*%', '>>', '/', '1 << 127', '-9223372036854775808', '0.5', '"s" + ', 'true', '!',
         'Option[', 'Result[', 'Map[', '[int]', 'std.', 'fs.', '&[', 'fn(int): ', 'T: ', '[T]', ' T',
         'var ', 'once ', 'pub ', 'import "./x" as y', 'import "fs"']


def mutate(src, r):
    toks = TOK.findall(src)
    for _ in range(r.randint(1, 3)):
        i = r.randrange(len(toks))
        op = r.random()
        if op < 0.3:
            del toks[i]
        elif op < 0.65:
            toks.insert(i, r.choice(EXTRA))
        elif op < 0.8:
            toks[i] = r.choice(toks)
        else:
            j = r.randrange(len(toks))
            toks[i], toks[j] = toks[j], toks[i]
    return ''.join(toks)


WORDS = ['int', 'i32', 'u32', 'u64', 'byte', 'i128', 'u128', 'float', 'bool', 'str', 'ptr', 'never', 'T',
         'U', 'Self', 'Option', 'Result', 'Map', 'StrBuf', 'copy', 'main', 'drop', 'len', 'Some', 'None',
         'fs', 'std', 'x', '0', '1', '2', '127', '128', '255', '256',
         '9223372036854775807', '0.5', '1e308', '63', '64']


KEYWORDS = {'as', 'break', 'closed', 'const', 'continue', 'else', 'extern', 'false', 'fn', 'for', 'if',
            'import', 'in', 'interface', 'let', 'match', 'mut', 'never', 'once', 'pub', 'record', 'return',
            'true', 'try', 'type', 'var', 'while', 'value'}


def mutate_names(src, r):
    """A semantic mutant: names and numbers replaced (by others from the program or from WORDS),
    so the program mostly still parses and sema and the body checker meet it."""
    toks = TOK.findall(src)
    words = [i for i, t in enumerate(toks) if re.match(r'\w', t) and t not in KEYWORDS]
    if not words:
        return src
    pool = [toks[i] for i in words]
    for _ in range(r.randint(1, 2)):
        i = r.choice(words)
        toks[i] = r.choice(WORDS) if r.random() < 0.5 else r.choice(pool)
    return ''.join(toks)


def make_mutants(files, n, seed, out):
    """Mutants as hidden files next to their programs (so ./ imports resolve), named
    .obxmut<k>.obx; glob('*.obx') does not see them, and main() removes them."""
    r = random.Random(seed)
    srcs = []
    for f in files:
        with open(f, 'rb') as fh:
            data = fh.read()
        try:
            srcs.append((f, data.decode('utf-8')))
        except UnicodeDecodeError:
            pass
    for k in range(n):
        f, src = r.choice(srcs)
        m = mutate(src, r) if k % 2 == 0 else mutate_names(src, r)
        p = os.path.join(os.path.dirname(f), f".obxmut{k:05d}.obx")
        with open(p, 'wb') as fh:
            data = m.encode('utf-8')
            if r.random() < 0.02:       # a few with invalid UTF-8
                at = r.randrange(len(data) + 1)
                data = data[:at] + bytes([r.choice([0x80, 0xc0, 0xe2, 0xed, 0xf4, 0xff])]) + data[at:]
            fh.write(data)
        out.append(p)
    return out


def check(argv, path, cc):
    """None if obxc compiled the mutant or rejected it cleanly, else what went wrong."""
    try:
        r = subprocess.run(argv + [path, '--emit-c'], capture_output=True, timeout=TIMEOUT,
                           env=dict(os.environ, OBX_CHECK='1'), cwd=os.path.dirname(path))
    except subprocess.TimeoutExpired:
        return 'timeout'
    err = r.stderr.decode('utf-8', 'replace')
    if r.returncode == 1 and MESSAGE.match(err):
        return None
    if r.returncode != 0:
        return f"exit {r.returncode}: {err.strip()[:300]!r}"
    if err:
        return f"exit 0 with stderr {err.strip()[:300]!r}"
    if cc:
        g = subprocess.run(['gcc', '-fsyntax-only', '-w', '-std=gnu11', '-x', 'c', '-'], input=r.stdout,
                           capture_output=True, timeout=120)
        if g.returncode:
            line = next((x for x in g.stderr.decode('utf-8', 'replace').splitlines() if 'error' in x), '')
            return f"the C does not compile: {line[:300]}"
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('-n', type=int, default=2000, help='number of mutants')
    ap.add_argument('--seed', type=int, default=1)
    ap.add_argument('-j', type=int, default=os.cpu_count() or 4)
    ap.add_argument('--cc', action='store_true', help='also check the C of every compiled mutant with gcc')
    ap.add_argument('--keep', default=os.path.join(HERE, 'out', 'fuzz'), help='where failing mutants go')
    ap.add_argument('--compiler', default=cbuild.OBXC)
    a = ap.parse_args()
    argv = cbuild.obxc_argv(a.compiler)
    ms = []
    try:
        make_mutants(corpus(), a.n, a.seed, ms)
        with ThreadPoolExecutor(a.j) as ex:
            res = list(ex.map(lambda m: check(argv, m, a.cc), ms))
        bad = [(m, e) for m, e in zip(ms, res) if e]
        if bad:
            os.makedirs(a.keep, exist_ok=True)
        for m, e in bad:
            name = f"seed{a.seed}_{os.path.basename(m)[1:]}"
            shutil.copy(m, os.path.join(a.keep, name))
            print(f"FAIL {name} (a mutant of a program in {os.path.relpath(os.path.dirname(m), OBX)}): {e}")
        print(f"mutants (seed {a.seed}): {len(ms) - len(bad)} clean, {len(bad)} failed"
              + (f" (kept in {a.keep})" if bad else ''))
        return 1 if bad else 0
    finally:
        for m in ms:
            if os.path.exists(m):
                os.remove(m)


if __name__ == '__main__':
    sys.exit(main())
