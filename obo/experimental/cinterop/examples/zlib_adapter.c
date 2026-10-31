/* In-memory zlib adapter. The real zlib header supplies the C prototypes; the
 * adapter exports only scalar long values and keeps all zlib pointers in C. */
#include "../toolkit/cres.h"
#include "zlib_adapter.h"

#include <stdlib.h>
#include <string.h>
#include <zlib.h>

#define ZLIB_MAX_BYTES (16L * 1024L * 1024L)

long zlib_version_buf(void) {
    const char *v = zlibVersion();
    if (v == NULL) return -1;
    return cres_buf_from_bytes(v, strlen(v));
}

long zlib_compress(long src_buf, long level) {
    unsigned char *src = NULL;
    size_t src_len = 0;
    uLong bound;
    unsigned char *tmp;
    uLongf out_len;
    int rc;
    long token;

    if (!cres_buf_data(src_buf, &src, &src_len)) return -1;
    if (src_len > (size_t)ZLIB_MAX_BYTES || level < -1 || level > 9) return -2;
    bound = compressBound((uLong)src_len);
    if (bound > (uLong)ZLIB_MAX_BYTES) return -2;
    tmp = (unsigned char *)malloc((size_t)(bound ? bound : 1));
    if (tmp == NULL) return -3;
    out_len = bound;
    rc = compress2(tmp, &out_len, src, (uLong)src_len, (int)level);
    if (rc != Z_OK) {
        free(tmp);
        return -10 - (long)rc;
    }
    token = cres_buf_from_bytes(tmp, (size_t)out_len);
    free(tmp);
    return token;
}

long zlib_uncompress(long src_buf, long out_len) {
    unsigned char *src = NULL;
    size_t src_len = 0;
    unsigned char *tmp;
    uLongf actual;
    int rc;
    long token;

    if (!cres_buf_data(src_buf, &src, &src_len)) return -1;
    if (out_len < 0 || out_len > ZLIB_MAX_BYTES || src_len > (size_t)ZLIB_MAX_BYTES) {
        return -2;
    }
    tmp = (unsigned char *)malloc((size_t)(out_len ? out_len : 1));
    if (tmp == NULL) return -3;
    actual = (uLongf)out_len;
    rc = uncompress(tmp, &actual, src, (uLong)src_len);
    if (rc != Z_OK) {
        free(tmp);
        return -10 - (long)rc;
    }
    if (actual > (uLongf)ZLIB_MAX_BYTES) {
        free(tmp);
        return -2;
    }
    token = cres_buf_from_bytes(tmp, (size_t)actual);
    free(tmp);
    return token;
}
