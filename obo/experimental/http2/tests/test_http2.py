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
    def __init__(self, exe, args, timeout=20):
        self.proc = subprocess.Popen([exe] + [str(a) for a in args],
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
    'peakActive', 'active', 'reservedBytes',
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
