"""Milestone 2: second-class references with group invalidation, in obo.obx."""
import sys
path = sys.argv[1]
s = open(path).read()


def rep(old, new, count=1):
    global s
    n = s.count(old)
    assert n == count, (n, old[:100])
    s = s.replace(old, new)


# ---------------------------------------------------------------- records
rep("""    isStr: bool               // a string local, holding one reference""", """    isStr: bool               // a string local, holding one reference
    isRef: bool               // a reference: a parameter or local that borrows
    isRefLocal: bool          // a reference local (its validity is tracked)
    refMut: bool              // changes may be made through it
    refRoot: ?Sym             // the owned local or reference parameter it borrows from
    refDeep: bool             // it may point below its root's own object
    iterating: bool           // a for loop is walking this array
    pmodes: []int             // fn: 0 owned, 1 &, 2 &mut, per parameter""")
rep("""    return new Sym{kind: kind, name: name, typ: t, addr: -1, fixups: new []int(0),
                   params: new []Type(0), ret: tVoid, line: -1}""", """    return new Sym{kind: kind, name: name, typ: t, addr: -1, fixups: new []int(0),
                   params: new []Type(0), ret: tVoid, line: -1, pmodes: new []int(0)}""")
rep("""    strTemp: bool             // a fresh string whose reference nothing owns yet""", """    strTemp: bool             // a fresh string whose reference nothing owns yet
    borrowed: bool            // the value is a reference (borrowed, not owned)""")
rep("""type Param = ref record {
    name: string
    typ: Type
}""", """type Param = ref record {
    name: string
    typ: Type
    mode: int                 // 0 owned, 1 &, 2 &mut
}""")
rep("""    entry: []bool             // move states when the loop was entered
}""", """    entry: []bool             // move states when the loop was entered
    lv: ?Sym                  // a for loop's variable, rebound every iteration
}""")

# --------------------------------------------------------- reference core
rep("""// an owned value that nothing owns yet must be stored, not just looked at""", """// ============================================================= references

var refMode = 0                   // set by parseRefType: 0 plain, 1 &, 2 &mut

// the owned local or reference parameter that x ultimately borrows from
fn rootOf(x: Item): ?Sym {
    let r = x.root
    if r == nil { return nil }
    if r.isRef {
        let rr = r.refRoot
        if rr != nil { return rr }
    }
    return r
}

fn mutableOf(x: Item): bool {
    let r = x.root
    if r != nil && r.isRef { return r.refMut }
    return true
}

fn deepOf(x: Item): bool {
    if x.deref { return true }
    let r = x.root
    if r != nil && r.isRefLocal { return r.refDeep }
    return false
}

// x is about to be borrowed: returns its root (nil for nil itself)
fn borrowRoot(x: Item): ?Sym {
    if x.mode == "const" { return nil }
    if x.mode == "reg" && isOwned(x.typ) {
        error("store this value in a variable first: a reference cannot borrow from a temporary")
    }
    let r = rootOf(x)
    if r == nil { error("only variables, fields and elements can be borrowed") }
    if r != nil && r.storage == "global" { error("cannot borrow from global '" + r.name + "'") }
    return r
}

fn makeRefLocal(v: Sym, x: Item, root: ?Sym, readOnly: bool) {
    v.isRef = true
    v.isRefLocal = true
    v.refRoot = root
    v.refMut = mutableOf(x) && !readOnly
    v.refDeep = deepOf(x)
    append(ownedLocals, v)
}

// Something under root may have been freed: the root's references become
// invalid, except via (the reference the change went through) and, unless
// all, references to the root's own object.
fn invalidate(root: ?Sym, via: ?Sym, all: bool) {
    if root == nil { return }
    for v in ownedLocals {
        if v.isRefLocal && v.refRoot == root && v != via && (all || v.refDeep) && !v.moved {
            if v.iterating { error("cannot change what '" + v.name + "' points into while a for loop walks it") }
            v.moved = true
        }
    }
}

// the reference local a change goes through, if any
fn viaOf(x: Item): ?Sym {
    let r = x.root
    if r != nil && r.isRefLocal { return r }
    return nil
}

fn parseRefType(): Type {
    refMode = 0
    if sym == T_AMP {
        next()
        var mode = 1
        if sym == T_IDENT && sval == "mut" {
            next()
            mode = 2
        }
        let t = parseType()
        if !isOwned(t) { error("only records, closed types and arrays can be borrowed, not " + tname(t)) }
        refMode = mode
        return t
    }
    return parseType()
}

// an owned value that nothing owns yet must be stored, not just looked at""")
# messages: references are invalidated, values are moved
rep("""    let d = stateDiff(a, b)
    if d != nil {
        errorAt("'" + d.name + "' is moved on one path but not another; move it on every path or none", line, col)
    }""", """    let d = stateDiff(a, b)
    if d != nil {
        if d.isRefLocal {
            errorAt("'" + d.name + "' is invalidated on one path but not another; point it again on every path or none", line, col)
        }
        errorAt("'" + d.name + "' is moved on one path but not another; move it on every path or none", line, col)
    }""")

# ---------------------------------------------------- moves and references
rep("""    if !isOwned(x.typ) || x.mode == "reg" || x.mode == "const" { return }
    let s = x.sym""", """    if !isOwned(x.typ) || x.mode == "reg" || x.mode == "const" { return }
    let s = x.sym
    if x.borrowed || (s != nil && s.isRef) { error("cannot move out of a reference: a reference only borrows") }
    if s != nil && s.iterating { error("cannot move '" + s.name + "' while a for loop walks it") }""")
rep("""        if s.deferUse { error("'" + s.name + "' is used by a defer and cannot be moved") }
        s.moved = true
        return""", """        if s.deferUse { error("'" + s.name + "' is used by a defer and cannot be moved") }
        s.moved = true
        invalidate(s, nil, true)                  // references into it now dangle
        return""")
rep("""        let s = ownedLocals[i]
        if !s.moved {
            let x = newItem("mem", symType(s))""", """        let s = ownedLocals[i]
        if s.owned { invalidate(s, nil, true) }
        if !s.moved && !s.isRefLocal {
            let x = newItem("mem", symType(s))""")

# designator: references are borrowed; invalid ones can't be used
rep("""        if s.owned && s.moved {
            if target {
                x.movedRef = true
            } else {
                error("'" + name + "' was moved and has no value here")
            }
        }""", """        x.borrowed = s.isRef
        if (s.owned || s.isRefLocal) && s.moved {
            if target {
                x.movedRef = true
            } else if s.isRefLocal {
                error("'" + name + "' may point to freed memory: something under what it points into was changed; point it again first")
            } else {
                error("'" + name + "' was moved and has no value here")
            }
        }""")
rep("""    if k == T_IDENT { return designator() }
    error("expected an expression")""", """    if k == T_AMP {
        // &place: borrow instead of moving
        next()
        if sym != T_IDENT { error("& needs a variable, field or element") }
        let x = designator()
        if (x.mode != "mem" && x.mode != "elem") || !isOwned(x.typ) {
            error("& needs a variable, field or element holding a record, closed type or array")
        }
        x.borrowed = true
        return x
    }
    if k == T_IDENT { return designator() }
    error("expected an expression")""")

# ------------------------------------------------------------- parameters
rep("""        expect(T_COLON, "")
        let t = parseType()
        for n in names { append(params, new Param{name: n, typ: t}) }""", """        expect(T_COLON, "")
        let t = parseRefType()
        let mode = refMode
        for n in names { append(params, new Param{name: n, typ: t, mode: mode}) }""")
rep("""                if !same(f.params[i], params[i].typ) { ok = false }""", """                if !same(f.params[i], params[i].typ) || f.pmodes[i] != params[i].mode { ok = false }""")
rep("""        for p in params { append(f.params, p.typ) }""", """        for p in params {
            append(f.params, p.typ)
            append(f.pmodes, p.mode)
        }""")
rep("""        if isOwned(params[i].typ) { addOwnedLocal(s) }""", """        if params[i].mode > 0 {
            s.isRef = true                         // borrowed: not freed here
            s.refMut = params[i].mode == 2
            s.refRoot = s
            s.kind = "let"
        } else if isOwned(params[i].typ) {
            addOwnedLocal(s)
        }""")
rep("""        checkAssign(f.params[i], a, "argument " + string(i + 1) + " of " + f.name)
        consume(a)""", """        checkAssign(f.params[i], a, "argument " + string(i + 1) + " of " + f.name)
        if f.pmodes[i] > 0 {
            let root = borrowRoot(a)
            if f.pmodes[i] == 2 && !mutableOf(a) {
                error("argument " + string(i + 1) + " of " + f.name + " needs a mut reference, but this one is read-only")
            }
            append(argRoots, root)
            append(argModes, f.pmodes[i])
            append(argVias, viaOf(a))
        } else {
            consume(a)
        }""")
rep("""    expect(T_LPAREN, "")
    for i in 0..n {
        if sym == T_RPAREN { error(f.name + " takes " + string(n) + " argument(s), not " + string(i)) }""", """    expect(T_LPAREN, "")
    let argRoots = new []?Sym(0)
    let argModes = new []int(0)
    let argVias = new []?Sym(0)
    for i in 0..n {
        if sym == T_RPAREN { error(f.name + " takes " + string(n) + " argument(s), not " + string(i)) }""")
rep("""    expect(T_RPAREN, "")
    userCalls += 1""", """    expect(T_RPAREN, "")
    // a mut reference and another reference into the same root could let the
    // callee free what the other points to
    for i in 0..len(argRoots) {
        for j in i + 1..len(argRoots) {
            let ri = argRoots[i]
            if ri != nil && ri == argRoots[j] && (argModes[i] == 2 || argModes[j] == 2) {
                error("cannot pass two references into '" + ri.name + "' to " + f.name + " when one of them is mut")
            }
        }
    }
    for i in 0..len(argRoots) {
        if argModes[i] == 2 { invalidate(argRoots[i], argVias[i], false) }
    }
    userCalls += 1""")

# -------------------------------------------------------------- locals
rep("""    var t: ?Type = nil
    if sym == T_COLON {
        next()
        t = parseType()
    }""", """    var t: ?Type = nil
    var declRef = 0
    if sym == T_COLON {
        next()
        t = parseRefType()
        declRef = refMode
        if declRef > 0 && isGlobal { error("references cannot be global") }
    }""")
rep("""        if isGlobal { fixHere1(initLink) }
        let x = expression()""", """        if isGlobal { fixHere1(initLink) }
        let x = expression()
        if !isGlobal && (declRef > 0 || (t == nil && x.borrowed)) {
            // a reference local: it borrows, nothing is moved or freed
            var rt = x.typ
            if t != nil { rt = t }
            checkAssign(rt, x, "initialising '" + name + "'")
            let root = borrowRoot(x)
            let v = newLocal("", rt, kind)
            v.name = name
            let dst = newItem("mem", rt)
            dst.base = RBP
            dst.disp = v.offset
            store(dst, x)
            declare(v)
            makeRefLocal(v, x, root, declRef == 1)
            return
        }
        if x.borrowed && t != nil && x.mode != "const" {
            error("cannot move out of a reference: declare '" + name + "' as &" + tname(t) + " to borrow")
        }""")

# ---------------------------------------------------------- assignment
rep("""        if o == T_ASSIGN {
            let callsBefore = userCalls""", """        let ws = rootOf(x)
        let wr = x.root
        if x.deref && wr != nil && wr.isRef && !wr.refMut {
            errorAt("cannot change through '" + wr.name + "': it is a read-only reference", line, col)
        }
        if o == T_ASSIGN && xs != nil && xs.isRefLocal {
            // pointing a reference somewhere else: nothing is moved or freed
            if xs.iterating { errorAt("cannot point '" + xs.name + "' elsewhere while a for loop walks it", line, col) }
            let y = expression()
            checkAssign(dtype, y, "assignment")
            let root = borrowRoot(y)
            if !assignable(x.typ, y.typ) { widen(xs) }
            store(x, y)
            xs.refRoot = root
            xs.refMut = mutableOf(y)
            xs.refDeep = deepOf(y)
            xs.moved = false
            return false
        }
        if o == T_ASSIGN {
            let callsBefore = userCalls""")
rep("""            if isOwned(dtype) {
                // free the old value first, unless the local's value was moved away""", """            if isOwned(dtype) {
                if xs != nil && xs.storage == "local" {
                    if xs.iterating { errorAt("cannot change '" + xs.name + "' while a for loop walks it", line, col) }
                    invalidate(xs, nil, true)
                } else if x.deref {
                    invalidate(ws, viaOf(x), false)
                }
                // free the old value first, unless the local's value was moved away""")

# ----------------------------------------------------- take and truncate
rep("""        if x.readonly { error("take cannot change '" + describe(x) + "': it is immutable") }""", """        if x.readonly { error("take cannot change '" + describe(x) + "': it is immutable") }
        let tr = x.root
        if x.deref && tr != nil && tr.isRef && !tr.refMut {
            error("take cannot change through '" + tr.name + "': it is a read-only reference")
        }
        if x.deref {
            invalidate(rootOf(x), viaOf(x), false)
        } else {
            let ts = x.sym
            if ts != nil && ts.storage == "local" { invalidate(ts, nil, true) }
        }""")
rep("""        noTemp(a)
        a = pinStr(a)""", """        noTemp(a)
        if name != "substr" {
            let ar = a.root
            if ar != nil && ar.isRef && !ar.refMut {
                error(name + " changes the array, but '" + ar.name + "' is a read-only reference")
            }
            if name == "truncate" { invalidate(rootOf(a), viaOf(a), false) }
        }
        a = pinStr(a)""")

# ------------------------------------------------------------- for loops
rep("""fn loopBody(entry: []bool, line: int, col: int): Loop {
    let loop = new Loop{brk: new []int(0), cont: new []int(0), blocks: len(blocks), entry: entry}""", """fn loopBody(entry: []bool, line: int, col: int): Loop {
    return loopBodyV(entry, line, col, nil)
}

// lv: a for loop's variable, rebound at the start of every iteration
fn loopBodyV(entry: []bool, line: int, col: int, lv: ?Sym): Loop {
    let loop = new Loop{brk: new []int(0), cont: new []int(0), blocks: len(blocks), entry: entry, lv: lv}""")
rep("""    let term = block()
    truncate(loops, len(loops) - 1)
    if !term {
        let d = stateDiff(entry, snapshot())
        if d != nil {
            errorAt("'" + d.name + "' is moved inside the loop; it must have a value again by the end of each iteration", line, col)
        }
    }""", """    let term = block()
    truncate(loops, len(loops) - 1)
    if lv != nil { lv.moved = false }
    if !term {
        let d = stateDiff(entry, snapshot())
        if d != nil {
            if d.isRefLocal {
                errorAt("'" + d.name + "' is invalidated inside the loop; point it again before the end of each iteration", line, col)
            }
            errorAt("'" + d.name + "' is moved inside the loop; it must have a value again by the end of each iteration", line, col)
        }
    }""")
rep("""            let loop = loops[len(loops) - 1]
            let d = stateDiff(loop.entry, snapshot())
            if d != nil {""", """            let loop = loops[len(loops) - 1]
            let st = snapshot()
            let lv = loop.lv
            if lv != nil {
                for q in 0..len(ownedLocals) {
                    if ownedLocals[q] == lv { st[q] = false }      // rebound next time round
                }
            }
            let d = stateDiff(loop.entry, st)
            if d != nil {""")
a = s.index("fn forStat()")
b = s.index("\nfn ", a + 10)
body = s[a:b]


def brep(old, new):
    global body
    assert body.count(old) == 1, old[:80]
    body = body.replace(old, new)


brep("""        noTemp(x)
        if isOwned(baseOf(x.typ)) {
            error("iterating over owned elements needs references, which come in milestone 2; index the array instead")
        }""", """        noTemp(x)
        let walked = x.sym
        if walked == nil || walked.storage != "local" {
            error("a for loop walks a local variable or parameter: borrow it first, for example let items = &p.items")
        }
        let owner = rootOf(x)""")
brep("""        let v = newLocal(name, baseOf(x.typ), "let")""", """        let v = newLocal(name, baseOf(x.typ), "let")
        if isOwned(baseOf(x.typ)) {
            // each element is borrowed, not moved out
            v.isRef = true
            v.isRefLocal = true
            v.refRoot = owner
            v.refMut = mutableOf(x)
            v.refDeep = true
            append(ownedLocals, v)
        }""")
brep("""        loopdepth += 1
        loop = loopBody(snapshot(), fline, fcol)
        loopdepth -= 1
        fixHere(loop.cont)
        aluMI(0, mem(RBP, -1, idx.offset, 8), 1)""", """        loopdepth += 1
        let wasIterating = walked.iterating
        walked.iterating = true
        loop = loopBodyV(snapshot(), fline, fcol, v)
        walked.iterating = wasIterating
        loopdepth -= 1
        fixHere(loop.cont)
        aluMI(0, mem(RBP, -1, idx.offset, 8), 1)""")
s = s[:a] + body + s[b:]
open(path, 'w').write(s)
print("references patch applied")
