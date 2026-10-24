#!/usr/bin/env python3
"""Mutation testing for obo: plants ownership bugs in a copy of src/obo.obo, builds
each with the seed compiler, and checks that the suite fails. Bugs that free
too little must be caught by the leak check, bugs that free too much by the
double-free check or poisoning, and missing compile-time checks by the error
tests.

    python3 obo/tests/mutate_fork.py [pattern]"""
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
FORK = os.path.dirname(HERE)

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
    "callees never free globals": ("    if f.addr < 0 || f == curFn || f.freesAll { return true }\n    let freed = &f.freed",
                                   "    if false { return true }\n    let freed = &f.freed"),
    "frees not recorded": ('    if g.storage != "global" { return }\n    let freed = &f.freed',
                           '    if true { return }\n    let freed = &f.freed'),
    "loop moves unchecked": ("    if !term {\n        let now = snapshot()", "    if false {\n        let now = snapshot()"),
    "string locals never dropped": ("            if s.isStr {\n                rtCall1(RT_STR_DEC, x, tVoid)",
                                    "            if s.isStr {\n                rtCall1(RT_STR_INC, x, tVoid)"),
    "dropped results kept": ("            if isOwned(x.typ) {\n                rtCall1(RT_FREE, x, tVoid)                 // a dropped result is freed at once",
                             "            if false {\n                rtCall1(RT_FREE, x, tVoid)                 // a dropped result is freed at once"),
}

MUTANTS = {
    # freeing too little: the leak check must notice
    "scope exit frees nothing": ("        callDefers(blk)\n        freeOwned(blk.omark, len(ownedLocals))\n",
                                 "        callDefers(blk)\n"),
    "early exits free nothing": ("        freeOwned(blocks[i].omark, top)\n", ""),
    "assignment keeps the old value": ("                if live {\n                    resolve(x)", "                if false {\n                    resolve(x)"),
    "dropped results kept": ("                rtCall1(RT_FREE, x, tVoid)                 // a dropped result is freed at once",
                             "                releaseItem(x)"),
    "records don't free their fields": ("        if isOwned(f.typ) { append(ptrs, f.offset) }            // the fields freed with the record", ""),
    "globals not freed at exit": ("            x.disp = g.offset\n            rtCall1(RT_FREE, x, tVoid)\n", "            x.disp = g.offset\n"),
    # freeing too much: double free or poisoned reads
    "moved locals still freed": ("        if !s.moved && !s.isRefLocal {\n            let x = newItem", "        if !s.isRefLocal {\n            let x = newItem"),
    "moves not recorded": ("        s.moved = true\n        s.touches = s.touches + 1\n        invalidate(s, nil, true, \"\")", "        s.touches = s.touches + 1\n        invalidate(s, nil, true, \"\")"),
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
    "read-only references can be passed as mut": ("            if f.pmodes[i] == 2 && !mutableOf(a) {", "            if false {"),
    "everything counts as an ancestor": ("        if p == a { return true }\n        p = p.refParent", "        return true"),
    # handles
    "handle borrows are untracked": ("    if x.mode == \"const\" { return nil }\n    if x.viaHandle { return handleSym }\n", "    if x.mode == \"const\" { return nil }\n    if x.viaHandle { return nil }\n"),
    # acceptance-equivalent: the general field-target check rejects the same programs,
    # so only the suite sees this, by the message text (the fuzzer compares acceptance only)
    "handle assignment across calls allowed": ("    if x.viaHandle {\n        errorAt(\"cannot assign through a handle across a call", "    if false {\n        errorAt(\"cannot assign through a handle across a call"),
    "handle index calls allowed": ("            if viaHandle && userCalls != callsBefore {\n                error(", "            if false {\n                error("),
    "match reads the tag with the generation": ("    opMem(0x8B, r, mem(r, -1, -16, 8), false, false)", "    opMem(0x8B, r, mem(r, -1, -16, 8), true, false)"),
    # effect summaries
    "callees never free globals": ("    if f.addr < 0 || f == curFn || f.freesAll { return true }\n    let freed = &f.freed", "    if false { return true }\n    let freed = &f.freed"),
    "calls invalidate nothing": ("            invalidate(g, nil, true, \"\")\n            if !all { recordFree(g) }", "            if !all { recordFree(g) }"),
    "frees not recorded": ("    if g.storage != \"global\" { return }\n    let freed = &f.freed", "    if true { return }\n    let freed = &f.freed"),
    "drop frees nothing": ("        consume(x)\n        rtCall1(RT_FREE, dx, tVoid)\n        return voidItem()", "        consume(x)\n        return voidItem()"),
    "replace keeps the old value in place": ("        let xt = x.typ\n        store(x, v)\n", "        let xt = x.typ\n        releaseItem(x)\n"),
    # compile-time checks
    "use after move allowed": ("                error(\"'\" + name + \"' was moved and has no value here\")\n            }\n        }",
                               "            }\n        }"),
    "branch states not compared": ("    let d = stateDiff(a, b)\n    if d != nil {\n        if d.isRefLocal {", "    let d = stateDiff(a, b)\n    if d != nil && false {\n        if d.isRefLocal {"),
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
    "compound targets not checked": ("            var y = expression()\n            pinned = savedPin\n            checkTargetAcrossCall(x, xs, r0, callsBefore, line, col)\n",
                                     "            var y = expression()\n            pinned = savedPin\n"),
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
    "plain assignments through a reference not checked for moves": ("            consume(y)\n            checkTargetAcrossCall(x, xs, r0, callsBefore, line, col)\n            if x.deref && r0 != nil && heldLost(r0, touch0) {",
                                                                     "            consume(y)\n            checkTargetAcrossCall(x, xs, r0, callsBefore, line, col)\n            if x.deref && r0 != nil && false {"),
    "compound assignments through a reference not checked for moves": ("            var y = expression()\n            pinned = savedPin\n            checkTargetAcrossCall(x, xs, r0, callsBefore, line, col)\n            if x.deref && r0 != nil && heldLost(r0, touch0) {",
                                                                       "            var y = expression()\n            pinned = savedPin\n            checkTargetAcrossCall(x, xs, r0, callsBefore, line, col)\n            if x.deref && r0 != nil && false {"),
    # the reference local's owner (ownerOf) and its invalidation (the moved flag): a reference into a
    # field or to an element's array, or a borrow through a handle, is only caught by one of them
    "reference roots not checked for moves or invalidation": ("fn heldLost(root: ?^Sym, t0: int): bool {\n    if root == nil { return false }\n    if root.isRefLocal && root.moved { return true }\n    return touched(ownerOf(root)) != t0\n}",
                                                              "fn heldLost(root: ?^Sym, t0: int): bool {\n    if root == nil { return false }\n    return !root.isRefLocal && touched(root) != t0\n}"),
    "reference invalidation not checked": ("    if root.isRefLocal && root.moved { return true }\n", ""),
    "moves not counted": ("        s.touches = s.touches + 1\n", ""),
    # finer paths (item 3): a change reaches only what lies under its place
    "field changes invalidate siblings": ("(all || v.refDeep && underPath(v.refPath, path))", "(all || v.refDeep)"),
    "known elements treated as one": ("(ri != rj || pathsOverlap(argPaths[i], argPaths[j]))", "(ri != rj || true)"),
}


def main():
    pattern = sys.argv[1] if len(sys.argv) > 1 else ''
    src = open(os.path.join(FORK, 'src', 'obo.obo')).read()
    work = tempfile.mkdtemp(prefix='obo-mutant-')
    missed = []
    names = [n for n in MUTANTS if pattern in n]
    for name in names:
        old, new = PORT_PATTERNS.get(name, MUTANTS[name])
        if src.count(old) != 1:
            print(f"STALE   {name} (pattern found {src.count(old)} times)")
            missed.append(name)
            continue
        with open(os.path.join(work, 'obo.obo'), 'w') as f:
            f.write(src.replace(old, new))
        exe = os.path.join(work, 'obo')
        b = subprocess.run([os.path.join(FORK, 'seed', 'obo'), os.path.join(work, 'obo.obo'), '-o', exe, '--rt', FORK],
                           capture_output=True, text=True)
        if b.returncode != 0:
            print(f"BROKEN  {name}: {b.stderr.strip()[:120]}")
            missed.append(name)
            continue
        p = subprocess.run([sys.executable, os.path.join(HERE, 'run_fork.py')], capture_output=True, text=True,
                           timeout=600, env=dict(os.environ, OBO=exe))
        lines = p.stdout.strip().splitlines()
        detail = next((l.strip() for l in lines if l.startswith('     ')), '')
        caught = p.returncode != 0
        print(f"{'caught' if caught else 'MISSED'}  {name:<36} {lines[-1].split(' in ')[0] if lines else ''}  {detail[:60]}")
        if not caught:
            missed.append(name)
    print(f"\n{len(names) - len(missed)}/{len(names)} mutants caught")
    sys.exit(1 if missed else 0)


if __name__ == '__main__':
    main()
