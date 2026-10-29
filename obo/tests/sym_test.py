#!/usr/bin/env python3
"""Stage 2 symbol-file tests: writer, reader, hashes, --check, staleness and CLI.

Every case writes a module tree in a private temp directory and uses a private
--sym-dir, so the repository is never polluted. The writer's output is compared
byte-for-byte after a reader round trip (--reemit-sym), and the reader's
structural validation is reached by recomputing the interface hash after a
deliberate corruption (see `rehash`).

    python3 obo/tests/sym_test.py [-k pattern] [--update]

--update rewrites tests/modules/golden/expected/*.sym; the default gate only
compares, so a writer change fails until the fixture is deliberately updated.
These tests are also wired into run_fork.py, so the mutation judges see them.
"""
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
FORK = os.path.dirname(HERE)
OBO = os.path.abspath(os.environ.get('OBO', os.path.join(FORK, 'obo')))
RT = FORK
GOLD = os.path.join(HERE, 'modules', 'golden')
UPDATE = '--update' in sys.argv

OBO_VERSION = '0.3'
SYM_FORMAT = 1
SYM_ABI = 1


class Fail(Exception):
    pass


# ------------------------------------------------------------------ helpers

def fnv(data):
    h = 0xcbf29ce484222325
    for b in data:
        h ^= b
        h = (h * 0x100000001b3) & 0xFFFFFFFFFFFFFFFF
    return h


def write_tree(root, files):
    for name, text in files.items():
        path = os.path.join(root, name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'w') as f:
            f.write(text)


def build(tree, symdir, root='main.obx', out=None, extra=(), check=False):
    args = [OBO]
    if check:
        args.append('--check')
    args.append(os.path.join(tree, root))
    if out is not None:
        args += ['-o', out]
    if not check:
        args += ['--rt', RT]
    args += ['-I', tree, '--sym-dir', symdir]
    args += list(extra)
    return subprocess.run(args, capture_output=True, text=True, timeout=120)


def ok(proc, what):
    if proc.returncode != 0:
        raise Fail(f"{what}: exit {proc.returncode}: {proc.stderr.strip()}")


def sym_text(path):
    with open(path, 'rb') as f:
        return f.read()


def sym_header(path):
    """The unhashed header lines, as a dict; body lines after `interface`."""
    lines = sym_text(path).decode().splitlines()
    head, body, in_body = {}, [], False
    deps = []
    for line in lines:
        if in_body:
            body.append(line)
            continue
        if line.startswith('interface '):
            head['interface'] = int(line.split()[1], 16)
            in_body = True
        elif line.startswith('dep '):
            _, name, path = line.split(' ', 2)
            deps.append((name, path))
        else:
            key, _, value = line.partition(' ')
            head[key] = value
    head['deps'] = deps
    head['body'] = '\n'.join(body) + ('\n' if body else '')
    return head


def rehash(path, edit, recompute=True):
    """Apply edit(bytes)->bytes to a .sym and optionally fix its interface line."""
    data = edit(sym_text(path))
    if recompute:
        lines = data.split(b'\n')
        i = next(k for k, l in enumerate(lines) if l.startswith(b'interface '))
        body = b'\n'.join(lines[i + 1:])
        lines[i] = b'interface 0x%016x' % fnv(body)
        data = b'\n'.join(lines)
    with open(path, 'wb') as f:
        f.write(data)


def mutate(path, old, new, recompute=True, count=1):
    data = sym_text(path)
    assert data.count(old) == count, f"{old!r} occurs {data.count(old)} times"
    rehash(path, lambda b: b.replace(old, new), recompute=recompute)


def sym_files(d):
    return sorted(f for f in os.listdir(d) if f.endswith('.sym'))


def reemit(path):
    return subprocess.run([OBO, '--reemit-sym', path], capture_output=True, timeout=120)


def tree(files, prefix='symcase-'):
    d = tempfile.mkdtemp(prefix=prefix)
    write_tree(d, files)
    symdir = os.path.join(d, 'sym')
    os.makedirs(symdir)
    return d, symdir


RICH_LIB = '''module lib

pub type Node = ref record { value: int; next: ?Node }
pub type Shape = closed { Circle { r: int } Rect { w: int; h: int } }
pub type Builder = ref record { items: []int }
pub type Holder = ref record { c: ?Circle }
pub type Text = []string
pub type NodeAlias = Node
pub type CircleAlias = Circle
pub const Limit = 10
pub const Big = byte(200)
pub const Yes = true
pub const Empty = ""
pub const Bytes = "a\\tb\\n"
pub const Negative = 0 - 5
pub var Cache: []Node = new []Node(0)
pub var freesall: int = 3
pub let Default: Node = new Node{value: 1, next: nil}

fn hidden(): int { return 1 }

pub fn make(v: int): Node { return new Node{value: v, next: nil} }
pub fn area(s: Shape): int {
    match s {
        Circle { return 3 * s.r * s.r }
        Rect { return s.w * s.h }
    }
}
pub fn put(mut b: &Builder, v: int) { append(b.items, v) }
pub fn clear(mut b: &Builder) { truncate(b.items, 0) }
pub fn wipe() { Cache = new []Node(0); freesall = 0 }
pub fn pure(x: int): int { return x + hidden() }
'''

RICH_MAIN = '''module main
import "lib.obx"
import "lib.obx" as l2
fn main() {
    let n = lib::make(7)
    println(n.value, " ", lib::Limit, " ", lib::Yes, " ", l2::Big)
    let c = new lib::Circle{r: 2}
    println(lib::area(c))
    println(lib::Empty == "", " ", lib::Negative)
}
'''


def rich_case():
    return tree({'lib.obx': RICH_LIB, 'main.obx': RICH_MAIN})


# -------------------------------------------------------------------- tests

def roundtrip_rich():
    """Every grammar production: writer -> reader -> re-emit is byte-identical."""
    d, s = rich_case()
    try:
        ok(build(d, s), 'build')
        for name in sym_files(s):
            p = reemit(os.path.join(s, name))
            ok(p, f'reemit {name}')
            want = sym_text(os.path.join(s, name))
            if p.stdout != want:
                raise Fail(f"re-emission of {name} is not byte-identical")
        ok(build(d, s, check=True), 'check')      # variants keep their parent in check mode
    finally:
        shutil.rmtree(d, ignore_errors=True)


def determinism():
    """Two builds in different directories emit byte-identical .sym files."""
    d1, s1 = rich_case()
    d2, s2 = rich_case()
    try:
        ok(build(d1, s1), 'first build')
        ok(build(d2, s2), 'second build')
        if sym_files(s1) != sym_files(s2):
            raise Fail(f"different module sets: {sym_files(s1)} vs {sym_files(s2)}")
        for name in sym_files(s1):
            if sym_text(os.path.join(s1, name)) != sym_text(os.path.join(s2, name)):
                raise Fail(f"{name} differs between two builds")
    finally:
        shutil.rmtree(d1, ignore_errors=True)
        shutil.rmtree(d2, ignore_errors=True)


def golden():
    """The committed golden .sym fixtures match the writer byte-for-byte."""
    src = os.path.join(GOLD, 'tree')
    expected = os.path.join(GOLD, 'expected')
    tmp = tempfile.mkdtemp(prefix='golden-')
    symdir = os.path.join(tmp, 'sym')
    os.makedirs(symdir)
    try:
        p = build(src, symdir, out=os.path.join(tmp, 'prog'))
        ok(p, 'golden build')
        got = sym_files(symdir)
        want = sorted(f for f in os.listdir(expected) if f.endswith('.sym')) if os.path.isdir(expected) else []
        if UPDATE:
            os.makedirs(expected, exist_ok=True)
            for name in got:
                shutil.copyfile(os.path.join(symdir, name), os.path.join(expected, name))
            return
        if got != want:
            raise Fail(f"golden set {want} != emitted {got}")
        for name in got:
            a = sym_text(os.path.join(symdir, name))
            b = sym_text(os.path.join(expected, name))
            if a != b:
                raise Fail(f"golden {name} differs; run sym_test.py --update after a deliberate writer change")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def hash_bytes():
    """source is the FNV of the source bytes; interface is the FNV of the body."""
    d, s = rich_case()
    try:
        ok(build(d, s), 'build')
        for mod, src in (('lib', 'lib.obx'), ('main', 'main.obx')):
            h = sym_header(os.path.join(s, mod + '.sym'))
            if int(h['source'], 16) != fnv(open(os.path.join(d, src), 'rb').read()):
                raise Fail(f"{mod}: source hash is not the FNV of {src}")
            if h['interface'] != fnv(h['body'].encode()):
                raise Fail(f"{mod}: interface hash is not the FNV of the body")
    finally:
        shutil.rmtree(d, ignore_errors=True)


def hash_private_and_rename():
    """A private body/type change keeps the interface hash; a parameter rename keeps
    it too (canonical p0..); renaming a private freed global changes it."""
    files = {'lib.obx': '''module lib
pub type Node = ref record { v: int }
pub var owned: ?Node = nil
fn hidden(): int { let x = 1; return x }
fn wipe() { owned = nil }
type Private = ref record { q: int }
pub fn use(alpha: int): int { return alpha + hidden() }
pub fn clear() { wipe() }
''', 'main.obx': 'module main\nimport "lib.obx"\nfn main() { println(lib::use(1)) }\n'}
    d, s = tree(files)
    try:
        ok(build(d, s), 'base')
        p = os.path.join(s, 'lib.sym')
        base = sym_header(p)
        if base['body'].find('fn use(p0: int): int') < 0:
            raise Fail(f"the writer did not emit canonical p0: {base['body']!r}")
        # private body
        text = open(os.path.join(d, 'lib.obx')).read()
        open(os.path.join(d, 'lib.obx'), 'w').write(text.replace('let x = 1', 'let x = 2'))
        ok(build(d, s), 'private body')
        h = sym_header(p)
        if h['source'] == base['source'] or h['interface'] != base['interface']:
            raise Fail('a private body change must change source only')
        # parameter rename
        text = open(os.path.join(d, 'lib.obx')).read()
        open(os.path.join(d, 'lib.obx'), 'w').write(text.replace('alpha', 'beta'))
        ok(build(d, s), 'rename')
        h = sym_header(p)
        if h['source'] == base['source'] or h['interface'] != base['interface']:
            raise Fail('a parameter rename must change source only')
        if 'fn use(p0: int)' not in h['body']:
            raise Fail('the writer did not keep canonical p0 after a rename')
        # private type not named in any summary
        text = open(os.path.join(d, 'lib.obx')).read()
        open(os.path.join(d, 'lib.obx'), 'w').write(text.replace('q: int', 'q: int; r: int'))
        ok(build(d, s), 'private type')
        h = sym_header(p)
        if h['interface'] != base['interface']:
            raise Fail('a private type change must keep the interface hash')
        # a private global named in an exported frees list is interface data
        old = open(os.path.join(d, 'lib.obx')).read()
        open(os.path.join(d, 'lib.obx'), 'w').write(old.replace('owned', 'owned2').replace('owned2 = nil', 'owned2 = nil'))
        ok(build(d, s), 'private freed global')
        h = sym_header(p)
        if h['interface'] == base['interface']:
            raise Fail('renaming a private freed global must change the interface hash')
        if 'frees 1 lib::owned2' not in h['body']:
            raise Fail('the new key is not in the summary')
    finally:
        shutil.rmtree(d, ignore_errors=True)


def hash_depiface():
    """A dependency's interface change changes its dependents' depiface; a body-only
    change does not; an old dependent against the new interface is the mismatch error."""
    files = {
        'a.obx': 'module a\npub var g: int = 1\npub fn f(): int { return 2 }\n',
        'b.obx': 'module b\nimport "a.obx"\npub fn mid(): int { return a::f() + a::g }\n',
        'main.obx': 'module main\nimport "b.obx"\nfn main() { println(b::mid()) }\n',
    }
    d, s = tree(files)
    try:
        ok(build(d, s), 'base')
        a0 = sym_text(os.path.join(s, 'a.sym'))
        b0 = sym_text(os.path.join(s, 'b.sym'))
        # body-only change in a: b's interface and depiface stay
        open(os.path.join(d, 'a.obx'), 'w').write(files['a.obx'].replace('return 2', 'return 3'))
        ok(build(d, s), 'a body')
        if sym_header(os.path.join(s, 'b.sym'))['interface'] != sym_header_bytes(b0)['interface']:
            raise Fail("a dependency's body-only change changed the dependent's interface")
        # exported change in a: b's depiface changes
        open(os.path.join(d, 'a.obx'), 'w').write(files['a.obx'] + 'pub fn extra(): int { return 4 }\n')
        ok(build(d, s), 'a interface')
        b1 = sym_header(os.path.join(s, 'b.sym'))
        if b1['interface'] == sym_header_bytes(b0)['interface']:
            raise Fail("a dependency's interface change did not change the dependent's interface")
        if sym_header(os.path.join(s, 'a.sym'))['interface'] == sym_header_bytes(a0)['interface']:
            raise Fail("a's exported change did not change a's interface")
        # put the old b back: its depiface names the old a interface
        open(os.path.join(s, 'b.sym'), 'wb').write(b0)
        q = build(d, s, check=True)
        if q.returncode != 2 or 'depends on' not in q.stderr or 'rebuild the dependent module' not in q.stderr:
            raise Fail(f"depiface mismatch: exit {q.returncode}: {q.stderr.strip()}")
    finally:
        shutil.rmtree(d, ignore_errors=True)


def sym_header_bytes(data):
    """sym_header for an in-memory .sym."""
    d = tempfile.mkdtemp(prefix='sh-')
    try:
        p = os.path.join(d, 'x.sym')
        open(p, 'wb').write(data)
        return sym_header(p)
    finally:
        shutil.rmtree(d, ignore_errors=True)


def stale_missing_corrupt_versions():
    files = {
        'lib.obx': 'module lib\npub fn f(): int { return 1 }\n',
        'main.obx': 'module main\nimport "lib.obx"\nfn main() { println(lib::f()) }\n',
    }
    d, s = tree(files)
    p = os.path.join(s, 'lib.sym')
    try:
        ok(build(d, s), 'base')
        base = sym_text(p)
        # stale: a comment-only source change
        with open(os.path.join(d, 'lib.obx'), 'a') as f:
            f.write('// a comment\n')
        q = build(d, s, check=True)
        if q.returncode != 2 or 'is stale' not in q.stderr:
            raise Fail(f"stale: exit {q.returncode}: {q.stderr.strip()}")
        # a build succeeds and rewrites it
        ok(build(d, s), 'rebuild')
        # a changed source whose FNV-1a hash has the top bit set is stale too:
        # -1 is a legal hash, not a "source absent" sentinel (B1)
        found = False
        for k in range(20):
            body = ('module lib\npub fn f(): int { return 1 }\n// a%d\n' % k).encode()
            if fnv(body) >= (1 << 63):
                open(os.path.join(d, 'lib.obx'), 'wb').write(body)
                found = True
                break
        if not found:
            raise Fail('could not find a changed source with the FNV top bit set')
        q = build(d, s, check=True)
        if q.returncode != 2 or 'is stale' not in q.stderr:
            raise Fail(f"negative-hash stale: exit {q.returncode}: {q.stderr.strip()}")
        ok(build(d, s), 'rebuild negative hash')
        base = sym_text(p)
        # missing
        os.unlink(p)
        q = build(d, s, check=True)
        if q.returncode != 2 or "no symbol file for module 'lib'" not in q.stderr:
            raise Fail(f"missing: exit {q.returncode}: {q.stderr.strip()}")
        ok(build(d, s), 'recreate')
        # corrupt body hash
        data = sym_text(p)
        idx = data.index(b'\nmodule lib\n') + len(b'\nmodule lib')
        open(p, 'wb').write(data[:idx] + b'X' + data[idx + 1:])
        q = build(d, s, check=True)
        if q.returncode != 2 or 'is corrupt (interface hash)' not in q.stderr:
            raise Fail(f"corrupt: exit {q.returncode}: {q.stderr.strip()}")
        open(p, 'wb').write(base)
        # format / ABI / compiler, each its own exact message
        cases = [
            (b'obosym 1', b'obosym 2', 'has format 2; this compiler reads 1'),
            (b'\nabi 1\n', b'\nabi 9\n', 'has ABI 9; this compiler uses 1'),
            (b'compiler "0.3"', b'compiler "9.9"', "was built by compiler '9.9'; this is '0.3'"),
        ]
        for old, new, msg in cases:
            data = base
            data = data.replace(old, new, 1)
            open(p, 'wb').write(data)
            q = build(d, s, check=True)
            if q.returncode != 2 or msg not in q.stderr:
                raise Fail(f"{msg!r}: exit {q.returncode}: {q.stderr.strip()}")
        open(p, 'wb').write(base)
    finally:
        shutil.rmtree(d, ignore_errors=True)


def sourceless_and_build_requirement():
    files = {
        'lib.obx': 'module lib\npub fn f(): int { return 1 }\npub type T = ref record { v: int }\n',
        'main.obx': 'module main\nimport "lib.obx"\nfn main() { println(lib::f()) }\n',
    }
    d, s = tree(files)
    try:
        ok(build(d, s), 'base')
        src = os.path.join(d, 'lib.obx')
        os.rename(src, src + '.bak')
        q = build(d, s, check=True)
        ok(q, 'source-less check')
        q = build(d, s, out=os.path.join(d, 'p2'))
        if q.returncode != 2 or 'only --check may use a symbol file without source' not in q.stderr:
            raise Fail(f"source-less build: exit {q.returncode}: {q.stderr.strip()}")
        os.rename(src + '.bak', src)
    finally:
        shutil.rmtree(d, ignore_errors=True)


def symdir_errors():
    files = {'lib.obx': 'module lib\npub fn f(): int { return 1 }\n',
             'main.obx': 'module main\nimport "lib.obx"\nfn main() { println(lib::f()) }\n'}
    d, s = tree(files)
    try:
        missing = os.path.join(d, 'does-not-exist')
        q = build(d, missing, out=os.path.join(d, 'p'))
        want = f"symbol-file directory '{missing}' does not exist or is not writable"
        if q.returncode != 2 or want not in q.stderr:
            raise Fail(f"missing sym-dir: exit {q.returncode}: {q.stderr.strip()}")
        if os.path.exists(missing):
            raise Fail('the compiler created the sym-dir (it must not mkdir)')
        # single-file --emit-sym with a missing directory
        single = os.path.join(d, 'single.obx')
        open(single, 'w').write('module single\nfn main() { }\n')
        q = subprocess.run([OBO, single, '--emit-sym', '--sym-dir', missing, '--rt', RT], capture_output=True, text=True)
        if q.returncode != 2 or want not in q.stderr:
            raise Fail(f"emit-sym missing sym-dir: exit {q.returncode}: {q.stderr.strip()}")
        # a regular file in place of the directory
        notdir = os.path.join(d, 'not-a-dir')
        open(notdir, 'w').write('x')
        q = build(d, notdir, out=os.path.join(d, 'p'))
        if q.returncode != 2 or 'does not exist or is not writable' not in q.stderr:
            raise Fail(f"file as sym-dir: exit {q.returncode}: {q.stderr.strip()}")
    finally:
        shutil.rmtree(d, ignore_errors=True)


def root_rules():
    d = tempfile.mkdtemp(prefix='rootsym-')
    s = os.path.join(d, 'sym')
    os.makedirs(s)
    try:
        single = os.path.join(d, 'single.obx')
        open(single, 'w').write('module single\npub fn f(): int { return 1 }\nfn main() { println(f()) }\n')
        out = os.path.join(d, 'p')
        ok(subprocess.run([OBO, single, '-o', out, '--rt', RT, '--sym-dir', s], capture_output=True, text=True), 'single build')
        if os.path.exists(os.path.join(s, 'single.sym')):
            raise Fail('a single-file build wrote a root .sym without --emit-sym')
        ok(subprocess.run([OBO, single, '-o', out, '--rt', RT, '--sym-dir', s, '--emit-sym'], capture_output=True, text=True), 'emit-sym')
        if not os.path.exists(os.path.join(s, 'single.sym')):
            raise Fail('--emit-sym did not write the root .sym')
        r = subprocess.run([out], capture_output=True, text=True)
        if r.stdout != '1\n':
            raise Fail('--emit-sym changed the executable')
        nohdr = os.path.join(d, 'nohdr.obx')
        open(nohdr, 'w').write('fn main() { }\n')
        q = subprocess.run([OBO, nohdr, '-o', os.path.join(d, 'q'), '--rt', RT, '--emit-sym'], capture_output=True, text=True)
        if q.returncode != 1 or "needs a 'module Name' header" not in q.stderr:
            raise Fail(f"headerless --emit-sym: exit {q.returncode}: {q.stderr.strip()}")
        # a build with imports writes a header root's .sym
        write_tree(d, {'lib.obx': 'module lib\npub fn f(): int { return 1 }\n',
                       'main.obx': 'module main\nimport "lib.obx"\nfn main() { println(lib::f()) }\n'})
        ok(build(d, s, out=os.path.join(d, 'pm')), 'module build')
        if not os.path.exists(os.path.join(s, 'main.sym')):
            raise Fail('a build with imports did not write the root .sym')
        # --check writes the checked module's, without an executable; a private main is omitted
        priv = os.path.join(d, 'priv.obx')
        open(priv, 'w').write('module priv\npub fn f(): int { return 2 }\nfn main() { println(f()) }\n')
        ok(subprocess.run([OBO, '--check', priv, '--sym-dir', s], capture_output=True, text=True), 'private main check')
        text = sym_text(os.path.join(s, 'priv.sym')).decode()
        if 'fn main' in text:
            raise Fail('a private main is in the checked .sym')
        # a checked module may have an exported main; an importer rejects it
        pubm = os.path.join(d, 'pubm.obx')
        open(pubm, 'w').write('module pubm\npub fn main() { }\n')
        ok(subprocess.run([OBO, '--check', pubm, '--sym-dir', s], capture_output=True, text=True), 'pub main check')
        if 'fn main()' not in sym_text(os.path.join(s, 'pubm.sym')).decode():
            raise Fail('an exported main is not in the checked .sym')
        imp = os.path.join(d, 'imp.obx')
        open(imp, 'w').write('module imp\nimport "pubm.obx"\nfn main() { pubm::main() }\n')
        q = subprocess.run([OBO, '--check', imp, '-I', d, '--sym-dir', s], capture_output=True, text=True)
        if q.returncode != 1 or "may not declare 'main'" not in q.stderr:
            raise Fail(f"importer of an exported main: exit {q.returncode}: {q.stderr.strip()}")
    finally:
        shutil.rmtree(d, ignore_errors=True)


def check_mode_and_cli():
    files = {'lib.obx': 'module lib\npub fn f(): int { return 1 }\n',
             'main.obx': 'module main\nimport "lib.obx"\nfn main() { println(lib::f()) }\n'}
    d, s = tree(files)
    try:
        ok(build(d, s, out=os.path.join(d, 'p')), 'build')
        # --check writes the checked module's .sym and no executable
        q = build(d, s, check=True)
        ok(q, 'check')
        if os.path.exists(os.path.join(d, 'main')):
            raise Fail('--check wrote an executable')
        # -o rejected in check and deps
        for mode in (['--check'], ['--deps']):
            q = subprocess.run([OBO] + mode + [os.path.join(d, 'main.obx'), '-o', 'x', '-I', d, '--sym-dir', s],
                               capture_output=True, text=True)
            want = 'obxc: -o cannot be used with --check or --deps'
            if q.returncode != 2 or q.stderr.strip() != want:
                raise Fail(f"{mode}: exit {q.returncode}: {q.stderr.strip()}")
        # --version
        q = subprocess.run([OBO, '--version'], capture_output=True, text=True)
        want = f'obo {OBO_VERSION} sym-format {SYM_FORMAT} abi {SYM_ABI}\n'
        if q.returncode != 0 or q.stdout != want:
            raise Fail(f"--version: exit {q.returncode}: {q.stdout!r}")
        # mutually exclusive modes
        q = subprocess.run([OBO, os.path.join(d, 'main.obx'), '--check', '--emit-sym'], capture_output=True, text=True)
        if q.returncode != 2 or 'mutually exclusive' not in q.stderr:
            raise Fail(f"mutual exclusion: exit {q.returncode}: {q.stderr.strip()}")
        # a missing option value is a needs-a-value error
        q = subprocess.run([OBO, os.path.join(d, 'main.obx'), '-I'], capture_output=True, text=True)
        if q.returncode != 2 or 'needs a value' not in q.stderr:
            raise Fail(f"missing -I value: exit {q.returncode}: {q.stderr.strip()}")
        # --deps writes nothing and does not need a sym-dir
        q = subprocess.run([OBO, '--deps', os.path.join(d, 'main.obx'), '-I', d], capture_output=True, text=True)
        if q.returncode != 0 or q.stdout.splitlines() != [os.path.join(d, 'lib.obx'), os.path.join(d, 'main.obx')]:
            raise Fail(f"--deps: exit {q.returncode}: {q.stdout!r} {q.stderr!r}")
    finally:
        shutil.rmtree(d, ignore_errors=True)


def malformed_structures():
    """Rehashed structural corruption is a clean symbol-file error with a line:col."""
    files = {'lib.obx': '''module lib
pub type Node = ref record { v: int }
pub type Shape = closed { Dot { x: int } }
pub const Limit = 10
pub const Mask = byte(255)
pub const On = true
pub var cache: ?Node = nil
pub fn zap(mut a: &[]int) { truncate(a, 0) }
pub fn wipe() { cache = nil }
pub fn pure(x: int): int { return x + 1 }
''', 'main.obx': 'module main\nimport "lib.obx"\nfn main() { println(lib::pure(1)) }\n'}
    d, s = tree(files)
    lib = os.path.join(s, 'lib.sym')
    try:
        ok(build(d, s), 'base')
        base = sym_text(lib)
        cases = [
            ('pf index', b'pf 0 "!"', b'pf 9 "!"', 'out of range'),
            ('event prefix', b'pf 0 "!"', b'pf 0 "x!"', 'starts with'),
            ('event empty segment', b'pf 0 "!"', b'pf 0 "!/"', 'empty segment'),
            ('event bad index', b'pf 0 "!"', b'pf 0 "!/[a]"', 'expected an index'),
            ('row without bit', b'pfree 0x1', b'pfree 0x0', 'no pfree bit'),
            ('bit without row', b' pf 0 "!"', b'', 'has no pf row'),
            ('bit beyond params', b'pfree 0x1', b'pfree 0x4', 'does not exist'),
            ('mut without &', b'mut p0: &[]int', b'mut p0: []int', 'mut needs'),
            ('scalar borrow', b'p0: int', b'p0: &int', 'can be borrowed'),
            ('unknown type', b'v: int', b'v: Nope', "unknown type 'Nope'"),
            ('unknown foreign type', b'v: int', b'v: other::Nope', "unknown module 'other'"),
            ('duplicate field', b'{ v: int }', b'{ v: int; v: int }', 'duplicate field'),
            ('zero variants', b'closed { Dot { x: int } }', b'closed { }', 'at least one variant'),
            ('duplicate type', b'type Node = ref record { v: int }',
             b'type Node = ref record { v: int }\ntype Node = ref record { v: int }', 'duplicate definition'),
            ('definition without shell', b'type Node uid 0x5fca22110fba38d4\n', b'', 'no shell'),
            ('shell never defined', b'type Node = ref record { v: int }', b'', 'shell but no definition'),
            ('kind mismatch', b'type Node = ref record { v: int }', b'type Node = closed { Dot { x: int } }', 'duplicate type key'),
            ('duplicate export', b'fn pure(p0: int): int', b'fn pure(p0: int): int\nfn pure(p0: int): int', 'duplicate exported'),
            ('frees count too large', b' frees 1 lib::cache', b' frees 2 lib::cache', 'frees list'),
            ('duplicate frees key', b' frees 1 lib::cache', b' frees 2 lib::cache lib::cache', 'duplicate frees key'),
            ('unknown summary item', b' frees 1 lib::cache', b' frobnicate 1', "unknown summary item 'frobnicate'"),
            ('unknown declaration', b'module lib', b'module lib\nbogus 1', 'expected a declaration'),
            ('trailing garbage', b'fn pure(p0: int): int', b'fn pure(p0: int): int\n\n}', 'expected a declaration'),
            ('duplicate exported name', b'var cache: ?Node', b'var cache: ?Node\nvar cache: ?Node', 'duplicate exported'),
            ('pf index 62 on a no-pfree function', b'fn pure(p0: int): int', b'fn pure(p0: int): int pf 62 "!"', 'out of range'),
            ('non-primitive constant', b'const Limit: int', b'const Limit: Node', 'constant must have type'),
            ('byte constant out of range', b'0x00000000000000ff', b'0x0000000000000100', 'between 0 and 255'),
            ('wrong shell uid', b'uid 0x5fca22110fba38d4', b'uid 0x1111111111111111', 'uid does not match'),
            ('bool constant is an integer', b'const On: bool = true', b'const On: bool = 0x0000000000000001', 'true or false'),
        ]
        for name, old, new, detail in cases:
            if base.count(old) != 1:
                raise Fail(f"fixture {name}: {old!r} occurs {base.count(old)} times")
            open(lib, 'wb').write(base)
            mutate(lib, old, new)
            q = build(d, s, check=True)
            if q.returncode != 2 or 'symbol file:' not in q.stderr:
                raise Fail(f"{name}: exit {q.returncode}: {q.stderr.strip()}")
            if detail not in q.stderr:
                raise Fail(f"{name}: wanted {detail!r}, got {q.stderr.strip()!r}")
            if ':' not in q.stderr:
                raise Fail(f"{name}: no line:col in {q.stderr.strip()!r}")
        # high pfree bits (62/63) are ignored, and a row at index 62 needs no bit
        open(lib, 'wb').write(base)
        mutate(lib, b'pfree 0x1', b'pfree 0xc000000000000001')
        ok(build(d, s, check=True), 'high pfree bits')
        # a private global key in a frees list is allowed (and creates a stub)
        open(lib, 'wb').write(base)
        mutate(lib, b' frees 1 lib::cache', b' frees 2 lib::cache lib::hidden')
        ok(build(d, s, check=True), 'private frees key')
        # malformed hashes/uid: wrong length and wrong digits are clean errors
        open(lib, 'wb').write(base)
        import re as _re
        data = _re.sub(rb'(?m)^source 0x[0-9a-f]{16}$', b'source 0x1234', base)
        open(lib, 'wb').write(data)
        q = build(d, s, check=True)
        if q.returncode != 2 or 'malformed source hash' not in q.stderr:
            raise Fail(f"short source hash: exit {q.returncode}: {q.stderr.strip()}")
        open(lib, 'wb').write(base)
        import re as _re2
        rehash(lib, lambda b: _re2.sub(rb'uid 0x[0-9a-f]{16}', b'uid 0x1234', b, count=1))
        q = build(d, s, check=True)
        if q.returncode != 2 or 'malformed type uid' not in q.stderr:
            raise Fail(f"short uid: exit {q.returncode}: {q.stderr.strip()}")
        # dep/depiface partner errors: mutate the dependency's .sym and check the importer
        files2 = {
            'lib.obx': 'module lib\npub fn f(): int { return 1 }\n',
            'lib2.obx': 'module lib2\npub fn g(): int { return 2 }\n',
            'mid.obx': 'module mid\nimport "lib.obx"\nimport "lib2.obx"\npub fn m(): int { return lib::f() + lib2::g() }\n',
            'main.obx': 'module main\nimport "mid.obx"\nfn main() { println(mid::m()) }\n',
        }
        d2, s2 = tree(files2)
        try:
            ok(build(d2, s2), 'dep pair base')
            mid = os.path.join(s2, 'mid.sym')
            m0 = sym_text(mid)
            edits = [
                ('duplicate dep', False, lambda b: b.replace(b'dep lib "lib.obx"', b'dep lib "lib.obx"\ndep lib "lib.obx"', 1)),
                ('missing dep partner', False, lambda b: b.replace(b'dep lib2 "lib2.obx"\n', b'', 1)),
                ('duplicate depiface', True, lambda b: b.replace(b'depiface lib 0x', b'depiface lib2 0x0000000000000000\ndepiface lib 0x', 1)),
                ('order swapped', False, lambda b: b.replace(b'dep lib "lib.obx"\ndep lib2 "lib2.obx"', b'dep lib2 "lib2.obx"\ndep lib "lib.obx"', 1)),
                ('empty dep path', False, lambda b: b.replace(b'dep lib "lib.obx"', b'dep lib ""', 1)),
            ]
            for name, recalc, edit in edits:
                open(mid, 'wb').write(m0)
                rehash(mid, edit, recompute=recalc)
                q = build(d2, s2, check=True)
                if q.returncode != 2:
                    raise Fail(f"{name}: exit {q.returncode}: {q.stderr.strip()}")
                if name == 'empty dep path' and 'dependency path cannot be empty' not in q.stderr:
                    raise Fail(f"{name}: wanted the empty-path error, got {q.stderr.strip()!r}")
        finally:
            shutil.rmtree(d2, ignore_errors=True)
    finally:
        shutil.rmtree(d, ignore_errors=True)


def handwritten_interface():
    """The reader accepts grammar the writer does not emit: pfree 0x0, arbitrary
    parameter names, summary items in any order, and a pf row at index >= 62."""
    params = ', '.join(f'q{i}: int' for i in range(63))
    sym = f'''obosym 1
abi 1
compiler "0.3"
source 0x{0:016x}
interface 0x0
module many
fn many({params}): int pf 62 "!" pfree 0xc000000000000000
'''
    d = tempfile.mkdtemp(prefix='handsym-')
    s = os.path.join(d, 'sym')
    os.makedirs(s)
    try:
        open(os.path.join(d, 'main.obx'), 'w').write(
            'module main\nimport "many.obx"\nfn main() { println(many::many(' +
            ', '.join('1' for _ in range(63)) + ')) }\n')
        p = os.path.join(s, 'many.sym')
        open(p, 'w').write(sym)
        rehash(p, lambda b: b)
        q = build(d, s, check=True)
        ok(q, 'hand-written interface')
        # the canonical re-emission drops pfree 0x0 and renames the parameters
        r = reemit(p)
        ok(r, 'reemit hand-written')
        text = r.stdout.decode()
        if 'pfree' in text or 'q0' in text or 'pf 62 "!"' not in text:
            raise Fail(f"re-emission is not canonical: {text!r}")
    finally:
        shutil.rmtree(d, ignore_errors=True)


def b6_stub_order():
    """A summary key for A::g is one ^Sym whether A's interface loads before or after
    the middle module's: both orders reject the use-after-free."""
    files = {
        'a.obx': '''module a
pub type Box = ref record { v: int }
pub var g: ?Box = new Box{v: 1}
pub fn wipe() { g = nil }
''',
        'b.obx': 'module b\nimport "a.obx"\npub fn mid() { a::wipe() }\n',
        'c.obx': 'module c\nimport "b.obx"\npub fn outer() { b::mid() }\n',
        'order1.obx': 'module order1\nimport "c.obx"\nimport "a.obx"\nfn main() { let r = &a::g; c::outer(); println(r.v) }\n',
        'order2.obx': 'module order2\nimport "a.obx"\nimport "c.obx"\nfn main() { let r = &a::g; c::outer(); println(r.v) }\n',
    }
    d, s = tree(files)
    try:
        q = build(d, s, root='order1.obx', out=os.path.join(d, 'p1'))
        if q.returncode != 1 or 'may point to freed memory' not in q.stderr:
            raise Fail(f"order c,a build: exit {q.returncode}: {q.stderr.strip()}")
        for root in ('order1.obx', 'order2.obx'):
            q = build(d, s, root=root, check=True)
            if q.returncode != 1 or 'may point to freed memory' not in q.stderr:
                raise Fail(f"{root} check: exit {q.returncode}: {q.stderr.strip()}")
        if 'frees 1 a::g' not in sym_text(os.path.join(s, 'c.sym')).decode():
            raise Fail('the middle module summary does not carry the transitive key')
    finally:
        shutil.rmtree(d, ignore_errors=True)


def b7_aliases():
    files = {
        'a.obx': 'module a\npub type T = ref record { v: int }\npub fn make(v: int): T { return new T{v: v} }\n',
        'b.obx': '''module b
import "a.obx" as a1
import "a.obx" as a2
pub type A1 = a1::T
pub type A2 = a2::T
pub fn use(v: int): A1 { return a2::make(v) }
pub fn twice(x: A1): int { return x.v + x.v }
''',
        'main.obx': '''module main
import "a.obx"
import "b.obx"
fn main() {
    let x: a::T = b::use(4)
    let y: b::A1 = x
    let z = b::use(5)
    let w: b::A2 = z
    println(b::twice(y), " ", b::twice(w))
}
'''}
    d, s = tree(files)
    try:
        out = os.path.join(d, 'p')
        ok(build(d, s, out=out), 'build')
        r = subprocess.run([out], capture_output=True, text=True, env=dict(os.environ, OBO_CHECK='1'))
        if r.returncode != 0 or r.stdout != '8 10\n':
            raise Fail(f"run: exit {r.returncode}: {r.stdout!r} {r.stderr!r}")
        text = sym_text(os.path.join(s, 'b.sym')).decode()
        if 'type A1 = a::T' not in text or 'type A2 = a::T' not in text:
            raise Fail(f"aliases are not emitted as underlying types: {text!r}")
        if 'fn twice(p0: a::T): int' not in text or 'fn use(p0: int): a::T' not in text:
            raise Fail('an alias leaked into a signature')
        ok(build(d, s, check=True), 'check')
        # the alias keys do not re-declare T: a distinct struct is still distinct
        files2 = {'c.obx': 'module c\npub type T = ref record { v: int }\npub fn make(): T { return new T{v: 1} }\n',
                  'a.obx': files['a.obx'],
                  'main.obx': 'module main\nimport "a.obx"\nimport "c.obx"\nfn main() { let x = c::make(); println(x.v) }\n',
                  'bad.obx': 'module bad\nimport "a.obx"\nimport "c.obx"\nfn main() { let x: a::T = c::make() }\n'}
        d2, s2 = tree(files2)
        try:
            ok(build(d2, s2), 'c build')
            q = build(d2, s2, root='bad.obx', check=True)
            if q.returncode != 1 or 'cannot use c::T as a::T' not in q.stderr:
                raise Fail(f"distinct types: exit {q.returncode}: {q.stderr.strip()}")
        finally:
            shutil.rmtree(d2, ignore_errors=True)
    finally:
        shutil.rmtree(d, ignore_errors=True)


def effects_check():
    """pmodes, pfree and pf cross the boundary precisely; imported functions are known."""
    files = {
        'lib.obx': '''module lib
pub type Node = ref record { v: int }
pub type Box = ref record { items: []Node }
pub type Bag = ref record { items: []Node }
pub fn appendOnly(mut a: &[]Node, v: int) { append(a, new Node{v: v}) }
pub fn clear(mut a: &[]Node) { truncate(a, 0) }
pub fn pure(x: int): int { return x + 1 }
pub fn takeWhole(mut b: &Box) { b.items = new []Node(0) }
pub fn hw(mut a: &[]Node, h: ^Bag): int { h.items = new []Node(0); return 1 }
''',
        'main.obx': 'module main\nimport "lib.obx"\nfn main() { println(lib::pure(1)) }\n'}
    d, s = tree(files)
    cases = [
        ('append accepted', 'fn main() { let xs = new []lib::Node(0); append(xs, new lib::Node{v: 1}); let r = &xs[0]; lib::appendOnly(xs, 2); println(r.v) }', 0),
        ('truncate rejected', 'fn main() { let xs = new []lib::Node(0); append(xs, new lib::Node{v: 1}); let r = &xs[0]; lib::clear(xs); println(r.v) }', 1),
        ('pure accepted', 'fn main() { let xs = new []lib::Node(0); append(xs, new lib::Node{v: 1}); let r = &xs[0]; println(lib::pure(1)); println(r.v) }', 0),
        ('readonly to mut', 'fn f(r: &[]lib::Node) { lib::appendOnly(r, 1) } fn main() { }', 1),
        ('deep to writer', 'fn main() { let b = new lib::Bag{items: new []lib::Node(0)}; let xs = new []lib::Node(0); append(xs, new lib::Node{v: 1}); let r = &xs[0]; println(lib::hw(xs, handle(b))); println(r.v) }', 1),
        ('takeWhole rejected', 'fn main() { let b = new lib::Box{items: new []lib::Node(0)}; append(b.items, new lib::Node{v: 1}); let p = &b.items[0]; lib::takeWhole(b); println(p.v) }', 1),
    ]
    try:
        ok(build(d, s), 'build')
        text = sym_text(os.path.join(s, 'lib.sym')).decode()
        for want in ('pfree 0x1 pf 0 "!"', 'pfree 0x1 pf 0 "=/items"', 'freesall writeshandles'):
            if want not in text:
                raise Fail(f"summary item missing: {want!r} in {text!r}")
        for name, body, want in cases:
            open(os.path.join(d, 'c1.obx'), 'w').write('module c1\nimport "lib.obx"\n' + body + '\n')
            q = build(d, s, root='c1.obx', check=True)
            if (q.returncode == 0) != (want == 0):
                raise Fail(f"{name}: exit {q.returncode}: {q.stderr.strip()}")
    finally:
        shutil.rmtree(d, ignore_errors=True)


def scanner_restore():
    """Several imports with declarations after each: the scanner state is restored
    around every dependency compile and every .sym read."""
    files = {
        'a.obx': 'module a\npub fn fa(): int { return 1 }\n',
        'b.obx': 'module b\nimport "a.obx"\npub fn fb(): int { return a::fa() + 1 }\n',
        'c.obx': 'module c\nimport "b.obx"\nimport "a.obx"\npub fn fc(): int { return a::fa() + b::fb() }\n',
        'main.obx': '''module main
import "a.obx"
import "c.obx"
import "b.obx"
pub var marker: int = 7
fn helper(): int { return marker }
fn main() { println(a::fa(), c::fc(), b::fb(), helper()) }
'''}
    d, s = tree(files)
    try:
        out = os.path.join(d, 'p')
        ok(build(d, s, out=out), 'build')
        r = subprocess.run([out], capture_output=True, text=True, env=dict(os.environ, OBO_CHECK='1'))
        if r.returncode != 0 or r.stdout != '1327\n':
            raise Fail(f"run: exit {r.returncode}: {r.stdout!r} {r.stderr!r}")
        ok(build(d, s, check=True), 'check')
    finally:
        shutil.rmtree(d, ignore_errors=True)


def reader_does_not_use_lookup():
    """The .sym reader resolves the module's own types, not the importer's scope:
    an importer's private type with the same name must not shadow the imported one."""
    files = {
        'lib.obx': 'module lib\npub type T = ref record { v: int }\npub fn make(): T { return new T{v: 1} }\npub fn get(t: T): int { return t.v }\n',
        'main.obx': '''module main
import "lib.obx"
type T = ref record { q: int }
fn main() {
    let x = lib::make()
    println(lib::get(x))
}
'''}
    d, s = tree(files)
    try:
        ok(build(d, s), 'build')
        q = build(d, s, check=True)
        ok(q, 'check with a local T')
    finally:
        shutil.rmtree(d, ignore_errors=True)


def module_names_int_result():
    """A module named int/Result is representable: int::T and Result::T parse, and
    Result[T] stays distinguishable from a module-qualified Result::T."""
    files = {
        'int.obx': 'module int\npub type T = ref record { v: int }\npub fn make(): T { return new T{v: 3} }\n',
        'Result.obx': 'module Result\npub type T = ref record { v: int }\npub fn r(): Result[int] { return ok(1) }\n',
        'main.obx': '''module main
import "int.obx" as ii
import "Result.obx" as rr
fn main() {
    let x = ii::make()
    println(x.v)
    let y = rr::r()
    match y { ok { println(y.value) } err { } }
}
'''}
    d, s = tree(files)
    try:
        out = os.path.join(d, 'p')
        ok(build(d, s, out=out), 'build')
        r = subprocess.run([out], capture_output=True, text=True, env=dict(os.environ, OBO_CHECK='1'))
        if r.returncode != 0 or r.stdout != '3\n1\n':
            raise Fail(f"run: exit {r.returncode}: {r.stdout!r} {r.stderr!r}")
        for name in ('int.sym', 'Result.sym'):
            p = os.path.join(s, name)
            text = sym_text(p).decode()
            if 'type T uid' not in text:
                raise Fail(f"{name} has no shell")
            r = reemit(p)
            ok(r, f'reemit {name}')
            if r.stdout != sym_text(p):
                raise Fail(f"{name} does not round-trip")
        ok(build(d, s, check=True), 'check')
    finally:
        shutil.rmtree(d, ignore_errors=True)


def checked_root_basename():
    """The checked root is exempt from the file-name rule; a checked dependency is not."""
    d = tempfile.mkdtemp(prefix='basename-')
    s = os.path.join(d, 'sym')
    os.makedirs(s)
    try:
        open(os.path.join(d, 'foo.obx'), 'w').write('module bar\npub fn f(): int { return 1 }\n')
        q = build(d, s, root='foo.obx', check=True)
        ok(q, 'checked root with a different basename')
        if not os.path.exists(os.path.join(s, 'bar.sym')):
            raise Fail('the checked module .sym is not named by module name')
        # a dependency with a mismatched basename (its source is present, its .sym is not)
        open(os.path.join(d, 'wrong.obx'), 'w').write('module other\npub fn g(): int { return 2 }\n')
        open(os.path.join(d, 'user.obx'), 'w').write('module user\nimport "wrong.obx"\nfn main() { }\n')
        q = build(d, s, root='user.obx', check=True)
        if q.returncode != 1 or "must be in a file named other.obx" not in q.stderr:
            raise Fail(f"checked dependency basename: exit {q.returncode}: {q.stderr.strip()}")
        # a dependency's .sym whose module line disagrees with the import basename
        open(os.path.join(d, 'good.obx'), 'w').write('module other\npub fn g(): int { return 2 }\n')
        ok(build(d, s, root='good.obx', check=True), 'good check')
        shutil.copyfile(os.path.join(s, 'other.sym'), os.path.join(s, 'dep.sym'))
        open(os.path.join(d, 'usebad.obx'), 'w').write('module usebad\nimport "dep.obx"\nfn main() { }\n')
        q = build(d, s, root='usebad.obx', check=True)
        if q.returncode != 1 or "must be in a file named other.obx" not in q.stderr:
            raise Fail(f"checked dep sym name: exit {q.returncode}: {q.stderr.strip()}")
    finally:
        shutil.rmtree(d, ignore_errors=True)


def default_symdir():
    """Without --sym-dir the root source's directory holds the .sym files; a
    single-file build still writes none."""
    d = tempfile.mkdtemp(prefix='defaultsym-')
    tree = os.path.join(d, 'tree')
    os.makedirs(tree)
    write_tree(tree, {'lib.obx': 'module lib\npub fn f(): int { return 1 }\n',
                      'main.obx': 'module main\nimport "lib.obx"\nfn main() { println(lib::f()) }\n'})
    try:
        q = subprocess.run([OBO, os.path.join(tree, 'main.obx'), '-o', os.path.join(d, 'p'),
                            '--rt', RT, '-I', tree], capture_output=True, text=True, cwd=d)
        ok(q, 'default sym-dir build')
        for name in ('lib.sym', 'main.sym'):
            if not os.path.exists(os.path.join(tree, name)):
                raise Fail(f'the default sym-dir is not the root source\'s directory: no {name} in the tree')
            if os.path.exists(os.path.join(d, name)):
                raise Fail('the default sym-dir wrote into the current directory')
        # a single-file build writes nothing
        single = os.path.join(d, 'single.obx')
        open(single, 'w').write('module single\nfn main() { }\n')
        q = subprocess.run([OBO, single, '-o', os.path.join(d, 'sp'), '--rt', RT],
                           capture_output=True, text=True, cwd=d)
        ok(q, 'single build')
        if os.path.exists(os.path.join(d, 'single.sym')):
            raise Fail('a single-file build wrote a root .sym')
    finally:
        shutil.rmtree(d, ignore_errors=True)


def check_requires_header():
    d = tempfile.mkdtemp(prefix='checkhdr-')
    s = os.path.join(d, 'sym')
    os.makedirs(s)
    try:
        open(os.path.join(d, 'nohdr.obx'), 'w').write('fn main() { }\n')
        q = subprocess.run([OBO, '--check', os.path.join(d, 'nohdr.obx'), '--sym-dir', s],
                           capture_output=True, text=True)
        if q.returncode != 1 or "needs a 'module Name' header" not in q.stderr:
            raise Fail(f"headerless --check: exit {q.returncode}: {q.stderr.strip()}")
    finally:
        shutil.rmtree(d, ignore_errors=True)


def transitive_type_check():
    """A signature may mention a type from a module the importer does not import
    directly: reading the middle .sym loads the transitive interface."""
    files = {
        'lib.obx': 'module lib\npub type T = ref record { v: int }\n',
        'mid.obx': 'module mid\nimport "lib.obx"\npub fn m(): lib::T { return new lib::T{v: 4} }\n',
        'main.obx': 'module main\nimport "mid.obx"\nfn main() { let x = mid::m(); println(x.v) }\n',
    }
    d, s = tree(files)
    try:
        out = os.path.join(d, 'p')
        ok(build(d, s, out=out), 'build')
        r = subprocess.run([out], capture_output=True, text=True, env=dict(os.environ, OBO_CHECK='1'))
        if r.returncode != 0 or r.stdout != '4\n':
            raise Fail(f"run: exit {r.returncode}: {r.stdout!r} {r.stderr!r}")
        os.rename(os.path.join(d, 'lib.obx'), os.path.join(d, 'lib.obx.bak'))
        os.rename(os.path.join(d, 'mid.obx'), os.path.join(d, 'mid.obx.bak'))
        ok(build(d, s, check=True), 'source-less transitive check')
    finally:
        shutil.rmtree(d, ignore_errors=True)


def forward_variant_check():
    """A forward-declared record whose field names a later closed type's variant:
    definitions are emitted in definition-line order, so check mode can read the
    writer's own output and re-emit it byte-for-byte (B2)."""
    files = {
        'lib.obx': '''module lib
pub type Holder
pub type Shape = closed { Circle { r: int } Rect { w: int; h: int } }
pub type Holder = ref record { c: ?Circle }
pub fn make(): Holder { return new Holder{c: new Circle{r: 1}} }
''',
        'main.obx': 'module main\nimport "lib.obx"\nfn main() { let h = lib::make() }\n',
    }
    d, s = tree(files)
    try:
        ok(build(d, s), 'build')
        text = sym_text(os.path.join(s, 'lib.sym')).decode()
        if text.index('type Shape = closed') > text.index('type Holder = ref record'):
            raise Fail('definitions are not in definition-line order')
        ok(build(d, s, check=True), 'check')
        r = reemit(os.path.join(s, 'lib.sym'))
        ok(r, 'reemit')
        if r.stdout != sym_text(os.path.join(s, 'lib.sym')):
            raise Fail('the forward variant interface does not round-trip')
    finally:
        shutil.rmtree(d, ignore_errors=True)


def b6_stub_completion():
    """The mandatory B6 case: a middle .sym whose summary names x::g but has no
    depiface to x creates the stub before x loads; both load orders reject the
    use-after-free through the same ^Sym."""
    x = 'module x\npub type Box = ref record { v: int }\npub var g: Box = new Box{v: 1}\n'
    files = {'x.obx': x,
             'main.obx': 'module main\nimport "x.obx"\nfn main() { let r = &x::g; println(r.v) }\n'}
    d, s = tree(files)
    try:
        ok(build(d, s), 'x build')
        m = os.path.join(s, 'm.sym')
        open(m, 'w').write('obosym 1\nabi 1\ncompiler "0.3"\nsource 0x%016x\ninterface 0x0\nmodule m\nfn f() frees 1 x::g\n' % 0)
        rehash(m, lambda b: b)
        for name, imports in (('order1', 'import "m.obx"\nimport "x.obx"'),
                              ('order2', 'import "x.obx"\nimport "m.obx"')):
            open(os.path.join(d, name + '.obx'), 'w').write(
                'module ' + name + '\n' + imports + '\nfn main() { let r = &x::g; m::f(); println(r.v) }\n')
            q = build(d, s, root=name + '.obx', check=True)
            if q.returncode != 1 or 'may point to freed memory' not in q.stderr:
                raise Fail(f"{name}: exit {q.returncode}: {q.stderr.strip()}")
    finally:
        shutil.rmtree(d, ignore_errors=True)


def nested_sym_error():
    """A structural error in a .sym that has a depiface is still a symbol-file
    error (obxc: ... symbol file:, exit 2), not a source error (B3)."""
    files = {
        'lib.obx': 'module lib\npub fn f(): int { return 1 }\n',
        'mid.obx': 'module mid\nimport "lib.obx"\npub fn m(): int { return lib::f() }\n',
        'main.obx': 'module main\nimport "mid.obx"\nfn main() { println(mid::m()) }\n',
    }
    d, s = tree(files)
    try:
        ok(build(d, s), 'build')
        mid = os.path.join(s, 'mid.sym')
        data = sym_text(mid).replace(b'fn m(): int', b'fn m(: int', 1)
        open(mid, 'wb').write(data)
        rehash(mid, lambda b: b)
        q = build(d, s, check=True)
        if q.returncode != 2 or not q.stderr.startswith('obxc: ') or 'symbol file:' not in q.stderr:
            raise Fail(f"nested sym error: exit {q.returncode}: {q.stderr.strip()}")
    finally:
        shutil.rmtree(d, ignore_errors=True)


def duplicate_import_pair():
    """The same module imported with two aliases has one dep/depiface pair and runs once."""
    files = {
        'lib.obx': 'module lib\npub var n: int = 0\npub fn bump(): int { n = n + 1; return n }\n',
        'main.obx': 'module main\nimport "lib.obx"\nimport "lib.obx" as l2\nfn main() { println(lib::bump(), l2::bump()) }\n'}
    d, s = tree(files)
    try:
        out = os.path.join(d, 'p')
        ok(build(d, s, out=out), 'build')
        text = sym_text(os.path.join(s, 'main.sym')).decode()
        if text.count('dep lib') != 1 or text.count('depiface lib') != 1:
            raise Fail(f"duplicate import made two pairs: {text!r}")
        r = subprocess.run([out], capture_output=True, text=True, env=dict(os.environ, OBO_CHECK='1'))
        if r.returncode != 0 or r.stdout != '12\n':
            raise Fail(f"run: exit {r.returncode}: {r.stdout!r} {r.stderr!r}")
        ok(build(d, s, check=True), 'check')
    finally:
        shutil.rmtree(d, ignore_errors=True)


def tests():
    return [
        ('sym/roundtrip_rich', roundtrip_rich),
        ('sym/determinism', determinism),
        ('sym/golden', golden),
        ('sym/hash_bytes', hash_bytes),
        ('sym/hash_private_and_rename', hash_private_and_rename),
        ('sym/hash_depiface', hash_depiface),
        ('sym/stale_missing_corrupt_versions', stale_missing_corrupt_versions),
        ('sym/sourceless_and_build_requirement', sourceless_and_build_requirement),
        ('sym/symdir_errors', symdir_errors),
        ('sym/root_rules', root_rules),
        ('sym/check_mode_and_cli', check_mode_and_cli),
        ('sym/malformed_structures', malformed_structures),
        ('sym/handwritten_interface', handwritten_interface),
        ('sym/b6_stub_order', b6_stub_order),
        ('sym/b7_aliases', b7_aliases),
        ('sym/effects_check', effects_check),
        ('sym/scanner_restore', scanner_restore),
        ('sym/reader_does_not_use_lookup', reader_does_not_use_lookup),
        ('sym/module_names_int_result', module_names_int_result),
        ('sym/checked_root_basename', checked_root_basename),
        ('sym/duplicate_import_pair', duplicate_import_pair),
        ('sym/default_symdir', default_symdir),
        ('sym/check_requires_header', check_requires_header),
        ('sym/transitive_type_check', transitive_type_check),
        ('sym/forward_variant_check', forward_variant_check),
        ('sym/b6_stub_completion', b6_stub_completion),
        ('sym/nested_sym_error', nested_sym_error),
    ]


def main():
    pattern = sys.argv[sys.argv.index('-k') + 1] if '-k' in sys.argv else ''
    todo = [(n, f) for n, f in tests() if pattern in n]
    failed = 0
    for name, fn in todo:
        try:
            fn()
        except Exception as e:  # noqa: BLE001 - report the case, keep going
            failed += 1
            print(f"FAIL {name}\n     " + str(e).replace('\n', '\n     '))
        else:
            print(f"ok   {name}")
    print(f"\n{len(todo) - failed} passed, {failed} failed")
    sys.exit(1 if failed else 0)


if __name__ == '__main__':
    main()
