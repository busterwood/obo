#!/usr/bin/env python3
"""Checks the examples in obo/LANGUAGE.md against the compiler.

A ```obo block whose first line is "// example: runs" must compile and exit 0
(or with the code given by "// exit: N"), printing the "// prints" lines.
"// example: error: TEXT" must be rejected with a message containing TEXT.
"// example: compiles" must compile.

A multi-file example starts "// example: modules" (or "modules error: TEXT"):
inside it, a line "// file: path" starts a new source file, the first is the
root, and the root is compiled with -I <example dir>, a private --sym-dir and a
private output name. "// prints"/"// exit" apply to the root program.
"// example: modules check" builds the root, deletes the dependency sources and
then runs --check against the emitted symbol files (expecting success, or the
text after "modules check error: TEXT").

    python3 obo/tests/doc_check.py
"""
import os, re, subprocess, sys, tempfile, textwrap
HERE = os.path.dirname(os.path.abspath(__file__))
FORK = os.path.dirname(HERE)
DOC = os.path.join(FORK, 'LANGUAGE.md')
OBO = os.environ.get('OBO', os.path.join(FORK, 'obo'))
RT = FORK
text = open(DOC).read()
blocks = [textwrap.dedent(b) for b in re.findall(r'^[ \t]*```obo\n(.*?)^[ \t]*```', text, re.S | re.M)]
tmp = tempfile.mkdtemp(prefix='doccheck-')
bad = 0
for i, b in enumerate(blocks, 1):
    lines = b.splitlines()
    head = lines[0]
    check_mods = head.startswith('// example: modules check')
    if check_mods:
        rest = head[len('// example: modules check'):].strip()
        want = rest[len('error:'):].strip() if rest.startswith('error:') else None
        m = re.match(r'// example: modules', head)
    else:
        m = re.match(r'// example: (runs|compiles|modules|error: (.*)|modules error: (.*))$', head)
        want = None
    if not m:
        print(f"block {i}: no example marker"); bad += 1; continue
    kind = 'modules' if check_mods else ('error' if m.group(2) else m.group(1))
    if kind == 'modules' or (not check_mods and m.group(3)):
        if not check_mods:
            want = m.group(3) if m.group(3) else m.group(2)
        case = os.path.join(tmp, f'm{i}')
        os.makedirs(case)
        files = []                       # (path, [lines])
        for line in lines[1:]:
            if line.startswith('// file: '):
                files.append((line[len('// file: '):].strip(), []))
            elif files:
                files[-1][1].append(line)
        if not files:
            print(f"block {i}: modules example with no // file: lines"); bad += 1; continue
        for name, body in files:
            fp = os.path.join(case, name)
            os.makedirs(os.path.dirname(fp), exist_ok=True)
            open(fp, 'w').write('\n'.join(body) + '\n')
        root = os.path.join(case, files[0][0])
        out = os.path.join(case, 'prog')
        symdir = os.path.join(case, 'sym')
        os.makedirs(symdir)
        c = subprocess.run([OBO, root, '-o', out, '--rt', RT, '-I', case, '--sym-dir', symdir],
                           capture_output=True, text=True)
        if want is not None:
            if c.returncode == 0:
                print(f"block {i}: expected error '{want}', but it compiled"); bad += 1
            elif want not in c.stderr:
                print(f"block {i}: error differs: wanted '{want}', got: {c.stderr.strip()}"); bad += 1
            else:
                print(f"block {i}: ok (modules error '{want}')")
            continue
        if c.returncode != 0:
            print(f"block {i}: modules did not compile: {c.stderr.strip()}"); bad += 1; continue
        if check_mods:
            # source-less checking: remove the dependency sources, keep the .sym files
            for name, _ in files[1:]:
                os.unlink(os.path.join(case, name))
            chk = subprocess.run([OBO, '--check', root, '-I', case, '--sym-dir', symdir],
                                 capture_output=True, text=True)
            if want is not None:
                if chk.returncode == 0:
                    print(f"block {i}: expected check error '{want}', but it passed"); bad += 1
                elif want not in chk.stderr:
                    print(f"block {i}: check error differs: wanted '{want}', got: {chk.stderr.strip()}"); bad += 1
                else:
                    print(f"block {i}: ok (modules check error '{want}')")
                continue
            if chk.returncode != 0:
                print(f"block {i}: modules --check failed: {chk.stderr.strip()}"); bad += 1; continue
        exp_exit = next((int(re.search(r'// exit: (\d+)', l).group(1)) for l in lines if '// exit:' in l), 0)
        prints = [l[len('// prints '):] for l in lines if l.startswith('// prints ')]
        r = subprocess.run([out], capture_output=True, text=True, env=dict(os.environ, OBO_CHECK='1', OBX_GC='poison'))
        if r.returncode != exp_exit:
            print(f"block {i}: exit {r.returncode}, expected {exp_exit}: {r.stderr.strip()}"); bad += 1; continue
        if exp_exit == 0 and r.stdout.splitlines() != prints:
            print(f"block {i}: output {r.stdout.splitlines()} expected {prints}"); bad += 1; continue
        print(f"block {i}: ok (modules run, exit {exp_exit})")
        continue
    src = '\n'.join(lines) + '\n'
    path = os.path.join(tmp, f'b{i}.obx')
    open(path, 'w').write(src)
    c = subprocess.run([OBO, path, '-o', path[:-4], '--rt', RT], capture_output=True, text=True)
    if kind == 'error':
        want = m.group(2)
        if c.returncode == 0:
            print(f"block {i}: expected error '{want}', but it compiled"); bad += 1
        elif want not in c.stderr:
            print(f"block {i}: error differs: wanted '{want}', got: {c.stderr.strip()}"); bad += 1
        else:
            print(f"block {i}: ok (error '{want}')")
        continue
    if c.returncode != 0:
        print(f"block {i}: did not compile: {c.stderr.strip()}"); bad += 1; continue
    if kind == 'compiles':
        print(f"block {i}: ok (compiles)"); continue
    exp_exit = next((int(re.search(r'// exit: (\d+)', l).group(1)) for l in lines if '// exit:' in l), 0)
    prints = [l[len('// prints '):] for l in lines if l.startswith('// prints ')]
    r = subprocess.run([path[:-4]], capture_output=True, text=True, env=dict(os.environ, OBO_CHECK='1', OBX_GC='poison'))
    if r.returncode != exp_exit:
        print(f"block {i}: exit {r.returncode}, expected {exp_exit}: {r.stderr.strip()}"); bad += 1; continue
    if exp_exit == 0 and r.stdout.splitlines() != prints:
        print(f"block {i}: output {r.stdout.splitlines()} expected {prints}"); bad += 1; continue
    print(f"block {i}: ok (runs, exit {exp_exit})")
print(f"\n{len(blocks)} examples, {bad} problems")
sys.exit(1 if bad else 0)
