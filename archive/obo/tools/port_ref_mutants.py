"""Edits by script: the mutants of the reference bindings per path, added to tests/mutate_port.py.
Stale mutants (whose text the rewrite of the reference code replaced) are retargeted to the same
behaviour in the new code; six new ones check the per-path join and the loop check. Each anchor
must match exactly once."""
import os

HERE = os.path.dirname(os.path.abspath(__file__))
P = os.path.join(os.path.dirname(HERE), "tests", "mutate_port.py")
t = open(P).read()

BLOCK = r'''# the reference bindings per path (obo/tools/port_ref_binds.py): the mutants whose text the rewrite
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


'''

anchor = "# mutants that keep memory alive: caught when the peak rises above the unmutated peak,\n"
assert t.count(anchor) == 1
t = t.replace(anchor, BLOCK + anchor)
open(P, "w").write(t)
print("ok")
