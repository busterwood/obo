/* Native fixture for the stage (a) argument kinds (tests/test_cinterop.py kinds()). */
long bytes_hash(const void *p, int n);
long str_hash(const void *p, int n);
long bytes_probe(const void *p, int n);
long str_probe(const void *p, int n);
long cstr_len(const char *s);
long rec_const(void *p);
long ui_echo(unsigned int v);
long i_echo(int v);
long ints_sum(const int *v, int n);
long ints_u_last(const unsigned int *v, int n);
long list_probe(const char *const *p, const int *l, int n);
long timing_probe(const void *p, int n, long g);
long scratch_live(void);
