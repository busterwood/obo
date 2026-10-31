/* Deterministic C-side zlib adapter test (real libz, in memory). */
#include "../toolkit/cres.h"
#include "../examples/zlib_adapter.h"

#include <stdio.h>
#include <string.h>

static int failures = 0;
#define CHECK(cond, msg) do { \
    if (!(cond)) { fprintf(stderr, "FAIL: %s\n", msg); failures++; } \
} while (0)

int main(void) {
    const unsigned char data[] = {0, 255, 65, 0, 1, 2};
    long in = cres_buf_from_bytes(data, sizeof(data));
    long comp, out, small, bad, ver;
    unsigned char *p = NULL;
    size_t n = 0;

    CHECK(in > 0, "input buffer");
    comp = zlib_compress(in, 6);
    CHECK(comp > 0, "compress");
    CHECK(cres_buf_free(in) == 1, "input close");
    CHECK(cres_buf_len(comp) > 0, "compressed length");
    CHECK(cres_buf_data(comp, &p, &n) && n > 0, "compressed data");

    out = zlib_uncompress(comp, (long)sizeof(data));
    CHECK(out > 0, "uncompress");
    CHECK(cres_buf_len(out) == (long)sizeof(data), "roundtrip length");
    CHECK(cres_buf_get(out, 0) == 0 && cres_buf_get(out, 1) == 255 &&
          cres_buf_get(out, 2) == 65 && cres_buf_get(out, 3) == 0,
          "roundtrip binary bytes");

    small = zlib_uncompress(comp, (long)sizeof(data) - 1);
    CHECK(small < 0, "too-small output rejected");
    CHECK(cres_buf_free(comp) == 1, "compressed close");
    CHECK(cres_buf_free(comp) == 0, "compressed double close false");
    bad = zlib_uncompress(comp, 1);
    CHECK(bad < 0, "stale token rejected");
    CHECK(cres_buf_free(out) == 1, "output close");
    CHECK(cres_buf_free(out) == 0, "output double close false");

    ver = zlib_version_buf();
    CHECK(ver > 0, "version buffer");
    CHECK(cres_buf_len(ver) >= 3, "version length");
    CHECK(cres_buf_free(ver) == 1, "version close");

    CHECK(zlib_compress(cres_buf_new(-1), 6) < 0, "invalid input rejected");
    CHECK(cres_live_count() == 0, "registry clean");
    if (failures != 0) {
        fprintf(stderr, "zlib adapter tests: %d failure(s)\n", failures);
        return 1;
    }
    printf("zlib adapter tests ok\n");
    return 0;
}
