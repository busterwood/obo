#!/usr/bin/env python3
"""H2a HTTP/2 C shim, obo session/stream and interop tests.

    python3 obo/experimental/http2/tests/test_http2.py

Builds the C shim against the installed libnghttp2 runtime and a privately
fetched matching libnghttp2-dev header tree (no headers are committed and no
absolute include path is committed), runs the C harness with a real nghttp2
client session, builds the obo memory/TLS/h2c drivers through cinterop, and
checks curl h2/h2c interop. h2spec/h2load are run only when already installed.
"""
import atexit
import glob
import importlib.util
import json
import os
import shutil
import socket
import ssl
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import h2client
PKG = os.path.dirname(HERE)
REPO = os.path.dirname(os.path.dirname(os.path.dirname(PKG)))
OBO = os.path.abspath(os.environ.get('OBO', os.path.join(REPO, 'obo', 'obo')))
TOOLPY = os.path.join(REPO, 'obo', 'experimental', 'cinterop', 'cinterop.py')
CINTEROP_TESTS = os.path.join(REPO, 'obo', 'experimental', 'cinterop', 'tests',
                              'test_cinterop.py')
NATIVE = os.path.join(PKG, 'native')
SYS_NATIVE = os.path.join(REPO, 'obo', 'experimental', 'sys', 'native')
TLS_NATIVE = os.path.join(REPO, 'obo', 'experimental', 'tls', 'native')
APPS = os.path.join(HERE, 'apps')
HARNESS = os.path.join(HERE, 'c', 'h2_harness.c')
TMPROOT = '/tmp/opencode'
CACHE = os.path.join(TMPROOT, 'nghttp2-dev-cache')
EXPECTED_NGHTPP2 = 0x013b00


class Fail(Exception):
    pass


def run(cmd, **kw):
    timeout = kw.pop('timeout', 300)
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, **kw)


def run_bytes(cmd, **kw):
    timeout = kw.pop('timeout', 300)
    return subprocess.run(cmd, capture_output=True, timeout=timeout, **kw)


def must(cmd, label, **kw):
    p = run(cmd, **kw)
    if p.returncode != 0:
        raise Fail("%s failed:\n%s\n%s\n%s" % (label, ' '.join(cmd),
                                               (p.stdout or '').strip(),
                                               (p.stderr or '').strip()))
    return p


def need(cond, message):
    if not cond:
        raise Fail(message)


def sha256(path):
    import hashlib
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for b in iter(lambda: f.read(1 << 20), b''):
            h.update(b)
    return h.hexdigest()


def load_cinterop_tests():
    spec = importlib.util.spec_from_file_location('h2_cinterop_tests', CINTEROP_TESTS)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def runtime_version():
    p = run(['dpkg-query', '-W', '-f=${Version}', 'libnghttp2-14'])
    if p.returncode != 0:
        raise Fail('libnghttp2-14 is not installed')
    return p.stdout.strip()


def system_headers_ok():
    h = '/usr/include/nghttp2/nghttp2ver.h'
    if not os.path.isfile(h):
        return False
    text = open(h).read()
    return 'NGHTTP2_VERSION_NUM 0x013b00' in text or '0x013b00' in text


def nghttp2_include_dirs():
    """Return a private include root for libnghttp2-dev matching the runtime."""
    if system_headers_ok():
        return ['/usr/include']
    version = runtime_version()
    root = os.path.join(CACHE, version, 'usr', 'include')
    marker = os.path.join(CACHE, version, '.complete')
    if os.path.isfile(marker) and os.path.isfile(os.path.join(root, 'nghttp2', 'nghttp2.h')):
        return [root]
    work = tempfile.mkdtemp(prefix='nghttp2-dev-', dir=TMPROOT)
    try:
        p = run(['apt-get', 'download', 'libnghttp2-dev=' + version], cwd=work,
                timeout=300)
        if p.returncode != 0:
            raise Fail("cannot download libnghttp2-dev=%s:\n%s"
                       % (version, (p.stderr or p.stdout).strip()))
        debs = glob.glob(os.path.join(work, 'libnghttp2-dev_*.deb'))
        need(len(debs) == 1, 'expected one libnghttp2-dev archive')
        pkgver = run(['dpkg-deb', '-f', debs[0], 'Version']).stdout.strip()
        need(pkgver == version, 'libnghttp2-dev version %r != runtime %r'
             % (pkgver, version))
        extracted = os.path.join(work, 'root')
        must(['dpkg-deb', '-x', debs[0], extracted], 'libnghttp2-dev extraction')
        inc = os.path.join(extracted, 'usr', 'include')
        actual = os.path.join(inc, 'nghttp2', 'nghttp2ver.h')
        need(os.path.isfile(actual), 'extracted headers have no nghttp2ver.h')
        text = open(actual).read()
        need('0x013b00' in text, 'extracted nghttp2 headers are not 1.59.0')
        os.makedirs(os.path.dirname(root), exist_ok=True)
        tmp = os.path.join(os.path.dirname(root), 'include.tmp-%d' % os.getpid())
        shutil.rmtree(tmp, ignore_errors=True)
        shutil.copytree(inc, tmp)
        shutil.rmtree(root, ignore_errors=True)
        os.rename(tmp, root)
        open(marker, 'w').write(version + '\n')
        return [root]
    finally:
        shutil.rmtree(work, ignore_errors=True)


def private_cbind(tmp, include_dirs):
    src = os.path.join(PKG, 'http2_adapter.cbind')
    spec = json.load(open(src))
    sys_dir = os.path.join(REPO, 'obo', 'experimental', 'sys')
    tls_dir = os.path.join(REPO, 'obo', 'experimental', 'tls')
    spec['wrapper'] = os.path.join(PKG, 'http2.obx')
    spec['sources'] = [
        os.path.join(PKG, 'native', 'h2_adapter.c'),
        os.path.join(tls_dir, 'native', 'tls_adapter.c'),
        os.path.join(sys_dir, 'native', 'sysregistry.c'),
        os.path.join(sys_dir, 'native', 'sysadapter_real.c'),
        os.path.join(sys_dir, 'native', 'sysnet_real.c'),
    ]
    spec['headers'] = [
        os.path.join(PKG, 'native', 'h2_adapter.h'),
        'nghttp2/nghttp2ver.h',
        'nghttp2/nghttp2.h',
        os.path.join(tls_dir, 'native', 'tls_adapter.h'),
        os.path.join(sys_dir, 'native', 'sysadapter.h'),
        os.path.join(sys_dir, 'native', 'sysnet.h'),
    ]
    spec['include_dirs'] = list(include_dirs)
    spec['cflags'] = ['-pthread', '-Os']
    spec['wrapper_sources'] = [
        os.path.join(PKG, 'http2.obx'),
        os.path.join(PKG, 'http2server.obx'),
        os.path.join(REPO, 'obo', 'experimental', 'httpapi', 'httpapi.obx'),
        os.path.join(REPO, 'obo', 'experimental', 'httptransport', 'httptransport.obx'),
        os.path.join(tls_dir, 'systls.obx'),
    ]
    for rel in ('sysplatform.obx', 'syserrors.obx', 'sysstream.obx',
                'sysbytes.obx', 'sysreactor.obx', 'sysclock.obx', 'sysnet.obx',
                'sysaddress.obx', 'sysfile.obx', 'sysdir.obx', 'sysstat.obx',
                'sysbuffer.obx', 'syswaitio.obx', 'sysresolve.obx',
                'sysrandom.obx'):
        spec['wrapper_sources'].append(os.path.join(sys_dir, rel))
    path = os.path.join(tmp, 'http2.cbind')
    with open(path, 'w') as f:
        json.dump(spec, f, indent=2)
    return path


def cc_command(include_dirs, sanitize=False):
    cc = shutil.which(os.environ.get('CC', 'cc'))
    if not cc:
        raise Fail('C compiler not found')
    cmd = [cc, '-std=c11', '-Wall', '-Wextra', '-Werror', '-fno-pie', '-no-pie',
           '-pthread']
    if sanitize:
        cmd += ['-O1', '-g', '-fsanitize=address,undefined', '-fno-omit-frame-pointer']
    else:
        cmd += ['-O2']
    cmd += ['-I', NATIVE, '-I', SYS_NATIVE]
    for d in include_dirs:
        cmd += ['-I', d]
    cmd += [HARNESS, os.path.join(NATIVE, 'h2_adapter.c'),
            os.path.join(SYS_NATIVE, 'sysregistry.c'),
            os.path.join(SYS_NATIVE, 'sysadapter_real.c'),
            os.path.join(SYS_NATIVE, 'sysnet_real.c'),
            '-l:libnghttp2.so.14']
    return cmd


def build_harness(tmp, include_dirs, sanitize=False):
    exe = os.path.join(tmp, 'h2-harness-asan' if sanitize else 'h2-harness')
    must(cc_command(include_dirs, sanitize) + ['-o', exe],
         'C harness build' + (' (ASAN/UBSAN)' if sanitize else ''))
    return exe


def build_app(tmp, spec, name):
    out = os.path.join(tmp, 'out-' + name)
    os.makedirs(out, exist_ok=True)
    must([sys.executable, TOOLPY, 'build', spec, os.path.join(APPS, name + '.obx'),
          '--out', out, '--obo', OBO], 'obo build ' + name, timeout=300)
    must([sys.executable, TOOLPY, 'verify', os.path.join(out, 'manifest.json')],
         'manifest verify ' + name)
    return os.path.join(out, 'app')


def free_port(preferred):
    for port in (preferred,):
        s = socket.socket()
        try:
            s.bind(('127.0.0.1', port))
            s.close()
            return port
        except OSError:
            s.close()
    s = socket.socket()
    s.bind(('127.0.0.1', 0))
    port = s.getsockname()[1]
    s.close()
    return port


class Server:
    def __init__(self, exe, args, timeout=20, wrap_limit=None):
        if wrap_limit is None:
            argv = [exe] + [str(a) for a in args]
        else:
            argv = ['bash', '-c',
                    'ulimit -n %d; exec "$0" "$@"' % wrap_limit,
                    exe] + [str(a) for a in args]
        self.proc = subprocess.Popen(argv,
                                     stdout=subprocess.PIPE,
                                     stderr=subprocess.PIPE, text=True,
                                     bufsize=1,
                                     env=dict(os.environ, OBO_CHECK='1'))
        self.lines = []
        deadline = time.time() + timeout
        while time.time() < deadline:
            line = self.proc.stdout.readline()
            if line == '':
                out, err = self.proc.communicate(timeout=5)
                raise Fail('server exited before LISTEN:\n%s\n%s' % (out, err))
            self.lines.append(line)
            if line.startswith('H2LISTEN '):
                return
        self.stop()
        raise Fail('server did not print LISTEN')

    def finish(self, timeout=20):
        try:
            out, err = self.proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            out, err = self.proc.communicate()
            raise Fail('server did not exit after graceful shutdown')
        text = ''.join(self.lines) + (out or '') + (err or '')
        if self.proc.returncode != 0 or 'H2ERR' in text or 'H2LOOPERR' in text:
            raise Fail('server reported an error:\n%s' % text)
        self.lines = text.splitlines(True)
        return text

    def stop(self):
        if self.proc.poll() is None:
            self.proc.kill()
        try:
            out, err = self.proc.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            out, err = self.proc.communicate()
        text = ''.join(self.lines) + (out or '') + (err or '')
        if 'H2ERR' in text or 'H2LOOPERR' in text:
            raise Fail('server reported an error:\n%s' % text)


def curl(*args, timeout=30):
    p = run(['curl', '-sS'] + list(args), timeout=timeout)
    if p.returncode != 0:
        raise Fail('curl failed: %s\n%s\n%s' % (' '.join(args), p.stdout, p.stderr))
    return p.stdout


NGHTTP2_HEADER_HASHES = {
    'nghttp2.h': '4eb610b672f7f6520250619330d16ec613c51e4f31c1418db93bfbbce58e50e9',
    'nghttp2ver.h': 'b72d511ddaf0059ad532ec83a9a5b3ec8ccc34968332a9beb016ef301b8d1187',
}


def check_nghttp2_provenance(include_dirs):
    version = runtime_version()
    if version != '1.59.0-1ubuntu0.4':
        print('nghttp2 provenance: non-baseline package %s (header hashes not pinned)' % version)
        return
    for name, want in NGHTTP2_HEADER_HASHES.items():
        found = None
        for root in include_dirs:
            candidate = os.path.join(root, 'nghttp2', name)
            if os.path.isfile(candidate):
                found = candidate
                break
        if found is None:
            raise Fail('pinned nghttp2 header %s not found' % name)
        got = sha256(found)
        if got != want:
            raise Fail('nghttp2 header %s hash %s does not match pinned %s'
                       % (name, got, want))
    print('nghttp2 provenance: pinned 1.59.0-1ubuntu0.4 headers match')


METRIC_FIELDS = [
    'accepted', 'rejected', 'closedConnections', 'requests', 'streamsOpened',
    'streamsClosed', 'protocolErrors', 'timeouts', 'acceptErrors',
    'handlerErrors', 'watchErrors', 'handshakeFailures', 'drains',
    'peakActive', 'active', 'reservedBytes', 'acceptPressure',
]


def parse_metrics(text):
    line = None
    for item in text.splitlines():
        if item.startswith('H2METRICS '):
            line = item
    need(line is not None, 'server printed no H2METRICS')
    values = [int(x) for x in line.split()[1:]]
    need(len(values) == len(METRIC_FIELDS), 'H2METRICS field count changed')
    return dict(zip(METRIC_FIELDS, values))


def parse_counts(text):
    line = None
    for item in text.splitlines():
        if item.startswith('H2COUNTS '):
            line = item
    need(line is not None, 'server printed no H2COUNTS')
    values = [int(x) for x in line.split()[1:]]
    need(len(values) == 5, 'H2COUNTS field count changed')
    return dict(zip(['liveConnections', 'liveStreams', 'closingStreams',
                     'streamsOpened', 'streamsClosed'], values))


def parse_platform(text):
    line = None
    for item in text.splitlines():
        if item.startswith('H2PLATFORM '):
            line = item
    need(line is not None, 'server printed no H2PLATFORM')
    values = [int(x) for x in line.split()[1:]]
    need(len(values) == 4, 'H2PLATFORM field count changed')
    return dict(zip(['resources', 'shells', 'nativeBytes',
                     'cleanupFailures'], values))


def server_cpu(proc):
    parts = open('/proc/%d/stat' % proc.pid).read().split()
    return (int(parts[13]) + int(parts[14])) / os.sysconf('SC_CLK_TCK')


def tls_raw_client(port, authority):
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    ctx.set_alpn_protocols(['h2'])
    raw = socket.create_connection(('127.0.0.1', port), timeout=8)
    sock = ctx.wrap_socket(raw, server_hostname='localhost')
    need(sock.selected_alpn_protocol() == 'h2', 'client ALPN is not h2')
    return h2client.H2Client(authority=authority, sock=sock)


def main():
    if not os.path.exists(OBO):
        raise Fail('no compiler at %s; run make -C obo obo' % OBO)
    tmp = tempfile.mkdtemp(prefix='http2-h2a-', dir=TMPROOT)
    atexit.register(shutil.rmtree, tmp, ignore_errors=True)
    include_dirs = nghttp2_include_dirs()
    check_nghttp2_provenance(include_dirs)
    ctests = load_cinterop_tests()
    include_dirs = include_dirs + ctests.openssl_include_dirs()

    harness = build_harness(tmp, include_dirs)
    p = run([harness], timeout=120, env=dict(os.environ, OBO_CHECK='1'))
    need(p.returncode == 0 and 'ok all' in p.stdout,
         'combined C harness failed:\n%s\n%s' % (p.stdout, p.stderr))
    print('C shim harness: ok')

    asan = build_harness(tmp, include_dirs, sanitize=True)
    env = dict(os.environ, OBO_CHECK='1',
               ASAN_OPTIONS='detect_leaks=1:abort_on_error=1',
               UBSAN_OPTIONS='halt_on_error=1')
    p = run([asan], timeout=180, env=env)
    need(p.returncode == 0 and 'ok all' in p.stdout,
         'ASAN/UBSAN C harness failed:\n%s\n%s' % (p.stdout, p.stderr))
    print('C shim ASAN/UBSAN/LSAN: ok')

    spec = private_cbind(tmp, include_dirs)
    mem = build_app(tmp, spec, 'h2_mem_driver')
    h2c = build_app(tmp, spec, 'h2c_server')
    tls = build_app(tmp, spec, 'h2tls_server')
    h2srv = build_app(tmp, spec, 'h2server')

    # Pure API checks.
    p = run([mem, 'unit'], timeout=60, env=dict(os.environ, OBO_CHECK='1'))
    need(p.returncode == 0, 'unit driver failed:\n%s\n%s' % (p.stdout, p.stderr))
    for want in ('UNIT limits-ok', 'UNIT reservation 4009984',
                 'UNIT bad-rejected', 'UNIT admitted 14 active 14',
                 'UNIT released 0 remaining 0', 'UNIT cleartext-rejected',
                 'UNIT alpn-accepted'):
        need(want in p.stdout, 'unit output missing %r:\n%s' % (want, p.stdout))
    print('obo limits/admission/mode: ok')

    p = run([mem, 'adoptfail'], timeout=60, env=dict(os.environ, OBO_CHECK='1'))
    need(p.returncode == 0, 'adoptfail driver failed:\n%s\n%s' % (p.stdout, p.stderr))
    need('ADOPT rejected' in p.stdout and 'unexpectedly-adopted' not in p.stdout,
         'adoption failure was not handled:\n%s' % p.stdout)
    print('obo adoption failure/idempotent abandon: ok')

    p = run([mem, 'badbody'], timeout=60, env=dict(os.environ, OBO_CHECK='1'))
    need(p.returncode == 0 and 'BADREQ 33554432' in p.stdout,
         'maxBody was not enforced:\n%s' % p.stdout)
    p = run([mem, 'badhost'], timeout=60, env=dict(os.environ, OBO_CHECK='1'))
    need(p.returncode == 0 and 'BADREQ 167772160' in p.stdout,
         'Host/:authority mismatch was not rejected:\n%s' % p.stdout)
    print('obo maxBody and Host/:authority policy: ok')

    p = run([mem, 'prefacetimeout'], timeout=60, env=dict(os.environ, OBO_CHECK='1'))
    need(p.returncode == 0 and 'PREFACE timeout' in p.stdout,
         'preface timeout did not surface EventTimedOut cleanly:\n%s' % p.stdout)
    print('obo preface timeout: ok')

    # Memory transport response path, verified by the nghttp2 client harness.
    for mode in ('hello', 'trailers'):
        out = os.path.join(tmp, 'mem-%s.wire' % mode)
        p = run_bytes([mem, mode], timeout=60, env=dict(os.environ, OBO_CHECK='1'))
        need(p.returncode == 0, 'memory driver %s failed:\n%s\n%s'
             % (mode, p.stdout, p.stderr))
        open(out, 'wb').write(p.stdout)
        v = run([harness, 'verify', out], timeout=60)
        need(v.returncode == 0 and 'status=200 body=6' in v.stdout,
             'memory response verification failed:\n%s\n%s' % (v.stdout, v.stderr))
        need('hello' in v.stdout, 'memory response body missing')
        if mode == 'trailers':
            need('trailer=ok' in v.stdout, 'response trailer missing:\n%s' % v.stdout)
    p = run_bytes([mem, 'reset'], timeout=60, env=dict(os.environ, OBO_CHECK='1'))
    need(p.returncode == 0, 'reset driver failed:\n%s\n%s' % (p.stdout, p.stderr))
    raw = p.stdout
    need(b'\x00\x00\x04\x03' in raw or b'\x00\x00\x00\x03' in raw,
         'memory reset did not emit RST_STREAM')
    print('obo memory transport/stream/response/trailer/reset: ok')

    # h2c prior-knowledge interop.
    port = free_port(1744)
    srv = Server(h2c, [port, 10])
    try:
        got = curl('--http2-prior-knowledge', 'http://127.0.0.1:%d/hello' % port)
        need(got == 'hello\n', 'h2c hello body %r' % got)
        got = curl('--http2-prior-knowledge', 'http://127.0.0.1:%d/big' % port)
        need(len(got) == 20480, 'h2c big length %d' % len(got))
        got = curl('--http2-prior-knowledge', '-I', '-o', '/dev/null',
                   'http://127.0.0.1:%d/head' % port)
        need(got == '', 'h2c HEAD body %r' % got)
        got = curl('--http2-prior-knowledge', 'http://127.0.0.1:%d/204' % port)
        need(got == '', 'h2c 204 body %r' % got)
        got = curl('--http2-prior-knowledge', 'http://127.0.0.1:%d/304' % port)
        need(got == '', 'h2c 304 body %r' % got)
        got = curl('--http2-prior-knowledge', '-d', 'abcdef',
                   'http://127.0.0.1:%d/echo' % port)
        need(got == 'abcdef', 'h2c echo body %r' % got)
        got = curl('--http2-prior-knowledge', 'http://127.0.0.1:%d/slow' % port)
        need(got == 'slow body\n', 'h2c slow body %r' % got)
        got = curl('--http2-prior-knowledge',
                   'http://127.0.0.1:%d/hello' % port,
                   'http://127.0.0.1:%d/hello' % port)
        need(got == 'hello\nhello\n', 'h2c connection reuse body %r' % got)
    finally:
        srv.stop()
    print('h2c prior-knowledge curl interop: ok')

    # H2b/H2c reactor server: multiplexed streams, flow control, abuse
    # isolation and graceful shutdown on a cleartext prior-knowledge listener.
    port = free_port(1744)
    srv = Server(h2srv, ['h2c', port])
    try:
        need(curl('--http2-prior-knowledge',
                  'http://127.0.0.1:%d/hello' % port) == 'hello\n',
             'H2b h2c hello failed')
        need(len(curl('--http2-prior-knowledge',
                      'http://127.0.0.1:%d/big' % port)) == 20480,
             'H2b h2c big failed')
        curl('--http2-prior-knowledge', '-I', '-o', '/dev/null',
             'http://127.0.0.1:%d/head' % port)
        need(curl('--http2-prior-knowledge',
                  'http://127.0.0.1:%d/204' % port) == '', 'H2b 204')
        need(curl('--http2-prior-knowledge',
                  'http://127.0.0.1:%d/304' % port) == '', 'H2b 304')
        need(curl('--http2-prior-knowledge', '-d', 'abcdef',
                  'http://127.0.0.1:%d/echo' % port) == 'abcdef',
             'H2b echo failed')
        need(curl('--http2-prior-knowledge',
                  'http://127.0.0.1:%d/chunked' % port) ==
             'chunk one\nchunk two\nchunk three\n', 'H2b chunked failed')
        need(curl('--http2-prior-knowledge',
                  'http://127.0.0.1:%d/hello' % port,
                  'http://127.0.0.1:%d/hello' % port) ==
             'hello\nhello\n', 'H2b connection reuse failed')

        # Multiplexed concurrent streams on one connection.
        c = h2client.H2Client(port)
        ids = [1, 3, 5, 7, 9, 11, 13, 15]
        paths = ['/hello', '/big', '/slow', '/chunked',
                 '/204', '/304', '/hello', '/head']
        for sid, path in zip(ids, paths):
            c.request(sid, path)
        c.collect(ids, timeout=10)
        need(c.body(1) == b'hello\n', 'multiplex hello body')
        need(len(c.body(3)) == 20480, 'multiplex big body')
        need(c.body(5) == b'slow body\n', 'multiplex slow body')
        need(c.body(7) == b'chunk one\nchunk two\nchunk three\n',
             'multiplex chunked body')
        need(c.body(9) == b'' and c.body(11) == b'',
             'multiplex empty-body statuses')
        need(c.body(13) == b'hello\n' and c.body(15) == b'',
             'multiplex later streams')
        need(c.goaway is None, 'multiplex connection received GOAWAY')
        c.close()
        print('H2b multiplexed concurrent streams: ok')

        # Flow control/backpressure across many streams and a 30 KiB echo.
        c = h2client.H2Client(port)
        ids = list(range(1, 17, 2))
        for sid in ids:
            c.request(sid, '/big')
        c.collect(ids, timeout=15)
        need(all(len(c.body(sid)) == 20480 for sid in ids),
             'flow-control /big bodies incomplete')
        need(sum(len(c.body(sid)) for sid in ids) > 65535,
             'flow-control test did not exceed the initial window')
        c.close()
        c = h2client.H2Client(port)
        payload = b'x' * 30000
        c.request(1, '/echo', method='POST', body=payload)
        c.collect({1}, timeout=10)
        need(c.body(1) == payload, 'flow-control echo body mismatch')
        c.close()
        print('H2b flow control/backpressure: ok')

        # Concurrent-stream cap: exactly 32 complete; a 33-stream burst is
        # isolated to its connection and the server keeps serving.
        c = h2client.H2Client(port)
        ids = list(range(1, 65, 2))
        for sid in ids:
            c.request(sid, '/hello')
        c.collect(ids, timeout=10)
        need(sum(1 for sid in ids if c.body(sid) == b'hello\n') == 32,
             'maxConcurrentStreams did not complete 32 streams')
        c.close()
        c = h2client.H2Client(port)
        ids = list(range(1, 67, 2))
        for sid in ids:
            c.request(sid, '/hello')
        c.collect(ids, timeout=5)
        need(c.goaway is not None or c.reset,
             '33-stream burst was neither refused nor drained')
        c.close()
        c = h2client.H2Client(port)
        c.request(1, '/hello')
        c.collect({1}, timeout=5)
        need(c.body(1) == b'hello\n', 'server did not survive stream cap')
        c.close()
        print('H2b max streams/connection limits: ok')

        # Rapid reset (CVE-2023-44487 shape): the connection is bounded and
        # closed/drained; a fresh connection still works.
        c = h2client.H2Client(port)
        for i in range(130):
            sid = 2 * i + 1
            c.headers_without_end(sid, '/echo')
            c.reset_stream(sid, 8)
        c.drain_for(2)
        need(c.goaway is not None or c.closed, 'rapid reset was not bounded')
        c.close()
        c = h2client.H2Client(port)
        c.request(1, '/hello')
        c.collect({1}, timeout=5)
        need(c.body(1) == b'hello\n', 'server did not survive rapid reset')
        c.close()
        print('H2c rapid reset isolation: ok')

        # CONTINUATION flood trips the continuation/frame budget.
        c = h2client.H2Client(port)
        c.continuation_flood(1, 20)
        c.drain_for(2)
        need(c.goaway is not None or c.closed,
             'CONTINUATION flood was not bounded')
        c.close()
        c = h2client.H2Client(port)
        c.request(1, '/hello')
        c.collect({1}, timeout=5)
        need(c.body(1) == b'hello\n', 'server did not survive CONTINUATION flood')
        c.close()
        print('H2c CONTINUATION flood: ok')

        # Header-list flood is refused at stream scope; connection survives.
        c = h2client.H2Client(port)
        c.header_flood(1, 140)
        c.collect({1}, timeout=5)
        need(1 in c.reset or c.goaway is not None,
             'header-list flood was not refused')
        c.close()
        c = h2client.H2Client(port)
        c.request(1, '/hello')
        c.collect({1}, timeout=5)
        need(c.body(1) == b'hello\n', 'server did not survive header flood')
        c.close()
        print('H2c header/HPACK caps: ok')

        # Zero-length DATA flood trips the zero-DATA budget.
        c = h2client.H2Client(port)
        c.headers_without_end(1, '/echo')
        c.empty_data(1, 300)
        c.drain_for(2)
        need(c.goaway is not None or c.closed, 'zero-DATA flood was not bounded')
        c.close()
        c = h2client.H2Client(port)
        c.request(1, '/hello')
        c.collect({1}, timeout=5)
        need(c.body(1) == b'hello\n', 'server did not survive zero-DATA flood')
        c.close()
        print('H2c zero-DATA/frame budget: ok')

        # h2c policy: no HTTP/1.1 Upgrade, and a bad preface closes only the
        # offending connection.
        p = run(['curl', '-sS', '--max-time', '5', '--http2',
                 'http://127.0.0.1:%d/hello' % port])
        need(p.returncode != 0, 'h2c Upgrade unexpectedly succeeded')
        bad = socket.create_connection(('127.0.0.1', port), timeout=5)
        bad.sendall(b'GET / HTTP/1.1\r\nHost: x\r\n\r\n')
        dead = False
        try:
            dead = bad.recv(16) == b''
        except OSError:
            dead = True
        bad.close()
        need(dead, 'bad h2c preface was not closed')
        c = h2client.H2Client(port)
        c.request(1, '/hello')
        c.collect({1}, timeout=5)
        need(c.body(1) == b'hello\n', 'server did not survive bad preface')
        c.close()
        print('H2c h2c policy/preface isolation: ok')

        # Graceful shutdown: GOAWAY drain, no live resources, zero counts.
        need(curl('--http2-prior-knowledge',
                  'http://127.0.0.1:%d/stop' % port) == 'hello\n',
             'stop request body')
        text = srv.finish()
        need('H2DRAINED' in text and 'H2DONE' in text,
             'graceful drain did not complete:\n%s' % text)
        m = parse_metrics(text)
        c = parse_counts(text)
        plat = parse_platform(text)
        need(m['active'] == 0 and m['accepted'] == m['closedConnections'],
             'H2 server connection accounting is not balanced: %r' % m)
        need(m['streamsOpened'] == m['streamsClosed'],
             'H2 server stream accounting is not balanced: %r' % m)
        need(m['protocolErrors'] > 0, 'abuse tests produced no protocol errors')
        need(c['liveConnections'] == 0 and c['liveStreams'] == 0 and
             c['closingStreams'] == 0, 'H2 global counts leaked: %r' % c)
        need(c['streamsOpened'] == c['streamsClosed'],
             'H2 global stream counts are not balanced: %r' % c)
        need(plat['cleanupFailures'] == 0,
             'H2 platform cleanup failures: %r' % plat)
        print('H2c graceful shutdown/accounting: ok')
    finally:
        srv.stop()

    # Fast-timeout profile: idle, stalled header block and body-idle deadlines
    # close only the offending connection; the loop keeps serving.
    port = free_port(1746)
    srv = Server(h2srv, ['h2c', port, 'fast'])
    try:
        silent = [socket.create_connection(('127.0.0.1', port), timeout=3)
                  for _ in range(14)]
        time.sleep(1.4)
        closed = 0
        for raw in silent:
            raw.settimeout(1)
            try:
                if raw.recv(16) == b'':
                    closed += 1
            except OSError:
                pass
        for raw in silent:
            raw.close()
        need(closed == 14,
             'silent h2c prefaces held slots past the armed deadline: %d' %
             closed)

        c = h2client.H2Client(port)
        c.drain_for(1.6)
        need(c.goaway is not None or c.closed, 'idle timeout did not close')
        c.close()

        c = h2client.H2Client(port)
        c.stall_headers(1)
        c.drain_for(1.0)
        need(c.goaway is not None or c.closed or 1 in c.reset,
             'header timeout did not bound a stalled HEADERS block')
        c.close()

        c = h2client.H2Client(port)
        c.stalled_body(1)
        c.drain_for(1.0)
        need(c.goaway is not None or c.closed or 1 in c.reset,
             'body-idle timeout did not bound a paused body')
        c.close()

        c = h2client.H2Client(port)
        c.request(1, '/hello')
        c.collect({1}, timeout=5)
        need(c.body(1) == b'hello\n', 'server did not survive timeout tests')
        c.close()

        need(curl('--http2-prior-knowledge',
                  'http://127.0.0.1:%d/stop' % port) == 'hello\n',
             'fast-profile stop body')
        text = srv.finish()
        need('H2DRAINED' in text and 'H2DONE' in text,
             'fast-profile drain did not complete:\n%s' % text)
        m = parse_metrics(text)
        c = parse_counts(text)
        need(m['timeouts'] >= 3, 'timeout metrics were not recorded: %r' % m)
        need(m['active'] == 0 and c['liveStreams'] == 0,
             'timeout profile left live resources: %r %r' % (m, c))
        print('H2b idle/header/body timeout isolation: ok')
    finally:
        srv.stop()

    # Graceful drain of an active stream: the notice GOAWAY, barrier PING and
    # final GOAWAY with min(last_proc, cutoff) must reach the wire before close.
    port = free_port(1747)
    srv = Server(h2srv, ['h2c', port])
    try:
        a = h2client.H2Client(port)
        a.request(1, '/echo', method='POST', body=b'p', end_stream=False)
        time.sleep(0.15)
        b = h2client.H2Client(port)
        b.request(1, '/stop')
        b.collect({1}, timeout=5)
        need(b.body(1) == b'hello\n', 'drain stop body')
        deadline = time.time() + 5
        a._deadline = deadline
        sent_tail = False
        while time.time() < deadline:
            if not sent_tail and (h2client.GOAWAY in a.frames or
                                  time.time() > deadline - 4.5):
                a.send(h2client.frame(h2client.DATA,
                                      h2client.FLAG_END_STREAM, 1, b'tail'))
                sent_tail = True
            try:
                fr = a._recv_frame()
            except socket.timeout:
                continue
            if fr is None:
                break
            a._handle(fr)
            if 1 in a.ended and len(a.goaways) >= 2:
                break
        need(a.body(1) == b'ptail', 'active stream drain lost the echo body')
        need(1 in a.ended, 'active stream did not finish during drain')
        need(len(a.goaways) >= 2,
             'drain did not send a notice and final GOAWAY: %r' % a.goaways)
        need(a.goaways[0][0] == 2147483647,
             'first GOAWAY was not the shutdown notice: %r' % a.goaways)
        final = a.goaways[-1]
        need(final[0] == 1 and final[1] == 0,
             'final GOAWAY was not min(last_proc, cutoff)=1/NO_ERROR: %r' %
             a.goaways)
        pings = [i for i, t in enumerate(a.frames) if t == h2client.PING]
        goaways = [i for i, t in enumerate(a.frames) if t == h2client.GOAWAY]
        need(pings and goaways and goaways[-1] > pings[-1],
             'final GOAWAY did not follow the drain barrier PING')
        a.close()
        b.close()
        text = srv.finish()
        need('H2DRAINED' in text and 'H2DONE' in text,
             'active-stream drain did not complete:\n%s' % text)
        m = parse_metrics(text)
        c = parse_counts(text)
        need(m['active'] == 0 and c['liveStreams'] == 0,
             'active-stream drain left live resources: %r %r' % (m, c))
        print('H2c final GOAWAY order/active-stream drain: ok')
    finally:
        srv.stop()

    # Admission saturation: with maxConnections reached, the listener watch is
    # paused instead of waking on a permanently readable backlog.
    port = free_port(1748)
    srv = Server(h2srv, ['h2c', port])
    pending = None
    clients = []
    try:
        clients = [h2client.H2Client(port) for _ in range(14)]
        time.sleep(0.2)
        base = server_cpu(srv.proc)
        time.sleep(0.8)
        need(server_cpu(srv.proc) - base < 0.2,
             'saturated admission spins before a pending accept')
        pending = socket.create_connection(('127.0.0.1', port), timeout=3)
        pending.sendall(h2client.PREFACE + h2client.frame(h2client.SETTINGS, 0, 0))
        base = server_cpu(srv.proc)
        time.sleep(1.5)
        used = server_cpu(srv.proc) - base
        need(used < 0.25,
             'saturated admission spins with a pending accept: %.2fs' % used)
        clients[0].request(1, '/hello')
        clients[0].collect({1}, timeout=5)
        need(clients[0].body(1) == b'hello\n',
             'existing connection failed while admission was full')
        clients[1].request(1, '/stop')
        clients[1].collect({1}, timeout=8)
        need(clients[1].body(1) == b'hello\n', 'saturated stop body')
        for c in clients:
            c.close()
        clients = []
        pending.close()
        pending = None
        text = srv.finish()
        need('H2DRAINED' in text and 'H2DONE' in text,
             'saturated server did not drain:\n%s' % text)
        m = parse_metrics(text)
        need(m['accepted'] == 14, 'saturation accepted count: %r' % m)
        need(m['rejected'] <= 4, 'saturation rejected count exploded: %r' % m)
        need(m['active'] == 0 and m['reservedBytes'] == 0,
             'saturation admission did not unwind: %r' % m)
        print('H2c admission saturation/watch pause: ok')
    finally:
        if pending is not None:
            pending.close()
        for c in clients:
            c.close()
        srv.stop()

    # Non-default buffers/read chunk/request cap: progress must follow a
    # zero-count writeBody, and a maxRequests refusal must emit RST_STREAM.
    port = free_port(1749)
    srv = Server(h2srv, ['h2c', port, 'small'])
    try:
        c = h2client.H2Client(port)
        c.request(1, '/big')
        c.collect({1}, timeout=10)
        need(c.body(1) == b'x' * 20480,
             'small send buffer stalled /big at %d bytes' % len(c.body(1)))
        c.close()

        c = h2client.H2Client(port)
        payload = b'z' * 10000
        c.request(1, '/echo', method='POST', body=payload)
        c.collect({1}, timeout=10)
        need(c.body(1) == payload, 'small maxReadChunk echo failed')
        c.close()

        c = h2client.H2Client(port)
        for sid in (1, 3):
            c.request(sid, '/hello')
            c.collect({sid}, timeout=5)
            need(c.body(sid) == b'hello\n', 'small request cap first two')
        c.request(5, '/hello')
        c.collect({5}, timeout=5)
        need(c.reset.get(5) == 7,
             'maxRequests refusal did not send REFUSED_STREAM: %r' % c.reset)
        c.close()

        d = h2client.H2Client(port)
        d.request(1, '/stop')
        d.collect({1}, timeout=8)
        d.close()
        text = srv.finish()
        need('H2DRAINED' in text and 'H2DONE' in text,
             'small-profile drain did not complete:\n%s' % text)
        m = parse_metrics(text)
        c = parse_counts(text)
        need(m['active'] == 0 and c['liveStreams'] == 0,
             'small profile left live resources: %r %r' % (m, c))
        print('H2b small buffers/maxRequests: ok')
    finally:
        srv.stop()

    # Response flow-control backpressure: request ten /big streams (204800
    # bytes total) and withhold connection WINDOW_UPDATE. The server must sleep
    # on readiness/deadline instead of busy-retrying blocked writes; one
    # connection WINDOW_UPDATE must resume every stream.
    port = free_port(1750)
    srv = Server(h2srv, ['h2c', port])
    try:
        c = h2client.H2Client(port, auto_window=False)
        ids = list(range(1, 21, 2))
        for sid in ids:
            c.request(sid, '/big')
        c.drain_for(0.5)
        seen = sum(len(c.body(sid)) for sid in ids)
        need(seen > 0, 'slow-reader received no response bytes')
        base = server_cpu(srv.proc)
        time.sleep(2.0)
        used = server_cpu(srv.proc) - base
        need(used < 0.3,
             'blocked response write busy-spins: %.2fs over 2s' % used)
        c.grant(0, 300000)
        c.collect(set(ids), timeout=10)
        need(all(c.body(sid) == b'x' * 20480 for sid in ids),
             'WINDOW_UPDATE did not resume all blocked responses')
        c.close()

        # The same blocked-write rule on the Echo path: send a 100 KiB body,
        # let the response fill the withheld window, then grant credit.
        c = h2client.H2Client(port, auto_window=False)
        payload = b'q' * 100000
        need(c.post_flow_controlled(1, payload),
             'slow-reader request body was not accepted')
        seen = len(c.body(1))
        need(seen > 0 and seen < len(payload),
             'echo response was not held by flow control: %d' % seen)
        base = server_cpu(srv.proc)
        time.sleep(1.5)
        used = server_cpu(srv.proc) - base
        need(used < 0.3,
             'blocked echo write busy-spins: %.2fs over 1.5s' % used)
        c.grant(1, 300000)
        c.grant(0, 300000)
        c.collect({1}, timeout=10)
        need(c.body(1) == payload,
             'WINDOW_UPDATE did not resume the blocked echo')
        c.close()
        d = h2client.H2Client(port)
        d.request(1, '/stop')
        d.collect({1}, timeout=8)
        d.close()
        text = srv.finish()
        need('H2DRAINED' in text and 'H2DONE' in text,
             'slow-reader server did not drain:\n%s' % text)
        m = parse_metrics(text)
        need(m['active'] == 0 and m['reservedBytes'] == 0,
             'slow-reader left live admission: %r' % m)
        print('H2b response flow-control backpressure: ok')
    finally:
        srv.stop()

    # Accept-pressure backoff under a low fd limit: EMFILE must pause the
    # listener (no readable-backlog spin) and recover once descriptors return.
    port = free_port(1751)
    srv = Server(h2srv, ['h2c', port], wrap_limit=12)
    socks = []
    try:
        for _ in range(30):
            try:
                socks.append(socket.create_connection(('127.0.0.1', port),
                                                      timeout=3))
            except OSError:
                break
            time.sleep(0.01)
        need(len(socks) > 0, 'low-fd test opened no sockets')
        time.sleep(0.4)
        base = server_cpu(srv.proc)
        time.sleep(2.0)
        used = server_cpu(srv.proc) - base
        need(used < 0.3,
             'EMFILE accept backoff busy-spins: %.2fs over 2s' % used)
        for raw in socks:
            raw.close()
        socks = []
        time.sleep(0.5)
        c = h2client.H2Client(port)
        c.request(1, '/hello')
        c.collect({1}, timeout=5)
        need(c.body(1) == b'hello\n', 'low-fd server did not recover')
        c.close()
        d = h2client.H2Client(port)
        d.request(1, '/stop')
        d.collect({1}, timeout=8)
        d.close()
        text = srv.finish()
        need('H2DRAINED' in text and 'H2DONE' in text,
             'low-fd server did not drain:\n%s' % text)
        m = parse_metrics(text)
        need(m['acceptPressure'] >= 1,
             'acceptPressure was not recorded: %r' % m)
        need(m['active'] == 0 and m['reservedBytes'] == 0,
             'low-fd recovery left live admission: %r' % m)
        print('H2c EMFILE accept-pressure pause/recovery: ok')
    finally:
        for raw in socks:
            raw.close()
        srv.stop()


    # TLS ALPN h2 interop.
    certdir = os.path.join(tmp, 'certs')
    os.makedirs(certdir)
    must(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes',
          '-keyout', os.path.join(certdir, 'key.pem'),
          '-out', os.path.join(certdir, 'cert.pem'), '-days', '1',
          '-subj', '/CN=localhost',
          '-addext', 'subjectAltName=DNS:localhost,IP:127.0.0.1'],
         'certificate generation')
    port = free_port(1549)
    srv = Server(tls, [port, 4, os.path.join(certdir, 'cert.pem'),
                       os.path.join(certdir, 'key.pem')])
    try:
        got = curl('-k', '--http2', 'https://127.0.0.1:%d/hello' % port)
        need(got == 'hello\n', 'TLS h2 hello body %r' % got)
        got = curl('-k', '--http2', '-d', 'abcdef',
                   'https://127.0.0.1:%d/echo' % port)
        need(got == 'abcdef', 'TLS h2 echo body %r' % got)
    finally:
        srv.stop()
    print('TLS ALPN h2 curl interop: ok')

    # H2c TLS reactor server: ALPN enforcement, multiplexing over TLS and
    # graceful shutdown. A client offering only http/1.1 must fail the
    # handshake; there is no downgrade.
    port = free_port(1549)
    srv = Server(h2srv, ['tls', port, os.path.join(certdir, 'cert.pem'),
                         os.path.join(certdir, 'key.pem')])
    try:
        need(curl('-k', '--http2', 'https://127.0.0.1:%d/hello' % port) ==
             'hello\n', 'H2c TLS hello failed')
        need(curl('-k', '--http2', '-d', 'abcdef',
                  'https://127.0.0.1:%d/echo' % port) == 'abcdef',
             'H2c TLS echo failed')
        wrong = run(['curl', '-k', '-sS', '--max-time', '5', '--http1.1',
                     'https://127.0.0.1:%d/hello' % port])
        need(wrong.returncode != 0, 'TLS server accepted ALPN http/1.1')

        c = tls_raw_client(port, '127.0.0.1:%d' % port)
        ids = [1, 3, 5, 7]
        for sid in ids:
            c.request(sid, '/hello')
        c.collect(ids, timeout=10)
        need(all(c.body(sid) == b'hello\n' for sid in ids),
             'TLS multiplexed bodies mismatch')
        c.close()

        need(curl('-k', '--http2',
                  'https://127.0.0.1:%d/stop' % port) == 'hello\n',
             'TLS stop request body')
        text = srv.finish()
        need('H2DRAINED' in text and 'H2DONE' in text,
             'TLS graceful drain did not complete:\n%s' % text)
        m = parse_metrics(text)
        c = parse_counts(text)
        plat = parse_platform(text)
        need(m['active'] == 0 and m['accepted'] == m['closedConnections'],
             'TLS connection accounting is not balanced: %r' % m)
        need(m['streamsOpened'] == m['streamsClosed'],
             'TLS stream accounting is not balanced: %r' % m)
        need(m['handshakeFailures'] >= 1,
             'TLS server did not record the wrong-ALPN failure')
        need(c['liveConnections'] == 0 and c['liveStreams'] == 0 and
             c['closingStreams'] == 0, 'TLS H2 counts leaked: %r' % c)
        need(plat['cleanupFailures'] == 0,
             'TLS platform cleanup failures: %r' % plat)
        print('H2c TLS ALPN + multiplex + graceful shutdown: ok')
    finally:
        srv.stop()


    h2spec = shutil.which('h2spec')
    h2load = shutil.which('h2load')
    if h2spec is None:
        print('h2spec: not installed (manual pinned target v2.6.0, '
              'h2spec_linux_amd64.tar.gz; no automatic fetch). Manual h2c: '
              'h2spec -h 127.0.0.1 -p <port> -P /')
    else:
        print('h2spec: present at %s (manual target; the deterministic suite '
              'does not run external protocol tools)' % h2spec)
    if h2load is None:
        print('h2load: not installed (Ubuntu package nghttp2-client; manual '
              'integration: h2load -n 1000 -c 10 -m 10 http://127.0.0.1:<port>/)')
    else:
        print('h2load: present at %s (manual target; not run automatically)'
              % h2load)
    print('H2a/H2b/H2c tests: all ok')


if __name__ == '__main__':
    try:
        main()
    except Fail as e:
        print('FAIL:', e, file=sys.stderr)
        sys.exit(1)
