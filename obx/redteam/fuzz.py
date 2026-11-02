#!/usr/bin/env python3
"""Mutation fuzzer: mutate test programs token-wise and report compiler crashes
(`obxc --emit-c` under OBX_CHECK=1: any result but exit 0, or exit 1 with one `file:line: message`,
is a crash or a leak; no C build unless FZGCC=1). usage: fuzz.py N seed
The compiler is $OBXC, else compiler/out/obxc (until ref was retired this caught ref's Python
tracebacks in-process; compiler/fuzz.py is the general mutant fuzzer)."""
import os, sys, random, re, glob, subprocess
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', 'tools'))
import cbuild
MESSAGE = re.compile(r'^[^\n]+:\d+: [^\n]+\n?$')
srcs = sorted(glob.glob(os.path.join(HERE, '..', 'tests', 'run', '*.obx')) + glob.glob(os.path.join(HERE, 'cases', '*.obx')))
TOK = re.compile(r'\s+|//[^\n]*|"(?:\\.|[^"\\])*"|\w+|..?')
EXTRA = ['&', 'mut &', 'move(', ')', '[', ']', '{', '}', 'try ', '-', '0', '..', '.', ',', ';', '\n', 'T', 'int',
         'str', '[]', 'fn ', 'return', 'break', 'None', 'Some(', 'x', 'self', '_', 'match ', 'value ', '<<', '%']
def mutate(s, r):
    toks = [t for t in TOK.findall(s)]
    for _ in range(r.randint(1, 3)):
        i = r.randrange(len(toks))
        op = r.random()
        if op < 0.3: del toks[i]
        elif op < 0.6: toks.insert(i, r.choice(EXTRA))
        elif op < 0.8: toks[i] = r.choice(toks)
        else:
            j = r.randrange(len(toks)); toks[i], toks[j] = toks[j], toks[i]
    return ''.join(toks)
GCC = os.environ.get('FZGCC') == '1'
def main():
    n, seed = int(sys.argv[1]), int(sys.argv[2])
    r = random.Random(seed)
    seen = {}
    path = os.path.join(HERE, 'build', f'fz{seed}.obx')
    os.makedirs(os.path.dirname(path), exist_ok=True)
    for k in range(n):
        src = open(r.choice(srcs)).read()
        m = mutate(src, r)
        open(path, 'w').write(m)
        c = subprocess.run(cbuild.obxc_argv() + [path, '--emit-c'], capture_output=True, text=True,
                           errors='replace', env=dict(os.environ, OBX_CHECK='1'))
        if c.returncode == 1 and MESSAGE.match(c.stderr):
            continue
        if c.returncode != 0 or c.stderr:
            last = (c.stderr.strip().splitlines() or [''])[-1]
            key = ('EXIT', c.returncode, re.sub(r'[^a-z ]', '', last)[:40])
            if key not in seen:
                seen[key] = m
                out = os.path.join(HERE, 'build', f'crash_{c.returncode}_{len(seen)}_{seed}.obx')
                open(out, 'w').write(m)
                print(key, last[:100], '->', out, flush=True)
            continue
        csrc = c.stdout
        if GCC:
            cf = path[:-4] + '.c'; open(cf, 'w').write(csrc)
            g = subprocess.run(['gcc', '-fsyntax-only', '-w', '-std=gnu11', cf], capture_output=True, text=True)
            if g.returncode:
                line = next((l for l in g.stderr.splitlines() if 'error' in l), '')
                key = ('CC', re.sub(r'[^a-z ]', '', line.split('error:')[-1])[:40])
                if key not in seen:
                    seen[key] = m
                    out = os.path.join(HERE, 'build', f'ccfail_{len(seen)}_{seed}.obx'); open(out, 'w').write(m)
                    print(key, line[:150], '->', out, flush=True)
    print('done', len(seen))
main()
