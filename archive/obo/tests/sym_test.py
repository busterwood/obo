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

OBO_VERSION = '0.19'
SYM_FORMAT = 12
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

pub type Node = ref record { pub value: int; pub next: ?Node }
pub type Shape = closed { Circle { r: int } Rect { w: int; h: int } }
pub type Builder = ref record { pub items: []int }
pub type Holder = ref record { pub c: ?Circle }
pub type Bag = ref record { pub items: []Node; pub n: int }
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

pub fn (n: &Node) worth(): int { return n.value }
pub fn (mut b: &Bag) add(n: Node) {
    append(b.items, n)
    b.n += 1
}
pub fn (mut b: &Bag) reset() { b.items = new []Node(0) }
pub fn (b: Bag) count(): int { return len(b.items) }
fn (b: &Bag) secret(): int { return b.n }
pub fn tpl(name: string): Html { return html`<p>${name}</p>` }
pub fn sqlq(id: int): Sql { return sql`SELECT ${id}` }
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
    println(n.worth())
    let b = new lib::Bag{items: new []lib::Node(0), n: 0}
    b.add(lib::make(3))
    println(b.n)
    b.reset()
    println(b.count())
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
        lib = sym_text(os.path.join(s, 'lib.sym')).decode()
        for line in ('fn (p0: &Node) worth(): int',
                     'fn (mut p0: &Bag) add(p1: Node)',
                     'fn (mut p0: &Bag) reset() pfree 0x1 pf 0 "=/items"',
                     'fn (p0: Bag) count(): int',
                     'fn tpl(p0: string): Html',
                     'fn sqlq(p0: int): Sql'):
            if line not in lib:
                raise Fail(f"method line missing from lib.sym: {line!r}")
        if 'secret' in lib:
            raise Fail('a private method was emitted')
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
pub type Node = ref record { pub v: int }
pub var owned: ?Node = nil
fn hidden(): int { let x = 1; return x }
fn wipe() { owned = nil }
type Private = ref record { pub q: int }
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
            (b'obosym 12', b'obosym 8', 'has format 8; this compiler reads 12'),
            (b'\nabi 1\n', b'\nabi 9\n', 'has ABI 9; this compiler uses 1'),
            (b'compiler "0.19"', b'compiler "9.9"', "was built by compiler '9.9'; this is '0.19'"),
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
        'lib.obx': 'module lib\npub fn f(): int { return 1 }\npub type T = ref record { pub v: int }\n',
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
pub type Node = ref record { pub v: int }
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
            ('duplicate field', b'{ pub v: int }', b'{ pub v: int; pub v: int }', 'duplicate field'),
            ('zero variants', b'closed { Dot { pub x: int } }', b'closed { }', 'at least one variant'),
            ('duplicate type', b'type Node = ref record { pub v: int }',
             b'type Node = ref record { pub v: int }\ntype Node = ref record { pub v: int }', 'duplicate definition'),
            ('definition without shell', b'type Node uid 0x5fca22110fba38d4\n', b'', 'no shell'),
            ('shell never defined', b'type Node = ref record { pub v: int }', b'', 'shell but no definition'),
            ('kind mismatch', b'type Node = ref record { pub v: int }', b'type Node = closed { Dot { pub x: int } }', 'duplicate type key'),
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
    uid = fnv(b'many::T')
    sym = f'''obosym 12
abi 1
compiler "0.19"
source 0x{0:016x}
interface 0x0
module many
type T uid 0x{uid:016x}
type T = ref record {{ pub v: int }}
fn (mut obj: &T) go(k: int) pf 0 "!" pfree 0x1
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
        # the receiver name is syntax only; the method's summary items are canonicalised
        if 'q0' in text or 'obj' in text or 'fn (mut p0: &T) go(p1: int) pfree 0x1 pf 0 "!"' not in text:
            raise Fail(f"re-emission is not canonical: {text!r}")
        fn_line = next((l for l in text.splitlines() if l.startswith('fn many(')), '')
        if 'pf 62 "!"' not in fn_line:
            raise Fail(f"the 63-parameter function lost its high pf row: {text!r}")
        if 'pfree' in fn_line:
            raise Fail(f"the function's high pfree bits were not ignored: {fn_line!r}")
    finally:
        shutil.rmtree(d, ignore_errors=True)


def b6_stub_order():
    """A summary key for A::g is one ^Sym whether A's interface loads before or after
    the middle module's: both orders reject the use-after-free."""
    files = {
        'a.obx': '''module a
pub type Box = ref record { pub v: int }
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
        'a.obx': 'module a\npub type T = ref record { pub v: int }\npub fn make(v: int): T { return new T{v: v} }\n',
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
        files2 = {'c.obx': 'module c\npub type T = ref record { pub v: int }\npub fn make(): T { return new T{v: 1} }\n',
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
pub type Node = ref record { pub v: int }
pub type Box = ref record { pub items: []Node }
pub type Bag = ref record { pub items: []Node }
pub fn appendOnly(mut a: &[]Node, v: int) { append(a, new Node{v: v}) }
pub fn clear(mut a: &[]Node) { truncate(a, 0) }
pub fn pure(x: int): int { return x + 1 }
pub fn takeWhole(mut b: &Box) { b.items = new []Node(0) }
pub fn hw(mut a: &[]Node, h: ^Bag): int { h.items = new []Node(0); return 1 }
pub var gNode: Node = new Node{v: 9}
pub fn (mut b: &Box) addOnly(n: Node) { append(b.items, n) }
pub fn (mut b: &Box) clear2() { b.items = new []Node(0) }
pub fn (mut b: &Box) wipeG() { gNode = new Node{v: 10} }
pub fn (mut b: &Box) viaH(h: ^Bag) { h.items = new []Node(0) }
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
        ('method append accepted', 'fn main() { let b = new lib::Box{items: new []lib::Node(0)}; append(b.items, new lib::Node{v: 1}); let r = &b.items[0]; b.addOnly(new lib::Node{v: 2}); println(r.v) }', 0),
        ('method clear rejected', 'fn main() { let b = new lib::Box{items: new []lib::Node(0)}; append(b.items, new lib::Node{v: 1}); let r = &b.items[0]; b.clear2(); println(r.v) }', 1),
        ('method frees global rejected', 'fn main() { let b = new lib::Box{items: new []lib::Node(0)}; let r = &lib::gNode; b.wipeG(); println(r.v) }', 1),
        ('method writes handle rejected', 'fn main() { let b = new lib::Box{items: new []lib::Node(0)}; let bag = new lib::Bag{items: new []lib::Node(0)}; let xs = new []lib::Node(0); append(xs, new lib::Node{v: 1}); let r = &xs[0]; b.viaH(handle(bag)); println(r.v) }', 1),
    ]
    try:
        ok(build(d, s), 'build')
        text = sym_text(os.path.join(s, 'lib.sym')).decode()
        for want in ('pfree 0x1 pf 0 "!"', 'pfree 0x1 pf 0 "=/items"', 'freesall writeshandles',
                     'fn (mut p0: &Box) addOnly(p1: Node)',
                     'fn (mut p0: &Box) clear2() pfree 0x1 pf 0 "=/items"',
                     'fn (mut p0: &Box) wipeG() frees 1 lib::gNode',
                     'fn (mut p0: &Box) viaH(p1: ^Bag) freesall writeshandles'):
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
        'lib.obx': 'module lib\npub type T = ref record { pub v: int }\npub fn make(): T { return new T{v: 1} }\npub fn get(t: T): int { return t.v }\n',
        'main.obx': '''module main
import "lib.obx"
type T = ref record { pub q: int }
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
        'int.obx': 'module int\npub type T = ref record { pub v: int }\npub fn make(): T { return new T{v: 3} }\n',
        'Result.obx': 'module Result\npub type T = ref record { pub v: int }\npub fn r(): Result[int] { return ok(1) }\n',
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
        'lib.obx': 'module lib\npub type T = ref record { pub v: int }\n',
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
pub type Holder = ref record { pub c: ?Circle }
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
    x = 'module x\npub type Box = ref record { pub v: int }\npub var g: Box = new Box{v: 1}\n'
    files = {'x.obx': x,
             'main.obx': 'module main\nimport "x.obx"\nfn main() { let r = &x::g; println(r.v) }\n'}
    d, s = tree(files)
    try:
        ok(build(d, s), 'x build')
        m = os.path.join(s, 'm.sym')
        open(m, 'w').write('obosym 12\nabi 1\ncompiler "0.19"\nsource 0x%016x\ninterface 0x0\nmodule m\nfn f() frees 1 x::g\n' % 0)
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


def hash_methods():
    """Method interface data: a private body or a parameter rename changes the
    source hash only; a method rename or receiver-mode change changes the
    interface; a private method is not interface data; a pub method and its order
    are."""
    base = '''module lib
pub type M = ref record { pub v: int }
pub fn (m: &M) get(k: int): int { return m.v + k }
fn (m: &M) hidden(): int { return m.v }
pub fn (m: &M) one(): int { return 1 }
pub fn (m: &M) two(): int { return 2 }
'''
    main = 'module main\nimport "lib.obx"\nfn main() { }\n'

    def iface(text):
        d, s = tree({'lib.obx': text, 'main.obx': main})
        try:
            ok(build(d, s), 'build')
            return sym_header(os.path.join(s, 'lib.sym'))
        finally:
            shutil.rmtree(d, ignore_errors=True)

    b0 = iface(base)
    if 'fn (p0: &M) get(p1: int): int' not in b0['body'] or 'hidden' in b0['body']:
        raise Fail(f"the base method interface is wrong: {b0['body']!r}")
    h = iface(base.replace('fn (m: &M) hidden(): int { return m.v }',
                           'fn (m: &M) hidden(): int { return m.v + 1 }'))
    if h['source'] == b0['source'] or h['interface'] != b0['interface']:
        raise Fail('a private method body change must change source only')
    h = iface(base.replace('pub fn (m: &M) get(k: int): int { return m.v + k }',
                           'pub fn (r: &M) get(q: int): int { return r.v + q }'))
    if h['source'] == b0['source'] or h['interface'] != b0['interface']:
        raise Fail('a receiver/argument rename must change source only')
    if 'fn (p0: &M) get(p1: int): int' not in h['body']:
        raise Fail('the writer did not keep canonical p0/p1 after a rename')
    h = iface(base.replace('pub fn (m: &M) get(k: int): int { return m.v + k }',
                           'pub fn (m: &M) fetch(k: int): int { return m.v + k }'))
    if h['interface'] == b0['interface']:
        raise Fail('renaming a pub method must change the interface hash')
    h = iface(base.replace('pub fn (m: &M) get(k: int): int { return m.v + k }',
                           'pub fn (mut m: &M) get(k: int): int { return m.v + k }'))
    if h['interface'] == b0['interface']:
        raise Fail('a receiver-mode change must change the interface hash')
    h = iface(base + 'fn (m: &M) more(): int { return 3 }\n')
    if h['source'] == b0['source'] or h['interface'] != b0['interface']:
        raise Fail('adding a private method must keep the interface hash')
    h = iface(base + 'pub fn (m: &M) more(): int { return 3 }\n')
    if h['interface'] == b0['interface']:
        raise Fail('adding a pub method must change the interface hash')
    h = iface(base.replace('pub fn (m: &M) one(): int { return 1 }\npub fn (m: &M) two(): int { return 2 }',
                           'pub fn (m: &M) two(): int { return 2 }\npub fn (m: &M) one(): int { return 1 }'))
    if h['interface'] == b0['interface']:
        raise Fail('reordering two pub methods must change the interface hash')


def malformed_methods():
    """Reader validation of a method line: structure, receiver kind and identity,
    position, duplicate keys and the summary indexes (0 is the receiver)."""
    base = '''module lib
pub type Node = ref record { pub v: int }
pub type Bag = ref record { pub items: []Node; pub n: int }
pub type NodeAlias = Node
pub type Shape = closed { Dot { x: int } }
pub fn (n: &Node) get(): int { return n.v }
pub fn (mut b: &Bag) clear() { b.items = new []Node(0) }
fn (n: &Node) hidden(): int { return n.v }
'''
    files = {'lib.obx': base, 'main.obx': 'module main\nimport "lib.obx"\nfn main() { }\n'}
    d, s = tree(files)
    lib = os.path.join(s, 'lib.sym')
    try:
        ok(build(d, s), 'base')
        base_sym = sym_text(lib)
        cases = [
            ('duplicate method key',
             b'fn (p0: &Node) get(): int',
             b'fn (p0: &Node) get(): int\nfn (p0: &Node) get(): int',
             "duplicate exported name 'Node::get'"),
            ('receiver is int', b'fn (p0: &Node) get()', b'fn (p0: int) get()',
             'the receiver must be a record or closed type defined in this module, not int'),
            ('receiver is an array', b'fn (p0: &Node) get()', b'fn (p0: &[]int) get()',
             'the receiver must be a record or closed type defined in this module, not []int'),
            ('receiver is a variant', b'fn (p0: &Node) get()', b'fn (p0: &Dot) get()',
             "the receiver cannot be a variant; declare the method on 'Shape'"),
            ('receiver mut without &', b'fn (mut p0: &Bag) clear()', b'fn (mut p0: Bag) clear()',
             'mut needs a reference parameter'),
            ('receiver named by an alias', b'fn (p0: &Node) get()', b'fn (p0: &NodeAlias) get()',
             'the receiver must be the defining name of a type in this module'),
            ('receiver of an unknown type', b'fn (p0: &Node) get()', b'fn (p0: &Nope) get()',
             "unknown type 'Nope'"),
            ('method pfree bit with no row', b'pfree 0x1 pf 0 "=/items"', b'pfree 0x1',
             'pfree bit 0 has no pf row'),
            ('method pf row with no bit', b'pfree 0x1 pf 0 "=/items"', b'pf 0 "=/items"',
             'pf row for parameter 0 has no pfree bit'),
            ('method unknown summary item', b'fn (p0: &Node) get(): int',
             b'fn (p0: &Node) get(): int frobnicate', "unknown summary item 'frobnicate'"),
        ]
        for name, old, new, detail in cases:
            if base_sym.count(old) != 1:
                raise Fail(f"fixture {name}: {old!r} occurs {base_sym.count(old)} times")
            open(lib, 'wb').write(base_sym)
            mutate(lib, old, new)
            q = build(d, s, check=True)
            if q.returncode != 2 or 'symbol file:' not in q.stderr or detail not in q.stderr:
                raise Fail(f"{name}: exit {q.returncode}: {q.stderr.strip()}")
        line = b'fn (p0: &Node) get(): int\n'
        def move_before(b):
            b = b.replace(line, b'', 1)
            at = b.index(b'type Node uid ')
            return b[:at] + line + b[at:]
        open(lib, 'wb').write(base_sym)
        rehash(lib, move_before)
        q = build(d, s, check=True)
        # before even the shell the type does not resolve: the existing unknown-type error
        if q.returncode != 2 or "unknown type 'Node'" not in q.stderr:
            raise Fail(f"method before the shell: exit {q.returncode}: {q.stderr.strip()}")
        def move_between(b):
            b = b.replace(line, b'', 1)
            at = b.index(b'type Node = ref record')
            return b[:at] + line + b[at:]
        open(lib, 'wb').write(base_sym)
        rehash(lib, move_between)
        q = build(d, s, check=True)
        if q.returncode != 2 or "comes before the definition of type 'Node'" not in q.stderr:
            raise Fail(f"method between shell and definition: exit {q.returncode}: {q.stderr.strip()}")
        files2 = {
            'other.obx': 'module other\npub type Box = ref record { pub v: int }\n',
            'lib.obx': 'module lib\nimport "other.obx"\npub type Node = ref record { pub v: int }\n'
                       'pub fn (n: &Node) get(): int { return n.v }\n',
            'main.obx': 'module main\nimport "lib.obx"\nfn main() { }\n',
        }
        d2, s2 = tree(files2)
        try:
            ok(build(d2, s2), 'foreign base')
            lib2 = os.path.join(s2, 'lib.sym')
            data = sym_text(lib2).replace(b'fn (p0: &Node) get(): int',
                                          b'fn (p0: &other::Box) get(): int', 1)
            open(lib2, 'wb').write(data)
            rehash(lib2, lambda b: b)
            q = build(d2, s2, check=True)
            if q.returncode != 2 or 'the receiver must be the defining name of a type in this module' not in q.stderr:
                raise Fail(f"foreign receiver: exit {q.returncode}: {q.stderr.strip()}")
        finally:
            shutil.rmtree(d2, ignore_errors=True)
    finally:
        shutil.rmtree(d, ignore_errors=True)


def methods_sourceless_check():
    """A method-bearing dependency checks source-less with exit 0; the checked
    module's own .sym carries its methods; a format-1 file is rejected."""
    files = {
        'lib.obx': '''module lib
pub type T = ref record { pub v: int }
pub fn make(v: int): T { return new T{v: v} }
pub fn (t: &T) get(): int { return t.v }
pub fn (mut t: &T) bump() { t.v += 1 }
''',
        'main.obx': '''module main
import "lib.obx"
pub type Local = ref record { pub n: int }
pub fn (l: &Local) lget(): int { return l.n }
fn main() {
    let t = lib::make(2)
    t.bump()
    println(t.get())
}
'''}
    d, s = tree(files)
    try:
        ok(build(d, s), 'base')
        libsym = os.path.join(s, 'lib.sym')
        text = sym_text(libsym).decode()
        for line in ('fn (p0: &T) get(): int', 'fn (mut p0: &T) bump()'):
            if line not in text:
                raise Fail(f"lib.sym lacks {line!r}")
        r = reemit(libsym)
        ok(r, 'reemit lib')
        if r.stdout != sym_text(libsym):
            raise Fail('the method-bearing lib.sym does not round-trip')
        os.rename(os.path.join(d, 'lib.obx'), os.path.join(d, 'lib.obx.bak'))
        q = build(d, s, check=True)
        ok(q, 'source-less check with methods')
        main_text = sym_text(os.path.join(s, 'main.sym')).decode()
        if 'fn (p0: &Local) lget(): int' not in main_text:
            raise Fail(f"the checked module's .sym lacks its method: {main_text!r}")
        base = sym_text(libsym)
        open(libsym, 'wb').write(base.replace(b'obosym 12', b'obosym 1', 1))
        q = build(d, s, check=True)
        if q.returncode != 2 or 'has format 1; this compiler reads 12' not in q.stderr:
            raise Fail(f"format 1: exit {q.returncode}: {q.stderr.strip()}")
        open(libsym, 'wb').write(base)
        os.rename(os.path.join(d, 'lib.obx.bak'), os.path.join(d, 'lib.obx'))
    finally:
        shutil.rmtree(d, ignore_errors=True)


IFACE_LIB = '''module lib
pub type Shape = interface {
    pure fn area(): int
    mut fn grow(k: int)
}
pub type Circle = ref record { pub r: int }
pub fn (c: &Circle) area(): int { return 3 * c.r * c.r }
pub fn (mut c: &Circle) grow(k: int) { c.r += k }
pub impl Shape for Circle
type Hidden = interface { pure fn area(): int }
impl Hidden for Circle
'''

IFACE_MAIN = '''module main
import "lib.obx"
fn main() {
    let c = new lib::Circle{r: 2}
    c.grow(1)
    println(c.area())
}
'''


def iface_tree():
    """A fresh tree with the interface lib and main, built with its sources into a sym dir."""
    d = tempfile.mkdtemp(prefix='symcase-iface-')
    write_tree(d, {'lib.obx': IFACE_LIB, 'main.obx': IFACE_MAIN})
    symdir = os.path.join(d, 'sym')
    os.makedirs(symdir)
    ok(build(d, symdir, out=os.path.join(d, 'prog')), 'interface tree build')
    return d, symdir


def iface_private_impl():
    """S-6: a private interface and its private impl are not written to lib.sym; a
    source-less --check of main writes a main.sym byte-identical to the full build's."""
    d, symdir = iface_tree()
    libsym = sym_text(os.path.join(symdir, 'lib.sym'))
    if b'Hidden' in libsym:
        raise Fail(f"a private interface reached lib.sym: {libsym!r}")
    if b'impl Shape for Circle' not in libsym:
        raise Fail(f"the exported impl is missing from lib.sym: {libsym!r}")
    full_main = sym_text(os.path.join(symdir, 'main.sym'))
    os.rename(os.path.join(d, 'lib.obx'), os.path.join(d, 'lib.obx.bak'))
    chk = os.path.join(d, 'sym-check')
    shutil.copytree(symdir, chk)
    ok(build(d, chk, check=True), 'source-less check')
    if sym_text(os.path.join(chk, 'main.sym')) != full_main:
        raise Fail('the source-less --check main.sym differs from the full build')
    shutil.rmtree(d, ignore_errors=True)


def iface_summary_matrix():
    """S-2: the committed all-forms interface tree (every member form of the v3 3.2
    table) checks source-less and writes a main.sym byte-identical to a full build
    of the same root, and its lib.sym re-emits unchanged."""
    src = os.path.join(HERE, 'modules', 'sym', 'iface_sig')
    d = tempfile.mkdtemp(prefix='symcase-matrix-')
    for name in ('lib.obx', 'dep.obx', 'main.obx'):
        shutil.copyfile(os.path.join(src, name), os.path.join(d, name))
    symdir = os.path.join(d, 'sym')
    os.makedirs(symdir)
    ok(build(d, symdir, out=os.path.join(d, 'prog')), 'full build of the all-forms tree')
    full_main = sym_text(os.path.join(symdir, 'main.sym'))
    lib_text = sym_text(os.path.join(symdir, 'lib.sym'))
    r = reemit(os.path.join(symdir, 'lib.sym'))
    if r.returncode != 0 or r.stdout != lib_text:
        raise Fail('the all-forms lib.sym does not re-emit unchanged')
    for name in ('lib.obx', 'dep.obx'):
        os.unlink(os.path.join(d, name))
    chk = os.path.join(d, 'sym-check')
    shutil.copytree(symdir, chk)
    ok(build(d, chk, check=True), 'source-less check of the all-forms tree')
    if sym_text(os.path.join(chk, 'main.sym')) != full_main:
        raise Fail('the source-less --check main.sym differs from the full build')
    shutil.rmtree(d, ignore_errors=True)


# (old, new, expected stderr text) for lib.sym, each refused at its reader line
IFACE_REFUSALS = [
    ('pure fn area(): int', 'mut pure fn area(): int', "'pure' and 'mut' come before 'fn'"),
    ('mut fn grow(p0: int)', 'pure fn area(): int', "duplicate interface method 'area'"),
    ('mut fn grow(p0: int)', 'mut fn grow(mut p0: int)', 'mut needs a reference parameter'),
    ('mut fn grow(p0: int)', 'mut fn grow(p0: &int)', 'only records, closed types and arrays can be borrowed, not int'),
    ('impl Shape for Circle\n', 'impl Shape for Circle\nimpl Shape for Circle\n', 'duplicate implementation'),
    # a ';' is not a parameter separator (the function-heading reader refuses it the same way)
    ('mut fn grow(p0: int)', 'mut fn grow(p0: int; p1: int)', "expected ',' or ')'"),
    ('mut fn grow(p0: int)', 'mut fn grow(p0: int;)', "expected ',' or ')'"),
]


# Step 3 (v3 5.1, 6): an owned interface crosses the module boundary in every position the reader accepts
IFACE_OWNED_LIB = '''module lib
pub type Shape = interface {
    pure fn area(): int
}
pub type Square = ref record { pub s: int }
pub fn (q: &Square) area(): int { return q.s * q.s }
pub impl Shape for Square
pub type Holder = ref record { pub d: Shape; pub maybe: ?Shape; pub items: []Shape; pub r: Result[Shape] }
pub var G: Shape = new Square{s: 1}
pub fn make(n: int): Shape {
    return new Square{s: n}
}
pub fn wrap(s: Shape): Result[Shape] {
    return ok(s)
}
'''
IFACE_OWNED_MAIN = '''module main
import "lib.obx"
fn main() {
    let t: lib::Shape = lib::make(3)
    let h = new lib::Holder{d: t, maybe: nil, items: new []lib::Shape(0), r: lib::wrap(new lib::Square{s: 2})}
    println(len(h.items))
}
'''
IFACE_HIDDEN_LIB = '''module lib
pub type Shape = interface {
    pure fn area(): int
}
pub type Hidden = ref record { pub s: int }
fn (h: &Hidden) area(): int { return h.s }
impl Shape for Hidden
'''
IFACE_HIDDEN_MAIN = '''module main
import "lib.obx"
fn main() {
    let h: lib::Shape = new lib::Hidden{s: 1}
}
'''


def iface_owned_check():
    """Step 3: a pub lib function returns lib::Shape and holds the interface in a field, a global and a
    Result; a source-less --check of main writes a main.sym byte-identical to the full build's. A private
    implementation is refused by importers with the same text in a full build and in --check."""
    d = tempfile.mkdtemp(prefix='symcase-owned-')
    try:
        write_tree(d, {'lib.obx': IFACE_OWNED_LIB, 'main.obx': IFACE_OWNED_MAIN})
        symdir = os.path.join(d, 'sym')
        os.makedirs(symdir)
        ok(build(d, symdir, out=os.path.join(d, 'prog')), 'owned full build')
        full_main = sym_text(os.path.join(symdir, 'main.sym'))
        os.rename(os.path.join(d, 'lib.obx'), os.path.join(d, 'lib.obx.bak'))
        chk = os.path.join(d, 'sym-check')
        shutil.copytree(symdir, chk)
        ok(build(d, chk, check=True), 'owned source-less check')
        if sym_text(os.path.join(chk, 'main.sym')) != full_main:
            raise Fail('the source-less --check main.sym differs from the full build')
    finally:
        shutil.rmtree(d, ignore_errors=True)

    # the same refusal text from a full build and from --check (the importer reads lib.sym only in --check)
    d = tempfile.mkdtemp(prefix='symcase-hidden-')
    try:
        write_tree(d, {'lib.obx': IFACE_HIDDEN_LIB, 'main.obx': IFACE_HIDDEN_MAIN})
        symdir = os.path.join(d, 'sym')
        os.makedirs(symdir)
        full = build(d, symdir, out=os.path.join(d, 'prog'))
        want = "module 'lib' has no exported implementation of 'lib::Shape' for 'Hidden'"
        if full.returncode != 1 or want not in full.stderr:
            raise Fail(f"full build: exit {full.returncode}: {full.stderr.strip()}")
        os.rename(os.path.join(d, 'lib.obx'), os.path.join(d, 'lib.obx.bak'))
        chk = os.path.join(d, 'sym-check')
        shutil.copytree(symdir, chk)
        q = build(d, chk, check=True)
        if q.returncode != 1 or want not in q.stderr:
            raise Fail(f"--check: exit {q.returncode}: {q.stderr.strip()}")
        if full.stderr.replace(d + os.sep, '') != q.stderr.replace(d + os.sep, ''):
            raise Fail(f"full and --check texts differ: {full.stderr!r} vs {q.stderr!r}")
    finally:
        shutil.rmtree(d, ignore_errors=True)


def iface_ref_check():
    """Step 5: a &Shape argument, a concrete borrow and an inferred &I local cross the module boundary and
    round-trip through lib.sym; a source-less --check writes the same main.sym as the full build. An inferred
    &I local is now accepted, while an explicit or inferred &?I local is refused with the same text in a full
    build and in --check."""
    lib = '''module lib
pub type Shape = interface {
    pure fn area(): int
}
pub type Square = ref record { pub s: int }
pub fn (q: &Square) area(): int { return q.s }
pub impl Shape for Square
pub fn total(xs: &[]Shape): int {
    var sum = 0
    var i = 0
    while i < len(xs) {
        sum += xs[i].area()
        i += 1
    }
    return sum
}
pub fn peek(r: &Result[Shape]): int {
    match r {
        ok { return r.value.area() }
        err { return -1 }
    }
}
pub fn describe(d: &Shape): int { return d.area() }
pub fn maybe(o: &?Shape): int {
    if o != nil { return o.area() }
    return -1
}
'''
    main = '''module main
import "lib.obx"
fn main() {
    let xs = new []lib::Shape(0)
    append(xs, new lib::Square{s: 2})
    println(lib::total(&xs))
    let s: lib::Shape = new lib::Square{s: 3}
    println(lib::describe(&s))
    let res: Result[lib::Shape] = ok(s)
    println(lib::peek(&res))
    let c = new lib::Square{s: 6}
    let r: &lib::Shape = &c
    println(lib::describe(r))
    let o: ?lib::Shape = new lib::Square{s: 5}
    println(lib::maybe(&o))
    let n: ?lib::Shape = nil
    println(lib::maybe(&n))
}
'''
    d = tempfile.mkdtemp(prefix='symcase-ref-')
    try:
        write_tree(d, {'lib.obx': lib, 'main.obx': main})
        symdir = os.path.join(d, 'sym')
        os.makedirs(symdir)
        exe = os.path.join(d, 'prog')
        ok(build(d, symdir, out=exe), 'borrowed-interface full build')
        full_main = sym_text(os.path.join(symdir, 'main.sym'))
        q = subprocess.run([exe], capture_output=True, text=True, timeout=60)
        if q.returncode != 0 or q.stdout != '2\n3\n3\n6\n5\n-1\n':
            raise Fail(f"borrowed-interface run: exit {q.returncode}, stdout {q.stdout!r}")
        os.rename(os.path.join(d, 'lib.obx'), os.path.join(d, 'lib.obx.bak'))
        chk = os.path.join(d, 'sym-check')
        shutil.copytree(symdir, chk)
        ok(build(d, chk, check=True), 'borrowed-interface source-less check')
        if sym_text(os.path.join(chk, 'main.sym')) != full_main:
            raise Fail('the source-less --check main.sym differs from the full build')
    finally:
        shutil.rmtree(d, ignore_errors=True)

    prelude = '''module main
type Shape = interface {
    pure fn area(): int
}
type Square = ref record { pub s: int }
fn (q: &Square) area(): int { return q.s }
impl Shape for Square
fn make(): Shape { return new Square{s: 1} }
'''
    direct = '''fn main() {
    let s: Shape = make()
    var r = &s
    println(r.area())
}
'''
    alias = '''type S = Shape
fn main() {
    let s: S = make()
    var r = &s
    println(r.area())
}
'''
    for name, body in (('direct', direct), ('alias', alias)):
        d = tempfile.mkdtemp(prefix='symcase-infer-' + name + '-')
        try:
            write_tree(d, {'main.obx': prelude + body})
            symdir = os.path.join(d, 'sym')
            os.makedirs(symdir)
            exe = os.path.join(d, 'prog')
            ok(build(d, symdir, out=exe), f'inferred {name} full build')
            q = subprocess.run([exe], capture_output=True, text=True, timeout=60)
            if q.returncode != 0 or q.stdout != '1\n':
                raise Fail(f"inferred {name} run: exit {q.returncode}, stdout {q.stdout!r}")
            ok(build(d, symdir, check=True), f'inferred {name} --check')
        finally:
            shutil.rmtree(d, ignore_errors=True)

    optional = '''type Holder = ref record { pub o: ?Shape }
fn main() {
    let h = new Holder{o: make()}
    var r = &h.o
    if r != nil { println(r.area()) }
}
'''
    optional_alias = '''type O = ?Shape
type Holder = ref record { pub o: O }
fn main() {
    let h = new Holder{o: make()}
    let r: &O = &h.o
    if r != nil { println(r.area()) }
}
'''
    want = "an optional interface reference can only be a parameter or a walk variable; narrow it to a '&I' local"
    for name, body in (('optional', optional), ('optional-alias', optional_alias)):
        d = tempfile.mkdtemp(prefix='symcase-optref-' + name + '-')
        try:
            write_tree(d, {'main.obx': prelude + body})
            symdir = os.path.join(d, 'sym')
            os.makedirs(symdir)
            full = build(d, symdir, out=os.path.join(d, 'prog'))
            chk = build(d, symdir, check=True)
            for what, q in (('full build', full), ('--check', chk)):
                if q.returncode != 1 or want not in q.stderr:
                    raise Fail(f"optional reference {name} {what}: exit {q.returncode}: {q.stderr.strip()}")
            if full.stderr != chk.stderr:
                raise Fail(f"optional reference {name} full and --check texts differ: {full.stderr!r} vs {chk.stderr!r}")
        finally:
            shutil.rmtree(d, ignore_errors=True)


def iface_reader_refusals():
    """S-5: each malformed interface or impl line in lib.sym (its hash recomputed, so
    the structural checks run) is refused with the reader's own message."""
    d, symdir = iface_tree()
    libpath = os.path.join(symdir, 'lib.sym')
    base = sym_text(libpath)
    os.unlink(os.path.join(d, 'lib.obx'))
    try:
        for old, new, want in IFACE_REFUSALS:
            if base.count(old.encode()) != 1:
                raise Fail(f"{old!r} occurs {base.count(old.encode())} times in lib.sym")
            with open(libpath, 'wb') as f:
                f.write(base)
            mutate(libpath, old.encode(), new.encode())
            q = build(d, symdir, check=True)
            if q.returncode != 2 or want not in q.stderr:
                raise Fail(f"{old!r} -> {new!r}: exit {q.returncode}, wanted '{want}': {q.stderr.strip()}")
    finally:
        shutil.rmtree(d, ignore_errors=True)


IFACE_IMPURE_LIB = '''module lib
var log: []int = new []int(0)
fn zap() { truncate(log, 0) }

pub type Handler = interface {
    impure fn push(k: int): int
    impure mut fn reset(k: int): int
}
pub type Counter = ref record { pub n: int }
pub fn (c: &Counter) push(k: int): int { zap(); return c.n + k }
pub fn (mut c: &Counter) reset(k: int): int { zap(); c.n = k; return c.n }
pub impl Handler for Counter
'''

IFACE_IMPURE_MAIN = '''module main
import "lib.obx"
fn main() {
    let h: lib::Handler = new lib::Counter{n: 3}
    println(h.push(1))
}
'''


def iface_impure_roundtrip():
    """The impure member marker round-trips through the writer and reader, rebuilds the
    maximum summary source-lessly, and duplicate/conflicting markers are refused."""
    d, symdir = tree({'lib.obx': IFACE_IMPURE_LIB, 'main.obx': IFACE_IMPURE_MAIN})
    try:
        ok(build(d, symdir, out=os.path.join(d, 'prog')), 'impure interface build')
        libsym = os.path.join(symdir, 'lib.sym')
        text = sym_text(libsym)
        for line in (b'impure fn push(p0: int): int', b'impure mut fn reset(p0: int): int'):
            if line not in text:
                raise Fail(f"lib.sym lacks {line!r}: {text!r}")
        if b'freesall' in text or b'writeshandles' in text:
            raise Fail(f"a member summary reached lib.sym: {text!r}")
        r = reemit(libsym)
        if r.returncode != 0 or r.stdout != text:
            raise Fail('the impure lib.sym does not re-emit unchanged')
        full_main = sym_text(os.path.join(symdir, 'main.sym'))
        os.rename(os.path.join(d, 'lib.obx'), os.path.join(d, 'lib.obx.bak'))
        chk = os.path.join(d, 'sym-check')
        shutil.copytree(symdir, chk)
        ok(build(d, chk, check=True), 'source-less check of an impure interface')
        if sym_text(os.path.join(chk, 'main.sym')) != full_main:
            raise Fail('the source-less --check main.sym differs from the full build')
        chk_lib = os.path.join(chk, 'lib.sym')
        # a format-9 file that carries the marker is refused by the format check, before the body
        base = sym_text(chk_lib)
        with open(chk_lib, 'wb') as f:
            f.write(base.replace(b'obosym 12', b'obosym 9', 1))
        q = build(d, chk, check=True)
        if q.returncode != 2 or 'has format 9; this compiler reads 12; rebuild it' not in q.stderr:
            raise Fail(f"format-9 impure interface: exit {q.returncode}: {q.stderr.strip()}")
        with open(chk_lib, 'wb') as f:
            f.write(base)
        for old, new, want in (
            (b'impure fn push', b'impure impure fn push', "'impure' is repeated on an interface member"),
            (b'impure fn push', b'pure impure fn push', "'pure' and 'impure' are mutually exclusive on an interface member"),
            (b'impure mut fn reset', b'mut impure fn reset', "'impure' comes before 'fn'"),
        ):
            with open(chk_lib, 'wb') as f:
                f.write(base)
            mutate(chk_lib, old, new)
            q = build(d, chk, check=True)
            if q.returncode != 2 or want not in q.stderr:
                raise Fail(f"{old!r} -> {new!r}: exit {q.returncode}, wanted {want!r}: {q.stderr.strip()}")
    finally:
        shutil.rmtree(d, ignore_errors=True)


def stale_format3_interface():
    """S-4: a format-3 symbol file that uses an interface type as a parameter is refused
    by the format check before its body is read."""
    d = tempfile.mkdtemp(prefix='symcase-stale-')
    write_tree(d, {'main.obx': 'module main\nimport "lib.obx"\nfn main() { }\n'})
    symdir = os.path.join(d, 'sym')
    os.makedirs(symdir)
    with open(os.path.join(symdir, 'lib.sym'), 'w') as f:
        f.write('obosym 3\nabi 1\ncompiler "0.7"\nsource 0x0000000000000001\ninterface 0x0\nmodule lib\n'
                'type I = interface { fn m(p0: I): int }\nfn f(p0: I): int\n')
    q = build(d, symdir, check=True)
    if q.returncode != 2 or 'has format 3; this compiler reads 12; rebuild it' not in q.stderr:
        raise Fail(f"stale format-3 interface file: exit {q.returncode}: {q.stderr.strip()}")
    shutil.rmtree(d, ignore_errors=True)


FN_LIB = '''module lib
pub type Item = ref record { pub n: int }
pub type I = interface { fn m(p0: int) }
pub type ArrI = []I
pub type OptI = ?I
pub type ResI = Result[I]
pub type Holder = ref record { pub s: I }
pub type Cmp = fn(&Item, &Item): int
pub type T = ref record { pub v: int }
pub type Closed = closed { V { v: int } }
pub type NodeI = ref record { pub next: ?NodeI; pub s: I }
pub var G: int = 0
pub const C = 7
pub fn plain(p0: int): int { return p0 }
pub fn apply(p0: fn(int): int, p1: int): int { return p1 }
pub fn useCmp(p0: Cmp): int { return 0 }
pub fn (i: &Item) use(p0: fn(int): int): int { return 0 }
'''


def fn_sym_roundtrip():
    """Stage A format 6: a public function-type alias, function parameter, method parameter and
    source-less check/re-emit round-trip. No function value is created or called yet."""
    d, symdir = tree({'lib.obx': FN_LIB,
                      'main.obx': 'module main\nimport "lib.obx"\nfn main() { }\n'},
                     prefix='symcase-fn-roundtrip-')
    try:
        ok(build(d, symdir, out=os.path.join(d, 'prog')), 'function-type module build')
        libpath = os.path.join(symdir, 'lib.sym')
        lib = sym_text(libpath).decode()
        for line in ('obosym 12\n', 'abi 1\n', 'compiler "0.19"\n',
                     'type Cmp = fn(&Item, &Item): int\n',
                     'fn apply(p0: fn(int): int, p1: int): int\n',
                     'fn useCmp(p0: fn(&Item, &Item): int): int\n',
                     'fn (p0: &Item) use(p1: fn(int): int): int\n'):
            if line not in lib:
                raise Fail(f"canonical function-type line missing from lib.sym: {line!r}")
        base = sym_text(libpath)
        r = reemit(libpath)
        ok(r, 'reemit lib.sym')
        if r.stdout != base:
            raise Fail('lib.sym is not byte-identical after a reader/writer round trip')
        again = os.path.join(d, 'again')
        os.makedirs(again)
        second = os.path.join(again, 'lib.sym')
        open(second, 'wb').write(r.stdout)
        r2 = reemit(second)
        ok(r2, 'second reemit')
        if r2.stdout != base:
            raise Fail('the second function-type round trip is not byte-identical')
        os.unlink(os.path.join(d, 'lib.obx'))
        ok(build(d, symdir, check=True), 'source-less check with function types')
    finally:
        shutil.rmtree(d, ignore_errors=True)


def stale_format4_fn_rejected():
    """A format-4 file (even one whose body now uses function types) is refused before its
    body is read, with the new format number."""
    d, symdir = tree({'lib.obx': FN_LIB,
                      'main.obx': 'module main\nimport "lib.obx"\nfn main() { }\n'},
                     prefix='symcase-stale-fn-')
    try:
        ok(build(d, symdir, out=os.path.join(d, 'prog')), 'honest format-5 build')
        libpath = os.path.join(symdir, 'lib.sym')
        os.unlink(os.path.join(d, 'lib.obx'))
        mutate(libpath, b'obosym 12', b'obosym 4', recompute=False)
        q = build(d, symdir, check=True)
        if q.returncode != 2 or 'has format 4; this compiler reads 12; rebuild it' not in q.stderr:
            raise Fail(f"stale format-4 function type: exit {q.returncode}: {q.stderr.strip()}")
    finally:
        shutil.rmtree(d, ignore_errors=True)


def forged_fn_positions():
    """A well-formed, rehashed format-5 file cannot put a function type in a storage,
    return, constructor, interface or nested-callback position. Each case is rehashed, so
    the refusal is structural, not a stale-hash accident."""
    d, symdir = tree({'lib.obx': FN_LIB,
                      'main.obx': 'module main\nimport "lib.obx"\nfn main() { }\n'},
                     prefix='symcase-fn-positions-')
    try:
        ok(build(d, symdir, out=os.path.join(d, 'prog')), 'function-type module build')
        libpath = os.path.join(symdir, 'lib.sym')
        base = sym_text(libpath)
        os.unlink(os.path.join(d, 'lib.obx'))
        cases = [
            (b'type T = ref record { pub v: int }', b'type T = ref record { pub v: fn(int): int }',
             'a record field cannot be a function value'),
            (b'type Closed = closed { V { pub v: int } }', b'type Closed = closed { V { pub v: fn(int): int } }',
             'a record field cannot be a function value'),
            (b'var G: int', b'var G: fn(int): int',
             'a function value cannot be a global; it is a parameter or a local'),
            (b'var G: int', b'var G: Cmp',
             'a function value cannot be a global; it is a parameter or a local'),
            (b'const C: int =', b'const C: fn(int): int =',
             'a constant cannot hold a function value'),
            (b'const C: int =', b'const C: Cmp =',
             'a constant cannot hold a function value'),
            (b'fn plain(p0: int): int', b'fn plain(p0: int): fn(int): int',
             'a function cannot return a function value; function values are parameters and locals only'),
            (b'fn plain(p0: int): int', b'fn plain(p0: int): Cmp',
             'a function cannot return a function value; function values are parameters and locals only'),
            (b'fn apply(p0: fn(int): int, p1: int): int',
             b'fn apply(p0: fn(int): fn(int): int, p1: int): int',
             'a function type cannot return a function value'),
            (b'fn plain(p0: int): int', b'fn plain(p0: ?fn(int): int): int',
             'an optional cannot hold a function value'),
            (b'fn plain(p0: int): int', b'fn plain(p0: ?Cmp): int',
             'an optional cannot hold a function value'),
            (b'fn plain(p0: int): int', b'fn plain(p0: []fn(int): int): int',
             'an array cannot hold function values'),
            (b'fn plain(p0: int): int', b'fn plain(p0: []Cmp): int',
             'an array cannot hold function values'),
            (b'fn plain(p0: int): int', b'fn plain(p0: Result[fn(int): int]): int',
             'Result cannot hold a function value'),
            (b'fn plain(p0: int): int', b'fn plain(p0: Result[Cmp]): int',
             'Result cannot hold a function value'),
            (b'fn plain(p0: int): int', b'fn plain(p0: ^fn(int): int): int',
             'handles name records and closed types, not fn(int): int'),
            (b'fn plain(p0: int): int', b'fn plain(p0: ^Cmp): int',
             'handles name records and closed types, not fn(&Item, &Item): int'),
            (b'fn plain(p0: int): int', b'fn plain(p0: &fn(int): int): int',
             'only records, closed types and arrays can be borrowed, not fn(int): int'),
            (b'fn plain(p0: int): int', b'fn plain(p0: &Cmp): int',
             'only records, closed types and arrays can be borrowed, not fn(&Item, &Item): int'),
            (b'fn apply(p0: fn(int): int, p1: int): int',
             b'fn apply(p0: fn[](int): int, p1: int): int',
             'a function type cannot have a capture list'),
            (b'fn apply(p0: fn(int): int, p1: int): int',
             b'fn apply(p0: fn int, p1: int): int',
             "expected '(' after 'fn'"),
            (b'fn apply(p0: fn(int): int, p1: int): int',
             b'fn apply(p0: fn(, p1: int): int',
             'expected a parameter type'),
            (b'fn apply(p0: fn(int): int, p1: int): int',
             b'fn apply(p0: fn(int,): int, p1: int): int',
             'expected a parameter type'),
            (b'fn apply(p0: fn(int): int, p1: int): int',
             b'fn apply(p0: fn(int,int,): int, p1: int): int',
             'expected a parameter type'),
            (b'fn apply(p0: fn(int): int, p1: int): int',
             b'fn apply(p0: fn(&Item,): int, p1: int): int',
             'expected a parameter type'),
            (b'fn apply(p0: fn(int): int, p1: int): int',
             b'fn apply(p0: fn(int int): int, p1: int): int',
             "expected ',' or ')'"),
            (b'fn apply(p0: fn(int): int, p1: int): int',
             b'fn apply(p0: fn(int: int, p1: int): int',
             "expected ',' or ')'"),
            (b'fn apply(p0: fn(int): int, p1: int): int',
             b'fn apply(p0: fn(I): int, p1: int): int',
             'a function value cannot take or return an interface value'),
            (b'fn apply(p0: fn(int): int, p1: int): int',
             b'fn apply(p0: fn(?I): int, p1: int): int',
             'a function value cannot take or return an interface value'),
            (b'fn apply(p0: fn(int): int, p1: int): int',
             b'fn apply(p0: fn([]I): int, p1: int): int',
             'a function value cannot take or return an interface value'),
            (b'fn apply(p0: fn(int): int, p1: int): int',
             b'fn apply(p0: fn(Result[I]): int, p1: int): int',
             'a function value cannot take or return an interface value'),
            (b'fn apply(p0: fn(int): int, p1: int): int',
             b'fn apply(p0: fn(&[]I): int, p1: int): int',
             'a function value cannot take or return an interface value'),
            (b'fn apply(p0: fn(int): int, p1: int): int',
             b'fn apply(p0: fn(ArrI): int, p1: int): int',
             'a function value cannot take or return an interface value'),
            (b'fn apply(p0: fn(int): int, p1: int): int',
             b'fn apply(p0: fn(OptI): int, p1: int): int',
             'a function value cannot take or return an interface value'),
            (b'fn apply(p0: fn(int): int, p1: int): int',
             b'fn apply(p0: fn(ResI): int, p1: int): int',
             'a function value cannot take or return an interface value'),
            (b'fn apply(p0: fn(int): int, p1: int): int',
             b'fn apply(p0: fn(&ArrI): int, p1: int): int',
             'a function value cannot take or return an interface value'),
            (b'fn apply(p0: fn(int): int, p1: int): int',
             b'fn apply(p0: fn(Holder): int, p1: int): int',
             "a function value cannot take or return 'Holder': it holds an interface value"),
            (b'fn apply(p0: fn(int): int, p1: int): int',
             b'fn apply(p0: fn([]Holder): int, p1: int): int',
             "a function value cannot take or return 'Holder': it holds an interface value"),
            (b'fn apply(p0: fn(int): int, p1: int): int',
             b'fn apply(p0: fn(NodeI): int, p1: int): int',
             "a function value cannot take or return 'NodeI': it holds an interface value"),
            (b'fn apply(p0: fn(int): int, p1: int): int',
             b'fn apply(p0: fn(fn(Holder): int): int, p1: int): int',
             "a function value cannot take or return 'Holder': it holds an interface value"),
            (b'fn apply(p0: fn(int): int, p1: int): int',
             b'fn apply(p0: fn(fn(I): int): int, p1: int): int',
             'a function value cannot take or return an interface value'),
            (b'type I = interface { fn m(p0: int) }',
             b'type I = interface { fn m(p0: fn(int): int) }',
             'an interface method cannot use a function value; function types are not supported in interfaces'),
            (b'type I = interface { fn m(p0: int) }',
             b'type I = interface { fn m(p0: int): fn(int): int }',
             'an interface method cannot use a function value; function types are not supported in interfaces'),
            (b'type I = interface { fn m(p0: int) }',
             b'type I = interface { fn m(p0: &fn(int): int) }',
             'only records, closed types and arrays can be borrowed, not fn(int): int'),
            (b'fn plain(p0: int): int', b'fn (p0: fn(int): int) plain()',
             'the receiver must be a record or closed type defined in this module, not fn(int): int'),
            (b'fn plain(p0: int): int', b'fn (p0: &fn(int): int) plain()',
             'only records, closed types and arrays can be borrowed, not fn(int): int'),
            (b'type T = ref record { pub v: int }', b'type T = fn(int): int',
             "a function type cannot be forward-declared: 'T' is already declared as a forward type"),
        ]
        for old, new, want in cases:
            open(libpath, 'wb').write(base)
            mutate(libpath, old, new)
            q = build(d, symdir, check=True)
            if q.returncode != 2 or want not in q.stderr:
                raise Fail(f"{old!r} -> {new!r}: exit {q.returncode}, wanted {want!r}: {q.stderr.strip()}")
        # The canonical writer puts aliases after definitions, so a record/interface definition
        # cannot normally name a function-type alias. A forged file can reorder them; the reader
        # must still refuse the alias spelling with the position message.
        alias_line = b'type Cmp = fn(&Item, &Item): int\n'
        bare = base.replace(alias_line, b'')
        for marker, old, new, want in [
            (b'type T = ref record { pub v: int }\n',
             b'type T = ref record { pub v: int }', b'type T = ref record { pub v: Cmp }',
             'a record field cannot be a function value'),
            (b'type I = interface { fn m(p0: int) }\n',
             b'type I = interface { fn m(p0: int) }', b'type I = interface { fn m(p0: Cmp) }',
             'an interface method cannot use a function value; function types are not supported in interfaces'),
        ]:
            at = bare.index(marker)
            open(libpath, 'wb').write(bare[:at] + alias_line + bare[at:])
            mutate(libpath, old, new)
            q = build(d, symdir, check=True)
            if q.returncode != 2 or want not in q.stderr:
                raise Fail(f"reordered alias {old!r} -> {new!r}: exit {q.returncode}: {q.stderr.strip()}")
        # a forward shell redefined through a bare function-type alias
        marker = b'type T = ref record { pub v: int }\n'
        bare2 = base.replace(alias_line, b'')
        at = bare2.index(marker)
        open(libpath, 'wb').write(bare2[:at] + alias_line + b'type T = Cmp\n' +
                                  bare2[at + len(marker):])
        rehash(libpath, lambda b: b)   # the spliced file changes the hashed body
        q = build(d, symdir, check=True)
        want = "a function type cannot be forward-declared: 'T' is already declared as a forward type"
        if q.returncode != 2 or want not in q.stderr:
            raise Fail(f"forward shell through a bare alias: exit {q.returncode}: {q.stderr.strip()}")
        # an incomplete forward shell as a function-type parameter
        manual = ('obosym 12\nabi 1\ncompiler "0.19"\nsource 0x0000000000000001\n'
                  'interface 0x0\nmodule lib\ntype Later uid 0x%016x\n'
                  'fn apply(p0: fn(Later): int): int\n' % fnv(b'lib::Later'))
        open(libpath, 'w').write(manual)
        rehash(libpath, lambda b: b)
        q = build(d, symdir, check=True)
        want = "a function value cannot take or return 'Later': it is not yet defined; define it before this function type"
        if q.returncode != 2 or want not in q.stderr:
            raise Fail(f"incomplete shell in a function type: exit {q.returncode}: {q.stderr.strip()}")
    finally:
        shutil.rmtree(d, ignore_errors=True)



def qualified_fnfwd_alias():
    """A forward shell redefined through a qualified function-type alias is refused with the
    forward-function diagnostic, even though the alias lives in another module's .sym."""
    d, symdir = tree({'dep.obx': 'module dep\npub type Cmp = fn(int): int\n',
                      'lib.obx': 'module lib\nimport "dep.obx"\npub type T = ref record { pub v: int }\n',
                      'main.obx': 'module main\nimport "lib.obx"\nfn main() { }\n'},
                     prefix='symcase-qfn-fwd-')
    try:
        ok(build(d, symdir, out=os.path.join(d, 'prog')), 'qualified forward build')
        libpath = os.path.join(symdir, 'lib.sym')
        mutate(libpath, b'type T = ref record { pub v: int }\n', b'type T = dep::Cmp\n')
        os.unlink(os.path.join(d, 'lib.obx'))
        os.unlink(os.path.join(d, 'dep.obx'))
        q = build(d, symdir, check=True)
        want = "a function type cannot be forward-declared: 'T' is already declared as a forward type"
        if q.returncode != 2 or want not in q.stderr:
            raise Fail(f"qualified function alias forward: exit {q.returncode}: {q.stderr.strip()}")
    finally:
        shutil.rmtree(d, ignore_errors=True)


# the imported-member tier cases of module_test (tests/modules/{run,errors}/iface_tier_*): each is also run
# with its library read from the .sym alone (--check), which rebuilds the members' summaries in the reader
IFACE_TIER_CASES = ['run/iface_tier_bits', 'errors/iface_tier_global', 'errors/iface_tier_handle',
                    'errors/iface_tier_pure_param', 'errors/iface_tier_pure_recv',
                    'errors/iface_impure', 'run/iface_calls', 'run/iface_call_tiers']


def iface_tier_check():
    """S-2 (tier checks): a downstream impl of an imported interface gets the same verdict from a full
    build (the live summary) and from --check against lib.sym alone (the reader's summary), with the
    same message, and that verdict is the module case's own expect file."""
    base = os.path.join(HERE, 'modules')
    for case in IFACE_TIER_CASES:
        src = os.path.join(base, case)
        want_exit, want_err = 0, ''
        expect = os.path.join(src, 'expect')
        if os.path.exists(expect):
            for line in open(expect).read().splitlines():
                if line.startswith('exit '):
                    want_exit = int(line[5:])
                elif line.startswith('stderr '):
                    want_err = line[7:]
        d = tempfile.mkdtemp(prefix='symcase-tier-')
        try:
            for name in os.listdir(src):
                if name.endswith('.obx'):
                    shutil.copyfile(os.path.join(src, name), os.path.join(d, name))
            symdir = os.path.join(d, 'sym')
            os.makedirs(symdir)
            full = build(d, symdir, out=os.path.join(d, 'prog'))
            # the library's .sym is written before the root is checked, so --check still has it when the root refuses
            for name in os.listdir(d):
                if name.endswith('.obx') and name != 'main.obx':
                    os.unlink(os.path.join(d, name))
            chk = build(d, symdir, check=True)
            problems = []
            if full.returncode != want_exit or full.stderr.strip().replace(d + os.sep, '') != want_err:
                problems.append(f"full build exit {full.returncode}: {full.stderr.strip()!r}")
            if chk.returncode != want_exit or chk.stderr.strip().replace(d + os.sep, '') != want_err:
                problems.append(f"--check exit {chk.returncode}: {chk.stderr.strip()!r}")
            if problems:
                raise Fail(f"{case}, wanted exit {want_exit} and {want_err!r}: " + '; '.join(problems))
        finally:
            shutil.rmtree(d, ignore_errors=True)


IFACE_FORGED = {
    'lib.obx': 'module lib\npub fn fwd(): int { return 1 }\n',
    'mid.obx': ('module mid\nimport "lib.obx"\npub type I = interface { mut fn f() }\n'
                'pub type T = ref record { pub k: int }\n'
                'pub fn (mut t: &T) f() {\n    let x = lib::fwd()\n    t.k = x\n}\n'
                'pub impl I for T\n'),
    'main.obx': 'module main\nimport "mid.obx"\nfn main() { }\n',
}


def iface_forged_freesall():
    """7.2 (freesAll conjunct): a forged lib.sym that marks a library function freesall with no writeshandles
    (the hash recomputed) makes the impl of mid.obx's interface refused by --check, and the honest file is
    accepted. Without the conjunct the forged file is accepted."""
    d = tempfile.mkdtemp(prefix='symcase-forged-')
    try:
        write_tree(d, IFACE_FORGED)
        symdir = os.path.join(d, 'sym')
        os.makedirs(symdir)
        ok(build(d, symdir, out=os.path.join(d, 'prog')), 'honest interface build')
        libpath = os.path.join(symdir, 'lib.sym')
        base = sym_text(libpath)
        if base.count(b'fn fwd(): int\n') != 1:
            raise Fail(f"lib.sym does not hold the expected heading: {base!r}")
        os.unlink(os.path.join(d, 'lib.obx'))
        ok(build(d, symdir, root='mid.obx', check=True), 'honest source-less check')
        with open(libpath, 'wb') as f:
            f.write(base)
        mutate(libpath, b'fn fwd(): int\n', b'fn fwd(): int freesall\n')
        q = build(d, symdir, root='mid.obx', check=True)
        want = ("method 'T::f' cannot implement 'I::f': it might free anything, because it calls a function "
                "that is not compiled yet; define that function first")
        if q.returncode != 1 or want not in q.stderr:
            raise Fail(f"forged freesall: exit {q.returncode}, wanted 1 and '{want}': {q.stderr.strip()}")
    finally:
        shutil.rmtree(d, ignore_errors=True)


def capture_sourceless_pure():
    """A source-less, well-formed lib.sym whose callback helpers are pure accepts a handle-rooted
    capture in both argument orders. This is the honest control for capture_forged_writeshandles;
    it is a real --check exit-0 assertion, not REF==PORT and not a source-less full build."""
    files = {
        'lib.obx': '''module lib
pub fn apply(x: int, f: fn(int): int): int { return f(x) }
pub fn applyf(f: fn(int): int, x: int): int { return f(x) }
''',
        'main.obx': '''module main
import "lib.obx"
type Box = ref record { pub v: int }
type Bag = ref record { pub items: []Box; pub n: int }
fn main() {
    let b = new Bag{items: new []Box(0), n: 0}
    append(b.items, new Box{v: 1})
    let h = handle(b)
    let r = &h.items[0]
    println(lib::apply(1, fn [r](x: int): int { return x + r.v }))
    println(lib::applyf(fn [r](x: int): int { return x + r.v }, 1))
}
''',
    }
    d, symdir = tree(files, prefix='symcase-capture-pure-')
    try:
        ok(build(d, symdir, out=os.path.join(d, 'p')), 'honest pure build')
        os.unlink(os.path.join(d, 'lib.obx'))
        q = build(d, symdir, check=True)
        ok(q, 'source-less pure check')
    finally:
        shutil.rmtree(d, ignore_errors=True)


def capture_forged_writeshandles():
    """A well-formed source-less lib.sym may state writeshandles without freesall. A handle-rooted
    capture must still be refused at the creating call, before and after a plain argument, with the
    capture wording -- not the generic source-less refusal and not silently accepted."""
    files = {
        'lib.obx': '''module lib
pub fn bad(n: int, f: fn(int): int): int { return f(n) }
pub fn good(f: fn(int): int, n: int): int { return f(n) }
''',
        'main_bad.obx': '''module main
import "lib.obx"
type Box = ref record { pub v: int }
type Bag = ref record { pub items: []Box; pub n: int }
fn main() {
    let b = new Bag{items: new []Box(0), n: 0}
    append(b.items, new Box{v: 1})
    let h = handle(b)
    let r = &h.items[0]
    println(lib::bad(1, fn [r](x: int): int { return x + r.v }))
}
''',
        'main_good.obx': '''module main
import "lib.obx"
type Box = ref record { pub v: int }
type Bag = ref record { pub items: []Box; pub n: int }
fn main() {
    let b = new Bag{items: new []Box(0), n: 0}
    append(b.items, new Box{v: 1})
    let h = handle(b)
    let r = &h.items[0]
    println(lib::good(fn [r](x: int): int { return x + r.v }, 1))
}
''',
    }
    d, symdir = tree(files, prefix='symcase-capture-forged-')
    try:
        ok(build(d, symdir, root='main_bad.obx', out=os.path.join(d, 'p')), 'honest build')
        libpath = os.path.join(symdir, 'lib.sym')
        base = sym_text(libpath)
        if base.count(b'fn bad(p0: int, p1: fn(int): int): int\n') != 1:
            raise Fail(f"lib.sym does not hold the bad heading: {base!r}")
        if base.count(b'fn good(p0: fn(int): int, p1: int): int\n') != 1:
            raise Fail(f"lib.sym does not hold the good heading: {base!r}")
        os.unlink(os.path.join(d, 'lib.obx'))
        with open(libpath, 'wb') as f:
            f.write(base)
        mutate(libpath, b'fn bad(p0: int, p1: fn(int): int): int\n',
               b'fn bad(p0: int, p1: fn(int): int): int writeshandles\n')
        mutate(libpath, b'fn good(p0: fn(int): int, p1: int): int\n',
               b'fn good(p0: fn(int): int, p1: int): int writeshandles\n')
        for root, name in (('main_bad.obx', 'lib::bad'), ('main_good.obx', 'lib::good')):
            q = build(d, symdir, root=root, check=True)
            want = ("cannot pass a closure that captures a reference through a handle to " + name +
                    ": it might free what the handle names")
            if q.returncode != 1 or want not in q.stderr:
                raise Fail(f"forged writeshandles {root}: exit {q.returncode}, wanted 1 and {want!r}: {q.stderr.strip()}")
    finally:
        shutil.rmtree(d, ignore_errors=True)


def field_visibility_roundtrip():
    '''Format 7: mixed records emit pub/priv, re-emit byte-identically, and a
    source-less --check keeps public access, private diagnostics and construction
    refusal identical to the full build.'''
    files = {
        'lib.obx': '''module lib
pub type Mixed = ref record {
    pub a: int
    b: []byte
    pub c: string
    d: int
}
pub type Empty = ref record { }
pub fn mk(): Mixed { return new Mixed{a: 1, b: new []byte(0), c: "x", d: 2} }
pub fn (x: &Mixed) peek(): int { return x.a }
''',
        'main.obx': '''module main
import "lib.obx"
fn main() {
    var x = lib::mk()
    println(x.a, x.c)
    x.a = 5
    let e = new lib::Empty{}
    drop(e)
}
''',
    }
    d, s = tree(files)
    try:
        ok(build(d, s, out=os.path.join(d, 'p')), 'build')
        lib = os.path.join(s, 'lib.sym')
        text = sym_text(lib)
        if b'obosym 12\n' not in text or b'compiler "0.19"' not in text:
            raise Fail(f"format/compiler header is wrong: {text[:80]!r}")
        if b'type Mixed = ref record { pub a: int; priv; pub c: string; priv }' not in text:
            raise Fail(f"mixed field list is not canonical: {text!r}")
        if b'type Empty = ref record { }' not in text:
            raise Fail(f"zero-field record is not canonical: {text!r}")
        r = reemit(lib)
        ok(r, 'reemit')
        if r.stdout != text:
            raise Fail('--reemit-sym is not byte-identical')
        # a visible method used as a value names the method, not a private field
        open(os.path.join(d, 'bad.obx'), 'w').write(
            'module main\nimport "lib.obx"\nfn main() { let x = lib::mk(); let f = x.peek }\n')
        want = "method 'lib::Mixed::peek' cannot be used as a value: call it with '(' after its name"
        q = build(d, s, root='bad.obx')
        if q.returncode != 1 or want not in q.stderr:
            raise Fail(f"full method-as-value: exit {q.returncode}: {q.stderr.strip()}")
        os.rename(os.path.join(d, 'lib.obx'), os.path.join(d, 'lib.obx.bak'))
        ok(build(d, s, check=True), 'source-less positive')
        q = build(d, s, root='bad.obx', check=True)
        if q.returncode != 1 or want not in q.stderr:
            raise Fail(f"checked method-as-value: exit {q.returncode}: {q.stderr.strip()}")
        open(os.path.join(d, 'bad.obx'), 'w').write(
            'module main\nimport "lib.obx"\nfn main() { let x = lib::mk(); println(x.b) }\n')
        q = build(d, s, root='bad.obx', check=True)
        want = ("type 'lib::Mixed' has no public field 'b' "
                "(only 'pub' fields are visible outside module 'lib'; mark 'b' 'pub' or add an accessor)")
        if q.returncode != 1 or want not in q.stderr:
            raise Fail(f"source-less private read: exit {q.returncode}: {q.stderr.strip()}")
        open(os.path.join(d, 'bad.obx'), 'w').write(
            'module main\nimport "lib.obx"\nfn main() { let x = new lib::Mixed{a: 1} }\n')
        q = build(d, s, root='bad.obx', check=True)
        want = ("cannot construct 'lib::Mixed' outside its module: it has private fields; "
                "add a constructor function in module 'lib'")
        if q.returncode != 1 or want not in q.stderr:
            raise Fail(f"source-less construction: exit {q.returncode}: {q.stderr.strip()}")
    finally:
        shutil.rmtree(d, ignore_errors=True)


def field_visibility_public_names():
    '''pub priv and pub priviface are ordinary public fields, distinct from the
    private markers, and re-emit byte-for-byte.'''
    files = {
        'lib.obx': '''module lib
pub type Odd = ref record {
    pub priv: int
    pub priviface: int
    x: int
}
pub fn mk(): Odd { return new Odd{priv: 1, priviface: 2, x: 3} }
''',
        'main.obx': 'module main\nimport "lib.obx"\nfn main() { let o = lib::mk(); println(o.priv, o.priviface) }\n',
    }
    d, s = tree(files)
    try:
        ok(build(d, s, out=os.path.join(d, 'p')), 'build')
        lib = os.path.join(s, 'lib.sym')
        text = sym_text(lib)
        if b'type Odd = ref record { pub priv: int; pub priviface: int; priv }' not in text:
            raise Fail(f"pub priv/priviface field list is wrong: {text!r}")
        if reemit(lib).stdout != text:
            raise Fail('pub priv/priviface re-emission is not byte-identical')
    finally:
        shutil.rmtree(d, ignore_errors=True)


def field_visibility_markers():
    '''Malformed visibility markers are symbol-file errors with a line:col.'''
    files = {
        'lib.obx': '''module lib
pub type Mixed = ref record {
    pub a: int
    b: []byte
}
pub type Plain = ref record { pub v: int }
pub type Shape = closed { Dot { x: int } }
''',
        'main.obx': 'module main\nimport "lib.obx"\nfn main() { }\n',
    }
    d, s = tree(files)
    try:
        ok(build(d, s), 'base')
        lib = os.path.join(s, 'lib.sym')
        base = sym_text(lib)
        cases = [
            ('private name', b'{ pub a: int; priv }', b'{ pub a: int; priv x: int }',
             'a private field has no name or type'),
            ('private colon', b'{ pub a: int; priv }', b'{ pub a: int; priv: int }',
             'a private field has no name or type'),
            ('unknown marker', b'{ pub a: int; priv }', b'{ pub a: int; wat }',
             "expected 'pub', 'priv' or 'priviface' in a record field list"),
            ('bare public', b'{ pub v: int }', b'{ v: int }',
             "expected 'pub', 'priv' or 'priviface' in a record field list"),
            ('variant private', b'Dot { pub x: int }', b'Dot { priv }',
             'private fields are not allowed in a closed variant'),
        ]
        for name, old, new, want in cases:
            if base.count(old) != 1:
                raise Fail(f"{name}: {old!r} occurs {base.count(old)} times")
            open(lib, 'wb').write(base)
            mutate(lib, old, new)
            q = build(d, s, check=True)
            if q.returncode != 2 or 'symbol file:' not in q.stderr or want not in q.stderr:
                raise Fail(f"{name}: exit {q.returncode}: {q.stderr.strip()}")
            if ':' not in q.stderr.split('symbol file:')[0]:
                raise Fail(f"{name}: no line:col in {q.stderr.strip()!r}")
        open(lib, 'wb').write(base)
        mutate(lib, b'{ pub v: int }', b'{ pub v: int; pub v: int }')
        q = build(d, s, check=True)
        if q.returncode != 2 or "duplicate field 'v'" not in q.stderr:
            raise Fail(f"duplicate public field: exit {q.returncode}: {q.stderr.strip()}")
    finally:
        shutil.rmtree(d, ignore_errors=True)


def field_visibility_tamper():
    '''An unrehashed visibility change is caught by the interface hash; a rehashed
    one is accepted (trusted build input) and then construct-refused.'''
    files = {
        'lib.obx': 'module lib\npub type P = ref record { pub v: int }\npub fn mk(): P { return new P{v: 1} }\n',
        'main.obx': 'module main\nimport "lib.obx"\nfn main() { let p = lib::mk() }\n',
    }
    d, s = tree(files)
    try:
        ok(build(d, s, out=os.path.join(d, 'p')), 'build')
        lib = os.path.join(s, 'lib.sym')
        base = sym_text(lib)
        open(lib, 'wb').write(base)
        mutate(lib, b'{ pub v: int }', b'{ priv }', recompute=False)
        q = build(d, s, check=True)
        if q.returncode != 2 or 'is corrupt (interface hash)' not in q.stderr:
            raise Fail(f"unhashed tamper: exit {q.returncode}: {q.stderr.strip()}")
        open(lib, 'wb').write(base)
        mutate(lib, b'{ pub v: int }', b'{ priv }', recompute=True)
        ok(build(d, s, check=True), 'rehashed tamper is trusted input')
        open(os.path.join(d, 'bad.obx'), 'w').write(
            'module main\nimport "lib.obx"\nfn main() { let p = new lib::P{v: 1} }\n')
        q = build(d, s, root='bad.obx', check=True)
        if q.returncode != 1 or 'it has private fields' not in q.stderr:
            raise Fail(f"rehashed private construction: exit {q.returncode}: {q.stderr.strip()}")
    finally:
        shutil.rmtree(d, ignore_errors=True)


def field_visibility_hashes():
    '''Private field identity/type changes keep the hash only when no exported
    summary changes; carrier-class changes always change it.'''
    base = '''module lib
pub type R = ref record {
    buf: []byte
    n: int
}
pub fn make(): R { return new R{buf: new []byte(0), n: 0} }
pub fn reset(mut r: &R) {
    r.buf = new []byte(0)
    r.n = 0
}
'''
    main = 'module main\nimport "lib.obx"\nfn main() { let r = lib::make(); lib::reset(r) }\n'
    # design section 11.1 field_visibility_pf_leak: the private field list is
    # opaque, while the exported summary names "buf" and is not redacted.
    d, s = tree({'lib.obx': base, 'main.obx': main})
    try:
        ok(build(d, s, out=os.path.join(d, 'p')), 'pf leak build')
        lib = os.path.join(s, 'lib.sym')
        text = sym_text(lib)
        if b'type R = ref record { priv; priv }' not in text:
            raise Fail(f'private field list is not opaque: {text!r}')
        if b'fn reset(mut p0: &R) pfree 0x1 pf 0 "=/buf"' not in text:
            raise Fail(f'private summary path is missing or redacted: {text!r}')
        if reemit(lib).stdout != text:
            raise Fail('pf-leak fixture re-emission is not byte-identical')
    finally:
        shutil.rmtree(d, ignore_errors=True)
    def ihash(src):
        d, s = tree({'lib.obx': src, 'main.obx': main})
        try:
            ok(build(d, s, out=os.path.join(d, 'p')), 'variant build')
            return sym_header(os.path.join(s, 'lib.sym'))['interface']
        finally:
            shutil.rmtree(d, ignore_errors=True)
    h0 = ihash(base)
    h_n = ihash(base.replace('n: int', 'm: int').replace('n: 0', 'm: 0').replace('r.n', 'r.m'))
    if h_n != h0:
        raise Fail('renaming a private scalar not named in a summary changed the interface hash')
    h_buf = ihash(base.replace('buf', 'data'))
    if h_buf == h0:
        raise Fail('renaming a private field named in a pf path kept the interface hash')
    h_int = ihash(base.replace('buf: []byte', 'buf: []int')
                      .replace('new []byte(0), n', 'new []int(0), n')
                      .replace('r.buf = new []byte(0)', 'r.buf = new []int(0)'))
    if h_int != h0:
        raise Fail('[]byte -> []int on a private pf-path field changed the interface hash')
    h_scalar = ihash('''module lib
pub type R = ref record {
    buf: int
    n: int
}
pub fn make(): R { return new R{buf: 0, n: 0} }
pub fn reset(mut r: &R) {
    r.n = 0
}
''')
    if h_scalar == h0:
        raise Fail('[]byte -> int on a private pf-path field kept the interface hash')
    h_pub = ihash('module lib\npub type R = ref record { pub buf: []byte; pub n: int }\npub fn make(): R { return new R{buf: new []byte(0), n: 0} }\npub fn reset(mut r: &R) { }\n')
    h_priv = ihash('module lib\npub type R = ref record { buf: []byte; pub n: int }\npub fn make(): R { return new R{buf: new []byte(0), n: 0} }\npub fn reset(mut r: &R) { }\n')
    if h_pub == h_priv:
        raise Fail('public -> private did not change the interface hash')
    h_iface = ihash('''module lib
pub type Shape = interface { pure fn area(): int }
pub type Square = ref record { pub s: int }
pub fn (q: &Square) area(): int { return q.s }
pub impl Shape for Square
pub type R = ref record {
    buf: []byte
    h: Shape
}
pub fn make(): R { return new R{buf: new []byte(0), h: new Square{s: 1}} }
pub fn reset(mut r: &R) { }
''')
    if h_iface == h0:
        raise Fail('a private interface carrier did not change the interface hash')


def field_visibility_carrier_multi():
    '''B3: each per-field carrier query starts clean, so both fields are priviface.'''
    files = {
        'lib.obx': '''module lib
pub type Shape = interface { pure fn area(): int }
pub type Square = ref record { pub s: int }
pub fn (q: &Square) area(): int { return q.s }
pub impl Shape for Square
type C = ref record { s: Shape }
type B = ref record { c: C }
pub type A = ref record {
    b: B
    c: C
}
pub fn mk(): A {
    return new A{b: new B{c: new C{s: new Square{s: 1}}},
                 c: new C{s: new Square{s: 2}}}
}
''',
        'main.obx': 'module main\nimport "lib.obx"\nfn main() { let a = lib::mk() }\n',
    }
    d, s = tree(files)
    try:
        ok(build(d, s, out=os.path.join(d, 'p')), 'build')
        lib = os.path.join(s, 'lib.sym')
        text = sym_text(lib)
        if b'type A = ref record { priviface; priviface }' not in text:
            raise Fail(f"B3 carrier visited state leaked across fields: {text!r}")
        if reemit(lib).stdout != text:
            raise Fail('carrier re-emission is not byte-identical')
        os.rename(os.path.join(d, 'lib.obx'), os.path.join(d, 'lib.obx.bak'))
        open(os.path.join(d, 'cap.obx'), 'w').write('''module main
import "lib.obx"
fn apply(f: fn(): int): int { return f() }
fn main() {
    let a = lib::mk()
    println(apply(fn [a](): int { return 0 }))
}
''')
        q = build(d, s, root='cap.obx', check=True)
        if q.returncode != 1 or 'holds an interface value' not in q.stderr:
            raise Fail(f"source-less carrier capture: exit {q.returncode}: {q.stderr.strip()}")
    finally:
        shutil.rmtree(d, ignore_errors=True)


def field_visibility_library_summaries():
    '''io::Reader/Writer are private and fs::Dir stays public; pf paths still name
    their buf fields, as v1 accepts.'''
    d = tempfile.mkdtemp(prefix='libsum-')
    s = os.path.join(d, 'sym')
    os.makedirs(s)
    try:
        open(os.path.join(d, 'main.obx'), 'w').write(
            'module main\nimport "io.obx" as io\nimport "fs.obx" as fs\n'
            'fn main() { let w = io::newWriter(1, 8); io::flush(w); println(fs::KIND_REG) }\n')
        libdir = os.path.join(FORK, 'lib')
        q = subprocess.run([OBO, os.path.join(d, 'main.obx'), '-o', os.path.join(d, 'p'),
                            '--rt', RT, '-I', d, '-I', libdir, '--sym-dir', s],
                           capture_output=True, text=True, timeout=120)
        ok(q, 'library build')
        io_text = sym_text(os.path.join(s, 'io.sym'))
        fs_text = sym_text(os.path.join(s, 'fs.sym'))
        if b'type Reader = ref record { priv; priv; priv; priv; priv }' not in io_text:
            raise Fail(f"io::Reader fields are not private: {io_text!r}")
        if b'type Writer = ref record { priv; priv; priv }' not in io_text:
            raise Fail(f"io::Writer fields are not private: {io_text!r}")
        if b'type Dir = ref record { pub fd: int;' not in fs_text:
            raise Fail(f"fs::Dir is not public: {fs_text!r}")
        if b'pf 0 "=/buf"' not in io_text or b'pf 0 "!/buf"' not in io_text:
            raise Fail('io pf paths are missing')
        if b'pf 0 "=/buf"' not in fs_text:
            raise Fail('fs::next pf path is missing')
        if b'fn bufferedBytes(p0: &Writer): int' not in io_text:
            raise Fail('io::bufferedBytes is not exported')
    finally:
        shutil.rmtree(d, ignore_errors=True)


def field_visibility_old_format():
    '''Format 6 is refused with the exact next-format message.'''
    files = {
        'lib.obx': 'module lib\npub type P = ref record { pub v: int }\npub fn mk(): P { return new P{v: 1} }\n',
        'main.obx': 'module main\nimport "lib.obx"\nfn main() { let p = lib::mk() }\n',
    }
    d, s = tree(files)
    try:
        ok(build(d, s, out=os.path.join(d, 'p')), 'build')
        p = os.path.join(s, 'lib.sym')
        text = sym_text(p)
        open(p, 'wb').write(text.replace(b'obosym 12', b'obosym 6', 1))
        q = build(d, s, check=True)
        want = f"obxc: symbol file '{p}' has format 6; this compiler reads 12; rebuild it"
        if q.returncode != 2 or want not in q.stderr:
            raise Fail(f"format 6: exit {q.returncode}: {q.stderr.strip()}")
        q = subprocess.run([OBO, '--version'], capture_output=True, text=True)
        want = f'obo {OBO_VERSION} sym-format {SYM_FORMAT} abi {SYM_ABI}\n'
        if q.stdout != want:
            raise Fail(f"--version: {q.stdout!r}")
    finally:
        shutil.rmtree(d, ignore_errors=True)


def decimal_fields_sym():
    '''D1: decimal record fields in a .sym. A public decimal field is written with its type; a private
    one is the 24-byte marker priv24, so the offsets of the fields after it survive. The file re-emits
    byte-identically, a source-less check of main writes the same main.sym as the full build, and a
    malformed priv24 is a symbol-file error.'''
    src = os.path.join(HERE, 'modules', 'run', 'decimal_fields')
    files = {}
    for name in ('main.obx', 'lib.obx'):
        with open(os.path.join(src, name)) as f:
            files[name] = f.read()
    d, s = tree(files)
    try:
        ok(build(d, s, out=os.path.join(d, 'prog')), 'full build of the decimal-field tree')
        lib = os.path.join(s, 'lib.sym')
        text = sym_text(lib)
        for want in (b'type Ledger = ref record { pub name: string; pub total: decimal; priv24; priv; pub fee: decimal; priv24 }',
                     b'type Shape = closed { Circle { pub r: decimal } Blank { } }'):
            if want not in text:
                raise Fail(f"decimal field line is wrong, wanted {want!r}: {text!r}")
        r = reemit(lib)
        if r.returncode != 0 or r.stdout != text:
            raise Fail('the decimal-field lib.sym does not re-emit unchanged')
        full_main = sym_text(os.path.join(s, 'main.sym'))
        os.rename(os.path.join(d, 'lib.obx'), os.path.join(d, 'lib.obx.bak'))
        chk = os.path.join(d, 'sym-check')
        shutil.copytree(s, chk)
        ok(build(d, chk, check=True), 'source-less check of the decimal-field tree')
        if sym_text(os.path.join(chk, 'main.sym')) != full_main:
            raise Fail('the source-less --check main.sym differs from the full build')
        os.rename(os.path.join(d, 'lib.obx.bak'), os.path.join(d, 'lib.obx'))
        cases = [
            ('priv24 with a name', b'priv24; priv; pub fee', b'priv24 x; priv; pub fee',
             'a private field has no name or type', True),
            ('priv24 with a type', b'priv24; priv; pub fee', b'priv24: decimal; priv; pub fee',
             'a private field has no name or type', True),
            ('misspelled marker', b'priv24; priv; pub fee', b'priv25; priv; pub fee',
             "expected 'pub', 'priv' or 'priviface' in a record field list", True),
            ('priv24 in a closed variant', b'Circle { pub r: decimal }', b'Circle { priv24 }',
             'private fields are not allowed in a closed variant', True),
            ('unrehashed priv24 change', b'priv24; priv; pub fee', b'priv; priv; pub fee',
             'is corrupt (interface hash)', False),
        ]
        base = sym_text(lib)
        for name, old, new, want, rehash in cases:
            if base.count(old) != 1:
                raise Fail(f"{name}: {old!r} occurs {base.count(old)} times")
            open(lib, 'wb').write(base)
            mutate(lib, old, new, recompute=rehash)
            q = build(d, s, check=True)
            if q.returncode != 2 or want not in q.stderr:
                raise Fail(f"{name}: exit {q.returncode}: {q.stderr.strip()}")
            if 'symbol file' not in q.stderr:
                raise Fail(f"{name}: not a symbol-file error: {q.stderr.strip()}")
        open(lib, 'wb').write(base)
    finally:
        shutil.rmtree(d, ignore_errors=True)


def decimal_arrays_sym():
    '''D2: []decimal in a .sym. A function signature, a public record field, a private owned array and a
    public global carry the array type; the file re-emits byte-identically, a source-less check of main
    writes the same main.sym as the full build, and a signature that names the wrong element type is
    refused by the check (the interface hash is recomputed, so the change reaches the importer).'''
    src = os.path.join(HERE, 'modules', 'run', 'decimal_arrays')
    files = {}
    for name in ('main.obx', 'lib.obx'):
        with open(os.path.join(src, name)) as f:
            files[name] = f.read()
    d, s = tree(files)
    try:
        ok(build(d, s, out=os.path.join(d, 'prog')), 'full build of the decimal-array tree')
        lib = os.path.join(s, 'lib.sym')
        text = sym_text(lib)
        for want in (b'type Book = ref record { pub lines: []decimal; pub name: string; priv }',
                     b'var shared: []decimal',
                     b'fn make(p0: int): []decimal',
                     b'fn total(p0: &[]decimal): decimal',
                     b'fn push(mut p0: &[]decimal, p1: decimal)'):
            if want not in text:
                raise Fail(f"decimal array line is wrong, wanted {want!r}: {text!r}")
        r = reemit(lib)
        if r.returncode != 0 or r.stdout != text:
            raise Fail('the decimal-array lib.sym does not re-emit unchanged')
        full_main = sym_text(os.path.join(s, 'main.sym'))
        os.rename(os.path.join(d, 'lib.obx'), os.path.join(d, 'lib.obx.bak'))
        chk = os.path.join(d, 'sym-check')
        shutil.copytree(s, chk)
        ok(build(d, chk, check=True), 'source-less check of the decimal-array tree')
        if sym_text(os.path.join(chk, 'main.sym')) != full_main:
            raise Fail('the source-less --check main.sym differs from the full build')
        os.rename(os.path.join(d, 'lib.obx.bak'), os.path.join(d, 'lib.obx'))
        base = sym_text(lib)
        open(lib, 'wb').write(base)
        mutate(lib, b'fn total(p0: &[]decimal): decimal', b'fn total(p0: &[]int): decimal', recompute=True)
        q = build(d, s, check=True)
        if q.returncode == 0 or 'cannot use []decimal as []int' not in q.stderr:
            raise Fail(f"a signature with the wrong element type was not refused: {q.returncode} {q.stderr.strip()}")
        open(lib, 'wb').write(base)
    finally:
        shutil.rmtree(d, ignore_errors=True)


def dec_hex(lo, hi, packed):
    """The 48 lowercase hex digits of a decimal constant: its 24 value bytes in memory order, each word
    little-endian (the same bytes the compiler writes, computed here from the words alone)."""
    return ''.join('%02x' % ((w & 0xFFFFFFFFFFFFFFFF) >> (8 * i) & 255)
                   for w in (lo, hi, packed) for i in range(8))


def decimal_globals_sym():
    '''D3: decimal globals and constants in a .sym. A public constant is `const NAME: decimal = hex "<48>"`
    (the 24 value bytes in memory order, from the three words) and a public global is `var NAME: decimal`.
    The file re-emits byte-identically, a source-less check of main writes the same main.sym as the full
    build, and each malformed constant is a symbol-file error.'''
    src = os.path.join(HERE, 'modules', 'run', 'decimal_globals')
    files = {}
    for name in ('main.obx', 'lib.obx'):
        with open(os.path.join(src, name)) as f:
            files[name] = f.read()
    d, s = tree(files)
    try:
        ok(build(d, s, out=os.path.join(d, 'prog')), 'full build of the decimal-global tree')
        lib = os.path.join(s, 'lib.sym')
        text = sym_text(lib)
        rate = dec_hex(825, 0, 0xfffc | (4 << 16))            # 0.0825: coefficient 825, exponent -4, dscale 4
        neg = dec_hex(15, 0, 0xffff | (1 << 16) | (1 << 32))  # -1.5: coefficient 15, exponent -1, dscale 1, sign 1
        neg0 = dec_hex(0, 0, 2 << 16)                         # -0.00 is 0.00: dscale 2, sign 0
        for want in (b'const RATE: decimal = hex "' + rate.encode() + b'"',
                     b'const NEG: decimal = hex "' + neg.encode() + b'"',
                     b'const NEG0: decimal = hex "' + neg0.encode() + b'"',
                     b'var total: decimal', b'var base: decimal', b'var start: decimal'):
            if want not in text:
                raise Fail(f"decimal global or constant line is wrong, wanted {want!r}: {text!r}")
        if b'HIDDEN' in text:
            raise Fail('a private constant is in the .sym')
        r = reemit(lib)
        if r.returncode != 0 or r.stdout != text:
            raise Fail('the decimal-global lib.sym does not re-emit unchanged')
        full_main = sym_text(os.path.join(s, 'main.sym'))
        os.rename(os.path.join(d, 'lib.obx'), os.path.join(d, 'lib.obx.bak'))
        chk = os.path.join(d, 'sym-check')
        shutil.copytree(s, chk)
        ok(build(d, chk, check=True), 'source-less check of the decimal-global tree')
        if sym_text(os.path.join(chk, 'main.sym')) != full_main:
            raise Fail('the source-less --check main.sym differs from the full build')
        os.rename(os.path.join(d, 'lib.obx.bak'), os.path.join(d, 'lib.obx'))
        base = sym_text(lib)
        # the reader's list: each malformed constant is refused with its own message
        bad = [
            ('uppercase hex', rate, rate.upper(), 'a decimal constant\'s hex must be lowercase'),
            ('47 digits', rate, rate[:-1], 'a decimal constant needs 48 lowercase hexadecimal digits'),
            ('49 digits', rate, rate + '0', 'a decimal constant needs 48 lowercase hexadecimal digits'),
            ('a non-hex digit', rate, rate[:47] + 'g', 'a decimal constant\'s hex has a character that is not hexadecimal'),
            ('coefficient above 10^38-1', rate, dec_hex(825, 0x7FFFFFFFFFFFFFFF, 0xfffc | (4 << 16)),
             'a decimal constant has more than 38 significant digits'),
            ('coefficient 10^38', rate, dec_hex(0x098A224000000000, 0x4B3B4CA85A86C47A, 0),
             'a decimal constant has more than 38 significant digits'),
            ('a negative coefficient word', rate, dec_hex(825, -1, 0xfffc | (4 << 16)),
             'a decimal constant has more than 38 significant digits'),
            ('reserved bit 40', neg, dec_hex(15, 0, 0xffff | (1 << 16) | (1 << 32) | (1 << 40)),
             'a decimal constant\'s reserved bits (40 to 63 of its last word) must be zero'),
            ('reserved bit 63', neg, dec_hex(15, 0, 0xffff | (1 << 16) | (1 << 32) | (1 << 63)),
             'a decimal constant\'s reserved bits (40 to 63 of its last word) must be zero'),
            ('sign 2', neg, dec_hex(15, 0, 0xffff | (1 << 16) | (2 << 32)),
             'a decimal constant\'s sign must be 0 or 1'),
            ('zero with sign 1', neg0, dec_hex(0, 0, (2 << 16) | (1 << 32)),
             'a decimal constant zero must have sign 0'),
            ('zero with exponent 1', neg0, dec_hex(0, 0, (2 << 16) | 1),
             'a decimal constant zero must have exponent 0'),
        ]
        for name, old, new, want in bad:
            old_b = old.encode() if isinstance(old, str) else old
            if base.count(old_b) != 1:
                raise Fail(f"{name}: {old!r} occurs {base.count(old_b)} times")
            open(lib, 'wb').write(base)
            mutate(lib, old_b, new.encode(), recompute=True)
            q = build(d, s, check=True)
            if q.returncode != 2 or want not in q.stderr:
                raise Fail(f"{name}: exit {q.returncode}: {q.stderr.strip()}")
            if 'symbol file' not in q.stderr:
                raise Fail(f"{name}: not a symbol-file error: {q.stderr.strip()}")
        # the boundary: 10^38-1 is a valid coefficient, and the check build accepts it
        open(lib, 'wb').write(base)
        mutate(lib, rate.encode(), dec_hex(0x098A223FFFFFFFFF, 0x4B3B4CA85A86C47A, 0xfffc | (4 << 16)).encode(),
               recompute=True)
        ok(build(d, s, check=True), 'a 38-digit coefficient of 10^38-1 in a constant')
        open(lib, 'wb').write(base)
    finally:
        shutil.rmtree(d, ignore_errors=True)


def decimal_pfree_sym():
    '''D4a: the free summary when a decimal parameter follows a mut reference. recordParamFree
    must map the reference's rbp offset back to its parameter index over the whole signature: the
    old offset/8 mapping saw a 24-byte parameter and dropped the bit entirely. Index 0 (a mut
    receiver) and index 2 (after two int parameters) are both pinned, so a regression that only
    misnames a higher index is caught. The written lines carry the right pfree masks and pf rows;
    the file re-emits byte-identically, and a source-less check rebuilds it identically.'''
    files = {
        'lib.obx': ('module lib\n'
                    'pub type Inner = ref record { pub v: int }\n'
                    'pub type Box = ref record { pub inner: Inner; pub n: int }\n'
                    'pub fn (mut b: &Box) redo(d: decimal): decimal {\n'
                    '    b.inner = new Inner{v: 2}\n'
                    '    return d\n'
                    '}\n'
                    'pub fn f(a: int, b: int, mut c: &Box, d: decimal): decimal {\n'
                    '    c.inner = new Inner{v: 3}\n'
                    '    return d\n'
                    '}\n'),
        'main.obx': 'module main\nimport "lib.obx"\nfn main() { }\n',
    }
    d, s = tree(files)
    try:
        ok(build(d, s, out=os.path.join(d, 'prog')), 'full build of the decimal-pfree tree')
        lib = os.path.join(s, 'lib.sym')
        text = sym_text(lib)
        for want in (b'fn (mut p0: &Box) redo(p1: decimal): decimal pfree 0x1 pf 0 "=/inner"',
                     b'fn f(p0: int, p1: int, mut p2: &Box, p3: decimal): decimal pfree 0x4 pf 2 "=/inner"'):
            if want not in text:
                raise Fail(f"a pfree bit or row is wrong, wanted {want!r}: {text!r}")
        r = reemit(lib)
        if r.returncode != 0 or r.stdout != text:
            raise Fail('the decimal-pfree lib.sym does not re-emit unchanged')
        full_main = sym_text(os.path.join(s, 'main.sym'))
        os.rename(os.path.join(d, 'lib.obx'), os.path.join(d, 'lib.obx.bak'))
        chk = os.path.join(d, 'sym-check')
        shutil.copytree(s, chk)
        ok(build(d, chk, check=True), 'source-less check of the decimal-pfree tree')
        if sym_text(os.path.join(chk, 'main.sym')) != full_main:
            raise Fail('the source-less --check main.sym differs from the full build')
        os.rename(os.path.join(d, 'lib.obx.bak'), os.path.join(d, 'lib.obx'))
    finally:
        shutil.rmtree(d, ignore_errors=True)


def decimal_methods_ifaces_sym():
    '''D4a: decimal method and interface signatures in a .sym. An interface member writes its decimal
    parameters and return, a method writes them in its receiver-plus-parameters form, and a function
    writes them in a plain signature. The file re-emits byte-identically, a source-less check of main
    writes the same main.sym as the full build, and a format-10 file (a 0.17 reader's output) is
    refused before its body is read.'''
    src = os.path.join(HERE, 'modules', 'run', 'decimal_iface')
    files = {}
    for name in ('main.obx', 'lib.obx'):
        with open(os.path.join(src, name)) as f:
            files[name] = f.read()
    d, s = tree(files)
    try:
        ok(build(d, s, out=os.path.join(d, 'prog')), 'full build of the decimal-interface tree')
        lib = os.path.join(s, 'lib.sym')
        text = sym_text(lib)
        for want in (
            b'type Ledger = interface { pure fn add(p0: decimal, p1: decimal): decimal; '
            b'fn scale(p0: int, p1: decimal, p2: string): decimal; mut fn deposit(p0: decimal): decimal; '
            b'impure fn charge(p0: decimal): decimal }',
            b'fn (p0: &Cash) add(p1: decimal, p2: decimal): decimal',
            b'fn (p0: &Cash) scale(p1: int, p2: decimal, p3: string): decimal',
            b'fn (mut p0: &Cash) deposit(p1: decimal): decimal',
            b'fn (p0: &Cash) charge(p1: decimal): decimal',
            b'fn make(p0: decimal): Ledger',
            b'fn total(p0: Ledger, p1: Ledger): decimal',
        ):
            if want not in text:
                raise Fail(f"decimal method or interface line is wrong, wanted {want!r}: {text!r}")
        if b'compiler "0.19"' not in text or b'obosym 12\n' not in text:
            raise Fail(f"the format/compiler header is wrong: {text[:60]!r}")
        r = reemit(lib)
        if r.returncode != 0 or r.stdout != text:
            raise Fail('the decimal-method lib.sym does not re-emit unchanged')
        full_main = sym_text(os.path.join(s, 'main.sym'))
        os.rename(os.path.join(d, 'lib.obx'), os.path.join(d, 'lib.obx.bak'))
        chk = os.path.join(d, 'sym-check')
        shutil.copytree(s, chk)
        ok(build(d, chk, check=True), 'source-less check of the decimal-interface tree')
        if sym_text(os.path.join(chk, 'main.sym')) != full_main:
            raise Fail('the source-less --check main.sym differs from the full build')
        # a 0.17 (.sym format 10) file is refused, even though its text grammar is this one
        chk_lib = os.path.join(chk, 'lib.sym')
        base = sym_text(chk_lib)
        with open(chk_lib, 'wb') as f:
            f.write(base.replace(b'obosym 12', b'obosym 10', 1))
        q = build(d, chk, check=True)
        want = 'has format 10; this compiler reads 12; rebuild it'
        if q.returncode != 2 or want not in q.stderr:
            raise Fail(f"format-10 decimal interface: exit {q.returncode}: {q.stderr.strip()}")
        with open(chk_lib, 'wb') as f:
            f.write(base)
        os.rename(os.path.join(d, 'lib.obx.bak'), os.path.join(d, 'lib.obx'))
        # the check build is still gone (the source was absent); the normal tree builds again
        ok(build(d, s, out=os.path.join(d, 'again')), 'rebuild after the format-10 check')
    finally:
        shutil.rmtree(d, ignore_errors=True)


def decimal_fnvalue_sym():
    '''D4b: decimal function types in a .sym. A public alias keeps the canonical `fn(decimal): decimal`
    text, a function signature expands the alias, a `Result[decimal]` return is one word, and a
    source-less --check rebuilds main.sym identically. A format-11 file is refused before its body is
    read, and the reader rejects a borrowed decimal, a function type returning a function type and a
    capture list.'''
    src = os.path.join(HERE, 'modules', 'run', 'decimal_fnvalue')
    files = {}
    for name in ('main.obx', 'lib.obx'):
        with open(os.path.join(src, name)) as f:
            files[name] = f.read()
    d, s = tree(files)
    try:
        ok(build(d, s, out=os.path.join(d, 'prog')), 'full build of the decimal-function-value tree')
        lib = os.path.join(s, 'lib.sym')
        text = sym_text(lib)
        for want in (
            b'type DecFn = fn(decimal): decimal',
            b'type DecResultFn = fn(decimal): Result[decimal]',
            b'fn apply(p0: decimal, p1: fn(decimal): decimal): decimal',
            b'fn applyR(p0: decimal, p1: fn(decimal): Result[decimal]): Result[decimal]',
            b'fn zero(p0: fn(): decimal): decimal',
            b'fn scale(p0: decimal): decimal',
        ):
            if want not in text:
                raise Fail(f"decimal function type line is wrong, wanted {want!r}: {text!r}")
        if b'compiler "0.19"' not in text or b'obosym 12\n' not in text:
            raise Fail(f"the format/compiler header is wrong: {text[:60]!r}")
        r = reemit(lib)
        if r.returncode != 0 or r.stdout != text:
            raise Fail('the decimal-function-value lib.sym does not re-emit unchanged')
        full_main = sym_text(os.path.join(s, 'main.sym'))
        os.rename(os.path.join(d, 'lib.obx'), os.path.join(d, 'lib.obx.bak'))
        chk = os.path.join(d, 'sym-check')
        shutil.copytree(s, chk)
        ok(build(d, chk, check=True), 'source-less check of the decimal-function-value tree')
        if sym_text(os.path.join(chk, 'main.sym')) != full_main:
            raise Fail('the source-less --check main.sym differs from the full build')
        # a 0.18 (.sym format 11) file is refused, even though its text grammar is this one
        chk_lib = os.path.join(chk, 'lib.sym')
        base = sym_text(chk_lib)
        with open(chk_lib, 'wb') as f:
            f.write(base.replace(b'obosym 12', b'obosym 11', 1))
        q = build(d, chk, check=True)
        want = 'has format 11; this compiler reads 12; rebuild it'
        if q.returncode != 2 or want not in q.stderr:
            raise Fail(f"format-11 decimal function type: exit {q.returncode}: {q.stderr.strip()}")
        # the reader's remaining function-type rules
        for old, new, err in (
            (b'type DecFn = fn(decimal): decimal',
             b'type DecFn = fn(&decimal): decimal',
             'only records, closed types and arrays can be borrowed, not decimal'),
            (b'type DecFn = fn(decimal): decimal',
             b'type DecFn = fn(decimal): fn(int): int',
             'a function type cannot return a function value'),
            (b'type DecFn = fn(decimal): decimal',
             b'type DecFn = fn[decimal](decimal): decimal',
             'a function type cannot have a capture list'),
        ):
            with open(chk_lib, 'wb') as f:
                f.write(base)
            mutate(chk_lib, old, new, recompute=True)
            q = build(d, chk, check=True)
            if q.returncode != 2 or err not in q.stderr:
                raise Fail(f"decimal function-type rejection {new!r}: exit {q.returncode}: {q.stderr.strip()}")
        with open(chk_lib, 'wb') as f:
            f.write(base)
        os.rename(os.path.join(d, 'lib.obx.bak'), os.path.join(d, 'lib.obx'))
        ok(build(d, s, out=os.path.join(d, 'again')), 'rebuild after the function-type reader cases')
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
        ('sym/hash_methods', hash_methods),
        ('sym/malformed_methods', malformed_methods),
        ('sym/methods_sourceless_check', methods_sourceless_check),
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
        ('sym/iface_summary_matrix', iface_summary_matrix),
        ('sym/iface_private_impl', iface_private_impl),
        ('sym/iface_reader_refusals', iface_reader_refusals),
        ('sym/iface_owned_check', iface_owned_check),
        ('sym/iface_ref_check', iface_ref_check),
        ('sym/iface_impure_roundtrip', iface_impure_roundtrip),
        ('sym/stale_format3_interface', stale_format3_interface),
        ('sym/fn_sym_roundtrip', fn_sym_roundtrip),
        ('sym/stale_format4_fn_rejected', stale_format4_fn_rejected),
        ('sym/forged_fn_positions', forged_fn_positions),
        ('sym/qualified_fnfwd_alias', qualified_fnfwd_alias),
        ('sym/iface_tier_check', iface_tier_check),
        ('sym/iface_forged_freesall', iface_forged_freesall),
        ('sym/capture_sourceless_pure', capture_sourceless_pure),
        ('sym/capture_forged_writeshandles', capture_forged_writeshandles),
        ('sym/field_visibility_roundtrip', field_visibility_roundtrip),
        ('sym/field_visibility_public_names', field_visibility_public_names),
        ('sym/field_visibility_markers', field_visibility_markers),
        ('sym/field_visibility_tamper', field_visibility_tamper),
        ('sym/field_visibility_hashes', field_visibility_hashes),
        ('sym/field_visibility_carrier_multi', field_visibility_carrier_multi),
        ('sym/field_visibility_library_summaries', field_visibility_library_summaries),
        ('sym/field_visibility_old_format', field_visibility_old_format),
        ('sym/decimal_fields', decimal_fields_sym),
        ('sym/decimal_arrays', decimal_arrays_sym),
        ('sym/decimal_globals', decimal_globals_sym),
        ('sym/decimal_methods_ifaces', decimal_methods_ifaces_sym),
        ('sym/decimal_pfree', decimal_pfree_sym),
        ('sym/decimal_fnvalue', decimal_fnvalue_sym),
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
