#!/usr/bin/env python3
"""Mutation testing: checks that the suite catches deliberate compiler bugs.

Each mutant is a small textual change to src/obxc.obx. It is written to a
temporary copy, built with seed/obxc, and the suite is run against that
compiler with the stage comparisons excluded: those compare against the
bootstrap and would flag every mutant trivially, whereas the point is to show
that the real oracles (expected output, expected errors, objdump, the Python
fuzz model, GC poisoning, golden token dumps) notice the bug. The real source
is never modified.

    python3 tests/mutate.py              run every mutant (several minutes)
    python3 tests/mutate.py narrow       only mutants whose name contains "narrow"
    python3 tests/mutate.py --part 2/4   the second quarter, for parallel runs

When adding a feature, add a mutant or two that break it; one that survives
marks a gap in the tests."""
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
OBXC_SRC = os.path.join(ROOT, 'src', 'obxc.obx')
BOOTSTRAP = os.path.join(ROOT, 'seed', 'obxc')

MUTANTS = {
    # scanner
    "tab escape wrong": ("        't' { return '\\t' }", "        't' { return 'x' }"),
    "no semicolon after ]": ("T_RPAREN, T_RBRACK, T_RBRACE { return true }", "T_RPAREN, T_RBRACE { return true }"),
    "hex size check off by one": ("if significant > 16 {", "if significant > 17 {"),
    "decimal overflow unchecked": ("big = true", "big = false"),
    "column not reset": ("        line += 1\n        col = 1", "        line += 1"),
    # encoder
    "disp8 range too wide": ("return v >= -128 && v < 128 }", "return v >= -128 && v <= 128 }"),
    "rbp base needs no disp": ("if m.disp == 0 && (m.base & 7) != 5 {", "if m.disp == 0 {"),
    "byte REX missing": ("rex(w, reg, 0, rm, byteReg && (rm >= 4 && rm <= 7 || opc == 0x88 && reg >= 4 && reg <= 7))",
                         "rex(w, reg, 0, rm, false)"),
    "movabs threshold": ("if v >= 0 && v < 0x100000000 {", "if v >= 0 && v < 0x80000000 {"),
    # types and narrowing
    "widening ignored": ("            e.alive = false\n", ""),
    "narrowing ignores loops": ("            if e.depth != loopdepth {", "            if false {"),
    "&& narrowing leaks": ("        let y = loadCond(relation())\n        truncate(narrowStack, mark)",
                           "        let y = loadCond(relation())"),
    "early-exit narrowing inverted": ("    fixHere(flinks)\n    if t1 { pushNarrowings(x.nf) }",
                                      "    fixHere(flinks)\n    if t1 { pushNarrowings(x.nt) }"),
    "match exhaustiveness off": ("if len(missing) > 0 && !hasElse {", "if false && !hasElse {"),
    "required fields unchecked": ('if len(missing) > 0 { error(name + " needs a value for "',
                                  'if false { error(name + " needs a value for "'),
    "non-null arrays allowed with a length": ("if nonNull(b) && !(n.mode == \"const\" && n.val == 0) {",
                                              "if false {"),
    "int match: duplicates allowed": ('if containsInt(seen, v.val) { error("value "', 'if false { error("value "'),
    "int match: comma arms jump wrongly": ("            append(toBody, jcc(CC_E))", "            append(toBody, jcc(CC_NE))"),
    "int match: terminates without else": ("    return allTerm && hasElse\n", "    return allTerm\n"),
    "int match: large values truncated": ("            if fits32(v.val) {\n                aluMI(7, at, v.val)", "            if true {\n                aluMI(7, at, v.val)"),
    "exit doesn't end control flow": ("        noreturn = true\n", ""),
    # code generation
    "bounds check skipped": ("    emit(0x72)\n    emit(14)", "    emit(0xEB)\n    emit(14)"),
    "! forgets to swap jump lists": ("        y.T = c.F\n        y.F = c.T", "        y.T = c.T\n        y.F = c.F"),
    "|| short-circuit broken": ("        append(x.T, jcc(x.cc))", "        jcc(x.cc)"),
    "comparison swap wrong": ("        CC_L { return CC_G }", "        CC_L { return CC_L }"),
    "keyword numbering off by one": ("if keywords[i] == w { return T_MODULE + i }", "if keywords[i] == w { return T_MODULE + i + 1 }"),
    "punctuation mapped wrongly": ("        '^' { return T_CARET }", "        '^' { return T_BAR }"),
    "remainder uses quotient": ("            movRR(x.r, RDX)", "            movRR(x.r, RAX)"),
    "shift directions swapped": ('if o == T_SHL { ext = 4 }', 'if o == T_SHR { ext = 4 }'),
    "imm32 check too loose": ("return v >= -2147483648 && v < 2147483648 }",
                              "return v >= -4294967296 && v < 4294967296 }"),
    "for-range limit off by one": ("        exitLink = jcc(CC_GE)\n        loopdepth += 1\n        loop = loopBody()\n"
                                   "        loopdepth -= 1\n        fixHere(loop.cont)\n        aluMI(0, mem(RBP, -1, v.offset, 8), 1)",
                                   "        exitLink = jcc(CC_G)\n        loopdepth += 1\n        loop = loopBody()\n"
                                   "        loopdepth -= 1\n        fixHere(loop.cont)\n        aluMI(0, mem(RBP, -1, v.offset, 8), 1)"),
    "defers skipped on break": ("        runDefers(loop.blocks)\n", ""),
    "string() of a byte allowed": ('        if a.typ.kind == "byte" {\n            error("string() of a byte', '        if false {\n            error("string() of a byte'),
    "string() of an int copies bytes": ('        if a.typ.kind == "int" {\n            if a.mode == "const"', '        if false {\n            if a.mode == "const"'),
    "byte() doesn't mask": ("if t == tByte && x.typ.kind != \"byte\" { aluRI(4, x.r, 255) }", ""),
    "byte stores write 8 bytes": ("    if dst.width == 1 {\n        if src.mode == \"const\" {",
                                  "    if false {\n        if src.mode == \"const\" {"),
    "string null check removed": ("                emit(0x75)\n                emit(14)", "                emit(0xEB)\n                emit(14)"),
    "array element located too early": ("                if elem.kind == \"byte\" { x.width = 1 }\n",
                                        "                if elem.kind == \"byte\" { x.width = 1 }\n                resolve(x)\n"),
    "eprint stays on stderr": ("        if toStderr { rtCall1(RT_SET_OUTPUT, makeConst(tInt, 1), tVoid) }\n", ""),
    "writeFile ignores the executable flag": ("        append(args3, e)", "        append(args3, makeConst(tBool, 0))"),
    # calls, stack and the collector contract
    "live regs not saved at runtime calls": ("    let saved = inUse()\n    for r in saved { pushR(r) }\n    let abi",
                                             "    let saved = new []int(0)\n    let abi"),
    "stack misaligned at runtime calls": ("    let pad = depth % 16", "    let pad = 0"),
    "defer body depth not +8": ("    depth = depth + 8\n", ""),
    "frame size not rounded": ("    let size = (frame + 15) & ~15", "    let size = frame"),
    "descriptors list no pointers": ("        if isRef(f.typ) { append(ptrs, f.offset) }", ""),
    "pointer arrays scanned as scalars": ("        } else if isRef(b) {\n            kind = 2",
                                          "        } else if isRef(b) {\n            kind = 1"),
    "globals range left empty": ("    let gend = GLOBAL_BASE + gsize", "    let gend = GLOBAL_BASE"),
}


def main():
    args = sys.argv[1:]
    part = None
    if '--part' in args:
        i = args.index('--part')
        part = tuple(map(int, args[i + 1].split('/')))
        del args[i:i + 2]
    pattern = args[0] if args else ''
    names = [n for n in MUTANTS if pattern in n]
    if part:
        k, n = part
        names = names[(k - 1) * len(names) // n:k * len(names) // n]
    orig = open(OBXC_SRC).read()
    work = tempfile.mkdtemp(prefix='obx-mutant-')
    missed = []
    for name in names:
        old, new = MUTANTS[name]
        if orig.count(old) != 1:
            print(f"STALE   {name}: the pattern must occur exactly once in obxc.obx (found {orig.count(old)})")
            missed.append(name)
            continue
        src = os.path.join(work, 'obxc.obx')
        exe = os.path.join(work, 'obxc')
        with open(src, 'w') as f:
            f.write(orig.replace(old, new))
        b = subprocess.run([BOOTSTRAP, src, '-o', exe, '--rt', ROOT], capture_output=True, text=True)
        if b.returncode != 0:
            print(f"BROKEN  {name}: the mutant does not compile: {b.stderr.strip()[:150]}")
            missed.append(name)
            continue
        try:
            p = subprocess.run([sys.executable, os.path.join(HERE, 'run.py'), '-x', 'stage/'],
                               capture_output=True, text=True, timeout=900, env=dict(os.environ, OBXC=exe))
            lines = p.stdout.strip().splitlines()
            caught = p.returncode != 0
            summary = lines[-1].split(' in ')[0] if lines else 'no output'
            first = next((l[5:].split()[0] for l in lines if l.startswith('FAIL ')), '')
        except subprocess.TimeoutExpired:
            caught, summary, first = True, 'suite timed out', ''
        print(f"{'caught' if caught else 'MISSED'}  {name:<38} {summary:<24} {first}", flush=True)
        if not caught:
            missed.append(name)
    print(f"\n{len(names) - len(missed)}/{len(names)} mutants caught")
    sys.exit(1 if missed else 0)


if __name__ == '__main__':
    main()
