#!/usr/bin/env python3
"""P3 process/signal/terminal tests for obo/experimental/sys.

    python3 obo/experimental/sys/tests/test_sys_p3.py

Builds the real Linux adapter and the link-time scripted fake, runs the P3 C
harness (normal and ASan/UBSan/LSan), compares real/fake process, signal and
terminal contracts, exercises the pty path when available, checks the
supervisor example and the P1b fake-only nits. P0/P1a/P1b/P2/P2q remain
separate files and are not weakened here.
"""
import os
import pty
import re
import select
import shutil
import signal
import struct
import subprocess
import sys
import tempfile
import termios
import fcntl

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
    kw.setdefault('stdin', subprocess.DEVNULL)
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, **kw)


def must(cmd, label, **kw):
    p = run(cmd, **kw)
    if p.returncode != 0:
        raise Fail("%s failed:\n%s\n%s" % (label, ' '.join(cmd),
                                            (p.stderr or p.stdout).strip()))
    return p


def build(spec, app, out, label):
    os.makedirs(out, exist_ok=True)
    must([sys.executable, TOOLPY, 'build', spec, app, '--out', out, '--obo', OBO], label)
    must([sys.executable, TOOLPY, 'verify', os.path.join(out, 'manifest.json')],
         label + ' manifest verify')
    return os.path.join(out, 'app')


def run_app(path, label, argv=None, cwd=None, env=None, expect=0):
    full_env = dict(os.environ, OBO_CHECK='1')
    if env is not None:
        full_env.update(env)
    p = run([path] + list(argv or []), env=full_env, timeout=120, cwd=cwd)
    if expect is not None and p.returncode != expect:
        raise Fail("%s exited %d:\n%s\n%s" % (label, p.returncode, p.stdout, p.stderr))
    return p, p.stdout.strip().splitlines()


def exact(label, lines, want):
    if lines != want:
        raise Fail("%s output mismatch:\n--- got ---\n%s\n--- want ---\n%s"
                   % (label, '\n'.join(lines), '\n'.join(want)))


def cc_compile(name, src, extra_sources, extra_flags, out_dir, define=None):
    cc = shutil.which(os.environ.get('CC', 'cc'))
    if not cc:
        raise Fail('C compiler not found')
    exe = os.path.join(out_dir, name)
    cmd = [cc, '-std=c11', '-Wall', '-Wextra', '-Werror', *extra_flags]
    if define:
        cmd += ['-D' + define]
    cmd += ['-I', NATIVE, os.path.join(CSRC, src)]
    cmd += [os.path.join(NATIVE, s) for s in extra_sources]
    cmd += ['-o', exe]
    must(cmd, 'compile ' + name)
    return exe


REAL_C = ['sysregistry.c', 'sysadapter_real.c', 'sysproc_real.c', 'sysnet_real.c']
FAKE_C = ['sysregistry.c', 'sysadapter_fake.c', 'sysnet_fake.c']

REAL_C_TRACE = [
    'real_out=hi',
    'real_status=exit:7',
    'real_127=exit:127',
    'real_execfail=-2',
    'real_child_cwd=/tmp',
    'real_group_term=1',
    'real_group_status=signal:2',
    'real_overlap=-16',
    'real_signal_poll=1',
    'real_signal=258',
    'real_mask_restored=1',
    'real_term_size=-95',
    'real_term_raw=-95',
    'real_term_dup=1',
    'real_chdir=1',
    'real_cwd_len_ok=1',
    'real_sigchld_init=-16',
    'real_sigchld_spawn=-16',
    'real_nocldwait_init=-16',
    'real_shutdown=0',
]

FAKE_C_TRACE = [
    'fake_write=2',
    'fake_exit=1',
    'fake_out=hi',
    'fake_status=exit:7',
    'fake_group=1',
    'fake_group_status=signal:2',
    'fake_signal=770',
    'fake_term_size=103079215184',
    'fake_term_raw=1',
    'fake_term_restore=1',
    'fake_uaf_retag=1',
    'fake_uaf=uafdata',
    'fake_shutdown=0',
    'fake_leak_shutdown=0,1',
]

REAL_PROCESS_TRACE = [
    'init=true',
    'chdir=true',
    'cwd=workdir',
    'out1=hello',
    'take2=InvalidArgument',
    'status1=exit:7',
    'status_repeat=exit:7',
    'nonblocking_fromstream=Unsupported',
    'take_input_write=3',
    'take_input_out=abc',
    'out2=out',
    'err2=err',
    'merge=merged',
    'inherit=inherited',
    'explicit=explicit',
    'empty_env=emptyenv',
    'child_cwd=sub',
    'pcmd_close=true',
    'execfail=NotFound',
    'exit127=exit:127',
    'signalled=signal:2',
    'group_term=true',
    'group_status=signal:2',
    'nogroup_term=InvalidArgument',
    'nogroup_status=signal:6',
    'detach=true',
    'detached_wait=Closed',
    'detached_poll=Closed',
    'close_running=true',
    'reaped=true',
    'watch_event=true',
    'watch_status=exit:3',
    'late_chdir=Busy',
    'fromstream=file-data',
    'ccmd_close=true',
    'ccmd_close2=false',
    'counts=0,0',
]

FAKE_PROCESS_TRACE = [
    'init=true',
    'cwd0=/fake-cwd',
    'chdir=true',
    'cwd1=/fake-dir',
    'poll_running=nil',
    'fake_write=5',
    'fake_exit=1',
    'out=hello',
    'eof=nil',
    'take2=InvalidArgument',
    'status=exit:7',
    'take_input_write=',
    '2',
    'take_input=in',
    'nonblocking_fromstream=Unsupported',
    'nonblocking_after_exit=Unsupported',
    'uaf_input=uafdata',
    'fromstream_output=',
    '3',
    'fromstream_error=',
    '3',
    'merge=merged',
    'fromstream=seed',
    'fromstream_close=true',
    'empty_exec=InvalidArgument',
    'empty_argv=InvalidArgument',
    'merge_input=InvalidArgument',
    'nil_source=InvalidArgument',
    'argmax=Range',
    'explicit=exit:0',
    'empty_env=exit:0',
    'empty_arg=exit:0',
    'group_term=true',
    'group_status=signal:2',
    'nogroup_term=InvalidArgument',
    'force_status=signal:6',
    'detach=true',
    'detached_wait=Closed',
    'reaped=true',
    'fault_spawn=NoMemory',
    'fault_pipe=ResourceLimit',
    'fault_dup=InvalidResource',
    'fault_exec=NotFound',
    'fault_wait=Interrupted',
    'fault_kill=Permission',
    'fault_detach=InvalidArgument',
    'fault_take=Interrupted',
    'fault_reap=Interrupted',
    'counts=0,0',
]

FAKE_SIGNAL_TERMINAL_TRACE = [
    'init=true',
    'add=true',
    'add2=false',
    'add_other=InvalidArgument',
    'add_range=Range',
    'sub_empty=InvalidArgument',
    'sub_forged=Range',
    'overlap=Busy',
    'next_empty=nil',
    'push=1',
    'event=2,3',
    'event2=nil',
    'sigclose=true',
    'sigclose2=false',
    'size=24,80',
    'size2=40,100',
    'raw=true',
    'raw2=false',
    'restore=true',
    'restore2=false',
    'dup_write=1',
    'resize=true',
    'term_close=true',
    'size_closed=Closed',
    'watch_close=true',
    'raw_close=true',
    'fault_sub=Busy',
    'fault_term=ResourceLimit',
    'fault_next=Interrupted',
    'fault_size=Unsupported',
    'fault_raw=Unsupported',
    'fault_restore=Unsupported',
    'fault_dup=ResourceLimit',
    'counts=0,0',
]

REAL_SIGNAL_TRACE = [
    'init=true',
    'add=true',
    'add2=false',
    'add_other=InvalidArgument',
    'add_range=Range',
    'overlap=Busy',
    'next_empty=nil',
    'event=2,1',
    'event2=nil',
    'sigclose=true',
    'sigclose2=false',
    'resize_event=true',
    'resize_next=5',
    'counts=0,0',
]

SUPERVISOR_TRACE = [
    'init=true',
    'worker=ready',
    'signal=2',
    'group_term=true',
    'worker_status=signal:2',
    'sig_closed=true',
    'dir_closed=true',
    'cleanup=0,0',
]

CENSUS_TRACE = [
    'init=true',
    'openfile_marker=clean',
    'census=0,0',
    'shutdown_cleanup=true',
]

NITS_TRACE = [
    'init=true',
    'dot_excl=AlreadyExists',
    'dot_plain=IsDirectory',
    'dot_kind=',
    '4',
    'root_kind=dir',
    'dot_identity=same',
    'counts=',
    '0',
    '0',
]


FD_TRACE = [
    'posix_null_pipe=stdin=open stdout=open stderr=open marker=clean',
    'fork_null_pipe=stdin=open stdout=open stderr=open marker=clean',
    'posix_stream_pipe=stdin=open stdout=open stderr=open marker=clean',
    'fork_stream_pipe=stdin=open stdout=open stderr=open marker=clean',
    'posix_inherit_pipe=stdin=closed stdout=open stderr=open marker=clean',
    'fork_inherit_pipe=stdin=closed stdout=open stderr=open marker=clean',
    'posix_pipe_inherit=stdin=open stdout=closed stderr=open marker=clean',
    'fork_pipe_inherit=stdin=open stdout=closed stderr=open marker=clean',
    'posix_out_null=stdin=closed stdout=open stderr=open marker=clean',
    'fork_out_null=stdin=closed stdout=open stderr=open marker=clean',
    'posix_err_pipe_err=childerr',
    'posix_err_pipe=stdin=open stdout=closed stderr=open marker=clean',
    'fork_err_pipe_err=childerr',
    'fork_err_pipe=stdin=open stdout=closed stderr=open marker=clean',
    'posix_err_null=stdin=open stdout=closed stderr=open marker=clean',
    'fork_err_null=stdin=open stdout=closed stderr=open marker=clean',
    'posix_err_inherit_err=childerr',
    'posix_err_inherit=stdin=open stdout=closed stderr=open marker=clean',
    'fork_err_inherit_err=childerr',
    'fork_err_inherit=stdin=open stdout=closed stderr=open marker=clean',
    'posix_missing=-2',
    'fork_missing=-2',
    'fd_shutdown=0',
]

def run_pty_terminal(app, label):
    pid, master = pty.fork()
    if pid == 0:
        fcntl.ioctl(0, termios.TIOCSWINSZ, struct.pack('HHHH', 24, 80, 0, 0))
        os.environ['OBO_CHECK'] = '1'
        os.execv(app, [app, 'resizeonly'])
    buf = b''
    raw_seen = False
    restored_seen = False
    resized = False
    try:
        while True:
            r, _, _ = select.select([master], [], [], 0.02)
            if r:
                try:
                    chunk = os.read(master, 4096)
                except OSError:
                    chunk = b''
                if chunk:
                    buf += chunk
                    if b'READY' in buf and not resized:
                        fcntl.ioctl(master, termios.TIOCSWINSZ,
                                    struct.pack('HHHH', 40, 100, 0, 0))
                        resized = True
            try:
                attrs = termios.tcgetattr(master)
                if not (attrs[3] & termios.ICANON) and not (attrs[3] & termios.ECHO):
                    raw_seen = True
                if raw_seen and (attrs[3] & termios.ICANON):
                    restored_seen = True
            except termios.error:
                pass
            done, status = os.waitpid(pid, os.WNOHANG)
            if done == pid:
                break
        if not os.WIFEXITED(status) or os.WEXITSTATUS(status) != 0:
            raise Fail('%s exited abnormally: status %d' % (label, status))
    finally:
        try:
            os.close(master)
        except OSError:
            pass
    if not resized:
        raise Fail('%s: READY marker was not observed' % label)
    if not raw_seen:
        raise Fail('%s: raw mode was never visible on the pty' % label)
    if not restored_seen:
        raise Fail('%s: termios was not restored before close' % label)
    text = buf.decode('utf-8', 'replace')
    want = [
        'init=true', 'size=24,80', 'resize_event=true', 'size_after=40,100',
        'raw=true',
        'raw_marker', 'restore=true', 'restore2=false', 'dup_ok=true',
        'term_close=true', 'late_size=Closed', 'out_close=true',
        'err_close=true', 'counts=0,0',
    ]
    lines = [line for line in text.splitlines() if line.strip() and line.strip() != 'READY']
    if lines != want:
        raise Fail('%s output mismatch:\n--- got ---\n%s\n--- want ---\n%s'
                   % (label, '\n'.join(lines), '\n'.join(want)))


def main():
    if not os.path.isfile(OBO):
        raise Fail("compiler not found: %s (run `make -C obo obo`)" % OBO)
    os.makedirs(TMPROOT, exist_ok=True)
    tmp = tempfile.mkdtemp(prefix='sys-p3-', dir=TMPROOT)
    print('sys P3 workdir:', tmp)

    # C harness: normal, then ASan/UBSan/LSan.
    native = cc_compile('p3_c_real', 'p3_test.c', REAL_C, ['-O2'], tmp)
    fake = cc_compile('p3_c_fake', 'p3_test.c', FAKE_C, ['-O2'], tmp, 'P3_FAKE')
    p = must([native], 'p3_c_real')
    exact('p3 C real', p.stdout.strip().splitlines(), REAL_C_TRACE)
    p = must([fake], 'p3_c_fake')
    exact('p3 C fake', p.stdout.strip().splitlines(), FAKE_C_TRACE)
    san = ['-O1', '-g', '-fsanitize=address,undefined',
           '-fno-sanitize-recover=all', '-fno-omit-frame-pointer']
    native_san = cc_compile('p3_c_real_san', 'p3_test.c', REAL_C, san, tmp)
    fake_san = cc_compile('p3_c_fake_san', 'p3_test.c', FAKE_C, san, tmp, 'P3_FAKE')
    for exe, label, arg in ((native_san, 'p3_c_real_san', None),
                            (fake_san, 'p3_c_fake_san', 'fake')):
        p = must([exe] + ([arg] if arg else []), label,
                 env=dict(os.environ, ASAN_OPTIONS='detect_leaks=1',
                          UBSAN_OPTIONS='halt_on_error=1:abort_on_error=1'))
        print(label + ': ok')

    # B1/B2: prepared-fd collisions and unintended-inheritance regression,
    # including a sanitizer run. This harness deliberately closes 0/1/2.
    fd_real = cc_compile('p3_fd_real', 'p3_fd_test.c', REAL_C, ['-O2'], tmp)
    p = must([fd_real], 'p3_fd_real')
    exact('p3 fd real', p.stdout.strip().splitlines(), FD_TRACE)
    fd_san = cc_compile('p3_fd_real_san', 'p3_fd_test.c', REAL_C, san, tmp)
    p = must([fd_san], 'p3_fd_real_san',
             env=dict(os.environ, ASAN_OPTIONS='detect_leaks=1',
                      UBSAN_OPTIONS='halt_on_error=1:abort_on_error=1'))
    exact('p3 fd real san', p.stdout.strip().splitlines(), FD_TRACE)
    print('p3_fd_real_san: ok')

    # Real process contract.
    work = os.path.join(tmp, 'process-work')
    os.makedirs(os.path.join(work, 'sub'), exist_ok=True)
    with open(os.path.join(work, 'input.txt'), 'wb') as f:
        f.write(b'file-data')
    real_app = build(REAL, os.path.join(APPS, 'p3_process_real_app.obx'),
                     os.path.join(tmp, 'real-process'), 'real process')
    p, out = run_app(real_app, 'real process', [work],
                     env={'OBO_P3_INHERIT': 'inherited'})
    exact('real process', out, REAL_PROCESS_TRACE)
    if sorted(name for name in os.listdir(work) if name.startswith('.obo-')) != []:
        raise Fail('process app left temporary files: %r' % os.listdir(work))

    # Fake process contract with scripted stages and faults.
    fake_app = build(FAKE_CTL, os.path.join(APPS, 'p3_process_fake_app.obx'),
                     os.path.join(tmp, 'fake-process'), 'fake process')
    p, out = run_app(fake_app, 'fake process', [])
    exact('fake process', out, FAKE_PROCESS_TRACE)

    # Fake signal + terminal.
    st_app = build(FAKE_CTL, os.path.join(APPS, 'p3_signal_terminal_fake_app.obx'),
                   os.path.join(tmp, 'fake-signal-terminal'), 'fake signal terminal')
    p, out = run_app(st_app, 'fake signal terminal', [])
    exact('fake signal terminal', out, FAKE_SIGNAL_TERMINAL_TRACE)

    # Real signals, including default-action restoration after close.
    sig_app = build(REAL, os.path.join(APPS, 'p3_signals_real_app.obx'),
                    os.path.join(tmp, 'real-signals'), 'real signals')
    p, out = run_app(sig_app, 'real signals', [])
    exact('real signals', out, REAL_SIGNAL_TRACE)
    p = run([sig_app, 'die'], env=dict(os.environ, OBO_CHECK='1'), timeout=60)
    if p.returncode != -signal.SIGTERM:
        raise Fail('real signal mask was not restored: die mode exited %d (%s)'
                   % (p.returncode, p.stdout[:200]))
    if 'survived' in p.stdout:
        raise Fail('real signal mask was not restored: app survived')

    # Real terminal on a pty and the non-tty Unsupported path.
    term_app = build(REAL, os.path.join(APPS, 'p3_terminal_real_app.obx'),
                     os.path.join(tmp, 'real-terminal'), 'real terminal')
    if hasattr(os, 'openpty') and os.path.isdir('/dev/pts'):
        run_pty_terminal(term_app, 'real terminal pty')
        print('real terminal pty: ok')
    else:
        print('skip real terminal pty: no /dev/pts')
    p, out = run_app(term_app, 'terminal notty', ['notty'])
    exact('terminal notty', out, [
        'init=true',
        'size=Unsupported',
        'raw=Unsupported',
        'out_close=true',
        'err_close=true',
        'counts=0,0',
    ])

    # Supervisor example in a test temp directory.
    sup_dir = os.path.join(tmp, 'supervisor')
    os.makedirs(sup_dir, exist_ok=True)
    sup = build(REAL, os.path.join(APPS, 'p3_supervisor_example.obx'),
                os.path.join(tmp, 'supervisor-app'), 'supervisor')
    p, out = run_app(sup, 'supervisor', [sup_dir])
    exact('supervisor', out, SUPERVISOR_TRACE)
    if os.listdir(sup_dir):
        raise Fail('supervisor wrote unexpected files: %r' % os.listdir(sup_dir))

    # Legacy openFile descriptor, repeated-spawn census and shutdown cleanup.
    census = build(REAL, os.path.join(APPS, 'p3_fd_census_app.obx'),
                   os.path.join(tmp, 'fd-census'), 'fd census')
    p, out = run_app(census, 'fd census', [])
    exact('fd census', out, CENSUS_TRACE)

    # P1b fake-only nits.
    nits = build(FAKE, os.path.join(APPS, 'p3_p1b_nits_app.obx'),
                 os.path.join(tmp, 'p1b-nits'), 'p1b nits')
    p, out = run_app(nits, 'p1b nits', [])
    exact('p1b nits', out, NITS_TRACE)

    print('sys P3: all tests passed')


if __name__ == '__main__':
    try:
        main()
    except Fail as e:
        print('FAIL: %s' % e, file=sys.stderr)
        sys.exit(1)
