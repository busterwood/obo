#!/usr/bin/env python3
"""Port step 1: free a function's own symbols (parameters, locals, local types
and constants) when the function ends, instead of keeping them until exit.
Every symbol created after the function's prologue is unreachable once both of
its blocks are closed; a stale handle would trap (exit 7)."""
import os
P = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'port', 'obo_own_wip.obx')
s = open(P).read()

def sub(old, new):
    global s
    assert s.count(old) == 1, f"expected exactly one match for:\n{old}"
    s = s.replace(old, new)

sub("""    let frameAt = pc()
    emit32(0)
    openBlock()
    fnBlock = len(blocks) - 1
""", """    let frameAt = pc()
    emit32(0)
    let symMark = len(symArena)                 // the function's own symbols start here
    openBlock()
    fnBlock = len(blocks) - 1
""")
sub("""    code[frameAt + 3] = byte(size >> 24)
    curFn = nil
}
""", """    code[frameAt + 3] = byte(size >> 24)
    curFn = nil
    truncate(symArena, symMark)                 // and nothing refers to them any more
}
""")
open(P, 'w').write(s)
print("patched", P)
