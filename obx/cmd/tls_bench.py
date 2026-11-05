#!/usr/bin/env python3
"""TLS and https measurements (PERFORMANCE.md, "TLS"): obx's tls and http
against openssl s_time and curl, on the same local servers.

usage: tls_bench.py [--time S] [--mib N] [--requests N]

Makes a CA and a localhost certificate (ECDSA P-256) with the openssl
command in a temporary directory, builds cmd/tlsbench.obx natively and via
C, and measures, the server pinned to CPU 0 and the client to CPU 2:
  handshakes/s   one connection after another to `openssl s_server`: tlsbench hs
                 against `openssl s_time -new` (full handshakes, no resumption)
  throughput     one GET of --mib MiB from `tlsbench serve` (native): tlsbench get
                 against curl; MB/s and the client's CPU seconds
  requests/s     --requests GETs over kept-alive connections to `tlsbench serve`:
                 tlsbench reqs with 1 and 10 connections, curl with 1
Each row is the median of three runs.
"""
import argparse, os, re, resource, shutil, statistics, subprocess, sys, tempfile, time

HERE = os.path.dirname(os.path.abspath(__file__))
OBX = os.path.normpath(os.path.join(HERE, '..'))
OBXC = os.path.join(OBX, 'compiler', 'out', 'obxc')
SERVER_CPU = '0'
CLIENT_CPU = '2'


def run(args, cwd=None):
    subprocess.run(args, cwd=cwd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def pki(d):
    open(os.path.join(d, 'san.ext'), 'w').write('subjectAltName=DNS:localhost,IP:127.0.0.1\n')
    ec = ['-newkey', 'ec', '-pkeyopt', 'ec_paramgen_curve:P-256', '-nodes']
    run(['openssl', 'req', '-x509'] + ec + ['-keyout', 'ca.key', '-out', 'ca.pem', '-days', '2', '-subj', '/CN=bench ca'], d)
    run(['openssl', 'req'] + ec + ['-keyout', 'srv.key', '-out', 'srv.csr', '-subj', '/CN=localhost'], d)
    run(['openssl', 'x509', '-req', '-in', 'srv.csr', '-CA', 'ca.pem', '-CAkey', 'ca.key', '-days', '2',
         '-extfile', 'san.ext', '-out', 'srv.pem'], d)


def pinned(cpu, args):
    return (['taskset', '-c', cpu] if shutil.which('taskset') else []) + args


def start(args, cwd, pattern):
    """Starts a server (its output to a file, read for the port it names);
    the process and the port."""
    log = os.path.join(cwd, 'server-%d.out' % time.monotonic_ns())
    with open(log, 'w') as f:
        p = subprocess.Popen(pinned(SERVER_CPU, args), cwd=cwd, stdin=subprocess.DEVNULL, stdout=f,
                             stderr=subprocess.STDOUT)
    for _ in range(500):
        m = re.search(pattern, open(log).read())
        if m:
            return p, int(m.group(1))
        time.sleep(0.01)
    p.kill()
    raise SystemExit('server did not start: ' + ' '.join(args))


def stop(p):
    p.kill()
    p.wait()


def timed(args, cwd, check=True):
    """Runs a client: its output, wall seconds and CPU seconds (user + system)."""
    before = resource.getrusage(resource.RUSAGE_CHILDREN)
    t0 = time.monotonic()
    r = subprocess.run(pinned(CLIENT_CPU, args), cwd=cwd, capture_output=True, text=True)
    if check and r.returncode != 0:
        raise SystemExit('%s: exit %d\n%s' % (' '.join(args), r.returncode, r.stderr[-2000:]))
    out = r.stdout
    wall = time.monotonic() - t0
    after = resource.getrusage(resource.RUSAGE_CHILDREN)
    cpu = (after.ru_utime - before.ru_utime) + (after.ru_stime - before.ru_stime)
    return out, wall, cpu


def median3(f):
    return statistics.median([f() for _ in range(3)])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--time', type=int, default=3, help='seconds per handshake run')
    ap.add_argument('--mib', type=int, default=1024)
    ap.add_argument('--requests', type=int, default=20000)
    a = ap.parse_args()
    for tool in ['openssl', 'curl']:
        if not shutil.which(tool):
            raise SystemExit(tool + ' is needed')
    d = tempfile.mkdtemp(prefix='tlsbench-')
    servers = []
    try:
        pki(d)
        bins = {}
        for name, extra in [('native', ['--native']), ('via C', [])]:
            b = os.path.join(d, 'tlsbench-' + name.replace(' ', ''))
            subprocess.run([OBXC, os.path.join(HERE, 'tlsbench.obx'), '-o', b] + extra, check=True)
            bins[name] = b
        print(subprocess.run(['openssl', 'version'], capture_output=True, text=True).stdout.strip())

        # handshakes against s_server
        srv, port = start(['openssl', 's_server', '-accept', '127.0.0.1:0', '-cert', 'srv.pem', '-key', 'srv.key',
                           '-www', '-naccept', '1000000'], d, r'ACCEPT 127\.0\.0\.1:(\d+)')
        servers.append(srv)
        addr = '127.0.0.1:%d' % port
        n = 500 * a.time
        for name, b in bins.items():
            rows = []
            for _ in range(3):
                out, wall, cpu = timed([b, 'hs', addr, 'ca.pem', str(n)], d)
                rows.append((n / wall, cpu / n * 1e6))
            rows.sort()
            print('handshakes/s, obx %s: %.0f, client CPU %.0f us each' % (name, rows[1][0], rows[1][1]))

        rows = []
        for _ in range(3):
            out, wall, cpu = timed(['openssl', 's_time', '-connect', addr, '-new', '-time', str(a.time),
                                    '-CAfile', 'ca.pem'], d, check=False)    # its exit status is 1 even when it worked
            m = re.search(r'(\d+) connections in (\d+) real seconds', out)
            if not m:
                raise SystemExit('s_time: ' + out[-2000:])
            k = int(m.group(1))
            rows.append((k / int(m.group(2)), cpu / k * 1e6))
        rows.sort()
        print('handshakes/s, openssl s_time: %.0f, client CPU %.0f us each' % rows[1])
        stop(srv)

        # throughput and requests against tlsbench serve (native)
        srv, port = start([bins['native'], 'serve', '127.0.0.1:0', 'srv.pem', 'srv.key'], d, r'listening (\d+)')
        servers.append(srv)
        base = 'https://localhost:%d' % port
        size = a.mib << 20
        big = '%s/big?n=%d' % (base, size)
        for name, b in bins.items():
            rows = []
            for _ in range(3):
                out, wall, cpu = timed([b, 'get', big, 'ca.pem'], d)
                rows.append((size / wall / 1e6, cpu))
            rows.sort()
            print('GET %d MiB, obx %s: %.0f MB/s, client CPU %.2f s' % (a.mib, name, rows[1][0], rows[1][1]))
        rows = []
        for _ in range(3):
            out, wall, cpu = timed(['curl', '-s', '--cacert', 'ca.pem', '-o', '/dev/null', big], d)
            rows.append((size / wall / 1e6, cpu))
        rows.sort()
        print('GET %d MiB, curl: %.0f MB/s, client CPU %.2f s' % (a.mib, rows[1][0], rows[1][1]))

        for name, b in bins.items():
            for conns in [1, 10]:
                def reqs():
                    out, wall, cpu = timed([b, 'reqs', base + '/', 'ca.pem', str(a.requests), str(conns)], d)
                    return a.requests / wall
                print('requests/s, %d connection(s), obx %s: %.0f' % (conns, name, median3(reqs)))
        k = os.path.join(d, 'curl.cfg')
        cn = min(a.requests, 5000)
        with open(k, 'w') as f:
            for _ in range(cn):
                f.write('url = "%s/"\noutput = "/dev/null"\n' % base)

        def curl():
            out, wall, cpu = timed(['curl', '-s', '--cacert', 'ca.pem', '-K', k], d)
            return cn / wall
        print('requests/s, 1 connection, curl: %.0f' % median3(curl))
        stop(srv)
    finally:
        for p in servers:
            if p.poll() is None:
                p.kill()
                p.wait()
        shutil.rmtree(d)


if __name__ == '__main__':
    main()
