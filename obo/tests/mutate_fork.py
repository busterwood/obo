#!/usr/bin/env python3
"""Mutation testing for obo: plants ownership bugs in a copy of src/obo.obo, builds
each with the seed compiler, and checks that the suite fails. Bugs that free
too little must be caught by the leak check, bugs that free too much by the
double-free check or poisoning, and missing compile-time checks by the error
tests.

    python3 obo/tests/mutate_fork.py [pattern]

Mutants run side by side, one per core; MUTATE_JOBS sets how many. Each mutant's
run_fork stops at its first failing test (the verdict is the same: a suite with a
failing test fails), and MUTATE_INNER_JOBS tests run at once inside it, so that
MUTATE_JOBS x MUTATE_INNER_JOBS is about the number of cores. Both are positive
integers; anything else is an error before a mutant starts.

The unmutated baseline must pass the normal suite and then the same judge path a
mutant takes (fail-fast, the smoke subset, the inner test count), and report the
full count: a broken judge path would otherwise make every mutant look caught."""
import concurrent.futures
import os
import re
import shutil
import subprocess
import sys
import tempfile

TRANSIENT = re.compile(r'timed out|out of memory', re.IGNORECASE)   # run_fork's wording for a load failure
HERE = os.path.dirname(os.path.abspath(__file__))
FORK = os.path.dirname(HERE)
DEFAULT_JOBS = os.cpu_count() or 1

# Patterns for the port where the text differs from the original obx compiler.
PORT_PATTERNS = {
    "scope exit frees nothing": ("        callDefers(last)\n        freeOwned(om, len(ownedLocals))\n",
                                 "        callDefers(last)\n"),
    "early exits free nothing": ("        freeOwned(om, top)\n", ""),
    "records don't free their fields": ("        if isOwned(ft) { append(ptrs, fo) }                      // the fields freed with the record\n", ""),
    "copies take no reference": ('        } else if x.mode != "const" {\n            let r = rtCall1(RT_STR_INC, dupItem(x), tString)',
                                 '        } else if false {\n            let r = rtCall1(RT_STR_INC, dupItem(x), tString)'),
    "loop variable takes no reference": ("            let held = rtCall1(RT_STR_INC, dupItem(vi), tString)   // and hold the new one\n            release(held.r)\n", ""),
    "records keep their strings": ("        if ft.kind == \"string\" { append(strs, fo) }              // and the strings it drops\n", ""),
    "callees never free globals": ("    if f.addr < 0 || f == curFn || f.freesAll { return true }\n    let freed = &f.fnRec.freed",
                                   "    if false { return true }\n    let freed = &f.fnRec.freed"),
    "frees not recorded": ('    if g.storage != "global" { return }\n    let freed = &f.fnRec.freed',
                           '    if true { return }\n    let freed = &f.fnRec.freed'),
    "loop moves unchecked": ("    if !term {\n        let now = snapshot()", "    if false {\n        let now = snapshot()"),
    "string locals never dropped": ("            if s.isStr {\n                rtCall1(RT_STR_DEC, x, tVoid)",
                                    "            if s.isStr {\n                rtCall1(RT_STR_INC, x, tVoid)"),
    "dropped results kept": ("        if isOwned(x.typ) {\n            rtCall1(RT_FREE, x, tVoid)                 // a dropped result is freed at once",
                             "        if false {\n            rtCall1(RT_FREE, x, tVoid)                 // a dropped result is freed at once"),
}

MUTANTS = {
    # freeing too little: the leak check must notice
    "scope exit frees nothing": ("        callDefers(blk)\n        freeOwned(blk.omark, len(ownedLocals))\n",
                                 "        callDefers(blk)\n"),
    "early exits free nothing": ("        freeOwned(blocks[i].omark, top)\n", ""),
    "assignment keeps the old value": ("                if live {\n                    resolve(x)", "                if false {\n                    resolve(x)"),
    "dropped results kept": ("            rtCall1(RT_FREE, x, tVoid)                 // a dropped result is freed at once",
                             "            releaseItem(x)"),
    "records don't free their fields": ("        if isOwned(f.typ) { append(ptrs, f.offset) }            // the fields freed with the record", ""),
    "globals not freed at exit": ("            x.disp = g.offset\n            rtCall1(RT_FREE, x, tVoid)\n", "            x.disp = g.offset\n"),
    # freeing too much: double free or poisoned reads
    "moved locals still freed": ("        if !s.moved && !s.isRefLocal {\n            let x = newItem", "        if !s.isRefLocal {\n            let x = newItem"),
    "moves not recorded": ("        s.moved = true\n        s.touches = s.touches + 1\n        heldMove(s)\n        invalidate(s, nil, true, \"\")", "        s.touches = s.touches + 1\n        heldMove(s)\n        invalidate(s, nil, true, \"\")"),
    "take leaves the value in place": ("        opMem(0x8B, r, memOf(x), true, false)\n        opMem(0xC7, 0, memOf(x), true, false)\n        emit32(0)\n",
                                       "        opMem(0x8B, r, memOf(x), true, false)\n"),
    # string reference counts
    "fresh strings shown to the runtime kept": ("            rtCall(RT_STR_DEC, one, tVoid)\n", ""),
    "copies take no reference": ("        } else if x.mode != \"const\" {\n            let r = rtCall1(RT_STR_INC, x, tString)", "        } else if false {\n            let r = rtCall1(RT_STR_INC, x, tString)"),
    "assignment keeps the old string": ("            } else if dtype.kind == \"string\" {\n                resolve(x)", "            } else if false {\n                resolve(x)"),
    "string locals never dropped": ("            if s.isStr {\n                rtCall1(RT_STR_DEC, x, tVoid)", "            if s.isStr {\n                releaseItem(x)"),
    "held strings not pinned": ("    if x.typ.kind == \"string\" && !x.strTemp && x.mode != \"const\" {\n        consume(x)", "    if false {\n        consume(x)"),
    "loop variable takes no reference": ("        if strs { release(rtCall1(RT_STR_INC, vi, tString).r) }", ""),
    "records keep their strings": ("        if f.typ.kind == \"string\" { append(strs, f.offset) }    // and the strings it drops", ""),
    "call results not fresh": ("    res.strTemp = f.ret == tString               // the caller now holds the returned reference", ""),
    # references
    "invalidation does nothing": ("                if v.iterating { error(\"cannot change what '\" + v.name + \"' points into while a for loop walks it\") }\n                v.moved = true",
                                  "                if v.iterating { error(\"cannot change what '\" + v.name + \"' points into while a for loop walks it\") }"),
    "writes through read-only references": ("        if x.deref && wr != nil && wr.isRef && !wr.refMut {", "        if x.deref && wr != nil && wr.isRef && !wr.refMut && false {"),
    "mut aliases allowed": ("argModes[i] == 2 && argDeeps[j] && ri", "false && argDeeps[j] && ri"),
    "mut calls invalidate nothing": ("            invalidate(argRoots[i], argVias[i], false, argPaths[i])\n            recordFree", "            recordFree"),
    "moving a root keeps its references": ("        invalidate(s, nil, true, \"\")                  // references into it now dangle\n", ""),
    "references can be moved out": ("    if x.borrowed || (s != nil && s.isRef) { error(", "    if false { error("),
    "walked arrays can change": ("                    if xs.iterating { errorAt(", "                    if false { errorAt("),
    "element writes invalidate nothing": ("                } else if x.deref || x.viaHandle {\n                    invalidateOwnedChange(x)\n", "                } else if x.deref || x.viaHandle {\n"),
    "read-only references can be passed as mut": ("            if f.fnRec.pmodes[i] == 2 && !mutableOf(a) {", "            if false {"),
    "everything counts as an ancestor": ("        if p == a { return true }\n        p = p.refParent", "        return true"),
    # handles
    "handle borrows are untracked": ("    if x.mode == \"const\" { return nil }\n    if x.viaHandle { return handleSym }\n", "    if x.mode == \"const\" { return nil }\n    if x.viaHandle { return nil }\n"),
    # acceptance-equivalent: the general field-target check rejects the same programs,
    # so only the suite sees this, by the message text (the fuzzer compares acceptance only)
    "handle assignment across calls allowed": ("    if x.viaHandle {\n        errorAt(\"cannot assign through a handle across a call", "    if false {\n        errorAt(\"cannot assign through a handle across a call"),
    "handle index calls allowed": ("            if viaHandle && userCalls != callsBefore {\n                error(", "            if false {\n                error("),
    "match reads the tag with the generation": ("    opMem(0x8B, r, mem(r, -1, -16, 8), false, false)", "    opMem(0x8B, r, mem(r, -1, -16, 8), true, false)"),
    # effect summaries
    "callees never free globals": ("    if f.addr < 0 || f == curFn || f.freesAll { return true }\n    let freed = &f.fnRec.freed", "    if false { return true }\n    let freed = &f.fnRec.freed"),
    "calls invalidate nothing": ("            invalidate(g, nil, true, \"\")\n            if !all { recordFree(g) }", "            if !all { recordFree(g) }"),
    "frees not recorded": ("    if g.storage != \"global\" { return }\n    let freed = &f.fnRec.freed", "    if true { return }\n    let freed = &f.fnRec.freed"),
    "drop frees nothing": ("        consume(x)\n        rtCall1(RT_FREE, dx, tVoid)\n        return voidItem()", "        consume(x)\n        return voidItem()"),
    "replace keeps the old value in place": ("        let xt = x.typ\n        store(x, v)\n", "        let xt = x.typ\n        releaseItem(x)\n"),
    # compile-time checks
    "use after move allowed": ("                error(\"'\" + name + \"' was moved and has no value here\")\n            }\n        }",
                               "            }\n        }"),
    "branch states not compared": ("    let d = stateDiff(a, b)\n    if d != nil {\n", "    let d = stateDiff(a, b)\n    if d != nil && false {\n"),
    "loop moves unchecked": ("    if !term {\n        let d = stateDiff(entry, snapshot())", "    if false {\n        let d = stateDiff(entry, snapshot())"),
    "moves inside && allowed": ("        if condDepth > 0 {", "        if false {"),
    "fields can be moved out": ("    error(\"cannot move out of a field or element; use take() on an optional one\")\n}",
                                "}"),
    "temporaries can be dropped": ("    if x.mode == \"reg\" && isOwned(x.typ) {\n        error(\"store this value in a variable first: otherwise", "    if false {\n        error(\"store this value in a variable first: otherwise"),
    # aliasing (step 1): changes must reach every root that may be the same object
    "changes reach no other root": ("    return isRefParam(changed) && (isRefParam(other) || other.storage == \"global\")",
                                    "    return false"),
    "changes through parameters miss globals": ("(isRefParam(other) || other.storage == \"global\")", "isRefParam(other)"),
    "aliases lose non-deep references too": ("if !hit && r != nil && r != root && v.refDeep && reaches(root, r)", "if !hit && r != nil && r != root && reaches(root, r)"),
    "global changes reach parameters": ("    if changed == other { return true }\n", "    if changed == other || changed.storage == \"global\" { return true }\n"),
    "calls check only one root": ("argDeeps[j] && ri != nil && rj != nil && reaches(ri, rj)", "argDeeps[j] && ri != nil && rj != nil && ri == rj"),
    "calls ignore depth": ("argModes[i] == 2 && argDeeps[j] && ri", "argModes[i] == 2 && ri"),
    # assigning into a field or element while a call might free it
    "field targets assigned across calls": ("    if xs == nil {\n        // a field or element: the call could free the record or array it is in",
                                            "    if false {\n        // a field or element: the call could free the record or array it is in"),
    # the same for the other forms of a target located before a call (call_targets.obx)
    "compound targets not checked": ("            var y = tailExpr()\n            pinned = savedPin\n            checkTargetAcrossCall(x, xs, r0, callsBefore, line, col)\n",
                                     "            var y = tailExpr()\n            pinned = savedPin\n"),
    "append targets not checked": ("        if name != \"substr\" && held && userCalls != callsBefore {",
                                   "        if name == \"truncate\" && held && userCalls != callsBefore {"),
    "truncate targets not checked": ("        if name != \"substr\" && held && userCalls != callsBefore {",
                                     "        if name == \"append\" && held && userCalls != callsBefore {"),
    "replace field targets not checked": ("        if (x.viaHandle || x.sym == nil) && userCalls != callsBefore {",
                                          "        if x.viaHandle && userCalls != callsBefore {"),
    "index targets not checked": ("            if !wholeLocal && userCalls != callsBefore {", "            if false {"),
    "held arrays ignore mut parameters": ("    return x.sym == nil || r.storage == \"global\" || r.isRef\n", "    return x.sym == nil || r.storage == \"global\"\n"),
    "indexes of mut parameters unchecked": ("            let wholeLocal = x.sym != nil && !(root != nil && (root.storage == \"global\" || root.isRef))",
                                            "            let wholeLocal = x.sym != nil && !(root != nil && root.storage == \"global\")"),
    # a whole local array is freed only by a call that moves it, so an operand holding it across
    # such a call is checked through the touch count (call_targets.obx: "... whose index moves the
    # array", "append whose value moves the array", "replace whose value moves the variable")
    # the root snapshot (heldTouch, heldLost): an index, assignment, append, truncate or replace that
    # holds an array in an owned local is checked for a move of that local, whatever the path to the
    # array (call_targets.obx: "a field's array ...", "an element's array ...", "a grid's inner index ...")
    "index of a field, element or whole local not checked for moves": ("            if root != nil && heldLost(root, touch0) {", "            if root != nil && false {"),
    "indexes checked only on a whole variable (not fields, elements, grids)": ("            if root != nil && heldLost(root, touch0) {", "            if wholeLocal && root != nil && heldLost(root, touch0) {"),
    "append and truncate not checked for moves": ("        if name != \"substr\" && r0 != nil && heldLost(r0, touch0) {", "        if name != \"substr\" && r0 != nil && false {"),
    "replace not checked for moves": ("        if tr != nil && heldLost(tr, touch0) {", "        if tr != nil && false {"),
    "plain assignments through a reference not checked for moves": ("            consume(y)\n            if traw == \"\" && !x.viaHandle && !x.deref { originMove(xs, false) }\n            checkTargetAcrossCall(x, xs, r0, callsBefore, line, col)\n            if x.deref && r0 != nil && heldLost(r0, touch0) {",
                                                                     "            consume(y)\n            if traw == \"\" && !x.viaHandle && !x.deref { originMove(xs, false) }\n            checkTargetAcrossCall(x, xs, r0, callsBefore, line, col)\n            if x.deref && r0 != nil && false {"),
    "compound assignments through a reference not checked for moves": ("            var y = tailExpr()\n            pinned = savedPin\n            checkTargetAcrossCall(x, xs, r0, callsBefore, line, col)\n            if x.deref && r0 != nil && heldLost(r0, touch0) {",
                                                                       "            var y = tailExpr()\n            pinned = savedPin\n            checkTargetAcrossCall(x, xs, r0, callsBefore, line, col)\n            if x.deref && r0 != nil && false {"),
    # the reference local's owner (ownerTouch) and its invalidation (the moved flag): a reference into a
    # field or to an element's array, or a borrow through a handle, is only caught by one of them
    "reference roots not checked for moves or invalidation": ("fn heldLost(root: ?^Sym, t0: int): bool {\n    if root == nil { return false }\n    if root.isRefLocal && root.moved { return true }\n    return ownerTouch(root) != t0\n}",
                                                              "fn heldLost(root: ?^Sym, t0: int): bool {\n    if root == nil { return false }\n    return !root.isRefLocal && touched(root) != t0\n}"),
    "reference invalidation not checked": ("    if root.isRefLocal && root.moved { return true }\n", ""),
    "moves not counted": ("        s.touches = s.touches + 1\n", ""),
    # finer paths (item 3): a change reaches only what lies under its place
    "field changes invalidate siblings": ("(all || v.refDeep && underPath(v.refPath, path))", "(all || v.refDeep)"),
    "known elements treated as one": ("(ri != rj || pathsOverlap(argPaths[i], argPaths[j]))", "(ri != rj || true)"),
    # held places (heldPush, heldMove, heldFree): a replace or take frees what it empties, so a value
    # that replaces an array an index, append or assignment holds is refused (call_targets.obx and
    # held_places.obx: "... replaces or moves what it holds"). A place held through a handle with an
    # origin is an owned place of that local; a handle without one is an identity, hit by frees of the
    # same identity at or below its path and by every free of another identity or of none
    "replace does not free what it holds": ("        consume(v)\n        freeEvent(x, place, raw, orig, ident)\n", "        consume(v)\n"),
    "take does not free what it holds": ("        freeEvent(x, lastPlace, lastRaw, lastOrigin, lastIdent)\n", ""),
    "moves do not reach held handle targets": ("        } else if heldForeign[i] {\n            if s.isParam || !refParamKey(heldKey[i]) { heldHit[i] = true }", "        } else if false {\n            if s.isParam || !refParamKey(heldKey[i]) { heldHit[i] = true }"),
    "moves do not free handle targets": ("        heldMove(s)\n", ""),
    "identity frees do not reach their entries": ("                if heldUnder(place, heldPath[k]) && !(heldStore[k] && place == heldPath[k] && place != anyPlace) { heldHit[k] = true }",
                                                  "                if false { heldHit[k] = true }"),
    "same-identity frees ignore the path": ("                if heldUnder(place, heldPath[k]) && !(heldStore[k] && place == heldPath[k] && place != anyPlace) { heldHit[k] = true }",
                                            "                heldHit[k] = true"),
    "identity exemption removed": ("                if heldUnder(place, heldPath[k]) && !(heldStore[k] && place == heldPath[k] && place != anyPlace) { heldHit[k] = true }",
                                   "                if heldUnder(place, heldPath[k]) { heldHit[k] = true }"),
    "origin ignored when holding": ("        if origin != nil {\n            heldAdd(origin, false, false, raw)", "        if false {\n            heldAdd(origin, false, false, raw)"),
    "origin ignored when freeing": ("        if origin != nil {\n            heldFree(origin, nil, raw)", "        if false {\n            heldFree(origin, nil, raw)"),
    "whole local arrays hit by identity frees": ("        } else if !heldWhole[k] {", "        } else {"),
    "reference parameters take their owner's place": ("    if isRefParam(r) {\n        heldAdd(r, true, false, raw)", "    if false {\n        heldAdd(r, true, false, raw)"),
    "owned frees ignore the path": ("            if heldKey[k] == o && heldUnder(place, heldPath[k]) && !(heldStore[k] && place == heldPath[k] && place != anyPlace) {",
                                    "            if heldKey[k] == o && !(heldStore[k] && place == heldPath[k]) {"),
    "owned assignment exemption removed": ("            if heldKey[k] == o && heldUnder(place, heldPath[k]) && !(heldStore[k] && place == heldPath[k] && place != anyPlace) {",
                                           "            if heldKey[k] == o && heldUnder(place, heldPath[k]) {"),
    "store exemption not applied through handles": ("    for k in mark..len(heldKey) { heldStore[k] = true }", "    for k in mark..len(heldKey) { heldStore[k] = !viaHandle }"),
    "index does not hold its array": ("            let hp = heldPush(root, viaHandle, hOrig, hIdent, placePath(root, path), path)", "            let hp = -1"),
    "replace does not hold its place": ("        let hp = heldPush(tr, x.viaHandle, orig, ident, place, raw)", "        let hp = -1"),
    "append and truncate do not hold their array": ("        let hp = heldPush(r0, aVia, aOrig, aIdent, aPlace, aRaw)", "        let hp = -1"),
    "plain assignments do not hold their target": ("            if x.deref || x.viaHandle { hp = heldPushStore(r0, x.viaHandle, torig, tident, tplace, traw) }\n            let y = tailExpr()",
                                                    "            let y = tailExpr()"),
    "compound assignments do not hold their target": ("            if x.deref || x.viaHandle { hp = heldPushStore(r0, x.viaHandle, torig, tident, tplace, traw) }\n            var y = tailExpr()",
                                                      "            var y = tailExpr()"),
    "reference places ignore the binding's path": ("            let p = heldAt(b, i, raw, r)\n            if o == handleSym {\n                heldAdd(nil, true, false, \"\")", "            let p = raw\n            if o == handleSym {\n                heldAdd(nil, true, false, \"\")"),
}


# the mutants whose patterns the reference bindings per path replaced: retargeted to the same behaviour
RETARGET = {
    "writes through read-only references": ("if x.deref && wr != nil && wr.isRef && !wr.bind.writable {",
                                            "if x.deref && wr != nil && wr.isRef && !wr.bind.writable && false {"),
    "mut aliases allowed": ("if i != j && argModes[i] == 2 {", "if i != j && argModes[i] == 2 && false {"),
    "mut calls invalidate nothing": ("                invalidate(b.roots[p], argVias[i], false, b.paths[p])\n", ""),
    "read-only references can be passed as mut": ("            if f.fnRec.pmodes[i] == 2 && !b.writable {", "            if false {"),
    "everything counts as an ancestor": ("        if p == a { return true }\n        p = p.bind.parent",
                                         "        if p == a || true { return true }\n        p = p.bind.parent"),
    "handle borrows are untracked": ("    if x.viaHandle { return singleBind(handleSym, deepOf(x), x.path, mutableOf(x), parentOf(x)) }\n",
                                     "    if x.viaHandle { return noBind }\n"),
    "loop moves unchecked": ("    if !term {\n        let now = snapshot()\n        loopMoveError(stateDiff(&entry, &now), line, col)",
                             "    if false {\n        let now = snapshot()\n        loopMoveError(stateDiff(&entry, &now), line, col)"),
    "aliases lose non-deep references too": ("if r != root && b.deeps[i] && reaches(root, r) { return true }",
                                             "if r != root && reaches(root, r) { return true }"),
    "calls check only one root": ("if bj.deeps[q] && reaches(ri, rj) && (ri != rj || pathsOverlap(bi.paths[p], bj.paths[q])) {",
                                  "if bj.deeps[q] && reaches(ri, rj) && ri == rj {"),
    "calls ignore depth": ("if bj.deeps[q] && reaches(ri, rj) && (ri != rj || pathsOverlap(bi.paths[p], bj.paths[q])) {",
                           "if reaches(ri, rj) && (ri != rj || pathsOverlap(bi.paths[p], bj.paths[q])) {"),
    "field changes invalidate siblings": ("(all || b.deeps[i] && underPath(b.paths[i], path))", "(all || b.deeps[i])"),
    "known elements treated as one": ("(ri != rj || pathsOverlap(bi.paths[p], bj.paths[q]))", "(ri != rj || true)"),
}
MUTANTS.update(RETARGET)
PORT_PATTERNS.update({k: v for k, v in RETARGET.items() if k in PORT_PATTERNS})

# the loop check and the defer rule: the marks that frees leave on the references they may reach, the reads
# that note them, the writable trigger, the refusal of a re-point in a defer body, and the loop start, which
# must keep the marks its condition leaves (the condition frees before the body reads)
MUTANTS.update({
    "frees do not mark carried references": ("        if ownedLocals[i].isRefLocal && !ownedLocals[i].fresh {\n            ownedLocals[i].carryFreed = true\n        }\n",
                                             ""),
    "a read of a marked reference is not noted": ("            if s.carryFreed { s.freedUse = useSeq }\n", ""),
    "the writable trigger dropped": ("            if e.writable && !b.writable && s.writeUse > useBefore {\n", "            if e.writable && !b.writable {\n"),
    "a loop start keeps the marks from before it": ("        saved[i] = ownedLocals[i].carryFreed\n        ownedLocals[i].carryFreed = false\n",
                                                   "        saved[i] = ownedLocals[i].carryFreed\n"),
    "a loop end does not join the marks from before it": ("        if saved[i] { ownedLocals[i].carryFreed = true }\n", ""),
    "the loop check ignores the overlap": ("            if hit[i] && (s.freedUse > useBefore || back.flags[3 * n + i]) {\n", "            if (s.freedUse > useBefore || back.flags[3 * n + i]) {\n"),
    "a loop exit ignores the frees on its way": ("            for k in logFrom..at {\n", "            for k in logFrom..logFrom {\n"),
    "a deferred body's frees are not replayed": ("        replayFrees(bi, i)\n", ""),
    "a deferred body's moves are kept where it is declared": ("        if ownedLocals[i].isRefLocal { ownedLocals[i].moved = before.flags[i] }\n", ""),
    "a defer body's re-point is not refused": ("            if deferMark >= 0 && declaredBefore(xs, deferMark) {", "            if false {"),
    "a re-point keeps the carried mark": ("            xs.carryFreed = false\n", ""),
    "a loop start forgets the carried marks": ("    for s in locals { s.fresh = false }\n", "    for s in locals { s.fresh = false; s.carryFreed = false }\n"),
    # a write through a mut argument, or through an append, is a write for the writable rule (review15 w_mutarg, w_append)
    "a mut argument is not a write": ("            if f.fnRec.pmodes[i] == 2 { noteWrite(a) }\n", ""),
    "an append is not a write": ("    if name == \"append\" || name == \"truncate\" { noteWrite(a) }\n", "    if name == \"truncate\" { noteWrite(a) }\n"),
    # a deferred change is replayed as it was made: through the reference it was made through (review15 FP_defer_via_child_*)
    "a deferred change is replayed without its reference": ("            invalidate(rt, rv, r.all, r.path)\n", "            invalidate(rt, nil, r.all, r.path)\n"),
    "a deferred deep change is replayed without its reference": ("            invalidateDeep(rv)\n", "            invalidateDeep(nil)\n"),
    "a deferred change is not replayed": ("            invalidate(rt, rv, r.all, r.path)\n", "            if false { invalidate(rt, rv, r.all, r.path) }\n"),
    "a logged free ignores the reference it is made through": ("    if ancestorOf(s, rv) { return false }\n", ""),
})

# the uses of a defer body (see checkDeferUses): the re-point check, the invalidation check, one exit kind
# at a time that skips the check, the uses not recorded, the uses of an inner defer not passed to the
# enclosing one, the frees of a newer defer applied after the older one's check, and the suspect check
def unchecked_exit(down):
    """the exit code of runDefers, with the defers' uses not checked"""
    return (f"            var mTop = len(ownedLocals)\n            var mI = len(blocks) - 1\n"
            f"            while mI >= {down} {{\n"
            "                var mD = len(blocks[mI].defers) - 1\n"
            "                while mD >= 0 {\n"
            "                    callLocal(blocks[mI].defers[mD])\n"
            "                    replayFrees(mI, mD)\n"
            "                    mD -= 1\n"
            "                }\n"
            "                freeOwned(blocks[mI].omark, mTop)\n"
            "                mTop = blocks[mI].omark\n"
            "                mI -= 1\n"
            "            }\n")


DEFER_USE_MUTANTS = {
    "a defer's reference pointed elsewhere is not refused": ("        if !withinDeclared(blocks[bi].useBinds[k], s.bind) {", "        if false {"),
    "a defer's reference may point beyond its declared targets": ("    for j in 0..len(e.roots) {\n        var covered = false\n        for i in 0..len(d.roots) {", "    for j in 0..0 {\n        var covered = false\n        for i in 0..len(d.roots) {"),
    "a defer's check covers the uses of the newer defers": ("    var hi = len(blocks[bi].uses)\n    if d + 1 < len(blocks[bi].useFrom) { hi = blocks[bi].useFrom[d + 1] }\n    for k in blocks[bi].useFrom[d]..hi {",
                                                           "    var hi = len(blocks[bi].uses)\n    for k in blocks[bi].useFrom[d]..hi {"),
    "an inner defer's uses of the body's own locals are not guarded": ("            if i < len(deferUses) { deferUses[i] = true }\n", "            deferUses[i] = true\n"),
    "a defer's invalidated reference is not refused": ("        if s.moved {\n            error(\"'\" + s.name + \"' is used by a defer", "        if false {\n            error(\"'\" + s.name + \"' is used by a defer"),
    "a defer's suspect reference is not reported": ("        if s.suspect {\n            notePending(tline, tcol, \"'\" + s.name + \"' is used by a defer", "        if false {\n            notePending(tline, tcol, \"'\" + s.name + \"' is used by a defer"),
    "defer uses not checked at the block's end": ("        callDefers(last)\n        freeOwned(om, len(ownedLocals))\n",
                                                  "        var mI = len(blocks[last].defers) - 1\n        while mI >= 0 {\n            callLocal(blocks[last].defers[mI])\n            replayFrees(last, mI)\n            mI -= 1\n        }\n        freeOwned(om, len(ownedLocals))\n"),
    # a break or continue leaves to a block above the function's own (a return runs the defers down to fnBlock)
    "defer uses not checked at a break or continue": ("    while i >= downTo {\n        callDefers(i)\n",
                                                      "    while i >= downTo {\n        if downTo == fnBlock { callDefers(i) } else {\n            var mD = len(blocks[i].defers) - 1\n            while mD >= 0 {\n                callLocal(blocks[i].defers[mD])\n                replayFrees(i, mD)\n                mD -= 1\n            }\n        }\n"),
    "defer uses not checked at a return without a value": ("        if sym != T_SEMI && sym != T_RBRACE { error(f.name + \" does not return a value\") }\n        runDefers(fnBlock)\n",
                                                           "        if sym != T_SEMI && sym != T_RBRACE { error(f.name + \" does not return a value\") }\n" + unchecked_exit("fnBlock")),
    "defer uses not checked at a return with a value": ("            release(x.r)\n            runDefers(fnBlock)\n",
                                                        "            release(x.r)\n" + unchecked_exit("fnBlock")),
    "a defer's uses are not recorded": ("        if inDefer && s.isRefLocal { noteDeferUse(s) }\n", ""),
    "an inner defer's uses are not passed outwards": ("            if i < len(deferUses) { deferUses[i] = true }\n", ""),
    "a newer defer's frees are applied after the older one's check": ('        replayFrees(bi, i)\n        // the body\'s assignments and drops of owners happen here, not where the defer is declared\n        replayFree = true\n        for j in 0..len(dfOwner) {\n            if dfBlock[j] == bi && dfIndex[j] == i {\n                let ev = dfEv[j]\n                originFreeAt(dfOwner[j], substr(ev, 1, len(ev)), ev[0] == \'!\', "assign over")\n            }\n        }\n        replayFree = false\n        i -= 1\n    }\n}',
        '        // the body\'s assignments and drops of owners happen here, not where the defer is declared\n        replayFree = true\n        for j in 0..len(dfOwner) {\n            if dfBlock[j] == bi && dfIndex[j] == i {\n                let ev = dfEv[j]\n                originFreeAt(dfOwner[j], substr(ev, 1, len(ev)), ev[0] == \'!\', "assign over")\n            }\n        }\n        replayFree = false\n        i -= 1\n    }\n    for q in 0..len(blocks[bi].defers) { replayFrees(bi, q) }\n}'),
}
MUTANTS.update(DEFER_USE_MUTANTS)

# a reference invalid on one path only (the join keeps the flag; the use is refused, see LANGUAGE.md section 4):
# the join must keep it at if, match and loop joins, the back edge and the loop's exits must carry it, the
# re-point must clear it, a use must be refused at the use (read, write, carried to the top), and the
# message must be the reference's
ONE_PATH_MUTANTS = {
    "a join drops a reference invalidated on one path": ("        if st.flags[i] && s.isRefLocal { s.moved = true }\n", ""),
    "a match join drops a reference invalidated on one path": ("        if a.flags[i] && ownedLocals[i].isRefLocal { out.flags[i] = true }\n", ""),
    "a loop's back edge drops a reference invalidated on the way round": ("        if st.flags[i] && ownedLocals[i].isRefLocal { acc.flags[i] = true }\n", ""),
    "a join of invalidations is an error again": ("        if i < n && a.flags[i] != b.flags[i] && !ownedLocals[i].isRefLocal { return ownedLocals[i] }",
                                                  "        if i < n && a.flags[i] != b.flags[i] { return ownedLocals[i] }"),
    "the top of a loop does not check a carried invalidation": ("    loopCarryCheck(&back, useBefore, lv)\n", ""),
    "the top of a loop checks a reference that is not read": ("        if s.isRefLocal && s != lv && back.flags[i] && s.staleUse > useBefore {",
                                                              "        if s.isRefLocal && s != lv && back.flags[i] {"),
    "a carried invalidation is reported at the wrong place": ("            errorAt(freedRefMsg(s.name), s.staleLine, s.staleCol)", "            errorAt(freedRefMsg(s.name), 0, 0)"),
    "the top of a loop reports a for-each variable pointed afresh": ("        if s.isRefLocal && s != lv && back.flags[i] && s.staleUse > useBefore {",
                                                                    "        if s.isRefLocal && back.flags[i] && s.staleUse > useBefore {"),
    "a loop's exit drops a carried invalidation": ("        if back.flags[i] && ownedLocals[i].isRefLocal { ownedLocals[i].moved = true }\n", ""),
    "a read of an invalidated reference is not refused": ("            } else if s.isRefLocal {\n                error(freedRefMsg(name))\n",
                                                          "            } else if false {\n                error(freedRefMsg(name))\n"),
    "a write through an invalidated reference gets the owned message": ("            if s.isRefLocal { error(freedRefMsg(name)) }\n", ""),
    "a re-point after the join keeps the invalidation": ("            xs.suspect = false\n            xs.moved = false\n", "            xs.suspect = false\n"),
}
MUTANTS.update(ONE_PATH_MUTANTS)
ORIGIN_MUTANTS = {
    'origin not cleared by a consume': ('        originMove(s, true)\n        s.moved = true\n',
        '        s.moved = true\n'),
    'origin not cleared by a replace of a whole local': ('        if raw == "" && !x.viaHandle && !x.deref { originMove(x.sym, true) }\n',
        ''),
    'origin not cleared by a take of a whole local': ('        if lastRaw == "" && !x.viaHandle && !x.deref { originMove(x.sym, true) }\n',
        ''),
    'origin not cleared by an assignment over a whole local': ('            if traw == "" && !x.viaHandle && !x.deref { originMove(xs, false) }\n',
        ''),
    'replace clears origins through a reference or handle': ('        if raw == "" && !x.viaHandle && !x.deref { originMove(x.sym, true) }\n',
        '        if raw == "" { originMove(x.sym, true) }\n'),
    'take clears origins through a reference or handle': ('        if lastRaw == "" && !x.viaHandle && !x.deref { originMove(x.sym, true) }\n',
        '        if lastRaw == "" { originMove(x.sym, true) }\n'),
    'assignment clears origins through a reference or handle': ('            if traw == "" && !x.viaHandle && !x.deref { originMove(xs, false) }\n',
        '            if traw == "" { originMove(xs, false) }\n'),
    "a move clears no handle's origin": ('        if x.hOrigin == r { x.hOrigin = nil }\n',
        ''),
    "a closed block's handles stay in the origin list": ('    truncate(originSyms, blocks[last].hmark)\n',
        ''),
    'a handle declared in the loop it is moved in is refused': ('        if originLive[i] == s && x.hDepth <= len(loops) && originDead[i] == nil {\n',
        '        if originLive[i] == s && x.hDepth <= len(loops) + 1 {\n'),
    'the loop check ignores where a handle was declared': ('        if originLive[i] == s && x.hDepth <= len(loops) && originDead[i] == nil {\n',
        '        if originLive[i] == s {\n'),
    "a let handle's loop depth is not recorded": ('                    s.hDepth = len(loops) + 1\n',
        ''),
    'moves do not check the loop': ('    if move { loopOriginPend(r) }\n',
        ''),
    'a var handle is given its origin': ('            if kind == "let" {\n                s.hOrigin = x.hOrigin\n',
        '            if true {\n                s.hOrigin = x.hOrigin\n'),
    'a temporary handle is given no origin': ('        made.hOrigin = origin\n',
        '        made.hOrigin = nil\n'),
}
MUTANTS.update(ORIGIN_MUTANTS)

# argument order: a reference argument is held while the later arguments run (holdArg, argOrderCheck,
# releaseArgs); errors/arg_order.obx and run/arg_order.obx show them
ARG_ORDER_MUTANTS = {
    'a reference argument is not held': ('            holdArg(b, i)\n', ''),
    'no check after a reference argument': ('            argOrderCheck(tbase, f.name, i, argLine, argCol)\n            holdArg(b, i)\n',
        '            holdArg(b, i)\n'),
    'no check after a by-value argument': ('            consume(a)\n            argOrderCheck(tbase, f.name, i, argLine, argCol)\n',
        '            consume(a)\n'),
    'the holder pool never grows': ('    if argTempsUsed == len(argTemps) {\n', '    if argTempsUsed < 0 {\n'),
    'an argument holder is not a reference local': ('        s.isRefLocal = true\n        append(argTemps, s)\n',
        '        append(argTemps, s)\n'),
    'an argument holder keeps its old binding': ('    t.bind = b\n    t.suspect = false\n', '    t.suspect = false\n'),
    'an argument holder keeps its old suspicion': ('    t.suspect = false\n    t.value = i\n', '    t.value = i\n'),
    'an argument holder keeps its old index': ('    t.value = i\n    append(ownedLocals, t)\n', '    append(ownedLocals, t)\n'),
    'a freed argument is not refused': ('        if t.moved {\n            errorAt("argument "', '        if false {\n            errorAt("argument "'),
    'a suspect argument is not noted': ('        if t.suspect {\n            notePending(line, col, "argument "',
        '        if false {\n            notePending(line, col, "argument "'),
    'the check reaches the holders of enclosing calls': ('    for k in tbase..argTempsUsed {\n        let t = argTemps[k]\n',
        '    for k in 0..argTempsUsed {\n        let t = argTemps[k]\n'),
    'only the newest holder is refused': ('        if t.moved {\n            errorAt("argument "',
        '        if t.moved && k == argTempsUsed - 1 {\n            errorAt("argument "'),
    'argument holders are not reused': ('    argTempsUsed = tbase\n    truncate(ownedLocals, omark)\n', '    truncate(ownedLocals, omark)\n'),
    'argument holders stay live after the call': ('    argTempsUsed = tbase\n    truncate(ownedLocals, omark)\n', '    argTempsUsed = tbase\n'),
    "a call's holders are not released": ('    releaseArgs(tbase, omark)\n    if sym != T_RPAREN', '    if sym != T_RPAREN'),
    "a function's argument holders outlive it": ('    truncate(bindArena, bindMark)\n    truncate(argTemps, tempMark)\n', '    truncate(bindArena, bindMark)\n'),
    "writeFile's bytes are not held": ('        holdArg(itemBind(d), 1)\n', ''),
    "writeFile's bytes are held as another argument": ('        holdArg(itemBind(d), 1)\n', '        holdArg(itemBind(d), 0)\n'),
    "writeFile's flag is not checked": ('        argOrderCheck(tbase, name, 2, eLine, eCol)\n', ''),
    "writeFile's holder is not released": ('        argOrderCheck(tbase, name, 2, eLine, eCol)\n        releaseArgs(tbase, omark)\n',
        '        argOrderCheck(tbase, name, 2, eLine, eCol)\n'),
}
MUTANTS.update(ARG_ORDER_MUTANTS)

# an assignment over, or a drop of, the owner a let handle was made from frees the handle's object
# (originFree, deadHandleCheck, the dead marks of armBlock); errors/handle_frees.obx and
# run/handle_frees.obx show them
HANDLE_FREE_MUTANTS = {
    'an assignment frees no handle': ('        originFree(r, "assign over")\n        return\n', '        return\n'),
    'a drop frees no handle': ('        originFree(ds, "drop")\n', ''),
    'a free does not mark the handle dead': ('            if x.hDepth <= len(loops) { pendPush(x, r, what) }\n            originDead[i] = r\n', '            if x.hDepth <= len(loops) { pendPush(x, r, what) }\n'),
    'an assignment clears the origins of the handles': ('        originFree(r, "assign over")\n        return\n', '        originFree(r, "assign over")\n'),
    'the loop move rule refuses a dead handle': ('x.hDepth <= len(loops) && originDead[i] == nil {\n', 'x.hDepth <= len(loops) {\n'),
    "a defer's frees are not replayed where its block is left": ('            if dfBlock[j] == bi && dfIndex[j] == i {\n                let ev = dfEv[j]\n                originFreeAt(dfOwner[j], substr(ev, 1, len(ev)), ev[0] == \'!\', "assign over")\n            }\n', ''),
    'a replayed free is checked against the defers again': ('        if replayFree { break }\n', ''),
    "a defer's frees are not recorded": ('    if inDefer {\n        append(deferFrees, r)\n        if strict { append(deferFreeEv, "!" + path) } else { append(deferFreeEv, "=" + path) }\n    }\n', ''),
    "a closed block's defer frees stay": ('    while len(dfBlock) > 0 && dfBlock[len(dfBlock) - 1] >= last {\n', '    while false {\n'),
    'a let copy of a dead handle is alive': ('                    append(originDead, dead)\n', '                    append(originDead, nil)\n'),
    'a free in a loop is not reported': ('            if x.hDepth <= len(loops) { pendPush(x, r, what) }\n            originDead[i] = r\n', '            originDead[i] = r\n'),
    'a free in a loop is refused without a use there': ('            if x.usedAt > loops[li].useFrom {\n', '            if true {\n'),
    'a free is checked at the end of the innermost loop': ('        if pendDepth[k] == li {\n', '        if pendDepth[k] <= li {\n'),
    'a use through a dead handle is not refused': ('                deadHandleCheck(x.sym)\n', ''),
    'a dead mark refuses every handle': ('        if o != nil && originSyms[i] == s {\n', '        if o != nil {\n'),
    "a handle's naming is not recorded": ('            lastNamedPrev = s.usedAt\n            s.usedAt = useSeq\n', '            lastNamedPrev = s.usedAt\n'),
    "a handle's naming is not recorded once a move clears its origin": ('        if s.hDepth > 0 {\n            useSeq += 1', '        if s.hOrigin != nil {\n            useSeq += 1'),
    'a loop does not record where its reads begin': ('logFrom: logFrom, useFrom: useBefore, exitDead', 'logFrom: logFrom, useFrom: 0, exitDead'),
    'a let handle gets no dead mark': ('                    append(originDead, dead)\n', ''),
    "a closed block's dead marks stay": ('    truncate(originDead, blocks[last].hmark)\n', ''),
    'an arm starts from the marks of the arm before': ('    deadSet(d0)\n    var term = false\n', '    var term = false\n'),
    "an arm's marks are not joined": ('        if originDead[i] != nil { acc[i] = originDead[i] }\n', ''),
    'an arm that ends keeps its marks for what follows': ('        return term\n    }\n    let m = len(acc) / 2', '    }\n    let m = len(acc) / 2'),
    "a break arm's marks are not kept for after the loop": ('        if len(loops) > 0 && jumpsHere() != js {\n', '        if false {\n'),
    "a returning arm's marks are kept for after the loop": ('        if len(loops) > 0 && jumpsHere() != js {\n', '        if len(loops) > 0 {\n'),
    'a break is not counted': ('            if len(loops) > 0 { addJump() }\n', ''),
    "a loop's exit marks are not applied after it": ('        if i < len(originDead) && loops[li].exitDead[i] != nil { originDead[i] = loops[li].exitDead[i] }\n', ''),
    "a defer's handles are not recorded": ('        if originSyms[i].usedAt > useAt {\n', '        if false {\n'),
    'a defer records every handle': ('        if originSyms[i].usedAt > useAt {\n', '        if true {\n'),
    "a free is refused by a defer using another owner's handle": ('        if live {\n            error("cannot " + what + " \'" + r.name + "\' while a defer', '        if true {\n            error("cannot " + what + " \'" + r.name + "\' while a defer'),
    "a closed block's defer handles stay": ('    while len(deferAt) > 0 && deferAt[len(deferAt) - 1] >= last {\n', '    while false {\n'),
    'the then branch is not an arm': ('    let t1 = armBlock(&d0, &dacc, false)\n', '    let t1 = block()\n'),
    'the else branch is not an arm': ('        let t2 = armBlock(&e0, &dacc, sym == T_IF)\n',
        '        var t2 = false\n        if sym == T_IF { t2 = ifStat() } else { t2 = block() }\n'),
    'an if with an else does not take the join': ('        deadSet(&dacc)\n        truncate(narrowStack, mark)\n', '        truncate(narrowStack, mark)\n'),
    'an if without an else does not take the join': ('    deadSet(&dacc)\n    if !t1 {\n', '    if !t1 {\n'),
    'an int match arm is not an arm': ('        fixHere(toBody)\n        restore(s0)\n        let bt = armBlock(&d0, &dacc, false)\n',
        '        fixHere(toBody)\n        restore(s0)\n        let bt = block()\n'),
    "an int match's else is not an arm": ('    let seen = new []int(0)\n    var ends = 0\n    var allTerm = true\n    var hasElse = false\n    while sym != T_RBRACE {\n        if sym == T_ELSE {\n            next()\n            hasElse = true\n            restore(s0)\n            let bt = armBlock(&d0, &dacc, false)\n',
        '    let seen = new []int(0)\n    var ends = 0\n    var allTerm = true\n    var hasElse = false\n    while sym != T_RBRACE {\n        if sym == T_ELSE {\n            next()\n            hasElse = true\n            restore(s0)\n            let bt = block()\n'),
    'an int match does not take the join': ('    deadSet(&dacc)\n    fixHere(ends)\n    return allTerm && hasElse\n', '    fixHere(ends)\n    return allTerm && hasElse\n'),
    'a match arm is not an arm': ('            bt = armBlock(&d0, &dacc, false)\n', '            bt = block()\n'),
    "a match's else is not an arm": ('    let seen = new []^Type(0)\n    var ends = 0\n    var allTerm = true\n    var hasElse = false\n    while sym != T_RBRACE {\n        if sym == T_ELSE {\n            if t.kind == "result" { errorAt("a Result match names the ok and err arms; \'else\' is not needed", tline, tcol) }\n            next()\n            hasElse = true\n            restore(s0)\n            let bt = armBlock(&d0, &dacc, false)\n',
        '    let seen = new []^Type(0)\n    var ends = 0\n    var allTerm = true\n    var hasElse = false\n    while sym != T_RBRACE {\n        if sym == T_ELSE {\n            if t.kind == "result" { errorAt("a Result match names the ok and err arms; \'else\' is not needed", tline, tcol) }\n            next()\n            hasElse = true\n            restore(s0)\n            let bt = block()\n'),
    'a match does not take the join': ('    deadSet(&dacc)\n    fixHere(ends)\n    return allTerm\n}', '    fixHere(ends)\n    return allTerm\n}'),
    "a defer body's frees mark handles where it is declared": ('    deadSet(&deadBefore)\n', ''),
}
MUTANTS.update(HANDLE_FREE_MUTANTS)


# the second review's gaps: origins, the joins over several roots, the chained handles, and the
# callee-local rule. An entry marked equivalent in FORK.md is expected to survive: the guard it removes
# is redundant (see there)
MUTANTS.update({
    "origin needs no non-handle designator": ("if !x.viaHandle && lastRaw == \"\" { origin = wholeOwnerOf(x.sym) }",
                                             "if lastRaw == \"\" { origin = wholeOwnerOf(x.sym) }"),
    "origin needs no whole path": ("if !x.viaHandle && lastRaw == \"\" { origin = wholeOwnerOf(x.sym) }",
                                  "if !x.viaHandle { origin = wholeOwnerOf(x.sym) }"),
    "origin of a global is kept": ("    if r.owned && !r.isRef && r.storage == \"local\" { return r }",
                                   "    if r.owned && !r.isRef { return r }"),
    "chained handles keep the first origin": ("            } else {\n                hOrig = nil\n                hIdent = nil\n            }",
                                              "            } else {\n            }"),
    "a handle root of a reference is not an identity (held)": ("            if o == handleSym {\n                heldAdd(nil, true, false, \"\")",
                                                              "            if false {\n                heldAdd(nil, true, false, \"\")"),
    "a handle root of a reference is not an identity (freed)": ("            if o == handleSym {\n                heldFree(nil, nil, \"\")",
                                                               "            if false {\n                heldFree(nil, nil, \"\")"),
    "reference roots are owners (held)": ("            } else if o.isRef {\n                heldAdd(o, true, false, p)",
                                          "            } else if false {\n                heldAdd(o, true, false, p)"),
    "reference roots are owners (freed)": ("            } else if o.isRef {\n                heldFree(nil, o, p)",
                                           "            } else if false {\n                heldFree(nil, o, p)"),
    "every held place is whole": ("    heldAdd(r, false, raw == \"\", raw)", "    heldAdd(r, false, true, raw)"),
    "unknown frees hit only whole arrays": ("        } else if !heldWhole[k] {", "        } else if heldWhole[k] {"),
    "moves reach reference parameters from callee locals": ("            if s.isParam || !refParamKey(heldKey[i]) { heldHit[i] = true }",
                                                           "            heldHit[i] = true"),
    "frees reach reference parameters from callee locals": ("            } else if !(calleeLocal(o) && refParamKey(heldKey[k])) {",
                                                           "            } else {"),
    "a by-value parameter counts as a callee local": ("    return r.storage == \"local\" && !r.isParam", "    return r.storage == \"local\""),
})


# the false-positive fixes (FORK.md, "Handle-owner false positives" and "Reference false positives"): each
# mutant undoes a fix in the wrong direction, so that an unsafe neighbour is accepted, or undoes a fix
# (so that the accepted shape is refused again). errors/fp_loops.obx and errors/fp_refs.obx hold the
# neighbours; run/fp_loops.obx and run/fp_refs.obx the accepted shapes
FP_MUTANTS = {
    # alive is no use of a handle
    'alive hides the earlier uses of the handle': ('        h.usedAt = lastNamedPrev\n        lastNamed = nil\n', '        h.usedAt = 0\n        lastNamed = nil\n'),
    'alive of any expression is no use': ('        inAlive = bare\n', '        inAlive = true\n'),
    'alive counts as a use': ('        unname(h.sym)\n', ''),
    'a copy of a handle not known to be dead is no use': ('                    if dead != nil { unname(x.sym) }\n', '                    unname(x.sym)\n'),
    'a copy of a dead handle is a use': ('                    if dead != nil { unname(x.sym) }\n', ''),
    # a free that ends its block in a break or a return (pendBlockEnds)
    'a block that ends drops its frees whatever follows': ('    if term { pendBlockEnds(pm, cs, bs) }\n    expect(T_RBRACE, "")\n    closeBlock(term)\n    return term\n', '    pendBlockEnds(pm, cs, bs)\n    expect(T_RBRACE, "")\n    closeBlock(term)\n    return term\n'),
    'a block with a continue in it drops its frees': ('    if contSeq != cs { return }\n', ''),
    'a break drops the frees of the loops outside the one it leaves': ('            if brkSeq != bs && pendDepth[k] == len(loops) - 1 { drop = true }\n', '            if brkSeq != bs { drop = true }\n'),
    'a block drops the frees made in the loops inside it': ('        if pendNest[k] == len(loops) && pendWhat[k] != "move" {\n', '        if pendWhat[k] != "move" {\n'),
    'a block drops the moves it made': ('        if pendNest[k] == len(loops) && pendWhat[k] != "move" {\n', '        if pendNest[k] == len(loops) {\n'),
    'a free before a break is never dropped': ('            if brkSeq != bs && pendDepth[k] == len(loops) - 1 { drop = true }\n', ''),
    'a free before a return is never dropped': ('            if brkSeq == bs { drop = true }\n', ''),
    # a move of the owner in a loop that does not name the handle (loopOriginPend, pendingFreeCheck, movedLoop)
    'a move in a loop is not reported': ('            pendPush(x, s, "move")\n', ''),
    'a moved owner leaves its handle free to be named': ('            if pendWhat[k] == "move" { x.movedLoop = true }\n', ''),
    'naming a handle after a loop moved its owner is allowed': ('            if s.movedLoop && !inAlive {\n', '            if false {\n'),
    'naming a moved handle in alive is refused': ('            if s.movedLoop && !inAlive {\n', '            if s.movedLoop {\n'),
    'a move in a loop is refused without a use there': ('            if x.usedAt > loops[li].useFrom {\n', '            if x.usedAt > loops[li].useFrom || pendWhat[k] == "move" {\n'),
    # the marks of an arm that leaves the loop (jumpsHere, addJump)
    'a continue is not counted as leaving the loop': ('            if len(loops) > 0 { addJump() }\n', '            if len(loops) > 0 && k == T_BREAK { addJump() }\n'),
    'the jumps of inner loops are counted as leaving the outer one': ('    depthJumps[len(loops) - 1] += 1\n}', '    for q in 0..len(depthJumps) { depthJumps[q] += 1 }\n}'),
    # a loop's recursive call reads a reference that was pointed afresh first
    'the walk of a reference is not a read of it': ('            walked.usedAt = useSeq\n', ''),
    'a read of a reference pointed afresh counts for the loop': ('            s.staleUse = useSeq\n            s.usedAt = useSeq\n', '            s.staleUse = useSeq\n'),
    # a mut argument's callee (FnRec.pfree, mutFrees)
    'a mut callee is assumed to free nothing': ('    return (f.fnRec.pfree >> i) & 1 == 1\n', '    return false\n'),
    'frees through a parameter are not recorded': ('        f.fnRec.pfree = f.fnRec.pfree | (1 << pi)\n', ''),
    'a callee with a forward heading is assumed to free nothing': ('    if f.addr < 0 || f == curFn || f.freesAll || f.writesHandles { return true }\n', '    if f == curFn || f.freesAll || f.writesHandles { return true }\n'),
    'a recursive callee is assumed to free nothing': ('    if f.addr < 0 || f == curFn || f.freesAll || f.writesHandles { return true }\n', '    if f.addr < 0 || f.freesAll || f.writesHandles { return true }\n'),
    'a callee that writes through a handle is assumed to free nothing': ('    if f.addr < 0 || f == curFn || f.freesAll || f.writesHandles { return true }\n', '    if f.addr < 0 || f == curFn { return true }\n'),
    'a parameter is recorded by the wrong place': ('    let pi = len(f.fnRec.params) - 1 - (g.offset - 16) / 8', '    let pi = (g.offset - 16) / 8'),
    'a mut argument passed on is not recorded as freed': ('                if !(br.isParam && br.isRef) { recordFree(br) }\n', '                if false { recordFree(br) }\n'),
    'the frees of a reference parameter are recorded as a global': ('    if g.isParam && g.isRef {', '    if g.isParam && g.isRef && false {'),
    # pure calls with mut parameters (pureFn, movesFn)
    'a callee with a mut parameter is never pure': ('        if f.fnRec.pmodes[i] == 2 && mutFrees(f, i) { return false }\n', '        if f.fnRec.pmodes[i] == 2 { return false }\n'),
    'a callee that frees below a mut argument is pure': ('        if f.fnRec.pmodes[i] == 2 && mutFrees(f, i) { return false }\n', ''),
    'a callee that frees globals is pure': ('    if f.addr < 0 || f == curFn || f.freesAll || len(f.fnRec.freed) > 0 { return false }\n', '    if f.addr < 0 || f == curFn || f.freesAll { return false }\n'),
    'a callee that frees anything is pure': ('    if f.addr < 0 || f == curFn || f.freesAll || len(f.fnRec.freed) > 0 { return false }\n', '    if f.addr < 0 || f == curFn || len(f.fnRec.freed) > 0 { return false }\n'),
    'a callee with a forward heading is pure': ('    if f.addr < 0 || f == curFn || f.freesAll || len(f.fnRec.freed) > 0 { return false }\n', '    if f == curFn || f.freesAll || len(f.fnRec.freed) > 0 { return false }\n'),
    'only the first mut parameter is checked for frees': ('    for i in 0..len(f.fnRec.pmodes) {\n        if f.fnRec.pmodes[i] == 2 && mutFrees(f, i) { return false }', '    for i in 0..1 {\n        if i < len(f.fnRec.pmodes) && f.fnRec.pmodes[i] == 2 && mutFrees(f, i) { return false }'),
    # a reference only borrows: replace and take refuse it
    'a replace of a reference local is allowed': ('        if rs != nil && rs.isRefLocal {\n            error("replace cannot change', '        if rs != nil && rs.isRefLocal && false {\n            error("replace cannot change'),
    'a take of a reference local is allowed': ('        if ts0 != nil && ts0.isRefLocal {\n            error("take cannot change', '        if ts0 != nil && ts0.isRefLocal && false {\n            error("take cannot change'),
}
MUTANTS.update(FP_MUTANTS)

# the paths of reference locals (FORK.md, "Reference-local paths"): a change through a reference is at the place it
# points at plus the path written. Each mutant undoes one part of the rule, in the unsafe direction (an unsafe
# neighbour in errors/ref_local_paths.obx is accepted) or in the safe one (a shape in run/ref_local_paths.obx, or in
# errors/fp_refs.obx and run/fp_refs.obx for a reference parameter, is refused)
REF_PATH_MUTANTS = {
    # the path of a designator, and where it is used
    'a path through a reference local is dropped': ('        if !viaHandle { x.path = path }', '        if !viaHandle && !root.isRefLocal { x.path = path }'),
    'a change through a reference local is at the whole root': ('invalidate(rb.roots[i], via, false, placeIn(r, i, x.path))', 'invalidate(rb.roots[i], via, false, "")'),
    'a reference derived from a reference local is anywhere in its root': ('        if x.deref { append(paths, placeIn(r, i, x.path)) }', '        if x.deref { append(paths, "") }'),
    'a mut argument invalidates the whole root': ('invalidate(b.roots[p], argVias[i], false, b.paths[p])', 'invalidate(b.roots[p], argVias[i], false, "")'),
    # placeIn
    'a reference ignores its own place for a change through it': ('    return b.paths[i] + xpath\n}', '    return xpath\n}'),
    'a reference with no known place is taken to know it': ('    if b.paths[i] == "" { return "" }\n', ''),
    'a reference to a whole object is taken to be anywhere': ('    if !b.deeps[i] { return xpath }\n', '    if !b.deeps[i] { return "" }\n'),
    'a reference not pointed afresh in a loop keeps its place': ('    if r.isRefLocal && !r.fresh && recording > 0 { return "" }\n', ''),
    # the join of two bindings
    'a join of a place and the place below it keeps the longer': ('    if segsPrefix(pa, pb) && !segsPrefix(pb, pa) { return "" }\n    if segsPrefix(pb, pa) && !segsPrefix(pa, pb) { return "" }',
                                                                  '    if segsPrefix(pa, pb) && !segsPrefix(pb, pa) { return pb }\n    if segsPrefix(pb, pa) && !segsPrefix(pa, pb) { return pa }'),
    'a join of a place and the place below it keeps the shorter': ('    if segsPrefix(pa, pb) && !segsPrefix(pb, pa) { return "" }\n    if segsPrefix(pb, pa) && !segsPrefix(pa, pb) { return "" }',
                                                                   '    if segsPrefix(pa, pb) && !segsPrefix(pb, pa) { return pa }\n    if segsPrefix(pb, pa) && !segsPrefix(pa, pb) { return pb }'),
    'a join of two places keeps the first': ('    if pa == pb { return pa }\n    if segsPrefix(pa, pb)', '    if true { return pa }\n    if segsPrefix(pa, pb)'),
    'a whole object and a place below it share one entry': ('            if roots[i] == b.roots[j] && deeps[i] == b.deeps[j] {', '            if roots[i] == b.roots[j] {'),
    # elements
    'an unknown element is taken for none of the known ones': ('            if pe - i > 1 && se - j > 1 {\n                if pe - i != se - j { return false }', '            if true {\n                if pe - i != se - j { return false }'),
    # held places
    'a held place under a reference with no known place is a known place': ('    if b.paths[i] == "" { return anyPlace }\n', ''),
    'a held place under a reference not pointed afresh in a loop is a known place': ('    if recording > 0 && !r.fresh { return anyPlace }\n', ''),
    'a free anywhere in a root misses the places held in it': ('fn heldUnder(place: string, held: string): bool { return place == anyPlace || held == anyPlace || segsPrefix(place, held) }',
                                                              'fn heldUnder(place: string, held: string): bool { return held == anyPlace || segsPrefix(place, held) }'),
    'a held place anywhere in a root misses the frees in it': ('fn heldUnder(place: string, held: string): bool { return place == anyPlace || held == anyPlace || segsPrefix(place, held) }',
                                                              'fn heldUnder(place: string, held: string): bool { return place == anyPlace || segsPrefix(place, held) }'),
    'a store to a place anywhere is exempt from a free of it (identity)': ('                if heldUnder(place, heldPath[k]) && !(heldStore[k] && place == heldPath[k] && place != anyPlace) { heldHit[k] = true }',
                                                                         '                if heldUnder(place, heldPath[k]) && !(heldStore[k] && place == heldPath[k]) { heldHit[k] = true }'),
    'a store to a place anywhere is exempt from a free of it (owned)': ('            if heldKey[k] == o && heldUnder(place, heldPath[k]) && !(heldStore[k] && place == heldPath[k] && place != anyPlace) {',
                                                                      '            if heldKey[k] == o && heldUnder(place, heldPath[k]) && !(heldStore[k] && place == heldPath[k]) {'),
    'a copy of a reference not pointed afresh in a loop keeps its place': ('else if moving { append(paths, "") } else {', 'else if false { append(paths, "") } else {'),
    # the defer rule: the declared binding must cover the one at the block's end at the same place
    'a defer body is checked against a binding that is not the same place': ('coversPlace(d.deeps[i], d.paths[i], e.deeps[j], e.paths[j], true)', 'coversPlace(d.deeps[i], d.paths[i], e.deeps[j], e.paths[j], false)'),
}
MUTANTS.update(REF_PATH_MUTANTS)

# the move rule (FORK.md, "Move bits"): a call counts in userCalls only if it might free (not pureFn); appending through
# a mut parameter moves nothing a caller has loaded. Each mutant undoes one part, in the unsafe direction (a case of
# errors/move_bits.obx is accepted) or in the safe one (run/move_bits.obx is refused again)
MOVES = 'fn movesFn(f: ^Sym): bool {\n    return !pureFn(f)\n}'
MOVE_MUTANTS = {
    'no call counts as moving': (MOVES, 'fn movesFn(f: ^Sym): bool {\n    return false\n}'),
    'every call counts as moving': (MOVES, 'fn movesFn(f: ^Sym): bool {\n    return true\n}'),
    'a call with a mut parameter counts as moving': (MOVES,
        'fn movesFn(f: ^Sym): bool {\n    if !pureFn(f) { return true }\n    for i in 0..len(f.fnRec.pmodes) {\n        if f.fnRec.pmodes[i] == 2 { return true }\n    }\n    return false\n}'),
    'the 63rd parameter is assumed to free nothing': ('    if i >= 62 { return true }\n', ''),
    'a truncate records no free': ('                if owned || !throughHandle(a) || baseOf(a.typ).kind == "string" { recordFreeChange(a, true) }   // a scalar array frees nothing; strings lose a reference\n', ''),
    'a truncate of strings through a handle records no free': ('|| !throughHandle(a) || baseOf(a.typ).kind == "string" { recordFreeChange(a, true) }', '|| !throughHandle(a) { recordFreeChange(a, true) }'),
    'a string index with a call holds no reference of its own': ('kind == "string" && x.mode != "const" && indexHasCall()', 'kind == "string" && x.mode != "const" && false'),
    'a string index pins only when the call is its first token': ("        if sym == T_LPAREN { found = true }\n", "        if sym == T_LPAREN && depth == 0 { found = true }\n"),
    'a nested bracket in an index ends the look-ahead': ("        if sym == T_LBRACK { depth += 1 }\n", ''),
    'the look-ahead does not put the scanner back': ("    pos = sPos\n    line = sLine\n", "    line = sLine\n"),
    'the look-ahead does not put the token back': ("    sym = sSym\n    sval = sSval\n", "    sval = sSval\n"),
}
MUTANTS.update(MOVE_MUTANTS)

# mutants that survive by design: each removes a test that is redundant, so the behaviour is unchanged
EQUIVALENT = {
    "a closed block's defer handles stay": "equivalent since origins per path: originFree finds the handles of a defer through originSyms, which a closed block drops, and a handle compares by generation, so a stale deferH entry never equals a later entry",
    "a loop does not restore the live marks from before it": "equivalent since loopOriginPend tests the live mark: a move of the owner in a loop, with the handle declared outside it and live at the move, is refused if the loop names the handle and otherwise sets movedLoop, which refuses naming it after the loop (errors/origin_paths.obx, the zero-trip loop after a moving return arm, is refused that way); a handle declared inside is gone at the loop's end; so the live mark after the loop is never read",
    "a defer body is checked against a binding that is not the same place": "equivalent: a reference that a defer body uses may not be pointed elsewhere while the defer is declared (deferUses), so the binding at the block's end is the one the defer was declared with, and the two are covered either way",
    "a store to a place anywhere is exempt from a free of it (identity)": "no unsafe program found: with the exemption, r[0] = replace(r[0], ..) with r not pointed afresh in a loop is accepted, which is right (same designator, run/fp-style); a free of another place needs a call in the value, which heldLost or the call rule refuses first. Tried: r1.kids = f(replace(r2[0], ..)) with both joined or not fresh",
    "a store to a place anywhere is exempt from a free of it (owned)": "equivalent for the same reason",
    "a callee with a forward heading is assumed to free nothing": "equivalent: afterCall treats a call to a callee that is not compiled as a write through a handle (invalidateDeep, writesHandles, freesAll), which invalidates every reference inside something and makes the caller's own summary assume every parameter freed; a reference to a whole object is never invalidated by a mut argument, so the check in mutFrees only repeats it",
    "a callee that writes through a handle is assumed to free nothing": "equivalent for the same reason: a callee that writes through a handle sets freesAll and writesHandles, which afterCall turns into invalidateDeep and into the same flags for its caller, so every reference a mut argument could invalidate is already invalid",
    "the frees of a reference parameter are recorded as a global": "equivalent after the refactor: recordFree's `g.isParam && g.isRef` guard cannot be reached, because no caller passes a reference parameter. recordFreeChange passes handleSym, or a root of a reference binding that is not a reference parameter (it tests o.isParam && o.isRef first and sends those to recordParamFree), or a root with r.isRef false; afterCall passes symbols of `globals`, which declare fills only outside any block; callFn passes a mut-argument root only when !(br.isParam && br.isRef); and the whole-designator callers (replace, take, assignment) cannot name a reference parameter, which is a let: replace and assignment refuse it as immutable, and take's recordFree is for globals only. A free below a reference parameter is recorded by recordParamFree at those sites, so removing this guard changes no summary",
    "origin not cleared by a take of a whole local": "equivalent: a take needs its optional owner widened first, and only an assignment over a narrowed owner does that (a handle is made only while the owner is narrowed), so the assignment hook has already cleared the same origins before any take can run",
    "replace clears origins through a reference or handle": "a designator through a field or element of a reference or handle has no sym (designatorT sets deref only when sym is nil), and a handle local is not owned (isOwned is false for handles), so originMove returns early; the guard is redundant",
    "take clears origins through a reference or handle": "the same: deref implies a nil sym, and a handle local is not owned, so originMove returns early",
    "assignment clears origins through a reference or handle": "the same: deref implies a nil sym, and a handle local is not owned, so originMove returns early",
    "origin needs no non-handle designator": "a designator through a handle has no sym, so wholeOwnerOf gives nil",
    "origin needs no whole path": "a designator with a path has no sym (only a whole name sets it), so wholeOwnerOf gives nil",
    "origin of a global is kept": "owned is set only for locals (addOwnedLocal), so a global never has an origin",
    "place origin: a handle of a reference or a global gets an origin": "equivalent: no event is ever raised for an owner that is a reference or a global (placeFreeIn, originMove and the mut-call hook all test that it is an owned local), so such an origin is never freed and never moved; run/field_origins_param.obx and field_origins_global.obx still trap at run time",
    "place origin: a callee without rows frees nothing": "defensive: a parameter's bit is set only by recordParamFree, which adds a row, so a set bit always has one",
    "place origin: a callee records every change as not strict": "equivalent: the strictness of a row matters only for a place that holds a record (the place itself survives a strict event), and a strict change is a truncate, whose place is an array, which no handle names; the anywhere row has its own encoding",
    "place origin: a row beside an anywhere row is kept": "equivalent: the events of a row below an anywhere row are covered by it",
    "a handle root of a reference is not an identity (held)": "equivalent because the older heldLost check on the reference local catches the case, not the held entry: under the mutant foreign is false, so heldMove does not hit",
}


# narrowing on alive (ifStat, conjunction, the alive builtin)
ALIVE_MUTANTS = {
    'the then arm of alive(h) is not narrowed': ('    if gi >= 0 && !neg { narrowMark(&d0, gi) }\n', ''),
    'the else arm of !alive(h) is not narrowed': ('    if gi >= 0 && neg { narrowMark(&e0, gi) }\n', ''),
    'the else arm of alive(h) is narrowed': ('    if gi >= 0 && neg { narrowMark(&e0, gi) }\n', '    if gi >= 0 { narrowMark(&e0, gi) }\n'),
    'the then arm of !alive(h) is narrowed': ('    if gi >= 0 && !neg { narrowMark(&d0, gi) }\n', '    if gi >= 0 { narrowMark(&d0, gi) }\n'),
    'an early exit does not narrow the code after it': ('    if gi >= 0 && neg && t1 { narrowMark(&dacc, gi) }\n    liveJoin(&d0, &dacc)\n    deadSet(&dacc)', '    liveJoin(&d0, &dacc)\n    deadSet(&dacc)'),
    'an early exit with an else does not narrow': ('        if gi >= 0 && neg && t1 { narrowMark(&dacc, gi) }\n', ''),
    'a negated guard whose block goes on narrows after it': ('    if gi >= 0 && neg && t1 { narrowMark(&dacc, gi) }\n    liveJoin(&d0, &dacc)\n    deadSet(&dacc)', '    if gi >= 0 && neg { narrowMark(&dacc, gi) }\n    liveJoin(&d0, &dacc)\n    deadSet(&dacc)'),
    'a negated guard with an else narrows after a block that goes on': ('        if gi >= 0 && neg && t1 { narrowMark(&dacc, gi) }\n', '        if gi >= 0 && neg { narrowMark(&dacc, gi) }\n'),
    'an or in the condition keeps the narrowing': ('        if !neg && orSeq == orBefore && (', '        if !neg && ('),
    'any token after alive(h) narrows': ('(guardFollow == T_LBRACE || guardFollow == T_ANDAND) {', 'true {'),
    'a negated guard followed by anything narrows': ('        if neg && guardFollow == T_LBRACE {', '        if neg {'),
    'alive anywhere in the condition is a guard': ('        let first = tokSeq == guardStart + 1\n', '        let first = true\n'),
    'the rest of a conjunction is not narrowed': ('                    originDead[i] = nil\n                    guardOpen = true', '                    guardOpen = true'),
    'the mark is not restored after a conjunction': ('        if guardFree == freeSeq { originDead[guardIdx] = guardMark }\n', ''),
    'a nested conjunction reopens the guard': ('guardSym != nil && !guardNeg && !guardOpen && guardFollow', 'guardSym != nil && !guardNeg && guardFollow'),
    'a negated guard opens the conjunction': ('guardSym != nil && !guardNeg && !guardOpen', 'guardSym != nil && !guardOpen'),
    'a loop does not take the marks from before it': ('    for i in 0..em {\n        if dEntry[i] != nil { originDead[i] = dEntry[i] }\n', '    for i in 0..em {\n'),
    'a move of the owner keeps a narrowed handle usable': ('        if wasLive && originNar[i] { originDead[i] = r }\n', ''),
    'the narrowed re-mark uses the flow-insensitive origin': ('        if wasLive && originNar[i] { originDead[i] = r }\n', '        if x.hOrigin == r && originNar[i] { originDead[i] = r }\n'),
    'narrowing is not remembered': ('    if d[i] != nil { originNar[i] = true }\n', ''),
    'narrowing is always remembered': ('    if d[i] != nil { originNar[i] = true }\n', '    originNar[i] = true\n'),
    'a guard in the right operand of or is open': ('        orSeq += 1\n', ''),
}
MUTANTS.update(ALIVE_MUTANTS)


# origins per path (originLive, liveClear, liveJoin, deadSave/deadSet, originMove, originFree)
PATH_MUTANTS = {
    'a move of the owner keeps the live mark': ('        if wasLive { originLive[i] = nil }\n', ''),
    'a free uses the flow-insensitive origin': ('        if placeHit(i, r, path, strict) {\n            if x.hDepth <= len(loops)', '        if placeHit(i, r, path, strict) && x.hOrigin != nil {\n            if x.hDepth <= len(loops)'),
    'an if with an else starts its live union from the marks before': ('    var dacc = deadSave()\n    liveClear(&dacc)\n    // the arm where h', '    var dacc = deadSave()\n    // the arm where h'),
    'an if without an else drops the path that runs no arm': ('    liveJoin(&d0, &dacc)\n    deadSet(&dacc)\n    if !t1 {', '    deadSet(&dacc)\n    if !t1 {'),
    'a match starts its live union from the marks before': ('    var dacc = deadSave()\n    liveClear(&dacc)\n    expect(T_LBRACE', '    var dacc = deadSave()\n    expect(T_LBRACE'),
    'a match without an else drops the path that matches no arm': ('        have = true\n        liveJoin(&d0, &dacc)\n', '        have = true\n'),
    'a match on a type starts its live union from the marks before': ('    var dacc = deadSave()\n    liveClear(&dacc)\n    let r = alloc()', '    var dacc = deadSave()\n    let r = alloc()'),
    'an arm that goes on does not add its live marks': ('        if originLive[i] != nil { acc[m + i] = originLive[i] }\n', ''),
    'the live marks are not restored with the dead ones': ('    for i in 0..m { originLive[i] = d[m + i] }\n', ''),
    'a copy of a handle has no live mark': ('append(originLive, live)', 'append(originLive, nil)'),
    'the defer guard uses the flow-insensitive origin': ('originSyms[i] == x && placeHit(i, r, path, strict) { live = true }', 'originSyms[i] == x && placeHit(i, r, path, strict) && x.hOrigin != nil { live = true }'),
    'the live marks are not dropped with the block': ('    truncate(originLive, blocks[last].hmark)\n', ''),
    'the loop move rule uses the flow-insensitive origin': ('        if originLive[i] == s && x.hDepth <= len(loops)', '        if originLive[i] == s && x.hOrigin != nil && x.hDepth <= len(loops)'),
    'a copy of a handle with no origin is not tracked': ('                if live != nil {\n                    s.hDepth', '                if live != nil && s.hOrigin != nil {\n                    s.hDepth'),
    'a loop does not restore the live marks from before it': ('        originLive[i] = dEntry[em + i]\n', ''),
}
MUTANTS.update(PATH_MUTANTS)


# handles made from a field or element of an owned local (FORK.md, "Field and element origins"): the place a handle
# is made from (pOwner, pPath, originPath), what frees it (placeHit, originFreeAt, placeFreeIn, the mut call hook, the
# assignment, replace and truncate hooks), what stops the tracking (placeOpaque), the loop and defer rules, and the
# cases that fall back to no origin. The refused shapes are in errors/field_origins.obx, the accepted neighbours in
# run/field_origins.obx, and the fallbacks in run/field_origins_*.obx. The take hook has no mutant: see EQUIVALENT
FIELD_MUTANTS = {
    # where the origin is made
    'place origin: a field handle gets no origin': ('lastRaw != "" && x.deref { pOwner = wholeOwnerOf(x.root) }', 'lastRaw != "" && x.deref && false { pOwner = wholeOwnerOf(x.root) }'),
    'place origin: a handle through a handle gets an origin': ('if origin == nil && !x.viaHandle && lastRaw != ""', 'if origin == nil && lastRaw != ""'),
    'place origin: a handle of a reference or a global gets an origin': ('{ pOwner = wholeOwnerOf(x.root) }', '{ pOwner = x.root }'),
    'place origin: the place is not kept': ('        made.pPath = praw\n', ''),
    'place origin: a handle takes no place from its item': ('                    lpath = x.pPath\n', ''),
    'place origin: a copy takes no place from the handle it copies': ('                        lpath = originPath[i]\n', ''),
    'place origin: a copy of a field handle is not tracked': ('                    live = x.pOwner\n', ''),
    'place origin: the places are not dropped with the block': ('    truncate(originPath, blocks[last].hmark)\n', ''),
    # what frees it
    'place origin: a free below the place frees the handle': ('    if !segsPrefix(path, e) { return false }\n', '    if !segsPrefix(e, path) { return false }\n'),
    'place origin: a free of another place frees it': ('    if !segsPrefix(path, e) { return false }\n', ''),
    'place origin: a free of a place frees a handle to the whole object': ('    if !segsPrefix(path, e) { return false }\n', '    if !segsPrefix(path, e) && e != "" { return false }\n'),
    'place origin: a free of another owner frees it': ('    if originLive[i] != r { return false }\n    let e = originPath[i]', '    let e = originPath[i]'),
    'place origin: a free that is strict frees the place itself': ('        if placeDepth(e) <= placeDepth(path) { return false }\n', ''),
    'place origin: a free below a place frees a place of the same depth': ('        if placeDepth(e) <= placeDepth(path) { return false }\n', '        if placeDepth(e) < placeDepth(path) { return false }\n'),
    'place origin: a truncate with a constant count frees the elements it keeps': ('            if k >= 0 && k < truncKeep { return false }\n', ''),
    'place origin: a truncate with a constant count keeps the element at the count': ('            if k >= 0 && k < truncKeep { return false }\n', '            if k >= 0 && k <= truncKeep { return false }\n'),
    'place origin: a truncate keeps a variable index': ('    if j == i + 1 { return -1 }\n    return k\n', '    if j == i + 1 { return 0 }\n    return k\n'),
    'place origin: a segment is counted by its brackets': ('        if p[i] == \'/\' { n += 1 }\n', '        if p[i] == \'[\' { n += 1 }\n'),
    # the hooks
    'place origin: an assignment over a place frees nothing': ('                    placeFreeChange(x, false, "assign over a place in")\n', ''),
    'place origin: a replace of a place frees nothing': ('            placeFreeChange(x, false, "replace a place in")\n', ''),
    'place origin: a truncate frees nothing': ('                placeFreeIn(r0, aVia, aPath, true, "truncate an array in")\n', ''),
    'place origin: a truncate of an array in a place is at the array': ('                placeFreeIn(r0, aVia, aPath, true, "truncate an array in")\n', '                placeFreeIn(r0, aVia, "", true, "truncate an array in")\n'),
    'place origin: a truncate is not strict': ('                placeFreeIn(r0, aVia, aPath, true, "truncate an array in")\n', '                placeFreeIn(r0, aVia, aPath, false, "truncate an array in")\n'),
    'place origin: a mut call frees nothing below its argument': ('                callFrees(f, argIdx[i], br, !b.deeps[p] || b.paths[p] != "", b.paths[p])\n', ''),
    'place origin: a mut call is at the whole root': ('callFrees(f, argIdx[i], br, !b.deeps[p] || b.paths[p] != "", b.paths[p])', 'callFrees(f, argIdx[i], br, !b.deeps[p] || b.paths[p] != "", "")'),
    'place origin: a change through a reference is not at its place': ('                let p = placeIn(q, i, xpath)\n', '                let p = ""\n'),
    'place origin: a change through a reference is not placed in each root': ('        for i in 0..len(rb.roots) {\n            let o = rb.roots[i]\n            if o != handleSym && !o.isRef && o.storage == "local" {', '        for i in 0..1 {\n            let o = rb.roots[i]\n            if o != handleSym && !o.isRef && o.storage == "local" {'),
    'place origin: a change through a reference with no known place is not below the root': ('originFreeAt(o, p, strict || unk, what)', 'originFreeAt(o, p, strict, what)'),
    'place origin: a direct change frees nothing': ('    } else if q.owned && q.storage == "local" {\n        originFreeAt(q, xpath, strict, what)', '    } else if q.owned && q.storage == "local" && false {\n        originFreeAt(q, xpath, strict, what)'),
    'place origin: a direct change is at the whole owner': ('        originFreeAt(q, xpath, strict, what)\n    }\n}', '        originFreeAt(q, "", strict, what)\n    }\n}'),
    # what does not stop the tracking (a free through a handle, or a recursive call, is not a reason to forget a place)
    'place origin: a change through a handle stops tracking': ('fn invalidateDeep(via: ?^Sym) {\n', 'fn invalidateDeep(via: ?^Sym) {\n    for q in 0..len(originSyms) {\n        if originPath[q] != "" { originLive[q] = nil }\n    }\n'),
    'place origin: a recursive call stops tracking': ('fn markSuspects() {\n', 'fn markSuspects() {\n    for q in 0..len(originSyms) {\n        if originPath[q] != "" { originLive[q] = nil }\n    }\n'),
    # what a mut callee may free: the rows of pfFn (pfAdd, recordParamFree, recordFreeChange, callFrees, callFreeEvent)
    'place origin: a mut callee frees anywhere below its argument': ('    if f.addr < 0 || f == curFn || f.freesAll || f.writesHandles || i >= 62 {\n        callFreeEvent(br, apath, true)', '    if true {\n        callFreeEvent(br, apath, true)'),
    'place origin: the places of a callee are not added to the argument': ('callFreeEvent(br, apath + substr(ev, 1, len(ev)), ev[0] == \'!\')', 'callFreeEvent(br, apath, ev[0] == \'!\')'),
    'place origin: the rows of a callee are all strict': ('callFreeEvent(br, apath + substr(ev, 1, len(ev)), ev[0] == \'!\')', 'callFreeEvent(br, apath + substr(ev, 1, len(ev)), true)'),
    'place origin: the rows of a callee are all not strict': ('callFreeEvent(br, apath + substr(ev, 1, len(ev)), ev[0] == \'!\')', 'callFreeEvent(br, apath + substr(ev, 1, len(ev)), false)'),
    'place origin: a callee without rows frees nothing': ('    if n == 0 { callFreeEvent(br, apath, true) }\n', ''),
    'place origin: an argument with no known place is taken to know it': ('    if !known {\n        callFreeEvent(br, "", true)\n        return\n    }\n', ''),
    'place origin: a call frees the argument itself for an unknown callee': ('    if f.addr < 0 || f == curFn || f.freesAll || f.writesHandles || i >= 62 {\n        callFreeEvent(br, apath, true)', '    if f.addr < 0 || f == curFn || f.freesAll || f.writesHandles || i >= 62 {\n        callFreeEvent(br, apath, false)'),
    'place origin: a nested call adds no rows to the caller': ('    if br.isParam && br.isRef {\n        if strict { recordParamFree(br, "!" + path) } else { recordParamFree(br, "=" + path) }\n    } else if', '    if br.isParam && br.isRef {\n    } else if'),
    'place origin: a callee records its changes as anywhere': ('                } else if strict {\n                    recordParamFree(o, "!" + p)\n                } else {\n                    recordParamFree(o, "=" + p)\n                }', '                } else {\n                    recordParamFree(o, "!")\n                }'),
    'place origin: a callee records an unknown place as a place': ('                if unknownPlace(r, i, p, x.path) {\n                    recordParamFree(o, "!")\n                } else if strict {', '                if false {\n                    recordParamFree(o, "!")\n                } else if strict {'),
    'place origin: a callee records every change as not strict': ('                } else if strict {\n                    recordParamFree(o, "!" + p)', '                } else if false {\n                    recordParamFree(o, "!" + p)'),
    'place origin: the rows of a parameter have no limit': ('    if ev != "!" && n < PF_MAX {', '    if ev != "!" {'),
    'place origin: a row is added twice': ('            if pfEv[k] == ev || pfEv[k] == "!" { return }', '            if pfEv[k] == "!" { return }'),
    'place origin: a row beside an anywhere row is kept': ('            if pfEv[k] == ev || pfEv[k] == "!" { return }', '            if pfEv[k] == ev { return }'),
    'place origin: an anywhere row does not replace the others': ('    for k in 0..len(pfFn) {\n        if pfFn[k] == f && pfIdx[k] == i { pfEv[k] = "!" }\n    }\n    if n == 0 {', '    if n == 0 {'),
    'place origin: a truncate through a reference with no known place keeps elements': ('                if unk { truncKeep = -1 }\n', ''),
    'place origin: a write through a handle to the owner frees nothing': ('                    if x.viaHandle { placeFreeVia(torig, traw, false, "assign over a place in") }\n', ''),
    'place origin: a replace through a handle to the owner frees nothing': ('            if x.viaHandle { placeFreeVia(orig, raw, false, "replace a place in") }\n', ''),
    'place origin: a truncate through a handle to the owner frees nothing': ('                if aVia { placeFreeVia(aOrig, aRaw, true, "truncate an array in") }\n', ''),
    'place origin: a write through a handle to the owner is at the whole owner': ('placeFreeVia(torig, traw, false, "assign over a place in")', 'placeFreeVia(torig, "", false, "assign over a place in")'),
    # loops and defers
    'place origin: a free of a place in a loop is not judged': ('            if x.hDepth <= len(loops) { pendPush(x, r, what) }\n            originDead[i] = r', '            if x.hDepth <= len(loops) && originPath[i] == "" { pendPush(x, r, what) }\n            originDead[i] = r'),
    'place origin: a move of the owner in a loop ignores a field handle': ('        if originLive[i] == s && x.hDepth <= len(loops) && originDead[i] == nil {', '        if originLive[i] == s && originPath[i] == "" && x.hDepth <= len(loops) && originDead[i] == nil {'),
    'place origin: a defer that uses a field handle does not stop a free of its place': ('            if originSyms[i] == x && placeHit(i, r, path, strict) { live = true }', '            if originSyms[i] == x && placeHit(i, r, path, strict) && originPath[i] == "" { live = true }'),
    'place origin: a defer that uses a field handle stops a free of another place': ('            if originSyms[i] == x && placeHit(i, r, path, strict) { live = true }', '            if originSyms[i] == x && originLive[i] == r { live = true }'),
    'place origin: a free in a defer is replayed at the whole owner': ('originFreeAt(dfOwner[j], substr(ev, 1, len(ev)), ev[0] == \'!\', "assign over")', 'originFreeAt(dfOwner[j], "", ev[0] == \'!\', "assign over")'),
    'place origin: a strict free in a defer is replayed as a free of the place': ('originFreeAt(dfOwner[j], substr(ev, 1, len(ev)), ev[0] == \'!\', "assign over")', 'originFreeAt(dfOwner[j], substr(ev, 1, len(ev)), false, "assign over")'),
    'place origin: a free of a place in a defer is not recorded': ('        append(deferFrees, r)\n        if strict { append(deferFreeEv, "!" + path) } else { append(deferFreeEv, "=" + path) }\n', ''),
}
MUTANTS.update(FIELD_MUTANTS)

# Result[T], ok/err, try and the consuming match
RESULT_MUTANTS = {
    # the shared err cell is what propagation relies on: without it an error is not recognised
    'result: try treats an ok cell as an error': ('    let toErr = jcc(CC_E)\n', '    let toErr = jcc(CC_NE)\n'),
    'result: try loads the tag as 8 bytes': ('    opMem(0x8B, tr, mem(cellR, -1, -16, 8), false, false)   // 32-bit: the high half is a generation',
                                             '    opMem(0x8B, tr, mem(cellR, -1, -16, 8), true, false)   // 32-bit: the high half is a generation'),
    'result: try returns the success value on error': ('    opMem(0x8B, RAX, mem(RBP, -1, slot.offset, 8), true, false)\n    epilogue()',
                                                       '    movRI(RAX, 0)\n    epilogue()'),
    'result: try keeps the ok shell': ('    rtCall1(RT_FREE, makeReg(rt, cellR), tVoid)             // the shell; releases cellR\n', ''),
    'result: try does not zero the ok payload': ('    opMem(0xC7, 0, mem(cellR, -1, 0, 8), true, false)       // the payload is moved out, not owned by the shell\n    emit32(0)\n', ''),
    'result: try returns the cell instead of the payload': ('    opMem(0x8B, payload, mem(cellR, -1, 0, 8), true, false)\n    opMem(0xC7, 0, mem(cellR, -1, 0, 8), true, false)       // the payload is moved out, not owned by the shell',
                                                            '    movRR(payload, cellR)\n    opMem(0xC7, 0, mem(cellR, -1, 0, 8), true, false)       // the payload is moved out, not owned by the shell'),
    'result: try does not consume its operand': ('    consume(e)\n    let cell = asReg(e)', '    let cell = asReg(e)'),
    'result: try snapshots before consuming': ("""    consume(e)
    let cell = asReg(e)
    let cellR = cell.r
    let slot = newLocal("", fr.ret, "var")
    opMem(0x89, cellR, mem(RBP, -1, slot.offset, 8), true, false)
    // The path marks are captured AFTER consume: a pre-consume snapshot would restore moved = false
    // for the operand on the ok path (double free at scope exit), and a pre-consume deadSave would
    // restore an originLive mark that consume's originMove cleared. The pending diagnostic
    // (pendLine/pendCol/pendMsg) is deliberately not restored: the error path is a real exit.
    let st = snapshot()""",
                                                 """    let st = snapshot()
    consume(e)
    let cell = asReg(e)
    let cellR = cell.r
    let slot = newLocal("", fr.ret, "var")
    opMem(0x89, cellR, mem(RBP, -1, slot.offset, 8), true, false)"""),
    'result: try error path skips the defers': ('    runDefers(fnBlock)\n    opMem(0x8B, RAX,',
                                                 '    freeOwned(blocks[fnBlock].omark, len(ownedLocals))\n    opMem(0x8B, RAX,'),
    'result: try error path skips freeOwned': ('    iteratingClear()\n    runDefers(fnBlock)\n', '    iteratingClear()\n    callDefers(fnBlock)\n'),
    'result: try error path keeps the free log': ('    truncate(freeLog, log0)\n', ''),
    'result: try error path keeps the for-walk marks': ('    iteratingClear()\n', ''),
    'result: try error path keeps the pending arrays': ('    truncate(pendSym, ps0)\n    truncate(pendDepth, pd0)\n    truncate(frLine, fl0)\n    truncate(frCol, fc0)\n    truncate(pendWhat, pw0)\n    truncate(pendOwn, po0)\n    truncate(pendNest, pn0)\n', ''),
    'result: try does not restore the state after the error block': ('    restore(st)\n    deadSet(dm)\n', '    deadSet(dm)\n'),
    'result: try does not restore the dead marks': ('    deadSet(dm)\n', ''),
    'result: err makes a descriptor per call site': ('fn errVariantType(): ^Type {\n    let have = errVariant\n    if have != nil { return have }',
                                                      'fn errVariantType(): ^Type {'),
    'result: ok descriptor owns a scalar payload': ('    if isOwned(base) { append(ptrs, 0) }', '    if true { append(ptrs, 0) }'),
    'result: ok descriptor never owns the payload': ('    if isOwned(base) { append(ptrs, 0) }', '    if false { append(ptrs, 0) }'),
    'result: ok descriptor drops no string payload': ('    if base.kind == "string" { append(strs, 0) }', '    if false { append(strs, 0) }'),
    'result: err descriptor drops no message': ('    let strs = new []int(0)\n    append(strs, 0)\n    e.desc = descriptor(16, ptrs, strs)',
                                                 '    let strs = new []int(0)\n    e.desc = descriptor(16, ptrs, strs)'),
    'result: err variant swaps message and code': ('''    append(e.fields, new Field{name: "message", offset: 0, typ: tString})
    append(e.fields, new Field{name: "code", offset: 8, typ: tInt})
    let ptrs = new []int(0)
    let strs = new []int(0)
    append(strs, 0)''',
                                                    '''    append(e.fields, new Field{name: "message", offset: 8, typ: tString})
    append(e.fields, new Field{name: "code", offset: 0, typ: tInt})
    let ptrs = new []int(0)
    let strs = new []int(0)
    append(strs, 8)'''),
    'result: ok does not consume its value': ('        if v.borrowed { error("ok takes ownership of its value: it cannot hold a borrowed reference") }\n        consume(v)',
                                              '        if v.borrowed { error("ok takes ownership of its value: it cannot hold a borrowed reference") }'),
    'result: err does not consume its message': ('        consume(m)\n        var code = makeConst(tInt, 0)', '        var code = makeConst(tInt, 0)'),
    'result: a bare err is not owned': ('    if t.kind == "result" || t.kind == "errval" { return true }', '    if t.kind == "result" { return true }'),
    'result: a string success is not a transferred reference': ('    if ut.kind == "string" { res.strTemp = true }          // the shell\'s reference is now the caller\'s\n', ''),
    # the consuming match
    'result: consuming match snapshots before consuming': ('''        release(c0)
        consume(x)                     // BEFORE the snapshot: every arm's restore must keep the move
    }
    let tag = newLocal("", tInt, "var")
    let s0 = snapshot()''',
                                                           '''        release(c0)
    }
    let tag = newLocal("", tInt, "var")
    let s0 = snapshot()
    consume(x)'''),
    'result: consuming match keeps the err shell': ('        opMem(0xC7, 0, mem(cellr, -1, 0, 8), true, false)      // the message reference is the local\'s now\n        emit32(0)\n        rtCall1(RT_FREE, makeReg(rt, cellr), tVoid)',
                                                    '        opMem(0xC7, 0, mem(cellr, -1, 0, 8), true, false)      // the message reference is the local\'s now\n        emit32(0)'),
    'result: consuming match does not zero the err message': ('        opMem(0xC7, 0, mem(cellr, -1, 0, 8), true, false)      // the message reference is the local\'s now\n        emit32(0)\n', ''),
    'result: consuming match message owns no reference': ('        addStrLocal(ml)\n', ''),
    'result: a Result match may miss an arm': ('    if len(missing) > 0 && !hasElse { errorAt("match on " + tname(t) + " is missing " + missing, line, col) }\n', ''),
    'result: else is allowed in a Result match': ('            if t.kind == "result" { errorAt("a Result match names the ok and err arms; \'else\' is not needed", tline, tcol) }\n', ''),
    'result: a variant may be matched twice': ('        for c in seen {\n            if c == v { error("\'" + vname + "\' is matched twice") }\n        }\n', ''),
    'result: try skips the function-result check': ('    if fr.ret.kind != "result" {\n        var what = "nothing"', '    if false {\n        var what = "nothing"'),
}
MUTANTS.update(RESULT_MUTANTS)

# Incremental I/O (phase 2): the builtin argument discipline, the descriptors and
# the stream accessors. mutate_port.py imports them too, judging them by the Stage B
# comparison; here the oracles are the io_pin_* programs, io_match, io_basic and
# errors/io.obx.
IO_MUTANTS = {
    # the path is pinned before the mode runs; without it the mode frees the path
    'io: openFile does not pin the path': (
        '        if p.typ.kind != "string" { error("openFile needs a string path, not " + tname(p.typ)) }\n        p = pinStr(p)\n',
        '        if p.typ.kind != "string" { error("openFile needs a string path, not " + tname(p.typ)) }\n'),
    # the fd is materialised before the later argument runs; without it the later
    # value is the one used (io_pin_fd's global read)
    'io: readFd does not materialise the fd': (
        '        if !isInt(fd.typ) { error("readFd needs an int file descriptor, not " + tname(fd.typ)) }\n        fd = materialise(fd)\n',
        '        if !isInt(fd.typ) { error("readFd needs an int file descriptor, not " + tname(fd.typ)) }\n'),
    'io: writeFd does not materialise the fd': (
        '        if !isInt(fd.typ) { error("writeFd needs an int file descriptor, not " + tname(fd.typ)) }\n        fd = materialise(fd)\n',
        '        if !isInt(fd.typ) { error("writeFd needs an int file descriptor, not " + tname(fd.typ)) }\n'),
    # a borrowed array with no owner must be refused (errors/io.obx compiles)
    'io: writeFd drops noTemp': (
        '        noTemp(d)\n        d = materialise(d)\n',
        '        d = materialise(d)\n'),
    # taking ownership of the borrowed array: a compile error or a leak after the call
    'io: writeFd consumes its data': (
        '        noTemp(d)\n        d = materialise(d)\n',
        '        noTemp(d)\n        consume(d)\n        d = materialise(d)\n'),
    # a fresh ok variant: match compares against the cached variant, so the ok arm
    # is missed; try-based code would not reveal it (io_match is the oracle)
    'io: wrong ok descriptor (fresh variant)': (
        '    append(args, b)\n    append(args, makeConst(tInt, errVariantType().desc))\n    append(args, makeConst(tInt, rt.variants[0].desc))\n',
        '    append(args, b)\n    append(args, makeConst(tInt, errVariantType().desc))\n    append(args, makeConst(tInt, okVariantOf(payload).desc))\n'),
    # closeFile's err and ok descriptors swapped: the cells carry the wrong tags
    'io: closeFile swaps the descriptors': (
        '    append(args, a)\n    append(args, makeConst(tInt, errVariantType().desc))\n    append(args, makeConst(tInt, rt.variants[0].desc))\n',
        '    append(args, a)\n    append(args, makeConst(tInt, rt.variants[0].desc))\n    append(args, makeConst(tInt, errVariantType().desc))\n'),
    # expression() leaves tryNest at 0, so the B4 case is accepted (or reports the
    # wrong message): errors/io.obx's try-in-an-argument case is the oracle
    'io: the mode argument bypasses nestedExpr': (
        '        var m = nestedExpr()\n        if m.typ.kind != "string" { error("openFile needs a string mode, not " + tname(m.typ)) }\n',
        '        var m = expression()\n        if m.typ.kind != "string" { error("openFile needs a string mode, not " + tname(m.typ)) }\n'),
    # the "w" and "a" cases of io_basic fail if every open is read-only
    'io: openFile ignores the mode': (
        '        return resultCall4(RT_OPEN_FILE, p, m, tInt)\n',
        '        return resultCall4(RT_OPEN_FILE, p, makeConst(tString, stringLit("r")), tInt)\n'),
    'io: stdout and stderr swap': (
        '        if name == "stdout" { fd = 1 } else if name == "stderr" { fd = 2 }\n',
        '        if name == "stdout" { fd = 2 } else if name == "stderr" { fd = 1 }\n'),
    'io: readFd passes max as the fd': (
        '        return resultCall4(RT_READ_FD, fd, n, derived("opt", derived("array", tByte)))\n',
        '        return resultCall4(RT_READ_FD, n, fd, derived("opt", derived("array", tByte)))\n'),
}
MUTANTS.update(IO_MUTANTS)


def mutation(src, name):
    """The mutant's source text, or (None, the stale-pattern message) when its
    pattern no longer matches once. A stale pattern is a 'missed' verdict: the
    gate fails rather than skipping the mutant."""
    old, new = PORT_PATTERNS.get(name, MUTANTS[name])
    n = src.count(old)
    if n != 1:
        return None, f"STALE   {name} (pattern found {n} times)"
    return src.replace(old, new), None


def env_jobs(var, default):
    """The positive integer in the environment's var, or default when it is unset.
    A typo is a clear error rather than a traceback from inside the pool."""
    raw = os.environ.get(var, '')
    if raw == '':
        return default
    try:
        n = int(raw)
    except ValueError:
        n = 0
    if n < 1:
        raise SystemExit(f"{var} must be a positive integer, not {raw!r}")
    return n


def jobs_and_inner():
    """(outer mutants, tests inside each), with outer x inner about the core count.
    At the default, that is one mutant per core and one test at a time: measured on
    this host, 16/1 beat 8/2 (21.9 s wall, 203 s CPU) and 4/4 (50.7 s, 262 s) --
    the suite is mostly short-lived compiler processes, so overlapping tests inside
    a mutant only adds spawn contention. MUTATE_INNER_JOBS overrides, for a host
    where it does pay."""
    jobs = env_jobs('MUTATE_JOBS', DEFAULT_JOBS)
    inner = env_jobs('MUTATE_INNER_JOBS', 0) or max(1, (os.cpu_count() or 1) // jobs)
    return jobs, inner


def build_and_run(src, fail_fast=False, jobs=1, smoke=False):
    """Builds the compiler from src, in a directory of its own, and runs run_fork.py
    with it: (the run_fork result, None), or (None, the build's error) when it does
    not build. fail_fast stops run_fork at its first failing test (the verdict is
    unchanged; only the time and the report differ), jobs tests run at once, and
    smoke tries run_fork's smoke subset first (a reorder: the rest still run)."""
    work = tempfile.mkdtemp(prefix='obo-mutant-')
    try:
        with open(os.path.join(work, 'obo.obo'), 'w') as f:
            f.write(src)
        exe = os.path.join(work, 'obo')
        b = subprocess.run([os.path.join(FORK, 'seed', 'obo'), os.path.join(work, 'obo.obo'), '-o', exe, '--rt', FORK],
                           capture_output=True, text=True)
        if b.returncode != 0:
            return None, b.stderr.strip()[:120]
        cmd = [sys.executable, os.path.join(HERE, 'run_fork.py')]
        if fail_fast:
            cmd.append('-x')
        if jobs > 1:
            cmd += ['-j', str(jobs)]
        if smoke:
            cmd.append('--smoke')
        return subprocess.run(cmd, capture_output=True, text=True, timeout=600, env=dict(os.environ, OBO=exe)), None
    finally:
        shutil.rmtree(work, ignore_errors=True)


def try_mutant(src, name, jobs=1):
    """The verdict on one mutant: (status, line, output), status being 'caught', 'missed' or 'equiv'.
    Mutants run side by side, each in a build directory of its own. The suite stops at
    the first failing test: a suite with one failing test fails, so the verdict holds.
    The smoke subset runs first; a failure there is a failure of the full suite, and a
    pass still runs every other test, so 'missed' always means the full suite passed."""
    text, stale = mutation(src, name)
    if text is None:
        return 'missed', stale, ''
    p, err = build_and_run(text, fail_fast=True, jobs=jobs,
                           smoke=os.environ.get('MUTATE_SMOKE', '1') != '0')
    if p is None:
        return 'missed', f"BROKEN  {name}: {err}", ''
    lines = p.stdout.strip().splitlines()
    detail = next((l.strip() for l in lines if l.startswith('     ')), '')
    caught = p.returncode != 0
    if not caught and name in EQUIVALENT:
        return 'equiv', f"EQUIV   {name}: {EQUIVALENT[name]}", ''
    early = " (smoke)" if "(in the smoke subset)" in p.stdout else ""
    line = f"{'caught' if caught else 'MISSED'}  {name:<36} {lines[-1].split(' in ')[0] if lines else ''}  {detail[:60]}{early}"
    return ('caught' if caught else 'missed'), line, p.stdout + p.stderr


def main():
    args = sys.argv[1:]
    options = [a for a in args if a.startswith('-')]
    if options:
        raise SystemExit(f"unknown option {options[0]!r}: the script takes a mutant-name pattern only")
    pattern = args[0] if args else ''
    src = open(os.path.join(FORK, 'src', 'obo.obo')).read()
    # the baseline: the unmutated compiler must pass run_fork, or no mutant's failure means anything
    base, err = build_and_run(src)
    if base is None:
        raise SystemExit(f"the unmutated compiler does not build ({err}), so no mutant can be judged")
    if base.returncode != 0:
        print(base.stdout.rstrip())
        raise SystemExit("run_fork fails on the unmutated compiler (above), so no mutant can be judged")
    summary = base.stdout.strip().splitlines()[-1] if base.stdout.strip() else ''
    passed = re.search(r'(\d+) passed, 0 failed', summary)
    if passed is None:
        print(base.stdout.rstrip())
        raise SystemExit("run_fork does not report a full pass on the unmutated compiler (above), "
                         "so no mutant can be judged")
    jobs, inner = jobs_and_inner()
    # ... and the same judge path a mutant takes must pass it too, with the full
    # count: a broken invocation would otherwise report every mutant as caught
    smoke = os.environ.get('MUTATE_SMOKE', '1') != '0'
    judge, err = build_and_run(src, fail_fast=True, jobs=inner, smoke=smoke)
    if judge is None:
        raise SystemExit(f"the unmutated compiler does not build ({err}), so no mutant can be judged")
    judge_summary = judge.stdout.strip().splitlines()[-1] if judge.stdout.strip() else ''
    if judge.returncode != 0 or not judge_summary.startswith(f"{passed.group(1)} passed, 0 failed"):
        print(judge.stdout.rstrip())
        raise SystemExit(f"run_fork -x{' --smoke' if smoke else ''} -j {inner} does not pass the unmutated "
                         "compiler (above), so a mutant's failure could not be trusted")
    names = [n for n in MUTANTS if pattern in n]
    status = {}
    outputs = {}
    with concurrent.futures.ThreadPoolExecutor(jobs) as pool:      # each mutant is a separate build and suite run
        for name, (s, line, out) in zip(names, pool.map(lambda n: try_mutant(src, n, inner), names)):
            print(line, flush=True)
            status[name], outputs[name] = s, out
    # a caught mutant whose failure is a timeout or an out-of-memory may be the load, not the mutant:
    # run it again alone, and report that result
    for name in names:
        if status[name] == 'caught' and TRANSIENT.search(outputs[name]):
            s, line, out = try_mutant(src, name)
            print(f"{line}   (serial re-run: the parallel run timed out or ran out of memory)", flush=True)
            status[name] = s
    missed = [n for n in names if status[n] == 'missed']
    equiv = sum(1 for n in names if n in EQUIVALENT)
    print(f"\n{len(names) - len(missed) - equiv}/{len(names)} mutants caught, {equiv} equivalent")
    sys.exit(1 if missed else 0)


if __name__ == '__main__':
    main()

