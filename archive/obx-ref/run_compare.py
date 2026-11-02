#!/usr/bin/env python3
"""Checks the self-hosted compiler against the reference compiler (stage 4).

    python3 obx/compiler/run_compare.py                 # build, then compare --dump-ast and --dump-types
    python3 obx/compiler/run_compare.py --mode types    # only --dump-types (also: --mode ast)
    python3 obx/compiler/run_compare.py --mode c        # only --emit-c: the C, or the error (also: all)
    python3 obx/compiler/run_compare.py --mutants 5000  # also on 5000 mutated programs (syntax and sema errors)
    python3 obx/compiler/run_compare.py --hashseed      # ref's C does not depend on PYTHONHASHSEED

The compiler is built with ref (obxc.py compiler/main.obx -o compiler/out/obxc) and run
with OBX_CHECK=1, so a leak fails the run. The files are every .obx file under tests/,
ref/tests/, lib/ (with lib/tests), redteam/ and compiler/. Both compilers dump many files
in one process (`--dump-ast a b ...`; ref's --dump-types runs in parallel batches, as it
loads std again for every file); the obx compiler stops at the first error (as the
compiler always does), so the run goes on from the next file. A file passes when the
dump, or the error message, is the same text.

--emit-c (--mode c) compares the whole C program of every file (or its first error, from
the parser, sema or the body checker); ref compiles one file per process (in parallel). A
difference names the C function where it starts.

--dump-types runs only sema (loading, declarations, constants), never the body checks,
so a program whose first error is in a body (a type error, a move) gives the full sema
dump on both sides, and its error is left to step 3. Every error before that point (a
syntax error in any module, an import, a declaration, a constant) is compared as text.

Mutants are made from the test programs by token and byte edits (deterministic for a
seed), so they cover the syntax and sema errors that the suites barely reach; they are
written to a temporary directory. Mutated imports stay valid: a mutant lives in the
directory of its source program (as a hidden .obxmut file, removed afterwards) when
`--dump-types` is compared, so its ./ imports still resolve.
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
    subprocess.run([sys.executable, os.path.join(HERE, 'gen_rt.py')], check=True)
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


def ref_c(f):
    """ref's --emit-c of one file as the obx compiler's multi-file form writes it."""
    r = subprocess.run([sys.executable, REF, f, '--emit-c'], capture_output=True, errors='replace', text=True)
    if 'Traceback' in r.stderr:
        return 'CRASH ref: ' + r.stderr.strip().splitlines()[-1] + '\n'
    return r.stdout if r.returncode == 0 else 'error: ' + r.stderr


def run_ref(files, mode):
    """{path: dump or 'error: ...'}; a Python traceback is reported as a crash of its file."""
    from concurrent.futures import ThreadPoolExecutor
    if mode == '--emit-c':
        with ThreadPoolExecutor(os.cpu_count() or 4) as ex:
            return dict(zip(files, ex.map(ref_c, files)))

    def batch(part):
        res = {}
        r = subprocess.run([sys.executable, REF, mode] + multi(part), capture_output=True,
                           text=True, errors='replace')
        if 'Traceback' not in r.stderr:
            res.update(sections(r.stdout))
            return res
        for f in part:
            r = subprocess.run([sys.executable, REF, mode] + multi([f]), capture_output=True,
                               text=True, errors='replace')
            if 'Traceback' in r.stderr:
                res[f] = 'CRASH ref: ' + r.stderr.strip().splitlines()[-1] + '\n'
            else:
                res.update(sections(r.stdout))
        return res
    res = {}
    size = 500 if mode == '--dump-ast' else 40
    with ThreadPoolExecutor(os.cpu_count() or 4) as ex:
        for got in ex.map(batch, chunks(files, size)):
            res.update(got)
    return res


def run_obx(exe, files, mode):
    """{path: text}. The compiler stops at a syntax error (exit 1 after `error: ...`); the run
    goes on from the next file. Another exit status (a trap, a leak) is a crash of the last
    file started."""
    res, i = {}, 0
    env = dict(os.environ, OBX_CHECK='1')
    while i < len(files):
        part = files[i:i + (50 if mode == '--emit-c' else 2000)]
        r = subprocess.run([exe, mode] + multi(part), capture_output=True, env=env)
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
            fn = next((al[j] for j in range(min(k, len(al) - 1), -1, -1)
                       if al[j].startswith('static ') and al[j].endswith('{')), None)
            where = f" (in {fn[:-2]})" if fn else ''
            return f"line {k + 1}{where}: ref {x[:300]!r} / obx {y[:300]!r}"
    return 'same lines'


# ---- mutants

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
    so the program mostly still parses and sema meets it."""
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


def single(exe, files, mode):
    """The one-file form: exit status and stderr must match too (the message goes to stderr)."""
    from concurrent.futures import ThreadPoolExecutor

    def one(f):
        a = subprocess.run([sys.executable, REF, mode, f], capture_output=True)
        b = subprocess.run([exe, mode, f], capture_output=True, env=dict(os.environ, OBX_CHECK='1'))
        if (a.returncode, a.stdout, a.stderr) != (b.returncode, b.stdout, b.stderr):
            return (f"DIFF (one file) {f}: ref exit {a.returncode} {a.stderr[:200]!r} / "
                    f"obx exit {b.returncode} {b.stderr[:200]!r}")
        return None
    with ThreadPoolExecutor(os.cpu_count() or 4) as ex:
        diffs = [d for d in ex.map(one, files) if d]
    for d in diffs:
        print(d)
    return len(diffs)


def compare(exe, files, label, nsingle, mode):
    label = f"{label} ({mode})"
    t0 = time.perf_counter()
    ref = run_ref(files, mode)
    t1 = time.perf_counter()
    obx = run_obx(exe, files, mode)
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
    print(f"{label}: {len(files) - bad} same, {bad} differ ({errors} errors); "
          f"ref {t1 - t0:.2f}s, obx {t2 - t1:.2f}s")
    errs = [f for f in files if ref.get(f, '').startswith('error: ')][:nsingle]
    sbad = single(exe, errs, mode)
    print(f"{label}, one file at a time: {len(errs) - sbad} of the errors same, {sbad} differ")
    return bad + sbad


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--mutants', type=int, default=0)
    ap.add_argument('--seed', type=int, default=1)
    ap.add_argument('--hashseed', action='store_true')
    ap.add_argument('--no-build', action='store_true')
    ap.add_argument('--mode', choices=['ast', 'types', 'c', 'both', 'all'], default='both')
    ap.add_argument('files', nargs='*')
    a = ap.parse_args()
    files = [os.path.abspath(f) for f in a.files] or corpus()
    if a.hashseed:
        return hashseed([f for f in files if not f.startswith(HERE)])
    exe = os.path.join(OUT, 'obxc') if a.no_build else build()
    modes = {'both': ['--dump-ast', '--dump-types'], 'all': ['--dump-ast', '--dump-types', '--emit-c'],
             'c': ['--emit-c']}.get(a.mode, ['--dump-' + a.mode])
    bad = 0
    for mode in modes:
        bad += compare(exe, files, 'test files', 1000, mode)
    if a.mutants:
        ms = []
        try:
            make_mutants(corpus(), a.mutants, a.seed, ms)
            for mode in modes:
                bad += compare(exe, ms, f'mutants (seed {a.seed})', 100, mode)
        finally:
            for m in ms:
                if os.path.exists(m):
                    os.remove(m)
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
