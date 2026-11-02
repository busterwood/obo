"""Edits by script: the fork mutants whose patterns the rewrite of the reference bindings per path
replaced, retargeted to the same behaviour in the new code (tests/mutate_fork.py). Each anchor must
match exactly once in the source the mutant patches (src/obo.obo)."""
import os

HERE = os.path.dirname(os.path.abspath(__file__))
P = os.path.join(os.path.dirname(HERE), "tests", "mutate_fork.py")
t = open(P).read()

BLOCK = r'''# the mutants whose patterns the reference bindings per path replaced: retargeted to the same behaviour
RETARGET = {
    "writes through read-only references": ("if x.deref && wr != nil && wr.isRef && !wr.bind.writable {",
                                            "if x.deref && wr != nil && wr.isRef && !wr.bind.writable && false {"),
    "mut aliases allowed": ("if i != j && argModes[i] == 2 {", "if i != j && argModes[i] == 2 && false {"),
    "mut calls invalidate nothing": ("                invalidate(b.roots[p], argVias[i], false, argPaths[i])\n", ""),
    "read-only references can be passed as mut": ("            if f.pmodes[i] == 2 && !b.writable {", "            if false {"),
    "everything counts as an ancestor": ("        if p == a { return true }\n        p = p.bind.parent",
                                         "        if p == a || true { return true }\n        p = p.bind.parent"),
    "handle borrows are untracked": ("    if x.viaHandle { return singleBind(handleSym, deepOf(x), x.path, mutableOf(x), parentOf(x)) }\n",
                                     "    if x.viaHandle { return noBind }\n"),
    "loop moves unchecked": ("    if !term {\n        let now = snapshot()\n        let d = stateDiff(&entry, &now)",
                             "    if false {\n        let now = snapshot()\n        let d = stateDiff(&entry, &now)"),
    "aliases lose non-deep references too": ("if r != root && b.deeps[i] && reaches(root, r) { hit = true }",
                                             "if r != root && reaches(root, r) { hit = true }"),
    "calls check only one root": ("if bj.deeps[q] && reaches(ri, rj) && (ri != rj || pathsOverlap(bi.paths[p], bj.paths[q])) {",
                                  "if bj.deeps[q] && reaches(ri, rj) && ri == rj {"),
    "calls ignore depth": ("if bj.deeps[q] && reaches(ri, rj) && (ri != rj || pathsOverlap(bi.paths[p], bj.paths[q])) {",
                           "if reaches(ri, rj) && (ri != rj || pathsOverlap(bi.paths[p], bj.paths[q])) {"),
    "field changes invalidate siblings": ("(all || b.deeps[i] && underPath(b.paths[i], path))", "(all || b.deeps[i])"),
    "known elements treated as one": ("(ri != rj || pathsOverlap(bi.paths[p], bj.paths[q]))", "(ri != rj || true)"),
}
MUTANTS.update(RETARGET)
PORT_PATTERNS.update({k: v for k, v in RETARGET.items() if k in PORT_PATTERNS})


'''

anchor = "def main():\n"
assert t.count(anchor) == 1
t = t.replace(anchor, BLOCK + anchor)
open(P, "w").write(t)
print("ok")
