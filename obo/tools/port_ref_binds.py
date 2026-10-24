#!/usr/bin/env python3
"""Reference bindings per path: a reference local's binding is part of each path's
state, not one per symbol, so re-pointing it on one branch does not change it for
the others. A binding is a list of roots (each with deep, place and writable) and
a parent; at a join it is the union of the paths' roots. Replaces the flow-insensitive
refRoot/refDeep/refPath/refParent/refMut fields in src/obo.obo.
Every anchor must occur exactly once (sub) or as a unique start and end (region)."""
import os
P = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'src', 'obo.obo')
s = open(P).read()
# the segment helpers (segsPrefix and elemEnd) are moved up, so that joins below compile after them
SEG_START = s.index("// the index of the ']' that closes the element segment")
SEG_END = s.index("// the change at path p may free what")
SEG = s[SEG_START:SEG_END]
s = s[:SEG_START] + s[SEG_END:]

def sub(old, new, count=1):
    global s
    assert s.count(old) == count, f"expected {count} match(es), found {s.count(old)}, for:\n{old}"
    s = s.replace(old, new)

def region(start, end, new):
    """Replace the text from the unique start up to (not including) the unique end."""
    global s
    assert s.count(start) == 1, f"start not unique:\n{start}"
    assert s.count(end) == 1, f"end not unique:\n{end}"
    i = s.index(start)
    j = s.index(end)
    assert i < j, f"end before start:\n{start}"
    s = s[:i] + new + s[j:]

# ---------------------------------------------------------------- types and arena
sub("type Type\ntype Sym\n", "type Type\ntype Sym\ntype RefBind\ntype PState\n")

region("    refMut: bool", "    iterating: bool", """    bind: ^RefBind            // what a reference local points into, on the path being compiled
    fresh: bool               // pointed afresh since the innermost loop began, on every path to here
    staleUse: int            // useSeq of the last read that was not fresh (see loopBindCheck)
    suspect: bool             // live across a recursive call that may write through a handle
    usedAt: int               // the reads so far (useSeq) when it was last read: see loopSelfCheck
""")

region("var symArena = new []Sym(0)", "fn newSym(", """// A reference local's binding, on one path: what it may point into. Its roots are owned locals,
// reference parameters, globals, or handleSym (a borrow through a handle). deeps says it may point
// below the root's own object, and paths says where below ("" = anywhere). writable says changes may
// be made through it, and parent is the reference local it was derived from, which a change through
// it does not free. A binding is never changed once made: a re-point makes a new one (see arenaBind).
type RefBind = ref record {
    roots: []^Sym
    deeps: []bool
    paths: []string
    writable: bool
    parent: ?^Sym
}

// The state of the owned locals on one path at one point (see snapshot): flags holds, per local, its
// moved flag, then its suspect flag, then its fresh flag; binds holds its binding.
type PState = ref record {
    flags: []bool
    binds: []^RefBind
}

// The bindings made while compiling a function. The arena owns them and fnDecl releases them when the
// function ends; handles to them are what symbols and states hold. noBind is the binding of anything
// that is not a reference local (it points at nothing); noneBind is the identity of a join.
var bindArena = new []RefBind(0)

// the changes that may free something, so far: a loop whose body makes none cannot free what its next
// iteration reads (see loopBindCheck)
var freeCount = 0

fn arenaBind(roots: []^Sym, deeps: []bool, paths: []string, writable: bool, parent: ?^Sym): ^RefBind {
    append(bindArena, new RefBind{roots: roots, deeps: deeps, paths: paths, writable: writable, parent: parent})
    return handle(bindArena[len(bindArena) - 1])
}

// a binding of one root
fn singleBind(root: ^Sym, deep: bool, path: string, writable: bool, parent: ?^Sym): ^RefBind {
    var roots = new []^Sym(0)
    append(roots, root)
    var deeps = new []bool(0)
    append(deeps, deep)
    var paths = new []string(0)
    if deep { append(paths, path) } else { append(paths, "") }
    return arenaBind(roots, deeps, paths, writable, parent)
}

let noBind = arenaBind(new []^Sym(0), new []bool(0), new []string(0), true, nil)
let noneBind = arenaBind(new []^Sym(0), new []bool(0), new []string(0), true, nil)

var symArena = new []Sym(0)            // owns every symbol; the rest of the compiler holds handles

""")

sub("    append(symArena, new Sym{kind: kind, name: name, typ: t, addr: -1, fixups: new []int(0),",
    "    append(symArena, new Sym{kind: kind, name: name, typ: t, bind: noBind, addr: -1, fixups: new []int(0),")

# ---------------------------------------------------------------- the state of a path
region("// A path's state: for each owned local", "// Takes ownership of x's value", """// The state of the owned locals on one path: moves must agree where paths join, while suspicion,
// freshness and bindings are the join of the paths'. See PState.
fn segsPrefix(p: string, s: string): bool
fn joinBind(a: ^RefBind, b: ^RefBind): ^RefBind

fn snapshot(): PState {
    let locals = &ownedLocals
    let n = len(locals)
    var flags = new []bool(3 * n)
    var binds = new []^RefBind(0)
    for i in 0..n {
        flags[i] = locals[i].moved
        flags[n + i] = locals[i].suspect
        flags[2 * n + i] = locals[i].fresh
        append(binds, locals[i].bind)
    }
    return new PState{flags: flags, binds: binds}
}

// no moves, no suspicion, every local fresh and nothing pointed at: where a join begins
fn neutralState(n: int): PState {
    var flags = new []bool(3 * n)
    var binds = new []^RefBind(0)
    for i in 0..n {
        flags[2 * n + i] = true
        append(binds, noneBind)
    }
    return new PState{flags: flags, binds: binds}
}

fn emptyState(): PState { return neutralState(0) }

// the number of locals a state covers
fn localsIn(st: &PState): int { return len(st.binds) }

fn copyState(a: &PState): PState {
    let n = len(a.flags)
    let m = len(a.binds)
    var flags = new []bool(n)
    var binds = new []^RefBind(0)
    for i in 0..n { flags[i] = a.flags[i] }
    for i in 0..m { append(binds, a.binds[i]) }
    return new PState{flags: flags, binds: binds}
}

fn restore(st: &PState) {
    let n = localsIn(st)
    for i in 0..n {
        ownedLocals[i].moved = st.flags[i]
        ownedLocals[i].suspect = st.flags[n + i]
        ownedLocals[i].fresh = st.flags[2 * n + i]
        ownedLocals[i].bind = st.binds[i]
    }
}

// a deferred body runs where its block is left, so the suspicion, freshness and bindings it
// leaves are taken back here; its moves are kept, since a deferred body may free
fn restoreDeferred(st: &PState) {
    let n = localsIn(st)
    for i in 0..n {
        ownedLocals[i].suspect = st.flags[n + i]
        ownedLocals[i].fresh = st.flags[2 * n + i]
        ownedLocals[i].bind = st.binds[i]
    }
}

// the moves of a state, without the rest (a loop's exits are joined in afterwards)
fn restoreMoved(st: &PState) {
    for i in 0..localsIn(st) { ownedLocals[i].moved = st.flags[i] }
}

// where paths join: st's suspicion, bindings and freshness join the current ones
fn joinSuspect(st: &PState) {
    let n = localsIn(st)
    for i in 0..n {
        let s = ownedLocals[i]
        if st.flags[n + i] { s.suspect = true }
        let jb = joinBind(s.bind, st.binds[i])
        s.bind = jb
        s.fresh = s.fresh && st.flags[2 * n + i]
    }
}

// as joinSuspect, but without suspicion (a continue's exit carries its suspicion to the top only)
fn joinBinds(st: &PState) {
    let n = localsIn(st)
    for i in 0..n {
        let s = ownedLocals[i]
        let jb = joinBind(s.bind, st.binds[i])
        s.bind = jb
        s.fresh = s.fresh && st.flags[2 * n + i]
    }
}

// per local: suspect where either state is, bindings joined, fresh only where both are; moves are b's
fn joinStates(a: &PState, b: &PState): PState {
    let out = copyState(b)
    let n = localsIn(b)
    for i in 0..n {
        if a.flags[n + i] { out.flags[n + i] = true }
        out.flags[2 * n + i] = out.flags[2 * n + i] && a.flags[2 * n + i]
        let jb = joinBind(a.binds[i], b.binds[i])
        out.binds[i] = jb
    }
    return out
}

// a loop's exit (a break, a continue, or the body's end) joins the loop's accumulated state, for the
// locals the loop began with: the exit's st has at least as many
fn absorb(mut acc: &PState, st: &PState) {
    let m = localsIn(acc)
    let n = localsIn(st)
    for i in 0..m {
        if st.flags[n + i] { acc.flags[m + i] = true }
        acc.flags[2 * m + i] = acc.flags[2 * m + i] && st.flags[2 * n + i]
        let jb = joinBind(acc.binds[i], st.binds[i])
        acc.binds[i] = jb
    }
}

// a reference is pointed afresh by the body of a loop before it is read there: the reads before
// that depend on the back edge, so they are not fresh
fn clearFresh() {
    let locals = &ownedLocals
    for s in locals { s.fresh = false }
}

// the first owned local whose move state differs between a and b, or nil
fn stateDiff(a: &PState, b: &PState): ?^Sym {
    let n = localsIn(b)
    for i in 0..localsIn(a) {
        if i < n && a.flags[i] != b.flags[i] { return ownedLocals[i] }
    }
    return nil
}

fn sameStates(a: &PState, b: &PState, line: int, col: int) {
    let d = stateDiff(a, b)
    if d != nil {
        if d.isRefLocal {
            errorAt("'" + d.name + "' is invalidated on one path but not another; point it again on every path or none", line, col)
        }
        errorAt("'" + d.name + "' is moved on one path but not another; move it on every path or none", line, col)
    }
}

// ------------------------------------------------------------ joining bindings

// two bindings of the same place (one each) joined: a place is hit by every change that hits either
// one, so when one lies under the other, the longer one is hit by all the changes that hit the shorter
fn joinPath(da: bool, pa: string, db: bool, pb: string): string {
    if !db { return pa }
    if !da { return pb }
    if pa == pb { return pa }
    if pa == "" || pb == "" { return "" }
    if segsPrefix(pa, pb) && !segsPrefix(pb, pa) { return pb }
    if segsPrefix(pb, pa) && !segsPrefix(pa, pb) { return pa }
    return ""
}

fn sameParent(a: ?^Sym, b: ?^Sym): bool {
    if a == nil { return b == nil }
    if b == nil { return false }
    return a == b
}

// the binding of a path that joins a and b: every root either may point into, deep where either may,
// at the place that covers both; writable only where both are, and a parent only where equal
fn joinBind(a: ^RefBind, b: ^RefBind): ^RefBind {
    if a == b || b == noneBind { return a }
    if a == noneBind { return b }
    var roots = new []^Sym(0)
    var deeps = new []bool(0)
    var paths = new []string(0)
    var changed = false
    for i in 0..len(a.roots) {
        append(roots, a.roots[i])
        append(deeps, a.deeps[i])
        append(paths, a.paths[i])
    }
    for j in 0..len(b.roots) {
        var found = false
        for i in 0..len(roots) {
            if roots[i] == b.roots[j] {
                found = true
                let d = deeps[i] || b.deeps[j]
                let p = joinPath(deeps[i], paths[i], b.deeps[j], b.paths[j])
                if d != deeps[i] || p != paths[i] { changed = true }
                deeps[i] = d
                paths[i] = p
            }
        }
        if !found {
            append(roots, b.roots[j])
            append(deeps, b.deeps[j])
            append(paths, b.paths[j])
            changed = true
        }
    }
    let writable = a.writable && b.writable
    if writable != a.writable { changed = true }
    var parent: ?^Sym = a.parent
    if !sameParent(a.parent, b.parent) {
        parent = nil
        changed = true
    }
    if !changed { return a }
    return arenaBind(roots, deeps, paths, writable, parent)
}

// the places e covers for a place of b, both in one root: a whole-object pointer covers only
// whole-object ones, and an e whose place lies under b's covers b's, since e is hit by all b is
fn coversPlace(ed: bool, ep: string, bd: bool, bp: string): bool {
    if !bd { return true }
    if !ed { return false }
    if ep == "" { return true }
    return bp != "" && segsPrefix(bp, ep)
}

// the binding b, at the end of a loop's body, is covered by e, the binding the body was checked with:
// every change that reaches b is one e was checked against, and b is writable only if e is
fn bindCovers(e: ^RefBind, b: ^RefBind): bool {
    if b == noneBind || b == e { return true }
    if e.writable && !b.writable { return false }
    if !sameParent(e.parent, b.parent) { return false }
    for j in 0..len(b.roots) {
        var covered = false
        for i in 0..len(e.roots) {
            if e.roots[i] == b.roots[j] && coversPlace(e.deeps[i], e.paths[i], b.deeps[j], b.paths[j]) {
                covered = true
            }
        }
        if !covered { return false }
    }
    return true
}

""")

# the joins come before the state functions that call them, and the segment helpers before both
_st = s.index("// The state of the owned locals on one path: moves must agree")
_jb = s.index("// ------------------------------------------------------------ joining bindings")
_ct = s.index("// Takes ownership of x's value")
_head = s[_st:_jb].replace("fn segsPrefix(p: string, s: string): bool\nfn joinBind(a: ^RefBind, b: ^RefBind): ^RefBind\n\n", "")
assert "fn joinBind(a: ^RefBind, b: ^RefBind): ^RefBind\n" not in _head
s = s[:_st] + SEG + s[_jb:_ct] + _head + s[_ct:]

# ---------------------------------------------------------------- reference helpers
region("// the owned local or reference parameter that x ultimately borrows from",
       "// a reference parameter: it may name the same object", """fn mutableOf(x: &Item): bool
fn deepOf(x: &Item): bool
fn parentOf(x: &Item): ?^Sym

// the binding a reference to x takes: what x may point into, through the binding of the reference it
// goes through; a constant points at nothing
fn itemBind(x: &Item): ^RefBind {
    if x.viaHandle { return singleBind(handleSym, deepOf(x), x.path, mutableOf(x), parentOf(x)) }
    let r = x.root
    if x.mode == "const" || r == nil { return noBind }
    if !r.isRef { return singleBind(r, x.deref, x.path, true, nil) }
    let rb = r.bind
    var roots = new []^Sym(0)
    var deeps = new []bool(0)
    var paths = new []string(0)
    for i in 0..len(rb.roots) {
        append(roots, rb.roots[i])
        append(deeps, x.deref || rb.deeps[i])
        if x.deref { append(paths, "") } else { append(paths, rb.paths[i]) }
    }
    var parent: ?^Sym = nil
    if r.isRefLocal {
        if x.deref { parent = r } else { parent = rb.parent }
    }
    return arenaBind(roots, deeps, paths, rb.writable, parent)
}

// x is about to be borrowed: the binding it takes, after the checks borrowing needs
fn borrowBind(x: &Item): ^RefBind {
    if x.mode == "const" { return noBind }
    if x.viaHandle { return itemBind(x) }
    if x.mode == "reg" && isOwned(x.typ) {
        error("store this value in a variable first: a reference cannot borrow from a temporary")
    }
    let b = itemBind(x)
    if len(b.roots) == 0 { error("only variables, fields and elements can be borrowed") }
    return b
}

// the same binding with another parent
fn rebind(b: ^RefBind, parent: ?^Sym): ^RefBind {
    var roots = new []^Sym(0)
    var deeps = new []bool(0)
    var paths = new []string(0)
    for i in 0..len(b.roots) {
        append(roots, b.roots[i])
        append(deeps, b.deeps[i])
        append(paths, b.paths[i])
    }
    return arenaBind(roots, deeps, paths, b.writable, parent)
}

// true if some place the binding may point into is deeper than its root's own object
fn anyDeep(b: ^RefBind): bool {
    for i in 0..len(b.deeps) {
        if b.deeps[i] { return true }
    }
    return false
}

fn mutableOf(x: &Item): bool {
    let r = x.root
    if r != nil && r.isRef { return r.bind.writable }
    return true
}

fn deepOf(x: &Item): bool {
    if x.deref { return true }
    let r = x.root
    if r != nil && r.isRefLocal { return anyDeep(r.bind) }
    return false
}

// the reference local x was derived from, through its own binding
fn parentOf(x: &Item): ?^Sym {
    let r = x.root
    if r == nil || !r.isRefLocal { return nil }
    if x.deref { return r }
    return r.bind.parent
}

// true if a is via itself or one of the references via was derived from
fn ancestorOf(a: ^Sym, via: ?^Sym): bool {
    var p = via
    while p != nil {
        if p == a { return true }
        p = p.bind.parent
    }
    return false
}

fn makeRefLocal(v: ^Sym, b: ^RefBind) {
    v.isRef = true
    v.isRefLocal = true
    v.bind = b
    v.fresh = true
    append(ownedLocals, v)
}

// a for loop's variable borrows each element of the walked array: deep, anywhere in what the array may be in
fn walkBind(x: &Item, walked: ^Sym): ^RefBind {
    let b = itemBind(x)
    var roots = new []^Sym(0)
    var deeps = new []bool(0)
    var paths = new []string(0)
    for i in 0..len(b.roots) {
        append(roots, b.roots[i])
        append(deeps, true)
        append(paths, "")
    }
    var parent: ?^Sym = nil
    if walked.isRefLocal { parent = walked }
    return arenaBind(roots, deeps, paths, b.writable, parent)
}

""")

# ---------------------------------------------------------------- changes
region("fn invalidate(root: ?^Sym, via: ?^Sym, all: bool, path: string) {",
       "// a deferred error, reported at the end of the body", """fn invalidate(root: ?^Sym, via: ?^Sym, all: bool, path: string) {
    if root == nil { return }
    freeCount += 1
    let locals = &ownedLocals
    for v in locals {
        // a change through via frees only things below via: references it was
        // derived from point above it and stay valid
        if v.isRefLocal && !ancestorOf(v, via) && !v.moved {
            let b = v.bind
            var hit = false
            for i in 0..len(b.roots) {
                let r = b.roots[i]
                // a borrow through a handle may point into any object, so any change reaches it
                if r == handleSym || r == root && (all || b.deeps[i] && underPath(b.paths[i], path)) { hit = true }
                if r != root && b.deeps[i] && reaches(root, r) { hit = true }
            }
            if hit {
                if v.iterating { error("cannot change what '" + v.name + "' points into while a for loop walks it") }
                v.moved = true
            }
        }
    }
}

// A change through a handle may free anything below its target, so every reference that points
// inside something is invalid, whatever it borrows from. A reference to a whole object stays
// valid: only owners free whole objects, and a change through a handle frees only below its target.
fn invalidateDeep(via: ?^Sym) {
    freeCount += 1
    let locals = &ownedLocals
    for v in locals {
        if v.isRefLocal && anyDeep(v.bind) && !ancestorOf(v, via) && !v.moved {
            if v.iterating { error("cannot change what '" + v.name + "' points into while a for loop walks it") }
            v.moved = true
        }
    }
}

""")

region("// a recursive call (or a deferred one) may write through a handle",
       "// a loop whose body calls the function itself", """// a recursive call (or a deferred one) may write through a handle: the references inside something
// that are live now may be freed, so they are suspect until they are pointed afresh
fn markSuspects() {
    let locals = &ownedLocals
    for v in locals {
        if v.isRefLocal && anyDeep(v.bind) && !v.moved { v.suspect = true }
    }
}

""")

sub("fn loopSelfCheck(nOuter: int, selfBefore: int, useBefore: int, back: &[]bool, lv: ?^Sym, line: int, col: int) {",
    "fn loopSelfCheck(nOuter: int, selfBefore: int, useBefore: int, back: &PState, lv: ?^Sym, line: int, col: int) {")
sub("        if back[i] && ownedLocals[i] != lv && ownedLocals[i].usedAt > useBefore {",
    "        if back.flags[localsIn(back) + i] && ownedLocals[i] != lv && ownedLocals[i].usedAt > useBefore {")
# loopBindCheck goes after loopSelfCheck's closing brace
i0 = s.index("fn loopSelfCheck(")
i1 = s.index("\n}\n", i0) + 3
s = s[:i1] + """
// A loop that points a reference somewhere new and reads it before pointing it again reads it on the
// next iteration with a binding the body was not checked with. If the body frees something, that
// binding must be covered by the one the body was checked with (see bindCovers); a body that frees
// nothing cannot free what the next iteration reads.
fn loopBindCheck(entry: &PState, back: &PState, useBefore: int, frees: bool, lv: ?^Sym, line: int, col: int) {
    let n = localsIn(entry)
    for i in 0..n {
        let s = ownedLocals[i]
        if frees && s.isRefLocal && s != lv && s.staleUse > useBefore && !bindCovers(entry.binds[i], back.binds[i]) {
            errorAt("'" + s.name + "' is pointed somewhere new inside the loop, and read before it is pointed again; point it before the loop, or point it afresh before reading it in the loop", line, col)
        }
    }
}
""" + s[i1:]

region("// the reference local a change goes through, if any", "fn parseType(): ^Type\n", """// the reference local a change goes through, if any
fn viaOf(x: &Item): ?^Sym {
    let r = x.root
    if r != nil && r.isRefLocal { return r }
    return nil
}

// a change through x: the roots its binding may point into lose their references, and so do the
// handle-rooted ones when x went through a handle. Such an x may have no root (handle(p).items).
fn invalidateChange(x: &Item) {
    let r = x.root
    let via = viaOf(x)
    if r != nil {
        if r.isRef {
            for i in 0..len(r.bind.roots) { invalidate(r.bind.roots[i], via, false, x.path) }
        } else {
            invalidate(r, via, false, x.path)
        }
    }
    if x.viaHandle { invalidate(handleSym, via, false, "") }
}

// a change through x goes through a handle, or through a reference that may point into one
fn throughHandle(x: &Item): bool {
    if x.viaHandle { return true }
    let r = x.root
    if r != nil && r.isRef {
        for i in 0..len(r.bind.roots) {
            if r.bind.roots[i] == handleSym { return true }
        }
    }
    return false
}

// the globals a change through x may free: a change through a handle may free anything
fn recordFreeChange(x: &Item) {
    if x.viaHandle {
        recordFree(handleSym)
        return
    }
    let r = x.root
    if r == nil { return }
    if r.isRef {
        for i in 0..len(r.bind.roots) { recordFree(r.bind.roots[i]) }
    } else {
        recordFree(r)
    }
}

// a change of an owned value that goes through a handle, or through a reference borrowed through
// one: it may free what references inside something point into
fn invalidateOwnedChange(x: &Item) {
    invalidateChange(x)
    if throughHandle(x) { invalidateDeep(viaOf(x)) }
}

fn parseType(): ^Type
""")

# ---------------------------------------------------------------- uses
region('    if s.isRefLocal && (!target || path != "") {', "    x.viaHandle = viaHandle", """    if s.isRefLocal && (!target || path != "") {        // a read, not a re-pointing: see loopBindCheck
        useSeq += 1
        s.usedAt = useSeq
        if !s.fresh { s.staleUse = useSeq }
    }
""")

sub("    let argRoots = new []?^Sym(0)", "    var argBinds = new []^RefBind(0)")
sub("    let argDeeps = new []bool(0)\n", "")
region("        if f.pmodes[i] > 0 {", "            consume(a)", """        if f.pmodes[i] > 0 {
            let b = borrowBind(a)
            if f.pmodes[i] == 2 && !b.writable {
                error("argument " + string(i + 1) + " of " + f.name + " needs a mut reference, but this one is read-only")
            }
            append(argBinds, b)
            append(argModes, f.pmodes[i])
            append(argVias, viaOf(a))
            if anyDeep(b) && deepLine == 0 {
                deepLine = argLine
                deepCol = argCol
            }
            append(argPaths, a.path)
        } else {
""")
region("    // a mut reference and another that may point inside it", "    afterCall(f)", """    // a mut reference and another that may point inside it could let the callee
    // free what the other points to; references to one object are fine
    for i in 0..len(argBinds) {
        for j in 0..len(argBinds) {
            let bi = argBinds[i]
            let bj = argBinds[j]
            if i != j && argModes[i] == 2 {
                for p in 0..len(bi.roots) {
                    for q in 0..len(bj.roots) {
                        let ri = bi.roots[p]
                        let rj = bj.roots[q]
                        // two places in one root may be told apart: elements known to differ
                        if bj.deeps[q] && reaches(ri, rj) && (ri != rj || pathsOverlap(bi.paths[p], bj.paths[q])) {
                            error("argument " + string(j + 1) + " of " + f.name + " may point inside argument " + string(i + 1) + ", which is mut")
                        }
                    }
                }
            }
        }
    }
    for i in 0..len(argBinds) {
        let b = argBinds[i]
        for p in 0..len(b.roots) {
            let ar = b.roots[p]
            if ar.storage == "global" && mightFree(f, ar) {
                error("cannot pass a reference into global '" + ar.name + "' to " + f.name + ": it might free it")
            }
        }
    }
    for i in 0..len(argBinds) {
        let b = argBinds[i]
        for p in 0..len(b.roots) {
            if b.roots[p] == handleSym && !pureFn(f) {
                error("cannot pass a reference that borrows through a handle to " + f.name + ": it might free what the handle names")
            }
        }
    }
    for i in 0..len(argBinds) {
        if argModes[i] == 2 {
            let b = argBinds[i]
            for p in 0..len(b.roots) {
                invalidate(b.roots[p], argVias[i], false, argPaths[i])
                recordFree(b.roots[p])
            }
        }
    }
""")

sub("        if x.deref && tr != nil && tr.isRef && !tr.refMut {", "        if x.deref && tr != nil && tr.isRef && !tr.bind.writable {", count=2)
sub("recordFree(freeRoot(x))", "recordFreeChange(x)", count=3)
sub("            if ar != nil && ar.isRef && !ar.refMut {", "            if ar != nil && ar.isRef && !ar.bind.writable {")
sub("                if owned || freeRoot(a) != handleSym { recordFree(freeRoot(a)) }   // a scalar array frees nothing",
    "                if owned || !throughHandle(a) { recordFreeChange(a) }   // a scalar array frees nothing")
sub("        if x.deref && wr != nil && wr.isRef && !wr.refMut {", "        if x.deref && wr != nil && wr.isRef && !wr.bind.writable {")

region("let root = borrowRoot(y)", "xs.suspect = false\n            xs.moved = false", """let nb = borrowBind(y)
            if !assignable(x.typ, y.typ) { widen(xs) }
            store(x, y)
            // the parent may be xs itself, or one below it (a reference copied from xs's own value): then
            // it is xs's old parent, so that the parents stay a chain
            let oldParent = xs.bind.parent
            var parent: ?^Sym = nb.parent
            if ancestorOf(xs, parent) { parent = oldParent }
            // the references derived from xs's old value derive from its parent now
            let locals = &ownedLocals
            for v in locals {
                if v.isRefLocal && v.bind.parent == xs {
                    let rb = rebind(v.bind, oldParent)
                    v.bind = rb
                }
            }
            let nbind = rebind(nb, parent)
            xs.bind = nbind
            xs.fresh = true
            """)

sub("let root = borrowRoot(x)", "let nb = borrowBind(x)")
sub("makeRefLocal(v, x, root)", "makeRefLocal(v, nb)")

sub("        let owner = rootOf(x)\n", "")
sub("        let xmut = mutableOf(x)\n", "        let wb = walkBind(x, walked)\n")
region("v.refRoot = owner", "            append(ownedLocals, v)\n        }\n        let strs", """v.bind = wb
            v.fresh = true
""")

sub("    let entry = snapshot()\n    let top = pc()", "    clearFresh()\n    let entry = snapshot()\n    let top = pc()")
sub("entry: new []bool(0), susp: new []bool(0), back: new []bool(0)}", "entry: emptyState(), susp: emptyState(), back: emptyState()}")

region("// entry: the move states when the loop is entered", "fn whileStat(): bool {", """// entry: the state when the loop is entered; every iteration must end, and every break or continue
// happen, with the same moves. nOuter and selfBefore: the owned locals and the self-call count before
// the loop's condition (if any) and body, so that a self-call in either is seen by loopSelfCheck;
// useBefore: the reads so far, so that a read in the loop is seen by loopBindCheck
fn loopBodyV(entry: PState, line: int, col: int, lv: ?^Sym, nOuter: int, selfBefore: int, useBefore: int): Loop

fn loopBody(entry: PState, line: int, col: int, nOuter: int, selfBefore: int, useBefore: int): Loop {
    return loopBodyV(entry, line, col, nil, nOuter, selfBefore, useBefore)
}

// lv: a for loop's variable, rebound at the start of every iteration
fn loopBodyV(entry: PState, line: int, col: int, lv: ?^Sym, nOuter: int, selfBefore: int, useBefore: int): Loop {
    let n = localsIn(entry)
    let frees = freeCount
    clearFresh()
    append(loops, new Loop{brk: new []int(0), cont: new []int(0), blocks: len(blocks), entry: copyState(entry), lv: lv,
                           susp: neutralState(n), back: neutralState(n)})
    let term = block()
    // the next iteration starts where the body ends, if it falls through, and at each continue (added then)
    let li = len(loops) - 1
    if !term {
        let now = snapshot()
        absorb(&loops[li].back, &now)
    }
    loopSelfCheck(nOuter, selfBefore, useBefore, &loops[li].back, lv, line, col)
    loopBindCheck(&entry, &loops[li].back, useBefore, freeCount > frees, lv, line, col)
    // the loop's record leaves the stack; what break and continue collected comes back
    let res = new Loop{brk: copyInts(loops[li].brk), cont: copyInts(loops[li].cont), blocks: loops[li].blocks,
                       entry: copyState(entry), lv: lv, susp: copyState(loops[li].susp), back: copyState(loops[li].back)}
    truncate(loops, li)
    if lv != nil { lv.moved = false }
    if !term {
        let now = snapshot()
        let d = stateDiff(&entry, &now)
        if d != nil {
            if d.isRefLocal {
                errorAt("'" + d.name + "' is invalidated inside the loop; point it again before the end of each iteration", line, col)
            }
            errorAt("'" + d.name + "' is moved inside the loop; it must have a value again by the end of each iteration", line, col)
        }
    }
    // the loop may run no time, or leave from its body: its suspicion, bindings and freshness are what
    // either carries. A body that always leaves carries none of its own, since its end is not reached
    if term { restore(&entry) } else { restoreMoved(&entry) }
    joinSuspect(&entry)
    joinSuspect(&res.susp)
    joinBinds(&res.back)
    return res
}

""")

sub("    let before = suspects()", "    let before = snapshot()")
sub("    restoreSuspects(before)", "    restoreDeferred(&before)")

sub("if ownedLocals[q] == lv { st[q] = false }", "if ownedLocals[q] == lv { st.flags[q] = false }")
region("            let nl = localsIn(loop.entry)", "            if k == T_BREAK {", """            runDefers(downTo)
            // the state this exit carries, after the deferred calls that run here
            let now = snapshot()
            let lp = &loops[len(loops) - 1]
            absorb(&lp.susp, &now)
            if k == T_CONTINUE { absorb(&lp.back, &now) }
            let cur = &loops[len(loops) - 1]               // runDefers might have freed: borrow again
""")

region("s.refMut = params[i].mode == 2", "            s.kind = \"let\"", """s.bind = singleBind(s, false, "", params[i].mode == 2, nil)
""")
sub("    let symMark = len(symArena)", "    let bindMark = len(bindArena)\n    let symMark = len(symArena)")
sub("    truncate(symArena, symMark)", "    truncate(bindArena, bindMark)\n    truncate(symArena, symMark)")

region("    entry: []bool", "fn makePool(): []int {", """    entry: PState              // the state when the loop was entered
    lv: ?^Sym                  // a for loop's variable, rebound every iteration
    susp: PState               // the state where a break or continue leaves the loop, joined
    back: PState               // the state where the next iteration starts: the body's end, or a continue
}

""")

# the remaining state-typed names: match arms copy their state
sub("copyBools(", "copyState(", count=s.count("copyBools("))

open(P, 'w').write(s)
print("ok")
