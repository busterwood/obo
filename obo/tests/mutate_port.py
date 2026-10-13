#!/usr/bin/env python3
"""Mutation testing for the port (obo/src/obo.obo): plants a bug in a copy,
runs the Stage B comparison against it (port_compare.py, with SRC pointed at the
copy), and checks that the comparison fails. Divergent output must show up as a
mismatch, freeing too early as a dangling-handle trap, and keeping memory alive
as a breach of the memory budget.

    python3 obo/tests/mutate_port.py [pattern]"""
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(os.path.dirname(HERE), 'src', 'obo.obo')

MARK = "    let symMark = len(symArena)                 // the function's own symbols start here\n"
FREE = "    truncate(symArena, symMark)                 // and nothing refers to them any more\n"
PARAMS_DONE = "        if params[i].typ.kind == \"string\" { addStrLocal(s) }\n    }\n"
BLOCKS_CLOSE = "    closeBlock(term)\n    closeBlock(term)\n    if !term { epilogue() }\n"

# each mutant is a list of (old, new) replacements, each of which must match exactly once
MUTANTS = {
    # divergence from obo: a mismatch
    "error message changed": [("cannot move out of a reference: a reference only borrows",
                               "cannot move out of a reference")],
    "condition test compares with 1": [("        aluMI(7, memOf(x), 0)\n        drop(x)",
                                        "        aluMI(7, memOf(x), 1)\n        drop(x)")],
    # freeing symbols too early: a dangling-handle trap
    "frees one symbol too many": [(FREE, FREE.replace("symMark)", "symMark - 1)"))],
    "frees symbols before the blocks close": [(BLOCKS_CLOSE, "    truncate(symArena, symMark)\n" + BLOCKS_CLOSE)],
    # keeping symbols alive: over the memory budget
    "symbols never freed": [(FREE, "")],
    "parameters kept": [(MARK, ""), (PARAMS_DONE, PARAMS_DONE + "    let symMark = len(symArena)\n")],
}


def main():
    pattern = sys.argv[1] if len(sys.argv) > 1 else ''
    src = open(SRC).read()
    tmp = tempfile.mkdtemp()
    caught = total = 0
    for name, edits in MUTANTS.items():
        if pattern not in name:
            continue
        text = src
        for old, new in edits:
            assert text.count(old) == 1, f"mutant '{name}': expected one match for {old!r}"
            text = text.replace(old, new)
        path = os.path.join(tmp, 'mutant.obx')
        open(path, 'w').write(text)
        code = f"import port_compare as pc; pc.SRC = {path!r}; pc.main()"
        r = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True, cwd=HERE)
        lines = (r.stdout + r.stderr).strip().splitlines()
        why = next((l for l in lines if l.startswith('FAIL') or 'does not compile' in l), lines[-1] if lines else '')
        total += 1
        if r.returncode != 0:
            caught += 1
            print(f"caught  {name:40s} {why[:110]}")
        else:
            print(f"MISSED  {name:40s} {lines[-1] if lines else ''}")
    print(f"\n{caught}/{total} mutants caught")
    sys.exit(0 if caught == total else 1)


if __name__ == '__main__':
    main()
