"""Reference row only: Python's C-level sum(range(n)), not an equivalent of the
explicit loop. It shows what the same arithmetic costs when the loop is in C:
python3 sumrange.py [N]."""
import sys

if __name__ == '__main__':
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 100_000_000
    print(sum(range(n)))
