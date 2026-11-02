"""The sieve of Eratosthenes, the same bytearray sieve as sieve.obx:
python3 sieve.py [N]."""
import sys

if __name__ == '__main__':
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 10_000_000
    composite = bytearray(n + 1)
    count = 0
    i = 2
    while i <= n:
        if not composite[i]:
            count += 1
            j = i * i
            while j <= n:
                composite[j] = 1
                j += i
        i += 1
    print(count)
