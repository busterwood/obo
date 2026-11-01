/* Null and const slot fixture (tests/test_cinterop.py null_const_slots()). Each function is
 * declared with the C type a real prototype would have. The two mismatched prototypes are
 * used only by the negative specs, which the signature check must refuse. */
struct foo;
struct bar;

long nc_cstr_null(const char *s);
long nc_struct_null(struct foo *f);
long nc_destructor(const char *s, void (*d)(void *));
long nc_int_const(int v);
long nc_bytes_wrong(struct foo *p, int n);
long nc_struct_null_bad(struct foo *f);
