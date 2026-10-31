/* rt_heap_test: the direct oracle for the runtime heap limit and the bounded
 * allocation/free paths. It links runtime.c into its own image (so it may use
 * alloc_raw, new_block and the metadata globals) and has its own _entry, which
 * passes the kernel stack to rt_init; OBO_HEAP_LIMIT therefore reaches the
 * runtime exactly as it does for a compiled program.
 *
 * Each case runs in a process of its own: the driver runs this binary once per
 * case, with the case name in argv[1]. A normal case calls rt_finish (exit 0,
 * the leak oracle); the boundary cases end in the runtime's own traps. An
 * assertion failure exits 1.
 *
 *     python3 obo/tests/heap_test.py
 */
#include "../runtime/runtime.c"

static void fail(i64 code) { sys3(60, code, 0, 0); for (;;); }
#define CHECK(c) do { if (!(c)) fail(1); } while (0)

/* Argument strings live in .data, like a program's literals (address below the
 * heap, so rt_str_dec ignores them). */
static char cmstr[256];
static u64 cmstr_used;
static u64 mkstr(const char *s) {
    u64 n = 0; while (s[n]) n++;
    u64 off = (cmstr_used + 7) & ~7UL;
    u64 p = (u64)&cmstr[off];
    *(u64 *)p = n;
    memcpy((char *)p + 8, s, n);
    cmstr_used = off + 8 + ((n + 7) & ~7UL);
    return p;
}
static int c_eq(const char *a, const char *b) {
    while (*a && *a == *b) { a++; b++; }
    return *a == *b;
}

/* One descriptor in .data, as generated descriptors are. */
static u64 desc_scalar[3] = {8, 0, 0};

static void check_mapped(u64 want) {
    CHECK(heap_size == want);
    CHECK(heap_end - heap_start == want);
    CHECK(heap_start != 0 && starts != 0 && mstack != 0);
}

/* The compiled default: 4 GiB, allocation, handles, strings and a clean free. */
static void default_body(void) {
    u64 top = heap_top;

    Array *a = (Array *)rt_alloc_array(0, 1);
    for (u64 i = 0; i < 8; i++) rt_append(a, i + 1);
    CHECK(a->len == 8);
    u64 h = rt_handle((u64)a);
    CHECK(h != 0);
    CHECK(rt_deref(h) == (u64)a);

    u64 s = rt_concat(mkstr("heap"), mkstr("-ok"));
    CHECK(slen(s) == 7);
    CHECK(rt_streq(s, mkstr("heap-ok")));

    u64 r = rt_alloc_record(desc_scalar);
    rt_free(r);
    rt_str_dec(s);
    rt_free((u64)a);
    CHECK(heap_top > top);
    rt_finish();
}

static void case_default(void) {
    check_mapped(HEAP_DEFAULT);
    default_body();
}

/* The same work under an explicit limit; argv[2] is the canonical env value. */
static void case_limit(const char *want) {
    u64 n = 0;
    CHECK(heap_parse_size(want, &n));
    check_mapped(n);
    default_body();
}

/* A lowered limit is the arena, and freed blocks are reused from its free lists
 * rather than extending the bump pointer. */
static void case_reuse(void) {
    check_mapped(HEAP_MIN);
    u64 objs[64];
    for (u64 i = 0; i < 64; i++) objs[i] = alloc_raw(1, 16);
    u64 top = heap_top;
    for (u64 i = 0; i < 64; i += 2) rt_free(objs[i]);
    for (u64 i = 0; i < 32; i++) {
        u64 p = alloc_raw(1, 16);                 /* refill the even slots */
        CHECK(p >= heap_start + GRAN && p < heap_top);
        objs[2 * i] = p;
    }
    CHECK(heap_top == top);                       /* every refill came from a free list */
    /* A different size class still uses its own reclaimed block. */
    u64 big = alloc_raw(1, 64);
    u64 small = alloc_raw(1, 16);
    rt_free(small);
    u64 again = alloc_raw(1, 16);
    CHECK(again == small);
    rt_free(again);
    rt_free(big);
    for (u64 i = 0; i < 64; i++) rt_free(objs[i]);
    rt_finish();
}

/* A 16 GiB arena is sparse: only touched pages become resident. Objects and
 * strings are placed just across the 4 GiB and 8 GiB offsets and in the last
 * granule, so checked handles must carry granule offsets beyond 32 bits' worth
 * of 4 GiB but still inside the low 32-bit handle field. */
static void case_sparse(void) {
    check_mapped(HEAP_CEILING);
    u64 kept[10];
    u64 n = 0;

    heap_top = heap_start + (4UL << 30) - 64;
    for (int i = 0; i < 4; i++) kept[n++] = alloc_raw(1, 16);
    u64 s = rt_concat(mkstr("a"), mkstr("bc"));
    CHECK((s - heap_start) >= (4UL << 30));

    heap_top = heap_start + (8UL << 30) - 64;
    for (int i = 0; i < 4; i++) kept[n++] = alloc_raw(1, 16);

    heap_top = heap_end - 64;
    for (int i = 0; i < 2; i++) kept[n++] = alloc_raw(1, 16);
    CHECK(heap_top == heap_end);

    for (u64 i = 0; i < n; i++) {
        u64 h = rt_handle(kept[i]);
        CHECK(h != 0);
        CHECK((h & 0xFFFFFFFFUL) == (kept[i] - heap_start) / GRAN);
        CHECK(rt_deref(h) == kept[i]);
    }
    CHECK((kept[n - 2] - heap_start) >= (16UL << 30) - 64);

    u64 h0 = rt_handle(kept[0]);
    for (u64 i = 0; i < n; i++) rt_free(kept[i]);
    rt_str_dec(s);
    CHECK(rt_alive(h0) == 0);
    rt_finish();
}

/* The rounded object is exactly 4 GiB. The cap is checked before zeroing; the
 * 64 KiB arena makes an accidental zero loop fault instead of quietly touching
 * four gigabytes. */
static void case_object_too_large(void) {
    check_mapped(HEAP_MIN);
    alloc_raw(1, MAX_PAYLOAD + GRAN);              /* 0xFFFFFFF0 payload -> 4 GiB object */
    fail(1);
}

/* The exact largest payload passes the object cap and then fails the arena bump
 * with the ordinary out-of-memory message. */
static void case_at_cap(void) {
    check_mapped(HEAP_MIN);
    alloc_raw(1, MAX_PAYLOAD);
    fail(1);
}

/* A size product that wraps in 64 bits must be rejected before allocating. */
static void case_array_overflow(void) {
    check_mapped(HEAP_MIN);
    rt_alloc_array((i64)0x7FFFFFFFFFFFFFFFUL, 1);
    fail(1);
}

/* Two fake string lengths whose sum wraps, then a payload over the object cap. */
static u64 fake_a[2] = {0, 0};
static u64 fake_b[2] = {0, 0};
static void case_concat_overflow(void) {
    check_mapped(HEAP_MIN);
    fake_a[0] = ~0UL - 4;
    fake_b[0] = 16;
    rt_concat((u64)fake_a, (u64)fake_b);
    fail(1);
}

/* A fabricated full array whose cap doubling would wrap. */
static u64 fake_block[4] = {1, 32, 0, 0};
static Array fake_array;
static void case_append_overflow(void) {
    check_mapped(HEAP_MIN);
    fake_array.len = 0x4000000000000000UL;
    fake_array.cap = fake_array.len;
    fake_array.data = (u64)&fake_block[2];
    fake_array.esize = 8;
    rt_append(&fake_array, 1);
    fail(1);
}

/* A graph wider than the worklist must exit 6 at the push, never write past the
 * mapping. mcap is lowered here so the case is small and deterministic. */
static void case_free_depth(void) {
    check_mapped(HEAP_MIN);
    u64 c1 = alloc_raw(1, 8);
    u64 c2 = alloc_raw(1, 8);
    u64 *p = (u64 *)new_block(2, 8, 2);
    p[0] = 2;
    p[1] = c1;
    p[2] = c2;
    mcap = 1;
    rt_free((u64)p);
    fail(1);
}

/* Ordinary heap exhaustion keeps the established out-of-memory diagnostic. */
static void case_top_exhaust(void) {
    check_mapped(HEAP_MIN);
    for (;;) alloc_raw(1, 32);
}

void test_main(u64 *sp);
__asm__(".globl _entry\n"
        ".section .text\n"
        "_entry:\n"
        "\tmov %rsp, %rdi\n"
        "\tcall test_main\n");

void test_main(u64 *sp) {
    rt_init((u64)sp, 0, 0);
    u64 argc = *(u64 *)sp;
    char **argv = (char **)(sp + 1);
    if (argc < 2) fail(2);
    const char *c = argv[1];
    if (c_eq(c, "default")) case_default();
    else if (c_eq(c, "limit")) {
        if (argc < 3) fail(2);
        case_limit(argv[2]);
    }
    else if (c_eq(c, "reuse")) case_reuse();
    else if (c_eq(c, "sparse")) case_sparse();
    else if (c_eq(c, "object_too_large")) case_object_too_large();
    else if (c_eq(c, "at_cap")) case_at_cap();
    else if (c_eq(c, "array_overflow")) case_array_overflow();
    else if (c_eq(c, "concat_overflow")) case_concat_overflow();
    else if (c_eq(c, "append_overflow")) case_append_overflow();
    else if (c_eq(c, "free_depth")) case_free_depth();
    else if (c_eq(c, "top_exhaust")) case_top_exhaust();
    fail(3);
}
