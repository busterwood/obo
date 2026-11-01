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
#include "foreign_abi.h"
#include "host_stack.h"
#include "../../runtime/host_abi.h"

#include <limits.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#define OBO_STACK_HEADROOM (128u << 10)

extern void _obo_launch(uint64_t *sbase);

/* Optional strong definition from a linked C fixture; used by tests to register
 * atexit/stdio state before the obo image runs. */
__attribute__((weak)) void obo_host_prelaunch(void);

/* Hidden visibility keeps the obo_ci_* helpers out of the dynamic symbol table, so a shared library
 * cannot bind to them. It does not make the names private to the link: a second object defining the
 * same obo_ci_* name in the same executable is still a duplicate-symbol link error. */
#define CI_HIDDEN __attribute__((visibility("hidden")))

/* ---- native argument kinds (stage (a)): helpers the generated thunks call ----
 *
 * These read obo values, so the thunk calls them only after every argument
 * expression has run and right before the C call. The layouts are the runtime's
 * (runtime/runtime.c): an array is {len, cap, data, esize} with its elements at
 * data + 8; a string is [len][bytes] with 0 for the nil string. The helpers
 * return scratch memory the thunk frees after the call (obo_ci_free). A contract
 * violation exits 11, as in design section 3.2 and D4; these exits run no census,
 * as a trap does. */
static long ci_live = 0;                       /* scratch blocks not yet freed */
static const uint64_t ci_nil_string[1] = {0};  /* the nil string: a valid, non-NULL, zero-length pointer */

static void ci_fail(const char *msg, int code) {
    size_t n = 0;
    while (msg[n] != 0) n++;
    (void)write(2, msg, n);
    exit(code);
}

static void *ci_alloc(size_t n) {
    void *p = malloc(n ? n : 1);
    if (p == NULL) ci_fail("obo: out of memory\n", 2);
    ci_live++;
    return p;
}

CI_HIDDEN void obo_ci_free(void *p) {
    if (p != NULL) {
        ci_live--;
        free(p);
    }
}

CI_HIDDEN long obo_ci_scratch_live(void) { return ci_live; }

static uint64_t ci_array_len(uint64_t a) { return *(const uint64_t *)(uintptr_t)a; }
static uint64_t ci_array_data(uint64_t a) { return *(const uint64_t *)(uintptr_t)(a + 16); }
static uint64_t ci_element_base(uint64_t a) { return ci_array_data(a) + 8; }

static uint64_t ci_checked_len(uint64_t n) {
    if (n > (uint64_t)INT_MAX) ci_fail("obo: native length does not fit int\n", 11);
    return n;
}

CI_HIDDEN long obo_ci_i32(long v) {
    if (v < (long)INT32_MIN || v > (long)INT32_MAX) ci_fail("obo: native i32 argument out of range\n", 11);
    return v;
}

CI_HIDDEN long obo_ci_u32(long v) {
    if (v < 0 || v > (long)0xFFFFFFFFUL) ci_fail("obo: native u32 argument out of range\n", 11);
    return v;
}

/* borrow_bytes, data pointer: a []byte (is_string 0) or a string (is_string 1). Never NULL. */
CI_HIDDEN const char *obo_ci_bytes_ptr(long v, long is_string) {
    if (is_string) {
        if (v == 0) return (const char *)ci_nil_string;
        return (const char *)(uintptr_t)((uint64_t)v + 8);
    }
    return (const char *)(uintptr_t)ci_element_base((uint64_t)v);
}

/* borrow_bytes, length: checked to fit int. */
CI_HIDDEN long obo_ci_bytes_len(long v, long is_string) {
    if (is_string) {
        if (v == 0) return 0;
        return (long)ci_checked_len(*(const uint64_t *)(uintptr_t)v);
    }
    return (long)ci_checked_len(ci_array_len((uint64_t)v));
}

/* borrow_cstr: a NUL-terminated copy. An embedded NUL exits 11 (D4). */
CI_HIDDEN char *obo_ci_cstr(const char *p, long n) {
    char *c;
    if (n > 0 && memchr(p, 0, (size_t)n) != NULL) ci_fail("obo: native C string contains a NUL byte\n", 11);
    c = ci_alloc((size_t)n + 1);
    if (n > 0) memcpy(c, p, (size_t)n);
    c[n] = 0;
    return c;
}

/* ints@k:i32 (is_u32 0) and ints@k:u32 (is_u32 1): a copied int array, each element range-checked. */
CI_HIDDEN void *obo_ci_ints(long arr, long is_u32) {
    uint64_t n = ci_checked_len(ci_array_len((uint64_t)arr));
    const int64_t *src = (const int64_t *)(uintptr_t)ci_element_base((uint64_t)arr);
    void *out = ci_alloc((size_t)n * 4);
    for (uint64_t i = 0; i < n; i++) {
        int64_t v = src[i];
        if (is_u32) {
            if (v < 0 || v > (int64_t)0xFFFFFFFFUL) ci_fail("obo: native u32 array element out of range\n", 11);
            ((uint32_t *)out)[i] = (uint32_t)v;
        } else {
            if (v < (int64_t)INT32_MIN || v > (int64_t)INT32_MAX) ci_fail("obo: native i32 array element out of range\n", 11);
            ((int32_t *)out)[i] = (int32_t)v;
        }
    }
    return out;
}

/* ints@k:len: the element count of a list, checked to fit int. */
CI_HIDDEN long obo_ci_count(long arr) { return (long)ci_checked_len(ci_array_len((uint64_t)arr)); }

/* nil_bytes_list@k, the pointer array: a NULL for a nil element, and a non-NULL pointer for an empty one. */
CI_HIDDEN const char **obo_ci_list_ptrs(long list) {
    uint64_t n = ci_checked_len(ci_array_len((uint64_t)list));
    const uint64_t *el = (const uint64_t *)(uintptr_t)ci_element_base((uint64_t)list);
    const char **out = ci_alloc((size_t)n * sizeof(char *));
    for (uint64_t i = 0; i < n; i++) {
        out[i] = el[i] == 0 ? NULL : (const char *)(uintptr_t)ci_element_base(el[i]);
    }
    return out;
}

/* nil_bytes_list@k, the length array: 0 for a nil element. */
CI_HIDDEN int *obo_ci_list_lens(long list) {
    uint64_t n = ci_checked_len(ci_array_len((uint64_t)list));
    const uint64_t *el = (const uint64_t *)(uintptr_t)ci_element_base((uint64_t)list);
    int *out = ci_alloc((size_t)n * sizeof(int));
    /* el[i] is an element of the list: a []byte array, so ci_array_len reads its length. */
    for (uint64_t i = 0; i < n; i++) {
        out[i] = el[i] == 0 ? 0 : (int)ci_checked_len(ci_array_len(el[i]));
    }
    return out;
}

/* ---- native results (stage (b), design 3.3): helpers the generated thunks call ----
 *
 * A copy-out allocates a normal obo []byte array through the runtime's jump-table slot 3
 * (rt_alloc_array, runtime.c). The raw runtime blob has no symbols, so this goes through the fixed
 * address in host_abi.h. alloc_raw counts it in live_owned, so OBO_CHECK=1 leak checking applies as
 * usual. The array layout is runtime.c's Array {len, cap, data, esize}, with its elements at data + 8.
 */
#define CI_CSTR_OUT_MAX (16L << 20)   /* 16 MiB, design 3.3 */

static uint64_t ci_new_byte_array(uint64_t n) {
    rt_alloc_array_fn alloc = (rt_alloc_array_fn)RT_ALLOC_ARRAY_ADDR;
    return alloc((int64_t)n, 4);      /* kind 4 = byte elements */
}

static void *ci_byte_array_data(uint64_t a) {
    uint64_t data = *(const uint64_t *)(uintptr_t)(a + 16);
    return (void *)(uintptr_t)(data + 8);
}

/* cbytes: copy n bytes from p into a new obo array. n < 0, or NULL with n > 0, exits 11. Above cap
 * gives nil (0), which the wrapper turns into err. n == 0 gives an empty array. NULs are preserved:
 * this never calls strlen. */
CI_HIDDEN const void *obo_ci_copy_out(const void *p, long n, long cap) {
    uint64_t a;
    if (n < 0) ci_fail("obo: native copy-out length is negative\n", 11);
    if (p == NULL && n > 0) ci_fail("obo: native copy-out is NULL with a positive length\n", 11);
    if (n > cap) return NULL;
    a = ci_new_byte_array((uint64_t)n);
    if (n > 0) memcpy(ci_byte_array_data(a), p, (size_t)n);
    return (const void *)(uintptr_t)a;
}

/* cstr_out: copy up to the first NUL into a new obo array. NULL gives an empty array; no NUL within
 * 16 MiB exits 11 rather than reading past the bound. */
CI_HIDDEN const void *obo_ci_cstr_out(const char *p) {
    uint64_t n = 0, a;
    if (p == NULL) return (const void *)(uintptr_t)ci_new_byte_array(0);
    while (n < (uint64_t)CI_CSTR_OUT_MAX && p[n] != 0) n++;
    if (n == (uint64_t)CI_CSTR_OUT_MAX) ci_fail("obo: native C string result has no NUL within 16 MiB\n", 11);
    a = ci_new_byte_array(n);
    if (n > 0) memcpy(ci_byte_array_data(a), p, (size_t)n);
    return (const void *)(uintptr_t)a;
}

/* ---- foreign handles (stage (d), design 3.4) ----
 *
 * A foreign type is an ordinary obo record with one private word at offset 0. The word is in neither
 * the owned nor the string offset list of its descriptor, so rt_free frees the block and never reads
 * or releases the C pointer. The live count for each type is kept here; the generated foreign table
 * (foreign_abi.h) supplies the release wrapper and the obo type name. */

static long ci_foreign_live[OBO_CI_FOREIGN_MAX];
static int ci_census_fault_underflow;

static void ci_write_str(int fd, const char *s) {
    size_t n = 0;
    while (s[n] != 0) n++;
    while (n > 0) {
        ssize_t w = write(fd, s, n);
        if (w <= 0) return;
        s += w;
        n -= (size_t)w;
    }
}

static void ci_write_int(int fd, unsigned long v) {
    char b[24];
    int i = 24;
    do { b[--i] = (char)('0' + v % 10); v /= 10; } while (v != 0);
    while (i < 24) {
        ssize_t w = write(fd, &b[i], (size_t)(24 - i));
        if (w <= 0) return;
        i += (int)w;
    }
}

/* The host foreign census, called by rt_finish between the fd and object censuses. It reports each
 * non-zero type and exits 8 once; with no OBO_CHECK=1 rt_finish never calls it. */
static void ci_foreign_census(void) {
    int any = 0;
    for (long i = 0; i < obo_ci_foreign_type_count; i++) {
        if (ci_foreign_live[i] == 0) continue;
        any = 1;
        ci_write_str(2, "obo: foreign handles leaked: ");
        ci_write_str(2, obo_ci_foreign_types[i].name);
        ci_write_str(2, " ");
        ci_write_int(2, (unsigned long)ci_foreign_live[i]);
        ci_write_str(2, "\n");
    }
    if (any) exit(8);
}

CI_HIDDEN long obo_ci_foreign_census_adjust(long id, long delta) {
    if (id < 0 || id >= obo_ci_foreign_type_count)
        ci_fail("obo: foreign census type id out of range\n", 11);
    if (delta < 0) {
        if (ci_census_fault_underflow) ci_foreign_live[id] = 0;
        if (ci_foreign_live[id] == 0)
            ci_fail("obo: internal error: foreign handle census underflow\n", 4);
        ci_foreign_live[id] -= 1;
    } else {
        ci_foreign_live[id] += delta;
    }
    return (long)ci_foreign_live[id];
}

static uint64_t ci_alloc_record(uint64_t desc) {
    rt_alloc_record_fn alloc = (rt_alloc_record_fn)RT_ALLOC_RECORD_ADDR;
    return alloc((const uint64_t *)(uintptr_t)desc);
}

/* borrow<T>: the C word for a live record; a nil record or a zero word exits 7 before the call. */
CI_HIDDEN const void *obo_ci_foreign_word(const void *rec) {
    const uint64_t *r = (const uint64_t *)rec;
    if (rec == NULL || r[0] == 0) ci_fail("obo: foreign handle used after close\n", 7);
    return (const void *)(uintptr_t)r[0];
}

/* own<T> / own_opt<T>: allocate a normal record of desc, store the raw C word, count it, return the
 * record pointer. own with NULL exits 11; own_opt with NULL returns nil. */
CI_HIDDEN const void *obo_ci_foreign_own(long id, const void *raw, long desc, long optional) {
    uint64_t rec;
    if (id < 0 || id >= obo_ci_foreign_type_count)
        ci_fail("obo: foreign type id out of range\n", 11);
    if (raw == NULL) {
        if (optional) return NULL;
        ci_fail("obo: native own returned NULL\n", 11);
    }
    rec = ci_alloc_record((uint64_t)desc);
    *(uint64_t *)(uintptr_t)rec = (uint64_t)(uintptr_t)raw;
    (void)obo_ci_foreign_census_adjust(id, 1);
    return (const void *)(uintptr_t)rec;
}

/* close: word 0 gives ok(false) and makes no C call. Otherwise zero the word, decrement the census,
 * release, then return ok(true) or, for an int release with a non-zero result, err(msg, code). */
CI_HIDDEN void *obo_ci_foreign_close(long id, void *rec, long okdesc, long errdesc, long msg) {
    uint64_t word;
    uint64_t cell;
    long rc;
    if (id < 0 || id >= obo_ci_foreign_type_count)
        ci_fail("obo: foreign type id out of range\n", 11);
    word = (rec != NULL) ? *(uint64_t *)rec : 0;
    if (word == 0) {
        cell = ci_alloc_record((uint64_t)okdesc);
        *(uint64_t *)(uintptr_t)cell = 0;
        return (void *)(uintptr_t)cell;
    }
    *(uint64_t *)rec = 0;                      /* before the release: a trap cannot release twice */
    (void)obo_ci_foreign_census_adjust(id, -1);
    rc = obo_ci_foreign_types[id].release((void *)(uintptr_t)word);
    if (obo_ci_foreign_types[id].release_result != 0 && rc != 0) {
        cell = ci_alloc_record((uint64_t)errdesc);
        *(uint64_t *)(uintptr_t)cell = (uint64_t)msg;
        *((uint64_t *)(uintptr_t)cell + 1) = (uint64_t)rc;
        return (void *)(uintptr_t)cell;
    }
    cell = ci_alloc_record((uint64_t)okdesc);
    *(uint64_t *)(uintptr_t)cell = 1;
    return (void *)(uintptr_t)cell;
}

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
    /* The census fault hook is inert unless OBO_CHECK=1, like OBO_IO_FAULT and OBO_META_FAULT. */
    {
        const char *check = getenv("OBO_CHECK");
        const char *fault = getenv("OBO_CENSUS_FAULT");
        if (check != NULL && check[0] == '1' && fault != NULL && strcmp(fault, "underflow") == 0) {
            ci_census_fault_underflow = 1;
        }
    }
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
    ((rt_set_foreign_census_fn)RT_FOREIGN_CENSUS_ADDR)(ci_foreign_census);
    if (obo_host_prelaunch != 0) obo_host_prelaunch();

    _obo_launch(p);
    obo_guard_stack_destroy(&stack);
    return 0;
}
