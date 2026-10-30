#!/usr/bin/env python3
"""Mutation testing for the port (obo/src/obo.obo): plants a bug in a copy,
runs the Stage B comparison against it (port_compare.compare, with the copy as
its source), and checks that the comparison fails. Divergent output must show up as a
mismatch, freeing too early as a dangling-handle trap, and keeping memory alive
as a peak more than MEMORY_MARGIN above the unmutated one. A mutant of the encoder (ENCODER) runs
port_test.py instead, whose objdump cross-check must catch it; a mutant of the image emitter
(IMAGE) runs elf_test.py, because it changes only the produced file's segment sizes.

Each mutant's comparison reuses obo's outcomes, stops at the first difference, and
allows each program 20 times what obo took; MUTATE_JOBS mutants run side by side and
MUTATE_INNER_JOBS cases inside each comparison (default MUTATE_JOBS=4, inner the core
count divided by it, so the two multiply to about the core count; both are positive
integers and anything else is an error). Measured here on a
28-mutant sample: 4 outer x 4 inner took 41.9 s wall (166 s CPU), against 46.9 s for
2 x 8 and 95.2 s (309 s CPU) for the old 4 x 16 -- the cases are process spawns, and
more than the core count only contends. A comparison alone uses one worker per core.

    python3 obo/tests/mutate_port.py [pattern]"""
import concurrent.futures
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(os.path.dirname(HERE), 'src', 'obo.obo')
sys.path.insert(0, HERE)
import port_compare as pc  # noqa: E402
import mutate_stats  # noqa: E402

DEFAULT_JOBS = 4                # outer mutants; MUTATE_JOBS overrides
TRANSIENT = re.compile(r'timed out|out of memory|no result in', re.IGNORECASE)
CATCHER_FILE = os.path.join(HERE, 'mutant_catchers.tsv')

MARK = "    let symMark = len(symArena)                 // the function's own symbols start here\n"
FREE = "    truncate(symArena, symMark)                 // and nothing refers to them any more\n"
PARAMS_DONE = "        }\n    }\n    expect(T_LBRACE, \"\")\n    openBlock()\n"
BLOCKS_CLOSE = "    closeBlock(term)\n    closeBlock(term)\n    homeBits = 0\n"

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
    "callee frees not recorded": [("        for g in freed { recordFree(g) }\n", "")],
    # a self-call is a change in its body, so it must not be taken for a call that frees nothing
    "recursive call might free": [("    if f.addr < 0 || f == curFn || f.freesAll { return true }",
                                   "    if f.addr < 0 || f.freesAll { return true }")],
    "recursive call is pure": [("    if f.addr < 0 || f == curFn || f.freesAll || len(f.fnRec.freed) > 0 { return false }",
                                "    if f.addr < 0 || f.freesAll || len(f.fnRec.freed) > 0 { return false }")],
    # a callee with a forward heading frees everything, and its callers must inherit that
    "forward heading not inherited": [("    if cur != nil && all && f != cur { cur.freesAll = true }",
                                       "    if cur != nil && f.addr < 0 && f != cur { cur.freesAll = true }")],
    # freeing symbols too early: a dangling-handle trap
    "frees one symbol too many": [(FREE, FREE.replace("symMark)", "symMark - 1)"))],
    "frees symbols before the blocks close": [(BLOCKS_CLOSE, "    truncate(symArena, symMark)\n" + BLOCKS_CLOSE)],
    # keeping symbols alive: over the unmutated peak by more than MEMORY_MARGIN (see try_mutant)
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
    "suspect uses not noted": [("    if s.suspect && (!target || path != \"\" || isCall) {", "    if false {")],
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
    # the loop-carried suspicion (fix for "a recursive call in a loop, read on the next iteration", the loop check):
    # the state at the body's end, which is where the next iteration starts, is not carried. Caught by
    # errors/handle_deep.obx "a loop's body reads a reference at its top, on the next iteration after a recursive call on one path"
    "loop-carried suspicion from the body's end dropped": [("            if ownedLocals[i].suspect { loops[li].back[i] = true }",
                                                           "            if false { loops[li].back[i] = true }")],
    # a body that always leaves keeps its suspicion after the loop. Caught by run/handle_deep.obx "retLeak"
    "terminated loop body's suspicion kept": [("    if term { restore(entry) } else { restoreMoved(entry) }", "    restoreMoved(entry)")],
    # the loop check reports a reference the loop never reads: caught by run/handle_deep.obx "unusedRef"
    "loop check reports unread references": [("        if back[i] && ownedLocals[i] != lv && ownedLocals[i].usedAt > useBefore {",
                                              "        if back[i] && ownedLocals[i] != lv {")],
    # reads in a loop are not counted: the loop check misses the reads it is for. Caught by errors/handle_deep.obx
    # "a loop's body reads a reference at its top, on the next iteration after a recursive call on one path"
    "reads in a loop not counted": [("            s.staleUse = useSeq\n            s.usedAt = useSeq\n", "            s.staleUse = useSeq\n")],
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
    "compound targets not checked": [("            var y = tailExpr()\n            pinned = savedPin\n            checkTargetAcrossCall(x, xs, r0, callsBefore, line, col)\n",
                                      "            var y = tailExpr()\n            pinned = savedPin\n")],
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
    "obo call pad dropped when misaligned": [("    let pad = (depth + 8 * nw) % 16\n", "    let pad = 0\n")],
    "push imm not counted in the depth": [("            pushI(a.val)                           // no register needed\n            depth += 8\n",
                                           "            pushI(a.val)                           // no register needed\n")],
    # an imm8 push for a value that needs imm32: caught by the objdump cross-check
    "push imm8 for any imm32": [("    if fits8(v) {\n        emit(0x6A)", "    if fits32(v) {\n        emit(0x6A)")],
    "constant runtime argument lost": [("                append(argConsts, a.val)\n", "                append(argConsts, 0)\n")],
    # a function without locals loses its 'sub rsp,0': its entry moves 7 bytes on and its body stays (see "entry:" below)
    "dropped frame: calls to known functions not recorded": [("            fix(at, f.addr)\n            append(farCalls, at)\n", "            fix(at, f.addr)\n")],
    # the boundaries of push imm8 and imm32, caught by the same program (Stage B, not only the objdump group)
    "push imm8 up to 128": [("fn pushI(v: int) {\n    if fits8(v) {", "fn pushI(v: int) {\n    if v >= -128 && v <= 128 {")],
    "push imm8 from -129": [("fn pushI(v: int) {\n    if fits8(v) {", "fn pushI(v: int) {\n    if v >= -129 && v < 128 {")],
    "push imm32 masked": [("        emit(0x68)\n        emit32(v)\n", "        emit(0x68)\n        emit32(v & 0x7FFFFFFF)\n")],
    "frame dropped although there are locals": [("    if size == 0 {\n        dropFrame(frameAt, f)", "    if size <= 16 {\n        dropFrame(frameAt, f)")],
    "held: replace does not free what it holds": [('        consume(v)\n        freeEvent(x, place, raw, orig, ident)\n', '        consume(v)\n')],
    "held: take does not free what it holds": [('        freeEvent(x, lastPlace, lastRaw, lastOrigin, lastIdent)\n', '')],
    "held: moves do not reach held handle targets": [('        } else if heldForeign[i] {\n            if s.isParam || !refParamKey(heldKey[i]) { heldHit[i] = true }', '        } else if false {\n            if s.isParam || !refParamKey(heldKey[i]) { heldHit[i] = true }')],
    "held: moves do not free handle targets": [('        heldMove(s)\n', '')],
    "held: identity frees do not reach their entries": [('                if heldUnder(place, heldPath[k]) && !(heldStore[k] && place == heldPath[k] && place != anyPlace) { heldHit[k] = true }', '                if false { heldHit[k] = true }')],
    "held: same-identity frees ignore the path": [('                if heldUnder(place, heldPath[k]) && !(heldStore[k] && place == heldPath[k] && place != anyPlace) { heldHit[k] = true }', '                heldHit[k] = true')],
    "held: identity exemption removed": [('                if heldUnder(place, heldPath[k]) && !(heldStore[k] && place == heldPath[k] && place != anyPlace) { heldHit[k] = true }', '                if heldUnder(place, heldPath[k]) { heldHit[k] = true }')],
    "held: origin ignored when holding": [('        if origin != nil {\n            heldAdd(origin, false, false, raw)', '        if false {\n            heldAdd(origin, false, false, raw)')],
    "held: origin ignored when freeing": [('        if origin != nil {\n            heldFree(origin, nil, raw)', '        if false {\n            heldFree(origin, nil, raw)')],
    "held: whole local arrays hit by identity frees": [('        } else if !heldWhole[k] {', '        } else {')],
    "held: reference parameters take their owner's place": [('    if isRefParam(r) {\n        heldAdd(r, true, false, raw)', '    if false {\n        heldAdd(r, true, false, raw)')],
    "held: owned frees ignore the path": [('            if heldKey[k] == o && heldUnder(place, heldPath[k]) && !(heldStore[k] && place == heldPath[k] && place != anyPlace) {', '            if heldKey[k] == o && !(heldStore[k] && place == heldPath[k]) {')],
    "held: owned assignment exemption removed": [('            if heldKey[k] == o && heldUnder(place, heldPath[k]) && !(heldStore[k] && place == heldPath[k] && place != anyPlace) {', '            if heldKey[k] == o && heldUnder(place, heldPath[k]) {')],
    "held: store exemption not applied through handles": [('    for k in mark..len(heldKey) { heldStore[k] = true }', '    for k in mark..len(heldKey) { heldStore[k] = !viaHandle }')],
    "held: index does not hold its array": [('            let hp = heldPush(root, viaHandle, hOrig, hIdent, placePath(root, path), path)', '            let hp = -1')],
    "held: replace does not hold its place": [('        let hp = heldPush(tr, x.viaHandle, orig, ident, place, raw)', '        let hp = -1')],
    "held: append and truncate do not hold their array": [('        let hp = heldPush(r0, aVia, aOrig, aIdent, aPlace, aRaw)', '        let hp = -1')],
    "held: plain assignments do not hold their target": [('            if x.deref || x.viaHandle { hp = heldPushStore(r0, x.viaHandle, torig, tident, tplace, traw) }\n            var y = tailExpr()\n            pinned = savedPin\n            checkAssign(dtype, y, "assignment")\n', '            var y = tailExpr()\n            pinned = savedPin\n            checkAssign(dtype, y, "assignment")\n')],
    "held: compound assignments do not hold their target": [('            if x.deref || x.viaHandle { hp = heldPushStore(r0, x.viaHandle, torig, tident, tplace, traw) }\n            var y = tailExpr()\n            pinned = savedPin\n            checkTargetAcrossCall(x, xs, r0, callsBefore, line, col)\n', '            var y = tailExpr()\n            pinned = savedPin\n            checkTargetAcrossCall(x, xs, r0, callsBefore, line, col)\n')],
    "held: reference places ignore the binding's path": [('            let p = heldAt(b, i, raw, r)\n            if o == handleSym {\n                heldAdd(nil, true, false, "")', '            let p = raw\n            if o == handleSym {\n                heldAdd(nil, true, false, "")')],
    # loop inversion: a while loop's condition moves after its body, and each kind of position it holds must move
    # with it (tests/run/loop_inversion.obx)
    "inverted loop: calls out of the condition not adjusted": [("        putRel(farCalls[k], getRel(farCalls[k]) - d)\n", "")],
    "inverted loop: calls out of the condition not recorded at the new place": [("        farCalls[k] += d\n", "")],
    "inverted loop: waiting calls in the condition not adjusted": [("append(waitFn[k].fnRec.fixups, waitAt[k] + d)", "append(waitFn[k].fnRec.fixups, waitAt[k])")],
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

# mutants of the image emitter: they change only the produced file (the zero tail
# in p_filesz, p_memsz shrunk to p_filesz, or only the first prefix byte copied
# while the sizes stay right), so the comparison's outcomes and peak are the
# same; elf_test.py judges them. The wrong epilogue opcode builds crashing
# programs, so the ordinary comparison catches it.
IMAGE = {"image: the runtime data keeps its zero tail",
         "image: the runtime data memory size is its file size",
         "image: the runtime data copies only its first byte"}


# the reference bindings per path (obo/tools/port_ref_binds.py): the mutants whose text the rewrite
# replaced are retargeted to the same behaviour, and the join and the loop check have their own
MUTANTS.update({
    "handle borrows survive other changes": [("if r == handleSym || r == root && (all || b.deeps[i] && underPath(b.paths[i], path)) { return true }",
                                              "if (r == handleSym && root == r) || r == root && (all || b.deeps[i] && underPath(b.paths[i], path)) { return true }")],
    "handle borrows passable to freeing calls": [("            if b.roots[p] == handleSym && !pureFn(f) {", "            if false {")],
    "handle temporaries invalidate nothing": [("if x.viaHandle { invalidate(handleSym, via, false, \"\") }",
                                               "if x.viaHandle && r != nil { invalidate(handleSym, via, false, \"\") }")],
    "handle temporary assignments record nothing": [("                } else if x.deref || x.viaHandle {\n                    invalidateOwnedChange(x)\n                    recordFreeChange(x, false)\n",
                                                     "                } else if x.deref || x.viaHandle {\n                    invalidateOwnedChange(x)\n                    if x.deref { recordFreeChange(x, false) }\n")],
    "body write doesn't invalidate deep refs": [("    if throughHandle(x) { invalidateDeep(viaOf(x)) }\n", "    if false { invalidateDeep(viaOf(x)) }\n")],
    "change through a borrowed handle reference doesn't invalidate deep refs": [("    if throughHandle(x) { invalidateDeep(viaOf(x)) }",
                                                                              "    if x.viaHandle { invalidateDeep(viaOf(x)) }")],
    "self-call suspects not marked": [("if v.isRefLocal && anyDeep(v.bind) && !v.moved { v.suspect = true }", "if false { v.suspect = true }")],
    "shallow references invalidated by handle writes": [("if v.isRefLocal && anyDeep(v.bind) && !ancestorOf(v, via) && !v.moved {",
                                                         "if v.isRefLocal && !ancestorOf(v, via) && !v.moved {")],
    "scalar truncate through a handle is a write": [('if owned || !throughHandle(a) || baseOf(a.typ).kind == "string" { recordFreeChange(a, true) }', 'recordFreeChange(a, true)')],
    "suspect not joined at if or match": [("            sameStates(s1, s2, iline, icol)\n            joinSuspect(s1)\n",
                                           "            sameStates(s1, s2, iline, icol)\n"),
                                          ("        sameStates(s1, s0, iline, icol)\n        joinSuspect(s1)\n",
                                           "        sameStates(s1, s0, iline, icol)\n"),
                                          ("if a.flags[n + i] { out.flags[n + i] = true }", "if false { out.flags[n + i] = true }")],
    "loop self-call counter taken after the condition": [("    let selfBefore = selfCalls\n    let useBefore = useSeq\n",
                                                          "    let useBefore = useSeq\n"),
                                                         ("    let condSt = snapshot()                  // the state when the condition leaves the loop",
                                                          "    let selfBefore = selfCalls\n    let condSt = snapshot()                  // the state when the condition leaves the loop")],
    # a break carries a neutral exit state: its suspicion (and every other mark) is not recorded
    "break suspicion not recorded": [("                append(lp.exits, now)\n", "                append(lp.exits, neutralState(localsIn(&now)))\n                drop(now)\n")],
    "loop-carried suspicion from the body's end dropped": [("        absorb(&loops[li].back, &now)\n", "        if false { absorb(&loops[li].back, &now) }\n")],
    "loop-carried suspicion from continue dropped": [("                append(lp.conts, now)\n", "                append(lp.conts, neutralState(localsIn(&now)))\n                drop(now)\n")],
    "terminated loop body's suspicion kept": [("    if term { restore(&entry) } else { restoreMoved(&entry) }", "    restoreMoved(&entry)")],
    "loop check reports unread references": [("back.flags[localsIn(back) + i] && ownedLocals[i] != lv && ownedLocals[i].usedAt > useBefore {",
                                              "back.flags[localsIn(back) + i] && ownedLocals[i] != lv {")],
    # the deferred body's recursive calls are replayed where the block is left (replayFrees kind 2);
    # dropping that replay keeps their suspicion from reaching the older defers and the block exit
    "deferred self-call suspicion not replayed": [("        } else {\n            markSuspects()\n        }\n", "        } else {\n        }\n")],
    # the join of two bindings keeps the binding of the last path, not the union of the paths
    "binding not joined: last path wins": [("    if a == b || b == noneBind { return a }\n    if a == noneBind { return b }\n",
                                            "    if b == noneBind { return a }\n    if true { return b }\n")],
    # a join keeps only the roots of the first path: a change under a root of the second is missed
    "join keeps only the first root": [("        if !found {\n            append(roots, b.roots[j])", "        if false {\n            append(roots, b.roots[j])")],
    # the spec's rule: the common prefix, which for a prefix pair is the shorter path; the longer one is hit
    # by every change that hits the shorter one and by changes below it
    "path join keeps the shorter path (the spec's rule)": [('    if segsPrefix(pa, pb) && !segsPrefix(pb, pa) { return "" }\n    if segsPrefix(pb, pa) && !segsPrefix(pa, pb) { return "" }',
                                                            "    if segsPrefix(pa, pb) && !segsPrefix(pb, pa) { return pa }\n    if segsPrefix(pb, pa) && !segsPrefix(pa, pb) { return pb }")],
    # writable only where both paths are: a reference writable on one path only is read-only after the join
    "refMut not ANDed at a join": [("    let writable = a.writable && b.writable", "    let writable = a.writable || b.writable")],
    # the loop's bindings at its end are never checked against the ones its body was checked with
    "loop back-edge binding check dropped": [("    loopBindCheck(&entry, &back, useBefore, lv, line, col, hit)\n", "")],
})

# the uses of a defer body: the same mutants as the fork's (see mutate_fork.py); the error cases of
# errors/defer_uses.obx show them, since the port's own output does not change
from mutate_fork import DEFER_USE_MUTANTS, env_jobs  # noqa: E402
MUTANTS.update({k: [v] for k, v in DEFER_USE_MUTANTS.items()})
# the references invalid on one path (errors/one_path_uses.obx, run/one_path.obx show them)
from mutate_fork import ONE_PATH_MUTANTS  # noqa: E402
MUTANTS.update({k: [v] for k, v in ONE_PATH_MUTANTS.items()})
from mutate_fork import ORIGIN_MUTANTS, EQUIVALENT as FORK_EQUIVALENT  # noqa: E402
# the fork's equivalent mutants are not run here: the port has the same source, so they are equivalent too
MUTANTS.update({k: [v] for k, v in ORIGIN_MUTANTS.items() if k not in FORK_EQUIVALENT})
# Stage 1 modules: the module trees of port_compare exercise these (see FORK.md)
from mutate_fork import MODULE_MUTANTS  # noqa: E402
MUTANTS.update({k: [v] for k, v in MODULE_MUTANTS.items() if k not in FORK_EQUIVALENT})
# argument order: the error and run cases of arg_order.obx show them, as for the defer uses
from mutate_fork import ARG_ORDER_MUTANTS  # noqa: E402
MUTANTS.update({k: [v] for k, v in ARG_ORDER_MUTANTS.items() if k not in FORK_EQUIVALENT})
# a handle's object freed by an assignment over, or a drop of, its owner: the cases of handle_frees.obx
from mutate_fork import HANDLE_FREE_MUTANTS  # noqa: E402
from mutate_fork import METHOD_MUTANTS  # noqa: E402
from mutate_fork import FP_MUTANTS, REF_PATH_MUTANTS, MOVE_MUTANTS, FIELD_MUTANTS  # noqa: E402
# the I/O builtins: the port compiles the run and error I/O cases (and, for the
# two module-root programs, the imported library modules), so these compiler
# mutants are judged by the Stage B comparison too
from mutate_fork import IO_MUTANTS  # noqa: E402
MUTANTS.update({k: [v] for k, v in IO_MUTANTS.items()})
# the image emitter (runtime data segment and epilogue): elf_test.py judges the two
# segment mutants; the epilogue ones are equivalent/caught as in the fork's notes
from mutate_fork import SIZE_MUTANTS  # noqa: E402
MUTANTS.update({k: [v] for k, v in SIZE_MUTANTS.items() if k not in FORK_EQUIVALENT})
MUTANTS.update({k: [v] for k, v in FP_MUTANTS.items() if k not in FORK_EQUIVALENT})
MUTANTS.update({k: [v] for k, v in REF_PATH_MUTANTS.items() if k not in FORK_EQUIVALENT})
MUTANTS.update({k: [v] for k, v in MOVE_MUTANTS.items() if k not in FORK_EQUIVALENT})
# handles made from a field or an element of an owned local (errors/field_origins.obx, run/field_origins*.obx)
MUTANTS.update({k: [v] for k, v in FIELD_MUTANTS.items() if k not in FORK_EQUIVALENT})
MUTANTS.update({k: [v] for k, v in HANDLE_FREE_MUTANTS.items() if k not in FORK_EQUIVALENT})
MUTANTS.update({k: [v] for k, v in METHOD_MUTANTS.items() if k not in FORK_EQUIVALENT})


# register allocation (FORK.md, Phase 2). A home is r12 to r15, taken out of the free list while its local is in
# scope; the callee may use any pool register, so the caller pushes the homes in scope around an obo call, and
# the C runtime keeps them. Most are caught by tests/run/register_alloc.obx; the SHAPES ones change speed only
# and are caught by tests/homes_check.py
MUTANTS.update({
    "homes not pushed around obo calls": [("    var saved = usedRegs()\n    if hasRecv {",
                                           "    var saved = usedRegs() & ~homeBits\n    if hasRecv {")],
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
    "scope end does not give the register back": [("        if s.home >= 0 && !homeRetry { releaseHome(s.home) }", "        if s.home < 0 && !homeRetry { releaseHome(s.home) }")],
    "runtime calls push the locals' registers": [("    let saved = usedRegs() & ~homeBits      // the C runtime keeps r12 to r15, where the locals are", "    let saved = usedRegs()")],
    "return copies a local's register": [("        let x = asReg(x0)\n        for k in blocks[fnBlock].omark", "        let x = load(x0)\n        for k in blocks[fnBlock].omark")],
    "for limit's register never given back": [("            if lim.home >= 0 { append(blocks[len(blocks) - 1].syms, lim) }       // so that the block's end frees it\n", "")],
    "bool parameters left in memory": [('        if params[i].mode == 0 && (params[i].typ.kind == "int" || params[i].typ.kind == "bool") {\n            let h', '        if params[i].mode == 0 && params[i].typ.kind == "int" {\n            let h')],
    "a local in a register as a statement accepted": [('        if x.mode != "reg" || x.home { errorAt("this expression does nothing"', '        if x.mode != "reg" { errorAt("this expression does nothing"')],
    # a function that runs out of registers with locals in them is compiled again with none (FORK.md, Phase 2);
    # tests/run/register_pressure.obx has such functions, one with calls waiting for a later function. Not planted,
    # equivalent: the counters (selfCalls, userCalls, useSeq, fwdCalls) are only compared with their value at a
    # point in the same pass, and what the first pass recorded as freed (f.fnRec.freed, freesAll, writesHandles) the
    # second records again, the checker being the same
    "retry: locals' registers not given up": [("    if len(free) == 0 && homeBits != 0 { giveUpHomes() }\n", "")],
    "retry: code not rewound": [("        truncCode(f.addr)\n        for gi in 0..len(curModule.globals) {", "        for gi in 0..len(curModule.globals) {")],
    "retry: waiting calls of the first pass kept": [("                let fr = g.fnRec\n                while len(fr.fixups) > 0 && fr.fixups[len(fr.fixups) - 1] >= f.addr { truncate(fr.fixups, len(fr.fixups) - 1) }\n", "                let fr = g.fnRec\n")],
    "retry: argument holders of the first pass kept": [("        truncate(argTemps, tempMark)\n        truncate(symArena, symMark)\n        return true", "        truncate(symArena, symMark)\n        return true")],
    "retry: free list left as the first pass had it": [("    homeBits = 0\n    setFree(0)                          // the registers in pool order: a function starts as the one before it did not leave them\n", "    homeBits = 0\n")],
    "retry: scanner not rewound": [("        pos = sPos\n", "")],
    "retry: token not restored": [("        sym = sSym\n", "")],
})
SHAPES = {"align: loops not padded", "align: wrong padding", "align: while loops not padded", "align: while true not padded",
          "align: for ranges not padded", "align: for arrays not padded", "align: not padded when compiled again",
          "align: 4-byte nop contents", "align: 5 and 6-byte nop contents", "align: 7-byte nop contents", "align: 8 and 9-byte nop contents",
          "fold: refused at a jump target", "fold: never (speed only)", "fold: the copy is kept", "scan: nothing counted", "scan: ranking ignored", "scope end does not give the register back",
          "runtime calls push the locals' registers", "return copies a local's register",
          "for limit's register never given back", "bool parameters left in memory"}
EDGE = {"retry: free list left as the first pass had it"}

# the peephole (FORK.md, "Peephole pass"): the store of `x = x op y` on a local in a register folds the copy, the
# operation and the copy back into one operation on the local. Caught by tests/run/peephole.obx (through the
# comparison of the port's output with obo's) or, for the one that only costs speed, by tests/homes_check.py.
# Not planted, equivalent: `barrier <= foldAt` (no emitter asks for a position between a copy and its store, so
# nothing reaches the guard today) and the records cleared in truncCode (a cut-back would have to be followed by
# code ending exactly where the old operation did, with the same registers), and `cpDst == t` in noteFold (an
# operation on a register right after the copy of a local into another one: no emitter makes that shape).
MUTANTS.update({
    "fold: more was emitted after the operation": [("if foldEnd == here() && foldT == src.r", "if foldT == src.r")],
    "fold: a different local": [(" && foldHome == dst.r && barrier <= foldAt {", " && barrier <= foldAt {")],
    "fold: the copy is kept": [("    truncCode(foldAt)\n    if form == 1 {", "    if form == 1 {")],
    "fold: immediate changed": [("        aluRI(opc, h, arg)\n    } else if form == 2 {", "        aluRI(opc, h, arg + 1)\n    } else if form == 2 {")],
    "fold: operand register is the temporary": [("        opReg(opc, h, arg, true, false)\n    } else {", "        opReg(opc, h, foldT, true, false)\n    } else {")],
    "fold: imul by an immediate keeps the temporary": [("        opReg(0x69, h, h, true, false)\n        emit32(arg)\n    }\n}", "        opReg(0x69, foldT, h, true, false)\n        emit32(arg)\n    }\n}")],
    "fold: a loaded operand taken for a register (alu)": [('            let direct = y.mode == "reg"\n            let ry = y.r\n            let a0 = here()\n            opOperand(c * 8 + 3', '            let direct = true\n            let ry = y.r\n            let a0 = here()\n            opOperand(c * 8 + 3')],
    "fold: a loaded operand taken for a register (imul)": [('            let direct = y.mode == "reg"\n            let ry = y.r\n            let a0 = here()\n            opOperand(0x0FAF', '            let direct = true\n            let ry = y.r\n            let a0 = here()\n            opOperand(0x0FAF')],
    "fold: the operation need not follow the copy": [("    if cpEnd == a0 && cpDst == t {", "    if cpDst == t {")],
    "fold: refused at a jump target": [(" && barrier <= foldAt {", " && barrier < foldAt {")],
    "fold: never (speed only)": [("            if foldEnd == here() && foldT", "            if false && foldT")],
})

# loop-head alignment (FORK.md, "Loop-head alignment"): the target of a backward jump is on a 32-byte boundary.
# Speed only, so the SHAPES ones are caught by tests/homes_check.py, which finds the targets in the disassembly,
# in a function with a frame, one without, and one compiled again with no registers. The entry-move ones change what
# runs and are caught by the comparison, through tests/run/loop_align.obx: the calls to a function with no locals,
# from itself and from the functions before and after it, must go to the entry that moved.
ALIGN_N = "    var n = (LOOP_ALIGN - (pc() & (LOOP_ALIGN - 1))) & (LOOP_ALIGN - 1)\n"
MUTANTS.update({
    "align: loops not padded": [(ALIGN_N, "    var n = 0\n")],
    "align: wrong padding": [(ALIGN_N, "    var n = (LOOP_ALIGN - 1 - (pc() & (LOOP_ALIGN - 1))) & (LOOP_ALIGN - 1)\n")],
    "align: while loops not padded": [("    alignLoop()\n    let bodyAt = pc()\n", "    let bodyAt = pc()\n")],
    "align: while true not padded": [("            alignLoop()\n            top = pc()\n        }\n        x = newItem", "            top = pc()\n        }\n        x = newItem")],
    "align: for ranges not padded": [("        alignLoop()\n        top = pc()\n        loopdepth += 1\n", "        top = pc()\n        loopdepth += 1\n")],
    "align: for arrays not padded": [("        alignLoop()\n        top = pc()\n        let ri = alloc()", "        top = pc()\n        let ri = alloc()")],
    "align: not padded when compiled again": [(ALIGN_N, "    if homeCap == 0 { return }\n" + ALIGN_N)],
    # the bytes of the padding: homes_check.py requires exactly the nops alignLoop emits, for every length
    "align: 4-byte nop contents": [("            } else if k == 4 {\n                emit(0x40)", "            } else if k == 4 {\n                emit(0xCC)")],
    "align: 5 and 6-byte nop contents": [("            } else if k == 5 || k == 6 {\n                emit(0x44)", "            } else if k == 5 || k == 6 {\n                emit(0xCC)")],
    "align: 7-byte nop contents": [("            } else if k == 7 {\n                emit(0x80)", "            } else if k == 7 {\n                emit(0xCC)")],
    "align: 8 and 9-byte nop contents": [("                emit(0x84)                      // 8 and 9", "                emit(0xCC)                      // 8 and 9")],
    "entry: calls to itself keep the old entry": [("        if ((p + 4 + getRel(p)) & 0xFFFFFFFF) == old { fix(p, f.addr) }", "        if p < 0 { fix(p, f.addr) }")],
    "entry: waiting calls keep the old entry": [("    for fi in 0..len(f.fnRec.fixups) { fix(f.fnRec.fixups[fi], f.addr) }\n}", "}")],
    "entry: stays where it was": [("    f.addr = old + 7\n", "")],
    "entry: prologue not moved": [("    setCode(old + 7, 0x55)\n", "")],
})

# PEAK_BUDGET is far above the port's peak, so a mutant that keeps memory alive is judged here instead:
# its live peak must stay within this margin of the unmutated peak (the baseline compare in main()).
MEMORY_MARGIN = 1.03


def apply_edits(src, edits):
    """Apply a mutant's replacements in order; each must match exactly once.
    Returns (text, None), or (None, stale message) when any site no longer
    matches, which the gate reports as a miss rather than skipping the mutant."""
    text = src
    for k, (old, new) in enumerate(edits, 1):
        n = text.count(old)
        if n != 1:
            where = f" (site {k} found {n} times)" if len(edits) > 1 else f" (pattern found {n} times)"
            return None, f"STALE   {where}"
        text = text.replace(old, new)
    return text, None


def load_catchers(path=CATCHER_FILE):
    """{mutant: check name} from the committed first-failure table. The table is an
    order, not a verdict: a check that passes, or a name the catalogue no longer
    has, falls through to the full comparison (see FORK.md)."""
    out = {}
    if not os.path.exists(path):
        return out
    with open(path) as f:
        for line in f:
            line = line.rstrip('\n')
            if not line or line.startswith('#') or '\t' not in line:
                continue
            name, check = line.split('\t', 1)
            out[name] = check
    return out


TABLE = load_catchers()


def catchers_for(name, table=None):
    """The check names to try first for a mutant. The environment switch
    MUTATE_CATCHERS=0 turns the ordering off, for an all-full comparison."""
    if os.environ.get('MUTATE_CATCHERS', '1') == '0':
        return ()
    table = TABLE if table is None else table
    check = table.get(name)
    return (check,) if check else ()


def catcher_kinds(checks):
    """One representative check per kind, for the baseline probe."""
    kinds = {}
    for check in checks:
        if check.startswith('cli '):
            kind = 'cli'
        elif check.startswith('special '):
            kind = 'special'
        elif check.startswith('reemit '):
            kind = 'reemit'
        elif check == 'modules/tokens':
            kind = 'tokens'
        elif check.startswith('modules/'):
            kind = 'tree'
        else:
            kind = 'flat'
        kinds.setdefault(kind, check)
    return kinds


def validate_catchers(table, names):
    """(unknown, representatives) for the selected mutants. An unknown name is
    only noted: it cannot catch or pass anything. The representatives are run on
    the unmutated compiler before any mutant is judged, so a broken catcher path
    aborts the gate instead of making every mutant look caught."""
    known = pc.known_checks()
    unknown, checks = [], []
    for name in names:
        check = table.get(name)
        if check is None:
            continue
        if check not in known:
            unknown.append((name, check))
            continue
        checks.append(check)
    return unknown, catcher_kinds(checks)


def try_mutant(name, text, base_peak, workers=None, stats=None, use_catchers=True):
    """('caught' or 'missed', the line to print) for one mutant, in its own directory.
    workers is the number of case comparisons inside this mutant; the caller sets
    it so that outer mutants x workers is about the core count. stats, when given,
    collects the stage seconds and counts; it never changes the verdict."""
    tmp = tempfile.mkdtemp(prefix='obo-mutant-')
    try:
        path = os.path.join(tmp, 'mutant.obx')
        open(path, 'w').write(text)
        if name in IMAGE:
            stage = 'image'
            if stats is not None:
                stats.add('subprocesses')
            r = subprocess.run([sys.executable, os.path.join(HERE, 'elf_test.py'), '--source', path],
                               capture_output=True, text=True, cwd=HERE, timeout=300)
            lines = (r.stdout + r.stderr).strip().splitlines()
            failures = [l for l in lines if l.startswith('FAIL')] or \
                       ([lines[-1] if lines else f'exit {r.returncode}'] if r.returncode != 0 else [])
        elif name in ENCODER:
            stage = 'encoder'
            if stats is not None:
                stats.add('subprocesses')
            r = subprocess.run([sys.executable, '-c', f"import port_test as pt; pt.SRC = {path!r}; pt.main()"],
                               capture_output=True, text=True, cwd=HERE)
            lines = (r.stdout + r.stderr).strip().splitlines()
            failures = [l for l in lines if l.startswith('FAIL') or 'does not compile' in l] or \
                       ([lines[-1] if lines else f'exit {r.returncode}'] if r.returncode != 0 else [])
        elif name in EDGE or name in SHAPES:
            # the register-allocation checks: caught when their script exits nonzero
            stage = 'edge' if name in EDGE else 'shapes'
            module, fn = ('homes_edge', 'he') if name in EDGE else ('homes_check', 'hc')
            if stats is not None:
                stats.add('subprocesses')
            r = subprocess.run([sys.executable, '-c', f"import {module} as {fn}; {fn}.SRC = {path!r}; {fn}.main()"],
                               capture_output=True, text=True, cwd=HERE)
            lines = (r.stdout + r.stderr).strip().splitlines()
            failures = [] if r.returncode == 0 else \
                       ([l for l in lines if l.startswith('FAIL') or 'does not compile' in l] or
                        [lines[-1] if lines else f'exit {r.returncode}'])
        else:
            stage = 'compare'
            lines, failures, peak = pc.compare(tmp, path, quick=True, workers=workers,
                                               catchers=catchers_for(name) if use_catchers else (),
                                               stats=stats)
            failures = ["FAIL " + f for f in failures]
            if not failures and peak is not None and peak > base_peak * MEMORY_MARGIN:
                failures = [f"FAIL peak {peak:,} is over the unmutated {base_peak:,} by more than 3%"]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    if stats is not None:
        stats.set('stage', stage)
        stats.set('transient', bool(failures and (stats.get('catcher_transient')
                                                  or TRANSIENT.search(failures[0]))))
    if failures:
        return 'caught', f"caught  {name:40s} {failures[0][:110]}"
    return 'missed', f"MISSED  {name:40s} {lines[-1].strip() if lines else ''}"


def timing_inputs():
    runtime = {}
    for name in ('runtime.c', 'entry.S', 'rt.ld'):
        runtime[name] = mutate_stats.sha256_file(os.path.join(os.path.dirname(HERE), 'runtime', name))
    return {
        'source': mutate_stats.sha256_file(SRC),
        'ref': mutate_stats.sha256_file(pc.REF),
        'ref_version': pc.ref_version(pc.REF),
        'runtime': runtime,
        'tests': mutate_stats.dir_digest(HERE),
        'harness': {
            'mutate_port.py': mutate_stats.sha256_file(os.path.abspath(__file__)),
            'port_compare.py': mutate_stats.sha256_file(os.path.join(HERE, 'port_compare.py')),
            'mutate_stats.py': mutate_stats.sha256_file(os.path.join(HERE, 'mutate_stats.py')),
            'mutant_catchers.tsv': mutate_stats.sha256_file(CATCHER_FILE),
        },
    }


def main():
    run_start = time.perf_counter()
    pattern = sys.argv[1] if len(sys.argv) > 1 else ''
    src = open(SRC).read()
    todo = []
    for name, edits in MUTANTS.items():
        if pattern not in name:
            continue
        text, stale = apply_edits(src, edits)
        todo.append((name, text, stale))
    sink = mutate_stats.Sink('mutate-port-timings')
    if sink.path:
        print(f"note: timings go to {sink.path}", file=sys.stderr)
        sink.write(mutate_stats.header('mutate_port', timing_inputs(),
                                       {'git_head': mutate_stats.git_head(os.path.dirname(HERE)),
                                        'ref': os.path.abspath(pc.REF)}))
    # the unmutated comparison: its peak, and REF's outcomes, which every mutant's comparison reuses
    tmp = tempfile.mkdtemp(prefix='obo-mutant-base-')
    t0 = time.perf_counter()
    base_stats = mutate_stats.Stats()
    try:
        lines, failures, base_peak = pc.compare(tmp, stats=base_stats)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    base_stats.set('wall_seconds', round(time.perf_counter() - t0, 3))
    sink.write({'kind': 'baseline', 'verdict': 'passed' if not failures else 'failed',
                **base_stats.snapshot()})
    if failures:
        print("\n".join(lines))
        raise SystemExit("the unmutated port fails Stage B, so no mutant can be judged")
    jobs = env_jobs('MUTATE_JOBS', DEFAULT_JOBS)
    # the outer mutants and the workers inside each comparison multiply: keep the
    # product about the core count, or the extra workers only contend for the
    # process spawns the cases are made of
    workers = env_jobs('MUTATE_INNER_JOBS', 0) or max(1, (os.cpu_count() or 1) // jobs)
    # Verify the catcher paths on the unmutated compiler before any mutant uses
    # them. An unknown name is only noted and falls back to the full comparison;
    # a catcher that fails here is a harness fault, and aborts the gate.
    if os.environ.get('MUTATE_CATCHERS', '1') != '0':
        unknown, reps = validate_catchers(TABLE, [n for n, _, _ in todo])
        for name, check in unknown:
            print(f"note: catcher {check!r} for {name!r} is not in the catalogue; "
                  "the full comparison decides", file=sys.stderr)
        if reps:
            probe = mutate_stats.Stats()
            tmp = tempfile.mkdtemp(prefix='obo-mutant-catcher-')
            t0 = time.perf_counter()
            try:
                plines, pfailures, _ = pc.compare(tmp, quick=True, catchers=list(reps.values()),
                                                  catchers_only=True, stats=probe)
            finally:
                shutil.rmtree(tmp, ignore_errors=True)
            probe.set('wall_seconds', round(time.perf_counter() - t0, 3))
            sink.write({'kind': 'catcher-probe', 'verdict': 'passed' if not pfailures else 'failed',
                        'representatives': reps, **probe.snapshot()})
            if pfailures or probe.get('unknown_catchers') or probe.get('catcher_cases') != len(reps):
                print("\n".join(plines))
                raise SystemExit("the unmutated compiler fails a catcher path (above), so a mutant's "
                                 "catch could not be trusted")

    def work(item):
        name, text, stale = item
        st = mutate_stats.Stats()
        t = time.perf_counter()
        if text is None:
            status, line = 'missed', f"MISSED  {name:40s} {stale}"
        else:
            status, line = try_mutant(name, text, base_peak, workers, stats=st)
        st.set('wall_seconds', round(time.perf_counter() - t, 3))
        st.set('jobs', jobs)
        st.set('inner_jobs', workers)
        st.set('cpus', os.cpu_count())
        return name, text, status, line, st

    with concurrent.futures.ThreadPoolExecutor(jobs) as pool:
        results = list(pool.map(work, todo))
    # A failure that reads as a timeout or out of memory may be the load, not the
    # mutant: run it again alone and report that result, as mutate_fork does.
    for i, (name, text, status, line, st) in enumerate(results):
        if status == 'caught' and st.get('transient') and text is not None:
            retry = mutate_stats.Stats()
            t = time.perf_counter()
            status2, line2 = try_mutant(name, text, base_peak, 1, stats=retry)
            retry.set('wall_seconds', round(time.perf_counter() - t, 3))
            retry.set('jobs', 1)
            retry.set('inner_jobs', 1)
            retry.set('cpus', os.cpu_count())
            retry.set('retry_of', 'parallel')
            results[i] = (name, text, status2, line2 + "   (serial re-run: the parallel run timed out or ran out of memory)", retry)
    for name, text, status, line, st in results:
        print(line)
        sink.write({'kind': 'mutant', 'name': name, 'verdict': status, 'line': line, **st.snapshot()})
    caught = sum(status == 'caught' for _, _, status, _, _ in results)
    sink.write({'kind': 'run', 'wall_seconds': round(time.perf_counter() - run_start, 3),
                'mutants': len(results), 'caught': caught, 'jobs': jobs, 'inner_jobs': workers})
    print(f"\n{caught}/{len(results)} mutants caught")
    sys.exit(0 if caught == len(results) else 1)


if __name__ == '__main__':
    main()
