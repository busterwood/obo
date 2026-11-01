#!/usr/bin/env python3
"""H1c tests: HTTPS integration over the plain H1b connection/server loop.

    python3 obo/experimental/http1/tests/test_http1c.py

Builds the H1c HTTPS loopback app through the cinterop TLS adapter and drives
it with Python's ssl module and curl. Covers TLS 1.2/1.3, ALPN http/1.1 and h2
rejection, no-ALPN policy, handshake timeout and byte-at-a-time ClientHello
fragmentation, keep-alive/pipelining, slowloris, close_notify, large/partial
writes, graceful shutdown, optional client certificates and plaintext
injection on a TLS port. HTTP/2, HTTP/3 and external network access are out of
scope. OpenSSL headers are located privately, exactly as the P2T-b suite does.
"""
import atexit
import importlib.util
import os
import select
import shutil
import socket
import ssl
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.dirname(HERE)
OBO_ROOT = os.path.dirname(os.path.dirname(PKG))
REPO = os.path.dirname(OBO_ROOT)
OBO = os.path.abspath(os.environ.get('OBO', os.path.join(OBO_ROOT, 'obo')))
TOOLPY = os.path.join(REPO, 'obo', 'experimental', 'cinterop', 'cinterop.py')
APPS = os.path.join(HERE, 'apps')
TLS_TESTS = os.path.join(REPO, 'obo', 'experimental', 'tls', 'tests',
                         'test_tls.py')
TMP = tempfile.mkdtemp(prefix='http1-h1c-', dir='/tmp/opencode')
atexit.register(shutil.rmtree, TMP, ignore_errors=True)

MODULES = ['http1core.obx', 'http1parse.obx', 'http1write.obx',
           'http1date.obx', 'http1io.obx', 'http1conn.obx',
           'http1server.obx']


class Fail(Exception):
    pass


def need(cond, message):
    if not cond:
        raise Fail(message)


def run(cmd, **kw):
    timeout = kw.pop('timeout', 600)
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                          **kw)


def load_tls_tests():
    spec = importlib.util.spec_from_file_location('h1c_tls_tests', TLS_TESTS)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def build_app():
    tls = load_tls_tests()
    cinterop = tls.load_cinterop_tests()
    include_dirs = cinterop.openssl_include_dirs()
    cb = os.path.join(TMP, 'cbind')
    os.makedirs(cb, exist_ok=True)
    spec = tls.private_cbind(cb, include_dirs)
    appdir = os.path.join(TMP, 'app')
    out = os.path.join(TMP, 'out')
    os.makedirs(appdir)
    os.makedirs(out, exist_ok=True)
    for name in MODULES:
        shutil.copyfile(os.path.join(PKG, name), os.path.join(appdir, name))
    shutil.copyfile(os.path.join(APPS, 'h1c_server_app.obx'),
                    os.path.join(appdir, 'h1c_server_app.obx'))
    p = run([sys.executable, TOOLPY, 'build', spec,
             os.path.join(appdir, 'h1c_server_app.obx'), '--out', out,
             '--obo', OBO])
    if p.returncode != 0:
        raise Fail('could not build H1c app:\n%s\n%s'
                   % ((p.stdout or '')[-3000:], (p.stderr or '')[-3000:]))
    return os.path.join(out, 'app')


class Server:
    def __init__(self, exe, fixtures, mode='alpn', profile='default'):
        self.fixtures = fixtures
        self.mode = mode
        self.profile = profile
        self.proc = subprocess.Popen(
            [exe, '0', mode, os.path.join(fixtures, 'server.pem'),
             os.path.join(fixtures, 'server.key'),
             os.path.join(fixtures, 'ca.pem'), profile],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            bufsize=1, env=dict(os.environ, OBO_CHECK='1'))
        self.port = None
        line = self._readline(10)
        need(line is not None, 'server produced no PORT line')
        line = line.strip()
        need(line.startswith('PORT '), 'bad server line: ' + line)
        self.port = int(line.split()[1])

    def _readline(self, timeout):
        r, _, _ = select.select([self.proc.stdout], [], [], timeout)
        if not r:
            return None
        return self.proc.stdout.readline()

    def connect_raw(self, timeout=8):
        s = socket.create_connection(('127.0.0.1', self.port), timeout=timeout)
        return s

    def context(self, version='1.3', alpn=('http/1.1',), cert=None, key=None,
                verify=False):
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        if verify:
            ctx.load_verify_locations(os.path.join(self.fixtures, 'ca.pem'))
            ctx.check_hostname = True
        else:
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
        if version == '1.2':
            ctx.minimum_version = ssl.TLSVersion.TLSv1_2
            ctx.maximum_version = ssl.TLSVersion.TLSv1_2
        if version == '1.3':
            ctx.minimum_version = ssl.TLSVersion.TLSv1_3
            ctx.maximum_version = ssl.TLSVersion.TLSv1_3
        if alpn is not None:
            ctx.set_alpn_protocols(list(alpn))
        if cert is not None:
            ctx.load_cert_chain(cert, key)
        return ctx

    def tls(self, version='1.3', alpn=('http/1.1',), cert=None, key=None,
            verify=False, timeout=8):
        raw = self.connect_raw(timeout)
        ctx = self.context(version, alpn, cert, key, verify)
        return ctx.wrap_socket(raw, server_hostname='localhost')

    def quit(self, timeout=15, cert=None, key=None):
        try:
            ss = self.tls(alpn=('http/1.1',), cert=cert, key=key)
            ss.sendall(b'GET /quit HTTP/1.1\r\nHost: localhost\r\n'
                       b'Connection: close\r\n\r\n')
            recv_http(ss)
            try:
                ss.unwrap()
            except (ssl.SSLError, OSError):
                pass
            ss.close()
        except Exception:
            pass
        try:
            out = self.proc.communicate(timeout=timeout)[0] or ''
        except subprocess.TimeoutExpired:
            self.proc.kill()
            out = self.proc.communicate()[0] or ''
        return out

    def stop(self):
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait()


def recv_until(sock, marker, timeout=8):
    sock.settimeout(timeout)
    data = b''
    while marker not in data:
        part = sock.recv(65536)
        if not part:
            break
        data += part
    return data


def recv_http(sock):
    data = recv_until(sock, b'\r\n\r\n')
    head, _, rest = data.partition(b'\r\n\r\n')
    length = 0
    for line in head.split(b'\r\n')[1:]:
        if line.lower().startswith(b'content-length:'):
            length = int(line.split(b':', 1)[1].strip())
    while len(rest) < length:
        part = sock.recv(65536)
        if not part:
            break
        rest += part
    return head + b'\r\n\r\n' + rest


def recv_http_many(sock, count):
    out = []
    data = b''
    while len(out) < count:
        need_head = True
        while b'\r\n\r\n' not in data:
            part = sock.recv(65536)
            if not part:
                raise Fail('connection closed before response %d' % (len(out) + 1))
            data += part
        head, _, data = data.partition(b'\r\n\r\n')
        length = 0
        for line in head.split(b'\r\n')[1:]:
            if line.lower().startswith(b'content-length:'):
                length = int(line.split(b':', 1)[1].strip())
        while len(data) < length:
            part = sock.recv(65536)
            if not part:
                raise Fail('connection closed in response body')
            data += part
        body = data[:length]
        data = data[length:]
        out.append(head + b'\r\n\r\n' + body)
    return out, data


def metrics(out):
    for line in out.splitlines():
        if line.startswith('METRICS '):
            return [int(x) for x in line.split()[1:]]
    return None


def tls_counts(out):
    for line in out.splitlines():
        if line.startswith('TLSCOUNTS '):
            return [int(x) for x in line.split()[1:]]
    return None


def need_clean(out, label):
    m = metrics(out)
    tc = tls_counts(out)
    need(m is not None and tc is not None, label + ' metrics/counts')
    need(m[8] == 0, label + ' has live HTTP connections')
    need(tc[0] == 0 and tc[1] == 0, label + ' leaked TLS context/session')
    need(tc[6] == 0 and tc[7] == 0, label + ' left TLS ciphertext/plaintext')
    return m, tc


def scenario_basic(exe, fixtures):
    srv = Server(exe, fixtures, 'alpn')
    try:
        for version in ('1.3', '1.2'):
            ss = srv.tls(version=version)
            need(ss.version() == 'TLSv' + version,
                 'negotiated %s, expected %s' % (ss.version(), version))
            need(ss.selected_alpn_protocol() == 'http/1.1',
                 'ALPN was %r' % ss.selected_alpn_protocol())
            ss.sendall(b'GET / HTTP/1.1\r\nHost: localhost\r\n'
                       b'Connection: close\r\n\r\n')
            resp = recv_http(ss)
            need(resp.startswith(b'HTTP/1.1 200 OK'), 'basic status')
            need(resp.endswith(b'hello\n'), 'basic body')
            ss.close()
        ss = srv.tls()
        ss.sendall(b'POST /echo HTTP/1.1\r\nHost: localhost\r\n'
                   b'Connection: close\r\nContent-Length: 11\r\n\r\nhello world')
        resp = recv_http(ss)
        need(b'hello world' in resp, 'TLS echo body')
        ss.close()
        ss = srv.tls()
        ss.sendall(b'GET /slow HTTP/1.1\r\nHost: localhost\r\n'
                   b'Connection: close\r\n\r\n')
        resp = recv_http(ss)
        head, _, body = resp.partition(b'\r\n\r\n')
        need(b'Content-Length: 1000\r\n' in head, 'slow length')
        need(len(body) == 1000 and body.endswith(b'0123456789'),
             'slow body over TLS was %d bytes' % len(body))
        ss.close()
        ss = srv.tls()
        ss.sendall(b'GET /big HTTP/1.1\r\nHost: localhost\r\n'
                   b'Connection: close\r\n\r\n')
        resp = recv_http(ss)
        head, _, body = resp.partition(b'\r\n\r\n')
        need(b'Content-Length: 20000\r\n' in head, 'big length')
        need(len(body) == 20000, 'big body over TLS was %d bytes' % len(body))
        ss.close()
    finally:
        out = srv.quit()
    m, tc = need_clean(out, 'basic')
    need(m[9] == 0, 'basic rejected a connection')


def scenario_alpn(exe, fixtures):
    # h2 is negotiated by the TLS context (Prefer) but rejected by HTTP before
    # any parse; a later HTTP/1.1 connection still works.
    srv = Server(exe, fixtures, 'h2')
    try:
        ss = srv.tls(alpn=('h2',))
        need(ss.selected_alpn_protocol() == 'h2', 'client expected h2')
        ss.sendall(b'GET / HTTP/1.1\r\nHost: localhost\r\n\r\n')
        got = b''
        try:
            while True:
                part = ss.recv(65536)
                if not part:
                    break
                got += part
        except (ssl.SSLError, OSError):
            pass
        need(b'HTTP/1.1' not in got, 'h2 connection received an HTTP/1.1 response')
        ss.close()
        ss = srv.tls(alpn=('http/1.1',))
        ss.sendall(b'GET / HTTP/1.1\r\nHost: localhost\r\n'
                   b'Connection: close\r\n\r\n')
        need(recv_http(ss).endswith(b'hello\n'), 'HTTP/1.1 after h2 rejection')
        ss.close()
    finally:
        out = srv.quit()
    m, tc = need_clean(out, 'h2 rejection')
    need(m[6] >= 1, 'h2 rejection counted as handler error')

    # A negotiated non-h2, non-http/1.1 protocol (AlpnOther) is also rejected
    # before any HTTP parse.
    srv = Server(exe, fixtures, 'other')
    try:
        ss = srv.tls(alpn=('spdy/3',))
        need(ss.selected_alpn_protocol() == 'spdy/3', 'client expected spdy/3')
        ss.sendall(b'GET / HTTP/1.1\r\nHost: localhost\r\n\r\n')
        got = b''
        try:
            while True:
                part = ss.recv(65536)
                if not part:
                    break
                got += part
        except (ssl.SSLError, OSError):
            pass
        need(b'HTTP/1.1' not in got, 'AlpnOther received an HTTP/1.1 response')
        ss.close()
    finally:
        out = srv.quit()
    m, tc = need_clean(out, 'AlpnOther')
    need(m[6] >= 1, 'AlpnOther rejection counted')

    # No ALPN is accepted only when Limits.allowNoAlpn is true.
    srv = Server(exe, fixtures, 'noalpn')
    try:
        ss = srv.tls(alpn=None)
        need(ss.selected_alpn_protocol() is None, 'client offered no ALPN')
        ss.sendall(b'GET / HTTP/1.1\r\nHost: localhost\r\n'
                   b'Connection: close\r\n\r\n')
        need(recv_http(ss).endswith(b'hello\n'), 'no-ALPN accepted by policy')
        ss.close()
    finally:
        out = srv.quit()
    need_clean(out, 'noalpn')

    srv = Server(exe, fixtures, 'noalpn', 'deny')
    try:
        ss = srv.tls(alpn=None)
        ss.sendall(b'GET / HTTP/1.1\r\nHost: localhost\r\n\r\n')
        got = b''
        try:
            while True:
                part = ss.recv(65536)
                if not part:
                    break
                got += part
        except (ssl.SSLError, OSError):
            pass
        need(b'HTTP/1.1' not in got, 'no-ALPN denial produced HTTP')
        ss.close()
    finally:
        out = srv.quit()
    m, tc = need_clean(out, 'no-ALPN denial')
    need(m[6] >= 1,
         'no-ALPN denial counted: metrics=%r counts=%r\n%s'
         % (m, tls_counts(out), out[-1500:]))


def scenario_keepalive(exe, fixtures):
    srv = Server(exe, fixtures, 'alpn')
    try:
        ss = srv.tls()
        ss.sendall(b'GET / HTTP/1.1\r\nHost: localhost\r\n\r\n'
                   b'GET / HTTP/1.1\r\nHost: localhost\r\n\r\n'
                   b'GET /quit HTTP/1.1\r\nHost: localhost\r\n'
                   b'Connection: close\r\n\r\n')
        responses, leftover = recv_http_many(ss, 3)
        need(len(responses) == 3, 'three pipelined responses')
        need(responses[0].endswith(b'hello\n') and
             responses[1].endswith(b'hello\n'), 'pipelined bodies')
        need(b'HTTP/1.1 200 OK' in responses[2], 'quit response')
        ss.close()
    finally:
        try:
            out = srv.proc.communicate(timeout=15)[0] or ''
        except subprocess.TimeoutExpired:
            srv.proc.kill()
            out = srv.proc.communicate()[0] or ''
    m, tc = need_clean(out, 'keep-alive')
    need(m[2] >= 3, 'keep-alive request count')


def scenario_slowloris(exe, fixtures):
    srv = Server(exe, fixtures, 'alpn', 'fast')
    try:
        ss = srv.tls()
        ss.sendall(b'GET / HTTP/1.1\r\nHost: localhost\r\n')
        ss.settimeout(3)
        got = b''
        try:
            while True:
                part = ss.recv(65536)
                if not part:
                    break
                got += part
        except (socket.timeout, ssl.SSLError, OSError):
            pass
        need(b'400' in got or b'408' in got or got == b'',
             'slowloris over TLS was not closed: %r' % got[:80])
        ss.close()
    finally:
        out = srv.quit()
    m, tc = need_clean(out, 'slowloris')
    need(m[4] >= 1, 'slowloris timeout counted')


def scenario_handshake_timeout(exe, fixtures):
    srv = Server(exe, fixtures, 'alpn', 'fast')
    try:
        raw = srv.connect_raw()
        raw.settimeout(3)
        got = b''
        try:
            while True:
                part = raw.recv(4096)
                if not part:
                    break
                got += part
        except (socket.timeout, OSError):
            pass
        need(len(got) < 64, 'silent handshake produced data: %r' % got[:80])
        raw.close()
        # A partial ClientHello also fails the handshake deadline.
        ctx = srv.context()
        inb = ssl.MemoryBIO()
        outb = ssl.MemoryBIO()
        obj = ctx.wrap_bio(inb, outb, server_hostname='localhost')
        try:
            obj.do_handshake()
        except (ssl.SSLWantReadError, ssl.SSLWantWriteError):
            pass
        hello = outb.read()
        need(len(hello) > 16, 'client produced no ClientHello')
        raw = srv.connect_raw()
        raw.sendall(hello[:max(8, len(hello) // 2)])
        raw.settimeout(3)
        got = b''
        try:
            while True:
                part = raw.recv(4096)
                if not part:
                    break
                got += part
        except (socket.timeout, OSError):
            pass
        need(len(got) < 128, 'partial handshake produced data: %r' % got[:80])
        raw.close()
        # The server is still healthy for a complete handshake.
        ss = srv.tls()
        ss.sendall(b'GET / HTTP/1.1\r\nHost: localhost\r\n'
                   b'Connection: close\r\n\r\n')
        need(recv_http(ss).endswith(b'hello\n'), 'server after handshake timeout')
        ss.close()
    finally:
        out = srv.quit()
    m, tc = need_clean(out, 'handshake timeout')
    need(m[4] >= 1, 'handshake timeout counted')


def fragmented_handshake(server):
    ctx = server.context()
    inb = ssl.MemoryBIO()
    outb = ssl.MemoryBIO()
    obj = ctx.wrap_bio(inb, outb, server_hostname='localhost')
    raw = server.connect_raw()
    raw.settimeout(5)
    done = False
    guard = 0
    while not done and guard < 20000:
        guard += 1
        try:
            obj.do_handshake()
            done = True
        except ssl.SSLWantReadError:
            pass
        except ssl.SSLWantWriteError:
            pass
        pending = outb.read()
        for i in range(len(pending)):
            raw.sendall(pending[i:i + 1])
        try:
            chunk = raw.recv(65536)
            if not chunk:
                break
            inb.write(chunk)
        except socket.timeout:
            pass
    need(done, 'byte-at-a-time handshake did not complete')
    obj.write(b'GET / HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n')
    data = b''
    while True:
        try:
            obj.do_handshake()
        except ssl.SSLError:
            pass
        out = outb.read()
        if out:
            raw.sendall(out)
        try:
            chunk = raw.recv(65536)
            if not chunk:
                break
            inb.write(chunk)
        except socket.timeout:
            pass
        try:
            part = obj.read(65536)
            if part:
                data += part
            if b'hello\n' in data:
                break
        except ssl.SSLWantReadError:
            pass
        except ssl.SSLZeroReturnError:
            break
    need(b'HTTP/1.1 200 OK' in data and data.endswith(b'hello\n'),
         'fragmented-handshake response: %r' % data[:120])
    raw.close()


def scenario_fragmented(exe, fixtures):
    srv = Server(exe, fixtures, 'alpn')
    try:
        fragmented_handshake(srv)
    finally:
        out = srv.quit()
    need_clean(out, 'fragmented handshake')


def scenario_close_notify(exe, fixtures):
    # The client sends no close_notify and never calls unwrap: only a
    # server-initiated close_notify makes recv return clean b''. A TCP-only
    # close raises SSLEOFError (unexpected EOF), which is the old defect.
    srv = Server(exe, fixtures, 'alpn', 'fast')
    try:
        ss = srv.tls()
        ss.settimeout(5)
        ss.sendall(b'GET /close HTTP/1.1\r\nHost: localhost\r\n\r\n')
        resp = recv_http(ss)
        need(b'200 OK' in resp and b'Connection: close' in resp,
             'close-path response')
        started = time.time()
        clean = False
        try:
            data = ss.recv(1)
            clean = data == b''
        except ssl.SSLEOFError:
            clean = False
        except (ssl.SSLError, OSError):
            clean = False
        need(clean, 'server closed TLS without close_notify')
        need(time.time() - started < 5, 'close_notify arrived after the deadline')
        ss.close()
    finally:
        out = srv.quit()
    need_clean(out, 'close_notify')


def scenario_shutdown_idle(exe, fixtures):
    srv = Server(exe, fixtures, 'alpn')
    try:
        idle = srv.tls()
        # An established, idle TLS connection must receive close_notify when
        # the server drains, not an abortive TCP reset.
        quitter = srv.tls()
        quitter.sendall(b'GET /quit HTTP/1.1\r\nHost: localhost\r\n'
                        b'Connection: close\r\n\r\n')
        need(b'200 OK' in recv_http(quitter), 'quit response')
        quitter.close()
        idle.settimeout(5)
        clean = False
        try:
            data = idle.recv(1)
            clean = data == b''
        except ssl.SSLEOFError:
            clean = False
        except (ssl.SSLError, OSError):
            clean = False
        need(clean, 'idle TLS connection did not get close_notify on shutdown')
        idle.close()
    finally:
        try:
            out = srv.proc.communicate(timeout=15)[0] or ''
        except subprocess.TimeoutExpired:
            srv.proc.kill()
            out = srv.proc.communicate()[0] or ''
    need_clean(out, 'idle shutdown')


def scenario_error_page(exe, fixtures):
    srv = Server(exe, fixtures, 'alpn')
    try:
        ss = srv.tls()
        ss.sendall(b'BAD\r\n\r\n')
        resp = recv_http(ss)
        need(resp.startswith(b'HTTP/1.1 400'), 'TLS malformed request 400')
        need(b'BAD' not in resp, 'TLS error page echoed request data')
        ss.settimeout(5)
        clean = False
        try:
            clean = ss.recv(1) == b''
        except (ssl.SSLEOFError, ssl.SSLError, OSError):
            clean = False
        need(clean, 'TLS error page closed without close_notify')
        ss.close()
    finally:
        out = srv.quit()
    m, tc = need_clean(out, 'TLS error page')
    need(m[3] >= 1, 'TLS protocol error counted')


def scenario_client_cert(exe, fixtures):
    srv = Server(exe, fixtures, 'clientcert')
    try:
        # VerifyOptional: no client certificate is accepted.
        ss = srv.tls()
        ss.sendall(b'GET / HTTP/1.1\r\nHost: localhost\r\n'
                   b'Connection: close\r\n\r\n')
        need(recv_http(ss).endswith(b'hello\n'), 'optional client cert absent')
        ss.close()
        # A trusted client certificate is also accepted.
        ss = srv.tls(cert=os.path.join(fixtures, 'client.pem'),
                     key=os.path.join(fixtures, 'client.key'))
        ss.sendall(b'GET / HTTP/1.1\r\nHost: localhost\r\n'
                   b'Connection: close\r\n\r\n')
        need(recv_http(ss).endswith(b'hello\n'), 'optional client cert present')
        ss.close()
    finally:
        out = srv.quit()
    need_clean(out, 'client certificate')


def scenario_required_client_cert(exe, fixtures):
    cert = os.path.join(fixtures, 'client.pem')
    key = os.path.join(fixtures, 'client.key')
    srv = Server(exe, fixtures, 'reqclient')
    try:
        accepted = False
        try:
            ss = srv.tls()
            ss.settimeout(5)
            ss.sendall(b'GET / HTTP/1.1\r\nHost: localhost\r\n'
                       b'Connection: close\r\n\r\n')
            # TLS 1.3 clients can finish their side before the server's
            # certificate-required alert arrives; require no HTTP response.
            data = ss.recv(1024)
            accepted = b'HTTP/1.1' in data
            ss.close()
        except (ssl.SSLError, OSError):
            accepted = False
        need(not accepted, 'VerifyRequired accepted a client with no certificate')
        # The server survives the failed handshake and accepts a trusted cert.
        ss = srv.tls(cert=cert, key=key)
        ss.sendall(b'GET / HTTP/1.1\r\nHost: localhost\r\n'
                   b'Connection: close\r\n\r\n')
        need(recv_http(ss).endswith(b'hello\n'), 'required client cert accepted')
        ss.close()
    finally:
        out = srv.quit(cert=cert, key=key)
    need_clean(out, 'required client certificate')


def scenario_plaintext(exe, fixtures):
    srv = Server(exe, fixtures, 'alpn')
    try:
        raw = srv.connect_raw()
        raw.sendall(b'GET / HTTP/1.1\r\nHost: localhost\r\n'
                    b'Connection: close\r\n\r\n')
        raw.settimeout(3)
        got = b''
        try:
            while True:
                part = raw.recv(4096)
                if not part:
                    break
                got += part
        except (socket.timeout, OSError):
            pass
        need(b'HTTP/1.1' not in got and b'hello' not in got,
             'plaintext leaked through the TLS listener: %r' % got[:120])
        raw.close()
        ss = srv.tls()
        ss.sendall(b'GET / HTTP/1.1\r\nHost: localhost\r\n'
                   b'Connection: close\r\n\r\n')
        need(recv_http(ss).endswith(b'hello\n'), 'TLS after plaintext probe')
        ss.close()
    finally:
        out = srv.quit()
    need_clean(out, 'plaintext rejection')


def scenario_backpressure(exe, fixtures):
    curl = shutil.which('curl')
    if not curl:
        return
    srv = Server(exe, fixtures, 'alpn')
    body_path = os.path.join(TMP, 'backpressure.bin')
    body = bytes((i * 13) & 0xff for i in range(4_000_000))
    with open(body_path, 'wb') as f:
        f.write(body)
    try:
        base = 'https://127.0.0.1:%d' % srv.port
        p = subprocess.run(
            [curl, '-skS', '--http1.1', '--max-time', '60',
             '--limit-rate', '512k', '--data-binary', '@' + body_path,
             base + '/echo'],
            capture_output=True, timeout=90)
        need(p.returncode == 0,
             'slow-reader curl failed: %r' % (p.stderr[-300:],))
        need(p.stdout == body,
             'slow-reader echo mismatch: %d/%d bytes' %
             (len(p.stdout), len(body)))
    finally:
        out = srv.quit()
    need_clean(out, 'slow-reader backpressure')


def scenario_curl(exe, fixtures):
    curl = shutil.which('curl')
    if not curl:
        return
    srv = Server(exe, fixtures, 'alpn')
    try:
        base = 'https://127.0.0.1:%d' % srv.port
        p = run([curl, '-skS', '--http1.1', '--max-time', '8', base + '/'])
        need(p.returncode == 0 and p.stdout == 'hello\n', 'curl -k GET')
        p = run([curl, '-skS', '--http1.1', '--max-time', '8', '-d',
                 'curl over tls', base + '/echo'])
        need(p.returncode == 0 and p.stdout == 'curl over tls',
             'curl -k POST echo')
    finally:
        out = srv.quit()
    need_clean(out, 'curl')


def main():
    if not os.path.exists(OBO):
        raise Fail('no compiler at %s; run make -C obo http1c' % OBO)
    fixtures = os.path.join(TMP, 'fixtures')
    tls = load_tls_tests()
    tls.gen_certs(fixtures)
    print('fixtures: ok')
    exe = build_app()
    print('H1c app build: ok')
    scenario_basic(exe, fixtures)
    print('ok   TLS 1.2/1.3, ALPN http/1.1, echo and partial writes')
    scenario_alpn(exe, fixtures)
    print('ok   h2 rejection and no-ALPN policy')
    scenario_keepalive(exe, fixtures)
    print('ok   keep-alive and pipelining over TLS')
    scenario_slowloris(exe, fixtures)
    print('ok   slowloris/header timeout over TLS')
    scenario_handshake_timeout(exe, fixtures)
    print('ok   silent handshake timeout')
    scenario_fragmented(exe, fixtures)
    print('ok   byte-at-a-time ClientHello fragmentation')
    scenario_close_notify(exe, fixtures)
    print('ok   close_notify on graceful close')
    scenario_error_page(exe, fixtures)
    print('ok   TLS protocol error page and close_notify')
    scenario_shutdown_idle(exe, fixtures)
    print('ok   idle TLS connection close_notify on server shutdown')
    scenario_client_cert(exe, fixtures)
    print('ok   optional client certificate')
    scenario_required_client_cert(exe, fixtures)
    print('ok   required client certificate failure and recovery')
    scenario_plaintext(exe, fixtures)
    print('ok   no plaintext handling on the TLS listener')
    scenario_curl(exe, fixtures)
    print('ok   curl -k HTTPS differential')
    scenario_backpressure(exe, fixtures)
    print('ok   slow-reader TLS backpressure and partial writes')
    print('all H1c tests passed')


if __name__ == '__main__':
    try:
        main()
    except Fail as e:
        print('FAIL: %s' % e)
        sys.exit(1)
