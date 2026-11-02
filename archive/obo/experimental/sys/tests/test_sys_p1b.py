#!/usr/bin/env python3
"""P1b practical-filesystem tests for obo/experimental/sys.

    python3 obo/experimental/sys/tests/test_sys_p1b.py

Builds the real Linux adapter and the link-time scripted fake, runs the P1b C
harness (normal and ASAN/UBSAN), compares real/fake filesystem and logger
contracts, injects replaceAtomic stage failures, and exercises RFC3339,
environment snapshots, bounded logging, openat2 containment, cross-device
rename and the configuration-update example. P0/P1a/P2 remain separate files.
"""
import os
import re
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


def run_app(path, label, argv, cwd=None, env=None):
    full_env = dict(os.environ, OBO_CHECK='1')
    if env is not None:
        full_env.update(env)
    p = run([path] + list(argv), env=full_env, timeout=120, cwd=cwd)
    if p.returncode != 0:
        raise Fail("%s exited %d:\n%s\n%s" % (label, p.returncode, p.stdout, p.stderr))
    return p, p.stdout.strip().splitlines()


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


FS_TRACE = [
    'init=true',
    'cap=true,true,true,true',
    'mkdir_empty=InvalidArgument',
    'mkdir_dot=InvalidArgument',
    'mkdir_slash=InvalidArgument',
    'mkdir_range=Range',
    'mkdir=ok',
    'mkdir_dup=AlreadyExists',
    'replace=true,true,0',
    'replace2=true,false,0',
    'read=new value',
    '',
    'chmod=true',
    'chmod_range=Range',
    'sync=true',
    'perm=384',
    'file_close=true',
    'sync_closed=Closed',
    'chmod_closed=Closed',
    'touch2=true',
    'rename_noreplace=AlreadyExists',
    'rename=true',
    'rename_missing=NotFound',
    'link=true',
    'link_dup=AlreadyExists',
    'link_dir=Permission',
    'rename_same_noreplace=AlreadyExists',
    'rename_same=true',
    'symlink=true',
    'dangling=true',
    'readlink=config2',
    'readlink_dangling=missing-target',
    'readlink_file=InvalidArgument',
    'readlink_missing=NotFound',
    'long_symlink=true',
    'long_readlink=4095',
    'unlink_dup=ok',
    'unlink_dir=IsDirectory',
    'unlink_missing=NotFound',
    'rmdir_file=NotDirectory',
    'rmdir_missing=NotFound',
    'beneath=ok',
    'beneath_dot=ok',
    'beneath_internal_dot=ok',
    'beneath_dotdot=CrossDevice',
    'beneath_trailing=CrossDevice',
    'beneath_internal_dotdot=ok',
    'beneath_create_dotdot=CrossDevice',
    'beneath_create_trailing=IsDirectory',
    'beneath_create_file=ok',
    'beneath_empty=InvalidArgument',
    'beneath_escape=CrossDevice',
    'beneath_abs=CrossDevice',
    'beneath_file=ok',
    'replace_dir=IsDirectory',
    'rmdir=true',
    'empty_replace=true,false',
    'dir_close=true',
    'mkdir_closed=Closed',
    'rename_closed=Closed',
    'sync_dir_closed=Closed',
    'counts=0,0',
    'shutdown=0,0,0',
]

ATOMIC_TRACE = [
    'init=true',
    'fake_reset=1',
    'baseline=3',
    'success=ok,true,true,0',
    'success_count=1 content=payload',
    'nondur=ok,true,false,0',
    'nondur_count=2',
    'seed_old=ok,true,false,0',
    'old_write=NoSpace',
    'old_write_content=old count=0',
    'old_chmod=Permission',
    'old_chmod_content=old count=0',
    'old_fsync=IoFailure',
    'old_fsync_content=old count=0',
    'old_rename=CrossDevice',
    'old_rename_content=old count=0',
    'temp_create=NoSpace',
    'temp_create_count=3',
    'write=NoSpace',
    'write_count=3',
    'chmod=Permission',
    'chmod_count=3',
    'fsync=IoFailure',
    'fsync_count=3',
    'rename=CrossDevice',
    'rename_count=3',
    'seed=ok,true,false,0',
    'postcommit=ok,true,false,10039',
    'post_count=0 content=payload',
    'commit_isdir=IsDirectory',
    'commit_dir_count=0',
    'temp_collide=Range',
    'collide_count=0',
    'huge_exact=true',
    'huge_seed=ok,true,false,0',
    'huge_over=Range',
    'huge_content=old count=0',
    'cleanup_fail=NoSpace',
    'cleanup_fail_message=write failed; temporary cleanup failed',
    'dir_close=true',
    'counts=0,0',
    'shutdown=0,0,0',
]

CALENDAR_TRACE = [
    'utc=1985-04-12T23:20:50.52Z',
    'lower_t=1996-12-20T00:39:57Z',
    'offset_east=2024-02-29T10:04:56.12Z',
    'negative_zero=2020-01-01T00:00:00Z',
    'lower_z=2020-06-01T12:00:00Z',
    'max_offset=2019-12-31T00:01:00Z',
    'min=0001-01-01T00:00:00Z',
    'max=9999-12-31T23:59:59.999999999Z',
    'zero_fraction=2020-01-01T00:00:00Z',
    'trim=2020-01-01T00:00:00.12Z',
    'leap_second=InvalidArgument',
    'bad_date=InvalidArgument',
    'year_zero=Range',
    'year_five=InvalidArgument',
    'bad_offset=InvalidArgument',
    'excess_fraction=InvalidArgument',
    'trailing=InvalidArgument',
    'space_separator=InvalidArgument',
    'no_zone=InvalidArgument',
    'fraction_empty=InvalidArgument',
    'hour_24=InvalidArgument',
    'format_bad_nanos=InvalidArgument',
    'format_low=Range',
    'format_high=Range',
]

ENV_TRACE = [
    'init=true',
    'count=5',
    'order=OBO_FAKE_ENV,PATH,OBO_FAKE_EMPTY,OBO_FAKE_DUP,OBO_FAKE_DUP',
    'empty=len0',
    'missing=nil',
    'dup=first',
    'deep_copy=1',
    'set_existing=true',
    'set_new=true',
    'new=value',
    'unset_dup=true',
    'dup_gone=nil',
    'unset_missing=false',
    'count_after=4',
    'bad_name=InvalidArgument',
    'bad_set=InvalidArgument',
    'bad_unset=InvalidArgument',
    'cap_before=40000',
    'cap_update=Range',
    'cap_after=40000',
    'cap_small=true',
    'cap_new=Range',
    'cap_new_missing=nil',
    'cap_entries=3',
    'cap_unset=true',
    'cap_reset=true',
    'huge_get=Range',
    'forged=InvalidArgument',
    'forged_entries=InvalidArgument',
    'counts=0,0',
    'shutdown=0,0,0',
]

LOG_TRACE = [
    'init=true',
    'fake_reset=1',
    'first=true,0',
    'second=true,0',
    'third=WouldBlock',
    'drain=true',
    'after_drain=true,0',
    'flush=true',
    'close=true',
    'closed_emit=Closed',
    'closed_flush=Closed',
    'drop_first=true,0',
    'drop_second=false,1',
    'drop_third=false,2',
    'drop_flush=true',
    'drop_close=true',
    'short_emit=true,0',
    'short_calls=7,true',
    'short_close=true',
    'sink_emit=true,0',
    'sink_drain=IoFailure',
    'sink_flush=true',
    'sink_close=true',
    'cap_zero=Range',
    'cap_big=Range',
    'record_cap=LimitExceeded',
    'record_exact=LimitExceeded',
    'forged_policy=InvalidArgument',
    'cap_close=true',
    'leak_emit=true,0',
    'counts=1,1',
    'shutdown=1,1,0',
]

BENEATH_TRACE = [
    'init=true',
    'cap=true',
    'nested=inside-data',
    'inside_link=inside-data',
    'parent=CrossDevice',
    'absolute=CrossDevice',
    'outside_link=CrossDevice',
    'missing=NotFound',
    'dir_close=true',
    'magic=PathLoop',
    'proc_close=true',
    'counts=0,0',
    'shutdown=0,0,0',
]

UNSUPPORTED_TRACE = [
    'init=true',
    'fake_reset=1',
    'disable=1',
    'cap=false',
    'beneath=Unsupported',
    'dir_close=true',
    'counts=0,0',
    'shutdown=0,0,0',
]

EXAMPLE_TRACE = [
    'init=true',
    'old=',
    'committed=true durable=true code=0',
    'new=enabled=true',
    '',
    'verified=true',
    'timestamp=2024-02-29T11:00:00.12Z',
    'demo_env=demo',
    'logged=true',
    'dir_close=true',
    'counts=0,0',
    'shutdown=0,0,0',
]


def make_beneath_fixture(tmp):
    root = os.path.join(tmp, 'beneath-root')
    outside = os.path.join(tmp, 'beneath-outside')
    os.makedirs(os.path.join(root, 'sub'), exist_ok=True)
    os.makedirs(outside, exist_ok=True)
    with open(os.path.join(root, 'sub', 'file'), 'wb') as f:
        f.write(b'inside-data')
    with open(os.path.join(outside, 'secret'), 'wb') as f:
        f.write(b'secret-data')
    os.symlink('sub/file', os.path.join(root, 'inlink'))
    os.symlink('../beneath-outside/secret', os.path.join(root, 'outlink'))
    return root


def main():
    if not os.path.isfile(OBO):
        raise Fail("compiler not found: %s (run `make -C obo obo`)" % OBO)
    os.makedirs(TMPROOT, exist_ok=True)
    tmp = tempfile.mkdtemp(prefix='sys-p1b-', dir=TMPROOT)
    print('sys P1b workdir:', tmp)

    # C harness: normal and ASAN/UBSAN, real and fake.
    native = cc_compile('p1b_c_real', 'p1b_test.c',
                        ['sysregistry.c', 'sysadapter_real.c', 'sysproc_real.c', 'sysnet_real.c'], ['-O2'], tmp)
    fake = cc_compile('p1b_c_fake', 'p1b_test.c',
                      ['sysregistry.c', 'sysadapter_fake.c', 'sysnet_fake.c'], ['-O2'], tmp)
    for exe, label, arg in ((native, 'p1b_c_real', 'real'), (fake, 'p1b_c_fake', 'fake')):
        p = must([exe, arg], label)
        if p.stdout.strip():
            print(label + ': ' + p.stdout.strip())
    san_flags = ['-O1', '-g', '-fsanitize=address,undefined',
                 '-fno-sanitize-recover=all', '-fno-omit-frame-pointer']
    native_san = cc_compile('p1b_c_real_san', 'p1b_test.c',
                            ['sysregistry.c', 'sysadapter_real.c', 'sysproc_real.c', 'sysnet_real.c'], san_flags, tmp)
    fake_san = cc_compile('p1b_c_fake_san', 'p1b_test.c',
                          ['sysregistry.c', 'sysadapter_fake.c', 'sysnet_fake.c'], san_flags, tmp)
    for exe, label, arg in ((native_san, 'p1b_c_real_san', 'real'),
                            (fake_san, 'p1b_c_fake_san', 'fake')):
        must([exe, arg], label, env=dict(os.environ,
                                          ASAN_OPTIONS='detect_leaks=1',
                                          UBSAN_OPTIONS='halt_on_error=1:abort_on_error=1'))
        print(label + ': ok')

    # Real/fake filesystem contract trace.
    fs_work = os.path.join(tmp, 'fs-work')
    os.makedirs(fs_work, exist_ok=True)
    real_fs = build(REAL, os.path.join(APPS, 'p1b_fs_app.obx'),
                    os.path.join(tmp, 'real-fs'), 'real fs')
    p, real_out = run_app(real_fs, 'real fs', [fs_work])
    exact('real fs', real_out, FS_TRACE)
    fake_fs = build(FAKE, os.path.join(APPS, 'p1b_fs_app.obx'),
                    os.path.join(tmp, 'fake-fs'), 'fake fs')
    p, fake_out = run_app(fake_fs, 'fake fs', [fs_work])
    exact('fake fs', fake_out, FS_TRACE)
    if real_out != fake_out:
        raise Fail('real/fake filesystem traces differ')
    leftovers = [name for name in os.listdir(fs_work) if name.startswith('.obo-replace-')]
    if leftovers:
        raise Fail('replaceAtomic left temporary files: %r' % leftovers)

    # replaceAtomic stage injection and no-temporary-left oracle.
    atomic = build(FAKE_CTL, os.path.join(APPS, 'p1b_atomic_fault_app.obx'),
                   os.path.join(tmp, 'fake-atomic'), 'fake atomic')
    p, atomic_out = run_app(atomic, 'fake atomic', [fs_work])
    exact('fake atomic', atomic_out, ATOMIC_TRACE)

    # RFC3339 profile table and formatting.
    cal = build(REAL, os.path.join(APPS, 'p1b_calendar_app.obx'),
                os.path.join(tmp, 'calendar'), 'calendar')
    p, cal_out = run_app(cal, 'calendar', [])
    exact('calendar', cal_out, CALENDAR_TRACE)

    # Environment: deterministic duplicate/empty/order/deep-copy on the fake,
    # plus a real startup snapshot that the process environment cannot leak into.
    env_fake = build(FAKE, os.path.join(APPS, 'p1b_env_app.obx'),
                     os.path.join(tmp, 'fake-env'), 'fake env')
    p, env_out = run_app(env_fake, 'fake env', [])
    exact('fake env', env_out, ENV_TRACE)
    env_real = build(REAL, os.path.join(APPS, 'p1b_env_real_app.obx'),
                     os.path.join(tmp, 'real-env'), 'real env')
    p, env_real_out = run_app(
        env_real, 'real env', [],
        env={'OBO_P1B_A': 'startup-value', 'OBO_P1B_EMPTY': ''})
    exact('real env', env_real_out, [
        'init=true',
        'a=startup-value',
        'empty=len0',
        'missing=nil',
        'mutated=true',
        'after=local-only',
        'shutdown=0,0,0',
    ])

    # Bounded logger: queue bound, drop counter, short drain, sink error, leak.
    log_app = build(FAKE_CTL, os.path.join(APPS, 'p1b_log_app.obx'),
                    os.path.join(tmp, 'fake-log'), 'fake log')
    p, log_out = run_app(log_app, 'fake log', [])
    exact('fake log', log_out, LOG_TRACE)
    log_real = build(REAL, os.path.join(APPS, 'p1b_log_real_app.obx'),
                     os.path.join(tmp, 'real-log'), 'real log')
    p, log_real_out = run_app(log_real, 'real log', [])
    exact('real log', log_real_out, [
        'init=true',
        'emit=true,0',
        'flush=true',
        'close=true',
        'shutdown=0,0,0',
    ])
    stamp = re.compile(r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}'
                       r'(?:\.\d{1,9})?Z WARN ')
    lines = [line for line in p.stderr.splitlines() if line]
    if len(lines) != 1 or not stamp.match(lines[0]):
        raise Fail('real log line is not one timestamped record: %r' % p.stderr)
    want_escaped = ' WARN a\\nb\\tc\\x01d\\\\e'
    if not lines[0].endswith(want_escaped):
        raise Fail('real log escaping mismatch: %r' % lines[0])

    # B1: a logger over stdout must not leave O_NONBLOCK on the shared open
    # file description. Hold the pipe reader paused so a leaked nonblocking flag
    # truncates the 200,000-byte print instead of applying backpressure.
    fd_app = build(REAL, os.path.join(APPS, 'p1b_log_fd_app.obx'),
                   os.path.join(tmp, 'log-fd'), 'log fd')
    fdproc = subprocess.Popen([fd_app], stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE,
                              env=dict(os.environ, OBO_CHECK='1'))
    time.sleep(0.25)
    fd_out, fd_err = fdproc.communicate(timeout=60)
    if fdproc.returncode != 0:
        raise Fail('log fd app exited %d:\n%s\n%s'
                   % (fdproc.returncode, fd_out.decode('utf-8', 'replace'),
                      fd_err.decode('utf-8', 'replace')))
    if fd_out.count(b'x') != 200000 or b'BEFORE' not in fd_out or b'AFTER' not in fd_out:
        raise Fail('large print after logger open was truncated: %d x bytes'
                   % fd_out.count(b'x'))

    # openat2 containment: parent, absolute, symlink escape and a magic link.
    root = make_beneath_fixture(tmp)
    beneath = build(REAL, os.path.join(APPS, 'p1b_beneath_app.obx'),
                    os.path.join(tmp, 'beneath'), 'beneath')
    p, beneath_out = run_app(beneath, 'beneath', [root, '/proc/self'])
    if 'cap=false' in beneath_out:
        print('skip openat2 containment: kernel lacks openat2 (capability=false)')
    else:
        exact('beneath', beneath_out, BENEATH_TRACE)

    unsupported = build(FAKE_CTL, os.path.join(APPS, 'p1b_beneath_unsupported_app.obx'),
                        os.path.join(tmp, 'beneath-unsupported'), 'beneath unsupported')
    p, unsupported_out = run_app(unsupported, 'beneath unsupported', [root])
    exact('beneath unsupported', unsupported_out, UNSUPPORTED_TRACE)

    # Real directory errors (ENOTEMPTY) and cross-device rename.
    direrr_root = os.path.join(tmp, 'dir-errors')
    os.makedirs(os.path.join(direrr_root, 'nonempty'), exist_ok=True)
    with open(os.path.join(direrr_root, 'nonempty', 'child'), 'wb') as f:
        f.write(b'x')
    second = None
    try:
        if os.path.isdir('/dev/shm') and os.access('/dev/shm', os.W_OK):
            cand = tempfile.mkdtemp(prefix='p1b-xdev-', dir='/dev/shm')
            if os.stat(cand).st_dev != os.stat(tmp).st_dev:
                second = cand
        if second is None:
            print('skip cross-device rename: no second filesystem under /dev/shm')
        else:
            direrr = build(REAL, os.path.join(APPS, 'p1b_dir_errors_app.obx'),
                           os.path.join(tmp, 'dir-errors-app'), 'dir errors')
            p, direrr_out = run_app(direrr, 'dir errors', [direrr_root, second])
            exact('dir errors', direrr_out, [
                'init=true',
                'nonempty=Busy',
                'unlink_nonempty=IsDirectory',
                'mkdir_existing=AlreadyExists',
                'crossdev=CrossDevice',
                'second_close=true',
                'first_close=true',
                'counts=0,0',
                'shutdown=0,0,0',
            ])
    finally:
        if second is not None:
            shutil.rmtree(second, ignore_errors=True)

    # Configuration-update example: only the test directory is written.
    example_dir = os.path.join(tmp, 'example')
    os.makedirs(example_dir, exist_ok=True)
    example = build(REAL, os.path.join(APPS, 'p1b_config_example.obx'),
                    os.path.join(tmp, 'example-app'), 'example')
    p, example_out = run_app(example, 'example', [example_dir],
                             env={'OBO_P1B_DEMO': 'demo'})
    exact('example', example_out, EXAMPLE_TRACE)
    if sorted(os.listdir(example_dir)) != ['config']:
        raise Fail('example wrote unexpected files: %r' % os.listdir(example_dir))
    with open(os.path.join(example_dir, 'config'), 'rb') as f:
        if f.read() != b'enabled=true\n':
            raise Fail('example config content mismatch')
    if 'configuration replaced' not in p.stderr:
        raise Fail('example did not log its update')

    print('sys P1b: all tests passed')


if __name__ == '__main__':
    try:
        main()
    except Fail as e:
        print('FAIL: %s' % e, file=sys.stderr)
        sys.exit(1)
