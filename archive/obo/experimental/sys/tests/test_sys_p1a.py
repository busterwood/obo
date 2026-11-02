#!/usr/bin/env python3
"""P1a systems-library tests for obo/experimental/sys.

    python3 obo/experimental/sys/tests/test_sys_p1a.py

Builds the real Linux adapter and the link-time scripted fake with the existing
experimental cinterop tool, runs C harnesses (normal and ASAN/UBSAN), compares
real/fake obo contract traces, exercises scripted faults, bounded buffers and a
bounded file-copy example. P0 remains in test_sys_p0.py.
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


def run_app(path, label, argv, cwd=None):
    p = run([path] + list(argv), env=dict(os.environ, OBO_CHECK='1'), timeout=120, cwd=cwd)
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


CONFORMANCE = [
    'init=true',
    'caps=true,true,false',
    'remaining=50',
    'remaining_zero=0',
    'remaining_inf=nil',
    'elapsed=100',
    'elapsed_reverse=InvalidArgument',
    'after_overflow=Range',
    'duration_negative=InvalidArgument',
    'duration_overflow=Range',
    'sleep_inf=InvalidArgument',
    'sleep0=true',
    'monotonic_nonneg=true',
    'realtime_nanos_ok=true',
    'random_len=16',
    'random_fill=8',
    'random_big=LimitExceeded',
    'random_range=InvalidArgument',
    'random_zero_len=0',
    'random_zero_fill=0',
    'opt_none=InvalidArgument',
    'opt_create_no_write=InvalidArgument',
    'opt_excl_no_create=InvalidArgument',
    'opt_trunc_append=InvalidArgument',
    'opt_perm=InvalidArgument',
    'write=11',
    'seek0=0',
    'read1=hello',
    'read2= world',
    'read3=nil',
    'seek_negative=Range',
    'seek_overflow=Range',
    'seek=0',
    'writeat=5',
    'readat=hello WORLD',
    'truncate=true',
    'meta=8,5,384,1',
    'identity_same=true',
    'back=hello',
    'dup_seek=2',
    'dup_read=llo',
    'dup_at=hello',
    'dup_close=true',
    'file_close=true',
    'into_read=hello',
    'into_old_close=InvalidResource',
    'into_close=true',
    'separate_read=he',
    'separate_read2=he',
    'separate_identity=true',
    'separate_close_a=true',
    'separate_close_b=true',
    'append_writeat=InvalidArgument',
    'append_writeat_zero=InvalidArgument',
    'append_writesome_zero=0',
    'append_write=2',
    'append_read=XY',
    'append_close=true',
    'forged=InvalidResource',
    'wrong_kind=InvalidResource',
    'wrong_kind_close=InvalidResource',
    'wrong_kind_bytes_close=true',
    'stale_source_close=true',
    'stale=Closed',
    'stale_close=false',
    'dir_names=true,true,true,true',
    'dir_iter_close=true',
    'dir_meta=8,0,8,0',
    'dir_identity_same=false',
    'dir_file_identity=true',
    'alpha_close=true',
    'dir_bad_name=InvalidArgument',
    'dir_child=NotFound',
    'dir_close=true',
    'buf_feed=true',
    'buf_find=1',
    'buf_copy=ab',
    'buf_consume=true',
    'buf_feed2=true',
    'buf_over=LimitExceeded',
    'buf_empty_sep=InvalidArgument',
    'counts=0,0',
    'shutdown=0,0,0',
]

BUFFER = [
    'init=true',
    'feed1=true',
    'find=1',
    'copy=ab',
    'consume=true',
    'feed2=true',
    'copy2=cdefgh',
    'find2=5',
    'over=LimitExceeded',
    'consume_beyond=Range',
    'empty_sep=InvalidArgument',
    'zero_feed_empty=true',
    'zero_feed=LimitExceeded',
    'neg_make=Range',
    'huge_make=Range',
    'exact16=ok len=16777216 avail=0',
    'bad_end=InvalidArgument',
    'bad_order=InvalidArgument',
    'bad_limit=InvalidArgument',
    'bad_len=InvalidArgument',
    'writer_feed=true',
    'write_step=3 pending=0',
    'write_step_empty=0',
    'write_exact=true done=5',
    'write_exact_done=true done=5',
    'write_close=true',
    'read_exact=true',
    'content=abchello',
    'read_exact_eof=EndOfInput',
    'read_close=true',
    'step1=4',
    'step1_data=abch',
    'step2_full=LimitExceeded',
    'step_consume=true',
    'step3=4',
    'step3_data=ello',
    'step3_consume=true',
    'step_eof=0 eof=true',
    'reader_close=true',
    'source_write=19',
    'source_seek=0',
    'copy_total=19',
    'copy_dst_close=true',
    'copy_src_close=true',
    'copy_verify=true',
    'copy_content=The quick brown fox',
    'copy_verify_close=true',
    'big_feed=true',
    'big_step1=1048576 pending=1048576',
    'big_step2=1048576 pending=0',
    'big_step3=0',
    'big_close=true',
    'big_size=2097152',
    'big_file_close=true',
    'counts=0,0',
    'shutdown=0,0,0',
]

FAULT = [
    'init=true',
    'fake_reset=1',
    'scripted_open=ResourceLimit',
    'file_open_calls=1',
    'scripted_enoent=NotFound',
    'file_open_calls2=2',
    'read_eintr=Interrupted',
    'read_calls_eintr=1',
    'read_eagain=WouldBlock',
    'read_calls_eagain=2',
    'read_eof=nil',
    'write_short=2',
    'write_calls_short=1',
    'write_nomem=NoMemory',
    'write_calls_nomem=2',
    'stat_nomem=NoMemory',
    'stat_calls=1',
    'close_eintr=Interrupted',
    'close_again=false',
    'dir_enoent=NotFound',
    'dir_open_calls=1',
    'random_eagain=WouldBlock',
    'random_calls=1',
    'sleep_eintr=Interrupted',
    'sleep_retry=true',
    'fake_time=1244',
    'errno_map=ResourceLimit,ResourceLimit,NoMemory,NotFound',
    'counts_resources=0',
    'shutdown=0,1',
]


def prepare_conf(paths):
    conf_dir = os.path.join(paths, 'conf-dir')
    os.makedirs(conf_dir, exist_ok=True)
    for name in ('alpha', 'beta'):
        p = os.path.join(conf_dir, name)
        if not os.path.exists(p):
            open(p, 'wb').close()
    return conf_dir


def clean_files(*names):
    for name in names:
        try:
            os.unlink(name)
        except FileNotFoundError:
            pass


def main():
    if not os.path.isfile(OBO):
        raise Fail("compiler not found: %s (run `make -C obo obo`)" % OBO)
    os.makedirs(TMPROOT, exist_ok=True)
    tmp = tempfile.mkdtemp(prefix='sys-p1a-', dir=TMPROOT)
    print('sys P1a workdir:', tmp)

    # C harnesses: normal and ASAN/UBSAN, real and fake.
    native = cc_compile('p1a_c_real', 'p1a_test.c',
                        ['sysregistry.c', 'sysadapter_real.c', 'sysproc_real.c', 'sysnet_real.c'], ['-O2'], tmp)
    fake = cc_compile('p1a_c_fake', 'p1a_test.c',
                      ['sysregistry.c', 'sysadapter_fake.c', 'sysnet_fake.c'], ['-O2'], tmp)
    for exe, label, arg in ((native, 'p1a_c_real', 'real'), (fake, 'p1a_c_fake', 'fake')):
        p = must([exe, arg], label)
        print(p.stdout.strip())
    san_flags = ['-O1', '-g', '-fsanitize=address,undefined', '-fno-omit-frame-pointer']
    native_san = cc_compile('p1a_c_real_san', 'p1a_test.c',
                            ['sysregistry.c', 'sysadapter_real.c', 'sysproc_real.c', 'sysnet_real.c'], san_flags, tmp)
    fake_san = cc_compile('p1a_c_fake_san', 'p1a_test.c',
                          ['sysregistry.c', 'sysadapter_fake.c', 'sysnet_fake.c'], san_flags, tmp)
    env = dict(os.environ, ASAN_OPTIONS='detect_leaks=1')
    for exe, label, arg in ((native_san, 'p1a_c_real_san', 'real'),
                            (fake_san, 'p1a_c_fake_san', 'fake')):
        p = must([exe, arg], label, env=env)
        print(label + ': ' + p.stdout.strip())

    conf_dir = prepare_conf(tmp)
    conf_file = os.path.join(tmp, 'conf-file')
    alpha_path = os.path.join(conf_dir, 'alpha')

    # Real conformance root.
    real_conf = build(REAL, os.path.join(APPS, 'p1a_app.obx'),
                      os.path.join(tmp, 'real-conformance'), 'real conformance')
    clean_files(conf_file)
    real_conf_out = run_app(real_conf, 'real conformance', [conf_file, conf_dir, alpha_path])

    # Fake conformance root: same contract trace.
    fake_conf = build(FAKE, os.path.join(APPS, 'p1a_app.obx'),
                      os.path.join(tmp, 'fake-conformance'), 'fake conformance')
    clean_files(conf_file)
    fake_conf_out = run_app(fake_conf, 'fake conformance', [conf_file, conf_dir, alpha_path])

    exact('real conformance', real_conf_out, CONFORMANCE)
    exact('fake conformance', fake_conf_out, CONFORMANCE)
    if real_conf_out != fake_conf_out:
        raise Fail('real/fake conformance traces differ')

    # Anchored child must resolve against the base directory, not process cwd.
    base = os.path.join(tmp, 'child-base')
    os.makedirs(os.path.join(base, 'sub'), exist_ok=True)
    elsewhere = os.path.join(tmp, 'child-cwd')
    os.makedirs(elsewhere, exist_ok=True)
    child_expected = [
        'init=true',
        'child=true',
        'child_empty=true',
        'child_iter_close=true',
        'child_close=true',
        'missing=NotFound',
        'dir_close=true',
        'shutdown=0,0',
    ]
    for spec, label in ((REAL, 'real child'), (FAKE, 'fake child')):
        exe = build(spec, os.path.join(APPS, 'p1a_child_app.obx'),
                    os.path.join(tmp, label.replace(' ', '-')), label)
        out = run_app(exe, label, [base], cwd=elsewhere)
        exact(label, out, child_expected)

    # Buffer and bounded file-copy roots.
    work = os.path.join(tmp, 'buffer-work')
    src = os.path.join(tmp, 'buffer-src')
    dst = os.path.join(tmp, 'buffer-dst')
    big = os.path.join(tmp, 'buffer-big')
    real_buf = build(REAL, os.path.join(APPS, 'p1a_buffer_app.obx'),
                     os.path.join(tmp, 'real-buffer'), 'real buffer')
    clean_files(work, src, dst, big)
    real_buf_out = run_app(real_buf, 'real buffer', [work, src, dst, big])
    fake_buf = build(FAKE, os.path.join(APPS, 'p1a_buffer_app.obx'),
                     os.path.join(tmp, 'fake-buffer'), 'fake buffer')
    clean_files(work, src, dst, big)
    fake_buf_out = run_app(fake_buf, 'fake buffer', [work, src, dst, big])
    exact('real buffer', real_buf_out, BUFFER)
    exact('fake buffer', fake_buf_out, BUFFER)
    if real_buf_out != fake_buf_out:
        raise Fail('real/fake buffer traces differ')

    # Fake-only DT_UNKNOWN script: gamma must surface as KIND_UNKNOWN.
    unknown_dir = build(FAKE, os.path.join(APPS, 'p1a_dir_unknown_app.obx'),
                        os.path.join(tmp, 'fake-unknown-dir'), 'fake unknown dir')
    unknown_out = run_app(unknown_dir, 'fake unknown dir', [conf_dir])
    exact('fake unknown dir', unknown_out, [
        'init=true',
        'entries=3 gamma=true kind=0 unknown=0',
        'iter_close=true',
        'dir_close=true',
        'shutdown=0,0',
    ])

    # Scripted faults and one-call-per-operation proof.
    fault_path = os.path.join(tmp, 'fault-file')
    fault = build(FAKE_CTL, os.path.join(APPS, 'p1a_fault_app.obx'),
                  os.path.join(tmp, 'fake-fault'), 'fake fault')
    clean_files(fault_path)
    fault_out = run_app(fault, 'fake fault', [fault_path])
    exact('fake fault', fault_out, FAULT)

    print('sys P1a: all tests passed')


if __name__ == '__main__':
    try:
        main()
    except Fail as e:
        print('FAIL: %s' % e, file=sys.stderr)
        sys.exit(1)
