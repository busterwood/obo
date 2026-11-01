#!/usr/bin/env python3
"""P2q systems-library tests for UDP batch, GSO/GRO and datagram watch.

    python3 obo/experimental/sys/tests/test_sys_p2q.py

Builds the real Linux adapter and the link-time scripted fake, runs the C
batch harness normally and under ASAN/UBSAN/LSAN, compares real/fake obo
contract traces for batch receive/send, GSO fallback, GRO segment reporting,
the 65507-byte boundary, total-budget Range, legacy one-datagram bind behavior,
truncation, peer, capabilities, reusePort and the QUIC bind profile, then
exercises scripted batch/GSO faults.
"""
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.dirname(HERE)
REPO = os.path.dirname(os.path.dirname(os.path.dirname(PKG)))
OBO = os.path.abspath(os.environ.get('OBO', os.path.join(REPO, 'obo', 'obo')))
TOOLPY = os.path.join(REPO, 'obo', 'experimental', 'cinterop', 'cinterop.py')
NATIVE = os.path.join(PKG, 'native')
APPS = os.path.join(HERE, 'apps')
CSRC = os.path.join(HERE, 'c')
REAL = os.path.join(NATIVE, 'sysadapter_real.cbind')
FAKE = os.path.join(NATIVE, 'sysadapter_fake.cbind')
FAKE_CTL = os.path.join(NATIVE, 'sysadapter_fake_ctl.cbind')
TMPROOT = '/tmp/opencode'


class Fail(Exception):
    pass


def run(cmd, **kw):
    timeout = kw.pop('timeout', 300)
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, **kw)


def must(cmd, label, **kw):
    p = run(cmd, **kw)
    if p.returncode != 0:
        raise Fail("%s failed:\n%s\n%s" % (label, ' '.join(cmd), (p.stderr or p.stdout).strip()))
    return p


def build(spec, app, out, label):
    os.makedirs(out, exist_ok=True)
    must([sys.executable, TOOLPY, 'build', spec, app, '--out', out, '--obo', OBO], label)
    must([sys.executable, TOOLPY, 'verify', os.path.join(out, 'manifest.json')],
         label + ' manifest verify')
    return os.path.join(out, 'app')


def run_app(path, label, argv):
    p = run([path] + list(argv), env=dict(os.environ, OBO_CHECK='1'), timeout=180)
    if p.returncode != 0:
        raise Fail("%s exited %d:\n%s\n%s" % (label, p.returncode, p.stdout, p.stderr))
    return p.stdout.strip().splitlines()


def exact(label, lines, want):
    if lines != want:
        raise Fail("%s output mismatch:\n--- got ---\n%s\n--- want ---\n%s"
                   % (label, '\n'.join(lines), '\n'.join(want)))


def cc_compile(name, src, extra_sources, extra_flags, out_dir):
    cc = shutil.which(os.environ.get('CC', 'cc'))
    if not cc:
        raise Fail('C compiler not found')
    exe = os.path.join(out_dir, name)
    cmd = [cc, '-std=c11', '-Wall', '-Wextra', '-Werror', *extra_flags,
           '-I', NATIVE, os.path.join(CSRC, src)]
    cmd += [os.path.join(NATIVE, s) for s in extra_sources]
    cmd += ['-o', exe]
    must(cmd, 'compile ' + name)
    return exe


TRACE = [
    'init=true',
    'opts=true,true,true',
    'caps=true',
    'local=true',
    'batch_send=true',
    'batch_count=true',
    'batch_total=300',
    'batch_segments=3',
    'batch_segment_ok=true',
    'batch_budget=true,300',
    'batch_budget_ok=true',
    'batch_range=Range',
    'single_trunc=true,300,10',
    'legacy_recv=100,100,0',
    'legacy_send5000=true',
    'gro_off_send=true',
    'gro_off_count=3',
    'gro_off_plain=true',
    'caps_off=false',
    'gso_off=Unsupported',
    'quic_reuse=InvalidArgument',
    'quic_wildcard=InvalidArgument',
    'quic_unix=Unsupported',
    'reuse_second=true',
    'empty_batch=WouldBlock',
    'bad_max=Range',
    'bad_bytes=Range',
    'bad_seg=Range',
    'bad_seg_max=Range',
    'bad_range=InvalidArgument',
    'bad_total=Range',
    'bad_fallback=Range',
    'bad_fallback_seg=Range',
    'fallback_clamp=true',
    'bad_buffer=Range',
    'counts=0,0',
    'shutdown=0,0,0',
]

FAULT = [
    'init=true',
    'fake_reset=1',
    'listen_fail=ResourceLimit',
    'write_fail=WouldBlock',
    'gso_reject=Unsupported',
    'gso_caps_after=false',
    'gso_after=Unsupported',
    'read_fail=WouldBlock',
    'gso_off=Unsupported',
    'fallback_ok=300',
    'reuse_conflict=AddressInUse',
    'counts=0,0',
    'shutdown=0,0,0',
]


def main():
    if not os.path.isfile(OBO):
        raise Fail("compiler not found: %s (run `make -C obo obo`)" % OBO)
    os.makedirs(TMPROOT, exist_ok=True)
    tmp = tempfile.mkdtemp(prefix='sys-p2q-', dir=TMPROOT)
    print('sys P2q workdir:', tmp)

    # C batch harness: normal and ASAN/UBSAN/LSAN, real and fake.
    native = cc_compile('p2q_c_real', 'p2q_test.c',
                        ['sysregistry.c', 'sysadapter_real.c', 'sysnet_real.c'],
                        ['-O2'], tmp)
    fake = cc_compile('p2q_c_fake', 'p2q_test.c',
                      ['sysregistry.c', 'sysadapter_fake.c', 'sysnet_fake.c'],
                      ['-O2'], tmp)
    for exe, label in ((native, 'p2q_c_real'), (fake, 'p2q_c_fake')):
        p = must([exe], label)
        print(p.stdout.strip())
    san_flags = ['-O1', '-g', '-fsanitize=address,undefined',
                 '-fno-omit-frame-pointer']
    native_san = cc_compile('p2q_c_real_san', 'p2q_test.c',
                            ['sysregistry.c', 'sysadapter_real.c', 'sysnet_real.c'],
                            san_flags, tmp)
    fake_san = cc_compile('p2q_c_fake_san', 'p2q_test.c',
                          ['sysregistry.c', 'sysadapter_fake.c', 'sysnet_fake.c'],
                          san_flags, tmp)
    env = dict(os.environ, ASAN_OPTIONS='detect_leaks=1')
    for exe, label in ((native_san, 'p2q_c_real_san'),
                       (fake_san, 'p2q_c_fake_san')):
        p = must([exe], label, env=env)
        print(label + ': ' + p.stdout.strip())

    # Real/fake conformance roots use the same private arguments.
    real_app = build(REAL, os.path.join(APPS, 'p2q_app.obx'),
                     os.path.join(tmp, 'real-conformance'), 'real conformance')
    real_out = run_app(real_app, 'real conformance', [])
    fake_app = build(FAKE, os.path.join(APPS, 'p2q_app.obx'),
                     os.path.join(tmp, 'fake-conformance'), 'fake conformance')
    fake_out = run_app(fake_app, 'fake conformance', [])
    exact('real conformance', real_out, TRACE)
    exact('fake conformance', fake_out, TRACE)
    if real_out != fake_out:
        raise Fail('real/fake conformance traces differ')

    # Scripted batch faults through the fake control surface.
    fault_app = build(FAKE_CTL, os.path.join(APPS, 'p2q_fault_app.obx'),
                      os.path.join(tmp, 'fake-fault'), 'fake fault')
    fault_out = run_app(fault_app, 'fake fault', [])
    exact('fake fault', fault_out, FAULT)

    print('sys P2q: all tests passed')


if __name__ == '__main__':
    try:
        main()
    except Fail as e:
        print('FAIL: %s' % e, file=sys.stderr)
        sys.exit(1)
