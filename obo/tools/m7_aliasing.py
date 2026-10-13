#!/usr/bin/env python3
"""Aliasing (group borrowing's core rule): several references, mut or not, may
name the same object; only a reference that may point inside something being
changed is invalidated.

- A change under a reference parameter may free inside another reference
  parameter or a global (the parameter may be either). A change under a global
  reaches no parameter: a function that might free under a global is never
  given references into it (the effect summaries, by induction over callers).
- At a call, a mut argument conflicts only with another argument that may point
  inside it (a deep one) from a root its changes reach. Two references to one
  object, both mut, are fine: neither can free the other's object.
This also closes a hole: a parameter that is a global the function borrows from."""
import os, sys
P = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'src', 'obo-on-obx.obx')
s = open(P).read()

def sub(old, new):
    global s
    assert s.count(old) == 1, f"expected exactly one match for:\n{old}"
    s = s.replace(old, new)

# 1. which roots a change can reach
sub("""// Something under root may have been freed: the root's references become
// invalid, except via (the reference the change went through) and, unless
// all, references to the root's own object.
fn invalidate(root: ?Sym, via: ?Sym, all: bool) {
    if root == nil { return }
    for v in ownedLocals {
        // a change through via frees only things below via: references it was
        // derived from point above it and stay valid
        if v.isRefLocal && v.refRoot == root && !ancestorOf(v, via) && (all || v.refDeep) && !v.moved {
            if v.iterating { error("cannot change what '" + v.name + "' points into while a for loop walks it") }
            v.moved = true
        }
    }
}
""", """// a reference parameter: it may name the same object as another one, or a global
fn isRefParam(s: Sym): bool { return s.isRef && !s.isRefLocal && s.storage == "local" }

// a change under root changed may free something inside root other. A change
// under a reference parameter reaches other reference parameters and globals,
// since it may be either; a change under a global reaches no parameter, since
// a function that might free under a global is never given references into it.
fn reaches(changed: Sym, other: Sym): bool {
    if changed == other { return true }
    return isRefParam(changed) && (isRefParam(other) || other.storage == "global")
}

// Something under root may have been freed: the root's references become
// invalid, except via (the reference the change went through) and, unless
// all, references to the root's own object; so do references that may point
// inside any other root the change reaches.
fn invalidate(root: ?Sym, via: ?Sym, all: bool) {
    if root == nil { return }
    for v in ownedLocals {
        // a change through via frees only things below via: references it was
        // derived from point above it and stay valid
        if v.isRefLocal && !ancestorOf(v, via) && !v.moved {
            let r = v.refRoot
            var hit = r == root && (all || v.refDeep)
            if !hit && r != nil && v.refDeep && reaches(root, r) { hit = true }
            if hit {
                if v.iterating { error("cannot change what '" + v.name + "' points into while a for loop walks it") }
                v.moved = true
            }
        }
    }
}
""")

# 2. remember whether each reference argument may point below its root
sub("""    let argVias = new []?Sym(0)
    for i in 0..n {""", """    let argVias = new []?Sym(0)
    let argDeeps = new []bool(0)
    for i in 0..n {""")
sub("""            append(argVias, viaOf(a))
        } else {""", """            append(argVias, viaOf(a))
            append(argDeeps, deepOf(a))
        } else {""")

# 3. the call rule
sub("""    // a mut reference and another reference into the same root could let the
    // callee free what the other points to
    for i in 0..len(argRoots) {
        for j in i + 1..len(argRoots) {
            let ri = argRoots[i]
            if ri != nil && ri == argRoots[j] && (argModes[i] == 2 || argModes[j] == 2) {
                error("cannot pass two references into '" + ri.name + "' to " + f.name + " when one of them is mut")
            }
        }
    }
""", """    // a mut reference and another that may point inside it could let the callee
    // free what the other points to; references to one object are fine
    for i in 0..len(argRoots) {
        for j in 0..len(argRoots) {
            let ri = argRoots[i]
            let rj = argRoots[j]
            if i != j && argModes[i] == 2 && argDeeps[j] && ri != nil && rj != nil && reaches(ri, rj) {
                error("argument " + string(j + 1) + " of " + f.name + " may point inside argument " + string(i + 1) + ", which is mut")
            }
        }
    }
""")
open(P, 'w').write(s)
print("patched", P)
