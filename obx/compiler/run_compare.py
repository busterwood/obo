#!/usr/bin/env python3
"""Checks the self-hosted compiler against the reference compiler (stage 4).

    python3 obx/compiler/run_compare.py                 # build, then compare --dump-ast on every test file
    python3 obx/compiler/run_compare.py --mutants 5000  # also on 5000 mutated programs (syntax errors)
    python3 obx/compiler/run_compare.py --hashseed      # ref's C does not depend on PYTHONHASHSEED

The compiler is built with ref (obxc.py compiler/main.obx -o compiler/out/obxc) and run
with OBX_CHECK=1, so a leak fails the run. The files are every .obx file under tests/,
ref/tests/, lib/ (with lib/tests), redteam/ and compiler/. Both compilers dump all files in
one process (`--dump-ast a b ...`); the obx compiler stops at the first syntax error (as
the compiler always does), so the run goes on from the next file. A file passes when the
dump, or the error message, is the same text. Mutants are made from the test programs
by token and byte edits (deterministic for a seed), so they cover the syntax errors that
the suites barely reach; they are written to a temporary directory.
"""
import argparse, glob, os, random, re, shutil, subprocess, sys, tempfile, time

HERE = os.path.dirname(os.path.abspath(__file__))
OBX = os.path.dirname(HERE)
REF = os.path.join(OBX, 'ref', 'obxc.py')
OUT = os.path.join(HERE, 'out')
SUITES = ['tests', 'ref/tests', 'lib', 'redteam', 'compiler']


def corpus():
    files = set()
    for s in SUITES:
        files.update(glob.glob(os.path.join(OBX, s, '**', '*.obx'), recursive=True))
    return sorted(files)


def build():
    os.makedirs(OUT, exist_ok=True)
    exe = os.path.join(OUT, 'obxc')
    t = time.perf_counter()
    r = subprocess.run([sys.executable, REF, os.path.join(HERE, 'main.obx'), '-o', exe],
                       capture_output=True, text=True)
    if r.returncode:
        sys.exit(f"building the compiler failed:\n{r.stderr}")
    print(f"built {os.path.relpath(exe, OBX)} in {time.perf_counter() - t:.1f}s")
    return exe


def sections(text):
    """`== path` sections of a multi-file dump: {path: text}."""
    out, cur, buf = {}, None, []
    for line in text.splitlines(keepends=True):
        if line.startswith('== '):
            if cur is not None:
                out[cur] = ''.join(buf)
            cur, buf = line[3:].rstrip('\n'), []
        else:
            buf.append(line)
    if cur is not None:
        out[cur] = ''.join(buf)
    return out


def chunks(xs, n):
    return [xs[i:i + n] for i in range(0, len(xs), n)]


def multi(part):
    """Arguments for the multi-file format (a single file is given twice)."""
    return part if len(part) > 1 else part + part


def run_ref(files):
    """{path: dump or 'error: ...'}; a Python traceback is reported as a crash of its file."""
    res = {}
    for part in chunks(files, 500):
        r = subprocess.run([sys.executable, REF, '--dump-ast'] + multi(part), capture_output=True,
                           text=True, errors='replace')
        if 'Traceback' not in r.stderr:
            res.update(sections(r.stdout))
            continue
        for f in part:
            r = subprocess.run([sys.executable, REF, '--dump-ast'] + multi([f]), capture_output=True,
                               text=True, errors='replace')
            if 'Traceback' in r.stderr:
                res[f] = 'CRASH ref: ' + r.stderr.strip().splitlines()[-1] + '\n'
            else:
                res.update(sections(r.stdout))
    return res


def run_obx(exe, files):
    """{path: text}. The compiler stops at a syntax error (exit 1 after `error: ...`); the run
    goes on from the next file. Another exit status (a trap, a leak) is a crash of the last
    file started."""
    res, i = {}, 0
    env = dict(os.environ, OBX_CHECK='1')
    while i < len(files):
        part = files[i:i + 2000]
        r = subprocess.run([exe, '--dump-ast'] + multi(part), capture_output=True, env=env)
        got = sections(r.stdout.decode('utf-8', 'replace'))
        res.update(got)
        if r.returncode == 0:
            i += len(part)
            continue
        started = [f for f in part if f in got]
        last = started[-1] if started else part[0]
        if r.returncode != 1 or not got.get(last, '').startswith('error: '):
            res[last] = (f"CRASH obx: exit {r.returncode}: "
                         f"{r.stderr.decode('utf-8', 'replace').strip()[:300]}\n")
        i += part.index(last) + 1
    return res


def first_diff(a, b):
    al, bl = a.splitlines(), b.splitlines()
    for k in range(max(len(al), len(bl))):
        x = al[k] if k < len(al) else '<end>'
        y = bl[k] if k < len(bl) else '<end>'
        if x != y:
            return f"line {k + 1}: ref {x!r} / obx {y!r}"
    return 'same lines'


# ---- mutants

TOK = re.compile(r'\s+|//[^\n]*|"(?:\\.|[^"\\\n])*"|\w+|..?', re.S)
EXTRA = ['&', 'mut &', 'move(', ')', '(', '[', ']', '{', '}', 'try ', '-', '0', '..', '.', ',', ';', '\n',
         'T', 'int', 'str', '[]', 'fn ', 'return', 'break', 'None', 'Some(', 'x', 'self', '_', 'match ',
         'value ', '<<', '%', 'let ', 'var ', 'if ', 'else ', 'type ', 'record ', 'closed ', 'pub ',
         'import "x"', 'extern "c" ', 'as ', ':', '=', '+=', '==', '|', 'once ', 'never', '@', '"', "'",
         "'\\", '\\', '0x', '1.', '.5', '1e', '9' * 45, '0x' + 'f' * 40, '"\\x4"', '"\\q"', "'ab'",
         '\r', '\r\n', '\t', '$', '#', '`', '?', '\x01', '\x7f', '\xe9', '€', '²', 'abé',
         '﻿', '"é"', "'é'", '"\\é"']


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


def make_mutants(files, n, seed, d):
    r = random.Random(seed)
    srcs = []
    for f in files:
        with open(f, 'rb') as fh:
            data = fh.read()
        try:
            srcs.append(data.decode('utf-8'))
        except UnicodeDecodeError:
            pass
    out = []
    for k in range(n):
        m = mutate(r.choice(srcs), r)
        p = os.path.join(d, f"m{k:05d}.obx")
        with open(p, 'wb') as fh:
            data = m.encode('utf-8')
            if r.random() < 0.02:       # a few with invalid UTF-8
                at = r.randrange(len(data) + 1)
                data = data[:at] + bytes([r.choice([0x80, 0xc0, 0xe2, 0xed, 0xf4, 0xff])]) + data[at:]
            fh.write(data)
        out.append(p)
    return out


# ---- determinism of ref

def hashseed(files):
    """Compiles every file to C with PYTHONHASHSEED=1 and =2 and compares output and errors."""
    from concurrent.futures import ThreadPoolExecutor

    def one(f):
        outs = []
        for seed in ('1', '2'):
            r = subprocess.run([sys.executable, REF, f, '--emit-c'], capture_output=True, text=True,
                               env=dict(os.environ, PYTHONHASHSEED=seed), errors='replace')
            outs.append((r.returncode, r.stdout, r.stderr))
        return f, outs[0] == outs[1]
    with ThreadPoolExecutor(os.cpu_count() or 4) as ex:
        bad = [f for f, same in ex.map(one, files) if not same]
    for f in bad:
        print(f"DIFFERS {os.path.relpath(f, OBX)}")
    print(f"hashseed: {len(files) - len(bad)} same, {len(bad)} differ")
    return 1 if bad else 0


def single(exe, files):
    """The one-file form: exit status and stderr must match too (the message goes to stderr)."""
    bad = 0
    for f in files:
        a = subprocess.run([sys.executable, REF, '--dump-ast', f], capture_output=True)
        b = subprocess.run([exe, '--dump-ast', f], capture_output=True, env=dict(os.environ, OBX_CHECK='1'))
        if (a.returncode, a.stdout, a.stderr) != (b.returncode, b.stdout, b.stderr):
            bad += 1
            print(f"DIFF (one file) {f}: ref exit {a.returncode} {a.stderr[:200]!r} / "
                  f"obx exit {b.returncode} {b.stderr[:200]!r}")
    return bad


def compare(exe, files, label, nsingle):
    t0 = time.perf_counter()
    ref = run_ref(files)
    t1 = time.perf_counter()
    obx = run_obx(exe, files)
    t2 = time.perf_counter()
    bad = 0
    errors = 0
    for f in files:
        a, b = ref.get(f, '<missing>\n'), obx.get(f, '<missing>\n')
        if a.startswith('error: '):
            errors += 1
        if a != b:
            bad += 1
            if bad <= 30:
                print(f"DIFF {os.path.relpath(f, OBX) if f.startswith(OBX) else f}: {first_diff(a, b)}")
    print(f"{label}: {len(files) - bad} same, {bad} differ ({errors} syntax errors); "
          f"ref {t1 - t0:.2f}s, obx {t2 - t1:.2f}s")
    errs = [f for f in files if ref.get(f, '').startswith('error: ')][:nsingle]
    sbad = single(exe, errs)
    print(f"{label}, one file at a time: {len(errs) - sbad} of the syntax errors same, {sbad} differ")
    return bad + sbad


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--mutants', type=int, default=0)
    ap.add_argument('--seed', type=int, default=1)
    ap.add_argument('--hashseed', action='store_true')
    ap.add_argument('--no-build', action='store_true')
    ap.add_argument('files', nargs='*')
    a = ap.parse_args()
    files = [os.path.abspath(f) for f in a.files] or corpus()
    if a.hashseed:
        return hashseed([f for f in files if not f.startswith(HERE)])
    exe = os.path.join(OUT, 'obxc') if a.no_build else build()
    bad = compare(exe, files, 'test files', 1000)
    if a.mutants:
        d = tempfile.mkdtemp(prefix='obxmut')
        try:
            ms = make_mutants(corpus(), a.mutants, a.seed, d)
            bad += compare(exe, ms, f'mutants (seed {a.seed})', 100)
        finally:
            shutil.rmtree(d)
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
