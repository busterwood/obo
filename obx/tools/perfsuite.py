#!/usr/bin/env python3
"""Compare two obx compilers on frozen inputs: compile speed and run speed.

    python3 obx/tools/perfsuite.py BASE NEW [-n RUNS] [--only WORD ...]
                                   [--frozen REV] [--work DIR]

BASE and NEW are each a git revision (main, HEAD, a branch, a hash) or the
path of a native-built compiler. For a revision the script builds that
revision's native compiler: gcc builds its seed (seed/obxc.c), and that
compiler builds the revision's main.obx with --native.

Every input comes from one frozen revision of the sources (FROZEN below,
or --frozen), so the inputs do not grow with each pass.

- Compile suite: each compiler compiles the frozen compiler (--native and
  --emit-c) and the frozen eza, grep and zoxide (--native). This is the
  compile speed a user sees. It counts both the cost of the new analysis
  and whatever the compiler gains from being compiled better itself.
- Run suite: each compiler builds the frozen bench programs and the
  frozen grep, sort, wc and eza with --native. The programs then run on
  fixed inputs; the 100 MB text is generated once, the same every time.
  Both builds must print the same output.

Instructions are the least of RUNS and cycles the best of RUNS (user
mode), with base and new interleaved. The summary is the geometric mean
of new/base for each suite. PERFORMANCE.md, "Judging a pass", says how
the two numbers are weighed. Needs perf and gcc.
"""
import argparse
import math
import os
import random
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
FROZEN = 'af99f933'         # move only on purpose, and say so in PERFORMANCE.md (was 7d519d1
                            # until the literal suffixes of 2026-11-06)
BENCH = ['fib', 'loop', 'array', 'alloc', 'lists', 'strings', 'trees', 'tokens', 'sieve', 'maps']
TOOLS = ['grep', 'sort', 'wc', 'eza']


def sh(cmd, **kw):
    return subprocess.run(cmd, check=True, **kw)


def extract(rev, work):
    """The obx/ tree of rev, under work/src-REV (once)."""
    h = sh(['git', '-C', ROOT, 'rev-parse', '--short=12', rev], capture_output=True, text=True).stdout.strip()
    d = os.path.join(work, 'src-' + h)
    if not os.path.isdir(d):
        tmp = d + '.tmp'
        shutil.rmtree(tmp, ignore_errors=True)
        os.makedirs(tmp)
        arc = subprocess.Popen(['git', '-C', ROOT, 'archive', h, 'obx/compiler', 'obx/lib', 'obx/cmd', 'obx/bench'],
                               stdout=subprocess.PIPE)
        sh(['tar', '-x', '-C', tmp], stdin=arc.stdout)
        if arc.wait() != 0:
            sys.exit(f'git archive {rev} failed')
        os.rename(tmp, d)
    return h, d


def compiler(spec, work):
    """A native-built compiler: the path given, or built from a revision."""
    if os.path.isfile(spec):
        return os.path.abspath(spec), os.path.basename(spec)
    h, d = extract(spec, work)
    out = os.path.join(work, 'obxc-' + h)
    if not os.path.exists(out):
        comp = os.path.join(d, 'obx', 'compiler')
        print(f'building the native compiler of {spec} ({h})', file=sys.stderr, flush=True)
        sh(['gcc', '-O2', '-w', '-std=gnu11', '-o', out + '.seed', os.path.join(comp, 'seed', 'obxc.c'), '-lm'])
        env = dict(os.environ, OBX_LIB=os.path.join(d, 'obx', 'lib'))
        sh([out + '.seed', 'main.obx', '--native', '-o', out + '.tmp'], cwd=comp, env=env)
        os.rename(out + '.tmp', out)
    return out, f'{spec} ({h})'


def text100(work):
    """100 MB of numbered lines of Greek letter names, some with needles."""
    p = os.path.join(work, 'text100.txt')
    if os.path.exists(p):
        return p
    words = ('alpha beta gamma delta epsilon zeta eta theta iota kappa lambda mu nu xi omicron '
             'pi rho sigma tau upsilon phi chi psi omega').split()
    rng = random.Random(20261104)
    out, size, i = [], 0, 0
    while size < 100_000_000:
        ws = [rng.choice(words) for _ in range(rng.randint(1, 12))]
        if rng.random() < 0.01:
            ws.append(f'needle{i % 100}')
        line = f'{i:07d} ' + ' '.join(ws) + '\n'
        out.append(line)
        size += len(line)
        i += 1
    data = ''.join(out)[:100_000_000]
    with open(p + '.tmp', 'w') as f:
        f.write(data[:data.rfind('\n') + 1])
    os.rename(p + '.tmp', p)
    return p


def stat(cmd, env, cwd=None):
    r = subprocess.run(['perf', 'stat', '-x,', '-e', 'instructions:u,cycles:u'] + cmd, env=env, cwd=cwd,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if r.returncode != 0:
        sys.exit(f'failed: {" ".join(cmd)}\n{r.stderr.decode(errors="replace")[-2000:]}')
    lines = r.stderr.decode().strip().splitlines()[-2:]
    return [int(l.split(',')[0]) for l in lines], r.stdout


def measure(cmds, runs, env, cwd=None):
    """cmds: [base command, new command]. Interleaved; least instructions, best cycles."""
    best = [[math.inf, math.inf], [math.inf, math.inf]]
    outs = [None, None]
    for _ in range(runs):
        for k in (0, 1):
            (ins, cyc), out = stat(cmds[k], env, cwd)
            best[k] = [min(best[k][0], ins), min(best[k][1], cyc)]
            outs[k] = out
    return best, outs


def row(label, best):
    (bi, bc), (ni, nc) = best
    print(f'{label:24s} {bi / 1e6:9.1f}M {ni / 1e6:9.1f}M {100 * (ni / bi - 1):+6.2f}%   '
          f'{bc / 1e6:8.1f}M {nc / 1e6:8.1f}M {100 * (nc / bc - 1):+6.2f}%', flush=True)
    return ni / bi, nc / bc


def geo(xs):
    return math.exp(sum(math.log(x) for x in xs) / len(xs)) if xs else 1.0


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('base')
    ap.add_argument('new')
    ap.add_argument('-n', type=int, default=5, help='runs of each (default 5)')
    ap.add_argument('--only', nargs='*', default=[], help='only the workloads whose label contains a word')
    ap.add_argument('--frozen', default=FROZEN, help=f'revision of the inputs (default {FROZEN})')
    ap.add_argument('--work', default=os.path.join(os.environ.get('TMPDIR', '/tmp'), 'obx-perfsuite'),
                    help='cache of sources, compilers, programs and inputs')
    a = ap.parse_args()
    os.makedirs(a.work, exist_ok=True)
    cs = [compiler(a.base, a.work), compiler(a.new, a.work)]
    fh, fd = extract(a.frozen, a.work)
    fobx = os.path.join(fd, 'obx')
    env = dict(os.environ, OBX_LIB=os.path.join(fobx, 'lib'))
    env.pop('OBX_CHECK', None)
    want = lambda label: not a.only or any(w in label for w in a.only)
    print(f'base {cs[0][1]}\nnew  {cs[1][1]}\ninputs frozen at {fh}, best of {a.n}\n')
    print(f'{"":24s} {"base instr":>10s} {"new instr":>10s} {"":7s}   {"base cyc":>9s} {"new cyc":>9s}')

    tmp = os.path.join(a.work, 'out')
    os.makedirs(tmp, exist_ok=True)
    print('compile suite')
    comp = []
    jobs = [('compiler --native', 'compiler', 'main.obx', ['--native']),
            ('compiler --emit-c', 'compiler', 'main.obx', ['--emit-c']),
            ('eza --native', 'cmd', 'eza.obx', ['--native']),
            ('grep --native', 'cmd', 'grep.obx', ['--native']),
            ('zoxide --native', 'cmd', 'zoxide.obx', ['--native'])]
    for label, sub, src, flags in jobs:
        if not want(label):
            continue
        out = ['-o', os.path.join(tmp, 'c.out')] if '--native' in flags else []
        best, _ = measure([[c, src] + flags + out for c, _ in cs], a.n, env, cwd=os.path.join(fobx, sub))
        comp.append(row(label, best))

    print('run suite')
    progs = {}
    def prog(k, name, src):
        p = os.path.join(tmp, f'{name}-{k}')
        sh([cs[k][0], os.path.basename(src), '--native', '-o', p], cwd=os.path.dirname(src), env=env)
        return p
    for name in BENCH:
        progs[name] = [prog(k, name, os.path.join(fobx, 'bench', name + '.obx')) for k in (0, 1)]
    for name in TOOLS:
        progs[name] = [prog(k, name, os.path.join(fobx, 'cmd', name + '.obx')) for k in (0, 1)]
    t = text100(a.work)
    runs = [(b, b, [os.path.join(fobx, 'compiler', 'sema.obx')] if b == 'tokens' else []) for b in BENCH]
    runs += [('grep -n omega', 'grep', ['-n', 'omega', t]), ('grep -cE a.*z.*e', 'grep', ['-c', '-E', 'a.*z.*e', t]),
             ('grep -cw eta', 'grep', ['-c', '-w', 'eta', t]), ('sort', 'sort', [t]), ('wc -w', 'wc', ['-w', t]),
             ('eza -T /usr/share', 'eza', ['-T', '/usr/share'])]
    run = []
    for label, name, args in runs:
        if not want(label):
            continue
        best, outs = measure([[progs[name][k]] + args for k in (0, 1)], a.n, env)
        if outs[0] != outs[1]:
            sys.exit(f'{label}: base and new builds print different output')
        run.append(row(label, best))

    print('\ngeometric mean of new/base     instructions   cycles')
    for label, rs in (('compile suite', comp), ('run suite', run)):
        if rs:
            gi, gc = geo([r[0] for r in rs]), geo([r[1] for r in rs])
            print(f'{label:30s} {100 * (gi - 1):+10.2f}%  {100 * (gc - 1):+7.2f}%')


if __name__ == '__main__':
    main()
