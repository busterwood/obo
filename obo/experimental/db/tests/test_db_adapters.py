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
import time

HERE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.dirname(HERE)
TOOL = os.path.join(os.path.dirname(DB), 'cinterop')
REPO = os.path.dirname(os.path.dirname(os.path.dirname(DB)))
EXPOBO = os.path.abspath(os.environ.get('OBO', os.path.join(REPO, 'obo', 'obo')))
TOOLPY = os.path.join(TOOL, 'cinterop.py')
# Scratch root for private build trees. Honour the environment (TMPROOT, then
# TMPDIR through tempfile) instead of a fixed /tmp path.
TMPROOT = os.environ.get('TMPROOT') or tempfile.gettempdir()
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
LIBPQ_FAKE_SQL = os.path.join(DB, 'postgres', 'postgres_fake_sql.cbind')
SQLITE = os.path.join(DB, 'sqlite', 'sqlite.cbind')
PG_EX = os.path.join(DB, 'postgres', 'examples')
SQ_EX = os.path.join(DB, 'sqlite', 'examples')
PG_DECIMAL_APP = os.path.join(PG_EX, 'postgres_decimal_fake_app.obx')
PG_DECIMAL_LIVE_APP = os.path.join(PG_EX, 'postgres_decimal_live_app.obx')
PG_SQL_APP = os.path.join(PG_EX, 'postgres_sql_app.obx')
SQ_DECIMAL_APP = os.path.join(SQ_EX, 'sqlite_decimal_app.obx')
SQ_SQL_APP = os.path.join(SQ_EX, 'sqlite_sql_app.obx')
FORBIDDEN_FLOAT_TOKENS = (
    'strtod', 'atof', 'sscanf', '%f', 'column_double', 'numeric_recv',
)
PG_DECL_FILES = [
    ('declaration_0', os.path.join(TOOL, 'toolkit', 'libpq_decls.h')),
    ('declaration_1', os.path.join(DB, 'postgres', 'postgres_adapter.h')),
    ('declaration_2', os.path.join(TOOL, 'toolkit', 'cres.h')),
    ('declaration_3', os.path.join(TOOL, 'toolkit', 'libpq_adapter.h')),
]
SQLITE_DECL_FILES = [
    ('declaration_0', os.path.join(DB, 'sqlite', 'sqlite_decls.h')),
    ('source_0', os.path.join(DB, 'sqlite', 'sq_stmt_errcode.c')),
    ('source_1', os.path.join(DB, 'sqlite', 'sq_prepare_at.c')),
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
    return app


def postgres_errors_fake_obo(tmp):
    """Specific messages for pg_exec_typed's negative codes and the NULL open.

    Fake libpq only; no server. The empty-error PQexecParams path, a closed
    connection and PQconnectdb NULL each give their own message.
    """
    out = os.path.join(tmp, 'pg_errors_fake_obo')
    app = must_build(LIBPQ_FAKE, os.path.join(PG_EX, 'postgres_errors_fake_app.obx'),
                     out, 'postgres errors fake obo')
    p = run([app])
    if p.returncode != 0:
        raise Fail('postgres errors fake obo exited %d:\n%s%s'
                   % (p.returncode, p.stdout, p.stderr))
    expect_lines(p.stdout, [
        'alloc_error=2 libpq returned no result and no error message',
        'conn_closed=true',
        'stale_error=2 connection is closed or invalid',
        'null_open_error=3 connection could not be allocated',
        'live=0', 'conns=0'], 'postgres errors fake obo')
    verify(out)
    print('postgres fake-libpq adapter error messages: ok (empty PQexecParams error, '
          'closed connection, PQconnectdb NULL)')


def postgres_decimal_fake_obo(tmp):
    out = os.path.join(tmp, 'pg_decimal_fake_obo')
    app = must_build(LIBPQ_FAKE, PG_DECIMAL_APP, out,
                     'postgres decimal fake obo')
    p = need(run([app]), 'postgres decimal fake obo run')
    if 'postgres_decimal_fake=ok' not in p.stdout:
        raise Fail('postgres decimal fake suite failed:\n%s' % p.stdout)
    ldd = need(run(['ldd', app]), 'postgres decimal fake ldd')
    if 'libpq.so.5' in ldd.stdout:
        raise Fail('postgres decimal fake app unexpectedly links real libpq')
    check_declaration_hash(manifest(out), PG_DECL_FILES, 'postgres decimal fake')
    verify(out)
    print('postgres decimal binding/read-back over fake libpq: ok')
    return app


def postgres_sql_fake_obo(tmp):
    out = os.path.join(tmp, 'pg_sql_fake_obo')
    app = must_build(LIBPQ_FAKE_SQL, PG_SQL_APP, out,
                     'postgres sql fake obo')
    p = need(run([app]), 'postgres sql fake obo run')
    expect_lines(p.stdout, [
        'status=2',
        'rows=1 cols=4',
        'sql=SELECT a = $1, b = $2, c = $3, d = $4, e = $5, f = $6',
        'nparams=6',
        'p0 type=25 len=5 null=0 bytes=104,101,108,108,111',
        'p1 type=20 len=2 null=0 bytes=52,50',
        'p2 type=16 len=1 null=0 bytes=116',
        'p3 type=17 len=3 null=0 bytes=0,255,65',
        'p4 type=0 len=0 null=1 bytes=',
        'p5 type=1700 len=6 null=0 bytes=49,46,50,51,48,48',
        'again_status=2',
        'again_sql=SELECT a = $1, b = $2, c = $3, d = $4, e = $5, f = $6',
        'float=1014 float SqlArg is not supported by the PostgreSQL adapter; use decimal or text',
        'after_float_nparams=6',
        'bad=2 malformed Sql fragment: text and parameters do not alternate',
        'dec_text=1.2300',
        'dec_empty=1006 invalid decimal text',
        'live=0',
    ], 'postgres sql fake obo')
    if 'FAIL' in p.stdout or 'unexpected_ok' in p.stdout:
        raise Fail('postgres sql fake suite reported a failure:\n%s' % p.stdout)
    ldd = need(run(['ldd', app]), 'postgres sql fake ldd')
    if 'libpq.so.5' in ldd.stdout:
        raise Fail('postgres sql fake app unexpectedly links real libpq')
    check_declaration_hash(manifest(out), PG_DECL_FILES, 'postgres sql fake')
    verify(out)
    print('postgres execute_sql fake packet/order/OIDs/decimal: ok')
    return app


def sqlite_sql(tmp):
    if not sqlite_present():
        print('sqlite sql bridge: skipped (libsqlite3.so.0 is absent)')
        return None
    out = os.path.join(tmp, 'sq_sql')
    app = must_build(SQLITE, SQ_SQL_APP, out, 'sqlite sql bridge')
    p = need(run([app], env=dict(os.environ, OBO_CHECK='1')), 'sqlite sql bridge run')
    expect_lines(p.stdout, [
        "s=x'); DROP TABLE t; --",
        'i=-7',
        'b=1',
        'blob=3:0,255,65',
        'z=null',
        'd=1.2300',
        'dtype=text',
        'float=1014 float SqlArg is not supported by the SQLite adapter; use decimal or text',
        'bad=2 malformed Sql fragment: text and parameters do not alternate',
        'sqlite_sql=ok',
    ], 'sqlite sql bridge')
    if 'FAIL' in p.stdout:
        raise Fail('sqlite sql bridge reported a failure:\n%s' % p.stdout)
    ldd = need(run(['ldd', app]), 'sqlite sql ldd')
    if 'libsqlite3.so.0' not in ldd.stdout:
        raise Fail('sqlite sql app does not depend on libsqlite3.so.0')
    check_declaration_hash(manifest(out), SQLITE_DECL_FILES, 'sqlite sql')
    verify(out)
    print('sqlite prepare_sql/bind_sql ordering/exact text/NULL/blob: ok')
    return app


def sqlite_decimal(tmp):
    if not sqlite_present():
        print('sqlite decimal: skipped (libsqlite3.so.0 is absent)')
        return None
    out = os.path.join(tmp, 'sq_decimal')
    app = must_build(SQLITE, SQ_DECIMAL_APP, out, 'sqlite decimal')
    path = os.path.join(tmp, 'decimal_oracle.db')
    for suffix in ('', '-journal', '-wal', '-shm'):
        try:
            os.unlink(path + suffix)
        except FileNotFoundError:
            pass
    try:
        p = need(run([app, path], env=dict(os.environ, OBO_CHECK='1')), 'sqlite decimal run')
        expect_lines(p.stdout, ['sqlite_decimal=ok'], 'sqlite decimal')
        import sqlite3
        conn = sqlite3.connect(path)
        try:
            text_rows = conn.execute(
                'SELECT id, typeof(v), quote(v) FROM t_text ORDER BY id'
            ).fetchall()
            want_text = [(1, 'text', "'1.2300'"),
                         (2, 'text', "'0.0000'"),
                         (3, 'text', "'1000'")]
            if text_rows != want_text:
                raise Fail('python TEXT oracle disagrees: %r' % (text_rows,))

            # NUMERIC/INTEGER affinity: integral text becomes INTEGER and
            # loses display scale; 1.2300 becomes REAL.
            num_rows = conn.execute(
                'SELECT id, typeof(v), quote(v) FROM t_num ORDER BY id'
            ).fetchall()
            want_num = [(1, 'integer', '1'), (2, 'integer', '1'),
                        (3, 'integer', '0'), (4, 'real', '1.23')]
            if num_rows != want_num:
                raise Fail('python NUMERIC affinity oracle disagrees: %r'
                           % (num_rows,))
            int_rows = conn.execute(
                'SELECT id, typeof(v), quote(v) FROM t_int ORDER BY id'
            ).fetchall()
            want_int = [(1, 'integer', '1'), (2, 'real', '1.23')]
            if int_rows != want_int:
                raise Fail('python INTEGER affinity oracle disagrees: %r'
                           % (int_rows,))

            kind, n, value = conn.execute(
                'SELECT typeof(v), length(v), v FROM t_big WHERE id=5'
            ).fetchone()
            if (kind, n) != ('text', 100001):
                raise Fail('python long-text oracle disagrees: %r'
                           % ((kind, n),))
            if set(value) != {'0', '1'} or not value.endswith('1'):
                raise Fail('python long-text content disagrees')
        finally:
            conn.close()
    finally:
        for suffix in ('', '-journal', '-wal', '-shm'):
            try:
                os.unlink(path + suffix)
            except FileNotFoundError:
                pass
    m = manifest(out)
    if m.get('signature_check') != 'declarations-only':
        raise Fail('sqlite decimal signature_check is %r'
                   % m.get('signature_check'))
    check_declaration_hash(m, SQLITE_DECL_FILES, 'sqlite decimal')
    verify(out)
    print('sqlite decimal bind/read/affinity/NUL/large Python oracle: ok')
    return app



SQLITE_TESTS = os.path.join(DB, 'sqlite', 'tests')



def sqlite_exec_many(tmp):
    """The 1 MiB/116,500-statement exec case is linear (bounded wall time)."""
    if not sqlite_present():
        print('sqlite exec many: skipped (libsqlite3.so.0 is absent)')
        return
    out = os.path.join(tmp, 'sq_exec_many')
    app = must_build(SQLITE, os.path.join(SQLITE_TESTS, 'sqlite_exec_many_app.obx'),
                     out, 'sqlite exec many')
    start = time.monotonic()
    p = need(run([app], env=dict(os.environ, OBO_CHECK='1')), 'sqlite exec many run')
    elapsed = time.monotonic() - start
    if p.returncode != 0 or p.stderr.strip():
        raise Fail('sqlite exec many exited %d: %s' % (p.returncode, p.stderr))
    expect_lines(p.stdout, ['exec_many=ok bytes=1048500'], 'sqlite exec many')
    if elapsed > 2.5:
        raise Fail('sqlite exec many took %.1fs; the quadratic copy regressed' % elapsed)
    print('sqlite exec many: 116,500 statements in %.2fs (linear): ok' % elapsed)

def sqlite_real_v2(tmp):
    """Real libsqlite3 3.45.1 through the v2 out-parameter binding."""
    if not sqlite_present():
        print('sqlite v2 real: skipped (libsqlite3.so.0 is absent)')
        return None
    out = os.path.join(tmp, 'sq_v2')
    app = must_build(SQLITE, os.path.join(SQLITE_TESTS, 'sqlite_v2_app.obx'), out,
                     'sqlite v2 real')
    path = os.path.join(tmp, 'v2.db')
    for suffix in ('', '-journal', '-wal', '-shm'):
        try:
            os.unlink(path + suffix)
        except FileNotFoundError:
            pass
    try:
        p = need(run([app, path], env=dict(os.environ, OBO_CHECK='1')),
                 'sqlite v2 real run')
        if p.returncode != 0 or p.stderr.strip():
            raise Fail('sqlite v2 real exited %d: %s' % (p.returncode, p.stderr))
        expect_lines(p.stdout, ['basic=ok exec=ok caps=ok errors=ok',
                                'close_v2=ok', 'sqlite_v2=ok'], 'sqlite v2 real')
    finally:
        for suffix in ('', '-journal', '-wal', '-shm'):
            try:
                os.unlink(path + suffix)
            except FileNotFoundError:
                pass
    ldd = need(run(['ldd', app]), 'sqlite v2 ldd')
    if 'libsqlite3.so.0' not in ldd.stdout:
        raise Fail('sqlite v2 app does not depend on libsqlite3.so.0')
    m = manifest(out)
    if m.get('signature_check') != 'declarations-only':
        raise Fail('sqlite v2 signature_check is %r' % m.get('signature_check'))
    check_declaration_hash(m, SQLITE_DECL_FILES, 'sqlite v2')
    verify(out)
    print('sqlite v2 real 3.45.1 out parameters/close_v2/NUL/caps: ok')
    return app


def sqlite_fake_v2(tmp):
    """Fake libsqlite3 failures and counters through the same wrapper."""
    if not sqlite_present():
        print('sqlite v2 fake: skipped (libsqlite3.so.0 is absent)')
        return None
    spec = json.load(open(SQLITE))
    spec['sources'] = [os.path.join(SQLITE_TESTS, 'fake_sqlite.c'),
                       os.path.join(DB, 'sqlite', 'sq_prepare_at.c')]
    spec['libs'] = []
    spec['wrapper'] = os.path.join(DB, 'sqlite', 'sqlite.obx')
    spec['wrapper_sources'] = [os.path.join(DB, '..', '..', 'lib', 'sqldialect.obx')]
    spec['declaration_files'] = [os.path.join(DB, 'sqlite', 'sqlite_decls.h')]
    spec['include_dirs'] = [os.path.join(DB, 'sqlite'), SQLITE_TESTS]
    d = os.path.join(tmp, 'sq_fake_src')
    os.makedirs(d)
    sp = os.path.join(d, 'fake.cbind')
    json.dump(spec, open(sp, 'w'), indent=2)
    out = os.path.join(d, 'out')
    app = must_build(sp, os.path.join(SQLITE_TESTS, 'sqlite_fake_app.obx'), out,
                     'sqlite v2 fake')
    p = need(run([app], env=dict(os.environ, OBO_CHECK='1')), 'sqlite v2 fake run')
    if p.returncode != 0 or p.stderr.strip():
        raise Fail('sqlite v2 fake exited %d: %s' % (p.returncode, p.stderr))
    expect_lines(p.stdout, ['sqlite_fake=ok'], 'sqlite v2 fake')
    ldd = need(run(['ldd', app]), 'sqlite v2 fake ldd')
    if 'libsqlite3.so.0' in ldd.stdout:
        raise Fail('sqlite v2 fake unexpectedly links real libsqlite3')
    verify(out)
    print('sqlite v2 fake: failed open/prepare, reset, NOMEM, -1 copy, zombie: ok')
    return app


def sqlite_asan_uaf(tmp):
    """The old owner-pointer release has a UAF; the v2 statement does not."""
    if not sqlite_present():
        print('sqlite ASan UAF: skipped (libsqlite3.so.0 is absent)')
        return
    exe = os.path.join(tmp, 'sqlite_uaf')
    cmd = [cc(), '-O1', '-g', '-fsanitize=address', '-fno-omit-frame-pointer',
           '-I', os.path.join(DB, 'sqlite'), '-o', exe,
           os.path.join(SQLITE_TESTS, 'sqlite_uaf_fixture.c'),
           '-l:libsqlite3.so.0']
    need(run(cmd), 'sqlite UAF build')
    env = dict(os.environ, ASAN_OPTIONS='detect_leaks=0')
    new = run([exe, 'new'], env=env)
    if new.returncode != 0:
        raise Fail('new-shape UAF fixture failed:\n%s' % (new.stderr + new.stdout))
    old = run([exe, 'old'], env=env)
    if old.returncode == 0 or 'heap-use-after-free' not in (old.stderr + old.stdout):
        raise Fail('old-shape UAF fixture did not fail under ASan (exit %d)' % old.returncode)
    print('ASan release_stmt UAF: old shape fails, new shape passes')


def sqlite_abi(tmp):
    """Every declared SQLite symbol resolved and called with a public prototype."""
    if not sqlite_present():
        print('sqlite ABI: skipped (libsqlite3.so.0 is absent)')
        return
    exe = os.path.join(tmp, 'sqlite_abi')
    cmd = [cc(), '-O2', '-Wall', '-Wextra', '-Werror',
           '-I', os.path.join(DB, 'sqlite'), '-o', exe,
           os.path.join(SQLITE_TESTS, 'real_sqlite_abi_test.c'),
           '-l:libsqlite3.so.0', '-ldl']
    need(run(cmd), 'sqlite ABI build')
    p = need(run([exe]), 'sqlite ABI run')
    if 'sqlite3 ABI symbols: ok' not in p.stdout:
        raise Fail('sqlite ABI suite failed:\n%s' % p.stdout)
    print('sqlite real-library ABI over every declared function: ok')

def no_float_gate(exes):
    sources = [
        os.path.join(DB, 'postgres', 'postgres.obx'),
        os.path.join(DB, 'postgres', 'postgres_adapter.c'),
        os.path.join(DB, 'sqlite', 'sqlite.obx'),
    ]
    for path in sources:
        text = open(path, encoding='utf-8').read()
        for token in FORBIDDEN_FLOAT_TOKENS:
            if token in text:
                raise Fail('%s contains forbidden float token %r'
                           % (path, token))
    for exe in exes:
        if exe is None or not os.path.exists(exe):
            continue
        p = need(run(['nm', '-u', exe]), 'nm -u %s' % exe)
        for token in ('strtod', 'atof', 'pow', 'ldexp',
                      'sqlite3_column_double', 'numeric_recv'):
            if token in p.stdout:
                raise Fail('%s imports forbidden float symbol %r'
                           % (exe, token))
    print('no-float source/nm gate: ok')


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
    p = need(run([app], env=dict(os.environ, OBO_CHECK='1')), 'sqlite memory run')
    expect_lines(p.stdout, ['sqlite_memory=ok', 'path_errors=ok', 'defer_order=ok',
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
    return app


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
        p = need(run([app, path], env=dict(os.environ, OBO_CHECK='1')), 'sqlite file run')
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


def postgres_sql_live(tmp):
    dsn = os.environ.get('CINTEROP_TEST_DSN')
    if not dsn:
        print('live postgres sql: skipped (set CINTEROP_TEST_DSN to run)')
        return
    if not libpq_present():
        raise Fail('CINTEROP_TEST_DSN is set but libpq.so.5 is absent')
    out = os.path.join(tmp, 'pg_sql_live')
    app = must_build(LIBPQ, os.path.join(PG_EX, 'postgres_sql_live_app.obx'),
                     out, 'postgres sql live')
    p = run([app, dsn], timeout=40)
    if p.returncode != 0:
        raise Fail('live postgres sql app failed (exit %d):\n%s'
                   % (p.returncode, p.stdout + p.stderr))
    if 'postgres_sql_live=ok' not in p.stdout or 'live=0' not in p.stdout:
        raise Fail('live postgres sql app output is incomplete:\n%s' % p.stdout)
    print('live postgres execute_sql SELECT-only vectors: ok')


def postgres_decimal_live(tmp):
    dsn = os.environ.get('CINTEROP_TEST_DSN')
    if not dsn:
        print('live postgres decimal: skipped (set CINTEROP_TEST_DSN to run)')
        return
    if not libpq_present():
        raise Fail('CINTEROP_TEST_DSN is set but libpq.so.5 is absent')
    out = os.path.join(tmp, 'pg_decimal_live')
    app = must_build(LIBPQ, PG_DECIMAL_LIVE_APP, out, 'postgres decimal live')
    p = run([app, dsn], timeout=60)
    if p.returncode != 0:
        raise Fail('live postgres decimal app failed (exit %d):\n%s'
                   % (p.returncode, p.stdout + p.stderr))
    expect_lines(p.stdout, ['postgres_decimal_live=ok'], 'live postgres decimal')
    print('live postgres decimal SELECT-only vectors: ok')


def main():
    os.makedirs(TMPROOT, exist_ok=True)
    tmp = tempfile.mkdtemp(prefix='db-adapters-test-', dir=TMPROOT)
    try:
        postgres_fake_c(tmp)
        fake_app = postgres_fake_obo(tmp)
        postgres_errors_fake_obo(tmp)
        decimal_fake_app = postgres_decimal_fake_obo(tmp)
        sql_fake_app = postgres_sql_fake_obo(tmp)
        sqlite_app = sqlite_memory(tmp)
        decimal_sqlite_app = sqlite_decimal(tmp)
        sqlite_sql_app = sqlite_sql(tmp)
        sqlite_file(tmp)
        sqlite_real_v2(tmp)
        sqlite_exec_many(tmp)
        sqlite_fake_v2(tmp)
        sqlite_asan_uaf(tmp)
        sqlite_abi(tmp)
        no_float_gate([fake_app, decimal_fake_app, sql_fake_app,
                       sqlite_app, decimal_sqlite_app, sqlite_sql_app])
        sanitizers(tmp)
        postgres_probe(tmp)
        postgres_live(tmp)
        postgres_sql_live(tmp)
        postgres_decimal_live(tmp)
        print('db adapter tests ok')
        return 0
    except Fail as e:
        print('FAIL: %s' % e, file=sys.stderr)
        return 1
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == '__main__':
    sys.exit(main())
