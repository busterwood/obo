#!/usr/bin/env python3
"""Mutation fuzzer: mutate test programs token-wise and report Python tracebacks
from the compiler (front end only, no C build). usage: fuzz.py N seed"""
import os, sys, random, re, glob, io, contextlib, traceback
HERE = os.path.dirname(os.path.abspath(__file__))
REF = os.path.join(HERE, '..', '..', 'ref')
sys.path.insert(0, REF)
sys.setrecursionlimit(5000)
from syntax import CompileError
import obxc
srcs = sorted(glob.glob(os.path.join(HERE, 'cases', '*.obx')))
TOK = re.compile(r'\s+|//[^\n]*|"(?:\\.|[^"\\])*"|\w+|..?')
EXTRA = ['&', 'mut &', 'move(', ')', '[', ']', '{', '}', 'try ', '-', '0', '..', '.', ',', ';', '\n', 'T', 'int',
         'str', '[]', 'fn ', 'return', 'break', 'None', 'Some(', 'x', 'self', '_', 'match ', 'value ', '<<', '%', '[..]', '[0..1]', '[1..]', '[..0]', '&[int]', 'mut &[int]', 'xs', 's', '""', '"a\\0b"']
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
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                csrc, _ = obxc.compile_c(path, [])
            if GCC:
                import subprocess
                cf = path[:-4] + '.c'; open(cf, 'w').write(csrc)
                g = subprocess.run(['gcc', '-fsyntax-only', '-w', '-std=gnu11', cf], capture_output=True, text=True)
                if g.returncode:
                    line = next((l for l in g.stderr.splitlines() if 'error' in l), '')
                    key = ('CC', re.sub(r'[^a-z ]', '', line.split('error:')[-1])[:40])
                    if key not in seen:
                        seen[key] = m
                        out = os.path.join(HERE, 'build', f'ccfail_{len(seen)}_{seed}.obx'); open(out, 'w').write(m)
                        print(key, line[:150], '->', out, flush=True)
        except CompileError:
            pass
        except RecursionError:
            pass
        except Exception as e:
            tb = traceback.extract_tb(e.__traceback__)[-1]
            key = (type(e).__name__, tb.name, tb.lineno)
            if key not in seen:
                seen[key] = m
                out = os.path.join(HERE, 'build', f'crash_{type(e).__name__}_{tb.name}_{tb.lineno}.obx')
                open(out, 'w').write(m)
                print(key, str(e)[:100], '->', out, flush=True)
    print('done', len(seen))
main()
