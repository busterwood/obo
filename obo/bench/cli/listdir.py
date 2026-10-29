"""Name-only directory listing sorted bytewise, matching the repo eza's non-TTY
default on this suite's plain ASCII names: python3 listdir.py DIR."""
import os
import sys

if __name__ == '__main__':
    names = sorted(os.listdir(sys.argv[1]))
    if names:
        sys.stdout.write('\n'.join(names) + '\n')
