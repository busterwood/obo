/* Guarded-stack fixture. The low/high modes deliberately touch a PROT_NONE
 * guard page and are expected to die by SIGSEGV with no handler installed. */
#include "../host_stack.h"

#include <stdio.h>
#include <string.h>

int main(int argc, char **argv) {
    obo_guard_stack s;
    char err[160];
    const char *mode = (argc > 1) ? argv[1] : "ok";

    if (strcmp(mode, "invalid") == 0) {
        if (obo_guard_stack_init(&s, 4096, err, sizeof(err))) {
            fprintf(stderr, "invalid size unexpectedly accepted\n");
            obo_guard_stack_destroy(&s);
            return 3;
        }
        printf("invalid_rejected=%s\n", err);
        return 0;
    }
    if (!obo_guard_stack_init(&s, OBO_GUARD_STACK_DEFAULT, err, sizeof(err))) {
        fprintf(stderr, "init failed: %s\n", err);
        return 2;
    }
    printf("usable=%zu page=%zu\n", s.usable, s.page);
    fflush(stdout);
    if (strcmp(mode, "low") == 0) {
        *(volatile unsigned char *)(s.lo - 1) = 1;
        return 10; /* not reached */
    }
    if (strcmp(mode, "high") == 0) {
        *(volatile unsigned char *)s.hi = 1;
        return 11; /* not reached */
    }
    obo_guard_stack_destroy(&s);
    printf("guard_stack=ok\n");
    return 0;
}
