#!/usr/bin/env python3
"""Streaming against buffered reads, per database client and backend
(design/db-streaming.md, section 7).

    python3 obx/bench/dbstream/run.py [--cpu 10] [--runs 3] [--rows 1000000] [--small 100000]

Builds dbstream.obx via C (obxc, gcc) and natively (obxc --native), then runs
every client (mssql, pg, oracle), mode (stream, buffered) and backend at
--rows rows, and streaming also at --small rows (peak RSS should not grow
with the rows). Each configuration runs --runs times, interleaved, pinned
to --cpu (taskset); the least time is kept, and its peak RSS (VmHWM). A
client without its DSN (see dbstream.obx) is skipped. The database servers
run on the same machine: check that it is quiet first (uptime).
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


def run(exe, cpu, client, mode, rows, size):
    r = subprocess.run(['taskset', '-c', str(cpu), exe, client, mode, str(rows), str(size)], capture_output=True, text=True)
    parts = r.stdout.split()
    if r.returncode != 0 or len(parts) != 6:
        return None
    return {'rows': int(parts[2]), 'ms': int(parts[3]), 'kb': int(parts[4]), 'sum': parts[5]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--cpu', type=int, default=10)
    ap.add_argument('--runs', type=int, default=3)
    ap.add_argument('--rows', type=int, default=1000000)
    ap.add_argument('--small', type=int, default=100000)
    ap.add_argument('--buffer', type=int, default=1024)
    ap.add_argument('--clients', default='mssql,pg,oracle')
    a = ap.parse_args()
    print(subprocess.run(['uptime'], capture_output=True, text=True).stdout.strip())
    with tempfile.TemporaryDirectory() as tmp:
        exes = build(tmp)
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
