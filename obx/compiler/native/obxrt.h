/* The native runtime's interface: what generated code may call (design/native.md).
   The native backend parses this text first (it is embedded in rtx.obx by gen_rtx.py), in
   place of ref/rt.h, whose static functions the generated C would otherwise define; rt.c,
   the freestanding runtime, includes it, so gcc checks that every definition matches. It
   is written in the C subset the backend reads: no macros, one declaration per line. The
   integer typedefs (int8_t ... uint64_t, size_t, bool) are built into the backend. */

typedef struct { int64_t rc; char data[]; } obx_sblk;
typedef struct { obx_sblk* b; uint32_t off, len; } obx_str;
typedef struct { void* data; int64_t len, cap; } obx_arr;
typedef struct { void* data; int64_t len; } obx_slice;
typedef struct { void* fn; void* env; } obx_clo;
typedef __int128 i128;
typedef unsigned __int128 u128;
typedef void (*rt_dfn)(void*);

/* process */
void rt_init(int argc, char** argv);
void rt_finish(void);
void rt_exit(int64_t code);
void rt_trap(int code, const char* msg, const char* where);
void rt_panic(obx_str msg, const char* w);
void rt_flush(void);

/* memory and drop actions */
void* rt_alloc(size_t n);
void rt_free(void* p);
void rt_dpush(rt_dfn f, void* p);
void rt_drun(rt_dfn f, void* p);
void rt_free_act(void* p);
void rt_ddefer(rt_dfn f, void* p, size_t n);

/* the handle of lib/task's Shared[T] (its dup and drop glue) */
void rt_sh_dup(void* p);
void rt_sh_drop(void* p);

/* a call of a blocking extern (SPEC 12): its thunk on the argument slots, on the helper pool */
void rt_blocking(rt_dfn fn, void* a, const char* w);

/* strings */
obx_str rt_str_new(int64_t len);
obx_str rt_str_of(const char* p, int64_t len);
obx_str rt_str_dup(obx_str s);
void rt_str_drop(obx_str s);
bool rt_str_eq(obx_str a, obx_str b);
int rt_str_cmp(obx_str a, obx_str b);
obx_str rt_str_concat(obx_str a, obx_str b);
uint8_t rt_str_byte(obx_str s, int64_t i, const char* w);
obx_str rt_str_sub(obx_str s, int64_t a, int64_t b, const char* w);
obx_str rt_str_from_bytes(obx_slice b);
obx_str rt_str_from_c(void* p);
const char* rt_cstr(obx_str s, const char* w);
int64_t rt_cmark(void);
void* rt_cdata(void* p);
void rt_cfree(int64_t m);
obx_str rt_u128_to_str(u128 v);
obx_str rt_i128_to_str(i128 v);
obx_str rt_int_to_str(int64_t v);
obx_str rt_u64_to_str(uint64_t v);
obx_str rt_float_to_str(double d);
bool rt_parse_float(obx_str s, double* out);
int64_t rt_write_fd(int64_t fd, obx_str s);
int64_t rt_read_file(obx_str path, obx_str* out);
bool rt_getenv(obx_str name, obx_str* out);

/* arrays */
int64_t rt_idx(int64_t i, int64_t len, const char* w);
obx_arr rt_arr_new(int64_t n, size_t size);
obx_arr rt_arr_fill(int64_t n, size_t size, const char* w);
void rt_arr_free(obx_arr a);
void* rt_arr_push(obx_arr* a, size_t size);
void* rt_arr_pop(obx_arr* a, size_t size, const char* w);
void rt_swap(obx_slice a, int64_t i, int64_t j, size_t size, const char* w);
obx_slice rt_as_slice(obx_arr* a);
obx_slice rt_sub(obx_slice s, int64_t lo, int64_t hi, size_t size, const char* w);
obx_arr rt_str_to_bytes(obx_str s);
obx_arr rt_args(void);

/* checked integer arithmetic (int is int64_t) */
int64_t rt_add_int(int64_t a, int64_t b, const char* w);
int64_t rt_sub_int(int64_t a, int64_t b, const char* w);
int64_t rt_mul_int(int64_t a, int64_t b, const char* w);
int64_t rt_wadd_int(int64_t a, int64_t b);
int64_t rt_wsub_int(int64_t a, int64_t b);
int64_t rt_wmul_int(int64_t a, int64_t b);
int64_t rt_div_int(int64_t a, int64_t b, const char* w);
int64_t rt_rem_int(int64_t a, int64_t b, const char* w);
int64_t rt_neg_int(int64_t a, const char* w);
int64_t rt_shl_int(int64_t a, int64_t n, const char* w);
int64_t rt_shr_int(int64_t a, int64_t n, const char* w);
int32_t rt_add_i32(int32_t a, int32_t b, const char* w);
int32_t rt_sub_i32(int32_t a, int32_t b, const char* w);
int32_t rt_mul_i32(int32_t a, int32_t b, const char* w);
int32_t rt_wadd_i32(int32_t a, int32_t b);
int32_t rt_wsub_i32(int32_t a, int32_t b);
int32_t rt_wmul_i32(int32_t a, int32_t b);
int32_t rt_div_i32(int32_t a, int32_t b, const char* w);
int32_t rt_rem_i32(int32_t a, int32_t b, const char* w);
int32_t rt_neg_i32(int32_t a, const char* w);
int32_t rt_shl_i32(int32_t a, int64_t n, const char* w);
int32_t rt_shr_i32(int32_t a, int64_t n, const char* w);
uint32_t rt_add_u32(uint32_t a, uint32_t b, const char* w);
uint32_t rt_sub_u32(uint32_t a, uint32_t b, const char* w);
uint32_t rt_mul_u32(uint32_t a, uint32_t b, const char* w);
uint32_t rt_wadd_u32(uint32_t a, uint32_t b);
uint32_t rt_wsub_u32(uint32_t a, uint32_t b);
uint32_t rt_wmul_u32(uint32_t a, uint32_t b);
uint32_t rt_div_u32(uint32_t a, uint32_t b, const char* w);
uint32_t rt_rem_u32(uint32_t a, uint32_t b, const char* w);
uint32_t rt_neg_u32(uint32_t a, const char* w);
uint32_t rt_shl_u32(uint32_t a, int64_t n, const char* w);
uint32_t rt_shr_u32(uint32_t a, int64_t n, const char* w);
uint64_t rt_add_u64(uint64_t a, uint64_t b, const char* w);
uint64_t rt_sub_u64(uint64_t a, uint64_t b, const char* w);
uint64_t rt_mul_u64(uint64_t a, uint64_t b, const char* w);
uint64_t rt_wadd_u64(uint64_t a, uint64_t b);
uint64_t rt_wsub_u64(uint64_t a, uint64_t b);
uint64_t rt_wmul_u64(uint64_t a, uint64_t b);
uint64_t rt_div_u64(uint64_t a, uint64_t b, const char* w);
uint64_t rt_rem_u64(uint64_t a, uint64_t b, const char* w);
uint64_t rt_neg_u64(uint64_t a, const char* w);
uint64_t rt_shl_u64(uint64_t a, int64_t n, const char* w);
uint64_t rt_shr_u64(uint64_t a, int64_t n, const char* w);
uint8_t rt_add_byte(uint8_t a, uint8_t b, const char* w);
uint8_t rt_sub_byte(uint8_t a, uint8_t b, const char* w);
uint8_t rt_mul_byte(uint8_t a, uint8_t b, const char* w);
uint8_t rt_wadd_byte(uint8_t a, uint8_t b);
uint8_t rt_wsub_byte(uint8_t a, uint8_t b);
uint8_t rt_wmul_byte(uint8_t a, uint8_t b);
uint8_t rt_div_byte(uint8_t a, uint8_t b, const char* w);
uint8_t rt_rem_byte(uint8_t a, uint8_t b, const char* w);
uint8_t rt_neg_byte(uint8_t a, const char* w);
uint8_t rt_shl_byte(uint8_t a, int64_t n, const char* w);
uint8_t rt_shr_byte(uint8_t a, int64_t n, const char* w);
i128 rt_add_i128(i128 a, i128 b, const char* w);
i128 rt_sub_i128(i128 a, i128 b, const char* w);
i128 rt_mul_i128(i128 a, i128 b, const char* w);
i128 rt_wadd_i128(i128 a, i128 b);
i128 rt_wsub_i128(i128 a, i128 b);
i128 rt_wmul_i128(i128 a, i128 b);
i128 rt_div_i128(i128 a, i128 b, const char* w);
i128 rt_rem_i128(i128 a, i128 b, const char* w);
i128 rt_neg_i128(i128 a, const char* w);
i128 rt_shl_i128(i128 a, int64_t n, const char* w);
i128 rt_shr_i128(i128 a, int64_t n, const char* w);
u128 rt_add_u128(u128 a, u128 b, const char* w);
u128 rt_sub_u128(u128 a, u128 b, const char* w);
u128 rt_mul_u128(u128 a, u128 b, const char* w);
u128 rt_wadd_u128(u128 a, u128 b);
u128 rt_wsub_u128(u128 a, u128 b);
u128 rt_wmul_u128(u128 a, u128 b);
u128 rt_div_u128(u128 a, u128 b, const char* w);
u128 rt_rem_u128(u128 a, u128 b, const char* w);
u128 rt_neg_u128(u128 a, const char* w);
u128 rt_shl_u128(u128 a, int64_t n, const char* w);
u128 rt_shr_u128(u128 a, int64_t n, const char* w);

/* checked conversions: from a signed value (as i128), an unsigned u128, or a double */
int64_t rt_cvs_int(i128 v, const char* w);
int64_t rt_cvu_int(u128 v, const char* w);
int64_t rt_cvf_int(double d, const char* w);
int32_t rt_cvs_i32(i128 v, const char* w);
int32_t rt_cvu_i32(u128 v, const char* w);
int32_t rt_cvf_i32(double d, const char* w);
uint32_t rt_cvs_u32(i128 v, const char* w);
uint32_t rt_cvu_u32(u128 v, const char* w);
uint32_t rt_cvf_u32(double d, const char* w);
uint64_t rt_cvs_u64(i128 v, const char* w);
uint64_t rt_cvu_u64(u128 v, const char* w);
uint64_t rt_cvf_u64(double d, const char* w);
uint8_t rt_cvs_byte(i128 v, const char* w);
uint8_t rt_cvu_byte(u128 v, const char* w);
uint8_t rt_cvf_byte(double d, const char* w);
i128 rt_cvs_i128(i128 v, const char* w);
i128 rt_cvu_i128(u128 v, const char* w);
i128 rt_cvf_i128(double d, const char* w);
u128 rt_cvs_u128(i128 v, const char* w);
u128 rt_cvu_u128(u128 v, const char* w);
u128 rt_cvf_u128(double d, const char* w);

/* helpers the native code generator calls for what it does not inline */
u128 rt_n_udiv128(u128 a, u128 b);
u128 rt_n_umod128(u128 a, u128 b);
i128 rt_n_sdiv128(i128 a, i128 b);
i128 rt_n_smod128(i128 a, i128 b);
u128 rt_n_mul128(u128 a, u128 b);
double rt_n_u64_to_double(uint64_t v);
double rt_n_i128_to_double(i128 v);
double rt_n_u128_to_double(u128 v);
i128 rt_n_double_to_i128(double d);
u128 rt_n_double_to_u128(double d);
uint64_t rt_n_double_to_u64(double d);
void rt_n_start(int64_t* sp, void* mainf);
