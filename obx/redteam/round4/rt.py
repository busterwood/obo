#!/usr/bin/env python3
"""Red-team runner: compile each case with --emit-c, build with ASan+UBSan, run with OBX_CHECK=1,
compare to directives (// expect:, // exit:, // error:). Classify:
  OK        matches the directives
  ASAN      sanitizer report (memory error / UB)
  LEAK      exit 5 under OBX_CHECK (or LeakSanitizer)
  CRASH     compiler crash (exit status other than 0 and 1, or a traceback)
  CCFAIL    generated C failed to compile
  REJECT    compile error but a run was expected
  ACCEPT    compiled but an error was expected
  MISMATCH  output/exit differs
usage: rt.py [--probe] [files or dirs...]  (--probe: build and run even cases that expect an error; print exit, output and the first sanitizer line)
        (default: cases/, mcases/*/main.obx and k*.obx)
"""
import os, re, subprocess, sys, glob
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'tools'))
import cbuild
from concurrent.futures import ThreadPoolExecutor
HERE = os.path.dirname(os.path.abspath(__file__))
OBXC = cbuild.OBXC   # $OBXC, else compiler/out/obxc
BUILD = os.path.join(HERE, 'build' + ('_' + os.path.basename(os.environ['CC']) if os.environ.get('CC') else ''))
DIRECTIVE = re.compile(r'^\s*//\s*(expect|exit|error):(.*)$')

def directives(path):
    d = {'expect': [], 'exit': 0, 'error': None}
    for raw in open(path, encoding='utf-8'):
        m = DIRECTIVE.match(raw.rstrip('\n'))
        if not m: continue
        k, v = m.group(1), m.group(2)
        if v.startswith(' '): v = v[1:]
        if k == 'expect': d['expect'].append(v)
        elif k == 'exit': d['exit'] = int(v.strip())
        else: d['error'] = (d['error'] or []) + [v.strip()]
    return d

def run1(path):
    name = os.path.splitext(os.path.basename(path))[0]
    d = directives(path)
    if name == 'main':
        name = os.path.basename(os.path.dirname(path))
    try:
        c = subprocess.run(cbuild.obxc_argv(OBXC) + [path, '--emit-c'], capture_output=True, text=True, timeout=30)
    except subprocess.TimeoutExpired:
        return name, 'HANG', 'compiler did not finish in 30 s'
    if 'Traceback' in c.stderr or c.returncode not in (0, 1):
        return name, 'CRASH', f'exit {c.returncode}: ' + (c.stderr.strip().splitlines() or [''])[-1]
    if c.returncode != 0:
        msg = c.stderr.strip()
        if d['error'] is not None:
            ok = all(any(w.lower() in msg.lower() for w in e.split('|')) for e in d['error'] if e)
            return name, 'OK' if ok else 'WRONGMSG', msg
        return name, 'REJECT', msg
    if d['error'] is not None and not PROBE:
        return name, 'ACCEPT', 'compiled'
    cfile = os.path.join(BUILD, name + '.c'); exe = os.path.join(BUILD, name)
    open(cfile, 'w').write(c.stdout)
    cc = os.environ.get('CC', 'gcc')
    san = 'address,undefined' + (',function' if 'clang' in cc else '')
    grc, gerr = cbuild.cc_build(c.stdout, [cc, '-g', '-O0', '-std=gnu11', '-fsanitize=' + san,
                        '-fno-sanitize-recover=undefined', '-w', '-o', '@OUT@', '@SRC@', '-lm'], exe)
    if grc != 0:
        return name, 'CCFAIL', gerr[:600]
    env = dict(os.environ, OBX_CHECK='1', ASAN_OPTIONS='detect_leaks=1:abort_on_error=0:exitcode=99',
               UBSAN_OPTIONS='print_stacktrace=1:halt_on_error=1:exitcode=98')
    try:
        r = subprocess.run([exe], capture_output=True, text=True, errors='replace', timeout=20, env=env)
    except subprocess.TimeoutExpired:
        return name, 'TIMEOUT', ''
    err = r.stderr
    if PROBE:
        first = next((l for l in err.splitlines() if 'ERROR' in l or 'runtime error' in l or l.startswith('obx:')), '')
        return name, 'PROBE', f"exit {r.returncode} out={r.stdout.splitlines()[:3]} {first.strip()[:200]}"
    if 'AddressSanitizer' in err or 'runtime error:' in err or r.returncode in (98, 99):
        first = next((l for l in err.splitlines() if 'ERROR' in l or 'runtime error' in l), err[:200])
        return name, 'ASAN', first.strip()
    if 'LeakSanitizer' in err or r.returncode == 5 and d['exit'] != 5:
        return name, 'LEAK', err.strip().splitlines()[0] if err.strip() else ''
    out = r.stdout.split('\n')
    if out and out[-1] == '': out.pop()
    if out != d['expect'] or r.returncode != d['exit']:
        return name, 'MISMATCH', f"exit {r.returncode} (want {d['exit']}) out={out[:8]} want={d['expect'][:8]} err={err.strip()[:150]}"
    return name, 'OK', ''

PROBE = False

def main(args):
    global PROBE
    if args and args[0] == '--probe':
        PROBE, args = True, args[1:]
    os.makedirs(BUILD, exist_ok=True)
    files = []
    for a in args or [os.path.join(HERE, 'cases'), os.path.join(HERE, 'mcases')] + sorted(glob.glob(os.path.join(HERE, 'k*.obx'))):
        if os.path.isdir(a) and os.path.exists(os.path.join(a, 'main.obx')): files.append(os.path.join(a, 'main.obx'))
        elif os.path.isdir(a):
            files += sorted(glob.glob(os.path.join(a, '*.obx')))
            files += sorted(glob.glob(os.path.join(a, '*', 'main.obx')))
        else: files.append(a)
    with ThreadPoolExecutor(8) as ex:
        res = list(ex.map(run1, files))
    for n, k, m in res:
        if k != 'OK' or '-v' in os.environ.get('RTV', ''):
            print(f"{k:9} {n}: {m[:300]}")
    from collections import Counter
    print(dict(Counter(k for _, k, _ in res)))

if __name__ == '__main__':
    main(sys.argv[1:])
