/* Native fixture for the stage (b) return kinds and the stage (c) typed native fn form. */
#ifndef CINTEROP_RETURN_FIXTURE_H
#define CINTEROP_RETURN_FIXTURE_H

/* cbytes: a value pointer plus a length function. The length is authoritative; nothing calls strlen. */
const char *rf_cb_empty(void);
int rf_cb_empty_len(void);
const char *rf_cb_one(void);
int rf_cb_one_len(void);
const char *rf_cb_nul(void);
int rf_cb_nul_len(void);
const char *rf_cb_null_pos(void);
int rf_cb_null_pos_len(void);
const char *rf_cb_over(void);
int rf_cb_over_len(void);
const char *rf_cb_exact(void);
int rf_cb_exact_len(void);
/* Nine C parameters (three on the stack, an odd count): both cbytes calls must see the same expanded
 * arguments and the same 16-byte alignment. */
const char *rf_cb_many(const void *a, int n, const void *b, int m, int x, int y, int z, int w, int v);
int rf_cb_many_len(const void *a, int n, const void *b, int m, int x, int y, int z, int w, int v);
/* Eight C parameters (two stack words): the even stack-parity case for both cbytes calls. */
const char *rf_cb_even(const void *a, int n, const void *b, int m, int x, int y, int z, int w);
int rf_cb_even_len(const void *a, int n, const void *b, int m, int x, int y, int z, int w);
/* A deliberately wrong length function: long return, for the signature check. */
long rf_cb_even_len_wrong(const void *a, int n, const void *b, int m, int x, int y, int z, int w);

/* The int form (all bare i64) with each typed return. */
void rf_take(long v);
const void *rf_nz_i(long v);
const char *rf_cb_arg(long v);
int rf_cb_arg_len(long v);
const char *rf_cs_arg(long v);
const char *rf_cb_neg(void);
int rf_cb_neg_len(void);

/* 16 MiB cstr_out bound: a NUL at the last byte is accepted; no NUL within the bound exits 11. */
const char *rf_cs_big_ok(void);
const char *rf_cs_big_bad(void);

/* A C-only slot before an obo argument, and ints:len before its list. */
long rf_prefix(const char *p, int v);
long rf_lenfirst(int n, const int *v);

/* cstr_out: copy up to the first NUL. */
const char *rf_cs_normal(void);
const char *rf_cs_empty(void);
const char *rf_cs_null(void);
const char *rf_cs_tail(void);

/* u32 return: zero-extended, so 4294967295 stays positive. */
unsigned int rf_u32max(void);

/* void and nonzero. */
void rf_setv(int v);
long rf_lastv(void);
const void *rf_nz_null(void);
const void *rf_nz_ptr(void);

/* Typed native fn declarations with C-only slots and list expansion. */
long rf_add(long a, long b);
long rf_sum3(const int *v, int n, int bias);
long rf_nullconst(const char *p, int v);
long rf_tagged(const char *s, int tag);
long rf_bytes_arg(const void *p, int n);

#endif
