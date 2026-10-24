"""Edits by script: the fixes to the reference bindings per path (src/obo.obo, after tools/port_ref_binds.py).

1. A defer body runs where its block is left, so a re-point of a reference declared before the body is
   refused (deferMark, declaredBefore): the change would not be seen at the exits.
2. A change that may free something marks each reference that is not pointed afresh on every path
   (carryFreed, per path: PState's fourth flag block). A read of a marked reference, and a marked
   reference carried round a loop, are checked by loopBindCheck. This replaces the frees counter that
   gated the loop check: a free in a while condition, or on one path only, is seen by the references it
   reaches, and a re-point after the free clears the mark.
3. The loop check: a read of a marked reference, or a marked reference carried round the loop, is an
   error unless the body was checked with a binding covering it; a binding that comes back read-only
   where the body was checked against a writable one is an error too.
4. The comment of bindCovers says what the code does: b is writable wherever e is.

Each replacement must match exactly once in src/obo.obo."""
import os

HERE = os.path.dirname(os.path.abspath(__file__))
P = os.path.join(os.path.dirname(HERE), "src", "obo.obo")
t = open(P).read()

EDITS = []


def edit(old, new):
    EDITS.append((old, new))


# the frees counter is replaced by the defer mark
edit('''// the changes that may free something, so far: a loop whose body makes none cannot free what its next
// iteration reads (see loopBindCheck)
var freeCount = 0
''',
'''// the ownedLocals a defer body was entered with: a re-point of one of them in the body is refused
// (see deferStat); -1 outside a defer body
var deferMark = -1
''')

# the symbol records the carried-free mark and the last read of a marked reference
edit('''    staleUse: int            // useSeq of the last read that was not fresh (see loopBindCheck)
''',
'''    staleUse: int            // useSeq of the last read that was not fresh (see loopBindCheck)
    freedUse: int            // useSeq of the last read that was not fresh while carryFreed (see loopBindCheck)
    carryFreed: bool         // a change that may free something came while it was not pointed afresh on every path
''')

# the state carries the mark as a fourth flag block
edit('''// moved flag, then its suspect flag, then its fresh flag; binds holds its binding.''',
'''// moved flag, then its suspect flag, then its fresh flag, then its carryFreed flag; binds holds its binding.''')
edit('''    var flags = new []bool(3 * n)
    var binds = new []^RefBind(0)
    for i in 0..n {
        flags[i] = locals[i].moved
''',
'''    var flags = new []bool(4 * n)
    var binds = new []^RefBind(0)
    for i in 0..n {
        flags[i] = locals[i].moved
''')
edit('''        flags[2 * n + i] = locals[i].fresh
''',
'''        flags[2 * n + i] = locals[i].fresh
        flags[3 * n + i] = locals[i].carryFreed
''')
edit('''    var flags = new []bool(3 * n)
    var binds = new []^RefBind(0)
    for i in 0..n {
        flags[2 * n + i] = true
''',
'''    var flags = new []bool(4 * n)
    var binds = new []^RefBind(0)
    for i in 0..n {
        flags[2 * n + i] = true
''')
edit('''        ownedLocals[i].moved = st.flags[i]
        ownedLocals[i].suspect = st.flags[n + i]
        ownedLocals[i].fresh = st.flags[2 * n + i]
''',
'''        ownedLocals[i].moved = st.flags[i]
        ownedLocals[i].suspect = st.flags[n + i]
        ownedLocals[i].fresh = st.flags[2 * n + i]
        ownedLocals[i].carryFreed = st.flags[3 * n + i]
''')
edit('''        if st.flags[n + i] { s.suspect = true }
''',
'''        if st.flags[n + i] { s.suspect = true }
        if st.flags[3 * n + i] { s.carryFreed = true }
''')
edit('''fn joinBinds(st: &PState) {
    let n = localsIn(st)
    for i in 0..n {
        let s = ownedLocals[i]
''',
'''fn joinBinds(st: &PState) {
    let n = localsIn(st)
    for i in 0..n {
        let s = ownedLocals[i]
        if st.flags[3 * n + i] { s.carryFreed = true }
''')
edit('''        if a.flags[n + i] { out.flags[n + i] = true }
''',
'''        if a.flags[n + i] { out.flags[n + i] = true }
        if a.flags[3 * n + i] { out.flags[3 * n + i] = true }
''')
edit('''        if st.flags[n + i] { acc.flags[m + i] = true }
''',
'''        if st.flags[n + i] { acc.flags[m + i] = true }
        if st.flags[3 * n + i] { acc.flags[3 * m + i] = true }
''')

# a re-point clears the mark of the reference it points; a loop start does not touch the marks, because
# loopBodyV calls clearFresh again after the condition, and a free in the condition must still be seen
edit('''            xs.fresh = true
            xs.suspect = false
            xs.moved = false
''',
'''            xs.fresh = true
            xs.carryFreed = false
            xs.suspect = false
            xs.moved = false
''')

# the marking, and the defer check's helper
edit('''// a reference is pointed afresh by the body of a loop before it is read there: the reads before
// that depend on the back edge, so they are not fresh
fn clearFresh() {''',
'''// a change that may free something marks each reference that is not pointed afresh on every path:
// what it points into may be freed, so a read of it, or its being carried round a loop, is checked
// (see loopBindCheck)
fn markCarriedFreed() {
    for i in 0..len(ownedLocals) {
        if ownedLocals[i].isRefLocal && !ownedLocals[i].fresh { ownedLocals[i].carryFreed = true }
    }
}

// whether s is one of the first mark locals (declared before a defer body was entered)
fn declaredBefore(s: ^Sym, mark: int): bool {
    for i in 0..mark {
        if ownedLocals[i] == s { return true }
    }
    return false
}

// a reference is pointed afresh by the body of a loop before it is read there: the reads before
// that depend on the back edge, so they are not fresh
fn clearFresh() {''')

edit('''fn invalidate(root: ?^Sym, via: ?^Sym, all: bool, path: string) {
    if root == nil { return }
    freeCount += 1
''',
'''fn invalidate(root: ?^Sym, via: ?^Sym, all: bool, path: string) {
    if root == nil { return }
    markCarriedFreed()
''')
edit('''fn invalidateDeep(via: ?^Sym) {
    freeCount += 1
''',
'''fn invalidateDeep(via: ?^Sym) {
    markCarriedFreed()
''')

# the loop check
edit('''// A loop that points a reference somewhere new and reads it before pointing it again reads it on the
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
}''',
'''// A loop that points a reference somewhere new and reads it before pointing it again reads it on the
// next iteration with a binding the body was not checked with. That is unsafe where a change may have
// freed what the reference points into while it was not pointed afresh (carryFreed, at a read or at the
// back edge), and where the binding comes back read-only but the body was checked against a writable
// one. Otherwise the binding must be covered by the one the body was checked with (see bindCovers).
fn loopBindCheck(entry: &PState, back: &PState, useBefore: int, lv: ?^Sym, line: int, col: int) {
    let n = localsIn(entry)
    for i in 0..n {
        let s = ownedLocals[i]
        let e = entry.binds[i]
        let b = back.binds[i]
        if s.isRefLocal && s != lv && s.staleUse > useBefore && !bindCovers(e, b) {
            if s.freedUse > useBefore || back.flags[3 * n + i] {
                errorAt("'" + s.name + "' is pointed somewhere new inside the loop, and read before it is pointed again; point it before the loop, or point it afresh before reading it in the loop", line, col)
            }
            if e.writable && !b.writable {
                errorAt("'" + s.name + "' is read-only where the loop starts again, and written before it is pointed again; point it afresh before writing it in the loop", line, col)
            }
        }
    }
}''')

# the comment of bindCovers says what the code does
edit('''// the binding b, at the end of a loop's body, is covered by e, the binding the body was checked with:
// every change that reaches b is one e was checked against, and b is writable only if e is
''',
'''// the binding b, at the end of a loop's body, is covered by e, the binding the body was checked with:
// every change that reaches b is one e was checked against, and b is writable wherever e is (a write
// checked against e is allowed through b too)
''')

# a read of a reference not pointed afresh notes whether its binding may be freed
edit('''        if !s.fresh { s.staleUse = useSeq }
''',
'''        if !s.fresh {
            s.staleUse = useSeq
            if s.carryFreed { s.freedUse = useSeq }
        }
''')

# the loop body no longer counts frees
edit('''    let n = localsIn(entry)
    let frees = freeCount
    clearFresh()''',
'''    let n = localsIn(entry)
    clearFresh()''')
edit('''    loopBindCheck(&entry, &loops[li].back, useBefore, freeCount > frees, lv, line, col)
''',
'''    loopBindCheck(&entry, &loops[li].back, useBefore, lv, line, col)
''')

# a defer body refuses to re-point what was declared before it: it runs where its block is left
edit('''    let savedInDefer = inDefer
''',
'''    let savedInDefer = inDefer
    let savedMark = deferMark
    deferMark = len(ownedLocals)
''')
edit('''    inDefer = savedInDefer
''',
'''    inDefer = savedInDefer
    deferMark = savedMark
''')
edit('''            if xs.iterating { errorAt("cannot point '" + xs.name + "' elsewhere while a for loop walks it", line, col) }
''',
'''            if xs.iterating { errorAt("cannot point '" + xs.name + "' elsewhere while a for loop walks it", line, col) }
            if deferMark >= 0 && declaredBefore(xs, deferMark) {
                errorAt("cannot point '" + xs.name + "' elsewhere in a defer: a defer body runs where its block is left, and the change would not be seen there", line, col)
            }
''')

for old, new in EDITS:
    n = t.count(old)
    assert n == 1, "expected one match, found %d for: %s" % (n, old[:70])
    t = t.replace(old, new)
open(P, "w").write(t)
print("ok: %d edits" % len(EDITS))
