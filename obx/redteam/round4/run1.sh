#!/bin/bash
# run1.sh file.obx: compile one program with ASan+UBSan and run it with OBX_CHECK=1
# (the C is left in build/one.c)
here=$(cd "$(dirname "$0")" && pwd)
mkdir -p "$here/build"
python3 "$here/../../ref/obxc.py" "$1" --emit-c > "$here/build/one.c" || exit
gcc -g -O0 -std=gnu11 -fsanitize=address,undefined -w -o "$here/build/one" "$here/build/one.c" -lm || exit
OBX_CHECK=1 "$here/build/one"
echo "exit=$?"
