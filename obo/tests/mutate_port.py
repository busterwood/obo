#!/usr/bin/env python3
"""Mutation testing for the port (obo/src/obo.obo): plants a bug in a copy,
runs the Stage B comparison against it (port_compare.py, with SRC pointed at the
copy), and checks that the comparison fails. Divergent output must show up as a
mismatch, freeing too early as a dangling-handle trap, and keeping memory alive
as a breach of the memory budget.

    python3 obo/tests/mutate_port.py [pattern]"""
import os
import re
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
    # keeping symbols alive: over the memory budget. "parameters kept" is too small for the budget
    # now that the peak is lower, so it is checked against the unmutated peak instead (KEEPS_ALIVE)
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
    "handle temporary assignments record nothing": [("                } else if x.deref || x.viaHandle {\n                    invalidateOwnedChange(x)\n                    recordFree(freeRoot(x))\n",
                                                     "                } else if x.deref || x.viaHandle {\n                    invalidateOwnedChange(x)\n                    if x.deref { recordFree(freeRoot(x)) }\n")],
    # references inside something (the deep rule): caught by errors/handle_deep.obx or run/handle_deep.obx
    # a change through a handle leaves references inside something valid
    "body write doesn't invalidate deep refs": [("    if freeRoot(x) == handleSym { invalidateDeep(viaOf(x)) }\n", "    if false { invalidateDeep(viaOf(x)) }\n")],
    # a change through a reference borrowed through a handle leaves references inside something valid
    "change through a borrowed handle reference doesn't invalidate deep refs": [("    if freeRoot(x) == handleSym { invalidateDeep(viaOf(x)) }",
                                                                              "    if x.viaHandle { invalidateDeep(viaOf(x)) }")],
    # a call to a function that writes through a handle leaves references inside something valid
    "callee writes keep deep refs": [("        invalidateDeep(nil)\n", "")],
    # a function that calls a writer is not itself marked as one: its callers keep their references
    "flag not inherited from callees": [("        if cur != nil { cur.writesHandles = true }\n", "")],
    # a forward-declared callee is not assumed to write: its callers keep their references
    "forward heading not assumed to write": [("    } else if f.addr < 0 || f.writesHandles {\n", "    } else if f.writesHandles {\n")],
    # a reference inside something passed to a forward-declared callee is not refused
    "forward heading deep argument allowed": [("        } else if f.addr < 0 {\n            errorAt(", "        } else if false {\n            errorAt(")],
    # a reference inside something passed to a writer is not refused
    "call-site deep-argument check dropped": [("    if deepLine > 0 {\n        if f == curFn {", "    if false {\n        if f == curFn {")],
    # a recursive call that writes through a handle, found at the end of the body, is not reported
    "deferred self-call check dropped": [("    if pendLine > 0 && f.writesHandles { errorAt(pendMsg, pendLine, pendCol) }",
                                          "    if false { errorAt(pendMsg, pendLine, pendCol) }")],
    # a recursive call leaves its references' suspicion unmarked
    "self-call suspects not marked": [("        if v.isRefLocal && v.refDeep && !v.moved { v.suspect = true }",
                                       "        if false { v.suspect = true }")],
    # a use of a suspect reference is not noted
    "suspect uses not noted": [("    if s.suspect && (!target || path != \"\") {", "    if false {")],
    # pointing a suspect reference elsewhere does not end its suspicion
    "re-pointing keeps suspicion": [("            xs.suspect = false\n", "")],
    # a loop with a recursive call is not checked for references used before the call on the next iteration
    "loop self-call check dropped": [("    if selfCalls == selfBefore { return }", "    if true { return }")],
    # a shallow reference (a whole object) is invalidated by a change through a handle
    "shallow references invalidated by handle writes": [("        if v.isRefLocal && v.refDeep && !ancestorOf(v, via) && !v.moved {",
                                                         "        if v.isRefLocal && !ancestorOf(v, via) && !v.moved {")],
    # a truncation of a scalar array through a handle (frees nothing) invalidates references inside something
    "scalar truncate invalidates deep references": [("                if owned { invalidateOwnedChange(a) } else { invalidateChange(a) }",
                                                     "                invalidateOwnedChange(a)")],
    # a truncation of a scalar array through a handle marks the function as a writer
    "scalar truncate through a handle is a write": [("                if owned || freeRoot(a) != handleSym { recordFree(freeRoot(a)) }   // a scalar array frees nothing",
                                                     "                recordFree(freeRoot(a))")],
    # per-path suspicion (the deep rule): a join of paths keeps the suspicion of each path. Caught by
    # errors/handle_deep.obx "a recursive call on one branch of an if" and "a recursive call in one match arm"
    "suspect not joined at if or match": [("            sameStates(s1, s2, iline, icol)\n            joinSuspect(s1)\n",
                                           "            sameStates(s1, s2, iline, icol)\n"),
                                          ("        sameStates(s1, s0, iline, icol)\n        joinSuspect(s1)\n",
                                           "        sameStates(s1, s0, iline, icol)\n"),
                                          ("        if a[n + i] { out[n + i] = true }", "        if false { out[n + i] = true }")],
    # a while condition's recursive calls are counted by the loop check: caught by errors/handle_deep.obx
    # "a recursive call in a while condition"
    "loop self-call counter taken after the condition": [("    let selfBefore = selfCalls\n    let useBefore = useSeq\n    let entry = snapshot()",
                                                          "    let useBefore = useSeq\n    let entry = snapshot()"),
                                                         ("    let condSt = snapshot()                  // the state when the condition leaves the loop",
                                                          "    let selfBefore = selfCalls\n    let condSt = snapshot()                  // the state when the condition leaves the loop")],
    # a break's suspicion is carried out of the loop: caught by errors/handle_deep.obx "a recursive call before a break"
    "break suspicion not recorded": [("                    lp.susp[i] = true\n", "                    if false { lp.susp[i] = true }\n")],
    # the loop-carried suspicion (fix for "a recursive call in a loop, read on the next iteration", the loop check):
    # the state at the body's end, which is where the next iteration starts, is not carried. Caught by
    # errors/handle_deep.obx "a loop's body reads a reference at its top, on the next iteration after a recursive call on one path"
    "loop-carried suspicion from the body's end dropped": [("            if ownedLocals[i].suspect { loops[li].back[i] = true }",
                                                           "            if false { loops[li].back[i] = true }")],
    # ... and the suspicion a continue carries to the next iteration. Caught by errors/handle_deep.obx
    # "a recursive call before a continue: the next iteration starts with the reference still deep"
    "loop-carried suspicion from continue dropped": [("                    if k == T_CONTINUE { lp.back[i] = true }", "                    if false { lp.back[i] = true }")],
    # a body that always leaves keeps its suspicion after the loop. Caught by run/handle_deep.obx "retLeak"
    "terminated loop body's suspicion kept": [("    if term { restore(entry) } else { restoreMoved(entry) }", "    restoreMoved(entry)")],
    # the loop check reports a reference the loop never reads: caught by run/handle_deep.obx "unusedRef"
    "loop check reports unread references": [("        if back[i] && ownedLocals[i] != lv && ownedLocals[i].usedAt > useBefore {",
                                              "        if back[i] && ownedLocals[i] != lv {")],
    # reads in a loop are not counted: the loop check misses the reads it is for. Caught by errors/handle_deep.obx
    # "a loop's body reads a reference at its top, on the next iteration after a recursive call on one path"
    "reads in a loop not counted": [("        useSeq += 1\n        s.usedAt = useSeq\n", "")],
    # a deferred recursive call is applied at the defer statement, not where the block ends. Caught by
    # run/handle_deep.obx "deferRec" (a use after the defer statement)
    "deferred self-call suspicion kept at the defer": [("    restoreSuspects(before)\n    if selfCalls != sc {", "    if selfCalls == sc { restoreSuspects(before) }\n    if selfCalls != sc {")],
    # a defer body's re-point clears suspicion before the defer has run. Caught by errors/handle_deep.obx
    # "a defer that re-points a reference"
    "a defer's re-point clears suspicion": [("    restoreSuspects(before)\n    if selfCalls != sc {", "    if selfCalls != sc { restoreSuspects(before) }\n    if selfCalls != sc {")],
    # a for walk over a reference is not a read on each iteration. Caught by errors/handle_deep.obx
    # "a walk over a reference with a recursive call in its body"
    "walk source not counted as a read": [("        if walked.isRefLocal {                  // the walk reads its array on every iteration\n            useSeq += 1\n", "        if false {\n            useSeq += 1\n")],
    # a deferred recursive call is not applied where the block ends at all. Caught by errors/handle_deep.obx
    # "a deferred recursive call runs where the block ends: a reference used after the block is suspect"
    "deferred self-call not applied at block close": [("    if blocks[bi].selfDefer { markSuspects() }", "    if false { markSuspects() }")],
}


# the reference bindings per path (obo/tools/port_ref_binds.py): the mutants whose text the rewrite
# replaced are retargeted to the same behaviour, and the join and the loop check have their own
MUTANTS.update({
    "handle borrows survive other changes": [("if r == handleSym || r == root && (all || b.deeps[i] && underPath(b.paths[i], path)) { hit = true }",
                                              "if (r == handleSym && root == r) || r == root && (all || b.deeps[i] && underPath(b.paths[i], path)) { hit = true }")],
    "handle borrows passable to freeing calls": [("            if b.roots[p] == handleSym && !pureFn(f) {", "            if false {")],
    "handle temporaries invalidate nothing": [("if x.viaHandle { invalidate(handleSym, via, false, \"\") }",
                                               "if x.viaHandle && r != nil { invalidate(handleSym, via, false, \"\") }")],
    "handle temporary assignments record nothing": [("                } else if x.deref || x.viaHandle {\n                    invalidateOwnedChange(x)\n                    recordFreeChange(x)\n",
                                                     "                } else if x.deref || x.viaHandle {\n                    invalidateOwnedChange(x)\n                    if x.deref { recordFreeChange(x) }\n")],
    "body write doesn't invalidate deep refs": [("    if throughHandle(x) { invalidateDeep(viaOf(x)) }\n", "    if false { invalidateDeep(viaOf(x)) }\n")],
    "change through a borrowed handle reference doesn't invalidate deep refs": [("    if throughHandle(x) { invalidateDeep(viaOf(x)) }",
                                                                              "    if x.viaHandle { invalidateDeep(viaOf(x)) }")],
    "self-call suspects not marked": [("if v.isRefLocal && anyDeep(v.bind) && !v.moved { v.suspect = true }", "if false { v.suspect = true }")],
    "shallow references invalidated by handle writes": [("if v.isRefLocal && anyDeep(v.bind) && !ancestorOf(v, via) && !v.moved {",
                                                         "if v.isRefLocal && !ancestorOf(v, via) && !v.moved {")],
    "scalar truncate through a handle is a write": [("if owned || !throughHandle(a) { recordFreeChange(a) }", "recordFreeChange(a)")],
    "suspect not joined at if or match": [("            sameStates(s1, s2, iline, icol)\n            joinSuspect(s1)\n",
                                           "            sameStates(s1, s2, iline, icol)\n"),
                                          ("        sameStates(s1, s0, iline, icol)\n        joinSuspect(s1)\n",
                                           "        sameStates(s1, s0, iline, icol)\n"),
                                          ("if a.flags[n + i] { out.flags[n + i] = true }", "if false { out.flags[n + i] = true }")],
    "loop self-call counter taken after the condition": [("    let selfBefore = selfCalls\n    let useBefore = useSeq\n    clearFresh()\n    let entry = snapshot()",
                                                          "    let useBefore = useSeq\n    clearFresh()\n    let entry = snapshot()"),
                                                         ("    let condSt = snapshot()                  // the state when the condition leaves the loop",
                                                          "    let selfBefore = selfCalls\n    let condSt = snapshot()                  // the state when the condition leaves the loop")],
    "break suspicion not recorded": [("            absorb(&lp.susp, &now)\n", "            if false { absorb(&lp.susp, &now) }\n")],
    "loop-carried suspicion from the body's end dropped": [("        absorb(&loops[li].back, &now)\n", "        if false { absorb(&loops[li].back, &now) }\n")],
    "loop-carried suspicion from continue dropped": [("if k == T_CONTINUE { absorb(&lp.back, &now) }", "if false { absorb(&lp.back, &now) }")],
    "terminated loop body's suspicion kept": [("    if term { restore(&entry) } else { restoreMoved(&entry) }", "    restoreMoved(&entry)")],
    "loop check reports unread references": [("back.flags[localsIn(back) + i] && ownedLocals[i] != lv && ownedLocals[i].usedAt > useBefore {",
                                              "back.flags[localsIn(back) + i] && ownedLocals[i] != lv {")],
    "deferred self-call suspicion kept at the defer": [("    restoreDeferred(&before)\n    if selfCalls != sc {", "    if selfCalls == sc { restoreDeferred(&before) }\n    if selfCalls != sc {")],
    "a defer's re-point clears suspicion": [("    restoreDeferred(&before)\n    if selfCalls != sc {", "    if selfCalls != sc { restoreDeferred(&before) }\n    if selfCalls != sc {")],
    # the join of two bindings keeps the binding of the last path, not the union of the paths
    "binding not joined: last path wins": [("    if a == b || b == noneBind { return a }\n    if a == noneBind { return b }\n",
                                            "    if b == noneBind { return a }\n    if true { return b }\n")],
    # a join keeps only the roots of the first path: a change under a root of the second is missed
    "join keeps only the first root": [("        if !found {\n            append(roots, b.roots[j])", "        if false {\n            append(roots, b.roots[j])")],
    # a join is deep only where both paths are deep: a change under a deep path is missed
    "deep not ORed at a join": [("                let d = deeps[i] || b.deeps[j]", "                let d = deeps[i] && b.deeps[j]")],
    # the spec's rule: the common prefix, which for a prefix pair is the shorter path; the longer one is hit
    # by every change that hits the shorter one and by changes below it
    "path join keeps the shorter path (the spec's rule)": [("    if segsPrefix(pa, pb) && !segsPrefix(pb, pa) { return pb }\n    if segsPrefix(pb, pa) && !segsPrefix(pa, pb) { return pa }",
                                                            "    if segsPrefix(pa, pb) && !segsPrefix(pb, pa) { return pa }\n    if segsPrefix(pb, pa) && !segsPrefix(pa, pb) { return pb }")],
    # writable only where both paths are: a reference writable on one path only is read-only after the join
    "refMut not ANDed at a join": [("    let writable = a.writable && b.writable", "    let writable = a.writable || b.writable")],
    # the loop's bindings at its end are never checked against the ones its body was checked with
    "loop back-edge binding check dropped": [("    loopBindCheck(&entry, &loops[li].back, useBefore, freeCount > frees, lv, line, col)\n", "")],
})


# mutants that keep memory alive: caught when the peak rises above the unmutated peak,
# for a leak too small to cross PEAK_BUDGET
KEEPS_ALIVE = {"parameters kept"}


def peak_of(output):
    m = re.search(r"peaks at ([\d,]+) bytes", output)
    return int(m.group(1).replace(',', '')) if m else None


def main():
    pattern = sys.argv[1] if len(sys.argv) > 1 else ''
    src = open(SRC).read()
    tmp = tempfile.mkdtemp()
    caught = total = 0
    base = subprocess.run([sys.executable, '-c', "import port_compare as pc; pc.main()"],
                          capture_output=True, text=True, cwd=HERE)
    base_peak = peak_of(base.stdout + base.stderr)
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
        total += 1
        if name in KEEPS_ALIVE and r.returncode == 0:
            peak = peak_of(r.stdout + r.stderr)
            if peak is not None and base_peak is not None and peak > base_peak:
                r = subprocess.CompletedProcess(r.args, 1, r.stdout, f"FAIL peak {peak:,} is above the unmutated {base_peak:,}")
        lines = (r.stdout + r.stderr).strip().splitlines()
        why = next((l for l in lines if l.startswith('FAIL') or 'does not compile' in l), lines[-1] if lines else '')
        if r.returncode != 0:
            caught += 1
            print(f"caught  {name:40s} {why[:110]}")
        else:
            print(f"MISSED  {name:40s} {lines[-1] if lines else ''}")
    print(f"\n{caught}/{total} mutants caught")
    sys.exit(0 if caught == total else 1)


if __name__ == '__main__':
    main()
