/* Experimental HTTP/2 C shim (H2a).
 *
 * A thin, trusted adapter around the system nghttp2 server session. nghttp2
 * owns frame grammar, HPACK, flow-control bookkeeping and stream state. This
 * file owns the bounded wire ring, the fixed event ring, per-stream header
 * arenas and body rings, the deferred data provider, registry lifetime and
 * scalar diagnostics. It never calls into obo and never exposes an nghttp2
 * pointer, C struct or function pointer across the cbind.
 */
#ifndef _GNU_SOURCE
#define _GNU_SOURCE
#endif
#include "h2_adapter.h"

#include <nghttp2/nghttp2.h>

#include <errno.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/types.h>

#include "../../sys/native/sysregistry.h"

#if NGHTTP2_VERSION_NUM < 0x013b00
#error "nghttp2 >= 1.59.0 (0x013b00) with max_continuations is required"
#endif

/* Link-time provenance probes: these fail the build if the Ubuntu security
 * update (or a later upstream release) lacks the required options. */
static void *const h2_probe_max_continuations
    __attribute__((unused)) = (void *)&nghttp2_option_set_max_continuations;
static void *const h2_probe_reset_rate
    __attribute__((unused)) = (void *)&nghttp2_option_set_stream_reset_rate_limit;

#define H2_MAX_CONCURRENT_HARD 256L
#define H2_MAX_WIRE_HARD (16L * 1024L * 1024L)
#define H2_MAX_EVENTS_HARD 65536L

#define H2_NV_NAME 0L
#define H2_NV_VALUE 1L

typedef struct {
    uint32_t name_off;
    uint32_t name_len;
    uint32_t value_off;
    uint32_t value_len;
} h2_nv_ref;

typedef struct {
    uint32_t type;
    int32_t stream_id;
    uint32_t flags;
    uint32_t error;
    int64_t aux;
} h2_event;

struct h2_conn;

typedef struct h2_stream {
    int32_t id;
    struct h2_conn *conn;
    long token;
    unsigned registered;
    unsigned adopted;
    unsigned closing;
    unsigned closed;
    unsigned rejected;
    unsigned rst_submitted;
    unsigned rst_sent;
    unsigned headers_done;
    unsigned in_trailers;
    unsigned header_active;
    unsigned peer_end;
    unsigned local_end;
    unsigned response_state; /* 0 none, 1 staged, 2 committed, 5 done, 6 reset */
    unsigned finished;
    unsigned provider_submitted;
    unsigned trailers_submitted;
    int status;
    int64_t declared_length;
    size_t body_bytes_in;
    size_t body_bytes_out;

    unsigned char *recv;
    size_t recv_cap;
    size_t recv_start;
    size_t recv_end;

    unsigned char *send;
    size_t send_cap;
    size_t send_start;
    size_t send_end;

    /* Four independent staging arenas keep request, trailer, response and
     * response-trailer lifetime separate. Their sum is the per-stream staging
     * factor validated by obo's maxHeaderArena formula. */
    unsigned char *hreq_arena;
    size_t hreq_cap;
    size_t hreq_used;
    h2_nv_ref *hreq;
    size_t hreq_count;
    size_t hreq_refcap;
    size_t hreq_list_bytes;
    unsigned request_cleared;

    unsigned char *htrail_arena;
    size_t htrail_cap;
    size_t htrail_used;
    h2_nv_ref *htrail;
    size_t htrail_count;
    size_t htrail_refcap;
    size_t htrail_list_bytes;

    unsigned char *rhead_arena;
    size_t rhead_cap;
    size_t rhead_used;
    h2_nv_ref *rhead;
    size_t rhead_count;
    size_t rhead_refcap;
    size_t rhead_list_bytes;

    unsigned char *rtrail_arena;
    size_t rtrail_cap;
    size_t rtrail_used;
    h2_nv_ref *rtrail;
    size_t rtrail_count;
    size_t rtrail_refcap;
    size_t rtrail_list_bytes;

    int64_t header_deadline;
    int64_t body_deadline;
    int64_t resp_deadline;
    int64_t resp_total_deadline;
    int64_t total_deadline;
} h2_stream;

typedef struct h2_conn {
    nghttp2_session *session;
    unsigned state;
    unsigned failed;
    unsigned closed;
    long failed_code;
    long mode;

    h2_stream **streams;
    size_t stream_cap;
    size_t stream_count;
    size_t streams_opened;
    size_t streams_closed;
    size_t closing_count;

    unsigned char *wire;
    size_t wire_cap;
    size_t wire_head;
    size_t wire_tail;
    size_t wire_count;
    size_t wire_high;
    size_t send_budget;

    h2_event *events;
    size_t event_cap;
    size_t event_head;
    size_t event_count;
    size_t event_high;
    h2_event current;
    int current_valid;

    size_t reserved;

    size_t max_concurrent;
    size_t max_streams;
    size_t max_header_list;
    size_t max_header_count;
    size_t max_header_field;
    size_t max_header_block;
    size_t max_continuations;
    size_t max_header_arena;
    size_t max_stream_recv_window;
    size_t max_stream_recv_buffer;
    size_t max_connection_window;
    size_t max_stream_send_buffer;
    size_t max_wire;
    size_t max_events;
    size_t max_body;
    size_t max_trailer_bytes;
    size_t max_trailer_count;
    size_t max_response_header;
    size_t max_response_trailer;
    size_t max_response_trailer_count;
    size_t max_read_chunk;
    long max_reset_burst;
    long max_reset_rate;
    long max_ping_acks;
    long max_settings_entries;
    long frame_burst;
    long frame_rate;
    long zero_data_burst;
    long zero_data_rate;
    long preface_timeout_ms;
    long settings_timeout_ms;
    long header_timeout_ms;
    long body_idle_timeout_ms;
    long response_timeout_ms;
    long response_total_timeout_ms;
    long stream_total_timeout_ms;
    long idle_timeout_ms;
    long drain_barrier_ms;
    long drain_timeout_ms;

    unsigned preface_ok;
    size_t preface_got;
    unsigned char preface[24];
    unsigned settings_sent;
    unsigned settings_acked;
    int64_t preface_deadline;
    int64_t settings_deadline;
    int64_t idle_deadline;
    int64_t drain_deadline;
    int64_t barrier_deadline;
    unsigned idle_active;
    unsigned settings_active;
    unsigned drain_state; /* 0 off, 1 notice queued, 2 notice sent, 3 barrier, 4 final */
    unsigned drain_final_sent;
    int32_t drain_cutoff;
    unsigned barrier_acked;
    uint8_t barrier_nonce[8];
    unsigned barrier_nonce_valid;

    int64_t now_ns;
    int64_t bucket_ns;
    int64_t frame_tokens;
    int64_t zero_tokens;
    unsigned block_active;
    size_t block_bytes;

    size_t resets_sent;
    size_t resets_received;
    size_t goaways_sent;
    size_t goaways_received;
    size_t frames_in;
    size_t zero_data_frames;
    size_t header_bytes;
    size_t body_bytes_in;
    size_t body_bytes_out;
    size_t wire_bytes;
    size_t window_restored;
    size_t protocol_errors;
    size_t timeouts;
    unsigned drain_complete_enqueued;
    unsigned error_delivered;
    unsigned failed_flush;
    int32_t refused_id;
} h2_conn;

static size_t g_live_connections;
static size_t g_live_streams;
static size_t g_streams_opened;
static size_t g_streams_closed;
static size_t g_resets_sent;
static size_t g_resets_received;
static size_t g_goaways_sent;
static size_t g_goaways_received;
static size_t g_frames_in;
static size_t g_zero_data_frames;
static size_t g_header_bytes;
static size_t g_body_in;
static size_t g_body_out;
static size_t g_wire_bytes;
static size_t g_window_restored;
static size_t g_protocol_errors;
static size_t g_timeouts;

static long h2_err(long kind, long payload) {
    return -H2_FAIL(kind, payload);
}

static void *h2_alloc(size_t n) {
    if (n == 0) n = 1;
    return malloc(n);
}

static h2_stream *find_stream(h2_conn *c, int32_t id) {
    size_t i;
    if (c == NULL || id <= 0) return NULL;
    for (i = 0; i < c->stream_count; i++) {
        if (c->streams[i] != NULL && c->streams[i]->id == id) return c->streams[i];
    }
    return NULL;
}

static h2_conn *peek_conn(long token) {
    return (h2_conn *)sys_reg_peek(token, (int)H2_KIND_CONNECTION);
}

static h2_stream *peek_stream(long token) {
    return (h2_stream *)sys_reg_peek(token, (int)H2_KIND_STREAM);
}

/* Accessors that read C-owned arenas must fail closed for an orphaned stream:
 * connection release frees the arenas before the shell is closed, and the
 * stream record stays alive so the shell can still retire its own token. */
static int stream_access_closed(h2_stream *s) {
    if (s == NULL) return 1;
    if (s->conn == NULL || s->closed) return 1;
    return 0;
}

static long buffer_view(long token, unsigned char **data, size_t *len) {
    if (token <= 0 || data == NULL || len == NULL) return h2_err(H2_FAIL_INVALID_ARGUMENT, 0);
    if (sys_reg_buffer_data(token, data, len) != 0) return h2_err(H2_FAIL_INVALID_RESOURCE, 0);
    return 0;
}

static long buffer_copy_out(long token, const void *src, size_t n, size_t max) {
    unsigned char *dst = NULL;
    size_t cap = 0;
    long r = buffer_view(token, &dst, &cap);
    if (r != 0) return r;
    if (n > max) n = max;
    if (n > cap) return h2_err(H2_FAIL_RANGE, 0);
    if (n > 0 && src != NULL) memcpy(dst, src, n);
    return (long)n;
}

static void write_i64(unsigned char *dst, size_t index, int64_t value) {
    memcpy(dst + index * 8, &value, sizeof(value));
}

static long packet_copy(long token, const int64_t *fields, size_t count) {
    unsigned char *dst = NULL;
    size_t cap = 0;
    size_t i;
    long r = buffer_view(token, &dst, &cap);
    if (r != 0) return r;
    if (cap < count * 8) return h2_err(H2_FAIL_RANGE, 0);
    for (i = 0; i < count; i++) write_i64(dst, i, fields[i]);
    return (long)(count * 8);
}

static long map_ngerror(int e) {
    switch (e) {
    case NGHTTP2_ERR_NOMEM: return H2_FAIL(H2_FAIL_RESOURCE_LIMIT, 0);
    case NGHTTP2_ERR_BAD_CLIENT_MAGIC: return H2_FAIL(H2_FAIL_BAD_PREFACE, 0);
    case NGHTTP2_ERR_FLOODED: return H2_FAIL(H2_FAIL_FLOOD, 0);
    case NGHTTP2_ERR_TOO_MANY_CONTINUATIONS: return H2_FAIL(H2_FAIL_CONTINUATION, 0);
    case NGHTTP2_ERR_HEADER_COMP: return H2_FAIL(H2_FAIL_COMPRESSION, 0);
    case NGHTTP2_ERR_FRAME_SIZE_ERROR: return H2_FAIL(H2_FAIL_FRAME_SIZE, 0);
    case NGHTTP2_ERR_PROTO: return H2_FAIL(H2_FAIL_PROTOCOL, 0);
    case NGHTTP2_ERR_INVALID_STREAM_ID:
    case NGHTTP2_ERR_STREAM_CLOSED:
    case NGHTTP2_ERR_STREAM_CLOSING: return H2_FAIL(H2_FAIL_STREAM_CLOSED, 0);
    case NGHTTP2_ERR_REFUSED_STREAM: return H2_FAIL(H2_FAIL_REFUSED, 0);
    case NGHTTP2_ERR_PUSH_DISABLED: return H2_FAIL(H2_FAIL_UNSUPPORTED, 0);
    case NGHTTP2_ERR_FLOW_CONTROL: return H2_FAIL(H2_FAIL_FLOW_CONTROL, 0);
    case NGHTTP2_ERR_TEMPORAL_CALLBACK_FAILURE: return H2_FAIL(H2_FAIL_RESET, 0);
    case NGHTTP2_ERR_CALLBACK_FAILURE: return H2_FAIL(H2_FAIL_INTERNAL, 0);
    case NGHTTP2_ERR_EOF: return H2_FAIL(H2_FAIL_CLOSED, 0);
    case NGHTTP2_ERR_GOAWAY_ALREADY_SENT: return H2_FAIL(H2_FAIL_GOAWAY, 0);
    case NGHTTP2_ERR_SESSION_CLOSING: return H2_FAIL(H2_FAIL_CLOSED, 0);
    case NGHTTP2_ERR_STREAM_ID_NOT_AVAILABLE: return H2_FAIL(H2_FAIL_REFUSED, 0);
    case NGHTTP2_ERR_INVALID_ARGUMENT: return H2_FAIL(H2_FAIL_INVALID_ARGUMENT, 0);
    case NGHTTP2_ERR_HTTP_HEADER:
    case NGHTTP2_ERR_HTTP_MESSAGING: return H2_FAIL(H2_FAIL_PROTOCOL, 0);
    default: return H2_FAIL(H2_FAIL_INTERNAL, 0);
    }
}

static long fail_code_kind(long code) {
    return (code >> 24) & 0xffffffL;
}

static uint32_t rfc_code_for_fail(long kind) {
    switch (kind) {
    case H2_FAIL_FLOOD:
    case H2_FAIL_CONTINUATION: return NGHTTP2_ENHANCE_YOUR_CALM;
    case H2_FAIL_COMPRESSION: return NGHTTP2_COMPRESSION_ERROR;
    case H2_FAIL_FRAME_SIZE: return NGHTTP2_FRAME_SIZE_ERROR;
    case H2_FAIL_FLOW_CONTROL: return NGHTTP2_FLOW_CONTROL_ERROR;
    case H2_FAIL_PROTOCOL:
    case H2_FAIL_BAD_FRAME: return NGHTTP2_PROTOCOL_ERROR;
    case H2_FAIL_SETTINGS_TIMEOUT: return NGHTTP2_SETTINGS_TIMEOUT;
    case H2_FAIL_CLOSED: return NGHTTP2_NO_ERROR;
    default: return NGHTTP2_INTERNAL_ERROR;
    }
}

static const char *fail_text(long code) {
    switch (fail_code_kind(code)) {
    case H2_FAIL_INVALID_ARGUMENT: return "invalid argument";
    case H2_FAIL_RANGE: return "range";
    case H2_FAIL_INVALID_RESOURCE: return "invalid resource";
    case H2_FAIL_CLOSED: return "closed";
    case H2_FAIL_NOT_READY: return "not ready";
    case H2_FAIL_INVALID_STATE: return "invalid state";
    case H2_FAIL_BAD_PREFACE: return "bad client preface";
    case H2_FAIL_BAD_FRAME: return "bad frame";
    case H2_FAIL_FRAME_SIZE: return "frame size";
    case H2_FAIL_PROTOCOL: return "protocol failure";
    case H2_FAIL_COMPRESSION: return "header compression";
    case H2_FAIL_HEADER_LIST: return "header list";
    case H2_FAIL_CONTINUATION: return "continuation flood";
    case H2_FAIL_FLOOD: return "flood";
    case H2_FAIL_REFUSED: return "refused";
    case H2_FAIL_STREAM_CLOSED: return "stream closed";
    case H2_FAIL_RESET: return "reset";
    case H2_FAIL_GOAWAY: return "goaway";
    case H2_FAIL_FLOW_CONTROL: return "flow control";
    case H2_FAIL_SETTINGS_TIMEOUT: return "settings timeout";
    case H2_FAIL_TIMEOUT: return "timeout";
    case H2_FAIL_RESOURCE_LIMIT: return "resource limit";
    case H2_FAIL_BUSY: return "busy";
    case H2_FAIL_UNSUPPORTED: return "unsupported";
    default: return "internal";
    }
}

static void stream_clear_deadlines(h2_stream *s) {
    s->header_deadline = -1;
    s->body_deadline = -1;
    s->resp_deadline = -1;
    s->resp_total_deadline = -1;
    s->total_deadline = -1;
}

static int64_t deadline_add(int64_t now, long ms) {
    int64_t add;
    if (ms <= 0) return -1;
    add = (int64_t)ms * 1000000LL;
    if (add < 0) return INT64_MAX;
    if (now > INT64_MAX - add) return INT64_MAX;
    return now + add;
}

static void event_push(h2_conn *c, uint32_t type, int32_t id, uint32_t flags,
                       uint32_t error, int64_t aux) {
    size_t tail;
    h2_event *e;
    if (c == NULL || type == H2_EV_NONE) return;
    if (c->event_count >= c->event_cap) {
        if (!c->failed && c->session != NULL) {
            (void)nghttp2_session_terminate_session2(
                c->session, nghttp2_session_get_last_proc_stream_id(c->session),
                NGHTTP2_ENHANCE_YOUR_CALM);
        }
        c->failed = 1;
        c->failed_flush = 1;
        c->failed_code = H2_FAIL(H2_FAIL_RESOURCE_LIMIT, 0);
        c->state = H2_STATE_FAILED;
        return;
    }
    tail = (c->event_head + c->event_count) % c->event_cap;
    e = &c->events[tail];
    e->type = type;
    e->stream_id = id;
    e->flags = flags;
    e->error = error;
    e->aux = aux;
    c->event_count += 1;
    if (c->event_count > c->event_high) c->event_high = c->event_count;
}

static int event_has_data(h2_conn *c, int32_t id) {
    size_t i;
    for (i = 0; i < c->event_count; i++) {
        h2_event *e = &c->events[(c->event_head + i) % c->event_cap];
        if (e->type == H2_EV_STREAM_DATA && e->stream_id == id) return 1;
    }
    return 0;
}

static void event_data(h2_conn *c, int32_t id) {
    if (event_has_data(c, id)) return;
    event_push(c, H2_EV_STREAM_DATA, id, 0, 0, 0);
}

static h2_event *event_pop(h2_conn *c) {
    if (c == NULL || c->event_count == 0) return NULL;
    c->current = c->events[c->event_head];
    c->event_head = (c->event_head + 1) % c->event_cap;
    c->event_count -= 1;
    c->current_valid = 1;
    return &c->current;
}

static long submit_rst(h2_stream *s, uint32_t code) {
    int r;
    if (s == NULL || s->conn == NULL || s->conn->session == NULL) return 0;
    if (s->rst_submitted || s->closed) return 0;
    r = nghttp2_submit_rst_stream(s->conn->session, NGHTTP2_FLAG_NONE, s->id, code);
    if (r != 0) return map_ngerror(r);
    s->rst_submitted = 1;
    return 1;
}

static size_t consume_buffered(h2_conn *c, h2_stream *s, int connection_level) {
    size_t n;
    if (s == NULL) return 0;
    n = s->recv_end - s->recv_start;
    if (n == 0) return 0;
    s->recv_start = 0;
    s->recv_end = 0;
    if (c != NULL && c->session != NULL) {
        if (connection_level) {
            (void)nghttp2_session_consume_connection(c->session, n);
        } else {
            if (nghttp2_session_consume(c->session, s->id, n) != 0) {
                (void)nghttp2_session_consume_connection(c->session, n);
            }
        }
    }
    if (c != NULL) c->window_restored += n;
    g_window_restored += n;
    return n;
}

static void free_stream_buffers(h2_stream *s) {
    if (s == NULL) return;
    free(s->recv);
    s->recv = NULL;
    free(s->send);
    s->send = NULL;
    free(s->hreq_arena);
    s->hreq_arena = NULL;
    free(s->hreq);
    s->hreq = NULL;
    free(s->htrail_arena);
    s->htrail_arena = NULL;
    free(s->htrail);
    s->htrail = NULL;
    free(s->rhead_arena);
    s->rhead_arena = NULL;
    free(s->rhead);
    s->rhead = NULL;
    free(s->rtrail_arena);
    s->rtrail_arena = NULL;
    free(s->rtrail);
    s->rtrail = NULL;
    s->recv_cap = 0;
    s->send_cap = 0;
}

static void free_stream(h2_stream *s) {
    if (s == NULL) return;
    free_stream_buffers(s);
    free(s);
    if (g_live_streams > 0) g_live_streams -= 1;
}

static void table_remove(h2_conn *c, size_t index) {
    size_t i;
    if (c == NULL || index >= c->stream_count) return;
    for (i = index + 1; i < c->stream_count; i++) c->streams[i - 1] = c->streams[i];
    c->stream_count -= 1;
    c->streams[c->stream_count] = NULL;
}

static int sweep_streams(h2_conn *c) {
    size_t i = 0;
    if (c == NULL) return 0;
    while (i < c->stream_count) {
        h2_stream *s = c->streams[i];
        if (s == NULL) { table_remove(c, i); continue; }
        if (s->closed || s->rst_sent) {
            if (s->registered) {
                void *ptr = NULL;
                int (*release)(void *) = NULL;
                long token = s->token;
                if (sys_reg_retire(token, (int)H2_KIND_STREAM, &ptr, &release)) {
                    s->registered = 0;
                    s->token = 0;
                    if (release != NULL) (void)release(ptr);
                } else if (sys_reg_peek(token, (int)H2_KIND_STREAM) == (void *)s) {
                    /* The registry still owns this record; the shell has not
                     * closed it yet. Never free while a live token resolves to
                     * it. The shell close will retire it and defer the free. */
                    s->closing = 1;
                    i += 1;
                    continue;
                } else {
                    s->registered = 0;
                    s->token = 0;
                }
            }
            if (!s->registered && (s->closed || s->rst_sent)) {
                table_remove(c, i);
                c->streams_closed += 1;
                g_streams_closed += 1;
                free_stream(s);
                if (c->stream_count == 0 && c->preface_ok && !c->failed &&
                    !c->closed && c->state != H2_STATE_CLOSING) {
                    c->idle_active = 1;
                    c->idle_deadline = deadline_add(c->now_ns, c->idle_timeout_ms);
                }
                continue;
            }
        }
        i += 1;
    }
    return 0;
}

static int stream_release(void *ptr) {
    h2_stream *s = (h2_stream *)ptr;
    if (s == NULL) return 0;
    s->registered = 0;
    s->token = 0;
    if (s->conn == NULL) {
        free_stream(s);
        g_streams_closed += 1;
        return 0;
    }
    if (!s->closing) {
        if (!s->closed && !s->rst_submitted) {
            consume_buffered(s->conn, s, 1);
            if (!s->adopted) (void)submit_rst(s, NGHTTP2_REFUSED_STREAM);
        }
        s->closing = 1;
    }
    return 0;
}

static int conn_release(void *ptr) {
    h2_conn *c = (h2_conn *)ptr;
    size_t i;
    if (c == NULL) return 0;
    if (c->session != NULL) {
        nghttp2_session_del(c->session);
        c->session = NULL;
    }
    for (i = 0; i < c->stream_count; i++) {
        h2_stream *s = c->streams[i];
        if (s == NULL) continue;
        if (s->registered) {
            /* Orphan the shell: keep the record allocated until its own close
             * retires KIND_H2_STREAM; free only nghttp2-dependent buffers. */
            s->conn = NULL;
            consume_buffered(c, s, 1);
            free_stream_buffers(s);
            s->hreq_count = 0;
            s->htrail_count = 0;
            s->hreq_used = 0;
            s->htrail_used = 0;
            s->hreq_list_bytes = 0;
            s->htrail_list_bytes = 0;
            s->request_cleared = 1;
            stream_clear_deadlines(s);
        } else {
            free_stream(s);
            c->streams_closed += 1;
            g_streams_closed += 1;
        }
    }
    c->stream_count = 0;
    free(c->streams);
    c->streams = NULL;
    free(c->wire);
    c->wire = NULL;
    free(c->events);
    c->events = NULL;
    c->closed = 1;
    c->state = H2_STATE_CLOSED;
    if (g_live_connections > 0) g_live_connections -= 1;
    free(c);
    return 0;
}

/* ------------------------------------------------------------------ */
/* nghttp2 callbacks                                                   */
/* ------------------------------------------------------------------ */

static h2_conn *conn_of(void *user_data) { return (h2_conn *)user_data; }

static ssize_t cb_send(nghttp2_session *session, const uint8_t *data,
                       size_t length, int flags, void *user_data) {
    h2_conn *c = conn_of(user_data);
    size_t budget;
    size_t first;
    (void)session;
    (void)flags;
    if (c == NULL) return NGHTTP2_ERR_CALLBACK_FAILURE;
    budget = c->wire_cap;
    if (c->send_budget > 0 && c->send_budget < budget) budget = c->send_budget;
    if (length > budget) return NGHTTP2_ERR_CALLBACK_FAILURE;
    if (c->wire_count > budget - length) return NGHTTP2_ERR_WOULDBLOCK;
    first = c->wire_cap - c->wire_head;
    if (first > length) first = length;
    memcpy(c->wire + c->wire_head, data, first);
    if (length > first) memcpy(c->wire, data + first, length - first);
    c->wire_head = (c->wire_head + length) % c->wire_cap;
    c->wire_count += length;
    if (c->wire_count > c->wire_high) c->wire_high = c->wire_count;
    c->wire_bytes += length;
    g_wire_bytes += length;
    return (ssize_t)length;
}

static void bucket_refill(h2_conn *c) {
    int64_t elapsed;
    if (c->bucket_ns == 0) {
        c->bucket_ns = c->now_ns;
        return;
    }
    if (c->now_ns <= c->bucket_ns) return;
    elapsed = c->now_ns - c->bucket_ns;
    c->bucket_ns = c->now_ns;
    if (elapsed > 1000000000LL) elapsed = 1000000000LL;
    c->frame_tokens += (elapsed * c->frame_rate) / 1000000000LL;
    if (c->frame_tokens > c->frame_burst) c->frame_tokens = c->frame_burst;
    c->zero_tokens += (elapsed * c->zero_data_rate) / 1000000000LL;
    if (c->zero_tokens > c->zero_data_burst) c->zero_tokens = c->zero_data_burst;
}

static int cb_begin_frame(nghttp2_session *session, const nghttp2_frame_hd *hd,
                          void *user_data) {
    h2_conn *c = conn_of(user_data);
    (void)session;
    if (c == NULL || hd == NULL) return NGHTTP2_ERR_CALLBACK_FAILURE;
    c->frames_in += 1;
    g_frames_in += 1;
    bucket_refill(c);
    c->frame_tokens -= 1;
    if (c->frame_tokens < 0) {
        c->failed = 1;
        c->failed_code = H2_FAIL(H2_FAIL_FLOOD, 0);
        c->state = H2_STATE_FAILED;
        c->protocol_errors += 1;
        event_push(c, H2_EV_PROTOCOL_ERROR, 0, 0, (uint32_t)H2_FAIL_FLOOD, 0);
        return NGHTTP2_ERR_CALLBACK_FAILURE;
    }
    if (hd->type == NGHTTP2_DATA && hd->length == 0) {
        c->zero_data_frames += 1;
        g_zero_data_frames += 1;
        c->zero_tokens -= 1;
        if (c->zero_tokens < 0) {
            c->failed = 1;
            c->failed_code = H2_FAIL(H2_FAIL_FLOOD, 0);
            c->state = H2_STATE_FAILED;
            c->protocol_errors += 1;
            event_push(c, H2_EV_PROTOCOL_ERROR, 0, 0, (uint32_t)H2_FAIL_FLOOD, 0);
            return NGHTTP2_ERR_CALLBACK_FAILURE;
        }
    }
    if (hd->type == NGHTTP2_HEADERS) {
        c->block_active = 1;
        c->block_bytes = hd->length;
    } else if (hd->type == NGHTTP2_CONTINUATION && c->block_active) {
        c->block_bytes += hd->length;
    }
    if (c->block_active && c->block_bytes > c->max_header_block) {
        c->failed = 1;
        c->failed_code = H2_FAIL(H2_FAIL_CONTINUATION, 0);
        c->state = H2_STATE_FAILED;
        c->protocol_errors += 1;
        event_push(c, H2_EV_PROTOCOL_ERROR, 0, 0, (uint32_t)H2_FAIL_CONTINUATION, 0);
        return NGHTTP2_ERR_CALLBACK_FAILURE;
    }
    return 0;
}

static int name_char(unsigned char ch) {
    if (ch >= 'a' && ch <= 'z') return 1;
    if (ch >= '0' && ch <= '9') return 1;
    switch (ch) {
    case '!': case '#': case '$': case '%': case '&': case '\'': case '*':
    case '+': case '-': case '.': case '^': case '_': case '`': case '|':
    case '~': return 1;
    default: return 0;
    }
}

static int header_name_ok(const unsigned char *name, size_t len, int allow_pseudo) {
    size_t i;
    if (len == 0 || len > 65535) return 0;
    if (name[0] == ':') {
        if (!allow_pseudo || len < 2) return 0;
        for (i = 1; i < len; i++) if (!name_char(name[i])) return 0;
        return 1;
    }
    for (i = 0; i < len; i++) if (!name_char(name[i])) return 0;
    return 1;
}

static int header_value_ok(const unsigned char *value, size_t len) {
    size_t i;
    for (i = 0; i < len; i++) {
        unsigned char ch = value[i];
        if (ch == 0 || ch == '\r' || ch == '\n') return 0;
        if (ch < 0x20 && ch != '\t') return 0;
        if (ch == 0x7f) return 0;
    }
    return 1;
}

static int store_header(unsigned char *arena, size_t cap, size_t *used,
                        h2_nv_ref *refs, size_t refcap, size_t *count,
                        size_t *list_bytes, size_t list_cap,
                        const unsigned char *name, size_t name_len,
                        const unsigned char *value, size_t value_len,
                        int lowercase_name, int allow_pseudo) {
    size_t total = name_len + value_len + 32;
    size_t off;
    size_t i;
    if (name_len > 65535 || value_len > 65535) return -1;
    if (*count >= refcap) return -1;
    if (!header_name_ok(name, name_len, allow_pseudo)) return -1;
    if (!header_value_ok(value, value_len)) return -1;
    if (total > list_cap - *list_bytes) return -1;
    if (name_len > cap - *used) return -1;
    if (value_len > cap - *used - name_len) return -1;
    off = *used;
    for (i = 0; i < name_len; i++) {
        unsigned char ch = name[i];
        if (lowercase_name && ch >= 'A' && ch <= 'Z') ch = (unsigned char)(ch + 32);
        arena[off + i] = ch;
    }
    memcpy(arena + off + name_len, value, value_len);
    *used += name_len + value_len;
    refs[*count].name_off = (uint32_t)off;
    refs[*count].name_len = (uint32_t)name_len;
    refs[*count].value_off = (uint32_t)(off + name_len);
    refs[*count].value_len = (uint32_t)value_len;
    *count += 1;
    *list_bytes += total;
    return 0;
}

static h2_stream *new_stream(h2_conn *c, int32_t id) {
    h2_stream *s;
    if (c->stream_count >= c->stream_cap) return NULL;
    s = (h2_stream *)h2_alloc(sizeof(*s));
    if (s == NULL) return NULL;
    memset(s, 0, sizeof(*s));
    s->id = id;
    s->conn = c;
    s->status = 0;
    s->declared_length = -1;
    stream_clear_deadlines(s);
    s->recv_cap = c->max_stream_recv_buffer;
    s->recv = (unsigned char *)h2_alloc(s->recv_cap);
    s->send_cap = c->max_stream_send_buffer;
    s->send = (unsigned char *)h2_alloc(s->send_cap);
    s->hreq_cap = c->max_header_list;
    s->hreq_arena = (unsigned char *)h2_alloc(s->hreq_cap);
    s->hreq_refcap = c->max_header_count;
    s->hreq = (h2_nv_ref *)h2_alloc(sizeof(h2_nv_ref) * s->hreq_refcap);
    s->htrail_cap = c->max_trailer_bytes;
    s->htrail_arena = (unsigned char *)h2_alloc(s->htrail_cap);
    s->htrail_refcap = c->max_trailer_count;
    s->htrail = (h2_nv_ref *)h2_alloc(sizeof(h2_nv_ref) * s->htrail_refcap);
    s->rhead_cap = c->max_response_header;
    s->rhead_arena = (unsigned char *)h2_alloc(s->rhead_cap);
    s->rhead_refcap = c->max_header_count;
    s->rhead = (h2_nv_ref *)h2_alloc(sizeof(h2_nv_ref) * s->rhead_refcap);
    s->rtrail_cap = c->max_response_trailer;
    s->rtrail_arena = (unsigned char *)h2_alloc(s->rtrail_cap);
    s->rtrail_refcap = c->max_response_trailer_count;
    s->rtrail = (h2_nv_ref *)h2_alloc(sizeof(h2_nv_ref) * s->rtrail_refcap);
    if (s->recv == NULL || s->send == NULL || s->hreq_arena == NULL ||
        s->hreq == NULL || s->htrail_arena == NULL || s->htrail == NULL ||
        s->rhead_arena == NULL || s->rhead == NULL ||
        s->rtrail_arena == NULL || s->rtrail == NULL) {
        free_stream_buffers(s);
        free(s);
        return NULL;
    }
    c->streams[c->stream_count++] = s;
    c->streams_opened += 1;
    g_streams_opened += 1;
    g_live_streams += 1;
    c->idle_active = 0;
    c->idle_deadline = -1;
    return s;
}

static int cb_begin_headers(nghttp2_session *session, const nghttp2_frame *frame,
                            void *user_data) {
    h2_conn *c = conn_of(user_data);
    h2_stream *s;
    (void)session;
    if (c == NULL || frame == NULL) return NGHTTP2_ERR_CALLBACK_FAILURE;
    if (frame->hd.type != NGHTTP2_HEADERS) return 0;
    s = find_stream(c, frame->hd.stream_id);
    if (s != NULL && frame->headers.cat == NGHTTP2_HCAT_REQUEST) {
        /* A second request HEADERS is a protocol error handled by nghttp2. */
        return 0;
    }
    if (s == NULL) {
        if (c->state == H2_STATE_CLOSING || c->state == H2_STATE_CLOSED ||
            c->state == H2_STATE_FAILED) {
            c->refused_id = frame->hd.stream_id;
            return 0;
        }
        if (c->streams_opened >= c->max_streams) {
            c->failed = 1;
            c->failed_code = H2_FAIL(H2_FAIL_FLOOD, 0);
            c->state = H2_STATE_FAILED;
            c->protocol_errors += 1;
            event_push(c, H2_EV_PROTOCOL_ERROR, 0, 0, (uint32_t)H2_FAIL_FLOOD, 0);
            return NGHTTP2_ERR_CALLBACK_FAILURE;
        }
        if (c->stream_count >= c->max_concurrent) {
            c->refused_id = frame->hd.stream_id;
            return 0;
        }
        s = new_stream(c, frame->hd.stream_id);
        if (s == NULL) {
            (void)nghttp2_submit_rst_stream(session, NGHTTP2_FLAG_NONE,
                                            frame->hd.stream_id,
                                            NGHTTP2_REFUSED_STREAM);
            return 0;
        }
    }
    if (s->closed) return 0;
    if (c->state == H2_STATE_AWAIT_SETTINGS) c->state = H2_STATE_OPEN;
    s->header_active = 1;
    s->in_trailers = (frame->headers.cat == NGHTTP2_HCAT_HEADERS) ? 1U : 0U;
    s->header_deadline = deadline_add(c->now_ns, c->header_timeout_ms);
    if (frame->headers.cat == NGHTTP2_HCAT_REQUEST) {
        s->total_deadline = deadline_add(c->now_ns, c->stream_total_timeout_ms);
        s->hreq_count = 0;
        s->hreq_used = 0;
        s->hreq_list_bytes = 0;
        s->request_cleared = 0;
    } else {
        s->htrail_count = 0;
        s->htrail_used = 0;
        s->htrail_list_bytes = 0;
    }
    return 0;
}

static int cb_header(nghttp2_session *session, const nghttp2_frame *frame,
                     const uint8_t *name, size_t namelen, const uint8_t *value,
                     size_t valuelen, uint8_t flags, void *user_data) {
    h2_conn *c = conn_of(user_data);
    h2_stream *s;
    (void)session;
    (void)flags;
    if (c == NULL || frame == NULL) return NGHTTP2_ERR_CALLBACK_FAILURE;
    if (c->refused_id == frame->hd.stream_id) return 0;
    s = find_stream(c, frame->hd.stream_id);
    if (s == NULL || s->closed || s->rejected) return 0;
    if (!s->header_active) return 0;
    if (namelen > c->max_header_field || valuelen > c->max_header_field) {
        (void)submit_rst(s, NGHTTP2_ENHANCE_YOUR_CALM);
        s->rejected = 1;
        return 0;
    }
    if (s->in_trailers) {
        if (store_header(s->htrail_arena, s->htrail_cap, &s->htrail_used,
                         s->htrail, s->htrail_refcap, &s->htrail_count,
                         &s->htrail_list_bytes, c->max_trailer_bytes,
                         name, namelen, value, valuelen, 0, 0) != 0) {
            (void)submit_rst(s, NGHTTP2_ENHANCE_YOUR_CALM);
            s->rejected = 1;
        }
    } else {
        if (store_header(s->hreq_arena, s->hreq_cap, &s->hreq_used,
                         s->hreq, s->hreq_refcap, &s->hreq_count,
                         &s->hreq_list_bytes, c->max_header_list,
                         name, namelen, value, valuelen, 0, 1) != 0) {
            (void)submit_rst(s, NGHTTP2_ENHANCE_YOUR_CALM);
            s->rejected = 1;
        }
    }
    if (!s->rejected) {
        c->header_bytes += namelen + valuelen;
        g_header_bytes += namelen + valuelen;
    }
    return 0;
}

static int cb_data_chunk(nghttp2_session *session, uint8_t flags,
                         int32_t stream_id, const uint8_t *data, size_t len,
                         void *user_data) {
    h2_conn *c = conn_of(user_data);
    h2_stream *s;
    size_t used;
    size_t free_space;
    (void)session;
    (void)flags;
    if (c == NULL) return NGHTTP2_ERR_CALLBACK_FAILURE;
    s = find_stream(c, stream_id);
    if (s == NULL) {
        if (len > 0) (void)nghttp2_session_consume_connection(session, len);
        return 0;
    }
    if (s->closed || s->closing || s->rejected) {
        if (len > 0) {
            (void)nghttp2_session_consume_connection(session, len);
            c->window_restored += len;
            g_window_restored += len;
        }
        return 0;
    }
    used = s->recv_end - s->recv_start;
    if (len > s->recv_cap - used) {
        c->failed = 1;
        c->failed_code = H2_FAIL(H2_FAIL_FLOW_CONTROL, 0);
        c->state = H2_STATE_FAILED;
        c->protocol_errors += 1;
        event_push(c, H2_EV_PROTOCOL_ERROR, stream_id, 0,
                   (uint32_t)H2_FAIL_FLOW_CONTROL, 0);
        return NGHTTP2_ERR_CALLBACK_FAILURE;
    }
    free_space = s->recv_cap - s->recv_end;
    if (len > free_space && s->recv_start > 0) {
        memmove(s->recv, s->recv + s->recv_start, used);
        s->recv_start = 0;
        s->recv_end = used;
        free_space = s->recv_cap - s->recv_end;
    }
    if (len > free_space) {
        c->failed = 1;
        c->failed_code = H2_FAIL(H2_FAIL_FLOW_CONTROL, 0);
        c->state = H2_STATE_FAILED;
        c->protocol_errors += 1;
        event_push(c, H2_EV_PROTOCOL_ERROR, stream_id, 0,
                   (uint32_t)H2_FAIL_FLOW_CONTROL, 0);
        return NGHTTP2_ERR_CALLBACK_FAILURE;
    }
    memcpy(s->recv + s->recv_end, data, len);
    s->recv_end += len;
    s->body_bytes_in += len;
    g_body_in += len;
    if (s->body_deadline >= 0) s->body_deadline = deadline_add(c->now_ns, c->body_idle_timeout_ms);
    event_data(c, stream_id);
    return 0;
}

static int cb_frame_recv(nghttp2_session *session, const nghttp2_frame *frame,
                         void *user_data) {
    h2_conn *c = conn_of(user_data);
    h2_stream *s;
    (void)session;
    if (c == NULL || frame == NULL) return NGHTTP2_ERR_CALLBACK_FAILURE;
    s = find_stream(c, frame->hd.stream_id);
    switch (frame->hd.type) {
    case NGHTTP2_HEADERS:
        c->block_active = 0;
        c->block_bytes = 0;
        if (c->refused_id == frame->hd.stream_id) {
            (void)nghttp2_submit_rst_stream(session, NGHTTP2_FLAG_NONE,
                                            frame->hd.stream_id,
                                            NGHTTP2_REFUSED_STREAM);
            c->refused_id = 0;
            return 0;
        }
        if (s == NULL) return 0;
        s->header_active = 0;
        s->header_deadline = -1;
        if (s->rejected) return 0;
        if (s->in_trailers) {
            s->in_trailers = 0;
            event_push(c, H2_EV_TRAILERS, s->id, H2_FLAG_TRAILERS, 0, 0);
        } else {
            s->headers_done = 1;
            event_push(c, H2_EV_STREAM_STARTED, s->id, 0, 0, 0);
        }
        if ((frame->hd.flags & NGHTTP2_FLAG_END_STREAM) != 0) {
            s->peer_end = 1;
            event_push(c, H2_EV_STREAM_END, s->id, H2_FLAG_END_STREAM, 0, 0);
        }
        break;
    case NGHTTP2_CONTINUATION:
        if ((frame->hd.flags & NGHTTP2_FLAG_END_HEADERS) != 0) {
            c->block_active = 0;
            c->block_bytes = 0;
        }
        break;
    case NGHTTP2_DATA:
        if (s == NULL) return 0;
        if ((frame->hd.flags & NGHTTP2_FLAG_END_STREAM) != 0) {
            s->peer_end = 1;
            event_push(c, H2_EV_STREAM_END, s->id, H2_FLAG_END_STREAM, 0, 0);
        }
        break;
    case NGHTTP2_SETTINGS:
        if ((frame->hd.flags & NGHTTP2_FLAG_ACK) != 0) {
            c->settings_acked = 1;
            c->settings_active = 0;
            c->settings_deadline = -1;
            if (c->stream_count == 0 && c->preface_ok && !c->failed &&
                !c->closed && c->state != H2_STATE_CLOSING) {
                c->idle_active = 1;
                c->idle_deadline = deadline_add(c->now_ns, c->idle_timeout_ms);
            }
            event_push(c, H2_EV_SETTINGS_ACKED, 0, 0, 0, 0);
        }
        break;
    case NGHTTP2_PING:
        if ((frame->hd.flags & NGHTTP2_FLAG_ACK) != 0 && c->barrier_nonce_valid &&
            memcmp(frame->ping.opaque_data, c->barrier_nonce, 8) == 0) {
            c->barrier_acked = 1;
        }
        break;
    case NGHTTP2_GOAWAY:
        c->goaways_received += 1;
        g_goaways_received += 1;
        event_push(c, H2_EV_GOAWAY, 0, 0, frame->goaway.error_code,
                   frame->goaway.last_stream_id);
        c->state = H2_STATE_DRAINING;
        break;
    case NGHTTP2_RST_STREAM:
        c->resets_received += 1;
        g_resets_received += 1;
        break;
    default:
        break;
    }
    return 0;
}

static int cb_stream_close(nghttp2_session *session, int32_t stream_id,
                           uint32_t error_code, void *user_data) {
    h2_conn *c = conn_of(user_data);
    h2_stream *s;
    (void)session;
    if (c == NULL) return 0;
    s = find_stream(c, stream_id);
    if (s == NULL) return 0;
    consume_buffered(c, s, 1);
    s->closed = 1;
    s->closing = 1;
    stream_clear_deadlines(s);
    event_push(c, H2_EV_STREAM_CLOSED, stream_id, 0, error_code, 0);
    return 0;
}

static int cb_frame_send(nghttp2_session *session, const nghttp2_frame *frame,
                         void *user_data) {
    h2_conn *c = conn_of(user_data);
    (void)session;
    if (c == NULL || frame == NULL) return 0;
    if (frame->hd.type == NGHTTP2_RST_STREAM) {
        h2_stream *s = find_stream(c, frame->hd.stream_id);
        if (s != NULL) s->rst_sent = 1;
        c->resets_sent += 1;
        g_resets_sent += 1;
    } else if (frame->hd.type == NGHTTP2_GOAWAY) {
        c->goaways_sent += 1;
        g_goaways_sent += 1;
        if (c->drain_state == 1) {
            c->drain_state = 2;
        } else if (c->drain_state == 4) {
            c->drain_final_sent = 1;
        } else if (!c->failed && c->state != H2_STATE_DRAINING &&
                   c->state != H2_STATE_CLOSING) {
            /* nghttp2 queues GOAWAY for connection-level protocol errors
             * without necessarily returning a negative mem_recv result. The
             * frame-send callback is the authoritative point to surface it. */
            c->failed = 1;
            c->failed_flush = 1;
            c->failed_code = H2_FAIL(H2_FAIL_PROTOCOL, frame->goaway.error_code);
            c->state = H2_STATE_FAILED;
            c->protocol_errors += 1;
            g_protocol_errors += 1;
            event_push(c, H2_EV_PROTOCOL_ERROR, 0, 0,
                       (uint32_t)H2_FAIL_PROTOCOL, 0);
        }
    } else if (frame->hd.type == NGHTTP2_HEADERS) {
        h2_stream *s = find_stream(c, frame->hd.stream_id);
        if (s != NULL && s->response_state == 1) {
            s->response_state = 2;
            s->resp_total_deadline = deadline_add(c->now_ns, c->response_total_timeout_ms);
            s->resp_deadline = deadline_add(c->now_ns, c->response_timeout_ms);
        }
    }
    return 0;
}

static int cb_frame_not_send(nghttp2_session *session, const nghttp2_frame *frame,
                             int lib_error_code, void *user_data) {
    h2_conn *c = conn_of(user_data);
    (void)session;
    (void)lib_error_code;
    if (c == NULL || frame == NULL) return 0;
    if (frame->hd.type == NGHTTP2_RST_STREAM) {
        h2_stream *s = find_stream(c, frame->hd.stream_id);
        if (s != NULL) s->rst_sent = 1;
    }
    return 0;
}

static ssize_t data_read_cb(nghttp2_session *session, int32_t stream_id,
                            uint8_t *buf, size_t length, uint32_t *data_flags,
                            nghttp2_data_source *source, void *user_data) {
    h2_stream *s = (h2_stream *)(source != NULL ? source->ptr : NULL);
    h2_conn *c = conn_of(user_data);
    size_t avail;
    size_t n;
    (void)session;
    if (s == NULL || c == NULL || s->id != stream_id) return NGHTTP2_ERR_CALLBACK_FAILURE;
    if (s->closing || s->closed) {
        *data_flags |= NGHTTP2_DATA_FLAG_EOF;
        return 0;
    }
    avail = s->send_end - s->send_start;
    if (avail > 0) {
        n = avail;
        if (n > length) n = length;
        memcpy(buf, s->send + s->send_start, n);
        s->send_start += n;
        if (s->send_start == s->send_end) {
            s->send_start = 0;
            s->send_end = 0;
        }
        if (s->resp_deadline >= 0) s->resp_deadline = deadline_add(c->now_ns, c->response_timeout_ms);
        return (ssize_t)n;
    }
    if (!s->finished) return NGHTTP2_ERR_DEFERRED;
    if (s->rtrail_count > 0 && !s->trailers_submitted) {
        nghttp2_nv *nva;
        size_t i;
        int r;
        nva = (nghttp2_nv *)h2_alloc(sizeof(nghttp2_nv) * s->rtrail_count);
        if (nva == NULL) return NGHTTP2_ERR_CALLBACK_FAILURE;
        for (i = 0; i < s->rtrail_count; i++) {
            nva[i].name = (uint8_t *)(s->rtrail_arena + s->rtrail[i].name_off);
            nva[i].namelen = s->rtrail[i].name_len;
            nva[i].value = (uint8_t *)(s->rtrail_arena + s->rtrail[i].value_off);
            nva[i].valuelen = s->rtrail[i].value_len;
            nva[i].flags = NGHTTP2_NV_FLAG_NONE;
        }
        r = nghttp2_submit_trailer(session, stream_id, nva, s->rtrail_count);
        free(nva);
        if (r != 0) return NGHTTP2_ERR_CALLBACK_FAILURE;
        s->trailers_submitted = 1;
        *data_flags |= NGHTTP2_DATA_FLAG_EOF | NGHTTP2_DATA_FLAG_NO_END_STREAM;
        return 0;
    }
    *data_flags |= NGHTTP2_DATA_FLAG_EOF;
    return 0;
}

/* ------------------------------------------------------------------ */
/* Public scalar surface                                               */
/* ------------------------------------------------------------------ */

long h2_init(void) {
    const nghttp2_info *info = nghttp2_version(0);
    if (info == NULL) return h2_err(H2_FAIL_UNSUPPORTED, 0);
    if (info->age != 1) return h2_err(H2_FAIL_UNSUPPORTED, info->age);
    if (info->version_num < 0x013b00) return h2_err(H2_FAIL_UNSUPPORTED, info->version_num);
    if (info->proto_str == NULL || strcmp(info->proto_str, "h2") != 0)
        return h2_err(H2_FAIL_UNSUPPORTED, 0);
    return 1;
}

long h2_version_num(void) {
    const nghttp2_info *info = nghttp2_version(0);
    return info == NULL ? 0 : (long)info->version_num;
}

static int read_options(h2_conn *c, const int64_t *v) {
    if ((uint64_t)v[H2_OPT_MAGIC] != (uint64_t)H2_OPTION_MAGIC) return -1;
    c->mode = (long)v[H2_OPT_MODE];
    if (c->mode != H2_MODE_TLS && c->mode != H2_MODE_PRIOR_KNOWLEDGE) return -1;
    c->max_concurrent = (size_t)v[H2_OPT_MAX_CONCURRENT];
    c->max_streams = (size_t)v[H2_OPT_MAX_STREAMS];
    c->max_header_list = (size_t)v[H2_OPT_MAX_HEADER_LIST];
    c->max_header_count = (size_t)v[H2_OPT_MAX_HEADER_COUNT];
    c->max_header_field = (size_t)v[H2_OPT_MAX_HEADER_FIELD];
    c->max_header_block = (size_t)v[H2_OPT_MAX_HEADER_BLOCK];
    c->max_continuations = (size_t)v[H2_OPT_MAX_CONTINUATIONS];
    c->max_header_arena = (size_t)v[H2_OPT_MAX_HEADER_ARENA];
    c->max_stream_recv_window = (size_t)v[H2_OPT_MAX_STREAM_RECV_WINDOW];
    c->max_stream_recv_buffer = (size_t)v[H2_OPT_MAX_STREAM_RECV_BUFFER];
    c->max_connection_window = (size_t)v[H2_OPT_MAX_CONNECTION_WINDOW];
    c->max_stream_send_buffer = (size_t)v[H2_OPT_MAX_STREAM_SEND_BUFFER];
    c->max_wire = (size_t)v[H2_OPT_MAX_WIRE];
    c->max_events = (size_t)v[H2_OPT_MAX_EVENTS];
    c->max_body = (size_t)v[H2_OPT_MAX_BODY];
    c->max_trailer_bytes = (size_t)v[H2_OPT_MAX_TRAILER_BYTES];
    c->max_trailer_count = (size_t)v[H2_OPT_MAX_TRAILER_COUNT];
    c->max_response_header = (size_t)v[H2_OPT_MAX_RESPONSE_HEADER];
    c->max_response_trailer = (size_t)v[H2_OPT_MAX_RESPONSE_TRAILER];
    c->max_response_trailer_count = (size_t)v[H2_OPT_MAX_RESPONSE_TRAILER_COUNT];
    c->max_read_chunk = (size_t)v[H2_OPT_MAX_READ_CHUNK];
    c->max_reset_burst = (long)v[H2_OPT_MAX_RESET_BURST];
    c->max_reset_rate = (long)v[H2_OPT_MAX_RESET_RATE];
    c->max_ping_acks = (long)v[H2_OPT_MAX_PING_ACKS];
    c->max_settings_entries = (long)v[H2_OPT_MAX_SETTINGS_ENTRIES];
    c->frame_burst = (long)v[H2_OPT_FRAME_BURST];
    c->frame_rate = (long)v[H2_OPT_FRAME_RATE];
    c->zero_data_burst = (long)v[H2_OPT_ZERO_DATA_BURST];
    c->zero_data_rate = (long)v[H2_OPT_ZERO_DATA_RATE];
    c->preface_timeout_ms = (long)v[H2_OPT_PREFACE_TIMEOUT_MS];
    c->settings_timeout_ms = (long)v[H2_OPT_SETTINGS_TIMEOUT_MS];
    c->header_timeout_ms = (long)v[H2_OPT_HEADER_TIMEOUT_MS];
    c->body_idle_timeout_ms = (long)v[H2_OPT_BODY_IDLE_TIMEOUT_MS];
    c->response_timeout_ms = (long)v[H2_OPT_RESPONSE_TIMEOUT_MS];
    c->response_total_timeout_ms = (long)v[H2_OPT_RESPONSE_TOTAL_TIMEOUT_MS];
    c->stream_total_timeout_ms = (long)v[H2_OPT_STREAM_TOTAL_TIMEOUT_MS];
    c->idle_timeout_ms = (long)v[H2_OPT_IDLE_TIMEOUT_MS];
    c->drain_barrier_ms = (long)v[H2_OPT_DRAIN_BARRIER_MS];
    c->drain_timeout_ms = (long)v[H2_OPT_DRAIN_TIMEOUT_MS];
    c->reserved = (size_t)v[H2_OPT_RESERVED];
    if (c->max_concurrent < 1 || c->max_concurrent > (size_t)H2_MAX_CONCURRENT_HARD) return -1;
    if (c->max_streams < c->max_concurrent) return -1;
    if (c->max_header_list < 1 || c->max_header_count < 1 || c->max_header_field < 1) return -1;
    if (c->max_header_block < c->max_header_list) return -1;
    if (c->max_stream_recv_buffer < c->max_stream_recv_window) return -1;
    if (c->max_stream_recv_buffer != c->max_stream_recv_window) return -1;
    if (c->max_stream_send_buffer < 1) return -1;
    if (c->max_wire < 1024 || c->max_wire > (size_t)H2_MAX_WIRE_HARD) return -1;
    if (c->max_events < 1 || c->max_events > (size_t)H2_MAX_EVENTS_HARD) return -1;
    if (c->max_read_chunk < 1 || c->max_read_chunk > c->max_wire - 9) return -1;
    if (c->max_connection_window < c->max_stream_recv_window) return -1;
    if (c->max_response_header < 1 || c->max_response_trailer < 1) return -1;
    if (c->max_header_list + c->max_trailer_bytes + c->max_response_header +
        c->max_response_trailer > c->max_header_arena) return -1;
    if (c->frame_burst < 1 || c->frame_rate < 1 || c->zero_data_burst < 1 ||
        c->zero_data_rate < 1) return -1;
    return 0;
}

long h2_conn_new(long option_token, long option_len) {
    unsigned char *opt_data = NULL;
    size_t opt_cap = 0;
    int64_t values[H2_OPTION_FIELDS];
    h2_conn *c;
    nghttp2_session_callbacks *callbacks = NULL;
    nghttp2_option *option = NULL;
    nghttp2_settings_entry iv[7];
    size_t niv = 0;
    long token;
    int r;
    if (option_len < H2_OPTION_BYTES) return h2_err(H2_FAIL_INVALID_ARGUMENT, 0);
    if (buffer_view(option_token, &opt_data, &opt_cap) != 0 ||
        opt_cap < (size_t)option_len) return h2_err(H2_FAIL_INVALID_ARGUMENT, 0);
    memcpy(values, opt_data, sizeof(values));
    c = (h2_conn *)h2_alloc(sizeof(*c));
    if (c == NULL) return h2_err(H2_FAIL_RESOURCE_LIMIT, 0);
    memset(c, 0, sizeof(*c));
    if (read_options(c, values) != 0) {
        free(c);
        return h2_err(H2_FAIL_INVALID_ARGUMENT, 0);
    }
    c->state = H2_STATE_AWAIT_PREFACE;
    c->preface_deadline = -1;
    c->settings_deadline = -1;
    c->idle_deadline = -1;
    c->drain_deadline = -1;
    c->barrier_deadline = -1;
    c->frame_tokens = c->frame_burst;
    c->zero_tokens = c->zero_data_burst;
    c->stream_cap = c->max_concurrent;
    c->streams = (h2_stream **)h2_alloc(sizeof(h2_stream *) * c->stream_cap);
    c->wire_cap = c->max_wire;
    c->wire = (unsigned char *)h2_alloc(c->wire_cap);
    c->event_cap = c->max_events;
    c->events = (h2_event *)h2_alloc(sizeof(h2_event) * c->event_cap);
    if (c->streams == NULL || c->wire == NULL || c->events == NULL) {
        free(c->streams);
        free(c->wire);
        free(c->events);
        free(c);
        return h2_err(H2_FAIL_RESOURCE_LIMIT, 0);
    }
    if (nghttp2_session_callbacks_new(&callbacks) != 0) goto nomem;
    nghttp2_session_callbacks_set_send_callback(callbacks, cb_send);
    nghttp2_session_callbacks_set_on_begin_frame_callback(callbacks, cb_begin_frame);
    nghttp2_session_callbacks_set_on_begin_headers_callback(callbacks, cb_begin_headers);
    nghttp2_session_callbacks_set_on_header_callback(callbacks, cb_header);
    nghttp2_session_callbacks_set_on_data_chunk_recv_callback(callbacks, cb_data_chunk);
    nghttp2_session_callbacks_set_on_frame_recv_callback(callbacks, cb_frame_recv);
    nghttp2_session_callbacks_set_on_stream_close_callback(callbacks, cb_stream_close);
    nghttp2_session_callbacks_set_on_frame_send_callback(callbacks, cb_frame_send);
    nghttp2_session_callbacks_set_on_frame_not_send_callback(callbacks, cb_frame_not_send);
    if (nghttp2_option_new(&option) != 0) goto nomem;
    nghttp2_option_set_no_recv_client_magic(option, 1);
    nghttp2_option_set_no_auto_window_update(option, 1);
    /* Retain closed streams up to the advertised max concurrent streams.
     * RFC 9113 5.1 requires STREAM_CLOSED (or a connection error) for frames
     * that arrive after RST_STREAM or END_STREAM; no_closed_streams discards
     * that state, so nghttp2 silently ignores those frames. nghttp2 bounds
     * the retained set by max_concurrent_streams, so this stays bounded. */
    nghttp2_option_set_max_settings(option, (size_t)c->max_settings_entries);
    nghttp2_option_set_max_outbound_ack(option, (size_t)c->max_ping_acks);
    nghttp2_option_set_max_continuations(option, (size_t)c->max_continuations);
    nghttp2_option_set_stream_reset_rate_limit(option, (size_t)c->max_reset_burst,
                                               (size_t)c->max_reset_rate);
    nghttp2_option_set_max_deflate_dynamic_table_size(option, 4096);
    r = nghttp2_session_server_new3(&c->session, callbacks, c, option, NULL);
    nghttp2_option_del(option);
    nghttp2_session_callbacks_del(callbacks);
    option = NULL;
    callbacks = NULL;
    if (r != 0) goto nomem;
    iv[niv].settings_id = NGHTTP2_SETTINGS_HEADER_TABLE_SIZE;
    iv[niv++].value = 4096;
    iv[niv].settings_id = NGHTTP2_SETTINGS_ENABLE_PUSH;
    iv[niv++].value = 0;
    iv[niv].settings_id = NGHTTP2_SETTINGS_MAX_CONCURRENT_STREAMS;
    iv[niv++].value = (uint32_t)c->max_concurrent;
    iv[niv].settings_id = NGHTTP2_SETTINGS_INITIAL_WINDOW_SIZE;
    iv[niv++].value = (uint32_t)c->max_stream_recv_window;
    iv[niv].settings_id = NGHTTP2_SETTINGS_MAX_FRAME_SIZE;
    iv[niv++].value = 16384;
    iv[niv].settings_id = NGHTTP2_SETTINGS_MAX_HEADER_LIST_SIZE;
    /* Advertise a soft ceiling above the hard decoded-list cap. nghttp2 then
     * leaves the stream-level rejection to our bounded callback instead of
     * turning an oversized list into a connection error. */
    iv[niv++].value = (uint32_t)(c->max_header_list > 0x3fffffffu / 4 ?
                                 c->max_header_list : c->max_header_list * 4);
    /* Do not advertise SETTINGS_NO_RFC7540_PRIORITIES. RFC 9218 would let us
     * ignore PRIORITY frames, but nghttp2 then destroys closed streams and
     * loses the RFC 9113 5.1 STREAM_CLOSED detection required for frames that
     * arrive after RST_STREAM or END_STREAM. We never schedule on the retained
     * RFC 7540 priority tree; nghttp2 bounds closed streams by the advertised
     * MAX_CONCURRENT_STREAMS. */
    r = nghttp2_submit_settings(c->session, NGHTTP2_FLAG_NONE, iv, niv);
    if (r != 0) goto nomem;
    c->settings_sent = 1;
    if (nghttp2_session_set_local_window_size(c->session, NGHTTP2_FLAG_NONE, 0,
                                              (int32_t)c->max_connection_window) != 0) {
        goto nomem;
    }
    token = sys_reg_new_object((int)H2_KIND_CONNECTION, c, conn_release, c->reserved);
    if (token < 0) {
        conn_release(c);
        return h2_err(H2_FAIL_RESOURCE_LIMIT, 0);
    }
    g_live_connections += 1;
    return token;
nomem:
    if (option != NULL) nghttp2_option_del(option);
    if (callbacks != NULL) nghttp2_session_callbacks_del(callbacks);
    if (c->session != NULL) nghttp2_session_del(c->session);
    free(c->streams);
    free(c->wire);
    free(c->events);
    free(c);
    return h2_err(H2_FAIL_RESOURCE_LIMIT, 0);
}

static long conn_fail(h2_conn *c, long code, uint32_t event_error) {
    if (c == NULL) return h2_err(H2_FAIL_INVALID_RESOURCE, 0);
    c->failed = 1;
    c->failed_flush = 1;
    c->failed_code = code;
    c->state = H2_STATE_FAILED;
    c->protocol_errors += 1;
    g_protocol_errors += 1;
    event_push(c, H2_EV_PROTOCOL_ERROR, 0, 0, event_error, 0);
    return -code;
}

long h2_conn_feed(long conn_token, long in_token, long offset, long count,
                  long now_ns) {
    h2_conn *c = peek_conn(conn_token);
    unsigned char *data = NULL;
    size_t cap = 0;
    size_t start;
    size_t total;
    ssize_t r;
    long preface_used = 0;
    if (c == NULL) return h2_err(H2_FAIL_INVALID_RESOURCE, 0);
    if (count < 0 || offset < 0) return h2_err(H2_FAIL_INVALID_ARGUMENT, 0);
    if (buffer_view(in_token, &data, &cap) != 0) return h2_err(H2_FAIL_INVALID_RESOURCE, 0);
    if ((size_t)offset > cap || (size_t)count > cap - (size_t)offset)
        return h2_err(H2_FAIL_RANGE, 0);
    c->now_ns = now_ns;
    if (c->failed || c->closed) return h2_err(H2_FAIL_CLOSED, 0);
    if (!c->preface_ok) {
        size_t need = 24 - c->preface_got;
        size_t have = (size_t)count;
        size_t take = need < have ? need : have;
        size_t i;
        for (i = 0; i < take; i++) {
            unsigned char ch = data[offset + i];
            static const unsigned char magic[24] = "PRI * HTTP/2.0\r\n\r\nSM\r\n\r\n";
            if (ch != magic[c->preface_got + i]) {
                return conn_fail(c, H2_FAIL(H2_FAIL_BAD_PREFACE, 0),
                                 (uint32_t)H2_FAIL_BAD_PREFACE);
            }
        }
        c->preface_got += take;
        preface_used = (long)take;
        offset += (long)take;
        count -= (long)take;
        if (c->preface_got < 24) return preface_used;
        c->preface_ok = 1;
        c->state = H2_STATE_AWAIT_SETTINGS;
        c->settings_active = 1;
        c->settings_deadline = deadline_add(now_ns, c->settings_timeout_ms);
        c->idle_active = 1;
        c->idle_deadline = deadline_add(now_ns, c->idle_timeout_ms);
    }
    if (count == 0) return preface_used;
    start = (size_t)offset;
    total = (size_t)count;
    r = nghttp2_session_mem_recv(c->session, data + start, total);
    sweep_streams(c);
    if (r < 0) {
        long code = map_ngerror((int)r);
        long kind = fail_code_kind(code);
        if (c->session != NULL && kind != H2_FAIL_BAD_PREFACE &&
            kind != H2_FAIL_CLOSED) {
            (void)nghttp2_session_terminate_session2(
                c->session, nghttp2_session_get_last_proc_stream_id(c->session),
                rfc_code_for_fail(kind));
        }
        return conn_fail(c, code, (uint32_t)kind);
    }
    /* nghttp2 reports some connection errors by queueing GOAWAY and stopping
     * input rather than returning a negative value (for example DATA on stream
     * 0 or a zero WINDOW_UPDATE increment). Surface that as a terminal protocol
     * error so obo flushes the queued GOAWAY and closes. A peer GOAWAY is a
     * drain, not a failure, and has already moved the state to DRAINING. */
    if (c->session != NULL && !c->failed && c->state != H2_STATE_DRAINING &&
        c->state != H2_STATE_CLOSING &&
        nghttp2_session_want_read(c->session) == 0) {
        (void)conn_fail(c, H2_FAIL(H2_FAIL_PROTOCOL, 0),
                        (uint32_t)H2_FAIL_PROTOCOL);
    }
    return preface_used + (long)r;
}

long h2_conn_send(long conn_token, long max) {
    h2_conn *c = peek_conn(conn_token);
    size_t before;
    int r;
    if (c == NULL) return h2_err(H2_FAIL_INVALID_RESOURCE, 0);
    if (c->closed) return h2_err(H2_FAIL_CLOSED, 0);
    if (c->failed && !c->failed_flush) return h2_err(H2_FAIL_CLOSED, 0);
    if (max < 0) return h2_err(H2_FAIL_INVALID_ARGUMENT, 0);
    if (!c->preface_ok && c->mode == H2_MODE_PRIOR_KNOWLEDGE) return 0;
    if (!c->preface_ok) {
        /* TLS: the server SETTINGS may precede the client preface. */
    }
    before = c->wire_count;
    c->send_budget = (size_t)max;
    while (c->wire_count < (size_t)max && nghttp2_session_want_write(c->session)) {
        size_t prior = c->wire_count;
        r = nghttp2_session_send(c->session);
        if (r != 0) {
            c->send_budget = 0;
            if (r == NGHTTP2_ERR_WOULDBLOCK) break;
            {
                long code = map_ngerror(r);
                return conn_fail(c, code, (uint32_t)fail_code_kind(code));
            }
        }
        if (c->wire_count == prior) break;
    }
    c->send_budget = 0;
    sweep_streams(c);
    return (long)(c->wire_count - before);
}

long h2_conn_wire_pending(long conn_token) {
    h2_conn *c = peek_conn(conn_token);
    if (c == NULL) return h2_err(H2_FAIL_INVALID_RESOURCE, 0);
    return (long)c->wire_count;
}

long h2_conn_want_write(long conn_token) {
    h2_conn *c = peek_conn(conn_token);
    if (c == NULL) return h2_err(H2_FAIL_INVALID_RESOURCE, 0);
    if (c->closed || c->session == NULL) return 0;
    return nghttp2_session_want_write(c->session) ? 1 : 0;
}

long h2_conn_wire_read(long conn_token, long out_token, long max) {
    h2_conn *c = peek_conn(conn_token);
    unsigned char *dst = NULL;
    size_t cap = 0;
    size_t n;
    size_t first;
    if (c == NULL) return h2_err(H2_FAIL_INVALID_RESOURCE, 0);
    if (max < 0) return h2_err(H2_FAIL_INVALID_ARGUMENT, 0);
    if (buffer_view(out_token, &dst, &cap) != 0) return h2_err(H2_FAIL_INVALID_RESOURCE, 0);
    n = c->wire_count;
    if (n > (size_t)max) n = (size_t)max;
    if (n > cap) return h2_err(H2_FAIL_RANGE, 0);
    first = c->wire_cap - c->wire_tail;
    if (first > n) first = n;
    memcpy(dst, c->wire + c->wire_tail, first);
    if (n > first) memcpy(dst + first, c->wire, n - first);
    c->wire_tail = (c->wire_tail + n) % c->wire_cap;
    c->wire_count -= n;
    return (long)n;
}

static int64_t earliest(int64_t a, int64_t b) {
    if (a < 0) return b;
    if (b < 0) return a;
    return a < b ? a : b;
}

static int64_t conn_deadline_raw(h2_conn *c) {
    int64_t d = -1;
    size_t i;
    if (c == NULL) return -1;
    if (!c->preface_ok) d = earliest(d, c->preface_deadline);
    if (c->settings_active) d = earliest(d, c->settings_deadline);
    if (c->idle_active) d = earliest(d, c->idle_deadline);
    if (c->drain_deadline >= 0) d = earliest(d, c->drain_deadline);
    if (c->barrier_deadline >= 0) d = earliest(d, c->barrier_deadline);
    for (i = 0; i < c->stream_count; i++) {
        h2_stream *s = c->streams[i];
        if (s == NULL) continue;
        d = earliest(d, s->header_deadline);
        d = earliest(d, s->body_deadline);
        d = earliest(d, s->resp_deadline);
        d = earliest(d, s->resp_total_deadline);
        d = earliest(d, s->total_deadline);
    }
    return d;
}

static void check_deadlines(h2_conn *c, int64_t now) {
    size_t i;
    if (c == NULL || c->failed || c->closed) return;
    if (!c->preface_ok) {
        if (c->preface_deadline < 0) c->preface_deadline = deadline_add(now, c->preface_timeout_ms);
        if (c->preface_deadline >= 0 && now >= c->preface_deadline) {
            c->failed = 1;
            c->failed_flush = 0; /* no GOAWAY is queued for a bad/silent preface */
            c->failed_code = H2_FAIL(H2_FAIL_TIMEOUT, 0);
            c->state = H2_STATE_FAILED;
            c->timeouts += 1;
            g_timeouts += 1;
            event_push(c, H2_EV_TIMEOUT, 0, 0, (uint32_t)H2_FAIL_TIMEOUT, now);
            return;
        }
    }
    if (c->settings_active && c->settings_deadline >= 0 && now >= c->settings_deadline) {
        (void)nghttp2_session_terminate_session2(c->session,
                                                 nghttp2_session_get_last_proc_stream_id(c->session),
                                                 NGHTTP2_SETTINGS_TIMEOUT);
        c->settings_active = 0;
        c->settings_deadline = -1;
        c->timeouts += 1;
        g_timeouts += 1;
        (void)conn_fail(c, H2_FAIL(H2_FAIL_SETTINGS_TIMEOUT, 0),
                        (uint32_t)H2_FAIL_SETTINGS_TIMEOUT);
        return;
    }
    for (i = 0; i < c->stream_count; i++) {
        h2_stream *s = c->streams[i];
        int timed = 0;
        if (s == NULL) continue;
        if (s->header_deadline >= 0 && now >= s->header_deadline) timed = 1;
        if (s->body_deadline >= 0 && now >= s->body_deadline) timed = 1;
        if (s->resp_deadline >= 0 && now >= s->resp_deadline) timed = 1;
        if (s->resp_total_deadline >= 0 && now >= s->resp_total_deadline) timed = 1;
        if (s->total_deadline >= 0 && now >= s->total_deadline) timed = 1;
        if (timed) {
            (void)submit_rst(s, NGHTTP2_CANCEL);
            stream_clear_deadlines(s);
            c->timeouts += 1;
            g_timeouts += 1;
            event_push(c, H2_EV_TIMEOUT, s->id, 0, (uint32_t)H2_FAIL_TIMEOUT, now);
        }
    }
    if (c->drain_deadline >= 0 && now >= c->drain_deadline) {
        (void)nghttp2_session_terminate_session2(c->session,
                                                 nghttp2_session_get_last_proc_stream_id(c->session),
                                                 NGHTTP2_NO_ERROR);
        c->drain_deadline = -1;
        c->state = H2_STATE_CLOSING;
        c->timeouts += 1;
        g_timeouts += 1;
        event_push(c, H2_EV_TIMEOUT, 0, 0, (uint32_t)H2_FAIL_TIMEOUT, now);
    }
    if (c->idle_active && c->idle_deadline >= 0 && now >= c->idle_deadline) {
        (void)nghttp2_submit_goaway(c->session, NGHTTP2_FLAG_NONE,
                                    nghttp2_session_get_last_proc_stream_id(c->session),
                                    NGHTTP2_NO_ERROR, NULL, 0);
        c->idle_active = 0;
        c->idle_deadline = -1;
        c->state = H2_STATE_CLOSING;
        c->timeouts += 1;
        g_timeouts += 1;
        event_push(c, H2_EV_TIMEOUT, 0, 0, (uint32_t)H2_FAIL_TIMEOUT, now);
    }
}

long h2_conn_next_event(long conn_token, long now_ns) {
    h2_conn *c = peek_conn(conn_token);
    h2_event *e;
    if (c == NULL) return h2_err(H2_FAIL_INVALID_RESOURCE, 0);
    check_deadlines(c, now_ns);
    c->now_ns = now_ns;
    if (c->drain_state == 4 && c->drain_final_sent &&
        c->stream_count == 0 && !c->drain_complete_enqueued) {
        c->drain_complete_enqueued = 1;
        event_push(c, H2_EV_DRAIN_COMPLETE, 0, 0, 0, 0);
    }
    e = event_pop(c);
    if (e == NULL && c->failed && !c->error_delivered) {
        c->error_delivered = 1;
        event_push(c, H2_EV_PROTOCOL_ERROR, 0, 0,
                   (uint32_t)fail_code_kind(c->failed_code),
                   (int64_t)c->failed_code);
        e = event_pop(c);
    }
    if (e == NULL) return H2_EV_NONE;
    if (e->type == H2_EV_PROTOCOL_ERROR) c->error_delivered = 1;
    return (long)e->type;
}

long h2_conn_event_stream_id(long conn_token) {
    h2_conn *c = peek_conn(conn_token);
    if (c == NULL || !c->current_valid) return 0;
    return c->current.stream_id;
}

long h2_conn_event_flags(long conn_token) {
    h2_conn *c = peek_conn(conn_token);
    if (c == NULL || !c->current_valid) return 0;
    return (long)c->current.flags;
}

long h2_conn_event_error(long conn_token) {
    h2_conn *c = peek_conn(conn_token);
    if (c == NULL || !c->current_valid) return 0;
    return (long)c->current.error;
}

long h2_conn_event_aux(long conn_token) {
    h2_conn *c = peek_conn(conn_token);
    if (c == NULL || !c->current_valid) return 0;
    return (long)c->current.aux;
}

long h2_conn_deadline(long conn_token) {
    h2_conn *c = peek_conn(conn_token);
    if (c == NULL) return h2_err(H2_FAIL_INVALID_RESOURCE, 0);
    return (long)conn_deadline_raw(c);
}

long h2_conn_state(long conn_token) {
    h2_conn *c = peek_conn(conn_token);
    if (c == NULL) return H2_STATE_CLOSED;
    return (long)c->state;
}

long h2_conn_settings_sent(long conn_token) {
    h2_conn *c = peek_conn(conn_token);
    if (c == NULL) return 0;
    return c->settings_sent ? 1 : 0;
}

long h2_conn_settings_acked(long conn_token) {
    h2_conn *c = peek_conn(conn_token);
    if (c == NULL) return 0;
    return c->settings_acked ? 1 : 0;
}

long h2_conn_drain(long conn_token) {
    h2_conn *c = peek_conn(conn_token);
    int r;
    if (c == NULL) return h2_err(H2_FAIL_INVALID_RESOURCE, 0);
    if (c->failed || c->closed) return h2_err(H2_FAIL_CLOSED, 0);
    if (c->drain_state != 0) return 1;
    c->drain_cutoff = nghttp2_session_get_last_proc_stream_id(c->session);
    c->state = H2_STATE_DRAINING;
    if (c->goaways_received > 0) {
        /* The peer already sent GOAWAY. nghttp2_submit_shutdown_notice() is a
         * no-op once goaway_flags is set, so the usual wait for its send
         * callback would never advance. Send our final GOAWAY directly and
         * let drain_complete close after the remaining streams retire. */
        r = nghttp2_submit_goaway(c->session, NGHTTP2_FLAG_NONE,
                                  c->drain_cutoff, NGHTTP2_NO_ERROR, NULL, 0);
        if (r != 0) return h2_err(H2_FAIL_GOAWAY, 0);
        c->drain_state = 4;
        c->state = H2_STATE_CLOSING;
        return 1;
    }
    r = nghttp2_submit_shutdown_notice(c->session);
    if (r != 0) return h2_err(H2_FAIL_GOAWAY, 0);
    c->drain_state = 1;
    return 1;
}

long h2_conn_drain_poll(long conn_token, long now_ns) {
    h2_conn *c = peek_conn(conn_token);
    int32_t last;
    int r;
    if (c == NULL) return h2_err(H2_FAIL_INVALID_RESOURCE, 0);
    if (c->failed || c->closed) return h2_err(H2_FAIL_CLOSED, 0);
    c->now_ns = now_ns;
    if (c->drain_deadline < 0 && c->drain_state != 0)
        c->drain_deadline = deadline_add(now_ns, c->drain_timeout_ms);
    if (c->drain_state == 2) {
        uint64_t n = (uint64_t)now_ns ^ (uint64_t)(uintptr_t)c;
        size_t i;
        for (i = 0; i < 8; i++) c->barrier_nonce[i] = (uint8_t)(n >> (8 * i));
        c->barrier_nonce_valid = 1;
        c->barrier_acked = 0;
        r = nghttp2_submit_ping(c->session, NGHTTP2_FLAG_NONE, c->barrier_nonce);
        if (r != 0) return h2_err(H2_FAIL_INTERNAL, 0);
        c->barrier_deadline = deadline_add(now_ns, c->drain_barrier_ms);
        c->drain_state = 3;
        return 1;
    }
    if (c->drain_state == 3) {
        if (!c->barrier_acked && (c->barrier_deadline < 0 || now_ns < c->barrier_deadline))
            return 0;
        last = nghttp2_session_get_last_proc_stream_id(c->session);
        if (last > c->drain_cutoff) last = c->drain_cutoff;
        r = nghttp2_submit_goaway(c->session, NGHTTP2_FLAG_NONE, last,
                                  NGHTTP2_NO_ERROR, NULL, 0);
        if (r != 0) return h2_err(H2_FAIL_GOAWAY, 0);
        c->barrier_deadline = -1;
        c->drain_state = 4;
        c->state = H2_STATE_CLOSING;
        return 1;
    }
    return 0;
}

long h2_conn_drain_complete(long conn_token) {
    h2_conn *c = peek_conn(conn_token);
    if (c == NULL) return 0;
    return (c->drain_state == 4 && c->drain_final_sent &&
            c->stream_count == 0) ? 1 : 0;
}

long h2_conn_arm_preface(long conn_token, long now_ns) {
    h2_conn *c = peek_conn(conn_token);
    if (c == NULL) return h2_err(H2_FAIL_INVALID_RESOURCE, 0);
    if (c->closed || c->failed) return h2_err(H2_FAIL_CLOSED, 0);
    c->now_ns = now_ns;
    if (!c->preface_ok && c->preface_deadline < 0)
        c->preface_deadline = deadline_add(now_ns, c->preface_timeout_ms);
    return 1;
}

long h2_conn_refuse(long conn_token, long stream_id, long error_code) {
    h2_conn *c = peek_conn(conn_token);
    int r;
    if (c == NULL) return h2_err(H2_FAIL_INVALID_RESOURCE, 0);
    if (c->closed || c->failed) return h2_err(H2_FAIL_CLOSED, 0);
    if (stream_id <= 0 || error_code < 0 ||
        error_code > 0xffffffffL) return h2_err(H2_FAIL_INVALID_ARGUMENT, 0);
    r = nghttp2_submit_rst_stream(c->session, NGHTTP2_FLAG_NONE,
                                  (int32_t)stream_id, (uint32_t)error_code);
    if (r != 0) return h2_err(H2_FAIL_REFUSED, 0);
    return 1;
}

long h2_conn_goaway(long conn_token, long last_stream_id, long error_code) {
    h2_conn *c = peek_conn(conn_token);
    int r;
    if (c == NULL) return h2_err(H2_FAIL_INVALID_RESOURCE, 0);
    if (c->failed || c->closed) return h2_err(H2_FAIL_CLOSED, 0);
    if (last_stream_id < 0) last_stream_id = nghttp2_session_get_last_proc_stream_id(c->session);
    r = nghttp2_submit_goaway(c->session, NGHTTP2_FLAG_NONE, (int32_t)last_stream_id,
                              (uint32_t)error_code, NULL, 0);
    if (r != 0) return h2_err(H2_FAIL_GOAWAY, 0);
    c->idle_active = 0;
    c->idle_deadline = -1;
    c->settings_active = 0;
    c->settings_deadline = -1;
    c->state = H2_STATE_CLOSING;
    return 1;
}

long h2_conn_terminate(long conn_token, long last_stream_id, long error_code) {
    h2_conn *c = peek_conn(conn_token);
    int r;
    if (c == NULL) return h2_err(H2_FAIL_INVALID_RESOURCE, 0);
    if (c->closed) return h2_err(H2_FAIL_CLOSED, 0);
    if (last_stream_id < 0) last_stream_id = nghttp2_session_get_last_proc_stream_id(c->session);
    r = nghttp2_session_terminate_session2(c->session, (int32_t)last_stream_id,
                                           (uint32_t)error_code);
    if (r != 0) return h2_err(H2_FAIL_INTERNAL, 0);
    c->idle_active = 0;
    c->idle_deadline = -1;
    c->settings_active = 0;
    c->settings_deadline = -1;
    c->drain_deadline = -1;
    c->barrier_deadline = -1;
    if ((uint32_t)error_code == NGHTTP2_NO_ERROR) {
        /* Clean terminate is a drain, not a protocol error. The queued GOAWAY
         * must still be sendable, and the frame-send callback must not classify
         * it as a failure. */
        c->state = H2_STATE_CLOSING;
        c->failed_flush = 1;
    } else {
        c->failed = 1;
        c->failed_flush = 1;
        c->failed_code = H2_FAIL(H2_FAIL_PROTOCOL, error_code);
        c->state = H2_STATE_FAILED;
    }
    return 1;
}

long h2_conn_consume(long conn_token, long bytes) {
    h2_conn *c = peek_conn(conn_token);
    if (c == NULL) return h2_err(H2_FAIL_INVALID_RESOURCE, 0);
    if (bytes < 0) return h2_err(H2_FAIL_INVALID_ARGUMENT, 0);
    if (bytes == 0) return 1;
    if (c->session == NULL) return h2_err(H2_FAIL_CLOSED, 0);
    if (nghttp2_session_consume_connection(c->session, (size_t)bytes) != 0)
        return h2_err(H2_FAIL_FLOW_CONTROL, 0);
    c->window_restored += (size_t)bytes;
    g_window_restored += (size_t)bytes;
    return 1;
}

long h2_conn_stats(long conn_token, long out_token) {
    h2_conn *c = peek_conn(conn_token);
    int64_t f[H2_STATS_FIELDS];
    size_t i;
    size_t closing = 0;
    if (c == NULL) return h2_err(H2_FAIL_INVALID_RESOURCE, 0);
    for (i = 0; i < c->stream_count; i++)
        if (c->streams[i] != NULL && c->streams[i]->closing) closing += 1;
    memset(f, 0, sizeof(f));
    f[H2_ST_LIVE_STREAMS] = (int64_t)c->stream_count;
    f[H2_ST_CLOSING_STREAMS] = (int64_t)closing;
    f[H2_ST_MAX_CONCURRENT] = (int64_t)c->max_concurrent;
    f[H2_ST_STREAMS_OPENED] = (int64_t)c->streams_opened;
    f[H2_ST_STREAMS_CLOSED] = (int64_t)c->streams_closed;
    f[H2_ST_RESETS_SENT] = (int64_t)c->resets_sent;
    f[H2_ST_RESETS_RECEIVED] = (int64_t)c->resets_received;
    f[H2_ST_GOAWAYS_SENT] = (int64_t)c->goaways_sent;
    f[H2_ST_GOAWAYS_RECEIVED] = (int64_t)c->goaways_received;
    f[H2_ST_FRAMES_IN] = (int64_t)c->frames_in;
    f[H2_ST_ZERO_DATA_FRAMES] = (int64_t)c->zero_data_frames;
    f[H2_ST_HEADER_BYTES] = (int64_t)c->header_bytes;
    f[H2_ST_BODY_BYTES_IN] = (int64_t)c->body_bytes_in;
    f[H2_ST_BODY_BYTES_OUT] = (int64_t)c->body_bytes_out;
    f[H2_ST_WIRE_BYTES] = (int64_t)c->wire_bytes;
    f[H2_ST_WINDOW_RESTORED] = (int64_t)c->window_restored;
    f[H2_ST_EVENT_HIGH_WATER] = (int64_t)c->event_high;
    f[H2_ST_WIRE_HIGH_WATER] = (int64_t)c->wire_high;
    f[H2_ST_STATE] = (int64_t)c->state;
    f[H2_ST_FAILED_CODE] = (int64_t)c->failed_code;
    f[H2_ST_RECV_WINDOW] = c->session == NULL ? 0 :
        (int64_t)nghttp2_session_get_local_window_size(c->session);
    if (f[H2_ST_RECV_WINDOW] < 0) f[H2_ST_RECV_WINDOW] = 0;
    f[H2_ST_CONN_WINDOW] = f[H2_ST_RECV_WINDOW];
    return packet_copy(out_token, f, H2_STATS_FIELDS);
}

long h2_conn_reset_counts(long conn_token) {
    h2_conn *c = peek_conn(conn_token);
    if (c == NULL) return h2_err(H2_FAIL_INVALID_RESOURCE, 0);
    c->resets_sent = 0;
    c->resets_received = 0;
    c->goaways_sent = 0;
    c->goaways_received = 0;
    c->frames_in = 0;
    c->zero_data_frames = 0;
    c->header_bytes = 0;
    c->body_bytes_in = 0;
    c->body_bytes_out = 0;
    c->wire_bytes = 0;
    c->window_restored = 0;
    c->event_high = c->event_count;
    c->wire_high = c->wire_count;
    return 1;
}

/* ------------------------------------------------------------------ */
/* Streams                                                             */
/* ------------------------------------------------------------------ */

long h2_stream_adopt(long conn_token, long stream_id) {
    h2_conn *c = peek_conn(conn_token);
    h2_stream *s;
    long token;
    if (c == NULL) return h2_err(H2_FAIL_INVALID_RESOURCE, 0);
    if (stream_id <= 0 || (stream_id & 1) == 0) return h2_err(H2_FAIL_INVALID_ARGUMENT, 0);
    if (c->failed || c->closed) return h2_err(H2_FAIL_CLOSED, 0);
    if (c->state == H2_STATE_CLOSING || c->state == H2_STATE_CLOSED)
        return h2_err(H2_FAIL_CLOSED, 0);
    s = find_stream(c, (int32_t)stream_id);
    if (s == NULL || s->closed) return h2_err(H2_FAIL_STREAM_CLOSED, 0);
    if (s->registered) return h2_err(H2_FAIL_INVALID_STATE, 1);
    if ((c->state == H2_STATE_DRAINING || c->state == H2_STATE_CLOSING) &&
        (int32_t)stream_id > c->drain_cutoff) return h2_err(H2_FAIL_REFUSED, 0);
    token = sys_reg_new_object((int)H2_KIND_STREAM, s, stream_release, 0);
    if (token < 0) {
        consume_buffered(c, s, 1);
        (void)submit_rst(s, NGHTTP2_REFUSED_STREAM);
        s->closing = 1;
        return h2_err(H2_FAIL_RESOURCE_LIMIT, 0);
    }
    s->token = token;
    s->registered = 1;
    s->adopted = 0;
    return token;
}

long h2_stream_mark_adopted(long stream_token) {
    h2_stream *s = peek_stream(stream_token);
    if (s == NULL) return 0;
    s->adopted = 1;
    return 1;
}

long h2_stream_abandon(long stream_token) {
    h2_stream *s = peek_stream(stream_token);
    void *ptr = NULL;
    int (*release)(void *) = NULL;
    if (s == NULL) return 0;
    if (!s->closing) {
        if (s->conn != NULL) {
            consume_buffered(s->conn, s, 1);
            (void)submit_rst(s, NGHTTP2_REFUSED_STREAM);
        }
        s->closing = 1;
    }
    if (sys_reg_retire(stream_token, (int)H2_KIND_STREAM, &ptr, &release)) {
        s->registered = 0;
        s->token = 0;
        if (release != NULL) (void)release(ptr);
    }
    return 1;
}

long h2_stream_close(long stream_token) {
    h2_stream *s = peek_stream(stream_token);
    if (s == NULL) return 0;
    if (s->closing || s->closed) return 0;
    consume_buffered(s->conn, s, 1);
    (void)submit_rst(s, NGHTTP2_CANCEL);
    s->closing = 1;
    s->response_state = 6;
    stream_clear_deadlines(s);
    return 1;
}

long h2_stream_reset(long stream_token, long error_code) {
    h2_stream *s = peek_stream(stream_token);
    long r;
    if (s == NULL) return h2_err(H2_FAIL_INVALID_RESOURCE, 0);
    if (s->conn == NULL) return h2_err(H2_FAIL_CLOSED, 0);
    if (error_code < 0 || error_code > 0xffffffffL) return h2_err(H2_FAIL_INVALID_ARGUMENT, 0);
    if (s->closing || s->closed) return 0;
    r = submit_rst(s, (uint32_t)error_code);
    if (r < 0) return r;
    consume_buffered(s->conn, s, 1);
    s->closing = 1;
    s->response_state = 6;
    stream_clear_deadlines(s);
    return 1;
}

long h2_stream_state(long stream_token) {
    h2_stream *s = peek_stream(stream_token);
    if (s == NULL) return H2_STREAM_CLOSED;
    if (s->conn == NULL) return H2_STREAM_CLOSED;
    if (s->closed || (s->peer_end && s->local_end)) return H2_STREAM_CLOSED;
    if (s->local_end) return H2_STREAM_HALF_CLOSED_LOCAL;
    if (s->peer_end) return H2_STREAM_HALF_CLOSED_REMOTE;
    return H2_STREAM_OPEN;
}

long h2_stream_windows(long stream_token, long out_token) {
    h2_stream *s = peek_stream(stream_token);
    int64_t f[2];
    if (s == NULL) return h2_err(H2_FAIL_INVALID_RESOURCE, 0);
    if (s->conn == NULL || s->conn->session == NULL) return h2_err(H2_FAIL_CLOSED, 0);
    f[0] = (int64_t)nghttp2_session_get_stream_local_window_size(s->conn->session, s->id);
    f[1] = (int64_t)nghttp2_session_get_local_window_size(s->conn->session);
    if (f[0] < 0) f[0] = 0;
    if (f[1] < 0) f[1] = 0;
    return packet_copy(out_token, f, 2);
}

long h2_stream_info(long stream_token, long out_token) {
    h2_stream *s = peek_stream(stream_token);
    int64_t f[H2_STREAM_INFO_FIELDS];
    if (s == NULL) return h2_err(H2_FAIL_INVALID_RESOURCE, 0);
    if (s->conn == NULL) return h2_err(H2_FAIL_CLOSED, 0);
    memset(f, 0, sizeof(f));
    f[H2_SI_ID] = s->id;
    f[H2_SI_STATE] = h2_stream_state(stream_token);
    f[H2_SI_RECV_BUFFERED] = (int64_t)(s->recv_end - s->recv_start);
    f[H2_SI_SEND_BUFFERED] = (int64_t)(s->send_end - s->send_start);
    f[H2_SI_RECV_WINDOW] = (s->conn != NULL && s->conn->session != NULL) ?
        (int64_t)nghttp2_session_get_stream_local_window_size(s->conn->session, s->id) : 0;
    f[H2_SI_CONN_WINDOW] = (s->conn != NULL && s->conn->session != NULL) ?
        (int64_t)nghttp2_session_get_local_window_size(s->conn->session) : 0;
    if (f[H2_SI_RECV_WINDOW] < 0) f[H2_SI_RECV_WINDOW] = 0;
    if (f[H2_SI_CONN_WINDOW] < 0) f[H2_SI_CONN_WINDOW] = 0;
    f[H2_SI_HEADER_COUNT] = (int64_t)s->hreq_count;
    f[H2_SI_TRAILER_COUNT] = (int64_t)s->htrail_count;
    f[H2_SI_BODY_IN] = (int64_t)s->body_bytes_in;
    f[H2_SI_BODY_OUT] = (int64_t)s->body_bytes_out;
    f[H2_SI_FLAGS] = (int64_t)((s->rst_submitted ? 1 : 0) | (s->closed ? 2 : 0) |
                              (s->rejected ? 4 : 0));
    f[H2_SI_DECLARED_LENGTH] = s->declared_length < 0 ? 0 : s->declared_length + 1;
    f[H2_SI_RESPONSE_STATE] = (int64_t)s->response_state;
    f[H2_SI_STATUS] = s->status;
    f[H2_SI_TOTAL_DEADLINE] = s->total_deadline < 0 ? 0 : s->total_deadline;
    f[H2_SI_RESPONSE_DEADLINE] = s->resp_total_deadline < 0 ? 0 : s->resp_total_deadline;
    f[H2_SI_LOCAL_END] = s->local_end ? 1 : 0;
    f[H2_SI_PEER_END] = s->peer_end ? 1 : 0;
    return packet_copy(out_token, f, H2_STREAM_INFO_FIELDS);
}

static long stage_response(h2_stream *s, long packet_token, long name_off,
                           long name_len, long value_off, long value_len,
                           int trailer) {
    unsigned char *pkt = NULL;
    size_t cap = 0;
    size_t total;
    h2_conn *c;
    if (s == NULL) return h2_err(H2_FAIL_INVALID_RESOURCE, 0);
    c = s->conn;
    if (c == NULL) return h2_err(H2_FAIL_CLOSED, 0);
    if (name_off < 0 || name_len <= 0 || value_off < 0 || value_len < 0)
        return h2_err(H2_FAIL_INVALID_ARGUMENT, 0);
    if (buffer_view(packet_token, &pkt, &cap) != 0) return h2_err(H2_FAIL_INVALID_RESOURCE, 0);
    if ((size_t)name_off > cap || (size_t)name_len > cap - (size_t)name_off ||
        (size_t)value_off > cap || (size_t)value_len > cap - (size_t)value_off)
        return h2_err(H2_FAIL_RANGE, 0);
    total = (size_t)name_len + (size_t)value_len;
    if (total > c->max_response_header + c->max_response_trailer)
        return h2_err(H2_FAIL_HEADER_LIST, 0);
    if (trailer) {
        if (s->finished) return h2_err(H2_FAIL_INVALID_STATE, 0);
        if (store_header(s->rtrail_arena, s->rtrail_cap, &s->rtrail_used,
                         s->rtrail, s->rtrail_refcap, &s->rtrail_count,
                         &s->rtrail_list_bytes, c->max_response_trailer,
                         pkt + name_off, (size_t)name_len,
                         pkt + value_off, (size_t)value_len, 1, 0) != 0) {
            return h2_err(H2_FAIL_HEADER_LIST, 0);
        }
    } else {
        if (s->response_state != 1) return h2_err(H2_FAIL_INVALID_STATE, 0);
        if (store_header(s->rhead_arena, s->rhead_cap, &s->rhead_used,
                         s->rhead, s->rhead_refcap, &s->rhead_count,
                         &s->rhead_list_bytes, c->max_response_header,
                         pkt + name_off, (size_t)name_len,
                         pkt + value_off, (size_t)value_len, 1, 0) != 0) {
            return h2_err(H2_FAIL_HEADER_LIST, 0);
        }
        if ((size_t)name_len == 14 && memcmp(s->rhead_arena +
                s->rhead[s->rhead_count - 1].name_off, "content-length", 14) == 0) {
            s->rhead_count -= 1;
            s->rhead_used -= (size_t)name_len + (size_t)value_len;
            s->rhead_list_bytes -= (size_t)name_len + (size_t)value_len + 32;
            return h2_err(H2_FAIL_INVALID_ARGUMENT, 0);
        }
    }
    return 1;
}

long h2_stream_begin(long stream_token, long status) {
    h2_stream *s = peek_stream(stream_token);
    if (s == NULL) return h2_err(H2_FAIL_INVALID_RESOURCE, 0);
    if (s->conn == NULL) return h2_err(H2_FAIL_CLOSED, 0);
    if (s->response_state != 0 || s->closing) return h2_err(H2_FAIL_INVALID_STATE, 0);
    if (status < 100 || status > 599 || status == 101) return h2_err(H2_FAIL_INVALID_ARGUMENT, 0);
    s->status = (int)status;
    s->response_state = 1;
    s->rhead_count = 0;
    s->rhead_used = 0;
    s->rhead_list_bytes = 0;
    s->rtrail_count = 0;
    s->rtrail_used = 0;
    s->rtrail_list_bytes = 0;
    s->declared_length = -1;
    return 1;
}

long h2_stream_set_length(long stream_token, long length) {
    h2_stream *s = peek_stream(stream_token);
    if (s == NULL) return h2_err(H2_FAIL_INVALID_RESOURCE, 0);
    if (s->response_state != 1 || s->closing) return h2_err(H2_FAIL_INVALID_STATE, 0);
    if (length < 0) return h2_err(H2_FAIL_RANGE, 0);
    s->declared_length = (int64_t)length;
    return 1;
}

long h2_stream_header(long stream_token, long packet_token, long name_off,
                      long name_len, long value_off, long value_len) {
    return stage_response(peek_stream(stream_token), packet_token, name_off,
                          name_len, value_off, value_len, 0);
}

long h2_stream_trailer(long stream_token, long packet_token, long name_off,
                       long name_len, long value_off, long value_len) {
    return stage_response(peek_stream(stream_token), packet_token, name_off,
                          name_len, value_off, value_len, 1);
}

static int commit_response(h2_stream *s, int end_stream) {
    nghttp2_nv *nva;
    nghttp2_data_provider provider;
    size_t i;
    size_t nvcount;
    size_t inject;
    int r;
    int body_possible;
    char statusbuf[8];
    int statuslen;
    if (s == NULL || s->conn == NULL || s->conn->session == NULL)
        return (int)h2_err(H2_FAIL_CLOSED, 0);
    if (s->response_state != 1) return (int)h2_err(H2_FAIL_INVALID_STATE, 0);
    body_possible = !(s->status >= 100 && s->status < 200) &&
                    s->status != 204 && s->status != 205;
    inject = (s->declared_length >= 0 && body_possible) ? 1U : 0U;
    nvcount = 1 + s->rhead_count + inject;
    nva = (nghttp2_nv *)h2_alloc(sizeof(nghttp2_nv) * nvcount);
    if (nva == NULL) return (int)h2_err(H2_FAIL_RESOURCE_LIMIT, 0);
    statuslen = snprintf(statusbuf, sizeof(statusbuf), "%d", s->status);
    if (statuslen <= 0 || (size_t)statuslen >= sizeof(statusbuf)) {
        free(nva);
        return (int)h2_err(H2_FAIL_INTERNAL, 0);
    }
    nva[0].name = (uint8_t *)":status";
    nva[0].namelen = 7;
    nva[0].value = (uint8_t *)statusbuf;
    nva[0].valuelen = (size_t)statuslen;
    nva[0].flags = NGHTTP2_NV_FLAG_NONE;
    for (i = 0; i < s->rhead_count; i++) {
        nva[1 + i].name = (uint8_t *)(s->rhead_arena + s->rhead[i].name_off);
        nva[1 + i].namelen = s->rhead[i].name_len;
        nva[1 + i].value = (uint8_t *)(s->rhead_arena + s->rhead[i].value_off);
        nva[1 + i].valuelen = s->rhead[i].value_len;
        nva[1 + i].flags = NGHTTP2_NV_FLAG_NONE;
    }
    if (inject) {
        nva[nvcount - 1].name = (uint8_t *)"content-length";
        nva[nvcount - 1].namelen = 14;
        nva[nvcount - 1].value = (uint8_t *)(s->rhead_arena + s->rhead_used);
        {
            char tmp[32];
            int len = snprintf(tmp, sizeof(tmp), "%lld", (long long)s->declared_length);
            if (len < 0 || (size_t)len >= sizeof(tmp)) { free(nva); return (int)h2_err(H2_FAIL_INTERNAL, 0); }
            if (s->rhead_used + (size_t)len > s->rhead_cap) { free(nva); return (int)h2_err(H2_FAIL_HEADER_LIST, 0); }
            memcpy(s->rhead_arena + s->rhead_used, tmp, (size_t)len);
            nva[nvcount - 1].valuelen = (size_t)len;
            s->rhead_used += (size_t)len;
        }
        nva[nvcount - 1].flags = NGHTTP2_NV_FLAG_NONE;
    }
    if (end_stream && s->send_start == s->send_end && s->rtrail_count == 0) {
        r = nghttp2_submit_response(s->conn->session, s->id, nva, nvcount, NULL);
        free(nva);
        if (r != 0) return (int)h2_err(H2_FAIL_INTERNAL, 0);
        s->response_state = 5;
        s->local_end = 1;
        s->finished = 1;
        stream_clear_deadlines(s);
        return 1;
    }
    memset(&provider, 0, sizeof(provider));
    provider.source.ptr = s;
    provider.read_callback = data_read_cb;
    r = nghttp2_submit_response(s->conn->session, s->id, nva, nvcount, &provider);
    free(nva);
    if (r != 0) return (int)h2_err(H2_FAIL_INTERNAL, 0);
    s->response_state = 2;
    s->provider_submitted = 1;
    s->resp_total_deadline = deadline_add(s->conn->now_ns, s->conn->response_total_timeout_ms);
    s->resp_deadline = deadline_add(s->conn->now_ns, s->conn->response_timeout_ms);
    if (end_stream) {
        s->finished = 1;
        s->local_end = 1;
        if (s->conn->session != NULL)
            (void)nghttp2_session_resume_data(s->conn->session, s->id);
    }
    return 1;
}

long h2_stream_commit(long stream_token, long end_stream) {
    h2_stream *s = peek_stream(stream_token);
    if (s == NULL) return h2_err(H2_FAIL_INVALID_RESOURCE, 0);
    return commit_response(s, end_stream != 0);
}

long h2_stream_write(long stream_token, long in_token, long offset, long count) {
    h2_stream *s = peek_stream(stream_token);
    unsigned char *src = NULL;
    size_t cap = 0;
    size_t free_space;
    size_t n;
    int r;
    if (s == NULL) return h2_err(H2_FAIL_INVALID_RESOURCE, 0);
    if (s->conn == NULL) return h2_err(H2_FAIL_CLOSED, 0);
    if (count < 0 || offset < 0) return h2_err(H2_FAIL_INVALID_ARGUMENT, 0);
    if (buffer_view(in_token, &src, &cap) != 0) return h2_err(H2_FAIL_INVALID_RESOURCE, 0);
    if ((size_t)offset > cap || (size_t)count > cap - (size_t)offset)
        return h2_err(H2_FAIL_RANGE, 0);
    if (s->closing || s->closed || s->response_state == 6)
        return h2_err(H2_FAIL_STREAM_CLOSED, 0);
    if (s->finished) return h2_err(H2_FAIL_INVALID_STATE, 0);
    if (s->response_state == 0) return h2_err(H2_FAIL_INVALID_STATE, 0);
    if (s->response_state == 1) {
        r = commit_response(s, 0);
        if (r < 0) return r;
    }
    if (s->send_start > 0 && s->send_end == s->send_cap) {
        size_t used = s->send_end - s->send_start;
        memmove(s->send, s->send + s->send_start, used);
        s->send_start = 0;
        s->send_end = used;
    }
    free_space = s->send_cap - s->send_end;
    n = (size_t)count;
    if (n > free_space) n = free_space;
    if (n > 0) {
        memcpy(s->send + s->send_end, src + offset, n);
        s->send_end += n;
    }
    s->body_bytes_out += n;
    g_body_out += n;
    if (s->resp_deadline >= 0)
        s->resp_deadline = deadline_add(s->conn->now_ns, s->conn->response_timeout_ms);
    if (n > 0 && s->conn->session != NULL)
        (void)nghttp2_session_resume_data(s->conn->session, s->id);
    return (long)n;
}

long h2_stream_end(long stream_token) {
    h2_stream *s = peek_stream(stream_token);
    int r;
    if (s == NULL) return h2_err(H2_FAIL_INVALID_RESOURCE, 0);
    if (s->conn == NULL) return h2_err(H2_FAIL_CLOSED, 0);
    if (s->closing || s->closed) return h2_err(H2_FAIL_STREAM_CLOSED, 0);
    if (s->response_state == 0) return h2_err(H2_FAIL_INVALID_STATE, 0);
    if (s->finished) return 0;
    if (s->response_state == 1) {
        r = commit_response(s, 1);
        if (r < 0) return r;
        return 1;
    }
    s->finished = 1;
    s->local_end = 1;
    if (s->conn->session != NULL)
        (void)nghttp2_session_resume_data(s->conn->session, s->id);
    return 1;
}

long h2_stream_read(long stream_token, long out_token, long max) {
    h2_stream *s = peek_stream(stream_token);
    unsigned char *dst = NULL;
    size_t cap = 0;
    size_t avail;
    size_t n;
    if (s == NULL) return h2_err(H2_FAIL_INVALID_RESOURCE, 0);
    if (max < 0) return h2_err(H2_FAIL_INVALID_ARGUMENT, 0);
    if (s->conn == NULL) return h2_err(H2_FAIL_CLOSED, 0);
    if (buffer_view(out_token, &dst, &cap) != 0) return h2_err(H2_FAIL_INVALID_RESOURCE, 0);
    avail = s->recv_end - s->recv_start;
    n = avail;
    if (n > (size_t)max) n = (size_t)max;
    if (n > cap) return h2_err(H2_FAIL_RANGE, 0);
    if (n > 0) {
        memcpy(dst, s->recv + s->recv_start, n);
        s->recv_start += n;
        if (s->recv_start == s->recv_end) { s->recv_start = 0; s->recv_end = 0; }
        if (s->conn->session != NULL) {
            if (nghttp2_session_consume(s->conn->session, s->id, n) != 0) {
                (void)nghttp2_session_consume_connection(s->conn->session, n);
            }
        }
    }
    if (s->body_deadline < 0 && n > 0)
        s->body_deadline = deadline_add(s->conn->now_ns, s->conn->body_idle_timeout_ms);
    else if (n > 0)
        s->body_deadline = deadline_add(s->conn->now_ns, s->conn->body_idle_timeout_ms);
    return (long)n;
}

long h2_stream_discard(long stream_token, long max, long now_ns) {
    h2_stream *s = peek_stream(stream_token);
    size_t avail;
    size_t n;
    if (s == NULL) return h2_err(H2_FAIL_INVALID_RESOURCE, 0);
    if (max < 0) return h2_err(H2_FAIL_INVALID_ARGUMENT, 0);
    if (s->conn == NULL) return h2_err(H2_FAIL_CLOSED, 0);
    avail = s->recv_end - s->recv_start;
    n = avail;
    if (n > (size_t)max) n = (size_t)max;
    s->recv_start += n;
    if (s->recv_start == s->recv_end) { s->recv_start = 0; s->recv_end = 0; }
    if (n > 0 && s->conn->session != NULL) {
        if (nghttp2_session_consume(s->conn->session, s->id, n) != 0)
            (void)nghttp2_session_consume_connection(s->conn->session, n);
        s->conn->now_ns = now_ns;
    }
    return (long)n;
}

long h2_stream_header_count(long stream_token) {
    h2_stream *s = peek_stream(stream_token);
    if (s == NULL) return h2_err(H2_FAIL_INVALID_RESOURCE, 0);
    if (stream_access_closed(s)) return h2_err(H2_FAIL_CLOSED, 0);
    if (s->request_cleared) return h2_err(H2_FAIL_INVALID_STATE, 0);
    return (long)s->hreq_count;
}

long h2_stream_header_at(long stream_token, long index, long out_token, long which) {
    h2_stream *s = peek_stream(stream_token);
    h2_nv_ref *ref;
    if (s == NULL) return h2_err(H2_FAIL_INVALID_RESOURCE, 0);
    if (stream_access_closed(s)) return h2_err(H2_FAIL_CLOSED, 0);
    if (s->request_cleared) return h2_err(H2_FAIL_INVALID_STATE, 0);
    if (index < 0 || (size_t)index >= s->hreq_count) return h2_err(H2_FAIL_RANGE, 0);
    if (which != H2_NV_NAME && which != H2_NV_VALUE) return h2_err(H2_FAIL_INVALID_ARGUMENT, 0);
    ref = &s->hreq[index];
    if (which == H2_NV_NAME)
        return buffer_copy_out(out_token, s->hreq_arena + ref->name_off, ref->name_len, ref->name_len);
    return buffer_copy_out(out_token, s->hreq_arena + ref->value_off, ref->value_len, ref->value_len);
}

long h2_stream_trailer_count(long stream_token) {
    h2_stream *s = peek_stream(stream_token);
    if (s == NULL) return h2_err(H2_FAIL_INVALID_RESOURCE, 0);
    if (stream_access_closed(s)) return h2_err(H2_FAIL_CLOSED, 0);
    return (long)s->htrail_count;
}

long h2_stream_trailer_at(long stream_token, long index, long out_token, long which) {
    h2_stream *s = peek_stream(stream_token);
    h2_nv_ref *ref;
    if (s == NULL) return h2_err(H2_FAIL_INVALID_RESOURCE, 0);
    if (stream_access_closed(s)) return h2_err(H2_FAIL_CLOSED, 0);
    if (index < 0 || (size_t)index >= s->htrail_count) return h2_err(H2_FAIL_RANGE, 0);
    if (which != H2_NV_NAME && which != H2_NV_VALUE) return h2_err(H2_FAIL_INVALID_ARGUMENT, 0);
    ref = &s->htrail[index];
    if (which == H2_NV_NAME)
        return buffer_copy_out(out_token, s->htrail_arena + ref->name_off, ref->name_len, ref->name_len);
    return buffer_copy_out(out_token, s->htrail_arena + ref->value_off, ref->value_len, ref->value_len);
}

long h2_stream_clear_request(long stream_token) {
    h2_stream *s = peek_stream(stream_token);
    if (s == NULL) return h2_err(H2_FAIL_INVALID_RESOURCE, 0);
    if (stream_access_closed(s)) return h2_err(H2_FAIL_CLOSED, 0);
    s->hreq_count = 0;
    s->hreq_used = 0;
    s->hreq_list_bytes = 0;
    s->request_cleared = 1;
    return 1;
}

long h2_text(long code, long out_token, long max) {
    const char *text = fail_text(code);
    return buffer_copy_out(out_token, text, strlen(text), (size_t)(max < 0 ? 0 : max));
}

long h2_counts(long out_token) {
    int64_t f[22];
    memset(f, 0, sizeof(f));
    f[0] = (int64_t)g_live_connections;
    f[1] = (int64_t)g_live_streams;
    f[2] = (int64_t)g_streams_opened;
    f[3] = (int64_t)g_streams_closed;
    f[4] = (int64_t)g_resets_sent;
    f[5] = (int64_t)g_resets_received;
    f[6] = (int64_t)g_goaways_sent;
    f[7] = (int64_t)g_goaways_received;
    f[8] = (int64_t)g_frames_in;
    f[9] = (int64_t)g_zero_data_frames;
    f[10] = (int64_t)g_header_bytes;
    f[11] = (int64_t)g_body_in;
    f[12] = (int64_t)g_body_out;
    f[13] = (int64_t)g_wire_bytes;
    f[14] = (int64_t)g_window_restored;
    f[15] = (int64_t)g_protocol_errors;
    f[16] = (int64_t)g_timeouts;
    f[17] = (int64_t)sys_reg_live_kind((int)H2_KIND_CONNECTION);
    f[18] = (int64_t)sys_reg_live_kind((int)H2_KIND_STREAM);
    f[19] = (int64_t)sys_reg_bytes();
    f[20] = 0;
    f[21] = 0;
    return packet_copy(out_token, f, 22);
}
