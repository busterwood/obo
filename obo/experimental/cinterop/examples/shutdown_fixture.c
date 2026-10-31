/* Linked C fixture for the hosted shutdown tests. host.c calls the weak
 * obo_host_prelaunch hook after libc is initialized and before obo runs. */
#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>

__attribute__((constructor)) static void obo_test_ctor(void) {
    static const char msg[] = "ctor-ran\n";
    (void)write(2, msg, sizeof(msg) - 1);
}

static void obo_test_atexit(void) {
    static const char msg[] = "atexit-ran\n";
    (void)write(2, msg, sizeof(msg) - 1);
}

void obo_host_prelaunch(void) {
    (void)atexit(obo_test_atexit);
    /* Deliberately no newline and no fflush: libc exit() must flush this. */
    (void)printf("cstdio-flush");
}
