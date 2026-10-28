#!/usr/bin/env python3
"""Deterministic .sym corruption fuzzer for --check (design §15.1).

Builds a seed module tree once, then mutates the dependency's symbol file:
truncation, line deletion/duplication/swap, token replacement, hash-digit flips,
version changes, depiface edits, type/field/variant renames, summary corruption
and unknown declarations. Some mutations also recompute the interface hash, so
the reader's structural validators are reached rather than only the body hash.

Every run must exit 0 or 2 (1 is allowed only for the source-language rules that
an interface can violate: an exported `main`, a `_root` module, or a file/module
name mismatch), with no signal, trap or hang. The corpus is seeded, so two runs
produce the same outcome digest.

    python3 obo/tests/sym_fuzz.py [--iterations N] [--seed S]
"""
import hashlib
import os
import random
import re
import resource
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
FORK = os.path.dirname(HERE)
OBO = os.path.abspath(os.environ.get('OBO', os.path.join(FORK, 'obo')))
RT = FORK
ADDRESS_SPACE = 5 << 30
RULE_ONE = ("may not declare 'main'", "_root' is reserved", "must be in a file named",
            "has no exported member", "is not a type")

LIB = '''module lib

pub type Node = ref record { value: int; next: ?Node }
pub type Shape = closed { Circle { r: int } Rect { w: int; h: int } }
pub type Alias = []Node
pub const Limit = 10
pub const Mask = byte(255)
pub const On = true
pub const Greeting = "hi\\n"
pub var cache: ?Node = nil
pub var freesall: int = 1
pub let Root: Node = new Node{value: 0, next: nil}

fn hidden(): int { return 1 }

pub fn make(v: int): Node { return new Node{value: v, next: nil} }
pub fn area(s: Shape): int {
    match s {
        Circle { return 3 * s.r * s.r }
        Rect { return s.w * s.h }
    }
}
pub fn add(mut n: &Node, v: int) { n.value = n.value + v }
pub fn clear(mut a: &[]Node) { truncate(a, 0) }
pub fn wipe() { cache = nil; freesall = 0 }
pub fn pure(x: int): int { return x + hidden() }
'''

MAIN = '''module main
import "lib.obx"
fn main() {
    let n = lib::make(2)
    println(lib::area(new lib::Circle{r: 1}), " ", lib::Mask, " ", lib::On, " ", n.value)
}
'''


def fnv(data):
    h = 0xcbf29ce484222325
    for b in data:
        h ^= b
        h = (h * 0x100000001b3) & 0xFFFFFFFFFFFFFFFF
    return h


def rehash_bytes(data):
    lines = data.split(b'\n')
    try:
        i = next(k for k, l in enumerate(lines) if l.startswith(b'interface '))
    except StopIteration:
        return data
    body = b'\n'.join(lines[i + 1:])
    lines[i] = b'interface 0x%016x' % fnv(body)
    return b'\n'.join(lines)


def setup():
    d = tempfile.mkdtemp(prefix='sym-fuzz-')
    with open(os.path.join(d, 'lib.obx'), 'w') as f:
        f.write(LIB)
    with open(os.path.join(d, 'main.obx'), 'w') as f:
        f.write(MAIN)
    symdir = os.path.join(d, 'sym')
    os.makedirs(symdir)
    p = subprocess.run([OBO, os.path.join(d, 'main.obx'), '-o', os.path.join(d, 'prog'),
                        '--rt', RT, '-I', d, '--sym-dir', symdir], capture_output=True, text=True)
    if p.returncode != 0:
        raise SystemExit('the fuzz seed tree does not build: ' + p.stderr.strip())
    with open(os.path.join(symdir, 'lib.sym'), 'rb') as f:
        seed = f.read()
    return d, symdir, seed


def limits():
    resource.setrlimit(resource.RLIMIT_AS, (ADDRESS_SPACE, ADDRESS_SPACE))


MUTATORS = []


def mutator(fn):
    MUTATORS.append(fn)
    return fn


def pick_line(lines, pred):
    idx = [i for i, l in enumerate(lines) if pred(l)]
    return idx


@mutator
def truncate(rng, data):
    if len(data) < 2:
        return data
    return data[:rng.randrange(1, len(data))]


@mutator
def delete_line(rng, data):
    lines = data.split(b'\n')
    idx = pick_line(lines, lambda l: l.startswith((b'type ', b'const ', b'var ', b'let ', b'fn ', b'depiface ')) and b'interface' not in l)
    if not idx:
        return data
    del lines[rng.choice(idx)]
    return b'\n'.join(lines)


@mutator
def duplicate_line(rng, data):
    lines = data.split(b'\n')
    idx = pick_line(lines, lambda l: l.startswith((b'type ', b'const ', b'var ', b'let ', b'fn ', b'depiface ')))
    if not idx:
        return data
    i = rng.choice(idx)
    lines.insert(i, lines[i])
    return b'\n'.join(lines)


@mutator
def swap_lines(rng, data):
    lines = data.split(b'\n')
    i = rng.randrange(len(lines))
    j = rng.randrange(len(lines))
    if i == j or b'interface ' in lines[i] or b'interface ' in lines[j]:
        return data
    lines[i], lines[j] = lines[j], lines[i]
    return b'\n'.join(lines)


@mutator
def replace_identifier(rng, data):
    names = [b'Node', b'Shape', b'Circle', b'cache', b'Limit', b'make', b'area', b'add']
    old = rng.choice(names)
    new = rng.choice([b'Nope', b'Other', b'broken', b'9x'])
    return data.replace(old, new, 1)


@mutator
def replace_number(rng, data):
    spots = [m.span() for m in re.finditer(rb'0x[0-9a-f]{16}', data)]
    if not spots:
        return data
    start, end = rng.choice(spots)
    digits = bytearray(data[start + 2:end])
    i = rng.randrange(16)
    digits[i] = ord(rng.choice('0123456789abcdef'))
    return data[:start + 2] + bytes(digits) + data[end:]


@mutator
def change_version(rng, data):
    choice = rng.randrange(3)
    if choice == 0:
        return re.sub(rb'(?m)^obosym \d+$', b'obosym 2', data, count=1)
    if choice == 1:
        return re.sub(rb'(?m)^abi \d+$', b'abi 9', data, count=1)
    return re.sub(rb'(?m)^compiler "[^"]*"$', b'compiler "9.9"', data, count=1)


@mutator
def change_dep(rng, data):
    if rng.randrange(2):
        return data.replace(b'dep lib "lib.obx"', b'dep lib "other.obx"', 1)
    return re.sub(rb'(?m)^depiface lib 0x[0-9a-f]{16}$', b'depiface lib 0x0000000000000000', data, count=1)


@mutator
def corrupt_summary(rng, data):
    options = [
        (b'pfree 0x1', b'pfree 0x0'),
        (b'pfree 0x1', b'pfree 0x4'),
        (b'pf 0 "!"', b'pf 9 "!"'),
        (b'pf 0 "!"', b'pf 0 "x"'),
        (b'pf 0 "!"', b'pf 0 "!/"'),
        (b' pf 0 "!"', b''),
        (b' frees 1 lib::cache', b' frees 2 lib::cache'),
        (b' frees 1 lib::cache', b' frees 2 lib::cache lib::cache'),
        (b'mut p0: &Node', b'mut p0: Node'),
        (b'p0: int', b'p0: &int'),
    ]
    old, new = rng.choice(options)
    return data.replace(old, new, 1)


@mutator
def insert_unknown(rng, data):
    lines = data.split(b'\n')
    i = rng.randrange(len(lines) + 1)
    lines.insert(i, rng.choice([b'bogus 1', b'}', b'type X = closed { }', b'fn f(p0: &int)']))
    return b'\n'.join(lines)


def run_case(d, symdir, data, timeout=10):
    # temp paths are normalized so two runs produce the same outcome digest
    path = os.path.join(symdir, 'lib.sym')
    with open(path, 'wb') as f:
        f.write(data)
    try:
        p = subprocess.run([OBO, '--check', os.path.join(d, 'main.obx'), '-I', d, '--sym-dir', symdir],
                           capture_output=True, text=True, timeout=timeout, preexec_fn=limits)
    except subprocess.TimeoutExpired:
        return 124, 'timeout'
    err = p.stderr.strip().replace(d, '<tmp>').replace(symdir, '<tmp>/sym')
    return p.returncode, err


def main():
    iterations = 200
    seed = 20261028
    if '--iterations' in sys.argv:
        iterations = int(sys.argv[sys.argv.index('--iterations') + 1])
    if '--seed' in sys.argv:
        seed = int(sys.argv[sys.argv.index('--seed') + 1])
    d, symdir, base = setup()
    outcomes = []
    failures = 0
    try:
        rng = random.Random(seed)
        for i in range(iterations):
            data = base
            for _ in range(rng.randrange(1, 4)):
                data = rng.choice(MUTATORS)(rng, data)
            if rng.randrange(2):
                data = rehash_bytes(data)
            code, err = run_case(d, symdir, data)
            outcomes.append((i, code, err[:40]))
            allowed = code in (0, 2) or (code == 1 and any(m in err for m in RULE_ONE))
            if code < 0 or code not in (0, 1, 2) or not allowed:
                failures += 1
                print(f"FAIL iteration {i}: exit {code}: {err[:200]}")
            if 'timeout' in err:
                failures += 1
                print(f"FAIL iteration {i}: timeout")
    finally:
        shutil.rmtree(d, ignore_errors=True)
    digest = hashlib.sha256(repr(outcomes).encode()).hexdigest()[:16]
    print(f"sym fuzz: {iterations - failures}/{iterations} clean, seed {seed}, digest {digest}")
    sys.exit(1 if failures else 0)


if __name__ == '__main__':
    main()
