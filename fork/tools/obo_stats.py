#!/usr/bin/env python3
"""OBO_STATS=1: at the normal end of a program, print the live peak (bytes held
at once) and the total allocated to stderr, so tests can hold memory to a budget.
The figures are exact and repeatable, unlike resident memory."""
import os
P = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'runtime', 'runtime.c')
s = open(P).read()

def sub(old, new):
    global s
    assert s.count(old) == 1, f"expected exactly one match for:\n{old}"
    s = s.replace(old, new)

sub("static int leak_check;\n", "static int leak_check;\nstatic int stats;                  /* OBO_STATS=1: report memory at exit */\n")
sub('        if (starts_with(*env, "OBO_CHECK=1")) leak_check = 1;\n',
    '        if (starts_with(*env, "OBO_CHECK=1")) leak_check = 1;\n'
    '        if (starts_with(*env, "OBO_STATS=1")) stats = 1;\n')
sub("""void rt_finish(void) {
    flush();
""", """void rt_finish(void) {
    flush();
    if (stats) {                       /* to stderr, leaving the program's output alone */
        i64 was = out_fd;
        out_fd = 2;
        puts_("obo: "); put_int((i64)peak_bytes); puts_(" peak bytes, ");
        put_int((i64)total_alloc); puts_(" allocated\\n");
        flush();
        out_fd = was;
    }
""")
open(P, 'w').write(s)
print("patched", P)
