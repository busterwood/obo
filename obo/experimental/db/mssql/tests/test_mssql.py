#!/usr/bin/env python3
"""Live tests for the experimental db/mssql package.

    python3 obo/experimental/db/mssql/tests/test_mssql.py

The suite builds the cinterop v2 binding and runs it against the local SQL
Server. It is opt-in and reads the DSN from MSSQL_TEST_DSN, or from
~/.obo-mssql-dsn (mode 600) when the environment is not set. The DSN is never
printed, hashed into the manifest or written to a repository file.

If neither source is present the whole suite skips cleanly. Once a DSN is
present, a build, link, run or assertion failure is a failure: there is no
silent downgrade. The live statements are SELECT-only; no role, database,
schema, service or persistent data is changed.

The measured ODBC choices (ANSI vs W, SQLWCHAR size, SQLGetData behaviour) are
recorded in ../README.md and were established by the probe described there.
"""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.dirname(HERE)
DB = os.path.dirname(PKG)
REPO = os.path.dirname(os.path.dirname(os.path.dirname(DB)))
TOOL = os.path.join(os.path.dirname(DB), 'cinterop')
TOOLPY = os.path.join(TOOL, 'cinterop.py')
EXPOBO = os.path.abspath(os.environ.get('OBO', os.path.join(REPO, 'obo', 'obo')))
SPEC = os.path.join(PKG, 'mssql.cbind')
APP = os.path.join(PKG, 'examples', 'mssql_app.obx')
NEGATIVE = {
    'leak_conn': os.path.join(HERE, 'mssql_leak_conn_app.obx'),
    'leak_stmt': os.path.join(HERE, 'mssql_leak_stmt_app.obx'),
    'uac': os.path.join(HERE, 'mssql_uac_app.obx'),
}
DECLARATION_FILES = [
    ('declaration_0', os.path.join(PKG, 'mssql_decls.h')),
    ('declaration_1', os.path.join(PKG, 'ms_internal.h')),
]
LIBODBC_PATHS = [
    '/lib/x86_64-linux-gnu/libodbc.so.2',
    '/usr/lib/x86_64-linux-gnu/libodbc.so.2',
]
TMPROOT = os.environ.get('TMPROOT') or tempfile.gettempdir()


class Fail(Exception):
    pass


def dsn():
    value = os.environ.get('MSSQL_TEST_DSN')
    if value:
        return value.strip()
    path = os.path.expanduser('~/.obo-mssql-dsn')
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return f.read().strip()
    except OSError:
        return None


def run(cmd, **kw):
    kw.setdefault('timeout', 300)
    kw.setdefault('capture_output', True)
    kw.setdefault('text', True)
    return subprocess.run(cmd, **kw)


def need(p, what):
    if p.returncode != 0:
        raise Fail('%s failed (exit %d):\n%s\n%s'
                   % (what, p.returncode, (p.stdout or '').strip(),
                      (p.stderr or '').strip()))
    return p


def build(app, out, what):
    p = run([sys.executable, TOOLPY, 'build', SPEC, app, '--out', out,
             '--obo', EXPOBO])
    if p.returncode != 0:
        raise Fail('%s build failed:\n%s\n%s'
                   % (what, (p.stdout or '').strip(), (p.stderr or '').strip()))
    exe = os.path.join(out, 'app')
    if not os.path.exists(exe):
        raise Fail('%s did not produce %s' % (what, exe))
    return exe


def verify(out, what):
    need(run([sys.executable, TOOLPY, 'verify',
              os.path.join(out, 'manifest.json')]), what + ' manifest verify')


def manifest(out):
    with open(os.path.join(out, 'manifest.json')) as f:
        return json.load(f)


def sha256(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for block in iter(lambda: f.read(1 << 20), b''):
            h.update(block)
    return h.hexdigest()


def check_manifest(out, what):
    m = manifest(out)
    if m.get('signature_check') != 'declarations-only':
        raise Fail('%s signature_check is %r' % (what, m.get('signature_check')))
    files = m.get('files', {})
    for key, path in DECLARATION_FILES:
        if key not in files:
            raise Fail('%s manifest is missing %s' % (what, key))
        if files[key] != sha256(path):
            raise Fail('%s manifest hash for %s does not match' % (what, key))
    verify(out, what)
    return m


def run_app(exe, what, env_extra=None, args=None, expect=0):
    env = dict(os.environ)
    env['MSSQL_TEST_DSN'] = TEST_DSN
    if env_extra:
        env.update(env_extra)
    cmd = [exe] + (args or [])
    p = run(cmd, env=env)
    if p.returncode != expect:
        raise Fail('%s exited %d, want %d:\n%s\n%s'
                   % (what, p.returncode, expect,
                      (p.stdout or '').strip(), (p.stderr or '').strip()))
    return p


def expect_lines(p, lines, what):
    for line in lines:
        if line not in p.stdout:
            raise Fail('%s stdout is missing %r:\n%s' % (what, line, p.stdout))


def expect_stderr(p, lines, what):
    for line in lines:
        if line not in p.stderr:
            raise Fail('%s stderr is missing %r:\n%s' % (what, line, p.stderr))


def main():
    global TEST_DSN
    TEST_DSN = dsn()
    if not TEST_DSN:
        print('mssql live tests: skipped (MSSQL_TEST_DSN and '
              '~/.obo-mssql-dsn are absent)')
        return 0
    if not os.path.exists(EXPOBO):
        raise Fail('obo compiler is missing: %s' % EXPOBO)
    if not any(os.path.exists(p) for p in LIBODBC_PATHS):
        raise Fail('libodbc.so.2 is absent; a live DSN cannot be tested')

    os.makedirs(TMPROOT, exist_ok=True)
    tmp = tempfile.mkdtemp(prefix='obo-mssql-', dir=TMPROOT)
    try:
        out = os.path.join(tmp, 'app')
        exe = build(APP, out, 'mssql app')
        m = check_manifest(out, 'mssql app')
        if TEST_DSN in json.dumps(m):
            raise Fail('the DSN leaked into the manifest')
        ldd = need(run(['ldd', exe]), 'mssql app ldd')
        if 'libodbc.so.2' not in ldd.stdout:
            raise Fail('mssql app does not depend on libodbc.so.2')

        p = run_app(exe, 'mssql live', {'OBO_CHECK': '1'})
        expect_lines(p, ['mssql=ok'], 'mssql live')
        if (p.stderr or '').strip():
            raise Fail('mssql live wrote to stderr:\n%s' % p.stderr)

        p = run_app(exe, 'mssql census underflow',
                    {'OBO_CHECK': '1', 'OBO_CENSUS_FAULT': 'underflow'}, expect=4)
        expect_stderr(p, ['census underflow'], 'mssql census underflow')

        neg = {}
        for name, path in NEGATIVE.items():
            neg_out = os.path.join(tmp, name)
            neg[name] = build(path, neg_out, 'mssql ' + name)
            check_manifest(neg_out, 'mssql ' + name)

        p = run_app(neg['leak_conn'], 'mssql leak conn', {'OBO_CHECK': '1'},
                    expect=8)
        expect_stderr(p, ['foreign handles leaked: Connection 1'],
                      'mssql leak conn')
        p = run_app(neg['leak_conn'], 'mssql leak conn unchecked', expect=0)
        expect_lines(p, ['leaking one connection'], 'mssql leak conn unchecked')
        p = run_app(neg['leak_conn'], 'mssql leak conn exit', {'OBO_CHECK': '1'},
                    args=['exit'], expect=0)
        expect_lines(p, ['leaking one connection'], 'mssql leak conn exit')

        p = run_app(neg['leak_stmt'], 'mssql leak stmt', {'OBO_CHECK': '1'},
                    expect=8)
        expect_stderr(p, ['foreign handles leaked: Stmt 1'], 'mssql leak stmt')
        p = run_app(neg['leak_stmt'], 'mssql leak stmt unchecked', expect=0)

        p = run_app(neg['uac'], 'mssql use after close', {'OBO_CHECK': '1'},
                    expect=7)
        expect_stderr(p, ['foreign handle used after close'],
                      'mssql use after close')
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print('mssql live tests: ok (SELECT 1, dbo.items text/decimal/nvarchar/'
          'varbinary/NULL/uniqueidentifier, typed params, SQLSTATE errors, '
          'close/double-close, W metadata/diagnostics, OBO_CHECK census)')
    return 0


if __name__ == '__main__':
    TEST_DSN = None
    try:
        sys.exit(main())
    except Fail as e:
        print('mssql tests: FAIL: %s' % e, file=sys.stderr)
        sys.exit(1)
