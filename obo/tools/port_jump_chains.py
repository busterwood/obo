#!/usr/bin/env python3
"""Port step: jump lists are chains through the jumps themselves, not arrays.

A jump waiting for its target keeps the position of the next waiting jump in its
own rel32 field, and 0 after the last one (a jump is never at position 0, since an
opcode comes first). A list is then one int: its first waiting jump, or 0. Every
Item used to hold four arrays (two jump lists, two narrowing lists) and a copy of
each list was made wherever one was handed on; now a Item carries two ints. The
bytes are unchanged: fix() still writes each jump's final rel32 once."""
import os
P = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'src', 'obo.obo')
s = open(P).read()

def sub(old, new):
    global s
    assert s.count(old) == 1, f"expected exactly one match for:\n{old}"
    s = s.replace(old, new)

# the rel32 field doubles as the link of a waiting jump
sub("""fn fix(at: int, target: int) {
    let rel = target - (at + 4)
    code[at] = byte(rel)
    code[at + 1] = byte(rel >> 8)
    code[at + 2] = byte(rel >> 16)
    code[at + 3] = byte(rel >> 24)
}
""", """// A jump's rel32 field holds its displacement once the target is known, and until then
// the position of the next jump waiting for the same target (waitJump): 0 after the last.
fn putRel(at: int, v: int) {
    code[at] = byte(v)
    code[at + 1] = byte(v >> 8)
    code[at + 2] = byte(v >> 16)
    code[at + 3] = byte(v >> 24)
}

fn getRel(at: int): int {
    return int(code[at]) | (int(code[at + 1]) << 8) | (int(code[at + 2]) << 16) | (int(code[at + 3]) << 24)
}

fn fix(at: int, target: int) { putRel(at, target - (at + 4)) }
""")

sub("""type Loop = ref record {
    brk: []int
    cont: []int
""", """type Loop = ref record {
    brk: int                  // jumps waiting for the loop's end (a list: see waitJump)
    cont: int                 // jumps waiting for the next iteration
""")

sub("""    T, F: []int               // cond: jumps to the true and false targets
""", """    T, F: int                 // cond: jumps waiting for the true and false targets (lists: see waitJump)
""")

sub("""fn newItem(mode: string, t: ^Type): Item {
    return new Item{mode: mode, typ: t, base: -1, index: -1, r: -1, T: new []int(0), F: new []int(0),
""", """fn newItem(mode: string, t: ^Type): Item {
    return new Item{mode: mode, typ: t, base: -1, index: -1, r: -1, T: 0, F: 0,
""")

sub("""fn concatInts(a: &[]int, b: &[]int): []int {
    let out = new []int(0)
    for v in a { append(out, v) }
    for v in b { append(out, v) }
    return out
}

""", "")

sub("""fn fixHere(links: &[]int) {
    for at in links { fix(at, pc()) }
}

fn fixTo(links: &[]int, target: int) {
    for at in links { fix(at, target) }
}
""", """// A list of jumps waiting for one target is 0 when empty, else the first of them; see putRel.
fn waitJump(head: int, at: int): int {
    putRel(at, head)
    return at
}

fn joinWaits(a: int, b: int): int {
    if a == 0 { return b }
    var at = a
    while getRel(at) != 0 { at = getRel(at) }
    putRel(at, b)
    return a
}

fn fixTo(head: int, target: int) {
    var at = head
    while at != 0 {
        let next = getRel(at)
        fix(at, target)
        at = next
    }
}

fn fixHere(head: int) { fixTo(head, pc()) }
""")

sub("""    } else if x.mode == "cond" {
        r = alloc()
        if len(x.T) == 0 && len(x.F) == 0 {
""", """    } else if x.mode == "cond" {
        r = alloc()
        if x.T == 0 && x.F == 0 {
""")
sub("""            append(x.F, jcc(x.cc ^ 1))
            fixHere(x.T)
            movRI(r, 1)
""", """            x.F = waitJump(x.F, jcc(x.cc ^ 1))
            fixHere(x.T)
            movRI(r, 1)
""")

sub("""fn falseJump(mut x: &Item): []int {
    append(x.F, jcc(x.cc ^ 1))
    fixHere(x.T)
    return copyInts(x.F)
}
""", """fn falseJump(mut x: &Item): int {
    x.F = waitJump(x.F, jcc(x.cc ^ 1))
    fixHere(x.T)
    return x.F
}
""")

sub("""        y.T = copyInts(c.F)
        y.F = copyInts(c.T)
""", """        y.T = c.F
        y.F = c.T
""")

sub("""        y.F = concatInts(y.F, flinks)
""", """        y.F = joinWaits(y.F, flinks)
""")

sub("""        append(x.T, jcc(x.cc))
        fixHere(x.F)
        let tlinks = copyInts(x.T)
        let mark = len(narrowStack)
        pushNarrowings(x.nf)
        condDepth += 1
        let y = loadCond(conjunction())
        condDepth -= 1
        truncate(narrowStack, mark)
        y.T = concatInts(y.T, tlinks)
""", """        x.T = waitJump(x.T, jcc(x.cc))
        fixHere(x.F)
        let mark = len(narrowStack)
        pushNarrowings(x.nf)
        condDepth += 1
        let y = loadCond(conjunction())
        condDepth -= 1
        truncate(narrowStack, mark)
        y.T = joinWaits(y.T, x.T)
""")

sub("""    var flinks = new []int(0)
""", """    var flinks = 0
""")
sub("""    return forever && len(loop.brk) == 0
""", """    return forever && loop.brk == 0
""")

sub("""    append(loops, new Loop{brk: new []int(0), cont: new []int(0), blocks: len(blocks), entry: copyBools(entry), lv: lv,
""", """    append(loops, new Loop{brk: 0, cont: 0, blocks: len(blocks), entry: copyBools(entry), lv: lv,
""")
sub("""    let res = new Loop{brk: copyInts(loops[li].brk), cont: copyInts(loops[li].cont), blocks: loops[li].blocks,
""", """    let res = new Loop{brk: loops[li].brk, cont: loops[li].cont, blocks: loops[li].blocks,
""")
sub("""    var loop = new Loop{brk: new []int(0), cont: new []int(0), blocks: 0,""", """    var loop = new Loop{brk: 0, cont: 0, blocks: 0,""")

sub("""            if k == T_BREAK {
                append(cur.brk, jmp())
            } else {
                append(cur.cont, jmp())
            }
""", """            if k == T_BREAK {
                cur.brk = waitJump(cur.brk, jmp())
            } else {
                cur.cont = waitJump(cur.cont, jmp())
            }
""")

sub("""    let seen = new []int(0)
    let ends = new []int(0)
""", """    let seen = new []int(0)
    var ends = 0
""")
sub("""    let seen = new []^Type(0)
    let ends = new []int(0)
""", """    let seen = new []^Type(0)
    var ends = 0
""")
sub("""        let toBody = new []int(0)
""", """        var toBody = 0
""")
sub("""            append(toBody, jcc(CC_E))
""", """            toBody = waitJump(toBody, jcc(CC_E))
""")
sub("""            have = true
        }
        append(ends, jmp())
""", """            have = true
        }
        ends = waitJump(ends, jmp())
""")
sub("""        truncate(narrowStack, mark)
        append(ends, jmp())
""", """        truncate(narrowStack, mark)
        ends = waitJump(ends, jmp())
""")

open(P, 'w').write(s)
print("patched", P)
