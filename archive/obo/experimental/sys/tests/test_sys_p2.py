#!/usr/bin/env python3
"""P2 systems-library tests for obo/experimental/sys.

    python3 obo/experimental/sys/tests/test_sys_p2.py

Builds the real Linux adapter and the link-time scripted fake with the existing
experimental cinterop tool, runs C harnesses (normal and ASAN/UBSAN), compares
real/fake obo contract traces for addresses, TCP/UDP/Unix sockets, reactor
timers/wake/stop/fairness, resolver and waitio helpers, and exercises scripted
faults (EMFILE, EAGAIN, short I/O, pending/failed connect, EAI and deadline
retention). P0/P1a remain in their own suites.
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
    p = run([path] + list(argv), env=dict(os.environ, OBO_CHECK='1'), timeout=120)
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
    'caps=true,true,true',
    'bad_backlog=Range',
    'addr4=127.0.0.1',
    'addr4_port=80',
    'addr6=2001:db8::1',
    'addr6_loop=::1',
    'addr6_zero=::',
    'addr6_full=1:2:3:4:5:6:7:8',
    'addr6_scope=2001::1%3',
    'bad_octal=InvalidArgument',
    'bad_byte=Range',
    'bad_short=InvalidArgument',
    'bad_v6=InvalidArgument',
    'bad_bracket=InvalidArgument',
    'bad_port=Range',
    'bad_port2=Range',
    'short4=InvalidArgument',
    'short6=InvalidArgument',
    'neg_scope=Range',
    'empty_path=InvalidArgument',
    'long_path=InvalidArgument',
    'empty_abs=InvalidArgument',
    'abs_fmt=unix-abstract:616263',
    'abs_sep=unix-abstract:0123,unix-abstract:1203,true',
    'tcp4_port=true',
    'tcp4_events=true,true',
    'tcp4_finish=true',
    'tcp4_accept=true',
    'tcp4_nodelay=true',
    'tcp4_keepalive=true',
    'tcp4_keepalive_clear=true',
    'tcp4_write=5',
    'tcp4_readinto=5,hello',
    'tcp4_writefrom=5',
    'tcp4_read=hello',
    'tcp4_dup_write=5',
    'tcp4_dup_read=hello',
    'tcp4_dup_close=true',
    'tcp4_shutdown=true',
    'tcp4_eof_ready=true',
    'tcp4_eof=nil',
    'tcp4_shutdown_idem=true',
    'tcp4_stream_shutdown=true',
    'tcp4_stream_close=true',
    'tcp4_close=true',
    'tcp4_listener_close=true',
    'tcp6_port=true',
    'tcp6_events=true,true',
    'tcp6_finish=true',
    'tcp6_accept=true',
    'tcp6_nodelay=true',
    'tcp6_keepalive=true',
    'tcp6_keepalive_clear=true',
    'tcp6_write=5',
    'tcp6_readinto=5,hello',
    'tcp6_writefrom=5',
    'tcp6_read=hello',
    'tcp6_dup_write=5',
    'tcp6_dup_read=hello',
    'tcp6_dup_close=true',
    'tcp6_shutdown=true',
    'tcp6_eof_ready=true',
    'tcp6_eof=nil',
    'tcp6_shutdown_idem=true',
    'tcp6_stream_shutdown=true',
    'tcp6_stream_close=true',
    'tcp6_close=true',
    'tcp6_listener_close=true',
    'unix_stream_ready=true,true',
    'unix_finish=true',
    'unix_write=4',
    'unix_read=unix',
    'unix_peer_close=true',
    'unix_conn_close=true',
    'unix_listener_close=true',
    'abstract_ready=true,true',
    'abstract_finish=true',
    'abstract_write=3',
    'abstract_read=abs',
    'abstract_listener_close=true',
    'dgram_local=true',
    'dgram_empty_send=0',
    'dgram_empty_len=0 trunc=false orig=0',
    'dgram_empty_peer=true',
    'dgram_send=100',
    'dgram_trunc=true orig=100 len=10',
    'dgram_peer=true',
    'dgram_full=false,100,100',
    'dgram_rcvbuf=true',
    'dgram_sndbuf=true',
    'dgram_broadcast=true',
    'dgram_recv_close=true',
    'dgram_send_close=true',
    'udp_port=true',
    'udp_send=3',
    'udp_recv=3',
    'udp_peer=true',
    'timer_fired=true ticks_ok=true',
    'timer_cancel=true',
    'reactor_wake=true',
    'wake_event=true',
    'fair_watches=true,true',
    'fairness=true',
    'stale_remove=false',
    'stale_events=0',
    'reactor_stop=true',
    'stop_wait=Cancelled',
    'reactor_stop2=false',
    'reactor_close=true',
    'resolve_num=1',
    'resolve_num6=1',
    'resolve_bad=ResolveNotFound',
    'resolve_family=InvalidArgument',
    'resolve_service=ResolveFailure',
    'resolve_local=true',
    'cancel_read=Cancelled',
    'waitio_write=true done=5',
    'waitio_read=true avail=5',
    'deadline_read=TimedOut',
    'cancel_wait=Cancelled',
    'file_watch=Unsupported',
    'counts=0,0',
    'shutdown=0,0,0',
]

FAULT = [
    'init=true',
    'fake_reset=1',
    'scripted_listen=ResourceLimit',
    'empty_accept=WouldBlock',
    'accept_emfile=ResourceLimit',
    'pending_first=false',
    'pending_second=true',
    'fail_finish=Refused',
    'fail_into=Refused',
    'fail_retired=true',
    'zero_read=InvalidArgument',
    'range_write=InvalidArgument',
    'big_read=Range',
    'neg_recv=InvalidArgument',
    'huge_recv=Range',
    'big_send=Range',
    'long_host=InvalidArgument',
    'short_write=2',
    'short_read=he',
    'read_short=1',
    'read_eagain=WouldBlock',
    'write_eagain=WouldBlock',
    'waitio_eagain=true done=5',
    'waitio_eintr=true done=2',
    'wait_bounds=Range',
    'eintr_read=TimedOut',
    'eintr_at_deadline=true',
    'progress=TimedOut',
    'progress_bytes=2',
    'resolve_tmp=ResolveTemporary',
    'resolve_none=ResolveNotFound',
    'resolve_sys=IoFailure',
    'resolve_num=ResolveNotFound',
    'watch_limit=ResourceLimit',
    'timer_limit=ResourceLimit',
    'connect_eintr_first=false second=true',
    'halfclose_ready=true',
    'halfclose_eof=nil',
    'shutdown_read_write=1 eof=true',
    'into_watch_events=0',
    'into_watch_remove=false',
    'counts_resources=0',
    'shutdown=0,0',
]


def clean_paths(names):
    for name in names:
        try:
            os.unlink(name)
        except FileNotFoundError:
            pass


def main():
    if not os.path.isfile(OBO):
        raise Fail("compiler not found: %s (run `make -C obo obo`)" % OBO)
    os.makedirs(TMPROOT, exist_ok=True)
    tmp = tempfile.mkdtemp(prefix='sys-p2-', dir=TMPROOT)
    print('sys P2 workdir:', tmp)

    # C harnesses: normal and ASAN/UBSAN, real and fake.
    native = cc_compile('p2_c_real', 'p2_test.c',
                        ['sysregistry.c', 'sysadapter_real.c', 'sysproc_real.c', 'sysnet_real.c'],
                        ['-O2'], tmp)
    fake = cc_compile('p2_c_fake', 'p2_test.c',
                      ['sysregistry.c', 'sysadapter_fake.c', 'sysnet_fake.c'],
                      ['-O2'], tmp)
    for exe, label, arg in ((native, 'p2_c_real', 'real'), (fake, 'p2_c_fake', 'fake')):
        p = must([exe, arg], label)
        print(p.stdout.strip())
    san_flags = ['-O1', '-g', '-fsanitize=address,undefined', '-fno-omit-frame-pointer']
    native_san = cc_compile('p2_c_real_san', 'p2_test.c',
                            ['sysregistry.c', 'sysadapter_real.c', 'sysproc_real.c', 'sysnet_real.c'],
                            san_flags, tmp)
    fake_san = cc_compile('p2_c_fake_san', 'p2_test.c',
                          ['sysregistry.c', 'sysadapter_fake.c', 'sysnet_fake.c'],
                          san_flags, tmp)
    env = dict(os.environ, ASAN_OPTIONS='detect_leaks=1')
    for exe, label, arg in ((native_san, 'p2_c_real_san', 'real'),
                            (fake_san, 'p2_c_fake_san', 'fake')):
        p = must([exe, arg], label, env=env)
        print(label + ': ' + p.stdout.strip())

    # Real/fake conformance roots use the same private argument strings; the
    # real run is cleaned before the fake run so both traces match exactly.
    paths = [os.path.join(tmp, name) for name in ('unix-stream', 'unix-a',
                                                   'unix-b', 'file', 'abstract')]
    real_app = build(REAL, os.path.join(APPS, 'p2_app.obx'),
                     os.path.join(tmp, 'real-conformance'), 'real conformance')
    clean_paths(paths)
    real_out = run_app(real_app, 'real conformance', paths)
    clean_paths(paths)
    fake_app = build(FAKE, os.path.join(APPS, 'p2_app.obx'),
                     os.path.join(tmp, 'fake-conformance'), 'fake conformance')
    fake_out = run_app(fake_app, 'fake conformance', paths)
    clean_paths(paths)
    exact('real conformance', real_out, TRACE)
    exact('fake conformance', fake_out, TRACE)
    if real_out != fake_out:
        raise Fail('real/fake conformance traces differ')

    # Scripted fault injection.
    fault_path = os.path.join(tmp, 'fault-file')
    clean_paths([fault_path])
    fault_app = build(FAKE_CTL, os.path.join(APPS, 'p2_fault_app.obx'),
                      os.path.join(tmp, 'fake-fault'), 'fake fault')
    fault_out = run_app(fault_app, 'fake fault', [fault_path])
    exact('fake fault', fault_out, FAULT)

    print('sys P2: all tests passed')


if __name__ == '__main__':
    try:
        main()
    except Fail as e:
        print('FAIL: %s' % e, file=sys.stderr)
        sys.exit(1)
