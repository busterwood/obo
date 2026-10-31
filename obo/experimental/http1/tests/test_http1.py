#!/usr/bin/env python3
"""H1a0 tests: pure HTTP/1.1 parser, serializer and HTTP-date helpers.

    python3 obo/experimental/http1/tests/test_http1.py

Builds three small obo drivers from the H1a0 modules and runs exact-byte and
status assertions in memory. No sockets, TLS, clocks or reactor are used.
"""
import atexit
import os
import random
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.dirname(HERE)
OBO_ROOT = os.path.dirname(os.path.dirname(PKG))
REPO = os.path.dirname(OBO_ROOT)
OBO = os.path.abspath(os.environ.get('OBO', os.path.join(OBO_ROOT, 'obo')))
APPS = os.path.join(HERE, 'apps')
TMP = tempfile.mkdtemp(prefix='http1-h1a0-', dir='/tmp/opencode')
atexit.register(shutil.rmtree, TMP, ignore_errors=True)

DATE = 'Sun, 06 Nov 1994 08:49:37 GMT'
DATE_SECONDS = 784111777


class Fail(Exception):
    pass


def need(cond, message):
    if not cond:
        raise Fail(message)


def run(cmd, **kw):
    timeout = kw.pop('timeout', 30)
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, **kw)


def build_app(name):
    exe = os.path.join(TMP, name)
    sym = os.path.join(TMP, 'sym-' + name)
    os.makedirs(sym)
    src = os.path.join(APPS, name + '.obx')
    p = run([OBO, src, '-o', exe, '--rt', OBO_ROOT, '-I', PKG,
             '--sym-dir', sym], timeout=120)
    if p.returncode != 0:
        raise Fail('could not build %s:\n%s' % (name, (p.stderr or p.stdout).strip()[:2000]))
    return exe


PARSER = None
WRITER = None
DATER = None


def bootstrap():
    global PARSER, WRITER, DATER
    if not os.path.exists(OBO):
        raise Fail('no compiler at %s; run make -C obo http1' % OBO)
    PARSER = build_app('parse_driver')
    WRITER = build_app('write_driver')
    DATER = build_app('date_driver')


def run_parser(mode, limits, data):
    path = os.path.join(TMP, 'case-%d.bin' % random.randrange(1 << 60))
    with open(path, 'wb') as f:
        f.write(data)
    p = run([PARSER, mode, limits, path], env=dict(os.environ, OBO_CHECK='1'))
    if p.returncode != 0:
        raise Fail('parser driver exited %d for %r: %s\n%s'
                   % (p.returncode, data[:80], p.stdout, p.stderr))
    lines = p.stdout.splitlines()
    for line in lines:
        need(not line.startswith('DRVERR'), 'parser driver error: ' + line)
        need(line != 'LOOP', 'parser loop guard fired for %r' % data[:120])
    return lines


def run_writer(limits, commands):
    path = os.path.join(TMP, 'wcmd-%d.txt' % random.randrange(1 << 60))
    with open(path, 'w') as f:
        f.write('\n'.join(commands) + '\n')
    p = run([WRITER, limits, path], env=dict(os.environ, OBO_CHECK='1'))
    if p.returncode != 0:
        raise Fail('writer driver exited %d: %s\n%s' % (p.returncode, p.stdout, p.stderr))
    return p.stdout.splitlines()


def run_date(args):
    p = run([DATER] + [str(a) for a in args], env=dict(os.environ, OBO_CHECK='1'))
    if p.returncode != 0:
        raise Fail('date driver exited %d: %s\n%s' % (p.returncode, p.stdout, p.stderr))
    return p.stdout.strip()


def head_info(line):
    parts = line.split()
    need(len(parts) >= 2 and parts[0] == 'HEAD', 'not a head line: ' + line)
    out = {'method': parts[1]}
    for part in parts[2:]:
        if '=' in part:
            k, v = part.split('=', 1)
            out[k] = v
    return out


def headers(lines):
    out = []
    for line in lines:
        if line.startswith('H '):
            _, n, v = line.split(' ', 2)
            out.append((bytes.fromhex(n), bytes.fromhex(v)))
    return out


def trailers(lines):
    out = []
    for line in lines:
        if line.startswith('T '):
            _, n, v = line.split(' ', 2)
            out.append((bytes.fromhex(n), bytes.fromhex(v)))
    return out


def body_bytes(lines):
    body = b''
    for line in lines:
        if line.startswith('BODY '):
            body += bytes.fromhex(line.split(' ', 2)[2])
    return body


def err_status(lines):
    for line in lines:
        if line.startswith('ERR '):
            return int(line.split()[1])
    return None


def canon(lines):
    non = []
    body = b''
    for line in lines:
        if line == 'NEED':
            continue
        if line.startswith('BODY '):
            body += bytes.fromhex(line.split(' ', 2)[2])
        else:
            non.append(line)
    return non, body


def expect(cond, label, detail=''):
    if not cond:
        raise Fail('%s%s' % (label, (': ' + detail) if detail else ''))


def parse_case(name, data, limits='default', modes=('whole', 'byte', 'frag')):
    base = run_parser('whole', limits, data)
    base_canon = canon(base)
    for mode in modes:
        if mode == 'whole':
            continue
        got = run_parser(mode, limits, data)
        g = canon(got)
        expect(g == base_canon,
               '%s: %s feed changed the parsed result' % (name, mode),
               'whole=%r %s=%r' % (base_canon, mode, g))
    return base


def test_valid_requests():
    get = (b'GET /hello?x=1 HTTP/1.1\r\n'
           b'Host: example.com\r\n'
           b'User-Agent: tester\r\n'
           b'\r\n')
    lines = parse_case('get', get)
    h = head_info(lines[0])
    expect(h['method'] == 'GET', 'GET method')
    expect(bytes.fromhex(h['target']) == b'/hello?x=1', 'target raw')
    expect(bytes.fromhex(h['path']) == b'/hello', 'path raw')
    expect(bytes.fromhex(h['query']) == b'x=1', 'query raw')
    expect(h['scheme'] == '', 'origin-form scheme empty')
    expect(h['http10'] == 'false', 'HTTP/1.1')
    expect(h['body'] == 'false', 'no body')
    expect(h['keep'] == 'true', 'keep-alive')
    expect(h['close'] == 'false', 'no close')
    expect(headers(lines) == [(b'Host', b'example.com'),
                              (b'User-Agent', b'tester')], 'ordered headers')
    expect('END' in lines and 'CLOSED' in lines, 'GET completes and closes at EOF')

    post = (b'POST /submit HTTP/1.1\r\nHost: h\r\nContent-Length: 5\r\n\r\nhello')
    lines = parse_case('post-fixed', post)
    h = head_info(lines[0])
    expect(h['method'] == 'POST' and h['body'] == 'true' and h['cl'] == '5', 'POST framing')
    expect(body_bytes(lines) == b'hello', 'fixed body bytes', repr(body_bytes(lines)))

    chunked = (b'POST /c HTTP/1.1\r\nHost: h\r\n'
               b'Transfer-Encoding: chunked\r\n\r\n'
               b'5;foo=bar\r\nhello\r\n'
               b'0\r\nX-Sum: abc\r\n\r\n')
    lines = parse_case('post-chunked', chunked)
    h = head_info(lines[0])
    expect(h['chunk'] == 'true' and h['body'] == 'true', 'chunked framing')
    expect(body_bytes(lines) == b'hello', 'chunked body bytes')
    expect(trailers(lines) == [(b'X-Sum', b'abc')], 'request trailer')

    duplicates = (b'GET /d HTTP/1.1\r\nHost: h\r\n'
                  b'X-A: 1\r\nSet-Cookie: a=1\r\nX-A: 2\r\nSet-Cookie: b=2\r\n\r\n')
    lines = parse_case('duplicate-headers', duplicates)
    expect(headers(lines) == [(b'Host', b'h'), (b'X-A', b'1'),
                              (b'Set-Cookie', b'a=1'), (b'X-A', b'2'),
                              (b'Set-Cookie', b'b=2')], 'duplicates preserved in order')

    head_req = b'HEAD /x HTTP/1.1\r\nHost: h\r\n\r\n'
    lines = parse_case('head', head_req)
    expect(head_info(lines[0])['method'] == 'HEAD', 'HEAD parses')


def test_pipelining():
    data = (b'GET /one HTTP/1.1\r\nHost: h\r\n\r\n'
            b'POST /two HTTP/1.1\r\nHost: h\r\nContent-Length: 3\r\n\r\nabc'
            b'GET /three HTTP/1.1\r\nHost: h\r\n\r\n')
    lines = parse_case('pipeline', data)
    heads = [head_info(l) for l in lines if l.startswith('HEAD ')]
    expect([bytes.fromhex(h['target']) for h in heads] == [b'/one', b'/two', b'/three'],
           'pipelined order', repr(heads))
    expect(body_bytes(lines) == b'abc', 'pipelined body belongs only to second request')
    expect(lines.count('END') == 3, 'three body ends')
    expect('CLOSED' in lines, 'pipeline closes at EOF')

    # maxRequests=2: the third request is never parsed.
    lines = parse_case('max-requests', data, limits='maxreq2', modes=('whole',))
    heads = [l for l in lines if l.startswith('HEAD ')]
    expect(len(heads) == 2, 'maxRequests stops before the third request', repr(lines))
    expect('CLOSED' in lines and 'ERR' not in '\n'.join(lines), 'maxRequests closes cleanly')

    # pipelining=false: buffered second request is a protocol error.
    lines = parse_case('pipeline-disabled', data, limits='nopipe', modes=('whole',))
    expect(err_status(lines) == 400, 'pipelining=false rejects buffered bytes', repr(lines))


def test_smuggling():
    # (name, request, status, limit profile)
    cases = [
        ('cl-te', b'POST / HTTP/1.1\r\nHost: h\r\nContent-Length: 5\r\n'
                  b'Transfer-Encoding: chunked\r\n\r\nhello', 400, 'default'),
        ('dup-cl-same', b'POST / HTTP/1.1\r\nHost: h\r\nContent-Length: 5\r\n'
                        b'Content-Length: 5\r\n\r\nhello', 400, 'default'),
        ('dup-cl-diff', b'POST / HTTP/1.1\r\nHost: h\r\nContent-Length: 5\r\n'
                        b'Content-Length: 6\r\n\r\nhello', 400, 'default'),
        ('cl-list', b'POST / HTTP/1.1\r\nHost: h\r\nContent-Length: 5, 5\r\n\r\nhello', 400, 'default'),
        ('cl-plus', b'POST / HTTP/1.1\r\nHost: h\r\nContent-Length: +5\r\n\r\nhello', 400, 'default'),
        ('cl-negative', b'POST / HTTP/1.1\r\nHost: h\r\nContent-Length: -1\r\n\r\n', 400, 'default'),
        ('cl-hex', b'POST / HTTP/1.1\r\nHost: h\r\nContent-Length: 0x5\r\n\r\nhello', 400, 'default'),
        ('cl-space', b'POST / HTTP/1.1\r\nHost: h\r\nContent-Length: 5 6\r\n\r\nhello', 400, 'default'),
        ('cl-overflow', b'POST / HTTP/1.1\r\nHost: h\r\nContent-Length: 999999999999999999999\r\n\r\n', 400, 'default'),
        ('te-twice', b'POST / HTTP/1.1\r\nHost: h\r\nTransfer-Encoding: chunked\r\n'
                     b'Transfer-Encoding: chunked\r\n\r\n0\r\n\r\n', 400, 'default'),
        ('te-gzip-chunked', b'POST / HTTP/1.1\r\nHost: h\r\nTransfer-Encoding: gzip, chunked\r\n\r\n0\r\n\r\n', 501, 'default'),
        ('te-chunked-identity', b'POST / HTTP/1.1\r\nHost: h\r\nTransfer-Encoding: chunked, identity\r\n\r\n0\r\n\r\n', 400, 'default'),
        ('te-chunked-param', b'POST / HTTP/1.1\r\nHost: h\r\nTransfer-Encoding: chunked;foo\r\n\r\n0\r\n\r\n', 400, 'default'),
        ('te-leading-comma', b'POST / HTTP/1.1\r\nHost: h\r\nTransfer-Encoding: ,chunked\r\n\r\n0\r\n\r\n', 400, 'default'),
        ('te-trailing-comma', b'POST / HTTP/1.1\r\nHost: h\r\nTransfer-Encoding: chunked,\r\n\r\n0\r\n\r\n', 400, 'default'),
        ('te-empty-middle', b'POST / HTTP/1.1\r\nHost: h\r\nTransfer-Encoding: gzip,,chunked\r\n\r\n0\r\n\r\n', 400, 'default'),
        ('connection-leading-comma', b'GET / HTTP/1.1\r\nHost: h\r\nConnection: ,close\r\n\r\n', 400, 'default'),
        ('connection-trailing-comma', b'GET / HTTP/1.1\r\nHost: h\r\nConnection: close,\r\n\r\n', 400, 'default'),
        ('trailer-forbidden-decl', b'POST / HTTP/1.1\r\nHost: h\r\nTransfer-Encoding: chunked\r\n'
                                   b'Trailer: Content-Length\r\n\r\n0\r\n\r\n', 400, 'default'),
        ('space-before-colon', b'GET / HTTP/1.1\r\nHost: h\r\nTransfer-Encoding : chunked\r\n\r\n', 400, 'default'),
        ('obs-fold', b'GET / HTTP/1.1\r\nHost: h\r\nX: a\r\n b\r\n\r\n', 400, 'default'),
        ('bare-lf', b'GET / HTTP/1.1\nHost: h\r\n\r\n', 400, 'default'),
        ('bare-cr', b'GET / HTTP/1.1\rHost: h\r\n\r\n', 400, 'default'),
        ('nul-target', b'GET /a\x00b HTTP/1.1\r\nHost: h\r\n\r\n', 400, 'default'),
        ('nul-header', b'GET / HTTP/1.1\r\nHost: h\r\nX: a\x00b\r\n\r\n', 400, 'default'),
        ('del-header', b'GET / HTTP/1.1\r\nHost: h\r\nX: a\x7fb\r\n\r\n', 400, 'default'),
        ('dup-host', b'GET / HTTP/1.1\r\nHost: h\r\nHost: h\r\n\r\n', 400, 'default'),
        ('missing-host', b'GET / HTTP/1.1\r\n\r\n', 400, 'default'),
        ('host-empty-port', b'GET / HTTP/1.1\r\nHost: example.com:\r\n\r\n', 400, 'default'),
        ('host-comma', b'GET / HTTP/1.1\r\nHost: a,b\r\n\r\n', 400, 'default'),
        ('host-obs-text', b'GET / HTTP/1.1\r\nHost: h\xff\r\n\r\n', 400, 'default'),
        ('absolute-empty-authority', b'GET http:///x HTTP/1.1\r\nHost: h\r\n\r\n', 400, 'default'),
        ('absolute-userinfo', b'GET http://u@h/x HTTP/1.1\r\nHost: h\r\n\r\n', 400, 'default'),
        ('absolute-mismatch', b'GET http://a/x HTTP/1.1\r\nHost: b\r\n\r\n', 400, 'default'),
        ('fragment', b'GET /a#b HTTP/1.1\r\nHost: h\r\n\r\n', 400, 'default'),
        ('bad-percent', b'GET /a%zz HTTP/1.1\r\nHost: h\r\n\r\n', 400, 'default'),
        ('target-over', b'GET /' + b'a' * 128 + b' HTTP/1.1\r\nHost: h\r\n\r\n', 414, 'small'),
        ('header-line-over', b'GET / HTTP/1.1\r\nHost: h\r\nX: ' + b'a' * 252 + b'\r\n\r\n', 431, 'small'),
        ('header-count-over', b'GET / HTTP/1.1\r\nHost: h\r\nX0: a\r\nX1: a\r\nX2: a\r\nX3: a\r\nX4: a\r\n\r\n', 431, 'small'),
        ('header-bytes-over', b'GET / HTTP/1.1\r\nHost: h\r\nX: ' + b'a' * 250 + b'\r\nY: ' + b'b' * 250 + b'\r\n\r\n', 431, 'small'),
        ('body-over', b'POST / HTTP/1.1\r\nHost: h\r\nContent-Length: 17\r\n\r\n' + b'a' * 17, 413, 'small'),
        ('chunk-plus', b'POST / HTTP/1.1\r\nHost: h\r\nTransfer-Encoding: chunked\r\n\r\n+5\r\nhello\r\n0\r\n\r\n', 400, 'default'),
        ('chunk-hex-prefix', b'POST / HTTP/1.1\r\nHost: h\r\nTransfer-Encoding: chunked\r\n\r\n0x5\r\nhello\r\n0\r\n\r\n', 400, 'default'),
        ('chunk-overflow', b'POST / HTTP/1.1\r\nHost: h\r\nTransfer-Encoding: chunked\r\n\r\nfffffffffffffffff\r\n', 400, 'default'),
        ('chunk-size-over', b'POST / HTTP/1.1\r\nHost: h\r\nTransfer-Encoding: chunked\r\n\r\n11\r\n' + b'a' * 17 + b'\r\n0\r\n\r\n', 413, 'small'),
        ('chunk-missing-crlf', b'POST / HTTP/1.1\r\nHost: h\r\nTransfer-Encoding: chunked\r\n\r\n5\r\nhelloXX0\r\n\r\n', 400, 'default'),
        ('chunk-ext-malformed', b'POST / HTTP/1.1\r\nHost: h\r\nTransfer-Encoding: chunked\r\n\r\n5;=x\r\nhello\r\n0\r\n\r\n', 400, 'default'),
        ('chunk-ext-control', b'POST / HTTP/1.1\r\nHost: h\r\nTransfer-Encoding: chunked\r\n\r\n5;x="a\x01"\r\nhello\r\n0\r\n\r\n', 400, 'default'),
        ('trailer-forbidden', b'POST / HTTP/1.1\r\nHost: h\r\nTransfer-Encoding: chunked\r\n\r\n0\r\nContent-Length: 0\r\n\r\n', 400, 'default'),
        ('trailer-without-chunked', b'POST / HTTP/1.1\r\nHost: h\r\nContent-Length: 0\r\nTrailer: X\r\n\r\n', 400, 'default'),
        ('chunk-count-over', b'POST / HTTP/1.1\r\nHost: h\r\nTransfer-Encoding: chunked\r\n\r\n' + b'1\r\na\r\n' * 6 + b'0\r\n\r\n', 400, 'small'),
        ('chunk-metadata-over', b'POST / HTTP/1.1\r\nHost: h\r\nTransfer-Encoding: chunked\r\n\r\n' + (b'1;a=' + b'x' * 20 + b'\r\na\r\n') * 5 + b'0\r\n\r\n', 400, 'small'),
        ('trailer-count-over', b'POST / HTTP/1.1\r\nHost: h\r\nTransfer-Encoding: chunked\r\n\r\n0\r\nX-A: 1\r\nX-B: 2\r\nX-C: 3\r\n\r\n', 400, 'small'),
        ('trailer-bytes-over', b'POST / HTTP/1.1\r\nHost: h\r\nTransfer-Encoding: chunked\r\n\r\n0\r\n' + b'X-A: ' + b'a' * 30 + b'\r\nX-B: ' + b'b' * 30 + b'\r\n\r\n', 400, 'small'),
        ('te-on-http10', b'POST / HTTP/1.0\r\nHost: h\r\nTransfer-Encoding: chunked\r\n\r\n0\r\n\r\n', 400, 'http10'),
        ('http10-default', b'GET / HTTP/1.0\r\nHost: h\r\n\r\n', 505, 'default'),
        ('http12', b'GET / HTTP/1.2\r\nHost: h\r\n\r\n', 505, 'default'),
        ('http20', b'GET / HTTP/2.0\r\nHost: h\r\n\r\n', 505, 'default'),
        ('http09', b'GET / HTTP/0.9\r\nHost: h\r\n\r\n', 505, 'default'),
        ('no-version', b'GET /\r\nHost: h\r\n\r\n', 400, 'default'),
        ('connect', b'CONNECT h:443 HTTP/1.1\r\nHost: h\r\n\r\n', 501, 'default'),
        ('trace', b'TRACE / HTTP/1.1\r\nHost: h\r\n\r\n', 501, 'default'),
        ('upgrade', b'GET / HTTP/1.1\r\nHost: h\r\nUpgrade: websocket\r\n\r\n', 501, 'default'),
        ('connection-upgrade', b'GET / HTTP/1.1\r\nHost: h\r\nConnection: upgrade\r\n\r\n', 501, 'default'),
        ('expect-other', b'POST / HTTP/1.1\r\nHost: h\r\nContent-Length: 1\r\nExpect: something\r\n\r\na', 417, 'default'),
        ('expect-list', b'POST / HTTP/1.1\r\nHost: h\r\nContent-Length: 1\r\nExpect: 100-continue, foo\r\n\r\na', 417, 'default'),
        ('expect-twice', b'POST / HTTP/1.1\r\nHost: h\r\nContent-Length: 1\r\nExpect: 100-continue\r\nExpect: 100-continue\r\n\r\na', 417, 'default'),
        ('eof-body', b'POST / HTTP/1.1\r\nHost: h\r\nContent-Length: 5\r\n\r\nhe', 400, 'default'),
        ('eof-chunk', b'POST / HTTP/1.1\r\nHost: h\r\nTransfer-Encoding: chunked\r\n\r\n5\r\nhe', 400, 'default'),
        ('pipeline-bytes-over', b'GET /one HTTP/1.1\r\nHost: h\r\n\r\n' + b'A' * 600, 400, 'small'),
        ('extension-method-disabled', b'PATCHX / HTTP/1.1\r\nHost: h\r\n\r\n', 501, 'default'),
        ('asterisk-non-options', b'GET * HTTP/1.1\r\nHost: h\r\n\r\n', 400, 'default'),
    ]
    for name, req, status, limits in cases:
        # A valid request after the bad one must never be parsed.
        data = req + b'GET /smuggled HTTP/1.1\r\nHost: ok\r\n\r\n'
        modes = ('whole',) if name == 'pipeline-bytes-over' else ('whole', 'byte', 'frag')
        lines = parse_case(name, data, limits=limits, modes=modes)
        got = err_status(lines)
        expect(got == status, '%s: expected %d, got %r' % (name, status, got),
               '\n'.join(lines))
        expect('FAILED' in lines, name + ': error is terminal')
        err_at = next((j for j, line in enumerate(lines) if line.startswith('ERR ')), len(lines))
        expect(not any(line.startswith('HEAD ') for line in lines[err_at:]),
               name + ': no request parsed after error', '\n'.join(lines))


def test_http10_and_forms():
    lines = parse_case('http10', b'GET / HTTP/1.0\r\n\r\n', limits='http10')
    h = head_info(lines[0])
    expect(h['http10'] == 'true' and h['keep'] == 'false' and h['close'] == 'true',
           'HTTP/1.0 is always close')
    expect('CLOSED' in lines, 'HTTP/1.0 closes after one request')

    lines = parse_case('http10-keepalive',
                       b'GET / HTTP/1.0\r\nHost: h\r\nConnection: keep-alive\r\n\r\n',
                       limits='http10')
    expect(head_info(lines[0])['keep'] == 'false', 'HTTP/1.0 ignores keep-alive')

    absreq = (b'GET http://Example.COM:80/a%2e%2e?q=1 HTTP/1.1\r\n'
              b'Host: example.com\r\n\r\n')
    lines = parse_case('absolute', absreq)
    h = head_info(lines[0])
    expect(h['scheme'] == 'http', 'absolute scheme lowercased')
    expect(bytes.fromhex(h['auth']) == b'Example.COM:80', 'authority raw preserved')
    expect(bytes.fromhex(h['path']) == b'/a%2e%2e', 'absolute path raw')
    expect(bytes.fromhex(h['query']) == b'q=1', 'absolute query raw')

    lines = parse_case('absolute-default-port',
                       b'GET https://[::1]/x HTTP/1.1\r\nHost: [::1]:443\r\n\r\n')
    expect(err_status(lines) is None, 'IPv6 default port canonicalizes', repr(lines))

    lines = parse_case('host-ipv6-mismatch',
                       b'GET https://[::1]/x HTTP/1.1\r\nHost: [::1]:80\r\n\r\n')
    expect(err_status(lines) == 400, 'IPv6 authority mismatch is 400', repr(lines))

    lines = parse_case('asterisk', b'OPTIONS * HTTP/1.1\r\nHost: h\r\n\r\n')
    h = head_info(lines[0])
    expect(bytes.fromhex(h['path']) == b'*' and h['method'] == 'OPTIONS', 'OPTIONS *')

    lines = parse_case('extension-allowed', b'PATCHX /x HTTP/1.1\r\nHost: h\r\n\r\n',
                       limits='ext')
    expect(head_info(lines[0])['method'] == 'PATCHX', 'extension method policy opt-in')


def test_continue():
    headers_only = b'POST /c HTTP/1.1\r\nHost: h\r\nContent-Length: 5\r\nExpect: 100-continue\r\n\r\n'
    lines = run_parser('whole', 'default', headers_only)
    expect('CONT due' in lines, '100-continue is due only after HeadReady', repr(lines))
    expect('BODY' not in '\n'.join(lines), 'no body before it arrives')

    lines = run_parser('mark', 'default', headers_only)
    expect('CONTSENT' in lines and 'CONT due' in lines, 'markContinueSent transitions to sent',
           repr(lines))

    with_body = headers_only + b'hello'
    lines = parse_case('continue-body-buffered', with_body, modes=('whole',))
    expect('CONT skipped' in lines, '100 skipped when body bytes are already buffered', repr(lines))
    expect(body_bytes(lines) == b'hello', 'buffered body still parsed')

    bodyless = b'POST /0 HTTP/1.1\r\nHost: h\r\nContent-Length: 0\r\nExpect: 100-continue\r\n\r\n'
    lines = parse_case('continue-bodyless', bodyless, modes=('whole',))
    expect('CONT none' in lines, '100 is not sent for a bodyless request', repr(lines))

    lines = parse_case('continue-http10', b'POST / HTTP/1.0\r\nHost: h\r\n'
                       b'Content-Length: 1\r\nExpect: 100-continue\r\n\r\na',
                       limits='http10', modes=('whole',))
    expect('CONT none' in lines, 'HTTP/1.0 ignores Expect', repr(lines))

    lines = run_parser('whole', 'noauto', headers_only)
    expect('CONT due' in lines, 'manual autoContinue leaves the interim response due', repr(lines))


def test_limits_exact():
    target128 = b'GET /' + b'a' * 127 + b' HTTP/1.1\r\nHost: h\r\n\r\n'
    lines = parse_case('target-exact', target128, limits='small')
    expect(err_status(lines) is None, 'exact maxTarget accepted', repr(lines))

    header256 = b'GET / HTTP/1.1\r\nHost: h\r\nX: ' + b'a' * 251 + b'\r\n\r\n'
    lines = parse_case('header-line-exact', header256, limits='small')
    expect(err_status(lines) is None, 'exact maxHeaderLine accepted', repr(lines))

    count5 = b'GET / HTTP/1.1\r\nHost: h\r\nX0: a\r\nX1: a\r\nX2: a\r\nX3: a\r\n\r\n'
    lines = parse_case('header-count-exact', count5, limits='small')
    expect(err_status(lines) is None, 'exact maxHeaderCount accepted', repr(lines))

    exact_header_bytes = (b'GET / HTTP/1.1\r\nHost: h\r\n'
                          b'X: ' + b'a' * 160 + b'\r\n'
                          b'Y: ' + b'b' * 160 + b'\r\n'
                          b'Z: ' + b'c' * 150 + b'\r\n\r\n')
    lines = parse_case('header-bytes-exact', exact_header_bytes, limits='small')
    expect(err_status(lines) is None, 'exact maxHeaderBytes accepted', repr(lines))
    over_header_bytes = exact_header_bytes.replace(b'Z: ' + b'c' * 150,
                                                  b'Z: ' + b'c' * 151)
    lines = parse_case('header-bytes-over-exact', over_header_bytes, limits='small')
    expect(err_status(lines) == 431, 'maxHeaderBytes + 1 is 431', repr(lines))

    body16 = b'POST / HTTP/1.1\r\nHost: h\r\nContent-Length: 16\r\n\r\n' + b'a' * 16
    lines = parse_case('body-exact', body16, limits='small')
    expect(err_status(lines) is None and body_bytes(lines) == b'a' * 16,
           'exact maxBody accepted', repr(lines))

    chunk16 = (b'POST / HTTP/1.1\r\nHost: h\r\nTransfer-Encoding: chunked\r\n\r\n'
               b'10\r\n' + b'a' * 16 + b'\r\n0\r\n\r\n')
    lines = parse_case('chunk-exact', chunk16, limits='small')
    expect(err_status(lines) is None and body_bytes(lines) == b'a' * 16,
           'exact maxChunkSize accepted', repr(lines))

    chunks5 = (b'POST / HTTP/1.1\r\nHost: h\r\nTransfer-Encoding: chunked\r\n\r\n' +
               b'1\r\na\r\n' * 5 + b'0\r\n\r\n')
    lines = parse_case('chunk-count-exact', chunks5, limits='small')
    expect(err_status(lines) is None and body_bytes(lines) == b'aaaaa',
           'exact maxChunkCount accepted', repr(lines))


def test_fuzz():
    rng = random.Random(0x48545450)
    alphabet = b'GET / HTTP/1.1\r\nHost: h:0123456789;=,abcdefABCDEF\x00\x7f\xff'
    for i in range(60):
        n = rng.randrange(0, 220)
        data = bytes(alphabet[rng.randrange(len(alphabet))] for _ in range(n))
        data += b'GET /ok HTTP/1.1\r\nHost: ok\r\n\r\n'
        lines = run_parser('whole', 'default', data)
        expect(not any(l.startswith('DRVERR') for l in lines),
               'fuzz %d: parser driver error' % i, repr(lines))
        # Once an error is terminal no request may be parsed after it.
        err = None
        for j, line in enumerate(lines):
            if line.startswith('ERR '):
                err = j
                break
        if err is not None:
            expect(not any(l.startswith('HEAD ') for l in lines[err:]),
                   'fuzz %d: head after terminal error' % i, repr(lines))
        expect(lines and (lines[-1] in ('FAILED', 'CLOSED') or
                          any(l.startswith('ERR ') for l in lines)),
               'fuzz %d: no terminal result' % i, repr(lines))

    # Random raw-byte fragmentation of a valid request and a chunked request.
    valid = [b'GET /x?y=1 HTTP/1.1\r\nHost: h\r\nX: v\r\n\r\n',
             b'POST /p HTTP/1.1\r\nHost: h\r\nContent-Length: 5\r\n\r\nhello',
             b'POST /c HTTP/1.1\r\nHost: h\r\nTransfer-Encoding: chunked\r\n\r\n'
             b'1;x=y\r\na\r\n2\r\nbc\r\n0\r\nT: v\r\n\r\n']
    for data in valid:
        base = canon(run_parser('whole', 'default', data))
        for i in range(8):
            # The driver's frag mode is a deterministic fragmentation schedule.
            got = canon(run_parser('frag', 'default', data))
            expect(got == base, 'fragmentation changed a valid request', repr(got))


def response(status, reason, fields, body=b'', version='HTTP/1.1', close=False):
    lines = ['%s %d %s' % (version, status, reason), 'Date: ' + DATE]
    lines.extend('%s: %s' % (n, v) for n, v in fields)
    if close:
        lines.append('Connection: close')
    return ('\r\n'.join(lines) + '\r\n\r\n').encode() + body


def outputs(lines):
    out = b''
    for line in lines:
        if line.startswith('OUT '):
            out += bytes.fromhex(line.split(' ', 1)[1])
    return out


def test_writer_fixed_chunked():
    cmds = ['ctx GET 0 0 %d 0' % DATE_SECONDS, 'begin 200', 'length 5',
            'body 68656c6c6f', 'end', 'take']
    lines = run_writer('default', cmds)
    want = response(200, 'OK', [('Content-Length', '5')], b'hello')
    expect(outputs(lines) == want, 'fixed response exact bytes',
           repr(outputs(lines)) + ' != ' + repr(want))
    expect('OUT ' in '\n'.join(lines), 'output taken')

    cmds = ['ctx GET 0 0 %d 0' % DATE_SECONDS, 'begin 200',
            'header 436f6e74656e742d54797065 746578742f706c61696e',
            'length 5', 'body 68656c6c6f', 'end', 'take']
    lines = run_writer('default', cmds)
    want = response(200, 'OK', [('Content-Type', 'text/plain'),
                                ('Content-Length', '5')], b'hello')
    expect(outputs(lines) == want, 'application header order before framing', repr(outputs(lines)))

    cmds = ['ctx GET 0 0 %d 0' % DATE_SECONDS, 'begin 200',
            'header 436f6e74656e742d54797065 746578742f706c61696e',
            'length 5', 'body 68656c6c6f', 'end', 'take']
    lines = run_writer('server', cmds)
    want = response(200, 'OK', [('Content-Type', 'text/plain'),
                                ('Content-Length', '5')], b'hello')
    want = want.replace(b'Date: ' + DATE.encode() + b'\r\n',
                        b'Date: ' + DATE.encode() + b'\r\nServer: obo\r\n')
    expect(outputs(lines) == want, 'Server follows Date and precedes application fields',
           repr(outputs(lines)))

    cmds = ['ctx GET 0 0 %d 0' % DATE_SECONDS, 'begin 200',
            'body 68656c6c6f', 'body 776f726c64', 'end', 'take']
    lines = run_writer('default', cmds)
    want = response(200, 'OK', [('Transfer-Encoding', 'chunked')],
                    b'5\r\nhello\r\n5\r\nworld\r\n0\r\n\r\n')
    expect(outputs(lines) == want, 'chunked response exact bytes', repr(outputs(lines)))

    cmds = ['ctx GET 0 0 %d 0' % DATE_SECONDS, 'begin 204', 'end', 'take']
    lines = run_writer('default', cmds)
    expect(outputs(lines) == response(204, 'No Content', []), '204 has no body or CL')

    cmds = ['ctx GET 0 0 %d 0' % DATE_SECONDS, 'begin 205', 'end', 'take']
    lines = run_writer('default', cmds)
    expect(outputs(lines) == response(205, 'Reset Content', [('Content-Length', '0')]),
           '205 sends Content-Length: 0')

    cmds = ['ctx GET 0 0 %d 0' % DATE_SECONDS, 'begin 304', 'length 10', 'end', 'take']
    lines = run_writer('default', cmds)
    expect(outputs(lines) == response(304, 'Not Modified', [('Content-Length', '10')]),
           '304 carries length metadata without a body')

    cmds = ['ctx GET 0 0 %d 0' % DATE_SECONDS, 'begin 200', 'end', 'take']
    lines = run_writer('default', cmds)
    expect(outputs(lines) == response(200, 'OK', [('Content-Length', '0')]),
           'empty unknown-length response uses Content-Length: 0')

    cmds = ['ctx HEAD 0 0 %d 0' % DATE_SECONDS, 'begin 200', 'length 5',
            'body 68656c6c6f', 'end', 'take']
    lines = run_writer('default', cmds)
    expect(outputs(lines) == response(200, 'OK', [('Content-Length', '5')]),
           'HEAD sends metadata but no body')

    cmds = ['ctx HEAD 0 0 %d 0' % DATE_SECONDS, 'begin 200', 'end', 'take']
    lines = run_writer('default', cmds)
    expect(outputs(lines) == response(200, 'OK', []),
           'HEAD without setLength omits Content-Length')

    cmds = ['ctx GET 0 0 %d 0' % DATE_SECONDS, 'begin 201', 'length 0', 'end', 'take']
    lines = run_writer('default', cmds)
    expect(outputs(lines) == response(201, 'Created', [('Content-Length', '0')]),
           'default reason table')

    cmds = ['ctx GET 0 0 %d 0' % DATE_SECONDS, 'breason 299 437573746f6d',
            'length 0', 'end', 'take']
    lines = run_writer('default', cmds)
    expect(outputs(lines) == response(299, 'Custom', [('Content-Length', '0')]),
           'beginResponseWithReason')

    cmds = ['ctx GET 0 0 %d 0' % DATE_SECONDS, 'begin 200',
            'header 58 20206120', 'length 1', 'body 61', 'end', 'take']
    lines = run_writer('default', cmds)
    expect(outputs(lines) == response(200, 'OK', [('X', 'a'), ('Content-Length', '1')], b'a'),
           'header value OWS trimming')

    cmds = ['ctx GET 0 0 %d 0' % DATE_SECONDS, 'begin 200', 'length 0',
            'header 58 ' + ('61' * 250), 'end', 'take']
    lines = run_writer('small', cmds)
    expect(any(l.startswith('ERR ') for l in lines),
           'response header block over maxResponseHeaderBytes is refused', repr(lines))

    cmds = ['ctx GET 0 0 %d 0' % DATE_SECONDS, 'begin 205', 'body 61', 'end']
    lines = run_writer('default', cmds)
    expect(any(l.startswith('ERR ') for l in lines), '205 rejects body bytes', repr(lines))


def test_writer_rules_and_injection():
    cases = [
        (['ctx GET 0 0 %d 0' % DATE_SECONDS, 'begin 100'], 'ERR'),
        (['ctx GET 0 0 %d 0' % DATE_SECONDS, 'begin 204', 'length 1'], 'ERR'),
        (['ctx GET 0 0 %d 0' % DATE_SECONDS, 'begin 205', 'length 1'], 'ERR'),
        (['ctx GET 0 0 %d 0' % DATE_SECONDS, 'begin 304', 'body 61'], 'ERR'),
        (['ctx GET 0 0 %d 0' % DATE_SECONDS, 'begin 204', 'body 61'], 'ERR'),
        (['ctx GET 0 0 %d 0' % DATE_SECONDS, 'begin 200',
          'header 436f6e74656e742d4c656e677468 35'], 'ERR'),
        (['ctx GET 0 0 %d 0' % DATE_SECONDS, 'begin 200',
          'header 580d0a496e6a6563746564 6f6f70'], 'ERR'),
        (['ctx GET 0 0 %d 0' % DATE_SECONDS, 'begin 200',
          'header 58 610d0a496e6a65637465643a2078'], 'ERR'),
        (['ctx GET 0 0 %d 0' % DATE_SECONDS,
          'breason 200 4f4b0d0a496e6a65637465643a2078'], 'ERR'),
        (['ctx GET 0 0 %d 0' % DATE_SECONDS, 'begin 200', 'length 5',
          'body 6162', 'end'], 'ERR'),
        (['ctx GET 0 0 %d 0' % DATE_SECONDS, 'begin 200', 'length 3',
          'body 61626364', 'end'], 'ERR'),
        (['ctx GET 0 0 %d 0' % DATE_SECONDS, 'begin 200', 'length 1',
          'trailers 582d536b'], 'ERR'),
    ]
    for cmds, want in cases:
        lines = run_writer('default', cmds)
        expect(any(l.startswith('ERR ') for l in lines), 'expected writer error', repr(lines))

    cmds = ['ctx GET 0 0 %d 0' % DATE_SECONDS, 'begin 200',
            'header 5365742d436f6f6b6965 613d31',
            'header 5365742d436f6f6b6965 623d32',
            'body 61', 'end', 'take']
    lines = run_writer('default', cmds)
    out = outputs(lines)
    expect(out.count(b'Set-Cookie: a=1\r\n') == 1 and out.count(b'Set-Cookie: b=2\r\n') == 1,
           'duplicate application headers preserved')
    expect(b'Injected' not in out, 'injection never reaches output')


def test_writer_trailers_and_http10():
    cmds = ['ctx GET 0 0 %d 0' % DATE_SECONDS,
            'trailers 582d53756d', 'begin 200', 'body 68656c6c6f',
            'addtrailer 582d53756d 616263', 'end', 'take']
    lines = run_writer('default', cmds)
    want = response(200, 'OK',
                    [('Transfer-Encoding', 'chunked'), ('Trailer', 'X-Sum')],
                    b'5\r\nhello\r\n0\r\nX-Sum: abc\r\n\r\n')
    expect(outputs(lines) == want, 'response trailer exact framing', repr(outputs(lines)))

    cmds = ['ctx GET 0 0 %d 0' % DATE_SECONDS,
            'trailers 582d53756d', 'begin 200', 'body 61',
            'addtrailer 582d4e6f 61', 'end']
    lines = run_writer('default', cmds)
    expect(any(l.startswith('ERR ') for l in lines), 'undeclared trailer rejected', repr(lines))

    cmds = ['ctx GET 1 0 %d 0' % DATE_SECONDS, 'begin 200', 'body 68656c6c6f',
            'end', 'take']
    lines = run_writer('default', cmds)
    want = response(200, 'OK', [], b'hello', version='HTTP/1.0', close=True)
    expect(outputs(lines) == want, 'HTTP/1.0 close-delimited response', repr(outputs(lines)))

    cmds = ['ctx GET 1 0 %d 0' % DATE_SECONDS, 'begin 200', 'length 5',
            'body 68656c6c6f', 'end', 'take']
    lines = run_writer('default', cmds)
    want = response(200, 'OK', [('Content-Length', '5')], b'hello',
                    version='HTTP/1.0', close=True)
    expect(outputs(lines) == want, 'HTTP/1.0 fixed-length response', repr(outputs(lines)))


def test_writer_backpressure():
    # 162-byte header leaves five bytes in the 256-byte small buffer: a chunk
    # cannot fit until output is drained.
    value = '61' * 162
    cmds = ['ctx GET 0 0 %d 0' % DATE_SECONDS, 'begin 200',
            'header 58 ' + value, 'body ' + '62' * 16, 'take',
            'body ' + '62' * 16, 'end', 'take']
    lines = run_writer('small', cmds)
    ok = [l for l in lines if l.startswith('OK ')]
    expect('OK 0' in ok, 'full output buffer returns accepted count 0', repr(lines))
    out = outputs(lines)
    expect(out.endswith(b'10\r\n' + b'b' * 16 + b'\r\n0\r\n\r\n'),
           'backpressured chunk completes exactly once after draining', repr(out))
    expect(out.count(b'0\r\n\r\n') == 1, 'exactly one chunk terminator', repr(out))

    value150 = '61' * 150
    cmds = ['ctx GET 0 0 %d 0' % DATE_SECONDS, 'begin 200',
            'header 58 ' + value150, 'body ' + '62' * 16, 'end', 'take', 'end', 'take']
    lines = run_writer('small', cmds)
    expect('OK false' in lines, 'endResponse reports false while the terminator does not fit',
           repr(lines))
    expect(outputs(lines).endswith(b'0\r\n\r\n'), 'retried endResponse emits one terminator')


def test_writer_errors_and_date():
    for status, reason in [(400, 'Bad Request'), (413, 'Content Too Large'),
                           (414, 'URI Too Long'), (417, 'Expectation Failed'),
                           (429, 'Too Many Requests'),
                           (431, 'Request Header Fields Too Large'),
                           (501, 'Not Implemented'), (505, 'HTTP Version Not Supported')]:
        body = ('%d %s\n' % (status, reason)).encode()
        cmds = ['error %d 0 0' % status]
        lines = run_writer('default', cmds)
        want = response(status, reason,
                        [('Content-Type', 'text/plain; charset=utf-8'),
                         ('Content-Length', str(len(body)))], body, close=True)
        expect(outputs(lines) == want, 'error response %d exact' % status, repr(outputs(lines)))

        cmds = ['error %d 1 0' % status]
        lines = run_writer('default', cmds)
        want = response(status, reason,
                        [('Content-Type', 'text/plain; charset=utf-8'),
                         ('Content-Length', str(len(body)))], b'', close=True)
        expect(outputs(lines) == want, 'HEAD error response %d exact' % status,
               repr(outputs(lines)))

    cmds = ['continue']
    lines = run_writer('default', cmds)
    expect(outputs(lines) == b'HTTP/1.1 100 Continue\r\n\r\n', '100 Continue exact bytes')

    lines = run_date(['fmt', DATE_SECONDS, 0])
    expect(lines == 'DATE ' + DATE, 'formatHttpDate known vector', lines)
    lines = run_date(['fmt', 0, 0])
    expect(lines == 'DATE Thu, 01 Jan 1970 00:00:00 GMT', 'epoch date', lines)
    lines = run_date(['fmt', -62135596800, 0])
    expect(lines == 'DATE Mon, 01 Jan 0001 00:00:00 GMT', 'minimum date', lines)
    lines = run_date(['fmt', 253402300799, 0])
    expect(lines == 'DATE Fri, 31 Dec 9999 23:59:59 GMT', 'maximum date', lines)
    expect(run_date(['fmt', 0, 1000000000]).startswith('ERR '), 'nanos range rejected')
    expect(run_date(['fmt', 253402300800, 0]).startswith('ERR '), 'year range rejected')

    for text in ['Sun, 06 Nov 1994 08:49:37 GMT',
                 'Sunday, 06-Nov-94 08:49:37 GMT',
                 'Sun Nov  6 08:49:37 1994',
                 'sun, 06 nov 1994 08:49:37 gmt']:
        lines = run_date(['parse', 1727740800, text])
        expect(lines == 'TS 784111777 0', 'parse date %r' % text, lines)

    lines = run_date(['parse', 1727740800, 'Wed, 06 Nov 1994 08:49:37 GMT'])
    expect(lines == 'TS 784111777 0', 'weekday name is checked as known, not matched', lines)

    # RFC 850 50-year window: now 2026 -> 1994 is the most recent past match.
    lines = run_date(['parse', 1767225600, 'Sunday, 06-Nov-94 08:49:37 GMT'])
    expect(lines == 'TS 784111777 0', 'RFC850 50-year window', lines)
    lines = run_date(['parse', 0, 'Sunday, 06-Nov-99 08:49:37 GMT'])
    expect(lines.startswith('TS 941878177 '), 'RFC850 1999 from 1970', lines)
    lines = run_date(['parse', 1767225600, 'Wednesday, 06-Nov-96 08:49:37 GMT'])
    expect(lines.startswith('TS '), 'RFC850 full Wednesday weekday', lines)

    for bad in ['Sun, 06 Nov 1994 08:49:37 GMT x',
                'Sun, 31 Feb 1994 08:49:37 GMT',
                'Sun, 06 Nov 1994 24:49:37 GMT',
                'Sun, 06 Nov 1994 08:49:37 UTC']:
        lines = run_date(['parse', 1727740800, bad])
        expect(lines.startswith('ERR '), 'bad date rejected: %r' % bad, lines)


def main():
    tests = [
        ('valid requests', test_valid_requests),
        ('pipelining and request limits', test_pipelining),
        ('smuggling corpus', test_smuggling),
        ('HTTP/1.0 and target forms', test_http10_and_forms),
        ('100-continue state', test_continue),
        ('exact limits', test_limits_exact),
        ('fuzz and fragmentation', test_fuzz),
        ('response fixed/chunked', test_writer_fixed_chunked),
        ('response rules and injection', test_writer_rules_and_injection),
        ('response trailers and HTTP/1.0', test_writer_trailers_and_http10),
        ('response backpressure', test_writer_backpressure),
        ('error responses and dates', test_writer_errors_and_date),
    ]
    bootstrap()
    failures = 0
    for name, fn in tests:
        try:
            fn()
            print('ok   ' + name)
        except Fail as e:
            failures += 1
            print('FAIL ' + name + ': ' + str(e))
        except Exception as e:  # noqa: BLE001
            failures += 1
            print('FAIL ' + name + ': unexpected %s: %s' % (type(e).__name__, e))
    if failures:
        print('%d H1a0 test group(s) failed' % failures)
        return 1
    print('all H1a0 tests passed')
    return 0


if __name__ == '__main__':
    sys.exit(main())
