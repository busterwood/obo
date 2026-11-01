#!/usr/bin/env python3
"""Deterministic adversarial corpus for html/sql template literals.

One generated program builds every case through markdown-free byte loops and
prints the rendered bytes, so Python can compare byte-for-byte with the same
escaping policy. SQL cases assert that a string is one bound argument and that
the literal text is unchanged, never interpolated.

    run from tests/run_fork.py as the `template/fuzz` case
"""
import os
import random
import shutil
import subprocess
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
FORK = os.path.dirname(HERE)
import sys
sys.path.insert(0, os.path.join(os.path.dirname(FORK), 'obx', 'tests'))
import run  # noqa: E402


def escape_text(b):
    out = bytearray()
    for c in b:
        if c == 0x26:
            out += b'&amp;'
        elif c == 0x3C:
            out += b'&lt;'
        elif c == 0x3E:
            out += b'&gt;'
        elif c == 0x22:
            out += b'&quot;'
        elif c == 0x27:
            out += b'&#39;'
        elif c == 0 or c == 0x7F or (c < 0x20 and c not in (9, 10, 13)):
            out += b'&#' + str(c).encode() + b';'
        else:
            out.append(c)
    return bytes(out)


def escape_attr(b):
    out = bytearray()
    for c in b:
        if c == 0x26:
            out += b'&amp;'
        elif c == 0x3C:
            out += b'&lt;'
        elif c == 0x3E:
            out += b'&gt;'
        elif c == 0x22:
            out += b'&quot;'
        elif c == 0x27:
            out += b'&#39;'
        elif c == 0 or c == 0x7F or c < 0x20:
            out += b'&#' + str(c).encode() + b';'
        else:
            out.append(c)
    return bytes(out)


def corpus():
    rng = random.Random(0x0B0_15)
    alphabet = list(range(0, 0x80)) + [0x80, 0xA9, 0xC3, 0xA9, 0xE2, 0x98, 0x83, 0xFF]
    cases = [b'', b'plain', b'&<>"\'', b'\x00\x01\t\n\r\x7f', 'é☃'.encode()]
    for _ in range(60):
        n = rng.randrange(0, 12)
        cases.append(bytes(rng.choice(alphabet) for _ in range(n)))
    return cases


def run_case() -> None:
    cases = corpus()
    lines = ['import "html.obx" as html', 'fn main() {']
    want = bytearray()
    safe_urls = ["/a/b?x=1&y=2", "https://example.com/a?b=1&c=2", "mailto:a@b",
                 "//host/x", "?q=1#f", "/é☃?x"]
    for k, u in enumerate(safe_urls):
        lit = u.replace('\\', '\\\\').replace('"', '\\"')
        lines.append(f'    let su{k} = "{lit}"')
        lines.append(f'    let sa{k} = html`<a href="${{su{k}}}">x</a>`')
        lines.append(f'    println(html::toText(sa{k}))')
        want += b'<a href="' + escape_attr(u.encode()) + b'">x</a>\n'
    for i, c in enumerate(cases):
        lines.append(f'    var b{i} = new []byte(0)')
        for byte in c:
            lines.append(f'    append(b{i}, byte({byte}))')
        lines.append(f'    let s{i} = string(b{i})')
        lines.append(f'    let h{i} = html`<p>${{s{i}}}</p>`')
        lines.append(f'    println(html::toText(h{i}))')
        lines.append(f'    let a{i} = html`<p title="${{s{i}}}">x</p>`')
        lines.append(f'    println(html::toText(a{i}))')
        lines.append(f'    let q{i} = sql`SELECT ${{s{i}}}`')
        lines.append(f'    println(len(q{i}.text), " ", len(q{i}.params), " ", q{i}.text[0])')
        lines.append(f'    var c{i} = new []byte(0)')
        for byte in c:
            lines.append(f'    append(c{i}, byte({byte}))')
        lines.append(f'    let qb{i} = sql`${{c{i}}}`')
        lines.append(f'    println(len(qb{i}.params), " ", len(qb{i}.text))')
        lines.append(f'    let f{i} = html`<b>${{s{i}}}</b>`')
        lines.append(f'    var arr{i} = new []Html(0)')
        lines.append(f'    append(arr{i}, html.copy(f{i}))')
        lines.append(f'    append(arr{i}, f{i})')
        lines.append(f'    let list{i} = html`<ul>${{arr{i}}}</ul>`')
        lines.append(f'    println(html::toText(list{i}))')
        want += b'<p>' + escape_text(c) + b'</p>\n'
        want += b'<p title="' + escape_attr(c) + b'">x</p>\n'
        want += b'2 1 SELECT \n'
        want += b'1 2\n'
        want += b'<ul><b>' + escape_text(c) + b'</b><b>' + escape_text(c) + b'</b></ul>\n'
    lines.append('}')

    case = tempfile.mkdtemp(prefix='template-fuzz-', dir=run.TMP)
    try:
        src = os.path.join(case, 'main.obx')
        with open(src, 'w') as f:
            f.write('\n'.join(lines) + '\n')
        exe = os.path.join(case, 'exe')
        sym = os.path.join(case, 'sym')
        os.makedirs(sym)
        p = subprocess.run([run.OBXC, src, '-o', exe, '--rt', FORK,
                            '-I', os.path.join(FORK, 'lib'), '--sym-dir', sym],
                           capture_output=True, text=True, timeout=180)
        if p.returncode != 0:
            raise run.Fail('template fuzz did not compile: ' + p.stderr.strip()[:400])
        r = subprocess.run([exe], capture_output=True, timeout=180,
                           env=dict(os.environ, OBO_CHECK='1'))
        if r.returncode != 0:
            raise run.Fail('template fuzz exit %d: %s' %
                           (r.returncode, r.stderr.decode('utf-8', 'replace').strip()[:300]))
        if r.stdout != want:
            for i in range(min(len(r.stdout), len(want))):
                if r.stdout[i] != want[i]:
                    raise run.Fail('template fuzz differs at byte %d: got %r want %r' %
                                   (i, r.stdout[max(0, i - 20):i + 40],
                                    want[max(0, i - 20):i + 40]))
            raise run.Fail('template fuzz length %d != %d' % (len(r.stdout), len(want)))
    finally:
        shutil.rmtree(case, ignore_errors=True)
