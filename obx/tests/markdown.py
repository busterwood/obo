#!/usr/bin/env python3
"""Tests for lib/markdown.obx and cmd/cmark.obx against the CommonMark spec and cmark.

    python3 obx/tests/markdown.py [--c] [-j N] [--ref CMARK] [--exe PATH] [-k PATTERN] [-v] [--no-patho]

Our cmark is built with compiler/out/obxc (native, or through C with --c) and
run with OBX_CHECK=1, so a leak fails a case. Suites:

  spec     every example of the CommonMark 0.31.2 spec (spec.json), in --unsafe mode
           as the spec assumes, compared with the spec's HTML exactly; pass counts
           are reported by section
  smart    the smart punctuation examples of cmark's tests (--smart --unsafe)
  regress  cmark's own regression examples (--unsafe)
  safe     the spec examples again in safe mode, against the reference cmark
  diff     generated and real documents (the repo's *.md files) against cmark
  patho    cmark's pathological inputs, against the reference, with a timeout
           (linear time: each must finish in a few seconds)

The reference is --ref, else $CMARK_REF, else `cmark` on PATH; without one only
the suites that need none run (spec, smart, regress). Build it from
github.com/commonmark/cmark 0.31.2 into a scratch directory:

    cmake -DCMAKE_INSTALL_PREFIX=/tmp/cmark-ref .. && make install

The spec and cmark's test files are downloaded data (CC-BY-SA, BSD-2): they are
fetched into a cache directory ($OBX_MD_CACHE, else ~/.cache/obx-markdown) when
missing and only read as data, never run.
"""
import argparse
import concurrent.futures
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
OBX = os.path.dirname(HERE)
ROOT = os.path.dirname(OBX)
OBXC = os.environ.get('OBXC') or os.path.join(OBX, 'compiler', 'out', 'obxc')
CACHE = os.environ.get('OBX_MD_CACHE') or os.path.join(os.path.expanduser('~'), '.cache', 'obx-markdown')

URLS = {
    'spec.json': 'https://spec.commonmark.org/0.31.2/spec.json',
    'regression.txt': 'https://raw.githubusercontent.com/commonmark/cmark/0.31.2/test/regression.txt',
    'smart_punct.txt': 'https://raw.githubusercontent.com/commonmark/cmark/0.31.2/test/smart_punct.txt',
}


def fetch(name):
    path = os.path.join(CACHE, name)
    if not os.path.exists(path):
        os.makedirs(CACHE, exist_ok=True)
        with urllib.request.urlopen(URLS[name], timeout=60) as r:
            data = r.read()
        with open(path + '.tmp', 'wb') as f:
            f.write(data)
        os.replace(path + '.tmp', path)
    return path


def load_spec():
    with open(fetch('spec.json'), encoding='utf-8') as f:
        return json.load(f)


def load_examples(name):
    """The examples of a spec.txt style file: (markdown, html, section, number)."""
    out = []
    section = ''
    with open(fetch(name), encoding='utf-8') as f:
        lines = f.read().split('\n')
    i = 0
    n = 0
    while i < len(lines):
        ln = lines[i]
        m = re.match(r'#+ +(.*)', ln)
        if m and not ln.startswith('```'):
            section = m.group(1)
        if re.match(r'`{32} example', ln):
            i += 1
            md, html = [], []
            while lines[i] != '.':
                md.append(lines[i])
                i += 1
            i += 1
            while not lines[i].startswith('`' * 32):
                html.append(lines[i])
                i += 1
            n += 1
            out.append((''.join(x + '\n' for x in md).replace('→', '\t'),
                        ''.join(x + '\n' for x in html).replace('→', '\t'), section, n))
        i += 1
    return out


def build(c_backend, workdir):
    exe = os.path.join(workdir, 'cmark-c' if c_backend else 'cmark-n')
    cmd = [OBXC, os.path.join(OBX, 'cmd', 'cmark.obx'), '-o', exe]
    if not c_backend:
        cmd.insert(2, '--native')
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        print(r.stdout + r.stderr)
        sys.exit('build failed')
    return exe


def run(exe, args, data, timeout=30, check=True):
    env = dict(os.environ)
    if check:
        env['OBX_CHECK'] = '1'
    t = time.time()
    try:
        r = subprocess.run([exe] + args, input=data, capture_output=True, timeout=timeout, env=env)
    except subprocess.TimeoutExpired:
        return None, b'timeout', time.time() - t
    return r.stdout, r.stderr + (b'' if r.returncode == 0 else b'exit %d' % r.returncode), time.time() - t


def show(b):
    return repr(b.decode('utf-8', 'replace')) if b is not None else 'None'


class Result:
    def __init__(self):
        self.fail = []
        self.passed = 0
        self.sections = {}

    def add(self, section, ok, msg=''):
        s = self.sections.setdefault(section, [0, 0])
        s[1] += 1
        if ok:
            s[0] += 1
            self.passed += 1
        else:
            self.fail.append(msg)


def suite_examples(pool, exe, exs, args, label, verbose, ref=None):
    res = Result()

    def one(ex):
        md, html, section, num = ex
        out, err, _ = run(exe, args, md.encode())
        if ref is not None:
            want, _, _ = run(ref, [a for a in args if a != '--validate-utf8'], md.encode(), check=False)
        else:
            want = html.encode()
        ok = out == want and not err
        return section, ok, '%s #%d\n   input: %s\n   want:  %s\n   got:   %s %s' % (label, num, show(md.encode()), show(want), show(out), show(err) if err else '')
    for section, ok, msg in pool.map(one, exs):
        res.add(section, ok, msg)
    return res


def report(label, res, verbose):
    total = sum(v[1] for v in res.sections.values())
    print('%-8s %d/%d' % (label, res.passed, total))
    if verbose or label == 'spec':
        for s, (p, t) in res.sections.items():
            print('    %-34s %3d/%-3d%s' % (s, p, t, '' if p == t else '   <--'))
    for m in res.fail[:15]:
        print('  FAIL ' + m)
    if len(res.fail) > 15:
        print('  ... %d more' % (len(res.fail) - 15))
    return not res.fail


def gen_cases():
    """Generated differential inputs: (name, text)."""
    cases = []
    import random
    rnd = random.Random(12345)
    frags = ['*', '_', '**', '__', '`', '``', '[', ']', '(', ')', '!', '<', '>', '&', '#', '-', '+', '1.', '1)', '    ',
             '\t', ' ', '\n', '\n\n', '> ', '- ', '* * *', '---', '===', '```', '~~~', '<div>', '</div>', '<!--', '-->',
             '[a]: /u "t"', '[a]', 'foo', 'bar', 'a b', '\\', '&amp;', '&#35;', '&bogus;', '<http://x.y>', '<a@b.co>',
             'é', '"', "'", '...', '--', '|', '<b>', '</b>', '<a href="x">', '  \n', '\\\n']
    for i in range(400):
        k = rnd.randint(3, 40)
        cases.append(('rand%03d' % i, ''.join(rnd.choice(frags) for _ in range(k))))
    for c in range(33, 127):
        ch = chr(c)
        cases.append(('href%02x' % c, '[a](<x%sx>) ![b](<y%sy> "t%s")\n' % (ch, ch, ch)))
    cases.append(('nul', 'abc\x00de\n'))
    cases.append(('crlf', 'a\r\nb\r\n\r\n- c\r\n- d\r\rlone\r'))
    cases.append(('bom', '﻿# h\n'))
    cases.append(('notrail', '> a\n> b'))
    return cases


def patho_cases():
    """cmark's pathological inputs (test/pathological_tests.py), sizes as there."""
    return [
        ('nested strong emph', ('*a **a ' * 32500) + 'b' + (' a** a*' * 32500)),
        ('many emph closers with no openers', 'a_ ' * 32500),
        ('many emph openers with no closers', '_a ' * 32500),
        ('many link closers with no openers', 'a]' * 32500),
        ('many link openers with no closers', '[a' * 32500),
        ('mismatched openers and closers', '*a_ ' * 25000),
        ('issue #389', '*a ' * 20000 + '_a*_ ' * 20000),
        ('openers and closers multiple of 3', 'a**b' + 'c* ' * 25000),
        ('link openers and emph closers', '[ a_' * 25000),
        ('pattern [ (]( repeated', '[ (](' * 40000),
        ('pattern ![[]() repeated', '![[]()' * 160000),
        ('hard link/emph case', '**x [a*b**c*](d)'),
        ('nested brackets', '[' * 25000 + 'a' + ']' * 25000),
        ('nested block quotes', '> ' * 25000 + 'a'),
        ('deeply nested lists', ''.join('  ' * x + '* a\n' for x in range(500))),
        ('U+0000 in input', 'abc\x00de\x00'),
        ('backticks', ''.join('e' + '`' * x for x in range(1, 2500))),
        ('unclosed links A', '[a](<b' * 30000),
        ('unclosed links B', '[a](b' * 30000),
        ('unclosed <!--', '</' + '<!--' * 300000),
        ('empty lines in deeply nested lists', '- ' * 30000 + 'x' + '\n' * 30000),
        ('empty lines in deeply nested lists in blockquote', '> ' + '- ' * 30000 + 'x\n' + '>\n' * 30000),
        ('emph in deep blockquote', '>' * 100000 + 'a*' * 100000),
        ('nested inlines', '*' * 20000 + 'a' + '*' * 20000),
        ('many references', ''.join('[%d]: u\n' % x for x in range(1, 5000 * 16)) + '[0] ' * 5000),
        ('many definitions used', ''.join('[a%d]: /url%d\n' % (x, x) for x in range(20000)) + ''.join('[a%d] ' % (x % 100) for x in range(20000))),
        ('long paragraph', 'word ' * 400000),
        ('many paragraphs', 'para *x* [l](u)\n\n' * 100000),
    ]


def real_docs():
    docs = []
    for dp, dn, fn in os.walk(ROOT):
        dn[:] = [d for d in dn if d not in ('.git', 'archive', 'out', 'node_modules', '.claude')]
        for f in fn:
            if f.lower().endswith('.md'):
                docs.append(os.path.join(dp, f))
    return sorted(docs)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--c', action='store_true', help='build through C instead of native')
    ap.add_argument('-j', type=int, default=8)
    ap.add_argument('--ref', default=os.environ.get('CMARK_REF') or shutil.which('cmark'))
    ap.add_argument('--exe')
    ap.add_argument('-k', default='')
    ap.add_argument('-v', action='store_true')
    ap.add_argument('--no-patho', action='store_true')
    a = ap.parse_args()
    ref = a.ref
    work = tempfile.mkdtemp(prefix='obx-md-')
    try:
        exe = a.exe or build(a.c, work)
        ok = True
        with concurrent.futures.ThreadPoolExecutor(a.j) as pool:
            spec = [(e['markdown'], e['html'], e['section'], e['example']) for e in load_spec()]
            if a.k in ('', 'spec'):
                ok &= report('spec', suite_examples(pool, exe, spec, ['--unsafe'], 'spec', a.v), a.v)
            if a.k in ('', 'smart'):
                ok &= report('smart', suite_examples(pool, exe, load_examples('smart_punct.txt'), ['--unsafe', '--smart'], 'smart', a.v), a.v)
            if a.k in ('', 'regress'):
                ok &= report('regress', suite_examples(pool, exe, load_examples('regression.txt'), ['--unsafe'], 'regress', a.v), a.v)
            if ref is None:
                print('no reference cmark (--ref / $CMARK_REF): differential suites skipped')
            else:
                if a.k in ('', 'safe'):
                    ok &= report('safe', suite_examples(pool, exe, spec, [], 'safe', a.v, ref), a.v)
                    ok &= report('safe+smart', suite_examples(pool, exe, spec, ['--smart'], 'smart', a.v, ref), a.v)
                    ok &= report('sourcepos', suite_examples(pool, exe, spec, ['--unsafe', '--sourcepos'], 'sourcepos', a.v, ref), a.v)
                    ok &= report('hardbreaks', suite_examples(pool, exe, spec, ['--unsafe', '--hardbreaks'], 'hardbreaks', a.v, ref), a.v)
                    ok &= report('nobreaks', suite_examples(pool, exe, spec, ['--unsafe', '--nobreaks'], 'nobreaks', a.v, ref), a.v)
                if a.k in ('', 'diff'):
                    cases = [(n, t.encode('utf-8', 'surrogatepass')) for n, t in gen_cases()]
                    for p in real_docs():
                        with open(p, 'rb') as f:
                            cases.append((os.path.relpath(p, ROOT), f.read()))
                    res = Result()

                    def one(c):
                        name, data = c
                        for args in (['--unsafe'], [], ['--smart', '--sourcepos', '--unsafe']):
                            want, _, _ = run(ref, args, data, check=False)
                            out, err, _ = run(exe, args, data)
                            if out != want or err:
                                return name, False, 'diff %s %s\n   want: %s\n   got:  %s %s' % (name, args, show(want[:300]), show(out[:300] if out else out), show(err))
                        return name, True, ''
                    for name, good, msg in pool.map(one, cases):
                        res.add('all', good, msg)
                    ok &= report('diff', res, a.v)
                if a.k in ('', 'patho') and not a.no_patho:
                    res = Result()
                    for name, text in patho_cases():
                        data = text.encode()
                        want, _, tref = run(ref, ['--unsafe'], data, timeout=120, check=False)
                        out, err, tour = run(exe, ['--unsafe'], data, timeout=60)
                        good = out == want and not err and tour < max(5.0, 20 * tref)
                        res.add('all', good, 'patho %s: %.2fs (cmark %.2fs) %s' % (name, tour, tref, 'output differs' if out != want else show(err)))
                        if a.v:
                            print('  %-52s obx %.2fs cmark %.2fs %s' % (name, tour, tref, 'ok' if good else 'FAIL'))
                    ok &= report('patho', res, a.v)
        sys.exit(0 if ok else 1)
    finally:
        shutil.rmtree(work, ignore_errors=True)


main()
