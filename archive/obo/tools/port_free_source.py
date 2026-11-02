#!/usr/bin/env python3
"""Port step: the source text is freed once the module is compiled. The scanner
was its only reader, and the image is built from the code and the runtime, so the
142 KB of source no longer stays live while the image is built."""
import os
P = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'src', 'obo.obo')
s = open(P).read()

def sub(old, new):
    global s
    assert s.count(old) == 1, f"expected exactly one match for:\n{old}"
    s = s.replace(old, new)

sub("""    startScanner(data)
    compileModule()
    let image = elfImage(rtText, rtData)
""", """    startScanner(data)
    compileModule()
    src = ""                       // the source is no longer read: free it before the image is built
    let image = elfImage(rtText, rtData)
""")

open(P, 'w').write(s)
print("patched", P)
