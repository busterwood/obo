/* Scalar-only zlib adapter API. No zlib pointer crosses into obo. */
#ifndef OBO_CINTEROP_ZLIB_ADAPTER_H
#define OBO_CINTEROP_ZLIB_ADAPTER_H

long zlib_version_buf(void);
long zlib_compress(long src_buf, long level);
long zlib_uncompress(long src_buf, long out_len);

#endif
