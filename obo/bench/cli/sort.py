"""Byte-order line sort with the same rules as the repo's sort: read the file,
split on b'\\n', drop the empty tail, sort, write every line with a newline.
python3 sort.py FILE."""
import sys

if __name__ == '__main__':
    with open(sys.argv[1], 'rb') as f:
        data = f.read()
    lines = data.split(b'\n')
    if lines and lines[-1] == b'':
        lines.pop()
    lines.sort()
    if lines:
        sys.stdout.buffer.write(b'\n'.join(lines) + b'\n')
