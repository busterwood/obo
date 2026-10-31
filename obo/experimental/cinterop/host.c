/* Experimental hosted startup for the cinterop prototype.
 *
 * The normal dynamic loader and __libc_start_main have already initialized libc,
 * TLS, constructors and IFUNCs. obo's rt_init expects the process's initial stack
 * shape, so build a small block {argc, argv..., NULL, envp..., NULL} and jump to
 * the fixed obo entry. This is a prototype boundary, not a production runtime
 * entry contract. */
#include <stdint.h>

extern void _obo_launch(uint64_t *sbase);

static unsigned char obo_stack[1 << 20] __attribute__((aligned(16)));

int main(int argc, char **argv, char **envp) {
    uint64_t *p = (uint64_t *)(obo_stack + sizeof(obo_stack));
    int en = 0;
    while (envp && envp[en]) en++;
    *--p = 0;
    for (int i = en - 1; i >= 0; i--) *--p = (uint64_t)envp[i];
    *--p = 0;
    for (int i = argc - 1; i >= 0; i--) *--p = (uint64_t)argv[i];
    *--p = (uint64_t)argc;
    if (((uintptr_t)p & 15) != 0) *--p = 0;
    _obo_launch(p);
    return 0;
}
