/* Experimental hosted startup for the cinterop prototype.
 *
 * The host crt1.o has already called __libc_start_main, so libc is initialized.
 * obo's rt_init expects the process's initial pointer block
 * {argc, argv..., NULL, envp..., NULL}; build it at the top of a separate
 * guarded mmap stack and enter the fixed obo image there. The block carries no
 * auxv because rt_init does not read one.
 *
 * The usable stack has PROT_NONE guard pages below and above it. The base is
 * aligned before writing argc, and a 128 KiB growth headroom is checked before
 * any write. This remains a supported-subset entry contract: constructors/TLS/
 * IFUNC are not claimed merely because the host CRT is linked.
 *
 * Normal/exit/leak/panic paths enter host_exit through the hosted runtime's
 * appended set-host-exit slot. host_exit calls libc exit(), so C stdio is
 * flushed and atexit handlers run. The baseline freestanding runtime is built
 * without this variant and keeps its raw-syscall exit. */
#include "host_stack.h"
#include "../../runtime/host_abi.h"

#include <stdint.h>
#include <stdlib.h>
#include <unistd.h>

#define OBO_STACK_HEADROOM (128u << 10)

extern void _obo_launch(uint64_t *sbase);

/* Optional strong definition from a linked C fixture; used by tests to register
 * atexit/stdio state before the obo image runs. */
__attribute__((weak)) void obo_host_prelaunch(void);

static void startup_fail(const char *msg) {
    size_t n = 0;
    while (msg[n] != 0) n++;
    (void)write(2, msg, n);
    _exit(127);
}

static void startup_fail_stack(obo_guard_stack *s, const char *msg) {
    obo_guard_stack_destroy(s);
    startup_fail(msg);
}

static int add_overflow(size_t a, size_t b, size_t *out) {
    if (a > (size_t)-1 - b) return 1;
    *out = a + b;
    return 0;
}

static int parse_size(const char *s, size_t *out) {
    size_t v = 0;
    if (s == NULL || *s == 0) return 0;
    for (; *s != 0; s++) {
        unsigned d;
        if (*s < '0' || *s > '9') return 0;
        d = (unsigned)(*s - '0');
        if (v > ((size_t)-1 - d) / 10) return 0;
        v = v * 10 + d;
    }
    *out = v;
    return 1;
}

static void host_exit(long code) {
    exit((int)code);
    _exit((int)code);
}

int main(int argc, char **argv, char **envp) {
    obo_guard_stack stack;
    size_t requested = OBO_GUARD_STACK_DEFAULT;
    size_t env_count = 0;
    size_t words = 0;
    size_t bytes;
    uintptr_t top, start;
    uint64_t *p;
    const char *size_env;
    char err[160];

    if (argc < 0) startup_fail("obo: cinterop invalid argc\n");
    size_env = getenv("OBO_CINTEROP_STACK_BYTES");
    if (size_env != NULL && !parse_size(size_env, &requested)) {
        startup_fail("obo: cinterop invalid stack size\n");
    }
    if (!obo_guard_stack_init(&stack, requested, err, sizeof(err))) {
        startup_fail(err);
    }

    while (envp && envp[env_count]) env_count++;
    if (add_overflow((size_t)argc, 3, &words)) {
        startup_fail_stack(&stack, "obo: cinterop startup stack overflow\n");
    }
    if (add_overflow(words, env_count, &words)) {
        startup_fail_stack(&stack, "obo: cinterop startup stack overflow\n");
    }
    if (words > stack.usable / sizeof(uint64_t)) {
        startup_fail_stack(&stack, "obo: cinterop startup stack overflow\n");
    }
    bytes = words * sizeof(uint64_t);
    top = (uintptr_t)stack.hi;
    if (bytes > stack.usable) {
        startup_fail_stack(&stack, "obo: cinterop startup stack overflow\n");
    }

    /* Align the base before writing argc. Any pad is above the block. */
    start = (top - bytes) & ~(uintptr_t)15;
    if (start < (uintptr_t)stack.lo + OBO_STACK_HEADROOM) {
        startup_fail_stack(&stack, "obo: cinterop startup stack overflow\n");
    }

    p = (uint64_t *)start;
    p[0] = (uint64_t)argc;
    for (int i = 0; i < argc; i++) p[1 + i] = (uint64_t)argv[i];
    p[1 + argc] = 0;
    for (size_t i = 0; i < env_count; i++) p[2 + (size_t)argc + i] = (uint64_t)envp[i];
    p[2 + (size_t)argc + env_count] = 0;

    /* The runtime callback is set before obo code runs, so normal finish,
     * exit(), OBO_CHECK failures and runtime panics all leave through libc. */
    ((rt_host_exit_fn)RT_HOST_EXIT_ADDR)((rt_host_exit_fn)host_exit);
    if (obo_host_prelaunch != 0) obo_host_prelaunch();

    _obo_launch(p);
    obo_guard_stack_destroy(&stack);
    return 0;
}
