"""Plain-integer numeric sort reference for the fixed-width 8-digit fixture:
sorted(lines, key=int), the same lines and output as `sort -n` on this data
(no signs, blanks or fractions, and fixed width makes integer order equal to
byte order). It is a reference row, not an implementation of GNU's full
strnumcmp: python3 sort_n.py FILE."""
import sys

if __name__ == '__main__':
    with open(sys.argv[1], 'rb') as f:
        data = f.read()
    lines = data.split(b'\n')
    if lines and lines[-1] == b'':
        lines.pop()
    lines.sort(key=int)
    if lines:
        sys.stdout.buffer.write(b'\n'.join(lines) + b'\n')
