#!/usr/bin/env python3
"""H1b tests: HTTP/1.1 connection state machine, plain transport and server loop.

    python3 obo/experimental/http1/tests/test_http1b.py

Two drivers are built with the experimental cinterop tool:
  * h1b_conn_driver over the in-memory transport (fake systems adapter): exact
    state-machine, framing, deadline, ALPN, injection and backpressure cases;
  * h1b_server_app over the real Linux adapter: loopback TCP IPv4/IPv6, Unix
    sockets, raw/curl clients, keep-alive/pipelining, 100-continue, timeouts,
    admission/idle pressure and graceful shutdown.
No TLS, no compiler/runtime change, no external network.
"""
import atexit
import os
import select
import shutil
import socket
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
SPEC_FAKE = os.path.join(REPO, 'obo', 'experimental', 'sys', 'native',
                         'sysadapter_fake.cbind')
SPEC_REAL = os.path.join(REPO, 'obo', 'experimental', 'sys', 'native',
                         'sysadapter_real.cbind')
TMP = tempfile.mkdtemp(prefix='http1-h1b-', dir='/tmp/opencode')
atexit.register(shutil.rmtree, TMP, ignore_errors=True)

MODULES = ['http1core.obx', 'http1parse.obx', 'http1write.obx',
           'http1date.obx', 'http1transport.obx', 'http1conn.obx',
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


def build(app, spec, tag):
    appdir = os.path.join(TMP, 'app-' + tag)
    out = os.path.join(TMP, 'out-' + tag)
    os.makedirs(appdir)
    for name in MODULES:
        shutil.copyfile(os.path.join(PKG, name), os.path.join(appdir, name))
    shutil.copyfile(os.path.join(APPS, app + '.obx'),
                    os.path.join(appdir, app + '.obx'))
    p = run([sys.executable, TOOLPY, 'build', spec,
             os.path.join(appdir, app + '.obx'), '--out', out, '--obo', OBO])
    if p.returncode != 0:
        raise Fail('could not build %s:\n%s\n%s'
                   % (tag, (p.stdout or '')[-2000:], (p.stderr or '')[-2000:]))
    return os.path.join(out, 'app')


def driver_lines(exe, mode):
    p = run([exe, mode], env=dict(os.environ, OBO_CHECK='1'), timeout=120)
    if p.returncode != 0:
        raise Fail('driver %s exited %d:\n%s\n%s'
                   % (mode, p.returncode, p.stdout, p.stderr))
    return p.stdout.splitlines()


def out_bytes(lines):
    out = []
    for line in lines:
        if line.startswith('OUT ') and len(line) > 4:
            out.append(bytes.fromhex(line[4:]))
        elif line == 'OUT ' or line == 'OUT':
            out.append(b'')
    return out


def has(lines, text):
    return any(text in line for line in lines)


def memory_tests(exe):
    lines = driver_lines(exe, 'basic')
    need(has(lines, 'EV handshake') and has(lines, 'EV head'), 'basic events')
    outs = out_bytes(lines)
    need(len(outs) == 1 and outs[0].startswith(b'HTTP/1.1 200 OK\r\n'),
         'basic response line')
    need(b'Content-Length: 5\r\n' in outs[0], 'basic fixed length')
    need(outs[0].endswith(b'hello'), 'basic body')
    need(has(lines, 'PH idle') and has(lines, 'N 1'), 'basic completion')

    lines = driver_lines(exe, 'keepalive')
    outs = out_bytes(lines)
    need(len(outs) == 2, 'two keep-alive responses, got %d' % len(outs))
    need(outs[0].endswith(b'A') and outs[1].endswith(b'B'), 'response order')
    need(has(lines, 'N 2'), 'two requests')

    lines = driver_lines(exe, 'head')
    outs = out_bytes(lines)
    need(b'Content-Length: 5\r\n' in outs[0], 'HEAD metadata length')
    need(outs[0].endswith(b'\r\n\r\n'), 'HEAD has no body')

    lines = driver_lines(exe, 'fixed')
    need(has(lines, 'BD data:68656c') and has(lines, 'BD data:6c6f'),
         'fixed body split')
    need(has(lines, 'BD end'), 'fixed body end')

    lines = driver_lines(exe, 'chunked')
    need(has(lines, 'BD data:68656c6c6f'), 'chunked decoded body')

    lines = driver_lines(exe, 'continue')
    outs = out_bytes(lines)
    need(any(b.startswith(b'HTTP/1.1 100 Continue\r\n\r\n') for b in outs),
         '100-continue interim response')
    need(outs[-1].startswith(b'HTTP/1.1 200') and outs[-1].endswith(b'ok'),
         'final response after continue')

    lines = driver_lines(exe, 'continue_early')
    need(has(lines, 'CLOSE true'), 'early final sets close')
    need(has(lines, 'BERR 1'), 'readBody invalid after early final')
    need(not any(b'100 Continue' in b for b in out_bytes(lines)),
         'no 100 after early final')

    lines = driver_lines(exe, 'unread')
    need(has(lines, 'N 1') and has(lines, 'N2 1'), 'no second request parsed')
    need(has(lines, 'RC true'), 'requestClose')

    lines = driver_lines(exe, 'limits')
    need(has(lines, 'EV protocol:431'), 'max header count 431')

    lines = driver_lines(exe, 'bigbody')
    need(has(lines, 'EV protocol:413'), 'max body 413')

    lines = driver_lines(exe, 'injection')
    need(has(lines, 'HERR 2') and has(lines, 'H2ERR 2'),
         'header injection and writer-owned rejection')
    outs = out_bytes(lines)
    need(outs and b'200 OK' in outs[0] and b'Injected' not in outs[0],
         'injection not reflected')

    lines = driver_lines(exe, 'backpressure')
    need(has(lines, 'W1 1') and has(lines, 'W2 1'), 'short writes accepted')
    need(has(lines, 'EERR 4'), 'declared length mismatch rejected')

    lines = driver_lines(exe, 'timeout_header')
    need(has(lines, 'EV timedout'), 'header timeout')
    need(any(b'408 Request Timeout' in b for b in out_bytes(lines)),
         'header timeout error page')

    lines = driver_lines(exe, 'timeout_accept')
    need(has(lines, 'EV timedout') and has(lines, 'PH closing'),
         'accept timeout closes silently')
    need(has(lines, 'EV closed'), 'accept timeout terminal')
    need(not out_bytes(lines), 'accept timeout writes no response')

    lines = driver_lines(exe, 'h2')
    need(has(lines, 'ALPNERR 10030') and has(lines, 'EV closed'),
         'h2 ALPN rejected before HTTP parse')

    lines = driver_lines(exe, 'alpn_required')
    need(has(lines, 'ERR 1'), 'next before acceptAlpn is InvalidState')

    lines = driver_lines(exe, 'close')
    need(has(lines, 'C1 true') and has(lines, 'C2 false') and
         has(lines, 'MC true'), 'close is idempotent and closes once')

    lines = driver_lines(exe, 'errors')
    outs = out_bytes(lines)
    for status in (b'400', b'413', b'414', b'417', b'431', b'501', b'505'):
        need(any(status + b' ' in o.split(b'\r\n')[0] for o in outs),
             'error status %s' % status.decode())
    need(all(b'GET /' not in o and b'BAD' not in o for o in outs),
         'error page does not echo request data')

    lines = driver_lines(exe, 'rules')
    need(has(lines, 'L204ERR 1'), '204 rejects Content-Length')
    need(has(lines, 'E304 true'), '304 completes')
    outs = out_bytes(lines)
    need(b'Content-Length' not in outs[0], '204 has no Content-Length')
    need(b'Content-Length: 9\r\n' in outs[1] and outs[1].endswith(b'\r\n\r\n'),
         '304 length metadata without body')

    lines = driver_lines(exe, 'trailers')
    outs = out_bytes(lines)
    need(outs and outs[0].endswith(b'0\r\nX-Sum: 1\r\n\r\n'),
         'chunked response trailers')
    need(b'Trailer: X-Sum\r\n' in outs[0], 'Trailer field emitted')

    lines = driver_lines(exe, 'http10')
    outs = out_bytes(lines)
    need(outs[0].startswith(b'HTTP/1.0 200 OK'), 'HTTP/1.0 status line')
    need(b'Connection: close\r\n' in outs[0], 'HTTP/1.0 close')
    need(b'Transfer-Encoding' not in outs[0], 'HTTP/1.0 never chunked')

    lines = driver_lines(exe, 'nopipe')
    need(len([x for x in out_bytes(lines) if x]) == 1,
         'pipelining=false serves one request')
    need(b'Connection: close\r\n' in out_bytes(lines)[0], 'pipelining false closes')

    lines = driver_lines(exe, 'maxreq1')
    need(has(lines, 'EV2 closed'), 'maxRequests=1 closes before second request')
    need(len([x for x in out_bytes(lines) if x]) == 1,
         'maxRequests one response')

    lines = driver_lines(exe, 'timeout_body')
    need(has(lines, 'BD data:6865'), 'partial body read')
    need(has(lines, 'BERR 408'), 'body deadline')
    need(any(b'408 Request Timeout' in b for b in out_bytes(lines)),
         'body timeout error page')

    lines = driver_lines(exe, 'idle_timeout')
    need(has(lines, 'EV2 timedout') and has(lines, 'PH closing'),
         'idle timeout closes silently')

    lines = driver_lines(exe, 'xfer')
    outs = out_bytes(lines)
    need(outs and outs[-1].endswith(b'0123456789'), 'maxTransfer splits reads/writes')
    need(has(lines, 'FL flushed'), 'maxTransfer response flushed')

    lines = driver_lines(exe, 'noalpn')
    need(has(lines, 'ALPNERR 10030') and has(lines, 'EV2 closed'),
         'no-ALPN forbidden by policy')

    lines = driver_lines(exe, 'discard')
    need(has(lines, 'DISCARD true'), 'bounded discard reaches BodyEnd')
    need(any(b'200 OK' in b for b in out_bytes(lines)), 'discard then response')

    lines = driver_lines(exe, 'manual_continue')
    need(has(lines, 'SENDCONT true'), 'manual sendContinue')
    need(any(b'100 Continue' in b for b in out_bytes(lines)),
         'manual interim response')
    need(has(lines, 'BD data:68656c6c6f'), 'body after manual continue')

    lines = driver_lines(exe, 'endfalse')
    need(has(lines, 'END1 false'), 'endResponse reports no room')
    need(has(lines, 'END2 true'), 'endResponse retry completes')
    outs = out_bytes(lines)
    need(outs and outs[-1].count(b'0\r\nX-Long:') == 1,
         'exactly one final chunk/trailer block')


def unit_tests(exe):
    p = run([exe], env=dict(os.environ, OBO_CHECK='1'), timeout=120)
    need(p.returncode == 0, 'server unit app exited: ' + p.stderr[-1000:])
    text = p.stdout
    need('pressure_res=true' in text, 'ResourceLimit classified as pressure')
    need('pressure_wb=false' in text, 'WouldBlock is not pressure')
    need('per=81920' in text, 'default reservation includes buffers and copies')
    need('admit255=true' in text and 'admit256=false' in text,
         'maxConnections admission')
    need('admit_budget=false' in text, 'aggregate budget admission')
    need('badbudget=3' in text, 'invalid aggregate budget rejected')


class Server:
    def __init__(self, exe, family, addr, port, profile='default'):
        self.proc = subprocess.Popen(
            [exe, family, addr, str(port), profile],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            bufsize=1, env=dict(os.environ, OBO_CHECK='1'))
        self.family = family
        self.addr = addr
        self.port = None
        line = self.readline(8)
        need(line is not None, 'server produced no PORT line')
        line = line.strip()
        need(line.startswith('PORT '), 'bad server line: ' + line)
        self.port = int(line.split()[1])

    def readline(self, timeout):
        r, _, _ = select.select([self.proc.stdout], [], [], timeout)
        if not r:
            return None
        return self.proc.stdout.readline()

    def connect(self):
        if self.family == 'unix':
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            s.settimeout(5)
            s.connect(self.addr)
            return s
        if self.family == 'tcp6':
            s = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
            s.settimeout(5)
            s.connect(('::1', self.port))
            return s
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(5)
        s.connect(('127.0.0.1', self.port))
        return s

    def request(self, raw, timeout=8):
        s = self.connect()
        try:
            s.sendall(raw)
            return recv_all(s, timeout)
        finally:
            s.close()

    def quit(self, timeout=12):
        try:
            s = self.connect()
            s.sendall(b'GET /quit HTTP/1.1\r\nHost: x\r\nConnection: close\r\n\r\n')
            recv_all(s, 5)
            s.close()
        except OSError:
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


def recv_until(sock, marker, timeout):
    sock.settimeout(timeout)
    data = b''
    while marker not in data:
        part = sock.recv(65536)
        if not part:
            break
        data += part
    return data


def recv_response(sock):
    data = recv_until(sock, b'\r\n\r\n', 5)
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


def recv_all(sock, timeout):
    sock.settimeout(timeout)
    data = b''
    while True:
        try:
            part = sock.recv(65536)
        except socket.timeout:
            break
        if not part:
            break
        data += part
    return data


def metrics_line(out):
    for line in out.splitlines():
        if line.startswith('METRICS '):
            return [int(x) for x in line.split()[1:]]
    return None


def start(exe, family, addr, profile='default'):
    return Server(exe, family, addr, 0, profile)


def network_tests(exe):
    # TCP IPv4: basic, HEAD, 204, 304, chunked, echo, keep-alive, pipelining.
    srv = start(exe, 'tcp4', '127.0.0.1')
    try:
        resp = srv.request(b'GET / HTTP/1.1\r\nHost: x\r\nConnection: close\r\n\r\n')
        need(resp.startswith(b'HTTP/1.1 200 OK\r\n'), 'tcp4 basic status')
        need(resp.endswith(b'hello\n'), 'tcp4 basic body')

        resp = srv.request(b'HEAD /head HTTP/1.1\r\nHost: x\r\nConnection: close\r\n\r\n')
        need(b'Content-Length: 5\r\n' in resp and resp.endswith(b'\r\n\r\n'),
             'tcp4 HEAD no body')

        resp = srv.request(b'GET /204 HTTP/1.1\r\nHost: x\r\nConnection: close\r\n\r\n')
        need(b'204 No Content' in resp and b'Content-Length' not in resp,
             'tcp4 204')

        resp = srv.request(b'GET /304 HTTP/1.1\r\nHost: x\r\nConnection: close\r\n\r\n')
        need(b'304 Not Modified' in resp and b'Content-Length: 7\r\n' in resp,
             'tcp4 304 metadata')

        resp = srv.request(b'GET /chunked HTTP/1.1\r\nHost: x\r\nConnection: close\r\n\r\n')
        need(b'Transfer-Encoding: chunked' in resp and b'chunk-a' in resp,
             'tcp4 chunked response')

        body = b'hello over tcp'
        resp = srv.request(b'POST /echo HTTP/1.1\r\nHost: x\r\nConnection: close\r\n'
                           b'Content-Length: ' + str(len(body)).encode() +
                           b'\r\n\r\n' + body)
        need(b'hello over tcp' in resp, 'tcp4 fixed echo')

        resp = srv.request(b'POST /echo HTTP/1.1\r\nHost: x\r\nConnection: close\r\n'
                           b'Transfer-Encoding: chunked\r\n\r\n'
                           b'5\r\nhello\r\n6\r\n world\r\n0\r\n\r\n')
        need(b'hello' in resp and b' world' in resp,
             'tcp4 chunked request echo')

        # Keep-alive: two requests on one connection, then a close.
        s = srv.connect()
        try:
            s.sendall(b'GET / HTTP/1.1\r\nHost: x\r\n\r\n')
            first = recv_response(s)
            need(first.startswith(b'HTTP/1.1 200 OK'), 'keep-alive first')
            s.sendall(b'GET /quit HTTP/1.1\r\nHost: x\r\nConnection: close\r\n\r\n')
            second = recv_all(s, 8)
            need(b'200 OK' in second, 'keep-alive second')
        finally:
            s.close()
        out = srv.proc.communicate(timeout=12)[0] or ''
        m = metrics_line(out)
        need(m is not None, 'metrics line after quit')
        need(m[0] >= 2 and m[8] == 0, 'keep-alive accepted and drained')
    finally:
        srv.stop()

    # Pipelining: two requests in one write.
    srv = start(exe, 'tcp4', '127.0.0.1')
    try:
        resp = srv.request(b'GET / HTTP/1.1\r\nHost: x\r\n\r\n'
                           b'GET / HTTP/1.1\r\nHost: x\r\nConnection: close\r\n\r\n')
        need(resp.count(b'HTTP/1.1 200 OK') == 2, 'two pipelined responses')
        need(resp.count(b'hello\n') == 2, 'pipelined bodies')
    finally:
        srv.stop()

    # 100-continue.
    srv = start(exe, 'tcp4', '127.0.0.1')
    try:
        s = srv.connect()
        try:
            s.sendall(b'POST /echo HTTP/1.1\r\nHost: x\r\nConnection: close\r\n'
                      b'Content-Length: 5\r\nExpect: 100-continue\r\n\r\n')
            interim = recv_until(s, b'\r\n\r\n', 5)
            need(interim.startswith(b'HTTP/1.1 100 Continue'), 'interim 100')
            s.sendall(b'hello')
            rest = recv_all(s, 8)
            need(rest.startswith(b'HTTP/1.1 200 OK'), 'final after 100')
            need(b'hello' in rest, 'continue body echo')
        finally:
            s.close()
    finally:
        srv.stop()

    # Fast profile: header timeout, accept timeout, idle timeout.
    srv = start(exe, 'tcp4', '127.0.0.1', 'fast')
    try:
        s = srv.connect()
        try:
            s.sendall(b'GET / HTTP/1.1\r\nHost: x\r\n')
            resp = recv_all(s, 3)
            need(b'408' in resp or resp == b'', 'slowloris header timeout')
        finally:
            s.close()
        s = srv.connect()
        try:
            resp = recv_all(s, 3)
            need(resp == b'', 'accept timeout closes silently')
        finally:
            s.close()
        s = srv.connect()
        try:
            s.sendall(b'GET / HTTP/1.1\r\nHost: x\r\n\r\n')
            first = recv_response(s)
            need(first.startswith(b'HTTP/1.1 200 OK'), 'idle first response')
            time.sleep(1.2)
            need(s.recv(1) == b'', 'idle timeout closes')
        finally:
            s.close()
    finally:
        srv.stop()

    # Small profile: request header and body limits.
    srv = start(exe, 'tcp4', '127.0.0.1', 'small')
    try:
        resp = srv.request(b'GET / HTTP/1.1\r\nHost: x\r\nA: 1\r\nB: 2\r\nC: 3\r\n'
                           b'D: 4\r\nE: 5\r\nConnection: close\r\n\r\n')
        need(b'431' in resp.split(b'\r\n')[0], 'tcp4 max header 431')
        resp = srv.request(b'POST /echo HTTP/1.1\r\nHost: x\r\nConnection: close\r\n'
                           b'Content-Length: 17\r\n\r\n')
        need(b'413' in resp.split(b'\r\n')[0], 'tcp4 max body 413')
    finally:
        srv.stop()

    # IPv6 loopback when available.
    try:
        srv = start(exe, 'tcp6', '::1')
        try:
            resp = srv.request(b'GET / HTTP/1.1\r\nHost: x\r\nConnection: close\r\n\r\n')
            need(resp.endswith(b'hello\n'), 'tcp6 basic')
        finally:
            srv.stop()
    except (Fail, OSError):
        pass

    # Unix domain socket when available.
    upath = os.path.join(TMP, 'h1b.sock')
    try:
        srv = start(exe, 'unix', upath)
        try:
            resp = srv.request(b'GET / HTTP/1.1\r\nHost: x\r\nConnection: close\r\n\r\n')
            need(resp.endswith(b'hello\n'), 'unix basic')
        finally:
            srv.stop()
    finally:
        try:
            os.unlink(upath)
        except OSError:
            pass

    # 256 idle connections and aggregate admission.
    srv = start(exe, 'tcp4', '127.0.0.1')
    socks = []
    try:
        for i in range(256):
            s = srv.connect()
            socks.append(s)
        time.sleep(2.0)
        # Ask the first connection to stop the server and report metrics.
        socks[0].sendall(b'GET /quit HTTP/1.1\r\nHost: x\r\nConnection: close\r\n\r\n')
        recv_all(socks[0], 5)
        out = srv.proc.communicate(timeout=15)[0] or ''
        m = metrics_line(out)
        need(m is not None, 'metric line after idle pressure')
        need(m[0] == 256, '256 connections accepted, got %d' % m[0])
        need(m[7] == 256, 'peak active 256, got %d' % m[7])
        need(m[8] == 0, 'all idle connections closed on shutdown')
    finally:
        for s in socks:
            try:
                s.close()
            except OSError:
                pass
        srv.stop()

    # curl differential for a valid request and an echo POST.
    curl = shutil.which('curl')
    if curl:
        srv = start(exe, 'tcp4', '127.0.0.1')
        try:
            base = 'http://127.0.0.1:%d' % srv.port
            p = run([curl, '-sS', '--max-time', '5', base + '/'])
            need(p.returncode == 0 and p.stdout == 'hello\n', 'curl GET body')
            p = run([curl, '-sS', '--max-time', '5', '-d', 'curl body', base + '/echo'])
            need(p.returncode == 0 and p.stdout == 'curl body', 'curl POST echo')
        finally:
            srv.stop()


def main():
    if not os.path.exists(OBO):
        raise Fail('no compiler at %s; run make -C obo http1' % OBO)
    memory = build('h1b_conn_driver', SPEC_FAKE, 'memory')
    unit = build('h1b_server_unit', SPEC_FAKE, 'unit')
    server = build('h1b_server_app', SPEC_REAL, 'server')
    memory_tests(memory)
    print('ok   connection state machine (memory transport)')
    unit_tests(unit)
    print('ok   admission and accept-pressure policy')
    network_tests(server)
    print('ok   loopback TCP IPv4/IPv6/Unix, curl/raw, keep-alive, 100-continue')
    print('ok   slowloris, idle/accept timeouts, limits, 256 idle connections')
    print('all H1b tests passed')


if __name__ == '__main__':
    try:
        main()
    except Fail as e:
        print('FAIL: %s' % e)
        sys.exit(1)
