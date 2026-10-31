#!/usr/bin/env python3
"""P0 boundary tests for obo/experimental/sys.

    python3 obo/experimental/sys/tests/test_sys_p0.py

Builds the real Linux adapter and the link-time scripted fake with the existing
experimental cinterop tool, runs obo roots and C harnesses, compares the two
backends' contract traces, checks adversarial identity/lifecycle behavior, runs
ASAN/UBSAN C harnesses where available and records the byte-bridge benchmark.
This is not part of the public compiler test catalog.
"""
import os
import shutil
import subprocess
import sys
import tempfile
import time

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


def run_app(path, label, argv=None):
    cmd = [path] + list(argv or [])
    p = run(cmd, env=dict(os.environ, OBO_CHECK='1'), timeout=60)
    if p.returncode != 0:
        raise Fail("%s exited %d:\n%s\n%s" % (label, p.returncode, p.stdout, p.stderr))
    return p.stdout.strip().splitlines()


def negative_check(label, fixture, out_dir):
    p = run([OBO, '--check', os.path.join(APPS, '..', 'errors', fixture),
             '-I', out_dir, '-I', os.path.join(APPS, '..', 'errors'),
             '--sym-dir', os.path.join(out_dir, 'sym')])
    if p.returncode == 0:
        raise Fail('%s: compiler accepted a negative fixture' % label)
    expect_path = os.path.join(APPS, '..', 'errors', fixture.replace('.obx', '.expect'))
    want = open(expect_path).read().strip()
    if want not in p.stderr:
        raise Fail('%s: diagnostic mismatch\n--- got ---\n%s\n--- want ---\n%s'
                   % (label, p.stderr.strip(), want))
    print('negative %s: %s' % (label, want))


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


CONFORMANCE = [
    'init=true',
    'source=ok',
    'write=true',
    'read=5',
    'read_empty=WouldBlock',
    'write_zero=InvalidArgument',
    'close=true',
    'double_close=false',
    'length=32',
    'fill=32',
    'get0=171',
    'copy_in=5',
    'copy_out=5 nul=0 ff=255',
    'copy_in_scalar=5',
    'copy_out_scalar=5 nul=0',
    'packed_write=4',
    'packed_read=67305985',
    'packed_max1=1',
    'packed_over1=Range',
    'packed_max2=2',
    'packed_over2=Range',
    'packed_max7=7',
    'packed_over7=Range',
    'packed_edge7=Range',
    'packed_oob=Range',
    'buffer_close=true',
    'oversize=Range',
    'forged=InvalidResource',
    'counts=0,0,0,0',
    'shutdown=0,0,0,0',
]

ADVERSARIAL = [
    'init=true',
    'future_before=InvalidResource',
    'out_of_range_before=InvalidResource',
    'live_open=ok',
    'future_after_populate=InvalidResource',
    'wrong_nonce=InvalidResource',
    'zero_shell=InvalidResource',
    'live_source=ok',
    'wrong_kind=InvalidResource',
    'copied_hint_live=ok',
    'wrong_kind_close=InvalidResource',
    'close=true',
    'double_close=false',
    'closed_read=Closed',
    'out_of_range_after_populate=InvalidResource',
    'orphan_owner_dead=InvalidResource',
    'counts=1,11',
    'shutdown=1,11',
]

TABLE = [
    'init1=true',
    'a=ok',
    'b=ResourceLimit',
    'after_close=ok',
    'shell_cap=LimitExceeded',
    'shutdown1=0,0',
    'init2=true',
    'buffer8=ok',
    'quota=NoSpace',
    'after_release=ok',
    'oversize=Range',
    'negative=Range',
    'zero=ok',
    'zero_len=0',
    'shutdown2=0,0',
    'init3=true',
    'tombstone_close=true',
    'tombstone_open=LimitExceeded',
    'after_drop_open=ok',
    'shutdown3=0,0',
    'init4=true',
    'exact16=ok',
    'exact16_len=16777216',
    'past16=Range',
    'shutdown4=0,0',
]

INIT_ORDER = [
    'cap_before=false',
    'counts_before=NotInitialized',
    'open_before=NotInitialized',
    'shutdown_before=NotInitialized',
    'invalid_options=InvalidArgument',
    'init1=true',
    'cap_during=true unknown=false',
    'init_same=false',
    'init_other=Busy',
    'shutdown1=0,1,0',
    'close_after_shutdown=false',
    'shutdown2=0,1,0',
    'cap_after=false',
    'counts_after=NotInitialized',
    'open_after=NotInitialized',
    'init_again=true',
    'close_after_reinit=false',
    'old_shell=InvalidResource',
    'fresh_open=ok',
    'shutdown3=0,0',
]

LEAK = [
    'init=true',
    'closed_a=true',
    'opened_b=1',
    'dropped_call=ok',
    'counts_before_shutdown=2,2,0,0',
    'shutdown_report=2,2,0,0',
    'shutdown_cached=2,2,0,0',
]

FAULT = [
    'init=true',
    'fake_reset=1',
    'push_new=1',
    'scripted_open=NoMemory retryable=false',
    'new_calls=1',
    'push_eintr=1',
    'scripted_write=Interrupted retryable=true',
    'interrupted=true would_block=false',
    'op_calls_after_eintr=1',
    'push_eagain=1',
    'scripted_read=WouldBlock retryable=true',
    'would_block=true interrupted=false',
    'op_calls_after_eagain=1',
    'push_progress=1',
    'progress=3',
    'op_calls_after_progress=1',
    'push_close_eintr=1',
    'scripted_close=Interrupted retryable=true',
    'close_again=false',
    'counts_after_close=0,1,1',
    'errno75=ResourceLimit',
    'errno122=NoSpace',
    'set_time=1 now=1234',
    'shutdown=0,0,0,1',
]


def main():
    if not os.path.isfile(OBO):
        raise Fail("compiler not found: %s (run `make -C obo obo`)" % OBO)
    os.makedirs(TMPROOT, exist_ok=True)
    tmp = tempfile.mkdtemp(prefix='sys-p0-', dir=TMPROOT)
    print('sys P0 workdir:', tmp)

    # C harnesses first: they need no obo compiler.
    native = cc_compile('native_test', 'native_test.c',
                        ['sysregistry.c', 'sysadapter_real.c'], ['-O2'], tmp)
    fake = cc_compile('fake_test', 'fake_test.c',
                      ['sysregistry.c', 'sysadapter_fake.c'], ['-O2'], tmp)
    for exe, label in ((native, 'native_test'), (fake, 'fake_test')):
        p = must([exe], label)
        print(p.stdout.strip())

    # ASAN/UBSAN C harnesses (hosted fixed-address obo images are not used here,
    # so the sanitizer can map freely).
    san_flags = ['-O1', '-g', '-fsanitize=address,undefined', '-fno-omit-frame-pointer']
    native_san = cc_compile('native_test_san', 'native_test.c',
                            ['sysregistry.c', 'sysadapter_real.c'], san_flags, tmp)
    fake_san = cc_compile('fake_test_san', 'fake_test.c',
                          ['sysregistry.c', 'sysadapter_fake.c'], san_flags, tmp)
    env = dict(os.environ, ASAN_OPTIONS='detect_leaks=1')
    for exe, label in ((native_san, 'native_test_san'), (fake_san, 'fake_test_san')):
        p = must([exe], label, env=env)
        print(label + ': ' + p.stdout.strip())

    # Bridge microbenchmark (C exact path).
    bench = cc_compile('bench_copy', 'bench_copy.c',
                       ['sysregistry.c', 'sysadapter_real.c'], ['-O2'], tmp)
    bp = must([bench], 'bench_copy')
    print('bridge benchmark: ' + bp.stdout.strip())
    if 'scalar_in=' not in bp.stdout or 'packed_in=' not in bp.stdout:
        raise Fail('benchmark did not report throughput')

    # Real backend roots.
    real_conformance = build(REAL, os.path.join(APPS, 'conformance_app.obx'),
                             os.path.join(tmp, 'real-conformance'), 'real conformance')
    real_conf_out = run_app(real_conformance, 'real conformance')
    real_conf_dir = os.path.dirname(real_conformance)
    negative_check('file-to-stream', 'file_to_stream.obx', real_conf_dir)
    negative_check('file-to-resource', 'file_to_resource.obx', real_conf_dir)

    build(REAL, os.path.join(APPS, 'adversarial_app.obx'),
          os.path.join(tmp, 'real-adversarial'), 'real adversarial')
    real_adv_out = run_app(os.path.join(tmp, 'real-adversarial', 'app'), 'real adversarial')

    build(REAL, os.path.join(APPS, 'table_app.obx'),
          os.path.join(tmp, 'real-table'), 'real table')
    real_table_out = run_app(os.path.join(tmp, 'real-table', 'app'), 'real table')

    build(REAL, os.path.join(APPS, 'init_order_app.obx'),
          os.path.join(tmp, 'real-init'), 'real init order')
    real_init_out = run_app(os.path.join(tmp, 'real-init', 'app'), 'real init order')

    build(REAL, os.path.join(APPS, 'leak_app.obx'),
          os.path.join(tmp, 'real-leak'), 'real leak')
    real_leak_out = run_app(os.path.join(tmp, 'real-leak', 'app'), 'real leak')

    # Fake backend roots: same contract traces.
    fake_conformance = build(FAKE, os.path.join(APPS, 'conformance_app.obx'),
                             os.path.join(tmp, 'fake-conformance'), 'fake conformance')
    fake_conf_out = run_app(fake_conformance, 'fake conformance')

    build(FAKE, os.path.join(APPS, 'adversarial_app.obx'),
          os.path.join(tmp, 'fake-adversarial'), 'fake adversarial')
    fake_adv_out = run_app(os.path.join(tmp, 'fake-adversarial', 'app'), 'fake adversarial')

    build(FAKE, os.path.join(APPS, 'table_app.obx'),
          os.path.join(tmp, 'fake-table'), 'fake table')
    fake_table_out = run_app(os.path.join(tmp, 'fake-table', 'app'), 'fake table')

    build(FAKE, os.path.join(APPS, 'init_order_app.obx'),
          os.path.join(tmp, 'fake-init'), 'fake init order')
    fake_init_out = run_app(os.path.join(tmp, 'fake-init', 'app'), 'fake init order')

    exact('real conformance', real_conf_out, CONFORMANCE)
    exact('fake conformance', fake_conf_out, CONFORMANCE)
    if real_conf_out != fake_conf_out:
        raise Fail('real/fake conformance traces differ')
    exact('real adversarial', real_adv_out, ADVERSARIAL)
    exact('fake adversarial', fake_adv_out, ADVERSARIAL)
    if real_adv_out != fake_adv_out:
        raise Fail('real/fake adversarial traces differ')
    exact('real table', real_table_out, TABLE)
    exact('fake table', fake_table_out, TABLE)
    exact('real init order', real_init_out, INIT_ORDER)
    exact('fake init order', fake_init_out, INIT_ORDER)
    exact('real leak', real_leak_out, LEAK)

    # Scripted fake fault injection and one-call-per-operation proof.
    fault = build(FAKE_CTL, os.path.join(APPS, 'fault_app.obx'),
                  os.path.join(tmp, 'fake-fault'), 'fake fault')
    exact('fake fault', run_app(fault, 'fake fault'), FAULT)

    # obo-level benchmark: packed vs scalar bridge through the public wrapper.
    bench_app = build(REAL, os.path.join(APPS, 'bench_app.obx'),
                      os.path.join(tmp, 'real-bench'), 'real bench')
    results = {}
    for mode in ('packed', 'scalar'):
        t0 = time.perf_counter()
        out = run_app(bench_app, 'bench ' + mode, [mode])
        elapsed = time.perf_counter() - t0
        if out[0] != 'copy_in=1048576' or out[1] != 'copy_out=1048576':
            raise Fail('bench %s copy counts: %r' % (mode, out))
        if 'mode=%s sum=131064401' % mode not in out[2]:
            raise Fail('bench %s checksum: %r' % (mode, out))
        throughput = (2.0 * 1048576.0) / elapsed / (1024.0 * 1024.0)
        results[mode] = throughput
        print('obo bridge %s: %.1f MiB/s (whole process, %.3fs)' % (mode, throughput, elapsed))
    if results['packed'] <= 0 or results['scalar'] <= 0:
        raise Fail('nonpositive obo benchmark')

    print('sys P0: all tests passed')


if __name__ == '__main__':
    try:
        main()
    except Fail as e:
        print('FAIL: %s' % e, file=sys.stderr)
        sys.exit(1)
