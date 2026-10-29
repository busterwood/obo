"""Add 0..n-1 with an explicit while loop, the same loop as loopsum.obx:
python3 loopsum.py [N]."""
import sys

if __name__ == '__main__':
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 100_000_000
    s = 0
    i = 0
    while i < n:
        s += i
        i += 1
    print(s)
