"""wc -l equivalent: count b'\\n' in 1 MiB reads, the same byte scan as the
repo's wc -l: python3 wc_lines.py FILE."""
import sys

if __name__ == '__main__':
    n = 0
    with open(sys.argv[1], 'rb', buffering=0) as f:
        while True:
            chunk = f.read(1 << 20)
            if not chunk:
                break
            n += chunk.count(b'\n')
    print(n)
