#!/usr/bin/env python3
"""Port step: the scanner keeps its source as a string, so that a token's text is one
substr() instead of a byte array copied element by element and then converted.
Each identifier used to cost a byte array (a header and a block) and a string; now
it costs the string alone. The source is only ever read by index, which strings allow."""
import os
P = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'src', 'obo.obo')
s = open(P).read()

def sub(old, new, count=1):
    global s
    assert s.count(old) == count, f"expected {count} match(es) for:\n{old}"
    s = s.replace(old, new)

sub("var src = new []byte(0)\n", "var src = \"\"                  // the source text, read by index\n")

sub("""fn text(from: int, to: int): string {
    let b = new []byte(0)
    for i in from..to { append(b, src[i]) }
    return string(b)
}

""", "")

sub("        sval = text(start, pos)\n", "        sval = substr(src, start, pos)\n")
sub("            fail(\"unexpected character '\" + text(pos, pos + 1) + \"'\")\n",
    "            fail(\"unexpected character '\" + substr(src, pos, pos + 1) + \"'\")\n")

sub("""fn startScanner(source: []byte) {
    src = source
""", """fn startScanner(source: []byte) {
    src = string(source)
""")

open(P, 'w').write(s)
print("patched", P)
