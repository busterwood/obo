#!/usr/bin/env python3
"""A TCP proxy that adds latency, for measuring database clients over a
slow network without root (tc/netem needs it).

    python3 -I obx/bench/dbstream/delayproxy.py LISTEN_PORT TARGET_HOST:TARGET_PORT DELAY_MS

Listens on 127.0.0.1:LISTEN_PORT and forwards each connection to the
target. Every chunk read in either direction is written DELAY_MS
milliseconds after it was read (0.5 for a 1 ms round trip), in order;
chunks in flight overlap, so the delay is latency, not a bandwidth cap.
One thread per direction, waiting in select for the source or the next due
chunk (asyncio's timers round to whole milliseconds).
Prints "ready" once listening; runs until killed.
"""
import collections, select, socket, sys, threading, time

SPIN = 0.0006


def pump(src, dst, delay):
    # One thread a direction: select waits for the source or for the next
    # chunk's due time, whichever comes first (no hand-over between threads).
    q = collections.deque()
    eof = False
    while True:
        now = time.monotonic()
        while q and q[0][0] <= now:
            _, data = q.popleft()
            if data is None:
                try:
                    dst.shutdown(socket.SHUT_WR)
                except OSError:
                    pass
                return
            try:
                dst.sendall(data)
            except OSError:
                return
        if eof:
            if q:
                time.sleep(max(0.0, q[0][0] - time.monotonic()))
            continue
        # A wake-up from select comes up to ~0.4 ms late on this machine:
        # wait to SPIN before the due time, then poll until it.
        timeout = max(0.0, q[0][0] - now - SPIN) if q else None
        r, _, _ = select.select([src], [], [], timeout)
        if r:
            try:
                data = src.recv(262144)
            except OSError:
                data = b''
            q.append((time.monotonic() + delay, data or None))
            if not data:
                eof = True


def serve(conn, target, delay):
    try:
        up = socket.create_connection(target)
    except OSError:
        conn.close()
        return
    for s in (conn, up):
        s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    a = threading.Thread(target=pump, args=(conn, up, delay), daemon=True)
    b = threading.Thread(target=pump, args=(up, conn, delay), daemon=True)
    a.start()
    b.start()
    a.join()
    b.join()
    conn.close()
    up.close()


def main():
    if len(sys.argv) != 4:
        print(__doc__.strip(), file=sys.stderr)
        return 2
    port = int(sys.argv[1])
    host, tport = sys.argv[2].rsplit(':', 1)
    delay = float(sys.argv[3]) / 1000.0
    sys.setswitchinterval(0.00005)          # hand the GIL over within 50 us, not 5 ms
    ls = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    ls.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    ls.bind(('127.0.0.1', port))
    ls.listen(16)
    print('ready', flush=True)
    while True:
        conn, _ = ls.accept()
        threading.Thread(target=serve, args=(conn, (host, int(tport)), delay), daemon=True).start()


if __name__ == '__main__':
    sys.exit(main())
