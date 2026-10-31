#!/usr/bin/env python3
"""Targeted tests for the experimental cinterop prototype.

    python3 obo/experimental/cinterop/tests/test_cinterop.py

This is not part of the public compiler test catalog. It builds a real scalar
module end to end, checks the ABI/alignment assertions, checks the synthetic
startup stack and its bounds, exercises direct-call and provenance failures, and
conditionally builds the PQlibVersion probe when the host has libpq.so.5. No
database connection is made.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
TOOL = os.path.dirname(HERE)
REPO = os.path.dirname(os.path.dirname(os.path.dirname(TOOL)))   # repository root
EX = os.path.join(TOOL, 'examples')
OBO = os.path.abspath(os.environ.get('OBO', os.path.join(REPO, 'obo', 'obo')))
TOOLPY = os.path.join(TOOL, 'cinterop.py')
TMPROOT = '/tmp/opencode'


class Fail(Exception):
    pass


def run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=300, **kw)


def must(cmd, **kw):
    p = run(cmd, **kw)
    if p.returncode != 0:
        raise Fail("command failed: %s\n%s" % (' '.join(cmd), (p.stderr or p.stdout).strip()))
    return p


def build(spec, app, out):
    return run([sys.executable, TOOLPY, 'build', spec, app, '--out', out, '--obo', OBO])


def need(p, what):
    if p.returncode != 0:
        raise Fail("%s failed:\n%s" % (what, (p.stderr or p.stdout).strip()))
    return p


def verify(out):
    return run([sys.executable, TOOLPY, 'verify', os.path.join(out, 'manifest.json')])


def scalar(tmp):
    out = os.path.join(tmp, 'scalar')
    need(build(os.path.join(EX, 'scalar.cbind'), os.path.join(EX, 'scalar_app.obx'), out), 'scalar build')
    p = need(run([os.path.join(out, 'app')], env=dict(os.environ, OBO_CHECK='1')), 'scalar run')
    if p.stdout.strip() != 'scalar ok':
        raise Fail("scalar output was %r" % p.stdout.strip())
    sym = open(os.path.join(out, 'sym', 'hello.sym')).read()
    if 'fn add(p0: int, p1: int): int freesall writeshandles' not in sym:
        raise Fail("wrapper .sym is not conservatively freesall/writeshandles")
    relocs = open(os.path.join(out, 'native.relocs')).read().splitlines()
    if not relocs or relocs[0] != 'cinterop-relocs 1':
        raise Fail("native sidecar header is missing")
    if not any(' add 2 i64,i64 i64 freesall,writeshandles' in ln for ln in relocs):
        raise Fail("native sidecar does not carry the checked add contract")
    elf = need(run(['readelf', '-hW', os.path.join(out, 'app')]), 'readelf -h')
    if 'EXEC' not in elf.stdout:
        raise Fail("scalar artifact is not ET_EXEC")
    ph = need(run(['readelf', '-lW', os.path.join(out, 'app')]), 'readelf -l')
    for addr in ('0x0000000000401000', '0x0000000008000000', '0x000000000c000000',
                 '0x0000000010000000', '0x0000000020000000'):
        if addr not in ph.stdout:
            raise Fail("missing obo load segment %s" % addr)
    if 'GNU_STACK' not in ph.stdout or 'RWE' in ph.stdout:
        raise Fail("final stack is not non-executable")
    undef = need(run(['nm', '-u', os.path.join(out, 'app')]), 'nm -u')
    if 'obo_native_' in undef.stdout:
        raise Fail("final executable has an unresolved thunk")
    need(verify(out), 'manifest verify')
    m = json.load(open(os.path.join(out, 'manifest.json')))
    if m.get('signature_check') != 'header':
        raise Fail("scalar signature_check is %r, expected header" % m.get('signature_check'))
    # Freestanding ABI probe: no libc, no dynamic loader; checks the thunk and
    # SysV callee-saved registers directly.
    probe = os.path.join(tmp, 'abi_probe')
    need(run(['gcc', '-nostdlib', '-no-pie', '-o', probe,
              os.path.join(EX, 'abi_probe.S'),
              os.path.join(out, 'shim.o'),
              os.path.join(out, 'src0.o'),
              os.path.join(out, 'src1.o')]), 'abi probe link')
    need(run([probe]), 'abi probe run')
    nm = must(['nm', '-u', probe])
    if nm.stdout.strip():
        raise Fail("freestanding ABI probe has undefined symbols: %s" % nm.stdout.strip())
    return out


def startup(tmp, scalar_out):
    out = os.path.join(tmp, 'runtime')
    need(build(os.path.join(EX, 'scalar.cbind'), os.path.join(EX, 'runtime_app.obx'), out), 'runtime build')
    app = os.path.join(out, 'app')
    for env, label in (({'FOO': 'bar'}, 'odd'), ({'FOO': 'bar', 'BAZ': 'qux'}, 'even')):
        p = need(run([app, 'one', 'two'], env=env), 'runtime %s' % label)
        if 'argc=3' not in p.stdout or 'arg0=' + app not in p.stdout or 'FOO=bar' not in p.stdout:
            raise Fail("runtime %s argv/env mismatch:\n%s" % (label, p.stdout))
    p = need(run([app], env={'FOO': 'bar', 'OBO_STATS': '1'}), 'stats visibility')
    if 'peak bytes' not in p.stderr:
        raise Fail("OBO_STATS was not visible through the environment block")
    leak = os.path.join(tmp, 'leak')
    need(build(os.path.join(EX, 'scalar.cbind'), os.path.join(EX, 'leak_app.obx'), leak), 'leak build')
    p = run([os.path.join(leak, 'app')], env={'FOO': 'bar', 'OBO_CHECK': '1'})
    if p.returncode != 8 or 'file descriptors leaked' not in p.stderr:
        raise Fail("OBO_CHECK was not visible: exit %d, stderr %r" % (p.returncode, p.stderr))
    p = run([os.path.join(leak, 'app')], env={'FOO': 'bar'})
    if p.returncode != 0:
        raise Fail("leak app failed without OBO_CHECK: %d %s" % (p.returncode, p.stderr))
    # Near-limit pointers: 116k very short variables are permitted by ARG_MAX
    # and exceed the checked 1 MiB pointer block; 110k still fits.
    for n, want in ((110000, 0), (116000, 127)):
        env = {('A%05d' % i): 'x' for i in range(n)}
        p = run([app], env=env)
        if want == 0 and p.returncode != 0:
            raise Fail("near-limit fit failed at %d vars: %d %s" % (n, p.returncode, p.stderr))
        if want == 127 and (p.returncode != 127 or 'startup stack overflow' not in p.stderr):
            raise Fail("near-limit overflow did not fail cleanly at %d vars: %d %s"
                       % (n, p.returncode, p.stderr[:200]))


def negative(tmp, scalar_out):
    spec0 = json.load(open(os.path.join(EX, 'scalar.cbind')))
    spec0['sources'] = [os.path.join(EX, 'scalar.c'), os.path.join(EX, 'scalar_abi.S')]
    spec0['headers'] = [os.path.join(EX, 'scalar.h')]

    def write_spec(name, spec):
        path = os.path.join(tmp, name + '.cbind')
        json.dump(spec, open(path, 'w'))
        return path

    # wrong direct arity and extra arguments, both with --native-relocs
    for expr, label in (('__cinterop("add", 1)', 'missing'), ('__cinterop("add", 1, 2, 3)', 'extra')):
        path = os.path.join(tmp, 'direct_%s.obx' % label)
        open(path, 'w').write('module app\n\nfn main() { println(%s) }\n' % expr)
        p = build(os.path.join(EX, 'scalar.cbind'), path, os.path.join(tmp, 'direct_' + label))
        if p.returncode == 0 or "spec says 2" not in (p.stderr + p.stdout):
            raise Fail("direct %s arity was not rejected:\n%s" % (label, p.stderr + p.stdout))
    path = os.path.join(tmp, 'direct_sub.obx')
    open(path, 'w').write('module app\n\nfn main() { println(__cinterop("sum3", 1, 2)) }\n')
    p = build(os.path.join(EX, 'scalar.cbind'), path, os.path.join(tmp, 'direct_sub'))
    if p.returncode == 0 or "spec says 3" not in (p.stderr + p.stdout):
        raise Fail("incompatible symbol substitution was not rejected:\n%s" % (p.stderr + p.stdout))

    # a sidecar symbol substitution that changes the required arity is caught
    import importlib.util
    mspec = importlib.util.spec_from_file_location('cinterop_tool', TOOLPY)
    tool = importlib.util.module_from_spec(mspec)
    mspec.loader.exec_module(tool)
    spec = json.load(open(os.path.join(EX, 'scalar.cbind')))
    relocs = tool.read_relocs(os.path.join(scalar_out, 'native.relocs'))
    for r in relocs:
        if r['sym'] == 'add':
            r['sym'] = 'sum3'
            break
    try:
        tool.validate_calls(relocs, spec)
    except tool.ToolError as e:
        if 'spec says 3' not in str(e):
            raise Fail("unexpected sidecar substitution error: %s" % e)
    else:
        raise Fail("incompatible sidecar symbol substitution was not rejected")

    # direct builtin without the sidecar flag remains refused
    direct = os.path.join(tmp, 'no_flag.obx')
    open(direct, 'w').write('module direct\n\nfn main() { println(__cinterop("add", 1, 2)) }\n')
    p = run([OBO, direct, '-o', os.path.join(tmp, 'no_flag'), '--rt', os.path.dirname(OBO)])
    if p.returncode == 0 or 'native-relocs' not in (p.stderr + p.stdout):
        raise Fail("__cinterop without --native-relocs was not refused")

    # i32 arguments are rejected; i32 returns remain supported and tested above
    bad = dict(spec0)
    bad['module'] = 'bad32'
    bad['functions'] = [{'name': 'bad32', 'symbol': 'bad32', 'args': ['i32'],
                         'ret': 'i64', 'effects': ['impure']}]
    p = build(write_spec('bad32', bad), os.path.join(EX, 'scalar_app.obx'), os.path.join(tmp, 'bad32'))
    if p.returncode == 0 or 'i32 arguments' not in (p.stderr + p.stdout):
        raise Fail("i32 argument was not rejected")

    # no header and no explicit declaration is not an unchecked mode
    bad = dict(spec0)
    bad['module'] = 'nosig'
    bad['sources'] = [os.path.join(EX, 'scalar.c')]
    bad['headers'] = []
    p = build(write_spec('nosig', bad), os.path.join(EX, 'scalar_app.obx'), os.path.join(tmp, 'nosig'))
    if p.returncode == 0 or 'header or explicit declaration' not in (p.stderr + p.stdout):
        raise Fail("a binding without a header/declaration was not rejected")

    # duplicate C symbol
    bad = dict(spec0)
    bad['module'] = 'dup'
    first = dict(spec0['functions'][1])
    second = dict(first)
    second['name'] = 'identity_copy'
    bad['functions'] = [first, second]
    p = build(write_spec('dup', bad), os.path.join(EX, 'scalar_app.obx'), os.path.join(tmp, 'dup'))
    if p.returncode == 0 or 'duplicate C symbol' not in (p.stderr + p.stdout):
        raise Fail("duplicate C symbol was not rejected")

    # variadic declaration fails the C signature check
    bad = dict(spec0)
    bad['module'] = 'var'
    bad['sources'] = []
    bad['headers'] = []
    bad['declarations'] = ['extern long vfun(long, ...);']
    bad['functions'] = [{'name': 'vfun', 'symbol': 'vfun', 'args': ['i64', 'i64'],
                         'ret': 'i64', 'effects': ['impure']}]
    p = build(write_spec('var', bad), os.path.join(EX, 'scalar_app.obx'), os.path.join(tmp, 'var'))
    if p.returncode == 0 or 'incompatible' not in (p.stderr + p.stdout):
        raise Fail("variadic declaration was not rejected by the C signature check")

    # wrong arity against a header
    bad = json.loads(json.dumps(spec0))
    bad['module'] = 'arity'
    for f in bad['functions']:
        if f['name'] == 'add':
            f['args'] = ['i64']
    p = build(write_spec('arity', bad), os.path.join(EX, 'scalar_app.obx'), os.path.join(tmp, 'arity'))
    if p.returncode == 0 or 'incompatible' not in (p.stderr + p.stdout):
        raise Fail("header arity mismatch was not rejected")

    # platform mismatch
    bad = dict(spec0)
    bad['platform'] = 'aarch64-linux-gnu'
    p = build(write_spec('platform', bad), os.path.join(EX, 'scalar_app.obx'), os.path.join(tmp, 'platform'))
    if p.returncode == 0 or 'platform' not in (p.stderr + p.stdout):
        raise Fail("platform mismatch was not rejected")

    # missing definition: declaration is checked, then the link must fail
    bad = dict(spec0)
    bad['module'] = 'missing'
    bad['sources'] = []
    bad['headers'] = []
    bad['declarations'] = ['extern long missing_native(long, long);']
    bad['functions'] = [{'name': 'missing_native', 'symbol': 'missing_native',
                         'args': ['i64', 'i64'], 'ret': 'i64', 'effects': ['impure']}]
    missing_app = os.path.join(tmp, 'missing_app.obx')
    open(missing_app, 'w').write('module app\n\nimport "missing.obx"\n\n'
                                 'fn main() { println(missing::missing_native(1, 2)) }\n')
    p = build(write_spec('missing', bad), missing_app, os.path.join(tmp, 'missing'))
    if p.returncode == 0 or 'undefined reference' not in (p.stderr + p.stdout):
        raise Fail("missing native definition did not fail the link:\n%s" % (p.stderr + p.stdout))

    # --check cannot claim a native module was checked without its typed sidecar
    checksym = os.path.join(tmp, 'checksym')
    os.makedirs(checksym)
    check = run([OBO, '--check', os.path.join(scalar_out, 'hello.obx'),
                 '--rt', os.path.dirname(OBO), '--sym-dir', checksym])
    if check.returncode == 0 or 'not supported in --check' not in (check.stderr + check.stdout):
        raise Fail("--check accepted a native module without its sidecar")

    # conservative native effects: a reference into a global cannot survive a call
    effect = os.path.join(tmp, 'effect_error.obx')
    open(effect, 'w').write(
        'module app\n\nimport "hello.obx"\n\nvar g = new []int(1)\n\n'
        'fn main() {\n    let r: &[]int = &g\n    let n = hello::zero()\n'
        '    println(len(r))\n}\n')
    p = run([OBO, effect, '-o', os.path.join(tmp, 'effect_error'),
             '--rt', os.path.dirname(OBO), '-I', scalar_out,
             '--sym-dir', os.path.join(scalar_out, 'sym'),
             '--native-relocs', os.path.join(tmp, 'effect.relocs')])
    if p.returncode == 0 or 'may point to freed memory' not in (p.stderr + p.stdout):
        raise Fail("native call did not conservatively invalidate a reference:\n%s"
                   % (p.stderr + p.stdout))

    # provenance negatives on a copy of a build made from a copied example tree
    prov_ex = os.path.join(tmp, 'prov_examples')
    shutil.copytree(EX, prov_ex)
    prov = os.path.join(tmp, 'prov')
    need(build(os.path.join(prov_ex, 'scalar.cbind'), os.path.join(prov_ex, 'scalar_app.obx'), prov), 'provenance build')
    for label, target, action in (
        ('header', os.path.join(prov_ex, 'scalar.h'), 'edit'),
        ('relocs', os.path.join(prov, 'native.relocs'), 'delete'),
        ('relocs_edit', os.path.join(prov, 'native.relocs'), 'edit'),
        ('wrappersym', os.path.join(prov, 'sym', 'hello.sym'), 'delete'),
        ('nativeobj', os.path.join(prov, 'native.o'), 'edit'),
    ):
        copy = os.path.join(tmp, 'prov_' + label)
        shutil.copytree(prov, copy)
        # the copied tree's manifest paths point at prov; point them at the copy
        mpath = os.path.join(copy, 'manifest.json')
        m = json.load(open(mpath))
        for k, rel in list(m['paths'].items()):
            m['paths'][k] = os.path.join(copy, rel) if not os.path.isabs(rel) else rel
        json.dump(m, open(mpath, 'w'))
        if label == 'header':
            path = os.path.join(prov_ex, 'scalar.h')
            orig = open(path).read()
            open(path, 'w').write(orig.replace('long add(long a, long b);', 'long add(long a);'))
            try:
                p = verify(copy)
            finally:
                open(path, 'w').write(orig)
        else:
            target_copy = os.path.join(copy, os.path.relpath(target, prov))
            if action == 'edit':
                with open(target_copy, 'ab') as f:
                    f.write(b'x')
            else:
                os.unlink(target_copy)
            p = verify(copy)
        if p.returncode == 0:
            raise Fail("provenance negative %s was not rejected" % label)

    # manifest metadata tamper
    copy = os.path.join(tmp, 'prov_meta')
    shutil.copytree(prov, copy)
    mpath = os.path.join(copy, 'manifest.json')
    m = json.load(open(mpath))
    for k, rel in list(m['paths'].items()):
        m['paths'][k] = os.path.join(copy, rel) if not os.path.isabs(rel) else rel
    m['platform'] = 'aarch64-linux-gnu'
    json.dump(m, open(mpath, 'w'))
    if verify(copy).returncode == 0:
        raise Fail("manifest metadata tamper was not rejected")


def pq(tmp):
    libs = ['/lib/x86_64-linux-gnu/libpq.so.5', '/usr/lib/x86_64-linux-gnu/libpq.so.5']
    if not any(os.path.exists(p) for p in libs):
        print('libpq: blocked (no libpq.so.5 on the declared probe paths)')
        return
    out = os.path.join(tmp, 'pq')
    p = build(os.path.join(EX, 'pq.cbind'), os.path.join(EX, 'pq_app.obx'), out)
    if p.returncode != 0:
        raise Fail("libpq is present but the probe build failed:\n%s" % (p.stderr or p.stdout))
    p = need(run([os.path.join(out, 'app')], env=dict(os.environ, OBO_CHECK='1')), 'pq run')
    if not p.stdout.startswith('PQlibVersion=') or not p.stdout.strip().split('=')[1].isdigit():
        raise Fail("unexpected pq output %r" % p.stdout)
    ldd = need(run(['ldd', os.path.join(out, 'app')]), 'ldd')
    if 'libpq.so.5' not in ldd.stdout:
        raise Fail("final pq artifact does not depend on libpq.so.5")
    m = json.load(open(os.path.join(out, 'manifest.json')))
    if m.get('signature_check') != 'declarations-only':
        raise Fail("pq signature_check is %r, expected declarations-only" % m.get('signature_check'))
    need(verify(out), 'pq manifest verify')
    print('libpq: %s' % p.stdout.strip())


def main():
    os.makedirs(TMPROOT, exist_ok=True)
    tmp = tempfile.mkdtemp(prefix='cinterop-test-', dir=TMPROOT)
    try:
        out = scalar(tmp)
        startup(tmp, out)
        negative(tmp, out)
        pq(tmp)
        print('cinterop tests ok')
        return 0
    except Fail as e:
        print('FAIL: %s' % e, file=sys.stderr)
        return 1
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == '__main__':
    sys.exit(main())
