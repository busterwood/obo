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
import time

TRANSIENT = re.compile(r'timed out|out of memory', re.IGNORECASE)   # run_fork's wording for a load failure
HERE = os.path.dirname(os.path.abspath(__file__))
FORK = os.path.dirname(HERE)
DEFAULT_JOBS = os.cpu_count() or 1
sys.path.insert(0, HERE)
import mutate_stats  # noqa: E402  (optional timings and counters)

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
    "calls invalidate nothing": ("            invalidate(g, nil, true, \"\")\n", ""),
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
    "plain assignments do not hold their target": ("            if x.deref || x.viaHandle { hp = heldPushStore(r0, x.viaHandle, torig, tident, tplace, traw) }\n            var y = tailExpr()\n            pinned = savedPin\n            checkAssign(dtype, y, \"assignment\")\n",
                                                    "            var y = tailExpr()\n            pinned = savedPin\n            checkAssign(dtype, y, \"assignment\")\n"),
    "compound assignments do not hold their target": ("            if x.deref || x.viaHandle { hp = heldPushStore(r0, x.viaHandle, torig, tident, tplace, traw) }\n            var y = tailExpr()\n            pinned = savedPin\n            checkTargetAcrossCall(x, xs, r0, callsBefore, line, col)\n",
                                                      "            var y = tailExpr()\n            pinned = savedPin\n            checkTargetAcrossCall(x, xs, r0, callsBefore, line, col)\n"),
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
    "defer uses not checked at a return without a value": ("        if sym != T_SEMI && sym != T_RBRACE { error(callableName(f) + \" does not return a value\") }\n        runDefers(fnBlock)\n",
                                                           "        if sym != T_SEMI && sym != T_RBRACE { error(callableName(f) + \" does not return a value\") }\n" + unchecked_exit("fnBlock")),
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
    "a write through an invalidated reference gets the owned message": ("            if s.isRefLocal { error(freedRefMsg(qname)) }\n", ""),
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
    'no check after a reference argument': ('            argOrderCheck(tbase, fname, meth, i, argLine, argCol)\n            holdArg(b, i)\n',
        '            holdArg(b, i)\n'),
    'no check after a by-value argument': ('            consume(a)\n            argOrderCheck(tbase, fname, meth, i, argLine, argCol)\n',
        '            consume(a)\n'),
    'the holder pool never grows': ('    if argTempsUsed == len(argTemps) {\n', '    if argTempsUsed < 0 {\n'),
    'an argument holder is not a reference local': ('        s.isRefLocal = true\n        append(argTemps, s)\n',
        '        append(argTemps, s)\n'),
    'an argument holder keeps its old binding': ('    t.bind = b\n    t.suspect = false\n', '    t.suspect = false\n'),
    'an argument holder keeps its old suspicion': ('    t.suspect = false\n    t.value = i\n', '    t.value = i\n'),
    'an argument holder keeps its old index': ('    t.value = i\n    append(ownedLocals, t)\n', '    append(ownedLocals, t)\n'),
    'a freed argument is not refused': ('        if t.moved {\n            errorAt(argLabel', '        if false {\n            errorAt(argLabel'),
    'a suspect argument is not noted': ('        if t.suspect {\n            notePending(line, col, argLabel',
        '        if false {\n            notePending(line, col, argLabel'),
    'the check reaches the holders of enclosing calls': ('    for k in tbase..argTempsUsed {\n        let t = argTemps[k]\n',
        '    for k in 0..argTempsUsed {\n        let t = argTemps[k]\n'),
    'only the newest holder is refused': ('        if t.moved {\n            errorAt(argLabel',
        '        if t.moved && k == argTempsUsed - 1 {\n            errorAt(argLabel'),
    'argument holders are not reused': ('    argTempsUsed = tbase\n    truncate(ownedLocals, omark)\n', '    truncate(ownedLocals, omark)\n'),
    'argument holders stay live after the call': ('    argTempsUsed = tbase\n    truncate(ownedLocals, omark)\n', '    argTempsUsed = tbase\n'),
    "a call's holders are not released": ('    releaseArgs(tbase, omark)\n    if sym != T_RPAREN', '    if sym != T_RPAREN'),
    "a function's argument holders outlive it": ('    truncate(bindArena, bindMark)\n    truncate(argTemps, tempMark)\n', '    truncate(bindArena, bindMark)\n'),
    "writeFile's bytes are not held": ('        holdArg(itemBind(d), 1)\n', ''),
    "writeFile's bytes are held as another argument": ('        holdArg(itemBind(d), 1)\n', '        holdArg(itemBind(d), 0)\n'),
    "writeFile's flag is not checked": ('        argOrderCheck(tbase, name, false, 2, eLine, eCol)\n', ''),
    "writeFile's holder is not released": ('        argOrderCheck(tbase, name, false, 2, eLine, eCol)\n        releaseArgs(tbase, omark)\n',
        '        argOrderCheck(tbase, name, false, 2, eLine, eCol)\n'),
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

# Stage 1 modules: exports and privacy, type identity (B7), the dependency-first build
# (B4 identity, duplicate/cycle/basename rules, globals and teardown) and the effect
# summaries across modules (B2). The module cases of tests/module_test.py, wired into
# run_fork, and the module trees of port_compare kill them.
MODULE_MUTANTS = {
    "modules: exports ignored (all private)": ("        if s.name == name && s.exported { return s }",
                                               "        if s.name == name && false { return s }"),
    "modules: exports ignored (all public)": ("        if s.name == name && s.exported { return s }",
                                              "        if s.name == name { return s }"),
    "modules: closed variants not exported": ("            vs.exported = pubType                // exporting a closed type exports its variants",
                                              "            vs.exported = false"),
    "modules: private-type closure dropped": ("    checkExports()\n", ""),
    "modules: closure skips closed variant fields (B5)": ("            closureCheck(t.variants[i], declName, t.variants[i].name, line)\n", ""),
    "modules: reserved type names allowed": ("    if reservedTypeName(name) { errorAt(\"cannot declare a type named '\" + name + \"'\", line, 1) }",
                                             "    if false { errorAt(\"cannot declare a type named '\" + name + \"'\", line, 1) }"),
    "modules: const nil allowed": ("    if x.typ.kind == \"nil\" { error(\"a constant cannot be nil; use a typed optional var or let\") }",
                                   "    if false { error(\"a constant cannot be nil; use a typed optional var or let\") }"),
    "modules: imports after declarations allowed": ("        if sym == T_IMPORT { errorAt(\"imports must come before declarations\", tline, tcol) }",
                                                    "        if false { errorAt(\"imports must come before declarations\", tline, tcol) }"),
    "modules: duplicate alias allowed": ("        if curModule.imports[i].alias == alias {\n            errorAt(\"'\" + alias + \"' is already used as an import alias\", iline, icol)\n        }",
                                         "        if false {\n            errorAt(\"'\" + alias + \"' is already used as an import alias\", iline, icol)\n        }"),
    "modules: alias/declaration clash allowed": ("        if curModule.imports[i].alias == s.name { error(\"'\" + s.name + \"' clashes with a top-level declaration\") }",
                                                 "        if false { error(\"'\" + s.name + \"' clashes with a top-level declaration\") }"),
    "modules: built-in import alias allowed": ("    if reservedTypeName(alias) || findSym(universe, alias) != nil {",
                                               "    if false {"),
    "modules: alias lookup takes any module": ("        if curModule.imports[i].alias == alias { return handle(curModule.imports[i]) }",
                                               "        if true { return handle(curModule.imports[i]) }"),
    "modules: cycle detection dropped": ("    if seen != nil && onImportStack(seen) {", "    if seen != nil && seen == curModule {"),
    "modules: duplicate module names allowed": ("        if modules[i].path != mod.path && modules[i].name == mod.name {", "        if false {"),
    "modules: basename rule dropped": ("        if mod.name != want {", "        if false {"),
    "modules: import syntax not required": ("        if !isRoot { errorAt(\"imported module '\" + display + \"' needs a 'module Name' header\", tline, tcol) }\n        mod.name = \"_root\"",
                                            "        if false { errorAt(\"imported module '\" + display + \"' needs a 'module Name' header\", tline, tcol) }\n        mod.name = \"_root\""),
    "modules: B4 path normalization keeps .": ("            if len(seg) > 0 && seg != \".\" {", "            if len(seg) > 0 {"),
    "modules: B2 freed keys propagated only when visible": ("""    if !all {
        let freed = &f.fnRec.freed
        for g in freed { recordFree(g) }
    }
    // The module's visible globals are invalidated for their references' sake only: a reference
    // can only point into a global the module can name, so its direct imports suffice.
    let gs = &visibleGlobals
    for g in gs {
        if (g.kind == "var" || g.kind == "let") && (all || mightFree(f, g)) {
            invalidate(g, nil, true, "")
        }
    }""",
        """    let gs = &visibleGlobals
    for g in gs {
        if (g.kind == "var" || g.kind == "let") && (all || mightFree(f, g)) {
            invalidate(g, nil, true, "")
            if !all { recordFree(g) }
        }
    }"""),
    "modules: imported globals not invalidated": ("            invalidate(g, nil, true, \"\")\n", ""),
    "modules: imported global lookup returns a copy": ("            if dep.globals[i].exported { append(visibleGlobals, dep.globals[i]) }",
                                                        "            if dep.globals[i].exported { append(visibleGlobals, newSym(\"var\", dep.globals[i].name, symType(dep.globals[i]))) }"),
    "modules: imported pmodes dropped": ("            if f.fnRec.pmodes[i] == 2 && !b.writable {", "            if false {"),
    "modules: a mut callee assumed to free below every argument": ("    if f.addr < 0 || f == curFn || f.freesAll || f.writesHandles { return true }",
                                                                    "    if true { return true }"),
    "modules: a module imported twice is compiled twice": ("    if seen == nil { compileUnit(rp, raw, false) }\n",
                                                            "    compileUnit(rp, raw, false)\n"),
    "modules: teardown frees imported globals too": ("        append(programGlobals, curModule.globals[i])",
                                                      "        append(programGlobals, visibleGlobals[i])"),
    "modules: teardown frees no module globals": ("        if (g.kind == \"var\" || g.kind == \"let\") && isOwned(symType(g)) {",
                                                   "        if false {"),
    "modules: scanner state not restored after an import": ("    visibleGlobals = savedVisible\n    restoreScan(ss)\n}", "    visibleGlobals = savedVisible\n}"),
    "modules: imports not bound": ("    append(curModule.imports, new Import{alias: alias, mod: dep, raw: raw})",
                                 "    if false { append(curModule.imports, new Import{alias: alias, mod: dep, raw: raw}) }"),
    "modules: B7 alias key overwrites the shared type": ("fn typeAlias(name: string, line: int, isPub: bool, t: ^Type) {\n    registerTypeAlias(curModuleName, name, t)\n",
                                                          "fn typeAlias(name: string, line: int, isPub: bool, t: ^Type) {\n    t.owner = curModuleName\n    registerTypeAlias(curModuleName, name, t)\n"),
    "modules: _root accepted in a dependency": ("        if !isRoot && mod.name == \"_root\" {", "        if false {"),
    "modules: main in a dependency allowed": ("    if !isMethod && name == \"main\" && !curModule.isRoot {", "    if false {"),
    "modules: pub inside a function accepted": ("        T_PUB { error(\"'pub' is only allowed on top-level declarations\") }", "        T_PUB { next() }"),
    "modules: pub import accepted": ("            if sym == T_IMPORT { errorAt(\"'pub' applies to declarations, not imports\", pline, pcol) }",
                                     "            if false { errorAt(\"'pub' applies to declarations, not imports\", pline, pcol) }"),
    "modules: import paths need not end in .obx": ("    if !endsWith(raw, \".obx\") { errorAt(\"import paths must end in .obx\", iline, icol) }\n    next()",
                                                   "    if false { errorAt(\"import paths must end in .obx\", iline, icol) }\n    next()"),
    "modules: -I ignored": ("        for k in 0..len(incDirs) { append(cands, joinDirPath(incDirs[k], raw)) }\n", ""),
    "modules: module globals share one slot": ("        gsym.offset = GLOBAL_BASE + gsize", "        gsym.offset = GLOBAL_BASE"),
    "modules: T_COLONCOLON is 66": ("const T_COLONCOLON = 67", "const T_COLONCOLON = 66"),
    "modules: token names lack ::": ("try ::\"", "try\""),
    "modules: pre-scan forgets the :: of a qualified literal": ("""        } else if c == ':' {
            // `new lib::Node{...}`: the `::` is part of the type name, so the member word after it is
            // still the type a record literal names. Treating the `::` like `new` keeps afterNew set
            // through the member word, so the `{` is counted as a record literal (kind 2).
            if p + 1 < n && src[p + 1] == ':' && wasNew {
                prevNew = true
                p += 2
            } else {
                p += 1
            }
""", """        } else if c == ':' {
            p += 1
"""),
}
SYM_MUTANTS = {
    # ---- writer: canonical declarations, order and summaries
    "sym: shells omitted": ("        if s.exported && symHasShell(s, modName) { appTypeShell(b, s) }",
                            "        if false { appTypeShell(b, s) }"),
    "sym: definitions omitted": ("            appTypeDef(b, mod.globals[best], modName)",
                                 "            let skippedDef = best"),
    "sym: aliases omitted": ("        if s.exported && s.kind == \"type\" && !symDefines(s, modName) && !symIsVariantSym(s) {",
                             "        if false {"),
    "sym: alias to a variant omitted": ("        if s.exported && s.kind == \"type\" && !symDefines(s, modName) && !symIsVariantSym(s) {",
                                        "        if s.exported && s.kind == \"type\" && !symDefines(s, modName) && true {"),
    "sym: constants omitted": ("        if s.exported && s.kind == \"const\" { appConst(b, s) }",
                               "        if false { appConst(b, s) }"),
    "sym: globals omitted": ("        if s.exported && (s.kind == \"var\" || s.kind == \"let\") && s.storage == \"global\" {",
                             "        if false {"),
    "sym: functions omitted": ("        if s.exported && s.kind == \"fn\" { appFn(b, s, modName) }",
                               "        if false { appFn(b, s, modName) }"),
    "sym: canonical parameter names lost": ("        append(b, 'p')\n        appInt(b, i)",
                                             "        append(b, 'q')\n        appInt(b, i)"),
    "sym: closed variants omitted": ("        appStr(b, t.variants[i].name)",
                                     "        appStr(b, \"\")"),
    "sym: record fields reversed": ("        appStr(b, rec.fields[i].name)\n        appStr(b, \": \")\n        appTypeExpr(b, rec.fields[i].typ, modName)",
                                    "        appStr(b, rec.fields[len(rec.fields) - 1 - i].name)\n        appStr(b, \": \")\n        appTypeExpr(b, rec.fields[len(rec.fields) - 1 - i].typ, modName)"),
    "sym: local type expressions lose their bare name": ("    if t.owner != \"\" && t.owner != modName {",
                                                         "    if t.owner != \"\" {"),
    "sym: freesall omitted": ("    if f.freesAll { appStr(b, \" freesall\") }",
                              "    if false { appStr(b, \" freesall\") }"),
    "sym: writeshandles omitted": ("    if f.writesHandles { appStr(b, \" writeshandles\") }",
                                   "    if false { appStr(b, \" writeshandles\") }"),
    "sym: pfree omitted": ("    if f.fnRec.pfree != 0 {", "    if false {"),
    "sym: frees list omitted": ("    if len(f.fnRec.freed) > 0 {", "    if false {"),
    "sym: pf rows omitted": ("            if pfFn[k] == f && pfIdx[k] == i {", "            if false {"),
    "sym: int constants written in decimal": ("    } else {\n        appHex16(b, s.value)\n    }",
                                              "    } else {\n        appStr(b, string(s.value))\n    }"),
    "sym: string constant bytes replaced by the address": ("        let data = rodataString(s.value)",
                                                            "        let data = string(s.value)"),
    # ---- header, hashes and dependencies
    "sym: module line omitted": ("    appStr(b, \"module \")\n    appStr(b, modName)",
                                 "    appStr(b, \"\")\n    appStr(b, modName)"),
    "sym: source line carries the interface hash": ("    appStr(b, \"source \")\n    appHex16(b, mod.srcHash)",
                                                    "    appStr(b, \"source \")\n    appHex16(b, h)"),
    "sym: interface line writes zero": ("    appStr(b, \"interface \")\n    appHex16(b, h)",
                                        "    appStr(b, \"interface \")\n    appHex16(b, 0)"),
    "sym: dep header lines omitted": ("    for i in 0..len(dl.names) {\n        appStr(b, \"dep \")",
                                      "    for i in 0..0 {\n        appStr(b, \"dep \")"),
    "sym: depiface body lines omitted": ("    for i in 0..len(dl.names) {\n        appStr(b, \"depiface \")",
                                         "    for i in 0..0 {\n        appStr(b, \"depiface \")"),
    "sym: duplicate imports not deduped": ("        if !have {\n            append(out.names, im.mod.name)",
                                           "        if true {\n            append(out.names, im.mod.name)"),
    # ---- reader: version, hashes, structure
    "sym: format check accepts any version": ("    if fmt != SYM_FORMAT {", "    if false {"),
    "sym: ABI check accepts any version": ("    if abi != SYM_ABI {", "    if false {"),
    "sym: compiler check accepts any version": ("    if cv != OBO_VERSION {", "    if false {"),
    "sym: source hash not validated": ("        if srcFound && srcHash != fileSrc {",
                                       "        if false {"),
    "sym: body hash not checked": ("    if computed != ifaceH { symFileFail(symPath, \"is corrupt (interface hash); rebuild it\") }",
                                   "    if false { symFileFail(symPath, \"is corrupt (interface hash); rebuild it\") }"),
    "sym: depiface hash not checked": ("        if dm.ifaceHash != depIfHashes[i] {",
                                       "        if false {"),
    "sym: transitive dependency interface not loaded": ("            dm = loadSymCheck(joinDirPath(symDir, dn + \".sym\"), dn, depPaths[i], cand)",
                                                        "            dm = handle(noModule)"),
    "sym: malformed hexadecimal hash accepted": ("    if !isHex16(text) {", "    if false {"),
    "sym: dependency interface order not checked": ("        if depIfNames[i] != depNames[i] {",
                                                    "        if false {"),
    "sym: empty dependency path accepted": ("        if len(dp) == 0 { error(\"a dependency path cannot be empty\") }",
                                            "        if false { error(\"a dependency path cannot be empty\") }"),
    "sym: malformed summary event prefix accepted": ("    if pref != '=' && pref != '!' { error(",
                                                     "    if false { error("),
    "sym: malformed summary event path accepted": ("            symValidateEvent(ev)",
                                                   "            if false { symValidateEvent(ev) }"),
    "sym: pf index not bounded": ("            if pi < 0 || pi >= len(ptypes) { error(\"pf index \" + string(pi) + \" is out of range\") }",
                                  "            if pi < 0 { error(\"pf index \" + string(pi) + \" is out of range\") }"),
    "sym: pf row without its pfree bit accepted": ("        if pi < 62 && (low >> pi) & 1 == 0 {",
                                                   "        if false {"),
    "sym: pfree bit without a pf row accepted": ("            if !have { error(\"pfree bit \" + string(i) + \" has no pf row\") }",
                                                 "            if false { error(\"pfree bit \" + string(i) + \" has no pf row\") }"),
    "sym: pfree high bits not ignored": ("    let low = pfree & 0x3FFFFFFFFFFFFFFF", "    let low = pfree"),
    "sym: mut without a reference accepted": ("            if pmut { error(\"mut needs a reference parameter: write mut p0: &T\") }\n            append(ptypes, symParseType())\n",
                                              "            append(ptypes, symParseType())\n"),
    "sym: scalar borrow accepted": ("            if !isOwned(t) { error(\"only records, closed types and arrays can be borrowed, not \" + symTypeText(t)) }",
                                    "            if false { error(\"only records, closed types and arrays can be borrowed, not \" + symTypeText(t)) }"),
    "sym: non-primitive constant accepted": ("    if t != tInt && t != tByte && t != tBool && t != tString {",
                                             "    if false {"),
    "sym: byte constant range unchecked": ("    if t == tByte && (v < 0 || v > 255) { error(\"a byte constant must be between 0 and 255\") }",
                                           "    if false { error(\"a byte constant must be between 0 and 255\") }"),
    "sym: bool constant value unchecked": ("    if t == tBool && kind != \"bool\" { error(\"a bool constant must be true or false\") }",
                                           "    if false { error(\"a bool constant must be true or false\") }"),
    "sym: duplicate exported names accepted": ("    if symHas(symSeen, name) { error(\"duplicate exported name '\" + name + \"'\") }",
                                               "    if false { error(\"duplicate exported name '\" + name + \"'\") }"),
    "sym: shell completeness unchecked": ("""    let shells = &symShells
    for sh in shells {
        if !symHas(symDefined, sh) { error("type '" + sh + "' has a shell but no definition") }
    }
    if mode == 1 {
        for i in 0..len(symCurMod.globals) {
            let s = symCurMod.globals[i]
            if s.kind == "type" && symType(s).incomplete { error("type '" + s.name + "' has a shell but no definition") }""",
        """    if mode == 1 {
        for i in 0..len(symCurMod.globals) {
            let s = symCurMod.globals[i]
            if false { error("type '" + s.name + "' has a shell but no definition") }"""),
    "sym: definition without a shell accepted": ("""        if !symHas(symShells, name) { error("type '" + name + "' has a definition but no shell") }
        if symHas(symDefined, name) { error("duplicate definition of type '" + name + "'") }
        symAfterShells = true
        next()
        expect(T_RECORD, "")""",
        """        if false { error("type '" + name + "' has a definition but no shell") }
        if symHas(symDefined, name) { error("duplicate definition of type '" + name + "'") }
        symAfterShells = true
        next()
        expect(T_RECORD, "")"""),
    "sym: reader resolves bare types through lookup": ("""    let s = findExport(symCurMod, name)
    if s != nil {
        if s.kind == "type" { return symType(s) }
    }
    errorAt("unknown type '" + name + "'", nline, ncol)""",
        """    let s = lookup(name)
    if s.kind == "type" { return symType(s) }
    errorAt("unknown type '" + name + "'", nline, ncol)"""),
    "sym: scanner state not restored after a .sym read": ("""    symReadDecls(mode)
    symFinish(mode)
    restoreScan(ss)
    symMode = savedSymMode""",
        """    symReadDecls(mode)
    symFinish(mode)
    symMode = savedSymMode"""),
    "sym: imported function is a forward heading": ("        f.addr = 0                                    // known: a complete imported summary, never a forward heading",
                                                     "        f.addr = -1"),
    "sym: imported pfree dropped": ("\n        f.fnRec.pfree = low\n", "\n        f.fnRec.pfree = 0\n"),
    "sym: imported parameter modes dropped": ("\n            append(f.fnRec.pmodes, pmodes[k])\n",
                                              "\n            append(f.fnRec.pmodes, 0)\n"),
    "sym: imported freed keys dropped": ("""        for k in 0..len(freedOwners) {
            append(f.fnRec.freed, globalSym(freedOwners[k], freedNames[k]))
        }""",
        """        for k in 0..0 {
            append(f.fnRec.freed, globalSym(freedOwners[k], freedNames[k]))
        }"""),
    "sym: B6 global stub not reused": ("    let have = globalAtKey(key)\n    if have != nil { return have }",
                                       "    let have = globalAtKey(key)\n    if have != nil { let fresh = newSym(\"var\", name, nil); fresh.owner = owner; fresh.storage = \"global\"; append(globalKeys, key); append(globalRegistry, fresh); return fresh }"),
    "sym: B6 defining global creates a fresh symbol": ("        let g = globalSym(symCurMod.name, name)",
                                                       "        let g = newSym(kind, name, nil)"),
    "sym: B7 alias key creates a new type": ("""        registerTypeAlias(symCurMod.name, name, t)
        let s = newSym("type", name, t)""",
        """        let nt = newType("record", name, nil)
        nt.owner = symCurMod.name
        registerTypeAlias(symCurMod.name, name, nt)
        let s = newSym("type", name, nt)"""),
    "sym: live type registry not filled": ("    registerType(t, curModuleName, name)",
                                           "    let keep = t"),
    "sym: live module globals not registered": ("    if s.storage == \"global\" { registerGlobalSym(s) }",
                                                "    if false { registerGlobalSym(s) }"),
    # ---- root rules, modes and CLI
    "sym: single-file build writes a root .sym": ("    if isRoot && !checking && len(mod.imports) == 0 && !emitRootSym { doWrite = false }",
                                                  "    if false { doWrite = false }"),
    "sym: --emit-sym ignored": ("    if isRoot && !checking && len(mod.imports) == 0 && !emitRootSym { doWrite = false }",
                                "    if isRoot && !checking && len(mod.imports) == 0 { doWrite = false }"),
    "sym: headerless --emit-sym accepted": ("""    if isRoot && emitRootSym && !mod.hasHeader {
        errorAt("--emit-sym needs a 'module Name' header in the root source", 1, 1)
    }""",
        """    if false {
        errorAt("--emit-sym needs a 'module Name' header in the root source", 1, 1)
    }"""),
    "sym: checked module needs no header": ("""        if checking && isRoot {
            errorAt("the checked module needs a 'module Name' header", tline, tcol)
        }""",
        """        if false {
            errorAt("the checked module needs a 'module Name' header", tline, tcol)
        }"""),
    "sym: --version reports the wrong format": ("            println(\"obo \", OBO_VERSION, \" sym-format \", SYM_FORMAT, \" abi \", SYM_ABI)",
                                                "            println(\"obo \", OBO_VERSION, \" sym-format \", 0, \" abi \", SYM_ABI)"),
    "sym: -o accepted with --check": ("    if mode != \"build\" && outSet { cliFail(\"-o cannot be used with --check or --deps\") }",
                                      "    if false { cliFail(\"-o cannot be used with --check or --deps\") }"),
    "sym: sym-dir write failure ignored": ("""    if !writeFile(joinDirPath(symDir, mod.name + ".sym"), text, false) {
        eprintln("obxc: symbol-file directory '", symDir, "' does not exist or is not writable")
        exit(2)
    }""",
        """    if false {
        eprintln("obxc: symbol-file directory '", symDir, "' does not exist or is not writable")
        exit(2)
    }"""),
    "sym: default sym-dir is the current directory": ("    if !symDirSet { symDir = dirOf(normPath(path)) }\n    compileProgram(path, out, rtDir)",
                                                      "    if !symDirSet { symDir = \".\" }\n    compileProgram(path, out, rtDir)"),
    "sym: --check does not set check mode": ("fn checkFile(path: string) {\n    checking = true",
                                              "fn checkFile(path: string) {\n    checking = false"),
    "sym: source-less build message not used": ("    if !depsWalk && probe != nil {",
                                                "    if false {"),
    "sym: negative source hash treated as absent": ("        if srcFound && srcHash != fileSrc {",
                                                     "        if srcHash >= 0 && srcHash != fileSrc {"),
    "sym: imported pf rows dropped": ("""        for k in 0..len(rowIdx) {
            append(pfFn, f)
            append(pfIdx, rowIdx[k])
            append(pfEv, rowEv[k])
        }""",
        """        for k in 0..len(rowIdx) {
            let lostRow = f
        }"""),
    "sym: dependency basename check dropped": ("""    if bodyName != expected {
        symSourceRule("module '" + bodyName + "' must be in a file named " + bodyName + ".obx (found " + display + ")")
    }""",
        """    if false {
        symSourceRule("module '" + bodyName + "' must be in a file named " + bodyName + ".obx (found " + display + ")")
    }"""),
    "sym: definition order reversed": ("                if best < 0 || s.line < bestLine {",
                                        "                if best < 0 || s.line > bestLine {"),
    "sym: type and global keys lose the module prefix": ('fn keyOf(owner: string, name: string): string { return owner + "::" + name }',
                                                          'fn keyOf(owner: string, name: string): string { return name }'),
    "sym: variant parent not set": ("                v.uid = fnv1a(vkey)\n                v.parent = ct",
                                     "                v.uid = fnv1a(vkey)\n                v.parent = nil"),
    "sym: shell uid not validated": ('            if uid != fnv1a(key) { error("type \'" + name + "\' uid does not match its key") }',
                                      '            if false { error("type \'" + name + "\' uid does not match its key") }'),
    "sym: symMode not restored after a nested read": ("""    restoreScan(ss)
    symMode = savedSymMode
    truncate(symLoadNames, len(symLoadNames) - 1)""",
        """    restoreScan(ss)
    symMode = false
    truncate(symLoadNames, len(symLoadNames) - 1)"""),
}

MODULE_MUTANTS.update(SYM_MUTANTS)

MUTANTS.update(MODULE_MUTANTS)

# mutants that survive by design: each removes a test that is redundant, so the behaviour is unchanged
EQUIVALENT = {
    "methods: closureSeen is not truncated per method": "equivalent: only a local named type that is not exported is an error in closureCheck, and it errors the first time it is seen, so sharing closureSeen across methods cannot suppress an error; a foreign type is marked seen and skipped, and an anonymous type has no name to check. Truncating per method keeps the visit set small and matches the function loop; it is not observable",
    "methods: the mode-0 method order check is dropped": "equivalent: a full build writes the dependency's .sym and reads it immediately (compileUnit then parseImport), so no test can interpose a file with a different method order; the writer's own order is checked by the golden and round-trip tests",
    "methods: the mode-0 missing method check is dropped": "equivalent for the same reason: in mode 0 a full build compares the file its own writer just produced from the same live method registry, so no live exported method can be missing from it; a hand-supplied file is read only by --check (mode 1), which does not run the mode-0 missing-method check",
    "methods: the mode-0 method signature check is dropped": "equivalent for the same reason: a full build compares its own freshly written signature with the live one; a forged file is only ever shown to --check, which trusts it by design (the .sym trust model)",
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
    "modules: pre-scan forgets the :: of a qualified literal": "equivalent: the pre-scan only estimates register pressure. A qualified record literal is then not counted as a record literal, which can underestimate the live registers and give the function more register homes, or exhaust its temporaries and make it run out and be compiled again with none (homeRetry); both produce the same code semantics. homeRetry is the documented fallback (FORK.md, modules Stage 1); tests/homes_check.py still passes either way, and tests/run/modules is a correctness oracle only",
    "image: the epilogue is the old two-instruction sequence": "equivalent: x86 leave is defined as mov rsp,rbp; pop rbp (RSP := RBP, then RBP := Pop()) and has no other effect, so the old five-byte encoding and leave; ret produce the same registers, flags and memory; only the code size differs, and the behavioural gates do not compare produced bytes",

    # interfaces v3 step 2: the summary bits that no reader consults, and the mode-0 check a full build cannot reach
    "interfaces v3 X30: the mode-0 comparison ignores parameter modes": "equivalent for the same reason as the methods' mode-0 checks: a full build reads each import's .sym right after its own writer has rewritten it from the same live interface (compileUnit, writeSym, then readSym in mode 0), and an import must have a header, so no full build reads a file it did not write (a forged lib.sym in the sym dir is overwritten first); --check reads in mode 1, which compares nothing with the live interface. The writer's modes are pinned by the golden and the round trip (a writer that drops mut, X26, is caught there)",
    "interfaces v3 X8: the receiver's obj and vtable are loaded after the arguments (S4)": "equivalent on legal source: S4's order is defensive. The receiver is held as argument 0 (holdArg), and argOrderCheck refuses any later argument that moves or frees what the receiver points into; the mut-alias check also refuses a mut argument that points inside the receiver. For an interface receiver that is the box or pair slot, so no accepted call can replace, re-point or free the place whose obj and vtable the mutant reads. The mutant still saves the box pointer as argument 0 before the explicit arguments and reserves the vtable slot above them; after the arguments it reads [box] and [box+8] and fills those exact slots, so argument order, layout and the two dispatch words are unchanged. E6's errors/ifaces_calls case `h.d.grow(swap(h))` is rejected identically by the unmutated and mutant compilers, and the full suite (including run/ifaces_calls and run/ifaces_effects) passes without a fault. The only difference is the time of two reads of unchanged box words, which no legal program can observe",
    "interfaces v3 X15: the local tier drops the pfree-subset check": "equivalent in the current architecture: the signature comparison has already required method and member parameter modes to be equal (X38d), and the synthetic S(m) puts a pfree bit on exactly the member's mode-2 parameters. callFn records pfree only for a mode-2 argument, so a source method's pfree mask is a subset of S(m) by construction; a mode-1 parameter cannot be changed and no recorded free can land on it. The branch is defensive for a forged *method* summary, but implDecl only ever sees methods compiled from source: a .sym reader trusts an imported implementation and does not re-run the check, and a root cannot implement an interface for an imported type. The full suite passes without the branch, and there is no honest input that reaches it, so no test can distinguish it.",
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

# image emission (FORK.md, "Compiler size fixes"): the runtime data segment's
# file size is its nonzero prefix, its file bytes that prefix and its memory
# size the full image; the epilogue is leave; ret. The first three change only
# produced images, so the image test in run_fork judges them (mutate_port runs
# elf_test.py); the int3 return is caught by the ordinary executable tests. The
# old two-instruction epilogue is genuinely equivalent: x86 leave is defined as
# mov rsp,rbp; pop rbp, and only the encoding's size differs.
SIZE_MUTANTS = {
    "image: the runtime data keeps its zero tail": (
        "    let rtDataFile = dataFileLen(rtData)             // the trailing .bss need not be in the file\n",
        "    let rtDataFile = len(rtData)\n"),
    "image: the runtime data memory size is its file size": (
        "    phdr(ph, 6, rtDataAt, RT_DATA, rtDataFile, len(rtData))\n",
        "    phdr(ph, 6, rtDataAt, RT_DATA, rtDataFile, rtDataFile)\n"),
    "image: the runtime data copies only its first byte": (
        "    for i in 0..rtDataFile { out[rtDataAt + i] = rtData[i] }\n",
        "    if rtDataFile > 0 { out[rtDataAt] = rtData[0] }\n"),
    "image: the epilogue is the old two-instruction sequence": (
        "    emit(0xC9)                   // leave: mov rsp,rbp; pop rbp\n",
        "    emit(0x48)\n    emit(0x89)\n    emit(0xEC)\n    emit(0x5D)\n"),
    "image: the epilogue returns with int3": (
        "    emit(0xC3)                   // ret\n",
        "    emit(0xCC)                   // ret\n"),
}
MUTANTS.update(SIZE_MUTANTS)


METHOD_MUTANTS = {
    "methods: an owned receiver is not consumed": (
        "            checkAssign(f.fnRec.params[i], a, argLabel(meth, i, start) + \" of \" + callDisplay(meth, fname))\n            consume(a)\n            argOrderCheck(tbase, fname, meth, i, argLine, argCol)\n",
        "            checkAssign(f.fnRec.params[i], a, argLabel(meth, i, start) + \" of \" + callDisplay(meth, fname))\n            if i > 0 { consume(a) }\n            argOrderCheck(tbase, fname, meth, i, argLine, argCol)\n"),
    "methods: an owned receiver through a handle is allowed": (
        "        if m.fnRec.pmodes[0] == 0 && recv.viaHandle && recv.mode == \"reg\" {\n",
        "        if false {\n"),
    "methods: every receiver is read-only": (
        "    if isMethod { append(params, new Param{name: recvName, typ: recvType, mode: recvMode}) }\n",
        "    if isMethod { append(params, new Param{name: recvName, typ: recvType, mode: 1}) }\n"),
    "methods: a mut receiver's writability is not checked": (
        "            if f.fnRec.pmodes[i] == 2 && !b.writable {\n",
        "            if false {\n"),
    "methods: the receiver is not parameter 0": (
        "    if isMethod { append(params, new Param{name: recvName, typ: recvType, mode: recvMode}) }\n",
        "    if false { append(params, new Param{name: recvName, typ: recvType, mode: recvMode}) }\n"),
    "methods: the receiver is not held across later arguments": (
        "            argOrderCheck(tbase, fname, meth, i, argLine, argCol)\n            holdArg(b, i)\n",
        "            argOrderCheck(tbase, fname, meth, i, argLine, argCol)\n            if i > start { holdArg(b, i) }\n"),
    "methods: a mut receiver's invalidation is dropped": (
        "        if argModes[i] == 2 && mutFrees(f, argIdx[i]) {\n",
        "        if argModes[i] == 2 && argIdx[i] != 0 && mutFrees(f, argIdx[i]) {\n"),
    "methods: the writer omits the receiver summary": (
        "    if f.fnRec.pfree != 0 {\n",
        "    if f.fnRec.pfree != 0 && !isMethod(f) {\n"),
    "methods: the receiver is not finalized": (
        "    if x.sym == nil && root != nil {\n        x.root = root\n",
        "    if x.sym == nil && root != nil && !isCall {\n        x.root = root\n"),
    "methods: isCall does not mark a receiver read": (
        "    if s.isRefLocal && (!target || path != \"\" || isCall) {        // a read, not a re-pointing: see loopBindCheck\n",
        "    if s.isRefLocal && (!target || path != \"\") {        // a read, not a re-pointing: see loopBindCheck\n"),
    "methods: a method call has no value": (
        "    if isCall { return methodCall(x, callName, callLine, callCol, uline, ucol) }\n",
        "    if isCall {\n        methodCall(x, callName, callLine, callCol, uline, ucol)\n        return voidItem()\n    }\n"),
    "methods: the owned-handle test uses path": (
        "        if m.fnRec.pmodes[0] == 0 && recv.viaHandle && recv.mode == \"reg\" {\n",
        "        if m.fnRec.pmodes[0] == 0 && recv.viaHandle && recv.path == \"\" {\n"),
    "methods: the field-call check is dropped": (
        "    if t.kind == \"record\" && fieldIndex(t, name) >= 0 {\n",
        "    if false {\n"),
    "methods: the while lift ignores method fixups": (
        "                if m.owner == curModuleName && m.addr < 0 {\n",
        "                if false {\n"),
    "methods: the retry cleanup ignores methods": (
        "            if m.addr < 0 {\n",
        "            if false {\n"),
    "methods: fnBody names a method by its bare name": (
        "    if isMethod { dname = methodDisplay(f) }\n",
        "    if false { dname = methodDisplay(f) }\n"),
    "methods: returnStat names a method by its bare name": (
        "        if sym != T_SEMI && sym != T_RBRACE { error(callableName(f) + \" does not return a value\") }\n",
        "        if sym != T_SEMI && sym != T_RBRACE { error(f.name + \" does not return a value\") }\n"),
    "methods: tryExpr names a method by its bare name": (
        "        errorAt(\"try needs a function that returns a Result; '\" + callableName(fr) + \"' returns \" + what, tl, tc)\n",
        "        errorAt(\"try needs a function that returns a Result; '\" + fr.name + \"' returns \" + what, tl, tc)\n"),
    "methods: the writer omits the method group": (
        "        if m.owner == modName && m.exported { appMethod(b, m, modName) }\n",
        "        if false { appMethod(b, m, modName) }\n"),
    "methods: the writer emits private methods": (
        "        if m.owner == modName && m.exported { appMethod(b, m, modName) }\n",
        "        if m.owner == modName { appMethod(b, m, modName) }\n"),
    "methods: lookup ignores the receiver type": (
        "fn findMethodIn(t: ^Type, name: string): ?^Sym {\n    for i in 0..len(methodSyms) {\n        let m = methodSyms[i]\n        if m.recv == t && m.name == name { return m }\n",
        "fn findMethodIn(t: ^Type, name: string): ?^Sym {\n    for i in 0..len(methodSyms) {\n        let m = methodSyms[i]\n        if m.name == name { return m }\n"),
    "methods: the call visibility check is dropped": (
        "    if t.owner != \"\" && t.owner != curModuleName && (m == nil || !m.exported) {\n",
        "    if false {\n"),
    "methods: the reader accepts any receiver type": (
        "        if recvType.kind != \"record\" && recvType.kind != \"closed\" {\n",
        "        if false {\n"),
    "methods: a method/field collision is allowed": (
        "    for i in 0..len(t.fields) {\n        if t.fields[i].name == name {\n            error(\"type '\" + tname(t) + \"' already has a field '\" + name + \"'; a method cannot have the same name\")\n        }\n    }\n",
        "    for i in 0..len(t.fields) {\n        if false {\n            error(\"type '\" + tname(t) + \"' already has a field '\" + name + \"'; a method cannot have the same name\")\n        }\n    }\n"),
    "methods: a forward heading's signature is ignored": (
        "                for i in 0..len(params) {\n                    if !same(f.fnRec.params[i], params[i].typ) || f.fnRec.pmodes[i] != params[i].mode { ok = false }\n                }\n            }\n            if !ok { error(\"method '\" + methodKey(prior) + \"' does not match its forward heading\") }\n",
        "                for i in 0..len(params) {\n                }\n            }\n            if !ok { error(\"method '\" + methodKey(prior) + \"' does not match its forward heading\") }\n"),
    "methods: the module-end method heading check is dropped": (
        "\n        if m.owner == curModuleName && m.addr < 0 {\n",
        "\n        if false {\n"),
    "methods: checkExports skips methods": (
        "        if m.owner == curModuleName && m.exported {\n            truncate(closureSeen, 0)\n",
        "        if false {\n            truncate(closureSeen, 0)\n"),
    "methods: closureSeen is not truncated per method": (
        "        if m.owner == curModuleName && m.exported {\n            truncate(closureSeen, 0)\n            for p in 0..len(m.fnRec.params)",
        "        if m.owner == curModuleName && m.exported {\n            for p in 0..len(m.fnRec.params)"),
    "methods: the reader's main check is not gated on a method": (
        "    if !isMethod && name == \"main\" {\n",
        "    if name == \"main\" {\n"),
    "methods: the reader drops a mut receiver's mode": (
        "        if pmut {\n            if !isref { error(\"mut needs a reference parameter: write mut p0: &T\") }\n            recvMode = 2\n        } else if isref {\n",
        "        if pmut {\n            if !isref { error(\"mut needs a reference parameter: write mut p0: &T\") }\n            recvMode = 1\n        } else if isref {\n"),
    "methods: the writer drops a mut receiver's mode": (
        "    if f.fnRec.pmodes[0] == 2 { appStr(b, \"mut \") }\n",
        "    if false { appStr(b, \"mut \") }\n"),
    "methods: the format check accepts any format": (
        "    if fmt != SYM_FORMAT {\n",
        "    if false {\n"),
    "methods: a method call on a built-in type has no methods check": (
        "    if t.kind != \"record\" && t.kind != \"closed\" { errorAt(tname(t) + \" has no methods\", dline, dcol) }\n",
        "    if false { errorAt(tname(t) + \" has no methods\", dline, dcol) }\n"),
    "methods: the method-value message is dropped": (
        "                if mv != nil && (mv.owner == curModuleName || mv.exported) {\n                    error(\"method '\" + methodDisplay(mv) + \"' cannot be used as a value: call it with '(' after its name\")\n                }\n",
        "                if mv != nil && (mv.owner == curModuleName || mv.exported) {\n                }\n"),
    "methods: a handle receiver may go to a freeing method": (
        "            if b.roots[p] == handleSym && !pureFn(f) {\n",
        "            if b.roots[p] == handleSym && !pureFn(f) && false {\n"),
    "methods: the mode-0 method order check is dropped": (
        "    if !symSameList(symMethodOrder, liveMethods) { symDisagree(\"the method order\") }\n",
        "    if false { symDisagree(\"the method order\") }\n"),
    "methods: the mode-0 missing method check is dropped": (
        "        if lm == nil { symDisagree(what) }\n",
        "        if false { symDisagree(what) }\n"),
    "methods: the mode-0 method signature check is dropped": (
        "            if !same(lm.ret, ret) { symDisagree(what) }\n            if lm.freesAll != freesAll || lm.writesHandles != writesHandles { symDisagree(what) }\n",
        "            if false { symDisagree(what) }\n            if lm.freesAll != freesAll || lm.writesHandles != writesHandles { symDisagree(what) }\n"),
}

# interfaces v3 step 2 (design section 12.2, encoding and version rows): the writer, the reader and the
# version constants. Each pattern occurs once in src/obo.obo; a stale pattern is a missed verdict.
INTERFACE_MUTANTS = {
    "interfaces v3 X25: an interface gets a shell line": (
        "    return symDefines(s, modName) && symType(s).kind != \"iface\"\n",
        "    return symDefines(s, modName)\n"),
    "interfaces v3 X26: the writer drops mut on a member parameter": (
        "        if i > 1 { appStr(b, \", \") }\n        if f.fnRec.pmodes[i] == 2 { appStr(b, \"mut \") }\n",
        "        if i > 1 { appStr(b, \", \") }\n        if false { appStr(b, \"mut \") }\n"),
    "interfaces v3 X27: the writer drops a member return type": (
        "        if m.ret != tVoid {\n            appStr(b, \": \")\n            appTypeExpr(b, m.ret, modName)\n",
        "        if false {\n            appStr(b, \": \")\n            appTypeExpr(b, m.ret, modName)\n"),
    "interfaces v3 X28: the reader parses an owned interface parameter as mode 1": (
        "                append(pmodes, 0)\n            }\n            append(ptypes, pt)\n",
        "                if pt.kind == \"iface\" { append(pmodes, 1) } else { append(pmodes, 0) }\n            }\n            append(ptypes, pt)\n"),
    "interfaces v3 X29: the reader accepts mut on an owned parameter": (
        "                if pmut { error(\"mut needs a reference parameter: write mut p0: &T\") }\n                append(pmodes, 0)\n            }\n            append(ptypes, pt)\n",
        "                append(pmodes, 0)\n            }\n            append(ptypes, pt)\n"),
    "interfaces v3 X30: the mode-0 comparison ignores parameter modes": (
        "if !same(lm.fnRec.params[k], ptypes[k]) || lm.fnRec.pmodes[k] != pmodes[k] { symDisagree(what) }",
        "if !same(lm.fnRec.params[k], ptypes[k]) { symDisagree(what) }"),
    "interfaces v3 X31: the source parser accepts mut on an interface parameter": (
        "            if mode == 0 { error(\"mut needs a reference parameter: write mut name: &T\") }\n",
        ""),
    # the implementation tier check (step 1, design 7.2 and 12.4): each conjunct
    "interfaces v3 X12: the local tier drops the writesHandles check": (
        "            if cm.writesHandles { error(tier + \"it writes an owned value through a handle, or calls a function that is not defined yet\" + rule) }\n",
        "            if false { error(tier + \"it writes an owned value through a handle, or calls a function that is not defined yet\" + rule) }\n"),
    "interfaces v3 X13: the local tier drops the freesAll check": (
        "            if cm.freesAll {\n",
        "            if false {\n"),
    "interfaces v3 X14: the local tier drops the freed-globals check": (
        "            if len(cm.fnRec.freed) > 0 {\n",
        "            if false {\n"),
    "interfaces v3 X15: the local tier drops the pfree-subset check": (
        "            if (cm.fnRec.pfree & ~m.fnRec.pfree) != 0 {\n",
        "            if false {\n"),
    "interfaces v3 X16: a pure member is checked with the local tier": (
        "        if m.pureMember {\n            if !pureFn(cm) || cm.writesHandles {\n",
        "        if false {\n            if !pureFn(cm) || cm.writesHandles {\n"),
    "interfaces v3 X17: a local member is checked with the pure tier": (
        "        if m.pureMember {\n            if !pureFn(cm) || cm.writesHandles {\n",
        "        if true {\n            if !pureFn(cm) || cm.writesHandles {\n"),
    # X38: each signature comparison the implementation check makes
    "interfaces v3 X38a: the receiver-mode comparison is dropped": (
        "        if cm.fnRec.pmodes[0] != m.fnRec.pmodes[0] {\n",
        "        if false {\n"),
    "interfaces v3 X38b: the argument-count comparison is dropped": (
        "        if len(cm.fnRec.params) != len(m.fnRec.params) {\n",
        "        if false {\n"),
    "interfaces v3 X38c: the argument-type comparison is dropped": (
        "            if !same(cm.fnRec.params[i], m.fnRec.params[i]) {\n",
        "            if false {\n"),
    "interfaces v3 X38d: the argument-mode comparison is dropped": (
        "            if cm.fnRec.pmodes[i] != m.fnRec.pmodes[i] {\n",
        "            if false {\n"),
    "interfaces v3 X38e: the return-type comparison is dropped": (
        "        if !same(cm.ret, m.ret) {\n",
        "        if false {\n"),
    # owned conversions and ownership (step 3, design section 12.4)
    "interfaces v3 X1: the box descriptor owns nothing": (
        "        append(ptrs, 0)\n",
        ""),
    "interfaces v3 X2: a conversion does not consume its source": (
        "    consume(x)\n    let obj = asReg(copyItem(x))\n",
        "    let obj = asReg(copyItem(x))\n"),
    "interfaces v3 X3: a variant is boxed with its own vtable, not the parent's": (
        "    if p != nil { im = findImplIn(p, it) } else { im = findImplIn(rt, it) }\n",
        "    im = findImplIn(rt, it)\n"),
    "interfaces v3 X4: nil converted to ?I allocates a box": (
        "    if assignable(dst, x.typ) { return }\n    if isIfaceVal(dst) {\n",
        "    if assignable(dst, x.typ) {\n        if isIfaceVal(dst) && x.typ.kind == \"nil\" { setItem(x, rtCall1(RT_ALLOC_RECORD, makeConst(tInt, boxDescriptor()), dst)) }\n        return\n    }\n    if isIfaceVal(dst) {\n"),
    "interfaces v3 X35: replace on an interface place does not return the old value": (
        "        store(x, v)\n        return makeReg(xt, r)\n",
        "        store(x, v)\n        if isIfaceVal(xt) { return makeConst(xt, 0) }\n        return makeReg(xt, r)\n"),
    # step 6: the equality, handle and global-initializer guards (design sections 5.4, 6.2, 6.3 and 12.4)
    "interfaces v3 X32: relation lets interface values through": (
        "    if (isIfaceVal(x.typ) || isIfaceVal(y.typ)) && kx != \"nil\" && ky != \"nil\" {\n        error(\"interface values cannot be compared; test an optional with '== nil' or '!= nil'\")\n    }\n",
        "    if false {\n        error(\"interface values cannot be compared; test an optional with '== nil' or '!= nil'\")\n    }\n"),
    "interfaces v3 handle refusal 0.8: a handle names an interface value": (
        "        if isIfaceVal(x.typ) { error(\"handles name records and closed types, not \" + tname(x.typ)) }\n",
        "        if false { error(\"handles name records and closed types, not \" + tname(x.typ)) }\n"),
    "interfaces v3 global-init call guard: an interface method call is accepted in a global initializer": (
        "    if curFn == nil {\n        errorAt(\"an interface method cannot be called in a global initializer; call a function that does it, as in var G = initG()\", dline, dcol)\n    }\n",
        "    if false {\n        errorAt(\"an interface method cannot be called in a global initializer; call a function that does it, as in var G = initG()\", dline, dcol)\n    }\n"),
    # borrowed interfaces (step 5, design sections 4, 6.4 and 12.4): B1's pair copy at a let, at a re-point and
    # in the vtable word; a reference local that is not registered; the &?I local refusal; the global cell
    "interfaces v3 X5: a &I local does not copy its pair (it keeps the source pointer)": (
        "            makeRefLocal(v, nb)\n            if pair { storePair(dst, x) } else { store(dst, x) }\n",
        "            makeRefLocal(v, nb)\n            if pair { store(dst, x) } else { store(dst, x) }\n"),
    "interfaces v3 X6: re-pointing a &I local stores a pointer, not a pair copy": (
        "            if x.pair { storePair(x, y) } else { store(x, y) }\n",
        "            if x.pair { store(x, y) } else { store(x, y) }\n"),
    "interfaces v3 X7: a &I let copies obj but writes the vtable over it": (
        "    opMem(0x89, hi, mem(RBP, -1, dst.disp + 8, 8), true, false)\n",
        "    opMem(0x89, hi, mem(RBP, -1, dst.disp, 8), true, false)\n"),
    "interfaces v3 X11: a &I local is not registered as a reference local": (
        "            makeRefLocal(v, nb)\n",
        "            if rt.kind != \"iface\" { makeRefLocal(v, nb) } else { v.isRef = true }\n"),
    "interfaces v3 X33: a &?I local is accepted": (
        "fn refuseOptionalIfaceLocal(t: ^Type) {\n    if t.kind == \"opt\" && baseOf(t).kind == \"iface\" {\n",
        "fn refuseOptionalIfaceLocal(t: ^Type) {\n    if false {\n"),
    "interfaces v3 X34: a cell is built in a global initializer": (
        "    if curFn == nil {\n        error(what + \": cannot create a borrowed interface value in a global initializer: pass the concrete value to a function that makes it\")\n    }\n",
        "    if false {\n        error(what + \": cannot create a borrowed interface value in a global initializer: pass the concrete value to a function that makes it\")\n    }\n"),
    "interfaces v3 X9: a temporary may be borrowed into a cell": (
        "    if x.mode == \"reg\" && isOwned(x.typ) {\n        error(\"store this value in a variable first: a reference cannot borrow from a temporary\")\n    }\n",
        "    if false && x.mode == \"reg\" && isOwned(x.typ) {\n        error(\"store this value in a variable first: a reference cannot borrow from a temporary\")\n    }\n"),
    "interfaces v3 X36a: SYM_FORMAT stays 2": ("const SYM_FORMAT = 4\n", "const SYM_FORMAT = 2\n"),
    "interfaces v3 X36b: OBO_VERSION stays 0.6": ("const OBO_VERSION = \"0.8\"\n", "const OBO_VERSION = \"0.6\"\n"),

    # summary reconstruction (S-2): setMemberSummary is shared by the source and the reader
    "interfaces v3 X18: the receiver bit is omitted for mut fn": (
        "        if i < 62 && m.fnRec.pmodes[i] == 2 { m.fnRec.pfree = m.fnRec.pfree | (1 << i) }\n",
        "        if i > 0 && i < 62 && m.fnRec.pmodes[i] == 2 { m.fnRec.pfree = m.fnRec.pfree | (1 << i) }\n"),
    "interfaces v3 X19: the bits go on mode-1 parameters too": (
        "        if i < 62 && m.fnRec.pmodes[i] == 2 { m.fnRec.pfree",
        "        if i < 62 && m.fnRec.pmodes[i] == 1 { m.fnRec.pfree"),
    "interfaces v3 X20: only the first mode-2 parameter gets its bit": (
        "        if i < 62 && m.fnRec.pmodes[i] == 2 { m.fnRec.pfree",
        "        if i < 62 && m.fnRec.pmodes[i] == 2 && m.fnRec.pfree == 0 { m.fnRec.pfree"),
    "interfaces v3 X21: writesHandles is set on every member": (
        "    m.freesAll = false\n    m.writesHandles = false\n    m.fnRec.pfree = 0\n",
        "    m.freesAll = false\n    m.writesHandles = true\n    m.fnRec.pfree = 0\n"),
    "interfaces v3 X22: a pure mut member gets the receiver bit": (
        "    if pure { return }\n    for i in 0..len(m.fnRec.pmodes) {\n",
        "    if pure && m.fnRec.pmodes[0] != 2 { return }\n    for i in 0..len(m.fnRec.pmodes) {\n"),
    "interfaces v3 X23: the reader ignores pure": (
        "            setMemberSummary(m, pure)\n            append(ifaceSyms, m)\n",
        "            setMemberSummary(m, false)\n            append(ifaceSyms, m)\n"),
    "interfaces v3 X24: the reader ignores a mut mode on a later parameter": (
        "                if pmut { append(pmodes, 2) } else { append(pmodes, 1) }\n            } else {\n                if pmut { error(\"mut needs a reference parameter: write mut p0: &T\") }\n",
        "                if pmut && len(pmodes) < 2 { append(pmodes, 2) } else { append(pmodes, 1) }\n            } else {\n                if pmut { error(\"mut needs a reference parameter: write mut p0: &T\") }\n"),
    # step 4: dispatch through an owned interface value (design sections 7 and 11): the call's vtable word, the
    # slot offset, and the caller's effects from the member's synthetic summary
    "interfaces v3 dispatch extra: the vtable word is read from the wrong stack slot": (
        "        opMem(0x8B, RAX, mem(RSP, -1, 8 * n, 8), true, false)\n",
        "        opMem(0x8B, RAX, mem(RSP, -1, 8 * n - 8, 8), true, false)\n"),
    # a design-ID-free name: this is an extra dispatch mutation, not one of X1-X38
    "interfaces v3 dispatch: the call goes to the wrong vtable slot": (
        "mem(RAX, -1, 8 * ifaceSlot(f), 8)",
        "mem(RAX, -1, 8 * ifaceSlot(f) + 8, 8)"),
    "interfaces v3 dispatch: the member's summary is not applied to the caller's receiver": (
        "        if argModes[i] == 2 && mutFrees(f, argIdx[i]) {\n",
        "        if argModes[i] == 2 && mutFrees(f, argIdx[i]) && !iface {\n"),
}

# a mutation whose behaviour needs more than one replacement, each of which must occur exactly once
MULTI_MUTANTS = {
    # S4, two sites: the actual reads of the receiver's obj and vtable happen after every explicit
    # argument has run. The box pointer itself is saved as argument 0 before the arguments, and the
    # vtable position is reserved, so the call layout and dispatch words stay valid.
    "interfaces v3 X8: the receiver's obj and vtable are loaded after the arguments (S4)": [
        ("        if iface && i == 0 {\n            pushIfaceReceiver(a)                   // the box's vtable and object, before any explicit argument runs (v3 7.1)\n",
         "        if iface && i == 0 {\n            let pb = asReg(a)\n            pushI(0)                               // S4 mutant: the vtable slot, filled after the arguments run\n            depth += 8\n            pushR(pb.r)                            // argument 0: the box pointer; its two words are read after the arguments\n            release(pb.r)\n"),
        ("            release(r.r)\n        }\n    }\n    releaseArgs(tbase, omark)\n",
         "            release(r.r)\n        }\n    }\n    if iface {\n        // S4 mutant: read the receiver's obj and vtable only after every explicit argument has run\n        let pb = alloc()\n        opMem(0x8B, pb, mem(RSP, -1, 8 * (n - 1), 8), true, false)\n        let ob = alloc()\n        opMem(0x8B, ob, mem(pb, -1, 0, 8), true, false)\n        opMem(0x8B, pb, mem(pb, -1, 8, 8), true, false)\n        opMem(0x89, pb, mem(RSP, -1, 8 * n, 8), true, false)\n        opMem(0x89, ob, mem(RSP, -1, 8 * (n - 1), 8), true, false)\n        release(ob)\n        release(pb)\n    }\n    releaseArgs(tbase, omark)\n"),
    ],
    # S4 extras, two sites: both words are loaded before the arguments, but with their roles exchanged,
    # which no single-site replacement can express. These are corruption faults, not load-order faults.
    "interfaces v3 dispatch extra: the receiver's obj and vtable words are swapped (two sites)": [
        ("    opMem(0x8B, vt, mem(b.r, -1, 8, 8), true, false)\n",
         "    opMem(0x8B, vt, mem(b.r, -1, 0, 8), true, false)\n"),
        ("    opMem(0x8B, ob, mem(b.r, -1, 0, 8), true, false)\n",
         "    opMem(0x8B, ob, mem(b.r, -1, 8, 8), true, false)\n"),
    ],
}
MUTANTS.update(INTERFACE_MUTANTS)
MUTANTS.update(METHOD_MUTANTS)

def apply_edits(src, edits):
    """Apply replacements in order; each must match exactly once. Returns
    (text, None), or (None, the stale message) for a pattern that no longer
    matches, which the gate reports as a miss rather than skipping the mutant."""
    text = src
    multi = len(edits) > 1
    for k, (old, new) in enumerate(edits, 1):
        n = text.count(old)
        if n != 1:
            where = f" (site {k} found {n} times)" if multi else f" (pattern found {n} times)"
            return None, f"STALE  {where}"
        text = text.replace(old, new)
    return text, None


def mutation(src, name):
    """The mutant's source text, or (None, the stale-pattern message) when its
    pattern no longer matches once. A stale pattern is a 'missed' verdict: the
    gate fails rather than skipping the mutant. A MULTI_MUTANTS entry applies every
    replacement in order, each of which must match exactly once."""
    edits = MULTI_MUTANTS[name] if name in MULTI_MUTANTS else [PORT_PATTERNS.get(name, MUTANTS[name])]
    text, stale = apply_edits(src, edits)
    if stale is not None:
        return None, f"STALE   {name}{stale[len('STALE  '):]}"
    return text, None


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


def build_and_run(src, fail_fast=False, jobs=1, smoke=False, stats=None):
    """Builds the compiler from src, in a directory of its own, and runs run_fork.py
    with it: (the run_fork result, None), or (None, the build's error) when it does
    not build. fail_fast stops run_fork at its first failing test (the verdict is
    unchanged; only the time and the report differ), jobs tests run at once, and
    smoke tries run_fork's smoke subset first (a reorder: the rest still run).

    run_fork already includes the module and .sym suites, so they are not run
    again here; a .sym reader's mutant is still seen by run_fork's own cases."""
    work = tempfile.mkdtemp(prefix='obo-mutant-')
    try:
        with open(os.path.join(work, 'obo.obo'), 'w') as f:
            f.write(src)
        exe = os.path.join(work, 'obo')
        t = time.perf_counter()
        if stats is not None:
            stats.add('subprocesses')
        b = subprocess.run([os.path.join(FORK, 'seed', 'obo'), os.path.join(work, 'obo.obo'), '-o', exe, '--rt', FORK],
                           capture_output=True, text=True)
        if stats is not None:
            stats.set('build_seconds', round(time.perf_counter() - t, 3))
            stats.add('builds')
        if b.returncode != 0:
            return None, b.stderr.strip()[:120]
        cmd = [sys.executable, os.path.join(HERE, 'run_fork.py')]
        if fail_fast:
            cmd.append('-x')
        if jobs > 1:
            cmd += ['-j', str(jobs)]
        if smoke:
            cmd.append('--smoke')
        t = time.perf_counter()
        if stats is not None:
            stats.add('subprocesses')
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=600, env=dict(os.environ, OBO=exe))
        if stats is not None:
            stats.set('suite_seconds', round(time.perf_counter() - t, 3))
            stats.add('suites')
            m = re.search(r'(\d+) passed, (\d+) failed(?:, (\d+) not run)?', res.stdout)
            if m:
                stats.set('cases_passed', int(m.group(1)))
                stats.set('cases_failed', int(m.group(2)))
                stats.set('cases_not_run', int(m.group(3) or 0))
        return res, None
    finally:
        shutil.rmtree(work, ignore_errors=True)


def try_mutant(src, name, jobs=1, stats=None):
    """The verdict on one mutant: (status, line, output), status being 'caught', 'missed' or 'equiv'.
    Mutants run side by side, each in a build directory of its own. The suite stops at
    the first failing test: a suite with one failing test fails, so the verdict holds.
    The smoke subset runs first; a failure there is a failure of the full suite, and a
    pass still runs every other test, so 'missed' always means the full suite passed.
    stats, when given, collects the build and suite seconds and counts."""
    text, stale = mutation(src, name)
    if text is None:
        return 'missed', stale, ''
    p, err = build_and_run(text, fail_fast=True, jobs=jobs,
                           smoke=os.environ.get('MUTATE_SMOKE', '1') != '0', stats=stats)
    if p is None:
        return 'missed', f"BROKEN  {name}: {err}", ''
    lines = p.stdout.strip().splitlines()
    detail = next((l.strip() for l in lines if l.startswith('     ')), '')
    caught = p.returncode != 0
    if stats is not None:
        stats.set('exit_code', p.returncode)
        stats.set('first_failure', next((l.strip() for l in lines if l.startswith('FAIL')), None))
        m = re.search(r'(\d+) passed, (\d+) failed(?:, (\d+) not run)?', p.stdout)
        if m:
            stats.set('cases_passed', int(m.group(1)))
            stats.set('cases_failed', int(m.group(2)))
            stats.set('cases_not_run', int(m.group(3) or 0))
    if not caught and name in EQUIVALENT:
        if stats is not None:
            stats.set('verdict', 'equiv')
        return 'equiv', f"EQUIV   {name}: {EQUIVALENT[name]}", ''
    early = " (smoke)" if "(in the smoke subset)" in p.stdout else ""
    line = f"{'caught' if caught else 'MISSED'}  {name:<36} {lines[-1].split(' in ')[0] if lines else ''}  {detail[:60]}{early}"
    if stats is not None:
        stats.set('verdict', 'caught' if caught else 'missed')
        stats.set('transient', bool(caught and TRANSIENT.search(p.stdout + p.stderr)))
        stats.set('smoke_hit', bool(early))
    return ('caught' if caught else 'missed'), line, p.stdout + p.stderr


def timing_inputs():
    runtime = {}
    for name in ('runtime.c', 'entry.S', 'rt.ld'):
        runtime[name] = mutate_stats.sha256_file(os.path.join(FORK, 'runtime', name))
    seed = os.path.join(FORK, 'seed', 'obo')
    try:
        p = subprocess.run([seed, '--version'], capture_output=True, text=True, timeout=10)
        seed_version = p.stdout.strip() if p.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired):
        seed_version = None
    return {
        'source': mutate_stats.sha256_file(os.path.join(FORK, 'src', 'obo.obo')),
        'seed': mutate_stats.sha256_file(seed),
        'seed_version': seed_version,
        'runtime': runtime,
        'tests': mutate_stats.dir_digest(HERE),
        'harness': {
            'mutate_fork.py': mutate_stats.sha256_file(os.path.abspath(__file__)),
            'run_fork.py': mutate_stats.sha256_file(os.path.join(HERE, 'run_fork.py')),
            'mutate_stats.py': mutate_stats.sha256_file(os.path.join(HERE, 'mutate_stats.py')),
        },
    }


def main():
    run_start = time.perf_counter()
    args = sys.argv[1:]
    options = [a for a in args if a.startswith('-')]
    if options:
        raise SystemExit(f"unknown option {options[0]!r}: the script takes a mutant-name pattern only")
    pattern = args[0] if args else ''
    src = open(os.path.join(FORK, 'src', 'obo.obo')).read()
    sink = mutate_stats.Sink('mutate-fork-timings')
    if sink.path:
        print(f"note: timings go to {sink.path}", file=sys.stderr)
        sink.write(mutate_stats.header('mutate_fork', timing_inputs(),
                                       {'git_head': mutate_stats.git_head(FORK)}))
    # the baseline: the unmutated compiler must pass run_fork, or no mutant's failure means anything
    base_stats = mutate_stats.Stats()
    t = time.perf_counter()
    base, err = build_and_run(src, stats=base_stats)
    base_stats.set('wall_seconds', round(time.perf_counter() - t, 3))
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
    sink.write({'kind': 'baseline', 'verdict': 'passed', **base_stats.snapshot()})
    jobs, inner = jobs_and_inner()
    # ... and the same judge path a mutant takes must pass it too, with the full
    # count: a broken invocation would otherwise report every mutant as caught
    smoke = os.environ.get('MUTATE_SMOKE', '1') != '0'
    judge_stats = mutate_stats.Stats()
    t = time.perf_counter()
    judge, err = build_and_run(src, fail_fast=True, jobs=inner, smoke=smoke, stats=judge_stats)
    judge_stats.set('wall_seconds', round(time.perf_counter() - t, 3))
    if judge is None:
        raise SystemExit(f"the unmutated compiler does not build ({err}), so no mutant can be judged")
    judge_summary = judge.stdout.strip().splitlines()[-1] if judge.stdout.strip() else ''
    if judge.returncode != 0 or not judge_summary.startswith(f"{passed.group(1)} passed, 0 failed"):
        print(judge.stdout.rstrip())
        raise SystemExit(f"run_fork -x{' --smoke' if smoke else ''} -j {inner} does not pass the unmutated "
                         "compiler (above), so a mutant's failure could not be trusted")
    sink.write({'kind': 'judge', 'verdict': 'passed', **judge_stats.snapshot()})
    names = [n for n in list(MUTANTS) + list(MULTI_MUTANTS) if pattern in n]

    def work(name):
        st = mutate_stats.Stats()
        t = time.perf_counter()
        result = try_mutant(src, name, inner, stats=st)
        st.set('wall_seconds', round(time.perf_counter() - t, 3))
        st.set('jobs', jobs)
        st.set('inner_jobs', inner)
        st.set('cpus', os.cpu_count())
        return name, result, st

    results = []
    with concurrent.futures.ThreadPoolExecutor(jobs) as pool:      # each mutant is a separate build and suite run
        for name, (s, line, out), st in pool.map(work, names):
            print(line, flush=True)
            results.append([name, s, line, out, st])
    # a caught mutant whose failure is a timeout or an out-of-memory may be the load, not the mutant:
    # run it again alone, and report that result
    for record in results:
        name, s, line, out, st = record
        if s == 'caught' and TRANSIENT.search(out):
            retry = mutate_stats.Stats()
            t = time.perf_counter()
            s2, line2, out2 = try_mutant(src, name, stats=retry)
            retry.set('wall_seconds', round(time.perf_counter() - t, 3))
            retry.set('jobs', 1)
            retry.set('inner_jobs', 1)
            retry.set('cpus', os.cpu_count())
            retry.set('retry_of', 'parallel')
            print(f"{line2}   (serial re-run: the parallel run timed out or ran out of memory)", flush=True)
            record[:] = [name, s2, line2, out2, retry]
    for record in results:
        name, s, line, out, st = record
        sink.write({'kind': 'mutant', 'name': name, 'verdict': s, 'line': line, **st.snapshot()})
    status = {record[0]: record[1] for record in results}
    missed = [n for n in names if status[n] == 'missed']
    equiv = sum(1 for n in names if n in EQUIVALENT)
    sink.write({'kind': 'run', 'wall_seconds': round(time.perf_counter() - run_start, 3),
                'mutants': len(names), 'missed': len(missed), 'equivalent_named': equiv,
                'caught_status': sum(1 for record in results if record[1] == 'caught'),
                'equiv_status': sum(1 for record in results if record[1] == 'equiv'),
                'jobs': jobs, 'inner_jobs': inner})
    print(f"\n{len(names) - len(missed) - equiv}/{len(names)} mutants caught, {equiv} equivalent")
    sys.exit(1 if missed else 0)


if __name__ == '__main__':
    main()

