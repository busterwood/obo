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
    # a call to the function itself frees nothing in its own body: references held across it go unseen
    "recursive call skips invalidation": [('        if (g.kind == "var" || g.kind == "let") && (all || mightFree(f, g)) {',
                                           '        if (g.kind == "var" || g.kind == "let") && f != cur && (all || mightFree(f, g)) {')],
    # a callee's frees not inherited: its callers' summaries are incomplete
    "callee frees not recorded": [("            if !all { recordFree(g) }\n", "")],
    # a self-call is a change in its body, so it must not be taken for a call that frees nothing
    "recursive call might free": [("    if f.addr < 0 || f == curFn || f.freesAll { return true }",
                                   "    if f.addr < 0 || f.freesAll { return true }")],
    "recursive call is pure": [("    if f.addr < 0 || f == curFn || f.freesAll || len(f.freed) > 0 { return false }",
                                "    if f.addr < 0 || f.freesAll || len(f.freed) > 0 { return false }")],
    # a callee with a forward heading frees everything, and its callers must inherit that
    "forward heading not inherited": [("    if cur != nil && all && f != cur { cur.freesAll = true }",
                                       "    if cur != nil && f.addr < 0 && f != cur { cur.freesAll = true }")],
    # freeing symbols too early: a dangling-handle trap
    "frees one symbol too many": [(FREE, FREE.replace("symMark)", "symMark - 1)"))],
    "frees symbols before the blocks close": [(BLOCKS_CLOSE, "    truncate(symArena, symMark)\n" + BLOCKS_CLOSE)],
    # keeping symbols alive: over the memory budget
    "symbols never freed": [(FREE, "")],
    "parameters kept": [(MARK, ""), (PARAMS_DONE, PARAMS_DONE + "    let symMark = len(symArena)\n")],
    # borrows through a handle (checker): each must be caught by a case in errors/handles.obx
    # or errors/handle_calls.obx
    # a call that might free does not invalidate borrows through a handle
    "handle borrows survive calls": [("    if !pureFn(f) { invalidate(handleSym, nil, false, \"\") }   // and so may a borrow through a handle\n", "")],
    # a change under another root does not reach a borrow through a handle
    "handle borrows survive other changes": [("var hit = r == handleSym || r == root && (all || v.refDeep && underPath(v.refPath, path))",
                                              "var hit = (r == handleSym && root == r) || r == root && (all || v.refDeep && underPath(v.refPath, path))")],
    # a borrow through a handle may be passed to a call that might free it
    "handle borrows passable to freeing calls": [("        if argRoots[i] == handleSym && !pureFn(f) {", "        if false {")],
    # a callee that writes through a handle is taken for pure, so its callers keep their borrows
    "handle writes not recorded": [("    if g == handleSym {                  // a change through a handle may free anything",
                                    "    if false {                           // a change through a handle may free anything")],
    # a change through a handle temporary (handle(p).items, no root) invalidates nothing: caught by errors/handles.obx
    "handle temporaries invalidate nothing": [("    if x.viaHandle { invalidate(handleSym, viaOf(x), false, \"\") }",
                                               "    if x.viaHandle && rootOf(x) != nil { invalidate(handleSym, viaOf(x), false, \"\") }")],
    # an assignment through a handle temporary records no free, so its function looks pure: caught by errors/handles.obx (zap)
    "handle temporary assignments record nothing": [("                } else if x.deref || x.viaHandle {\n                    invalidateChange(x)\n                    recordFree(freeRoot(x))\n",
                                                     "                } else if x.deref || x.viaHandle {\n                    invalidateChange(x)\n                    if x.deref { recordFree(freeRoot(x)) }\n")],
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
