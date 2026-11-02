"""wc -lwc equivalent: lines, words and bytes over 1 MiB reads, the same
counts as the repo's wc -lwc on this suite's ASCII fixture.

Words are maximal runs of non-whitespace bytes, with the chunk-boundary carry
below. That is the C-locale word rule for ASCII text; the fixture contains no
NUL or high bytes, where GNU wc's rule and bytes.split() differ (see
README.md): python3 wc_full.py FILE."""
import sys

if __name__ == '__main__':
    lines = words = total = 0
    carry = False
    with open(sys.argv[1], 'rb', buffering=0) as f:
        while True:
            chunk = f.read(1 << 20)
            if not chunk:
                break
            total += len(chunk)
            lines += chunk.count(b'\n')
            parts = chunk.split()
            n = len(parts)
            if carry and n and not chunk[0:1].isspace():
                n -= 1
            words += n
            carry = bool(parts) and not chunk[-1:].isspace()
    print(lines, words, total)
