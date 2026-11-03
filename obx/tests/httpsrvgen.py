#!/usr/bin/env python3
"""Differential test of the HTTP server's request parsing (lib/httpserver.obx, design/http-server.md
section 5.2, item 2) against Go's net/http and Node's llhttp.

usage: python3 obx/tests/httpsrvgen.py [--compiler PATH] [-n N] [--seed S] [-j N] [--show]

It builds an echo server with obx (through C and with --native, both under OBX_CHECK=1) and with Go
(go build), each answering every request with "METHOD TARGET BODYHEX" (the raw target, the body as
read after the framing). Node's server cannot say which requests of a pipelined stream it took (it
writes the 400 for a later request before the replies to the earlier ones), so its parser, llhttp
in the strict mode the server uses, runs on each stream directly, with the server's Host rule.

The generator makes N request streams from a grammar with mutations: one to three pipelined
requests, the last with Connection: close; methods, targets (origin and absolute form), versions,
Host, padding headers in any case, bodies framed by Content-Length or chunked (sizes in either case
with leading zeros, extensions, trailers) and the mutations that smuggling and desync attacks use
(CL and TE together, repeated or listed lengths, TE variants, obs-fold, whitespace before the
colon, bare LF and CR, NUL and other controls, bad chunk sizes and extensions, bad versions,
leading empty lines, Expect, long fields). Each stream goes to each server on a new connection;
replies are read until the server closes (or a second passes).

The properties, for every stream:
- the two obx builds give the same replies;
- obx accepts only what every oracle accepts, framed the same way: the requests obx answered with
  a 2xx before its first refusal must be the first requests each oracle took, with the same
  method, target and body. So where the oracles disagree, obx has refused by then. Known
  departures that are not framing decisions are listed in EXEMPT: an oracle that took the same
  requests as obx and then stopped at one of those says nothing about the rest;
  and replies from Go that stop with no refusal on a connection it closed are no verdict either
  (Go closes with the client's next bytes unread, and the kernel's reset can lose replies it sent);
- the obx servers stay up, and at the end stop with exit 0, nothing on stderr, nothing leaked.
A missing oracle (no go, no node) is skipped with a note. Exit 1 on any failure (the stream and
each server's replies are printed). The summary counts the streams obx refuses where the oracles
agree to accept, by the mutation it refused.
"""
import argparse, json, os, random, re, shutil, socket, subprocess, sys, tempfile, time
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, '..'))
OBXC = os.environ.get('OBXC') or os.path.join(ROOT, 'compiler', 'out', 'obxc')

OBX_SERVER = r'''import "net"
import "io"
import "httpserver" as http

type App = value record { n: int }

const HEX = "0123456789abcdef"

fn echo(app: &App, rq: mut &http.Request, w: mut &http.Response): Result[Unit, http.Error] {
    let b = try rq.bytes(1000000)
    var out = newStrBuf()
    out.add(rq.method + " " + rq.rawTarget() + " ")
    for c in b {
        out.addByte(HEX[int(c) / 16])
        out.addByte(HEX[int(c) % 16])
    }
    return w.text(out.toStr())
}

fn stop(app: &App, rq: mut &http.Request, w: mut &http.Response): Result[Unit, http.Error] {
    rq.stopServer()
    w.header("Connection", "close")
    return w.text("bye")
}

fn main() {
    var r = http.router[App]()
    r.handle("/", echo)
    r.handle("POST /__stop", stop)
    var cfg = http.defaults()
    cfg.logFd = -1
    cfg.headerTimeoutMs = 2000
    cfg.idleTimeoutMs = 2000
    let ln = net.listen("127.0.0.1:0").unwrap()
    println(ln.localAddr())
    io.flushStdout().unwrap()
    match http.serve(ln, App{n: 0}, r, cfg) {
        Ok(s) { println("stopped: " + s.toStr()) }
        Err(e) { println("error: " + e.toStr()) }
    }
}
'''

GO_SERVER = r'''package main

import (
	"encoding/hex"
	"fmt"
	"io"
	"net"
	"net/http"
	"os"
)

func main() {
	ln, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		os.Exit(1)
	}
	fmt.Println(ln.Addr().String())
	http.Serve(ln, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		b, err := io.ReadAll(r.Body)
		if err != nil {
			w.Header().Set("Connection", "close")
			w.WriteHeader(400)
			return
		}
		fmt.Fprintf(w, "%s %s %s", r.Method, r.RequestURI, hex.EncodeToString(b))
	}))
}
'''

# Node's server answers a pipelined stream's 400 before the replies to the requests ahead of it
# (they wait on 'end' events), so it cannot say which requests it took; its parser (llhttp, strict,
# as the server configures it) runs here on each stream instead, with the server's Host rule.
NODE_PARSER = r'''const { HTTPParser, methods } = require('_http_common');
const rl = require('readline').createInterface({ input: process.stdin });
rl.on('line', line => {
  const msgs = [];
  let err = null, url = '', headers = [], body = [], method = '', done = false;
  const p = new HTTPParser();
  p.initialize(HTTPParser.REQUEST, {}, 16384);
  p[HTTPParser.kOnHeaders] = (h, u) => { if (!done) { headers = headers.concat(h || []); url += u || ''; } };   // after the head: trailers
  p[HTTPParser.kOnHeadersComplete] = (major, minor, h, m, u) => {
    if (h) headers = headers.concat(h);
    if (u) url = u;
    done = true;
    method = methods[m];
    let host = false;
    for (let i = 0; i < headers.length; i += 2) if (headers[i].toLowerCase() === 'host') host = true;
    if (major === 1 && minor === 1 && !host && err === null) err = 'NO_HOST';
    return 0;
  };
  p[HTTPParser.kOnBody] = (b, start, len) => { body.push(start === undefined ? Buffer.from(b) : Buffer.from(b.slice(start, start + len))); };
  p[HTTPParser.kOnMessageComplete] = () => {
    if (err === null) msgs.push(method + ' ' + url + ' ' + Buffer.concat(body).toString('hex'));
    url = ''; headers = []; body = []; done = false;
  };
  const r = p.execute(Buffer.from(line, 'hex'));
  if (r instanceof Error && err === null) err = r.code;
  console.log(JSON.stringify({ msgs, err }));
});
'''


# ---------------------------------------------------------------- the grammar

TCHARS = 'abcdefghijklmnopqrstuvwxyz0123456789-_.~'


def word(rng, n=None):
    return ''.join(rng.choice(TCHARS[:36]) for _ in range(n or rng.randint(1, 8)))


def case(rng, name):
    k = rng.randrange(4)
    if k == 0:
        return name
    if k == 1:
        return name.lower()
    if k == 2:
        return name.upper()
    return ''.join(c.upper() if rng.random() < 0.5 else c.lower() for c in name)


def body(rng):
    n = rng.choice([0, 1, 3, 10, 50, 300])
    return bytes(rng.choice(b'abcxyz019 \r\n\x00\xff') for _ in range(n))


def chunked(rng, data, mut):
    out = b''
    i = 0
    while i < len(data):
        k = min(len(data) - i, rng.choice([1, 2, 7, 64, 300]))
        size = '%x' % k
        if rng.random() < 0.2:
            size = size.upper()
        if rng.random() < 0.1:
            size = '0' * rng.randint(1, 3) + size
        ext = ''
        if rng.random() < 0.15:
            ext = rng.choice([';a', ';a=b', ';a="x y"', ';a=b;c=d'])
        if mut == 'chunk-space':
            size = size + ' '
        elif mut == 'chunk-0x':
            size = '0x' + size
        elif mut == 'chunk-neg':
            size = '-' + size
        elif mut == 'chunk-ext-bad':
            ext = rng.choice([';', '; a', ';a=', ';=b', ';a=b c'])
        elif mut == 'chunk-overflow':
            size = 'f' * 17
        sep = b'\n' if mut == 'chunk-lf' else b'\r\n'
        out += (size + ext).encode() + sep + data[i:i + k] + (b'X\r\n' if mut == 'chunk-long' else b'\r\n')
        i += k
        mut = None              # one mutation per body
    last = b'0\r\n'
    if rng.random() < 0.2:
        last += b'X-Trailer: 1\r\n'
    if mut == 'trailer-fold':
        last += b'X-T: 1\r\n 2\r\n'
    return out + last + b'\r\n'


MUTATIONS = [None] * 12 + [
    'cl-te', 'te-cl', 'cl-twice-same', 'cl-twice-diff', 'cl-list', 'cl-plus', 'cl-space-inside', 'cl-big',
    'te-identity', 'te-gzip', 'te-twice', 'te-list', 'te-xchunked', 'te-space', 'te-upper', 'te-tab',
    'obs-fold', 'space-colon', 'tab-colon', 'bare-lf', 'bare-cr', 'nul-value', 'ctl-value', 'del-value',
    'no-host', 'two-hosts', 'bad-host', 'abs-form', 'abs-form-other', 'version-10', 'version-12', 'version-20',
    'version-lower', 'two-spaces', 'trailing-space', 'leading-crlf', 'leading-3crlf', 'expect', 'expect-other',
    'chunk-space', 'chunk-0x', 'chunk-neg', 'chunk-ext-bad', 'chunk-overflow', 'chunk-lf', 'chunk-long',
    'trailer-fold', 'method-lower', 'target-space', 'target-nul', 'target-dotdot', 'header-utf8', 'empty-name',
    'long-header',
]


# Where an oracle departs from RFC 9112 by its own choice rather than by framing: Go refuses a request
# after empty lines (9112 2.2 says a server SHOULD ignore at least one); llhttp knows a fixed list of
# methods (any token is a method, 9110 9.1) and versions (a 1.x minor version is 1.1, 9112 2.3).
EXEMPT = {'go': {'leading-crlf'}, 'node': {'method-lower', 'version-12'}}


def request(rng, last):
    mut = rng.choice(MUTATIONS)
    method = rng.choice(['GET', 'POST', 'PUT', 'DELETE', 'PATCH'])
    if mut == 'method-lower':
        method = method.lower()
    target = '/' + '/'.join(word(rng) for _ in range(rng.randint(0, 3)))
    if rng.random() < 0.3:
        target += '?' + '&'.join(word(rng) + '=' + word(rng) for _ in range(rng.randint(1, 3)))
    if mut == 'abs-form':
        target = 'http://test' + target
    elif mut == 'abs-form-other':
        target = 'http://other' + target
    elif mut == 'target-space':
        target = target + ' x'
    elif mut == 'target-nul':
        target = target + '%00'
    elif mut == 'target-dotdot':
        target = '/a/../b'
    version = 'HTTP/1.1'
    if mut == 'version-10':
        version = 'HTTP/1.0'
    elif mut == 'version-12':
        version = 'HTTP/1.2'
    elif mut == 'version-20':
        version = 'HTTP/2.0'
    elif mut == 'version-lower':
        version = 'http/1.1'
    sp = '  ' if mut == 'two-spaces' else ' '
    line = method + sp + target + ' ' + version + (' ' if mut == 'trailing-space' else '')
    headers = []
    host = 'test'
    if mut == 'bad-host':
        host = rng.choice(['a b', 'a/b', 'u@a', 'test:8x', ''])
    if mut != 'no-host':
        headers.append((case(rng, 'Host'), host))
    if mut == 'two-hosts':
        headers.append(('Host', 'test'))
    for _ in range(rng.randint(0, 3)):
        headers.append((case(rng, 'X-Pad-' + word(rng, 3)), word(rng)))
    data = body(rng) if method in ('POST', 'PUT', 'PATCH') or rng.random() < 0.1 else b''
    framing = 'cl' if data and rng.random() < 0.6 else ('chunked' if data else ('cl' if rng.random() < 0.3 else 'none'))
    cl = str(len(data))
    if mut == 'cl-big':
        cl = '1' * 25
    raw_body = data
    if framing == 'chunked' or mut in ('te-cl', 'cl-te'):
        raw_body = chunked(rng, data, mut if mut and mut.startswith(('chunk', 'trailer')) else None)
        te = 'chunked'
        if mut == 'te-identity':
            te = 'chunked, identity'
        elif mut == 'te-gzip':
            te = 'gzip, chunked'
        elif mut == 'te-list':
            te = 'chunked, chunked'
        elif mut == 'te-xchunked':
            te = 'xchunked'
        elif mut == 'te-space':
            te = ' chunked '
        elif mut == 'te-upper':
            te = 'CHUNKED'
        elif mut == 'te-tab':
            te = '\tchunked'
        if mut == 'cl-te':
            headers.append(('Content-Length', str(len(raw_body))))
        headers.append((case(rng, 'Transfer-Encoding'), te))
        if mut == 'te-cl':
            headers.append(('Content-Length', str(len(raw_body))))
        if mut == 'te-twice':
            headers.append(('Transfer-Encoding', 'chunked'))
    elif framing == 'cl':
        if mut == 'cl-plus':
            cl = '+' + cl
        elif mut == 'cl-space-inside':
            cl = cl + ' 0'
        if mut == 'cl-list':
            headers.append((case(rng, 'Content-Length'), cl + ', ' + cl))
        else:
            headers.append((case(rng, 'Content-Length'), cl))
        if mut == 'cl-twice-same':
            headers.append(('Content-Length', cl))
        elif mut == 'cl-twice-diff':
            headers.append(('Content-Length', str(len(data) + 1)))
    if mut == 'expect':
        headers.append(('Expect', '100-continue'))
    elif mut == 'expect-other':
        headers.append(('Expect', 'tea'))
    elif mut == 'header-utf8':
        headers.append(('X-Name', 'café'))
    elif mut == 'long-header':
        headers.append(('X-Long', 'x' * 9000))
    if last:
        headers.append(('Connection', 'close'))
    out = (line + '\r\n').encode('latin-1')
    for i, (k, v) in enumerate(headers):
        colon = ':'
        if mut == 'space-colon' and i == len(headers) - 1:
            colon = ' :'
        elif mut == 'tab-colon' and i == len(headers) - 1:
            colon = '\t:'
        val = v
        if mut == 'nul-value' and i == 0:
            val = v + '\x00x'
        elif mut == 'ctl-value' and i == 0:
            val = v + '\x01x'
        elif mut == 'del-value' and i == 0:
            val = v + '\x7fx'
        end = '\r\n'
        if mut == 'bare-lf' and i == len(headers) // 2:
            end = '\n'
        elif mut == 'bare-cr' and i == len(headers) // 2:
            end = '\r'
        out += (k + colon + ' ' + val + end).encode('utf-8' if mut == 'header-utf8' else 'latin-1')
        if mut == 'obs-fold' and i == 0:
            out += b' folded\r\n'
    if mut == 'empty-name':
        out += b': x\r\n'
    out += b'\r\n' + raw_body
    if mut == 'leading-crlf':
        out = b'\r\n' + out
    elif mut == 'leading-3crlf':
        out = b'\r\n\r\n\r\n' + out
    return out, mut


def stream(rng):
    n = rng.choice([1, 1, 2, 3])
    parts = [request(rng, i == n - 1) for i in range(n)]
    return b''.join(p[0] for p in parts), [p[1] for p in parts]


# ---------------------------------------------------------------- talking to the servers

def exchange(addr, data, timeout=1.0):
    host, port = addr.rsplit(':', 1)
    s = socket.create_connection((host, int(port)))
    s.settimeout(timeout)
    got = b''
    closed = False
    try:
        try:
            s.sendall(data)
        except OSError:
            pass
        while True:
            d = s.recv(65536)
            if not d:
                closed = True
                break
            got += d
    except socket.timeout:
        pass
    except OSError:
        closed = True
    finally:
        s.close()
    return got, closed


def replies(data):
    """[(code, body)] of the final replies in data (1xx skipped)."""
    out = []
    while data.startswith(b'HTTP/1.'):
        i = data.find(b'\r\n\r\n')
        if i < 0:
            break
        head = data[:i].decode('latin-1').split('\r\n')
        try:
            code = int(head[0].split(' ')[1])
        except (IndexError, ValueError):
            break
        data = data[i + 4:]
        length = None
        chunk = False
        for h in head[1:]:
            k, _, v = h.partition(':')
            if k.strip().lower() == 'content-length':
                length = int(v.strip())
            if k.strip().lower() == 'transfer-encoding' and 'chunked' in v.lower():
                chunk = True
        b = b''
        if code < 200:
            continue
        if chunk:
            while True:
                j = data.find(b'\r\n')
                if j < 0:
                    break
                n = int(data[:j].split(b';')[0], 16)
                data = data[j + 2:]
                if n == 0:
                    k = data.find(b'\r\n\r\n')
                    data = data[k + 4:] if data[:2] != b'\r\n' else data[2:]
                    break
                b += data[:n]
                data = data[n + 2:]
        elif length is not None:
            b, data = data[:length], data[length:]
        else:
            b, data = data, b''
        out.append((code, b.decode('latin-1')))
    return out


def accepted(rs):
    """The 2xx replies before the first other one."""
    out = []
    for code, b in rs:
        if code < 200 or code > 299:
            break
        out.append(b)
    return out


# ---------------------------------------------------------------- building and running

def start(cmd, env=None, cwd=None):
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env, cwd=cwd)
    line = p.stdout.readline().strip()
    if not re.match(r'^[0-9.]+:\d+$', line):
        p.kill()
        out, err = p.communicate()
        raise RuntimeError('%s: no address: %r %r' % (cmd[0], line, err[-500:]))
    return p, line


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--compiler', default=OBXC)
    ap.add_argument('-n', type=int, default=300)
    ap.add_argument('--seed', type=int, default=None)
    ap.add_argument('-j', type=int, default=16)
    ap.add_argument('--show', action='store_true', help='print every stream and its replies')
    args = ap.parse_args(argv)
    seed = args.seed if args.seed is not None else random.randrange(1 << 30)
    rng = random.Random(seed)
    tmp = tempfile.mkdtemp(prefix='obx-httpsrvgen-')
    procs = []
    try:
        src = os.path.join(tmp, 'echo.obx')
        open(src, 'w').write(OBX_SERVER)
        servers = []
        env = dict(os.environ, OBX_CHECK='1')
        for name, extra in [('obx-c', []), ('obx-native', ['--native'])]:
            exe = os.path.join(tmp, name)
            r = subprocess.run([args.compiler, src, '-o', exe] + extra, capture_output=True, text=True)
            if r.returncode:
                sys.exit('%s: %s%s' % (name, r.stdout, r.stderr))
            p, addr = start([exe], env=env)
            procs.append((name, p))
            servers.append((name, addr))
        notes = []
        go = shutil.which('go')
        if go:
            d = os.path.join(tmp, 'goecho')
            os.makedirs(d)
            open(os.path.join(d, 'main.go'), 'w').write(GO_SERVER)
            open(os.path.join(d, 'go.mod'), 'w').write('module goecho\n\ngo 1.21\n')
            r = subprocess.run([go, 'build', '-o', os.path.join(tmp, 'goecho-bin'), '.'], cwd=d, capture_output=True,
                               text=True, env=dict(os.environ, GOFLAGS='-mod=mod'))
            if r.returncode == 0:
                p, addr = start([os.path.join(tmp, 'goecho-bin')])
                procs.append(('go', p))
                servers.append(('go', addr))
            else:
                notes.append('go: build failed, skipped: ' + r.stderr[-300:])
        else:
            notes.append('go: not installed, skipped')
        node = shutil.which('node')
        js = os.path.join(tmp, 'parse.js')
        if node:
            open(js, 'w').write(NODE_PARSER)
        else:
            notes.append('node: not installed, skipped')
        for n in notes:
            print(n)
        oracles = [s[0] for s in servers if not s[0].startswith('obx')]
        cases = [stream(rng) for _ in range(args.n)]
        parsed = None
        if node:
            r = subprocess.run([node, js], input=''.join(c[0].hex() + '\n' for c in cases), capture_output=True, text=True)
            if r.returncode:
                sys.exit('node: ' + r.stderr[-800:])
            parsed = [json.loads(x) for x in r.stdout.splitlines()]
            if len(parsed) != len(cases):
                sys.exit('node: %d results for %d streams' % (len(parsed), len(cases)))
            oracles.append('node')

        def run(c):
            data, muts = c
            return {name: exchange(addr, data) for name, addr in servers}

        with ThreadPoolExecutor(args.j) as ex:
            results = list(ex.map(run, cases))
        failures = 0
        stricter = 0
        disagree = 0
        accepted_all = 0
        refused_all = 0
        exempt = {'go': 0, 'node': 0}
        lost = 0
        why = {}
        for k, ((data, muts), res) in enumerate(zip(cases, results)):
            got = {name: replies(r[0]) for name, r in res.items()}
            acc = {name: accepted(rs) for name, rs in got.items()}
            if parsed is not None:
                acc['node'] = parsed[k]['msgs']
                got['node'] = [(200, m) for m in parsed[k]['msgs']] + ([(400, parsed[k]['err'])] if parsed[k]['err'] else [])
            bad = []
            if got['obx-c'] != got['obx-native'] or res['obx-c'][1] != res['obx-native'][1]:
                bad.append('the obx builds differ')
            mine = acc['obx-c']
            for name in oracles:
                a = acc[name]
                # A known departure that is not a framing decision (EXEMPT): an oracle that took the
                # same requests as obx and then stopped at one of these says nothing about the rest.
                if len(a) < len(muts) and muts[len(a)] in EXEMPT[name] and mine[:len(a)] == a:
                    exempt[name] += 1
                    continue
                # Go closes a connection it will not read on (after HTTP/1.0, or Connection: close) with
                # the client's next bytes unread, so the kernel resets it and the client may lose replies
                # already sent: replies that stop with no refusal are no verdict on the rest (llhttp,
                # run offline, still judges it).
                if name == 'go' and len(a) < len(mine) and mine[:len(a)] == a and \
                        all(200 <= c <= 299 for c, _ in got[name]) and res[name][1]:
                    lost += 1
                    continue
                if a[:len(mine)] != mine:
                    bad.append('obx accepted what %s did not (or framed it differently)' % name)
            if oracles:
                o = [acc[name] for name in oracles]
                if any(x != o[0] for x in o):
                    disagree += 1
                elif len(mine) < len(o[0]):
                    stricter += 1
                    why[str(muts[len(mine)])] = why.get(str(muts[len(mine)]), 0) + 1
            if len(mine) == len(muts):
                accepted_all += 1
            if len(mine) == 0:
                refused_all += 1
            if bad or args.show:
                print('%s stream (mutations %s): %r' % ('FAIL' if bad else 'ok', muts, data[:600]))
                for name, rs in got.items():
                    print('   %-10s %s%s' % (name, [(c, b[:60]) for c, b in rs], (' closed' if res[name][1] else ' open') if name in res else ''))
                for b in bad:
                    print('   ' + b)
            if bad:
                failures += 1
        # the obx servers stop cleanly
        for name, addr in servers:
            if not name.startswith('obx'):
                continue
            exchange(addr, b'POST /__stop HTTP/1.1\r\nHost: t\r\nContent-Length: 0\r\n\r\n', 3.0)
        for name, p in procs:
            if not name.startswith('obx'):
                continue
            try:
                out, err = p.communicate(timeout=30)
            except subprocess.TimeoutExpired:
                p.kill()
                out, err = p.communicate()
                print('FAIL %s did not stop' % name)
                failures += 1
                continue
            if p.returncode != 0 or err.strip():
                print('FAIL %s: exit %d: %s' % (name, p.returncode, err[-800:]))
                failures += 1
        print('httpsrvgen: seed %d, %d streams, %d oracles (%s): %d accepted whole by obx, %d refused at once, '
              '%d where obx is stricter than the agreeing oracles, %d where the oracles disagree; %d failures'
              % (seed, len(cases), len(oracles), ', '.join(oracles) or 'none', accepted_all, refused_all,
                 stricter, disagree, failures))
        if why:
            print('  where obx is stricter, the mutation of the request it refused: ' +
                  ', '.join('%s %d' % (k, v) for k, v in sorted(why.items(), key=lambda x: -x[1])))
        if lost:
            print('  go replies lost to its reset after a close (no verdict): %d' % lost)
        if exempt['go'] or exempt['node']:
            print('  known departures (no say after them): go %d (leading empty line), node %d (method or version '
                  'outside llhttp\'s tables)' % (exempt['go'], exempt['node']))
        return 1 if failures else 0
    finally:
        for name, p in procs:
            if p.poll() is None:
                p.kill()
                p.communicate()
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
