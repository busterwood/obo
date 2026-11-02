/* Stage (f) out-parameter fixture: an owned C handle written through an out slot. */
#ifndef OBO_OUT_FIXTURE_H
#define OBO_OUT_FIXTURE_H

typedef struct Of Of;

/* Counters: one live counter, one release counter, and the bytes-copy recorder. */
void of_reset(void);
long of_live(void);
long of_closed(void);
long of_last_dtor(void);
long of_last_len(void);

/* out_own: success, failure with a non-NULL handle, and failure with NULL. */
int of_new(int v, Of **out);
int of_zero(Of **out);
int of_new_fail(int v, Of **out);
int of_new_null(int v, Of **out);

/* Seven C parameters: the out slot is the seventh and goes on the stack. */
int of_wide(int a, int b, int c, int d, int e, int f, Of **out);

/* open_v2 shape: out slot between input C parameters. */
int of_open(const char *path, Of **out, int flags, const char *unused);

/* prepare_v2 shape: a trailing out_own and out_offset. */
int of_prepare(Of *db, const void *sql, int n, Of **st, const char **tail);

/* out_offset modes: 0 exact+2, 1 zero, 2 len, 3 NULL, 4 before-base, 5 past-end. */
int of_tail(const void *s, int n, int mode, const char **tail);

/* const:-1 destructor slot: records the destructor word and copies len bytes. */
int of_bind(const void *s, int n, void (*dtor)(void *), Of **out);

int of_get(const Of *o);
void of_release(Of *o);

#endif
