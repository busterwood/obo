"""Minimal raw HTTP/2 client used by the H2b/H2c server tests.

This is test support, not product code. It sends HPACK literal-without-indexing
request blocks and parses only the frame framing, DATA payloads, RST_STREAM and
GOAWAY. It does not decode response headers. Flow-control windows are updated as
DATA arrives so a large response can make progress.
"""
import select
import socket
import struct
import time

PREFACE = b'PRI * HTTP/2.0\r\n\r\nSM\r\n\r\n'

DATA = 0
HEADERS = 1
RST_STREAM = 3
SETTINGS = 4
PING = 6
GOAWAY = 7
WINDOW_UPDATE = 8
CONTINUATION = 9

FLAG_ACK = 0x1
FLAG_END_STREAM = 0x1
FLAG_END_HEADERS = 0x4


def frame(ftype, flags, stream_id, payload=b''):
    return (len(payload).to_bytes(3, 'big') + bytes([ftype, flags]) +
            struct.pack('!I', stream_id & 0x7fffffff) + payload)


def hpack_int(value, prefix_bits, first):
    limit = (1 << prefix_bits) - 1
    if value < limit:
        return bytes([first | value])
    out = [first | limit]
    value -= limit
    while value >= 128:
        out.append((value & 0x7f) | 0x80)
        value >>= 7
    out.append(value)
    return bytes(out)


def literal(name, value):
    if isinstance(name, str):
        name = name.encode('ascii')
    if isinstance(value, str):
        value = value.encode('latin-1')
    return (b'\x00' + hpack_int(len(name), 7, 0) + name +
            hpack_int(len(value), 7, 0) + value)


def request_block(method, path, authority, headers=()):
    out = bytearray()
    if method == 'GET':
        out.append(0x82)
    elif method == 'POST':
        out.append(0x83)
    else:
        out += literal(':method', method)
    out.append(0x86)  # :scheme http
    if path == '/':
        out.append(0x84)
    else:
        out += literal(':path', path)
    out += literal(':authority', authority)
    for name, value in headers:
        out += literal(name, value)
    return bytes(out)


class H2Client:
    def __init__(self, port=None, timeout=8.0, authority=None, sock=None,
                 auto_window=True):
        if sock is None:
            self.sock = socket.create_connection(('127.0.0.1', port),
                                                 timeout=timeout)
        else:
            self.sock = sock
        self.sock.settimeout(0.2)
        self.authority = authority or ('127.0.0.1:%d' % port)
        self.bodies = {}
        self.ended = set()
        self.reset = {}
        self.header_seen = set()
        self.goaway = None
        self.goaways = []
        self.frames = []
        self.closed = False
        self.auto_window = auto_window
        self.cw = 65535
        self.sw = {}
        self._deadline = None
        self.sock.sendall(PREFACE + frame(SETTINGS, 0, 0))
        self._wait_server_settings(timeout)

    def _read_exact(self, n):
        buf = b''
        while len(buf) < n:
            try:
                chunk = self.sock.recv(n - len(buf))
            except socket.timeout:
                if time.time() >= self._deadline:
                    raise
                continue
            if not chunk:
                self.closed = True
                return None
            buf += chunk
        return buf

    def _recv_frame(self):
        if self._deadline is None:
            self._deadline = time.time() + 8.0
        head = self._read_exact(9)
        if head is None:
            return None
        length = int.from_bytes(head[0:3], 'big')
        ftype = head[3]
        flags = head[4]
        sid = struct.unpack('!I', head[5:9])[0] & 0x7fffffff
        payload = b''
        if length:
            payload = self._read_exact(length)
            if payload is None:
                return None
        return ftype, flags, sid, payload

    def _handle(self, fr):
        ftype, flags, sid, payload = fr
        self.frames.append(ftype)
        if ftype == SETTINGS and not (flags & FLAG_ACK):
            self.send(frame(SETTINGS, FLAG_ACK, 0))
            return
        if ftype == PING and not (flags & FLAG_ACK):
            self.send(frame(PING, FLAG_ACK, 0, payload[:8]))
            return
        if ftype == DATA:
            if payload:
                self.bodies.setdefault(sid, bytearray()).extend(payload)
                if self.auto_window:
                    self.send(frame(WINDOW_UPDATE, 0, 0,
                                    struct.pack('!I', len(payload))))
                    if sid not in self.ended:
                        self.send(frame(WINDOW_UPDATE, 0, sid,
                                        struct.pack('!I', len(payload))))
            if flags & FLAG_END_STREAM:
                self.ended.add(sid)
            return
        if ftype == WINDOW_UPDATE and len(payload) >= 4:
            inc = struct.unpack('!I', payload[:4])[0] & 0x7fffffff
            if sid == 0:
                self.cw += inc
            else:
                self.sw[sid] = self.sw.get(sid, 0) + inc
            return
        if ftype == HEADERS:
            self.header_seen.add(sid)
            if flags & FLAG_END_STREAM:
                self.ended.add(sid)
            return
        if ftype == RST_STREAM:
            code = struct.unpack('!I', payload[:4])[0] if len(payload) >= 4 else 0
            self.reset[sid] = code
            self.ended.add(sid)
            return
        if ftype == GOAWAY:
            code = struct.unpack('!I', payload[4:8])[0] if len(payload) >= 8 else 0
            last = struct.unpack('!I', payload[0:4])[0] & 0x7fffffff if len(payload) >= 4 else 0
            self.goaway = (last, code)
            self.goaways.append((last, code))
            return

    def _wait_server_settings(self, timeout):
        deadline = time.time() + timeout
        self._deadline = deadline
        while time.time() < deadline:
            try:
                fr = self._recv_frame()
            except socket.timeout:
                continue
            if fr is None:
                raise RuntimeError('connection closed before SETTINGS')
            if fr[0] == SETTINGS and not (fr[1] & FLAG_ACK):
                self._handle(fr)
                return
            self._handle(fr)
        raise RuntimeError('server SETTINGS not received')

    def send(self, data):
        try:
            self.sock.sendall(data)
            return True
        except OSError:
            self.closed = True
            return False

    def request(self, sid, path, method='GET', body=None, headers=(),
                end_stream=True):
        block = request_block(method, path, self.authority, headers)
        flags = FLAG_END_HEADERS
        if end_stream and not body:
            flags |= FLAG_END_STREAM
        if not self.send(frame(HEADERS, flags, sid, block)):
            return False
        if body:
            off = 0
            while off < len(body):
                part = body[off:off + 8192]
                off += len(part)
                last = off >= len(body)
                f = FLAG_END_STREAM if (last and end_stream) else 0
                if not self.send(frame(DATA, f, sid, part)):
                    return False
        return True

    def grant(self, sid, increment):
        return self.send(frame(WINDOW_UPDATE, 0, sid,
                               struct.pack('!I', increment)))

    def post_flow_controlled(self, sid, body):
        self.sw[sid] = 32768
        if not self.send(frame(HEADERS, FLAG_END_HEADERS, sid,
                               request_block('POST', '/echo', self.authority))):
            return False
        off = 0
        deadline = time.time() + 10
        while off < len(body) and time.time() < deadline:
            while True:
                r, _, _ = select.select([self.sock], [], [], 0)
                if not r:
                    break
                self._deadline = time.time() + 10
                fr = self._recv_frame()
                if fr is None:
                    return False
                self._handle(fr)
            allow = min(self.sw.get(sid, 0), self.cw)
            if allow <= 0:
                select.select([self.sock], [], [], 0.02)
                continue
            n = min(4096, allow, len(body) - off)
            last = off + n >= len(body)
            f = FLAG_END_STREAM if last else 0
            if not self.send(frame(DATA, f, sid, body[off:off + n])):
                return False
            off += n
            self.sw[sid] -= n
            self.cw -= n
            time.sleep(0.001)
        return off == len(body)

    def reset_stream(self, sid, code=8):
        self.send(frame(RST_STREAM, 0, sid, struct.pack('!I', code)))

    def empty_data(self, sid, count, end_stream=False):
        for i in range(count):
            flags = FLAG_END_STREAM if (end_stream and i == count - 1) else 0
            if not self.send(frame(DATA, flags, sid)):
                return False
        return True

    def headers_without_end(self, sid, path, method='POST'):
        block = request_block(method, path, self.authority)
        return self.send(frame(HEADERS, FLAG_END_HEADERS, sid, block))

    def continuation_flood(self, sid, count, path='/hello'):
        block = request_block('GET', path, self.authority)
        if not self.send(frame(HEADERS, FLAG_END_STREAM, sid, block)):
            return False
        for _ in range(count):
            if not self.send(frame(CONTINUATION, 0, sid)):
                return False
        return True

    def stall_headers(self, sid, path='/echo', method='POST'):
        block = request_block(method, path, self.authority)
        return self.send(frame(HEADERS, 0, sid, block))

    def stalled_body(self, sid, path='/echo', payload=b'x'):
        block = request_block('POST', path, self.authority)
        if not self.send(frame(HEADERS, FLAG_END_HEADERS, sid, block)):
            return False
        return self.send(frame(DATA, 0, sid, payload))

    def header_flood(self, sid, count):
        headers = [('x-flood-%d' % i, 'v' * 96) for i in range(count)]
        return self.request(sid, '/hello', headers=headers)

    def collect(self, ids, timeout=10.0):
        ids = set(ids)
        deadline = time.time() + timeout
        self._deadline = deadline
        while time.time() < deadline:
            if ids and ids <= self.ended:
                break
            if self.closed:
                break
            try:
                fr = self._recv_frame()
            except socket.timeout:
                continue
            if fr is None:
                break
            self._handle(fr)
        return self

    def drain_for(self, seconds):
        deadline = time.time() + seconds
        self._deadline = deadline
        while time.time() < deadline and not self.closed:
            try:
                fr = self._recv_frame()
            except socket.timeout:
                continue
            if fr is None:
                break
            self._handle(fr)
        return self

    def body(self, sid):
        return bytes(self.bodies.get(sid, b''))

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass
