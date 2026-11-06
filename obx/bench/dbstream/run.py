#!/usr/bin/env python3
"""Streaming against buffered reads, per database client and backend
(design/db-streaming.md, section 7).

    python3 obx/bench/dbstream/run.py [--cpu 10] [--runs 3] [--rows 1000000] [--small 100000]
    python3 obx/bench/dbstream/run.py --latency 0,0.5,2.5 [--cpu 10] [--runs 3] ...

Builds dbstream.obx via C (obxc, gcc) and natively (obxc --native), then runs
every client (mssql, pg, oracle), mode (stream, buffered) and backend at
--rows rows, and streaming also at --small rows (peak RSS should not grow
with the rows). Each configuration runs --runs times, interleaved, pinned
to --cpu (taskset); the least time is kept, and its peak RSS (VmHWM). A
client without its DSN (see dbstream.obx) is skipped. The database servers
run on the same machine: check that it is quiet first (uptime).

--latency (Oracle only, design/oracle.md): for each one-way delay in ms (0:
direct), a delayproxy.py in front of the server's port (on --proxy-cpus),
and buffered and streamed reads of --small and --rows rows with the old
fetching (Fetch Size 100, no fetching ahead) and the new (rows by bytes,
fetching ahead; --fetches may add noahead: rows by bytes without it; --modes
and --small 0 narrow the set).
Reports the measured round trip (500 SELECT 1 through the
proxy), the query's round trips, rows per round trip, the query's time
(without the login) and peak RSS.
"""
import argparse, os, subprocess, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
OBXC = os.environ.get('OBXC') or os.path.normpath(os.path.join(HERE, '..', '..', 'compiler', 'out', 'obxc'))


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


def proxy(port, delay, cpus):
    p = subprocess.Popen(['taskset', '-c', cpus, sys.executable, '-I', os.path.join(HERE, 'delayproxy.py'), str(port),
                          '127.0.0.1:1521', str(delay)], stdout=subprocess.PIPE, text=True)
    if p.stdout.readline().strip() != 'ready':
        p.kill()
        raise SystemExit('delayproxy did not start')
    return p


def latency(a, exes):
    delays = [float(x) for x in a.latency.split(',')]
    procs, ports = [], {}
    try:
        for i, d in enumerate(delays):
            if d > 0:
                ports[d] = 15210 + i
                procs.append(proxy(ports[d], d, a.proxy_cpus))
            else:
                ports[d] = 0
        rtt = {}
        for d in delays:
            r = subprocess.run(['taskset', '-c', str(a.cpu), exes['C'], 'oracle', 'ping', '500', '1', 'new', str(ports[d])],
                               capture_output=True, text=True)
            parts = r.stdout.split()
            rtt[d] = int(parts[3]) if r.returncode == 0 and len(parts) == 4 else -1
        configs = []
        for d in delays:
            for backend in ('C', 'native'):
                for mode in a.modes.split(','):
                    for rows in ([a.small] if a.small > 0 else []) + [a.rows]:
                        for fetch in a.fetches.split(','):
                            configs.append((d, backend, mode, rows, fetch))
        best = {}
        for i in range(a.runs):
            for c in configs:
                d, backend, mode, rows, fetch = c
                r = run(exes[backend], a.cpu, 'oracle', mode, rows, a.buffer, fetch, ports[d])
                if r is None:
                    continue
                if c not in best or r['qms'] < best[c]['qms']:
                    best[c] = r
        print('round trips measured (SELECT 1, mean of 500): ' +
              ', '.join('%s ms one way: %.2f ms' % (d, rtt[d] / 1000) for d in delays))
        print('| one-way delay | round trip | backend | mode | rows | fetching | round trips | rows/trip | query ms | peak RSS MB | checksum |')
        print('|---:|---:|---|---|---:|---|---:|---:|---:|---:|---|')
        for c in configs:
            d, backend, mode, rows, fetch = c
            if c not in best:
                print('| %s | | %s | %s | %d | %s | failed | | | | |' % (d, backend, mode, rows, fetch))
                continue
            r = best[c]
            per = r['rows'] / max(r['trips'], 1)
            print('| %s ms | %.2f ms | %s | %s | %s | %s | %s | %s | %s | %.1f | %s |' % (
                d, rtt[d] / 1000, backend, mode, f"{rows:,}", fetch, f"{r['trips']:,}", f"{per:,.0f}", f"{r['qms']:,}",
                r['kb'] / 1024, r['sum']))
    finally:
        for p in procs:
            p.kill()
            p.wait()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--cpu', type=int, default=10)
    ap.add_argument('--runs', type=int, default=3)
    ap.add_argument('--rows', type=int, default=1000000)
    ap.add_argument('--small', type=int, default=100000)
    ap.add_argument('--buffer', type=int, default=1024)
    ap.add_argument('--clients', default='mssql,pg,oracle')
    ap.add_argument('--latency', default='')
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
