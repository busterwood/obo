/* UTF-8 <-> UTF-16 conversion for the ODBC W entry points.
 *
 * Phase 1 measured that the driver converts narrow SQL text, column names and
 * diagnostic messages through the process locale, while narrow result and
 * parameter data is honoured as UTF-8. The binding therefore converts the
 * locale-sensitive surfaces itself and passes UTF-16 to the W functions.
 *
 * The conversion is strict: overlong forms, lone surrogates, values above
 * U+10FFFF and truncated sequences are rejected rather than replaced. The
 * caller turns a rejection into a clear error before any C call.
 */
#include <stdlib.h>
#include <string.h>
#include "ms_internal.h"

/* Decode one UTF-8 scalar. Returns the number of bytes consumed (1..4), or 0 on
 * a malformed sequence. */
static int utf8_next(const unsigned char *p, int avail, unsigned *cp) {
    if (avail <= 0) return 0;
    unsigned c = p[0];
    if (c < 0x80) {
        *cp = c;
        return 1;
    }
    if (c >= 0xc2 && c <= 0xdf) {
        if (avail < 2) return 0;
        if ((p[1] & 0xc0) != 0x80) return 0;
        *cp = ((c & 0x1f) << 6) | (p[1] & 0x3f);
        return 2;
    }
    if (c >= 0xe0 && c <= 0xef) {
        if (avail < 3) return 0;
        if ((p[1] & 0xc0) != 0x80 || (p[2] & 0xc0) != 0x80) return 0;
        if (c == 0xe0 && p[1] < 0xa0) return 0;      /* overlong */
        if (c == 0xed && p[1] >= 0xa0) return 0;     /* surrogate */
        *cp = ((c & 0x0f) << 12) | ((p[1] & 0x3f) << 6) | (p[2] & 0x3f);
        return 3;
    }
    if (c >= 0xf0 && c <= 0xf4) {
        if (avail < 4) return 0;
        if ((p[1] & 0xc0) != 0x80 || (p[2] & 0xc0) != 0x80 ||
            (p[3] & 0xc0) != 0x80) return 0;
        if (c == 0xf0 && p[1] < 0x90) return 0;      /* overlong */
        if (c == 0xf4 && p[1] >= 0x90) return 0;     /* above U+10FFFF */
        *cp = ((c & 0x07) << 18) | ((p[1] & 0x3f) << 12) |
              ((p[2] & 0x3f) << 6) | (p[3] & 0x3f);
        return 4;
    }
    return 0;
}

int ms_utf8_to_utf16(const void *src, int n, SQLWCHAR **out, int *units) {
    const unsigned char *p = (const unsigned char *)src;
    int i = 0;
    int cap;
    SQLWCHAR *w;
    int j = 0;

    if (!out || !units || n < 0 || (n > 0 && !src)) return -1;
    *out = NULL;
    *units = 0;
    /* One UTF-16 unit per input byte, plus room for surrogate pairs and NUL. */
    if ((size_t)n > (((size_t)-1) / sizeof(SQLWCHAR)) - 2) return -2;
    cap = n + 2;
    w = (SQLWCHAR *)malloc((size_t)cap * sizeof(SQLWCHAR));
    if (!w) return -2;
    while (i < n) {
        unsigned cp;
        int used = utf8_next(p + i, n - i, &cp);
        if (used == 0) {
            free(w);
            return -1;
        }
        i += used;
        if (cp >= 0x10000) {
            unsigned v = cp - 0x10000;
            if (j + 2 >= cap) {
                /* A 4-byte sequence consumes 4 input bytes but 2 units, so this
                 * cannot normally happen; keep the bound safe regardless. */
                SQLWCHAR *bigger = (SQLWCHAR *)realloc(w,
                                    (size_t)(cap + 4) * sizeof(SQLWCHAR));
                if (!bigger) { free(w); return -2; }
                w = bigger;
                cap += 4;
            }
            w[j++] = (SQLWCHAR)(0xd800 + (v >> 10));
            w[j++] = (SQLWCHAR)(0xdc00 + (v & 0x3ff));
        } else {
            w[j++] = (SQLWCHAR)cp;
        }
    }
    w[j] = 0;
    *out = w;
    *units = j;
    return 0;
}

int ms_utf16_to_utf8(const SQLWCHAR *src, int units, char **out, int *bytes) {
    char *b;
    int i = 0;
    int j = 0;
    size_t cap;

    if (!out || !bytes || units < 0 || (units > 0 && !src)) return -1;
    *out = NULL;
    *bytes = 0;
    if ((size_t)units > (((size_t)-1) / 4) - 2) return -2;
    cap = (size_t)units * 4 + 2;
    b = (char *)malloc(cap);
    if (!b) return -2;
    while (i < units) {
        unsigned cp = src[i++];
        if (cp >= 0xd800 && cp <= 0xdbff) {
            unsigned lo;
            if (i >= units) { free(b); return -1; }
            lo = src[i];
            if (lo < 0xdc00 || lo > 0xdfff) { free(b); return -1; }
            i++;
            cp = 0x10000 + ((cp - 0xd800) << 10) + (lo - 0xdc00);
        } else if (cp >= 0xdc00 && cp <= 0xdfff) {
            free(b);
            return -1;
        }
        if (cp < 0x80) {
            b[j++] = (char)cp;
        } else if (cp < 0x800) {
            b[j++] = (char)(0xc0 | (cp >> 6));
            b[j++] = (char)(0x80 | (cp & 0x3f));
        } else if (cp < 0x10000) {
            b[j++] = (char)(0xe0 | (cp >> 12));
            b[j++] = (char)(0x80 | ((cp >> 6) & 0x3f));
            b[j++] = (char)(0x80 | (cp & 0x3f));
        } else {
            b[j++] = (char)(0xf0 | (cp >> 18));
            b[j++] = (char)(0x80 | ((cp >> 12) & 0x3f));
            b[j++] = (char)(0x80 | ((cp >> 6) & 0x3f));
            b[j++] = (char)(0x80 | (cp & 0x3f));
        }
    }
    b[j] = 0;
    *out = b;
    *bytes = j;
    return 0;
}
