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
TMPROOT = '/tmp/opencode'
LIBPQ_PROBE_PATHS = [
    '/lib/x86_64-linux-gnu/libpq.so.5',
    '/usr/lib/x86_64-linux-gnu/libpq.so.5',
]


class Fail(Exception):
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
        p = run(['apt-get', 'download', 'libssl-dev=' + version], cwd=work, timeout=300)
        if p.returncode != 0:
            raise Fail("cannot download libssl-dev=%s for the installed libssl %s:\n%s"
                       % (version, version, (p.stderr or p.stdout).strip()))
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
    if not relocs or relocs[0] != 'cinterop-relocs 1':
        raise Fail("native sidecar header is missing")
    if not any(' add 2 i64,i64 i64 freesall,writeshandles' in ln for ln in relocs):
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
              os.path.join(TOOL, 'toolkit', 'cres.c'),
              os.path.join(TOOL, 'toolkit', 'libpq_adapter.c'),
              '-Wl,--wrap=PQconnectdb', '-l:libpq.so.5']), 'timeout parse C build')
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
    p = need(run([os.path.join(out, 'app')]), 'libpq result probe run')
    for line in ('PQlibVersion=', 'empty_status=2', 'empty_rows=0',
                 'empty_fields=0', 'empty_closed=true', 'live=0'):
        if line not in p.stdout:
            raise Fail("libpq result probe output missing %r:\n%s" % (line, p.stdout))
    ldd = need(run(['ldd', os.path.join(out, 'app')]), 'libpq result ldd')
    if 'libpq.so.5' not in ldd.stdout:
        raise Fail("libpq result probe does not depend on libpq.so.5")
    need(verify(out), 'libpq result manifest verify')
    m = json.load(open(os.path.join(out, 'manifest.json')))
    if m.get('signature_check') != 'declarations-only':
        raise Fail("libpq signature_check is %r" % m.get('signature_check'))
    print('libpq result ownership: ok')


def fake_adapter(tmp):
    exe = os.path.join(tmp, 'fake_adapter_test')
    cc = os.environ.get('CC', 'gcc')
    need(run([cc, '-O2', '-I', os.path.join(TOOL, 'toolkit'), '-o', exe,
              os.path.join(HERE, 'fake_adapter_test.c'),
              os.path.join(TOOL, 'toolkit', 'cres.c'),
              os.path.join(TOOL, 'toolkit', 'libpq_adapter.c'),
              os.path.join(HERE, 'fake_libpq.c')]), 'fake adapter C build')
    p = need(run([exe]), 'fake adapter C run')
    if 'fake adapter tests ok' not in p.stdout:
        raise Fail("fake adapter C tests failed:\n%s" % p.stdout)
    print('fake adapter C: ok')


def libpq_fake(tmp):
    out = os.path.join(tmp, 'fake')
    p = build(os.path.join(EX, 'libpq_fake.cbind'), os.path.join(EX, 'libpq_fake_app.obx'), out)
    if p.returncode != 0:
        raise Fail("fake libpq app build failed:\n%s" % (p.stderr or p.stdout))
    p = need(run([os.path.join(out, 'app')]), 'fake libpq app run')
    for line in ('status=2', 't=hello', 'n_null=true', 'n_len=0', 'b_len=3',
                 'b0=0 b1=255 b2=65', 'num=1.234567890123456789',
                 'num_type=1700 num_mod=-1', 'error_status=7', 'error_closed=true',
                 'conn_closed=true', 'connect_failed=1', 'live=0', 'conns=0',
                 'results=0', 'buffers=0'):
        if line not in p.stdout:
            raise Fail("fake libpq output missing %r:\n%s" % (line, p.stdout))
    need(verify(out), 'fake libpq manifest verify')
    ldd = need(run(['ldd', os.path.join(out, 'app')]), 'fake libpq ldd')
    if 'libpq.so.5' in ldd.stdout:
        raise Fail("fake libpq app unexpectedly links the real libpq")
    leakout = os.path.join(tmp, 'fake_leak')
    p = build(os.path.join(EX, 'libpq_fake.cbind'), os.path.join(EX, 'libpq_leak_app.obx'), leakout)
    if p.returncode != 0:
        raise Fail("fake libpq leak app build failed:\n%s" % (p.stderr or p.stdout))
    p = need(run([os.path.join(leakout, 'app')]), 'fake libpq leak app run')
    if 'live=1' not in p.stdout or 'conns=1' not in p.stdout:
        raise Fail("fake libpq leak app did not expose the missing close:\n%s" % p.stdout)
    deferout = os.path.join(tmp, 'fake_defer')
    p = build(os.path.join(EX, 'libpq_fake.cbind'), os.path.join(EX, 'libpq_defer_app.obx'), deferout)
    if p.returncode != 0:
        raise Fail("fake libpq defer app build failed:\n%s" % (p.stderr or p.stdout))
    p = need(run([os.path.join(deferout, 'app')]), 'fake libpq defer app run')
    for line in ('query_ok=true', 'early_error=7', 'nested_ok=true',
                 'loop_closes=3', 'buffer_ok=true', 'live=0', 'conns=0',
                 'results=0', 'buffers=0'):
        if line not in p.stdout:
            raise Fail("fake libpq defer output missing %r:\n%s" % (line, p.stdout))
    print('fake libpq wrapper: ok (explicit defer cleanup and leak counter)')
    outlives = os.path.join(tmp, 'fake_outlives')
    p = build(os.path.join(EX, 'libpq_fake.cbind'), os.path.join(EX, 'libpq_outlives_app.obx'), outlives)
    if p.returncode != 0:
        raise Fail("fake libpq outlives app build failed:\n%s" % (p.stderr or p.stdout))
    p = need(run([os.path.join(outlives, 'app')]), 'fake libpq outlives app run')
    for line in ('result_after_conn_close=1.234567890123456789', 'live=0',
                 'conns=0', 'results=0', 'live_result_outlives_conn=ok'):
        if line not in p.stdout:
            raise Fail("fake libpq outlives output missing %r:\n%s" % (line, p.stdout))
    print('fake libpq result-outlives-connection: ok')


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
    p = run([os.path.join(out, 'app'), dsn], timeout=30)
    if p.returncode != 0:
        raise Fail("live libpq smoke failed (exit %d):\n%s" % (p.returncode, p.stdout + p.stderr))
    for line in ('live_db_smoke=ok', 'live=0', 'null_and_empty=ok',
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
    include_dirs = openssl_include_dirs()
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
        fake_adapter(tmp)
        libpq_fake(tmp)
        zlib_second(tmp)
        include_and_cflags(tmp)
        cache_manifest_self_test(tmp)
        openssl_qualification(tmp)
        sanitizer_adapters(tmp)
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
