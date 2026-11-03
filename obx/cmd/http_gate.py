#!/usr/bin/env python3
"""The C1b, C1c and C2 server gates (design/concurrency.md): cmd/httpd.obx holding
many idle keep-alive connections while it serves load, then shutting down
gracefully with no leaks (OBX_CHECK=1).

    python3 cmd/http_gate.py [--idle N] [--conns 1,10,100] [--seconds S]
                             [--servers native,c,go,node] [--compiler PATH] [--no-pin]
                             [--db 4,16]
    python3 cmd/http_gate.py --workers 1,2,4,8 [--servers native,c] [--db 4]
                             [--listen shared|reuseport|both] [--conns 256]
                             [--seconds S] [--no-kill] [--no-pin]

With --db (stage C1c), each obx server runs once per pool size with a
Postgres pool of that many connections ($OBX_PG_DSN, default the local
socket), and the load is GET /db: one query through the pool per request.
After the shutdown the server must report its pool with nothing waiting.
The go and node references need a Postgres driver (lib/pq or pgx in the Go
module cache, the pg module for node); without one they are skipped.

For each server: start it on 0.0.0.0 port 0 (so that the idle connections can
go to 127.0.0.1, .2, ...: one address pair has only the ephemeral port range,
4,096 ports on WSL2); open N idle connections (each
sends one request first, so it has been accepted and served); read the
server's RSS before and after (VmRSS), so the cost of an idle connection;
run cmd/httpload.obx (always the native build) with each client count for S
seconds while the idle connections stay open; check that the idle
connections still answer; then GET /shutdown and check that the program ends
with exit 0, nothing on stderr and every connection closed. go and node are
references when they are installed (GOMAXPROCS=1; node is single-threaded);
they are stopped with SIGTERM. Unless --no-pin, the server runs on CPU 0 and
the load client on CPU 1 (taskset), so each has one core.

With --workers 1,2,4,8 (stage C2) it is another gate: the scaling of
`httpd ADDR POOL WORKERS MODE` over worker processes (task.workers), for each
obx server (native, c), each listening MODE of --listen (shared: one
listener made before the fork; reuseport: one per worker, SO_REUSEPORT;
both) and each path: GET /, and GET /db with --db P (a pool of P
connections in each worker). No idle connections; --conns gives the load's
connections (default 256), spread over 8 load processes (httpload -p 8).
Pinning: n workers run on the first n CPUs of 0,2,4,6,1,3,5,7 (one hardware
thread of each of the first four cores, then their second threads), the
load on CPUs 8-15 (the other four cores); PostgreSQL is not pinned. After
each load: GET /shutdown; the server must end with exit 0, nothing on
stderr, every worker drained with exit 0 and its connections closed (and
its pool with nobody waiting). Then the kill test (unless --no-kill): 4
workers, GET /, the load reconnecting after an error (httpload -r); a third
of the way in, SIGKILL one worker. With the shared listener the requests
lost must be at most its connections (one request in flight on each; with
reuseport the connections in its own accept queue are lost too, so that is
only reported), the supervisor must start it
again, and the server must still serve and stop cleanly, the restart
counted in its report.

Exit status 0 when every obx server passes.
"""
import argparse
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))       # .../obx/cmd
OBX = os.path.dirname(HERE)
DEFAULT_OBXC = os.path.join(OBX, 'compiler', 'out', 'obxc')

GO_SRC = r'''package main

import (
	"fmt"
	"net"
	"net/http"
	"os"
)

func main() {
	ln, err := net.Listen("tcp", os.Args[1])
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
	http.HandleFunc("/", func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "text/plain")
		fmt.Fprint(w, "hello, world\n")
	})
	fmt.Println("listening on", ln.Addr().String())
	http.Serve(ln, nil)
}
'''

NODE_SRC = r'''const http = require('http');
const [host, port] = process.argv[2].split(':');
const srv = http.createServer((req, res) => {
  res.writeHead(200, {'Content-Type': 'text/plain', 'Content-Length': 13});
  res.end('hello, world\n');
});
srv.keepAliveTimeout = 0;      // no idle timeout (the default closes after 5 s)
srv.headersTimeout = 0;
srv.requestTimeout = 0;
srv.listen(+port, host, () => {
  console.log('listening on ' + host + ':' + srv.address().port);
});
'''


def log(*a):
    print(*a, flush=True)


def build(args, tmp):
    exes = {}
    obxc = args.compiler
    env = dict(os.environ, OBX_LIB=os.path.join(OBX, 'lib'))
    for name, src, native in [('httpd-native', 'httpd.obx', True), ('httpd-c', 'httpd.obx', False),
                              ('httpload', 'httpload.obx', True)]:
        out = os.path.join(tmp, name)
        cmd = [obxc, os.path.join(HERE, src), '-o', out] + (['--native'] if native else [])
        r = subprocess.run(cmd, capture_output=True, text=True, env=env)
        if r.returncode != 0:
            sys.exit('build failed: %s\n%s%s' % (' '.join(cmd), r.stdout, r.stderr))
        exes[name] = out
    go = shutil.which('go') or ('/home/chris/go/bin/go' if os.path.exists('/home/chris/go/bin/go') else None)
    if 'go' in args.servers and go:
        d = os.path.join(tmp, 'goref')
        os.makedirs(d)
        with open(os.path.join(d, 'main.go'), 'w') as f:
            f.write(GO_SRC)
        r = subprocess.run([go, 'build', '-o', os.path.join(tmp, 'httpd-go'), 'main.go'], cwd=d,
                           capture_output=True, text=True,
                           env=dict(os.environ, GO111MODULE='off', GOFLAGS='', GOCACHE=os.path.join(tmp, 'gocache')))
        if r.returncode == 0:
            exes['go'] = os.path.join(tmp, 'httpd-go')
        else:
            log('go build failed; no go reference\n' + r.stderr)
    node = shutil.which('node')
    if 'node' in args.servers and node:
        p = os.path.join(tmp, 'httpd.js')
        with open(p, 'w') as f:
            f.write(NODE_SRC)
        exes['node'] = (node, p)
    return exes


def rss_kb(pid):
    with open('/proc/%d/status' % pid) as f:
        for line in f:
            if line.startswith('VmRSS:'):
                return int(line.split()[1])
    return 0


def exchange(s, raw):
    """Sends raw, reads one reply; (status line, body)."""
    s.sendall(raw)
    data = b''
    while True:
        i = data.find(b'\r\n\r\n')
        if i >= 0:
            head = data[:i].decode()
            n = 0
            for line in head.split('\r\n')[1:]:
                k, _, v = line.partition(':')
                if k.strip().lower() == 'content-length':
                    n = int(v)
            while len(data) < i + 4 + n:
                d = s.recv(65536)
                if not d:
                    break
                data += d
            return head.split('\r\n')[0], data[i + 4:i + 4 + n].decode()
        d = s.recv(65536)
        if not d:
            return None, ''
        data += d


GET = b'GET / HTTP/1.1\r\nHost: gate\r\n\r\n'


def dial(port, k):
    """A connection to 127.0.0.k (the server listens on 0.0.0.0): one pair of addresses has only
    the ephemeral port range (4,096 ports on some systems), so the destination changes every 2,000
    connections. Not the source: a bind before connect costs about 10 ms on WSL2."""
    return socket.create_connection(('127.0.0.%d' % k, port))


def open_idle(host, port, n):
    """n connections, each served once; a batch at a time, so the backlog never overflows."""
    socks = []
    batch = 200
    while len(socks) < n:
        k = min(batch, n - len(socks))
        new = [dial(port, 1 + (len(socks) + i) // 2000) for i in range(k)]
        for s in new:
            s.sendall(GET)
        for s in new:
            st, body = exchange(s, b'')
            if st != 'HTTP/1.1 200 OK':
                raise RuntimeError('idle connection %d: %r' % (len(socks), st))
            socks.append(s)
    return socks


def parse_load(line):
    m = dict(re.findall(r'(\w+) (\d+)', line))
    return {k: int(v) for k, v in m.items()}


def run_server(name, exe, args, loader, pin, pool=0):
    env = dict(os.environ, OBX_CHECK='1', GOMAXPROCS='1')
    cmd = list(exe) if isinstance(exe, tuple) else [exe]
    cmd += ['0.0.0.0:0'] + ([str(pool)] if pool else [])
    if pin:
        cmd = ['taskset', '-c', '0'] + cmd
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)
    res = {'name': name, 'ok': True, 'notes': []}
    try:
        line = p.stdout.readline().strip()
        m = re.search(r'listening on (\S+):(\d+)$', line)
        if not m:
            raise RuntimeError('no address: %r' % line)
        host, port = '127.0.0.1', int(m.group(2))
        addr = '%s:%d' % (host, port)
        time.sleep(0.2)
        r0 = rss_kb(p.pid)
        t0 = time.monotonic()
        idle = open_idle(host, port, args.idle)
        res['open_s'] = time.monotonic() - t0
        time.sleep(0.5)
        r1 = rss_kb(p.pid)
        res['rss0'], res['rss1'] = r0, r1
        res['per_conn'] = (r1 - r0) * 1024 / max(1, args.idle)
        if name.startswith('obx'):
            s = socket.create_connection((host, port))
            st, body = exchange(s, b'GET /stats HTTP/1.1\r\n\r\n')
            s.close()
            m = re.search(r'open (\d+)', body)
            if not m or int(m.group(1)) < args.idle:
                raise RuntimeError('stats: %r' % body)
            res['notes'].append('stats: ' + body.strip().replace('\n', ', '))
        if pool:
            s = socket.create_connection((host, port))
            st, body = exchange(s, b'GET /db HTTP/1.1\r\n\r\n')
            s.close()
            if st != 'HTTP/1.1 200 OK' or not body.startswith('db '):
                raise RuntimeError('/db: %r %r' % (st, body))
        res['load'] = []
        for c in args.conns:
            lcmd = [loader, addr, str(c), str(args.seconds)] + (['/db'] if pool else [])
            if pin:
                lcmd = ['taskset', '-c', '1'] + lcmd
            r = subprocess.run(lcmd, capture_output=True, text=True, timeout=args.seconds + 60)
            if r.returncode != 0:
                raise RuntimeError('httpload %d: %s%s' % (c, r.stdout, r.stderr))
            d = parse_load(r.stdout)
            d['conns'] = c
            res['load'].append(d)
            if d.get('errors'):
                raise RuntimeError('httpload %d: errors: %s' % (c, r.stdout + r.stderr))
        res['rss2'] = rss_kb(p.pid)
        # the idle connections still answer (a sample)
        for s in idle[::max(1, len(idle) // 100)]:
            st, body = exchange(s, GET)
            if st != 'HTTP/1.1 200 OK':
                raise RuntimeError('an idle connection did not answer: %r' % st)
        if name.startswith('obx'):
            s = socket.create_connection((host, port))
            st, body = exchange(s, b'GET /shutdown HTTP/1.1\r\n\r\n')
            if body != 'bye\n':
                raise RuntimeError('shutdown: %r %r' % (st, body))
            t1 = time.monotonic()
            out, err = p.communicate(timeout=60)
            res['stop_s'] = time.monotonic() - t1
            if p.returncode != 0 or err:
                raise RuntimeError('exit %d, stderr %r' % (p.returncode, err[-2000:]))
            res['notes'].append(out.strip())
            if ', open 0' not in out:
                raise RuntimeError('connections left open: %r' % out)
            if pool and ' waiting 0' not in out:
                raise RuntimeError('tasks left waiting for the pool: %r' % out)
            # every idle connection sees the end of the stream
            for s in idle:
                s.settimeout(5)
                if s.recv(16) != b'':
                    raise RuntimeError('an idle connection got data after shutdown')
            s.close()
        else:
            p.send_signal(signal.SIGTERM)
            p.communicate(timeout=10)
        for s in idle:
            s.close()
    except Exception as e:     # noqa: BLE001
        res['ok'] = False
        res['notes'].append('FAILED: %s' % e)
        if p.poll() is None:
            p.kill()
        out, err = p.communicate()
        if err:
            res['notes'].append('stderr: ' + err[-2000:])
    return res


# ---- the stage C2 gate: worker processes

SERVER_CPUS = [0, 2, 4, 6, 1, 3, 5, 7]   # one thread of each of the first four cores, then the second
LOAD_CPUS = '8-15'
LOAD_PROCS = 8


def children(pid):
    try:
        with open('/proc/%d/task/%d/children' % (pid, pid)) as f:
            return [int(x) for x in f.read().split()]
    except OSError:
        return []


def sockets(pid):
    """The sockets a process holds (its listener and its connections)."""
    n = 0
    d = '/proc/%d/fd' % pid
    for fd in os.listdir(d):
        try:
            if os.readlink(os.path.join(d, fd)).startswith('socket:'):
                n += 1
        except OSError:
            pass
    return n


def get(port, path):
    s = socket.create_connection(('127.0.0.1', port))
    try:
        return exchange(s, ('GET %s HTTP/1.1\r\nHost: gate\r\n\r\n' % path).encode())
    finally:
        s.close()


def start_workers(exe, n, pool, mode, pin):
    cmd = [exe, '0.0.0.0:0', str(pool), str(n), mode]
    if pin:
        cmd = ['taskset', '-c', ','.join(str(c) for c in sorted(SERVER_CPUS[:n]))] + cmd
    env = dict(os.environ, OBX_CHECK='1')
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)
    line = p.stdout.readline().strip()
    m = re.search(r'listening on (\S+):(\d+)$', line)
    if not m:
        p.kill()
        out, err = p.communicate()
        raise RuntimeError('no address: %r %r' % (line, err))
    p.stdout.readline()                       # "N workers, ..."
    port = int(m.group(2))
    t = time.monotonic()
    while len(children(p.pid)) < n and time.monotonic() - t < 5:
        time.sleep(0.01)
    return p, port


def stop_workers(p, port, n, pool):
    """GET /shutdown, then the checks of the end; the per-worker accept counts."""
    st, body = get(port, '/shutdown')
    if body != 'bye\n':
        raise RuntimeError('shutdown: %r %r' % (st, body))
    out, err = p.communicate(timeout=60)
    if p.returncode != 0 or err:
        raise RuntimeError('exit %d, stderr %r' % (p.returncode, err[-2000:]))
    lines = out.strip().split('\n')
    mine = [l for l in lines if re.match(r'stopped: worker \d+: ', l)]
    ends = [l for l in lines if re.match(r'stopped: worker \d+ \(pid', l)]
    if len(ends) != n or any('exit 0, drained' not in l for l in ends):
        raise RuntimeError('worker ends: %r' % ends)
    if any(', open 0' not in l for l in mine) or (pool and any(' waiting 0' not in l for l in mine)):
        raise RuntimeError('a worker left connections open or tasks waiting: %r' % mine)
    accepted = [int(re.search(r'accepted (\d+)', l).group(1)) for l in mine]
    return accepted, ends


def run_load(loader, addr, conns, seconds, path, pin, reconnect=False):
    cmd = [loader, '-p', str(LOAD_PROCS)] + (['-r'] if reconnect else []) + [addr, str(conns), str(seconds), path]
    if pin:
        cmd = ['taskset', '-c', LOAD_CPUS] + cmd
    return subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


def scale_row(exe, loader, n, pool, mode, path, args, pin):
    p, port = start_workers(exe, n, pool, mode, pin)
    try:
        st, body = get(port, path)
        if st != 'HTTP/1.1 200 OK':
            raise RuntimeError('%s: %r %r' % (path, st, body))
        lp = run_load(loader, '127.0.0.1:%d' % port, args.conns, args.seconds, path, pin)
        out, err = lp.communicate(timeout=args.seconds + 60)
        if lp.returncode != 0:
            raise RuntimeError('httpload: %s%s' % (out, err))
        d = parse_load(out)
        if d.get('errors'):
            raise RuntimeError('httpload errors: %s%s' % (out, err))
        d['accepted'], _ = stop_workers(p, port, n, pool)
        return d
    finally:
        if p.poll() is None:
            p.kill()
            p.communicate()


def kill_test(exe, loader, mode, args, pin):
    n = 4
    p, port = start_workers(exe, n, 0, mode, pin)
    res = {}
    lp = None
    try:
        lp = run_load(loader, '127.0.0.1:%d' % port, args.conns, args.seconds, '/', pin, reconnect=True)
        time.sleep(args.seconds / 3)
        before = children(p.pid)
        victim = before[1]
        # its sockets: the shared listener (or its own) and its connections
        res['victim_conns'] = sockets(victim) - 1
        os.kill(victim, signal.SIGKILL)
        t = time.monotonic()
        new = []
        while time.monotonic() - t < 5:
            new = [c for c in children(p.pid) if c not in before]
            if new:
                break
            time.sleep(0.005)
        if not new:
            raise RuntimeError('the killed worker was not started again')
        res['restart_ms'] = (time.monotonic() - t) * 1000
        out, err = lp.communicate(timeout=args.seconds + 60)
        if lp.returncode != 0:
            raise RuntimeError('httpload: %s%s' % (out, err))
        d = parse_load(out)
        res.update(d)
        # with reuseport the worker's own accept queue dies with it: connections queued
        # there (not yet accepted) are reset too, so only the shared listener is held to this
        if mode == 'shared' and d['errors'] > res['victim_conns']:
            raise RuntimeError('%d requests lost, the worker had %d connections' % (d['errors'], res['victim_conns']))
        st, body = get(port, '/')                     # still serving
        if st != 'HTTP/1.1 200 OK':
            raise RuntimeError('after the kill: %r' % st)
        # the end: the killed worker's slot ended normally after one restart
        stp = get(port, '/shutdown')
        out, err = p.communicate(timeout=60)
        if p.returncode != 0 or err:
            raise RuntimeError('exit %d, stderr %r' % (p.returncode, err[-2000:]))
        ends = [l for l in out.split('\n') if re.match(r'stopped: worker \d+ \(pid', l)]
        if sum('1 restart' in l for l in ends) != 1 or any('exit 0, drained' not in l for l in ends):
            raise RuntimeError('worker ends: %r' % ends)
        res['ends'] = ends
        return res
    finally:
        for q in (lp, p):
            if q is not None and q.poll() is None:
                q.kill()
                q.communicate()


def main_workers(args, ap):
    counts = [int(x) for x in args.workers.split(',')]
    if max(counts) > len(SERVER_CPUS):
        sys.exit('at most %d workers (the server CPUs)' % len(SERVER_CPUS))
    args.conns = int(args.conns or '256')
    modes = ['shared', 'reuseport'] if args.listen == 'both' else [args.listen]
    servers = [s for s in args.servers.split(',') if s in ('native', 'c')]
    pool = int(args.db.split(',')[0]) if args.db else 0
    paths = ['/'] + (['/db'] if pool else [])
    pin = not args.no_pin and shutil.which('taskset') is not None and os.cpu_count() >= 16
    log('pinning: %s' % ('n workers on CPUs %s[:n], the load (httpload -p %d) on CPUs %s' %
                         (SERVER_CPUS, LOAD_PROCS, LOAD_CPUS) if pin else 'none'))
    tmp = tempfile.mkdtemp(prefix='obx-http-')
    bad = []
    try:
        args.servers = servers
        exes = build(args, tmp)
        for name in servers:
            exe = exes['httpd-' + name]
            for mode in modes:
                for path in paths:
                    log('== obx %s, %s listener, GET %s%s, %d connections, %d s per row' %
                        (name, mode, path, ' (pool %d per worker)' % pool if path == '/db' else '',
                         args.conns, args.seconds))
                    base = None
                    for n in counts:
                        try:
                            d = scale_row(exe, exes['httpload'], n, pool if path == '/db' else 0, mode, path, args, pin)
                        except Exception as e:      # noqa: BLE001
                            log('  %d workers: FAILED: %s' % (n, e))
                            bad.append('%s %s %s %d' % (name, mode, path, n))
                            continue
                        base = base or d['rps'] / n
                        log('  %d workers: %7d req/s (x%.2f), latency us p50 %d p99 %d; accepted per worker %s' %
                            (n, d['rps'], d['rps'] / base, d['p50'], d['p99'], d['accepted']))
            if not args.no_kill:
                for mode in modes:
                    log('== obx %s, %s listener: kill test (4 workers, GET /, %d connections, %d s, reconnecting)' %
                        (name, mode, args.conns, args.seconds))
                    try:
                        r = kill_test(exe, exes['httpload'], mode, args, pin)
                        log('  SIGKILL to a worker holding %d connections: %d requests lost, restarted in %.0f ms; '
                            '%d req/s over the run, p99 %d us' %
                            (r['victim_conns'], r['errors'], r['restart_ms'], r['rps'], r['p99']))
                        for l in r['ends']:
                            log('  ' + l)
                    except Exception as e:          # noqa: BLE001
                        log('  FAILED: %s' % e)
                        bad.append('%s %s kill' % (name, mode))
        log('FAIL: ' + ', '.join(bad) if bad else 'PASS')
        return 1 if bad else 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--idle', type=int, default=10000)
    ap.add_argument('--conns', default=None, help='load clients: default 1,10,100 (with --workers: 256)')
    ap.add_argument('--seconds', type=int, default=5)
    ap.add_argument('--servers', default='native,c,go,node')
    ap.add_argument('--compiler', default=os.environ.get('OBXC', DEFAULT_OBXC))
    ap.add_argument('--no-pin', action='store_true')
    ap.add_argument('--db', default='', help='pool sizes: serve GET /db through a Postgres pool')
    ap.add_argument('--workers', default='', help='worker counts: the stage C2 gate (see above)')
    ap.add_argument('--listen', default='shared', help='with --workers: shared, reuseport or both')
    ap.add_argument('--no-kill', action='store_true', help='with --workers: no kill test')
    args = ap.parse_args(argv)
    if args.workers:
        return main_workers(args, ap)
    args.conns = [int(c) for c in (args.conns or '1,10,100').split(',')]
    args.servers = args.servers.split(',')
    pools = [int(x) for x in args.db.split(',')] if args.db else [0]
    pin = not args.no_pin and shutil.which('taskset') is not None and os.cpu_count() >= 2
    import resource
    soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    need = args.idle + max(args.conns) + 1000
    if soft < need:
        resource.setrlimit(resource.RLIMIT_NOFILE, (min(hard, max(need, soft)), hard))
        soft = resource.getrlimit(resource.RLIMIT_NOFILE)[0]
        if soft < need:
            sys.exit('ulimit -n is %d; %d needed (hard limit %d)' % (soft, need, hard))
    tmp = tempfile.mkdtemp(prefix='obx-http-')
    try:
        exes = build(args, tmp)
        results = []
        for name, pool in [(n, q) for q in pools for n in args.servers]:
            key = {'native': 'httpd-native', 'c': 'httpd-c'}.get(name, name)
            if key not in exes:
                log('%s: not available, skipped' % name)
                continue
            if pool and name not in ('native', 'c'):
                log('%s: no Postgres driver installed, skipped' % name)
                continue
            label = 'obx ' + name if name in ('native', 'c') else name
            log('== %s: %d idle connections, load %s%s for %d s each%s' %
                (label, args.idle, args.conns, ' on /db, pool %d' % pool if pool else '', args.seconds,
                 ' (pinned)' if pin else ''))
            r = run_server(label, exes[key], args, exes['httpload'], pin, pool)
            results.append(r)
            if 'per_conn' in r:
                log('  RSS %d kB idle, %d kB with %d connections: %.0f bytes per idle connection (opened in %.1f s)' %
                    (r['rss0'], r['rss1'], args.idle, r['per_conn'], r['open_s']))
            for d in r.get('load', []):
                log('  %4d clients: %7d req/s, latency us p50 %d p90 %d p99 %d max %d' %
                    (d['conns'], d['rps'], d['p50'], d['p90'], d['p99'], d['max']))
            if 'rss2' in r:
                log('  RSS after the load: %d kB' % r['rss2'])
            if 'stop_s' in r:
                log('  shutdown with %d connections open: %.2f s' % (args.idle, r['stop_s']))
            for n in r['notes']:
                log('  ' + n)
        bad = [r['name'] for r in results if not r['ok'] and r['name'].startswith('obx')]
        log('FAIL: ' + ', '.join(bad) if bad else 'PASS')
        return 1 if bad else 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
