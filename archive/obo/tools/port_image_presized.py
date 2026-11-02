#!/usr/bin/env python3
"""Port step: the ELF image is allocated once, at its final size.

elfImage used to start from a 4 KB header and append the code, the rodata and the
runtime, each on a page boundary, so the buffer doubled as it grew: at the last
doubling the old and new buffers were live together, while the code was still
live as well. The layout is known from the sizes of the parts, so the image now
gets its size first and each part is copied into place. The bytes are the same:
the padding was zero before, and a new array is zero."""
import os
P = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'src', 'obo.obo')
s = open(P).read()

def sub(old, new):
    global s
    assert s.count(old) == 1, f"expected exactly one match for:\n{old}"
    s = s.replace(old, new)

sub("""fn pageAlign(mut b: &[]byte) {
    while len(b) % 0x1000 != 0 { append(b, 0) }
}
""", """fn pageUp(n: int): int { return (n + 0xFFF) / 0x1000 * 0x1000 }
""")

sub("""// Same layout as obx.py: headers and code, then rodata, the globals
// (memory only), and the runtime's code and data at fixed addresses.
fn elfImage(rtText: []byte, rtData: []byte): []byte {
    let out = new []byte(0x1000)
    for cI in 0..len(code) {
        let c = code[cI]
        append(out, c)
    }
    let ph = new []byte(0)
    let codeEnd = len(out)
    phdr(ph, 5, 0, IMAGE_BASE, codeEnd, codeEnd)
    pageAlign(out)
    var off = len(out)
    var roLen = len(rodata)
    for i in 0..roLen { append(out, rodata[i]) }
    if roLen == 0 {
        append(out, 0)                               // an empty segment still gets one byte
        roLen = 1
    }
    phdr(ph, 4, off, RODATA_BASE, roLen, roLen)
    pageAlign(out)
    var gmem = gsize
    if gmem < 8 { gmem = 8 }
    phdr(ph, 6, 0, GLOBAL_BASE, 0, gmem)
    pageAlign(out)
    off = len(out)
    for c in rtText { append(out, c) }
    phdr(ph, 5, off, RT_TEXT, len(rtText), len(rtText))
    pageAlign(out)
    off = len(out)
    for c in rtData { append(out, c) }
    phdr(ph, 6, off, RT_DATA, len(rtData), len(rtData))
""", """// copies a part of the image into place
fn copyAt(mut out: &[]byte, at: int, data: &[]byte) {
    for i in 0..len(data) { out[at + i] = data[i] }
}

// Same layout as obx.py: headers and code, then rodata, the globals
// (memory only), and the runtime's code and data at fixed addresses. Each part
// starts on a page, and the image is allocated once, at its final size.
fn elfImage(rtText: []byte, rtData: []byte): []byte {
    let codeAt = 0x1000
    let roAt = pageUp(codeAt + len(code))
    var roLen = len(rodata)
    if roLen == 0 { roLen = 1 }                      // an empty segment still gets one byte
    let rtTextAt = pageUp(roAt + roLen)
    let rtDataAt = pageUp(rtTextAt + len(rtText))
    let out = new []byte(rtDataAt + len(rtData))
    copyAt(out, codeAt, code)
    copyAt(out, roAt, rodata)
    copyAt(out, rtTextAt, rtText)
    copyAt(out, rtDataAt, rtData)
    let ph = new []byte(0)
    phdr(ph, 5, 0, IMAGE_BASE, codeAt + len(code), codeAt + len(code))
    phdr(ph, 4, roAt, RODATA_BASE, roLen, roLen)
    var gmem = gsize
    if gmem < 8 { gmem = 8 }
    phdr(ph, 6, 0, GLOBAL_BASE, 0, gmem)
    phdr(ph, 5, rtTextAt, RT_TEXT, len(rtText), len(rtText))
    phdr(ph, 6, rtDataAt, RT_DATA, len(rtData), len(rtData))
""")

open(P, 'w').write(s)
print("patched", P)
