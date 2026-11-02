#!/usr/bin/env python3
"""diff.py dir: run the gen.py pairs (xNNNNA.obx, xNNNNB.obx) under ASan+UBSan and OBX_CHECK=1.
Reports A's sanitizer/leak/crash/C-compile failures, A rejected while B is accepted (grouped by
message), and output or exit differences between A and B. CC=clang to use clang."""
import os, re, subprocess, sys, glob
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'tools'))
import cbuild
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
HERE = os.path.dirname(os.path.abspath(__file__))
OBXC = cbuild.OBXC   # $OBXC, else compiler/out/obxc
CC = os.environ.get('CC', 'gcc')

def build_run(path):
    build = os.path.join(os.path.dirname(path), 'build_' + os.path.basename(CC))
    os.makedirs(build, exist_ok=True)
    name = os.path.splitext(os.path.basename(path))[0]
    c = subprocess.run(cbuild.obxc_argv(OBXC) + [path, '--emit-c'], capture_output=True, text=True, timeout=60)
    if 'Traceback' in c.stderr or c.returncode not in (0, 1):
        return ('CRASH', f'exit {c.returncode}: ' + (c.stderr.strip().splitlines() or [''])[-1])
    if c.returncode:
        return ('REJECT', re.sub(r'^\S+:\d+: ', '', c.stderr.strip()))
    cf, exe = os.path.join(build, name + '.c'), os.path.join(build, name)
    open(cf, 'w').write(c.stdout)
    san = 'address,undefined'
    grc, gerr = cbuild.cc_build(c.stdout, [CC, '-g', '-O0', '-std=gnu11', '-fsanitize=' + san, '-fno-sanitize-recover=undefined', '-w',
                        '-o', '@OUT@', '@SRC@', '-lm'], exe)
    if grc:
        return ('CCFAIL', gerr[:300])
    env = dict(os.environ, OBX_CHECK='1', ASAN_OPTIONS='detect_leaks=1:exitcode=99',
               UBSAN_OPTIONS='print_stacktrace=1:halt_on_error=1:exitcode=98')
    try:
        r = subprocess.run([exe], capture_output=True, text=True, errors='replace', timeout=20, env=env)
    except subprocess.TimeoutExpired:
        return ('TIMEOUT', '')
    os.remove(exe)
    if 'AddressSanitizer' in r.stderr or 'runtime error:' in r.stderr or r.returncode in (98, 99):
        return ('ASAN', next((l for l in r.stderr.splitlines() if 'ERROR' in l or 'runtime error' in l), ''))
    if 'LeakSanitizer' in r.stderr or r.returncode == 5:
        return ('LEAK', r.stderr[:200])
    trap = next((l for l in r.stderr.splitlines() if l.startswith('obx:')), '')
    return ('RUN', (r.stdout, r.returncode, re.sub(r' at \S+', '', trap)))

def pair(a):
    b = a[:-5] + 'B.obx'
    return a, build_run(a), build_run(b)

def main():
    d = sys.argv[1]
    files = sorted(glob.glob(os.path.join(d, '*A.obx')))
    with ThreadPoolExecutor(int(os.environ.get('J', '8'))) as ex:
        res = list(ex.map(pair, files))
    kinds, rejects = Counter(), defaultdict(list)
    for a, ra, rb in res:
        head = open(a).readline().strip()
        if ra[0] in ('CRASH', 'CCFAIL', 'ASAN', 'LEAK', 'TIMEOUT'):
            kinds[ra[0]] += 1
            print(f'{ra[0]:8} {os.path.basename(a)} {head} :: {ra[1][:200]}')
        elif ra[0] == 'REJECT':
            kinds['REJECT-A' + ('' if rb[0] == 'REJECT' else ' (B ok)')] += 1
            if rb[0] != 'REJECT':
                rejects[ra[1][:90]].append(os.path.basename(a) + ' ' + head)
        elif rb[0] != 'RUN':
            kinds['B-' + rb[0]] += 1
            print(f'B-{rb[0]:6} {os.path.basename(a)} {head} :: {str(rb[1])[:200]}')
        elif ra[1] != rb[1]:
            kinds['DIFF'] += 1
            print(f'DIFF     {os.path.basename(a)} {head}\n   A={ra[1]!r:.300}\n   B={rb[1]!r:.300}')
        else:
            kinds['SAME' + (' trap' if ra[1][1] else '')] += 1
    for msg, xs in rejects.items():
        print(f'REJECT(A only) x{len(xs)}: {msg}\n    e.g. {xs[0]}')
    print(dict(kinds))

main()
