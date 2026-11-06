#!/usr/bin/env python3
"""Streaming against buffered reads, per database client and backend
(design/db-streaming.md, section 7).

    python3 obx/bench/dbstream/run.py [--cpu 10] [--runs 5] [--rows 1000000] [--small 100000]
    python3 obx/bench/dbstream/run.py --latency 0,0.5,2.5 [--latency-clients oracle,pg,pglibpq] [--cpu 10] ...

Builds dbstream.obx via C (obxc, gcc) and natively (obxc --native), then runs
every client (mssql, pg, pglibpq, oracle), mode (stream, buffered) and backend
at --rows rows, and streaming also at --small rows (peak RSS should not grow
with the rows). Each configuration runs --runs times, interleaved, pinned
to --cpu (taskset); the least time is kept, and its peak RSS (VmHWM). A
client without its DSN (see dbstream.obx) is skipped. The database servers
run on the same machine: check that it is quiet first (uptime).

--latency (design/oracle.md for oracle, design/db-streaming.md for pg):
for each one-way delay in ms (0: direct), a delayproxy.py in front of the
server's TCP port (on --proxy-cpus), and buffered and streamed reads of
--small and --rows rows. oracle: the old fetching (Fetch Size 100, no
fetching ahead) and the new (rows by bytes, fetching ahead; --fetches may
add noahead). pg and pglibpq: the TCP DSN with TLS (OBX_PG_TLS_DSN, or
~/.obo-pg-tls-dsn; the file is read here for its host and port and never
printed; skipped without it). --modes and --small 0 narrow the set.
Reports the measured round trip (500 SELECT 1 through the proxy), the
query's round trips (not for pglibpq), rows per round trip, the query's time
(without the login) and peak RSS.
"""
import argparse, os, re, subprocess, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
OBXC = os.environ.get('OBXC') or os.path.normpath(os.path.join(HERE, '..', '..', 'compiler', 'out', 'obxc'))
ORACLE_TARGET = '127.0.0.1:1521'


def build(tmp):
    src = os.path.join(HERE, 'dbstream.obx')
    out = {}
    for backend, extra in (('C', []), ('native', ['--native'])):
        exe = os.path.join(tmp, 'dbstream_' + backend)
        subprocess.run([OBXC, src, '-o', exe] + extra, check=True)
        out[backend] = exe
    return out


def run(exe, cpu, client, mode, rows, size, fetch=None, port=None):
    args = ['taskset', '-c', str(cpu), exe, client, mode, str(rows), str(size)]
    if fetch is not None:
        args += [fetch, str(port or 0)]
    r = subprocess.run(args, capture_output=True, text=True)
    parts = r.stdout.split()
    if r.returncode != 0 or len(parts) < 6:
        return None
    out = {'rows': int(parts[2]), 'ms': int(parts[3]), 'kb': int(parts[4]), 'sum': parts[5]}
    if len(parts) >= 8:
        out['trips'] = int(parts[6])
        out['qms'] = int(parts[7])
    return out


def proxy(port, delay, cpus, target):
    p = subprocess.Popen(['taskset', '-c', cpus, sys.executable, '-I', os.path.join(HERE, 'delayproxy.py'), str(port),
                          target, str(delay)], stdout=subprocess.PIPE, text=True)
    if p.stdout.readline().strip() != 'ready':
        p.kill()
        raise SystemExit('delayproxy did not start')
    return p


def tls_dsn():
    """The TCP DSN with TLS: OBX_PG_TLS_DSN, else ~/.obo-pg-tls-dsn. Read for
    its host and port only; its contents are never printed."""
    text = os.environ.get('OBX_PG_TLS_DSN', '').strip()
    if not text:
        try:
            with open(os.path.join(os.environ.get('HOME', ''), '.obo-pg-tls-dsn')) as f:
                text = f.read().strip()
        except OSError:
            return None
    host = port = None
    m = re.search(r'(?:^|\s)host=(\S+)', text)
    if m:
        host = m.group(1)
    m = re.search(r'(?:^|\s)port=(\d+)', text)
    if m:
        port = int(m.group(1))
    if host is None:
        m = re.match(r'postgres(?:ql)?://(?:[^@/]*@)?([^:/?]+)(?::(\d+))?', text)
        if m:
            host = m.group(1)
            port = int(m.group(2)) if m.group(2) else port
    if host is None or host.startswith('/') or '%2F' in host:
        return None
    if port is None:
        port = 5432
    return ('127.0.0.1' if host == 'localhost' else host), port


def endpoint(client):
    """(proxy target, the server's own port) for a latency client; None when
    the client has no TCP endpoint here (pg without its TLS DSN)."""
    if client == 'oracle':
        return ORACLE_TARGET, 0
    ep = tls_dsn()
    if ep is None:
        return None
    return '%s:%d' % ep, ep[1]


def latency(a, exes):
    delays = [float(x) for x in a.latency.split(',')]
    procs = []
    try:
        for k, client in enumerate(a.latency_clients.split(',')):
            ep = endpoint(client)
            if ep is None:
                print('%s: skipped (no TCP DSN with TLS: OBX_PG_TLS_DSN or ~/.obo-pg-tls-dsn)' % client)
                continue
            target, direct = ep
            ports = {}
            for i, d in enumerate(delays):
                if d > 0:
                    ports[d] = 15210 + 10 * k + i
                    procs.append(proxy(ports[d], d, a.proxy_cpus, target))
                else:
                    ports[d] = direct
            rtt = {}
            for d in delays:
                r = subprocess.run(['taskset', '-c', str(a.cpu), exes['C'], client, 'ping', '500', '1', 'new', str(ports[d])],
                                   capture_output=True, text=True)
                parts = r.stdout.split()
                rtt[d] = int(parts[3]) if r.returncode == 0 and len(parts) == 4 else -1
            fetches = a.fetches.split(',') if client == 'oracle' else ['-']
            configs = []
            for d in delays:
                for backend in ('C', 'native'):
                    for mode in a.modes.split(','):
                        for rows in ([a.small] if a.small > 0 else []) + [a.rows]:
                            for fetch in fetches:
                                configs.append((d, backend, mode, rows, fetch))
            best = {}
            for i in range(a.runs):
                for c in configs:
                    d, backend, mode, rows, fetch = c
                    r = run(exes[backend], a.cpu, client, mode, rows, a.buffer,
                            'new' if fetch == '-' else fetch, ports[d])
                    if r is None:
                        continue
                    if c not in best or r['qms'] < best[c]['qms']:
                        best[c] = r
            print('client %s: round trips measured (SELECT 1, mean of 500): %s' % (client, ', '.join(
                '%s ms one way: %.2f ms' % (d, rtt[d] / 1000) for d in delays)))
            print('| one-way delay | round trip | backend | mode | rows | fetching | round trips | rows/trip | query ms | peak RSS MB | checksum |')
            print('|---:|---:|---|---|---:|---|---:|---:|---:|---:|---|')
            for c in configs:
                d, backend, mode, rows, fetch = c
                if c not in best:
                    print('| %s | | %s | %s | %d | %s | failed | | | | |' % (d, backend, mode, rows, fetch))
                    continue
                r = best[c]
                trips = r['trips']
                if trips < 0:
                    trips_s, per_s = 'n/a', 'n/a'
                else:
                    trips_s, per_s = f"{trips:,}", f"{r['rows'] / max(trips, 1):,.0f}"
                print('| %s ms | %.2f ms | %s | %s | %s | %s | %s | %s | %s | %.1f | %s |' % (
                    d, rtt[d] / 1000, backend, mode, f"{rows:,}", fetch, trips_s, per_s, f"{r['qms']:,}",
                    r['kb'] / 1024, r['sum']))
    finally:
        for p in procs:
            p.kill()
            p.wait()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--cpu', type=int, default=10)
    ap.add_argument('--runs', type=int, default=5)
    ap.add_argument('--rows', type=int, default=1000000)
    ap.add_argument('--small', type=int, default=100000)
    ap.add_argument('--buffer', type=int, default=1024)
    ap.add_argument('--clients', default='mssql,pg,pglibpq,oracle')
    ap.add_argument('--latency', default='')
    ap.add_argument('--latency-clients', default='oracle')
    ap.add_argument('--proxy-cpus', default='4-8')
    ap.add_argument('--modes', default='buffered,stream')
    ap.add_argument('--fetches', default='old,new')
    a = ap.parse_args()
    print(subprocess.run(['uptime'], capture_output=True, text=True).stdout.strip())
    with tempfile.TemporaryDirectory() as tmp:
        exes = build(tmp)
        if a.latency:
            latency(a, exes)
            print(subprocess.run(['uptime'], capture_output=True, text=True).stdout.strip())
            return 0
        configs = []
        for client in a.clients.split(','):
            for backend in ('C', 'native'):
                configs.append((client, backend, 'stream', a.small))
                configs.append((client, backend, 'stream', a.rows))
                configs.append((client, backend, 'buffered', a.rows))
        best = {}
        for i in range(a.runs):
            for c in configs:
                client, backend, mode, rows = c
                r = run(exes[backend], a.cpu, client, mode, rows, a.buffer)
                if r is None:
                    continue
                if c not in best or r['ms'] < best[c]['ms']:
                    best[c] = r
        print('| client | backend | mode | rows | ms | peak RSS MB | checksum |')
        print('|---|---|---|---:|---:|---:|---|')
        for c in configs:
            if c not in best:
                print('| %s | %s | %s | %d | skipped | | |' % c)
                continue
            r = best[c]
            print('| %s | %s | %s | %s | %s | %.1f | %s |' % (c[0], c[1], c[2], f"{r['rows']:,}", f"{r['ms']:,}", r['kb'] / 1024, r['sum']))
    return 0


if __name__ == '__main__':
    sys.exit(main())
