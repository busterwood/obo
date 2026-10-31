/* Experimental hosted startup for the cinterop prototype.
 *
 * The host crt1.o has already called __libc_start_main, so libc is initialized.
 * obo's rt_init expects the process's initial stack shape, so build a block
 * {argc, argv..., NULL, envp..., NULL} and jump to the fixed obo entry.
 *
 * The block is placed 16-byte aligned with its padding ABOVE it, never between
 * the base pointer and argc. The 1 MiB buffer is checked before any write and
 * keeps a stack-growth headroom; an environment that does not fit fails cleanly
 * instead of writing below the buffer. This remains a prototype entry contract:
 * constructors/TLS/IFUNC and normal termination are not claimed. */
#include <stdint.h>
#include <unistd.h>

#define OBO_STACK_BYTES (1u << 20)
#define OBO_STACK_HEADROOM (128u << 10)

extern void _obo_launch(uint64_t *sbase);

static unsigned char obo_stack[OBO_STACK_BYTES] __attribute__((aligned(16)));

static void startup_fail(void) {
    static const char msg[] = "obo: cinterop startup stack overflow\n";
    (void)write(2, msg, sizeof(msg) - 1);
    _exit(127);
}

static int add_overflow(size_t a, size_t b, size_t *out) {
    if (a > (size_t)-1 - b) return 1;
    *out = a + b;
    return 0;
}

int main(int argc, char **argv, char **envp) {
    if (argc < 0) startup_fail();

    size_t env_count = 0;
    while (envp && envp[env_count]) env_count++;

    /* argc + argv NULL + envp NULL */
    size_t words = 0;
    if (add_overflow((size_t)argc, 3, &words)) startup_fail();
    if (add_overflow(words, env_count, &words)) startup_fail();
    if (words > sizeof(obo_stack) / sizeof(uint64_t)) startup_fail();

    uintptr_t top = (uintptr_t)(obo_stack + sizeof(obo_stack));
    size_t bytes = words * sizeof(uint64_t);
    if (bytes > (size_t)(top - (uintptr_t)obo_stack)) startup_fail();

    /* Align the base BEFORE writing argc. Any pad lies above the block, not
       between the base pointer and the argc word. */
    uintptr_t start = (top - bytes) & ~(uintptr_t)15;
    if (start < (uintptr_t)obo_stack + OBO_STACK_HEADROOM) startup_fail();

    uint64_t *p = (uint64_t *)start;
    p[0] = (uint64_t)argc;
    for (int i = 0; i < argc; i++) p[1 + i] = (uint64_t)argv[i];
    p[1 + argc] = 0;
    for (size_t i = 0; i < env_count; i++) p[2 + (size_t)argc + i] = (uint64_t)envp[i];
    p[2 + (size_t)argc + env_count] = 0;

    _obo_launch(p);
    return 0;
}
