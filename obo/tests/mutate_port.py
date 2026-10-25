#!/usr/bin/env python3
"""Mutation testing for the port (obo/src/obo.obo): plants a bug in a copy,
runs the Stage B comparison against it (port_compare.py, with SRC pointed at the
copy), and checks that the comparison fails. Divergent output must show up as a
mismatch, freeing too early as a dangling-handle trap, and keeping memory alive
as a breach of the memory budget. A mutant of the encoder (ENCODER) runs
port_test.py instead, whose objdump cross-check must catch it.

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
PARAMS_DONE = "        }\n    }\n    expect(T_LBRACE, \"\")\n    openBlock()\n"
BLOCKS_CLOSE = "    closeBlock(term)\n    closeBlock(term)\n    homeBits = 0\n    if !term { epilogue() }\n"

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
    "callee writes keep deep refs": [("        invalidateDeep(nil)\n    }\n    if !pureFn(f)", "    }\n    if !pureFn(f)")],
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
    "a defer's re-point clears suspicion": [("    restoreDeferred(&before)\n", "")],
    # a for walk over a reference is not a read on each iteration. Caught by errors/handle_deep.obx
    # "a walk over a reference with a recursive call in its body"
    "walk source not counted as a read": [("        if walked.isRefLocal {                  // the walk reads its array on every iteration\n            useSeq += 1\n", "        if false {\n            useSeq += 1\n")],
    # a deferred recursive call is not applied where the block ends at all. Caught by errors/handle_deep.obx
    # "a deferred recursive call runs where the block ends: a reference used after the block is suspect"
    # assignment targets across calls (tests/errors/call_targets.obx). Each one accepts programs the
    # compiler must refuse; the port's own output does not change, but port_compare also runs the
    # error cases, so the mutant is caught there
    "field targets assigned across calls": [("    if xs == nil {\n        // a field or element: the call could free the record or array it is in",
                                             "    if false {\n        // a field or element: the call could free the record or array it is in")],
    "handle assignment across calls allowed": [("    if x.viaHandle {\n        errorAt(\"cannot assign through a handle across a call",
                                                "    if false {\n        errorAt(\"cannot assign through a handle across a call")],
    "compound targets not checked": [("            var y = expression()\n            pinned = savedPin\n            checkTargetAcrossCall(x, xs, r0, callsBefore, line, col)\n",
                                      "            var y = expression()\n            pinned = savedPin\n")],
    "append targets not checked": [("        if name != \"substr\" && held && userCalls != callsBefore {",
                                    "        if name == \"truncate\" && held && userCalls != callsBefore {")],
    "truncate targets not checked": [("        if name != \"substr\" && held && userCalls != callsBefore {",
                                      "        if name == \"append\" && held && userCalls != callsBefore {")],
    "replace field targets not checked": [("        if (x.viaHandle || x.sym == nil) && userCalls != callsBefore {",
                                           "        if x.viaHandle && userCalls != callsBefore {")],
    "index targets not checked": [("            if !wholeLocal && userCalls != callsBefore {", "            if false {")],
    "held arrays ignore mut parameters": [("    return x.sym == nil || r.storage == \"global\" || r.isRef\n", "    return x.sym == nil || r.storage == \"global\"\n")],
    "indexes of mut parameters unchecked": [("            let wholeLocal = x.sym != nil && !(root != nil && (root.storage == \"global\" || root.isRef))",
                                             "            let wholeLocal = x.sym != nil && !(root != nil && root.storage == \"global\")")],
    "index of a field, element or whole local not checked for moves": [("            if root != nil && heldLost(root, touch0) {",
                                                                        "            if root != nil && false {")],
    # cheap code generation (FORK.md, Phase 1). A misaligned stack at a runtime call is trapped by the
    # runtime's entry stubs (exit 4), so a wrong depth shows up as a mismatch
    "runtime call pad dropped when misaligned": [("    let pad = depth % 16\n", "    let pad = 0\n")],
    "obo call pad dropped when misaligned": [("    let pad = (depth + 8 * n) % 16\n", "    let pad = 0\n")],
    "push imm not counted in the depth": [("            pushI(a.val)                           // no register needed\n            depth += 8\n",
                                           "            pushI(a.val)                           // no register needed\n")],
    # an imm8 push for a value that needs imm32: caught by the objdump cross-check
    "push imm8 for any imm32": [("    if fits8(v) {\n        emit(0x6A)", "    if fits32(v) {\n        emit(0x6A)")],
    "constant runtime argument lost": [("                append(argConsts, a.val)\n", "                append(argConsts, 0)\n")],
    # a function without locals loses its 'sub rsp,0' and its body moves back 7 bytes
    "dropped frame: calls out not moved": [("    for p in calls { putRel(p - 7, getRel(p - 7) + 7) }\n", "")],
    "dropped frame: calls waiting for later functions not moved": [("                if g.fixups[fi] > at { g.fixups[fi] -= 7 }\n", "")],
    "dropped frame: calls to known functions not recorded": [("        fix(at, f.addr)\n        append(farCalls, at)\n", "        fix(at, f.addr)\n")],
    # a pending forward call of an earlier function shifted when a later zero-frame function drops its frame:
    # caught by tests/run/code_shapes.obx
    "dropped frame: every waiting call shifted": [("                if g.fixups[fi] > at { g.fixups[fi] -= 7 }\n", "                g.fixups[fi] -= 7\n")],
    "dropped frame: waiting calls shifted if not negative": [("                if g.fixups[fi] > at { g.fixups[fi] -= 7 }\n", "                if g.fixups[fi] >= 0 { g.fixups[fi] -= 7 }\n")],
    # the boundaries of push imm8 and imm32, caught by the same program (Stage B, not only the objdump group)
    "push imm8 up to 128": [("fn pushI(v: int) {\n    if fits8(v) {", "fn pushI(v: int) {\n    if v >= -128 && v <= 128 {")],
    "push imm8 from -129": [("fn pushI(v: int) {\n    if fits8(v) {", "fn pushI(v: int) {\n    if v >= -129 && v < 128 {")],
    "push imm32 masked": [("        emit(0x68)\n        emit32(v)\n", "        emit(0x68)\n        emit32(v & 0x7FFFFFFF)\n")],
    "frame dropped although there are locals": [("    if size == 0 {\n        dropFrame(frameAt)", "    if size <= 16 {\n        dropFrame(frameAt)")],
    # loop inversion: a while loop's condition moves after its body, and each kind of position it holds must move
    # with it (tests/run/loop_inversion.obx)
    "inverted loop: calls out of the condition not adjusted": [("        putRel(farCalls[k], getRel(farCalls[k]) - d)\n", "")],
    "inverted loop: calls out of the condition not recorded at the new place": [("        farCalls[k] += d\n", "")],
    "inverted loop: waiting calls in the condition not adjusted": [("append(globals[waitFn[k]].fixups, waitAt[k] + d)", "append(globals[waitFn[k]].fixups, waitAt[k])")],
    "inverted loop: a nested loop lifts its outer loop's waiting calls": [("    let waitFrom = len(waitFn)\n", "    let waitFrom = 0\n")],
    "inverted loop: waiting calls in the condition not lifted": [("        if fwdCalls != fwdFrom {", "        if false {")],
    "inverted loop: links of the true and false lists not adjusted": [("        putRel(at, nxt)\n        at = nxt\n", "        putRel(at, nxt - d)\n        at = nxt\n")],
    "inverted loop: head of the true list not adjusted": [("    let tl = shiftWaits(x.T, d)\n", "    shiftWaits(x.T, d)\n    let tl = x.T\n")],
    "inverted loop: head of the false list not adjusted": [("    let fl = shiftWaits(x.F, d)\n", "    shiftWaits(x.F, d)\n    let fl = x.F\n")],
    "inverted loop: continue goes to the body, not the condition": [("    fixTo(loop.cont, at)\n", "    fixTo(loop.cont, bodyAt)\n")],
    "inverted loop: continue goes to the end of the condition": [("    fixTo(loop.cont, at)\n", "    fixTo(loop.cont, at + len(lifted) - liftFrom)\n")],
    "inverted for loop: test jumps back to its own top": [("        fix(jcc(CC_L), top)\n    } else {", "        fix(jcc(CC_L), pc() - 6)\n    } else {")],
}

# mutants of the encoder: caught by port_test.py's objdump cross-check
ENCODER = {"push imm8 for any imm32"}


# the reference bindings per path (obo/tools/port_ref_binds.py): the mutants whose text the rewrite
# replaced are retargeted to the same behaviour, and the join and the loop check have their own
MUTANTS.update({
    "handle borrows survive other changes": [("if r == handleSym || r == root && (all || b.deeps[i] && underPath(b.paths[i], path)) { return true }",
                                              "if (r == handleSym && root == r) || r == root && (all || b.deeps[i] && underPath(b.paths[i], path)) { return true }")],
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
    "loop self-call counter taken after the condition": [("    let selfBefore = selfCalls\n    let useBefore = useSeq\n",
                                                          "    let useBefore = useSeq\n"),
                                                         ("    let condSt = snapshot()                  // the state when the condition leaves the loop",
                                                          "    let selfBefore = selfCalls\n    let condSt = snapshot()                  // the state when the condition leaves the loop")],
    "break suspicion not recorded": [("                append(lp.exits, now)\n", "                if false { append(lp.exits, now) }\n")],
    "loop-carried suspicion from the body's end dropped": [("        absorb(&loops[li].back, &now)\n", "        if false { absorb(&loops[li].back, &now) }\n")],
    "loop-carried suspicion from continue dropped": [("                append(lp.conts, now)\n", "                if false { append(lp.conts, now) }\n")],
    "terminated loop body's suspicion kept": [("    if term { restore(&entry) } else { restoreMoved(&entry) }", "    restoreMoved(&entry)")],
    "loop check reports unread references": [("back.flags[localsIn(back) + i] && ownedLocals[i] != lv && ownedLocals[i].usedAt > useBefore {",
                                              "back.flags[localsIn(back) + i] && ownedLocals[i] != lv {")],
    "deferred self-call suspicion kept at the defer": [("    restoreDeferred(&before)\n", "    if selfCalls == sc { restoreDeferred(&before) }\n")],
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
    "loop back-edge binding check dropped": [("    loopBindCheck(&entry, &back, useBefore, lv, line, col, hit)\n", "")],
})

# the uses of a defer body: the same mutants as the fork's (see mutate_fork.py); the error cases of
# errors/defer_uses.obx show them, since the port's own output does not change
from mutate_fork import DEFER_USE_MUTANTS  # noqa: E402
MUTANTS.update({k: [v] for k, v in DEFER_USE_MUTANTS.items()})


# register allocation (FORK.md, Phase 2). A home is r12 to r15, taken out of the free list while its local is in
# scope; the callee may use any pool register, so the caller pushes the homes in scope around an obo call, and
# the C runtime keeps them. Most are caught by tests/run/register_alloc.obx; the SHAPES ones change speed only
# and are caught by tests/homes_check.py
MUTANTS.update({
    "homes not pushed around obo calls": [("    let saved = usedRegs()\n    pushRegs(saved)\n    setFree(0)",
                                           "    let saved = usedRegs() & ~homeBits\n    pushRegs(saved)\n    setFree(0)")],
    "a home released as a temporary": [(" && !containsInt(free, r) && (homeBits & (1 << r)) == 0 { append(free, r) }",
                                        " && !containsInt(free, r) { append(free, r) }")],
    "runtime call clobbers r15": [("    callAbs(RT_TEXT + 8 * slot)\n    if pad != 0 {", "    callAbs(RT_TEXT + 8 * slot)\n    movRI(15, 0)\n    if pad != 0 {")],
    "array walk's element variable in a register": [('        let v = newLocal(name, baseOf(xt), "let")\n', '        let v = newLocal(name, baseOf(xt), "let")\n        homeFor(v, name, true)\n')],
    "one register for two live locals": [("        if (homeBits & (1 << r)) == 0 && containsInt(free, r) {", "        if true {"),
                                         ("    while free[j] != pick { j += 1 }", "    while j < len(free) && free[j] != pick { j += 1 }")],
    "parameter not loaded into its register": [("                opMem(0x8B, h, mem(RBP, -1, s.offset, 8), true, false)      // the argument, from the stack\n", "")],
    "uninitialised local not zeroed": [("                movRI(s.home, 0)\n", "")],
    "compound assignment changes a copy": [("            var r = x.r                       // a local in a register is changed where it is", "            var r = alloc()")],
    "multiplication by a constant writes the local": [("        let t = alloc()                           // imul reads one register and writes another", "        let t = x.r")],
    "a local's register used as the caller's copy": [("        if !x.home { return x }\n        let c = alloc()", "        if true { return x }\n        let c = alloc()")],
    "for limit in the variable's register": [("            homeFor(lim, name, false)              // the limit follows the variable into a register", "            lim.home = v.home")],
    "scan: nothing counted": [("                if k >= 0 {\n                    scanScore[k] += w", "                if k >= 0 {\n                    scanScore[k] += 0")],
    "scan: ranking ignored": [("    if nfree <= better { return -1 }", "")],
    "scope end does not give the register back": [("        if s.home >= 0 { releaseHome(s.home) }", "        if s.home < 0 { releaseHome(s.home) }")],
    "runtime calls push the locals' registers": [("    let saved = usedRegs() & ~homeBits      // the C runtime keeps r12 to r15, where the locals are", "    let saved = usedRegs()")],
    "return copies a local's register": [("        let x = asReg(x0)\n        for k in blocks[fnBlock].omark", "        let x = load(x0)\n        for k in blocks[fnBlock].omark")],
    "for limit's register never given back": [("            if lim.home >= 0 { append(blocks[len(blocks) - 1].syms, lim) }       // so that the block's end frees it\n", "")],
    "bool parameters left in memory": [('        if params[i].mode == 0 && (params[i].typ.kind == "int" || params[i].typ.kind == "bool") {\n            let h', '        if params[i].mode == 0 && params[i].typ.kind == "int" {\n            let h')],
    "a local in a register as a statement accepted": [('            if x.mode != "reg" || x.home { errorAt("this expression does nothing"', '            if x.mode != "reg" { errorAt("this expression does nothing"')],
    "destinations not counted in the register cap": [("                    held = 2 * lhsCount", "                    held = 0")],
})
SHAPES = {"scan: nothing counted", "scan: ranking ignored", "scope end does not give the register back",
          "runtime calls push the locals' registers", "return copies a local's register",
          "for limit's register never given back", "bool parameters left in memory"}
EDGE = {"destinations not counted in the register cap"}

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
        if name in ENCODER:
            code = f"import port_test as pt; pt.SRC = {path!r}; pt.main()"
        if name in EDGE:
            code = f"import homes_edge as he; he.SRC = {path!r}; he.main()"
        if name in SHAPES:
            code = f"import homes_check as hc; hc.SRC = {path!r}; hc.main()"
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
