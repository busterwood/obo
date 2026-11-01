#!/usr/bin/env python3
"""Targeted tests for the experimental cinterop prototype.

    python3 obo/experimental/cinterop/tests/test_cinterop.py

This is not part of the public compiler test catalog. It builds a real scalar
module end to end, checks the ABI/alignment assertions, checks the synthetic
startup stack and its bounds, exercises direct-call and provenance failures,
checks private include/cflag provenance and RELRO, conditionally builds the
PQlibVersion probe when the host has libpq.so.5, and conditionally runs the
hosted OpenSSL qualification. No database connection is made.
"""
import glob
import hashlib
import json
import os
import re
import resource
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
TOOL = os.path.dirname(HERE)
REPO = os.path.dirname(os.path.dirname(os.path.dirname(TOOL)))   # repository root
EX = os.path.join(TOOL, 'examples')
OBO = os.path.abspath(os.environ.get('OBO', os.path.join(REPO, 'obo', 'obo')))
TOOLPY = os.path.join(TOOL, 'cinterop.py')
# Scratch root for private build trees and the OpenSSL header cache. Honour the
# environment (TMPROOT, then TMPDIR through tempfile) instead of a fixed /tmp path.
TMPROOT = os.environ.get('TMPROOT') or tempfile.gettempdir()
LIBPQ_PROBE_PATHS = [
    '/lib/x86_64-linux-gnu/libpq.so.5',
    '/usr/lib/x86_64-linux-gnu/libpq.so.5',
]


class Fail(Exception):
    pass


class Skip(Exception):
    pass


def libpq_present():
    # One policy for every real-libpq test: skip only when the declared
    # libpq.so.5 paths are absent. If the library is present, a build failure is
    # a test failure, not a skip.
    if os.environ.get('CINTEROP_TEST_NO_LIBPQ') == '1':
        return False
    return any(os.path.exists(p) for p in LIBPQ_PROBE_PATHS)


def libpq_skip(label):
    print('%s: skipped (libpq.so.5 is absent from the declared probe paths)' % label)


ZLIB_HEADER = '/usr/include/zlib.h'
ZLIB_PROBE_PATHS = ['/usr/lib/x86_64-linux-gnu/libz.so.1',
                    '/lib/x86_64-linux-gnu/libz.so.1']


def zlib_present():
    return os.path.exists(ZLIB_HEADER) and any(os.path.exists(p) for p in ZLIB_PROBE_PATHS)


OPENSSL_LIB_FALLBACKS = {
    'libssl.so.3': ['/usr/lib/x86_64-linux-gnu/libssl.so.3',
                    '/lib/x86_64-linux-gnu/libssl.so.3'],
    'libcrypto.so.3': ['/usr/lib/x86_64-linux-gnu/libcrypto.so.3',
                       '/lib/x86_64-linux-gnu/libcrypto.so.3'],
}
OPENSSL_HEADER = '/usr/include/openssl/ssl.h'
OPENSSL_CACHE = os.path.join(TMPROOT, 'cinterop-openssl-dev')


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for b in iter(lambda: f.read(1 << 20), b''):
            h.update(b)
    return h.hexdigest()


def ldconfig_paths(name):
    if not shutil.which('ldconfig') and not os.path.exists('/sbin/ldconfig'):
        return []
    p = run([shutil.which('ldconfig') or '/sbin/ldconfig', '-p'])
    if p.returncode != 0:
        return []
    paths = []
    for line in p.stdout.splitlines():
        parts = line.split()
        if parts and parts[0] == name and '=>' in line:
            path = line.split('=>', 1)[1].strip().split()[0]
            if os.path.exists(path):
                paths.append(path)
    return paths


def openssl_library_paths(name):
    paths = ldconfig_paths(name)
    if not paths:
        paths = [p for p in OPENSSL_LIB_FALLBACKS[name] if os.path.exists(p)]
    return paths


def openssl_library_present():
    if os.environ.get('CINTEROP_TEST_NO_OPENSSL') == '1':
        return False
    return (bool(openssl_library_paths('libssl.so.3'))
            and bool(openssl_library_paths('libcrypto.so.3')))


def openssl_installed_version():
    if not shutil.which('dpkg-query'):
        return None
    for package in ('libssl3t64', 'libssl3'):
        p = run(['dpkg-query', '-W', '-f=${Version}', package])
        if p.returncode == 0 and p.stdout.strip():
            return p.stdout.strip()
    return None


def openssl_header_version_string():
    path = '/usr/include/openssl/opensslv.h'
    if not os.path.isfile(path):
        return None
    m = re.search(r'OPENSSL_VERSION_STR\s+"([^"]+)"', open(path).read())
    return m.group(1) if m else None


def openssl_runtime_version_string():
    if not shutil.which('openssl'):
        return None
    p = run(['openssl', 'version', '-v'])
    if p.returncode != 0:
        return None
    parts = p.stdout.split()
    return parts[1] if len(parts) > 1 else None


def openssl_system_headers_match(version):
    if not os.path.exists(OPENSSL_HEADER):
        return False
    if version is not None and shutil.which('dpkg-query'):
        p = run(['dpkg-query', '-W', '-f=${Version}', 'libssl-dev'])
        if p.returncode == 0 and p.stdout.strip() == version:
            return True
    # Fall back to the upstream version string when the package database is not
    # available (or says the headers are not package-owned).
    header = openssl_header_version_string()
    runtime = openssl_runtime_version_string()
    return header is not None and runtime is not None and header == runtime


def open_cache_manifest(root):
    return os.path.join(root, 'cache-manifest.sha256')


def write_cache_manifest(include_root):
    """Hash the extracted include tree so a later cache hit is content-checked."""
    entries = []
    for dirpath, _, filenames in os.walk(include_root):
        for name in filenames:
            path = os.path.join(dirpath, name)
            entries.append((os.path.relpath(path, include_root), sha256_file(path)))
    entries.sort()
    with open(open_cache_manifest(os.path.dirname(os.path.dirname(include_root))), 'w') as f:
        for rel, digest in entries:
            f.write('%s  %s\n' % (digest, rel))


def cache_manifest_ok(cache, include_root):
    manifest = open_cache_manifest(cache)
    if not os.path.isfile(manifest):
        return False
    want = {}
    for line in open(manifest):
        parts = line.rstrip('\n').split('  ', 1)
        if len(parts) == 2:
            want[parts[1]] = parts[0]
    got = {}
    for dirpath, _, filenames in os.walk(include_root):
        for name in filenames:
            path = os.path.join(dirpath, name)
            got[os.path.relpath(path, include_root)] = sha256_file(path)
    return want == got


def cache_manifest_self_test(tmp):
    root = os.path.join(tmp, 'cache-manifest-test')
    include = os.path.join(root, 'usr', 'include')
    os.makedirs(os.path.join(include, 'openssl'))
    with open(os.path.join(include, 'openssl', 'ssl.h'), 'w') as f:
        f.write('#define TEST 1\n')
    write_cache_manifest(include)
    if not cache_manifest_ok(root, include):
        raise Fail("a freshly written cache manifest did not validate")
    with open(os.path.join(include, 'openssl', 'ssl.h'), 'a') as f:
        f.write('#define TEST 2\n')
    if cache_manifest_ok(root, include):
        raise Fail("a tampered private-header cache was not rejected")


def openssl_include_dirs():
    """Return include roots for the exact installed libssl-dev without root.

    If the host already has matching development headers they are used
    directly. Otherwise the matching package is downloaded with `apt-get
    download` and unpacked with `dpkg-deb -x` into a private cache. The cache
    key is the installed library package version, so headers and library stay
    paired; a cache hit is content-checked against a stored hash manifest.
    """
    version = openssl_installed_version()
    if openssl_system_headers_match(version):
        dirs = ['/usr/include']
        if os.path.isdir('/usr/include/x86_64-linux-gnu'):
            dirs.append('/usr/include/x86_64-linux-gnu')
        return dirs
    if version is None:
        raise Fail("libssl is present but its installed package version is unknown; "
                   "cannot select matching libssl-dev headers")
    cache = os.path.join(OPENSSL_CACHE, version)
    marker = os.path.join(cache, '.complete')
    include = os.path.join(cache, 'usr', 'include')
    arch_include = os.path.join(include, 'x86_64-linux-gnu')
    if (os.path.isfile(marker) and os.path.exists(os.path.join(include, 'openssl', 'ssl.h'))
            and cache_manifest_ok(cache, include)):
        return [include, arch_include]
    work = tempfile.mkdtemp(prefix='openssl-dev-', dir=TMPROOT)
    try:
        try:
            p = run(['apt-get', 'download', 'libssl-dev=' + version], cwd=work, timeout=300)
        except (OSError, subprocess.TimeoutExpired) as e:
            raise Skip("cannot run apt-get download libssl-dev=%s: %s" % (version, e))
        if p.returncode != 0:
            # The download needs the network; without it the qualification is
            # skipped (printed by the caller), never silently passed.
            first = (p.stderr or p.stdout).strip().splitlines()
            raise Skip("cannot download libssl-dev=%s: %s"
                       % (version, first[-1] if first else 'apt-get failed'))
        debs = glob.glob(os.path.join(work, 'libssl-dev_*.deb'))
        if len(debs) != 1:
            raise Fail("apt-get download produced %d libssl-dev archives" % len(debs))
        package_version = run(['dpkg-deb', '-f', debs[0], 'Version']).stdout.strip()
        if package_version != version:
            raise Fail("downloaded libssl-dev %r does not match installed libssl %r"
                       % (package_version, version))
        root = os.path.join(work, 'root')
        need(run(['dpkg-deb', '-x', debs[0], root]), 'libssl-dev extraction')
        root_include = os.path.join(root, 'usr', 'include')
        if not os.path.exists(os.path.join(root_include, 'openssl', 'ssl.h')):
            raise Fail("extracted libssl-dev has no openssl/ssl.h")
        write_cache_manifest(root_include)
        os.makedirs(OPENSSL_CACHE, exist_ok=True)
        tmp_cache = cache + '.tmp-%d' % os.getpid()
        shutil.rmtree(tmp_cache, ignore_errors=True)
        shutil.move(root, tmp_cache)
        shutil.rmtree(cache, ignore_errors=True)
        os.rename(tmp_cache, cache)
        with open(marker, 'w') as f:
            f.write(version + '\n')
        return [include, arch_include]
    finally:
        shutil.rmtree(work, ignore_errors=True)


def run(cmd, **kw):
    timeout = kw.pop('timeout', 300)
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, **kw)


def must(cmd, **kw):
    p = run(cmd, **kw)
    if p.returncode != 0:
        raise Fail("command failed: %s\n%s" % (' '.join(cmd), (p.stderr or p.stdout).strip()))
    return p


def build(spec, app, out):
    return run([sys.executable, TOOLPY, 'build', spec, app, '--out', out, '--obo', OBO])


def need(p, what):
    if p.returncode != 0:
        raise Fail("%s failed:\n%s" % (what, (p.stderr or p.stdout).strip()))
    return p


def no_core_env(tmp):
    """Disable core dumps for deliberate-fault tests.

    Some hosts route core dumps through a slow pipe handler that ignores
    RLIMIT_CORE, so waiting for SIGSEGV can take much longer than the test
    timeout. The preload's constructor clears PR_SET_DUMPABLE before main; the
    signal itself and the exit status are unchanged.
    """
    src = os.path.join(tmp, 'cinterop_no_core.c')
    so = os.path.join(tmp, 'cinterop_no_core.so')
    if not os.path.exists(so):
        with open(src, 'w') as f:
            f.write('#include <sys/prctl.h>\n'
                    '__attribute__((constructor)) static void obo_test_no_core(void) {\n'
                    '    prctl(PR_SET_DUMPABLE, 0, 0, 0, 0);\n}\n')
        need(run([os.environ.get('CC', 'gcc'), '-shared', '-fPIC', '-o', so, src]),
             'no-core preload build')
    return {'LD_PRELOAD': so}


def verify(out):
    return run([sys.executable, TOOLPY, 'verify', os.path.join(out, 'manifest.json')])


def assert_relro_sections(exe):
    """The final ELF must cover constructors/destructors with GNU_RELRO."""
    ph = need(run(['readelf', '-lW', exe]), 'readelf -l')
    ranges = []
    for line in ph.stdout.splitlines():
        parts = line.split()
        if parts and parts[0] == 'GNU_RELRO':
            ranges.append((int(parts[2], 16), int(parts[2], 16) + int(parts[5], 16)))
    if not ranges:
        raise Fail("final artifact has no GNU_RELRO segment")
    sw = need(run(['readelf', '-SW', exe]), 'readelf -S')
    for name in ('.preinit_array', '.init_array', '.fini_array'):
        for line in sw.stdout.splitlines():
            parts = line.split()
            if name in parts:
                i = parts.index(name)
                addr = int(parts[i + 2], 16)
                size = int(parts[i + 4], 16)
                if size and not any(a <= addr and addr + size <= b for a, b in ranges):
                    raise Fail("%s at %#x..%#x is outside GNU_RELRO" % (name, addr, addr + size))


def scalar(tmp):
    out = os.path.join(tmp, 'scalar')
    need(build(os.path.join(EX, 'scalar.cbind'), os.path.join(EX, 'scalar_app.obx'), out), 'scalar build')
    p = need(run([os.path.join(out, 'app')], env=dict(os.environ, OBO_CHECK='1')), 'scalar run')
    if p.stdout.strip() != 'scalar ok':
        raise Fail("scalar output was %r" % p.stdout.strip())
    sym = open(os.path.join(out, 'sym', 'hello.sym')).read()
    if 'fn add(p0: int, p1: int): int freesall writeshandles' not in sym:
        raise Fail("wrapper .sym is not conservatively freesall/writeshandles")
    relocs = open(os.path.join(out, 'native.relocs')).read().splitlines()
    if not relocs or relocs[0] != 'cinterop-relocs 2':
        raise Fail("native sidecar header is missing or not v2")
    if not any(' add 2 call - i64 freesall,writeshandles' in ln for ln in relocs):
        raise Fail("native sidecar does not carry the checked add contract")
    elf = need(run(['readelf', '-hW', os.path.join(out, 'app')]), 'readelf -h')
    if 'EXEC' not in elf.stdout:
        raise Fail("scalar artifact is not ET_EXEC")
    ph = need(run(['readelf', '-lW', os.path.join(out, 'app')]), 'readelf -l')
    for addr in ('0x0000000000401000', '0x0000000008000000', '0x000000000c000000',
                 '0x0000000010000000', '0x0000000020000000'):
        if addr not in ph.stdout:
            raise Fail("missing obo load segment %s" % addr)
    if 'GNU_STACK' not in ph.stdout or 'RWE' in ph.stdout:
        raise Fail("final stack is not non-executable")
    assert_relro_sections(os.path.join(out, 'app'))
    dyn = need(run(['readelf', '-dW', os.path.join(out, 'app')]), 'readelf -d')
    if 'BIND_NOW' not in dyn.stdout and 'NOW' not in dyn.stdout:
        raise Fail("final artifact does not request immediate binding")
    undef = need(run(['nm', '-u', os.path.join(out, 'app')]), 'nm -u')
    if 'obo_native_' in undef.stdout:
        raise Fail("final executable has an unresolved thunk")
    need(verify(out), 'manifest verify')
    m = json.load(open(os.path.join(out, 'manifest.json')))
    if m.get('signature_check') != 'header':
        raise Fail("scalar signature_check is %r, expected header" % m.get('signature_check'))
    for key in ('include_dirs', 'include_paths', 'cflags', 'header_deps',
                'link_flags', 'hardening'):
        if key not in m:
            raise Fail("build manifest is missing %r" % key)
    if m['hardening'] != {'relro': True, 'now': True, 'noexecstack': True}:
        raise Fail("build manifest hardening record is %r" % m['hardening'])
    if '-Wl,-z,relro' not in m['link_flags'] or '-Wl,-z,now' not in m['link_flags']:
        raise Fail("build manifest link_flags are %r" % m['link_flags'])
    # Freestanding ABI probe: no libc, no dynamic loader; checks the thunk and
    # SysV callee-saved registers directly.
    probe = os.path.join(tmp, 'abi_probe')
    need(run(['gcc', '-nostdlib', '-no-pie', '-o', probe,
              os.path.join(EX, 'abi_probe.S'),
              os.path.join(out, 'shim.o'),
              os.path.join(out, 'src0.o'),
              os.path.join(out, 'src1.o')]), 'abi probe link')
    need(run([probe]), 'abi probe run')
    nm = must(['nm', '-u', probe])
    if nm.stdout.strip():
        raise Fail("freestanding ABI probe has undefined symbols: %s" % nm.stdout.strip())
    return out


def startup(tmp, scalar_out):
    out = os.path.join(tmp, 'runtime')
    need(build(os.path.join(EX, 'scalar.cbind'), os.path.join(EX, 'runtime_app.obx'), out), 'runtime build')
    app = os.path.join(out, 'app')
    for env, label in (({'FOO': 'bar'}, 'odd'), ({'FOO': 'bar', 'BAZ': 'qux'}, 'even')):
        p = need(run([app, 'one', 'two'], env=env), 'runtime %s' % label)
        if 'argc=3' not in p.stdout or 'arg0=' + app not in p.stdout or 'FOO=bar' not in p.stdout:
            raise Fail("runtime %s argv/env mismatch:\n%s" % (label, p.stdout))
    p = need(run([app], env={'FOO': 'bar', 'OBO_STATS': '1'}), 'stats visibility')
    if 'peak bytes' not in p.stderr:
        raise Fail("OBO_STATS was not visible through the environment block")
    leak = os.path.join(tmp, 'leak')
    need(build(os.path.join(EX, 'scalar.cbind'), os.path.join(EX, 'leak_app.obx'), leak), 'leak build')
    p = run([os.path.join(leak, 'app')], env={'FOO': 'bar', 'OBO_CHECK': '1'})
    if p.returncode != 8 or 'file descriptors leaked' not in p.stderr:
        raise Fail("OBO_CHECK was not visible: exit %d, stderr %r" % (p.returncode, p.stderr))
    p = run([os.path.join(leak, 'app')], env={'FOO': 'bar'})
    if p.returncode != 0:
        raise Fail("leak app failed without OBO_CHECK: %d %s" % (p.returncode, p.stderr))
    # Near-limit pointers: 116k very short variables are permitted by ARG_MAX
    # and exceed the checked 1 MiB pointer block; 110k still fits.
    for n, want in ((110000, 0), (116000, 127)):
        env = {('A%05d' % i): 'x' for i in range(n)}
        p = run([app], env=env)
        if want == 0 and p.returncode != 0:
            raise Fail("near-limit fit failed at %d vars: %d %s" % (n, p.returncode, p.stderr))
        if want == 127 and (p.returncode != 127 or 'startup stack overflow' not in p.stderr):
            raise Fail("near-limit overflow did not fail cleanly at %d vars: %d %s"
                       % (n, p.returncode, p.stderr[:200]))

    p = run([app], env={'OBO_CINTEROP_STACK_BYTES': '4096'})
    if p.returncode != 127 or 'invalid stack size' not in p.stderr:
        raise Fail("invalid guarded-stack size was not rejected cleanly: %d %s"
                   % (p.returncode, p.stderr[:200]))

    # Guarded stack: touching either guard is a controlled SIGSEGV, not a
    # write into adjacent memory. No signal handler is installed.
    guard = os.path.join(tmp, 'guard_stack_test')
    need(run(['gcc', '-O2', '-o', guard,
              os.path.join(HERE, 'guard_stack_test.c'),
              os.path.join(TOOL, 'host_stack.c')]), 'guard stack C build')
    p = need(run([guard, 'ok']), 'guard stack ok run')
    if 'guard_stack=ok' not in p.stdout:
        raise Fail("guard stack normal run failed:\n%s" % p.stdout)
    p = need(run([guard, 'invalid']), 'guard stack invalid run')
    if 'invalid_rejected=' not in p.stdout:
        raise Fail("guard stack invalid-size run did not report a clean error")
    fault_env = no_core_env(tmp)
    for mode in ('low', 'high'):
        p = run([guard, mode], env=dict(os.environ, **fault_env))
        if p.returncode != -11:
            raise Fail("guard stack %s did not die by SIGSEGV: %d %s"
                       % (mode, p.returncode, p.stderr[:120]))

    # The actual obo entry stack is guarded too: deep recursion must fault on
    # the guard page rather than corrupting its neighbours.
    deep = os.path.join(tmp, 'deep_recursion')
    p = build(os.path.join(EX, 'scalar.cbind'), os.path.join(EX, 'deep_recursion_app.obx'), deep)
    if p.returncode != 0:
        raise Fail("deep-recursion app build failed:\n%s" % (p.stderr or p.stdout))
    p = run([os.path.join(deep, 'app')],
            env=dict(os.environ, OBO_CINTEROP_STACK_BYTES='524288', **fault_env),
            timeout=20)
    if p.returncode != -11:
        raise Fail("deep recursion did not hit the stack guard: %d %s"
                   % (p.returncode, p.stderr[:120]))

    # Proper hosted shutdown: constructors, atexit and C stdio flushing run, and
    # exit statuses from normal return, exit() and OBO_CHECK are preserved.
    shutdown_out = os.path.join(tmp, 'shutdown')
    need(build(os.path.join(EX, 'shutdown.cbind'), os.path.join(EX, 'shutdown_app.obx'),
               shutdown_out), 'shutdown app build')
    p = need(run([os.path.join(shutdown_out, 'app')]), 'shutdown app run')
    if 'shutdown-app' not in p.stdout or 'cstdio-flush' not in p.stdout:
        raise Fail("hosted normal shutdown did not flush C stdio:\n%s" % p.stdout)
    if 'ctor-ran' not in p.stderr or 'atexit-ran' not in p.stderr:
        raise Fail("hosted normal shutdown did not run constructor/atexit:\n%s" % p.stderr)
    exit_out = os.path.join(tmp, 'shutdown_exit')
    need(build(os.path.join(EX, 'shutdown.cbind'), os.path.join(EX, 'shutdown_exit_app.obx'),
               exit_out), 'shutdown exit build')
    p = run([os.path.join(exit_out, 'app')])
    if p.returncode != 7 or 'before-exit' not in p.stdout or 'cstdio-flush' not in p.stdout:
        raise Fail("hosted exit(7) did not preserve status/flush: %d %r %r"
                   % (p.returncode, p.stdout, p.stderr))
    if 'atexit-ran' not in p.stderr:
        raise Fail("hosted exit(7) did not run atexit")
    leak_out = os.path.join(tmp, 'shutdown_leak')
    need(build(os.path.join(EX, 'shutdown.cbind'), os.path.join(EX, 'shutdown_leak_app.obx'),
               leak_out), 'shutdown leak build')
    p = run([os.path.join(leak_out, 'app')], env={'OBO_CHECK': '1'})
    if p.returncode != 8 or 'file descriptors leaked' not in p.stderr or 'atexit-ran' not in p.stderr:
        raise Fail("hosted OBO_CHECK failure did not preserve exit 8/atexit: %d %r"
                   % (p.returncode, p.stderr))


def negative(tmp, scalar_out):
    spec0 = json.load(open(os.path.join(EX, 'scalar.cbind')))
    spec0['sources'] = [os.path.join(EX, 'scalar.c'), os.path.join(EX, 'scalar_abi.S')]
    spec0['headers'] = [os.path.join(EX, 'scalar.h')]

    def write_spec(name, spec):
        path = os.path.join(tmp, name + '.cbind')
        json.dump(spec, open(path, 'w'))
        return path

    # wrong direct arity and extra arguments, both with --native-relocs
    for expr, label in (('__cinterop("add", 1)', 'missing'), ('__cinterop("add", 1, 2, 3)', 'extra')):
        path = os.path.join(tmp, 'direct_%s.obx' % label)
        open(path, 'w').write('module app\n\nfn main() { println(%s) }\n' % expr)
        p = build(os.path.join(EX, 'scalar.cbind'), path, os.path.join(tmp, 'direct_' + label))
        if p.returncode == 0 or "spec says 2" not in (p.stderr + p.stdout):
            raise Fail("direct %s arity was not rejected:\n%s" % (label, p.stderr + p.stdout))
    path = os.path.join(tmp, 'direct_sub.obx')
    open(path, 'w').write('module app\n\nfn main() { println(__cinterop("sum3", 1, 2)) }\n')
    p = build(os.path.join(EX, 'scalar.cbind'), path, os.path.join(tmp, 'direct_sub'))
    if p.returncode == 0 or "spec says 3" not in (p.stderr + p.stdout):
        raise Fail("incompatible symbol substitution was not rejected:\n%s" % (p.stderr + p.stdout))

    # a sidecar symbol substitution that changes the required arity is caught
    import importlib.util
    mspec = importlib.util.spec_from_file_location('cinterop_tool', TOOLPY)
    tool = importlib.util.module_from_spec(mspec)
    mspec.loader.exec_module(tool)
    spec = json.load(open(os.path.join(EX, 'scalar.cbind')))
    relocs = tool.read_relocs(os.path.join(scalar_out, 'native.relocs'))
    for r in relocs:
        if r['sym'] == 'add':
            r['sym'] = 'sum3'
            break
    try:
        tool.validate_calls(relocs, spec)
    except tool.ToolError as e:
        if 'spec says 3' not in str(e):
            raise Fail("unexpected sidecar substitution error: %s" % e)
    else:
        raise Fail("incompatible sidecar symbol substitution was not rejected")

    # direct builtin without the sidecar flag remains refused
    direct = os.path.join(tmp, 'no_flag.obx')
    open(direct, 'w').write('module direct\n\nfn main() { println(__cinterop("add", 1, 2)) }\n')
    p = run([OBO, direct, '-o', os.path.join(tmp, 'no_flag'), '--rt', os.path.dirname(OBO)])
    if p.returncode == 0 or 'native-relocs' not in (p.stderr + p.stdout):
        raise Fail("__cinterop without --native-relocs was not refused")

    # i32 arguments are rejected; i32 returns remain supported and tested above
    bad = dict(spec0)
    bad['module'] = 'bad32'
    bad['functions'] = [{'name': 'bad32', 'symbol': 'bad32', 'args': ['i32'],
                         'ret': 'i64', 'effects': ['impure']}]
    p = build(write_spec('bad32', bad), os.path.join(EX, 'scalar_app.obx'), os.path.join(tmp, 'bad32'))
    if p.returncode == 0 or 'i32 arguments' not in (p.stderr + p.stdout):
        raise Fail("i32 argument was not rejected")

    # Source float types are outside the native contract.
    bad = dict(spec0)
    bad['module'] = 'floatsrc'
    bad['functions'] = [{'name': 'floatsrc', 'symbol': 'floatsrc', 'args': ['i64'],
                         'ret': 'float', 'effects': ['impure']}]
    p = build(write_spec('floatsrc', bad), os.path.join(EX, 'scalar_app.obx'),
              os.path.join(tmp, 'floatsrc'))
    if p.returncode == 0 or 'ret must be i32 or i64' not in (p.stderr + p.stdout):
        raise Fail("a float native result was not rejected")
    bad = dict(spec0)
    bad['module'] = 'floatarg'
    bad['functions'] = [{'name': 'floatarg', 'symbol': 'floatarg', 'args': ['float'],
                         'ret': 'i64', 'effects': ['impure']}]
    p = build(write_spec('floatarg', bad), os.path.join(EX, 'scalar_app.obx'),
              os.path.join(tmp, 'floatarg'))
    if p.returncode == 0 or 'args must be i64' not in (p.stderr + p.stdout):
        raise Fail("a float native argument was not rejected")
    path = os.path.join(tmp, 'direct_float.obx')
    open(path, 'w').write('module app\n\nfn main() { println(__cinterop("add", 1.5f, 2)) }\n')
    p = build(os.path.join(EX, 'scalar.cbind'), path, os.path.join(tmp, 'direct_float'))
    if p.returncode == 0 or 'arguments must be int' not in (p.stderr + p.stdout):
        raise Fail("a direct float native argument was not rejected")

    # no header and no explicit declaration is not an unchecked mode
    bad = dict(spec0)
    bad['module'] = 'nosig'
    bad['sources'] = [os.path.join(EX, 'scalar.c')]
    bad['headers'] = []
    p = build(write_spec('nosig', bad), os.path.join(EX, 'scalar_app.obx'), os.path.join(tmp, 'nosig'))
    if p.returncode == 0 or 'header or explicit declaration' not in (p.stderr + p.stdout):
        raise Fail("a binding without a header/declaration was not rejected")

    # duplicate C symbol
    bad = dict(spec0)
    bad['module'] = 'dup'
    first = dict(spec0['functions'][1])
    second = dict(first)
    second['name'] = 'identity_copy'
    bad['functions'] = [first, second]
    p = build(write_spec('dup', bad), os.path.join(EX, 'scalar_app.obx'), os.path.join(tmp, 'dup'))
    if p.returncode == 0 or 'duplicate C symbol' not in (p.stderr + p.stdout):
        raise Fail("duplicate C symbol was not rejected")

    # variadic declaration fails the C signature check
    bad = dict(spec0)
    bad['module'] = 'var'
    bad['sources'] = []
    bad['headers'] = []
    bad['declarations'] = ['extern long vfun(long, ...);']
    bad['functions'] = [{'name': 'vfun', 'symbol': 'vfun', 'args': ['i64', 'i64'],
                         'ret': 'i64', 'effects': ['impure']}]
    p = build(write_spec('var', bad), os.path.join(EX, 'scalar_app.obx'), os.path.join(tmp, 'var'))
    if p.returncode == 0 or 'incompatible' not in (p.stderr + p.stdout):
        raise Fail("variadic declaration was not rejected by the C signature check")

    # wrong arity against a header
    bad = json.loads(json.dumps(spec0))
    bad['module'] = 'arity'
    for f in bad['functions']:
        if f['name'] == 'add':
            f['args'] = ['i64']
    p = build(write_spec('arity', bad), os.path.join(EX, 'scalar_app.obx'), os.path.join(tmp, 'arity'))
    if p.returncode == 0 or 'incompatible' not in (p.stderr + p.stdout):
        raise Fail("header arity mismatch was not rejected")

    # platform mismatch
    bad = dict(spec0)
    bad['platform'] = 'aarch64-linux-gnu'
    p = build(write_spec('platform', bad), os.path.join(EX, 'scalar_app.obx'), os.path.join(tmp, 'platform'))
    if p.returncode == 0 or 'platform' not in (p.stderr + p.stdout):
        raise Fail("platform mismatch was not rejected")

    # missing definition: declaration is checked, then the link must fail
    bad = dict(spec0)
    bad['module'] = 'missing'
    bad['sources'] = []
    bad['headers'] = []
    bad['declarations'] = ['extern long missing_native(long, long);']
    bad['functions'] = [{'name': 'missing_native', 'symbol': 'missing_native',
                         'args': ['i64', 'i64'], 'ret': 'i64', 'effects': ['impure']}]
    missing_app = os.path.join(tmp, 'missing_app.obx')
    open(missing_app, 'w').write('module app\n\nimport "missing.obx"\n\n'
                                 'fn main() { println(missing::missing_native(1, 2)) }\n')
    p = build(write_spec('missing', bad), missing_app, os.path.join(tmp, 'missing'))
    if p.returncode == 0 or 'undefined reference' not in (p.stderr + p.stdout):
        raise Fail("missing native definition did not fail the link:\n%s" % (p.stderr + p.stdout))

    # --check cannot claim a native module was checked without its typed sidecar
    checksym = os.path.join(tmp, 'checksym')
    os.makedirs(checksym)
    check = run([OBO, '--check', os.path.join(scalar_out, 'hello.obx'),
                 '--rt', os.path.dirname(OBO), '--sym-dir', checksym])
    if check.returncode == 0 or 'not supported in --check' not in (check.stderr + check.stdout):
        raise Fail("--check accepted a native module without its sidecar")

    # conservative native effects: a reference into a global cannot survive a call
    effect = os.path.join(tmp, 'effect_error.obx')
    open(effect, 'w').write(
        'module app\n\nimport "hello.obx"\n\nvar g = new []int(1)\n\n'
        'fn main() {\n    let r: &[]int = &g\n    let n = hello::zero()\n'
        '    println(len(r))\n}\n')
    p = run([OBO, effect, '-o', os.path.join(tmp, 'effect_error'),
             '--rt', os.path.dirname(OBO), '-I', scalar_out,
             '--sym-dir', os.path.join(scalar_out, 'sym'),
             '--native-relocs', os.path.join(tmp, 'effect.relocs')])
    if p.returncode == 0 or 'may point to freed memory' not in (p.stderr + p.stdout):
        raise Fail("native call did not conservatively invalidate a reference:\n%s"
                   % (p.stderr + p.stdout))

    # provenance negatives on a copy of a build made from a copied example tree
    prov_ex = os.path.join(tmp, 'prov_examples')
    shutil.copytree(EX, prov_ex)
    prov = os.path.join(tmp, 'prov')
    need(build(os.path.join(prov_ex, 'scalar.cbind'), os.path.join(prov_ex, 'scalar_app.obx'), prov), 'provenance build')
    for label, target, action in (
        ('header', os.path.join(prov_ex, 'scalar.h'), 'edit'),
        ('relocs', os.path.join(prov, 'native.relocs'), 'delete'),
        ('relocs_edit', os.path.join(prov, 'native.relocs'), 'edit'),
        ('wrappersym', os.path.join(prov, 'sym', 'hello.sym'), 'delete'),
        ('nativeobj', os.path.join(prov, 'native.o'), 'edit'),
    ):
        copy = os.path.join(tmp, 'prov_' + label)
        shutil.copytree(prov, copy)
        # the copied tree's manifest paths point at prov; point them at the copy
        mpath = os.path.join(copy, 'manifest.json')
        m = json.load(open(mpath))
        for k, rel in list(m['paths'].items()):
            m['paths'][k] = os.path.join(copy, rel) if not os.path.isabs(rel) else rel
        json.dump(m, open(mpath, 'w'))
        if label == 'header':
            path = os.path.join(prov_ex, 'scalar.h')
            orig = open(path).read()
            open(path, 'w').write(orig.replace('long add(long a, long b);', 'long add(long a);'))
            try:
                p = verify(copy)
            finally:
                open(path, 'w').write(orig)
        else:
            target_copy = os.path.join(copy, os.path.relpath(target, prov))
            if action == 'edit':
                with open(target_copy, 'ab') as f:
                    f.write(b'x')
            else:
                os.unlink(target_copy)
            p = verify(copy)
        if p.returncode == 0:
            raise Fail("provenance negative %s was not rejected" % label)

    # manifest metadata tamper
    copy = os.path.join(tmp, 'prov_meta')
    shutil.copytree(prov, copy)
    mpath = os.path.join(copy, 'manifest.json')
    m = json.load(open(mpath))
    for k, rel in list(m['paths'].items()):
        m['paths'][k] = os.path.join(copy, rel) if not os.path.isabs(rel) else rel
    m['platform'] = 'aarch64-linux-gnu'
    json.dump(m, open(mpath, 'w'))
    if verify(copy).returncode == 0:
        raise Fail("manifest metadata tamper was not rejected")
    # A consistent rewrite of symbols+thunks must still be rejected against the
    # hashed spec, not just against its own metadata.
    copy = os.path.join(tmp, 'prov_symbols')
    shutil.copytree(prov, copy)
    mpath = os.path.join(copy, 'manifest.json')
    m = json.load(open(mpath))
    for k, rel in list(m['paths'].items()):
        m['paths'][k] = os.path.join(copy, rel) if not os.path.isabs(rel) else rel
    m['symbols'][0]['symbol'] = 'evil'
    m['expected_thunks'] = ['obo_native_evil'] + m['expected_thunks'][1:]
    json.dump(m, open(mpath, 'w'))
    if verify(copy).returncode == 0:
        raise Fail("manifest symbol/thunk rewrite was not rejected against the spec")
    # Host/start objects are required build roles, not optional extras.
    copy = os.path.join(tmp, 'prov_hostrole')
    shutil.copytree(prov, copy)
    mpath = os.path.join(copy, 'manifest.json')
    m = json.load(open(mpath))
    for k, rel in list(m['paths'].items()):
        m['paths'][k] = os.path.join(copy, rel) if not os.path.isabs(rel) else rel
    m['files'].pop('host_object')
    m['paths'].pop('host_object')
    json.dump(m, open(mpath, 'w'))
    if verify(copy).returncode == 0:
        raise Fail("manifest missing required host_object role was not rejected")




def pq(tmp):
    if not libpq_present():
        libpq_skip('libpq version probe')
        return
    out = os.path.join(tmp, 'pq')
    p = build(os.path.join(EX, 'pq.cbind'), os.path.join(EX, 'pq_app.obx'), out)
    if p.returncode != 0:
        raise Fail("libpq is present but the probe build failed:\n%s" % (p.stderr or p.stdout))
    p = need(run([os.path.join(out, 'app')], env=dict(os.environ, OBO_CHECK='1')), 'pq run')
    if not p.stdout.startswith('PQlibVersion=') or not p.stdout.strip().split('=')[1].isdigit():
        raise Fail("unexpected pq output %r" % p.stdout)
    ldd = need(run(['ldd', os.path.join(out, 'app')]), 'ldd')
    if 'libpq.so.5' not in ldd.stdout:
        raise Fail("final pq artifact does not depend on libpq.so.5")
    m = json.load(open(os.path.join(out, 'manifest.json')))
    if m.get('signature_check') != 'declarations-only':
        raise Fail("pq signature_check is %r, expected declarations-only" % m.get('signature_check'))
    need(verify(out), 'pq manifest verify')
    print('libpq: %s' % p.stdout.strip())


def template(tmp):
    tdir = os.path.join(TOOL, 'skill', 'templates')
    out = os.path.join(tmp, 'template')
    p = build(os.path.join(tdir, 'library.cbind'), os.path.join(tdir, 'app.obx'), out)
    if p.returncode != 0:
        raise Fail("template build failed:\n%s" % (p.stderr or p.stdout))
    p = need(run([os.path.join(out, 'app')]), 'template run')
    if 'byte0=0' not in p.stdout or 'live=0' not in p.stdout:
        raise Fail("template output mismatch:\n%s" % p.stdout)
    need(verify(out), 'template manifest verify')
    print('template dry-run: ok')


def timeout_parse(tmp):
    if not libpq_present():
        libpq_skip('libpq timeout parser')
        return
    exe = os.path.join(tmp, 'timeout_parse_test')
    cc = os.environ.get('CC', 'gcc')
    need(run([cc, '-O2', '-I', os.path.join(TOOL, 'toolkit'), '-o', exe,
              os.path.join(HERE, 'timeout_parse_test.c'),
              os.path.join(TOOL, 'toolkit', 'libpq_conninfo.c'),
              '-l:libpq.so.5']), 'timeout parse C build')
    p = need(run([exe]), 'timeout parse C run')
    if 'timeout parse tests ok' not in p.stdout:
        raise Fail("timeout parse C tests failed:\n%s" % p.stdout)
    print('libpq timeout parser: ok')


def libpq_result(tmp):
    if not libpq_present():
        libpq_skip('libpq result probe')
        return
    out = os.path.join(tmp, 'libpq_result')
    p = build(os.path.join(EX, 'libpq.cbind'), os.path.join(EX, 'libpq_probe_app.obx'), out)
    if p.returncode != 0:
        raise Fail("libpq result probe build failed:\n%s" % (p.stderr or p.stdout))
    p = need(run([os.path.join(out, 'app')], env=dict(os.environ, OBO_CHECK='1')),
             'libpq result probe run')
    for line in ('PQlibVersion=', 'empty_status=2', 'empty_rows=0',
                 'empty_fields=0', 'empty_closed=true'):
        if line not in p.stdout:
            raise Fail("libpq result probe output missing %r:\n%s" % (line, p.stdout))
    ldd = need(run(['ldd', os.path.join(out, 'app')]), 'libpq result ldd')
    if 'libpq.so.5' not in ldd.stdout:
        raise Fail("libpq result probe does not depend on libpq.so.5")
    need(verify(out), 'libpq result manifest verify')
    m = json.load(open(os.path.join(out, 'manifest.json')))
    if m.get('signature_check') != 'header':
        raise Fail("libpq signature_check is %r, expected header" % m.get('signature_check'))
    print('libpq result ownership: ok')

def fake_adapter(tmp):
    """Edge cases of the v2 libpq wrapper through the fake ABI: a dropped Rows,
    a missing Connection close, use-after-close, NUL rejection, an explicit
    connect_timeout, and a NULL PQmakeEmptyPGresult.

    The full fake app and its counter report run in libpq_fake(); this function
    avoids duplicating that build. OBO_CHECK=1 turns the leaks into the census.
    """
    # A dropped Rows leaves its owned Res counted; the Connection is closed.
    rlout = os.path.join(tmp, 'fake_v2_rows_leak')
    p = build(os.path.join(EX, 'libpq_fake.cbind'),
              os.path.join(EX, 'libpq_rows_leak_app.obx'), rlout)
    if p.returncode != 0:
        raise Fail("fake libpq Rows-leak build failed:\n%s" % (p.stderr or p.stdout))
    p = run([os.path.join(rlout, 'app')],
            env=dict(os.environ, OBO_CHECK='1', FAKE_REPORT='1'))
    if p.returncode != 8 or 'foreign handles leaked: Res 1' not in p.stderr:
        raise Fail("dropped Rows census: want exit 8 Res 1, got %d:\n%s"
                   % (p.returncode, p.stderr))
    if 'clear=0' not in p.stderr or 'finish=1' not in p.stderr:
        raise Fail("a dropped Rows was cleared or its Connection left open:\n%s" % p.stderr)

    # A missing close leaves the Connection counted and releases nothing.
    leakout = os.path.join(tmp, 'fake_v2_leak')
    p = build(os.path.join(EX, 'libpq_fake.cbind'), os.path.join(EX, 'libpq_leak_app.obx'), leakout)
    if p.returncode != 0:
        raise Fail("fake libpq v2 leak build failed:\n%s" % (p.stderr or p.stdout))
    p = run([os.path.join(leakout, 'app')],
            env=dict(os.environ, OBO_CHECK='1', FAKE_REPORT='1'))
    if p.returncode != 8 or 'foreign handles leaked: Connection 1' not in p.stderr:
        raise Fail("fake libpq missing-close census: want exit 8 Connection 1, got %d:\n%s"
                   % (p.returncode, p.stderr))
    if 'finish=0' not in p.stderr:
        raise Fail("a dropped Connection was released:\n%s" % p.stderr)

    # A second close is ok(false), and a native borrow after close exits 7.
    uacout = os.path.join(tmp, 'fake_v2_uac')
    p = build(os.path.join(EX, 'libpq_fake.cbind'), os.path.join(EX, 'libpq_uac_app.obx'), uacout)
    if p.returncode != 0:
        raise Fail("fake libpq v2 use-after-close build failed:\n%s" % (p.stderr or p.stdout))
    p = run([os.path.join(uacout, 'app')], env=dict(os.environ, OBO_CHECK='1'))
    # NUL rejection for the DSN, SQL and text values; binary NUL is data.
    nulout = os.path.join(tmp, 'fake_v2_nul')
    p = build(os.path.join(EX, 'libpq_fake.cbind'),
              os.path.join(EX, 'libpq_nul_app.obx'), nulout)
    if p.returncode != 0:
        raise Fail("fake libpq NUL build failed:\n%s" % (p.stderr or p.stdout))
    p = need(run([os.path.join(nulout, 'app')], env=dict(os.environ, OBO_CHECK='1')),
             'fake libpq NUL run')
    for line in ('dsn_nul=2 connection string contains a NUL byte',
                 'sql_nul=2 SQL text contains an embedded NUL',
                 'text_nul=2 text parameter contains an embedded NUL',
                 'binary_nul_ok=true', 'conn_closed=true'):
        if line not in p.stdout:
            raise Fail("fake libpq NUL output missing %r:\n%s" % (line, p.stdout))

    # An explicit connect_timeout is not given a second one.
    tout = os.path.join(tmp, 'fake_v2_timeout')
    p = build(os.path.join(EX, 'libpq_fake.cbind'),
              os.path.join(EX, 'libpq_timeout_app.obx'), tout)
    if p.returncode != 0:
        raise Fail("fake libpq timeout build failed:\n%s" % (p.stderr or p.stdout))
    p = need(run([os.path.join(tout, 'app')], env=dict(os.environ, FAKE_REPORT='1')),
             'fake libpq timeout run')
    if 'timeout_open=true' not in p.stdout:
        raise Fail("fake libpq timeout app did not connect:\n%s" % p.stdout)
    if 'conninfo=fake-ok connect_timeout=3' not in p.stderr:
        raise Fail("explicit connect_timeout was not preserved:\n%s" % p.stderr)
    if 'connect_timeout=3 connect_timeout=5' in p.stderr:
        raise Fail("a second default timeout was appended:\n%s" % p.stderr)

    # PQmakeEmptyPGresult returning NULL is an own contract violation: exit 11.
    enout = os.path.join(tmp, 'fake_v2_empty_null')
    p = build(os.path.join(EX, 'libpq_fake.cbind'),
              os.path.join(EX, 'libpq_empty_null_app.obx'), enout)
    if p.returncode != 0:
        raise Fail("fake libpq empty-NULL build failed:\n%s" % (p.stderr or p.stdout))
    p = run([os.path.join(enout, 'app')], env=dict(os.environ, FAKE_EMPTY_NULL='1'))
    if p.returncode != 11 or 'own returned NULL' not in p.stderr:
        raise Fail("NULL PQmakeEmptyPGresult: want exit 11, got %d:\n%s"
                   % (p.returncode, p.stderr))
    print('fake libpq edge cases: ok (Rows/Connection census, use-after-close, NUL, timeout, own NULL)')

def libpq_fake(tmp):
    out = os.path.join(tmp, 'fake')
    p = build(os.path.join(EX, 'libpq_fake.cbind'), os.path.join(EX, 'libpq_fake_app.obx'), out)
    if p.returncode != 0:
        raise Fail("fake libpq app build failed:\n%s" % (p.stderr or p.stdout))
    p = need(run([os.path.join(out, 'app')], env=dict(os.environ, FAKE_REPORT='1')),
             'fake libpq app run')
    for line in ('status=2', 't=hello', 'n_null=true', 'n_len=0', 'b_len=3',
                 'b0=0 b1=255 b2=65', 'num=1.234567890123456789',
                 'num_type=1700 num_mod=-1', 'exact_ok=true exact_len=8',
                 'fmt=0', 'bin_fmt=1', 'null_value=1 SQL NULL',
                 'overcap_error=2 value is larger than the 16 MiB limit',
                 'num_name=num', 'error_len=17',
                 'closed_null=false', 'closed_len=-1', 'closed_ftype=0',
                 'closed_fmod=-1', 'closed_fname=2 rows are closed',
                 'bounds_null=false', 'bounds_len=-1',
                 'bounds_ftype=0', 'bounds_fmod=-1',
                 'bounds_value=2 row or column is out of range',
                 'bounds_fname=2 column is out of range',
                 'rows_closed=true', 'closed_status=-1',
                 'closed_value=2 rows are closed',
                 'error_status=7', 'error_closed=true',
                 'conn_closed=true', 'conn_again=false', 'connect_failed=1',
                 'connect_failed_msg=fake connection failure'):
        if line not in p.stdout:
            raise Fail("fake libpq output missing %r:\n%s" % (line, p.stdout))
    for line in ('conns=0', 'results=0', 'finish=2', 'clear=6', 'exec=5', 'empty=1',
                 'misaligned=0', 'binary_fmt=1', 'text_overrun=0', 'oob=0',
                 'conninfo=fake-fail connect_timeout=5'):
        if line not in p.stderr:
            raise Fail("fake libpq counters missing %r:\n%s" % (line, p.stderr))
    need(verify(out), 'fake libpq manifest verify')
    ldd = need(run(['ldd', os.path.join(out, 'app')]), 'fake libpq ldd')
    if 'libpq.so.5' in ldd.stdout:
        raise Fail("fake libpq app unexpectedly links the real libpq")
    leakout = os.path.join(tmp, 'fake_leak')
    p = build(os.path.join(EX, 'libpq_fake.cbind'), os.path.join(EX, 'libpq_leak_app.obx'), leakout)
    if p.returncode != 0:
        raise Fail("fake libpq leak app build failed:\n%s" % (p.stderr or p.stdout))
    p = run([os.path.join(leakout, 'app')],
            env=dict(os.environ, OBO_CHECK='1', FAKE_REPORT='1'))
    if p.returncode != 8 or 'foreign handles leaked: Connection 1' not in p.stderr:
        raise Fail("fake libpq leak app did not expose the missing close:\n%s%s"
                   % (p.stdout, p.stderr))
    deferout = os.path.join(tmp, 'fake_defer')
    p = build(os.path.join(EX, 'libpq_fake.cbind'), os.path.join(EX, 'libpq_defer_app.obx'), deferout)
    if p.returncode != 0:
        raise Fail("fake libpq defer app build failed:\n%s" % (p.stderr or p.stdout))
    p = need(run([os.path.join(deferout, 'app')], env=dict(os.environ, FAKE_REPORT='1')),
             'fake libpq defer app run')
    for line in ('query_ok=true', 'early_error=7', 'nested_ok=true', 'loop_closes=3'):
        if line not in p.stdout:
            raise Fail("fake libpq defer output missing %r:\n%s" % (line, p.stdout))
    for line in ('conns=0', 'results=0', 'finish=7', 'clear=1', 'misaligned=0', 'text_overrun=0'):
        if line not in p.stderr:
            raise Fail("fake libpq defer counters missing %r:\n%s" % (line, p.stderr))
    print('fake libpq wrapper: ok (explicit defer cleanup and the foreign census)')
    outlives = os.path.join(tmp, 'fake_outlives')
    p = build(os.path.join(EX, 'libpq_fake.cbind'), os.path.join(EX, 'libpq_outlives_app.obx'), outlives)
    if p.returncode != 0:
        raise Fail("fake libpq outlives app build failed:\n%s" % (p.stderr or p.stdout))
    p = need(run([os.path.join(outlives, 'app')], env=dict(os.environ, FAKE_REPORT='1')),
             'fake libpq outlives app run')
    for line in ('result_after_conn_close=1.234567890123456789', 'live_result_outlives_conn=ok'):
        if line not in p.stdout:
            raise Fail("fake libpq outlives output missing %r:\n%s" % (line, p.stdout))
    for line in ('conns=0', 'results=0', 'finish=1', 'clear=1', 'misaligned=0', 'text_overrun=0'):
        if line not in p.stderr:
            raise Fail("fake libpq outlives counters missing %r:\n%s" % (line, p.stderr))
    print('fake libpq result-outlives-connection: ok')

def libpq_conninfo(tmp):
    """Invalid conninfo returns libpq's copied diagnostic from the new helper.

    No server is contacted: libpq only parses the string. The fake ABI always
    runs; the real libpq parser runs too when libpq.so.5 is present.
    """
    variants = [('fake', 'libpq_fake.cbind')]
    if libpq_present():
        variants.append(('real', 'libpq.cbind'))
    else:
        libpq_skip('libpq conninfo diagnostic (real parser)')
    for label, cbind in variants:
        out = os.path.join(tmp, 'conninfo_' + label)
        p = build(os.path.join(EX, cbind), os.path.join(EX, 'libpq_conninfo_app.obx'), out)
        if p.returncode != 0:
            raise Fail("%s conninfo app build failed:\n%s" % (label, p.stderr or p.stdout))
        p = need(run([os.path.join(out, 'app')], env=dict(os.environ, OBO_CHECK='1')),
                 '%s conninfo app run' % label)
        for line in ('conninfo_error_code=2',
                     'conninfo_error=unterminated quoted string in connection info string'):
            if line not in p.stdout:
                raise Fail("%s conninfo output missing %r:\n%s" % (label, line, p.stdout))
    print('libpq conninfo diagnostic: ok (%s)' % ' and '.join(v[0] for v in variants))

def libpq_limits(tmp):
    """Wrapper limits are enforced before any C call, with specific messages.

    Uses the fake ABI only, so no server is needed. 33 parameters, 1 MiB + 1
    bytes of SQL, a 16 MiB + 1 value and a 16 MiB + 1 DSN are rejected; 32
    parameters, exact 1 MiB SQL and a 16 MiB value reach the C call.
    """
    out = os.path.join(tmp, 'libpq_limits')
    p = build(os.path.join(EX, 'libpq_fake.cbind'), os.path.join(EX, 'libpq_limits_app.obx'), out)
    if p.returncode != 0:
        raise Fail("libpq limits app build failed:\n%s" % (p.stderr or p.stdout))
    p = run([os.path.join(out, 'app')], env=dict(os.environ, OBO_CHECK='1'))
    if p.returncode != 0:
        raise Fail("libpq limits app failed: %d\n%s%s" % (p.returncode, p.stdout, p.stderr))
    for line in ('params33_error=too many query parameters: the limit is 32 (PQ_MAX_PARAMS) (2)',
                 'params32_status=2', 'params32_closed=true',
                 'sql_error=SQL text is larger than the 1 MiB limit (PQ_MAX_SQL) (2)',
                 'value_16mib=accepted status=2',
                 'value_16mib_plus1_error=query parameter is larger than the 16 MiB limit (2)',
                 'dsn_error=connection string is too large (2)',
                 'conn_closed=true'):
        if line not in p.stdout:
            raise Fail("libpq limits output missing %r:\n%s" % (line, p.stdout))
    # Exactly 1 MiB of SQL is not the limit error. The fake cannot run a
    # statement this long, so its own failure is expected; only the limit
    # message is excluded.
    at = [l for l in p.stdout.splitlines() if l.startswith('sql_at_limit=')]
    if len(at) != 1 or 'PQ_MAX_SQL' in at[0] or 'larger than the 1 MiB' in at[0]:
        raise Fail("exact 1 MiB SQL was treated as over the limit: %r" % at)
    print('libpq limits: ok (33 params, oversized SQL/value/DSN rejected; '
          'exact 1 MiB SQL and 16 MiB value accepted)')

def libpq_errors(tmp):
    """Specific messages for the wrapper's allocation failures, plus the closed
    connection trap and the double-close result. Fake ABI only.
    """
    out = os.path.join(tmp, 'libpq_errors')
    p = build(os.path.join(EX, 'libpq_fake.cbind'), os.path.join(EX, 'libpq_errors_app.obx'), out)
    if p.returncode != 0:
        raise Fail("libpq errors app build failed:\n%s" % (p.stderr or p.stdout))
    p = run([os.path.join(out, 'app')], env=dict(os.environ, OBO_CHECK='1'))
    if p.returncode != 0:
        raise Fail("libpq errors app failed: %d\n%s%s" % (p.returncode, p.stdout, p.stderr))
    for line in ('null_connect_error=2 libpq returned no connection object (PQconnectdb returned NULL)',
                 'alloc_error=2 libpq returned no result and no error message',
                 'conn_closed=true', 'conn_open=false'):
        if line not in p.stdout:
            raise Fail("libpq errors output missing %r:\n%s" % (line, p.stdout))

    uac = os.path.join(tmp, 'libpq_uac')
    p = build(os.path.join(EX, 'libpq_fake.cbind'), os.path.join(EX, 'libpq_uac_app.obx'), uac)
    if p.returncode != 0:
        raise Fail("libpq use-after-close build failed:\n%s" % (p.stderr or p.stdout))
    p = run([os.path.join(uac, 'app')], env=dict(os.environ, OBO_CHECK='1'))
    if p.returncode != 7 or 'foreign handle used after close' not in p.stderr:
        raise Fail("libpq use-after-close: want exit 7, got %d:\n%s" % (p.returncode, p.stderr))
    print('libpq errors: ok (connect/exec allocation failures, closed trap, double close)')

def libpq_constants():
    """The wrapper's limits are the reviewed v2 values, with no adapter mirror."""
    wrapper = os.path.join(EX, 'libpq.obx')
    obx = open(wrapper).read()

    def obo_value(name):
        m = re.search(r'^pub const %s = (\d+)$' % name, obx, re.M)
        if not m:
            raise Fail("libpq.obx does not define pub const %s" % name)
        return int(m.group(1))

    checks = [('PQ_MAX_PARAMS', 32), ('PQ_MAX_SQL', 1048576),
              ('PQ_MAX_DSN', 16777216), ('PQ_MAX_VALUE', 16777216)]
    for name, want in checks:
        got = obo_value(name)
        if got != want:
            raise Fail("libpq.obx %s is %d, expected %d" % (name, got, want))
    if obx.count('16777216') != 2:
        raise Fail("libpq.obx must spell 16 MiB exactly once for DSN and once for values")
    print('libpq limit constants: ok (PQ_MAX_PARAMS, PQ_MAX_SQL, PQ_MAX_DSN, PQ_MAX_VALUE)')

def libpq_header_mismatch(tmp):
    """A spec declaration that disagrees with libpq_decls.h fails the signature
    check before any link. A control copy of the same spec builds."""
    spec = json.load(open(os.path.join(EX, 'libpq_fake.cbind')))
    for key in ('sources', 'headers'):
        spec[key] = [os.path.normpath(os.path.join(EX, p)) for p in spec.get(key, [])]
    spec['wrapper'] = os.path.join(EX, spec['wrapper'])
    app = os.path.join(EX, 'libpq_limits_app.obx')

    control = os.path.join(tmp, 'libpq_header_control.cbind')
    json.dump(spec, open(control, 'w'))
    out = os.path.join(tmp, 'libpq_header_control')
    p = build(control, app, out)
    if p.returncode != 0:
        raise Fail("control copy of the libpq spec did not build:\n%s" % (p.stderr + p.stdout))

    # PQlibVersion returns int in libpq_decls.h; declaring long/i64 in the
    # binding is internally consistent but incompatible with the header.
    bad = json.loads(json.dumps(spec))
    for f in bad['functions']:
        if f['name'] == 'lib_version_native':
            f['ret'] = 'i64'
    mismatch = os.path.join(tmp, 'libpq_header_mismatch.cbind')
    json.dump(bad, open(mismatch, 'w'))
    out = os.path.join(tmp, 'libpq_header_mismatch')
    p = build(mismatch, app, out)
    text = p.stderr + p.stdout
    if p.returncode == 0 or 'incompatible' not in text:
        raise Fail("a spec declaration that disagrees with libpq_decls.h was not rejected:\n%s"
                   % text)
    if os.path.exists(os.path.join(out, 'app')):
        raise Fail("a mismatched declaration reached the link")
    print('libpq header mismatch: ok (declaration disagreeing with libpq_decls.h rejected before link)')

def zlib_second(tmp):
    if not zlib_present():
        print('zlib second library: skipped (header or libz.so.1 absent)')
        return
    out = os.path.join(tmp, 'zlib')
    p = build(os.path.join(EX, 'zlib.cbind'), os.path.join(EX, 'zlib_app.obx'), out)
    if p.returncode != 0:
        raise Fail("zlib second-library build failed:\n%s" % (p.stderr or p.stdout))
    p = need(run([os.path.join(out, 'app')]), 'zlib second-library run')
    for line in ('zlib-version=', 'compressed_len=', 'roundtrip_len=6',
                 'small_error=', 'bad_error=', 'live=0', 'zlib_smoke=ok'):
        if line not in p.stdout:
            raise Fail("zlib output missing %r:\n%s" % (line, p.stdout))
    ldd = need(run(['ldd', os.path.join(out, 'app')]), 'zlib ldd')
    if 'libz.so.1' not in ldd.stdout:
        raise Fail("zlib app does not link libz.so.1")
    m = json.load(open(os.path.join(out, 'manifest.json')))
    if m.get('signature_check') != 'header':
        raise Fail("zlib signature_check is %r, expected header" % m.get('signature_check'))
    need(verify(out), 'zlib manifest verify')

    exe = os.path.join(tmp, 'zlib_adapter_test')
    cc = os.environ.get('CC', 'gcc')
    need(run([cc, '-O2', '-I', os.path.join(TOOL, 'toolkit'),
              '-I', os.path.join(TOOL, 'examples'), '-o', exe,
              os.path.join(HERE, 'zlib_adapter_test.c'),
              os.path.join(TOOL, 'toolkit', 'cres.c'),
              os.path.join(TOOL, 'examples', 'zlib_adapter.c'),
              '-l:libz.so.1']), 'zlib adapter C build')
    p = need(run([exe]), 'zlib adapter C run')
    if 'zlib adapter tests ok' not in p.stdout:
        raise Fail("zlib adapter C tests failed:\n%s" % p.stdout)
    print('zlib second library: ok (real header, in-memory roundtrip)')


def sanitizer_adapters(tmp):
    cc = os.environ.get('CC', 'gcc')
    common = ['-O1', '-g', '-fsanitize=address,undefined',
              '-fno-sanitize-recover=all', '-fno-omit-frame-pointer',
              '-I', os.path.join(TOOL, 'toolkit'),
              '-I', os.path.join(TOOL, 'examples')]
    fake = os.path.join(tmp, 'fake_adapter_asan')
    need(run([cc, *common, '-o', fake,
              os.path.join(HERE, 'fake_adapter_test.c'),
              os.path.join(TOOL, 'toolkit', 'cres.c'),
              os.path.join(TOOL, 'toolkit', 'libpq_adapter.c'),
              os.path.join(HERE, 'fake_libpq.c')]), 'fake adapter ASAN/UBSAN build')
    p = need(run([fake]), 'fake adapter ASAN/UBSAN run')
    if 'fake adapter tests ok' not in p.stdout:
        raise Fail("fake adapter ASAN/UBSAN failed:\n%s" % p.stdout)
    g = os.path.join(tmp, 'guard_stack_asan')
    need(run([cc, *common, '-o', g,
              os.path.join(HERE, 'guard_stack_test.c'),
              os.path.join(TOOL, 'host_stack.c')]), 'guard stack ASAN/UBSAN build')
    p = need(run([g, 'ok']), 'guard stack ASAN/UBSAN run')
    if 'guard_stack=ok' not in p.stdout:
        raise Fail("guard stack ASAN/UBSAN failed:\n%s" % p.stdout)
    ff = os.path.join(tmp, 'foreign_fixture_asan')
    need(run([cc, *common, '-I', HERE, '-o', ff,
              os.path.join(HERE, 'foreign_fixture_test.c'),
              os.path.join(HERE, 'foreign_fixture.c')]), 'foreign fixture ASAN/UBSAN build')
    p = need(run([ff]), 'foreign fixture ASAN/UBSAN run')
    if 'foreign fixture tests ok' not in p.stdout:
        raise Fail("foreign fixture ASAN/UBSAN failed:\n%s" % p.stdout)
    if libpq_present():
        cq = os.path.join(tmp, 'libpq_conninfo_asan')
        need(run([cc, *common, '-I', os.path.join(TOOL, 'toolkit'), '-o', cq,
                  os.path.join(HERE, 'timeout_parse_test.c'),
                  os.path.join(TOOL, 'toolkit', 'libpq_conninfo.c'),
                  '-l:libpq.so.5']), 'libpq conninfo helper ASAN/UBSAN build')
        p = need(run([cq]), 'libpq conninfo helper ASAN/UBSAN run')
        if 'timeout parse tests ok' not in p.stdout:
            raise Fail("libpq conninfo helper ASAN/UBSAN failed:\n%s" % p.stdout)
    if zlib_present():
        z = os.path.join(tmp, 'zlib_adapter_asan')
        need(run([cc, *common, '-o', z,
                  os.path.join(HERE, 'zlib_adapter_test.c'),
                  os.path.join(TOOL, 'toolkit', 'cres.c'),
                  os.path.join(TOOL, 'examples', 'zlib_adapter.c'),
                  '-l:libz.so.1']), 'zlib adapter ASAN/UBSAN build')
        p = need(run([z]), 'zlib adapter ASAN/UBSAN run')
        if 'zlib adapter tests ok' not in p.stdout:
            raise Fail("zlib adapter ASAN/UBSAN failed:\n%s" % p.stdout)
    print('ASAN/UBSAN adapter tests: ok')


def libpq_live(tmp):
    dsn = os.environ.get('CINTEROP_TEST_DSN')
    if not dsn:
        print('live libpq: skipped (set CINTEROP_TEST_DSN to run the opt-in local smoke test)')
        return
    if not libpq_present():
        raise Fail("CINTEROP_TEST_DSN is set but libpq.so.5 is absent")
    out = os.path.join(tmp, 'live')
    p = build(os.path.join(EX, 'libpq.cbind'), os.path.join(EX, 'libpq_live_app.obx'), out)
    if p.returncode != 0:
        raise Fail("live libpq app build failed:\n%s" % (p.stderr or p.stdout))
    p = run([os.path.join(out, 'app'), dsn], timeout=30,
            env=dict(os.environ, OBO_CHECK='1'))
    if p.returncode != 0:
        raise Fail("live libpq smoke failed (exit %d):\n%s" % (p.returncode, p.stdout + p.stderr))
    for line in ('live_db_smoke=ok', 'exact_text=AAAAAAAA', 'null_and_empty=ok',
                 'numeric_text=1.234567890123456789 oid=1700 typmod=-1',
                 'binary_bytes=00ff41 len=3', 'sql_error_length='):
        if line not in p.stdout:
            raise Fail("live libpq output missing %r:\n%s" % (line, p.stdout))
    print('live libpq: ok (explicit opt-in DSN)')


def include_and_cflags(tmp):
    """A private include dir and a cflag must reach the signature check and the
    binding source compile with the same preprocessor state, and both must be
    recorded in the manifest."""
    root = os.path.join(tmp, 'include_cflags')
    private = os.path.join(root, 'inc', 'private')
    dep = os.path.join(root, 'inc', 'dep')
    opt = os.path.join(root, 'inc', 'opt')
    debug = os.path.join(root, 'inc', 'debug')
    proto = os.path.join(root, 'inc', 'proto')
    for path in (private, dep, opt, debug, proto):
        os.makedirs(path)
    with open(os.path.join(private, 'probe.h'), 'w') as f:
        f.write('#ifndef OBO_INCLUDE_PROBE_H\n#define OBO_INCLUDE_PROBE_H\n'
                '#include "probe_dep.h"\n#endif\n')
    with open(os.path.join(dep, 'probe_dep.h'), 'w') as f:
        f.write('#ifndef OBO_INCLUDE_PROBE_DEP_H\n#define OBO_INCLUDE_PROBE_DEP_H\n'
                '#if !defined(CINTEROP_CFLAG_PROBE)\n'
                '#error "CINTEROP_CFLAG_PROBE missing from this compile"\n'
                '#endif\n'
                '#ifdef __OPTIMIZE__\n#include "opt.h"\n#else\n#include "debug.h"\n#endif\n'
                'long probe_value(long x);\n#endif\n')
    with open(os.path.join(opt, 'opt.h'), 'w') as f:
        f.write('#ifndef OBO_INCLUDE_OPT_H\n#define OBO_INCLUDE_OPT_H\n'
                '#define PROBE_K 7\n#endif\n')
    with open(os.path.join(debug, 'debug.h'), 'w') as f:
        f.write('#ifndef OBO_INCLUDE_DEBUG_H\n#define OBO_INCLUDE_DEBUG_H\n'
                '#define PROBE_K 0\n#endif\n')
    with open(os.path.join(proto, 'proto.h'), 'w') as f:
        f.write('#ifndef OBO_INCLUDE_PROTO_H\n#define OBO_INCLUDE_PROTO_H\n'
                '#ifdef __OPTIMIZE__\n'
                'int probe_value(long x);\n'
                '#else\n'
                'long probe_value(long x);\n'
                '#endif\n#endif\n')
    with open(os.path.join(root, 'probe.c'), 'w') as f:
        f.write('#include "probe.h"\nlong probe_value(long x) { return x + PROBE_K; }\n')
    with open(os.path.join(root, 'proto.c'), 'w') as f:
        f.write('long probe_value(long x) { return x; }\n')
    with open(os.path.join(root, 'app.obx'), 'w') as f:
        f.write('module app\n\nimport "probe.obx"\n\n'
                'fn main() { println(probe::probe_value(35)) }\n')
    spec = {
        'format': 'cinterop-proto/1',
        'module': 'probe',
        'platform': 'x86_64-linux-gnu',
        'abi': 'sysv-amd64',
        'sources': ['probe.c'],
        'headers': ['probe.h'],
        'include_dirs': ['inc/private', 'inc/dep', 'inc/opt', 'inc/debug'],
        'cflags': ['-DCINTEROP_CFLAG_PROBE=1'],
        'libs': [],
        'functions': [{'name': 'probe_value', 'symbol': 'probe_value',
                       'args': ['i64'], 'ret': 'i64', 'effects': ['impure']}],
    }
    spec_path = os.path.join(root, 'probe.cbind')

    def write(s):
        with open(spec_path, 'w') as f:
            json.dump(s, f)
        return spec_path

    out = os.path.join(tmp, 'include_cflags_build')
    need(build(write(spec), os.path.join(root, 'app.obx'), out), 'include/cflags build')
    p = need(run([os.path.join(out, 'app')], env=dict(os.environ, OBO_CHECK='1')),
             'include/cflags run')
    if p.stdout.strip() != '42':
        raise Fail("include/cflags app output was %r" % p.stdout.strip())
    need(verify(out), 'include/cflags manifest verify')
    m = json.load(open(os.path.join(out, 'manifest.json')))
    if m['include_dirs'] != spec['include_dirs'] or m['cflags'] != spec['cflags']:
        raise Fail("manifest did not record include_dirs/cflags: %r %r"
                   % (m['include_dirs'], m['cflags']))
    names = {os.path.basename(d['path']) for d in m['header_deps']}
    if not {'probe.h', 'probe_dep.h', 'opt.h'}.issubset(names):
        raise Fail("manifest header_deps do not include the optimized private headers: %r"
                   % sorted(names))
    if 'debug.h' in names:
        raise Fail("dependency scan used a different __OPTIMIZE__ state than the source compile")
    for target in (os.path.join(opt, 'opt.h'), os.path.join(private, 'probe.h')):
        original = open(target).read()
        try:
            with open(target, 'a') as f:
                f.write('\n')
            if verify(out).returncode == 0:
                raise Fail("a changed private header was not rejected: %s" % target)
        finally:
            with open(target, 'w') as f:
                f.write(original)

    no_inc = dict(spec)
    no_inc.pop('include_dirs')
    p = build(write(no_inc), os.path.join(root, 'app.obx'), os.path.join(tmp, 'include_missing'))
    if p.returncode == 0 or 'probe.h' not in (p.stderr + p.stdout):
        raise Fail("a header reachable only through include_dirs was not rejected:\n%s"
                   % (p.stderr + p.stdout))
    no_flag = dict(spec)
    no_flag['cflags'] = []
    p = build(write(no_flag), os.path.join(root, 'app.obx'), os.path.join(tmp, 'cflag_missing'))
    if p.returncode == 0 or 'CINTEROP_CFLAG_PROBE' not in (p.stderr + p.stdout):
        raise Fail("a cflag missing from the signature check/source compile was not rejected:\n%s"
                   % (p.stderr + p.stdout))
    escape = dict(spec)
    escape['include_dirs'] = ['../escape']
    p = build(write(escape), os.path.join(root, 'app.obx'), os.path.join(tmp, 'include_escape'))
    if p.returncode == 0 or 'escapes the spec directory' not in (p.stderr + p.stdout):
        raise Fail("a relative include-dir escape was not rejected:\n%s" % (p.stderr + p.stdout))
    missing = dict(spec)
    missing['include_dirs'] = [os.path.join(tmp, 'no-such-include-root')]
    p = build(write(missing), os.path.join(root, 'app.obx'), os.path.join(tmp, 'include_nonexistent'))
    if p.returncode == 0 or 'include directory not found' not in (p.stderr + p.stdout):
        raise Fail("a nonexistent include root was not rejected:\n%s" % (p.stderr + p.stdout))
    outside = os.path.join(tmp, 'outside_headers')
    os.makedirs(outside)
    link = os.path.join(root, 'inc', 'link-outside')
    os.symlink(outside, link)
    symlink = dict(spec)
    symlink['include_dirs'] = ['inc/link-outside']
    p = build(write(symlink), os.path.join(root, 'app.obx'), os.path.join(tmp, 'include_symlink'))
    if p.returncode == 0 or 'resolves outside' not in (p.stderr + p.stdout):
        raise Fail("a symlink include-root escape was not rejected:\n%s" % (p.stderr + p.stdout))

    # The signature check must use the same __OPTIMIZE__ branch as the source.
    # Under -O2 this header says int, while the spec says i64; the build must
    # fail rather than accept a prototype that the source object disagrees with.
    mismatch = dict(spec)
    mismatch['sources'] = ['proto.c']
    mismatch['headers'] = ['proto.h']
    mismatch['include_dirs'] = ['inc/proto']
    p = build(write(mismatch), os.path.join(root, 'app.obx'),
              os.path.join(tmp, 'include_proto_mismatch'))
    if p.returncode == 0 or 'probe_value' not in (p.stderr + p.stdout):
        raise Fail("a __OPTIMIZE__-dependent prototype mismatch was not rejected:\n%s"
                   % (p.stderr + p.stdout))
    print('include_dirs/cflags: ok (same flags for signature check, source and dependency scan)')


def openssl_qualification(tmp):
    if not openssl_library_present():
        print('hosted OpenSSL qualification: skipped (libssl.so.3/libcrypto.so.3 absent, '
              'or CINTEROP_TEST_NO_OPENSSL=1)')
        return
    try:
        include_dirs = openssl_include_dirs()
    except Skip as e:
        print('skipped: openssl (download failed): %s' % e)
        return
    test_dir = HERE
    # Keep the generated spec and C sources in the private build directory so
    # the uncommitted spec has no absolute repository source paths. The private
    # sysroot remains an explicit absolute build input.
    src_dir = os.path.join(tmp, 'openssl-src')
    os.makedirs(src_dir)
    for name in ('openssl_qualify.c', 'openssl_qualify.h'):
        shutil.copyfile(os.path.join(test_dir, name), os.path.join(src_dir, name))
    spec = {
        'format': 'cinterop-proto/1',
        'module': 'openssl_probe',
        'platform': 'x86_64-linux-gnu',
        'abi': 'sysv-amd64',
        'sources': ['openssl_qualify.c'],
        'headers': [
            'openssl_qualify.h',
            'openssl/ssl.h', 'openssl/evp.h', 'openssl/rand.h',
            'openssl/err.h', 'openssl/provider.h', 'openssl/x509.h',
            'openssl/pem.h', 'openssl/bio.h', 'openssl/opensslv.h',
        ],
        'include_dirs': include_dirs,
        'cflags': ['-pthread'],
        'libs': ['-l:libssl.so.3', '-l:libcrypto.so.3', '-pthread'],
        'functions': [
            {'name': 'qualify', 'symbol': 'obo_openssl_qualify',
             'args': ['i64'], 'ret': 'i64', 'effects': ['impure']},
            {'name': 'version', 'symbol': 'obo_openssl_version_num',
             'args': [], 'ret': 'i64', 'effects': ['impure']},
        ],
    }
    spec_path = os.path.join(src_dir, 'openssl_qualify.cbind')
    with open(spec_path, 'w') as f:
        json.dump(spec, f)
    out = os.path.join(tmp, 'openssl')
    p = build(spec_path, os.path.join(test_dir, 'openssl_qualify_app.obx'), out)
    if p.returncode != 0:
        raise Fail("hosted OpenSSL qualification build failed:\n%s" % (p.stderr or p.stdout))
    app_path = os.path.join(out, 'app')
    blob = open(app_path, 'rb').read()
    if src_dir.encode() in blob:
        raise Fail("the final artifact embeds the private source directory path")
    p = need(run([app_path], env=dict(os.environ, OBO_CHECK='1'),
                 timeout=120), 'hosted OpenSSL qualification run')
    for line in ('openssl-qualify=ok', 'openssl-version-num=0x300',
                 'openssl-tls=1.3 alpn=http/1.1', 'openssl-post-cleanup=ok'):
        if line not in p.stdout:
            raise Fail("hosted OpenSSL output missing %r:\n%s" % (line, p.stdout))
    if 'openssl-qualify=1' not in p.stdout:
        raise Fail("the obo scalar seam did not return OpenSSL success:\n%s" % p.stdout)
    for marker in ('openssl-ctor', 'openssl-cleanup-atexit', 'openssl-atexit'):
        if marker not in p.stderr:
            raise Fail("hosted OpenSSL stderr missing %r:\n%s" % (marker, p.stderr))
    if p.stderr.index('openssl-cleanup-atexit') > p.stderr.index('openssl-atexit'):
        raise Fail("OPENSSL_cleanup did not run before the libc atexit marker:\n%s" % p.stderr)
    ldd = need(run(['ldd', os.path.join(out, 'app')]), 'openssl ldd')
    for lib in ('libssl.so.3', 'libcrypto.so.3'):
        if lib not in ldd.stdout:
            raise Fail("hosted OpenSSL app does not depend on %s" % lib)
    assert_relro_sections(app_path)
    dyn = need(run(['readelf', '-dW', app_path]), 'openssl readelf -d')
    if 'BIND_NOW' not in dyn.stdout and 'NOW' not in dyn.stdout:
        raise Fail("hosted OpenSSL app does not request immediate binding")
    need(verify(out), 'openssl manifest verify')
    m = json.load(open(os.path.join(out, 'manifest.json')))
    if m.get('signature_check') != 'header':
        raise Fail("OpenSSL signature_check is %r" % m.get('signature_check'))
    if m.get('include_dirs') != include_dirs or m.get('cflags') != ['-pthread']:
        raise Fail("OpenSSL manifest did not record include_dirs/cflags")
    deps = {os.path.basename(d['path']) for d in m['header_deps']}
    if 'ssl.h' not in deps or 'opensslv.h' not in deps or 'openssl_qualify.h' not in deps:
        raise Fail("OpenSSL manifest header_deps are incomplete: %r" % sorted(deps))
    if m.get('hardening') != {'relro': True, 'now': True, 'noexecstack': True}:
        raise Fail("OpenSSL manifest hardening record is %r" % m.get('hardening'))

    # The same C fixture under ASAN/UBSAN, separately from the fixed hosted
    # image: provider setup, the BIO handshake and cleanup must stay clean.
    asan_main = os.path.join(tmp, 'openssl_asan_main.c')
    with open(asan_main, 'w') as f:
        f.write('#include <stdio.h>\n#include "openssl_qualify.h"\n'
                'int main(void) {\n'
                '    long ok = obo_openssl_qualify(1);\n'
                '    printf("asan-qualify=%ld\\n", ok);\n'
                '    return ok == 1 ? 0 : 1;\n}\n')
    cc = os.environ.get('CC', 'gcc')
    asan_exe = os.path.join(tmp, 'openssl_qualify_asan')
    flags = ['-O1', '-g', '-fsanitize=address,undefined',
             '-fno-sanitize-recover=all', '-fno-omit-frame-pointer', '-pthread']
    for d in include_dirs:
        flags += ['-I', d]
    need(run([cc, *flags, '-I', src_dir, '-o', asan_exe,
              os.path.join(src_dir, 'openssl_qualify.c'), asan_main,
              '-l:libssl.so.3', '-l:libcrypto.so.3']), 'OpenSSL ASAN/UBSAN build')
    p = need(run([asan_exe], env=dict(os.environ,
                                      ASAN_OPTIONS='detect_leaks=1:abort_on_error=1',
                                      UBSAN_OPTIONS='halt_on_error=1'),
                 timeout=120), 'OpenSSL ASAN/UBSAN run')
    if 'asan-qualify=1' not in p.stdout or 'openssl-post-cleanup=ok' not in p.stdout:
        raise Fail("OpenSSL ASAN/UBSAN run failed:\n%s\n%s" % (p.stdout, p.stderr))
    print('hosted OpenSSL qualification: ok (TLS 1.3 memory BIO, ALPN, provider, '
          'RAND, pthread_once, TLS errno/error queue, atexit/cleanup; ASAN/UBSAN)')

def fnv1a_mask(data):
    h = 1469598103934665603
    for b in data:
        h = ((h ^ b) * 1099511628211) & ((1 << 64) - 1)
    return h & 0x7fffffffffffffff


# (obo name, args (descriptors), C symbol)
KIND_FUNCS = [
    ('bytes_hash', ['borrow_bytes@0'], 'bytes_hash'),
    ('str_hash', ['borrow_bytes@0'], 'str_hash'),
    ('bytes_probe', ['borrow_bytes@0'], 'bytes_probe'),
    ('str_probe', ['borrow_bytes@0'], 'str_probe'),
    ('cstr_len', ['borrow_cstr@0'], 'cstr_len'),
    ('rec_const', ['const:-1'], 'rec_const'),
    ('ui_echo', ['u32@0'], 'ui_echo'),
    ('i_echo', ['i32@0'], 'i_echo'),
    ('ints_sum', ['ints@0:i32', 'ints@0:len'], 'ints_sum'),
    ('ints_u_last', ['ints@0:u32', 'ints@0:len'], 'ints_u_last'),
    ('list_probe', ['nil_bytes_list@0', 'ints@0:len'], 'list_probe'),
    ('timing_probe', ['borrow_bytes@0', 'i64@1'], 'timing_probe'),
    ('scratch_live', [], 'scratch_live'),
]

KIND_WRAPPER = """module bytesfix

pub fn bytes_hash(b: &[]byte): int {
    return __cinterop("bytes_hash", "borrow_bytes@0", b)
}

pub fn str_hash(s: string): int {
    return __cinterop("str_hash", "borrow_bytes@0", s)
}

pub fn bytes_probe(b: &[]byte): int {
    return __cinterop("bytes_probe", "borrow_bytes@0", b)
}

pub fn str_probe(s: string): int {
    return __cinterop("str_probe", "borrow_bytes@0", s)
}

pub fn cstr_len(s: string): int {
    return __cinterop("cstr_len", "borrow_cstr@0", s)
}

pub fn rec_const(): int {
    return __cinterop("rec_const", "const:-1")
}

pub fn ui_echo(v: int): int {
    return __cinterop("ui_echo", "u32@0", v)
}

pub fn i_echo(v: int): int {
    return __cinterop("i_echo", "i32@0", v)
}

pub fn ints_sum(v: &[]int): int {
    return __cinterop("ints_sum", "ints@0:i32,ints@0:len", v)
}

pub fn ints_u_last(v: &[]int): int {
    return __cinterop("ints_u_last", "ints@0:u32,ints@0:len", v)
}

pub fn list_probe(v: &[]?[]byte): int {
    return __cinterop("list_probe", "nil_bytes_list@0,ints@0:len", v)
}

pub fn timing_probe(b: &[]byte, g: int): int {
    return __cinterop("timing_probe", "borrow_bytes@0,i64@1", b, g)
}

pub fn scratch_live(): int {
    return __cinterop("scratch_live")
}
"""

KIND_MAIN = """fn bytesN(n: int): []byte {
    var out = new []byte(0)
    for i in 0..n {
        append(out, byte(i % 251))
    }
    return out
}

fn main() {
    let empty = new []byte(0)
    println("empty ", bytesfix::bytes_hash(&empty))
    let mib = bytesN(1048576)
    println("mib ", bytesfix::bytes_hash(&mib))
    var one = new []byte(0)
    append(one, byte(9))
    println("one ", bytesfix::bytes_probe(&one))
    var z: string
    println("nil ", bytesfix::str_probe(z))
    println("empty_str ", bytesfix::str_probe(""))
    let e = "a\\0b"
    var eb = new []byte(0)
    append(eb, byte(97))
    append(eb, byte(0))
    append(eb, byte(98))
    println("emb ", bytesfix::bytes_hash(&eb))
    println("embs ", bytesfix::str_hash(e))
    println("embp ", bytesfix::bytes_probe(&eb))
    println("cstr ", bytesfix::cstr_len("hello"))
    println("utf8 ", bytesfix::cstr_len("héllo"))
    println("const ", bytesfix::rec_const())
    println("u32 ", bytesfix::ui_echo(3000000000))
    println("i32 ", bytesfix::i_echo(-5))
    var iv = new []int(0)
    append(iv, 1)
    append(iv, -2)
    append(iv, 3)
    println("sum ", bytesfix::ints_sum(&iv))
    var uv = new []int(0)
    append(uv, 1)
    append(uv, 3000000000)
    println("ulast ", bytesfix::ints_u_last(&uv))
    var l = new []?[]byte(0)
    append(l, nil)
    append(l, new []byte(0))
    var ab = new []byte(0)
    append(ab, byte(97))
    append(ab, byte(98))
    append(l, ab)
    println("list ", bytesfix::list_probe(&l))
    println("live ", bytesfix::scratch_live())
}
"""

KIND_TIMING = """fn grow(mut a: &[]byte): int {
    append(a, byte(2))
    return 7
}

fn main() {
    var a = new []byte(0)
    append(a, byte(1))
    println("timing ", bytesfix::timing_probe(&a, grow(&a)))
}
"""


def kinds_spec(module, funcs, wrapper='bytesfix_wrap.obx'):
    return {'format': 'cinterop-proto/1', 'module': module, 'platform': 'x86_64-linux-gnu',
            'abi': 'sysv-amd64', 'sources': ['bytes_fixture.c'], 'headers': ['bytes_fixture.h'],
            'libs': [], 'cflags': [], 'wrapper': wrapper,
            'functions': [kinds_entry(sym, args) for _, args, sym in funcs]}


def kinds_entry(sym, args):
    entry = {'name': sym, 'symbol': sym, 'args': args, 'ret': 'i64', 'effects': ['impure']}
    ct = ['void *' if (a == 'null' or a.startswith('const:')) else None for a in args]
    if any(ct):
        entry['ctypes'] = ct
    return entry


def kinds(tmp):
    """Stage (a) argument kinds: borrow_bytes, borrow_cstr, null/const, i32, u32, ints, nil_bytes_list,
    scratch freed, the borrow-timing rule, and negative spec tests (design section 9)."""
    d = os.path.join(tmp, 'kinds_src')
    os.makedirs(d)
    for name in ('bytes_fixture.c', 'bytes_fixture.h'):
        shutil.copy(os.path.join(HERE, name), os.path.join(d, name))
    open(os.path.join(d, 'bytesfix_wrap.obx'), 'w').write(KIND_WRAPPER)
    spec_path = os.path.join(d, 'bytesfix.cbind')
    json.dump(kinds_spec('bytesfix', KIND_FUNCS), open(spec_path, 'w'), indent=2)

    def app(name, body):
        ad = os.path.join(tmp, 'kapp_' + name)
        os.makedirs(ad)
        path = os.path.join(ad, 'app.obx')
        open(path, 'w').write('module app\n\nimport "bytesfix.obx"\n\n' + body)
        out = os.path.join(ad, 'out')
        return build(spec_path, path, out), out

    # Happy path: one app covering the positive cases.
    p, out = app('main', KIND_MAIN)
    need(p, 'kinds main build')
    p = need(run([os.path.join(out, 'app')], env=dict(os.environ, OBO_CHECK='1')), 'kinds main run')
    want = [
        'empty %d' % fnv1a_mask(b''),
        'mib %d' % fnv1a_mask(bytes(i % 251 for i in range(1 << 20))),
        'one 3',
        'nil 1',
        'empty_str 1',
        'emb %d' % fnv1a_mask(b'a\x00b'),
        'embs %d' % fnv1a_mask(b'a\x00b'),
        'embp 7',
        'cstr 5',
        'utf8 6',
        'const 1',
        'u32 3000000000',
        'i32 -5',
        'sum 2',
        'ulast 3000000000',
        'list 292',
        'live 0',
    ]
    got = p.stdout.splitlines()
    if got != want:
        raise Fail("kinds main output mismatch:\n got  %r\n want %r" % (got, want))
    relocs = open(os.path.join(out, 'native.relocs')).read().splitlines()
    if not any(' ints_sum 2 call ints@0:i32,ints@0:len ' in ln for ln in relocs):
        raise Fail("kinds sidecar does not carry the ints_sum descriptor: %r" % relocs)

    # Exit 11 contracts (design D4): each trap happens before the C call.
    traps = [
        ('cstr_nul', 'fn main() {\n    let s = "a\\0b"\n    println(bytesfix::cstr_len(s))\n}\n', 'NUL'),
        ('u32_neg', 'fn main() {\n    println(bytesfix::ui_echo(-1))\n}\n', 'out of range'),
        ('u32_big', 'fn main() {\n    println(bytesfix::ui_echo(4294967296))\n}\n', 'out of range'),
        ('i32_big', 'fn main() {\n    println(bytesfix::i_echo(2147483648))\n}\n', 'out of range'),
        ('i32_elem', 'fn main() {\n    var v = new []int(0)\n    append(v, 2147483648)\n'
                     '    println(bytesfix::ints_sum(&v))\n}\n', 'out of range'),
        ('u32_elem', 'fn main() {\n    var v = new []int(0)\n    append(v, 4294967296)\n'
                     '    println(bytesfix::ints_u_last(&v))\n}\n', 'out of range'),
    ]
    for name, body, text in traps:
        p, out = app(name, body)
        need(p, 'kinds %s build' % name)
        r = run([os.path.join(out, 'app')])
        if r.returncode != 11 or text not in r.stderr:
            raise Fail("kinds %s: want exit 11 with %r, got %d: %s"
                       % (name, text, r.returncode, (r.stderr or r.stdout).strip()))

    # Borrow timing (design 3.2, section 9): f(&a, grow(&a)).
    p, out = app('timing', KIND_TIMING)
    if p.returncode == 0:
        r = need(run([os.path.join(out, 'app')]), 'kinds timing run')
        # The native sees the grown array: length 2, g 7.
        if r.stdout.strip() != 'timing 2007':
            raise Fail("kinds timing: the native did not see the grown array: %r" % r.stdout.strip())
    elif 'point' not in (p.stderr + p.stdout):
        raise Fail("kinds timing: refused, but not by the overlap rule:\n%s" % (p.stderr + p.stdout).strip())

    # Negative spec tests: refused before link, so no executable is produced.
    def refused(name, spec, wrapper_text, want_text):
        nd = os.path.join(tmp, 'kneg_' + name)
        os.makedirs(nd)
        for n in ('bytes_fixture.c', 'bytes_fixture.h'):
            shutil.copy(os.path.join(d, n), os.path.join(nd, n))
        if wrapper_text is not None:
            open(os.path.join(nd, 'bytesfix_wrap.obx'), 'w').write(wrapper_text)
        sp = os.path.join(nd, 'bytesfix.cbind')
        json.dump(spec, open(sp, 'w'), indent=2)
        ad = os.path.join(nd, 'app.obx')
        open(ad, 'w').write('module app\n\nimport "bytesfix.obx"\n\n' + KIND_MAIN)
        out = os.path.join(nd, 'out')
        q = build(sp, ad, out)
        if q.returncode == 0 or want_text not in (q.stderr + q.stdout):
            raise Fail("kinds negative %s: want refusal with %r, got rc %d: %s"
                       % (name, want_text, q.returncode, (q.stderr + q.stdout).strip()))
        if os.path.exists(os.path.join(out, 'app')):
            raise Fail("kinds negative %s linked an executable" % name)

    def edited(fn_name, key, value):
        sp = kinds_spec('bytesfix', KIND_FUNCS)
        for f in sp['functions']:
            if f['name'] == fn_name:
                f[key] = value
        return sp

    # The spec says two arguments; the C header says one. The signature check refuses it.
    bad = edited('i_echo', 'args', ['i32@0', 'i64@1'])
    refused('arity_spec', bad, KIND_WRAPPER, 'incompatible')
    # The descriptor names two arguments; the call passes one.
    wrong_arity = KIND_WRAPPER.replace('"i32@0", v)', '"i32@0,i64@1", v)')
    refused('arity_call', kinds_spec('bytesfix', KIND_FUNCS), wrong_arity, 'takes 2 argument(s)')
    # A spec that says i32 where the wrapper passes u32: the sidecar kinds disagree.
    wrong = KIND_WRAPPER.replace('"i32@0", v)', '"u32@0", v)')
    refused('kind_mismatch', kinds_spec('bytesfix', KIND_FUNCS), wrong, 'argument kinds')
    refused('bare_i32', edited('i_echo', 'args', ['i32', 'i64@1']), KIND_WRAPPER,
            'i32 arguments need an argument index')
    refused('len_without_list', edited('ints_sum', 'args', ['ints@0:len']), KIND_WRAPPER,
            'counts a list')
    nowrap = kinds_spec('bytesfix', KIND_FUNCS)
    del nowrap['wrapper']
    refused('no_wrapper', nowrap, None, 'needs a maintained wrapper')
    refused('flag_on_cstr', edited('cstr_len', 'args', ['borrow_cstr@0:x']), KIND_WRAPPER,
            'takes no flag')
    print('kinds tests: ok')


# Stage (b) return kinds and stage (c) typed native fn declarations. The wrapper declares each native
# with its obo-side shape; the spec supplies the exact C kind and any C-only slots.
RET_WRAPPER = """module retfix

native fn rf_cb_empty(): ?[]byte
native fn rf_cb_one(): ?[]byte
native fn rf_cb_nul(): ?[]byte
native fn rf_cb_null_pos(): ?[]byte
native fn rf_cb_over(): ?[]byte
native fn rf_cb_exact(): ?[]byte
native fn rf_cb_many(a: &[]byte, b: &[]byte, x: int, y: int, z: int, w: int, v: int): ?[]byte
native fn rf_cb_even(a: &[]byte, b: &[]byte, x: int, y: int, z: int, w: int): ?[]byte
native fn rf_cb_arg(v: int): ?[]byte
native fn rf_take(v: int)
native fn rf_nz_i(v: int): int
native fn rf_cs_arg(v: int): []byte
native fn rf_cs_big_ok(): []byte
native fn rf_prefix(v: int): int
native fn rf_lenfirst(v: &[]int): int
native fn rf_cb_i64_many(a: int, b: int, c: int, d: int, e: int, f: int, g: int, h: int): ?[]byte
native fn rf_i32_neg(v: int): int
native fn rf_cs_normal(): []byte
native fn rf_cs_empty(): []byte
native fn rf_cs_null(): []byte
native fn rf_cs_tail(): []byte
native fn rf_setv(v: int)
native fn rf_u32max(): int
native fn rf_lastv(): int
native fn rf_nz_null(): int
native fn rf_nz_ptr(): int
native fn rf_add(a: int, b: int): int
native fn rf_sum3(v: &[]int, bias: int): int
native fn rf_nullconst(): int
native fn rf_tagged(s: string): int

pub fn cb_empty(): ?[]byte { return rf_cb_empty() }
pub fn cb_one(): ?[]byte { return rf_cb_one() }
pub fn cb_nul(): ?[]byte { return rf_cb_nul() }
pub fn cb_null_pos(): ?[]byte { return rf_cb_null_pos() }
pub fn cb_exact(): ?[]byte { return rf_cb_exact() }
pub fn cb_many(a: &[]byte, b: &[]byte, x: int, y: int, z: int, w: int, v: int): ?[]byte {
    return rf_cb_many(a, b, x, y, z, w, v)
}
pub fn cb_even(a: &[]byte, b: &[]byte, x: int, y: int, z: int, w: int): ?[]byte {
    return rf_cb_even(a, b, x, y, z, w)
}
pub fn cb_arg(v: int): ?[]byte { return rf_cb_arg(v) }
pub fn take(v: int) { rf_take(v) }
pub fn nz_i(v: int): int { return rf_nz_i(v) }
pub fn cs_arg(v: int): []byte { return rf_cs_arg(v) }
pub fn cs_big_ok(): []byte { return rf_cs_big_ok() }
pub fn prefix(v: int): int { return rf_prefix(v) }
pub fn lenfirst(v: &[]int): int { return rf_lenfirst(v) }
pub fn cb_i64_many(a: int, b: int, c: int, d: int, e: int, f: int, g: int, h: int): ?[]byte {
    return rf_cb_i64_many(a, b, c, d, e, f, g, h)
}
pub fn i32neg(v: int): int { return rf_i32_neg(v) }
pub fn cs_normal(): []byte { return rf_cs_normal() }
pub fn cs_empty(): []byte { return rf_cs_empty() }
pub fn cs_null(): []byte { return rf_cs_null() }
pub fn cs_tail(): []byte { return rf_cs_tail() }
pub fn setv(v: int) { rf_setv(v) }
pub fn u32max(): int { return rf_u32max() }
pub fn lastv(): int { return rf_lastv() }
pub fn nz_null(): int { return rf_nz_null() }
pub fn nz_ptr(): int { return rf_nz_ptr() }
pub fn add(a: int, b: int): int { return rf_add(a, b) }
pub fn sum3(v: &[]int, bias: int): int { return rf_sum3(v, bias) }
pub fn nullconst(): int { return rf_nullconst() }
pub fn tagged(s: string): int { return rf_tagged(s) }

pub fn over(): Result[[]byte] {
    let v = rf_cb_over()
    if v == nil { return err("over cap", 2) }
    return ok(v)
}
"""

RET_APP = """module app

import "retfix.obx"

fn main() {
    let e = retfix::cb_empty()
    if e == nil { println("empty nil") } else { println("empty len ", len(e)) }
    let o = retfix::cb_one()
    if o == nil { println("one nil") } else { println("one ", len(o), " ", string(o)) }
    let n = retfix::cb_nul()
    if n == nil { println("nul nil") } else {
        println("nul ", len(n), " ", int(n[0]), " ", int(n[1]), " ", int(n[2]))
    }
    let x = retfix::cb_exact()
    if x == nil { println("exact nil") } else {
        println("exact ", len(x), " ", int(x[0]), " ", int(x[1]), " ", int(x[2]), " ", int(x[3]))
    }
    var ma = new []byte(0)
    var mb = new []byte(0)
    let many = retfix::cb_many(&ma, &mb, 1, 2, 3, 4, 9)
    if many == nil { println("many nil") } else { println("many ", len(many), " ", string(many)) }
    let even = retfix::cb_even(&ma, &mb, 1, 2, 3, 8)
    if even == nil { println("even nil") } else { println("even ", len(even), " ", string(even)) }
    let cba = retfix::cb_arg(7)
    if cba == nil { println("cbarg nil") } else { println("cbarg ", len(cba), " ", string(cba)) }
    retfix::take(77)
    println("take ", retfix::lastv())
    println("nzi ", retfix::nz_i(7), " ", retfix::nz_i(1))
    let csa = retfix::cs_arg(5)
    println("csarg ", len(csa), " ", string(csa))
    let big = retfix::cs_big_ok()
    println("big ", len(big))
    println("prefix ", retfix::prefix(11))
    var lv = new []int(0)
    append(lv, 1)
    append(lv, 2)
    append(lv, 3)
    println("lenfirst ", retfix::lenfirst(&lv))
    let i64m = retfix::cb_i64_many(1, 2, 3, 4, 5, 6, 7, 9)
    if i64m == nil { println("i64many nil") } else { println("i64many ", len(i64m), " ", string(i64m)) }
    println("i32neg ", retfix::i32neg(9))
    let cn = retfix::cs_normal()
    println("cs ", len(cn), " ", string(cn))
    let ce = retfix::cs_empty()
    println("cse ", len(ce))
    let cx = retfix::cs_null()
    println("csn ", len(cx))
    let ct = retfix::cs_tail()
    println("cst ", len(ct), " ", string(ct))
    retfix::setv(42)
    println("void ", retfix::lastv())
    println("u32 ", retfix::u32max())
    println("nz ", retfix::nz_null(), " ", retfix::nz_ptr())
    println("add ", retfix::add(20, 22))
    var v = new []int(0)
    append(v, 1)
    append(v, 2)
    append(v, 3)
    println("sum3 ", retfix::sum3(&v, 10))
    println("nc ", retfix::nullconst())
    println("tag ", retfix::tagged("hi"))
    let ov = retfix::over()
    match ov {
        ok(b) { println("over ok ", len(b)) }
        err(m, c) { println("over err ", m, " ", c) }
    }
}
"""

RET_WANT = [
    'empty len 0', 'one 1 A', 'nul 3 97 0 98', 'exact 4 120 121 0 122',
    'many 2 hi', 'even 3 abc', 'cbarg 2 ok', 'take 77', 'nzi 1 0',
    'csarg 3 arg', 'big 16777215', 'prefix 11', 'lenfirst 6',
    'i64many 4 wxyz', 'i32neg -5',
    'cs 5 hello', 'cse 0', 'csn 0', 'cst 2 ok', 'void 42', 'u32 4294967295', 'nz 0 1',
    'add 42', 'sum3 16', 'nc 5', 'tag 9', 'over err over cap 2',
]


def ret_func(name, ret, **extra):
    e = {'name': name, 'symbol': name, 'args': [], 'ret': ret, 'effects': ['impure']}
    e.update(extra)
    return e


def returns_spec():
    cb = lambda n, cap=8: ret_func(n, 'cbytes', length=n + '_len', cap=cap,
                                  ret_ctype='const char *')
    return {
        'format': 'cinterop-proto/2', 'module': 'retfix',
        'platform': 'x86_64-linux-gnu', 'abi': 'sysv-amd64',
        'signature_check': 'header',
        'sources': ['return_fixture.c'], 'headers': ['return_fixture.h'],
        'libs': [], 'cflags': ['-fno-omit-frame-pointer'], 'wrapper': 'retfix_wrap.obx',
        'functions': [
            cb('rf_cb_empty'), cb('rf_cb_one'), cb('rf_cb_nul'),
            cb('rf_cb_null_pos'), cb('rf_cb_over'), cb('rf_cb_exact'),
            {'name': 'rf_cb_many', 'symbol': 'rf_cb_many',
             'args': ['borrow_bytes@0', 'borrow_bytes@1', 'i32@2', 'i32@3', 'i32@4', 'i32@5',
                      'i32@6'],
             'ret': 'cbytes', 'length': 'rf_cb_many_len', 'cap': 8,
             'ret_ctype': 'const char *', 'effects': ['impure']},
            {'name': 'rf_cb_even', 'symbol': 'rf_cb_even',
             'args': ['borrow_bytes@0', 'borrow_bytes@1', 'i32@2', 'i32@3', 'i32@4', 'i32@5'],
             'ret': 'cbytes', 'length': 'rf_cb_even_len', 'cap': 8,
             'ret_ctype': 'const char *', 'effects': ['impure']},
            {'name': 'rf_cb_arg', 'symbol': 'rf_cb_arg', 'args': ['i64'], 'ret': 'cbytes',
             'length': 'rf_cb_arg_len', 'cap': 8, 'ret_ctype': 'const char *',
             'effects': ['impure']},
            {'name': 'rf_take', 'symbol': 'rf_take', 'args': ['i64'], 'ret': 'void',
             'effects': ['impure']},
            {'name': 'rf_nz_i', 'symbol': 'rf_nz_i', 'args': ['i64'], 'ret': 'nonzero',
             'ret_ctype': 'const void *', 'effects': ['impure']},
            {'name': 'rf_cs_arg', 'symbol': 'rf_cs_arg', 'args': ['i64'], 'ret': 'cstr_out',
             'ret_ctype': 'const char *', 'effects': ['impure']},
            {'name': 'rf_cs_big_ok', 'symbol': 'rf_cs_big_ok', 'args': [], 'ret': 'cstr_out',
             'ret_ctype': 'const char *', 'effects': ['impure']},
            {'name': 'rf_prefix', 'symbol': 'rf_prefix', 'args': ['null', 'i32@0'],
             'ctypes': ['const char *', None], 'ret': 'i64', 'effects': ['impure']},
            {'name': 'rf_lenfirst', 'symbol': 'rf_lenfirst',
             'args': ['ints@0:len', 'ints@0:i32'], 'ret': 'i64', 'effects': ['impure']},
            {'name': 'rf_cb_i64_many', 'symbol': 'rf_cb_i64_many',
             'args': ['i64'] * 8, 'ret': 'cbytes', 'length': 'rf_cb_i64_many_len', 'cap': 8,
             'ret_ctype': 'const char *', 'effects': ['impure']},
            {'name': 'rf_i32_neg', 'symbol': 'rf_i32_neg', 'args': ['i64'], 'ret': 'i32',
             'effects': ['impure']},
            ret_func('rf_cs_normal', 'cstr_out', ret_ctype='const char *'),
            ret_func('rf_cs_empty', 'cstr_out', ret_ctype='const char *'),
            ret_func('rf_cs_null', 'cstr_out', ret_ctype='const char *'),
            ret_func('rf_cs_tail', 'cstr_out', ret_ctype='const char *'),
            {'name': 'rf_setv', 'symbol': 'rf_setv', 'args': ['i32@0'], 'ret': 'void',
             'effects': ['impure']},
            ret_func('rf_u32max', 'u32'),
            ret_func('rf_lastv', 'i64'),
            ret_func('rf_nz_null', 'nonzero', ret_ctype='const void *'),
            ret_func('rf_nz_ptr', 'nonzero', ret_ctype='const void *'),
            {'name': 'rf_add', 'symbol': 'rf_add', 'args': ['i64', 'i64'], 'ret': 'i64',
             'effects': ['impure']},
            {'name': 'rf_sum3', 'symbol': 'rf_sum3',
             'args': ['ints@0:i32', 'ints@0:len', 'i32@1'], 'ret': 'i64', 'effects': ['impure']},
            {'name': 'rf_nullconst', 'symbol': 'rf_nullconst', 'args': ['null', 'const:5'],
             'ctypes': ['const char *', 'int'], 'ret': 'i64', 'effects': ['impure']},
            {'name': 'rf_tagged', 'symbol': 'rf_tagged',
             'args': ['borrow_cstr@0', 'const:7'], 'ctypes': [None, 'int'],
             'ret': 'i64', 'effects': ['impure']},
        ],
    }


def _tool():
    import importlib.util
    spec = importlib.util.spec_from_file_location('cinterop_tool_ret', TOOLPY)
    tool = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tool)
    return tool


def returns(tmp):
    """Stage (b) copy-out and typed returns, and stage (c) native fn declarations end to end."""
    d = os.path.join(tmp, 'returns_src')
    os.makedirs(d)
    for name in ('return_fixture.c', 'return_fixture.h'):
        shutil.copy(os.path.join(HERE, name), os.path.join(d, name))
    open(os.path.join(d, 'retfix_wrap.obx'), 'w').write(RET_WRAPPER)
    spec_path = os.path.join(d, 'retfix.cbind')
    json.dump(returns_spec(), open(spec_path, 'w'), indent=2)
    ad = os.path.join(d, 'app.obx')
    open(ad, 'w').write(RET_APP)
    out = os.path.join(d, 'out')
    need(build(spec_path, ad, out), 'returns build')
    p = need(run([os.path.join(out, 'app')], env=dict(os.environ, OBO_CHECK='1')), 'returns run')
    if p.stdout.splitlines() != RET_WANT:
        raise Fail("returns output:\n got  %r\n want %r" % (p.stdout.splitlines(), RET_WANT))
    relocs = open(os.path.join(out, 'native.relocs')).read().splitlines()
    if relocs[0] != 'cinterop-relocs 2':
        raise Fail("returns sidecar is not v2")
    if not any(' rf_cb_one 0 fn - optbytes ' in ln for ln in relocs):
        raise Fail("cbytes sidecar record is missing: %r" % relocs[:3])
    if not any(' rf_sum3 2 fn ints,int i64 ' in ln for ln in relocs):
        raise Fail("C-only list expansion sidecar record is missing")
    if not any(' rf_nullconst 0 fn - i64 ' in ln for ln in relocs):
        raise Fail("null/const native fn sidecar record is missing")

    # cbytes NULL with a positive length exits 11 before any C call returns.
    npd = os.path.join(tmp, 'returns_nullpos')
    os.makedirs(npd)
    for name in ('return_fixture.c', 'return_fixture.h', 'retfix_wrap.obx'):
        shutil.copy(os.path.join(d, name), os.path.join(npd, name))
    shutil.copy(spec_path, os.path.join(npd, 'retfix.cbind'))
    nap = os.path.join(npd, 'app.obx')
    open(nap, 'w').write('module app\n\nimport "retfix.obx"\n\n'
                         'fn main() {\n    let v = retfix::cb_null_pos()\n'
                         '    if v == nil { println("nil") } else { println(len(v)) }\n}\n')
    nout = os.path.join(npd, 'out')
    need(build(os.path.join(npd, 'retfix.cbind'), nap, nout), 'returns null-positive build')
    r = run([os.path.join(nout, 'app')])
    if r.returncode != 11 or 'NULL' not in (r.stderr + r.stdout):
        raise Fail("cbytes NULL with positive length: want exit 11, got %d: %s"
                   % (r.returncode, (r.stderr or r.stdout).strip()))

    # Tool negatives: a typed declaration that disagrees with the spec is refused before link.
    def one_build(name, spec, wrapper, app_body, want):
        nd = os.path.join(tmp, 'retneg_' + name)
        os.makedirs(nd)
        for n in ('return_fixture.c', 'return_fixture.h'):
            shutil.copy(os.path.join(HERE, n), os.path.join(nd, n))
        sp = dict(spec)
        sp['module'] = 'one'
        sp['wrapper'] = 'one_wrap.obx'
        open(os.path.join(nd, 'one_wrap.obx'), 'w').write(wrapper)
        spath = os.path.join(nd, 'one.cbind')
        json.dump(sp, open(spath, 'w'), indent=2)
        ap = os.path.join(nd, 'app.obx')
        open(ap, 'w').write('module app\n\nimport "one.obx"\n\n' + app_body)
        q = build(spath, ap, os.path.join(nd, 'out'))
        if q.returncode == 0 or want not in (q.stderr + q.stdout):
            raise Fail("returns negative %s: want %r, got rc %d: %s"
                       % (name, want, q.returncode, (q.stderr + q.stdout).strip()[-500:]))
        return os.path.join(nd, 'out')

    # One-function specs that must build and then hit an exit-11 contract in the helper.
    def one_run(name, spec, wrapper, app_body, want):
        nd = os.path.join(tmp, 'retrun_' + name)
        os.makedirs(nd)
        for n in ('return_fixture.c', 'return_fixture.h'):
            shutil.copy(os.path.join(HERE, n), os.path.join(nd, n))
        sp = dict(spec)
        sp['module'] = 'one'
        sp['wrapper'] = 'one_wrap.obx'
        open(os.path.join(nd, 'one_wrap.obx'), 'w').write(wrapper)
        spath = os.path.join(nd, 'one.cbind')
        json.dump(sp, open(spath, 'w'), indent=2)
        ap = os.path.join(nd, 'app.obx')
        open(ap, 'w').write('module app\n\nimport "one.obx"\n\n' + app_body)
        out = os.path.join(nd, 'out')
        need(build(spath, ap, out), 'returns run build ' + name)
        q = run([os.path.join(out, 'app')])
        if q.returncode != 11 or want not in (q.stderr + q.stdout):
            raise Fail("returns run %s: want exit 11 with %r, got %d: %s"
                       % (name, want, q.returncode, (q.stderr or q.stdout).strip()[-400:]))

    def one_ok(name, spec, wrapper, app_body, want):
        nd = os.path.join(tmp, 'retok_' + name)
        os.makedirs(nd)
        for n in ('return_fixture.c', 'return_fixture.h'):
            shutil.copy(os.path.join(HERE, n), os.path.join(nd, n))
        sp = dict(spec)
        sp['module'] = 'one'
        sp['wrapper'] = 'one_wrap.obx'
        open(os.path.join(nd, 'one_wrap.obx'), 'w').write(wrapper)
        spath = os.path.join(nd, 'one.cbind')
        json.dump(sp, open(spath, 'w'), indent=2)
        ap = os.path.join(nd, 'app.obx')
        open(ap, 'w').write('module app\n\nimport "one.obx"\n\n' + app_body)
        out = os.path.join(nd, 'out')
        need(build(spath, ap, out), 'returns ok build ' + name)
        q = need(run([os.path.join(out, 'app')]), 'returns ok run ' + name)
        if q.stdout.strip() != want:
            raise Fail("returns ok %s: output %r, want %r" % (name, q.stdout.strip(), want))

    def one_func(name, ret, args, **extra):
        e = {'name': name, 'symbol': name, 'args': args, 'ret': ret,
             'effects': ['impure']}
        e.update(extra)
        return {'format': 'cinterop-proto/2', 'module': 'one',
                'platform': 'x86_64-linux-gnu', 'abi': 'sysv-amd64',
                'signature_check': 'header', 'sources': ['return_fixture.c'],
                'headers': ['return_fixture.h'], 'libs': [], 'cflags': [],
                'functions': [e]}

    one_build('ret_int_for_cbytes',
              {'format': 'cinterop-proto/2', 'module': 'one',
               'platform': 'x86_64-linux-gnu', 'abi': 'sysv-amd64',
               'signature_check': 'header', 'sources': ['return_fixture.c'],
               'headers': ['return_fixture.h'], 'libs': [], 'cflags': [],
               'functions': [ret_func('rf_cb_one', 'cbytes', length='rf_cb_one_len', cap=8,
                                      ret_ctype='const char *')]},
              'module one\n\nnative fn rf_cb_one(): int\n\n'
              'pub fn run(): int { return rf_cb_one() }\n',
              'fn main() { println(one::run()) }\n',
              'the spec needs optbytes')
    one_build('ints_for_borrow_bytes',
              {'format': 'cinterop-proto/2', 'module': 'one',
               'platform': 'x86_64-linux-gnu', 'abi': 'sysv-amd64',
               'signature_check': 'header', 'sources': ['return_fixture.c'],
               'headers': ['return_fixture.h'], 'libs': [], 'cflags': [],
               'functions': [{'name': 'rf_bytes_arg', 'symbol': 'rf_bytes_arg',
                              'args': ['borrow_bytes@0'], 'ret': 'i64',
                              'effects': ['impure']}]},
              'module one\n\nnative fn rf_bytes_arg(v: &[]int): int\n\n'
              'pub fn run(v: &[]int): int { return rf_bytes_arg(v) }\n',
              'fn main() {\n    var v = new []int(0)\n    append(v, 1)\n'
              '    println(one::run(&v))\n}\n',
              'the spec needs bytes')

    # A spec name that differs from the C symbol is mapped to the symbol in the relocation.
    one_ok('rename_to_symbol',
           {'format': 'cinterop-proto/2', 'module': 'one',
            'platform': 'x86_64-linux-gnu', 'abi': 'sysv-amd64',
            'signature_check': 'header', 'sources': ['return_fixture.c'],
            'headers': ['return_fixture.h'], 'libs': [], 'cflags': [],
            'functions': [{'name': 'echo_long', 'symbol': 'rf_add', 'args': ['i64', 'i64'],
                           'ret': 'i64', 'effects': ['impure']}]},
           'module one\n\nnative fn echo_long(a: int, b: int): int\n\n'
           'pub fn run(a: int, b: int): int { return echo_long(a, b) }\n',
           'fn main() { println(one::run(20, 22)) }\n', '42')

    # The declaration name is the spec `name`; the C symbol is not accepted in its place.
    one_build('fn_uses_c_symbol_name',
              {'format': 'cinterop-proto/2', 'module': 'one',
               'platform': 'x86_64-linux-gnu', 'abi': 'sysv-amd64',
               'signature_check': 'header', 'sources': ['return_fixture.c'],
               'headers': ['return_fixture.h'], 'libs': [], 'cflags': [],
               'functions': [{'name': 'alpha', 'symbol': 'rf_add', 'args': ['i64', 'i64'],
                              'ret': 'i64', 'effects': ['impure']}]},
              'module one\n\nnative fn rf_add(a: int, b: int): int\n\n'
              'pub fn run(a: int, b: int): int { return rf_add(a, b) }\n',
              'fn main() { println(one::run(1, 2)) }\n',
              'not in the binding spec by name')

    # cbytes length function: wrong parameters, wrong return, and a missing C symbol.
    even_wrap = ('module one\n\n'
                 'native fn rf_cb_even(a: &[]byte, b: &[]byte, x: int, y: int, z: int, w: int): ?[]byte\n\n'
                 'pub fn run(a: &[]byte, b: &[]byte, x: int, y: int, z: int, w: int): ?[]byte {\n'
                 '    return rf_cb_even(a, b, x, y, z, w)\n}\n')
    even_app = ('fn main() {\n    var a = new []byte(0)\n    var b = new []byte(0)\n'
                '    let v = one::run(&a, &b, 1, 2, 3, 8)\n'
                '    if v == nil { println("nil") } else { println(len(v)) }\n}\n')
    even_args = ['borrow_bytes@0', 'borrow_bytes@1', 'i32@2', 'i32@3', 'i32@4', 'i32@5']
    one_build('cbytes_length_params',
              one_func('rf_cb_even', 'cbytes', even_args, length='rf_cb_one_len', cap=8,
                       ret_ctype='const char *'),
              even_wrap, even_app, 'incompatible')
    one_build('cbytes_length_return',
              one_func('rf_cb_even', 'cbytes', even_args, length='rf_cb_even_len_wrong', cap=8,
                       ret_ctype='const char *'),
              even_wrap, even_app, 'incompatible')
    one_build('cbytes_length_missing',
              one_func('rf_cb_even', 'cbytes', even_args, length='rf_no_such_len', cap=8,
                       ret_ctype='const char *'),
              even_wrap, even_app, 'rf_no_such_len')

    # The two exit-11 copy-out contracts that need a separate run.
    one_run('cbytes_negative_length',
            one_func('rf_cb_neg', 'cbytes', [], length='rf_cb_neg_len', cap=8,
                     ret_ctype='const char *'),
            'module one\n\nnative fn rf_cb_neg(): ?[]byte\n\n'
            'pub fn run(): ?[]byte { return rf_cb_neg() }\n',
            'fn main() {\n    let v = one::run()\n'
            '    if v == nil { println("nil") } else { println(len(v)) }\n}\n',
            'negative')
    one_run('cstr_out_no_nul_bound',
            one_func('rf_cs_big_bad', 'cstr_out', [], ret_ctype='const char *'),
            'module one\n\nnative fn rf_cs_big_bad(): []byte\n\n'
            'pub fn run(): []byte { return rf_cs_big_bad() }\n',
            'fn main() { let v = one::run()\n    println(len(v)) }\n',
            '16 MiB')

    # A v1 sidecar (written by an older compiler) is still read and accepted for its int form.
    tool = _tool()
    v1 = os.path.join(tmp, 'v1.relocs')
    open(v1, 'w').write('cinterop-relocs 1\n42 add 2 i64,i64 i64 freesall,writeshandles\n'
                        '99 zero 0 - i64 freesall,writeshandles\n')
    old = tool.read_relocs(v1)
    if [r['mode'] for r in old] != ['call', 'call']:
        raise Fail("a v1 sidecar was not read as call mode")
    tool.validate_calls(old, {'format': 'cinterop-proto/1', 'functions': [
        {'name': 'add', 'symbol': 'add', 'args': ['i64', 'i64'], 'ret': 'i64'},
        {'name': 'zero', 'symbol': 'zero', 'args': [], 'ret': 'i64'}]})

    # A v2 fn sidecar needs a /2 spec; a v2 call cannot target a typed return.
    def relocs_text(lines):
        path = os.path.join(tmp, 'relocs_%d' % len(os.listdir(tmp)))
        open(path, 'w').write('cinterop-relocs 2\n' + '\n'.join(lines) + '\n')
        return tool.read_relocs(path)

    try:
        tool.validate_calls(relocs_text(['42 rf_add 2 fn int,int i64 freesall,writeshandles']),
                            {'format': 'cinterop-proto/1', 'functions': [
                                {'name': 'rf_add', 'symbol': 'rf_add',
                                 'args': ['i64', 'i64'], 'ret': 'i64'}]})
    except tool.ToolError as e:
        if 'needs a cinterop-proto/2 spec' not in str(e):
            raise Fail("v2 fn against a v1 spec: unexpected error %s" % e)
    else:
        raise Fail("a v2 fn sidecar was accepted against a v1 spec")
    try:
        tool.validate_calls(relocs_text(['42 rf_cb_one 0 call - i64 freesall,writeshandles']),
                            {'format': 'cinterop-proto/2', 'functions': [
                                {'name': 'rf_cb_one', 'symbol': 'rf_cb_one', 'args': [],
                                 'ret': 'cbytes', 'length': 'rf_cb_one_len', 'cap': 8,
                                 'ret_ctype': 'const char *'}]})
    except tool.ToolError as e:
        if 'use a typed native fn declaration' not in str(e):
            raise Fail("v2 call against a typed ret: unexpected error %s" % e)
    else:
        raise Fail("a v2 call against a typed return was accepted")

    # declarations-only is a valid /2 mode; a non-object-pointer ret_ctype is not.
    ds = {'format': 'cinterop-proto/2', 'module': 'one',
          'platform': 'x86_64-linux-gnu', 'abi': 'sysv-amd64',
          'signature_check': 'declarations-only',
          'declarations': ['long rf_add(long, long);'],
          'wrapper': 'one_wrap.obx',
          'functions': [{'name': 'rf_add', 'symbol': 'rf_add', 'args': ['i64', 'i64'],
                         'ret': 'i64', 'effects': ['impure']}]}
    dsp = os.path.join(tmp, 'declonly.cbind')
    json.dump(ds, open(dsp, 'w'), indent=2)
    tool.load_spec(dsp)
    # Every spelling below must be refused by the ret_ctype grammar, not by a missing wrapper.
    ret_variants = [
        ('fnptr_spaced', 'void ( * )(void *)', 'not a function pointer'),
        ('fnptr_unprototyped', 'void (*)()', 'parameter'),
        ('typedef_name', 'rf_dtor', 'not a pointer'),
        ('ptr_to_fnptr', 'void (**)(void *)', 'not one C type'),
        ('ptr_to_array', 'int (*)[4]', 'characters'),
    ]
    for name, ctype, want in ret_variants:
        fp = dict(ds)
        fp['functions'] = [{'name': 'rf_nz', 'symbol': 'rf_nz', 'args': [], 'ret': 'nonzero',
                            'ret_ctype': ctype, 'effects': ['impure']}]
        fpp = os.path.join(tmp, 'retctype_' + name + '.cbind')
        json.dump(fp, open(fpp, 'w'), indent=2)
        try:
            tool.load_spec(fpp)
        except tool.ToolError as e:
            text = str(e)
            if 'must spell an object pointer type' not in text or want not in text:
                raise Fail("ret_ctype %s: unexpected error %s" % (name, e))
        else:
            raise Fail("ret_ctype %s was accepted" % name)

    # Sidecar v2 tamper: an unknown shape, mode or result shape is refused by the reader.
    good = open(os.path.join(out, 'native.relocs')).read().splitlines()

    def reloc_index(word):
        for i, ln in enumerate(good):
            if (' ' + word + ' ') in ln:
                return i
        raise Fail("no sidecar record for %r" % word)

    def reloc_tamper(name, index, old, new, want):
        lines = list(good)
        lines[index] = lines[index].replace(old, new, 1)
        path = os.path.join(tmp, 'tamper_' + name)
        open(path, 'w').write('\n'.join(lines) + '\n')
        try:
            tool.read_relocs(path)
        except tool.ToolError as e:
            if want not in str(e):
                raise Fail("sidecar tamper %s: unexpected error %s" % (name, e))
        else:
            raise Fail("sidecar tamper %s was accepted" % name)

    # a shape on a record with one argument, so the arity still matches and the token itself is checked
    reloc_tamper('shape', reloc_index('rf_setv'), ' fn int void ', ' fn bogus void ', 'unknown shape')
    reloc_tamper('mode', reloc_index('rf_cb_one'), ' fn ', ' weird ', 'unknown mode')
    # A syntactically valid but different shape is refused by the spec comparison, not the reader.
    lines = list(good)
    for i, ln in enumerate(lines):
        if ' rf_sum3 ' in ln:
            lines[i] = ln.replace(' ints,int ', ' int,int ', 1)
    tp = os.path.join(tmp, 'tamper_shape_spec')
    open(tp, 'w').write('\n'.join(lines) + '\n')
    relocs = tool.read_relocs(tp)
    try:
        tool.validate_calls(relocs, returns_spec())
    except tool.ToolError as e:
        if 'the spec needs ints,int' not in str(e):
            raise Fail("sidecar shape tamper: unexpected error %s" % e)
    else:
        raise Fail("a sidecar shape tamper was accepted")
    # A changed return shape is refused by the spec comparison.
    lines = list(good)
    i = reloc_index('rf_cb_one')
    lines[i] = lines[i].replace(' optbytes ', ' i64 ', 1)
    tp = os.path.join(tmp, 'tamper_ret_spec')
    open(tp, 'w').write('\n'.join(lines) + '\n')
    try:
        tool.validate_calls(tool.read_relocs(tp), returns_spec())
    except tool.ToolError as e:
        if 'the spec needs optbytes' not in str(e):
            raise Fail("sidecar return-shape tamper: unexpected error %s" % e)
    else:
        raise Fail("a sidecar return-shape tamper was accepted")
    # A changed nargs is refused by the reader before the spec comparison.
    lines = list(good)
    i = reloc_index('rf_add')
    lines[i] = lines[i].replace(' rf_add 2 fn ', ' rf_add 1 fn ', 1)
    tp = os.path.join(tmp, 'tamper_nargs')
    open(tp, 'w').write('\n'.join(lines) + '\n')
    try:
        tool.read_relocs(tp)
    except tool.ToolError as e:
        if 'shapes do not match arity' not in str(e):
            raise Fail("sidecar nargs tamper: unexpected error %s" % e)
    else:
        raise Fail("a sidecar nargs tamper was accepted")
    # Shapes swapped between two functions are refused by the spec comparison.
    lines = list(good)
    ia, ib = reloc_index('rf_add'), reloc_index('rf_sum3')
    fa, fb = lines[ia].split(), lines[ib].split()
    fa[4], fb[4] = fb[4], fa[4]
    lines[ia], lines[ib] = ' '.join(fa), ' '.join(fb)
    tp = os.path.join(tmp, 'tamper_swap')
    open(tp, 'w').write('\n'.join(lines) + '\n')
    try:
        tool.validate_calls(tool.read_relocs(tp), returns_spec())
    except tool.ToolError as e:
        if 'the spec needs int,int' not in str(e):
            raise Fail("sidecar swapped-shapes tamper: unexpected error %s" % e)
    else:
        raise Fail("a sidecar swapped-shapes tamper was accepted")

    # Spec v2: signature_check is required and honest; cbytes carries length, cap and ret_ctype.
    def spec_error(name, mutate, want):
        sp = returns_spec()
        mutate(sp)
        path = os.path.join(tmp, 'spec_' + name + '.cbind')
        json.dump(sp, open(path, 'w'), indent=2)
        try:
            tool.load_spec(path)
        except tool.ToolError as e:
            if want not in str(e):
                raise Fail("spec negative %s: unexpected error %s" % (name, e))
        else:
            raise Fail("spec negative %s was accepted" % name)

    spec_error('no_sigmode', lambda s: s.pop('signature_check'), 'needs "signature_check"')
    spec_error('bad_sigmode', lambda s: s.__setitem__('signature_check', 'trust-me'),
               'needs "signature_check"')
    spec_error('header_no_headers',
               lambda s: s.__setitem__('headers', []), 'requires "headers"')
    spec_error('cbytes_no_length',
               lambda s: s['functions'][0].pop('length'), 'cbytes needs "length"')
    spec_error('cbytes_no_cap',
               lambda s: s['functions'][0].pop('cap'), 'cbytes needs "cap"')
    spec_error('cbytes_no_ret_ctype',
               lambda s: s['functions'][0].pop('ret_ctype'), 'needs "ret_ctype"')
    spec_error('nonzero_no_ret_ctype',
               lambda s: next(f for f in s['functions'] if f['name'] == 'rf_nz_null').pop('ret_ctype'),
               'needs "ret_ctype"')

    # Compile-time negatives: the declaration form and its collisions.
    compile_neg = [
        ('body', 'module m\nnative fn f(a: int): int { return a }\n', 'takes no body'),
        ('method', 'module m\ntype T = ref record { x: int }\n'
                   'native fn (r: &T) f(a: int): int\n', 'cannot have a receiver'),
        ('pub', 'module m\npub native fn f(a: int): int\n', "'pub' is not allowed"),
        ('badparam', 'module m\nnative fn f(a: float): int\n', 'unsupported type'),
        ('badret', 'module m\nnative fn f(): []int\n', 'unsupported return type'),
        ('duplicate', 'module m\nnative fn f(a: int): int\nnative fn f(a: int): int\n',
         'already declared'),
        ('native_then_fn', 'module m\nnative fn f(a: int): int\n'
                           'fn f(a: int): int { return a }\n', 'declared native'),
        ('fn_then_native', 'module m\nfn f(a: int): int { return a }\n'
                           'native fn f(a: int): int\n',
         'already declared as a normal function'),
        ('next_line_brace', 'module m\nnative fn f(a: int): int\n{ }\n', 'takes no body'),
        ('void_return', 'module m\nnative fn f(): void\n', 'omit the return type'),
        ('native_arity_more', 'module m\nnative fn f(a: int): int\n'
                              'fn g() { println(f(1, 2)) }\n', 'takes 1 argument(s), not more'),
        ('native_arity_less', 'module m\nnative fn f(a: int): int\n'
                              'fn g() { println(f()) }\n', 'takes 1 argument(s), not 0'),
        ('native_arg_type', 'module m\nnative fn f(a: int): int\n'
                            'fn g() { println(f("x")) }\n', 'must be int'),
        ('native_list_arg', 'module m\nnative fn f(v: &[]int): int\n'
                            'fn g() { println(f(1)) }\n', 'needs a []int'),
        ('native_void_value', 'module m\nnative fn h()\n'
                              'fn g() { let x = h() }\n', 'cannot infer a type'),
    ]
    for name, text, want in compile_neg:
        cd = os.path.join(tmp, 'compile_neg_' + name)
        os.makedirs(cd)
        path = os.path.join(cd, 'm.obx')
        open(path, 'w').write(text)
        q = run([OBO, path, '-o', os.path.join(cd, 'out')])
        if q.returncode == 0 or want not in (q.stderr + q.stdout):
            raise Fail("native compile negative %s: want %r, got rc %d: %s"
                       % (name, want, q.returncode, (q.stderr + q.stdout).strip()[-400:]))

    # `native` stays a contextual word: it is still usable as a function, local, top-level let and field.
    ident_programs = [
        ('fn', 'module app\nfn native(x: int): int { return x + 1 }\n'
               'fn main() { println(native(1)) }\n', '2'),
        ('var', 'module app\nfn main() { var native = 2\n    let x = native + 1\n    println(x) }\n',
         '3'),
        ('top_let', 'module app\nlet native = 3\nfn main() { println(native) }\n', '3'),
        ('field', 'module app\ntype T = ref record { native: int }\n'
                  'fn main() { var t = new T{native: 4}\n    println(t.native) }\n', '4'),
    ]
    for name, text, want in ident_programs:
        idir = os.path.join(tmp, 'ident_' + name)
        os.makedirs(idir)
        path = os.path.join(idir, 'app.obx')
        open(path, 'w').write(text)
        out = os.path.join(idir, 'out')
        need(run([OBO, path, '-o', out]), 'native identifier ' + name + ' build')
        q = need(run([out]), 'native identifier ' + name + ' run')
        if q.stdout.strip() != want:
            raise Fail("native identifier %s: output %r, want %r"
                       % (name, q.stdout.strip(), want))

    # --check parses and type-checks native declarations but refuses a native call.
    ck = os.path.join(tmp, 'returns_check')
    os.makedirs(ck)
    open(os.path.join(ck, 'retfix.obx'), 'w').write(RET_WRAPPER)
    q = run([OBO, os.path.join(ck, 'retfix.obx'), '--check', '--sym-dir', ck])
    if q.returncode == 0 or 'cannot be called in --check' not in (q.stderr + q.stdout):
        raise Fail("--check did not refuse a native call: %s"
                   % (q.stderr + q.stdout).strip()[-400:])
    print('returns tests: ok')


FOREIGN_WRAPPER = """module handles

pub type Conn = foreign "Fk"
pub type Bad = foreign "FkBad"

native fn ff_new(v: int): Conn
native fn ff_new_opt(v: int): ?Conn
native fn ff_get(c: &Conn): int
native fn ff_mark(c: &Conn): int
native fn ff_touch(c: &Conn): int
native fn ff_bad_new(): Bad
native fn ff_frees(): int

pub fn (mut c: &Conn) close(): Result[bool] { return __foreignClose(c) }
pub fn (c: &Conn) is_open(): bool { return __foreignIsOpen(c) }
pub fn (mut c: &Bad) close(): Result[bool] { return __foreignClose(c) }
pub fn (c: &Bad) is_open(): bool { return __foreignIsOpen(c) }

pub type Box = ref record { h: Conn }

pub fn open(v: int): Conn { return ff_new(v) }
pub fn (mut b: &Box) touch(): int { return ff_touch(&b.h) }
pub fn (mut b: &Box) closebox() { b.h.close() }
pub fn boxed(v: int): Box { return new Box{h: open(v)} }
pub fn open_opt(v: int): ?Conn { return ff_new_opt(v) }
pub fn get(c: &Conn): int { return ff_get(c) }
pub fn mark(c: &Conn): int { return ff_mark(c) }
pub fn touch(c: &Conn): int { return ff_touch(c) }
pub fn bad(): Bad { return ff_bad_new() }
pub fn frees(): int { return ff_frees() }
"""

FOREIGN_APP = """type Box = ref record { h: handles::Conn }

fn (mut b: &Box) closeh() { b.h.close() }

fn use_defer() {
    var a = handles::open(8)
    defer a.close()
    println("defer ", handles::get(&a))
}

fn main() {
    var a = handles::open(11)
    println("open ", a.is_open(), " ", handles::get(&a))
    println("touch ", handles::touch(&a))
    let hh = handle(a)
    println("alive ", alive(hh), " open ", a.is_open())
    let r = a.close()
    match r {
        ok(v) { println("close ", v) }
        err(m, c) { println("err ", m, " ", c) }
    }
    println("closed ", a.is_open(), " alive ", alive(hh))
    let r2 = a.close()
    match r2 {
        ok(v) { println("again ", v) }
        err(m, c) { println("err ", m, " ", c) }
    }
    println("frees ", handles::frees())

    var bx = handles::boxed(4)
    println("fieldtouch ", bx.touch())
    bx.closebox()

    var b = new Box{h: handles::open(3)}
    println("field open ", b.h.is_open())
    b.closeh()
    println("field closed ", b.h.is_open())

    var arr = new []handles::Conn(0)
    append(arr, handles::open(4))
    println("elem open ", arr[0].is_open())
    arr[0].close()
    println("elem closed ", arr[0].is_open())

    var c = handles::open(9)
    let ch = handle(c)
    println("handle open ", ch.is_open())
    ch.close()
    println("handle closed ", c.is_open())

    use_defer()
    let o = handles::open_opt(-1)
    if o == nil { println("opt nil") } else { println("opt bad") }
    var o2 = handles::open_opt(12)
    if o2 == nil {
        println("opt2 nil")
    } else {
        println("opt2 ", handles::get(&o2), " ", o2.is_open())
        o2.close()
    }

    var bad = handles::bad()
    println("bad open ", bad.is_open())
    let rb = bad.close()
    match rb {
        ok(v) { println("bad ok ", v) }
        err(m, c) { println("bad err ", m, " ", c) }
    }
    println("bad closed ", bad.is_open())
    println("final frees ", handles::frees())
}
"""

FOREIGN_WANT = [
    'open true 11', 'touch 12', 'alive true open true', 'close true', 'closed false alive true',
    'again false', 'frees 1', 'fieldtouch 5',
    'field open true', 'field closed false',
    'elem open true', 'elem closed false',
    'handle open true', 'handle closed false',
    'defer 8', 'opt nil', 'opt2 12 true',
    'bad open true', 'bad err foreign release failed 42', 'bad closed false',
    'final frees 7',
]


def foreign_spec():
    return {
        'format': 'cinterop-proto/2', 'module': 'handles',
        'platform': 'x86_64-linux-gnu', 'abi': 'sysv-amd64',
        'signature_check': 'header',
        'sources': ['foreign_fixture.c'], 'headers': ['foreign_fixture.h'],
        'libs': [], 'cflags': [], 'wrapper': 'handles_wrap.obx',
        'types': [
            {'name': 'Fk', 'obo': 'Conn', 'release': 'ff_release', 'release_result': 'void'},
            {'name': 'FkBad', 'obo': 'Bad', 'release': 'ff_bad_release', 'release_result': 'int'},
        ],
        'functions': [
            {'name': 'ff_new', 'symbol': 'ff_new', 'args': ['i32@0'], 'ret': 'own<Fk>',
             'effects': ['impure']},
            {'name': 'ff_new_opt', 'symbol': 'ff_new_opt', 'args': ['i32@0'],
             'ret': 'own_opt<Fk>', 'effects': ['impure']},
            {'name': 'ff_get', 'symbol': 'ff_get', 'args': ['borrow<Fk>@0'], 'ret': 'i32',
             'effects': ['impure']},
            {'name': 'ff_mark', 'symbol': 'ff_mark', 'args': ['borrow<Fk>@0'], 'ret': 'i32',
             'effects': ['impure']},
            {'name': 'ff_touch', 'symbol': 'ff_touch', 'args': ['borrow_mut<Fk>@0'],
             'ret': 'i32', 'effects': ['impure']},
            {'name': 'ff_bad_new', 'symbol': 'ff_bad_new', 'args': [], 'ret': 'own<FkBad>',
             'effects': ['impure']},
            {'name': 'ff_frees', 'symbol': 'ff_frees', 'args': [], 'ret': 'i32',
             'effects': ['impure']},
        ],
    }


def foreigns(tmp):
    """Stage (d): foreign handles, own/own_opt/borrow, close/is_open and the census."""
    d = os.path.join(tmp, 'foreign_src')
    os.makedirs(d)
    for name in ('foreign_fixture.c', 'foreign_fixture.h'):
        shutil.copy(os.path.join(HERE, name), os.path.join(d, name))
    open(os.path.join(d, 'handles_wrap.obx'), 'w').write(FOREIGN_WRAPPER)
    spec_path = os.path.join(d, 'handles.cbind')
    json.dump(foreign_spec(), open(spec_path, 'w'), indent=2)

    def app(name, body, spec=None, wrapper=None):
        ad = os.path.join(tmp, 'fapp_' + name)
        os.makedirs(ad)
        for n in ('foreign_fixture.c', 'foreign_fixture.h'):
            shutil.copy(os.path.join(HERE, n), os.path.join(ad, n))
        sp = spec or foreign_spec()
        sp = dict(sp)
        sp['sources'] = ['foreign_fixture.c']
        sp['headers'] = ['foreign_fixture.h']
        sp['wrapper'] = 'handles_wrap.obx'
        open(os.path.join(ad, 'handles_wrap.obx'), 'w').write(wrapper or FOREIGN_WRAPPER)
        spath = os.path.join(ad, 'handles.cbind')
        json.dump(sp, open(spath, 'w'), indent=2)
        ap = os.path.join(ad, 'app.obx')
        open(ap, 'w').write('module app\n\nimport "handles.obx"\n\n' + body)
        out = os.path.join(ad, 'out')
        return build(spath, ap, out), out

    p, out = app('main', FOREIGN_APP)
    need(p, 'foreign main build')
    main_out = out
    env = dict(os.environ, OBO_CHECK='1')
    r = need(run([os.path.join(out, 'app')], env=env), 'foreign main run')
    if r.stdout.splitlines() != FOREIGN_WANT:
        raise Fail("foreign output:\n got  %r\n want %r" % (r.stdout.splitlines(), FOREIGN_WANT))
    relocs = open(os.path.join(out, 'native.relocs')).read().splitlines()
    if not any(ln == 'foreign 0 Conn Fk' for ln in relocs):
        raise Fail("foreign sidecar missing the Conn type: %r" % relocs[:4])
    if not any(' ff_new 1 fn int own:Conn ' in ln for ln in relocs):
        raise Fail("own return shape missing from the sidecar")
    if not any(' ff_get 1 fn foreign:Conn i64 ' in ln for ln in relocs):
        raise Fail("borrow shape missing from the sidecar")
    if not any(' obo_ci_foreign_own 4 host i64,i64,i64,i64 i64 ' in ln for ln in relocs):
        raise Fail("host own relocation missing from the sidecar")

    # Use after close: exit 7 before the C call, traced by the fixture.
    uac = ('fn main() {\n    var a = handles::open(5)\n    a.close()\n'
           '    println(handles::mark(&a))\n}\n')
    p, out = app('uac', uac)
    need(p, 'foreign uac build')
    r = run([os.path.join(out, 'app')], env=dict(os.environ, FF_TRACE='1'))
    if r.returncode != 7 or 'used after close' not in (r.stderr + r.stdout) or 'ff_mark' in r.stderr:
        raise Fail("use after close: want exit 7 with no ff_mark, got %d: %r"
                   % (r.returncode, (r.stderr, r.stdout)))

    # A leaked handle at scope end: exit 8 under OBO_CHECK=1, no release, names the type.
    leak = 'fn main() {\n    var a = handles::open(5)\n    println("leak")\n}\n'
    p, out = app('leak', leak)
    need(p, 'foreign leak build')
    r = run([os.path.join(out, 'app')], env=dict(os.environ, OBO_CHECK='1', FF_TRACE='1'))
    if r.returncode != 8 or 'foreign handles leaked: Conn 1' not in r.stderr or 'ff_release' in r.stderr:
        raise Fail("leak scope: want exit 8 Conn 1 and no release, got %d: %r"
                   % (r.returncode, (r.stderr, r.stdout)))
    r = run([os.path.join(out, 'app')], env=dict(os.environ, FF_TRACE='1'))
    if r.returncode != 0 or 'leaked' in r.stderr:
        raise Fail("leak without OBO_CHECK: want exit 0 and no message, got %d: %r"
                   % (r.returncode, r.stderr))

    # Overwritten and dropped-with-record handles also leak without a release.
    over = ('fn main() {\n    var a = handles::open(1)\n    a = handles::open(2)\n'
            '    println("over")\n}\n')
    p, out = app('over', over)
    need(p, 'foreign overwrite build')
    r = run([os.path.join(out, 'app')], env=dict(os.environ, OBO_CHECK='1', FF_TRACE='1'))
    if r.returncode != 8 or 'foreign handles leaked: Conn 2' not in r.stderr or 'ff_release' in r.stderr:
        raise Fail("overwrite leak: want exit 8 Conn 2, got %d: %r" % (r.returncode, r.stderr))

    rec = ('type Box = ref record { h: handles::Conn }\n\n'
           'fn main() {\n    var b = new Box{h: handles::open(3)}\n    println("rec")\n}\n')
    p, out = app('rec', rec)
    need(p, 'foreign record leak build')
    r = run([os.path.join(out, 'app')], env=dict(os.environ, OBO_CHECK='1', FF_TRACE='1'))
    if r.returncode != 8 or 'foreign handles leaked: Conn 1' not in r.stderr or 'ff_release' in r.stderr:
        raise Fail("record leak: want exit 8 Conn 1, got %d: %r" % (r.returncode, r.stderr))

    # exit(n) and a trap run no census, even with both a leaked fd and a live handle.
    lf = os.path.join(tmp, 'foreign_fd_file')
    open(lf, 'w').write('x')
    ex = ('fn main() {\n'
          '    let f = openFile("' + lf + '", "r")\n'
          '    match f {\n        ok(fd) { }\n'
          '        err(m, c) { println("open failed"); exit(9) }\n    }\n'
          '    var a = handles::open(5)\n'
          '    exit(3)\n}\n')
    p, out = app('exit', ex)
    need(p, 'foreign exit build')
    r = run([os.path.join(out, 'app')], env=dict(os.environ, OBO_CHECK='1'))
    if r.returncode != 3 or 'leaked' in r.stderr:
        raise Fail("exit with a live handle and fd: want exit 3 and no census, got %d: %r"
                   % (r.returncode, r.stderr))
    # A different handle stays live while the closed one traps.
    trap2 = ('fn main() {\n'
             '    var a = handles::open(5)\n'
             '    a.close()\n'
             '    var b = handles::open(6)\n'
             '    println(handles::mark(&a))\n}\n')
    p, out = app('trap', trap2)
    need(p, 'foreign trap build')
    r = run([os.path.join(out, 'app')], env=dict(os.environ, OBO_CHECK='1'))
    if r.returncode != 7 or 'leaked' in r.stderr or 'foreign handles leaked' in r.stderr:
        raise Fail("trap with another handle live: want exit 7 and no census, got %d: %r"
                   % (r.returncode, r.stderr))

    # Forced census underflow: only under OBO_CHECK=1.
    p, out = app('underflow', FOREIGN_APP)
    need(p, 'foreign underflow build')
    r = run([os.path.join(out, 'app')],
            env=dict(os.environ, OBO_CHECK='1', OBO_CENSUS_FAULT='underflow'))
    if r.returncode != 4 or 'census underflow' not in r.stderr:
        raise Fail("census underflow: want exit 4, got %d: %r" % (r.returncode, r.stderr))
    r = run([os.path.join(out, 'app')], env=dict(os.environ, OBO_CENSUS_FAULT='underflow'))
    if r.returncode != 0:
        raise Fail("census fault without OBO_CHECK must be inert, got %d" % r.returncode)

    # Compile negatives through the wrapper.
    compile_neg = [
        ('new', 'fn main() {\n    let c = new handles::Conn{word: 1}\n}\n', 'cannot construct'),
        ('field', 'fn main() {\n    var c = handles::open(1)\n    println(c.word)\n}\n',
         'word is private'),
        ('convert', 'fn main() {\n    var c = handles::open(1)\n    println(int(c))\n}\n',
         'cannot be converted'),
    ]
    for name, body, want in compile_neg:
        p, _ = app('neg_' + name, body)
        if p.returncode == 0 or want not in (p.stderr + p.stdout):
            raise Fail("foreign compile negative %s: want %r, got rc %d: %s"
                       % (name, want, p.returncode, (p.stderr + p.stdout).strip()[-400:]))

    # A native declaration that takes an owned handle is unsupported.
    badwrap = FOREIGN_WRAPPER.replace('native fn ff_get(c: &Conn): int',
                                      'native fn ff_get(c: Conn): int')
    p, _ = app('neg_owned', 'fn main() {}\n', wrapper=badwrap)
    if p.returncode == 0 or 'unsupported type' not in (p.stderr + p.stdout):
        raise Fail("an owned foreign native parameter was not refused: %s"
                   % (p.stderr + p.stdout).strip()[-300:])
    # A &Bad declaration against a borrow<Fk> spec is refused before link.
    badwrap = FOREIGN_WRAPPER.replace('native fn ff_get(c: &Conn): int',
                                      'native fn ff_get(c: &Bad): int')
    badwrap = badwrap.replace('pub fn get(c: &Conn): int { return ff_get(c) }',
                              'pub fn get(c: &Bad): int { return ff_get(c) }')
    p, _ = app('neg_shape', 'fn main() {}\n', wrapper=badwrap)
    if p.returncode == 0 or 'foreign:Bad' not in (p.stderr + p.stdout):
        raise Fail("a mismatched foreign borrow was not refused: %s"
                   % (p.stderr + p.stdout).strip()[-300:])

    # borrow is const T *; borrow_mut is T *. A mismatched constness fails the signature check.
    def one_foreign(name, fn, wrapper, want):
        sp = {'format': 'cinterop-proto/2', 'module': 'handles',
              'platform': 'x86_64-linux-gnu', 'abi': 'sysv-amd64',
              'signature_check': 'header', 'sources': ['foreign_fixture.c'],
              'headers': ['foreign_fixture.h'], 'libs': [], 'cflags': [],
              'types': [{'name': 'Fk', 'obo': 'Conn', 'release': 'ff_release',
                         'release_result': 'void'}],
              'functions': [fn]}
        p, _ = app(name, 'fn main() {}\n', spec=sp, wrapper=wrapper)
        if p.returncode == 0 or want not in (p.stderr + p.stdout):
            raise Fail("foreign constness %s: want %r, got rc %d: %s"
                       % (name, want, p.returncode, (p.stderr + p.stdout).strip()[-400:]))

    one_foreign('const_as_mut',
                {'name': 'ff_get', 'symbol': 'ff_get', 'args': ['borrow_mut<Fk>@0'],
                 'ret': 'i32', 'effects': ['impure']},
                'module handles\npub type Conn = foreign "Fk"\n'
                'native fn ff_get(c: &Conn): int\n'
                'pub fn get(c: &Conn): int { return ff_get(c) }\n', 'incompatible')
    one_foreign('mut_as_const',
                {'name': 'ff_touch', 'symbol': 'ff_touch', 'args': ['borrow<Fk>@0'],
                 'ret': 'i32', 'effects': ['impure']},
                'module handles\npub type Conn = foreign "Fk"\n'
                'native fn ff_touch(c: &Conn): int\n'
                'pub fn touch(c: &Conn): int { return ff_touch(c) }\n', 'incompatible')

    # The foreign-borrow relaxation is narrow: a deep array borrow to a native
    # still gets the conservative refusal.
    deepdir = os.path.join(tmp, 'deep_array')
    os.makedirs(deepdir)
    deepp = os.path.join(deepdir, 'm.obx')
    open(deepp, 'w').write(
        'module m\ntype Box = ref record { a: []int }\n'
        'native fn f(a: &[]int): int\n'
        'fn main() { var b = new Box{a: new []int(0)}\n    println(f(&b.a)) }\n')
    q = run([OBO, deepp, '-o', os.path.join(deepdir, 'out')])
    if q.returncode == 0 or 'points inside something' not in (q.stderr + q.stdout):
        raise Fail("a deep array borrow to a native was not refused: %s"
                   % (q.stderr + q.stdout).strip()[-400:])

    # A wrapper with no close/is_open methods: the sugar consumes its empty argument list.
    nowrap = ('module handles\n\npub type Conn = foreign "Fk"\n\n'
              'native fn ff_new(v: int): Conn\n\n'
              'pub fn open(v: int): Conn { return ff_new(v) }\n')
    sugar = ('fn main() {\n'
             '    let a = handles::open(3)\n'
             '    println("sugar open ", a.is_open())\n'
             '    let ra = a.close()\n'
             '    match ra {\n        ok(v) { println("sugar close ", v) }\n'
             '        err(m, c) { println("sugar err ", m, " ", c) }\n    }\n'
             '    println("sugar closed ", a.is_open())\n'
             '    let b = handles::open(4)\n'
             '    let rb = __foreignClose(b)\n'
             '    match rb {\n        ok(v) { println("builtin close ", v) }\n'
             '        err(m, c) { println("builtin err ", m, " ", c) }\n    }\n'
             '    println("builtin closed ", b.is_open())\n'
             '    let hc = handles::open(7)\n'
             '    let hh = handle(hc)\n'
             '    println("handle sugar ", hh.is_open())\n'
             '    let rh = hh.close()\n'
             '    match rh {\n        ok(v) { println("handle sugar close ", v) }\n'
             '        err(m, c) { println("handle sugar err ", m, " ", c) }\n    }\n'
             '    println("handle sugar closed ", hc.is_open())\n'
             '    let hc2 = handles::open(8)\n'
             '    let hh2 = handle(hc2)\n'
             '    println("hbuiltin open ", hh2.is_open())\n'
             '    let rh2 = __foreignClose(hh2)\n'
             '    match rh2 {\n        ok(v) { println("hbuiltin close ", v) }\n'
             '        err(m, c) { println("hbuiltin err ", m, " ", c) }\n    }\n'
             '    println("hbuiltin closed ", hc2.is_open())\n'
             '}\n')
    sugarspec = {'format': 'cinterop-proto/2', 'module': 'handles',
                 'platform': 'x86_64-linux-gnu', 'abi': 'sysv-amd64',
                 'signature_check': 'header', 'sources': ['foreign_fixture.c'],
                 'headers': ['foreign_fixture.h'], 'libs': [], 'cflags': [],
                 'types': [{'name': 'Fk', 'obo': 'Conn', 'release': 'ff_release',
                            'release_result': 'void'}],
                 'functions': [{'name': 'ff_new', 'symbol': 'ff_new', 'args': ['i32@0'],
                                'ret': 'own<Fk>', 'effects': ['impure']}]}
    p, out = app('sugar', sugar, spec=sugarspec, wrapper=nowrap)
    need(p, 'foreign sugar build')
    r = need(run([os.path.join(out, 'app')], env=dict(os.environ, OBO_CHECK='1')), 'foreign sugar run')
    want = ['sugar open true', 'sugar close true', 'sugar closed false',
            'builtin close true', 'builtin closed false',
            'handle sugar true', 'handle sugar close true', 'handle sugar closed false',
            'hbuiltin open true', 'hbuiltin close true', 'hbuiltin closed false']
    if r.stdout.splitlines() != want:
        raise Fail("foreign sugar output %r, want %r" % (r.stdout.splitlines(), want))
    # The method path and the builtin both refuse a temporary, with the ordinary message.
    # The sugar path (no declared methods), the declared-method path, and the builtin all refuse a
    # temporary with the same message.
    for name, body, sp, wr in (
            ('temp_sugar', 'fn main() { handles::open(1).close() }\n', sugarspec, nowrap),
            ('temp_method', 'fn main() { handles::open(1).close() }\n', foreign_spec(), FOREIGN_WRAPPER),
            ('temp_builtin', 'fn main() { __foreignClose(handles::open(1)) }\n', sugarspec, nowrap)):
        p, _ = app(name, body, spec=sp, wrapper=wr)
        if p.returncode == 0 or 'store this value in a variable first' not in (p.stderr + p.stdout):
            raise Fail("foreign %s: want the temporary refusal, got rc %d: %s"
                       % (name, p.returncode, (p.stderr + p.stdout).strip()[-300:]))

    # Tool negatives: a sidecar type mismatch, an unknown host helper and the host arity.
    tool = _tool()
    good = open(os.path.join(main_out, 'native.relocs')).read().splitlines()
    spec = foreign_spec()
    bad_rel = os.path.join(tmp, 'foreign_bad_type.relocs')
    open(bad_rel, 'w').write('\n'.join(['cinterop-relocs 2', 'foreign 0 Conn NotFk'] + good[2:]) + '\n')
    try:
        tool.validate_calls(tool.read_relocs(bad_rel), spec)
    except tool.ToolError as e:
        if 'sidecar' not in str(e) and 'types' not in str(e):
            raise Fail("foreign type tamper: unexpected error %s" % e)
    else:
        raise Fail("a foreign type tamper was accepted")
    hb = os.path.join(tmp, 'foreign_bad_host.relocs')
    lines = [ln for ln in good if ' host ' not in ln]
    for ln in good:
        if ' obo_ci_foreign_own ' in ln:
            lines.append(ln.replace(' obo_ci_foreign_own 4 ', ' obo_ci_foreign_own 3 ', 1))
            break
    open(hb, 'w').write('\n'.join(lines) + '\n')
    try:
        tool.read_relocs(hb)
    except tool.ToolError as e:
        if 'needs 4 argument' not in str(e):
            raise Fail("host arity tamper: unexpected error %s" % e)
    else:
        raise Fail("a host arity tamper was accepted")
    hu = os.path.join(tmp, 'foreign_unknown_host.relocs')
    open(hu, 'w').write('\n'.join([good[0], good[1], '50 obo_ci_nope 1 host - i64 freesall,writeshandles']) + '\n')
    try:
        tool.read_relocs(hu)
    except tool.ToolError as e:
        if 'unknown host helper' not in str(e):
            raise Fail("unknown host helper: unexpected error %s" % e)
    else:
        raise Fail("an unknown host helper was accepted")

    # An app imports the wrapper from its .sym only: the type is opaque (no C name, no private
    # word name) and close/is_open type-check through the exported methods. --check is the only
    # mode that can use a .sym without the wrapper source; a full build needs that source.
    ck = os.path.join(tmp, 'foreign_check')
    os.makedirs(ck)
    shutil.copy(os.path.join(main_out, 'sym', 'handles.sym'), os.path.join(ck, 'handles.sym'))
    symtext = open(os.path.join(ck, 'handles.sym')).read()
    if 'Fk' in symtext or 'Fk2' in symtext:
        raise Fail("the exported foreign type leaks its C name into the .sym")
    if 'priv' not in symtext:
        raise Fail("the exported foreign type is not an opaque record with a priv field")
    if 'close' not in symtext or 'is_open' not in symtext:
        raise Fail("the wrapper's close/is_open methods are not exported")
    ap = os.path.join(ck, 'app.obx')
    open(ap, 'w').write('module app\n\nimport "handles.obx"\n\n' + FOREIGN_APP)
    q = run([OBO, ap, '--check', '--sym-dir', ck])
    if q.returncode != 0:
        raise Fail("--check of an app importing only a foreign wrapper .sym failed: %s"
                   % (q.stderr + q.stdout).strip()[-500:])
    print('foreign tests: ok')


# Null and const slots take the C type from the spec's ctypes annotation (sig_params), so they are checked
# against the header exactly. These are the cases the design needs: a NULL for a const char *, a NULL for a
# struct pointer, const:-1 as a function-pointer destructor, and const:5 for an int.
NC_POS = [
    {'name': 'nc_cstr_null', 'symbol': 'nc_cstr_null', 'args': ['null'], 'ctypes': ['const char *']},
    {'name': 'nc_struct_null', 'symbol': 'nc_struct_null', 'args': ['null'], 'ctypes': ['struct foo *']},
    {'name': 'nc_destructor', 'symbol': 'nc_destructor', 'args': ['borrow_cstr@0', 'const:-1'],
     'ctypes': [None, 'void (*)(void *)']},
    {'name': 'nc_int_const', 'symbol': 'nc_int_const', 'args': ['const:5'], 'ctypes': ['int']},
    {'name': 'nc_uint', 'symbol': 'nc_uint', 'args': ['const:4294967295'], 'ctypes': ['unsigned int']},
]

NC_POS_WRAP = """module nullconst

pub fn cstr_null(): int {
    return __cinterop("nc_cstr_null", "null")
}

pub fn struct_null(): int {
    return __cinterop("nc_struct_null", "null")
}

pub fn destructor(s: string): int {
    return __cinterop("nc_destructor", "borrow_cstr@0,const:-1", s)
}

pub fn int_const(): int {
    return __cinterop("nc_int_const", "const:5")
}

pub fn uint_max(): int {
    return __cinterop("nc_uint", "const:4294967295")
}
"""

NC_APP = """fn main() {
    println("cstr_null ", nullconst::cstr_null())
    println("struct_null ", nullconst::struct_null())
    println("destructor ", nullconst::destructor("hello"))
    println("int_const ", nullconst::int_const())
    println("uint_max ", nullconst::uint_max())
}
"""

NC_BAD = [
    ('ptr_mismatch', [{'name': 'nc_bytes_wrong', 'symbol': 'nc_bytes_wrong', 'args': ['borrow_bytes@0'],
                       'ctypes': [None]}],
     'pub fn bytes_wrong(b: &[]byte): int {\n    return __cinterop("nc_bytes_wrong", "borrow_bytes@0", b)\n}\n',
     'incompatible'),
    ('struct_swap', [{'name': 'nc_struct_null_bad', 'symbol': 'nc_struct_null_bad', 'args': ['null'],
                      'ctypes': ['struct bar *']}],
     'pub fn struct_null_bad(): int {\n    return __cinterop("nc_struct_null_bad", "null")\n}\n',
     'incompatible'),
    ('null_no_ctype', [{'name': 'nc_struct_null', 'symbol': 'nc_struct_null', 'args': ['null']}],
     'pub fn struct_null(): int {\n    return __cinterop("nc_struct_null", "null")\n}\n',
     'needs its C parameter type'),
    ('struct_by_value', [{'name': 'nc_struct_null', 'symbol': 'nc_struct_null', 'args': ['null'],
                          'ctypes': ['struct foo']}],
     'pub fn struct_null(): int {\n    return __cinterop("nc_struct_null", "null")\n}\n',
     'not a pointer or an integer type'),
    # One C type only: a comma, unbalanced parentheses, a trailing declarator name, two types.
    ('comma_injection', [{'name': 'nc_two', 'symbol': 'nc_two', 'args': ['null'],
                          'ctypes': ['void *, void *']}],
     'pub fn two(): int {\n    return __cinterop("nc_two", "null")\n}\n',
     'comma outside a function parameter list'),
    ('unbalanced_parens', [{'name': 'nc_destructor', 'symbol': 'nc_destructor',
                            'args': ['borrow_cstr@0', 'const:-1'], 'ctypes': [None, 'void (*)(void *']}],
     'pub fn destructor(s: string): int {\n    return __cinterop("nc_destructor", "borrow_cstr@0,const:-1", s)\n}\n',
     'unbalanced parentheses'),
    ('trailing_declarator', [{'name': 'nc_struct_null', 'symbol': 'nc_struct_null', 'args': ['null'],
                              'ctypes': ['struct foo *p']}],
     'pub fn struct_null(): int {\n    return __cinterop("nc_struct_null", "null")\n}\n',
     'declarator is not allowed'),
    ('two_types', [{'name': 'nc_struct_null', 'symbol': 'nc_struct_null', 'args': ['null'],
                    'ctypes': ['void int']}],
     'pub fn struct_null(): int {\n    return __cinterop("nc_struct_null", "null")\n}\n',
     'two C types'),
    ('int_for_long', [{'name': 'nc_int_long', 'symbol': 'nc_int_long', 'args': ['null'],
                       'ctypes': ['int']}],
     'pub fn int_long(): int {\n    return __cinterop("nc_int_long", "null")\n}\n',
     'incompatible'),
    # Const values must fit the ctype by width and sign; a pointer ctype takes any 64-bit value.
    ('int_const_overflow', [{'name': 'nc_int_const', 'symbol': 'nc_int_const', 'args': ['const:4294967296'],
                             'ctypes': ['int']}],
     'pub fn int_const(): int {\n    return __cinterop("nc_int_const", "const:4294967296")\n}\n',
     'does not fit int'),
    ('uint_const_overflow', [{'name': 'nc_uint', 'symbol': 'nc_uint', 'args': ['const:4294967296'],
                              'ctypes': ['unsigned int']}],
     'pub fn uint_max(): int {\n    return __cinterop("nc_uint", "const:4294967296")\n}\n',
     'does not fit unsigned int'),
    ('uint_const_negative', [{'name': 'nc_uint', 'symbol': 'nc_uint', 'args': ['const:-1'],
                              'ctypes': ['unsigned int']}],
     'pub fn uint_max(): int {\n    return __cinterop("nc_uint", "const:-1")\n}\n',
     'does not fit unsigned int'),
    ('const_too_many_digits', [{'name': 'nc_int_const', 'symbol': 'nc_int_const',
                                'args': ['const:100000000000000000'], 'ctypes': ['int']}],
     'pub fn int_const(): int {\n    return __cinterop("nc_int_const", "const:100000000000000000")\n}\n',
     'at most 17 digits'),
    # ctypes is for argument kinds; an int-form binding has no null or const slot.
    ('int_form_ctypes', [{'name': 'nc_int_long', 'symbol': 'nc_int_long', 'args': ['i64'],
                          'ctypes': ['long']}],
     'pub fn int_long(v: int): int {\n    return __cinterop("nc_int_long", v)\n}\n',
     'int-form binding'),
]

STACK_FUNCS = [
    ('f7', ['i64@0', 'borrow_cstr@1', 'ints@2:i32', 'ints@2:len', 'nil_bytes_list@3', 'ints@3:len']),
    ('f8', ['i64@0', 'borrow_cstr@1', 'ints@2:i32', 'ints@2:len', 'nil_bytes_list@3', 'ints@3:len', 'i32@4']),
    ('f9', ['i64@0', 'borrow_cstr@1', 'ints@2:i32', 'ints@2:len', 'nil_bytes_list@3', 'ints@3:len',
            'borrow_bytes@4']),
    ('f10', ['i64@0', 'borrow_cstr@1', 'ints@2:i32', 'ints@2:len', 'nil_bytes_list@3', 'ints@3:len',
             'borrow_bytes@4', 'i32@5']),
    ('f11', ['i64@0', 'borrow_cstr@1', 'ints@2:i32', 'ints@2:len', 'nil_bytes_list@3', 'ints@3:len',
             'borrow_bytes@4', 'i32@5', 'u32@6']),
]

STACK_APP = """module app

import "stackargs.obx"

fn main() {
    var iv = new []int(0)
    append(iv, 10)
    append(iv, -20)
    append(iv, 2147483647)
    var l = new []?[]byte(0)
    append(l, nil)
    append(l, new []byte(0))
    var ab2 = new []byte(0)
    append(ab2, byte(97))
    append(ab2, byte(0))
    append(ab2, byte(98))
    append(l, ab2)
    var ab = new []byte(0)
    append(ab, byte(97))
    append(ab, byte(0))
    append(ab, byte(98))
    println("r7 ", stackargs::f7(42, "hello", &iv, &l))
    println("r8 ", stackargs::f8(42, "hello", &iv, &l, -7))
    println("r9 ", stackargs::f9(42, "hello", &iv, &l, "xyz"))
    println("r10 ", stackargs::f10(42, "hello", &iv, &l, &ab, -2147483648))
    println("r11 ", stackargs::f11(42, "hello", &iv, &l, &ab, 2147483647, 4294967295))
}
"""

# The callees flush their own lines as they run; obo's println output is buffered to exit.
STACK_WANT = [
    'f7 al=1 a=42 s=hello iv=[10,-20,2147483647] m=3 l=[nil,0:,3:610062]',
    'f8 al=1 a=42 s=hello iv=[10,-20,2147483647] m=3 l=[nil,0:,3:610062] x=-7',
    'f9 al=1 a=42 s=hello iv=[10,-20,2147483647] m=3 l=[nil,0:,3:610062] b=3:78797a',
    'f10 al=1 a=42 s=hello iv=[10,-20,2147483647] m=3 l=[nil,0:,3:610062] b=3:610062 x=-2147483648',
    'f11 al=1 a=42 s=hello iv=[10,-20,2147483647] m=3 l=[nil,0:,3:610062] b=3:610062 x=2147483647 u=4294967295',
    'r7 7', 'r8 8', 'r9 9', 'r10 10', 'r11 11',
]


def fixture_spec(module, funcs, sources, headers, wrapper, cflags=()):
    return {'format': 'cinterop-proto/1', 'module': module, 'platform': 'x86_64-linux-gnu',
            'abi': 'sysv-amd64', 'sources': sources, 'headers': headers, 'libs': [],
            'cflags': list(cflags), 'wrapper': wrapper,
            'functions': [dict({'ret': 'i64', 'effects': ['impure']}, **f) for f in funcs]}


def stack_args_funcs():
    return [{'name': n, 'symbol': n, 'args': a} for n, a in STACK_FUNCS]


def null_const_slots(tmp):
    """Null and const slots type from ctypes, checked against the header exactly; a genuine
    mismatch on a normal pointer slot or a struct swap is still refused before link."""
    d = os.path.join(tmp, 'nullconst_src')
    os.makedirs(d)
    for name in ('nullconst_fixture.c', 'nullconst_fixture.h'):
        shutil.copy(os.path.join(HERE, name), os.path.join(d, name))
    open(os.path.join(d, 'nullconst_wrap.obx'), 'w').write(NC_POS_WRAP)
    sp = os.path.join(d, 'nullconst.cbind')
    json.dump(fixture_spec('nullconst', NC_POS, ['nullconst_fixture.c'], ['nullconst_fixture.h'],
                           'nullconst_wrap.obx'), open(sp, 'w'), indent=2)
    ad = os.path.join(d, 'app.obx')
    open(ad, 'w').write('module app\n\nimport "nullconst.obx"\n\n' + NC_APP)
    out = os.path.join(d, 'out')
    need(build(sp, ad, out), 'null/const slot build')
    p = need(run([os.path.join(out, 'app')]), 'null/const slot run')
    want = ['cstr_null 1', 'struct_null 1', 'destructor 105', 'int_const 5', 'uint_max 4294967295']
    if p.stdout.splitlines() != want:
        raise Fail("null/const slot output %r, want %r" % (p.stdout.splitlines(), want))

    for name, funcs, wrap, want_text in NC_BAD:
        nd = os.path.join(tmp, 'ncneg_' + name)
        os.makedirs(nd)
        for n in ('nullconst_fixture.c', 'nullconst_fixture.h'):
            shutil.copy(os.path.join(HERE, n), os.path.join(nd, n))
        open(os.path.join(nd, 'nullconst_wrap.obx'), 'w').write('module nullconst\n\n' + wrap)
        nsp = os.path.join(nd, 'nullconst.cbind')
        json.dump(fixture_spec('nullconst', funcs, ['nullconst_fixture.c'], ['nullconst_fixture.h'],
                               'nullconst_wrap.obx'), open(nsp, 'w'), indent=2)
        nad = os.path.join(nd, 'app.obx')
        open(nad, 'w').write('module app\n\nimport "nullconst.obx"\n\nfn main() {}\n')
        nout = os.path.join(nd, 'out')
        q = build(nsp, nad, nout)
        if q.returncode == 0 or want_text not in (q.stderr + q.stdout):
            raise Fail("null/const negative %s: want refusal with %r, got rc %d: %s"
                       % (name, want_text, q.returncode, (q.stderr + q.stdout).strip()[-400:]))
        if os.path.exists(os.path.join(nout, 'app')):
            raise Fail("null/const negative %s linked an executable" % name)
    print('null/const slot tests: ok')


def stack_args(tmp):
    """Seven to eleven C parameters: stack-passed arguments with odd and even counts. Each callee
    checks it is entered with a 16-byte aligned frame, and every value arrives unchanged."""
    d = os.path.join(tmp, 'stack_args_src')
    os.makedirs(d)
    for name in ('stack_args_fixture.c', 'stack_args_fixture.h', 'stack_args_wrap.obx'):
        shutil.copy(os.path.join(HERE, name), os.path.join(d, name))
    sp = os.path.join(d, 'stackargs.cbind')
    json.dump(fixture_spec('stackargs', stack_args_funcs(), ['stack_args_fixture.c'],
                           ['stack_args_fixture.h'], 'stack_args_wrap.obx',
                           cflags=['-fno-omit-frame-pointer']), open(sp, 'w'), indent=2)
    ad = os.path.join(d, 'app.obx')
    open(ad, 'w').write(STACK_APP)
    out = os.path.join(d, 'out')
    need(build(sp, ad, out), 'stack args build')
    p = need(run([os.path.join(out, 'app')]), 'stack args run')
    if p.stdout.splitlines() != STACK_WANT:
        raise Fail("stack args output:\n got  %r\n want %r" % (p.stdout.splitlines(), STACK_WANT))
    print('stack-argument tests: ok')


def out_spec():
    return {
        'format': 'cinterop-proto/2', 'module': 'outnat',
        'platform': 'x86_64-linux-gnu', 'abi': 'sysv-amd64',
        'signature_check': 'header',
        'sources': ['out_fixture.c'], 'headers': ['out_fixture.h'],
        'libs': [], 'cflags': [], 'wrapper': 'out_wrap.obx',
        'types': [{'name': 'Of', 'obo': 'Obj', 'release': 'of_release',
                   'release_result': 'void'}],
        'functions': [
            {'name': 'of_new', 'symbol': 'of_new', 'args': ['i32@0', 'out_own<Of>'],
             'ret': 'i32', 'effects': ['impure']},
            {'name': 'of_zero', 'symbol': 'of_zero', 'args': ['out_own<Of>'],
             'ret': 'i32', 'effects': ['impure']},
            {'name': 'of_new_fail', 'symbol': 'of_new_fail',
             'args': ['i32@0', 'out_own<Of>'], 'ret': 'i32', 'effects': ['impure']},
            {'name': 'of_new_null', 'symbol': 'of_new_null',
             'args': ['i32@0', 'out_own<Of>'], 'ret': 'i32', 'effects': ['impure']},
            {'name': 'of_wide', 'symbol': 'of_wide',
             'args': ['i32@0', 'i32@1', 'i32@2', 'i32@3', 'i32@4', 'i32@5',
                      'out_own<Of>'], 'ret': 'i32', 'effects': ['impure']},
            {'name': 'of_open', 'symbol': 'of_open',
             'args': ['borrow_cstr@0', 'out_own<Of>', 'i32@1', 'null'],
             'ctypes': [None, None, None, 'const char *'],
             'ret': 'i32', 'effects': ['impure']},
            {'name': 'of_prepare', 'symbol': 'of_prepare',
             'args': ['borrow_mut<Of>@0', 'borrow_bytes@1', 'out_own<Of>',
                      'out_offset@1'], 'ret': 'i32', 'effects': ['impure']},
            {'name': 'of_tail', 'symbol': 'of_tail',
             'args': ['borrow_bytes@0', 'i32@1', 'out_offset@0'],
             'ret': 'i32', 'effects': ['impure']},
            {'name': 'of_bind', 'symbol': 'of_bind',
             'args': ['borrow_bytes@0', 'const:-1', 'out_own<Of>'],
             'ctypes': [None, 'void (*)(void *)', None],
             'ret': 'i32', 'effects': ['impure']},
            {'name': 'of_get', 'symbol': 'of_get', 'args': ['borrow<Of>@0'],
             'ret': 'i32', 'effects': ['impure']},
            {'name': 'of_reset', 'symbol': 'of_reset', 'args': [], 'ret': 'void',
             'effects': ['impure']},
            {'name': 'of_live', 'symbol': 'of_live', 'args': [], 'ret': 'i64',
             'effects': ['impure']},
            {'name': 'of_closed', 'symbol': 'of_closed', 'args': [], 'ret': 'i64',
             'effects': ['impure']},
            {'name': 'of_last_dtor', 'symbol': 'of_last_dtor', 'args': [], 'ret': 'i64',
             'effects': ['impure']},
            {'name': 'of_last_len', 'symbol': 'of_last_len', 'args': [], 'ret': 'i64',
             'effects': ['impure']},
        ],
    }


OUT_APP = '''fn main() {
    outnat::reset()
    var r = outnat::open(5)
    match r {
        ok(h) {
            println("open ", outnat::get(&h), " ", h.is_open())
            h.close()
            println("closed ", h.is_open())
        }
        err(m, c) { println("open err ", m, " ", c) }
    }
    println("counts ", outnat::live(), " ", outnat::releases())
    println("fail ", outnat::open_fail(7), " ", outnat::live(), " ", outnat::releases())
    println("null ", outnat::open_null(9), " ", outnat::live(), " ", outnat::releases())
    var w = outnat::wide(1, 2, 3, 4, 5, 6)
    match w {
        ok(h) { println("wide ", outnat::get(&h)); h.close() }
        err(m, c) { println("wide err ", m, " ", c) }
    }
    var pb = new []byte(0)
    append(pb, byte(97))
    append(pb, byte(98))
    append(pb, byte(99))
    var o = outnat::open_shape(&pb, 7)
    match o {
        ok(h) {
            println("shape ", outnat::get(&h))
            var sql = new []byte(0)
            append(sql, byte(97))
            append(sql, byte(98))
            append(sql, byte(99))
            append(sql, byte(100))
            append(sql, byte(101))
            println("ptail ", outnat::prepare_tail(&h, &sql))
            var one = new []byte(0)
            append(one, byte(120))
            println("ptail1 ", outnat::prepare_tail(&h, &one))
            println("tail ", outnat::tail(&pb, 0), " ", outnat::tail(&pb, 1),
                    " ", outnat::tail(&pb, 2), " ", outnat::tail(&pb, 3),
                    " ", outnat::tail(&pb, 4), " ", outnat::tail(&pb, 5))
            var empty = new []byte(0)
            println("empty ", outnat::tail(&empty, 0), " ", outnat::tail(&empty, 1),
                    " ", outnat::tail(&empty, 2))
            h.close()
        }
        err(m, c) { println("shape err ", m, " ", c) }
    }
    var bnd = outnat::bind(&pb)
    match bnd {
        ok(h) { println("bind ", outnat::last_dtor(), " ", outnat::last_len(),
                        " ", outnat::get(&h)); h.close() }
        err(m, c) { println("bind err ", m, " ", c) }
    }
    var zr = outnat::open_zero()
    match zr {
        ok(h) { println("zero ", outnat::get(&h)); h.close() }
        err(m, c) { println("zero err ", m, " ", c) }
    }
    outnat::close_drop(11)
    println("flow ", outnat::flow(1, 3))
    println("final ", outnat::live(), " ", outnat::releases())
}
'''

OUT_WANT = [
    'open 5 true',
    'closed false',
    'counts 0 1',
    'fail 1 0 2',
    'null 4 0 2',
    'wide 21',
    'shape 7',
    'ptail 2',
    'ptail1 1',
    'tail 2 0 3 -1 -1 -1',
    'empty -1 0 0',
    'bind -1 3 3',
    'zero 0',
    'flow 6',
    'final 0 12',
]


def out_params(tmp):
    """Stage (f): out_own/out_offset, the generated out record, and its sidecar."""
    def app(name, body, wrapper=None):
        ad = os.path.join(tmp, 'outapp_' + name)
        os.makedirs(ad)
        for n in ('out_fixture.c', 'out_fixture.h'):
            shutil.copy(os.path.join(HERE, n), os.path.join(ad, n))
        sp = out_spec()
        sp['sources'] = ['out_fixture.c']
        sp['headers'] = ['out_fixture.h']
        sp['wrapper'] = 'out_wrap.obx'
        open(os.path.join(ad, 'out_wrap.obx'), 'w').write(wrapper or open(
            os.path.join(HERE, 'out_wrap.obx')).read())
        spath = os.path.join(ad, 'out.cbind')
        json.dump(sp, open(spath, 'w'), indent=2)
        ap = os.path.join(ad, 'app.obx')
        open(ap, 'w').write('module app\n\nimport "outnat.obx"\n\n' + body)
        out = os.path.join(ad, 'out')
        return build(spath, ap, out), out, ad

    p, out, _ = app('main', OUT_APP)
    need(p, 'out main build')
    r = need(run([os.path.join(out, 'app')], env=dict(os.environ, OBO_CHECK='1')),
             'out main run')
    if r.stdout.splitlines() != OUT_WANT:
        raise Fail("out main output:\n got  %r\n want %r" % (r.stdout.splitlines(), OUT_WANT))
    if r.stderr.strip():
        raise Fail("out main wrote to stderr: %r" % r.stderr)
    relocs = open(os.path.join(out, 'native.relocs')).read().splitlines()
    if 'outrec of_new of_new_out rc:int o:own:Obj' not in relocs:
        raise Fail("out_own outrec line missing: %r" % relocs[:8])
    if 'outrec of_zero of_zero_out rc:int o:own:Obj' not in relocs:
        raise Fail("zero-input out_own outrec line missing: %r" % relocs[:12])
    if 'outrec of_prepare of_prepare_out rc:int st:own:Obj tail:offset:1' not in relocs:
        raise Fail("out_offset outrec line missing or out of order: %r" % relocs[:10])
    if any(ln.startswith('outrec of_get ') for ln in relocs):
        raise Fail("a native with no out parameters wrote an outrec line")
    if not any(' of_new 1 fn int outrec:of_new_out ' in ln for ln in relocs):
        raise Fail("out_own return shape missing from the sidecar")
    if not any(' of_prepare 2 fn foreign:Obj,bytes outrec:of_prepare_out ' in ln for ln in relocs):
        raise Fail("out_offset return shape missing from the sidecar")
    if not any(' of_bind 1 fn bytes outrec:of_bind_out ' in ln for ln in relocs):
        raise Fail("const:-1 out binding missing from the sidecar")
    # Old tools reject the new sidecar: a v1 reader sees an unknown relocation line.
    tool = _tool()
    try:
        tool._read_relocs_v1(relocs[1:])
    except tool.ToolError:
        pass
    else:
        raise Fail("the old sidecar reader accepted an outrec line")

    # out_own dropped: the nested record is freed, the C object is not. Exit 8.
    leak = ('fn main() {\n    outnat::reset()\n    outnat::leak(3)\n'
            '    println("live ", outnat::live())\n}\n')
    p, out, _ = app('leak', leak)
    need(p, 'out leak build')
    r = run([os.path.join(out, 'app')], env=dict(os.environ, OBO_CHECK='1'))
    if r.returncode != 8 or 'foreign handles leaked: Obj 1' not in r.stderr:
        raise Fail("out_own dropped: want exit 8 Obj 1, got %d: %r"
                   % (r.returncode, (r.stderr, r.stdout)))
    r = run([os.path.join(out, 'app')])
    if r.returncode != 0 or 'leaked' in r.stderr:
        raise Fail("out_own dropped without OBO_CHECK: want exit 0, got %d: %r"
                   % (r.returncode, r.stderr))

    # The wrapper closes the handle, then drops the record: no census.
    closed = ('fn main() {\n    outnat::reset()\n    outnat::close_drop(4)\n'
              '    println("c ", outnat::live(), " ", outnat::releases())\n}\n')
    p, out, _ = app('closed', closed)
    need(p, 'out closed build')
    r = need(run([os.path.join(out, 'app')], env=dict(os.environ, OBO_CHECK='1')),
             'out closed run')
    if r.stdout.strip() != 'c 0 1' or r.stderr.strip():
        raise Fail("wrapper-closed record: want 'c 0 1', got %r %r"
                   % (r.stdout, r.stderr))

    # An out_offset-only var is an ordinary owned record, freed at scope end.
    only = ('fn main() {\n    outnat::reset()\n    var pb = new []byte(0)\n'
            '    append(pb, byte(97))\n    append(pb, byte(98))\n    append(pb, byte(99))\n'
            '    println("t ", outnat::tail(&pb, 0))\n}\n')
    p, out, _ = app('only', only)
    need(p, 'out only build')
    r = need(run([os.path.join(out, 'app')], env=dict(os.environ, OBO_CHECK='1')),
             'out only run')
    if r.stdout.strip() != 't 2' or r.stderr.strip():
        raise Fail("out_offset-only record: want 't 2' and no census, got %r %r"
                   % (r.stdout, r.stderr))

    # Compile negatives: a bare out-call statement, a global initializer, and defer.
    base_wrap = open(os.path.join(HERE, 'out_wrap.obx')).read()
    for name, extra, want in (
            ('bare', '\npub fn bad() { of_new(1) }\n',
             'store an out-native result'),
            ('global', '\nvar g = of_new(1)\n',
             'global initializer'),
            ('defer', '\npub fn bad_defer(v: int) { defer of_new(v) }\n',
             'out-native result')):
        p, _, _ = app('neg_' + name, 'fn main() {}\n', wrapper=base_wrap + extra)
        if p.returncode == 0 or want not in (p.stderr + p.stdout):
            raise Fail("out compile negative %s: want %r, got rc %d: %s"
                       % (name, want, p.returncode, (p.stderr + p.stdout).strip()[-500:]))

    # The generated <native>_out name is reserved in both declaration orders.
    for name, wrap, want in (
            ('collide_before',
             base_wrap.replace('module outnat\n',
                               'module outnat\n\ntype of_new_out = ref record { x: int }\n',
                               1),
             'already declared'),
            ('collide_after',
             base_wrap + '\ntype of_new_out = ref record { x: int }\n',
             'reserved by the generated out record')):
        p, _, _ = app('neg_' + name, 'fn main() {}\n', wrapper=wrap)
        if p.returncode == 0 or want not in (p.stderr + p.stdout):
            raise Fail("out name collision %s: want %r, got rc %d: %s"
                       % (name, want, p.returncode, (p.stderr + p.stdout).strip()[-400:]))

    # --check parses an out declaration but refuses a native call.
    ckdir = os.path.join(tmp, 'out_check')
    os.makedirs(ckdir)
    open(os.path.join(ckdir, 'out_wrap.obx'), 'w').write(base_wrap)
    q = run([OBO, os.path.join(ckdir, 'out_wrap.obx'), '--check', '--sym-dir', ckdir])
    if q.returncode == 0 or 'cannot be called in --check' not in (q.stderr + q.stdout):
        raise Fail("--check with an out native call: want a refusal, got rc %d: %s"
                   % (q.returncode, (q.stderr + q.stdout).strip()[-300:]))

    # Tool negatives and tampered sidecars.
    tool = _tool()
    spec = out_spec()
    good = open(os.path.join(out, 'native.relocs')).read().splitlines()

    def reject(lines, want):
        path = os.path.join(tmp, 'out_tamper_%d.relocs' % len(os.listdir(tmp)))
        open(path, 'w').write('\n'.join(lines) + '\n')
        try:
            tool.validate_calls(tool.read_relocs(path), spec)
        except tool.ToolError as e:
            if want is not None and want not in str(e):
                raise Fail("out tamper %r: unexpected error %s" % (want, e))
        else:
            raise Fail("an out tamper was accepted: %r" % want)

    drop_new = [ln for ln in good if not ln.startswith('outrec of_new ')]
    reject(drop_new, 'outrec')
    reject([ln.replace('outrec of_new of_new_out', 'outrec of_new other_out')
            for ln in good], 'must be named')
    reject([ln.replace('rc:int o:own:Obj', 'o:own:Obj rc:int')
            for ln in good], 'rc:int')
    reject([ln.replace('o:own:Obj', 'o:own:Obj o:own:Obj') for ln in good], 'duplicate')
    reject(good + ['outrec of_new of_new_out rc:int o:own:Obj'], 'duplicate out record')
    reject([ln.replace('o:own:Obj', 'o:own:Nope') for ln in good], 'not in the sidecar')
    reject([ln.replace('rc:int st:own:Obj tail:offset:1',
                       'rc:int tail:offset:1 st:own:Obj') for ln in good], 'spec needs')
    reject([ln.replace('of_new 1 fn int outrec:of_new_out',
                       'of_new 1 fn int i64') for ln in good], 'spec needs')
    reject([ln.replace('of_new 1 fn int outrec:of_new_out',
                       'of_new 1 call - i64') for ln in good], 'typed native fn')

    # Spec-level refusals: out with a non-scalar result, out in __cinterop's shape, bad ordering.
    for bad_ret in ('void', 'own<Of>'):
        sp = out_spec()
        sp['functions'] = [{'name': 'of_new', 'symbol': 'of_new',
                            'args': ['i32@0', 'out_own<Of>'], 'ret': bad_ret,
                            'effects': ['impure']}]
        spath = os.path.join(tmp, 'out_bad_ret.cbind')
        json.dump(sp, open(spath, 'w'), indent=2)
        try:
            tool.load_spec(spath)
        except tool.ToolError as e:
            if 'scalar C result' not in str(e):
                raise Fail("out ret %r: unexpected error %s" % (bad_ret, e))
        else:
            raise Fail("out ret %r was accepted" % bad_ret)
    for kinds, want in (
            (['i32@0', 'out_own<Of>', 'out_own<Of>'], 'at most one out_own'),
            (['i32@0', 'out_offset@0'], 'borrow_bytes')):
        try:
            tool.check_kinds('bad', kinds)
        except tool.ToolError as e:
            if want not in str(e):
                raise Fail("out kinds %r: unexpected error %s" % (kinds, e))
        else:
            raise Fail("out kinds %r were accepted" % kinds)
    # const:0 in a function-pointer slot is a use-after-free, and only -1 is accepted.
    try:
        tool.check_ctypes('dtor', ['const:0'], ['void (*)(void *)'])
    except tool.ToolError as e:
        if 'must be -1' not in str(e):
            raise Fail("const:0 function pointer: unexpected error %s" % e)
    else:
        raise Fail("const:0 in a function-pointer slot was accepted")
    tool.check_ctypes('dtor', ['const:-1'], ['void (*)(void *)'])
    try:
        tool.check_ctypes('dtor', ['const:7'], ['void (*)(void *)'])
    except tool.ToolError as e:
        if 'must be -1' not in str(e):
            raise Fail("const:7 function pointer: unexpected error %s" % e)
    else:
        raise Fail("const:7 in a function-pointer slot was accepted")
    try:
        tool.check_kinds('bad', ['borrow_cstr@0', 'out_offset@0'])
    except tool.ToolError as e:
        if 'out_offset@0 must name a borrow_bytes argument' not in str(e):
            raise Fail("out_offset on borrow_cstr: unexpected error %s" % e)
    else:
        raise Fail("out_offset targeting borrow_cstr was accepted")
    print('out parameter tests: ok')

def main():
    # The guard/deep-recursion tests deliberately fault. A host core-dump pipe
    # can take longer than the test timeout; the signal verdict is what matters.
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    os.makedirs(TMPROOT, exist_ok=True)
    tmp = tempfile.mkdtemp(prefix='cinterop-test-', dir=TMPROOT)
    try:
        out = scalar(tmp)
        startup(tmp, out)
        negative(tmp, out)
        pq(tmp)
        template(tmp)
        timeout_parse(tmp)
        libpq_result(tmp)
        libpq_header_mismatch(tmp)
        libpq_conninfo(tmp)
        libpq_limits(tmp)
        libpq_errors(tmp)
        libpq_constants()
        fake_adapter(tmp)
        libpq_fake(tmp)
        zlib_second(tmp)
        include_and_cflags(tmp)
        cache_manifest_self_test(tmp)
        openssl_qualification(tmp)
        sanitizer_adapters(tmp)
        kinds(tmp)
        returns(tmp)
        foreigns(tmp)
        out_params(tmp)
        null_const_slots(tmp)
        stack_args(tmp)
        libpq_live(tmp)
        print('cinterop tests ok')
        return 0
    except Fail as e:
        print('FAIL: %s' % e, file=sys.stderr)
        return 1
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == '__main__':
    sys.exit(main())
