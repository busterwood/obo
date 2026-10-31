#!/usr/bin/env python3
"""Targeted tests for the experimental db/postgres and db/sqlite adapters.

    python3 obo/experimental/db/tests/test_db_adapters.py

PostgreSQL live tests are opt-in through CINTEROP_TEST_DSN; the non-networked
link/PGresult probe and the deterministic fake-libpq C suite always run when the
toolchain is present. SQLite tests use the real libsqlite3.so.0 in memory and in
a temporary file under /tmp, with Python's sqlite3 module as an independent
oracle. A missing library is the only skip; once a library is present, a build
or test failure is a failure.

This is not part of the public compiler test catalog. It does not change server
configuration, create roles/databases, start services, or depend on sqlite3.h.
"""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.dirname(HERE)
TOOL = os.path.join(os.path.dirname(DB), 'cinterop')
REPO = os.path.dirname(os.path.dirname(os.path.dirname(DB)))
EXPOBO = os.path.abspath(os.environ.get('OBO', os.path.join(REPO, 'obo', 'obo')))
TOOLPY = os.path.join(TOOL, 'cinterop.py')
TMPROOT = '/tmp/opencode'
LIBPQ_PATHS = [
    '/lib/x86_64-linux-gnu/libpq.so.5',
    '/usr/lib/x86_64-linux-gnu/libpq.so.5',
]
SQLITE_PATHS = [
    '/lib/x86_64-linux-gnu/libsqlite3.so.0',
    '/usr/lib/x86_64-linux-gnu/libsqlite3.so.0',
]
LIBPQ = os.path.join(DB, 'postgres', 'postgres.cbind')
LIBPQ_FAKE = os.path.join(DB, 'postgres', 'postgres_fake.cbind')
SQLITE = os.path.join(DB, 'sqlite', 'sqlite.cbind')
PG_EX = os.path.join(DB, 'postgres', 'examples')
SQ_EX = os.path.join(DB, 'sqlite', 'examples')
PG_DECL_FILES = [
    ('declaration_0', os.path.join(TOOL, 'toolkit', 'libpq_decls.h')),
    ('declaration_1', os.path.join(DB, 'postgres', 'postgres_adapter.h')),
    ('declaration_2', os.path.join(TOOL, 'toolkit', 'cres.h')),
    ('declaration_3', os.path.join(TOOL, 'toolkit', 'libpq_adapter.h')),
]
SQLITE_DECL_FILES = [
    ('declaration_0', os.path.join(DB, 'sqlite', 'sqlite_decls.h')),
    ('declaration_1', os.path.join(DB, 'sqlite', 'sqlite_adapter.h')),
    ('declaration_2', os.path.join(TOOL, 'toolkit', 'cres.h')),
]


class Fail(Exception):
    pass


def libpq_present():
    return os.environ.get('DB_TEST_NO_LIBPQ') != '1' and any(
        os.path.exists(p) for p in LIBPQ_PATHS)


def sqlite_present():
    return os.environ.get('DB_TEST_NO_SQLITE') != '1' and any(
        os.path.exists(p) for p in SQLITE_PATHS)


def run(cmd, **kw):
    kw.setdefault('timeout', 300)
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def need(p, what):
    if p.returncode != 0:
        raise Fail("%s failed:\n%s" % (what, (p.stderr or p.stdout).strip()))
    return p


def build(spec, app, out, name='app'):
    return run([sys.executable, TOOLPY, 'build', spec, app, '--out', out,
                '--obo', EXPOBO, '--name', name])


def must_build(spec, app, out, what):
    p = build(spec, app, out)
    if p.returncode != 0:
        raise Fail("%s build failed:\n%s" % (what, (p.stderr or p.stdout).strip()))
    return os.path.join(out, 'app')


def verify(out):
    return need(run([sys.executable, TOOLPY, 'verify',
                     os.path.join(out, 'manifest.json')]), 'manifest verify')


def manifest(out):
    return json.load(open(os.path.join(out, 'manifest.json')))


def sha256(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for b in iter(lambda: f.read(1 << 20), b''):
            h.update(b)
    return h.hexdigest()


def check_declaration_hash(m, expected, what):
    files = m.get('files', {})
    for key, path in expected:
        if key not in files:
            raise Fail('%s manifest is missing %s (%s)' % (what, key, path))
        if files[key] != sha256(path):
            raise Fail('%s manifest hash for %s does not match source'
                       % (what, key))


def expect_lines(text, lines, what):
    for line in lines:
        if line not in text:
            raise Fail("%s output is missing %r:\n%s" % (what, line, text))


def cc():
    return os.environ.get('CC', 'gcc')


def postgres_probe(tmp):
    if not libpq_present():
        print('postgres probe: skipped (libpq.so.5 is absent)')
        return
    out = os.path.join(tmp, 'pg_probe')
    app = must_build(LIBPQ, os.path.join(PG_EX, 'postgres_probe_app.obx'), out,
                     'postgres probe')
    p = need(run([app]), 'postgres probe run')
    expect_lines(p.stdout, ['PQlibVersion=', 'empty_status=2', 'empty_rows=0',
                            'empty_fields=0', 'empty_open=true',
                            'invalid_conninfo_code=2',
                            'invalid_conninfo_msg=invalid connection string:',
                            'live=0'], 'postgres probe')
    if 'could not be allocated' in p.stdout:
        raise Fail('invalid conninfo was misreported as an allocation failure')
    m = manifest(out)
    check_declaration_hash(m, PG_DECL_FILES, 'postgres')
    ldd = need(run(['ldd', app]), 'postgres probe ldd')
    if 'libpq.so.5' not in ldd.stdout:
        raise Fail('postgres probe does not depend on libpq.so.5')
    if m.get('signature_check') != 'declarations-only':
        raise Fail('postgres signature_check is %r' % m.get('signature_check'))
    verify(out)
    print('postgres link/ownership probe: ok')


def postgres_fake_obo(tmp):
    out = os.path.join(tmp, 'pg_fake_obo')
    app = must_build(LIBPQ_FAKE, os.path.join(PG_EX, 'postgres_fake_app.obx'),
                     out, 'postgres fake obo')
    p = need(run([app]), 'postgres fake obo run')
    expect_lines(p.stdout, ['status=2', 'rows=1 cols=4', 't=hello',
                            'n=-9223372036854775807', 'b_len=3',
                            'b0=0 b1=255 b2=65',
                            'num=1.234567890123456789',
                            'oid=1700', 'mod=-1', 'live=0'],
                 'postgres fake obo')
    ldd = need(run(['ldd', app]), 'postgres fake obo ldd')
    if 'libpq.so.5' in ldd.stdout:
        raise Fail('postgres fake obo unexpectedly links real libpq')
    check_declaration_hash(manifest(out), PG_DECL_FILES, 'postgres fake')
    verify(out)
    print('postgres fake-libpq obo packet encoder: ok')


def postgres_fake_c(tmp):
    exe = os.path.join(tmp, 'postgres_adapter_test')
    cmd = [cc(), '-O2', '-Wall', '-Wextra',
           '-I', os.path.join(TOOL, 'toolkit'),
           '-I', os.path.join(DB, 'postgres'),
           '-o', exe,
           os.path.join(HERE, 'postgres_adapter_test.c'),
           os.path.join(TOOL, 'toolkit', 'cres.c'),
           os.path.join(TOOL, 'toolkit', 'libpq_adapter.c'),
           os.path.join(DB, 'postgres', 'postgres_adapter.c'),
           os.path.join(TOOL, 'tests', 'fake_libpq.c')]
    need(run(cmd), 'postgres fake C build')
    p = need(run([exe]), 'postgres fake C run')
    if 'postgres typed adapter tests ok' not in p.stdout:
        raise Fail('postgres fake C suite failed:\n%s' % p.stdout)
    print('postgres fake/negative ownership C: ok')


def sqlite_memory(tmp):
    if not sqlite_present():
        print('sqlite memory: skipped (libsqlite3.so.0 is absent)')
        return
    out = os.path.join(tmp, 'sq_memory')
    app = must_build(SQLITE, os.path.join(SQ_EX, 'sqlite_memory_app.obx'), out,
                     'sqlite memory')
    p = need(run([app]), 'sqlite memory run')
    expect_lines(p.stdout, ['sqlite_memory=ok', 'live=0', 'conns=0', 'stmts=0',
                            'buffers=0', 'path_errors=ok', 'defer_order=ok',
                            'large_and_real_errors=ok', 'sqlite_smoke=ok'], 'sqlite memory')
    ldd = need(run(['ldd', app]), 'sqlite ldd')
    if 'libsqlite3.so.0' not in ldd.stdout:
        raise Fail('sqlite app does not depend on libsqlite3.so.0')
    m = manifest(out)
    if m.get('signature_check') != 'declarations-only':
        raise Fail('sqlite signature_check is %r' % m.get('signature_check'))
    check_declaration_hash(m, SQLITE_DECL_FILES, 'sqlite')
    verify(out)
    print('sqlite in-memory: ok')


def sqlite_file(tmp):
    if not sqlite_present():
        print('sqlite tempfile: skipped (libsqlite3.so.0 is absent)')
        return
    out = os.path.join(tmp, 'sq_file')
    app = must_build(SQLITE, os.path.join(SQ_EX, 'sqlite_file_app.obx'), out,
                     'sqlite file')
    path = os.path.join(tmp, 'oracle.db')
    for suffix in ('', '-journal', '-wal', '-shm'):
        try:
            os.unlink(path + suffix)
        except FileNotFoundError:
            pass
    try:
        p = need(run([app, path]), 'sqlite file run')
        expect_lines(p.stdout, ['changes=1', 'sqlite_file=ok'], 'sqlite file')
        import sqlite3
        conn = sqlite3.connect(path)
        try:
            rows = conn.execute(
                'SELECT id,typeof(id),label,typeof(label),n,typeof(n),'
                'data,typeof(data),note,typeof(note),empty,typeof(empty) '
                'FROM items').fetchall()
        finally:
            conn.close()
        want = (1, 'integer', 'oracle', 'text', -9000000000, 'integer',
                bytes([0, 255, 65]), 'blob', None, 'null', '', 'text')
        if rows != [want]:
            raise Fail('python sqlite3 oracle disagrees: %r' % (rows,))
        print('sqlite tempfile/Python typeof/NULL/empty/blob oracle: ok')
    finally:
        for suffix in ('', '-journal', '-wal', '-shm'):
            try:
                os.unlink(path + suffix)
            except FileNotFoundError:
                pass


def sqlite_c(tmp):
    if not sqlite_present():
        print('sqlite C adapter: skipped (libsqlite3.so.0 is absent)')
        return
    exe = os.path.join(tmp, 'sqlite_adapter_test')
    cmd = [cc(), '-O2', '-Wall', '-Wextra',
           '-I', os.path.join(TOOL, 'toolkit'),
           '-I', os.path.join(DB, 'sqlite'),
           '-o', exe,
           os.path.join(HERE, 'sqlite_adapter_test.c'),
           os.path.join(TOOL, 'toolkit', 'cres.c'),
           os.path.join(DB, 'sqlite', 'sqlite_adapter.c'),
           '-l:libsqlite3.so.0']
    need(run(cmd), 'sqlite C adapter build')
    p = need(run([exe]), 'sqlite C adapter run')
    if 'sqlite adapter tests ok' not in p.stdout:
        raise Fail('sqlite C adapter suite failed:\n%s' % p.stdout)
    print('sqlite direct C adapter: ok')


def sanitizers(tmp):
    cflags = ['-O1', '-g', '-fsanitize=address,undefined',
              '-fno-sanitize-recover=all', '-fno-omit-frame-pointer']
    exe = os.path.join(tmp, 'postgres_adapter_asan')
    cmd = [cc(), *cflags,
           '-I', os.path.join(TOOL, 'toolkit'),
           '-I', os.path.join(DB, 'postgres'),
           '-o', exe,
           os.path.join(HERE, 'postgres_adapter_test.c'),
           os.path.join(TOOL, 'toolkit', 'cres.c'),
           os.path.join(TOOL, 'toolkit', 'libpq_adapter.c'),
           os.path.join(DB, 'postgres', 'postgres_adapter.c'),
           os.path.join(TOOL, 'tests', 'fake_libpq.c')]
    need(run(cmd), 'postgres fake ASAN/UBSAN build')
    p = need(run([exe]), 'postgres fake ASAN/UBSAN run')
    if 'postgres typed adapter tests ok' not in p.stdout:
        raise Fail('postgres fake ASAN/UBSAN failed:\n%s' % p.stdout)

    if sqlite_present():
        exe = os.path.join(tmp, 'sqlite_adapter_asan')
        cmd = [cc(), *cflags,
               '-I', os.path.join(TOOL, 'toolkit'),
               '-I', os.path.join(DB, 'sqlite'),
               '-o', exe,
               os.path.join(HERE, 'sqlite_adapter_test.c'),
               os.path.join(TOOL, 'toolkit', 'cres.c'),
               os.path.join(DB, 'sqlite', 'sqlite_adapter.c'),
               '-l:libsqlite3.so.0']
        need(run(cmd), 'sqlite ASAN/UBSAN build')
        p = need(run([exe]), 'sqlite ASAN/UBSAN run')
        if 'sqlite adapter tests ok' not in p.stdout:
            raise Fail('sqlite ASAN/UBSAN failed:\n%s' % p.stdout)
    print('ASAN/UBSAN adapter tests: ok')


def postgres_live(tmp):
    dsn = os.environ.get('CINTEROP_TEST_DSN')
    if not dsn:
        print('live postgres: skipped (set CINTEROP_TEST_DSN to run)')
        return
    if not libpq_present():
        raise Fail('CINTEROP_TEST_DSN is set but libpq.so.5 is absent')
    out = os.path.join(tmp, 'pg_live')
    app = must_build(LIBPQ, os.path.join(PG_EX, 'postgres_live_app.obx'), out,
                     'postgres live')
    p = run([app, dsn], timeout=40)
    if p.returncode != 0:
        raise Fail('live postgres app failed (exit %d):\n%s'
                   % (p.returncode, p.stdout + p.stderr))
    expect_lines(p.stdout, ['numeric_text=1.234567890123456789 oid=1700 typmod=-1',
                            'null_and_empty=ok', 'binary_bytes=00ff41 len=3',
                            'empty_blob=ok', 'sql_error_length=', 'init_error=ok',
                            'badsocket_error=ok', 'live=0',
                            'live_db_smoke=ok'], 'live postgres')
    out2 = os.path.join(tmp, 'pg_outlives')
    app2 = must_build(LIBPQ, os.path.join(PG_EX, 'postgres_outlives_app.obx'),
                      out2, 'postgres outlives')
    p = run([app2, dsn], timeout=40)
    if p.returncode != 0 or 'pgresult_outlives_conn=ok' not in p.stdout:
        raise Fail('postgres outlives app failed (exit %d):\n%s'
                   % (p.returncode, p.stdout + p.stderr))
    print('live postgres SELECT-only smoke: ok (explicit CINTEROP_TEST_DSN)')


def main():
    os.makedirs(TMPROOT, exist_ok=True)
    tmp = tempfile.mkdtemp(prefix='db-adapters-test-', dir=TMPROOT)
    try:
        postgres_fake_c(tmp)
        postgres_fake_obo(tmp)
        sqlite_c(tmp)
        sqlite_memory(tmp)
        sqlite_file(tmp)
        sanitizers(tmp)
        postgres_probe(tmp)
        postgres_live(tmp)
        print('db adapter tests ok')
        return 0
    except Fail as e:
        print('FAIL: %s' % e, file=sys.stderr)
        return 1
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == '__main__':
    sys.exit(main())
