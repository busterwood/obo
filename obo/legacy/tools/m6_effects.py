"""Borrowing from globals, checked with per-function effect summaries."""
import sys
path = sys.argv[1]
s = open(path).read()


def rep(old, new, count=1):
    global s
    n = s.count(old)
    assert n == count, (n, old[:100])
    s = s.replace(old, new)


rep("""    pmodes: []int             // fn: 0 owned, 1 &, 2 &mut, per parameter""", """    pmodes: []int             // fn: 0 owned, 1 &, 2 &mut, per parameter
    freed: []Sym              // fn: the globals it might free (directly or through calls)
    freesAll: bool            // fn: it might free anything (or isn't compiled yet)""")
rep("""                   params: new []Type(0), ret: tVoid, line: -1, pmodes: new []int(0)}""",
    """                   params: new []Type(0), ret: tVoid, line: -1, pmodes: new []int(0), freed: new []Sym(0)}""")
rep("""    if r != nil && r.storage == "global" { error("cannot borrow from global '" + r.name + "'") }""", "")
rep("""// the reference local a change goes through, if any""", """// ======================================================== effect summaries
// Every function records which globals it might free. Bodies are compiled
// before they can be called (declare before use), so at a call site the
// callee's summary is complete, unless it is still being compiled (recursion)
// or only has a forward heading: then it is assumed to free anything.

fn mightFree(f: Sym, g: Sym): bool {
    if f.addr < 0 || f == curFn || f.freesAll { return true }
    for x in f.freed {
        if x == g { return true }
    }
    return false
}

// the current function might free global g
fn recordFree(g: ?Sym) {
    let f = curFn
    if f == nil || g == nil { return }
    if g.storage != "global" { return }
    for x in f.freed {
        if x == g { return }
    }
    append(f.freed, g)
}

// after calling f: references into what f might free are no longer valid,
// and the caller inherits f's effects
fn afterCall(f: Sym) {
    let cur = curFn
    let all = f.addr < 0 || f == cur || f.freesAll
    if cur != nil && all { cur.freesAll = true }
    for g in globals {
        if (g.kind == "var" || g.kind == "let") && (all || mightFree(f, g)) {
            invalidate(g, nil, true)
            if !all { recordFree(g) }
        }
    }
}

// the reference local a change goes through, if any""")
# call sites: a reference into a global may only go to a callee that doesn't free it
rep("""    for i in 0..len(argRoots) {
        if argModes[i] == 2 { invalidate(argRoots[i], argVias[i], false) }
    }""", """    for i in 0..len(argRoots) {
        let ar = argRoots[i]
        if ar != nil && ar.storage == "global" && mightFree(f, ar) {
            error("cannot pass a reference into global '" + ar.name + "' to " + f.name + ": it might free it")
        }
    }
    for i in 0..len(argRoots) {
        if argModes[i] == 2 {
            invalidate(argRoots[i], argVias[i], false)
            recordFree(argRoots[i])
        }
    }
    afterCall(f)""")
# the current function's own frees under globals
rep("""                if xs != nil && xs.storage == "local" {
                    if xs.iterating { errorAt("cannot change '" + xs.name + "' while a for loop walks it", line, col) }
                    invalidate(xs, nil, true)
                } else if x.deref {
                    invalidate(ws, viaOf(x), false)
                }""", """                if xs != nil && xs.storage == "local" {
                    if xs.iterating { errorAt("cannot change '" + xs.name + "' while a for loop walks it", line, col) }
                    invalidate(xs, nil, true)
                } else if x.deref {
                    invalidate(ws, viaOf(x), false)
                    recordFree(ws)
                } else if xs != nil {
                    invalidate(xs, nil, true)               // a global given a new value
                    recordFree(xs)
                }""")
rep("""        if x.deref {
            invalidate(rootOf(x), viaOf(x), false)
        } else {
            let ts = x.sym
            if ts != nil && ts.storage == "local" { invalidate(ts, nil, true) }
        }""", """        if x.deref {
            invalidate(rootOf(x), viaOf(x), false)
            recordFree(rootOf(x))
        } else {
            let ts = x.sym
            if ts != nil && ts.storage == "local" { invalidate(ts, nil, true) }
            if ts != nil && ts.storage == "global" {
                invalidate(ts, nil, true)
                recordFree(ts)
            }
        }""")
rep("""            if name == "truncate" { invalidate(rootOf(a), viaOf(a), false) }""", """            if name == "truncate" {
                invalidate(rootOf(a), viaOf(a), false)
                recordFree(rootOf(a))
            }""")
open(path, 'w').write(s)
print("effects patch applied")
