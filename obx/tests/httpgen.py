#!/usr/bin/env python3
"""Generated test of the http client's reply framing (lib/http.obx, design/http-client.md H1).

usage: python3 obx/tests/httpgen.py [--compiler PATH] [-n N] [--seed S] [-j N] [--keep DIR]

Each program serves a few cases from the scripted server of lib/tests/support/httptest.obx.
A case is one to three replies on a fresh listener, each with a random body framed one of the
ways RFC 9112 allows: Content-Length (any header case, repeated, a list of equal values, leading
zeros), chunked (random chunk sizes, hex in either case with leading zeros, extensions, trailers,
"Chunked"), until the connection closes (HTTP/1.1 without a length, HTTP/1.0), and no body (HEAD,
204, 304 with a length). Replies may carry interim 100/103 replies, padding headers,
Connection: close or keep-alive, Keep-Alive: timeout=N, and may be written a byte at a time.
The client reads each body with text(), bytes() or read() into a buffer of 1 to 4096 bytes and
the program checks it against the body sent, then checks how many connections the case took
(the generator works out which replies leave the connection reusable). Each program is built
through C and with --native and both run under OBX_CHECK=1 (a leak exits 5): every case must
print "ok" and both outputs must be equal. Exit 1 otherwise; failing programs are kept (in
--keep, or a temporary directory it names).
"""
import argparse, os, random, shutil, subprocess, sys, tempfile
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, '..'))
OBXC = os.environ.get('OBXC') or os.path.join(ROOT, 'compiler', 'out', 'obxc')
SUPPORT = os.path.join(ROOT, 'lib', 'tests', 'support', 'httptest.obx')

HEAD = '''import "task"
import "net"
import "http"
import "../support/httptest" as ht

// The body the client reads: mode 0 text(), 1 bytes(), 2 read() into size bytes at a time.
fn body(r: Result[http.Response, http.Error], mode: int, size: int): str {
    match move(r) {
        Ok(x) {
            if mode == 0 {
                match x.text() {
                    Ok(t) { return t }
                    Err(e) { return "ERROR " + e.toStr() }
                }
            }
            if mode == 1 {
                match x.bytes() {
                    Ok(b) { return strFromBytes(&b) }
                    Err(e) { return "ERROR " + e.toStr() }
                }
            }
            var buf = array(size, byte(0))
            var got = ""
            var rd = x
            while true {
                match rd.read(mut &buf) {
                    Ok(n) {
                        if n == 0 { return got }
                        got = got + strFromBytes(&buf[0..n])
                    }
                    Err(e) { return "ERROR " + e.toStr() }
                }
            }
            return got
        }
        Err(e) { return "ERROR " + e.toStr() }
    }
}

fn run(name: str, steps: &[]ht.Step, method: str, want: &[]str, mode: int, size: int, dials: int) {
    let line = task.scoped(fn(s: mut &task.Scope): str {
        let ln = net.listen("127.0.0.1:0").unwrap()
        let u = "http://" + ln.localAddr() + "/"
        var mine: []ht.Step = []
        for x in steps { append(mut &mine, x) }
        let srv = s.start(ht.scripted, ht.Script{ln: ln, steps: mine})
        let c = http.newClient(http.defaults())
        var bad = ""
        for i in 0..len(want) {
            let rq = http.request(method, u)
            let got = body(c.send(&rq), mode, size)
            if got != want[i] {
                bad = bad + " request " + intToStr(i) + ": " + intToStr(len(got)) + " bytes, want " + intToStr(len(want[i])) + ": " + got[0..min(len(got), 120)]
            }
        }
        let d = c.stats().dials
        if d != dials { bad = bad + " dials " + intToStr(d) + ", want " + intToStr(dials) }
        return if len(bad) == 0 { "ok" } else { "BAD" + bad }
    })
    println(name + ": " + line)
}

fn main() {
'''

SAFE = 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 ,.;:!-_=+*/()[]<>?'


def lit(s):
    """an obx string literal"""
    return '"' + s.replace('\\', '\\\\').replace('"', '\\"').replace('\r', '\\r').replace('\n', '\\n') + '"'


def case_of(rng, name):
    if rng.random() < 0.3:
        return rng.choice([name.upper(), name.lower(), name.title()])
    return name


def make_body(rng):
    r = rng.random()
    n = 0 if r < 0.1 else rng.randint(1, 40) if r < 0.6 else rng.randint(41, 600) if r < 0.85 else rng.randint(600, 9000)
    out = []
    for _ in range(n):
        out.append('\r\n' if rng.random() < 0.02 else rng.choice(SAFE))
    return ''.join(out)[:n]


def reply(rng, method):
    """(text, body the client sees, kept) for one reply"""
    body = make_body(rng)
    head = method == 'HEAD'
    minor = 0 if rng.random() < 0.15 else 1
    framing = rng.choice(['length'] * 4 + ['chunked'] * 4 + ['close', 'empty'])
    if minor == 0 and framing == 'chunked':
        framing = 'length'
    lines = []
    status = '200 OK'
    if framing == 'empty':
        status = rng.choice(['204 No Content', '304 Not Modified'])
    extra = []
    for _ in range(rng.randint(0, 3)):
        extra.append('X-Pad-%d: %s' % (rng.randint(0, 99), ''.join(rng.choice(SAFE) for _ in range(rng.randint(0, 30))).strip()))
    closing = rng.random() < 0.1
    ka_token = minor == 0 and framing != 'close' and rng.random() < 0.7
    ka_timeout = None
    if rng.random() < 0.15:
        ka_timeout = rng.choice([1, 5, 60])
    if closing:
        extra.append(case_of(rng, 'Connection') + ': close')
    elif ka_token:
        extra.append(case_of(rng, 'Connection') + ': keep-alive')
    if ka_timeout is not None:
        extra.append(case_of(rng, 'Keep-Alive') + ': timeout=%d' % ka_timeout + rng.choice(['', ', max=100']))
    payload = ''
    seen = body
    if framing == 'length':
        n = len(body)
        v = rng.random()
        if v < 0.15:
            lines.append('Content-Length: %d, %d' % (n, n))
        elif v < 0.3:
            lines.append(case_of(rng, 'Content-Length') + ': %d' % n)
            lines.append(case_of(rng, 'Content-Length') + ': %d' % n)
        elif v < 0.4:
            lines.append('Content-Length: 00%d' % n)
        else:
            lines.append(case_of(rng, 'Content-Length') + ': %d' % n)
        payload = '' if head else body
    elif framing == 'chunked':
        lines.append(case_of(rng, 'Transfer-Encoding') + ': ' + rng.choice(['chunked', 'chunked', 'Chunked']))
        parts = []
        i = 0
        while i < len(body):
            k = rng.randint(1, max(1, min(len(body) - i, rng.choice([1, 5, 50, 1000, 5000]))))
            size = '%x' % k
            if rng.random() < 0.3:
                size = size.upper()
            if rng.random() < 0.1:
                size = '0' + size
            ext = rng.choice(['', '', '', ';a=b', ';x', ';n="q v"'])
            parts.append(size + ext + '\r\n' + body[i:i + k] + '\r\n')
            i += k
        last = rng.choice(['0', '0', '000']) + rng.choice(['', ';e=1'])
        trailers = ''
        if rng.random() < 0.3:
            trailers = 'X-Trailer: t%d\r\n' % rng.randint(0, 9)
        payload = '' if head else ''.join(parts) + last + '\r\n' + trailers + '\r\n'
    elif framing == 'close':
        payload = '' if head else body
    else:
        if status.startswith('304') and rng.random() < 0.5:
            lines.append('Content-Length: %d' % rng.randint(1, 50))
        seen = ''
    if head:
        seen = ''
    interim = ''
    if minor == 1 and rng.random() < 0.15:
        interim = rng.choice(['HTTP/1.1 100 Continue\r\n\r\n', 'HTTP/1.1 103 Early Hints\r\nLink: </a>\r\n\r\n'])
    allh = lines + extra
    rng.shuffle(allh)
    text = interim + 'HTTP/1.%d %s\r\n' % (minor, status) + ''.join(h + '\r\n' for h in allh) + '\r\n' + payload
    close_delimited = framing == 'close' and not head
    kept = not closing and (minor == 1 or ka_token) and not close_delimited and ka_timeout != 1
    return text, seen, kept


def gen(rng, k):
    out = [HEAD]
    for c in range(6):
        method = 'HEAD' if rng.random() < 0.1 else 'GET'
        n = rng.randint(1, 3)
        steps, want = [], []
        dials = 1
        for i in range(n):
            text, seen, kept = reply(rng, method)
            dribble = 1 if len(text) < 80 and rng.random() < 0.2 else 0
            steps.append('ht.Step{reply: %s, close: %s, dribbleMs: %d}' % (lit(text), 'false' if kept else 'true', dribble))
            want.append(lit(seen))
            if i < n - 1 and not kept:
                dials += 1
        mode = rng.randint(0, 2)
        size = rng.choice([1, 7, 100, 4096])
        out.append('    run("case %d", &[%s], "%s", &[%s], %d, %d, %d)\n' % (
            c, ', '.join(steps), method, ', '.join(want), mode, size, dials))
    out.append('}\n')
    return ''.join(out)


def one(k, a, out):
    rng = random.Random(a.seed + k)
    base = os.path.join(out, 'run', 'p%d' % k)
    with open(base + '.obx', 'w') as f:
        f.write(gen(rng, k))
    c = subprocess.run([a.compiler, base + '.obx', '-o', base + '_c'], capture_output=True, text=True)
    if c.returncode != 0:
        return ('fail', k, 'C build failed: ' + (c.stdout + c.stderr).strip()[:400])
    nb = subprocess.run([a.compiler, base + '.obx', '--native', '-o', base + '_n'], capture_output=True, text=True)
    if nb.returncode != 0:
        return ('fail', k, 'native build failed: ' + (nb.stdout + nb.stderr).strip()[:400])
    env = dict(os.environ)
    env['OBX_CHECK'] = '1'
    try:
        rc = subprocess.run([base + '_c'], env=env, capture_output=True, timeout=60)
        rn = subprocess.run([base + '_n'], env=env, capture_output=True, timeout=60)
    except subprocess.TimeoutExpired:
        return ('fail', k, 'timeout')
    for f in ('_c', '_n'):
        os.remove(base + f)
    if (rc.returncode, rc.stdout, rc.stderr) != (rn.returncode, rn.stdout, rn.stderr):
        return ('fail', k, 'C exit %d, native exit %d: %r' % (rc.returncode, rn.returncode, (rc.stdout + rn.stdout)[-400:]))
    lines = rc.stdout.decode(errors='replace').splitlines()
    bad = [l for l in lines if not l.endswith(': ok')]
    if rc.returncode != 0 or len(lines) != 6 or bad:
        return ('fail', k, 'exit %d: %s' % (rc.returncode, ' | '.join(bad)[:600] or rc.stderr.decode(errors='replace')[:300]))
    os.remove(base + '.obx')
    return ('ok', k, '')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--compiler', default=OBXC)
    ap.add_argument('-n', type=int, default=40)
    ap.add_argument('--seed', type=int, default=None)
    ap.add_argument('-j', type=int, default=os.cpu_count() or 4)
    ap.add_argument('--keep', default=None)
    a = ap.parse_args()
    a.compiler = os.path.abspath(a.compiler)
    if a.seed is None:
        a.seed = random.randrange(1 << 30)
    out = a.keep or tempfile.mkdtemp(prefix='httpgen')
    os.makedirs(os.path.join(out, 'run'), exist_ok=True)
    os.makedirs(os.path.join(out, 'support'), exist_ok=True)
    shutil.copy(SUPPORT, os.path.join(out, 'support', 'httptest.obx'))
    with ThreadPoolExecutor(max_workers=a.j) as ex:
        res = list(ex.map(lambda k: one(k, a, out), range(a.n)))
    fails = [r for r in res if r[0] == 'fail']
    for _, k, why in fails:
        print('FAIL p%d.obx: %s' % (k, why))
    print('httpgen: seed %d: %d programs (%d cases), %d failed%s' % (
        a.seed, a.n, a.n * 6, len(fails), ' (programs in %s/run)' % out if fails else ''))
    if not fails and not a.keep:
        shutil.rmtree(out, ignore_errors=True)
    sys.exit(1 if fails else 0)


if __name__ == '__main__':
    main()
