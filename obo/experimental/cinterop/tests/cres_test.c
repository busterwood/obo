/* Deterministic C-side tests for the resource registry.
 *
 * Stage (g) removed the last libpq consumer of the old token adapter, so this
 * file keeps only the `cres` registry checks that the zlib/buffer examples and
 * the skill templates still use. It links cres.c alone. */
#include "../toolkit/cres.h"

#include <stdio.h>

static int failures = 0;

#define CHECK(cond, msg) do { \
    if (!(cond)) { fprintf(stderr, "FAIL: %s\n", msg); failures++; } \
} while (0)

static void test_registry(void) {
    long before = cres_live_count();
    long t = cres_buf_new(4);
    long t2;
    CHECK(t > 0, "buffer allocation");
    CHECK(cres_buf_len(t) == 4, "buffer length");
    CHECK(cres_buf_set(t, 0, 65) == 1, "buffer set");
    CHECK(cres_buf_get(t, 0) == 65, "buffer get");
    CHECK(cres_buf_set(t, 4, 1) == 0, "buffer set bound");
    CHECK(cres_buf_get(t, 4) == -1, "buffer get bound");
    CHECK(cres_buf_set(t, 0, 256) == 0, "buffer byte bound");
    t2 = cres_buf_new(4);
    CHECK(t2 > t, "token generation/index changes");
    CHECK(cres_buf_free(t) == 1, "buffer close");
    CHECK(cres_buf_free(t) == 0, "double close");
    CHECK(cres_buf_get(t, 0) == -1, "stale token rejected");
    CHECK(cres_buf_set(t2, 0, 66) == 1, "live token remains valid");
    CHECK(cres_buf_free(t2) == 1, "second buffer close");
    CHECK(cres_buf_new(CRES_MAX_BUFFER + 1) == 0, "oversized buffer rejected");
    CHECK(cres_buf_new(-1) == 0, "negative buffer rejected");
    CHECK(cres_live_count() == before, "registry clean after buffer tests");
}

int main(void) {
    test_registry();
    if (failures != 0) {
        fprintf(stderr, "cres tests: %d failure(s)\n", failures);
        return 1;
    }
    printf("cres tests ok\n");
    return 0;
}
