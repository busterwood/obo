# legacy/obx.py

The original obx compiler, written in Python. It is no longer used to build or
test anything: obx is self-hosting, and `src/obxc.obx` is the compiler.

It is kept for reference, and because `seed/obxc` was first built with it:
`python3 legacy/obx.py src/obxc.obx -o obxc` produced the same bytes as the
self-hosted compiler did at the moment the Python version was retired.
