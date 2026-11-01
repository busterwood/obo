/* Experimental HTTP/2 C shim (H2a).
 *
 * Scalar-only boundary: every exported symbol takes `long` arguments and
 * returns `long`. nghttp2's `nghttp2_session`, `nghttp2_frame`, `nghttp2_nv`,
 * `nghttp2_data_provider` and every callback pointer stay inside the C unit.
 * Obo receives registry tokens (via sysregistry.c), bounded byte-buffer
 * tokens, copied header/body bytes and fixed scalar events.
 *
 * The shim owns framing, HPACK, flow-control bookkeeping and stream state via
 * the system nghttp2. It does not parse HTTP, decide policy, read a clock or
 * call into obo. This is trusted experimental support code, not a stable ABI.
 */
#ifndef OBO_H2_ADAPTER_H
#define OBO_H2_ADAPTER_H

#include <stddef.h>

/* Resource kinds. Must match sysplatform::KIND_H2_CONNECTION /
 * KIND_H2_STREAM. 23/24 are the TLS kinds; 25/26 are the next free values. */
#define H2_KIND_CONNECTION 25L
#define H2_KIND_STREAM 26L

/* Failure code encoding: (kind << 24) | payload. A negative return is
 * -(code). Positive returns are progress/status/event values and are never
 * failure codes. */
#define H2_FAIL_INVALID_ARGUMENT 1L
#define H2_FAIL_RANGE 2L
#define H2_FAIL_INVALID_RESOURCE 3L
#define H2_FAIL_CLOSED 4L
#define H2_FAIL_NOT_READY 5L
#define H2_FAIL_INVALID_STATE 6L
#define H2_FAIL_BAD_PREFACE 7L
#define H2_FAIL_BAD_FRAME 8L
#define H2_FAIL_FRAME_SIZE 9L
#define H2_FAIL_PROTOCOL 10L
#define H2_FAIL_COMPRESSION 11L
#define H2_FAIL_HEADER_LIST 12L
#define H2_FAIL_CONTINUATION 13L
#define H2_FAIL_FLOOD 14L
#define H2_FAIL_REFUSED 15L
#define H2_FAIL_STREAM_CLOSED 16L
#define H2_FAIL_RESET 17L
#define H2_FAIL_GOAWAY 18L
#define H2_FAIL_FLOW_CONTROL 19L
#define H2_FAIL_SETTINGS_TIMEOUT 20L
#define H2_FAIL_TIMEOUT 21L
#define H2_FAIL_RESOURCE_LIMIT 22L
#define H2_FAIL_BUSY 23L
#define H2_FAIL_UNSUPPORTED 24L
#define H2_FAIL_INTERNAL 25L

#define H2_FAIL(kind, payload) \
    ((((long)(kind)) << 24) | ((long)(payload) & 0xffffffL))

/* Event types (fixed event ring records; payload is pulled separately). */
#define H2_EV_NONE 0L
#define H2_EV_STREAM_STARTED 1L
#define H2_EV_STREAM_DATA 2L
#define H2_EV_STREAM_END 3L
#define H2_EV_STREAM_CLOSED 4L
#define H2_EV_TRAILERS 5L
#define H2_EV_SETTINGS_ACKED 6L
#define H2_EV_GOAWAY 7L
#define H2_EV_TIMEOUT 8L
#define H2_EV_PROTOCOL_ERROR 9L
#define H2_EV_DRAIN_COMPLETE 10L
#define H2_EV_CONNECTION_CLOSED 11L

/* Event flags. */
#define H2_FLAG_END_STREAM 1L
#define H2_FLAG_TRAILERS 2L
#define H2_FLAG_LOCAL 4L

/* Connection states. */
#define H2_STATE_AWAIT_PREFACE 0L
#define H2_STATE_AWAIT_SETTINGS 1L
#define H2_STATE_OPEN 2L
#define H2_STATE_DRAINING 3L
#define H2_STATE_CLOSING 4L
#define H2_STATE_CLOSED 5L
#define H2_STATE_FAILED 6L

/* RFC 9113 stream states as observed on the server side. */
#define H2_STREAM_OPEN 1L
#define H2_STREAM_HALF_CLOSED_REMOTE 2L
#define H2_STREAM_HALF_CLOSED_LOCAL 3L
#define H2_STREAM_CLOSED 4L

/* Operational modes. */
#define H2_MODE_TLS 0L
#define H2_MODE_PRIOR_KNOWLEDGE 1L

/* Option packet: fixed signed 64-bit fields, little-endian host order. */
#define H2_OPTION_FIELDS 44L
#define H2_OPTION_BYTES (H2_OPTION_FIELDS * 8L)
#define H2_OPTION_MAGIC 0x48324131L /* "H2A1" */

#define H2_OPT_MAGIC 0L
#define H2_OPT_MODE 1L
#define H2_OPT_MAX_CONCURRENT 2L
#define H2_OPT_MAX_STREAMS 3L
#define H2_OPT_MAX_HEADER_LIST 4L
#define H2_OPT_MAX_HEADER_COUNT 5L
#define H2_OPT_MAX_HEADER_FIELD 6L
#define H2_OPT_MAX_HEADER_BLOCK 7L
#define H2_OPT_MAX_CONTINUATIONS 8L
#define H2_OPT_MAX_HEADER_ARENA 9L
#define H2_OPT_MAX_STREAM_RECV_WINDOW 10L
#define H2_OPT_MAX_STREAM_RECV_BUFFER 11L
#define H2_OPT_MAX_CONNECTION_WINDOW 12L
#define H2_OPT_MAX_STREAM_SEND_BUFFER 13L
#define H2_OPT_MAX_WIRE 14L
#define H2_OPT_MAX_EVENTS 15L
#define H2_OPT_MAX_BODY 16L
#define H2_OPT_MAX_TRAILER_BYTES 17L
#define H2_OPT_MAX_TRAILER_COUNT 18L
#define H2_OPT_MAX_RESPONSE_HEADER 19L
#define H2_OPT_MAX_RESPONSE_TRAILER 20L
#define H2_OPT_MAX_RESPONSE_TRAILER_COUNT 21L
#define H2_OPT_MAX_READ_CHUNK 22L
#define H2_OPT_MAX_RESET_BURST 23L
#define H2_OPT_MAX_RESET_RATE 24L
#define H2_OPT_MAX_PING_ACKS 25L
#define H2_OPT_MAX_SETTINGS_ENTRIES 26L
#define H2_OPT_FRAME_BURST 27L
#define H2_OPT_FRAME_RATE 28L
#define H2_OPT_ZERO_DATA_BURST 29L
#define H2_OPT_ZERO_DATA_RATE 30L
#define H2_OPT_PREFACE_TIMEOUT_MS 31L
#define H2_OPT_SETTINGS_TIMEOUT_MS 32L
#define H2_OPT_HEADER_TIMEOUT_MS 33L
#define H2_OPT_BODY_IDLE_TIMEOUT_MS 34L
#define H2_OPT_RESPONSE_TIMEOUT_MS 35L
#define H2_OPT_RESPONSE_TOTAL_TIMEOUT_MS 36L
#define H2_OPT_STREAM_TOTAL_TIMEOUT_MS 37L
#define H2_OPT_IDLE_TIMEOUT_MS 38L
#define H2_OPT_DRAIN_BARRIER_MS 39L
#define H2_OPT_DRAIN_TIMEOUT_MS 40L
#define H2_OPT_RESERVED 41L
#define H2_OPT_SERVER_NAME_LEN 42L
#define H2_OPT_FLAGS 43L

/* Stream info packet: fixed signed 64-bit fields. */
#define H2_STREAM_INFO_FIELDS 18L
#define H2_STREAM_INFO_BYTES (H2_STREAM_INFO_FIELDS * 8L)
#define H2_SI_ID 0L
#define H2_SI_STATE 1L
#define H2_SI_RECV_BUFFERED 2L
#define H2_SI_SEND_BUFFERED 3L
#define H2_SI_RECV_WINDOW 4L
#define H2_SI_CONN_WINDOW 5L
#define H2_SI_HEADER_COUNT 6L
#define H2_SI_TRAILER_COUNT 7L
#define H2_SI_BODY_IN 8L
#define H2_SI_BODY_OUT 9L
#define H2_SI_FLAGS 10L
#define H2_SI_DECLARED_LENGTH 11L
#define H2_SI_RESPONSE_STATE 12L
#define H2_SI_STATUS 13L
#define H2_SI_TOTAL_DEADLINE 14L
#define H2_SI_RESPONSE_DEADLINE 15L
#define H2_SI_LOCAL_END 16L
#define H2_SI_PEER_END 17L

/* Connection stats packet: fixed signed 64-bit fields. */
#define H2_STATS_FIELDS 22L
#define H2_STATS_BYTES (H2_STATS_FIELDS * 8L)
#define H2_ST_LIVE_STREAMS 0L
#define H2_ST_CLOSING_STREAMS 1L
#define H2_ST_MAX_CONCURRENT 2L
#define H2_ST_STREAMS_OPENED 3L
#define H2_ST_STREAMS_CLOSED 4L
#define H2_ST_RESETS_SENT 5L
#define H2_ST_RESETS_RECEIVED 6L
#define H2_ST_GOAWAYS_SENT 7L
#define H2_ST_GOAWAYS_RECEIVED 8L
#define H2_ST_FRAMES_IN 9L
#define H2_ST_ZERO_DATA_FRAMES 10L
#define H2_ST_HEADER_BYTES 11L
#define H2_ST_BODY_BYTES_IN 12L
#define H2_ST_BODY_BYTES_OUT 13L
#define H2_ST_WIRE_BYTES 14L
#define H2_ST_WINDOW_RESTORED 15L
#define H2_ST_RECV_WINDOW 16L
#define H2_ST_CONN_WINDOW 17L
#define H2_ST_EVENT_HIGH_WATER 18L
#define H2_ST_WIRE_HIGH_WATER 19L
#define H2_ST_STATE 20L
#define H2_ST_FAILED_CODE 21L

long h2_init(void);
long h2_version_num(void);

long h2_conn_new(long option_token, long option_len);
long h2_conn_feed(long conn_token, long in_token, long offset, long count,
                  long now_ns);
long h2_conn_send(long conn_token, long max);
long h2_conn_wire_pending(long conn_token);
long h2_conn_wire_read(long conn_token, long out_token, long max);
long h2_conn_next_event(long conn_token, long now_ns);
long h2_conn_event_stream_id(long conn_token);
long h2_conn_event_flags(long conn_token);
long h2_conn_event_error(long conn_token);
long h2_conn_event_aux(long conn_token);
long h2_conn_deadline(long conn_token);
long h2_conn_state(long conn_token);
long h2_conn_settings_sent(long conn_token);
long h2_conn_settings_acked(long conn_token);
long h2_conn_drain(long conn_token);
long h2_conn_drain_poll(long conn_token, long now_ns);
long h2_conn_drain_complete(long conn_token);
long h2_conn_arm_preface(long conn_token, long now_ns);
long h2_conn_refuse(long conn_token, long stream_id, long error_code);
long h2_conn_goaway(long conn_token, long last_stream_id, long error_code);
long h2_conn_terminate(long conn_token, long last_stream_id, long error_code);
long h2_conn_consume(long conn_token, long bytes);
long h2_conn_stats(long conn_token, long out_token);
long h2_conn_reset_counts(long conn_token);

long h2_stream_adopt(long conn_token, long stream_id);
long h2_stream_mark_adopted(long stream_token);
long h2_stream_abandon(long stream_token);
long h2_stream_close(long stream_token);
long h2_stream_reset(long stream_token, long error_code);
long h2_stream_state(long stream_token);
long h2_stream_windows(long stream_token, long out_token);
long h2_stream_info(long stream_token, long out_token);

long h2_stream_begin(long stream_token, long status);
long h2_stream_set_length(long stream_token, long length);
long h2_stream_header(long stream_token, long packet_token, long name_off,
                      long name_len, long value_off, long value_len);
long h2_stream_commit(long stream_token, long end_stream);
long h2_stream_write(long stream_token, long in_token, long offset, long count);
long h2_stream_end(long stream_token);
long h2_stream_trailer(long stream_token, long packet_token, long name_off,
                       long name_len, long value_off, long value_len);

long h2_stream_read(long stream_token, long out_token, long max);
long h2_stream_discard(long stream_token, long max, long now_ns);
long h2_stream_header_count(long stream_token);
long h2_stream_header_at(long stream_token, long index, long out_token,
                         long which);
long h2_stream_trailer_count(long stream_token);
long h2_stream_trailer_at(long stream_token, long index, long out_token,
                          long which);
long h2_stream_clear_request(long stream_token);

long h2_text(long code, long out_token, long max);
long h2_counts(long out_token);

#endif
