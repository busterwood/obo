#!/usr/bin/env python3
"""Private-source regression for html.appendDecimal.

appendDecimal is intentionally private: it is only an implementation detail of
the control-character escaping path. This test reads the real
obo/lib/html.obx, makes one temporary copy with the function exported so a
separate root can call it, and checks every value 0..127 byte-for-byte. The
copy differs only in the `pub` keyword; a rename or signature change fails the
replacement check instead of silently testing a stale duplicate.

    run from obo/tests/run_fork.py as the `html/decimal-private` case
"""
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
FORK = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(os.path.dirname(FORK), 'obx', 'tests'))
import run  # noqa: E402


def decimal_private():
    src = os.path.join(FORK, 'lib', 'html.obx')
    text = open(src).read()
    needle = 'fn appendDecimal('
    if text.count(needle) != 1:
        raise run.Fail(f"{src}: expected exactly one '{needle}'")
    text = text.replace(needle, 'pub fn appendDecimal(', 1)

    case = tempfile.mkdtemp(prefix='html-decimal-', dir=run.TMP)
    try:
        html = os.path.join(case, 'html.obx')
        with open(html, 'w') as f:
            f.write(text)
        main = os.path.join(case, 'main.obx')
        with open(main, 'w') as f:
            f.write('import "html.obx" as html\n'
                    'fn main() {\n'
                    '    for i in 0..128 {\n'
                    '        var b = new []byte(0)\n'
                    '        html::appendDecimal(b, i)\n'
                    '        println(i, " ", string(b))\n'
                    '    }\n'
                    '}\n')
        exe = os.path.join(case, 'exe')
        sym = os.path.join(case, 'sym')
        os.makedirs(sym)
        p = subprocess.run([run.OBXC, main, '-o', exe, '--rt', FORK,
                            '-I', case, '--sym-dir', sym],
                           capture_output=True, text=True, timeout=120)
        if p.returncode != 0:
            raise run.Fail(f"html decimal test did not compile: {p.stderr.strip()[:400]}")
        r = subprocess.run([exe], capture_output=True, text=True, timeout=120,
                           env=dict(os.environ, OBO_CHECK='1'))
        want = ''.join(f"{i} {i}\n" for i in range(128))
        if r.returncode != 0:
            raise run.Fail(f"html decimal test exit {r.returncode}: {r.stderr.strip()[:300]}")
        if r.stdout != want:
            got = r.stdout.splitlines()
            exp = want.splitlines()
            for i in range(max(len(got), len(exp))):
                g = got[i] if i < len(got) else '<nothing>'
                e = exp[i] if i < len(exp) else '<nothing>'
                if g != e:
                    raise run.Fail(f"appendDecimal at {i}: got {g!r}, want {e!r}")
            raise run.Fail("appendDecimal output differs")
    finally:
        shutil.rmtree(case, ignore_errors=True)
