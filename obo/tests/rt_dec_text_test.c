/* rt_dec_text_test: the public decimal text runtime under the real heap
 * controls.  It links runtime.c into its own image and has its own _entry, so
 * OBO_HEAP_LIMIT reaches rt_init exactly as it does for a compiled program.
 *
 * Cases:
 *   max_output       the 98,342-byte negative display form and its spare NUL
 *   try_bad          alloc_raw_try refuses an over-cap payload and a full arena
 *   try_reuse        alloc_raw_try reuses a freed block without growing the arena
 *   parse10/format10/canonical10/check10
 *                    with exactly 32 bytes left, the err cell fits but the
 *                    24-byte ok cell (48-byte block) or the >32-byte text does
 *                    not: code 10, exact message, no fatal OOM
 *   bad_exp_sign / bad_exp_coeff / bad_exp_flags / bad_exp_pad / bad_dscale_*
 *                    malformed backing bits trap with diagnostic 43
 *
 *     python3 obo/tests/heap_test.py
 */
#include "../runtime/runtime.c"

static void fail(i64 code) { sys3(60, code, 0, 0); for (;;); }
#define CHECK(c) do { if (!(c)) fail(1); } while (0)

/* Length-prefixed static strings, like a program's literals. */
static u64 static_str(const char *s) {
    /* The test strings live in .data; the length is stored in the first word. */
    static char buf[256];
    static u64 used;
    u64 n = 0; while (s[n]) n++;
    u64 off = (used + 7) & ~7UL;
    u64 p = (u64)&buf[off];
    *(u64 *)p = n;
    memcpy((char *)p + 8, s, n);
    used = off + 8 + ((n + 7) & ~7UL);
    return p;
}

static int c_eqn(const char *a, const char *b, u64 n) {
    for (u64 i = 0; i < n; i++) if (a[i] != b[i]) return 0;
    return 1;
}

static int c_eq(const char *a, const char *b) {
    while (*a && *a == *b) { a++; b++; }
    return *a == *b;
}

static void check_mapped(u64 want) {
    CHECK(heap_size == want);
    CHECK(heap_end - heap_start == want);
    CHECK(heap_start != 0 && starts != 0 && mstack != 0);
}

static int msg_is(u64 cell, const char *want) {
    u64 msg = ((u64 *)cell)[0];
    u64 n = 0; while (want[n]) n++;
    return slen(msg) == n && c_eqn(sdata(msg), want, n);
}

/* Descriptors in .data, with the compiler's field layout. */
static u64 desc_err[4] = {16, 0, 1, 0};      /* message string at offset 0 */
static u64 desc_dec[3] = {24, 0, 0};         /* plain 24-byte payload */
static u64 desc_str[4] = {8, 0, 1, 0};       /* string at offset 0 */

static void free_err(u64 cell) {
    CHECK(((u64 *)cell)[1] == 10);
    CHECK(msg_is(cell, "obo: decimal result could not be allocated"));
    rt_free(cell);
}

static void check_err(u64 cell, i64 code, const char *msg) {
    CHECK(((u64 *)cell)[1] == code);
    CHECK(msg_is(cell, msg));
    rt_free(cell);
}

/* The exact maximum display form: 38 nines, exponent 32767, dscale 65535.
 * The negative sign makes the required text length 98,342 bytes. */
static void case_max_output(void) {
    dec24 v;
    CHECK(dec_set(&v, 1, (u64)DEC_COEFF_MAX_LO, (u64)DEC_COEFF_MAX_HI,
                  32767, 65535) == DEC_OK);
    u64 c = rt_dec_format_text(&v, (u64)desc_err, (u64)desc_str);
    u64 s = ((u64 *)c)[0];
    CHECK(slen(s) == 98342);
    const char *p = sdata(s);
    CHECK(p[98342] == '\0');                   /* the spare NUL is written */
    CHECK(p[0] == '-');
    CHECK(p[98342 - 1] == '0');
    rt_free(c);

    CHECK(dec_set(&v, 0, (u64)DEC_COEFF_MAX_LO, (u64)DEC_COEFF_MAX_HI,
                  32767, 65535) == DEC_OK);
    c = rt_dec_format_text(&v, (u64)desc_err, (u64)desc_str);
    s = ((u64 *)c)[0];
    CHECK(slen(s) == 98341);
    CHECK(sdata(s)[98341] == '\0');
    rt_free(c);
    rt_finish();
}

static void case_try_bad(void) {
    check_mapped(HEAP_MIN);
    CHECK(alloc_raw_try(1, MAX_PAYLOAD + GRAN) == 0);
    CHECK(alloc_raw_try(1, MAX_PAYLOAD) == 0);
    rt_finish();
}

static void case_try_reuse(void) {
    check_mapped(HEAP_MIN);
    u64 a = alloc_raw_try(1, 16);
    CHECK(a != 0);
    u64 top = heap_top;
    rt_free(a);
    u64 b = alloc_raw_try(1, 16);
    CHECK(b == a);
    CHECK(heap_top == top);
    rt_free(b);
    rt_finish();
}

/* The fatal allocator keeps its two distinct diagnostics. */
static void case_alloc_too_large(void) {
    check_mapped(HEAP_MIN);
    alloc_raw(1, MAX_PAYLOAD + GRAN);
    fail(1);
}

static void case_alloc_oom(void) {
    check_mapped(HEAP_MIN);
    alloc_raw(1, MAX_PAYLOAD);
    fail(1);
}

/* Exactly 32 bytes remain: the err cell (16 + 16 header) fits, while a 24-byte
 * ok payload (48-byte block) or a 42-byte text block does not. */
static void exhaust_to_err_block(void) {
    CHECK(heap_end - heap_top >= 32);
    heap_top = heap_end - 32;
}

static void case_parse10(void) {
    check_mapped(HEAP_MIN);
    u64 text = static_str("1");
    exhaust_to_err_block();
    u64 c = rt_dec_parse_text(text, (u64)desc_err, (u64)desc_dec);
    free_err(c);
    rt_finish();
}

static void case_check10(void) {
    check_mapped(HEAP_MIN);
    dec24 v;
    CHECK(dec_set(&v, 0, 1, 0, 0, 0) == DEC_OK);
    i64 req[2] = {1, 0};
    exhaust_to_err_block();
    u64 c = rt_dec_check(&v, req, (u64)desc_err, (u64)desc_dec);
    free_err(c);
    rt_finish();
}

static void case_format10(void) {
    check_mapped(HEAP_MIN);
    dec24 v;                                    /* 1e-40: 42 bytes of text */
    CHECK(dec_set(&v, 0, 1, 0, -40, 40) == DEC_OK);
    exhaust_to_err_block();
    u64 c = rt_dec_format_text(&v, (u64)desc_err, (u64)desc_str);
    free_err(c);
    rt_finish();
}

static void case_canonical10(void) {
    check_mapped(HEAP_MIN);
    dec24 v;
    CHECK(dec_set(&v, 0, 1, 0, -40, 40) == DEC_OK);
    exhaust_to_err_block();
    u64 c = rt_dec_canonical_text(&v, (u64)desc_err, (u64)desc_str);
    free_err(c);
    rt_finish();
}

/* Malformed raw values must trap, not return a Result. */
static void case_bad_exp_sign(void) {
    dec24 v;
    dec_zero(&v);
    v.sign = 2;
    rt_dec_exponent(&v);
    fail(1);
}

static void case_bad_exp_coeff(void) {
    dec24 v;
    dec_zero(&v);
    v.lo = ~0UL;
    v.hi = ~0UL;                                /* > 10^38 - 1 */
    rt_dec_exponent(&v);
    fail(1);
}

static void case_bad_exp_flags(void) {
    dec24 v;
    dec_zero(&v);
    v.lo = 1;
    v.flags = 1;
    rt_dec_exponent(&v);
    fail(1);
}

static void case_bad_exp_pad(void) {
    dec24 v;
    dec_zero(&v);
    v.lo = 1;
    v.pad[1] = 1;
    rt_dec_exponent(&v);
    fail(1);
}

static void case_bad_dscale_sign(void) {
    dec24 v;
    dec_zero(&v);
    v.lo = 1;
    v.sign = 2;
    rt_dec_dscale(&v);
    fail(1);
}

static void case_bad_dscale_coeff(void) {
    dec24 v;
    dec_zero(&v);
    v.lo = ~0UL;
    v.hi = ~0UL;
    rt_dec_dscale(&v);
    fail(1);
}

/* The Result-returning text APIs report malformed backing bits as code 4
 * rather than trapping. */
static void case_bad_format(void) {
    dec24 v;
    dec_zero(&v);
    v.lo = 1;
    v.sign = 2;
    u64 c = rt_dec_format_text(&v, (u64)desc_err, (u64)desc_str);
    check_err(c, DEC_INVALID, "obo: invalid decimal value");
    rt_finish();
}

static void case_bad_canonical(void) {
    dec24 v;
    dec_zero(&v);
    v.lo = ~0UL;
    v.hi = ~0UL;
    u64 c = rt_dec_canonical_text(&v, (u64)desc_err, (u64)desc_str);
    check_err(c, DEC_INVALID, "obo: invalid decimal value");
    rt_finish();
}

static void case_bad_check(void) {
    dec24 v;
    dec_zero(&v);
    v.lo = 1;
    v.flags = 1;
    i64 req[2] = {10, 2};
    u64 c = rt_dec_check(&v, req, (u64)desc_err, (u64)desc_dec);
    check_err(c, DEC_INVALID, "obo: invalid decimal value");
    rt_finish();
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
    if (c_eq(c, "max_output")) case_max_output();
    else if (c_eq(c, "try_bad")) case_try_bad();
    else if (c_eq(c, "try_reuse")) case_try_reuse();
    else if (c_eq(c, "alloc_too_large")) case_alloc_too_large();
    else if (c_eq(c, "alloc_oom")) case_alloc_oom();
    else if (c_eq(c, "parse10")) case_parse10();
    else if (c_eq(c, "check10")) case_check10();
    else if (c_eq(c, "format10")) case_format10();
    else if (c_eq(c, "canonical10")) case_canonical10();
    else if (c_eq(c, "bad_exp_sign")) case_bad_exp_sign();
    else if (c_eq(c, "bad_exp_coeff")) case_bad_exp_coeff();
    else if (c_eq(c, "bad_exp_flags")) case_bad_exp_flags();
    else if (c_eq(c, "bad_exp_pad")) case_bad_exp_pad();
    else if (c_eq(c, "bad_dscale_sign")) case_bad_dscale_sign();
    else if (c_eq(c, "bad_dscale_coeff")) case_bad_dscale_coeff();
    else if (c_eq(c, "bad_format")) case_bad_format();
    else if (c_eq(c, "bad_canonical")) case_bad_canonical();
    else if (c_eq(c, "bad_check")) case_bad_check();
    fail(3);
}
