"""Recursive fib, the same call tree as fib.obx: python3 fib.py [N]."""
import sys


def fib(n):
    if n < 2:
        return n
    return fib(n - 1) + fib(n - 2)


if __name__ == '__main__':
    print(fib(int(sys.argv[1]) if len(sys.argv) > 1 else 32))
