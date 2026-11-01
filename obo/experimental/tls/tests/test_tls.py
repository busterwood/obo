#!/usr/bin/env python3
"""P2T-b hosted TLS tests.

    python3 obo/experimental/tls/tests/test_tls.py

Locates matching OpenSSL 3.0 headers (installed or privately fetched exactly as
the P2T-a cinterop qualification does), builds the C harness and the obo echo
fixture through cinterop, generates local test CAs/certificates, and exercises
TLS 1.2/1.3, ALPN, SNI, client certificates, truncation, KeyUpdate, large
records, fragmentation, output-first steps, leak counters, ASAN/UBSAN,
openssl s_client and optionally curl. No external network is used.
"""
import importlib.util
import json
import os
import shutil
import socket
import ssl
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.dirname(HERE)
REPO = os.path.dirname(os.path.dirname(os.path.dirname(PKG)))
OBO = os.path.abspath(os.environ.get('OBO', os.path.join(REPO, 'obo', 'obo')))
TOOLPY = os.path.join(REPO, 'obo', 'experimental', 'cinterop', 'cinterop.py')
CINTEROP_TESTS = os.path.join(REPO, 'obo', 'experimental', 'cinterop', 'tests',
                              'test_cinterop.py')
NATIVE = os.path.join(PKG, 'native')
SYS_NATIVE = os.path.join(REPO, 'obo', 'experimental', 'sys', 'native')
APP = os.path.join(HERE, 'apps', 'tls_echo_app.obx')
HARNESS = os.path.join(HERE, 'c', 'tls_harness.c')
TMPROOT = '/tmp/opencode'


class Fail(Exception):
    pass


def run(cmd, **kw):
    timeout = kw.pop('timeout', 300)
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, **kw)


def must(cmd, label, **kw):
    p = run(cmd, **kw)
    if p.returncode != 0:
        raise Fail("%s failed:\n%s\n%s\n%s" % (label, ' '.join(cmd),
                                               (p.stdout or '').strip(),
                                               (p.stderr or '').strip()))
    return p


def load_cinterop_tests():
    spec = importlib.util.spec_from_file_location('tls_cinterop_tests', CINTEROP_TESTS)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def openssl_cmd(*args, **kw):
    return run(['openssl', *args], **kw)


def gen_certs(d):
    def sh(*args, **kw):
        must(['openssl', *args], 'openssl ' + ' '.join(args), **kw)

    os.makedirs(d, exist_ok=True)
    sh('req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-keyout',
       os.path.join(d, 'ca.key'), '-out', os.path.join(d, 'ca.pem'),
       '-days', '3650', '-subj', '/CN=obo TLS test CA')
    san = os.path.join(d, 'san.cnf')
    with open(san, 'w') as f:
        f.write('[req]\ndistinguished_name=dn\nreq_extensions=ext\nprompt=no\n'
                '[dn]\nCN=localhost\n[ext]\n'
                'subjectAltName=DNS:localhost,IP:127.0.0.1\n'
                'basicConstraints=CA:FALSE\nkeyUsage=digitalSignature,keyEncipherment\n'
                'extendedKeyUsage=serverAuth\n')
    sh('req', '-newkey', 'rsa:2048', '-nodes', '-keyout',
       os.path.join(d, 'server.key'), '-out', os.path.join(d, 'server.csr'),
       '-config', san)
    sh('x509', '-req', '-in', os.path.join(d, 'server.csr'), '-CA',
       os.path.join(d, 'ca.pem'), '-CAkey', os.path.join(d, 'ca.key'),
       '-CAcreateserial', '-out', os.path.join(d, 'server.pem'),
       '-days', '3650', '-extfile', san, '-extensions', 'ext')
    client_cnf = os.path.join(d, 'client.cnf')
    with open(client_cnf, 'w') as f:
        f.write('[req]\ndistinguished_name=dn\nreq_extensions=ext\nprompt=no\n'
                '[dn]\nCN=obo test client\n[ext]\n'
                'basicConstraints=CA:FALSE\nkeyUsage=digitalSignature,keyEncipherment\n'
                'extendedKeyUsage=clientAuth\n')
    sh('req', '-newkey', 'rsa:2048', '-nodes', '-keyout',
       os.path.join(d, 'client.key'), '-out', os.path.join(d, 'client.csr'),
       '-config', client_cnf)
    sh('x509', '-req', '-in', os.path.join(d, 'client.csr'), '-CA',
       os.path.join(d, 'ca.pem'), '-CAkey', os.path.join(d, 'ca.key'),
       '-CAcreateserial', '-out', os.path.join(d, 'client.pem'),
       '-days', '3650', '-extfile', client_cnf, '-extensions', 'ext')
    sh('req', '-x509', '-newkey', 'rsa:1024', '-nodes', '-keyout',
       os.path.join(d, 'weak.key'), '-out', os.path.join(d, 'weak.pem'),
       '-days', '3650', '-subj', '/CN=weak')
    sh('pkcs8', '-topk8', '-in', os.path.join(d, 'server.key'), '-out',
       os.path.join(d, 'encrypted.key'), '-passout', 'pass:secret')
    with open(os.path.join(d, 'malformed.pem'), 'w') as f:
        f.write('-----BEGIN CERTIFICATE-----\nnot base64\n-----END CERTIFICATE-----\n')
    sh('req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-keyout',
       os.path.join(d, 'untrusted-ca.key'), '-out',
       os.path.join(d, 'untrusted-ca.pem'), '-days', '3650',
       '-subj', '/CN=untrusted CA')
    ucnf = os.path.join(d, 'untrusted-client.cnf')
    with open(ucnf, 'w') as f:
        f.write('[req]\ndistinguished_name=dn\nreq_extensions=ext\nprompt=no\n'
                '[dn]\nCN=untrusted client\n[ext]\n'
                'basicConstraints=CA:FALSE\nkeyUsage=digitalSignature,keyEncipherment\n'
                'extendedKeyUsage=clientAuth\n')
    sh('req', '-newkey', 'rsa:2048', '-nodes', '-keyout',
       os.path.join(d, 'untrusted-client.key'), '-out',
       os.path.join(d, 'untrusted-client.csr'), '-config', ucnf)
    sh('x509', '-req', '-in', os.path.join(d, 'untrusted-client.csr'), '-CA',
       os.path.join(d, 'untrusted-ca.pem'), '-CAkey',
       os.path.join(d, 'untrusted-ca.key'), '-CAcreateserial', '-out',
       os.path.join(d, 'untrusted-client.pem'), '-days', '3650',
       '-extfile', ucnf, '-extensions', 'ext')
    # A root -> intermediate -> leaf server chain.
    sh('req', '-newkey', 'rsa:2048', '-nodes', '-keyout',
       os.path.join(d, 'intermediate.key'), '-out',
       os.path.join(d, 'intermediate.csr'), '-subj', '/CN=obo intermediate CA')
    int_cnf = os.path.join(d, 'intermediate.cnf')
    with open(int_cnf, 'w') as f:
        f.write('[v3_ca]\nbasicConstraints=critical,CA:TRUE\n'
                'keyUsage=critical,keyCertSign,cRLSign\n'
                'subjectKeyIdentifier=hash\n')
    sh('x509', '-req', '-in', os.path.join(d, 'intermediate.csr'), '-CA',
       os.path.join(d, 'ca.pem'), '-CAkey', os.path.join(d, 'ca.key'),
       '-CAcreateserial', '-out', os.path.join(d, 'intermediate.pem'),
       '-days', '3650', '-extfile', int_cnf, '-extensions', 'v3_ca')
    int_san = os.path.join(d, 'int-san.cnf')
    with open(int_san, 'w') as f:
        f.write('[req]\ndistinguished_name=dn\nreq_extensions=ext\nprompt=no\n'
                '[dn]\nCN=localhost\n[ext]\n'
                'subjectAltName=DNS:localhost,IP:127.0.0.1\n'
                'basicConstraints=CA:FALSE\nkeyUsage=digitalSignature,keyEncipherment\n'
                'extendedKeyUsage=serverAuth\n')
    sh('req', '-newkey', 'rsa:2048', '-nodes', '-keyout',
       os.path.join(d, 'server-int.key'), '-out',
       os.path.join(d, 'server-int.csr'), '-config', int_san)
    sh('x509', '-req', '-in', os.path.join(d, 'server-int.csr'), '-CA',
       os.path.join(d, 'intermediate.pem'), '-CAkey',
       os.path.join(d, 'intermediate.key'), '-CAcreateserial', '-out',
       os.path.join(d, 'server-int-leaf.pem'), '-days', '3650',
       '-extfile', int_san, '-extensions', 'ext')
    with open(os.path.join(d, 'server-int.pem'), 'wb') as out:
        out.write(open(os.path.join(d, 'server-int-leaf.pem'), 'rb').read())
        out.write(open(os.path.join(d, 'intermediate.pem'), 'rb').read())

    # An expired client cert signed by the test CA.
    ca_dir = os.path.join(d, 'demoCA')
    os.makedirs(os.path.join(ca_dir, 'newcerts'), exist_ok=True)
    open(os.path.join(ca_dir, 'index.txt'), 'w').close()
    with open(os.path.join(ca_dir, 'serial'), 'w') as f:
        f.write('1000\n')
    ca_cnf = os.path.join(ca_dir, 'ca.cnf')
    with open(ca_cnf, 'w') as f:
        f.write('''[ ca ]
default_ca = CA_default
[ CA_default ]
dir = %s
database = $dir/index.txt
new_certs_dir = $dir/newcerts
certificate = %s
private_key = %s
serial = $dir/serial
default_md = sha256
policy = policy_any
x509_extensions = client_ext
copy_extensions = copy
[ policy_any ]
commonName = supplied
[ client_ext ]
basicConstraints = CA:FALSE
keyUsage = digitalSignature,keyEncipherment
extendedKeyUsage = clientAuth
''' % (ca_dir, os.path.join(d, 'ca.pem'), os.path.join(d, 'ca.key')))
    sh('ca', '-batch', '-config', ca_cnf, '-in', os.path.join(d, 'client.csr'),
       '-out', os.path.join(d, 'expired-client.pem'),
       '-startdate', '20000101000000Z', '-enddate', '20010101000000Z')


def cc_command(include_dirs, sanitize=False):
    cc = shutil.which(os.environ.get('CC', 'cc'))
    if not cc:
        raise Fail('C compiler not found')
    cmd = [cc, '-std=c11', '-Wall', '-Wextra', '-Werror', '-fno-pie', '-no-pie',
           '-pthread']
    if sanitize:
        cmd += ['-O1', '-g', '-fsanitize=address,undefined', '-fno-omit-frame-pointer']
    else:
        cmd += ['-O2']
    cmd += ['-I', NATIVE, '-I', SYS_NATIVE]
    for d in include_dirs:
        cmd += ['-I', d]
    cmd += [HARNESS, os.path.join(NATIVE, 'tls_adapter.c'),
            os.path.join(SYS_NATIVE, 'sysregistry.c'),
            os.path.join(SYS_NATIVE, 'sysadapter_real.c'),
            os.path.join(SYS_NATIVE, 'sysnet_real.c'),
            '-l:libssl.so.3', '-l:libcrypto.so.3', '-pthread']
    return cmd


def build_harness(tmp, include_dirs, sanitize=False):
    exe = os.path.join(tmp, 'tls-harness-asan' if sanitize else 'tls-harness')
    must(cc_command(include_dirs, sanitize) + ['-o', exe],
         'C harness build' + (' (ASAN/UBSAN)' if sanitize else ''))
    return exe


def run_harness(exe, modes, fixtures, sanitize=False):
    env = dict(os.environ)
    if sanitize:
        env['ASAN_OPTIONS'] = 'detect_leaks=1:abort_on_error=1'
        env['UBSAN_OPTIONS'] = 'halt_on_error=1'
    for mode in modes:
        p = run([exe, mode, fixtures], env=env, timeout=120)
        if p.returncode != 0:
            raise Fail('C harness %s failed:\n%s\n%s' % (mode, p.stdout, p.stderr))
        if 'ok ' + mode not in p.stdout:
            raise Fail('C harness %s printed no success marker:\n%s' %
                       (mode, p.stdout + p.stderr))


def private_cbind(tmp, include_dirs):
    src = os.path.join(PKG, 'tls_adapter.cbind')
    spec = json.load(open(src))
    tls_dir = PKG
    sys_dir = os.path.join(REPO, 'obo', 'experimental', 'sys')
    spec['wrapper'] = os.path.join(tls_dir, 'systls.obx')
    spec['sources'] = [
        os.path.join(tls_dir, 'native', 'tls_adapter.c'),
        os.path.join(sys_dir, 'native', 'sysregistry.c'),
        os.path.join(sys_dir, 'native', 'sysadapter_real.c'),
        os.path.join(sys_dir, 'native', 'sysnet_real.c'),
    ]
    spec['headers'] = [
        os.path.join(tls_dir, 'native', 'tls_adapter.h'),
        os.path.join(sys_dir, 'native', 'sysadapter.h'),
        os.path.join(sys_dir, 'native', 'sysnet.h'),
    ]
    spec['wrapper_sources'] = [os.path.join(tls_dir, 'systls.obx')]
    for rel in ('sysplatform.obx', 'syserrors.obx', 'sysstream.obx',
                'sysbytes.obx', 'sysreactor.obx', 'sysclock.obx', 'sysnet.obx',
                'sysaddress.obx', 'sysfile.obx', 'sysdir.obx', 'sysstat.obx',
                'sysbuffer.obx', 'syswaitio.obx', 'sysresolve.obx',
                'sysrandom.obx'):
        spec['wrapper_sources'].append(os.path.join(sys_dir, rel))
    spec['include_dirs'] = list(include_dirs)
    path = os.path.join(tmp, 'tls.cbind')
    with open(path, 'w') as f:
        json.dump(spec, f, indent=2)
    return path


def build_app(tmp, include_dirs):
    spec = private_cbind(tmp, include_dirs)
    out = os.path.join(tmp, 'app-out')
    os.makedirs(out, exist_ok=True)
    must([sys.executable, TOOLPY, 'build', spec, APP, '--out', out,
          '--obo', OBO], 'obo app build', timeout=300)
    must([sys.executable, TOOLPY, 'verify', os.path.join(out, 'manifest.json')],
         'obo app manifest verify')
    return os.path.join(out, 'app'), os.path.join(out, 'manifest.json')


def free_port():
    s = socket.socket()
    s.bind(('127.0.0.1', 0))
    port = s.getsockname()[1]
    s.close()
    return port


class App:
    def __init__(self, exe, fixtures, mode):
        self.port = free_port()
        self.proc = subprocess.Popen(
            [exe, str(self.port), mode, os.path.join(fixtures, 'server.pem'),
             os.path.join(fixtures, 'server.key'),
             os.path.join(fixtures, 'ca.pem') if mode in ('clientcert', 'busy')
             else ''],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, bufsize=1,
            env=dict(os.environ, OBO_CHECK='1'))
        self.pre = []
        while True:
            line = self.proc.stdout.readline()
            if line == '':
                out, err = self.proc.communicate(timeout=10)
                raise Fail('app did not start:\n%s\n%s' % (out, err))
            if line.strip() == 'ready':
                return
            self.pre.append(line)

    def finish(self, timeout=15):
        try:
            out, err = self.proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            out, err = self.proc.communicate()
            raise Fail('app did not exit:\n%s\n%s' % (out, err))
        return ''.join(self.pre) + out, err


def ssl_context(fixtures, version=None, alpn=None, cert=None, key=None):
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.load_verify_locations(os.path.join(fixtures, 'ca.pem'))
    ctx.check_hostname = True
    if version == '1.2':
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        ctx.maximum_version = ssl.TLSVersion.TLSv1_2
    if version == '1.3':
        ctx.minimum_version = ssl.TLSVersion.TLSv1_3
        ctx.maximum_version = ssl.TLSVersion.TLSv1_3
    if alpn is not None:
        ctx.set_alpn_protocols(alpn)
    if cert is not None:
        ctx.load_cert_chain(cert, key)
    return ctx


def echo_client(app, fixtures, version='1.3', alpn=None, cert=None, key=None):
    ctx = ssl_context(fixtures, version, alpn, cert, key)
    raw = socket.create_connection(('127.0.0.1', app.port), timeout=8)
    ss = ctx.wrap_socket(raw, server_hostname='localhost')
    ss.sendall(b'hello over tls')
    got = b''
    while len(got) < 14:
        part = ss.recv(64)
        if not part:
            break
        got += part
    if got != b'hello over tls':
        raise Fail('echo mismatch: %r' % got)
    selected = ss.selected_alpn_protocol()
    version_seen = ss.version()
    ss.close()
    return version_seen, selected


def scenario(exe, fixtures, mode, expect, version='1.3', alpn=None,
             cert=None, key=None):
    app = App(exe, fixtures, mode)
    err = None
    try:
        version_seen, selected = echo_client(app, fixtures, version, alpn, cert, key)
    except Exception as e:
        err = e
    out, errtxt = app.finish()
    if err is not None:
        if expect is None:
            raise Fail('%s client failed unexpectedly: %r\n%s\n%s'
                       % (mode, err, out, errtxt))
        for marker in expect:
            if marker not in out and marker not in errtxt:
                raise Fail('%s missing %r:\n%s\n%s' % (mode, marker, out, errtxt))
        return out
    if expect is not None:
        raise Fail('%s was expected to fail but succeeded' % mode)
    for marker in ('version=TLSv' + version, 'counts=0,0',
                   'double-close=false,false', 'max-transfer=65536',
                   'secure=true', 'verify-text=ok'):
        if marker not in out:
            raise Fail('%s missing %r:\n%s' % (mode, marker, out))
    if alpn is not None and selected != alpn[0]:
        raise Fail('%s ALPN %r, expected %r' % (mode, selected, alpn[0]))
    return out


def offset_scenario(exe, fixtures):
    app = App(exe, fixtures, 'offset')
    ctx = ssl_context(fixtures, '1.3', ['http/1.1'])
    ss = ctx.wrap_socket(socket.create_connection(('127.0.0.1', app.port), timeout=8),
                         server_hostname='localhost')
    greeting = b''
    while len(greeting) < 5:
        part = ss.recv(16)
        if not part:
            break
        greeting += part
    if greeting != b'34567':
        raise Fail('offset write greeting %r' % greeting)
    ss.sendall(b'hello over tls')
    got = b''
    while len(got) < 14:
        part = ss.recv(64)
        if not part:
            break
        got += part
    if got != b'hello over tls':
        raise Fail('offset echo %r' % got)
    ss.close()
    out, err = app.finish()
    for marker in ('offset-wrote=5', 'counts=0,0'):
        if marker not in out:
            raise Fail('offset scenario missing %r:\n%s\n%s' % (marker, out, err))


def alert_check(exe, fixtures, mode, alpn, needle):
    app = App(exe, fixtures, mode)
    ctx = ssl_context(fixtures, '1.3', alpn)
    failed = None
    try:
        ss = ctx.wrap_socket(socket.create_connection(('127.0.0.1', app.port), timeout=8),
                             server_hostname='localhost')
        ss.close()
    except ssl.SSLError as e:
        failed = e
    except Exception as e:
        failed = e
    out, err = app.finish()
    if failed is None:
        raise Fail('%s alert check unexpectedly succeeded' % mode)
    if isinstance(failed, ssl.SSLEOFError):
        raise Fail('%s got SSLEOFError, not a TLS alert: %r\n%s\n%s'
                   % (mode, failed, out, err))
    text = str(failed) + ' ' + repr(failed)
    if needle not in text.upper() and needle not in out.upper():
        raise Fail('%s missing alert %r: %r\n%s\n%s' % (mode, needle, failed, out, err))


def large_echo_scenario(exe, fixtures):
    app = App(exe, fixtures, 'echo')
    ctx = ssl_context(fixtures, '1.3', ['http/1.1'])
    ss = ctx.wrap_socket(socket.create_connection(('127.0.0.1', app.port), timeout=8),
                         server_hostname='localhost')
    payload = bytes((i * 31) & 0xff for i in range(65536))
    ss.sendall(payload)
    got = b''
    while len(got) < len(payload):
        part = ss.recv(65536)
        if not part:
            break
        got += part
    if got != payload:
        raise Fail('large echo mismatch (%d/%d bytes)' % (len(got), len(payload)))
    ss.close()
    out, err = app.finish()
    if 'counts=0,0' not in out:
        raise Fail('large echo did not finish cleanly:\n%s\n%s' % (out, err))


def weak_policy_scenario(exe, fixtures):
    app = App(exe, fixtures, 'weak-policy')
    ctx = ssl_context(fixtures, '1.3', ['http/1.1'])
    ss = ctx.wrap_socket(socket.create_connection(('127.0.0.1', app.port), timeout=8),
                         server_hostname='localhost')
    ss.sendall(b'hello over tls')
    got = b''
    while len(got) < 14:
        part = ss.recv(64)
        if not part:
            break
        got += part
    if got != b'hello over tls':
        raise Fail('weak-policy normal echo %r' % got)
    ss.close()
    out, err = app.finish()
    for marker in ('weak-policy=InvalidArgument', 'weak-groups=InvalidArgument',
                   'counts=0,0'):
        if marker not in out:
            raise Fail('weak-policy missing %r:\n%s\n%s' % (marker, out, err))


def weak_policy_null_rejected(exe, fixtures):
    app = App(exe, fixtures, 'weak-policy')
    cmd = ['timeout', '6', 'openssl', 's_client', '-connect',
           '127.0.0.1:%d' % app.port, '-servername', 'localhost',
           '-tls1_2', '-cipher', 'eNULL:@SECLEVEL=0', '-brief']
    p = run(cmd, timeout=15)
    text = (p.stdout or '') + (p.stderr or '')
    if 'Ciphersuite:' in text and 'NULL' in text.split('Ciphersuite:', 1)[1].splitlines()[0]:
        raise Fail('NULL cipher was negotiated:\n%s' % text[:3000])
    app.finish()


def min_version_check(exe, fixtures):
    app = App(exe, fixtures, 'min13')
    try:
        ctx = ssl_context(fixtures, '1.2', ['http/1.1'])
        raw = socket.create_connection(('127.0.0.1', app.port), timeout=8)
        ctx.wrap_socket(raw, server_hostname='localhost')
        raise Fail('TLS 1.2 client unexpectedly succeeded against min TLS 1.3')
    except ssl.SSLError:
        pass
    out, err = app.finish()
    if 'handshake-error' not in out and 'error=' not in out:
        raise Fail('min-version rejection had no error:\n%s\n%s' % (out, err))
    app2 = App(exe, fixtures, 'min13')
    ctx = ssl_context(fixtures, '1.3', ['http/1.1'])
    ss = ctx.wrap_socket(socket.create_connection(('127.0.0.1', app2.port), timeout=8),
                         server_hostname='localhost')
    ss.sendall(b'hello over tls')
    got = b''
    while len(got) < 14:
        part = ss.recv(64)
        if not part:
            break
        got += part
    if got != b'hello over tls':
        raise Fail('min TLS 1.3 echo %r' % got)
    ss.close()
    out, err = app2.finish()
    if 'counts=0,0' not in out:
        raise Fail('min-version TLS 1.3 did not finish cleanly:\n%s\n%s' % (out, err))


def forged_check(exe, fixtures):
    p = run([exe, str(free_port()), 'forged', os.path.join(fixtures, 'server.pem'),
             os.path.join(fixtures, 'server.key')], timeout=15)
    text = (p.stdout or '') + (p.stderr or '')
    if 'forged=InvalidResource' not in text:
        raise Fail('forged TlsContext was not rejected:\n%s' % text)


def s_client(exe, fixtures, mode, alpn, tlsver, want_version, want_alpn):
    app = App(exe, fixtures, mode)
    cmd = ['timeout', '8', 'openssl', 's_client', '-connect',
           '127.0.0.1:%d' % app.port, '-servername', 'localhost',
           '-CAfile', os.path.join(fixtures, 'ca.pem'),
           '-verify_return_error', '-tls1_3' if tlsver == '1.3' else '-tls1_2']
    if alpn is not None:
        cmd += ['-alpn', alpn]
    p = run(cmd, input='hello s_client\n', timeout=20)
    text = (p.stdout or '') + (p.stderr or '')
    markers = ['Verification: OK', want_version]
    if want_alpn:
        markers.append('ALPN protocol: ' + want_alpn)
    else:
        markers.append('No ALPN negotiated')
    for marker in markers:
        if marker not in text:
            raise Fail('s_client %s/%s missing %r:\n%s' %
                       (tlsver, alpn, marker, text[:4000]))
    out, errtxt = app.finish()
    if 'counts=0,0' not in out:
        raise Fail('s_client app did not report zero live resources:\n%s\n%s'
                   % (out, errtxt))


def curl_check(exe, fixtures):
    if not shutil.which('curl'):
        print('curl: skipped (not installed)')
        return
    app = App(exe, fixtures, 'echo')
    cmd = ['curl', '-v', '--http1.1', '--cacert', os.path.join(fixtures, 'ca.pem'),
           '--max-time', '3', 'https://localhost:%d/' % app.port]
    p = run(cmd, timeout=15)
    text = (p.stdout or '') + (p.stderr or '')
    if 'SSL connection using' not in text:
        raise Fail('curl did not establish TLS:\n%s' % text)
    out, errtxt = app.finish()
    if 'counts=0,0' not in out:
        raise Fail('curl app did not report zero live resources:\n%s\n%s'
                   % (out, errtxt))
    print('curl: ok (TLS handshake observed)')


def cbind_contract_check():
    path = os.path.join(PKG, 'tls_adapter.cbind')
    spec = json.load(open(path))
    for f in spec['functions']:
        if f['ret'] not in ('i32', 'i64'):
            raise Fail('cbind %s has non-scalar result' % f['name'])
        if any(a != 'i64' for a in f['args']):
            raise Fail('cbind %s has a non-i64 argument' % f['name'])
        for bad in ('callbacks', 'variadic', 'struct_by_value', 'tls'):
            if f.get(bad):
                raise Fail('cbind %s declares %s' % (f['name'], bad))
    src = open(os.path.join(NATIVE, 'tls_adapter.c')).read()
    if '__cinterop' in src:
        raise Fail('C adapter contains an obo interop call')
    print('cbind contract: ok (%d scalar functions)' % len(spec['functions']))


def main():
    os.makedirs(TMPROOT, exist_ok=True)
    cinterop = load_cinterop_tests()
    if not cinterop.openssl_library_present():
        print('TLS suite: skipped (libssl.so.3/libcrypto.so.3 absent)')
        return 0
    if not os.path.exists(OBO):
        raise Fail('obo compiler not found: ' + OBO)
    include_dirs = cinterop.openssl_include_dirs()
    cbind_contract_check()

    tmp = tempfile.mkdtemp(prefix='tls-suite-', dir=TMPROOT)
    fixtures = os.path.join(tmp, 'fixtures')
    gen_certs(fixtures)
    print('fixtures: ok')

    harness = build_harness(tmp, include_dirs)
    modes = ['tls13', 'tls12', 'alpn-h2', 'alpn-none', 'echo13', 'echo12',
             'require-missing', 'require-overlap', 'prefer-none', 'sni',
             'clientcert', 'truncate', 'truncate-mid', 'keyupdate', 'large', 'fragment', 'prefix',
             'cert-errors', 'validate', 'limits', 'early', 'reneg', 'bypass', 'charge', 'chain', 'trust-atomic',
             'truncate-handshake']
    run_harness(harness, modes, fixtures)
    print('C harness: ok (%d modes)' % len(modes))

    asan = build_harness(tmp, include_dirs, sanitize=True)
    asan_modes = ['tls13', 'tls12', 'echo13', 'echo12', 'require-missing',
                  'clientcert', 'truncate', 'truncate-mid', 'keyupdate', 'large', 'fragment',
                  'prefix', 'cert-errors', 'validate', 'limits', 'early', 'reneg', 'bypass', 'charge', 'chain', 'trust-atomic',
             'truncate-handshake']
    run_harness(asan, asan_modes, fixtures, sanitize=True)
    print('ASAN/UBSAN harness: ok (%d modes)' % len(asan_modes))

    app, manifest = build_app(tmp, include_dirs)
    print('obo app build: ok')

    scenario(app, fixtures, 'echo', None, '1.3', ['http/1.1'])
    scenario(app, fixtures, 'echo', None, '1.2', ['http/1.1'])
    scenario(app, fixtures, 'h2', None, '1.3', ['h2'])
    scenario(app, fixtures, 'echo', None, '1.3', None)
    out = scenario(app, fixtures, 'require', ['AlpnRequired'], '1.3', None)
    alert_check(app, fixtures, 'require', None, 'ALERT')
    out = scenario(app, fixtures, 'require', ['NoApplicationProtocol'], '1.3', ['h2'])
    alert_check(app, fixtures, 'require', ['h2'], 'NOAPPLICATIONPROTOCOL')
    out = scenario(app, fixtures, 'busy', None, '1.3', ['http/1.1'])
    if 'busy=Busy' not in out:
        raise Fail('closeContext while live did not report Busy:\n%s' % out)
    out = scenario(app, fixtures, 'policy', None, '1.3', ['http/1.1'])
    if 'policy=ok' not in out:
        raise Fail('policy setters did not apply:\n%s' % out)
    out = scenario(app, fixtures, 'sys-trust', None, '1.3', ['http/1.1'])
    if 'sys-trust=Unsupported' not in out:
        raise Fail('server useSystemTrustStore did not report Unsupported:\n%s' % out)
    print('obo ALPN/SNI/busy scenarios: ok')

    scenario(app, fixtures, 'clientcert',
             ['NoPeerCertificate'], '1.3', ['http/1.1'], None, None)
    scenario(app, fixtures, 'clientcert', None, '1.3', ['http/1.1'],
             os.path.join(fixtures, 'client.pem'),
             os.path.join(fixtures, 'client.key'))
    scenario(app, fixtures, 'clientcert', ['VerifyFailed'], '1.3', ['http/1.1'],
             os.path.join(fixtures, 'untrusted-client.pem'),
             os.path.join(fixtures, 'untrusted-client.key'))
    scenario(app, fixtures, 'clientcert', ['VerifyFailed'], '1.3', ['http/1.1'],
             os.path.join(fixtures, 'expired-client.pem'),
             os.path.join(fixtures, 'client.key'))
    print('obo client-certificate scenarios: ok')

    offset_scenario(app, fixtures)
    large_echo_scenario(app, fixtures)
    weak_policy_scenario(app, fixtures)
    weak_policy_null_rejected(app, fixtures)
    min_version_check(app, fixtures)
    forged_check(app, fixtures)
    print('obo offset/weak-policy/min-version/forged scenarios: ok')

    s_client(app, fixtures, 'h2', 'h2', '1.3', 'TLSv1.3', 'h2')
    s_client(app, fixtures, 'echo', 'http/1.1', '1.2', 'TLSv1.2', 'http/1.1')
    s_client(app, fixtures, 'echo', None, '1.3', 'TLSv1.3', '')
    print('openssl s_client: ok')

    curl_check(app, fixtures)
    print('TLS suite: ok')
    print('artifacts: ' + tmp)
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Fail as e:
        print('TLS suite FAILED: %s' % e)
        sys.exit(1)
