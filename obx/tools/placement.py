#!/usr/bin/env python3
"""Code-placement perturbation test (PERFORMANCE.md, "Code placement matters").

    python3 obx/tools/placement.py OBXC OUT.json [NAME ...]     (ONLY=native|c, GCCEXTRA="flags")

Builds each bench (default fib loop array alloc sieve trees maps) eight times with its code moved
by 0, 8, ..., 56 bytes, and prints the best of 7 cycles (perf stat, taskset -c 4) of each and
their spread. Native: OBX_PADB=n puts n bytes before every function's alignment (code growth ahead
of it). C: gcc -S, then `.nops n` after every function's label (the function's code moves inside
its aligned start). Same instructions up to the nops; only the placement changes. A spread above
a few percent is placement (or, for the memory-bound sieve and maps, other load on the machine).
"""
import json, os, re, subprocess, sys, tempfile

obxc, outp = os.path.abspath(sys.argv[1]), os.path.abspath(sys.argv[2])
names = sys.argv[3:] or ['fib', 'loop', 'array', 'alloc', 'sieve', 'trees', 'maps']
root = os.path.dirname(os.path.dirname(os.path.dirname(obxc)))      # obx/
bench = os.path.join(root, 'bench')
extra = os.environ.get('GCCEXTRA', '').split()
only = os.environ.get('ONLY', '')
tmp = tempfile.mkdtemp(dir=os.path.dirname(outp))


def cycles(exe, n=7):
    best = None
    for _ in range(n):
        r = subprocess.run(['taskset', '-c', '4', 'perf', 'stat', '-x,', '-e', 'instructions:u,cycles:u', exe],
                           capture_output=True, text=True)
        d = {}
        for l in r.stderr.splitlines():
            p = l.split(',')
            if len(p) > 2 and p[0].isdigit():
                d[p[2].split(':')[0]] = int(p[0])
        if best is None or d['cycles'] < best[1]:
            best = (d['instructions'], d['cycles'])
    return best


res = {}
for name in names:
    src = os.path.join(bench, name + '.obx')
    j = json.loads(subprocess.run([obxc, src, '--emit-c-json'], capture_output=True, text=True, check=True).stdout)
    cf = os.path.join(tmp, name + '.c')
    open(cf, 'w').write(j['c'])
    c1 = [a for a in j['cmd'] if a not in ('-o', '@OUT@', '@SRC@', 'gcc')]
    libs = [a for a in c1 if a.startswith('-l')]
    flags = [a for a in c1 if not a.startswith('-l')]
    for pad in range(0, 64, 8):
        if only != 'c':
            exe = os.path.join(tmp, f'{name}_n{pad}')
            subprocess.run([obxc, src, '--native', '-o', exe], env=dict(os.environ, OBX_PADB=str(pad)), check=True)
            res.setdefault(name, {}).setdefault('native', {})[pad] = cycles(exe)
        if only == 'native':
            continue
        sfile = os.path.join(tmp, f'{name}_{pad}.s')
        subprocess.run(['gcc', '-S'] + flags + extra + ['-o', sfile, cf], check=True)
        asm = open(sfile).read()
        if pad:
            asm = re.sub(r'(\t\.type\t([^,\n]+), @function\n\2:\n)', f'\\1\t.nops {pad}\n', asm)
        open(sfile, 'w').write(asm)
        exe = os.path.join(tmp, f'{name}_c{pad}')
        subprocess.run(['gcc', '-o', exe, sfile] + libs, check=True)
        res.setdefault(name, {}).setdefault('c', {})[pad] = cycles(exe)
    for be in [b for b in ('native', 'c') if only in ('', b)]:
        cs = [v[1] for v in res[name][be].values()]
        print(f'{name:6} {be:6} min {min(cs)/1e6:7.0f}M max {max(cs)/1e6:7.0f}M spread {100*(max(cs)-min(cs))/min(cs):5.1f}%  ',
              ' '.join(f'{v[1]/1e6:.0f}' for v in res[name][be].values()), flush=True)
json.dump(res, open(outp, 'w'))
