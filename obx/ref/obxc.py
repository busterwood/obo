#!/usr/bin/env python3
"""obxc: the obx reference compiler driver (obx -> C -> gcc).

    python3 obxc.py file.obx [-o out] [--run] [--emit-c] [-L libroot] [-- args...]
"""
import glob, os, subprocess, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.setrecursionlimit(10000)

from syntax import CompileError  # noqa: E402
from sema import Program, FnDecl  # noqa: E402
from check import Checker  # noqa: E402
from emit import Emitter  # noqa: E402

# The library: std.obx (the prelude) and the modules found by `import "name"`.
LIB = os.path.normpath(os.path.join(HERE, '..', 'lib'))
LIBDIRS = ['/usr/lib/x86_64-linux-gnu', '/lib/x86_64-linux-gnu', '/usr/lib64', '/usr/lib',
           '/usr/local/lib', '/lib']


def compile_c(path, libroots):
    prog = Program(os.path.join(LIB, 'std.obx'), libroots + [LIB])
    name = os.path.splitext(os.path.basename(path))[0]
    prog.main = prog.load(path, None, name)
    prog.resolve_all()
    Checker(prog).check_all()
    main = prog.main.scope.get('main')
    if not isinstance(main, FnDecl) or main.extern is not None:
        raise CompileError("the program has no function main", f"{os.path.basename(path)}:1")
    with open(os.path.join(HERE, 'rt.h')) as f:
        rt = f.read()
    return Emitter(prog).program(main, rt)


def lib_flags(libs):
    out = []
    for lib in libs:
        if any(os.path.exists(os.path.join(d, f"lib{lib}.so")) for d in LIBDIRS):
            out.append('-l' + lib)
            continue
        found = sorted(p for d in LIBDIRS for p in glob.glob(os.path.join(d, f"lib{lib}.so.*")))
        out.append(found[0] if found else '-l' + lib)
    return out


def main(argv):
    out, run, emit_c, roots, src, rest = None, False, False, [], None, []
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == '--':
            rest = argv[i + 1:]
            break
        if a == '-o':
            i += 1
            out = argv[i]
        elif a == '--run':
            run = True
        elif a == '--emit-c':
            emit_c = True
        elif a == '-L':
            i += 1
            roots.append(os.path.abspath(argv[i]))
        elif a.startswith('-L'):
            roots.append(os.path.abspath(a[2:]))
        elif src is None:
            src = a
        else:
            sys.exit(f"usage: obxc.py file.obx [-o out] [--run] [--emit-c] [-L libroot]")
        i += 1
    if src is None:
        sys.exit("usage: obxc.py file.obx [-o out] [--run] [--emit-c] [-L libroot]")
    try:
        csrc, libs = compile_c(src, roots)
    except CompileError as e:
        print(str(e), file=sys.stderr)
        return 1
    except RecursionError:
        print(f"{os.path.basename(src)}:1: the program is nested too deeply", file=sys.stderr)
        return 1
    if emit_c:
        sys.stdout.write(csrc)
        return 0
    tmp = tempfile.mkdtemp(prefix='obxc')
    cfile = os.path.join(tmp, 'prog.c')
    with open(cfile, 'w') as f:
        f.write(csrc)
    exe = out or (os.path.join(tmp, 'prog') if run else os.path.splitext(src)[0])
    r = subprocess.run(['gcc', '-O1', '-w', '-std=gnu11', '-o', exe, cfile] + lib_flags(libs) + ['-lm'],
                       capture_output=True, text=True)
    if r.returncode != 0:
        print(f"obxc: internal error: the C compiler failed (C kept in {cfile})\n{r.stderr[:4000]}",
              file=sys.stderr)
        return 3
    os.remove(cfile)
    if run:
        return subprocess.run([exe] + rest).returncode
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
