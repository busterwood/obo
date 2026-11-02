#!/usr/bin/env python3
"""Port step: a condition's narrowings are an owned linked list, not two arrays.

Every Item used to allocate two empty arrays for its narrowings (80 bytes each,
live until the Item dies), and copies or concatenates them where conditions are
negated or joined. An empty list is now nil, costing nothing; a list is its first
pair, each pair holding the rest. Negation moves the lists across, and && and ||
copy the left list onto the right one (joinPairs), since a copy is what each owner
needs anyway."""
import os
P = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'src', 'obo.obo')
s = open(P).read()

def sub(old, new):
    global s
    assert s.count(old) == 1, f"expected exactly one match for:\n{old}"
    s = s.replace(old, new)

sub("""type NPair = ref record {
    sym: ^Sym
    typ: ^Type
}
""", """type NPair = ref record {
    sym: ^Sym
    typ: ^Type
    next: ?NPair              // the rest of the list
}
""")

sub("""    nt, nf: []NPair           // narrowings valid when the condition holds / fails
""", """    nt, nf: ?NPair            // narrowings valid when the condition holds / fails (a list of pairs)
""")

sub("""                    nt: new []NPair(0), nf: new []NPair(0), width: 8, scale: 8}
""", """                    nt: nil, nf: nil, width: 8, scale: 8}
""")

sub("""fn copyPairs(a: &[]NPair): []NPair {
    let out = new []NPair(0)
    for v in a { append(out, new NPair{sym: v.sym, typ: v.typ}) }
    return out
}

fn concatPairs(a: &[]NPair, b: &[]NPair): []NPair {
    let out = new []NPair(0)
    for v in a { append(out, new NPair{sym: v.sym, typ: v.typ}) }
    for v in b { append(out, new NPair{sym: v.sym, typ: v.typ}) }
    return out
}
""", """// a copy of a's narrowings, then b's (b is moved in)
fn joinPairs(a: &?NPair, b: ?NPair): ?NPair {
    if a == nil { return b }
    return new NPair{sym: a.sym, typ: a.typ, next: joinPairs(&a.next, b)}
}
""")

sub("""        y.nt = copyPairs(c.nf)
        y.nf = copyPairs(c.nt)
""", """        y.nt = take(c.nf)
        y.nf = take(c.nt)
""")

sub("""        y.nt = concatPairs(x.nt, y.nt)
        y.nf = new []NPair(0)
""", """        y.nt = joinPairs(&x.nt, take(y.nt))
        y.nf = nil
""")

sub("""        y.nf = concatPairs(x.nf, y.nf)
        y.nt = new []NPair(0)
""", """        y.nf = joinPairs(&x.nf, take(y.nf))
        y.nt = nil
""")

sub("""fn pushNarrowings(lst: &[]NPair) {
    for p in lst { append(narrowStack, new NEntry{sym: p.sym, typ: p.typ, depth: loopdepth, alive: true}) }
}
""", """fn pushNarrowings(lst: &?NPair) {
    if lst == nil { return }
    append(narrowStack, new NEntry{sym: lst.sym, typ: lst.typ, depth: loopdepth, alive: true})
    pushNarrowings(&lst.next)
}
""")

sub("""    if narrowSym != nil {
        let p = new NPair{sym: narrowSym, typ: narrowType}
        if o == T_NE {
            append(z.nt, p)
        } else {
            append(z.nf, p)
        }
    }
""", """    if narrowSym != nil {
        if o == T_NE {
            z.nt = new NPair{sym: narrowSym, typ: narrowType, next: nil}
        } else {
            z.nf = new NPair{sym: narrowSym, typ: narrowType, next: nil}
        }
    }
""")

open(P, 'w').write(s)
print("patched", P)
