#!/usr/bin/env python3
"""Focused mutation gate for the new decimal compiler paths (0.12 WIP).

Each mutant is one textual change in obo/src/obo.obo.  The mutant compiler is
built from seed/obo and run against the decimal subset of run_fork (run files,
error cases and the decimal module tree).  A mutant is caught when at least one
decimal test fails.  This is deliberately separate from the historical
mutate_fork/mutate_port catalogs, which stay unchanged; it is not a rewrite of
those gates.

    python3 obo/tests/mutate_decimal.py [-v]
"""
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
FORK = os.path.dirname(HERE)
SRC = os.path.join(FORK, 'src', 'obo.obo')
SEED = os.path.join(FORK, 'seed', 'obo')
RUNNER = os.path.join(HERE, 'run_fork.py')

MUTANTS = [
    ("literal dscale ignores the explicit exponent",
     "var ds = frac - explicit\n", "var ds = frac\n"),
    ("literal explicit exponent sign is dropped",
     'if !edig { fail("a decimal exponent needs digits") }\n        if eneg { explicit = -explicit }',
     'if !edig { fail("a decimal exponent needs digits") }\n        if eneg { explicit = explicit }'),
    ("literal precision limit is 39 digits",
     'if len(sig) > 38 { fail("decimal literal has more than 38 significant digits") }',
     'if len(sig) > 39 { fail("decimal literal has more than 38 significant digits") }', 2),
    ("literal display scale is packed at bit 15",
     "((d.dscale & 0xffff) << 16)", "((d.dscale & 0xffff) << 15)"),
    ("literal exponent is packed unmasked",
     "(d.exp & 0xffff)", "d.exp"),
    ("literal zero loses its display scale",
     "return new DecLit{lo: 0, hi: 0, exp: 0, dscale: ds}",
     "return new DecLit{lo: 0, hi: 0, exp: 0, dscale: 0}"),
    ("decimal parameter offset is one word per later parameter",
     "return 16 + paramsBytes(params, i + 1)",
     "return 16 + 8 * (len(params) - 1 - i)"),
    ("decimal return destination is one word per visible parameter",
     "16 + visibleTypeParamsBytes(f.fnRec.params)",
     "16 + 8 * len(f.fnRec.params)"),
    ("decimal argument words are pushed low first",
     "let off = 16 - 8 * k", "let off = 8 * k"),
    ("packed scale is multiplied by 8",
     "emit32(256)", "emit32(8)"),
    ("decimal comparison always tests equality",
     "z.cc = relCC(o)", "z.cc = CC_E"),
    ("decimal locals get one word",
     'if t.kind == "decimal" { frame += 24 } else { frame += 8 }',
     'if t.kind == "decimal" { frame += 8 } else { frame += 8 }'),
    ("a decimal copy writes two words",
     "for off in 0..3 {\n        let r = alloc()\n        opMem(0x8B, r, mem(src.base, src.index, src.disp + 8 * off, 8), true, false)\n        opMem(0x89, r, mem(dst.base, dst.index, dst.disp + 8 * off, 8), true, false)\n        release(r)\n    }",
     "for off in 0..2 {\n        let r = alloc()\n        opMem(0x8B, r, mem(src.base, src.index, src.disp + 8 * off, 8), true, false)\n        opMem(0x89, r, mem(dst.base, dst.index, dst.disp + 8 * off, 8), true, false)\n        release(r)\n    }"),
    ("try moves only one word of a Result[decimal] payload",
     "        for off in 0..3 {\n            let dr = alloc()\n            opMem(0x8B, dr, mem(cellR, -1, 8 * off, 8), true, false)\n            opMem(0x89, dr, mem(RBP, -1, decimalDisp + 8 * off, 8), true, false)\n            release(dr)\n        }",
     "        for off in 0..1 {\n            let dr = alloc()\n            opMem(0x8B, dr, mem(cellR, -1, 8 * off, 8), true, false)\n            opMem(0x89, dr, mem(RBP, -1, decimalDisp + 8 * off, 8), true, false)\n            release(dr)\n        }"),
    ("decimal multiply calls the add helper",
     "if o == T_STAR { x = decimalCall2(RT_DEC_MUL, x, y) } else { x = decimalCall2(RT_DEC_REM, x, y) }",
     "if o == T_STAR { x = decimalCall2(RT_DEC_ADD, x, y) } else { x = decimalCall2(RT_DEC_REM, x, y) }"),
    ("decimal subtraction calls the add helper",
     "if o == T_PLUS { x = decimalCall2(RT_DEC_ADD, x, y) } else { x = decimalCall2(RT_DEC_SUB, x, y) }",
     "if o == T_PLUS { x = decimalCall2(RT_DEC_ADD, x, y) } else { x = decimalCall2(RT_DEC_ADD, x, y) }"),
    ("decimal div Result uses the err descriptor for ok",
     "append(args, makeConst(tInt, rt.variants[0].desc))\n    return rtCall(RT_DEC_RESULT, args, rt)",
     "append(args, makeConst(tInt, rt.variants[1].desc))\n    return rtCall(RT_DEC_RESULT, args, rt)"),
]


def main():
    verbose = '-v' in sys.argv[1:]
    text = open(SRC).read()
    tmp = tempfile.mkdtemp(prefix='obo-mut-decimal-')
    caught = missed = broken = 0
    try:
        for item in MUTANTS:
            name, old, new = item[0], item[1], item[2]
            want = item[3] if len(item) > 3 else 1
            n = text.count(old)
            if n != want:
                print(f"BROKEN  {name}: pattern occurs {n} times, want {want}")
                broken += 1
                continue
            src = os.path.join(tmp, 'src.obo')
            exe = os.path.join(tmp, 'obo')
            open(src, 'w').write(text.replace(old, new))
            p = subprocess.run([SEED, src, '-o', exe, '--rt', FORK],
                               capture_output=True, text=True, timeout=180)
            if p.returncode != 0:
                print(f"BROKEN  {name}: mutant did not compile: {p.stderr.strip()[:140]}")
                broken += 1
                continue
            env = dict(os.environ, OBO=exe)
            r = subprocess.run([sys.executable, RUNNER, '-k', 'decimal'],
                               capture_output=True, text=True, timeout=300, env=env)
            if r.returncode != 0:
                caught += 1
                print(f"caught  {name}")
                if verbose:
                    for line in (r.stdout + r.stderr).splitlines()[:8]:
                        print('        ' + line)
            else:
                missed += 1
                print(f"MISSED  {name}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print(f"\n{caught} caught, {missed} missed, {broken} broken (of {len(MUTANTS)})")
    return 1 if missed or broken else 0


if __name__ == '__main__':
    sys.exit(main())
