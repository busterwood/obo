"""Generational handles (^T) in obo-on-obx.obx."""
import sys
path = sys.argv[1]
s = open(path).read()


def rep(old, new, count=1):
    global s
    n = s.count(old)
    assert n == count, (n, old[:100])
    s = s.replace(old, new)


rep("""const RT_STR_DEC = 25""", """const RT_STR_DEC = 25
const RT_HANDLE = 26
const RT_DEREF = 27
const RT_ALIVE = 28""")
rep("""    borrowed: bool            // the value is a reference (borrowed, not owned)""", """    borrowed: bool            // the value is a reference (borrowed, not owned)
    viaHandle: bool           // the designator went through a handle""")

# a match reads the type tag as 32 bits: the high half of the header word is the block's generation
rep("""    opMem(0x8B, r, mem(r, -1, -16, 8), true, false)""", """    opMem(0x8B, r, mem(r, -1, -16, 8), false, false)          // the tag; the high half is a generation""")

# types
rep("""fn tname(t: Type): string {
    if t.kind == "array" { return "[]" + tname(baseOf(t)) }""", """fn tname(t: Type): string {
    if t.kind == "array" { return "[]" + tname(baseOf(t)) }
    if t.kind == "handle" { return "^" + tname(baseOf(t)) }""")
rep("""fn nonNull(t: Type): bool { return t.kind == "record" || t.kind == "closed" || t.kind == "array" }""",
    """fn nonNull(t: Type): bool { return t.kind == "record" || t.kind == "closed" || t.kind == "array" || t.kind == "handle" }""")
rep("""    if a.kind == "array" || a.kind == "opt" { return same(baseOf(a), baseOf(b)) }""",
    """    if a.kind == "array" || a.kind == "opt" || a.kind == "handle" { return same(baseOf(a), baseOf(b)) }""")
rep("""    if dst.kind == "closed" && src.kind == "record" && src.parent == dst { return true }""", """    if dst.kind == "closed" && src.kind == "record" && src.parent == dst { return true }
    if dst.kind == "handle" && src.kind == "handle" { return assignable(baseOf(dst), baseOf(src)) }""")
rep("""    if sym == T_LBRACK {
        next()
        expect(T_RBRACK, "")
        return newType("array", "", parseType())
    }""", """    if sym == T_LBRACK {
        next()
        expect(T_RBRACK, "")
        return newType("array", "", parseType())
    }
    if sym == T_CARET {
        next()
        let b = parseType()
        if b.kind != "record" && b.kind != "closed" { error("handles name records and closed types, not " + tname(b)) }
        return newType("handle", "", b)
    }""")

# built-ins: handle(place) and alive(h)
rep("""    append(universe, newSym("builtin", "take", nil))""", """    append(universe, newSym("builtin", "take", nil))
    append(universe, newSym("builtin", "handle", nil))
    append(universe, newSym("builtin", "alive", nil))""")
rep("""    if name == "take" {""", """    if name == "handle" {
        // a handle to an object: copyable, storable, checked on every use
        expect(T_LPAREN, "")
        if sym != T_IDENT { error("handle needs a variable, field or element") }
        let x = designator()
        expect(T_RPAREN, "")
        if (x.mode != "mem" && x.mode != "elem") || (x.typ.kind != "record" && x.typ.kind != "closed") {
            error("handle needs a record or closed-type value that is not optional, not " + tname(x.typ))
        }
        let ht = newType("handle", "", x.typ)
        return rtCall1(RT_HANDLE, x, ht)
    }
    if name == "alive" {
        expect(T_LPAREN, "")
        let h = expression()
        expect(T_RPAREN, "")
        if h.typ.kind != "handle" { error("alive needs a handle, not " + tname(h.typ)) }
        return rtCall1(RT_ALIVE, h, tBool)
    }
    if name == "take" {""")

# designators: a field through a handle checks the handle first
rep("""    let root = x.root
    while sym == T_DOT || sym == T_LBRACK {""", """    let root = x.root
    var viaHandle = false
    while sym == T_DOT || sym == T_LBRACK {""")
rep("""        if sym == T_DOT {
            next()
            let fname = ident()""", """        if sym == T_DOT && x.typ.kind == "handle" {
            // check the handle now: the object must still be the one it named
            let hb = baseOf(x.typ)
            x = rtCall1(RT_DEREF, x, hb)
            viaHandle = true
        }
        if sym == T_DOT {
            next()
            let fname = ident()""")
rep("""            if root != nil && root.storage == "global" && userCalls != callsBefore {""",
    """            if viaHandle && userCalls != callsBefore {
                error("cannot index through a handle with an index that calls a function: the call could free the object")
            }
            if root != nil && root.storage == "global" && userCalls != callsBefore {""")
rep("""    if x.sym == nil && root != nil {
        x.root = root
        x.deref = true""", """    x.viaHandle = viaHandle
    if x.sym == nil && root != nil {
        x.root = root
        x.deref = true""")
rep("""fn borrowRoot(x: Item): ?Sym {
    if x.mode == "const" { return nil }""", """fn borrowRoot(x: Item): ?Sym {
    if x.mode == "const" { return nil }
    if x.viaHandle { error("cannot borrow through a handle: pass the handle itself") }""")
rep("""            if x.deref && r0 != nil && r0.storage == "global" && isOwned(symType(r0)) && userCalls != callsBefore {""",
    """            if x.viaHandle && userCalls != callsBefore {
                errorAt("cannot assign through a handle across a call: the call could free the object; compute the value first", line, col)
            }
            if x.deref && r0 != nil && r0.storage == "global" && isOwned(symType(r0)) && userCalls != callsBefore {""")
open(path, 'w').write(s)
print("handles patch applied")
