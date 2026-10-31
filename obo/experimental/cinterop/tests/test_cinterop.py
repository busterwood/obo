#!/usr/bin/env python3
"""Targeted tests for the experimental cinterop prototype.

    python3 obo/experimental/cinterop/tests/test_cinterop.py

This is not part of the public compiler test catalog. It builds a real scalar
module end to end, checks the ABI/alignment assertions, runs negative artifact
tests, and conditionally builds the PQlibVersion probe when the host has the
libpq shared library. No database connection is made.
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
    return subprocess.run(cmd, capture_output=True, text=True, timeout=180, **kw)


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


def scalar(tmp):
    out = os.path.join(tmp, 'scalar')
    need(build(os.path.join(EX, 'scalar.cbind'), os.path.join(EX, 'scalar_app.obx'), out), 'scalar build')
    p = need(run([os.path.join(out, 'app')], env=dict(os.environ, OBO_CHECK='1')), 'scalar run')
    if p.stdout.strip() != 'scalar ok':
        raise Fail("scalar output was %r" % p.stdout.strip())
    elf = need(run(['readelf', '-hW', os.path.join(out, 'app')]), 'readelf -h')
    if 'Type:' not in elf.stdout or 'EXEC' not in elf.stdout:
        raise Fail("scalar artifact is not ET_EXEC")
    ph = need(run(['readelf', '-lW', os.path.join(out, 'app')]), 'readelf -l')
    for addr in ('0x0000000000401000', '0x0000000008000000', '0x000000000c000000',
                 '0x0000000010000000', '0x0000000020000000'):
        if addr not in ph.stdout:
            raise Fail("missing obo load segment %s" % addr)
    undef = need(run(['nm', '-u', os.path.join(out, 'app')]), 'nm -u')
    if 'obo_native_' in undef.stdout:
        raise Fail("final executable has an unresolved thunk")
    need(run([sys.executable, TOOLPY, 'verify', os.path.join(out, 'manifest.json')]), 'manifest verify')
    # Freestanding ABI probe: no libc, no dynamic loader; checks the thunk and
    # SysV callee-saved registers directly.
    probe = os.path.join(tmp, 'abi_probe')
    need(run(['gcc', '-nostdlib', '-no-pie', '-o', probe,
              os.path.join(EX, 'abi_probe.S'),
              os.path.join(out, 'shim.o'),
              os.path.join(out, 'src0.o'),
              os.path.join(out, 'src1.o')]), 'abi probe link')
    need(run([probe]), 'abi probe run')
    return out


def negative(tmp, scalar_out):
    # wrong arity against a real header: the generated C signature check fails before execution
    spec = json.load(open(os.path.join(EX, 'scalar.cbind')))
    spec['sources'] = [os.path.join(EX, 'scalar.c'), os.path.join(EX, 'scalar_abi.S')]
    spec['headers'] = [os.path.join(EX, 'scalar.h')]
    spec['functions'] = [dict(f) for f in spec['functions']]
    for f in spec['functions']:
        if f['name'] == 'add':
            f['args'] = ['i64']
    bad = os.path.join(tmp, 'wrong-arity.cbind')
    json.dump(spec, open(bad, 'w'))
    p = build(bad, os.path.join(EX, 'scalar_app.obx'), os.path.join(tmp, 'wrong-arity'))
    if p.returncode == 0 or 'incompatible' not in (p.stderr + p.stdout):
        raise Fail("wrong arity did not fail at the C signature check:\n%s" % (p.stderr + p.stdout))

    # platform mismatch
    spec2 = json.load(open(os.path.join(EX, 'scalar.cbind')))
    spec2['platform'] = 'aarch64-linux-gnu'
    bad2 = os.path.join(tmp, 'platform.cbind')
    json.dump(spec2, open(bad2, 'w'))
    p = build(bad2, os.path.join(EX, 'scalar_app.obx'), os.path.join(tmp, 'platform'))
    if p.returncode == 0 or 'platform' not in (p.stderr + p.stdout):
        raise Fail("platform mismatch was not rejected")

    # a declared but missing C definition is a link failure, not a runtime lookup
    spec_missing = json.load(open(os.path.join(EX, 'scalar.cbind')))
    spec_missing['module'] = 'missing'
    spec_missing['sources'] = []
    spec_missing['headers'] = []
    spec_missing['declarations'] = ['extern long missing_native(long, long);']
    spec_missing['functions'] = [
        {'name': 'missing_native', 'symbol': 'missing_native', 'args': ['i64', 'i64'],
         'ret': 'i64', 'effects': ['impure']}
    ]
    missing_spec = os.path.join(tmp, 'missing.cbind')
    json.dump(spec_missing, open(missing_spec, 'w'))
    missing_app = os.path.join(tmp, 'missing_app.obx')
    open(missing_app, 'w').write('module app\n\nimport "missing.obx"\n\n'
                                 'fn main() { println(missing::missing_native(1, 2)) }\n')
    p = build(missing_spec, missing_app, os.path.join(tmp, 'missing'))
    if p.returncode == 0 or 'undefined reference' not in (p.stderr + p.stdout):
        raise Fail("missing native definition did not fail the link:\n%s" % (p.stderr + p.stdout))

    # direct use of the experimental builtin without the link sidecar is refused
    direct = os.path.join(tmp, 'direct.obx')
    open(direct, 'w').write('module direct\n\nfn main() { println(cinterop("add", 1, 2)) }\n')
    p = run([OBO, direct, '-o', os.path.join(tmp, 'direct'), '--rt', os.path.dirname(OBO)])
    if p.returncode == 0 or 'native-relocs' not in (p.stderr + p.stdout):
        raise Fail("cinterop without --native-relocs was not refused")

    # modified native object
    shutil.copytree(scalar_out, os.path.join(tmp, 'modified'))
    with open(os.path.join(tmp, 'modified', 'native.o'), 'ab') as f:
        f.write(b'x')
    p = run([sys.executable, TOOLPY, 'verify', os.path.join(tmp, 'modified', 'manifest.json')])
    if p.returncode == 0 or 'hash' not in (p.stderr + p.stdout):
        raise Fail("modified native object was not rejected")

    # stale manifest after a wrapper edit
    shutil.copytree(scalar_out, os.path.join(tmp, 'stale'))
    with open(os.path.join(tmp, 'stale', 'hello.obx'), 'a') as f:
        f.write('\n')
    p = run([sys.executable, TOOLPY, 'verify', os.path.join(tmp, 'stale', 'manifest.json')])
    if p.returncode == 0 or 'hash' not in (p.stderr + p.stdout):
        raise Fail("stale manifest was not rejected")


def pq(tmp):
    libs = ['/lib/x86_64-linux-gnu/libpq.so.5', '/usr/lib/x86_64-linux-gnu/libpq.so.5']
    if not any(os.path.exists(p) for p in libs):
        print('libpq: blocked (no libpq.so.5 on the declared probe paths)')
        return
    out = os.path.join(tmp, 'pq')
    p = build(os.path.join(EX, 'pq.cbind'), os.path.join(EX, 'pq_app.obx'), out)
    if p.returncode != 0:
        print('libpq: blocked (build failed: %s)' % (p.stderr.strip().splitlines()[-1] if p.stderr else 'unknown'))
        return
    p = need(run([os.path.join(out, 'app')], env=dict(os.environ, OBO_CHECK='1')), 'pq run')
    if not p.stdout.startswith('PQlibVersion='):
        raise Fail("unexpected pq output %r" % p.stdout)
    ldd = need(run(['ldd', os.path.join(out, 'app')]), 'ldd')
    if 'libpq.so.5' not in ldd.stdout:
        raise Fail("final pq artifact does not depend on libpq.so.5")
    print('libpq: %s' % p.stdout.strip())


def main():
    os.makedirs(TMPROOT, exist_ok=True)
    tmp = tempfile.mkdtemp(prefix='cinterop-test-', dir=TMPROOT)
    try:
        out = scalar(tmp)
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
